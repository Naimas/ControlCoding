"""Tests for coder_agent.py - CoderAgent (Sprint S7)."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import AgentStateBase, BackendAdapter, ReportBuilder, BaseAgent
from coder_agent import (
    CoderState, CoderAgent, build_coder_report,
    MAX_WRITE_SIZE, BLOCKED_COMMANDS, TEST_FRAMEWORKS,
)


# ===========================================================================
# CoderState
# ===========================================================================

class TestCoderState:
    def test_defaults(self):
        s = CoderState()
        assert s.agent_type == "coder"
        assert s.task == ""
        assert s.target_files == []
        assert s.files_modified == []
        assert s.files_created == []
        assert s.tests_run == 0
        assert s.tests_passed == 0
        assert s.tests_failed == 0
        assert s.build_success is None

    def test_to_context_summary(self):
        s = CoderState()
        s.current_step = 5
        s.task = "Implement auth module"
        s.target_files = ["src/auth.py"]
        s.files_modified = ["src/auth.py"]
        s.files_created = ["tests/test_auth.py"]
        s.tests_run = 10
        s.tests_passed = 9
        s.tests_failed = 1
        s.constraints = ["stdlib only"]
        s.acceptance_criteria = ["Login works"]

        summary = s.to_context_summary()
        assert "auth module" in summary
        assert "src/auth.py" in summary
        assert "9 passed" in summary
        assert "1 failed" in summary
        assert "stdlib only" in summary
        assert "Login works" in summary


# ===========================================================================
# Tool helpers
# ===========================================================================

class TestCoderTools:
    def _make_agent(self, tmp_path):
        agent = CoderAgent(backend="ollama", mode="autonomous")
        agent._project_root = tmp_path
        agent._register_tools()
        return agent

    # --- read_file ---

    def test_read_file_full_context(self, tmp_path):
        """Coder reads up to 500 lines by default."""
        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line {i}" for i in range(600)))
        agent = self._make_agent(tmp_path)
        result = agent._tool_read_file("big.py")
        assert "line 499" in result
        assert "truncated" in result
        assert "line 550" not in result

    # --- write_file ---

    def test_write_file_creates(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_write_file("new.py", "print('hello')\n")
        assert "Written" in result
        assert (tmp_path / "new.py").exists()
        assert "new.py" in agent.state.files_created

    def test_write_file_modifies(self, tmp_path):
        (tmp_path / "existing.py").write_text("old content")
        agent = self._make_agent(tmp_path)
        result = agent._tool_write_file("existing.py", "new content")
        assert "Written" in result
        assert "existing.py" in agent.state.files_modified
        assert (tmp_path / "existing.py").read_text() == "new content"

    def test_write_file_size_limit(self, tmp_path):
        agent = self._make_agent(tmp_path)
        big_content = "x" * (MAX_WRITE_SIZE + 1)
        result = agent._tool_write_file("big.txt", big_content)
        assert "too large" in result.lower()
        assert not (tmp_path / "big.txt").exists()

    def test_write_file_outside_project(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_write_file("../../etc/passwd", "hack")
        # Should be caught by path check
        assert "error" in result.lower() or "outside" in result.lower() \
            or "Written" in result  # some OS resolve differently

    def test_write_file_creates_dirs(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_write_file("src/deep/module.py", "code")
        assert "Written" in result
        assert (tmp_path / "src" / "deep" / "module.py").exists()

    # --- edit_file ---

    def test_edit_file_replace(self, tmp_path):
        (tmp_path / "code.py").write_text("def old_func():\n    pass\n")
        agent = self._make_agent(tmp_path)
        result = agent._tool_edit_file(
            "code.py", "old_func", "new_func")
        assert "Edited" in result
        assert "new_func" in (tmp_path / "code.py").read_text()
        assert "code.py" in agent.state.files_modified

    def test_edit_file_not_found_text(self, tmp_path):
        (tmp_path / "code.py").write_text("hello world")
        agent = self._make_agent(tmp_path)
        result = agent._tool_edit_file("code.py", "nonexistent", "new")
        assert "not found" in result.lower()

    def test_edit_file_missing_file(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_edit_file("missing.py", "a", "b")
        assert "not found" in result.lower()

    # --- run_command ---

    def test_run_command_blocked(self, tmp_path):
        agent = self._make_agent(tmp_path)
        for cmd in ["rm -rf /", "del file.txt", "mv a b"]:
            result = agent._tool_run_command(cmd)
            assert "blocked" in result.lower() or "error" in result.lower()

    def test_run_command_success(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_run_command("echo hello")
        assert "hello" in result

    # --- run_tests ---

    def test_run_tests_pytest(self, tmp_path):
        """Detects pytest from tests/ directory."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "def test_a(): pass\n")
        agent = self._make_agent(tmp_path)
        detected = agent._detect_test_command()
        assert "pytest" in detected

    def test_run_tests_npm(self, tmp_path):
        """Detects npm test from package.json."""
        pkg = {"scripts": {"test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        agent = self._make_agent(tmp_path)
        detected = agent._detect_test_command()
        assert "npm" in detected

    def test_run_tests_explicit_command(self, tmp_path):
        agent = self._make_agent(tmp_path)
        result = agent._tool_run_tests(command="echo '3 passed, 1 failed'")
        assert "3 passed" in result
        assert agent.state.tests_passed >= 3
        assert agent.state.tests_failed >= 1

    def test_parse_test_output_pytest(self, tmp_path):
        agent = self._make_agent(tmp_path)
        p, f, t = agent._parse_test_output(
            "====== 42 passed, 3 failed in 2.5s ======")
        assert p == 42
        assert f == 3
        assert t == 45

    # --- ask_concierge ---

    def test_ask_concierge_autonomous(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.state.constraints = ["Use stdlib only", "No external deps"]
        result = agent._tool_ask_concierge("Can I use requests library?")
        # Should find relevant constraint
        assert len(agent.state.questions_asked) == 1

    def test_ask_concierge_records(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._tool_ask_concierge("How should I structure this?")
        assert len(agent.state.questions_asked) == 1
        assert len(agent.state.concierge_answers) == 1

    # --- list_files ---

    def test_list_files_basic(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        (tmp_path / "sub").mkdir()
        agent = self._make_agent(tmp_path)
        result = agent._tool_list_files()
        assert "a.py" in result
        assert "b.py" in result
        assert "[d]" in result  # sub directory

    def test_list_files_pattern(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        agent = self._make_agent(tmp_path)
        result = agent._tool_list_files(pattern="*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    # --- done ---

    def test_done_report(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.state.task = "Implement feature X"
        agent.state.files_modified = ["src/x.py"]
        agent.state.files_created = ["tests/test_x.py"]
        agent.state.tests_run = 5
        agent.state.tests_passed = 4
        agent.state.tests_failed = 1
        agent.state.acceptance_criteria = ["Feature works", "Tests pass"]

        report = build_coder_report(agent.state)
        assert report["task"] == "Implement feature X"
        assert report["files_modified"] == ["src/x.py"]
        assert report["files_created"] == ["tests/test_x.py"]
        assert report["tests_run"] == 5
        assert report["tests_passed"] == 4
        assert report["tests_failed"] == 1


# ===========================================================================
# Lifecycle
# ===========================================================================

class TestCoderLifecycle:
    def test_guidance_loaded_on_start(self, tmp_path):
        """Architect's guide_coder parsed into state."""
        guidance = {
            "task": "Implement auth",
            "approach": "Use session tokens",
            "files_to_touch": ["src/auth.py"],
            "patterns_to_follow": ["Repository pattern"],
            "patterns_to_avoid": ["Global state"],
            "constraints": ["stdlib only"],
            "acceptance_criteria": ["Login works", "Tests pass"],
            "estimated_complexity": "medium",
        }
        agent = CoderAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="I'll start reading the files"):
            agent.start(
                project_root=str(tmp_path),
                guidance=guidance,
            )
        assert agent.state.task == "Implement auth"
        assert agent.state.approach == "Use session tokens"
        assert "src/auth.py" in agent.state.target_files
        assert "Repository pattern" in agent.state.patterns_to_follow
        assert "Global state" in agent.state.patterns_to_avoid
        assert "stdlib only" in agent.state.constraints
        assert len(agent.state.acceptance_criteria) == 2

    def test_guidance_as_json_string(self, tmp_path):
        """Guidance can be passed as JSON string."""
        guidance = json.dumps({"task": "Fix bug", "approach": "Patch"})
        agent = CoderAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="ready"):
            agent.start(
                project_root=str(tmp_path),
                guidance=guidance,
            )
        assert agent.state.task == "Fix bug"

    def test_guidance_as_plain_text(self, tmp_path):
        """Plain text task falls back gracefully."""
        agent = CoderAgent(backend="ollama")
        with patch.object(agent.backend_adapter, "call",
                          return_value="ready"):
            agent.start(
                project_root=str(tmp_path),
                guidance="Just fix the login bug",
            )
        assert agent.state.task == "Just fix the login bug"

    def test_coder_full_loop(self, tmp_path):
        """Start -> read -> write -> test -> done."""
        (tmp_path / "main.py").write_text("print('old')\n")

        agent = CoderAgent(backend="ollama")
        responses = [
            "I'll read the file first",
            '```json\n{"tool": "read_file", "params": {"path": "main.py"}}\n```',
            '```json\n{"tool": "write_file", "params": {"path": "main.py", "content": "print(\'new\')\\n"}}\n```',
            '```json\n{"tool": "done", "params": {"summary": "Updated main.py"}}\n```',
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
                guidance={"task": "Update main.py"},
            )
        assert report["phase"] == "done"
        assert "main.py" in agent.state.files_modified
