#!/usr/bin/env python3
"""T2 Integration Tests - MCP server round-trips via fastmcp Client.

Tests each MCP server by calling tools through the real MCP protocol
(in-process via fastmcp Client) and verifying filesystem results.

Unlike T1 unit tests (which mock everything), these tests exercise
the full tool -> logic -> filesystem -> response path.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# --------------- Setup: ensure templates/scripts is importable ---------------

TEMPLATES_SCRIPTS = str(Path(__file__).resolve().parent.parent / "templates" / "scripts")
SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if TEMPLATES_SCRIPTS not in sys.path:
    sys.path.insert(0, TEMPLATES_SCRIPTS)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def _restore_real_modules(include_fastmcp: bool = False):
    """Remove mocked modules so the real MCP stack can load consistently."""
    prefixes = ("watchfiles", "mcp_bridge", "mcp_session", "mcp_consultant")
    if include_fastmcp:
        prefixes = ("fastmcp",) + prefixes
    for mod_name in list(sys.modules):
        if mod_name == prefixes or mod_name.startswith(prefixes):
            del sys.modules[mod_name]


_restore_real_modules(include_fastmcp=True)

from fastmcp import Client as _FastMCPClient
from fastmcp.client.transports import FastMCPTransport


def Client(transport):
    return _FastMCPClient(FastMCPTransport(mcp=transport))


def _control_plane_file(root: Path, filename: str) -> Path:
    canonical = root / ".controlcoding" / filename
    if canonical.exists():
        return canonical
    return root / ".claude" / filename


# ============================================================
# Session Manager Integration Tests
# ============================================================


@pytest.fixture
def session_server(tmp_path):
    """Create a fresh mcp_session server pointing to tmp_path."""
    _restore_real_modules()

    # Set env vars before import
    os.environ["SESSION_PROJECT_ROOT"] = str(tmp_path)
    os.environ["SESSION_DEVLOG_DIR"] = "devlog"
    os.environ["SESSION_STATUS_FILE"] = "STATUS.md"
    os.environ["SESSION_HISTORY_FILE"] = "STATUS_HISTORY.md"

    # Force reimport to pick up new env vars
    if "mcp_session" in sys.modules:
        del sys.modules["mcp_session"]
    import mcp_session

    # Override module-level paths to tmp_path
    mcp_session.PROJECT_ROOT = tmp_path
    mcp_session.STATUS_FILE = tmp_path / "STATUS.md"
    mcp_session.HISTORY_FILE = tmp_path / "STATUS_HISTORY.md"
    mcp_session.DEVLOG_DIR = tmp_path / "devlog"
    mcp_session.CLAUDE_MD = tmp_path / "CLAUDE.md"
    mcp_session.SESSION_COUNTER_FILE = tmp_path / ".claude" / "session_counter.json"

    yield mcp_session.mcp, tmp_path


class TestSessionIntegration:
    def test_read_status_empty(self, session_server):
        mcp, tmp_path = session_server

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("read_status", {})
                assert "No STATUS.md found" in result.data

        asyncio.run(run())

    def test_update_status_creates_file(self, session_server):
        mcp, tmp_path = session_server

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("update_status", {
                    "what_done": "Implemented auth module",
                    "what_next": "Add tests for auth",
                    "blockers": "",
                    "notes": "Using JWT tokens",
                })
                assert "STATUS.md updated" in result.data

            # Verify file on disk
            status = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
            assert "Implemented auth module" in status
            assert "Add tests for auth" in status
            assert "JWT tokens" in status

        asyncio.run(run())

    def test_update_status_archives_previous(self, session_server):
        mcp, tmp_path = session_server

        async def run():
            async with Client(mcp) as client:
                # First update
                await client.call_tool("update_status", {
                    "what_done": "First milestone",
                    "what_next": "Second task",
                })
                # Second update should archive the first
                await client.call_tool("update_status", {
                    "what_done": "Second milestone",
                    "what_next": "Third task",
                })

            # STATUS.md should have second content
            status = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
            assert "Second milestone" in status
            assert "First milestone" not in status

            # HISTORY should have first content
            history = (tmp_path / "STATUS_HISTORY.md").read_text(encoding="utf-8")
            assert "First milestone" in history

        asyncio.run(run())

    def test_write_devlog_creates_file(self, session_server):
        mcp, tmp_path = session_server

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("write_devlog", {
                    "summary": "Added lighting system",
                    "decisions": "- Used Phong model over PBR",
                    "problems": "None",
                    "files_changed": "renderer/lighting.cpp",
                })
                assert "Devlog created" in result.data

            # Verify devlog file exists
            devlogs = [p for p in (tmp_path / "devlog").glob("*.md") if p.name != "index.md"]
            assert len(devlogs) == 1
            content = devlogs[0].read_text(encoding="utf-8")
            assert "lighting system" in content
            assert "Phong model" in content

        asyncio.run(run())

    def test_checkpoint_roundtrip(self, session_server):
        mcp, tmp_path = session_server

        # Create a CLAUDE.md to test the review check
        (tmp_path / "CLAUDE.md").write_text(
            "# Project\n## Module Boundaries\n- renderer/lighting.cpp\n",
            encoding="utf-8",
        )

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("checkpoint", {
                    "summary": "Added lighting system",
                    "decisions": "Used Phong model",
                    "problems": "None",
                    "files_changed": "renderer/lighting.cpp",
                    "what_next": "Add shadows",
                })
                text = result.data
                assert "Status:" in text
                assert "Devlog:" in text
                assert "CLAUDE.md:" in text

            # Verify all artifacts
            assert (tmp_path / "STATUS.md").exists()
            status = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
            assert "lighting system" in status
            assert "Add shadows" in status

            devlogs = [
                p
                for p in (tmp_path / "devlog").glob("*.md")
                if p.name != "index.md"
            ]
            assert len(devlogs) == 1

        asyncio.run(run())

    def test_read_devlog_after_writes(self, session_server):
        mcp, tmp_path = session_server

        async def run():
            async with Client(mcp) as client:
                await client.call_tool("write_devlog", {
                    "summary": "First entry",
                })
                await client.call_tool("write_devlog", {
                    "summary": "Second entry",
                })
                result = await client.call_tool("read_devlog", {"last_n": 2})
                assert "First entry" in result.data
                assert "Second entry" in result.data
                assert "2 devlog entries" in result.data

        asyncio.run(run())

    def test_read_history_after_updates(self, session_server):
        mcp, tmp_path = session_server

        async def run():
            async with Client(mcp) as client:
                await client.call_tool("update_status", {
                    "what_done": "v1",
                    "what_next": "v2 next",
                })
                await client.call_tool("update_status", {
                    "what_done": "v2",
                    "what_next": "v3 next",
                })
                await client.call_tool("update_status", {
                    "what_done": "v3",
                    "what_next": "done",
                })
                result = await client.call_tool("read_history", {"last_n": 2})
                # Should have v1 and v2 archived (v3 is current)
                assert "v1" in result.data or "v2" in result.data

        asyncio.run(run())

    def test_escalate_to_human(self, session_server):
        mcp, tmp_path = session_server

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("escalate_to_human", {
                    "problem": "Cannot decide between approach A and B",
                    "options": "A) Fast but fragile\nB) Slow but robust",
                    "session_id": "debug-003",
                })
                assert "ESCALATION" in result.data
                assert "STOP" in result.data

            # Verify escalation file
            esc = _control_plane_file(tmp_path, "escalation.md").read_text(encoding="utf-8")
            assert "approach A and B" in esc
            assert "debug-003" in esc

        asyncio.run(run())


# ============================================================
# Bridge Integration Tests
# ============================================================


@pytest.fixture
def bridge_server(tmp_path):
    """Create a fresh mcp_bridge server pointing to tmp_path."""
    _restore_real_modules()

    bridge_dir = tmp_path / ".bridge"
    messages_dir = bridge_dir / "messages"
    messages_dir.mkdir(parents=True)

    os.environ["BRIDGE_AGENT_ID"] = "integration_tester"
    os.environ["BRIDGE_DIR"] = str(bridge_dir)
    os.environ["BRIDGE_POLL_INTERVAL"] = "0.05"
    os.environ["BRIDGE_TIMEOUT"] = "0.2"

    if "mcp_bridge" in sys.modules:
        del sys.modules["mcp_bridge"]
    import mcp_bridge

    # Override globals
    mcp_bridge.BRIDGE_DIR = bridge_dir
    mcp_bridge.MESSAGES_DIR = messages_dir
    mcp_bridge.AGENT_ID = "integration_tester"
    mcp_bridge.POLL_INTERVAL = 0.05
    mcp_bridge.DEFAULT_TIMEOUT = 0.2
    mcp_bridge.HAS_WATCHFILES = False

    yield mcp_bridge.mcp, tmp_path, bridge_dir


class TestBridgeIntegration:
    def test_send_and_receive(self, bridge_server):
        mcp, tmp_path, bridge_dir = bridge_server

        async def run():
            async with Client(mcp) as client:
                # Send a message
                send_result = await client.call_tool("send", {
                    "to": "helper",
                    "content": "Need help with rendering",
                })
                assert "001" in send_result.data

                # Verify file on disk
                files = list((bridge_dir / "messages").glob("*.json"))
                assert len(files) >= 1
                msg = json.loads(files[0].read_text(encoding="utf-8"))
                assert msg["from"] == "integration_tester"
                assert msg["to"] == "helper"
                assert msg["content"] == "Need help with rendering"

        asyncio.run(run())

    def test_send_then_list_agents(self, bridge_server):
        mcp, tmp_path, bridge_dir = bridge_server

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("list_agents", {})
                assert "integration_tester" in result.data

        asyncio.run(run())

    def test_receive_no_messages(self, bridge_server):
        mcp, tmp_path, bridge_dir = bridge_server

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("receive", {})
                assert "No new messages" in result.data

        asyncio.run(run())

    def test_send_receive_reply_flow(self, bridge_server):
        """Test the full send -> simulate reply -> receive flow."""
        mcp, tmp_path, bridge_dir = bridge_server

        async def run():
            async with Client(mcp) as client:
                # Tester sends a message
                send_result = await client.call_tool("send", {
                    "to": "helper",
                    "content": "What is the bug?",
                })
                msg_id = "001"

                # Simulate helper replying (write file directly)
                reply = {
                    "id": "002",
                    "from": "helper",
                    "to": "integration_tester",
                    "timestamp": "2026-03-12T10:00:00Z",
                    "reply_to": msg_id,
                    "status": "pending",
                    "content": "The bug is in the renderer",
                }
                reply_file = bridge_dir / "messages" / "002_helper_to_integration_tester.json"
                reply_file.write_text(json.dumps(reply), encoding="utf-8")

                # Tester receives the reply
                recv_result = await client.call_tool("receive", {
                    "mark_read": True,
                })
                assert "renderer" in recv_result.data
                assert "1 message(s)" in recv_result.data

        asyncio.run(run())

    def test_history_shows_messages(self, bridge_server):
        mcp, tmp_path, bridge_dir = bridge_server

        async def run():
            async with Client(mcp) as client:
                await client.call_tool("send", {
                    "to": "helper",
                    "content": "msg1",
                })
                await client.call_tool("send", {
                    "to": "helper",
                    "content": "msg2",
                })
                result = await client.call_tool("history", {})
                assert "msg1" in result.data
                assert "msg2" in result.data

        asyncio.run(run())

    def test_wait_reply_timeout(self, bridge_server):
        mcp, tmp_path, bridge_dir = bridge_server

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("wait_reply", {
                    "message_id": "999",
                    "timeout": 0.1,
                })
                assert "TIMEOUT" in result.data

        asyncio.run(run())


# ============================================================
# Consultant Integration Tests
# ============================================================


@pytest.fixture
def consultant_server(tmp_path):
    """Create a fresh mcp_consultant server with mocked backends."""
    _restore_real_modules()

    os.environ["CONSULT_MAX_CALLS"] = "0"
    os.environ["CONSULT_BACKEND"] = "ollama"

    if "mcp_consultant" in sys.modules:
        del sys.modules["mcp_consultant"]
    import mcp_consultant

    # Redirect log dir
    mcp_consultant.LOG_DIR = str(tmp_path / ".claude")
    # Reset call counter
    mcp_consultant._call_counter = mcp_consultant._CallCounter(0)

    yield mcp_consultant.mcp, tmp_path, mcp_consultant


class TestConsultantIntegration:
    def test_consult_basic(self, consultant_server):
        mcp, tmp_path, mod = consultant_server

        async def run():
            with patch.object(mod, "_call_ollama", return_value="Root cause is X"):
                async with Client(mcp) as client:
                    result = await client.call_tool("consult", {
                        "problem": "Function returns None instead of a dict",
                        "role": "debug",
                        "code_snippets": "def get_data(): return None",
                    })
                    assert "Root cause is X" in result.data

        asyncio.run(run())

    def test_consult_creates_log(self, consultant_server):
        mcp, tmp_path, mod = consultant_server

        async def run():
            with patch.object(mod, "_call_ollama", return_value="Analysis done"):
                async with Client(mcp) as client:
                    await client.call_tool("consult", {
                        "problem": "Test logging",
                    })

            # Verify log file created
            log_file = tmp_path / ".claude" / "consult_log.jsonl"
            assert log_file.exists()
            entry = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
            assert entry["role"] == "debug"
            assert entry["backend"] == "ollama"

        asyncio.run(run())

    def test_consult_prompt_too_large(self, consultant_server):
        mcp, tmp_path, mod = consultant_server

        original = mod.MAX_PROMPT_CHARS
        mod.MAX_PROMPT_CHARS = 50

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("consult", {
                    "problem": "x" * 200,
                })
                assert "too large" in result.data

        try:
            asyncio.run(run())
        finally:
            mod.MAX_PROMPT_CHARS = original

    def test_consult_session_new(self, consultant_server):
        mcp, tmp_path, mod = consultant_server

        async def run():
            with patch.object(mod, "_call_ollama", return_value="Session answer"):
                async with Client(mcp) as client:
                    result = await client.call_tool("consult", {
                        "problem": "Debug iteratively",
                        "session": "new",
                    })
                    assert "debug-001" in result.data
                    assert "Exchange 1" in result.data

        asyncio.run(run())

    def test_consult_all_roles(self, consultant_server):
        """Verify all 5 roles are accepted."""
        mcp, tmp_path, mod = consultant_server

        async def run():
            for role in ["debug", "architect", "planner", "reviewer", "socratic"]:
                with patch.object(mod, "_call_ollama", return_value=f"{role} ok"):
                    async with Client(mcp) as client:
                        result = await client.call_tool("consult", {
                            "problem": f"Test {role}",
                            "role": role,
                        })
                        assert f"{role} ok" in result.data

        asyncio.run(run())

    def test_consult_domain_specialization(self, consultant_server):
        mcp, tmp_path, mod = consultant_server

        async def run():
            with patch.object(mod, "_call_ollama", return_value="3D answer") as mock:
                async with Client(mcp) as client:
                    await client.call_tool("consult", {
                        "problem": "Rendering issue",
                        "domain": "3D graphics",
                    })
                # Verify domain was injected into system prompt
                call_args = mock.call_args
                system_prompt = call_args[1].get("system_prompt") or call_args[0][2]
                assert "3D graphics" in system_prompt

        asyncio.run(run())

    def test_consult_call_limit(self, consultant_server):
        mcp, tmp_path, mod = consultant_server

        mod._call_counter = mod._CallCounter(1)
        mod._call_counter.increment()
        original_max = mod.MAX_CALLS
        mod.MAX_CALLS = 1

        async def run():
            async with Client(mcp) as client:
                result = await client.call_tool("consult", {
                    "problem": "Should be blocked",
                })
                assert "limit reached" in result.data

        try:
            asyncio.run(run())
        finally:
            mod.MAX_CALLS = original_max
            mod._call_counter = mod._CallCounter(0)
