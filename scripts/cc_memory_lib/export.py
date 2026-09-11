"""Derived graph export and lightweight viewer for project memory."""

from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path
from typing import Any

from .graph import EDGE_TYPE_TO_GRAPH_EDGE, ENTITY_TYPE_TO_GRAPH_NODE, GRAPH_CONTRACT_VERSION, SUGGESTION_TYPE_TO_GRAPH_EDGE
from .store import (
    _ensure_schema,
    _memory_connection,
    _now_iso,
    _print_json_error_or_text,
    _print_json_or_text,
    _relative_path,
    _require_initialized,
)


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError:
        return fallback
    return parsed


def _split_filter(values: list[str] | None) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        for item in str(value).split(","):
            normalized = item.strip()
            if normalized:
                result.add(normalized)
    return result


def _is_inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_output_path(project: Path, output: Path) -> Path:
    root = project.resolve()
    target = output.expanduser()
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    if not _is_inside(root, target):
        raise ValueError("output path must stay inside the selected project root")
    return target


def _source_bucket(node_type: str, plane: str, path: str) -> str:
    if node_type == "application_memory_component" or plane == "application_memory":
        return "application"
    if path == "CONTROLWORK.md" or path.startswith(".controlwork/"):
        return "project"
    return "dev"


def _source_matches(path: str, bucket: str, filters: set[str]) -> bool:
    if not filters:
        return True
    for item in filters:
        lowered = item.lower()
        if lowered in {"dev", "project", "application"} and bucket == lowered:
            return True
        if path.startswith(item) or item in path:
            return True
    return False


def _entity_nodes(conn: sqlite3.Connection, project: Path) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM entities ORDER BY type, title, id").fetchall()
    nodes: list[dict[str, Any]] = []
    for row in rows:
        data = _json_loads(str(row["data"] or "{}"), {})
        if not isinstance(data, dict):
            data = {}
        node_type = str(row["type"] or "")
        path = str(row["path"] or "")
        plane = str(row["plane"] or "")
        bucket = _source_bucket(node_type, plane, path)
        nodes.append({
            "id": str(row["id"]),
            "recordType": "entity",
            "type": node_type,
            "graphNodeType": ENTITY_TYPE_TO_GRAPH_NODE.get(node_type, "document"),
            "title": str(row["title"] or row["id"]),
            "path": path,
            "lifecycle": str(row["lifecycle"] or ""),
            "plane": plane,
            "source": bucket,
            "documentType": str(data.get("document_type") or ""),
            "facets": data.get("document_facets") if isinstance(data.get("document_facets"), list) else [],
        })
    return nodes


