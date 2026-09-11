#!/usr/bin/env python3
"""visual_agent.py - Autonomous QA Agent for visual testing.

The VisualAgent is an on-demand LLM-based agent that tests applications by
seeing (screenshots), thinking (LLM reasoning), and acting (keyboard/mouse).

It inherits from BaseAgent and uses visual_check_utils.py as its tool library
and pyautogui for input simulation.

Design document: dev/design/03_DSN_VisualAgent_InProgress.md
Plan: dev/plans/archive/07_DEV_VisualAgent_Completed.md (Sprint S4)
"""

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Import base agent framework
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, BaseAgent, BackendAdapter, ToolExecutor,
    ReportBuilder, ToolCall, ToolResult,
)

# Import visual tools (graceful fallback if not available)
try:
    from visual_check_utils import (
        take_screenshot_window, take_screenshot,
        compare_screenshots, launch_process, kill_process,
        run_build, focus_window, find_window_by_pid,
        run_output_test, run_http_test, run_file_test,
    )
    HAS_VISUAL_UTILS = True
except ImportError:
    HAS_VISUAL_UTILS = False

# pyautogui import (graceful fallback)
try:
    import pyautogui
    HAS_PYAUTOGUI = True
    # Keep failsafe ON - human can abort by moving mouse to corner
except ImportError:
    HAS_PYAUTOGUI = False


# ---------------------------------------------------------------------------
# Action tools that trigger auto-screenshot
# ---------------------------------------------------------------------------

ACTION_TOOLS = {"click", "type_text", "press_key", "hotkey", "scroll", "drag"}


# ---------------------------------------------------------------------------
# VisualAgentState
# ---------------------------------------------------------------------------

@dataclass
class VisualAgentState(AgentStateBase):
    """Deterministic state for the VisualAgent."""

    agent_type: str = "visual"

    # Application lifecycle
    exe_path: str = ""
    build_cmd: str = ""
    pid: int = 0
    owns_process: bool = False

    # Criteria tracking
    criteria: list = field(default_factory=list)
    # Each: {id, text, status, evidence, attempts, reasoning}

    # Evidence
    screenshots: list = field(default_factory=list)
    # Each: {step, path, description}
    last_screenshot: str = ""
    screenshot_counter: int = 0

    # Config
    operating_mode: str = "autonomous"
    auto_screenshot: bool = True
    screenshot_dir: str = "screenshots"

    def to_context_summary(self) -> str:
        lines = [
            f"=== VISUAL AGENT STATE SUMMARY (step {self.current_step}) ===",
            f"App: {self.exe_path} (PID: {self.pid})",
            f"Mode: {self.operating_mode}",
            f"Screenshots taken: {self.screenshot_counter}",
            f"Steps: {self.total_steps} | LLM calls: {self.total_llm_calls}",
        ]
        if self.criteria:
            passed = sum(1 for c in self.criteria
                         if c.get("status") == "pass")
            failed = sum(1 for c in self.criteria
                         if c.get("status") == "fail")
            pending = sum(1 for c in self.criteria
                          if c.get("status") == "pending")
            lines.append(
                f"Criteria: {passed} pass, {failed} fail, {pending} pending")
            for c in self.criteria:
                lines.append(
                    f"  [{c.get('status', '?')}] {c.get('id', '?')}: "
                    f"{c.get('text', '?')[:60]}")
        if self.screenshots:
            lines.append("\nLast screenshots:")
            for s in self.screenshots[-5:]:
                lines.append(
                    f"  [step {s.get('step', '?')}] "
                    f"{s.get('description', '?')[:80]}")
        if self.errors:
            lines.append(f"\nErrors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END VISUAL AGENT STATE SUMMARY ===")
        return "\n".join(lines)

    def save(self, path: str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self.session_id,
            "agent_type": self.agent_type,
            "started_at": self.started_at,
            "mode": self.mode,
            "phase": self.phase,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "total_llm_calls": self.total_llm_calls,
            "stuck_count": self.stuck_count,
            "elapsed_seconds": self.elapsed_seconds,
            "stop_reason": self.stop_reason,
            "errors": self.errors,
            "exe_path": self.exe_path,
            "pid": self.pid,
            "owns_process": self.owns_process,
            "operating_mode": self.operating_mode,
            "screenshot_counter": self.screenshot_counter,
            "criteria": self.criteria,
            "screenshots": self.screenshots[-20:],
        }
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "VisualAgentState":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        state = cls()
        for key, value in data.items():
            if hasattr(state, key):
                setattr(state, key, value)
        return state


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

