"""Bounded-handle source acquisition with private resumable content checkpoints."""
import json
import os
from pathlib import Path
import re
import time

from cc_setup_service import _Snapshots, _root
from cc_layout import is_contained, managed_path, source_path
from .knowledge_store import ordinary, get, put, KnowledgeError
from .knowledge_import import EXTRACTOR_VERSION

PREFIX = 'source-scan/'
VERSION = 2


def clear(db):
    db.execute("DELETE FROM meta WHERE key GLOB 'source-scan/*'")


def fingerprint(path, directory=False):
    s = ordinary(path, directory)
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def capture(root, scopes, db=None, rich_paths=None, ocr_records=None):
    from .knowledge_sources import LIMITS, KnowledgeReadPolicy, digest
    root = _root(str(root))
    policy = KnowledgeReadPolicy()
    started = time.monotonic()
    files, directories, sidecars, entries = {}, {}, {}, 0

    def tick():
        if time.monotonic() - started > LIMITS['seconds']:
            raise KnowledgeError('source_budget')

    def listing(path):
        # Each reader holds only this directory's ancestor chain, then closes.
        with _Snapshots(root, {}, policy) as reader:
            if reader.observe(path, {}, directory=True) is None:
                return None
            fd = reader.entries[path][2]
            with os.scandir(path if os.name == 'nt' else fd) as it:
                names = []
                for item in it:
                    names.append(item.name)
                    if len(names) > LIMITS['entries']:
                        raise KnowledgeError('source_budget')
            reader.recheck()
        return sorted(names)

    def walk(relative, recursive=False, depth=0, kind='document'):
        nonlocal entries
        tick()
        logical = Path(relative)
        logical_name = logical.as_posix()
        owned_namespace = logical.parts and logical.parts[0] in ('.controlcoding', '.controlwork')
        path = root if logical_name == '.' else source_path(root, logical_name) if owned_namespace else root / logical
        names = listing(path)
        directories[logical.as_posix()] = names
        if names is None:
            return
        entries += len(names)
        if entries > LIMITS['entries']:
            raise KnowledgeError('source_budget')
        for name in names:
            tick()
            if name.startswith('.') or name in ('node_modules', '__pycache__'):
                continue
            if any(c in name for c in ':\\/') or name.endswith(('.', ' ')):
                raise KnowledgeError('unsupported_path')
            child = path / name
            child_logical = logical / name
            if child.is_dir():
                if recursive:
                    ordinary(child, True)
                    if depth >= 7:
                        raise KnowledgeError('source_budget')
                    walk(child_logical, True, depth + 1, kind)
            elif name.upper() != 'AGENTS.MD' and (
                    kind == 'rich-document' and child.suffix.lower() in ('.docx', '.xlsx', '.pdf') or
                    kind != 'rich-document' and (child.suffix.lower() in ('.md', '.txt') or
                    kind in ('session', 'receipt') and child.suffix.lower() == '.json')):
                key = child_logical.as_posix()
                if kind == 'rich-document' and rich_paths is not None and key not in rich_paths:
                    continue
                physical_key = child.relative_to(root).as_posix()
                files.setdefault(key, (kind, fingerprint(child), physical_key))
                if kind == 'rich-document' and child.suffix.lower() == '.pdf':
                    companion = Path(str(child) + '.ocr.json')
                    sidecars[key] = (fingerprint(companion) if os.path.lexists(companion) else None,
                                     companion.relative_to(root).as_posix())
                if len(files) > LIMITS['files']:
                    raise KnowledgeError('source_budget')

    for scope in scopes:
        if scope == 'project':
            walk(Path('.'))
            walk(Path('docs'), True)
        elif scope == 'rich-documents':
            walk(Path('.'), kind='rich-document')
            walk(Path('docs'), True, kind='rich-document')
        elif scope == 'work':
            for area in ('inbox', 'sources', 'notes', 'ideas', 'decisions', 'plans', 'outputs'):
                walk(Path('.controlwork/memory') / area, True, kind=area)
            walk(Path('.controlwork/sessions'), True, kind='session')
        elif scope == 'dev-views':
            walk(Path('.controlcoding/memory/views'), True, kind='dev-view')
        elif scope == 'dev-memory':
            for folder in ('verification_receipts', 'invariants_receipts'):
                walk(Path('.controlcoding') / folder, kind='receipt')
        else:
            walk(Path('_work') / ('plans' if scope == 'plans' else 'handoff'), True, kind=scope)
    if is_contained(root) and 'project' in scopes:
        # These CC-owned canonical documents moved physically, but their source
        # IDs and stored paths remain in the established project namespace.
        for name in ('CONTROLCODING.md', 'CONTROLWORK.md', 'PROJECT.md', 'STATUS.md', 'ROADMAP.md', 'BUGS.md'):
            physical = managed_path(root, name)
            if physical.is_file():
                logical = 'cc/' + name
                files.setdefault(logical, ('document', fingerprint(physical), physical.relative_to(root).as_posix()))
    manifest = digest(json.dumps([VERSION, EXTRACTOR_VERSION, str(root), scopes, rich_paths, files, directories, sidecars, ocr_records], sort_keys=True))
    if db is not None and get(db, PREFIX + 'manifest') != manifest:
        with db:
            clear(db)
            put(db, PREFIX + 'manifest', manifest)
    from .knowledge_checkpoints import Writer
    writer = Writer(db, PREFIX + 'progress', first=get(db, PREFIX + 'progress') is None) if db is not None else None
    sources, used = [], 0
    failures = []
    from .knowledge_source_errors import failure, success
    from .knowledge_read_batches import SnapshotBatches
    with SnapshotBatches(root, _Snapshots) as readers:
        for relative, (kind, signature, physical_relative) in sorted(files.items()):
            tick()
            companion_signature, companion_relative = sidecars.get(relative, (None, None))
            used += signature[2] + (companion_signature[2] if companion_signature else 0)
            cap = 1024 * 1024 if kind == 'rich-document' else LIMITS['file_bytes']
            if used > LIMITS['total_bytes']:
                raise KnowledgeError('source_budget')
            key = PREFIX + digest(relative)
            source = get(db, key) if db is not None else None
            if source is not None and 'reuse' in source:
                stored = db.execute('SELECT id,path,title,revision,body,kind FROM sources WHERE id=? AND revision=? AND path=? AND deleted=0',
                                    (source['reuse'], source['revision'], relative)).fetchone()
                source = dict(stored) if stored else None
                if source is not None:
                    source['id'] = 'source:' + digest(relative)
            try:
                if signature[2] > cap or (companion_signature and companion_signature[2] > 65536):
                    raise KnowledgeError('source_budget')
                if source is None:
                    raw, sidecar_raw = readers.read(physical_relative, cap, companion_signature is not None)
                    if ocr_records and relative in ocr_records and kind == 'rich-document':
                        sidecar_raw = json.dumps(ocr_records[relative]).encode('utf-8')
                    revision = digest(raw)
                    if kind == 'rich-document':
                        from .knowledge_import import extract
                        from cc_panel_configuration import ConfigurationError
                        try:
                            body = ('# ' + Path(relative).name + '\n\n> Automatically extracted, unverified source text. '
                                    'Citation line numbers refer to this extraction, not original document pages.\n\n'
                                    + extract(relative, raw, sidecar_raw))
                        except ConfigurationError as exc:
                            raise KnowledgeError(str(exc)) from exc
                        revision = digest(json.dumps([EXTRACTOR_VERSION, digest(raw), digest(sidecar_raw) if sidecar_raw is not None else None]))
                    else:
                        body = raw.decode('utf-8-sig')
                    if kind == 'session' and relative.lower().endswith('.json'):
                        value = json.loads(body)
                        if not isinstance(value, dict) or value.get('schemaVersion') != 1:
                            raise KnowledgeError('invalid_session_source')
                        body = '# ' + str(value.get('topic', Path(relative).name)) + '\n\n' + str(value.get('summary', ''))
                        for field in ('decisions', 'followups', 'notes', 'links'):
                            body += '\n\n## ' + field.title() + '\n' + json.dumps(value.get(field, []), ensure_ascii=False, indent=2)
                    if kind == 'receipt':
                        body = '# Recorded verification receipt: ' + Path(relative).name + '\n\nObserved receipt, not revalidated against current source inputs.\n\n```json\n' + json.dumps(json.loads(body), ensure_ascii=False, indent=2) + '\n```'
                    heading = re.search(r'^#\s+(.+)', body, re.M)
                    source = {'id': 'source:' + digest(relative), 'path': relative,
                              'title': (heading[1].strip() if heading else Path(relative).name)[:180],
                              'revision': revision, 'body': body, 'kind': kind,
                              'physicalPath': physical_relative}
                    if db is not None:
                        stored = db.execute('SELECT id,revision FROM sources WHERE path=? AND revision=? AND deleted=0',
                                            (relative, source['revision'])).fetchone()
                        checkpoint = {'reuse': stored['id'], 'revision': stored['revision']} if stored else source
                        writer.add(key, checkpoint, {'captured': len(sources) + 1, 'total': len(files)})
                sources.append(source)
                success(db, relative)
            except (KnowledgeError, UnicodeError, ValueError) as exc:
                failure(db, relative, digest(json.dumps([signature, companion_signature])), exc)
                failures.append(exc)

    if writer is not None:
        writer.flush()
    if failures:
        raise failures[0]
    # Metadata validation guards normal edits across bounded reads. The service
    # performs a fresh uncached content capture before publishing changed sources.
    for relative, (_, signature, physical_relative) in files.items():
        tick()
        if fingerprint(root / physical_relative) != signature:
            raise KnowledgeError('changed_input')
    for relative, (signature, physical_relative) in sidecars.items():
        companion = root / physical_relative
        if (fingerprint(companion) if os.path.lexists(companion) else None) != signature:
            raise KnowledgeError('changed_input')
    for relative, names in directories.items():
        tick()
        logical = Path(relative)
        owned_namespace = logical.parts and logical.parts[0] in ('.controlcoding', '.controlwork')
        physical = root if relative == '.' else source_path(root, relative) if owned_namespace else root / logical
        if listing(physical) != names:
            raise KnowledgeError('changed_input')
    return sources
