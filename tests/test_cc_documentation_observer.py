"""Documentation observation is separate from initialized project memory."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_documentation_observer import DocumentationError, observe, references
from cc_panel_bridge import dispatch


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return target


def test_real_documents_without_memory_initialization(tmp_path):
    write(tmp_path, 'README.md', '# Product\n[Architecture](docs/design.md)\n[External](https://example.org/private)')
    write(tmp_path, 'docs/design.md', '# Architecture\nActual design')
    write(tmp_path, '.env', 'SECRET_CANARY')
    write(tmp_path, 'AGENTS.md', 'SECRET_CANARY')
    write(tmp_path, '_work/plans/phase.md', '# Local plan')
    before = {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    result = observe(str(tmp_path), 'project')['documentation']
    assert len(result['documents']) == 2 and len(result['edges']) == 1
    assert 'SECRET_CANARY' not in json.dumps(result)
    assert not (tmp_path / '.controlwork').exists()
    assert before == {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}


def test_local_plans_and_handoffs_are_explicit_disjoint_scopes(tmp_path):
    write(tmp_path, '_work/plans/phase.md', '# Plan')
    write(tmp_path, '_work/handoff/session.md', '# Handoff, not a chat record')
    assert observe(str(tmp_path), 'project')['documentation']['documents'] == []
    plans = observe(str(tmp_path), 'plans')['documentation']['documents']
    handoffs = observe(str(tmp_path), 'handoffs')['documentation']['documents']
    assert len(plans) == len(handoffs) == 1
    assert plans[0]['area'] == 'plans' and handoffs[0]['area'] == 'archive'
    assert 'sessions' not in observe(str(tmp_path), 'handoffs')['documentation']


def test_updates_additions_deletes_change_snapshot_but_preserve_path_ids(tmp_path):
    p = write(tmp_path, 'README.md', '# First')
    first = observe(str(tmp_path), 'project')['documentation']
    p.write_text('# Changed', encoding='utf-8')
    write(tmp_path, 'docs/new.md', '# Added')
    second = observe(str(tmp_path), 'project')['documentation']
    assert second['snapshot_id'] != first['snapshot_id']
    assert second['documents'][0]['id'] == first['documents'][0]['id']
    p.unlink()
    assert len(observe(str(tmp_path), 'project')['documentation']['documents']) == 1


def test_unresolved_references_are_not_followed_or_declared_broken(tmp_path):
    write(tmp_path, 'README.md', '# Root\n[Unknown](outside.md) [Code](src/main.py) [Private](../secret.md)')
    result = observe(str(tmp_path), 'project')['documentation']
    assert result['unresolved_references'] == 2 and result['edges'] == []
    assert 'not broken' in result['notice']


def test_link_subset_excludes_code_external_images_and_traversal():
    text = '[a](../README.md#title) [b](<topic%20name.md>) ![image](image.md) [url](https://example.com/a.md) ` [code](ignored.md) `\n```md\n[example](example.md)\n```\n[unsafe](../../../private.md)'
    assert list(references(text, 'docs/a.md')) == ['README.md', 'docs/topic name.md']


@pytest.mark.parametrize('scope', ['../../private', '', None, {}, 1])
def test_arbitrary_scope_rejected(tmp_path, scope):
    with pytest.raises(DocumentationError, match='invalid_document_scope'):
        observe(str(tmp_path), scope)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory'])
def test_linked_sources_and_directories_are_rejected(tmp_path, kind):
    outside = write(tmp_path, 'private.txt', 'SECRET_CANARY')
    try:
        if kind == 'hardlink':
            os.link(outside, tmp_path / 'README.md')
        elif kind == 'symlink':
            (tmp_path / 'README.md').symlink_to(outside)
        else:
            (tmp_path / 'docs').symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink privilege unavailable')
    with pytest.raises(DocumentationError, match='unsupported_path'):
        observe(str(tmp_path), 'project')


@pytest.mark.parametrize('kind', ['size', 'count', 'utf8'])
def test_bad_or_excessive_corpus_never_returns_partial_success(tmp_path, kind):
    if kind == 'size':
        write(tmp_path, 'README.md', 'x' * 262145)
    elif kind == 'count':
        for i in range(201):
            write(tmp_path, f'docs/{i}.md', '# Text')
    else:
        (tmp_path / 'README.md').write_bytes(b'\xff')
    with pytest.raises(DocumentationError, match='invalid_documentation' if kind == 'utf8' else 'documentation_limit'):
        observe(str(tmp_path), 'project')


def test_membership_change_during_read_is_rejected(tmp_path, monkeypatch):
    from cc_setup_service import _Snapshots
    write(tmp_path, 'README.md', '# Text')
    original = _Snapshots.recheck

    def changed(self):
        write(tmp_path, 'new.md', '# New')
        original(self)

    monkeypatch.setattr(_Snapshots, 'recheck', changed)
    with pytest.raises(DocumentationError, match='changed_input'):
        observe(str(tmp_path), 'project')


def test_bridge_rejects_extra_paths_and_supports_exact_read_request(tmp_path):
    base = {'version': 1, 'id': 'test', 'operation': 'documentation_read_v1', 'project_root': str(tmp_path), 'scope': 'project'}
    assert dispatch({**base, 'path': '../private'})['status'] == 'error'
    assert dispatch({**base, 'scope': '../private'})['status'] == 'error'
    assert dispatch(base)['result']['documentation']['documents'] == []
