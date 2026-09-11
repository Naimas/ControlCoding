"""Tests for check_file_organization.py - file organization enforcement hook."""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "templates" / "hooks" / "check_file_organization.py"


def run_hook(file_path, tool_name="Write", project_root=None):
    """Run check_file_organization.py with simulated stdin."""
    inp = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}})
    env = None
    if project_root is not None:
        env = dict(**os.environ, CC_PROJECT_ROOT=str(project_root))
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=inp, capture_output=True, text=True, env=env,
    )
    return result.stdout.strip(), result.returncode


class TestBlocksMisplacedFiles:
    """Files matching patterns but in wrong directory should be blocked."""

    def test_blocks_plan_in_root(self):
        """Plan file in project root should be blocked."""
        stdout, code = run_hook("/project/plan-v1-draft.json")
        assert code == 2
        assert "dev/plans" in stdout

    def test_blocks_plan_in_devlog_root(self):
        """Plan file in devlog/ root (not devlog/plans/) should be blocked."""
        stdout, code = run_hook("/project/devlog/plan-v1-approved.json")
        assert code == 2
        assert "dev/plans" in stdout

    def test_blocks_design_in_root(self):
        """Design doc in project root should be blocked."""
        stdout, code = run_hook("/project/design-rendering.md")
        assert code == 2
        assert "dev/design" in stdout

    def test_blocks_reasoning_in_root(self):
        """Reasoning log in project root should be blocked."""
        stdout, code = run_hook("/project/reasoning-phase-1.md")
        assert code == 2
        assert "devlog/reasoning" in stdout

    def test_blocks_criteria_in_root(self):
        """Criteria file in project root should be blocked."""
        stdout, code = run_hook("/project/criteria-v1.md")
        assert code == 2
        assert "dev/criteria" in stdout

    def test_blocks_acceptance_in_root(self):
        """Acceptance criteria file should be blocked outside dev/criteria/."""
        stdout, code = run_hook("/project/acceptance-v2.md")
        assert code == 2
        assert "dev/criteria" in stdout

    def test_blocks_reasoning_md_bare(self):
        """Bare reasoning.md in root should be blocked."""
        stdout, code = run_hook("/project/reasoning.md")
        assert code == 2
        assert "devlog/reasoning" in stdout

    def test_block_message_suggests_correct_path(self):
        """Block message should include the suggested correct path."""
        stdout, code = run_hook("/project/plan-v3-draft.json")
        assert code == 2
        data = json.loads(stdout)
        assert "dev/plans/plan-v3-draft.json" in data["reason"]


class TestAllowsCorrectPaths:
    """Files in the correct devlog/ subdirectory should be allowed."""

    def test_allows_plan_in_plans(self):
        """Plan file in devlog/plans/ should be allowed."""
        _, code = run_hook("/project/devlog/plans/plan-v1-draft.json")
        assert code == 0

    def test_allows_design_in_design(self):
        """Design doc in devlog/design/ should be allowed."""
        _, code = run_hook("/project/devlog/design/design-rendering.md")
        assert code == 0

    def test_allows_reasoning_in_reasoning(self):
        """Reasoning log in devlog/reasoning/ should be allowed."""
        _, code = run_hook("/project/devlog/reasoning/reasoning-phase-1.md")
        assert code == 0

    def test_allows_criteria_in_criteria(self):
        """Legacy criteria file in devlog/criteria/ should be allowed."""
        _, code = run_hook("/project/devlog/criteria/criteria-v1.md")
        assert code == 0

    def test_allows_criteria_in_dev_criteria(self):
        """Criteria file in dev/criteria/ should be allowed."""
        _, code = run_hook("/project/dev/criteria/criteria-v1.md")
        assert code == 0

    def test_allows_archived_plan(self):
        """Plan file in devlog/archive/ should be allowed."""
        _, code = run_hook("/project/devlog/archive/2026-03-15-plan-v1-draft.json")
        assert code == 0

    def test_allows_archived_design(self):
        """Design doc in devlog/archive/ should be allowed."""
        _, code = run_hook("/project/devlog/archive/design-old.md")
        assert code == 0

    def test_allows_plan_in_dev_plans(self):
        """Plan file in dev/plans/ should be allowed."""
        _, code = run_hook("/project/dev/plans/03_DEV_AuthorizedInterfaces_InProgress.md")
        assert code == 0

    def test_allows_design_in_dev_design(self):
        """Design doc in dev/design/ should be allowed."""
        _, code = run_hook("/project/dev/design/design-base-agent.md")
        assert code == 0

    def test_allows_plan_in_dev_plans_archive(self):
        """Archived plan in dev/plans/archive/ should be allowed."""
        _, code = run_hook("/project/dev/plans/archive/2026-03-23-plan-old.md")
        assert code == 0

    def test_allows_design_in_dev_design_archive(self):
        """Archived design doc in dev/design/archive/ should be allowed."""
        _, code = run_hook("/project/dev/design/archive/2026-03-16-design-old.md")
        assert code == 0

    def test_allows_plan_in_dev_plans_deprecated(self):
        """Deprecated plan in dev/plans/deprecated/ should be allowed."""
        _, code = run_hook("/project/dev/plans/deprecated/03_DEV_OldPlan_Deprecated.md")
        assert code == 0

    def test_allows_design_in_dev_design_deprecated(self):
        """Deprecated design doc in dev/design/deprecated/ should be allowed."""
        _, code = run_hook("/project/dev/design/deprecated/04_DSN_OldDesign_Deprecated.md")
        assert code == 0


