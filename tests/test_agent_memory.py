"""Tests for per-agent memory system (Phase 2 of F1 Context Handoff).

Tests cover:
- agent_start: init, resume, stateless rejection, unknown rejection
- agent_checkpoint: state update, decision recording, error cases
- agent_close: log.md writing, state update, index rebuild
- agent_resume: brief construction, stateless rejection
- agent_status: single agent, all agents table
- cc resume --agent: brief output
- cc agents: status table
- write isolation: SESSION_AGENT_NAME blocks cross-agent writes
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Add templates/scripts to sys.path to import mcp_agent_memory
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "templates" / "scripts"))
sys.path.insert(0, str(REPO / "scripts"))

import importlib
import os


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project structure in tmp_path."""
    (tmp_path / ".controlcoding").mkdir()
    (tmp_path / "STATUS.md").write_text(
        "# Project Status\n\n## Current State\nTest state.\n\n## Next Steps\nTest next.\n",
        encoding="utf-8",
    )
    return tmp_path


def _load_session(project: Path):
    """Load mcp_agent_memory with SESSION_PROJECT_ROOT pointing to project."""
    os.environ["SESSION_PROJECT_ROOT"] = str(project)
    if "mcp_agent_memory" in sys.modules:
        del sys.modules["mcp_agent_memory"]
    import mcp_agent_memory as m
    return m


# ---------------------------------------------------------------------------
# agent_start
# ---------------------------------------------------------------------------