def _chunk_nodes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, document_id, source_path, heading_path, summary, lifecycle, metadata
        FROM semantic_chunks
        ORDER BY source_path, ordinal, sequence_index
        """
    ).fetchall()
    nodes: list[dict[str, Any]] = []
    for row in rows:
        metadata = _json_loads(str(row["metadata"] or "{}"), {})
        if not isinstance(metadata, dict):
            metadata = {}
        path = str(row["source_path"] or "")
        nodes.append({
            "id": str(row["id"]),
            "recordType": "semantic_chunk",
            "type": "chunk",
            "graphNodeType": "chunk",
            "title": str(row["summary"] or row["heading_path"] or row["id"]),
            "path": path,
            "headingPath": str(row["heading_path"] or ""),
            "lifecycle": str(row["lifecycle"] or ""),
            "documentId": str(row["document_id"] or ""),
            "source": _source_bucket("chunk", "controlcoding_dev", path),
            "structuralLevel": str(metadata.get("structural_level") or ""),
            "blockType": str(metadata.get("block_type") or ""),
            "semanticRole": str(metadata.get("semantic_role") or ""),
            "topics": metadata.get("topics") if isinstance(metadata.get("topics"), list) else [],
        })
    return nodes


def _node_allowed(
    node: dict[str, Any],
    type_filters: set[str],
    lifecycle_filters: set[str],
    source_filters: set[str],
) -> bool:
    node_types = {
        str(node.get("type") or ""),
        str(node.get("graphNodeType") or ""),
        str(node.get("recordType") or ""),
        str(node.get("documentType") or ""),
    }
    if type_filters and not (node_types & type_filters):
        return False
    if lifecycle_filters and str(node.get("lifecycle") or "") not in lifecycle_filters:
        return False
    return _source_matches(str(node.get("path") or ""), str(node.get("source") or ""), source_filters)


def _canonical_edges(conn: sqlite3.Connection, selected_node_ids: set[str], confidence_filters: set[str]) -> list[dict[str, Any]]:
    if confidence_filters and "canonical" not in confidence_filters:
        return []
    rows = conn.execute("SELECT * FROM edges ORDER BY type, source_id, target_id").fetchall()
    edges: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row["source_id"])
        target_id = str(row["target_id"])
        if source_id not in selected_node_ids or target_id not in selected_node_ids:
            continue
        edge_type = str(row["type"] or "")
        edges.append({
            "id": str(row["id"]),
            "sourceId": source_id,
            "targetId": target_id,
            "type": edge_type,
            "graphEdgeType": EDGE_TYPE_TO_GRAPH_EDGE.get(edge_type, edge_type),
            "confidence": "canonical",
            "status": "canonical",
            "source": "canonical_edge",
            "createdAt": str(row["created_at"] or ""),
        })
    return edges


def _suggestions(conn: sqlite3.Connection, selected_node_ids: set[str], confidence_filters: set[str]) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM correlation_suggestions
        ORDER BY status, confidence, type, source_id, target_id
        """
    ).fetchall()
    suggestions: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row["source_id"])
        target_id = str(row["target_id"])
        confidence = str(row["confidence"] or "")
        if source_id not in selected_node_ids or target_id not in selected_node_ids:
            continue
        if confidence_filters and confidence not in confidence_filters:
            continue
        suggestion_type = str(row["type"] or "")
        suggestions.append({
            "id": str(row["id"]),
            "sourceId": source_id,
            "targetId": target_id,
            "type": suggestion_type,
            "graphEdgeType": SUGGESTION_TYPE_TO_GRAPH_EDGE.get(suggestion_type, suggestion_type),
            "confidence": confidence,
            "status": str(row["status"] or ""),
            "source": "correlation_suggestion",
            "reason": str(row["reason"] or ""),
            "createdAt": str(row["created_at"] or ""),
            "updatedAt": str(row["updated_at"] or ""),
        })
    return suggestions


