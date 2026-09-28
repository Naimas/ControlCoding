"""Durable, opt-in analysis transport boundary for consolidation.

The provider runs outside this module and outside the archive writer lock. This
module freezes requests, reserves sends, and imports only reviewable candidates.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .knowledge_store import KnowledgeError, database, get, put, now
from .knowledge_consolidation import (binding, current, digest, encoded, identifier,
                                      job_row, propose, require, shape, view)

MODES = ('local', 'api', 'manual')
OUTCOMES = ('proposals', 'no_change', 'insufficient_evidence')
EPISTEMIC = ('observed', 'decided', 'planned', 'inferred', 'disputed', 'unknown')
TRIGGERS = ('source', 'conversation', 'commit', 'daily')
CONFIG_KEYS = {'provider', 'model', 'generation', 'maxContextChars',
               'maxOutputTokens', 'timeoutSeconds', 'maxRequests', 'roleRevision'}
MAX_PACKET = 60 * 1024
MAX_OUTPUT = 64 * 1024
SETTINGS_KEY = 'consolidation_settings'
CURSOR_KEY = 'consolidation_cursor'


def _json(value):
    try:
        return json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError) as exc:
        raise KnowledgeError('invalid_consolidation_reply') from exc


def _config(value):
    from .knowledge_consolidation_execution_validation import config
    try:
        config(value)
    except (KeyError, TypeError, KnowledgeError) as exc:
        raise KnowledgeError('invalid_consolidation_request') from exc
    return value


def _default_settings():
    return {'enabled': False, 'triggers': [], 'mode': 'manual', 'timezone': 'UTC',
            'daily_hour': 2, 'send_policy': 'none', 'daily_request_cap': 0,
            'prompt': '', 'config': None}


def _settings(value):
    shape(value, 'enabled triggers mode timezone daily_hour send_policy daily_request_cap prompt config')
    require(type(value['enabled']) is bool and type(value['triggers']) is list
            and all(type(t) is str and t in TRIGGERS for t in value['triggers'])
            and len(set(value['triggers'])) == len(value['triggers']))
    require(value['mode'] in MODES and value['send_policy'] in ('none', 'local', 'api'))
    require(type(value['timezone']) is str and 0 < len(value['timezone']) <= 80)
    try:
        ZoneInfo(value['timezone'])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise KnowledgeError('invalid_consolidation_timezone') from exc
    require(type(value['daily_hour']) is int and 0 <= value['daily_hour'] <= 23)
    require(type(value['daily_request_cap']) is int and 0 <= value['daily_request_cap'] <= 6)
    require(type(value['prompt']) is str and len(value['prompt'].encode()) <= 12000)
    require(value['config'] is None or type(value['config']) is dict)
    if value['config'] is not None:
        _config(value['config'])
    if value['enabled']:
        require(value['prompt'].strip() and value['config'] is not None)
    if value['send_policy'] == 'api':
        require(value['mode'] == 'api' and value['daily_request_cap'] > 0)
    if value['send_policy'] == 'local':
        require(value['mode'] == 'local')
    return value


def settings(db, value):
    current_value = get(db, SETTINGS_KEY) or _default_settings()
    if value is None:
        return {'schema': db.execute('PRAGMA user_version').fetchone()[0],
                'migration_required': db.execute('PRAGMA user_version').fetchone()[0] != 3,
                'settings': current_value}
    require(db.execute('PRAGMA user_version').fetchone()[0] == 3, 'consolidation_migration_required')
    checked = _settings(value)
    # Reauthorizing a different prompt or role requires a fresh explicit write.
    put(db, SETTINGS_KEY, checked)
    return {'schema': 3, 'migration_required': False, 'settings': checked}


def _packet(db, job, prompt, config, mode):
    manifest = _json(job['manifest'])
    from .knowledge_consolidation_prior import select, serialized_chars
    selected = manifest['sources'][:8]
    inputs, prior, approved = select(db, selected)
    if not approved and len(selected) > 1 and not manifest['before']:
        for count in range(len(selected) - 1, 0, -1):
            candidate_inputs, candidate_prior, candidate_approved = select(db, selected[:count])
            if candidate_approved:
                selected = selected[:count]
                inputs, prior, approved = candidate_inputs, candidate_prior, candidate_approved
                break
    while serialized_chars(inputs + prior) > 6000 and len(selected) > 1 and not manifest['before']:
        selected = selected[:-1]
        inputs, prior, approved = select(db, selected)
    require(serialized_chars(inputs + prior) <= 6000, 'consolidation_packet_limit')
    if selected != manifest['sources'] or prior:
        manifest['sources'] = selected
        manifest['count'] = len(selected)
        manifest['deferred'] = manifest['total'] - len(selected)
        manifest['prior_sources'] = [{k: v for k, v in item.items() if k != 'evidence_id'} for item in prior]
        require(len(encoded(manifest)) <= 48000, 'consolidation_source_budget')
        db.execute('UPDATE consolidation_jobs SET manifest=?,updated=? WHERE id=?',
                   (encoded(manifest), now(), job['id']))
        db.execute('DELETE FROM consolidation_inputs WHERE job=?', (job['id'],))
        for source in selected + manifest['prior_sources']:
            db.execute('INSERT OR IGNORE INTO consolidation_inputs VALUES(?,?,?)',
                       (job['id'], source['source'], source['revision']))
    request_id = uuid4().hex
    packet = {'job': job['id'], 'request_id': request_id, 'mode': mode,
              'prompt': prompt, 'config': config,
              'manifest_digest': digest({'input_manifest': inputs, 'approved_evidence': prior,
                                         'approved_context': approved}),
              'input_manifest': inputs, 'approved_evidence': prior,
              'approved_context': approved, 'protocol': 1,
              'selection_offset': (get(db, CURSOR_KEY, {}).get('offset', 0)
                                   if get(db, CURSOR_KEY, {}).get('generation') == get(db, 'generation', 0) else 0),
              'existing_proposals': [dict(row) for row in db.execute(
                  'SELECT id,status,target FROM consolidation_proposals WHERE job=? ORDER BY ordinal LIMIT 20',
                  (job['id'],))]}
    require(len(encoded(packet).encode()) <= MAX_PACKET, 'consolidation_packet_limit')
    return packet


def prepare(db, root, value):
    shape(value, 'job prompt config mode')
    require(value['mode'] in MODES and type(value['prompt']) is str
            and 0 < len(value['prompt'].strip()) <= 12000)
    _config(value['config'])
    require(value['mode'] == 'manual' or
            value['config']['provider'] == ('ollama' if value['mode'] == 'local' else 'openai')
            and bool(value['config']['model'].strip()))
    job = job_row(db, value['job'])
    current(db, root, job)
    require(job['state'] in ('ready', 'queued', 'waiting_manual', 'failed', 'interrupted'),
            'consolidation_job_state')
    prior = db.execute('SELECT * FROM consolidation_attempts WHERE job=? ORDER BY ordinal DESC LIMIT 1',
                       (job['id'],)).fetchone()
    if prior:
        packet = _json(prior['packet'])
        same = packet.get('prompt') == value['prompt'] and packet.get('config') == value['config'] and packet.get('mode') == value['mode']
        require(same, 'consolidation_prepare_conflict')
        return packet
    packet = _packet(db, job, value['prompt'], value['config'], value['mode'])
    db.execute('INSERT INTO consolidation_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (packet['request_id'], job['id'], 0, value['mode'], encoded(packet), digest(packet),
                'prepared', None, None, None, None, None, None, None, None))
    db.execute('UPDATE consolidation_jobs SET state=?,updated=? WHERE id=?',
               ('waiting_manual' if value['mode'] == 'manual' else 'ready', now(), job['id']))
    return packet


def _attempt(db, value):
    shape(value, 'job request_id operation owner')
    require(identifier(value['job']) and identifier(value['request_id'])
            and identifier(value['owner']))
    row = db.execute('SELECT * FROM consolidation_attempts WHERE request_id=? AND job=?',
                     (value['request_id'], value['job'])).fetchone()
    require(row is not None, 'consolidation_attempt_unavailable')
    return dict(row)


def _expiry(seconds=90):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def _active_seconds(db, job):
    total = 0.0
    present = datetime.now(timezone.utc)
    for row in db.execute('SELECT started,finished FROM consolidation_attempts WHERE job=? AND started IS NOT NULL',
                          (job,)):
        start = datetime.fromisoformat(row['started'])
        finish = datetime.fromisoformat(row['finished']) if row['finished'] else present
        total += max(0, (finish - start).total_seconds())
    return total


def recover(db):
    """Conservatively recover expired reservations without resending unknown calls."""
    stamp = now()
    for row in db.execute("SELECT request_id,job,state FROM consolidation_attempts WHERE state IN ('reserved','started') AND lease_until<?",
                          (stamp,)).fetchall():
        state = 'prepared' if row['state'] == 'reserved' else 'interrupted'
        db.execute('UPDATE consolidation_attempts SET state=?,owner=NULL,lease_until=NULL,finished=CASE WHEN started IS NOT NULL THEN ? ELSE finished END,error=? WHERE request_id=?',
                   (state, stamp, encoded({'code': 'lease_expired', 'message': 'Send outcome unknown'})
                    if state == 'interrupted' else None, row['request_id']))
        db.execute('UPDATE consolidation_jobs SET state=?,updated=? WHERE id=?',
                   ('ready' if state == 'prepared' else 'interrupted', stamp, row['job']))
        db.execute('UPDATE consolidation_queue SET state=? WHERE job=?',
                   ('queued' if state == 'prepared' else 'interrupted', row['job']))
        if state == 'interrupted':
            _close_episode(db, row['job'])


def attempt(db, root, value):
    row = _attempt(db, value)
    op = value['operation']
    require(op in ('reserve', 'start', 'heartbeat', 'pause', 'resume', 'cancel', 'retry'))
    job = job_row(db, row['job'])
    if op not in ('pause', 'cancel'):
        current(db, root, job)
    if op == 'retry':
        require(row['state'] in ('failed', 'interrupted', 'canceled'), 'consolidation_retry_unavailable')
        ordinal = db.execute('SELECT count(*) FROM consolidation_attempts WHERE job=?', (row['job'],)).fetchone()[0]
        packet = _json(row['packet'])
        require(ordinal < packet['config']['maxRequests'], 'consolidation_request_budget')
        require(_active_seconds(db, row['job']) < 600, 'consolidation_active_deadline')
        queued = db.execute('SELECT dedup FROM consolidation_queue WHERE job=?', (row['job'],)).fetchone()
        if queued:
            prefix = queued['dedup'][:62]
            budget = _budget_row(db, prefix)
            require(budget is not None and budget['used'] < min(6, packet['config']['maxRequests']),
                    'consolidation_episode_budget')
        packet['request_id'] = uuid4().hex
        db.execute('INSERT INTO consolidation_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (packet['request_id'], row['job'], ordinal, row['mode'], encoded(packet), digest(packet),
                    'prepared', None, None, None, None, None, None, None, None))
        db.execute("UPDATE consolidation_jobs SET state='ready',updated=? WHERE id=?", (now(), row['job']))
        db.execute("UPDATE consolidation_queue SET state='queued' WHERE job=?", (row['job'],))
        if queued:
            db.execute('UPDATE consolidation_episode_budget SET used=used+1,closed=0,updated=? WHERE prefix=?',
                       (now(), prefix))
        return {'state': 'prepared', 'request_id': packet['request_id'], 'packet': packet}
    if op == 'reserve':
        require(row['state'] == 'prepared', 'consolidation_attempt_state')
        require(_active_seconds(db, row['job']) < 600, 'consolidation_active_deadline')
        live = db.execute("SELECT 1 FROM consolidation_attempts WHERE state IN ('reserved','started') AND request_id!=?",
                          (row['request_id'],)).fetchone()
        require(live is None, 'consolidation_job_busy')
        queued = db.execute('SELECT dedup FROM consolidation_queue WHERE job=?', (row['job'],)).fetchone()
        if queued:
            policy = get(db, SETTINGS_KEY) or _default_settings()
            packet = _json(row['packet'])
            budget = _budget_row(db, queued['dedup'][:62])
            require(budget is not None and not budget['closed'], 'consolidation_episode_budget')
            require(policy['enabled'] and policy['send_policy'] == row['mode']
                    and policy['prompt'] == packet['prompt'] and policy['config'] == packet['config'],
                    'consolidation_send_policy_changed')
            if row['mode'] == 'api':
                day = datetime.now(timezone.utc).astimezone(ZoneInfo(policy['timezone'])).date().isoformat()
                key = 'consolidation_send_count/' + day
                count = get(db, key, 0)
                require(count < policy['daily_request_cap'], 'consolidation_daily_request_budget')
                put(db, key, count + 1)
        db.execute("UPDATE consolidation_attempts SET state='reserved',owner=?,lease_until=? WHERE request_id=?",
                   (value['owner'], _expiry(), row['request_id']))
        db.execute("UPDATE consolidation_queue SET state='claimed' WHERE job=?", (row['job'],))
    elif op == 'start':
        require(row['state'] == 'reserved' and row['owner'] == value['owner']
                and row['lease_until'] > now(), 'consolidation_lease_required')
        queued = db.execute('SELECT dedup FROM consolidation_queue WHERE job=?', (row['job'],)).fetchone()
        if queued:
            policy = get(db, SETTINGS_KEY) or _default_settings()
            packet = _json(row['packet'])
            require(policy['enabled'] and policy['send_policy'] == row['mode']
                    and policy['prompt'] == packet['prompt'] and policy['config'] == packet['config'],
                    'consolidation_send_policy_changed')
        db.execute("UPDATE consolidation_attempts SET state='started',started=?,finished=NULL,lease_until=? WHERE request_id=?",
                   (now(), _expiry(), row['request_id']))
        db.execute("UPDATE consolidation_jobs SET state='running',updated=? WHERE id=?", (now(), row['job']))
        db.execute("UPDATE consolidation_queue SET state='claimed' WHERE job=?", (row['job'],))
    elif op == 'heartbeat':
        require(row['state'] in ('reserved', 'started') and row['owner'] == value['owner']
                and row['lease_until'] > now(), 'consolidation_lease_required')
        require(_active_seconds(db, row['job']) < 600, 'consolidation_active_deadline')
        db.execute('UPDATE consolidation_attempts SET lease_until=? WHERE request_id=?', (_expiry(), row['request_id']))
    elif op in ('pause', 'cancel'):
        require(row['state'] in ('prepared', 'reserved', 'started'), 'consolidation_attempt_state')
        state = 'paused' if op == 'pause' else 'canceled'
        db.execute('UPDATE consolidation_attempts SET state=?,lease_until=NULL,finished=? WHERE request_id=?',
                   (state, now(), row['request_id']))
        db.execute('UPDATE consolidation_jobs SET state=?,updated=? WHERE id=?', (state, now(), row['job']))
        db.execute('UPDATE consolidation_queue SET state=? WHERE job=?', (state, row['job']))
        if op == 'cancel':
            _close_episode(db, row['job'])
    elif op == 'resume':
        require(row['state'] == 'paused', 'consolidation_attempt_state')
        # A paused request that had started may have reached a provider.
        state = 'interrupted' if row['started'] else 'prepared'
        db.execute('UPDATE consolidation_attempts SET state=?,owner=NULL,lease_until=NULL WHERE request_id=?',
                   (state, row['request_id']))
        db.execute('UPDATE consolidation_jobs SET state=?,updated=? WHERE id=?',
                   (state, now(), row['job']))
        db.execute('UPDATE consolidation_queue SET state=? WHERE job=?',
                   ('queued' if state == 'prepared' else state, row['job']))
    new = db.execute('SELECT state,request_id,lease_until FROM consolidation_attempts WHERE request_id=?',
                     (row['request_id'],)).fetchone()
    return {**dict(new), 'packet': _json(row['packet'])}


def _reply(db, packet, text):
    require(type(text) is str and len(text.encode()) <= MAX_OUTPUT, 'consolidation_output_limit')
    result = _json(text)
    shape(result, 'protocol job request_id manifest_digest outcome proposals used_evidence_ids unresolved_questions coverage')
    require(type(result['protocol']) is int and result['protocol'] == 1 and result['job'] == packet['job']
            and result['request_id'] == packet['request_id']
            and result['manifest_digest'] == packet['manifest_digest'], 'consolidation_reply_mismatch')
    require(result['outcome'] in OUTCOMES and type(result['proposals']) is list
            and len(result['proposals']) <= 5)
    require((result['outcome'] == 'proposals') == bool(result['proposals']))
    evidence = packet['input_manifest'] + packet['approved_evidence']
    ids = {item['evidence_id'] for item in evidence}
    require(type(result['used_evidence_ids']) is list and len(result['used_evidence_ids']) <= 8
            and all(type(item) is str and item in ids for item in result['used_evidence_ids'])
            and len(set(result['used_evidence_ids'])) == len(result['used_evidence_ids']))
    require(type(result['unresolved_questions']) is list and len(result['unresolved_questions']) <= 5
            and all(type(item) is str and len(item) <= 500 for item in result['unresolved_questions']))
    shape(result['coverage'], 'inspected analyzed deferred')
    require(all(type(result['coverage'][k]) is int and result['coverage'][k] >= 0
                for k in ('inspected', 'analyzed', 'deferred')))
    require(result['coverage']['analyzed'] <= len(packet['input_manifest'])
            and result['coverage']['inspected'] <= len(packet['input_manifest'])
            and result['coverage']['analyzed'] <= result['coverage']['inspected']
            and result['coverage']['deferred'] == len(packet['input_manifest']) - result['coverage']['analyzed'])
    analyzed_ids = ({item['evidence_id'] for item in packet['input_manifest'][:result['coverage']['analyzed']]}
                    | {item['evidence_id'] for item in packet['approved_evidence']})
    require(set(result['used_evidence_ids']) <= analyzed_ids, 'invalid_consolidation_coverage')
    anchors = {item['evidence_id']: {k: item[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt')}
               for item in evidence}
    normalized = []
    for item in result['proposals']:
        shape(item, 'page_type key title body kind epistemic_status scope citations conflicting_evidence_ids reason prerequisite_ids')
        require(type(item['body']) is str and 0 < len(item['body']) <= 3000)
        require(item['epistemic_status'] in EPISTEMIC and type(item['scope']) is str
                and 0 < len(item['scope']) <= 160)
        require(type(item['citations']) is list and 0 < len(item['citations']) <= 8)
        require(type(item['conflicting_evidence_ids']) is list
                and all(type(x) is str and x in analyzed_ids for x in item['conflicting_evidence_ids'])
                and len(set(item['conflicting_evidence_ids'])) == len(item['conflicting_evidence_ids']))
        require(type(item['prerequisite_ids']) is list and len(item['prerequisite_ids']) <= 20
                and all(identifier(key) for key in item['prerequisite_ids'])
                and len(set(item['prerequisite_ids'])) == len(item['prerequisite_ids']))
        existing = {p['id']: p['status'] for p in packet['existing_proposals']}
        require(all(existing.get(key) in ('pending', 'accepted') for key in item['prerequisite_ids']),
                'consolidation_prerequisite_unavailable')
        seen_citations = set()
        for citation in item['citations']:
            require(type(citation) is dict and type(citation.get('citation')) is str
                    and citation['citation'] in anchors)
            require(citation['citation'] in analyzed_ids and citation['citation'] not in seen_citations,
                    'invalid_consolidation_coverage')
            seen_citations.add(citation['citation'])
            require(set(citation) == set(anchors[citation['citation']]) | {'citation'})
            require(all(citation[k] == anchors[citation['citation']][k] for k in anchors[citation['citation']]),
                    'consolidation_reply_mismatch')
        require({c['citation'] for c in item['citations']} <= set(result['used_evidence_ids']))
        if item['conflicting_evidence_ids']:
            require(item['epistemic_status'] == 'disputed')
        if item['epistemic_status'] == 'disputed':
            require(item['conflicting_evidence_ids'] and len(item['citations']) >= 2
                    and set(item['conflicting_evidence_ids']) <= {c['citation'] for c in item['citations']})
        require(set(re.findall(r'\[(S\d+)\]', item['body'])) <= {c['citation'] for c in item['citations']})
        require(re.search(r'\[S\d+\]', item['body']) is not None)
        normalized.append(item)
    return result, normalized


def import_reply(db, root, value):
    require(type(value) is dict and {'job', 'request_id', 'text'} <= set(value)
            and set(value) <= {'job', 'request_id', 'text', 'usage'})
    usage = _usage(value.get('usage'))
    require(identifier(value['job']) and identifier(value['request_id']))
    row = db.execute('SELECT * FROM consolidation_attempts WHERE request_id=? AND job=?',
                     (value['request_id'], value['job'])).fetchone()
    require(row is not None, 'consolidation_attempt_unavailable')
    require(row['packet'] != '{}', 'consolidation_attempt_unavailable')
    packet = _json(row['packet'])
    reply, proposals = _reply(db, packet, value['text'])
    response_digest = hashlib.sha256(value['text'].encode()).hexdigest()
    if row['state'] == 'imported':
        require(row['result_digest'] == response_digest, 'consolidation_reply_conflict')
        return {'job': row['job'], 'request_id': row['request_id'],
                'outcome': row['outcome'], 'count': len(proposals), 'replay': True}
    require(row['state'] in ('started', 'reserved', 'prepared'), 'consolidation_attempt_state')
    current(db, root, job_row(db, row['job']))
    if row['state'] == 'started':
        require(row['lease_until'] and row['lease_until'] > now(), 'consolidation_lease_expired')
        require(_active_seconds(db, row['job']) < 600, 'consolidation_active_deadline')
    for item in proposals:
        payload = {k: item[k] for k in ('page_type', 'key', 'title', 'body', 'kind', 'reason')}
        mapping = {citation['citation']: 'S' + str(number)
                   for number, citation in enumerate(item['citations'], 1)}
        payload['body'] = re.sub(r'\[(S\d+)\]', lambda found: '[' + mapping[found.group(1)] + ']', item['body'])
        payload.update(job=row['job'], citations=item['citations'])
        propose(db, root, payload)
        proposal_id = db.execute('SELECT id FROM consolidation_proposals WHERE job=? ORDER BY ordinal DESC LIMIT 1',
                                 (row['job'],)).fetchone()[0]
        db.execute('INSERT INTO consolidation_proposal_metadata VALUES(?,?,?,?,?)',
                   (proposal_id, item['epistemic_status'], item['scope'],
                    encoded([citation for citation in item['citations']
                             if citation['citation'] in item['conflicting_evidence_ids']]),
                    encoded(item['prerequisite_ids'])))
    db.execute("UPDATE consolidation_attempts SET state='imported',finished=?,outcome=?,result_digest=?,usage=?,lease_until=NULL,owner=NULL WHERE request_id=?",
               (now(), reply['outcome'], response_digest, encoded(usage) if usage else None, row['request_id']))
    db.execute('UPDATE consolidation_jobs SET state=?,updated=? WHERE id=?',
               ('ready' if proposals else 'analyzed', now(), row['job']))
    db.execute("UPDATE consolidation_queue SET state='completed' WHERE job=?", (row['job'],))
    from .knowledge_consolidation_selection import mark_analyzed
    mark_analyzed(db, packet, reply['coverage']['analyzed'])
    _continue_episode(db, root, row, packet, reply['coverage']['analyzed'])
    return {'job': row['job'], 'request_id': row['request_id'],
            'outcome': reply['outcome'], 'count': len(proposals), 'replay': False}


def fail(db, root, value):
    require(type(value) is dict and {'job', 'request_id', 'code', 'message'} <= set(value)
            and set(value) <= {'job', 'request_id', 'code', 'message', 'usage'})
    usage = _usage(value.get('usage'))
    require(identifier(value['job']) and identifier(value['request_id'])
            and identifier(value['code']) and type(value['message']) is str
            and len(value['message']) <= 500)
    row = db.execute('SELECT state FROM consolidation_attempts WHERE request_id=? AND job=?',
                     (value['request_id'], value['job'])).fetchone()
    require(row is not None and row['state'] in ('prepared', 'reserved', 'started'),
            'consolidation_attempt_state')
    db.execute("UPDATE consolidation_attempts SET state='failed',finished=?,error=?,usage=?,lease_until=NULL,owner=NULL WHERE request_id=?",
               (now(), encoded({'code': value['code'], 'message': value['message']}),
                encoded(usage) if usage else None, value['request_id']))
    db.execute("UPDATE consolidation_jobs SET state='failed',error=?,updated=? WHERE id=?",
               (encoded({'code': value['code']}), now(), value['job']))
    db.execute("UPDATE consolidation_queue SET state='failed' WHERE job=?", (value['job'],))
    _close_episode(db, value['job'])
    return {'job': value['job'], 'request_id': value['request_id'], 'state': 'failed'}


def _usage(value):
    if value is None:
        return None
    shape(value, 'input_tokens output_tokens')
    require(all(value[key] is None or type(value[key]) is int and 0 <= value[key] <= 1000000
                for key in ('input_tokens', 'output_tokens')))
    return value


def _episode_prefix(root, db, trigger, settings_value, period):
    return digest({'project': binding(db, root)['project'],
                   'generation': None if trigger == 'daily' else binding(db, root)['generation'],
                   'policy': digest(settings_value), 'period': period,
                   'trigger': 'daily' if trigger == 'daily' else 'change'})[:62]


def _budget_row(db, prefix):
    return db.execute('SELECT used,closed FROM consolidation_episode_budget WHERE prefix=?',
                      (prefix,)).fetchone()


def _close_episode(db, job):
    item = db.execute('SELECT dedup FROM consolidation_queue WHERE job=?', (job,)).fetchone()
    if item:
        db.execute('UPDATE consolidation_episode_budget SET closed=1,updated=? WHERE prefix=?',
                   (now(), item['dedup'][:62]))


def _trim_budgets(db, root):
    """Retain current-day/generation tombstones and all queue-linked budgets."""
    policy = get(db, SETTINGS_KEY) or _default_settings()
    day = datetime.now(timezone.utc).astimezone(ZoneInfo(policy['timezone'])).date().isoformat()
    protected = {_episode_prefix(root, db, 'source', policy, ''),
                 _episode_prefix(root, db, 'daily', policy, day)}
    while db.execute('SELECT count(*) FROM consolidation_episode_budget').fetchone()[0] >= 200:
        old = next((row for row in db.execute('SELECT prefix FROM consolidation_episode_budget ORDER BY updated,prefix')
                    if row['prefix'] not in protected and
                    db.execute('SELECT 1 FROM consolidation_queue WHERE substr(dedup,1,62)=? LIMIT 1',
                               (row['prefix'],)).fetchone() is None), None)
        require(old is not None, 'consolidation_episode_budget_limit')
        db.execute('DELETE FROM consolidation_episode_budget WHERE prefix=?', (old['prefix'],))


def _continue_episode(db, root, attempt_row, packet, analyzed):
    """Queue the next bounded batch only after durable analysis progress."""
    prior = db.execute('SELECT trigger,dedup FROM consolidation_queue WHERE job=?',
                       (attempt_row['job'],)).fetchone()
    if prior is None:
        return
    prefix, ordinal = prior['dedup'][:62], int(prior['dedup'][62:], 16)
    budget = _budget_row(db, prefix)
    require(budget is not None, 'consolidation_episode_budget_missing')
    if analyzed == 0:
        _close_episode(db, attempt_row['job'])
        return
    policy = get(db, SETTINGS_KEY) or _default_settings()
    if (not policy['enabled'] or policy['prompt'] != packet['prompt']
            or policy['config'] != packet['config'] or policy['mode'] != packet['mode']
            or packet['mode'] != 'manual' and policy['send_policy'] != packet['mode']):
        _close_episode(db, attempt_row['job'])
        return
    cap = min(6, packet['config']['maxRequests'])
    if budget['used'] >= cap or ordinal + 1 >= cap:
        _close_episode(db, attempt_row['job'])
        return
    from .knowledge_consolidation_selection import eligible_count
    if eligible_count(db) == 0:
        _close_episode(db, attempt_row['job'])
        return
    if prior['trigger'] == 'daily':
        day = datetime.now(timezone.utc).astimezone(ZoneInfo(policy['timezone'])).date().isoformat()
        if prefix != _episode_prefix(root, db, 'daily', policy, day):
            _close_episode(db, attempt_row['job'])
            return
    elif prefix != _episode_prefix(root, db, prior['trigger'], policy, ''):
        _close_episode(db, attempt_row['job'])
        return
    if packet['mode'] == 'api':
        day = datetime.now(timezone.utc).astimezone(ZoneInfo(policy['timezone'])).date().isoformat()
        if get(db, 'consolidation_send_count/' + day, 0) >= policy['daily_request_cap']:
            _close_episode(db, attempt_row['job'])
            return
    if db.execute('SELECT count(*) FROM consolidation_jobs').fetchone()[0] >= 20:
        put(db, 'consolidation_queue_error', 'consolidation_job_limit')
        return
    if db.execute('SELECT count(*) FROM consolidation_queue').fetchone()[0] >= 200:
        put(db, 'consolidation_queue_error', 'consolidation_queue_limit')
        return
    from .knowledge_consolidation import create
    job = create(db, root, {'title': 'Consolidation continuation ' + now()})
    prepare(db, root, {'job': job, 'prompt': policy['prompt'],
                       'config': policy['config'], 'mode': policy['mode']})
    db.execute('INSERT INTO consolidation_queue VALUES(?,?,?,?,?,?)',
               (uuid4().hex, prior['trigger'], prefix + format(ordinal + 1, '02x'),
                'queued', now(), job))
    db.execute('UPDATE consolidation_episode_budget SET used=used+1,updated=? WHERE prefix=?',
               (now(), prefix))


def queue(db, root, value):
    require(db.execute('PRAGMA user_version').fetchone()[0] == 3, 'consolidation_migration_required')
    from .knowledge_consolidation import stale
    for old in db.execute("SELECT q.id,q.job FROM consolidation_queue q WHERE q.state IN ('queued','claimed') AND q.job IS NOT NULL").fetchall():
        job = job_row(db, old['job'])
        if stale(db, root, job):
            db.execute("UPDATE consolidation_queue SET state='stale' WHERE id=?", (old['id'],))
            db.execute("UPDATE consolidation_jobs SET state='stale',updated=? WHERE id=?", (now(), old['job']))
    settings_value = get(db, SETTINGS_KEY) or _default_settings()
    if value is not None:
        shape(value, 'trigger event_id')
        require(value['trigger'] in TRIGGERS and identifier(value['event_id']))
        if not settings_value['enabled'] or value['trigger'] not in settings_value['triggers']:
            return {'queued': False, 'reason': 'disabled', 'items': []}
        require(not get(db, 'needs_reconcile', True), 'reconcile_required')
        if value['trigger'] == 'daily':
            local = datetime.now(timezone.utc).astimezone(ZoneInfo(settings_value['timezone']))
            require(local.hour >= settings_value['daily_hour'], 'consolidation_window_not_due')
            period = local.date().isoformat()
        else:
            period = ''
        prefix = _episode_prefix(root, db, value['trigger'], settings_value, period)
        budget = _budget_row(db, prefix)
        cap = min(6, settings_value['config']['maxRequests'])
        if budget and (budget['closed'] or budget['used'] >= cap):
            return {'queued': False, 'reason': 'episode_budget_exhausted', 'items': []}
        signature = prefix + format(budget['used'] if budget else 0, '02x')
        existing = db.execute('SELECT id FROM consolidation_queue WHERE substr(dedup,1,62)=?',
                              (prefix,)).fetchone()
        if not existing:
            require(db.execute('SELECT 1 FROM consolidation_queue WHERE id=?',
                               (value['event_id'],)).fetchone() is None,
                    'consolidation_event_conflict')
            from .knowledge_consolidation_selection import eligible_count
            if eligible_count(db) == 0:
                return {'queued': False, 'reason': 'no_eligible_sources', 'items': []}
            while db.execute('SELECT count(*) FROM consolidation_queue').fetchone()[0] >= 200:
                old = db.execute("SELECT id FROM consolidation_queue WHERE state IN ('completed','failed','canceled','interrupted') ORDER BY created,id LIMIT 1").fetchone()
                require(old is not None, 'consolidation_queue_limit')
                db.execute('DELETE FROM consolidation_queue WHERE id=?', (old['id'],))
            from .knowledge_consolidation import create
            job = create(db, root, {'title': 'Consolidation ' + value['trigger'] + ' ' + now()})
            prepare(db, root, {'job': job, 'prompt': settings_value['prompt'],
                               'config': settings_value['config'], 'mode': settings_value['mode']})
            db.execute('INSERT INTO consolidation_queue VALUES(?,?,?,?,?,?)',
                       (value['event_id'], value['trigger'], signature, 'queued', now(), job))
            if budget:
                db.execute('UPDATE consolidation_episode_budget SET used=used+1,updated=? WHERE prefix=?',
                           (now(), prefix))
            else:
                _trim_budgets(db, root)
                db.execute('INSERT INTO consolidation_episode_budget VALUES(?,?,?,?)',
                           (prefix, 1, 0, now()))
    rows = [dict(row) for row in db.execute('SELECT * FROM consolidation_queue ORDER BY rowid LIMIT 50')]
    return {'queued': any(row['state'] == 'queued' for row in rows), 'items': rows,
            'executor_available': False,
            'notice': 'Queued analysis waits for an attached runner; publication always requires review.'}


def prune(db, value):
    shape(value, 'max_terminal')
    require(type(value['max_terminal']) is int and 0 <= value['max_terminal'] <= 20)
    protected = {row[0] for row in db.execute("SELECT DISTINCT job FROM consolidation_proposals WHERE status='pending'")}
    for job in db.execute('SELECT id,receipt FROM consolidation_jobs WHERE receipt IS NOT NULL'):
        receipt = _json(job['receipt'])
        if receipt.get('undone'):
            continue
        for patch in receipt.get('patches', []):
            claim = db.execute('SELECT revision FROM memory_claims WHERE target=?',
                               (patch['target'],)).fetchone()
            if claim and claim['revision'] == digest(patch['after']):
                protected.add(job['id'])
                break
    terminal = [row[0] for row in db.execute("SELECT id FROM consolidation_jobs WHERE state IN ('analyzed','published','rejected','stale','failed','purged','canceled') ORDER BY updated DESC,id DESC")]
    count = 0
    for job in terminal[value['max_terminal']:]:
        if job in protected:
            continue
        db.execute('DELETE FROM consolidation_proposal_metadata WHERE proposal IN (SELECT id FROM consolidation_proposals WHERE job=?)', (job,))
        for table in ('consolidation_attempts', 'consolidation_queue', 'consolidation_inputs',
                      'consolidation_proposals', 'consolidation_events'):
            db.execute('DELETE FROM ' + table + ' WHERE job=?', (job,))
        db.execute('DELETE FROM consolidation_jobs WHERE id=?', (job,))
        count += 1
    return {'pruned': count}


def validate_storage(db):
    for row in db.execute('SELECT * FROM consolidation_attempts'):
        require(db.execute('SELECT 1 FROM consolidation_jobs WHERE id=?', (row['job'],)).fetchone() is not None,
                'invalid_consolidation_storage')
        require(identifier(row['request_id']) and row['mode'] in MODES
                and row['state'] in ('prepared', 'reserved', 'started', 'paused', 'canceled',
                                     'interrupted', 'failed', 'imported'), 'invalid_consolidation_storage')
        require(len(row['packet'].encode()) <= MAX_PACKET, 'consolidation_storage_limit')
        if row['packet'] != '{}':
            packet = _json(row['packet'])
            require(packet['job'] == row['job'] and packet['request_id'] == row['request_id'],
                    'invalid_consolidation_storage')
    for row in db.execute('SELECT * FROM consolidation_proposal_metadata'):
        require(row['epistemic_status'] in EPISTEMIC and len(row['scope']) <= 160,
                'invalid_consolidation_storage')
        require(db.execute('SELECT 1 FROM consolidation_proposals WHERE id=?', (row['proposal'],)).fetchone() is not None,
                'invalid_consolidation_storage')
        require(type(_json(row['prerequisites'])) is list and type(_json(row['conflicting'])) is list,
                'invalid_consolidation_storage')


def dispatch(root, action, value):
    if action == 'consolidation-settings' and value is None:
        with database(root) as db:
            return settings(db, None) if db else {'schema': 1, 'migration_required': True,
                                                   'settings': _default_settings()}
    if action == 'consolidation-queue' and value is None:
        with database(root) as db:
            if db is None or db.execute('PRAGMA user_version').fetchone()[0] != 3:
                return {'migration_required': True, 'queued': False, 'items': []}
            with db:
                recover(db)
                return queue(db, root, None)
    if (action in ('consolidation-prepare', 'consolidation-import')
            or action == 'consolidation-attempt' and isinstance(value, dict)
            and value.get('operation') in ('reserve', 'start', 'retry')):
        from .knowledge_service import reconcile
        reconcile(root)
    with database(root) as db:
        require(db is not None and db.execute('PRAGMA user_version').fetchone()[0] == 3,
                'consolidation_migration_required')
        with db:
            recover(db)
            if action == 'consolidation-settings':
                return settings(db, value)
            if action == 'consolidation-prepare':
                return prepare(db, root, value)
            if action == 'consolidation-attempt':
                return attempt(db, root, value)
            if action == 'consolidation-import':
                return import_reply(db, root, value)
            if action == 'consolidation-fail':
                return fail(db, root, value)
            if action == 'consolidation-queue':
                return queue(db, root, value)
            if action == 'consolidation-prune':
                return prune(db, value)
    raise KnowledgeError('invalid_consolidation_action')
