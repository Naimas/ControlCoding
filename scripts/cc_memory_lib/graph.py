"""Graph inspection and suggestion governance for project memory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .entities import _row_to_dict
from .ledger import _insert_event
from .lifecycle import _find_entity_for_lifecycle
from .schema import ENTITY_TYPE_CODES, SCHEMA_VERSION
from .store import (
    _ensure_schema,
    _json_dumps,
    _memory_connection,
    _now_iso,
    _print_json_error_or_text,
    _print_json_or_text,
    _relative_path,
    _require_initialized,
)
from .views import _generate_views

GRAPH_CONTRACT_VERSION = "memory-graph-contract/v1"

ENTITY_TYPE_TO_GRAPH_NODE = {
    "agent_run": "agent_run",
    "application_memory_component": "source",
    "benchmark": "evidence",
    "chunk_node": "chunk",
    "consult": "consult",
    "decision": "decision",
    "design": "document",
    "doc_node": "document",
    "file_node": "file",
    "handoff": "work_output",
    "idea": "idea",
    "note": "note",
    "plan": "plan",
    "research_note": "source",
    "session": "session",
    "test_node": "evidence",
    "work_item": "plan",
}

EDGE_TYPE_TO_GRAPH_EDGE = {
    "conflicts_with": "conflicts_with",
    "contains": "contains",
    "continues_from": "continues_from",
    "continues_to": "continues_to",
    "references": "references",
    "same_section_as": "same_section_as",
    "next_chunk": "next_chunk",
    "previous_chunk": "previous_chunk",
    "superseded_by": "superseded_by",
    "supersedes": "supersedes",
}

SUGGESTION_TYPE_TO_GRAPH_EDGE = {
    "references": "references",
    "related_heading": "mentions",
    "related_terms": "mentions",
}

SUGGESTION_STATUSES = {"accepted", "confirmed", "rejected", "suggested"}


def _edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    seed = f"{source_id}:{target_id}:{edge_type}"
    return "EDGE_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _json_loads_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _suggestion_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["data"] = _json_loads_dict(str(data.get("data") or "{}"))
    data["graphEdgeType"] = SUGGESTION_TYPE_TO_GRAPH_EDGE.get(str(data.get("type") or ""), str(data.get("type") or ""))
    return data


def _edge_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["data"] = _json_loads_dict(str(data.get("data") or "{}"))
    data["graphEdgeType"] = EDGE_TYPE_TO_GRAPH_EDGE.get(str(data.get("type") or ""), str(data.get("type") or ""))
    return data


def _chunk_row_to_node(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "type": "chunk",
        "graphNodeType": "chunk",
        "title": str(row["summary"] or row["heading_path"] or row["id"]),
        "path": str(row["source_path"] or ""),
        "lifecycle": str(row["lifecycle"] or ""),
        "headingPath": str(row["heading_path"] or ""),
    }


def _entity_row_to_node(row: sqlite3.Row) -> dict[str, Any]:
    entity = _row_to_dict(row)
    entity["graphNodeType"] = ENTITY_TYPE_TO_GRAPH_NODE.get(str(entity.get("type") or ""), "document")
    return entity


def _node_by_id(conn: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (node_id,)).fetchone()
    if row:
        return _entity_row_to_node(row)
    chunk = conn.execute("SELECT * FROM semantic_chunks WHERE id = ?", (node_id,)).fetchone()
    if chunk:
        return _chunk_row_to_node(chunk)
    return {
        "id": node_id,
        "type": "unknown",
        "graphNodeType": "unknown",
        "title": node_id,
        "path": "",
        "lifecycle": "",
    }


def _resolve_graph_selector(
    conn: sqlite3.Connection,
    project: Path,
    selector: str,
) -> tuple[str, dict[str, Any] | None, str]:
    selector = selector.strip()
    if not selector:
        return "", None, "empty selector"
    chunk = conn.execute("SELECT * FROM semantic_chunks WHERE id = ?", (selector,)).fetchone()
    if chunk:
        return str(chunk["id"]), _chunk_row_to_node(chunk), ""
    entity, error = _find_entity_for_lifecycle(conn, project, selector)
    if entity:
        entity["graphNodeType"] = ENTITY_TYPE_TO_GRAPH_NODE.get(str(entity.get("type") or ""), "document")
        return str(entity["id"]), entity, ""
    rel_selector = _relative_path(project, selector)
    chunk = conn.execute(
        """
        SELECT *
        FROM semantic_chunks
        WHERE source_path = ?
        ORDER BY ordinal, sequence_index
        LIMIT 1
        """,
        (rel_selector,),
    ).fetchone()
    if chunk:
        return str(chunk["id"]), _chunk_row_to_node(chunk), ""
    return "", None, error or f"node not found: {selector}"


def _status_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    entity_rows = conn.execute(
        "SELECT type, COUNT(*) AS count FROM entities GROUP BY type ORDER BY type"
    ).fetchall()
    edge_rows = conn.execute(
        "SELECT type, COUNT(*) AS count FROM edges GROUP BY type ORDER BY type"
    ).fetchall()
    suggestion_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM correlation_suggestions
        GROUP BY status
        ORDER BY status
        """
    ).fetchall()
    chunk_count = int(conn.execute("SELECT COUNT(*) AS count FROM semantic_chunks").fetchone()["count"])
    node_counts: dict[str, int] = {}
    unmapped_entity_types: list[str] = []
    for row in entity_rows:
        entity_type = str(row["type"])
        count = int(row["count"])
        graph_type = ENTITY_TYPE_TO_GRAPH_NODE.get(entity_type)
        if not graph_type:
            unmapped_entity_types.append(entity_type)
            graph_type = "unknown"
        node_counts[graph_type] = node_counts.get(graph_type, 0) + count
    if chunk_count:
        node_counts["chunk"] = node_counts.get("chunk", 0) + chunk_count
    edge_counts: dict[str, int] = {}
    unmapped_edge_types: list[str] = []
    for row in edge_rows:
        edge_type = str(row["type"])
        count = int(row["count"])
        graph_type = EDGE_TYPE_TO_GRAPH_EDGE.get(edge_type)
        if not graph_type:
            unmapped_edge_types.append(edge_type)
            graph_type = "unknown"
        edge_counts[graph_type] = edge_counts.get(graph_type, 0) + count
    suggestion_counts = {str(row["status"]): int(row["count"]) for row in suggestion_rows}
    return {
        "ok": True,
        "contractVersion": GRAPH_CONTRACT_VERSION,
        "sqliteSchemaVersion": SCHEMA_VERSION,
        "entityTypeCoverage": {
            "knownCurrentTypes": sorted(ENTITY_TYPE_CODES),
            "mappedTypes": sorted(ENTITY_TYPE_TO_GRAPH_NODE),
            "unmappedTypes": sorted(unmapped_entity_types),
        },
        "edgeTypeCoverage": {
            "mappedTypes": sorted(EDGE_TYPE_TO_GRAPH_EDGE),
            "unmappedTypes": sorted(unmapped_edge_types),
        },
        "nodeCounts": dict(sorted(node_counts.items())),
        "edgeCounts": dict(sorted(edge_counts.items())),
        "suggestionCounts": suggestion_counts,
        "semanticChunks": chunk_count,
    }


