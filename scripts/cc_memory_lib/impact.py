"""Impact and context lookup for project memory."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .entities import _row_to_dict
from .freshness_projection import projected_status_payload
from .ids import _project_short
from .lifecycle import LIFECYCLE_ATTENTION_STATES
from .migrations import TARGET_SCHEMA_VERSION, inspect_schema
from .store import (
    _ensure_schema,
    _get_metadata,
    _memory_db_error_payload,
    _memory_db_error_text,
    _memory_connection,
    _now_iso,
    _print_json_error_or_text,
    _print_json_or_text,
    _readonly_memory_connection,
    _relative_path,
    _require_initialized,
    _set_metadata,
)
from .vector import _vector_health

_ALLOWED_COUNT_FIELDS = {"type": "type", "lifecycle": "lifecycle"}

def _counts_by(conn: sqlite3.Connection, field: str) -> dict[str, int]:
    try:
        column = _ALLOWED_COUNT_FIELDS[field]
    except KeyError:
        raise ValueError(f"Unsupported count field: {field!r}") from None
    rows = conn.execute(f"SELECT {column} AS name, COUNT(*) AS count FROM entities GROUP BY {column} ORDER BY {column}").fetchall()
    return {str(row["name"]): int(row["count"]) for row in rows}

def _count_events(conn: sqlite3.Connection, event_type: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM events WHERE type = ?", (event_type,)).fetchone()
    return int(row["count"] if row else 0)

def _status_payload(project: Path) -> dict[str, Any]:
    """Compatibility entry point retained as a strictly projection-only read."""
    return projected_status_payload(project)

def cmd_memory_status(project: Path, json_output: bool = False) -> int:
    payload = _status_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2))
        return 0
    schema = payload.get("schema", {})
    health = payload.get("health", {})
    if not payload.get("available"):
        print("Project memory status")
        print(f"  Health: {health.get('state', 'unknown').upper()} ({health.get('color', 'YELLOW')})")
        print(f"  Reason: {health.get('reason', 'projection_missing')}")
        print(f"  Remediation: {health.get('remediation', '')}")
        return 0
    else:
        print("Project memory status")
        print(f"  Health: {health.get('state', 'fresh').upper()} ({health.get('color', 'GREEN')})")
        print(f"  Schema state: {schema.get('schemaState', 'unknown')}")
        print(f"  User version: {schema.get('userVersion', 0)}")
        print(f"  Metadata version: {schema.get('metadataVersion')}")
        print(f"  Target version: {schema.get('targetVersion', 0)}")
        print(f"  Project short: {payload['projectShort']}")
        print(f"  Entities: {payload['entities']}")
        print(f"  Lifecycle attention: {payload['lifecycleAttention']}")
        print(f"  Last scan: {payload['lastScanAt'] or 'never'}")
        print(f"  Last views generated: {payload['lastViewsGeneratedAt'] or 'never'}")
        vectors = payload["vectors"]
        print(
            "  Vectors: "
            f"rows={vectors['rowCount']}, "
            f"semanticChunks={vectors['semanticChunks']}, "
            f"stale={vectors['stale']}"
        )
        print(f"  Last vector rebuild: {vectors['lastRebuiltAt'] or 'never'}")
        if vectors["stale"]:
            print(f"  Vector rebuild command: {vectors['rebuildCommand']}")
        print("  By type:")
        for key, value in payload["byType"].items():
            print(f"    {key}: {value}")
        print("  By lifecycle:")
        for key, value in payload["byLifecycle"].items():
            print(f"    {key}: {value}")
    return 0

def _entity_text(entity: dict[str, Any]) -> str:
    parts = [
        str(entity.get("id", "")),
        str(entity.get("type", "")),
        str(entity.get("title", "")),
        str(entity.get("path", "")),
        str(entity.get("body", "")),
        json.dumps(entity.get("data", {}), sort_keys=True),
    ]
    return " ".join(parts).lower()

def _query_tokens(query: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", query) if len(token) >= 2]

def _load_entities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM entities ORDER BY updated_at DESC, title").fetchall()
    return [_row_to_dict(row) for row in rows]

def _correlation_weight(confidence: str) -> int:
    return {
        "explicit_link": 40,
        "exact_id_or_path_match": 36,
        "human_confirmed_relation": 34,
        "strong_title_or_heading_match": 22,
        "weak_lexical_similarity": 10,
    }.get(confidence, 6)

def _seed_entity_ids(entities: list[dict[str, Any]], query: str) -> list[str]:
    query_lower = query.lower()
    seeds: list[str] = []
    for entity in entities:
        path = str(entity.get("path") or "").lower()
        entity_id = str(entity.get("id") or "").lower()
        if query_lower and query_lower in {path, entity_id}:
            seeds.append(str(entity.get("id")))
    return seeds

def _load_correlation_reasons(
    conn: sqlite3.Connection,
    seed_ids: list[str],
) -> dict[str, tuple[int, list[str]]]:
    if not seed_ids:
        return {}
    placeholders = ",".join("?" for _item in seed_ids)
    rows = conn.execute(
        f"""
        SELECT source_id, target_id, type, confidence, reason
        FROM correlation_suggestions
        WHERE status IN ('suggested', 'accepted', 'confirmed')
          AND (source_id IN ({placeholders}) OR target_id IN ({placeholders}))
        """,
        (*seed_ids, *seed_ids),
    ).fetchall()
    related: dict[str, tuple[int, list[str]]] = {}
    seed_set = set(seed_ids)
    for row in rows:
        source_id = str(row["source_id"])
        target_id = str(row["target_id"])
        related_id = target_id if source_id in seed_set else source_id
        if related_id in seed_set:
            continue
        confidence = str(row["confidence"])
        score, reasons = related.get(related_id, (0, []))
        score += _correlation_weight(confidence)
        reasons.append(f"{confidence}: {row['reason']}")
        related[related_id] = (score, reasons)
    return related

def _score_entity(
    entity: dict[str, Any],
    query: str,
    tokens: list[str],
    exact_area: str = "",
    correlation: tuple[int, list[str]] | None = None,
) -> tuple[int, list[str]]:
    text = _entity_text(entity)
    query_lower = query.lower()
    score = 0
    reasons: list[str] = []
    path = str(entity.get("path") or "").lower()
    title = str(entity.get("title") or "").lower()
    entity_id = str(entity.get("id") or "").lower()
    if query_lower and query_lower in {path, entity_id}:
        score += 100
        reasons.append("exact path or entity id match")
    if query_lower and query_lower in path:
        score += 35
        reasons.append("query appears in path")
    if query_lower and query_lower in title:
        score += 25
        reasons.append("query appears in title")
    for token in tokens:
        if token in text:
            score += 5
            if len(reasons) < 6:
                reasons.append(f"token match: {token}")
        if token in title:
            score += 3
        if token in path:
            score += 2
    data = entity.get("data", {}) if isinstance(entity.get("data"), dict) else {}
    document_type = str(data.get("document_type") or "")
    keywords = {str(value).lower() for value in data.get("keywords", []) if str(value).strip()} if isinstance(data.get("keywords"), list) else set()
    keyword_hits = sorted(set(tokens) & keywords)
    if keyword_hits:
        score += 4 * len(keyword_hits)
        reasons.append("document keyword match: " + ", ".join(keyword_hits[:4]))
    if document_type in {"decision", "plan", "research", "handoff"}:
        score += 3
        reasons.append(f"document type: {document_type}")
    if exact_area and str(data.get("area", "")).lower() == exact_area.lower():
        score += 4
        reasons.append(f"same area: {exact_area}")
    if entity.get("lifecycle") in LIFECYCLE_ATTENTION_STATES:
        score += 2
        reasons.append(f"lifecycle warning: {entity.get('lifecycle')}")
    if correlation:
        boost, correlation_reasons = correlation
        score += boost
        reasons.extend(correlation_reasons[:3])
    return score, reasons

def _impact_entities(project: Path, query: str) -> list[dict[str, Any]]:
    query = query.strip()
    with _memory_connection(project) as conn:
        entities = _load_entities(conn)
        seed_ids = _seed_entity_ids(entities, _relative_path(project, query) if query else query)
        correlation_reasons = _load_correlation_reasons(conn, seed_ids)
    rel_query = _relative_path(project, query) if query else query
    tokens = _query_tokens(rel_query)
    exact_area = ""
    for entity in entities:
        if entity.get("path") == rel_query:
            data = entity.get("data", {}) if isinstance(entity.get("data"), dict) else {}
            exact_area = str(data.get("area", ""))
            break
    scored = [
        (
            entity,
            *_score_entity(
                entity,
                rel_query,
                tokens,
                exact_area=exact_area,
                correlation=correlation_reasons.get(str(entity.get("id"))),
            ),
        )
        for entity in entities
    ]
    scored = [(entity, score, reasons) for entity, score, reasons in scored if score > 0]
    scored.sort(key=lambda item: (-item[1], str(item[0].get("type", "")), str(item[0].get("title", ""))))
    annotated: list[dict[str, Any]] = []
    for entity, score, reasons in scored[:40]:
        item = dict(entity)
        item["_impactScore"] = score
        item["_impactReasons"] = reasons[:8]
        annotated.append(item)
    return annotated

def _group_impact(entities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {
        "files": [],
        "docsAndPlans": [],
        "decisions": [],
        "notesAndIdeas": [],
        "consults": [],
        "agentRuns": [],
        "testsAndBenchmarks": [],
        "staleOrNeedsReview": [],
    }
    for entity in entities:
        entity_type = entity.get("type")
        lifecycle = entity.get("lifecycle")
        if lifecycle in LIFECYCLE_ATTENTION_STATES:
            groups["staleOrNeedsReview"].append(entity)
        if entity_type in {"file_node"}:
            groups["files"].append(entity)
        elif entity_type in {"doc_node", "plan", "research_note", "handoff"}:
            groups["docsAndPlans"].append(entity)
        elif entity_type == "decision":
            groups["decisions"].append(entity)
        elif entity_type in {"note", "idea"}:
            groups["notesAndIdeas"].append(entity)
        elif entity_type == "consult":
            groups["consults"].append(entity)
        elif entity_type == "agent_run":
            groups["agentRuns"].append(entity)
        elif entity_type in {"test_node", "benchmark"}:
            groups["testsAndBenchmarks"].append(entity)
    return groups

def _format_entity_line(entity: dict[str, Any]) -> str:
    path = entity.get("path") or ""
    path_part = f" [{path}]" if path else ""
    score = entity.get("_impactScore")
    score_part = f", score {score}" if score is not None else ""
    return f"- {entity.get('id')} - {entity.get('title')} ({entity.get('type')}, {entity.get('lifecycle')}{score_part}){path_part}"

def _print_impact(query: str, entities: list[dict[str, Any]], context_mode: bool = False) -> None:
    title = "Memory context" if context_mode else "Memory impact"
    print(f"{title}: {query}")
    groups = _group_impact(entities)
    if not entities:
        print("No matching memory records found. Run `cc memory scan` or add notes and decisions.")
        return
    labels = [
        ("files", "Files"),
        ("docsAndPlans", "Docs and plans"),
        ("decisions", "Decisions"),
        ("notesAndIdeas", "Notes and ideas"),
        ("consults", "Consults"),
        ("agentRuns", "Agent runs"),
        ("testsAndBenchmarks", "Tests and benchmarks"),
        ("staleOrNeedsReview", "Lifecycle attention"),
    ]
    for key, label in labels:
        items = groups[key]
        if not items:
            continue
        print(f"\n{label}:")
        for entity in items[:10]:
            print(_format_entity_line(entity))
            reasons = entity.get("_impactReasons")
            if isinstance(reasons, list) and reasons:
                print(f"  Reasons: {'; '.join(str(reason) for reason in reasons[:4])}")
            if context_mode and entity.get("body"):
                body = str(entity["body"]).strip().replace("\n", " ")
                print(f"  Summary: {body[:240]}")

def _record_read_context(project: Path, command: str, query: str) -> None:
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        now = _now_iso()
        _set_metadata(conn, "last_impact_checked_at", now)
        _set_metadata(conn, "last_impact_query", query)
        _set_metadata(conn, "last_impact_command", command)

def cmd_memory_impact(project: Path, query: str, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_or_text(
            json_output,
            {"ok": False, "error": "memory_impact_memory_not_initialized", "message": message},
            f"Error: {message}",
        )
        return 1
    try:
        entities = _impact_entities(project, query)
        _record_read_context(project, "cc memory impact", query)
    except sqlite3.Error as exc:
        payload = _memory_db_error_payload(
            project,
            exc,
            operation="memory_impact",
            command="python scripts/cc.py memory impact --project-root . <query>",
        )
        _print_json_or_text(json_output, payload, _memory_db_error_text(payload))
        return 1
    payload = {"query": query, "matches": entities, "groups": _group_impact(entities)}
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        _print_impact(query, entities, context_mode=False)
    return 0

def cmd_memory_context(project: Path, query: str, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_or_text(
            json_output,
            {"ok": False, "error": "memory_context_memory_not_initialized", "message": message},
            f"Error: {message}",
        )
        return 1
    try:
        entities = _impact_entities(project, query)
        _record_read_context(project, "cc memory context", query)
    except sqlite3.Error as exc:
        payload = _memory_db_error_payload(
            project,
            exc,
            operation="memory_context",
            command="python scripts/cc.py memory context --project-root . <query>",
        )
        _print_json_or_text(json_output, payload, _memory_db_error_text(payload))
        return 1
    payload = {"query": query, "matches": entities, "groups": _group_impact(entities)}
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        _print_impact(query, entities, context_mode=True)
    return 0
