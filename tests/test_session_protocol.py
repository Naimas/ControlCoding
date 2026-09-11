"""Tests for session_protocol.py - S12a stdio transport and dispatcher.

16 tests covering: serialization, dispatch routing, request-response correlation,
drain semantics, robustness (malformed JSON, unknown ids, EOF), and envelope format.
"""

import io
import json
import threading
import queue
import pytest

import session_protocol as sp


@pytest.fixture(autouse=True)
def reset_protocol():
    """Reset module state before each test."""
    sp.reset_module_state()
    yield
    sp.reset_module_state()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_envelope(msg_type, **kwargs):
    """Build a protocol envelope."""
    base = {"v": 1, "source": "gateway", "slot_id": "test_slot", "type": msg_type}
    base.update(kwargs)
    return base


class _WriteNotifyingStringIO(io.StringIO):
    """StringIO that exposes when a complete protocol write has occurred."""

    def __init__(self):
        super().__init__()
        self.write_complete = threading.Event()

    def write(self, value):
        written = super().write(value)
        self.write_complete.set()
        return written


def _join_reader(timeout=1.0):
    """Wait for the finite test stream reader and assert bounded shutdown."""
    reader = sp._reader_thread
    assert reader is not None
    reader.join(timeout=timeout)
    assert not reader.is_alive()


def _inject_messages(messages: list[dict]):
    """Create a StringIO stdin with JSON-lines messages and start the reader."""
    lines = "\n".join(json.dumps(m) for m in messages) + "\n"
    fake_stdin = io.StringIO(lines)
    sp.start_reader(stdin_stream=fake_stdin)
    _join_reader()


# ---------------------------------------------------------------------------
# 1. write_message: valid single-line JSON with envelope fields
# ---------------------------------------------------------------------------

class TestWriteMessage:
    def test_write_message_produces_single_line_json(self):
        out = io.StringIO()
        sp.set_slot_id("my_slot")
        sp.send_event("agent.ready", {"foo": "bar"}, stdout_stream=out)
        line = out.getvalue().strip()
        msg = json.loads(line)
        assert msg["v"] == sp.PROTOCOL_VERSION
        assert msg["source"] == "agent"
        assert msg["slot_id"] == "my_slot"
        assert msg["type"] == "evt"
        assert msg["channel"] == "agent.ready"
        assert msg["data"] == {"foo": "bar"}

    def test_write_message_no_multiline(self):
        out = io.StringIO()
        sp.write_message({"data": "line1\nline2"}, stdout_stream=out)
        lines = out.getvalue().strip().split("\n")
        assert len(lines) == 1  # single line, \n inside is escaped


# ---------------------------------------------------------------------------
# 2-3. send_request + response correlation + timeout
# ---------------------------------------------------------------------------

class TestSendRequest:
    def test_send_request_correlates_response(self):
        """send_request sends a request and returns the matched response."""
        out = _WriteNotifyingStringIO()
        sp.set_slot_id("test")

        # Use dispatch directly to simulate a response arriving
        result_holder = []
        worker_errors = []

        def do_request():
            try:
                result_holder.append(
                    sp.send_request(
                        "llm.call", {"prompt": "hi"}, timeout=2, stdout_stream=out
                    )
                )
            except BaseException as exc:
                worker_errors.append(exc)

        # Start the request in a thread
        t = threading.Thread(target=do_request)
        t.start()
        assert out.write_complete.wait(timeout=1)

        # Extract the request id from what was written to stdout
        written = out.getvalue().strip()
        req_msg = json.loads(written)
        req_id = req_msg["id"]

        # Simulate a response arriving via dispatch
        sp._dispatch(_make_envelope("res", id=req_id, ok=True, payload={"text": "hello"}))
        t.join(timeout=2)
        assert not t.is_alive()
        assert worker_errors == []

        assert len(result_holder) == 1
        assert result_holder[0] is not None
        assert result_holder[0]["ok"] is True
        assert result_holder[0]["payload"]["text"] == "hello"

    def test_send_request_timeout(self):
        """send_request returns None on timeout."""
        out = io.StringIO()
        result = sp.send_request("llm.call", timeout=0.1, stdout_stream=out)
        assert result is None


