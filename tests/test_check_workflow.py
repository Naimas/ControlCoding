"""Unit tests for check_workflow.py - workflow enforcement hook.

Tests follow the subprocess invocation pattern used by test_check_boundaries.py
and test_check_dangerous_commands.py: run the actual hook script and verify
exit codes + stdout output.

check_workflow.py tracks state in .claude/workflow_state.json and detects
artifacts (consult_log.jsonl, screenshots/, devlog/) to decide whether
workflow prerequisites are met.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).parent.parent / "templates" / "hooks" / "check_workflow.py"
HOOK_UTILS_PATH = Path(__file__).parent.parent / "templates" / "hooks" / "hook_utils.py"


def setup_project(tmp_path):
    """Create minimal project structure so find_project_root() resolves to tmp_path.

    The hook walks up from __file__ looking for .git/ - placing it at tmp_path
    and the hook at tmp_path/hooks/ puts the marker one level up from the script.
    """
    (tmp_path / ".git").mkdir()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hook_content = HOOK_PATH.read_text(encoding="utf-8")
    (hooks_dir / "check_workflow.py").write_text(hook_content, encoding="utf-8")
    (hooks_dir / "hook_utils.py").write_text(
        HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # Provide a no-op hook_logger so the import doesn't fail
    (hooks_dir / "hook_logger.py").write_text(
        "def log_event(*a, **kw): pass", encoding="utf-8"
    )
    return hooks_dir


def run_hook(hooks_dir, tool_name, file_path):
    """Run check_workflow.py with simulated stdin, return (stdout, exit_code)."""
    inp = json.dumps({
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    })
    result = subprocess.run(
        [sys.executable, str(hooks_dir / "check_workflow.py")],
        input=inp, capture_output=True, text=True,
    )
    return result.stdout.strip(), result.returncode


def write_state(tmp_path, state):
    """Write a pre-seeded workflow_state.json."""
    state_path = tmp_path / ".claude" / "workflow_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")


def read_state(tmp_path):
    """Read workflow_state.json back."""
    state_path = tmp_path / ".claude" / "workflow_state.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return None


class TestPlanBeforeCode:
    """plan_before_code: Write/Edit to src/** without planner consultation."""

    def test_warn_on_src_write_without_plan(self, tmp_path):
        """First src/ write without consult_log entry should WARN."""
        hooks_dir = setup_project(tmp_path)
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        assert "planning" in stdout.lower() or "planner" in stdout.lower()

    def test_allow_with_planner_consultation(self, tmp_path):
        """src/ write WITH a planner entry in consult_log.jsonl should allow silently."""
        hooks_dir = setup_project(tmp_path)
        # Create consult_log with a planner entry
        log_path = tmp_path / ".claude" / "consult_log.jsonl"
        entry = {"role": "planner", "timestamp": "2026-03-08T10:00:00Z", "query": "plan"}
        log_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        stdout, code = run_hook(hooks_dir, "Edit", str(tmp_path / "src" / "main.py"))
        assert code == 0
        assert "planner" not in stdout.lower()

    def test_allow_with_planner_consultation_in_canonical_control_plane(self, tmp_path):
        """Canonical consult_log.jsonl should satisfy the planner check."""
        hooks_dir = setup_project(tmp_path)
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "settings.json").write_text("{}", encoding="utf-8")
        log_path = control_dir / "consult_log.jsonl"
        entry = {"role": "planner", "timestamp": "2026-03-08T10:00:00Z", "query": "plan"}
        log_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        stdout, code = run_hook(hooks_dir, "Edit", str(tmp_path / "src" / "main.py"))
        assert code == 0
        assert "planner" not in stdout.lower()

    def test_allow_with_architect_consultation(self, tmp_path):
        """Architect role also satisfies the planner check."""
        hooks_dir = setup_project(tmp_path)
        log_path = tmp_path / ".claude" / "consult_log.jsonl"
        entry = {"role": "architect", "timestamp": "2026-03-08T10:00:00Z"}
        log_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "module.py"))
        assert code == 0
        assert "planner" not in stdout.lower()

    def test_no_warn_for_non_src_file(self, tmp_path):
        """Write to file outside src/ should not trigger plan_before_code."""
        hooks_dir = setup_project(tmp_path)
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "README.md"))
        assert code == 0
        assert stdout == ""


class TestSingleWarningPerSession:
    """plan_before_code warns only once per session (tracked in warned_rules)."""

    def test_second_src_write_silent_after_first_warning(self, tmp_path):
        """After first WARN for plan_before_code, subsequent writes allow silently."""
        hooks_dir = setup_project(tmp_path)

        # First write: should warn
        stdout1, code1 = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "a.py"))
        assert code1 == 0
        assert "planner" in stdout1.lower()

        # Second write: should be silent (already warned)
        stdout2, code2 = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "b.py"))
        assert code2 == 0
        assert stdout2 == ""


class TestVisualCheckAfterRendering:
    """visual_check_after_rendering: after 3+ renderer writes without screenshots."""

    def test_warn_on_third_renderer_write_without_screenshots(self, tmp_path):
        """3rd renderer write without screenshots should WARN (after_count=3)."""
        hooks_dir = setup_project(tmp_path)

        # Writes 1 and 2: no warning expected for visual check
        for i in range(2):
            stdout, code = run_hook(
                hooks_dir, "Write", str(tmp_path / "src" / "renderer" / f"file{i}.py")
            )
            assert code == 0

        # Write 3: threshold reached, visual_checked_recently=False, no screenshots
        stdout, code = run_hook(
            hooks_dir, "Write", str(tmp_path / "src" / "renderer" / "file2.py")
        )
        assert code == 0
        assert "visual" in stdout.lower() or "rendering" in stdout.lower()

    def test_no_warn_below_threshold(self, tmp_path):
        """2nd renderer write should not trigger visual check warning."""
        hooks_dir = setup_project(tmp_path)

        for i in range(2):
            stdout, code = run_hook(
                hooks_dir, "Write", str(tmp_path / "src" / "renderer" / f"f{i}.py")
            )
            assert code == 0
            # Should not contain visual check warning
            if "visual" in stdout.lower():
                pytest.fail(f"Unexpected visual warning on write {i+1}")

    def test_allow_with_screenshots(self, tmp_path):
        """3rd renderer write WITH screenshots/ files should allow silently."""
        hooks_dir = setup_project(tmp_path)

        # Create a screenshot artifact
        screenshots_dir = tmp_path / "screenshots"
        screenshots_dir.mkdir()
        (screenshots_dir / "frame_001.png").write_text("fake", encoding="utf-8")

        # 3 writes - should not warn because screenshots exist
        for i in range(3):
            run_hook(hooks_dir, "Write", str(tmp_path / "src" / "renderer" / f"f{i}.py"))

        stdout, code = run_hook(
            hooks_dir, "Write", str(tmp_path / "src" / "renderer" / "f3.py")
        )
        assert code == 0
        # screenshots exist, so visual_checked_recently check passes
        assert "visual" not in stdout.lower()


class TestCheckpointFrequency:
    """checkpoint_frequency: after 20+ src writes without devlog entries."""

    def test_warn_on_20th_src_write_without_devlog(self, tmp_path):
        """20th src/ write without devlog should WARN (after_count=20)."""
        hooks_dir = setup_project(tmp_path)

        # Pre-seed state to simulate 19 prior writes and checkpoint_recent=False
        write_state(tmp_path, {
            "planner_consulted": False,
            "visual_checked_recently": True,
            "checkpoint_recent": False,
            "renderer_writes": 0,
            "total_src_writes": 19,
            "warned_rules": ["plan_before_code"],
        })

        # 20th write: total_src_writes becomes 20, >= after_count(20)
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        assert "checkpoint" in stdout.lower() or "20+" in stdout

    def test_no_warn_below_checkpoint_threshold(self, tmp_path):
        """19th src/ write should not trigger checkpoint warning."""
        hooks_dir = setup_project(tmp_path)

        write_state(tmp_path, {
            "planner_consulted": False,
            "visual_checked_recently": True,
            "checkpoint_recent": False,
            "renderer_writes": 0,
            "total_src_writes": 18,
            "warned_rules": ["plan_before_code"],
        })

        # 19th write: total_src_writes becomes 19, < 20
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        assert "checkpoint" not in stdout.lower()

    def test_allow_with_devlog_entries(self, tmp_path):
        """20th src/ write WITH devlog entries should allow silently."""
        hooks_dir = setup_project(tmp_path)

        # Create devlog artifact
        devlog_dir = tmp_path / "devlog"
        devlog_dir.mkdir()
        (devlog_dir / "session_001.md").write_text("# Session", encoding="utf-8")

        write_state(tmp_path, {
            "planner_consulted": False,
            "visual_checked_recently": True,
            "checkpoint_recent": False,
            "renderer_writes": 0,
            "total_src_writes": 19,
            "warned_rules": ["plan_before_code"],
        })

        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        # devlog exists, so check_checkpoint_recent returns True
        assert "checkpoint" not in stdout.lower()


class TestEdgeCases:
    """Fail-open behavior and edge cases."""

    def test_empty_stdin(self, tmp_path):
        """Empty stdin should exit 0 (fail-open)."""
        hooks_dir = setup_project(tmp_path)
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_workflow.py")],
            input="", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_malformed_json_stdin(self, tmp_path):
        """Malformed JSON on stdin should exit 0 (fail-open)."""
        hooks_dir = setup_project(tmp_path)
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_workflow.py")],
            input="{broken json", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_missing_file_path(self, tmp_path):
        """Missing file_path in tool_input should exit 0."""
        hooks_dir = setup_project(tmp_path)
        inp = json.dumps({"tool_name": "Edit", "tool_input": {}})
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_workflow.py")],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_no_claude_directory(self, tmp_path):
        """Project without .claude/ should still work (state write creates it)."""
        hooks_dir = setup_project(tmp_path)
        # The setup already creates .claude/, but the hook gracefully handles
        # missing state files by using defaults
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "x.py"))
        assert code == 0


class TestFailOpen:
    """Verify the never-exit-1 invariant shared by all CC hooks."""

    def test_hook_never_exits_one(self, tmp_path):
        """Hook should only exit 0 or 2, never 1."""
        hooks_dir = setup_project(tmp_path)
        for invalid_input in ["", "{}", '{"tool_name": "Edit"}', "null", "{broken"]:
            result = subprocess.run(
                [sys.executable, str(hooks_dir / "check_workflow.py")],
                input=invalid_input, capture_output=True, text=True,
            )
            assert result.returncode in (0, 2), (
                f"Got exit code {result.returncode} for input: {invalid_input}\n"
                f"stderr: {result.stderr}"
            )

    def test_null_json_should_fail_open(self, tmp_path):
        """Valid JSON 'null' should exit 0 (fail-open), not crash."""
        hooks_dir = setup_project(tmp_path)
        result = subprocess.run(
            [sys.executable, str(hooks_dir / "check_workflow.py")],
            input="null", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_all_scenario_exit_codes(self, tmp_path):
        """Verify exit codes across all test scenarios are 0 or 2."""
        hooks_dir = setup_project(tmp_path)
        # Write to src/ (will warn for plan_before_code)
        _, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "a.py"))
        assert code in (0, 2)
        # Write to non-src/
        _, code = run_hook(hooks_dir, "Write", str(tmp_path / "docs" / "a.md"))
        assert code in (0, 2)
        # Write to renderer/
        _, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "renderer" / "r.py"))
        assert code in (0, 2)


class TestConfigLoading:
    """Test that workflow_rules load from cc_config.json with inline fallback."""

    def test_uses_config_workflow_rules(self, tmp_path):
        """When cc_config.json has workflow_rules, those are used."""
        hooks_dir = setup_project(tmp_path)
        # Config with a custom rule that triggers on docs/** instead of src/**
        config = {
            "workflow_rules": [
                {
                    "name": "custom_rule",
                    "trigger": {"tools": ["Write", "Edit"], "path_patterns": ["docs/**"]},
                    "check": "planner_consulted",
                    "after_count": 0,
                    "mode": "warn",
                    "message": "Custom: plan before docs",
                }
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # Write to docs/ should trigger custom rule
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "docs" / "readme.md"))
        assert code == 0
        assert "custom" in stdout.lower()

    def test_config_replaces_inline_rules(self, tmp_path):
        """Config rules REPLACE inline ones, not extend."""
        hooks_dir = setup_project(tmp_path)
        # Config with a rule only for docs/ - src/ should NOT trigger
        config = {
            "workflow_rules": [
                {
                    "name": "docs_only",
                    "trigger": {"tools": ["Write"], "path_patterns": ["docs/**"]},
                    "check": "planner_consulted",
                    "after_count": 0,
                    "mode": "warn",
                    "message": "Docs only rule",
                }
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # src/ write should be silent (inline plan_before_code not active)
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        assert stdout == ""

    def test_fallback_when_no_config(self, tmp_path):
        """Without cc_config.json, inline WORKFLOW_RULES are used."""
        hooks_dir = setup_project(tmp_path)
        # No cc_config.json - inline plan_before_code should trigger
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        assert "planner" in stdout.lower()

    def test_fallback_when_config_has_no_section(self, tmp_path):
        """Config exists but lacks workflow_rules -> inline fallback."""
        hooks_dir = setup_project(tmp_path)
        config = {"protected_zones": {"deny": ["core/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        assert "planner" in stdout.lower()

    def test_malformed_config_falls_back(self, tmp_path):
        """Malformed cc_config.json -> inline fallback."""
        hooks_dir = setup_project(tmp_path)
        (tmp_path / ".claude" / "cc_config.json").write_text(
            "{broken json", encoding="utf-8"
        )
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0
        assert "planner" in stdout.lower()

    def test_config_rule_with_deny_mode(self, tmp_path):
        """Config rule with mode=deny should block (exit 2)."""
        hooks_dir = setup_project(tmp_path)
        config = {
            "workflow_rules": [
                {
                    "name": "strict_plan",
                    "trigger": {"tools": ["Write"], "path_patterns": ["src/**"]},
                    "check": "planner_consulted",
                    "after_count": 0,
                    "mode": "deny",
                    "message": "STRICT: must plan first",
                }
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 2
        assert "strict" in stdout.lower()

    def test_invalid_rules_skipped(self, tmp_path):
        """Rules missing required fields are silently skipped."""
        hooks_dir = setup_project(tmp_path)
        config = {
            "workflow_rules": [
                {"name": "no_check"},
                {"check": "planner_consulted"},
                "not_a_dict",
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # All rules invalid -> effectively no rules -> silent allow
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code == 0


class TestExceptionHandler:
    """ENG-003: hook must have exception handler, never exit 1."""

    def test_corrupted_state_file_does_not_crash(self, tmp_path):
        """Corrupted workflow_state.json should not crash the hook."""
        hooks_dir = setup_project(tmp_path)
        state_path = tmp_path / ".claude" / "workflow_state.json"
        state_path.write_text("{corrupted json!!!", encoding="utf-8")
        # Should not crash - state defaults are used
        stdout, code = run_hook(hooks_dir, "Write", str(tmp_path / "src" / "app.py"))
        assert code in (0, 2), f"Got exit code {code}, expected 0 or 2"
