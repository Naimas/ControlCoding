"""Tests for expert_agent.py - ExpertAgent (Sprint S11)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import ReportBuilder, BaseAgent
from expert_agent import (
    ExpertState, ExpertAgent, build_expert_report,
    DOMAIN_PROMPTS, VALID_SEVERITIES, VALID_PRIORITIES, VALID_EFFORTS,
)


# ===========================================================================
# ExpertState
# ===========================================================================

class TestExpertState:
    def test_defaults(self):
        s = ExpertState()
        assert s.agent_type == "expert"
        assert s.domain == ""
        assert s.analysis_findings == []
        assert s.recommendations == []

    def test_to_context_summary(self):
        s = ExpertState()
        s.current_step = 5
        s.domain = "security"
        s.question = "Check auth module"
        s.analysis_findings = [
            {"topic": "SQL injection", "finding": "Unparameterized query",
             "severity": "critical", "location": "auth.py:42"}
        ]
        s.recommendations = [
            {"recommendation": "Use parameterized queries",
             "priority": "critical", "effort": "small"}
        ]

        summary = s.to_context_summary()
        assert "security" in summary
        assert "SQL injection" in summary
        assert "parameterized" in summary


# ===========================================================================
# Domain Configuration
# ===========================================================================

class TestDomainConfig:
    def test_predefined_domains_exist(self):
        expected = ["security", "engineering", "design-intent",
                    "methodology", "performance", "accessibility"]
        for d in expected:
            assert d in DOMAIN_PROMPTS

    def test_custom_domain_fallback(self, tmp_path):
        """Custom domain generates generic prompt, not crash."""
        agent = ExpertAgent(
            backend="ollama", domain="volcanology")
        agent._project_root = tmp_path
        agent._register_tools()
        prompt = agent._build_system_prompt()
        assert "volcanology" in prompt.lower()

    def test_predefined_domain_prompt(self, tmp_path):
        agent = ExpertAgent(backend="ollama", domain="security")
        agent._project_root = tmp_path
        agent._register_tools()
        prompt = agent._build_system_prompt()
        assert "OWASP" in prompt


# ===========================================================================
# Tools
# ===========================================================================

class TestExpertTools:
    def _make_agent(self, tmp_path, domain="engineering"):
        agent = ExpertAgent(
            backend="ollama", mode="autonomous", domain=domain)
        agent._project_root = tmp_path
        agent._register_tools()
        return agent

    def test_analyze_finding(self, tmp_path):
        agent = self._make_agent(tmp_path, "security")
        result = agent._tool_analyze(
            topic="SQL injection",
            finding="Query uses string concatenation",
            severity="critical",
            location="auth.py:42",
        )
        assert "critical" in result
        assert len(agent.state.analysis_findings) == 1
        f = agent.state.analysis_findings[0]
        assert f["topic"] == "SQL injection"
        assert f["severity"] == "critical"
        assert f["location"] == "auth.py:42"

    def test_analyze_all_severities(self, tmp_path):
        agent = self._make_agent(tmp_path)
        for sev in VALID_SEVERITIES:
            agent._tool_analyze(f"Topic {sev}", f"Finding {sev}", sev)
        assert len(agent.state.analysis_findings) == 5

    def test_analyze_invalid_severity(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_analyze("X", "Y", "urgent")
        assert "Error" in result
        assert len(agent.state.analysis_findings) == 0

    def test_recommend(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_recommend(
            recommendation="Use parameterized queries",
            priority="critical",
            effort="small",
        )
        assert "critical" in result
        assert len(agent.state.recommendations) == 1
        r = agent.state.recommendations[0]
        assert r["priority"] == "critical"
        assert r["effort"] == "small"

    def test_recommend_all_priorities(self, tmp_path):
        agent = self._make_agent(tmp_path)
        for p in VALID_PRIORITIES:
            agent._tool_recommend(f"Rec {p}", p, "medium")
        assert len(agent.state.recommendations) == 4

    def test_recommend_invalid_priority(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_recommend("X", "urgent", "medium")
        assert "Error" in result

    def test_recommend_invalid_effort(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_recommend("X", "high", "huge")
        assert "Error" in result

    def test_search_code(self, tmp_path):
        (tmp_path / "code.py").write_text("password = 'secret123'\n")
        agent = self._make_agent(tmp_path, "security")
        with patch("expert_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="code.py:1:password = 'secret123'\n", stderr="")
            result = agent._tool_search_code("password")
        assert "password" in result

    def test_ask_question_autonomous(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_ask_question("Which module is critical?")
        assert "autonomous" in result.lower()
        assert len(agent.state.questions_asked) == 1

    def test_run_command_blocked(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_run_command("rm -rf /")
        assert "blocked" in result.lower()

    def test_done(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_done("Analysis complete")
        assert "Done" in result
        assert agent._done_flag is True


# ===========================================================================
# Lifecycle
# ===========================================================================

class TestExpertLifecycle:
    def test_domain_loaded_on_start(self, tmp_path):
        agent = ExpertAgent(backend="ollama", domain="performance")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I'll analyze performance"):
            agent.start(
                project_root=str(tmp_path),
                domain="performance",
                question="Find bottlenecks in the API",
            )
        assert agent.state.domain == "performance"
        assert agent.state.question == "Find bottlenecks in the API"

    def test_report_structure(self):
        state = ExpertState()
        state.domain = "security"
        state.question = "Audit auth module"
        state.analysis_findings = [
            {"topic": "XSS", "finding": "Unescaped output",
             "severity": "high", "location": "views.py:10"},
            {"topic": "CSRF", "finding": "No token",
             "severity": "critical", "location": "forms.py:5"},
        ]
        state.recommendations = [
            {"recommendation": "Escape all output",
             "priority": "high", "effort": "small"},
        ]

        report = build_expert_report(state)
        assert report["domain"] == "security"
        assert report["findings_count"] == 2
        assert report["critical_findings"] == 1
        assert report["recommendations_count"] == 1

    def test_full_expert_loop(self, tmp_path):
        (tmp_path / "app.py").write_text("x = eval(input())\n")
        agent = ExpertAgent(backend="ollama", domain="security")
        responses = [
            "I'll analyze for security issues",
            '```json\n{"tool": "read_file", "params": {"path": "app.py"}}\n```',
            '```json\n{"tool": "analyze", "params": {"topic": "Code injection", "finding": "eval() on user input", "severity": "critical", "location": "app.py:1"}}\n```',
            '```json\n{"tool": "recommend", "params": {"recommendation": "Replace eval with safe parser", "priority": "critical", "effort": "small"}}\n```',
            '```json\n{"tool": "done", "params": {"summary": "1 critical finding"}}\n```',
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
                domain="security",
                question="Audit app.py",
            )
        assert report["phase"] == "done"
        assert len(agent.state.analysis_findings) >= 1
        assert len(agent.state.recommendations) >= 1
