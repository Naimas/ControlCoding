"""Tests for Tandem Agent (Sprint 3).

Tests the tandem comparison workflow in mcp_consultant.py:
- Round management, convergence detection, deadlock, socratic escalation
- Domain-adapted prompts, output format, decision logging
- Integration with control plane (event_log, decision_log)

Minimum 25 tests. All backend calls are mocked.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

# Must import after path setup
import mcp_consultant as mc


# ===========================================================================
# Helpers
# ===========================================================================


@pytest.fixture(autouse=True)
def _isolated_project_root(tmp_path, monkeypatch):
    """Isolate tandem tests from the repo-local control plane."""
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    monkeypatch.setattr(mc, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(mc, "LOG_DIR", str(control_dir))
    monkeypatch.chdir(tmp_path)

def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    lines = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


class MockBackend:
    """Context manager that mocks all backend calls."""

    def __init__(self, response_a="Position A response",
                 response_b="Position B response",
                 classify_result=None,
                 socratic_response="Socratic insight here"):
        self.response_a = response_a
        self.response_b = response_b
        self.classify_result = classify_result or {
            "agreements": [
                {"topic": "Topic 1", "position": "Both agree", "confidence": "high"}
            ],
            "divergences": [
                {"topic": "Topic 2", "position_a": "A says X",
                 "position_b": "B says Y",
                 "analysis": "Trade-off", "user_decision_needed": True}
            ],
        }
        self.socratic_response = socratic_response
        self._call_count = 0
        self._patches = []

    def _mock_call(self, backend, prompt, system_prompt, model="",
                   image_path=""):
        self._call_count += 1
        # Classify calls get the classify result
        if "JSON classifier" in system_prompt or "impartial analyst" in prompt[:50]:
            return json.dumps(self.classify_result)
        # Socratic calls
        if "deadlock" in prompt.lower() or "socratic" in system_prompt.lower():
            return self.socratic_response
        # Alternate A and B for initial + reaction rounds
        if self._call_count % 2 == 1:
            return self.response_a
        return self.response_b

    def __enter__(self):
        p = patch.object(mc, '_tandem_call_backend', side_effect=self._mock_call)
        self._patches.append(p)
        p.start()
        # Reset call counter
        mc._call_counter._count = 0
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.stop()


# ===========================================================================
# Domain-adapted prompts
# ===========================================================================

class TestTandemDomainPrompts:
    def test_financial_prompt(self):
        prompt = mc._tandem_get_system_prompt("architect", "financial")
        assert "quantitative developer" in prompt
        assert "trading system" in prompt

    def test_game_prompt(self):
        prompt = mc._tandem_get_system_prompt("architect", "game")
        assert "game engine architect" in prompt

    def test_physics_prompt(self):
        prompt = mc._tandem_get_system_prompt("architect", "physics")
        assert "computational physicist" in prompt

    def test_web_prompt(self):
        prompt = mc._tandem_get_system_prompt("architect", "web")
        assert "OWASP" in prompt

    def test_unknown_domain_uses_generic(self):
        prompt = mc._tandem_get_system_prompt("architect", "robotics")
        assert "robotics" in prompt
        assert "expert" in prompt.lower()

    def test_empty_domain_uses_base(self):
        prompt = mc._tandem_get_system_prompt("architect", "")
        assert "architecture reviewer" in prompt.lower() or "architect" in prompt.lower()


# ===========================================================================
# Prompt building
# ===========================================================================

class TestTandemPromptBuilding:
    def test_initial_prompt(self):
        prompt = mc._tandem_build_prompt("Fix the bug", "system",
                                         code_snippets="def foo(): pass")
        assert "Fix the bug" in prompt
        assert "def foo(): pass" in prompt

    def test_reaction_prompt(self):
        prompt = mc._tandem_build_reaction_prompt(
            "Fix the bug", "I think we should do X", 2)
        assert "Fix the bug" in prompt
        assert "colleague" in prompt.lower()
        assert "I think we should do X" in prompt


# ===========================================================================
# Convergence detection
# ===========================================================================

class TestConvergenceDetection:
    def test_classify_with_mock(self):
        with MockBackend() as mb:
            result = mc._tandem_classify_convergence(
                "A response", "B response", "problem",
                backend="ollama", model="test")
            assert "agreements" in result
            assert "divergences" in result

    def test_classify_fallback_on_bad_json(self):
        def bad_backend(*args, **kwargs):
            return "Not valid JSON at all"

        with patch.object(mc, '_tandem_call_backend', side_effect=bad_backend):
            result = mc._tandem_classify_convergence(
                "A", "B", "problem", backend="ollama")
            # Should fallback to heuristic
            assert "divergences" in result
            assert len(result["divergences"]) >= 1

    def test_classify_extracts_json_from_code_fence(self):
        def fenced_backend(*args, **kwargs):
            return '```json\n{"agreements": [], "divergences": []}\n```'

        with patch.object(mc, '_tandem_call_backend', side_effect=fenced_backend):
            result = mc._tandem_classify_convergence(
                "A", "B", "problem", backend="ollama")
            assert result["agreements"] == []
            assert result["divergences"] == []


# ===========================================================================
# Deadlock detection
# ===========================================================================

class TestDeadlockDetection:
    def test_no_deadlock_different_topics(self):
        prev = {"divergences": [{"topic": "A"}]}
        curr = {"divergences": [{"topic": "B"}]}
        assert mc._tandem_detect_deadlock(prev, curr) is False

    def test_deadlock_same_topics(self):
        prev = {"divergences": [{"topic": "partial fills"}]}
        curr = {"divergences": [{"topic": "partial fills"}]}
        assert mc._tandem_detect_deadlock(prev, curr) is True

    def test_no_deadlock_empty(self):
        assert mc._tandem_detect_deadlock(None, None) is False
        assert mc._tandem_detect_deadlock({}, {}) is False

    def test_no_deadlock_convergence(self):
        prev = {"divergences": [{"topic": "A"}]}
        curr = {"divergences": []}
        assert mc._tandem_detect_deadlock(prev, curr) is False


# ===========================================================================
# Round management (run_tandem)
# ===========================================================================

class TestRunTandem:
    def test_single_round_consensus(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        classify = {
            "agreements": [
                {"topic": "Approach", "position": "Both agree", "confidence": "high"}
            ],
            "divergences": [],
        }
        with MockBackend(classify_result=classify):
            report = mc.run_tandem(
                "Test problem", backend_a="ollama", backend_b="ollama",
                rounds=3, event_log=el, decision_log=dl,
                classify_backend="ollama")
        assert report["consensus_reached"] is True
        assert report["rounds"] == 1
        assert len(report["agreements"]) == 1
        assert len(report["divergences"]) == 0

    def test_multiple_rounds_with_divergence(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            report = mc.run_tandem(
                "Test problem", backend_a="ollama", backend_b="ollama",
                rounds=2, event_log=el, decision_log=dl,
                classify_backend="ollama")
        assert report["rounds"] >= 1
        assert len(report["divergences"]) >= 1
        assert report["consensus_reached"] is False

    def test_max_rounds_respected(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            report = mc.run_tandem(
                "Test problem", backend_a="ollama", backend_b="ollama",
                rounds=2, event_log=el, decision_log=dl,
                classify_backend="ollama")
        assert report["rounds"] <= 2

    def test_socratic_escalation_on_deadlock(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        # Always return same divergence to trigger deadlock
        deadlock_classify = {
            "agreements": [],
            "divergences": [
                {"topic": "Persistent disagreement",
                 "position_a": "X", "position_b": "Y",
                 "analysis": "Deadlocked", "user_decision_needed": True}
            ],
        }
        with MockBackend(classify_result=deadlock_classify,
                         socratic_response="The core assumption differs"):
            report = mc.run_tandem(
                "Test problem", backend_a="ollama", backend_b="ollama",
                rounds=5, event_log=el, decision_log=dl,
                classify_backend="ollama")
        assert report["socratic_insight"] != ""
        assert "assumption" in report["socratic_insight"].lower()

    def test_backend_error_handled(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")

        call_count = [0]
        def failing_backend(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("Backend A down")
            return "Response from B"

        with patch.object(mc, '_tandem_call_backend',
                          side_effect=failing_backend):
            report = mc.run_tandem(
                "Test problem", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        # Should still produce a report (with error in response_a)
        assert "error" in report["round_details"][0]["response_a"].lower()


# ===========================================================================
# Output format
# ===========================================================================

class TestOutputFormat:
    def test_all_required_fields(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            report = mc.run_tandem(
                "Test problem", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        required_keys = {"problem", "rounds", "backend_a", "backend_b",
                         "agreements", "divergences", "consensus_reached",
                         "socratic_insight"}
        assert required_keys.issubset(set(report.keys()))

    def test_report_serializable_as_json(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            report = mc.run_tandem(
                "Test", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        # Must not raise
        serialized = json.dumps(report, indent=2, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert parsed["problem"] == "Test"

    def test_round_details_present(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            report = mc.run_tandem(
                "Test", backend_a="ollama", backend_b="ollama",
                rounds=2, event_log=el, decision_log=dl,
                classify_backend="ollama")
        assert "round_details" in report
        for rd in report["round_details"]:
            assert "round" in rd
            assert "response_a" in rd
            assert "response_b" in rd


# ===========================================================================
# Decision logging
# ===========================================================================

class TestDecisionLogging:
    def test_divergences_logged_to_decision_log(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            mc.run_tandem(
                "Architecture choice", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        decisions = _read_jsonl(dl)
        assert len(decisions) >= 1
        dec = decisions[0]
        assert dec["id"].startswith("DEC-")
        assert dec["type"] == "architecture_choice"
        assert dec["source"] == "tandem"
        assert len(dec["alternatives"]) == 2

    def test_divergences_logged_to_event_log(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            mc.run_tandem(
                "Test", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        events = _read_jsonl(el)
        diverged_events = [e for e in events if e.get("event") == "tandem_diverged"]
        assert len(diverged_events) >= 1

    def test_agreements_logged_as_events(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        with MockBackend():
            mc.run_tandem(
                "Test", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        events = _read_jsonl(el)
        converged_events = [e for e in events if e.get("event") == "tandem_converged"]
        assert len(converged_events) >= 1

    def test_no_criteria_created(self, tmp_path):
        """Divergences go to decision_log, NOT criteria.json."""
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        cp = str(tmp_path / "criteria.json")
        with MockBackend():
            mc.run_tandem(
                "Test", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        # No criteria.json should exist
        assert not Path(cp).exists()

    def test_consensus_no_decisions(self, tmp_path):
        el = str(tmp_path / "event_log.jsonl")
        dl = str(tmp_path / "decision_log.jsonl")
        classify = {"agreements": [{"topic": "A", "position": "Both agree",
                                    "confidence": "high"}],
                     "divergences": []}
        with MockBackend(classify_result=classify):
            mc.run_tandem(
                "Test", backend_a="ollama", backend_b="ollama",
                rounds=1, event_log=el, decision_log=dl,
                classify_backend="ollama")
        decisions = _read_jsonl(dl)
        assert len(decisions) == 0


# ===========================================================================
# MCP tool wrapper
# ===========================================================================

class TestConsultTandemMCPTool:
    @pytest.mark.parametrize(
        "backend_a,backend_b",
        [("", "ollama"), ("   ", "ollama"), ("fallback", "ollama"),
         ("unknown", "ollama")],
    )
    def test_invalid_backend_has_zero_calls_or_files(
            self, tmp_path, monkeypatch, backend_a, backend_b):
        monkeypatch.chdir(tmp_path)
        before = set(tmp_path.rglob("*"))
        with patch.object(mc, "_tandem_call_backend") as adapter, \
             patch.object(mc, "_log_consultation") as log:
            result = json.loads(mc.consult_tandem(
                problem="Test",
                backend_a=backend_a,
                backend_b=backend_b,
            ))
        assert result["callStarted"] is False
        assert set(tmp_path.rglob("*")) == before
        adapter.assert_not_called()
        log.assert_not_called()

    def test_returns_valid_json(self, tmp_path):
        old_el = mc.DEFAULT_EVENT_LOG
        old_dl = mc.DEFAULT_DECISION_LOG
        mc.DEFAULT_EVENT_LOG = str(tmp_path / "el.jsonl")
        mc.DEFAULT_DECISION_LOG = str(tmp_path / "dl.jsonl")
        try:
            with MockBackend():
                result_str = mc.consult_tandem(
                    problem="Test architecture",
                    backend_a="ollama", backend_b="ollama")
            result = json.loads(result_str)
            assert "problem" in result
            assert "agreements" in result
            assert "divergences" in result
        finally:
            mc.DEFAULT_EVENT_LOG = old_el
            mc.DEFAULT_DECISION_LOG = old_dl

    def test_no_round_details_in_summary(self, tmp_path):
        old_el = mc.DEFAULT_EVENT_LOG
        old_dl = mc.DEFAULT_DECISION_LOG
        mc.DEFAULT_EVENT_LOG = str(tmp_path / "el.jsonl")
        mc.DEFAULT_DECISION_LOG = str(tmp_path / "dl.jsonl")
        try:
            with MockBackend():
                result_str = mc.consult_tandem(
                    problem="Test", backend_a="ollama", backend_b="ollama")
            result = json.loads(result_str)
            # MCP tool strips round_details for readability
            assert "round_details" not in result
        finally:
            mc.DEFAULT_EVENT_LOG = old_el
            mc.DEFAULT_DECISION_LOG = old_dl

    def test_runtime_gate_blocks_human_mediated_tandem(self, tmp_path):
        original_root = mc.PROJECT_ROOT
        try:
            mc.PROJECT_ROOT = tmp_path
            control_dir = tmp_path / ".controlcoding"
            control_dir.mkdir(parents=True, exist_ok=True)
            (control_dir / "cc_engagement.json").write_text(
                json.dumps({
                    "tier": "agents",
                    "specialist_paths": [{
                        "role_id": "tandem",
                        "label": "Local Tandem",
                        "path_type": "tandem",
                        "active": True,
                        "backend": "ollama_local",
                        "model": "model-a vs model-b",
                        "permission": "user_mediated",
                        "execution_mode": "human_mediated",
                        "max_calls": 1,
                    }],
                }),
                encoding="utf-8",
            )
            with patch.object(mc, "run_tandem") as mock_run:
                result = json.loads(mc.consult_tandem(
                    problem="Test", backend_a="ollama", backend_b="ollama"
                ))
            assert "[GATED]" in result["error"]
            mock_run.assert_not_called()
        finally:
            mc.PROJECT_ROOT = original_root


# ===========================================================================
# Socratic escalation
# ===========================================================================

class TestSocraticEscalation:
    def test_socratic_prompt_content(self):
        with patch.object(mc, '_tandem_call_backend',
                          return_value="The core difference is...") as mock:
            result = mc._tandem_socratic_escalation(
                "Problem", "Position A", "Position B",
                "claude", "openai", backend="ollama")
            assert result == "The core difference is..."
            call_args = mock.call_args
            prompt = call_args[0][1]
            assert "deadlock" in prompt.lower()
            assert "claude" in prompt
            assert "openai" in prompt

    def test_socratic_error_handled(self):
        def failing(*args, **kwargs):
            raise ConnectionError("down")

        with patch.object(mc, '_tandem_call_backend', side_effect=failing):
            result = mc._tandem_socratic_escalation(
                "P", "A", "B", "claude", "openai", backend="ollama")
            assert "failed" in result.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
