"""Tests for base_agent.py - BaseAgent framework (Sprint S0)."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from dataclasses import dataclass, field

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, ActionRecord, Finding, ToolCall, ToolResult,
    BackendAdapter, ToolExecutor, ReportBuilder, BaseAgent,
)


@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path, monkeypatch):
    """Keep unit tests independent from the LAB's engagement configuration."""
    monkeypatch.chdir(tmp_path)


# ===========================================================================
# AgentStateBase
# ===========================================================================

class TestAgentStateBase:
    def test_defaults(self):
        s = AgentStateBase()
        assert s.session_id == ""
        assert s.current_step == 0
        assert s.total_steps == 0
        assert s.max_steps == 200
        assert s.timeout_seconds == 600.0
        assert s.mode == "autonomous"
        assert s.phase == "init"

    def test_next_step(self):
        s = AgentStateBase()
        assert s.next_step() == 1
        assert s.next_step() == 2
        assert s.current_step == 2
        assert s.total_steps == 2

    def test_log_action(self):
        s = AgentStateBase()
        s.next_step()
        s.log_action("click", {"x": 10, "y": 20}, "clicked", True)
        assert len(s.actions_log) == 1
        assert s.actions_log[0].tool == "click"
        assert s.actions_log[0].success is True

    def test_log_finding(self):
        s = AgentStateBase()
        s.next_step()
        s.log_finding("Found a bug", ["screenshot.png"], "bug")
        assert len(s.findings) == 1
        assert s.findings[0].severity == "bug"
        assert s.findings[0].evidence == ["screenshot.png"]

    def test_log_error(self):
        s = AgentStateBase()
        s.current_step = 5
        s.log_error("Something broke")
        assert len(s.errors) == 1
        assert "[step 5]" in s.errors[0]

    def test_to_context_summary(self):
        s = AgentStateBase(agent_type="visual", mode="autonomous", phase="testing")
        s.current_step = 10
        s.total_steps = 10
        s.total_llm_calls = 5
        s.log_finding("Button is blue", severity="pass")
        summary = s.to_context_summary()
        assert "visual" in summary
        assert "step 10" in summary
        assert "Button is blue" in summary

    def test_to_dict(self):
        s = AgentStateBase(session_id="abc", agent_type="test")
        d = s.to_dict()
        assert isinstance(d, dict)
        assert d["session_id"] == "abc"
        assert d["agent_type"] == "test"


# ===========================================================================
# ActionRecord and Finding
# ===========================================================================

class TestDataStructures:
    def test_action_record_auto_timestamp(self):
        r = ActionRecord(step=1, tool="click", params={}, result="ok", success=True)
        assert r.timestamp != ""

    def test_action_record_explicit_timestamp(self):
        r = ActionRecord(step=1, tool="click", params={}, result="ok",
                         success=True, timestamp="2026-01-01T00:00:00")
        assert r.timestamp == "2026-01-01T00:00:00"

    def test_finding_defaults(self):
        f = Finding(step=1, description="test")
        assert f.severity == "info"
        assert f.evidence == []

    def test_tool_call(self):
        tc = ToolCall(name="click", params={"x": 10})
        assert tc.name == "click"
        assert tc.raw == ""

    def test_tool_result(self):
        tr = ToolResult(tool="click", params={}, result="ok", success=True)
        assert tr.image_path is None


# ===========================================================================
# BackendAdapter
# ===========================================================================

