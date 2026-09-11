#!/usr/bin/env python3
"""mcp_session.py - Session documentation MCP server for ControlCoding.

Provides structured session documentation tools that persist across sessions.
The AI calls these tools to maintain STATUS.md, STATUS_HISTORY.md, and
per-commit devlog files automatically.

Tools:
    update_status   - Write/overwrite STATUS.md, archive previous to history
    write_devlog    - Create a new devlog entry file for a commit
    checkpoint      - Combined: update_status + write_devlog + context-file check
    read_status     - Read current STATUS.md
    read_history    - Read last N entries from STATUS_HISTORY.md
    read_devlog     - Read last N devlog files
    escalate_to_human - L4 escalation: write structured problem for human decision

For global context handoff (decisions.jsonl, warm.md): see mcp_handoff.py
For per-agent memory (agent_start/close/resume): see mcp_agent_memory.py

The canonical project constitution is CONTROLCODING.md. Host-native files
such as CLAUDE.md or AGENTS.md are derived sync artifacts and should be
reviewed in the same ceremony when project truth changes.

Setup in .controlcoding/settings.json:
    {
      "mcpServers": {
        "session-manager": {
          "command": "python",
          "args": ["tools/mcp_session.py"]
        }
      }
    }

Requirements:
    pip install fastmcp
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

# --- Configuration ---

PROJECT_ROOT = Path(os.environ.get("SESSION_PROJECT_ROOT", ".")).resolve()
DEVLOG_DIR = PROJECT_ROOT / os.environ.get("SESSION_DEVLOG_DIR", "devlog")
STATUS_FILE = PROJECT_ROOT / os.environ.get("SESSION_STATUS_FILE", "STATUS.md")
HISTORY_FILE = PROJECT_ROOT / os.environ.get("SESSION_HISTORY_FILE", "STATUS_HISTORY.md")
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


def _context_doc_path() -> Path:
    canonical = PROJECT_ROOT / "CONTROLCODING.md"
    if canonical.exists():
        return canonical
    legacy = PROJECT_ROOT / "CLAUDE.md"
    if legacy.exists():
        return legacy
    return canonical


CLAUDE_MD = _context_doc_path()
SESSION_COUNTER_FILE = _control_plane_dir() / "session_counter.json"


def _host_context_path() -> Path | None:
    gateway_path = _control_plane_dir() / "gateway_config.json"
    if gateway_path.exists():
        try:
            gateway = json.loads(gateway_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            gateway = {}
        host_profile = gateway.get("hostProfile", {}) if isinstance(gateway, dict) else {}
        relative = ""
        if isinstance(host_profile, dict):
            relative = str(host_profile.get("contextFile", "")).strip()
        if not relative:
            relative = str(gateway.get("contextFile", "")).strip() if isinstance(gateway, dict) else ""
        if relative:
            return PROJECT_ROOT / relative

    for filename in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".clinerules"):
        candidate = PROJECT_ROOT / filename
        if candidate.exists() and candidate != CLAUDE_MD:
            return candidate
    return None


def _get_session_number() -> int:
    """Get and increment the session counter."""
    counter_file = SESSION_COUNTER_FILE
    counter_file.parent.mkdir(parents=True, exist_ok=True)

    count = 1
    if counter_file.exists():
        try:
            data = json.loads(counter_file.read_text(encoding="utf-8"))
            count = data.get("session", 0) + 1
        except (json.JSONDecodeError, OSError):
            count = 1

    counter_file.write_text(
        json.dumps({"session": count}), encoding="utf-8"
    )
    return count


SESSION_NUMBER = None


def _session_number() -> int:
    """Return the current session number, allocating one only when needed."""
    global SESSION_NUMBER
    if SESSION_NUMBER is None:
        SESSION_NUMBER = _get_session_number()
    return SESSION_NUMBER


# --- MCP Server ---

mcp = FastMCP(
    "session-manager",
    instructions=(
        "Session documentation tools for ControlCoding. "
        "Call checkpoint() at every significant milestone (feature done, "
        "bug fixed, refactor complete). Call read_status() at session start "
        "to understand current project state. Call read_devlog() to review "
        "recent changes. "
        "For global context handoff (write_decision, generate_brief) use the "
        "'handoff' MCP server. For per-agent memory (agent_start, agent_close) "
        "use the 'agent-memory' MCP server."
    ),
)


# --- Utilities ---


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slugify(text: str, max_words: int = 4) -> str:
    """Convert text to kebab-case slug."""
    words = re.sub(r"[^a-z0-9\s]", "", text.lower()).split()
    return "-".join(words[:max_words])


def _next_devlog_number() -> int:
    """Get the next devlog entry number for today."""
    DEVLOG_DIR.mkdir(parents=True, exist_ok=True)
    today = _now_date()
    existing = list(DEVLOG_DIR.glob(f"{today}_*.md"))
    if not existing:
        return 1
    numbers = []
    for f in existing:
        parts = f.stem.split("_")
        if len(parts) >= 2:
            try:
                numbers.append(int(parts[1]))
            except ValueError:
                pass
    return (max(numbers) + 1) if numbers else 1


def _generate_devlog_index() -> None:
    """Rebuild devlog/index.md as part of the project ceremony."""
    DEVLOG_DIR.mkdir(parents=True, exist_ok=True)
    entries = sorted(
        f for f in DEVLOG_DIR.glob("*.md")
        if f.name != "index.md"
    )

    lines = [
        "# Devlog Index",
        "",
        "> Auto-generated by checkpoint(). Do not edit manually.",
        "",
    ]
    if not entries:
        lines += [
            "_No devlog entries yet._",
            "",
        ]
    else:
        for entry in entries:
            lines.append(f"- [{entry.name}]({entry.name})")

    (DEVLOG_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _context_review_status(context_path: Path, files_changed: str) -> str:
    if not context_path.exists():
        return f"MISSING - create or sync {context_path.name} before closing the ceremony."

    if not files_changed:
        return "OK - no update needed"

    try:
        context_content = context_path.read_text(encoding="utf-8").lower()
    except OSError:
        return f"REVIEW NEEDED - could not read {context_path.name}."

    changed_files = [f.strip() for f in files_changed.replace("\n", ",").split(",") if f.strip()]
    affected_sections = []
    for file_path in changed_files:
        basename = Path(file_path).stem.lower()
        if basename and basename in context_content:
            affected_sections.append(file_path)

    if affected_sections:
        return (
            f"REVIEW NEEDED - these changed files are referenced in {context_path.name}: "
            f"{', '.join(affected_sections)}. Check if architecture, boundaries, "
            f"or invariants need updating."
        )
    return "OK - no direct overlap detected"


def _archive_status() -> str:
    """Archive current STATUS.md to STATUS_HISTORY.md."""
    if not STATUS_FILE.exists():
        return "No previous status to archive."

    current = STATUS_FILE.read_text(encoding="utf-8").strip()
    if not current:
        return "Previous status was empty."

    separator = f"\n\n---\n\n## Archived: {_now_iso()}\n\n"
    if HISTORY_FILE.exists():
        existing = HISTORY_FILE.read_text(encoding="utf-8")
        HISTORY_FILE.write_text(
            existing + separator + current + "\n",
            encoding="utf-8",
        )
    else:
        header = "# Status History\n\nArchived status entries, newest last.\n"
        HISTORY_FILE.write_text(
            header + separator + current + "\n",
            encoding="utf-8",
        )

    history = HISTORY_FILE.read_text(encoding="utf-8")
    entry_count = history.count("## Archived:")
    return f"Archived to STATUS_HISTORY.md (entry #{entry_count})"


# --- Tools ---


@mcp.tool
def update_status(
    what_done: str,
    what_next: str,
    blockers: str = "",
    notes: str = "",
) -> str:
    """Update the project STATUS.md file.

    Archives the current status to STATUS_HISTORY.md before overwriting.
    Call this when project state changes significantly.

    Args:
        what_done: What was accomplished in this session/milestone.
        what_next: What should be done next (for the next session to read).
        blockers: Any blocking issues. Empty string if none.
        notes: Additional context or observations.

    Returns:
        Confirmation with archive info.
    """
    archive_msg = _archive_status()

    content = f"""# Project Status
