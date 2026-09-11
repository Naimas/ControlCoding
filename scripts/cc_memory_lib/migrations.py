"""SQLite schema inspection and migrations for project memory."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .schema import CONTROLCODING_VERSION, SCHEMA_VERSION

TARGET_SCHEMA_VERSION = SCHEMA_VERSION

CORE_TABLES = frozenset({
    "schema_version",
    "metadata",
    "entities",
    "edges",
    "events",
    "sources",
    "views",
})
SEMANTIC_TABLES = CORE_TABLES | frozenset({
    "semantic_chunks",
    "correlation_suggestions",
})
VECTOR_TABLES = SEMANTIC_TABLES | frozenset({
    "derived_vector_index",
})
COMPAT_TABLES = VECTOR_TABLES
CURRENT_TABLES = COMPAT_TABLES | frozenset({
    "document_layout_nodes",
    "document_layout_edges",
    "derived_vector_terms",
    "session_records",
    "session_edges",
    "retrieval_records",
    "retrieval_terms",
    "retrieval_term_stats",
})
OPTIONAL_TABLES = frozenset({"retrieval_fts"})
SQLITE_INTERNAL_PREFIXES = ("sqlite_",)

VERSION_TABLES = {
    1: CORE_TABLES,
    2: SEMANTIC_TABLES,
    3: VECTOR_TABLES,
    4: COMPAT_TABLES,
    5: CURRENT_TABLES,
}

TABLE_COLUMNS = {
    "schema_version": frozenset({"version", "applied_at"}),
    "metadata": frozenset({"key", "value"}),
    "entities": frozenset({
        "id",
        "type",
        "title",
        "project_short",
        "plane",
        "path",
        "lifecycle",
        "content_hash",
        "source_refs",
        "provenance",
        "body",
        "data",
        "created_at",
        "updated_at",
    }),
    "edges": frozenset({
        "id",
        "source_id",
        "target_id",
        "type",
        "data",
        "created_at",
    }),
    "events": frozenset({
        "id",
        "type",
        "timestamp",
        "actor_type",
        "command",
        "target_entity_ids",
        "affected_paths",
        "before_state",
        "after_state",
        "reason",
        "provenance",
        "review_required",
    }),
    "sources": frozenset({"id", "type", "ref", "title", "data", "created_at"}),
    "views": frozenset({
        "path",
        "title",
        "generated_at",
        "command",
        "source_counts",
        "stale_warning",
        "content_hash",
    }),
    "semantic_chunks": frozenset({
        "id",
        "document_id",
        "source_path",
        "heading_path",
        "ordinal",
        "sequence_index",
        "parent_chunk_id",
        "previous_chunk_id",
        "next_chunk_id",
        "lifecycle",
        "content_hash",
        "summary",
        "keywords",
        "metadata",
        "content_preview",
        "created_at",
        "updated_at",
    }),
    "correlation_suggestions": frozenset({
        "id",
        "source_id",
        "target_id",
        "type",
        "confidence",
        "reason",
        "status",
        "data",
        "created_at",
        "updated_at",
    }),
    "derived_vector_index": frozenset({
        "id",
        "adapter",
        "source_type",
        "source_id",
        "source_path",
        "heading_path",
        "content_hash",
        "terms",
        "term_count",
        "norm",
        "created_at",
        "updated_at",
    }),
    "document_layout_nodes": frozenset({
        "id",
        "document_id",
        "source_path",
        "node_type",
        "parent_id",
        "ordinal",
        "sequence_index",
        "heading_path",
        "title",
        "lifecycle",
        "content_hash",
        "summary",
        "metadata",
        "content_preview",
        "created_at",
        "updated_at",
    }),
    "document_layout_edges": frozenset({
        "id",
        "document_id",
        "source_id",
        "target_id",
        "type",
        "data",
        "created_at",
    }),
    "derived_vector_terms": frozenset({
        "adapter",
        "term",
        "source_id",
        "term_weight",
    }),
    "session_records": frozenset({
        "id",
        "schema_version",
        "started_at",
        "ended_at",
        "mode",
        "scope",
        "topic",
        "status",
        "summary",
        "categories",
        "files_changed",
        "docs_changed",
        "commands_run",
        "tests_run",
        "commits",
        "packets",
        "decisions",
        "followups",
        "links",
        "data",
        "created_at",
        "updated_at",
    }),
    "session_edges": frozenset({
        "id",
        "session_id",
        "type",
        "target",
        "target_type",
        "data",
        "created_at",
    }),
    "retrieval_records": frozenset({
        "record_key",
        "record_type",
        "source_id",
        "title",
        "path",
        "heading_path",
        "lifecycle",
        "content_hash",
        "updated_at",
        "search_text",
        "indexed_at",
    }),
    "retrieval_terms": frozenset({
        "term",
        "record_key",
        "term_count",
        "term_weight",
    }),
    "retrieval_term_stats": frozenset({
        "term",
        "document_frequency",
        "idf",
        "updated_at",
    }),
}

V2_SEMANTIC_CHUNKS_COLUMNS = TABLE_COLUMNS["semantic_chunks"] - frozenset({"metadata"})
VERSION_COLUMN_OVERRIDES = {
    2: {"semantic_chunks": V2_SEMANTIC_CHUNKS_COLUMNS},
    3: {"semantic_chunks": V2_SEMANTIC_CHUNKS_COLUMNS},
}


class SchemaMigrationError(RuntimeError):
    """Raised when the database cannot be safely migrated."""


@dataclass(frozen=True)
class SchemaInspection:
    state: str
    user_version: int
    metadata_version: int | None
    target_version: int
    tables: tuple[str, ...] = ()
    schema_version_rows: tuple[int, ...] = ()
    effective_version: int = 0
    missing_tables: tuple[str, ...] = ()
    missing_columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    issues: tuple[str, ...] = ()

    @property
    def can_migrate(self) -> bool:
        return self.state in {"missing", "old_compatible", "legacy_adoptable", "current"}

    @property
    def has_queryable_current_shape(self) -> bool:
        return self.state in {"current", "legacy_adoptable"}

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaState": self.state,
            "userVersion": self.user_version,
            "metadataVersion": self.metadata_version,
            "targetVersion": self.target_version,
            "effectiveVersion": self.effective_version,
            "schemaVersionRows": list(self.schema_version_rows),
            "missingTables": list(self.missing_tables),
            "missingColumns": {
                key: list(value)
                for key, value in sorted(self.missing_columns.items())
            },
            "issues": list(self.issues),
        }


def _now_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'view')
        """
    ).fetchall()
    names = {str(row[0]) for row in rows}
    return {
        name
        for name in names
        if not name.startswith(SQLITE_INTERNAL_PREFIXES)
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _metadata_version(conn: sqlite3.Connection, tables: set[str]) -> tuple[int | None, list[str]]:
    if "metadata" not in tables:
        return None, []
    columns = _columns(conn, "metadata")
    if not {"key", "value"} <= columns:
        return None, ["metadata table is missing key/value columns"]
    row = conn.execute(
        "SELECT value FROM metadata WHERE key = ?",
        ("schema_version",),
    ).fetchone()
    if row is None:
        return None, []
    raw_value = str(row[0])
    try:
        return int(raw_value), []
    except ValueError:
        return None, [f"metadata schema_version is not an integer: {raw_value}"]


def _schema_version_rows(conn: sqlite3.Connection, tables: set[str]) -> tuple[int, ...]:
    if "schema_version" not in tables:
        return ()
    columns = _columns(conn, "schema_version")
    if "version" not in columns:
        return ()
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    versions: list[int] = []
    for row in rows:
        try:
            versions.append(int(row[0]))
        except (TypeError, ValueError):
            continue
    return tuple(versions)


def _missing_columns(
    conn: sqlite3.Connection,
    tables: set[str],
    expected_tables: set[str],
    version: int,
) -> dict[str, tuple[str, ...]]:
    missing: dict[str, tuple[str, ...]] = {}
    overrides = VERSION_COLUMN_OVERRIDES.get(version, {})
    for table in sorted(expected_tables & tables):
        expected = overrides.get(table, TABLE_COLUMNS.get(table, frozenset()))
        if not expected:
            continue
        missing_for_table = tuple(sorted(expected - _columns(conn, table)))
        if missing_for_table:
            missing[table] = missing_for_table
    return missing


def _effective_version(
    user_version: int,
    metadata_version: int | None,
    schema_rows: tuple[int, ...],
) -> int:
    if user_version:
        return user_version
    if metadata_version:
        return metadata_version
    if schema_rows:
        return max(schema_rows)
    return 0


def inspect_schema(conn: sqlite3.Connection) -> SchemaInspection:
    """Inspect schema state without creating or altering database objects."""

    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    tables = _user_tables(conn)
    metadata_version, metadata_issues = _metadata_version(conn, tables)
    schema_rows = _schema_version_rows(conn, tables)
    effective = _effective_version(user_version, metadata_version, schema_rows)
    issues: list[str] = list(metadata_issues)

    max_schema_row = max(schema_rows) if schema_rows else 0
    future_values = [
        value
        for value in (user_version, metadata_version or 0, max_schema_row)
        if value > TARGET_SCHEMA_VERSION
    ]
    if future_values:
        issues.append(
            "future schema marker detected: "
            + ", ".join(str(value) for value in sorted(set(future_values)))
        )
        return SchemaInspection(
            state="future",
            user_version=user_version,
            metadata_version=metadata_version,
            target_version=TARGET_SCHEMA_VERSION,
            tables=tuple(sorted(tables)),
            schema_version_rows=schema_rows,
            effective_version=effective,
            issues=tuple(issues),
        )

    current_missing_tables = tuple(sorted(CURRENT_TABLES - tables))
    current_missing_columns = _missing_columns(conn, tables, set(CURRENT_TABLES), TARGET_SCHEMA_VERSION)
    has_current_shape = not current_missing_tables and not current_missing_columns
    has_current_markers = (
        user_version == TARGET_SCHEMA_VERSION
        and metadata_version == TARGET_SCHEMA_VERSION
        and TARGET_SCHEMA_VERSION in schema_rows
    )
    if has_current_shape and has_current_markers:
        return SchemaInspection(
            state="current",
            user_version=user_version,
            metadata_version=metadata_version,
            target_version=TARGET_SCHEMA_VERSION,
            tables=tuple(sorted(tables)),
            schema_version_rows=schema_rows,
            effective_version=TARGET_SCHEMA_VERSION,
        )
    if has_current_shape:
        marker_issues = []
        if user_version != TARGET_SCHEMA_VERSION:
            marker_issues.append(f"user_version is {user_version}, expected {TARGET_SCHEMA_VERSION}")
        if metadata_version != TARGET_SCHEMA_VERSION:
            marker_issues.append(
                f"metadata schema_version is {metadata_version if metadata_version is not None else 'missing'}, "
                f"expected {TARGET_SCHEMA_VERSION}"
            )
        if TARGET_SCHEMA_VERSION not in schema_rows:
            marker_issues.append(f"schema_version table does not include {TARGET_SCHEMA_VERSION}")
        return SchemaInspection(
            state="legacy_adoptable",
            user_version=user_version,
            metadata_version=metadata_version,
            target_version=TARGET_SCHEMA_VERSION,
            tables=tuple(sorted(tables)),
            schema_version_rows=schema_rows,
            effective_version=effective,
            issues=tuple(issues + marker_issues),
        )

    quasi_empty_tables = tables <= {"metadata", "schema_version"}
    if not tables or (quasi_empty_tables and not metadata_issues):
        return SchemaInspection(
            state="missing",
            user_version=user_version,
            metadata_version=metadata_version,
            target_version=TARGET_SCHEMA_VERSION,
            tables=tuple(sorted(tables)),
            schema_version_rows=schema_rows,
            effective_version=effective,
            missing_tables=current_missing_tables,
            missing_columns=current_missing_columns,
            issues=tuple(issues),
        )

    if effective in VERSION_TABLES and 0 < effective < TARGET_SCHEMA_VERSION:
        expected_tables = set(VERSION_TABLES[effective])
        stage_missing_tables = tuple(sorted(expected_tables - tables))
        stage_missing_columns = _missing_columns(conn, tables, expected_tables, effective)
        if not stage_missing_tables and not stage_missing_columns:
            return SchemaInspection(
                state="old_compatible",
                user_version=user_version,
                metadata_version=metadata_version,
                target_version=TARGET_SCHEMA_VERSION,
                tables=tuple(sorted(tables)),
                schema_version_rows=schema_rows,
                effective_version=effective,
                missing_tables=current_missing_tables,
                missing_columns=current_missing_columns,
            )
        issues.append(
            f"schema marker {effective} is missing required version {effective} objects"
        )
        return SchemaInspection(
            state="partial",
            user_version=user_version,
            metadata_version=metadata_version,
            target_version=TARGET_SCHEMA_VERSION,
            tables=tuple(sorted(tables)),
            schema_version_rows=schema_rows,
            effective_version=effective,
            missing_tables=stage_missing_tables,
            missing_columns=stage_missing_columns,
            issues=tuple(issues),
        )

    if effective == TARGET_SCHEMA_VERSION:
        issues.append("schema marker is current but required current objects are missing")
    elif effective == 0:
        issues.append("database has memory tables but no recognized schema marker")
    else:
        issues.append(f"unsupported schema marker: {effective}")
    return SchemaInspection(
        state="partial",
        user_version=user_version,
        metadata_version=metadata_version,
        target_version=TARGET_SCHEMA_VERSION,
        tables=tuple(sorted(tables)),
        schema_version_rows=schema_rows,
        effective_version=effective,
        missing_tables=current_missing_tables,
        missing_columns=current_missing_columns,
        issues=tuple(issues),
    )


V1_CORE_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  project_short TEXT NOT NULL,
  plane TEXT NOT NULL,
  path TEXT,
  lifecycle TEXT NOT NULL,
  content_hash TEXT,
  source_refs TEXT NOT NULL DEFAULT '[]',
  provenance TEXT NOT NULL DEFAULT '{}',
  body TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_path ON entities(path);
CREATE INDEX IF NOT EXISTS idx_entities_lifecycle ON entities(lifecycle);

CREATE TABLE IF NOT EXISTS edges (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  type TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(source_id, target_id, type)
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  command TEXT NOT NULL,
  target_entity_ids TEXT NOT NULL,
  affected_paths TEXT NOT NULL,
  before_state TEXT NOT NULL,
  after_state TEXT NOT NULL,
  reason TEXT NOT NULL,
  provenance TEXT NOT NULL,
  review_required INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  ref TEXT NOT NULL,
  title TEXT,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS views (
  path TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  command TEXT NOT NULL,
  source_counts TEXT NOT NULL,
  stale_warning TEXT NOT NULL,
  content_hash TEXT
);
"""

V2_SEMANTIC_DDL = """
CREATE TABLE IF NOT EXISTS semantic_chunks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  sequence_index INTEGER NOT NULL DEFAULT 1,
  parent_chunk_id TEXT,
  previous_chunk_id TEXT,
  next_chunk_id TEXT,
  lifecycle TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  summary TEXT NOT NULL,
  keywords TEXT NOT NULL DEFAULT '[]',
  content_preview TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(document_id, ordinal, sequence_index)
);

CREATE INDEX IF NOT EXISTS idx_semantic_chunks_document ON semantic_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_semantic_chunks_source_path ON semantic_chunks(source_path);
CREATE INDEX IF NOT EXISTS idx_semantic_chunks_hash ON semantic_chunks(content_hash);

CREATE TABLE IF NOT EXISTS correlation_suggestions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  type TEXT NOT NULL,
  confidence TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'suggested',
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source_id, target_id, type, confidence)
);

