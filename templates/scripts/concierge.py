#!/usr/bin/env python3
"""concierge.py - The Orchestrator Agent for ControlCoding.

The Concierge is the main entry point for the user. It orchestrates all
other agents through a state machine workflow:

    8 Thinking Agents:
        Concierge (this), Architect, Coder, Reviewer, Debugger,
        Socratic, CodeWarden, Expert on-demand

    Internal Functions (not separate agents):
        Planner - maieutic expansion (Concierge function)

    Protocols (not agents):
        Tandem - multi-model debate on architecture decisions

    Tools:
        visual_test.py - interactive testing with input simulation
        visual_check.py - basic single screenshot

Key design decisions (March 19, 2026 reorg):
    - Planner = Concierge internal function, not separate agent
    - Tandem = debate protocol, not agent
    - Verifier = merged into Reviewer (structured JSON PASS/FAIL)
    - Architect = permanent project expert (reads spec, decides structure)
    - Coder = worker (receives instructions, does not decide)
    - Scientist = spec validation role via consultation

Usage:
    python concierge.py --project-root /path/to/project
    python concierge.py --project-root /path/to/project --task "Add inventory"
    python concierge.py --project-root /path/to/project --resume

Only Python stdlib - no external dependencies.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTROL_PLANE_DIR = ".controlcoding"
LEGACY_CONTROL_PLANE_DIR = ".claude"
PRIMARY_CONTEXT_FILENAME = "CONTROLCODING.md"
LEGACY_CONTEXT_FILENAME = "CLAUDE.md"

MAX_ITERATIONS = 100
PROGRESS_POLL_INTERVAL = 15     # seconds between progress checks
CODER_STALL_LIMIT = 180        # kill only if no progress for this long
CONSULT_STALL_LIMIT = 120
BUILD_STALL_LIMIT = 120
VISUAL_TEST_TIMEOUT = 120      # visual test is short, timeout is OK here

REVIEW_JSON_INSTRUCTION = """\
RESPOND WITH ONLY a JSON object in this format:
```json
{
  "criteria": [
    {"name": "criterion name", "status": "PASS|FAIL", "detail": "what you ACTUALLY see/verify"}
  ],
  "summary": {"pass": 0, "fail": 0, "total": 0},
  "overall": "PASS|FAIL"
}
```
If no screenshot is provided, review code quality instead.

CRITICAL VERIFICATION RULES:
- For EACH criterion, ask: "Can I LITERALLY see or verify this? Yes or no?"
- "Something vaguely works" is a FAIL. Only clear, verifiable results PASS.
- If a visual output is mostly empty, black, or shows no meaningful content:
  FAIL the rendering criterion. Do not accept "minimal" as success.
