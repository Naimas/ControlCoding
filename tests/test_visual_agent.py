"""Tests for visual_agent.py - VisualAgent (Sprint S4)."""

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
from visual_agent import (
    VisualAgentState, VisualAgent,
    _build_visual_prompt, build_visual_report,
    ACTION_TOOLS,
)


# ===========================================================================
# VisualAgentState
# ===========================================================================

class TestVisualAgentState:
    def test_defaults(self):
        s = VisualAgentState()
        assert s.agent_type == "visual"
        assert s.exe_path == ""
        assert s.pid == 0
        assert s.owns_process is False
        assert s.criteria == []
        assert s.screenshots == []
        assert s.screenshot_counter == 0
        assert s.auto_screenshot is True
        assert s.operating_mode == "autonomous"

    def test_to_context_summary_with_criteria(self):
        s = VisualAgentState(
            exe_path="app.exe", pid=1234,
            operating_mode="criteria", current_step=10,
        )
        s.criteria = [
            {"id": "C-01", "text": "Login works", "status": "pass"},
            {"id": "C-02", "text": "Dashboard loads", "status": "fail"},
            {"id": "C-03", "text": "Menu opens", "status": "pending"},
        ]
        s.screenshots = [
            {"step": 5, "path": "s1.png", "description": "Login screen"},
        ]
        summary = s.to_context_summary()
        assert "app.exe" in summary
        assert "1234" in summary
        assert "1 pass" in summary
        assert "1 fail" in summary
        assert "1 pending" in summary
        assert "C-01" in summary
        assert "Login screen" in summary

    def test_to_context_summary_empty(self):
        s = VisualAgentState()
        summary = s.to_context_summary()
        assert "VISUAL AGENT STATE SUMMARY" in summary

    def test_save_and_load(self, tmp_path):
        s = VisualAgentState(
            session_id="abc", exe_path="app.exe",
            pid=999, screenshot_counter=5,
        )
        s.criteria = [
            {"id": "C-01", "text": "Works", "status": "pass"}]
        path = str(tmp_path / "state.json")
        s.save(path)

        loaded = VisualAgentState.load(path)
        assert loaded.session_id == "abc"
        assert loaded.exe_path == "app.exe"
        assert loaded.pid == 999
        assert loaded.screenshot_counter == 5
        assert len(loaded.criteria) == 1


# ===========================================================================
# System Prompt
# ===========================================================================

class TestSystemPrompt:
    def test_base_prompt_has_tools(self):
        agent = VisualAgent(backend="ollama")
        agent._register_tools()
        prompt = agent._build_system_prompt()
        assert "screenshot" in prompt
        assert "click" in prompt
        assert "done" in prompt

    def test_criteria_mode_includes_criteria(self):
        s = VisualAgentState()
        s.criteria = [
            {"id": "C-01", "text": "Login works",
             "verification_method": "visual"},
        ]
        prompt = _build_visual_prompt(s, "tools here")
        assert "C-01" in prompt
        assert "Login works" in prompt
        assert "Verify each criterion" in prompt

    def test_explore_mode_prompt(self):
        s = VisualAgentState(operating_mode="explore")
        prompt = _build_visual_prompt(s, "tools here")
        assert "Exploration mode" in prompt
        assert "No predefined criteria" in prompt

    def test_autonomous_mode_no_criteria_no_explore(self):
        s = VisualAgentState(operating_mode="autonomous")
        prompt = _build_visual_prompt(s, "tools here")
        assert "Exploration mode" not in prompt
        assert "Verify each criterion" not in prompt


# ===========================================================================
# VisualAgent init
# ===========================================================================

class TestVisualAgentInit:
    def test_creates_visual_state(self):
        agent = VisualAgent(backend="ollama")
        assert isinstance(agent.state, VisualAgentState)
        assert agent.state.agent_type == "visual"

    def test_registers_all_tools(self):
        agent = VisualAgent(backend="ollama")
        agent._register_tools()
        names = agent.tools.get_registered_names()
        expected = ["screenshot", "compare", "click", "type_text",
                    "press_key", "hotkey", "scroll", "drag", "wait",
                    "run_command", "http_check", "file_check",
                    "assert_changed", "log_finding", "mark_criterion",
                    "done"]
        for name in expected:
            assert name in names, f"Missing tool: {name}"


