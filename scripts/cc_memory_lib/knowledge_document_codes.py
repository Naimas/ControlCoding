"""Reviewed, source-bound document codes. AI proposes meaning; this store assigns numbers."""
import json
import re
from uuid import uuid4

from .knowledge_store import database, get, put, now, KnowledgeError
from .knowledge_sources import digest

SETTINGS = 'document_codes:settings:v1'
ASSIGN = 'document_codes:assignment:'
PROPOSAL = 'document_codes:proposal:'
NEXT = 'document_codes:next:v1'
DEFAULT_KINDS = {
    'DEV': 'Development plan', 'PROT': 'Prototype plan',
    'DES': 'Design document', 'REQ': 'Requirements or specification',
    'DEC': 'Directive or decision', 'TEST': 'Test plan or result',
    'OPS': 'Release or operations plan', 'LEG': 'Historical or legacy material',
}
CODE = re.compile(r'[A-Z][A-Z0-9]{1,7}\Z')
PHASE = re.compile(r'[A-Z][A-Z0-9]{1,7}\Z')
LIMIT = 24000


def _settings(db):
    saved = get(db, SETTINGS, {'project': '', 'automatic': False, 'kinds': DEFAULT_KINDS})
    if (type(saved) is not dict or set(saved) != {'project', 'automatic', 'kinds'}
            or type(saved['project']) is not str or saved['project'] and not CODE.fullmatch(saved['project'])
            or type(saved['automatic']) is not bool or type(saved['kinds']) is not dict
            or not 1 <= len(saved['kinds']) <= 16
            or any(type(k) is not str or not CODE.fullmatch(k) or k == 'NONE'
                   or type(v) is not str or not 3 <= len(v.strip()) <= 80
                   for k, v in saved['kinds'].items())):
        raise KnowledgeError('invalid_document_code_settings')
    return saved


def _token(value):
    return digest(json.dumps(value, sort_keys=True))


def _classification_token(settings):
    # Scheduling does not change the meaning of a reviewed proposal.
    return _token({'project': settings['project'], 'kinds': settings['kinds']})


def _source(db, identifier):
    row = db.execute('SELECT id,path,title,kind,revision,body FROM sources WHERE id=? AND deleted=0', (identifier,)).fetchone()
    if row is None or row['kind'] in ('conversation', 'session', 'receipt', 'dev-view'):
        raise KnowledgeError('document_code_source_unavailable')
    return dict(row)


def _state(db, row):
    assignment = get(db, ASSIGN + row['id'])
    proposal = get(db, PROPOSAL + row['id'])
    if assignment and (type(assignment) is not dict or not {'id', 'number', 'kind', 'project', 'phase', 'revision', 'path', 'reason', 'updated'} <= set(assignment)):
        raise KnowledgeError('invalid_document_code_state')
    if proposal and (type(proposal) is not dict or not {'id', 'kind', 'phase', 'quote', 'reason', 'revision', 'path', 'settings', 'status'} <= set(proposal)):
        raise KnowledgeError('invalid_document_code_state')
    current = bool(assignment and assignment['revision'] == row['revision'] and assignment['path'] == row['path'])
    proposal_current = bool(proposal and proposal['revision'] == row['revision']
                            and proposal['path'] == row['path'] and proposal['settings'] == _classification_token(_settings(db)))
    pending = bool(proposal_current and proposal['status'] == 'pending')
    return {'assignment': assignment, 'confirmed': current, 'stale': bool(assignment and not current),
            'proposal': dict(proposal, stale=not proposal_current) if proposal else None,
            'pending': pending,
            'token': _token([row['id'], row['path'], row['revision'], assignment, proposal])}


def _display(assignment):
    if not assignment or assignment['kind'] == 'NONE':
        return ''
    return '-'.join((assignment['kind'], assignment['project'], assignment['phase'] or 'GEN',
                     str(assignment['number']).zfill(3)))


def _public(db, row):
    state = _state(db, row)
    from .knowledge_document_lineage import read_for
    assignment = state['assignment']
    if assignment:
        assignment = {key: value for key, value in assignment.items() if key != 'history'}
        assignment = dict(assignment, display=_display(assignment))
    return {**state, 'assignment': assignment, 'lineage': read_for(db, row['id'])}


def _validate_evidence(value, text):
    if (type(value) is not dict or set(value) != {'kind', 'phase', 'quote', 'reason'}
            or type(value['kind']) is not str or type(value['phase']) is not str
            or type(value['quote']) is not str or type(value['reason']) is not str
            or value['kind'] != 'NONE' and not CODE.fullmatch(value['kind'])
            or value['phase'] and not PHASE.fullmatch(value['phase'])
            or value['kind'] == 'NONE' and value['phase'] != ''
            or not 8 <= len(value['quote']) <= 200 or value['quote'] not in text
            or not 5 <= len(value['reason'].strip()) <= 600):
        raise KnowledgeError('invalid_document_code_proposal')


