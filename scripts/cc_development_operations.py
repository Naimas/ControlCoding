"""Stateless operational feedback from bounded canonical local observations.

Retained observations describe history. Local hashes detect inconsistent records;
they are neither authenticated incident attestations nor current health claims.
"""
from __future__ import annotations

import copy
import datetime
import json
import hashlib

import cc_evidence_inputs as inputs
from cc_layout import managed_relative
from cc_development_adapters import require, read_json, monitor_metrics, assess_release, candidate_current, IDENTIFIER

MAX_SNAPSHOT_BYTES = 128 * 1024
MAX_OCCURRENCES = 256


def _hash(value):
    return inputs.digest(value)


def _bounded(value):
    require(len(json.dumps(value, allow_nan=False, ensure_ascii=True).encode()) <= MAX_SNAPSHOT_BYTES,
            'incident_snapshot_too_large')


def _stamp(value):
    require(type(value) is str, 'incident_timestamp_invalid')
    stamp = datetime.datetime.fromisoformat(value)
    require(stamp.tzinfo is not None, 'incident_timestamp_invalid')
    return stamp


def _fact(receipt, signal_id):
    configuration = receipt['plan']['configuration']
    result = monitor_metrics(configuration, receipt['observation'])
    require(result['state'] in ('allowed', 'blocked'), 'incident_monitor_invalid')
    rules = [r for r in configuration['metrics'] if r['id'] == signal_id]
    require(len(rules) == 1, 'incident_signal_missing')
    rule, sample = rules[0], receipt['observation']['metrics'][signal_id]
    return {'metric': signal_id, 'window': configuration['window'], 'baseline': rule['baseline'],
            'maxDelta': rule['maxDelta'], 'threshold': rule['baseline'] + rule['maxDelta'],
            'value': sample['value'], 'samples': sample['samples'], 'minimumSamples': rule['minimumSamples'],
            'citation': sample['citation'], 'breached': sample['value'] > rule['baseline'] + rule['maxDelta']}


