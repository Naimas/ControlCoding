"""Tests for debugger_agent.py - DebuggerAgent (Sprint S9)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import ReportBuilder, BaseAgent
from debugger_agent import (
    DebuggerState, DebuggerAgent, build_debugger_report,
    VALID_CONFIDENCE,
)


# ===========================================================================
# DebuggerState
# ===========================================================================

class TestDebuggerState:
    def test_defaults(self):
        s = DebuggerState()
        assert s.agent_type == "debugger"
        assert s.bug_description == ""
        assert s.hypotheses == []
        assert s.observations == []
        assert s.root_cause == ""
        assert s.confidence == ""

    def test_to_context_summary(self):
        s = DebuggerState()
        s.current_step = 5
        s.bug_description = "Login returns 500"
        s.hypotheses = [
            {"id": 1, "hypothesis": "DB connection timeout",
             "status": "invalidated"},
            {"id": 2, "hypothesis": "Missing env var",
             "status": "validated"},
        ]
        s.observations = ["Checked DB: OK", "Env var missing"]
        s.root_cause = "AUTH_SECRET not set"
        s.suggested_fix = "Add AUTH_SECRET to .env"
        s.confidence = "high"

        summary = s.to_context_summary()
        assert "Login returns 500" in summary
        assert "invalidated" in summary
        assert "validated" in summary
        assert "AUTH_SECRET" in summary
        assert "high" in summary


# ===========================================================================
# Tools
# ===========================================================================

class TestDebuggerTools:
    def _make_agent(self, tmp_path):
        agent = DebuggerAgent(backend="ollama", mode="autonomous")
        agent._project_root = tmp_path
        agent._register_tools()
        return agent

    def test_form_hypothesis(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_form_hypothesis(
            hypothesis="DB connection pool exhausted",
            rationale="Error occurs under load, DB pool is default size",
        )
        assert "H1" in result
        assert len(agent.state.hypotheses) == 1
        assert agent.state.hypotheses[0]["status"] == "untested"
        assert agent.state.hypotheses[0]["rationale"] != ""

    def test_form_multiple_hypotheses(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_form_hypothesis("H1 text", "R1")
        agent._tool_form_hypothesis("H2 text", "R2")
        agent._tool_form_hypothesis("H3 text", "R3")
        assert len(agent.state.hypotheses) == 3
        assert agent.state.hypotheses[2]["id"] == 3

    def test_test_hypothesis_validated(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_form_hypothesis("Missing env var", "")
        with patch("debugger_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="AUTH_SECRET: not set\n", stderr="")
            result = agent._tool_test_hypothesis(
                hypothesis_id=1,
                command="env | grep AUTH_SECRET",
                expected="not set",
            )
        assert "validated" in result.lower()
        assert agent.state.hypotheses[0]["status"] == "validated"

    def test_test_hypothesis_invalidated(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_form_hypothesis("Port conflict", "")
        with patch("debugger_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Port 8080 is free\n", stderr="")
            result = agent._tool_test_hypothesis(
                hypothesis_id=1,
                command="check port 8080",
                expected="in use",
            )
        assert "invalidated" in result.lower()
        assert agent.state.hypotheses[0]["status"] == "invalidated"

    def test_test_hypothesis_not_found(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_test_hypothesis(
            hypothesis_id=99, command="echo test")
        assert "not found" in result.lower()

    def test_test_hypothesis_no_expected(self, tmp_path):
        """Without expected value, status is 'tested' for LLM to interpret."""
        agent = self._make_agent(tmp_path)
        agent._tool_form_hypothesis("Check logs", "")
        with patch("debugger_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="some output\n", stderr="")
            result = agent._tool_test_hypothesis(
                hypothesis_id=1, command="cat log.txt")
        assert agent.state.hypotheses[0]["status"] == "tested"

    def test_read_logs(self, tmp_path):
        log_file = tmp_path / "app.log"
        lines = [f"[INFO] line {i}" for i in range(200)]
        log_file.write_text("\n".join(lines))

        agent = self._make_agent(tmp_path)
        result = agent._tool_read_logs("app.log", tail_lines=50)
        assert "line 199" in result
        assert "line 100" not in result
        assert len(agent.state.observations) == 1

    def test_read_logs_not_found(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_read_logs("nonexistent.log")
        assert "not found" in result.lower()

    def test_search_code(self, tmp_path):
        (tmp_path / "code.py").write_text(
            "def broken_func():\n    raise ValueError('oops')\n")
        agent = self._make_agent(tmp_path)
        with patch("debugger_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="code.py:2:    raise ValueError('oops')\n",
                stderr="")
            result = agent._tool_search_code("ValueError")
        assert "ValueError" in result

    def test_call_visual_agent_placeholder(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_call_visual_agent(
            "Screen goes black after login")
        assert "not spawned" in result.lower()
        assert len(agent.state.observations) == 1

    def test_diagnosis_high_confidence(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_form_hypothesis("Missing env var", "")
        result = agent._tool_diagnosis(
            root_cause="AUTH_SECRET environment variable not set",
            suggested_fix="Add AUTH_SECRET=xxx to .env file",
            confidence="high",
        )
        assert "high" in result.lower()
        assert agent.state.root_cause == "AUTH_SECRET environment variable not set"
        assert agent.state.confidence == "high"
        assert agent._done_flag is True

    def test_diagnosis_invalid_confidence(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_diagnosis(
            root_cause="Unknown", suggested_fix="Investigate more",
            confidence="very_high")
        # Should default to medium
        assert agent.state.confidence == "medium"

    def test_run_command_blocked(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_run_command("rm -rf /")
        assert "blocked" in result.lower()

    def test_run_command_records_observation(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_run_command("echo test_output")
        assert "test_output" in result
        assert len(agent.state.observations) >= 1


# ===========================================================================
# Lifecycle
# ===========================================================================

class TestDebuggerLifecycle:
    def test_bug_loaded_on_start(self, tmp_path):
        agent = DebuggerAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I'll investigate"):
            agent.start(
                project_root=str(tmp_path),
                bug_description="Login returns 500",
                error_output="Internal Server Error",
                stack_trace="File auth.py, line 42",
                relevant_files=["src/auth.py"],
                already_tried=["Restarted server"],
            )
        assert agent.state.bug_description == "Login returns 500"
        assert agent.state.error_output == "Internal Server Error"
        assert agent.state.stack_trace == "File auth.py, line 42"
        assert "src/auth.py" in agent.state.relevant_files
        assert "Restarted server" in agent.state.already_tried

    def test_report_structure(self):
        state = DebuggerState()
        state.bug_description = "Crash on save"
        state.hypotheses = [
            {"id": 1, "hypothesis": "Disk full", "status": "invalidated"},
            {"id": 2, "hypothesis": "Permission denied",
             "status": "validated"},
        ]
        state.root_cause = "Write permission missing on /data"
        state.suggested_fix = "chmod 755 /data"
        state.confidence = "high"

        report = build_debugger_report(state)
        assert report["bug_description"] == "Crash on save"
        assert report["hypothesis_count"] == 2
        assert report["validated_count"] == 1
        assert report["invalidated_count"] == 1
        assert report["root_cause"] == "Write permission missing on /data"
        assert report["confidence"] == "high"

    def test_full_debug_loop(self, tmp_path):
        agent = DebuggerAgent(backend="ollama")
        responses = [
            "I'll investigate the bug",
            '```json\n{"tool": "form_hypothesis", "params": {"hypothesis": "Missing config", "rationale": "Error mentions config"}}\n```',
            '```json\n{"tool": "run_command", "params": {"command": "echo config_missing"}}\n```',
            '```json\n{"tool": "diagnosis", "params": {"root_cause": "Config file missing", "suggested_fix": "Create config.json", "confidence": "high"}}\n```',
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
                bug_description="App crashes on start",
            )
        assert report["phase"] == "done"
        assert agent.state.root_cause != ""
        assert len(agent.state.hypotheses) >= 1
