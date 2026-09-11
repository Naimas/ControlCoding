"""Static dashboard and Project Map projections for ControlWork."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path


def _features():
    from . import work_features

    return work_features


def recent_checkpoints(project: Path, limit: int = 5) -> list[dict]:
    features = _features()
    root = project / features.CHECKPOINT_ROOT
    if not root.exists():
        return []
    items = []
    for path in sorted(root.glob("*.md"), reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        title = path.stem
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except OSError:
            pass
        items.append({
            "title": title,
            "path": features.rel(project, path),
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
        if len(items) >= max(1, limit):
            break
    return items


def _dashboard_entry_summary(entry: dict) -> dict:
    return {
        "title": entry.get("title", ""),
        "path": entry.get("path", ""),
        "area": entry.get("area", ""),
        "lifecycle": entry.get("lifecycle", ""),
        "category": entry.get("category", ""),
        "source": entry.get("source", ""),
    }


def build_dashboard_payload(project: Path, limit: int = 20) -> dict:
    features = _features()
    entries = features.iter_entries(project)
    active_decisions = [
        entry for entry in entries
        if entry.get("area") == "decisions" and entry.get("lifecycle") == "active"
    ]
    open_questions = [
        entry for entry in entries
        if entry.get("lifecycle") == "needs_review"
        or "?" in (entry.get("title", "") + entry.get("body", ""))
    ]
    needs_review = [entry for entry in entries if entry.get("lifecycle") == "needs_review"]
    needs_review_sources = [
        entry for entry in needs_review
        if entry.get("area") == "sources" or entry.get("source")
    ]
    stale_entries = [
        entry for entry in entries
        if entry.get("lifecycle") in {"legacy", "superseded"}
    ]
    scan_payload = features.read_file_index(project)
    scan_summary = features.scan_review_summary(scan_payload, limit=limit, project=project) if scan_payload else {}
    graph = features.portable_graph(project)
    context_root = project / features.CONTEXT_PACKET_ROOT
    context_packets = sorted(context_root.glob("*.md")) if context_root.exists() else []
    views_index = project / features.MEMORY_ROOT / "views" / "index.md"
    checkpoint_items = recent_checkpoints(project, limit=5)
    handoff_gaps = []
    if not views_index.exists():
        handoff_gaps.append("missing generated views")
    if not context_packets:
        handoff_gaps.append("missing context packet")
    if not checkpoint_items:
        handoff_gaps.append("missing checkpoint")
    if needs_review:
        handoff_gaps.append("needs-review memory")
    if scan_summary.get("pendingReviewCount", 0):
        handoff_gaps.append("pending scan review")
    readiness = {
        "status": "ready" if not handoff_gaps else "attention",
        "gaps": handoff_gaps,
        "hasViews": views_index.exists(),
        "contextPackets": len(context_packets),
        "latestCheckpoint": checkpoint_items[0] if checkpoint_items else None,
    }
    node_counts: dict[str, int] = {}
    for node in graph["nodes"]:
        node_type = str(node.get("type", "unknown"))
        node_counts[node_type] = node_counts.get(node_type, 0) + 1
    return {
        "ok": True,
        "generatedAt": features.utc_iso(),
        "projectRoot": str(project),
        "summary": {
            "entries": len(entries),
            "activeDecisions": len(active_decisions),
            "openQuestions": len(open_questions),
            "needsReview": len(needs_review),
            "needsReviewSources": len(needs_review_sources),
            "staleEntries": len(stale_entries),
            "recentCheckpoints": len(checkpoint_items),
            "pendingScanReview": scan_summary.get("pendingReviewCount", 0),
        },
        "handoffReadiness": readiness,
        "activeDecisions": [_dashboard_entry_summary(item) for item in active_decisions[: max(1, limit)]],
        "openQuestions": [_dashboard_entry_summary(item) for item in open_questions[: max(1, limit)]],
        "needsReviewSources": [_dashboard_entry_summary(item) for item in needs_review_sources[: max(1, limit)]],
        "staleEntries": [_dashboard_entry_summary(item) for item in stale_entries[: max(1, limit)]],
        "recentCheckpoints": checkpoint_items,
        "scanReview": {
            "pendingReviewCount": scan_summary.get("pendingReviewCount", 0),
            "byReviewStatus": scan_summary.get("byReviewStatus", {}) if scan_summary else {},
            "reviewQueue": scan_summary.get("reviewQueue", [])[: max(1, limit)] if scan_summary else [],
        },
        "graphHealth": {
            "contractVersion": graph["contractVersion"],
            "nodeCounts": dict(sorted(node_counts.items())),
            "edges": len(graph["edges"]),
            "suggestions": len(graph["suggestions"]),
            "suggestionCounts": graph.get("suggestionCounts", {}),
        },
        "externalCalls": {
            "aiCalls": False,
            "networkCalls": False,
            "subprocessCalls": False,
        },
        "projectionPolicy": "Dashboard output is a derived Project Plane projection. CONTROLWORK.md and reviewed .controlwork/memory entries remain authoritative.",
    }


def refresh_scan_state(project: Path, limit: int = 50) -> dict:
    """Refresh local file scan artifacts for an explicit dashboard generation."""
    features = _features()
    features.ensure_feature_layout(project)
    scan_payload = features.refresh_file_index(project)
    index_path = features.file_index_path(project)
    analysis = features.build_scan_analysis(project, scan_payload, limit=limit)
    analysis_path = features.scan_analysis_path(project)
    features.write_json(analysis_path, analysis)
    understanding = features.write_project_understanding(
        project,
        scan_payload,
        analysis=analysis,
        distribution="embedded_controlcoding",
    )
    base_document = features.ensure_project_base_document(
        project,
        purpose=features.project_understanding_summary(understanding),
        understanding=understanding,
    )
    return {
        "enabled": True,
        "trigger": "explicit_dashboard_generation",
        "indexPath": features.rel(project, index_path),
        "analysisPath": features.rel(project, analysis_path),
        "projectUnderstandingPath": features.rel(project, features.project_understanding_path(project)),
        "baseDocument": base_document,
        "summary": scan_payload.get("summary", {}),
        "analysisSummary": analysis.get("summary", {}),
        "projectUnderstanding": {
            "status": understanding.get("status", "needs_human_confirmation"),
            "selectedKind": understanding.get("selectedKind", "archive_organization"),
            "primaryPurpose": understanding.get("primaryPurpose", "confirm_project_goal"),
            "questions": understanding.get("questions", []),
        },
        "policy": "The local cockpit reads this generated state. The static UI does not execute hidden local commands.",
    }


def _dashboard_markdown_table(entries: list[dict], columns: tuple[str, ...]) -> list[str]:
    if not entries:
        return ["_None._"]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for entry in entries:
        row = []
        for column in columns:
            value = str(entry.get(column, ""))
            if column == "path" and value:
                value = f"`{value}`"
            row.append(value.replace("|", "\\|"))
        lines.append("| " + " | ".join(row) + " |")
    return lines


def dashboard_markdown(payload: dict) -> str:
    readiness = payload["handoffReadiness"]
    summary = payload["summary"]
    lines = [
        "# ControlWork Static Dashboard",
        "",
        f"- **Generated**: {payload['generatedAt']}",
        f"- **Handoff Readiness**: {readiness['status']}",
        f"- **Entries**: {summary['entries']}",
        f"- **Active Decisions**: {summary['activeDecisions']}",
        f"- **Open Questions**: {summary['openQuestions']}",
        f"- **Needs Review**: {summary['needsReview']}",
        f"- **Stale Entries**: {summary['staleEntries']}",
        "",
        "## Readiness Gaps",
        "",
    ]
    if readiness["gaps"]:
        lines.extend(f"- {gap}" for gap in readiness["gaps"])
    else:
        lines.append("- None.")
    for title, key in (
        ("Active Decisions", "activeDecisions"),
        ("Open Questions", "openQuestions"),
        ("Needs-Review Sources", "needsReviewSources"),
        ("Stale Entries", "staleEntries"),
        ("Recent Checkpoints", "recentCheckpoints"),
    ):
        lines.extend(["", f"## {title}", ""])
        columns = ("title", "path", "modifiedAt") if key == "recentCheckpoints" else ("title", "path", "lifecycle")
        lines.extend(_dashboard_markdown_table(payload[key], columns))
    lines.extend([
        "",
        "## Graph Health",
        "",
        f"- **Edges**: {payload['graphHealth']['edges']}",
        f"- **Suggestions**: {payload['graphHealth']['suggestions']}",
        f"- **Node Counts**: `{json.dumps(payload['graphHealth']['nodeCounts'], sort_keys=True)}`",
        "",
        "## Projection Policy",
        "",
        payload["projectionPolicy"],
        "",
    ])
    return "\n".join(lines)


def _html_rows(entries: list[dict], columns: tuple[str, ...]) -> str:
    if not entries:
        return "<p class=\"empty\">None.</p>"
    header = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    rows = []
    for entry in entries:
        cells = []
        for column in columns:
            value = html.escape(str(entry.get(column, "")))
            if column == "path" and value:
                value = f"<code>{value}</code>"
            cells.append(f"<td>{value}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def dashboard_html(payload: dict) -> str:
    summary = payload["summary"]
    readiness = payload["handoffReadiness"]
    cards = "".join(
        f"<div class=\"card\"><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in (
            ("Entries", summary["entries"]),
            ("Active Decisions", summary["activeDecisions"]),
            ("Open Questions", summary["openQuestions"]),
            ("Needs Review", summary["needsReview"]),
            ("Stale Entries", summary["staleEntries"]),
            ("Pending Scan Review", summary["pendingScanReview"]),
        )
    )
    gaps = "".join(f"<li>{html.escape(gap)}</li>" for gap in readiness["gaps"]) or "<li>None.</li>"
    sections = []
    for title, key, columns in (
        ("Active Decisions", "activeDecisions", ("title", "path", "lifecycle")),
        ("Open Questions", "openQuestions", ("title", "path", "lifecycle")),
        ("Needs-Review Sources", "needsReviewSources", ("title", "path", "lifecycle")),
        ("Stale Entries", "staleEntries", ("title", "path", "lifecycle")),
        ("Recent Checkpoints", "recentCheckpoints", ("title", "path", "modifiedAt")),
    ):
        sections.append(
            f"<section><h2>{html.escape(title)}</h2>{_html_rows(payload[key], columns)}</section>"
        )
    return "\n".join([
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>ControlWork Static Dashboard</title>",
        "<style>",
        "body{font-family:Segoe UI,system-ui,sans-serif;margin:0;background:#f7f7f4;color:#1f2933;line-height:1.45}",
        "main{max-width:1180px;margin:0 auto;padding:28px}",
        "header{border-bottom:1px solid #d8ddd7;margin-bottom:20px;padding-bottom:16px}",
        "h1{font-size:28px;margin:0 0 8px} h2{font-size:18px;margin-top:28px}",
        ".muted{color:#667085}.status{display:inline-block;padding:4px 9px;border-radius:6px;background:#e9f5ee;color:#1f6f43;font-weight:600}",
        ".status.attention{background:#fff4db;color:#8a5b00}",
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:18px 0}",
        ".card{background:#fff;border:1px solid #d9dfd8;border-radius:6px;padding:12px}.card span{display:block;color:#667085;font-size:12px}.card strong{font-size:24px}",
        "section{background:#fff;border:1px solid #d9dfd8;border-radius:6px;padding:16px;margin:14px 0}",
        "table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid #edf0ed;padding:7px;text-align:left;vertical-align:top}th{color:#475467;background:#fafafa}",
        "code{font-size:12px}.empty{color:#667085}",
        "</style></head><body><main>",
        "<header>",
        "<h1>ControlWork Static Dashboard</h1>",
        f"<p class=\"muted\">Generated: {html.escape(payload['generatedAt'])}</p>",
        f"<p>Handoff readiness: <span class=\"status {html.escape(readiness['status'])}\">{html.escape(readiness['status'])}</span></p>",
        "</header>",
        f"<div class=\"cards\">{cards}</div>",
        "<section><h2>Readiness Gaps</h2><ul>" + gaps + "</ul></section>",
        *sections,
        "<section><h2>Graph Health</h2>",
        f"<p>Edges: {payload['graphHealth']['edges']}. Suggestions: {payload['graphHealth']['suggestions']}.</p>",
        f"<pre>{html.escape(json.dumps(payload['graphHealth']['nodeCounts'], indent=2, sort_keys=True))}</pre>",
        "</section>",
        f"<p class=\"muted\">{html.escape(payload['projectionPolicy'])}</p>",
        "</main></body></html>",
    ])


def cmd_dashboard(args) -> int:
    features = _features()
    project = args.project_root.resolve()
    output_format = str(args.format or "html")
    scan_refresh = {"enabled": False, "policy": "Scan refresh disabled for this explicit dashboard generation."}
    if not getattr(args, "no_refresh_scan", False):
        scan_refresh = refresh_scan_state(project, limit=getattr(args, "scan_limit", args.limit))

    if output_format == "json":
        from . import work_project_map

        payload = work_project_map.build_project_map_payload(
            project,
            distribution="embedded_controlcoding",
            scan_refresh=scan_refresh,
        )
        validation_errors = work_project_map.validate_project_map_payload(payload)
        if validation_errors:
            print(json.dumps({"ok": False, "errors": validation_errors}, indent=2))
            return 1
        default_name = "project-map.json"
        content = work_project_map.project_map_json(payload)
    else:
        payload = build_dashboard_payload(project, limit=args.limit)
        default_name = "index.md" if output_format == "md" else "index.html"
        content = dashboard_markdown(payload) if output_format == "md" else dashboard_html(payload)

    output = args.output or (Path(".controlwork") / "dashboard" / default_name)
    try:
        target = features.resolve_output_path(project, output)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")

    if output_format == "json":
        result = {
            "ok": True,
            "path": features.rel(project, target),
            "format": output_format,
            "contractVersion": payload["projectMapContractVersion"],
            "documents": len(payload["documents"]),
            "topics": len(payload["topics"]),
            "edges": len(payload["edges"]),
            "sessions": len(payload["sessions"]),
            "externalCalls": payload["externalCalls"],
            "scanRefresh": scan_refresh,
            "projectionPolicy": payload["contract"]["relationPolicy"],
        }
        print(json.dumps(result, indent=2))
        return 0

    result = {
        "ok": True,
        "path": features.rel(project, target),
        "format": output_format,
        "summary": payload["summary"],
        "handoffReadiness": payload["handoffReadiness"],
        "graphHealth": payload["graphHealth"],
        "externalCalls": payload["externalCalls"],
        "scanRefresh": scan_refresh,
        "projectionPolicy": payload["projectionPolicy"],
    }
    print(json.dumps(result, indent=2))
    return 0
