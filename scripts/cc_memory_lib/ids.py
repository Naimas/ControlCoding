"""Readable ID and project-short helpers for project memory."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from .schema import ENTITY_TYPE_CODES
from .store import _manifest_path, _read_json, _today_yyyymmdd

def _derive_project_short(project: Path) -> str:
    words = re.findall(r"[A-Za-z0-9]+", project.name)
    if not words:
        return "Project"
    compact = "".join(word[:1].upper() + word[1:] for word in words)
    return compact[:24] or "Project"

def _load_manifest(project: Path) -> dict[str, Any]:
    return _read_json(_manifest_path(project))

def _project_short(project: Path, explicit: str = "") -> str:
    explicit = explicit.strip()
    if explicit:
        return _slug_component(explicit, fallback="Project", max_len=24)
    manifest = _load_manifest(project)
    stored = str(manifest.get("project_short", "")).strip()
    if stored:
        return _slug_component(stored, fallback="Project", max_len=24)
    return _derive_project_short(project)

def _slug_component(text: str, fallback: str = "General", max_len: int = 48) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words:
        return fallback
    slug = "".join(word[:1].upper() + word[1:] for word in words)
    return slug[:max_len] or fallback

def make_memory_entity_id(
    project_short: str,
    entity_type: str,
    area: str,
    topic: str,
    date_yyyymmdd: str | None = None,
    application_owned: bool = False,
) -> str:
    """Build a readable stable memory ID.

    ControlCoding development memory uses CC_<Project>_DEV_...
    Application-owned memory references use <Project>_MEM_...
    """
    project_token = _slug_component(project_short, fallback="Project", max_len=24)
    type_code = ENTITY_TYPE_CODES.get(entity_type, entity_type.upper())
    type_token = _slug_component(type_code, fallback="DOC", max_len=20).upper()
    area_token = _slug_component(area, fallback="General", max_len=36)
    topic_token = _slug_component(topic, fallback=type_token.title(), max_len=56)
    date_token = date_yyyymmdd or _today_yyyymmdd()
    if application_owned:
        return f"{project_token}_MEM_{type_token}_{area_token}_{topic_token}_{date_token}"
    return f"CC_{project_token}_DEV_{type_token}_{area_token}_{topic_token}_{date_token}"

def _entity_exists(conn: sqlite3.Connection, entity_id: str) -> bool:
    return conn.execute("SELECT 1 FROM entities WHERE id = ?", (entity_id,)).fetchone() is not None

def _unique_entity_id(
    conn: sqlite3.Connection,
    project_short: str,
    entity_type: str,
    area: str,
    topic: str,
    date_yyyymmdd: str,
    path_hint: str = "",
) -> str:
    application_owned = entity_type == "application_memory_component"
    entity_id = make_memory_entity_id(
        project_short,
        entity_type,
        area,
        topic,
        date_yyyymmdd,
        application_owned=application_owned,
    )
    if not _entity_exists(conn, entity_id):
        return entity_id
    if path_hint:
        topic = f"{topic} {hashlib.sha1(path_hint.encode('utf-8')).hexdigest()[:8]}"
    counter = 2
    while True:
        candidate = make_memory_entity_id(
            project_short,
            entity_type,
            area,
            f"{topic} {counter}",
            date_yyyymmdd,
            application_owned=application_owned,
        )
        if not _entity_exists(conn, candidate):
            return candidate
        counter += 1
