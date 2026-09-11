# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Stefano Tonello.

"""Optional ControlWork feature commands.

These helpers stay stdlib-only and operate only inside the selected project root.
"""

from __future__ import annotations

import json
import os
import re
import hashlib
import html
import csv
import io
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from .extractors import docx_text_extract, pdf_text_extract, xlsx_text_extract
from .work_review_queue import ReviewQueueError, mutate_file_index, mutate_scan_record, refresh_scan_index, run_scan_review

AREAS = ["inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy", "views"]
CAPTURE_AREAS = [area for area in AREAS if area != "views"]
MEMORY_ROOT = Path(".controlwork") / "memory"
CATEGORY_PATH = Path(".controlwork") / "categories.json"
CHECKPOINT_ROOT = Path(".controlwork") / "checkpoints"
CHECKPOINT_STATE_PATH = CHECKPOINT_ROOT / "auto-state.json"
PROPOSAL_ROOT = Path(".controlwork") / "proposals"
CONTEXT_PACKET_ROOT = Path(".controlwork") / "context-packets"
SESSION_ROOT = Path(".controlwork") / "sessions"
INGESTION_ROOT = Path(".controlwork") / "ingestion"
EXTRACTS_ROOT = Path(".controlwork") / "extracts"
FILE_INDEX_PATH = INGESTION_ROOT / "file-index.json"
SCAN_ANALYSIS_PATH = INGESTION_ROOT / "scan-analysis.json"
SOURCE_LEDGER_PATH = Path(".controlwork") / "source-ledger.json"
OCR_ADAPTER_CONFIG_PATH = Path(".controlwork") / "ocr-adapters.json"
GRAPH_SUGGESTIONS_PATH = Path(".controlwork") / "graph-suggestions.json"
WIKI_ROOT = Path("wiki")
PROJECT_UNDERSTANDING_PATH = INGESTION_ROOT / "project-understanding.json"
SCAN_SCHEMA_VERSION = "controlwork-file-index/v1"
SCAN_ANALYSIS_SCHEMA_VERSION = "controlwork-scan-analysis/v1"
PROJECT_UNDERSTANDING_SCHEMA_VERSION = "controlwork-project-understanding/v1"
SOURCE_LEDGER_SCHEMA_VERSION = "controlwork-source-ledger/v1"
OCR_SIDECAR_SCHEMA_VERSION = "controlwork-ocr-sidecar/v1"
OCR_ADAPTER_INTERFACE_VERSION = "controlwork-ocr-adapter/v1"
SCAN_MAX_TEXT_HASH_BYTES = 1024 * 1024
SCAN_TEXT_SUFFIXES = {
    "",
    ".adoc",
    ".cfg",
    ".css",
    ".env",
    ".ini",
    ".js",
    ".jsx",
    ".log",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
}
SCAN_MARKDOWN_SUFFIXES = {".md", ".mdown", ".markdown", ".rst"}
SCAN_JSON_SUFFIXES = {".json"}
SCAN_YAML_SUFFIXES = {".yaml", ".yml"}
SCAN_CSV_SUFFIXES = {".csv", ".tsv"}
SCAN_HTML_SUFFIXES = {".html", ".htm", ".xml"}
SCAN_PDF_SUFFIXES = {".pdf"}
SCAN_IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
SCAN_OFFICE_SUFFIXES = {".doc", ".docx", ".odf", ".odp", ".ods", ".odt", ".ppt", ".pptx", ".xls", ".xlsx"}
SCAN_URL_METADATA_SUFFIXES = (".url.json", ".link.json")
SCAN_TEXT_KINDS = {"markdown", "text", "json", "yaml", "csv", "html", "url_metadata"}
OCR_SOURCE_KINDS = {"pdf", "image", "office"}
SCAN_REVIEW_STATUSES = [
    "unreviewed",
    "reviewed",
    "ready_to_promote",
    "ignored",
    "needs_human",
    "needs_import",
    "imported",
    "promoted",
    "duplicate",
    "conflict",
    "superseded_candidate",
]
SCAN_REVIEW_ACTIONABLE_STATUSES = {"new", "changed", "missing", "unsupported"}
SCAN_PROMOTABLE_REVIEW_STATUSES = {"reviewed", "needs_import", "ready_to_promote"}
SCAN_BATCH_REVIEW_STATUSES = set(SCAN_REVIEW_STATUSES) - {"imported", "promoted"}
SCAN_REVIEW_RESOLVED_STATUSES = {"reviewed", "ignored", "imported", "promoted"}
SCAN_PROMOTION_BLOCKING_REVIEW_FLAGS = {
    "duplicate",
    "conflict",
    "missing_promoted_source",
    "text_conflict_markers",
    "version_candidate",
    "version_ambiguous",
}
SCAN_REVIEW_FILTERS = [
    "pending",
    "all",
    "new",
    "changed",
    "missing",
    "unsupported",
    "unreviewed",
    "needs-human",
    "needs-import",
    "ready-to-promote",
    "duplicate",
    "conflict",
    "versions",
    "sensitive",
]
SCAN_SENSITIVITY_LEVELS = ["unknown", "public", "internal", "confidential", "restricted"]
SCAN_PROMOTION_EXCERPT_CHARS = 5000
SCAN_IMPORT_EXTRACT_CHARS = 12000
SCAN_VERSION_HINT_WORDS = {"backup", "copy", "draft", "final", "old", "previous", "rev", "review", "updated", "version"}
SCAN_IGNORED_DIRS = {
    ".cache",
    ".controlcoding",
    ".controlwork",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".obsidian",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
    "wiki",
}
SCAN_IGNORED_FILES = {
    ".DS_Store",
    "AGENTS.md",
    "AI_CONTEXT.md",
    "CLAUDE.md",
    "GEMINI.md",
    "Thumbs.db",
    "desktop.ini",
}
GENERATED_AT_RE = re.compile(r"^generated_at: .*$", re.MULTILINE)
GENERATED_LINE_RE = re.compile(r"^Generated: .*$", re.MULTILINE)
CONTEXT_SCOPES = [
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
]
SCOPE_AREA_PRIORITY = {
    "research": ["sources", "notes", "ideas"],
    "planning": ["plans", "decisions", "ideas", "sources"],
    "analysis": ["sources", "notes", "decisions", "plans"],
    "writing": ["outputs", "notes", "sources", "decisions"],
    "ux": ["ideas", "plans", "decisions", "outputs", "sources"],
    "ui": ["ideas", "plans", "decisions", "outputs", "sources"],
    "frontend": ["plans", "decisions", "notes", "sources"],
    "backend": ["plans", "decisions", "notes", "sources"],
    "architecture": ["decisions", "plans", "sources", "notes"],
    "implementation": ["plans", "decisions", "notes", "sources"],
    "bugfix": ["decisions", "notes", "sources", "plans"],
    "refactor": ["decisions", "plans", "notes", "legacy"],
    "review": ["decisions", "plans", "sources", "outputs", "legacy"],
    "handoff": ["decisions", "plans", "sources", "outputs", "notes"],
    "general": ["decisions", "plans", "sources", "notes", "ideas", "outputs"],
}
DEFAULT_CATEGORIES = [
    ("inbox", "Inbox", "inbox", "Unclassified captured material."),
    ("sources", "Sources", "sources", "Source summaries and references."),
    ("notes", "Notes", "notes", "Clean observations and reusable context."),
    ("ideas", "Ideas", "ideas", "Options, concepts, and directions."),
    ("decisions", "Decisions", "decisions", "Accepted decisions and rationale."),
    ("plans", "Plans", "plans", "Work plans, workflows, and briefs."),
    ("outputs", "Outputs", "outputs", "Deliverables and final artifacts."),
    ("legacy", "Legacy", "legacy", "Superseded or archived material."),
]
PROJECT_BASE_DOCUMENT_FILENAME = "PROJECT.md"
PROJECT_BASE_DOCUMENT_CANDIDATES = [
    PROJECT_BASE_DOCUMENT_FILENAME,
    "README.md",
    "Project.md",
    "project.md",
    "docs/PROJECT.md",
    "docs/project.md",
]
PROJECT_UNDERSTANDING_KIND_LABELS = {
    "study_learning": "Study And Learning Project",
    "software_development": "Software Development Project",
    "research_corpus": "Research Corpus",
    "wiki_knowledge_base": "Wiki Or Knowledge Base",
    "archive_organization": "Archive Organization",
    "data_analysis": "Data Or Analysis Project",
    "writing_design": "Writing Or Design Project",
    "mixed_project": "Mixed Project",
}
CHECKPOINT_REASONS = [
    "manual",
    "auto",
    "milestone",
    "handoff",
    "context_pack",
    "context_compaction",
    "session_end",
]
ALWAYS_CHECKPOINT_REASONS = {"handoff", "context_compaction", "session_end"}
GRAPH_CONTRACT_VERSION = "memory-graph-contract/v1"
PORTABLE_GRAPH_NODE_TYPES = [
    "project_context",
    "session",
    "source",
    "note",
    "idea",
    "decision",
    "plan",
    "output",
    "legacy",
    "chunk",
]
PORTABLE_GRAPH_EDGE_TYPES = [
    "contains",
    "references",
    "mentions",
    "related_topic",
    "supersedes",
    "superseded_by",
]
PORTABLE_CONFIDENCE_LEVELS = [
    "explicit_link",
    "strong_topic_overlap",
    "weak_topic_overlap",
    "human_reviewed",
]
SESSION_MODES = [
    "continue_previous_work",
    "new_work",
    "planning",
    "research",
    "review",
    "implementation",
    "handoff",
    "maintenance",
]
SESSION_STATUSES = ["active", "completed", "needs_followup", "blocked", "superseded", "archived"]
SESSION_EDGE_TYPES = [
    "belongs_to_category",
    "references_entry",
    "changes_memory",
    "left_followup",
    "produced_packet",
    "references_decision",
    "uses_work_graphrag",
]



def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return result or "untitled"


def rel(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return path.as_posix()


def is_inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_output_path(project: Path, output: Path) -> Path:
    """Resolve a user-supplied output path and require it to stay in project."""
    root = project.resolve()
    target = output.expanduser()
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    if not is_inside(root, target):
        raise ValueError("output path must stay inside the selected project root")
    return target


def resolve_input_path(project: Path, source: Path) -> Path:
    """Resolve a project-owned input path and require it to stay in project."""
    root = project.resolve()
    target = source.expanduser()
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    if not is_inside(root, target):
        raise ValueError("input path must stay inside the selected project root")
    return target


CONTROLWORK_READABLE_FILES = {
    Path("CONTROLWORK.md"),
    Path("PROJECT.md"),
}
CONTROLWORK_READABLE_ROOTS = (
    MEMORY_ROOT,
    CHECKPOINT_ROOT,
    PROPOSAL_ROOT,
    CONTEXT_PACKET_ROOT,
    SESSION_ROOT,
    EXTRACTS_ROOT,
    WIKI_ROOT,
)


def is_controlwork_readable_path(project: Path, target: Path) -> bool:
    """Return whether a read-only MCP path is ControlWork memory/wiki material."""
    root = project.resolve()
    resolved = target.resolve()
    if not is_inside(root, resolved):
        return False
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return False
    if relative in CONTROLWORK_READABLE_FILES:
        return True
    for allowed_root in CONTROLWORK_READABLE_ROOTS:
        base = (root / allowed_root).resolve()
        if resolved == base or is_inside(base, resolved):
            return True
    return False


def controlwork_read_entry_payload(project: Path, entry_path: str) -> dict:
    """Read one governed ControlWork memory/wiki file for MCP callers."""
    root = project.resolve()
    raw_path = str(entry_path or "").strip()
    target_path = Path(raw_path).expanduser()
    target = target_path.resolve() if target_path.is_absolute() else (root / target_path).resolve()
    if (
        not raw_path
        or not is_controlwork_readable_path(root, target)
        or not target.exists()
        or not target.is_file()
    ):
        return {"ok": False, "error": "entry not found or outside ControlWork memory/wiki roots"}
    return {"ok": True, "path": rel(root, target), "text": target.read_text(encoding="utf-8")}


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def mutate_current_scan_record(project: Path, target_path: str, *, expected_content_hash: str, operation: str, mutate):
    return mutate_scan_record(
        file_index_path(project), target_path,
        expected_content_hash=expected_content_hash, operation=operation,
        schema_version=SCAN_SCHEMA_VERSION, record_locator=scan_record_by_path,
        mutate=mutate,
    )


def _default_registry() -> dict:
    now = utc_iso()
    return {
        "schemaVersion": 1,
        "categories": [
            {
                "slug": item[0],
                "name": item[1],
                "area": item[2],
                "description": item[3],
                "status": "approved",
                "builtin": True,
                "createdAt": now,
                "approvedAt": now,
            }
            for item in DEFAULT_CATEGORIES
        ],
    }


def ensure_feature_layout(project: Path) -> list[str]:
    written: list[str] = []
    for folder in (CHECKPOINT_ROOT, PROPOSAL_ROOT, CONTEXT_PACKET_ROOT, SESSION_ROOT, INGESTION_ROOT, EXTRACTS_ROOT):
        target = project / folder
        target.mkdir(parents=True, exist_ok=True)
        keep = target / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
            written.append(rel(project, keep))
    categories = project / CATEGORY_PATH
    if not categories.exists():
        write_json(categories, _default_registry())
        written.append(rel(project, categories))
    else:
        ensure_category_registry(project)
    return written


def ensure_category_registry(project: Path) -> dict:
    path = project / CATEGORY_PATH
    registry = read_json(path)
    if not registry.get("categories"):
        registry = _default_registry()
        write_json(path, registry)
        return registry
    by_slug = {str(item.get("slug", "")): item for item in registry.get("categories", [])}
    changed = False
    now = utc_iso()
    for default_slug, name, area, description in DEFAULT_CATEGORIES:
        if default_slug not in by_slug:
            registry.setdefault("categories", []).append({
                "slug": default_slug,
                "name": name,
                "area": area,
                "description": description,
                "status": "approved",
                "builtin": True,
                "createdAt": now,
                "approvedAt": now,
            })
            changed = True
    if changed:
        write_json(path, registry)
    return registry


def read_category_registry(project: Path) -> dict:
    registry = read_json(project / CATEGORY_PATH)
    if registry.get("categories"):
        return registry
    return _default_registry()


def validate_category(project: Path, category: str) -> str:
    if not category:
        return ""
    target = slug(category)
    for item in ensure_category_registry(project).get("categories", []):
        if item.get("slug") == target:
            if item.get("status") != "approved":
                raise ValueError(f"category is not approved: {category}")
            return target
    raise ValueError(f"unknown category: {category}; propose or add it first")


def feature_status_payload(project: Path) -> dict:
    """Expose category facts through the shared read-only uncertainty adapter."""
    from .freshness_projection import category_feature_status_payload
    return category_feature_status_payload(project / CATEGORY_PATH, project / CONTEXT_PACKET_ROOT, project / WIKI_ROOT, CAPTURE_AREAS)


def metadata_value(lines: list[str], key: str) -> str:
    pattern = re.compile(rf"^- \*\*{re.escape(key)}\*\*: ?(.*)$", re.IGNORECASE)
    for line in lines:
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def entry_from_path(project: Path, path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = path.stem
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    body = ""
    if "## Body" in lines:
        body = "\n".join(lines[lines.index("## Body") + 1:]).strip()
    return {
        "title": title,
        "area": metadata_value(lines, "Area") or path.parent.name,
        "lifecycle": metadata_value(lines, "Lifecycle") or "captured",
        "source": metadata_value(lines, "Source"),
        "category": metadata_value(lines, "Category"),
        "captured": metadata_value(lines, "Captured"),
        "body": body,
        "path": rel(project, path),
        "stem": path.stem,
    }


def iter_entries(project: Path, area: str = "") -> list[dict]:
    entries: list[dict] = []
    areas = [area] if area else CAPTURE_AREAS
    for current_area in areas:
        area_path = project / MEMORY_ROOT / current_area
        if not area_path.exists():
            continue
        for path in sorted(area_path.glob("*.md")):
            if path.name != ".gitkeep":
                entries.append(entry_from_path(project, path))
    return entries


def markdown_entry_list(entries: list[dict], empty: str = "No entries.") -> list[str]:
    if not entries:
        return [f"- {empty}"]
    lines = []
    for entry in entries:
        detail = f"{entry['path']} - {entry.get('lifecycle', '')}"
        if entry.get("category"):
            detail += f" - category: {entry['category']}"
        lines.append(f"- [{entry['title']}]({entry['path']}) - {detail}")
    return lines


def view_header(title: str) -> list[str]:
    return [f"# {title}", "", f"Generated: {utc_iso()}", ""]


def build_views(project: Path, read_only: bool = False) -> dict[str, str]:
    registry = read_category_registry(project) if read_only else ensure_category_registry(project)
    if not read_only:
        ensure_feature_layout(project)
    entries = iter_entries(project)
    views: dict[str, str] = {}
    lines = view_header("ControlWork Memory Index")
    for area in AREAS:
        lines.extend([f"## {area}", ""])
        lines.extend(markdown_entry_list([entry for entry in entries if entry["area"] == area]))
        lines.append("")
    views["index.md"] = "\n".join(lines)
    active_decisions = [entry for entry in entries if entry["area"] == "decisions" and entry["lifecycle"] == "active"]
    lines = view_header("Active Decisions") + markdown_entry_list(active_decisions, "No active decisions.")
    views["active-decisions.md"] = "\n".join(lines)
    open_questions = [entry for entry in entries if entry["lifecycle"] == "needs_review" or "?" in (entry.get("title", "") + entry.get("body", ""))]
    lines = view_header("Open Questions") + markdown_entry_list(open_questions, "No open questions detected.")
    views["open-questions.md"] = "\n".join(lines)
    source_entries = [entry for entry in entries if entry["area"] == "sources" or entry.get("source")]
    lines = view_header("Source Ledger")
    if not source_entries:
        lines.append("- No source references captured.")
    else:
        for entry in source_entries:
            lines.append(f"- **{entry['title']}** - {entry.get('source') or entry['path']} - `{entry['path']}`")
    views["source-ledger.md"] = "\n".join(lines)
    plans = [entry for entry in entries if entry["area"] == "plans" and entry["lifecycle"] in {"active", "captured"}]
    lines = view_header("Handoff Packet")
    for title, items, empty in (
        ("Active Decisions", active_decisions, "No active decisions."),
        ("Plans", plans, "No plans captured."),
        ("Open Questions", open_questions, "No open questions detected."),
        ("Sources", source_entries, "No source references captured."),
    ):
        lines.extend([f"## {title}", ""])
        lines.extend(markdown_entry_list(items, empty))
        lines.append("")
    views["handoff-packet.md"] = "\n".join(lines)
    lines = view_header("Category Registry")
    for item in sorted(registry.get("categories", []), key=lambda row: str(row.get("slug", ""))):
        lines.append(f"- **{item.get('name', item.get('slug', ''))}** (`{item.get('slug', '')}`) - {item.get('status', '')} - area: {item.get('area', '')}")
    views["category-registry.md"] = "\n".join(lines)
    return views


def write_views(project: Path) -> list[str]:
    views_dir = project / MEMORY_ROOT / "views"
    views_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, content in build_views(project).items():
        path = views_dir / filename
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        written.append(rel(project, path))
    return written


def cmd_category_list(args) -> int:
    project = args.project_root.resolve()
    ensure_feature_layout(project)
    categories = ensure_category_registry(project).get("categories", [])
    if args.status:
        categories = [item for item in categories if item.get("status") == args.status]
    print(json.dumps({"ok": True, "categories": categories}, indent=2))
    return 0


def upsert_category(project: Path, name: str, area: str, description: str, status: str) -> dict:
    ensure_feature_layout(project)
    if area not in CAPTURE_AREAS:
        raise ValueError(f"unknown default area: {area}")
    target = slug(name)
    registry = ensure_category_registry(project)
    now = utc_iso()
    for item in registry.get("categories", []):
        if item.get("slug") == target:
            if item.get("builtin") and status != "approved":
                raise ValueError("builtin categories cannot be proposed")
            item.update({"name": name, "area": area, "description": description, "status": status, "updatedAt": now})
            if status == "approved":
                item["approvedAt"] = now
            write_json(project / CATEGORY_PATH, registry)
            return item
    item = {"slug": target, "name": name, "area": area, "description": description, "status": status, "builtin": False, "createdAt": now}
    if status == "approved":
        item["approvedAt"] = now
    registry.setdefault("categories", []).append(item)
    write_json(project / CATEGORY_PATH, registry)
    return item


def cmd_category_propose(args) -> int:
    try:
        item = upsert_category(args.project_root.resolve(), args.name, args.area, args.description, "proposed")
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"ok": True, "category": item}, indent=2))
    return 0

def cmd_category_add(args) -> int:
    try:
        item = upsert_category(args.project_root.resolve(), args.name, args.area, args.description, "approved")
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"ok": True, "category": item}, indent=2))
    return 0


def cmd_category_approve(args) -> int:
    project = args.project_root.resolve()
    target = slug(args.category)
    registry = ensure_category_registry(project)
    for item in registry.get("categories", []):
        if item.get("slug") == target:
            item["status"] = "approved"
            item["approvedAt"] = utc_iso()
            write_json(project / CATEGORY_PATH, registry)
            print(json.dumps({"ok": True, "category": item}, indent=2))
            return 0
    print(json.dumps({"ok": False, "error": f"unknown category: {args.category}"}, indent=2))
    return 1


def cmd_views_generate(args) -> int:
    print(json.dumps({"ok": True, "written": write_views(args.project_root.resolve())}, indent=2))
    return 0


