"""Tests for request_lift, Bash write checks, and boundary enforcement."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


HOOK_SOURCE_DIR = Path(__file__).resolve().parents[1] / "templates" / "hooks"
HOOK_FILES = (
    "check_bash_writes.py",
    "check_boundaries.py",
    "check_dangerous_commands.py",
    "feature_lock.py",
    "hook_logger.py",
    "hook_utils.py",
    "request_lift.py",
)


class HookProject:
    """A copied hook pack running inside an isolated temporary Git project."""

    def __init__(self, root, hooks_dir, runtime_dir, env):
        self.root = root
        self.hooks_dir = hooks_dir
        self.runtime_dir = runtime_dir
        self.env = env
        self.control_dir = root / ".controlcoding"
        self.lift_path = self.control_dir / "lift_request.json"
        self.hook_log_path = root / "cc_hook_log.jsonl"

    def run_hook(self, script, payload):
        return subprocess.run(
            [sys.executable, str(self.hooks_dir / script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=self.root,
            env=self.env,
            timeout=30,
            check=False,
        )

    def run_lift(self, *args):
        return subprocess.run(
            [sys.executable, str(self.hooks_dir / "request_lift.py"), *args],
            capture_output=True,
            text=True,
            cwd=self.root,
            env=self.env,
            timeout=30,
            check=False,
        )


def _run_git(root, env, *args):
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
        timeout=30,
        check=False,
    )


@pytest.fixture
def hook_project(tmp_path):
    """Install the production hook pack in a clean, disposable Git project."""
    root = tmp_path / "project"
    hooks_dir = root / "hooks"
    control_dir = root / ".controlcoding"
    runtime_dir = root / ".runtime"
    core_dir = root / "core"
    for directory in (hooks_dir, control_dir, runtime_dir, core_dir):
        directory.mkdir(parents=True)

    for filename in HOOK_FILES:
        shutil.copy2(HOOK_SOURCE_DIR / filename, hooks_dir / filename)

    (control_dir / "cc_config.json").write_text(
        json.dumps(
            {
                "protected_zones": [
                    {
                        "path": "core/",
                        "level": "deny",
                        "description": "Temporary test core",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "test.py").write_text("# protected test file\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "CONTROLCODING_PROJECT_ROOT": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(runtime_dir),
            "TMP": str(runtime_dir),
            "TMPDIR": str(runtime_dir),
        }
    )
    env.pop("CC_ACTIVE_MODULE", None)
    env.pop("PYTHONPATH", None)

    init_result = _run_git(root, env, "init", "--quiet")
    assert init_result.returncode == 0, init_result.stderr
    add_result = _run_git(root, env, "add", "--all")
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(
        root,
        env,
        "-c",
        "user.name=ControlCoding Tests",
        "-c",
        "user.email=tests@controlcoding.invalid",
        "commit",
        "--quiet",
        "-m",
        "temporary hook test baseline",
    )
    assert commit_result.returncode == 0, commit_result.stderr

    project = HookProject(root, hooks_dir, runtime_dir, env)
    yield project

    if project.lift_path.exists():
        clear_result = project.run_lift("--clear")
        assert clear_result.returncode == 0
    assert not project.lift_path.exists()
    for hook_log in tmp_path.rglob("cc_hook_log.jsonl"):
        assert hook_log.resolve().is_relative_to(tmp_path.resolve())


def _edit_payload(file_path):
    return {"tool_name": "Edit", "tool_input": {"file_path": file_path}}


def _bash_payload(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _create_pending_lift(project):
    result = project.run_lift(
        "--file",
        "core/test.py",
        "--reason",
        "unit test",
    )
    assert result.returncode == 0
    return result


def test_t1_boundaries_empty_input_allows(hook_project):
    result = hook_project.run_hook("check_boundaries.py", {})
    assert result.returncode == 0


def test_t2_boundaries_self_protection_blocks(hook_project):
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("hooks/check_boundaries.py"),
    )
    assert result.returncode == 2
    assert "self-protected" in result.stdout
    assert hook_project.hook_log_path.exists()
    assert hook_project.hook_log_path.resolve().is_relative_to(
        hook_project.root.resolve()
    )


def test_t3_request_lift_is_self_protected(hook_project):
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("hooks/request_lift.py"),
    )
    assert result.returncode == 2
    assert "self-protected" in result.stdout


def test_t4_bash_write_hook_is_self_protected(hook_project):
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("hooks/check_bash_writes.py"),
    )
    assert result.returncode == 2
    assert "self-protected" in result.stdout


def test_t5_safe_command_allows(hook_project):
    result = hook_project.run_hook(
        "check_dangerous_commands.py",
        _bash_payload("ls -la"),
    )
    assert result.returncode == 0


def test_t6_git_reset_hard_blocks(hook_project):
    result = hook_project.run_hook(
        "check_dangerous_commands.py",
        _bash_payload("git reset --hard HEAD~1"),
    )
    assert result.returncode == 2
    assert "block" in result.stdout


def test_t7_non_bash_tool_skips_bash_write_check(hook_project):
    result = hook_project.run_hook(
        "check_bash_writes.py",
        {"tool_name": "Edit"},
    )
    assert result.returncode == 0


def test_t8_clean_bash_command_preserves_project(hook_project):
    result = hook_project.run_hook(
        "check_bash_writes.py",
        _bash_payload("ls"),
    )
    assert result.returncode == 0
    assert (hook_project.root / "core" / "test.py").exists()


def test_t9_lift_status_is_empty_initially(hook_project):
    result = hook_project.run_lift("--status")
    assert result.returncode == 0
    assert "No lift request found" in result.stdout


def test_t10_lift_request_creates_pending_file(hook_project):
    result = _create_pending_lift(hook_project)
    assert "PENDING" in result.stdout
    assert hook_project.lift_path.exists()
    assert hook_project.lift_path.resolve().is_relative_to(
        hook_project.root.resolve()
    )


def test_t11_lift_status_remains_pending(hook_project):
    _create_pending_lift(hook_project)
    lift_data = json.loads(hook_project.lift_path.read_text(encoding="utf-8"))
    status_result = hook_project.run_lift("--status")
    assert lift_data["status"] == "PENDING"
    assert "Status:  PENDING" in status_result.stdout


def test_t12_pending_lift_does_not_bypass_boundary(hook_project):
    _create_pending_lift(hook_project)
    result = hook_project.run_hook(
        "check_boundaries.py",
        _edit_payload("core/test.py"),
    )
    assert result.returncode == 2
    assert "DENY-protected" in result.stdout


def test_t13_noninteractive_approval_is_blocked(hook_project):
    _create_pending_lift(hook_project)
    result = hook_project.run_lift("--approve")
    assert result.returncode == 2
    assert "interactive human terminal" in result.stdout


def test_t14_pending_request_has_no_expiry(hook_project):
    _create_pending_lift(hook_project)
    lift_data = json.loads(hook_project.lift_path.read_text(encoding="utf-8"))
    assert lift_data["status"] == "PENDING"
    assert not lift_data.get("expires_at")


def test_t15_repeated_noninteractive_approval_stays_blocked(hook_project):
    _create_pending_lift(hook_project)
    first_result = hook_project.run_lift("--approve")
    second_result = hook_project.run_lift("--approve")
    lift_data = json.loads(hook_project.lift_path.read_text(encoding="utf-8"))
    assert first_result.returncode == 2
    assert second_result.returncode == 2
    assert lift_data["status"] == "PENDING"


def test_t16_clear_lift_succeeds(hook_project):
    _create_pending_lift(hook_project)
    result = hook_project.run_lift("--clear")
    assert result.returncode == 0
    assert "cleared" in result.stdout.lower()


def test_t17_clear_removes_lift_file(hook_project):
    _create_pending_lift(hook_project)
    hook_project.run_lift("--clear")
    assert not hook_project.lift_path.exists()
