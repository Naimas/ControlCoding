"""Durable batches bound retry loss without exposing incomplete evidence."""
import pytest
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service, knowledge_scan as scan
from cc_memory_lib.knowledge_checkpoints import Writer
from cc_memory_lib.knowledge_store import database, get


def corpus(project):
    for number in range(40):
        (project / 'docs' / f'batch-{number:02}.md').write_text(f'# Batch {number}\nEvidence {number}', encoding='utf-8')


def test_preparation_interruption_keeps_committed_batch_and_retries_tail(project, monkeypatch):
    corpus(project)
    before = service.catalog(project)
    original = service.passages
    count = 0
    def interrupted(source):
        nonlocal count
        count += 1
        if count == 35:
            raise KeyboardInterrupt()
        yield from original(source)
    monkeypatch.setattr(service, 'passages', interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.reconcile(project)
    assert service.status(project)['ingestion']['prepared'] == 33
    assert service.catalog(project) == before
    retried = []
    def resume(source):
        retried.append(source['id'])
        yield from original(source)
    monkeypatch.setattr(service, 'passages', resume)
    service.reconcile(project)
    assert len(retried) == 7 and service.status(project)['counts']['sources'] == 42


def test_acquisition_interruption_keeps_only_committed_progress(project, monkeypatch):
    corpus(project)
    original = scan._Snapshots.observe
    count = 0
    def interrupted(self, path, observations, **kwargs):
        nonlocal count
        if not kwargs.get('directory'):
            count += 1
            if count == 35:
                raise KeyboardInterrupt()
        return original(self, path, observations, **kwargs)
    monkeypatch.setattr(scan._Snapshots, 'observe', interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.reconcile(project)
    assert service.status(project)['acquisition'] == {'captured': 33, 'total': 42}
    monkeypatch.setattr(scan._Snapshots, 'observe', original)
    assert service.reconcile(project)['counts']['sources'] == 42


def test_progress_and_rows_roll_back_together_on_write_failure(project, monkeypatch):
    import cc_memory_lib.knowledge_checkpoints as checkpoints
    with database(project) as db:
        writer = Writer(db, 'test-progress')
        writer.add('test-a', {'value': 'a'}, {'captured': 1})
        writer.add('test-b', {'value': 'b'}, {'captured': 2})
        original = checkpoints.put
        def fail(db, key, value):
            if key == 'test-b':
                raise RuntimeError('simulated write failure')
            original(db, key, value)
        monkeypatch.setattr(checkpoints, 'put', fail)
        with pytest.raises(RuntimeError):
            writer.flush()
        assert get(db, 'test-a') is None and get(db, 'test-progress') is None


def test_abrupt_process_exit_keeps_only_complete_batch(project):
    import subprocess
    import sys
    from pathlib import Path
    corpus(project)
    before = service.catalog(project)
    script = '''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from cc_memory_lib import knowledge_service as service
original=service.passages
count=0
def terminate(source):
 global count
 count+=1
 if count==35: os._exit(17)
 yield from original(source)
service.passages=terminate
service.reconcile(Path(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                             str(Path(__file__).resolve().parents[1] / 'scripts'), str(project)],
                            capture_output=True, timeout=30)
    assert result.returncode == 17
    assert service.status(project)['ingestion']['prepared'] == 33
    assert service.catalog(project) == before
    assert service.reconcile(project)['counts']['sources'] == 42