def checkpoint_fingerprint(project: Path) -> str:
    """Fingerprint durable work state, excluding generated checkpoint/view files."""
    digest = hashlib.sha256()
    candidates: list[Path] = []
    for path in (project / "CONTROLWORK.md", project / CATEGORY_PATH):
        if path.exists() and path.is_file():
            candidates.append(path)
    for area in CAPTURE_AREAS:
        root = project / MEMORY_ROOT / area
        if root.exists():
            candidates.extend(path for path in root.rglob("*.md") if path.is_file())
    for path in sorted(candidates, key=lambda item: rel(project, item)):
        digest.update(rel(project, path).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def checkpoint_due(project: Path, reason: str = "auto") -> dict:
    fingerprint = checkpoint_fingerprint(project)
    state = read_json(project / CHECKPOINT_STATE_PATH)
    latest = latest_checkpoint(project)
    forced = reason in ALWAYS_CHECKPOINT_REASONS
    changed = state.get("fingerprint") != fingerprint
    due = forced or changed or latest is None
    reasons = []
    if forced:
        reasons.append(reason)
    if latest is None:
        reasons.append("no_previous_checkpoint")
    if changed:
        reasons.append("durable_memory_changed")
    return {
        "due": due,
        "reason": reason,
        "reasons": reasons,
        "fingerprint": fingerprint,
        "previousFingerprint": state.get("fingerprint", ""),
        "latestCheckpoint": rel(project, latest) if latest else "",
    }


def checkpoint_content(project: Path, title: str, reason: str = "manual", note: str = "") -> str:
    entries = iter_entries(project)
    active_decisions = [entry for entry in entries if entry["area"] == "decisions" and entry["lifecycle"] == "active"]
    needs_review = [entry for entry in entries if entry["lifecycle"] == "needs_review"]
    legacy = [entry for entry in entries if entry["lifecycle"] in {"legacy", "superseded"}]
    source_entries = [entry for entry in entries if entry["area"] == "sources" or entry.get("source")]
    lines = [
        f"# {title}",
        "",
        f"- **Generated**: {utc_iso()}",
        f"- **Reason**: {reason}",
        f"- **Entries**: {len(entries)}",
        f"- **Active Decisions**: {len(active_decisions)}",
        f"- **Needs Review**: {len(needs_review)}",
        f"- **Legacy Or Superseded**: {len(legacy)}",
        f"- **Source References**: {len(source_entries)}",
        "",
    ]
    if note.strip():
        lines.extend(["## Checkpoint Note", "", note.strip(), ""])
    for section, items, empty in (
        ("Added Or Changed", entries[-20:], "No memory entries captured."),
        ("Active Decisions", active_decisions, "No active decisions."),
        ("Needs Review", needs_review, "No entries need review."),
        ("Legacy Or Superseded", legacy, "No legacy entries."),
        ("Sources", source_entries, "No sources captured."),
    ):
        lines.extend([f"## {section}", ""])
        lines.extend(markdown_entry_list(items, empty))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def create_checkpoint(
    project: Path,
    title: str = "",
    reason: str = "manual",
    note: str = "",
    auto: bool = False,
) -> dict:
    ensure_feature_layout(project)
    reason = reason if reason in CHECKPOINT_REASONS else "manual"
    due = checkpoint_due(project, reason=reason)
    if auto and not due["due"]:
        return {
            "ok": True,
            "created": False,
            "checkpoint": due["latestCheckpoint"],
            "reason": reason,
            "due": due,
            "written": [],
        }
    written = write_views(project)
    title = title or f"ControlWork Checkpoint {utc_stamp()}"
    path = new_checkpoint_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(checkpoint_content(project, title, reason=reason, note=note), encoding="utf-8")
    written.append(rel(project, path))
    state = {
        "schemaVersion": 1,
        "fingerprint": checkpoint_fingerprint(project),
        "checkpoint": rel(project, path),
        "reason": reason,
        "updatedAt": utc_iso(),
    }
    write_json(project / CHECKPOINT_STATE_PATH, state)
    written.append(rel(project, project / CHECKPOINT_STATE_PATH))
    return {
        "ok": True,
        "created": True,
        "checkpoint": rel(project, path),
        "reason": reason,
        "due": due,
        "written": written,
    }


def cmd_checkpoint(args) -> int:
    project = args.project_root.resolve()
    reason = getattr(args, "reason", "manual")
    if getattr(args, "check", False):
        print(json.dumps({"ok": True, **checkpoint_due(project, reason=reason)}, indent=2))
        return 0
    title = args.title or f"ControlWork Checkpoint {utc_stamp()}"
    payload = create_checkpoint(
        project,
        title=title,
        reason=reason,
        note=getattr(args, "note", ""),
        auto=bool(getattr(args, "auto", False)),
    )
    print(json.dumps(payload, indent=2))
    return 0


def cmd_handoff(args) -> int:
    project = args.project_root.resolve()
    output = None
    if args.output:
        try:
            output = resolve_output_path(project, args.output)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
    checkpoint = create_checkpoint(
        project,
        title=f"Handoff checkpoint {utc_stamp()}",
        reason="handoff",
        note="Automatic checkpoint created before generating a handoff packet.",
    )
    written = list(checkpoint.get("written", []))
    written.extend(write_views(project))
    handoff = project / MEMORY_ROOT / "views" / "handoff-packet.md"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(handoff.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(rel(project, output))
    print(json.dumps({"ok": True, "handoff": rel(project, handoff), "checkpoint": checkpoint.get("checkpoint", ""), "written": written}, indent=2))
    return 0


def tokens(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", value) if len(token) >= 2]


def lifecycle_weight(lifecycle: str) -> int:
    return {
        "active": 12,
        "captured": 6,
        "needs_review": 2,
        "superseded": -4,
        "legacy": -6,
    }.get(lifecycle, 0)


def score_entry_for_context(entry: dict, scope: str, topic: str) -> tuple[int, list[str]]:
    scope = scope if scope in CONTEXT_SCOPES else "general"
    priority = SCOPE_AREA_PRIORITY.get(scope, SCOPE_AREA_PRIORITY["general"])
    terms = tokens(topic) or tokens(scope)
    haystack = "\n".join([
        entry.get("title", ""),
        entry.get("body", ""),
        entry.get("source", ""),
        entry.get("category", ""),
        entry.get("area", ""),
    ]).lower()
    score = lifecycle_weight(entry.get("lifecycle", ""))
    reasons: list[str] = []
    if entry.get("area") in priority:
        area_score = max(1, len(priority) - priority.index(entry["area"]))
        score += area_score
        reasons.append(f"scope area: {entry['area']}")
    for term in terms:
        if term in haystack:
            score += 8
            if len(reasons) < 6:
                reasons.append(f"topic match: {term}")
    if not terms and entry.get("lifecycle") in {"active", "captured"}:
        score += 2
    return score, reasons


def select_context_entries(
    project: Path,
    scope: str,
    topic: str,
    limit: int,
    include_legacy: bool = False,
) -> dict[str, list[dict]]:
    entries = iter_entries(project)
    scored = []
    for entry in entries:
        score, reasons = score_entry_for_context(entry, scope, topic)
        if score > 0 or not topic:
            item = dict(entry)
            item["_contextScore"] = score
            item["_contextReasons"] = reasons
            scored.append(item)
    scored.sort(key=lambda item: (-int(item.get("_contextScore", 0)), str(item.get("area", "")), str(item.get("captured", "")), str(item.get("title", ""))))
    current = [
        item for item in scored
        if item.get("lifecycle") in {"active", "captured"}
    ][:limit]
    needs_review = [
        item for item in scored
        if item.get("lifecycle") == "needs_review"
    ][: max(3, limit // 2)]
    legacy = [
        item for item in scored
        if item.get("lifecycle") in {"legacy", "superseded"}
    ][: max(3, limit // 2)]
    if not include_legacy:
        legacy = legacy[:3]
    return {"current": current, "needsReview": needs_review, "legacy": legacy}


def text_excerpt(path: Path, max_chars: int = 2800) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[Excerpt truncated]"


def latest_checkpoint(project: Path) -> Path | None:
    """Return the latest checkpoint only from a stable regular-file inventory."""
    from .freshness_projection import latest_stable_markdown_file

    return latest_stable_markdown_file(project / CHECKPOINT_ROOT, "project_checkpoint")


def new_checkpoint_path(project: Path) -> Path:
    root = project / CHECKPOINT_ROOT
    stamp = utc_stamp()
    path = root / f"{stamp}-checkpoint.md"
    index = 2
    while path.exists():
        path = root / f"{stamp}-repeat{index}-checkpoint.md"
        index += 1
    return path


def format_context_entry(entry: dict) -> list[str]:
    line = f"- **{entry.get('title', entry.get('stem', 'Untitled'))}** - `{entry.get('path', '')}`"
    details = []
    for key in ("area", "lifecycle", "category", "source"):
        if entry.get(key):
            details.append(f"{key}: {entry[key]}")
    if entry.get("_contextScore") is not None:
        details.append(f"score: {entry['_contextScore']}")
    lines = [line]
    if details:
        lines.append(f"  - {'; '.join(details)}")
    reasons = entry.get("_contextReasons") or []
    if reasons:
        lines.append(f"  - reasons: {'; '.join(str(reason) for reason in reasons[:4])}")
    body = str(entry.get("body", "")).strip().replace("\n", " ")
    if body:
        lines.append(f"  - summary: {body[:420]}")
    return lines


def build_context_pack(
    project: Path,
    scope: str = "general",
    topic: str = "",
    limit: int = 10,
    include_legacy: bool = False,
    refresh_views: bool = True,
) -> str:
    if refresh_views:
        ensure_feature_layout(project)
        write_views(project)
    scope = scope if scope in CONTEXT_SCOPES else "general"
    topic = topic.strip()
    limit = max(1, min(int(limit or 10), 30))
    selected = select_context_entries(project, scope, topic, limit, include_legacy=include_legacy)
    context_excerpt = text_excerpt(project / "CONTROLWORK.md")
    handoff_path = project / MEMORY_ROOT / "views" / "handoff-packet.md"
    checkpoint_path = latest_checkpoint(project)
    title_topic = topic or scope
    lines = [
        f"# ControlWork Context Packet: {title_topic}",
        "",
        f"- **Generated**: {utc_iso()}",
        f"- **Scope**: {scope}",
        f"- **Topic**: {topic or '(none)'}",
        f"- **Selection Rule**: active and captured material is current; needs_review is uncertainty; legacy and superseded material is warning-only.",
        "",
        "## AI Use Contract",
        "",
        "- Use this packet as the working context for the current chat or task.",
        "- Prefer active decisions, active plans, and cited sources over older notes.",
        "- Do not treat legacy or superseded entries as current truth.",
        "- If the packet is missing obvious context, ask for a narrower topic or run another context-pack.",
        "",
        "## Canonical Project Context Excerpt",
        "",
        context_excerpt or "No CONTROLWORK.md found.",
        "",
        "## Current Context To Use",
        "",
    ]
    if selected["current"]:
        for entry in selected["current"]:
            lines.extend(format_context_entry(entry))
    else:
        lines.append("- No active or captured context matched this scope/topic.")
    lines.extend(["", "## Needs Review Or Open Questions", ""])
    if selected["needsReview"]:
        for entry in selected["needsReview"]:
            lines.extend(format_context_entry(entry))
    else:
        lines.append("- No needs_review context matched this scope/topic.")
    lines.extend(["", "## Legacy Or Superseded Warning", ""])
    if selected["legacy"]:
        for entry in selected["legacy"]:
            lines.extend(format_context_entry(entry))
    else:
        lines.append("- No related legacy or superseded entries detected.")
    lines.extend(["", "## Latest Handoff Excerpt", "", text_excerpt(handoff_path, max_chars=2200) or "No handoff packet generated yet."])
    lines.extend(["", "## Latest Checkpoint Excerpt", ""])
    if checkpoint_path:
        lines.append(text_excerpt(checkpoint_path, max_chars=2200))
    else:
        lines.append("No checkpoint generated yet.")
    lines.extend([
        "",
        "## Suggested Chat Opening",
        "",
        f"Use this context packet for scope `{scope}` and topic `{topic or title_topic}`. Keep current and legacy material separate, and ask before using stale or uncertain information as active truth.",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def write_context_pack(project: Path, content: str, scope: str, topic: str, output: Path | None = None) -> Path:
    if output:
        target = resolve_output_path(project, output)
    else:
        target = project / CONTEXT_PACKET_ROOT / f"{utc_stamp()}-{slug(scope)}-{slug(topic or 'context')}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def cmd_context_pack(args) -> int:
    project = args.project_root.resolve()
    output = None
    if args.output:
        try:
            output = resolve_output_path(project, args.output)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
    checkpoint = create_checkpoint(project, title=f"Context packet checkpoint {utc_stamp()}", reason="context_pack", note="Automatic checkpoint created before generating a scoped context packet.", auto=True)
    content = build_context_pack(project, scope=args.scope, topic=args.topic, limit=args.limit, include_legacy=args.include_legacy)
    if args.stdout:
        print(json.dumps({"ok": True, "scope": args.scope, "topic": args.topic, "checkpoint": checkpoint.get("checkpoint", ""), "checkpointCreated": bool(checkpoint.get("created")), "packetMarkdown": content}, indent=2) if getattr(args, "json_output", False) else content, end="" if not getattr(args, "json_output", False) else "\n")
        return 0
    try:
        target = write_context_pack(project, content, args.scope, args.topic, output=output)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "ok": True,
        "path": rel(project, target),
        "scope": args.scope,
        "topic": args.topic,
        "checkpoint": checkpoint.get("checkpoint", ""),
        "checkpointCreated": bool(checkpoint.get("created")),
    }, indent=2))
    return 0


def frontmatter(title: str, kind: str, sources: list[str]) -> str:
    refs = "\n".join([f"  - {item}" for item in sources]) or "  - CONTROLWORK.md"
    return "\n".join([
        "---",
        "controlwork_projection: true",
        f"title: {json.dumps(title)}",
        f"kind: {kind}",
        f"generated_at: {utc_iso()}",
        "generated_from:",
        refs,
        "---",
        "",
    ])


def normalize_generated_timestamps(content: str) -> str:
    content = GENERATED_AT_RE.sub("generated_at: <normalized>", content)
    return GENERATED_LINE_RE.sub("Generated: <normalized>", content)


def obsidian_pages(project: Path, read_only: bool = False) -> dict[str, str]:
    views = build_views(project, read_only=read_only)
    entries = iter_entries(project)
    pages: dict[str, str] = {}
    home = frontmatter("Home", "home", ["CONTROLWORK.md"]) + "# Home\n\n## Core\n\n"
    for page in ("Project Status", "Active Decisions", "Open Questions", "Source Ledger", "Handoff Packet", "Category Registry"):
        home += f"- [[{page}]]\n"
    home += "\n## Areas\n\n"
    for area in CAPTURE_AREAS:
        home += f"- [[Areas/{area}|{area}]]\n"
    pages["Home.md"] = home
    status = frontmatter("Project Status", "status", ["CONTROLWORK.md"]) + "# Project Status\n\n## Memory Counts\n\n"
    for area in AREAS:
        count = len([entry for entry in entries if entry["area"] == area])
        status += f"- **{area}**: {count}\n"
    pages["Project Status.md"] = status
    mapping = {
        "Active Decisions.md": ("Active Decisions", "active-decisions.md"),
        "Open Questions.md": ("Open Questions", "open-questions.md"),
        "Source Ledger.md": ("Source Ledger", "source-ledger.md"),
        "Handoff Packet.md": ("Handoff Packet", "handoff-packet.md"),
        "Category Registry.md": ("Category Registry", "category-registry.md"),
    }
    for wiki_name, (title, view_name) in mapping.items():
        pages[wiki_name] = frontmatter(title, "view", [f".controlwork/memory/views/{view_name}"]) + views[view_name]
    for area in CAPTURE_AREAS:
        area_entries = [entry for entry in entries if entry["area"] == area]
        content = frontmatter(area, "area", [f".controlwork/memory/{area}/"]) + f"# {area}\n\n"
        content += "\n".join(markdown_entry_list(area_entries, "No entries.")) + "\n"
        pages[f"Areas/{area}.md"] = content
    return pages


def write_obsidian_settings(project: Path) -> str:
    settings = project / ".obsidian" / "app.json"
    payload = {"userIgnoreFilters": [".git/", ".controlcoding/", "__pycache__/", "*.pyc"]}
    write_json(settings, payload)
    return rel(project, settings)


def write_obsidian_wiki(project: Path) -> list[str]:
    ensure_feature_layout(project)
    write_views(project)
    written = []
    for rel_path, content in obsidian_pages(project).items():
        path = project / WIKI_ROOT / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        written.append(rel(project, path))
    return written


def obsidian_drift(project: Path, read_only: bool = False) -> list[dict]:
    drift = []
    for rel_path, content in obsidian_pages(project, read_only=read_only).items():
        path = project / WIKI_ROOT / rel_path
        expected = content.rstrip() + "\n"
        if not path.exists():
            drift.append({"path": rel(project, path), "state": "missing"})
        else:
            actual = normalize_generated_timestamps(path.read_text(encoding="utf-8"))
            normalized_expected = normalize_generated_timestamps(expected)
            if actual != normalized_expected:
                drift.append({"path": rel(project, path), "state": "drifted"})
    return drift


def cmd_obsidian_init(args) -> int:
    project = args.project_root.resolve()
    settings = write_obsidian_settings(project)
    written = write_obsidian_wiki(project)
    print(json.dumps({"ok": True, "settings": settings, "written": written}, indent=2))
    return 0


def cmd_obsidian_sync(args) -> int:
    print(json.dumps({"ok": True, "written": write_obsidian_wiki(args.project_root.resolve())}, indent=2))
    return 0


def cmd_obsidian_check(args) -> int:
    project = args.project_root.resolve()
    drift = obsidian_drift(project, read_only=True)
    print(json.dumps({"ok": not drift, "drift": drift}, indent=2))
    return 0 if not drift else 1


def cmd_wiki_build(args) -> int:
    return cmd_obsidian_sync(args)


def cmd_wiki_import_edits(args) -> int:
    project = args.project_root.resolve()
    drift = obsidian_drift(project)
    proposals = []
    if args.review:
        for item in drift:
            path = project / item["path"]
            proposal = project / PROPOSAL_ROOT / f"{utc_stamp()}-wiki-edit-{slug(item['path'])}.md"
            body = path.read_text(encoding="utf-8") if path.exists() else ""
            proposal.parent.mkdir(parents=True, exist_ok=True)
            proposal.write_text("\n".join([
                f"# Proposed Wiki Edit: {item['path']}",
                "",
                f"- **State**: {item['state']}",
                f"- **Captured**: {utc_iso()}",
                "- **Review Required**: true",
                "",
                "## Edited Content",
                "",
                body,
                "",
            ]), encoding="utf-8")
            proposals.append(rel(project, proposal))
    print(json.dumps({"ok": True, "drift": drift, "proposals": proposals}, indent=2))
    return 0


def search_memory(project: Path, query: str, area: str = "", limit: int = 20) -> list[dict]:
    q = query.lower()
    matches = []
    for entry in iter_entries(project, area=area):
        haystack = "\n".join([entry.get("title", ""), entry.get("body", ""), entry.get("source", ""), entry.get("category", "")]).lower()
        if q in haystack:
            matches.append(entry)
        if len(matches) >= limit:
            break
    return matches


# Portable Session GraphRAG subset. This is file-based and intentionally omits
# ControlCoding-only evidence such as code impact, hooks, agent runs, and
# verification receipts.
def session_id_for(topic: str) -> str:
    return f"{utc_stamp()}-{slug(topic)[:48]}"


def session_file(project: Path, session_id: str) -> Path:
    return project / SESSION_ROOT / f"{slug(session_id)}.json"


def normalize_list(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def read_session(project: Path, session_id: str) -> dict:
    path = session_file(project, session_id)
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def write_session(project: Path, payload: dict) -> Path:
    path = session_file(project, str(payload.get("id", "")))
    payload["updatedAt"] = utc_iso()
    write_json(path, payload)
    return path


def iter_sessions(project: Path) -> list[dict]:
    root = project / SESSION_ROOT
    if not root.exists():
        return []
    sessions: list[dict] = []
    for path in sorted(root.glob("*.json")):
        payload = read_json(path)
        if isinstance(payload, dict) and payload.get("id"):
            sessions.append(payload)
    sessions.sort(key=lambda item: str(item.get("updatedAt") or item.get("startedAt") or ""), reverse=True)
    return sessions


def session_lifecycle(status: str) -> str:
    return {
        "active": "active",
        "completed": "captured",
        "needs_followup": "needs_review",
        "blocked": "needs_review",
        "superseded": "superseded",
        "archived": "legacy",
    }.get(status, "captured")


def session_body(session: dict) -> str:
    parts = [
        str(session.get("topic", "")),
        str(session.get("summary", "")),
        " ".join(session.get("categories", [])),
        " ".join(session.get("memoryChanged", [])),
        " ".join(session.get("packets", [])),
        " ".join(session.get("decisions", [])),
        " ".join(session.get("followups", [])),
    ]
    for link in session.get("links", []):
        parts.append(" ".join(str(link.get(key, "")) for key in ("type", "target", "targetType")))
    return "\n".join(part for part in parts if part)


def session_to_graph_entry(session: dict) -> dict:
    session_id = str(session.get("id", ""))
    status = str(session.get("status", "captured"))
    return {
        "id": stable_graph_id("CW_SESSION", session_id),
        "type": "session",
        "title": str(session.get("topic") or session_id),
        "path": f".controlwork/sessions/{slug(session_id)}.json",
        "area": "sessions",
        "lifecycle": session_lifecycle(status),
        "source": "session_record",
        "category": ", ".join(session.get("categories", [])),
        "body": session_body(session),
        "tokens": sorted(set(graph_tokens(session_body(session)))),
        "session": session,
    }


def append_unique(items: list[str], value: str) -> list[str]:
    value = str(value or "").strip()
    if value and value not in items:
        items.append(value)
    return items


def cmd_session_start(args) -> int:
    project = args.project_root.resolve()
    ensure_feature_layout(project)
    session_id = slug(args.session_id) if args.session_id else session_id_for(args.topic)
    path = session_file(project, session_id)
    if path.exists():
        print(json.dumps({"ok": False, "error": "session already exists", "path": rel(project, path)}, indent=2))
        return 1
    now = utc_iso()
    payload = {
        "schemaVersion": 1,
        "id": session_id,
        "startedAt": now,
        "endedAt": "",
        "mode": args.mode,
        "operator": getattr(args, "operator", "manual") or "manual",
        "topic": args.topic,
        "status": "active",
        "summary": args.summary,
        "categories": normalize_list(args.category),
        "memoryChanged": [],
        "packets": [],
        "decisions": [],
        "followups": [],
        "links": [],
        "notes": [],
        "createdAt": now,
        "updatedAt": now,
    }
    write_session(project, payload)
    print(json.dumps({"ok": True, "session": payload, "path": rel(project, path)}, indent=2))
    return 0


def cmd_session_close(args) -> int:
    project = args.project_root.resolve()
    payload = read_session(project, args.session_id)
    if not payload:
        print(json.dumps({"ok": False, "error": "session not found"}, indent=2))
        return 1
    payload["status"] = args.status
    payload["summary"] = args.summary or payload.get("summary", "")
    payload["endedAt"] = utc_iso()
    for item in args.followup:
        payload["followups"] = append_unique(payload.get("followups", []), item)
    for item in args.decision:
        payload["decisions"] = append_unique(payload.get("decisions", []), item)
    path = write_session(project, payload)
    print(json.dumps({"ok": True, "session": payload, "path": rel(project, path)}, indent=2))
    return 0


def cmd_session_note(args) -> int:
    project = args.project_root.resolve()
    payload = read_session(project, args.session_id)
    if not payload:
        print(json.dumps({"ok": False, "error": "session not found"}, indent=2))
        return 1
    note = {"createdAt": utc_iso(), "kind": args.kind, "text": args.text}
    payload.setdefault("notes", []).append(note)
    if args.kind == "followup":
        payload["followups"] = append_unique(payload.get("followups", []), args.text)
    if args.kind == "decision":
        payload["decisions"] = append_unique(payload.get("decisions", []), args.text)
    path = write_session(project, payload)
    print(json.dumps({"ok": True, "note": note, "path": rel(project, path)}, indent=2))
    return 0


def cmd_session_link(args) -> int:
    project = args.project_root.resolve()
    payload = read_session(project, args.session_id)
    if not payload:
        print(json.dumps({"ok": False, "error": "session not found"}, indent=2))
        return 1
    link = {"createdAt": utc_iso(), "type": args.type, "target": args.target, "targetType": args.target_type}
    payload.setdefault("links", []).append(link)
    if args.type in {"references_entry", "changes_memory"}:
        payload["memoryChanged"] = append_unique(payload.get("memoryChanged", []), args.target)
    elif args.type == "produced_packet":
        payload["packets"] = append_unique(payload.get("packets", []), args.target)
    elif args.type == "references_decision":
        payload["decisions"] = append_unique(payload.get("decisions", []), args.target)
    elif args.type == "left_followup":
        payload["followups"] = append_unique(payload.get("followups", []), args.target)
    elif args.type == "belongs_to_category":
        payload["categories"] = append_unique(payload.get("categories", []), args.target)
    path = write_session(project, payload)
    print(json.dumps({"ok": True, "link": link, "path": rel(project, path)}, indent=2))
    return 0


def cmd_session_list(args) -> int:
    sessions = iter_sessions(args.project_root.resolve())
    if args.status != "all":
        sessions = [item for item in sessions if item.get("status") == args.status]
    if args.topic:
        q = args.topic.lower()
        sessions = [item for item in sessions if q in str(item.get("topic", "")).lower()]
    print(json.dumps({"ok": True, "sessions": sessions[: max(1, args.limit)]}, indent=2))
    return 0


def cmd_session_show(args) -> int:
    payload = read_session(args.project_root.resolve(), args.session_id)
    print(json.dumps({"ok": bool(payload), "session": payload}, indent=2))
    return 0 if payload else 1

# Portable graph, retrieval, and rag-pack subset.
def stable_graph_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("\0".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:20].upper()
    return f"{prefix}_{digest}"


def graph_tokens(value: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_/-]{2,}", value)
        if token.lower() not in {"and", "the", "for", "with", "that", "this", "from", "into", "controlwork"}
    ]


def suggestion_state_path(project: Path) -> Path:
    return project / GRAPH_SUGGESTIONS_PATH


def read_suggestion_state(project: Path) -> dict:
    payload = read_json(suggestion_state_path(project))
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("suggestions"), dict):
        return {"schemaVersion": 1, "suggestions": {}}
    return payload


def write_suggestion_state(project: Path, payload: dict) -> None:
    payload["schemaVersion"] = 1
    payload.setdefault("suggestions", {})
    write_json(suggestion_state_path(project), payload)


def _apply_suggestion_state(project: Path, suggestions: list[dict], edges: list[dict]) -> tuple[list[dict], dict[str, int]]:
    state = read_suggestion_state(project)
    reviewed = state.get("suggestions", {})
    counts = {"suggested": 0, "accepted": 0, "rejected": 0, "audit_only": 0}
    visible: list[dict] = []
    generated_ids: set[str] = set()
    for suggestion in suggestions:
        suggestion_id = str(suggestion.get("id", ""))
        generated_ids.add(suggestion_id)
        audit = reviewed.get(suggestion_id, {}) if isinstance(reviewed, dict) else {}
        status = str(audit.get("status") or "suggested")
        item = dict(suggestion)
        if audit:
            item.update({
                "status": status,
                "reviewedAt": audit.get("reviewedAt", ""),
                "reviewReason": audit.get("reason", ""),
            })
        if status == "accepted":
            counts["accepted"] += 1
            accepted_edge = {
                "id": stable_graph_id("CW_EDGE_ACCEPTED", suggestion_id),
                "sourceId": item["sourceId"],
                "targetId": item["targetId"],
                "type": item["type"],
                "confidence": "human_reviewed",
                "status": "canonical",
                "reason": "Accepted graph suggestion: " + str(audit.get("reason") or item.get("reason", "")),
                "fromSuggestionId": suggestion_id,
            }
            edges.append(accepted_edge)
            visible.append(item)
        elif status == "rejected":
            counts["rejected"] += 1
            visible.append(item)
        else:
            counts["suggested"] += 1
            visible.append(item)
    if isinstance(reviewed, dict):
        for suggestion_id, audit in reviewed.items():
            if suggestion_id in generated_ids or not isinstance(audit, dict):
                continue
            counts["audit_only"] += 1
            original = audit.get("original", {}) if isinstance(audit.get("original"), dict) else {}
            visible.append({
                **original,
                "id": suggestion_id,
                "status": audit.get("status", "reviewed"),
                "reviewedAt": audit.get("reviewedAt", ""),
                "reviewReason": audit.get("reason", ""),
                "auditOnly": True,
            })
    return visible, counts


def find_graph_suggestion(project: Path, suggestion_id: str) -> dict:
    graph = portable_graph(project)
    for suggestion in graph["suggestions"]:
        if suggestion.get("id") == suggestion_id:
            return suggestion
    return {}


def update_graph_suggestion_review(project: Path, suggestion_id: str, status: str, reason: str) -> dict:
    graph = portable_graph(project)
    suggestion = next((item for item in graph["suggestions"] if item.get("id") == suggestion_id), {})
    if not suggestion:
        state = read_suggestion_state(project)
        existing = state.get("suggestions", {}).get(suggestion_id, {})
        if not existing:
            return {"ok": False, "error": "suggestion not found"}
        suggestion = existing.get("original", {}) if isinstance(existing.get("original"), dict) else {"id": suggestion_id}
    state = read_suggestion_state(project)
    reviewed = state.setdefault("suggestions", {})
    reviewed[suggestion_id] = {
        "status": status,
        "reason": reason,
        "reviewedAt": utc_iso(),
        "original": suggestion,
    }
    write_suggestion_state(project, state)
    return {"ok": True, "suggestion": reviewed[suggestion_id], "path": rel(project, suggestion_state_path(project))}


def split_markdown_chunks(text: str, path: str, lifecycle: str) -> list[dict]:
    sections: list[dict] = []
    current_title = Path(path).stem
    current_lines: list[str] = []
    current_level = 1
    ordinal = 0
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            body = "\n".join(current_lines).strip()
            if body:
                ordinal += 1
                sections.append({
                    "id": stable_graph_id("CW_CHUNK", path, str(ordinal), current_title),
                    "type": "chunk",
                    "path": path,
                    "title": current_title,
                    "headingPath": current_title,
                    "headingLevel": current_level,
                    "lifecycle": lifecycle,
                    "body": body,
                    "tokens": sorted(set(graph_tokens(current_title + " " + body))),
                })
            current_level = len(match.group(1))
            current_title = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    body = "\n".join(current_lines).strip()
    if body:
        ordinal += 1
        sections.append({
            "id": stable_graph_id("CW_CHUNK", path, str(ordinal), current_title),
            "type": "chunk",
            "path": path,
            "title": current_title,
            "headingPath": current_title,
            "headingLevel": current_level,
            "lifecycle": lifecycle,
            "body": body,
            "tokens": sorted(set(graph_tokens(current_title + " " + body))),
        })
    return sections


def entry_node_type(entry: dict) -> str:
    area = str(entry.get("area", ""))
    if area == "sources":
        return "source"
    if area == "decisions":
        return "decision"
    if area == "plans":
        return "plan"
    if area == "outputs":
        return "output"
    if area == "ideas":
        return "idea"
    if area == "legacy":
        return "legacy"
    if area == "notes":
        return "note"
    return "project_context"


def portable_graph_entries(project: Path) -> list[dict]:
    entries = []
    context_path = project / "CONTROLWORK.md"
    if context_path.exists():
        text = context_path.read_text(encoding="utf-8", errors="replace")
        entries.append({
            "id": stable_graph_id("CW_NODE", "CONTROLWORK.md"),
            "type": "project_context",
            "title": "CONTROLWORK.md",
            "path": "CONTROLWORK.md",
            "area": "context",
            "lifecycle": "active",
            "source": "canonical_context",
            "body": text,
            "tokens": sorted(set(graph_tokens(text))),
        })
    for entry in iter_entries(project):
        body = str(entry.get("body", ""))
        title = str(entry.get("title", ""))
        path = str(entry.get("path", ""))
        entries.append({
            "id": stable_graph_id("CW_NODE", path),
            "type": entry_node_type(entry),
            "title": title,
            "path": path,
            "area": str(entry.get("area", "")),
            "lifecycle": str(entry.get("lifecycle", "captured")),
            "source": str(entry.get("source", "")),
            "category": str(entry.get("category", "")),
            "body": body,
            "tokens": sorted(set(graph_tokens(" ".join([title, body, str(entry.get("source", "")), str(entry.get("category", ""))])))),
        })
    for session in iter_sessions(project):
        entries.append(session_to_graph_entry(session))
    return entries


def safe_project_relative_path(value: str, fallback: str = PROJECT_BASE_DOCUMENT_FILENAME) -> str:
    normalized = str(value or "").replace("\\", "/").strip().strip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized or normalized == "..":
        return fallback
    if normalized.startswith(".controlwork/") or normalized == "CONTROLWORK.md":
        return fallback
    return normalized


def configured_base_document_path(project: Path, config: dict | None = None) -> str:
    raw_config = config if isinstance(config, dict) else read_json(project / ".controlwork" / "config.json")
    for key in ("baseDocument", "projectDocument", "projectBaseDocument"):
        value = raw_config.get(key) if isinstance(raw_config, dict) else ""
        if isinstance(value, str) and value.strip():
            return safe_project_relative_path(value)
    if isinstance(raw_config, dict):
        project_section = raw_config.get("project", {})
        if isinstance(project_section, dict):
            value = project_section.get("baseDocumentPath") or project_section.get("documentPath")
            if isinstance(value, str) and value.strip():
                return safe_project_relative_path(value)
    for candidate in PROJECT_BASE_DOCUMENT_CANDIDATES:
        if (project / candidate).exists():
            return safe_project_relative_path(candidate)
    return PROJECT_BASE_DOCUMENT_FILENAME


def _project_understanding_section(understanding: dict | None) -> list[str]:
    if not isinstance(understanding, dict) or not understanding:
        return []
    selected_kind = str(understanding.get("selectedKind") or "needs_confirmation")
    primary_purpose = str(understanding.get("primaryPurpose") or "needs_confirmation")
    evidence = [str(item) for item in understanding.get("evidence", []) if str(item).strip()]
    questions = [str(item) for item in understanding.get("questions", []) if str(item).strip()]
    lines = [
        "## Guided Setup",
        "",
        f"- **Detected project kind**: {selected_kind}",
        f"- **Suggested primary purpose**: {primary_purpose}",
        "- **Status**: needs human confirmation before physical organization or promotion.",
        "",
    ]
    if evidence:
        lines.extend(["### Evidence", ""])
        lines.extend(f"- {item}" for item in evidence[:8])
        lines.append("")
    if questions:
        lines.extend(["### Questions To Confirm", ""])
        lines.extend(f"- {item}" for item in questions[:8])
        lines.append("")
    return lines


def project_base_document_template(project_name: str, purpose: str, understanding: dict | None = None) -> str:
    name = str(project_name or "ControlWork Project").strip() or "ControlWork Project"
    description = str(purpose or "Project knowledge and work memory.").strip() or "Project knowledge and work memory."
    lines = [
        f"# {name}",
        "",
        "## Project Overview",
        "",
        description,
        "",
    ]
    lines.extend(_project_understanding_section(understanding))
    lines.extend([
        "## Current Working Order",
        "",
        "- Confirm the project kind and primary purpose with the user.",
        "- Keep the existing folder layout intact until physical organization is explicitly approved.",
        "- Capture and review source material.",
        "- Promote stable notes, ideas, decisions, plans, and outputs into ControlWork memory.",
        "- Keep legacy or superseded material linked for traceability.",
        "",
        "## Open Follow-Ups",
        "",
        "- Review and expand this base project document.",
        "",
    ])
    return "\n".join(lines)


def ensure_project_base_document(
    project: Path,
    project_name: str = "",
    purpose: str = "",
    understanding: dict | None = None,
) -> dict:
    config_path = project / ".controlwork" / "config.json"
    config = read_json(config_path)
    relative_path = configured_base_document_path(project, config)
    target = project / relative_path
    created = False
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        name = project_name or project.name or "ControlWork Project"
        target.write_text(project_base_document_template(name, purpose, understanding=understanding), encoding="utf-8")
        created = True
    config_changed = False
    if isinstance(config, dict):
        if config.get("baseDocument") != relative_path:
            config["baseDocument"] = relative_path
            config_changed = True
        project_section = config.get("project")
        if not isinstance(project_section, dict):
            project_section = {}
            config["project"] = project_section
            config_changed = True
        if project_section.get("baseDocumentPath") != relative_path:
            project_section["baseDocumentPath"] = relative_path
            config_changed = True
        if config_changed:
            write_json(config_path, config)
    return {
        "path": relative_path,
        "created": created,
        "exists": target.exists(),
        "updatedAt": utc_iso() if created else "",
    }


def portable_graph(project: Path) -> dict:
    entries = portable_graph_entries(project)
    nodes = []
    edges = []
    suggestions = []
    for entry in entries:
        node = {key: entry.get(key, "") for key in ("id", "type", "title", "path", "area", "lifecycle", "source", "category")}
        node["recordType"] = "session" if entry.get("type") == "session" else "entry"
        nodes.append(node)
        for chunk in split_markdown_chunks(str(entry.get("body", "")), str(entry.get("path", "")), str(entry.get("lifecycle", ""))):
            chunk_node = {
                "id": chunk["id"],
                "type": "chunk",
                "recordType": "chunk",
                "title": chunk["title"],
                "path": chunk["path"],
                "headingPath": chunk["headingPath"],
                "headingLevel": chunk["headingLevel"],
                "lifecycle": chunk["lifecycle"],
            }
            nodes.append(chunk_node)
            edges.append({
                "id": stable_graph_id("CW_EDGE", entry["id"], chunk["id"], "contains"),
                "sourceId": entry["id"],
                "targetId": chunk["id"],
                "type": "contains",
                "confidence": "human_reviewed",
                "status": "canonical",
                "reason": "Entry contains deterministic markdown chunk.",
            })
        if entry.get("type") == "session":
            for link in entry.get("session", {}).get("links", []):
                target = str(link.get("target", ""))
                normalized_target = target.replace("\\", "/")
                target_entry = next(
                    (
                        item for item in entries
                        if str(item.get("path", "")).replace("\\", "/") == normalized_target
                        or item.get("id") == target
                    ),
                    None,
                )
                if not target_entry:
                    continue
                edge_type = "mentions" if link.get("type") == "belongs_to_category" else "references"
                edges.append({
                    "id": stable_graph_id("CW_EDGE", entry["id"], str(target_entry.get("id", "")), str(link.get("type", ""))),
                    "sourceId": entry["id"],
                    "targetId": target_entry["id"],
                    "type": edge_type,
                    "confidence": "explicit_link",
                    "status": "canonical",
                    "reason": f"Session link {link.get('type', '')}.",
                })
    for left_index, left in enumerate(entries):
        for right in entries[left_index + 1:]:
            if left["id"] == right["id"]:
                continue
            overlap = sorted(set(left.get("tokens", [])) & set(right.get("tokens", [])))
            if len(overlap) < 2:
                continue
            confidence = "strong_topic_overlap" if len(overlap) >= 4 else "weak_topic_overlap"
            suggestions.append({
                "id": stable_graph_id("CW_SUG", left["id"], right["id"], confidence),
                "sourceId": left["id"],
                "targetId": right["id"],
                "type": "related_topic",
                "confidence": confidence,
                "status": "suggested",
                "reason": "Shared terms: " + ", ".join(overlap[:8]),
            })
    suggestions, suggestion_counts = _apply_suggestion_state(project, suggestions, edges)
    return {
        "contractVersion": GRAPH_CONTRACT_VERSION,
        "nodeTypes": PORTABLE_GRAPH_NODE_TYPES,
        "edgeTypes": PORTABLE_GRAPH_EDGE_TYPES,
        "confidenceLevels": PORTABLE_CONFIDENCE_LEVELS,
        "nodes": nodes,
        "edges": edges,
        "suggestions": suggestions,
        "suggestionCounts": suggestion_counts,
    }


def cmd_graph_status(args) -> int:
    graph = portable_graph(args.project_root.resolve())
    node_counts: dict[str, int] = {}
    for node in graph["nodes"]:
        node_counts[node["type"]] = node_counts.get(node["type"], 0) + 1
    payload = {
        "ok": True,
        "contractVersion": graph["contractVersion"],
        "nodeCounts": node_counts,
        "edges": len(graph["edges"]),
        "suggestions": len(graph["suggestions"]),
        "suggestionCounts": graph.get("suggestionCounts", {}),
        "portableOnly": True,
        "excludedControlCodingFeatures": ["code impact", "hook receipts", "agent runs", "verification receipts"],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_graph_suggestions(args) -> int:
    graph = portable_graph(args.project_root.resolve())
    suggestions = graph["suggestions"]
    if args.status != "all":
        suggestions = [item for item in suggestions if item.get("status") == args.status]
    if args.confidence:
        suggestions = [item for item in suggestions if item.get("confidence") == args.confidence]
    print(json.dumps({
        "ok": True,
        "suggestionCounts": graph.get("suggestionCounts", {}),
        "suggestions": suggestions[: max(1, args.limit)],
    }, indent=2))
    return 0


def cmd_graph_accept(args) -> int:
    payload = update_graph_suggestion_review(
        args.project_root.resolve(),
        args.suggestion_id,
        "accepted",
        args.reason,
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_graph_reject(args) -> int:
    payload = update_graph_suggestion_review(
        args.project_root.resolve(),
        args.suggestion_id,
        "rejected",
        args.reason,
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def _compact_graph_node(node: dict) -> dict:
    from . import work_graph_ops

    return work_graph_ops._compact_graph_node(node)

def _graph_relation_payload(relation: dict, node_by_id: dict[str, dict], kind: str) -> dict:
    from . import work_graph_ops

    return work_graph_ops._graph_relation_payload(relation, node_by_id, kind)

def _normalize_graph_selector(value: str) -> str:
    from . import work_graph_ops

    return work_graph_ops._normalize_graph_selector(value)

def _graph_node_matches_selector(node: dict, selector: str) -> bool:
    from . import work_graph_ops

    return work_graph_ops._graph_node_matches_selector(node, selector)

def _find_graph_node(graph: dict, selector: str) -> dict:
    from . import work_graph_ops

    return work_graph_ops._find_graph_node(graph, selector)

def _find_graph_relation(graph: dict, selector: str) -> tuple[str, dict]:
    from . import work_graph_ops

    return work_graph_ops._find_graph_relation(graph, selector)

def build_graph_explain_payload(project: Path, selector: str, limit: int = 10) -> dict:
    from . import work_graph_ops

    return work_graph_ops.build_graph_explain_payload(project, selector, limit=limit)

def cmd_graph_explain(args) -> int:
    from . import work_graph_ops

    return work_graph_ops.cmd_graph_explain(args)

def _graph_traversal_relations(graph: dict, include_suggestions: bool) -> list[tuple[str, dict]]:
    from . import work_graph_ops

    return work_graph_ops._graph_traversal_relations(graph, include_suggestions)

def build_graph_path_payload(
    project: Path,
    source_selector: str,
    target_selector: str,
    max_depth: int = 3,
    include_suggestions: bool = False,
) -> dict:
    from . import work_graph_ops

    return work_graph_ops.build_graph_path_payload(
        project,
        source_selector,
        target_selector,
        max_depth=max_depth,
        include_suggestions=include_suggestions,
    )

def cmd_graph_path(args) -> int:
    from . import work_graph_ops

    return work_graph_ops.cmd_graph_path(args)

def build_graph_neighbors_payload(
    project: Path,
    selector: str,
    limit: int = 20,
    include_suggestions: bool = False,
    include_chunks: bool = False,
) -> dict:
    from . import work_graph_ops

    return work_graph_ops.build_graph_neighbors_payload(
        project,
        selector,
        limit=limit,
        include_suggestions=include_suggestions,
        include_chunks=include_chunks,
    )

def cmd_graph_neighbors(args) -> int:
    from . import work_graph_ops

    return work_graph_ops.cmd_graph_neighbors(args)

def _non_current_reason(lifecycle: str) -> str:
    from . import work_graph_ops

    return work_graph_ops._non_current_reason(lifecycle)

def build_graph_stale_payload(project: Path, limit: int = 50, include_scan: bool = True) -> dict:
    from . import work_graph_ops

    return work_graph_ops.build_graph_stale_payload(project, limit=limit, include_scan=include_scan)

def cmd_graph_stale(args) -> int:
    from . import work_graph_ops

    return work_graph_ops.cmd_graph_stale(args)

def _unresolved_entry_reason(entry: dict) -> list[str]:
    from . import work_graph_ops

    return work_graph_ops._unresolved_entry_reason(entry)

def build_graph_unresolved_payload(project: Path, limit: int = 50, include_scan: bool = True) -> dict:
    from . import work_graph_ops

    return work_graph_ops.build_graph_unresolved_payload(project, limit=limit, include_scan=include_scan)

def cmd_graph_unresolved(args) -> int:
    from . import work_graph_ops

    return work_graph_ops.cmd_graph_unresolved(args)

def _graph_diff_node_map(graph: dict) -> dict[str, dict]:
    from . import work_graph_ops

    return work_graph_ops._graph_diff_node_map(graph)

def _graph_diff_relation_map(graph: dict, key_name: str) -> dict[str, dict]:
    from . import work_graph_ops

    return work_graph_ops._graph_diff_relation_map(graph, key_name)

def _changed_nodes(previous: dict[str, dict], current: dict[str, dict]) -> list[dict]:
    from . import work_graph_ops

    return work_graph_ops._changed_nodes(previous, current)

def build_graph_diff_payload(project: Path, baseline: Path, limit: int = 100) -> dict:
    from . import work_graph_ops

    return work_graph_ops.build_graph_diff_payload(project, baseline, limit=limit)

def cmd_graph_diff(args) -> int:
    from . import work_graph_ops

    return work_graph_ops.cmd_graph_diff(args)

def _filter_graph_projection(graph: dict, args) -> dict:
    node_types = set(args.type or [])
    lifecycles = set(args.lifecycle or [])
    source_filter = str(args.source or "").lower()
    kept_nodes = []
    kept_ids: set[str] = set()
    for node in graph["nodes"]:
        if node_types and node.get("type") not in node_types:
            continue
        if lifecycles and node.get("lifecycle") not in lifecycles:
            continue
        if source_filter and source_filter not in str(node.get("path", "")).lower() and source_filter not in str(node.get("source", "")).lower():
            continue
        kept_nodes.append(node)
        kept_ids.add(str(node.get("id", "")))
    confidence = str(args.confidence or "")
    edges = [
        edge for edge in graph["edges"]
        if edge.get("sourceId") in kept_ids
        and edge.get("targetId") in kept_ids
        and (not confidence or edge.get("confidence") == confidence)
    ]
    suggestions = [
        item for item in graph["suggestions"]
        if item.get("sourceId") in kept_ids
        and item.get("targetId") in kept_ids
        and (not confidence or item.get("confidence") == confidence)
    ]
    return {
        "contractVersion": graph["contractVersion"],
        "portableOnly": True,
        "filters": {
            "type": sorted(node_types),
            "lifecycle": sorted(lifecycles),
            "confidence": confidence,
            "source": source_filter,
        },
        "nodes": kept_nodes,
        "edges": edges,
        "suggestions": suggestions,
    }


def _graph_projection_html(payload: dict) -> str:
    rows = []
    for node in payload["nodes"]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(node.get('type', '')))}</td>"
            f"<td>{html.escape(str(node.get('title', '')))}</td>"
            f"<td><code>{html.escape(str(node.get('path', '')))}</code></td>"
            f"<td>{html.escape(str(node.get('lifecycle', '')))}</td>"
            "</tr>"
        )
    edge_rows = []
    for edge in payload["edges"][:300]:
        edge_rows.append(
            "<tr>"
            f"<td>{html.escape(str(edge.get('type', '')))}</td>"
            f"<td><code>{html.escape(str(edge.get('sourceId', '')))}</code></td>"
            f"<td><code>{html.escape(str(edge.get('targetId', '')))}</code></td>"
            f"<td>{html.escape(str(edge.get('confidence', '')))}</td>"
            "</tr>"
        )
    return "\n".join([
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>ControlWork Graph Projection</title>",
        "<style>body{font-family:system-ui,Segoe UI,sans-serif;margin:24px;line-height:1.4}table{border-collapse:collapse;width:100%;margin:16px 0}td,th{border:1px solid #ddd;padding:6px;text-align:left}th{background:#f5f5f5}code{font-size:12px}</style>",
        "</head><body>",
        "<h1>ControlWork Graph Projection</h1>",
        "<p>Derived projection only. CONTROLWORK.md and .controlwork/memory remain authoritative.</p>",
        f"<p>Nodes: {len(payload['nodes'])}. Edges: {len(payload['edges'])}. Suggestions: {len(payload['suggestions'])}.</p>",
        "<h2>Nodes</h2>",
        "<table><thead><tr><th>Type</th><th>Title</th><th>Path</th><th>Lifecycle</th></tr></thead><tbody>",
        *rows,
        "</tbody></table>",
        "<h2>Edges</h2>",
        "<table><thead><tr><th>Type</th><th>Source</th><th>Target</th><th>Confidence</th></tr></thead><tbody>",
        *edge_rows,
        "</tbody></table>",
        "</body></html>",
    ])


def cmd_graph_export(args) -> int:
    project = args.project_root.resolve()
    graph = portable_graph(project)
    payload = _filter_graph_projection(graph, args)
    try:
        target = resolve_output_path(project, args.output)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "html":
        target.write_text(_graph_projection_html(payload), encoding="utf-8")
    else:
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "path": rel(project, target), "nodes": len(payload["nodes"]), "edges": len(payload["edges"]), "suggestions": len(payload["suggestions"])}, indent=2))
    return 0


def recent_checkpoints(project: Path, limit: int = 5) -> list[dict]:
    from . import work_dashboard

    return work_dashboard.recent_checkpoints(project, limit=limit)


def _dashboard_entry_summary(entry: dict) -> dict:
    from . import work_dashboard

    return work_dashboard._dashboard_entry_summary(entry)


def build_dashboard_payload(project: Path, limit: int = 20) -> dict:
    from . import work_dashboard

    return work_dashboard.build_dashboard_payload(project, limit=limit)


def _dashboard_markdown_table(entries: list[dict], columns: tuple[str, ...]) -> list[str]:
    from . import work_dashboard

    return work_dashboard._dashboard_markdown_table(entries, columns)


def dashboard_markdown(payload: dict) -> str:
    from . import work_dashboard

    return work_dashboard.dashboard_markdown(payload)


def _html_rows(entries: list[dict], columns: tuple[str, ...]) -> str:
    from . import work_dashboard

    return work_dashboard._html_rows(entries, columns)


def dashboard_html(payload: dict) -> str:
    from . import work_dashboard

    return work_dashboard.dashboard_html(payload)


def cmd_dashboard(args) -> int:
    from . import work_dashboard

    return work_dashboard.cmd_dashboard(args)


def lifecycle_points(lifecycle: str) -> int:
    return {"active": 12, "captured": 8, "needs_review": -4, "superseded": -8, "legacy": -10}.get(lifecycle, 0)


def retrieve_matches(project: Path, query: str, limit: int = 10, include_legacy: bool = False) -> dict:
    graph = portable_graph(project)
    terms = set(graph_tokens(query))
    suggestion_neighbors: dict[str, list[dict]] = {}
    for relation in [*graph["suggestions"], *graph["edges"]]:
        suggestion_neighbors.setdefault(relation["sourceId"], []).append(relation)
        suggestion_neighbors.setdefault(relation["targetId"], []).append(relation)
    matches = []
    excluded = []
    for node in graph["nodes"]:
        lifecycle = str(node.get("lifecycle", ""))
        if lifecycle in {"legacy", "superseded"} and not include_legacy:
            excluded.append({"id": node["id"], "path": node.get("path", ""), "reason": f"lifecycle {lifecycle}"})
            continue
        text = " ".join(str(node.get(key, "")) for key in ("title", "path", "area", "source", "category", "headingPath")).lower()
        node_terms = set(graph_tokens(text))
        hits = sorted(terms & node_terms)
        if not hits and query.lower() not in text:
            excluded.append({"id": node["id"], "path": node.get("path", ""), "reason": "no text signal"})
            continue
        score = len(hits) * 10 + lifecycle_points(lifecycle)
        reasons = []
        if hits:
            reasons.append("matched terms: " + ", ".join(hits[:8]))
        if query.lower() in text:
            score += 12
            reasons.append("query phrase match")
        graph_edges = suggestion_neighbors.get(node["id"], [])
        if graph_edges:
            score += min(12, len(graph_edges) * 3)
            reasons.append("related topic suggestions nearby")
        demotions = []
        if lifecycle in {"needs_review", "legacy", "superseded"}:
            demotions.append(f"lifecycle {lifecycle}")
        matches.append({
            "id": node["id"],
            "type": node["type"],
            "recordType": node["recordType"],
            "title": node.get("title", ""),
            "path": node.get("path", ""),
            "headingPath": node.get("headingPath", ""),
            "lifecycle": lifecycle,
            "score": score,
            "reasons": reasons,
            "demotions": demotions,
            "edgesUsed": graph_edges[:6],
            "citation": node.get("path", "") + (f"#{node.get('headingPath')}" if node.get("headingPath") else ""),
        })
    matches.sort(key=lambda item: (-int(item["score"]), str(item["path"]), str(item["title"])))
    return {
        "ok": True,
        "query": query,
        "contractVersion": GRAPH_CONTRACT_VERSION,
        "matches": matches[: max(1, limit)],
        "exclusionReport": {"count": len(excluded), "items": excluded[:20]},
        "scanEvidenceWarnings": scan_retrieval_warnings(project, query, limit=5),
    }


def cmd_retrieve(args) -> int:
    payload = retrieve_matches(args.project_root.resolve(), args.query, limit=args.limit, include_legacy=args.include_legacy)
    print(json.dumps(payload, indent=2))
    return 0


def build_rag_pack_payload(project: Path, query: str, limit: int = 10, include_legacy: bool = False) -> dict:
    retrieval = retrieve_matches(project, query, limit=limit, include_legacy=include_legacy)
    citations = []
    for index, match in enumerate(retrieval["matches"], start=1):
        citation = dict(match)
        citation["citationId"] = f"C{index}"
        citations.append(citation)
    edges = []
    for citation in citations:
        for edge in citation.get("edgesUsed", []):
            item = dict(edge)
            item["citationId"] = citation["citationId"]
            edges.append(item)
    warnings = []
    for citation in citations:
        lifecycle = citation.get("lifecycle", "")
        if lifecycle in {"needs_review", "legacy", "superseded"}:
            warnings.append({
                "citationId": citation["citationId"],
                "message": f"{citation['title']} is {lifecycle}; verify before treating it as current.",
            })
    for warning in retrieval.get("scanEvidenceWarnings", {}).get("warnings", []):
        warnings.append({"citationId": "SCAN", "message": warning})
    lines = [
        f"# ControlWork GraphRAG Packet: {query}",
        "",
        f"- **Generated**: {utc_iso()}",
        f"- **Graph Contract**: {GRAPH_CONTRACT_VERSION}",
        "- **Boundary**: Portable Project Plane only. No ControlCoding code impact, hooks, agent runs, or verification receipts.",
        "",
        "## Citations",
        "",
    ]
    for citation in citations:
        lines.append(f"- [{citation['citationId']}] `{citation['citation']}` - {citation['title']} ({citation['lifecycle']}, score {citation['score']})")
    lines.extend(["", "## Edges Used", ""])
    if edges:
        for edge in edges[:20]:
            lines.append(f"- [{edge['citationId']}] {edge['type']} {edge.get('confidence', '')}: {edge.get('reason', '')}")
    else:
        lines.append("- No topic suggestions contributed to selected citations.")
    lines.extend(["", "## Warnings", ""])
    if warnings:
        for warning in warnings:
            lines.append(f"- [{warning['citationId']}] {warning['message']}")
    else:
        lines.append("- No lifecycle warnings in selected citations.")
    lines.extend(["", "## Excluded Records", ""])
    for item in retrieval["exclusionReport"]["items"][:10]:
        lines.append(f"- `{item.get('path', item['id'])}` - {item['reason']}")
    if not retrieval["exclusionReport"]["items"]:
        lines.append("- None.")
    lines.extend(["", "## Next Reads", ""])
    for citation in citations[:5]:
        lines.append(f"- Read `{citation['path']}` to verify [{citation['citationId']}].")
    packet = "\n".join(lines).rstrip() + "\n"
    return {
        "ok": True,
        "query": query,
        "contractVersion": GRAPH_CONTRACT_VERSION,
        "citations": citations,
        "edgesUsed": edges,
        "warnings": warnings,
        "scanEvidenceWarnings": retrieval.get("scanEvidenceWarnings", {}),
        "excludedRecords": retrieval["exclusionReport"]["items"],
        "nextReads": [item for item in lines if item.startswith("- Read `")],
        "packetMarkdown": packet,
        "retrieval": retrieval,
    }


def cmd_rag_pack(args) -> int:
    project = args.project_root.resolve()
    payload = build_rag_pack_payload(project, args.query, limit=args.limit, include_legacy=args.include_legacy)
    if args.output:
        try:
            target = resolve_output_path(project, args.output)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload["packetMarkdown"], encoding="utf-8")
        payload["outputPath"] = rel(project, target)
    if args.stdout and not getattr(args, "json_output", False):
        print(payload["packetMarkdown"], end="")
    else:
        print(json.dumps(payload, indent=2))
    return 0


def _query_command_prefix(surface: str) -> dict[str, str]:
    from . import work_query

    return work_query._query_command_prefix(surface)


def _query_workflow_commands(
    project: Path,
    query: str,
    matches: list[dict],
    surface: str,
    path_to: str = "",
    max_depth: int = 3,
    include_suggestions: bool = False,
) -> dict:
    from . import work_query

    return work_query._query_workflow_commands(
        project,
        query,
        matches,
        surface,
        path_to=path_to,
        max_depth=max_depth,
        include_suggestions=include_suggestions,
    )


def _query_top_matches(matches: list[dict]) -> list[dict]:
    from . import work_query

    return work_query._query_top_matches(matches)


def _build_query_markdown(payload: dict) -> str:
    from . import work_query

    return work_query._build_query_markdown(payload)


def build_query_payload(
    project: Path,
    query: str,
    limit: int = 10,
    include_legacy: bool = False,
    path_to: str = "",
    max_depth: int = 3,
    include_suggestions: bool = False,
    surface: str = "standalone",
) -> dict:
    from . import work_query

    return work_query.build_query_payload(
        project,
        query,
        limit=limit,
        include_legacy=include_legacy,
        path_to=path_to,
        max_depth=max_depth,
        include_suggestions=include_suggestions,
        surface=surface,
    )


def cmd_query(args) -> int:
    from . import work_query

    return work_query.cmd_query(args)


def file_index_path(project: Path) -> Path:
    return project / FILE_INDEX_PATH


def read_file_index(project: Path) -> dict:
    payload = read_json(file_index_path(project))
    if payload.get("schemaVersion") != SCAN_SCHEMA_VERSION:
        return {}
    return payload


def refresh_file_index(project: Path) -> dict:
    return refresh_scan_index(file_index_path(project), lambda current: build_file_index(project, current))


def project_understanding_path(project: Path) -> Path:
    return project / PROJECT_UNDERSTANDING_PATH


def read_project_understanding(project: Path) -> dict:
    payload = read_json(project_understanding_path(project))
    if payload.get("schemaVersion") != PROJECT_UNDERSTANDING_SCHEMA_VERSION:
        return {}
    return payload


def _understanding_source_records(payload: dict) -> list[dict]:
    records = []
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).replace("\\", "/")
        lowered = path.lower()
        if not path or lowered == "controlwork.md" or lowered == "project.md" or lowered.startswith(".controlwork/"):
            continue
        records.append(item)
    return records


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "") or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda row: (-row[1], row[0])))