class TestBackendAdapter:
    def test_init_valid(self):
        ba = BackendAdapter("anthropic", "claude-sonnet-4-20250514")
        assert ba.backend == "anthropic"
        assert ba.model == "claude-sonnet-4-20250514"

    def test_init_invalid_backend(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            BackendAdapter("invalid_backend")

    def test_set_system_prompt(self):
        ba = BackendAdapter("ollama")
        ba.set_system_prompt("You are a tester")
        assert ba.messages[0]["role"] == "system"
        assert ba.messages[0]["content"] == "You are a tester"

    def test_set_system_prompt_replaces(self):
        ba = BackendAdapter("ollama")
        ba.set_system_prompt("First")
        ba.set_system_prompt("Second")
        system_msgs = [m for m in ba.messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "Second"

    def test_add_user_message(self):
        ba = BackendAdapter("ollama")
        ba.add_user_message("Hello")
        assert ba.messages[-1]["role"] == "user"
        assert ba.messages[-1]["content"] == "Hello"

    def test_add_user_message_with_image(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 100)
        ba = BackendAdapter("ollama")
        with patch("base_agent._load_image_b64", return_value=("b64data", "image/png")):
            ba.add_user_message("Look at this", str(img))
        assert ba.messages[-1]["_image"] == ("b64data", "image/png")

    def test_add_assistant_message(self):
        ba = BackendAdapter("ollama")
        ba.add_assistant_message("I see a button")
        assert ba.messages[-1]["role"] == "assistant"

    def test_get_message_count(self):
        ba = BackendAdapter("ollama")
        ba.add_user_message("A")
        ba.add_assistant_message("B")
        assert ba.get_message_count() == 2

    def test_estimate_tokens(self):
        ba = BackendAdapter("ollama")
        ba.add_user_message("Hello world")  # ~3 tokens
        est = ba.estimate_tokens()
        assert est > 0

    def test_estimate_tokens_with_image(self):
        ba = BackendAdapter("ollama")
        ba.messages.append({"role": "user", "content": "img",
                            "_image": ("data", "image/png")})
        est = ba.estimate_tokens()
        assert est >= 1000  # image = ~1000 tokens

    def test_summarize_and_rebuild(self):
        ba = BackendAdapter("ollama")
        ba.set_system_prompt("System")
        for i in range(20):
            ba.add_user_message(f"msg {i}")
            ba.add_assistant_message(f"reply {i}")
        assert ba.get_message_count() == 41  # 1 system + 40

        state = AgentStateBase(agent_type="test", current_step=20)
        ba.summarize_and_rebuild(state, keep_last_n=4)

        # Should have: system + summary + last 4
        assert ba.get_message_count() == 6
        assert ba.messages[0]["role"] == "system"
        assert "STATE SUMMARY" in ba.messages[1]["content"]

    def test_call_ollama_mock(self):
        ba = BackendAdapter("ollama", "llama3")
        ba.set_system_prompt("test")
        ba.add_user_message("hello")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"message": {"content": "I am an AI"}}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ba.call()
        assert "I am an AI" in result
        assert ba.messages[-1]["role"] == "assistant"

    def test_call_anthropic_mock(self):
        ba = BackendAdapter("anthropic", "claude-sonnet-4-20250514")
        ba.set_system_prompt("test")
        ba.add_user_message("hello")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"content": [{"type": "text", "text": "Response"}]}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = ba.call()
        assert "Response" in result

    def test_call_anthropic_no_key(self):
        ba = BackendAdapter("anthropic")
        ba.set_system_prompt("test")
        ba.add_user_message("hello")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = ba.call()
        assert "ERROR" in result

    def test_call_session_not_implemented(self):
        ba = BackendAdapter("session")
        ba.set_system_prompt("test")
        ba.add_user_message("hello")
        result = ba.call()
        assert "not yet implemented" in result


# ===========================================================================
# ToolExecutor
# ===========================================================================

