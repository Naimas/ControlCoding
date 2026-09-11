"""Derived evidence references for Dev GraphRAG citations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .privacy import scrub_value
from .retrieve import _load_candidates
from .store import _ensure_schema, _memory_connection, _print_json_error_or_text, _print_json_or_text, _require_initialized

EVIDENCE_REF_VERSION = "cc-evidence-ref/v1"
EVIDENCE_REF_PREFIX = "ccref"


def _slug(value: str, fallback: str = "record") -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "-", str(value or "").strip().lower()).strip("-")
    return cleaned or fallback


def _record_kind(record: dict[str, Any]) -> str:
    record_type = str(record.get("recordType") or "")
    type_name = str(record.get("type") or "")
    if type_name:
        return _slug(type_name)
    if record_type == "semantic_chunk":
        return "chunk"
    return _slug(record_type)


def _plane_slug(record: dict[str, Any]) -> str:
    kind = _record_kind(record)
    raw = str(record.get("plane") or "").strip().lower()
    if kind == "session" or raw in {"session", "session graphrag"}:
        return "session"
    if "project" in raw or "controlwork" in raw:
        return "project"
    if "application" in raw:
        return "application"
    return "dev"


def _plane_label(plane: str) -> str:
    return {
        "dev": "Dev Plane",
        "project": "Project Plane",
        "session": "Session GraphRAG",
        "application": "Application-owned memory",
    }.get(plane, plane or "Dev Plane")


def _stable_digest(record: dict[str, Any], plane: str, kind: str) -> str:
    stable_parts = [
        plane,
        kind,
        str(record.get("id") or ""),
        str(record.get("path") or ""),
        str(record.get("headingPath") or ""),
    ]
    return hashlib.sha256("\0".join(stable_parts).encode("utf-8")).hexdigest()[:12]


def evidence_ref_for_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a derived stable evidence reference for a retrieval record."""
    plane = _plane_slug(record)
    kind = _record_kind(record)
    node_id = f"{EVIDENCE_REF_PREFIX}:{plane}:{kind}:{_stable_digest(record, plane, kind)}"
    path = str(record.get("path") or "")
    heading = str(record.get("headingPath") or "")
    citation = path or str(record.get("id") or "")
    if heading:
        citation = f"{citation}#{heading}"
    return {
        "schemaVersion": EVIDENCE_REF_VERSION,
        "nodeId": node_id,
        "evidenceRefId": node_id,
        "plane": _plane_label(plane),
        "planeKey": plane,
        "recordKind": kind,
        "recordId": str(record.get("id") or ""),
        "recordType": str(record.get("recordType") or ""),
        "type": str(record.get("type") or ""),
        "title": str(record.get("title") or ""),
        "path": path,
        "headingPath": heading,
        "lifecycle": str(record.get("lifecycle") or ""),
        "citation": citation,
        "readOnly": True,
        "storage": "derived_from_memory_index_no_table",
        "drillDownCommand": f"python scripts/cc.py memory evidence show --project-root . {node_id}",
    }


def annotate_evidence_ref(record: dict[str, Any]) -> dict[str, Any]:
    ref = evidence_ref_for_record(record)
    annotated = dict(record)
    annotated["nodeId"] = ref["nodeId"]
    annotated["evidenceRefId"] = ref["evidenceRefId"]
    annotated["evidencePlane"] = ref["plane"]
    annotated["evidenceKind"] = ref["recordKind"]
    annotated.setdefault("citation", ref["citation"])
    annotated["evidenceRef"] = ref
    return annotated


def evidence_refs_for_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for record in records:
        ref = dict(record.get("evidenceRef") or evidence_ref_for_record(record))
        citation_id = str(record.get("citationId") or "")
        if citation_id:
            ref["citationId"] = citation_id
        refs.append(ref)
    return refs


