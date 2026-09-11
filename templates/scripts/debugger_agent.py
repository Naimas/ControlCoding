#!/usr/bin/env python3
"""debugger_agent.py - Hypothesis-first bug diagnosis agent.

Follows the L1 debug protocol (CC methodology Section 9.3.1):
form hypothesis BEFORE tweaking parameters, test systematically,
narrow down the root cause.

Design document: dev/design/07_DSN_DebuggerAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S9)
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, BaseAgent, BackendAdapter, ToolExecutor,
    ReportBuilder, ToolCall, ToolResult,
)

BLOCKED_COMMANDS = {"rm", "mv", "cp", "chmod", "chown", "del", "move", "ren"}
VALID_CONFIDENCE = ("high", "medium", "low")


# ---------------------------------------------------------------------------
# DebuggerState
# ---------------------------------------------------------------------------

@dataclass
class DebuggerState(AgentStateBase):
    """Deterministic state for the Debugger Agent."""
    agent_type: str = "debugger"

    # Bug context (from Concierge)
    bug_description: str = ""
    error_output: str = ""
    stack_trace: str = ""
    already_tried: list = field(default_factory=list)
    relevant_files: list = field(default_factory=list)

    # Hypotheses
    hypotheses: list = field(default_factory=list)
    # Each: {id, hypothesis, rationale, status, test_result}
    # status: untested | validated | invalidated

    # Investigation
    observations: list = field(default_factory=list)

    # Diagnosis
    root_cause: str = ""
    suggested_fix: str = ""
    confidence: str = ""  # high | medium | low

    def to_context_summary(self) -> str:
        lines = [
            f"=== DEBUGGER STATE SUMMARY (step {self.current_step}) ===",
            f"Bug: {self.bug_description[:200]}",
        ]
        if self.hypotheses:
            lines.append(f"\nHypotheses ({len(self.hypotheses)}):")
            for h in self.hypotheses:
                status = h.get("status", "untested")
                lines.append(
                    f"  [{status}] H{h.get('id', '?')}: "
                    f"{h.get('hypothesis', '?')[:100]}")
        if self.observations:
            lines.append(
                f"\nObservations: {len(self.observations)}")
            for o in self.observations[-3:]:
                lines.append(f"  - {o[:100]}")
        if self.root_cause:
            lines.append(
                f"\nDiagnosis [{self.confidence}]: {self.root_cause[:200]}")
            lines.append(f"Fix: {self.suggested_fix[:200]}")
        if self.errors:
            lines.append(
                f"Errors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END DEBUGGER STATE SUMMARY ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

DEBUGGER_SYSTEM_PROMPT = """\
{behavioral_rules}

## Identity

You are the Debugger Agent. You diagnose bugs using the hypothesis-first \
protocol.

NEVER randomly tweak parameters hoping something works. ALWAYS:
1. Read the error output and relevant code
2. Form a HYPOTHESIS about the root cause
3. Design a TEST for that hypothesis
4. Run the test
5. Based on the result: validate, invalidate, or refine
6. Repeat until you have a diagnosis with evidence

## Workflow

1. Read the bug description and error output
2. form_hypothesis: what you think is wrong and why
3. test_hypothesis: run a command that would prove/disprove it
4. If invalidated, form a new hypothesis
5. If validated, use diagnosis to report root cause and fix

## Confidence levels

- high: reproduced the bug, identified exact line/condition, fix is clear
- medium: strong evidence for root cause, but fix needs verification
- low: best guess based on available evidence, needs more investigation

## Tool call format

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

## Available tools

