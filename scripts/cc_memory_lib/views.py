"""Markdown view and sync-report generation for project memory."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .entities import _row_to_dict
from .impact import _count_events, _counts_by
from .ledger import _insert_event
from .lifecycle import LIFECYCLE_ATTENTION_STATES
from .scanner import _area_from_relpath
from .schema import CONTROL_DIRNAME, VALID_LIFECYCLES, VIEWS_DIRNAME
from .store import (
    _content_hash_text,
    _ensure_schema,
    _get_metadata,
    _json_dumps,
    _memory_connection,
    _now_iso,
    _print_json_error_or_text,
    _require_initialized,
    _set_metadata,
    _transactional_write_text,
    _views_dir,
)

def _markdown_header(
    title: str,
    generated_at: str,
    command: str,
    counts: dict[str, int],
    stale_count: int,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"> Generated at: {generated_at}",
        f"> Generation command: `{command}`",
        f"> Source record counts: {sum(counts.values())} entities",
        f"> Stale warning: {'yes' if stale_count else 'no'}",
        "",
    ]
    return "\n".join(lines)

def _short(value: str, limit: int = 90) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."

def _entity_table(entities: list[dict[str, Any]], limit: int = 50) -> str:
    if not entities:
        return "_No records._\n"
    lines = [
        "| ID | Type | Lifecycle | Title | Path |",
        "|---|---|---|---|---|",
    ]
    for entity in entities[:limit]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_short(entity.get('id'), 70)}`",
                    _short(entity.get("type"), 24),
                    _short(entity.get("lifecycle"), 24),
                    _short(entity.get("title"), 80),
                    f"`{_short(entity.get('path'), 80)}`" if entity.get("path") else "",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"

def _recent_entities(conn: sqlite3.Connection, entity_type: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM entities WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
        (entity_type, limit),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]

def _all_entities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM entities ORDER BY type, title").fetchall()
    return [_row_to_dict(row) for row in rows]

def _entity_path(entity: dict[str, Any]) -> str:
    return str(entity.get("path") or "").replace("\\", "/")


def _entity_body(entity: dict[str, Any]) -> str:
    return str(entity.get("body") or "")


def _entities_under(entities: list[dict[str, Any]], *prefixes: str) -> list[dict[str, Any]]:
    normalized = tuple(prefix.lower().rstrip("/") + "/" for prefix in prefixes)
    return [
        entity
        for entity in entities
        if _entity_path(entity).lower().startswith(normalized)
    ]


def _work_source_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entity in entities:
        path = _entity_path(entity).lower()
        data = entity.get("data") if isinstance(entity.get("data"), dict) else {}
        source_type = str(data.get("source_type") or "").strip().lower()
        refs = entity.get("source_refs")
        has_refs = isinstance(refs, list) and bool(refs)
        if (
            path.startswith(("docs/sources/", "docs/extracts/"))
            or source_type not in {"", "other"}
            or has_refs
        ):
            result.append(entity)
    return result


def _open_question_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_lifecycles = {"captured", "draft", "triaged", "needs_review", "conflicting"}
    result: list[dict[str, Any]] = []
    for entity in entities:
        lifecycle = str(entity.get("lifecycle") or "")
        path = _entity_path(entity).lower()
        text = f"{entity.get('title') or ''}\n{_entity_body(entity)}"
        if lifecycle in open_lifecycles and ("?" in text or path.startswith("docs/inbox/")):
            result.append(entity)
    return result

def _generated_view_contents(
    project: Path,
    command: str,
    generated_at: str,
    connection: sqlite3.Connection | None = None,
) -> dict[str, str]:
    context = nullcontext(connection) if connection is not None else _memory_connection(project)
    with context as conn:
        _ensure_schema(conn)
        entities = _all_entities(conn)
        counts = _counts_by(conn, "type")
        lifecycle_counts = _counts_by(conn, "lifecycle")
        stale_entities = [
            entity
            for entity in entities
            if entity.get("lifecycle") in LIFECYCLE_ATTENTION_STATES
        ]
        stale_count = len(stale_entities)
        header_args = (generated_at, command, counts, stale_count)
        plans = [entity for entity in entities if entity.get("type") == "plan"]
        active = [entity for entity in entities if entity.get("lifecycle") in {"active", "implemented", "verified"}]
        legacy = [
            entity
            for entity in entities
            if entity.get("lifecycle") in {"archived", "superseded", "stale"}
            or any(part in str(entity.get("path") or "").lower() for part in ("archive", "legacy", "deprecated", "old"))
        ]
        ideas = [entity for entity in entities if entity.get("type") == "idea"]
        decisions = _recent_entities(conn, "decision")
        consults = _recent_entities(conn, "consult")
        agent_runs = _recent_entities(conn, "agent_run")
        notes = _recent_entities(conn, "note")
        source_entities = _work_source_entities(entities)
        open_questions = _open_question_entities(entities)
        active_decisions = [
            entity
            for entity in decisions
            if entity.get("lifecycle") in {"active", "implemented", "verified", "triaged"}
        ]
        inbox_items = _entities_under(entities, "docs/inbox")
        research_items = _entities_under(entities, "docs/research")
        output_items = _entities_under(entities, "docs/outputs")
        chunk_count = int(conn.execute("SELECT COUNT(*) AS count FROM semantic_chunks").fetchone()["count"])
        layout_count = int(conn.execute("SELECT COUNT(*) AS count FROM document_layout_nodes").fetchone()["count"])
        vector_count = int(conn.execute("SELECT COUNT(*) AS count FROM derived_vector_index").fetchone()["count"])
        correlation_status_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM correlation_suggestions
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        correlation_counts = {
            str(row["status"]): int(row["count"])
            for row in correlation_status_rows
        }
        correlation_count = int(correlation_counts.get("suggested", 0))
        vector_rows = conn.execute(
            """
            SELECT source_path, COUNT(*) AS count
            FROM derived_vector_index
            GROUP BY source_path
            ORDER BY count DESC, source_path
            LIMIT 25
            """
        ).fetchall()
        chunk_rows = conn.execute(
            """
            SELECT source_path, COUNT(*) AS count
            FROM semantic_chunks
            GROUP BY source_path
            ORDER BY count DESC, source_path
            LIMIT 25
            """
        ).fetchall()
        layout_type_rows = conn.execute(
            """
            SELECT node_type, COUNT(*) AS count
            FROM document_layout_nodes
            GROUP BY node_type
            ORDER BY node_type
            """
        ).fetchall()
        layout_rows = conn.execute(
            """
            SELECT source_path, COUNT(*) AS count
            FROM document_layout_nodes
            GROUP BY source_path
            ORDER BY count DESC, source_path
            LIMIT 25
            """
        ).fetchall()
        correlation_rows = conn.execute(
            """
            SELECT id, source_id, target_id, type, confidence, reason, status
            FROM correlation_suggestions
            WHERE status IN ('suggested', 'accepted', 'rejected', 'confirmed')
            ORDER BY status, confidence, type, source_id
            LIMIT 50
            """
        ).fetchall()
        last_scan = _get_metadata(conn, "last_scan_at")
        last_impact_query = _get_metadata(conn, "last_impact_query")
        last_vector_rebuild = _get_metadata(conn, "last_vector_rebuild_at")
        last_vector_adapter = _get_metadata(conn, "last_vector_adapter")

    status_lines = [
        _markdown_header("Project Memory Status", *header_args),
        "## Summary",
        "",
        f"- Total entities: {len(entities)}",
        f"- Semantic chunks: {chunk_count}",
        f"- Document layout nodes: {layout_count}",
        f"- Vector index entries: {vector_count}",
        f"- Correlation suggestions: {correlation_count}",
        f"- Lifecycle attention: {stale_count}",
        f"- Last scan: {last_scan or 'never'}",
        "",
        "## By Type",
        "",
    ]
    status_lines.extend(f"- {key}: {value}" for key, value in counts.items())
    status_lines.extend(["", "## By Lifecycle", ""])
    status_lines.extend(f"- {key}: {value}" for key, value in lifecycle_counts.items())

    roadmap = [
        _markdown_header("Project Memory Roadmap", *header_args),
        "## Active Plans",
        "",
        _entity_table([entity for entity in plans if entity.get("lifecycle") in {"active", "triaged", "draft"}]),
        "## Idea Inbox",
        "",
        _entity_table([entity for entity in ideas if entity.get("lifecycle") in {"captured", "triaged"}]),
    ]

    handoff = [
        _markdown_header("Project Memory Handoff", *header_args),
        "## Active Context",
        "",
        _entity_table(active[:25]),
        "## Recent Decisions",
        "",
        _entity_table(decisions),
        "## Recent Consults",
        "",
        _entity_table(consults),
        "## Recent Agent Runs",
        "",
        _entity_table(agent_runs),
        "## Needs Review",
        "",
        _entity_table(stale_entities),
    ]

    work_context = [
        _markdown_header("Work Context", *header_args),
        f"Last impact query: `{last_impact_query or 'none'}`",
        "",
        "## Decisions",
        "",
        _entity_table(decisions),
        "## Notes",
        "",
        _entity_table(notes),
        "## Stale Or Needs Review",
        "",
        _entity_table(stale_entities),
    ]

    work_handoff = [
        _markdown_header("Work Memory Handoff", *header_args),
        "## Inbox To Triage",
        "",
        _entity_table(inbox_items),
        "## Active Decisions",
        "",
        _entity_table(active_decisions),
        "## Open Questions",
        "",
        _entity_table(open_questions),
        "## Research",
        "",
        _entity_table(research_items),
        "## Outputs",
        "",
        _entity_table(output_items),
        "## Legacy Or Needs Review",
        "",
        _entity_table(stale_entities),
    ]

    source_ledger = [
        _markdown_header("Source Ledger", *header_args),
        "Sources, extracts, and imported references are evidence inputs. Decisions and maintained notes remain canonical memory.",
        "",
        _entity_table(source_entities, limit=100),
    ]

    open_question_lines = [
        _markdown_header("Open Questions", *header_args),
        "Records listed here still need triage, resolution, or conversion into a decision.",
        "",
        _entity_table(open_questions, limit=100),
    ]

    active_decision_lines = [
        _markdown_header("Active Decisions", *header_args),
        "Active, implemented, verified, and triaged decisions that currently shape the work context.",
        "",
        _entity_table(active_decisions, limit=100),
    ]

    by_area: dict[str, dict[str, int]] = {}
    for entity in entities:
        data = entity.get("data", {}) if isinstance(entity.get("data"), dict) else {}
        area = str(data.get("area") or _area_from_relpath(str(entity.get("path") or "")))
        by_area.setdefault(area, {})
        entity_type = str(entity.get("type"))
        by_area[area][entity_type] = by_area[area].get(entity_type, 0) + 1
    impact_lines = [
        _markdown_header("Impact Map", *header_args),
        "| Area | Entity Counts |",
        "|---|---|",
    ]
    for area in sorted(by_area):
        counts_text = ", ".join(f"{key}: {value}" for key, value in sorted(by_area[area].items()))
        impact_lines.append(f"| {area} | {counts_text} |")

    lifecycle_lines = [_markdown_header("Lifecycle Index", *header_args)]
    for lifecycle in sorted(VALID_LIFECYCLES):
        lifecycle_items = [entity for entity in entities if entity.get("lifecycle") == lifecycle]
        lifecycle_lines.extend([f"## {lifecycle}", "", _entity_table(lifecycle_items), ""])

    graph_lines = [
        _markdown_header("Memory Graph Index", *header_args),
        "## Summary",
        "",
        f"- Semantic chunks: {chunk_count}",
        f"- Document layout nodes: {layout_count}",
        f"- Vector index entries: {vector_count}",
        f"- Suggested correlations: {correlation_counts.get('suggested', 0)}",
        f"- Accepted correlations: {correlation_counts.get('accepted', 0)}",
        f"- Rejected correlations: {correlation_counts.get('rejected', 0)}",
        f"- Confirmed correlations: {correlation_counts.get('confirmed', 0)}",
        "",
        "## Chunked Documents",
        "",
        "| Path | Chunks |",
        "|---|---|",
    ]
    if chunk_rows:
        graph_lines.extend(f"| `{row['source_path']}` | {row['count']} |" for row in chunk_rows)
    else:
        graph_lines.append("| _No chunked documents._ | 0 |")
    graph_lines.extend(["", "## Document Layout Nodes", "", "| Type | Nodes |", "|---|---|"])
    if layout_type_rows:
        graph_lines.extend(f"| {row['node_type']} | {row['count']} |" for row in layout_type_rows)
    else:
        graph_lines.append("| _No layout nodes._ | 0 |")
    graph_lines.extend(["", "## Layout Documents", "", "| Path | Layout Nodes |", "|---|---|"])
    if layout_rows:
        graph_lines.extend(f"| `{row['source_path']}` | {row['count']} |" for row in layout_rows)
    else:
        graph_lines.append("| _No layout documents._ | 0 |")
    graph_lines.extend(["", "## Correlation Suggestions", "", "| ID | Status | Source | Target | Type | Confidence | Reason |", "|---|---|---|---|---|---|---|"])
    if correlation_rows:
        for row in correlation_rows:
            graph_lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{_short(row['id'], 64)}`",
                        _short(row["status"], 16),
                        f"`{_short(row['source_id'], 64)}`",
                        f"`{_short(row['target_id'], 64)}`",
                        _short(row["type"], 24),
                        _short(row["confidence"], 32),
                        _short(row["reason"], 90),
                    ]
                )
                + " |"
            )
    else:
        graph_lines.append("| _No suggestions._ |  |  |  |  |  |  |")

    vector_lines = [
        _markdown_header("Memory Vector Index", *header_args),
        "## Summary",
        "",
        f"- Adapter: `{last_vector_adapter or 'none'}`",
        f"- Entries: {vector_count}",
        f"- Last rebuild: {last_vector_rebuild or 'never'}",
        "",
        "The vector index is a derived local sparse index rebuilt from semantic chunks. It is not canonical memory.",
        "",
        "## Indexed Documents",
        "",
        "| Path | Entries |",
        "|---|---|",
    ]
    if vector_rows:
        vector_lines.extend(f"| `{row['source_path']}` | {row['count']} |" for row in vector_rows)
    else:
        vector_lines.append("| _No indexed documents._ | 0 |")

    return {
        "STATUS.md": "\n".join(status_lines).rstrip() + "\n",
        "ROADMAP.md": "\n".join(roadmap).rstrip() + "\n",
        "HANDOFF.md": "\n".join(handoff).rstrip() + "\n",
        "WORK_CONTEXT.md": "\n".join(work_context).rstrip() + "\n",
        "WORK_HANDOFF.md": "\n".join(work_handoff).rstrip() + "\n",
        "SOURCE_LEDGER.md": "\n".join(source_ledger).rstrip() + "\n",
        "OPEN_QUESTIONS.md": "\n".join(open_question_lines).rstrip() + "\n",
        "ACTIVE_DECISIONS.md": "\n".join(active_decision_lines).rstrip() + "\n",
        "IMPACT_MAP.md": "\n".join(impact_lines).rstrip() + "\n",
        "LIFECYCLE_INDEX.md": "\n".join(lifecycle_lines).rstrip() + "\n",
        "LEGACY_INDEX.md": (
            _markdown_header("Legacy Index", *header_args)
            + "Records here are archived, superseded, stale, or located under legacy/archive/deprecated paths.\n\n"
            + _entity_table(legacy)
        ),
        "STALE_INDEX.md": (
            _markdown_header("Stale Index", *header_args)
            + _entity_table(stale_entities)
        ),
        "CONSULT_LEDGER.md": (
            _markdown_header("Consult Ledger", *header_args)
            + "Consults inform project memory but do not own canonical truth.\n\n"
            + _entity_table(consults, limit=100)
        ),
        "AGENT_RUN_LEDGER.md": (
            _markdown_header("Agent Run Ledger", *header_args)
            + "Agent runs inform project memory but do not own canonical truth.\n\n"
            + _entity_table(agent_runs, limit=100)
        ),
        "IDEA_INBOX.md": (
            _markdown_header("Idea Inbox", *header_args)
            + _entity_table(ideas, limit=100)
        ),
        "GRAPH_INDEX.md": "\n".join(graph_lines).rstrip() + "\n",
        "VECTOR_INDEX.md": "\n".join(vector_lines).rstrip() + "\n",
    }

def _generate_views_in_transaction(
    project: Path,
    conn: sqlite3.Connection,
    command: str = "cc memory views generate",
) -> dict[str, Any]:
    generated_at = _now_iso()
    contents = _generated_view_contents(project, command, generated_at, connection=conn)
    generated: list[str] = []
    _ensure_schema(conn)
    counts = _counts_by(conn, "type")
    attention_states = tuple(sorted(LIFECYCLE_ATTENTION_STATES))
    placeholders = ",".join("?" for _item in attention_states)
    stale_count = conn.execute(
        f"SELECT COUNT(*) AS count FROM entities WHERE lifecycle IN ({placeholders})",
        attention_states,
    ).fetchone()["count"]
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
                _json_dumps(counts),
                "yes" if stale_count else "no",
                _content_hash_text(text),
            ),
        )
        generated.append(rel_path)
    _set_metadata(conn, "last_views_generated_at", generated_at)
    _insert_event(
        conn,
        project,
        "generate_view",
        command,
        affected_paths=generated,
        after_state={"generated_views": generated, "stale_count": int(stale_count)},
        reason="Generated markdown views from canonical memory records",
    )
    return {"generated": generated, "generatedAt": generated_at}


def _generate_views(project: Path, command: str = "cc memory views generate", quiet: bool = False) -> dict[str, Any]:
    ok, message = _require_initialized(project)
    if not ok:
        raise RuntimeError(message)
    with _memory_connection(project) as conn:
        result = _generate_views_in_transaction(project, conn, command=command)
    if not quiet:
        print("Generated memory views:")
        for rel_path in result["generated"]:
            print(f"  - {rel_path}")
    return result

def cmd_memory_views_generate(project: Path, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_views_memory_not_initialized", message)
        return 1
    result = _generate_views(project, quiet=json_output)
    if json_output:
        print(json.dumps({"ok": True, **result}, indent=2))
    return 0

def _build_sync_report(project: Path) -> str:
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        counts = _counts_by(conn, "type")
        lifecycle_counts = _counts_by(conn, "lifecycle")
        docs_updated = conn.execute(
            "SELECT COUNT(*) AS count FROM entities WHERE path IS NOT NULL AND path != ''"
        ).fetchone()["count"]
        docs_stale = conn.execute(
            "SELECT COUNT(*) AS count FROM entities WHERE type IN ('doc_node', 'plan', 'research_note', 'handoff') AND lifecycle IN ('stale', 'conflicting', 'needs_review')"
        ).fetchone()["count"]
        new_decisions = int(counts.get("decision", 0))
        consults = int(counts.get("consult", 0))
        agent_runs = int(counts.get("agent_run", 0))
        lifecycle_changes = (
            _count_events(conn, "mark_conflicting")
            + _count_events(conn, "mark_legacy")
            + _count_events(conn, "mark_stale")
            + _count_events(conn, "resolve_conflict")
            + _count_events(conn, "resolve_stale")
            + _count_events(conn, "supersede")
            + _count_events(conn, "archive")
        )
        open_gaps = int(lifecycle_counts.get("needs_review", 0)) + int(lifecycle_counts.get("conflicting", 0))
        impact_checked = bool(_get_metadata(conn, "last_impact_checked_at"))
        views_generated = bool(_get_metadata(conn, "last_views_generated_at"))
        last_scan = _get_metadata(conn, "last_scan_at")
        app_memory_components = int(counts.get("application_memory_component", 0))

    lines = [
        "# Memory Sync Report",
        "",
        f"> Generated at: {_now_iso()}",
        "> Generation command: `cc memory sync-report`",
        "",
        "## Closure Summary",
        "",
        f"- Indexed path-backed records: {docs_updated}",
        f"- Stale documents: {docs_stale}",
        f"- Decisions recorded: {new_decisions}",
        f"- Consults recorded: {consults}",
        f"- Agent runs recorded: {agent_runs}",
        f"- Lifecycle changes: {lifecycle_changes}",
        f"- Open knowledge gaps: {open_gaps}",
        f"- Last scan: {last_scan or 'never'}",
        f"- Application memory components referenced: {app_memory_components}",
        "",
        "## Boundary Confirmation",
        "",
        "ControlCoding memory remains under `.controlcoding/`. Application-owned runtime or domain memory is referenced only as project context and is not canonical ControlCoding truth.",
        "",
        "## Commit Ceremony Block",
        "",
        "Memory sync:",
        f"- Impact context checked: {'yes' if impact_checked else 'no'}",
        f"- New decisions recorded: {new_decisions}",
        f"- Consults or agent runs recorded: {consults + agent_runs}",
        f"- Docs updated: {docs_updated}",
        f"- Docs stale: {docs_stale}",
        f"- Lifecycle changes: {lifecycle_changes}",
        f"- Views regenerated: {'yes' if views_generated else 'no'}",
        f"- Open knowledge gaps: {open_gaps}",
    ]
    return "\n".join(lines) + "\n"

def cmd_memory_sync_report(project: Path, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_sync_report_memory_not_initialized", message)
        return 1
    report = _build_sync_report(project)
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        _set_metadata(conn, "last_sync_report_at", _now_iso())
        _insert_event(
            conn,
            project,
            "sync_report",
            "cc memory sync-report",
            after_state={"report_hash": _content_hash_text(report)},
            reason="Generated memory sync report for work closure",
        )
    if json_output:
        print(json.dumps({"ok": True, "report": report}, indent=2))
    else:
        print(report, end="")
    return 0
