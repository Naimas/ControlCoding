"""Event and source ledger helpers for project memory."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .schema import EVENT_TYPES
from .store import _json_dumps, _logs_dir, _now_iso, _transactional_append_jsonl

def _source_id_for_ref(ref: str) -> str:
    return "SRC_" + hashlib.sha1(ref.encode("utf-8")).hexdigest()[:16].upper()

def _upsert_source(
    conn: sqlite3.Connection,
    source_type: str,
    ref: str,
    title: str = "",
    data: dict[str, Any] | None = None,
) -> str:
    source_id = _source_id_for_ref(f"{source_type}:{ref}")
    conn.execute(
        """
        INSERT OR IGNORE INTO sources(id, type, ref, title, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source_id, source_type, ref, title, _json_dumps(data or {}), _now_iso()),
    )
    return source_id

def _insert_event(
    conn: sqlite3.Connection,
    project: Path,
    event_type: str,
    command: str,
    target_entity_ids: list[str] | None = None,
    affected_paths: list[str] | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    reason: str = "",
    provenance: dict[str, Any] | None = None,
    review_required: bool = False,
    mirror_log: str = "",
) -> str:
    if event_type not in EVENT_TYPES:
        event_type = "review"
    timestamp = _now_iso()
    event_id = f"EVT_{timestamp.replace('-', '').replace(':', '').replace('Z', '')}_{uuid.uuid4().hex[:8].upper()}"
    row = {
        "id": event_id,
        "type": event_type,
        "timestamp": timestamp,
        "actor_type": "cli",
        "command": command,
        "target_entity_ids": target_entity_ids or [],
        "affected_paths": affected_paths or [],
        "before_state": before_state or {},
        "after_state": after_state or {},
        "reason": reason,
        "provenance": provenance or {"source": "cc memory"},
        "review_required": bool(review_required),
    }
    conn.execute(
        """
        INSERT INTO events(
          id, type, timestamp, actor_type, command, target_entity_ids,
          affected_paths, before_state, after_state, reason, provenance,
          review_required
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["type"],
            row["timestamp"],
            row["actor_type"],
            row["command"],
            _json_dumps(row["target_entity_ids"]),
            _json_dumps(row["affected_paths"]),
            _json_dumps(row["before_state"]),
            _json_dumps(row["after_state"]),
            row["reason"],
            _json_dumps(row["provenance"]),
            1 if row["review_required"] else 0,
        ),
    )
    _transactional_append_jsonl(conn, _logs_dir(project) / "event_log.jsonl", row)
    if mirror_log:
        _transactional_append_jsonl(conn, _logs_dir(project) / mirror_log, row)
    return event_id
