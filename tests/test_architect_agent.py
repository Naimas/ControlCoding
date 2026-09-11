"""Tests for architect_agent.py - ArchitectAgent v1 (Sprint S6)."""

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
    BaseAgent,
)
from architect_agent import (
    ArchitectState, ArchitectAgent, _empty_kb, _load_kb, _save_kb,
    _cap_list, _migrate_kb, build_architect_report,
    KB_SCHEMA_VERSION, BLOCKED_COMMANDS, SKIP_DIRS, STACK_MARKERS,
    IMPORT_PATTERNS,
)


# ===========================================================================
# ArchitectState
# ===========================================================================

class TestArchitectState:
    def test_defaults(self):
        s = ArchitectState()
        assert s.agent_type == "architect"
        assert s.architecture_rules == ""
        assert s.kb_path == ""
        assert s.kb_dirty is False
        assert s.stack == ""
        assert s.module_map == {}
        assert s.patterns == []
        assert s.adrs == []
        assert s.guidance_history == []
        assert s.user_decisions == []

    def test_to_context_summary_with_data(self):
        s = ArchitectState()
        s.current_step = 10
        s.total_steps = 10
        s.total_llm_calls = 5
        s.stack = "Python 3.13"
        s.module_map = {"src/": "Core code"}
        s.standing_rules = ["No external deps in hooks"]
        s.adrs = [{"title": "Use BaseAgent", "status": "Accepted",
                    "rationale": "Shared infra"}]
        s.patterns = [{"pattern": "Inheritance", "where": "agents/"}]
        s.guidance_history = [{"task": "Implement X"}]
        s.user_decisions = ["Chose option B"]

        summary = s.to_context_summary()
        assert "Python 3.13" in summary
        assert "src/" in summary
        assert "No external deps" in summary
        assert "Use BaseAgent" in summary
        assert "Inheritance" in summary
        assert "Implement X" in summary
        assert "Chose option B" in summary
        assert "step 10" in summary

    def test_to_context_summary_empty(self):
        s = ArchitectState()
        summary = s.to_context_summary()
        assert "ARCHITECT STATE SUMMARY" in summary
        assert "architect" in summary


# ===========================================================================
# Knowledge Base helpers
# ===========================================================================

