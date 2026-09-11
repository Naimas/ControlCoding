#!/usr/bin/env python3
"""mcp_agent_memory.py - Per-agent task memory MCP server for ControlCoding.

Implements task-scoped persistent memory for named agents across chat
sessions. The canonical layout lives under `.controlcoding/agents/`,
while a compatibility mirror is also written under
`.controlcoding/sessions/agents/` for older flows.

Tools:
    agent_start       - Initialize or resume a persistent agent session
    agent_checkpoint  - Write mid-session checkpoint for an agent task
    agent_close       - Close agent chat via the handoff ceremony
    agent_resume      - Return full resume brief for an agent
    agent_status      - Show status of one or all persistent agents

Setup in .controlcoding/settings.json:
    {
      "mcpServers": {
        "agent-memory": {
          "command": "python",
          "args": ["tools/mcp_agent_memory.py"]
        }
      }
    }
Requirements:
    pip install fastmcp
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

# --- Configuration ---

PROJECT_ROOT = Path(os.environ.get("SESSION_PROJECT_ROOT", ".")).resolve()
CONTROL_PLANE_DIR = ".controlcoding"
LEGACY_CONTROL_PLANE_DIR = ".claude"


def _control_plane_dir() -> Path:
    canonical = PROJECT_ROOT / CONTROL_PLANE_DIR
    if canonical.exists():
        return canonical
    legacy = PROJECT_ROOT / LEGACY_CONTROL_PLANE_DIR
    if legacy.exists():
        return legacy
    return canonical


AGENT_MEMORY_SCHEMA_VERSION = 2
AGENTS_DIR = _control_plane_dir() / "agents"
LEGACY_AGENTS_DIR = _control_plane_dir() / "sessions" / "agents"

PERSISTENT_AGENTS = frozenset({
    "concierge", "architect", "coder", "reviewer", "debugger"
})
STATELESS_AGENTS = frozenset({
    "expert", "socratic"
})

_TYPE_PREFIX = {
    "DECISION": "DEC",
    "CONSTRAINT": "CON",
    "PLAN": "PLAN",
    "FINDING": "FIND",
    "QUESTION": "Q",
}
_VALID_TYPES = set(_TYPE_PREFIX.keys())

# --- MCP Server ---

mcp = FastMCP(
    "agent-memory",
    instructions=(
        "Per-agent memory tools for ControlCoding. "
        "For agentic sessions: call agent_start() at the beginning to get "
        "the resume brief. Call agent_checkpoint() at milestones to preserve "
        "progress. Call agent_close() at session end or when context is near "
        "its limit. "
        "Stateless agents (expert, socratic) must never call agent_start() - "
        "they have no memory by design so they can give unbiased opinions."
    ),
)


# --- Shared decision utilities (duplicated from mcp_handoff.py) ---


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slugify(text: str, max_words: int = 6) -> str:
    cleaned = []
    for ch in text.lower():
        cleaned.append(ch if ch.isalnum() else " ")
    words = "".join(cleaned).split()
    return "-".join(words[:max_words]) or "task"


def _next_decision_id(dtype: str, entries: list) -> str:
    """Generate the next sequential ID for the given type prefix."""
    prefix = _TYPE_PREFIX.get(dtype, "DEC")
    nums = []
    for e in entries:
        eid = e.get("id", "")
        if eid.startswith(prefix + "-"):
            try:
                nums.append(int(eid.split("-")[1]))
            except (IndexError, ValueError):
                pass
    next_num = (max(nums) + 1) if nums else 1
    return f"{prefix}-{next_num:03d}"


def _active_entries(entries: list) -> list:
    """Return entries that have not been superseded by a later entry."""
    superseded_ids = {e.get("supersedes") for e in entries if e.get("supersedes")}
    return [e for e in entries if e.get("id") not in superseded_ids]


def _fmt_decision_block(entry_list: list) -> str:
    """Format a list of decision entries as verbatim blockquote blocks."""
    parts = []
    for e in entry_list:
        text_lines = e["text"].split("\n")
        quoted = "\n".join(f"> {line}" for line in text_lines)
        parts.append(f"### {e['id']}\n{quoted}")
    return "\n\n".join(parts)


# --- Write isolation ---


def _check_write_isolation(agent_name: str) -> str | None:
    """Enforce SESSION_AGENT_NAME write isolation.

    Returns an error string if the calling session is locked to a different
    agent, None if the write is allowed. Read operations bypass this check.
    """
    env_agent = os.environ.get("SESSION_AGENT_NAME", "").lower().strip()
    if env_agent and env_agent != agent_name.lower().strip():
        return (
            f"ERROR: Write isolation violation. This session is locked to agent "
            f"'{env_agent}' (SESSION_AGENT_NAME), but attempted to write to '{agent_name}'. "
            f"Each agent session may only write to its own memory folder."
        )
    return None


# --- Agent filesystem helpers ---


def _agent_dir(agent_name: str) -> Path:
    return AGENTS_DIR / agent_name


def _legacy_agent_dir(agent_name: str) -> Path:
    return LEGACY_AGENTS_DIR / agent_name


def _agent_read_dir(agent_name: str) -> Path:
    canonical = _agent_dir(agent_name)
    if canonical.exists():
        return canonical
    legacy = _legacy_agent_dir(agent_name)
    if legacy.exists():
        return legacy
    return canonical


def _agent_state_file(agent_name: str) -> Path:
    return _agent_dir(agent_name) / "state.json"


def _legacy_agent_state_file(agent_name: str) -> Path:
    return _legacy_agent_dir(agent_name) / "state.json"


def _agent_decisions_file(agent_name: str) -> Path:
    return _agent_dir(agent_name) / "decisions.jsonl"


def _legacy_agent_decisions_file(agent_name: str) -> Path:
    return _legacy_agent_dir(agent_name) / "decisions.jsonl"


def _agent_index_file(agent_name: str) -> Path:
    return _agent_dir(agent_name) / "index.md"


def _legacy_agent_index_file(agent_name: str) -> Path:
    return _legacy_agent_dir(agent_name) / "index.md"


def _agent_profile_file(agent_name: str) -> Path:
    return _agent_dir(agent_name) / "profile.json"


def _agent_memory_file(agent_name: str) -> Path:
    return _agent_dir(agent_name) / "memory.md"


def _agent_task_index_file(agent_name: str) -> Path:
    return _agent_dir(agent_name) / "task_index.jsonl"


def _task_dir(agent_name: str, task_id: str) -> Path:
    return _agent_dir(agent_name) / "tasks" / task_id


def _task_state_file(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "state.json"


def _task_request_file(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "request.md"


def _task_summary_file(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "summary.md"


def _task_result_file(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "result.md"


def _task_worklog_dir(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "worklog"


def _task_chats_dir(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "chats"


def _task_executions_dir(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "executions"


def _task_handoffs_dir(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "handoffs"


def _task_artifacts_dir(agent_name: str, task_id: str) -> Path:
    return _task_dir(agent_name, task_id) / "artifacts"


def _legacy_chats_dir(agent_name: str) -> Path:
    return _legacy_agent_dir(agent_name) / "chats"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_agent_state(agent_name: str) -> dict | None:
    for state_file in (_agent_state_file(agent_name), _legacy_agent_state_file(agent_name)):
        if not state_file.exists():
            continue
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _write_agent_state(agent_name: str, state: dict) -> None:
    payload = dict(state)
    payload["updated"] = _now_iso()
    _write_json(_agent_state_file(agent_name), payload)
    _write_json(_legacy_agent_state_file(agent_name), payload)


def _next_chat_folder(chat_root: Path) -> str:
    """Generate next chat folder name: chat-NNN-YYYY-MM-DD."""
    chats_dir = chat_root
    if not chats_dir.exists():
        return f"chat-001-{_now_date()}"
    existing = [d.name for d in chats_dir.iterdir() if d.is_dir() and d.name.startswith("chat-")]
    nums = []
    for entry in existing:
        parts = entry.split("-")
        if len(parts) >= 2:
            try:
                nums.append(int(parts[1]))
            except ValueError:
                pass
    next_num = (max(nums) + 1) if nums else 1
    return f"chat-{next_num:03d}-{_now_date()}"


def _next_agent_chat_folder(agent_name: str) -> str:
    nums = []
    legacy_chats = _legacy_chats_dir(agent_name)
    if legacy_chats.exists():
        for entry in legacy_chats.iterdir():
            if not entry.is_dir() or not entry.name.startswith("chat-"):
                continue
            parts = entry.name.split("-")
            if len(parts) >= 2:
                try:
                    nums.append(int(parts[1]))
                except ValueError:
                    pass
    if not nums:
        return f"chat-001-{_now_date()}"
    return f"chat-{max(nums) + 1:03d}-{_now_date()}"


def _load_agent_decisions(agent_name: str) -> list:
    entries = []
    decisions_file = _agent_decisions_file(agent_name)
    if not decisions_file.exists():
        decisions_file = _legacy_agent_decisions_file(agent_name)
    if not decisions_file.exists():
        return []
    for line in decisions_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _append_agent_decision(agent_name: str, entry: dict) -> None:
    _append_jsonl(_agent_decisions_file(agent_name), entry)
    _append_jsonl(_legacy_agent_decisions_file(agent_name), entry)


def _read_task_state(agent_name: str, task_id: str) -> dict | None:
    state_file = _task_state_file(agent_name, task_id)
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_task_state(agent_name: str, task_id: str, state: dict) -> None:
    payload = dict(state)
    payload["updated"] = _now_iso()
    _write_json(_task_state_file(agent_name, task_id), payload)


def _load_task_states(agent_name: str) -> list[dict]:
    tasks_dir = _agent_dir(agent_name) / "tasks"
    rows: list[dict] = []
    if tasks_dir.exists():
        for task_path in sorted(tasks_dir.iterdir()):
            if not task_path.is_dir():
                continue
            payload = _read_task_state(agent_name, task_path.name)
            if payload:
                rows.append(payload)
    rows.sort(key=lambda item: str(item.get("updated", "")), reverse=True)
    return rows


def _next_task_id(agent_name: str, task: str) -> str:
    tasks_dir = _agent_dir(agent_name) / "tasks"
    nums = []
    if tasks_dir.exists():
        for path in tasks_dir.iterdir():
            if not path.is_dir() or not path.name.startswith("task-"):
                continue
            parts = path.name.split("-", 2)
            if len(parts) >= 2:
                try:
                    nums.append(int(parts[1]))
                except ValueError:
                    pass
    next_num = (max(nums) + 1) if nums else 1
    return f"task-{next_num:03d}-{_slugify(task)}"


def _next_named_entry(directory: Path, prefix: str, suffix: str = ".md") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    nums = []
    for path in directory.iterdir():
        if not path.is_file() or not path.name.startswith(prefix + "-"):
            continue
        parts = path.stem.split("-", 2)
        if len(parts) >= 2:
            try:
                nums.append(int(parts[1]))
            except ValueError:
                pass
    next_num = (max(nums) + 1) if nums else 1
    return directory / f"{prefix}-{next_num:03d}-{_stamp()}{suffix}"


def _ensure_agent_layout(agent_name: str) -> None:
    agent_dir = _agent_dir(agent_name)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "tasks").mkdir(exist_ok=True)
    legacy_dir = _legacy_agent_dir(agent_name)
    (legacy_dir / "chats").mkdir(parents=True, exist_ok=True)
    profile = _agent_profile_file(agent_name)
    if not profile.exists():
        _write_json(profile, {
            "schema_version": AGENT_MEMORY_SCHEMA_VERSION,
            "agent": agent_name,
            "persistent": True,
            "created_at": _now_iso(),
            "canonical_root": _agent_dir(agent_name).relative_to(PROJECT_ROOT).as_posix(),
            "legacy_mirror": _legacy_agent_dir(agent_name).relative_to(PROJECT_ROOT).as_posix(),
        })


def _ensure_task_layout(agent_name: str, task_id: str) -> None:
    for directory in (
        _task_worklog_dir(agent_name, task_id),
        _task_chats_dir(agent_name, task_id),
        _task_executions_dir(agent_name, task_id),
        _task_handoffs_dir(agent_name, task_id),
        _task_artifacts_dir(agent_name, task_id),
    ):
        directory.mkdir(parents=True, exist_ok=True)


def _append_task_event(agent_name: str, event_type: str, task_state: dict, detail: str = "") -> None:
    _append_jsonl(_agent_task_index_file(agent_name), {
        "timestamp": _now_iso(),
        "event": event_type,
        "task_id": task_state.get("task_id", ""),
        "request": task_state.get("request", ""),
        "phase": task_state.get("phase", ""),
        "status": task_state.get("status", ""),
        "last_chat": task_state.get("last_chat", ""),
        "detail": detail,
    })


def _append_task_worklog(agent_name: str,
                         task_id: str,
                         *,
                         title: str,
                         body: str,
                         chat_folder: str = "") -> Path:
    path = _next_named_entry(_task_worklog_dir(agent_name, task_id), "worklog")
    lines = [
        f"# Task Worklog: {agent_name} / {task_id}",
        "",
        f"**Date**: {_now_iso()}",
    ]
    if chat_folder:
        lines.append(f"**Chat**: {chat_folder}")
    lines += [
        "",
        f"## {title}",
        body.strip() or "_No details provided._",
        "",
    ]
    _write_text(path, "\n".join(lines))
    return path


def _update_task_summary(agent_name: str, task_id: str, task_state: dict) -> None:
    pending = task_state.get("pending", [])
    questions = task_state.get("open_questions", [])
    lines = [
        f"# Task Summary: {agent_name} / {task_id}",
        "",
        f"**Request**: {task_state.get('request', '')}",
        f"**Phase**: {task_state.get('phase', 'unknown')}",
        f"**Status**: {task_state.get('status', 'unknown')}",
        f"**Last chat**: {task_state.get('last_chat', '-')}",
        "",
        "## Pending",
    ]
    if pending:
        lines.extend(f"- {item}" for item in pending)
    else:
        lines.append("_None._")
    lines += [
        "",
        "## Open questions",
    ]
    if questions:
        lines.extend(f"- {item}" for item in questions)
    else:
        lines.append("_None._")
    context_note = str(task_state.get("context_note", "")).strip()
    if context_note:
        lines += [
            "",
            "## Context",
            context_note,
        ]
    _write_text(_task_summary_file(agent_name, task_id), "\n".join(lines) + "\n")


def _update_agent_memory(agent_name: str) -> None:
    state = _read_agent_state(agent_name)
    if state is None:
        return
    task_states = _load_task_states(agent_name)
    decisions = _active_entries(_load_agent_decisions(agent_name))
    lines = [
        f"# Agent Memory: {agent_name}",
        "",
        f"**Last updated**: {state.get('updated', 'unknown')}",
        "",
        "## Current focus",
        f"- Task: {state.get('current_task', '_Not specified._')}",
        f"- Task ID: {state.get('current_task_id', '-')}",
        f"- Phase: {state.get('phase', 'unknown')}",
        "",
        "## Pending",
    ]
    pending = state.get("pending", [])
    if pending:
        lines.extend(f"- {item}" for item in pending)
    else:
        lines.append("_None._")
    lines += [
        "",
        "## Open questions",
    ]
    questions = state.get("open_questions", [])
    if questions:
        lines.extend(f"- {item}" for item in questions)
    else:
        lines.append("_None._")
    if task_states:
        lines += [
            "",
            "## Recent tasks",
        ]
        for task_state in task_states[:5]:
            lines.append(
                f"- {task_state.get('task_id', '')}: "
                f"{task_state.get('request', '')} "
                f"({task_state.get('phase', 'unknown')})"
            )
    if decisions:
        for dtype in ("DECISION", "CONSTRAINT", "PLAN"):
            entries = [entry for entry in decisions if entry.get("type") == dtype]
            if not entries:
                continue
            lines += [
                "",
                f"## Active {dtype.lower()}s [verbatim]",
                "",
                _fmt_decision_block(entries),
            ]
    _write_text(_agent_memory_file(agent_name), "\n".join(lines) + "\n")


def _update_agent_index(agent_name: str) -> None:
    task_states = _load_task_states(agent_name)
    decisions = _load_agent_decisions(agent_name)
    all_chats = []
    for task_state in task_states:
        task_id = str(task_state.get("task_id", "")).strip()
        if not task_id:
            continue
        chats_dir = _task_chats_dir(agent_name, task_id)
        if not chats_dir.exists():
            continue
        for chat_dir in sorted(chats_dir.iterdir()):
            if not chat_dir.is_dir() or not chat_dir.name.startswith("chat-"):
                continue
            log_file = chat_dir / "log.md"
            if not log_file.exists():
                continue
            log_text = log_file.read_text(encoding="utf-8")
            status = date = ""
            for line in log_text.splitlines():
                if line.startswith("**Status**:"):
                    status = line.replace("**Status**:", "").strip()
                elif line.startswith("**Date**:"):
                    date = line.replace("**Date**:", "").strip()
                if status and date:
                    break
            all_chats.append((chat_dir.name, date, task_state.get("request", ""), status))
    lines = [
        f"# Agent Memory Index: {agent_name}",
        "",
        f"**Last updated**: {_now_date()}",
        f"**Total tasks**: {len(task_states)}",
        "",
        "## Task Index",
        "",
        "| Task ID | Request | Phase | Last chat |",
        "|---------|---------|-------|-----------|",
    ]
    if task_states:
        for task_state in task_states:
            request = str(task_state.get("request", "")).replace("|", "/")
            if len(request) > 60:
                request = request[:60] + "..."
            lines.append(
                f"| {task_state.get('task_id', '')} | {request} | "
                f"{task_state.get('phase', 'unknown')} | {task_state.get('last_chat', '-')} |"
            )
    else:
        lines.append("| - | _no tasks_ | - | - |")

    lines += [
        "",
        "## Decision Index",
        "",
        "| ID | Type | Task | Summary |",
        "|----|------|------|---------|",
    ]
    if decisions:
        for decision in decisions:
            summary = decision.get("text", "").replace("|", "/")
            if len(summary) > 60:
                summary = summary[:60] + "..."
            lines.append(
                f"| {decision.get('id', '')} | {decision.get('type', '')} | "
                f"{decision.get('task_id', '-')} | {summary} |"
            )
    else:
        lines.append("| - | - | - | _no decisions_ |")

    lines += [
        "",
        "## Chat Timeline",
        "",
        "| Chat | Date | Task | Status |",
        "|------|------|------|--------|",
    ]
    if all_chats:
        for chat_name, date, task, status in all_chats:
            task_short = str(task).replace("|", "/")
            if len(task_short) > 50:
                task_short = task_short[:50] + "..."
            lines.append(f"| {chat_name} | {date} | {task_short} | {status} |")
    else:
        lines.append("| - | - | _no chats_ | - |")

    content = "\n".join(lines) + "\n"
    _write_text(_agent_index_file(agent_name), content)
    _write_text(_legacy_agent_index_file(agent_name), content)


def _current_chat_log_path(agent_name: str, state: dict) -> Path | None:
    last_chat = str(state.get("last_chat", "")).strip()
    if not last_chat:
        return None
    task_id = str(state.get("current_task_id", "")).strip()
    if task_id:
        canonical = _task_chats_dir(agent_name, task_id) / last_chat / "log.md"
        if canonical.exists():
            return canonical
    legacy = _legacy_chats_dir(agent_name) / last_chat / "log.md"
    if legacy.exists():
        return legacy
    return None


def _build_agent_resume_brief(agent_name: str) -> str:
    """Build a resume brief from state + last log + active decisions."""
    state = _read_agent_state(agent_name)
    if state is None:
        return f"No prior memory found for agent '{agent_name}'."

    parts = [
        f"# Resume Brief: {agent_name}",
        f"**Date**: {_now_date()}",
        f"**Last updated**: {state.get('updated', 'unknown')}",
        "",
        "---",
        "",
        "## Current task",
        state.get("current_task", "_Not specified._"),
        "",
        f"**Task ID**: {state.get('current_task_id', '-')}",
        f"**Phase**: {state.get('phase', 'unknown')}",
        "",
    ]

    pending = state.get("pending", [])
    if pending:
        parts += ["## Pending", ""]
        parts += [f"- {item}" for item in pending]
        parts.append("")

    oq = state.get("open_questions", [])
    if oq:
        parts += ["## Open questions", ""]
        parts += [f"- {q}" for q in oq]
        parts.append("")

    ctx = state.get("context_note", "")
    if ctx:
        parts += ["## Context", ctx, ""]

    parts += ["---", ""]

    last_chat = state.get("last_chat", "")
    log_path = _current_chat_log_path(agent_name, state)
    if last_chat and log_path is not None and log_path.exists():
        log_content = log_path.read_text(encoding="utf-8")
        parts += [f"## Last chat: {last_chat}", "", log_content, "", "---", ""]

    entries = _load_agent_decisions(agent_name)
    active = _active_entries(entries)
    decisions = [e for e in active if e["type"] == "DECISION"]
    constraints = [e for e in active if e["type"] == "CONSTRAINT"]
    plans = [e for e in active if e["type"] == "PLAN"]

    if decisions:
        parts += ["## Active decisions [verbatim]", "", _fmt_decision_block(decisions), "", "---", ""]
    if constraints:
        parts += ["## Active constraints [verbatim]", "", _fmt_decision_block(constraints), "", "---", ""]
    if plans:
        parts += ["## Active plans [verbatim]", "", _fmt_decision_block(plans), "", "---", ""]

    if _agent_memory_file(agent_name).exists():
        parts += [
            "## Agent memory",
            f"Structured memory file: `{_agent_memory_file(agent_name).relative_to(PROJECT_ROOT).as_posix()}`",
            "",
        ]

    if _agent_index_file(agent_name).exists():
        index_display_path = _agent_index_file(agent_name).relative_to(PROJECT_ROOT).as_posix()
        parts += [
            "## Memory index",
            f"Task and decision index: `{index_display_path}`",
            "",
        ]

    return "\n".join(parts)


def _task_request_markdown(agent_name: str, task_id: str, task: str) -> str:
    return "\n".join([
        f"# Agent Task Request: {agent_name} / {task_id}",
        "",
        f"**Created**: {_now_iso()}",
        f"**Agent**: {agent_name}",
        "",
        "## Request",
        task,
        "",
    ])


# --- Tools ---


@mcp.tool
def agent_start(agent_name: str, task: str) -> str:
    """Initialize or resume a persistent agent session."""
    name = agent_name.lower().strip()
    isolation_err = _check_write_isolation(name)
    if isolation_err:
        return isolation_err
    if name in STATELESS_AGENTS:
        return (
            f"ERROR: '{name}' is a stateless agent and must not have memory. "
            f"Stateless agents (expert, socratic) give unbiased opinions - "
            f"they must not remember prior sessions."
        )
    if name not in PERSISTENT_AGENTS:
        return (
            f"ERROR: Unknown agent '{name}'. "
            f"Persistent agents: {sorted(PERSISTENT_AGENTS)}. "
            f"Stateless agents (no memory): {sorted(STATELESS_AGENTS)}."
        )

    normalized_task = task.strip()
    if not normalized_task:
        return "ERROR: task must not be empty."

    _ensure_agent_layout(name)
    prior_state = _read_agent_state(name)
    has_history = prior_state is not None

    reuse_current = (
        prior_state is not None
        and str(prior_state.get("phase", "")).strip() != "complete"
        and str(prior_state.get("current_task_id", "")).strip()
    )
    task_id = (
        str(prior_state.get("current_task_id", "")).strip()
        if reuse_current
        else _next_task_id(name, normalized_task)
    )

    _ensure_task_layout(name, task_id)
    chat_root = _task_chats_dir(name, task_id)
    chat_folder = _next_agent_chat_folder(name)
    chat_path = chat_root / chat_folder
    chat_path.mkdir(parents=True, exist_ok=True)
    (_legacy_chats_dir(name) / chat_folder).mkdir(parents=True, exist_ok=True)

    if not _task_request_file(name, task_id).exists():
        _write_text(_task_request_file(name, task_id), _task_request_markdown(name, task_id, normalized_task))

    state = prior_state or {}
    if not reuse_current:
        state["pending"] = []
        state["open_questions"] = []
        state["context_note"] = ""
    state["schema_version"] = AGENT_MEMORY_SCHEMA_VERSION
    state["agent"] = name
    state["current_task"] = normalized_task
    state["current_task_id"] = task_id
    state["phase"] = "in_progress"
    state["last_chat"] = chat_folder
    state["last_chat_relpath"] = chat_path.relative_to(PROJECT_ROOT).as_posix()
    _write_agent_state(name, state)

    task_state = _read_task_state(name, task_id) or {
        "schema_version": AGENT_MEMORY_SCHEMA_VERSION,
        "task_id": task_id,
        "agent": name,
        "request": normalized_task,
        "created_at": _now_iso(),
        "phase": "in_progress",
        "status": "active",
        "pending": [],
        "open_questions": [],
        "context_note": "",
    }
    task_state["request"] = normalized_task
    task_state["phase"] = "in_progress"
    task_state["status"] = "active"
    task_state["last_chat"] = chat_folder
    task_state["last_chat_relpath"] = chat_path.relative_to(PROJECT_ROOT).as_posix()
    task_state["pending"] = state.get("pending", [])
    task_state["open_questions"] = state.get("open_questions", [])
    task_state["context_note"] = state.get("context_note", "")
    _write_task_state(name, task_id, task_state)

    _append_task_event(name, "started", task_state, detail="agent_start")
    _update_task_summary(name, task_id, task_state)
    _update_agent_index(name)
    _update_agent_memory(name)

    if has_history:
        brief = _build_agent_resume_brief(name)
        return (
            f"Agent '{name}' resumed.\n"
            f"Task folder: {_task_dir(name, task_id)}\n"
            f"Chat folder: {chat_path}\n\n"
            f"--- RESUME BRIEF ---\n\n{brief}"
        )
    return (
        f"Agent '{name}' initialized (first session).\n"
        f"Task folder: {_task_dir(name, task_id)}\n"
        f"Chat folder: {chat_path}\n"
        f"No prior memory - starting fresh."
    )


@mcp.tool
def agent_checkpoint(
    agent_name: str,
    pending: str = "",
    open_questions: str = "",
    context_note: str = "",
    decision_type: str = "",
    decision_text: str = "",
) -> str:
    """Write a mid-session checkpoint for a persistent agent.

    Updates state.json with current progress. Optionally records one decision
    to the agent's decisions.jsonl.

    Call this at major milestones within a session to ensure progress is
    preserved even if context fills unexpectedly.

    Args:
        agent_name: Name of the persistent agent.
        pending: Newline-separated list of items still to do.
        open_questions: Newline-separated list of unresolved questions.
        context_note: One paragraph of verbatim context to carry forward.
        decision_type: If recording a decision: DECISION, CONSTRAINT, PLAN, FINDING, QUESTION
        decision_text: Verbatim decision text including the 'because'. Required if decision_type set.

    Returns:
        Confirmation of state update and optional decision ID.
    """
    name = agent_name.lower().strip()
    isolation_err = _check_write_isolation(name)
    if isolation_err:
        return isolation_err
    if name not in PERSISTENT_AGENTS:
        return f"ERROR: '{name}' is not a persistent agent. Persistent agents: {sorted(PERSISTENT_AGENTS)}"

    state = _read_agent_state(name)
    if state is None:
        return f"ERROR: Agent '{name}' has no state. Call agent_start() first."

    task_id = str(state.get("current_task_id", "")).strip()
    if not task_id:
        return f"ERROR: Agent '{name}' has no active task id. Call agent_start() first."
    task_state = _read_task_state(name, task_id)
    if task_state is None:
        return f"ERROR: Task state missing for '{name}' / {task_id}."

    results = []
    pending_items = state.get("pending", [])
    question_items = state.get("open_questions", [])

    if pending.strip():
        pending_items = [x.strip() for x in pending.splitlines() if x.strip()]
    if open_questions.strip():
        question_items = [x.strip() for x in open_questions.splitlines() if x.strip()]
    if context_note.strip():
        state["context_note"] = context_note.strip()
        task_state["context_note"] = context_note.strip()

    state["pending"] = pending_items
    state["open_questions"] = question_items
    task_state["pending"] = pending_items
    task_state["open_questions"] = question_items
    task_state["phase"] = "in_progress"
    task_state["status"] = "active"
    _write_agent_state(name, state)
    _write_task_state(name, task_id, task_state)
    results.append(f"State updated for '{name}' / {task_id}.")

    decision_id = ""
    if decision_type.strip() and decision_text.strip():
        dtype = decision_type.upper().strip()
        if dtype not in _VALID_TYPES:
            results.append(f"WARNING: Invalid decision_type '{decision_type}'. Decision not recorded.")
        else:
            entries = _load_agent_decisions(name)
            decision_id = _next_decision_id(dtype, entries)
            entry = {
                "id": decision_id,
                "ts": _now_iso(),
                "type": dtype,
                "task_id": task_id,
                "chat": state.get("last_chat", ""),
                "text": decision_text.strip(),
                "supersedes": None,
            }
            _append_agent_decision(name, entry)
            results.append(f"Decision recorded: {decision_id} ({dtype})")

    body_lines = []
    if pending_items:
        body_lines.append("Pending:")
        body_lines.extend(f"- {item}" for item in pending_items)
    if question_items:
        if body_lines:
            body_lines.append("")
        body_lines.append("Open questions:")
        body_lines.extend(f"- {item}" for item in question_items)
    if context_note.strip():
        if body_lines:
            body_lines.append("")
        body_lines.append("Context:")
        body_lines.append(context_note.strip())
    if decision_id:
        if body_lines:
            body_lines.append("")
        body_lines.append(f"Recorded decision: {decision_id}")
    if not body_lines:
        body_lines.append("Checkpoint recorded with no additional details.")

    _append_task_worklog(
        name,
        task_id,
        title="Checkpoint",
        body="\n".join(body_lines),
        chat_folder=str(state.get("last_chat", "")),
    )
    _append_task_event(name, "checkpoint", task_state, detail=decision_id or "state-only")
    _update_task_summary(name, task_id, task_state)
    _update_agent_index(name)
    _update_agent_memory(name)

    return "\n".join(results)


@mcp.tool
def agent_close(
    agent_name: str,
    status: str,
    summary: str,
    files_changed: str = "",
    pending: str = "",
    open_questions: str = "",
) -> str:
    """Close the current agent chat session via the handoff ceremony."""
    name = agent_name.lower().strip()
    isolation_err = _check_write_isolation(name)
    if isolation_err:
        return isolation_err
    if name not in PERSISTENT_AGENTS:
        return f"ERROR: '{name}' is not a persistent agent."

    state = _read_agent_state(name)
    if state is None:
        return f"ERROR: Agent '{name}' has no state. Call agent_start() first."

    valid_statuses = {"complete", "context-exhausted", "interrupted", "blocked"}
    if status not in valid_statuses:
        return f"ERROR: status must be one of {sorted(valid_statuses)}, got '{status}'"

    task_id = str(state.get("current_task_id", "")).strip()
    if not task_id:
        return f"ERROR: Agent '{name}' has no active task id. Call agent_start() first."
    task_state = _read_task_state(name, task_id)
    if task_state is None:
        return f"ERROR: Task state missing for '{name}' / {task_id}."

    chat_folder = str(state.get("last_chat", "")).strip()
    if not chat_folder:
        return f"ERROR: No active chat found for '{name}'. Call agent_start() first."

    chat_path = _task_chats_dir(name, task_id) / chat_folder
    chat_path.mkdir(parents=True, exist_ok=True)
    legacy_chat_path = _legacy_chats_dir(name) / chat_folder
    legacy_chat_path.mkdir(parents=True, exist_ok=True)

    pending_items = [x.strip() for x in pending.splitlines() if x.strip()]
    oq_items = [x.strip() for x in open_questions.splitlines() if x.strip()]
    all_decisions = _load_agent_decisions(name)
    active_ids = [e["id"] for e in _active_entries(all_decisions)]

    pending_lines = "\n".join(f"- {item}" for item in pending_items) if pending_items else "_None._"
    oq_lines = "\n".join(f"- {q}" for q in oq_items) if oq_items else "_None._"
    dec_refs = ", ".join(active_ids) if active_ids else "_None._"

    log_content = f"""# Chat Log: {name} / {task_id} / {chat_folder}
