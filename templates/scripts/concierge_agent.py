#!/usr/bin/env python3
"""concierge_agent.py - The brain of the ControlCoding agent system.

The ConciergeAgent is a permanent LLM-based orchestrator that:
- Holds the complete project vision (reads the project context file directly)
- Makes all strategic decisions (which agent, when to transition, what to prioritize)
- Orchestrates all other agents (provides context, receives reports, judges results)
- Maintains a persistent session with accumulated project history
- Manages the workflow lifecycle (IDLE -> PLANNING -> ... -> DONE)

Inherits from BaseAgent (base_agent.py). Wraps concierge.py mechanics
(PromptGenerator, AgentCaller, ProjectContext) as tools.

Design document: dev/design/02_DSN_ConciergeAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S2-S3)
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

# Import base agent framework
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, BaseAgent, BackendAdapter, ToolExecutor,
    ReportBuilder, ToolCall, ToolResult,
)


# ---------------------------------------------------------------------------
# Phase transition rules
# ---------------------------------------------------------------------------

VALID_TRANSITIONS = {
    "IDLE": ["PLANNING"],
    "PLANNING": ["ARCH_REVIEW", "CODING"],
    "ARCH_REVIEW": ["CODING"],
    "CODING": ["TESTING", "REVIEWING"],
    "TESTING": ["REVIEWING", "DEBUGGING"],
    "REVIEWING": ["CODING", "DEBUGGING", "DONE"],
    "DEBUGGING": ["CODING"],
}

ALL_PHASES = {"IDLE", "PLANNING", "ARCH_REVIEW", "CODING", "TESTING",
              "REVIEWING", "DEBUGGING", "DONE"}

CONTROL_PLANE_DIR = ".controlcoding"
LEGACY_CONTROL_PLANE_DIR = ".claude"
PRIMARY_CONTEXT_FILENAME = "CONTROLCODING.md"
LEGACY_CONTEXT_FILENAME = "CLAUDE.md"


def _control_plane_dir(project_root: Path) -> Path:
    canonical = project_root / CONTROL_PLANE_DIR
    if canonical.exists():
        return canonical
    legacy = project_root / LEGACY_CONTROL_PLANE_DIR
    if legacy.exists():
        return legacy
    return canonical


def _control_plane_read_path(project_root: Path, *parts: str) -> Path:
    canonical = project_root / CONTROL_PLANE_DIR / Path(*parts)
    if canonical.exists():
        return canonical
    legacy = project_root / LEGACY_CONTROL_PLANE_DIR / Path(*parts)
    if legacy.exists():
        return legacy
    return canonical


def _context_doc_path(project_root: Path) -> Path:
    canonical = project_root / PRIMARY_CONTEXT_FILENAME
    if canonical.exists():
        return canonical
    legacy = project_root / LEGACY_CONTEXT_FILENAME
    if legacy.exists():
        return legacy
    return canonical


def _context_doc_label(project_root: Path) -> str:
    return _context_doc_path(project_root).name


# ---------------------------------------------------------------------------
# Project context parser
# ---------------------------------------------------------------------------

def _parse_markdown_sections(content: str) -> dict[str, str]:
    """Parse markdown into sections by ## headers.

    Returns {header_text: section_body} for all ## headings.
    """
    sections = {}
    current_header = ""
    current_lines = []

    for line in content.splitlines():
        if line.startswith("## "):
            if current_header:
                sections[current_header] = "\n".join(current_lines).strip()
            # Strip ## prefix and any trailing markers like [hook-enforced]
            header = line[3:].strip()
            # Remove enforcement markers for clean keys
            for marker in ("[hook-enforced]", "[advisory]",
                           "[advisory, workflow hook available]"):
                header = header.replace(marker, "").strip()
            current_header = header
            current_lines = []
        else:
            current_lines.append(line)

    if current_header:
        sections[current_header] = "\n".join(current_lines).strip()

    return sections


# ---------------------------------------------------------------------------
# ConciergeAgentState
# ---------------------------------------------------------------------------

@dataclass
class ConciergeAgentState(AgentStateBase):
    """Deterministic state for the ConciergeAgent.

    Extends AgentStateBase with project context, plan management,
    phase tracking, agent coordination, and decision logging.
    This is the source of truth that survives context compression.
    """

    agent_type: str = "concierge"

    # Project context (loaded from the project context file at start)
    project_root: str = ""
    project_description: str = ""
    architecture_rules: str = ""
    module_boundaries: str = ""
    domain_invariants: str = ""

    # Plan management
    plan: dict = field(default_factory=dict)
    plan_history: list = field(default_factory=list)

    # Phase tracking (overrides base 'phase' default)
    phase: str = "IDLE"
    phase_history: list = field(default_factory=list)

    # Agent coordination
    coder_instructions: list = field(default_factory=list)
    agent_reports: list = field(default_factory=list)
    decisions: list = field(default_factory=list)

    # Criteria tracking
    criteria: list = field(default_factory=list)

    # User interaction
    pending_questions: list = field(default_factory=list)

    def to_context_summary(self) -> str:
        """Generate text summary for rebuilding LLM awareness after
        context compression. Includes Concierge-specific state."""
        lines = [
            f"=== CONCIERGE STATE SUMMARY (step {self.current_step}) ===",
            f"Phase: {self.phase}",
            f"Steps: {self.total_steps} | LLM calls: {self.total_llm_calls}",
            f"Plan phases: {len(self.plan.get('phases', []))} total",
            f"Decisions made: {len(self.decisions)}",
            f"Agent reports received: {len(self.agent_reports)}",
            f"Coder instructions sent: {len(self.coder_instructions)}",
            f"Criteria: {len(self.criteria)}",
        ]
        if self.decisions:
            lines.append("\nRecent decisions:")
            for d in self.decisions[-5:]:
                lines.append(
                    f"  [step {d.get('step', '?')}] {d.get('decision', '?')}")
        if self.agent_reports:
            lines.append("\nRecent agent reports:")
            for r in self.agent_reports[-3:]:
                summary = r.get("summary", r.get("result", "?"))
                lines.append(
                    f"  [{r.get('agent', '?')}] {str(summary)[:100]}")
        if self.phase_history:
            lines.append("\nPhase transitions:")
            for t in self.phase_history[-5:]:
                lines.append(
                    f"  {t.get('from', '?')} -> {t.get('to', '?')} "
                    f"({t.get('reason', '?')})")
        if self.errors:
            lines.append(f"\nErrors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END CONCIERGE STATE SUMMARY ===")
        return "\n".join(lines)

    def save(self, path: str):
        """Persist full Concierge state to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            # Base fields
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
            # Concierge-specific fields
            "project_root": self.project_root,
            "project_description": self.project_description[:500],
            "architecture_rules": self.architecture_rules[:500],
            "plan": self.plan,
            "phase_history": self.phase_history[-20:],
            "coder_instructions": self.coder_instructions[-10:],
            "agent_reports": self.agent_reports[-10:],
            "decisions": self.decisions[-20:],
            "criteria": self.criteria,
        }
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "ConciergeAgentState":
        """Load state from JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        state = cls()
        for key, value in data.items():
            if hasattr(state, key):
                setattr(state, key, value)
        return state


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

CONCIERGE_SYSTEM_PROMPT = """\
You are the Concierge - the central orchestrator of the ControlCoding agent system.

