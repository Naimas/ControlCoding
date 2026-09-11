#!/usr/bin/env python3
"""expert_agent.py - Configurable domain specialist agent.

The ExpertAgent is spawned with a domain (security, performance,
volcanology, finance, etc.) and analyzes the project from that
domain's perspective. Produces structured findings and recommendations.

Predefined domains map to existing auditor profiles. Custom domains
use the LLM's training knowledge.

Design document: dev/design/09_DSN_ExpertAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S11)
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
VALID_PRIORITIES = ("critical", "high", "medium", "low")
VALID_EFFORTS = ("trivial", "small", "medium", "large")

# Predefined domain prompts
DOMAIN_PROMPTS = {
    "security": (
        "You are a Security Expert. Focus on:\n"
        "- OWASP Top 10 vulnerabilities\n"
        "- Authentication and authorization flaws\n"
        "- Injection risks (SQL, command, XSS)\n"
        "- Secrets exposure (API keys, passwords in code)\n"
        "- Cryptographic weaknesses\n"
        "- Input validation gaps\n"
    ),
    "engineering": (
        "You are a Software Engineering Expert. Focus on:\n"
        "- Code quality and maintainability\n"
        "- Design patterns and anti-patterns\n"
        "- Coupling and cohesion\n"
        "- Error handling completeness\n"
        "- Test coverage adequacy\n"
        "- Naming conventions and readability\n"
    ),
    "design-intent": (
        "You are a Design Intent Auditor. Focus on:\n"
        "- Does the implementation match the design document?\n"
        "- Are all specified features present?\n"
        "- Are constraints from the design respected?\n"
        "- Are there deviations that need justification?\n"
        "- Is the design document still accurate?\n"
    ),
    "methodology": (
        "You are a Methodology Compliance Expert. Focus on:\n"
        "- Are CC hooks properly configured?\n"
        "- Are protected zones enforced?\n"
        "- Is the commit ceremony followed?\n"
        "- Are design docs and plans up to date?\n"
        "- Is the devlog maintained?\n"
        "- Are tests adequate for the changes?\n"
    ),
    "performance": (
        "You are a Performance Expert. Focus on:\n"
        "- Algorithmic complexity (O(n) analysis)\n"
        "- Memory allocation patterns\n"
        "- I/O bottlenecks (disk, network, DB)\n"
        "- Unnecessary computation or redundant work\n"
        "- Caching opportunities\n"
        "- Concurrency issues\n"
    ),
    "accessibility": (
        "You are an Accessibility Expert. Focus on:\n"
        "- WCAG 2.1 AA compliance\n"
        "- Keyboard navigation completeness\n"
        "- Screen reader compatibility\n"
        "- Color contrast ratios\n"
        "- ARIA attributes usage\n"
        "- Focus management\n"
    ),
}


# ---------------------------------------------------------------------------
# ExpertState
# ---------------------------------------------------------------------------

@dataclass
class ExpertState(AgentStateBase):
    """Deterministic state for the Expert Agent."""
    agent_type: str = "expert"

    # Domain config
    domain: str = ""
    question: str = ""
    context: str = ""

    # Analysis
    analysis_findings: list = field(default_factory=list)
    # Each: {topic, finding, severity, location, step}

    # Recommendations
    recommendations: list = field(default_factory=list)
    # Each: {recommendation, priority, effort, step}

    # Communication
    questions_asked: list = field(default_factory=list)

    def to_context_summary(self) -> str:
        lines = [
            f"=== EXPERT STATE SUMMARY (step {self.current_step}) ===",
            f"Domain: {self.domain} | Mode: {self.mode}",
            f"Question: {self.question[:200]}",
        ]
        if self.analysis_findings:
            lines.append(
                f"\nFindings ({len(self.analysis_findings)}):")
            for f in self.analysis_findings[-5:]:
                lines.append(
                    f"  [{f.get('severity', '?')}] "
                    f"{f.get('topic', '?')}: "
                    f"{f.get('finding', '?')[:100]}")
        if self.recommendations:
            lines.append(
                f"\nRecommendations ({len(self.recommendations)}):")
            for r in self.recommendations[-5:]:
                lines.append(
                    f"  [{r.get('priority', '?')}] "
                    f"{r.get('recommendation', '?')[:100]}")
        if self.errors:
            lines.append(
                f"Errors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END EXPERT STATE SUMMARY ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

EXPERT_SYSTEM_PROMPT = """\
{behavioral_rules}

## Identity

{domain_prompt}

You analyze code and project artifacts from the perspective of your \
domain expertise. Produce structured findings with severity and \
actionable recommendations with priority.

## Workflow

1. Read the question and relevant files
2. Analyze from your domain's perspective
3. Record each finding with analyze (severity + location)
4. Produce recommendations with recommend (priority + effort)
5. Call done when analysis is complete

## Tool call format

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

## Available tools

