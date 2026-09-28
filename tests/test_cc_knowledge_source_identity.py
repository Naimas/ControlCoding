"""Rename identity must survive later scans and reuse of the old pathname."""
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service


def row(project, path):
    return next(s for s in service.catalog(project)['sources'] if s['path'] == path)


def test_renamed_source_keeps_identity_notes_and_generation_on_next_scan(project):
    original = row(project, 'docs/design.md')
    service.notes(project, original['id'], 'Owner note follows the document')
    (project / 'docs/design.md').rename(project / 'docs/moved.md')
    first = service.reconcile(project)
    assert row(project, 'docs/moved.md')['id'] == original['id']
    second = service.reconcile(project)
    assert row(project, 'docs/moved.md')['id'] == original['id']
    assert second['generation'] == first['generation']
    assert service.page(project, original['id'])['notes'] == 'Owner note follows the document'


def test_reusing_old_path_does_not_steal_moved_source_identity(project):
    original = row(project, 'docs/design.md')
    (project / 'docs/design.md').rename(project / 'docs/moved.md')
    service.reconcile(project)
    (project / 'docs/design.md').write_text('# New independent document\nDifferent evidence.', encoding='utf-8')
    service.reconcile(project)
    moved, new = row(project, 'docs/moved.md'), row(project, 'docs/design.md')
    assert moved['id'] == original['id'] and new['id'] != original['id']
    service.reconcile(project)
    assert row(project, 'docs/design.md')['id'] == new['id']


def test_ambiguous_duplicate_content_does_not_merge_identities(project):
    original = row(project, 'docs/design.md')
    raw = (project / 'docs/design.md').read_bytes()
    (project / 'docs/design.md').unlink()
    (project / 'docs/copy-one.md').write_bytes(raw)
    (project / 'docs/copy-two.md').write_bytes(raw)
    service.reconcile(project)
    copies = [row(project, 'docs/copy-one.md'), row(project, 'docs/copy-two.md')]
    assert len({r['id'] for r in copies}) == 2
    assert all(r['id'] != original['id'] for r in copies)


def test_second_move_and_edit_keep_the_same_owner_notes(project):
    original = row(project, 'docs/design.md')
    service.notes(project, original['id'], 'Persistent owner note')
    for name in ['first.md', 'second.md']:
        current = row(project, 'docs/design.md' if name == 'first.md' else 'docs/first.md')
        (project / current['path']).rename(project / 'docs' / name)
        service.reconcile(project)
        service.reconcile(project)
        assert row(project, 'docs/' + name)['id'] == original['id']
    (project / 'docs/second.md').write_text('# Edited after two moves\nNew evidence.', encoding='utf-8')
    service.reconcile(project)
    assert row(project, 'docs/second.md')['id'] == original['id']
    assert service.page(project, original['id'])['notes'] == 'Persistent owner note'
