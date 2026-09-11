#!/usr/bin/env python3
"""mcp_consultant.py - MCP server for external consultation.

Exposes a 'consult' tool that sends a structured question to an external LLM
with ZERO project context. The external model reasons purely from the data
provided in the tool call, breaking tunnel vision loops.

Consultation levels:
    L1 (one-shot): single question -> single answer. Default behavior.
    L2 (session):  iterative ping-pong with context. Use session="new" to
                   start, then pass the returned session_id for follow-ups.
                   Max exchanges per-role: CONSULT_MAX_EXCHANGES_<ROLE>
                   (planner=10, others=5). Global fallback: CONSULT_MAX_EXCHANGES.
    L3 (socratic): after N failed exchanges, auto-suggests escalation to
                   socratic role with full session context.
    L4 (human):    after socratic fails, suggests escalating to human via
                   the escalate_to_human tool in mcp_session.py.

Use cases:
    - Debug (L2 session): iterative debugging with context preservation
    - Architecture review: validate design decisions with fresh eyes
    - Plan validation: check a plan for risks before implementation
    - Code review: spot edge cases in isolated logic
    - Socratic challenge (L3): explore alternatives, break tunnel vision
    - Human escalation (L4): present structured problem for human decision

This is the MCP version of consult.py. Instead of being called via Bash,
the AI sees 'consult' as a native tool - same as Read or Write.

Setup in .controlcoding/settings.json:
    {
      "mcpServers": {
        "consultant": {
          "command": "python",
          "args": ["tools/mcp_consultant.py"]
        }
      }
    }

Requirements:
    pip install fastmcp

Backends for consult() (select one explicitly as primary, optionally set a fallback):
    - claude: optional adapter for the official Claude Code CLI. Authentication
      and access remain managed by that vendor CLI. ControlCoding does not read,
      copy, or persist consumer login, OAuth, or session tokens. Requires
      `claude` in PATH.
    - ollama: free, local, requires Ollama running with a model pulled.
    - openai: OpenAI-compatible API. Works with OpenAI (GPT-4), Google Gemini
      (via OpenAI-compatible endpoint), Azure OpenAI, LM Studio, or any
      service exposing a /v1/chat/completions endpoint. Requires
      CONSULT_OPENAI_API_KEY. Set CONSULT_OPENAI_API_BASE to point to
      non-OpenAI services.
    - anthropic: Anthropic API directly. Requires ANTHROPIC_API_KEY.

Backend selection is explicit for consultation, verification, tandem, and
agent runtime paths. PATH discovery and API-key presence are informational
only and never select a backend.

Example configs:
    # Use the optional official Claude CLI adapter with Ollama fallback:
    {
      "mcpServers": {
        "consultant": {
          "command": "python",
          "args": ["tools/mcp_consultant.py"],
          "env": {
            "CONSULT_BACKEND": "claude",
            "CONSULT_FALLBACK_BACKEND": "ollama"
          }
        }
      }
    }

    # Use OpenAI API (ChatGPT):
    {
      "env": {
        "CONSULT_BACKEND": "openai",
        "CONSULT_OPENAI_API_KEY": "sk-...",
        "CONSULT_OPENAI_MODEL": "gpt-4o"
      }
    }

    # Use Google Gemini via OpenAI-compatible endpoint:
    {
      "env": {
        "CONSULT_BACKEND": "openai",
        "CONSULT_OPENAI_API_KEY": "your-gemini-key",
        "CONSULT_OPENAI_API_BASE": "https://generativelanguage.googleapis.com/v1beta/openai",
        "CONSULT_OPENAI_MODEL": "gemini-2.0-flash"
      }
    }
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import urllib.error
import warnings
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

# --- Configuration ---

DEFAULT_BACKEND = os.environ.get("CONSULT_BACKEND", "")
FALLBACK_BACKEND = os.environ.get("CONSULT_FALLBACK_BACKEND", "")
SUPPORTED_CONSULTATION_BACKENDS = frozenset({
    "ollama", "claude", "anthropic", "openai",
})
_FALLBACK_TRIGGER_EXCEPTIONS = (
    urllib.error.URLError,
    ConnectionError,
    TimeoutError,
    subprocess.TimeoutExpired,
    FileNotFoundError,
)
DEFAULT_OLLAMA_MODEL = os.environ.get("CONSULT_OLLAMA_MODEL", "qwen2.5:14b")
DEFAULT_ANTHROPIC_MODEL = os.environ.get(
    "CONSULT_ANTHROPIC_MODEL", "claude-sonnet-4-6"
)
DEFAULT_OPENAI_MODEL = os.environ.get("CONSULT_OPENAI_MODEL", "gpt-4o")
OLLAMA_URL = os.environ.get("CONSULT_OLLAMA_URL", "http://localhost:11434")
OPENAI_API_BASE = os.environ.get(
    "CONSULT_OPENAI_API_BASE", "https://api.openai.com/v1"
)
PROJECT_ROOT = Path(os.environ.get("SESSION_PROJECT_ROOT", ".")).resolve()
CONTROL_PLANE_DIR = ".controlcoding"
LEGACY_CONTROL_PLANE_DIR = ".claude"


def _control_plane_dir() -> Path:
    env_path = os.environ.get("CONSULT_LOG_DIR")
    if env_path:
        return Path(env_path)
    canonical = PROJECT_ROOT / CONTROL_PLANE_DIR
    if canonical.exists():
        return canonical
    legacy = PROJECT_ROOT / LEGACY_CONTROL_PLANE_DIR
    if legacy.exists():
        return legacy
    return canonical


def _control_plane_read_path(*parts: str) -> Path:
    canonical = PROJECT_ROOT / CONTROL_PLANE_DIR / Path(*parts)
    if canonical.exists():
        return canonical
    legacy = PROJECT_ROOT / LEGACY_CONTROL_PLANE_DIR / Path(*parts)
    if legacy.exists():
        return legacy
    return canonical


def _display_control_plane_path(*parts: str) -> str:
    try:
        return _control_plane_dir().joinpath(*parts).relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(_control_plane_dir().joinpath(*parts))

# Maximum prompt size in characters. Prevents the local AI from dumping
# the full codebase into the consultation. The external model should
# receive only the focused problem data.
MAX_PROMPT_CHARS = int(os.environ.get("CONSULT_MAX_PROMPT", "32000"))

# Log file for audit trail
LOG_DIR = str(_control_plane_dir())

# --- External Connection Control (P1-P6) ---
# Maximum number of consultant calls per session. 0 = unlimited.
# Prevents runaway consultation loops (P5: no cost-multiplication).
MAX_CALLS = int(os.environ.get("CONSULT_MAX_CALLS", "0"))

# --- Session Configuration ---
# Maximum exchanges per consultation session before auto-close.
MAX_EXCHANGES = int(os.environ.get("CONSULT_MAX_EXCHANGES", "5"))
# After this many exchanges without resolution, suggest socratic escalation.
ESCALATION_THRESHOLD = int(os.environ.get("CONSULT_ESCALATION_THRESHOLD", "3"))
# Max chars per historical exchange injected into session context.
SESSION_CONTEXT_CHAR_LIMIT = int(os.environ.get("CONSULT_SESSION_CONTEXT_LIMIT", "1500"))


class _CallCounter:
    """Thread-safe call counter for external connection control (P3/P5).

    Tracks actual backend calls (not mocks or errors) to enforce
    the CONSULT_MAX_CALLS limit. When the limit is reached, further
    calls return an error instead of contacting the backend.
    """

    def __init__(self, max_calls: int = 0):
        self._count = 0
        self._max = max_calls
        self._lock = threading.Lock()

    def can_call(self) -> bool:
        if self._max <= 0:
            return True  # unlimited
        with self._lock:
            return self._count < self._max

    def increment(self) -> int:
        with self._lock:
            self._count += 1
            return self._count

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def remaining(self) -> int | str:
        if self._max <= 0:
            return "unlimited"
        with self._lock:
            return max(0, self._max - self._count)


_call_counter = _CallCounter(MAX_CALLS)

def _load_image_b64(image_path: str) -> tuple[str, str] | None:
    """Load an image file and return (base64_data, media_type) or None."""
    p = Path(image_path)
    if not p.exists():
        return None
    suffix = p.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(suffix, "image/png")
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return data, media_type


# --- Session Management ---

def _session_log_path() -> Path:
    """Path to the session-aware consultation log."""
    return Path(LOG_DIR) / "consult_sessions.jsonl"


def _generate_session_id(role: str) -> str:
    """Generate a unique session ID like 'debug-001'."""
    log_path = _session_log_path()
    existing_ids: set[str] = set()
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                sid = entry.get("session_id", "")
                if sid:
                    existing_ids.add(sid)
            except json.JSONDecodeError:
                continue
    # Find next number for this role prefix
    n = 1
    while f"{role}-{n:03d}" in existing_ids:
        n += 1
    return f"{role}-{n:03d}"


def _load_session_history(session_id: str) -> list[dict]:
    """Load all exchanges for a given session_id, ordered chronologically."""
    log_path = _session_log_path()
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            if entry.get("session_id") == session_id:
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def _log_session_exchange(
    session_id: str,
    exchange_num: int,
    role: str,
    problem: str,
    response: str,
    backend: str,
    model: str,
    closed: bool = False,
) -> None:
    """Append a session exchange to the session log."""
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = _session_log_path()

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "exchange_num": exchange_num,
        "role": role,
        "backend": backend,
        "model": model,
        "problem": problem[:2000],
        "response": response[:3000],
        "session_closed": closed,
        "call_number": _call_counter.count,
    }

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _build_session_context(history: list[dict]) -> str:
    """Build a context block from previous session exchanges."""
    if not history:
        return ""
    parts = ["## Previous exchanges in this session\n"]
    for entry in history:
        num = entry.get("exchange_num", "?")
        q = entry.get("problem", "")[:SESSION_CONTEXT_CHAR_LIMIT]
        a = entry.get("response", "")[:SESSION_CONTEXT_CHAR_LIMIT]
        parts.append(f"### Exchange {num}")
        parts.append(f"**Question**: {q}")
        parts.append(f"**Answer**: {a}\n")
    parts.append(
        "## Current question (build on the above context)\n"
    )
    return "\n".join(parts)


ROLE_PROMPTS = {
    "debug": """\
