#!/usr/bin/env python3
"""Tests for mcp_bridge.py - Inter-session communication bridge."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock fastmcp and watchfiles before importing mcp_bridge
_mock_mcp_instance = MagicMock()
_mock_mcp_instance.tool = lambda f: f  # passthrough decorator
_mock_mcp_instance.run = MagicMock()

_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP = MagicMock(return_value=_mock_mcp_instance)

sys.modules["fastmcp"] = _mock_fastmcp
sys.modules["watchfiles"] = MagicMock()

# Set env vars before import
_ORIG_AGENT_ID = os.environ.get("BRIDGE_AGENT_ID")
_ORIG_BRIDGE_DIR = os.environ.get("BRIDGE_DIR")
_ORIG_POLL = os.environ.get("BRIDGE_POLL_INTERVAL")
_ORIG_TIMEOUT = os.environ.get("BRIDGE_TIMEOUT")

os.environ["BRIDGE_AGENT_ID"] = "tester"
import tempfile as _tempfile
os.environ["BRIDGE_DIR"] = str(Path(_tempfile.gettempdir()) / "_bridge_tmp_init")
os.environ["BRIDGE_POLL_INTERVAL"] = "0.05"
os.environ["BRIDGE_TIMEOUT"] = "0.2"

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
)
if "mcp_bridge" in sys.modules:
    del sys.modules["mcp_bridge"]
import mcp_bridge  # noqa: E402


def _cleanup_env():
    for key, orig in [
        ("BRIDGE_AGENT_ID", _ORIG_AGENT_ID),
        ("BRIDGE_DIR", _ORIG_BRIDGE_DIR),
        ("BRIDGE_POLL_INTERVAL", _ORIG_POLL),
        ("BRIDGE_TIMEOUT", _ORIG_TIMEOUT),
    ]:
        if orig is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = orig


import atexit
atexit.register(_cleanup_env)


@pytest.fixture(autouse=True)
def _redirect_bridge(tmp_path):
    """Point module-level globals to a fresh tmp dir for each test."""
    bridge_dir = tmp_path / ".bridge"
    messages_dir = bridge_dir / "messages"
    messages_dir.mkdir(parents=True)

    mcp_bridge.BRIDGE_DIR = bridge_dir
    mcp_bridge.MESSAGES_DIR = messages_dir
    mcp_bridge.AGENT_ID = "tester"
    mcp_bridge.POLL_INTERVAL = 0.05
    mcp_bridge.DEFAULT_TIMEOUT = 0.2
    mcp_bridge.HAS_WATCHFILES = False  # Force polling for determinism

    yield bridge_dir


# ------------------------------------------------------------------ helpers ---


def _write_msg(bridge_dir: Path, msg: dict) -> Path:
    """Write a message JSON file to the bridge messages dir."""
    msg_dir = bridge_dir / "messages"
    msg_id = msg.get("id", "001")
    sender = msg.get("from", "unknown")
    to = msg.get("to", "unknown")
    fname = f"{msg_id}_{sender}_to_{to}.json"
    p = msg_dir / fname
    p.write_text(json.dumps(msg, indent=2), encoding="utf-8")
    return p


# ------------------------------------------------------------ TestHelpers ---


class TestHelpers:
    def test_ensure_dirs_creates_messages(self, tmp_path, _redirect_bridge):
        # Remove the messages dir, verify _ensure_dirs recreates it
        mcp_bridge.MESSAGES_DIR = tmp_path / "new_bridge" / "messages"
        mcp_bridge.MESSAGES_DIR.parent.mkdir(parents=True, exist_ok=True)
        assert not mcp_bridge.MESSAGES_DIR.exists()
        mcp_bridge._ensure_dirs()
        assert mcp_bridge.MESSAGES_DIR.exists()

    def test_next_id_empty(self, _redirect_bridge):
        assert mcp_bridge._next_id() == "001"

    def test_next_id_increments(self, _redirect_bridge):
        _write_msg(_redirect_bridge, {"id": "001", "from": "a", "to": "b"})
        _write_msg(_redirect_bridge, {"id": "002", "from": "a", "to": "b"})
        assert mcp_bridge._next_id() == "003"

    def test_read_message_invalid(self, _redirect_bridge):
        bad = _redirect_bridge / "messages" / "bad.json"
        bad.write_text("NOT JSON", encoding="utf-8")
        assert mcp_bridge._read_message(bad) == {}


# ------------------------------------------------------- TestRegisterAgent ---


class TestRegisterAgent:
    def test_registers_new_agent(self, _redirect_bridge):
        mcp_bridge._register_agent()
        agents_file = _redirect_bridge / "agents.json"
        assert agents_file.exists()
        agents = json.loads(agents_file.read_text(encoding="utf-8"))
        assert "tester" in agents
        assert agents["tester"]["status"] == "online"

    def test_updates_existing(self, _redirect_bridge):
        first_seen = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
        second_seen = datetime(2026, 7, 22, 10, 1, tzinfo=timezone.utc)
        with patch.object(mcp_bridge, "datetime") as datetime_mock:
            datetime_mock.now.side_effect = [first_seen, second_seen]
            mcp_bridge._register_agent()
            agents_file = _redirect_bridge / "agents.json"
            first = json.loads(agents_file.read_text(encoding="utf-8"))
            t1 = first["tester"]["last_seen"]

            mcp_bridge._register_agent()
        second = json.loads(agents_file.read_text(encoding="utf-8"))
        t2 = second["tester"]["last_seen"]
        assert t1 == first_seen.isoformat()
        assert t2 == second_seen.isoformat()
        assert t2 != t1

    def test_handles_corrupt_agents_file(self, _redirect_bridge):
        agents_file = _redirect_bridge / "agents.json"
        agents_file.write_text("BROKEN", encoding="utf-8")
        mcp_bridge._register_agent()
        agents = json.loads(agents_file.read_text(encoding="utf-8"))
        assert "tester" in agents


# ---------------------------------------------------------------- TestSend ---


class TestSend:
    def test_creates_message_file(self, _redirect_bridge):
        result = mcp_bridge.send(to="helper", content="hello")
        files = list((_redirect_bridge / "messages").glob("*.json"))
        # At least one file (from send)
        assert any("tester_to_helper" in f.name for f in files)
        assert "001" in result

    def test_message_format(self, _redirect_bridge):
        mcp_bridge.send(to="helper", content="test msg")
        files = [
            f for f in (_redirect_bridge / "messages").glob("*.json")
            if "tester_to_helper" in f.name
        ]
        assert len(files) == 1
        msg = json.loads(files[0].read_text(encoding="utf-8"))
        assert msg["from"] == "tester"
        assert msg["to"] == "helper"
        assert msg["content"] == "test msg"
        assert msg["status"] == "pending"
        assert msg["reply_to"] is None

    def test_reply_to_set(self, _redirect_bridge):
        mcp_bridge.send(to="helper", content="reply", reply_to="005")
        files = [
            f for f in (_redirect_bridge / "messages").glob("*.json")
            if "tester_to_helper" in f.name
        ]
        msg = json.loads(files[0].read_text(encoding="utf-8"))
        assert msg["reply_to"] == "005"

    def test_sequential_ids(self, _redirect_bridge):
        mcp_bridge.send(to="a", content="m1")
        mcp_bridge.send(to="a", content="m2")
        mcp_bridge.send(to="a", content="m3")
        files = sorted((_redirect_bridge / "messages").glob("*.json"))
        ids = []
        for f in files:
            msg = json.loads(f.read_text(encoding="utf-8"))
            if msg.get("from") == "tester":
                ids.append(msg["id"])
        assert ids == ["001", "002", "003"]


# ------------------------------------------------------------- TestReceive ---


class TestReceive:
    def test_no_messages(self, _redirect_bridge):
        result = mcp_bridge.receive()
        assert "No new messages" in result

    def test_receives_pending(self, _redirect_bridge):
        _write_msg(
            _redirect_bridge,
            {
                "id": "001",
                "from": "helper",
                "to": "tester",
                "timestamp": "2026-03-01T10:00:00Z",
                "reply_to": None,
                "status": "pending",
                "content": "Here is help",
            },
        )
        result = mcp_bridge.receive()
        assert "Here is help" in result
        assert "1 message(s)" in result

    def test_mark_read(self, _redirect_bridge):
        path = _write_msg(
            _redirect_bridge,
            {
                "id": "001",
                "from": "helper",
                "to": "tester",
                "timestamp": "2026-03-01T10:00:00Z",
                "reply_to": None,
                "status": "pending",
                "content": "read me",
            },
        )
        mcp_bridge.receive(mark_read=True)
        msg = json.loads(path.read_text(encoding="utf-8"))
        assert msg["status"] == "read"
        assert "read_at" in msg

    def test_filters_by_agent(self, _redirect_bridge):
        _write_msg(
            _redirect_bridge,
            {
                "id": "001",
                "from": "helper",
                "to": "other_agent",
                "timestamp": "2026-03-01T10:00:00Z",
                "reply_to": None,
                "status": "pending",
                "content": "not for tester",
            },
        )
        result = mcp_bridge.receive()
        assert "No new messages" in result


# ---------------------------------------------------------- TestWaitReply ---


class TestWaitReply:
    def test_immediate_reply(self, _redirect_bridge):
        _write_msg(
            _redirect_bridge,
            {
                "id": "010",
                "from": "helper",
                "to": "tester",
                "timestamp": "2026-03-01T10:00:00Z",
                "reply_to": "005",
                "status": "pending",
                "content": "here is the reply",
            },
        )
        result = mcp_bridge.wait_reply("005")
        assert "here is the reply" in result
        assert "helper" in result

    def test_timeout(self, _redirect_bridge):
        result = mcp_bridge.wait_reply("999", timeout=0.1)
        assert "TIMEOUT" in result

    def test_marks_reply_as_read(self, _redirect_bridge):
        path = _write_msg(
            _redirect_bridge,
            {
                "id": "010",
                "from": "helper",
                "to": "tester",
                "timestamp": "2026-03-01T10:00:00Z",
                "reply_to": "005",
                "status": "pending",
                "content": "reply",
            },
        )
        mcp_bridge.wait_reply("005")
        msg = json.loads(path.read_text(encoding="utf-8"))
        assert msg["status"] == "read"


# --------------------------------------------------------- TestListAgents ---


class TestListAgents:
    def test_no_agents_file(self, _redirect_bridge):
        # Remove agents file if _register_agent created it
        af = _redirect_bridge / "agents.json"
        if af.exists():
            af.unlink()
        result = mcp_bridge.list_agents()
        assert "tester" in result

    def test_shows_registered(self, _redirect_bridge):
        agents = {
            "tester": {"last_seen": "2026-03-01T10:00:00Z", "status": "online"},
            "helper": {"last_seen": "2026-03-01T09:00:00Z", "status": "online"},
        }
        (_redirect_bridge / "agents.json").write_text(
            json.dumps(agents), encoding="utf-8"
        )
        result = mcp_bridge.list_agents()
        assert "tester" in result
        assert "helper" in result
        assert "<-- you" in result


# ------------------------------------------------------------- TestHistory ---


class TestHistory:
    def test_empty_bridge(self, _redirect_bridge):
        result = mcp_bridge.history()
        assert "No messages" in result

    def test_shows_recent(self, _redirect_bridge):
        for i in range(3):
            _write_msg(
                _redirect_bridge,
                {
                    "id": f"{i + 1:03d}",
                    "from": "coder",
                    "to": "helper",
                    "timestamp": f"2026-03-01T10:0{i}:00Z",
                    "reply_to": None,
                    "status": "pending",
                    "content": f"msg {i + 1}",
                },
            )
        result = mcp_bridge.history()
        assert "msg 1" in result
        assert "msg 3" in result
        assert "3 messages" in result
