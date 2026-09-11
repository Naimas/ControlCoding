"""Graph operation payloads for the embedded ControlWork Project Plane."""

from __future__ import annotations

import json
from pathlib import Path


def _features():
    from . import work_features

    return work_features

def portable_graph(project: Path) -> dict:
    return _features().portable_graph(project)


def scan_retrieval_warnings(project: Path, query: str, limit: int = 5) -> dict:
    return _features().scan_retrieval_warnings(project, query, limit=limit)


def read_file_index(project: Path) -> dict:
    return _features().read_file_index(project)


def build_scan_analysis(project: Path, payload: dict, limit: int = 50) -> dict:
    return _features().build_scan_analysis(project, payload, limit=limit)


def portable_graph_entries(project: Path) -> list[dict]:
    return _features().portable_graph_entries(project)


def scan_review_summary(payload: dict, limit: int = 20, project: Path | None = None, filter_name: str = "pending") -> dict:
    return _features().scan_review_summary(payload, limit=limit, project=project, filter_name=filter_name)


def resolve_input_path(project: Path, source: Path) -> Path:
    return _features().resolve_input_path(project, source)

def _compact_graph_node(node: dict) -> dict:
    return {
        key: node.get(key, "")
        for key in (
            "id",
            "type",
            "recordType",
            "title",
            "path",
            "area",
            "lifecycle",
            "source",
            "category",
            "headingPath",
            "headingLevel",
        )
        if node.get(key, "") != ""
    }


def _graph_relation_payload(relation: dict, node_by_id: dict[str, dict], kind: str) -> dict:
    source_id = str(relation.get("sourceId", ""))
    target_id = str(relation.get("targetId", ""))
    payload = {
        "id": relation.get("id", ""),
        "kind": kind,
        "type": relation.get("type", ""),
        "status": relation.get("status", ""),
        "confidence": relation.get("confidence", ""),
        "reason": relation.get("reason", ""),
        "sourceId": source_id,
        "targetId": target_id,
        "source": _compact_graph_node(node_by_id.get(source_id, {})),
        "target": _compact_graph_node(node_by_id.get(target_id, {})),
        "canonical": kind == "edge" and relation.get("status") == "canonical",
    }
    if relation.get("fromSuggestionId"):
        payload["fromSuggestionId"] = relation.get("fromSuggestionId")
    if relation.get("reviewReason"):
        payload["reviewReason"] = relation.get("reviewReason")
    return payload