class TestToolExecutor:
    def test_register_and_list(self):
        te = ToolExecutor("ollama")
        te.register("click", lambda x, y: f"clicked {x},{y}", "Click at coords")
        assert "click" in te.get_registered_names()
        assert "Click at coords" in te.get_tool_descriptions()

    def test_parse_json_fence(self):
        te = ToolExecutor("ollama")
        response = 'Some text\n```json\n{"tool": "click", "params": {"x": 10, "y": 20}}\n```\nMore text'
        calls = te.parse(response)
        assert len(calls) == 1
        assert calls[0].name == "click"
        assert calls[0].params == {"x": 10, "y": 20}

    def test_parse_multiple_fences(self):
        te = ToolExecutor("ollama")
        response = '```json\n{"tool": "click", "params": {"x": 1}}\n```\ntext\n```json\n{"tool": "wait", "params": {"seconds": 2}}\n```'
        calls = te.parse(response)
        assert len(calls) == 2
        assert calls[0].name == "click"
        assert calls[1].name == "wait"

    def test_parse_no_tool_key(self):
        te = ToolExecutor("ollama")
        response = '```json\n{"name": "click"}\n```'
        calls = te.parse(response)
        assert len(calls) == 0

    def test_parse_invalid_json(self):
        te = ToolExecutor("ollama")
        response = '```json\n{invalid json}\n```'
        calls = te.parse(response)
        assert len(calls) == 0

    def test_parse_no_fences(self):
        te = ToolExecutor("ollama")
        calls = te.parse("Just some text without any tool calls")
        assert len(calls) == 0

    def test_execute_registered(self):
        te = ToolExecutor("ollama")
        te.register("greet", lambda name: f"Hello {name}")
        result = te.execute(ToolCall(name="greet", params={"name": "World"}))
        assert result.success is True
        assert result.result == "Hello World"

    def test_execute_unknown(self):
        te = ToolExecutor("ollama")
        result = te.execute(ToolCall(name="fly", params={}))
        assert result.success is False
        assert "Unknown tool" in result.result

    def test_execute_error(self):
        def broken():
            raise RuntimeError("oops")
        te = ToolExecutor("ollama")
        te.register("broken", broken)
        result = te.execute(ToolCall(name="broken", params={}))
        assert result.success is False
        assert "oops" in result.result

    def test_execute_tuple_return(self):
        te = ToolExecutor("ollama")
        te.register("screenshot", lambda: ("saved", "/tmp/img.png"))
        result = te.execute(ToolCall(name="screenshot", params={}))
        assert result.success is True
        assert result.image_path == "/tmp/img.png"


# ===========================================================================
# ReportBuilder
# ===========================================================================

class TestReportBuilder:
    def test_build(self):
        s = AgentStateBase(session_id="abc", agent_type="visual",
                           started_at="2026-01-01", mode="autonomous")
        s.total_steps = 10
        s.total_llm_calls = 5
        s.log_finding("Bug found", severity="bug")

        report = ReportBuilder.build(s)
        assert report["session_id"] == "abc"
        assert report["agent_type"] == "visual"
        assert report["total_steps"] == 10
        assert len(report["findings"]) == 1
        assert report["findings"][0]["severity"] == "bug"
        assert "completed_at" in report

    def test_write(self, tmp_path):
        report = {"test": True}
        path = ReportBuilder.write(report, str(tmp_path / "report.json"))
        assert Path(path).exists()
        data = json.loads(Path(path).read_text())
        assert data["test"] is True


# ===========================================================================
# BaseAgent (via concrete test agent)
# ===========================================================================

class _TestState(AgentStateBase):
    """Minimal state for testing."""
    agent_type: str = "test"


class _TestAgent(BaseAgent):
    """Minimal concrete agent for testing BaseAgent."""

    def __init__(self, *args, **kwargs):
        self._done_flag = False
        self._start_msg = "Start testing"
        super().__init__(*args, **kwargs)

    def _create_state(self):
        return _TestState(agent_type="test")

    def _build_system_prompt(self, **context):
        return "You are a test agent. " + context.get("extra", "")

    def _register_tools(self):
        self.tools.register("echo", self._echo, "Echo back the input")
        self.tools.register("done", self._done_tool, "Signal completion")

    def _on_start(self, **context):
        return self._start_msg

    def _is_done(self, response):
        return self._done_flag

    def _echo(self, text=""):
        return f"Echo: {text}"

    def _done_tool(self):
        self._done_flag = True
        return "Done"


