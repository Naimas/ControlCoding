#!/usr/bin/env python3
"""T3 Scenario Tests - Full workflow simulations.

Simulates realistic ControlCoding sessions end-to-end:
1. Two-session project: init -> work -> checkpoint -> new session -> read state
2. Debug escalation: repeated failures -> consult -> socratic -> human escalation
3. Multi-agent: coder sends to helper, helper replies, coder receives

These tests verify that components work together in realistic sequences,
catching integration issues that isolated tests miss.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


# ================================================================
# Scenario 1: Two-Session Project
# ================================================================


class TestTwoSessionProject:
    """Simulates a project across two sessions.

    Session 1: create project, do work, checkpoint
    Session 2: read previous state, continue work, checkpoint again
    Verify: history accumulates, devlogs chain, status reflects latest
    """

    @pytest.fixture
    def project(self, tmp_path):
        _restore_real_modules()

        # Create minimal project structure
        (tmp_path / "CLAUDE.md").write_text(
            "# MyProject\n## Module Boundaries\n- src/core/engine.py\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "core").mkdir(parents=True)
        (tmp_path / "src" / "core" / "engine.py").write_text(
            "class Engine: pass\n", encoding="utf-8"
        )

        os.environ["SESSION_PROJECT_ROOT"] = str(tmp_path)
        if "mcp_session" in sys.modules:
            del sys.modules["mcp_session"]
        import mcp_session

        mcp_session.PROJECT_ROOT = tmp_path
        mcp_session.STATUS_FILE = tmp_path / "STATUS.md"
        mcp_session.HISTORY_FILE = tmp_path / "STATUS_HISTORY.md"
        mcp_session.DEVLOG_DIR = tmp_path / "devlog"
        mcp_session.CLAUDE_MD = tmp_path / "CLAUDE.md"
        mcp_session.SESSION_COUNTER_FILE = tmp_path / ".claude" / "session_counter.json"

        yield mcp_session.mcp, tmp_path

    def test_two_session_workflow(self, project):
        mcp, tmp_path = project

        async def run():
            # ---- SESSION 1 ----
            async with Client(mcp) as client:
                # Start of session: no status yet
                r = await client.call_tool("read_status", {})
                assert "No STATUS.md found" in r.data

                # Do work, then checkpoint
                r = await client.call_tool("checkpoint", {
                    "summary": "Implemented core engine with basic physics",
                    "decisions": "- Used Euler integration for simplicity",
                    "problems": "None",
                    "files_changed": "src/core/engine.py",
                    "what_next": "Add collision detection",
                })
                assert "Status:" in r.data
                assert "Devlog:" in r.data
                # CLAUDE.md references engine.py -> should flag review
                assert "REVIEW NEEDED" in r.data

            # Verify session 1 artifacts
            status = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
            assert "core engine" in status
            assert "collision detection" in status

            devlogs = sorted(p for p in (tmp_path / "devlog").glob("*.md") if p.name != "index.md")
            assert len(devlogs) == 1
            assert "engine" in devlogs[0].read_text(encoding="utf-8").lower()

            # ---- SESSION 2 ----
            async with Client(mcp) as client:
                # Read previous state
                r = await client.call_tool("read_status", {})
                assert "core engine" in r.data
                assert "collision detection" in r.data

                # Read devlog from session 1
                r = await client.call_tool("read_devlog", {"last_n": 3})
                assert "engine" in r.data.lower()

                # Do more work, checkpoint again
                r = await client.call_tool("checkpoint", {
                    "summary": "Added collision detection system",
                    "decisions": "- Used AABB for broad phase",
                    "problems": "- GJK narrow phase is complex",
                    "files_changed": "src/core/collision.py",
                    "what_next": "Optimize with spatial hashing",
                })
                assert "Status:" in r.data

            # Verify session 2 artifacts
            status = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
            assert "collision detection" in status
            assert "spatial hashing" in status
            # Session 1 status should NOT be in current
            assert "core engine with basic physics" not in status

            # History should contain session 1
            history = (tmp_path / "STATUS_HISTORY.md").read_text(encoding="utf-8")
            assert "core engine" in history

            # Two devlogs now
            devlogs = sorted(p for p in (tmp_path / "devlog").glob("*.md") if p.name != "index.md")
            assert len(devlogs) == 2

            # Read history via tool
            async with Client(mcp) as client:
                r = await client.call_tool("read_history", {"last_n": 5})
                assert "core engine" in r.data

        asyncio.run(run())


# ================================================================
# Scenario 2: Debug Escalation (L1 -> L2 -> L3 -> L4)
# ================================================================


class TestDebugEscalation:
    """Simulates a debug escalation from consultation to human.

    1. Start a debug session (L2)
    2. Multiple exchanges without resolution
    3. System suggests socratic escalation (L3)
    4. Socratic also fails -> escalate to human (L4)
    """

    @pytest.fixture
    def servers(self, tmp_path):
        _restore_real_modules()

        # Consultant setup
        os.environ["CONSULT_MAX_CALLS"] = "0"
        os.environ["CONSULT_BACKEND"] = "ollama"
        if "mcp_consultant" in sys.modules:
            del sys.modules["mcp_consultant"]
        import mcp_consultant
        mcp_consultant.LOG_DIR = str(tmp_path / ".claude")
        mcp_consultant._call_counter = mcp_consultant._CallCounter(0)
        # Lower thresholds for testing
        mcp_consultant.ESCALATION_THRESHOLD = 2
        mcp_consultant.MAX_EXCHANGES = 3

        # Session setup for escalation
        os.environ["SESSION_PROJECT_ROOT"] = str(tmp_path)
        if "mcp_session" in sys.modules:
            del sys.modules["mcp_session"]
        import mcp_session
        mcp_session.PROJECT_ROOT = tmp_path
        mcp_session.STATUS_FILE = tmp_path / "STATUS.md"
        mcp_session.HISTORY_FILE = tmp_path / "STATUS_HISTORY.md"
        mcp_session.DEVLOG_DIR = tmp_path / "devlog"
        mcp_session.CLAUDE_MD = tmp_path / "CLAUDE.md"
        mcp_session.SESSION_COUNTER_FILE = tmp_path / ".claude" / "session_counter.json"

        yield mcp_consultant, mcp_session, tmp_path

    def test_escalation_flow(self, servers):
        consultant_mod, session_mod, tmp_path = servers

        async def run():
            # -- L2: Start debug session --
            with patch.object(consultant_mod, "_call_ollama",
                              return_value="Try checking the null pointer"):
                async with Client(consultant_mod.mcp) as client:
                    r = await client.call_tool("consult", {
                        "problem": "Renderer crashes on frame 3",
                        "role": "debug",
                        "session": "new",
                    })
                    assert "debug-001" in r.data
                    assert "Exchange 1" in r.data
                    session_id = "debug-001"

            # -- L2: Second exchange --
            with patch.object(consultant_mod, "_call_ollama",
                              return_value="Check the buffer allocation"):
                async with Client(consultant_mod.mcp) as client:
                    r = await client.call_tool("consult", {
                        "problem": "Null pointer checked, still crashes",
                        "role": "debug",
                        "session": session_id,
                    })
                    assert "Exchange 2" in r.data
                    # Should get escalation hint (threshold = 2)
                    assert "ESCALATION HINT" in r.data
                    assert "socratic" in r.data

            # -- L3: Socratic challenge --
            with patch.object(consultant_mod, "_call_ollama",
                              return_value="Have you considered a race condition?"):
                async with Client(consultant_mod.mcp) as client:
                    r = await client.call_tool("consult", {
                        "problem": "Stuck after 2 debug exchanges. Is it a threading issue?",
                        "role": "socratic",
                        "session": "new",
                    })
                    assert "race condition" in r.data

            # Verify session log accumulated
            session_log = tmp_path / ".claude" / "consult_sessions.jsonl"
            assert session_log.exists()
            entries = [
                json.loads(line)
                for line in session_log.read_text(encoding="utf-8").splitlines()
            ]
            debug_entries = [e for e in entries if e["session_id"] == "debug-001"]
            assert len(debug_entries) == 2

            # -- L4: Human escalation --
            async with Client(session_mod.mcp) as client:
                r = await client.call_tool("escalate_to_human", {
                    "problem": "Renderer crash not resolved after debug + socratic",
                    "options": "A) Rewrite renderer from scratch\nB) Add mutex locks",
                    "session_id": session_id,
                    "consultant_summary": "Suggested null check and buffer alloc",
                    "socratic_insight": "Might be a race condition",
                })
                assert "ESCALATION" in r.data
                assert "STOP" in r.data

            # Verify escalation file
            esc = _control_plane_file(tmp_path, "escalation.md").read_text(encoding="utf-8")
            assert "not resolved" in esc
            assert "debug-001" in esc
            assert "race condition" in esc

        asyncio.run(run())


# ================================================================
# Scenario 3: Multi-Agent Communication
# ================================================================


class TestMultiAgent:
    """Simulates two agents communicating via the bridge.

    1. Coder sends a request to helper
    2. Helper receives it
    3. Helper sends a reply
    4. Coder receives the reply
    """

    @pytest.fixture
    def bridge_pair(self, tmp_path):
        _restore_real_modules()

        bridge_dir = tmp_path / ".bridge"
        messages_dir = bridge_dir / "messages"
        messages_dir.mkdir(parents=True)

        def _make_bridge(agent_id):
            os.environ["BRIDGE_AGENT_ID"] = agent_id
            os.environ["BRIDGE_DIR"] = str(bridge_dir)
            os.environ["BRIDGE_POLL_INTERVAL"] = "0.05"
            os.environ["BRIDGE_TIMEOUT"] = "0.3"
            if "mcp_bridge" in sys.modules:
                del sys.modules["mcp_bridge"]
            import mcp_bridge
            mcp_bridge.BRIDGE_DIR = bridge_dir
            mcp_bridge.MESSAGES_DIR = messages_dir
            mcp_bridge.AGENT_ID = agent_id
            mcp_bridge.POLL_INTERVAL = 0.05
            mcp_bridge.DEFAULT_TIMEOUT = 0.3
            mcp_bridge.HAS_WATCHFILES = False
            return mcp_bridge

        yield _make_bridge, bridge_dir

    def test_coder_helper_exchange(self, bridge_pair):
        make_bridge, bridge_dir = bridge_pair

        async def run():
            # -- Coder sends request --
            coder_mod = make_bridge("coder")
            async with Client(coder_mod.mcp) as coder:
                r = await coder.call_tool("send", {
                    "to": "helper",
                    "content": "Please review the auth module for security issues",
                })
                assert "001" in r.data
                msg_id = "001"

            # -- Helper receives --
            helper_mod = make_bridge("helper")
            async with Client(helper_mod.mcp) as helper:
                r = await helper.call_tool("receive", {"mark_read": True})
                assert "auth module" in r.data
                assert "1 message(s)" in r.data

                # Helper sends reply
                r = await helper.call_tool("send", {
                    "to": "coder",
                    "content": "Found SQL injection in login handler, line 42",
                    "reply_to": msg_id,
                })

            # -- Coder reads reply --
            coder_mod2 = make_bridge("coder")
            async with Client(coder_mod2.mcp) as coder:
                r = await coder.call_tool("receive", {"mark_read": True})
                assert "SQL injection" in r.data

            # -- Both see full history --
            async with Client(coder_mod2.mcp) as coder:
                r = await coder.call_tool("history", {})
                assert "auth module" in r.data
                assert "SQL injection" in r.data

            # -- Agent list shows both --
            async with Client(coder_mod2.mcp) as coder:
                r = await coder.call_tool("list_agents", {})
                # At least the current agent should be listed
                assert "coder" in r.data

        asyncio.run(run())

    def test_agent_isolation(self, bridge_pair):
        """Messages to other agents are not visible."""
        make_bridge, bridge_dir = bridge_pair

        async def run():
            # Coder sends to helper
            coder_mod = make_bridge("coder")
            async with Client(coder_mod.mcp) as coder:
                await coder.call_tool("send", {
                    "to": "helper",
                    "content": "secret message",
                })

            # Analyst should not see coder->helper messages
            analyst_mod = make_bridge("analyst")
            async with Client(analyst_mod.mcp) as analyst:
                r = await analyst.call_tool("receive", {})
                assert "No new messages" in r.data

        asyncio.run(run())


# ================================================================
# Scenario 4: Session + Consultation Combined
# ================================================================


class TestSessionWithConsultation:
    """Verifies that session checkpoints and consultations
    produce complementary artifacts in the same project."""

    @pytest.fixture
    def combined(self, tmp_path):
        _restore_real_modules()

        # Session
        os.environ["SESSION_PROJECT_ROOT"] = str(tmp_path)
        if "mcp_session" in sys.modules:
            del sys.modules["mcp_session"]
        import mcp_session
        mcp_session.PROJECT_ROOT = tmp_path
        mcp_session.STATUS_FILE = tmp_path / "STATUS.md"
        mcp_session.HISTORY_FILE = tmp_path / "STATUS_HISTORY.md"
        mcp_session.DEVLOG_DIR = tmp_path / "devlog"
        mcp_session.CLAUDE_MD = tmp_path / "CLAUDE.md"
        mcp_session.SESSION_COUNTER_FILE = tmp_path / ".claude" / "session_counter.json"

        # Consultant
        os.environ["CONSULT_MAX_CALLS"] = "0"
        os.environ["CONSULT_BACKEND"] = "ollama"
        if "mcp_consultant" in sys.modules:
            del sys.modules["mcp_consultant"]
        import mcp_consultant
        mcp_consultant.LOG_DIR = str(tmp_path / ".claude")
        mcp_consultant._call_counter = mcp_consultant._CallCounter(0)

        yield mcp_session, mcp_consultant, tmp_path

    def test_checkpoint_then_consult_then_checkpoint(self, combined):
        session_mod, consultant_mod, tmp_path = combined

        async def run():
            # Checkpoint: started work
            async with Client(session_mod.mcp) as client:
                await client.call_tool("checkpoint", {
                    "summary": "Built parser module",
                    "files_changed": "src/parser.py",
                    "what_next": "Handle edge cases",
                })

            # Consult: ask about edge cases
            with patch.object(consultant_mod, "_call_ollama",
                              return_value="Watch for empty input and unicode"):
                async with Client(consultant_mod.mcp) as client:
                    r = await client.call_tool("consult", {
                        "problem": "What edge cases should I handle in a text parser?",
                        "role": "reviewer",
                    })
                    assert "empty input" in r.data

            # Checkpoint: applied advice
            async with Client(session_mod.mcp) as client:
                await client.call_tool("checkpoint", {
                    "summary": "Added edge case handling per consultant advice",
                    "decisions": "- Handle empty input, unicode, max length",
                    "files_changed": "src/parser.py",
                    "what_next": "Write tests",
                })

            # Verify both systems produced artifacts
            status = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
            assert "edge case" in status

            devlogs = sorted(p for p in (tmp_path / "devlog").glob("*.md") if p.name != "index.md")
            assert len(devlogs) == 2

            consult_log = tmp_path / ".claude" / "consult_log.jsonl"
            assert consult_log.exists()
            entry = json.loads(consult_log.read_text(encoding="utf-8").splitlines()[0])
            assert entry["role"] == "reviewer"

            history = (tmp_path / "STATUS_HISTORY.md").read_text(encoding="utf-8")
            assert "parser module" in history

        asyncio.run(run())
