#!/usr/bin/env python3
"""coder_agent.py - Worker agent that implements code changes.

The CoderAgent receives structured instructions from the Concierge
(via Architect's guide_coder output) and executes them: reads files,
writes/edits code, runs builds and tests, reports results.

Does NOT decide what to build or how to structure it. Executes.

Design document: dev/design/05_DSN_CoderAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S7)
"""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, BaseAgent, BackendAdapter, ToolExecutor,
    ReportBuilder, ToolCall, ToolResult,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_WRITE_SIZE = 100 * 1024  # 100KB max file write

BLOCKED_COMMANDS = {"rm", "mv", "cp", "chmod", "chown", "del", "move", "ren"}

# Test framework detection markers
TEST_FRAMEWORKS = [
    (["pytest.ini", "conftest.py", "pyproject.toml"],
     "pytest", "python -m pytest --tb=short -q"),
    (["package.json"], "npm", "npm test"),
    (["Cargo.toml"], "cargo", "cargo test"),
    (["go.mod"], "go", "go test ./..."),
]


# ---------------------------------------------------------------------------
# CoderState
# ---------------------------------------------------------------------------

@dataclass
class CoderState(AgentStateBase):
    """Deterministic state for the Coder Agent."""
    agent_type: str = "coder"

    # Task (from Concierge / Architect)
    task: str = ""
    approach: str = ""
    target_files: list = field(default_factory=list)
    patterns_to_follow: list = field(default_factory=list)
    patterns_to_avoid: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    acceptance_criteria: list = field(default_factory=list)
    estimated_complexity: str = "medium"

    # Progress
    files_modified: list = field(default_factory=list)
    files_created: list = field(default_factory=list)
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    build_success: bool | None = None

    # Communication
    questions_asked: list = field(default_factory=list)
    concierge_answers: list = field(default_factory=list)

    def to_context_summary(self) -> str:
        lines = [
            f"=== CODER STATE SUMMARY (step {self.current_step}) ===",
            f"Agent: {self.agent_type} | Mode: {self.mode} | "
            f"Phase: {self.phase}",
            f"Task: {self.task[:200]}",
        ]
        if self.approach:
            lines.append(f"Approach: {self.approach[:200]}")
        if self.target_files:
            lines.append(
                f"Target files: {', '.join(self.target_files[:10])}")
        if self.files_modified:
            lines.append(
                f"Modified: {', '.join(self.files_modified[:10])}")
        if self.files_created:
            lines.append(
                f"Created: {', '.join(self.files_created[:10])}")
        lines.append(
            f"Tests: {self.tests_run} run, {self.tests_passed} passed, "
            f"{self.tests_failed} failed")
        if self.constraints:
            lines.append("Constraints:")
            for c in self.constraints[:5]:
                lines.append(f"  - {c}")
        if self.acceptance_criteria:
            lines.append("Acceptance criteria:")
            for ac in self.acceptance_criteria[:10]:
                lines.append(f"  - {ac}")
        if self.errors:
            lines.append(
                f"\nRecent errors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END CODER STATE SUMMARY ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

CODER_SYSTEM_PROMPT = """\
{behavioral_rules}

## Identity

You are the Coder Agent - a worker that implements code changes per \
instructions.

You receive structured instructions with:
- task: what to implement
- approach: how to structure it
- files_to_touch: which files to modify
- patterns_to_follow / patterns_to_avoid
- constraints: hard rules you must not violate
- acceptance_criteria: what "done" looks like

Your job is to EXECUTE, not to DECIDE. Follow the instructions precisely.
If instructions are ambiguous, use ask_concierge to get clarification.
Do not make architectural decisions - that is the Architect's job.

## Workflow

1. Read the target files to understand current code
2. Implement the changes following the approach and patterns
3. Run tests after each significant change
4. If tests fail, fix the issue before moving on
5. Call done when all acceptance criteria are met

## Tool call format

Write a JSON block in a markdown fence:

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

## Available tools

{tool_descriptions}
"""


def _build_coder_prompt(state: CoderState,
                         tool_descriptions: str) -> str:
    parts = [CODER_SYSTEM_PROMPT.format(
        behavioral_rules=BaseAgent._base_behavioral_rules(),
        tool_descriptions=tool_descriptions)]

    # Task context
    if state.task:
        parts.append(f"\n## Current Task\n\n{state.task}\n")
    if state.approach:
        parts.append(f"### Approach\n\n{state.approach}\n")
    if state.target_files:
        parts.append("### Target files\n")
        for f in state.target_files:
            parts.append(f"- {f}")
        parts.append("")
    if state.patterns_to_follow:
        parts.append("### Patterns to follow\n")
        for p in state.patterns_to_follow:
            parts.append(f"- {p}")
        parts.append("")
    if state.patterns_to_avoid:
        parts.append("### Patterns to avoid\n")
        for p in state.patterns_to_avoid:
            parts.append(f"- {p}")
        parts.append("")
    if state.constraints:
        parts.append("### Constraints (MUST NOT violate)\n")
        for c in state.constraints:
            parts.append(f"- {c}")
        parts.append("")
    if state.acceptance_criteria:
        parts.append("### Acceptance criteria (all must pass)\n")
        for ac in state.acceptance_criteria:
            parts.append(f"- [ ] {ac}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CoderAgent
# ---------------------------------------------------------------------------

class CoderAgent(BaseAgent):
    """Worker agent that implements code changes.

    Tools: read_file, write_file, edit_file, run_command, run_tests,
    ask_concierge, list_files, done.
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "autonomous"):
        self._done_flag = False
        self._project_root: Path | None = None
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> CoderState:
        return CoderState()

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_coder_prompt(self.state, tool_desc)

    def _register_tools(self):
        self.tools.register(
            "read_file", self._tool_read_file,
            "Read a project file (path, max_lines)")
        self.tools.register(
            "write_file", self._tool_write_file,
            "Write/create a file (path, content)")
        self.tools.register(
            "edit_file", self._tool_edit_file,
            "Replace text in a file (path, old_text, new_text)")
        self.tools.register(
            "run_command", self._tool_run_command,
            "Run shell command (command, timeout)")
        self.tools.register(
            "run_tests", self._tool_run_tests,
            "Run test suite (command, timeout)")
        self.tools.register(
            "ask_concierge", self._tool_ask_concierge,
            "Ask Concierge for clarification (question)")
        self.tools.register(
            "list_files", self._tool_list_files,
            "List files in directory (path, pattern)")
        self.tools.register(
            "done", self._tool_done,
            "Task complete (summary)")

    def _on_start(self, **context) -> str:
        project_root = context.get("project_root", ".")
        self._project_root = Path(project_root).resolve()

        # Load guidance from Architect (guide_coder output)
        guidance = context.get("guidance", {})
        if isinstance(guidance, str):
            try:
                guidance = json.loads(guidance)
            except json.JSONDecodeError:
                guidance = {"task": guidance}

        self.state.task = guidance.get(
            "task", context.get("task", ""))
        self.state.approach = guidance.get("approach", "")
        self.state.target_files = guidance.get("files_to_touch", [])
        self.state.patterns_to_follow = guidance.get(
            "patterns_to_follow", [])
        self.state.patterns_to_avoid = guidance.get(
            "patterns_to_avoid", [])
        self.state.constraints = guidance.get("constraints", [])
        self.state.acceptance_criteria = guidance.get(
            "acceptance_criteria", [])
        self.state.estimated_complexity = guidance.get(
            "estimated_complexity", "medium")

        parts = [f"Task received: {self.state.task}"]
        if self.state.target_files:
            parts.append(
                f"Files to modify: {', '.join(self.state.target_files)}")
        if self.state.acceptance_criteria:
            parts.append(
                f"Criteria: {len(self.state.acceptance_criteria)}")
        parts.append("Begin implementation. Read target files first.")
        return " ".join(parts)

    def _is_done(self, response: str) -> bool:
        return self._done_flag

    # --- Tool implementations ---

    def _tool_read_file(self, path: str,
                         max_lines: int = 500) -> str:
        """Read a project file with higher default for full context."""
        if not self._project_root:
            return "Error: no project root set"
        target = self._project_root / path
        if not target.exists():
            return f"Error: file not found: {path}"
        try:
            resolved = target.resolve()
            if not str(resolved).startswith(
                    str(self._project_root)):
                return f"Error: path outside project: {path}"
        except (OSError, ValueError):
            pass
        try:
            content = target.read_text(
                encoding="utf-8", errors="replace")
            lines = content.split("\n")
            if len(lines) > max_lines:
                content = "\n".join(lines[:max_lines]) + (
                    f"\n... (truncated at {max_lines} lines, "
                    f"{len(lines)} total)")
            return content
        except OSError as e:
            return f"Error reading {path}: {e}"

    def _tool_write_file(self, path: str, content: str) -> str:
        """Write or create a file."""
        if not self._project_root:
            return "Error: no project root set"
        target = self._project_root / path
        try:
            resolved = target.resolve()
            if not str(resolved).startswith(
                    str(self._project_root)):
                return f"Error: path outside project: {path}"
        except (OSError, ValueError):
            pass

        if len(content.encode("utf-8")) > MAX_WRITE_SIZE:
            return (f"Error: content too large "
                    f"({len(content.encode('utf-8'))} bytes, "
                    f"max {MAX_WRITE_SIZE})")

        existed = target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"Error writing {path}: {e}"

        if existed:
            if path not in self.state.files_modified:
                self.state.files_modified.append(path)
        else:
            if path not in self.state.files_created:
                self.state.files_created.append(path)

        lines = content.count("\n") + 1
        return f"Written: {path} ({lines} lines)"

    def _tool_edit_file(self, path: str, old_text: str,
                          new_text: str) -> str:
        """Replace text in a file."""
        if not self._project_root:
            return "Error: no project root set"
        target = self._project_root / path
        if not target.exists():
            return f"Error: file not found: {path}"
        try:
            content = target.read_text(
                encoding="utf-8", errors="replace")
        except OSError as e:
            return f"Error reading {path}: {e}"

        if old_text not in content:
            return (f"Error: old_text not found in {path}. "
                    f"Read the file first to get exact content.")

        new_content = content.replace(old_text, new_text, 1)
        try:
            target.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return f"Error writing {path}: {e}"

        if path not in self.state.files_modified:
            self.state.files_modified.append(path)

        return f"Edited: {path} (replaced {len(old_text)} chars)"

    def _tool_run_command(self, command: str,
                           timeout: int = 60) -> str:
        """Run shell command."""
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word.lower() in BLOCKED_COMMANDS:
            return (f"Error: '{first_word}' is blocked. "
                    f"Use write_file/edit_file for modifications.")

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
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except OSError as e:
            return f"Error: {e}"

    def _tool_run_tests(self, command: str = "",
                          timeout: int = 120) -> str:
        """Run test suite. Auto-detects framework if no command given."""
        if not command and self._project_root:
            command = self._detect_test_command()
        if not command:
            return ("Error: no test command specified and no framework "
                    "detected. Provide command parameter.")

        cwd = str(self._project_root) if self._project_root else "."
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=min(timeout, 300), cwd=cwd)
            output = result.stdout + "\n" + result.stderr
        except subprocess.TimeoutExpired:
            output = f"Test suite timed out after {timeout}s"
        except OSError as e:
            output = f"Error running tests: {e}"

        # Parse results
        passed, failed, total = self._parse_test_output(output)
        self.state.tests_run += total
        self.state.tests_passed += passed
        self.state.tests_failed += failed

        # Cap output
        lines = output.split("\n")
        if len(lines) > 200:
            output = "\n".join(lines[:100]) + (
                "\n... (truncated) ...\n") + "\n".join(lines[-50:])

        return (f"Tests: {passed} passed, {failed} failed, "
                f"{total} total\n\n{output}")

    def _detect_test_command(self) -> str:
        """Auto-detect test framework from project files."""
        if not self._project_root:
            return ""
        for markers, _name, command in TEST_FRAMEWORKS:
            for marker in markers:
                if (self._project_root / marker).exists():
                    # Special: check package.json has test script
                    if marker == "package.json":
                        try:
                            pkg = json.loads(
                                (self._project_root / marker).read_text())
                            if "test" not in pkg.get("scripts", {}):
                                continue
                        except (json.JSONDecodeError, OSError):
                            continue
                    return command
        # Check for tests/ directory (pytest)
        if (self._project_root / "tests").is_dir():
            return "python -m pytest --tb=short -q"
        return ""

    def _parse_test_output(self, output: str) -> tuple[int, int, int]:
        """Parse test output for pass/fail counts."""
        passed = failed = 0
        # pytest format: "X passed, Y failed"
        m = re.search(r"(\d+)\s+passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            failed = int(m.group(1))
        # npm/generic: "X passing, Y failing"
        if not passed:
            m = re.search(r"(\d+)\s+passing", output)
            if m:
                passed = int(m.group(1))
        if not failed:
            m = re.search(r"(\d+)\s+failing", output)
            if m:
                failed = int(m.group(1))
        total = passed + failed
        return passed, failed, total

    def _tool_ask_concierge(self, question: str) -> str:
        """Ask the Concierge for clarification."""
        self.state.questions_asked.append(question)

        if self.mode == "autonomous":
            # In autonomous mode, check if guidance covers the question
            for c in self.state.constraints:
                if any(word in c.lower() for word in
                       question.lower().split()[:3]):
                    answer = f"Guidance says: {c}"
                    self.state.concierge_answers.append(answer)
                    return answer
            answer = ("No specific guidance for this question. "
                      "Proceed with best judgment based on constraints "
                      "and patterns provided.")
            self.state.concierge_answers.append(answer)
            return answer
        else:
            # Interactive mode: this would prompt the user
            return (f"[Question logged: {question}] "
                    f"Waiting for Concierge response.")

    def _tool_list_files(self, path: str = ".",
                           pattern: str = "") -> str:
        """List files in a directory."""
        if not self._project_root:
            return "Error: no project root set"
        target = self._project_root / path
        if not target.is_dir():
            return f"Error: not a directory: {path}"
        try:
            if pattern:
                files = sorted(target.glob(pattern))
            else:
                files = sorted(target.iterdir())
            lines = []
            for f in files[:200]:
                rel = f.relative_to(self._project_root)
                prefix = "d" if f.is_dir() else "f"
                lines.append(f"[{prefix}] {rel}")
            result = "\n".join(lines)
            if len(files) > 200:
                result += f"\n... ({len(files)} total, showing 200)"
            return result or "(empty directory)"
        except OSError as e:
            return f"Error: {e}"

    def _tool_done(self, summary: str = "") -> str:
        """Task complete."""
        self._done_flag = True
        if summary:
            self.state.log_finding(
                f"Task complete: {summary}", severity="info")
        return "Done. Generating report."


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_coder_report(state: CoderState) -> dict:
    """Extended report with Coder-specific data."""
    base = ReportBuilder.build(state)
    base["task"] = state.task
    base["files_modified"] = state.files_modified
    base["files_created"] = state.files_created
    base["tests_run"] = state.tests_run
    base["tests_passed"] = state.tests_passed
    base["tests_failed"] = state.tests_failed
    base["build_success"] = state.build_success
    base["acceptance_criteria"] = state.acceptance_criteria
    base["questions_asked"] = len(state.questions_asked)
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="CoderAgent - code implementation worker")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--guidance-file", default="",
                        help="JSON file with Architect guidance")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    guidance = {}
    if args.guidance_file:
        guidance = json.loads(
            Path(args.guidance_file).read_text(encoding="utf-8"))
    elif args.task:
        guidance = {"task": args.task}

    agent = CoderAgent(backend=args.backend, model=args.model)
    report = agent.run(
        project_root=args.project_root,
        guidance=guidance,
    )
    if args.output:
        ReportBuilder.write(report, args.output)
        print(f"Report: {args.output}")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