{tool_descriptions}
"""


def _build_debugger_prompt(state: DebuggerState,
                            tool_descriptions: str) -> str:
    parts = [DEBUGGER_SYSTEM_PROMPT.format(
        behavioral_rules=BaseAgent._base_behavioral_rules(),
        tool_descriptions=tool_descriptions)]

    if state.bug_description:
        parts.append(f"\n## Bug Report\n\n{state.bug_description}\n")
    if state.error_output:
        err_preview = state.error_output[:2000]
        parts.append(
            f"### Error output\n\n```\n{err_preview}\n```\n")
    if state.stack_trace:
        parts.append(
            f"### Stack trace\n\n```\n{state.stack_trace[:2000]}\n```\n")
    if state.already_tried:
        parts.append("### Already tried (do NOT repeat)\n")
        for t in state.already_tried:
            parts.append(f"- {t}")
        parts.append("")
    if state.relevant_files:
        parts.append(
            f"### Relevant files\n\n"
            f"{', '.join(state.relevant_files)}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# DebuggerAgent
# ---------------------------------------------------------------------------

class DebuggerAgent(BaseAgent):
    """Hypothesis-first bug diagnosis agent.

    Tools: read_file, run_command, form_hypothesis, test_hypothesis,
    read_logs, search_code, call_visual_agent, diagnosis.
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "autonomous"):
        self._done_flag = False
        self._project_root: Path | None = None
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> DebuggerState:
        return DebuggerState()

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_debugger_prompt(self.state, tool_desc)

    def _register_tools(self):
        self.tools.register(
            "read_file", self._tool_read_file,
            "Read project file (path, max_lines)")
        self.tools.register(
            "run_command", self._tool_run_command,
            "Run shell command (command, timeout)")
        self.tools.register(
            "form_hypothesis", self._tool_form_hypothesis,
            "Record hypothesis (hypothesis, rationale)")
        self.tools.register(
            "test_hypothesis", self._tool_test_hypothesis,
            "Test hypothesis (hypothesis_id, command, expected)")
        self.tools.register(
            "read_logs", self._tool_read_logs,
            "Read log file last N lines (path, tail_lines)")
        self.tools.register(
            "search_code", self._tool_search_code,
            "Grep pattern in codebase (pattern, path, file_type)")
        self.tools.register(
            "call_visual_agent", self._tool_call_visual_agent,
            "Request visual investigation (description)")
        self.tools.register(
            "diagnosis", self._tool_diagnosis,
            "Final diagnosis (root_cause, suggested_fix, confidence)")

    def _on_start(self, **context) -> str:
        project_root = context.get("project_root", ".")
        self._project_root = Path(project_root).resolve()

        self.state.bug_description = context.get(
            "bug_description", context.get("bug", ""))
        self.state.error_output = context.get("error_output", "")
        self.state.stack_trace = context.get("stack_trace", "")
        self.state.relevant_files = context.get(
            "relevant_files", [])

        tried = context.get("already_tried", [])
        if isinstance(tried, str):
            tried = [tried]
        self.state.already_tried = tried

        parts = [f"Bug investigation started."]
        if self.state.bug_description:
            parts.append(f"Bug: {self.state.bug_description[:200]}")
        if self.state.relevant_files:
            parts.append(
                f"Files: {', '.join(self.state.relevant_files[:5])}")
        parts.append(
            "Read error output and relevant code, then "
            "form_hypothesis before trying anything.")
        return " ".join(parts)

    def _is_done(self, response: str) -> bool:
        return self._done_flag

    # --- Tools ---

    def _tool_read_file(self, path: str,
                         max_lines: int = 500) -> str:
        if not self._project_root:
            return "Error: no project root"
        target = self._project_root / path
        if not target.exists():
            return f"Error: file not found: {path}"
        try:
            content = target.read_text(
                encoding="utf-8", errors="replace")
            lines = content.split("\n")
            if len(lines) > max_lines:
                content = "\n".join(lines[:max_lines]) + (
                    f"\n... (truncated at {max_lines})")
            return content
        except OSError as e:
            return f"Error: {e}"

    def _tool_run_command(self, command: str,
                           timeout: int = 60) -> str:
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word.lower() in BLOCKED_COMMANDS:
            return f"Error: '{first_word}' is blocked."
        cwd = str(self._project_root) if self._project_root else "."
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=min(timeout, 120), cwd=cwd)
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            lines = output.split("\n")
            if len(lines) > 500:
                output = "\n".join(lines[:500]) + "\n... (truncated)"
            self.state.observations.append(
                f"Command: {command[:80]} -> "
                f"{output[:100].strip()}")
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except OSError as e:
            return f"Error: {e}"

    def _tool_form_hypothesis(self, hypothesis: str,
                                rationale: str = "") -> str:
        """Record a hypothesis."""
        h_id = len(self.state.hypotheses) + 1
        entry = {
            "id": h_id,
            "hypothesis": hypothesis,
            "rationale": rationale,
            "status": "untested",
            "test_result": "",
            "step": self.state.current_step,
        }
        self.state.hypotheses.append(entry)
        self.state.log_finding(
            f"H{h_id}: {hypothesis[:100]}", severity="info")
        return f"Hypothesis H{h_id} recorded. Now test it."

    def _tool_test_hypothesis(self, hypothesis_id: int,
                                command: str,
                                expected: str = "") -> str:
        """Test a hypothesis by running a command."""
        h = None
        for entry in self.state.hypotheses:
            if entry.get("id") == hypothesis_id:
                h = entry
                break
        if h is None:
            return (f"Error: hypothesis H{hypothesis_id} not found. "
                    f"Use form_hypothesis first.")

        # Run the test command
        output = self._tool_run_command(command)

        # Check against expected
        if expected:
            matched = expected.lower() in output.lower()
            h["status"] = "validated" if matched else "invalidated"
            h["test_result"] = (
                f"Expected: {expected[:100]}, "
                f"Got: {output[:200].strip()}, "
                f"Match: {matched}")
        else:
            # No expected value - let the LLM interpret
            h["test_result"] = output[:500]
            h["status"] = "tested"

        self.state.log_finding(
            f"H{hypothesis_id} [{h['status']}]: {output[:100].strip()}",
            severity="info")

        return (f"H{hypothesis_id} result [{h['status']}]: "
                f"{output[:300].strip()}")

    def _tool_read_logs(self, path: str,
                          tail_lines: int = 100) -> str:
        """Read last N lines of a log file."""
        if not self._project_root:
            return "Error: no project root"
        target = self._project_root / path
        if not target.exists():
            return f"Error: log file not found: {path}"
        try:
            content = target.read_text(
                encoding="utf-8", errors="replace")
            lines = content.split("\n")
            tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
            result = "\n".join(tail)
            self.state.observations.append(
                f"Log {path}: {len(tail)} lines read")
            return result
        except OSError as e:
            return f"Error: {e}"

    def _tool_search_code(self, pattern: str, path: str = ".",
                            file_type: str = "") -> str:
        """Grep for pattern in codebase."""
        if not self._project_root:
            return "Error: no project root"
        target = self._project_root / path
        cmd = f'grep -rn "{pattern}" "{target}"'
        if file_type:
            cmd += f' --include="*.{file_type}"'
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=str(self._project_root))
            output = result.stdout
            lines = output.split("\n")
            if len(lines) > 100:
                output = "\n".join(lines[:100]) + (
                    f"\n... ({len(lines)} matches, showing 100)")
            self.state.observations.append(
                f"Search '{pattern}': {len(lines)} matches")
            return output or f"No matches for '{pattern}'"
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"Error: {e}"

    def _tool_call_visual_agent(self, description: str) -> str:
        """Placeholder for visual investigation."""
        self.state.observations.append(
            f"Visual investigation requested: {description[:100]}")
        return (f"[Visual Agent not spawned in this context] "
                f"Requested: {description[:200]}. "
                f"Use run_command or read_file to investigate further.")

    def _tool_diagnosis(self, root_cause: str,
                          suggested_fix: str,
                          confidence: str = "medium") -> str:
        """Final diagnosis."""
        confidence = confidence.lower()
        if confidence not in VALID_CONFIDENCE:
            confidence = "medium"

        self.state.root_cause = root_cause
        self.state.suggested_fix = suggested_fix
        self.state.confidence = confidence
        self._done_flag = True

        # Count hypothesis stats
        validated = sum(1 for h in self.state.hypotheses
                        if h.get("status") == "validated")
        invalidated = sum(1 for h in self.state.hypotheses
                          if h.get("status") == "invalidated")

        self.state.log_finding(
            f"Diagnosis [{confidence}]: {root_cause[:200]}",
            severity="bug")

        return (f"Diagnosis complete [{confidence}]. "
                f"Hypotheses: {validated} validated, "
                f"{invalidated} invalidated, "
                f"{len(self.state.hypotheses)} total.")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_debugger_report(state: DebuggerState) -> dict:
    base = ReportBuilder.build(state)
    base["bug_description"] = state.bug_description
    base["hypotheses"] = state.hypotheses
    base["hypothesis_count"] = len(state.hypotheses)
    base["validated_count"] = sum(
        1 for h in state.hypotheses
        if h.get("status") == "validated")
    base["invalidated_count"] = sum(
        1 for h in state.hypotheses
        if h.get("status") == "invalidated")
    base["observations_count"] = len(state.observations)
    base["root_cause"] = state.root_cause
    base["suggested_fix"] = state.suggested_fix
    base["confidence"] = state.confidence
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="DebuggerAgent - hypothesis-first diagnosis")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--bug", default="", help="Bug description")
    parser.add_argument("--error", default="", help="Error output")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    agent = DebuggerAgent(backend=args.backend, model=args.model)
    report = agent.run(
        project_root=args.project_root,
        bug_description=args.bug,
        error_output=args.error,
    )
    if args.output:
        ReportBuilder.write(report, args.output)
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
