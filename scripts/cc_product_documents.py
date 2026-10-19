"""Bounded source observations and reviewed Project Document file projections.

These helpers never create a Core decision. Arbitrary Markdown is an observed
source; only our versioned exports support identity-preserving roundtrip.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re

from cc_evidence_inputs import SafeRoot, Budget, EvidenceError
from cc_development_lib.schema import DevelopmentError

LIMIT = 128 * 1024
SOURCE_FIELDS = {'schema_version', 'source_id', 'kind', 'owner', 'path', 'sha256',
                 'observed_at', 'content', 'sections'}
SECTION_FIELDS = {'id', 'title', 'start_line', 'end_line', 'text', 'selected',
                  'disposition', 'targets', 'reason'}


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _sections(content):
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if re.match(r'^#{1,6}\s+\S', line)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    if len(starts) > 128:
        raise DevelopmentError('source_section_limit')
    result = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        text = ''.join(lines[start:end])
        title = re.sub(r'^#{1,6}\s+', '', lines[start]).strip() if lines and re.match(r'^#{1,6}\s+', lines[start]) else 'Text'
        result.append({'id': f'section-{n + 1}', 'title': title, 'start_line': start + 1,
                       'end_line': end, 'text': text, 'selected': False,
                       'disposition': 'unresolved', 'targets': [], 'reason': ''})
    return result


def observe_source(root, path='', *, text=''):
    if type(path) is not str or type(text) is not str or bool(path) == bool(text):
        raise DevelopmentError('invalid_source_selection')
    try:
        if path:
            if not path.lower().endswith(('.md', '.markdown', '.txt')):
                raise DevelopmentError('unsupported_source_format')
            with SafeRoot(root) as safe:
                raw, _ = safe.read(path, Budget(per_file=LIMIT, total=LIMIT))
            content = raw.decode('utf-8-sig')
        else:
            raw = text.encode('utf-8')
            content = text
        if len(raw) > LIMIT or not content.strip() or '\x00' in content:
            raise DevelopmentError('source_size_or_content_limit')
    except (EvidenceError, UnicodeError, OSError) as error:
        raise DevelopmentError(getattr(error, 'code', 'source_unreadable')) from None
    digest = _hash(raw)
    return {'schema_version': 1, 'source_id': 'src-' + digest[:16],
            'kind': 'file' if path else 'user_text', 'owner': 'project' if path else 'user',
            'path': path, 'sha256': digest,
            'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'content': content, 'sections': _sections(content)}


def source_items(source):
    return source['items'] if source.get('schema_version') == 2 else source['sections']


def validate_source(root, source, *, current=True, reviewed=False):
    if type(source) is dict and source.get('schema_version') == 2:
        from cc_product_adoption import validate
        return validate(root, source, current=current, reviewed=reviewed)
    if type(source) is not dict or set(source) != SOURCE_FIELDS or source['schema_version'] != 1:
        raise DevelopmentError('invalid_source_observation')
    if source['kind'] not in ('file', 'user_text') or source['owner'] != ('project' if source['kind'] == 'file' else 'user'):
        raise DevelopmentError('invalid_source_owner')
    if any(type(source[k]) is not str for k in ('path', 'content', 'sha256', 'source_id', 'observed_at')):
        raise DevelopmentError('invalid_source_observation')
    if (len(source['content'].encode('utf-8')) > LIMIT or not source['content'].strip()
            or not re.fullmatch(r'[a-f0-9]{64}', source['sha256'])
            or source['source_id'] != 'src-' + source['sha256'][:16]):
        raise DevelopmentError('invalid_source_observation')
    try:
        datetime.datetime.fromisoformat(source['observed_at'])
    except ValueError:
        raise DevelopmentError('invalid_source_observation') from None
    if source['kind'] == 'user_text':
        if source['path'] or _hash(source['content'].encode('utf-8')) != source['sha256']:
            raise DevelopmentError('invalid_source_digest')
    elif not source['path'] or not source['path'].lower().endswith(('.md', '.markdown', '.txt')):
        raise DevelopmentError('invalid_source_path')
    else:
        from cc_evidence_inputs import parts
        parts(source['path'])
    expected = _sections(source['content'])
    if type(source_items(source)) is not list or len(source_items(source)) != len(expected):
        raise DevelopmentError('invalid_source_inventory')
    for actual, original in zip(source_items(source), expected):
        if type(actual) is not dict or set(actual) != SECTION_FIELDS:
            raise DevelopmentError('invalid_source_inventory')
        if any(actual[k] != original[k] for k in ('id', 'title', 'start_line', 'end_line', 'text')):
            raise DevelopmentError('invalid_source_inventory')
        if (type(actual['selected']) is not bool or actual['disposition'] not in ('unresolved', 'mapped', 'reference-only', 'excluded')
                or type(actual['reason']) is not str or len(actual['reason']) > 4000
                or type(actual['targets']) is not list or len(actual['targets']) > 128
                or any(type(t) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', t) for t in actual['targets'])):
            raise DevelopmentError('invalid_source_disposition')
        if actual['selected'] and (actual['disposition'] == 'mapped' and not actual['targets'] or actual['disposition'] == 'excluded' and not actual['reason'].strip()):
            raise DevelopmentError('invalid_source_disposition')
        if reviewed and actual['selected'] and actual['disposition'] == 'unresolved':
            raise DevelopmentError('selected_source_unresolved')
    if current and source['kind'] == 'file':
        observed = observe_source(root, source['path'])
        if observed['sha256'] != source['sha256'] or observed['content'] != source['content']:
            raise DevelopmentError('source_drift')


def source_coverage(source):
    selected = [s for s in source_items(source) if s['selected']]
    reviewed = [s for s in selected if s['disposition'] != 'unresolved']
    return {'total': len(source_items(source)), 'selected': len(selected), 'reviewed': len(reviewed),
            'selected_complete': len(reviewed) == len(selected),
            'whole_complete': len(reviewed) == len(source_items(source))}


def export_destination(root, ownership='managed', path=''):
    if ownership not in ('managed', 'project_managed'):
        raise DevelopmentError('invalid_document_ownership')
    if ownership == 'project_managed' and not path:
        raise DevelopmentError('export_destination_required')
    from cc_layout import managed_relative
    destination = path or managed_relative(root, 'dev/product/product.md')
    from cc_evidence_inputs import parts
    parts(destination)
    if not destination.lower().endswith('.md') or any(p.lower() in ('.git', '.controlcoding', '.controlwork', '_work') for p in destination.split('/')):
        raise DevelopmentError('unsafe_export_path')
    return destination


def render_document(document, revision, *, ownership='managed', status='draft'):
    metadata = {'format': 1, 'revision': revision, 'owner': ownership, 'status': status, 'base': document}
    header = json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e')
    pieces = ['<!-- cc-product-export ' + header + ' -->\n']
    def block(kind, identifier, value):
        pieces.append(f'<!-- cc-product:{kind}:{identifier}:begin -->\n{value.rstrip()}\n<!-- cc-product:{kind}:{identifier}:end -->\n')
    block('document', document['id'], '# ' + document['title'])
    for key, title in (('goals', 'Goals'), ('users', 'Users'), ('constraints', 'Constraints'), ('non_goals', 'Out of scope'), ('questions', 'Open questions')):
        block('field', key, '## ' + title + '\n' + '\n'.join('- ' + v for v in document[key]))
    for chapter in document['chapters']:
        block('chapter', chapter['id'], '## ' + chapter['title'] + '\n' + chapter['content'])
    for feature in document['features']:
        block('feature', feature['id'], '### ' + feature['title'])
        for name in ('problem', 'outcome', 'users', 'priority', 'dependencies', 'risks', 'questions', 'source_references'):
            value = feature[name]
            if name == 'dependencies':
                value = '\n'.join('- JSON: ' + json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=False) for v in value)
            block('featurefield', feature['id'] + '--' + name, '#### ' + name.replace('_', ' ').title() + '\n' + ('\n'.join('- ' + v for v in value) if type(value) is list else value))
        for criterion in feature['criteria']:
            block('criterion', criterion['id'], '- ' + criterion['text'])
    block('notes', document['id'], document['notes'])
    return '\n'.join(pieces)


def _read_file(root, path):
    try:
        with SafeRoot(root) as safe:
            item = safe.read(path, Budget(per_file=512 * 1024, total=512 * 1024), missing=True)
        return None if item is None else item[0]
    except (EvidenceError, OSError) as error:
        raise DevelopmentError(getattr(error, 'code', 'export_unreadable')) from None


def export_preview(root, document, revision, *, ownership='managed', path='', status='draft'):
    destination = export_destination(root, ownership, path)
    raw = _read_file(root, destination)
    content = render_document(document, revision, ownership=ownership, status=status)
    value = {'path': destination, 'ownership': ownership, 'revision': revision,
             'expected_hash': 'absent' if raw is None else _hash(raw), 'content': content,
             'replacement_required': raw is not None}
    value['preview_id'] = _hash(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())
    return value


def observe_files(root, paths):
    if type(paths) is not list or not 1 <= len(paths) <= 64 or len(set(paths)) != len(paths):
        raise DevelopmentError('invalid_plan_files')
    from cc_development_lib.schema import source_path
    result = []
    with SafeRoot(root) as safe:
        budget = Budget(per_file=4 * 1024 * 1024, total=32 * 1024 * 1024)
        for path in paths:
            source_path(path)
            item = safe.read(path, budget, missing=True)
            result.append({'path': path, 'kind': 'new' if item is None else 'existing',
                           'sha256': 'absent' if item is None else _hash(item[0])})
    return result


def export_commit(root, document, revision, *, ownership, path, expected_hash, preview_id, replacement=False, status='draft'):
    path = export_destination(root, ownership, path)
    from cc_development_lib.storage import transaction
    from cc_development_gateway import _write_bytes
    from cc_layout import managed_relative
    with SafeRoot(root) as safe:
        with transaction(safe, operation_id='product_export_' + preview_id):
            from cc_development_lib.storage import load
            from cc_product import view
            state, _ = load(safe, locked=True)
            product = view(state)
            if product['current_revision'] != revision or product['document'] != document:
                raise DevelopmentError('export_canonical_revision_stale')
            expected_status = 'accepted' if product['accepted_revision'] == revision else 'draft'
            if status != expected_status:
                raise DevelopmentError('export_status_stale')
            preview = export_preview(root, document, revision, ownership=ownership, path=path, status=expected_status)
            return _publish_export(safe, preview, expected_hash, preview_id, replacement)


def _publish_export(safe, preview, expected_hash, preview_id, replacement):
    from cc_development_gateway import _write_bytes
    from cc_layout import managed_relative
    path = preview['path']; revision = preview['revision']
    receipt_path = managed_relative(safe.path, '.controlcoding/development_exports/' + preview_id + '.json')
    prior = safe.read(receipt_path, Budget(per_file=8192, total=8192), missing=True)
    content = preview['content'].encode('utf-8')
    if prior is not None:
        old = json.loads(prior[0]); actual = _read_file(safe.path, path)
        if (old.get('operation_id') == preview_id and old.get('revision') == revision
                and old.get('expected_hash') == expected_hash and old.get('path') == path
                and old.get('sha256') == _hash(content) and actual is not None
                and _hash(actual) == old['sha256']):
            old['state'] = 'exported'; old['idempotent'] = True
            safe.write_atomic(receipt_path, old); return old
    if preview['preview_id'] != preview_id or preview['expected_hash'] != expected_hash:
        raise DevelopmentError('export_preview_stale')
    if expected_hash != 'absent' and not replacement:
        raise DevelopmentError('export_replacement_review_required')
    receipt = {'operation_id': preview_id, 'revision': revision, 'path': path,
               'expected_hash': expected_hash, 'sha256': _hash(content), 'state': 'pending'}
    safe.write_atomic(receipt_path, receipt)
    try:
        _write_bytes(safe, {'path': path, 'content': content,
                           'before_sha256': None if expected_hash == 'absent' else expected_hash,
                           'after_sha256': receipt['sha256']})
        receipt['state'] = 'exported'
    except (OSError, ValueError) as error:
        receipt['state'] = 'needs_attention'; receipt['reason'] = getattr(error, 'code', 'export_write_failed')
        safe.write_atomic(receipt_path, receipt); return receipt
    safe.write_atomic(receipt_path, receipt); return receipt


def _change_export_preview(root, state, control_revision, change_id, stage, ownership, path):
    from cc_development_lib.schema import identifier
    from cc_development_lib import snapshot
    from cc_development_lib.evaluator import latest
    from cc_layout import managed_relative
    identifier(change_id)
    if stage not in ('intent', 'design', 'plan'):
        raise DevelopmentError('unsupported_change_export_stage')
    if stage in ('design','plan') and __import__('cc_product_planning').profile(state.get('product',{}),change_id):
        detailed=_stage_preview(root,state,change_id,stage,ownership,path)
        current_view=snapshot(root,state,control_revision,locked=True)
        current_change=next(c for c in current_view['changes'] if c['id']==change_id)
        pending=next((d for d in current_view['product']['drafts'] if d['change_id']==change_id and d['stage']==stage),None)
        gate=current_change['gates'][stage]
        status='draft' if pending and not pending['published'] else 'accepted_current' if gate['accepted'] and gate['outcome']=='allowed' else 'accepted_blocked' if gate['accepted'] else 'draft'
        result={'path':detailed['path'],'ownership':ownership,'revision':detailed['base_revision'],'control_revision':control_revision,'change_id':change_id,'stage':stage,'status':status,'expected_hash':detailed['existing_sha256'],'content':detailed['content'],'replacement_required':detailed['existing_sha256']!='absent'}
        result['preview_id']=_hash(json.dumps(result,sort_keys=True,ensure_ascii=False).encode())
        return result
    snapshot_value = snapshot(root, state, control_revision, locked=True)
    change = next((c for c in snapshot_value['changes'] if c['id'] == change_id), None)
    if change is None: raise DevelopmentError('change_not_found')
    artifact = latest(change, stage)
    pending = next((d for d in snapshot_value.get('product', {}).get('drafts', []) if d['change_id'] == change_id and d['stage'] == stage), None)
    if pending and not pending['published']:
        artifact = {'revision': pending['artifact_revision'], 'content': pending['content']}
    if artifact is None: raise DevelopmentError('artifact_missing')
    filename = {'intent': 'intent', 'design': 'spec', 'plan': 'plan'}[stage]
    if ownership == 'managed' and not path:
        path = managed_relative(root.path, 'dev/changes/' + change_id + '/' + filename + '.md')
    path = export_destination(root.path, ownership, path)
    gate = change['gates'][stage]
    status = 'draft' if pending and not pending['published'] else 'accepted_current' if gate['accepted'] and gate['outcome'] == 'allowed' else 'accepted_blocked' if gate['accepted'] else 'draft'
    title = {'intent': 'Request', 'design': 'Specification', 'plan': 'Implementation plan'}[stage]
    lines = ['# ' + title + ': ' + change['title'], '', 'Owner: ' + ownership,
             'Canonical revision: ' + artifact['revision'], 'Status: ' + status,
             'This file is a readable projection. Editing it does not approve work.', '']
    if gate['reasons']: lines += ['Current gate reasons:'] + ['- ' + reason for reason in gate['reasons']] + ['']
    def value_lines(value, depth=2):
        if type(value) is dict:
            result = []
            for key, value in value.items():
                result += ['#' * min(depth, 6) + ' ' + key.replace('_', ' ').title(), ''] + value_lines(value, depth + 1) + ['']
            return result
        if type(value) is list:
            result = []
            for row in value:
                result += value_lines(row, depth) if type(row) in (dict, list) else ['- ' + str(row)]
            return result or ['None declared.']
        return [str(value)]
    content = '\n'.join(lines + value_lines(artifact['content'])) + '\n'
    raw = _read_file(root.path, path)
    preview = {'path': path, 'ownership': ownership, 'revision': artifact['revision'],
               'control_revision': control_revision, 'change_id': change_id, 'stage': stage, 'status': status,
               'expected_hash': 'absent' if raw is None else _hash(raw), 'content': content,
               'replacement_required': raw is not None}
    preview['preview_id'] = _hash(json.dumps(preview, sort_keys=True, ensure_ascii=False).encode())
    return preview


def change_export_preview(root, change_id, stage, *, ownership='managed', path=''):
    from cc_development_lib.storage import load
    with SafeRoot(root) as safe:
        state, revision = load(safe)
        return _change_export_preview(safe, state, revision, change_id, stage, ownership, path)


def change_export_commit(root, change_id, stage, *, ownership, path, expected_hash, preview_id, replacement=False):
    from cc_development_lib.storage import load, transaction
    with SafeRoot(root) as safe:
        with transaction(safe, operation_id='change_export_' + preview_id):
            state, revision = load(safe, locked=True)
            preview = _change_export_preview(safe, state, revision, change_id, stage, ownership, path)
            return _publish_export(safe, preview, expected_hash, preview_id, replacement)



def roundtrip_preview(root, path, current, current_revision, *, resolutions=None):
    raw = _read_file(root, path)
    if raw is None:
        raise DevelopmentError('export_missing')
    try:
        text = raw.decode('utf-8')
        header = re.match(r'<!-- cc-product-export (.*?) -->\r?\n', text)
        from cc_development_lib.storage import pairs
        metadata = json.loads(header[1], object_pairs_hook=pairs) if header else None
        if type(metadata) is not dict or set(metadata) != {'format', 'revision', 'owner', 'status', 'base'} or metadata['format'] != 1:
            raise ValueError()
        from cc_development_lib.storage import load
        with SafeRoot(root) as safe:
            state, _ = load(safe)
        from cc_product import view
        actual = view(state)
        if actual['current_revision'] != current_revision or actual['document'] != current:
            raise DevelopmentError('roundtrip_current_revision_stale')
        retained = next((r for r in state.get('product', {}).get('revisions', []) if r['revision'] == metadata['revision']), None)
        if retained is None or metadata['base'] != retained['document']:
            raise DevelopmentError('roundtrip_base_not_retained')
        base = retained['document']
        if base['id'] != current['id']:
            raise ValueError()
    except DevelopmentError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise DevelopmentError('not_versioned_product_export') from None
    proposal = copy.deepcopy(current)
    conflicts = []
    removed = []
    found = {}
    pattern = re.compile(r'<!-- cc-product:([a-z]+):([A-Za-z0-9_.-]+):begin -->\r?\n(.*?)\r?\n<!-- cc-product:\1:\2:end -->', re.S)
    matches = list(pattern.finditer(text, header.end()))
    for match in matches:
        key = (match[1], match[2])
        if key in found:
            conflicts.append({'id': 'duplicate:' + ':'.join(key), 'kind': 'duplicate_marker'})
        found[key] = match[3]
    notes = pattern.sub('', text[header.end():]).strip()
    if '<!-- cc-product:' in notes:
        conflicts.append({'id': 'malformed-markers', 'kind': 'missing_or_malformed_marker'})
    base_blocks = {(m[1], m[2]): m[3] for m in pattern.finditer(render_document(base, metadata['revision']))}
    current_blocks = {(m[1], m[2]): m[3] for m in pattern.finditer(render_document(current, current_revision))}
    for key, old in base_blocks.items():
        token = ':'.join(key)
        if key not in found:
            removed.append(token)
            conflicts.append({'id': 'remove:' + token, 'kind': 'removal'})
            continue
        changed = found[key]
        if changed == old:
            continue
        if current_blocks.get(key) != old and current_blocks.get(key) != changed:
            conflicts.append({'id': 'competing:' + token, 'kind': 'competing_edit'})
        kind, identifier = key
        if kind == 'document':
            proposal['title'] = re.sub(r'^#\s+', '', changed).strip()
        elif kind == 'field':
            proposal[identifier] = [re.sub(r'^-\s*', '', line).strip() for line in changed.splitlines()[1:] if line.strip()]
        elif kind == 'chapter':
            chapter = next((c for c in proposal['chapters'] if c['id'] == identifier), None)
            if chapter:
                lines = changed.splitlines(); chapter['title'] = re.sub(r'^##\s+', '', lines[0]); chapter['content'] = '\n'.join(lines[1:]).strip()
        elif kind == 'feature':
            feature = next((f for f in proposal['features'] if f['id'] == identifier), None)
            if feature:
                feature['title'] = re.sub(r'^###\s+', '', changed).strip()
        elif kind == 'featurefield':
            feature_id, name = identifier.rsplit('--', 1)
            feature = next((f for f in proposal['features'] if f['id'] == feature_id), None)
            if feature:
                body = '\n'.join(changed.splitlines()[1:]).strip()
                if name == 'dependencies':
                    try:
                        rows = [line.strip() for line in body.splitlines() if line.strip()]
                        if any(not line.startswith('- JSON: ') for line in rows):
                            raise ValueError()
                        feature[name] = [json.loads(line[8:], parse_constant=lambda _: (_ for _ in ()).throw(ValueError())) for line in rows]
                    except (ValueError, TypeError):
                        raise DevelopmentError('invalid_product_dependencies') from None
                else:
                    feature[name] = [re.sub(r'^-\s*', '', line).strip() for line in body.splitlines() if line.strip()] if type(feature[name]) is list else body
        elif kind == 'criterion':
            for feature in proposal['features']:
                for criterion in feature['criteria']:
                    if criterion['id'] == identifier:
                        criterion['text'] = re.sub(r'^-\s*', '', changed).strip()
        elif kind == 'notes':
            proposal['notes'] = changed
    for kind, collection in (('chapter', 'chapters'), ('feature', 'features')):
        order = [m[2] for m in matches if m[1] == kind]
        proposal[collection].sort(key=lambda row: order.index(row['id']) if row['id'] in order else len(order))
    criterion_order = [m[2] for m in matches if m[1] == 'criterion']
    for feature in proposal['features']:
        feature['criteria'].sort(key=lambda row: criterion_order.index(row['id']) if row['id'] in criterion_order else len(criterion_order))
    for key in found.keys() - base_blocks.keys():
        conflicts.append({'id': 'unknown:' + ':'.join(key), 'kind': 'unknown_marker'})
        notes += '\n\n' + found[key]
    if notes:
        proposal['notes'] = (proposal['notes'] + '\n\n' + notes).strip()
    if resolutions is not None:
        if type(resolutions) is not dict or set(resolutions) != {c['id'] for c in conflicts} or any(v not in ('keep_core', 'adopt_file') for v in resolutions.values()):
            raise DevelopmentError('invalid_roundtrip_resolution')
        for conflict in conflicts:
            if conflict['kind'] in ('duplicate_marker', 'missing_or_malformed_marker', 'unknown_marker'):
                raise DevelopmentError('roundtrip_markers_need_repair')
            token = conflict['id'].split(':', 1)[1]
            kind, identifier = token.split(':', 1)
            if conflict['kind'] == 'removal' and resolutions[conflict['id']] == 'adopt_file':
                if kind == 'feature':
                    proposal['features'] = [f for f in proposal['features'] if f['id'] != identifier]
                    for chapter in proposal['chapters']:
                        chapter['feature_ids'] = [f for f in chapter['feature_ids'] if f != identifier]
                elif kind == 'chapter':
                    proposal['chapters'] = [c for c in proposal['chapters'] if c['id'] != identifier]
                elif kind == 'criterion':
                    for feature in proposal['features']:
                        feature['criteria'] = [c for c in feature['criteria'] if c['id'] != identifier]
                elif kind == 'featurefield' and identifier.rsplit('--', 1)[0] not in {f['id'] for f in proposal['features']}:
                    pass
                else:
                    raise DevelopmentError('required_block_removal')
            if conflict['kind'] == 'competing_edit' and resolutions[conflict['id']] == 'keep_core':
                if kind == 'document': proposal['title'] = current['title']
                elif kind == 'field': proposal[identifier] = copy.deepcopy(current[identifier])
                elif kind == 'notes': proposal['notes'] = current['notes']
                elif kind == 'featurefield':
                    feature_id, name = identifier.rsplit('--', 1)
                    original = next(f for f in current['features'] if f['id'] == feature_id)
                    target = next(f for f in proposal['features'] if f['id'] == feature_id)
                    target[name] = copy.deepcopy(original[name])
                else:
                    collection = 'chapters' if kind == 'chapter' else 'features'
                    if kind == 'criterion':
                        for feature in proposal['features']:
                            for criterion in feature['criteria']:
                                if criterion['id'] == identifier:
                                    original = next(c for f in current['features'] for c in f['criteria'] if c['id'] == identifier); criterion.update(copy.deepcopy(original))
                    else:
                        original = next(v for v in current[collection] if v['id'] == identifier)
                        item = next(v for v in proposal[collection] if v['id'] == identifier); item.update(copy.deepcopy(original))
    from cc_development_lib.method import digest
    return {'path': path, 'file_hash': _hash(raw), 'base_revision': metadata['revision'],
            'current_revision': current_revision, 'document': proposal, 'retained_notes': notes,
            'conflicts': conflicts, 'removals': removed, 'resolved': not conflicts or resolutions is not None,
            'source_review': {'path': path, 'sha256': _hash(raw), 'base_revision': current_revision,
                              'proposal_digest': digest(proposal)}}


def _stage_blocks(value,path=()):
    result=[]
    if type(value) is dict:
        for key,child in value.items():result += _stage_blocks(child,path+(key,))
    elif type(value) is list:
        if not value:result.append((path,'[]',False))
        for index,child in enumerate(value):
            token='@'+child['id'] if type(child) is dict and type(child.get('id')) is str else '@'+child['task_id'] if type(child) is dict and type(child.get('task_id')) is str else str(index)
            result += _stage_blocks(child,path+(token,))
    else:result.append((path,value if type(value) is str else json.dumps(value),type(value) is str))
    return result

def _stage_title(pointer):
    labels={'content':'Reviewed artifact','details':'Planning detail','requirements':'Requirements','criteria':'Conditions','task_details':'Task instructions','requirement_details':'Requirement approach','condition_mappings':'Condition interpretation','source_refs':'Source provenance'}
    return ' / '.join(labels.get(part,part.replace('_',' ').capitalize()) for part in pointer if not part.startswith('@') and not part.isdigit())

def _stage_render(marker,status):
    lines=['<!-- CC-PLANNING-STAGE-V1 '+json.dumps(marker,sort_keys=True,ensure_ascii=True,separators=(',',':'))+' -->',
           '# '+('Specification' if marker['stage']=='design' else 'Implementation plan')+': '+marker['change_id'],'',
           'Status: '+status,'Editing this projection creates draft proposals; it does not approve work.','']
    for pointer,value,_ in _stage_blocks({k:marker[k] for k in ('content','details','mappings','files')}):
        token=hashlib.sha256(json.dumps(pointer).encode()).hexdigest()[:24]
        lines += ['<!-- cc-planning-field:'+token+':begin -->','## '+_stage_title(pointer),value,'<!-- cc-planning-field:'+token+':end -->','']
    text='\n'.join(lines)+'\n'
    if len(text.encode('utf-8'))>128*1024:raise DevelopmentError('stage_export_size_limit')
    return text

def _stage_preview(safe,state,change_id,stage,mode,path):
    from cc_product_planning import preview
    from cc_development_lib import change_for
    from cc_development_lib.evaluator import evaluate
    from cc_layout import managed_relative
    if mode not in ('managed','project_managed'):raise DevelopmentError('invalid_export_ownership')
    planning=preview(safe,state,change_id,stage)
    if mode=='managed' and not path:path=managed_relative(safe.path,'dev/changes/'+change_id+'/'+('spec' if stage=='design' else 'plan')+'.md')
    path=export_destination(safe.path,mode,path)
    from cc_development_lib.method import digest
    marker={'change_id':change_id,'stage':stage,'artifact_revision':digest(planning['content']),'detail_revision':planning['detail_revision'],'profile_revision':planning['profile']['revision'] if planning['profile'] else 'absent','selection_revision':planning['selection_revision'],'content':planning['content'],'details':planning['details'],'mappings':planning['mappings'],'files':planning['files']}
    gate=evaluate(safe,copy.deepcopy(change_for(state,change_id)),through_stage=stage,product=state['product'],state=state)[stage]
    status='accepted_current' if gate['accepted'] and gate['outcome']=='allowed' else 'accepted_blocked' if gate['accepted'] else 'draft'
    content=_stage_render(marker,status);raw=_read_file(safe.path,path)
    return {'schema_version':1,'change_id':change_id,'stage':stage,'path':path,'mode':mode,'base_revision':marker['artifact_revision'],'detail_revision':marker['detail_revision'],'profile_revision':marker['profile_revision'],'selection_revision':marker['selection_revision'],'sha256':_hash(content.encode()),'existing_sha256':'absent' if raw is None else _hash(raw),'content':content}

def stage_export_preview(root,change_id,stage,mode='managed',path=''):
    from cc_development_lib.storage import load
    with SafeRoot(root) as safe:
        state,_=load(safe);return _stage_preview(safe,state,change_id,stage,mode,path)

def stage_export_commit(root,change_id,stage,mode,path,preview):
    from cc_development_lib.storage import load,transaction
    from cc_development_lib.method import digest
    with SafeRoot(root) as safe:
        with transaction(safe,operation_id='stage_export_'+digest(preview)):
            state,_=load(safe,locked=True);current=_stage_preview(safe,state,change_id,stage,mode,path)
            if type(preview) is not dict or set(preview)!=set(current) or any(preview[k]!=current[k] for k in current if k!='existing_sha256'):raise DevelopmentError('stage_export_preview_stale')
            ordinary={'path':path,'revision':current['base_revision'],'content':current['content'],'expected_hash':current['existing_sha256'],'preview_id':digest(preview)}
            return _publish_export(safe,ordinary,preview['existing_sha256'],digest(preview),True)

def stage_import_preview(root,change_id,stage,path,*,state=None):
    from cc_development_lib.storage import load,pairs
    from cc_development_lib.method import digest
    from cc_product_planning import preview,validate_action
    with SafeRoot(root) as stage_safe:
        observed=stage_safe.read(path,Budget(per_file=128*1024,total=128*1024),missing=True)
        raw=None if observed is None else observed[0]
    if raw is None:raise DevelopmentError('export_missing')
    try:
        content=raw.decode('utf-8').replace('\r\n','\n');match=re.match(r'<!-- CC-PLANNING-STAGE-V1 (.*?) -->\r?\n',content)
        marker=json.loads(match[1],object_pairs_hook=pairs) if match else None
        if type(marker) is not dict or set(marker)!={'change_id','stage','artifact_revision','detail_revision','profile_revision','selection_revision','content','details','mappings','files'} or marker['change_id']!=change_id or marker['stage']!=stage:raise ValueError()
    except (ValueError,UnicodeError,TypeError):raise DevelopmentError('not_versioned_stage_export') from None
    with SafeRoot(root) as safe:
        if state is None:state,_=load(safe)
        current=preview(safe,state,change_id,stage)
    if marker['artifact_revision']!=digest(current['content']) or marker['detail_revision']!=current['detail_revision'] or marker['profile_revision']!=(current['profile']['revision'] if current['profile'] else 'absent') or marker['selection_revision']!=current['selection_revision'] or any(marker[k]!=current[k] for k in ('content','details','mappings','files')):raise DevelopmentError('stage_import_base_stale')
    pattern=re.compile(r'<!-- cc-planning-field:([a-f0-9]{24}):begin -->\r?\n(.*?)\r?\n<!-- cc-planning-field:\1:end -->',re.S)
    found={};blockers=[];differences=[]
    for item in pattern.finditer(content,match.end()):
        if item[1] in found:blockers.append('stage_duplicate_field_marker')
        found[item[1]]=item[2]
    proposed=copy.deepcopy({k:marker[k] for k in ('content','details','mappings','files')})
    for pointer,value,editable in _stage_blocks(proposed):
        token=hashlib.sha256(json.dumps(pointer).encode()).hexdigest()[:24];expected='## '+_stage_title(pointer)+'\n'+value
        observed=found.pop(token,None)
        if observed is None:
            blockers.append('stage_missing_field_marker');differences.append({'pointer':list(pointer),'base':value,'proposed':'','editable':editable,'disposition':'manual-resolution-required'});continue
        prefix='## '+_stage_title(pointer)+'\n'
        if observed==expected:continue
        parsed_edit=editable and observed.startswith(prefix) and pointer[-1] not in ('id','task_id','requirement_id','criterion_id','product_criterion_id','contract','kind','path','sha256','source_id','observed_at','upstream_intent_revision','upstream_specification_revision')
        differences.append({'pointer':list(pointer),'base':value,'proposed':observed[len(prefix):] if observed.startswith(prefix) else observed,'editable':parsed_edit,'disposition':'parsed' if parsed_edit else 'manual-resolution-required'})
        if not editable or not observed.startswith(prefix) or pointer[-1] in ('id','task_id','requirement_id','criterion_id','product_criterion_id','contract','kind','path','sha256','source_id','observed_at','upstream_intent_revision','upstream_specification_revision'):
            blockers.append('stage_readable_edit_requires_resolution');continue
        target=proposed
        for segment in pointer[:-1]:
            target=next(row for row in target if row.get('id',row.get('task_id'))==segment[1:]) if type(target) is list and segment.startswith('@') else target[int(segment)] if type(target) is list else target[segment]
        leaf=pointer[-1];replacement=observed[len(prefix):]
        if type(target) is list:target[int(leaf)]=replacement
        else:target[leaf]=replacement
    if found:blockers.append('stage_unknown_field_marker')
    notes=pattern.sub('',content[match.end():]).strip()
    expected_preamble='# '+('Specification' if stage=='design' else 'Implementation plan')+': '+change_id
    notes=re.sub(r'^'+re.escape(expected_preamble)+r'\r?\n\s*Status: [^\n]*\r?\nEditing this projection creates draft proposals; it does not approve work\.\s*','',notes).strip()
    unknown_notes=[notes] if notes else []
    if unknown_notes:blockers.append('stage_unknown_notes_review_required')
    action={'kind':'product_specification_v2' if stage=='design' else 'product_plan_v2','change_id':change_id,'content':proposed['content'],'details':proposed['details'],'actor':'document-review',('mappings' if stage=='design' else 'files'):proposed['mappings' if stage=='design' else 'files']}
    review={'path':path,'sha256':_hash(raw),'base_revision':marker['artifact_revision'],'detail_revision':marker['detail_revision'],'proposal_digest':digest(action)}
    try:validate_action(action)
    except DevelopmentError as error:blockers.append(error.code)
    if not blockers:action['source_review']=review
    return {'schema_version':1,'action':action,'review':review,'unknown_notes':unknown_notes,'differences':differences,'blockers':list(dict.fromkeys(blockers))}
