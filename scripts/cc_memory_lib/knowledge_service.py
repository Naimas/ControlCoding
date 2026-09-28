"""Unified embedded memory facade: source generations, wiki, chat and GraphRAG.

This coordinator never edits source documents or the portable Work contract.
Derived pages are explicitly extractive, and answers quote evidence, not guesses.
"""
import json
import re
import time
from .knowledge_store import database, get, put, now, KnowledgeError
from .knowledge_sources import capture, passages, digest, references, head, SCOPES, LIMITS
from .knowledge_semantic import OllamaEmbedding, encode_vector
from . import knowledge_wiki as wiki
from . import knowledge_staging as staging
from . import knowledge_scan as scan

DEFAULT = {'scopes': ['project', 'work'], 'automatic': True, 'worker': False,
           'retention': 'transcript', 'embedding': 'bge-m3:latest'}


def require(condition, code='invalid_knowledge_request'):
    if not condition:
        raise KnowledgeError(code)


def policy(value):
    require(type(value) is dict and set(DEFAULT) <= set(value) <= set(DEFAULT) | {'rich_paths'})
    selected = value.get('rich_paths')
    if selected is not None:
        require(type(selected) is list and 0 < len(selected) <= 128)
        for name in selected:
            require(type(name) is str and len(name) <= 1024 and name == name.strip())
            parts = name.split('/')
            require(all(p and not p.startswith('.') and not p.endswith(('.', ' '))
                        and not re.search(r'[\\:*?"<>|\x00-\x1f]', p) for p in parts))
            require((len(parts) == 1 or parts[0] == 'docs') and
                    name.lower().endswith(('.docx', '.xlsx', '.pdf')))
        require(len({p.casefold() for p in selected}) == len(selected))
    require(type(value['scopes']) is list and 0 < len(value['scopes']) <= len(SCOPES)
            and all(type(s) is str and s in SCOPES for s in value['scopes'])
            and len(set(value['scopes'])) == len(value['scopes']))
    require(type(value['automatic']) is bool and type(value['worker']) is bool)
    require(value['retention'] in ('none', 'summary', 'transcript'))
    require(type(value['embedding']) is str and (value['embedding'] == '' or
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}', value['embedding'])))
    return value


def initialized(db):
    require(db is not None and get(db, 'policy') is not None, 'knowledge_not_enabled')


def configure(root, value):
    value = policy(value)
    with database(root, create=True) as db:
        old = get(db, 'policy')
        with db:
            put(db, 'policy', value)
            if old != value:
                staging.clear(db)
                scan.clear(db)
            if not old or old['scopes'] != value['scopes'] or old.get('rich_paths') != value.get('rich_paths'):
                put(db, 'needs_reconcile', True)
            if not old or old['embedding'] != value['embedding']:
                db.execute('UPDATE chunks SET vector=NULL,model=NULL')
                put(db, 'embedding_identity', None)
            enqueue(db, 'policy changed')
    return status(root)


def enqueue(db, reason):
    if not db.execute("SELECT 1 FROM jobs WHERE kind='reconcile' AND state='pending'").fetchone():
        db.execute("INSERT INTO jobs(kind,state,reason,created) VALUES('reconcile','pending',?,?)", (reason, now()))


def source_rows(db):
    return [dict(r) for r in db.execute('SELECT * FROM sources WHERE deleted=0 ORDER BY path')]


def reconcile(root, reason='manual'):
    require(reason in ('manual', 'startup', 'timer', 'commit-ceremony', 'source-change', 'worker', 'conversation'))
    pending_conversation = False
    with database(root) as db:
        initialized(db)
        previously_dirty = get(db, 'needs_reconcile', True)
        with db:
            # A killed process releases the OS lock. Its transaction is rolled back
            # by SQLite; the durable job is retryable at the next reconciliation.
            db.execute("UPDATE jobs SET state='pending',error='interrupted' WHERE state='running'")
            enqueue(db, reason)
            job = db.execute("SELECT id FROM jobs WHERE state='pending' ORDER BY id LIMIT 1").fetchone()[0]
            db.execute("UPDATE jobs SET state='running',error=NULL WHERE id=?", (job,))
            put(db, 'needs_reconcile', True)
        try:
            config = get(db, 'policy')
            selection = {'rich_paths': config['rich_paths']} if config.get('rich_paths') is not None else {}
            if get(db, 'reviewed_ocr_v1'):
                selection['ocr_records'] = get(db, 'reviewed_ocr_v1')
            captured = capture(root, config['scopes'], db, **selection)
            captured_files = staging.identity(captured)
            dev_edges = []
            if 'dev-memory' in config['scopes']:
                from .knowledge_dev import capture_dev
                dev_sources, dev_edges = capture_dev(root)
                captured.extend(dev_sources)
                captured_dev = (staging.identity(dev_sources), dev_edges)
            stamp = now()
            current_head = head(root)
            # Native conversations are lower-trust source records, not decisions.
            for row in db.execute("SELECT * FROM conversations WHERE retention!='none'"):
                body = '# ' + row['title'] + '\n\nConversation evidence; not an approved decision.\n\n' + row['summary']
                for turn in db.execute('SELECT * FROM turns WHERE conversation=? ORDER BY sequence', (row['id'],)):
                    body += '\n\n## ' + turn['role'] + '\n' + turn['content']
                path = 'conversation/' + row['id']
                captured.append({'id': 'conversation:' + row['id'], 'path': path, 'title': row['title'],
                                 'revision': digest(body), 'body': body, 'kind': 'conversation'})
            require(len(captured) <= LIMITS['sources'], 'source_budget')
            old = {r['id']: r for r in db.execute('SELECT id,path,revision,kind,deleted FROM sources')}
            from .knowledge_identity import bind
            bind(captured, old)
            changed, removed = [], []
            seen = {s['id'] for s in captured}
            pending = [s for s in captured if s['id'] not in old or old[s['id']]['deleted'] or
                       old[s['id']]['revision'] != s['revision'] or old[s['id']]['path'] != s['path']]
            removed_pending = any(identifier not in seen and not previous['deleted']
                                  for identifier, previous in old.items())
            checkpoint = staging.begin(db, root, config, current_head, captured, pending)
            if removed_pending:
                with db:
                    put(db, 'needs_reconcile', True)
            staging.prepare(db, checkpoint, pending, passages)
            if pending or removed_pending:
                require(staging.identity(capture(root, config['scopes'], **selection)) == captured_files and
                        head(root) == current_head, 'changed_input')
                if 'dev-memory' in config['scopes']:
                    verified_sources, verified_edges = capture_dev(root)
                    require((staging.identity(verified_sources), verified_edges) == captured_dev, 'changed_input')
            with db:
                for source in captured:
                    previous = old.get(source['id'])
                    if previous and not previous['deleted'] and previous['revision'] == source['revision'] and previous['path'] == source['path']:
                        continue
                    changed.append(source['path'])
                    db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,0,?)',
                               tuple(source[k] for k in ('id', 'path', 'title', 'revision', 'body', 'kind')) + (stamp,))
                    db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)',
                               (source['id'], source['revision'], source['body'], stamp))
                    db.execute('DELETE FROM chunks WHERE source=?', (source['id'],))
                    for chunk in staging.chunks(db, source):
                        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,NULL,NULL)',
                                   tuple(chunk[k] for k in ('id', 'source', 'revision', 'line', 'end_line', 'text')))
                for identifier, previous in old.items():
                    if identifier not in seen and not previous['deleted']:
                        removed.append(previous['path'])
                        db.execute('UPDATE sources SET deleted=1,updated=? WHERE id=?', (stamp, identifier))
                        db.execute('DELETE FROM chunks WHERE source=?', (identifier,))
                        db.execute('DELETE FROM wiki WHERE id=?', (identifier,))
                require(db.execute('SELECT count(*) FROM chunks').fetchone()[0] <= LIMITS['chunks'], 'chunk_budget')
                generation = get(db, 'generation', 0) + bool(changed or removed or previously_dirty or get(db, 'wiki_compiler') != wiki.COMPILER)
                if changed or removed or previously_dirty or get(db, 'wiki_compiler') != wiki.COMPILER:
                    db.execute('DELETE FROM edges')
                    by_path = {s['path']: s['id'] for s in captured}
                    for source in captured:
                        for target in set(references(source['body'], source['path'])):
                            if target in by_path:
                                db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?,?)',
                                           (source['id'], by_path[target], source['revision'], 'explicit_markdown_reference'))
                    by_id = {s['id']: s for s in captured}
                    for edge in dev_edges:
                        db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?,?)',
                                   (edge['source'], edge['target'], by_id[edge['source']]['revision'], edge['kind']))
                    compile_wiki(db, captured, stamp)
                    db.execute('DELETE FROM wiki WHERE id IN (SELECT id FROM sources WHERE deleted=1)')
                    wiki.compile_topics(db, captured, stamp)
                    put(db, 'wiki_compiler', wiki.COMPILER)
                commit_changed = current_head != get(db, 'head')
                put(db, 'head', current_head)
                put(db, 'generation', generation)
                if changed or removed:
                    put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
                from .knowledge_source_errors import published
                published(db)
                put(db, 'needs_reconcile', False)
                put(db, 'last_reconcile', stamp)
                put(db, 'last_change', {'changed': changed, 'removed': removed, 'commit_changed': commit_changed,
                                       'reason': reason, 'head': current_head, 'generation': generation})
                pending_conversation = bool(get(db, 'consolidation_pending_conversation', False))
                if pending_conversation:
                    put(db, 'consolidation_pending_conversation', False)
                db.execute("UPDATE jobs SET state='done',finished=?,error=NULL WHERE id=?", (stamp, job))
                db.execute('DELETE FROM jobs WHERE id NOT IN (SELECT id FROM jobs ORDER BY id DESC LIMIT 100)')
                staging.clear(db)
                scan.clear(db)
        except Exception as exc:
            with db:
                put(db, 'needs_reconcile', True)
                db.execute("UPDATE jobs SET state='failed',finished=?,error=? WHERE id=?",
                           (now(), str(exc)[:160] if isinstance(exc, KnowledgeError) else 'source_read_failed', job))
            raise
    if changed or removed or commit_changed or pending_conversation:
        from uuid import uuid4
        from .knowledge_consolidation_execution import dispatch as consolidation_execution
        trigger = ('conversation' if reason == 'conversation' or pending_conversation else
                   'commit' if commit_changed and not (changed or removed) else 'source')
        try:
            consolidation_execution(root, 'consolidation-queue',
                                    {'trigger': trigger, 'event_id': uuid4().hex})
        except KnowledgeError as exc:
            # Source reconciliation succeeds independently of optional analysis.
            with database(root) as queue_db:
                if queue_db is not None:
                    with queue_db:
                        put(queue_db, 'consolidation_queue_error', str(exc)[:160])
    return status(root)