CREATE INDEX IF NOT EXISTS idx_correlation_suggestions_source ON correlation_suggestions(source_id);
CREATE INDEX IF NOT EXISTS idx_correlation_suggestions_target ON correlation_suggestions(target_id);
CREATE INDEX IF NOT EXISTS idx_correlation_suggestions_status ON correlation_suggestions(status);
"""

V3_VECTOR_DDL = """
CREATE TABLE IF NOT EXISTS derived_vector_index (
  id TEXT PRIMARY KEY,
  adapter TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  terms TEXT NOT NULL DEFAULT '{}',
  term_count INTEGER NOT NULL DEFAULT 0,
  norm REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(adapter, source_id)
);

CREATE INDEX IF NOT EXISTS idx_derived_vector_index_adapter ON derived_vector_index(adapter);
CREATE INDEX IF NOT EXISTS idx_derived_vector_index_source_path ON derived_vector_index(source_path);
"""

V5_CURRENT_DDL = """
CREATE TABLE IF NOT EXISTS document_layout_nodes (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  node_type TEXT NOT NULL,
  parent_id TEXT NOT NULL DEFAULT '',
  ordinal INTEGER NOT NULL,
  sequence_index INTEGER NOT NULL DEFAULT 0,
  heading_path TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  lifecycle TEXT NOT NULL,
  content_hash TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}',
  content_preview TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(document_id, node_type, ordinal, sequence_index, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_document_layout_nodes_document ON document_layout_nodes(document_id);
CREATE INDEX IF NOT EXISTS idx_document_layout_nodes_source_path ON document_layout_nodes(source_path);
CREATE INDEX IF NOT EXISTS idx_document_layout_nodes_type ON document_layout_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_document_layout_nodes_parent ON document_layout_nodes(parent_id);

CREATE TABLE IF NOT EXISTS document_layout_edges (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  type TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(source_id, target_id, type)
);

CREATE INDEX IF NOT EXISTS idx_document_layout_edges_document ON document_layout_edges(document_id);
CREATE INDEX IF NOT EXISTS idx_document_layout_edges_source ON document_layout_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_document_layout_edges_target ON document_layout_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_document_layout_edges_type ON document_layout_edges(type);

CREATE TABLE IF NOT EXISTS derived_vector_terms (
  adapter TEXT NOT NULL,
  term TEXT NOT NULL,
  source_id TEXT NOT NULL,
  term_weight REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(adapter, term, source_id)
);

CREATE INDEX IF NOT EXISTS idx_derived_vector_terms_source
  ON derived_vector_terms(adapter, source_id);

CREATE TABLE IF NOT EXISTS session_records (
  id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL DEFAULT 1,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL,
  scope TEXT NOT NULL,
  topic TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  categories TEXT NOT NULL DEFAULT '[]',
  files_changed TEXT NOT NULL DEFAULT '[]',
  docs_changed TEXT NOT NULL DEFAULT '[]',
  commands_run TEXT NOT NULL DEFAULT '[]',
  tests_run TEXT NOT NULL DEFAULT '[]',
  commits TEXT NOT NULL DEFAULT '[]',
  packets TEXT NOT NULL DEFAULT '[]',
  decisions TEXT NOT NULL DEFAULT '[]',
  followups TEXT NOT NULL DEFAULT '[]',
  links TEXT NOT NULL DEFAULT '[]',
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_records_status ON session_records(status);
CREATE INDEX IF NOT EXISTS idx_session_records_topic ON session_records(topic);
CREATE INDEX IF NOT EXISTS idx_session_records_updated_at ON session_records(updated_at);

CREATE TABLE IF NOT EXISTS session_edges (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,
  target TEXT NOT NULL,
  target_type TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(session_id, type, target)
);

CREATE INDEX IF NOT EXISTS idx_session_edges_session ON session_edges(session_id);
CREATE INDEX IF NOT EXISTS idx_session_edges_type ON session_edges(type);

CREATE TABLE IF NOT EXISTS retrieval_records (
  record_key TEXT PRIMARY KEY,
  record_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  heading_path TEXT NOT NULL DEFAULT '',
  lifecycle TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  search_text TEXT NOT NULL DEFAULT '',
  indexed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_records_type ON retrieval_records(record_type);
CREATE INDEX IF NOT EXISTS idx_retrieval_records_path ON retrieval_records(path);
CREATE INDEX IF NOT EXISTS idx_retrieval_records_title ON retrieval_records(title);

CREATE TABLE IF NOT EXISTS retrieval_terms (
  term TEXT NOT NULL,
  record_key TEXT NOT NULL,
  term_count INTEGER NOT NULL,
  term_weight REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(term, record_key)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_terms_record ON retrieval_terms(record_key);

CREATE TABLE IF NOT EXISTS retrieval_term_stats (
  term TEXT PRIMARY KEY,
  document_frequency INTEGER NOT NULL,
  idf REAL NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def _mark_version(conn: sqlite3.Connection, version: int, now: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (version, now),
    )
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _execute_static_ddl(conn: sqlite3.Connection, script: str) -> None:
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        (key, value),
    )


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _apply_v1(conn: sqlite3.Connection, now: str) -> None:
    _execute_static_ddl(conn, V1_CORE_DDL)
    _mark_version(conn, 1, now)


def _apply_v2(conn: sqlite3.Connection, now: str) -> None:
    _execute_static_ddl(conn, V2_SEMANTIC_DDL)
    _mark_version(conn, 2, now)


def _apply_v3(conn: sqlite3.Connection, now: str) -> None:
    _execute_static_ddl(conn, V3_VECTOR_DDL)
    _mark_version(conn, 3, now)


def _apply_v4(conn: sqlite3.Connection, now: str) -> None:
    _add_column_if_missing(
        conn,
        "semantic_chunks",
        "metadata",
        "metadata TEXT NOT NULL DEFAULT '{}'",
    )
    _mark_version(conn, 4, now)


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("DROP TABLE IF EXISTS temp.cc_fts_probe")
        conn.execute("CREATE VIRTUAL TABLE temp.cc_fts_probe USING fts5(content)")
        conn.execute("DROP TABLE temp.cc_fts_probe")
    except sqlite3.Error:
        try:
            conn.execute("DROP TABLE IF EXISTS temp.cc_fts_probe")
        except sqlite3.Error:
            pass
        return False
    return True


def ensure_retrieval_fts(conn: sqlite3.Connection) -> bool:
    if not _fts5_available(conn):
        return False
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(
          record_key UNINDEXED,
          title,
          path,
          heading_path,
          search_text,
          tokenize='unicode61'
        )
        """
    )
    return True


def _apply_v5(conn: sqlite3.Connection, now: str) -> None:
    _execute_static_ddl(conn, V5_CURRENT_DDL)
    ensure_retrieval_fts(conn)
    _mark_version(conn, TARGET_SCHEMA_VERSION, now)


MIGRATIONS = {
    1: _apply_v1,
    2: _apply_v2,
    3: _apply_v3,
    4: _apply_v4,
    5: _apply_v5,
}


def _finalize_metadata(conn: sqlite3.Connection, now: str) -> None:
    _set_metadata(conn, "schema_version", str(TARGET_SCHEMA_VERSION))
    conn.execute(
        "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
        ("created_at", now),
    )
    _set_metadata(conn, "controlcoding_version", CONTROLCODING_VERSION)
    _mark_version(conn, TARGET_SCHEMA_VERSION, now)


def _raise_unsafe(inspection: SchemaInspection) -> None:
    details = "; ".join(inspection.issues) if inspection.issues else inspection.state
    raise SchemaMigrationError(
        f"Cannot migrate memory schema: state={inspection.state}; {details}"
    )


def migrate_schema(conn: sqlite3.Connection) -> SchemaInspection:
    """Apply migrations without committing the caller-owned transaction."""

    inspection = inspect_schema(conn)
    if inspection.state == "current":
        return inspection
    if not inspection.can_migrate:
        _raise_unsafe(inspection)
    if not conn.in_transaction:
        raise SchemaMigrationError("Schema migration requires a caller-owned transaction")
    now = _now_iso()
    if inspection.state == "legacy_adoptable":
        _finalize_metadata(conn, now)
    else:
        start_version = 0 if inspection.state == "missing" else inspection.effective_version
        for version in range(start_version + 1, TARGET_SCHEMA_VERSION + 1):
            MIGRATIONS[version](conn, now)
        _finalize_metadata(conn, now)
    migrated = inspect_schema(conn)
    if migrated.state != "current":
        raise SchemaMigrationError(
            f"Schema migration did not reach current state: {migrated.to_payload()}"
        )
    return migrated


__all__ = [
    "SchemaInspection",
    "SchemaMigrationError",
    "ensure_retrieval_fts",
    "inspect_schema",
    "migrate_schema",
]