You are an external debugging consultant. You have NO access to the project \
codebase and NO history of previous debugging attempts.

You will receive a structured problem description with selected code snippets \
and data. Your job is to:

1. Analyze the data provided - do not assume anything not explicitly stated
2. Form hypotheses about the root cause, ranked by likelihood
3. For each hypothesis, suggest a specific diagnostic step to confirm or eliminate it
4. If the data is insufficient, state exactly what additional information you need

Rules:
- Focus on ROOT CAUSES, not workarounds or parameter tweaks
- Be specific: "check if X equals Y" not "look into the rendering"
- If you see a likely answer, state it directly with your reasoning
- Do not hedge excessively - give your best assessment with confidence levels
""",
    "architect": """\
You are an external architecture reviewer. You have NO access to the project \
codebase. You will receive a design description with selected code structures.

Your job is to:

1. Identify coupling, hidden dependencies, and responsibility violations
2. Check if the proposed design respects separation of concerns
3. Suggest specific structural improvements with rationale
4. Flag risks: what could break if the system grows or requirements change?

Rules:
- Be concrete: "class X should not depend on Y because Z"
- If the design is solid, say so - do not invent problems
- Focus on maintainability and testability, not style preferences
""",
    "planner": """\
You are an external plan reviewer. You have NO access to the project codebase. \
You will receive an implementation plan with context about the system.

Your job is to:

1. Identify gaps: what does the plan miss or assume incorrectly?
2. Flag risk areas: which steps are most likely to cause problems?
3. Check ordering: are there dependencies that require different sequencing?
4. Suggest specific improvements or missing steps

Rules:
- Assume the implementer is competent - focus on structural risks, not basics
- If a step is vague, ask for specifics rather than guessing
- If the plan is solid, confirm it and note the highest-risk step
""",
    "reviewer": """\
You are an external code reviewer. You have NO access to the full project. \
You will receive isolated code snippets for review.

Your job is to:

1. Find bugs: logic errors, off-by-one, null/undefined access, race conditions
2. Find edge cases the code does not handle
3. Check invariants: does the code maintain stated constraints?
4. Assess clarity: is the intent obvious from the code?

Rules:
- Be specific: line numbers, exact conditions, concrete failing inputs
- Do not suggest style changes unless they mask bugs
- If the code is correct, say so - do not invent issues
""",
    "socratic": """\
You are a Socratic challenger. You have NO access to the project codebase \
and NO attachment to any solution. Your role is to force deeper exploration \
of the solution space before committing to an approach.

You will receive a problem description and the current proposed solution. \
Your job is to:

1. Question the assumptions behind the current approach
   - Why this method and not alternatives?
   - What implicit assumptions does it make?
   - Under what conditions would it fail?

2. Propose alternative approaches the team may not have considered
   - Different paradigms (e.g., linear vs non-linear, static vs dynamic)
   - Different fields that solved similar problems differently
   - Approaches that seem unconventional but might fit

3. Search for prior art
   - Has this problem been solved before in another domain?
   - Are there known patterns or anti-patterns for this type of problem?
   - What does the research literature say?

4. Challenge convergence
   - If the team is settling on a solution too quickly, push back
   - Ask "what would happen if we tried the opposite?"
   - Ask "what are we losing by choosing this approach?"

Rules:
- Never accept "it works" as sufficient - ask "is it the best we can do?"
- Propose at least 2 alternative approaches for every problem
- Be specific: "have you considered X because Y" not "think about alternatives"
- If the current approach is genuinely the best, say so with reasoning
- Do not be contrarian for its own sake - challenge with purpose
""",
    "scientist": """\
You are a Domain Scientist. You have NO access to the project codebase. \
You will receive a theoretical specification (design document, formula, \
algorithm description) alongside the implementation code or test data.

Your job is to:

1. Verify conformance: does the implementation match the spec?
   - Formulas implemented correctly? Units consistent?
   - Algorithms follow the described steps in the right order?
   - Domain rules and constraints enforced as specified?

2. Identify deviations: where does the code diverge from the spec?
   - Missing edge cases the spec describes
   - Approximations or simplifications not mentioned in the spec
   - Hardcoded values that should be configurable per spec

3. Validate test data: do runtime results match theoretical expectations?
   - Expected distributions, ranges, rates within tolerance?
   - Conservation laws / invariants maintained?
   - Statistical properties (mean, variance, correlation) consistent?

4. Flag theoretical concerns:
   - Numerical stability issues (overflow, precision loss, cancellation)
   - Algorithmic complexity mismatches vs spec expectations
   - Domain assumptions that may not hold in production conditions

Report findings in structured JSON format:
```json
{
  "conformance": [
    {"spec_ref": "Section 3.2", "status": "PASS|FAIL|PARTIAL",
     "detail": "What matches or deviates"}
  ],
  "deviations": [
    {"spec_ref": "Section 4.1", "severity": "high|medium|low",
     "description": "What differs and why it matters"}
  ],
  "theoretical_concerns": [
    {"area": "numerical stability", "description": "Specific concern"}
  ],
  "overall_assessment": "Brief summary"
}
```

Rules:
- Reference the spec by section/page/formula number when possible
- Be quantitative: "error exceeds 5% tolerance" not "seems inaccurate"
- If the implementation correctly deviates from spec (e.g., optimization), note it
- Do not review code style - focus only on domain correctness
""",
    "verifier": """\
You are an independent code reviewer. You have NEVER seen this code before. \
You have no attachment to the implementation. You are looking for problems.

You will receive a git diff (code changes) along with the project's \
architectural rules (CLAUDE.md) and optionally the implementation plan.

Your review dimensions:
1. Plan alignment: does the code match the plan? Scope creep? Scope reduction?
2. Architecture alignment: does the code respect CLAUDE.md rules and zone boundaries?
3. Sanity: logic errors, dead code, race conditions, edge cases, infinite loops
4. Security: injection, hardcoded secrets, resource leaks, input validation, permissions
5. Elegance: unnecessary complexity, DRY violations, abstraction level, naming
6. Wiring: imports, exports, responsibilities, dependency graph, circular deps

Report findings in structured JSON format. For each finding:
- Identify the dimension, severity (critical/high/medium/low), file, line
- Describe the problem concretely (not "could be improved" but "X causes Y")
- Suggest a specific fix
- Provide evidence (the actual code line)

If you find no issues in a dimension, say so explicitly.
Do NOT compliment the code. Only report problems.

