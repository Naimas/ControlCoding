"""Bounded descriptors and acquisition checkpoint reuse in isolated projects."""
import pytest
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib import knowledge_scan as scan
from cc_memory_lib.knowledge_store import database, KnowledgeError


def test_interrupted_acquisition_resumes_cached_file_and_revalidates_content(project, monkeypatch):
    (project / 'README.md').write_text('# Changed root\ncheckpointword', encoding='utf-8')
    (project / 'docs/design.md').write_text('# Changed design\nsecondword', encoding='utf-8')
    original = scan._Snapshots.observe
    observed = []

    def interrupted(self, path, observations, **kwargs):
        if not kwargs.get('directory'):
            observed.append(str(path))
            if len(observed) == 2:
                raise KeyboardInterrupt()
        return original(self, path, observations, **kwargs)

    monkeypatch.setattr(scan._Snapshots, 'observe', interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.reconcile(project)
    state = service.status(project)
    assert state['acquisition'] == {'captured': 1, 'total': 2}
    assert state['needs_reconcile']
    reads = []

    def resumed(self, path, observations, **kwargs):
        if not kwargs.get('directory'):
            reads.append(str(path))
        return original(self, path, observations, **kwargs)

    monkeypatch.setattr(scan._Snapshots, 'observe', resumed)
    result = service.reconcile(project)
    assert reads.count(observed[0]) == 1  # Final uncached publication check only.
    assert result['acquisition'] is None
    assert service.query(project, 'checkpointword', False)['citations']


def test_capture_closes_each_file_instead_of_retaining_corpus_handles(project, monkeypatch):
    for i in range(100):
        (project / 'docs' / f'{i}.md').write_text(f'# Record {i}', encoding='utf-8')
    original = scan._Snapshots.__exit__
    sizes = []

    def close(self, *args):
        sizes.append(len(self.entries))
        return original(self, *args)

    monkeypatch.setattr(scan._Snapshots, '__exit__', close)
    assert service.reconcile(project)['counts']['sources'] == 102
    assert max(sizes) < 32
    before = service.status(project)['generation']
    assert service.reconcile(project)['generation'] == before


def test_hardlinked_source_is_refused_before_checkpoint(project):
    import os
    os.link(project / 'README.md', project / 'docs/copy.md')
    with pytest.raises(KnowledgeError, match='unsupported_path'):
        service.reconcile(project)