def _top_level_counts(records: list[dict], limit: int = 12) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in records:
        path = str(item.get("path", "")).replace("\\", "/")
        root = path.split("/", 1)[0] if "/" in path else "project root"
        counts[root] = counts.get(root, 0) + 1
    return dict(sorted(counts.items(), key=lambda row: (-row[1], row[0]))[:limit])


def _understanding_tokens(records: list[dict]) -> set[str]:
    text = " ".join(str(item.get("path", "")) for item in records)
    return set(graph_tokens(text))


def _score_project_kinds(records: list[dict], by_kind: dict[str, int], tokens: set[str]) -> dict[str, dict]:
    paths = [str(item.get("path", "")).replace("\\", "/").lower() for item in records]
    suffixes = [Path(str(item.get("path", ""))).suffix.lower() for item in records]
    code_suffixes = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cs", ".cpp", ".c", ".h", ".php", ".rb"}
    package_files = {"pyproject.toml", "package.json", "requirements.txt", "cargo.toml", "go.mod", "pom.xml"}
    names = {Path(str(item.get("path", ""))).name.lower() for item in records}
    code_count = sum(1 for suffix in suffixes if suffix in code_suffixes or suffix == ".ipynb")
    notebook_count = sum(1 for suffix in suffixes if suffix == ".ipynb")
    course_path_count = sum(
        1
        for path in paths
        if any(token in path for token in ("course", "corso", "lesson", "lezione", "tutorial", "learning", "study", "python", "machine", "universit", "fondamenti"))
    )
    software_path_count = sum(1 for path in paths if any(token in path for token in ("/src/", "/tests/", "/test/", "frontend", "backend", "server", "api")))
    dataset_path_count = sum(1 for path in paths if any(token in path for token in ("dataset", "datasets", "data/", "/data", "analysis")))
    design_path_count = sum(1 for path in paths if any(token in path for token in ("design", "draft", "proposal", "brief", "copy", "content")))
    package_count = len(names & package_files)
    markdown_count = by_kind.get("markdown", 0)
    pdf_count = by_kind.get("pdf", 0)
    office_count = by_kind.get("office", 0)
    image_count = by_kind.get("image", 0)
    csv_count = by_kind.get("csv", 0)
    study_code_score = code_count * 2 if (pdf_count or course_path_count) else notebook_count * 2
    scores = {
        "study_learning": {
            "score": pdf_count * 5 + course_path_count * 4 + study_code_score,
            "evidence": [],
        },
        "software_development": {
            "score": code_count * 3 + package_count * 5 + software_path_count * 3,
            "evidence": [],
        },
        "research_corpus": {
            "score": pdf_count * 3 + office_count + sum(1 for token in tokens if token in {"paper", "papers", "research", "article", "source", "sources", "bibliography"}) * 3,
            "evidence": [],
        },
        "wiki_knowledge_base": {
            "score": markdown_count * 2 + sum(1 for token in tokens if token in {"wiki", "knowledge", "notes", "docs", "documentation"}) * 3,
            "evidence": [],
        },
        "data_analysis": {
            "score": csv_count * 4 + dataset_path_count * 3 + notebook_count,
            "evidence": [],
        },
        "writing_design": {
            "score": office_count * 2 + markdown_count + min(image_count, 20) + design_path_count * 3,
            "evidence": [],
        },
        "archive_organization": {
            "score": max(1, len(records) // 20) + sum(1 for token in tokens if token in {"archive", "backup", "old", "raw", "files"}) * 3,
            "evidence": [],
        },
    }
    if pdf_count:
        scores["study_learning"]["evidence"].append(f"{pdf_count} PDF/source file(s)")
        scores["research_corpus"]["evidence"].append(f"{pdf_count} PDF/source file(s)")
    if code_count:
        scores["software_development"]["evidence"].append(f"{code_count} code or notebook file(s)")
        scores["study_learning"]["evidence"].append(f"{code_count} notebook/code practice file(s)")
    if package_count:
        scores["software_development"]["evidence"].append(f"{package_count} package/config marker file(s)")
    if markdown_count:
        scores["wiki_knowledge_base"]["evidence"].append(f"{markdown_count} markdown document(s)")
    if csv_count:
        scores["data_analysis"]["evidence"].append(f"{csv_count} dataset/table file(s)")
    if image_count:
        scores["writing_design"]["evidence"].append(f"{image_count} image or asset file(s)")
    return scores


def build_project_understanding(
    project: Path,
    scan_payload: dict,
    analysis: dict | None = None,
    purpose_hint: str = "",
    distribution: str = "standalone_controlwork",
) -> dict:
    records = _understanding_source_records(scan_payload)
    by_kind = _count_by(records, "kind")
    tokens = _understanding_tokens(records)
    scores = _score_project_kinds(records, by_kind, tokens)
    ranked = sorted(scores.items(), key=lambda row: (-int(row[1].get("score", 0)), row[0]))
    positive = [(kind, data) for kind, data in ranked if int(data.get("score", 0)) > 0]
    selected_kind = positive[0][0] if positive else "archive_organization"
    if len(positive) >= 2 and int(positive[1][1].get("score", 0)) >= max(3, int(positive[0][1].get("score", 0)) - 2):
        selected_kind = "mixed_project"
    primary_by_kind = {
        "study_learning": "build_learning_path",
        "software_development": "develop_or_maintain_software",
        "research_corpus": "review_and_promote_sources",
        "wiki_knowledge_base": "create_wiki_or_knowledge_base",
        "archive_organization": "organize_archive",
        "data_analysis": "analyze_data",
        "writing_design": "develop_content_or_design",
        "mixed_project": "confirm_project_goal",
    }
    selected_score = int(scores.get(selected_kind, {}).get("score", 0))
    primary_purpose = primary_by_kind.get(selected_kind, "confirm_project_goal")
    secondary = [primary_by_kind.get(kind, kind) for kind, _data in positive[:4] if kind != selected_kind]
    evidence: list[str] = []
    for kind, data in positive[:4]:
        label = PROJECT_UNDERSTANDING_KIND_LABELS.get(kind, kind)
        score = int(data.get("score", 0))
        if score:
            evidence.append(f"{label}: score {score}")
        for item in data.get("evidence", [])[:3]:
            if item not in evidence:
                evidence.append(str(item))
    if not evidence and records:
        evidence.append(f"{len(records)} file(s) detected in the project folder")
    analysis_summary = analysis.get("summary", {}) if isinstance(analysis, dict) else {}
    questions = [
        "What is the main purpose of this project: learning path, software development, research corpus, wiki, archive organization, data analysis, writing/design, or a mix?",
        "Which document should be treated as the master project document if PROJECT.md is not the right one?",
        "Should ControlWork keep a virtual map first, or physically organize files after approval?",
        "Which source groups are authoritative enough to review and promote first?",
    ]
    if selected_kind == "mixed_project":
        questions.insert(0, "Which detected project type should drive the first Project view?")
    return {
        "schemaVersion": PROJECT_UNDERSTANDING_SCHEMA_VERSION,
        "generatedAt": utc_iso(),
        "projectRoot": str(project),
        "distribution": distribution,
        "status": "needs_human_confirmation",
        "confidence": "suggested" if selected_score else "weak_topic_overlap",
        "selectedKind": selected_kind,
        "selectedKindLabel": PROJECT_UNDERSTANDING_KIND_LABELS.get(selected_kind, "Mixed Project"),
        "primaryPurpose": primary_purpose,
        "secondaryPurposes": secondary,
        "purposeHint": str(purpose_hint or ""),
        "totalSourceFiles": len(records),
        "countsByFileKind": by_kind,
        "countsByTopLevelFolder": _top_level_counts(records),
        "detectedKinds": [
            {
                "kind": kind,
                "label": PROJECT_UNDERSTANDING_KIND_LABELS.get(kind, kind),
                "score": int(data.get("score", 0)),
                "evidence": list(data.get("evidence", []))[:6],
            }
            for kind, data in ranked
            if int(data.get("score", 0)) > 0
        ],
        "evidence": evidence[:12],
        "analysisSummary": analysis_summary,
        "questions": questions,
        "automaticSteps": [
            "scan_existing_folder_without_moving_files",
            "infer_project_kind_from_local_evidence",
            "draft_or_preserve_project_master_document",
            "present_questions_for_human_confirmation",
            "propose_virtual_project_structure",
        ],
        "blockedUntilApproval": [
            "move_copy_rename_or_delete_source_files",
            "expand_archives_for_organization",
            "treat_suggested_relations_as_truth",
            "promote_sources_into_durable_memory",
        ],
        "policy": "ControlWork setup is guided and evidence-first. It scans local files, suggests project kind and purpose, asks for confirmation, and does not reorganize or promote source files without explicit human approval.",
    }


def write_project_understanding(
    project: Path,
    scan_payload: dict,
    analysis: dict | None = None,
    purpose_hint: str = "",
    distribution: str = "standalone_controlwork",
) -> dict:
    understanding = build_project_understanding(
        project,
        scan_payload,
        analysis=analysis,
        purpose_hint=purpose_hint,
        distribution=distribution,
    )
    path = project_understanding_path(project)
    write_json(path, understanding)
    return understanding


def project_understanding_summary(understanding: dict, fallback: str = "Project knowledge and work memory.") -> str:
    if not understanding:
        return fallback
    kind = str(understanding.get("selectedKindLabel") or understanding.get("selectedKind") or "project")
    purpose = str(understanding.get("primaryPurpose") or "confirm_project_goal").replace("_", " ")
    total = int(understanding.get("totalSourceFiles") or 0)
    return f"Guided ControlWork setup detected a {kind} with {total} source file(s). Suggested purpose: {purpose}. Confirm this with the user before physical organization or source promotion."


def scan_file_kind(path: Path) -> str:
    lowered_name = path.name.lower()
    if lowered_name.endswith(SCAN_URL_METADATA_SUFFIXES):
        return "url_metadata"
    suffix = path.suffix.lower()
    if suffix in SCAN_MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in SCAN_JSON_SUFFIXES:
        return "json"
    if suffix in SCAN_YAML_SUFFIXES:
        return "yaml"
    if suffix in SCAN_CSV_SUFFIXES:
        return "csv"
    if suffix in SCAN_HTML_SUFFIXES:
        return "html"
    if suffix in SCAN_PDF_SUFFIXES:
        return "pdf"
    if suffix in SCAN_IMAGE_SUFFIXES:
        return "image"
    if suffix in SCAN_OFFICE_SUFFIXES:
        return "office"
    if suffix in SCAN_TEXT_SUFFIXES:
        return "text"
    return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_text(value: str) -> str:
    return " ".join(value.split())


def scan_modified_at(stat_result) -> str:
    return datetime.fromtimestamp(stat_result.st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def should_ignore_scan_dir(name: str, relative: str) -> bool:
    del relative
    return name in SCAN_IGNORED_DIRS


def should_ignore_scan_file(name: str, relative: str) -> bool:
    del relative
    if name in SCAN_IGNORED_FILES:
        return True
    lowered = name.lower()
    return lowered.endswith((".lock", ".pyc", ".pyo", ".tmp"))


def collect_scan_files(project: Path) -> tuple[list[Path], dict]:
    files: list[Path] = []
    counters = {"ignoredDirs": 0, "ignoredFiles": 0}
    for dirpath, dirnames, filenames in os.walk(project):
        current = Path(dirpath)
        kept_dirs = []
        for dirname in dirnames:
            child = current / dirname
            if child.is_symlink() or should_ignore_scan_dir(dirname, rel(project, child)):
                counters["ignoredDirs"] += 1
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            path = current / filename
            if path.is_symlink() or should_ignore_scan_file(filename, rel(project, path)):
                counters["ignoredFiles"] += 1
                continue
            if path.is_file():
                files.append(path)
    return sorted(files, key=lambda item: rel(project, item).lower()), counters


def text_hashes_for_file(path: Path, kind: str, size_bytes: int) -> tuple[str, str]:
    if kind not in SCAN_TEXT_KINDS or size_bytes > SCAN_MAX_TEXT_HASH_BYTES:
        return "", ""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return "", ""
    return sha256_text(text), sha256_text(normalized_text(text))


def can_reuse_file_hashes(previous_record: dict, size_bytes: int, modified_at_ns: int) -> bool:
    return (
        type(previous_record.get("sizeBytes")) is int
        and type(previous_record.get("modifiedAtNs")) is int
        and isinstance(previous_record.get("contentHash"), str)
        and isinstance(previous_record.get("textHash"), str)
        and isinstance(previous_record.get("normalizedTextHash"), str)
        and previous_record["sizeBytes"] == size_bytes
        and previous_record["modifiedAtNs"] == modified_at_ns
    )


def build_file_index(project: Path, previous: dict | None = None) -> dict:
    previous = previous or {}
    previous_by_path = {
        str(item.get("path", "")): item
        for item in previous.get("files", [])
        if isinstance(item, dict) and str(item.get("path", "")).strip()
    }
    now = utc_iso()
    scan_files, counters = collect_scan_files(project)
    current_paths: set[str] = set()
    records: list[dict] = []
    summary = {key: 0 for key in ("files", "records", "new", "unchanged", "changed", "missing", "unsupported")}
    summary.update({"ignoredDirs": counters["ignoredDirs"], "ignoredFiles": counters["ignoredFiles"]})

    for path in scan_files:
        relative = rel(project, path)
        current_paths.add(relative)
        previous_record = previous_by_path.get(relative, {})
        kind = scan_file_kind(path)
        error = ""
        try:
            stat_result = path.stat()
            size_bytes = stat_result.st_size
            modified_at = scan_modified_at(stat_result)
            modified_at_ns = stat_result.st_mtime_ns
            if can_reuse_file_hashes(previous_record, size_bytes, modified_at_ns):
                content_hash = previous_record["contentHash"]
                text_hash = previous_record["textHash"]
                normalized_hash = previous_record["normalizedTextHash"]
            else:
                content_hash = sha256_file(path)
                text_hash, normalized_hash = text_hashes_for_file(path, kind, size_bytes)
        except OSError as exc:
            size_bytes = 0
            modified_at = ""
            modified_at_ns = 0
            content_hash = ""
            text_hash = ""
            normalized_hash = ""
            error = str(exc)

        if error:
            scan_status = "unsupported"
        elif not previous_record:
            scan_status = "new"
        elif previous_record.get("contentHash") == content_hash:
            scan_status = "unchanged"
        else:
            scan_status = "changed"

        if scan_status == "unchanged":
            review_status = previous_record.get("reviewStatus", "unreviewed")
            sensitivity = previous_record.get("sensitivity", "unknown")
            source_record = previous_record.get("sourceRecord", "")
        else:
            review_status = "unreviewed"
            sensitivity = previous_record.get("sensitivity", "unknown")
            source_record = previous_record.get("sourceRecord", "")

        record = {
            "path": relative,
            "kind": kind,
            "sizeBytes": size_bytes,
            "modifiedAt": modified_at,
            "modifiedAtNs": modified_at_ns,
            "contentHash": content_hash,
            "textHash": text_hash,
            "normalizedTextHash": normalized_hash,
            "scanStatus": scan_status,
            "reviewStatus": review_status,
            "sensitivity": sensitivity,
            "lastScannedAt": now,
            "sourceRecord": source_record,
            "error": error,
        }
        if scan_status == "unchanged":
            for key in ("reviewedAt", "reviewNote", "reviewFlags", "reviewedContentHash", "ocrSidecar", "ocrGeneratedAt", "ocrImportedAt", "ocrTextHash", "importMode"):
                if key in previous_record:
                    record[key] = previous_record[key]
        records.append(record)
        summary[scan_status] += 1
        summary["files"] += 1

    for relative, previous_record in sorted(previous_by_path.items(), key=lambda item: item[0].lower()):
        if relative in current_paths:
            continue
        missing = dict(previous_record)
        missing["scanStatus"] = "missing"
        missing["lastScannedAt"] = now
        records.append(missing)
        summary["missing"] += 1

    summary["records"] = len(records)
    result = {
        "schemaVersion": SCAN_SCHEMA_VERSION,
        "generatedAt": now,
        "projectRoot": project.as_posix(),
        "summary": summary,
        "files": records,
    }
    for key, value in previous.items():
        if key not in result:
            result[key] = value
    return result


def cmd_scan(args) -> int:
    project = args.project_root.resolve()
    if not project.exists() or not project.is_dir():
        print(json.dumps({"ok": False, "error": "project root does not exist"}, indent=2))
        return 1
    ensure_feature_layout(project)
    path = file_index_path(project)
    try:
        payload = refresh_file_index(project)
    except ReviewQueueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    result = {
        "ok": True,
        "indexPath": rel(project, path),
        "summary": payload["summary"],
    }
    if getattr(args, "json_output", False):
        print(json.dumps(result, indent=2))
    else:
        summary = payload["summary"]
        print(f"Scanned {summary['files']} file(s); index written to {rel(project, path)}")
        print(
            f"new={summary['new']} changed={summary['changed']} unchanged={summary['unchanged']} "
            f"missing={summary['missing']} unsupported={summary['unsupported']}"
        )
    return 0


def scan_record_by_path(payload: dict, relative_path: str) -> tuple[dict | None, int]:
    normalized = relative_path.replace("\\", "/").strip()
    for index, item in enumerate(payload.get("files", [])):
        if isinstance(item, dict) and str(item.get("path", "")).replace("\\", "/") == normalized:
            return item, index
    return None, -1


def scan_review_flags(project: Path | None, payload: dict) -> dict[str, set[str]]:
    flags: dict[str, set[str]] = {}
    files = [item for item in payload.get("files", []) if isinstance(item, dict)]

    grouped: dict[str, list[dict]] = {}
    for item in files:
        key = str(item.get("normalizedTextHash") or item.get("contentHash") or "")
        if key:
            grouped.setdefault(key, []).append(item)
    for items in grouped.values():
        if len(items) <= 1:
            continue
        for item in items:
            flags.setdefault(str(item.get("path", "")), set()).add("duplicate")

    family_groups: dict[str, list[dict]] = {}
    for item in files:
        if item.get("scanStatus") == "missing":
            continue
        family_groups.setdefault(scan_family_key(str(item.get("path", ""))), []).append(item)
    for items in family_groups.values():
        content_hashes = {str(item.get("contentHash", "")) for item in items if item.get("contentHash")}
        if len(items) <= 1 or len(content_hashes) <= 1:
            continue
        for item in items:
            flags.setdefault(str(item.get("path", "")), set()).add("version_candidate")
            if scan_version_hints(str(item.get("path", ""))):
                flags.setdefault(str(item.get("path", "")), set()).add("version_ambiguous")

    if project is not None:
        for item in files:
            path = str(item.get("path", ""))
            if item.get("scanStatus") == "missing" and item.get("sourceRecord"):
                flags.setdefault(path, set()).add("missing_promoted_source")
                flags.setdefault(path, set()).add("conflict")
            if scan_text_has_conflict_markers(project, item):
                flags.setdefault(path, set()).add("text_conflict_markers")
                flags.setdefault(path, set()).add("conflict")
    return flags


def readiness_issue(kind: str, severity: str, message: str) -> dict:
    return {"kind": kind, "severity": severity, "message": message}


def ocr_confidence_values(payload: dict) -> list[float]:
    values: list[float] = []

    def collect(candidate: dict) -> None:
        for key in ("confidence", "ocrConfidence", "ocr_confidence"):
            raw = candidate.get(key)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 1.0:
                value = value / 100.0
            if 0.0 <= value <= 1.0:
                values.append(value)

    pages = payload.get("pages", [])
    if not isinstance(pages, list):
        return values
    for page in pages:
        if not isinstance(page, dict):
            continue
        collect(page)
        blocks = page.get("blocks", [])
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict):
                    collect(block)
    return values


def scan_promotion_blockers(
    project: Path | None,
    payload: dict,
    item: dict,
    flags: set[str] | None = None,
    check_files: bool = True,
) -> list[dict]:
    del payload
    blockers: list[dict] = []
    item_flags = set(flags or [])
    path = str(item.get("path", "")).replace("\\", "/")
    scan_status = str(item.get("scanStatus", ""))
    if not path:
        blockers.append(readiness_issue("missing_path", "high", "Scan record has no project-relative path."))
        return blockers
    if scan_status == "missing":
        blockers.append(readiness_issue("missing_source", "high", "Source file is missing from the project."))
    if scan_status == "unsupported":
        blockers.append(readiness_issue("unsupported_source", "medium", "Source scan failed or is unsupported; import as a reference or review manually."))
    for flag in sorted(item_flags & SCAN_PROMOTION_BLOCKING_REVIEW_FLAGS):
        if flag == "duplicate":
            blockers.append(readiness_issue("duplicate_source", "medium", "Another scanned file has the same normalized text or content hash."))
        elif flag == "version_candidate":
            blockers.append(readiness_issue("parallel_version_candidate", "medium", "Similar filenames with different content suggest parallel versions."))
        elif flag == "version_ambiguous":
            blockers.append(readiness_issue("ambiguous_version_hint", "medium", "Filename hints suggest draft, old, copy, final, or version ambiguity."))
        elif flag == "missing_promoted_source":
            blockers.append(readiness_issue("missing_promoted_source", "high", "A source already promoted into memory is now missing."))
        elif flag == "text_conflict_markers":
            blockers.append(readiness_issue("text_conflict_markers", "high", "The source contains merge conflict markers."))
        elif flag == "conflict":
            blockers.append(readiness_issue("conflict_flag", "high", "The scan analysis flagged this source as conflicting."))
    reviewed_hash = str(item.get("reviewedContentHash", "") or "")
    if reviewed_hash and reviewed_hash != str(item.get("contentHash", "") or ""):
        blockers.append(readiness_issue("review_hash_mismatch", "high", "Review was recorded for a different source hash."))

    if project is None or not check_files:
        return blockers

    root = project.resolve()
    source = (root / path).resolve()
    if not is_inside(root, source):
        blockers.append(readiness_issue("source_outside_project", "high", "Source path resolves outside the project root."))
    elif scan_status != "missing":
        if not source.exists() or not source.is_file():
            blockers.append(readiness_issue("source_not_found", "high", "Source file no longer exists at the indexed path."))
        else:
            expected_hash = str(item.get("contentHash", "") or "")
            try:
                current_hash = sha256_file(source)
            except OSError:
                current_hash = ""
            if expected_hash and current_hash and current_hash != expected_hash:
                blockers.append(readiness_issue("source_changed_after_scan", "high", "Source file changed after the last scan; run scan again before promotion."))

    sidecar_rel = str(item.get("ocrSidecar", "") or "").replace("\\", "/")
    if sidecar_rel:
        sidecar = (root / sidecar_rel).resolve()
        if not is_inside(root, sidecar):
            blockers.append(readiness_issue("ocr_sidecar_outside_project", "high", "OCR sidecar path resolves outside the project root."))
        elif not sidecar.exists() or not sidecar.is_file():
            blockers.append(readiness_issue("ocr_sidecar_missing", "high", "OCR sidecar is referenced by the scan record but the file is missing."))
        else:
            try:
                sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                blockers.append(readiness_issue("ocr_sidecar_invalid_json", "high", f"OCR sidecar cannot be read as JSON: {exc}"))
            else:
                valid, error = validate_ocr_sidecar_payload(sidecar_payload, path, str(item.get("contentHash", "") or ""))
                if not valid:
                    blockers.append(readiness_issue("ocr_sidecar_invalid", "high", error))
                expected_text_hash = str(item.get("ocrTextHash", "") or "")
                if expected_text_hash:
                    current_text_hash = sha256_text(ocr_sidecar_text(sidecar_payload))
                    if current_text_hash != expected_text_hash:
                        blockers.append(readiness_issue("ocr_text_hash_mismatch", "high", "OCR sidecar text hash differs from the imported scan record."))
    return blockers


def scan_promotion_warnings(project: Path | None, item: dict) -> list[dict]:
    warnings: list[dict] = []
    scan_status = str(item.get("scanStatus", ""))
    if scan_status == "changed":
        warnings.append(readiness_issue("changed_since_previous_scan", "medium", "Source changed since the previous scan and needs fresh review."))
    sensitivity = str(item.get("sensitivity", "unknown"))
    if sensitivity in {"confidential", "restricted"}:
        warnings.append(readiness_issue("sensitive_source", "medium", f"Source sensitivity is {sensitivity}."))
    if project is None:
        return warnings
    sidecar_rel = str(item.get("ocrSidecar", "") or "").replace("\\", "/")
    if sidecar_rel:
        sidecar = (project.resolve() / sidecar_rel).resolve()
        if is_inside(project.resolve(), sidecar) and sidecar.exists() and sidecar.is_file():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                return warnings
            values = ocr_confidence_values(payload)
            if values and min(values) < 0.70:
                warnings.append(readiness_issue("ocr_low_confidence", "medium", f"OCR sidecar has low-confidence text blocks; minimum confidence is {min(values):.2f}."))
    return warnings


def scan_record_review_summary(
    item: dict,
    flags: set[str] | None = None,
    project: Path | None = None,
    payload: dict | None = None,
) -> dict:
    summary = scan_record_summary(item)
    item_flags = set(flags or [])
    blockers = scan_promotion_blockers(project, payload or {}, item, item_flags)
    warnings = scan_promotion_warnings(project, item)
    summary["reviewFlags"] = sorted(item_flags)
    summary["promotionReady"] = str(item.get("reviewStatus", "unreviewed")) in SCAN_PROMOTABLE_REVIEW_STATUSES and not blockers
    summary["promotionBlockers"] = blockers
    summary["promotionWarnings"] = warnings
    return summary


def scan_record_matches_review_filter(item: dict, flags: set[str], filter_name: str) -> bool:
    scan_status = str(item.get("scanStatus", ""))
    review_status = str(item.get("reviewStatus", "unreviewed"))
    sensitivity = str(item.get("sensitivity", "unknown"))
    if filter_name == "all":
        return True
    if filter_name == "pending":
        return review_status not in SCAN_REVIEW_RESOLVED_STATUSES
    if filter_name in {"new", "changed", "missing", "unsupported"}:
        return scan_status == filter_name
    if filter_name == "unreviewed":
        return review_status == "unreviewed"
    if filter_name == "needs-human":
        return review_status == "needs_human"
    if filter_name == "needs-import":
        return review_status == "needs_import"
    if filter_name == "ready-to-promote":
        return review_status == "ready_to_promote" or (
            review_status in SCAN_PROMOTABLE_REVIEW_STATUSES
            and not bool(flags & SCAN_PROMOTION_BLOCKING_REVIEW_FLAGS)
        )
    if filter_name == "duplicate":
        return review_status == "duplicate" or "duplicate" in flags
    if filter_name == "conflict":
        return review_status == "conflict" or "conflict" in flags
    if filter_name == "versions":
        return review_status == "superseded_candidate" or bool({"version_candidate", "version_ambiguous"} & flags)
    if filter_name == "sensitive":
        return sensitivity in {"confidential", "restricted"}
    return False


def select_scan_review_records(payload: dict, project: Path | None, filter_name: str) -> tuple[list[dict], dict[str, set[str]]]:
    flags = scan_review_flags(project, payload)
    records = [
        item
        for item in payload.get("files", [])
        if isinstance(item, dict)
        and scan_record_matches_review_filter(item, flags.get(str(item.get("path", "")), set()), filter_name)
    ]
    records.sort(key=lambda item: (str(item.get("scanStatus", "")), str(item.get("path", "")).lower()))
    return records, flags


def scan_review_summary(payload: dict, limit: int = 20, project: Path | None = None, filter_name: str = "pending") -> dict:
    files = [item for item in payload.get("files", []) if isinstance(item, dict)]
    by_scan_status: dict[str, int] = {}
    by_review_status: dict[str, int] = {}
    by_sensitivity: dict[str, int] = {}
    pending = []
    review_flags = scan_review_flags(project, payload)
    by_flag: dict[str, int] = {}
    by_blocker: dict[str, int] = {}
    for item in files:
        scan_status = str(item.get("scanStatus", ""))
        review_status = str(item.get("reviewStatus", "unreviewed"))
        sensitivity = str(item.get("sensitivity", "unknown"))
        by_scan_status[scan_status] = by_scan_status.get(scan_status, 0) + 1
        by_review_status[review_status] = by_review_status.get(review_status, 0) + 1
        by_sensitivity[sensitivity] = by_sensitivity.get(sensitivity, 0) + 1
        for flag in review_flags.get(str(item.get("path", "")), set()):
            by_flag[flag] = by_flag.get(flag, 0) + 1
        for blocker in scan_promotion_blockers(
            project,
            payload,
            item,
            review_flags.get(str(item.get("path", "")), set()),
            check_files=False,
        ):
            kind = str(blocker.get("kind", "unknown"))
            by_blocker[kind] = by_blocker.get(kind, 0) + 1
        if review_status not in SCAN_REVIEW_RESOLVED_STATUSES:
            pending.append(item)
    review_queue, _flags = select_scan_review_records(payload, project, filter_name)

    duplicate_groups = []
    grouped: dict[str, list[dict]] = {}
    for item in files:
        key = str(item.get("normalizedTextHash") or item.get("contentHash") or "")
        if key:
            grouped.setdefault(key, []).append(item)
    for key, items in grouped.items():
        if len(items) > 1:
            duplicate_groups.append({
                "hash": key,
                "paths": [str(item.get("path", "")) for item in items],
            })

    duplicate_groups.sort(key=lambda item: (len(item["paths"]), item["paths"][0]), reverse=True)
    return {
        "records": len(files),
        "byScanStatus": dict(sorted(by_scan_status.items())),
        "byReviewStatus": dict(sorted(by_review_status.items())),
        "bySensitivity": dict(sorted(by_sensitivity.items())),
        "byReviewFlag": dict(sorted(by_flag.items())),
        "byPromotionBlocker": dict(sorted(by_blocker.items())),
        "queueFilter": filter_name,
        "reviewQueue": [
            scan_record_review_summary(item, review_flags.get(str(item.get("path", "")), set()), project, payload)
            for item in review_queue[: max(1, limit)]
        ],
        "reviewQueueCount": len(review_queue),
        "pendingReview": [
            scan_record_review_summary(item, review_flags.get(str(item.get("path", "")), set()), project, payload)
            for item in pending[: max(1, limit)]
        ],
        "pendingReviewCount": len(pending),
        "duplicateGroups": duplicate_groups[: max(1, limit)],
        "duplicateGroupCount": len(duplicate_groups),
    }


def scan_analysis_path(project: Path) -> Path:
    return project / SCAN_ANALYSIS_PATH


def scan_record_summary(item: dict) -> dict:
    return {
        "path": str(item.get("path", "")),
        "kind": str(item.get("kind", "")),
        "scanStatus": str(item.get("scanStatus", "")),
        "reviewStatus": str(item.get("reviewStatus", "unreviewed")),
        "sensitivity": str(item.get("sensitivity", "unknown")),
        "modifiedAt": str(item.get("modifiedAt", "")),
        "contentHash": str(item.get("contentHash", "")),
        "normalizedTextHash": str(item.get("normalizedTextHash", "")),
        "sourceRecord": str(item.get("sourceRecord", "")),
    }


def scan_family_key(path: str) -> str:
    stem = Path(path).stem.lower()
    stem = re.sub(r"\b20\d{2}[-_.]?\d{2}[-_.]?\d{2}\b", " ", stem)
    stem = re.sub(r"\b\d{4}[-_.]\d{1,2}[-_.]\d{1,2}\b", " ", stem)
    stem = re.sub(r"\bv(?:ersion)?[-_. ]?\d+\b", " ", stem)
    stem = re.sub(r"\brev[-_. ]?\d+\b", " ", stem)
    tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", stem)
        if token and token not in SCAN_VERSION_HINT_WORDS and not token.isdigit()
    ]
    return "-".join(tokens) or Path(path).stem.lower()