def validate_observation(snapshot):
    """Validate retained local facts without consulting mutable project state."""
    from cc_development_jobs import _validate_receipt
    _bounded(snapshot)
    require(type(snapshot) is dict and set(snapshot) == {'id', 'capturedAt', 'parentChangeId', 'receipt',
            'receiptDigest', 'configurationDigest', 'release', 'releaseAssessment', 'releaseReceipt', 'group', 'facts', 'signalId'}, 'incident_snapshot_invalid')
    _stamp(snapshot['capturedAt'])
    require(type(snapshot['signalId']) is str and IDENTIFIER.fullmatch(snapshot['signalId']), 'incident_signal_invalid')
    receipt = _validate_receipt(snapshot['receipt'])
    require(receipt.get('schemaVersion') == 1 and receipt.get('kind') == 'monitor' and receipt.get('state') == 'allowed'
            and receipt.get('provenance') == 'observed_command', 'incident_receipt_invalid')
    receipt_id = receipt.get('id')
    require(type(receipt_id) is str and len(receipt_id) == 48 and receipt_id.startswith('development_job_')
            and all(c in '0123456789abcdef' for c in receipt_id[16:]), 'incident_receipt_invalid')
    plan = receipt['plan']
    approval = dict(plan)
    approval.pop('approval_id')
    require(_hash(approval) == receipt['approvalId'] == plan['approval_id'], 'incident_approval_invalid')
    require(receipt['command'].get('status') == 'passed' and receipt['command'].get('returnCode') == 0
            and receipt['command'].get('error') is None, 'incident_command_invalid')
    require(snapshot['receiptDigest'] == _hash(receipt) and snapshot['configurationDigest'] == _hash(plan['configuration'])
            and snapshot['parentChangeId'] == plan['changeId'], 'incident_binding_invalid')
    release, assessment = snapshot['release'], snapshot['releaseAssessment']
    release_receipt = _validate_receipt(snapshot['releaseReceipt'])
    release_plan = release_receipt['plan']
    release_approval = {k: v for k, v in release_plan.items() if k != 'approval_id'}
    require(release_receipt.get('schemaVersion') == 1 and release_receipt.get('kind') in ('deploy', 'rollback', 'release_status')
            and release_receipt.get('state') == 'allowed' and release_receipt.get('provenance') == 'observed_command'
            and release_receipt['id'] == assessment.get('receiptId') == release.get('observation', {}).get('receiptId')
            and release_receipt['observation'] == assessment.get('observation')
            and release_receipt['approvalId'] == release_plan['approval_id'] == _hash(release_approval)
            and all(release_plan.get(k) == plan.get(k) for k in ('candidateDigest', 'changeId', 'method', 'projectIdentity', 'root')),
            'incident_release_receipt_invalid')
    require(release_receipt['command'].get('status') == 'passed' and release_receipt['command'].get('returnCode') == 0
            and release_receipt['command'].get('error') is None, 'incident_release_command_invalid')
    require(release.get('status') == 'observed' and assessment.get('state') == 'allowed'
            and assessment.get('provenance') == 'observed_command'
            and assessment.get('observation') == release.get('observation', {}).get('observation'), 'incident_release_invalid')
    group = {'releaseId': _hash(release), 'releaseReceiptId': assessment['receiptId'],
             'candidateDigest': plan['candidateDigest'], 'deployedDigest': plan['configuration']['deployedDigest'],
             'environment': release['environment'],
             'window': plan['configuration']['window']}
    require(group['deployedDigest'] == release['artifact_digest']
            and type(group['environment']) is str and bool(group['environment'].strip())
            and plan['configuration'].get('environment') == group['environment']
            and assessment['observation']['candidateDigest'] == group['candidateDigest']
            and assessment['observation']['environment'] == group['environment']
            and assessment['observation']['deployedDigest'] == group['deployedDigest']
            and release_plan['configuration'].get('environment') == group['environment']
            and release_plan['configuration'].get('mode', 'live') != 'rehearsal'
            and assessment['observation'].get('status') in ('deployed', 'released', 'rolled_back'),
            'incident_release_binding_invalid')
    facts = [_fact(receipt, rule['id']) for rule in plan['configuration']['metrics']]
    require(snapshot['capturedAt'] == receipt['finishedAt'] and snapshot['group'] == group
            and snapshot['facts'] == facts, 'incident_fact_changed')
    require(snapshot['id'] == _hash({k: v for k, v in snapshot.items() if k != 'id'}), 'incident_snapshot_changed')
    return snapshot


def validate_incident(incident):
    require(type(incident) is dict and set(incident) == {'id', 'parentChangeId', 'group', 'occurrences', 'revision'}, 'incident_invalid')
    occurrences = incident['occurrences']
    require(type(occurrences) is list and 1 <= len(occurrences) <= MAX_OCCURRENCES, 'incident_occurrences_invalid')
    seen = set()
    for occurrence in occurrences:
        require(type(occurrence) is dict and set(occurrence) == {'snapshot', 'fact'}, 'incident_occurrence_invalid')
        snapshot = validate_observation(occurrence['snapshot'])
        require(occurrence['fact'] in snapshot['facts'] and occurrence['fact']['breached']
                and {**snapshot['group'], 'metric': occurrence['fact']['metric']} == incident['group']
                and snapshot['parentChangeId'] == incident['parentChangeId'], 'incident_occurrence_mismatch')
        key = snapshot['receipt']['id']
        require(key not in seen, 'incident_occurrence_duplicate')
        seen.add(key)
    require(incident['id'] == _hash({'parentChangeId': incident['parentChangeId'], 'group': incident['group']})
            and incident['revision'] == _hash({'group': incident['group'], 'occurrences': occurrences}), 'incident_revision_changed')
    return incident


