#!/usr/bin/env python3
"""socratic_agent.py - Assumption challenger and risk exposer.

The SocraticAgent does NOT solve problems. It makes the thinker
(Architect, Concierge, user) think harder by asking hard questions,
probing answers, and identifying hidden risks.

Used as escalation when brainstorming gets stuck in loops or tunnel vision.

Design document: dev/design/08_DSN_SocraticAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S10)
"""

import json
import os
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
VALID_RISK_SEVERITY = ("critical", "high", "medium", "low")
VALID_RECOMMENDATIONS = ("proceed", "reconsider", "stop")


# ---------------------------------------------------------------------------
# SocraticState
# ---------------------------------------------------------------------------

@dataclass
class SocraticState(AgentStateBase):
    """Deterministic state for the Socratic Agent."""
    agent_type: str = "socratic"

    # Topic (from caller)
    topic: str = ""
    context: str = ""

    # Questioning
    questions: list = field(default_factory=list)
    # Each: {id, question, target, answer, followups}

    # Risks discovered
    risks: list = field(default_factory=list)
    # Each: {risk, severity, mitigation, step}

    # Outcome
    recommendation: str = ""   # proceed | reconsider | stop
    recommendation_reasoning: str = ""

    def to_context_summary(self) -> str:
        lines = [
            f"=== SOCRATIC STATE SUMMARY (step {self.current_step}) ===",
            f"Topic: {self.topic[:200]}",
        ]
        if self.questions:
            lines.append(f"\nQuestions asked ({len(self.questions)}):")
            for q in self.questions[-5:]:
                lines.append(
                    f"  Q{q.get('id', '?')}: {q.get('question', '?')[:100]}")
                if q.get("answer"):
                    lines.append(f"    A: {q['answer'][:100]}")
        if self.risks:
            lines.append(f"\nRisks identified ({len(self.risks)}):")
            for r in self.risks:
                lines.append(
                    f"  [{r.get('severity', '?')}] "
                    f"{r.get('risk', '?')[:100]}")
        if self.recommendation:
            lines.append(
                f"\nRecommendation: {self.recommendation} - "
                f"{self.recommendation_reasoning[:200]}")
        if self.errors:
            lines.append(
                f"Errors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END SOCRATIC STATE SUMMARY ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

SOCRATIC_SYSTEM_PROMPT = """\
{behavioral_rules}

## Identity

You are the Socratic Agent. You challenge assumptions and expose \
hidden risks. You do NOT solve problems. You make others think harder.

Your purpose: when someone (Architect, Concierge, user) is making a \
decision, you force them to examine their reasoning by asking hard \
questions they have not considered.

## Technique

1. Identify the core assumption behind the decision
2. Ask a question that challenges that assumption
3. When you get an answer, probe deeper - do not accept surface answers
4. If you discover a risk, record it with identify_risk
5. After 3-5 rounds of questioning, summarize with a recommendation

## Question types

- "What is the strongest argument AGAINST this approach?"
- "What happens if [assumption X] turns out to be wrong?"
- "What is the blast radius if this fails?"
- "Is there a simpler approach you're overlooking due to sunk cost?"
- "Who would disagree with this, and what would their argument be?"
- "What would have to be true for the opposite decision to be correct?"
- "What are you optimizing for, and what are you sacrificing?"

## Rules

- NEVER propose solutions. Only ask questions and identify risks.
- Each question must target a SPECIFIC assumption, not be generic.
- probe_deeper must reference the previous answer, not repeat the question.
- A risk is only valid if discovered through questioning, not assumed.

## Tool call format

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

## Available tools

{tool_descriptions}
"""


def _build_socratic_prompt(state: SocraticState,
                            tool_descriptions: str) -> str:
    parts = [SOCRATIC_SYSTEM_PROMPT.format(
        behavioral_rules=BaseAgent._base_behavioral_rules(),
        tool_descriptions=tool_descriptions)]

    if state.topic:
        parts.append(f"\n## Topic to Challenge\n\n{state.topic}\n")
    if state.context:
        parts.append(f"### Context\n\n{state.context}\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# SocraticAgent
# ---------------------------------------------------------------------------

class SocraticAgent(BaseAgent):
    """Assumption challenger. Asks hard questions, identifies risks.

    Tools: ask_question, probe_deeper, identify_risk, read_file,
    run_command, summarize_session.
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "autonomous"):
        self._done_flag = False
        self._project_root: Path | None = None
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> SocraticState:
        return SocraticState()

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_socratic_prompt(self.state, tool_desc)

    def _register_tools(self):
        self.tools.register(
            "ask_question", self._tool_ask_question,
            "Pose challenging question (question, target)")
        self.tools.register(
            "probe_deeper", self._tool_probe_deeper,
            "Follow up on answer (previous_answer, followup)")
        self.tools.register(
            "identify_risk", self._tool_identify_risk,
            "Record discovered risk (risk, severity, mitigation)")
        self.tools.register(
            "read_file", self._tool_read_file,
            "Read project file (path, max_lines)")
        self.tools.register(
            "run_command", self._tool_run_command,
            "Read-only shell command (command, timeout)")
        self.tools.register(
            "summarize_session", self._tool_summarize_session,
            "Final recommendation (recommendation, reasoning)")

    def _on_start(self, **context) -> str:
        project_root = context.get("project_root", ".")
        self._project_root = Path(project_root).resolve()

        self.state.topic = context.get("topic", "")
        self.state.context = context.get("context", "")

        parts = ["Socratic session started."]
        if self.state.topic:
            parts.append(f"Topic: {self.state.topic[:200]}")
        parts.append(
            "I will challenge assumptions. Begin questioning.")
        return " ".join(parts)

    def _is_done(self, response: str) -> bool:
        return self._done_flag

    # --- Tools ---

    def _tool_ask_question(self, question: str,
                             target: str = "") -> str:
        """Pose a challenging question."""
        q_id = len(self.state.questions) + 1
        entry = {
            "id": q_id,
            "question": question,
            "target": target,
            "answer": "",
            "followups": [],
            "step": self.state.current_step,
        }
        self.state.questions.append(entry)
        self.state.log_finding(
            f"Q{q_id}: {question[:100]}", severity="info")

        return (f"Question Q{q_id} posed: {question}\n"
                f"Target assumption: {target or '(general)'}")

    def _tool_probe_deeper(self, previous_answer: str,
                             followup: str) -> str:
        """Follow up on an answer."""
        if not self.state.questions:
            return "Error: no previous question. Use ask_question first."

        last_q = self.state.questions[-1]
        last_q["answer"] = previous_answer
        last_q.setdefault("followups", []).append({
            "previous_answer": previous_answer[:300],
            "followup": followup,
            "step": self.state.current_step,
        })

        self.state.log_finding(
            f"Probe on Q{last_q['id']}: {followup[:100]}",
            severity="info")

        return (f"Follow-up on Q{last_q['id']}: {followup}\n"
                f"(Previous answer was: {previous_answer[:200]})")

    def _tool_identify_risk(self, risk: str, severity: str = "medium",
                              mitigation: str = "") -> str:
        """Record a risk discovered through questioning."""
        severity = severity.lower()
        if severity not in VALID_RISK_SEVERITY:
            return (f"Error: severity must be one of "
                    f"{VALID_RISK_SEVERITY}. Got: {severity}")

        entry = {
            "risk": risk,
            "severity": severity,
            "mitigation": mitigation,
            "step": self.state.current_step,
            "discovered_from_question": (
                self.state.questions[-1]["id"]
                if self.state.questions else None),
        }
        self.state.risks.append(entry)

        sev_map = {"critical": "bug", "high": "warning",
                    "medium": "warning", "low": "info"}
        self.state.log_finding(
            f"Risk [{severity}]: {risk[:100]}",
            severity=sev_map.get(severity, "info"))

        return (f"Risk recorded [{severity}]: {risk}\n"
                f"Mitigation: {mitigation or 'none proposed'}")

    def _tool_read_file(self, path: str,
                         max_lines: int = 200) -> str:
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
            if len(lines) > 200:
                output = "\n".join(lines[:200]) + "\n... (truncated)"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except OSError as e:
            return f"Error: {e}"

    def _tool_summarize_session(self, recommendation: str,
                                  reasoning: str = "") -> str:
        """Final recommendation."""
        recommendation = recommendation.lower()
        if recommendation not in VALID_RECOMMENDATIONS:
            return (f"Error: recommendation must be one of "
                    f"{VALID_RECOMMENDATIONS}. Got: {recommendation}")

        self.state.recommendation = recommendation
        self.state.recommendation_reasoning = reasoning
        self._done_flag = True

        q_count = len(self.state.questions)
        r_count = len(self.state.risks)

        self.state.log_finding(
            f"Recommendation: {recommendation} - {reasoning[:200]}",
            severity="info")

        return (f"Session complete. {q_count} questions, "
                f"{r_count} risks. Recommendation: {recommendation}.")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_socratic_report(state: SocraticState) -> dict:
    base = ReportBuilder.build(state)
    base["topic"] = state.topic
    base["questions_count"] = len(state.questions)
    base["questions"] = state.questions
    base["risks"] = state.risks
    base["risks_count"] = len(state.risks)
    base["critical_risks"] = sum(
        1 for r in state.risks if r.get("severity") == "critical")
    base["recommendation"] = state.recommendation
    base["recommendation_reasoning"] = state.recommendation_reasoning
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="SocraticAgent - assumption challenger")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--topic", default="", help="What to challenge")
    parser.add_argument("--context", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    agent = SocraticAgent(backend=args.backend, model=args.model)
    report = agent.run(
        project_root=args.project_root,
        topic=args.topic,
        context=args.context,
    )
    if args.output:
        ReportBuilder.write(report, args.output)
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
