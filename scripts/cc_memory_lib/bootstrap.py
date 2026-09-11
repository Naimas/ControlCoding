"""Read-only bootstrap status for ControlCoding memory planes."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from .graph import GRAPH_CONTRACT_VERSION
from .migrations import inspect_schema
from .schema import CONTROL_DIRNAME, SCHEMA_VERSION, VIEWS_DIRNAME
from .store import (
    MEMORY_DB_UNREADABLE_CODE,
    _connect_readonly_db,
    _db_path,
    _manifest_path,
    _memory_db_error_payload,
    _print_json_or_text,
    _read_json,
    _relative_path,
    _views_dir,
)
from .vector import _vector_health

CONTROLWORK_CONTEXT_FILENAME = "CONTROLWORK.md"
CONTROLWORK_DIRNAME = ".controlwork"
CONTROLWORK_MEMORY_DIRNAME = "memory"
CONTROLWORK_MEMORY_AREAS = [
    "inbox",
    "sources",
    "notes",
    "ideas",
    "decisions",
    "plans",
    "outputs",
    "legacy",
    "views",
]
DEV_CONTEXT_PACKET_DIRNAME = "context-packets"
PROJECT_CONTEXT_SCOPES = {
    "general",
    "research",
    "planning",
    "analysis",
    "writing",
    "ux",
    "ui",
    "frontend",
    "backend",
    "architecture",
    "implementation",
    "bugfix",
    "refactor",
    "review",
    "handoff",
}
BOOTSTRAP_MEMORY_COMMAND = "python scripts/cc.py memory bootstrap --project-root ."


def _utc_from_timestamp(timestamp: float) -> str:
    return (
        _dt.datetime.fromtimestamp(timestamp, tz=_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _file_info(project: Path, path: Path) -> dict[str, Any]:
    exists = path.exists()
    info: dict[str, Any] = {
        "path": _relative_path(project, path),
        "exists": exists,
    }
    if exists:
        stat = path.stat()
        info.update({
            "isFile": path.is_file(),
            "isDirectory": path.is_dir(),
            "sizeBytes": stat.st_size if path.is_file() else None,
            "updatedAt": _utc_from_timestamp(stat.st_mtime),
        })
    return info


def _list_markdown_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.md") if path.is_file())


def _latest_file(project: Path, root: Path) -> dict[str, Any]:
    files = _list_markdown_files(root)
    return _latest_from_files(project, files)


def _latest_from_files(project: Path, files: list[Path]) -> dict[str, Any]:
    if not files:
        return {}
    latest = max(files, key=lambda path: path.stat().st_mtime)
    return _file_info(project, latest)


def _list_rag_packet_files(root: Path) -> list[Path]:
    packets: list[Path] = []
    for path in _list_markdown_files(root):
        try:
            sample = path.read_text(encoding="utf-8", errors="replace")[:512]
        except OSError:
            continue
        if "ControlCoding GraphRAG Packet" in sample:
            packets.append(path)
    return packets


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    return _connect_readonly_db(db_path)


def _bootstrap_database_error_payload(project: Path, exc: sqlite3.Error) -> dict[str, Any]:
    payload = _memory_db_error_payload(
        project,
        exc,
        operation="memory_bootstrap",
        command=BOOTSTRAP_MEMORY_COMMAND,
        degraded=True,
    )
    if payload["code"] != MEMORY_DB_UNREADABLE_CODE:
        payload = dict(payload)
        sqlite_error = str(payload.get("sqliteError") or exc.__class__.__name__)
        payload["message"] = (
            f"{payload['code']}: memory database SQLite read failed at "
            f"{payload['databasePath']}: {sqlite_error}. "
            f"Run `{payload['diagnosticCommand']}`. "
            "If the database is locked or busy, retry after the concurrent writer "
            "finishes. No automatic repair was attempted."
        )
        payload["manualAction"] = (
            "No automatic repair was attempted. If the database is locked or busy, "
            "retry after the concurrent writer finishes; if the error persists, "
            "preserve memory.db and run the diagnostic command before any manual "
            "restore or reinitialization."
        )
    return payload


def _database_error_schema(error_payload: dict[str, Any]) -> dict[str, Any]:
    schema_state = (
        "unreadable"
        if error_payload.get("code") == MEMORY_DB_UNREADABLE_CODE
        else "sqlite_error"
    )
    return {
        "schemaState": schema_state,
        "userVersion": 0,
        "metadataVersion": None,
        "targetVersion": SCHEMA_VERSION,
    }


def _database_error_warning(error_payload: dict[str, Any]) -> str:
    return (
        "Dev Plane database read degraded: "
        f"code={error_payload['code']}; "
        f"sqliteError={error_payload['sqliteError']}; "
        f"diagnostic={error_payload['diagnosticCommand']}; "
        f"manualAction={error_payload['manualAction']}"
    )


def _database_error_result(
    project: Path,
    exc: sqlite3.Error,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    error_payload = _bootstrap_database_error_payload(project, exc)
    return {
        "databaseError": error_payload,
        "schema": _database_error_schema(error_payload),
    }, {}, [_database_error_warning(error_payload)]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _count_rows(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    query = f"SELECT COUNT(*) AS count FROM {table}"
    if where:
        query += f" WHERE {where}"
    row = conn.execute(query, params).fetchone()
    return int(row["count"]) if row else 0


def _count_by(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    if not _table_exists(conn, table):
        return {}
    rows = conn.execute(
        f"SELECT {column} AS key, COUNT(*) AS count FROM {table} GROUP BY {column} ORDER BY {column}"
    ).fetchall()
    return {str(row["key"]): int(row["count"]) for row in rows}


def _metadata(conn: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(conn, "metadata"):
        return {}
    rows = conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def _load_json_dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _entity_summary(row: sqlite3.Row) -> dict[str, Any]:
    data = _load_json_dict(str(row["data"] or "{}")) if "data" in row.keys() else {}
    return {
        "id": str(row["id"]),
        "type": str(row["type"]),
        "title": str(row["title"]),
        "path": str(row["path"] or ""),
        "lifecycle": str(row["lifecycle"]),
        "updatedAt": str(row["updated_at"]),
        "documentType": str(data.get("document_type") or ""),
        "documentFacets": data.get("document_facets") if isinstance(data.get("document_facets"), list) else [],
    }


def _recent_entities(conn: sqlite3.Connection, where: str = "", params: tuple[Any, ...] = (), limit: int = 8) -> list[dict[str, Any]]:
    if not _table_exists(conn, "entities"):
        return []
    query = "SELECT id, type, title, path, lifecycle, data, updated_at FROM entities"
    if where:
        query += f" WHERE {where}"
    query += " ORDER BY updated_at DESC, title ASC LIMIT ?"
    rows = conn.execute(query, (*params, limit)).fetchall()
    return [_entity_summary(row) for row in rows]


def _read_dev_database(project: Path, db_path: Path) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        return _database_error_result(project, exc)
    try:
        inspection = inspect_schema(conn)
        schema_payload = inspection.to_payload()
        if not inspection.has_queryable_current_shape:
            return {
                "schema": schema_payload,
                "sqliteSchemaVersion": (
                    str(inspection.metadata_version)
                    if inspection.metadata_version is not None
                    else ""
                ),
            }, {}, [
                "Dev Plane schema is not queryable: "
                f"schemaState={inspection.state}; "
                f"userVersion={inspection.user_version}; "
                f"metadataVersion={inspection.metadata_version}; "
                f"targetVersion={inspection.target_version}"
            ]
        metadata = _metadata(conn)
        counts = {
            "entities": _count_rows(conn, "entities"),
            "semanticChunks": _count_rows(conn, "semantic_chunks"),
            "edges": _count_rows(conn, "edges"),
            "events": _count_rows(conn, "events"),
            "vectorRows": _count_rows(conn, "derived_vector_index"),
            "applicationMemoryComponents": _count_rows(
                conn,
                "entities",
                "type = ?",
                ("application_memory_component",),
            ),
        }
        data = {
            "metadata": metadata,
            "counts": counts,
            "entityTypes": _count_by(conn, "entities", "type"),
            "lifecycles": _count_by(conn, "entities", "lifecycle"),
            "suggestionCounts": _count_by(conn, "correlation_suggestions", "status"),
            "graphContractVersion": GRAPH_CONTRACT_VERSION,
            "sqliteSchemaVersion": metadata.get("schema_version") or "",
            "schema": schema_payload,
            "recentEntities": _recent_entities(
                conn,
                "path IS NOT NULL AND path != '' "
                "AND path NOT LIKE '.controlwork/%' "
                "AND path != 'CONTROLWORK.md'",
                limit=8,
            ),
            "activeFocus": _recent_entities(
                conn,
                "lifecycle IN ('active', 'implemented', 'triaged', 'needs_review') "
                "AND type IN ('decision', 'plan', 'idea', 'note', 'work_item')",
                limit=8,
            ),
            "applicationMemoryComponents": _recent_entities(
                conn,
                "type = ?",
                ("application_memory_component",),
                limit=8,
            ),
            "vectorHealth": _vector_health(conn),
        }
        return data, metadata, warnings
    except sqlite3.Error as exc:
        return _database_error_result(project, exc)
    finally:
        conn.close()


def _artifact_status(project: Path, db_data: dict[str, Any], metadata: dict[str, str]) -> dict[str, Any]:
    views_root = _views_dir(project)
    view_files = _list_markdown_files(views_root)
    chunk_count = int(db_data.get("counts", {}).get("semanticChunks") or 0)
    vector_count = int(db_data.get("counts", {}).get("vectorRows") or 0)
    entity_count = int(db_data.get("counts", {}).get("entities") or 0)
    last_scan = metadata.get("last_scan_at") or ""
    last_vector_rebuild = metadata.get("last_vector_rebuild_at") or ""
    last_views_generated = metadata.get("last_views_generated_at") or ""
    vector_health = db_data.get("vectorHealth")
    if not isinstance(vector_health, dict):
        vector_health = {
            "adapter": metadata.get("last_vector_adapter") or "local_sparse_v1",
            "rowCount": vector_count,
            "semanticChunks": chunk_count,
            "lastRebuiltAt": last_vector_rebuild,
            "stale": bool(chunk_count and (not last_vector_rebuild or vector_count < chunk_count)),
            "staleReasons": [],
            "missingRows": 0,
            "contentHashMismatches": 0,
            "orphanRows": 0,
            "rebuildCommand": "python scripts/cc.py memory vector rebuild --project-root ."
            if bool(chunk_count and (not last_vector_rebuild or vector_count < chunk_count))
            else "",
        }

    dev_context_root = project / CONTROL_DIRNAME / DEV_CONTEXT_PACKET_DIRNAME
    rag_packet_files = _list_rag_packet_files(dev_context_root)
    work_context_root = project / CONTROLWORK_DIRNAME / "context-packets"
    work_views_root = project / CONTROLWORK_DIRNAME / CONTROLWORK_MEMORY_DIRNAME / "views"

    return {
        "views": {
            "path": _relative_path(project, views_root),
            "exists": views_root.exists(),
            "fileCount": len(view_files),
            "lastGeneratedAt": last_views_generated,
            "latestFile": _latest_file(project, views_root),
            "stale": bool(entity_count and (not view_files or not last_views_generated)),
        },
        "vectors": vector_health,
        "devContextPackets": {
            "path": _relative_path(project, dev_context_root),
            "exists": dev_context_root.exists(),
            "fileCount": len(_list_markdown_files(dev_context_root)),
            "latestFile": _latest_file(project, dev_context_root),
        },
        "graphPackets": {
            "path": _relative_path(project, dev_context_root),
            "exists": dev_context_root.exists(),
            "fileCount": len(rag_packet_files),
            "latestFile": _latest_from_files(project, rag_packet_files),
            "implemented": True,
            "stale": False if rag_packet_files else None,
            "note": "GraphRAG packets are explicit rag-pack snapshots stored with Dev Plane context packets.",
        },
        "projectPlaneViews": {
            "path": _relative_path(project, work_views_root),
            "exists": work_views_root.exists(),
            "fileCount": len(_list_markdown_files(work_views_root)),
            "latestFile": _latest_file(project, work_views_root),
        },
        "projectPlaneContextPackets": {
            "path": _relative_path(project, work_context_root),
            "exists": work_context_root.exists(),
            "fileCount": len(_list_markdown_files(work_context_root)),
            "latestFile": _latest_file(project, work_context_root),
        },
        "lastScanAt": last_scan,
    }


def _controlwork_area_counts(root: Path) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    memory_root = root / CONTROLWORK_MEMORY_DIRNAME
    for area in CONTROLWORK_MEMORY_AREAS:
        area_path = memory_root / area
        if not area_path.exists() or not area_path.is_dir():
            counts[area] = None
            continue
        counts[area] = len([path for path in area_path.iterdir() if path.is_file() and path.name != ".gitkeep"])
    return counts


def _project_plane(project: Path) -> dict[str, Any]:
    root = project / CONTROLWORK_DIRNAME
    context_path = project / CONTROLWORK_CONTEXT_FILENAME
    config_path = root / "config.json"
    link_path = root / "link.json"
    config = _read_json(config_path)
    link = _read_json(link_path)
    external_path = Path(str(link.get("externalPath", ""))).expanduser() if link.get("externalPath") else None
    external_validation = {}
    if external_path:
        resolved = external_path.resolve()
        external_validation = {
            "path": str(resolved),
            "hasCanonicalContext": (resolved / CONTROLWORK_CONTEXT_FILENAME).exists(),
            "hasConfig": (resolved / CONTROLWORK_DIRNAME / "config.json").exists(),
            "hasMemoryRoot": (resolved / CONTROLWORK_DIRNAME / CONTROLWORK_MEMORY_DIRNAME).exists(),
        }
        external_validation["ok"] = all(
            bool(external_validation[key])
            for key in ("hasCanonicalContext", "hasConfig", "hasMemoryRoot")
        )
    return {
        "hasControlWorkRoot": root.exists(),
        "hasCanonicalContext": context_path.exists(),
        "hasConfig": bool(config),
        "canonicalContext": CONTROLWORK_CONTEXT_FILENAME,
        "distribution": str(config.get("distribution") or "") if config else "",
        "standaloneCompatible": bool(config.get("standaloneCompatible")) if config else False,
        "memoryCounts": _controlwork_area_counts(root),
        "attachedExternal": bool(link),
        "link": {
            "exists": bool(link),
            "path": _relative_path(project, link_path),
            "syncPolicy": str(link.get("syncPolicy") or ""),
            "externalPath": str(external_path.resolve()) if external_path else "",
            "attachedAt": str(link.get("attachedAt") or ""),
            "validation": external_validation,
        },
    }


def _recommendations(
    project: Path,
    dev_plane: dict[str, Any],
    project_plane: dict[str, Any],
    derived: dict[str, Any],
    topic: str,
    scope: str,
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    short = dev_plane.get("manifest", {}).get("project_short") or project.name.replace(" ", "")
    project_scope = scope if scope in PROJECT_CONTEXT_SCOPES else "general"
    if not dev_plane["initialized"]:
        recommendations.append({
            "reason": "Dev Plane memory is not initialized.",
            "command": f"python scripts/cc.py memory init --project-root . --mode full --project-short {short}",
            "writes": True,
        })
        if project_plane["hasControlWorkRoot"]:
            recommendations.append({
                "reason": "Project Plane exists; inspect it separately before any sync.",
                "command": "python scripts/cc.py memory work-status --project-root .",
                "writes": False,
            })
        return recommendations

    if not derived.get("lastScanAt"):
        recommendations.append({
            "reason": "Dev Plane has no recorded scan timestamp.",
            "command": "python scripts/cc.py memory scan --project-root .",
            "writes": True,
        })
    if derived["vectors"]["stale"]:
        recommendations.append({
            "reason": "Sparse vectors are stale or missing for current chunks.",
            "command": "python scripts/cc.py memory vector rebuild --project-root .",
            "writes": True,
        })
    if derived["views"]["stale"]:
        recommendations.append({
            "reason": "Generated Dev Plane views are stale or missing.",
            "command": "python scripts/cc.py memory views generate --project-root .",
            "writes": True,
        })
    suggested = int(dev_plane.get("suggestionCounts", {}).get("suggested") or 0)
    if suggested:
        recommendations.append({
            "reason": f"{suggested} graph correlation suggestion(s) need review.",
            "command": "python scripts/cc.py memory graph suggestions --project-root .",
            "writes": False,
        })
    if project_plane["hasControlWorkRoot"] or project_plane["hasCanonicalContext"]:
        recommendations.append({
            "reason": "Project Plane is present and should be inspected explicitly.",
            "command": "python scripts/cc.py memory work-status --project-root .",
            "writes": False,
        })
        if not derived["projectPlaneContextPackets"]["fileCount"]:
            command_topic = topic or "current focus"
            recommendations.append({
                "reason": "No Project Plane context packet is available for this topic.",
                "command": (
                    "python scripts/cc.py memory work-context-pack --project-root . "
                    f"--scope {project_scope} --topic \"{command_topic}\""
                ),
                "writes": True,
            })
    if int(dev_plane.get("counts", {}).get("applicationMemoryComponents") or 0):
        recommendations.append({
            "reason": "Application-owned memory references exist; keep runtime truth in the application.",
            "command": "Use application-approved adapters only.",
            "writes": False,
        })
    return recommendations


def _warnings(dev_plane: dict[str, Any], project_plane: dict[str, Any], derived: dict[str, Any], db_warnings: list[str]) -> list[str]:
    warnings = list(db_warnings)
    schema = dev_plane.get("schema", {}) if isinstance(dev_plane.get("schema"), dict) else {}
    schema_state = str(schema.get("schemaState") or "")
    if schema_state in {"legacy_adoptable", "old_compatible"}:
        warnings.append(
            "Dev Plane schema can be migrated: "
            f"schemaState={schema_state}; "
            f"userVersion={schema.get('userVersion', 0)}; "
            f"metadataVersion={schema.get('metadataVersion')}; "
            f"targetVersion={schema.get('targetVersion', SCHEMA_VERSION)}."
        )
    if not dev_plane["initialized"]:
        warnings.append("Dev Plane memory is missing or incomplete; bootstrap did not create it.")
    if derived["vectors"]["stale"]:
        warnings.append("Sparse vector index is stale or missing.")
    if derived["views"]["stale"]:
        warnings.append("Dev Plane generated views are stale or missing.")
    if project_plane["attachedExternal"] and not project_plane["link"].get("validation", {}).get("ok", False):
        warnings.append("Linked external ControlWork project is not fully valid.")
    if project_plane["attachedExternal"]:
        warnings.append("ControlWork sync is manual and explicit; bootstrap did not pull or push.")
    if int(dev_plane.get("counts", {}).get("applicationMemoryComponents") or 0):
        warnings.append("Application-owned memory is referenced for project context only, not owned by ControlCoding.")
    return warnings


def _format_count_map(values: dict[str, int | None]) -> str:
    present = [
        f"{key}={value if value is not None else 'missing'}"
        for key, value in sorted(values.items())
    ]
    return ", ".join(present) if present else "none"


def _bootstrap_text(payload: dict[str, Any]) -> str:
    dev = payload["devPlane"]
    project = payload["projectPlane"]
    derived = payload["derivedArtifacts"]
    app = payload["applicationOwnedMemory"]
    lines = [
        "Memory bootstrap",
        f"  Scope: {payload['scope']}",
        f"  Topic: {payload['topic'] or '(none)'}",
        "  Mode: read-only, no sync, no hidden writes",
        "",
        "Dev Plane",
        f"  Initialized: {dev['initialized']}",
        f"  Manifest: {dev['hasManifest']}",
        f"  Database: {dev['hasDatabase']}",
        f"  Schema state: {dev.get('schema', {}).get('schemaState', 'missing')}",
        f"  User version: {dev.get('schema', {}).get('userVersion', 0)}",
        f"  Metadata version: {dev.get('schema', {}).get('metadataVersion')}",
        f"  Target version: {dev.get('schema', {}).get('targetVersion', SCHEMA_VERSION)}",
        f"  Counts: entities={dev.get('counts', {}).get('entities', 0)}, chunks={dev.get('counts', {}).get('semanticChunks', 0)}, vectors={dev.get('counts', {}).get('vectorRows', 0)}",
        f"  Suggestions: {_format_count_map(dev.get('suggestionCounts', {}))}",
        "",
        "Project Plane",
        f"  ControlWork root: {project['hasControlWorkRoot']}",
        f"  Canonical context: {project['hasCanonicalContext']}",
        f"  Attached external: {project['attachedExternal']}",
        f"  Sync policy: {project['link'].get('syncPolicy') or '(none)'}",
        f"  Areas: {_format_count_map(project['memoryCounts'])}",
        "",
        "Application-owned memory",
        f"  Referenced components: {app['referencedComponents']}",
        "  Boundary: application runtime memory remains application-owned",
        "",
        "Derived artifacts",
        f"  Views: files={derived['views']['fileCount']}, stale={derived['views']['stale']}",
        f"  Vectors: rows={derived['vectors']['rowCount']}, stale={derived['vectors']['stale']}",
        f"  Dev context packets: files={derived['devContextPackets']['fileCount']}",
        f"  Graph packets: files={derived['graphPackets']['fileCount']}, implemented={derived['graphPackets']['implemented']}",
        f"  Project Plane context packets: files={derived['projectPlaneContextPackets']['fileCount']}",
        "",
        "Hot documents",
    ]
    if derived["vectors"].get("stale"):
        lines.insert(
            -2,
            f"  Vector rebuild command: {derived['vectors'].get('rebuildCommand')}",
        )
    hot_docs = payload["hotDocuments"]
    if hot_docs:
        for item in hot_docs[:8]:
            lines.append(f"  - {item['title']} [{item['lifecycle']}] {item['path']}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Active focus")
    active_focus = payload["activeFocus"]
    if active_focus:
        for item in active_focus[:8]:
            lines.append(f"  - {item['title']} ({item['type']}, {item['lifecycle']})")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Recommended maintenance")
    if payload["recommendations"]:
        for item in payload["recommendations"]:
            lines.append(f"  - {item['reason']} {item['command']}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Warnings")
    if payload["warnings"]:
        for warning in payload["warnings"]:
            lines.append(f"  - {warning}")
    else:
        lines.append("  - none")
    return "\n".join(lines)


def _bootstrap_payload(
    project: Path,
    scope: str = "general",
    topic: str = "",
) -> dict[str, Any]:
    project = project.resolve()
    manifest_path = _manifest_path(project)
    db_path = _db_path(project)
    manifest = _read_json(manifest_path)
    db_data: dict[str, Any] = {}
    db_metadata: dict[str, str] = {}
    db_warnings: list[str] = []
    if db_path.exists():
        db_data, db_metadata, db_warnings = _read_dev_database(project, db_path)
    derived = _artifact_status(project, db_data, db_metadata)
    project_plane = _project_plane(project)
    dev_plane = {
        "hasControlDir": (project / CONTROL_DIRNAME).exists(),
        "hasManifest": manifest_path.exists(),
        "hasDatabase": db_path.exists(),
        "initialized": bool(manifest_path.exists() and db_path.exists() and not db_warnings),
        "manifest": manifest,
        "databasePath": _relative_path(project, db_path),
        "graphContractVersion": GRAPH_CONTRACT_VERSION,
        "sqliteSchemaVersion": db_data.get("sqliteSchemaVersion") or "",
        "schema": db_data.get("schema", {
            "schemaState": "missing",
            "userVersion": 0,
            "metadataVersion": None,
            "targetVersion": SCHEMA_VERSION,
        }),
        "counts": db_data.get("counts", {}),
        "entityTypes": db_data.get("entityTypes", {}),
        "lifecycles": db_data.get("lifecycles", {}),
        "suggestionCounts": db_data.get("suggestionCounts", {}),
    }
    if isinstance(db_data.get("databaseError"), dict) and db_data["databaseError"]:
        dev_plane["databaseError"] = db_data["databaseError"]
    app_memory = {
        "referencedComponents": int(db_data.get("counts", {}).get("applicationMemoryComponents") or 0),
        "records": db_data.get("applicationMemoryComponents", []),
        "boundary": "Application runtime memory remains application-owned; ControlCoding may reference approved project documents only.",
    }
    payload = {
        "ok": True,
        "projectRoot": str(project),
        "scope": scope or "general",
        "topic": topic,
        "mode": "read_only_no_sync_no_hidden_writes",
        "devPlane": dev_plane,
        "projectPlane": project_plane,
        "applicationOwnedMemory": app_memory,
        "derivedArtifacts": derived,
        "hotDocuments": db_data.get("recentEntities", []),
        "activeFocus": db_data.get("activeFocus", []),
        "recommendations": _recommendations(
            project,
            dev_plane,
            project_plane,
            derived,
            topic,
            scope,
        ),
        "warnings": [],
    }
    payload["warnings"] = _warnings(dev_plane, project_plane, derived, db_warnings)
    return payload


def cmd_memory_bootstrap(
    project: Path,
    scope: str = "general",
    topic: str = "",
    json_output: bool = False,
) -> int:
    payload = _bootstrap_payload(project, scope=scope, topic=topic)
    _print_json_or_text(json_output, payload, _bootstrap_text(payload))
    return 0
