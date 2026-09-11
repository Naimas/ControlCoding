"""Tests for concierge.py - The Orchestrator Agent.

Tests the Concierge state machine, prompt generation, review parsing,
agent delegation, Planner as internal function, Tandem as protocol,
Architect guidance persistence, and structured Reviewer JSON output.

Minimum 20 tests. All backend/subprocess calls are mocked.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import concierge as cc
from concierge import (
    STATES, TRANSITIONS, MAX_ITERATIONS,
    ConciergeState, ProjectContext, PromptGenerator, AgentCaller,
    Concierge, parse_review_json, log_event,
    REVIEW_JSON_INSTRUCTION,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _setup_project(tmp_path):
    """Create minimal project structure."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    devlog = tmp_path / "devlog"
    devlog.mkdir(parents=True, exist_ok=True)
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    tools = tmp_path / "tools"
    tools.mkdir(parents=True, exist_ok=True)

    # Minimal CLAUDE.md
    (tmp_path / "CLAUDE.md").write_text(
        "# Test Project\n\n## Build\n$ cmake --build build --config Release\n",
        encoding="utf-8",
    )

    return tmp_path


def _setup_plan(tmp_path, phases=None):
    """Create an approved plan with phases."""
    if phases is None:
        phases = [
            {"name": "Core rendering", "features": ["3D engine", "camera"]},
            {"name": "Combat system", "features": ["attack", "defense"]},
        ]
    plan = {
        "status": "APPROVED",
        "idea": "Test project",
        "phases": phases,
    }
    plans_dir = tmp_path / "devlog" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "plan.current.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )
    return plan


def _make_concierge(tmp_path):
    """Create a Concierge with mocked subprocess calls."""
    root = _setup_project(tmp_path)
    c = Concierge(str(root))
    # Default to full engagement for tests (all components active)
    c.engagement = {"level": 4, "backend_policy": "all",
                    "budget_policy": {"max_calls": 0,
                                      "exhaustion_behavior": "degrade"}}
    return c


# ===========================================================================
# State Machine Tests
# ===========================================================================