VISUAL_SYSTEM_PROMPT_BASE = """\
You are a Visual QA Agent. You test applications by seeing screenshots and
interacting via keyboard and mouse.

Your loop: screenshot -> analyze what you see -> decide action -> execute -> repeat

Rules:
1. ALWAYS describe what you see in the screenshot before acting
2. If an action had no visible effect, try a different approach (don't repeat)
3. Be precise with coordinates - describe what element you're targeting
4. Log findings as you discover them with log_finding
5. When done testing, call the done tool

Tool call format - write a JSON block in a markdown fence:

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

Available tools:
{tool_descriptions}
"""

VISUAL_PROMPT_CRITERIA = """
## Criteria to verify

{criteria_list}

Verify each criterion systematically:
1. Plan how to verify it
2. Execute the verification steps (interact with the app)
3. Screenshot the evidence
4. Mark it pass or fail with mark_criterion
5. Move to the next criterion

A criterion PASSES only if you can see clear, unambiguous evidence.
Ambiguous or partial results are FAIL.
"""

VISUAL_PROMPT_EXPLORE = """
## Exploration mode

No predefined criteria. Explore the application freely:
1. Identify all visible UI elements
2. Try each interactive element (buttons, inputs, menus)
3. Navigate through all screens and sections
4. Test edge cases (empty inputs, special characters, rapid clicks)
5. Log every finding with log_finding (bugs, behaviors, UI issues, good features)
6. Call done when you've thoroughly explored
"""


def _build_visual_prompt(state: VisualAgentState,
                          tool_descriptions: str) -> str:
    parts = [VISUAL_SYSTEM_PROMPT_BASE.format(
        tool_descriptions=tool_descriptions)]

    if state.criteria:
        criteria_lines = []
        for c in state.criteria:
            cid = c.get("id", "?")
            text = c.get("text", "?")
            method = c.get("verification_method", c.get("method", "visual"))
            criteria_lines.append(f"- [{cid}] ({method}) {text}")
        parts.append(VISUAL_PROMPT_CRITERIA.format(
            criteria_list="\n".join(criteria_lines)))
    elif state.operating_mode == "explore":
        parts.append(VISUAL_PROMPT_EXPLORE)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# VisualAgent
# ---------------------------------------------------------------------------

