"""Contained-layout coverage for Core setup, doctor and installed hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import importlib
import shutil
import time
import types
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TEMPLATE_SCRIPTS = Path(__file__).resolve().parents[1] / "templates" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cc
import cc_setup
from cc_layout import MARKER_BYTES


def _contained_project(tmp_path: Path) -> Path:
    project = tmp_path / "application"
    project.mkdir()
    (project / ".git" / "hooks").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    namespace = project / "cc"
    namespace.mkdir()
    (namespace / "layout.json").write_bytes(MARKER_BYTES)
    return project


def test_contained_init_setup_context_doctor_and_hook_keep_source_root_clean(tmp_path, capsys):
    project = _contained_project(tmp_path)
    source_before = (project / "src" / "app.py").read_bytes()

    assert cc.cmd_init(project, quiet=True) == 0
    assert cc_setup._generate_context_source(
        project,
        {"name": "Contained Demo", "stack": "Python", "arch": "modular"},
    )
    assert cc.cmd_doctor(project) in {0, 1}

    contained = project / "cc"
    assert (contained / ".controlcoding" / "settings.json").is_file()
    assert (contained / "CONTROLCODING.md").is_file()
    assert (contained / "STATUS.md").is_file()
    assert (contained / "devlog").is_dir()
    assert (contained / "hooks" / "cc_layout.py").is_file()
    assert (contained / "tools" / "fitness_check.py").is_file()
    assert (contained / "tools" / "cc_layout.py").is_file()
    assert (contained / "tools" / "control_plane_utils.py").is_file()
    assert not (project / ".controlcoding").exists()
    assert not (project / "hooks").exists()
    assert not (project / "tools").exists()

    assert cc.cmd_init_module(project, "orders") == 0
    assert (project / "modules" / "orders").is_dir()
    assert not (project / "modules" / "orders" / ".feature-lock.json").exists()
    assert (contained / ".controlcoding" / "module-locks" / "modules" / "orders" / ".feature-lock.json").is_file()
    assert (project / "src" / "app.py").read_bytes() == source_before

    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(contained / "layout.json")}})
    result = subprocess.run(
        [sys.executable, str(contained / "hooks" / "check_boundaries.py")],
        input=payload,
        text=True,
        capture_output=True,
        cwd=project,
    )
    assert result.returncode == 2
    assert "self-protected" in result.stderr.lower()


def test_contained_hook_denies_invalid_marker(tmp_path):
    project = _contained_project(tmp_path)
    assert cc.cmd_init(project, quiet=True) == 0
    contained = project / "cc"
    hook = contained / "hooks" / "check_boundaries.py"
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(project / "src" / "app.py")}})

    (contained / "layout.json").write_text("{}\n", encoding="utf-8")
    malformed = subprocess.run(
        [sys.executable, str(hook)], input=payload, text=True, capture_output=True, cwd=project,
    )
    assert malformed.returncode == 2
    assert "layout" in malformed.stderr.lower()


def test_contained_hook_denies_unsafe_module_lock(tmp_path):
    project = _contained_project(tmp_path)
    assert cc.cmd_init(project, quiet=True) == 0
    contained = project / "cc"
    hook = contained / "hooks" / "check_boundaries.py"
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(project / "src" / "app.py")}})

    lock = contained / ".controlcoding" / "module-locks" / "orders" / ".feature-lock.json"
    lock.parent.mkdir(parents=True)
    try:
        os.symlink(project / "src" / "app.py", lock)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    unsafe = subprocess.run(
        [sys.executable, str(hook)], input=payload, text=True, capture_output=True, cwd=project,
        env={**os.environ, "CC_ACTIVE_MODULE": "orders"},
    )
    assert unsafe.returncode == 2
    assert "unsafe" in unsafe.stderr.lower()


def test_contained_git_hook_dispatches_protected_path_without_root_runtime(tmp_path):
    project = tmp_path / "git-application"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    (project / "src").mkdir()
    locked = project / "src" / "locked.py"
    locked.write_text("VALUE = 1\n", encoding="utf-8")
    (project / "cc").mkdir()
    (project / "cc" / "layout.json").write_bytes(MARKER_BYTES)
    assert cc.cmd_init(project, quiet=True) == 0
    config_path = project / "cc" / ".controlcoding" / "cc_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["protected_zones"] = [{"path": "src/locked.py", "level": "deny", "description": "fixture"}]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    subprocess.run(["git", "add", "src/locked.py"], cwd=project, check=True, capture_output=True, text=True)
    result = subprocess.run(
        ["git", "-c", "user.name=Contained Test", "-c", "user.email=contained@example.invalid", "commit", "-m", "blocked fixture"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    # Git normalizes a rejecting hook's exit status to its own commit failure.
    assert result.returncode != 0
    assert "src/locked.py" in result.stderr.replace("\\", "/")
    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    assert "/cc/" in gitignore
    assert "/dev/" not in gitignore
    assert "/hooks/" not in gitignore
    assert not (project / ".controlcoding").exists()
    assert not (project / "hooks").exists()
    assert not (project / "tools").exists()


def test_contained_successful_git_commit_refreshes_only_contained_control_state(tmp_path):
    project = tmp_path / "git-success-application"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    source = project / "src"
    source.mkdir()
    source_file = source / "releasable.py"
    source_file.write_text("VALUE = 1\n", encoding="utf-8")
    git_identity = ["-c", "user.name=Contained Test", "-c", "user.email=contained@example.invalid"]
    subprocess.run(["git", "add", "src/releasable.py"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(["git", *git_identity, "commit", "-m", "baseline"], cwd=project, check=True, capture_output=True, text=True)
    (project / "cc").mkdir()
    (project / "cc" / "layout.json").write_bytes(MARKER_BYTES)
    assert cc.cmd_init(project, quiet=True) == 0

    source_file.write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/releasable.py"], cwd=project, check=True, capture_output=True, text=True)
    result = subprocess.run(
        ["git", *git_identity, "commit", "-m", "contained success"],
        cwd=project,
        capture_output=True,
        text=True,
        env={**os.environ, "CODEWARDEN_BACKEND": "", "CODEWARDEN_CONSENT": ""},
    )
    assert result.returncode == 0, result.stderr
    event_log = project / "cc" / ".controlcoding" / "event_log.jsonl"
    deadline = time.monotonic() + 5
    events = []
    while time.monotonic() < deadline:
        # Post-commit runs in the background. Creation precedes the completed
        # event; Windows also denies reads while append_event holds its lock.
        try:
            text = event_log.read_text(encoding="utf-8")
            # Only parse completed JSONL records; a malformed completed line
            # remains a failure, not a reason to silently ignore the event.
            events = [json.loads(line) for line in text.split("\n")[:-1] if line]
        except (FileNotFoundError, PermissionError):
            pass
        else:
            if any(event.get("event") == "agent_started" and event.get("agent") == "codewarden" for event in events):
                break
        time.sleep(0.05)
    assert event_log.is_file()
    assert any(event.get("event") == "agent_started" and event.get("agent") == "codewarden" for event in events)
    assert not (project / ".controlcoding").exists()
    assert not (project / "cc_hook_log.jsonl").exists()
    assert source_file.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_contained_host_assets_write_and_reference_cc_namespace(tmp_path):
    project = _contained_project(tmp_path)
    written = cc._write_host_integration_assets(project, "codex_cli")
    contained = project / "cc"
    manifest_path = contained / ".controlcoding" / "launchers" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "cc/.controlcoding/launchers/" in manifest["primaryEntryPoint"]["shell"]
    assert manifest["publicAssistant"]["starterPath"].startswith("cc/.controlcoding/")
    assert any(path.startswith("cc/.controlcoding/") for path in written)
    assert not (project / ".controlcoding").exists()


def test_contained_default_verification_contract_uses_contained_receipts(tmp_path):
    project = _contained_project(tmp_path)
    contract = cc._default_verification_contract(project)
    junit_commands = [suite["command"] for suite in contract["suites"] if "--junitxml" in suite["command"]]
    assert junit_commands
    assert all("{project}/cc/.controlcoding/verification_receipts/" in command for command in junit_commands)
    assert all("{project}/.controlcoding/verification_receipts/" not in command for command in junit_commands)


def test_contained_template_runtime_defaults_stay_in_cc_namespace(tmp_path, monkeypatch):
    project = _contained_project(tmp_path)
    (project / "cc" / ".controlcoding").mkdir()
    (project / "cc" / "CONTROLCODING.md").write_text(
        "# Project Identity\n\nPython service\n", encoding="utf-8"
    )
    monkeypatch.setenv("SESSION_PROJECT_ROOT", str(project))
    monkeypatch.syspath_prepend(str(TEMPLATE_SCRIPTS))
    for name in ("control_plane_utils", "planner", "verification_agent", "consult"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()

    control_utils = importlib.import_module("control_plane_utils")
    planner = importlib.import_module("planner")
    verification = importlib.import_module("verification_agent")
    consult = importlib.import_module("consult")

    control_utils.save_engagement({"level": 2})
    assert (project / "cc" / ".controlcoding" / "cc_engagement.json").is_file()
    external_config = project / "application-config" / "engagement.json"
    external_config.parent.mkdir()
    control_utils.save_engagement({"level": 3}, str(external_config))
    assert external_config.is_file()
    assert Path(planner._resolve_runtime_default(planner.PLAN_DIR)).is_relative_to(project / "cc")
    assert Path(planner._resolve_runtime_default(planner.DEFAULT_EVENT_LOG)).is_relative_to(project / "cc")
    assert planner._context_doc_path(planner.DEFAULT_CONTEXT_DOC) == project / "cc" / "CONTROLCODING.md"
    verification.save_criteria([], verification.DEFAULT_CRITERIA_PATH)
    assert (project / "cc" / "devlog" / "criteria" / "criteria.json").is_file()
    assert Path(verification._resolve_runtime_default(verification.DEFAULT_EVENT_LOG)).is_relative_to(project / "cc")
    assert consult._control_plane_read_path("cc_engagement.json") == (
        project / "cc" / ".controlcoding" / "cc_engagement.json"
    )
    stale_legacy = project / ".claude" / "stale_engagement.json"
    stale_legacy.parent.mkdir()
    stale_legacy.write_text('{"level": 4}\n', encoding="utf-8")
    assert consult._control_plane_read_path("stale_engagement.json") == (
        project / "cc" / ".controlcoding" / "stale_engagement.json"
    )
    assert not (project / ".controlcoding").exists()
    assert not (project / "devlog").exists()


def test_contained_optional_runtime_defaults_use_cc_but_custom_paths_stay_literal(
        tmp_path, monkeypatch):
    """Optional tools route only omitted CC artifact destinations."""
    project = _contained_project(tmp_path)
    monkeypatch.setenv("SESSION_PROJECT_ROOT", str(project))
    monkeypatch.delenv("BRIDGE_DIR", raising=False)
    monkeypatch.syspath_prepend(str(TEMPLATE_SCRIPTS))

    class FakeMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, function=None, **kwargs):
            return function if callable(function) else lambda decorated: decorated

    monkeypatch.setitem(sys.modules, "fastmcp", types.SimpleNamespace(FastMCP=FakeMCP))
    for name in (
        "mcp_bridge", "visual_check", "visual_test", "visual_agent",
        "mcp_vision", "mcp_consultant",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()

    bridge = importlib.import_module("mcp_bridge")
    visual_check = importlib.import_module("visual_check")
    visual_test = importlib.import_module("visual_test")
    visual_agent = importlib.import_module("visual_agent")
    concierge = importlib.import_module("concierge")
    vision = importlib.import_module("mcp_vision")
    consultant = importlib.import_module("mcp_consultant")

    assert bridge.BRIDGE_DIR == project / "cc" / ".bridge"
    assert visual_check._default_output_path(None) == str(
        project / "cc" / "screenshots" / "visual_check.png"
    )
    assert visual_test._default_output_dir(None) == str(project / "cc" / "screenshots")
    assert visual_agent._default_screenshot_dir(None, {"project_root": str(project)}) == str(
        project / "cc" / "screenshots"
    )
    assert concierge._default_visual_output_dir(project) == str(project / "cc" / "screenshots")
    assert vision._default_report_output_dir(None) == str(project / "cc" / "devlog")
    report = consultant.save_verifier_report({"ok": True})
    assert Path(report) == project / "cc" / ".controlcoding" / "verifier_report.json"
    assert Path(report).is_file()

    custom = str(project / "application-output" / "shot.png")
    assert visual_check._default_output_path(custom) == custom
    assert visual_test._default_output_dir("application-output") == "application-output"
    assert visual_agent._default_screenshot_dir("application-output", {}) == "application-output"
    assert vision._default_report_output_dir("application-output") == "application-output"


def test_legacy_template_defaults_resolve_after_chdir_not_at_import_root(tmp_path):
    """Legacy default paths remain relative when a process changes project cwd."""
    engine = tmp_path / "engine"
    engine_scripts = engine / "scripts"
    engine_templates = engine / "templates" / "scripts"
    engine_scripts.mkdir(parents=True)
    engine_templates.mkdir(parents=True)
    for name in ("cc_layout.py",):
        shutil.copy2(SCRIPTS / name, engine_scripts / name)
    for name in ("control_plane_utils.py", "planner.py", "verification_agent.py"):
        shutil.copy2(TEMPLATE_SCRIPTS / name, engine_templates / name)
    legacy_project = tmp_path / "legacy-project"
    legacy_project.mkdir()
    program = """