class TestBaseAgent:
    def test_init(self):
        with patch.object(BackendAdapter, "__init__", return_value=None):
            agent = _TestAgent(backend="ollama", model="test")
        assert agent.state.agent_type == "test"
        assert agent.mode == "autonomous"

    def test_start(self):
        agent = _TestAgent(backend="ollama")
        mock_resp = "I am ready to test"
        with patch.object(agent.backend_adapter, "call", return_value=mock_resp):
            result = agent.start(extra="context here")
        assert result == mock_resp
        assert agent.state.session_id != ""
        assert agent.state.phase == "active"
        assert agent.state.total_llm_calls == 1
        assert "echo" in agent.tools.get_registered_names()

    def test_step_with_user_input(self):
        agent = _TestAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call", return_value="initial"):
            agent.start()
        with patch.object(agent.backend_adapter, "call",
                          return_value="I see your message"):
            result = agent.step("Look at the button")
        assert result == "I see your message"
        assert agent.state.total_llm_calls == 2

    def test_finish(self):
        agent = _TestAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call", return_value="ready"):
            agent.start()
        report = agent.finish()
        assert report["agent_type"] == "test"
        assert report["phase"] == "done"
        assert "completed_at" in report

    def test_timeout_stops_loop(self):
        agent = _TestAgent(backend="ollama")
        agent.state.timeout_seconds = 0.01
        with patch.object(agent.backend_adapter, "call", return_value="ready"):
            agent.start()
        agent._start_time -= agent.state.timeout_seconds + 1
        # Loop should stop due to timeout
        agent._agent_loop()
        assert any("timeout" in e.lower() for e in agent.state.errors)

    def test_max_steps_stops_loop(self):
        agent = _TestAgent(backend="ollama")
        agent.state.max_steps = 0  # immediate limit
        with patch.object(agent.backend_adapter, "call", return_value="ready"):
            agent.start()
        agent._agent_loop()
        assert any("max steps" in e.lower() for e in agent.state.errors)

    def test_run_single_call(self):
        agent = _TestAgent(backend="ollama")
        # LLM returns done tool on first response
        responses = [
            "Starting",
            '```json\n{"tool": "done", "params": {}}\n```',
        ]
        call_count = [0]
        def mock_call(tools=None):
            resp = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call", side_effect=mock_call):
            report = agent.run()
        assert report["phase"] == "done"
        assert report["agent_type"] == "test"

    def test_tool_execution_in_loop(self):
        agent = _TestAgent(backend="ollama")
        responses = [
            "initial",
            '```json\n{"tool": "echo", "params": {"text": "hello"}}\n```',
            '```json\n{"tool": "done", "params": {}}\n```',
        ]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call", side_effect=mock_call):
            report = agent.run()
        # echo tool should have been executed
        assert any(a.tool == "echo" for a in agent.state.actions_log
                   if isinstance(a, ActionRecord))

    def test_context_budget_triggers_summarize(self):
        agent = _TestAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call", return_value="ready"):
            agent.start()
        # Fake high token count
        with patch.object(agent.backend_adapter, "estimate_tokens",
                          return_value=110000):
            with patch.object(agent.backend_adapter, "summarize_and_rebuild") as mock_sum:
                agent._check_context_budget()
                mock_sum.assert_called_once()