def _source_excerpt(project: Path, path_value: str, heading_path: str, max_chars: int = 4000) -> dict[str, Any]:
    if not path_value:
        return {"available": False, "path": "", "lineStart": 0, "lineEnd": 0, "text": "", "reason": "no source path"}
    source_path = (project / path_value).resolve()
    try:
        source_path.relative_to(project.resolve())
    except ValueError:
        return {
            "available": False,
            "path": path_value,
            "lineStart": 0,
            "lineEnd": 0,
            "text": "",
            "reason": "source path resolves outside project root",
        }
    if not source_path.exists() or not source_path.is_file():
        return {"available": False, "path": path_value, "lineStart": 0, "lineEnd": 0, "text": "", "reason": "source file missing"}
    try:
        text = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"available": False, "path": path_value, "lineStart": 0, "lineEnd": 0, "text": "", "reason": str(exc)}
    lines = text.splitlines()
    start = 0
    end = min(len(lines), 80)
    if heading_path:
        target = re.split(r"\s*(?:>|/)\s*", heading_path.strip())[-1].strip().lower()
        if target:
            for index, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("#") and stripped.lstrip("#").strip().lower() == target:
                    start = index
                    end = min(len(lines), index + 80)
                    break
    excerpt = "\n".join(lines[start:end])
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip() + "\n...[truncated]"
    return {
        "available": True,
        "path": path_value,
        "lineStart": start + 1 if lines else 0,
        "lineEnd": end,
        "text": excerpt,
        "reason": "",
    }


