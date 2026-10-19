"""Bounded diagnostic hypotheses. Validation does not establish causal truth."""
from __future__ import annotations

import math
import json
import hashlib
import datetime

import cc_evidence_inputs as inputs
from cc_development_adapters import HEX, IDENTIFIER, require


def assess_request(request):
    require(type(request) is dict and set(request) == {'schemaVersion', 'requestDigest', 'incidentId',
            'incidentRevision', 'parentChangeId', 'candidateDigest', 'binding', 'events', 'sourceFiles', 'limitations'}
            and type(request.get('schemaVersion')) is int and request.get('schemaVersion') == 1,
            'diagnosis_request_invalid')
    require(len(json.dumps(request, allow_nan=False, ensure_ascii=True).encode()) <= 128 * 1024,
            'diagnosis_request_bounds')
    require(request.get('requestDigest') == inputs.digest({k: v for k, v in request.items() if k != 'requestDigest'}),
            'diagnosis_request_digest_invalid')
    require(type(request.get('incidentId')) is str and HEX.fullmatch(request['incidentId'])
            and type(request.get('incidentRevision')) is str and HEX.fullmatch(request['incidentRevision'])
            and type(request['candidateDigest']) is str and HEX.fullmatch(request['candidateDigest'])
            and type(request['parentChangeId']) is str and IDENTIFIER.fullmatch(request['parentChangeId']), 'diagnosis_request_invalid')
    binding = request['binding']
    require(type(binding) is dict and set(binding) == {'method', 'group', 'configurationDigest', 'receiptDigest'}
            and type(binding['method']) is dict
            and all(type(binding[k]) is str and HEX.fullmatch(binding[k]) for k in ('configurationDigest', 'receiptDigest')),
            'diagnosis_binding_invalid')
    group = binding['group']
    require(type(group) is dict and set(group) == {'releaseId', 'releaseReceiptId', 'candidateDigest',
            'deployedDigest', 'environment', 'window', 'metric'}
            and all(type(group[k]) is str and HEX.fullmatch(group[k]) for k in ('releaseId', 'candidateDigest', 'deployedDigest'))
            and group['candidateDigest'] == request['candidateDigest']
            and all(type(group[k]) is str and 1 <= len(group[k]) <= 2048 for k in ('releaseReceiptId', 'environment', 'window', 'metric')),
            'diagnosis_binding_invalid')
    events = request.get('events')
    require(type(events) is list and 1 <= len(events) <= 256
            and all(type(e) is dict and type(e.get('id')) is str and HEX.fullmatch(e['id']) for e in events)
            and len({e['id'] for e in events}) == len(events), 'diagnosis_events_invalid')
    for event in events:
        require(set(event) == {'id', 'receiptId', 'capturedAt', 'group', 'metric', 'window', 'baseline',
                'maxDelta', 'threshold', 'value', 'samples', 'minimumSamples', 'citation', 'breached'}
                and event['group'] == group and event['metric'] == group['metric'] and event['window'] == group['window']
                and type(event['receiptId']) is str and len(event['receiptId']) == 48
                and event['receiptId'].startswith('development_job_')
                and all(c in '0123456789abcdef' for c in event['receiptId'][16:])
                and type(event['citation']) is str and 1 <= len(event['citation']) <= 8192,
                'diagnosis_event_invalid')
        require(type(event['capturedAt']) is str and datetime.datetime.fromisoformat(event['capturedAt']).tzinfo is not None,
                'diagnosis_event_invalid')
        require(all(type(event[k]) in (int, float) and -1e308 <= event[k] <= 1e308
                    and math.isfinite(event[k]) for k in ('baseline', 'maxDelta', 'threshold', 'value'))
                and event['maxDelta'] >= 0 and event['threshold'] == event['baseline'] + event['maxDelta']
                and type(event['samples']) is int and type(event['minimumSamples']) is int
                and event['samples'] >= event['minimumSamples'] >= 1
                and event['breached'] is True and event['value'] > event['threshold'], 'diagnosis_event_invalid')
    sources = request['sourceFiles']
    require(type(sources) is list and len(sources) <= 64, 'diagnosis_source_bounds')
    paths, content_bytes, source_bytes = set(), 0, 0
    for source in sources:
        require(type(source) is dict and set(source) == {'path', 'sha256', 'size', 'content', 'reason'}
                and type(source['path']) is str and source['path'].casefold() not in paths
                and type(source['sha256']) is str and HEX.fullmatch(source['sha256'])
                and type(source['size']) is int and source['size'] >= 0, 'diagnosis_source_invalid')
        inputs.parts(source['path'])
        paths.add(source['path'].casefold())
        source_bytes += source['size']
        if source['content'] is None:
            require(source['reason'] in ('binary', 'content_budget'), 'diagnosis_source_invalid')
        else:
            require(type(source['content']) is str and source['reason'] is None and '\x00' not in source['content'], 'diagnosis_source_invalid')
            raw = source['content'].encode('utf-8')
            require(len(raw) == source['size'] and hashlib.sha256(raw).hexdigest() == source['sha256'], 'diagnosis_source_invalid')
            content_bytes += len(raw)
    require(content_bytes <= 8 * 1024 and source_bytes <= 2 * 1024 * 1024, 'diagnosis_source_bounds')
    require(request['limitations'] == ['Historical observations do not establish current health.',
            'Diagnosis findings are hypotheses requiring independent verification.'], 'diagnosis_limitations_invalid')
    return {'state': 'allowed', 'reasons': [], 'eventIds': [e['id'] for e in events]}


