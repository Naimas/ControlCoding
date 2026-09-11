"""Dev GraphRAG retrieval for the ControlCoding Project Memory Engine."""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from .entities import _row_to_dict
from .migrations import inspect_schema
from .schema import SCHEMA_VERSION, VALID_LIFECYCLES
from .semantic import semantic_score_candidates
from .scoring import DEFAULT_RETRIEVAL_SCORING, retrieval_scoring_config_payload
from .store import (
    _ensure_retrieval_fts,
    _fts5_available,
    _json_dumps,
    _memory_db_error_payload,
    _now_iso,
    _print_json_or_text,
    _readonly_memory_connection,
    _relative_path,
    _require_initialized,
    _set_metadata,
)
from .vector import (
    LOCAL_SPARSE_ADAPTER,
    VECTOR_REBUILD_COMMAND,
    _empty_vector_search_report,
    _search_vector_index_with_report,
    _vector_health_fast,
)

ATTENTION_LIFECYCLES = {"stale", "conflicting", "needs_review", "legacy", "superseded"}
EXCLUDED_LIFECYCLES = {"rejected"}
ARCHIVE_LIFECYCLES = {"archived"}

CONFIDENCE_POINTS = dict(DEFAULT_RETRIEVAL_SCORING.confidence_points)
CONFIDENCE_FALLBACK_POINTS = DEFAULT_RETRIEVAL_SCORING.confidence_fallback_points
CANDIDATE_FILTER_MIN_CAP = DEFAULT_RETRIEVAL_SCORING.candidate_filter.min_cap
CANDIDATE_FILTER_MAX_CAP = DEFAULT_RETRIEVAL_SCORING.candidate_filter.max_cap
CANDIDATE_FILTER_LIMIT_MULTIPLIER = DEFAULT_RETRIEVAL_SCORING.candidate_filter.limit_multiplier
GRAPH_ADJACENCY_EDGE_ROW_MIN = DEFAULT_RETRIEVAL_SCORING.graph_adjacency_edge_rows.min_rows
GRAPH_ADJACENCY_EDGE_ROW_MAX = DEFAULT_RETRIEVAL_SCORING.graph_adjacency_edge_rows.max_rows
GRAPH_ADJACENCY_EDGE_ROW_MULTIPLIER = DEFAULT_RETRIEVAL_SCORING.graph_adjacency_edge_rows.node_multiplier
TEXT_SIGNAL_POINTS = dict(DEFAULT_RETRIEVAL_SCORING.text_points)
SPARSE_VECTOR_RETRIEVAL_MULTIPLIER = DEFAULT_RETRIEVAL_SCORING.sparse_vector_retrieval_multiplier
GRAPH_SUGGESTION_EDGE_POINTS = dict(DEFAULT_RETRIEVAL_SCORING.graph_suggestion_edge_points)
GRAPH_DEFAULT_EDGE_POINTS = dict(DEFAULT_RETRIEVAL_SCORING.graph_default_edge_points)
GRAPH_SUGGESTION_FALLBACK_POINTS = DEFAULT_RETRIEVAL_SCORING.graph_suggestion_fallback_points
GRAPH_DEFAULT_FALLBACK_POINTS = DEFAULT_RETRIEVAL_SCORING.graph_default_fallback_points
LIFECYCLE_POINTS = dict(DEFAULT_RETRIEVAL_SCORING.lifecycle_points)
RECENCY_DAY_POINTS = tuple(DEFAULT_RETRIEVAL_SCORING.recency_day_points)
SOURCE_TRUST_TYPE_POINTS = dict(DEFAULT_RETRIEVAL_SCORING.source_trust_type_points)
RETRIEVAL_SIGNAL_NAMES = [
    "text",
    "sparse_vector",
    "semantic",
    "graph_proximity",
    "lifecycle",
    "confidence",
    "recency",
    "source_trust",
]


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except json.JSONDecodeError:
        return fallback
    return parsed


def _query_tokens(query: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_/-]+", query)
        if len(token) >= 2
    ]


def _stringify_json(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value or "")


def _retrieval_tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_/-]+", text)
        if 2 <= len(token) <= 80
    ]


def _candidate_filter_cap(limit: int) -> int:
    requested = max(1, int(limit or 10)) * CANDIDATE_FILTER_LIMIT_MULTIPLIER
    return max(1, min(CANDIDATE_FILTER_MAX_CAP, max(CANDIDATE_FILTER_MIN_CAP, requested)))


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _live_retrieval_records_sql() -> str:
    return """
        SELECT id AS record_key, content_hash AS content_hash
        FROM entities
        UNION ALL
        SELECT id AS record_key, content_hash AS content_hash
        FROM semantic_chunks
    """


def _entity_candidate(row: sqlite3.Row) -> dict[str, Any]:
    entity = _row_to_dict(row)
    data = entity.get("data", {}) if isinstance(entity.get("data"), dict) else {}
    source_refs = entity.get("source_refs", []) if isinstance(entity.get("source_refs"), list) else []
    search_parts = [
        entity.get("id", ""),
        entity.get("type", ""),
        entity.get("title", ""),
        entity.get("path", ""),
        entity.get("body", ""),
        _stringify_json(data),
        _stringify_json(source_refs),
    ]
    return {
        "id": str(entity.get("id") or ""),
        "recordType": "entity",
        "type": str(entity.get("type") or ""),
        "title": str(entity.get("title") or entity.get("id") or ""),
        "path": str(entity.get("path") or ""),
        "headingPath": "",
        "lifecycle": str(entity.get("lifecycle") or ""),
        "updatedAt": str(entity.get("updated_at") or entity.get("created_at") or ""),
        "plane": str(entity.get("plane") or ""),
        "bodyPreview": str(entity.get("body") or "")[:1200],
        "documentType": str(data.get("document_type") or ""),
        "documentFacets": data.get("document_facets") if isinstance(data.get("document_facets"), list) else [],
        "sourceRefs": source_refs,
        "searchText": " ".join(str(part) for part in search_parts).lower(),
    }


def _chunk_candidate(row: sqlite3.Row) -> dict[str, Any]:
    metadata = _json_loads(str(row["metadata"] or "{}"), {})
    if not isinstance(metadata, dict):
        metadata = {}
    keywords = _json_loads(str(row["keywords"] or "[]"), [])
    if not isinstance(keywords, list):
        keywords = []
    document_data = _json_loads(str(row["document_data"] or "{}"), {})
    if not isinstance(document_data, dict):
        document_data = {}
    search_parts = [
        row["id"],
        row["source_path"],
        row["heading_path"],
        row["summary"],
        row["content_preview"],
        _stringify_json(keywords),
        _stringify_json(metadata),
        str(row["document_title"] or ""),
        _stringify_json(document_data),
    ]
    return {
        "id": str(row["id"]),
        "recordType": "semantic_chunk",
        "type": "chunk",
        "title": str(row["summary"] or row["heading_path"] or row["id"]),
        "path": str(row["source_path"] or ""),
        "headingPath": str(row["heading_path"] or ""),
        "lifecycle": str(row["lifecycle"] or ""),
        "updatedAt": str(row["updated_at"] or row["created_at"] or ""),
        "plane": "controlcoding_dev",
        "contentPreview": str(row["content_preview"] or ""),
        "documentId": str(row["document_id"] or ""),
        "documentType": str(document_data.get("document_type") or ""),
        "documentFacets": document_data.get("document_facets") if isinstance(document_data.get("document_facets"), list) else [],
        "sourceRefs": [],
        "metadata": metadata,
        "searchText": " ".join(str(part) for part in search_parts).lower(),
    }


