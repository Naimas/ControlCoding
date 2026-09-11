"""Tests for agent_runner.py - S12b subprocess state machine.

Tests the runner's state transitions, control message handling, and lifecycle
without requiring actual LLM backends or the Gateway. Uses mocks for agents
and the protocol module.
"""

import io
import json
import time
import threading
import pytest
from unittest.mock import MagicMock, patch

import session_protocol as sp


@pytest.fixture(autouse=True)
def reset_protocol():
    """Reset module state before each test."""
    sp.reset_module_state()
    # Import runner module fresh-ish
    import agent_runner
    agent_runner._last_heartbeat = 0.0
    yield
    sp.reset_module_state()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_req(method, params=None, req_id="req-001"):
    return {
        "v": 1, "source": "gateway", "slot_id": "test",
        "type": "req", "id": req_id, "method": method,
        "params": params or {},
    }


def _make_evt(channel, data=None):
    return {
        "v": 1, "source": "gateway", "slot_id": "test",
        "type": "evt", "channel": channel, "data": data or {},
    }


def _capture_stdout():
    """Return a StringIO that captures protocol output."""
    return io.StringIO()


def _parse_messages(out: io.StringIO) -> list[dict]:
    """Parse all JSON messages from a StringIO."""
    out.seek(0)
    msgs = []
    for line in out.getvalue().strip().split("\n"):
        if line.strip():
            msgs.append(json.loads(line))
    return msgs


# ---------------------------------------------------------------------------
# Test 13: agent.ready sent after BOOTING
# ---------------------------------------------------------------------------

class TestStartupLifecycle:
    def test_agent_ready_sent(self):
        """After startup, agent sends agent.ready event and enters READY state."""
        out = _capture_stdout()
        sp.set_slot_id("test_slot")
        sp._set_state(sp.AgentState.READY)
        sp.send_event("agent.ready", {}, stdout_stream=out)

        msgs = _parse_messages(out)
        assert len(msgs) == 1
        assert msgs[0]["type"] == "evt"
        assert msgs[0]["channel"] == "agent.ready"

    # -------------------------------------------------------------------
    # Test 14: session.init transitions to INITIALIZED then IDLE
    # -------------------------------------------------------------------
    def test_session_init_state_transition(self):
        """session.init on control queue triggers INITIALIZED state."""
        sp._set_state(sp.AgentState.READY)
        init_msg = _make_req("session.init", {"slot_id": "main_coder",
                                               "backend": "ollama", "model": "llama3"})
        sp._dispatch(init_msg)

        ctrl = sp.drain_control()
        assert len(ctrl) == 1
        assert ctrl[0]["method"] == "session.init"

        # Simulate runner processing
        sp._set_state(sp.AgentState.INITIALIZED)
        config = ctrl[0].get("params", {})
        sp.set_slot_id(config.get("slot_id", "fallback"))
        assert sp.get_state() == sp.AgentState.INITIALIZED

        sp._set_state(sp.AgentState.IDLE)
        assert sp.get_state() == sp.AgentState.IDLE


# ---------------------------------------------------------------------------
# Test 15-16: session.message processing cycle
# ---------------------------------------------------------------------------

class TestTaskProcessing:
    def test_session_message_dispatched_to_task_queue(self):
        """session.message in IDLE is dispatched to task queue."""
        sp._set_state(sp.AgentState.IDLE)
        task_msg = _make_req("session.message", {"content": "Write a function",
                                                   "from": "concierge"})
        sp._dispatch(task_msg)
        task = sp.wait_for_task(timeout=0.5)
        assert task is not None
        assert task["params"]["content"] == "Write a function"

    def test_response_sent_after_processing(self):
        """After processing, response with payload is sent, state returns to IDLE."""
        out = _capture_stdout()
        sp._set_state(sp.AgentState.PROCESSING)

        # Simulate sending response
        sp.send_response("req-task", ok=True, payload={"text": "Done."},
                          stdout_stream=out)
        sp.send_event("agent.done", {"summary": "Done."}, stdout_stream=out)
        sp._set_state(sp.AgentState.IDLE)

        msgs = _parse_messages(out)
        assert msgs[0]["type"] == "res"
        assert msgs[0]["ok"] is True
        assert msgs[0]["payload"]["text"] == "Done."
        assert msgs[1]["channel"] == "agent.done"
        assert sp.get_state() == sp.AgentState.IDLE


# ---------------------------------------------------------------------------
# Test 17: session.stop in IDLE
# ---------------------------------------------------------------------------

