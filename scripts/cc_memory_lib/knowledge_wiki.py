"""Source-bound topic compiler and explicitly saved, unreviewed AI wiki drafts.

Pages are projections, never retrieval evidence. Classification is deterministic
and explained; quoted blocks retain source revision and exact line anchors.
"""
import json
import re

from .knowledge_sources import digest
from .knowledge_store import KnowledgeError, database, get, put, now

COMPILER = 5
TOPICS = (
    ('sessions', 'Conversations and continuity', r'conversation|session|handoff'),
    ('decisions', 'Decisions and rationale', r'decision|rationale|\badr\b'),
    ('plans', 'Development plans and requirements', r'plan|roadmap|requirement|backlog'),
    ('verification', 'Verification and evidence', r'verif|test|evidence|invariant|review|security|trust'),
    ('architecture', 'Architecture and interfaces', r'architecture|design|contract|interface|\bapi\b|schema'),
    ('operations', 'Operations and adoption', r'install|setup|adopt|deploy|release|operat|runbook|quick.start'),
    ('knowledge', 'Memory and knowledge', r'memory|knowledge|wiki|controlwork|graph|rag'),
    ('reference', 'Project reference', r'.'),
)


def classify(source):
    label = source['kind'] + ' ' + source['path'] + ' ' + source['title']
    for key, title, pattern in TOPICS:
        match = re.search(pattern, label, re.I)
        if match:
            return key, title, ('Fallback: no named topic rule matched.' if key == 'reference'
                                else 'Matched "' + match[0] + '" in source kind, path or title.')


def store_page(db, identifier, title, body, dependencies, stamp):
    encoded = json.dumps(dependencies, sort_keys=True)
    revision = digest(body + encoded)
    previous = db.execute('SELECT revision FROM wiki WHERE id=?', (identifier,)).fetchone()
    if previous and previous[0] == revision:
        return
    notes = get(db, 'wiki_notes:' + identifier, '')
    db.execute('INSERT INTO wiki(id,title,body,revision,dependencies,notes,updated) VALUES(?,?,?,?,?,?,?) '
               'ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,revision=excluded.revision,'
               'dependencies=excluded.dependencies,updated=excluded.updated',
               (identifier, title, body, revision, encoded, notes, stamp))
    db.execute('INSERT OR IGNORE INTO wiki_history VALUES(?,?,?,?,?)',
               (identifier, revision, body, encoded, stamp))


def compile_topics(db, sources, stamp):
    groups = {}
    for source in sorted(sources, key=lambda s: s['path']):
        key, title, reason = classify(source)
        groups.setdefault(key, (title, []))[1].append((source, reason))
    current = set()
    for key, (title, members) in groups.items():
        identifier = 'wiki:topic:' + key
        current.add(identifier)
        body = '# ' + title + '\n\n> Automatic source digest. Quoted evidence is not independent verification.\n'
        body += '\nSources are grouped by an explicit, ordered rule on kind, path and title. Each assignment is explained below.\n'
        dependencies = []
        for number, (source, reason) in enumerate(members[:80], 1):
            lines = source['body'].splitlines()
            # Quote the opening non-heading evidence, without inventing prose.
            start = next((i for i, line in enumerate(lines) if line.strip() and not line.startswith('#')), 0)
            selected = []
            for line in lines[start:start + 12]:
                if sum(map(len, selected)) + len(line) > 1000:
                    break
                selected.append(line)
            if not selected:
                selected = [lines[start][:1000] if lines else '']
            excerpt = '\n'.join(selected)
            dep = {'source': source['id'], 'path': source['path'], 'revision': source['revision'],
                   'line': start + 1, 'end_line': start + len(selected), 'citation': 'S' + str(number),
                   'excerpt': excerpt}
            dependencies.append(dep)
            body += '\n## ' + source['title'].replace('\n', ' ') + '\n\n' + reason + '\n\n'
            body += '\n'.join('> ' + line for line in selected)
            body += '\n\n[S' + str(number) + '] `' + source['path'] + '` lines ' + str(dep['line']) + '-' + str(dep['end_line'])
            body += ' | ' + source['kind'] + ' | revision `' + source['revision'] + '`\n'
        if len(members) > 80:
            body += '\n> This digest shows 80 of ' + str(len(members)) + ' sources. The source library lists the full scope.\n'
        store_page(db, identifier, title, body, dependencies, stamp)
    for row in db.execute("SELECT id,notes FROM wiki WHERE id LIKE 'wiki:topic:%'").fetchall():
        if row['id'] not in current:
            put(db, 'wiki_notes:' + row['id'], row['notes'])
            db.execute('DELETE FROM wiki WHERE id=?', (row['id'],))
    from .knowledge_wiki_review import compile_pages
    compile_pages(db, sources, stamp)


def freshness(db, dependencies):
    issues = []
    for dep in dependencies:
        row = db.execute('SELECT revision,deleted,body,path FROM sources WHERE id=?', (dep['source'],)).fetchone()
        code = None
        if row is None or row['deleted']:
            code = 'source_missing'
        elif row['revision'] != dep['revision'] or row['path'] != dep['path']:
            code = 'source_changed'
        elif 'line' in dep:
            lines = row['body'].splitlines()
            if not 1 <= dep['line'] <= dep['end_line'] <= max(1, len(lines)):
                code = 'anchor_invalid'
            elif dep.get('excerpt') and dep['excerpt'] not in '\n'.join(lines[dep['line'] - 1:dep['end_line']]):
                # A very long source line is split into bounded provider chunks.
                chunk = db.execute('SELECT 1 FROM chunks WHERE source=? AND revision=? AND line=? '
                                   'AND end_line=? AND text=?', (dep['source'], dep['revision'], dep['line'],
                                   dep['end_line'], dep['excerpt'])).fetchone()
                if chunk is None:
                    code = 'anchor_invalid'
        if code:
            issues.append({'source': dep['source'], 'path': dep['path'], 'code': code})
    return issues


