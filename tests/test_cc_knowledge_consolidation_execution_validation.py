"""Malformed execution backups cannot become a target archive or active request."""
import hashlib
import json

import pytest

from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib import knowledge_backup as backups
from cc_memory_lib.knowledge_consolidation import digest
from cc_memory_lib.knowledge_consolidation_store import migrate
from cc_memory_lib.knowledge_store import KnowledgeError, database


CONFIG = {'provider': 'ollama', 'model': 'fixture', 'generation': {
    'effort': 'default', 'thinking': None, 'reasoningMode': 'default',
    'verbosity': 'default', 'temperature': None, 'format': 'json', 'summary': False},
    'maxContextChars': 12000, 'maxOutputTokens': 2048, 'timeoutSeconds': 120,
    'maxRequests': 3, 'roleRevision': 'fixture-revision'}


@pytest.fixture
def prepared(project, tmp_path):
    migrate(project, tmp_path / 'before.ccmemory')
    view = service.dispatch(project, 'consolidation-create', {'title': 'Validate durable execution'})
    packet = service.dispatch(project, 'consolidation-prepare', {
        'job': view['job']['id'], 'prompt': 'Review only the pinned original evidence.',
        'config': CONFIG, 'mode': 'manual'})
    return project, packet


def change(table, field, value, row=0):
    table['rows'][row][table['columns'].index(field)] = value


@pytest.mark.parametrize('corruption', (
    'unknown_packet_field', 'nested_secret', 'packet_digest', 'foreign_anchor',
    'orphan_job', 'invalid_started_lease', 'duplicate_ordinal', 'orphan_metadata',
    'unbounded_queue', 'orphan_progress', 'invalid_progress_chunk',
))
def test_corrupt_v3_backup_rejected_before_creating_target(prepared, tmp_path, corruption):
    root, packet = prepared
    path = tmp_path / 'valid.ccmemory'
    backups.backup(root, path)
    raw = json.loads(path.read_bytes().split(b'\n', 1)[1])
    tables = raw['tables']
    attempts = tables['consolidation_attempts']
    row = dict(zip(attempts['columns'], attempts['rows'][0]))
    saved = json.loads(row['packet'])
    if corruption == 'unknown_packet_field':
        saved['execute'] = 'untrusted command'
    elif corruption == 'nested_secret':
        saved['config']['generation']['extension'] = {'credential': 'fixture-canary'}
    elif corruption == 'foreign_anchor':
        saved['input_manifest'][0]['revision'] = 'f' * 64
        saved['manifest_digest'] = digest(saved['input_manifest'])
    elif corruption == 'packet_digest':
        change(attempts, 'packet_digest', '0' * 64)
    elif corruption == 'orphan_job':
        change(attempts, 'job', 'missing-job')
    elif corruption == 'invalid_started_lease':
        change(attempts, 'state', 'started')
    elif corruption == 'duplicate_ordinal':
        extra = list(attempts['rows'][0])
        copy_packet = {**saved, 'request_id': 'duplicate-request'}
        extra[attempts['columns'].index('request_id')] = copy_packet['request_id']
        extra[attempts['columns'].index('packet')] = json.dumps(copy_packet)
        extra[attempts['columns'].index('packet_digest')] = digest(copy_packet)
        attempts['rows'].append(extra)
    elif corruption == 'orphan_metadata':
        table = tables['consolidation_proposal_metadata']
        value = {'proposal': 'missing', 'epistemic_status': 'observed', 'scope': 'project',
                 'conflicting': '[]', 'prerequisites': '[]'}
        table['rows'].append([value[key] for key in table['columns']])
    elif corruption in ('orphan_progress', 'invalid_progress_chunk'):
        table = tables['consolidation_passage_progress']
        value = {'chunk': 'bad' if corruption == 'invalid_progress_chunk' else 'f' * 64,
                 'source': 'missing' if corruption == 'orphan_progress' else saved['input_manifest'][0]['source'],
                 'revision': saved['input_manifest'][0]['revision'], 'analyzed': '2026-09-28T12:00:00+00:00'}
        table['rows'].append([value[key] for key in table['columns']])
    else:
        table = tables['consolidation_queue']
        for i in range(201):
            value = {'id': f'event-{i}', 'trigger': 'source', 'dedup': digest(i),
                     'state': 'queued', 'created': '2026-09-28T12:00:00+00:00', 'job': None}
            table['rows'].append([value[key] for key in table['columns']])
    if corruption in ('unknown_packet_field', 'nested_secret', 'foreign_anchor'):
        change(attempts, 'packet', json.dumps(saved))
        change(attempts, 'packet_digest', digest(saved))
    content = json.dumps(raw).encode('utf-8')
    bad = tmp_path / 'corrupt.ccmemory'
    bad.write_bytes(b'CCMEMORY/3 ' + hashlib.sha256(content).hexdigest().encode() + b'\n' + content)
    target = tmp_path / 'target'
    target.mkdir()
    with pytest.raises(KnowledgeError, match='invalid_consolidation_storage'):
        backups.restore(target, bad)
    assert not (target / '.controlcoding').exists()


def test_v3_restore_disarms_prepared_manual_request(prepared, tmp_path):
    root, packet = prepared
    path = tmp_path / 'valid.ccmemory'
    backups.backup(root, path)
    target = tmp_path / 'target'
    target.mkdir()
    backups.restore(target, path)
    with database(target) as db:
        request = db.execute('SELECT * FROM consolidation_attempts').fetchone()
        assert request['state'] == 'interrupted' and request['packet'] == '{}'
        assert request['owner'] is None and request['lease_until'] is None
    # Restored tombstones themselves remain exportable.
    backups.backup(target, tmp_path / 'restored.ccmemory')
    result = service.dispatch(target, 'consolidation-settings')
    assert not result['settings']['enabled'] and result['settings']['send_policy'] == 'none'
    with pytest.raises(KnowledgeError, match='consolidation_attempt_unavailable'):
        service.dispatch(target, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'], 'text': '{}'})


def test_invalid_generation_cannot_be_persisted(prepared):
    root, packet = prepared
    with pytest.raises(KnowledgeError):
        service.dispatch(root, 'consolidation-prepare', {
            'job': packet['job'], 'prompt': packet['prompt'], 'mode': 'manual',
            'config': {**CONFIG, 'generation': {**CONFIG['generation'], 'api_key': 'fixture-secret'}}})
    with database(root) as db:
        assert 'fixture-secret' not in db.execute('SELECT packet FROM consolidation_attempts').fetchone()[0]
