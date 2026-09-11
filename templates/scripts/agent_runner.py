"""agent_runner.py - internal Gateway subprocess runner for agents.

S12b: spawns a single agent, manages its state machine, communicates with the
Gateway via the session_protocol stdio JSON-lines bridge.

Current status: Gateway runtime agent execution is deferred for the current
public scope. This file remains an internal/lab bridge for tested protocol and
class-resolution paths, not a supported public agent runtime.

Usage:
    python -u agent_runner.py --agent coder --slot main_coder --project-root /path

The -u flag is critical: Python stdout must be unbuffered for real-time protocol.
"""

import argparse
import sys
import time

from session_protocol import (
    start_reader, send_event, send_response,
    wait_for_task, drain_events, drain_control, is_shutdown,
    set_slot_id, get_state, AgentState, _set_state, _shutdown_flag,
)

# Heartbeat tracking (safety net for Gateway deadlock detection)
_last_heartbeat: float = 0.0
_HEARTBEAT_TIMEOUT = 180.0  # 3 missed beats at 60s interval


class DeferredAgentRuntimeError(RuntimeError):
    """Raised when a named runner role is intentionally deferred."""


def main():
    parser = argparse.ArgumentParser(description="Run an agent as a Gateway subprocess")
    parser.add_argument("--agent", required=True,
                        choices=["concierge", "architect", "coder", "reviewer",
                                 "debugger", "socratic", "codewarden", "expert"])
    parser.add_argument("--slot", required=True)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    global _last_heartbeat

    # --- BOOTING ---
    try:
        agent_class = _get_agent_class(args.agent)
    except DeferredAgentRuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(2)
    set_slot_id(args.slot)  # provisional slot_id from CLI arg
    start_reader()

    # --- READY ---
    _set_state(AgentState.READY)
    send_event("agent.ready", {})
    _last_heartbeat = time.monotonic()  # start heartbeat timer

    # Wait for session.init (arrives on control queue)
    init_msg = _wait_for_control("session.init", timeout=30)
    if not init_msg:
        sys.stderr.write("ERROR: No session.init received within 30s\n")
        sys.exit(1)

    # --- INITIALIZED ---
    _set_state(AgentState.INITIALIZED)
    config = init_msg.get("params", {})
    set_slot_id(config.get("slot_id", args.slot))  # definitive slot_id

    # Create agent with ACTUAL backend (not "session")
    backend = str(config.get("backend") or "").strip().lower()
    if not backend:
        send_response(
            init_msg["id"],
            ok=False,
            error="No backend configured explicitly for agent runtime",
        )
        return 1
    model = config.get("model", "")
    agent = agent_class(
        agent_id=args.slot,
        backend=backend,
        model=model,
        project_root=args.project_root,
    )
    send_response(init_msg["id"], ok=True, payload={})

    # Apply system prompt if provided
    if config.get("system_prompt"):
        agent.backend_adapter.set_system_prompt(config["system_prompt"])

    # --- IDLE (main loop) ---
    _set_state(AgentState.IDLE)

    while not is_shutdown():
        # 1. Process control messages (stop, reconfigure)
        for ctrl in drain_control():
            _handle_control(ctrl, agent)
            if is_shutdown():
                break
        if is_shutdown():
            break

        # 2. Process events (heartbeat, violations, agent messages)
        _process_events(drain_events())

        # 3. Check heartbeat timeout (safety net)
        if _last_heartbeat > 0 and (time.monotonic() - _last_heartbeat) > _HEARTBEAT_TIMEOUT:
            sys.stderr.write("WARNING: No heartbeat for 180s, Gateway may be hung. Exiting.\n")
            break

        # 4. Wait for a task
        task = wait_for_task(timeout=2.0)
        if task is None:
            continue

        # 5. State enforcement: reject task if not IDLE
        if get_state() != AgentState.IDLE:
            send_response(task["id"], ok=False,
                          error=f"Wrong state: {get_state().value}, expected IDLE")
            continue

        # --- PROCESSING ---
        _set_state(AgentState.PROCESSING)
        content = task["params"].get("content", "")
        briefing = task["params"].get("briefing", "")

        # Inject briefing + content as user message
        if briefing:
            full_msg = f"<context_briefing>\n{briefing}\n</context_briefing>\n\n{content}"
        else:
            full_msg = content

        agent.backend_adapter.add_user_message(full_msg)

        # Call LLM directly (agent uses its own backend).
        # This is synchronous. session.stop during this call is DEFERRED
        # until the call returns (v1 limitation, documented in design doc section 4.4).
        response_text = agent.backend_adapter.call()

        # Send result back to Gateway
        send_response(task["id"], ok=True, payload={"text": response_text})
        send_event("agent.done", {"summary": response_text[:200]})

        # Process any control messages that arrived during PROCESSING
        # (handles deferred session.stop)
        for ctrl in drain_control():
            _handle_control(ctrl, agent)

        # --- back to IDLE ---
        if not is_shutdown():
            _set_state(AgentState.IDLE)

    # --- EXITED ---
    _set_state(AgentState.EXITED)
    sys.exit(0)


