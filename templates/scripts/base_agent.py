#!/usr/bin/env python3
"""base_agent.py - Shared foundation for all LLM-based agents.

Every agent (Concierge, Visual, Architect, Coder, Reviewer, Debugger,
Socratic, Expert) inherits from BaseAgent and gets:
- Persistent LLM conversation session (multi-turn with images)
- Deterministic state store (survives context compression)
- Adaptive tool execution (native tool_use or JSON fence per backend)
- Safety guardrails (timeout, max steps, stuck detection, parse retry)
- Report generation from state

BaseAgent is abstract. Concrete agents extend it with their own state, tools,
and prompts.

Design document: dev/design/01_DSN_BaseAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S0)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

# Import backend helpers from mcp_consultant.py
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
try:
    from mcp_consultant import _load_image_b64
except ImportError:
    def _load_image_b64(path):
        """Fallback: read image as base64 if mcp_consultant not available."""
        import base64
        p = Path(path)
        if not p.exists():
            return None
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        suffix = p.suffix.lower()
        media = {"png": "image/png", "jpg": "image/jpeg",
                 "jpeg": "image/jpeg", "gif": "image/gif",
                 "webp": "image/webp"}.get(suffix.lstrip("."), "image/png")
        return (data, media)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ActionRecord:
    """Record of a single tool execution."""
    step: int
    tool: str
    params: dict
    result: str
    success: bool
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")


@dataclass
class Finding:
    """An observation or result recorded during the agent session."""
    step: int
    description: str
    evidence: list = field(default_factory=list)
    severity: str = "info"  # info | warning | bug | pass | fail


@dataclass
class ToolCall:
    """A parsed tool invocation from LLM output."""
    name: str
    params: dict
    raw: str = ""


@dataclass
class ToolResult:
    """Result of executing a tool."""
    tool: str
    params: dict
    result: str
    success: bool
    image_path: str | None = None


# ---------------------------------------------------------------------------
# AgentStateBase
# ---------------------------------------------------------------------------

@dataclass
class AgentStateBase:
    """Deterministic state - the source of truth for agent progress.

    This is NOT the LLM conversation. This is the ledger that survives
    context compression, backend changes, and session restarts.
    """

    # Identity
    session_id: str = ""
    agent_type: str = ""
    started_at: str = ""
    mode: str = "autonomous"    # autonomous | interactive
    phase: str = "init"

    # Progress
    current_step: int = 0

    # Safety counters
    total_steps: int = 0
    total_llm_calls: int = 0
    max_steps: int = 200
    timeout_seconds: float = 600.0
    max_retries_per_task: int = 3
    stuck_threshold: int = 5
    stuck_count: int = 0
    elapsed_seconds: float = 0.0
    stop_reason: str = ""           # "", "done", "timeout", "max_steps", "stuck", "error"

    # Logs
    actions_log: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def next_step(self) -> int:
        """Increment and return current step."""
        self.current_step += 1
        self.total_steps += 1
        return self.current_step

    def log_action(self, tool: str, params: dict, result: str,
                   success: bool = True):
        """Record a tool execution."""
        self.actions_log.append(ActionRecord(
            step=self.current_step, tool=tool, params=params,
            result=result, success=success,
        ))

    def log_finding(self, description: str, evidence: list | None = None,
                    severity: str = "info"):
        """Record an observation."""
        self.findings.append(Finding(
            step=self.current_step, description=description,
            evidence=evidence or [], severity=severity,
        ))

    def log_error(self, error: str):
        """Record an error."""
        self.errors.append(f"[step {self.current_step}] {error}")

    def to_dict(self) -> dict:
        """Serialize state for JSON persistence."""
        return asdict(self)

    def save(self, path: str):
        """Persist state to JSON file for crash recovery."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Convert ActionRecord/Finding to dicts for serialization
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
            "findings": [
                asdict(f) if isinstance(f, Finding) else f
                for f in self.findings
            ],
            "actions_log": [
                asdict(a) if isinstance(a, ActionRecord) else a
                for a in self.actions_log
            ],
        }
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "AgentStateBase":
        """Load state from JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        state = cls()
        for key, value in data.items():
            if hasattr(state, key):
                setattr(state, key, value)
        return state

    def to_context_summary(self) -> str:
        """Generate text summary for rebuilding LLM awareness after
        context compression."""
        lines = [
            f"=== AGENT STATE SUMMARY (step {self.current_step}) ===",
            f"Agent: {self.agent_type} | Mode: {self.mode} | Phase: {self.phase}",
            f"Steps: {self.total_steps} | LLM calls: {self.total_llm_calls}",
            f"Findings: {len(self.findings)} | Errors: {len(self.errors)}",
        ]
        if self.findings:
            lines.append("\nKey findings:")
            for f in self.findings[-10:]:
                sev = f.severity if isinstance(f, Finding) else f.get("severity", "info")
                desc = f.description if isinstance(f, Finding) else f.get("description", "")
                lines.append(f"  [{sev}] {desc}")
        if self.errors:
            lines.append(f"\nRecent errors: {'; '.join(self.errors[-5:])}")
        lines.append("=== END STATE SUMMARY ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# BackendAdapter
# ---------------------------------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OPENAI_API_BASE = os.environ.get("CONSULT_OPENAI_API_BASE",
                                  "https://api.openai.com/v1")


class BackendAdapter:
    """Multi-turn LLM communication with image support.

    Wraps existing backend functions but maintains conversation state
    (messages array) across calls.
    """

    def __init__(self, backend: str, model: str = ""):
        known = ("claude", "anthropic", "ollama", "openai", "session")
        backend = str(backend or "").strip().lower()
        if backend == "fallback":
            backend = ""
        if backend and backend not in known:
            raise ValueError(f"Unknown backend: {backend}. Use: {', '.join(known)}")
        self.backend = backend
        self.model = model
        self.messages: list[dict] = []

    def set_system_prompt(self, prompt: str):
        """Set or replace the system prompt."""
        self.messages = [m for m in self.messages if m["role"] != "system"]
        self.messages.insert(0, {"role": "system", "content": prompt})

    def add_user_message(self, text: str, image_path: str | None = None):
        """Add a user message with optional image."""
        msg = {"role": "user", "content": text}
        if image_path:
            img_data = _load_image_b64(image_path)
            if img_data:
                msg["_image"] = img_data
                msg["_image_path"] = image_path
        self.messages.append(msg)

    def add_assistant_message(self, text: str):
        """Record the assistant's response."""
        self.messages.append({"role": "assistant", "content": text})

    def call(self, tools: list[dict] | None = None) -> str:
        """Send conversation to LLM and return response.
        Response is automatically appended as assistant message."""
        if not self.backend:
            response = "ERROR: No backend configured explicitly"
        elif self.backend == "anthropic":
            response = self._call_anthropic(tools)
        elif self.backend == "ollama":
            response = self._call_ollama()
        elif self.backend == "openai":
            response = self._call_openai()
        elif self.backend == "claude":
            response = self._call_claude(tools)
        elif self.backend == "session":
            response = self._call_session()
        else:
            response = f"ERROR: Unknown backend {self.backend}"
        self.add_assistant_message(response)
        return response

    def get_message_count(self) -> int:
        return len(self.messages)

    def estimate_tokens(self) -> int:
        """Rough token estimate: ~4 chars per token, ~1000 per image."""
        total = 0
        for msg in self.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            if msg.get("_image"):
                total += 1000
        return total

    def summarize_and_rebuild(self, state: AgentStateBase,
                              keep_last_n: int = 5):
        """Compress conversation: keep system + state summary + last N messages."""
        system = [m for m in self.messages if m["role"] == "system"]
        recent = [m for m in self.messages if m["role"] != "system"][-keep_last_n:]
        summary_msg = {
            "role": "user",
            "content": (
                "The conversation was compressed to save context. "
                "Here is the current state summary:\n\n"
                + state.to_context_summary()
            ),
        }
        self.messages = system + [summary_msg] + recent

    # --- Backend implementations ---

    def _get_system_prompt(self) -> str:
        for m in self.messages:
            if m["role"] == "system":
                return m["content"]
        return ""

    def _get_api_messages(self) -> list[dict]:
        """Build messages array for API calls (excluding system)."""
        result = []
        for m in self.messages:
            if m["role"] == "system":
                continue
            if m.get("_image") and self.backend == "anthropic":
                b64, media = m["_image"]
                result.append({
                    "role": m["role"],
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": media, "data": b64}},
                        {"type": "text", "text": m["content"]},
                    ],
                })
            elif m.get("_image") and self.backend in ("openai", "ollama"):
                b64, media = m["_image"]
                if self.backend == "openai":
                    result.append({
                        "role": m["role"],
                        "content": [
                            {"type": "image_url", "image_url": {
                                "url": f"data:{media};base64,{b64}"}},
                            {"type": "text", "text": m["content"]},
                        ],
                    })
                else:
                    result.append({
                        "role": m["role"],
                        "content": m["content"],
                        "images": [b64],
                    })
            else:
                result.append({"role": m["role"], "content": m["content"]})
        return result

    def _call_anthropic(self, tools=None) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return "ERROR: ANTHROPIC_API_KEY not set"
        model = self.model or "claude-sonnet-4-20250514"
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": self._get_system_prompt(),
            "messages": self._get_api_messages(),
        }
        if tools:
            payload["tools"] = tools
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                # Extract text from content blocks
                texts = []
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        texts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        # Serialize tool_use as JSON fence for ToolExecutor
                        texts.append(
                            f"```json\n{json.dumps({'tool': block['name'], 'params': block.get('input', {})})}\n```"
                        )
                return "\n".join(texts)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            return f"ERROR: Anthropic API call failed: {e}"

    def _call_ollama(self) -> str:
        model = self.model or "llama3"
        messages = [{"role": "system", "content": self._get_system_prompt()}]
        messages.extend(self._get_api_messages())
        payload = {"model": model, "messages": messages, "stream": False}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("message", {}).get("content", "")
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            return f"ERROR: Ollama call failed: {e}"

    def _call_openai(self) -> str:
        api_key = os.environ.get("CONSULT_OPENAI_API_KEY")
        if not api_key:
            return "ERROR: CONSULT_OPENAI_API_KEY not set"
        model = self.model or "gpt-4o"
        messages = [{"role": "system", "content": self._get_system_prompt()}]
        messages.extend(self._get_api_messages())
        payload = {"model": model, "max_tokens": 4096, "messages": messages}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{OPENAI_API_BASE}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return "ERROR: Empty response from OpenAI API"
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            return f"ERROR: OpenAI call failed: {e}"

    def _call_claude(self, tools=None) -> str:
        exe = shutil.which("claude") or shutil.which("claude.cmd")
        if not exe:
            return "ERROR: Claude CLI not found in PATH"
        # Concatenate conversation into single prompt
        parts = [self._get_system_prompt(), "---"]
        for m in self._get_api_messages():
            role = m["role"].upper()
            content = m["content"]
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if c.get("type") == "text")
            parts.append(f"[{role}]: {content}")
        full_prompt = "\n\n".join(parts)
        cmd = [exe, "-p", "-", "--output-format", "text"]
        if self.model:
            cmd.extend(["--model", self.model])
        if tools:
            tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]
            if tool_names:
                cmd.extend(["--allowedTools", ",".join(tool_names)])
        try:
            result = subprocess.run(
                cmd, input=full_prompt, capture_output=True,
                text=True, timeout=120,
            )
            return result.stdout.strip() if result.returncode == 0 else (
                f"ERROR: Claude CLI returned {result.returncode}: {result.stderr[:500]}")
        except subprocess.TimeoutExpired:
            return "ERROR: Claude CLI timed out"
        except FileNotFoundError:
            return "ERROR: Claude CLI not found"

    def _call_session(self) -> str:
        """Reserved for S12d (LLM proxy through Gateway). In v1, agents call
        LLMs directly via their actual backend. Use agent_runner.py instead."""
        return ("ERROR: Session backend requires LLM proxy (S12d, not yet implemented). "
                "Use agent_runner.py with a direct backend instead.")