# ===========================================================================
# Perception tools
# ===========================================================================

class TestScreenshotTool:
    def test_screenshot_success(self, tmp_path):
        agent = VisualAgent(backend="ollama")
        agent.state.pid = 1234
        agent.state.screenshot_dir = str(tmp_path)

        with patch("visual_agent.take_screenshot_window",
                   return_value=("", True)) as mock_ss, \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            result = agent._tool_screenshot("Login screen")

        assert isinstance(result, tuple)
        assert "Login screen" in result[0]
        assert len(agent.state.screenshots) == 1
        assert agent.state.screenshot_counter == 1

    def test_screenshot_no_utils(self):
        agent = VisualAgent(backend="ollama")
        with patch("visual_agent.HAS_VISUAL_UTILS", False):
            result = agent._tool_screenshot()
        assert "Error" in result


class TestCompareTool:
    def test_compare_defaults_to_last_two(self):
        agent = VisualAgent(backend="ollama")
        agent.state.screenshots = [
            {"step": 1, "path": "a.png", "description": "first"},
            {"step": 2, "path": "b.png", "description": "second"},
        ]
        with patch("visual_agent.compare_screenshots",
                   return_value=(True, 0.15)) as mock_cmp, \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            result = agent._tool_compare()
        assert "CHANGED" in result
        assert "0.150" in result
        mock_cmp.assert_called_once_with("a.png", "b.png")

    def test_compare_not_enough_screenshots(self):
        agent = VisualAgent(backend="ollama")
        agent.state.screenshots = [{"step": 1, "path": "a.png", "description": "one"}]
        with patch("visual_agent.HAS_VISUAL_UTILS", True):
            result = agent._tool_compare()
        assert "Error" in result


# ===========================================================================
# Action tools
# ===========================================================================

class TestActionTools:
    def test_click(self):
        agent = VisualAgent(backend="ollama")
        agent.state.auto_screenshot = False
        with patch("visual_agent.pyautogui") as mock_pa, \
             patch("visual_agent.HAS_PYAUTOGUI", True):
            result = agent._tool_click(x=100, y=200)
        mock_pa.click.assert_called_once_with(100, 200, button="left")
        assert "100" in result
        assert "200" in result

    def test_type_text(self):
        agent = VisualAgent(backend="ollama")
        agent.state.auto_screenshot = False
        with patch("visual_agent.pyautogui") as mock_pa, \
             patch("visual_agent.HAS_PYAUTOGUI", True):
            result = agent._tool_type_text(text="hello")
        mock_pa.write.assert_called_once_with("hello", interval=0.02)
        assert "hello" in result

    def test_press_key(self):
        agent = VisualAgent(backend="ollama")
        agent.state.auto_screenshot = False
        with patch("visual_agent.pyautogui") as mock_pa, \
             patch("visual_agent.HAS_PYAUTOGUI", True):
            result = agent._tool_press_key(key="enter")
        mock_pa.press.assert_called_once_with("enter")
        assert "enter" in result

    def test_hotkey(self):
        agent = VisualAgent(backend="ollama")
        agent.state.auto_screenshot = False
        with patch("visual_agent.pyautogui") as mock_pa, \
             patch("visual_agent.HAS_PYAUTOGUI", True):
            result = agent._tool_hotkey(keys="ctrl+s")
        mock_pa.hotkey.assert_called_once_with("ctrl", "s")
        assert "ctrl+s" in result

    def test_scroll(self):
        agent = VisualAgent(backend="ollama")
        agent.state.auto_screenshot = False
        with patch("visual_agent.pyautogui") as mock_pa, \
             patch("visual_agent.HAS_PYAUTOGUI", True):
            result = agent._tool_scroll(amount=-3)
        mock_pa.scroll.assert_called_once_with(-3)
        assert "down" in result

    def test_wait_caps_at_10(self):
        agent = VisualAgent(backend="ollama")
        with patch("visual_agent.time.sleep") as mock_sleep:
            result = agent._tool_wait(seconds=30.0)
        # Should cap at 10s
        mock_sleep.assert_called_once_with(10.0)

    def test_no_pyautogui(self):
        agent = VisualAgent(backend="ollama")
        with patch("visual_agent.HAS_PYAUTOGUI", False):
            assert "Error" in agent._tool_click(x=1, y=1)
            assert "Error" in agent._tool_type_text(text="a")
            assert "Error" in agent._tool_press_key(key="a")


