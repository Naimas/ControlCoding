"""Query-first Project Plane surface for embedded ControlWork."""

from __future__ import annotations

import json
from pathlib import Path


def _features():
    from . import work_features

    return work_features


def _query_command_prefix(surface: str) -> dict[str, str]:
    if surface == "embedded":
        return {
            "retrieve": "python scripts\\cc.py memory work-retrieve",
            "rag": "python scripts\\cc.py memory work-rag-pack",
            "graph": "python scripts\\cc.py memory work-graph",
        }
    return {
        "retrieve": "python scripts\\cw.py retrieve",
        "rag": "python scripts\\cw.py rag-pack",
        "graph": "python scripts\\cw.py graph",
    }


def _query_workflow_commands(
    project: Path,
    query: str,
    matches: list[dict],
    surface: str,
    path_to: str = "",
    max_depth: int = 3,
    include_suggestions: bool = False,
) -> dict:
    prefixes = _query_command_prefix(surface)
    project_arg = json.dumps(str(project))
    query_arg = json.dumps(query)
    explain_targets = [
        {
            "selector": str(match.get("path", "")),
            "title": str(match.get("title", "")),
            "command": f"{prefixes['graph']} explain {json.dumps(str(match.get('path', '')))} --project-root {project_arg}",
        }
        for match in matches[:3]
        if str(match.get("path", "")).strip()
    ]
    path_command = ""
    if path_to and explain_targets:
        path_command = (
            f"{prefixes['graph']} path {json.dumps(explain_targets[0]['selector'])} "
            f"{json.dumps(path_to)} --project-root {project_arg} --max-depth {max_depth}"
        )
        if include_suggestions:
            path_command += " --include-suggestions"
    return {
        "surface": surface,
        "commands": [
            f"{prefixes['retrieve']} {query_arg} --project-root {project_arg}",
            f"{prefixes['rag']} {query_arg} --project-root {project_arg}",
            *[item["command"] for item in explain_targets[:2]],
            *([path_command] if path_command else []),
        ],
        "graphExplainTargets": explain_targets,
        "graphPathCommand": path_command,
        "externalCalls": {
            "aiCalls": False,
            "networkCalls": False,
            "subprocessCalls": False,
        },
        "projectionPolicy": "Query results, GraphRAG packets, and graph explanations are projections or review evidence. CONTROLWORK.md and reviewed .controlwork/memory entries remain authoritative.",
    }


def _query_top_matches(matches: list[dict]) -> list[dict]:
    return [
        {
            "title": item.get("title", ""),
            "path": item.get("path", ""),
            "citation": item.get("citation", ""),
            "lifecycle": item.get("lifecycle", ""),
            "score": item.get("score", 0),
            "reasons": item.get("reasons", []),
            "demotions": item.get("demotions", []),
        }
        for item in matches[:5]
    ]


def _build_query_markdown(payload: dict) -> str:
    features = _features()
    lines = [
        f"# ControlWork Query Packet: {payload['query']}",
        "",
        f"- **Generated**: {features.utc_iso()}",
        f"- **Surface**: {payload['queryWorkflow']['surface']}",
        f"- **Graph Contract**: {payload['contractVersion']}",
        "- **Boundary**: Local Project Plane only. No AI calls, network calls, or subprocess calls.",
        "",
        "## Top Matches",
        "",
    ]
    top_matches = payload.get("topMatches", [])
    if top_matches:
        for index, match in enumerate(top_matches, start=1):
            reasons = "; ".join(match.get("reasons", [])) or "no reason recorded"
            lines.append(
                f"- [{index}] `{match.get('citation') or match.get('path')}` - "
                f"{match.get('title', '')} ({match.get('lifecycle', '')}, score {match.get('score', 0)}): {reasons}"
            )
    else:
        lines.append("- No Project Plane matches found.")
    lines.extend(["", "## Graph Explain Targets", ""])
    targets = payload.get("queryWorkflow", {}).get("graphExplainTargets", [])
    if targets:
        for target in targets:
            lines.append(f"- `{target['selector']}` - {target['command']}")
    else:
        lines.append("- No graph explain target available from retrieval.")
    if payload.get("graphPath"):
        lines.extend(["", "## Graph Path Probe", ""])
        graph_path = payload["graphPath"]
        if graph_path.get("ok"):
            lines.append(f"- Path found: {graph_path.get('pathFound')} with {graph_path.get('hops', 0)} hop(s).")
        else:
            lines.append(f"- Path probe failed: {graph_path.get('error', 'unknown error')}")
    lines.extend(["", "## Next Commands", ""])
    for command in payload.get("queryWorkflow", {}).get("commands", []):
        lines.append(f"- `{command}`")
    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("ragPack", {}).get("warnings", [])
    if warnings:
        for warning in warnings:
            lines.append(f"- [{warning.get('citationId', 'WARN')}] {warning.get('message', '')}")
    else:
        lines.append("- No lifecycle warnings in selected citations.")
    return "\n".join(lines).rstrip() + "\n"


def build_query_payload(
    project: Path,
    query: str,
    limit: int = 10,
    include_legacy: bool = False,
    path_to: str = "",
    max_depth: int = 3,
    include_suggestions: bool = False,
    surface: str = "standalone",
) -> dict:
    features = _features()
    retrieval = features.retrieve_matches(project, query, limit=limit, include_legacy=include_legacy)
    rag_pack = features.build_rag_pack_payload(project, query, limit=limit, include_legacy=include_legacy)
    matches = retrieval.get("matches", [])
    workflow = _query_workflow_commands(
        project,
        query,
        matches,
        surface=surface,
        path_to=path_to,
        max_depth=max_depth,
        include_suggestions=include_suggestions,
    )
    graph_path = None
    if path_to and matches:
        graph_path = features.build_graph_path_payload(
            project,
            str(matches[0].get("path", "")),
            path_to,
            max_depth=max_depth,
            include_suggestions=include_suggestions,
        )
    payload = {
        "ok": True,
        "query": query,
        "contractVersion": features.GRAPH_CONTRACT_VERSION,
        "topMatches": _query_top_matches(matches),
        "retrieval": retrieval,
        "ragPack": rag_pack,
        "graphPath": graph_path,
        "queryWorkflow": workflow,
    }
    payload["packetMarkdown"] = _build_query_markdown(payload)
    return payload


def cmd_query(args) -> int:
    features = _features()
    project = args.project_root.resolve()
    json_output = bool(getattr(args, "json_output", False))
    payload = build_query_payload(
        project,
        args.query,
        limit=args.limit,
        include_legacy=args.include_legacy,
        path_to=getattr(args, "path_to", ""),
        max_depth=getattr(args, "max_depth", 3),
        include_suggestions=getattr(args, "include_suggestions", False),
        surface=getattr(args, "command_surface", "standalone"),
    )
    if getattr(args, "output", None):
        try:
            target = features.resolve_output_path(project, args.output)
        except ValueError as exc:
            error_payload = {"ok": False, "error": str(exc)}
            if json_output and getattr(args, "stdout", False):
                error_payload["message"] = str(exc)
            print(json.dumps(error_payload, indent=2))
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload["packetMarkdown"], encoding="utf-8")
        payload["outputPath"] = features.rel(project, target)
    if getattr(args, "stdout", False) and not json_output:
        print(payload["packetMarkdown"], end="")
    else:
        print(json.dumps(payload, indent=2))
    return 0
