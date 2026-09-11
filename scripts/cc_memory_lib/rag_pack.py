"""Dev GraphRAG packet builder for ControlCoding development memory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence import annotate_evidence_ref, evidence_refs_for_records
from .graph import GRAPH_CONTRACT_VERSION
from .privacy import scrub_value
from .retrieve import _retrieve_payload
from .store import _now_iso, _print_json_error_or_text, _print_json_or_text, _relative_path, _require_initialized

WARNING_LIFECYCLES = {"stale", "conflicting", "needs_review", "legacy", "superseded"}


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


def _citation_id(index: int) -> str:
    return f"C{index}"


def _unique_edges(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    edges: list[dict[str, Any]] = []
    for match in matches:
        citation = str(match.get("citationId") or "")
        for edge in match.get("edgesUsed", []):
            key = (
                str(edge.get("sourceId") or edge.get("from") or ""),
                str(edge.get("targetId") or edge.get("to") or ""),
                str(edge.get("type") or ""),
                str(edge.get("source") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "citationId": citation,
                "type": str(edge.get("type") or ""),
                "source": str(edge.get("source") or ""),
                "confidence": str(edge.get("confidence") or ""),
                "status": str(edge.get("status") or ""),
                "sourceId": str(edge.get("sourceId") or edge.get("from") or ""),
                "targetId": str(edge.get("targetId") or edge.get("to") or ""),
                "depth": edge.get("depth", ""),
                "reason": str(edge.get("reason") or ""),
            })
    return edges


def _warnings(matches: list[dict[str, Any]], retrieval: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for match in matches:
        lifecycle = str(match.get("lifecycle") or "")
        if lifecycle in WARNING_LIFECYCLES:
            warnings.append({
                "citationId": match.get("citationId", ""),
                "kind": "lifecycle",
                "lifecycle": lifecycle,
                "message": f"{match.get('title')} is {lifecycle}; treat it as warning material until reviewed.",
            })
        for demotion in match.get("demotions", []):
            if "application-owned" in str(demotion):
                warnings.append({
                    "citationId": match.get("citationId", ""),
                    "kind": "ownership",
                    "message": "Application-owned memory is referenced only as project context.",
                })
            if "Project Plane" in str(demotion):
                warnings.append({
                    "citationId": match.get("citationId", ""),
                    "kind": "plane",
                    "message": "Project Plane records should be verified through ControlWork commands.",
                })
    vector_warning = retrieval.get("vectorIndex", {}).get("warning")
    if vector_warning:
        warnings.append({"kind": "vector", "message": str(vector_warning)})
    return warnings


def _next_reads(matches: list[dict[str, Any]], retrieval: dict[str, Any], scope: str, query: str) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for match in matches:
        path = str(match.get("path") or "")
        if path and path not in seen_paths:
            seen_paths.add(path)
            reads.append({
                "kind": "source",
                "target": path,
                "reason": f"Read source for citation {match.get('citationId')}.",
            })
        if len(reads) >= 6:
            break
    first_path = next((str(match.get("path") or "") for match in matches if match.get("path")), "")
    if first_path:
        reads.append({
            "kind": "command",
            "command": f"python scripts/cc.py memory graph around --project-root . {first_path} --depth 2",
            "reason": "Inspect graph neighborhood for the strongest source.",
        })
    if not retrieval.get("vectorIndex", {}).get("used"):
        reads.append({
            "kind": "command",
            "command": "python scripts/cc.py memory vector rebuild --project-root .",
            "reason": "Rebuild sparse vectors before relying on hybrid retrieval.",
        })
    if scope == "project":
        reads.append({
            "kind": "command",
            "command": f"python scripts/cc.py memory work-context-pack --project-root . --scope general --topic \"{query}\"",
            "reason": "Use ControlWork for canonical Project Plane context.",
        })
    return reads[:10]


def _annotate_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for index, match in enumerate(matches, start=1):
        item = dict(match)
        item["citationId"] = _citation_id(index)
        item = annotate_evidence_ref(item)
        annotated.append(item)
    return annotated


def _packet_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# ControlCoding GraphRAG Packet: {payload['query']}",
        "",
        f"- **Generated**: {payload['generatedAt']}",
        f"- **Scope**: {payload['scope']}",
        f"- **Graph Contract**: {payload['graphContractVersion']}",
        f"- **Retrieval Signals**: {', '.join(payload['retrieval']['signalsUsed'])}",
        "",
        "## AI Use Contract",
        "",
        "- Use this packet for ControlCoding development context only.",
        "- Treat citations as pointers to local project sources, not as copied source truth.",
        "- Keep Dev Plane, Project Plane, and application-owned memory separate.",
        "- Treat stale, conflicting, legacy, superseded, and needs_review material as warning material.",
        "",
        "## Source Citations",
        "",
    ]
    if not payload["citations"]:
        lines.append("- No citations returned.")
    for citation in payload["citations"]:
        location = citation["path"] or citation["id"]
        if citation.get("headingPath"):
            location = f"{location} - {citation['headingPath']}"
        lines.append(
            f"- [{citation['citationId']}] {location} ({citation['recordType']}, {citation['lifecycle']}, score {citation['score']:.2f})"
        )
        lines.append(f"  - Evidence ref: `{citation['nodeId']}`")
    lines.extend(["", "## Evidence Summary", ""])
    for citation in payload["citations"]:
        lines.extend([
            f"### [{citation['citationId']}] {citation['title']}",
            "",
            f"- Citation: `{citation['citation']}`",
            f"- Evidence ref: `{citation['nodeId']}`",
            f"- Drill-down: `{citation['evidenceRef']['drillDownCommand']}`",
            f"- Type: {citation['recordType']} / {citation['type']}",
            f"- Lifecycle: {citation['lifecycle']}",
            f"- Score: {citation['score']:.2f}",
        ])
        if citation["reasons"]:
            lines.append("- Reasons: " + "; ".join(str(reason) for reason in citation["reasons"][:6]))
        if citation["demotions"]:
            lines.append("- Demotions: " + "; ".join(str(reason) for reason in citation["demotions"][:6]))
        lines.append("")
    lines.extend(["## Edges Used", ""])
    if not payload["edgesUsed"]:
        lines.append("- No graph edges contributed to selected citations.")
    for edge in payload["edgesUsed"]:
        status = f", status {edge['status']}" if edge.get("status") else ""
        lines.append(
            f"- [{edge['citationId']}] {edge['type']} ({edge['source']}, confidence {edge['confidence']}{status})"
        )
        if edge.get("reason"):
            lines.append(f"  - Reason: {edge['reason']}")
    lines.extend(["", "## Warnings", ""])
    if not payload["warnings"]:
        lines.append("- No stale or conflict warnings in selected citations.")
    for warning in payload["warnings"]:
        prefix = f"[{warning.get('citationId')}] " if warning.get("citationId") else ""
        lines.append(f"- {prefix}{warning['message']}")
    lines.extend(["", "## Excluded Records", ""])
    excluded = payload["excludedRecords"]
    if not excluded:
        lines.append("- None reported.")
    for item in excluded[:12]:
        location = item.get("path") or item.get("id")
        lines.append(f"- {item['category']}: {item['title']} `{location}` - {item['reason']}")
    lines.extend(["", "## Next Reads", ""])
    if not payload["nextReads"]:
        lines.append("- None.")
    for item in payload["nextReads"]:
        target = item.get("target") or item.get("command")
        lines.append(f"- {item['kind']}: `{target}` - {item['reason']}")
    lines.append("")
    return "\n".join(lines)


def _build_rag_pack(
    project: Path,
    query: str,
    scope: str,
    limit: int,
    include_archived: bool,
) -> dict[str, Any]:
    retrieval = _retrieve_payload(project, query, scope, limit, include_archived)
    matches = _annotate_matches(retrieval["matches"])
    edges_used = _unique_edges(matches)
    warnings = _warnings(matches, retrieval)
    payload = {
        "ok": True,
        "query": query,
        "scope": scope,
        "generatedAt": _now_iso(),
        "graphContractVersion": GRAPH_CONTRACT_VERSION,
        "citations": matches,
        "evidenceRefs": evidence_refs_for_records(matches),
        "edgesUsed": edges_used,
        "warnings": warnings,
        "excludedRecords": retrieval["exclusionReport"]["items"],
        "excludedCounts": retrieval["exclusionReport"]["counts"],
        "demotionCounts": retrieval["demotionReport"]["counts"],
        "nextReads": _next_reads(matches, retrieval, scope, query),
        "retrieval": retrieval,
    }
    payload["packetMarkdown"] = _packet_markdown(payload)
    scrubbed_payload, privacy_receipt = scrub_value(payload)
    scrubbed_payload["privacyReceipt"] = privacy_receipt
    return scrubbed_payload


def _write_output(project: Path, output: Path | None, markdown: str) -> str:
    if output is None:
        return ""
    output_path = _resolve_output_path(project, output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return _relative_path(project, output_path)


def cmd_memory_rag_pack(
    project: Path,
    query: str,
    scope: str = "general",
    limit: int = 10,
    include_archived: bool = False,
    output: Path | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_or_text(
            json_output,
            {
                "ok": False,
                "error": "rag_pack_memory_not_initialized",
                "message": message,
            },
            f"Error: {message}",
        )
        return 1
    query = query.strip()
    if not query:
        message = "rag-pack query is required"
        _print_json_or_text(
            json_output,
            {
                "ok": False,
                "error": "rag_pack_query_required",
                "message": message,
            },
            f"Error: {message}",
        )
        return 1
    limit = max(1, min(int(limit or 10), 50))
    scope = (scope or "general").strip().lower()
    payload = _build_rag_pack(project, query, scope, limit, include_archived)
    try:
        output_path = _write_output(project, output, payload["packetMarkdown"])
    except ValueError as exc:
        _print_json_error_or_text(json_output, "rag_pack_output_invalid", str(exc))
        return 1
    if output_path:
        payload["outputPath"] = output_path
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(payload["packetMarkdown"])
        if output_path:
            print(f"Written: {output_path}")
    return 0