def _process_events(events: list[dict]):
    """Process incoming events from the Gateway."""
    global _last_heartbeat
    for evt in events:
        channel = evt.get("channel", "")
        if channel == "gateway.heartbeat":
            _last_heartbeat = time.monotonic()
        elif channel == "codewarden.violation":
            data = evt.get("data", {})
            sys.stderr.write(f"CODEWARDEN: {data.get('severity', '?')} - "
                             f"{data.get('file', '?')}: {data.get('rule', '?')}\n")
        elif channel == "agent.message":
            data = evt.get("data", {})
            sys.stderr.write(f"MSG from {data.get('from_slot', '?')}: "
                             f"{data.get('message', '')[:200]}\n")
        # Other events: logged to stderr for debugging
        else:
            sys.stderr.write(f"EVT: {channel}\n")


def _wait_for_control(method: str, timeout: float) -> dict | None:
    """Wait for a specific control message."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ctrl in drain_control():
            if ctrl.get("method") == method:
                return ctrl
        time.sleep(0.1)
    return None


def _handle_control(msg: dict, agent):
    """Handle a control message."""
    method = msg.get("method", "")
    if method == "session.stop":
        _set_state(AgentState.STOPPING)
        send_response(msg["id"], ok=True, payload={})
        _shutdown_flag.set()
    elif method == "session.reconfigure":
        # v1: only model change supported. Backend change requires restart.
        params = msg.get("params", {})
        if params.get("model"):
            agent.backend_adapter.model = params["model"]
            sys.stderr.write(f"RECONFIGURE: model -> {params['model']}\n")
        send_response(msg["id"], ok=True, payload={})


def _get_agent_class(name: str):
    """Import and return the agent class by name. Lazy imports."""
    if name == "concierge":
        from concierge_agent import ConciergeAgent
        return ConciergeAgent
    elif name == "architect":
        from architect_agent import ArchitectAgent
        return ArchitectAgent
    elif name == "coder":
        from coder_agent import CoderAgent
        return CoderAgent
    elif name == "reviewer":
        from reviewer_agent import ReviewerAgent
        return ReviewerAgent
    elif name == "debugger":
        from debugger_agent import DebuggerAgent
        return DebuggerAgent
    elif name == "socratic":
        from socratic_agent import SocraticAgent
        return SocraticAgent
    elif name == "codewarden":
        raise DeferredAgentRuntimeError(
            "Gateway runtime execution for role 'codewarden' is deferred in "
            "the current scope. CodeWarden is currently exposed through hook "
            "and review paths, not as a runnable BaseAgent role."
        )
    elif name == "expert":
        from expert_agent import ExpertAgent
        return ExpertAgent
    else:
        raise ValueError(f"Unknown agent: {name}")


if __name__ == "__main__":
    raise SystemExit(main())
