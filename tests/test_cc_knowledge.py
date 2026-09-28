"""Real service transactions, source invalidation, privacy and semantic routing."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import database, KnowledgeError


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'docs').mkdir()
    (root / 'README.md').write_text('# Welcome\nSee [design](docs/design.md).\n', encoding='utf-8')
    (root / 'docs/design.md').write_text('# Authentication\nPeople enter with a secret passphrase.\n', encoding='utf-8')
    service.configure(root, service.DEFAULT.copy())
    service.reconcile(root)
    return root


def test_absent_status_never_initializes(tmp_path):
    assert not service.status(tmp_path)['enabled']
    assert not (tmp_path / '.controlcoding').exists()


def test_incremental_and_atomic_wiki(project):
    original = service.catalog(project)
    state = service.reconcile(project)
    assert state['generation'] == 1
    assert state['last_change']['changed'] == []
    identifier = next(s['id'] for s in original['sources'] if s['path'] == 'docs/design.md')
    service.notes(project, identifier, 'Human observation')
    (project / 'docs/design.md').write_text('# Authentication\nA token replaces the passphrase.\n', encoding='utf-8')
    state = service.reconcile(project)
    assert state['generation'] == 2
    assert state['last_change']['changed'] == ['docs/design.md']
    page = service.page(project, identifier)
    assert 'token' in page['body'] and page['notes'] == 'Human observation'
    assert len(page['history']) == 2
    assert service.query(project, 'token', False)['citations'][0]['revision'] == page['dependencies'][0]['revision']
    (project / 'docs/design.md').unlink()
    service.reconcile(project)
    assert service.status(project)['counts']['edges'] == 0
    assert not service.query(project, 'token', False)['citations']
    with pytest.raises(KnowledgeError, match='page_unavailable'):
        service.page(project, identifier)


def test_source_failure_keeps_generation_and_retry(project, monkeypatch):
    before = service.catalog(project)
    def fail(*args):
        raise KnowledgeError('changed_input')
    monkeypatch.setattr(service, 'capture', fail)
    with pytest.raises(KnowledgeError):
        service.reconcile(project)
    assert service.catalog(project) == before
    assert service.status(project)['jobs'][0]['state'] == 'failed'


def test_interrupted_transaction_rolls_back(project):
    with pytest.raises(RuntimeError):
        with database(project) as db:
            db.execute("DELETE FROM chunks")
            raise RuntimeError('interruption')
    assert service.status(project)['counts']['chunks'] == 2


def test_writer_exclusion(project):
    with database(project):
        with pytest.raises(KnowledgeError, match='knowledge_busy'):
            with database(project):
                pass


def record(retention='transcript'):
    return {'id': 'chat-1', 'title': 'Project decisions', 'retention': retention,
            'summary': 'Continue authentication design.', 'status': 'active',
            'turns': [{'id': 'turn-1', 'sequence': 0, 'role': 'user', 'content': 'Private apricot phrase',
                       'provenance': {'model': 'test', 'origin': 'manual'}}]}


def test_conversation_idempotence_continuity_and_privacy(project):
    value = record()
    service.conversation(project, value)
    service.conversation(project, value)
    assert len(service.read_conversation(project, 'chat-1')['turns']) == 1
    service.reconcile(project)
    assert service.query(project, 'apricot', False)['citations']
    value['turns'][0]['content'] = 'conflicting replay'
    with pytest.raises(KnowledgeError, match='conversation_event_conflict'):
        service.conversation(project, value)
    assert service.read_conversation(project, 'chat-1')['turns'][0]['content'] == 'Private apricot phrase'
    service.conversation(project, record('summary'))
    service.reconcile(project)
    assert not service.read_conversation(project, 'chat-1')['turns']
    assert not service.query(project, 'apricot', False)['citations']
    service.dispatch(project, 'forget', 'chat-1')
    service.reconcile(project)
    assert service.status(project)['counts']['conversations'] == 0


class NeuralFixture:
    """Deterministic transport fixture; real neural quality is evaluated separately."""
    def pin(self):
        return 'test@fixed-model'

    def embed(self, texts):
        return [[1., 0.] if 'passphrase' in text or 'login' in text else [0., 1.] for text in texts]


def test_semantic_recall_graph_and_model_invalidation(project):
    adapter = NeuralFixture()
    service.index(project, adapter)
    result = service.query(project, 'login', True, adapter)
    assert result['mode'].startswith('neural')
    assert result['citations'][0]['path'] == 'docs/design.md'
    assert result['edges'][0]['kind'] == 'explicit_markdown_reference'
    # Original graph neighbors are candidates, with the traversal made explicit.
    assert 'README.md' in {c['path'] for c in result['citations']}
    assert any(item['signal'] == 'graph' for item in result['retrieval']['selected'])
    adapter.pin = lambda: 'test@different-model'
    result = service.query(project, 'passphrase', True, adapter)
    assert 'not_current' in result['warning']
    assert not result['mode'].startswith('neural')


def test_model_failure_preserves_source_and_job_state(project):
    class Offline:
        def pin(self):
            raise KnowledgeError('embedding_unavailable')
    with pytest.raises(KnowledgeError):
        service.index(project, Offline())
    assert service.status(project)['embedding_error'] == 'embedding_unavailable'
    assert service.status(project)['counts']['sources'] == 2
    result = service.query(project, 'passphrase', True, Offline())
    assert result['citations'] and 'fallback' in result['warning']


def test_scope_removal_tombstones_sources(project):
    config = {**service.DEFAULT, 'scopes': ['work']}
    service.configure(project, config)
    with pytest.raises(KnowledgeError, match='reconcile_required'):
        service.query(project, 'passphrase', False)
    service.reconcile(project)
    assert service.status(project)['counts']['sources'] == 0
    assert service.status(project)['counts']['wiki'] == 0


@pytest.mark.parametrize('field,value', [('scopes', ['../']), ('embedding', 'bad model'), ('worker', 1), ('retention', 'all')])
def test_invalid_policy(tmp_path, field, value):
    with pytest.raises(KnowledgeError):
        service.configure(tmp_path, {**service.DEFAULT, field: value})
    assert not (tmp_path / '.controlcoding').exists()


def test_linked_database_refused(project, tmp_path):
    target = tmp_path / 'outside.db'
    target.write_bytes(b'sentinel')
    db = project / '.controlcoding/knowledge/knowledge.db'
    db.unlink()
    import os
    os.link(target, db)
    with pytest.raises(KnowledgeError, match='unsupported_path'):
        service.status(project)
    assert target.read_bytes() == b'sentinel'


def test_rename_preserves_identity_and_history(project):
    before = next(s for s in service.catalog(project)['sources'] if s['path'] == 'docs/design.md')
    (project / 'docs/design.md').rename(project / 'docs/identity.md')
    service.reconcile(project)
    after = next(s for s in service.catalog(project)['sources'] if s['path'] == 'docs/identity.md')
    assert before['id'] == after['id']
    assert len(service.page(project, after['id'])['history']) == 2


def test_repeated_long_lines_have_unique_passage_ids(project):
    (project / 'docs/long.md').write_text('# Long\n' + ('word ' * 1500), encoding='utf-8')
    service.reconcile(project)
    assert service.status(project)['counts']['chunks'] > 3


def test_dev_records_are_projected_readonly(project):
    from cc_memory_lib.migrations import migrate_schema
    from cc_memory_lib.knowledge_sources import digest
    directory = project / '.controlcoding/memory'
    directory.mkdir()
    path = directory / 'memory.db'
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    migrate_schema(db)
    db.execute("INSERT INTO entities(id,type,title,project_short,plane,lifecycle,body,created_at,updated_at) VALUES('decision:one','decision','Retry budget','sample','dev','draft','Retry three times.','now','now')")
    db.commit()
    db.close()
    original = path.read_bytes()
    service.configure(project, {**service.DEFAULT, 'scopes': ['project', 'dev-memory']})
    service.reconcile(project)
    item = next(s for s in service.catalog(project)['sources'] if s['kind'] == 'dev-record:draft')
    assert 'Retry three times' in service.page(project, item['id'])['body']
    assert path.read_bytes() == original
    assert sorted(p.name for p in directory.iterdir()) == ['memory.db']


def test_real_process_interruption_recovers(project, tmp_path):
    import subprocess
    import time
    marker = tmp_path / 'transaction-open'
    script = tmp_path / 'crash.py'
    source = str(Path(__file__).resolve().parents[1] / 'scripts')
    script.write_text('import sys,time\nfrom pathlib import Path\nsys.path.insert(0,' + repr(source) + ')\n'
                      'from cc_memory_lib.knowledge_store import database\n'
                      'with database(Path(' + repr(str(project)) + ')) as db:\n'
                      ' db.execute("DELETE FROM chunks")\n Path(' + repr(str(marker)) + ').write_text("ready")\n time.sleep(60)\n')
    process = subprocess.Popen([sys.executable, '-I', '-B', str(script)], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(.05)
        assert marker.exists()
    finally:
        process.kill()
        process.wait(timeout=5)
    assert service.status(project)['counts']['chunks'] == 2
    service.reconcile(project)
    assert service.status(project)['generation'] == 1


def test_commit_head_transition_is_observed_without_hooks(project):
    import subprocess
    def git(*args):
        return subprocess.run(['git', '-C', str(project), *args], check=True, capture_output=True,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.decode().strip()
    git('init')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'core.hooksPath=/dev/null', 'commit', '--allow-empty', '-m', 'fixture')
    state = service.reconcile(project, 'commit-ceremony')
    assert state['head'] == git('rev-parse', 'HEAD')
    assert state['last_change']['commit_changed']
    assert not service.reconcile(project)['last_change']['commit_changed']


def test_lexical_does_not_rank_substrings_as_words(project):
    assert not service.query(project, 'phrase', False)['citations']
    assert not service.query(project, 'le', False)['citations']


def test_worker_continues_without_ui_and_stops_on_policy(project):
    import subprocess
    import time
    cli = Path(__file__).resolve().parents[1] / 'scripts/cc_knowledge.py'
    config = {**service.DEFAULT, 'embedding': '', 'worker': True}
    service.configure(project, config)
    process = subprocess.Popen([sys.executable, '-I', '-B', str(cli), '--project-root', str(project), 'worker'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    def eventually(predicate, timeout=38):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if predicate():
                    return
            except KnowledgeError as exc:
                if str(exc) != 'knowledge_busy':
                    raise
            time.sleep(.2)
        raise AssertionError('worker deadline')
    try:
        eventually(lambda: service.status(project)['worker_heartbeat'] is not None, 10)
        (project / 'docs/design.md').write_text('# Authentication\nWorker observed edit.', encoding='utf-8')
        eventually(lambda: service.status(project)['generation'] >= 2)
        def disable():
            service.configure(project, {**config, 'worker': False})
            return True
        eventually(disable, 5)
        assert process.wait(timeout=38) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_late_embedding_cannot_restore_changed_passages(project):
    class ChangedSource(NeuralFixture):
        def embed(self, texts):
            (project / 'docs/design.md').write_text('# Authentication\nCompletely new design.', encoding='utf-8')
            service.reconcile(project)
            return super().embed(texts)
    result = service.index(project, ChangedSource())
    assert result['counts']['embedded'] == 1  # unchanged README only
    assert result['counts']['chunks'] == 2
    assert service.query(project, 'passphrase', False)['citations'] == []


def test_wiki_history_is_readable_and_marked_historical(project):
    source = next(s for s in service.catalog(project)['sources'] if s['path'] == 'docs/design.md')
    original = service.page(project, source['id'])
    (project / 'docs/design.md').write_text('# Authentication\nDifferent source.', encoding='utf-8')
    service.reconcile(project)
    older = service.dispatch(project, 'page-revision', {'id': source['id'], 'revision': original['revision']})
    assert older['historical'] and 'passphrase' in older['body']
    assert 'Different source' in service.page(project, source['id'])['body']