def _session_lifecycle(status: str) -> str:
    return {
        "active": "active",
        "completed": "verified",
        "needs_followup": "needs_review",
        "blocked": "needs_review",
        "superseded": "superseded",
        "archived": "archived",
    }.get(status, "captured")


def _session_candidate(row: sqlite3.Row) -> dict[str, Any]:
    categories = _json_loads(str(row["categories"] or "[]"), [])
    files_changed = _json_loads(str(row["files_changed"] or "[]"), [])
    docs_changed = _json_loads(str(row["docs_changed"] or "[]"), [])
    commands_run = _json_loads(str(row["commands_run"] or "[]"), [])
    tests_run = _json_loads(str(row["tests_run"] or "[]"), [])
    commits = _json_loads(str(row["commits"] or "[]"), [])
    packets = _json_loads(str(row["packets"] or "[]"), [])
    decisions = _json_loads(str(row["decisions"] or "[]"), [])
    followups = _json_loads(str(row["followups"] or "[]"), [])
    links = _json_loads(str(row["links"] or "[]"), [])
    data = _json_loads(str(row["data"] or "{}"), {})
    evidence_refs: list[str] = []
    for values in (files_changed, docs_changed, commands_run, tests_run, commits, packets, decisions, followups):
        if isinstance(values, list):
            evidence_refs.extend(str(value) for value in values if str(value or "").strip())
    search_parts = [
        row["id"],
        "session",
        row["mode"],
        row["scope"],
        row["topic"],
        row["status"],
        row["summary"],
        _stringify_json(categories),
        _stringify_json(evidence_refs),
        _stringify_json(links),
        _stringify_json(data),
    ]
    return {
        "id": str(row["id"]),
        "recordType": "session",
        "type": "session",
        "title": str(row["topic"] or row["id"]),
        "path": "",
        "headingPath": "",
        "lifecycle": _session_lifecycle(str(row["status"] or "")),
        "updatedAt": str(row["updated_at"] or row["created_at"] or ""),
        "plane": "controlcoding_dev",
        "documentType": "session_record",
        "documentFacets": [],
        "sourceRefs": evidence_refs,
        "sessionStatus": str(row["status"] or ""),
        "evidenceLinkCount": len(evidence_refs),
        "searchText": " ".join(str(part) for part in search_parts).lower(),
    }


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _value in values)


def _graph_edge_row_cap(node_count: int, requested_cap: int | None = None) -> int:
    scaled = max(GRAPH_ADJACENCY_EDGE_ROW_MIN, max(1, node_count) * GRAPH_ADJACENCY_EDGE_ROW_MULTIPLIER)
    if requested_cap is not None:
        scaled = max(scaled, int(requested_cap) * 4)
    return min(GRAPH_ADJACENCY_EDGE_ROW_MAX, scaled)


def _existing_candidate_keys(conn: sqlite3.Connection, record_keys: set[str]) -> set[str]:
    if not record_keys:
        return set()
    keys = sorted(record_keys)
    placeholders = _placeholders(keys)
    existing: set[str] = set()
    for table in ("entities", "semantic_chunks", "session_records"):
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE id IN ({placeholders})",
            keys,
        ).fetchall()
        existing.update(str(row["id"]) for row in rows)
    return existing


