"""Reviewed choices and native persistence on disposable external fixtures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_project_map_definition as definition
import cc_project_map_sources as source


@pytest.fixture
def context(tmp_path):
    (tmp_path / 'one.py').write_text('def run(): return 1\n', encoding='utf-8')
    (tmp_path / 'two.py').write_text('def stop(): return 2\n', encoding='utf-8')
    request = {'project_root': str(tmp_path), 'project_id': 'fixture', 'observed_at': '2026-09-21T12:00:00Z', 'design_paths': []}
    preview = source.preview_project_map_scope(request)['preview_id']
    return request, preview


def observe(context):
    return definition.observe_reviewed(*context)


def node(observation, title):
    return next(n['id'] for n in observation['projection']['bundle']['nodes'] if n['title'] == title)


def prepare(context, change):
    review = observe(context)['review']
    args = (*context, review['source_snapshot'], review['revision'], change)
    return args, definition.preview_change(*args)[0]


def save(context, change):
    if os.name != 'nt':
        pytest.skip('Native mandatory sharing exclusion is Windows-only')
    args, preview = prepare(context, change)
    return definition.commit_change(*args, preview['approval_id'])


def fails(code, call):
    with pytest.raises(definition.DefinitionError) as caught:
        call()
    assert caught.value.code == code


def test_preview_reads_no_writes_and_deterministic_diff(context, tmp_path):
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    observed = observe(context)
    args, preview = prepare(context, {'operation': 'accept', 'target': node(observed, 'one.py')})
    assert preview == definition.preview_change(*args)[0]
    assert preview['changes'][0]['before'] is None
    assert preview['changes'][0]['after']['decision'] == 'confirmed'
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert not (tmp_path / '.controlcoding').exists()


def test_accept_rename_reject_refresh_survive_without_completion(context, tmp_path):
    original = observe(context)
    save(context, {'operation': 'accept', 'target': node(original, 'one.py')})
    accepted = observe(context)
    stable = node(accepted, 'one.py')
    assert stable.startswith('map:')
    save(context, {'operation': 'rename', 'target': stable, 'title': 'API façade'})
    save(context, {'operation': 'reject', 'target': node(observe(context), 'two.py')})
    latest = observe(context)
    assert node(latest, 'API façade') == stable
    assert next(n for n in latest['projection']['bundle']['nodes'] if n['title'] == 'two.py')['mapping'] == 'rejected'
    assert latest['projection']['summary']['verified_units'] == 0
    assert not latest['projection']['bundle']['assessments'] and not latest['projection']['bundle']['constraints']
    assert list((tmp_path / '.controlcoding/project-map').iterdir()) == [tmp_path / definition.LOCATION]
    assert (tmp_path / 'one.py').read_text() == 'def run(): return 1\n'


def test_missing_and_explicit_alias_preserve_identity(context, tmp_path):
    save(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    stable = node(observe(context), 'one.py')
    (tmp_path / 'one.py').rename(tmp_path / 'new.py')
    missing = observe(context)
    assert next(n for n in missing['projection']['bundle']['nodes'] if n['id'] == stable)['presence'] == 'missing'
    save(context, {'operation': 'alias', 'target': stable, 'replacement': node(missing, 'new.py')})
    restored = observe(context)
    assert next(n for n in restored['projection']['bundle']['nodes'] if n['id'] == stable)['presence'] == 'observed'
    assert len(restored['review']['entries'][0]['bindings']) == 2
    (tmp_path / 'one.py').write_text('def other(): pass\n')
    assert next(n for n in observe(context)['projection']['bundle']['nodes'] if n['id'] == stable)['mapping'] == 'conflicted'


def test_group_split_merge_retain_prior_ids_and_member_links(context):
    initial = observe(context)
    save(context, {'operation': 'group', 'title': 'Backend', 'members': [node(initial, 'one.py'), node(initial, 'two.py')]})
    grouped = observe(context)
    group = next(r for r in grouped['review']['entries'] if r['title'] == 'Backend')
    save(context, {'operation': 'split', 'target': group['id'], 'groups': [
        {'title': 'Read service', 'members': [group['members'][0]]}, {'title': 'Write service', 'members': [group['members'][1]]}]})
    split = observe(context)
    targets = [r['id'] for r in split['review']['entries'] if r['supersedes'] == [group['id']]]
    save(context, {'operation': 'merge', 'title': 'Application', 'targets': targets})
    merged = observe(context)
    combined = next(r for r in merged['review']['entries'] if r['title'] == 'Application')
    assert set(combined['members']) == set(group['members'])
    assert combined['supersedes'] == targets
    assert all(any(n['id'] == t for n in merged['projection']['bundle']['nodes']) for t in [group['id'], *targets])
    assert len(merged['projection']['bundle']['selection']['nodes']) == 2


def test_stale_source_blocks_preview_and_commit(context, tmp_path):
    args, preview = prepare(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    (tmp_path / 'one.py').write_text('def changed(): pass\n')
    fails('stale_snapshot', lambda: definition.preview_change(*args))
    if os.name == 'nt':
        fails('stale_snapshot', lambda: definition.commit_change(*args, preview['approval_id']))
    assert not (tmp_path / '.controlcoding').exists()


def test_foreign_valid_edit_conflicts_without_overwrite(context, tmp_path):
    save(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    args, preview = prepare(context, {'operation': 'rename', 'target': node(observe(context), 'one.py'), 'title': 'New label'})
    file = tmp_path / definition.LOCATION
    foreign = file.read_bytes() + b'\n'
    file.write_bytes(foreign)
    fails('definition_conflict', lambda: definition.commit_change(*args, preview['approval_id']))
    assert file.read_bytes() == foreign and not (tmp_path / definition.JOURNAL).exists()


def test_foreign_edit_between_preview_and_exclusive_open_is_preserved(context, tmp_path, monkeypatch):
    save(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    args, preview = prepare(context, {'operation': 'rename', 'target': node(observe(context), 'one.py'), 'title': 'New label'})
    file = tmp_path / definition.LOCATION
    foreign = file.read_bytes() + b'\n'
    original = definition._exclusive
    def race(path, create=False):
        if path == file and not create:
            file.write_bytes(foreign)
        return original(path, create)
    monkeypatch.setattr(definition, '_exclusive', race)
    fails('definition_conflict', lambda: definition.commit_change(*args, preview['approval_id']))
    assert file.read_bytes() == foreign


@pytest.mark.skipif(os.name != 'nt', reason='Windows native sharing test')
def test_native_handle_denies_foreign_write_and_replacement(context, tmp_path, monkeypatch):
    save(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    args, preview = prepare(context, {'operation': 'rename', 'target': node(observe(context), 'one.py'), 'title': 'New label'})
    original = definition._write
    results = []
    def during(fd, data):
        if b'"state":"prepared"' in data:
            code = 'from pathlib import Path\nimport sys\np=Path(sys.argv[1])\ntry:\n p.write_bytes(b"FOREIGN")\nexcept OSError:\n sys.exit(23)\nsys.exit(0)'
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(tmp_path / definition.LOCATION)], capture_output=True, timeout=10)
            results.append(result.returncode)
            with pytest.raises(OSError):
                (tmp_path / definition.LOCATION).unlink()
        return original(fd, data)
    monkeypatch.setattr(definition, '_write', during)
    definition.commit_change(*args, preview['approval_id'])
    assert results == [23]


@pytest.mark.skipif(os.name != 'nt', reason='Windows persistence interruption test')
def test_process_interruption_leaves_preimage_and_blocks_reentry(context, tmp_path):
    save(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    args, preview = prepare(context, {'operation': 'rename', 'target': node(observe(context), 'one.py'), 'title': 'New label'})
    before = (tmp_path / definition.LOCATION).read_bytes()
    code = '''import sys,json,os
sys.path.insert(0,sys.argv[1])
import cc_project_map_definition as d
original=d._write
def crash(fd,data):
 if b'"state":"prepared"' not in data: os._exit(73)
 original(fd,data)
d._write=crash
d.commit_change(*json.loads(sys.argv[2]))
'''
    child = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(Path(definition.__file__).parent), json.dumps([*args, preview['approval_id']])], capture_output=True, timeout=20)
    assert child.returncode == 73 and not child.stderr
    journal = json.loads((tmp_path / definition.JOURNAL).read_bytes())
    assert journal['before'].encode() == before
    fails('recovery_required', lambda: observe(context))
    assert (tmp_path / definition.LOCATION).read_bytes() == before


@pytest.mark.parametrize('payload', [b'{', b'{"schema_version":1,"schema_version":1}', b'[]', b'x' * (definition.LIMIT + 1)], ids=['malformed','duplicate','array','oversized'])
def test_invalid_definition_is_preserved_and_safe(context, tmp_path, payload):
    path = tmp_path / definition.LOCATION
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    with pytest.raises(definition.DefinitionError):
        observe(context)
    assert path.read_bytes() == payload


def test_root_identity_rejects_copied_choices(context, tmp_path):
    save(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    other = tmp_path / 'other'
    target = other / definition.LOCATION
    target.parent.mkdir(parents=True)
    target.write_bytes((tmp_path / definition.LOCATION).read_bytes())
    request = {**context[0], 'project_root': str(other)}
    fails('root_conflict', lambda: definition.observe_reviewed(request, source.preview_project_map_scope(request)['preview_id']))


def test_hardlink_definition_rejected_without_touching_target(context, tmp_path):
    path = tmp_path / definition.LOCATION
    path.parent.mkdir(parents=True)
    foreign = tmp_path / 'foreign.txt'
    foreign.write_bytes(b'FOREIGN')
    os.link(foreign, path)
    fails('unsupported_path', lambda: observe(context))
    assert foreign.read_bytes() == b'FOREIGN'


@pytest.mark.parametrize('change', [None, {}, {'operation': 'accept', 'target': 'unknown'},
    {'operation': 'accept', 'target': 'x', 'permission': 'allow'}, {'operation': 'group', 'title': 'x', 'members': []},
    {'operation': 'merge', 'targets': [], 'title': 'x'}, {'operation': 'rename', 'target': 'unknown', 'title': '\n'}])
def test_bad_changes_never_write(context, tmp_path, change):
    with pytest.raises(definition.DefinitionError):
        prepare(context, change)
    assert not (tmp_path / '.controlcoding').exists()


def test_partition_and_alias_conflict(context):
    initial = observe(context)
    save(context, {'operation': 'group', 'title': 'Both', 'members': [node(initial, 'one.py'), node(initial, 'two.py')]})
    grouped = observe(context)
    row = next(r for r in grouped['review']['entries'] if r['title'] == 'Both')
    fails('invalid_partition', lambda: prepare(context, {'operation': 'split', 'target': row['id'], 'groups': [
        {'title': 'A', 'members': row['members']}, {'title': 'B', 'members': row['members']}]}))
    fails('alias_conflict', lambda: prepare(context, {'operation': 'alias', 'target': node(grouped, 'one.py'), 'replacement': node(initial, 'two.py')}))


def test_definition_forbids_extra_truth_and_cycles(context):
    initial = observe(context)
    blank = {'schema_version': 1, 'root_identity': initial['root_identity'], 'entries': []}
    proposed = definition.change_definition(initial, blank, {'operation': 'accept', 'target': node(initial, 'one.py')})
    bad = deepcopy(proposed)
    bad['entries'][0]['delivery'] = 'accepted'
    fails('invalid_definition', lambda: definition.validate(bad, initial['root_identity']))
    bad = deepcopy(proposed)
    bad['entries'][0]['supersedes'] = [bad['entries'][0]['id']]
    fails('invalid_definition', lambda: definition.validate(bad, initial['root_identity']))


def test_wrong_approval_cannot_save(context, tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows save')
    args, preview = prepare(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    fails('preview_mismatch', lambda: definition.commit_change(*args, '0' * 64))
    assert not (tmp_path / '.controlcoding').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows retained source/ancestor checks')
def test_late_source_edit_before_pinning_blocks_write(context, tmp_path, monkeypatch):
    args, preview = prepare(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    original = definition.preview_change
    def change(*a):
        result = original(*a)
        (tmp_path / 'one.py').write_text('def changed(): pass\n')
        return result
    monkeypatch.setattr(definition, 'preview_change', change)
    fails('stale_snapshot', lambda: definition.commit_change(*args, preview['approval_id']))
    assert not (tmp_path / definition.LOCATION).exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction confinement')
def test_junction_store_never_reads_or_writes_foreign_definition(context, tmp_path):
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'sentinel.txt').write_bytes(b'FOREIGN')
    control = tmp_path / '.controlcoding'
    control.mkdir()
    link = control / 'project-map'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(foreign)], capture_output=True, timeout=10)
    assert result.returncode == 0
    fails('unsupported_path', lambda: observe(context))
    assert list(foreign.iterdir()) == [foreign / 'sentinel.txt']


@pytest.mark.skipif(os.name != 'nt', reason='Windows first-create conflict')
def test_foreign_first_creation_wins_without_overwrite(context, tmp_path, monkeypatch):
    args, preview = prepare(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    original = definition._exclusive
    target = tmp_path / definition.LOCATION
    def race(path, create=False):
        if path == target and create:
            target.write_bytes(b'FOREIGN')
        return original(path, create)
    monkeypatch.setattr(definition, '_exclusive', race)
    fails('recovery_required', lambda: definition.commit_change(*args, preview['approval_id']))
    assert target.read_bytes() == b'FOREIGN'
    assert (tmp_path / definition.JOURNAL).exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive writer')
def test_already_open_writer_prevents_definition_observation(context, tmp_path):
    save(context, {'operation': 'accept', 'target': node(observe(context), 'one.py')})
    file = tmp_path / definition.LOCATION
    before = file.read_bytes()
    fd = definition._exclusive(file)
    try:
        fails('inaccessible', lambda: observe(context))
    finally:
        os.close(fd)
    assert file.read_bytes() == before


def test_entry_budget_and_supersession_cycle_rejected(context):
    initial = observe(context)
    value = {'schema_version': 1, 'root_identity': initial['root_identity'], 'entries': []}
    for title in ('one.py', 'two.py'):
        value = definition.change_definition(initial, value, {'operation': 'accept', 'target': node(initial, title)})
    bad = deepcopy(value)
    bad['entries'][0]['supersedes'] = [bad['entries'][1]['id']]
    bad['entries'][1]['supersedes'] = [bad['entries'][0]['id']]
    fails('invalid_definition', lambda: definition.validate(bad, initial['root_identity']))
    bad = deepcopy(value)
    bad['entries'] = bad['entries'] * 65
    fails('invalid_definition', lambda: definition.validate(bad, initial['root_identity']))