**Date**: {_now_date()}
**Task**: {state.get("current_task", "_Not specified._")}
**Task ID**: {task_id}
**Status**: {status}

---

## What was worked on

{summary.strip()}

---

## Files changed

{files_changed.strip() if files_changed.strip() else "_None specified._"}

---

## Pending at close

{pending_lines}

---

## Open questions at close

{oq_lines}

---

## Active decisions at close

{dec_refs}
"""
    log_file = chat_path / "log.md"
    _write_text(log_file, log_content)
    _write_text(legacy_chat_path / "log.md", log_content)

    _write_text(
        chat_path / "summary.md",
        "\n".join([
            f"# Chat Summary: {name} / {task_id} / {chat_folder}",
            "",
            f"**Status**: {status}",
            "",
            "## Summary",
            summary.strip() or "_No summary provided._",
            "",
        ]),
    )

    execution_record = {
        "timestamp": _now_iso(),
        "agent": name,
        "task_id": task_id,
        "chat": chat_folder,
        "status": status,
        "summary": summary.strip(),
        "files_changed": [line.strip() for line in files_changed.splitlines() if line.strip()],
        "pending": pending_items,
        "open_questions": oq_items,
    }
    execution_path = _next_named_entry(_task_executions_dir(name, task_id), "execution", ".json")
    _write_json(execution_path, execution_record)

    handoff_lines = [
        f"# Agent Handoff: {name} / {task_id}",
        "",
        f"**Created**: {_now_iso()}",
        f"**Chat**: {chat_folder}",
        f"**Status**: {status}",
        "",
        "## Resume from here",
        summary.strip() or "_No summary provided._",
        "",
        "## Pending",
    ]
    if pending_items:
        handoff_lines.extend(f"- {item}" for item in pending_items)
    else:
        handoff_lines.append("_None._")
    handoff_lines += [
        "",
        "## Open questions",
    ]
    if oq_items:
        handoff_lines.extend(f"- {item}" for item in oq_items)
    else:
        handoff_lines.append("_None._")
    handoff_lines += [
        "",
        "## Ceremony note",
        "If this chat changed project truth, code, bugs, roadmap state, or dev docs, run the project checkpoint/commit ceremony as well.",
        "",
    ]
    handoff_path = _next_named_entry(_task_handoffs_dir(name, task_id), "handoff")
    _write_text(handoff_path, "\n".join(handoff_lines))

    _append_task_worklog(
        name,
        task_id,
        title="Handoff ceremony",
        body="\n".join([
            f"Status: {status}",
            "",
            "Summary:",
            summary.strip() or "_No summary provided._",
            "",
            "Pending:",
            pending_lines,
            "",
            "Open questions:",
            oq_lines,
        ]),
        chat_folder=chat_folder,
    )

    task_state["phase"] = "complete" if status == "complete" else "in_progress"
    task_state["status"] = status
    task_state["pending"] = pending_items
    task_state["open_questions"] = oq_items
    task_state["last_chat"] = chat_folder
    task_state["last_handoff"] = handoff_path.relative_to(PROJECT_ROOT).as_posix()
    task_state["last_execution"] = execution_path.relative_to(PROJECT_ROOT).as_posix()
    task_state["last_summary"] = summary.strip()
    _write_task_state(name, task_id, task_state)

    state["phase"] = "complete" if status == "complete" else "in_progress"
    state["pending"] = pending_items
    state["open_questions"] = oq_items
    state["last_chat"] = chat_folder
    state["last_handoff"] = handoff_path.relative_to(PROJECT_ROOT).as_posix()
    _write_agent_state(name, state)

    _append_task_event(name, "closed", task_state, detail=status)
    _update_task_summary(name, task_id, task_state)

    if status == "complete":
        _write_text(
            _task_result_file(name, task_id),
            "\n".join([
                f"# Task Result: {name} / {task_id}",
                "",
                f"**Closed**: {_now_iso()}",
                f"**Final status**: {status}",
                "",
                "## Result",
                summary.strip() or "_No summary provided._",
                "",
            ]),
        )

    _update_agent_index(name)
    _update_agent_memory(name)

    return (
        f"Chat '{chat_folder}' closed via handoff ceremony (status: {status}).\n"
        f"Log: {log_file}\n"
        f"Handoff: {handoff_path}\n"
        f"Index updated: {_agent_index_file(name)}"
    )


@mcp.tool
def agent_resume(agent_name: str) -> str:
    """Return the full resume brief for a persistent agent.

    Builds a brief from:
    - state.json: current task, phase, pending, open questions
    - Last chat's log.md: what happened most recently
    - Active decisions from decisions.jsonl: verbatim decisions still in force

    Use this when starting a new agent chat, or when the Concierge needs
    to brief a specialist agent before delegation.

    Args:
        agent_name: Name of the persistent agent.

    Returns:
        Full resume brief as formatted text ready to inject into a new chat.
    """
    name = agent_name.lower().strip()
    if name in STATELESS_AGENTS:
        return f"ERROR: '{name}' is a stateless agent - no resume brief exists by design."
    if name not in PERSISTENT_AGENTS:
        return f"ERROR: Unknown agent '{name}'. Persistent agents: {sorted(PERSISTENT_AGENTS)}"

    state = _read_agent_state(name)
    if state is None:
        return f"Agent '{name}' has no prior memory. This will be a first session."

    return _build_agent_resume_brief(name)


@mcp.tool
def agent_status(agent_name: str = "") -> str:
    """Show the current status of one or all persistent agents.

    Args:
        agent_name: Agent name to inspect, or empty string to show all agents.

    Returns:
        For a specific agent: full state.json content.
        For all agents: summary table with task id, task, phase, last chat, pending count.
    """
    if agent_name.strip():
        name = agent_name.lower().strip()
        if name not in PERSISTENT_AGENTS:
            return f"ERROR: Unknown agent '{name}'. Persistent agents: {sorted(PERSISTENT_AGENTS)}"
        state = _read_agent_state(name)
        if state is None:
            return f"Agent '{name}': no memory yet (call agent_start() to initialize)."
        return json.dumps(state, indent=2, ensure_ascii=False)

    lines = [
        "# Agent Status",
        "",
        f"**Date**: {_now_date()}",
        "",
        "| Agent | Task ID | Task | Phase | Last chat | Pending |",
        "|-------|---------|------|-------|-----------|---------|",
    ]
    for name in sorted(PERSISTENT_AGENTS):
        state = _read_agent_state(name)
        if state is None:
            lines.append(f"| {name} | - | _no memory_ | - | - | - |")
        else:
            task = state.get("current_task", "")
            task_short = (task[:40] + "...") if len(task) > 40 else task
            task_short = task_short.replace("|", "/")
            phase = state.get("phase", "unknown")
            last_chat = state.get("last_chat", "-")
            pending_count = len(state.get("pending", []))
            task_id = state.get("current_task_id", "-")
            lines.append(
                f"| {name} | {task_id} | {task_short} | {phase} | {last_chat} | {pending_count} items |"
            )

    lines += [
        "",
        f"**Stateless agents** (no memory by design): {', '.join(sorted(STATELESS_AGENTS))}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