class TestAutoScreenshot:
    def test_action_tools_set(self):
        """ACTION_TOOLS should contain the right set."""
        assert "click" in ACTION_TOOLS
        assert "type_text" in ACTION_TOOLS
        assert "scroll" in ACTION_TOOLS
        assert "wait" not in ACTION_TOOLS

    def test_auto_screenshot_on_click(self, tmp_path):
        agent = VisualAgent(backend="ollama")
        agent.state.pid = 1234
        agent.state.auto_screenshot = True
        agent.state.screenshot_dir = str(tmp_path)

        with patch("visual_agent.pyautogui") as mock_pa, \
             patch("visual_agent.HAS_PYAUTOGUI", True), \
             patch("visual_agent.take_screenshot_window",
                   return_value=("", True)), \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            result = agent._tool_click(x=50, y=50)

        # Should return tuple (text, image_path)
        assert isinstance(result, tuple)
        assert agent.state.screenshot_counter == 1

    def test_no_auto_screenshot_when_disabled(self):
        agent = VisualAgent(backend="ollama")
        agent.state.pid = 1234
        agent.state.auto_screenshot = False

        with patch("visual_agent.pyautogui") as mock_pa, \
             patch("visual_agent.HAS_PYAUTOGUI", True):
            result = agent._tool_click(x=50, y=50)

        # Should return plain string, no screenshot
        assert isinstance(result, str)
        assert agent.state.screenshot_counter == 0


# ===========================================================================
# Verification tools
# ===========================================================================

class TestVerificationTools:
    def test_run_command(self):
        agent = VisualAgent(backend="ollama")
        with patch("visual_agent.run_output_test",
                   return_value=(True, "output here")), \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            result = agent._tool_run_command("echo hello")
        assert "PASS" in result
        assert "output here" in result

    def test_assert_changed_pass(self):
        agent = VisualAgent(backend="ollama")
        agent.state.screenshots = [
            {"step": 1, "path": "a.png", "description": "before"},
            {"step": 2, "path": "b.png", "description": "after"},
        ]
        with patch("visual_agent.compare_screenshots",
                   return_value=(True, 0.15)), \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            result = agent._tool_assert_changed()
        assert "PASS" in result

    def test_assert_changed_fail(self):
        agent = VisualAgent(backend="ollama")
        agent.state.screenshots = [
            {"step": 1, "path": "a.png", "description": "before"},
            {"step": 2, "path": "b.png", "description": "after"},
        ]
        with patch("visual_agent.compare_screenshots",
                   return_value=(False, 0.001)), \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            result = agent._tool_assert_changed()
        assert "FAIL" in result
        assert "unchanged" in result

    def test_assert_changed_not_enough_screenshots(self):
        agent = VisualAgent(backend="ollama")
        result = agent._tool_assert_changed()
        assert "FAIL" in result


# ===========================================================================
# Session tools
# ===========================================================================

class TestSessionTools:
    def test_log_finding(self):
        agent = VisualAgent(backend="ollama")
        agent.state.last_screenshot = "screen.png"
        result = agent._tool_log_finding("Button is broken", severity="bug")
        assert "Finding logged" in result
        assert len(agent.state.findings) == 1
        assert agent.state.findings[0].severity == "bug"

    def test_mark_criterion_pass(self):
        agent = VisualAgent(backend="ollama")
        agent.state.criteria = [
            {"id": "C-01", "text": "Login works", "status": "pending",
             "evidence": "", "attempts": 0, "reasoning": ""},
        ]
        agent.state.last_screenshot = "login.png"
        result = agent._tool_mark_criterion(
            "C-01", "pass", reasoning="Login form accepted credentials")
        assert "PASS" in result
        assert agent.state.criteria[0]["status"] == "pass"
        assert agent.state.criteria[0]["evidence"] == "login.png"
        assert agent.state.criteria[0]["attempts"] == 1

    def test_mark_criterion_fail(self):
        agent = VisualAgent(backend="ollama")
        agent.state.criteria = [
            {"id": "C-02", "text": "Dashboard", "status": "pending",
             "evidence": "", "attempts": 0, "reasoning": ""},
        ]
        result = agent._tool_mark_criterion(
            "C-02", "fail", reasoning="Dashboard shows error")
        assert "FAIL" in result
        assert agent.state.criteria[0]["status"] == "fail"

    def test_mark_criterion_invalid_status(self):
        agent = VisualAgent(backend="ollama")
        agent.state.criteria = [
            {"id": "C-01", "text": "Test", "status": "pending",
             "evidence": "", "attempts": 0, "reasoning": ""},
        ]
        result = agent._tool_mark_criterion("C-01", "maybe")
        assert "Error" in result

    def test_mark_criterion_not_found(self):
        agent = VisualAgent(backend="ollama")
        agent.state.criteria = []
        result = agent._tool_mark_criterion("C-99", "pass")
        assert "Error" in result
        assert "not found" in result

    def test_done(self):
        agent = VisualAgent(backend="ollama")
        result = agent._tool_done("All tests complete")
        assert agent._done_flag is True
        assert agent.state.stop_reason == "done"


