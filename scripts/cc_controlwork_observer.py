"""Bounded desktop view of embedded ControlWork; never initializes or writes memory."""

import hashlib
import json
import os
from pathlib import Path
import time

from cc_setup_service import ReadPolicy, SetupServiceError, _Snapshots, _root
from cc_memory_lib import work_features as work

POLICY = ReadPolicy(file_bytes=65536, target_bytes=4 * 1024 * 1024)
MAX_RECORDS = 96
MAX_ENTRIES = 512
MAX_OUTPUT = 768 * 1024
DIRECTORIES = [*(str(work.MEMORY_ROOT / area).replace('\\', '/') for area in work.CAPTURE_AREAS),
               '.controlwork/sessions', '.controlwork/checkpoints', '.controlwork/context-packets']
SCOPE = ['CONTROLWORK.md', *[p + ('/*.json' if p.endswith('/sessions') else '/*.md') for p in DIRECTORIES],
         '.controlwork/graph-suggestions.json']


class WorkObserverError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _scope(root, reader):
    identity = reader.observe(root, {}, directory=True)
    if identity is None:
        raise WorkObserverError('root_unavailable')
    binding = {'root': os.path.normcase(str(root)), 'identity': identity, 'paths': SCOPE,
               'policy': 'controlwork-observer-v1', 'records': MAX_RECORDS, 'file_bytes': POLICY.file_bytes}
    return {'scope_id': _hash(json.dumps(binding, sort_keys=True).encode()), 'paths': SCOPE,
            'max_records': MAX_RECORDS, 'file_bytes': POLICY.file_bytes,
            'notice': 'Reads local memory text and session summaries. No initialization, indexing, external links, model calls or chat collection.'}


def _json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise WorkObserverError('invalid_memory')
            out[key] = value
        return out
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 4096 or depth > 16:
            raise WorkObserverError('memory_limit')
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
    if type(value) is not dict:
        raise WorkObserverError('invalid_memory')
    return value


def _session(data):
    value = _json(data)
    if type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1 or not value.get('id'):
        raise WorkObserverError('invalid_memory')
    for key in ('id', 'topic', 'summary', 'status', 'startedAt', 'updatedAt', 'endedAt', 'operator', 'mode'):
        if key in value and type(value[key]) is not str:
            raise WorkObserverError('invalid_memory')
    result = {k: value.get(k, '') for k in ('id', 'topic', 'summary', 'status', 'startedAt', 'updatedAt', 'endedAt')}
    for key in ('categories', 'memoryChanged', 'packets', 'decisions', 'followups'):
        items = value.get(key, [])
        if type(items) is not list or len(items) > 128 or any(type(v) is not str for v in items):
            raise WorkObserverError('invalid_memory')
        result[key] = items
    for key, fields in (('links', ('type', 'target', 'targetType')), ('notes', ('createdAt', 'kind', 'text'))):
        items = value.get(key, [])
        if type(items) is not list or len(items) > 128 or any(type(v) is not dict or any(type(v.get(f, '')) is not str for f in fields) for v in items):
            raise WorkObserverError('invalid_memory')
        result[key] = [{f: v.get(f, '') for f in fields} for v in items]
    return result


