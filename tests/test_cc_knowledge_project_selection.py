"""Explicit project documents preserve source boundaries and live revisions."""
import os
import pytest
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_sources import capture
from cc_memory_lib.knowledge_store import KnowledgeError


def config(paths):
    return {**service.DEFAULT, 'embedding': '', 'automatic': False,
            'scopes': ['project'], 'project_paths': paths}


def test_selected_paths_never_enumerate_unrelated_directories(project, monkeypatch):
    (project / 'docs/selected.md').write_text('# Current design\nVerified current source.')
    (project / 'docs/history').mkdir()
    (project / 'docs/history/broken.md').write_bytes(b'\xff')
    (project / 'module').mkdir()
    (project / 'module/README.md').write_text('# Module\n[Design](../docs/selected.md)')
    service.configure(project, config(['docs/selected.md', 'module/README.md']))
    def forbidden(*args, **kwargs):
        raise AssertionError('Selected mode must not enumerate any directory')
    with monkeypatch.context() as m:
        m.setattr(os, 'scandir', forbidden)
        service.reconcile(project)
    catalog = service.catalog(project)
    assert {row['path'] for row in catalog['sources']} == {'docs/selected.md', 'module/README.md'}
    assert len(catalog['edges']) == 1


def test_selected_edit_delete_restore_and_scope_change(project):
    source = project / 'docs/selected.md'
    source.write_text('# Design\nFirst version.')
    service.configure(project, config(['docs/selected.md']))
    service.reconcile(project)
    first = service.catalog(project)['sources'][0]
    source.write_text('# Design\nSecond version.')
    service.reconcile(project, 'source-change')
    updated = service.catalog(project)['sources'][0]
    assert updated['id'] == first['id'] and updated['revision'] != first['revision']
    source.unlink()
    service.reconcile(project)
    assert service.catalog(project)['sources'] == []
    source.write_text('# Design\nRestored version.')
    service.reconcile(project)
    assert len(service.catalog(project)['sources']) == 1
    service.configure(project, config(['not-yet-present.md']))
    assert service.status(project)['needs_reconcile']
    service.reconcile(project)
    assert service.catalog(project)['sources'] == [] and source.exists()


@pytest.mark.parametrize('paths', [[], 'docs/a.md', ['../a.md'], ['/a.md'],
    ['docs/../a.md'], ['docs/.secret/a.md'], ['C:/a.md'], ['docs\\a.md'],
    ['runtime/a.md'], ['AGENTS.md'], ['cc/.controlcoding/a.md'], ['docs/a.pdf'],
    ['docs/a.md', 'docs/A.md'], ['docs/a.md\n'], ['docs/a?.md'], ['docs/CON.md'],
    ['docs/a.md'] * 129])
def test_invalid_list_rejected_without_mutating_policy(project, paths):
    before = service.status(project)['policy']
    with pytest.raises(KnowledgeError):
        service.configure(project, config(paths))
    assert service.status(project)['policy'] == before


def test_selected_hardlink_is_rejected(project, tmp_path):
    original = tmp_path / 'private.md'
    original.write_text('Not an authorized ordinary source')
    os.link(original, project / 'docs/selected.md')
    with pytest.raises(KnowledgeError, match='unsupported_path'):
        capture(project, ['project'], project_paths=['docs/selected.md'])


def test_selected_parent_link_is_rejected(project, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'source.md').write_text('Outside source')
    try:
        (project / 'linked').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Directory symlink not available')
    with pytest.raises(KnowledgeError, match='unsupported_path'):
        capture(project, ['project'], project_paths=['linked/source.md'])


def test_contained_selection_does_not_add_unselected_cc_documents(project):
    from cc_layout import MARKER_BYTES
    (project / 'cc').mkdir()
    (project / 'cc/layout.json').write_bytes(MARKER_BYTES)
    (project / 'cc/CONTROLCODING.md').write_text('# Not selected')
    (project / 'cc/task.md').write_text('# Selected pilot task')
    rows = capture(project, ['project'], project_paths=['cc/task.md'])
    assert [row['path'] for row in rows] == ['cc/task.md']


def test_backup_keeps_selection_without_reactivating_worker(project, tmp_path):
    from cc_memory_lib.knowledge_backup import backup, restore
    service.configure(project, config(['docs/a.md']))
    archive = tmp_path / 'selected.ccmemory'
    backup(project, archive)
    target = tmp_path / 'restored'
    target.mkdir()
    restore(target, archive)
    saved = service.status(target)['policy']
    assert saved['project_paths'] == ['docs/a.md']
    assert not saved['automatic'] and not saved['worker']