def dispatch(root, action, value):
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        settings = _settings(db)
        settings_token = _token(settings)
        classification_token = _classification_token(settings)
        if action == 'code-settings':
            if value is not None:
                raise KnowledgeError('invalid_document_code_request')
            return {'settings': settings, 'token': settings_token}
        if action == 'code-configure':
            if (type(value) is not dict or set(value) != {'project', 'automatic', 'kinds', 'expected'}
                    or type(value['expected']) is not str or value['expected'] != settings_token):
                raise KnowledgeError('document_code_settings_changed')
            candidate = {k: value[k] for k in ('project', 'automatic', 'kinds')}
            # Validate without mutating the database.
            if (type(candidate['project']) is not str or candidate['project'] and not CODE.fullmatch(candidate['project'])
                    or type(candidate['automatic']) is not bool or not candidate['project'] and candidate['automatic']
                    or type(candidate['kinds']) is not dict or not 1 <= len(candidate['kinds']) <= 16
                    or any(type(k) is not str or not CODE.fullmatch(k) or k == 'NONE' or type(v) is not str
                           or not 3 <= len(v.strip()) <= 80 for k, v in candidate['kinds'].items())):
                raise KnowledgeError('invalid_document_code_settings')
            with db:
                put(db, SETTINGS, candidate)
            return {'settings': candidate, 'token': _token(candidate)}
        if get(db, 'needs_reconcile', True):
            raise KnowledgeError('document_code_refresh_required')
        if action == 'code-view':
            if (type(value) is not dict or set(value) != {'offset', 'query'}
                    or type(value['offset']) is not int or not 0 <= value['offset'] <= 100000
                    or type(value['query']) is not str or len(value['query']) > 200):
                raise KnowledgeError('invalid_document_code_request')
            rows, total, needs, pending = [], 0, 0, 0
            for raw in db.execute("SELECT id,path,title,kind,revision FROM sources WHERE deleted=0 AND kind NOT IN ('conversation','session','receipt','dev-view') ORDER BY path,id"):
                row = dict(raw)
                state = _public(db, row)
                if state['pending']:
                    pending += 1
                elif not state['confirmed']:
                    needs += 1
                if value['query'].casefold() not in (row['title'] + ' ' + row['path']).casefold():
                    continue
                if value['offset'] <= total < value['offset'] + 25:
                    rows.append({**row, 'code': state})
                total += 1
            return {'rows': rows, 'total': total, 'needs_review': needs, 'pending': pending,
                    'offset': value['offset'], 'next_offset': value['offset'] + 25 if value['offset'] + 25 < total else None,
                    'settings': settings, 'settings_token': settings_token}
        if action == 'code-next':
            if value is not None:
                raise KnowledgeError('invalid_document_code_request')
            if not settings['automatic'] or not settings['project']:
                return {'candidate': None, 'reason': 'automatic_off'}
            for raw in db.execute("SELECT id,path,title,kind,revision FROM sources WHERE deleted=0 AND kind NOT IN ('conversation','session','receipt','dev-view') AND length(body)>=8 ORDER BY path,id"):
                row = dict(raw)
                state = _state(db, row)
                if not state['confirmed'] and not state['pending']:
                    proposal = state['proposal']
                    if proposal and not proposal['stale'] and proposal['status'] in ('rejected', 'failed'):
                        continue
                    return {'candidate': {**row, 'token': state['token']}, 'reason': 'ready'}
            return {'candidate': None, 'reason': 'up_to_date'}
        if action not in ('code-prepare', 'code-proposal-save', 'code-decide', 'code-fail') or type(value) is not dict:
            raise KnowledgeError('invalid_document_code_request')
        if type(value.get('source')) is not str or type(value.get('revision')) is not str or type(value.get('expected')) is not str:
            raise KnowledgeError('invalid_document_code_request')
        row = _source(db, value['source'])
        state = _state(db, row)
        if row['revision'] != value['revision'] or state['token'] != value['expected']:
            raise KnowledgeError('document_code_changed')
        if action == 'code-prepare':
            if set(value) != {'source', 'revision', 'expected'}:
                raise KnowledgeError('invalid_document_code_request')
            if len(row['body']) < 8:
                raise KnowledgeError('document_code_text_unavailable')
            from .knowledge_tags import registry, registry_token
            tagset = registry(db)
            vocabulary = sorted(tagset['tags'].values(), key=str.casefold)
            body_folded = row['body'][:LIMIT].casefold()
            selected = [name for name in vocabulary if name.casefold() in body_folded][:50]
            selected += [name for name in vocabulary if name not in selected][:max(0, 100 - len(selected))]
            return {'source': {k: row[k] for k in ('id', 'path', 'title', 'revision')},
                    'text': row['body'][:LIMIT], 'truncated': len(row['body']) > LIMIT,
                    'settings': settings, 'settings_token': classification_token, 'code': _public(db, row),
                    'tag_registry': registry_token(tagset), 'tag_names': selected}
        if action == 'code-proposal-save':
            if (set(value) != {'source', 'revision', 'expected', 'settings_token', 'kind', 'phase', 'quote', 'reason', 'model', 'provider', 'read_chars', 'truncated'}
                    or value['settings_token'] != classification_token or type(value['model']) is not str
                    or not 1 <= len(value['model']) <= 160 or value['provider'] not in ('ollama', 'openai', 'manual')
                    or type(value['read_chars']) is not int or not 1 <= value['read_chars'] <= LIMIT
                    or type(value['truncated']) is not bool or value['truncated'] != (len(row['body']) > value['read_chars'])):
                raise KnowledgeError('document_code_changed')
            evidence = {k: value[k] for k in ('kind', 'phase', 'quote', 'reason')}
            _validate_evidence(evidence, row['body'][:value['read_chars']])
            if evidence['kind'] != 'NONE' and evidence['kind'] not in settings['kinds']:
                raise KnowledgeError('unknown_document_code_kind')
            if not settings['project']:
                raise KnowledgeError('document_code_project_required')
            record = {**evidence, 'id': uuid4().hex, 'revision': row['revision'], 'path': row['path'],
                      'settings': classification_token, 'status': 'pending', 'model': value['model'],
                      'provider': value['provider'], 'read_chars': value['read_chars'],
                      'truncated': value['truncated'], 'updated': now()}
            with db:
                put(db, PROPOSAL + row['id'], record)
            return {'saved': True, 'source': row['id'], 'code': _public(db, row)}
        if action == 'code-fail':
            if (set(value) != {'source', 'revision', 'expected', 'settings_token', 'error'}
                    or value['settings_token'] != classification_token or type(value['error']) is not str
                    or not re.fullmatch('[a-z0-9_]{3,80}', value['error'])):
                raise KnowledgeError('invalid_document_code_request')
            record = {'id': uuid4().hex, 'kind': 'NONE', 'phase': '', 'quote': '',
                      'reason': value['error'], 'revision': row['revision'], 'path': row['path'],
                      'settings': classification_token, 'status': 'failed', 'model': '',
                      'provider': '', 'truncated': False, 'updated': now()}
            with db:
                put(db, PROPOSAL + row['id'], record)
            return {'saved': True, 'source': row['id'], 'code': _public(db, row)}
        if (set(value) != {'source', 'revision', 'expected', 'proposal', 'decision', 'kind', 'phase', 'reason'}
                or value['decision'] not in ('accept', 'reject') or type(value['proposal']) is not str
                or type(value['kind']) is not str or type(value['phase']) is not str
                or type(value['reason']) is not str or not 5 <= len(value['reason'].strip()) <= 600):
            raise KnowledgeError('invalid_document_code_request')
        proposal = state['proposal']
        if not state['pending'] or proposal['id'] != value['proposal']:
            raise KnowledgeError('document_code_proposal_changed')
        if value['decision'] == 'accept' and (value['kind'] != 'NONE' and value['kind'] not in settings['kinds']
                                               or value['kind'] == 'NONE' and value['phase'] != ''
                                               or value['phase'] and not PHASE.fullmatch(value['phase'])):
            raise KnowledgeError('invalid_document_code_request')
        with db:
            updated = {**proposal, 'status': 'accepted' if value['decision'] == 'accept' else 'rejected',
                       'updated': now(), 'review_reason': value['reason'].strip()}
            put(db, PROPOSAL + row['id'], updated)
            if value['decision'] == 'accept':
                old = state['assignment']
                if old:
                    identifier, number, project = old['id'], old['number'], old['project']
                else:
                    identifier, number, project = uuid4().hex, 0, settings['project']
                if value['kind'] != 'NONE' and number == 0:
                    number = get(db, NEXT, 1)
                    if type(number) is not int or not 1 <= number <= 999999:
                        raise KnowledgeError('document_code_limit')
                    put(db, NEXT, number + 1)
                history = (old.get('history', []) if old else [])[-19:]
                if old:
                    history.append({k: old[k] for k in ('kind', 'phase', 'revision', 'updated')})
                put(db, ASSIGN + row['id'], {'id': identifier, 'number': number, 'project': project,
                    'kind': value['kind'], 'phase': value['phase'], 'revision': row['revision'],
                    'path': row['path'], 'reason': value['reason'].strip(), 'updated': now(),
                    'evidence': proposal['quote'], 'origin': proposal['provider'], 'history': history})
            put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
        return {'saved': True, 'source': row['id'], 'code': _public(db, row)}


def forget(db, source):
    db.execute('DELETE FROM meta WHERE key IN (?,?)', (ASSIGN + source, PROPOSAL + source))