def incidents_from_captures(captures):
    """Reconstruct the canonical projection from retained capture history."""
    require(type(captures) is list and len(captures) <= MAX_OCCURRENCES, 'incident_captures_invalid')
    records = {}
    seen = set()
    for snapshot in captures:
        validate_observation(snapshot)
        require(snapshot['receipt']['id'] not in seen, 'incident_capture_duplicate')
        seen.add(snapshot['receipt']['id'])
        for fact in snapshot['facts']:
            if not fact['breached']:
                continue
            group = {**snapshot['group'], 'metric': fact['metric']}
            key = _hash({'parentChangeId': snapshot['parentChangeId'], 'group': group})
            record = records.setdefault(key, {'id': key, 'parentChangeId': snapshot['parentChangeId'],
                                             'group': group, 'occurrences': []})
            record['occurrences'].append({'snapshot': copy.deepcopy(snapshot), 'fact': copy.deepcopy(fact)})
    for record in records.values():
        record['revision'] = _hash({'group': record['group'], 'occurrences': record['occurrences']})
        validate_incident(record)
    return list(records.values())


def validate_change(change, *, allow_preview=False):
    captures = change.get('incident_captures', [])
    require(type(captures) is list and len(captures) <= MAX_OCCURRENCES, 'incident_captures_invalid')
    capture_keys = set()
    retained_project = None
    for snapshot in captures:
        validate_observation(snapshot)
        key = snapshot['receipt']['id']
        require(snapshot['parentChangeId'] == change['id'] and key not in capture_keys, 'incident_capture_duplicate')
        capture_keys.add(key)
        plan = snapshot['receipt']['plan']
        require(plan.get('method') == change.get('method'), 'incident_method_changed')
        project = (plan.get('projectIdentity'), plan.get('root'))
        require(retained_project is None or retained_project == project, 'incident_project_changed')
        retained_project = project
    seen = set()
    for incident in change.get('incidents', []):
        validate_incident(incident)
        require(incident['parentChangeId'] == change['id'] and incident['id'] not in seen, 'incident_parent_invalid')
        seen.add(incident['id'])
    require(change.get('incidents', []) == incidents_from_captures(captures), 'incident_projection_changed')
    for decision in change.get('incident_decisions', []):
        require(type(decision) is dict and set(decision) == {'incident_id', 'incident_revision', 'decision',
                'owner', 'actor', 'reason', 'timestamp', 'child_id'}, 'incident_decision_invalid')
        require(decision['incident_id'] in seen and decision['decision'] in ('resolved', 'rejected', 'deferred', 'followup'),
                'incident_decision_invalid')
        for field in ('owner', 'actor', 'reason'):
            require(type(decision[field]) is str and 0 < len(decision[field].strip()) <= 4096, 'incident_decision_invalid')
        if not (allow_preview and decision['timestamp'] == 'preview'):
            _stamp(decision['timestamp'])
        incident = next(i for i in change['incidents'] if i['id'] == decision['incident_id'])
        revisions = {_hash({'group': incident['group'], 'occurrences': incident['occurrences'][:n]})
                     for n in range(1, len(incident['occurrences']) + 1)}
        require(decision['incident_revision'] in revisions, 'incident_decision_revision_invalid')
        require((decision['decision'] == 'followup') == (type(decision['child_id']) is str and bool(decision['child_id'])),
                'incident_decision_child_invalid')
        if decision['decision'] != 'followup':
            require(decision['child_id'] is None, 'incident_decision_child_invalid')
    return True


def reports(change, *, allow_preview=False):
    validate_change(change, allow_preview=allow_preview)
    result = []
    for incident in change.get('incidents', []):
        decisions = [d for d in change.get('incident_decisions', []) if d.get('incident_id') == incident['id']]
        decision = decisions[-1] if decisions else None
        result.append({**copy.deepcopy(incident), 'recurrence_count': len(incident['occurrences']),
                       'latest_decision': copy.deepcopy(decision),
                       'current_decision': bool(decision and decision.get('incident_revision') == incident['revision']),
                       'linked_changes': list(dict.fromkeys(d['child_id'] for d in decisions if d.get('child_id'))),
                       'historical': True, 'currentHealth': 'unknown'})
    return result


