"""Behavioral isolation, migration preservation and stale-preview boundaries."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_layout as layout
import cc_layout_cli as cli


def test_legacy_and_foreign_cc_are_not_adopted(tmp_path):
    (tmp_path / "cc").mkdir()
    (tmp_path / "cc" / "app.py").write_text("application")
    assert layout.managed_path(tmp_path, ".controlcoding") == tmp_path / ".controlcoding"
    with pytest.raises(layout.LayoutError):
        cli.initialize(tmp_path)
    assert (tmp_path / "cc/app.py").read_text() == "application"


def test_explicit_containment_keeps_project_source_namespace(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("User source")
    cli.initialize(tmp_path)
    assert layout.managed_path(tmp_path, "dev/design") == tmp_path / "cc/dev/design"
    assert layout.source_path(tmp_path, "README.md") == source
    assert layout.source_path(tmp_path, "ROADMAP.md") == tmp_path / "ROADMAP.md"
    assert layout.source_path(tmp_path, ".controlwork/memory.json") == tmp_path / "cc/.controlwork/memory.json"
    assert layout.logical_relative(tmp_path, "cc/dev/design") == "dev/design"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["README.md", "cc"]


@pytest.mark.parametrize("value", [b"{}", b'{"schema":true,"layout":"contained"}',
    b'{"schema":1,"schema":1,"layout":"contained"}', b"x" * 1025,
    b'{"schema":1,"layout":"contained","root":"../elsewhere"}'])
def test_marker_fails_closed(tmp_path, value):
    (tmp_path / "cc").mkdir()
    (tmp_path / "cc/layout.json").write_bytes(value)
    with pytest.raises(layout.LayoutError):
        layout.managed_path(tmp_path, ".controlcoding/cc_config.json")
    assert not (tmp_path / ".controlcoding").exists()


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/elsewhere", "x/../y", "x//y", "x:stream", "x."])
def test_managed_traversal_is_refused(tmp_path, path):
    with pytest.raises(layout.LayoutError):
        layout.managed_path(tmp_path, path)


def test_preview_context_is_scoped_and_does_not_write(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with layout.preview_contained(tmp_path):
        assert layout.is_contained(tmp_path)
        assert not layout.is_contained(other)
    assert not layout.is_contained(tmp_path)
    assert not (tmp_path / "cc").exists()


def test_marker_change_invalidates_snapshot(tmp_path):
    from cc_setup_service import _Snapshots, ReadPolicy, SetupServiceError
    with _Snapshots(tmp_path, {}, ReadPolicy()) as reader:
        cli.initialize(tmp_path)
        with pytest.raises(SetupServiceError):
            reader.recheck()


@pytest.mark.parametrize('journal_mode', ['delete', 'wal'])
def test_migration_preserves_db_records_user_files_and_backup(tmp_path, journal_mode):
    project = tmp_path / "project"
    project.mkdir()
    runtime = project / ".controlcoding/knowledge"
    runtime.mkdir(parents=True)
    database = runtime / "knowledge.db"
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA journal_mode=' + journal_mode)
        db.execute("CREATE TABLE sources(id TEXT PRIMARY KEY, path TEXT)")
        db.execute("INSERT INTO sources VALUES('stable-source','.controlwork/decision.md')")
    db.close()
    user = project / "ROADMAP.md"
    user.write_text("Not selected: application-owned document")
    original = database.read_bytes()
    backup = tmp_path / "backup"
    plan, _, _ = cli.migration_plan(project, [".controlcoding"], backup)
    assert not plan["blockers"]
    result = cli.migrate(project, [".controlcoding"], backup, plan["approval_id"])
    assert result["layout"] == "contained"
    assert not (project / ".controlcoding").exists()
    assert (backup / "original/.controlcoding/knowledge/knowledge.db").read_bytes() == original
    with sqlite3.connect(project / "cc/.controlcoding/knowledge/knowledge.db") as db:
        assert db.execute("SELECT * FROM sources").fetchall() == [("stable-source", ".controlwork/decision.md")]
    assert user.read_text() == "Not selected: application-owned document"


def test_stale_migration_preview_never_creates_backup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".controlcoding").mkdir()
    config = project / ".controlcoding/cc_config.json"
    config.write_text("{}")
    plan, _, _ = cli.migration_plan(project, [".controlcoding"], tmp_path / "backup")
    config.write_text('{"changed":true}')
    with pytest.raises(layout.LayoutError, match="preview_mismatch"):
        cli.migrate(project, [".controlcoding"], tmp_path / "backup", plan["approval_id"])
    assert not (tmp_path / "backup").exists()
    assert not (project / "cc").exists()


def test_reference_relocation_preserves_similarly_named_application_paths(tmp_path):
    selected = ['.controlcoding', 'tools/fitness_check.py', 'src/core/.feature-lock.json']
    original = {'command': 'python "{project}/tools/fitness_check.py"',
                'task': '${workspaceFolder}/.controlcoding/launchers/run.sh',
                'foreign': '{project}/tools/fitness_check.py.backup',
                'foreign_state': str(tmp_path / '.controlcoding-other/app.json'),
                'state': str(tmp_path / '.controlcoding/config.json'),
                'lock': str(tmp_path / 'src/core/.feature-lock.json')}
    result = json.loads(cli._relocate_text(tmp_path, json.dumps(original).encode(), selected))
    assert result['command'] == 'python "{project}/cc/tools/fitness_check.py"'
    assert result['task'] == '${workspaceFolder}/cc/.controlcoding/launchers/run.sh'
    assert result['foreign'] == original['foreign'] and result['foreign_state'] == original['foreign_state']
    assert result['state'] == str(tmp_path / 'cc/.controlcoding/config.json')
    assert result['lock'] == str(tmp_path / 'cc/.controlcoding/module-locks/src/core/.feature-lock.json')


def test_busy_database_rejected_before_backup_or_namespace_creation(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    folder = project / '.controlcoding'; folder.mkdir()
    db = sqlite3.connect(folder / 'state.db')
    try:
        db.execute('CREATE TABLE state(value TEXT)'); db.commit()
        db.execute('BEGIN IMMEDIATE')
        plan = cli.migration_plan(project, ['.controlcoding'], tmp_path / 'backup')[0]
        with pytest.raises((layout.LayoutError, sqlite3.OperationalError)):
            cli.migrate(project, ['.controlcoding'], tmp_path / 'backup', plan['approval_id'])
        assert not (project / 'cc').exists() and not (tmp_path / 'backup').exists()
    finally:
        db.close()


def test_migration_blocks_links_to_unmoved_sources_without_rewriting_human_text(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    (project / 'CONTROLCODING.md').write_text('# Context\n[App](README.md)\n')
    (project / 'README.md').write_text('# Application\n')
    original = (project / 'CONTROLCODING.md').read_bytes()
    plan = cli.migration_plan(project, ['CONTROLCODING.md'], tmp_path / 'backup')[0]
    assert any('relative document reference' in issue for issue in plan['blockers'])
    with pytest.raises(layout.LayoutError, match='migration_blocked'):
        cli.migrate(project, ['CONTROLCODING.md'], tmp_path / 'backup', plan['approval_id'])
    assert (project / 'CONTROLCODING.md').read_bytes() == original
    assert not (project / 'cc').exists() and not (tmp_path / 'backup').exists()


def test_incomplete_copy_can_be_inspected_without_deleting_originals(tmp_path, monkeypatch):
    project = tmp_path / 'project'; project.mkdir()
    (project / '.controlcoding').mkdir()
    original = project / '.controlcoding/config.json'; original.write_text('{}')
    plan = cli.migration_plan(project, ['.controlcoding'], tmp_path / 'backup')[0]
    copy = cli._copy
    def interrupt(source, target, expected, owned_stream=None):
        if target.is_relative_to(project / 'cc'):
            raise RuntimeError('interrupted copy')
        return copy(source, target, expected, owned_stream)
    monkeypatch.setattr(cli, '_copy', interrupt)
    with pytest.raises(RuntimeError):
        cli.migrate(project, ['.controlcoding'], tmp_path / 'backup', plan['approval_id'])
    assert cli.recover(project)['stage'] == 'copying'
    with pytest.raises(layout.LayoutError, match='migration_copy_incomplete'):
        cli.recover(project, apply=True)
    assert original.read_text() == '{}'
    assert (tmp_path / 'backup/original/.controlcoding/config.json').read_text() == '{}'


def test_contained_configuration_and_evidence_use_actual_paths(tmp_path):
    import cc_panel_configuration as config
    import cc_evidence_inputs as inputs
    cli.initialize(tmp_path)
    assert config.read_draft(str(tmp_path))["path"] == "cc/.controlcoding/panel-setup-draft.json"
    contract = tmp_path / "cc/controlcoding.verification.json"
    contract.write_text('{"suites":[]}')
    assert inputs.read_contract(tmp_path, "verification")[1]["path"] == "cc/controlcoding.verification.json"
    before = inputs.capture_inputs(tmp_path, inputs.input_policy())
    runtime = tmp_path / "cc/.controlcoding/knowledge"
    runtime.mkdir(parents=True)
    (runtime / "cache.json").write_text("{}");
    after = inputs.capture_inputs(tmp_path, inputs.input_policy())
    assert before["complete"] and after["complete"]
    assert before["contentDigest"] == after["contentDigest"]
    contract.write_text('{"suites":[],"changed":true}')
    assert inputs.capture_inputs(tmp_path, inputs.input_policy())["contentDigest"] != after["contentDigest"]


@pytest.mark.parametrize('rollback', [False, True])
def test_interrupted_publication_can_resume_or_restore_verified_bytes(tmp_path, monkeypatch, rollback):
    project = tmp_path / 'project'; project.mkdir()
    (project / '.controlcoding').mkdir()
    (project / '.controlcoding/cc_config.json').write_text('{}')
    backup = tmp_path / 'backup'
    plan = cli.migration_plan(project, ['.controlcoding'], backup)[0]
    original_recover = cli.recover
    def interrupted(*args, **kwargs):
        raise RuntimeError('simulated interruption before publication')
    monkeypatch.setattr(cli, 'recover', interrupted)
    with pytest.raises(RuntimeError):
        cli.migrate(project, ['.controlcoding'], backup, plan['approval_id'])
    assert (project / '.controlcoding/cc_config.json').read_text() == '{}'
    with pytest.raises(layout.LayoutError, match='layout_recovery_required'):
        layout.managed_path(project, '.controlcoding/cc_config.json')
    assert original_recover(project)['stage'] == 'prepared'
    result = original_recover(project, apply=True, rollback=rollback)
    assert result['layout'] == ('legacy' if rollback else 'contained')
    assert (backup / 'original/.controlcoding/cc_config.json').read_text() == '{}'
    if rollback:
        assert not (project / 'cc').exists()
        assert not (project / '.gitignore').exists()
    else:
        assert not (project / '.controlcoding').exists()
        assert '/cc/' in (project / '.gitignore').read_text()


def test_migration_installed_helpers_inline_locks_and_ignore_block(tmp_path):
    import cc
    project = tmp_path / 'project'; project.mkdir()
    assert cc.cmd_init(project) == 0
    (project / 'src/engine').mkdir(parents=True)
    lock = project / 'src/engine/.feature-lock.json'
    lock.write_text(json.dumps({'version': 1, 'module': 'engine', 'owns': ['src/engine/**']}))
    application = project / 'tools/application.py'
    application.write_text('application helper')
    selected = ['.controlcoding', 'CONTROLCODING.md', 'STATUS.md', 'ROADMAP.md', 'BUGS.md',
                'devlog', 'hooks', 'tools/fitness_check.py', 'tools/cc_layout.py', 'tools/control_plane_utils.py',
                'src/engine/.feature-lock.json']
    selected = [p for p in selected if (project / p).exists()]
    plan = cli.migration_plan(project, selected, tmp_path / 'backup')[0]
    assert not plan['blockers']
    cli.migrate(project, selected, tmp_path / 'backup', plan['approval_id'])
    assert application.read_text() == 'application helper'
    assert (project / 'cc/.controlcoding/module-locks/src/engine/.feature-lock.json').exists()
    assert not lock.exists()
    assert (project / 'src/engine').is_dir()
    ignore = (project / '.gitignore').read_text()
    assert '/cc/' in ignore and '\ndev/' not in ignore and '\nhooks/' not in ignore
    assert not (project / 'cc/src').exists()


def test_hooks_only_migration_installs_governance_companions(tmp_path):
    import cc
    import subprocess
    project = tmp_path / 'project'; project.mkdir()
    hooks = project / 'hooks'; hooks.mkdir()
    for name in cc.INIT_HOOKS:
        (hooks / name).write_bytes(cc._init_hook_source(name).read_bytes())
    plan = cli.migration_plan(project, ['hooks'], tmp_path / 'backup')[0]
    assert not plan['blockers']
    assert 'tools/control_plane_utils.py' in plan['refresh']
    cli.migrate(project, ['hooks'], tmp_path / 'backup', plan['approval_id'])
    result = subprocess.run(
        [sys.executable, '-B', '-c',
         "import sys,json; from pathlib import Path; sys.path.insert(0,'cc/hooks'); "
         "from codewarden_review import _check_engagement_gate; "
         "print(json.dumps(_check_engagement_gate(Path.cwd())))"],
        cwd=project, capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == {'allowed': True, 'block': False, 'reason': 'active'}
    assert not (project / 'tools').exists()


def test_tool_migration_restores_local_dependency_closure(tmp_path):
    import cc
    project = tmp_path / 'project'; project.mkdir()
    tools = project / 'tools'; tools.mkdir()
    (tools / 'mcp_session.py').write_bytes((cc.SCRIPTS_DIR / 'mcp_session.py').read_bytes())
    plan = cli.migration_plan(project, ['tools/mcp_session.py'], tmp_path / 'backup')[0]
    assert not plan['blockers']
    assert 'tools/cc_lockfile.py' in plan['refresh']
    cli.migrate(project, ['tools/mcp_session.py'], tmp_path / 'backup', plan['approval_id'])
    assert (project / 'cc/tools/cc_lockfile.py').is_file()
    assert not (tools / 'mcp_session.py').exists()


def test_existing_unselected_companion_blocks_migration(tmp_path):
    import cc
    project = tmp_path / 'project'; project.mkdir()
    tools = project / 'tools'; tools.mkdir()
    for name in ('mcp_session.py', 'cc_lockfile.py'):
        (tools / name).write_bytes((cc.SCRIPTS_DIR / name).read_bytes())
    plan = cli.migration_plan(project, ['tools/mcp_session.py'], tmp_path / 'backup')[0]
    assert any('tools/cc_lockfile.py' in issue for issue in plan['blockers'])
    with pytest.raises(layout.LayoutError, match='migration_blocked'):
        cli.migrate(project, ['tools/mcp_session.py'], tmp_path / 'backup', plan['approval_id'])
    assert (tools / 'cc_lockfile.py').is_file() and not (project / 'cc').exists()


def test_optional_bridge_and_visual_artifacts_migrate_only_when_selected(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    for name in ('.bridge/messages', 'screenshots', '.controlcoding'):
        (project / name).mkdir(parents=True)
    (project / '.bridge/messages/001.json').write_text('{"content":"retained"}')
    (project / 'screenshots/check.png').write_bytes(b'fixture image')
    (project / '.controlcoding/settings.json').write_text(
        '{"mcpServers":{"bridge":{"env":{"BRIDGE_DIR":".bridge"}}}}')
    selected = ['.bridge', 'screenshots', '.controlcoding']
    plan = cli.migration_plan(project, selected, tmp_path / 'backup')[0]
    assert not plan['blockers']
    cli.migrate(project, selected, tmp_path / 'backup', plan['approval_id'])
    assert (project / 'cc/.bridge/messages/001.json').read_text() == '{"content":"retained"}'
    assert (project / 'cc/screenshots/check.png').read_bytes() == b'fixture image'
    settings = json.loads((project / 'cc/.controlcoding/settings.json').read_text())
    assert settings['mcpServers']['bridge']['env']['BRIDGE_DIR'] == 'cc/.bridge'
    assert not (project / '.bridge').exists() and not (project / 'screenshots').exists()


def test_optional_pack_install_routes_mcp_entries_and_contains_companions(tmp_path, capsys):
    import cc
    cli.initialize(tmp_path)
    assert cc.cmd_install(tmp_path, 'all') == 0
    assert 'python cc/tools/cc_dashboard.py --project-root .' in capsys.readouterr().out
    settings = json.loads((tmp_path / 'cc/.controlcoding/settings.json').read_text())
    for server in settings['mcpServers'].values():
        for argument in server.get('args', []):
            if isinstance(argument, str) and argument.endswith('.py'):
                assert argument.startswith('cc/tools/')
                assert (tmp_path / argument).is_file()
    assert settings['mcpServers']['bridge']['env']['BRIDGE_DIR'] == 'cc/.bridge'
    assert (tmp_path / 'cc/tools/control_plane_utils.py').is_file()
    assert (tmp_path / 'cc/tools/visual_check_utils.py').is_file()
    assert not (tmp_path / 'tools').exists() and not (tmp_path / '.bridge').exists()
    assert not (tmp_path / '.controlcoding').exists()


@pytest.mark.parametrize('journal_exists', [False, True])
def test_interrupted_atomic_journal_write_is_inspectable_and_fail_closed(tmp_path, journal_exists):
    project = tmp_path / 'project'; project.mkdir()
    (project / 'cc').mkdir()
    pending = project / cli.PENDING_JOURNAL
    pending.write_bytes(b'{"incomplete":')
    if journal_exists:
        (project / cli.JOURNAL).write_text('{"old":"journal"}')
    before = {p.name: p.read_bytes() for p in (project / 'cc').iterdir()}
    assert cli.status(project)['recovery_required']
    assert cli.recover(project)['manual_recovery_required']
    with pytest.raises(layout.LayoutError, match='layout_recovery_required'):
        layout.managed_path(project, '.controlcoding/config.json')
    for rollback in (False, True):
        with pytest.raises(layout.LayoutError, match='migration_journal_write_incomplete'):
            cli.recover(project, apply=True, rollback=rollback)
    assert {p.name: p.read_bytes() for p in (project / 'cc').iterdir()} == before
