"""Unit tests for check_boundaries.py - boundary enforcement hook."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SOURCE_DIR = Path(__file__).parent.parent / "templates" / "hooks"
HOOK_PATH = HOOK_SOURCE_DIR / "check_boundaries.py"
HOOK_UTILS_PATH = HOOK_SOURCE_DIR / "hook_utils.py"
REQUEST_LIFT_PATH = HOOK_SOURCE_DIR / "request_lift.py"


@pytest.fixture(autouse=True)
def _isolated_hook_sources(tmp_path, monkeypatch):
    """Run source-hook checks against a real hook pack copied under tmp_path."""
    project_root = tmp_path / "_isolated_boundary_hook_project"
    hooks_dir = project_root / "hooks"
    hooks_dir.mkdir(parents=True)
    (project_root / ".git").mkdir()

    for filename in (
        "check_boundaries.py",
        "feature_lock.py",
        "hook_logger.py",
        "hook_utils.py",
        "request_lift.py",
    ):
        shutil.copy2(HOOK_SOURCE_DIR / filename, hooks_dir / filename)

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "HOOK_PATH", hooks_dir / "check_boundaries.py")
    monkeypatch.setattr(module, "HOOK_UTILS_PATH", hooks_dir / "hook_utils.py")
    monkeypatch.setattr(module, "REQUEST_LIFT_PATH", hooks_dir / "request_lift.py")

    yield

    for hook_log in tmp_path.rglob("cc_hook_log.jsonl"):
        assert hook_log.resolve().is_relative_to(tmp_path.resolve())


def run_hook(tool_name, file_path, cwd=None):
    """Run check_boundaries.py with simulated stdin, return (stdout, exit_code)."""
    inp = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}})
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=inp, capture_output=True, text=True,
        cwd=cwd,
    )
    return result.stdout.strip(), result.returncode


def _install_boundary_hook_project(tmp_path, zones=None, lift=None):
    """Create a minimal temp project with a copied boundary hook."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}", encoding="utf-8")
    if zones is not None:
        (claude_dir / "cc_config.json").write_text(
            json.dumps({"protected_zones": zones}), encoding="utf-8"
        )
    if lift is not None:
        (claude_dir / "hooks_lifted.json").write_text(
            json.dumps(lift), encoding="utf-8"
        )

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "check_boundaries.py").write_text(
        HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (hooks_dir / "hook_logger.py").write_text(
        "def log_event(*a, **kw): pass", encoding="utf-8"
    )
    (hooks_dir / "hook_utils.py").write_text(
        HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return hooks_dir


def _run_boundary_hook(hooks_dir, tool_name, file_path):
    inp = json.dumps({
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    })
    return subprocess.run(
        [sys.executable, str(hooks_dir / "check_boundaries.py")],
        input=inp, capture_output=True, text=True,
    )


def _write_project_file(project_root, relative_path, content="test"):
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestSelfProtection:
    def test_blocks_hook_file_edit(self):
        stdout, code = run_hook("Edit", "check_boundaries.py")
        assert code == 2
        assert "self-protected" in stdout

    def test_blocks_other_hook_file(self):
        stdout, code = run_hook("Write", "check_dangerous_commands.py")
        assert code == 2
        assert "self-protected" in stdout

    def test_blocks_hook_logger(self):
        stdout, code = run_hook("Edit", "hook_logger.py")
        assert code == 2
        assert "self-protected" in stdout

    def test_blocks_settings_json(self):
        stdout, code = run_hook("Edit", ".claude/settings.json")
        assert code == 2
        assert "self-protected" in stdout

    def test_blocks_canonical_settings_json(self):
        stdout, code = run_hook("Edit", ".controlcoding/settings.json")
        assert code == 2
        assert "self-protected" in stdout

    @pytest.mark.parametrize(
        "config_path",
        [
            ".controlcoding/cc_config.json",
            ".claude/cc_config.json",
            ".controlcoding/hooks_lifted.json",
            ".claude/hooks_lifted.json",
        ],
    )
    def test_blocks_security_config_mutability(self, config_path):
        stdout, code = run_hook("Edit", config_path)
        assert code == 2
        assert "self-protected" in stdout

    def test_blocks_nested_settings_path(self):
        stdout, code = run_hook("Edit", "/some/project/.claude/settings.json")
        assert code == 2
        assert "self-protected" in stdout

    def test_self_protection_before_new_file_check(self):
        """Self-protection must block even for non-existent paths."""
        stdout, code = run_hook("Edit", "/nonexistent/path/check_boundaries.py")
        assert code == 2
        assert "self-protected" in stdout


class TestDenyZones:
    def test_mandatory_templates_hooks_deny_blocks_without_config(self, tmp_path):
        hooks_dir = _install_boundary_hook_project(tmp_path)
        target = _write_project_file(
            tmp_path,
            Path("templates") / "hooks" / "non_self_protected_helper.txt",
        )

        result = _run_boundary_hook(hooks_dir, "Edit", str(target))
        assert result.returncode == 2
        assert "mandatory protected zone" in result.stdout

    def test_mandatory_methodology_deny_blocks_without_config(self, tmp_path):
        hooks_dir = _install_boundary_hook_project(tmp_path)
        target = _write_project_file(
            tmp_path,
            Path("dev") / "methodology_full.md",
            "# methodology",
        )

        result = _run_boundary_hook(hooks_dir, "Edit", str(target))
        assert result.returncode == 2
        assert "mandatory protected file" in result.stdout

    def test_empty_config_does_not_disable_mandatory_deny_zones(self, tmp_path):
        hooks_dir = _install_boundary_hook_project(
            tmp_path,
            zones={"deny": [], "warn": []},
        )
        target = _write_project_file(
            tmp_path,
            Path("templates") / "hooks" / "non_self_protected_helper.txt",
        )

        result = _run_boundary_hook(hooks_dir, "Edit", str(target))
        assert result.returncode == 2
        assert "mandatory protected zone" in result.stdout

    def test_custom_config_zones_merge_with_mandatory_zones(self, tmp_path):
        hooks_dir = _install_boundary_hook_project(
            tmp_path,
            zones=[
                {"path": "src/core/", "description": "Core models", "level": "deny"}
            ],
        )
        core_file = _write_project_file(
            tmp_path,
            Path("src") / "core" / "engine.py",
            "# engine",
        )
        hook_zone_file = _write_project_file(
            tmp_path,
            Path("templates") / "hooks" / "non_self_protected_helper.txt",
        )

        custom_result = _run_boundary_hook(hooks_dir, "Edit", str(core_file))
        mandatory_result = _run_boundary_hook(hooks_dir, "Edit", str(hook_zone_file))
        assert custom_result.returncode == 2
        assert "Core models" in custom_result.stdout
        assert mandatory_result.returncode == 2
        assert "mandatory protected zone" in mandatory_result.stdout

    def test_new_file_in_mandatory_deny_zone_is_blocked(self, tmp_path):
        hooks_dir = _install_boundary_hook_project(
            tmp_path,
            zones={"deny": [], "warn": []},
        )
        target = tmp_path / "templates" / "hooks" / "new_helper.txt"

        result = _run_boundary_hook(hooks_dir, "Write", str(target))
        assert result.returncode == 2
        assert "mandatory protected zone" in result.stdout

    def test_deny_blocks_via_canonical_config(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "src/core/", "description": "Core models", "level": "deny"}
            ]
        }
        (control_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (control_dir / "settings.json").write_text("{}", encoding="utf-8")

        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "check_boundaries.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (hooks_dir / "hook_logger.py").write_text(
            "def log_event(*a, **kw): pass", encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )

        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(core_dir / "engine.py")},
        })
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_boundaries.py")],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "Core models" in result.stdout

    def test_deny_blocks_via_config(self, tmp_path):
        """DENY zone from cc_config.json blocks edits to existing files."""
        # Create project structure with config
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "src/core/", "description": "Core models", "level": "deny"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        # Create the target file (DENY only applies to existing files)
        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        # Copy hook to tmp project
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_content = HOOK_PATH.read_text(encoding="utf-8")
        (hooks_dir / "check_boundaries.py").write_text(hook_content, encoding="utf-8")
        # Copy hook_logger stub
        (hooks_dir / "hook_logger.py").write_text(
            "def log_event(*a, **kw): pass", encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )

        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(core_dir / "engine.py")},
        })
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_boundaries.py")],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "Core models" in result.stdout

    def test_warn_zone_allows(self, tmp_path):
        """WARN zone allows with warning message."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "src/shared/", "description": "Shared utils", "level": "warn"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        shared_dir = tmp_path / "src" / "shared"
        shared_dir.mkdir(parents=True)
        (shared_dir / "helpers.py").write_text("# helpers", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "check_boundaries.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (hooks_dir / "hook_logger.py").write_text(
            "def log_event(*a, **kw): pass", encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )

        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(shared_dir / "helpers.py")},
        })
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_boundaries.py")],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Shared utils" in result.stdout

    def test_deny_zone_normalizes_traversal_path(self, tmp_path):
        core_dir = tmp_path / "src" / "core"
        public_dir = tmp_path / "src" / "public"
        core_dir.mkdir(parents=True)
        public_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        hooks_dir = _install_boundary_hook_project(
            tmp_path,
            zones=[{"path": "src/core/", "description": "Core", "level": "deny"}],
        )

        traversal_path = tmp_path / "src" / "public" / ".." / "core" / "engine.py"
        result = _run_boundary_hook(hooks_dir, "Edit", str(traversal_path))
        assert result.returncode == 2
        assert "Core" in result.stdout

    def test_deny_zone_checks_resolved_symlink_target(self, tmp_path):
        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")
        link_dir = tmp_path / "linked_core"
        try:
            link_dir.symlink_to(core_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation is not reliable in this environment: {exc}")

        hooks_dir = _install_boundary_hook_project(
            tmp_path,
            zones=[{"path": "src/core/", "description": "Core", "level": "deny"}],
        )

        result = _run_boundary_hook(hooks_dir, "Edit", str(link_dir / "engine.py"))
        assert result.returncode == 2
        assert "Core" in result.stdout


class TestNewFileBypass:
    def test_allows_new_files(self, tmp_path):
        """New files in custom DENY zones keep the existing allow behavior."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "src/core/", "description": "Core", "level": "deny"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        # src/core/ exists but the target file does NOT
        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "check_boundaries.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (hooks_dir / "hook_logger.py").write_text(
            "def log_event(*a, **kw): pass", encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )

        inp = json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": str(core_dir / "new_module.py")},
        })
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_boundaries.py")],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0


