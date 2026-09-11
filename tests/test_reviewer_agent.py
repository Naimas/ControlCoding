"""Tests for reviewer_agent.py - ReviewerAgent (Sprint S8)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import AgentStateBase, ReportBuilder, BaseAgent
from reviewer_agent import (
    ReviewerState, ReviewerAgent, build_reviewer_report,
    VALID_SEVERITIES, VALID_VERDICTS,
)


# ===========================================================================
# ReviewerState
# ===========================================================================

class TestReviewerState:
    def test_defaults(self):
        s = ReviewerState()
        assert s.agent_type == "reviewer"
        assert s.review_scope == ""
        assert s.files_to_review == []
        assert s.criteria == []
        assert s.issues == []
        assert s.criteria_results == []
        assert s.verdict == ""
        assert s.blocking_count == 0
        assert s.warning_count == 0

    def test_to_context_summary(self):
        s = ReviewerState()
        s.current_step = 5
        s.review_scope = "Auth module review"
        s.files_to_review = ["src/auth.py"]
        s.criteria = ["Login works", "Passwords hashed"]
        s.criteria_results = [
            {"criterion": "Login works", "status": "pass"},
            {"criterion": "Passwords hashed", "status": "fail"},
        ]
        s.issues = [
            {"severity": "high", "location": "auth.py:42",
             "issue": "Plaintext passwords"}
        ]
        s.blocking_count = 1
        s.verdict = "fail"

        summary = s.to_context_summary()
        assert "Auth module" in summary
        assert "1 pass" in summary
        assert "1 fail" in summary
        assert "1 blocking" in summary
        assert "Plaintext" in summary
        assert "fail" in summary


# ===========================================================================
# Tools
# ===========================================================================

class TestReviewerTools:
    def _make_agent(self, tmp_path):
        agent = ReviewerAgent(backend="ollama", mode="autonomous")
        agent._project_root = tmp_path
        agent._register_tools()
        return agent

    def test_read_diff_git(self, tmp_path):
        """Reads git diff output."""
        agent = self._make_agent(tmp_path)
        # Mock subprocess for git diff
        mock_result = MagicMock()
        mock_result.stdout = "+added line\n-removed line\n"
        with patch("reviewer_agent.subprocess.run",
                    return_value=mock_result):
            result = agent._tool_read_diff()
        assert "+added" in result

    def test_read_diff_empty(self, tmp_path):
        agent = self._make_agent(tmp_path)
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("reviewer_agent.subprocess.run",
                    return_value=mock_result):
            result = agent._tool_read_diff()
        assert "no diff" in result

    def test_check_criterion_pass(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_check_criterion(
            criterion="Login form works",
            status="pass",
            evidence="test_login passes",
            reasoning="All login tests green",
        )
        assert "pass" in result
        assert len(agent.state.criteria_results) == 1
        assert agent.state.criteria_results[0]["status"] == "pass"

    def test_check_criterion_fail(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_check_criterion(
            criterion="Passwords hashed",
            status="fail",
            evidence="auth.py:42 stores plaintext",
            reasoning="No hashing applied before DB write",
        )
        assert "fail" in result
        assert agent.state.criteria_results[0]["status"] == "fail"

    def test_check_criterion_invalid_status(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_check_criterion(
            criterion="X", status="maybe")
        assert "Error" in result

    def test_log_issue_severity(self, tmp_path):
        agent = self._make_agent(tmp_path)
        for sev in VALID_SEVERITIES:
            agent._tool_log_issue(
                severity=sev, location="file.py:1",
                issue=f"Test {sev}", suggestion="Fix it")
        assert len(agent.state.issues) == 5

    def test_log_issue_counts(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_log_issue("critical", "a.py:1", "crash", "fix")
        agent._tool_log_issue("high", "b.py:2", "wrong output", "fix")
        agent._tool_log_issue("medium", "c.py:3", "code smell", "refactor")
        agent._tool_log_issue("low", "d.py:4", "naming", "rename")
        agent._tool_log_issue("info", "e.py:5", "note", "")
        assert agent.state.blocking_count == 2  # critical + high
        assert agent.state.warning_count == 2   # medium + low

    def test_log_issue_invalid_severity(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_log_issue("urgent", "x.py", "bad", "fix")
        assert "Error" in result
        assert len(agent.state.issues) == 0

    def test_verdict_pass(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_verdict(
            status="pass", reasoning="All criteria met, no blocking issues")
        assert "pass" in result.lower()
        assert agent.state.verdict == "pass"
        assert agent._done_flag is True

    def test_verdict_fail(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.state.blocking_count = 2
        result = agent._tool_verdict(
            status="fail", reasoning="2 critical issues found")
        assert agent.state.verdict == "fail"

    def test_verdict_partial(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_verdict(
            status="partial",
            reasoning="Some criteria pass, non-blocking issues remain")
        assert agent.state.verdict == "partial"

    def test_verdict_invalid(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_verdict(status="maybe")
        assert "Error" in result
        assert agent.state.verdict == ""

    def test_run_command_read_only(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_run_command("rm -rf /")
        assert "blocked" in result.lower() or "error" in result.lower()


# ===========================================================================
# Lifecycle
# ===========================================================================

class TestReviewerLifecycle:
    def test_criteria_loaded_on_start(self, tmp_path):
        agent = ReviewerAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I'll start reviewing"):
            agent.start(
                project_root=str(tmp_path),
                review_scope="Auth module",
                files_to_review=["src/auth.py"],
                criteria=["Login works", "Passwords hashed"],
            )
        assert agent.state.review_scope == "Auth module"
        assert agent.state.files_to_review == ["src/auth.py"]
        assert len(agent.state.criteria) == 2

    def test_criteria_as_json_string(self, tmp_path):
        agent = ReviewerAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="ready"):
            agent.start(
                project_root=str(tmp_path),
                criteria='["C1", "C2"]',
            )
        assert len(agent.state.criteria) == 2

    def test_report_structure(self, tmp_path):
        agent = ReviewerAgent(backend="ollama")
        agent.state.review_scope = "Test review"
        agent.state.files_to_review = ["a.py"]
        agent.state.criteria = ["C1"]
        agent.state.criteria_results = [
            {"criterion": "C1", "status": "pass"}]
        agent.state.issues = [
            {"severity": "low", "location": "a.py:1",
             "issue": "naming", "suggestion": "rename"}]
        agent.state.blocking_count = 0
        agent.state.warning_count = 1
        agent.state.verdict = "pass"
        agent.state.verdict_reasoning = "All good"

        report = build_reviewer_report(agent.state)
        assert report["review_scope"] == "Test review"
        assert report["criteria_count"] == 1
        assert len(report["criteria_results"]) == 1
        assert len(report["issues"]) == 1
        assert report["blocking_count"] == 0
        assert report["verdict"] == "pass"

    def test_full_review_loop(self, tmp_path):
        (tmp_path / "code.py").write_text("x = 1\n")
        agent = ReviewerAgent(backend="ollama")
        responses = [
            "I'll read the code",
            '```json\n{"tool": "read_file", "params": {"path": "code.py"}}\n```',
            '```json\n{"tool": "check_criterion", "params": {"criterion": "Variable defined", "status": "pass", "evidence": "x=1 on line 1"}}\n```',
            '```json\n{"tool": "verdict", "params": {"status": "pass", "reasoning": "All good"}}\n```',
        ]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call",
                          side_effect=mock_call):
            report = agent.run(
                project_root=str(tmp_path),
                review_scope="Check variables",
                criteria=["Variable defined"],
            )
        assert report["phase"] == "done"
        assert agent.state.verdict == "pass"
        assert len(agent.state.criteria_results) == 1
