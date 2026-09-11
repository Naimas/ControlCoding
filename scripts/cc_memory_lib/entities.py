"""Entity persistence helpers for project memory."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .ids import _project_short, _unique_entity_id
from .store import _today_yyyymmdd
from .ledger import _insert_event, _upsert_source
from .schema import VALID_LIFECYCLES
from .store import (
    _content_hash_text,
    _ensure_schema,
    _json_dumps,
    _memory_connection,
    _now_iso,
    _relative_path,
)

def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    for key in ("source_refs", "provenance", "data"):
        try:
            data[key] = json.loads(data.get(key) or "{}")
        except json.JSONDecodeError:
            data[key] = {} if key != "source_refs" else []
    return data

def _find_entity_by_path(conn: sqlite3.Connection, rel_path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM entities WHERE path = ? ORDER BY created_at LIMIT 1",
        (rel_path,),
    ).fetchone()

def _record_entity(
    project: Path,
    entity_type: str,
    title: str,
    body: str = "",
    area: str = "General",
    lifecycle: str = "captured",
    path: str = "",
    data: dict[str, Any] | None = None,
    source_refs: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
    event_type: str = "create",
    command: str = "cc memory",
    mirror_log: str = "",
    transactional_filesystem_action: Callable[[sqlite3.Connection], None] | None = None,
    require_unique_path: bool = False,
) -> dict[str, Any]:
    if lifecycle not in VALID_LIFECYCLES:
        lifecycle = "captured"
    project_short = _project_short(project)
    now = _now_iso()
    rel_path = _relative_path(project, path) if path else ""
    content_hash = _content_hash_text(body) if body else ""
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        if require_unique_path and rel_path and _find_entity_by_path(conn, rel_path) is not None:
            raise FileExistsError(f"memory entity path already exists: {rel_path}")
        entity_id = _unique_entity_id(
            conn,
            project_short,
            entity_type,
            area,
            title,
            _today_yyyymmdd(),
            path_hint=rel_path or title,
        )
        normalized_sources = []
        for source in source_refs or []:
            normalized_sources.append(_upsert_source(conn, "reference", source, source))
        if transactional_filesystem_action is not None:
            transactional_filesystem_action(conn)
        entity_data = data or {}
        entity_data.setdefault("area", area)
        entity_provenance = provenance or {"source": command}
        conn.execute(
            """
            INSERT INTO entities(
              id, type, title, project_short, plane, path, lifecycle,
              content_hash, source_refs, provenance, body, data,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                entity_type,
                title.strip(),
                project_short,
                "controlcoding_dev",
                rel_path,
                lifecycle,
                content_hash,
                _json_dumps(normalized_sources),
                _json_dumps(entity_provenance),
                body,
                _json_dumps(entity_data),
                now,
                now,
            ),
        )
        _insert_event(
            conn,
            project,
            event_type,
            command,
            target_entity_ids=[entity_id],
            affected_paths=[rel_path] if rel_path else [],
            after_state={
                "type": entity_type,
                "title": title,
                "lifecycle": lifecycle,
                "data": entity_data,
            },
            reason=f"Recorded {entity_type}",
            provenance=entity_provenance,
            mirror_log=mirror_log,
        )
    return {
        "id": entity_id,
        "type": entity_type,
        "title": title,
        "lifecycle": lifecycle,
        "path": rel_path,
    }