You hold the complete project vision. You make all strategic decisions.
The user talks to you. You talk to the other agents via tools.

## Your workflow phases

IDLE -> PLANNING -> ARCH_REVIEW -> CODING -> TESTING -> REVIEWING -> DONE
                                      ^                    |
                                      |                    v
                                      +--- DEBUGGING <-----+

At each phase, decide:
- What needs to happen next
- Which tool to use (transition_phase, instruct_coder, call_consultant, etc.)
- Whether results are acceptable or need retry
- When to transition to the next phase

## Tool call format

To call a tool, write a JSON block in a markdown fence:

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

## Rules

1. Always log_decision before major choices (which agent, phase transition, accept/reject)
2. Always transition_phase before starting work in a new phase
3. Never skip PLANNING - every task needs a plan (even a simple one)
4. The Coder receives instructions FROM you - it does not decide what to build
5. After REVIEWING, either go to DONE (if pass) or loop back (CODING/DEBUGGING)
6. Use call_consultant for external perspective on hard decisions
7. Use run_tandem for critical architecture decisions that need multi-model validation

## Available tools

{tool_descriptions}
"""


def _build_concierge_prompt(state: ConciergeAgentState,
                             tool_descriptions: str) -> str:
    """Assemble the full system prompt from layers."""
    parts = [CONCIERGE_SYSTEM_PROMPT.format(
        tool_descriptions=tool_descriptions)]

    # Layer 3: Project context
    if state.project_description or state.architecture_rules:
        context_label = _context_doc_label(Path(state.project_root)) if state.project_root else PRIMARY_CONTEXT_FILENAME
        parts.append(f"## Project Rules (from {context_label})\n")
        if state.project_description:
            parts.append(
                f"### Project Identity\n{state.project_description}\n")
        if state.architecture_rules:
            parts.append(
                f"### Architecture Rules\n{state.architecture_rules}\n")
        if state.module_boundaries:
            parts.append(
                f"### Module Boundaries\n{state.module_boundaries}\n")
        if state.domain_invariants:
            parts.append(
                f"### Domain Invariants\n{state.domain_invariants}\n")

    # Layer 4: Current plan
    if state.plan:
        plan_summary = json.dumps(state.plan, indent=2)
        if len(plan_summary) > 3000:
            plan_summary = plan_summary[:3000] + "\n... (truncated)"
        parts.append(f"## Current Plan\n\n```json\n{plan_summary}\n```\n")
    else:
        parts.append(
            "## Current Plan\n\nNo plan yet. Use planning tools or "
            "instruct_coder to define the work.\n")

    # Layer 5: Criteria
    if state.criteria:
        criteria_lines = []
        for c in state.criteria[:20]:
            name = c.get("name", c.get("id", "?"))
            status = c.get("status", "pending")
            criteria_lines.append(f"  - [{status}] {name}")
        parts.append(
            "## Criteria\n\n" + "\n".join(criteria_lines) + "\n")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# ConciergeAgent
# ---------------------------------------------------------------------------

class ConciergeAgent(BaseAgent):
    """The brain of the agent system.

    Permanent LLM-based agent that orchestrates all other agents.
    Reads the project context file directly (only agent to do so).
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "interactive"):
        self._done_flag = False
        self._project_root: Path | None = None
        self._auto_answer = "y"
        # concierge.py mechanics (lazy-loaded)
        self._project_context = None
        self._prompt_generator = None
        self._agent_caller = None
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> ConciergeAgentState:
        return ConciergeAgentState()

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_concierge_prompt(self.state, tool_desc)

    def _register_tools(self):
        # S2: Phase management
        self.tools.register(
            "transition_phase", self._tool_transition_phase,
            "Transition to a new workflow phase (to_phase, reason)")
        self.tools.register(
            "log_decision", self._tool_log_decision,
            "Record a strategic decision (decision, reasoning)")
        self.tools.register(
            "ask_user", self._tool_ask_user,
            "Ask the user a question (question)")

        # S3: Agent coordination
        self.tools.register(
            "instruct_coder", self._tool_instruct_coder,
            "Generate instructions for the Coder (task, constraints, files, guidance)")
        self.tools.register(
            "call_consultant", self._tool_call_consultant,
            "Consult an agent role (role, question, context)")
        self.tools.register(
            "run_tandem", self._tool_run_tandem,
            "Run multi-model debate (problem, role, domain)")
        self.tools.register(
            "spawn_visual_agent", self._tool_spawn_visual_agent,
            "Spawn Visual Agent for QA testing (exe, criteria, timeout, mode)")

        # S3: Project inspection
        self.tools.register(
            "read_file", self._tool_read_file,
            "Read a project file (path, max_lines)")
        self.tools.register(
            "run_command", self._tool_run_command,
            "Run shell command in project dir (command, timeout)")

        # S3: Plan management
        self.tools.register(
            "update_plan", self._tool_update_plan,
            "Update plan (action: set|mark_phase_done|add_note|add_phase, data)")

        # S3: Session
        self.tools.register(
            "done", self._tool_done,
            "Signal work is complete, generate final report (summary)")

    def _on_start(self, **context) -> str:
        """Load project context and return initial message to LLM."""
        # Restore Concierge-specific phase (BaseAgent.start sets "active")
        self.state.phase = "IDLE"

        project_root = context.get("project_root", ".")
        self._project_root = Path(project_root).resolve()
        self.state.project_root = str(self._project_root)

        # Load project context file
        self._load_project_context()

        # Load plan
        self._load_plan()

        # Load criteria
        self._load_criteria()

        # Initialize concierge.py mechanics
        self._init_concierge_mechanics()

        # Build initial message
        task = context.get("task", "")
        parts = ["Project loaded."]
        if self.state.project_description:
            parts.append(
                f"Project: {self.state.project_description[:200]}")
        if self.state.plan:
            phases = self.state.plan.get("phases", [])
            parts.append(f"Plan: {len(phases)} phases loaded.")
        if self.state.criteria:
            parts.append(f"Criteria: {len(self.state.criteria)} loaded.")
        parts.append(f"Phase: {self.state.phase}.")
        if task:
            parts.append(f"Task: {task}")
            parts.append("What should we do first?")
        else:
            parts.append("No task specified. What would you like to work on?")

        return " ".join(parts)

    def _is_done(self, response: str) -> bool:
        return self._done_flag

    def _on_finish(self):
        """Add Concierge-specific data to the report."""
        pass

    # --- Context loading ---

    def _load_project_context(self):
        """Parse the project context file into structured sections in state."""
        if not self._project_root:
            return
        claude_md = _context_doc_path(self._project_root)
        if not claude_md.exists():
            return

        try:
            content = claude_md.read_text(encoding="utf-8")
        except OSError:
            return

        sections = _parse_markdown_sections(content)
        self.state.project_description = sections.get(
            "Project Identity", "")
        self.state.architecture_rules = sections.get(
            "Architecture Rules", "")
        self.state.module_boundaries = sections.get(
            "Module Boundaries", "")
        self.state.domain_invariants = sections.get(
            "Domain Invariants", "")

    def _load_plan(self):
        """Load plan.current.json into state."""
        if not self._project_root:
            return
        plan_path = (self._project_root / "devlog" / "plans"
                     / "plan.current.json")
        if not plan_path.exists():
            return
        try:
            self.state.plan = json.loads(
                plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    def _load_criteria(self):
        """Load criteria.json into state."""
        if not self._project_root:
            return
        criteria_path = (self._project_root / "devlog" / "criteria"
                         / "criteria.json")
        if not criteria_path.exists():
            return
        try:
            data = json.loads(criteria_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.state.criteria = data
            elif isinstance(data, dict):
                self.state.criteria = data.get("criteria", [])
        except (json.JSONDecodeError, OSError):
            pass

    def _init_concierge_mechanics(self):
        """Lazy-load concierge.py classes for tool implementations."""
        if not self._project_root:
            return
        try:
            from concierge import ProjectContext, PromptGenerator, AgentCaller
            self._project_context = ProjectContext(self._project_root)
            self._prompt_generator = PromptGenerator(self._project_context)
            self._agent_caller = AgentCaller(self._project_root)
        except ImportError:
            # concierge.py not available - tools will work in degraded mode
            pass

    # --- S2 Tools: Phase management ---

    def _tool_transition_phase(self, to_phase: str,
                                reason: str = "") -> str:
        """Transition to a new workflow phase."""
        to_phase = to_phase.upper()
        if to_phase not in ALL_PHASES:
            return (f"Error: Unknown phase '{to_phase}'. "
                    f"Valid phases: {sorted(ALL_PHASES)}")

        current = self.state.phase
        allowed = VALID_TRANSITIONS.get(current, [])
        if to_phase not in allowed:
            return (f"Error: Cannot transition from {current} to "
                    f"{to_phase}. Allowed: {allowed}")

        # Record transition
        transition = {
            "from": current,
            "to": to_phase,
            "reason": reason,
            "step": self.state.current_step,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self.state.phase_history.append(transition)
        self.state.phase = to_phase

        return (f"Phase transitioned: {current} -> {to_phase}. "
                f"Reason: {reason or 'not specified'}")

    def _tool_log_decision(self, decision: str,
                            reasoning: str = "") -> str:
        """Record a strategic decision with reasoning.

        Persists to both in-memory state AND append-only JSONL on disk.
        The JSONL survives context compression and session restarts.
        """
        entry = {
            "session_id": self.state.session_id,
            "step": self.state.current_step,
            "phase": self.state.phase,
            "decision": decision,
            "reasoning": reasoning,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self.state.decisions.append(entry)

        # Persist to disk (append-only JSONL)
        if self._project_root:
            log_path = (_control_plane_dir(self._project_root)
                        / "concierge_decisions.jsonl")
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass  # best-effort persistence

        return f"Decision logged: {decision}"

    def _tool_ask_user(self, question: str) -> str:
        """Ask the user a question."""
        if self.mode == "autonomous":
            return f"[auto-answer: {self._auto_answer}]"

        print(f"\n>>> [Concierge asks] {question}")
        try:
            answer = input("<<< ").strip()
            return answer if answer else "[no answer]"
        except (EOFError, KeyboardInterrupt):
            return "[no answer]"

    # --- S3 Tools: Agent coordination ---

    def _tool_instruct_coder(self, task: str, constraints: str = "",
                              files: str = "",
                              guidance: str = "") -> str:
        """Generate and record instructions for the Coder agent."""
        instructions = {
            "step": self.state.current_step,
            "phase": self.state.phase,
            "task": task,
            "constraints": constraints,
            "files": files,
            "guidance": guidance,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self.state.coder_instructions.append(instructions)

        # Format as readable text
        parts = [f"## Coder Instructions (step {self.state.current_step})\n"]
        parts.append(f"### Task\n{task}\n")
        if constraints:
            parts.append(f"### Constraints\n{constraints}\n")
        if files:
            parts.append(f"### Target Files\n{files}\n")
        if guidance:
            parts.append(f"### Architect Guidance\n{guidance}\n")

        formatted = "\n".join(parts)

        # Tag with verbatim hash for integrity verification
        instructions["_verbatim_hash"] = self._hash_content(formatted)

        return f"Instructions generated and recorded.\n\n{formatted}"

    def _tool_call_consultant(self, role: str, question: str,
                               context: str = "") -> str:
        """Consult an agent in the specified role.

        Routes through the Consultation Protocol when available,
        falls back to direct concierge.py mechanics.
        """
        # Map legacy role to consultation situation
        _role_to_situation = {
            "architect": "architecture_decision",
            "reviewer": "code_review",
            "debug": "bug_diagnosis",
            "socratic": "assumption_challenge",
            "scientist": "domain_question",
            "planner": "plan_review",
            "expert": "domain_question",
        }

        valid_roles = tuple(_role_to_situation.keys())
        if role not in valid_roles:
            return (f"Error: Unknown role '{role}'. "
                    f"Valid: {valid_roles}")

        # Try Consultation Protocol (new path)
        used_path = "protocol"
        try:
            situation = _role_to_situation.get(role, "domain_question")
            result_obj = self.consult(
                situation=situation,
                question=question,
                context=context,
            )
            result = result_obj.answer
            if result.startswith("ERROR:") or result.startswith("[FALLBACK]"):
                raise RuntimeError(result)
        except (RuntimeError, OSError, ImportError):
            # Fallback: direct concierge.py mechanics
            used_path = "legacy"
            if self._prompt_generator and self._agent_caller:
                prompt = self._prompt_generator.generate(
                    role=role,
                    task=(f"{question}\n\nContext: {context}"
                          if context else question),
                )
                result = self._agent_caller.call_consult(
                    role=role,
                    prompt=prompt,
                    backend=os.environ.get("CONCIERGE_BACKEND", "").strip(),
                )
            else:
                result = (f"[Consultant call - {role}] Would consult "
                          f"about: {question[:200]}")

        # Record the consultation
        report = {
            "agent": f"consultant-{role}",
            "role": role,
            "path": used_path,
            "question": question[:200],
            "result": result[:500] if isinstance(result, str) else str(result)[:500],
            "summary": result[:200] if isinstance(result, str) else str(result)[:200],
            "step": self.state.current_step,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self.state.agent_reports.append(report)

        return result if isinstance(result, str) else json.dumps(result)

    def _tool_run_tandem(self, problem: str, role: str = "architect",
                          domain: str = "") -> str:
        """Run multi-model Tandem debate.

        Routes through the Consultation Protocol at 'critical' level
        to enable multi-source consultation with external path.
        Falls back to direct concierge.py mechanics.
        """
        # Map role to situation for protocol routing
        _role_to_situation = {
            "architect": "critical_decision",
            "reviewer": "design_review",
            "debug": "bug_diagnosis",
            "socratic": "assumption_challenge",
        }

        # Try Consultation Protocol
        used_path = "protocol"
        try:
            situation = _role_to_situation.get(role, "critical_decision")
            result_obj = self.consult(
                situation=situation,
                question=problem,
                criticality="high",
                domain=domain,
            )
            if result_obj.answer.startswith("ERROR:"):
                raise RuntimeError(result_obj.answer)
            result = result_obj.to_dict()
        except (RuntimeError, OSError, ImportError):
            # Fallback: direct tandem
            used_path = "legacy"
            if self._agent_caller:
                result = self._agent_caller.call_tandem(
                    problem=problem,
                    role=role,
                    domain=domain,
                )
            else:
                result = {"skipped": True,
                          "reason": "concierge.py not available"}

        # Record
        report = {
            "agent": "tandem",
            "path": used_path,
            "problem": problem[:200],
            "result": result,
            "step": self.state.current_step,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self.state.agent_reports.append(report)

        if isinstance(result, dict):
            return json.dumps(result, indent=2)
        return str(result)

    def _tool_spawn_visual_agent(self, exe: str = "",
                                  criteria: str = "",
                                  timeout: int = 300,
                                  mode: str = "autonomous") -> str:
        """Spawn the Visual Agent for QA testing.

        The Visual Agent launches the application, takes screenshots,
        interacts via keyboard/mouse, verifies criteria, and produces
        a structured report with evidence.
        """
        try:
            from visual_agent import VisualAgent, build_visual_report
        except ImportError:
            return ("Error: visual_agent.py not available. "
                    "Use run_command with visual_test.py as fallback.")

        agent = VisualAgent(
            backend=self.backend_adapter.backend,
            model=self.backend_adapter.model,
            mode=mode,
        )
        agent.state.timeout_seconds = timeout

        # Parse criteria if provided as JSON string
        criteria_list = []
        if criteria:
            try:
                criteria_list = json.loads(criteria)
                if not isinstance(criteria_list, list):
                    criteria_list = criteria_list.get("criteria", [])
            except json.JSONDecodeError:
                criteria_list = []

        report = agent.run(
            exe=exe,
            criteria=criteria_list,
            mode=mode,
        )

        # Record in Concierge state
        self.state.agent_reports.append({
            "agent": "visual",
            "summary": (f"pass={report.get('criteria_summary', {}).get('pass', 0)}, "
                        f"fail={report.get('criteria_summary', {}).get('fail', 0)}"),
            "report": report,
            "step": self.state.current_step,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })

        # Return summary for the LLM
        cs = report.get("criteria_summary", {})
        return (f"Visual Agent complete. "
                f"Pass: {cs.get('pass', 0)}, "
                f"Fail: {cs.get('fail', 0)}, "
                f"Pending: {cs.get('pending', 0)}. "
                f"Screenshots: {report.get('screenshots_count', 0)}. "
                f"Stop reason: {report.get('stop_reason', '?')}.")

    # --- S3 Tools: Project inspection ---

    def _tool_read_file(self, path: str,
                         max_lines: int = 200) -> str:
        """Read a project file (path relative to project_root)."""
        if not self._project_root:
            return "Error: project_root not set"

        # Resolve and validate path
        target = (self._project_root / path).resolve()
        try:
            # Security: check path is within project_root
            target.relative_to(self._project_root)
        except ValueError:
            return (f"Error: Path '{path}' is outside the project "
                    f"directory. Access denied.")

        if not target.exists():
            return f"Error: File not found: {path}"
        if not target.is_file():
            return f"Error: Not a file: {path}"

        try:
            lines = target.read_text(encoding="utf-8").splitlines()
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                lines.append(f"... (truncated at {max_lines} lines)")
            return "\n".join(lines)
        except (OSError, UnicodeDecodeError) as e:
            return f"Error reading file: {e}"

    def _tool_run_command(self, command: str,
                           timeout: int = 60) -> str:
        """Run a shell command in the project directory."""
        if not self._project_root:
            return "Error: project_root not set"

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=min(timeout, 120),
                cwd=str(self._project_root),
            )
            output = result.stdout + result.stderr
            if len(output) > 2000:
                output = output[:2000] + "\n... (truncated)"
            if result.returncode != 0:
                output = (f"[exit code {result.returncode}]\n" + output)
            return output if output.strip() else "[no output]"
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout}s"
        except OSError as e:
            return f"Error running command: {e}"

    # --- S3 Tools: Plan management ---

    def _tool_update_plan(self, action: str,
                           data: str = "") -> str:
        """Update the current plan.

        Actions: set, mark_phase_done, add_note, add_phase
        """
        action = action.lower()

        if action == "set":
            try:
                self.state.plan = json.loads(data)
                return "Plan replaced with new content."
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON for plan: {e}"

        elif action == "mark_phase_done":
            phases = self.state.plan.get("phases", [])
            # Find current phase and mark done
            for phase in phases:
                if phase.get("status") != "done":
                    phase["status"] = "done"
                    return f"Phase '{phase.get('name', '?')}' marked as done."
            return "No pending phases to mark as done."

        elif action == "add_note":
            if "notes" not in self.state.plan:
                self.state.plan["notes"] = []
            self.state.plan["notes"].append({
                "step": self.state.current_step,
                "text": data,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
            return f"Note added to plan: {data[:100]}"

        elif action == "add_phase":
            try:
                phase_data = json.loads(data)
            except json.JSONDecodeError:
                phase_data = {"name": data, "status": "pending"}
            if "phases" not in self.state.plan:
                self.state.plan["phases"] = []
            self.state.plan["phases"].append(phase_data)
            return f"Phase added: {phase_data.get('name', data[:50])}"

        else:
            return (f"Error: Unknown action '{action}'. "
                    f"Use: set, mark_phase_done, add_note, add_phase")

    # --- S3 Tools: Session ---

    def _tool_done(self, summary: str = "") -> str:
        """Signal work is complete."""
        self._done_flag = True
        self.state.stop_reason = "done"

        if summary:
            self.state.log_finding(
                f"Session complete: {summary}",
                severity="info",
            )

        return "Work complete. Generating final report."


# ---------------------------------------------------------------------------
# Report builder extension
# ---------------------------------------------------------------------------

def build_concierge_report(state: ConciergeAgentState) -> dict:
    """Build report with Concierge-specific data."""
    base = ReportBuilder.build(state)
    base.update({
        "project_root": state.project_root,
        "phase_history": state.phase_history,
        "decisions": state.decisions,
        "coder_instructions_count": len(state.coder_instructions),
        "agent_reports_count": len(state.agent_reports),
        "criteria_count": len(state.criteria),
    })
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for ConciergeAgent."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ConciergeAgent - ControlCoding orchestrator")
    parser.add_argument("--project-root", default=".",
                        help="Path to project directory")
    parser.add_argument("--task", default="",
                        help="Task to work on")
    parser.add_argument("--backend", required=True,
                        choices=["anthropic", "ollama", "openai",
                                 "claude", "session"],
                        help="LLM backend")
    parser.add_argument("--model", default="",
                        help="Model name override")
    parser.add_argument("--mode", default="interactive",
                        choices=["interactive", "autonomous"],
                        help="Operating mode")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from saved state")
    parser.add_argument("--timeout", type=int, default=3600,
                        help="Session timeout in seconds (default: 3600)")

    args = parser.parse_args()

    agent = ConciergeAgent(
        backend=args.backend,
        model=args.model,
        mode=args.mode,
    )
    agent.state.timeout_seconds = args.timeout

    if args.resume:
        project_root = Path(args.project_root).resolve()
        state_path = _control_plane_read_path(project_root, "concierge_agent_state.json")
        if state_path.exists():
            agent.state = ConciergeAgentState.load(str(state_path))
            print(f"Resumed from {state_path}")
            print(f"Phase: {agent.state.phase}, "
                  f"Step: {agent.state.current_step}")
        else:
            print(f"No saved state found at {state_path}")

    save_path = str(_control_plane_dir(Path(args.project_root).resolve())
                    / "concierge_agent_state.json")

    report = agent.run(
        state_save_path=save_path,
        project_root=args.project_root,
        task=args.task,
    )

    print("\n" + "=" * 60)
    print("CONCIERGE REPORT")
    print("=" * 60)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