def scan_version_hints(path: str) -> list[str]:
    lower = path.lower()
    hints = []
    for word in sorted(SCAN_VERSION_HINT_WORDS):
        if re.search(rf"(^|[^a-z0-9]){re.escape(word)}([^a-z0-9]|$)", lower):
            hints.append(word)
    if re.search(r"\bv(?:ersion)?[-_. ]?\d+\b", lower):
        hints.append("version-number")
    if re.search(r"\b20\d{2}[-_.]?\d{2}[-_.]?\d{2}\b", lower):
        hints.append("date")
    return hints


def scan_text_has_conflict_markers(project: Path, item: dict) -> bool:
    if item.get("kind") not in SCAN_TEXT_KINDS or item.get("scanStatus") == "missing":
        return False
    path = (project / str(item.get("path", ""))).resolve()
    try:
        path.relative_to(project.resolve())
    except ValueError:
        return False
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    return "<<<<<<<" in text and "=======" in text and ">>>>>>>" in text


def build_scan_analysis(project: Path, payload: dict, limit: int = 50) -> dict:
    files = [item for item in payload.get("files", []) if isinstance(item, dict)]
    duplicate_groups = []
    by_hash: dict[str, list[dict]] = {}
    for item in files:
        key = str(item.get("normalizedTextHash") or item.get("contentHash") or "")
        if key:
            by_hash.setdefault(key, []).append(item)
    for key, items in by_hash.items():
        if len(items) > 1:
            duplicate_groups.append({
                "kind": "normalized_text" if any(item.get("normalizedTextHash") == key for item in items) else "content",
                "hash": key,
                "records": [scan_record_summary(item) for item in sorted(items, key=lambda row: str(row.get("path", "")))],
                "reason": "Multiple scanned files share the same normalized text or content hash.",
            })
    duplicate_groups.sort(key=lambda item: (-len(item["records"]), item["records"][0]["path"]))

    family_groups: dict[str, list[dict]] = {}
    for item in files:
        if item.get("scanStatus") == "missing":
            continue
        family_groups.setdefault(scan_family_key(str(item.get("path", ""))), []).append(item)
    version_candidates = []
    conflict_candidates = []
    for family, items in family_groups.items():
        content_hashes = {str(item.get("contentHash", "")) for item in items if item.get("contentHash")}
        if len(items) > 1 and len(content_hashes) > 1:
            ordered = sorted(items, key=lambda row: str(row.get("modifiedAt", "")), reverse=True)
            version_candidates.append({
                "family": family,
                "latestPath": str(ordered[0].get("path", "")),
                "records": [scan_record_summary(item) for item in ordered],
                "reason": "Similar filenames with different content hashes suggest multiple versions.",
            })
            hinted = [item for item in ordered if scan_version_hints(str(item.get("path", "")))]
            if hinted:
                conflict_candidates.append({
                    "severity": "medium",
                    "kind": "parallel_versions",
                    "family": family,
                    "paths": [str(item.get("path", "")) for item in hinted],
                    "reason": "Filename hints such as draft, final, old, copy, version, or date suggest version ambiguity.",
                })

    for item in files:
        if item.get("scanStatus") == "missing" and item.get("sourceRecord"):
            conflict_candidates.append({
                "severity": "high",
                "kind": "missing_promoted_source",
                "path": str(item.get("path", "")),
                "sourceRecord": str(item.get("sourceRecord", "")),
                "reason": "A source that was promoted into memory is now missing from the scanned project files.",
            })
        if scan_text_has_conflict_markers(project, item):
            conflict_candidates.append({
                "severity": "high",
                "kind": "text_conflict_markers",
                "path": str(item.get("path", "")),
                "reason": "The file contains merge conflict markers and needs human review before promotion.",
            })

    review_summary = scan_review_summary(payload, limit=limit, project=project)
    return {
        "schemaVersion": SCAN_ANALYSIS_SCHEMA_VERSION,
        "generatedAt": utc_iso(),
        "indexPath": FILE_INDEX_PATH.as_posix(),
        "summary": {
            "records": review_summary["records"],
            "pendingReviewCount": review_summary["pendingReviewCount"],
            "duplicateGroupCount": len(duplicate_groups),
            "versionCandidateCount": len(version_candidates),
            "conflictCandidateCount": len(conflict_candidates),
        },
        "byScanStatus": review_summary["byScanStatus"],
        "byReviewStatus": review_summary["byReviewStatus"],
        "pendingReview": review_summary["pendingReview"],
        "duplicateGroups": duplicate_groups[: max(1, limit)],
        "versionCandidates": version_candidates[: max(1, limit)],
        "conflictCandidates": conflict_candidates[: max(1, limit)],
    }


