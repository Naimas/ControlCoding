#!/usr/bin/env python3
"""reviewer_agent.py - Code review and criteria verification agent.

The ReviewerAgent evaluates code quality, checks criteria compliance,
and finds bugs. Pure analysis - does NOT modify code.

Design document: dev/design/06_DSN_ReviewerAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S8)
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
VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")
VALID_VERDICTS = ("pass", "fail", "partial")


# ---------------------------------------------------------------------------
# ReviewerState
# ---------------------------------------------------------------------------

@dataclass
class ReviewerState(AgentStateBase):
    """Deterministic state for the Reviewer Agent."""
    agent_type: str = "reviewer"

    # Scope (from Concierge)
    review_scope: str = ""
    files_to_review: list = field(default_factory=list)
    diff: str = ""
    criteria: list = field(default_factory=list)
    architecture_rules: str = ""

    # Findings
    issues: list = field(default_factory=list)
    criteria_results: list = field(default_factory=list)

    # Verdict
    verdict: str = ""
    verdict_reasoning: str = ""
    blocking_count: int = 0
    warning_count: int = 0

    def to_context_summary(self) -> str:
        lines = [
            f"=== REVIEWER STATE SUMMARY (step {self.current_step}) ===",
            f"Agent: {self.agent_type} | Mode: {self.mode} | "
            f"Phase: {self.phase}",
            f"Scope: {self.review_scope[:200]}",
            f"Files: {', '.join(self.files_to_review[:10])}",
        ]
        if self.criteria:
            lines.append(f"Criteria: {len(self.criteria)}")
        if self.criteria_results:
            passed = sum(1 for r in self.criteria_results
                         if r.get("status") == "pass")
            failed = sum(1 for r in self.criteria_results
                         if r.get("status") == "fail")
            lines.append(
                f"Criteria checked: {passed} pass, {failed} fail")
        if self.issues:
            lines.append(
                f"Issues: {len(self.issues)} "
                f"({self.blocking_count} blocking, "
                f"{self.warning_count} warning)")
            for i in self.issues[-5:]:
                lines.append(
                    f"  [{i.get('severity', '?')}] "
                    f"{i.get('location', '?')}: "
                    f"{i.get('issue', '?')[:100]}")
        if self.verdict:
            lines.append(f"Verdict: {self.verdict}")
        if self.errors:
            lines.append(
                f"Errors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END REVIEWER STATE SUMMARY ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM_PROMPT = """\
{behavioral_rules}

## Identity

You are the Reviewer Agent - a code quality evaluator.

You read code and diffs, check acceptance criteria, find bugs and \
issues. You do NOT modify code. You analyze and judge.

## Workflow

1. Read the files and/or diff to understand the changes
2. For each acceptance criterion, use check_criterion to mark pass/fail
3. Log any issues found with log_issue (severity + location + suggestion)
4. When done, use verdict to give your overall judgment

## Severity levels for log_issue

- critical: security vulnerability, data loss risk, crash
- high: incorrect behavior, broken functionality
- medium: code smell, maintainability concern, edge case
- low: style issue, minor improvement opportunity
- info: observation, not an issue

## Tool call format

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

## Available tools

