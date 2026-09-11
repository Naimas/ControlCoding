#!/usr/bin/env python3
"""mcp_bridge.py - Inter-session communication bridge via filesystem.

Enables multiple Claude Code sessions (or any AI chat) to communicate
with each other through a shared directory. Each session runs this MCP
server with a different AGENT_ID, and they exchange messages via JSON
files in a shared bridge directory.

This creates a simple message-passing protocol:
- send(): write a message for another agent
- receive(): read messages addressed to this agent
- wait_reply(): block until a specific reply arrives

Example setup - two VS Code windows on the same project:

Session 1 (coder) - .claude/settings.json:
    {
      "mcpServers": {
        "bridge": {
          "command": "python",
          "args": ["tools/mcp_bridge.py"],
          "env": {
            "BRIDGE_AGENT_ID": "coder",
            "BRIDGE_DIR": ".bridge"
          }
        }
      }
    }

Session 2 (consultant) - opened on a separate folder with its own config:
    {
      "mcpServers": {
        "bridge": {
          "command": "python",
          "args": ["/absolute/path/to/mcp_bridge.py"],
          "env": {
            "BRIDGE_AGENT_ID": "consultant",
            "BRIDGE_DIR": "/absolute/path/to/.bridge"
          }
        }
      }
    }

The BRIDGE_DIR must resolve to the same physical directory for both sessions.

Message format (JSON files in BRIDGE_DIR/messages/):
    {
      "id": "001",
      "from": "coder",
      "to": "debugger",
      "timestamp": "2026-03-02T10:30:00Z",
      "reply_to": null,
      "status": "pending",
      "content": "Floor quad is invisible. Vertices at y=0..."
    }

Requirements:
    pip install fastmcp
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

try:
    from watchfiles import watch
    HAS_WATCHFILES = True
except ImportError:
    HAS_WATCHFILES = False

# --- Configuration ---

AGENT_ID = os.environ.get("BRIDGE_AGENT_ID", "agent")
BRIDGE_DIR = Path(os.environ.get("BRIDGE_DIR", ".bridge")).resolve()
MESSAGES_DIR = BRIDGE_DIR / "messages"
POLL_INTERVAL = float(os.environ.get("BRIDGE_POLL_INTERVAL", "2.0"))
DEFAULT_TIMEOUT = float(os.environ.get("BRIDGE_TIMEOUT", "120.0"))

# --- MCP Server ---

mcp = FastMCP(
    "bridge",
    instructions=(
        f"Inter-session communication bridge. This agent is '{AGENT_ID}'. "
        f"Use send() to message other agents, receive() to check for "
        f"incoming messages, wait_reply() to block until a reply arrives. "
        f"Connected agents share the bridge directory at {BRIDGE_DIR}."
    ),
)


def _ensure_dirs() -> None:
    """Create bridge directories if they don't exist."""
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)


def _next_id() -> str:
    """Generate the next message ID based on existing files."""
    _ensure_dirs()
    existing = list(MESSAGES_DIR.glob("*.json"))
    if not existing:
        return "001"
    ids = []
    for f in existing:
        try:
            ids.append(int(f.stem.split("_")[0]))
        except (ValueError, IndexError):
            pass
    next_num = max(ids) + 1 if ids else 1
    return f"{next_num:03d}"