class TestAllowsNonPatternFiles:
    """Files that don't match any pattern should always be allowed."""

    def test_allows_regular_python(self):
        """Regular Python file should be allowed anywhere."""
        _, code = run_hook("/project/src/main.py")
        assert code == 0

    def test_allows_readme(self):
        """README should be allowed in root."""
        _, code = run_hook("/project/README.md")
        assert code == 0

    def test_allows_claude_md(self):
        """CLAUDE.md should be allowed."""
        _, code = run_hook("/project/CLAUDE.md")
        assert code == 0

    def test_allows_index_md(self):
        """devlog/index.md should be allowed (not a pattern match)."""
        _, code = run_hook("/project/devlog/index.md")
        assert code == 0

    def test_allows_wiring_matrix(self):
        """devlog/wiring-matrix.md should be allowed."""
        _, code = run_hook("/project/devlog/wiring-matrix.md")
        assert code == 0

    def test_allows_criteria_json(self):
        """criteria.json (machine-readable) should be allowed anywhere."""
        _, code = run_hook("/project/criteria.json")
        assert code == 0

    def test_allows_design_py(self):
        """design_utils.py should not be caught by design pattern."""
        _, code = run_hook("/project/src/design_utils.py")
        assert code == 0


class TestOnlyWrite:
    """Hook should only trigger on Write, not Edit."""

    def test_ignores_edit(self):
        """Edit tool should always be allowed (modifying existing files)."""
        _, code = run_hook("/project/plan-v1-draft.json", tool_name="Edit")
        assert code == 0

    def test_ignores_bash(self):
        """Bash tool should always be allowed."""
        _, code = run_hook("/project/plan-v1-draft.json", tool_name="Bash")
        assert code == 0


class TestEdgeCases:
    def test_empty_stdin(self):
        """Empty stdin should exit 0."""
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_no_file_path(self):
        """Missing file_path should exit 0."""
        inp = json.dumps({"tool_name": "Write", "tool_input": {}})
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_broken_json(self):
        """Malformed JSON should exit 0."""
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="{broken", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_hook_never_exits_one(self):
        """Hook should only exit 0 or 2, never 1."""
        for inp in ["", "{}", '{"tool_name": "Write"}', "null"]:
            result = subprocess.run(
                [sys.executable, str(HOOK_PATH)],
                input=inp, capture_output=True, text=True,
            )
            assert result.returncode in (0, 2), (
                f"Got exit code {result.returncode} for input: {inp}"
            )


class TestDocumentationMode:
    def test_project_managed_mode_disables_enforcement_from_canonical_config(self, tmp_path):
        """Canonical cc_config.json should disable the hook in project_managed mode."""
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps({"documentation_mode": "project_managed"}),
            encoding="utf-8",
        )

        _, code = run_hook(
            str(tmp_path / "plan-v1-draft.json"),
            project_root=tmp_path,
        )
        assert code == 0

    def test_project_managed_mode_disables_enforcement(self, tmp_path):
        """project_managed repos keep their own documentation layout."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "cc_config.json").write_text(
            json.dumps({"documentation_mode": "project_managed"}),
            encoding="utf-8",
        )

        _, code = run_hook(
            str(tmp_path / "plan-v1-draft.json"),
            project_root=tmp_path,
        )
        assert code == 0

    def test_managed_mode_keeps_enforcement(self, tmp_path):
        """managed repos should still be blocked for misplaced files."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "cc_config.json").write_text(
            json.dumps({"documentation_mode": "managed"}),
            encoding="utf-8",
        )

        stdout, code = run_hook(
            str(tmp_path / "plan-v1-draft.json"),
            project_root=tmp_path,
        )
        assert code == 2
        assert "dev/plans" in stdout