def _json_column(value: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError:
        return fallback
    return parsed


def _record_details(conn, candidate: dict[str, Any]) -> dict[str, Any]:
    record_id = str(candidate.get("id") or "")
    record_type = str(candidate.get("recordType") or "")
    if record_type == "semantic_chunk":
        row = conn.execute(
            """
            SELECT summary, keywords, metadata, content_preview
            FROM semantic_chunks
            WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        if not row:
            return {}
        return {
            "summary": str(row["summary"] or ""),
            "keywords": _json_column(str(row["keywords"] or "[]"), []),
            "metadata": _json_column(str(row["metadata"] or "{}"), {}),
            "contentPreview": str(row["content_preview"] or ""),
        }
    if record_type == "entity":
        row = conn.execute(
            "SELECT body, source_refs, data FROM entities WHERE id = ?",
            (record_id,),
        ).fetchone()
        if not row:
            return {}
        return {
            "body": str(row["body"] or ""),
            "sourceRefs": _json_column(str(row["source_refs"] or "[]"), []),
            "data": _json_column(str(row["data"] or "{}"), {}),
        }
    if record_type == "session":
        row = conn.execute(
            """
            SELECT summary, files_changed, docs_changed, commands_run, tests_run,
                   commits, packets, decisions, followups, links
            FROM session_records
            WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        if not row:
            return {}
        return {
            "summary": str(row["summary"] or ""),
            "filesChanged": _json_column(str(row["files_changed"] or "[]"), []),
            "docsChanged": _json_column(str(row["docs_changed"] or "[]"), []),
            "commandsRun": _json_column(str(row["commands_run"] or "[]"), []),
            "testsRun": _json_column(str(row["tests_run"] or "[]"), []),
            "commits": _json_column(str(row["commits"] or "[]"), []),
            "packets": _json_column(str(row["packets"] or "[]"), []),
            "decisions": _json_column(str(row["decisions"] or "[]"), []),
            "followups": _json_column(str(row["followups"] or "[]"), []),
            "links": _json_column(str(row["links"] or "[]"), []),
        }
    return {}


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[str, str]:
    return (str(candidate.get("updatedAt") or ""), str(candidate.get("id") or ""))


def _load_evidence_candidates(project: Path) -> list[dict[str, Any]]:
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        candidates = [annotate_evidence_ref(candidate) for candidate in _load_candidates(conn).values()]
    candidates.sort(key=_candidate_sort_key, reverse=True)
    return candidates


def _show_payload(project: Path, selector: str) -> dict[str, Any]:
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        candidates = [annotate_evidence_ref(candidate) for candidate in _load_candidates(conn).values()]
        matches = [
            candidate
            for candidate in candidates
            if selector in {
                str(candidate.get("nodeId") or ""),
                str(candidate.get("evidenceRefId") or ""),
                str(candidate.get("id") or ""),
                str(candidate.get("path") or ""),
                str(candidate.get("citation") or ""),
            }
        ]
        if not matches:
            return {
                "ok": False,
                "selector": selector,
                "message": "Evidence reference not found. Run `cc memory rag-pack` and use a returned nodeId or evidenceRefId.",
                "readOnly": True,
            }
        candidate = matches[0]
        ref = candidate.get("evidenceRef") or evidence_ref_for_record(candidate)
        details = _record_details(conn, candidate)
    source_excerpt = _source_excerpt(project, str(candidate.get("path") or ""), str(candidate.get("headingPath") or ""))
    payload = {
        "ok": True,
        "selector": selector,
        "schemaVersion": EVIDENCE_REF_VERSION,
        "nodeId": ref["nodeId"],
        "evidenceRefId": ref["evidenceRefId"],
        "plane": ref["plane"],
        "recordKind": ref["recordKind"],
        "recordId": ref["recordId"],
        "recordType": ref["recordType"],
        "type": ref["type"],
        "title": ref["title"],
        "path": ref["path"],
        "headingPath": ref["headingPath"],
        "lifecycle": ref["lifecycle"],
        "citation": ref["citation"],
        "readOnly": True,
        "source": source_excerpt,
        "details": details,
        "commands": [
            {
                "kind": "graph_neighborhood",
                "command": f"python scripts/cc.py memory graph around --project-root . {ref['recordId']} --depth 2",
                "writes": False,
            },
        ],
        "collisionCount": len(matches),
    }
    if ref["path"]:
        payload["commands"].append({
            "kind": "source_graph",
            "command": f"python scripts/cc.py memory graph around --project-root . {ref['path']} --depth 2",
            "writes": False,
        })
    scrubbed_payload, privacy_receipt = scrub_value(payload)
    scrubbed_payload["privacyReceipt"] = privacy_receipt
    return scrubbed_payload


def _show_text(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return f"Evidence ref not found: {payload.get('selector', '')}\n{payload.get('message', '')}"
    lines = [
        f"Evidence ref: {payload['nodeId']}",
        f"Plane: {payload['plane']}",
        f"Type: {payload['recordType']} / {payload['type']}",
        f"Title: {payload['title']}",
        f"Lifecycle: {payload['lifecycle']}",
        f"Citation: {payload['citation']}",
        f"Read only: {payload['readOnly']}",
        "",
        "Source",
    ]
    source = payload.get("source", {})
    if source.get("available"):
        lines.append(f"  {source.get('path')}:{source.get('lineStart')}")
        if source.get("text"):
            lines.extend(["", "Excerpt", source.get("text", "")])
    else:
        lines.append(f"  unavailable - {source.get('reason', '')}")
    details = payload.get("details", {})
    preview = str(details.get("contentPreview") or details.get("summary") or details.get("body") or "")
    if preview:
        lines.extend(["", "Indexed Preview", preview[:1600]])
    lines.extend(["", "Read-only commands"])
    for command in payload.get("commands", []):
        lines.append(f"- {command['command']}")
    return "\n".join(lines)


def _list_payload(project: Path, limit: int, record_type: str = "") -> dict[str, Any]:
    candidates = _load_evidence_candidates(project)
    if record_type:
        wanted = record_type.strip().lower()
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("recordType") or "").lower() == wanted
            or str(candidate.get("type") or "").lower() == wanted
            or str(candidate.get("evidenceKind") or "").lower() == wanted
        ]
    limit = max(1, min(int(limit or 20), 200))
    items = evidence_refs_for_records(candidates[:limit])
    return {
        "ok": True,
        "schemaVersion": EVIDENCE_REF_VERSION,
        "readOnly": True,
        "storage": "derived_from_memory_index_no_table",
        "count": len(items),
        "items": items,
    }


def _list_text(payload: dict[str, Any]) -> str:
    lines = [
        "Evidence refs",
        f"Storage: {payload['storage']}",
        f"Count: {payload['count']}",
        "",
    ]
    if not payload["items"]:
        lines.append("- none")
    for item in payload["items"]:
        target = item.get("path") or item.get("recordId")
        lines.append(f"- {item['nodeId']} - {item['plane']} - `{target}` ({item.get('lifecycle', '')})")
    return "\n".join(lines)


def cmd_memory_evidence_show(project: Path, selector: str, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_evidence_memory_not_initialized", message)
        return 1
    selector = str(selector or "").strip()
    if not selector:
        _print_json_error_or_text(json_output, "evidence_selector_required", "evidence selector is required")
        return 1
    payload = _show_payload(project, selector)
    _print_json_or_text(json_output, payload, _show_text(payload))
    return 0 if payload.get("ok") else 1


def cmd_memory_evidence_list(
    project: Path,
    limit: int = 20,
    record_type: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_evidence_memory_not_initialized", message)
        return 1
    payload = _list_payload(project, limit=limit, record_type=record_type)
    _print_json_or_text(json_output, payload, _list_text(payload))
    return 0


__all__ = [
    "EVIDENCE_REF_VERSION",
    "annotate_evidence_ref",
    "cmd_memory_evidence_list",
    "cmd_memory_evidence_show",
    "evidence_ref_for_record",
    "evidence_refs_for_records",
]
