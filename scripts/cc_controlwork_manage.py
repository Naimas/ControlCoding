"""Reviewed, bounded additions to the portable ControlWork project plane."""
import datetime
import os
from pathlib import Path
import re

from cc_memory_lib import commands, work_features as work
from cc_setup_service import _Snapshots, _root, ReadPolicy
from cc_panel_configuration import encoded, digest, require, text, relative, load_json, _inputs
from cc_panel_transaction import commit, JOURNAL
from cc_controlwork_observer import DIRECTORIES, MAX_RECORDS

AREAS = tuple(work.CAPTURE_AREAS)
FIELDS = {'init': {'name', 'purpose'}, 'capture': {'title', 'body', 'area'},
          'import': {'area'}, 'session': {'title', 'body', 'decisions', 'followups'},
          'conversation': {'title', 'messages', 'provider'}}


def validate(value):
    require(type(value) is dict and type(value.get('action')) is str and value['action'] in FIELDS, 'invalid_memory_request')
    action = value['action']
    require(set(value) == FIELDS[action] | {'action'}, 'invalid_memory_request')
    out = {'action': action}
    for key in FIELDS[action]:
        if key == 'messages':
            require(type(value[key]) is list and 1 <= len(value[key]) <= 24, 'invalid_memory_request')
            out[key] = []
            for row in value[key]:
                require(type(row) is dict and set(row) == {'role', 'content'} and row['role'] in ('user', 'assistant'), 'invalid_memory_request')
                out[key].append({'role': row['role'], 'content': text(row['content'], 12000, True)})
        elif key in ('decisions', 'followups'):
            require(type(value[key]) is list and len(value[key]) <= 16, 'invalid_memory_request')
            out[key] = [text(v, 400) for v in value[key]]
        else:
            out[key] = text(value[key], 8000 if key == 'body' else 1600 if key == 'purpose' else 120, key in ('body', 'purpose'))
    if action in ('capture', 'import'):
        require(out['area'] in AREAS, 'invalid_area')
    require(all(out.get(k, True) for k in ('name', 'title', 'body', 'purpose')), 'empty_content')
    require(len(encoded(out)) <= 40000, 'memory_limit')
    return out


