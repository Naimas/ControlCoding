"""Session GraphRAG storage and CLI commands."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .ledger import _insert_event
from .migrations import inspect_schema
from .schema import (
    CONTROL_DIRNAME,
    SESSION_EDGE_TYPES,
    SESSION_RECORD_SCHEMA_VERSION,
    VALID_SESSION_MODES,
    VALID_SESSION_STATUSES,
    VIEWS_DIRNAME,
)
from .store import (
    _content_hash_text,
    _ensure_schema,
    _json_dumps,
    _memory_connection,
    _now_iso,
    _print_json_error_or_text,
    _print_json_or_text,
    _readonly_memory_connection,
    _require_initialized,
    _set_metadata,
    _transactional_write_text,
    _views_dir,
)

SESSION_VIEW_FILENAMES = [
    "SESSION_INDEX.md",
    "SESSION_BY_DATE.md",
    "SESSION_BY_TOPIC.md",
    "SESSION_BY_CATEGORY.md",
    "SESSION_BY_STATUS.md",
    "SESSION_BY_COMMIT.md",
    "SESSION_BY_FILE.md",
    "SESSION_FOLLOWUPS.md",
    "SESSION_HANDOFF.md",
]

def _is_inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_path(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_output_path(project: Path, output: Path) -> Path:
    root = project.resolve()
    target = output.expanduser()
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    if not _is_inside(root, target):
        raise ValueError("output path must stay inside the selected project root")
    return target


_SESSION_LIST_FIELDS = {
    "categories",
    "files_changed",
    "docs_changed",
    "commands_run",
    "tests_run",
    "commits",
    "packets",
    "decisions",
    "followups",
    "links",
}

_SESSION_FIELD_BY_EDGE_TYPE = {
    "belongs_to_category": "categories",
    "changes_doc": "docs_changed",
    "changes_file": "files_changed",
    "left_followup": "followups",
    "produced_commit": "commits",
    "produced_packet": "packets",
    "references_decision": "decisions",
    "uses_command": "commands_run",
    "verified_by": "tests_run",
}

_SESSION_ORDER_BY = "updated_at DESC, started_at DESC, created_at DESC, id DESC"


def _json_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _slug(value: str, fallback: str = "session") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_")
    return slug[:48] or fallback


def _make_session_id(topic: str, timestamp: str) -> str:
    stamp = timestamp.replace("-", "").replace(":", "").replace("Z", "Z")
    digest = hashlib.sha1(f"{timestamp}:{topic}".encode("utf-8")).hexdigest()[:8].upper()
    return f"SESSION_{stamp}_{_slug(topic).upper()}_{digest}"


def _edge_id(session_id: str, edge_type: str, target: str) -> str:
    seed = f"{session_id}:{edge_type}:{target}"
    return "SESSION_EDGE_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _normalize_text_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _append_unique_text(items: list[Any], value: str) -> list[str]:
    text_items = [str(item) for item in items if str(item or "").strip()]
    value = str(value or "").strip()
    if value and value not in text_items:
        text_items.append(value)
    return text_items


def _append_unique_link(items: list[Any], link: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("type") or ""), str(item.get("target") or ""))
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            normalized.append(item)
    key = (str(link.get("type") or ""), str(link.get("target") or ""))
    if key[0] and key[1] and key not in seen:
        normalized.append(link)
    return normalized


def _session_edges(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, session_id, type, target, target_type, data, created_at
        FROM session_edges
        WHERE session_id = ?
        ORDER BY created_at, type, target
        """,
        (session_id,),
    ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "sessionId": str(row["session_id"]),
            "type": str(row["type"]),
            "target": str(row["target"]),
            "targetType": str(row["target_type"] or ""),
            "data": _json_dict(str(row["data"] or "{}")),
            "createdAt": str(row["created_at"]),
        }
        for row in rows
    ]


def _session_payload(row: sqlite3.Row, edges: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "schemaVersion": int(row["schema_version"]),
        "startedAt": str(row["started_at"]),
        "endedAt": str(row["ended_at"] or ""),
        "mode": str(row["mode"]),
        "scope": str(row["scope"]),
        "topic": str(row["topic"]),
        "status": str(row["status"]),
        "summary": str(row["summary"] or ""),
        "categories": _json_list(str(row["categories"] or "[]")),
        "filesChanged": _json_list(str(row["files_changed"] or "[]")),
        "docsChanged": _json_list(str(row["docs_changed"] or "[]")),
        "commandsRun": _json_list(str(row["commands_run"] or "[]")),
        "testsRun": _json_list(str(row["tests_run"] or "[]")),
        "commits": _json_list(str(row["commits"] or "[]")),
        "packets": _json_list(str(row["packets"] or "[]")),
        "decisions": _json_list(str(row["decisions"] or "[]")),
        "followups": _json_list(str(row["followups"] or "[]")),
        "links": _json_list(str(row["links"] or "[]")),
        "data": _json_dict(str(row["data"] or "{}")),
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
        "edges": edges or [],
    }


