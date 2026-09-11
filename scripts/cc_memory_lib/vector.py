"""Derived local vector index adapter for the Project Memory Engine.

The adapter is intentionally stdlib-only. It stores sparse lexical weights that
can be rebuilt from semantic chunks. It is not canonical memory.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .ledger import _insert_event
from .scoring import DEFAULT_RETRIEVAL_SCORING
from .store import (
    _ensure_schema,
    _get_metadata,
    _json_dumps,
    _memory_db_error_payload,
    _memory_db_error_text,
    _memory_connection,
    _now_iso,
    _print_json_or_text,
    _require_initialized,
    _set_metadata,
)

LOCAL_SPARSE_ADAPTER = "local_sparse_v1"
VECTOR_REBUILD_COMMAND = "python scripts/cc.py memory vector rebuild --project-root ."
VECTOR_PREFILTER_MIN_CAP = DEFAULT_RETRIEVAL_SCORING.vector_prefilter.min_cap
VECTOR_PREFILTER_MAX_CAP = DEFAULT_RETRIEVAL_SCORING.vector_prefilter.max_cap
VECTOR_PREFILTER_LIMIT_MULTIPLIER = DEFAULT_RETRIEVAL_SCORING.vector_prefilter.limit_multiplier

_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "before",
    "between",
    "but",
    "can",
    "code",
    "controlcoding",
    "document",
    "for",
    "from",
    "has",
    "have",
    "into",
    "memory",
    "not",
    "only",
    "project",
    "should",
    "that",
    "the",
    "their",
    "this",
    "under",
    "use",
    "when",
    "with",
    "without",
}


def _tokenize_vector_text(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_/-]{2,}", text)
        if token.lower() not in _STOP_WORDS
    ]


def _sparse_terms(*parts: str) -> dict[str, float]:
    counts = Counter(_tokenize_vector_text(" ".join(parts)))
    if not counts:
        return {}
    total = sum(counts.values())
    return {
        term: round(count / total, 8)
        for term, count in sorted(counts.items())
    }


def _norm(terms: dict[str, float]) -> float:
    return math.sqrt(sum(weight * weight for weight in terms.values()))


def _entry_id(adapter: str, source_id: str) -> str:
    seed = f"{adapter}:{source_id}"
    return "VEC_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _value in values)


def _chunk_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, source_path, heading_path, content_hash, summary, keywords, metadata, content_preview
        FROM semantic_chunks
        ORDER BY source_path, ordinal, sequence_index
        """
    ).fetchall()


def _keywords_text(value: str) -> str:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, list):
        return ""
    return " ".join(str(item) for item in parsed)


def _metadata_text(value: str) -> str:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    parts: list[str] = []
    for key in (
        "structural_level",
        "block_type",
        "semantic_role",
        "context_policy",
        "extraction_confidence",
    ):
        raw_value = parsed.get(key)
        if raw_value:
            parts.append(str(raw_value))
    for key in ("structural_types", "topics", "subtopics", "domains"):
        values = parsed.get(key)
        if isinstance(values, list):
            parts.extend(str(item) for item in values)
    return " ".join(parts)


def _chunk_vector_terms(row: sqlite3.Row) -> tuple[dict[str, float], float]:
    terms = _sparse_terms(
        str(row["source_path"] or ""),
        str(row["heading_path"] or ""),
        str(row["summary"] or ""),
        _keywords_text(str(row["keywords"] or "")),
        _metadata_text(str(row["metadata"] or "{}")),
        str(row["content_preview"] or ""),
    )
    return terms, _norm(terms)