def compile_wiki(db, sources, stamp):
    for source in sources:
        dependency = json.dumps([{'source': source['id'], 'path': source['path'], 'revision': source['revision']}])
        body = ('# ' + source['title'] + '\n\n> Source-bound extractive page. This is a derived reading aid, '
                'not an independently verified claim.\n\n' + re.sub(r'^#\s+[^\n]+\n?', '', source['body'], count=1, flags=re.M)[:6000])
        if len(source['body']) > 6000:
            body += '\n\n> Extract limited to 6,000 characters. Open the source for the complete document.'
        body += '\n\n## Provenance\n\nSource: `' + source['path'] + '`\n\nRevision: `' + source['revision'] + '`\n'
        wiki.store_page(db, source['id'], source['title'], body, json.loads(dependency), stamp)


def index(root, adapter=None):
    with database(root) as db:
        initialized(db)
        model = get(db, 'policy')['embedding']
        require(bool(model), 'embeddings_disabled')
    adapter = adapter or OllamaEmbedding(model)
    try:
        identity = adapter.pin()
        with database(root) as db:
            require(get(db, 'policy')['embedding'] == model, 'embedding_policy_changed')
            with db:
                if get(db, 'embedding_identity') != identity:
                    db.execute('UPDATE chunks SET vector=NULL,model=NULL')
                put(db, 'embedding_identity', identity)
                put(db, 'embedding_error', None)
            rows = list(db.execute('SELECT id,text FROM chunks WHERE vector IS NULL ORDER BY id LIMIT 16'))
        # Inference never holds the database writer lock. Archive writes and
        # reconciliation can proceed; content-bound chunk IDs reject late results.
        if rows:
            vectors = adapter.embed([r['text'] for r in rows])
            require(len(vectors) == len(rows), 'invalid_embeddings')
            require(len({len(v) for v in vectors}) == 1, 'embedding_dimension_changed')
            require(adapter.pin() == identity, 'embedding_model_changed')
            with database(root) as db:
                require(get(db, 'policy')['embedding'] == model and get(db, 'embedding_identity') == identity,
                        'embedding_policy_changed')
                with db:
                    for row, vector in zip(rows, vectors):
                        db.execute('UPDATE chunks SET vector=?,model=? WHERE id=?', (encode_vector(vector), identity, row['id']))
        with database(root) as db:
            with db:
                put(db, 'last_embedding', now())
    except KnowledgeError as exc:
        with database(root) as db:
            with db:
                if get(db, 'policy')['embedding'] == model:
                    put(db, 'embedding_error', str(exc))
        raise
    return status(root)


