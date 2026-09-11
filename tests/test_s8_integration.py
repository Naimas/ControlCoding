"""Tests for Sprint S8: Concierge Integration + Hardening.

High-value integration tests covering:
1. Concierge engagement gating (skip disabled agents)
2. Concierge state recovery (crash mid-phase + resume)
3. Budget exhaustion behavior
4. plan_approve auto-chain from Concierge PLANNING transition
5. Double plan_approve idempotency
6. Engagement gating precedence (hooks > config > concierge)
7. Malformed engagement config
8. Phase-entry/exit markers in state
9. Tandem gated at low engagement
10. cc doctor --json output
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add templates/scripts to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from concierge import (
    Concierge,
    ConciergeState,
    STATES,
    TRANSITIONS,
)
from control_plane_utils import (
    load_engagement,
    save_engagement,
    is_component_active,
    check_budget,
    atomic_write,
)


def _setup_project(tmp_path):
    """Create minimal project structure for Concierge."""
    root = tmp_path / "project"
    root.mkdir()
    (root / ".claude").mkdir()
    (root / "devlog").mkdir()
    (root / "tools").mkdir()
    (root / "CLAUDE.md").write_text(
        "# Test Project\n## Project Identity\n- Stack: Python\n",
        encoding="utf-8",
    )
    return root


def _make_concierge(tmp_path, engagement_level=4):
    """Create a Concierge with specified engagement level."""
    root = _setup_project(tmp_path)
    c = Concierge(str(root))
    c.engagement = {
        "level": engagement_level,
        "backend_policy": "all",
        "budget_policy": {"max_calls": 0, "exhaustion_behavior": "degrade"},
    }
    return c


class TestEngagementGating(unittest.TestCase):
    """Test that Concierge respects engagement level."""

    def test_level_1_skips_architect(self):
        """At level 1, architecture review should be skipped."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=1)
            c.state.state = "PLANNING"
            c.state.task = "Test task"
            c.state.total_phases = 1

            # Transition to ARCHITECTURE_REVIEW
            c.state.transition("ARCHITECTURE_REVIEW")
            c.handle_architecture_review()

            # Should have skipped to CODING
            self.assertEqual(c.state.state, "CODING")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_level_4_calls_architect(self):
        """At level 4, architecture review should call consultant."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            c.state.state = "PLANNING"
            c.state.task = "Test task"
            c.state.total_phases = 1
            c.state.transition("ARCHITECTURE_REVIEW")

            with patch.object(c.agents, "call_consult",
                              return_value="Use MVC pattern"):
                c.handle_architecture_review()

            self.assertEqual(c.state.state, "CODING")
            self.assertIn("MVC", c.state.architect_guidance)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tandem_gated_at_level_2(self):
        """Tandem requires level 4. At level 2, it should be skipped."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=2)
            result = c._run_tandem_debate("How to structure?")
            self.assertTrue(result.get("skipped"))
            self.assertEqual(result.get("reason"), "engagement_gated")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tandem_active_at_level_4(self):
        """Tandem at level 4 should proceed."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            c.engagement["tandem"] = {
                "mode": "auto",
                "backend_a": "ollama",
                "backend_b": "openai",
                "model_a": "",
                "model_b": "",
            }
            mock_report = {
                "agreements": [{"topic": "ECS"}],
                "divergences": [],
            }
            with patch.object(c.agents, "call_tandem",
                              return_value=mock_report):
                result = c._run_tandem_debate("Entity system?")
            self.assertFalse(result.get("skipped", False))
            self.assertEqual(len(result["agreements"]), 1)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_is_active_check(self):
        """Concierge._is_active() delegates to engagement config."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=1)
            self.assertFalse(c._is_active("consultant"))
            self.assertFalse(c._is_active("tandem"))

            c.engagement["level"] = 4
            self.assertTrue(c._is_active("consultant"))
            self.assertTrue(c._is_active("tandem"))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestStateRecovery(unittest.TestCase):
    """Test crash recovery via state persistence."""

    def test_save_and_resume(self):
        """State persists across Concierge instances."""
        tmp = tempfile.mkdtemp()
        try:
            root = _setup_project(Path(tmp))

            # First instance: start task
            c1 = Concierge(str(root))
            c1.engagement = {"level": 4, "backend_policy": "all",
                             "budget_policy": {"max_calls": 0,
                                               "exhaustion_behavior": "degrade"}}
            c1.state.state = "IDLE"
            c1.state.task = "Build a game"
            c1.state.transition("PLANNING")
            c1.state.save()

            # Second instance: resume
            c2 = Concierge(str(root))
            loaded = c2.state.load()
            self.assertTrue(loaded)
            self.assertEqual(c2.state.state, "PLANNING")
            self.assertEqual(c2.state.task, "Build a game")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_phase_ops_persist(self):
        """Phase operation markers survive save/load."""
        tmp = tempfile.mkdtemp()
        try:
            root = _setup_project(Path(tmp))
            c = Concierge(str(root))
            c.state.state = "CODING"
            c.state.current_phase = 0
            c.state.mark_op("build", "completed")
            c.state.mark_op("test", "in_progress")
            c.state.save()

            c2 = Concierge(str(root))
            c2.state.load()
            self.assertEqual(c2.state.get_op_status("build"), "completed")
            self.assertEqual(c2.state.get_op_status("test"), "in_progress")
            self.assertEqual(c2.state.get_op_status("review"), "not_started")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_phase_entry_exit_markers(self):
        """Main loop writes phase-entry/exit markers."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            c.state.state = "IDLE"

            # Run with a task that enters PLANNING
            with patch.object(c, "handle_idle") as mock_idle:
                def _fake_idle(task):
                    c.state.task = task
                    c.state.transition("PLANNING")
                mock_idle.side_effect = _fake_idle

                with patch.object(c, "handle_planning") as mock_plan:
                    def _fake_plan():
                        c.state.total_phases = 1
                        c.state.transition("DONE")
                    mock_plan.side_effect = _fake_plan

                    with patch.object(c, "handle_done"):
                        c.run(task="test")

            # Should have phase ops recorded
            self.assertIn("state_PLANNING", c.state.phase_ops.get("0", {}))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestBudgetIntegration(unittest.TestCase):
    """Test budget enforcement in Concierge."""

    def test_budget_check_allows_unlimited(self):
        """Zero max_calls means unlimited."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            c.engagement["budget_policy"]["max_calls"] = 0
            self.assertTrue(c._check_budget())
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_budget_check_fails_closed_when_control_plane_utils_missing(self):
        """Missing governance utility denies budget-governed runtime paths."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            with patch.dict(sys.modules, {"control_plane_utils": None}):
                with patch.object(c, "log") as mock_log:
                    self.assertFalse(c._check_budget())
            self.assertEqual(
                c.last_governance_denial["reason"],
                "control_plane_utils_unavailable",
            )
            self.assertIn("control_plane_utils_unavailable",
                          mock_log.call_args[0][0])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_budget_check_fails_closed_when_control_plane_check_raises(self):
        """Unexpected budget check failures deny governed runtime paths."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            with patch("control_plane_utils.check_budget",
                       side_effect=RuntimeError("boom")):
                with patch.object(c, "log") as mock_log:
                    self.assertFalse(c._check_budget())
            self.assertEqual(
                c.last_governance_denial["reason"],
                "governance_check_failed",
            )
            self.assertIn("governance_check_failed",
                          mock_log.call_args[0][0])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_budget_exhaustion_skips_architect(self):
        """When budget is exhausted, architect review skips."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            # Mock _check_budget to return False
            with patch.object(c, "_check_budget", return_value=False):
                c.state.state = "PLANNING"
                c.state.task = "test"
                c.state.total_phases = 1
                c.state.transition("ARCHITECTURE_REVIEW")
                c.handle_architecture_review()
                self.assertEqual(c.state.state, "CODING")
                # Architect guidance should be empty (skipped)
                self.assertEqual(c.state.architect_guidance, "")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestPlanApproveAutoChain(unittest.TestCase):
    """Test plan_approve auto-chain from Concierge context."""

    def test_double_approve_idempotent(self):
        """Calling plan_approve twice should not corrupt state."""
        tmp = tempfile.mkdtemp()
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            from planner import plan_approve, save_plan, load_plan
            plan_dir = os.path.join(tmp, "devlog")
            os.makedirs(plan_dir)
            event_log = os.path.join(tmp, ".claude", "event_log.jsonl")
            decision_log = os.path.join(tmp, ".claude", "decision_log.jsonl")
            os.makedirs(os.path.dirname(event_log))

            plan = {
                "status": "DRAFT", "version": 1, "scope": "Test",
                "phases": [{"phase": 1, "name": "P1", "features": []}],
                "architecture": {"zones": {}},
                "invariants_global": [],
                "expansion_state": {"completed_rounds": [], "responses": {}},
            }
            save_plan(plan, plan_dir)

            r1 = plan_approve(plan_dir=plan_dir, event_log=event_log,
                              decision_log=decision_log)
            self.assertEqual(r1["plan"]["status"], "APPROVED")

            # Amend and re-approve
            p = load_plan(plan_dir)
            p["status"] = "AMENDED"
            save_plan(p, plan_dir)

            r2 = plan_approve(plan_dir=plan_dir, event_log=event_log,
                              decision_log=decision_log)
            self.assertEqual(r2["plan"]["status"], "APPROVED")
            self.assertEqual(r2["plan"]["version"], 2)
            criteria_path = Path(tmp) / "devlog" / "criteria" / "criteria.json"
            config_path = Path(tmp) / ".controlcoding" / "cc_config.json"
            self.assertTrue(criteria_path.exists())
            self.assertTrue(config_path.exists())
            self.assertTrue(
                criteria_path.resolve().is_relative_to(Path(tmp).resolve()))
            self.assertTrue(
                config_path.resolve().is_relative_to(Path(tmp).resolve()))
        finally:
            os.chdir(original_cwd)
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestMalformedConfig(unittest.TestCase):
    """Test handling of malformed engagement config."""

    def test_concierge_survives_bad_config(self):
        """Concierge should work even with broken config."""
        tmp = tempfile.mkdtemp()
        try:
            root = _setup_project(Path(tmp))
            config_path = root / ".claude" / "cc_engagement.json"
            config_path.write_text("{bad json", encoding="utf-8")

            c = Concierge(str(root))
            # Should have loaded defaults (level 2 from load_engagement,
            # or level 4 from Concierge fallback - either is acceptable)
            self.assertIn(c.engagement.get("level"), (2, 4))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_engagement_gating_with_missing_utils(self):
        """If control_plane_utils is not importable, fail closed."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            with patch.dict(sys.modules, {"control_plane_utils": None}):
                with patch.object(c, "log") as mock_log:
                    self.assertFalse(c._is_active("consultant"))
            self.assertEqual(
                c.last_governance_denial["reason"],
                "control_plane_utils_unavailable",
            )
            self.assertIn("control_plane_utils_unavailable",
                          mock_log.call_args[0][0])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_engagement_gating_check_exception_fails_closed(self):
        """Unexpected active-check failures deny governed components."""
        tmp = tempfile.mkdtemp()
        try:
            c = _make_concierge(Path(tmp), engagement_level=4)
            with patch("control_plane_utils.is_component_active",
                       side_effect=RuntimeError("boom")):
                with patch.object(c, "log") as mock_log:
                    self.assertFalse(c._is_active("consultant"))
            self.assertEqual(
                c.last_governance_denial["reason"],
                "governance_check_failed",
            )
            self.assertIn("governance_check_failed",
                          mock_log.call_args[0][0])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestDoctorJson(unittest.TestCase):
    """Test cc doctor --json output."""

    def test_json_output_valid(self):
        """cc doctor --json produces valid JSON."""
        tmp = tempfile.mkdtemp()
        try:
            root = _setup_project(Path(tmp))
            # Add minimal settings.json
            (root / ".claude" / "settings.json").write_text(
                '{"hooks": {}}', encoding="utf-8")
            (root / "CLAUDE.md").write_text(
                "# Test\n" * 60, encoding="utf-8")

            # Import and call cmd_doctor
            scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
            sys.path.insert(0, str(scripts_dir))
            from cc import cmd_doctor

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                cmd_doctor(root, json_output=True)

            output = f.getvalue()
            report = json.loads(output)
            self.assertIn("project", report)
            self.assertIn("issues", report)
            self.assertIn("healthy", report)
            self.assertIn("checks", report)
            self.assertIsInstance(report["checks"], list)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestConciergeEngagementFromFile(unittest.TestCase):
    """Test Concierge loading engagement from actual file."""

    def test_loads_from_file(self):
        """Concierge loads engagement level from cc_engagement.json."""
        tmp = tempfile.mkdtemp()
        try:
            root = _setup_project(Path(tmp))
            config = {
                "schema_version": 1,
                "level": 1,
                "backend_policy": "local_only",
                "budget_policy": {"max_calls": 10,
                                  "exhaustion_behavior": "stop"},
            }
            save_engagement(config,
                            str(root / ".claude" / "cc_engagement.json"))

            c = Concierge(str(root))
            # Should have loaded level 1
            self.assertEqual(c.engagement.get("level", 4), 1)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