class VisualAgent(BaseAgent):
    """Autonomous QA Agent - sees, thinks, acts.

    Inherits session management, tool parsing, safety guardrails,
    and report generation from BaseAgent.
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "autonomous"):
        self._done_flag = False
        self._process = None  # subprocess.Popen handle
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> VisualAgentState:
        return VisualAgentState()

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_visual_prompt(self.state, tool_desc)

    def _register_tools(self):
        # Perception
        self.tools.register(
            "screenshot", self._tool_screenshot,
            "Capture application window screenshot (description)")
        self.tools.register(
            "compare", self._tool_compare,
            "Compare two screenshots for visual difference (path1, path2)")

        # Actions
        self.tools.register(
            "click", self._tool_click,
            "Click at screen coordinates (x, y, button='left')")
        self.tools.register(
            "type_text", self._tool_type_text,
            "Type text character by character (text)")
        self.tools.register(
            "press_key", self._tool_press_key,
            "Press a single key like enter, tab, escape (key)")
        self.tools.register(
            "hotkey", self._tool_hotkey,
            "Key combination separated by + like ctrl+s (keys)")
        self.tools.register(
            "scroll", self._tool_scroll,
            "Scroll mouse wheel, positive=up negative=down (amount)")
        self.tools.register(
            "drag", self._tool_drag,
            "Drag mouse to coordinates (x, y, duration=0.5)")
        self.tools.register(
            "wait", self._tool_wait,
            "Wait for rendering/animation (seconds=1.0)")

        # Verification
        self.tools.register(
            "run_command", self._tool_run_command,
            "Run shell command and check output (command, expected)")
        self.tools.register(
            "http_check", self._tool_http_check,
            "Test HTTP endpoint (url, status=200, contains)")
        self.tools.register(
            "file_check", self._tool_file_check,
            "Check file existence/content (path, exists=true, contains)")
        self.tools.register(
            "assert_changed", self._tool_assert_changed,
            "Verify last action had visible effect (threshold=0.02)")

        # Session
        self.tools.register(
            "log_finding", self._tool_log_finding,
            "Record an observation (description, severity=info)")
        self.tools.register(
            "mark_criterion", self._tool_mark_criterion,
            "Mark criterion pass/fail (criterion_id, status, evidence, reasoning)")
        self.tools.register(
            "done", self._tool_done,
            "Signal testing complete (summary)")

    def _on_start(self, **context) -> str:
        """Launch app, take initial screenshot, return first message."""
        exe = context.get("exe", "")
        build_cmd = context.get("build_cmd", "")
        criteria = context.get("criteria", [])
        operating_mode = context.get("mode", self.mode)
        screenshot_dir = context.get("screenshot_dir", "screenshots")

        self.state.exe_path = exe
        self.state.build_cmd = build_cmd
        self.state.operating_mode = operating_mode
        self.state.screenshot_dir = screenshot_dir
        if criteria:
            # Normalize criteria format
            self.state.criteria = [
                {
                    "id": c.get("id", c.get("name", f"C-{i+1:02d}")),
                    "text": c.get("text", c.get("original_text", str(c))),
                    "status": "pending",
                    "evidence": "",
                    "attempts": 0,
                    "reasoning": "",
                    "verification_method": c.get("verification_method",
                                                  c.get("method", "visual")),
                }
                for i, c in enumerate(
                    criteria if isinstance(criteria, list) else [criteria])
            ]

        # Ensure screenshot directory exists
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

        # Build (optional)
        if build_cmd and HAS_VISUAL_UTILS:
            success, duration = run_build(build_cmd)
            if not success:
                self.state.log_error(f"Build failed after {duration:.0f}ms")
                return (f"Build failed after {duration:.0f}ms. "
                        f"Cannot test application.")

        # Launch process
        if exe and HAS_VISUAL_UTILS:
            try:
                self._process = launch_process(exe)
                self.state.pid = self._process.pid
                self.state.owns_process = True
                time.sleep(2)  # Wait for window to appear
            except (OSError, FileNotFoundError) as e:
                self.state.log_error(f"Failed to launch {exe}: {e}")
                return f"Failed to launch application: {e}"

        # Initial screenshot
        initial_msg = self._take_initial_screenshot()
        return initial_msg

    def _take_initial_screenshot(self) -> str:
        """Take the first screenshot and build the initial message."""
        if self.state.pid > 0 and HAS_VISUAL_UTILS:
            path = str(
                Path(self.state.screenshot_dir) / "initial.png")
            err, ok = take_screenshot_window(self.state.pid, path)
            if ok:
                self.state.screenshot_counter += 1
                self.state.screenshots.append({
                    "step": 0, "path": path,
                    "description": "Initial state after launch",
                })
                self.state.last_screenshot = path
                # Return message - the image will be sent via _on_start
                # but we need to add it to backend manually since
                # BaseAgent.start() only sends text from _on_start
                self.backend_adapter.add_user_message(
                    "", image_path=path)
                return (f"Application launched (PID: {self.state.pid}). "
                        f"Initial screenshot taken. "
                        f"Describe what you see and begin testing.")

        if self.state.exe_path:
            return (f"Application launched (PID: {self.state.pid}) "
                    f"but screenshot failed. "
                    f"Use the screenshot tool to try again.")

        return ("No executable specified. "
                "Use screenshot tool to capture the current screen, "
                "or use action tools to interact.")

    def _is_done(self, response: str) -> bool:
        return self._done_flag

    def _on_finish(self):
        """Kill the process if we own it."""
        if (self.state.owns_process and self._process is not None
                and HAS_VISUAL_UTILS):
            try:
                kill_process(self._process)
            except Exception:
                pass

    # --- Helper: auto-screenshot after actions ---

    def _auto_screenshot(self, tool_name: str) -> tuple[str, str] | None:
        """Take auto-screenshot after an action tool. Returns (text, path) or None."""
        if not self.state.auto_screenshot:
            return None
        if self.state.pid <= 0:
            return None
        if not HAS_VISUAL_UTILS:
            return None

        self.state.screenshot_counter += 1
        path = str(Path(self.state.screenshot_dir)
                    / f"auto_{self.state.screenshot_counter:03d}_{tool_name}.png")
        try:
            err, ok = take_screenshot_window(self.state.pid, path)
            if ok:
                self.state.screenshots.append({
                    "step": self.state.current_step,
                    "path": path,
                    "description": f"Auto-screenshot after {tool_name}",
                })
                self.state.last_screenshot = path
                return (path, path)
        except Exception:
            pass
        return None

    # --- Perception tools ---

    def _tool_screenshot(self, description: str = "") -> tuple[str, str] | str:
        """Capture the application window."""
        if not HAS_VISUAL_UTILS:
            return "Error: visual_check_utils not available"

        self.state.screenshot_counter += 1
        path = str(Path(self.state.screenshot_dir)
                    / f"step_{self.state.screenshot_counter:03d}.png")

        if self.state.pid > 0:
            err, ok = take_screenshot_window(self.state.pid, path)
        else:
            err, ok = take_screenshot(path)

        if not ok:
            return f"Screenshot failed: {err}"

        desc = description or f"Screenshot #{self.state.screenshot_counter}"
        self.state.screenshots.append({
            "step": self.state.current_step,
            "path": path,
            "description": desc,
        })
        self.state.last_screenshot = path
        return (f"Screenshot captured: {desc}", path)

    def _tool_compare(self, path1: str = "", path2: str = "") -> str:
        """Compare two screenshots for visual difference."""
        if not HAS_VISUAL_UTILS:
            return "Error: visual_check_utils not available"

        # Default to last two screenshots
        if not path1 or not path2:
            if len(self.state.screenshots) < 2:
                return "Error: Need at least 2 screenshots to compare"
            path1 = self.state.screenshots[-2]["path"]
            path2 = self.state.screenshots[-1]["path"]

        changed, ratio = compare_screenshots(path1, path2)
        status = "CHANGED" if changed else "IDENTICAL"
        return (f"Comparison: {status} (difference: {ratio:.3f}). "
                f"Compared: {Path(path1).name} vs {Path(path2).name}")

    # --- Action tools ---

    def _tool_click(self, x: int, y: int,
                     button: str = "left") -> tuple[str, str] | str:
        if not HAS_PYAUTOGUI:
            return "Error: pyautogui not available"
        pyautogui.click(x, y, button=button)
        text = f"Clicked at ({x}, {y}) with {button} button"
        auto = self._auto_screenshot("click")
        if auto:
            return (text, auto[1])
        return text

    def _tool_type_text(self, text: str,
                         interval: float = 0.02) -> tuple[str, str] | str:
        if not HAS_PYAUTOGUI:
            return "Error: pyautogui not available"
        pyautogui.write(text, interval=interval)
        result = f"Typed: '{text[:50]}{'...' if len(text) > 50 else ''}'"
        auto = self._auto_screenshot("type")
        if auto:
            return (result, auto[1])
        return result

    def _tool_press_key(self, key: str) -> tuple[str, str] | str:
        if not HAS_PYAUTOGUI:
            return "Error: pyautogui not available"
        pyautogui.press(key)
        result = f"Pressed key: {key}"
        auto = self._auto_screenshot("key")
        if auto:
            return (result, auto[1])
        return result

    def _tool_hotkey(self, keys: str) -> tuple[str, str] | str:
        if not HAS_PYAUTOGUI:
            return "Error: pyautogui not available"
        key_list = [k.strip() for k in keys.split("+")]
        pyautogui.hotkey(*key_list)
        result = f"Pressed hotkey: {keys}"
        auto = self._auto_screenshot("hotkey")
        if auto:
            return (result, auto[1])
        return result

    def _tool_scroll(self, amount: int) -> tuple[str, str] | str:
        if not HAS_PYAUTOGUI:
            return "Error: pyautogui not available"
        pyautogui.scroll(amount)
        direction = "up" if amount > 0 else "down"
        result = f"Scrolled {direction} by {abs(amount)}"
        auto = self._auto_screenshot("scroll")
        if auto:
            return (result, auto[1])
        return result

    def _tool_drag(self, x: int, y: int,
                    duration: float = 0.5) -> tuple[str, str] | str:
        if not HAS_PYAUTOGUI:
            return "Error: pyautogui not available"
        pyautogui.moveTo(x, y, duration=duration)
        result = f"Dragged to ({x}, {y})"
        auto = self._auto_screenshot("drag")
        if auto:
            return (result, auto[1])
        return result

    def _tool_wait(self, seconds: float = 1.0) -> str:
        """Wait - no auto-screenshot (caller should screenshot explicitly)."""
        time.sleep(min(seconds, 10.0))  # Cap at 10s
        return f"Waited {seconds:.1f}s"

    # --- Verification tools ---

    def _tool_run_command(self, command: str,
                          expected: str = "") -> str:
        if not HAS_VISUAL_UTILS:
            return "Error: visual_check_utils not available"
        success, output = run_output_test(
            command, expected if expected else None)
        status = "PASS" if success else "FAIL"
        return f"[{status}] {output[:500]}"

    def _tool_http_check(self, url: str, status: int = 200,
                          contains: str = "") -> str:
        if not HAS_VISUAL_UTILS:
            return "Error: visual_check_utils not available"
        success, body = run_http_test(
            url, status, contains if contains else None)
        result_status = "PASS" if success else "FAIL"
        return f"[{result_status}] {body[:500]}"

    def _tool_file_check(self, path: str, exists: bool = True,
                          contains: str = "") -> str:
        if not HAS_VISUAL_UTILS:
            return "Error: visual_check_utils not available"
        success, detail = run_file_test(
            path, exists, contains if contains else None)
        status = "PASS" if success else "FAIL"
        return f"[{status}] {detail[:500]}"

    def _tool_assert_changed(self, threshold: float = 0.02) -> str:
        """Verify the last action had a visible effect."""
        if len(self.state.screenshots) < 2:
            return "FAIL: Need at least 2 screenshots to compare"
        if not HAS_VISUAL_UTILS:
            return "Error: visual_check_utils not available"

        path1 = self.state.screenshots[-2]["path"]
        path2 = self.state.screenshots[-1]["path"]
        changed, ratio = compare_screenshots(path1, path2, threshold)

        if changed:
            return (f"PASS: Screen changed (difference: {ratio:.3f}). "
                    f"Action had visible effect.")
        return (f"FAIL: Screen unchanged (difference: {ratio:.3f}). "
                f"Action had no visible effect.")

    # --- Session tools ---

    def _tool_log_finding(self, description: str,
                           severity: str = "info") -> str:
        evidence = [self.state.last_screenshot] if self.state.last_screenshot else []
        self.state.log_finding(description, evidence=evidence,
                               severity=severity)
        return f"Finding logged: [{severity}] {description[:100]}"

    def _tool_mark_criterion(self, criterion_id: str, status: str,
                              evidence: str = "",
                              reasoning: str = "") -> str:
        if status not in ("pass", "fail", "skip"):
            return f"Error: status must be pass, fail, or skip (got '{status}')"

        for c in self.state.criteria:
            if c.get("id") == criterion_id:
                c["status"] = status
                c["attempts"] = c.get("attempts", 0) + 1
                c["reasoning"] = reasoning
                if evidence:
                    c["evidence"] = evidence
                elif self.state.last_screenshot:
                    c["evidence"] = self.state.last_screenshot
                return (f"Criterion {criterion_id} marked as {status.upper()}"
                        f"{': ' + reasoning[:100] if reasoning else ''}")

        return f"Error: Criterion '{criterion_id}' not found"

    def _tool_done(self, summary: str = "") -> str:
        self._done_flag = True
        self.state.stop_reason = "done"
        if summary:
            self.state.log_finding(
                f"Session complete: {summary}", severity="info")
        return "Testing complete. Generating report."


# ---------------------------------------------------------------------------
# Report extension
# ---------------------------------------------------------------------------

def build_visual_report(state: VisualAgentState) -> dict:
    """Build report with visual-specific data."""
    base = ReportBuilder.build(state)
    base.update({
        "exe_path": state.exe_path,
        "operating_mode": state.operating_mode,
        "screenshots_count": state.screenshot_counter,
        "screenshots": state.screenshots,
        "criteria": state.criteria,
        "criteria_summary": {
            "total": len(state.criteria),
            "pass": sum(1 for c in state.criteria
                        if c.get("status") == "pass"),
            "fail": sum(1 for c in state.criteria
                        if c.get("status") == "fail"),
            "pending": sum(1 for c in state.criteria
                           if c.get("status") == "pending"),
            "skip": sum(1 for c in state.criteria
                        if c.get("status") == "skip"),
        },
    })
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="VisualAgent - Autonomous QA Agent")
    parser.add_argument("--exe", default="",
                        help="Path to application executable")
    parser.add_argument("--build-cmd", default="",
                        help="Build command to run before testing")
    parser.add_argument("--criteria", default="",
                        help="Path to criteria JSON file")
    parser.add_argument("--backend", required=True,
                        choices=["anthropic", "ollama", "openai",
                                 "claude", "session"])
    parser.add_argument("--model", default="")
    parser.add_argument("--mode", default="autonomous",
                        choices=["autonomous", "interactive",
                                 "criteria", "explore"])
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--screenshot-dir", default="screenshots")

    args = parser.parse_args()

    # Load criteria from file if provided
    criteria = []
    if args.criteria:
        try:
            data = json.loads(Path(args.criteria).read_text(encoding="utf-8"))
            criteria = data if isinstance(data, list) else data.get("criteria", [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Failed to load criteria: {e}")

    agent = VisualAgent(
        backend=args.backend,
        model=args.model,
        mode=args.mode,
    )
    agent.state.timeout_seconds = args.timeout

    report = agent.run(
        exe=args.exe,
        build_cmd=args.build_cmd,
        criteria=criteria,
        mode=args.mode,
        screenshot_dir=args.screenshot_dir,
    )

    print("\n" + "=" * 60)
    print("VISUAL AGENT REPORT")
    print("=" * 60)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
