"""session_protocol.py - Stdio JSON-lines protocol for Gateway communication.

S12a: transport and dispatcher for Mode 3 (Full UI). Agents communicate with the
Gateway via JSON objects on stdin/stdout, one per newline-terminated line.

Protocol version: 1
Dependencies: Python stdlib only
"""

import sys
import json
import uuid
import time
import threading
import queue
from enum import Enum


# --- Protocol version ---
PROTOCOL_VERSION = 1

# --- Agent states ---
class AgentState(Enum):
    BOOTING = "booting"
    READY = "ready"
    INITIALIZED = "initialized"
    IDLE = "idle"
    PROCESSING = "processing"
    STOPPING = "stopping"
    EXITED = "exited"


# --- Module state ---
_slot_id: str = ""
_state: AgentState = AgentState.BOOTING
_state_lock = threading.Lock()

# Dispatcher queues
_pending_responses: dict[str, queue.Queue] = {}  # id -> single-item queue
_pending_lock = threading.Lock()
_task_queue: queue.Queue = queue.Queue()       # session.message requests
_event_buffer: queue.Queue = queue.Queue()     # incoming events
_control_queue: queue.Queue = queue.Queue()    # session.stop, session.reconfigure, session.init
_shutdown_flag = threading.Event()

# Reader thread
_reader_thread: threading.Thread | None = None
_started = False

# Configurable IO streams (for testing)
_stdin = sys.stdin
_stdout = sys.stdout


def _set_state(new_state: AgentState):
    global _state
    with _state_lock:
        _state = new_state


def get_state() -> AgentState:
    with _state_lock:
        return _state


# --- IO ---

def start_reader(stdin_stream=None):
    """Start background thread that reads stdin and dispatches to queues.

    Args:
        stdin_stream: optional override for stdin (used in tests).
    """
    global _reader_thread, _started, _stdin
    if _started:
        return
    _started = True

    if stdin_stream is not None:
        _stdin = stdin_stream

    def _reader_loop():
        try:
            for line in _stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # ignore malformed input
                _dispatch(msg)
        except (EOFError, OSError):
            pass
        # stdin closed or EOF - Gateway died
        _shutdown_flag.set()

    _reader_thread = threading.Thread(target=_reader_loop, daemon=True)
    _reader_thread.start()


def _dispatch(msg: dict):
    """Route incoming message to the correct queue."""
    msg_type = msg.get("type")

    if msg_type == "res":
        # Response to one of our requests
        msg_id = msg.get("id", "")
        with _pending_lock:
            q = _pending_responses.get(msg_id)
        if q:
            q.put(msg)
        # else: response with unknown id - ignore

    elif msg_type == "req":
        method = msg.get("method", "")
        if method == "session.message":
            _task_queue.put(msg)
        elif method in ("session.stop", "session.reconfigure", "session.init"):
            _control_queue.put(msg)
        # else: unknown method - ignore

    elif msg_type == "evt":
        _event_buffer.put(msg)


def write_message(msg: dict, stdout_stream=None):
    """Write a JSON message to stdout (one line, no pretty-printing)."""
    out = stdout_stream or _stdout
    line = json.dumps(msg, ensure_ascii=False)
    out.write(line + "\n")
    out.flush()


def send_event(channel: str, data: dict, stdout_stream=None):
    """Send an event to the Gateway."""
    write_message({
        "v": PROTOCOL_VERSION,
        "source": "agent",
        "slot_id": _slot_id,
        "type": "evt",
        "channel": channel,
        "data": data,
    }, stdout_stream=stdout_stream)


def send_response(request_id: str, ok: bool = True,
                   payload: dict | None = None, error: str = "",
                   stdout_stream=None):
    """Send a response to a Gateway request."""
    msg = {
        "v": PROTOCOL_VERSION,
        "source": "agent",
        "slot_id": _slot_id,
        "type": "res",
        "id": request_id,
        "ok": ok,
    }
    if ok and payload is not None:
        msg["payload"] = payload
    if not ok:
        msg["error"] = error
    write_message(msg, stdout_stream=stdout_stream)


def send_request(method: str, params: dict | None = None,
                 timeout: float = 30, stdout_stream=None) -> dict | None:
    """Send a request to the Gateway and wait for the response.

    RESERVED FOR S12d (LLM proxy). Not used in v1 protocol where
    only the Gateway sends requests. Included here so the infrastructure
    is ready when the proxy is implemented.
    """
    req_id = str(uuid.uuid4())
    response_queue: queue.Queue = queue.Queue()

    with _pending_lock:
        _pending_responses[req_id] = response_queue

    write_message({
        "v": PROTOCOL_VERSION,
        "source": "agent",
        "slot_id": _slot_id,
        "type": "req",
        "id": req_id,
        "method": method,
        "params": params or {},
    }, stdout_stream=stdout_stream)

    try:
        return response_queue.get(timeout=timeout)
    except queue.Empty:
        return None
    finally:
        with _pending_lock:
            _pending_responses.pop(req_id, None)


def wait_for_task(timeout: float = 5.0) -> dict | None:
    """Wait for a session.message request from the Gateway.
    Returns the full request dict, or None on timeout."""
    try:
        return _task_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def drain_events() -> list[dict]:
    """Drain all pending events from the buffer. Non-blocking."""
    events = []
    while True:
        try:
            events.append(_event_buffer.get_nowait())
        except queue.Empty:
            break
    return events


def drain_control() -> list[dict]:
    """Drain all pending control messages. Non-blocking."""
    msgs = []
    while True:
        try:
            msgs.append(_control_queue.get_nowait())
        except queue.Empty:
            break
    return msgs


def is_shutdown() -> bool:
    return _shutdown_flag.is_set()


def set_slot_id(slot_id: str):
    global _slot_id
    _slot_id = slot_id


def reset_module_state():
    """Reset all module-level state. For testing only."""
    global _slot_id, _state, _started, _reader_thread, _stdin, _stdout
    _slot_id = ""
    _state = AgentState.BOOTING
    _started = False
    _reader_thread = None
    _stdin = sys.stdin
    _stdout = sys.stdout
    _shutdown_flag.clear()
    _pending_responses.clear()
    # Drain queues
    for q in (_task_queue, _event_buffer, _control_queue):
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break