# ===========================================================================
# Report
# ===========================================================================

class TestVisualReport:
    def test_includes_visual_fields(self):
        s = VisualAgentState(
            session_id="x", exe_path="app.exe",
            operating_mode="criteria", screenshot_counter=5,
        )
        s.criteria = [
            {"id": "C-01", "status": "pass"},
            {"id": "C-02", "status": "fail"},
            {"id": "C-03", "status": "pending"},
        ]
        s.screenshots = [
            {"step": 1, "path": "s1.png", "description": "test"},
        ]
        report = build_visual_report(s)
        assert report["exe_path"] == "app.exe"
        assert report["operating_mode"] == "criteria"
        assert report["screenshots_count"] == 5
        assert len(report["screenshots"]) == 1
        assert report["criteria_summary"]["pass"] == 1
        assert report["criteria_summary"]["fail"] == 1
        assert report["criteria_summary"]["pending"] == 1
        assert report["criteria_summary"]["total"] == 3


# ===========================================================================
# Process lifecycle
# ===========================================================================

class TestProcessLifecycle:
    def test_on_start_launches_process(self, tmp_path):
        agent = VisualAgent(backend="ollama")
        mock_proc = MagicMock()
        mock_proc.pid = 9999

        with patch("visual_agent.launch_process",
                   return_value=mock_proc), \
             patch("visual_agent.take_screenshot_window",
                   return_value=("", True)), \
             patch("visual_agent.HAS_VISUAL_UTILS", True), \
             patch("visual_agent.time.sleep"), \
             patch.object(agent.backend_adapter, "add_user_message"):
            msg = agent._on_start(
                exe="app.exe",
                screenshot_dir=str(tmp_path))

        assert agent.state.pid == 9999
        assert agent.state.owns_process is True
        assert "9999" in msg

    def test_on_start_no_exe(self):
        agent = VisualAgent(backend="ollama")
        msg = agent._on_start()
        assert "No executable" in msg
        assert agent.state.pid == 0

    def test_on_start_with_criteria(self):
        agent = VisualAgent(backend="ollama")
        criteria = [
            {"id": "C-01", "text": "Login works"},
            {"id": "C-02", "text": "Dashboard loads"},
        ]
        agent._on_start(criteria=criteria)
        assert len(agent.state.criteria) == 2
        assert agent.state.criteria[0]["status"] == "pending"
        assert agent.state.criteria[0]["id"] == "C-01"

    def test_on_finish_kills_owned_process(self):
        agent = VisualAgent(backend="ollama")
        agent.state.owns_process = True
        mock_proc = MagicMock()
        agent._process = mock_proc

        with patch("visual_agent.kill_process") as mock_kill, \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            agent._on_finish()

        mock_kill.assert_called_once_with(mock_proc)

    def test_on_finish_does_not_kill_unowned(self):
        agent = VisualAgent(backend="ollama")
        agent.state.owns_process = False
        agent._process = MagicMock()

        with patch("visual_agent.kill_process") as mock_kill, \
             patch("visual_agent.HAS_VISUAL_UTILS", True):
            agent._on_finish()

        mock_kill.assert_not_called()