import os
import sys
from pathlib import Path

os.environ.pop('SESSION_PROJECT_ROOT', None)
engine = Path(sys.argv[1])
project = Path(sys.argv[2])
os.chdir(engine)
sys.path[:0] = [str(engine / 'templates' / 'scripts'), str(engine / 'scripts')]
import control_plane_utils
import planner
import verification_agent
os.chdir(project)
control_plane_utils.save_engagement({'level': 2})
planner._log_event('legacy_default_probe', 'test')
verification_agent.save_criteria([], verification_agent.DEFAULT_CRITERIA_PATH)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(engine), str(legacy_project)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (legacy_project / ".controlcoding" / "cc_engagement.json").is_file()
    assert (legacy_project / ".controlcoding" / "event_log.jsonl").is_file()
    assert (legacy_project / "devlog" / "criteria" / "criteria.json").is_file()
    assert not (engine / ".controlcoding").exists()
    assert not (engine / "devlog").exists()


def test_contained_template_defaults_resolve_after_chdir_not_at_import_root(tmp_path):
    engine = tmp_path / "engine"
    engine_scripts = engine / "scripts"
    engine_templates = engine / "templates" / "scripts"
    engine_scripts.mkdir(parents=True)
    engine_templates.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "cc_layout.py", engine_scripts / "cc_layout.py")
    for name in ("control_plane_utils.py", "planner.py", "verification_agent.py"):
        shutil.copy2(TEMPLATE_SCRIPTS / name, engine_templates / name)
    project = _contained_project(tmp_path)
    program = """
import os
import sys
from pathlib import Path

os.environ.pop('SESSION_PROJECT_ROOT', None)
engine = Path(sys.argv[1])
project = Path(sys.argv[2])
os.chdir(engine)
sys.path[:0] = [str(engine / 'templates' / 'scripts'), str(engine / 'scripts')]
import planner
import verification_agent
os.chdir(project)
planner._log_event('contained_default_probe', 'test')
verification_agent.save_criteria([], verification_agent.DEFAULT_CRITERIA_PATH)
"""
    result = subprocess.run([sys.executable, "-c", program, str(engine), str(project)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (project / "cc" / ".controlcoding" / "event_log.jsonl").is_file()
    assert (project / "cc" / "devlog" / "criteria" / "criteria.json").is_file()
    assert not (project / ".controlcoding").exists()
    assert not (project / "devlog").exists()
    assert not (engine / ".controlcoding").exists()
    assert not (engine / "devlog").exists()