# ---------------------------------------------------------------------------
# 4-7. Dispatcher routing
# ---------------------------------------------------------------------------

class TestDispatchRouting:
    def test_response_routed_to_pending_queue(self):
        """res messages go to _pending_responses by id."""
        q = queue.Queue()
        with sp._pending_lock:
            sp._pending_responses["abc-123"] = q

        sp._dispatch(_make_envelope("res", id="abc-123", ok=True, payload={}))
        assert not q.empty()
        msg = q.get_nowait()
        assert msg["id"] == "abc-123"

    def test_session_message_routed_to_task_queue(self):
        """req with session.message goes to _task_queue."""
        sp._dispatch(_make_envelope("req", id="r1", method="session.message",
                                     params={"content": "do stuff"}))
        assert not sp._task_queue.empty()
        msg = sp._task_queue.get_nowait()
        assert msg["method"] == "session.message"

    def test_session_stop_routed_to_control_queue(self):
        """req with session.stop goes to _control_queue."""
        sp._dispatch(_make_envelope("req", id="r2", method="session.stop",
                                     params={"reason": "user"}))
        assert not sp._control_queue.empty()
        msg = sp._control_queue.get_nowait()
        assert msg["method"] == "session.stop"

    def test_session_init_routed_to_control_queue(self):
        """req with session.init goes to _control_queue."""
        sp._dispatch(_make_envelope("req", id="r3", method="session.init",
                                     params={"slot_id": "s1"}))
        msg = sp._control_queue.get_nowait()
        assert msg["method"] == "session.init"

    def test_event_routed_to_event_buffer(self):
        """evt messages go to _event_buffer."""
        sp._dispatch(_make_envelope("evt", channel="gateway.heartbeat", data={}))
        assert not sp._event_buffer.empty()
        msg = sp._event_buffer.get_nowait()
        assert msg["channel"] == "gateway.heartbeat"


# ---------------------------------------------------------------------------
# 8. drain_events
# ---------------------------------------------------------------------------

class TestDrainEvents:
    def test_drain_events_returns_all_and_empties(self):
        sp._event_buffer.put({"channel": "a"})
        sp._event_buffer.put({"channel": "b"})
        sp._event_buffer.put({"channel": "c"})

        events = sp.drain_events()
        assert len(events) == 3
        assert sp._event_buffer.empty()

    def test_drain_events_empty_returns_empty_list(self):
        assert sp.drain_events() == []


# ---------------------------------------------------------------------------
# 9. Unknown response id silently dropped
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_unknown_response_id_dropped(self):
        """Response with no matching pending request is silently ignored."""
        sp._dispatch(_make_envelope("res", id="nonexistent", ok=True, payload={}))
        # No exception, no queue pollution
        assert sp._task_queue.empty()
        assert sp._event_buffer.empty()
        assert sp._control_queue.empty()

    # -------------------------------------------------------------------
    # 10. Malformed JSON skipped
    # -------------------------------------------------------------------
    def test_malformed_json_skipped(self):
        """Malformed JSON on stdin doesn't crash, is silently skipped."""
        fake_stdin = io.StringIO("not valid json\n{\"type\":\"evt\",\"channel\":\"test\",\"data\":{}}\n")
        sp.start_reader(stdin_stream=fake_stdin)
        _join_reader()

        # Only the valid message should arrive
        events = sp.drain_events()
        assert len(events) == 1
        assert events[0]["channel"] == "test"

    # -------------------------------------------------------------------
    # 11. EOF sets shutdown flag
    # -------------------------------------------------------------------
    def test_eof_sets_shutdown(self):
        """EOF on stdin sets the shutdown flag (Gateway death detection)."""
        fake_stdin = io.StringIO("")  # immediate EOF
        sp.start_reader(stdin_stream=fake_stdin)
        _join_reader()
        assert sp.is_shutdown()

    # -------------------------------------------------------------------
    # 12. send_event envelope correctness
    # -------------------------------------------------------------------
    def test_send_event_envelope(self):
        """send_event includes v, source, slot_id, type, channel, data."""
        out = io.StringIO()
        sp.set_slot_id("slot_42")
        sp.send_event("agent.state_update", {"phase": "CODING"}, stdout_stream=out)
        msg = json.loads(out.getvalue().strip())
        assert msg["v"] == 1
        assert msg["source"] == "agent"
        assert msg["slot_id"] == "slot_42"
        assert msg["type"] == "evt"
        assert msg["channel"] == "agent.state_update"
        assert msg["data"]["phase"] == "CODING"


