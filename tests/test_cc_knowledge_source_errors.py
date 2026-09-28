import pytest
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service


def test_multiple_invalid_sources_are_diagnosed_and_recover_after_restart(project):
    before = service.catalog(project)
    for name in ('bad-a', 'bad-b'):
        (project / 'docs' / (name+'.md')).write_bytes(b'\xff')
    with pytest.raises(UnicodeError):
        service.reconcile(project)
    state = service.status(project)
    assert state['needs_reconcile'] and len(state['source_errors']) == 2
    assert all(row['state'] == 'failed' and row['attempts'] == 1 for row in state['source_errors'])
    assert service.catalog(project) == before
    for name in ('bad-a', 'bad-b'):
        (project / 'docs' / (name+'.md')).write_text('# Recovered\nvalid content', encoding='utf-8')
    result = service.dispatch(project, 'source-retry', 'docs/bad-a.md')
    assert not result['needs_reconcile'] and result['counts']['sources'] == 4
    assert all(row['state'] == 'resolved' for row in result['source_errors'])


def test_unknown_retry_does_not_read_arbitrary_path(project):
    with pytest.raises(ValueError, match='unknown_source_failure'):
        service.dispatch(project, 'source-retry', '../outside.md')
