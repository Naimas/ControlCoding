"""Explicit file following narrows extraction without rewriting source archives."""
import pytest
from test_cc_knowledge import project
from test_cc_knowledge_rich_refresh import docx
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError


def config(paths):
    return {**service.DEFAULT, 'embedding': '', 'scopes': ['rich-documents'], 'rich_paths': paths}


def test_only_selected_files_and_selection_removal(project):
    original = docx('First')
    (project / 'docs/one.docx').write_bytes(original)
    (project / 'docs/two.docx').write_bytes(docx('Second'))
    (project / 'docs/broken.pdf').write_bytes(b'cannot extract')
    service.configure(project, config(['docs/one.docx']))
    service.reconcile(project)
    first = service.catalog(project)['sources']
    assert [r['path'] for r in first] == ['docs/one.docx']
    service.configure(project, config(['docs/two.docx']))
    assert service.status(project)['needs_reconcile']
    service.reconcile(project)
    assert [r['path'] for r in service.catalog(project)['sources']] == ['docs/two.docx']
    assert (project / 'docs/one.docx').read_bytes() == original
    (project / 'docs/two.docx').unlink()
    service.reconcile(project)
    assert service.catalog(project)['sources'] == []


@pytest.mark.parametrize('paths', [[], 'docs/a.pdf', ['../a.pdf'], ['/a.pdf'], ['docs/../a.pdf'],
    ['docs/.secret/a.pdf'], ['other/a.pdf'], ['C:/a.pdf'], ['docs\\a.pdf'], ['docs/a.txt'],
    ['docs/a.pdf', 'docs/A.pdf'], ['docs/a.pdf\n'], ['docs/a?.pdf'], ['docs/a.pdf']*129])
def test_invalid_selection_is_rejected_before_mutation(project, paths):
    previous = service.status(project)['policy']
    with pytest.raises(KnowledgeError):
        service.configure(project, config(paths))
    assert service.status(project)['policy'] == previous


def test_legacy_policy_and_explicit_all_keep_compatibility(project):
    original = service.DEFAULT.copy()
    assert service.policy(original) == original
    (project / 'docs/a.docx').write_bytes(docx('Follow all'))
    service.configure(project, config(None))
    service.reconcile(project)
    assert service.catalog(project)['sources'][0]['path'] == 'docs/a.docx'


def test_backup_restore_preserves_selection_with_automatic_updates_off(project, tmp_path):
    from cc_memory_lib.knowledge_backup import backup, restore
    service.configure(project, config(['docs/a.docx']))
    archive = tmp_path / 'selected.ccmemory'
    backup(project, archive)
    target = tmp_path / 'restored'
    target.mkdir()
    restore(target, archive)
    saved = service.status(target)['policy']
    assert saved['rich_paths'] == ['docs/a.docx']
    assert not saved['automatic'] and not saved['worker']