def _normalize_graph_selector(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lower()


def _graph_node_matches_selector(node: dict, selector: str) -> bool:
    normalized = _normalize_graph_selector(selector)
    if not normalized:
        return False
    fields = [
        str(node.get("id", "")),
        str(node.get("path", "")),
        str(node.get("title", "")),
    ]
    return any(_normalize_graph_selector(field) == normalized for field in fields)


def _find_graph_node(graph: dict, selector: str) -> dict:
    for node in graph["nodes"]:
        if _graph_node_matches_selector(node, selector):
            return node
    normalized = _normalize_graph_selector(selector)
    suffix_matches = [
        node
        for node in graph["nodes"]
        if _normalize_graph_selector(str(node.get("path", ""))).endswith(normalized)
    ]
    return suffix_matches[0] if len(suffix_matches) == 1 else {}


def _find_graph_relation(graph: dict, selector: str) -> tuple[str, dict]:
    normalized = _normalize_graph_selector(selector)
    for edge in graph["edges"]:
        if _normalize_graph_selector(str(edge.get("id", ""))) == normalized:
            return "edge", edge
    for suggestion in graph["suggestions"]:
        if _normalize_graph_selector(str(suggestion.get("id", ""))) == normalized:
            return "suggestion", suggestion
    return "", {}


def build_graph_explain_payload(project: Path, selector: str, limit: int = 10) -> dict:
    graph = portable_graph(project)
    node_by_id = {str(node.get("id", "")): node for node in graph["nodes"]}
    relation_kind, relation = _find_graph_relation(graph, selector)
    if relation:
        payload = _graph_relation_payload(relation, node_by_id, relation_kind)
        warnings = []
        if relation_kind == "suggestion" and relation.get("status") == "suggested":
            warnings.append({
                "code": "suggested_relation_not_canonical",
                "message": "This relation is a suggestion and has not been accepted as canonical evidence.",
            })
        return {
            "ok": True,
            "contractVersion": graph["contractVersion"],
            "selector": selector,
            "matched": {"kind": relation_kind, "id": relation.get("id", "")},
            "explanation": {
                "relation": payload,
                "warnings": warnings,
            },
            "portableOnly": True,
        }

    node = _find_graph_node(graph, selector)
    if not node:
        return {"ok": False, "error": "graph selector not found", "selector": selector}

    node_id = str(node.get("id", ""))
    touching_edges = [
        _graph_relation_payload(edge, node_by_id, "edge")
        for edge in graph["edges"]
        if edge.get("sourceId") == node_id or edge.get("targetId") == node_id
    ]
    touching_suggestions = [
        _graph_relation_payload(suggestion, node_by_id, "suggestion")
        for suggestion in graph["suggestions"]
        if suggestion.get("sourceId") == node_id or suggestion.get("targetId") == node_id
    ]
    warnings = []
    if any(item.get("status") == "suggested" for item in touching_suggestions):
        warnings.append({
            "code": "node_has_unreviewed_suggestions",
            "message": "Some related graph links are suggestions and not canonical evidence.",
        })
    scan_query = " ".join(str(node.get(key, "")) for key in ("title", "path", "source", "category"))
    scan_warnings = scan_retrieval_warnings(project, scan_query, limit=3) if scan_query.strip() else []
    return {
        "ok": True,
        "contractVersion": graph["contractVersion"],
        "selector": selector,
        "matched": {"kind": "node", "id": node_id},
        "explanation": {
            "node": _compact_graph_node(node),
            "edges": touching_edges[: max(1, limit)],
            "suggestions": touching_suggestions[: max(1, limit)],
            "warnings": warnings,
            "scanEvidenceWarnings": scan_warnings,
        },
        "portableOnly": True,
    }


def cmd_graph_explain(args) -> int:
    payload = build_graph_explain_payload(args.project_root.resolve(), args.selector, limit=args.limit)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def _graph_traversal_relations(graph: dict, include_suggestions: bool) -> list[tuple[str, dict]]:
    relations = [("edge", edge) for edge in graph["edges"]]
    if include_suggestions:
        for suggestion in graph["suggestions"]:
            if suggestion.get("status") != "rejected":
                relations.append(("suggestion", suggestion))
    return relations


def build_graph_path_payload(
    project: Path,
    source_selector: str,
    target_selector: str,
    max_depth: int = 3,
    include_suggestions: bool = False,
) -> dict:
    graph = portable_graph(project)
    node_by_id = {str(node.get("id", "")): node for node in graph["nodes"]}
    source = _find_graph_node(graph, source_selector)
    target = _find_graph_node(graph, target_selector)
    if not source or not target:
        return {
            "ok": False,
            "error": "source or target graph selector not found",
            "sourceSelector": source_selector,
            "targetSelector": target_selector,
            "sourceFound": bool(source),
            "targetFound": bool(target),
        }
    source_id = str(source.get("id", ""))
    target_id = str(target.get("id", ""))
    depth_limit = max(0, min(8, int(max_depth)))
    if source_id == target_id:
        return {
            "ok": True,
            "contractVersion": graph["contractVersion"],
            "pathFound": True,
            "includeSuggestions": include_suggestions,
            "maxDepth": depth_limit,
            "hops": 0,
            "nodes": [_compact_graph_node(source)],
            "relations": [],
            "warnings": [],
            "portableOnly": True,
        }

    adjacency: dict[str, list[tuple[str, dict, str]]] = {}
    for kind, relation in _graph_traversal_relations(graph, include_suggestions):
        left = str(relation.get("sourceId", ""))
        right = str(relation.get("targetId", ""))
        if not left or not right:
            continue
        adjacency.setdefault(left, []).append((kind, relation, right))
        adjacency.setdefault(right, []).append((kind, relation, left))

    queue: list[tuple[str, list[str], list[tuple[str, dict, str, str]]]] = [(source_id, [source_id], [])]
    visited = {source_id}
    found_nodes: list[str] = []
    found_relations: list[tuple[str, dict, str, str]] = []
    while queue:
        current, path_nodes, path_relations = queue.pop(0)
        if len(path_relations) >= depth_limit:
            continue
        for kind, relation, neighbor in adjacency.get(current, []):
            if neighbor in visited:
                continue
            next_nodes = [*path_nodes, neighbor]
            next_relations = [*path_relations, (kind, relation, current, neighbor)]
            if neighbor == target_id:
                found_nodes = next_nodes
                found_relations = next_relations
                queue = []
                break
            visited.add(neighbor)
            queue.append((neighbor, next_nodes, next_relations))

    warnings = []
    if found_relations and any(kind == "suggestion" for kind, _, _, _ in found_relations):
        warnings.append({
            "code": "suggested_relations_in_path",
            "message": "This path uses suggested relations. Treat it as evidence for review, not canonical truth.",
        })
    return {
        "ok": True,
        "contractVersion": graph["contractVersion"],
        "pathFound": bool(found_nodes),
        "includeSuggestions": include_suggestions,
        "maxDepth": depth_limit,
        "source": _compact_graph_node(source),
        "target": _compact_graph_node(target),
        "hops": len(found_relations),
        "nodes": [_compact_graph_node(node_by_id.get(node_id, {})) for node_id in found_nodes],
        "relations": [
            {
                **_graph_relation_payload(relation, node_by_id, kind),
                "traversal": {"fromId": from_id, "toId": to_id},
            }
            for kind, relation, from_id, to_id in found_relations
        ],
        "warnings": warnings,
        "portableOnly": True,
    }


def cmd_graph_path(args) -> int:
    payload = build_graph_path_payload(
        args.project_root.resolve(),
        args.source_selector,
        args.target_selector,
        max_depth=args.max_depth,
        include_suggestions=args.include_suggestions,
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def build_graph_neighbors_payload(
    project: Path,
    selector: str,
    limit: int = 20,
    include_suggestions: bool = False,
    include_chunks: bool = False,
) -> dict:
    graph = portable_graph(project)
    node_by_id = {str(node.get("id", "")): node for node in graph["nodes"]}
    node = _find_graph_node(graph, selector)
    if not node:
        return {"ok": False, "error": "graph selector not found", "selector": selector}
    node_id = str(node.get("id", ""))
    relations: list[tuple[str, dict]] = [("edge", edge) for edge in graph["edges"]]
    if include_suggestions:
        relations.extend(
            ("suggestion", suggestion)
            for suggestion in graph["suggestions"]
            if suggestion.get("status") != "rejected"
        )
    neighbors = []
    for kind, relation in relations:
        source_id = str(relation.get("sourceId", ""))
        target_id = str(relation.get("targetId", ""))
        if source_id != node_id and target_id != node_id:
            continue
        neighbor_id = target_id if source_id == node_id else source_id
        neighbor = node_by_id.get(neighbor_id, {})
        if not include_chunks and neighbor.get("type") == "chunk":
            continue
        neighbors.append({
            "direction": "out" if source_id == node_id else "in",
            "neighbor": _compact_graph_node(neighbor),
            "relation": _graph_relation_payload(relation, node_by_id, kind),
        })
    neighbors.sort(key=lambda item: (
        item["relation"].get("kind", ""),
        item["neighbor"].get("path", ""),
        item["neighbor"].get("title", ""),
    ))
    return {
        "ok": True,
        "contractVersion": graph["contractVersion"],
        "selector": selector,
        "node": _compact_graph_node(node),
        "neighbors": neighbors[: max(1, limit)],
        "neighborCount": len(neighbors),
        "includeSuggestions": include_suggestions,
        "includeChunks": include_chunks,
        "portableOnly": True,
    }


def cmd_graph_neighbors(args) -> int:
    payload = build_graph_neighbors_payload(
        args.project_root.resolve(),
        args.selector,
        limit=args.limit,
        include_suggestions=args.include_suggestions,
        include_chunks=args.include_chunks,
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def _non_current_reason(lifecycle: str) -> str:
    if lifecycle == "legacy":
        return "legacy material is warning-only and should not be treated as current truth"
    if lifecycle == "superseded":
        return "superseded material may have a newer replacement"
    return "non-current lifecycle"


def build_graph_stale_payload(project: Path, limit: int = 50, include_scan: bool = True) -> dict:
    graph = portable_graph(project)
    stale_nodes = [
        {
            "node": _compact_graph_node(node),
            "reason": _non_current_reason(str(node.get("lifecycle", ""))),
        }
        for node in graph["nodes"]
        if node.get("recordType") != "chunk"
        and str(node.get("lifecycle", "")) in {"legacy", "superseded"}
    ]
    stale_nodes.sort(key=lambda item: (
        item["node"].get("lifecycle", ""),
        item["node"].get("path", ""),
        item["node"].get("title", ""),
    ))
    scan_payload = read_file_index(project) if include_scan else {}
    scan_analysis = build_scan_analysis(project, scan_payload, limit=limit) if scan_payload else {}
    scan_attention = {
        "versionCandidates": scan_analysis.get("versionCandidates", [])[: max(1, limit)] if scan_analysis else [],
        "conflictCandidates": scan_analysis.get("conflictCandidates", [])[: max(1, limit)] if scan_analysis else [],
        "missingPromotedSources": [
            item for item in scan_analysis.get("conflictCandidates", [])
            if item.get("kind") == "missing_promoted_source"
        ][: max(1, limit)] if scan_analysis else [],
    }
    return {
        "ok": True,
        "contractVersion": graph["contractVersion"],
        "staleNodes": stale_nodes[: max(1, limit)],
        "staleNodeCount": len(stale_nodes),
        "scanAttention": scan_attention,
        "scanSummary": scan_analysis.get("summary", {}) if scan_analysis else {},
        "portableOnly": True,
        "projectionPolicy": "Stale graph output is review evidence. Current truth remains CONTROLWORK.md and reviewed active/captured memory entries.",
    }


def cmd_graph_stale(args) -> int:
    payload = build_graph_stale_payload(
        args.project_root.resolve(),
        limit=args.limit,
        include_scan=not args.no_scan,
    )
    print(json.dumps(payload, indent=2))
    return 0


def _unresolved_entry_reason(entry: dict) -> list[str]:
    reasons = []
    lifecycle = str(entry.get("lifecycle", ""))
    text = str(entry.get("title", "")) + "\n" + str(entry.get("body", ""))
    if lifecycle == "needs_review":
        reasons.append("needs_review lifecycle")
    if "?" in text:
        reasons.append("question marker")
    if str(entry.get("type", "")) == "session" and lifecycle == "needs_review":
        reasons.append("session needs follow-up or is blocked")
    return reasons


def build_graph_unresolved_payload(project: Path, limit: int = 50, include_scan: bool = True) -> dict:
    graph = portable_graph(project)
    entries = portable_graph_entries(project)
    unresolved = []
    for entry in entries:
        reasons = _unresolved_entry_reason(entry)
        if not reasons:
            continue
        unresolved.append({
            "node": _compact_graph_node({
                key: entry.get(key, "")
                for key in ("id", "type", "title", "path", "area", "lifecycle", "source", "category")
            }),
            "reasons": reasons,
        })
    unresolved.sort(key=lambda item: (
        item["node"].get("lifecycle", ""),
        item["node"].get("path", ""),
        item["node"].get("title", ""),
    ))
    scan_payload = read_file_index(project) if include_scan else {}
    scan_summary = scan_review_summary(scan_payload, limit=limit, project=project) if scan_payload else {}
    return {
        "ok": True,
        "contractVersion": graph["contractVersion"],
        "unresolved": unresolved[: max(1, limit)],
        "unresolvedCount": len(unresolved),
        "scanReview": {
            "pendingReviewCount": scan_summary.get("pendingReviewCount", 0),
            "reviewQueue": scan_summary.get("reviewQueue", [])[: max(1, limit)] if scan_summary else [],
            "byReviewStatus": scan_summary.get("byReviewStatus", {}) if scan_summary else {},
        },
        "portableOnly": True,
        "projectionPolicy": "Unresolved graph output is an attention queue, not canonical truth.",
    }


def cmd_graph_unresolved(args) -> int:
    payload = build_graph_unresolved_payload(
        args.project_root.resolve(),
        limit=args.limit,
        include_scan=not args.no_scan,
    )
    print(json.dumps(payload, indent=2))
    return 0


def _graph_diff_node_map(graph: dict) -> dict[str, dict]:
    result = {}
    for node in graph.get("nodes", []):
        key = str(node.get("id") or node.get("path") or node.get("title") or "")
        if key:
            result[key] = node
    return result


def _graph_diff_relation_map(graph: dict, key_name: str) -> dict[str, dict]:
    result = {}
    for relation in graph.get(key_name, []):
        key = str(relation.get("id") or "")
        if key:
            result[key] = relation
    return result


def _changed_nodes(previous: dict[str, dict], current: dict[str, dict]) -> list[dict]:
    changed = []
    for key in sorted(set(previous) & set(current)):
        before = previous[key]
        after = current[key]
        fields = [
            field for field in ("title", "path", "type", "lifecycle", "source", "category")
            if str(before.get(field, "")) != str(after.get(field, ""))
        ]
        if fields:
            changed.append({
                "id": key,
                "fields": fields,
                "before": _compact_graph_node(before),
                "after": _compact_graph_node(after),
            })
    return changed


def build_graph_diff_payload(project: Path, baseline: Path, limit: int = 100) -> dict:
    if not baseline.exists():
        return {"ok": False, "error": "baseline graph file not found", "baseline": str(baseline)}
    try:
        previous = json.loads(baseline.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"failed to read baseline graph: {exc}", "baseline": str(baseline)}
    current = portable_graph(project)
    previous_nodes = _graph_diff_node_map(previous)
    current_nodes = _graph_diff_node_map(current)
    previous_edges = _graph_diff_relation_map(previous, "edges")
    current_edges = _graph_diff_relation_map(current, "edges")
    previous_suggestions = _graph_diff_relation_map(previous, "suggestions")
    current_suggestions = _graph_diff_relation_map(current, "suggestions")

    added_nodes = sorted(set(current_nodes) - set(previous_nodes))
    removed_nodes = sorted(set(previous_nodes) - set(current_nodes))
    added_edges = sorted(set(current_edges) - set(previous_edges))
    removed_edges = sorted(set(previous_edges) - set(current_edges))
    added_suggestions = sorted(set(current_suggestions) - set(previous_suggestions))
    removed_suggestions = sorted(set(previous_suggestions) - set(current_suggestions))
    changed = _changed_nodes(previous_nodes, current_nodes)
    return {
        "ok": True,
        "contractVersion": current["contractVersion"],
        "baseline": str(baseline),
        "summary": {
            "addedNodes": len(added_nodes),
            "removedNodes": len(removed_nodes),
            "changedNodes": len(changed),
            "addedEdges": len(added_edges),
            "removedEdges": len(removed_edges),
            "addedSuggestions": len(added_suggestions),
            "removedSuggestions": len(removed_suggestions),
        },
        "addedNodes": [_compact_graph_node(current_nodes[item]) for item in added_nodes[: max(1, limit)]],
        "removedNodes": [_compact_graph_node(previous_nodes[item]) for item in removed_nodes[: max(1, limit)]],
        "changedNodes": changed[: max(1, limit)],
        "addedEdges": [_graph_relation_payload(current_edges[item], current_nodes, "edge") for item in added_edges[: max(1, limit)]],
        "removedEdges": [_graph_relation_payload(previous_edges[item], previous_nodes, "edge") for item in removed_edges[: max(1, limit)]],
        "addedSuggestions": [_graph_relation_payload(current_suggestions[item], current_nodes, "suggestion") for item in added_suggestions[: max(1, limit)]],
        "removedSuggestions": [_graph_relation_payload(previous_suggestions[item], previous_nodes, "suggestion") for item in removed_suggestions[: max(1, limit)]],
        "portableOnly": True,
        "projectionPolicy": "Graph diff compares derived projections only. It does not prove source truth changed.",
    }


def cmd_graph_diff(args) -> int:
    project = args.project_root.resolve()
    try:
        baseline = resolve_input_path(project, args.baseline)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    payload = build_graph_diff_payload(project, baseline, limit=args.limit)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1