# ---------------------------------------------------------------------------
# 13. send_response envelope correctness
# ---------------------------------------------------------------------------

class TestSendResponse:
    def test_send_response_ok(self):
        out = io.StringIO()
        sp.set_slot_id("s1")
        sp.send_response("req-001", ok=True, payload={"result": "done"}, stdout_stream=out)
        msg = json.loads(out.getvalue().strip())
        assert msg["v"] == 1
        assert msg["source"] == "agent"
        assert msg["type"] == "res"
        assert msg["id"] == "req-001"
        assert msg["ok"] is True
        assert msg["payload"]["result"] == "done"

    def test_send_response_error(self):
        out = io.StringIO()
        sp.send_response("req-002", ok=False, error="bad state", stdout_stream=out)
        msg = json.loads(out.getvalue().strip())
        assert msg["ok"] is False
        assert msg["error"] == "bad state"


# ---------------------------------------------------------------------------
# Edge cases (from design doc section 10.3)
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_duplicate_request_id(self):
        """Second request with same pending id does not crash."""
        out = io.StringIO()
        result1 = []
        result2 = []
        worker_errors = []

        def req1():
            try:
                result1.append(sp.send_request("m1", timeout=1, stdout_stream=out))
            except BaseException as exc:
                worker_errors.append(exc)

        def req2():
            try:
                result2.append(sp.send_request("m2", timeout=1, stdout_stream=out))
            except BaseException as exc:
                worker_errors.append(exc)

        t1 = threading.Thread(target=req1)
        t2 = threading.Thread(target=req2)
        t1.start()
        t2.start()

        # Both should timeout without crash
        t1.join(timeout=2)
        t2.join(timeout=2)
        assert not t1.is_alive()
        assert not t2.is_alive()
        assert worker_errors == []
        assert result1 == [None]
        assert result2 == [None]

    def test_large_message(self):
        """Messages >1MB serialize and parse correctly."""
        out = io.StringIO()
        big_data = {"payload": "x" * (1024 * 1024)}  # ~1MB
        sp.write_message(big_data, stdout_stream=out)
        line = out.getvalue().strip()
        parsed = json.loads(line)
        assert len(parsed["payload"]) == 1024 * 1024

    def test_heartbeat_tracking_via_events(self):
        """gateway.heartbeat events are routed to event_buffer."""
        sp._dispatch(_make_envelope("evt", channel="gateway.heartbeat", data={}))
        events = sp.drain_events()
        assert len(events) == 1
        assert events[0]["channel"] == "gateway.heartbeat"

    def test_drain_control_returns_all(self):
        """drain_control returns all buffered control messages."""
        sp._dispatch(_make_envelope("req", id="c1", method="session.stop", params={}))
        sp._dispatch(_make_envelope("req", id="c2", method="session.reconfigure", params={}))
        msgs = sp.drain_control()
        assert len(msgs) == 2
        assert sp._control_queue.empty()