# ---------------------------------------------------------------------------
# ToolExecutor
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Parse and execute tool calls from LLM responses.

    Parsing adapts per backend:
    - anthropic: native tool_use serialized as JSON fences by BackendAdapter
    - ollama/openai/claude: JSON blocks in markdown fences
    - session: JSON from bridge
    """

    def __init__(self, backend: str):
        self.backend = backend
        self._tools: dict[str, callable] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, name: str, func: callable, description: str = ""):
        """Register a tool function."""
        self._tools[name] = func
        if description:
            self._descriptions[name] = description

    def parse(self, response: str) -> list[ToolCall]:
        """Extract tool calls from LLM response.
        Returns empty list if no tool calls found."""
        return self._parse_json_fence(response)

    def _parse_json_fence(self, response: str) -> list[ToolCall]:
        """Parse JSON blocks from markdown fences.

        Expected format:
        ```json
        {"tool": "click", "params": {"x": 320, "y": 240}}
        ```
        """
        calls = []
        pattern = r'```json\s*\n(.*?)\n\s*```'
        for match in re.finditer(pattern, response, re.DOTALL):
            try:
                data = json.loads(match.group(1).strip())
                if "tool" in data:
                    calls.append(ToolCall(
                        name=data["tool"],
                        params=data.get("params", {}),
                        raw=match.group(0),
                    ))
            except json.JSONDecodeError:
                continue
        return calls

    def execute(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call."""
        func = self._tools.get(call.name)
        if func is None:
            return ToolResult(
                tool=call.name, params=call.params,
                result=f"Unknown tool: {call.name}", success=False)
        try:
            result = func(**call.params)
            if isinstance(result, tuple) and len(result) == 2:
                text, img = result
                return ToolResult(
                    tool=call.name, params=call.params,
                    result=str(text), success=True, image_path=img)
            return ToolResult(
                tool=call.name, params=call.params,
                result=str(result), success=True)
        except Exception as e:
            return ToolResult(
                tool=call.name, params=call.params,
                result=f"Error: {e}", success=False)

    def get_tool_descriptions(self) -> str:
        """Generate tool list for the system prompt."""
        lines = []
        for name in sorted(self._tools):
            desc = self._descriptions.get(name, "")
            lines.append(f"  - {name}: {desc}" if desc else f"  - {name}")
        return "\n".join(lines)

    def get_registered_names(self) -> list[str]:
        return sorted(self._tools.keys())