RESPOND WITH ONLY a JSON object in this format:
```json
{
  "findings": [
    {
      "id": "VF-001",
      "dimension": "sanity",
      "severity": "high",
      "file": "path/to/file.py",
      "line": 42,
      "description": "Concrete problem description",
      "suggested_fix": "Specific fix",
      "evidence": "The actual code line"
    }
  ],
  "summary": {
    "total_findings": 1,
    "by_dimension": {"sanity": 1},
    "by_severity": {"high": 1},
    "overall_assessment": "Brief overall assessment"
  }
}
```
If no issues found, return: {"findings": [], "summary": {"total_findings": 0, "by_dimension": {}, "by_severity": {}, "overall_assessment": "No issues found"}}
""",
}

DEFAULT_ROLE = "debug"


def _format_gate_detail(gate):
    """Return a stable reason/detail string for governed runtime denials."""
    reason = str(gate.get("reason") or "").strip()
    detail = str(gate.get("detail") or "").strip()
    if reason and detail:
        return f"{reason}: {detail}"
    return reason or detail or "governance_denied"


def _runtime_governance_denial(reason, detail, role_ref, component,
                               requested_backend="", requested_model=""):
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


def _check_engagement_gating(component="consultant"):
    """Check if this component is active at the current engagement level.

    Returns None if active, or an error message string if gated.
    """
    try:
        from control_plane_utils import is_component_active
        config_path = str(_control_plane_read_path("cc_engagement.json"))
        if not os.path.exists(config_path):
            return None  # No config = default (active)
        if is_component_active(component, config_path=config_path):
            return None
        return (f"[GATED] {component} is disabled at the current engagement "
                f"level. Increase the level in {_display_control_plane_path('cc_engagement.json')} "
                f"or run: cc setup --engagement")
    except ImportError:
        detail = (
            "Control plane governance is unavailable; refusing "
            f"{component} runtime path."
        )
        gate = {
            "reason": "control_plane_utils_unavailable",
            "detail": detail,
        }
        return f"[GATED] {_format_gate_detail(gate)}"
    except Exception as exc:
        detail = f"Control plane governance check failed for {component}: {exc}"
        gate = {
            "reason": "governance_check_failed",
            "detail": detail,
        }
        return f"[GATED] {_format_gate_detail(gate)}"


def _check_runtime_specialist_gate(role_ref, component="consultant",
                                   requested_backend="", requested_model=""):
    """Check routed specialist runtime gating for this transport layer."""
    try:
        from control_plane_utils import check_specialist_runtime
        return check_specialist_runtime(
            role_ref=role_ref,
            requested_backend=requested_backend,
            requested_model=requested_model,
            component=component,
            config_path=str(_control_plane_read_path("cc_engagement.json")),
            gateway_config_path=str(_control_plane_read_path("gateway_config.json")),
            event_log_path=str(_control_plane_read_path("event_log.jsonl")),
        )
    except ImportError:
        return _runtime_governance_denial(
            "control_plane_utils_unavailable",
            "Control plane governance is unavailable; refusing specialist runtime path.",
            role_ref,
            component,
            requested_backend,
            requested_model,
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


def _configured_role_binding(role_ref):
    """Return an explicit specialist-path backend/model without side effects."""
    try:
        from control_plane_utils import (
            find_specialist_path,
            load_engagement,
            normalize_specialist_paths,
        )
        config_path = str(_control_plane_read_path("cc_engagement.json"))
        if not os.path.exists(config_path):
            return "", ""
        config = load_engagement(config_path)
        path = find_specialist_path(
            role_ref,
            normalize_specialist_paths(config.get("specialist_paths")),
        )
        if not path:
            return "", ""
        return (
            str(path.get("backend") or "").strip(),
            str(path.get("model") or "").strip(),
        )
    except (ImportError, OSError, TypeError, ValueError):
        return "", ""


def _record_runtime_call_started(role_ref, backend, model):
    """Persist an attempt receipt only after validation and consent pass."""
    try:
        from control_plane_utils import append_event, generate_id, utc_now_iso
        event_log = _control_plane_read_path("event_log.jsonl")
        existing = []
        if event_log.exists():
            for line in event_log.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_id = event.get("id")
                if isinstance(event_id, str) and event_id:
                    existing.append(event_id)
        append_event(str(event_log), {
            "id": generate_id("EVT", existing),
            "ts": utc_now_iso(),
            "agent": "consultant",
            "event": "llm_call_routed",
            "details": {
                "specialist_role_id": role_ref,
                "backend": backend,
                "model": model,
                "call_started": True,
            },
            "related_ids": [],
        })
    except Exception:
        pass


# --- Per-Role Configuration ---
# Each role can have its own model and tool set. When using the claude
# backend, tools are passed via --allowedTools to the subprocess.
# Non-claude backends ignore the tools list (text-only, no agent loop).
# Model overrides: set CONSULT_MODEL_<ROLE> env vars (e.g. CONSULT_MODEL_DEBUG).

ROLE_CONFIG = {
    "debug": {
        "model": os.environ.get("CONSULT_MODEL_DEBUG", ""),
        "tools": ["WebSearch", "WebFetch"],
        "max_prompt": int(os.environ.get("CONSULT_MAX_PROMPT_DEBUG", "0")),
        "max_exchanges": int(os.environ.get("CONSULT_MAX_EXCHANGES_DEBUG", "0")),
    },
    "architect": {
        "model": os.environ.get("CONSULT_MODEL_ARCHITECT", ""),
        "tools": ["WebSearch", "WebFetch"],
        "max_prompt": int(os.environ.get("CONSULT_MAX_PROMPT_ARCHITECT", "0")),
        "max_exchanges": int(os.environ.get("CONSULT_MAX_EXCHANGES_ARCHITECT", "0")),
    },
    "planner": {
        "model": os.environ.get("CONSULT_MODEL_PLANNER", ""),
        "tools": ["WebSearch", "WebFetch"],
        "max_prompt": int(os.environ.get("CONSULT_MAX_PROMPT_PLANNER", "0")),
        "max_exchanges": int(os.environ.get("CONSULT_MAX_EXCHANGES_PLANNER", "10")),
    },
    "reviewer": {
        "model": os.environ.get("CONSULT_MODEL_REVIEWER", ""),
        "tools": [],  # Pure code analysis, no external knowledge
        "max_prompt": int(os.environ.get("CONSULT_MAX_PROMPT_REVIEWER", "16000")),
        "max_exchanges": int(os.environ.get("CONSULT_MAX_EXCHANGES_REVIEWER", "0")),
    },
    "socratic": {
        "model": os.environ.get("CONSULT_MODEL_SOCRATIC", ""),
        "tools": ["WebSearch", "WebFetch"],
        "max_prompt": int(os.environ.get("CONSULT_MAX_PROMPT_SOCRATIC", "0")),
        "max_exchanges": int(os.environ.get("CONSULT_MAX_EXCHANGES_SOCRATIC", "0")),
    },
    "verifier": {
        "model": os.environ.get("CONSULT_MODEL_VERIFIER", ""),
        "tools": [],  # Pure code analysis, no external knowledge
        "max_prompt": int(os.environ.get("CONSULT_MAX_PROMPT_VERIFIER", "32000")),
        "max_exchanges": int(os.environ.get("CONSULT_MAX_EXCHANGES_VERIFIER", "0")),
    },
    "scientist": {
        "model": os.environ.get("CONSULT_MODEL_SCIENTIST", ""),
        "tools": ["WebSearch", "WebFetch"],
        "max_prompt": int(os.environ.get("CONSULT_MAX_PROMPT_SCIENTIST", "0")),
        "max_exchanges": int(os.environ.get("CONSULT_MAX_EXCHANGES_SCIENTIST", "5")),
    },
}

# --- MCP Server ---

mcp = FastMCP(
    "consultant",
    instructions=(
        "External consultation tool for independent analysis. "
        "Use for debug (stuck after 3+ attempts), architecture review, "
        "plan validation, code review, Socratic challenge (explore "
        "alternatives), or scientist (validate implementation against "
        "theoretical spec). The external model has ZERO project context "
        "- send ONLY the relevant data. "
        "Available roles: debug, architect, planner, reviewer, socratic, scientist."
    ),
)


def _log_consultation(
    role: str, problem: str, response: str, backend: str, model: str,
    session_id: str = "",
) -> None:
    """Append consultation to audit log."""
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "consult_log.jsonl"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "backend": backend,
        "model": model,
        "prompt_chars": len(problem),
        "response_chars": len(response),
        "problem_summary": problem[:200],
        "call_number": _call_counter.count,
        "calls_remaining": str(_call_counter.remaining),
        "session_id": session_id,
    }

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _call_ollama(
    prompt: str,
    model: str,
    system_prompt: str,
    image: tuple[str, str] | None = None,
) -> str:
    """Send prompt to local Ollama instance. Supports optional image for vision models."""
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
    }
    if image:
        b64_data, _media_type = image
        payload["images"] = [b64_data]

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result.get("response", "")


def _call_anthropic(
    prompt: str,
    model: str,
    system_prompt: str,
    image: tuple[str, str] | None = None,
) -> str:
    """Send prompt to Anthropic API. Supports optional image (base64, media_type)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "ERROR: ANTHROPIC_API_KEY environment variable not set."

    content: list[dict] = []
    if image:
        b64_data, media_type = image
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64_data,
            },
        })
    content.append({"type": "text", "text": prompt})

    data = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": content}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["content"][0]["text"]


def _call_openai(
    prompt: str,
    model: str,
    system_prompt: str,
    image: tuple[str, str] | None = None,
) -> str:
    """Send prompt to OpenAI-compatible API. Supports optional image.

    Works with OpenAI (GPT-4o), Google Gemini (via OpenAI-compatible
    endpoint), Azure OpenAI, LM Studio, or any service that exposes
    a /v1/chat/completions endpoint.
    """
    api_key = os.environ.get("CONSULT_OPENAI_API_KEY")
    if not api_key:
        return "ERROR: CONSULT_OPENAI_API_KEY environment variable not set."

    user_content: list[dict] | str
    if image:
        b64_data, media_type = image
        user_content = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{b64_data}",
                },
            },
            {"type": "text", "text": prompt},
        ]
    else:
        user_content = prompt

    data = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OPENAI_API_BASE}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        choices = result.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return "ERROR: Empty response from OpenAI-compatible API."


