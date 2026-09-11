"""Schema constants for the ControlCoding Project Memory Engine."""

from __future__ import annotations

CONTROL_DIRNAME = ".controlcoding"
MEMORY_DIRNAME = "memory"
LOGS_DIRNAME = "logs"
VIEWS_DIRNAME = "views"
DB_FILENAME = "memory.db"
MANIFEST_FILENAME = "module_manifest.json"
SCHEMA_VERSION = 5
CONTROLCODING_VERSION = "v3.0.2"
SESSION_RECORD_SCHEMA_VERSION = 1

DOCUMENT_LAYOUT_NODE_TYPES = {
    "annotation",
    "caption",
    "chapter",
    "document",
    "figure",
    "footnote",
    "image",
    "margin_note",
    "page",
    "paragraph",
    "schema",
    "schema_field",
    "section",
    "subsection",
    "subtitle",
    "table",
    "table_cell",
    "title",
}

DOCUMENT_LAYOUT_EDGE_TYPES = {
    "caption_for",
    "contains",
    "footnote_for",
    "next_layout",
    "previous_layout",
    "represented_by_chunk",
    "schema_field_of",
    "has_layout_node",
    "in_page",
    "table_cell_of",
    "visual_adjacent",
    "visual_above",
    "visual_below",
    "visual_left_of",
    "visual_right_of",
    "visual_overlaps",
}

VALID_INSTALL_MODES = {"full", "document-only"}
VALID_MEMORY_PROFILES = {"project", "work"}
WORK_MEMORY_PROFILE_DIRS = [
    "docs/inbox",
    "docs/sources",
    "docs/extracts",
    "docs/research",
    "docs/ideas",
    "docs/decisions",
    "docs/workflows",
    "docs/outputs",
    "docs/archive",
]
INSTALL_MODE_STORAGE = {
    "full": "full_controlcoding",
    "document-only": "document_memory_only",
}

VALID_LIFECYCLES = {
    "captured",
    "draft",
    "triaged",
    "active",
    "implemented",
    "verified",
    "legacy",
    "superseded",
    "archived",
    "stale",
    "conflicting",
    "rejected",
    "merged",
    "needs_review",
}

VALID_SESSION_MODES = {
    "continue_previous_work",
    "new_work",
    "handoff",
    "maintenance",
    "review",
}

VALID_SESSION_STATUSES = {
    "active",
    "completed",
    "needs_followup",
    "blocked",
    "superseded",
    "archived",
}

SESSION_EDGE_TYPES = {
    "belongs_to_category",
    "changes_doc",
    "changes_file",
    "continues_session",
    "left_followup",
    "produced_commit",
    "produced_packet",
    "references_decision",
    "references_session",
    "uses_command",
    "uses_dev_graphrag",
    "uses_rag_o",
    "uses_work_graphrag",
    "verified_by",
}

EVENT_TYPES = {
    "create",
    "edit",
    "move",
    "rename",
    "archive",
    "supersede",
    "delete",
    "link",
    "unlink",
    "review",
    "import",
    "classify",
    "promote",
    "reject",
    "merge",
    "split",
    "mark_conflicting",
    "mark_legacy",
    "mark_stale",
    "resolve_conflict",
    "resolve_stale",
    "record_decision",
    "record_consult",
    "record_agent_run",
    "rebuild_index",
    "generate_view",
    "sync_report",
    "scan",
    "chunk",
    "correlate",
}

REQUIRED_LOGS = [
    "event_log.jsonl",
    "decision_log.jsonl",
    "consult_log.jsonl",
    "agent_run_log.jsonl",
]

REQUIRED_VIEWS = [
    "STATUS.md",
    "ROADMAP.md",
    "HANDOFF.md",
    "WORK_CONTEXT.md",
    "WORK_HANDOFF.md",
    "SOURCE_LEDGER.md",
    "OPEN_QUESTIONS.md",
    "ACTIVE_DECISIONS.md",
    "IMPACT_MAP.md",
    "LIFECYCLE_INDEX.md",
    "LEGACY_INDEX.md",
    "STALE_INDEX.md",
    "CONSULT_LEDGER.md",
    "AGENT_RUN_LEDGER.md",
    "IDEA_INBOX.md",
    "GRAPH_INDEX.md",
    "VECTOR_INDEX.md",
]

VALID_DOCUMENT_TYPES = {
    "application_memory_component",
    "benchmark",
    "decision",
    "design",
    "general_document",
    "handoff",
    "host_context_projection",
    "plan",
    "research",
    "status",
    "test_evidence",
}

VALID_DOCUMENT_FACETS = {
    "active",
    "archived",
    "conflicting",
    "external_source",
    "generated_view",
    "legacy",
    "needs_review",
    "stale",
    "superseded",
}

CORRELATION_CONFIDENCE_LEVELS = {
    "explicit_link",
    "exact_id_or_path_match",
    "strong_title_or_heading_match",
    "weak_lexical_similarity",
    "human_confirmed_relation",
}

TEXT_EXTENSIONS = {
    ".adoc",
    ".cfg",
    ".cjs",
    ".css",
    ".csv",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".rst",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_DIRS = {
    ".controlcoding",
    ".git",
    ".hg",
    ".svn",
    ".claude",
    ".vscode",
    ".idea",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

HOST_CONTEXT_FILES = {
    ".clinerules",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTROLCODING.md",
    "GEMINI.md",
}

ENTITY_TYPE_CODES = {
    "agent_run": "AGENTRUN",
    "application_memory_component": "MEMCOMP",
    "benchmark": "BENCH",
    "chunk_node": "CHUNK",
    "consult": "CONSULT",
    "decision": "DECISION",
    "design": "DESIGN",
    "doc_node": "DOC",
    "file_node": "FILE",
    "handoff": "HANDOFF",
    "idea": "IDEA",
    "note": "NOTE",
    "plan": "PLAN",
    "research_note": "RESEARCH",
    "session": "SESSION",
    "test_node": "TEST",
    "work_item": "TASK",
}