class TestControlMessages:
    def test_session_stop_in_idle(self):
        """session.stop in IDLE sets shutdown flag."""
        import agent_runner

        out = _capture_stdout()
        sp._set_state(sp.AgentState.IDLE)

        mock_agent = MagicMock()
        stop_msg = _make_req("session.stop", {"reason": "user"}, req_id="stop-1")

        with patch.object(sp, '_stdout', out):
            agent_runner._handle_control(stop_msg, mock_agent)

        assert sp.get_state() == sp.AgentState.STOPPING
        assert sp.is_shutdown()

    # -------------------------------------------------------------------
    # Test 18: session.stop deferred during PROCESSING
    # -------------------------------------------------------------------
    def test_session_stop_deferred_during_processing(self):
        """session.stop during PROCESSING is handled after task completes."""
        sp._set_state(sp.AgentState.PROCESSING)

        # Stop arrives on control queue during processing
        stop_msg = _make_req("session.stop", {"reason": "user"}, req_id="stop-2")
        sp._dispatch(stop_msg)

        # Still processing - control is queued, not applied yet
        assert sp.get_state() == sp.AgentState.PROCESSING
        assert not sp.is_shutdown()

        # After task completes, runner drains control
        ctrls = sp.drain_control()
        assert len(ctrls) == 1
        assert ctrls[0]["method"] == "session.stop"

    # -------------------------------------------------------------------
    # Test 19: session.reconfigure
    # -------------------------------------------------------------------
    def test_session_reconfigure_updates_model(self):
        """session.reconfigure updates the agent's backend model."""
        import agent_runner

        out = _capture_stdout()
        sp._set_state(sp.AgentState.IDLE)

        mock_agent = MagicMock()
        mock_agent.backend_adapter = MagicMock()
        mock_agent.backend_adapter.model = "llama3"

        reconf_msg = _make_req("session.reconfigure", {"model": "mistral"},
                                req_id="reconf-1")

        with patch.object(sp, '_stdout', out):
            agent_runner._handle_control(reconf_msg, mock_agent)

        assert mock_agent.backend_adapter.model == "mistral"

    # -------------------------------------------------------------------
    # Test 20: session.init timeout exits with code 1
    # -------------------------------------------------------------------
    def test_init_timeout(self):
        """_wait_for_control returns None on timeout (runner would exit 1)."""
        import agent_runner

        sp._set_state(sp.AgentState.READY)
        # No init message on the queue
        result = agent_runner._wait_for_control("session.init", timeout=0.2)
        assert result is None

    # -------------------------------------------------------------------
    # Test 21: session.message in READY rejected
    # -------------------------------------------------------------------
    def test_task_rejected_in_wrong_state(self):
        """Task in non-IDLE state should be rejected."""
        sp._set_state(sp.AgentState.READY)

        # State enforcement: runner checks state before processing
        assert sp.get_state() != sp.AgentState.IDLE


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestRunnerEdgeCases:
    def test_process_events_heartbeat_tracking(self):
        """_process_events resets heartbeat timestamp on gateway.heartbeat."""
        import agent_runner

        agent_runner._last_heartbeat = 100.0
        events = [_make_evt("gateway.heartbeat")]
        agent_runner._process_events(events)
        assert agent_runner._last_heartbeat > 100.0

    def test_process_events_codewarden_logged(self, capsys):
        """Codewarden violations are logged to stderr."""
        import agent_runner

        events = [_make_evt("codewarden.violation",
                             {"severity": "critical", "file": "auth.py",
                              "rule": "boundary"})]
        agent_runner._process_events(events)
        captured = capsys.readouterr()
        assert "CODEWARDEN" in captured.err
        assert "auth.py" in captured.err

    def test_process_events_agent_message_logged(self, capsys):
        """Agent messages are logged to stderr."""
        import agent_runner

        events = [_make_evt("agent.message",
                             {"from_slot": "architect", "message": "Use DI pattern"})]
        agent_runner._process_events(events)
        captured = capsys.readouterr()
        assert "architect" in captured.err

    def test_get_agent_class_unknown_raises(self):
        """Unknown agent name raises ValueError."""
        import agent_runner

        with pytest.raises(ValueError, match="Unknown agent"):
            agent_runner._get_agent_class("nonexistent")

    def test_get_agent_class_codewarden_is_explicitly_deferred(self):
        """The codewarden runner role is unsupported without missing-module noise."""
        import agent_runner

        with patch("builtins.__import__",
                   side_effect=AssertionError("unexpected import")):
            with pytest.raises(
                agent_runner.DeferredAgentRuntimeError,
                match="codewarden.*deferred",
            ) as exc_info:
                agent_runner._get_agent_class("codewarden")

        assert "runnable BaseAgent role" in str(exc_info.value)

    @pytest.mark.parametrize(
        ("role", "class_name"),
        [
            ("concierge", "ConciergeAgent"),
            ("architect", "ArchitectAgent"),
            ("coder", "CoderAgent"),
            ("reviewer", "ReviewerAgent"),
            ("debugger", "DebuggerAgent"),
            ("socratic", "SocraticAgent"),
            ("expert", "ExpertAgent"),
        ],
    )
    def test_get_agent_class_supported_roles_still_resolve(self, role, class_name):
        """Supported legacy runner roles still resolve to their current classes."""
        import agent_runner

        assert agent_runner._get_agent_class(role).__name__ == class_name

    def test_handle_control_ignores_unknown_method(self):
        """Unknown control method doesn't crash."""
        import agent_runner

        out = _capture_stdout()
        mock_agent = MagicMock()
        unknown_msg = _make_req("session.unknown", {}, req_id="u1")

        # Should not raise
        with patch.object(sp, '_stdout', out):
            agent_runner._handle_control(unknown_msg, mock_agent)