def _session_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM session_records WHERE id = ?",
        (session_id,),
    ).fetchone()


def _all_session_payloads(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT * FROM session_records ORDER BY {_SESSION_ORDER_BY}").fetchall()
    return [_session_payload(row, _session_edges(conn, str(row["id"]))) for row in rows]


def _print_missing_session(session_id: str, json_output: bool = False) -> int:
    _print_json_error_or_text(json_output, "session_not_found", f"session record not found: {session_id}")
    return 1


def _load_session_lists(row: sqlite3.Row) -> dict[str, list[Any]]:
    return {field: _json_list(str(row[field] or "[]")) for field in _SESSION_LIST_FIELDS}


def _update_session_lists(
    conn: sqlite3.Connection,
    session_id: str,
    lists: dict[str, list[Any]],
    updated_at: str,
) -> None:
    conn.execute(
        """
        UPDATE session_records
        SET categories = ?,
            files_changed = ?,
            docs_changed = ?,
            commands_run = ?,
            tests_run = ?,
            commits = ?,
            packets = ?,
            decisions = ?,
            followups = ?,
            links = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            _json_dumps(lists["categories"]),
            _json_dumps(lists["files_changed"]),
            _json_dumps(lists["docs_changed"]),
            _json_dumps(lists["commands_run"]),
            _json_dumps(lists["tests_run"]),
            _json_dumps(lists["commits"]),
            _json_dumps(lists["packets"]),
            _json_dumps(lists["decisions"]),
            _json_dumps(lists["followups"]),
            _json_dumps(lists["links"]),
            updated_at,
            session_id,
        ),
    )


def _short(value: Any, limit: int = 90) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _date_key(value: str) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else "unknown"


def _session_view_header(title: str, generated_at: str, command: str, sessions: list[dict[str, Any]]) -> str:
    active_count = sum(1 for session in sessions if session.get("status") == "active")
    followup_count = sum(len(session.get("followups") or []) for session in sessions)
    return "\n".join(
        [
            f"# {title}",
            "",
            f"> Generated at: {generated_at}",
            f"> Generation command: `{command}`",
            f"> Source record counts: {len(sessions)} sessions",
            f"> Active sessions: {active_count}",
            f"> Open follow-ups: {followup_count}",
            "",
        ]
    )


def _session_table(sessions: list[dict[str, Any]], limit: int = 100) -> str:
    if not sessions:
        return "_No session records._\n"
    lines = [
        "| ID | Status | Topic | Updated | Summary |",
        "|---|---|---|---|---|",
    ]
    for session in sessions[:limit]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_short(session.get('id'), 70)}`",
                    _short(session.get("status"), 24),
                    _short(session.get("topic"), 80),
                    _short(session.get("updatedAt"), 32),
                    _short(session.get("summary"), 100),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _group_by_text(sessions: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        key = str(session.get(field) or "").strip() or "(none)"
        groups.setdefault(key, []).append(session)
    return groups


def _group_by_list_field(sessions: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        values = session.get(field)
        if not isinstance(values, list) or not values:
            groups.setdefault("(none)", []).append(session)
            continue
        for value in values:
            key = str(value or "").strip() or "(none)"
            groups.setdefault(key, []).append(session)
    return groups


def _grouped_session_sections(groups: dict[str, list[dict[str, Any]]], limit: int = 25) -> str:
    if not groups:
        return "_No session records._\n"
    lines: list[str] = []
    for key in sorted(groups):
        sessions = sorted(groups[key], key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        lines.extend([f"## {_short(key, 120)}", "", _session_table(sessions, limit=limit), ""])
    return "\n".join(lines).rstrip() + "\n"


def _session_followup_view(sessions: list[dict[str, Any]]) -> str:
    rows: list[tuple[str, str, str, str]] = []
    for session in sessions:
        for followup in session.get("followups") or []:
            rows.append((
                str(session.get("id") or ""),
                str(session.get("status") or ""),
                str(session.get("topic") or ""),
                str(followup or ""),
            ))
    if not rows:
        return "_No open follow-ups recorded in sessions._\n"
    lines = [
        "| Session | Status | Topic | Follow-up |",
        "|---|---|---|---|",
    ]
    for session_id, status, topic, followup in rows:
        lines.append(
            f"| `{_short(session_id, 70)}` | {_short(status, 24)} | {_short(topic, 80)} | {_short(followup, 120)} |"
        )
    return "\n".join(lines) + "\n"


def _session_handoff_view(sessions: list[dict[str, Any]]) -> str:
    if not sessions:
        return "_No session records._\n"
    latest = sessions[0]
    active = [session for session in sessions if session.get("status") == "active"]
    needs_followup = [
        session
        for session in sessions
        if session.get("status") == "needs_followup" or session.get("followups")
    ]
    lines = [
        "## Latest Session",
        "",
        _session_table([latest], limit=1),
        "",
        "## Active Sessions",
        "",
        _session_table(active, limit=20),
        "",
        "## Sessions With Follow-ups",
        "",
        _session_table(needs_followup, limit=50),
        "",
        "## Recommended Next Reads",
        "",
    ]
    next_reads: list[str] = []
    for session in sessions[:10]:
        for path in (session.get("docsChanged") or []) + (session.get("filesChanged") or []) + (session.get("packets") or []):
            path_text = str(path or "").strip()
            if path_text and path_text not in next_reads:
                next_reads.append(path_text)
    if next_reads:
        lines.extend(f"- `{_short(path, 120)}`" for path in next_reads[:20])
    else:
        lines.append("- No linked docs, files, or packets recorded yet.")
    lines.append("")
    return "\n".join(lines)


def _session_packet_selection(
    sessions: list[dict[str, Any]],
    topic: str,
    mode: str,
    status: str,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    topic_lower = str(topic or "").strip().lower()
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for session in sessions:
        reasons: list[str] = []
        if topic_lower and topic_lower not in str(session.get("topic") or "").lower():
            reasons.append("topic filter")
        if mode and str(session.get("mode") or "") != mode:
            reasons.append("mode filter")
        if status and status != "all" and str(session.get("status") or "") != status:
            reasons.append("status filter")
        if reasons:
            excluded.append({
                "id": str(session.get("id") or ""),
                "topic": str(session.get("topic") or ""),
                "status": str(session.get("status") or ""),
                "reasons": reasons,
            })
        else:
            selected.append(session)
    return selected[:limit], excluded


def _session_packet_payload(
    project: Path,
    topic: str,
    mode: str,
    status: str,
    limit: int,
) -> dict[str, Any]:
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        sessions = _all_session_payloads(conn)
        selected, excluded = _session_packet_selection(sessions, topic, mode, status, limit)
        citations: list[dict[str, Any]] = []
        edges_used: list[dict[str, Any]] = []
        next_reads: list[str] = []
        warnings: list[str] = []
        for session in selected:
            session_id = str(session.get("id") or "")
            citations.append({
                "id": session_id,
                "type": "session",
                "citation": f"session:{session_id}",
                "topic": str(session.get("topic") or ""),
                "status": str(session.get("status") or ""),
                "summary": str(session.get("summary") or ""),
                "updatedAt": str(session.get("updatedAt") or ""),
                "docs": session.get("docsChanged") or [],
                "files": session.get("filesChanged") or [],
                "commits": session.get("commits") or [],
                "tests": session.get("testsRun") or [],
                "packets": session.get("packets") or [],
                "followups": session.get("followups") or [],
            })
            edges_used.extend(session.get("edges") or [])
            for value in (session.get("docsChanged") or []) + (session.get("filesChanged") or []) + (session.get("packets") or []):
                value_text = str(value or "").strip()
                if value_text and value_text not in next_reads:
                    next_reads.append(value_text)
            if session.get("status") in {"needs_followup", "blocked"}:
                warnings.append(f"Session {session_id} has status {session.get('status')}.")
            if session.get("status") in {"superseded", "archived"}:
                warnings.append(f"Session {session_id} is historical: {session.get('status')}.")
        view_status = _session_view_status(conn)
        if view_status.get("stale"):
            warnings.append("Session views are stale or missing. Run `cc memory session views`.")
        return {
            "ok": True,
            "packetType": "session-graphrag-packet/v1",
            "projectRoot": str(project.resolve()),
            "topic": topic,
            "mode": mode,
            "status": status,
            "limit": limit,
            "generatedAt": _now_iso(),
            "citations": citations,
            "edgesUsed": edges_used,
            "warnings": warnings,
            "excludedRecords": excluded[:50],
            "excludedCount": len(excluded),
            "nextReads": next_reads[:30],
            "sessionViews": view_status,
        }


def _session_packet_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Session GraphRAG Packet",
        "",
        f"> Generated at: {payload['generatedAt']}",
        f"> Topic filter: {payload.get('topic') or '(none)'}",
        f"> Mode filter: {payload.get('mode') or '(none)'}",
        f"> Status filter: {payload.get('status') or 'all'}",
        "",
        "## Warnings",
        "",
    ]
    warnings = payload.get("warnings") or []
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    lines.extend(["", "## Sessions", ""])
    citations = payload.get("citations") or []
    if not citations:
        lines.append("_No matching session records._")
    for citation in citations:
        lines.extend([
            f"### {citation['topic']}",
            "",
            f"- Citation: `{citation['citation']}`",
            f"- Status: `{citation['status']}`",
            f"- Updated: `{citation['updatedAt']}`",
            f"- Summary: {_short(citation['summary'], 220) or '(none)'}",
        ])
        for label, field in (
            ("Docs", "docs"),
            ("Files", "files"),
            ("Commits", "commits"),
            ("Tests", "tests"),
            ("Packets", "packets"),
            ("Follow-ups", "followups"),
        ):
            values = citation.get(field) or []
            if values:
                lines.append(f"- {label}: " + ", ".join(f"`{_short(value, 100)}`" for value in values[:10]))
        lines.append("")
    lines.extend(["## Edges Used", ""])
    edges = payload.get("edgesUsed") or []
    if edges:
        for edge in edges[:80]:
            lines.append(f"- `{edge.get('type')}` from `{edge.get('sessionId')}` to `{edge.get('target')}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Excluded Records", ""])
    excluded = payload.get("excludedRecords") or []
    if excluded:
        for item in excluded:
            lines.append(f"- `{item['id']}`: {', '.join(item.get('reasons') or [])}")
        if int(payload.get("excludedCount") or 0) > len(excluded):
            lines.append(f"- {int(payload.get('excludedCount') or 0) - len(excluded)} more excluded record(s).")
    else:
        lines.append("- none")
    lines.extend(["", "## Next Reads", ""])
    next_reads = payload.get("nextReads") or []
    if next_reads:
        lines.extend(f"- `{_short(item, 140)}`" for item in next_reads)
    else:
        lines.append("- No linked docs, files, or packets recorded.")
    lines.append("")
    return "\n".join(lines)


def _session_view_contents(
    sessions: list[dict[str, Any]],
    generated_at: str,
    command: str,
) -> dict[str, str]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        by_date.setdefault(_date_key(str(session.get("startedAt") or session.get("updatedAt") or "")), []).append(session)
    headers = {
        "SESSION_INDEX.md": "Session Index",
        "SESSION_BY_DATE.md": "Sessions By Date",
        "SESSION_BY_TOPIC.md": "Sessions By Topic",
        "SESSION_BY_CATEGORY.md": "Sessions By Category",
        "SESSION_BY_STATUS.md": "Sessions By Status",
        "SESSION_BY_COMMIT.md": "Sessions By Commit",
        "SESSION_BY_FILE.md": "Sessions By Changed File",
        "SESSION_FOLLOWUPS.md": "Session Follow-ups",
        "SESSION_HANDOFF.md": "Session Handoff Index",
    }
    contents = {
        "SESSION_INDEX.md": (
            _session_view_header(headers["SESSION_INDEX.md"], generated_at, command, sessions)
            + "## Latest Sessions\n\n"
            + _session_table(sessions, limit=50)
            + "\n## Active Sessions\n\n"
            + _session_table([session for session in sessions if session.get("status") == "active"], limit=50)
        ),
        "SESSION_BY_DATE.md": (
            _session_view_header(headers["SESSION_BY_DATE.md"], generated_at, command, sessions)
            + _grouped_session_sections(by_date)
        ),
        "SESSION_BY_TOPIC.md": (
            _session_view_header(headers["SESSION_BY_TOPIC.md"], generated_at, command, sessions)
            + _grouped_session_sections(_group_by_text(sessions, "topic"))
        ),
        "SESSION_BY_CATEGORY.md": (
            _session_view_header(headers["SESSION_BY_CATEGORY.md"], generated_at, command, sessions)
            + _grouped_session_sections(_group_by_list_field(sessions, "categories"))
        ),
        "SESSION_BY_STATUS.md": (
            _session_view_header(headers["SESSION_BY_STATUS.md"], generated_at, command, sessions)
            + _grouped_session_sections(_group_by_text(sessions, "status"))
        ),
        "SESSION_BY_COMMIT.md": (
            _session_view_header(headers["SESSION_BY_COMMIT.md"], generated_at, command, sessions)
            + _grouped_session_sections(_group_by_list_field(sessions, "commits"))
        ),
        "SESSION_BY_FILE.md": (
            _session_view_header(headers["SESSION_BY_FILE.md"], generated_at, command, sessions)
            + _grouped_session_sections(_group_by_list_field(sessions, "filesChanged"))
        ),
        "SESSION_FOLLOWUPS.md": (
            _session_view_header(headers["SESSION_FOLLOWUPS.md"], generated_at, command, sessions)
            + _session_followup_view(sessions)
        ),
        "SESSION_HANDOFF.md": (
            _session_view_header(headers["SESSION_HANDOFF.md"], generated_at, command, sessions)
            + _session_handoff_view(sessions)
        ),
    }
    return contents


def _session_view_status(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT MAX(updated_at) AS latest_updated_at, COUNT(*) AS count FROM session_records"
    ).fetchone()
    latest_updated_at = str(row["latest_updated_at"] or "") if row else ""
    session_count = int(row["count"] or 0) if row else 0
    generated_row = conn.execute(
        """
        SELECT MIN(generated_at) AS oldest_generated_at, COUNT(*) AS generated_count
        FROM views
        WHERE path IN ({})
        """.format(",".join("?" for _item in SESSION_VIEW_FILENAMES)),
        tuple(f"{CONTROL_DIRNAME}/{VIEWS_DIRNAME}/{filename}" for filename in SESSION_VIEW_FILENAMES),
    ).fetchone()
    oldest_generated_at = str(generated_row["oldest_generated_at"] or "") if generated_row else ""
    generated_count = int(generated_row["generated_count"] or 0) if generated_row else 0
    stale = bool(
        session_count
        and (generated_count < len(SESSION_VIEW_FILENAMES) or not oldest_generated_at or latest_updated_at > oldest_generated_at)
    )
    return {
        "implemented": True,
        "requiredViewCount": len(SESSION_VIEW_FILENAMES),
        "generatedViewCount": generated_count,
        "latestSessionUpdatedAt": latest_updated_at,
        "oldestGeneratedAt": oldest_generated_at,
        "stale": stale,
        "paths": [f"{CONTROL_DIRNAME}/{VIEWS_DIRNAME}/{filename}" for filename in SESSION_VIEW_FILENAMES],
    }


def session_status_payload(project: Path) -> dict[str, Any]:
    ok, message = _require_initialized(project)
    if not ok:
        return {
            "available": False,
            "message": message,
            "sessionCount": 0,
            "activeCount": 0,
            "latestSession": {},
            "openFollowups": [],
            "views": {"implemented": True, "stale": False},
        }
    with _readonly_memory_connection(project) as conn:
        inspection = inspect_schema(conn)
        if not inspection.has_queryable_current_shape:
            return {
                "available": False,
                "message": f"memory schema is not queryable: {inspection.state}",
                "schema": inspection.to_payload(),
                "sessionCount": 0,
                "activeCount": 0,
                "latestSession": {},
                "openFollowups": [],
                "views": {"implemented": True, "stale": False},
            }
        sessions = _all_session_payloads(conn)
        open_followups = [
            {
                "sessionId": str(session.get("id") or ""),
                "topic": str(session.get("topic") or ""),
                "status": str(session.get("status") or ""),
                "text": str(followup or ""),
            }
            for session in sessions
            for followup in (session.get("followups") or [])
        ]
        return {
            "available": True,
            "message": "",
            "sessionCount": len(sessions),
            "activeCount": sum(1 for session in sessions if session.get("status") == "active"),
            "latestSession": sessions[0] if sessions else {},
            "openFollowups": open_followups,
            "views": _session_view_status(conn),
        }


def cmd_memory_session_start(
    project: Path,
    topic: str,
    mode: str = "continue_previous_work",
    scope: str = "dev",
    session_id: str = "",
    categories: list[str] | None = None,
    summary: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    topic = str(topic or "").strip()
    if not topic:
        _print_json_error_or_text(json_output, "session_topic_required", "session topic is required")
        return 1
    if mode not in VALID_SESSION_MODES:
        _print_json_error_or_text(json_output, "session_mode_invalid", f"invalid session mode: {mode}")
        return 1
    now = _now_iso()
    session_id = str(session_id or "").strip() or _make_session_id(topic, now)
    category_values = _normalize_text_list(categories)
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        if _session_row(conn, session_id) is not None:
            _print_json_error_or_text(json_output, "session_already_exists", f"session record already exists: {session_id}")
            return 1
        conn.execute(
            """
            INSERT INTO session_records(
              id, schema_version, started_at, ended_at, mode, scope, topic,
              status, summary, categories, files_changed, docs_changed,
              commands_run, tests_run, commits, packets, decisions, followups,
              links, data, created_at, updated_at
            )
            VALUES (?, ?, ?, '', ?, ?, ?, 'active', ?, ?, '[]', '[]', '[]',
                    '[]', '[]', '[]', '[]', '[]', '[]', '{}', ?, ?)
            """,
            (
                session_id,
                SESSION_RECORD_SCHEMA_VERSION,
                now,
                mode,
                str(scope or "dev").strip() or "dev",
                topic,
                str(summary or "").strip(),
                _json_dumps(category_values),
                now,
                now,
            ),
        )
        row = _session_row(conn, session_id)
        payload = _session_payload(row) if row else {}
        _insert_event(
            conn,
            project,
            "create",
            "cc memory session start",
            target_entity_ids=[session_id],
            after_state=payload,
            provenance={"source": "cc memory session start"},
        )
    text = "\n".join(
        [
            f"Session started: {session_id}",
            f"  Topic: {topic}",
            f"  Mode: {mode}",
            f"  Scope: {scope}",
        ]
    )
    _print_json_or_text(json_output, {"ok": True, "session": payload}, text)
    return 0


def cmd_memory_session_close(
    project: Path,
    session_id: str,
    status: str = "completed",
    summary: str = "",
    followups: list[str] | None = None,
    decisions: list[str] | None = None,
    commits: list[str] | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    session_id = str(session_id or "").strip()
    if status not in VALID_SESSION_STATUSES or status == "active":
        _print_json_error_or_text(json_output, "session_close_status_invalid", f"invalid close status: {status}")
        return 1
    now = _now_iso()
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        row = _session_row(conn, session_id)
        if row is None:
            return _print_missing_session(session_id, json_output)
        before = _session_payload(row, _session_edges(conn, session_id))
        lists = _load_session_lists(row)
        for value in _normalize_text_list(followups):
            lists["followups"] = _append_unique_text(lists["followups"], value)
        for value in _normalize_text_list(decisions):
            lists["decisions"] = _append_unique_text(lists["decisions"], value)
        for value in _normalize_text_list(commits):
            lists["commits"] = _append_unique_text(lists["commits"], value)
        _update_session_lists(conn, session_id, lists, now)
        conn.execute(
            """
            UPDATE session_records
            SET ended_at = ?, status = ?, summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                now,
                status,
                str(summary or row["summary"] or "").strip(),
                now,
                session_id,
            ),
        )
        updated = _session_row(conn, session_id)
        payload = _session_payload(updated, _session_edges(conn, session_id)) if updated else {}
        _insert_event(
            conn,
            project,
            "edit",
            "cc memory session close",
            target_entity_ids=[session_id],
            before_state=before,
            after_state=payload,
            provenance={"source": "cc memory session close"},
        )
    text = "\n".join(
        [
            f"Session closed: {session_id}",
            f"  Status: {status}",
            f"  Summary: {payload.get('summary', '')}",
        ]
    )
    _print_json_or_text(json_output, {"ok": True, "session": payload}, text)
    return 0