def _load_candidate_details(
    conn: sqlite3.Connection,
    record_keys: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if record_keys is not None and not record_keys:
        return {}
    candidates: dict[str, dict[str, Any]] = {}
    keys = sorted(record_keys) if record_keys is not None else []
    entity_sql = "SELECT * FROM entities"
    entity_params: list[str] = []
    if record_keys is not None:
        entity_sql += f" WHERE id IN ({_placeholders(keys)})"
        entity_params = keys
    entity_sql += " ORDER BY updated_at DESC, title"
    entity_rows = conn.execute(entity_sql, entity_params).fetchall()
    for row in entity_rows:
        candidate = _entity_candidate(row)
        candidates[candidate["id"]] = candidate
    chunk_sql = """
        SELECT c.*, e.title AS document_title, e.data AS document_data
        FROM semantic_chunks c
        LEFT JOIN entities e ON e.id = c.document_id
    """
    chunk_params: list[str] = []
    if record_keys is not None:
        chunk_sql += f" WHERE c.id IN ({_placeholders(keys)})"
        chunk_params = keys
    chunk_sql += " ORDER BY c.source_path, c.ordinal, c.sequence_index"
    chunk_rows = conn.execute(chunk_sql, chunk_params).fetchall()
    for row in chunk_rows:
        candidate = _chunk_candidate(row)
        candidates[candidate["id"]] = candidate
    session_sql = "SELECT * FROM session_records"
    session_params: list[str] = []
    if record_keys is not None:
        session_sql += f" WHERE id IN ({_placeholders(keys)})"
        session_params = keys
    session_sql += " ORDER BY updated_at DESC, started_at DESC"
    session_rows = conn.execute(session_sql, session_params).fetchall()
    for row in session_rows:
        candidate = _session_candidate(row)
        candidates[candidate["id"]] = candidate
    return candidates


def _load_candidates(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return _load_candidate_details(conn)


def _retrieval_record_rows(conn: sqlite3.Connection) -> list[tuple[dict[str, Any], str]]:
    rows: list[tuple[dict[str, Any], str]] = []
    for row in conn.execute("SELECT * FROM entities ORDER BY updated_at DESC, title").fetchall():
        rows.append((_entity_candidate(row), str(row["content_hash"] or "")))
    chunk_rows = conn.execute(
        """
        SELECT c.*, e.title AS document_title, e.data AS document_data
        FROM semantic_chunks c
        LEFT JOIN entities e ON e.id = c.document_id
        ORDER BY c.source_path, c.ordinal, c.sequence_index
        """
    ).fetchall()
    for row in chunk_rows:
        rows.append((_chunk_candidate(row), str(row["content_hash"] or "")))
    return rows


def _rebuild_retrieval_index(conn: sqlite3.Connection) -> dict[str, Any]:
    now = _now_iso()
    records = _retrieval_record_rows(conn)
    conn.execute("DELETE FROM retrieval_terms")
    conn.execute("DELETE FROM retrieval_term_stats")
    conn.execute("DELETE FROM retrieval_records")
    fts5_available = _ensure_retrieval_fts(conn)
    if fts5_available and _table_exists(conn, "retrieval_fts"):
        conn.execute("DELETE FROM retrieval_fts")

    counts_by_record: dict[str, Counter[str]] = {}
    document_frequency: Counter[str] = Counter()
    for candidate, content_hash in records:
        record_key = str(candidate["id"])
        search_text = str(candidate.get("searchText") or "")
        terms = Counter(_retrieval_tokens(search_text))
        counts_by_record[record_key] = terms
        document_frequency.update(terms.keys())
        conn.execute(
            """
            INSERT INTO retrieval_records(
              record_key, record_type, source_id, title, path, heading_path,
              lifecycle, content_hash, updated_at, search_text, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_key,
                str(candidate.get("recordType") or ""),
                record_key,
                str(candidate.get("title") or ""),
                str(candidate.get("path") or ""),
                str(candidate.get("headingPath") or ""),
                str(candidate.get("lifecycle") or ""),
                content_hash,
                str(candidate.get("updatedAt") or ""),
                search_text,
                now,
            ),
        )
        if fts5_available and _table_exists(conn, "retrieval_fts"):
            conn.execute(
                """
                INSERT INTO retrieval_fts(record_key, title, path, heading_path, search_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record_key,
                    str(candidate.get("title") or ""),
                    str(candidate.get("path") or ""),
                    str(candidate.get("headingPath") or ""),
                    search_text,
                ),
            )

    record_count = len(records)
    idf_by_term: dict[str, float] = {}
    for term, frequency in sorted(document_frequency.items()):
        idf = math.log((record_count + 1) / (int(frequency) + 1)) + 1.0 if record_count else 0.0
        idf_by_term[term] = idf
        conn.execute(
            """
            INSERT INTO retrieval_term_stats(term, document_frequency, idf, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (term, int(frequency), round(idf, 8), now),
        )
    term_rows = 0
    for record_key, counts in counts_by_record.items():
        for term, term_count in sorted(counts.items()):
            idf = idf_by_term.get(term, 0.0)
            conn.execute(
                """
                INSERT INTO retrieval_terms(term, record_key, term_count, term_weight)
                VALUES (?, ?, ?, ?)
                """,
                (term, record_key, int(term_count), round(float(term_count) * idf, 8)),
            )
            term_rows += 1
    _set_metadata(conn, "last_retrieval_index_rebuild_at", now)
    _set_metadata(conn, "last_retrieval_index_record_count", str(record_count))
    _set_metadata(conn, "last_retrieval_index_term_count", str(term_rows))
    _set_metadata(conn, "last_retrieval_index_fts5_available", str(bool(fts5_available)).lower())
    return {
        "recordCount": record_count,
        "termRows": term_rows,
        "termStats": len(idf_by_term),
        "fts5Available": bool(fts5_available),
        "rebuiltAt": now,
    }


def _retrieval_index_health(conn: sqlite3.Connection, fts5_available: bool) -> dict[str, Any]:
    live_sql = _live_retrieval_records_sql()
    live_count = int(conn.execute(f"SELECT COUNT(*) AS count FROM ({live_sql}) live").fetchone()["count"])
    record_count = int(conn.execute("SELECT COUNT(*) AS count FROM retrieval_records").fetchone()["count"])
    term_count = int(conn.execute("SELECT COUNT(*) AS count FROM retrieval_terms").fetchone()["count"])
    stat_count = int(conn.execute("SELECT COUNT(*) AS count FROM retrieval_term_stats").fetchone()["count"])
    missing_rows = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM ({live_sql}) live
            LEFT JOIN retrieval_records r ON r.record_key = live.record_key
            WHERE r.record_key IS NULL
            """
        ).fetchone()["count"]
    )
    orphan_rows = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM retrieval_records r
            LEFT JOIN ({live_sql}) live ON live.record_key = r.record_key
            WHERE live.record_key IS NULL
            """
        ).fetchone()["count"]
    )
    hash_mismatches = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM retrieval_records r
            JOIN ({live_sql}) live ON live.record_key = r.record_key
            WHERE COALESCE(r.content_hash, '') != COALESCE(live.content_hash, '')
            """
        ).fetchone()["count"]
    )
    fts_row_count = 0
    fts_missing = fts5_available and not _table_exists(conn, "retrieval_fts")
    if fts5_available and not fts_missing:
        fts_row_count = int(conn.execute("SELECT COUNT(*) AS count FROM retrieval_fts").fetchone()["count"])
    stale_reasons: list[str] = []
    if missing_rows:
        stale_reasons.append("missing_retrieval_records")
    if orphan_rows:
        stale_reasons.append("orphan_retrieval_records")
    if hash_mismatches:
        stale_reasons.append("content_hash_mismatch")
    if live_count and record_count and term_count == 0:
        stale_reasons.append("empty_retrieval_terms")
    index_missing = live_count > 0 and record_count == 0
    index_stale = bool(stale_reasons)
    return {
        "liveRecordCount": live_count,
        "recordCount": record_count,
        "termRows": term_count,
        "termStats": stat_count,
        "missingRows": missing_rows,
        "orphanRows": orphan_rows,
        "contentHashMismatches": hash_mismatches,
        "staleReasons": stale_reasons,
        "indexMissing": index_missing,
        "indexStale": index_stale,
        "ftsRowCount": fts_row_count,
        "ftsMissing": bool(fts_missing),
        "ftsStale": bool(fts5_available and not fts_missing and fts_row_count != record_count),
    }


def _exact_record_keys(conn: sqlite3.Connection, query: str, rel_query: str) -> list[str]:
    query_lower = query.lower()
    rel_query_lower = rel_query.lower()
    rows = conn.execute(
        """
        SELECT record_key
        FROM retrieval_records
        WHERE lower(record_key) = ?
           OR lower(source_id) = ?
           OR lower(path) = ?
           OR lower(title) = ?
        ORDER BY record_key
        """,
        (query_lower, query_lower, rel_query_lower, query_lower),
    ).fetchall()
    keys = [str(row["record_key"]) for row in rows]
    session_rows = conn.execute(
        """
        SELECT id
        FROM session_records
        WHERE lower(id) = ? OR lower(topic) = ?
        ORDER BY updated_at DESC, started_at DESC
        """,
        (query_lower, query_lower),
    ).fetchall()
    keys.extend(str(row["id"]) for row in session_rows)
    return keys


def _fts_query(tokens: list[str]) -> str:
    quoted = []
    for token in tokens:
        safe = token.replace('"', '""')
        if safe:
            quoted.append(f'"{safe}"')
    return " OR ".join(quoted)


def _fts_candidate_keys(conn: sqlite3.Connection, tokens: list[str], cap: int) -> tuple[list[str], str]:
    query = _fts_query(tokens)
    if not query:
        return [], ""
    try:
        rows = conn.execute(
            """
            SELECT record_key, bm25(retrieval_fts) AS rank
            FROM retrieval_fts
            WHERE retrieval_fts MATCH ?
            ORDER BY rank, record_key
            LIMIT ?
            """,
            (query, cap),
        ).fetchall()
    except sqlite3.Error as exc:
        return [], f"fts_query_error: {exc}"
    return [str(row["record_key"]) for row in rows], ""


def _sql_inverted_candidate_keys(conn: sqlite3.Connection, tokens: list[str], cap: int) -> list[str]:
    if not tokens:
        return []
    unique_terms = sorted(set(tokens))
    rows = conn.execute(
        f"""
        SELECT rt.record_key, SUM(rt.term_weight) AS rank, SUM(rt.term_count) AS term_count
        FROM retrieval_terms rt
        WHERE rt.term IN ({_placeholders(unique_terms)})
        GROUP BY rt.record_key
        ORDER BY rank DESC, term_count DESC, rt.record_key
        LIMIT ?
        """,
        [*unique_terms, cap],
    ).fetchall()
    return [str(row["record_key"]) for row in rows]