{tool_descriptions}
"""


def _build_expert_prompt(state: ExpertState,
                          tool_descriptions: str) -> str:
    domain_prompt = DOMAIN_PROMPTS.get(
        state.domain,
        f"You are a {state.domain} Expert. Analyze the project from "
        f"the perspective of {state.domain}. Apply your domain knowledge "
        f"to identify issues, risks, and improvement opportunities.\n")

    parts = [EXPERT_SYSTEM_PROMPT.format(
        behavioral_rules=BaseAgent._base_behavioral_rules(),
        domain_prompt=domain_prompt,
        tool_descriptions=tool_descriptions)]

    if state.question:
        parts.append(f"\n## Question\n\n{state.question}\n")
    if state.context:
        parts.append(f"### Context\n\n{state.context}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# ExpertAgent
# ---------------------------------------------------------------------------

class ExpertAgent(BaseAgent):
    """Configurable domain specialist.

    Tools: read_file, run_command, search_code, analyze, recommend,
    ask_question, done.
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "autonomous", domain: str = "engineering"):
        self._done_flag = False
        self._project_root: Path | None = None
        self._domain = domain
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> ExpertState:
        return ExpertState(domain=self._domain)

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_expert_prompt(self.state, tool_desc)

    def _register_tools(self):
        self.tools.register(
            "read_file", self._tool_read_file,
            "Read project file (path, max_lines)")
        self.tools.register(
            "run_command", self._tool_run_command,
            "Read-only shell command (command, timeout)")
        self.tools.register(
            "search_code", self._tool_search_code,
            "Grep pattern in codebase (pattern, path, file_type)")
        self.tools.register(
            "analyze", self._tool_analyze,
            "Record finding (topic, finding, severity, location)")
        self.tools.register(
            "recommend", self._tool_recommend,
            "Produce recommendation (recommendation, priority, effort)")
        self.tools.register(
            "ask_question", self._tool_ask_question,
            "Ask caller for clarification (question)")
        self.tools.register(
            "done", self._tool_done,
            "Analysis complete (summary)")

    def _on_start(self, **context) -> str:
        project_root = context.get("project_root", ".")
        self._project_root = Path(project_root).resolve()

        self.state.domain = context.get("domain", self._domain)
        self.state.question = context.get("question", "")
        self.state.context = context.get("context", "")

        parts = [f"Expert consultation started. Domain: {self.state.domain}."]
        if self.state.question:
            parts.append(f"Question: {self.state.question[:200]}")
        parts.append("Begin analysis.")
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
                           timeout: int = 30) -> str:
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word.lower() in BLOCKED_COMMANDS:
            return f"Error: '{first_word}' is blocked."
        cwd = str(self._project_root) if self._project_root else "."
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=min(timeout, 60), cwd=cwd)
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            lines = output.split("\n")
            if len(lines) > 300:
                output = "\n".join(lines[:300]) + "\n... (truncated)"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except OSError as e:
            return f"Error: {e}"

    def _tool_search_code(self, pattern: str, path: str = ".",
                            file_type: str = "") -> str:
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
                    f"\n... ({len(lines)} matches)")
            return output or f"No matches for '{pattern}'"
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"Error: {e}"

    def _tool_analyze(self, topic: str, finding: str,
                        severity: str = "medium",
                        location: str = "") -> str:
        """Record a structured analysis finding."""
        severity = severity.lower()
        if severity not in VALID_SEVERITIES:
            return (f"Error: severity must be one of "
                    f"{VALID_SEVERITIES}. Got: {severity}")

        entry = {
            "topic": topic,
            "finding": finding,
            "severity": severity,
            "location": location,
            "step": self.state.current_step,
        }
        self.state.analysis_findings.append(entry)

        sev_map = {"critical": "bug", "high": "bug",
                    "medium": "warning", "low": "info", "info": "info"}
        self.state.log_finding(
            f"[{self.state.domain}:{severity}] {topic}: {finding[:100]}",
            severity=sev_map.get(severity, "info"))

        return f"Finding recorded [{severity}]: {topic}"

    def _tool_recommend(self, recommendation: str,
                          priority: str = "medium",
                          effort: str = "medium") -> str:
        """Produce an actionable recommendation."""
        priority = priority.lower()
        effort = effort.lower()
        if priority not in VALID_PRIORITIES:
            return (f"Error: priority must be one of "
                    f"{VALID_PRIORITIES}. Got: {priority}")
        if effort not in VALID_EFFORTS:
            return (f"Error: effort must be one of "
                    f"{VALID_EFFORTS}. Got: {effort}")

        entry = {
            "recommendation": recommendation,
            "priority": priority,
            "effort": effort,
            "step": self.state.current_step,
        }
        self.state.recommendations.append(entry)

        self.state.log_finding(
            f"Recommend [{priority}/{effort}]: {recommendation[:100]}",
            severity="info")

        return (f"Recommendation recorded [{priority}, {effort}]: "
                f"{recommendation[:80]}")

    def _tool_ask_question(self, question: str) -> str:
        """Ask caller for clarification."""
        self.state.questions_asked.append(question)
        if self.mode == "autonomous":
            return ("No clarification available in autonomous mode. "
                    "Proceed with best judgment.")
        return f"[Question logged: {question}]"

    def _tool_done(self, summary: str = "") -> str:
        """Analysis complete."""
        self._done_flag = True
        if summary:
            self.state.log_finding(
                f"Expert analysis complete: {summary}", severity="info")
        return "Done. Generating report."


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_expert_report(state: ExpertState) -> dict:
    base = ReportBuilder.build(state)
    base["domain"] = state.domain
    base["question"] = state.question
    base["analysis_findings"] = state.analysis_findings
    base["findings_count"] = len(state.analysis_findings)
    base["critical_findings"] = sum(
        1 for f in state.analysis_findings
        if f.get("severity") == "critical")
    base["recommendations"] = state.recommendations
    base["recommendations_count"] = len(state.recommendations)
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="ExpertAgent - domain specialist")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--domain", default="engineering",
                        help="Expert domain")
    parser.add_argument("--question", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    agent = ExpertAgent(
        backend=args.backend, model=args.model, domain=args.domain)
    report = agent.run(
        project_root=args.project_root,
        domain=args.domain,
        question=args.question,
    )
    if args.output:
        ReportBuilder.write(report, args.output)
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