def status(root):
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            return {'schema_version': 1, 'enabled': False, 'defaults': DEFAULT, 'limits': LIMITS}
        counts = {table: db.execute('SELECT count(*) FROM ' + table + (' WHERE deleted=0' if table == 'sources' else '')).fetchone()[0]
                  for table in ('sources', 'chunks', 'edges', 'wiki', 'conversations', 'turns')}
        counts['embedded'] = db.execute('SELECT count(*) FROM chunks WHERE vector IS NOT NULL').fetchone()[0]
        from .knowledge_following import read as following
        from .knowledge_work import snapshot as work_snapshot
        return {'schema_version': 1, 'enabled': True, 'policy': get(db, 'policy'), 'counts': counts,
                'generation': get(db, 'generation', 0), 'work_revision': work_snapshot(db), 'last_reconcile': get(db, 'last_reconcile'),
                'needs_reconcile': get(db, 'needs_reconcile', True), 'head': get(db, 'head'),
                'last_change': get(db, 'last_change'), 'embedding_identity': get(db, 'embedding_identity'),
                'embedding_error': get(db, 'embedding_error'), 'worker_heartbeat': get(db, 'worker_heartbeat'),
                'source_errors': list(get(db, 'source_diagnostics', {}).values()),
                'ingestion': get(db, staging.HEADER), 'following': following(db),
                'acquisition': get(db, scan.PREFIX + 'progress'),
                'jobs': [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 10')],
                'limits': LIMITS}


def catalog(root):
    with database(root) as db:
        initialized(db)
        return {'sources': [dict(r) for r in db.execute('SELECT id,path,title,revision,kind,updated,substr(body,1,300) AS excerpt FROM sources WHERE deleted=0 ORDER BY path')],
                'wiki': [{**{k: r[k] for k in ('id', 'title', 'revision', 'updated')},
                          'source_count': len(json.loads(r['dependencies'])),
                          'kind': 'draft' if r['id'].startswith('wiki:draft:') else ('reviewed' if r['id'].startswith('wiki:review:') else 'topic'),
                          'stale': bool(wiki.freshness(db, json.loads(r['dependencies'])))}
                         for r in db.execute("SELECT id,title,revision,updated,dependencies FROM wiki WHERE id LIKE 'wiki:%' ORDER BY title")],
                'edges': [dict(r) for r in db.execute('SELECT * FROM edges LIMIT 2000')],
                'conversations': [dict(r) for r in db.execute('SELECT id,title,retention,status,updated,substr(summary,1,300) AS summary FROM conversations ORDER BY updated DESC LIMIT 200')]}


def page(root, identifier, revision=None):
    require(type(identifier) is str and len(identifier) <= 180)
    with database(root) as db:
        initialized(db)
        row = db.execute('SELECT * FROM wiki WHERE id=?', (identifier,)).fetchone()
        require(row is not None, 'page_unavailable')
        result = dict(row)
        if revision is not None:
            require(type(revision) is str and re.fullmatch('[a-f0-9]{64}', revision))
            older = db.execute('SELECT * FROM wiki_history WHERE id=? AND revision=?', (identifier, revision)).fetchone()
            require(older is not None, 'page_revision_unavailable')
            result.update(dict(older))
            result['historical'] = row['revision'] != revision
        result['dependencies'] = json.loads(result['dependencies'])
        result['issues'] = wiki.freshness(db, result['dependencies'])
        result['stale'] = bool(result['issues']) or get(db, 'needs_reconcile', True)
        result['history'] = [dict(r) for r in db.execute('SELECT revision,updated FROM wiki_history WHERE id=? ORDER BY updated DESC LIMIT 30', (identifier,))]
        return result


def notes(root, identifier, value):
    require(type(value) is str and len(value) <= 8000)
    with database(root) as db:
        initialized(db)
        with db:
            changed = db.execute('UPDATE wiki SET notes=? WHERE id=?', (value, identifier)).rowcount
            require(changed == 1, 'page_unavailable')
            put(db, 'wiki_notes:' + identifier, value)
    return page(root, identifier)


def conversation(root, value):
    require(type(value) is dict and set(value) == {'id', 'title', 'retention', 'summary', 'status', 'turns'})
    require(type(value['id']) is str and re.fullmatch('[A-Za-z0-9_-]{1,80}', value['id']))
    require(type(value['title']) is str and 0 < len(value['title']) <= 180)
    require(type(value['summary']) is str and len(value['summary']) <= 8000)
    require(value['retention'] in ('none', 'summary', 'transcript') and value['status'] in ('active', 'closed', 'interrupted'))
    require(type(value['turns']) is list and len(value['turns']) <= 64)
    require(len(json.dumps(value)) <= 60000, 'conversation_limit')
    for turn in value['turns']:
        require(type(turn) is dict and set(turn) == {'id', 'sequence', 'role', 'content', 'provenance'})
        require(type(turn['id']) is str and re.fullmatch('[A-Za-z0-9_-]{1,100}', turn['id']))
        require(type(turn['sequence']) is int and 0 <= turn['sequence'] <= 10000)
        require(turn['role'] in ('user', 'assistant') and type(turn['content']) is str and len(turn['content']) <= 16000)
        require(type(turn['provenance']) is dict and len(json.dumps(turn['provenance'])) <= 4000)
        require(set(turn['provenance']) <= {'provider', 'model', 'context', 'prompt_hash', 'origin', 'incomplete', 'error'})
    with database(root) as db:
        initialized(db)
        require(get(db, 'policy')['retention'] != 'none' or value['retention'] == 'none', 'retention_disabled')
        require(get(db, 'policy')['retention'] != 'summary' or value['retention'] != 'transcript', 'transcript_disabled')
        with db:
            if value['retention'] == 'none':
                forget(db, value['id'])
                return {'saved': False, 'retention': 'none'}
            require(db.execute('SELECT count(*) FROM conversations').fetchone()[0] < LIMITS['conversations'] or
                    db.execute('SELECT 1 FROM conversations WHERE id=?', (value['id'],)).fetchone(), 'conversation_limit')
            db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                       "title=excluded.title,retention=excluded.retention,summary=CASE WHEN excluded.summary='' THEN conversations.summary ELSE excluded.summary END,status=excluded.status,updated=excluded.updated",
                       (value['id'], value['title'], value['retention'], value['summary'], value['status'], now()))
            if value['retention'] == 'summary':
                db.execute('DELETE FROM turns WHERE conversation=?', (value['id'],))
                purge_conversation_projection(db, value['id'])
            else:
                for turn in value['turns']:
                    prior = db.execute('SELECT * FROM turns WHERE id=? OR (conversation=? AND sequence=?)',
                                       (turn['id'], value['id'], turn['sequence'])).fetchone()
                    serialized = json.dumps(turn['provenance'], sort_keys=True)
                    if prior:
                        require(prior['id'] == turn['id'] and prior['conversation'] == value['id'] and prior['sequence'] == turn['sequence']
                                and prior['role'] == turn['role'] and prior['content'] == turn['content'] and prior['provenance'] == serialized,
                                'conversation_event_conflict')
                        continue
                    db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?)',
                               (turn['id'], value['id'], turn['sequence'], turn['role'], turn['content'], serialized, now()))
                require(db.execute('SELECT count(*) FROM turns WHERE conversation=?', (value['id'],)).fetchone()[0] <= 512, 'conversation_limit')
                require(db.execute('SELECT coalesce(sum(length(content)),0) FROM turns WHERE conversation=?',
                                   (value['id'],)).fetchone()[0] <= 180000, 'conversation_limit')
                exported = [dict(r) for r in db.execute('SELECT id,sequence,role,content,provenance FROM turns WHERE conversation=?', (value['id'],))]
                require(len(json.dumps(exported, ensure_ascii=False).encode()) <= 50000, 'conversation_limit')
            put(db, 'needs_reconcile', True)
            put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
            enqueue(db, 'conversation saved')
            if value['status'] == 'closed':
                put(db, 'consolidation_pending_conversation', True)
        return {'saved': True, 'id': value['id'], 'retention': value['retention']}