def observe(root_value, *, preview=False, scope_id=None, query=''):
    """Project paths are main-owned; query is text, never a path/command."""
    try:
        root = _root(root_value)
        if type(query) is not str or len(query) > 240 or any(ord(c) < 32 for c in query):
            raise WorkObserverError('invalid_query')
        started = time.monotonic()
        def tick():
            if time.monotonic() - started > 8:
                raise WorkObserverError('time_limit')
        with _Snapshots(root, {}, POLICY) as reader:
            scope = _scope(root, reader)
            if preview:
                reader.recheck()
                return {'project_root': root_value, 'work_scope': scope}
            if scope_id != scope['scope_id']:
                raise WorkObserverError('scope_mismatch')
            found_root = reader.observe(root / '.controlwork', {}, directory=True) is not None
            captured, directories = {}, {}
            inspected = 0
            def names(path):
                tick()
                fd = reader.entries[path][2]
                result = []
                with os.scandir(path if os.name == 'nt' else fd) as items:
                    for item in items:
                        result.append(item.name)
                        if len(result) > MAX_ENTRIES:
                            raise WorkObserverError('memory_limit')
                return sorted(result)
            def capture(relative):
                tick()
                snap = reader.observe(root / relative, {})
                if snap is not None:
                    if len(captured) >= MAX_RECORDS:
                        raise WorkObserverError('memory_limit')
                    captured[relative] = snap[-1]
            capture('CONTROLWORK.md')
            capture('.controlwork/graph-suggestions.json')
            for relative in DIRECTORIES:
                directory = root / relative
                if reader.observe(directory, {}, directory=True) is None:
                    continue
                listed = names(directory)
                inspected += len(listed)
                if inspected > MAX_ENTRIES:
                    raise WorkObserverError('memory_limit')
                directories[directory] = listed
                suffix = '.json' if relative.endswith('/sessions') else '.md'
                for name in listed:
                    if name.lower().endswith(suffix):
                        if any(c in name for c in ':\\/') or name.endswith(('.', ' ')):
                            raise WorkObserverError('unsupported_path')
                        capture(relative + '/' + name)
            reader.recheck()
            if any(names(p) != old for p, old in directories.items()):
                raise WorkObserverError('changed_input')
            entries, documents, sessions = [], [], []
            review_state = {'schemaVersion': 1, 'suggestions': {}}
            chunks = 0
            for relative, data in sorted(captured.items()):
                tick()
                if relative.endswith('graph-suggestions.json'):
                    review_state = _json(data)
                    if review_state.get('schemaVersion') != 1 or type(review_state.get('suggestions')) is not dict:
                        raise WorkObserverError('invalid_memory')
                    for item in review_state['suggestions'].values():
                        if type(item) is not dict or item.get('status') not in ('accepted', 'rejected'):
                            raise WorkObserverError('invalid_memory')
                    continue
                if relative.startswith('.controlwork/sessions/'):
                    session = _session(data)
                    entry = work.session_to_graph_entry(session)
                    entry['path'] = relative
                    sessions.append({**session, 'path': relative, 'node_id': entry['id']})
                else:
                    text = data.decode('utf-8-sig')
                    parsed = work.entry_from_text(root, root / relative, text)
                    if not relative.startswith('.controlwork/memory/'):
                        parsed.update(body=text, area='context' if relative == 'CONTROLWORK.md' else Path(relative).parent.name,
                                      lifecycle='active' if relative == 'CONTROLWORK.md' else 'captured')
                    entry = {**parsed, 'id': work.stable_graph_id('CW_NODE', relative), 'type': work.entry_node_type(parsed),
                             'tokens': sorted(set(work.graph_tokens(' '.join(parsed.get(k, '') for k in ('title', 'body', 'source', 'category')))))}
                    if relative == 'CONTROLWORK.md':
                        entry.update(title='CONTROLWORK.md', source='canonical_context', tokens=sorted(set(work.graph_tokens(text))))
                    documents.append({'id': entry['id'], **{k: str(entry.get(k, '')) for k in ('title', 'path', 'area', 'lifecycle', 'source', 'category', 'captured')},
                                      'excerpt': text[:8000], 'excerpt_truncated': len(text) > 8000, 'sha256': _hash(data)})
                chunks += len(work.split_markdown_chunks(entry.get('body', ''), relative, entry.get('lifecycle', '')))
                if chunks > 512:
                    raise WorkObserverError('memory_limit')
                entries.append(entry)
            if len({e['id'] for e in entries}) != len(entries):
                raise WorkObserverError('invalid_memory')
            # Preserve Core entry order: suggestion IDs include ordered endpoints.
            contexts = [e for e in entries if e['path'] == 'CONTROLWORK.md']
            memory_entries = [e for area in work.CAPTURE_AREAS for e in entries
                              if e['path'].startswith(f'.controlwork/memory/{area}/')]
            session_entries = sorted([e for e in entries if e['type'] == 'session'],
                                     key=lambda e: e['session'].get('updatedAt') or e['session'].get('startedAt', ''), reverse=True)
            included = {e['id'] for e in contexts + memory_entries + session_entries}
            entries = contexts + memory_entries + session_entries + [e for e in entries if e['id'] not in included]
            graph = work.graph_from_entries(entries, review_state)
            # Audit-only and rejected suggestions are displayed but do not influence UI retrieval.
            graph_for_query = {**graph, 'suggestions': [s for s in graph['suggestions'] if s.get('status') == 'suggested' and not s.get('auditOnly')]}
            notices = ['Partial scope: portable local records only. Dev Plane SQLite, external sources, ingestion freshness and full chat transcripts are not inspected.',
                       'Search ranks recorded titles, headings and metadata using the Core graph retriever; no semantic model or generated answer.']
            packet = None
            if query.strip():
                retrieval = work.retrieve_from_graph(graph_for_query, query.strip(), 10, False, {'warnings': ['Source ingestion freshness was not inspected by this observer.']})
                packet = work.rag_pack_from_retrieval(retrieval, query.strip())
            result = {'schema_version': 1, 'scope_id': scope['scope_id'], 'state': 'present' if found_root else 'context_only' if documents else 'absent',
                      'documents': documents, 'sessions': sorted(sessions, key=lambda s: s['updatedAt'] or s['startedAt'], reverse=True),
                      'graph': graph, 'packet': packet, 'query': query.strip(), 'notices': notices,
                      'counts': {'records': len(captured), 'bytes': reader.used['target'], 'directories': len(directories)},
                      'snapshot_id': _hash(json.dumps({p: _hash(v) for p, v in captured.items()}, sort_keys=True).encode())}
            tick()
            if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_OUTPUT:
                raise WorkObserverError('memory_limit')
            return {'project_root': root_value, 'work': result}
    except WorkObserverError:
        raise
    except SetupServiceError as error:
        raise WorkObserverError({'too_large': 'memory_limit', 'input_limit': 'memory_limit'}.get(error.code, error.code)) from None
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise WorkObserverError('invalid_memory') from None