- If a command should produce output but produces nothing: FAIL.
- If a test should pass but has errors or warnings: FAIL.
- Do NOT use confirmation bias. If you WANT it to pass but the evidence
  is ambiguous, mark it FAIL and explain what you expected vs what you see."""


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
# State Machine
# ---------------------------------------------------------------------------

STATES = {
    "IDLE",
    "PLANNING",
    "ARCHITECTURE_REVIEW",
    "CODING",
    "TESTING",
    "REVIEWING",
    "DEBUGGING",
    "FIX_LOOP",
    "FINAL_REVIEW",
    "DONE",
}

TRANSITIONS = {
    "IDLE": ["PLANNING"],
    "PLANNING": ["ARCHITECTURE_REVIEW", "CODING"],
    "ARCHITECTURE_REVIEW": ["CODING"],
    "CODING": ["TESTING", "REVIEWING"],
    "TESTING": ["REVIEWING", "DEBUGGING"],
    "REVIEWING": ["CODING", "DEBUGGING", "FINAL_REVIEW"],
    "DEBUGGING": ["CODING", "FIX_LOOP"],
    "FIX_LOOP": ["TESTING"],
    "FINAL_REVIEW": ["DONE", "DEBUGGING"],
    "DONE": ["IDLE"],
}


class ConciergeState:
    """Persistent state for the Concierge workflow."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.state_path = _control_plane_dir(self.project_root) / "concierge_state.json"
        self.state = "IDLE"
        self.task = ""
        self.current_phase = 0
        self.total_phases = 0
        self.phase_attempts = 0
        self.debug_attempts = 0
        self.failures = []
        self.completed_phases = []
        self.review_results = []  # structured JSON from Reviewer
        self.architect_guidance = ""  # Architect's persistent guidance
        self.history = []
        # S8: State recovery - operation status per phase
        self.phase_ops = {}  # {phase_idx: {op_name: "not_started"|"in_progress"|"completed"}}

    def save(self):
        """Persist state to disk."""
        data = {
            "state": self.state,
            "task": self.task,
            "current_phase": self.current_phase,
            "total_phases": self.total_phases,
            "phase_attempts": self.phase_attempts,
            "debug_attempts": self.debug_attempts,
            "failures": self.failures[-20:],
            "completed_phases": self.completed_phases,
            "review_results": self.review_results[-10:],
            "architect_guidance": self.architect_guidance[:2000],
            "history": self.history[-50:],
            "phase_ops": self.phase_ops,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load(self):
        """Load state from disk."""
        if not self.state_path.exists():
            return False
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.state = data.get("state", "IDLE")
            self.task = data.get("task", "")
            self.current_phase = data.get("current_phase", 0)
            self.total_phases = data.get("total_phases", 0)
            self.phase_attempts = data.get("phase_attempts", 0)
            self.debug_attempts = data.get("debug_attempts", 0)
            self.failures = data.get("failures", [])
            self.completed_phases = data.get("completed_phases", [])
            self.review_results = data.get("review_results", [])
            self.architect_guidance = data.get("architect_guidance", "")
            self.history = data.get("history", [])
            self.phase_ops = data.get("phase_ops", {})
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def transition(self, new_state: str):
        """Transition to a new state with validation."""
        if new_state not in TRANSITIONS.get(self.state, []):
            raise ValueError(
                f"Invalid transition: {self.state} -> {new_state}. "
                f"Allowed: {TRANSITIONS.get(self.state, [])}"
            )
        old = self.state
        self.state = new_state
        self.history.append({
            "from": old,
            "to": new_state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.save()

    def record(self, action: str, result: str):
        """Record an action and its result."""
        self.history.append({
            "state": self.state,
            "action": action,
            "result": result[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def mark_op(self, op_name: str, status: str):
        """Mark a phase operation status for recovery.

        Status: "not_started", "in_progress", "completed"
        """
        phase_key = str(self.current_phase)
        if phase_key not in self.phase_ops:
            self.phase_ops[phase_key] = {}
        self.phase_ops[phase_key][op_name] = status

    def get_op_status(self, op_name: str) -> str:
        """Get operation status for current phase. Default: not_started."""
        phase_key = str(self.current_phase)
        return self.phase_ops.get(phase_key, {}).get(op_name, "not_started")


# ---------------------------------------------------------------------------
# Context Reader
# ---------------------------------------------------------------------------

class ProjectContext:
    """Reads project state for prompt generation."""

    def __init__(self, project_root: Path):
        self.root = project_root

    def read_claude_md(self, max_lines: int = 300) -> str:
        """Read the project context file, truncated."""
        p = _context_doc_path(self.root)
        if not p.exists():
            return ""
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append("... (truncated)")
        return "\n".join(lines)

    def read_plan(self) -> dict | None:
        """Read current plan."""
        p = self.root / "devlog" / "plans" / "plan.current.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def read_criteria(self) -> list[dict]:
        """Read verification criteria."""
        p = self.root / "devlog" / "criteria" / "criteria.json"
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else data.get("criteria", [])
        except (json.JSONDecodeError, OSError):
            return []

    def read_recent_events(self, max_events: int = 20) -> list[dict]:
        """Read recent events from event_log."""
        p = _control_plane_read_path(self.root, "event_log.jsonl")
        if not p.exists():
            return []
        events = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
        return events[-max_events:]

    def read_violations(self) -> list[dict]:
        """Read CodeWarden violations."""
        p = _control_plane_read_path(self.root, "codewarden_violations.jsonl")
        if not p.exists():
            return []
        violations = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        violations.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
        return violations

    def read_spec_files(self) -> str:
        """Read design spec files (for Scientist validation)."""
        specs = []
        for name in ("SPEC.md", "DESIGN.md", "spec.md", "design.md"):
            p = self.root / name
            if p.exists():
                text = p.read_text(encoding="utf-8")
                if len(text) > 5000:
                    text = text[:5000] + "\n... (truncated)"
                specs.append(f"--- {name} ---\n{text}")
        # Also check devlog/
        for name in ("spec.md", "design.md"):
            p = self.root / "devlog" / name
            if p.exists():
                text = p.read_text(encoding="utf-8")
                if len(text) > 5000:
                    text = text[:5000] + "\n... (truncated)"
                specs.append(f"--- devlog/{name} ---\n{text}")
        return "\n\n".join(specs) if specs else ""

    def get_phase_info(self, phase_index: int) -> dict:
        """Get info about a specific plan phase."""
        plan = self.read_plan()
        if not plan:
            return {}
        phases = plan.get("phases", [])
        if 0 <= phase_index < len(phases):
            return phases[phase_index]
        return {}

    def count_src_files(self) -> int:
        """Count source files in src/."""
        src = self.root / "src"
        if not src.exists():
            return 0
        return sum(1 for _ in src.rglob("*.cpp")) + sum(1 for _ in src.rglob("*.h"))

    def has_plan(self) -> bool:
        """Check if an approved plan exists."""
        plan = self.read_plan()
        return plan is not None and plan.get("status") == "APPROVED"

    def has_criteria(self) -> bool:
        """Check if criteria exist."""
        return len(self.read_criteria()) > 0

    def get_build_cmd(self) -> str:
        """Extract build command from the project context file or return default."""
        claude_md = self.read_claude_md()
        for line in claude_md.splitlines():
            if "cmake --build" in line and "Release" in line:
                return line.strip().lstrip("$ ").lstrip("`").rstrip("`")
        return "cmake --build build --config Release"

    def get_exe_path(self) -> str:
        """Find the built executable."""
        # Check common locations
        for pattern in ("build/Release/*.exe", "build/*.exe",
                        "build/Debug/*.exe"):
            matches = list(self.root.glob(pattern))
            if matches:
                return str(matches[0])
        return str(self.root / "build" / "Release" / "app.exe")


# ---------------------------------------------------------------------------
# Prompt Generator
# ---------------------------------------------------------------------------

class PromptGenerator:
    """Generates contextual prompts for each agent role."""

    ROLE_INSTRUCTIONS = {
        "planner": (
            "You are the Planner (Concierge internal function). Expand the "
            "user's idea into a fully discretized implementation plan with "
            "phases, features, acceptance criteria, and domain invariants. "
            "Ask probing questions to uncover hidden requirements."
        ),
        "architect": (
            "You are the Architect - the permanent project expert. You read "
            "specs and design documents, decide file structure and interfaces, "
            "and communicate instructions to the Coder. Review interfaces for "
            "completeness, coupling, missing methods, and extensibility. "
            "DENY-protected files CANNOT be modified after creation."
        ),
        "coder": (
            "You are the Coder - a worker agent. Implement exactly what the "
            "Architect and Concierge specify for this phase. Follow the plan. "
            "Do not add features not in the plan. Do not skip steps. "
            "Commit after completing the phase."
        ),
        "reviewer": (
            "You are the Reviewer. You judge quality (code, visual, functional) "
            "with structured PASS/FAIL output per criterion. Be strict - if you "
            "cannot clearly verify a criterion, it FAILS.\n\n"
            + REVIEW_JSON_INSTRUCTION
        ),
        "debug": (
            "You are the Debugger. Formulate a hypothesis BEFORE suggesting "
            "fixes. Follow the protocol: 1) verify the component exists, "
            "2) verify data flow, 3) only then adjust parameters. Never skip "
            "to parameter tweaking."
        ),
        "socratic": (
            "You are the Socratic Questioner. Ask 'why', 'what if', 'how do "
            "you know', 'what could go wrong'. Challenge assumptions. Propose "
            "fundamentally different approaches, not variations of what was "
            "already tried."
        ),
        "scientist": (
            "You are the Domain Scientist. Validate the implementation against "
            "its theoretical specification. Check that formulas, algorithms, "
            "and domain rules match what the design document prescribes. "
            "Report conformance and deviations with references to the spec."
        ),
    }

    def __init__(self, context: ProjectContext):
        self.ctx = context

    def generate(self, role: str, task: str,
                 failures: list | None = None,
                 phase_info: dict | None = None,
                 architect_guidance: str = "") -> str:
        """Generate a contextual prompt for the given agent role."""
        parts = []

        # 1. Role instruction
        instruction = self.ROLE_INSTRUCTIONS.get(role, "")
        if instruction:
            parts.append(instruction)

        # 2. Project rules (for roles that need them)
        if role in ("reviewer", "architect", "scientist", "coder"):
            claude_md = self.ctx.read_claude_md()
            if claude_md:
                parts.append(f"## Project Rules (from {_context_doc_label(self.ctx.root)})\n\n{claude_md}")

        # 3. Architect guidance (for Coder)
        if role == "coder" and architect_guidance:
            parts.append(
                f"## Architect Guidance\n\n{architect_guidance}"
            )

        # 4. Spec files (for Scientist)
        if role == "scientist":
            specs = self.ctx.read_spec_files()
            if specs:
                parts.append(f"## Design Specifications\n\n{specs}")

        # 5. Current plan phase
        if phase_info:
            parts.append(
                f"## Current Phase\n\n"
                f"Phase: {phase_info.get('name', '?')}\n"
                f"Features: {json.dumps(phase_info.get('features', []), indent=2)}"
            )

        # 6. Recent events
        events = self.ctx.read_recent_events(10)
        if events and role in ("debug", "socratic", "reviewer"):
            event_lines = []
            for e in events:
                event_lines.append(
                    f"[{e.get('ts', '?')}] {e.get('agent', '?')}: "
                    f"{e.get('event', '?')} - {json.dumps(e.get('details', {}))}"
                )
            parts.append("## Recent Events\n\n" + "\n".join(event_lines))

        # 7. Previous failures
        if failures:
            fail_lines = [f"- {f}" for f in failures[-5:]]
            parts.append(
                "## Previous Failures (fix these)\n\n" + "\n".join(fail_lines)
            )

        # 8. Violations
        if role in ("debug", "coder"):
            violations = self.ctx.read_violations()
            if violations:
                v_lines = []
                for v in violations[-5:]:
                    v_lines.append(
                        f"- [{v.get('severity', '?')}] {v.get('file', '?')}: "
                        f"{v.get('description', v.get('rule_quoted', '?'))}"
                    )
                parts.append(
                    "## Active Violations\n\n" + "\n".join(v_lines)
                )

        # 9. The specific task
        parts.append(f"## Your Task\n\n{task}")

        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agent Caller
# ---------------------------------------------------------------------------

def _format_gate_detail(gate: dict) -> str:
    """Return a stable reason/detail string for governed runtime denials."""
    reason = str(gate.get("reason") or "").strip()
    detail = str(gate.get("detail") or "").strip()
    if reason and detail:
        return f"{reason}: {detail}"
    return reason or detail or "governance_denied"


def _runtime_governance_denial(reason: str, detail: str, role_ref: str,
                               component: str, requested_backend: str = "",
                               requested_model: str = "") -> dict:
    """Build a fail-closed runtime gate result."""
    return {
        "allowed": False,
        "enforced": True,
        "reason": reason,
        "detail": detail,
        "component": component,
        "role_ref": role_ref,
        "backend": requested_backend,
        "model": requested_model,
    }


class AgentCaller:
    """Calls agents via subprocess."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.tools_dir = project_root / "tools"

    def _find_python(self) -> str:
        """Find Python executable."""
        return sys.executable

    def _find_claude(self) -> str | None:
        """Find Claude CLI."""
        return shutil.which("claude") or shutil.which("claude.cmd")

    def _check_runtime_gate(
        self,
        role_ref: str,
        component: str,
        requested_backend: str = "",
        requested_model: str = "",
    ) -> dict:
        """Check the specialist runtime gate before spawning backend work."""
        try:
            from control_plane_utils import check_specialist_runtime
        except ImportError:
            return _runtime_governance_denial(
                "control_plane_utils_unavailable",
                "Control plane governance is unavailable; refusing specialist runtime path.",
                role_ref,
                component,
                requested_backend,
                requested_model,
            )

        config_path = _control_plane_read_path(self.root, "cc_engagement.json")
        gateway_path = _control_plane_read_path(self.root, "gateway_config.json")
        event_log_path = _control_plane_read_path(self.root, "event_log.jsonl")
        try:
            return check_specialist_runtime(
                role_ref=role_ref,
                requested_backend=requested_backend,
                requested_model=requested_model,
                component=component,
                config_path=str(config_path),
                gateway_config_path=str(gateway_path),
                event_log_path=str(event_log_path),
            )
        except Exception as exc:
            return _runtime_governance_denial(
                "governance_check_failed",
                f"Specialist runtime governance check failed: {exc}",
                role_ref,
                component,
                requested_backend,
                requested_model,
            )

    def call_consult(self, role: str, prompt: str,
                     image_path: str = "",
                     backend: str = "",
                     model: str = "") -> str:
        """Call consult.py as a separate process."""
        gate = self._check_runtime_gate(
            role_ref=role,
            component="consultant",
            requested_backend=backend,
            requested_model=model,
        )
        if not gate.get("allowed", True):
            return f"[GATED] {_format_gate_detail(gate)}"

        cmd = [
            self._find_python(),
            str(self.tools_dir / "consult.py"),
            "--role", role,
            "--backend", gate.get("backend") or backend,
            "--prompt", prompt,
        ]
        resolved_model = gate.get("model") or model
        if resolved_model:
            cmd.extend(["--model", resolved_model])
        if image_path:
            cmd.extend(["--image", image_path])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.root),
            )
            last_activity = time.time()
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - last_activity > CONSULT_STALL_LIMIT:
                    proc.kill()
                    return (f"[ERROR] Consultation stalled for "
                            f"{CONSULT_STALL_LIMIT}s")
                # Assume active if process is running (consult is a single call)
                last_activity = time.time()

            stdout, _ = proc.communicate(timeout=10)
            return stdout.strip()
        except Exception as e:
            return f"[ERROR] Consultation failed: {e}"

    def call_planner(self, idea: str) -> dict | None:
        """Run the planner as internal Concierge function.

        The Planner is NOT a separate agent - it's a Concierge function
        that uses the planner module directly.
        """
        try:
            sys.path.insert(0, str(self.tools_dir))
            from planner import plan_expand, plan_approve
            from verification_agent import import_plan

            plan_expand(
                idea=idea,
                project_root=str(self.root),
                domain="auto",
                auto_answer=True,
            )

            approved = plan_approve(project_root=str(self.root))

            import_plan(
                str(self.root / "devlog" / "plans" / "plan.current.json"),
                criteria_path=str(self.root / "devlog" / "criteria" / "criteria.json"),
                event_log=str(_control_plane_dir(self.root) / "event_log.jsonl"),
            )

            return approved
        except Exception as e:
            return {"error": str(e)}

    def call_tandem(self, problem: str,
                    backend_a: str = "",
                    backend_b: str = "",
                    model_a: str = "",
                    model_b: str = "",
                    role: str = "architect",
                    domain: str = "",
                    rounds: int = 3) -> dict:
        """Run Tandem debate protocol.

        Tandem is a PROTOCOL, not an agent. The Concierge invokes it
        to have two models debate on architecture or design decisions.
        Returns the structured comparison report.
        """
        gate = self._check_runtime_gate(
            role_ref="tandem",
            component="tandem",
            requested_backend=backend_a,
            requested_model=model_a,
        )
        if not gate.get("allowed", True):
            return {"error": f"[GATED] {_format_gate_detail(gate)}"}

        try:
            sys.path.insert(0, str(self.tools_dir.parent / "scripts"))
            from mcp_consultant import run_tandem

            event_log = str(_control_plane_dir(self.root) / "event_log.jsonl")
            decision_log = str(_control_plane_dir(self.root) / "decision_log.jsonl")

            return run_tandem(
                problem=problem,
                backend_a=backend_a,
                backend_b=backend_b,
                model_a=model_a,
                model_b=model_b,
                role=role,
                rounds=rounds,
                domain=domain,
                event_log=event_log,
                decision_log=decision_log,
            )
        except Exception as e:
            return {"error": str(e)}

    def call_coder(self, instructions: str, backend: str = "",
                   launcher: str = "") -> str:
        """Launch Claude Code to implement instructions.

        Uses Popen with progress monitoring instead of fixed timeout.
        Monitors git status and file modifications to detect stalls.
        Only kills the process if no progress for CODER_STALL_LIMIT seconds.
        """
        backend = str(backend or "").strip().lower()
        launcher = str(launcher or "").strip().lower()
        if backend != "claude" or launcher != "claude":
            return (
                "[ERROR] Coder skipped: this transport requires explicit "
                "backend=claude and launcher=claude"
            )
        gate = self._check_runtime_gate(
            role_ref="coder",
            component="concierge",
            requested_backend=backend,
        )
        if not gate.get("allowed", False):
            return f"[GATED] {_format_gate_detail(gate)}"
        exe = self._find_claude()
        if not exe:
            return "[ERROR] Claude CLI not found"

        try:
            proc = subprocess.Popen(
                [exe, "-p", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=(os.name == "nt"),
                cwd=str(self.root),
            )
            # Send instructions and close stdin
            proc.stdin.write(instructions)
            proc.stdin.close()

            # Monitor progress via file system activity
            output_lines = []
            last_progress = time.time()

            def _check_progress():
                """Check if files were modified recently."""
                try:
                    result = subprocess.run(
                        ["git", "status", "--porcelain"],
                        capture_output=True, text=True, timeout=5,
                        cwd=str(self.root),
                    )
                    return bool(result.stdout.strip())
                except Exception:
                    return False

            while proc.poll() is None:
                time.sleep(PROGRESS_POLL_INTERVAL)

                # Check if still making progress
                if _check_progress():
                    last_progress = time.time()

                stall_time = time.time() - last_progress
                if stall_time > CODER_STALL_LIMIT:
                    proc.kill()
                    return (f"[ERROR] Coder stalled for "
                            f"{int(stall_time)}s with no file changes")

                # Read any available stdout (non-blocking)
                try:
                    import select
                    if select.select([proc.stdout], [], [], 0)[0]:
                        line = proc.stdout.readline()
                        if line:
                            output_lines.append(line)
                except (ImportError, OSError):
                    pass  # select not available on Windows pipes

            # Process finished - read remaining output
            remaining, stderr = proc.communicate(timeout=10)
            if remaining:
                output_lines.append(remaining)

            output = "".join(output_lines).strip()
            if proc.returncode == 0:
                return output if output else "Coder completed (no output)"
            else:
                return f"[ERROR] Coder failed (exit {proc.returncode}): {stderr[:300]}"

        except Exception as e:
            return f"[ERROR] Coder failed: {e}"

    def call_visual_test(self, exe_path: str, actions: str,
                         output_dir: str = "screenshots",
                         build_cmd: str = "",
                         actions_file: str = "") -> str:
        """Run visual_test.py for interactive functional testing.

        Uses visual_test.py (not visual_check.py) for full input
        simulation with keyboard/mouse, multi-screenshot capture.
        """
        cmd = [
            self._find_python(),
            str(self.tools_dir / "visual_test.py"),
            "--exe", exe_path,
            "--output-dir", output_dir,
            "--json",
        ]
        if build_cmd:
            cmd.extend(["--build-cmd", build_cmd])
        if actions_file:
            cmd.extend(["--actions-file", actions_file])
        elif actions:
            cmd.extend(["--actions", actions])
        else:
            # Default: wait, take screenshot
            cmd.extend(["--actions", "wait:3,screenshot:test.png"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=VISUAL_TEST_TIMEOUT,
                cwd=str(self.root),
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return f"[ERROR] Visual test timed out after {VISUAL_TEST_TIMEOUT}s"
        except Exception as e:
            return f"[ERROR] Visual test failed: {e}"

    def call_visual_check(self, exe_path: str, output_path: str,
                          build_cmd: str = "") -> str:
        """Run visual_check.py for a basic single screenshot."""
        cmd = [
            self._find_python(),
            str(self.tools_dir / "visual_check.py"),
            "--exe", exe_path,
            "--output", output_path,
        ]
        if build_cmd:
            cmd.extend(["--build-cmd", build_cmd])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=VISUAL_TEST_TIMEOUT,
                cwd=str(self.root),
            )
            return result.stdout.strip()
        except Exception as e:
            return f"[ERROR] Visual check failed: {e}"

    def call_build(self, build_cmd: str) -> tuple[bool, str]:
        """Run the build command with stall detection."""
        try:
            proc = subprocess.Popen(
                build_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.root),
            )
            last_activity = time.time()
            while proc.poll() is None:
                time.sleep(5)
                # Build is active if process is running - check stall
                if time.time() - last_activity > BUILD_STALL_LIMIT:
                    proc.kill()
                    return False, f"Build stalled for {BUILD_STALL_LIMIT}s"
                # Reset timer if stdout has data
                try:
                    import select
                    if select.select([proc.stdout], [], [], 0)[0]:
                        last_activity = time.time()
                except (ImportError, OSError):
                    last_activity = time.time()  # can't check, assume active

            stdout, stderr = proc.communicate(timeout=10)
            success = proc.returncode == 0
            output = (stdout or "") + "\n" + (stderr or "")
            return success, output.strip()
        except Exception as e:
            return False, str(e)

    def call_tests(self, test_cmd: str) -> tuple[bool, str]:
        """Run tests."""
        return self.call_build(test_cmd)


# ---------------------------------------------------------------------------
# Review Result Parser
# ---------------------------------------------------------------------------

def parse_review_json(raw: str) -> dict:
    """Parse structured JSON review from Reviewer output.

    Returns dict with 'criteria', 'summary', 'overall' keys.
    Falls back to text-based counting if JSON parsing fails.
    """
    # Try to extract JSON from the response
    text = raw.strip()

    # Try direct parse
    try:
        data = json.loads(text)
        if "criteria" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find JSON block in markdown
    for marker in ("```json", "```"):
        if marker in text:
            start = text.index(marker) + len(marker)
            end = text.find("```", start)
            if end > start:
                try:
                    data = json.loads(text[start:end].strip())
                    if "criteria" in data:
                        return data
                except (json.JSONDecodeError, ValueError):
                    pass

    # Fallback: count PASS/FAIL in text
    fail_count = text.lower().count("fail")
    pass_count = text.lower().count("pass")
    total = fail_count + pass_count
    return {
        "criteria": [],
        "summary": {"pass": pass_count, "fail": fail_count, "total": total},
        "overall": "FAIL" if fail_count > 0 else "PASS",
        "_raw": text[:500],
    }


# ---------------------------------------------------------------------------
# Event Logger
# ---------------------------------------------------------------------------

def log_event(project_root: Path, event_type: str, details: dict):
    """Log an event to the control plane."""
    try:
        sys.path.insert(0, str(project_root / "tools"))
        from control_plane_utils import append_event, generate_id, utc_now_iso

        event_log = str(_control_plane_dir(project_root) / "event_log.jsonl")

        existing = []
        el_path = Path(event_log)
        if el_path.exists():
            for line in el_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        if "id" in obj:
                            existing.append(obj["id"])
                    except json.JSONDecodeError:
                        pass

        evt = {
            "id": generate_id("EVT", existing),
            "ts": utc_now_iso(),
            "agent": "concierge",
            "event": event_type,
            "details": details,
            "related_ids": [],
        }
        append_event(event_log, evt)
    except Exception:
        pass  # fail-safe


# ---------------------------------------------------------------------------
# The Concierge
# ---------------------------------------------------------------------------

class Concierge:
    """The Orchestrator Agent.

    The Concierge is the MAIN entry point. The user talks to the Concierge,
    not to the Coder. The Concierge decides which agent to call, generates
    contextual prompts, and manages the build/test/review loop.

    Key orchestration patterns:
    - Planner is an internal function (not a separate agent call)
    - Tandem is a debate protocol for architecture decisions
    - Architect is a permanent expert that guides the Coder
    - Reviewer produces structured JSON (PASS/FAIL per criterion)
    - visual_test.py is used for functional testing (input simulation)
    """

    def __init__(self, project_root: str):
        self.root = Path(project_root).resolve()
        self.state = ConciergeState(project_root)
        self.ctx = ProjectContext(self.root)
        self.prompt_gen = PromptGenerator(self.ctx)
        self.agents = AgentCaller(self.root)
        self.backend = os.environ.get("CONCIERGE_BACKEND", "").strip().lower()
        self.model = os.environ.get("CONCIERGE_MODEL", "")
        self.tandem_backend_a = os.environ.get("TANDEM_BACKEND_A", "").strip().lower()
        self.tandem_backend_b = os.environ.get("TANDEM_BACKEND_B", "").strip().lower()
        self.tandem_model_a = os.environ.get("TANDEM_MODEL_A", "")
        self.tandem_model_b = os.environ.get("TANDEM_MODEL_B", "")
        self._governance_load_failure = None
        self.last_governance_denial = None
        self.engagement = self._load_engagement()

    def log(self, msg: str):
        """Print with timestamp."""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [Concierge] {msg}")

    def _record_governance_denial(self, reason: str, detail: str):
        """Record and log a fail-closed governance denial."""
        self.last_governance_denial = {
            "reason": reason,
            "detail": detail,
        }
        self.log(f"Governance denied ({reason}): {detail}")

    def ask_user(self, question: str) -> str:
        """Ask the user a question and wait for response."""
        print(f"\n>>> {question}")
        try:
            return input("<<< ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    # --- Engagement + Budget gating ---

    def _load_engagement(self) -> dict:
        """Load engagement config. Returns defaults if not found."""
        default = {"level": 4, "backend_policy": "all",
                   "budget_policy": {"max_calls": 0,
                                     "exhaustion_behavior": "degrade"}}
        try:
            tools_dir = self.root / "tools"
            if str(tools_dir) not in sys.path:
                sys.path.insert(0, str(tools_dir))
            from control_plane_utils import load_engagement
            config_path = str(_control_plane_read_path(self.root, "cc_engagement.json"))
            return load_engagement(config_path)
        except ImportError:
            self._governance_load_failure = (
                "control_plane_utils_unavailable",
                "Control plane governance is unavailable; refusing governed component paths.",
            )
            return default
        except Exception as exc:
            self._governance_load_failure = (
                "governance_check_failed",
                f"Engagement governance load failed: {exc}",
            )
            return default

    def _is_active(self, component: str) -> bool:
        """Check if a component is active at the current engagement level.

        Authority model: engagement config gates all LLM-capable components.
        Concierge MUST NOT invoke disabled components.
        """
        if self._governance_load_failure is not None:
            reason, detail = self._governance_load_failure
            self._record_governance_denial(reason, detail)
            return False
        try:
            from control_plane_utils import is_component_active
            return is_component_active(component, config=self.engagement)
        except ImportError:
            self._record_governance_denial(
                "control_plane_utils_unavailable",
                f"Control plane governance is unavailable; refusing {component}.",
            )
            return False
        except Exception as exc:
            self._record_governance_denial(
                "governance_check_failed",
                f"Component governance check failed for {component}: {exc}",
            )
            return False

    def _check_budget(self) -> bool:
        """Check if budget allows another LLM call. Returns True if allowed."""
        if self._governance_load_failure is not None:
            reason, detail = self._governance_load_failure
            self._record_governance_denial(reason, detail)
            return False
        try:
            from control_plane_utils import check_budget
            config_path = str(_control_plane_read_path(self.root, "cc_engagement.json"))
            result = check_budget(config=self.engagement,
                                  config_path=config_path)
            if not result["allowed"]:
                behavior = result["exhaustion_behavior"]
                self.log(f"Budget exhausted ({result['current_calls']}"
                         f"/{result['max_calls']}). "
                         f"Behavior: {behavior}")
                return False
            return True
        except ImportError:
            self._record_governance_denial(
                "control_plane_utils_unavailable",
                "Control plane governance is unavailable; refusing budget-governed runtime path.",
            )
            return False
        except Exception as exc:
            self._record_governance_denial(
                "governance_check_failed",
                f"Budget governance check failed: {exc}",
            )
            return False

    def _assess_local_tandem_capacity(self) -> dict:
        """Return the current machine assessment for local dual-model tandem."""
        fallback = {
            "status": "risky",
            "preferred_mode": "single_model_consult",
            "summary": "Hardware assessment unavailable; stay conservative.",
            "total_ram_gb": None,
            "gpu_vram_gb": None,
            "reasons": ["control_plane_utils unavailable"],
        }
        try:
            from control_plane_utils import assess_local_tandem_capacity
            return assess_local_tandem_capacity()
        except Exception:
            return fallback

    def _resolve_tandem_runtime(self) -> dict:
        """Resolve the effective tandem runtime plan from engagement + env."""
        tandem_cfg = self.engagement.get("tandem", {})
        tandem_cfg = tandem_cfg if isinstance(tandem_cfg, dict) else {}
        mode = str(tandem_cfg.get("mode", "off") or "off").strip().lower()
        if mode == "off":
            return {"mode": "disabled", "reason": "tandem_config_off"}

        backend_a = str(
            tandem_cfg.get("backend_a") or self.tandem_backend_a or ""
        ).strip()
        backend_b = str(
            tandem_cfg.get("backend_b") or self.tandem_backend_b or ""
        ).strip()
        model_a = str(
            tandem_cfg.get("model_a")
            or self.tandem_model_a
            or ""
        ).strip()
        model_b = str(
            tandem_cfg.get("model_b") or self.tandem_model_b or ""
        ).strip()

        if not backend_a or not backend_b:
            return {"mode": "disabled", "reason": "missing_explicit_tandem_backend"}

        return {
            "mode": "tandem",
            "backend_a": backend_a,
            "backend_b": backend_b,
            "model_a": model_a,
            "model_b": model_b,
        }

    # --- Verification Strategy ---

    def _generate_verification_strategy(self, phase_info: dict) -> list[dict]:
        """Generate domain-appropriate verification checks for a phase.

        Instead of hardcoded checks (darkness detection, HTTP status, etc.),
        asks the LLM to produce concrete, executable verification steps
        based on the project type and phase features.

        Returns list of verification checks:
        [{"name": "...", "method": "visual|command|file|test", "command": "...",
          "expected": "...", "fail_if": "..."}]
        """
        if not self._is_active("consultant"):
            return []

        features = phase_info.get("features", [])
        feature_desc = "\n".join(
            f"- {f.get('description', f.get('name', '?'))}"
            for f in features
        ) if features else "general implementation"

        # Read project type from the project context file
        claude_md = self.ctx.read_claude_md(max_lines=50)

        prompt = (
            f"Project context (from {_context_doc_label(self.root)}):\n{claude_md[:1000]}\n\n"
            f"Phase features:\n{feature_desc}\n\n"
            f"Generate 3-5 CONCRETE verification checks for this phase. "
            f"Each check must be executable - not subjective.\n\n"
            f"Types of checks:\n"
            f"- visual: take screenshot, describe EXACTLY what must be visible\n"
            f"- command: run a command, check output contains specific string\n"
            f"- file: check a file exists and contains specific content\n"
            f"- test: run test suite, check all pass\n"
            f"- http: curl an endpoint, check response code/body\n\n"
            f"Return ONLY JSON array:\n"
            f'[{{"name": "check name", "method": "visual|command|file|test|http", '
            f'"command": "command to run or what to look for", '
            f'"expected": "what success looks like", '
            f'"fail_if": "what failure looks like"}}]\n\n'
            f"Be specific to THIS project type. A game needs visual checks. "
            f"A CLI tool needs output checks. A web API needs HTTP checks. "
            f"A library needs test checks."
        )

        result = self.agents.call_consult(
            "reviewer", prompt, backend=self.backend
        )

        # Parse JSON
        try:
            checks = json.loads(result.strip())
            if isinstance(checks, list):
                return checks[:5]
        except (json.JSONDecodeError, ValueError):
            pass

        for marker in ("```json", "```"):
            if marker in result:
                start = result.index(marker) + len(marker)
                end = result.find("```", start)
                if end > start:
                    try:
                        checks = json.loads(result[start:end].strip())
                        if isinstance(checks, list):
                            return checks[:5]
                    except (json.JSONDecodeError, ValueError):
                        pass

        return []

    def _run_verification_checks(self, checks: list[dict],
                                  phase: int) -> dict:
        """Execute verification checks and return structured results.

        Each check is run according to its method type.
        Returns review dict compatible with parse_review_json output.
        """
        criteria = []
        for check in checks:
            name = check.get("name", "unknown")
            method = check.get("method", "file")
            command = check.get("command", "")
            expected = check.get("expected", "")
            fail_if = check.get("fail_if", "")

            status = "FAIL"
            detail = ""

            try:
                if method == "test":
                    # Run test suite
                    success, output = self.agents.call_build(
                        command or "cmake --build build --config Release --target run_tests"
                    )
                    status = "PASS" if success else "FAIL"
                    detail = output[:200] if not success else "All tests passed"

                elif method == "command":
                    # Run command and check output
                    import subprocess as sp
                    result = sp.run(
                        command, shell=True, capture_output=True, text=True,
                        timeout=30, cwd=str(self.root),
                    )
                    output = result.stdout + result.stderr
                    if expected and expected.lower() in output.lower():
                        status = "PASS"
                        detail = f"Found '{expected}' in output"
                    elif result.returncode == 0 and not expected:
                        status = "PASS"
                        detail = "Command succeeded"
                    else:
                        detail = f"Output: {output[:200]}"

                elif method == "file":
                    # Check file exists and optionally contains content
                    target = self.root / command if command else None
                    if target and target.exists():
                        if expected:
                            content = target.read_text(encoding="utf-8",
                                                       errors="ignore")
                            if expected.lower() in content.lower():
                                status = "PASS"
                                detail = f"File contains '{expected}'"
                            else:
                                detail = f"File exists but missing '{expected}'"
                        else:
                            status = "PASS"
                            detail = "File exists"
                    else:
                        detail = f"File not found: {command}"

                elif method == "http":
                    # HTTP check
                    import subprocess as sp
                    result = sp.run(
                        f"curl -s -o /dev/null -w '%{{http_code}}' {command}",
                        shell=True, capture_output=True, text=True,
                        timeout=10, cwd=str(self.root),
                    )
                    code = result.stdout.strip()
                    if expected and code == expected:
                        status = "PASS"
                        detail = f"HTTP {code}"
                    elif code.startswith("2"):
                        status = "PASS"
                        detail = f"HTTP {code}"
                    else:
                        detail = f"HTTP {code} (expected {expected or '2xx'})"

                elif method == "visual":
                    # Visual check - take screenshot and let LLM evaluate
                    # The check's "expected" field describes what must be visible
                    status = "DEFER"  # handled by LLM review later
                    detail = f"Visual: {expected}"

            except Exception as e:
                detail = f"Check error: {str(e)[:100]}"

            criteria.append({
                "name": name,
                "status": status,
                "detail": detail,
            })

        # Count results
        passed = sum(1 for c in criteria if c["status"] == "PASS")
        failed = sum(1 for c in criteria if c["status"] == "FAIL")
        deferred = sum(1 for c in criteria if c["status"] == "DEFER")
        total = len(criteria)

        overall = "FAIL" if failed > 0 else ("PASS" if passed == total else "PARTIAL")

        return {
            "criteria": criteria,
            "summary": {"pass": passed, "fail": failed,
                        "deferred": deferred, "total": total},
            "overall": overall,
        }

    # --- Criteria update ---

    def _update_criteria_for_phase(self, phase_index: int):
        """Update verification criteria after a phase passes.

        Marks criteria associated with the completed phase as 'passed'.
        Idempotent - safe to call multiple times for the same phase.
        """
        try:
            criteria_path = self.root / "devlog" / "criteria" / "criteria.json"
            if not criteria_path.exists():
                return

            criteria = json.loads(criteria_path.read_text(encoding="utf-8"))
            if not isinstance(criteria, list):
                criteria = criteria.get("criteria", [])

            updated = 0
            for c in criteria:
                # Match by phase number in source_history or layer
                layer = c.get("layer", 0)
                status = c.get("status", "pending")
                if layer == phase_index + 1 and status == "pending":
                    c["status"] = "passed"
                    c["verified_at"] = datetime.now(timezone.utc).isoformat()
                    updated += 1

            if updated > 0:
                criteria_path.write_text(
                    json.dumps(criteria, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                self.log(f"Updated {updated} criteria to PASSED for phase {phase_index + 1}")
                log_event(self.root, "criteria_batch_updated", {
                    "phase": phase_index + 1,
                    "updated": updated,
                })
        except Exception as e:
            self.log(f"Warning: could not update criteria: {e}")

    # --- Vision Expansion ---

    def _build_domain_expert_prompt(self, domain: str, plan: dict) -> str:
        """Dynamically construct a domain expert system prompt.

        This is NOT a predefined role - it's an ad-hoc agent built from
        the project's domain, customized in real-time.
        """
        expert_titles = {
            "game": "Senior Game Designer with 15 years of experience",
            "financial": "Fintech Architect and former trading systems lead",
            "physics": "Computational Physicist specializing in real-time simulation",
            "web": "Full-Stack Architect with expertise in scalable web systems",
        }
        title = expert_titles.get(domain,
                                  f"Senior {domain.title()} domain expert")

        phases = plan.get("phases", [])
        phase_summary = "\n".join(
            f"  Phase {p.get('phase', i+1)}: {p.get('name', '?')} "
            f"({len(p.get('features', []))} features)"
            for i, p in enumerate(phases)
        )

        return (
            f"You are a {title}. You are reviewing an implementation plan "
            f"for a project and your job is to identify what's MISSING - "
            f"features, systems, or qualities that a real {domain} project "
            f"needs but the developer didn't think to ask for.\n\n"
            f"Current plan:\n{phase_summary}\n\n"
            f"Task: {self.state.task}\n\n"
            f"Propose 3-7 additions that would make this project "
            f"significantly better. For each:\n"
            f"- Name (short)\n"
            f"- Why it matters (1 sentence)\n"
            f"- Which phase it fits in (existing or new)\n"
            f"- Effort: small/medium/large\n\n"
            f"Only propose things that are genuinely useful, not bloat. "
            f"A {domain} expert would consider these obvious omissions.\n"
            f"Format as a numbered list."
        )

    def _expand_vision(self):
        """Vision expansion: ask user, consult domain expert, enrich plan.

        Three modes:
        1. Focus: skip expansion, use plan as-is
        2. Expand: Planner LLM proposes additions
        3. Expert: spawn ad-hoc domain expert for deeper analysis
        """
        plan = self.ctx.read_plan()
        if not plan:
            return

        # Detect domain
        try:
            from planner import detect_domain
            claude_md = str(_context_doc_path(self.root))
            detection = detect_domain(claude_md)
            domain = detection.get("domain", "generic")
        except ImportError:
            domain = "generic"

        self.log(f"Domain detected: {domain}")
        self.log("Vision expansion options:")
        self.log("  [1] Focus - implement only what was requested")
        self.log("  [2] Expand - AI proposes additions to the plan")
        self.log("  [3] Expert - spawn a domain expert for deep analysis")
        self.log("  [4] Panel - assemble a full expert panel (multiple specialists)")

        choice = self.ask_user(
            "Expand the vision or focus on requirements? [1/2/3/4]"
        )

        if choice == "1" or not choice:
            self.log("Focusing on original requirements.")
            return

        if choice == "4":
            # Full expert panel
            self._run_expert_panel()
            return

        if choice in ("2", "3"):
            # Build the expansion prompt
            if choice == "3" and domain != "generic":
                # Ad-hoc domain expert
                self.log(f"Spawning {domain} domain expert...")
                expert_prompt = self._build_domain_expert_prompt(domain, plan)

                # Use a different backend if available for diversity
                expert_backend = self.tandem_backend_b
                if expert_backend == self.backend:
                    expert_backend = self.backend

                result = self.agents.call_consult(
                    "architect",  # closest role, prompt overrides behavior
                    expert_prompt,
                    backend=expert_backend,
                )
            else:
                # Standard LLM expansion
                self.log("Consulting Planner for vision expansion...")
                phases = plan.get("phases", [])
                phase_list = "\n".join(
                    f"  Phase {p.get('phase', i+1)}: {p.get('name', '?')}"
                    for i, p in enumerate(phases)
                )
                expansion_prompt = (
                    f"I have this implementation plan:\n{phase_list}\n\n"
                    f"Task: {self.state.task}\n\n"
                    f"What's missing? Propose 3-7 additions that would make "
                    f"this project significantly better. Things a senior "
                    f"developer would consider obvious but a first draft "
                    f"might miss. Format as numbered list with: name, why, "
                    f"which phase, effort (small/medium/large)."
                )
                result = self.agents.call_consult(
                    "planner", expansion_prompt, backend=self.backend
                )

            self.state.record("vision_expansion", result[:500])
            self.log(f"Expansion suggestions:\n{result[:1000]}")

            # Ask user which to include
            include = self.ask_user(
                "Which suggestions to include? (all/none/1,3,5 or similar)"
            )

            if include and include.lower() not in ("none", "n", "0"):
                # Store expansion in architect guidance for Coder
                if include.lower() in ("all", "a", "y", "yes"):
                    self.state.architect_guidance += (
                        f"\n\n## Vision Expansion (approved)\n\n{result[:2000]}"
                    )
                else:
                    self.state.architect_guidance += (
                        f"\n\n## Vision Expansion (selected: {include})\n\n"
                        f"{result[:2000]}"
                    )
                self.log("Expansion integrated into Architect guidance.")

                log_event(self.root, "vision_expanded", {
                    "domain": domain,
                    "mode": "expert" if choice == "3" else "planner",
                    "selected": include,
                })
            else:
                self.log("No expansion items selected.")

    # --- Expert Factory ---

    def _analyze_expert_needs(self, plan: dict) -> list[dict]:
        """Ask an LLM to determine which domain experts this project needs.

        Returns a list of expert descriptors:
        [{"title": "...", "domain": "...", "focus": "...", "why": "..."}]
        """
        phases = plan.get("phases", [])
        phase_list = "\n".join(
            f"  Phase {p.get('phase', i+1)}: {p.get('name', '?')} - "
            f"{', '.join(f.get('description', f.get('name', '?')) for f in p.get('features', []))}"
            for i, p in enumerate(phases)
        )

        prompt = (
            f"Project: {self.state.task}\n\n"
            f"Plan:\n{phase_list}\n\n"
            f"As a project planning expert, determine which domain specialists "
            f"would improve this project's plan. For each expert:\n"
            f"- title: their professional title (e.g., 'UX Designer for Trading Platforms')\n"
            f"- domain: their expertise area (e.g., 'financial UX')\n"
            f"- focus: what they should review in this plan (1 sentence)\n"
            f"- why: why this project specifically needs them (1 sentence)\n\n"
            f"Return ONLY a JSON array. Example:\n"
            f'[{{"title": "Market Data Specialist", "domain": "financial data", '
            f'"focus": "data feeds, latency, reliability", '
            f'"why": "trading platform needs real-time price data"}}]\n\n'
            f"Propose 2-5 experts. Only experts genuinely useful for THIS project."
        )

        result = self.agents.call_consult(
            "planner", prompt, backend=self.backend
        )
        self.state.record("expert_needs_analysis", result[:500])

        # Parse JSON from response
        try:
            # Try direct parse
            experts = json.loads(result.strip())
            if isinstance(experts, list):
                return experts[:5]
        except (json.JSONDecodeError, ValueError):
            pass

        # Try to extract JSON from markdown
        for marker in ("```json", "```"):
            if marker in result:
                start = result.index(marker) + len(marker)
                end = result.find("```", start)
                if end > start:
                    try:
                        experts = json.loads(result[start:end].strip())
                        if isinstance(experts, list):
                            return experts[:5]
                    except (json.JSONDecodeError, ValueError):
                        pass

        self.log("Could not parse expert list from LLM response.")
        return []

    def _create_and_consult_expert(self, expert: dict, plan: dict,
                                    backend: str = "") -> str:
        """Build an ad-hoc expert agent and consult it about the plan.

        The expert is constructed dynamically - NOT a predefined role.
        Its system prompt is built from the expert descriptor.
        """
        title = expert.get("title", "Domain Expert")
        domain = expert.get("domain", "general")
        focus = expert.get("focus", "overall quality")

        phases = plan.get("phases", [])
        phase_summary = "\n".join(
            f"  Phase {p.get('phase', i+1)}: {p.get('name', '?')}"
            for i, p in enumerate(phases)
        )

        expert_prompt = (
            f"You are a {title}. Your expertise is in {domain}.\n\n"
            f"A development team is building: {self.state.task}\n\n"
            f"Their plan:\n{phase_summary}\n\n"
            f"Your focus area: {focus}\n\n"
            f"Review this plan from your expert perspective. Identify:\n"
            f"1. Missing features or considerations in your domain\n"
            f"2. Risks the team might not see without your expertise\n"
            f"3. Specific technical recommendations\n\n"
            f"Be concrete - no generic advice. Reference specific phases "
            f"where your suggestions apply. Keep it to 3-5 key points."
        )

        use_backend = backend or self.tandem_backend_b
        result = self.agents.call_consult(
            "architect",  # role doesn't matter, prompt overrides
            expert_prompt,
            backend=use_backend,
        )
        return result

    def _run_expert_panel(self):
        """Full Expert Factory flow: analyze needs, create experts, consult.

        Can run autonomously or collaboratively with the user.
        """
        plan = self.ctx.read_plan()
        if not plan:
            self.log("No plan available for expert panel.")
            return

        self.log("Analyzing which domain experts this project needs...")
        experts = self._analyze_expert_needs(plan)

        if not experts:
            self.log("No additional experts identified.")
            return

        # Present experts to user
        self.log(f"Identified {len(experts)} needed experts:")
        for i, exp in enumerate(experts, 1):
            self.log(f"  [{i}] {exp.get('title', '?')} - {exp.get('why', '?')}")

        choice = self.ask_user(
            "Consult these experts? (all/none/1,3 or similar)"
        )

        if not choice or choice.lower() in ("none", "n", "0"):
            self.log("Expert panel skipped.")
            return

        # Determine which experts to consult
        if choice.lower() in ("all", "a", "y", "yes"):
            selected = experts
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected = [experts[i] for i in indices if 0 <= i < len(experts)]
            except (ValueError, IndexError):
                selected = experts

        # Consult each selected expert
        all_feedback = []
        for exp in selected:
            title = exp.get("title", "Expert")
            self.log(f"Consulting {title}...")

            feedback = self._create_and_consult_expert(exp, plan)
            self.state.record(f"expert_{exp.get('domain', 'unknown')}", feedback[:300])
            all_feedback.append(f"### {title}\n\n{feedback}")
            self.log(f"  {title}: {feedback[:150]}...")

        # Synthesize and add to architect guidance
        if all_feedback:
            synthesis = "\n\n".join(all_feedback)
            self.state.architect_guidance += (
                f"\n\n## Expert Panel Recommendations\n\n{synthesis[:4000]}"
            )
            self.log(f"Expert panel complete. {len(all_feedback)} experts consulted.")

            log_event(self.root, "expert_panel_completed", {
                "experts_consulted": len(all_feedback),
                "expert_titles": [e.get("title", "?") for e in selected],
            })

    # --- Internal: Planner function ---

    def _plan_expand(self):
        """Planner is a Concierge internal function, not a separate agent.

        Runs the planner module directly to expand the user's idea into
        phases with criteria. If programmatic planning fails, falls back
        to consultation.
        """
        self.log("Planning phase (Concierge internal function)...")

        # Try programmatic planner first
        plan_result = self.agents.call_planner(self.state.task)

        if plan_result and "error" not in plan_result:
            plan = self.ctx.read_plan()
            if plan:
                self.state.total_phases = len(plan.get("phases", []))
                self.state.current_phase = 0
                self.log(f"Plan created: {self.state.total_phases} phases.")
                log_event(self.root, "plan_created_by_concierge", {
                    "phases": self.state.total_phases,
                })
                return True

        # Fallback: consult for planning advice
        prompt = self.prompt_gen.generate(
            "planner",
            f"Expand this idea into a full implementation plan: {self.state.task}"
        )
        result = self.agents.call_consult("planner", prompt, backend=self.backend)
        self.state.record("planner_consult", result[:300])
        self.log(f"Planner consultation: {result[:200]}...")

        # Even without a formal plan, set 1 phase for the task
        self.state.total_phases = 1
        self.state.current_phase = 0
        return False

    # --- Internal: Tandem protocol ---

    def _run_tandem_debate(self, problem: str,
                           role: str = "architect",
                           domain: str = "") -> dict:
        """Tandem is a PROTOCOL, not an agent.

        The Concierge invokes it to have two models debate on
        architecture or design decisions. Used during ARCHITECTURE_REVIEW.
        Gated by engagement level (requires tandem component).
        """
        # Engagement gating
        if not self._is_active("tandem"):
            self.log("Tandem skipped (engagement level).")
            return {"skipped": True, "reason": "engagement_gated"}

        runtime = self._resolve_tandem_runtime()
        if runtime.get("mode") == "disabled":
            self.log("Tandem skipped (config off).")
            result = {"skipped": True, "reason": runtime.get("reason", "disabled")}
        elif runtime.get("mode") == "single_model_consult":
            backend = runtime.get("backend", self.tandem_backend_b)
            model = runtime.get("model", "")
            self.log(
                f"Tandem degraded to single-model consult: {runtime.get('reason')} "
                f"({backend}{f'/{model}' if model else ''})"
            )
            analysis = self.agents.call_consult(
                role,
                problem,
                backend=backend,
                model=model,
            )
            result = {
                "fallback": True,
                "mode": "single_model_consult",
                "reason": runtime.get("reason"),
                "backend": backend,
                "model": model,
                "analysis": analysis,
                "agreements": [],
                "divergences": [],
                "consensus_reached": False,
            }
        else:
            backend_a = runtime.get("backend_a", self.tandem_backend_a)
            backend_b = runtime.get("backend_b", self.tandem_backend_b)
            model_a = runtime.get("model_a", "")
            model_b = runtime.get("model_b", "")
            label_a = f"{backend_a}/{model_a}" if model_a else backend_a
            label_b = f"{backend_b}/{model_b}" if model_b else backend_b
            self.log(f"Tandem debate: {role} ({label_a} vs {label_b})")
            assessment = runtime.get("assessment", {})
            if assessment.get("status") == "risky":
                self.log("Local tandem policy: sequential/quantized mode recommended on this machine.")

            result = self.agents.call_tandem(
                problem=problem,
                backend_a=backend_a,
                backend_b=backend_b,
                model_a=model_a,
                model_b=model_b,
                role=role,
                domain=domain,
            )

        if "error" in result:
            self.log(f"Tandem error: {result['error']}")
        else:
            agreements = result.get("agreements", [])
            divergences = result.get("divergences", [])
            self.log(f"Tandem: {len(agreements)} agreements, "
                     f"{len(divergences)} divergences")

        self.state.record("tandem", json.dumps(result)[:500])
        return result

    # --- State handlers ---

    # --- Design Workshop ---

    def _run_design_workshop(self, task: str) -> str:
        """Guide the user from idea to professional design document.

        Interactive process: Concierge asks structured questions, optionally
        consults domain experts, produces a deep design document (not a summary).

        Returns the enriched task description (may be much longer than input).
        """
        self.log("=" * 50)
        self.log("DESIGN WORKSHOP")
        self.log("=" * 50)

        # Check existing design docs
        has_spec = bool(self.ctx.read_spec_files())
        has_plan = self.ctx.has_plan()

        if has_spec or has_plan:
            self.log("Existing design documents found.")
            self.log("  [1] Use existing docs (skip workshop)")
            self.log("  [2] Expand existing docs (fill gaps)")
            choice = self.ask_user("Choice? [1/2]")
            if choice == "1" or not choice:
                return task
            # Fall through to expansion mode

        self.log("How prepared is your project?")
        self.log("  [1] Just an idea (I'll guide you through design)")
        self.log("  [2] I have a partial spec (expand and fill gaps)")
        self.log("  [3] I have a complete document (import as-is)")

        prep = self.ask_user("Preparation level? [1/2/3]")

        if prep == "3":
            self.log("Great - the Planner will import your existing spec.")
            return task

        if prep == "2":
            self.log("I'll ask clarifying questions to fill gaps.")
            return self._workshop_expand(task)

        # Full guided design (prep == "1" or default)
        return self._workshop_guided(task)

    def _workshop_guided(self, task: str) -> str:
        """Full guided design interview. Produces enriched task description."""
        sections = {}

        # 1. Vision
        self.log("\n--- 1/7 VISION ---")
        self.log(f"Starting idea: {task}")
        vision = self.ask_user(
            "Describe your project in 2-3 sentences. "
            "What does it do? Who is it for? What problem does it solve?"
        )
        sections["vision"] = vision or task

        # 2. Users
        self.log("\n--- 2/7 USERS ---")
        users = self.ask_user(
            "Who are the users? What do they do with the system? "
            "Describe 1-3 user types and their main workflows."
        )
        sections["users"] = users or "Not specified"

        # 3. Core features
        self.log("\n--- 3/7 CORE FEATURES ---")
        features = self.ask_user(
            "List the MUST-HAVE features for v1. "
            "What can absolutely not be missing?"
        )
        sections["features"] = features or "Not specified"

        # 4. Domain rules
        self.log("\n--- 4/7 DOMAIN RULES ---")
        rules = self.ask_user(
            "What must ALWAYS be true in your system? "
            "Examples: 'money never disappears', 'positions are always valid', "
            "'data is never lost'. These become invariant tests."
        )
        sections["rules"] = rules or "Not specified"

        # 5. Tech stack
        self.log("\n--- 5/7 TECHNOLOGY ---")
        tech = self.ask_user(
            "Tech stack and constraints? Language, framework, database, "
            "platforms, performance requirements?"
        )
        sections["tech"] = tech or "Not specified"

        # 6. Quality
        self.log("\n--- 6/7 QUALITY ---")
        quality = self.ask_user(
            "Quality requirements? Security, performance, accessibility, "
            "offline support, scalability? (or 'skip')"
        )
        sections["quality"] = quality if quality != "skip" else ""

        # 7. Anything else
        self.log("\n--- 7/7 ANYTHING ELSE ---")
        extra = self.ask_user(
            "Anything else important? References, inspiration, "
            "anti-goals (what it should NOT be)? (or 'done')"
        )
        sections["extra"] = extra if extra != "done" else ""

        # Build enriched task from answers
        enriched_parts = [f"## Project Vision\n\n{sections['vision']}"]
        if sections["users"] != "Not specified":
            enriched_parts.append(f"## Users\n\n{sections['users']}")
        if sections["features"] != "Not specified":
            enriched_parts.append(f"## Core Features\n\n{sections['features']}")
        if sections["rules"] != "Not specified":
            enriched_parts.append(f"## Domain Rules (Invariants)\n\n{sections['rules']}")
        if sections["tech"] != "Not specified":
            enriched_parts.append(f"## Technology\n\n{sections['tech']}")
        if sections.get("quality"):
            enriched_parts.append(f"## Quality Requirements\n\n{sections['quality']}")
        if sections.get("extra"):
            enriched_parts.append(f"## Additional Notes\n\n{sections['extra']}")

        enriched = "\n\n".join(enriched_parts)

        # Save as design doc (NEVER summarize - full content)
        design_dir = self.root / "devlog" / "design"
        design_dir.mkdir(parents=True, exist_ok=True)
        design_path = design_dir / "design-workshop-output.md"
        design_path.write_text(
            f"# Design Workshop Output\n\n"
            f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n"
            f"{enriched}\n",
            encoding="utf-8",
        )
        self.log(f"Design document saved: {design_path}")

        # Ask if user wants expert review
        if self._is_active("consultant"):
            review = self.ask_user(
                "Want domain experts to review and expand this design? (y/n)"
            )
            if review and review.lower() in ("y", "yes"):
                self.state.architect_guidance = enriched[:3000]
                # Expert panel will run during vision expansion

        log_event(self.root, "design_workshop_completed", {
            "sections": len(sections),
            "enriched_length": len(enriched),
        })

        # Return enriched task for the Planner
        return enriched[:5000]

    def _workshop_expand(self, task: str) -> str:
        """Expansion mode: ask clarifying questions about existing spec."""
        self.log("Tell me what you already have (paste or describe).")
        existing = self.ask_user("Your existing spec/description:")

        if not existing:
            return task

        # Use LLM to identify gaps
        if self._is_active("consultant"):
            gap_prompt = (
                f"A user wants to build: {task}\n\n"
                f"They provided this partial spec:\n{existing[:3000]}\n\n"
                f"Identify 3-5 important gaps or ambiguities in this spec. "
                f"For each gap, explain why it matters and suggest a question "
                f"to ask the user to fill it. Be specific, not generic."
            )
            gaps = self.agents.call_consult(
                "planner", gap_prompt, backend=self.backend
            )
            self.log(f"Gaps identified:\n{gaps[:800]}")
            self.state.record("workshop_gaps", gaps[:500])

            answers = self.ask_user(
                "Address these gaps? Respond to each, or 'skip' to proceed."
            )
            if answers and answers.lower() != "skip":
                existing += f"\n\nGap clarifications:\n{answers}"

        # Save enriched spec
        design_dir = self.root / "devlog" / "design"
        design_dir.mkdir(parents=True, exist_ok=True)
        (design_dir / "design-workshop-output.md").write_text(
            f"# Design Workshop Output (Expanded)\n\n{existing}\n",
            encoding="utf-8",
        )
        self.log("Expanded spec saved.")

        return f"{task}\n\nDetailed spec:\n{existing[:5000]}"

    def handle_idle(self, task: str):
        """Start a new task. Optionally runs Design Workshop first."""
        self.state.task = task
        self.state.failures = []
        self.state.completed_phases = []
        self.state.review_results = []
        self.state.architect_guidance = ""
        self.state.phase_attempts = 0
        self.state.debug_attempts = 0
        self.log(f"New task: {task}")
        log_event(self.root, "concierge_started", {"task": task})

        # Design Workshop: help user go from idea to detailed spec
        enriched_task = self._run_design_workshop(task)
        if enriched_task != task:
            self.state.task = enriched_task
            self.log(f"Task enriched ({len(task)} -> {len(enriched_task)} chars)")

        self.state.transition("PLANNING")

    def handle_planning(self):
        """Run the Planner (internal Concierge function)."""
        # Check if plan already exists
        if self.ctx.has_plan():
            self.log("Plan already exists and is approved.")
            plan = self.ctx.read_plan()
            self.state.total_phases = len(plan.get("phases", []))
            self.state.current_phase = 0
            self.log(f"Plan has {self.state.total_phases} phases.")

            if not self.ctx.has_criteria():
                self.log("Exporting plan to criteria...")
                try:
                    sys.path.insert(0, str(self.root / "tools"))
                    from verification_agent import import_plan
                    import_plan(
                        str(self.root / "devlog" / "plans" / "plan.current.json"),
                        criteria_path=str(self.root / "devlog" / "criteria" / "criteria.json"),
                        event_log=str(_control_plane_dir(self.root) / "event_log.jsonl"),
                    )
                except Exception as e:
                    self.log(f"Warning: could not export criteria: {e}")

            # Vision expansion (interactive)
            if self._is_active("consultant"):
                self._expand_vision()

            self.state.transition("ARCHITECTURE_REVIEW")
            return

        # Plan internally (not a separate agent)
        success = self._plan_expand()

        if success:
            # Vision expansion after successful planning
            if self._is_active("consultant"):
                self._expand_vision()
            self.state.transition("ARCHITECTURE_REVIEW")
        else:
            self.log("No formal plan. Skipping to CODING with single phase.")
            self.state.transition("CODING")

    def handle_architecture_review(self):
        """Architect as permanent project expert.

        The Architect reads specs, reviews interfaces, and produces
        persistent guidance that the Coder receives in every phase.
        Gated by engagement level (requires consultant component).
        """
        self.log("Architect reviewing project structure...")

        # Engagement gating: skip if consultant not active
        if not self._is_active("consultant") or not self._check_budget():
            self.log("Architect review skipped (engagement/budget).")
            self.state.transition("CODING")
            return

        phase_info = self.ctx.get_phase_info(0)

        # Generate architect prompt with full context
        prompt = self.prompt_gen.generate(
            "architect",
            "Review the planned interfaces for DENY-protected files. "
            "Provide specific guidance for the Coder: file structure, "
            "interface definitions, dependency direction, and invariants. "
            "Your guidance will be sent to the Coder for ALL phases.",
            phase_info=phase_info,
        )

        result = self.agents.call_consult("architect", prompt, backend=self.backend)
        self.state.record("architect", result[:300])

        # Store architect guidance persistently for Coder
        self.state.architect_guidance = result[:2000]
        self.log(f"Architect guidance stored ({len(result)} chars)")

        log_event(self.root, "architect_review_completed", {
            "guidance_length": len(result),
        })

        self.state.transition("CODING")

    def _get_deny_zones(self) -> str:
        """Read DENY zones from cc_config.json for Coder instructions."""
        try:
            config_path = _control_plane_read_path(self.root, "cc_config.json")
            if not config_path.exists():
                return ""
            config = json.loads(config_path.read_text(encoding="utf-8"))
            zones = config.get("protected_zones", {})
            if isinstance(zones, dict):
                deny = zones.get("deny", [])
                warn = zones.get("warn", [])
            elif isinstance(zones, list):
                deny = [z["path"] for z in zones
                        if z.get("level") == "deny"]
                warn = [z["path"] for z in zones
                        if z.get("level") == "warn"]
            else:
                return ""
            parts = []
            if deny:
                parts.append(f"DENY (immutable after creation): {', '.join(deny)}")
            if warn:
                parts.append(f"WARN (modify with care): {', '.join(warn)}")
            return "\n".join(parts)
        except Exception:
            return ""

    def handle_coding(self):
        """Give the Coder instructions with Architect guidance."""
        phase = self.state.current_phase
        phase_info = self.ctx.get_phase_info(phase)
        phase_name = phase_info.get("name", f"Phase {phase + 1}")

        self.log(f"Phase {phase + 1}/{self.state.total_phases}: {phase_name}")

        # Read DENY zones to include in Coder instructions
        deny_info = self._get_deny_zones()
        zone_block = ""
        if deny_info:
            zone_block = (
                f"\n\nPROTECTED ZONES (hooks enforce these mechanically):\n"
                f"{deny_info}\n"
                f"DENY files: create them carefully in Phase 1 with complete "
                f"interfaces. After creation, they CANNOT be modified. "
                f"Plan ahead."
            )

        # Coder receives Architect's persistent guidance
        prompt = self.prompt_gen.generate(
            "coder",
            f"Implement phase {phase + 1}: {phase_name}\n\n"
            f"Features: {json.dumps(phase_info.get('features', []), indent=2)}\n\n"
            f"RULES:\n"
            f"- NEVER use bash cat/heredoc/echo to create or modify files. "
            f"Always use the Write tool for new files and Edit tool for changes. "
            f"Bash file writes are blocked by hooks and will cause failures.\n"
            f"- Never use cd in Bash commands\n"
            f"- Do not create files that already exist in hooks/ or tools/\n"
            f"- Commit after completing this phase\n"
            f"- Follow {_context_doc_label(self.root)} architecture rules\n"
            f"- Follow the Architect's guidance above\n"
            f"- Append reasoning to devlog/reasoning.md before each commit "
            f"(what you built, what decisions you made, what tradeoffs)"
            f"{zone_block}",
            failures=self.state.failures,
            phase_info=phase_info,
            architect_guidance=self.state.architect_guidance,
        )

        self.log("Sending instructions to Coder...")
        result = self.agents.call_coder(
            prompt,
            backend=self.backend,
            launcher=os.environ.get("CONCIERGE_CODER_LAUNCHER", ""),
        )
        self.state.record("coder", result[:300])
        self.state.phase_attempts += 1

        if "[ERROR]" in result:
            self.log(f"Coder error: {result[:200]}")
            self.state.failures.append(
                f"Phase {phase + 1} coder error: {result[:200]}"
            )
            if self.state.phase_attempts >= 3:
                self.state.transition("DEBUGGING")
            return

        self.log(f"Phase {phase + 1} coding complete.")
        log_event(self.root, "phase_coded", {
            "phase": phase + 1,
            "phase_name": phase_name,
            "attempts": self.state.phase_attempts,
        })

        # Always go to TESTING after coding (build verification is mandatory).
        # Visual testing is attempted if an executable exists.
        self.state.transition("TESTING")

    def handle_testing(self):
        """Run functional tests using visual_test.py.

        Uses visual_test.py (not visual_check.py) for interactive testing
        with input simulation (keyboard, mouse), multi-screenshot capture.
        Falls back to visual_check.py if visual_test.py is unavailable.
        """
        phase = self.state.current_phase
        self.log(f"Functional testing phase {phase + 1}...")

        build_cmd = self.ctx.get_build_cmd()
        exe_path = self.ctx.get_exe_path()
        output_dir = str(self.root / "screenshots")

        # Build first
        self.log(f"Building: {build_cmd}")
        success, output = self.agents.call_build(build_cmd)

        if not success:
            self.log(f"Build FAILED: {output[:200]}")
            self.state.failures.append(f"Build failed: {output[:200]}")
            self.state.transition("DEBUGGING")
            return

        self.log("Build successful. Running functional test...")

        # Check for phase-specific actions file
        actions_file = str(
            self.root / "devlog" / f"test_actions_phase_{phase + 1}.json"
        )
        if not Path(actions_file).exists():
            actions_file = ""

        # Default actions: wait for render, screenshot
        default_actions = (
            f"wait:3,screenshot:phase_{phase + 1}_front.png,"
            f"key:w,wait:1,screenshot:phase_{phase + 1}_moved.png"
        )

        # Try visual_test.py first (full interactive testing)
        visual_test_path = self.agents.tools_dir / "visual_test.py"
        if visual_test_path.exists():
            result = self.agents.call_visual_test(
                exe_path=exe_path,
                actions=default_actions if not actions_file else "",
                output_dir=output_dir,
                actions_file=actions_file,
            )
        else:
            # Fallback to visual_check.py
            screenshot_path = str(
                self.root / "screenshots" / f"phase_{phase + 1}.png"
            )
            result = self.agents.call_visual_check(
                exe_path, screenshot_path, build_cmd
            )

        self.state.record("testing", result[:300])
        self.log(f"Test result: {result[:200]}...")

        self.state.transition("REVIEWING")

    def handle_reviewing(self):
        """Reviewer produces structured JSON output (PASS/FAIL per criterion)."""
        phase = self.state.current_phase
        screenshot_dir = self.root / "screenshots"

        # Find screenshots for this phase
        screenshots = sorted(screenshot_dir.glob(f"phase_{phase + 1}*.png"))

        # Step 1: Run domain-appropriate verification checks
        phase_info = self.ctx.get_phase_info(phase)
        if self._is_active("consultant"):
            self.log("Generating verification strategy for this phase...")
            checks = self._generate_verification_strategy(phase_info)
            if checks:
                self.log(f"Running {len(checks)} verification checks...")
                mech_review = self._run_verification_checks(checks, phase)
                self.state.record("verification_checks",
                                  json.dumps(mech_review)[:500])

                mech_failed = mech_review["summary"].get("fail", 0)
                if mech_failed > 0:
                    self.log(f"Verification: {mech_failed} check(s) FAILED")
                    for c in mech_review.get("criteria", []):
                        if c["status"] == "FAIL":
                            self.state.failures.append(
                                f"Phase {phase + 1}: {c['name']} FAIL - "
                                f"{c['detail']}"
                            )
                    self.state.review_results.append({
                        "phase": phase + 1,
                        "review": mech_review,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    self.state.debug_attempts = 0
                    self.state.transition("DEBUGGING")
                    return
                else:
                    self.log(f"Verification: {mech_review['summary'].get('pass', 0)} "
                             f"check(s) passed")

        # Step 2: Visual review via LLM (if screenshots exist)
        if screenshots:
            self.log(f"Reviewing {len(screenshots)} screenshot(s)...")
            screenshot_path = str(screenshots[0])

            prompt = self.prompt_gen.generate(
                "reviewer",
                "Grade EACH visual/functional criterion as PASS or FAIL.\n"
                "For each element, ask: 'Can I LITERALLY see/verify this? Yes or no?'\n"
                "Do not use confirmation bias - if you cannot clearly identify "
                "the element, mark it FAIL.\n"
                "Return structured JSON with PASS/FAIL per criterion.",
                phase_info=phase_info,
            )

            result = self.agents.call_consult(
                "reviewer", prompt,
                image_path=screenshot_path,
                backend=self.backend,
            )
            self.state.record("reviewer", result[:300])

            # Parse structured JSON review
            review = parse_review_json(result)
            self.state.review_results.append({
                "phase": phase + 1,
                "review": review,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            summary = review.get("summary", {})
            pass_count = summary.get("pass", 0)
            fail_count = summary.get("fail", 0)
            overall = review.get("overall", "UNKNOWN")

            self.log(f"Review: {pass_count} PASS, {fail_count} FAIL "
                     f"(overall: {overall})")

            log_event(self.root, "review_completed", {
                "phase": phase + 1,
                "pass": pass_count,
                "fail": fail_count,
                "overall": overall,
            })

            if overall == "FAIL" or fail_count > 0:
                # Extract specific failures for debugging
                criteria = review.get("criteria", [])
                for c in criteria:
                    if c.get("status") == "FAIL":
                        self.state.failures.append(
                            f"Phase {phase + 1}: {c.get('name', '?')} FAIL "
                            f"- {c.get('detail', 'no detail')}"
                        )
                if not criteria:
                    self.state.failures.append(
                        f"Phase {phase + 1} review: {fail_count} FAIL items"
                    )

                self.state.debug_attempts = 0
                self.state.transition("DEBUGGING")
                return
        else:
            # No screenshots yet - try to capture one if exe exists
            exe_path = self.ctx.get_exe_path()
            if Path(exe_path).exists() and self._is_active("consultant"):
                self.log("No screenshots - capturing one for review...")
                screenshot_path = str(
                    self.root / "screenshots" / f"phase_{phase + 1}_auto.png"
                )
                self.agents.call_visual_check(
                    exe_path, screenshot_path,
                    build_cmd=self.ctx.get_build_cmd(),
                )
                if Path(screenshot_path).exists():
                    self.log("Screenshot captured. Sending to Reviewer...")
                    prompt = self.prompt_gen.generate(
                        "reviewer",
                        "Grade visual quality. Return structured JSON "
                        "with PASS/FAIL per criterion.",
                        phase_info=self.ctx.get_phase_info(phase),
                    )
                    result = self.agents.call_consult(
                        "reviewer", prompt,
                        image_path=screenshot_path,
                        backend=self.backend,
                    )
                    review = parse_review_json(result)
                    self.state.review_results.append({
                        "phase": phase + 1,
                        "review": review,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    overall = review.get("overall", "UNKNOWN")
                    fail_count = review.get("summary", {}).get("fail", 0)
                    self.log(f"Auto-review: {overall}")

                    if overall == "FAIL" or fail_count > 0:
                        for c in review.get("criteria", []):
                            if c.get("status") == "FAIL":
                                self.state.failures.append(
                                    f"Phase {phase + 1}: {c.get('name', '?')} "
                                    f"FAIL - {c.get('detail', '')}"
                                )
                        self.state.debug_attempts = 0
                        self.state.transition("DEBUGGING")
                        return
                else:
                    self.log("Could not capture screenshot. Code-only review.")
            else:
                self.log("No exe or consultant disabled. Code-only pass.")

        # Phase passed - update criteria in verification engine
        self._update_criteria_for_phase(phase)

        self.state.completed_phases.append(phase)
        self.state.current_phase += 1
        self.state.phase_attempts = 0
        self.state.debug_attempts = 0

        log_event(self.root, "phase_completed", {
            "phase": phase + 1,
            "total": self.state.total_phases,
        })

        if self.state.current_phase >= self.state.total_phases:
            self.log("All phases complete! Running final review...")
            self.state.transition("FINAL_REVIEW")
        else:
            self.log(f"Phase {phase + 1} PASSED. Moving to phase {phase + 2}.")
            self.state.transition("CODING")

    def handle_debugging(self):
        """Call Debugger or Socratic for failures."""
        self.state.debug_attempts += 1

        if self.state.debug_attempts >= 3:
            self.log("3+ debug attempts. Escalating to Socratic...")
            prompt = self.prompt_gen.generate(
                "socratic",
                f"Stuck after {self.state.debug_attempts} attempts. "
                f"Challenge my assumptions. What fundamentally different "
                f"approach exists?",
                failures=self.state.failures,
            )
            result = self.agents.call_consult(
                "socratic", prompt, backend=self.backend
            )
            self.state.record("socratic", result[:300])
            self.log(f"Socratic: {result[:200]}...")
        else:
            self.log(f"Debug attempt {self.state.debug_attempts}...")

            screenshot = (
                self.root / "screenshots"
                / f"phase_{self.state.current_phase + 1}.png"
            )
            img = str(screenshot) if screenshot.exists() else ""

            prompt = self.prompt_gen.generate(
                "debug",
                "Diagnose these failures. Form a hypothesis BEFORE "
                "suggesting fixes.",
                failures=self.state.failures,
                phase_info=self.ctx.get_phase_info(self.state.current_phase),
            )
            result = self.agents.call_consult(
                "debug", prompt,
                image_path=img,
                backend=self.backend,
            )
            self.state.record("debug", result[:300])
            self.log(f"Debugger: {result[:200]}...")

        log_event(self.root, "debug_escalation", {
            "attempt": self.state.debug_attempts,
            "failures": self.state.failures[-3:],
        })

        self.state.transition("FIX_LOOP")

    def handle_fix_loop(self):
        """Apply fix and re-test."""
        self.log("Applying fix and re-testing...")
        self.state.transition("TESTING")

    def handle_final_review(self):
        """Run final comprehensive review with structured output."""
        self.log("Running FINAL review...")

        # Run tests
        test_cmd = "cmake --build build --config Release --target run_tests"
        success, output = self.agents.call_tests(test_cmd)
        self.log(f"Tests: {'PASSED' if success else 'FAILED'}")
        if not success:
            self.state.failures.append(f"Final tests failed: {output[:200]}")
            self.state.transition("DEBUGGING")
            return

        # Final screenshot + structured review
        screenshot = self.root / "screenshots" / "final.png"
        exe_path = self.ctx.get_exe_path()
        self.agents.call_visual_check(exe_path, str(screenshot))

        if screenshot.exists():
            prompt = self.prompt_gen.generate(
                "reviewer",
                "FINAL REVIEW. Grade every visual requirement as PASS or "
                "FAIL. This is the last check before release. "
                "Return structured JSON with status per criterion.",
            )
            result = self.agents.call_consult(
                "reviewer", prompt,
                image_path=str(screenshot),
                backend=self.backend,
            )
            self.state.record("final_review", result[:300])

            review = parse_review_json(result)
            self.state.review_results.append({
                "phase": "final",
                "review": review,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.log(f"Final review: {review.get('overall', '?')}")

        # Run Engineering audit at final review (if consultant active)
        if self._is_active("consultant") and self._check_budget():
            self.log("Running Engineering audit on final code...")
            audit_prompt = self.prompt_gen.generate(
                "reviewer",
                "Run a quick engineering audit on the project. Check for: "
                "dependency violations, error handling consistency, "
                "untested critical paths, and configuration issues. "
                "Return structured JSON with PASS/FAIL per criterion.",
            )
            audit_result = self.agents.call_consult(
                "reviewer", audit_prompt, backend=self.backend
            )
            self.state.record("engineering_audit", audit_result[:300])
            log_event(self.root, "audit_invoked", {
                "type": "engineering",
                "trigger": "final_review",
            })

        # Run Scientist validation if specs exist
        specs = self.ctx.read_spec_files()
        if specs:
            self.log("Running Scientist validation against specs...")
            sci_prompt = self.prompt_gen.generate(
                "scientist",
                "Validate the final implementation against the design "
                "specifications. Report conformance and deviations.",
            )
            sci_result = self.agents.call_consult(
                "scientist", sci_prompt, backend=self.backend
            )
            self.state.record("scientist", sci_result[:300])
            self.log(f"Scientist: {sci_result[:200]}...")

        log_event(self.root, "concierge_final_review", {
            "phases_completed": len(self.state.completed_phases),
            "total_phases": self.state.total_phases,
            "failures": len(self.state.failures),
            "review_results": len(self.state.review_results),
        })

        self.state.transition("DONE")

    def handle_done(self):
        """Print summary with structured review results."""
        self.log("=" * 60)
        self.log("TASK COMPLETE")
        self.log("=" * 60)
        self.log(f"Task: {self.state.task}")
        self.log(f"Phases completed: "
                 f"{len(self.state.completed_phases)}/{self.state.total_phases}")
        self.log(f"Source files: {self.ctx.count_src_files()}")
        self.log(f"Criteria: {len(self.ctx.read_criteria())}")
        self.log(f"Events: {len(self.ctx.read_recent_events(1000))}")
        self.log(f"Violations: {len(self.ctx.read_violations())}")
        self.log(f"Total failures encountered: {len(self.state.failures)}")

        # Print structured review summary
        if self.state.review_results:
            self.log("--- Review Results ---")
            for rr in self.state.review_results:
                phase = rr.get("phase", "?")
                review = rr.get("review", {})
                summary = review.get("summary", {})
                self.log(f"  Phase {phase}: {summary.get('pass', 0)} PASS, "
                         f"{summary.get('fail', 0)} FAIL "
                         f"(overall: {review.get('overall', '?')})")

        self.log("=" * 60)

        log_event(self.root, "concierge_completed", {
            "task": self.state.task,
            "phases": len(self.state.completed_phases),
            "failures": len(self.state.failures),
        })

        self.state.save()

    # --- Main loop ---

    def run(self, task: str = "", resume: bool = False):
        """Run the Concierge orchestration loop."""
        if resume:
            if self.state.load():
                self.log(f"Resumed from state: {self.state.state}")
            else:
                self.log("No saved state found. Starting fresh.")
                if not task:
                    task = self.ask_user("What would you like to build?")

        if self.state.state == "IDLE":
            if not task:
                task = self.ask_user("What would you like to build?")
            if not task:
                self.log("No task provided. Exiting.")
                return
            self.handle_idle(task)

        handlers = {
            "PLANNING": self.handle_planning,
            "ARCHITECTURE_REVIEW": self.handle_architecture_review,
            "CODING": self.handle_coding,
            "TESTING": self.handle_testing,
            "REVIEWING": self.handle_reviewing,
            "DEBUGGING": self.handle_debugging,
            "FIX_LOOP": self.handle_fix_loop,
            "FINAL_REVIEW": self.handle_final_review,
            "DONE": self.handle_done,
        }

        for i in range(MAX_ITERATIONS):
            handler = handlers.get(self.state.state)
            if not handler:
                self.log(f"Unknown state: {self.state.state}")
                break

            self.log(f"--- State: {self.state.state} (iteration {i + 1}) ---")

            # Phase-entry marker
            self.state.mark_op(f"state_{self.state.state}", "in_progress")
            self.state.save()

            try:
                handler()
            except Exception as e:
                self.log(f"ERROR in {self.state.state}: {e}")
                self.state.record("error", str(e))
                self.state.failures.append(str(e))
                self.state.save()
                break

            # Phase-exit marker
            self.state.mark_op(f"state_{self.state.state}", "completed")
            self.state.save()

            if self.state.state == "DONE":
                break

        if self.state.state != "DONE":
            self.log(f"Stopped in state: {self.state.state} "
                     f"after {MAX_ITERATIONS} iterations")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="ControlCoding Concierge - The Orchestrator Agent"
    )
    parser.add_argument(
        "--project-root", default=".",
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--task", default="",
        help="Task to accomplish (interactive if not provided)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from saved state",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print current state and exit",
    )

    args = parser.parse_args()

    concierge = Concierge(args.project_root)

    if args.status:
        if concierge.state.load():
            print(f"State: {concierge.state.state}")
            print(f"Task: {concierge.state.task}")
            print(f"Phase: {concierge.state.current_phase + 1}"
                  f"/{concierge.state.total_phases}")
            print(f"Failures: {len(concierge.state.failures)}")
            if concierge.state.review_results:
                print("Reviews:")
                for rr in concierge.state.review_results:
                    review = rr.get("review", {})
                    print(f"  Phase {rr.get('phase')}: "
                          f"{review.get('overall', '?')}")
        else:
            print("No saved state.")
        return

    concierge.run(task=args.task, resume=args.resume)


if __name__ == "__main__":
    main()