def assess_result(request, result):
    """Reject uncited/unsupported wire claims; statements remain hypotheses."""
    allowed = set(assess_request(request)['eventIds'])
    require(type(result) is dict and set(result) == {
        'schemaVersion', 'requestDigest', 'incidentId', 'incidentRevision', 'outcome', 'findings'},
        'diagnosis_result_invalid')
    require(type(result['schemaVersion']) is int and result['schemaVersion'] == 1
            and type(result['incidentRevision']) is str
            and all(result[k] == request[k] for k in ('requestDigest', 'incidentId', 'incidentRevision')),
            'diagnosis_result_binding_invalid')
    findings = result['findings']
    require(result['outcome'] in ('reported', 'abstained') and type(findings) is list and len(findings) <= 16
            and (bool(findings) if result['outcome'] == 'reported' else not findings), 'diagnosis_outcome_invalid')
    seen = set()
    for finding in findings:
        require(type(finding) is dict and set(finding) == {
            'id', 'statement', 'confidence', 'hypothesis', 'citations', 'proposedChecks'}, 'diagnosis_finding_invalid')
        require(type(finding['id']) is str and IDENTIFIER.fullmatch(finding['id']) and finding['id'] not in seen,
                'diagnosis_finding_invalid')
        seen.add(finding['id'])
        require(type(finding['statement']) is str and 1 <= len(finding['statement']) <= 2048
                and '\x00' not in finding['statement'] and finding['hypothesis'] is True,
                'diagnosis_unsupported_claim')
        confidence = finding['confidence']
        require(type(confidence) in (int, float) and 0 <= confidence <= 1 and math.isfinite(confidence),
                'diagnosis_confidence_invalid')
        citations = finding['citations']
        require(type(citations) is list and 1 <= len(citations) <= 32
                and all(type(c) is str and c in allowed for c in citations)
                and len(set(citations)) == len(citations), 'diagnosis_citation_invalid')
        checks = finding['proposedChecks']
        require(type(checks) is list and 1 <= len(checks) <= 16
                and all(type(c) is str and 1 <= len(c) <= 1024 and '\x00' not in c for c in checks),
                'diagnosis_checks_invalid')
    return {'state': 'allowed', 'reasons': [], 'outcome': result['outcome'],
            'findings': findings, 'causalCorrectnessVerified': False}


def reports(root, change, *, locked=False):
    """Project canonical attempts; newer failed/running attempts supersede success."""
    import cc_development_jobs as jobs
    try:
        attempts = jobs._attempts(root, 'diagnosis', change)
        newest = {}
        for receipt in attempts:
            newest[receipt['plan']['configuration']['incidentId']] = receipt['id']
        result = []
        for receipt in attempts:
            report = jobs.assess_job_observation(root, {'receiptId': receipt['id'],
                    'observation': receipt['observation']}, change, 'diagnosis', _locked=locked)
            result.append({**report, 'id': receipt['id'], 'incidentId': receipt['plan']['configuration']['incidentId'],
                           'current': newest[receipt['plan']['configuration']['incidentId']] == receipt['id'],
                           'attemptState': receipt['state']})
        return result
    except (OSError, ValueError, KeyError, TypeError, inputs.EvidenceError) as exc:
        return [{'state': 'unknown', 'current': False, 'reasons': [getattr(exc, 'code', 'diagnosis_receipts_unavailable')]}]