> Updated: {_now_iso()} | Session: {_session_number()}

## Current State
{what_done}

## Next Steps
{what_next}

## Blockers
{blockers if blockers else "None"}
"""
    if notes:
        content += f"\n## Notes\n{notes}\n"

    STATUS_FILE.write_text(content, encoding="utf-8")

    return f"STATUS.md updated. {archive_msg}"


@mcp.tool
def write_devlog(
    summary: str,
    decisions: str = "",
    problems: str = "",
    files_changed: str = "",
) -> str:
    """Create a new devlog entry for a commit or milestone.

    Each entry is a separate file in devlog/ named with date and sequence number.

    Args:
        summary: Brief description of what was done (1-2 sentences).
        decisions: Key decisions made and their rationale (bulleted list).
        problems: Problems encountered during implementation (bulleted list).
        files_changed: List of files created or modified.

    Returns:
        The created devlog filename.
    """
    DEVLOG_DIR.mkdir(parents=True, exist_ok=True)
    num = _next_devlog_number()
    slug = _slugify(summary)
    filename = f"{_now_date()}_{num:03d}_{slug}.md"
    filepath = DEVLOG_DIR / filename

    content = f"""# Devlog: {summary}
> Date: {_now_date()} | Entry: {num:03d} | Session: {_session_number()}