def _register_agent() -> None:
    """Register this agent as active in the bridge."""
    _ensure_dirs()
    agents_file = BRIDGE_DIR / "agents.json"
    agents = {}
    if agents_file.exists():
        try:
            agents = json.loads(agents_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            agents = {}

    agents[AGENT_ID] = {
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "status": "online",
    }
    agents_file.write_text(
        json.dumps(agents, indent=2), encoding="utf-8"
    )


def _read_message(path: Path) -> dict:
    """Read and parse a message file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# Register on import (server startup)
_register_agent()


@mcp.tool
def send(to: str, content: str, reply_to: str = "") -> str:
    """Send a message to another agent via the bridge.

    The message is written as a JSON file in the shared bridge directory.
    The receiving agent will see it when they call receive() or
    wait_reply().

    Args:
        to: The agent ID of the recipient (e.g., "debugger", "consultant",
            "scientist"). Must match the BRIDGE_AGENT_ID of the target session.
        content: The message content. Be specific and structured.
                 Include all data the other agent needs to respond.
        reply_to: Optional message ID this is a reply to. This links
                  the conversation thread.

    Returns:
        The message ID for tracking. Use this with wait_reply() to
        get the response.
    """
    _ensure_dirs()
    msg_id = _next_id()
    filename = f"{msg_id}_{AGENT_ID}_to_{to}.json"

    message = {
        "id": msg_id,
        "from": AGENT_ID,
        "to": to,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reply_to": reply_to or None,
        "status": "pending",
        "content": content,
    }

    msg_path = MESSAGES_DIR / filename
    msg_path.write_text(
        json.dumps(message, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Update presence
    _register_agent()

    return f"Message {msg_id} sent to '{to}'. Use wait_reply('{msg_id}') to wait for their response."


@mcp.tool
def receive(mark_read: bool = True) -> str:
    """Check for new messages addressed to this agent.

    Reads all pending messages in the bridge directory that are
    addressed to this agent (AGENT_ID).

    Args:
        mark_read: If true, mark messages as "read" after returning them.
                   Set to false to peek without consuming.

    Returns:
        All pending messages as formatted text, or "No new messages."
    """
    _ensure_dirs()
    _register_agent()

    pending = []
    for msg_file in sorted(MESSAGES_DIR.glob("*.json")):
        msg = _read_message(msg_file)
        if msg.get("to") == AGENT_ID and msg.get("status") == "pending":
            pending.append((msg_file, msg))

    if not pending:
        return "No new messages."

    results = []
    for msg_file, msg in pending:
        reply_info = f" (reply to #{msg['reply_to']})" if msg.get("reply_to") else ""
        results.append(
            f"--- Message #{msg['id']} from '{msg['from']}'{reply_info} ---\n"
            f"{msg['content']}\n"
            f"--- End #{msg['id']} ---"
        )

        if mark_read:
            msg["status"] = "read"
            msg["read_at"] = datetime.now(timezone.utc).isoformat()
            msg_file.write_text(
                json.dumps(msg, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    return f"{len(results)} message(s):\n\n" + "\n\n".join(results)


@mcp.tool
def wait_reply(message_id: str, timeout: float = 0) -> str:
    """Wait for a reply to a specific message.

    Polls the bridge directory until a message with reply_to matching
    the given message_id appears, or until timeout.

    Args:
        message_id: The ID of the message you sent (returned by send()).
        timeout: Maximum seconds to wait. Default uses BRIDGE_TIMEOUT
                 env var (120s). Set to 0 for default.

    Returns:
        The reply content, or a timeout message.
    """
    _ensure_dirs()
    use_timeout = timeout if timeout > 0 else DEFAULT_TIMEOUT

    def _check_reply():
        """Scan for a matching reply. Returns response string or None."""
        for msg_file in sorted(MESSAGES_DIR.glob("*.json")):
            msg = _read_message(msg_file)
            if (
                msg.get("reply_to") == message_id
                and msg.get("to") == AGENT_ID
                and msg.get("status") == "pending"
            ):
                msg["status"] = "read"
                msg["read_at"] = datetime.now(timezone.utc).isoformat()
                msg_file.write_text(
                    json.dumps(msg, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                return (
                    f"Reply from '{msg['from']}' (message #{msg['id']}):\n\n"
                    f"{msg['content']}"
                )
        return None

    # Check immediately first
    result = _check_reply()
    if result:
        return result

    # Use watchfiles if available (instant), fall back to polling
    if HAS_WATCHFILES:
        deadline = time.time() + use_timeout
        for _changes in watch(
            MESSAGES_DIR,
            stop_event=None,
            yield_on_timeout=True,
            rust_timeout=int(min(5000, (deadline - time.time()) * 1000)),
        ):
            result = _check_reply()
            if result:
                return result
            if time.time() >= deadline:
                break
    else:
        start = time.time()
        while time.time() - start < use_timeout:
            time.sleep(POLL_INTERVAL)
            result = _check_reply()
            if result:
                return result

    return (
        f"TIMEOUT: No reply to message #{message_id} after "
        f"{use_timeout:.0f}s. The other agent may not be running "
        f"or may not have seen the message yet. Try receive() to "
        f"check for any messages."
    )


@mcp.tool
def list_agents() -> str:
    """Show all agents registered in the bridge.

    Returns:
        List of agent IDs with their last seen time and status.
    """
    agents_file = BRIDGE_DIR / "agents.json"
    if not agents_file.exists():
        return f"Only this agent ('{AGENT_ID}') is registered."

    try:
        agents = json.loads(agents_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return f"Only this agent ('{AGENT_ID}') is registered."

    lines = [f"Registered agents ({len(agents)}):"]
    for aid, info in agents.items():
        marker = " <-- you" if aid == AGENT_ID else ""
        lines.append(f"  - {aid}: last seen {info.get('last_seen', '?')}{marker}")

    return "\n".join(lines)


@mcp.tool
def history(limit: int = 20) -> str:
    """Show recent message history in the bridge.

    Args:
        limit: Maximum number of messages to show (default 20).

    Returns:
        Chronological list of recent messages.
    """
    _ensure_dirs()
    files = sorted(MESSAGES_DIR.glob("*.json"))[-limit:]

    if not files:
        return "No messages in bridge history."

    lines = [f"Bridge history (last {len(files)} messages):"]
    for msg_file in files:
        msg = _read_message(msg_file)
        if not msg:
            continue
        reply_info = f" re:#{msg['reply_to']}" if msg.get("reply_to") else ""
        status = msg.get("status", "?")
        lines.append(
            f"  #{msg.get('id','?')} [{status}] "
            f"{msg.get('from','?')} -> {msg.get('to','?')}"
            f"{reply_info}: {msg.get('content','')[:80]}..."
        )

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