{tool_descriptions}
"""


def _build_reviewer_prompt(state: ReviewerState,
                            tool_descriptions: str) -> str:
    parts = [REVIEWER_SYSTEM_PROMPT.format(
        behavioral_rules=BaseAgent._base_behavioral_rules(),
        tool_descriptions=tool_descriptions)]

    if state.review_scope:
        parts.append(f"\n## Review Scope\n\n{state.review_scope}\n")
    if state.files_to_review:
        parts.append("### Files to review\n")
        for f in state.files_to_review:
            parts.append(f"- {f}")
        parts.append("")
    if state.criteria:
        parts.append("### Acceptance criteria to check\n")
        for i, c in enumerate(state.criteria):
            ctext = c if isinstance(c, str) else c.get("text", str(c))
            parts.append(f"{i+1}. {ctext}")
        parts.append("")
    if state.architecture_rules:
        parts.append(
            f"### Architecture rules\n\n{state.architecture_rules}\n")
    if state.diff:
        diff_preview = state.diff[:3000]
        if len(state.diff) > 3000:
            diff_preview += "\n... (diff truncated in prompt, "
            diff_preview += "use read_diff tool for full content)"
        parts.append(f"### Diff preview\n\n```diff\n{diff_preview}\n```\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# ReviewerAgent
# ---------------------------------------------------------------------------

class ReviewerAgent(BaseAgent):
    """Code review agent. Pure analysis, no modifications.

    Tools: read_file, read_diff, check_criterion, log_issue,
    run_tests, run_command, verdict.
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "autonomous"):
        self._done_flag = False
        self._project_root: Path | None = None
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> ReviewerState:
        return ReviewerState()

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_reviewer_prompt(self.state, tool_desc)

    def _register_tools(self):
        self.tools.register(
            "read_file", self._tool_read_file,
            "Read a project file (path, max_lines)")
        self.tools.register(
            "read_diff", self._tool_read_diff,
            "Read git diff (path, staged)")
        self.tools.register(
            "check_criterion", self._tool_check_criterion,
            "Mark criterion pass/fail "
            "(criterion, status, evidence, reasoning)")
        self.tools.register(
            "log_issue", self._tool_log_issue,
            "Record code issue "
            "(severity, location, issue, suggestion)")
        self.tools.register(
            "run_tests", self._tool_run_tests,
            "Run test suite (command, timeout)")
        self.tools.register(
            "run_command", self._tool_run_command,
            "Run read-only command (command, timeout)")
        self.tools.register(
            "verdict", self._tool_verdict,
            "Final judgment (status: pass|fail|partial, reasoning)")

    def _on_start(self, **context) -> str:
        project_root = context.get("project_root", ".")
        self._project_root = Path(project_root).resolve()

        self.state.review_scope = context.get("review_scope", "")
        self.state.files_to_review = context.get(
            "files_to_review", [])
        self.state.diff = context.get("diff", "")
        self.state.architecture_rules = context.get(
            "architecture_rules", "")

        criteria = context.get("criteria", [])
        if isinstance(criteria, str):
            try:
                criteria = json.loads(criteria)
            except json.JSONDecodeError:
                criteria = [criteria]
        self.state.criteria = criteria

        parts = [f"Review started: {self.state.review_scope or 'general review'}"]
        if self.state.files_to_review:
            parts.append(
                f"Files: {', '.join(self.state.files_to_review[:5])}")
        if self.state.criteria:
            parts.append(f"Criteria: {len(self.state.criteria)}")
        if self.state.diff:
            parts.append(f"Diff: {len(self.state.diff)} chars")
        parts.append("Begin review. Read files and check criteria.")
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
                    f"\n... (truncated at {max_lines} lines)")
            return content
        except OSError as e:
            return f"Error: {e}"

    def _tool_read_diff(self, path: str = "",
                          staged: bool = False) -> str:
        """Read git diff."""
        if not self._project_root:
            return "Error: no project root"
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        if path:
            cmd.extend(["--", path])
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=30, cwd=str(self._project_root))
            output = result.stdout
            if not output:
                return "(no diff)"
            lines = output.split("\n")
            if len(lines) > 500:
                output = "\n".join(lines[:500]) + "\n... (truncated)"
            return output
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"Error: {e}"

    def _tool_check_criterion(self, criterion: str, status: str,
                                evidence: str = "",
                                reasoning: str = "") -> str:
        """Mark a criterion pass/fail."""
        status = status.lower()
        if status not in ("pass", "fail", "skip"):
            return (f"Error: status must be pass, fail, or skip. "
                    f"Got: {status}")
        result = {
            "criterion": criterion,
            "status": status,
            "evidence": evidence,
            "reasoning": reasoning,
            "step": self.state.current_step,
        }
        self.state.criteria_results.append(result)
        self.state.log_finding(
            f"Criterion [{status}]: {criterion[:100]}",
            severity="pass" if status == "pass" else "fail")
        return f"Criterion '{criterion[:50]}' marked: {status}"

    def _tool_log_issue(self, severity: str, location: str,
                          issue: str, suggestion: str = "") -> str:
        """Record a code issue."""
        severity = severity.lower()
        if severity not in VALID_SEVERITIES:
            return (f"Error: severity must be one of "
                    f"{VALID_SEVERITIES}. Got: {severity}")
        entry = {
            "severity": severity,
            "location": location,
            "issue": issue,
            "suggestion": suggestion,
            "step": self.state.current_step,
        }
        self.state.issues.append(entry)

        if severity in ("critical", "high"):
            self.state.blocking_count += 1
        elif severity in ("medium", "low"):
            self.state.warning_count += 1

        self.state.log_finding(
            f"[{severity}] {location}: {issue[:100]}",
            severity="bug" if severity in ("critical", "high")
            else "warning")

        return (f"Issue logged: [{severity}] {location}: "
                f"{issue[:80]}")

    def _tool_run_tests(self, command: str = "",
                          timeout: int = 120) -> str:
        if not command:
            return "Error: test command required"
        cwd = str(self._project_root) if self._project_root else "."
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=min(timeout, 300), cwd=cwd)
            output = result.stdout + "\n" + result.stderr
            lines = output.split("\n")
            if len(lines) > 200:
                output = "\n".join(lines[:100]) + (
                    "\n...\n") + "\n".join(lines[-50:])
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except OSError as e:
            return f"Error: {e}"

    def _tool_run_command(self, command: str,
                           timeout: int = 30) -> str:
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word.lower() in BLOCKED_COMMANDS:
            return f"Error: '{first_word}' is blocked. Read-only."
        cwd = str(self._project_root) if self._project_root else "."
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=min(timeout, 60), cwd=cwd)
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            lines = output.split("\n")
            if len(lines) > 500:
                output = "\n".join(lines[:500]) + "\n... (truncated)"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except OSError as e:
            return f"Error: {e}"

    def _tool_verdict(self, status: str,
                        reasoning: str = "") -> str:
        """Final judgment."""
        status = status.lower()
        if status not in VALID_VERDICTS:
            return (f"Error: status must be one of "
                    f"{VALID_VERDICTS}. Got: {status}")

        self.state.verdict = status
        self.state.verdict_reasoning = reasoning
        self._done_flag = True

        self.state.log_finding(
            f"Verdict: {status} - {reasoning[:200]}",
            severity="pass" if status == "pass" else "fail")

        return f"Verdict: {status}. Review complete."


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_reviewer_report(state: ReviewerState) -> dict:
    base = ReportBuilder.build(state)
    base["review_scope"] = state.review_scope
    base["files_reviewed"] = state.files_to_review
    base["criteria_count"] = len(state.criteria)
    base["criteria_results"] = state.criteria_results
    base["issues"] = state.issues
    base["blocking_count"] = state.blocking_count
    base["warning_count"] = state.warning_count
    base["verdict"] = state.verdict
    base["verdict_reasoning"] = state.verdict_reasoning
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="ReviewerAgent - code review")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--scope", default="")
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    agent = ReviewerAgent(backend=args.backend, model=args.model)
    report = agent.run(
        project_root=args.project_root,
        review_scope=args.scope,
        files_to_review=args.files,
    )
    if args.output:
        ReportBuilder.write(report, args.output)
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
