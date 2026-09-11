"""Federated cross-plane GraphRAG packet builder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import work_features
from .graph import GRAPH_CONTRACT_VERSION
from .op_index import _op_index_payload
from .privacy import scrub_value
from .rag_pack import _build_rag_pack
from .sessions import _session_packet_payload
from .store import _now_iso, _print_json_error_or_text, _print_json_or_text, _relative_path, _require_initialized

CROSS_PLANE_PACKET_TYPE = "controlcoding-cross-plane-graphrag-packet/v1"


def _short(text: Any, limit: int = 700) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + " [truncated]"


def _project_plane_present(project: Path) -> bool:
    return (project / "CONTROLWORK.md").exists() or (project / ".controlwork" / "memory").exists()


def _project_selection(project: Path, scope: str, query: str, limit: int, include_legacy: bool) -> dict[str, Any]:
    if not _project_plane_present(project):
        return {
            "present": False,
            "current": [],
            "needsReview": [],
            "legacy": [],
            "contextMarkdown": "",
            "warnings": ["Project Plane is not initialized in this workspace."],
        }
    selected = work_features.select_context_entries(
        project,
        scope=scope,
        topic=query,
        limit=limit,
        include_legacy=include_legacy,
    )
    markdown = work_features.build_context_pack(
        project,
        scope=scope,
        topic=query,
        limit=limit,
        include_legacy=include_legacy,
        refresh_views=False,
    )
    warnings: list[str] = []
    if selected.get("needsReview"):
        warnings.append("Project Plane includes needs_review records.")
    if selected.get("legacy"):
        warnings.append("Project Plane includes legacy or superseded records as warning material.")
    return {
        "present": True,
        "current": selected.get("current", []),
        "needsReview": selected.get("needsReview", []),
        "legacy": selected.get("legacy", []),
        "contextMarkdown": markdown,
        "warnings": warnings,
    }


def _project_citations(project_payload: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    buckets = (
        ("current", "current"),
        ("needsReview", "needs_review"),
        ("legacy", "legacy"),
    )
    index = 1
    for bucket, lifecycle_group in buckets:
        for entry in project_payload.get(bucket, []) or []:
            citations.append({
                "citationId": f"P{index}",
                "plane": "Project Plane",
                "recordType": "controlwork_entry",
                "lifecycleGroup": lifecycle_group,
                "title": str(entry.get("title") or entry.get("stem") or "Untitled"),
                "path": str(entry.get("path") or ""),
                "lifecycle": str(entry.get("lifecycle") or ""),
                "area": str(entry.get("area") or ""),
                "category": str(entry.get("category") or ""),
                "score": entry.get("_contextScore"),
                "reasons": entry.get("_contextReasons") or [],
                "summary": _short(entry.get("body"), 420),
            })
            index += 1
    return citations


def _dev_citations(dev_pack: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for index, item in enumerate(dev_pack.get("citations", []) or [], start=1):
        citation = dict(item)
        citation["citationId"] = f"D{index}"
        citation["plane"] = "Dev Plane"
        citations.append(citation)
    return citations


def _session_citations(session_pack: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for index, item in enumerate(session_pack.get("citations", []) or [], start=1):
        citation = dict(item)
        citation["citationId"] = f"S{index}"
        citation["plane"] = "Session GraphRAG"
        citations.append(citation)
    return citations


def _warnings(
    dev_pack: dict[str, Any],
    project_payload: dict[str, Any],
    session_pack: dict[str, Any],
    op_index: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    for item in dev_pack.get("warnings", []) or []:
        message = item.get("message") if isinstance(item, dict) else item
        if message:
            warnings.append(f"Dev Plane: {message}")
    for item in project_payload.get("warnings", []) or []:
        warnings.append(f"Project Plane: {item}")
    for item in session_pack.get("warnings", []) or []:
        warnings.append(f"Session GraphRAG: {item}")
    for item in op_index.get("warnings", []) or []:
        warnings.append(f"RAG-O: {item}")
    if op_index.get("planes", {}).get("project", {}).get("actionableSemanticDrift"):
        warnings.append("Project Plane drift needs review before treating embedded and external ControlWork as equivalent.")
    if int(op_index.get("planes", {}).get("applicationOwned", {}).get("referencedComponents") or 0):
        warnings.append("Application-owned memory references must be accessed only through approved application adapters.")
    return warnings


def _next_reads(
    dev_pack: dict[str, Any],
    project_citations: list[dict[str, Any]],
    session_pack: dict[str, Any],
    op_index: dict[str, Any],
) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, target: str, reason: str) -> None:
        if not target:
            return
        key = (kind, target)
        if key in seen:
            return
        seen.add(key)
        reads.append({"kind": kind, "target": target, "reason": reason})

    for item in dev_pack.get("nextReads", []) or []:
        target = str(item.get("target") or item.get("command") or "")
        add(str(item.get("kind") or "source"), target, str(item.get("reason") or "Dev GraphRAG next read."))
    for item in project_citations:
        add("project_source", str(item.get("path") or ""), f"Project Plane citation {item.get('citationId')}.")
    for item in session_pack.get("nextReads", []) or []:
        add("session_link", str(item), "Linked session artifact or changed file.")
    for route in op_index.get("routes", []) or []:
        if route.get("plane") in {"Dev Plane", "Project Plane", "Session GraphRAG"}:
            add("route", str(route.get("command") or ""), str(route.get("purpose") or "RAG-O route."))
    return reads[:30]


def _packet_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Cross-Plane GraphRAG Packet: {payload['query']}",
        "",
        f"- **Generated**: {payload['generatedAt']}",
        f"- **Scope**: {payload['scope']}",
        f"- **Packet Type**: `{payload['packetType']}`",
        f"- **Graph Contract**: `{payload['graphContractVersion']}`",
        f"- **Mode**: {payload['mode']}",
        "",
        "## AI Use Contract",
        "",
        "- This is a federated packet, not a merged source of truth.",
        "- Keep Dev Plane, Project Plane, Session GraphRAG, and application-owned memory separate.",
        "- Use Dev Plane citations for coding and verification context.",
        "- Use Project Plane citations through ControlWork for requirements, research, decisions, and plans.",
        "- Use Session GraphRAG citations for chat and work continuity only.",
        "- RAG-O routes are operational guidance and do not retrieve or generate answers.",
        "",
        "## Plane Summary",
        "",
        f"- Dev Plane citations: {len(payload['planes']['dev']['citations'])}",
        f"- Project Plane present: {payload['planes']['project']['present']}",
        f"- Project Plane citations: {len(payload['planes']['project']['citations'])}",
        f"- Session citations: {len(payload['planes']['sessions']['citations'])}",
        f"- RAG-O action items: {len(payload['planes']['operations']['actionQueue'])}",
        "",
        "## Cross-Plane Citations",
        "",
    ]
    if not payload["citations"]:
        lines.append("- No citations returned.")
    for citation in payload["citations"]:
        location = citation.get("path") or citation.get("citation") or citation.get("id") or citation.get("title")
        lifecycle = citation.get("lifecycle") or citation.get("status") or citation.get("lifecycleGroup") or ""
        lines.append(f"- [{citation['citationId']}] {citation['plane']} - `{location}` ({lifecycle})")
    lines.extend(["", "## Dev Plane Evidence", ""])
    dev = payload["planes"]["dev"]
    if dev["citations"]:
        for citation in dev["citations"]:
            lines.append(f"- [{citation['citationId']}] {citation.get('title')} - `{citation.get('citation')}`")
    else:
        lines.append("- No Dev Plane citations returned.")
    lines.extend(["", "## Project Plane Evidence", ""])
    project = payload["planes"]["project"]
    if project["citations"]:
        for citation in project["citations"]:
            lines.append(f"- [{citation['citationId']}] {citation['title']} - `{citation['path']}` ({citation['lifecycleGroup']})")
            if citation.get("summary"):
                lines.append(f"  - Summary: {citation['summary']}")
    else:
        lines.append("- No Project Plane citations returned.")
    lines.extend(["", "## Session GraphRAG Evidence", ""])
    sessions = payload["planes"]["sessions"]
    if sessions["citations"]:
        for citation in sessions["citations"]:
            lines.append(f"- [{citation['citationId']}] {citation.get('topic')} - `{citation.get('citation')}` ({citation.get('status')})")
    else:
        lines.append("- No session citations returned.")
    lines.extend(["", "## RAG-O Routes And Actions", ""])
    for route in payload["planes"]["operations"]["routes"]:
        write_marker = "write" if route.get("writes") else "read"
        lines.append(f"- {route['plane']} [{write_marker}]: `{route['command']}`")
    if payload["planes"]["operations"]["actionQueue"]:
        lines.extend(["", "Action queue:", ""])
        for item in payload["planes"]["operations"]["actionQueue"]:
            command = item.get("command") or "; ".join(item.get("commands") or [])
            lines.append(f"- [{item.get('priority')}] {item.get('reason')} `{command}`")
    lines.extend(["", "## Warnings", ""])
    if payload["warnings"]:
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    else:
        lines.append("- none")
    lines.extend(["", "## Next Reads", ""])
    if payload["nextReads"]:
        for item in payload["nextReads"]:
            lines.append(f"- {item['kind']}: `{item['target']}` - {item['reason']}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _build_cross_pack(
    project: Path,
    query: str,
    scope: str,
    limit: int,
    include_archived: bool,
    include_legacy: bool,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 10), 50))
    scope = (scope or "general").strip().lower()
    dev_pack = _build_rag_pack(project, query, scope, limit, include_archived)
    project_payload = _project_selection(project, scope, query, limit, include_legacy)
    session_pack = _session_packet_payload(project, topic=query, mode="", status="all", limit=limit)
    op_index = _op_index_payload(project, scope=scope, topic=query)
    dev_citations = _dev_citations(dev_pack)
    project_citations = _project_citations(project_payload)
    session_citations = _session_citations(session_pack)
    payload = {
        "ok": True,
        "packetType": CROSS_PLANE_PACKET_TYPE,
        "query": query,
        "scope": scope,
        "generatedAt": _now_iso(),
        "graphContractVersion": GRAPH_CONTRACT_VERSION,
        "mode": "federated_read_only_no_sync_no_hidden_writes",
        "sourceOfTruth": False,
        "planes": {
            "dev": {
                "available": True,
                "citations": dev_citations,
                "edgesUsed": dev_pack.get("edgesUsed", []),
                "retrievalSignals": dev_pack.get("retrieval", {}).get("signalsUsed", []),
                "semanticAdapter": dev_pack.get("retrieval", {}).get("semanticAdapter", {}),
                "excludedCounts": dev_pack.get("excludedCounts", {}),
                "demotionCounts": dev_pack.get("demotionCounts", {}),
            },
            "project": {
                "present": project_payload.get("present", False),
                "citations": project_citations,
                "contextMarkdown": project_payload.get("contextMarkdown", ""),
                "syncPolicy": "manual_explicit_no_pull_no_push",
            },
            "sessions": {
                "available": True,
                "citations": session_citations,
                "edgesUsed": session_pack.get("edgesUsed", []),
                "excludedCount": session_pack.get("excludedCount", 0),
                "sessionViews": session_pack.get("sessionViews", {}),
            },
            "operations": {
                "alias": "RAG-O",
                "ragEngine": False,
                "routes": op_index.get("routes", []),
                "actionQueue": op_index.get("actionQueue", []),
                "indexHealth": op_index.get("indexHealth", {}),
                "featureState": op_index.get("featureState", {}),
                "workingTree": op_index.get("workingTree", {}),
            },
        },
    }
    payload["citations"] = dev_citations + project_citations + session_citations
    payload["evidenceRefs"] = [
        dict(citation.get("evidenceRef") or {})
        for citation in dev_citations
        if citation.get("evidenceRef")
    ]
    payload["warnings"] = _warnings(dev_pack, project_payload, session_pack, op_index)
    payload["nextReads"] = _next_reads(dev_pack, project_citations, session_pack, op_index)
    payload["packetMarkdown"] = _packet_markdown(payload)
    scrubbed_payload, privacy_receipt = scrub_value(payload)
    scrubbed_payload["privacyReceipt"] = privacy_receipt
    return scrubbed_payload


def _write_output(project: Path, output: Path | None, markdown: str) -> str:
    if output is None:
        return ""
    output_path = work_features.resolve_output_path(project, output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return _relative_path(project, output_path)


def cmd_memory_cross_pack(
    project: Path,
    query: str,
    scope: str = "general",
    limit: int = 10,
    include_archived: bool = False,
    include_legacy: bool = False,
    output: Path | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_cross_pack_memory_not_initialized", message)
        return 1
    query = query.strip()
    if not query:
        _print_json_error_or_text(json_output, "cross_pack_query_required", "cross-pack query is required")
        return 1
    payload = _build_cross_pack(project, query, scope, limit, include_archived, include_legacy)
    try:
        output_path = _write_output(project, output, payload["packetMarkdown"])
    except ValueError as exc:
        _print_json_error_or_text(json_output, "cross_pack_output_invalid", str(exc))
        return 1
    if output_path:
        payload["outputPath"] = output_path
    text = payload["packetMarkdown"] + (f"\nWritten: {output_path}" if output_path else "")
    _print_json_or_text(json_output, payload, text)
    return 0


__all__ = ["CROSS_PLANE_PACKET_TYPE", "cmd_memory_cross_pack"]