class TestSpecialistRuntimeGateFailClosed:
    def test_consultation_call_gates_when_control_plane_utils_unavailable(self):
        agent = _TestAgent(backend="ollama")
        with patch.dict(sys.modules, {"control_plane_utils": None}), \
             patch.object(BackendAdapter, "call") as mock_call:
            result = agent._execute_consultation_call(
                "ArchitectAgent", "prompt", object())

        assert result.startswith("[GATED] control_plane_utils_unavailable:")
        assert "refusing specialist runtime path" in result
        assert agent.last_governance_denial["reason"] == (
            "control_plane_utils_unavailable")
        assert any("control_plane_utils_unavailable" in e
                   for e in agent.state.errors)
        mock_call.assert_not_called()

    def test_external_call_gates_when_control_plane_utils_unavailable(self):
        agent = _TestAgent(backend="ollama")
        with patch.dict(sys.modules, {"control_plane_utils": None}), \
             patch.object(BackendAdapter, "call") as mock_call:
            result = agent._execute_external_call("prompt", object())

        assert result.startswith("[GATED] control_plane_utils_unavailable:")
        assert "refusing specialist runtime path" in result
        assert agent.last_governance_denial["reason"] == (
            "control_plane_utils_unavailable")
        mock_call.assert_not_called()

    def test_consultation_call_gates_when_governance_check_fails(self):
        agent = _TestAgent(backend="ollama")
        with patch("control_plane_utils.check_specialist_runtime",
                   side_effect=RuntimeError("boom")), \
             patch.object(BackendAdapter, "call") as mock_call:
            result = agent._execute_consultation_call(
                "ArchitectAgent", "prompt", object())

        assert result.startswith("[GATED] governance_check_failed:")
        assert "refusing specialist runtime path" in result
        assert agent.last_governance_denial["reason"] == (
            "governance_check_failed")
        mock_call.assert_not_called()

    def test_external_call_gates_when_governance_check_fails(self):
        agent = _TestAgent(backend="ollama")
        with patch("control_plane_utils.check_specialist_runtime",
                   side_effect=RuntimeError("boom")), \
             patch.object(BackendAdapter, "call") as mock_call:
            result = agent._execute_external_call("prompt", object())

        assert result.startswith("[GATED] governance_check_failed:")
        assert "refusing specialist runtime path" in result
        assert agent.last_governance_denial["reason"] == (
            "governance_check_failed")
        mock_call.assert_not_called()

    def test_missing_config_accepts_only_explicit_backend(self, tmp_path):
        agent = _TestAgent(backend="ollama")
        agent.state.project_root = str(tmp_path)

        gate = agent._check_specialist_runtime_gate(
            role_ref="architect",
            component="consultant",
            requested_backend="claude",
        )

        assert gate["allowed"] is True
        assert gate["enforced"] is True
        assert gate["reason"] == "explicit_backend"

    def test_consultation_call_fails_closed_without_component_backend(
            self, tmp_path):
        agent = _TestAgent(backend="")
        agent.state.project_root = str(tmp_path)
        with patch.dict(sys.modules, {"concierge": None}), \
             patch.object(BackendAdapter, "call",
                          return_value="consulted") as mock_call:
            result = agent._execute_consultation_call(
                "ArchitectAgent", "prompt", object())

        assert result.startswith("[GATED] missing_backend:")
        mock_call.assert_not_called()

    def test_external_call_fails_closed_without_component_backend(self, tmp_path):
        agent = _TestAgent(backend="")
        agent.state.project_root = str(tmp_path)
        with patch.object(BackendAdapter, "call",
                          return_value="external consulted") as mock_call:
            result = agent._execute_external_call("prompt", object())

        assert result.startswith("[GATED] missing_backend:")
        mock_call.assert_not_called()

    @pytest.mark.parametrize("backend", ["", "   ", "fallback"])
    def test_agent_start_missing_backend_has_zero_calls(self, tmp_path, backend):
        agent = _TestAgent(backend=backend)
        agent.state.project_root = str(tmp_path)
        with patch.object(BackendAdapter, "call") as mock_call:
            result = agent.start()
        assert result.startswith("[GATED] missing_backend:")
        mock_call.assert_not_called()

    def test_unknown_backend_is_rejected_before_call(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            _TestAgent(backend="not-registered")


# ===========================================================================
# S1: Robustness tests
# ===========================================================================

class TestStuckDetection:
    def test_stuck_count_increments_on_same_result(self):
        agent = _TestAgent(backend="ollama")
        r1 = ToolResult(tool="click", params={}, result="clicked", success=True)
        agent._check_stuck(r1)
        agent._check_stuck(r1)
        agent._check_stuck(r1)
        assert agent.state.stuck_count == 2  # first is baseline, 2nd+3rd increment

    def test_stuck_count_resets_on_different_result(self):
        agent = _TestAgent(backend="ollama")
        r1 = ToolResult(tool="click", params={}, result="clicked A", success=True)
        r2 = ToolResult(tool="click", params={}, result="clicked B", success=True)
        agent._check_stuck(r1)
        agent._check_stuck(r1)
        assert agent.state.stuck_count == 1
        agent._check_stuck(r2)
        assert agent.state.stuck_count == 0

    def test_stuck_count_increments_on_failure(self):
        agent = _TestAgent(backend="ollama")
        r1 = ToolResult(tool="click", params={}, result="error", success=False)
        agent._check_stuck(r1)
        agent._check_stuck(r1)
        assert agent.state.stuck_count >= 1

    def test_stuck_stops_loop(self):
        agent = _TestAgent(backend="ollama")
        agent.state.stuck_threshold = 2
        agent.state.stuck_count = 2
        with patch.object(agent.backend_adapter, "call", return_value="ready"):
            agent.start()
        agent._agent_loop()
        assert agent.state.stop_reason == "stuck"
        assert any("stuck" in e.lower() for e in agent.state.errors)


class TestLLMErrorHandling:
    def test_error_response_retries(self):
        agent = _TestAgent(backend="ollama")
        responses = [
            "initial",
            "ERROR: Connection refused",
            '```json\n{"tool": "done", "params": {}}\n```',
        ]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call", side_effect=mock_call), \
             patch("base_agent.time.sleep"):
            report = agent.run()
        assert report["stop_reason"] == "done"

    def test_persistent_error_stops(self):
        agent = _TestAgent(backend="ollama")
        def always_error(tools=None):
            agent.backend_adapter.add_assistant_message("ERROR: down")
            return "ERROR: down"
        with patch.object(agent.backend_adapter, "call", side_effect=always_error), \
             patch("base_agent.time.sleep"):
            report = agent.run()
        assert report["stop_reason"] == "error"


class TestGracefulShutdown:
    def test_timeout_sets_stop_reason(self):
        agent = _TestAgent(backend="ollama")
        agent.state.timeout_seconds = 0.01
        with patch.object(agent.backend_adapter, "call", return_value="ready"):
            agent.start()
        agent._start_time -= agent.state.timeout_seconds + 1
        agent._agent_loop()
        assert agent.state.stop_reason == "timeout"

    def test_max_steps_sets_stop_reason(self):
        agent = _TestAgent(backend="ollama")
        agent.state.max_steps = 0
        with patch.object(agent.backend_adapter, "call", return_value="ready"):
            agent.start()
        agent._agent_loop()
        assert agent.state.stop_reason == "max_steps"

    def test_done_sets_stop_reason(self):
        agent = _TestAgent(backend="ollama")
        responses = [
            "initial",
            '```json\n{"tool": "done", "params": {}}\n```',
        ]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call", side_effect=mock_call):
            report = agent.run()
        assert report["stop_reason"] == "done"

    def test_elapsed_seconds_in_report(self):
        agent = _TestAgent(backend="ollama")
        responses = [
            "initial",
            '```json\n{"tool": "done", "params": {}}\n```',
        ]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call", side_effect=mock_call):
            report = agent.run()
        assert "elapsed_seconds" in report
        assert report["elapsed_seconds"] >= 0

    def test_report_includes_stop_reason_and_stuck(self):
        s = AgentStateBase(session_id="x", agent_type="test",
                           stop_reason="stuck", stuck_count=5)
        report = ReportBuilder.build(s)
        assert report["stop_reason"] == "stuck"
        assert report["stuck_count"] == 5


class TestStatePersistence:
    def test_save_and_load(self, tmp_path):
        s = AgentStateBase(session_id="abc", agent_type="test",
                           current_step=10, total_steps=10)
        s.log_finding("Found bug", severity="bug")
        s.log_error("Something broke")

        path = str(tmp_path / "state.json")
        s.save(path)

        loaded = AgentStateBase.load(path)
        assert loaded.session_id == "abc"
        assert loaded.current_step == 10
        assert len(loaded.errors) == 1

    def test_save_creates_dirs(self, tmp_path):
        s = AgentStateBase(session_id="x")
        path = str(tmp_path / "deep" / "dir" / "state.json")
        s.save(path)
        assert Path(path).exists()

    def test_auto_save_in_loop(self, tmp_path):
        agent = _TestAgent(backend="ollama")
        save_path = str(tmp_path / "state.json")
        # Make the loop execute 10+ steps to trigger auto-save at step 10
        responses = []
        for i in range(12):
            responses.append(f'```json\n{{"tool": "echo", "params": {{"text": "step{i}"}}}}\n```')
        responses.append('```json\n{"tool": "done", "params": {}}\n```')
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call", side_effect=mock_call):
            agent.run(state_save_path=save_path)
        # state saved at finish (and possibly at step 10)
        assert Path(save_path).exists()
        data = json.loads(Path(save_path).read_text())
        assert data["agent_type"] == "test"


# ===========================================================================
# GPT-R2: Verbatim verification
# ===========================================================================

class TestVerbatimVerification:
    def test_hash_content_deterministic(self):
        h1 = BaseAgent._hash_content("hello world")
        h2 = BaseAgent._hash_content("hello world")
        assert h1 == h2

    def test_hash_content_different(self):
        h1 = BaseAgent._hash_content("hello")
        h2 = BaseAgent._hash_content("world")
        assert h1 != h2

    def test_verify_verbatim_match(self):
        original = "This is the full design document content."
        received = "This is the full design document content."
        assert BaseAgent._verify_verbatim(original, received) is True

    def test_verify_verbatim_mismatch(self):
        original = "This is the full design document content."
        received = "This is a summary of the design document."
        assert BaseAgent._verify_verbatim(original, received) is False

    def test_tag_verbatim(self):
        tagged = BaseAgent._tag_verbatim("important content")
        assert tagged["content"] == "important content"
        assert "_verbatim_hash" in tagged
        assert len(tagged["_verbatim_hash"]) == 16

    def test_tag_and_verify_roundtrip(self):
        content = "Architecture rules: no external deps in hooks."
        tagged = BaseAgent._tag_verbatim(content)
        received_hash = BaseAgent._hash_content(tagged["content"])
        assert received_hash == tagged["_verbatim_hash"]


# ===========================================================================
# GPT-R3: Failure-mode tests
# ===========================================================================

class TestFailureModes:
    def test_tool_error_in_loop(self):
        """Agent handles tool execution error without crashing."""
        agent = _TestAgent(backend="ollama")
        # Register a tool that always fails
        agent.tools.register("fail_tool", lambda: (_ for _ in ()).throw(
            RuntimeError("tool crashed")))

        responses = [
            "initial",
            '```json\n{"tool": "fail_tool", "params": {}}\n```',
            '```json\n{"tool": "done", "params": {}}\n```',
        ]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call",
                          side_effect=mock_call):
            report = agent.run()
        # Should complete despite tool error
        assert report["phase"] == "done"
        # Error should be logged in actions
        failed = [a for a in agent.state.actions_log
                   if hasattr(a, 'success') and not a.success]
        assert len(failed) >= 1

    def test_empty_response_handling(self):
        """Agent handles empty LLM response."""
        agent = _TestAgent(backend="ollama")
        responses = ["initial", ""]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call",
                          side_effect=mock_call):
            report = agent.run()
        # Should stop gracefully
        assert report["phase"] == "done"

    def test_malformed_json_in_tool_call(self):
        """Agent handles malformed JSON in tool call fence."""
        agent = _TestAgent(backend="ollama")
        responses = [
            "initial",
            '```json\n{broken json here}\n```',
            '```json\n{"tool": "done", "params": {}}\n```',
        ]
        idx = [0]
        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp
        with patch.object(agent.backend_adapter, "call",
                          side_effect=mock_call):
            report = agent.run()
        assert report["phase"] == "done"

    def test_backend_timeout_recovery(self):
        """Agent handles backend call timeout."""
        agent = _TestAgent(backend="ollama")
        agent.state.timeout_seconds = 10
        call_count = [0]
        def mock_call(tools=None):
            call_count[0] += 1
            if call_count[0] == 1:
                agent.backend_adapter.add_assistant_message("ready")
                return "ready"
            # Simulate timeout via ERROR response
            agent.backend_adapter.add_assistant_message(
                "ERROR: Connection timed out")
            return "ERROR: Connection timed out"
        with patch.object(agent.backend_adapter, "call",
                          side_effect=mock_call), \
             patch("base_agent.time.sleep"):
            report = agent.run()
        # Should stop with error, not crash
        assert report["stop_reason"] in ("error", "done", "no_tool_calls")