def lint(db):
    issues, pages, anchors, checks = [], 0, {}, {'original_links': 0, 'dependencies': 0}
    for row in db.execute('SELECT id,body,dependencies FROM wiki'):
        pages += 1
        deps = json.loads(row['dependencies'])
        checks['dependencies'] += len(deps)
        issues.extend({'page': row['id'], **issue} for issue in freshness(db, deps))
        if row['id'].startswith('wiki:draft:'):
            if not deps:
                issues.append({'page': row['id'], 'code': 'orphan_page'})
            ids = {d['citation'] for d in deps}
            if len(ids) != len(deps):
                issues.append({'page': row['id'], 'code': 'duplicate_citation_id'})
            for identifier in set(re.findall(r'\[(S\d+)\]', row['body'])) - ids:
                issues.append({'page': row['id'], 'code': 'citation_invalid', 'citation': identifier})
    from cc_documentation_observer import references
    paths = {r[0] for r in db.execute('SELECT path FROM sources WHERE deleted=0')}
    for row in db.execute('SELECT id,path,body FROM sources WHERE deleted=0'):
        seen = set()
        for identifier in re.findall(r'\{#([\w-]+)\}|<a\s+id=[\"\x27]([\w-]+)', row['body']):
            identifier = next(x for x in identifier if x)
            if identifier in seen:
                issues.append({'page': row['id'], 'code': 'duplicate_anchor_id', 'anchor': identifier})
            seen.add(identifier)
        for target in set(references(row['body'], row['path'])):
            checks['original_links'] += 1
            if target not in paths:
                issues.append({'page': row['id'], 'code': 'link_outside_index_or_missing', 'path': target})
    return {'pages': pages, 'issues': issues[:1000], 'truncated': len(issues) > 1000, 'checked_at': now(), 'checks': checks,
            'notice': 'Checks original links, duplicate explicit IDs, source revisions and anchors. Links outside the index may be intentionally out of scope. Semantic findings require review.'}


def save_draft(root, value):
    required = {'title', 'body', 'citations', 'generation', 'provider', 'model'}
    if (type(value) is not dict or set(value) != required or type(value['title']) is not str
            or not 0 < len(value['title']) <= 180 or type(value['body']) is not str
            or not 0 < len(value['body']) <= 16000 or type(value['generation']) is not int
            or type(value['citations']) is not list or not 0 < len(value['citations']) <= 10
            or any(type(value[k]) is not str or len(value[k]) > 180 for k in ('provider', 'model'))):
        raise KnowledgeError('invalid_wiki_draft')
    with database(root) as db:
        if db is None or get(db, 'needs_reconcile', True) or value['generation'] != get(db, 'generation'):
            raise KnowledgeError('wiki_context_changed')
        deps = []
        for citation in value['citations']:
            if (type(citation) is not dict or not {'id', 'source', 'path', 'revision', 'line', 'end_line', 'excerpt'} <= set(citation)
                    or type(citation['id']) is not str or not re.fullmatch(r'S(?:[1-9]|10)', citation['id'])):
                raise KnowledgeError('invalid_wiki_citation')
            row = db.execute('SELECT 1 FROM chunks c JOIN sources s ON s.id=c.source WHERE s.deleted=0 '
                             'AND s.revision=c.revision AND c.source=? AND s.path=? AND c.revision=? '
                             'AND c.line=? AND c.end_line=? AND c.text=?', tuple(citation[k] for k in
                             ('source', 'path', 'revision', 'line', 'end_line', 'excerpt'))).fetchone()
            if row is None:
                raise KnowledgeError('wiki_context_changed')
            deps.append({**{k: citation[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt')},
                         'citation': citation['id']})
        ids = {d['citation'] for d in deps}
        used = set(re.findall(r'\[(S\d+)\]', value['body']))
        if len(ids) != len(deps) or not used or used - ids:
            raise KnowledgeError('invalid_wiki_citation')
        for paragraph in re.split(r'\n\s*\n', value['body']):
            prose = '\n'.join(line for line in paragraph.splitlines() if line.strip()
                              and not re.fullmatch(r'#{1,6}\s+.*', line))
            if prose and not re.search(r'\[S\d+\]', prose):
                raise KnowledgeError('wiki_uncited_paragraph')
        body = '# ' + value['title'].replace('\n', ' ') + '\n\n> Unreviewed AI synthesis; never independent evidence.\n\n'
        body += value['body'] + '\n\n## Generator\n\n' + value['provider'] + ' / ' + value['model'] + '\n'
        identifier = 'wiki:draft:' + digest(value['title'])
        with db:
            store_page(db, identifier, value['title'], body, deps, now())
            put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
        return identifier


def purge_dependents(db, source):
    """Forgetting a transcript also removes retained derived extracts of it."""
    from .knowledge_wiki_review import purge_source
    purge_source(db, source)
    for table in ('wiki', 'wiki_history'):
        for row in db.execute('SELECT id,revision,dependencies FROM ' + table).fetchall():
            if any(d['source'] == source for d in json.loads(row['dependencies'])):
                db.execute('DELETE FROM ' + table + ' WHERE id=? AND revision=?', (row['id'], row['revision']))