def incident_view(change, incident_id):
    validate_change(change)
    matches = [i for i in change.get('incidents', []) if i['id'] == incident_id]
    require(len(matches) == 1, 'incident_missing')
    incident = copy.deepcopy(matches[0])
    decisions = [d for d in change.get('incident_decisions', []) if d.get('incident_id') == incident_id
                 and d.get('incident_revision') == incident['revision']]
    return {**incident, 'recurrence': len(incident['occurrences']), 'disposition': decisions[-1]['decision'] if decisions else 'open',
            'historical': True, 'currentHealth': 'unknown'}


def capture(root, change, receipt_id, signal_id, timestamp):
    from cc_development_jobs import RECEIPTS, _validate_receipt, assess_job_observation, _attempts
    validate_change(change)
    snapshots = list(change.get('incident_captures', [])) + [o['snapshot'] for i in change.get('incidents', []) for o in i['occurrences']]
    for event in snapshots:
        if event['receipt']['id'] == receipt_id:
            require(event['signalId'] == signal_id, 'incident_receipt_already_captured')
            existing = [i for i in change.get('incidents', []) if any(o['snapshot']['receipt']['id'] == receipt_id for o in i['occurrences'])]
            return {'observation': copy.deepcopy(event), 'incidents': copy.deepcopy(existing), 'replay': True}
    require(type(receipt_id) is str and len(receipt_id) == 48 and receipt_id.startswith('development_job_')
            and all(c in '0123456789abcdef' for c in receipt_id[16:]), 'job_receipt_invalid')
    receipt, _ = read_json(root, managed_relative(root, RECEIPTS) + '/' + receipt_id + '.json')
    _validate_receipt(receipt)
    observed = assess_job_observation(root, {'receiptId': receipt_id, 'observation': receipt['observation']}, change, 'monitor')
    require(observed['state'] == 'allowed', 'incident_monitor_unobserved')
    require(observed['observation'] == receipt['observation'] and observed['configuration'] == receipt['plan']['configuration'],
            'incident_monitor_changed')
    releases = change.get('releases', [])
    require(releases and type(releases[-1]) is dict, 'incident_observed_release_missing')
    release = releases[-1]
    require(release.get('status') == 'observed'
            and release.get('artifact_digest') == observed['configuration']['deployedDigest'],
            'incident_latest_release_unobserved')
    assessment = assess_release(root, change, release)
    require(assessment['state'] == 'allowed', 'incident_release_stale')
    release_receipt, _ = read_json(root, managed_relative(root, RECEIPTS) + '/' + assessment['receiptId'] + '.json')
    _validate_receipt(release_receipt)
    environment = observed['configuration'].get('environment')
    require(type(environment) is str and bool(environment.strip())
            and environment == release['environment'], 'incident_environment_mismatch')
    attempts = [r for r in _attempts(root, 'monitor', change, environment)
                if r['plan'].get('adapterId') == receipt['plan'].get('adapterId')
                and r['plan'].get('configuration', {}).get('window') == observed['configuration'].get('window')]
    require(attempts and attempts[-1]['id'] == receipt_id, 'incident_monitor_superseded')
    require(attempts[-1] == receipt, 'incident_monitor_changed')
    if timestamp != 'preview':
        require(_stamp(timestamp) >= _stamp(receipt['finishedAt']), 'incident_capture_predates_receipt')
    facts = [_fact(receipt, rule['id']) for rule in observed['configuration']['metrics']]
    group = {'releaseId': _hash(release), 'releaseReceiptId': assessment['receiptId'],
             'candidateDigest': change['candidate']['digest'], 'deployedDigest': release['artifact_digest'],
             'environment': release['environment'], 'window': observed['configuration']['window']}
    snapshot = {'capturedAt': receipt['finishedAt'], 'parentChangeId': change['id'], 'signalId': signal_id, 'receipt': copy.deepcopy(receipt),
                'receiptDigest': _hash(receipt), 'configurationDigest': _hash(observed['configuration']),
                'release': copy.deepcopy(release), 'releaseAssessment': copy.deepcopy(assessment),
                'releaseReceipt': copy.deepcopy(release_receipt), 'group': group, 'facts': facts}
    snapshot['id'] = _hash(snapshot)
    validate_observation(snapshot)
    incidents = []
    for fact in facts:
        if not fact['breached']:
            continue
        incident_group = {**group, 'metric': fact['metric']}
        incident_id = _hash({'parentChangeId': change['id'], 'group': incident_group})
        old = next((i for i in change.get('incidents', []) if i['id'] == incident_id), None)
        occurrences = copy.deepcopy(old['occurrences']) if old else []
        occurrences.append({'snapshot': snapshot, 'fact': fact})
        incident = {'id': incident_id, 'parentChangeId': change['id'], 'group': incident_group, 'occurrences': occurrences,
                    'revision': _hash({'group': incident_group, 'occurrences': occurrences})}
        validate_incident(incident)
        incidents.append(incident)
    return {'observation': snapshot, 'incidents': incidents, 'replay': False}