class TestLiftSystem:
    def test_global_lift_does_not_bypass_self_protection(self, tmp_path):
        from datetime import datetime, timezone, timedelta

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        hooks_dir = _install_boundary_hook_project(
            tmp_path,
            lift={"lifted": ["check_boundaries.py"], "lifted_at": future},
        )

        result = _run_boundary_hook(
            hooks_dir,
            "Edit",
            str(hooks_dir / "check_boundaries.py"),
        )
        assert result.returncode == 2
        assert "self-protected" in result.stdout

    def test_lift_disables_hook(self, tmp_path):
        """When hooks_lifted.json lists this hook, it should skip all checks."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "src/core/", "description": "Core", "level": "deny"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        # Create lift file with future timestamp
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        lift = {"lifted": ["check_boundaries.py"], "lifted_at": future}
        (claude_dir / "hooks_lifted.json").write_text(
            json.dumps(lift), encoding="utf-8"
        )

        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "check_boundaries.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (hooks_dir / "hook_logger.py").write_text(
            "def log_event(*a, **kw): pass", encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )

        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(core_dir / "engine.py")},
        })
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_boundaries.py")],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "LIFTED" in result.stdout

    def test_expired_lift_still_enforces(self, tmp_path):
        """Lift with past timestamp should not disable the hook."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "src/core/", "description": "Core", "level": "deny"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        # Expired lift (2 hours ago)
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        lift = {"lifted": ["check_boundaries.py"], "lifted_at": past}
        (claude_dir / "hooks_lifted.json").write_text(
            json.dumps(lift), encoding="utf-8"
        )

        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "check_boundaries.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (hooks_dir / "hook_logger.py").write_text(
            "def log_event(*a, **kw): pass", encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )

        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(core_dir / "engine.py")},
        })
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_boundaries.py")],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "LIFTED" not in result.stdout