def scan_query_matches(project: Path, query: str, payload: dict, limit: int = 5) -> list[dict]:
    terms = set(graph_tokens(query))
    if not terms:
        return []
    matches = []
    for item in payload.get("files", []):
        if not isinstance(item, dict):
            continue
        review_status = str(item.get("reviewStatus", "unreviewed"))
        if review_status in {"ignored", "promoted", "imported"}:
            continue
        text = " ".join([
            str(item.get("path", "")),
            scan_family_key(str(item.get("path", ""))),
            str(item.get("kind", "")),
        ]).lower()
        hits = sorted(terms & set(graph_tokens(text)))
        if hits or query.lower() in text:
            matches.append({
                "path": str(item.get("path", "")),
                "kind": str(item.get("kind", "")),
                "scanStatus": str(item.get("scanStatus", "")),
                "reviewStatus": review_status,
                "matchedTerms": hits,
            })
        if len(matches) >= limit:
            break
    return matches


def scan_retrieval_warnings(project: Path, query: str, limit: int = 5) -> dict:
    payload = read_file_index(project)
    if not payload:
        return {"ok": True, "hasIndex": False, "warnings": [], "matchingPendingEvidence": []}
    analysis = build_scan_analysis(project, payload, limit=limit)
    matches = scan_query_matches(project, query, payload, limit=limit)
    warnings = []
    summary = analysis["summary"]
    if summary["pendingReviewCount"]:
        warnings.append(f"{summary['pendingReviewCount']} scanned file(s) still need review before they can be treated as memory.")
    if summary["duplicateGroupCount"]:
        warnings.append(f"{summary['duplicateGroupCount']} duplicate scan group(s) need review.")
    if summary["versionCandidateCount"]:
        warnings.append(f"{summary['versionCandidateCount']} possible version family/families need review.")
    if summary["conflictCandidateCount"]:
        warnings.append(f"{summary['conflictCandidateCount']} possible scan conflict(s) need review.")
    if matches:
        warnings.append("The query also matches scanned evidence that is not yet promoted into memory.")
    return {
        "ok": True,
        "hasIndex": True,
        "analysisSummary": summary,
        "warnings": warnings,
        "matchingPendingEvidence": matches,
    }


def cmd_scan_analyze(args) -> int:
    project = args.project_root.resolve()
    payload = read_file_index(project)
    if not payload:
        print(json.dumps({"ok": False, "error": "file index not found; run scan first"}, indent=2))
        return 1
    analysis = build_scan_analysis(project, payload, limit=args.limit)
    target = scan_analysis_path(project)
    write_json(target, analysis)
    result = {"ok": True, "analysisPath": rel(project, target), "summary": analysis["summary"]}
    if getattr(args, "json_output", False):
        result["analysis"] = analysis
        print(json.dumps(result, indent=2))
    else:
        summary = analysis["summary"]
        print(f"Scan analysis written to {rel(project, target)}")
        print(
            f"pending={summary['pendingReviewCount']} duplicates={summary['duplicateGroupCount']} "
            f"versions={summary['versionCandidateCount']} conflicts={summary['conflictCandidateCount']}"
        )
    return 0