def _export_payload(
    conn: sqlite3.Connection,
    project: Path,
    type_filters: set[str],
    lifecycle_filters: set[str],
    confidence_filters: set[str],
    source_filters: set[str],
) -> dict[str, Any]:
    all_nodes = _entity_nodes(conn, project) + _chunk_nodes(conn)
    nodes = [
        node
        for node in all_nodes
        if _node_allowed(node, type_filters, lifecycle_filters, source_filters)
    ]
    selected_ids = {str(node["id"]) for node in nodes}
    edges = _canonical_edges(conn, selected_ids, confidence_filters)
    suggestions = _suggestions(conn, selected_ids, confidence_filters)
    return {
        "ok": True,
        "generatedAt": _now_iso(),
        "contractVersion": GRAPH_CONTRACT_VERSION,
        "projection": {
            "sourceOfTruth": False,
            "derived": True,
            "note": "This export is a derived projection. The SQLite store and source files remain authoritative.",
        },
        "filters": {
            "type": sorted(type_filters),
            "lifecycle": sorted(lifecycle_filters),
            "confidence": sorted(confidence_filters),
            "source": sorted(source_filters),
        },
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "suggestions": len(suggestions),
            "unfilteredNodes": len(all_nodes),
        },
        "nodes": nodes,
        "edges": edges,
        "suggestions": suggestions,
    }


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _html_view(payload: dict[str, Any]) -> str:
    node_rows = [
        [
            str(node["id"]),
            str(node["type"]),
            str(node["lifecycle"]),
            str(node["source"]),
            str(node.get("path") or ""),
            str(node.get("title") or ""),
        ]
        for node in payload["nodes"]
    ]
    edge_rows = [
        [
            str(edge["type"]),
            str(edge["confidence"]),
            str(edge["sourceId"]),
            str(edge["targetId"]),
            str(edge["source"]),
        ]
        for edge in payload["edges"]
    ]
    suggestion_rows = [
        [
            str(item["type"]),
            str(item["confidence"]),
            str(item["status"]),
            str(item["sourceId"]),
            str(item["targetId"]),
            str(item["reason"])[:140],
        ]
        for item in payload["suggestions"]
    ]
    filters = ", ".join(
        f"{key}={value or ['all']}"
        for key, value in payload["filters"].items()
    )
    return "\n".join([
        "<!doctype html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<title>ControlCoding Memory Graph Export</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;margin:24px;color:#1f2933;background:#f7f9fb}",
        "h1,h2{margin:0 0 12px}",
        "section{margin:24px 0}",
        "table{border-collapse:collapse;width:100%;background:#fff}",
        "th,td{border:1px solid #d9e2ec;padding:6px 8px;text-align:left;font-size:13px;vertical-align:top}",
        "th{background:#eef3f8}",
        ".notice{padding:10px 12px;background:#fff8e1;border:1px solid #d6a700;margin:12px 0}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>ControlCoding Memory Graph Export</h1>",
        "<div class=\"notice\">Derived projection only. The SQLite store and source files remain authoritative.</div>",
        f"<p>Generated: {html.escape(payload['generatedAt'])}</p>",
        f"<p>Contract: {html.escape(payload['contractVersion'])}</p>",
        f"<p>Filters: {html.escape(filters)}</p>",
        f"<p>Counts: nodes={payload['counts']['nodes']}, edges={payload['counts']['edges']}, suggestions={payload['counts']['suggestions']}</p>",
        "<section><h2>Nodes</h2>",
        _html_table(["ID", "Type", "Lifecycle", "Source", "Path", "Title"], node_rows),
        "</section>",
        "<section><h2>Edges</h2>",
        _html_table(["Type", "Confidence", "Source ID", "Target ID", "Source"], edge_rows),
        "</section>",
        "<section><h2>Suggestions</h2>",
        _html_table(["Type", "Confidence", "Status", "Source ID", "Target ID", "Reason"], suggestion_rows),
        "</section>",
        "</body></html>",
    ])


def _write_output(project: Path, output: Path | None, content: str) -> str:
    if output is None:
        return ""
    output_path = _resolve_output_path(project, output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return _relative_path(project, output_path)


def cmd_memory_graph_export(
    project: Path,
    output_format: str = "json",
    output: Path | None = None,
    type_filters: list[str] | None = None,
    lifecycle_filters: list[str] | None = None,
    confidence_filters: list[str] | None = None,
    source_filters: list[str] | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_graph_export_memory_not_initialized", message)
        return 1
    normalized_format = output_format if output_format in {"json", "html"} else "json"
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        payload = _export_payload(
            conn,
            project,
            _split_filter(type_filters),
            _split_filter(lifecycle_filters),
            _split_filter(confidence_filters),
            _split_filter(source_filters),
        )
    content = json.dumps(payload, indent=2) if normalized_format == "json" else _html_view(payload)
    try:
        output_path = _write_output(project, output, content)
    except ValueError as exc:
        _print_json_error_or_text(json_output, "graph_export_output_invalid", str(exc))
        return 1
    if output_path:
        payload["outputPath"] = output_path
    if json_output:
        _print_json_or_text(True, payload, "")
        return 0
    if output is not None:
        print(f"Memory graph export written: {output_path}")
        print("Projection only: SQLite memory and source files remain authoritative.")
        return 0
    print(content)
    return 0