def _rebuild_vector_index(conn: sqlite3.Connection, adapter: str = LOCAL_SPARSE_ADAPTER) -> dict[str, Any]:
    now = _now_iso()
    rows = _chunk_rows(conn)
    conn.execute("DELETE FROM derived_vector_terms WHERE adapter = ?", (adapter,))
    conn.execute("DELETE FROM derived_vector_index WHERE adapter = ?", (adapter,))
    indexed = 0
    skipped = 0
    posting_rows = 0
    posting_sources = 0
    for row in rows:
        terms, term_norm = _chunk_vector_terms(row)
        if not terms or term_norm <= 0:
            skipped += 1
            continue
        source_id = str(row["id"])
        conn.execute(
            """
            INSERT INTO derived_vector_index(
              id, adapter, source_type, source_id, source_path, heading_path,
              content_hash, terms, term_count, norm, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _entry_id(adapter, source_id),
                adapter,
                "semantic_chunk",
                source_id,
                str(row["source_path"] or ""),
                str(row["heading_path"] or ""),
                str(row["content_hash"] or ""),
                _json_dumps(terms),
                len(terms),
                term_norm,
                now,
                now,
            ),
        )
        for term, weight in sorted(terms.items()):
            conn.execute(
                """
                INSERT INTO derived_vector_terms(adapter, term, source_id, term_weight)
                VALUES (?, ?, ?, ?)
                """,
                (adapter, term, source_id, float(weight)),
            )
            posting_rows += 1
        posting_sources += 1
        indexed += 1
    _set_metadata(conn, "last_vector_rebuild_at", now)
    _set_metadata(conn, "last_vector_adapter", adapter)
    _set_metadata(conn, "last_vector_entry_count", str(indexed))
    _set_metadata(conn, "last_vector_skipped_count", str(skipped))
    _set_metadata(conn, "last_vector_posting_rows", str(posting_rows))
    _set_metadata(conn, "last_vector_posting_sources", str(posting_sources))
    return {
        "adapter": adapter,
        "indexed": indexed,
        "skipped": skipped,
        "postingRows": posting_rows,
        "postingSources": posting_sources,
        "rebuiltAt": now,
    }


def _metadata_int(conn: sqlite3.Connection, key: str, default: int = 0) -> int:
    try:
        return int(_get_metadata(conn, key, str(default)) or default)
    except ValueError:
        return default


def _vector_posting_stats(conn: sqlite3.Connection, adapter: str = LOCAL_SPARSE_ADAPTER) -> dict[str, int]:
    posting_row = conn.execute(
        """
        SELECT COUNT(*) AS posting_rows, COUNT(DISTINCT source_id) AS posting_sources
        FROM derived_vector_terms
        WHERE adapter = ?
        """,
        (adapter,),
    ).fetchone()
    term_row = conn.execute(
        """
        SELECT COALESCE(SUM(term_count), 0) AS vector_term_rows
        FROM derived_vector_index
        WHERE adapter = ?
        """,
        (adapter,),
    ).fetchone()
    missing_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM derived_vector_index v
        LEFT JOIN (
          SELECT DISTINCT source_id
          FROM derived_vector_terms
          WHERE adapter = ?
        ) t ON t.source_id = v.source_id
        WHERE v.adapter = ? AND t.source_id IS NULL
        """,
        (adapter, adapter),
    ).fetchone()
    orphan_row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM (
          SELECT DISTINCT t.source_id
          FROM derived_vector_terms t
          LEFT JOIN derived_vector_index v
            ON v.adapter = t.adapter AND v.source_id = t.source_id
          WHERE t.adapter = ? AND v.source_id IS NULL
        ) orphan_sources
        """,
        (adapter,),
    ).fetchone()
    return {
        "postingRows": int(posting_row["posting_rows"] or 0),
        "postingSources": int(posting_row["posting_sources"] or 0),
        "vectorTermRows": int(term_row["vector_term_rows"] or 0),
        "missingPostingSources": int(missing_row["count"] or 0),
        "orphanPostingSources": int(orphan_row["count"] or 0),
    }


def _vector_posting_stale_reasons(posting_stats: dict[str, int]) -> list[str]:
    reasons: list[str] = []
    if int(posting_stats.get("missingPostingSources", 0) or 0):
        reasons.append("missing_vector_postings")
    if int(posting_stats.get("orphanPostingSources", 0) or 0):
        reasons.append("orphan_vector_postings")
    if int(posting_stats.get("postingRows", 0) or 0) != int(posting_stats.get("vectorTermRows", 0) or 0):
        reasons.append("vector_posting_count_mismatch")
    return reasons


def _vector_health_fast(conn: sqlite3.Connection, adapter: str = LOCAL_SPARSE_ADAPTER) -> dict[str, Any]:
    semantic_count = int(conn.execute("SELECT COUNT(*) AS count FROM semantic_chunks").fetchone()["count"])
    vector_row = conn.execute(
        """
        SELECT COUNT(*) AS row_count
        FROM derived_vector_index
        WHERE adapter = ?
        """,
        (adapter,),
    ).fetchone()
    row_count = int(vector_row["row_count"] or 0)
    hash_mismatches = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM derived_vector_index v
            JOIN semantic_chunks c ON c.id = v.source_id
            WHERE v.adapter = ?
              AND (
                COALESCE(v.content_hash, '') != COALESCE(c.content_hash, '')
                OR COALESCE(v.source_path, '') != COALESCE(c.source_path, '')
              )
            """,
            (adapter,),
        ).fetchone()["count"]
        or 0
    )
    orphan_rows = int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM derived_vector_index v
            LEFT JOIN semantic_chunks c ON c.id = v.source_id
            WHERE v.adapter = ? AND c.id IS NULL
            """,
            (adapter,),
        ).fetchone()["count"]
        or 0
    )
    last_rebuilt = _get_metadata(conn, "last_vector_rebuild_at")
    skipped_count = _metadata_int(conn, "last_vector_skipped_count")
    missing_rows = 0
    stale_reasons: list[str] = []
    if last_rebuilt and semantic_count != row_count + skipped_count:
        missing_rows = max(0, semantic_count - row_count - skipped_count)
        stale_reasons.append("semantic_chunk_count_mismatch")
    if hash_mismatches:
        stale_reasons.append("content_hash_mismatch")
    if orphan_rows:
        stale_reasons.append("orphan_vector_rows")
    posting_stats = _vector_posting_stats(conn, adapter)
    stale_reasons.extend(_vector_posting_stale_reasons(posting_stats))
    stale = bool(stale_reasons)
    return {
        "adapter": adapter,
        "rowCount": row_count,
        "semanticChunks": semantic_count,
        "indexableChunks": row_count,
        "skippedChunks": skipped_count,
        "lastRebuiltAt": last_rebuilt,
        "stale": stale,
        "staleReasons": stale_reasons,
        "missingRows": missing_rows,
        "contentHashMismatches": hash_mismatches,
        "orphanRows": orphan_rows,
        **posting_stats,
        "rebuildCommand": VECTOR_REBUILD_COMMAND if stale else "",
    }


def _vector_health(conn: sqlite3.Connection, adapter: str = LOCAL_SPARSE_ADAPTER) -> dict[str, Any]:
    chunk_rows = _chunk_rows(conn)
    vector_rows = conn.execute(
        """
        SELECT source_id, source_path, content_hash
        FROM derived_vector_index
        WHERE adapter = ?
        """,
        (adapter,),
    ).fetchall()
    vector_by_source = {str(row["source_id"]): row for row in vector_rows}
    indexable_chunks: dict[str, sqlite3.Row] = {}
    skipped_chunks = 0
    for row in chunk_rows:
        terms, term_norm = _chunk_vector_terms(row)
        if terms and term_norm > 0:
            indexable_chunks[str(row["id"])] = row
        else:
            skipped_chunks += 1
    chunk_count = len(chunk_rows)
    indexable_count = len(indexable_chunks)
    row_count = len(vector_rows)
    missing_rows = sum(1 for chunk_id in indexable_chunks if chunk_id not in vector_by_source)
    hash_mismatches = sum(
        1
        for chunk_id, chunk in indexable_chunks.items()
        if chunk_id in vector_by_source
        and (
            str(vector_by_source[chunk_id]["content_hash"] or "") != str(chunk["content_hash"] or "")
            or str(vector_by_source[chunk_id]["source_path"] or "") != str(chunk["source_path"] or "")
        )
    )
    orphan_rows = sum(1 for row in vector_rows if str(row["source_id"]) not in indexable_chunks)
    last_rebuilt = _get_metadata(conn, "last_vector_rebuild_at")
    stale_reasons: list[str] = []
    if missing_rows:
        stale_reasons.append("missing_vector_rows")
    if hash_mismatches:
        stale_reasons.append("content_hash_mismatch")
    if orphan_rows:
        stale_reasons.append("orphan_vector_rows")
    posting_stats = _vector_posting_stats(conn, adapter)
    stale_reasons.extend(_vector_posting_stale_reasons(posting_stats))
    stale = bool(stale_reasons)
    return {
        "adapter": adapter,
        "rowCount": row_count,
        "semanticChunks": chunk_count,
        "indexableChunks": indexable_count,
        "skippedChunks": skipped_chunks,
        "lastRebuiltAt": last_rebuilt,
        "stale": stale,
        "staleReasons": stale_reasons,
        "missingRows": missing_rows,
        "contentHashMismatches": hash_mismatches,
        "orphanRows": orphan_rows,
        **posting_stats,
        "rebuildCommand": VECTOR_REBUILD_COMMAND if stale else "",
    }


def cmd_memory_vector_rebuild(project: Path, json_output: bool = False) -> int:
    try:
        return _cmd_memory_vector_rebuild_impl(project, json_output=json_output)
    except sqlite3.Error as exc:
        payload = _memory_db_error_payload(
            project,
            exc,
            operation="memory_vector_rebuild",
            command="python scripts/cc.py memory vector rebuild --project-root .",
        )
        _print_json_or_text(json_output, payload, _memory_db_error_text(payload))
        return 1


def _cmd_memory_vector_rebuild_impl(project: Path, json_output: bool = False) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_or_text(
            json_output,
            {
                "ok": False,
                "error": "vector_memory_not_initialized",
                "message": message,
            },
            f"Error: {message}",
        )
        return 1
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        result = _rebuild_vector_index(conn)
        _insert_event(
            conn,
            project,
            "rebuild_index",
            "cc memory vector rebuild",
            after_state=result,
            reason="Rebuilt derived local sparse vector index from semantic chunks",
            provenance={"source": "cc memory vector", "canonical": False},
        )
    _print_json_or_text(
        json_output,
        {"ok": True, **result},
        (
            "Memory vector index rebuilt\n"
            f"  Adapter: {result['adapter']}\n"
            f"  Indexed chunks: {result['indexed']}\n"
            f"  Skipped chunks: {result['skipped']}\n"
            f"  Posting rows: {result['postingRows']}"
        ),
    )
    return 0


def _load_terms(value: str) -> dict[str, float]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    loaded: dict[str, float] = {}
    for key, raw_value in parsed.items():
        try:
            loaded[str(key)] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return loaded


def _score_terms(query_terms: dict[str, float], query_norm: float, row: sqlite3.Row) -> float:
    row_terms = _load_terms(str(row["terms"] or "{}"))
    row_norm = float(row["norm"] or 0)
    if query_norm <= 0 or row_norm <= 0:
        return 0.0
    dot = sum(weight * row_terms.get(term, 0.0) for term, weight in query_terms.items())
    return dot / (query_norm * row_norm)


def _vector_prefilter_cap(limit: int) -> int:
    requested = max(1, int(limit or 10)) * VECTOR_PREFILTER_LIMIT_MULTIPLIER
    return max(1, min(VECTOR_PREFILTER_MAX_CAP, max(VECTOR_PREFILTER_MIN_CAP, requested)))


def _empty_vector_search_report(
    query: str,
    limit: int,
    adapter: str = LOCAL_SPARSE_ADAPTER,
    health: dict[str, Any] | None = None,
    prefilter_mode: str = "postings",
    fallback_reason: str = "",
) -> dict[str, Any]:
    query_terms = _sparse_terms(query)
    health = health or {}
    return {
        "adapter": adapter,
        "entryCount": int(health.get("rowCount", 0) or 0),
        "prefilterMode": prefilter_mode,
        "prefilterCap": _vector_prefilter_cap(limit),
        "queryTermCount": len(query_terms),
        "candidateSourceCount": 0,
        "candidateCountBeforeCosine": 0,
        "scoredVectorRows": 0,
        "usedFullVectorScan": False,
        "postingRows": int(health.get("postingRows", 0) or 0),
        "postingSources": int(health.get("postingSources", 0) or 0),
        "fallbackReason": fallback_reason,
    }


def _search_vector_index_with_report(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    adapter: str = LOCAL_SPARSE_ADAPTER,
    health: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query_terms = _sparse_terms(query)
    query_norm = _norm(query_terms)
    if health is None:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM derived_vector_index WHERE adapter = ?",
            (adapter,),
        ).fetchone()
        health = {"rowCount": int(row["count"] or 0), **_vector_posting_stats(conn, adapter)}
    report = _empty_vector_search_report(query, limit, adapter, health=health)
    if not query_terms or query_norm <= 0:
        report["fallbackReason"] = "no_query_terms"
        return [], report
    unique_terms = sorted(query_terms)
    prefilter_cap = int(report["prefilterCap"])
    candidate_rows = conn.execute(
        f"""
        SELECT source_id, SUM(term_weight) AS rank, COUNT(*) AS matched_terms
        FROM derived_vector_terms
        WHERE adapter = ? AND term IN ({_placeholders(unique_terms)})
        GROUP BY source_id
        ORDER BY rank DESC, matched_terms DESC, source_id
        LIMIT ?
        """,
        [adapter, *unique_terms, prefilter_cap],
    ).fetchall()
    candidate_source_ids = [str(row["source_id"]) for row in candidate_rows]
    report["candidateSourceCount"] = len(candidate_source_ids)
    if not candidate_source_ids:
        return [], report
    rows = conn.execute(
        f"""
        SELECT v.*, c.summary, c.lifecycle
        FROM derived_vector_index v
        LEFT JOIN semantic_chunks c ON c.id = v.source_id
        WHERE v.adapter = ? AND v.source_id IN ({_placeholders(candidate_source_ids)})
        ORDER BY v.source_path, v.heading_path
        """,
        [adapter, *candidate_source_ids],
    ).fetchall()
    report["candidateCountBeforeCosine"] = len(rows)
    scored: list[tuple[float, sqlite3.Row]] = []
    scored_rows = 0
    for row in rows:
        scored_rows += 1
        score = _score_terms(query_terms, query_norm, row)
        if score > 0:
            scored.append((score, row))
    report["scoredVectorRows"] = scored_rows
    scored.sort(key=lambda item: (-item[0], str(item[1]["source_path"]), str(item[1]["heading_path"])))
    results: list[dict[str, Any]] = []
    for score, row in scored[: max(1, limit)]:
        row_terms = _load_terms(str(row["terms"] or "{}"))
        matched_terms = sorted(set(query_terms) & set(row_terms))
        results.append(
            {
                "score": round(score, 6),
                "adapter": adapter,
                "sourceId": row["source_id"],
                "sourcePath": row["source_path"],
                "headingPath": row["heading_path"],
                "summary": row["summary"] or "",
                "lifecycle": row["lifecycle"] or "",
                "matchedTerms": matched_terms[:12],
            }
        )
    return results, report


def _search_vector_index(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 10,
    adapter: str = LOCAL_SPARSE_ADAPTER,
) -> list[dict[str, Any]]:
    results, _report = _search_vector_index_with_report(conn, query, limit=limit, adapter=adapter)
    return results


def cmd_memory_vector_search(
    project: Path,
    query: str,
    limit: int = 10,
    json_output: bool = False,
) -> int:
    try:
        return _cmd_memory_vector_search_impl(
            project,
            query,
            limit=limit,
            json_output=json_output,
        )
    except sqlite3.Error as exc:
        payload = _memory_db_error_payload(
            project,
            exc,
            operation="memory_vector_search",
            command="python scripts/cc.py memory vector search --project-root . <query>",
        )
        _print_json_or_text(json_output, payload, _memory_db_error_text(payload))
        return 1


def _cmd_memory_vector_search_impl(
    project: Path,
    query: str,
    limit: int = 10,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_or_text(
            json_output,
            {
                "ok": False,
                "error": "vector_memory_not_initialized",
                "message": message,
            },
            f"Error: {message}",
        )
        return 1
    limit = max(1, min(limit, 50))
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        health = _vector_health_fast(conn)
        entry_count = int(health["rowCount"])
        search_limit = limit
        if health["stale"]:
            results = []
            warning = f"Sparse vector index is stale. Run `{VECTOR_REBUILD_COMMAND}`."
            vector_report = _empty_vector_search_report(
                query,
                search_limit,
                health=health,
                prefilter_mode="disabled",
                fallback_reason="vector_index_stale",
            )
            used = False
        elif not entry_count:
            results = []
            warning = f"No vector index entries found. Run `{VECTOR_REBUILD_COMMAND}`."
            vector_report = _empty_vector_search_report(
                query,
                search_limit,
                health=health,
                fallback_reason="vector_index_empty",
            )
            used = False
        else:
            results, vector_report = _search_vector_index_with_report(
                conn,
                query,
                limit=search_limit,
                health=health,
            )
            warning = ""
            used = True
        _set_metadata(conn, "last_vector_search_at", _now_iso())
        _set_metadata(conn, "last_vector_search_query", query)
    vector_index = {
        **vector_report,
        "used": used,
        "matchedEntries": len(results),
        "health": health,
        "warning": warning,
    }
    payload = {
        "ok": True,
        "query": query,
        "adapter": LOCAL_SPARSE_ADAPTER,
        "entryCount": entry_count,
        "vectorHealth": health,
        "vectorIndex": vector_index,
        "warning": warning,
        "matches": results,
    }
    if json_output:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"Memory vector search: {query}")
    if warning:
        print(warning)
        return 0
    if not results:
        print("No vector matches found.")
        return 0
    for item in results:
        print(
            f"- {item['score']:.3f} {item['sourcePath']} - {item['headingPath']}"
        )
        if item["matchedTerms"]:
            print(f"  Terms: {', '.join(item['matchedTerms'])}")
        if item["summary"]:
            print(f"  Summary: {item['summary'][:220]}")
    return 0


__all__ = [
    "LOCAL_SPARSE_ADAPTER",
    "VECTOR_REBUILD_COMMAND",
    "cmd_memory_vector_rebuild",
    "cmd_memory_vector_search",
    "_empty_vector_search_report",
    "_search_vector_index_with_report",
    "_vector_health",
    "_vector_health_fast",
]