def purge_conversation_projection(db, identifier):
    staging.clear(db)
    scan.clear(db)
    source = 'conversation:' + identifier
    from .knowledge_consolidation_privacy import purge
    purge(db, source)
    wiki.purge_dependents(db, source)
    for table, column in [('sources', 'id'), ('revisions', 'source'), ('chunks', 'source'), ('wiki', 'id'), ('wiki_history', 'id')]:
        db.execute('DELETE FROM ' + table + ' WHERE ' + column + '=?', (source,))
    db.execute('DELETE FROM edges WHERE source=? OR target=?', (source, source))


def forget(db, identifier):
    put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    db.execute('DELETE FROM conversations WHERE id=?', (identifier,))
    db.execute('DELETE FROM turns WHERE conversation=?', (identifier,))
    purge_conversation_projection(db, identifier)
    put(db, 'consolidation_pending_conversation', False)
    put(db, 'needs_reconcile', True)


def read_conversation(root, identifier):
    require(type(identifier) is str and len(identifier) <= 80)
    with database(root) as db:
        initialized(db)
        row = db.execute('SELECT * FROM conversations WHERE id=?', (identifier,)).fetchone()
        require(row is not None, 'conversation_unavailable')
        return {**dict(row), 'turns': [{**dict(t), 'provenance': json.loads(t['provenance'])} for t in db.execute(
            'SELECT * FROM turns WHERE conversation=? ORDER BY sequence LIMIT 512', (identifier,))]}


