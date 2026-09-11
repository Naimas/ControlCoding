#!/usr/bin/env python3
"""mcp_handoff.py - F1 Context Handoff MCP server for ControlCoding.

Implements the F1 Context Handoff Protocol (global level): verbatim decision
storage and context brief generation for seamless session transitions.

Tools:
    write_decision  - Record a verbatim decision/constraint/plan/finding
    generate_brief  - Generate warm.md context brief for next chat session

Setup in .controlcoding/settings.json:
    {
      "mcpServers": {
        "handoff": {
          "command": "python",
          "args": ["tools/mcp_handoff.py"]
        }
      }
    }

Requirements:
    pip install fastmcp

See dev/design/13_DSN_HandoffProtocol_InProgress.md for full specification.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP
from cc_lockfile import LockfileGuard, LockfileTimeoutError, adjacent_lockfile_guard

# --- Configuration ---

PROJECT_ROOT = Path(os.environ.get("SESSION_PROJECT_ROOT", ".")).resolve()
STATUS_FILE = PROJECT_ROOT / os.environ.get("SESSION_STATUS_FILE", "STATUS.md")
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


SESSIONS_DIR = _control_plane_dir() / "sessions"
DECISIONS_FILE = SESSIONS_DIR / "decisions.jsonl"
WARM_BRIEF_FILE = SESSIONS_DIR / "warm.md"
BRIEF_SIZE_LIMIT = 6 * 1024  # 6KB
_DECISIONS_LOCK_TIMEOUT_SECONDS = 5.0
_DECISIONS_LOCK_POLL_SECONDS = 0.05

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
    "handoff",
    instructions=(
        "F1 Context Handoff tools for ControlCoding. "
        "Call write_decision() whenever you make a decision, discover a "
        "constraint, or form a plan that must survive context transitions - "
        "use verbatim text, not summaries. "
        "Call generate_brief() when context is filling up or before ending "
        "a session. The brief is read at next session start via 'cc resume'."
    ),
)


# --- Shared utilities ---


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


def _decisions_lock_guard() -> LockfileGuard:
    return adjacent_lockfile_guard(
        DECISIONS_FILE,
        timeout_seconds=_DECISIONS_LOCK_TIMEOUT_SECONDS,
        poll_seconds=_DECISIONS_LOCK_POLL_SECONDS,
    )


def _load_decisions_unlocked() -> list:
    """Load all entries from decisions.jsonl. Returns [] if file missing."""
    if not DECISIONS_FILE.exists():
        return []
    entries = []
    for line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _load_decisions() -> list:
    """Load a consistent snapshot of decisions.jsonl."""
    with _decisions_lock_guard():
        return _load_decisions_unlocked()


def _append_decision_unlocked(entry: dict) -> None:
    with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


def _status_excerpt() -> str:
    """Extract Current State and Next Steps sections from STATUS.md."""
    if not STATUS_FILE.exists():
        return "_No STATUS.md found._"
    status = STATUS_FILE.read_text(encoding="utf-8")
    lines = status.splitlines()
    result = []
    capture = False
    for line in lines:
        if line.startswith("## Current State") or line.startswith("## Next Steps"):
            capture = True
        elif (
            line.startswith("## ")
            and capture
            and not line.startswith("## Current State")
            and not line.startswith("## Next Steps")
        ):
            capture = False
        if capture:
            result.append(line)
    return "\n".join(result).strip() if result else status[:500]


# --- Tools ---


@mcp.tool
def write_decision(
    decision_type: str,
    text: str,
    supersedes: str = "",
    decision_id: str = "",
) -> str:
    """Record a verbatim decision, constraint, plan, or finding.

    Call this when you make a decision that must survive context transitions.
    Text must be verbatim - the actual reasoning, not a summary.

    Semantic Fidelity rule (Section 9.14): write the full statement including
    the 'because'. Not a bullet point. Not a keyword list.

    Args:
        decision_type: One of: DECISION, CONSTRAINT, PLAN, FINDING, QUESTION
        text: Verbatim content. No paraphrase, no compression. Write the
              actual decision as it was reasoned, including the why.
        supersedes: ID of a previous entry this replaces (empty if original).
        decision_id: Optional explicit ID (e.g. DEC-005). Auto-generated
                     from type prefix if omitted.

    Returns:
        Confirmation with entry ID and path.
    """
    dtype = decision_type.upper().strip()
    if dtype not in _VALID_TYPES:
        return f"ERROR: decision_type must be one of {sorted(_VALID_TYPES)}, got '{decision_type}'"
    if not text.strip():
        return "ERROR: text must not be empty."

    normalized_text = text.strip()
    normalized_supersedes = supersedes.strip()
    normalized_id = decision_id.strip()
    DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _decisions_lock_guard():
            entries = _load_decisions_unlocked()
            eid = normalized_id if normalized_id else _next_decision_id(dtype, entries)
            existing_ids = {e.get("id") for e in entries}
            if eid in existing_ids:
                return (
                    f"ERROR: ID '{eid}' already exists. Use a different ID or "
                    f"leave decision_id empty to auto-assign."
                )
            entry = {
                "id": eid,
                "ts": _now_iso(),
                "type": dtype,
                "text": normalized_text,
                "supersedes": normalized_supersedes if normalized_supersedes else None,
            }
            _append_decision_unlocked(entry)
    except LockfileTimeoutError as exc:
        return f"ERROR: {exc}"

    supersedes_note = f", supersedes {normalized_supersedes}" if normalized_supersedes else ""
    return f"Decision recorded: {eid} ({dtype}{supersedes_note})"


@mcp.tool
def generate_brief(current_task: str = "") -> str:
    """Generate a context brief (warm.md) for the next chat session.

    Reads all decisions.jsonl entries and constructs a compact verbatim
    brief under 6KB. The brief contains verbatim decisions - no paraphrase.
    Superseded entries are excluded. The brief may omit oldest decisions
    if the size limit is exceeded - it never compresses or paraphrases.

    Call this:
    - When context is approaching its limit
    - At session end
    - Before starting a new chat manually

    Args:
        current_task: One-line description of what was being worked on.
                      If empty, falls back to STATUS.md Next Steps.

    Returns:
        Path to generated warm.md and summary of included entries.
    """
    try:
        entries = _load_decisions()
    except LockfileTimeoutError as exc:
        return f"ERROR: {exc}"
    active = _active_entries(entries)

    decisions = [e for e in active if e["type"] == "DECISION"]
    constraints = [e for e in active if e["type"] == "CONSTRAINT"]
    questions = [e for e in active if e["type"] == "QUESTION"]
    plans = [e for e in active if e["type"] == "PLAN"]

    type_counts = {}
    for e in entries:
        type_counts[e["type"]] = type_counts.get(e["type"], 0) + 1

    task_line = current_task.strip()
    if not task_line:
        excerpt = _status_excerpt()
        for line in excerpt.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("**"):
                task_line = stripped
                break

    date_str = _now_date()
    ts_str = _now_iso()

    counts_str = ", ".join(
        f"{v} {k.lower()}s" for k, v in sorted(type_counts.items()) if v
    )
    decisions_display_path = f"{SESSIONS_DIR.relative_to(PROJECT_ROOT).as_posix()}/decisions.jsonl"
    search_cmd = (
        "python -c \"import json; "
        "[print(e['id'], e['text'][:80]) for e in "
        f"map(json.loads, open('{decisions_display_path}'))]\""
    )

    def build_brief(dec_list, con_list, q_list, pl_list, truncated=False) -> str:
        parts = [
            "# Context Brief",
            f"**Session**: {date_str}",
            f"**Generated**: {ts_str}",
            f"**Source**: {decisions_display_path}",
            "",
            "---",
            "",
            "## Current task",
            task_line if task_line else "_Not specified._",
            "",
            "---",
        ]
        if dec_list:
            parts += ["", "## Active decisions [verbatim]", "", _fmt_decision_block(dec_list), "", "---"]
        if con_list:
            parts += ["", "## Active constraints [verbatim]", "", _fmt_decision_block(con_list), "", "---"]
        if pl_list:
            parts += ["", "## Active plans [verbatim]", "", _fmt_decision_block(pl_list), "", "---"]
        if q_list:
            parts += ["", "## Open questions", "", _fmt_decision_block(q_list), "", "---"]
        parts += ["", "## Project state", "", _status_excerpt(), "", "---", ""]
        if truncated:
            parts += [
                "_Brief truncated: oldest decisions omitted to stay under 6KB._",
                "_See full log for complete history._",
                "",
            ]
        parts += [
            "## Full decision log",
            f"Path: `{decisions_display_path}`",
            f"Entries: {counts_str if counts_str else 'none'}",
            f"To search: `{search_cmd}`",
        ]
        return "\n".join(parts)

    brief = build_brief(decisions, constraints, questions, plans)

    truncated = False
    if len(brief.encode("utf-8")) > BRIEF_SIZE_LIMIT:
        truncated = True
        brief = build_brief(decisions[-10:], constraints, questions[-5:], plans[-5:], truncated=True)

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    WARM_BRIEF_FILE.write_text(brief, encoding="utf-8")

    size_kb = len(brief.encode("utf-8")) / 1024
    trunc_note = " (truncated)" if truncated else ""
    return (
        f"Context brief generated: {WARM_BRIEF_FILE}\n"
        f"Size: {size_kb:.1f}KB{trunc_note}\n"
        f"Active entries: {len(decisions)} decisions, {len(constraints)} constraints, "
        f"{len(questions)} questions, {len(plans)} plans"
    )


if __name__ == "__main__":
    mcp.run()