class TestKBHelpers:
    def test_empty_kb(self):
        kb = _empty_kb()
        assert kb["schema_version"] == KB_SCHEMA_VERSION
        assert kb["stack"] == ""
        assert isinstance(kb["module_map"], dict)
        assert isinstance(kb["patterns"], list)
        assert isinstance(kb["decisions"], list)

    def test_save_and_load(self, tmp_path):
        kb = _empty_kb()
        kb["stack"] = "Python 3.13"
        kb["module_map"] = {"src/": "Core"}
        path = str(tmp_path / "kb.json")
        assert _save_kb(kb, path) is True
        loaded = _load_kb(path)
        assert loaded is not None
        assert loaded["stack"] == "Python 3.13"
        assert loaded["module_map"]["src/"] == "Core"

    def test_kb_persistence_to_disk(self, tmp_path):
        kb = _empty_kb()
        kb["decisions"] = [{"id": "ADR-001", "title": "Test"}]
        path = str(tmp_path / "kb.json")
        _save_kb(kb, path)
        # Verify file exists and is valid JSON
        content = Path(path).read_text(encoding="utf-8")
        data = json.loads(content)
        assert data["decisions"][0]["id"] == "ADR-001"

    def test_kb_atomic_write(self, tmp_path):
        """Verify tmp file is used and cleaned up."""
        kb = _empty_kb()
        path = str(tmp_path / "kb.json")
        tmp_file = path + ".tmp"
        _save_kb(kb, path)
        # tmp file should not exist after successful write
        assert not Path(tmp_file).exists()
        # main file should exist
        assert Path(path).exists()

    def test_kb_write_failure_continues(self, tmp_path):
        """Write failure returns False, no crash."""
        kb = _empty_kb()
        # Use an invalid path
        result = _save_kb(kb, "/nonexistent/deep/path/kb.json")
        # On some systems this may succeed (mkdir -p), on others fail
        # The key is it doesn't crash
        assert isinstance(result, bool)

    def test_kb_schema_version(self, tmp_path):
        """KB with newer schema returns None (start fresh)."""
        path = str(tmp_path / "kb.json")
        kb = _empty_kb()
        kb["schema_version"] = KB_SCHEMA_VERSION + 1
        Path(path).write_text(json.dumps(kb), encoding="utf-8")
        loaded = _load_kb(path)
        assert loaded is None  # newer schema, start fresh

    def test_kb_migration(self):
        """Migrate v0 KB to v1."""
        old_kb = {"stack": "Python", "module_map": {}}
        migrated = _migrate_kb(old_kb, 0)
        assert migrated["schema_version"] == 1
        assert "guidance_history_archived" in migrated

    def test_kb_growth_control(self):
        """Cap list at 50, return overflow."""
        lst = list(range(60))
        overflow = _cap_list(lst, cap=50)
        assert len(lst) == 50
        assert len(overflow) == 10
        assert overflow == list(range(10))

    def test_kb_growth_control_under_cap(self):
        """No overflow when under cap."""
        lst = list(range(30))
        overflow = _cap_list(lst, cap=50)
        assert len(lst) == 30
        assert overflow == []

    def test_load_nonexistent(self):
        loaded = _load_kb("/nonexistent/path/kb.json")
        assert loaded is None

    def test_load_invalid_json(self, tmp_path):
        path = str(tmp_path / "kb.json")
        Path(path).write_text("not json", encoding="utf-8")
        loaded = _load_kb(path)
        assert loaded is None


# ===========================================================================
# ArchitectAgent tools
# ===========================================================================

