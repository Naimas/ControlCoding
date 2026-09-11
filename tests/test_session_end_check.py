"""Unit tests for session_end_check.py - session end integrity verification."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).parent.parent / "templates" / "hooks" / "session_end_check.py"
HOOK_UTILS_PATH = Path(__file__).parent.parent / "templates" / "hooks" / "hook_utils.py"


def _copy_hook_utils(hooks_dir):
    """Copy hook_utils.py to the test hooks directory."""
    (hooks_dir / "hook_utils.py").write_text(
        HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class TestDenyFileDetection:
    def test_creates_canonical_baseline_on_first_run(self, tmp_path):
        """First run should create deny_hashes.json in the canonical control plane when present."""
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "core.py", "description": "Core", "level": "deny"}
            ]
        }
        (control_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (control_dir / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / "core.py").write_text("original content", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Baseline created" in result.stdout
        assert (control_dir / "deny_hashes.json").exists()

    def test_creates_baseline_on_first_run(self, tmp_path):
        """First run should create deny_hashes.json baseline."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "core.py", "description": "Core", "level": "deny"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / "core.py").write_text("original content", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Baseline created" in result.stdout
        assert (claude_dir / "deny_hashes.json").exists()

    def test_detects_modified_deny_file(self, tmp_path):
        """Second run should detect and warn about modifications to DENY files (no rollback)."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "core.py", "description": "Core", "level": "deny"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / "core.py").write_text("original content", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hook_content = HOOK_PATH.read_text(encoding="utf-8")
        (hooks_dir / "session_end_check.py").write_text(hook_content, encoding="utf-8")
        _copy_hook_utils(hooks_dir)

        # First run: create baseline
        subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )

        # Modify the DENY file
        (tmp_path / "core.py").write_text("MODIFIED content", encoding="utf-8")

        # Second run: should detect change and warn (not rollback)
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "WARNING" in result.stdout
        assert "review" in result.stdout.lower()
        # Verify the file was NOT rolled back (warn-only, no auto-restore)
        assert (tmp_path / "core.py").read_text(encoding="utf-8") == "MODIFIED content"

    def test_intact_files_pass(self, tmp_path):
        """Unmodified DENY files should pass cleanly."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "core.py", "description": "Core", "level": "deny"}
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / "core.py").write_text("stable content", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        # First run
        subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        # Second run without changes
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "intact" in result.stdout.lower()


class TestConfigLoading:
    def test_loads_from_cc_config(self, tmp_path):
        """Should load DENY patterns from cc_config.json."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        config = {
            "protected_zones": [
                {"path": "important.py", "description": "Important", "level": "deny"},
                {"path": "shared/", "description": "Shared", "level": "warn"},
            ]
        }
        (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        # Only the DENY file should be tracked, not WARN
        (tmp_path / "important.py").write_text("important", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert "Baseline created for 1" in result.stdout

    def test_no_config_no_crash(self, tmp_path):
        """Should exit cleanly when no cc_config.json exists."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0


class TestLiftSystem:
    def test_lift_skips_check(self, tmp_path):
        """When lifted, session_end_check should skip entirely."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        lift = {"lifted": ["session_end_check.py"], "lifted_at": future}
        (claude_dir / "hooks_lifted.json").write_text(
            json.dumps(lift), encoding="utf-8"
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "LIFTED" in result.stdout


class TestZoneLiftReporting:
    def test_reports_active_lift_request(self, tmp_path):
        """Session end should report active lift_request.json."""
        from datetime import datetime, timezone, timedelta

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        lift = {
            "zones": ["src/core/"],
            "reason": "Bug fix",
            "expires_at": expires,
        }
        (claude_dir / "lift_request.json").write_text(
            json.dumps(lift), encoding="utf-8"
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "ACTIVE" in result.stdout
        assert "src/core/" in result.stdout

    def test_reports_expired_lift_request(self, tmp_path):
        """Session end should flag expired lift_request.json."""
        from datetime import datetime, timezone, timedelta

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        lift = {
            "zones": ["src/core/"],
            "reason": "Old fix",
            "expires_at": past,
        }
        (claude_dir / "lift_request.json").write_text(
            json.dumps(lift), encoding="utf-8"
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "EXPIRED" in result.stdout

    def test_no_lift_request_no_report(self, tmp_path):
        """Without lift_request.json, no lift report."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "session_end_check.py").write_text(
            HOOK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _copy_hook_utils(hooks_dir)

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "session_end_check.py")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "lift" not in result.stdout.lower()
