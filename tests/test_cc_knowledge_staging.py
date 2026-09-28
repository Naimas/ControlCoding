"""Checkpoint recovery, current-source publication and transcript privacy."""
import json

import pytest

from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib import knowledge_staging as stage
from cc_memory_lib.knowledge_store import database, KnowledgeError
from cc_memory_lib.knowledge_backup import backup


def changed_files(project):
    (project / 'README.md').write_text('# Updated home\nnewrootword', encoding='utf-8')
    (project / 'docs/design.md').write_text('# Updated design\nnewdesignword', encoding='utf-8')


def interrupt(project, monkeypatch):
    original = service.passages
    seen = []

    def fail(source):
        seen.append(source['path'])
        if len(seen) == 2:
            raise KeyboardInterrupt('simulated process interruption')
        yield from original(source)

    monkeypatch.setattr(service, 'passages', fail)
    with pytest.raises(KeyboardInterrupt):
        service.reconcile(project)
    monkeypatch.setattr(service, 'passages', original)
    return seen, original


def test_checkpoint_survives_interruption_and_matching_retry_reuses_it(project, monkeypatch):
    before = service.catalog(project)
    generation = service.status(project)['generation']
    changed_files(project)
    seen, original = interrupt(project, monkeypatch)
    state = service.status(project)
    assert state['ingestion']['prepared'] == 1 and state['ingestion']['total'] == 2
    assert state['generation'] == generation and state['needs_reconcile']
    assert service.catalog(project) == before
    with pytest.raises(KnowledgeError, match='reconcile_required'):
        service.query(project, 'passphrase', False)
    resumed = []

    def track(source):
        resumed.append(source['path'])
        yield from original(source)

    monkeypatch.setattr(service, 'passages', track)
    state = service.reconcile(project)
    assert resumed == seen[1:]
    assert state['generation'] == generation + 1 and state['ingestion'] is None
    assert service.query(project, 'newrootword', False)['citations']
    assert service.query(project, 'newdesignword', False)['citations']


@pytest.mark.parametrize('mutation', ['edit', 'rename', 'delete', 'head'])
def test_retry_invalidates_checkpoint_when_inputs_change(project, monkeypatch, mutation):
    changed_files(project)
    seen, original = interrupt(project, monkeypatch)
    if mutation == 'edit':
        (project / seen[0]).write_text('# Different\nreplacementword', encoding='utf-8')
    elif mutation == 'rename':
        (project / seen[0]).rename(project / 'renamed.md')
    elif mutation == 'delete':
        (project / seen[0]).unlink()
    else:
        monkeypatch.setattr(service, 'head', lambda root: 'f' * 40)
    prepared = []

    def track(source):
        prepared.append(source['path'])
        yield from original(source)

    monkeypatch.setattr(service, 'passages', track)
    service.reconcile(project)
    assert len(prepared) == (1 if mutation == 'delete' else 2)
    assert service.status(project)['ingestion'] is None


def test_source_change_during_preparation_never_publishes(project, monkeypatch):
    before = service.catalog(project)
    changed_files(project)
    original = service.passages

    def edit_after_read(source):
        yield from original(source)
        if source['path'] == 'README.md':
            (project / 'README.md').write_text('# Changed during preparation', encoding='utf-8')

    monkeypatch.setattr(service, 'passages', edit_after_read)
    with pytest.raises(KnowledgeError, match='changed_input'):
        service.reconcile(project)
    assert service.catalog(project) == before
    assert service.status(project)['needs_reconcile']


def test_failed_publication_rolls_back_but_preparation_can_resume(project, monkeypatch):
    before = service.catalog(project)
    changed_files(project)
    compiler = service.compile_wiki

    def fail(*args):
        raise RuntimeError('publication failed')

    monkeypatch.setattr(service, 'compile_wiki', fail)
    with pytest.raises(RuntimeError):
        service.reconcile(project)
    assert service.catalog(project) == before
    assert service.status(project)['ingestion']['prepared'] == 2
    monkeypatch.setattr(service, 'compile_wiki', compiler)

    def unnecessary(source):
        raise AssertionError('matching checkpoint should be reused')

    monkeypatch.setattr(service, 'passages', unnecessary)
    assert not service.reconcile(project)['needs_reconcile']


def test_staging_is_excluded_from_backups_and_purged_on_forget(project, monkeypatch, tmp_path):
    service.conversation(project, record())
    changed_files(project)
    compiler = service.compile_wiki
    monkeypatch.setattr(service, 'compile_wiki', lambda *args: (_ for _ in ()).throw(RuntimeError('interrupt')))
    with pytest.raises(RuntimeError):
        service.reconcile(project)
    with database(project) as db:
        assert 'Private apricot phrase' in '\n'.join(r[0] for r in db.execute("SELECT value FROM meta WHERE key GLOB 'ingest-stage/*'"))
    archive = tmp_path / 'pending.ccmemory'
    backup(project, archive)
    payload = json.loads(archive.read_bytes().split(b'\n', 1)[1])
    assert not any(row[0].startswith(stage.PREFIX) for row in payload['tables']['meta']['rows'])
    service.dispatch(project, 'forget', 'chat-1')
    with database(project) as db:
        assert not db.execute("SELECT 1 FROM meta WHERE key GLOB 'ingest-stage/*'").fetchone()
    monkeypatch.setattr(service, 'compile_wiki', compiler)
    service.reconcile(project)
    assert not service.query(project, 'apricot', False)['citations']


def test_policy_change_discards_pending_preparation(project, monkeypatch):
    changed_files(project)
    interrupt(project, monkeypatch)
    service.configure(project, {**service.DEFAULT, 'scopes': ['work']})
    assert service.status(project)['ingestion'] is None
    assert service.reconcile(project)['counts']['sources'] == 0


def test_checkpoint_survives_abrupt_child_process_exit(project):
    import subprocess
    import sys
    from pathlib import Path
    changed_files(project)
    script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from cc_memory_lib import knowledge_service as service
original = service.passages
count = 0
def terminate(source):
    global count
    count += 1
    if count == 2:
        os._exit(17)
    yield from original(source)
service.passages = terminate
service.reconcile(Path(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                             str(Path(__file__).resolve().parents[1] / 'scripts'), str(project)],
                            capture_output=True, timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 17, result.stderr
    assert service.status(project)['ingestion']['prepared'] == 1
    assert service.reconcile(project)['ingestion'] is None
    assert service.query(project, 'newdesignword', False)['citations']