# ===========================================================================
# Integration: full run with mocked LLM
# ===========================================================================

class TestVisualAgentIntegration:
    def test_full_run_criteria_mode(self, tmp_path):
        """Simulate: start -> screenshot -> mark criterion -> done."""
        agent = VisualAgent(backend="ollama", mode="autonomous")
        agent.state.auto_screenshot = False  # Simplify mock

        criteria = [{"id": "C-01", "text": "App launches"}]

        responses = [
            # start() initial LLM response
            "I see the application.",
            # agent_loop: LLM takes screenshot
            '```json\n{"tool": "mark_criterion", "params": {"criterion_id": "C-01", "status": "pass", "reasoning": "App is visible"}}\n```',
            # agent_loop: LLM calls done
            '```json\n{"tool": "done", "params": {"summary": "All criteria verified"}}\n```',
        ]
        idx = [0]

        def mock_call(tools=None):
            resp = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            agent.backend_adapter.add_assistant_message(resp)
            return resp

        with patch.object(
                agent,
                "_check_specialist_runtime_gate",
                return_value={"allowed": True, "backend": "ollama", "model": ""}), \
             patch.object(agent.backend_adapter, "call",
                          side_effect=mock_call):
            report = agent.run(
                criteria=criteria,
                screenshot_dir=str(tmp_path),
            )

        assert report["stop_reason"] == "done"
        assert agent.state.criteria[0]["status"] == "pass"


# ===========================================================================
# S5: Modes + Criteria + Concierge Integration
# ===========================================================================

class TestCriteriaFlow:
    def test_criteria_normalization_from_dicts(self):
        """Criteria dicts are normalized with status=pending and id."""
        agent = VisualAgent(backend="ollama")
        raw = [
            {"name": "Login", "text": "Login form works"},
            {"id": "C-02", "original_text": "Dashboard loads"},
        ]
        agent._on_start(criteria=raw)
        assert len(agent.state.criteria) == 2
        assert agent.state.criteria[0]["id"] == "Login"
        assert agent.state.criteria[0]["status"] == "pending"
        assert agent.state.criteria[1]["id"] == "C-02"
        assert "Dashboard loads" in agent.state.criteria[1]["text"]

    def test_mark_criterion_sets_evidence_from_last_screenshot(self):
        """mark_criterion auto-sets evidence from last_screenshot if not provided."""
        agent = VisualAgent(backend="ollama")
        agent.state.criteria = [
            {"id": "C-01", "text": "Test", "status": "pending",
             "evidence": "", "attempts": 0, "reasoning": ""},
        ]
        agent.state.last_screenshot = "screenshots/step_005.png"
        agent._tool_mark_criterion("C-01", "pass")
        assert agent.state.criteria[0]["evidence"] == "screenshots/step_005.png"

    def test_mark_criterion_explicit_evidence(self):
        """Explicit evidence overrides last_screenshot."""
        agent = VisualAgent(backend="ollama")
        agent.state.criteria = [
            {"id": "C-01", "text": "Test", "status": "pending",
             "evidence": "", "attempts": 0, "reasoning": ""},
        ]
        agent.state.last_screenshot = "auto.png"
        agent._tool_mark_criterion("C-01", "pass", evidence="explicit.png")
        assert agent.state.criteria[0]["evidence"] == "explicit.png"

    def test_multiple_criteria_independent(self):
        """Marking one criterion doesn't affect others."""
        agent = VisualAgent(backend="ollama")
        agent.state.criteria = [
            {"id": "C-01", "text": "A", "status": "pending",
             "evidence": "", "attempts": 0, "reasoning": ""},
            {"id": "C-02", "text": "B", "status": "pending",
             "evidence": "", "attempts": 0, "reasoning": ""},
        ]
        agent._tool_mark_criterion("C-01", "pass")
        assert agent.state.criteria[0]["status"] == "pass"
        assert agent.state.criteria[1]["status"] == "pending"