def cmd_memory_graph_status(project: Path, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_graph_memory_not_initialized", message)
        return 1
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        payload = _status_payload(conn)
    lines = [
        "Memory graph status",
        f"  Contract version: {payload['contractVersion']}",
        f"  SQLite schema version: {payload['sqliteSchemaVersion']}",
        f"  Semantic chunks: {payload['semanticChunks']}",
        "  Suggestions:",
    ]
    suggestion_counts = payload["suggestionCounts"]
    if suggestion_counts:
        lines.extend(f"    {key}: {value}" for key, value in sorted(suggestion_counts.items()))
    else:
        lines.append("    none: 0")
    lines.append("  Node types:")
    lines.extend(f"    {key}: {value}" for key, value in payload["nodeCounts"].items())
    lines.append("  Edge types:")
    if payload["edgeCounts"]:
        lines.extend(f"    {key}: {value}" for key, value in payload["edgeCounts"].items())
    else:
        lines.append("    none: 0")
    if payload["entityTypeCoverage"]["unmappedTypes"]:
        lines.append("  Unmapped entity types: " + ", ".join(payload["entityTypeCoverage"]["unmappedTypes"]))
    if payload["edgeTypeCoverage"]["unmappedTypes"]:
        lines.append("  Unmapped edge types: " + ", ".join(payload["edgeTypeCoverage"]["unmappedTypes"]))
    _print_json_or_text(json_output, payload, "\n".join(lines))
    return 0


def cmd_memory_graph_suggestions(
    project: Path,
    status: str = "suggested",
    limit: int = 50,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_graph_memory_not_initialized", message)
        return 1
    limit = max(1, min(int(limit or 50), 200))
    normalized_status = str(status or "suggested").strip().lower()
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        if normalized_status == "all":
            rows = conn.execute(
                """
                SELECT *
                FROM correlation_suggestions
                ORDER BY status, confidence, type, source_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM correlation_suggestions
                WHERE status = ?
                ORDER BY confidence, type, source_id
                LIMIT ?
                """,
                (normalized_status, limit),
            ).fetchall()
    suggestions = [_suggestion_row_to_dict(row) for row in rows]
    payload = {
        "ok": True,
        "status": normalized_status,
        "count": len(suggestions),
        "suggestions": suggestions,
    }
    if json_output:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"Memory graph suggestions: {normalized_status}")
    if not suggestions:
        print("No correlation suggestions found.")
        return 0
    for item in suggestions:
        print(
            f"- {item['id']} [{item['status']}] {item['source_id']} -> {item['target_id']} "
            f"({item['type']} => {item['graphEdgeType']}, {item['confidence']})"
        )
        print(f"  Reason: {item['reason']}")
    return 0


def _load_suggestion(conn: sqlite3.Connection, suggestion_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM correlation_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    return _suggestion_row_to_dict(row) if row else None


def _review_data(existing_data: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    data = dict(existing_data)
    command = {
        "accepted": "cc memory graph accept",
        "rejected": "cc memory graph reject",
    }.get(status, f"cc memory graph {status}")
    data["review"] = {
        "status": status,
        "reason": reason,
        "reviewed_at": _now_iso(),
        "command": command,
    }
    return data


def _insert_accepted_edge(
    conn: sqlite3.Connection,
    suggestion: dict[str, Any],
    reason: str,
) -> str:
    edge_type = str(suggestion.get("graphEdgeType") or suggestion.get("type") or "mentions")
    source_id = str(suggestion["source_id"])
    target_id = str(suggestion["target_id"])
    edge_data = {
        "from_suggestion_id": str(suggestion["id"]),
        "original_suggestion_type": str(suggestion["type"]),
        "confidence": str(suggestion["confidence"]),
        "reason": reason,
        "review_status": "accepted",
    }
    edge_id = _edge_id(source_id, target_id, edge_type)
    conn.execute(
        """
        INSERT OR IGNORE INTO edges(id, source_id, target_id, type, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            edge_id,
            source_id,
            target_id,
            edge_type,
            _json_dumps(edge_data),
            _now_iso(),
        ),
    )
    return edge_id


def _set_suggestion_status(
    conn: sqlite3.Connection,
    suggestion: dict[str, Any],
    status: str,
    reason: str,
) -> dict[str, Any]:
    now = _now_iso()
    updated_data = _review_data(
        suggestion.get("data") if isinstance(suggestion.get("data"), dict) else {},
        status,
        reason,
    )
    conn.execute(
        """
        UPDATE correlation_suggestions
        SET status = ?, data = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, _json_dumps(updated_data), now, suggestion["id"]),
    )
    updated = dict(suggestion)
    updated["status"] = status
    updated["data"] = updated_data
    updated["updated_at"] = now
    return updated


def cmd_memory_graph_accept(
    project: Path,
    suggestion_id: str,
    reason: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_graph_memory_not_initialized", message)
        return 1
    suggestion_id = suggestion_id.strip()
    if not suggestion_id:
        _print_json_error_or_text(json_output, "graph_suggestion_id_required", "suggestion id is required")
        return 1
    review_reason = reason.strip() or "Accepted graph suggestion"
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        suggestion = _load_suggestion(conn, suggestion_id)
        if not suggestion:
            _print_json_error_or_text(json_output, "graph_suggestion_not_found", f"suggestion not found: {suggestion_id}")
            return 1
        before = {"status": suggestion["status"], "data": suggestion.get("data", {})}
        edge_id = _insert_accepted_edge(conn, suggestion, review_reason)
        updated = _set_suggestion_status(conn, suggestion, "accepted", review_reason)
        _insert_event(
            conn,
            project,
            "link",
            "cc memory graph accept",
            target_entity_ids=[str(suggestion["source_id"]), str(suggestion["target_id"])],
            before_state=before,
            after_state={
                "suggestion_id": suggestion_id,
                "status": "accepted",
                "edge_id": edge_id,
            },
            reason=review_reason,
            provenance={"source": "cc memory graph accept", "suggestion_id": suggestion_id},
        )
    _generate_views(project, command="cc memory graph accept", quiet=True)
    payload = {"ok": True, "suggestion": updated, "edgeId": edge_id}
    _print_json_or_text(
        json_output,
        payload,
        f"Accepted graph suggestion: {suggestion_id}\n  Edge: {edge_id}",
    )
    return 0


def cmd_memory_graph_reject(
    project: Path,
    suggestion_id: str,
    reason: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_graph_memory_not_initialized", message)
        return 1
    suggestion_id = suggestion_id.strip()
    if not suggestion_id:
        _print_json_error_or_text(json_output, "graph_suggestion_id_required", "suggestion id is required")
        return 1
    review_reason = reason.strip() or "Rejected graph suggestion"
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        suggestion = _load_suggestion(conn, suggestion_id)
        if not suggestion:
            _print_json_error_or_text(json_output, "graph_suggestion_not_found", f"suggestion not found: {suggestion_id}")
            return 1
        before = {"status": suggestion["status"], "data": suggestion.get("data", {})}
        updated = _set_suggestion_status(conn, suggestion, "rejected", review_reason)
        _insert_event(
            conn,
            project,
            "reject",
            "cc memory graph reject",
            target_entity_ids=[str(suggestion["source_id"]), str(suggestion["target_id"])],
            before_state=before,
            after_state={"suggestion_id": suggestion_id, "status": "rejected"},
            reason=review_reason,
            provenance={"source": "cc memory graph reject", "suggestion_id": suggestion_id},
            review_required=True,
        )
    _generate_views(project, command="cc memory graph reject", quiet=True)
    payload = {"ok": True, "suggestion": updated}
    _print_json_or_text(json_output, payload, f"Rejected graph suggestion: {suggestion_id}")
    return 0


def _edge_rows_around(conn: sqlite3.Connection, node_ids: set[str]) -> list[sqlite3.Row]:
    if not node_ids:
        return []
    placeholders = ",".join("?" for _item in node_ids)
    return conn.execute(
        f"""
        SELECT *
        FROM edges
        WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
        ORDER BY type, source_id, target_id
        """,
        (*node_ids, *node_ids),
    ).fetchall()


def _around_payload(conn: sqlite3.Connection, project: Path, selector: str, depth: int) -> tuple[dict[str, Any], str]:
    center_id, center, error = _resolve_graph_selector(conn, project, selector)
    if error:
        return {}, error
    depth = max(1, min(int(depth or 1), 4))
    visited = {center_id}
    frontier = {center_id}
    collected_edges: dict[str, dict[str, Any]] = {}
    for _level in range(depth):
        rows = _edge_rows_around(conn, frontier)
        next_frontier: set[str] = set()
        for row in rows:
            edge = _edge_row_to_dict(row)
            collected_edges[str(edge["id"])] = edge
            for node_id in (str(edge["source_id"]), str(edge["target_id"])):
                if node_id not in visited:
                    next_frontier.add(node_id)
                    visited.add(node_id)
        frontier = next_frontier
        if not frontier:
            break
    nodes = [_node_by_id(conn, node_id) for node_id in sorted(visited)]
    suggestions = conn.execute(
        """
        SELECT *
        FROM correlation_suggestions
        WHERE source_id = ? OR target_id = ?
        ORDER BY status, confidence, type, source_id
        LIMIT 50
        """,
        (center_id, center_id),
    ).fetchall()
    return {
        "ok": True,
        "selector": selector,
        "depth": depth,
        "center": center,
        "nodes": nodes,
        "edges": list(collected_edges.values()),
        "suggestions": [_suggestion_row_to_dict(row) for row in suggestions],
    }, ""


def cmd_memory_graph_around(
    project: Path,
    selector: str,
    depth: int = 1,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_graph_memory_not_initialized", message)
        return 1
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        payload, error = _around_payload(conn, project, selector, depth)
    if error:
        _print_json_error_or_text(json_output, "graph_around_selector_not_found", str(error))
        return 1
    if json_output:
        print(json.dumps(payload, indent=2))
        return 0
    center = payload["center"] or {}
    print(f"Memory graph around: {selector}")
    print(f"  Center: {center.get('id')} - {center.get('title')} ({center.get('graphNodeType')})")
    print("  Nodes:")
    for node in payload["nodes"]:
        path = f" [{node.get('path')}]" if node.get("path") else ""
        print(f"    - {node.get('id')} - {node.get('title')} ({node.get('graphNodeType')}){path}")
    print("  Edges:")
    if payload["edges"]:
        for edge in payload["edges"]:
            print(
                f"    - {edge['source_id']} -> {edge['target_id']} "
                f"({edge['type']} => {edge['graphEdgeType']})"
            )
    else:
        print("    none")
    if payload["suggestions"]:
        print("  Suggestions touching center:")
        for suggestion in payload["suggestions"][:10]:
            print(
                f"    - {suggestion['id']} [{suggestion['status']}] "
                f"{suggestion['type']} => {suggestion['graphEdgeType']}"
            )
    return 0


__all__ = [
    "EDGE_TYPE_TO_GRAPH_EDGE",
    "ENTITY_TYPE_TO_GRAPH_NODE",
    "GRAPH_CONTRACT_VERSION",
    "SUGGESTION_TYPE_TO_GRAPH_EDGE",
    "cmd_memory_graph_accept",
    "cmd_memory_graph_around",
    "cmd_memory_graph_reject",
    "cmd_memory_graph_status",
    "cmd_memory_graph_suggestions",
]
