"""Tests for concierge_agent.py - ConciergeAgent (Sprint S2-S3)."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, BackendAdapter, ToolExecutor, ReportBuilder,
    ToolCall, ToolResult,
)
from concierge_agent import (
    ConciergeAgentState, ConciergeAgent,
    _parse_markdown_sections, _build_concierge_prompt,
    VALID_TRANSITIONS, ALL_PHASES, build_concierge_report,
)


# ===========================================================================
# _parse_markdown_sections
# ===========================================================================

class TestParseMarkdownSections:
    def test_basic_sections(self):
        md = "# Title\nIntro\n## Foo\nfoo body\n## Bar\nbar body"
        sections = _parse_markdown_sections(md)
        assert "Foo" in sections
        assert "Bar" in sections
        assert "foo body" in sections["Foo"]

    def test_strips_enforcement_markers(self):
        md = "## Protected Zones [hook-enforced]\nzones here"
        sections = _parse_markdown_sections(md)
        assert "Protected Zones" in sections
        assert "zones here" in sections["Protected Zones"]

    def test_advisory_marker(self):
        md = "## Commit Ceremony [advisory]\nceremony rules"
        sections = _parse_markdown_sections(md)
        assert "Commit Ceremony" in sections

    def test_empty_content(self):
        sections = _parse_markdown_sections("")
        assert sections == {}

    def test_no_h2_headers(self):
        md = "# Title\nSome text\n### H3 only\nmore text"
        sections = _parse_markdown_sections(md)
        assert sections == {}


# ===========================================================================
# ConciergeAgentState
# ===========================================================================

class TestConciergeAgentState:
    def test_defaults(self):
        s = ConciergeAgentState()
        assert s.agent_type == "concierge"
        assert s.phase == "IDLE"
        assert s.project_root == ""
        assert s.project_description == ""
        assert s.plan == {}
        assert s.decisions == []
        assert s.coder_instructions == []
        assert s.agent_reports == []
        assert s.criteria == []

    def test_phase_history_tracking(self):
        s = ConciergeAgentState()
        s.phase_history.append({
            "from": "IDLE", "to": "PLANNING",
            "reason": "user requested", "step": 1,
        })
        assert len(s.phase_history) == 1
        assert s.phase_history[0]["to"] == "PLANNING"

    def test_decisions_logging(self):
        s = ConciergeAgentState()
        s.decisions.append({
            "step": 5, "phase": "ARCH_REVIEW",
            "decision": "Use repository pattern",
            "reasoning": "Better testability",
        })
        assert len(s.decisions) == 1
        assert s.decisions[0]["decision"] == "Use repository pattern"

    def test_to_context_summary_basic(self):
        s = ConciergeAgentState(phase="CODING", current_step=15)
        s.decisions.append({
            "step": 10, "decision": "Use factory pattern"})
        s.agent_reports.append({
            "agent": "reviewer", "summary": "Code looks good"})
        s.phase_history.append({
            "from": "PLANNING", "to": "CODING", "reason": "plan ready"})

        summary = s.to_context_summary()
        assert "CODING" in summary
        assert "step 15" in summary
        assert "Use factory pattern" in summary
        assert "reviewer" in summary
        assert "PLANNING" in summary

    def test_to_context_summary_empty_state(self):
        s = ConciergeAgentState()
        summary = s.to_context_summary()
        assert "CONCIERGE STATE SUMMARY" in summary
        assert "IDLE" in summary

    def test_save_and_load(self, tmp_path):
        s = ConciergeAgentState(
            session_id="abc",
            project_root="/test/project",
            phase="CODING",
            current_step=20,
        )
        s.decisions.append({
            "step": 5, "decision": "Use REST API",
            "reasoning": "Simpler"})
        s.plan = {"phases": [{"name": "phase1", "status": "done"}]}

        path = str(tmp_path / "state.json")
        s.save(path)

        loaded = ConciergeAgentState.load(path)
        assert loaded.session_id == "abc"
        assert loaded.phase == "CODING"
        assert loaded.current_step == 20
        assert loaded.project_root == "/test/project"
        assert len(loaded.decisions) == 1
        assert loaded.plan["phases"][0]["name"] == "phase1"


# ===========================================================================
# Phase transitions
# ===========================================================================

class TestPhaseTransitions:
    def test_valid_transitions_complete(self):
        """Every phase in ALL_PHASES has an entry in VALID_TRANSITIONS
        or is a terminal phase (DONE)."""
        for phase in ALL_PHASES:
            if phase != "DONE":
                assert phase in VALID_TRANSITIONS, \
                    f"{phase} missing from VALID_TRANSITIONS"

    def test_idle_to_planning(self):
        assert "PLANNING" in VALID_TRANSITIONS["IDLE"]

    def test_planning_to_arch_or_coding(self):
        assert "ARCH_REVIEW" in VALID_TRANSITIONS["PLANNING"]
        assert "CODING" in VALID_TRANSITIONS["PLANNING"]

    def test_reviewing_to_done(self):
        assert "DONE" in VALID_TRANSITIONS["REVIEWING"]


# ===========================================================================
# ConciergeAgent - S2 Core
# ===========================================================================

class TestConciergeAgentInit:
    def test_creates_concierge_state(self):
        agent = ConciergeAgent(backend="ollama")
        assert isinstance(agent.state, ConciergeAgentState)
        assert agent.state.agent_type == "concierge"

    def test_default_mode_interactive(self):
        agent = ConciergeAgent(backend="ollama")
        assert agent.mode == "interactive"


class TestContextLoading:
    def test_loads_claude_md(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n"
            "## Project Identity\n\n"
            "- **Name**: TestProject\n"
            "- **Stack**: Python\n\n"
            "## Architecture Rules\n\n"
            "1. No external deps\n"
            "2. Pure functions\n\n"
            "## Module Boundaries\n\n"
            "### Stable\n- src/core/\n\n"
            "## Domain Invariants\n\n"
            "1. All IDs are UUIDs\n",
            encoding="utf-8",
        )

        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent._load_project_context()

        assert "TestProject" in agent.state.project_description
        assert "No external deps" in agent.state.architecture_rules
        assert "src/core/" in agent.state.module_boundaries
        assert "UUIDs" in agent.state.domain_invariants

    def test_no_claude_md_graceful(self, tmp_path):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent._load_project_context()
        assert agent.state.project_description == ""

    def test_loads_plan(self, tmp_path):
        plan_dir = tmp_path / "devlog" / "plans"
        plan_dir.mkdir(parents=True)
        plan = {"status": "APPROVED", "phases": [
            {"name": "Phase 1", "status": "pending"}]}
        (plan_dir / "plan.current.json").write_text(
            json.dumps(plan), encoding="utf-8")

        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent._load_plan()

        assert len(agent.state.plan.get("phases", [])) == 1
        assert agent.state.plan["phases"][0]["name"] == "Phase 1"

    def test_loads_criteria(self, tmp_path):
        criteria_dir = tmp_path / "devlog" / "criteria"
        criteria_dir.mkdir(parents=True)
        criteria = [{"id": "C-01", "name": "Login works", "status": "pending"}]
        (criteria_dir / "criteria.json").write_text(
            json.dumps(criteria), encoding="utf-8")

        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent._load_criteria()

        assert len(agent.state.criteria) == 1
        assert agent.state.criteria[0]["id"] == "C-01"


class TestSystemPrompt:
    def test_includes_tool_descriptions(self):
        agent = ConciergeAgent(backend="ollama")
        agent._register_tools()
        prompt = agent._build_system_prompt()
        assert "transition_phase" in prompt
        assert "instruct_coder" in prompt
        assert "done" in prompt

    def test_includes_project_context(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.project_description = "A test project"
        agent.state.architecture_rules = "No external deps"
        agent._register_tools()
        prompt = agent._build_system_prompt()
        assert "A test project" in prompt
        assert "No external deps" in prompt

    def test_includes_plan_when_present(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.plan = {"phases": [{"name": "Phase 1"}]}
        agent._register_tools()
        prompt = agent._build_system_prompt()
        assert "Phase 1" in prompt

    def test_shows_no_plan_message(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.plan = {}
        agent._register_tools()
        prompt = agent._build_system_prompt()
        assert "No plan yet" in prompt


# ===========================================================================
# S2 Tools: Phase management
# ===========================================================================

class TestTransitionPhaseTool:
    def test_valid_transition(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.phase = "IDLE"
        result = agent._tool_transition_phase("PLANNING", "user requested")
        assert "IDLE -> PLANNING" in result
        assert agent.state.phase == "PLANNING"
        assert len(agent.state.phase_history) == 1
        assert agent.state.phase_history[0]["reason"] == "user requested"

    def test_invalid_transition(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.phase = "IDLE"
        result = agent._tool_transition_phase("CODING")
        assert "Error" in result
        assert agent.state.phase == "IDLE"  # unchanged

    def test_unknown_phase(self):
        agent = ConciergeAgent(backend="ollama")
        result = agent._tool_transition_phase("FANTASY_PHASE")
        assert "Error" in result
        assert "Unknown phase" in result

    def test_case_insensitive(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.phase = "IDLE"
        result = agent._tool_transition_phase("planning")
        assert agent.state.phase == "PLANNING"


class TestLogDecisionTool:
    def test_logs_decision(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.phase = "ARCH_REVIEW"
        agent.state.current_step = 10
        result = agent._tool_log_decision(
            "Use repository pattern", "Better testability")
        assert "Decision logged" in result
        assert len(agent.state.decisions) == 1
        assert agent.state.decisions[0]["phase"] == "ARCH_REVIEW"
        assert agent.state.decisions[0]["step"] == 10
        assert agent.state.decisions[0]["reasoning"] == "Better testability"


class TestAskUserTool:
    def test_autonomous_mode_auto_answers(self):
        agent = ConciergeAgent(backend="ollama", mode="autonomous")
        result = agent._tool_ask_user("Should I continue?")
        assert "auto-answer" in result
        assert "y" in result

    def test_interactive_mode_prompts(self):
        agent = ConciergeAgent(backend="ollama", mode="interactive")
        with patch("builtins.input", return_value="yes"):
            result = agent._tool_ask_user("Continue?")
        assert result == "yes"

    def test_interactive_empty_answer(self):
        agent = ConciergeAgent(backend="ollama", mode="interactive")
        with patch("builtins.input", return_value=""):
            result = agent._tool_ask_user("Continue?")
        assert result == "[no answer]"


# ===========================================================================
# S3 Tools: Agent coordination
# ===========================================================================

class TestInstructCoderTool:
    def test_format(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.current_step = 5
        result = agent._tool_instruct_coder(
            task="Implement login form",
            constraints="No external auth libraries",
            files="src/login.py, src/auth.py",
            guidance="Use bcrypt for password hashing",
        )
        assert "Implement login form" in result
        assert "No external auth libraries" in result
        assert "src/login.py" in result
        assert "bcrypt" in result

    def test_recorded_in_state(self):
        agent = ConciergeAgent(backend="ollama")
        agent._tool_instruct_coder(task="Build API")
        assert len(agent.state.coder_instructions) == 1
        assert agent.state.coder_instructions[0]["task"] == "Build API"


class TestCallConsultantTool:
    def test_invalid_role(self):
        agent = ConciergeAgent(backend="ollama")
        result = agent._tool_call_consultant("wizard", "Help me")
        assert "Error" in result
        assert "Unknown role" in result

    def test_degraded_mode_no_concierge(self, tmp_path, monkeypatch):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent.state.project_root = str(tmp_path)
        agent._prompt_generator = None
        agent._agent_caller = None
        monkeypatch.setattr(
            agent, "consult",
            MagicMock(side_effect=RuntimeError("consultation unavailable")),
        )
        result = agent._tool_call_consultant("architect", "Review design")
        assert "Consultant call" in result
        assert "architect" in result
        assert len(agent.state.agent_reports) == 1

    def test_records_report(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_engagement.json").write_text(
            json.dumps({
                "tier": "agents",
                "specialist_paths": [{
                    "role_id": "consultant_1",
                    "label": "Reviewer Consultant",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "anthropic_prod",
                    "model": "claude-sonnet",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }],
            }),
            encoding="utf-8",
        )
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent.state.project_root = str(tmp_path)
        agent._prompt_generator = None
        agent._agent_caller = None
        agent._tool_call_consultant("reviewer", "Check code quality")
        report = agent.state.agent_reports[0]
        assert report["role"] == "reviewer"
        assert "Check code quality" in report["question"]
        event_log = tmp_path / ".controlcoding" / "event_log.jsonl"
        assert not event_log.exists()


class TestRunTandemTool:
    def test_degraded_mode(self, tmp_path, monkeypatch):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent.state.project_root = str(tmp_path)
        agent._agent_caller = None
        monkeypatch.setattr(
            agent, "consult",
            MagicMock(side_effect=RuntimeError("consultation unavailable")),
        )
        result = agent._tool_run_tandem("Should we use microservices?")
        assert "not available" in result or "skipped" in result
        assert len(agent.state.agent_reports) == 1

    def test_with_mock_caller(self, tmp_path, monkeypatch):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent.state.project_root = str(tmp_path)
        monkeypatch.setattr(
            agent, "consult",
            MagicMock(side_effect=RuntimeError("consultation unavailable")),
        )
        mock_caller = MagicMock()
        mock_caller.call_tandem.return_value = {
            "agreements": ["Use REST"], "divergences": []}
        agent._agent_caller = mock_caller
        result = agent._tool_run_tandem("API design", role="architect")
        mock_caller.call_tandem.assert_called_once()
        assert "REST" in result


class TestSpawnVisualAgentTool:
    def test_spawns_visual_agent(self):
        """spawn_visual_agent creates a VisualAgent and runs it."""
        agent = ConciergeAgent(backend="ollama")
        mock_report = {
            "stop_reason": "done",
            "criteria_summary": {"pass": 1, "fail": 0, "pending": 0},
            "screenshots_count": 3,
        }
        with patch.dict("sys.modules", {
            "visual_agent": MagicMock(
                VisualAgent=MagicMock(return_value=MagicMock(
                    run=MagicMock(return_value=mock_report),
                    state=MagicMock(timeout_seconds=300),
                )),
                build_visual_report=MagicMock(),
            ),
        }):
            result = agent._tool_spawn_visual_agent(exe="app.exe")
        assert "Pass: 1" in result
        assert "complete" in result.lower()


# ===========================================================================
# S3 Tools: Project inspection
# ===========================================================================

class TestReadFileTool:
    def test_reads_file(self, tmp_path):
        (tmp_path / "test.txt").write_text("hello world", encoding="utf-8")
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        result = agent._tool_read_file("test.txt")
        assert "hello world" in result

    def test_path_traversal_blocked(self, tmp_path):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        result = agent._tool_read_file("../../etc/passwd")
        assert "Error" in result
        assert "outside" in result

    def test_file_not_found(self, tmp_path):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        result = agent._tool_read_file("nonexistent.txt")
        assert "Error" in result
        assert "not found" in result

    def test_truncates_long_files(self, tmp_path):
        long_file = tmp_path / "long.txt"
        long_file.write_text(
            "\n".join(f"Line {i}" for i in range(500)),
            encoding="utf-8",
        )
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        result = agent._tool_read_file("long.txt", max_lines=10)
        assert "truncated" in result

    def test_no_project_root(self):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = None
        result = agent._tool_read_file("test.txt")
        assert "Error" in result


class TestRunCommandTool:
    def test_runs_command(self, tmp_path):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        result = agent._tool_run_command("echo hello")
        assert "hello" in result

    def test_timeout(self, tmp_path):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        # Use a command that takes a while
        if sys.platform == "win32":
            cmd = "ping -n 5 127.0.0.1"
        else:
            cmd = "sleep 5"
        result = agent._tool_run_command(cmd, timeout=1)
        assert "timed out" in result

    def test_no_project_root(self):
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = None
        result = agent._tool_run_command("echo test")
        assert "Error" in result


# ===========================================================================
# S3 Tools: Plan management
# ===========================================================================

class TestUpdatePlanTool:
    def test_set_plan(self):
        agent = ConciergeAgent(backend="ollama")
        plan = {"phases": [{"name": "Setup", "status": "pending"}]}
        result = agent._tool_update_plan("set", json.dumps(plan))
        assert "replaced" in result
        assert agent.state.plan["phases"][0]["name"] == "Setup"

    def test_set_invalid_json(self):
        agent = ConciergeAgent(backend="ollama")
        result = agent._tool_update_plan("set", "not json")
        assert "Error" in result

    def test_mark_phase_done(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.plan = {"phases": [
            {"name": "Phase 1", "status": "pending"},
            {"name": "Phase 2", "status": "pending"},
        ]}
        result = agent._tool_update_plan("mark_phase_done")
        assert "Phase 1" in result
        assert agent.state.plan["phases"][0]["status"] == "done"
        assert agent.state.plan["phases"][1]["status"] == "pending"

    def test_add_note(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.plan = {}
        result = agent._tool_update_plan("add_note", "Remember to test edge cases")
        assert "Note added" in result
        assert len(agent.state.plan["notes"]) == 1

    def test_add_phase(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.plan = {"phases": []}
        result = agent._tool_update_plan(
            "add_phase",
            json.dumps({"name": "Testing", "status": "pending"}))
        assert "Testing" in result
        assert len(agent.state.plan["phases"]) == 1

    def test_add_phase_string(self):
        agent = ConciergeAgent(backend="ollama")
        agent.state.plan = {"phases": []}
        result = agent._tool_update_plan("add_phase", "Documentation")
        assert "Documentation" in result

    def test_unknown_action(self):
        agent = ConciergeAgent(backend="ollama")
        result = agent._tool_update_plan("delete_everything")
        assert "Error" in result


# ===========================================================================
# S3 Tools: Session
# ===========================================================================

class TestDoneTool:
    def test_sets_flag(self):
        agent = ConciergeAgent(backend="ollama")
        result = agent._tool_done("All tasks completed")
        assert agent._done_flag is True
        assert agent.state.stop_reason == "done"
        assert "complete" in result.lower()

    def test_logs_finding(self):
        agent = ConciergeAgent(backend="ollama")
        agent._tool_done("Finished inventory system")
        assert len(agent.state.findings) == 1
        assert "Finished inventory system" in agent.state.findings[0].description


# ===========================================================================
# Report
# ===========================================================================

class TestConciergeReport:
    def test_includes_concierge_fields(self):
        s = ConciergeAgentState(
            session_id="x", project_root="/test",
            phase="DONE", stop_reason="done",
        )
        s.decisions.append({"step": 1, "decision": "test"})
        s.phase_history.append({"from": "IDLE", "to": "PLANNING"})
        s.coder_instructions.append({"task": "build"})
        s.agent_reports.append({"agent": "reviewer"})

        report = build_concierge_report(s)
        assert report["project_root"] == "/test"
        assert len(report["phase_history"]) == 1
        assert len(report["decisions"]) == 1
        assert report["coder_instructions_count"] == 1
        assert report["agent_reports_count"] == 1


# ===========================================================================
# Integration: start -> tools -> finish
# ===========================================================================

class TestConciergeAgentIntegration:
    def test_start_with_project(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Project Identity\n\n- **Name**: IntegTest\n\n"
            "## Architecture Rules\n\n1. Pure functions\n",
            encoding="utf-8",
        )

        agent = ConciergeAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I see the project"):
            result = agent.start(
                project_root=str(tmp_path),
                task="Build the API",
            )
        assert "IntegTest" in agent.state.project_description
        assert agent.state.phase == "IDLE"

    def test_full_run_with_done(self, tmp_path):
        """Simulate: start -> LLM transitions to PLANNING -> LLM calls done."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Project Identity\n\n- **Name**: RunTest\n",
            encoding="utf-8",
        )

        agent = ConciergeAgent(backend="ollama", mode="autonomous")
        responses = [
            # start() call
            "I'll begin planning.",
            # agent_loop: LLM calls transition_phase
            '```json\n{"tool": "transition_phase", "params": {"to_phase": "PLANNING", "reason": "starting"}}\n```',
            # agent_loop: LLM calls log_decision
            '```json\n{"tool": "log_decision", "params": {"decision": "Simple API", "reasoning": "fast"}}\n```',
            # agent_loop: LLM calls done
            '```json\n{"tool": "done", "params": {"summary": "Planning complete"}}\n```',
        ]
        idx = [0]

        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp

        with patch.object(agent.backend_adapter, "call",
                          side_effect=mock_call):
            report = agent.run(
                project_root=str(tmp_path),
                task="Build API",
            )

        assert report["phase"] == "done"
        assert report["stop_reason"] == "done"
        assert agent.state.phase_history[0]["to"] == "PLANNING"
        assert len(agent.state.decisions) == 1
        assert agent.state.decisions[0]["decision"] == "Simple API"


