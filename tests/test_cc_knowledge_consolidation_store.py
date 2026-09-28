"""Explicit current migration and compatibility with genuine version-two archives."""
import hashlib
import json
import sqlite3

import pytest

from test_cc_knowledge import project
from cc_memory_lib import knowledge_backup as backups
from cc_memory_lib import knowledge_consolidation_store as storage
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_consolidation import binding
from cc_memory_lib import knowledge_consolidation as consolidation
from cc_memory_lib.knowledge_consolidation_validation import claim_id
from cc_memory_lib.knowledge_store import KnowledgeError, database


def legacy_v2(project):
    """Model an archive written by the prior release, without invoking v3 migration."""
    with database(project) as db:
        with db:
            for statement in storage.DDL_STATEMENTS:
                db.execute(statement)
            db.execute('PRAGMA user_version=2')


def test_fresh_archive_stays_v1_and_backup_header(project, tmp_path):
    with database(project) as db:
        assert storage.schema(db) == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='consolidation_jobs'").fetchone()
    path = tmp_path / 'before.ccmemory'
    backups.backup(project, path)
    assert path.read_bytes().startswith(b'CCMEMORY/1 ')


def test_migration_requires_new_verified_backup_and_is_explicit(project, tmp_path):
    path = tmp_path / 'before.ccmemory'
    result = storage.migrate(project, path)
    assert result['migrated'] and result['schema'] == 3
    assert result['backup_sha256'] == path.read_bytes().split(b'\n', 1)[0].split(b' ')[1].decode()
    assert path.read_bytes().startswith(b'CCMEMORY/1 ')
    with database(project) as db:
        assert storage.schema(db) == 3
        assert set(storage.TABLES + storage.V3_TABLES).issubset({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
    assert service.status(project)['schema_version'] == 1
    assert storage.migrate(project, path) == {'migrated': False, 'schema': 3}
    assert not (tmp_path / 'unused.ccmemory').exists()


def test_v2_to_v3_uses_one_verified_v2_backup(project, tmp_path):
    legacy_v2(project)
    with database(project) as db:
        assert storage.schema(db) == 2
    output = tmp_path / 'before-v3.ccmemory'
    result = storage.migrate(project, output)
    assert result['migrated'] and result['schema'] == 3
    assert output.read_bytes().startswith(b'CCMEMORY/2 ')
    with database(project) as db:
        assert storage.schema(db) == 3
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='consolidation_attempts'").fetchone()


def test_intervening_cache_write_blocks_migration(project, tmp_path, monkeypatch):
    original = backups.backup

    def changed(root, path):
        result = original(root, path)
        with database(root) as db:
            db.execute("INSERT INTO chunks(id,source,revision,line,end_line,text) VALUES('new','s','r',1,1,'cache')")
            db.commit()
        return result

    monkeypatch.setattr(backups, 'backup', changed)
    with pytest.raises(KnowledgeError, match='knowledge_changed_since_backup'):
        storage.migrate(project, tmp_path / 'changed.ccmemory')
    with database(project) as db:
        assert storage.schema(db) == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='consolidation_jobs'").fetchone()


def test_corrupt_new_backup_blocks_migration(project, tmp_path, monkeypatch):
    original = backups.backup

    def corrupted(root, path):
        result = original(root, path)
        path.write_bytes(path.read_bytes() + b'x')
        return result

    monkeypatch.setattr(backups, 'backup', corrupted)
    with pytest.raises(KnowledgeError, match='invalid_backup_json'):
        storage.migrate(project, tmp_path / 'corrupt.ccmemory')
    with database(project) as db:
        assert storage.schema(db) == 1


def test_migration_ddl_failure_rolls_back(project, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DDL_STATEMENTS', storage.DDL_STATEMENTS + ('CREATE TABLE meta(x)',))
    with pytest.raises(sqlite3.OperationalError):
        storage.migrate(project, tmp_path / 'failure.ccmemory')
    with database(project) as db:
        assert storage.schema(db) == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='consolidation_jobs'").fetchone()


def test_v2_roundtrip_marks_candidates_stale_and_rejects_bad_metadata(project, tmp_path):
    legacy_v2(project)
    with database(project) as db:
        source = db.execute('SELECT * FROM sources WHERE deleted=0 ORDER BY path LIMIT 1').fetchone()
        anchor = {'source': source['id'], 'title': source['title'], 'path': source['path'],
                  'revision': source['revision'], 'line': 1, 'end_line': 1,
                  'excerpt': source['body'].splitlines()[0]}
        dependency = {key: anchor[key] for key in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt')}
        dependency['citation'] = 'S1'
        manifest = {'title': 'review', 'sources': [anchor], 'count': 1, 'total': 1,
                    'deferred': 0, 'before': {'proposal': None}}
        db.execute("INSERT INTO consolidation_jobs VALUES(?,?,?,?,?,?,?,?)",
                   ('job', '2026-09-28', '2026-09-28', 'ready', json.dumps(binding(db, project)),
                    json.dumps(manifest), None, None))
        db.execute("INSERT INTO consolidation_inputs VALUES(?,?,?)",
                   ('job', source['id'], source['revision']))
        db.execute("INSERT INTO consolidation_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ('proposal', 'job', 0, 'lesson', 'overview/key', None, 'title', 'body [S1]',
                    json.dumps([dependency]), 'reason', 'pending', '2026-09-28', None))
        db.execute("INSERT INTO memory_claims VALUES(?,?,?,?,?,?,?,?,?)",
                   (claim_id('overview/key'), 'overview/key', 'lesson', 'title', 'body [S1]',
                    'rev', json.dumps([dependency]), 'approved', '2026-09-28'))
        db.commit()
    path = tmp_path / 'v2.ccmemory'
    backups.backup(project, path)
    assert path.read_bytes().startswith(b'CCMEMORY/2 ')
    target = tmp_path / 'target'
    target.mkdir()
    backups.restore(target, path)
    with database(target) as db:
        assert storage.schema(db) == 2
        assert db.execute('SELECT state FROM consolidation_jobs').fetchone()[0] == 'stale'
        assert db.execute('SELECT status FROM consolidation_proposals').fetchone()[0] == 'stale'
        assert db.execute('SELECT state FROM memory_claims').fetchone()[0] == 'stale'
        assert db.execute("SELECT json_extract(value,'$.automatic') FROM meta WHERE key='policy'").fetchone()[0] == 0
    raw = json.loads(path.read_bytes().split(b'\n', 1)[1])
    raw['tables']['consolidation_jobs']['rows'][0][4] = '{bad'
    payload = json.dumps(raw).encode()
    bad = tmp_path / 'bad.ccmemory'
    bad.write_bytes(b'CCMEMORY/2 ' + hashlib.sha256(payload).hexdigest().encode() + b'\n' + payload)
    empty = tmp_path / 'empty'
    empty.mkdir()
    with pytest.raises(KnowledgeError, match='invalid_consolidation_storage'):
        backups.restore(empty, bad)
    assert not (empty / '.controlcoding').exists()


@pytest.mark.parametrize('corruption', ('manifest', 'count', 'target', 'orphan', 'receipt', 'event'))
def test_v2_semantic_corruption_rejected_before_target_creation(project, tmp_path, corruption):
    legacy_v2(project)
    with database(project) as db:
        source = db.execute('SELECT * FROM sources WHERE deleted=0 LIMIT 1').fetchone()
        anchor = {'source': source['id'], 'title': source['title'], 'path': source['path'],
                  'revision': source['revision'], 'line': 1, 'end_line': 1,
                  'excerpt': source['body'].splitlines()[0]}
        db.execute('INSERT INTO consolidation_jobs VALUES(?,?,?,?,?,?,?,?)',
                   ('job', '2026-09-28', '2026-09-28', 'ready', json.dumps(binding(db, project)),
                    json.dumps({'title': 'review', 'sources': [anchor], 'count': 1,
                                'total': 1, 'deferred': 0, 'before': {}}), None, None))
        db.execute('INSERT INTO consolidation_inputs VALUES(?,?,?)', ('job', source['id'], source['revision']))
        db.commit()
    path = tmp_path / 'valid.ccmemory'
    backups.backup(project, path)
    raw = json.loads(path.read_bytes().split(b'\n', 1)[1])
    tables = raw['tables']
    job = tables['consolidation_jobs']['rows'][0]
    if corruption in ('manifest', 'count'):
        manifest = json.loads(job[5])
        if corruption == 'manifest':
            del manifest['title']
        else:
            manifest['count'] = 2
        job[5] = json.dumps(manifest)
    elif corruption == 'target':
        tables['consolidation_proposals']['rows'].append(
            ['proposal', 'job', 0, 'lesson', 'badtarget', None, 'title', 'body [S1]',
             json.dumps([{k: v for k, v in {**json.loads(job[5])['sources'][0], 'citation': 'S1'}.items()
                          if k != 'title'}]), 'reason', 'pending', '2026-09-28', None])
    elif corruption == 'orphan':
        tables['consolidation_inputs']['rows'].append(['missing', 'source', 'revision'])
    elif corruption == 'receipt':
        job[7] = json.dumps({'patches': []})
    else:
        tables['consolidation_events']['rows'].append([1, 'job', 'accept', json.dumps({'operation': {}}), '2026-09-28'])
    payload = json.dumps(raw).encode()
    bad = tmp_path / 'bad.ccmemory'
    bad.write_bytes(b'CCMEMORY/2 ' + hashlib.sha256(payload).hexdigest().encode() + b'\n' + payload)
    target = tmp_path / 'target'
    target.mkdir()
    with pytest.raises(KnowledgeError, match='invalid_consolidation_storage'):
        backups.restore(target, bad)
    assert not (target / '.controlcoding').exists()


def test_v2_oversized_json_rejected(project, tmp_path):
    legacy_v2(project)
    with database(project) as db:
        db.execute("INSERT INTO consolidation_jobs VALUES(?,?,?,?,?,?,?,?)",
                   ('job', 'now', 'now', 'ready', json.dumps({'x': 'a' * (storage.MAX_JSON_BYTES + 1)}), '{}', None, None))
        db.commit()
    with pytest.raises(KnowledgeError, match='consolidation_storage_limit'):
        backups.backup(project, tmp_path / 'bad.ccmemory')


def test_published_cumulative_receipt_and_events_roundtrip(project, tmp_path):
    legacy_v2(project)
    view = consolidation.dispatch(project, 'consolidation-create', {'title': 'Review'})
    citation = next(source for source in view['sources'] if source['path'] == 'docs/design.md')
    for key, page in (('first', 'overview'), ('second', 'timeline')):
        view = consolidation.dispatch(project, 'consolidation-propose', {
            'job': view['job']['id'], 'page_type': page, 'key': key, 'title': 'Summary',
            'body': 'A passphrase is required [S1].', 'kind': 'summary',
            'citations': [citation], 'reason': 'Source-supported'})
    first, second = [proposal['id'] for proposal in view['job']['proposals']]
    consolidation.dispatch(project, 'consolidation-decide', {
        'job': view['job']['id'], 'ids': [first], 'decision': 'accept', 'request_id': 'request-1'})
    consolidation.dispatch(project, 'consolidation-decide', {
        'job': view['job']['id'], 'ids': [second], 'decision': 'accept', 'request_id': 'request-2'})
    path = tmp_path / 'published.ccmemory'
    backups.backup(project, path)
    target = tmp_path / 'restored'
    target.mkdir()
    backups.restore(target, path)
    with database(target) as db:
        assert storage.schema(db) == 2
        assert db.execute('SELECT count(*) FROM consolidation_events').fetchone()[0] == 2
        receipt = json.loads(db.execute('SELECT receipt FROM consolidation_jobs').fetchone()[0])
        assert receipt['cumulative'] and len(receipt['patches']) == 2