def _call_claude(
    prompt: str,
    model: str,
    system_prompt: str,
    tools: list[str] | None = None,
    image_path: str = "",
) -> str:
    """Send a prompt through the optional official Claude Code CLI adapter.

    Authentication and access remain managed by the vendor CLI. This adapter
    does not inspect, copy, or persist consumer login, OAuth, or session tokens.

    When tools are provided (e.g. ["WebSearch", "WebFetch"]), the
    subprocess gets --allowedTools so it can search the web and fetch
    documentation while maintaining project isolation (no Read, Bash,
    Edit, or file access).

    When image_path is provided, adds Read to allowedTools so the
    subprocess can view the screenshot for visual analysis.
    """
    # Combine system prompt and user prompt into a single message
    full_message = f"{system_prompt}\n\n---\n\n{prompt}"

    if image_path:
        full_message += (
            f"\n\n## Screenshot\n"
            f"Read and analyze the screenshot at: {image_path}\n"
            f"Compare what you see against the expected visual criteria "
            f"described above. Be specific about what matches and what does not."
        )

    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        return "ERROR: Claude CLI not found in PATH"

    cmd = [exe, "-p", "-", "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])

    effective_tools = list(tools) if tools else []
    if image_path and "Read" not in effective_tools:
        effective_tools.append("Read")
    if effective_tools:
        cmd.extend(["--allowedTools", ",".join(effective_tools)])

    result = subprocess.run(
        cmd,
        input=full_message,
        capture_output=True,
        text=True,
        timeout=300,
        shell=(os.name == "nt"),
        cwd=tempfile.gettempdir(),  # isolate from project dir
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            return f"ERROR: Claude CLI failed: {stderr}"
        return "ERROR: Claude CLI returned non-zero exit code."

    return result.stdout.strip()


def _normalize_consultation_backend(backend: object) -> str:
    """Return the canonical backend identifier used by consultation dispatch."""
    return str(backend or "").strip().lower()


def _is_valid_consultation_response(response: object) -> bool:
    """Return whether an adapter response is safe to count and log as success."""
    return (
        isinstance(response, str)
        and bool(response.strip())
        and not response.lstrip().startswith("ERROR:")
    )


def _invalid_consultation_response(backend: str) -> str:
    """Build a controlled error without echoing potentially sensitive output."""
    return (
        f"ERROR: Consultation backend '{backend}' returned an invalid response."
    )


def _adapter_response_error(backend: str, response: object) -> str:
    """Classify adapter-reported failures without reflecting provider output."""
    if not isinstance(response, str) or not response.strip():
        return _invalid_consultation_response(backend)

    error = response.strip()
    if not error.startswith("ERROR:"):
        return _invalid_consultation_response(backend)
    if backend == "claude" and error == "ERROR: Claude CLI not found in PATH":
        return error
    if backend == "anthropic" and error.startswith("ERROR: ANTHROPIC_API_KEY"):
        return "ERROR: Anthropic API is not configured: ANTHROPIC_API_KEY is not set."
    if backend == "openai" and error.startswith("ERROR: CONSULT_OPENAI_API_KEY"):
        return (
            "ERROR: OpenAI-compatible API is not configured: "
            "CONSULT_OPENAI_API_KEY is not set."
        )
    if backend == "openai" and error == "ERROR: Empty response from OpenAI-compatible API.":
        return error
    return f"ERROR: Consultation backend '{backend}' reported an operational failure."


def _expected_backend_failure(backend: str, error: BaseException) -> str:
    """Return a safe diagnostic for an expected provider availability failure."""
    if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
        return f"ERROR: Consultation backend '{backend}' timed out."
    if backend == "ollama":
        return (
            f"ERROR: Cannot connect to Ollama at {OLLAMA_URL}. "
            "Ensure Ollama is running and the configured model is available."
        )
    if backend == "anthropic":
        return "ERROR: Cannot connect to Anthropic API."
    if backend == "openai":
        return "ERROR: Cannot connect to the configured OpenAI-compatible API."
    if backend == "claude":
        return "ERROR: Claude CLI could not be started."
    return f"ERROR: Consultation backend '{backend}' is unavailable."


def _unexpected_backend_failure(
    backend: str,
    error: BaseException,
    *,
    fallback: bool = False,
) -> str:
    """Classify programming failures without exposing exception messages."""
    label = "Fallback consultation backend" if fallback else "Consultation backend"
    return f"ERROR: {label} '{backend}' failed unexpectedly ({type(error).__name__})."


def _both_backends_failed(
    primary_backend: str,
    fallback_backend: str,
    fallback_error: str,
) -> str:
    """Report both failures using only an already-sanitized fallback diagnostic."""
    detail = fallback_error.removeprefix("ERROR: ")
    return (
        f"ERROR: Primary backend ({primary_backend}) and fallback "
        f"({fallback_backend}) both failed. Fallback: {detail}"
    )


def _default_consultation_model(backend: str) -> str:
    """Return the model request that corresponds to a supported backend."""
    if backend == "ollama":
        return DEFAULT_OLLAMA_MODEL
    if backend == "anthropic":
        return DEFAULT_ANTHROPIC_MODEL
    if backend == "openai":
        return DEFAULT_OPENAI_MODEL
    return ""


@mcp.tool
def consult(
    problem: str,
    role: str = "",
    domain: str = "",
    code_snippets: str = "",
    tried_already: str = "",
    image_path: str = "",
    backend: str = "",
    model: str = "",
    session: str = "",
) -> str:
    """Send a question to an external LLM with no project context.

    The external model has NO access to the project codebase - it reasons
    purely from the data you provide. This breaks tunnel vision by getting
    an independent analysis from a model with zero accumulated context.

    Consultation levels:
    - L1 (one-shot): default. Single question, single answer, no context.
    - L2 (session): pass session="new" to start an iterative session.
      Returns a session_id. Pass that session_id in subsequent calls to
      maintain context. The consultant sees all previous exchanges.
      Max exchanges per session: configurable per-role via
      CONSULT_MAX_EXCHANGES_<ROLE> (planner=10, others=5 by default).
    - L3 (socratic): after 3 exchanges without resolution, the system
      auto-suggests escalating to role="socratic" with the session context.
      The socratic consultant challenges assumptions and explores alternatives.
    - L4 (human): if socratic also fails, suggests using escalate_to_human
      tool for human decision with full context.

    Use cases:
    - role="debug": stuck on a bug after 3+ fix attempts
    - role="architect": validate a design or spot hidden coupling
    - role="planner": check an implementation plan for gaps and risks
    - role="reviewer": find bugs and edge cases in isolated code
    - role="socratic": challenge assumptions, explore alternatives,
      force deeper thinking before committing to an approach
    - role="scientist": validate implementation against theoretical spec,
      check formulas, algorithms, domain rules, and test data conformance

    Vision support: pass image_path to send a screenshot to the consultant
    for visual analysis. The consultant will compare what it sees against
    the expected criteria described in your problem statement.

    Args:
        problem: Clear description of the question or situation.
                 For debug: what's expected vs what happens.
                 For architect: the design and its constraints.
                 For planner: the plan steps and system context.
                 For reviewer: what the code should do.
                 When using image_path: include the expected visual
                 criteria so the consultant can compare.
        role: Consultation type - "debug" (default), "architect",
              "planner", "reviewer", "socratic", or "scientist".
              Each gets a specialized system prompt.
        domain: Optional domain specialization. When provided, the
                consultant adapts its expertise to the domain.
                Examples: "3D graphics", "finance", "physics simulation",
                "web security", "embedded systems", "machine learning".
                The domain is prepended to the system prompt.
        code_snippets: Relevant code fragments ONLY (not full files).
                       Include function signatures, the specific lines
                       that matter, and surrounding context.
        tried_already: What you already attempted and why it did not work.
                       Prevents the consultant from suggesting the same
                       failed approaches. Most useful for debug role.
        image_path: Absolute path to a screenshot for visual analysis.
                    The consultant will view the image and compare it
                    against the criteria in your problem description.
                    Supported formats: PNG, JPEG, GIF, WebP.
        backend: LLM backend - "claude" (official CLI adapter),
                 "ollama" (local),
                 "openai" (ChatGPT/Gemini/Azure/LM Studio), or
                 "anthropic" (API). Default set via CONSULT_BACKEND env.
        model: Override the default model name for the chosen backend.
        session: Session management. "" = one-shot (L1, default).
                 "new" = start a new iterative session (L2), returns
                 session_id in response. Pass an existing session_id
                 (e.g. "debug-001") to continue that session with full
                 context from previous exchanges.

    Returns:
        The external consultant's analysis. For session calls, includes
        the session_id and exchange count. May include escalation hints
        when approaching exchange limits.
    """
    requested_backend = _normalize_consultation_backend(backend)
    if requested_backend == "fallback":
        requested_backend = ""
    if requested_backend and requested_backend not in SUPPORTED_CONSULTATION_BACKENDS:
        return (
            f"ERROR: Unsupported consultation backend '{requested_backend}'."
        )

    # Resolve role and its configuration
    # DEPRECATION (v2.2): Direct role-based consultation is deprecated.
    # Use the Consultation Protocol (consultation_protocol.py) via
    # BaseAgent.consult() instead. Roles are replaced by agents:
    #   debug -> DebuggerAgent, architect -> ArchitectAgent,
    #   planner -> ConciergeAgent, reviewer -> ReviewerAgent,
    #   socratic -> SocraticAgent, scientist -> ExpertAgent,
    #   verifier -> ReviewerAgent.
    # mcp_consultant.py remains as transport layer only.
    if role and role in ROLE_PROMPTS:
        warnings.warn(
            f"Direct role '{role}' usage is deprecated (v2.2). "
            f"Use BaseAgent.consult(situation=...) with the "
            f"Consultation Protocol instead. "
            f"mcp_consultant.py is now a transport layer.",
            DeprecationWarning,
            stacklevel=2,
        )
    use_role = role if role in ROLE_PROMPTS else DEFAULT_ROLE
    system_prompt = ROLE_PROMPTS[use_role]
    role_cfg = ROLE_CONFIG.get(use_role, {})
    role_tools = role_cfg.get("tools", [])
    role_model = role_cfg.get("model", "")

    selected_model = model
    if not requested_backend:
        requested_backend, binding_model = _configured_role_binding(use_role)
        selected_model = model or binding_model
    if not requested_backend:
        requested_backend = _normalize_consultation_backend(
            os.environ.get("CONSULT_BACKEND", "")
        )
        if requested_backend == "fallback":
            requested_backend = ""
        if requested_backend and requested_backend not in SUPPORTED_CONSULTATION_BACKENDS:
            return (
                f"ERROR: Unsupported consultation backend '{requested_backend}'."
            )
    if not requested_backend:
        return (
            "ERROR: No consultation backend configured. "
            "Pass backend explicitly, configure a role binding, or set CONSULT_BACKEND."
        )

    # Consent/policy gating happens only after backend validation.
    gated = _check_engagement_gating("consultant")
    if gated:
        return gated

    gate = _check_runtime_specialist_gate(
        role_ref=use_role,
        component="consultant",
        requested_backend=requested_backend,
        requested_model=selected_model,
    )
    if not gate.get("allowed", False):
        return f"[GATED] {_format_gate_detail(gate)}"
    use_backend = _normalize_consultation_backend(gate.get("backend"))
    if use_backend not in SUPPORTED_CONSULTATION_BACKENDS:
        return (
            f"ERROR: Unsupported consultation backend '{use_backend}' "
            "returned by runtime gate."
        )
    use_model = gate.get("model") or selected_model or role_model

    # --- Session handling (L2) ---
    session_id = ""
    exchange_num = 0
    session_history: list[dict] = []

    # Per-role max exchanges (0 = use global default)
    role_max_exchanges = role_cfg.get("max_exchanges", 0) or MAX_EXCHANGES

    if session == "new":
        session_id = _generate_session_id(use_role)
        exchange_num = 1
    elif session:
        session_id = session
        session_history = _load_session_history(session_id)
        # Check if session is closed
        if session_history and session_history[-1].get("session_closed"):
            # Reopen: treat as continuation (user explicitly came back)
            exchange_num = len(session_history) + 1
        else:
            exchange_num = len(session_history) + 1
        # Check max exchanges
        if exchange_num > role_max_exchanges:
            return (
                f"Session {session_id} has reached the maximum of "
                f"{role_max_exchanges} exchanges. Start a new session with "
                f'session="new" or escalate to a different role.\n\n'
                f"To review this session's history, check "
                f"{_display_control_plane_path('consult_sessions.jsonl')} for session_id={session_id}"
            )

    # Apply domain specialization
    if domain:
        system_prompt = (
            f"DOMAIN SPECIALIZATION: You are an expert in {domain}. "
            f"Use domain-specific terminology, flag domain-relevant risks, "
            f"and reference standard practices from this field.\n\n"
            + system_prompt
        )

    # For session continuations, add context about the ongoing conversation
    if session_id and session_history:
        system_prompt += (
            "\n\nIMPORTANT: This is an ongoing consultation session. "
            "You will see previous exchanges below. Build on your prior "
            "analysis - do not repeat suggestions already given. Focus on "
            "what is NEW in the current question."
        )

    # Build the structured prompt
    parts = []

    # Inject session context if available
    if session_history:
        parts.append(_build_session_context(session_history))

    parts.append(f"## Problem\n{problem}")

    if code_snippets:
        parts.append(f"## Relevant Code\n```\n{code_snippets}\n```")

    if tried_already:
        parts.append(f"## Already Tried (did not work)\n{tried_already}")

    full_prompt = "\n\n".join(parts)

    # Enforce size limit (per-role override > global default)
    effective_limit = role_cfg.get("max_prompt", 0) or MAX_PROMPT_CHARS
    if len(full_prompt) > effective_limit:
        return (
            f"ERROR: Prompt too large ({len(full_prompt)} chars, "
            f"max {effective_limit}). Send only the relevant code "
            f"snippets and data, not full files. Reduce the input and "
            f"try again."
        )

    # Load image if provided
    image_data = None
    resolved_image_path = ""
    if image_path:
        resolved_image_path = str(Path(image_path).resolve())
        image_data = _load_image_b64(resolved_image_path)
        if image_data is None:
            return f"ERROR: Image not found at {image_path}"

    # --- External Connection Control ---
    # P5: check call limit before contacting any backend
    if not _call_counter.can_call():
        remaining = _call_counter.remaining
        total = _call_counter.count
        return (
            f"ERROR: Consultation limit reached ({total}/{MAX_CALLS} calls). "
            f"This prevents runaway consultation loops. If you need more calls, "
            f"set CONSULT_MAX_CALLS to a higher value or 0 (unlimited)."
        )

    def _try_backend(bk, mdl):
        if bk == "claude":
            mdl = mdl or ""  # uses the official CLI's configured default
            _record_runtime_call_started(use_role, bk, mdl or "cli-default")
            return (
                _call_claude(
                    full_prompt, mdl, system_prompt,
                    tools=role_tools, image_path=resolved_image_path,
                ),
                bk,
                mdl or "cli-default",
            )
        elif bk == "anthropic":
            mdl = mdl or DEFAULT_ANTHROPIC_MODEL
            _record_runtime_call_started(use_role, bk, mdl)
            return (
                _call_anthropic(full_prompt, mdl, system_prompt, image=image_data),
                bk,
                mdl,
            )
        elif bk == "openai":
            mdl = mdl or DEFAULT_OPENAI_MODEL
            _record_runtime_call_started(use_role, bk, mdl)
            return (
                _call_openai(full_prompt, mdl, system_prompt, image=image_data),
                bk,
                mdl,
            )
        elif bk == "ollama":
            mdl = mdl or DEFAULT_OLLAMA_MODEL
            _record_runtime_call_started(use_role, bk, mdl)
            return (
                _call_ollama(full_prompt, mdl, system_prompt, image=image_data),
                bk,
                mdl,
            )
        raise ValueError(f"Unsupported consultation backend '{bk}'.")

    primary_backend = use_backend
    primary_error = ""
    try:
        response, use_backend, use_model = _try_backend(use_backend, use_model)
        if not _is_valid_consultation_response(response):
            primary_error = _adapter_response_error(primary_backend, response)
    except _FALLBACK_TRIGGER_EXCEPTIONS as error:
        primary_error = _expected_backend_failure(primary_backend, error)
    except Exception as e:
        return _unexpected_backend_failure(primary_backend, e)

    if primary_error:
        fallback_backend = _normalize_consultation_backend(FALLBACK_BACKEND)
        if not fallback_backend or fallback_backend == primary_backend:
            return primary_error
        if fallback_backend not in SUPPORTED_CONSULTATION_BACKENDS:
            return (
                "ERROR: Unsupported consultation fallback backend "
                f"'{fallback_backend}'."
            )

        fallback_requested_model = _default_consultation_model(fallback_backend)
        try:
            fallback_gate = _check_runtime_specialist_gate(
                role_ref=use_role,
                component="consultant",
                requested_backend=fallback_backend,
                requested_model=fallback_requested_model,
            )
        except Exception:
            return (
                f"ERROR: Runtime gate failed for fallback backend "
                f"'{fallback_backend}'."
            )
        if fallback_gate.get("allowed") is not True:
            return (
                f"ERROR: Fallback backend '{fallback_backend}' was denied "
                f"by runtime gate: {_format_gate_detail(fallback_gate)}"
            )

        gated_fallback_backend = _normalize_consultation_backend(
            fallback_gate.get("backend")
        )
        if gated_fallback_backend not in SUPPORTED_CONSULTATION_BACKENDS:
            return (
                "ERROR: Runtime gate returned unsupported fallback backend "
                f"'{gated_fallback_backend}'."
            )
        if gated_fallback_backend != fallback_backend:
            return (
                f"ERROR: Runtime gate rewrote fallback backend "
                f"'{fallback_backend}' to '{gated_fallback_backend}'."
            )

        fallback_model = (
            fallback_gate.get("model") or fallback_requested_model
        )
        try:
            response, use_backend, use_model = _try_backend(
                fallback_backend, fallback_model
            )
        except _FALLBACK_TRIGGER_EXCEPTIONS as error:
            return _both_backends_failed(
                primary_backend,
                fallback_backend,
                _expected_backend_failure(fallback_backend, error),
            )
        except Exception as error:
            return _unexpected_backend_failure(
                fallback_backend, error, fallback=True
            )
        if not _is_valid_consultation_response(response):
            return _both_backends_failed(
                primary_backend,
                fallback_backend,
                _adapter_response_error(fallback_backend, response),
            )
        response = f"[Fallback: {fallback_backend}]\n\n{response}"

    # Track successful call (P3/P5: external connection control)
    call_num = _call_counter.increment()

    # Log for audit (include call count for cost transparency - P4)
    _log_consultation(
        use_role, full_prompt, response, use_backend, use_model,
        session_id=session_id,
    )

    # Log session exchange with full Q&A for context reconstruction
    if session_id:
        is_closing = exchange_num >= role_max_exchanges
        _log_session_exchange(
            session_id=session_id,
            exchange_num=exchange_num,
            role=use_role,
            problem=problem,
            response=response,
            backend=use_backend,
            model=use_model,
            closed=is_closing,
        )

    if image_path:
        # Log image usage separately for audit
        log_dir = Path(LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "consult_log.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "vision_consultation",
            "role": use_role,
            "image_path": image_path,
            "backend": use_backend,
            "session_id": session_id,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # --- Build response footer ---
    footer_parts = ["[costStatus: unknown]"]

    # P4: cost transparency
    if MAX_CALLS > 0:
        footer_parts.append(f"[Consultation {call_num}/{MAX_CALLS}]")

    # Session info
    if session_id:
        footer_parts.append(
            f"[Session: {session_id} | Exchange {exchange_num}/{role_max_exchanges}]"
        )

        # L3: Socratic escalation hint
        if (
            exchange_num >= ESCALATION_THRESHOLD
            and use_role != "socratic"
        ):
            footer_parts.append(
                f"[CC ESCALATION HINT: {exchange_num} exchanges without "
                f"resolution. Consider escalating to socratic for lateral "
                f"thinking:\n"
                f'  consult(role="socratic", session="{session_id}", '
                f'problem="Stuck on ... after {exchange_num} exchanges. '
                f'Current approach: ... What assumptions am I making wrong?")]'
            )

        # L4: Human escalation hint (after socratic in same session)
        if use_role == "socratic" and exchange_num >= 2:
            footer_parts.append(
                "[CC ESCALATION HINT: Socratic consultation ongoing. If "
                "still unresolved, consider escalating to human:\n"
                '  escalate_to_human(problem="...", '
                f'session_id="{session_id}")]'
            )

        # Session closing warning
        if exchange_num == role_max_exchanges:
            footer_parts.append(
                f"[Session {session_id} reached max exchanges. "
                f"Session auto-closed. Start a new session if needed.]"
            )

    if footer_parts:
        response += "\n\n---\n" + "\n".join(footer_parts)

    return response


# ---------------------------------------------------------------------------
# Verifier (Sprint 5a)
# ---------------------------------------------------------------------------

VERIFIER_DIMENSIONS = [
    "plan_alignment", "architecture_alignment", "sanity",
    "security", "elegance", "wiring",
]

VERIFIER_DOMAIN_CHECKLISTS = {
    "financial": (
        "\n## Domain-specific checks (financial)\n"
        "- Decimal precision: monetary values must NOT use float/double\n"
        "- Transaction atomicity: state changes must be all-or-nothing\n"
        "- Audit trail: every state mutation must be logged\n"
        "- Race conditions in concurrent order processing\n"
        "- Conservation of value: sum(debits) == sum(credits)\n"
    ),
    "web": (
        "\n## Domain-specific checks (web)\n"
        "- SQL injection: parameterized queries only\n"
        "- XSS: user input escaped before rendering\n"
        "- CSRF: state-changing endpoints need token protection\n"
        "- Input validation: all external input validated\n"
        "- Secrets: no hardcoded passwords, API keys, tokens\n"
        "- CORS: properly configured, not wildcard\n"
    ),
    "game": (
        "\n## Domain-specific checks (game)\n"
        "- Frame independence: behavior identical at any frame rate\n"
        "- State serialization: save/load preserves exact state\n"
        "- Resource lifecycle: every loaded resource freed\n"
        "- Memory management: no unbounded allocations in game loop\n"
    ),
    "physics": (
        "\n## Domain-specific checks (physics)\n"
        "- Conservation laws: energy/momentum conserved within tolerance\n"
        "- Numerical stability: no division by zero, no NaN propagation\n"
        "- Determinism: same seed + same input = identical output\n"
        "- Float comparison: use epsilon, never exact equality\n"
    ),
    "data": (
        "\n## Domain-specific checks (data pipeline)\n"
        "- Idempotency: re-running produces same result\n"
        "- Ordering: message/event ordering preserved\n"
        "- Schema evolution: backward compatible changes\n"
        "- Backpressure: producers handle slow consumers\n"
    ),
    "generic": "",
}

DEFAULT_VERIFIER_BACKEND = os.environ.get("VERIFIER_BACKEND", "")
VERIFIER_REPORT_PATH = f"{CONTROL_PLANE_DIR}/verifier_report.json"


def verify_code(
    diff,
    claude_md="",
    plan_json="",
    focus=None,
    domain="",
    backend="",
    model="",
):
    """Public API for independent code verification.

    Args:
        diff: Git diff string to verify.
        claude_md: CLAUDE.md content for architecture alignment check.
        plan_json: Plan content (JSON string) for plan alignment check.
        focus: List of dimensions to check (default: all 6).
        domain: Project domain for adapted checklist.
        backend: Explicit verifier backend.
        model: Model override.

    Returns:
        dict with: timestamp, backend, findings, summary.
    """
    focus = focus or VERIFIER_DIMENSIONS
    use_backend = _normalize_consultation_backend(
        backend or os.environ.get("VERIFIER_BACKEND", "")
    )
    if use_backend == "fallback":
        use_backend = ""
    if not use_backend:
        return {
            "error": "No verifier backend configured explicitly",
            "backend": "",
            "callStarted": False,
        }
    if use_backend not in SUPPORTED_CONSULTATION_BACKENDS:
        return {
            "error": f"Unsupported verifier backend '{use_backend}'",
            "backend": use_backend,
            "callStarted": False,
        }
    gate = _check_runtime_specialist_gate(
        role_ref="verifier",
        component="verification",
        requested_backend=use_backend,
        requested_model=model,
    )
    if not gate.get("allowed", False):
        return {
            "error": f"[GATED] {_format_gate_detail(gate)}",
            "backend": use_backend,
            "callStarted": False,
        }
    use_backend = gate.get("backend") or use_backend
    model = gate.get("model") or model

    # Track truncation
    DIFF_LIMIT = 16000
    CONTEXT_LIMIT = 8000
    diff_truncated = len(diff) > DIFF_LIMIT
    plan_truncated = len(plan_json) > CONTEXT_LIMIT if plan_json else False
    claude_md_truncated = len(claude_md) > CONTEXT_LIMIT if claude_md else False

    # Build the structured prompt
    parts = []

    if plan_json and "plan_alignment" in focus:
        parts.append(f"## Implementation Plan\n{plan_json[:CONTEXT_LIMIT]}")

    if claude_md and "architecture_alignment" in focus:
        parts.append(f"## Architecture Rules (CLAUDE.md)\n{claude_md[:CONTEXT_LIMIT]}")

    # Domain checklist
    domain_extra = VERIFIER_DOMAIN_CHECKLISTS.get(
        domain.lower() if domain else "generic", "")
    if domain_extra:
        parts.append(domain_extra)

    # Focus dimensions
    dim_text = ", ".join(focus)
    parts.append(f"## Focus dimensions: {dim_text}")

    diff_note = ""
    if diff_truncated:
        diff_note = (f"\n\n**WARNING: diff truncated from {len(diff)} to "
                     f"{DIFF_LIMIT} chars. Review is partial.**")
    parts.append(f"## Code to review (git diff)\n```diff\n{diff[:DIFF_LIMIT]}\n```{diff_note}")

    full_prompt = "\n\n".join(parts)

    system_prompt = ROLE_PROMPTS["verifier"]

    # Call the backend
    try:
        if use_backend == "claude":
            raw = _call_claude(full_prompt, model or "", system_prompt)
        elif use_backend == "anthropic":
            raw = _call_anthropic(full_prompt, model or DEFAULT_ANTHROPIC_MODEL,
                                  system_prompt)
        elif use_backend == "openai":
            raw = _call_openai(full_prompt, model or DEFAULT_OPENAI_MODEL,
                               system_prompt)
        elif use_backend == "ollama":
            raw = _call_ollama(full_prompt, model or DEFAULT_OLLAMA_MODEL,
                               system_prompt)
        else:
            raise ValueError(f"Unsupported verifier backend '{use_backend}'")
    except Exception as e:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backend": use_backend,
            "scope": "incremental",
            "files_reviewed": [],
            "findings": [],
            "summary": {
                "total_findings": 0,
                "by_dimension": {},
                "by_severity": {},
                "overall_assessment": f"Verification failed: {e}",
            },
            "error": str(e),
            "costStatus": "unknown",
            "callStarted": True,
        }

    _call_counter.increment()

    # Parse response
    report = _parse_verifier_response(raw, use_backend)
    report["costStatus"] = "unknown"
    report["callStarted"] = True

    # Add truncation warnings
    if diff_truncated or plan_truncated or claude_md_truncated:
        truncations = []
        if diff_truncated:
            truncations.append(f"diff ({len(diff)} -> {DIFF_LIMIT} chars)")
        if plan_truncated:
            truncations.append(f"plan ({len(plan_json)} -> {CONTEXT_LIMIT} chars)")
        if claude_md_truncated:
            truncations.append(f"CLAUDE.md ({len(claude_md)} -> {CONTEXT_LIMIT} chars)")
        report["truncated"] = truncations
        report["scope"] = "partial"

    # Log
    _log_consultation("verifier", full_prompt[:2000], raw[:3000],
                      use_backend, model or "default")

    return report


def _parse_verifier_response(raw, backend):
    """Parse verifier LLM response into structured report."""
    text = raw.strip()

    # Extract JSON from code fences
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()

    ts = datetime.now(timezone.utc).isoformat()

    try:
        data = json.loads(text)
        findings = data.get("findings", [])
        summary = data.get("summary", {})

        # Assign IDs if missing
        for i, f in enumerate(findings, 1):
            if not f.get("id"):
                f["id"] = f"VF-{i:03d}"
            # Validate dimension
            if f.get("dimension") not in VERIFIER_DIMENSIONS:
                f["dimension"] = "sanity"
            # Validate severity
            if f.get("severity") not in ("critical", "high", "medium", "low"):
                f["severity"] = "medium"

        # Build summary if not provided
        if not summary.get("total_findings"):
            by_dim = {}
            by_sev = {}
            for f in findings:
                d = f.get("dimension", "sanity")
                s = f.get("severity", "medium")
                by_dim[d] = by_dim.get(d, 0) + 1
                by_sev[s] = by_sev.get(s, 0) + 1
            summary = {
                "total_findings": len(findings),
                "by_dimension": by_dim,
                "by_severity": by_sev,
                "overall_assessment": summary.get("overall_assessment",
                                                  f"{len(findings)} findings"),
            }

        # Extract files from findings
        files = sorted(set(f.get("file", "") for f in findings if f.get("file")))

        return {
            "timestamp": ts,
            "backend": backend,
            "scope": "incremental",
            "files_reviewed": files,
            "findings": findings,
            "summary": summary,
        }
    except (json.JSONDecodeError, KeyError):
        # Fallback: no structured findings
        return {
            "timestamp": ts,
            "backend": backend,
            "scope": "incremental",
            "files_reviewed": [],
            "findings": [],
            "summary": {
                "total_findings": 0,
                "by_dimension": {},
                "by_severity": {},
                "overall_assessment": "Could not parse verifier response as JSON",
            },
            "raw_response": raw[:3000],
        }


def save_verifier_report(report, project_root="."):
    """Save verifier report to the canonical control-plane directory."""
    report_path = Path(project_root) / VERIFIER_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(report_path)


@mcp.tool
def consult_verify(
    diff: str,
    claude_md: str = "",
    plan_json: str = "",
    focus: str = "",
    domain: str = "",
    backend: str = "",
) -> str:
    """Independent code verification across 6 dimensions.

    Uses a different AI backend (typically GPT) to review code written by
    the primary AI (typically Claude). This eliminates self-review bias.

    The 6 dimensions: plan_alignment, architecture_alignment, sanity,
    security, elegance, wiring.

    Findings are saved to the control-plane verifier report and can be imported
    to the Verification Engine via import-verify.

    Args:
        diff: Git diff string to verify.
        claude_md: CLAUDE.md content for architecture checks.
        plan_json: Plan content for plan alignment checks.
        focus: Comma-separated dimensions to check (default: all).
        domain: Project domain (financial, web, game, physics, data).
        backend: Explicit verifier backend, or VERIFIER_BACKEND when set.

    Returns:
        JSON report with findings and summary.
    """
    # DEPRECATION (v2.2): Direct verify calls are deprecated.
    # Use BaseAgent.consult(situation="code_review") or
    # ReviewerAgent for structured code verification.
    warnings.warn(
        "consult_verify() is deprecated (v2.2). "
        "Use BaseAgent.consult(situation='code_review') with the "
        "Consultation Protocol instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    requested_backend = _normalize_consultation_backend(
        backend or os.environ.get("VERIFIER_BACKEND", "")
    )
    if requested_backend == "fallback":
        requested_backend = ""
    if not requested_backend:
        return json.dumps({
            "error": "No verifier backend configured explicitly",
            "callStarted": False,
        }, indent=2)
    if requested_backend not in SUPPORTED_CONSULTATION_BACKENDS:
        return json.dumps({
            "error": f"Unsupported verifier backend '{requested_backend}'",
            "callStarted": False,
        }, indent=2)

    # Engagement gating
    gated = _check_engagement_gating("verification")
    if gated:
        return gated

    focus_list = [f.strip() for f in focus.split(",") if f.strip()] if focus else None

    report = verify_code(
        diff=diff,
        claude_md=claude_md,
        plan_json=plan_json,
        focus=focus_list,
        domain=domain,
        backend=requested_backend,
    )

    if report.get("callStarted") is False:
        clean = {k: v for k, v in report.items() if k != "raw_response"}
        return json.dumps(clean, indent=2, ensure_ascii=False)

    # Save report for import-verify
    try:
        save_verifier_report(report)
    except Exception:
        pass

    # Log event
    try:
        from control_plane_utils import append_event, generate_id, utc_now_iso
        event_log = f"{CONTROL_PLANE_DIR}/event_log.jsonl"
        existing = []
        el = Path(event_log)
        if el.exists():
            for line in el.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        if "id" in obj:
                            existing.append(obj["id"])
                    except json.JSONDecodeError:
                        pass
        evt_id = generate_id("EVT", existing)
        event = {
            "id": evt_id,
            "ts": utc_now_iso(),
            "agent": "verifier",
            "event": "verification_run",
            "details": {
                "backend": report.get("backend", ""),
                "total_findings": report.get("summary", {}).get("total_findings", 0),
                "by_dimension": report.get("summary", {}).get("by_dimension", {}),
                "files_reviewed": report.get("files_reviewed", []),
            },
            "related_ids": [f.get("id", "") for f in report.get("findings", [])],
        }
        append_event(event_log, event)
    except Exception:
        pass

    # Return summary without raw_response
    clean = {k: v for k, v in report.items() if k != "raw_response"}
    return json.dumps(clean, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tandem Agent (Sprint 3)
# ---------------------------------------------------------------------------

# Tandem configuration
TANDEM_MAX_ROUNDS = int(os.environ.get("TANDEM_MAX_ROUNDS", "5"))
TANDEM_DEADLOCK_THRESHOLD = 2  # rounds of no convergence before socratic

# Domain-adapted role prompts for tandem mode
TANDEM_DOMAIN_PROMPTS = {
    "financial": (
        "You are a senior quantitative developer reviewing a trading system "
        "architecture. Focus on transaction integrity, decimal precision for "
        "monetary values, audit trail completeness, and race conditions in "
        "concurrent order processing."
    ),
    "finance": (
        "You are a senior quantitative developer reviewing a trading system "
        "architecture. Focus on transaction integrity, decimal precision for "
        "monetary values, audit trail completeness, and race conditions in "
        "concurrent order processing."
    ),
    "game": (
        "You are a senior game engine architect reviewing a game system design. "
        "Focus on state serialization, frame-rate independence, resource loading "
        "lifecycle, memory management, and deterministic behavior."
    ),
    "physics": (
        "You are a computational physicist reviewing a simulation architecture. "
        "Focus on conservation laws, numerical stability, deterministic behavior "
        "with seed control, and floating-point comparison with epsilon."
    ),
    "web": (
        "You are a senior web architect reviewing a web application design. "
        "Focus on OWASP top 10, input validation, SQL injection, XSS, CSRF "
        "protection, and hardcoded secrets."
    ),
    "data": (
        "You are a senior data engineer reviewing a data pipeline architecture. "
        "Focus on idempotency, ordering guarantees, schema evolution, and "
        "backpressure handling."
    ),
}

# Control plane utilities for decision/event logging
try:
    from control_plane_utils import (
        append_event as _cp_append_event,
        generate_id as _cp_generate_id,
        utc_now_iso as _cp_utc_now_iso,
    )
    _HAS_CONTROL_PLANE = True
except ImportError:
    _HAS_CONTROL_PLANE = False

    def _cp_utc_now_iso():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _cp_append_event(path, data):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")

    def _cp_generate_id(prefix, existing=None):
        import re as _re
        existing = existing or []
        mx = 0
        pat = _re.compile(rf"^{_re.escape(prefix)}-(\d+)$")
        for eid in existing:
            m = pat.match(eid)
            if m:
                mx = max(mx, int(m.group(1)))
        return f"{prefix}-{mx + 1:03d}"


DEFAULT_EVENT_LOG = os.environ.get(
    "TANDEM_EVENT_LOG", f"{CONTROL_PLANE_DIR}/event_log.jsonl"
)
DEFAULT_DECISION_LOG = os.environ.get(
    "TANDEM_DECISION_LOG", f"{CONTROL_PLANE_DIR}/decision_log.jsonl"
)


def _tandem_build_prompt(problem, role_prompt, code_snippets="",
                         image_path=""):
    """Build the tandem initial prompt."""
    parts = [f"## Problem\n{problem}"]
    if code_snippets:
        parts.append(f"## Relevant Code\n```\n{code_snippets}\n```")
    return "\n\n".join(parts)


def _tandem_build_reaction_prompt(problem, other_response, round_num):
    """Build a cross-examination prompt for subsequent rounds."""
    return (
        f"## Original Problem\n{problem}\n\n"
        f"## Your colleague's response (round {round_num - 1})\n"
        f"{other_response}\n\n"
        f"## Instructions\n"
        f"React to your colleague's position. Where do you agree? "
        f"Where do you disagree and why? Be specific and concise."
    )


def _tandem_call_backend(backend, prompt, system_prompt, model="",
                         image_path=""):
    """Call a single backend for tandem. Returns response string."""
    image_data = None
    resolved_path = ""
    if image_path:
        resolved_path = str(Path(image_path).resolve())
        image_data = _load_image_b64(resolved_path)

    _call_counter.increment()
    if backend == "claude":
        return _call_claude(prompt, model or "", system_prompt,
                            image_path=resolved_path)
    elif backend == "anthropic":
        return _call_anthropic(prompt, model or DEFAULT_ANTHROPIC_MODEL,
                               system_prompt, image=image_data)
    elif backend == "openai":
        return _call_openai(prompt, model or DEFAULT_OPENAI_MODEL,
                            system_prompt, image=image_data)
    elif backend == "ollama":
        return _call_ollama(prompt, model or DEFAULT_OLLAMA_MODEL,
                            system_prompt, image=image_data)
    raise ValueError(f"Unsupported tandem backend '{backend}'")


def _tandem_get_system_prompt(role, domain):
    """Get the system prompt for tandem, adapted for domain."""
    base = ROLE_PROMPTS.get(role, ROLE_PROMPTS.get("architect", ""))
    domain_prompt = TANDEM_DOMAIN_PROMPTS.get(domain.lower(), "")
    if domain_prompt:
        return domain_prompt + "\n\n" + base
    if domain:
        return (
            f"DOMAIN: You are an expert in {domain}. "
            f"Use domain-specific terminology and patterns.\n\n" + base
        )
    return base


def _tandem_classify_convergence(response_a, response_b, problem,
                                 backend="", model=""):
    """Use an LLM to classify agreements and divergences.

    Returns a dict with 'agreements' and 'divergences' lists.
    This is a best-effort classification - if the backend is unavailable,
    it falls back to a simple heuristic.
    """
    classify_prompt = (
        "You are an impartial analyst. Compare these two AI responses "
        "to the same problem and produce a JSON classification.\n\n"
        f"## Problem\n{problem[:2000]}\n\n"
        f"## Response A\n{response_a[:3000]}\n\n"
        f"## Response B\n{response_b[:3000]}\n\n"
        "## Instructions\n"
        "Output ONLY a valid JSON object with this structure:\n"
        '{\n'
        '  "agreements": [\n'
        '    {"topic": "...", "position": "...", "confidence": "high|medium|low"}\n'
        '  ],\n'
        '  "divergences": [\n'
        '    {"topic": "...", "position_a": "...", "position_b": "...", '
        '"analysis": "...", "user_decision_needed": true}\n'
        '  ]\n'
        '}\n'
        "Be specific about topics. If they fully agree, divergences is empty. "
        "If they fully disagree, agreements is empty."
    )

    try:
        raw = _tandem_call_backend(backend, classify_prompt,
                                   "You are a JSON classifier. Output ONLY valid JSON.",
                                   model=model)
        # Extract JSON from response (may have markdown code fences)
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        result = json.loads(text)
        if "agreements" in result and "divergences" in result:
            return result
    except (json.JSONDecodeError, Exception):
        pass

    # Fallback: simple heuristic
    return {
        "agreements": [],
        "divergences": [{
            "topic": "Overall approach",
            "position_a": response_a[:200],
            "position_b": response_b[:200],
            "analysis": "Automated classification unavailable - manual review needed",
            "user_decision_needed": True,
        }],
    }


def _tandem_detect_deadlock(prev_classification, curr_classification):
    """Check if the same divergences persist (deadlock)."""
    if not prev_classification or not curr_classification:
        return False
    prev_topics = {d.get("topic", "") for d in prev_classification.get("divergences", [])}
    curr_topics = {d.get("topic", "") for d in curr_classification.get("divergences", [])}
    if not prev_topics or not curr_topics:
        return False
    # Deadlock if divergence topics haven't changed
    return prev_topics == curr_topics


def _tandem_socratic_escalation(problem, response_a, response_b,
                                backend_a, backend_b,
                                backend="", model=""):
    """Escalate to socratic when tandem is deadlocked."""
    socratic_prompt = (
        f"Two AI systems ({backend_a} and {backend_b}) have been debating "
        f"this problem and reached a deadlock:\n\n"
        f"## Problem\n{problem[:2000]}\n\n"
        f"## {backend_a}'s position\n{response_a[:2000]}\n\n"
        f"## {backend_b}'s position\n{response_b[:2000]}\n\n"
        f"## Your task\n"
        f"1. What fundamental assumption is each side making?\n"
        f"2. Is there a synthesis that captures the best of both?\n"
        f"3. What question would resolve this disagreement?\n"
        f"Be specific and concrete."
    )
    system_prompt = ROLE_PROMPTS.get("socratic", "You are a Socratic challenger.")
    try:
        return _tandem_call_backend(backend, socratic_prompt, system_prompt,
                                    model=model)
    except Exception:
        return "Socratic escalation failed - backend unavailable."


def _tandem_log_decisions(divergences, event_log, decision_log):
    """Log divergences to decision_log and event_log."""
    dec_ids = []
    existing_dec_ids = []
    dec_path = Path(decision_log)
    if dec_path.exists():
        for line in dec_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if "id" in obj:
                        existing_dec_ids.append(obj["id"])
                except json.JSONDecodeError:
                    pass

    for d in divergences:
        dec_id = _cp_generate_id("DEC", existing_dec_ids + dec_ids)
        dec_ids.append(dec_id)

        decision = {
            "id": dec_id,
            "timestamp": _cp_utc_now_iso(),
            "type": "architecture_choice",
            "context": d.get("topic", ""),
            "alternatives": [
                d.get("position_a", ""),
                d.get("position_b", ""),
            ],
            "chosen": "",
            "chosen_by": "",
            "source": "tandem",
            "source_id": "",
            "rationale": d.get("analysis", ""),
            "impact": [],
            "requires_reapproval": False,
        }
        _cp_append_event(decision_log, decision)

        # Event log
        evt_existing = []
        evt_path = Path(event_log)
        if evt_path.exists():
            for line in evt_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        if "id" in obj:
                            evt_existing.append(obj["id"])
                    except json.JSONDecodeError:
                        pass

        evt_id = _cp_generate_id("EVT", evt_existing)
        event = {
            "id": evt_id,
            "ts": _cp_utc_now_iso(),
            "agent": "tandem",
            "event": "tandem_diverged",
            "details": {
                "topic": d.get("topic", ""),
                "decision_id": dec_id,
            },
            "related_ids": [dec_id],
        }
        _cp_append_event(event_log, event)

    return dec_ids


def _tandem_log_agreements(agreements, event_log):
    """Log agreements as events."""
    evt_existing = []
    evt_path = Path(event_log)
    if evt_path.exists():
        for line in evt_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if "id" in obj:
                        evt_existing.append(obj["id"])
                except json.JSONDecodeError:
                    pass

    for a in agreements:
        evt_id = _cp_generate_id("EVT", evt_existing)
        evt_existing.append(evt_id)
        event = {
            "id": evt_id,
            "ts": _cp_utc_now_iso(),
            "agent": "tandem",
            "event": "tandem_converged",
            "details": {
                "topic": a.get("topic", ""),
                "confidence": a.get("confidence", ""),
            },
            "related_ids": [],
        }
        _cp_append_event(event_log, event)


def run_tandem(problem, backend_a="", backend_b="",
               role="architect", rounds=3, convergence="enumerate",
               domain="", code_snippets="", image_path="",
               model_a="", model_b="",
               event_log=DEFAULT_EVENT_LOG,
               decision_log=DEFAULT_DECISION_LOG,
               classify_backend="", classify_model=""):
    """Execute the tandem comparison workflow.

    Returns structured JSON report (section 4.4 format).
    This is the pure logic function - no MCP dependency.
    """
    backend_a = _normalize_consultation_backend(backend_a)
    backend_b = _normalize_consultation_backend(backend_b)
    if backend_a == "fallback":
        backend_a = ""
    if backend_b == "fallback":
        backend_b = ""
    if not backend_a or not backend_b:
        return {
            "error": "Two tandem backends must be configured explicitly",
            "callStarted": False,
        }
    unknown = [
        candidate for candidate in (backend_a, backend_b)
        if candidate not in SUPPORTED_CONSULTATION_BACKENDS
    ]
    if unknown:
        return {
            "error": f"Unsupported tandem backend '{unknown[0]}'",
            "callStarted": False,
        }
    classify_backend = _normalize_consultation_backend(classify_backend) or backend_a
    if classify_backend not in SUPPORTED_CONSULTATION_BACKENDS:
        return {
            "error": f"Unsupported tandem classifier backend '{classify_backend}'",
            "callStarted": False,
        }
    gate = _check_runtime_specialist_gate(
        role_ref="tandem",
        component="tandem",
        requested_backend=backend_a,
        requested_model=model_a,
    )
    if not gate.get("allowed", False):
        return {
            "error": f"[GATED] {_format_gate_detail(gate)}",
            "callStarted": False,
        }

    max_rounds = min(rounds, TANDEM_MAX_ROUNDS)
    system_prompt = _tandem_get_system_prompt(role, domain)

    # Round 1: initial positions
    initial_prompt = _tandem_build_prompt(problem, system_prompt,
                                         code_snippets, image_path)

    try:
        response_a = _tandem_call_backend(backend_a, initial_prompt,
                                          system_prompt, model=model_a,
                                          image_path=image_path)
    except Exception as e:
        response_a = f"ERROR: {backend_a} unavailable - {e}"

    try:
        response_b = _tandem_call_backend(backend_b, initial_prompt,
                                          system_prompt, model=model_b,
                                          image_path=image_path)
    except Exception as e:
        response_b = f"ERROR: {backend_b} unavailable - {e}"

    # Classify round 1
    classification = _tandem_classify_convergence(
        response_a, response_b, problem,
        backend=classify_backend, model=classify_model,
    )

    all_rounds = [{
        "round": 1,
        "response_a": response_a,
        "response_b": response_b,
        "classification": classification,
    }]

    prev_classification = None
    deadlock_count = 0
    socratic_insight = ""

    # Rounds 2+: cross-examination
    for round_num in range(2, max_rounds + 1):
        # Check if consensus already reached
        if not classification.get("divergences"):
            break

        # Build reaction prompts
        reaction_prompt_a = _tandem_build_reaction_prompt(
            problem, response_b, round_num)
        reaction_prompt_b = _tandem_build_reaction_prompt(
            problem, response_a, round_num)

        try:
            response_a = _tandem_call_backend(backend_a, reaction_prompt_a,
                                              system_prompt, model=model_a)
        except Exception as e:
            response_a = f"ERROR: {backend_a} unavailable - {e}"

        try:
            response_b = _tandem_call_backend(backend_b, reaction_prompt_b,
                                              system_prompt, model=model_b)
        except Exception as e:
            response_b = f"ERROR: {backend_b} unavailable - {e}"

        prev_classification = classification
        classification = _tandem_classify_convergence(
            response_a, response_b, problem,
            backend=classify_backend, model=classify_model,
        )

        all_rounds.append({
            "round": round_num,
            "response_a": response_a,
            "response_b": response_b,
            "classification": classification,
        })

        # Deadlock detection
        if _tandem_detect_deadlock(prev_classification, classification):
            deadlock_count += 1
        else:
            deadlock_count = 0

        # Socratic escalation after TANDEM_DEADLOCK_THRESHOLD rounds of deadlock
        if deadlock_count >= TANDEM_DEADLOCK_THRESHOLD:
            socratic_insight = _tandem_socratic_escalation(
                problem, response_a, response_b,
                backend_a, backend_b,
                backend=classify_backend, model=classify_model,
            )
            break

    # Build final report
    final_agreements = classification.get("agreements", [])
    final_divergences = classification.get("divergences", [])
    consensus_reached = len(final_divergences) == 0

    report = {
        "problem": problem,
        "rounds": len(all_rounds),
        "backend_a": backend_a,
        "backend_b": backend_b,
        "agreements": final_agreements,
        "divergences": final_divergences,
        "consensus_reached": consensus_reached,
        "socratic_insight": socratic_insight,
        "round_details": all_rounds,
        "costStatus": "unknown",
        "callStarted": True,
    }

    # Log divergences to decision_log + event_log (NOT criteria)
    if final_divergences:
        _tandem_log_decisions(final_divergences, event_log, decision_log)

    # Log agreements as events
    if final_agreements:
        _tandem_log_agreements(final_agreements, event_log)

    return report


@mcp.tool
def consult_tandem(
    problem: str,
    backend_a: str = "",
    backend_b: str = "",
    role: str = "architect",
    rounds: int = 3,
    convergence: str = "enumerate",
    domain: str = "",
    code_snippets: str = "",
    image_path: str = "",
) -> str:
    """Cross-AI comparison: send the same problem to two AI backends.

    Collects independent responses, identifies agreements and divergences,
    and presents a structured comparison for user decision. Uses ping-pong
    rounds where each AI reacts to the other's position.

    Divergences are logged to decision_log.jsonl and event_log.jsonl for
    tracking. They do NOT automatically become verification criteria -
    use import-tandem --mode criteria-for-unresolved for that.

    Socratic escalation triggers automatically after 2 rounds of deadlock.

    Args:
        problem: The problem or architecture question to compare.
        backend_a: Explicit first AI backend.
        backend_b: Explicit second AI backend.
        role: Consultation role - "architect" (default), "debug",
              "reviewer", "planner".
        rounds: Max ping-pong rounds (default 3, max 5).
        convergence: "consensus" (stop when agreed) or "enumerate"
                     (default, report all topics).
        domain: Project domain for adapted prompts (financial, game,
                physics, web, data).
        code_snippets: Relevant code fragments for both AIs to review.
        image_path: Optional screenshot path for visual comparison.

    Returns:
        Structured JSON report with agreements, divergences,
        consensus status, and socratic insight if triggered.
    """
    # DEPRECATION (v2.2): Direct tandem calls are deprecated.
    # Use BaseAgent.consult(situation=..., criticality="high") which
    # automatically enables multi-source consultation via the
    # Consultation Protocol.
    warnings.warn(
        "consult_tandem() is deprecated (v2.2). "
        "Use BaseAgent.consult(criticality='high') with the "
        "Consultation Protocol instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    backend_a = _normalize_consultation_backend(backend_a)
    backend_b = _normalize_consultation_backend(backend_b)
    if backend_a == "fallback":
        backend_a = ""
    if backend_b == "fallback":
        backend_b = ""
    if not backend_a or not backend_b:
        return json.dumps({
            "error": "Two tandem backends must be configured explicitly",
            "callStarted": False,
        }, indent=2)
    for candidate in (backend_a, backend_b):
        if candidate not in SUPPORTED_CONSULTATION_BACKENDS:
            return json.dumps({
                "error": f"Unsupported tandem backend '{candidate}'",
                "callStarted": False,
            }, indent=2)

    # Engagement gating
    gated = _check_engagement_gating("tandem")
    if gated:
        return gated

    gate = _check_runtime_specialist_gate(
        role_ref="tandem",
        component="tandem",
        requested_backend=backend_a,
    )
    if not gate.get("allowed", True):
        return json.dumps({
            "error": f"[GATED] {_format_gate_detail(gate)}",
            "callStarted": False,
        }, indent=2)

    # Check call limit
    if not _call_counter.can_call():
        return json.dumps({
            "error": f"Consultation limit reached ({_call_counter.count}/{MAX_CALLS})",
            "callStarted": False,
        }, indent=2)

    report = run_tandem(
        problem=problem,
        backend_a=backend_a,
        backend_b=backend_b,
        role=role,
        rounds=rounds,
        convergence=convergence,
        domain=domain,
        code_snippets=code_snippets,
        image_path=image_path,
    )

    if report.get("callStarted") is False:
        summary = {k: v for k, v in report.items() if k != "round_details"}
        return json.dumps(summary, indent=2, ensure_ascii=False)

    # Log the consultation
    _log_consultation(
        role=f"tandem-{role}",
        problem=problem,
        response=json.dumps(report, indent=2)[:3000],
        backend=f"{backend_a}+{backend_b}",
        model="tandem",
    )

    # Return structured JSON (without verbose round_details for readability)
    summary = {k: v for k, v in report.items() if k != "round_details"}
    return json.dumps(summary, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