class TestAgentStart:
    def test_creates_canonical_agent_task_layout(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design WebSocket Gateway")
        agent_root = tmp_path / ".controlcoding" / "agents" / "architect"
        task_dirs = list((agent_root / "tasks").iterdir())
        assert agent_root.is_dir()
        assert (agent_root / "profile.json").exists()
        assert (agent_root / "memory.md").exists()
        assert (agent_root / "task_index.jsonl").exists()
        assert len(task_dirs) == 1
        task_root = task_dirs[0]
        assert (task_root / "request.md").exists()
        assert (task_root / "state.json").exists()
        assert (task_root / "chats").is_dir()
        assert (task_root / "worklog").is_dir()
        assert (task_root / "handoffs").is_dir()
        assert (task_root / "executions").is_dir()

    def test_first_session_creates_folders(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_start("architect", "Design WebSocket Gateway")
        assert "initialized (first session)" in result
        assert (tmp_path / ".controlcoding" / "sessions" / "agents" / "architect").is_dir()
        assert (tmp_path / ".controlcoding" / "sessions" / "agents" / "architect" / "chats").is_dir()

    def test_first_session_writes_state(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("coder", "Implement login form")
        state_file = tmp_path / ".controlcoding" / "sessions" / "agents" / "coder" / "state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["agent"] == "coder"
        assert state["current_task"] == "Implement login form"
        assert state["phase"] == "in_progress"
        assert state["last_chat"].startswith("chat-001-")

    def test_resume_returns_brief(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "First task")
        # Close to create log
        m.agent_close("architect", "complete", "Did first task.", pending="Nothing left")
        # Start again
        result = m.agent_start("architect", "Second task")
        assert "resumed" in result
        assert "RESUME BRIEF" in result
        assert "Second task" in result

    def test_second_start_creates_chat_002(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Task 1")
        m.agent_close("architect", "complete", "Done task 1.")
        m.agent_start("architect", "Task 2")
        state_file = tmp_path / ".controlcoding" / "sessions" / "agents" / "architect" / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["last_chat"].startswith("chat-002-")

    def test_stateless_agent_rejected(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_start("expert", "Consult on UI design")
        assert "ERROR" in result
        assert "stateless" in result
        # No folder should be created
        assert not (tmp_path / ".controlcoding" / "sessions" / "agents" / "expert").exists()

    def test_socratic_rejected(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_start("socratic", "Challenge assumptions")
        assert "ERROR" in result
        assert "stateless" in result

    def test_unknown_agent_rejected(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_start("wizard", "Do magic")
        assert "ERROR" in result
        assert "Unknown agent" in result

    def test_case_insensitive_name(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_start("ARCHITECT", "Design something")
        assert "ERROR" not in result
        assert (tmp_path / ".controlcoding" / "sessions" / "agents" / "architect").is_dir()

    def test_all_persistent_agents_accepted(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        for name in ["concierge", "architect", "coder", "reviewer", "debugger"]:
            result = m.agent_start(name, f"Task for {name}")
            assert "ERROR" not in result, f"agent_start failed for {name}: {result}"


# ---------------------------------------------------------------------------
# agent_checkpoint
# ---------------------------------------------------------------------------

class TestAgentCheckpoint:
    def test_updates_pending(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("coder", "Build auth module")
        result = m.agent_checkpoint(
            "coder",
            pending="Write tests\nFix linting",
        )
        assert "State updated" in result
        state_file = tmp_path / ".controlcoding" / "sessions" / "agents" / "coder" / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert "Write tests" in state["pending"]
        assert "Fix linting" in state["pending"]

    def test_updates_open_questions(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design DB schema")
        m.agent_checkpoint("architect", open_questions="Should we use UUID primary keys?")
        state_file = tmp_path / ".controlcoding" / "sessions" / "agents" / "architect" / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert any("UUID" in q for q in state["open_questions"])

    def test_updates_context_note(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("reviewer", "Review PR #42")
        m.agent_checkpoint("reviewer", context_note="Found 3 issues in auth module.")
        state_file = tmp_path / ".controlcoding" / "sessions" / "agents" / "reviewer" / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert "Found 3 issues" in state["context_note"]

    def test_records_decision(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design API")
        result = m.agent_checkpoint(
            "architect",
            decision_type="DECISION",
            decision_text="Use REST over GraphQL because the team has more REST experience.",
        )
        assert "DEC-001" in result
        df = tmp_path / ".controlcoding" / "sessions" / "agents" / "architect" / "decisions.jsonl"
        assert df.exists()
        entry = json.loads(df.read_text(encoding="utf-8").strip())
        assert entry["id"] == "DEC-001"
        assert "REST over GraphQL" in entry["text"]

    def test_invalid_decision_type_warns(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("coder", "Build feature")
        result = m.agent_checkpoint(
            "coder",
            decision_type="BADTYPE",
            decision_text="Some decision.",
        )
        assert "WARNING" in result
        assert "Decision not recorded" in result

    def test_no_start_returns_error(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_checkpoint("coder", pending="Something")
        assert "ERROR" in result
        assert "agent_start" in result

    def test_stateless_agent_rejected(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_checkpoint("expert", pending="Something")
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# agent_close
# ---------------------------------------------------------------------------

class TestAgentClose:
    def test_writes_handoff_and_execution_in_task_folder(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design login flow")
        result = m.agent_close(
            "architect",
            status="context-exhausted",
            summary="Designed the login flow. Need to finish token expiry rules.",
            files_changed="dev/design/auth-design.md - initial draft",
            pending="Define token expiry",
            open_questions="Should refresh tokens be stored in DB?",
        )
        assert "handoff ceremony" in result
        agent_root = tmp_path / ".controlcoding" / "agents" / "architect"
        task_root = next((agent_root / "tasks").iterdir())
        handoffs = list((task_root / "handoffs").glob("*.md"))
        executions = list((task_root / "executions").glob("*.json"))
        worklogs = list((task_root / "worklog").glob("*.md"))
        assert handoffs
        assert executions
        assert worklogs
        handoff = handoffs[0].read_text(encoding="utf-8")
        assert "Define token expiry" in handoff
        assert "checkpoint/commit ceremony" in handoff

    def test_writes_log_md(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design login flow")
        result = m.agent_close(
            "architect",
            status="complete",
            summary="Designed the login flow. Decided to use JWT tokens.",
            files_changed="dev/design/auth-design.md - new file",
            pending="",
            open_questions="",
        )
        assert "closed" in result
        agents_dir = tmp_path / ".controlcoding" / "sessions" / "agents" / "architect"
        chats = list((agents_dir / "chats").iterdir())
        assert len(chats) == 1
        log_file = chats[0] / "log.md"
        assert log_file.exists()
        log_content = log_file.read_text(encoding="utf-8")
        assert "Design login flow" in log_content
        assert "JWT tokens" in log_content
        assert "**Status**: complete" in log_content

    def test_updates_state_phase(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("coder", "Implement feature")
        m.agent_close("coder", "context-exhausted", "Made progress on the feature.")
        state = json.loads(
            (tmp_path / ".controlcoding" / "sessions" / "agents" / "coder" / "state.json")
            .read_text(encoding="utf-8")
        )
        assert state["phase"] == "in_progress"  # context-exhausted = still in progress

    def test_complete_sets_phase_complete(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("reviewer", "Review sprint")
        m.agent_close("reviewer", "complete", "Reviewed all PRs.")
        state = json.loads(
            (tmp_path / ".controlcoding" / "sessions" / "agents" / "reviewer" / "state.json")
            .read_text(encoding="utf-8")
        )
        assert state["phase"] == "complete"

    def test_creates_index_md(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("debugger", "Debug crash on startup")
        m.agent_close("debugger", "complete", "Found null pointer in init().")
        index_file = tmp_path / ".controlcoding" / "sessions" / "agents" / "debugger" / "index.md"
        assert index_file.exists()
        index_content = index_file.read_text(encoding="utf-8")
        assert "Memory Index: debugger" in index_content
        assert "Chat Timeline" in index_content

    def test_pending_saved_to_state(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("coder", "Build payment form")
        m.agent_close(
            "coder", "interrupted", "Partially done.",
            pending="Add validation\nWrite tests",
        )
        state = json.loads(
            (tmp_path / ".controlcoding" / "sessions" / "agents" / "coder" / "state.json")
            .read_text(encoding="utf-8")
        )
        assert "Add validation" in state["pending"]

    def test_invalid_status_rejected(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Task")
        result = m.agent_close("architect", "done", "Summary.")
        assert "ERROR" in result
        assert "status must be one of" in result

    def test_no_start_returns_error(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_close("coder", "complete", "Summary.")
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# agent_resume
# ---------------------------------------------------------------------------

class TestAgentResume:
    def test_returns_brief_after_close(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design caching layer")
        m.agent_checkpoint(
            "architect",
            decision_type="DECISION",
            decision_text="Use Redis because it supports TTL natively.",
        )
        m.agent_close("architect", "complete", "Designed the caching layer.")
        brief = m.agent_resume("architect")
        assert "Resume Brief: architect" in brief
        assert "Design caching layer" in brief

    def test_includes_last_log(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("coder", "Write auth middleware")
        m.agent_close("coder", "context-exhausted", "Wrote 80% of the middleware.")
        brief = m.agent_resume("coder")
        assert "Wrote 80%" in brief

    def test_includes_active_decisions(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Task")
        m.agent_checkpoint(
            "architect",
            decision_type="CONSTRAINT",
            decision_text="Must use Python 3.13 because the production server is on 3.13.",
        )
        m.agent_close("architect", "complete", "Done.")
        brief = m.agent_resume("architect")
        assert "Python 3.13" in brief
        assert "Active constraints" in brief

    def test_stateless_rejected(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_resume("expert")
        assert "ERROR" in result
        assert "stateless" in result

    def test_no_memory_returns_message(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_resume("debugger")
        assert "no prior memory" in result.lower()


# ---------------------------------------------------------------------------
# agent_status
# ---------------------------------------------------------------------------

class TestAgentStatus:
    def test_all_agents_table(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("concierge", "Orchestrate sprint")
        result = m.agent_status()
        assert "Agent Status" in result
        assert "concierge" in result
        assert "architect" in result
        assert "coder" in result
        assert "Stateless agents" in result

    def test_single_agent_returns_state_json(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("reviewer", "Review PR #55")
        result = m.agent_status("reviewer")
        data = json.loads(result)
        assert data["agent"] == "reviewer"
        assert data["current_task"] == "Review PR #55"

    def test_no_memory_agent_shows_placeholder(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_status()
        assert "no memory" in result

    def test_unknown_agent_returns_error(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        result = m.agent_status("wizard")
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# cc agents
# ---------------------------------------------------------------------------

class TestCcAgents:
    def test_no_memory_shows_message(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "cc.py"), "agents",
             "--project-root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "No agent memory found" in result.stdout

    def test_shows_initialized_agents(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design the gateway")
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "cc.py"), "agents",
             "--project-root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "architect" in result.stdout
        assert "in_progress" in result.stdout


# ---------------------------------------------------------------------------
# cc resume --agent
# ---------------------------------------------------------------------------

class TestCcResumeAgent:
    def test_no_memory_returns_error(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "cc.py"), "resume",
             "--agent", "architect", "--brief",
             "--project-root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "No memory found" in result.stdout

    def test_brief_output(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("coder", "Build search feature")
        m.agent_close("coder", "complete", "Built the search feature.")
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "cc.py"), "resume",
             "--agent", "coder", "--brief",
             "--project-root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Resume Brief: coder" in result.stdout
        assert "Build search feature" in result.stdout


# ---------------------------------------------------------------------------
# Integration: full lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_architect_two_chat_sessions(self, tmp_path):
        """Simulate architect working across two context windows."""
        _make_project(tmp_path)
        m = _load_session(tmp_path)

        # Chat 1
        m.agent_start("architect", "Design authentication system")
        m.agent_checkpoint(
            "architect",
            decision_type="DECISION",
            decision_text=(
                "Use JWT over sessions because the system is stateless and "
                "tokens can be verified without a DB lookup."
            ),
        )
        m.agent_checkpoint("architect", pending="Define token expiry policy\nWrite ADR-001")
        m.agent_close(
            "architect",
            status="context-exhausted",
            summary="Designed the auth flow. Chose JWT. Still need token expiry policy.",
            files_changed="dev/design/auth-design.md - initial draft",
            pending="Define token expiry policy\nWrite ADR-001",
            open_questions="Should refresh tokens be stored in the DB?",
        )

        # Chat 2: start returns resume brief
        result = m.agent_start("architect", "Continue auth design - token expiry")
        assert "resumed" in result
        assert "RESUME BRIEF" in result
        assert "JWT" in result  # decision survived
        assert "Define token expiry policy" in result  # pending survived
        assert "refresh tokens" in result  # open question survived

        # Verify chat-002 created
        agents_dir = tmp_path / ".controlcoding" / "sessions" / "agents" / "architect"
        chats = sorted((agents_dir / "chats").iterdir())
        assert len(chats) == 2
        assert chats[1].name.startswith("chat-002-")

        # Verify index has both chats
        index = (agents_dir / "index.md").read_text(encoding="utf-8")
        assert "chat-001-" in index
        assert "context-exhausted" in index

    def test_same_task_reuses_task_folder_across_chat_sessions(self, tmp_path):
        _make_project(tmp_path)
        m = _load_session(tmp_path)

        m.agent_start("architect", "Design authentication system")
        state_before = json.loads(
            (tmp_path / ".controlcoding" / "agents" / "architect" / "state.json")
            .read_text(encoding="utf-8")
        )
        first_task_id = state_before["current_task_id"]
        m.agent_close(
            "architect",
            status="context-exhausted",
            summary="Designed the auth flow. Need token expiry policy.",
            pending="Define token expiry policy",
        )
        m.agent_start("architect", "Design authentication system")
        state_after = json.loads(
            (tmp_path / ".controlcoding" / "agents" / "architect" / "state.json")
            .read_text(encoding="utf-8")
        )
        assert state_after["current_task_id"] == first_task_id

    def test_concierge_global_state(self, tmp_path):
        """Concierge holds global project state across sessions."""
        _make_project(tmp_path)
        m = _load_session(tmp_path)

        m.agent_start("concierge", "Orchestrate UI-Wizard sprint W2")
        m.agent_checkpoint(
            "concierge",
            decision_type="PLAN",
            decision_text="Sprint W2 focus: WebSocket Gateway live + panel splitting. Coder starts Monday.",
        )
        m.agent_close(
            "concierge",
            status="complete",
            summary="Planned W2 sprint. Assigned tasks to Architect and Coder.",
        )

        brief = m.agent_resume("concierge")
        assert "Orchestrate UI-Wizard sprint W2" in brief
        assert "WebSocket Gateway" in brief


# ---------------------------------------------------------------------------
# Write isolation (SESSION_AGENT_NAME)
# ---------------------------------------------------------------------------

class TestWriteIsolation:
    """Verify that SESSION_AGENT_NAME blocks cross-agent writes."""

    def test_agent_start_blocked_wrong_agent(self, tmp_path):
        """agent_start blocked when SESSION_AGENT_NAME is set to a different agent."""
        _make_project(tmp_path)
        os.environ["SESSION_AGENT_NAME"] = "coder"
        try:
            m = _load_session(tmp_path)
            result = m.agent_start("architect", "Design something")
            assert "ERROR" in result
            assert "isolation" in result.lower()
            assert "coder" in result
            assert "architect" in result
        finally:
            del os.environ["SESSION_AGENT_NAME"]

    def test_agent_checkpoint_blocked_wrong_agent(self, tmp_path):
        """agent_checkpoint blocked when SESSION_AGENT_NAME is set to a different agent."""
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design something")

        os.environ["SESSION_AGENT_NAME"] = "coder"
        try:
            result = m.agent_checkpoint("architect", pending="task 1")
            assert "ERROR" in result
            assert "isolation" in result.lower()
        finally:
            del os.environ["SESSION_AGENT_NAME"]

    def test_agent_close_blocked_wrong_agent(self, tmp_path):
        """agent_close blocked when SESSION_AGENT_NAME is set to a different agent."""
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design something")

        os.environ["SESSION_AGENT_NAME"] = "reviewer"
        try:
            result = m.agent_close("architect", status="complete", summary="Done.")
            assert "ERROR" in result
            assert "isolation" in result.lower()
        finally:
            del os.environ["SESSION_AGENT_NAME"]

    def test_agent_start_allowed_matching_agent(self, tmp_path):
        """agent_start succeeds when SESSION_AGENT_NAME matches agent_name."""
        _make_project(tmp_path)
        os.environ["SESSION_AGENT_NAME"] = "architect"
        try:
            m = _load_session(tmp_path)
            result = m.agent_start("architect", "Design WebSocket Gateway")
            assert "ERROR" not in result
            assert "architect" in result
        finally:
            del os.environ["SESSION_AGENT_NAME"]

    def test_agent_start_allowed_no_env_var(self, tmp_path):
        """agent_start succeeds when SESSION_AGENT_NAME is not set (backward compat)."""
        _make_project(tmp_path)
        os.environ.pop("SESSION_AGENT_NAME", None)
        m = _load_session(tmp_path)
        result = m.agent_start("architect", "Design WebSocket Gateway")
        assert "ERROR" not in result

    def test_isolation_case_insensitive(self, tmp_path):
        """Write isolation comparison is case-insensitive."""
        _make_project(tmp_path)
        os.environ["SESSION_AGENT_NAME"] = "ARCHITECT"
        try:
            m = _load_session(tmp_path)
            result = m.agent_start("architect", "Design WebSocket Gateway")
            assert "ERROR" not in result
        finally:
            del os.environ["SESSION_AGENT_NAME"]

    def test_read_ops_unaffected_by_isolation(self, tmp_path):
        """agent_resume and agent_status are read-only; SESSION_AGENT_NAME does not block them."""
        _make_project(tmp_path)
        m = _load_session(tmp_path)
        m.agent_start("architect", "Design something")
        m.agent_close("architect", status="complete", summary="Done.")

        os.environ["SESSION_AGENT_NAME"] = "coder"
        try:
            # Concierge (locked to coder) should still read architect's memory
            brief = m.agent_resume("architect")
            assert "ERROR" not in brief
            status = m.agent_status("architect")
            assert "ERROR" not in status
        finally:
            del os.environ["SESSION_AGENT_NAME"]