def proposed_intent(incident):
    validate_incident(incident)
    fact = incident['occurrences'][-1]['fact']
    return {'problem': f"Observed {fact['metric']} breached its retained threshold in {incident['group']['environment']}.",
            'outcome': 'Investigate the observed breach and independently verify an appropriate correction.',
            'affected_users_and_systems': [incident['group']['environment']],
            'constraints': ['Retained operational evidence describes historical observations.'],
            'non_goals': ['Automatic deployment or automatic acceptance of a diagnosis.'],
            'open_questions': ['What explains the observation and which independent checks establish the correction?'],
            'success_measures': [f"Independently verify {fact['metric']} against the approved monitoring rule."]}


def diagnosis_request(root, change, incident_id):
    incident = incident_view(change, incident_id)
    require(change.get('candidate', {}).get('digest') == incident['group']['candidateDigest']
            and candidate_current(root, change['candidate'])['state'] == 'allowed', 'diagnosis_candidate_stale')
    files = change['candidate']['files']
    require(len(files) <= 64, 'diagnosis_source_bounds')
    sources = []
    content_remaining = 8 * 1024
    with inputs.SafeRoot(root) as reader:
        budget = inputs.Budget(total=2 * 1024 * 1024, per_file=2 * 1024 * 1024, entries=64)
        for item in files:
            budget.entry()
            raw, _ = reader.read(item['path'], budget)
            require(len(raw) == item['size'] and hashlib.sha256(raw).hexdigest() == item['sha256'], 'diagnosis_source_changed')
            try:
                content = raw.decode('utf-8') if b'\x00' not in raw else None
            except UnicodeDecodeError:
                content = None
            reason = 'binary' if content is None else None
            if content is not None and len(raw) > content_remaining:
                content, reason = None, 'content_budget'
            elif content is not None:
                content_remaining -= len(raw)
            sources.append({**item, 'content': content, 'reason': reason})
        reader.validate()
    request = {'schemaVersion': 1, 'incidentId': incident_id, 'incidentRevision': incident['revision'],
               'parentChangeId': change['id'], 'candidateDigest': incident['group']['candidateDigest'],
               'binding': {'method': change.get('method'), 'group': incident['group'],
                           'configurationDigest': incident['occurrences'][-1]['snapshot']['configurationDigest'],
                           'receiptDigest': incident['occurrences'][-1]['snapshot']['receiptDigest']},
               'events': [{'id': o['snapshot']['id'], 'receiptId': o['snapshot']['receipt']['id'],
                           'capturedAt': o['snapshot']['capturedAt'], 'group': incident['group'], **o['fact']}
                          for o in incident['occurrences']], 'sourceFiles': sources,
               'limitations': ['Historical observations do not establish current health.',
                               'Diagnosis findings are hypotheses requiring independent verification.']}
    _bounded(request)
    request['requestDigest'] = _hash(request)
    return request