class TestEdgeCases:
    def test_empty_stdin(self):
        """Hook should exit 0 on invalid/empty stdin."""
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_no_file_path(self):
        """Hook should exit 0 when no file_path in tool_input."""
        inp = json.dumps({"tool_name": "Edit", "tool_input": {}})
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_unprotected_file_passes(self):
        """Files outside any protected zone should pass freely."""
        stdout, code = run_hook("Edit", "README.md")
        assert code == 0
        assert stdout == "" or "LIFTED" not in stdout


class TestFailOpen:
    def test_broken_stdin_exits_zero(self):
        """Malformed JSON on stdin should exit 0, not crash."""
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="{broken json", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_hook_never_exits_one(self):
        """Hook should only exit 0 or 2, never 1."""
        for invalid_input in ["", "{}", '{"tool_name": "Edit"}', "null"]:
            result = subprocess.run(
                [sys.executable, str(HOOK_PATH)],
                input=invalid_input, capture_output=True, text=True,
            )
            assert result.returncode in (0, 2), (
                f"Got exit code {result.returncode} for input: {invalid_input}"
            )


class TestZoneScopedLift:
    def _setup_project(self, tmp_path, zones, lift_request=None):
        """Helper: create a tmp project with config, hook copy, and optional lift."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {"protected_zones": zones}
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        if lift_request:
            (claude_dir / "lift_request.json").write_text(
                json.dumps(lift_request), encoding="utf-8"
            )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "check_boundaries.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (hooks_dir / "hook_logger.py").write_text(
            "def log_event(*a, **kw): pass", encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        return hooks_dir

    def _run(self, hooks_dir, file_path):
        inp = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": file_path},
        })
        return subprocess.run(
            [sys.executable, str(hooks_dir / "check_boundaries.py")],
            input=inp, capture_output=True, text=True,
        )

    def test_zone_lift_allows_deny_file(self, tmp_path):
        """Active zone lift should allow modification with WARN."""
        from datetime import datetime, timezone, timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        hooks_dir = self._setup_project(tmp_path,
            zones=[{"path": "src/core/", "description": "Core models", "level": "deny"}],
            lift_request={"zones": ["src/core/"], "reason": "Fix bug", "expires_at": expires, "status": "APPROVED"},
        )
        result = self._run(hooks_dir, str(core_dir / "engine.py"))
        assert result.returncode == 0
        assert "lift" in result.stdout.lower()

    def test_zone_lift_does_not_affect_other_zones(self, tmp_path):
        """A lift for zone A should NOT lift zone B."""
        from datetime import datetime, timezone, timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        auth_dir = tmp_path / "src" / "auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "login.py").write_text("# login", encoding="utf-8")

        hooks_dir = self._setup_project(tmp_path,
            zones=[
                {"path": "src/core/", "description": "Core", "level": "deny"},
                {"path": "src/auth/", "description": "Auth", "level": "deny"},
            ],
            lift_request={"zones": ["src/core/"], "reason": "Fix bug", "expires_at": expires, "status": "APPROVED"},
        )
        result = self._run(hooks_dir, str(auth_dir / "login.py"))
        assert result.returncode == 2  # Still blocked

    def test_multi_zone_lift(self, tmp_path):
        """Lift with multiple zones should allow all listed zones."""
        from datetime import datetime, timezone, timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")
        shared_dir = tmp_path / "shared" / "utils"
        shared_dir.mkdir(parents=True)
        (shared_dir / "helpers.py").write_text("# helpers", encoding="utf-8")

        hooks_dir = self._setup_project(tmp_path,
            zones=[
                {"path": "src/core/", "description": "Core", "level": "deny"},
                {"path": "shared/utils/", "description": "Shared utils", "level": "deny"},
            ],
            lift_request={
                "zones": ["src/core/", "shared/utils/"],
                "reason": "Refactoring",
                "expires_at": expires,
                "status": "APPROVED",
            },
        )
        r1 = self._run(hooks_dir, str(core_dir / "engine.py"))
        r2 = self._run(hooks_dir, str(shared_dir / "helpers.py"))
        assert r1.returncode == 0
        assert r2.returncode == 0

    def test_self_protected_files_never_liftable(self):
        """Even with a zone lift, self-protected files remain blocked."""
        stdout, code = run_hook("Edit", "check_boundaries.py")
        assert code == 2
        assert "self-protected" in stdout

    def test_expired_zone_lift_still_blocks(self, tmp_path):
        """Expired zone lift should not allow modifications."""
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        hooks_dir = self._setup_project(tmp_path,
            zones=[{"path": "src/core/", "description": "Core", "level": "deny"}],
            lift_request={"zones": ["src/core/"], "reason": "Old", "expires_at": past, "status": "APPROVED"},
        )
        result = self._run(hooks_dir, str(core_dir / "engine.py"))
        assert result.returncode == 2

    def test_no_lift_request_unchanged(self):
        """Without lift_request.json, behavior is identical to before."""
        stdout, code = run_hook("Edit", "README.md")
        assert code == 0

    def test_deny_message_includes_lift_instructions(self, tmp_path):
        """DENY block message should include instructions for requesting a lift."""
        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "engine.py").write_text("# engine", encoding="utf-8")

        hooks_dir = self._setup_project(tmp_path,
            zones=[{"path": "src/core/", "description": "Core", "level": "deny"}],
        )
        result = self._run(hooks_dir, str(core_dir / "engine.py"))
        assert result.returncode == 2
        assert "request_lift.py" in result.stdout


class TestLiftApproval:
    def test_non_interactive_approve_is_blocked(self, tmp_path):
        (tmp_path / ".git").mkdir()
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        lift_path = control_dir / "lift_request.json"
        lift_path.write_text(
            json.dumps({
                "status": "PENDING",
                "zones": ["src/core/"],
                "file": "src/core/engine.py",
                "reason": "test",
                "approval_token": "abc123",
                "requested_at": "2026-06-16T00:00:00+00:00",
                "expires_at": "",
            }),
            encoding="utf-8",
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "request_lift.py").write_text(
            REQUEST_LIFT_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (hooks_dir / "hook_utils.py").write_text(
            HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "request_lift.py"), "--approve"],
            input="abc123\n",
            capture_output=True,
            text=True,
        )

        payload = json.loads(lift_path.read_text(encoding="utf-8"))
        assert result.returncode == 2
        assert "interactive human terminal" in result.stdout
        assert payload["status"] == "PENDING"