def scan_review_proposal_markdown(summary: dict) -> str:
    lines = [
        "# ControlWork Scan Review Proposal",
        "",
        f"- **Generated**: {utc_iso()}",
        f"- **Queue Filter**: {summary.get('queueFilter', 'pending')}",
        f"- **Records**: {summary.get('records', 0)}",
        f"- **Pending Review**: {summary.get('pendingReviewCount', 0)}",
        f"- **Selected Queue**: {summary.get('reviewQueueCount', 0)}",
        "",
        "## Review Status Counts",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for status, count in summary.get("byReviewStatus", {}).items():
        lines.append(f"| {status} | {count} |")
    lines.extend([
        "",
        "## Review Flags",
        "",
        "| Flag | Count |",
        "|---|---:|",
    ])
    flags = summary.get("byReviewFlag", {})
    if flags:
        for flag, count in flags.items():
            lines.append(f"| {flag} | {count} |")
    else:
        lines.append("| _none_ | 0 |")
    lines.extend([
        "",
        "## Selected Queue",
        "",
        "| Path | Scan | Review | Sensitivity | Flags |",
        "|---|---|---|---|---|",
    ])
    queue = summary.get("reviewQueue", [])
    if queue:
        for item in queue:
            flags_text = ", ".join(item.get("reviewFlags", []))
            lines.append(
                f"| `{item.get('path', '')}` | {item.get('scanStatus', '')} | "
                f"{item.get('reviewStatus', '')} | {item.get('sensitivity', '')} | {flags_text} |"
            )
    else:
        lines.append("| _none_ |  |  |  |  |")
    lines.extend([
        "",
        "## Notes",
        "",
        "- This proposal is evidence for review, not canonical memory.",
        "- Use `scan-review <path> --review-status ...` or `scan-review --batch --filter ... --review-status ...` to apply review decisions.",
        "- Use `scan-promote` only after the selected source has an acceptable review status.",
        "",
    ])
    return "\n".join(lines)


def write_scan_review_proposal(project: Path, summary: dict, output: Path | None) -> Path:
    if output is None:
        target = project / PROPOSAL_ROOT / f"{utc_stamp()}-scan-review.md"
    else:
        target = resolve_output_path(project, output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(scan_review_proposal_markdown(summary), encoding="utf-8")
    return target


def cmd_scan_review(args) -> int:
    from . import work_features as feature_api

    return run_scan_review(args, feature_api)


def unique_memory_entry_path(project: Path, area: str, title: str) -> Path:
    base = project / MEMORY_ROOT / area / f"{utc_stamp()}-{slug(title)}.md"
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    for index in range(2, 1000):
        candidate = base.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("could not find an available memory entry filename")


def memory_entries_with_source_hash(project: Path, content_hash: str) -> list[dict]:
    if not content_hash:
        return []
    matches = []
    needle = f"- **Source Hash**: {content_hash}"
    for area in CAPTURE_AREAS:
        root = project / MEMORY_ROOT / area
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md")):
            if path.name == ".gitkeep":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if needle in text:
                matches.append({"path": rel(project, path), "area": area})
    return matches


def source_ledger_path(project: Path) -> Path:
    return project / SOURCE_LEDGER_PATH


def read_source_ledger(project: Path) -> dict:
    payload = read_json(source_ledger_path(project))
    if payload.get("schemaVersion") != SOURCE_LEDGER_SCHEMA_VERSION or not isinstance(payload.get("records"), list):
        return {"schemaVersion": SOURCE_LEDGER_SCHEMA_VERSION, "records": []}
    return payload


def append_source_ledger_record(project: Path, record: dict) -> dict:
    payload = read_source_ledger(project)
    payload["updatedAt"] = utc_iso()
    payload.setdefault("records", []).append(record)
    write_json(source_ledger_path(project), payload)
    return payload


class SourceHTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self.headings: list[str] = []
        self.links: list[str] = []
        self._ignore_depth = 0
        self._in_title = False
        self._heading_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignore_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_tag = tag
        if tag == "a":
            href = ""
            label = ""
            for key, value in attrs:
                if key.lower() == "href" and value:
                    href = value.strip()
                if key.lower() in {"title", "aria-label"} and value:
                    label = value.strip()
            if href:
                self.links.append(f"{label}: {href}" if label else href)
        if tag in {"br", "p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == self._heading_tag:
            self._heading_tag = ""
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        if self._heading_tag:
            self.headings.append(value)
        self.text_parts.append(value)


def truncate_import_text(text: str, max_chars: int, warnings: list[str]) -> tuple[str, bool]:
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip() + "\n\n[Extract truncated.]"
        warnings.append("Extract truncated by max character limit.")
    return text, truncated


def csv_preview(text: str, suffix: str = ".csv", max_rows: int = 40) -> dict:
    warnings = []
    rows = []
    sample = text[:8192]
    try:
        if suffix.lower() == ".tsv":
            dialect = csv.excel_tab
        else:
            try:
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        total_rows = 0
        max_columns = 0
        for row in reader:
            total_rows += 1
            max_columns = max(max_columns, len(row))
            if len(rows) < max_rows:
                rows.append(row)
        if total_rows > max_rows:
            warnings.append("CSV preview truncated by row limit.")
    except csv.Error as exc:
        return {
            "ok": True,
            "text": text,
            "metadata": {},
            "warnings": [f"CSV parse failed; imported raw text for review: {exc}"],
        }
    delimiter = "\\t" if getattr(dialect, "delimiter", ",") == "\t" else getattr(dialect, "delimiter", ",")
    lines = [
        f"CSV table preview",
        f"Rows: {total_rows}",
        f"Columns: {max_columns}",
        f"Delimiter: {delimiter}",
        "",
    ]
    if rows:
        lines.extend(", ".join(cell.strip() for cell in row) for row in rows)
    else:
        lines.append("[No rows extracted.]")
    return {
        "ok": True,
        "text": "\n".join(lines),
        "metadata": {"rowCount": total_rows, "columnCount": max_columns, "delimiter": delimiter},
        "warnings": warnings,
    }


def html_text_extract(text: str) -> dict:
    parser = SourceHTMLTextParser()
    try:
        parser.feed(text)
    except Exception as exc:  # HTMLParser can surface malformed entity errors.
        return {
            "ok": True,
            "text": text,
            "metadata": {},
            "warnings": [f"HTML parse failed; imported raw text for review: {exc}"],
        }
    body = "\n".join(line.strip() for line in " ".join(parser.text_parts).splitlines() if line.strip())
    body = "\n".join(line.strip() for line in body.split("\n") if line.strip())
    title = " ".join(parser.title_parts).strip()
    headings = [item for item in parser.headings if item]
    links = []
    seen_links = set()
    for link in parser.links:
        if link not in seen_links:
            links.append(link)
            seen_links.add(link)
    lines = []
    if title:
        lines.extend([f"Title: {title}", ""])
    if headings:
        lines.extend(["Headings:", *[f"- {item}" for item in headings[:40]], ""])
    if body:
        lines.extend(["Text:", body, ""])
    if links:
        lines.extend(["Links:", *[f"- {item}" for item in links[:80]]])
    return {
        "ok": True,
        "text": "\n".join(lines).strip() or body,
        "metadata": {"title": title, "headingCount": len(headings), "linkCount": len(links)},
        "warnings": [],
    }


def url_metadata_extract(text: str) -> dict:
    warnings = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "ok": True,
            "text": text,
            "metadata": {},
            "warnings": [f"URL metadata JSON parse failed; imported raw text for review: {exc}"],
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "URL metadata import expects a JSON object",
        }
    url = str(payload.get("url") or payload.get("href") or payload.get("sourceUrl") or "").strip()
    title = str(payload.get("title") or payload.get("name") or "").strip()
    description = str(payload.get("description") or payload.get("summary") or "").strip()
    captured_at = str(payload.get("capturedAt") or payload.get("createdAt") or "").strip()
    parsed = urlparse(url) if url else None
    if not url:
        warnings.append("URL metadata does not include a url field.")
    lines = ["URL metadata"]
    if title:
        lines.append(f"Title: {title}")
    if url:
        lines.append(f"URL: {url}")
    if parsed and parsed.netloc:
        lines.append(f"Domain: {parsed.netloc}")
    if captured_at:
        lines.append(f"Captured: {captured_at}")
    if description:
        lines.extend(["", "Description:", description])
    extras = {
        key: value
        for key, value in payload.items()
        if key not in {"url", "href", "sourceUrl", "title", "name", "description", "summary", "capturedAt", "createdAt"}
        and isinstance(value, (str, int, float, bool))
    }
    if extras:
        lines.extend(["", "Metadata:"])
        for key, value in sorted(extras.items()):
            lines.append(f"- {key}: {value}")
    return {
        "ok": True,
        "text": "\n".join(lines),
        "metadata": {"url": url, "domain": parsed.netloc if parsed else "", "title": title},
        "warnings": warnings,
    }


def extraction_with_text(mode: str, extractor: str, text: str, warnings: list[str], metadata: dict, max_chars: int) -> dict:
    text, truncated = truncate_import_text(text, max_chars, warnings)
    return {
        "ok": True,
        "mode": mode,
        "extractor": extractor,
        "text": text,
        "textHash": sha256_text(text) if text else "",
        "truncated": truncated,
        "warnings": warnings,
        "metadata": metadata,
    }


def import_extract_for_source(source: Path, kind: str, as_reference: bool, max_chars: int) -> dict:
    suffix = source.suffix.lower()
    if kind == "office" and suffix == ".docx":
        extraction = docx_text_extract(source)
        if not extraction.get("ok"):
            if not as_reference:
                return {"ok": False, "error": extraction.get("error", "DOCX extraction failed")}
            return {
                "ok": True,
                "mode": "reference",
                "extractor": "reference_only",
                "text": "",
                "textHash": "",
                "truncated": False,
                "warnings": [str(extraction.get("error", "DOCX imported as reference metadata only"))],
                "metadata": {},
            }
        return extraction_with_text(
            "extracted_text",
            "docx_text",
            str(extraction.get("text", "")),
            list(extraction.get("warnings", [])),
            dict(extraction.get("metadata", {})),
            max_chars,
        )
    if kind == "office" and suffix == ".xlsx":
        extraction = xlsx_text_extract(source)
        if not extraction.get("ok"):
            if not as_reference:
                return {"ok": False, "error": extraction.get("error", "XLSX extraction failed")}
            return {
                "ok": True,
                "mode": "reference",
                "extractor": "reference_only",
                "text": "",
                "textHash": "",
                "truncated": False,
                "warnings": [str(extraction.get("error", "XLSX imported as reference metadata only"))],
                "metadata": {},
            }
        return extraction_with_text(
            "table_summary",
            "xlsx_table_summary",
            str(extraction.get("text", "")),
            list(extraction.get("warnings", [])),
            dict(extraction.get("metadata", {})),
            max_chars,
        )
    if kind == "pdf":
        extraction = pdf_text_extract(source, max_chars)
        if extraction.get("ok"):
            return extraction_with_text(
                "extracted_text",
                "pdf_native_text_best_effort",
                str(extraction.get("text", "")),
                list(extraction.get("warnings", [])),
                dict(extraction.get("metadata", {})),
                max_chars,
            )
        if not as_reference:
            return {"ok": False, "error": extraction.get("error", "no PDF text importer result")}
    if kind not in SCAN_TEXT_KINDS:
        if not as_reference:
            return {
                "ok": False,
                "error": f"no text importer for {kind}; use --as-reference to import metadata only",
            }
        return {
            "ok": True,
            "mode": "reference",
            "extractor": "reference_only",
            "text": "",
            "textHash": "",
            "truncated": False,
            "warnings": [f"{kind} imported as reference metadata only"],
            "metadata": {},
        }
    try:
        text = source.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    warnings = []
    metadata = {}
    extractor = f"{kind}_text"
    if kind == "json":
        try:
            text = json.dumps(json.loads(text), indent=2, sort_keys=True)
            extractor = "json_pretty"
        except json.JSONDecodeError:
            warnings.append("JSON parse failed; imported raw text for review.")
            extractor = "json_raw"
    elif kind == "url_metadata":
        extraction = url_metadata_extract(text)
        if not extraction.get("ok"):
            return extraction
        text = str(extraction.get("text", ""))
        metadata = dict(extraction.get("metadata", {}))
        warnings.extend(extraction.get("warnings", []))
        extractor = "url_metadata"
    elif kind == "csv":
        extraction = csv_preview(text, suffix=suffix)
        text = str(extraction.get("text", ""))
        metadata = dict(extraction.get("metadata", {}))
        warnings.extend(extraction.get("warnings", []))
        extractor = "csv_table_preview"
    elif kind == "html":
        extraction = html_text_extract(text)
        text = str(extraction.get("text", ""))
        metadata = dict(extraction.get("metadata", {}))
        warnings.extend(extraction.get("warnings", []))
        extractor = "html_text"
    return extraction_with_text("extracted_text", extractor, text, warnings, metadata, max_chars)


def imported_entry_content(
    title: str,
    area: str,
    lifecycle: str,
    record: dict,
    summary: str,
    category: str,
    extraction: dict,
) -> str:
    metadata = extraction.get("metadata", {})
    lines = [
        f"# {title}",
        "",
        f"- **Area**: {area}",
        f"- **Lifecycle**: {lifecycle}",
        f"- **Captured**: {utc_stamp()}",
        f"- **Source**: {record.get('path', '')}",
        f"- **Source Kind**: {record.get('kind', '')}",
        f"- **Source Hash**: {record.get('contentHash', '')}",
        f"- **Text Hash**: {extraction.get('textHash', '')}",
        f"- **Import Mode**: {extraction.get('mode', '')}",
        f"- **Extractor**: {extraction.get('extractor', '')}",
        "- **Review Status**: needs_review",
    ]
    if category:
        lines.append(f"- **Category**: {category}")
    if isinstance(metadata, dict):
        for key in sorted(metadata):
            value = metadata[key]
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                lines.append(f"- **Import Metadata {key}**: {value}")
    for warning in extraction.get("warnings", []):
        lines.append(f"- **Import Warning**: {warning}")
    lines.extend([
        "",
        "## Summary",
        "",
        summary.strip() or "Imported source evidence. Review before treating it as current project memory.",
        "",
        "## Body",
        "",
    ])
    if extraction.get("text"):
        lines.extend(["```text", str(extraction["text"]).strip(), "```"])
    else:
        lines.append("Reference-only import. Review the original source file before extracting claims or decisions.")
    lines.append("")
    return "\n".join(lines)


def scan_import_proposal_markdown(record: dict, area: str, lifecycle: str, title: str, summary: str, extraction: dict) -> str:
    warnings = extraction.get("warnings", [])
    metadata = extraction.get("metadata", {})
    lines = [
        "# ControlWork Source Import Proposal",
        "",
        f"- **Generated**: {utc_iso()}",
        f"- **Source Path**: {record.get('path', '')}",
        f"- **Source Kind**: {record.get('kind', '')}",
        f"- **Target Area**: {area}",
        f"- **Target Lifecycle**: {lifecycle}",
        f"- **Title**: {title}",
        f"- **Import Mode**: {extraction.get('mode', '')}",
        f"- **Extractor**: {extraction.get('extractor', '')}",
        f"- **Source Hash**: {record.get('contentHash', '')}",
        f"- **Text Hash**: {extraction.get('textHash', '')}",
        "",
        "## Summary",
        "",
        summary.strip() or "Imported source evidence. Review before treating it as current project memory.",
        "",
        "## Warnings",
        "",
    ]
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- No import warnings.")
    if isinstance(metadata, dict) and metadata:
        lines.extend(["", "## Extraction Metadata", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(metadata.items()) if str(value).strip())
    lines.extend([
        "",
        "## Proposed Entry Preview",
        "",
        "```markdown",
        imported_entry_content(title, area, lifecycle, record, summary, "", extraction).strip(),
        "```",
        "",
    ])
    return "\n".join(lines)


def write_scan_import_proposal(
    project: Path,
    record: dict,
    area: str,
    lifecycle: str,
    title: str,
    summary: str,
    extraction: dict,
    output: Path | None,
) -> Path:
    if output is None:
        target = project / PROPOSAL_ROOT / f"{utc_stamp()}-source-import-{slug(title)}.md"
    else:
        target = resolve_output_path(project, output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(scan_import_proposal_markdown(record, area, lifecycle, title, summary, extraction), encoding="utf-8")
    return target


def cmd_scan_import(args) -> int:
    project = args.project_root.resolve()
    payload = read_file_index(project)
    if not payload:
        print(json.dumps({"ok": False, "error": "file index not found; run scan first"}, indent=2))
        return 1
    target_path = str(args.path).strip().replace("\\", "/")
    record, _index = scan_record_by_path(payload, target_path)
    if not record:
        print(json.dumps({"ok": False, "error": f"path is not in file index: {target_path}"}, indent=2))
        return 1
    expected_content_hash = str(record.get("contentHash", ""))
    if record.get("scanStatus") == "missing":
        print(json.dumps({"ok": False, "error": "cannot import a missing file"}, indent=2))
        return 1
    if record.get("sourceRecord") and not args.force:
        print(json.dumps({"ok": False, "error": "file is already imported or promoted", "sourceRecord": record["sourceRecord"]}, indent=2))
        return 1
    area = args.area
    if area not in CAPTURE_AREAS:
        print(json.dumps({"ok": False, "error": f"unknown memory area: {area}"}, indent=2))
        return 1
    lifecycle = args.lifecycle
    if lifecycle not in {"captured", "active", "needs_review", "superseded", "legacy"}:
        print(json.dumps({"ok": False, "error": f"unknown lifecycle: {lifecycle}"}, indent=2))
        return 1
    try:
        category = validate_category(project, args.category) if args.category else ""
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    source = (project / target_path).resolve()
    try:
        source.relative_to(project)
    except ValueError:
        print(json.dumps({"ok": False, "error": "source path is outside project root"}, indent=2))
        return 1
    if not source.exists() or not source.is_file():
        print(json.dumps({"ok": False, "error": "source file does not exist"}, indent=2))
        return 1
    expected_hash = expected_content_hash
    try:
        current_hash = sha256_file(source)
    except OSError:
        current_hash = ""
    if expected_hash and current_hash and current_hash != expected_hash:
        print(json.dumps({
            "ok": False,
            "error": "source file changed after the last scan; run scan again before import",
            "path": target_path,
            "expectedHash": expected_hash,
            "currentHash": current_hash,
        }, indent=2))
        return 1
    extraction = import_extract_for_source(
        source,
        str(record.get("kind", "")),
        as_reference=bool(getattr(args, "as_reference", False)),
        max_chars=max(1, int(getattr(args, "max_chars", SCAN_IMPORT_EXTRACT_CHARS))),
    )
    if not extraction.get("ok"):
        print(json.dumps(extraction, indent=2))
        return 1
    title = args.title or Path(target_path).stem.replace("-", " ").replace("_", " ").strip().title() or "Imported Source"
    summary = str(getattr(args, "summary", "") or "").strip()
    try:
        memory_path = unique_memory_entry_path(project, area, title)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    preview = {
        "ok": True,
        "path": target_path,
        "area": area,
        "lifecycle": lifecycle,
        "title": title,
        "importMode": extraction.get("mode", ""),
        "extractor": extraction.get("extractor", ""),
        "wouldWrite": rel(project, memory_path),
        "warnings": extraction.get("warnings", []),
        "dryRun": bool(getattr(args, "dry_run", False)),
        "proposal": bool(getattr(args, "proposal", False)),
    }
    if getattr(args, "dry_run", False):
        print(json.dumps(preview, indent=2))
        return 0
    if getattr(args, "proposal", False):
        try:
            proposal = write_scan_import_proposal(project, record, area, lifecycle, title, summary, extraction, getattr(args, "output", None))
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
        preview["proposalPath"] = rel(project, proposal)
        print(json.dumps(preview, indent=2))
        return 0

    memory_path.parent.mkdir(parents=True, exist_ok=True)
    content = imported_entry_content(title, area, lifecycle, record, summary, category, extraction)
    memory_path.write_text(content, encoding="utf-8")
    imported_hash = sha256_text(content)
    now = utc_iso()

    def complete_import(_current: dict, current_record: dict) -> dict:
        if current_record.get("scanStatus") == "missing":
            raise ReviewQueueError("cannot import a missing file")
        if current_record.get("sourceRecord") and not args.force:
            raise ReviewQueueError("file is already imported or promoted")
        current_record["reviewStatus"] = "imported"
        current_record["sourceRecord"] = rel(project, memory_path)
        current_record["importedAt"] = now
        current_record["importMode"] = extraction.get("mode", "")
        current_record["importedHash"] = imported_hash
        return current_record

    try:
        record = mutate_current_scan_record(
            project,
            target_path,
            expected_content_hash=expected_hash,
            operation="scan-import",
            mutate=complete_import,
        )
    except ReviewQueueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    ledger_record = {
        "importedAt": now,
        "originalPath": target_path,
        "importedMemoryPath": rel(project, memory_path),
        "originalHash": record.get("contentHash", ""),
        "textHash": extraction.get("textHash", ""),
        "importedHash": imported_hash,
        "extractor": extraction.get("extractor", ""),
        "importMode": extraction.get("mode", ""),
        "reviewStatus": "imported",
        "lifecycle": lifecycle,
        "category": category,
        "extractionMetadata": extraction.get("metadata", {}),
        "warnings": extraction.get("warnings", []),
    }
    append_source_ledger_record(project, ledger_record)
    print(json.dumps({
        "ok": True,
        "path": target_path,
        "memoryPath": rel(project, memory_path),
        "ledgerPath": SOURCE_LEDGER_PATH.as_posix(),
        "reviewStatus": record["reviewStatus"],
        "lifecycle": lifecycle,
        "importMode": extraction.get("mode", ""),
        "warnings": extraction.get("warnings", []),
    }, indent=2))
    return 0


def default_ocr_sidecar_path(project: Path, source_rel: str) -> Path:
    return project / EXTRACTS_ROOT / f"{slug(source_rel)}.ocr.json"


def ocr_adapter_config_path(project: Path) -> Path:
    return project / OCR_ADAPTER_CONFIG_PATH


def runtime_command_args(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return shlex.split(value, posix=os.name != "nt")
    return []


def command_executable(project: Path, command_args: list[str]) -> bool:
    if not command_args:
        return False
    executable = command_args[0]
    executable_path = Path(executable)
    if executable_path.is_absolute():
        return executable_path.exists()
    if "/" in executable or "\\" in executable:
        return (project / executable_path).exists()
    return shutil.which(executable) is not None


def timeout_seconds(config: dict) -> tuple[int, str]:
    try:
        timeout = int(config.get("timeoutSeconds") or 60)
    except (TypeError, ValueError):
        return 60, "timeoutSeconds must be an integer"
    if timeout < 1 or timeout > 600:
        return max(1, min(timeout, 600)), "timeoutSeconds must be between 1 and 600"
    return timeout, ""


def read_ocr_adapter_config(project: Path) -> dict:
    payload = read_json(ocr_adapter_config_path(project))
    return payload if isinstance(payload, dict) else {}


def local_ocr_runtime_status(project: Path, config: dict) -> dict:
    enabled = bool(config.get("enabled"))
    command_args = runtime_command_args(config.get("command"))
    executable = command_executable(project, command_args)
    timeout, timeout_issue = timeout_seconds(config)
    accepted = config.get("acceptedSourceKinds")
    if not isinstance(accepted, list) or not accepted:
        accepted = config.get("acceptedSourceTypes")
    if not isinstance(accepted, list) or not accepted:
        accepted = sorted(OCR_SOURCE_KINDS)
    accepted = sorted({str(item).strip().lower().lstrip(".") for item in accepted if str(item).strip()})
    issues = []
    if enabled and not command_args:
        issues.append("localRuntime.command is required when enabled")
    if enabled and command_args and not executable:
        issues.append("localRuntime.command executable was not found")
    if timeout_issue:
        issues.append(timeout_issue)
    return {
        "id": "local_runtime_v1",
        "kind": "local_runtime",
        "configured": enabled and bool(command_args),
        "available": enabled and bool(command_args) and executable and not issues,
        "externalRuntime": True,
        "network": False,
        "commandConfigured": bool(command_args),
        "commandExecutable": executable,
        "timeoutSeconds": timeout,
        "acceptedSourceKinds": accepted or sorted(OCR_SOURCE_KINDS),
        "configurationIssues": issues,
    }


def ocr_adapter_status(project: Path) -> dict:
    config = read_ocr_adapter_config(project)
    runtime_config = config.get("localRuntime") if isinstance(config.get("localRuntime"), dict) else {}
    adapter = local_ocr_runtime_status(project, runtime_config)
    active = str(config.get("activeAdapter") or "")
    active_adapter = active if active == adapter["id"] and adapter["available"] else ""
    return {
        "interfaceVersion": OCR_ADAPTER_INTERFACE_VERSION,
        "configPath": OCR_ADAPTER_CONFIG_PATH.as_posix(),
        "hasConfig": ocr_adapter_config_path(project).exists(),
        "activeAdapter": active_adapter,
        "adapterConfigured": bool(adapter["configured"]),
        "runSupported": bool(active_adapter),
        "adapters": [adapter],
    }


def ocr_page_text(page: dict) -> list[str]:
    parts = []
    for key in ("text", "ocrText", "ocr_text"):
        value = str(page.get(key, "") or "").strip()
        if value:
            parts.append(value)
    blocks = page.get("blocks", [])
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for key in ("text", "ocrText", "ocr_text"):
                value = str(block.get(key, "") or "").strip()
                if value:
                    parts.append(value)
                    break
    return parts


def ocr_sidecar_text(payload: dict) -> str:
    pages = payload.get("pages", [])
    if not isinstance(pages, list):
        return ""
    page_texts = []
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        parts = ocr_page_text(page)
        if parts:
            page_number = page.get("pageNumber", page.get("page", index))
            page_texts.append(f"[Page {page_number}]\n" + "\n".join(parts))
    return "\n\n".join(page_texts).strip()


def ocr_source_hash(payload: dict) -> str:
    for key in ("sourceHash", "sourceSha256", "sha256"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            return value
    source = payload.get("source", {})
    if isinstance(source, dict):
        for key in ("hash", "sha256", "sourceHash"):
            value = str(source.get(key, "") or "").strip()
            if value:
                return value
    return ""


def ocr_source_path(payload: dict) -> str:
    for key in ("sourcePath", "source", "path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\\", "/")
    source = payload.get("source", {})
    if isinstance(source, dict):
        value = str(source.get("path", "") or "").strip()
        if value:
            return value.replace("\\", "/")
    return ""


def ocr_extractor_name(payload: dict) -> str:
    for key in ("extractor", "extractionMethod", "engine"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            name = str(value.get("name", "") or "").strip()
            version = str(value.get("version", "") or "").strip()
            if name and version:
                return f"{name}/{version}"
            if name:
                return name
    return "ocr_sidecar"


def validate_ocr_sidecar_payload(payload: dict, source_rel: str, expected_hash: str) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "OCR sidecar must be a JSON object"
    if not isinstance(payload.get("pages"), list):
        return False, "OCR sidecar must contain a top-level pages list"
    declared_source = ocr_source_path(payload)
    if declared_source and declared_source != source_rel.replace("\\", "/"):
        return False, f"OCR sidecar sourcePath does not match source: {declared_source}"
    declared_hash = ocr_source_hash(payload)
    if declared_hash and expected_hash and declared_hash != expected_hash:
        return False, "OCR sidecar source hash does not match scanned source hash"
    if not ocr_sidecar_text(payload):
        return False, "OCR sidecar contains no extractable page or block text"
    return True, ""


def run_local_ocr_adapter(project: Path, source_path: Path, source_rel: str, record: dict, output_path: Path) -> tuple[dict | None, str, dict]:
    config = read_ocr_adapter_config(project)
    if str(config.get("activeAdapter") or "") != "local_runtime_v1":
        return None, "no OCR adapter configured for portable ControlWork", {}
    runtime_config = config.get("localRuntime") if isinstance(config.get("localRuntime"), dict) else {}
    status = local_ocr_runtime_status(project, runtime_config)
    if not status["available"]:
        issues = "; ".join(status.get("configurationIssues", []))
        suffix = f": {issues}" if issues else ""
        return None, "local OCR runtime adapter is not available" + suffix, status
    source_kind = str(record.get("kind", ""))
    accepted = set(status.get("acceptedSourceKinds", []))
    if accepted and source_kind not in accepted:
        return None, f"source kind is not accepted by the OCR adapter: {source_kind}", status
    request = {
        "interfaceVersion": OCR_ADAPTER_INTERFACE_VERSION,
        "adapter": "local_runtime_v1",
        "projectRoot": str(project.resolve()),
        "sourcePath": source_rel,
        "sourceAbsolutePath": str(source_path),
        "sourceHash": record.get("contentHash", ""),
        "sourceKind": source_kind,
        "sourceSizeBytes": record.get("sizeBytes", 0),
        "outputPath": rel(project, output_path),
        "outputAbsolutePath": str(output_path),
        "sidecarSchema": OCR_SIDECAR_SCHEMA_VERSION,
        "timeoutSeconds": status["timeoutSeconds"],
    }
    command_args = runtime_command_args(runtime_config.get("command"))
    try:
        result = subprocess.run(
            command_args,
            cwd=project,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            timeout=int(status["timeoutSeconds"]),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc), status
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or f"runtime exited {result.returncode}").strip()
        return None, reason[:500], status
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return None, f"invalid OCR adapter JSON: {exc}", status
    valid, error = validate_ocr_sidecar_payload(payload, source_rel, str(record.get("contentHash", "")))
    if not valid:
        return None, error, status
    return payload, "", status


def cmd_ocr_status(args) -> int:
    project = args.project_root.resolve()
    index = read_file_index(project)
    adapter_status = ocr_adapter_status(project)
    sidecars = []
    extracts = project / EXTRACTS_ROOT
    if extracts.exists():
        sidecars = [rel(project, path) for path in sorted(extracts.glob("*.ocr.json")) if path.is_file()]
    pending = []
    for item in index.get("files", []):
        if not isinstance(item, dict):
            continue
        if item.get("kind") in OCR_SOURCE_KINDS and item.get("scanStatus") != "missing" and not item.get("ocrSidecar"):
            pending.append(scan_record_summary(item))
    payload = {
        "ok": True,
        "adapterConfigured": adapter_status["adapterConfigured"],
        "runSupported": adapter_status["runSupported"],
        "interfaceVersion": OCR_ADAPTER_INTERFACE_VERSION,
        "configPath": OCR_ADAPTER_CONFIG_PATH.as_posix(),
        "hasConfig": adapter_status["hasConfig"],
        "activeAdapter": adapter_status["activeAdapter"],
        "adapters": adapter_status["adapters"],
        "acceptedSourceKinds": sorted(OCR_SOURCE_KINDS),
        "sidecarSchema": OCR_SIDECAR_SCHEMA_VERSION,
        "extractsRoot": EXTRACTS_ROOT.as_posix(),
        "sidecars": sidecars,
        "sidecarCount": len(sidecars),
        "pendingSourceCount": len(pending),
        "pendingSources": pending[: max(1, int(getattr(args, "limit", 20)))],
        "message": (
            "OCR adapter ready. Run ocr run for one source."
            if adapter_status["runSupported"]
            else "No OCR adapter is bundled or configured. Import a project-owned sidecar with ocr import-sidecar."
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_ocr_run(args) -> int:
    project = args.project_root.resolve()
    payload = read_file_index(project)
    if not payload:
        print(json.dumps({"ok": False, "error": "file index not found; run scan first"}, indent=2))
        return 1
    source = str(args.source).strip().replace("\\", "/")
    record, _index = scan_record_by_path(payload, source)
    if not record:
        print(json.dumps({"ok": False, "error": f"source is not in file index: {source}"}, indent=2))
        return 1
    if record.get("scanStatus") == "missing":
        print(json.dumps({"ok": False, "error": "cannot run OCR for a missing file"}, indent=2))
        return 1
    if str(record.get("kind", "")) not in OCR_SOURCE_KINDS:
        print(json.dumps({"ok": False, "error": "OCR adapters are accepted only for pdf, image, or office sources"}, indent=2))
        return 1
    expected_content_hash = str(record.get("contentHash", ""))
    try:
        source_path = resolve_input_path(project, Path(source))
        output = resolve_output_path(project, Path(args.output)) if args.output else default_ocr_sidecar_path(project, rel(project, source_path))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    if not source_path.exists() or not source_path.is_file():
        print(json.dumps({"ok": False, "error": "source file does not exist", "sourcePath": rel(project, source_path)}, indent=2))
        return 1
    if output.exists() and not getattr(args, "force", False):
        print(json.dumps({"ok": False, "error": "OCR sidecar output already exists", "sidecarPath": rel(project, output)}, indent=2))
        return 1
    sidecar_payload, error, status = run_local_ocr_adapter(project, source_path, source, record, output)
    if sidecar_payload is None:
        print(json.dumps({
            "ok": False,
            "error": error,
            "sourcePath": rel(project, source_path),
            "suggestedOutput": rel(project, output),
            "adapterStatus": status,
            "nextStep": "Configure a local OCR adapter or import a project-owned sidecar with `ocr import-sidecar`.",
        }, indent=2))
        return 1
    normalized = dict(sidecar_payload)
    normalized.setdefault("schemaVersion", OCR_SIDECAR_SCHEMA_VERSION)
    normalized.setdefault("sourcePath", source)
    normalized.setdefault("sourceHash", record.get("contentHash", ""))
    normalized.setdefault("extractor", "local_runtime_v1")
    normalized["generatedAt"] = utc_iso()
    text = ocr_sidecar_text(normalized)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, normalized)

    def complete_ocr_run(_current: dict, current_record: dict) -> dict:
        if current_record.get("scanStatus") == "missing":
            raise ReviewQueueError("cannot run OCR for a missing file")
        if str(current_record.get("kind", "")) not in OCR_SOURCE_KINDS:
            raise ReviewQueueError("OCR adapters are accepted only for pdf, image, or office sources")
        current_record["ocrSidecar"] = rel(project, output)
        current_record["ocrGeneratedAt"] = normalized["generatedAt"]
        current_record["ocrTextHash"] = sha256_text(text)
        return current_record

    try:
        record = mutate_current_scan_record(
            project,
            source,
            expected_content_hash=expected_content_hash,
            operation="ocr run",
            mutate=complete_ocr_run,
        )
    except ReviewQueueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "ok": True,
        "sourcePath": source,
        "sidecarPath": rel(project, output),
        "textHash": record["ocrTextHash"],
        "textChars": len(text),
        "adapter": "local_runtime_v1",
        "nextStep": "Review with `ocr import-sidecar` before treating OCR text as project memory.",
    }, indent=2))
    return 0


def cmd_ocr_import_sidecar(args) -> int:
    project = args.project_root.resolve()
    payload = read_file_index(project)
    if not payload:
        print(json.dumps({"ok": False, "error": "file index not found; run scan first"}, indent=2))
        return 1
    source_rel = str(args.source).strip().replace("\\", "/")
    record, _index = scan_record_by_path(payload, source_rel)
    if not record:
        print(json.dumps({"ok": False, "error": f"source is not in file index: {source_rel}"}, indent=2))
        return 1
    if record.get("scanStatus") == "missing":
        print(json.dumps({"ok": False, "error": "cannot import OCR for a missing file"}, indent=2))
        return 1
    if str(record.get("kind", "")) not in OCR_SOURCE_KINDS:
        print(json.dumps({"ok": False, "error": "OCR sidecars are accepted only for pdf, image, or office sources"}, indent=2))
        return 1
    expected_content_hash = str(record.get("contentHash", ""))
    source_path = (project / source_rel).resolve()
    if not is_inside(project, source_path) or not source_path.exists() or not source_path.is_file():
        print(json.dumps({"ok": False, "error": "source file does not exist or is outside project root"}, indent=2))
        return 1
    try:
        sidecar_path = resolve_input_path(project, Path(args.sidecar))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    try:
        sidecar_payload = json.loads(sidecar_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": f"invalid OCR sidecar JSON: {exc}"}, indent=2))
        return 1
    valid, error = validate_ocr_sidecar_payload(sidecar_payload, source_rel, str(record.get("contentHash", "")))
    if not valid:
        print(json.dumps({"ok": False, "error": error}, indent=2))
        return 1
    text = ocr_sidecar_text(sidecar_payload)
    target = default_ocr_sidecar_path(project, source_rel)
    if target.exists() and not args.force and rel(project, target) != rel(project, sidecar_path):
        print(json.dumps({"ok": False, "error": "OCR sidecar already exists", "sidecarPath": rel(project, target)}, indent=2))
        return 1
    if args.import_source and record.get("sourceRecord") and not args.force:
        print(json.dumps({"ok": False, "error": "source already has imported memory", "sourceRecord": record["sourceRecord"]}, indent=2))
        return 1
    title = args.title or Path(source_rel).stem.replace("-", " ").replace("_", " ").strip().title() + " OCR"
    area = args.area
    if area not in CAPTURE_AREAS:
        print(json.dumps({"ok": False, "error": f"unknown memory area: {area}"}, indent=2))
        return 1
    lifecycle = args.lifecycle
    if lifecycle not in {"captured", "active", "needs_review", "superseded", "legacy"}:
        print(json.dumps({"ok": False, "error": f"unknown lifecycle: {lifecycle}"}, indent=2))
        return 1
    extraction = {
        "ok": True,
        "mode": "ocr_sidecar",
        "extractor": ocr_extractor_name(sidecar_payload),
        "text": text,
        "textHash": sha256_text(text),
        "truncated": False,
        "warnings": ["OCR text is needs-review evidence and may contain extraction errors."],
    }
    result = {
        "ok": True,
        "sourcePath": source_rel,
        "inputSidecarPath": rel(project, sidecar_path),
        "sidecarPath": rel(project, target),
        "textHash": extraction["textHash"],
        "textChars": len(text),
        "importSource": bool(args.import_source),
        "dryRun": bool(args.dry_run),
    }
    if args.dry_run:
        print(json.dumps(result, indent=2))
        return 0
    category = ""
    memory_path = None
    if args.import_source:
        try:
            category = validate_category(project, args.category) if args.category else ""
            memory_path = unique_memory_entry_path(project, area, title)
        except (OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(sidecar_payload)
    normalized.setdefault("schemaVersion", OCR_SIDECAR_SCHEMA_VERSION)
    normalized.setdefault("sourcePath", source_rel)
    normalized.setdefault("sourceHash", record.get("contentHash", ""))
    normalized["importedAt"] = utc_iso()
    write_json(target, normalized)

    if args.import_source:
        assert memory_path is not None
        content = imported_entry_content(title, area, lifecycle, record, args.summary, category, extraction)
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(content, encoding="utf-8")
        imported_hash = sha256_text(content)
    else:
        imported_hash = ""

    def complete_ocr_import(_current: dict, current_record: dict) -> dict:
        if current_record.get("scanStatus") == "missing":
            raise ReviewQueueError("cannot import OCR for a missing file")
        if str(current_record.get("kind", "")) not in OCR_SOURCE_KINDS:
            raise ReviewQueueError("OCR sidecars are accepted only for pdf, image, or office sources")
        if args.import_source and current_record.get("sourceRecord") and not args.force:
            raise ReviewQueueError("source already has imported memory")
        current_record["ocrSidecar"] = rel(project, target)
        current_record["ocrImportedAt"] = normalized["importedAt"]
        current_record["ocrTextHash"] = extraction["textHash"]
        if args.import_source:
            assert memory_path is not None
            current_record["reviewStatus"] = "imported"
            current_record["sourceRecord"] = rel(project, memory_path)
            current_record["importMode"] = "ocr_sidecar"
            current_record["importedHash"] = imported_hash
        return current_record

    try:
        record = mutate_current_scan_record(
            project,
            source_rel,
            expected_content_hash=expected_content_hash,
            operation="ocr import-sidecar",
            mutate=complete_ocr_import,
        )
    except ReviewQueueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1

    if args.import_source:
        append_source_ledger_record(project, {
            "importedAt": utc_iso(),
            "originalPath": source_rel,
            "importedMemoryPath": rel(project, memory_path),
            "originalHash": record.get("contentHash", ""),
            "textHash": extraction["textHash"],
            "importedHash": imported_hash,
            "extractor": extraction["extractor"],
            "importMode": "ocr_sidecar",
            "reviewStatus": "imported",
            "lifecycle": lifecycle,
            "category": category,
            "sidecarPath": rel(project, target),
            "warnings": extraction["warnings"],
        })
        result["memoryPath"] = rel(project, memory_path)
        result["ledgerPath"] = SOURCE_LEDGER_PATH.as_posix()
    print(json.dumps(result, indent=2))
    return 0


def read_promoted_excerpt(source: Path, record: dict, include_excerpt: bool) -> str:
    if not include_excerpt or record.get("kind") not in SCAN_TEXT_KINDS:
        return ""
    try:
        text = source.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if not text:
        return ""
    excerpt = text[:SCAN_PROMOTION_EXCERPT_CHARS]
    if len(text) > SCAN_PROMOTION_EXCERPT_CHARS:
        excerpt += "\n\n[Excerpt truncated.]"
    return excerpt


def promoted_entry_content(
    title: str,
    area: str,
    lifecycle: str,
    record: dict,
    summary: str,
    category: str,
    excerpt: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- **Area**: {area}",
        f"- **Lifecycle**: {lifecycle}",
        f"- **Captured**: {utc_stamp()}",
        f"- **Source**: {record.get('path', '')}",
        f"- **Source Hash**: {record.get('contentHash', '')}",
        f"- **Source Kind**: {record.get('kind', '')}",
        f"- **Source Size**: {record.get('sizeBytes', 0)}",
        f"- **Source Modified**: {record.get('modifiedAt', '')}",
        f"- **Source Sensitivity**: {record.get('sensitivity', 'unknown')}",
        f"- **Review Status**: {record.get('reviewStatus', '')}",
    ]
    if category:
        lines.append(f"- **Category**: {category}")
    lines.extend([
        "",
        "## Provenance",
        "",
        f"- **Scan Path**: {record.get('path', '')}",
        f"- **Scan Status**: {record.get('scanStatus', '')}",
        f"- **Index Path**: {FILE_INDEX_PATH.as_posix()}",
        "",
        "## Body",
        "",
    ])
    if summary.strip():
        lines.append(summary.strip())
    else:
        lines.append("Promoted file evidence. Review the source file before treating it as final truth.")
    if excerpt:
        lines.extend(["", "## Source Excerpt", "", "```text", excerpt, "```"])
    lines.append("")
    return "\n".join(lines)


def cmd_scan_promote(args) -> int:
    project = args.project_root.resolve()
    payload = read_file_index(project)
    if not payload:
        print(json.dumps({"ok": False, "error": "file index not found; run scan first"}, indent=2))
        return 1
    target_path = str(args.path).strip().replace("\\", "/")
    record, _index = scan_record_by_path(payload, target_path)
    if not record:
        print(json.dumps({"ok": False, "error": f"path is not in file index: {target_path}"}, indent=2))
        return 1
    expected_content_hash = str(record.get("contentHash", ""))
    force = bool(getattr(args, "force", False))
    force_note = str(getattr(args, "force_note", "") or "").strip()
    review_flags = scan_review_flags(project, payload).get(target_path, set())
    blockers = scan_promotion_blockers(project, payload, record, review_flags)
    warnings = scan_promotion_warnings(project, record)
    if record.get("scanStatus") == "missing":
        print(json.dumps({"ok": False, "error": "cannot promote a missing file"}, indent=2))
        return 1
    already_promoted = bool(record.get("sourceRecord"))
    if already_promoted and not force:
        print(json.dumps({"ok": False, "error": "file is already promoted", "sourceRecord": record["sourceRecord"]}, indent=2))
        return 1
    review_status = str(record.get("reviewStatus", "unreviewed"))
    non_promotable_review = review_status not in SCAN_PROMOTABLE_REVIEW_STATUSES
    if non_promotable_review and not force:
        print(json.dumps({
            "ok": False,
            "error": "file must be reviewed or marked needs_import before promotion",
            "reviewStatus": review_status,
        }, indent=2))
        return 1
    area = args.area
    if area not in CAPTURE_AREAS:
        print(json.dumps({"ok": False, "error": f"unknown memory area: {area}"}, indent=2))
        return 1
    lifecycle = args.lifecycle
    if lifecycle not in {"captured", "active", "needs_review", "superseded", "legacy"}:
        print(json.dumps({"ok": False, "error": f"unknown lifecycle: {lifecycle}"}, indent=2))
        return 1
    try:
        category = validate_category(project, args.category) if args.category else ""
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1

    duplicates = memory_entries_with_source_hash(project, str(record.get("contentHash", "")))
    if blockers and not force:
        print(json.dumps({
            "ok": False,
            "error": "promotion blocked by readiness gate",
            "path": target_path,
            "reviewStatus": review_status,
            "reviewFlags": sorted(review_flags),
            "promotionBlockers": blockers,
            "promotionWarnings": warnings,
        }, indent=2))
        return 1
    if duplicates and not force:
        print(json.dumps({"ok": False, "error": "source hash is already promoted", "duplicates": duplicates}, indent=2))
        return 1
    force_reasons = []
    if non_promotable_review:
        force_reasons.append(readiness_issue("non_promotable_review_status", "high", f"Review status is {review_status}."))
    if already_promoted:
        force_reasons.append(readiness_issue("already_promoted", "high", f"Existing source record: {record.get('sourceRecord', '')}."))
    if duplicates:
        force_reasons.append(readiness_issue("already_promoted_hash", "high", "Source hash is already promoted in memory."))
    force_reasons.extend(blockers)
    if force_reasons and force and not force_note:
        print(json.dumps({
            "ok": False,
            "error": "--force-note is required when overriding promotion blockers or duplicate promotion checks",
            "path": target_path,
            "forceReasons": force_reasons,
        }, indent=2))
        return 1

    source = (project / target_path).resolve()
    try:
        source.relative_to(project)
    except ValueError:
        print(json.dumps({"ok": False, "error": "source path is outside project root"}, indent=2))
        return 1
    if not source.exists() or not source.is_file():
        print(json.dumps({"ok": False, "error": "source file does not exist"}, indent=2))
        return 1

    title = args.title or Path(target_path).stem.replace("-", " ").replace("_", " ").strip().title() or "Promoted Source"
    try:
        memory_path = unique_memory_entry_path(project, area, title)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    content = promoted_entry_content(
        title=title,
        area=area,
        lifecycle=lifecycle,
        record=record,
        summary=str(args.summary or ""),
        category=category,
        excerpt=read_promoted_excerpt(source, record, bool(args.include_excerpt)),
    )
    memory_path.write_text(content, encoding="utf-8")

    def complete_promotion(current: dict, current_record: dict) -> dict:
        if current_record.get("scanStatus") == "missing":
            raise ReviewQueueError("cannot promote a missing file")
        if current_record.get("sourceRecord") and not force:
            raise ReviewQueueError("file is already promoted")
        current_review_status = str(current_record.get("reviewStatus", "unreviewed"))
        current_non_promotable = current_review_status not in SCAN_PROMOTABLE_REVIEW_STATUSES
        if current_non_promotable and not force:
            raise ReviewQueueError("file must be reviewed or marked needs_import before promotion")
        current_flags = scan_review_flags(project, current).get(target_path, set())
        current_blockers = scan_promotion_blockers(project, current, current_record, current_flags)
        if current_blockers and not force:
            raise ReviewQueueError("promotion blocked by readiness gate")
        current_force_reasons = []
        if current_non_promotable:
            current_force_reasons.append(readiness_issue(
                "non_promotable_review_status",
                "high",
                f"Review status is {current_review_status}.",
            ))
        if current_record.get("sourceRecord"):
            current_force_reasons.append(readiness_issue(
                "already_promoted",
                "high",
                f"Existing source record: {current_record.get('sourceRecord', '')}.",
            ))
        if duplicates:
            current_force_reasons.append(readiness_issue(
                "already_promoted_hash",
                "high",
                "Source hash is already promoted in memory.",
            ))
        current_force_reasons.extend(current_blockers)
        if current_force_reasons and force and not force_note:
            raise ReviewQueueError("--force-note is required when overriding promotion blockers or duplicate promotion checks")
        current_record["reviewStatus"] = "promoted"
        current_record["sourceRecord"] = rel(project, memory_path)
        current_record["promotedAt"] = utc_iso()
        current_record["promotionArea"] = area
        current_record["promotionWarnings"] = scan_promotion_warnings(project, current_record)
        if current_force_reasons:
            current_record["promotionForceNote"] = force_note
            current_record["promotionForceReasons"] = current_force_reasons
        else:
            current_record.pop("promotionForceNote", None)
            current_record.pop("promotionForceReasons", None)
        return {
            "record": current_record,
            "warnings": current_record["promotionWarnings"],
            "forceReasons": current_force_reasons,
        }

    try:
        promotion = mutate_current_scan_record(
            project,
            target_path,
            expected_content_hash=expected_content_hash,
            operation="scan-promote",
            mutate=complete_promotion,
        )
    except ReviewQueueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    record = promotion["record"]
    warnings = promotion["warnings"]
    force_reasons = promotion["forceReasons"]
    print(json.dumps({
        "ok": True,
        "path": target_path,
        "memoryPath": rel(project, memory_path),
        "indexPath": rel(project, file_index_path(project)),
        "reviewStatus": record["reviewStatus"],
        "promotionWarnings": warnings,
        "forced": bool(force_reasons),
    }, indent=2))
    return 0


def mcp_tools() -> list[dict]:
    return [
        {"name": "controlwork_status", "mode": "read-only", "description": "Return project status and memory counts."},
        {"name": "controlwork_list_entries", "mode": "read-only", "description": "List memory entries."},
        {"name": "controlwork_read_entry", "mode": "read-only", "description": "Read a project-relative memory file."},
        {"name": "controlwork_search_memory", "mode": "read-only", "description": "Search memory entries."},
        {"name": "controlwork_list_categories", "mode": "read-only", "description": "List category registry entries."},
        {"name": "controlwork_context_pack", "mode": "read-only", "description": "Build a scoped context packet without writing it."},
    ]


def cmd_mcp_tools(args) -> int:
    print(json.dumps({"ok": True, "tools": mcp_tools(), "server": "scripts/controlwork_mcp.py"}, indent=2))
    return 0


def cmd_mcp_call(args) -> int:
    project = args.project_root.resolve()
    params = read_json(args.args_file) if args.args_file else {}
    if args.tool == "controlwork_status":
        result = feature_status_payload(project)
    elif args.tool == "controlwork_list_entries":
        entries = iter_entries(project, area=str(params.get("area", "")))
        lifecycle = str(params.get("lifecycle", ""))
        if lifecycle:
            entries = [entry for entry in entries if entry.get("lifecycle") == lifecycle]
        result = {"entries": entries}
    elif args.tool == "controlwork_read_entry":
        result = controlwork_read_entry_payload(project, str(params.get("path", "")))
        if not result.get("ok"):
            print(json.dumps(result, indent=2))
            return 1
    elif args.tool == "controlwork_search_memory":
        result = {"matches": search_memory(project, str(params.get("query", "")), str(params.get("area", "")), int(params.get("limit", 20)))}
    elif args.tool == "controlwork_list_categories":
        result = {"categories": read_category_registry(project).get("categories", [])}
    elif args.tool == "controlwork_context_pack":
        result = {
            "packet": build_context_pack(
                project,
                scope=str(params.get("scope", "general")),
                topic=str(params.get("topic", "")),
                limit=int(params.get("limit", 10)),
                include_legacy=bool(params.get("include_legacy", False)),
                refresh_views=False,
            )
        }
    else:
        print(json.dumps({"ok": False, "error": f"unknown tool: {args.tool}"}, indent=2))
        return 1
    print(json.dumps({"ok": True, "tool": args.tool, "result": result}, indent=2))
    return 0

def register_feature_parsers(sub) -> None:
    p_scan = sub.add_parser("scan", help="Scan project files into a governed file index")
    p_scan.add_argument("--project-root", type=Path, default=Path.cwd())
    p_scan.add_argument("--json", action="store_true", dest="json_output")
    p_scan.set_defaults(func=cmd_scan)

    p_scan_analyze = sub.add_parser("scan-analyze", help="Analyze scan index duplicates, versions, and conflicts")
    p_scan_analyze.add_argument("--project-root", type=Path, default=Path.cwd())
    p_scan_analyze.add_argument("--limit", type=int, default=50)
    p_scan_analyze.add_argument("--json", action="store_true", dest="json_output")
    p_scan_analyze.set_defaults(func=cmd_scan_analyze)

    p_scan_review = sub.add_parser("scan-review", help="Review or summarize scan index records")
    p_scan_review.add_argument("path", nargs="?", default="")
    p_scan_review.add_argument("--project-root", type=Path, default=Path.cwd())
    p_scan_review.add_argument("--review-status", choices=SCAN_REVIEW_STATUSES, default="")
    p_scan_review.add_argument("--sensitivity", choices=SCAN_SENSITIVITY_LEVELS, default="")
    p_scan_review.add_argument("--note", default="")
    p_scan_review.add_argument("--filter", choices=SCAN_REVIEW_FILTERS, default="pending")
    p_scan_review.add_argument("--batch", action="store_true")
    p_scan_review.add_argument("--item", default="")
    p_scan_review.add_argument("--action", default="")
    p_scan_review.add_argument("--canonical", default="")
    p_scan_review.add_argument("--fingerprint", default="")
    p_scan_review.add_argument("--proposal", action="store_true")
    p_scan_review.add_argument("--output", type=Path, default=None)
    p_scan_review.add_argument("--limit", type=int, default=20)
    p_scan_review.add_argument("--json", action="store_true", dest="json_output")
    p_scan_review.set_defaults(func=cmd_scan_review)

    p_scan_import = sub.add_parser("scan-import", help="Import one scanned source into governed memory as needs-review evidence")
    p_scan_import.add_argument("path")
    p_scan_import.add_argument("--project-root", type=Path, default=Path.cwd())
    p_scan_import.add_argument("--area", choices=CAPTURE_AREAS, default="sources")
    p_scan_import.add_argument("--title", default="")
    p_scan_import.add_argument("--summary", default="")
    p_scan_import.add_argument("--lifecycle", choices=["active", "captured", "legacy", "needs_review", "superseded"], default="needs_review")
    p_scan_import.add_argument("--category", default="")
    p_scan_import.add_argument("--max-chars", type=int, default=SCAN_IMPORT_EXTRACT_CHARS)
    p_scan_import.add_argument("--as-reference", action="store_true")
    p_scan_import.add_argument("--dry-run", action="store_true")
    p_scan_import.add_argument("--proposal", action="store_true")
    p_scan_import.add_argument("--output", type=Path, default=None)
    p_scan_import.add_argument("--force", action="store_true")
    p_scan_import.add_argument("--json", action="store_true", dest="json_output")
    p_scan_import.set_defaults(func=cmd_scan_import)

    p_ocr = sub.add_parser("ocr", help="Manage OCR sidecars for scanned binary sources")
    ocr_sub = p_ocr.add_subparsers(dest="ocr_command")
    p_ocr_status = ocr_sub.add_parser("status", help="Show OCR sidecar status and pending binary sources")
    p_ocr_status.add_argument("--project-root", type=Path, default=Path.cwd())
    p_ocr_status.add_argument("--limit", type=int, default=20)
    p_ocr_status.set_defaults(func=cmd_ocr_status)
    p_ocr_run = ocr_sub.add_parser("run", help="Report OCR adapter requirements for one source")
    p_ocr_run.add_argument("source")
    p_ocr_run.add_argument("--project-root", type=Path, default=Path.cwd())
    p_ocr_run.add_argument("--output", type=Path, default=None)
    p_ocr_run.add_argument("--force", action="store_true")
    p_ocr_run.set_defaults(func=cmd_ocr_run)
    p_ocr_import = ocr_sub.add_parser("import-sidecar", help="Import a project-owned OCR sidecar as needs-review evidence")
    p_ocr_import.add_argument("source")
    p_ocr_import.add_argument("--sidecar", type=Path, required=True)
    p_ocr_import.add_argument("--project-root", type=Path, default=Path.cwd())
    p_ocr_import.add_argument("--area", choices=CAPTURE_AREAS, default="sources")
    p_ocr_import.add_argument("--title", default="")
    p_ocr_import.add_argument("--summary", default="")
    p_ocr_import.add_argument("--lifecycle", choices=["active", "captured", "legacy", "needs_review", "superseded"], default="needs_review")
    p_ocr_import.add_argument("--category", default="")
    p_ocr_import.add_argument("--import-source", action="store_true")
    p_ocr_import.add_argument("--dry-run", action="store_true")
    p_ocr_import.add_argument("--force", action="store_true")
    p_ocr_import.add_argument("--json", action="store_true", dest="json_output")
    p_ocr_import.set_defaults(func=cmd_ocr_import_sidecar)

    p_scan_promote = sub.add_parser("scan-promote", help="Promote one reviewed scan record into memory")
    p_scan_promote.add_argument("path")
    p_scan_promote.add_argument("--project-root", type=Path, default=Path.cwd())
    p_scan_promote.add_argument("--area", choices=CAPTURE_AREAS, default="sources")
    p_scan_promote.add_argument("--title", default="")
    p_scan_promote.add_argument("--summary", default="")
    p_scan_promote.add_argument("--lifecycle", choices=["active", "captured", "legacy", "needs_review", "superseded"], default="captured")
    p_scan_promote.add_argument("--category", default="")
    p_scan_promote.add_argument("--include-excerpt", action="store_true")
    p_scan_promote.add_argument("--force", action="store_true")
    p_scan_promote.add_argument("--force-note", default="")
    p_scan_promote.add_argument("--json", action="store_true", dest="json_output")
    p_scan_promote.set_defaults(func=cmd_scan_promote)

    p_category = sub.add_parser("category", help="Manage custom categories")
    category_sub = p_category.add_subparsers(dest="category_command")
    p_list = category_sub.add_parser("list", help="List categories")
    p_list.add_argument("--project-root", type=Path, default=Path.cwd())
    p_list.add_argument("--status", choices=["approved", "proposed"], default="")
    p_list.set_defaults(func=cmd_category_list)
    p_propose = category_sub.add_parser("propose", help="Propose a category for review")
    p_propose.add_argument("name")
    p_propose.add_argument("--project-root", type=Path, default=Path.cwd())
    p_propose.add_argument("--area", choices=CAPTURE_AREAS, default="notes")
    p_propose.add_argument("--description", default="")
    p_propose.set_defaults(func=cmd_category_propose)
    p_add = category_sub.add_parser("add", help="Add an approved category")
    p_add.add_argument("name")
    p_add.add_argument("--project-root", type=Path, default=Path.cwd())
    p_add.add_argument("--area", choices=CAPTURE_AREAS, default="notes")
    p_add.add_argument("--description", default="")
    p_add.set_defaults(func=cmd_category_add)
    p_approve = category_sub.add_parser("approve", help="Approve a proposed category")
    p_approve.add_argument("category")
    p_approve.add_argument("--project-root", type=Path, default=Path.cwd())
    p_approve.set_defaults(func=cmd_category_approve)

    p_checkpoint = sub.add_parser("checkpoint", help="Create a Work Checkpoint receipt")
    p_checkpoint.add_argument("--project-root", type=Path, default=Path.cwd())
    p_checkpoint.add_argument("--title", default="")
    p_checkpoint.add_argument(
        "--reason",
        choices=CHECKPOINT_REASONS,
        default="manual",
        help="Why the checkpoint is being created",
    )
    p_checkpoint.add_argument(
        "--note",
        default="",
        help="Optional note to preserve chat state, handoff details, or compaction context",
    )
    p_checkpoint.add_argument(
        "--auto",
        action="store_true",
        help="Create only when durable memory changed, except for handoff/session/context-compaction reasons",
    )
    p_checkpoint.add_argument(
        "--check",
        action="store_true",
        help="Report whether a checkpoint is due without writing one",
    )
    p_checkpoint.set_defaults(func=cmd_checkpoint)

    p_handoff = sub.add_parser("handoff", help="Generate a handoff packet")
    p_handoff.add_argument("--project-root", type=Path, default=Path.cwd())
    p_handoff.add_argument("--output", type=Path, default=None)
    p_handoff.set_defaults(func=cmd_handoff)

    p_dashboard = sub.add_parser("dashboard", help="Generate a static Project Plane dashboard")
    p_dashboard.add_argument("--project-root", type=Path, default=Path.cwd())
    p_dashboard.add_argument("--format", choices=["html", "md", "json"], default="html")
    p_dashboard.add_argument("--output", type=Path, default=None)
    p_dashboard.add_argument("--limit", type=int, default=20)
    p_dashboard.add_argument("--scan-limit", type=int, default=50)
    p_dashboard.add_argument("--no-refresh-scan", action="store_true")
    p_dashboard.set_defaults(func=cmd_dashboard)

    p_context_pack = sub.add_parser("context-pack", help="Build a scoped AI context packet")
    p_context_pack.add_argument("--project-root", type=Path, default=Path.cwd())
    p_context_pack.add_argument("--scope", choices=CONTEXT_SCOPES, default="general")
    p_context_pack.add_argument("--topic", default="")
    p_context_pack.add_argument("--limit", type=int, default=10)
    p_context_pack.add_argument("--include-legacy", action="store_true")
    p_context_pack.add_argument("--output", type=Path, default=None)
    p_context_pack.add_argument("--stdout", action="store_true")
    p_context_pack.set_defaults(func=cmd_context_pack)

    p_session = sub.add_parser("session", help="Manage portable session records")
    session_sub = p_session.add_subparsers(dest="session_command")
    p_session_start = session_sub.add_parser("start", help="Start a portable session record")
    p_session_start.add_argument("--project-root", type=Path, default=Path.cwd())
    p_session_start.add_argument("--topic", required=True)
    p_session_start.add_argument("--mode", choices=SESSION_MODES, default="continue_previous_work")
    p_session_start.add_argument("--operator", default="manual")
    p_session_start.add_argument("--id", dest="session_id", default="")
    p_session_start.add_argument("--summary", default="")
    p_session_start.add_argument("--category", action="append", default=[])
    p_session_start.set_defaults(func=cmd_session_start)
    p_session_close = session_sub.add_parser("close", help="Close a portable session record")
    p_session_close.add_argument("session_id")
    p_session_close.add_argument("--project-root", type=Path, default=Path.cwd())
    p_session_close.add_argument("--status", choices=[item for item in SESSION_STATUSES if item != "active"], default="completed")
    p_session_close.add_argument("--summary", default="")
    p_session_close.add_argument("--followup", action="append", default=[])
    p_session_close.add_argument("--decision", action="append", default=[])
    p_session_close.set_defaults(func=cmd_session_close)
    p_session_link = session_sub.add_parser("link", help="Link a session to portable memory evidence")
    p_session_link.add_argument("session_id")
    p_session_link.add_argument("--project-root", type=Path, default=Path.cwd())
    p_session_link.add_argument("--type", choices=SESSION_EDGE_TYPES, required=True)
    p_session_link.add_argument("--target", required=True)
    p_session_link.add_argument("--target-type", default="")
    p_session_link.set_defaults(func=cmd_session_link)
    p_session_note = session_sub.add_parser("note", help="Record a portable session note")
    p_session_note.add_argument("session_id")
    p_session_note.add_argument("text")
    p_session_note.add_argument("--project-root", type=Path, default=Path.cwd())
    p_session_note.add_argument("--kind", choices=["note", "decision", "followup"], default="note")
    p_session_note.set_defaults(func=cmd_session_note)
    p_session_list = session_sub.add_parser("list", help="List portable session records")
    p_session_list.add_argument("--project-root", type=Path, default=Path.cwd())
    p_session_list.add_argument("--status", choices=["all", *SESSION_STATUSES], default="all")
    p_session_list.add_argument("--topic", default="")
    p_session_list.add_argument("--limit", type=int, default=20)
    p_session_list.set_defaults(func=cmd_session_list)
    p_session_show = session_sub.add_parser("show", help="Show one portable session record")
    p_session_show.add_argument("session_id")
    p_session_show.add_argument("--project-root", type=Path, default=Path.cwd())
    p_session_show.set_defaults(func=cmd_session_show)
    p_obsidian = sub.add_parser("obsidian", help="Generate or check an Obsidian wiki projection")
    obsidian_sub = p_obsidian.add_subparsers(dest="obsidian_command")
    for name, func, help_text in (
        ("init", cmd_obsidian_init, "Initialize Obsidian settings and wiki projection"),
        ("sync", cmd_obsidian_sync, "Regenerate the Obsidian wiki projection"),
        ("check", cmd_obsidian_check, "Check Obsidian projection drift"),
    ):
        parser_obj = obsidian_sub.add_parser(name, help=help_text)
        parser_obj.add_argument("--project-root", type=Path, default=Path.cwd())
        parser_obj.set_defaults(func=func)

    p_wiki = sub.add_parser("wiki", help="Manage generated wiki projection")
    wiki_sub = p_wiki.add_subparsers(dest="wiki_command")
    p_wiki_build = wiki_sub.add_parser("build", help="Build wiki projection")
    p_wiki_build.add_argument("--project-root", type=Path, default=Path.cwd())
    p_wiki_build.set_defaults(func=cmd_wiki_build)
    p_wiki_import = wiki_sub.add_parser("import-edits", help="Prepare review proposals from edited wiki pages")
    p_wiki_import.add_argument("--project-root", type=Path, default=Path.cwd())
    p_wiki_import.add_argument("--review", action="store_true")
    p_wiki_import.set_defaults(func=cmd_wiki_import_edits)

    p_graph = sub.add_parser("graph", help="Inspect portable Project Plane graph")
    graph_sub = p_graph.add_subparsers(dest="graph_command")
    p_graph_status = graph_sub.add_parser("status", help="Show graph contract and counts")
    p_graph_status.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_status.set_defaults(func=cmd_graph_status)
    p_graph_suggestions = graph_sub.add_parser("suggestions", help="List deterministic topic suggestions")
    p_graph_suggestions.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_suggestions.add_argument("--status", choices=["all", "suggested", "accepted", "rejected"], default="suggested")
    p_graph_suggestions.add_argument("--confidence", choices=PORTABLE_CONFIDENCE_LEVELS, default="")
    p_graph_suggestions.add_argument("--limit", type=int, default=50)
    p_graph_suggestions.set_defaults(func=cmd_graph_suggestions)
    p_graph_accept = graph_sub.add_parser("accept", help="Accept a deterministic graph suggestion")
    p_graph_accept.add_argument("suggestion_id")
    p_graph_accept.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_accept.add_argument("--reason", required=True)
    p_graph_accept.set_defaults(func=cmd_graph_accept)
    p_graph_reject = graph_sub.add_parser("reject", help="Reject a deterministic graph suggestion")
    p_graph_reject.add_argument("suggestion_id")
    p_graph_reject.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_reject.add_argument("--reason", required=True)
    p_graph_reject.set_defaults(func=cmd_graph_reject)
    p_graph_explain = graph_sub.add_parser("explain", help="Explain a graph node, edge, or suggestion")
    p_graph_explain.add_argument("selector")
    p_graph_explain.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_explain.add_argument("--limit", type=int, default=10)
    p_graph_explain.set_defaults(func=cmd_graph_explain)
    p_graph_path = graph_sub.add_parser("path", help="Find an evidence path between two graph nodes")
    p_graph_path.add_argument("source_selector")
    p_graph_path.add_argument("target_selector")
    p_graph_path.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_path.add_argument("--max-depth", type=int, default=3)
    p_graph_path.add_argument("--include-suggestions", action="store_true")
    p_graph_path.set_defaults(func=cmd_graph_path)
    p_graph_neighbors = graph_sub.add_parser("neighbors", help="List graph neighbors for one node")
    p_graph_neighbors.add_argument("selector")
    p_graph_neighbors.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_neighbors.add_argument("--limit", type=int, default=20)
    p_graph_neighbors.add_argument("--include-suggestions", action="store_true")
    p_graph_neighbors.add_argument("--include-chunks", action="store_true")
    p_graph_neighbors.set_defaults(func=cmd_graph_neighbors)
    p_graph_stale = graph_sub.add_parser("stale", help="List non-current graph records and stale scan attention")
    p_graph_stale.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_stale.add_argument("--limit", type=int, default=50)
    p_graph_stale.add_argument("--no-scan", action="store_true")
    p_graph_stale.set_defaults(func=cmd_graph_stale)
    p_graph_unresolved = graph_sub.add_parser("unresolved", help="List unresolved graph records and pending scan review")
    p_graph_unresolved.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_unresolved.add_argument("--limit", type=int, default=50)
    p_graph_unresolved.add_argument("--no-scan", action="store_true")
    p_graph_unresolved.set_defaults(func=cmd_graph_unresolved)
    p_graph_diff = graph_sub.add_parser("diff", help="Compare current graph projection with an exported baseline")
    p_graph_diff.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_diff.add_argument("--baseline", type=Path, required=True)
    p_graph_diff.add_argument("--limit", type=int, default=100)
    p_graph_diff.set_defaults(func=cmd_graph_diff)
    p_graph_export = graph_sub.add_parser("export", help="Export a derived graph projection")
    p_graph_export.add_argument("--project-root", type=Path, default=Path.cwd())
    p_graph_export.add_argument("--format", choices=["json", "html"], default="json")
    p_graph_export.add_argument("--output", type=Path, required=True)
    p_graph_export.add_argument("--type", action="append", default=[])
    p_graph_export.add_argument("--lifecycle", action="append", default=[])
    p_graph_export.add_argument("--confidence", choices=PORTABLE_CONFIDENCE_LEVELS, default="")
    p_graph_export.add_argument("--source", default="")
    p_graph_export.set_defaults(func=cmd_graph_export)

    p_query = sub.add_parser("query", help="Run query-first retrieval with GraphRAG and graph explain hints")
    p_query.add_argument("query")
    p_query.add_argument("--project-root", type=Path, default=Path.cwd())
    p_query.add_argument("--limit", type=int, default=10)
    p_query.add_argument("--include-legacy", action="store_true")
    p_query.add_argument("--path-to", default="")
    p_query.add_argument("--max-depth", type=int, default=3)
    p_query.add_argument("--include-suggestions", action="store_true")
    p_query.add_argument("--output", type=Path, default=None)
    p_query.add_argument("--stdout", action="store_true")
    p_query.set_defaults(func=cmd_query, command_surface="standalone")

    p_retrieve = sub.add_parser("retrieve", help="Retrieve Project Plane memory with reasons")
    p_retrieve.add_argument("query")
    p_retrieve.add_argument("--project-root", type=Path, default=Path.cwd())
    p_retrieve.add_argument("--limit", type=int, default=10)
    p_retrieve.add_argument("--include-legacy", action="store_true")
    p_retrieve.set_defaults(func=cmd_retrieve)

    p_rag_pack = sub.add_parser("rag-pack", help="Build a portable Project Plane GraphRAG packet")
    p_rag_pack.add_argument("query")
    p_rag_pack.add_argument("--project-root", type=Path, default=Path.cwd())
    p_rag_pack.add_argument("--limit", type=int, default=10)
    p_rag_pack.add_argument("--include-legacy", action="store_true")
    p_rag_pack.add_argument("--output", type=Path, default=None)
    p_rag_pack.add_argument("--stdout", action="store_true")
    p_rag_pack.set_defaults(func=cmd_rag_pack)


    p_mcp = sub.add_parser("mcp", help="Inspect read-only MCP tool contract")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_command")
    p_mcp_tools = mcp_sub.add_parser("tools", help="List ControlWork MCP tools")
    p_mcp_tools.add_argument("--project-root", type=Path, default=Path.cwd())
    p_mcp_tools.set_defaults(func=cmd_mcp_tools)
    p_mcp_call = mcp_sub.add_parser("call", help="Call a read-only ControlWork tool locally")
    p_mcp_call.add_argument("tool")
    p_mcp_call.add_argument("--project-root", type=Path, default=Path.cwd())
    p_mcp_call.add_argument("--args-file", type=Path, default=None)
    p_mcp_call.set_defaults(func=cmd_mcp_call)