class TestExploreMode:
    def test_explore_prompt_no_criteria(self):
        """Explore mode includes exploration instructions, no criteria section."""
        s = VisualAgentState(operating_mode="explore")
        s.criteria = []  # No criteria in explore
        prompt = _build_visual_prompt(s, "tools")
        assert "Exploration mode" in prompt
        assert "Verify each criterion" not in prompt

    def test_explore_findings_logged(self):
        """log_finding works in explore mode."""
        agent = VisualAgent(backend="ollama")
        agent.state.operating_mode = "explore"
        agent._tool_log_finding("Found broken button", severity="bug")
        agent._tool_log_finding("Menu animation smooth", severity="info")
        assert len(agent.state.findings) == 2
        assert agent.state.findings[0].severity == "bug"


class TestInteractiveMode:
    def test_step_sends_user_input(self):
        """Interactive step() sends user text to LLM and gets response."""
        agent = VisualAgent(backend="ollama", mode="interactive")
        with patch.object(
                agent,
                "_check_specialist_runtime_gate",
                return_value={"allowed": True, "backend": "ollama", "model": ""}), \
             patch.object(agent.backend_adapter, "call",
                          return_value="initial"):
            agent.start()

        with patch.object(agent.backend_adapter, "call",
                          return_value="I see a blue button"):
            result = agent.step("check the menu bar")

        assert result == "I see a blue button"
        assert agent.state.total_llm_calls == 2


class TestConciergeIntegration:
    def test_spawn_visual_agent_calls_run(self):
        """Concierge spawn_visual_agent creates and runs a VisualAgent."""
        from concierge_agent import ConciergeAgent

        concierge = ConciergeAgent(backend="ollama")

        # Mock the VisualAgent so we don't need a real LLM
        mock_report = {
            "stop_reason": "done",
            "criteria_summary": {"pass": 2, "fail": 1, "pending": 0},
            "screenshots_count": 5,
        }

        with patch("concierge_agent.json") as mock_json:
            # We need json.loads to work for criteria parsing
            mock_json.loads.return_value = [
                {"id": "C-01", "text": "test"}]
            mock_json.JSONDecodeError = json.JSONDecodeError

            with patch(
                "concierge_agent.ConciergeAgent._tool_spawn_visual_agent"
            ) as original:
                # Call the real method but mock the import
                pass

        # Simpler approach: test the tool directly with mocked import
        with patch.dict("sys.modules", {
            "visual_agent": MagicMock(
                VisualAgent=MagicMock(return_value=MagicMock(
                    run=MagicMock(return_value=mock_report),
                    state=MagicMock(timeout_seconds=300),
                )),
                build_visual_report=MagicMock(),
            ),
        }):
            result = concierge._tool_spawn_visual_agent(
                exe="app.exe",
                criteria='[{"id": "C-01", "text": "works"}]',
                timeout=120,
            )

        assert "Pass: 2" in result
        assert "Fail: 1" in result
        assert "Screenshots: 5" in result
        assert len(concierge.state.agent_reports) == 1
        assert concierge.state.agent_reports[0]["agent"] == "visual"

    def test_spawn_visual_agent_no_module(self):
        """spawn_visual_agent returns error if visual_agent.py not importable."""
        from concierge_agent import ConciergeAgent

        concierge = ConciergeAgent(backend="ollama")

        # Force ImportError by patching
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "visual_agent":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = concierge._tool_spawn_visual_agent(exe="app.exe")

        assert "Error" in result or "not available" in result


class TestVisualReportExtended:
    def test_report_criteria_summary(self):
        """Report includes correct criteria summary counts."""
        s = VisualAgentState()
        s.criteria = [
            {"id": "C-01", "status": "pass"},
            {"id": "C-02", "status": "pass"},
            {"id": "C-03", "status": "fail"},
            {"id": "C-04", "status": "pending"},
            {"id": "C-05", "status": "skip"},
        ]
        report = build_visual_report(s)
        assert report["criteria_summary"]["pass"] == 2
        assert report["criteria_summary"]["fail"] == 1
        assert report["criteria_summary"]["pending"] == 1
        assert report["criteria_summary"]["skip"] == 1
        assert report["criteria_summary"]["total"] == 5

    def test_report_includes_screenshots_list(self):
        s = VisualAgentState(screenshot_counter=3)
        s.screenshots = [
            {"step": 1, "path": "a.png", "description": "first"},
            {"step": 2, "path": "b.png", "description": "second"},
        ]
        report = build_visual_report(s)
        assert report["screenshots_count"] == 3
        assert len(report["screenshots"]) == 2
