"""External-only CC facade: immutable source observations, external knowledge.

Invoke with an installed Python: python -I -B /installed/scripts/cc_external.py.
The original product is data, never a working directory or import/command target.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

sys.dont_write_bytecode = True

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_external_boundary import (ExternalError, absolute, disjoint, inside,
                                  ordinary, safe_tree, selection, install_guard)

MAX_REQUEST = 128 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
LIMITS = {'files': 5000, 'entries': 60000, 'bytes': 128 * 1024 * 1024,
          'file_bytes': 16 * 1024 * 1024, 'document_bytes': 262144, 'seconds': 45}
ACTIONS = {'init', 'status', 'refresh', 'catalog', 'query', 'page', 'document',
           'records', 'record', 'conversation', 'conversation-read'}
CAPABILITIES = {'source_read_only': True, 'lexical_search': True, 'extractive_wiki': True,
                'document_graph': True, 'conversations': True, 'governance_records': True,
                'hooks': False, 'apply': False, 'commands': False, 'agents': False,
                'semantic_search': False, 'ocr': False, 'rich_extraction': False}
POLICY = {'scopes': ['project'], 'automatic': False, 'worker': False,
          'retention': 'transcript', 'embedding': ''}


def stamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def identity(path):
    info = ordinary(path, True)
    return [info.st_dev, info.st_ino]


def read_bytes(path, maximum):
    info = ordinary(path)
    if info.st_size > maximum:
        raise ExternalError('file_budget')
    with path.open('rb') as stream:
        current = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ExternalError('source_changed')
        value = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    final = ordinary(path)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    # Windows path stat and CRT fstat expose different ctime meanings on some
    # supported Python versions. Compare ctime only within the same API.
    if (len(value) > maximum or signature(info) != signature(after) or signature(info) != signature(final)
            or info.st_ctime_ns != final.st_ctime_ns or current.st_ctime_ns != after.st_ctime_ns):
        raise ExternalError('source_changed')
    return value


def read_json(path, maximum=MAX_RESPONSE):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ExternalError('invalid_metadata')
            result[key] = value
        return result
    return json.loads(read_bytes(path, maximum), object_pairs_hook=pairs)


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_pair(source, workspace):
    source, workspace = absolute(source), absolute(workspace, exists=False)
    disjoint(source, workspace)
    for protected in (Path(__file__).resolve().parent.parent, Path(sys.executable).resolve().parent):
        disjoint(workspace, protected)
    # Installing/running Core inside the product would defeat the external contract.
    if inside(Path(__file__).resolve(), source) or inside(Path(sys.executable).resolve(), source):
        raise ExternalError('runtime_inside_source')
    return source, workspace


def descriptor(workspace):
    value = read_json(workspace / 'external.json', 16384)
    if (type(value) is not dict or set(value) != {'schema', 'mode', 'source', 'workspace', 'source_identity', 'include'}
            or value['schema'] != 1 or value['mode'] != 'external'):
        raise ExternalError('invalid_descriptor')
    source, current = validate_pair(value['source'], workspace)
    if str(current) != value['workspace'] or str(source) != value['source']:
        raise ExternalError('workspace_binding_changed')
    if identity(source) != value['source_identity']:
        raise ExternalError('source_identity_changed')
    value['include'] = selection(value['include'])
    return value


def capture(config):
    root = absolute(config['source'])
    if identity(root) != config['source_identity']:
        raise ExternalError('source_identity_changed')
    seen, bodies, total, entries = {}, {}, 0, 0
    started = time.monotonic()
    pending = [root / name for name in config['include']]
    while pending:
        path = pending.pop()
        entries += 1
        if entries > LIMITS['entries'] or time.monotonic() - started > LIMITS['seconds']:
            raise ExternalError('source_budget')
        # Every ancestor is checked, including an explicitly selected nested file.
        for ancestor in reversed(path.parents):
            if inside(ancestor, root):
                ordinary(ancestor, True)
        if path.is_dir():
            ordinary(path, True)
            if len(path.relative_to(root).parts) > 5:
                raise ExternalError('source_depth_budget')
            with os.scandir(path) as iterator:
                children = []
                for entry in iterator:
                    if entry.name.lower() in ('.git', '.hg', '.svn', 'node_modules', '__pycache__'):
                        continue
                    children.append(Path(entry.path))
                    if len(children) + entries > LIMITS['entries']:
                        raise ExternalError('source_budget')
            pending.extend(sorted(children))
            continue
        name = path.relative_to(root).as_posix()
        if name in seen:
            continue
        # Reject Windows aliases, ambiguous filenames and case collisions on all hosts.
        selection([name])
        raw = read_bytes(path, LIMITS['file_bytes'])
        total += len(raw)
        if len(seen) >= LIMITS['files'] or total > LIMITS['bytes']:
            raise ExternalError('source_budget')
        row = {'sha256': digest(raw), 'bytes': len(raw), 'document': False}
        if path.suffix.lower() in ('.md', '.txt'):
            if len(raw) > LIMITS['document_bytes']:
                raise ExternalError('document_budget')
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeError:
                raise ExternalError('document_encoding') from None
            if '\x00' in text:
                raise ExternalError('document_encoding')
            # Markdown/plain-text projection preserves line order and content.
            # Hidden names and AGENTS are stored with neutral stable filenames.
            projection = 'docs/source/' + digest(name.encode()) + '.md'
            row.update(document=True, projection=projection)
            bodies[projection] = text
        seen[name] = row
    if len({name.casefold() for name in seen}) != len(seen):
        raise ExternalError('source_case_collision')
    return dict(sorted(seen.items())), bodies


@contextmanager
def lease(workspace):
    path = workspace / 'operation.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'0')
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise ExternalError('workspace_busy') from None
        yield
    finally:
        if locked:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == 'nt':
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def state_read(workspace):
    path = workspace / 'state.json'
    if not path.exists():
        return {'schema': 1, 'generation': 0, 'manifest': {}, 'records': [],
                'fresh': False, 'observed_at': None, 'error': 'refresh_required', 'changes': []}
    state = read_json(path)
    if type(state) is not dict or state.get('schema') != 1 or type(state.get('manifest')) is not dict or type(state.get('records')) is not list:
        raise ExternalError('invalid_state')
    return state


def archive_hash(workspace):
    path = workspace / 'projection/.controlcoding/knowledge/knowledge.db'
    before = ordinary(path)
    if before.st_size > 512 * 1024 * 1024:
        raise ExternalError('archive_budget')
    result = hashlib.sha256()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ExternalError('archive_changed')
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    after = ordinary(path)
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ExternalError('archive_changed')
    return result.hexdigest()


def check_sources(config, workspace, state):
    try:
        observed, bodies = capture(config)
        previous = state['manifest']
        changed = sorted(p for p in set(previous) | set(observed) if previous.get(p) != observed.get(p))
        state['changes'] = changed
        if (state.get('archive_sha256') and state.get('error') != 'refresh_incomplete'
                and archive_hash(workspace) != state['archive_sha256']):
            state['archive_untrusted'] = True
        if state.get('archive_untrusted'):
            state.update(fresh=False, error='archive_changed')
            raise ExternalError('archive_changed')
        if changed or not state['generation']:
            state.update(fresh=False, error='stale_sources' if state['generation'] else 'refresh_required')
        if state['fresh']:
            if any(read_bytes(workspace / 'projection' / p, LIMITS['document_bytes']) != b.encode('utf-8')
                   for p, b in bodies.items()):
                state.update(fresh=False, error='projection_changed')
        for record in state['records']:
            if any(observed.get(ref['path'], {}).get('sha256') != ref['sha256'] for ref in record['sources']):
                record['stale'] = True
        # A dependent decision cannot remain current after its supporting record
        # was invalidated. Invalidation is sticky; review creates a new record.
        for _ in range(len(state['records'])):
            stale = {r['id'] for r in state['records'] if r['stale']}
            dependents = [r for r in state['records'] if not r['stale'] and any(link in stale for link in r['links'])]
            if not dependents:
                break
            for record in dependents:
                record['stale'] = True
        write_json(workspace / 'state.json', state)
        return observed, bodies
    except (OSError, ExternalError) as error:
        state.update(fresh=False, error=str(error) if isinstance(error, ExternalError) else 'source_unavailable')
        for record in state['records']:
            record['stale'] = True
        write_json(workspace / 'state.json', state)
        raise ExternalError(state['error']) from None


def status(config, workspace, state):
    return {'mode': 'external', 'source': config['source'], 'workspace': str(workspace),
            'include': config['include'], 'observed_at': state['observed_at'],
            'generation': state['generation'], 'fresh': state['fresh'], 'error': state['error'],
            'changes': state['changes'], 'files': len(state['manifest']),
            'indexed_documents': sum(bool(v['document']) for v in state['manifest'].values()),
            'records': state['records'], 'capabilities': CAPABILITIES, 'limits': LIMITS}


def engine(workspace, action, value=None):
    from cc_memory_lib.knowledge_service import dispatch
    from cc_memory_lib.knowledge_sources import external_snapshot
    with external_snapshot():
        return dispatch(workspace / 'projection', action, value)


def refresh(config, workspace, state, manifest, bodies):
    state.update(fresh=False, error='refresh_incomplete')
    write_json(workspace / 'state.json', state)
    project = workspace / 'projection'
    destination = project / 'docs' / 'source'
    destination.mkdir(parents=True, exist_ok=True)
    expected = {Path(p).name for p in bodies}
    for old in destination.iterdir():
        ordinary(old)
        if old.name not in expected:
            old.unlink()
    for name, body in bodies.items():
        path = project / name
        data = body.encode('utf-8')
        if not path.exists() or read_bytes(path, LIMITS['document_bytes']) != data:
            path.write_bytes(data)
    engine(workspace, 'configure', POLICY)
    engine(workspace, 'sync')
    # Projection filenames are neutral and stable even for hidden original paths.
    # Build explicit document edges from ORIGINAL relative links, not copy names.
    from cc_memory_lib.knowledge_store import database
    from cc_memory_lib.knowledge_sources import references
    with database(project) as db:
        rows = {r['path']: dict(r) for r in db.execute('SELECT id,path,revision FROM sources WHERE deleted=0')}
        by_original = {p: rows[v['projection']] for p, v in manifest.items()
                       if v['document'] and v['projection'] in rows}
        with db:
            db.execute("DELETE FROM edges WHERE kind='original_document_link'")
            for name, row in by_original.items():
                for target in set(references(bodies[manifest[name]['projection']], name)):
                    if target in by_original:
                        db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?,?)',
                                   (row['id'], by_original[target]['id'], row['revision'], 'original_document_link'))
    verified, _ = capture(config)
    if verified != manifest:
        raise ExternalError('source_changed')
    state.update(manifest=manifest, generation=state['generation'] + 1, fresh=True,
                 error=None, changes=[], observed_at=stamp(), archive_sha256=archive_hash(workspace))
    write_json(workspace / 'state.json', state)
    return status(config, workspace, state)


def provenance(value, config, state):
    """Keep engine locators distinct; bind citations to original bytes/paths."""
    by_projection = {v.get('projection'): (p, v) for p, v in state['manifest'].items() if v['document']}
    def visit(item):
        if isinstance(item, list):
            return [visit(v) for v in item]
        if not isinstance(item, dict):
            return item
        result = {k: visit(v) for k, v in item.items()}
        name = result.get('path')
        if isinstance(name, str) and name in by_projection:
            original, row = by_projection[name]
            result.update(cached_path=name, path=original, original_path=original,
                          source_sha256=row['sha256'], source_root=config['source'])
            if 'physicalPath' in result:
                result['physicalPath'] = original
        return result
    return visit(value)


def save_record(value, state):
    kinds = {'objective', 'phase', 'task', 'blocker', 'decision', 'evidence', 'note'}
    if (type(value) is not dict or not {'kind', 'title', 'body', 'sources', 'links'} <= set(value)
            or set(value) - {'id', 'kind', 'title', 'body', 'sources', 'links'}
            or value['kind'] not in kinds or type(value['title']) is not str or not 0 < len(value['title']) <= 180
            or type(value['body']) is not str or len(value['body']) > 8000
            or type(value['sources']) is not list or not 0 < len(value['sources']) <= 128
            or type(value['links']) is not list or len(value['links']) > 128):
        raise ExternalError('invalid_record')
    if len(state['records']) >= 2000:
        raise ExternalError('record_budget')
    identifier = value.get('id', uuid.uuid4().hex)
    if type(identifier) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,80}', identifier):
        raise ExternalError('invalid_record')
    existing = {r['id'] for r in state['records']}
    if identifier in existing or any(type(v) is not str or v not in existing for v in value['links']):
        raise ExternalError('record_conflict')
    for ref in value['sources']:
        if (type(ref) is not dict or set(ref) != {'path', 'sha256'} or type(ref['path']) is not str
                or type(ref['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', ref['sha256'])
                or ref['path'] not in state['manifest']
                or state['manifest'][ref['path']]['sha256'] != ref['sha256']):
            raise ExternalError('stale_evidence')
    stale_ids = {r['id'] for r in state['records'] if r['stale']}
    record = dict(value, id=identifier, created=stamp(), generation=state['generation'],
                  stale=any(link in stale_ids for link in value['links']))
    state['records'].append(record)
    if len(encoded(state)) > MAX_RESPONSE:
        raise ExternalError('record_budget')
    return record


def dispatch(request):
    if (type(request) is not dict or set(request) != {'action', 'workspace', 'value'}
            or type(request['action']) is not str or request['action'] not in ACTIONS):
        raise ExternalError('unsupported_external_action')
    action, value = request['action'], request['value']
    if action in ('status', 'refresh', 'catalog', 'records') and value is not None:
        raise ExternalError('invalid_external_value')
    workspace = absolute(request['workspace'], exists=action != 'init')
    if action == 'init':
        if type(value) is not dict or set(value) != {'source', 'include'}:
            raise ExternalError('invalid_initialization')
        source, workspace = validate_pair(value['source'], workspace)
        config = {'schema': 1, 'mode': 'external', 'source': str(source), 'workspace': str(workspace),
                  'source_identity': identity(source), 'include': selection(value['include'])}
        if workspace.exists() and any(workspace.iterdir()):
            raise ExternalError('workspace_not_empty')
        # Validate source scope and budgets before creating any external state.
        capture(config)
    else:
        config = descriptor(workspace)
        safe_tree(workspace)
    # Guard is deliberately process lifetime, never temporarily removed.
    install_guard(workspace)
    if action == 'init':
        workspace.mkdir(parents=True, exist_ok=True)
    temp = workspace / 'tmp'
    temp.mkdir(exist_ok=True)
    os.environ.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
                      PYTHONDONTWRITEBYTECODE='1', SQLITE_TMPDIR=str(temp))
    import tempfile
    tempfile.tempdir = str(temp)
    os.chdir(workspace)
    with lease(workspace):
        if action == 'init':
            if any(p.name not in ('operation.lock', 'tmp') for p in workspace.iterdir()):
                raise ExternalError('workspace_not_empty')
            write_json(workspace / 'external.json', config)
        state = state_read(workspace)
        try:
            manifest, bodies = check_sources(config, workspace, state)
        except ExternalError:
            if action in ('status', 'records'):
                return status(config, workspace, state) if action == 'status' else state['records']
            raise
        if action in ('init', 'status'):
            return status(config, workspace, state)
        if action == 'records':
            return state['records']
        if action == 'refresh':
            return refresh(config, workspace, state, manifest, bodies)
        if not state['fresh']:
            raise ExternalError('stale_sources')
        if action == 'record':
            saved = save_record(value, state)
            verified, _ = capture(config)
            if verified != manifest:
                raise ExternalError('source_changed')
            write_json(workspace / 'state.json', state)
            return saved
        if action == 'document':
            if type(value) is not str or not state['manifest'].get(value, {}).get('document'):
                raise ExternalError('document_unavailable')
            row = state['manifest'][value]
            check_sources(config, workspace, state)
            if not state['fresh']:
                raise ExternalError('stale_sources')
            return {'path': value, 'sha256': row['sha256'], 'source_root': config['source'],
                    'markdown': bodies[row['projection']]}
        if action == 'query':
            value = {'text': value, 'semantic': False, 'retrieval': {'graph': False, 'wiki': False}}
        if action == 'conversation':
            state.update(fresh=False, error='refresh_incomplete')
            write_json(workspace / 'state.json', state)
        result = engine(workspace, action, value)
        if action == 'conversation':
            # Archive and derived search/wiki become one successfully observed
            # result. A crash before publication leaves a retryable refresh.
            refresh(config, workspace, state, manifest, bodies)
        if action == 'page':
            sources = engine(workspace, 'catalog')['sources']
            dependencies = {d['source'] for d in result['dependencies']}
            result['original_sources'] = [s for s in sources if s['id'] in dependencies]
        # The conversation action deliberately changes this external archive;
        # seal only after successful trusted engine work, never before validation.
        state['archive_sha256'] = archive_hash(workspace)
        # Catch a change during derived retrieval too, not just at its beginning.
        check_sources(config, workspace, state)
        if not state['fresh']:
            raise ExternalError('stale_sources')
        return provenance(result, config, state)


def respond(request):
    try:
        return {'ok': True, 'result': dispatch(request)}
    except ExternalError as error:
        return {'ok': False, 'error': str(error)}
    except Exception as error:
        from cc_memory_lib.knowledge_store import KnowledgeError
        if isinstance(error, KnowledgeError):
            return {'ok': False, 'error': str(error)}
        return {'ok': False, 'error': 'external_operation_failed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stdio', action='store_true')
    parser.add_argument('--workspace')
    parser.add_argument('--source')
    parser.add_argument('--include', action='append')
    parser.add_argument('--value', help='JSON value for record/conversation, string for query/page/document')
    parser.add_argument('action', nargs='?', choices=sorted(ACTIONS))
    args = parser.parse_args()
    if args.stdio:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        try:
            if len(raw) > MAX_REQUEST:
                raise ValueError()
            request = json.loads(raw)
        except (ValueError, UnicodeError):
            request = None
    else:
        if not args.action or not args.workspace:
            parser.error('action and --workspace are required')
        value = args.value
        if args.action == 'init':
            value = {'source': args.source, 'include': args.include}
        elif args.action in ('record', 'conversation'):
            try:
                value = json.loads(value or 'null')
            except ValueError:
                parser.error('--value requires valid JSON')
        request = {'action': args.action, 'workspace': args.workspace, 'value': value}
    response = respond(request)
    data = encoded(response)
    if len(data) > MAX_RESPONSE:
        response = {'ok': False, 'error': 'response_budget'}
        data = encoded(response)
    sys.stdout.buffer.write(data)
    return 0 if response['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