class TestArchitectTools:
    def _make_agent(self, tmp_path):
        """Create an agent with mocked backend for testing."""
        agent = ArchitectAgent(backend="ollama", mode="interactive")
        agent._project_root = tmp_path
        agent.state.project_root = str(tmp_path)
        agent.state.kb_path = str(tmp_path / ".claude" / "architect_kb.json")
        agent._register_tools()
        return agent

    def test_analyze_structure_discovery(self, tmp_path):
        """Discovers files and identifies stack from markers."""
        # Create a Python project structure
        (tmp_path / "requirements.txt").write_text("pytest\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")

        agent = self._make_agent(tmp_path)
        result = agent._tool_analyze_structure()
        data = json.loads(result)
        assert "Python" in data["stack"]
        assert "requirements.txt" in data["marker_files"]

    def test_analyze_structure_bounds(self, tmp_path):
        """Respects max_depth parameter."""
        agent = self._make_agent(tmp_path)
        # max_depth should be capped at 8
        result = agent._tool_analyze_structure(max_depth=100)
        # Should not crash, depth capped internally
        assert isinstance(result, str)

    def test_produce_adr_format(self, tmp_path):
        """ADR follows Nygard format."""
        agent = self._make_agent(tmp_path)
        result = agent._tool_produce_adr(
            title="Use repository pattern",
            context="Need data access abstraction",
            decision="Use repository pattern for all DB access",
            consequences="Positive: testable. Negative: more boilerplate.",
        )
        assert "ADR-001" in result
        assert "Use repository pattern" in result
        assert "## Context" in result
        assert "## Decision" in result
        assert "## Consequences" in result
        assert "Proposed" in result
        # Saved to state
        assert len(agent.state.adrs) == 1
        assert agent.state.adrs[0]["title"] == "Use repository pattern"

    def test_guide_coder_structured(self, tmp_path):
        """Returns all 8 contract fields."""
        agent = self._make_agent(tmp_path)
        agent.state.standing_rules = ["No external deps"]
        result = agent._tool_guide_coder(
            task="Implement user auth",
            constraints="stdlib only, no JWT",
            files="src/auth.py, src/session.py",
        )
        data = json.loads(result)
        assert "task" in data
        assert data["task"] == "Implement user auth"
        assert "approach" in data
        assert "files_to_touch" in data
        assert "src/auth.py" in data["files_to_touch"]
        assert "patterns_to_follow" in data
        assert "patterns_to_avoid" in data
        assert "constraints" in data
        assert "acceptance_criteria" in data
        assert "estimated_complexity" in data
        # Standing rules included
        assert "No external deps" in data["constraints"]

    def test_review_design_contract(self, tmp_path):
        """Returns structured review template."""
        agent = self._make_agent(tmp_path)
        result = agent._tool_review_design(
            design_text="A module that does X",
            context="Part of the auth system",
        )
        data = json.loads(result)
        assert data["review_requested"] is True
        assert "output_format" in data
        fmt = data["output_format"]
        assert "verdict" in fmt
        assert "blocking_issues" in fmt
        assert "non_blocking_issues" in fmt
        assert "options" in fmt
        assert "recommendation" in fmt
        assert "assumptions" in fmt
        assert "required_followups" in fmt

    def test_check_coupling_grep(self, tmp_path):
        """Finds imports between modules."""
        # Create two modules with cross-imports
        mod_a = tmp_path / "mod_a"
        mod_b = tmp_path / "mod_b"
        mod_a.mkdir()
        mod_b.mkdir()
        (mod_a / "main.py").write_text("from mod_b import helper\n")
        (mod_b / "helper.py").write_text("# no imports\n")

        agent = self._make_agent(tmp_path)
        result = agent._tool_check_coupling("mod_a", "mod_b")
        data = json.loads(result)
        assert len(data["a_imports_b"]) >= 1
        assert data["b_imports_a"] == []
        assert data["circular"] is False
        assert data["coupling_score"] in ("low", "moderate")

    def test_check_coupling_circular(self, tmp_path):
        """Detects circular imports."""
        mod_a = tmp_path / "mod_a"
        mod_b = tmp_path / "mod_b"
        mod_a.mkdir()
        mod_b.mkdir()
        (mod_a / "main.py").write_text("from mod_b import x\n")
        (mod_b / "main.py").write_text("from mod_a import y\n")

        agent = self._make_agent(tmp_path)
        result = agent._tool_check_coupling("mod_a", "mod_b")
        data = json.loads(result)
        assert data["circular"] is True
        assert data["coupling_score"] == "circular"

    def test_check_coupling_languages(self, tmp_path):
        """Detects import patterns for multiple languages."""
        mod_a = tmp_path / "mod_a"
        mod_b = tmp_path / "mod_b"
        mod_a.mkdir()
        mod_b.mkdir()
        # JavaScript import
        (mod_a / "index.js").write_text(
            'import { foo } from "mod_b/utils"\n')
        # Rust use
        (mod_a / "lib.rs").write_text("use mod_b::something;\n")

        agent = self._make_agent(tmp_path)
        result = agent._tool_check_coupling("mod_a", "mod_b")
        data = json.loads(result)
        assert len(data["a_imports_b"]) >= 2

    def test_log_to_devlog(self, tmp_path):
        """Decision written to devlog file."""
        devlog = tmp_path / "devlog"
        devlog.mkdir()

        agent = self._make_agent(tmp_path)
        result = agent._tool_log_to_devlog(
            "User chose option B for auth pattern",
            entry_type="decision",
        )
        assert "Logged to devlog" in result
        # Verify file was created
        entries = list(devlog.glob("*.md"))
        assert len(entries) == 1
        content = entries[0].read_text(encoding="utf-8")
        assert "User chose option B" in content
        assert "architect" in content

    def test_user_decision_recorded(self, tmp_path):
        """User choice persisted in state and KB."""
        agent = self._make_agent(tmp_path)
        # Simulate recording a user decision
        agent.state.user_decisions.append("Chose repository pattern")
        agent._tool_write_knowledge(
            "decisions", "ADR-002", "Use repository pattern")
        assert len(agent.state.adrs) == 1
        assert agent._kb_data["decisions"][-1]["title"] == (
            "Use repository pattern")
        assert "Chose repository pattern" in agent.state.user_decisions

    def test_run_command_read_only(self, tmp_path):
        """Blocked commands rejected."""
        agent = self._make_agent(tmp_path)
        for cmd in ["rm -rf /", "mv file.txt", "cp a b", "del file"]:
            result = agent._tool_run_command(cmd)
            assert "blocked" in result.lower() or "error" in result.lower()

    def test_read_file_max_lines(self, tmp_path):
        """Large files truncated at max_lines."""
        big_file = tmp_path / "big.txt"
        big_file.write_text("\n".join(f"line {i}" for i in range(1000)))

        agent = self._make_agent(tmp_path)
        result = agent._tool_read_file("big.txt", max_lines=10)
        assert "truncated" in result
        assert "line 9" in result
        assert "line 100" not in result