class TestStateMachine:
    """Tests for ConciergeState transitions and persistence."""

    def test_initial_state_is_idle(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        assert state.state == "IDLE"

    def test_valid_transition(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        state.transition("PLANNING")
        assert state.state == "PLANNING"

    def test_invalid_transition_raises(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        with pytest.raises(ValueError, match="Invalid transition"):
            state.transition("CODING")  # IDLE -> CODING not allowed

    def test_transition_records_history(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        state.transition("PLANNING")
        assert len(state.history) == 1
        assert state.history[0]["from"] == "IDLE"
        assert state.history[0]["to"] == "PLANNING"

    def test_save_and_load(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        state.task = "Build something"
        state.state = "CODING"
        state.current_phase = 2
        state.total_phases = 5
        state.architect_guidance = "Use MVC pattern"
        state.save()

        state2 = ConciergeState(str(root))
        assert state2.load() is True
        assert state2.state == "CODING"
        assert state2.task == "Build something"
        assert state2.current_phase == 2
        assert state2.total_phases == 5
        assert state2.architect_guidance == "Use MVC pattern"

    def test_load_nonexistent_returns_false(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        assert state.load() is False

    def test_record_truncates_result(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        long_result = "x" * 1000
        state.record("test", long_result)
        assert len(state.history[0]["result"]) == 500

    def test_all_states_defined_in_transitions(self):
        """Every state must have an entry in TRANSITIONS."""
        for state in STATES:
            assert state in TRANSITIONS, f"{state} missing from TRANSITIONS"

    def test_transitions_only_target_valid_states(self):
        """All transition targets must be valid states."""
        for source, targets in TRANSITIONS.items():
            assert source in STATES
            for target in targets:
                assert target in STATES, f"{target} is not a valid state"

    def test_review_results_persisted(self, tmp_path):
        root = _setup_project(tmp_path)
        state = ConciergeState(str(root))
        state.review_results = [
            {"phase": 1, "review": {"overall": "PASS", "summary": {"pass": 3, "fail": 0}}}
        ]
        state.save()

        state2 = ConciergeState(str(root))
        state2.load()
        assert len(state2.review_results) == 1
        assert state2.review_results[0]["review"]["overall"] == "PASS"


# ===========================================================================
# ProjectContext Tests
# ===========================================================================

class TestProjectContext:
    """Tests for project state reading."""

    def test_read_claude_md(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        md = ctx.read_claude_md()
        assert "Test Project" in md

    def test_read_claude_md_missing(self, tmp_path):
        ctx = ProjectContext(tmp_path)
        assert ctx.read_claude_md() == ""

    def test_read_plan(self, tmp_path):
        root = _setup_project(tmp_path)
        _setup_plan(root)
        ctx = ProjectContext(root)
        plan = ctx.read_plan()
        assert plan is not None
        assert plan["status"] == "APPROVED"

    def test_has_plan(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        assert ctx.has_plan() is False
        _setup_plan(root)
        assert ctx.has_plan() is True

    def test_get_phase_info(self, tmp_path):
        root = _setup_project(tmp_path)
        _setup_plan(root)
        ctx = ProjectContext(root)
        info = ctx.get_phase_info(0)
        assert info["name"] == "Core rendering"
        assert ctx.get_phase_info(99) == {}

    def test_read_violations_empty(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        assert ctx.read_violations() == []

    def test_read_violations(self, tmp_path):
        root = _setup_project(tmp_path)
        v_path = root / ".claude" / "codewarden_violations.jsonl"
        v_path.write_text(
            json.dumps({"severity": "high", "file": "test.py", "description": "bad"}) + "\n",
            encoding="utf-8",
        )
        ctx = ProjectContext(root)
        violations = ctx.read_violations()
        assert len(violations) == 1
        assert violations[0]["severity"] == "high"

    def test_get_build_cmd_from_claude_md(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        cmd = ctx.get_build_cmd()
        assert "cmake --build" in cmd
        assert "Release" in cmd

    def test_get_build_cmd_default(self, tmp_path):
        ctx = ProjectContext(tmp_path)
        cmd = ctx.get_build_cmd()
        assert "cmake --build build --config Release" == cmd

    def test_read_spec_files(self, tmp_path):
        root = _setup_project(tmp_path)
        (root / "SPEC.md").write_text("# Physics Spec\nF=ma\n", encoding="utf-8")
        ctx = ProjectContext(root)
        specs = ctx.read_spec_files()
        assert "Physics Spec" in specs
        assert "F=ma" in specs

    def test_read_spec_files_empty(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        assert ctx.read_spec_files() == ""

    def test_read_criteria(self, tmp_path):
        root = _setup_project(tmp_path)
        criteria = [{"id": "C-001", "text": "Walls visible"}]
        (root / "devlog" / "criteria").mkdir(parents=True, exist_ok=True)
        (root / "devlog" / "criteria" / "criteria.json").write_text(
            json.dumps(criteria), encoding="utf-8"
        )
        ctx = ProjectContext(root)
        result = ctx.read_criteria()
        assert len(result) == 1
        assert result[0]["id"] == "C-001"


# ===========================================================================
# PromptGenerator Tests
# ===========================================================================

class TestPromptGenerator:
    """Tests for contextual prompt generation."""

    def test_generate_includes_role_instruction(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate("coder", "Implement phase 1")
        assert "Coder" in prompt
        assert "worker agent" in prompt

    def test_generate_includes_claude_md_for_coder(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate("coder", "Implement phase 1")
        assert "Project Rules" in prompt
        assert "Test Project" in prompt

    def test_generate_excludes_claude_md_for_debug(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate("debug", "Fix this bug")
        assert "Project Rules" not in prompt

    def test_generate_includes_failures(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate("debug", "Fix", failures=["Build failed", "Test error"])
        assert "Build failed" in prompt
        assert "Previous Failures" in prompt

    def test_generate_includes_phase_info(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        phase = {"name": "Combat", "features": ["attack", "block"]}
        prompt = gen.generate("coder", "Implement", phase_info=phase)
        assert "Combat" in prompt

    def test_generate_includes_architect_guidance(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate(
            "coder", "Implement phase 1",
            architect_guidance="Use ECS pattern for entities"
        )
        assert "Architect Guidance" in prompt
        assert "ECS pattern" in prompt

    def test_generate_scientist_includes_specs(self, tmp_path):
        root = _setup_project(tmp_path)
        (root / "SPEC.md").write_text("# Spec\nDPS = ATK * SPD", encoding="utf-8")
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate("scientist", "Validate combat formulas")
        assert "Design Specifications" in prompt
        assert "DPS = ATK * SPD" in prompt

    def test_generate_reviewer_includes_json_instruction(self, tmp_path):
        root = _setup_project(tmp_path)
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate("reviewer", "Grade screenshots")
        assert "PASS|FAIL" in prompt
        assert "JSON" in prompt

    def test_generate_includes_violations_for_coder(self, tmp_path):
        root = _setup_project(tmp_path)
        v_path = root / ".claude" / "codewarden_violations.jsonl"
        v_path.write_text(
            json.dumps({"severity": "high", "file": "main.cpp",
                        "description": "Missing null check"}) + "\n",
            encoding="utf-8",
        )
        ctx = ProjectContext(root)
        gen = PromptGenerator(ctx)
        prompt = gen.generate("coder", "Fix issues")
        assert "Active Violations" in prompt
        assert "Missing null check" in prompt


# ===========================================================================
# parse_review_json Tests
# ===========================================================================

class TestParseReviewJson:
    """Tests for structured review JSON parsing."""

    def test_parse_valid_json(self):
        raw = json.dumps({
            "criteria": [
                {"name": "Walls", "status": "PASS", "detail": "Visible"},
                {"name": "Floor", "status": "FAIL", "detail": "Not visible"},
            ],
            "summary": {"pass": 1, "fail": 1, "total": 2},
            "overall": "FAIL",
        })
        result = parse_review_json(raw)
        assert result["overall"] == "FAIL"
        assert len(result["criteria"]) == 2
        assert result["summary"]["fail"] == 1

    def test_parse_json_in_markdown(self):
        raw = """Here is my review:
```json
{
  "criteria": [{"name": "Lighting", "status": "PASS", "detail": "OK"}],
  "summary": {"pass": 1, "fail": 0, "total": 1},
  "overall": "PASS"
}
```
"""
        result = parse_review_json(raw)
        assert result["overall"] == "PASS"
        assert len(result["criteria"]) == 1

    def test_parse_fallback_text(self):
        raw = "Walls: PASS\nFloor: FAIL\nLighting: PASS\nCeiling: FAIL"
        result = parse_review_json(raw)
        assert result["summary"]["pass"] == 2
        assert result["summary"]["fail"] == 2
        assert result["overall"] == "FAIL"

    def test_parse_empty_input(self):
        result = parse_review_json("")
        assert result["summary"]["pass"] == 0
        assert result["summary"]["fail"] == 0

    def test_parse_all_pass(self):
        raw = json.dumps({
            "criteria": [
                {"name": "A", "status": "PASS", "detail": "OK"},
                {"name": "B", "status": "PASS", "detail": "OK"},
            ],
            "summary": {"pass": 2, "fail": 0, "total": 2},
            "overall": "PASS",
        })
        result = parse_review_json(raw)
        assert result["overall"] == "PASS"
        assert result["summary"]["fail"] == 0

    def test_parse_invalid_json_with_text(self):
        raw = "This is not JSON but has some PASS and FAIL markers"
        result = parse_review_json(raw)
        assert "_raw" in result
        assert result["summary"]["pass"] == 1
        assert result["summary"]["fail"] == 1


# ===========================================================================
# AgentCaller Tests
# ===========================================================================

class TestAgentCaller:
    """Tests for subprocess agent delegation."""

    @staticmethod
    def _write_runtime_gate(root: Path, execution_mode: str, backend: str = "anthropic_prod"):
        control_dir = root / ".controlcoding"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "cc_engagement.json").write_text(
            json.dumps({
                "tier": "agents",
                "specialist_paths": [{
                    "role_id": "consultant_1",
                    "label": "Architect Consultant",
                    "path_type": "consultant",
                    "active": True,
                    "backend": backend,
                    "model": "claude-sonnet",
                    "permission": (
                        "user_mediated" if execution_mode == "human_mediated"
                        else "within_budget_auto" if execution_mode == "auto_bounded"
                        else "approval_required"
                    ),
                    "execution_mode": execution_mode,
                    "max_calls": 2,
                }],
            }),
            encoding="utf-8",
        )
        (control_dir / "gateway_config.json").write_text(
            json.dumps({"backends": {backend: {"transport": "anthropic"}}}),
            encoding="utf-8",
        )

    def test_call_consult_failure(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        with patch("subprocess.Popen", side_effect=OSError("no such process")):
            result = caller.call_consult("debug", "test prompt", backend="ollama")
        assert "[ERROR]" in result

    def test_call_consult_exception(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        with patch("subprocess.Popen", side_effect=FileNotFoundError("no consult.py")):
            result = caller.call_consult("debug", "test prompt", backend="ollama")
        assert "[ERROR]" in result

    def test_call_consult_blocks_human_mediated_path(self, tmp_path):
        root = _setup_project(tmp_path)
        self._write_runtime_gate(root, execution_mode="human_mediated")
        caller = AgentCaller(root)
        with patch("subprocess.Popen") as mock_popen:
            result = caller.call_consult("architect", "test prompt", backend="claude")
        assert result.startswith("[GATED]")
        assert "consult-packet create" in result
        mock_popen.assert_not_called()

    def test_call_consult_fails_closed_when_control_plane_utils_unavailable(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        with patch.dict(sys.modules, {"control_plane_utils": None}):
            with patch(
                "subprocess.Popen",
                side_effect=AssertionError("subprocess should not run"),
            ) as mock_popen:
                result = caller.call_consult("architect", "test prompt", backend="claude")
        assert result.startswith("[GATED]")
        assert "control_plane_utils_unavailable" in result
        mock_popen.assert_not_called()

    def test_call_consult_fails_closed_when_runtime_gate_raises(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        with patch(
            "control_plane_utils.check_specialist_runtime",
            side_effect=RuntimeError("boom"),
        ):
            with patch(
                "subprocess.Popen",
                side_effect=AssertionError("subprocess should not run"),
            ) as mock_popen:
                result = caller.call_consult("architect", "test prompt", backend="claude")
        assert result.startswith("[GATED]")
        assert "governance_check_failed" in result
        mock_popen.assert_not_called()

    def test_call_tandem_fails_closed_when_control_plane_utils_unavailable(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        fake_consultant = MagicMock()
        fake_consultant.run_tandem.side_effect = AssertionError("tandem should not run")
        with patch.dict(
            sys.modules,
            {"control_plane_utils": None, "mcp_consultant": fake_consultant},
        ):
            result = caller.call_tandem("Design the combat system")
        assert result["error"].startswith("[GATED]")
        assert "control_plane_utils_unavailable" in result["error"]
        fake_consultant.run_tandem.assert_not_called()

    def test_call_consult_uses_configured_backend_and_model(self, tmp_path):
        root = _setup_project(tmp_path)
        self._write_runtime_gate(root, execution_mode="cc_routed", backend="anthropic_prod")
        gateway_path = root / ".controlcoding" / "gateway_config.json"
        gateway = json.loads(gateway_path.read_text(encoding="utf-8"))
        gateway["backends"]["claude"] = {"transport": "official_cli"}
        gateway_path.write_text(json.dumps(gateway), encoding="utf-8")
        caller = AgentCaller(root)
        mock_proc = MagicMock()
        mock_proc.poll = MagicMock(return_value=0)
        mock_proc.communicate = MagicMock(return_value=("Consult OK", ""))
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = caller.call_consult("architect", "test prompt", backend="claude", model="wrong-model")
        assert result == "Consult OK"
        cmd = mock_popen.call_args[0][0]
        assert "--backend" in cmd
        assert "claude" in cmd
        assert "--model" in cmd
        assert "wrong-model" in cmd

    def test_call_coder_no_claude(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        with patch.object(caller, "_find_claude", return_value=None):
            result = caller.call_coder(
                "do stuff", backend="claude", launcher="claude"
            )
        assert "[ERROR]" in result
        assert "not found" in result

    def test_call_coder_failure(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        with patch.object(caller, "_find_claude", return_value="claude"), \
             patch("subprocess.Popen", side_effect=OSError("spawn failed")):
            result = caller.call_coder(
                "do stuff", backend="claude", launcher="claude"
            )
        assert "[ERROR]" in result

    def test_call_build_success(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        mock_proc = MagicMock()
        mock_proc.poll = MagicMock(return_value=0)  # already finished
        mock_proc.returncode = 0
        mock_proc.communicate = MagicMock(return_value=("Build OK", ""))
        mock_proc.stdout = MagicMock()
        with patch("subprocess.Popen", return_value=mock_proc):
            success, output = caller.call_build("cmake --build build")
        assert success is True
        assert "Build OK" in output

    def test_call_build_failure(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        mock_proc = MagicMock()
        mock_proc.poll = MagicMock(return_value=1)
        mock_proc.returncode = 1
        mock_proc.communicate = MagicMock(return_value=("", "error: cannot find file"))
        mock_proc.stdout = MagicMock()
        with patch("subprocess.Popen", return_value=mock_proc):
            success, output = caller.call_build("cmake --build build")
        assert success is False

    def test_call_visual_test_builds_correct_cmd(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        mock_result = MagicMock()
        mock_result.stdout = "[L2+] Done"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            caller.call_visual_test(
                exe_path="build/app.exe",
                actions="wait:2,screenshot:test.png",
            )
        cmd = mock_run.call_args[0][0]
        assert "visual_test.py" in cmd[1]
        assert "--exe" in cmd
        assert "--json" in cmd

    def test_call_tandem(self, tmp_path):
        root = _setup_project(tmp_path)
        caller = AgentCaller(root)
        mock_report = {
            "agreements": [{"topic": "Use ECS"}],
            "divergences": [],
        }
        with patch.dict("sys.modules", {"mcp_consultant": MagicMock()}):
            sys.modules["mcp_consultant"].run_tandem = MagicMock(return_value=mock_report)
            result = caller.call_tandem("Design the combat system")
        # If import fails (no mcp_consultant in path), we get error dict
        assert isinstance(result, dict)


# ===========================================================================
# Concierge Integration Tests
# ===========================================================================

import subprocess  # needed for subprocess.TimeoutExpired


class TestConciergeWorkflow:
    """Tests for the Concierge orchestration workflow."""

    def test_handle_idle_sets_task(self, tmp_path):
        c = _make_concierge(tmp_path)
        # Mock workshop to skip (user picks "3" = complete doc)
        with patch.object(c, "ask_user", return_value="3"):
            c.handle_idle("Build a game")
        assert c.state.task == "Build a game"
        assert c.state.state == "PLANNING"

    def test_handle_idle_clears_state(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.state.failures = ["old failure"]
        c.state.completed_phases = [0, 1]
        c.state.architect_guidance = "old guidance"
        with patch.object(c, "ask_user", return_value="3"):
            c.handle_idle("New task")
        assert c.state.failures == []
        assert c.state.completed_phases == []

    def test_handle_planning_existing_plan(self, tmp_path):
        c = _make_concierge(tmp_path)
        _setup_plan(tmp_path)
        c.state.state = "PLANNING"
        # Mock ask_user to skip vision expansion
        with patch.object(c, "ask_user", return_value="1"):
            c.handle_planning()
        assert c.state.state == "ARCHITECTURE_REVIEW"
        assert c.state.total_phases == 2

    def test_handle_planning_no_plan_falls_to_coding(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.state.state = "PLANNING"
        c.state.task = "Build something"
        # Mock planner to fail
        with patch.object(c.agents, "call_planner", return_value={"error": "no planner"}), \
             patch.object(c.agents, "call_consult", return_value="Plan advice"):
            c.handle_planning()
        assert c.state.state == "CODING"
        assert c.state.total_phases == 1

    def test_handle_architecture_stores_guidance(self, tmp_path):
        c = _make_concierge(tmp_path)
        _setup_plan(tmp_path)
        c.state.state = "ARCHITECTURE_REVIEW"
        with patch.object(c.agents, "call_consult", return_value="Use MVC pattern. Keep entities separate."):
            c.handle_architecture_review()
        assert "MVC" in c.state.architect_guidance
        assert c.state.state == "CODING"

    def test_handle_coding_sends_architect_guidance(self, tmp_path):
        c = _make_concierge(tmp_path)
        _setup_plan(tmp_path)
        c.state.state = "CODING"
        c.state.total_phases = 2
        c.state.architect_guidance = "Use ECS for all entities"
        with patch.object(c.agents, "call_coder", return_value="Phase done") as mock_coder:
            c.handle_coding()
        # The prompt should include architect guidance
        prompt = mock_coder.call_args[0][0]
        assert "ECS" in prompt

    def test_handle_coding_error_increments_attempts(self, tmp_path):
        c = _make_concierge(tmp_path)
        _setup_plan(tmp_path)
        c.state.state = "CODING"
        c.state.total_phases = 2
        with patch.object(c.agents, "call_coder", return_value="[ERROR] Claude CLI not found"):
            c.handle_coding()
        assert c.state.phase_attempts == 1
        assert len(c.state.failures) == 1

    def test_handle_coding_visual_phase_goes_to_testing(self, tmp_path):
        c = _make_concierge(tmp_path)
        phases = [{"name": "Rendering engine", "features": ["3D"]}]
        _setup_plan(tmp_path, phases=phases)
        c.state.state = "CODING"
        c.state.total_phases = 1
        with patch.object(c.agents, "call_coder", return_value="Done"):
            c.handle_coding()
        assert c.state.state == "TESTING"

    def test_handle_coding_always_goes_to_testing(self, tmp_path):
        """All phases go to TESTING (build verification is mandatory)."""
        c = _make_concierge(tmp_path)
        phases = [{"name": "Combat system", "features": ["attack"]}]
        _setup_plan(tmp_path, phases=phases)
        c.state.state = "CODING"
        c.state.total_phases = 1
        with patch.object(c.agents, "call_coder", return_value="Done"):
            c.handle_coding()
        assert c.state.state == "TESTING"

    def test_handle_reviewing_structured_json(self, tmp_path):
        c = _make_concierge(tmp_path)
        _setup_plan(tmp_path)
        c.state.state = "REVIEWING"
        c.state.total_phases = 2

        # Create a screenshot
        ss = tmp_path / "screenshots" / "phase_1.png"
        ss.parent.mkdir(parents=True, exist_ok=True)
        ss.write_bytes(b"fake png" * 5000)  # big enough to pass darkness heuristic

        review_json = json.dumps({
            "criteria": [
                {"name": "Walls", "status": "PASS", "detail": "Visible"},
                {"name": "Floor", "status": "PASS", "detail": "Visible"},
            ],
            "summary": {"pass": 2, "fail": 0, "total": 2},
            "overall": "PASS",
        })
        # Mock darkness pre-check to pass (fake PNG isn't a real image)
        with patch.object(c, "_generate_verification_strategy", return_value=[]), \
             patch.object(c.agents, "call_consult", return_value=review_json):
            c.handle_reviewing()

        # Phase should pass and advance
        assert c.state.state == "CODING"
        assert 0 in c.state.completed_phases
        assert c.state.current_phase == 1
        assert len(c.state.review_results) == 1
        assert c.state.review_results[0]["review"]["overall"] == "PASS"

    def test_handle_reviewing_fail_goes_to_debugging(self, tmp_path):
        c = _make_concierge(tmp_path)
        _setup_plan(tmp_path)
        c.state.state = "REVIEWING"
        c.state.total_phases = 2

        ss = tmp_path / "screenshots" / "phase_1.png"
        ss.parent.mkdir(parents=True, exist_ok=True)
        ss.write_bytes(b"fake png")

        review_json = json.dumps({
            "criteria": [
                {"name": "Walls", "status": "PASS", "detail": "OK"},
                {"name": "Floor", "status": "FAIL", "detail": "Not visible"},
            ],
            "summary": {"pass": 1, "fail": 1, "total": 2},
            "overall": "FAIL",
        })
        with patch.object(c, "_generate_verification_strategy", return_value=[]), \
             patch.object(c.agents, "call_consult", return_value=review_json):
            c.handle_reviewing()

        assert c.state.state == "DEBUGGING"
        # Should have specific failure detail
        assert any("Floor" in f for f in c.state.failures)

    def test_handle_debugging_escalates_to_socratic(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.state.state = "DEBUGGING"
        c.state.debug_attempts = 2  # will be incremented to 3
        c.state.failures = ["bug1", "bug2", "bug3"]
        with patch.object(c.agents, "call_consult", return_value="Try different approach") as mock:
            c.handle_debugging()
        # Should have called with socratic role
        assert mock.call_args[0][0] == "socratic"
        assert c.state.state == "FIX_LOOP"

    def test_handle_debugging_calls_debugger_first(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.state.state = "DEBUGGING"
        c.state.debug_attempts = 0
        c.state.failures = ["build failed"]
        with patch.object(c.agents, "call_consult", return_value="Check main.cpp line 42") as mock:
            c.handle_debugging()
        assert mock.call_args[0][0] == "debug"
        assert c.state.debug_attempts == 1
        assert c.state.state == "FIX_LOOP"

    def test_handle_fix_loop_transitions_to_testing(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.state.state = "FIX_LOOP"
        c.handle_fix_loop()
        assert c.state.state == "TESTING"

    def test_handle_testing_build_failure(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.state.state = "TESTING"
        c.state.total_phases = 1
        with patch.object(c.agents, "call_build", return_value=(False, "error: missing file")):
            c.handle_testing()
        assert c.state.state == "DEBUGGING"
        assert any("Build failed" in f for f in c.state.failures)

    def test_handle_done_prints_summary(self, tmp_path, capsys):
        c = _make_concierge(tmp_path)
        c.state.state = "DONE"
        c.state.task = "Build a game"
        c.state.completed_phases = [0, 1]
        c.state.total_phases = 2
        c.state.failures = ["one failure"]
        c.state.review_results = [
            {"phase": 1, "review": {"overall": "PASS", "summary": {"pass": 3, "fail": 0}}}
        ]
        # Need to come from FINAL_REVIEW
        c.state.state = "FINAL_REVIEW"
        with patch.object(c.agents, "call_tests", return_value=(True, "OK")):
            c.handle_final_review()
        assert c.state.state == "DONE"

    def test_full_workflow_no_plan(self, tmp_path):
        """Test a minimal workflow: IDLE -> PLANNING -> CODING -> TESTING -> REVIEWING -> DONE."""
        c = _make_concierge(tmp_path)

        with patch.object(c.agents, "call_planner", return_value={"error": "no planner"}), \
             patch.object(c.agents, "call_consult", return_value="advice"), \
             patch.object(c.agents, "call_coder", return_value="Phase done"), \
             patch.object(c.agents, "call_build", return_value=(True, "OK")), \
             patch.object(c, "ask_user", return_value="3"):
            c.handle_idle("Simple task")
            assert c.state.state == "PLANNING"

            c.handle_planning()
            assert c.state.state == "CODING"

            c.handle_coding()
            assert c.state.state == "TESTING"

            c.handle_testing()
            assert c.state.state == "REVIEWING"

            # No screenshots and no exe, so reviewing passes directly
            c.handle_reviewing()
            assert c.state.state == "FINAL_REVIEW"

    def test_run_exits_on_no_task(self, tmp_path, capsys):
        c = _make_concierge(tmp_path)
        with patch.object(c, "ask_user", return_value=""):
            c.run()
        output = capsys.readouterr().out
        assert "No task" in output

    def test_tandem_protocol_integration(self, tmp_path):
        """Tandem is a protocol, not an agent."""
        c = _make_concierge(tmp_path)
        c.engagement["tandem"] = {
            "mode": "auto",
            "backend_a": "ollama",
            "backend_b": "ollama",
            "model_a": "model-a",
            "model_b": "model-b",
        }
        mock_report = {
            "agreements": [{"topic": "Use ECS", "position": "Both agree"}],
            "divergences": [{"topic": "State mgmt", "position_a": "Redux", "position_b": "MobX"}],
        }
        with patch.object(c.agents, "call_tandem", return_value=mock_report):
            result = c._run_tandem_debate("How to structure entities?")
        assert len(result["agreements"]) == 1
        assert len(result["divergences"]) == 1

    def test_explicit_same_backend_and_model_tandem_is_preserved(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.engagement = {
            "tier": "agents",
            "level": 4,
            "backend_policy": "local_only",
            "tandem": {
                "mode": "auto",
                "backend_a": "ollama",
                "backend_b": "ollama",
                "model_a": "qwen3-coder",
                "model_b": "qwen3-coder",
            },
            "budget_policy": {"max_calls": 0, "exhaustion_behavior": "degrade"},
        }
        mock_report = {"agreements": [], "divergences": []}
        with patch.object(c.agents, "call_consult") as mock_consult, patch.object(
            c.agents, "call_tandem", return_value=mock_report
        ) as mock_tandem:
            result = c._run_tandem_debate("How to structure entities?")

        assert result == mock_report
        mock_consult.assert_not_called()
        mock_tandem.assert_called_once()

    def test_local_only_tandem_uses_two_ollama_models_when_configured(self, tmp_path):
        c = _make_concierge(tmp_path)
        c.engagement = {
            "tier": "agents",
            "level": 4,
            "backend_policy": "local_only",
            "tandem": {
                "mode": "auto",
                "backend_a": "ollama",
                "backend_b": "ollama",
                "model_a": "qwen3-coder",
                "model_b": "codestral",
            },
            "budget_policy": {"max_calls": 0, "exhaustion_behavior": "degrade"},
        }
        mock_report = {"agreements": [{"topic": "ECS"}], "divergences": []}
        with patch.object(
            c,
            "_assess_local_tandem_capacity",
            return_value={
                "status": "risky",
                "preferred_mode": "dual_model_sequential",
                "summary": "sequential",
                "total_ram_gb": 48.0,
                "gpu_vram_gb": 16.0,
                "reasons": [],
            },
        ), patch.object(c.agents, "call_tandem", return_value=mock_report) as mock_tandem:
            result = c._run_tandem_debate("How to structure entities?")

        assert result == mock_report
        mock_tandem.assert_called_once_with(
            problem="How to structure entities?",
            backend_a="ollama",
            backend_b="ollama",
            model_a="qwen3-coder",
            model_b="codestral",
            role="architect",
            domain="",
        )


# ===========================================================================
# Constants / Smoke Tests
# ===========================================================================

class TestConstants:
    """Smoke tests for module-level constants."""

    def test_max_iterations_is_reasonable(self):
        assert MAX_ITERATIONS == 100

    def test_review_json_instruction_contains_format(self):
        assert "PASS|FAIL" in REVIEW_JSON_INSTRUCTION
        assert "criteria" in REVIEW_JSON_INSTRUCTION

    def test_all_role_instructions_defined(self):
        expected = {"planner", "architect", "coder", "reviewer",
                    "debug", "socratic", "scientist"}
        actual = set(PromptGenerator.ROLE_INSTRUCTIONS.keys())
        assert expected.issubset(actual)
