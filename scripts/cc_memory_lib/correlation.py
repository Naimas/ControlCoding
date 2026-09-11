"""Cross-document correlation suggestions for the Project Memory Engine."""

from __future__ import annotations

import hashlib
import posixpath
import sqlite3
from pathlib import PurePosixPath
from typing import Any

from .entities import _row_to_dict
from .store import _json_dumps, _now_iso

_WEAK_KEYWORD_MINIMUM = 4
_MAX_DOCUMENTS_FOR_PAIRWISE = 500
_REVIEWED_SUGGESTION_STATUSES = {"accepted", "confirmed", "rejected"}


def _suggestion_id(source_id: str, target_id: str, relation_type: str, confidence: str) -> str:
    seed = f"{source_id}:{target_id}:{relation_type}:{confidence}"
    return "CORR_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    seed = f"{source_id}:{target_id}:{edge_type}"
    return "EDGE_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _normalize_ref(source_path: str, ref: str) -> str:
    ref = ref.replace("\\", "/").split("#", 1)[0].strip()
    if not ref:
        return ""
    if ref.startswith("/"):
        return posixpath.normpath(ref.lstrip("/"))
    parent = PurePosixPath(source_path).parent
    return posixpath.normpath((parent / ref).as_posix())


def _entity_data(entity: dict[str, Any]) -> dict[str, Any]:
    data = entity.get("data")
    return data if isinstance(data, dict) else {}


def _terms(entity: dict[str, Any], key: str) -> set[str]:
    data = _entity_data(entity)
    values = data.get(key)
    if not isinstance(values, list):
        return set()
    return {str(value).lower() for value in values if str(value).strip()}


def _insert_suggestion(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    relation_type: str,
    confidence: str,
    reason: str,
    data: dict[str, Any] | None = None,
) -> None:
    if source_id == target_id:
        return
    suggestion_id = _suggestion_id(source_id, target_id, relation_type, confidence)
    existing = conn.execute(
        "SELECT status FROM correlation_suggestions WHERE id = ?",
        (suggestion_id,),
    ).fetchone()
    if existing and str(existing["status"]) in _REVIEWED_SUGGESTION_STATUSES:
        return
    now = _now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO correlation_suggestions(
          id, source_id, target_id, type, confidence, reason, status, data,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
          (SELECT created_at FROM correlation_suggestions WHERE id = ?),
          ?
        ), ?)
        """,
        (
            suggestion_id,
            source_id,
            target_id,
            relation_type,
            confidence,
            reason,
            "suggested",
            _json_dumps(data or {}),
            suggestion_id,
            now,
            now,
        ),
    )


def _insert_canonical_edge(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    edge_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO edges(id, source_id, target_id, type, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _edge_id(source_id, target_id, edge_type),
            source_id,
            target_id,
            edge_type,
            _json_dumps(data or {}),
            _now_iso(),
        ),
    )


def _load_document_entities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM entities
        WHERE path IS NOT NULL AND path != ''
          AND type IN (
            'application_memory_component',
            'benchmark',
            'decision',
            'doc_node',
            'handoff',
            'plan',
            'research_note',
            'test_node'
          )
        ORDER BY path
        """
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _refresh_correlation_suggestions(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM correlation_suggestions WHERE status = 'suggested'")
    docs = _load_document_entities(conn)[:_MAX_DOCUMENTS_FOR_PAIRWISE]
    by_path = {str(doc.get("path")): doc for doc in docs if doc.get("path")}
    by_path_lower = {path.lower(): doc for path, doc in by_path.items()}
    created = 0

    for doc in docs:
        source_id = str(doc.get("id"))
        source_path = str(doc.get("path") or "")
        for ref in sorted(_terms(doc, "explicit_refs")):
            target = by_path.get(ref) or by_path_lower.get(ref.lower())
            target_path = str(target.get("path")) if target else _normalize_ref(source_path, ref)
            target = target or by_path.get(target_path) or by_path_lower.get(target_path.lower())
            if not target:
                continue
            target_id = str(target.get("id"))
            _insert_suggestion(
                conn,
                source_id,
                target_id,
                "references",
                "explicit_link",
                f"{source_path} links to {target_path}",
                {"source_path": source_path, "target_path": target_path},
            )
            _insert_canonical_edge(
                conn,
                source_id,
                target_id,
                "references",
                {"source_path": source_path, "target_path": target_path},
            )
            created += 1

    for index, left in enumerate(docs):
        left_id = str(left.get("id"))
        left_path = str(left.get("path") or "")
        left_headings = _terms(left, "heading_keys")
        left_keywords = _terms(left, "keywords")
        for right in docs[index + 1 :]:
            right_id = str(right.get("id"))
            right_path = str(right.get("path") or "")
            common_headings = sorted(left_headings & _terms(right, "heading_keys"))
            common_headings = [heading for heading in common_headings if heading]
            if common_headings:
                reason = "shared heading: " + common_headings[0]
                data = {"shared_headings": common_headings[:8], "paths": [left_path, right_path]}
                _insert_suggestion(
                    conn,
                    left_id,
                    right_id,
                    "related_heading",
                    "strong_title_or_heading_match",
                    reason,
                    data,
                )
                _insert_suggestion(
                    conn,
                    right_id,
                    left_id,
                    "related_heading",
                    "strong_title_or_heading_match",
                    reason,
                    data,
                )
                created += 2
                continue
            common_keywords = sorted(left_keywords & _terms(right, "keywords"))
            if len(common_keywords) >= _WEAK_KEYWORD_MINIMUM:
                reason = "shared keywords: " + ", ".join(common_keywords[:6])
                data = {"shared_keywords": common_keywords[:12], "paths": [left_path, right_path]}
                _insert_suggestion(
                    conn,
                    left_id,
                    right_id,
                    "related_terms",
                    "weak_lexical_similarity",
                    reason,
                    data,
                )
                _insert_suggestion(
                    conn,
                    right_id,
                    left_id,
                    "related_terms",
                    "weak_lexical_similarity",
                    reason,
                    data,
                )
                created += 2
    return created


__all__ = ["_refresh_correlation_suggestions"]