# ---------------------------------------------------------------------------
# ReportBuilder
# ---------------------------------------------------------------------------

class ReportBuilder:
    """Generate structured report from AgentState."""

    @staticmethod
    def build(state: AgentStateBase) -> dict:
        report = {
            "session_id": state.session_id,
            "agent_type": state.agent_type,
            "started_at": state.started_at,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "mode": state.mode,
            "phase": state.phase,
            "stop_reason": state.stop_reason,
            "elapsed_seconds": round(state.elapsed_seconds, 1),
            "total_steps": state.total_steps,
            "total_llm_calls": state.total_llm_calls,
            "stuck_count": state.stuck_count,
            "findings": [
                {"step": f.step, "description": f.description,
                 "evidence": f.evidence, "severity": f.severity}
                if isinstance(f, Finding) else f
                for f in state.findings
            ],
            "errors": state.errors,
            "actions_count": len(state.actions_log),
        }
        if state.total_llm_calls:
            report["costStatus"] = "unknown"
        return report

    @staticmethod
    def write(report: dict, output_path: str) -> str:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return str(p)


# ---------------------------------------------------------------------------
# BaseAgent (abstract)
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """Abstract base for all LLM-based agents.

    Concrete agents must implement:
    - _create_state() -> AgentStateBase subclass
    - _build_system_prompt(**context) -> str
    - _register_tools() -> None
    - _on_start(**context) -> str (initial message to LLM)

    Optional overrides:
    - _on_tool_result(result) -> str|None
    - _is_done(response) -> bool
    - _on_finish() -> None
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "autonomous"):
        self.backend_adapter = BackendAdapter(backend, model)
        self.tools = ToolExecutor(backend)
        self.mode = mode
        self.state: AgentStateBase = self._create_state()
        self._start_time: float = 0.0
        self._max_parse_retries: int = 2
        self._parse_retry_count: int = 0
        self._last_action_result: str = ""  # for stuck detection
        self._state_save_path: str | None = None
        self.last_governance_denial: dict | None = None
        self._runtime_authorized = False
        self._runtime_project_root: Path | None = None

    @abstractmethod
    def _create_state(self) -> AgentStateBase:
        """Create the agent-specific state object."""

    @abstractmethod
    def _build_system_prompt(self, **context) -> str:
        """Build the system prompt with context."""

    @abstractmethod
    def _register_tools(self):
        """Register all tools via self.tools.register(name, func, desc)."""

    @abstractmethod
    def _on_start(self, **context) -> str:
        """Return the initial user message to send to LLM."""

    def _on_tool_result(self, result: ToolResult) -> str | None:
        """Called after each tool execution. Return custom feedback message
        or None for default."""
        return None

    def _is_done(self, response: str) -> bool:
        """Check if the agent signaled completion."""
        return False

    def _on_finish(self):
        """Called before report generation. Override for cleanup."""
        pass

    @staticmethod
    def _base_behavioral_rules() -> str:
        """Shared behavioral rules for ALL agents (Layer 0 of system prompt).

        These are inviolable constraints that every agent includes in its
        system prompt. They enforce semantic fidelity, honesty, and
        disciplined decision-making across the entire agent system.

        Concrete agents call this in _build_system_prompt() and prepend
        the result to their agent-specific prompt.
        """
        return (
            "INVIOLABLE RULES (apply to all agents):\n"
            "1. Never invent facts. If unsure, verify or say "
            "\"I don't know, I need to check\".\n"
            "2. Never summarize content between agents. "
            "Pass verbatim. Semantic fidelity is non-negotiable.\n"
            "3. Never take the easy/patchwork path. "
            "Propose real alternatives with honest trade-offs.\n"
            "4. Always present multiple options with pro/contra "
            "for significant decisions. Recommend, but let the "
            "user/Concierge choose.\n"
            "5. Record user choices in state and devlog. "
            "Decisions must be traceable.\n"
            "6. If your training data may be outdated for a claim, "
            "verify online before asserting.\n"
        )

    # --- Verbatim verification ---

    @staticmethod
    def _hash_content(content: str) -> str:
        """Compute SHA-256 hash of content for verbatim verification."""
        import hashlib
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _verify_verbatim(original: str, received: str) -> bool:
        """Verify that content passed between agents was not altered.

        Returns True if the received content matches the original.
        Used to enforce behavioral rule 2 (semantic fidelity).
        """
        return BaseAgent._hash_content(original) == BaseAgent._hash_content(received)

    @staticmethod
    def _tag_verbatim(content: str) -> dict:
        """Tag content with a hash for verbatim verification.

        Returns dict with content and hash. The receiving agent can
        verify with _verify_verbatim(content, received_content).
        """
        return {
            "content": content,
            "_verbatim_hash": BaseAgent._hash_content(content),
        }

    # --- Consultation Protocol ---

    @staticmethod
    def _project_control_plane_read_path(project_root: str | Path, filename: str) -> Path:
        """Return canonical control-plane file path with legacy fallback."""
        root = Path(project_root).resolve()
        canonical = root / ".controlcoding" / filename
        legacy = root / ".claude" / filename
        if canonical.exists():
            return canonical
        if legacy.exists():
            return legacy
        return canonical

    def _check_specialist_runtime_gate(
        self,
        role_ref: str,
        component: str = "consultant",
        requested_backend: str = "",
        requested_model: str = "",
    ) -> dict:
        """Resolve the runtime specialist gate for a routed consultation path."""
        try:
            from control_plane_utils import check_specialist_runtime
        except ImportError:
            return {
                "allowed": False,
                "enforced": True,
                "reason": "control_plane_utils_unavailable",
                "detail": (
                    "refusing specialist runtime path because "
                    "control_plane_utils is not importable"
                ),
                "component": component,
                "role_ref": role_ref,
                "backend": requested_backend,
                "model": requested_model,
            }

        project_root = (
            self._runtime_project_root
            or getattr(self.state, "project_root", None)
            or "."
        )
        config_path = self._project_control_plane_read_path(project_root, "cc_engagement.json")
        gateway_path = self._project_control_plane_read_path(project_root, "gateway_config.json")
        event_log_path = self._project_control_plane_read_path(project_root, "event_log.jsonl")
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
        except Exception:
            return {
                "allowed": False,
                "enforced": True,
                "reason": "governance_check_failed",
                "detail": (
                    "refusing specialist runtime path because the "
                    "specialist runtime governance check failed"
                ),
                "component": component,
                "role_ref": role_ref,
                "backend": requested_backend,
                "model": requested_model,
            }

    def _format_governance_denial(self, gate: dict) -> str:
        """Record and render a stable specialist runtime denial."""
        reason = str(gate.get("reason") or "governance_denied").strip()
        detail = str(
            gate.get("detail")
            or "refusing specialist runtime path"
        ).strip()
        denial = {
            "allowed": False,
            "enforced": gate.get("enforced", True),
            "reason": reason,
            "detail": detail,
            "component": gate.get("component"),
            "role_ref": gate.get("role_ref"),
            "backend": gate.get("backend"),
            "model": gate.get("model"),
        }
        self.last_governance_denial = denial
        if hasattr(self.state, "log_error"):
            self.state.log_error(
                f"Specialist runtime gated: {reason}: {detail}"
            )
        return f"[GATED] {reason}: {detail}"

    def consult(self, situation: str, question: str,
                criticality: str = "normal", **kwargs) -> "ConsultationResult":
        """Structured consultation via the Consultation Protocol.

        The agent describes the situation and question.
        The protocol determines who to ask, in what format, how many.
        Returns a ConsultationResult with answer, confidence, and audit trail.

        Kwargs:
            domain: str - domain specialization for ExpertAgent
            context: str - additional context for the consultation
            constraints: list[str] - constraints for the consultation
            artifacts: dict[str, str] - name -> content artifacts
            format_override: type - override the auto-selected format
            target_agent: str - bypass routing table and use this agent
            instructions: str - additional instructions appended to prompt
        """
        from consultation_protocol import (
            route as cp_route,
            ConsultationResult,
            AntiLoopTracker,
            parse_verdict_points,
            check_convergence,
            derive_confidence,
            cap_audit_trail,
            validate_target_agent,
            Brief, QuickQuestion, DesignReview,
            VERDICT_INSTRUCTION,
        )

        # Lazy-init anti-loop tracker per agent instance
        if not hasattr(self, "_anti_loop"):
            self._anti_loop = AntiLoopTracker()

        # Check anti-loop
        allowed, deny_reason = self._anti_loop.check_allowed(question)
        if not allowed:
            cached = self._anti_loop.get_cached(question)
            if cached is not None:
                return cached
            return ConsultationResult(
                answer=f"[consultation blocked: {deny_reason}]",
                confidence="low",
                agreement_mode="first_answer",
                agreement_reached=False,
                budget_exceeded=True,
            )

        # Validate target_agent if specified
        target_agent = kwargs.get("target_agent")
        if target_agent is not None:
            valid, err_msg = validate_target_agent(target_agent)
            if not valid:
                return ConsultationResult(
                    answer=f"[invalid target_agent: {err_msg}]",
                    confidence="low",
                    agreement_mode="first_answer",
                    agreement_reached=False,
                )

        # Get routing plan
        domain = kwargs.get("domain", "")
        plan = cp_route(situation, criticality, domain=domain)

        # Override agents if target_agent specified (bypass routing table)
        if target_agent is not None:
            plan.agents = [target_agent]

        # Allow format override
        fmt_cls = kwargs.get("format_override", plan.format)

        # Build the message
        ctx = kwargs.get("context", "")
        constraints = kwargs.get("constraints", [])
        artifacts = kwargs.get("artifacts", {})

        if fmt_cls == QuickQuestion:
            msg = QuickQuestion(question=question, context=ctx)
        elif fmt_cls == DesignReview:
            msg = DesignReview(
                document=ctx or question,
                focus_areas=kwargs.get("focus_areas", []),
                constraints=constraints,
                questions=[question] if ctx else [],
            )
        else:
            msg = Brief(
                context=f"{question}\n\n{ctx}" if ctx else question,
                constraints=constraints,
                questions=[question],
                artifacts=artifacts,
            )

        prompt_text = msg.to_prompt()

        # Append additional instructions if provided
        instructions = kwargs.get("instructions")
        if instructions:
            prompt_text += f"\n\n## Additional Instructions\n{instructions}"

        prompt_text += VERDICT_INSTRUCTION

        # Execute consultation via Path A (agent-to-agent simulation)
        # In practice, agents call other agents or use mcp transport.
        # Here we use whatever backend is available.
        responses = []
        calls_used = 0
        audit_trail = []
        start_time = time.monotonic()

        self._anti_loop.enter_depth()
        try:
            for agent_name in plan.agents:
                if calls_used >= plan.max_calls:
                    break
                if (time.monotonic() - start_time) > plan.timeout_seconds:
                    break

                # Try Path A: call agent via concierge mechanics
                result_text = self._execute_consultation_call(
                    agent_name, prompt_text, plan, **kwargs)
                calls_used += 1

                verdict_points = parse_verdict_points(result_text)
                responses.append({
                    "agent": agent_name,
                    "response": result_text[:2000],
                    "path": "A",
                    "verdict_points": verdict_points,
                })
                audit_trail.append({
                    "agent": agent_name,
                    "path": "A",
                    "chars": len(result_text),
                    "verdict_points_count": len(verdict_points),
                })

            # Path B: external (if plan requires it)
            if plan.external and calls_used < plan.max_calls:
                ext_text = self._execute_external_call(
                    prompt_text, plan, **kwargs)
                calls_used += 1
                ext_verdicts = parse_verdict_points(ext_text)
                responses.append({
                    "agent": "external",
                    "response": ext_text[:2000],
                    "path": "B",
                    "verdict_points": ext_verdicts,
                })
                audit_trail.append({
                    "agent": "external",
                    "path": "B",
                    "chars": len(ext_text),
                    "verdict_points_count": len(ext_verdicts),
                })
        finally:
            self._anti_loop.exit_depth()

        # Check convergence
        converged, disagreements = check_convergence(
            responses, plan.convergence)

        # Derive confidence
        budget_exceeded = calls_used >= plan.max_calls
        confidence = derive_confidence(
            converged, len(responses), budget_exceeded, criticality)

        # Build answer from first response (or merged)
        if responses:
            answer = responses[0]["response"]
        else:
            answer = "[no consultation responses received]"

        result = ConsultationResult(
            answer=answer,
            confidence=confidence,
            sources=[{
                "agent": r["agent"],
                "path": r["path"],
                "response": r["response"][:500],
            } for r in responses],
            agreement_mode=plan.convergence,
            agreement_reached=converged,
            disagreements=disagreements,
            format_used=fmt_cls.__name__,
            rounds=1,
            calls_used=calls_used,
            budget_exceeded=budget_exceeded,
            escalated=not converged and criticality in ("high", "critical"),
            audit_trail=cap_audit_trail(audit_trail),
        )

        self._anti_loop.record(question, result,
                               used_tandem=plan.use_tandem)
        return result

    def _execute_consultation_call(
        self, agent_name: str, prompt: str,
        plan, **kwargs,
    ) -> str:
        """Execute a consultation call to a named agent (Path A).

        Tries concierge mechanics first, falls back to degraded mode.
        """
        role = self._agent_name_to_role(agent_name)
        backend = str(
            kwargs.get("backend") or self.backend_adapter.backend or ""
        ).strip().lower()
        gate = self._check_specialist_runtime_gate(
            role_ref=role,
            component="consultant",
            requested_backend=backend,
        )
        if not gate.get("allowed", True):
            return self._format_governance_denial(gate)
        resolved_backend = gate.get("backend") or backend
        resolved_model = gate.get("model") or self.backend_adapter.model

        # Try using concierge AgentCaller if available
        try:
            from concierge import AgentCaller
            project_root = getattr(self.state, "project_root", ".")
            caller = AgentCaller(project_root)
            # Map agent name to mcp role for transport
            return caller.call_consult(
                role=role, prompt=prompt, backend=resolved_backend)
        except (ImportError, Exception):
            pass

        # Fallback: use own backend adapter for a one-shot call
        try:
            from consultation_protocol import VERDICT_INSTRUCTION
            adapter_copy = BackendAdapter(
                resolved_backend,
                resolved_model,
            )
            adapter_copy.set_system_prompt(
                f"You are {agent_name}. Provide expert consultation."
            )
            adapter_copy.add_user_message(prompt)
            return adapter_copy.call()
        except Exception as e:
            return (f"[FALLBACK] {agent_name} unavailable: {e}. "
                    f"Proceeding without consultation.")

    def _execute_external_call(
        self, prompt: str, plan, **kwargs,
    ) -> str:
        """Execute an external consultation call (Path B).

        Uses a different backend than the current session.
        """
        ext_backend = str(
            kwargs.get("backend") or self.backend_adapter.backend or ""
        ).strip().lower()

        try:
            gate = self._check_specialist_runtime_gate(
                role_ref="consultant",
                component="consultant",
                requested_backend=ext_backend,
            )
            if not gate.get("allowed", True):
                return self._format_governance_denial(gate)
            adapter = BackendAdapter(
                gate.get("backend") or ext_backend,
                gate.get("model") or "",
            )
            adapter.set_system_prompt(
                "You are an external consultant with NO project context. "
                "Analyze purely from the data provided."
            )
            adapter.add_user_message(prompt)
            return adapter.call()
        except Exception as e:
            return (f"[FALLBACK] External consultation ({ext_backend}) "
                    f"failed: {e}")

    @staticmethod
    def _agent_name_to_role(agent_name: str) -> str:
        """Map agent class name to mcp_consultant role for transport."""
        mapping = {
            "ArchitectAgent": "architect",
            "ReviewerAgent": "reviewer",
            "DebuggerAgent": "debug",
            "SocraticAgent": "socratic",
            "ExpertAgent": "scientist",
            "ConciergeAgent": "planner",
            "CoderAgent": "reviewer",
        }
        # Handle domain suffix like "ExpertAgent:security"
        base = agent_name.split(":")[0]
        return mapping.get(base, "architect")

    @staticmethod
    def _pick_consultation_backend() -> str:
        """Return only an explicitly configured consultation backend."""
        import os as _os
        return _os.environ.get("CONSULT_BACKEND", "").strip().lower()

    # --- Public API ---

    def run(self, state_save_path: str | None = None, **context) -> dict:
        """Single-call: start, loop until done, return report.
        If state_save_path is given, state is auto-saved periodically
        and on finish (for crash recovery)."""
        self._state_save_path = state_save_path
        self.start(**context)
        self._agent_loop()
        return self.finish()

    def start(self, **context):
        """Begin an agent session."""
        self.state.session_id = str(uuid.uuid4())[:8]
        self.state.started_at = datetime.now().isoformat(timespec="seconds")
        self.state.mode = self.mode
        self.state.phase = "active"
        self._start_time = time.monotonic()
        explicit_project_root = context.get("project_root")
        if explicit_project_root:
            self._runtime_project_root = Path(explicit_project_root).resolve()

        self._register_tools()

        prompt = self._build_system_prompt(**context)
        self.backend_adapter.set_system_prompt(prompt)

        initial_msg = self._on_start(**context)
        self.backend_adapter.add_user_message(initial_msg)

        gate = self._check_specialist_runtime_gate(
            role_ref=self.state.agent_type or self.__class__.__name__,
            component="concierge" if self.__class__.__name__ == "ConciergeAgent" else "consultant",
            requested_backend=self.backend_adapter.backend,
            requested_model=self.backend_adapter.model,
        )
        if not gate.get("allowed", False):
            return self._format_governance_denial(gate)
        self._runtime_authorized = True

        response = self.backend_adapter.call()
        self.state.total_llm_calls += 1
        return response

    def step(self, user_input: str | None = None) -> str:
        """One turn of the agent loop. For interactive mode."""
        if not self._runtime_authorized:
            return "[GATED] Agent runtime has not been authorized by an explicit backend and consent gate."
        if user_input:
            self.backend_adapter.add_user_message(user_input)
            response = self.backend_adapter.call()
            self.state.total_llm_calls += 1
            return response
        return self._process_response()

    def finish(self) -> dict:
        """End session and generate report."""
        if self._start_time > 0:
            self.state.elapsed_seconds = time.monotonic() - self._start_time
        if not self.state.stop_reason:
            self.state.stop_reason = "done"
        self.state.phase = "done"
        self._on_finish()
        report = ReportBuilder.build(self.state)
        if self._state_save_path:
            self.state.save(self._state_save_path)
        return report

    # --- Internal ---

    def _agent_loop(self):
        """Core loop: process response -> execute tools -> send results -> repeat.

        Safety: timeout, max steps, stuck detection, LLM error handling.
        On any stop condition, state.stop_reason is set and a partial report
        can be generated.
        """
        while True:
            # --- Safety checks ---
            if self._check_timeout():
                self.state.stop_reason = "timeout"
                self.state.log_error("Session timeout reached")
                break
            if self.state.total_steps >= self.state.max_steps:
                self.state.stop_reason = "max_steps"
                self.state.log_error("Max steps reached")
                break
            if self.state.stuck_count >= self.state.stuck_threshold:
                self.state.stop_reason = "stuck"
                self.state.log_error(
                    f"Stuck: {self.state.stuck_count} consecutive no-change results")
                break

            response = self._get_last_assistant_response()
            if not response:
                self.state.stop_reason = "no_response"
                break

            # --- LLM error detection ---
            if response.startswith("ERROR:"):
                self.state.log_error(f"LLM error: {response[:200]}")
                # Retry once on LLM error
                self._parse_retry_count += 1
                if self._parse_retry_count > self._max_parse_retries:
                    self.state.stop_reason = "error"
                    self.state.log_error("LLM errors exceeded retry limit")
                    break
                time.sleep(1)  # brief pause before retry
                response = self.backend_adapter.call()
                self.state.total_llm_calls += 1
                continue

            # --- Done check ---
            if self._is_done(response):
                self.state.stop_reason = "done"
                break

            # --- Parse tool calls ---
            calls = self.tools.parse(response)

            if not calls:
                if self.mode == "autonomous":
                    self._parse_retry_count += 1
                    if self._parse_retry_count > self._max_parse_retries:
                        self.state.stop_reason = "no_tool_calls"
                        self.state.log_error(
                            "No tool calls after retries, stopping")
                        break
                    self.backend_adapter.add_user_message(
                        "Continue with the next action. "
                        "Use a tool by writing a JSON block like:\n"
                        '```json\n{"tool": "tool_name", "params": {...}}\n```')
                    response = self.backend_adapter.call()
                    self.state.total_llm_calls += 1
                    continue
                else:
                    break  # interactive: wait for user

            self._parse_retry_count = 0

            # --- Execute tools ---
            for call in calls:
                self.state.next_step()
                result = self.tools.execute(call)
                self.state.log_action(
                    tool=call.name, params=call.params,
                    result=result.result, success=result.success)

                # Stuck detection: track if results are repeating
                self._check_stuck(result)

                custom_msg = self._on_tool_result(result)
                feedback = custom_msg or f"Tool '{call.name}': {result.result}"

                if result.image_path:
                    self.backend_adapter.add_user_message(
                        feedback, image_path=result.image_path)
                else:
                    self.backend_adapter.add_user_message(feedback)

            # --- Auto-save state periodically ---
            if self._state_save_path and self.state.current_step % 10 == 0:
                self.state.save(self._state_save_path)

            self._check_context_budget()

            response = self.backend_adapter.call()
            self.state.total_llm_calls += 1

        # Update elapsed time on exit
        if self._start_time > 0:
            self.state.elapsed_seconds = time.monotonic() - self._start_time

    def _process_response(self) -> str:
        """Process last response for tool calls. Returns next response."""
        response = self._get_last_assistant_response()
        if not response:
            return ""

        calls = self.tools.parse(response)
        if not calls:
            return response

        for call in calls:
            self.state.next_step()
            result = self.tools.execute(call)
            self.state.log_action(
                tool=call.name, params=call.params,
                result=result.result, success=result.success)

            custom_msg = self._on_tool_result(result)
            feedback = custom_msg or f"Tool '{call.name}': {result.result}"
            if result.image_path:
                self.backend_adapter.add_user_message(
                    feedback, image_path=result.image_path)
            else:
                self.backend_adapter.add_user_message(feedback)

        self._check_context_budget()
        response = self.backend_adapter.call()
        self.state.total_llm_calls += 1
        return response

    def _check_stuck(self, result: ToolResult):
        """Track consecutive identical or failed results for stuck detection.
        Resets on any new/different result."""
        current = f"{result.tool}:{result.result[:100]}"
        if current == self._last_action_result or not result.success:
            self.state.stuck_count += 1
        else:
            self.state.stuck_count = 0
        self._last_action_result = current

    def _get_last_assistant_response(self) -> str:
        for msg in reversed(self.backend_adapter.messages):
            if msg["role"] == "assistant":
                return msg.get("content", "")
        return ""

    def _check_timeout(self) -> bool:
        if self._start_time <= 0:
            return False
        return (time.monotonic() - self._start_time) > self.state.timeout_seconds

    def _check_context_budget(self):
        estimated = self.backend_adapter.estimate_tokens()
        limit = 200000 if self.backend_adapter.backend in (
            "anthropic", "claude") else 128000
        if estimated / limit > 0.8:
            self.backend_adapter.summarize_and_rebuild(self.state)
