"""Logical backup round trips, empty-target policy and corrupt-input rejection."""
import hashlib
import json

import pytest

from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_backup import backup, restore
from cc_memory_lib.knowledge_store import KnowledgeError, database


def test_roundtrip_preserves_private_content_and_disables_automation(project, tmp_path):
    service.conversation(project, record())
    service.reconcile(project)
    identifier = service.catalog(project)['sources'][0]['id']
    service.notes(project, identifier, 'Retain owner notes')
    archive = tmp_path / 'private.ccmemory'
    result = backup(project, archive)
    assert result['saved'] and result['bytes'] == archive.stat().st_size
    target = tmp_path / 'recovered'
    target.mkdir()
    assert restore(target, archive)['requires_reconcile']
    state = service.status(target)
    assert state['needs_reconcile'] and not state['policy']['automatic'] and not state['policy']['worker']
    assert state['counts']['embedded'] == 0
    assert service.read_conversation(target, 'chat-1')['turns'][0]['content'] == 'Private apricot phrase'
    assert service.page(target, identifier)['notes'] == 'Retain owner notes'
    with pytest.raises(KnowledgeError, match='reconcile_required'):
        service.query(target, 'passphrase', False)
    service.reconcile(target)
    assert not any(c['path'] == 'docs/design.md' for c in service.query(target, 'passphrase', False)['citations'])
    assert service.query(target, 'apricot', False)['citations']


def test_backup_and_restore_never_overwrite(project, tmp_path):
    output = tmp_path / 'private.ccmemory'
    backup(project, output)
    original = output.read_bytes()
    with pytest.raises(KnowledgeError, match='backup_already_exists'):
        backup(project, output)
    assert output.read_bytes() == original
    state = service.status(project)
    with pytest.raises(KnowledgeError, match='restore_requires_empty_archive'):
        restore(project, output)
    assert service.status(project) == state
    with pytest.raises(KnowledgeError, match='backup_must_be_external'):
        backup(project, project / 'inside.ccmemory')
    assert not (project / 'inside.ccmemory').exists()


@pytest.mark.parametrize('corruption', ['hash', 'schema', 'columns', 'table', 'policy'])
def test_invalid_backup_has_no_target_side_effects(project, tmp_path, corruption):
    archive = tmp_path / 'private.ccmemory'
    backup(project, archive)
    header, raw = archive.read_bytes().split(b'\n', 1)
    value = json.loads(raw)
    if corruption == 'schema':
        value['schema'] = 99
    elif corruption == 'columns':
        value['tables']['sources']['columns'][0] = 'id); DROP TABLE meta;--'
    elif corruption == 'table':
        value['tables']['injected'] = {'columns': [], 'rows': []}
    elif corruption == 'policy':
        for row in value['tables']['meta']['rows']:
            if row[0] == 'policy':
                row[1] = '{}'
    raw = json.dumps(value).encode()
    if corruption != 'hash':
        header = b'CCMEMORY/1 ' + hashlib.sha256(raw).hexdigest().encode()
    archive.write_bytes(header + b'\n' + raw)
    target = tmp_path / 'new'
    target.mkdir()
    with pytest.raises((KnowledgeError, ValueError)):
        restore(target, archive)
    assert not (target / '.controlcoding').exists()


def test_backup_serializes_active_database_under_writer_lease(project, tmp_path):
    with database(project):
        with pytest.raises(KnowledgeError, match='knowledge_busy'):
            backup(project, tmp_path / 'busy.ccmemory')
    assert not (tmp_path / 'busy.ccmemory').exists()


def test_backup_omits_rebuildable_vectors_but_preserves_history(project, tmp_path):
    from test_cc_knowledge import NeuralFixture
    service.index(project, NeuralFixture())
    assert service.status(project)['counts']['embedded'] > 0
    with database(project) as db:
        expected = {table: [list(r) for r in db.execute('SELECT * FROM ' + table)]
                    for table in ('sources', 'revisions', 'wiki', 'wiki_history', 'conversations', 'turns')}
        cache = {table: {'columns': [r[1] for r in db.execute('PRAGMA table_info(' + table + ')')],
                         'rows': [list(r) for r in db.execute('SELECT * FROM ' + table)]}
                 for table in ('chunks', 'edges')}
    archive = tmp_path / 'compact.ccmemory'
    backup(project, archive)
    value = json.loads(archive.read_bytes().split(b'\n', 1)[1])
    for table in cache:
        assert value['tables'][table]['rows'] == []
        assert value['tables'][table]['columns'] == cache[table]['columns']
    for table, rows in expected.items():
        assert value['tables'][table]['rows'] == rows
    # Export does not delete the active index.
    assert service.status(project)['counts']['embedded'] > 0
    # Older archives containing vectors still restore; cache never becomes truth.
    from cc_memory_lib.knowledge_backup import encode
    for table, block in cache.items():
        value['tables'][table] = {**block, 'rows': [[encode(v) for v in row] for row in block['rows']]}
    raw = json.dumps(value).encode()
    archive.write_bytes(b'CCMEMORY/1 ' + hashlib.sha256(raw).hexdigest().encode() + b'\n' + raw)
    target = tmp_path / 'older-backup'
    target.mkdir()
    restore(target, archive)
    state = service.status(target)
    assert state['needs_reconcile'] and state['counts']['chunks'] == state['counts']['edges'] == 0
