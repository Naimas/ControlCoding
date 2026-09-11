"""Tests for socratic_agent.py - SocraticAgent (Sprint S10)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import ReportBuilder, BaseAgent
from socratic_agent import (
    SocraticState, SocraticAgent, build_socratic_report,
    VALID_RISK_SEVERITY, VALID_RECOMMENDATIONS,
)


# ===========================================================================
# SocraticState
# ===========================================================================

class TestSocraticState:
    def test_defaults(self):
        s = SocraticState()
        assert s.agent_type == "socratic"
        assert s.topic == ""
        assert s.questions == []
        assert s.risks == []
        assert s.recommendation == ""

    def test_to_context_summary(self):
        s = SocraticState()
        s.current_step = 5
        s.topic = "Use microservices vs monolith"
        s.questions = [
            {"id": 1, "question": "What is the team size?",
             "answer": "3 developers"},
        ]
        s.risks = [
            {"risk": "Operational overhead too high for 3 devs",
             "severity": "high"}
        ]
        s.recommendation = "reconsider"
        s.recommendation_reasoning = "Team too small for microservices"

        summary = s.to_context_summary()
        assert "microservices" in summary
        assert "team size" in summary.lower()
        assert "3 developers" in summary
        assert "Operational overhead" in summary
        assert "reconsider" in summary


# ===========================================================================
# Tools
# ===========================================================================

class TestSocraticTools:
    def _make_agent(self, tmp_path):
        agent = SocraticAgent(backend="ollama", mode="autonomous")
        agent._project_root = tmp_path
        agent._register_tools()
        return agent

    def test_ask_question(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_ask_question(
            question="What happens if the DB goes down?",
            target="Availability assumption",
        )
        assert "Q1" in result
        assert len(agent.state.questions) == 1
        assert agent.state.questions[0]["target"] == "Availability assumption"

    def test_ask_multiple_questions(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_ask_question("Q1?", "A1")
        agent._tool_ask_question("Q2?", "A2")
        agent._tool_ask_question("Q3?", "A3")
        assert len(agent.state.questions) == 3
        assert agent.state.questions[2]["id"] == 3

    def test_probe_deeper(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_ask_question("Initial question?", "assumption")
        result = agent._tool_probe_deeper(
            previous_answer="We use Redis for caching",
            followup="What if Redis is a single point of failure?",
        )
        assert "Q1" in result
        q = agent.state.questions[0]
        assert q["answer"] == "We use Redis for caching"
        assert len(q["followups"]) == 1
        assert "single point" in q["followups"][0]["followup"]

    def test_probe_deeper_no_question(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_probe_deeper("answer", "followup")
        assert "Error" in result

    def test_identify_risk(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_ask_question("What if load spikes?", "scalability")
        result = agent._tool_identify_risk(
            risk="Auto-scaling not configured",
            severity="high",
            mitigation="Add HPA with CPU threshold",
        )
        assert "high" in result
        assert len(agent.state.risks) == 1
        r = agent.state.risks[0]
        assert r["severity"] == "high"
        assert r["mitigation"] == "Add HPA with CPU threshold"
        assert r["discovered_from_question"] == 1

    def test_identify_risk_all_severities(self, tmp_path):
        agent = self._make_agent(tmp_path)
        for sev in VALID_RISK_SEVERITY:
            agent._tool_identify_risk(f"Risk {sev}", sev, "fix")
        assert len(agent.state.risks) == 4

    def test_identify_risk_invalid_severity(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_identify_risk("bad", "urgent", "fix")
        assert "Error" in result
        assert len(agent.state.risks) == 0

    def test_summarize_proceed(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_summarize_session(
            recommendation="proceed",
            reasoning="All assumptions validated, risks manageable",
        )
        assert "proceed" in result
        assert agent.state.recommendation == "proceed"
        assert agent._done_flag is True

    def test_summarize_reconsider(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_summarize_session(
            recommendation="reconsider",
            reasoning="Critical risk unmitigated",
        )
        assert agent.state.recommendation == "reconsider"

    def test_summarize_stop(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_summarize_session(
            recommendation="stop",
            reasoning="Fundamental flaw in approach",
        )
        assert agent.state.recommendation == "stop"

    def test_summarize_invalid(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_summarize_session(
            recommendation="maybe")
        assert "Error" in result
        assert agent.state.recommendation == ""

    def test_run_command_blocked(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_run_command("rm -rf /")
        assert "blocked" in result.lower()


# ===========================================================================
# Lifecycle
# ===========================================================================

class TestSocraticLifecycle:
    def test_topic_loaded(self, tmp_path):
        agent = SocraticAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I'll challenge this"):
            agent.start(
                project_root=str(tmp_path),
                topic="Using inheritance for all agents",
                context="vs composition pattern",
            )
        assert agent.state.topic == "Using inheritance for all agents"
        assert agent.state.context == "vs composition pattern"

    def test_report_structure(self):
        state = SocraticState()
        state.topic = "Microservices decision"
        state.questions = [
            {"id": 1, "question": "Team size?", "answer": "3"},
        ]
        state.risks = [
            {"risk": "Ops overhead", "severity": "high",
             "mitigation": "none"},
        ]
        state.recommendation = "reconsider"
        state.recommendation_reasoning = "Team too small"

        report = build_socratic_report(state)
        assert report["topic"] == "Microservices decision"
        assert report["questions_count"] == 1
        assert report["risks_count"] == 1
        assert report["recommendation"] == "reconsider"

    def test_full_socratic_loop(self, tmp_path):
        agent = SocraticAgent(backend="ollama")
        responses = [
            "I'll challenge your assumptions",
            '```json\n{"tool": "ask_question", "params": {"question": "What if the DB goes down?", "target": "availability"}}\n```',
            '```json\n{"tool": "identify_risk", "params": {"risk": "No failover", "severity": "high", "mitigation": "Add replica"}}\n```',
            '```json\n{"tool": "summarize_session", "params": {"recommendation": "reconsider", "reasoning": "Critical gap in HA"}}\n```',
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
                topic="Single DB architecture",
            )
        assert report["phase"] == "done"
        assert agent.state.recommendation == "reconsider"
        assert len(agent.state.risks) >= 1