def _session_candidate_keys(conn: sqlite3.Connection, tokens: list[str], cap: int) -> list[str]:
    if not tokens:
        return []
    unique_terms = sorted(set(tokens))[:12]
    predicates = [
        "lower(topic || ' ' || summary || ' ' || categories || ' ' || files_changed || ' ' || docs_changed || ' ' || commands_run || ' ' || tests_run || ' ' || commits || ' ' || packets || ' ' || decisions || ' ' || followups || ' ' || links || ' ' || data) LIKE ?"
        for _term in unique_terms
    ]
    rows = conn.execute(
        f"""
        SELECT id
        FROM session_records
        WHERE {" OR ".join(predicates)}
        ORDER BY updated_at DESC, started_at DESC, id
        LIMIT ?
        """,
        [*(f"%{term}%" for term in unique_terms), cap],
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _dedupe_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _load_graph_expansion_keys(conn: sqlite3.Connection, seed_keys: list[str] | set[str], cap: int) -> list[str]:
    ordered_seed_keys = _dedupe_ordered([str(key) for key in seed_keys if str(key)])
    if not ordered_seed_keys or cap <= 0:
        return []
    seed_key_set = set(ordered_seed_keys)
    expanded: list[str] = []
    seen = set(ordered_seed_keys)
    for seed_key in ordered_seed_keys:
        remaining = cap - len(expanded)
        if remaining <= 0:
            break
        row_cap = _graph_edge_row_cap(1, remaining)
        edge_rows = conn.execute(
            """
            SELECT source_id, target_id, type
            FROM edges
            WHERE source_id = ? OR target_id = ?
            ORDER BY source_id, target_id, type
            LIMIT ?
            """,
            (seed_key, seed_key, row_cap),
        ).fetchall()
        suggestion_rows = conn.execute(
            """
            SELECT source_id, target_id, type
            FROM correlation_suggestions
            WHERE status IN ('suggested', 'accepted', 'confirmed')
              AND (source_id = ? OR target_id = ?)
            ORDER BY source_id, target_id, type, confidence
            LIMIT ?
            """,
            (seed_key, seed_key, row_cap),
        ).fetchall()
        for row in [*edge_rows, *suggestion_rows]:
            source_id = str(row["source_id"])
            target_id = str(row["target_id"])
            neighbors: list[str] = []
            if source_id in seed_key_set:
                neighbors.append(target_id)
            if target_id in seed_key_set:
                neighbors.append(source_id)
            for neighbor in neighbors:
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                expanded.append(neighbor)
                if len(expanded) >= cap:
                    existing = _existing_candidate_keys(conn, set(expanded))
                    return [key for key in expanded if key in existing]
    existing = _existing_candidate_keys(conn, set(expanded))
    return [key for key in expanded if key in existing]


def _indexed_candidate_keys(
    conn: sqlite3.Connection,
    query: str,
    rel_query: str,
    limit: int,
) -> tuple[set[str] | None, dict[str, Any]]:
    cap = _candidate_filter_cap(limit)
    fts5_available = _fts5_available(conn)
    health = _retrieval_index_health(conn, fts5_available)
    base_report: dict[str, Any] = {
        "mode": "",
        "fts5Available": bool(fts5_available),
        "fallbackReason": "",
        "totalCandidateCount": 0,
        "candidateCountBeforeScoring": 0,
        "candidateCap": cap,
        "indexStale": bool(health["indexStale"]),
        "indexMissing": bool(health["indexMissing"]),
        "usedFullScan": False,
        "protectedCandidateCount": 0,
        "ordinaryCandidateCountBeforeScoring": 0,
        "candidateCountBeforeGraphExpansion": 0,
        "graphExpansionCount": 0,
        "graphExpansionCap": cap,
        "graphAdjacencyMode": "bounded",
        "health": health,
    }
    if health["indexMissing"] or health["indexStale"]:
        reason = "retrieval_index_missing" if health["indexMissing"] else "retrieval_index_stale"
        base_report.update({"mode": "full_scan", "fallbackReason": reason, "usedFullScan": True})
        return None, base_report

    tokens = _query_tokens(query)
    protected = _dedupe_ordered(_exact_record_keys(conn, query, rel_query))
    ordinary: list[str] = []
    mode = "sql_inverted"
    fallback_reason = ""
    fts_usable = fts5_available and not health["ftsMissing"] and not health["ftsStale"]
    if fts_usable:
        mode = "fts5"
        ordinary, fts_error = _fts_candidate_keys(conn, tokens, cap)
        if fts_error:
            mode = "sql_inverted"
            fallback_reason = fts_error
            ordinary = _sql_inverted_candidate_keys(conn, tokens, cap)
    else:
        if not fts5_available:
            fallback_reason = "fts5_unavailable"
        elif health["ftsMissing"]:
            fallback_reason = "fts_index_missing"
        elif health["ftsStale"]:
            fallback_reason = "fts_index_stale"
        ordinary = _sql_inverted_candidate_keys(conn, tokens, cap)
    session_ordinary = _session_candidate_keys(conn, tokens, cap)
    ordinary = _dedupe_ordered([*ordinary, *session_ordinary])
    total_candidate_count = len(set(protected) | set(ordinary))
    ordinary_selected = ordinary[:cap]
    selected = _dedupe_ordered([*protected, *ordinary_selected])
    candidate_count_before_graph = len(selected)
    graph_expansion = _load_graph_expansion_keys(conn, selected, cap)
    selected = _dedupe_ordered([*selected, *graph_expansion])
    base_report.update(
        {
            "mode": mode,
            "fallbackReason": fallback_reason,
            "totalCandidateCount": total_candidate_count,
            "candidateCountBeforeScoring": len(selected),
            "protectedCandidateCount": len(set(protected)),
            "ordinaryCandidateCountBeforeScoring": len(ordinary_selected),
            "candidateCountBeforeGraphExpansion": candidate_count_before_graph,
            "graphExpansionCount": len(set(graph_expansion) - set(protected) - set(ordinary_selected)),
        }
    )
    return set(selected), base_report


def _text_signal(candidate: dict[str, Any], query: str, rel_query: str, tokens: list[str]) -> dict[str, Any]:
    title = str(candidate.get("title") or "").lower()
    path = str(candidate.get("path") or "").lower()
    heading = str(candidate.get("headingPath") or "").lower()
    candidate_id = str(candidate.get("id") or "").lower()
    search_text = str(candidate.get("searchText") or "")
    query_lower = query.lower()
    rel_query_lower = rel_query.lower()
    points = 0.0
    reasons: list[str] = []
    matched_terms: list[str] = []
    if rel_query_lower and rel_query_lower in {path, candidate_id}:
        points += TEXT_SIGNAL_POINTS["exactPathOrId"]
        reasons.append("exact path or id match")
    if query_lower and query_lower == title:
        points += TEXT_SIGNAL_POINTS["exactTitle"]
        reasons.append("exact title match")
    if query_lower and query_lower in title:
        points += TEXT_SIGNAL_POINTS["queryInTitle"]
        reasons.append("query phrase appears in title")
    if rel_query_lower and rel_query_lower in path:
        points += TEXT_SIGNAL_POINTS["queryInPath"]
        reasons.append("query phrase appears in path")
    if query_lower and query_lower in heading:
        points += TEXT_SIGNAL_POINTS["queryInHeading"]
        reasons.append("query phrase appears in heading")
    if query_lower and len(query_lower) >= 4 and query_lower in search_text:
        points += TEXT_SIGNAL_POINTS["queryInText"]
        reasons.append("query phrase appears in record text")
    for token in tokens:
        token_points = 0.0
        if token in title:
            token_points += TEXT_SIGNAL_POINTS["tokenInTitle"]
        if token in heading:
            token_points += TEXT_SIGNAL_POINTS["tokenInHeading"]
        if token in path:
            token_points += TEXT_SIGNAL_POINTS["tokenInPath"]
        if token in search_text:
            token_points += TEXT_SIGNAL_POINTS["tokenInText"]
        if token_points:
            points += token_points
            matched_terms.append(token)
    if matched_terms:
        reasons.append("matched terms: " + ", ".join(sorted(set(matched_terms))[:8]))
    return {
        "points": round(points, 4),
        "matchedTerms": sorted(set(matched_terms)),
        "reasons": reasons,
    }


def _vector_signals(conn: sqlite3.Connection, query: str, limit: int) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    search_limit = max(50, limit * 12)
    health = _vector_health_fast(conn)
    entry_count = int(health["rowCount"])
    if health["stale"]:
        report = _empty_vector_search_report(
            query,
            search_limit,
            health=health,
            prefilter_mode="disabled",
            fallback_reason="vector_index_stale",
        )
        return {}, {
            **report,
            "used": False,
            "warning": f"Sparse vector index is stale. Run `{VECTOR_REBUILD_COMMAND}`.",
            "health": health,
        }
    if not entry_count:
        report = _empty_vector_search_report(
            query,
            search_limit,
            health=health,
            fallback_reason="vector_index_empty",
        )
        return {}, {
            **report,
            "used": False,
            "warning": f"No sparse vector entries found. Run `{VECTOR_REBUILD_COMMAND}`.",
            "health": health,
        }
    results, report = _search_vector_index_with_report(
        conn,
        query,
        limit=search_limit,
        adapter=LOCAL_SPARSE_ADAPTER,
        health=health,
    )
    signals = {
        str(item["sourceId"]): {
            "rawScore": float(item["score"]),
            "points": round(float(item["score"]) * SPARSE_VECTOR_RETRIEVAL_MULTIPLIER, 4),
            "matchedTerms": item.get("matchedTerms", []),
            "reasons": [
                f"sparse vector score {float(item['score']):.3f}",
            ],
        }
        for item in results
    }
    return signals, {
        **report,
        "used": True,
        "matchedEntries": len(signals),
        "health": health,
        "warning": "",
    }


def _edge_weight(edge_type: str, source: str) -> float:
    if source == "suggestion":
        return GRAPH_SUGGESTION_EDGE_POINTS.get(edge_type, GRAPH_SUGGESTION_FALLBACK_POINTS)
    return GRAPH_DEFAULT_EDGE_POINTS.get(edge_type, GRAPH_DEFAULT_FALLBACK_POINTS)


def _load_adjacency(conn: sqlite3.Connection, node_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not node_ids:
        return adjacency
    keys = sorted(node_ids)
    placeholders = _placeholders(keys)
    row_cap = _graph_edge_row_cap(len(keys))
    edge_rows = conn.execute(
        f"""
        SELECT source_id, target_id, type
        FROM edges
        WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
        ORDER BY source_id, target_id, type
        LIMIT ?
        """,
        [*keys, *keys, row_cap],
    ).fetchall()
    for row in edge_rows:
        source_id = str(row["source_id"])
        target_id = str(row["target_id"])
        edge_type = str(row["type"])
        item = {
            "source": "canonical",
            "type": edge_type,
            "confidence": "canonical",
            "reason": f"canonical edge: {edge_type}",
            "sourceId": source_id,
            "targetId": target_id,
        }
        adjacency[source_id].append({**item, "neighbor": target_id, "direction": "out"})
        adjacency[target_id].append({**item, "neighbor": source_id, "direction": "in"})
    suggestion_rows = conn.execute(
        f"""
        SELECT source_id, target_id, type, confidence, reason, status
        FROM correlation_suggestions
        WHERE status IN ('suggested', 'accepted', 'confirmed')
          AND (source_id IN ({placeholders}) OR target_id IN ({placeholders}))
        ORDER BY source_id, target_id, type, confidence
        LIMIT ?
        """,
        [*keys, *keys, row_cap],
    ).fetchall()
    for row in suggestion_rows:
        source_id = str(row["source_id"])
        target_id = str(row["target_id"])
        status = str(row["status"])
        confidence = str(row["confidence"])
        item = {
            "source": "suggestion",
            "type": str(row["type"]),
            "confidence": confidence,
            "status": status,
            "reason": f"{status} suggestion: {confidence}: {row['reason']}",
            "sourceId": source_id,
            "targetId": target_id,
        }
        adjacency[source_id].append({**item, "neighbor": target_id, "direction": "out"})
        adjacency[target_id].append({**item, "neighbor": source_id, "direction": "in"})
    return adjacency


def _seed_ids(
    candidates: dict[str, dict[str, Any]],
    text_signals: dict[str, dict[str, Any]],
    vector_signals: dict[str, dict[str, Any]],
    semantic_signals: dict[str, dict[str, Any]],
    rel_query: str,
) -> list[str]:
    rel_query_lower = rel_query.lower()
    seeds: list[tuple[float, str]] = []
    for candidate_id, candidate in candidates.items():
        path = str(candidate.get("path") or "").lower()
        title = str(candidate.get("title") or "").lower()
        exact = rel_query_lower and rel_query_lower in {candidate_id.lower(), path, title}
        score = float(text_signals.get(candidate_id, {}).get("points") or 0) + float(
            vector_signals.get(candidate_id, {}).get("points") or 0
        ) + float(
            semantic_signals.get(candidate_id, {}).get("points") or 0
        )
        if exact:
            score += 80.0
        if score >= 14.0:
            seeds.append((score, candidate_id))
    seeds.sort(key=lambda item: (-item[0], item[1]))
    return [candidate_id for _score, candidate_id in seeds[:8]]


def _semantic_pool(
    candidates: dict[str, dict[str, Any]],
    text_signals: dict[str, dict[str, Any]],
    vector_signals: dict[str, dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str]] = []
    for candidate_id, candidate in candidates.items():
        score = float(text_signals.get(candidate_id, {}).get("points") or 0) + float(
            vector_signals.get(candidate_id, {}).get("points") or 0
        )
        if score > 0:
            scored.append((score, candidate_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_ids = [candidate_id for _score, candidate_id in scored[: max(50, limit * 20)]]
    if not selected_ids:
        selected_ids = list(candidates)[: max(50, limit * 20)]
    pool: list[dict[str, Any]] = []
    for candidate_id in selected_ids:
        candidate = candidates[candidate_id]
        pool.append({
            "id": candidate_id,
            "recordType": candidate.get("recordType", ""),
            "type": candidate.get("type", ""),
            "title": candidate.get("title", ""),
            "path": candidate.get("path", ""),
            "headingPath": candidate.get("headingPath", ""),
            "lifecycle": candidate.get("lifecycle", ""),
            "text": str(candidate.get("searchText") or "")[:4000],
        })
    return pool


def _graph_signals(
    conn: sqlite3.Connection,
    candidates: dict[str, dict[str, Any]],
    seeds: list[str],
) -> dict[str, dict[str, Any]]:
    if not seeds:
        return {}
    adjacency = _load_adjacency(conn, set(candidates) | set(seeds))
    signals: dict[str, dict[str, Any]] = {}
    queue: deque[tuple[str, int, str]] = deque((seed, 0, seed) for seed in seeds)
    visited: dict[tuple[str, str], int] = {(seed, seed): 0 for seed in seeds}
    while queue:
        current_id, depth, seed_id = queue.popleft()
        if depth >= 2:
            continue
        for edge in adjacency.get(current_id, []):
            neighbor = str(edge["neighbor"])
            if neighbor not in candidates:
                continue
            next_depth = depth + 1
            visit_key = (neighbor, seed_id)
            if visited.get(visit_key, 99) <= next_depth:
                continue
            visited[visit_key] = next_depth
            queue.append((neighbor, next_depth, seed_id))
            if neighbor == seed_id:
                continue
            edge_type = str(edge["type"])
            source = str(edge["source"])
            confidence = str(edge["confidence"])
            graph_points = _edge_weight(edge_type, source) / next_depth
            confidence_points = CONFIDENCE_POINTS.get(confidence, CONFIDENCE_FALLBACK_POINTS) / next_depth
            signal = signals.setdefault(
                neighbor,
                {
                    "points": 0.0,
                    "confidencePoints": 0.0,
                    "reasons": [],
                    "edgesUsed": [],
                },
            )
            signal["points"] = round(float(signal["points"]) + graph_points, 4)
            signal["confidencePoints"] = round(float(signal["confidencePoints"]) + confidence_points, 4)
            seed_title = candidates.get(seed_id, {}).get("title") or seed_id
            if len(signal["reasons"]) < 6:
                signal["reasons"].append(
                    f"graph proximity via {edge_type} from {seed_title} at depth {next_depth}: {edge['reason']}"
                )
            if len(signal["edgesUsed"]) < 10:
                signal["edgesUsed"].append({
                    "seedId": seed_id,
                    "from": current_id,
                    "to": neighbor,
                    "sourceId": edge.get("sourceId", ""),
                    "targetId": edge.get("targetId", ""),
                    "type": edge_type,
                    "source": source,
                    "confidence": confidence,
                    "status": edge.get("status", ""),
                    "depth": next_depth,
                    "reason": edge.get("reason", ""),
                })
    return signals


def _lifecycle_signal(candidate: dict[str, Any], demotions: list[str]) -> dict[str, Any]:
    lifecycle = str(candidate.get("lifecycle") or "")
    points = LIFECYCLE_POINTS.get(lifecycle, 0.0)
    reasons: list[str] = []
    if lifecycle in VALID_LIFECYCLES:
        reasons.append(f"lifecycle: {lifecycle}")
    if lifecycle in ATTENTION_LIFECYCLES:
        demotions.append(f"lifecycle {lifecycle}")
    return {"points": points, "reasons": reasons}


def _parse_time(value: str) -> _dt.datetime | None:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _recency_signal(candidate: dict[str, Any]) -> dict[str, Any]:
    updated = _parse_time(str(candidate.get("updatedAt") or ""))
    if not updated:
        return {"points": 0.0, "reasons": []}
    now = _dt.datetime.now(_dt.timezone.utc)
    days = max(0, (now - updated).days)
    points = 0.0
    for max_days, recency_points in RECENCY_DAY_POINTS:
        if days <= max_days:
            points = recency_points
            break
    return {"points": points, "reasons": [f"recency: updated {days} day(s) ago"] if points else []}


def _source_trust_signal(candidate: dict[str, Any], scope: str, demotions: list[str]) -> dict[str, Any]:
    candidate_type = str(candidate.get("type") or "")
    path = str(candidate.get("path") or "")
    plane = str(candidate.get("plane") or "")
    facets = candidate.get("documentFacets") if isinstance(candidate.get("documentFacets"), list) else []
    points = 0.0
    reasons: list[str] = []
    if candidate_type == "decision":
        points += SOURCE_TRUST_TYPE_POINTS["decision"]
        reasons.append("source trust: decision record")
    elif candidate_type in {"plan", "research_note", "handoff", "doc_node"}:
        points += SOURCE_TRUST_TYPE_POINTS[candidate_type]
        reasons.append(f"source trust: {candidate_type}")
    elif candidate_type == "chunk":
        points += SOURCE_TRUST_TYPE_POINTS["chunk"]
        reasons.append("source trust: semantic chunk")
    elif candidate_type == "session":
        points += SOURCE_TRUST_TYPE_POINTS["session"]
        demotions.append("session trace not canonical")
        if int(candidate.get("evidenceLinkCount") or 0):
            points += DEFAULT_RETRIEVAL_SCORING.session_evidence_link_points
            reasons.append("source trust: session record with evidence links")
    elif candidate_type in {"test_node", "benchmark"}:
        points += SOURCE_TRUST_TYPE_POINTS[candidate_type]
        reasons.append("source trust: evidence record")
    elif candidate_type == "file_node":
        points += SOURCE_TRUST_TYPE_POINTS["file_node"]
        reasons.append("source trust: source file")
    if candidate.get("sourceRefs"):
        points += DEFAULT_RETRIEVAL_SCORING.source_refs_points
        reasons.append("source trust: source references present")
    if "generated_view" in facets or path.startswith(".controlcoding/views/"):
        points += DEFAULT_RETRIEVAL_SCORING.generated_view_points
        demotions.append("generated view projection")
    if path.startswith(".controlwork/") or path == "CONTROLWORK.md":
        if scope == "project":
            points += DEFAULT_RETRIEVAL_SCORING.project_plane_scope_points
            reasons.append("Project Plane source; verify canonical ControlWork context")
        else:
            points += DEFAULT_RETRIEVAL_SCORING.project_plane_boundary_points
            demotions.append("Project Plane boundary")
    if candidate_type == "application_memory_component" or plane == "application_memory":
        if scope == "application":
            points += DEFAULT_RETRIEVAL_SCORING.application_scope_points
            reasons.append("application-owned memory reference requested by scope")
        else:
            points += DEFAULT_RETRIEVAL_SCORING.application_boundary_points
            demotions.append("application-owned memory boundary")
    return {"points": points, "reasons": reasons}


def _add_report(report: dict[str, Any], category: str, candidate: dict[str, Any], reason: str) -> None:
    report["counts"][category] = int(report["counts"].get(category, 0)) + 1
    if len(report["items"]) < 20:
        report["items"].append({
            "id": candidate["id"],
            "type": candidate["type"],
            "path": candidate.get("path", ""),
            "title": candidate.get("title", ""),
            "category": category,
            "reason": reason,
        })


def _public_match(candidate: dict[str, Any], signals: dict[str, Any], final_score: float, reasons: list[str], demotions: list[str]) -> dict[str, Any]:
    citation = candidate.get("path") or candidate.get("id")
    if candidate.get("headingPath"):
        citation = f"{citation}#{candidate['headingPath']}"
    return {
        "id": candidate["id"],
        "recordType": candidate["recordType"],
        "type": candidate["type"],
        "title": candidate["title"],
        "path": candidate.get("path", ""),
        "headingPath": candidate.get("headingPath", ""),
        "lifecycle": candidate.get("lifecycle", ""),
        "plane": candidate.get("plane", ""),
        "score": round(final_score, 4),
        "signals": signals,
        "reasons": reasons[:12],
        "demotions": demotions[:8],
        "edgesUsed": signals.get("graphProximity", {}).get("edgesUsed", []),
        "citation": citation,
    }


def _schema_warning_code(schema_state: str) -> str:
    if schema_state == "future":
        return "schema_future"
    return "schema_not_queryable"


def _schema_not_queryable_payload(
    query: str,
    scope: str,
    limit: int,
    schema_payload: dict[str, Any],
) -> dict[str, Any]:
    schema_state = str(schema_payload.get("schemaState") or "unknown")
    warning_code = _schema_warning_code(schema_state)
    warning = (
        f"{warning_code}: memory schema is not queryable for read-only retrieval "
        f"(schemaState={schema_state}; "
        f"userVersion={schema_payload.get('userVersion', 0)}; "
        f"metadataVersion={schema_payload.get('metadataVersion')}; "
        f"targetVersion={schema_payload.get('targetVersion', 0)})"
    )
    search_limit = max(50, int(limit or 10) * 12)
    return {
        "ok": True,
        "available": False,
        "degraded": True,
        "message": warning,
        "query": query,
        "scope": scope,
        "limit": limit,
        "signalsUsed": RETRIEVAL_SIGNAL_NAMES,
        "scoring": retrieval_scoring_config_payload(),
        "schema": schema_payload,
        "warnings": [
            {
                "kind": "schema",
                "code": warning_code,
                "message": warning,
                "schema": schema_payload,
            }
        ],
        "vectorIndex": {
            **_empty_vector_search_report(
                query,
                search_limit,
                health={},
                prefilter_mode="disabled",
                fallback_reason=warning_code,
            ),
            "used": False,
            "matchedEntries": 0,
            "health": {},
            "warning": warning,
        },
        "candidateFilter": {
            "mode": "disabled",
            "fallbackReason": warning_code,
            "totalCandidateCount": 0,
            "candidateCountBeforeScoring": 0,
            "candidateCap": _candidate_filter_cap(limit),
            "indexStale": False,
            "indexMissing": False,
            "usedFullScan": False,
            "schema": schema_payload,
            "warning": warning,
        },
        "semanticAdapter": {
            "used": False,
            "warning": warning,
        },
        "graphSeeds": [],
        "matches": [],
        "demotionReport": {"counts": {}, "items": []},
        "exclusionReport": {"counts": {}, "items": []},
    }


def _work_start_retrieve_payload(project: Path, topic: str, scope: str, limit: int) -> dict[str, Any]:
    ok_initialized, message = _require_initialized(project)
    if not ok_initialized:
        return {
            "ok": False,
            "available": False,
            "message": message,
            "query": topic,
            "scope": scope,
            "matches": [],
        }
    if not topic.strip():
        return {
            "ok": False,
            "available": False,
            "message": "Retrieve query is empty. Pass --topic to get targeted memory context.",
            "query": topic,
            "scope": scope,
            "matches": [],
        }
    try:
        payload = _retrieve_payload(project, topic, scope, max(1, min(int(limit or 10), 50)), False)
    except Exception as exc:  # defensive: work-start must report context failures, not hide them
        return {
            "ok": False,
            "available": False,
            "message": f"Memory retrieve unavailable: {exc}",
            "query": topic,
            "scope": scope,
            "matches": [],
        }
    payload["available"] = bool(payload.get("ok", False) and not payload.get("degraded", False))
    if not payload["available"] and not payload.get("message"):
        payload["message"] = "Memory retrieve unavailable."
    return payload


def _retrieve_payload(
    project: Path,
    query: str,
    scope: str,
    limit: int,
    include_archived: bool,
) -> dict[str, Any]:
    try:
        return _retrieve_payload_impl(project, query, scope, limit, include_archived)
    except sqlite3.Error as exc:
        return _sqlite_error_retrieve_payload(project, query, scope, limit, exc)


def _sqlite_error_retrieve_payload(
    project: Path,
    query: str,
    scope: str,
    limit: int,
    exc: sqlite3.Error,
) -> dict[str, Any]:
    error_payload = _memory_db_error_payload(
        project,
        exc,
        operation="memory_retrieve",
        command="python scripts/cc.py memory retrieve --project-root . <query>",
        degraded=True,
    )
    search_limit = max(50, int(limit or 10) * 12)
    warning = {
        "kind": "database",
        "code": error_payload["code"],
        "message": error_payload["message"],
        "databasePath": error_payload["databasePath"],
        "diagnosticCommand": error_payload["diagnosticCommand"],
        "manualAction": error_payload["manualAction"],
        "sqliteError": error_payload["sqliteError"],
    }
    schema_state = (
        "unreadable"
        if error_payload["code"] == "memory_db_unreadable"
        else "sqlite_error"
    )
    return {
        "ok": True,
        "available": False,
        "degraded": True,
        "code": error_payload["code"],
        "error": error_payload,
        "message": error_payload["message"],
        "query": query,
        "scope": scope,
        "limit": limit,
        "signalsUsed": RETRIEVAL_SIGNAL_NAMES,
        "scoring": retrieval_scoring_config_payload(),
        "schema": {
            "schemaState": schema_state,
            "userVersion": 0,
            "metadataVersion": None,
            "targetVersion": SCHEMA_VERSION,
        },
        "warnings": [warning],
        "vectorIndex": {
            **_empty_vector_search_report(
                query,
                search_limit,
                health={},
                prefilter_mode="disabled",
                fallback_reason=error_payload["code"],
            ),
            "used": False,
            "matchedEntries": 0,
            "health": {},
            "warning": error_payload["message"],
        },
        "candidateFilter": {
            "mode": "disabled",
            "fallbackReason": error_payload["code"],
            "totalCandidateCount": 0,
            "candidateCountBeforeScoring": 0,
            "candidateCap": _candidate_filter_cap(limit),
            "indexStale": False,
            "indexMissing": False,
            "usedFullScan": False,
            "warning": error_payload["message"],
        },
        "semanticAdapter": {
            "used": False,
            "warning": error_payload["message"],
        },
        "graphSeeds": [],
        "matches": [],
        "demotionReport": {"counts": {}, "items": []},
        "exclusionReport": {"counts": {}, "items": []},
    }


def _retrieve_payload_impl(
    project: Path,
    query: str,
    scope: str,
    limit: int,
    include_archived: bool,
) -> dict[str, Any]:
    with _readonly_memory_connection(project) as conn:
        inspection = inspect_schema(conn)
        schema_payload = inspection.to_payload()
        if not inspection.has_queryable_current_shape:
            return _schema_not_queryable_payload(query, scope, limit, schema_payload)
        rel_query = _relative_path(project, query)
        selected_keys, candidate_filter = _indexed_candidate_keys(conn, query, rel_query, limit)
        if selected_keys is None:
            candidates = _load_candidates(conn)
            candidate_filter["totalCandidateCount"] = len(candidates)
            candidate_filter["candidateCountBeforeScoring"] = len(candidates)
        else:
            candidates = _load_candidate_details(conn, selected_keys)
            candidate_filter["candidateCountBeforeScoring"] = len(candidates)
        tokens = _query_tokens(query)
        text_signals = {
            candidate_id: _text_signal(candidate, query, rel_query, tokens)
            for candidate_id, candidate in candidates.items()
        }
        vector_signals, vector_report = _vector_signals(conn, query, limit)
        semantic_pool = _semantic_pool(candidates, text_signals, vector_signals, limit)
        semantic_signals, semantic_report = semantic_score_candidates(project, query, semantic_pool)
        seeds = _seed_ids(candidates, text_signals, vector_signals, semantic_signals, rel_query)
        graph_signals = _graph_signals(conn, candidates, seeds)

    excluded = {"counts": {}, "items": []}
    demoted = {"counts": {}, "items": []}
    if not candidate_filter.get("usedFullScan"):
        health = candidate_filter.get("health", {}) if isinstance(candidate_filter.get("health"), dict) else {}
        omitted = max(
            0,
            int(health.get("liveRecordCount", 0) or 0)
            - int(candidate_filter.get("candidateCountBeforeScoring", 0) or 0),
        )
        if omitted:
            excluded["counts"]["candidate_filter_omitted"] = omitted
            excluded["items"].append({
                "id": "candidate_filter",
                "type": "retrieval_filter",
                "path": "",
                "title": "Indexed candidate filter",
                "category": "candidate_filter_omitted",
                "reason": f"{omitted} scan-backed record(s) were outside the bounded candidate set",
            })
    matches: list[dict[str, Any]] = []
    for candidate_id, candidate in candidates.items():
        lifecycle = str(candidate.get("lifecycle") or "")
        if lifecycle in EXCLUDED_LIFECYCLES:
            _add_report(excluded, f"lifecycle:{lifecycle}", candidate, f"excluded lifecycle {lifecycle}")
            continue
        if lifecycle in ARCHIVE_LIFECYCLES and not include_archived:
            _add_report(excluded, f"lifecycle:{lifecycle}", candidate, "archived records require --include-archived")
            continue
        text_signal = text_signals.get(candidate_id, {"points": 0.0, "reasons": []})
        vector_signal = vector_signals.get(candidate_id, {"points": 0.0, "rawScore": 0.0, "matchedTerms": [], "reasons": []})
        semantic_signal = semantic_signals.get(candidate_id, {"points": 0.0, "rawScore": 0.0, "reasons": []})
        graph_signal = graph_signals.get(
            candidate_id,
            {"points": 0.0, "confidencePoints": 0.0, "reasons": [], "edgesUsed": []},
        )
        direct_points = (
            float(text_signal.get("points") or 0.0)
            + float(vector_signal.get("points") or 0.0)
            + float(semantic_signal.get("points") or 0.0)
            + float(graph_signal.get("points") or 0.0)
        )
        if direct_points <= 0:
            _add_report(excluded, "no_positive_signal", candidate, "no text, vector, semantic, or graph signal")
            continue
        demotions: list[str] = []
        lifecycle_signal = _lifecycle_signal(candidate, demotions)
        recency_signal = _recency_signal(candidate)
        source_trust_signal = _source_trust_signal(candidate, scope, demotions)
        signals = {
            "text": text_signal,
            "sparseVector": vector_signal,
            "semantic": semantic_signal,
            "graphProximity": {
                "points": graph_signal.get("points", 0.0),
                "reasons": graph_signal.get("reasons", []),
                "edgesUsed": graph_signal.get("edgesUsed", []),
            },
            "confidence": {
                "points": graph_signal.get("confidencePoints", 0.0),
                "reasons": graph_signal.get("reasons", []),
            },
            "lifecycle": lifecycle_signal,
            "recency": recency_signal,
            "sourceTrust": source_trust_signal,
        }
        final_score = sum(float(signal.get("points") or 0.0) for signal in signals.values())
        if final_score <= 0:
            _add_report(excluded, "non_positive_after_demotions", candidate, "score fell below zero after demotions")
            continue
        for demotion in demotions:
            _add_report(demoted, demotion.replace(" ", "_"), candidate, demotion)
        reasons: list[str] = []
        for key in ("text", "sparseVector", "semantic", "graphProximity", "confidence", "lifecycle", "recency", "sourceTrust"):
            signal_reasons = signals[key].get("reasons") if isinstance(signals[key], dict) else []
            if isinstance(signal_reasons, list):
                reasons.extend(str(reason) for reason in signal_reasons)
        matches.append(_public_match(candidate, signals, final_score, reasons, demotions))

    matches.sort(key=lambda item: (-float(item["score"]), item["path"], item["title"], item["id"]))
    return {
        "ok": True,
        "available": True,
        "degraded": False,
        "message": "",
        "query": query,
        "scope": scope,
        "limit": limit,
        "signalsUsed": RETRIEVAL_SIGNAL_NAMES,
        "scoring": retrieval_scoring_config_payload(),
        "schema": schema_payload,
        "warnings": [],
        "vectorIndex": vector_report,
        "candidateFilter": candidate_filter,
        "semanticAdapter": semantic_report,
        "graphSeeds": seeds,
        "matches": matches[:limit],
        "demotionReport": demoted,
        "exclusionReport": excluded,
    }


def _retrieve_text(payload: dict[str, Any]) -> str:
    lines = [
        f"Memory retrieve: {payload['query']}",
        "Signals: text, sparse vector, semantic, graph proximity, lifecycle, confidence, recency, source trust",
    ]
    if not payload["vectorIndex"].get("used"):
        lines.append(f"Vector warning: {payload['vectorIndex'].get('warning', '')}")
    candidate_filter = payload.get("candidateFilter", {})
    if candidate_filter.get("usedFullScan"):
        lines.append(f"Candidate filter warning: {candidate_filter.get('fallbackReason', 'full_scan')}")
    if payload.get("semanticAdapter", {}).get("used"):
        lines.append(f"Semantic adapter: {payload['semanticAdapter'].get('adapter', '')}")
    for warning in payload.get("warnings", []):
        if isinstance(warning, dict):
            lines.append(f"Retrieval warning: {warning.get('message', '')}")
    if not payload["matches"]:
        lines.append("No matching memory records found.")
    else:
        lines.append("")
        lines.append("Matches:")
        for item in payload["matches"]:
            location = item["path"] or item["id"]
            if item["headingPath"]:
                location = f"{location} - {item['headingPath']}"
            lines.append(
                f"- {item['score']:.2f} {location} ({item['recordType']}, {item['lifecycle']})"
            )
            if item["reasons"]:
                lines.append("  Reasons: " + "; ".join(item["reasons"][:5]))
            if item["demotions"]:
                lines.append("  Demotions: " + "; ".join(item["demotions"][:4]))
            lines.append(f"  Citation: {item['citation']}")
    lines.append("")
    lines.append("Exclusion and demotion report:")
    excluded_counts = payload["exclusionReport"]["counts"]
    demoted_counts = payload["demotionReport"]["counts"]
    lines.append("  Excluded: " + (", ".join(f"{key}={value}" for key, value in sorted(excluded_counts.items())) or "none"))
    lines.append("  Demoted: " + (", ".join(f"{key}={value}" for key, value in sorted(demoted_counts.items())) or "none"))
    return "\n".join(lines)


def cmd_memory_retrieve(
    project: Path,
    query: str,
    scope: str = "general",
    limit: int = 10,
    include_archived: bool = False,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_or_text(
            json_output,
            {
                "ok": False,
                "error": "memory_not_initialized",
                "message": message,
            },
            f"Error: {message}",
        )
        return 1
    query = query.strip()
    if not query:
        message = "retrieve query is required"
        _print_json_or_text(
            json_output,
            {
                "ok": False,
                "error": "retrieve_query_required",
                "message": message,
            },
            f"Error: {message}",
        )
        return 1
    limit = max(1, min(int(limit or 10), 50))
    scope = (scope or "general").strip().lower()
    payload = _retrieve_payload(project, query, scope, limit, include_archived)
    _print_json_or_text(json_output, payload, _retrieve_text(payload))
    return 0