# ===========================================================================
# GPT-R1: Persistent decision log
# ===========================================================================

class TestDecisionLogPersistence:
    def test_decision_persists_to_jsonl(self, tmp_path):
        """log_decision writes to .controlcoding/concierge_decisions.jsonl."""
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent.state.session_id = "test-123"
        agent.state.current_step = 5
        agent.state.phase = "PLANNING"
        agent._register_tools()

        agent._tool_log_decision("Use microservices", "Better scaling")

        log_path = tmp_path / ".controlcoding" / "concierge_decisions.jsonl"
        assert log_path.exists()
        line = log_path.read_text(encoding="utf-8").strip()
        data = json.loads(line)
        assert data["decision"] == "Use microservices"
        assert data["reasoning"] == "Better scaling"
        assert data["session_id"] == "test-123"
        assert data["phase"] == "PLANNING"

    def test_decision_log_appends(self, tmp_path):
        """Multiple decisions append to the same file."""
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = tmp_path
        agent.state.session_id = "s1"
        agent._register_tools()

        agent._tool_log_decision("Decision A", "Reason A")
        agent._tool_log_decision("Decision B", "Reason B")

        log_path = tmp_path / ".controlcoding" / "concierge_decisions.jsonl"
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["decision"] == "Decision A"
        assert json.loads(lines[1])["decision"] == "Decision B"

    def test_decision_log_survives_no_project_root(self):
        """No crash if project_root not set."""
        agent = ConciergeAgent(backend="ollama")
        agent._project_root = None
        agent._register_tools()
        # Should not crash, just skip persistence
        result = agent._tool_log_decision("Test", "")
        assert "logged" in result.lower()