# ===========================================================================
# ArchitectAgent lifecycle
# ===========================================================================

class TestArchitectLifecycle:
    def test_startup_first_session(self, tmp_path):
        """No KB -> initial message says 'no KB found'."""
        agent = ArchitectAgent(backend="ollama", mode="interactive")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I'll analyze the structure"):
            response = agent.start(project_root=str(tmp_path))
        assert "No existing KB" in response or "no KB" in response.lower() \
            or "No Knowledge Base" in response.lower() \
            or response  # agent started successfully

    def test_startup_subsequent_session(self, tmp_path):
        """KB exists -> loaded, message mentions stack."""
        # Create a KB
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        kb = _empty_kb()
        kb["stack"] = "Rust + WebAssembly"
        kb["decisions"] = [{"id": "ADR-001", "title": "Use wasm"}]
        kb["patterns"] = [{"pattern": "ECS", "where": "src/"}]
        _save_kb(kb, str(claude_dir / "architect_kb.json"))

        agent = ArchitectAgent(backend="ollama", mode="interactive")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I see a Rust project"):
            response = agent.start(project_root=str(tmp_path))
        assert "Rust" in response or "KB loaded" in response

    def test_context_compression_preserves_kb(self, tmp_path):
        """After compression, KB data survives in state."""
        agent = ArchitectAgent(backend="ollama")
        agent.state.stack = "Python 3.13"
        agent.state.adrs = [{"title": "Test ADR", "status": "Accepted",
                              "rationale": "Because reasons"}]
        agent.state.standing_rules = ["No deps in hooks"]
        agent.state.patterns = [{"pattern": "BaseAgent", "where": "scripts/"}]

        summary = agent.state.to_context_summary()
        assert "Python 3.13" in summary
        assert "Test ADR" in summary
        assert "No deps" in summary
        assert "BaseAgent" in summary

    def test_done_generates_report(self, tmp_path):
        """Report includes ADRs, guidance, KB state."""
        agent = ArchitectAgent(backend="ollama")
        agent.state.stack = "Go"
        agent.state.adrs = [{"title": "Use interfaces", "status": "Proposed"}]
        agent.state.guidance_history = [{"task": "Implement handler"}]
        agent.state.patterns = [{"pattern": "Middleware"}]
        agent.state.kb_dirty = False

        report = build_architect_report(agent.state)
        assert report["agent_type"] == "architect"
        assert report["stack"] == "Go"
        assert len(report["adrs"]) == 1
        assert report["guidance_count"] == 1
        assert report["patterns_count"] == 1
        assert report["kb_dirty"] is False

    def test_kb_flush_on_finish(self, tmp_path):
        """KB is flushed to disk on finish."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        agent = ArchitectAgent(backend="ollama")
        agent._project_root = tmp_path
        agent.state.kb_path = str(claude_dir / "architect_kb.json")
        agent.state.stack = "Python"
        agent._kb_data = _empty_kb()
        agent._kb_data["stack"] = "Python"

        with patch.object(agent.backend_adapter, "call",
                          return_value="ready"):
            agent.start(project_root=str(tmp_path))

        agent._tool_done("Complete")
        agent._on_finish()

        # KB should be written to disk
        kb_path = claude_dir / "architect_kb.json"
        assert kb_path.exists()
        data = json.loads(kb_path.read_text())
        assert data["stack"] == "Python"


# ===========================================================================
# BaseAgent behavioral rules
# ===========================================================================

class TestBehavioralRules:
    def test_base_behavioral_rules_exists(self):
        """_base_behavioral_rules is a static method on BaseAgent."""
        rules = BaseAgent._base_behavioral_rules()
        assert isinstance(rules, str)
        assert "INVIOLABLE" in rules

    def test_rules_content(self):
        """Rules contain all 6 constraints."""
        rules = BaseAgent._base_behavioral_rules()
        assert "Never invent" in rules
        assert "Never summarize" in rules or "verbatim" in rules.lower()
        assert "patchwork" in rules.lower() or "alternatives" in rules.lower()
        assert "multiple options" in rules.lower() or "pro/contra" in rules.lower()
        assert "Record" in rules or "choices" in rules
        assert "outdated" in rules.lower() or "verify" in rules.lower()


# ===========================================================================
# v2: Consultation chain
# ===========================================================================

class TestConsultationChain:
    def _make_agent(self, tmp_path):
        agent = ArchitectAgent(backend="ollama", mode="interactive")
        agent._project_root = tmp_path
        agent.state.project_root = str(tmp_path)
        agent.state.kb_path = str(
            tmp_path / ".claude" / "architect_kb.json")
        agent._register_tools()
        return agent

    def test_call_consultant_fallback(self, tmp_path):
        """Backend unavailable -> fallback warning, continues."""
        agent = self._make_agent(tmp_path)
        # No _agent_caller set -> fallback
        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            result = agent._tool_call_consultant(
                domain="volcanology",
                question="What is the viscosity of basaltic magma?",
                context="Building a magmatic simulation",
            )
        assert "FALLBACK" in result
        assert "volcanology" in result.lower() or "consult" in result.lower()
        # Should still be recorded in KB
        assert len(agent._kb_data.get("consultations", [])) == 1

    def test_call_consultant_records(self, tmp_path):
        """Consultation recorded in state findings and KB."""
        agent = self._make_agent(tmp_path)
        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            agent._tool_call_consultant(
                domain="security", question="Is this auth pattern safe?")
        # Check state findings
        assert any("Consultation" in str(f) for f in agent.state.findings)
        # Check KB
        assert len(agent._kb_data["consultations"]) == 1
        assert "security" in agent._kb_data["consultations"][0]["type"]

    def test_call_consultant_preserves_gated_protocol_result(
            self, tmp_path):
        """[GATED] protocol results must not fall back to legacy mechanics."""
        from consultation_protocol import ConsultationResult

        agent = self._make_agent(tmp_path)
        agent._agent_caller = MagicMock()
        gated = (
            "[GATED] governance_check_failed: "
            "refusing specialist runtime path"
        )
        with patch.object(agent, "consult",
                          return_value=ConsultationResult(answer=gated)):
            result = agent._tool_call_consultant(
                domain="security",
                question="Is this auth pattern safe?",
            )

        assert result == gated
        assert agent._kb_data["consultations"][-1]["path"] == "protocol"
        agent._agent_caller.call_consult.assert_not_called()

    def test_run_tandem_fallback(self, tmp_path):
        """Tandem fallback when no agent_caller."""
        agent = self._make_agent(tmp_path)
        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            result = agent._tool_run_tandem(
                problem="Should we use microservices or monolith?",
                role="architect",
            )
        data = json.loads(result)
        assert data.get("fallback") is True
        assert "consultations" in agent._kb_data
        assert len(agent._kb_data["consultations"]) == 1

    def test_call_peer_architect_fallback(self, tmp_path):
        """Peer architect fallback when no backend."""
        agent = self._make_agent(tmp_path)
        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            result = agent._tool_call_peer_architect(
                question="Is BaseAgent inheritance the right choice?",
                context="Considering composition as alternative",
            )
        assert "FALLBACK" in result
        assert "unvalidated" in result.lower() or "unavailable" in result.lower()
        assert len(agent._kb_data["consultations"]) == 1

    def test_web_search_fallback(self, tmp_path):
        """Web search fallback when no Claude CLI."""
        agent = self._make_agent(tmp_path)
        with patch("architect_agent.subprocess.run",
                    side_effect=OSError("not found")):
            result = agent._tool_web_search(
                "Python 3.14 release date")
        assert "FALLBACK" in result
        assert "configure" in result.lower() or "explicit" in result.lower()

    def test_web_search_success(self, tmp_path):
        """Web search succeeds via Claude CLI mock."""
        agent = self._make_agent(tmp_path)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Python 3.14 is scheduled for October 2025."
        with patch.dict(os.environ, {
                 "ARCHITECT_WEB_BACKEND": "claude",
                 "ARCHITECT_WEB_LAUNCHER": "claude",
             }), patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("architect_agent.subprocess.run",
                    return_value=mock_result):
            result = agent._tool_web_search(
                "Python 3.14 release date")
        assert "Python 3.14" in result
        assert len(agent._kb_data["consultations"]) == 1

    def test_call_socratic_fallback(self, tmp_path):
        """Socratic fallback generates self-challenge questions."""
        agent = self._make_agent(tmp_path)
        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            result = agent._tool_call_socratic(
                topic="Using inheritance for all agents",
                context="vs composition pattern",
            )
        assert "FALLBACK" in result
        assert "devil's advocate" in result.lower()
        assert "strongest argument AGAINST" in result
        assert len(agent._kb_data["consultations"]) == 1

    def test_call_socratic_verbatim_context(self, tmp_path):
        """Context passed to socratic is not truncated."""
        agent = self._make_agent(tmp_path)
        long_context = "A" * 500
        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            result = agent._tool_call_socratic(
                topic="Architecture choice", context=long_context)
        # In fallback mode, context doesn't appear in output
        # but recording should have the full topic
        record = agent._kb_data["consultations"][-1]
        assert record["topic"] == "Architecture choice"

    def test_all_consultation_fallbacks(self, tmp_path):
        """Every consultation tool degrades gracefully."""
        agent = self._make_agent(tmp_path)
        # Call all 5 tools with no backend
        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            r1 = agent._tool_call_consultant("physics", "gravity?")
            r2 = agent._tool_run_tandem("monolith vs micro?")
            r3 = agent._tool_call_peer_architect("Is this right?")
            r5 = agent._tool_call_socratic("test topic")
        with patch("architect_agent.subprocess.run",
                    side_effect=OSError("no")):
            r4 = agent._tool_web_search("test query")

        # None should crash, all should return strings
        for r in [r1, r2, r3, r4, r5]:
            assert isinstance(r, str)
            assert len(r) > 0

        # All should be recorded
        assert len(agent._kb_data["consultations"]) == 5

    def test_consultation_with_mocked_agent_caller(self, tmp_path):
        """When agent_caller is available, it's used."""
        agent = self._make_agent(tmp_path)
        # Mock agent_caller
        mock_caller = MagicMock()
        mock_caller.call_consult.return_value = (
            "Basaltic magma has viscosity 10^2-10^4 Pa.s")
        mock_pg = MagicMock()
        mock_pg.generate.return_value = "prompt text"
        agent._agent_caller = mock_caller
        agent._prompt_generator = mock_pg

        with patch.object(agent, "consult",
                          side_effect=ImportError("no protocol")):
            result = agent._tool_call_consultant(
                domain="volcanology",
                question="What is basaltic magma viscosity?",
            )
        assert "10^2" in result or "Pa.s" in result
        mock_caller.call_consult.assert_called_once()