def query(root, text, semantic=True, adapter=None):
    from .knowledge_query import query as run_query
    return run_query(root, text, semantic, adapter)


def dispatch(root, action, value=None):
    if action in ('consolidation-settings', 'consolidation-prepare', 'consolidation-attempt',
                  'consolidation-import', 'consolidation-fail', 'consolidation-queue',
                  'consolidation-prune'):
        from .knowledge_consolidation_execution import dispatch as execution_dispatch
        return execution_dispatch(root, action, value)
    if action == 'consolidation-context':
        from .knowledge_consolidation_context import read
        return read(root, value)
    from .knowledge_consolidation import ACTIONS as CONSOLIDATION_ACTIONS, dispatch as consolidate
    if type(action) is str and action in CONSOLIDATION_ACTIONS:
        return consolidate(root, action, value)
    require(action in ('status', 'configure', 'sync', 'index', 'catalog', 'page', 'page-revision', 'notes', 'conversation',
                       'conversation-read', 'query', 'forget', 'wiki-lint', 'wiki-draft', 'library', 'catalog-window', 'graph-view', 'graph-source', 'source-retry', 'work-view', 'work-propose', 'work-review', 'work-schedule-view', 'work-schedule-save', 'ocr-preview', 'ocr-save', 'wiki-review-view', 'wiki-review-propose', 'wiki-review-decide'))
    if action in ('work-schedule-view', 'work-schedule-save'):
        from .knowledge_work_schedule import dispatch as schedule_dispatch
        reconcile(root)
        return schedule_dispatch(root, action, value)
    if action in ('wiki-review-view', 'wiki-review-propose', 'wiki-review-decide'):
        from .knowledge_wiki_review import dispatch as review_dispatch
        return review_dispatch(root, action, value)
    if action in ('ocr-preview', 'ocr-save'):
        from .knowledge_ocr import dispatch as ocr_dispatch
        return ocr_dispatch(root, action, value)
    if action in ('work-view', 'work-propose', 'work-review'):
        from .knowledge_work import dispatch as work_dispatch
        return work_dispatch(root, action, value)
    if action == 'source-retry':
        from .knowledge_source_errors import retry
        return retry(root, value)
    if action in ('graph-view', 'graph-source'):
        from .knowledge_graph import read, source
        return (read if action == 'graph-view' else source)(root, value)
    if action == 'catalog-window':
        from .knowledge_catalog import read
        return read(root, value)
    if action == 'library':
        from .knowledge_library import read
        return read(root, value)
    if action == 'wiki-draft':
        return page(root, wiki.save_draft(root, value))
    if action == 'wiki-lint':
        require(value is None)
        with database(root) as db:
            initialized(db)
            return wiki.lint(db)
    if action == 'status':
        require(value is None)
        return status(root)
    if action == 'configure':
        return configure(root, value)
    if action == 'sync':
        return reconcile(root, value or 'manual')
    if action == 'index':
        require(value is None)
        return index(root)
    if action == 'catalog':
        require(value is None)
        return catalog(root)
    if action == 'page':
        return page(root, value)
    if action == 'page-revision':
        require(type(value) is dict and set(value) == {'id', 'revision'})
        return page(root, value['id'], value['revision'])
    if action == 'notes':
        require(type(value) is dict and set(value) == {'id', 'text'})
        return notes(root, value['id'], value['text'])
    if action == 'conversation':
        return conversation(root, value)
    if action == 'conversation-read':
        return read_conversation(root, value)
    if action == 'query':
        require(type(value) is dict and set(value) == {'text', 'semantic'})
        return query(root, value['text'], value['semantic'])
    require(type(value) is str and re.fullmatch('[A-Za-z0-9_-]{1,80}', value))
    with database(root) as db:
        initialized(db)
        with db:
            forget(db, value)
    return {'forgotten': value}