def cmd_memory_session_list(
    project: Path,
    status: str = "all",
    topic: str = "",
    limit: int = 20,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    if status != "all" and status not in VALID_SESSION_STATUSES:
        _print_json_error_or_text(json_output, "session_status_invalid", f"invalid session status: {status}")
        return 1
    limit = max(1, min(int(limit or 20), 200))
    clauses: list[str] = []
    params: list[Any] = []
    if status != "all":
        clauses.append("status = ?")
        params.append(status)
    topic = str(topic or "").strip()
    if topic:
        clauses.append("LOWER(topic) LIKE ?")
        params.append(f"%{topic.lower()}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT * FROM session_records {where} ORDER BY {_SESSION_ORDER_BY} LIMIT ?",
            (*params, limit),
        ).fetchall()
        sessions = [_session_payload(row) for row in rows]
    lines = ["Session records"]
    if not sessions:
        lines.append("  none")
    else:
        for session in sessions:
            lines.append(
                f"  {session['id']} [{session['status']}] {session['topic']} "
                f"({session['updatedAt']})"
            )
    _print_json_or_text(
        json_output,
        {"ok": True, "sessions": sessions, "count": len(sessions)},
        "\n".join(lines),
    )
    return 0


def cmd_memory_session_show(project: Path, session_id: str, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    session_id = str(session_id or "").strip()
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        row = _session_row(conn, session_id)
        if row is None:
            return _print_missing_session(session_id, json_output)
        payload = _session_payload(row, _session_edges(conn, session_id))
    lines = [
        f"Session record: {payload['id']}",
        f"  Status: {payload['status']}",
        f"  Topic: {payload['topic']}",
        f"  Mode: {payload['mode']}",
        f"  Scope: {payload['scope']}",
        f"  Started: {payload['startedAt']}",
        f"  Ended: {payload['endedAt'] or 'open'}",
    ]
    if payload["summary"]:
        lines.append(f"  Summary: {payload['summary']}")
    if payload["edges"]:
        lines.append("  Links:")
        lines.extend(
            f"    {edge['type']} -> {edge['target']}"
            for edge in payload["edges"]
        )
    else:
        lines.append("  Links: none")
    _print_json_or_text(json_output, {"ok": True, "session": payload}, "\n".join(lines))
    return 0


def cmd_memory_session_link(
    project: Path,
    session_id: str,
    link_type: str,
    target: str,
    target_type: str = "",
    data: dict[str, Any] | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    session_id = str(session_id or "").strip()
    link_type = str(link_type or "").strip()
    target = str(target or "").strip()
    if link_type not in SESSION_EDGE_TYPES:
        _print_json_error_or_text(json_output, "session_link_type_invalid", f"invalid session link type: {link_type}")
        return 1
    if not target:
        _print_json_error_or_text(json_output, "session_link_target_required", "session link target is required")
        return 1
    now = _now_iso()
    edge = {
        "id": _edge_id(session_id, link_type, target),
        "sessionId": session_id,
        "type": link_type,
        "target": target,
        "targetType": str(target_type or "").strip(),
        "data": data or {},
        "createdAt": now,
    }
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        row = _session_row(conn, session_id)
        if row is None:
            return _print_missing_session(session_id, json_output)
        before = _session_payload(row, _session_edges(conn, session_id))
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO session_edges(id, session_id, type, target, target_type, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge["id"],
                session_id,
                link_type,
                target,
                edge["targetType"],
                _json_dumps(edge["data"]),
                now,
            ),
        )
        lists = _load_session_lists(row)
        field = _SESSION_FIELD_BY_EDGE_TYPE.get(link_type)
        if field:
            lists[field] = _append_unique_text(lists[field], target)
        link_record = {
            "type": link_type,
            "target": target,
            "targetType": edge["targetType"],
            "createdAt": now,
        }
        lists["links"] = _append_unique_link(lists["links"], link_record)
        _update_session_lists(conn, session_id, lists, now)
        updated = _session_row(conn, session_id)
        payload = _session_payload(updated, _session_edges(conn, session_id)) if updated else {}
        _insert_event(
            conn,
            project,
            "link",
            "cc memory session link",
            target_entity_ids=[session_id],
            before_state=before,
            after_state={"session": payload, "edge": edge, "created": bool(cursor.rowcount)},
            provenance={"source": "cc memory session link"},
        )
    text = "\n".join(
        [
            f"Session link {'created' if cursor.rowcount else 'already present'}: {session_id}",
            f"  Type: {link_type}",
            f"  Target: {target}",
        ]
    )
    _print_json_or_text(
        json_output,
        {"ok": True, "created": bool(cursor.rowcount), "session": payload, "edge": edge},
        text,
    )
    return 0


def cmd_memory_session_note(
    project: Path,
    session_id: str,
    text: str,
    kind: str = "note",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    session_id = str(session_id or "").strip()
    text = str(text or "").strip()
    kind = str(kind or "note").strip()
    if kind not in {"note", "decision", "followup"}:
        _print_json_error_or_text(json_output, "session_note_kind_invalid", f"invalid session note kind: {kind}")
        return 1
    if not text:
        _print_json_error_or_text(json_output, "session_note_text_required", "session note text is required")
        return 1
    now = _now_iso()
    note = {"kind": kind, "text": text, "createdAt": now}
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        row = _session_row(conn, session_id)
        if row is None:
            return _print_missing_session(session_id, json_output)
        before = _session_payload(row, _session_edges(conn, session_id))
        data = _json_dict(str(row["data"] or "{}"))
        notes = data.get("notes")
        data["notes"] = notes if isinstance(notes, list) else []
        data["notes"].append(note)
        lists = _load_session_lists(row)
        if kind == "decision":
            lists["decisions"] = _append_unique_text(lists["decisions"], text)
        if kind == "followup":
            lists["followups"] = _append_unique_text(lists["followups"], text)
        _update_session_lists(conn, session_id, lists, now)
        conn.execute(
            "UPDATE session_records SET data = ?, updated_at = ? WHERE id = ?",
            (_json_dumps(data), now, session_id),
        )
        updated = _session_row(conn, session_id)
        payload = _session_payload(updated, _session_edges(conn, session_id)) if updated else {}
        _insert_event(
            conn,
            project,
            "edit",
            "cc memory session note",
            target_entity_ids=[session_id],
            before_state=before,
            after_state={"session": payload, "note": note},
            provenance={"source": "cc memory session note"},
        )
    _print_json_or_text(
        json_output,
        {"ok": True, "session": payload, "note": note},
        f"Session note recorded: {session_id}\n  Kind: {kind}",
    )
    return 0


def cmd_memory_session_views(project: Path, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    command = "cc memory session views"
    generated_at = _now_iso()
    generated: list[str] = []
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        sessions = _all_session_payloads(conn)
        contents = _session_view_contents(sessions, generated_at, command)
        source_counts = {
            "sessions": len(sessions),
            "active": sum(1 for session in sessions if session.get("status") == "active"),
            "openFollowups": sum(len(session.get("followups") or []) for session in sessions),
        }
        for filename, text in contents.items():
            path = _views_dir(project) / filename
            _transactional_write_text(conn, path, text)
            rel_path = f"{CONTROL_DIRNAME}/{VIEWS_DIRNAME}/{filename}"
            conn.execute(
                """
                INSERT OR REPLACE INTO views(path, title, generated_at, command, source_counts, stale_warning, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel_path,
                    filename.removesuffix(".md").replace("_", " ").title(),
                    generated_at,
                    command,
                    _json_dumps(source_counts),
                    "no",
                    _content_hash_text(text),
                ),
            )
            generated.append(rel_path)
        _set_metadata(conn, "last_session_views_generated_at", generated_at)
        _insert_event(
            conn,
            project,
            "generate_view",
            command,
            affected_paths=generated,
            after_state={"generated_views": generated, **source_counts},
            reason="Generated markdown views from explicit session records",
        )
        payload = {
            "ok": True,
            "generated": generated,
            "generatedAt": generated_at,
            "sessionCount": source_counts["sessions"],
            "activeCount": source_counts["active"],
            "openFollowupCount": source_counts["openFollowups"],
            "views": _session_view_status(conn),
        }
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print("Generated session views:")
        for rel_path in generated:
            print(f"  - {rel_path}")
    return 0


def cmd_memory_session_pack(
    project: Path,
    topic: str = "",
    mode: str = "",
    status: str = "all",
    limit: int = 10,
    output: Path | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_session_memory_not_initialized", message)
        return 1
    mode = str(mode or "").strip()
    if mode and mode not in VALID_SESSION_MODES:
        _print_json_error_or_text(json_output, "session_mode_invalid", f"invalid session mode: {mode}")
        return 1
    status = str(status or "all").strip()
    if status != "all" and status not in VALID_SESSION_STATUSES:
        _print_json_error_or_text(json_output, "session_status_invalid", f"invalid session status: {status}")
        return 1
    limit = max(1, min(int(limit or 10), 50))
    payload = _session_packet_payload(project, topic=topic, mode=mode, status=status, limit=limit)
    markdown = _session_packet_markdown(payload)
    payload["markdown"] = markdown
    if output is not None:
        try:
            output_path = _resolve_output_path(project, output)
        except ValueError as exc:
            _print_json_error_or_text(json_output, "session_pack_output_invalid", str(exc))
            return 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        payload["outputPath"] = _relative_path(project, output_path)
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(markdown)
    return 0


__all__ = [
    "cmd_memory_session_close",
    "cmd_memory_session_link",
    "cmd_memory_session_list",
    "cmd_memory_session_note",
    "cmd_memory_session_pack",
    "cmd_memory_session_show",
    "cmd_memory_session_start",
    "cmd_memory_session_views",
    "session_status_payload",
]