## Summary
{summary}

## Decisions Made
{decisions if decisions else "None recorded."}

## Problems Encountered
{problems if problems else "None."}

## Files Changed
{files_changed if files_changed else "Not specified."}
"""

    filepath.write_text(content, encoding="utf-8")
    return f"Devlog created: {filename}"


@mcp.tool
def checkpoint(
    summary: str,
    decisions: str = "",
    problems: str = "",
    files_changed: str = "",
    what_next: str = "",
    blockers: str = "",
) -> str:
    """Combined operation: update status + write devlog + check context rules.

    Call this at every significant milestone. It:
    1. Archives the current STATUS.md to history
    2. Writes a new STATUS.md with current state
    3. Creates a devlog entry for this milestone
    4. Checks if the canonical context and host-derived context might need updating
    5. Rebuilds devlog/index.md

    Args:
        summary: What was accomplished (used for both status and devlog).
        decisions: Key decisions made.
        problems: Problems encountered.
        files_changed: Files created or modified.
        what_next: What should be done next (for STATUS.md).
        blockers: Any blocking issues.

    Returns:
        Structured report of all actions taken.
    """
    results = []

    status_result = update_status(
        what_done=summary,
        what_next=what_next if what_next else "Continue from where this checkpoint left off.",
        blockers=blockers,
        notes=f"Decisions: {decisions}" if decisions else "",
    )
    results.append(f"Status: {status_result}")

    devlog_result = write_devlog(
        summary=summary,
        decisions=decisions,
        problems=problems,
        files_changed=files_changed,
    )
    results.append(f"Devlog: {devlog_result}")

    canonical_context = CLAUDE_MD
    results.append(
        f"{canonical_context.name}: "
        f"{_context_review_status(canonical_context, files_changed)}"
    )

    host_context = _host_context_path()
    if host_context and host_context != canonical_context:
        results.append(
            f"{host_context.name}: "
            f"{_context_review_status(host_context, files_changed)}"
        )

    _generate_devlog_index()
    results.append("devlog/index.md: updated")
    results.append(
        "Ceremony follow-up: review BUGS.md, ROADMAP.md, and affected dev docs when the change impacts them."
    )

    return "\n".join(results)


@mcp.tool
def read_status() -> str:
    """Read the current project STATUS.md.

    Call this at the start of every new session to understand where
    the project is and what needs to be done next.

    Returns:
        The content of STATUS.md, or a message if no status exists.
    """
    if not STATUS_FILE.exists():
        return (
            "No STATUS.md found. This appears to be a new project or "
            "the first session. Use checkpoint() after your first "
            "significant change to create the initial status."
        )
    return STATUS_FILE.read_text(encoding="utf-8")


@mcp.tool
def read_history(last_n: int = 5) -> str:
    """Read recent entries from STATUS_HISTORY.md.

    Shows how the project state evolved over time.

    Args:
        last_n: Number of most recent entries to return (default 5).

    Returns:
        The last N status history entries.
    """
    if not HISTORY_FILE.exists():
        return "No status history yet. History is created when STATUS.md is updated."

    content = HISTORY_FILE.read_text(encoding="utf-8")
    entries = content.split("## Archived:")

    if len(entries) <= 1:
        return "Status history is empty."

    recent = entries[-last_n:] if last_n < len(entries) else entries[1:]
    result_parts = [f"Last {len(recent)} status history entries:\n"]
    for entry in recent:
        result_parts.append(f"## Archived:{entry.rstrip()}")

    return "\n\n---\n".join(result_parts)


@mcp.tool
def read_devlog(last_n: int = 3) -> str:
    """Read the most recent devlog entries.

    Shows what was done in recent commits/milestones.

    Args:
        last_n: Number of most recent devlog files to return (default 3).

    Returns:
        Content of the last N devlog files.
    """
    if not DEVLOG_DIR.exists():
        return "No devlog directory found. Devlogs are created by checkpoint()."

    files = sorted(DEVLOG_DIR.glob("*.md"))
    if not files:
        return "No devlog entries yet."

    recent = files[-last_n:]
    result_parts = [f"Last {len(recent)} devlog entries:\n"]
    for f in recent:
        content = f.read_text(encoding="utf-8").strip()
        result_parts.append(f"--- {f.name} ---\n{content}")

    return "\n\n".join(result_parts)


@mcp.tool
def escalate_to_human(
    problem: str,
    options: str = "",
    session_id: str = "",
    consultant_summary: str = "",
    socratic_insight: str = "",
) -> str:
    """L4 Escalation: present a structured problem to the human for decision.

    Use this when:
    - Debug consultation (L2) and socratic challenge (L3) did not resolve
      the issue
    - A decision has high architectural impact and needs human judgment
    - Multiple valid approaches exist with significant trade-offs

    This tool writes a structured escalation file and signals that the AI
    should STOP and wait for human input before proceeding.

    Args:
        problem: Clear description of the unresolved problem.
        options: Available options with pros and cons for each.
                 Format as a structured list, e.g.:
                 "A) Do X - Pro: fast. Con: fragile.
                  B) Do Y - Pro: clean. Con: slow."
        session_id: If this escalation follows a consultation session,
                    include the session_id so the human can review the
                    full exchange history in the control-plane consult session log.
        consultant_summary: Summary of what the debug consultant said.
        socratic_insight: Summary of what the socratic consultant found.

    Returns:
        Confirmation that escalation was written. The AI should STOP
        working on this problem and wait for human response.
    """
    escalation_dir = _control_plane_dir()
    escalation_dir.mkdir(parents=True, exist_ok=True)
    escalation_file = escalation_dir / "escalation.md"
    consult_sessions_path = f"{_control_plane_dir().relative_to(PROJECT_ROOT).as_posix()}/consult_sessions.jsonl"

    session_ref = ""
    if session_id:
        session_ref = (
            f"\n## Session Reference\n"
            f"Session ID: `{session_id}`\n"
            f"Full exchange log: `{consult_sessions_path}`\n"
            f"Filter: `session_id={session_id}`\n"
        )

    consultant_section = ""
    if consultant_summary:
        consultant_section = (
            f"\n## Consultant Analysis (L2)\n{consultant_summary}\n"
        )

    socratic_section = ""
    if socratic_insight:
        socratic_section = (
            f"\n## Socratic Challenge (L3)\n{socratic_insight}\n"
        )

    options_section = ""
    if options:
        options_section = f"\n## Options\n{options}\n"

    content = f"""# ESCALATION: Human Decision Required
> Generated: {_now_iso()} | Session: {_session_number()}

## Problem
{problem}
{consultant_section}{socratic_section}{options_section}{session_ref}
## Your Input Needed

Review the problem, consultant analyses, and options above.
You can:
- Choose one of the proposed options
- Propose a different approach
- Ask for re-analysis with a different focus
- Provide a hint or partial direction

Respond in this chat or edit this file, then tell the AI to continue.
"""

    escalation_file.write_text(content, encoding="utf-8")

    log_file = escalation_dir / "consult_log.jsonl"
    entry = {
        "timestamp": _now_iso(),
        "event": "escalation_l4",
        "session_id": session_id,
        "problem_summary": problem[:200],
        "options_count": options.count("\n") + 1 if options else 0,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return (
        f"ESCALATION written to {_control_plane_dir().relative_to(PROJECT_ROOT).as_posix()}/escalation.md\n\n"
        f"STOP working on this problem. Wait for human input.\n"
        f"The human will review the problem, consultant analyses, and "
        f"options, then provide direction.\n\n"
        f"Do NOT proceed with any of the options until the human responds."
    )


if __name__ == "__main__":
    mcp.run()