def prepare(root_value, value, source_paths, request_id, timestamp):
    value = validate(value)
    require(type(request_id) is str and re.fullmatch('[a-f0-9]{32}', request_id), 'invalid_memory_request')
    require(type(timestamp) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', timestamp), 'invalid_memory_request')
    datetime.datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')
    require(type(source_paths) is list and len(source_paths) <= 8, 'invalid_memory_request')
    sources = sorted(set(relative(p) for p in source_paths))
    action = value['action']
    require(bool(sources) if action == 'import' else not sources, 'invalid_memory_request')
    from cc_memory_lib.knowledge_import import SUPPORTED, extract
    require(all(Path(p).suffix.lower() in SUPPORTED for p in sources), 'unsupported_document')
    root = _root(root_value)
    scripts = Path(__file__).absolute().parent
    trusted = {scripts / p: 'scripts/'+p for p in ('cc_controlwork_manage.py', 'cc_panel_configuration.py',
        'cc_panel_transaction.py', 'cc_setup_service.py', 'cc_project_map_definition.py', 'cc_controlwork_observer.py',
        'cc_memory_lib/commands.py', 'cc_memory_lib/work_features.py',
        'cc_memory_lib/knowledge_import.py', 'cc_memory_lib/extractors.py')}
    with _Snapshots(root, trusted, ReadPolicy()) as reader:
        require(reader.observe(root, {}, directory=True) is not None, 'missing_root')
        require(reader.observe(root / JOURNAL, {}) is None, 'recovery_required')
        for path in trusted:
            require(reader.observe(path, {}) is not None, 'missing_source')
        def observe(name, cap=65536):
            snap = reader.observe(root / name, {})
            require(snap is None or len(snap[-1]) <= cap, 'memory_limit')
            return snap
        context = observe('CONTROLWORK.md')
        config = observe('.controlwork/config.json')
        categories = observe('.controlwork/categories.json')
        graph_review = observe('.controlwork/graph-suggestions.json')
        if config:
            cfg = load_json(config[-1])
            require(type(cfg.get('schemaVersion')) is int and cfg['schemaVersion'] == 1 and cfg.get('product') == 'ControlWork', 'invalid_memory')
        if categories:
            cat = load_json(categories[-1])
            require(type(cat.get('schemaVersion')) is int and cat['schemaVersion'] == 1 and type(cat.get('categories')) is list, 'invalid_memory')
        # Keep new records within the same visible archive budget. Enumeration
        # is bounded and membership participates in the preview identity.
        memberships, existing_records = {}, set()
        for folder in DIRECTORIES:
            path = root / folder
            if reader.observe(path, {}, directory=True) is None:
                continue
            names = []
            with os.scandir(path) as entries:
                for entry in entries:
                    names.append(entry.name)
                    require(len(names) <= 512, 'memory_limit')
            memberships[folder] = sorted(names)
            require(sum(len(v) for v in memberships.values()) <= 512, 'memory_limit')
            suffix = '.json' if folder.endswith('/sessions') else '.md'
            existing_records.update(folder+'/'+n for n in names if n.lower().endswith(suffix))
        outputs, preserve = {}, set()
        if action == 'init':
            require(not config or cfg.get('baseDocument', 'PROJECT.md') == 'PROJECT.md'
                    and cfg.get('project', {}).get('baseDocumentPath', 'PROJECT.md') == 'PROJECT.md', 'custom_layout')
            outputs = {'CONTROLWORK.md': commands._controlwork_context_template(value['name'], value['purpose']).encode('utf-8'),
                       'PROJECT.md': work.project_base_document_template(value['name'], value['purpose']).encode('utf-8'),
                       '.controlwork/config.json': encoded(commands._controlwork_config()),
                       '.controlwork/categories.json': encoded(work._default_registry(timestamp))}
            folders = [work.MEMORY_ROOT / area for area in work.AREAS] + [work.CHECKPOINT_ROOT, work.PROPOSAL_ROOT,
                work.CONTEXT_PACKET_ROOT, work.SESSION_ROOT, work.INGESTION_ROOT, work.EXTRACTS_ROOT]
            outputs.update({(p / '.gitkeep').as_posix(): b'' for p in folders})
            preserve = set(outputs)
        else:
            require(context is not None and config is not None and categories is not None, 'memory_not_initialized')
            stamp = timestamp.replace('-', '').replace(':', '')
            if action == 'capture':
                name = 'panel-'+request_id+'-'+work.slug(value['title'])[:60]+'.md'
                outputs[(work.MEMORY_ROOT / value['area'] / name).as_posix()] = commands._work_capture_content(
                    value['area'], value['title'], value['body'], 'captured', 'Manual panel capture', '', stamp).encode('utf-8')
            elif action in ('session', 'conversation'):
                session_id = 'panel-'+request_id
                body = value['body'] if action == 'session' else 'Conversation recorded from '+value['provider']+'. Advisory, unverified content.'
                record = work.session_start_payload(session_id, value['title'], body, 'continue_previous_work', 'manual', [], timestamp)
                record.update(status='completed', endedAt=timestamp,
                              decisions=value.get('decisions', []), followups=value.get('followups', []))
                if action == 'conversation':
                    record['notes'] = [{'createdAt': timestamp, 'kind': row['role'], 'text': row['content']} for row in value['messages']]
                outputs[(work.SESSION_ROOT / (session_id+'.json')).as_posix()] = encoded(record)
            else:
                for source in sources:
                    snap = observe(source, 1024 * 1024)
                    require(snap is not None, 'missing_source')
                    sidecar = observe(source + '.ocr.json') if source.lower().endswith('.pdf') else None
                    body = extract(source, snap[-1], sidecar[-1] if sidecar else None)
                    identity = digest(source.encode('utf-8')+b'\0'+snap[-1] +
                                      (b'\0' + sidecar[-1] if sidecar else b''))
                    name = 'import-'+identity[:24]+'.md'
                    path = (work.MEMORY_ROOT / value['area'] / name).as_posix()
                    generated = commands._work_capture_content(value['area'], Path(source).stem, body, 'captured', source, '', stamp)
                    existing = observe(path)
                    if existing:
                        # Re-import preserves the original capture date only if all
                        # other bytes still match this source and Core format.
                        old = existing[-1].decode('utf-8')
                        normalize = lambda s: re.sub(r'^- \*\*Captured\*\*: [^\n]*$', '- **Captured**: (preserved)', s, count=1, flags=re.M)
                        require(normalize(old) == normalize(generated), 'record_conflict')
                        generated = old
                    outputs[path] = generated.encode('utf-8')
        files, changes, blockers = [], {}, []
        for name, data in sorted(outputs.items()):
            require(len(data) <= 65536, 'memory_limit')
            snap = observe(name)
            keep = snap is not None and (name in preserve or snap[-1] == data)
            op = 'keep' if keep else 'conflict' if snap else 'create'
            if op == 'conflict':
                blockers.append('Existing record differs: '+name)
            if op == 'create':
                changes[root / name] = data
            intended = snap[-1] if keep else data
            files.append({'path': name, 'action': op, 'bytes': len(intended), 'sha256': digest(intended),
                          'before_sha256': digest(snap[-1]) if snap else None,
                          'preview': intended.decode('utf-8', errors='replace')[:8000], 'truncated': len(intended.decode('utf-8', errors='replace')) > 8000})
        reader.recheck()
        resulting_records = existing_records | {p for p in outputs if any(p.startswith(d+'/') for d in DIRECTORIES) and p.endswith(('.md', '.json'))}
        require(len(resulting_records) + 1 + int(graph_review is not None) <= MAX_RECORDS, 'memory_limit')
        public = {'schema_version': 1, 'action': action, 'root': root_value, 'files': files, 'blockers': blockers,
                  'write_supported': os.name == 'nt', 'sources': sources, 'inputs': reader.fingerprints(), 'membership': memberships,
                  'notice': 'Portable ControlWork records only. No Dev Plane database, code scan, provider call, host adapter or automatic chat capture. Imported/captured knowledge is not verified.'}
        public['approval_id'] = digest(encoded(public))
        return public, (root, _inputs(reader), trusted, changes)


def save(root_value, value, source_paths, request_id, timestamp, approval_id):
    public, args = prepare(root_value, value, source_paths, request_id, timestamp)
    require(public['approval_id'] == approval_id, 'preview_mismatch')
    require(not public['blockers'], 'record_conflict')
    result = commit(*args, approval_id) if args[-1] else {'saved': True, 'files': [], 'directories_created': []}
    return {**result, 'action': public['action'], 'verified': False}
