"""Public command implementations for the Project Memory Engine."""

from __future__ import annotations

import json
import re
import hashlib
import os
from argparse import Namespace
import shutil
import sqlite3
import stat
from pathlib import Path, PurePosixPath

from . import freshness_projection, work_features
from .chunks import _sync_semantic_chunks
from .classifier import _classify_document
from .correlation import _refresh_correlation_suggestions
from .entities import _find_entity_by_path, _record_entity, _row_to_dict
from .ids import _project_short, _unique_entity_id
from .impact import _group_impact, _impact_entities
from .ledger import _insert_event, _upsert_source
from .lifecycle import (
    _find_entity_for_lifecycle,
    _normalize_entity_data_for_lifecycle,
    diagnose_pytest_temp_dirs,
    format_pytest_temp_warning,
)
from .migrations import inspect_schema
from .scanner import (
    _area_from_relpath,
    _classify_path,
    _is_governed_relpath,
    _iter_scannable_files,
    _safe_read_text_with_metadata,
    _title_from_file,
)
from .schema import (
    CONTROLCODING_VERSION,
    CONTROL_DIRNAME,
    DB_FILENAME,
    INSTALL_MODE_STORAGE,
    MANIFEST_FILENAME,
    MEMORY_DIRNAME,
    REQUIRED_LOGS,
    REQUIRED_VIEWS,
    SCHEMA_VERSION,
    VALID_LIFECYCLES,
    VALID_INSTALL_MODES,
    VALID_MEMORY_PROFILES,
    WORK_MEMORY_PROFILE_DIRS,
)
from .store import (
    _content_hash,
    _db_path,
    _ensure_schema,
    _json_dumps,
    _logs_dir,
    _manifest_path,
    _memory_db_error_payload,
    _memory_db_error_text,
    _memory_connection,
    _memory_dir,
    _now_iso,
    _physical_file_path,
    _print_json_error_or_text,
    _print_json_or_text,
    _read_json,
    _readonly_memory_connection,
    _relative_path,
    _require_initialized,
    _set_metadata,
    _today_yyyymmdd,
    _transactional_ensure_directory,
    _transactional_ensure_layout,
    _transactional_move_file,
    _transactional_write_json,
    _transactional_write_text,
    _validate_jsonl,
    _views_dir,
    _write_json,
)
from .retrieve import _rebuild_retrieval_index
from .vector import _rebuild_vector_index
from .views import _generate_views, _generate_views_in_transaction

CHUNKABLE_DOCUMENT_EXTENSIONS = {".adoc", ".md", ".pdf", ".rst", ".txt"}
LAYOUT_EXTRACTION_SIDECAR_SUFFIXES = (
    ".layout.json",
    ".ocr.json",
    ".pdf.layout.json",
    ".pdf.ocr.json",
)

INTAKE_PROMOTION_TARGET_DIRS = {
    "source": "docs/sources",
    "sources": "docs/sources",
    "extract": "docs/extracts",
    "extracts": "docs/extracts",
    "research": "docs/research",
    "idea": "docs/ideas",
    "ideas": "docs/ideas",
    "decision": "docs/decisions",
    "decisions": "docs/decisions",
    "workflow": "docs/workflows",
    "workflows": "docs/workflows",
    "output": "docs/outputs",
    "outputs": "docs/outputs",
    "archive": "docs/archive",
}
INTAKE_PROMOTION_DEFAULT_LIFECYCLES = {
    "source": "triaged",
    "extract": "triaged",
    "research": "triaged",
    "idea": "triaged",
    "decision": "active",
    "workflow": "active",
    "output": "verified",
    "archive": "archived",
}


def _canonical_intake_target(target: str) -> str:
    normalized = str(target or "").strip().lower()
    if normalized.endswith("s") and normalized[:-1] in INTAKE_PROMOTION_TARGET_DIRS:
        return normalized[:-1]
    return normalized



def _should_chunk_document(path: Path) -> bool:
    return path.suffix.lower() in CHUNKABLE_DOCUMENT_EXTENSIONS


def _slug_for_filename(value: str, fallback: str = "intake") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:80] or fallback


def _unique_project_file(path: Path) -> Path:
    try:
        path.lstat()
    except FileNotFoundError:
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        try:
            candidate.lstat()
        except FileNotFoundError:
            return candidate
    raise OSError(f"could not find available filename near {path}")

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
CONTROLWORK_LIFECYCLES = [
    "captured",
    "active",
    "needs_review",
    "superseded",
    "legacy",
]
CONTROLWORK_PORTABLE_FEATURE_TOKENS = [
    "cc memory work-category",
    "cc memory work-views",
    "cc memory work-checkpoint",
    "cc memory work-handoff",
    "cc memory work-context-pack",
    "cc memory work-obsidian",
    "cc memory work-wiki",
    "cc memory work-mcp",
    "cc memory op-index",
]
CONTROLWORK_SHARED_COMMAND_SURFACE = [
    {"feature": "Initialize Work Plane", "standalone": "cw.py init", "embedded": "cc memory work-init"},
    {"feature": "Status and drift", "standalone": "cw.py status", "embedded": "cc memory work-status"},
    {"feature": "Safe first-pass setup", "standalone": "cw.py quickstart", "embedded": "cc memory work-quickstart"},
    {"feature": "File scan index", "standalone": "cw.py scan", "embedded": "cc memory work-scan"},
    {"feature": "Scan analysis", "standalone": "cw.py scan-analyze", "embedded": "cc memory work-analyze"},
    {"feature": "Scan review gate", "standalone": "cw.py scan-review", "embedded": "cc memory work-review"},
    {"feature": "Rich source import", "standalone": "cw.py scan-import", "embedded": "cc memory work-import-source"},
    {"feature": "OCR sidecar import", "standalone": "cw.py ocr", "embedded": "cc memory work-ocr"},
    {"feature": "Reviewed promotion", "standalone": "cw.py scan-promote", "embedded": "cc memory work-promote"},
    {"feature": "Capture memory", "standalone": "cw.py capture", "embedded": "cc memory work-capture"},
    {"feature": "Categories", "standalone": "cw.py category", "embedded": "cc memory work-category"},
    {"feature": "Views", "standalone": "cw.py views", "embedded": "cc memory work-views"},
    {"feature": "Dashboard and Project Map", "standalone": "cw.py dashboard", "embedded": "cc memory work-dashboard"},
    {"feature": "Checkpoints", "standalone": "cw.py checkpoint", "embedded": "cc memory work-checkpoint"},
    {"feature": "Handoff", "standalone": "cw.py handoff", "embedded": "cc memory work-handoff"},
    {"feature": "Context packet", "standalone": "cw.py context-pack", "embedded": "cc memory work-context-pack"},
    {"feature": "Session continuity", "standalone": "cw.py session", "embedded": "cc memory work-session"},
    {"feature": "Portable graph", "standalone": "cw.py graph", "embedded": "cc memory work-graph"},
    {"feature": "Query-first retrieval", "standalone": "cw.py query", "embedded": "cc memory work-query"},
    {"feature": "Retrieval", "standalone": "cw.py retrieve", "embedded": "cc memory work-retrieve"},
    {"feature": "RAG packet", "standalone": "cw.py rag-pack", "embedded": "cc memory work-rag-pack"},
    {"feature": "Obsidian/wiki projection", "standalone": "cw.py obsidian/wiki", "embedded": "cc memory work-obsidian/work-wiki"},
    {"feature": "MCP inspection", "standalone": "cw.py mcp", "embedded": "cc memory work-mcp"},
]
CONTROLWORK_WORK_PLANE_CONTRACT = {
    "identity": "controlwork-work-plane/1.0.0",
    "profile": "ControlCoding embedded conformance profile",
    "status": "non-canonical",
    "canonicalAuthority": (
        "ControlWork repository, docs/work-plane-compatibility-contract.md"
    ),
}
CONTROLWORK_ACCEPTED_COMMAND_DIVERGENCES = [
    {
        "surface": "embedded_controlcoding",
        "features": [
            "verification receipts",
            "hook and boundary enforcement",
            "code-specific GraphRAG",
            "release doctor integration",
            "agent runs",
            "code impact",
            "Dev Plane",
            "Code Plane",
            "developer graph routing",
            "work-attach",
            "work-import",
            "work-export",
            "work-sync",
            "work-parity",
        ],
        "reason": "These depend on ControlCoding Dev Plane or Code Plane behavior and are not portable Work Plane contract features.",
    },
    {
        "surface": "standalone_controlwork",
        "features": [
            "standalone product docs maintenance",
            "maintenance carousel",
            "host adapter maintenance",
            "install-guide",
        ],
        "reason": "These maintain a standalone ControlWork repository or standalone host adapters and are not required inside embedded project folders.",
    },
]


def _should_chunk_document(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in CHUNKABLE_DOCUMENT_EXTENSIONS
        or any(name.endswith(suffix) for suffix in LAYOUT_EXTRACTION_SIDECAR_SUFFIXES)
    )


def _controlwork_config() -> dict:
    return {
        "schemaVersion": 1,
        "product": "ControlWork",
        "distribution": "embedded_controlcoding",
        "standaloneCompatible": True,
        "canonicalContext": CONTROLWORK_CONTEXT_FILENAME,
        "baseDocument": work_features.PROJECT_BASE_DOCUMENT_FILENAME,
        "project": {
            "baseDocumentPath": work_features.PROJECT_BASE_DOCUMENT_FILENAME,
        },
        "ownerProduct": "ControlCoding",
        "memory": {
            "root": f"{CONTROLWORK_DIRNAME}/{CONTROLWORK_MEMORY_DIRNAME}",
            "areas": CONTROLWORK_MEMORY_AREAS,
            "lifecycles": CONTROLWORK_LIFECYCLES,
        },
        "compatibility": {
            "standaloneRepo": "ControlWork",
            "sharedMemoryContractVersion": 1,
        },
        "features": {
            "categories": True,
            "checkpoints": True,
            "views": True,
            "obsidianProjection": True,
            "mcpReadOnly": True,
            "contextPackets": True,
        },
    }


def _standalone_controlwork_config(exported_from: str = "") -> dict:
    config = {
        "schemaVersion": 1,
        "product": "ControlWork",
        "distribution": "standalone_repo",
        "canonicalContext": CONTROLWORK_CONTEXT_FILENAME,
        "baseDocument": work_features.PROJECT_BASE_DOCUMENT_FILENAME,
        "project": {
            "baseDocumentPath": work_features.PROJECT_BASE_DOCUMENT_FILENAME,
        },
        "hostAdapters": ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "AI_CONTEXT.md"],
        "standalone": True,
        "embeddedCompatible": True,
        "embeddedTargets": ["ControlCoding"],
        "memory": {
            "root": f"{CONTROLWORK_DIRNAME}/{CONTROLWORK_MEMORY_DIRNAME}",
            "areas": CONTROLWORK_MEMORY_AREAS,
            "lifecycles": CONTROLWORK_LIFECYCLES,
        },
        "features": {
            "categories": True,
            "checkpoints": True,
            "views": True,
            "obsidianProjection": True,
            "mcpReadOnly": True,
            "contextPackets": True,
        },
    }
    if exported_from:
        config["exportedFrom"] = exported_from
    return config


def _controlwork_context_template(project_name: str, purpose: str) -> str:
    return f"""# {project_name}

## Project Identity

- **Name**: {project_name}
- **Purpose**: {purpose}
- **Product Boundary**: Embedded ControlWork project plane inside a ControlCoding project. The same contract can also live as a standalone ControlWork repository.

## Work Memory Rules

1. `{CONTROLWORK_CONTEXT_FILENAME}` is the canonical source of truth for work/project context.
2. ControlCoding host files such as `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or `.clinerules` remain projections, not the ControlWork source of truth.
3. Captured material starts in inbox or sources, then gets promoted into notes, ideas, decisions, plans, outputs, or legacy.
4. Active knowledge must stay current. Superseded material moves to legacy instead of being silently deleted.
5. Categories are minimal by default. Add custom categories only when the work needs them.
6. Legal, compliance, or regulatory categories such as normative are optional and project-specific, not default categories.
7. Evidence-sensitive claims must point to a source, artifact, decision, or explicit assumption.
8. ControlWork organizes project knowledge; it is not legal, compliance, security, financial, medical, or other professional advice.
9. Do not place secrets, personal data, customer data, or confidential material in ControlWork or send it to an AI provider unless that is approved for the project.

## Memory Areas

- `inbox` - raw captured thoughts or imported fragments awaiting classification.
- `sources` - source documents, summaries, references, PDFs, scans, tables, images, links, or external notes.
- `notes` - cleaned observations and reusable context.
- `ideas` - brainstormed concepts, alternatives, open options, and design directions.
- `decisions` - accepted decisions with rationale and current status.
- `plans` - work plans, roadmaps, workflows, and implementation-ready briefs.
- `outputs` - deliverables, reports, drafts, analyses, exported packets, and final artifacts.
- `legacy` - superseded or archived material kept for traceability.
- `views` - generated indexes and summaries.

## Lifecycle

- `captured` - stored but not yet reviewed.
- `active` - currently valid and usable.
- `needs_review` - useful but uncertain, stale, conflicting, or incomplete.
- `superseded` - replaced by newer material.
- `legacy` - archived and not part of the current working context.

## ControlCoding Relationship

ControlWork holds the Project Plane: goals, domain knowledge, user research, documents, requirements, constraints, workflows, analysis, decisions, and planning artifacts.

ControlCoding adds Dev Plane and Code Plane behavior around this Project Plane. Development and code-specific decisions stay in ControlCoding unless they update the project knowledge.

The embedded Project Plane stays compatible with standalone ControlWork, but it does not copy the standalone repository identity, license section, or standalone-only host onboarding text. Only implemented portable Project Plane behavior should be reflected here.

## Origin And License Boundary

ControlWork originated as the Project Plane inside ControlCoding and is now a standalone, source-available noncommercial product. This embedded Project Plane remains compatible with standalone ControlWork.

ControlCoding and ControlWork are source-available noncommercial products. Commercial use, resale, paid hosting, commercial redistribution, commercial SaaS use, paid consulting packages, or inclusion of ControlCoding or ControlWork components in a commercial product or service requires a separate written commercial license from Stefano Tonello.

## Bridge Workflows

- Start directly inside ControlCoding: `cc memory work-init`.
- Port a standalone ControlWork repo into this project: `cc memory work-import <path>`.
- Attach a standalone ControlWork repo for manual sync: `cc memory work-attach <path>`, then `cc memory work-sync --direction pull --force` or `cc memory work-sync --direction push --force`.
- Create a separate work-only folder from this embedded plane: `cc memory work-export <target>`.

## Implemented Embedded Work Features

- Category registry: `cc memory work-category list`, `propose`, `add`, and `approve` manage default and project-specific categories.
- Views: `cc memory work-views` builds the memory index, active decisions, open questions, source ledger, category registry, and handoff packet views.
- Checkpoints: `cc memory work-checkpoint` creates explicit Work Checkpoint receipts under `.controlwork/checkpoints/`.
- Handoff: `cc memory work-handoff` prepares a compact transfer packet from generated Project Plane views.
- Context packets: `cc memory work-context-pack` builds a scoped packet for a chat or task and separates current, needs-review, legacy, and superseded material.
- Obsidian projection: `cc memory work-obsidian init`, `sync`, and `check` manage generated wiki pages under `wiki/`.
- Wiki edit review: `cc memory work-wiki import-edits --review` creates review proposals when generated wiki pages drift.
- Read-only MCP inspection: `cc memory work-mcp tools` and `cc memory work-mcp call` expose local read-only Project Plane queries.
- Operational index: `cc memory op-index` coordinates Dev Plane, Project Plane, application-owned memory, stale artifacts, RAG routes, action queue, and commit hygiene without hidden writes.

## Project Sync Choices

- The project owner decides whether ControlWork lives embedded, standalone, or both.
- Import, attach, sync, and export operations are explicit user actions.
- A project can keep standalone and embedded ControlWork aligned, or intentionally let them diverge for different audiences.
- Public release policy is project-specific and is not defined by ControlWork.

## Operative Rules

- Keep `{CONTROLWORK_CONTEXT_FILENAME}` concise and operational.
- Put large source material under `{CONTROLWORK_DIRNAME}/{CONTROLWORK_MEMORY_DIRNAME}/sources/` and summarize it into notes, decisions, or plans.
- Do not mix obsolete decisions into current context. Move them to legacy or mark them as superseded.
- Prefer clear filenames with date prefixes for captured artifacts.
- When a chat produces durable knowledge, capture it in memory instead of relying on chat history.
- At meaningful work milestones, create a Work Checkpoint. It is the embedded Project Plane closing ceremony for research, documents, decisions, plans, handoffs, and context preservation before chat compaction or transfer.
- Create checkpoints explicitly with `cc memory work-checkpoint`. Embedded `work-handoff` and `work-context-pack` currently prepare packets but do not automatically create checkpoint receipts.
- When unsure where something belongs, put it in inbox first and classify later.
- Keep standalone ControlWork and embedded ControlCoding ControlWork contract-compatible.
- Use `cc memory op-index --scope dev --topic "<topic>"` at session start when the AI needs a fast operational map before deeper retrieval or packet building.

## Current Focus

- [ ] Capture initial project sources.
- [ ] Classify inbox material.
- [ ] Promote current decisions and plans.
"""


def _controlwork_root(project: Path) -> Path:
    return project / CONTROLWORK_DIRNAME


def _controlwork_config_path(project: Path) -> Path:
    return _controlwork_root(project) / "config.json"


def _controlwork_context_path(project: Path) -> Path:
    return project / CONTROLWORK_CONTEXT_FILENAME


def _controlwork_link_path(project: Path) -> Path:
    return _controlwork_root(project) / "link.json"


def _controlwork_agent_adapter(source_text: str) -> str:
    return "\n".join([
        "# AGENTS.md",
        "",
        f"> Generated from {CONTROLWORK_CONTEXT_FILENAME} by ControlWork host-context export.",
        "> Target host: Codex CLI.",
        f"> Canonical source of truth: {CONTROLWORK_CONTEXT_FILENAME}.",
        "",
        source_text.strip(),
        "",
    ])


def _resolve_work_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _controlwork_validation(path: Path) -> dict:
    context_path = path / CONTROLWORK_CONTEXT_FILENAME
    config_path = path / CONTROLWORK_DIRNAME / "config.json"
    memory_path = path / CONTROLWORK_DIRNAME / CONTROLWORK_MEMORY_DIRNAME
    issues = []
    if not context_path.exists():
        issues.append(f"{CONTROLWORK_CONTEXT_FILENAME} is missing")
    if not config_path.exists():
        issues.append(f"{CONTROLWORK_DIRNAME}/config.json is missing")
    if not memory_path.exists():
        issues.append(f"{CONTROLWORK_DIRNAME}/{CONTROLWORK_MEMORY_DIRNAME} is missing")
    return {
        "ok": not issues,
        "path": str(path),
        "contextPath": str(context_path),
        "configPath": str(config_path),
        "memoryPath": str(memory_path),
        "issues": issues,
    }


def _controlwork_memory_counts(root: Path) -> dict[str, int | None]:
    memory_counts: dict[str, int | None] = {}
    for area in CONTROLWORK_MEMORY_AREAS:
        area_path = root / CONTROLWORK_MEMORY_DIRNAME / area
        if not area_path.exists():
            memory_counts[area] = None
            continue
        memory_counts[area] = len([
            path for path in area_path.iterdir()
            if path.is_file() and path.name != ".gitkeep"
        ])
    return memory_counts


def _controlwork_fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    files: list[Path] = []
    for relative in (
        CONTROLWORK_CONTEXT_FILENAME,
        f"{CONTROLWORK_DIRNAME}/config.json",
        f"{CONTROLWORK_DIRNAME}/categories.json",
    ):
        target = path / relative
        if target.exists() and target.is_file():
            files.append(target)
    memory_root = path / CONTROLWORK_DIRNAME / CONTROLWORK_MEMORY_DIRNAME
    for area in CONTROLWORK_MEMORY_AREAS:
        if area == "views":
            continue
        area_path = memory_root / area
        if area_path.exists():
            files.extend(
                item
                for item in area_path.rglob("*")
                if item.is_file() and item.name != ".gitkeep"
            )
    unreadable: list[str] = []
    for item in sorted(files, key=lambda candidate: candidate.relative_to(path).as_posix()):
        rel_item = item.relative_to(path).as_posix()
        digest.update(rel_item.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        try:
            digest.update(item.read_bytes())
        except OSError:
            unreadable.append(rel_item)
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return {
        "hash": digest.hexdigest(),
        "fileCount": len(files),
        "unreadable": unreadable,
    }


def _hash_json_payload(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read_controlwork_text(path: Path) -> str:
    target = path / CONTROLWORK_CONTEXT_FILENAME
    try:
        return target.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _first_markdown_title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _controlwork_context_signature(path: Path) -> dict:
    text = _read_controlwork_text(path)
    lower = text.lower()
    token_map = {
        token: token in text
        for token in CONTROLWORK_PORTABLE_FEATURE_TOKENS
    }
    missing_tokens = [
        token for token, present in token_map.items()
        if not present
    ]
    return {
        "exists": bool(text),
        "title": _first_markdown_title(text),
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
        "markers": {
            "embeddedProjectPlane": "embedded controlwork project plane" in lower or "# controlcoding project plane" in lower,
            "standaloneProduct": "# controlwork" in lower or "standalone controlwork repository" in lower,
            "standaloneOnlySections": "## ai host onboarding" in lower or "## license and attribution" in lower,
            "embeddedFeatureSection": "## implemented embedded work features" in lower,
            "operationalIndex": "cc memory op-index" in text,
        },
        "portableFeatureCoverage": {
            "present": len(CONTROLWORK_PORTABLE_FEATURE_TOKENS) - len(missing_tokens),
            "expected": len(CONTROLWORK_PORTABLE_FEATURE_TOKENS),
            "missing": missing_tokens,
        },
    }


def _controlwork_shared_config_contract(path: Path) -> dict:
    config = _read_json(path / CONTROLWORK_DIRNAME / "config.json")
    memory = config.get("memory", {}) if isinstance(config.get("memory"), dict) else {}
    return {
        "exists": bool(config),
        "schemaVersion": config.get("schemaVersion"),
        "product": config.get("product"),
        "distribution": config.get("distribution", ""),
        "canonicalContext": config.get("canonicalContext"),
        "memory": {
            "root": memory.get("root"),
            "areas": list(memory.get("areas") or []),
            "lifecycles": list(memory.get("lifecycles") or []),
        },
    }


def _normalize_controlwork_category_registry(path: Path) -> dict:
    registry = _read_json(path / CONTROLWORK_DIRNAME / "categories.json")
    categories = []
    raw_categories = registry.get("categories", [])
    if not isinstance(raw_categories, list):
        raw_categories = []
    for item in raw_categories:
        categories.append({
            "slug": str(item.get("slug", "")),
            "name": str(item.get("name", "")),
            "area": str(item.get("area", "")),
            "description": str(item.get("description", "")),
            "status": str(item.get("status", "")),
            "builtin": bool(item.get("builtin")),
        })
    return {
        "exists": bool(registry),
        "schemaVersion": registry.get("schemaVersion"),
        "categories": sorted(categories, key=lambda item: item["slug"]),
    }


def _controlwork_memory_content_fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    files: list[Path] = []
    memory_root = path / CONTROLWORK_DIRNAME / CONTROLWORK_MEMORY_DIRNAME
    for area in CONTROLWORK_MEMORY_AREAS:
        if area == "views":
            continue
        area_path = memory_root / area
        if area_path.exists():
            files.extend(
                item
                for item in area_path.rglob("*")
                if item.is_file() and item.name != ".gitkeep"
            )
    unreadable: list[str] = []
    for item in sorted(files, key=lambda candidate: candidate.relative_to(path).as_posix()):
        rel_item = item.relative_to(path).as_posix()
        digest.update(rel_item.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        try:
            digest.update(item.read_bytes())
        except OSError:
            unreadable.append(rel_item)
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return {
        "hash": digest.hexdigest(),
        "fileCount": len(files),
        "unreadable": unreadable,
    }


def _controlwork_memory_content_inventory(embedded_path: Path, external_path: Path) -> dict:
    def manifest(project_path: Path) -> dict[str, str]:
        files: list[Path] = []
        memory_root = project_path / CONTROLWORK_DIRNAME / CONTROLWORK_MEMORY_DIRNAME
        for area in CONTROLWORK_MEMORY_AREAS:
            if area == "views":
                continue
            area_path = memory_root / area
            if area_path.exists():
                files.extend(
                    item
                    for item in area_path.rglob("*")
                    if item.is_file() and item.name != ".gitkeep"
                )
        result: dict[str, str] = {}
        for item in sorted(files, key=lambda candidate: candidate.relative_to(project_path).as_posix()):
            rel_item = item.relative_to(project_path).as_posix()
            try:
                result[rel_item] = hashlib.sha256(item.read_bytes()).hexdigest()
            except OSError:
                result[rel_item] = "<unreadable>"
        return result

    embedded = manifest(embedded_path)
    external = manifest(external_path)
    shared_paths = set(embedded) & set(external)
    return {
        "embeddedOnly": sorted(set(embedded) - set(external)),
        "externalOnly": sorted(set(external) - set(embedded)),
        "changed": sorted(path for path in shared_paths if embedded[path] != external[path]),
    }


def _category_diff(embedded_categories: dict, external_categories: dict) -> dict:
    embedded_by_slug = {
        item["slug"]: item for item in embedded_categories.get("categories", [])
    }
    external_by_slug = {
        item["slug"]: item for item in external_categories.get("categories", [])
    }
    embedded_only = sorted(set(embedded_by_slug) - set(external_by_slug))
    external_only = sorted(set(external_by_slug) - set(embedded_by_slug))
    changed = sorted(
        slug
        for slug in set(embedded_by_slug) & set(external_by_slug)
        if embedded_by_slug[slug] != external_by_slug[slug]
    )
    return {
        "semanticDifferent": bool(embedded_only or external_only or changed),
        "embeddedOnly": embedded_only,
        "externalOnly": external_only,
        "changed": changed,
    }


def _context_difference_classification(embedded_context: dict, external_context: dict) -> str:
    if not embedded_context.get("exists") or not external_context.get("exists"):
        return "missing_context"
    if embedded_context.get("hash") == external_context.get("hash"):
        return "same"
    embedded_markers = embedded_context.get("markers", {})
    external_markers = external_context.get("markers", {})
    embedded_title = str(embedded_context.get("title", "")).strip().lower()
    external_title = str(external_context.get("title", "")).strip().lower()
    official_embedded = embedded_title == "controlcoding project plane"
    official_external = external_title == "controlwork"
    expected_distribution = (
        official_embedded
        and official_external
        and bool(embedded_markers.get("embeddedFeatureSection"))
        and bool(embedded_markers.get("operationalIndex"))
        and bool(external_markers.get("standaloneOnlySections"))
    )
    if expected_distribution:
        return "expected_distribution_context"
    return "needs_review"


def _controlwork_semantic_drift(project: Path, external_path: Path | None) -> dict:
    if external_path is None:
        return {
            "available": False,
            "actionableDrift": False,
            "recommendedAction": "none",
            "notes": ["No linked external ControlWork project."],
        }

    embedded_config = _controlwork_shared_config_contract(project)
    external_config = _controlwork_shared_config_contract(external_path)
    embedded_config_shared = {
        key: value for key, value in embedded_config.items()
        if key not in {"distribution"}
    }
    external_config_shared = {
        key: value for key, value in external_config.items()
        if key not in {"distribution"}
    }
    config_shared_different = embedded_config_shared != external_config_shared
    config_distribution_difference = embedded_config.get("distribution") != external_config.get("distribution")

    embedded_context = _controlwork_context_signature(project)
    external_context = _controlwork_context_signature(external_path)
    context_classification = _context_difference_classification(embedded_context, external_context)
    context_actionable = context_classification not in {"same", "expected_distribution_context"}

    embedded_categories = _normalize_controlwork_category_registry(project)
    external_categories = _normalize_controlwork_category_registry(external_path)
    categories = _category_diff(embedded_categories, external_categories)

    embedded_memory = _controlwork_memory_content_fingerprint(project)
    external_memory = _controlwork_memory_content_fingerprint(external_path)
    memory_content_different = embedded_memory.get("hash") != external_memory.get("hash")

    actionable = bool(
        config_shared_different
        or context_actionable
        or categories["semanticDifferent"]
        or memory_content_different
    )
    notes: list[str] = []
    if config_distribution_difference and not config_shared_different:
        notes.append("Config differs only by distribution-specific metadata.")
    if context_classification == "expected_distribution_context":
        notes.append("Context differs by expected embedded versus standalone distribution text.")
    if embedded_categories.get("exists") and external_categories.get("exists") and not categories["semanticDifferent"]:
        notes.append("Category registries match after ignoring audit timestamps.")
    if not memory_content_different:
        notes.append("No Project Plane memory content drift was detected outside generated views.")

    return {
        "available": True,
        "actionableDrift": actionable,
        "recommendedAction": "manual_review" if actionable else "none",
        "config": {
            "sharedContractDifferent": config_shared_different,
            "distributionDifferent": config_distribution_difference,
            "embeddedDistribution": embedded_config.get("distribution", ""),
            "externalDistribution": external_config.get("distribution", ""),
            "embeddedSharedHash": _hash_json_payload(embedded_config_shared),
            "externalSharedHash": _hash_json_payload(external_config_shared),
        },
        "context": {
            "classification": context_classification,
            "actionable": context_actionable,
            "embeddedTitle": embedded_context.get("title", ""),
            "externalTitle": external_context.get("title", ""),
            "embeddedPortableFeatureCoverage": embedded_context.get("portableFeatureCoverage", {}),
        },
        "categories": categories,
        "memoryContent": {
            "different": memory_content_different,
            "embeddedFileCount": embedded_memory.get("fileCount", 0),
            "externalFileCount": external_memory.get("fileCount", 0),
            "embeddedHash": embedded_memory.get("hash", ""),
            "externalHash": external_memory.get("hash", ""),
        },
        "notes": notes,
    }


def _controlwork_views_drift(project: Path) -> list[dict]:
    views_dir = project / CONTROLWORK_DIRNAME / CONTROLWORK_MEMORY_DIRNAME / "views"
    expected = {
        "index.md",
        "active-decisions.md",
        "open-questions.md",
        "source-ledger.md",
        "handoff-packet.md",
        "category-registry.md",
    }
    drift: list[dict] = []
    for filename in sorted(expected):
        path = views_dir / filename
        rel_item = _relative_path(project, path)
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            drift.append({"path": rel_item, "state": "missing"})
            continue
        except OSError:
            drift.append({"path": rel_item, "state": "unreadable"})
            continue

        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        file_attributes = getattr(path_stat, "st_file_attributes", 0)
        if file_attributes & reparse_attribute or not stat.S_ISREG(path_stat.st_mode):
            drift.append({"path": rel_item, "state": "wrong_type"})
            continue

        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            descriptor_before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(descriptor_before.st_mode)
                or (descriptor_before.st_dev, descriptor_before.st_ino)
                != (path_stat.st_dev, path_stat.st_ino)
            ):
                drift.append({"path": rel_item, "state": "wrong_type"})
                continue
            while os.read(descriptor, 64 * 1024):
                pass
            descriptor_after = os.fstat(descriptor)
            path_after = path.lstat()
            if (
                (descriptor_before.st_dev, descriptor_before.st_ino)
                != (descriptor_after.st_dev, descriptor_after.st_ino)
                or descriptor_before.st_size != descriptor_after.st_size
                or descriptor_before.st_mtime_ns != descriptor_after.st_mtime_ns
                or (descriptor_after.st_dev, descriptor_after.st_ino)
                != (path_after.st_dev, path_after.st_ino)
                or path_after.st_size != descriptor_after.st_size
                or path_after.st_mtime_ns != descriptor_after.st_mtime_ns
                or (
                    getattr(path_after, "st_file_attributes", 0)
                    & reparse_attribute
                )
                or not stat.S_ISREG(path_after.st_mode)
            ):
                drift.append({"path": rel_item, "state": "unreadable"})
        except FileNotFoundError:
            drift.append({"path": rel_item, "state": "missing"})
        except OSError:
            drift.append({"path": rel_item, "state": "unreadable"})
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    return drift


def _controlwork_derived_status(project: Path) -> dict:
    context_packets_root = project / CONTROLWORK_DIRNAME / "context-packets"
    packet_inventory, packet_reason = (
        freshness_projection._stable_markdown_inventory_for_feature_status(
            context_packets_root,
            "context_packets_unreadable",
        )
    )
    packet_path = f"{CONTROLWORK_DIRNAME}/context-packets"
    if packet_reason or packet_inventory is None:
        context_packet_status = {
            "path": packet_path,
            "available": False,
            "count": None,
            "latest": None,
            "stale": None,
            "health": {
                "state": "unknown",
                "reason": packet_reason or "context_packets_unreadable",
            },
        }
    else:
        packet_entries = packet_inventory["entries"]
        latest_name = packet_entries[-1]["name"] if packet_entries else ""
        context_packet_status = {
            "path": packet_path,
            "available": True,
            "count": len(packet_entries),
            "latest": (
                _relative_path(project, context_packets_root / latest_name)
                if latest_name
                else ""
            ),
            "stale": not bool(packet_entries),
            "health": {"state": "observed", "reason": ""},
        }
    view_drift = _controlwork_views_drift(project)
    return {
        "views": {
            "drift": view_drift,
            "stale": bool(view_drift),
        },
        "contextPackets": context_packet_status,
        "obsidianProjection": {
            "exists": (project / "wiki").exists(),
            "drift": [],
            "stale": False,
            "checkCommand": "python scripts/cc.py memory work-obsidian check --project-root .",
        },
    }


def _controlwork_drift_payload(project: Path, attachment: dict) -> dict:
    embedded_fingerprint = _controlwork_fingerprint(project)
    external_validation = attachment.get("validation", {}) if isinstance(attachment, dict) else {}
    external_ok = bool(external_validation.get("ok"))
    external_path = Path(str(attachment.get("externalPath", ""))) if attachment.get("externalPath") else None
    external_fingerprint = _controlwork_fingerprint(external_path) if external_ok and external_path else {}
    embedded_vs_external = None
    if external_fingerprint:
        embedded_vs_external = embedded_fingerprint.get("hash") != external_fingerprint.get("hash")
    semantic = _controlwork_semantic_drift(project, external_path if external_ok else None)
    if external_ok and external_path:
        semantic["memoryContent"]["inventory"] = _controlwork_memory_content_inventory(project, external_path)
    derived = _controlwork_derived_status(project)
    update_requests: list[dict] = []
    if semantic.get("config", {}).get("sharedContractDifferent"):
        update_requests.append({
            "kind": "manual_controlwork_contract_review",
            "priority": "review",
            "owner": "human_operator",
            "severity": "medium",
            "reason": "Embedded and standalone ControlWork shared config contracts differ.",
            "recommendedAction": "manual_review",
            "writesRequired": False,
            "decisionRequired": True,
            "preconditions": [
                "Review the shared config contract drift.",
                "Choose whether embedded or standalone ControlWork is authoritative.",
            ],
            "steps": [
                {
                    "order": 1,
                    "kind": "read_only_review",
                    "command": "python scripts/cc.py memory work-status --project-root .",
                    "writesRequired": False,
                },
            ],
            "commands": [
                "python scripts/cc.py memory work-status --project-root .",
            ],
            "autoApplied": False,
        })
    if semantic.get("context", {}).get("actionable"):
        commands = ["python scripts/cc.py memory work-status --project-root ."]
        if external_path:
            external_context = str(external_path / CONTROLWORK_CONTEXT_FILENAME).replace('"', '\\"')
            commands.append(f'git diff --no-index -- "{CONTROLWORK_CONTEXT_FILENAME}" "{external_context}"')
        update_requests.append({
            "kind": "manual_context_merge_review",
            "priority": "review",
            "owner": "human_operator",
            "severity": "medium",
            "reason": "Embedded and standalone ControlWork contexts differ in a way that needs review.",
            "recommendedAction": "manual_review",
            "writesRequired": False,
            "decisionRequired": True,
            "preconditions": [
                "Review the context drift evidence.",
                "Choose whether embedded or standalone ControlWork is authoritative.",
            ],
            "steps": [
                {
                    "order": 1,
                    "kind": "read_only_review",
                    "command": "python scripts/cc.py memory work-status --project-root .",
                    "writesRequired": False,
                },
            ],
            "commands": commands,
            "autoApplied": False,
        })
    if semantic.get("categories", {}).get("semanticDifferent"):
        update_requests.append({
            "kind": "manual_category_merge_review",
            "priority": "review",
            "owner": "human_operator",
            "severity": "medium",
            "reason": "Embedded and standalone ControlWork category registries differ beyond audit timestamps.",
            "recommendedAction": "manual_review",
            "writesRequired": False,
            "decisionRequired": True,
            "preconditions": [
                "Review the category registry drift.",
                "Choose whether embedded or standalone ControlWork is authoritative.",
            ],
            "steps": [
                {
                    "order": 1,
                    "kind": "read_only_review",
                    "command": "python scripts/cc.py memory work-status --project-root .",
                    "writesRequired": False,
                },
            ],
            "commands": [
                "python scripts/cc.py memory work-status --project-root .",
                "python scripts/cc.py memory work-category list --project-root .",
            ],
            "autoApplied": False,
        })
    if semantic.get("memoryContent", {}).get("different"):
        memory_inventory = semantic["memoryContent"]["inventory"]
        update_requests.append({
            "kind": "manual_sync_review",
            "priority": "review",
            "owner": "human_operator",
            "severity": "high",
            "reason": "Embedded and standalone ControlWork memory content differs.",
            "recommendedAction": "manual_review",
            "writesRequired": False,
            "decisionRequired": True,
            "inventory": memory_inventory,
            "preconditions": [
                "Review the memory drift inventory.",
                "Choose whether embedded or standalone ControlWork is authoritative.",
            ],
            "steps": [
                {
                    "order": 1,
                    "kind": "read_only_review",
                    "command": "python scripts/cc.py memory work-status --project-root .",
                    "writesRequired": False,
                },
                {
                    "order": 2,
                    "kind": "mutative_alternative",
                    "alternativeGroup": "memory_sync_direction",
                    "alternative": "pull",
                    "command": "python scripts/cc.py memory work-sync --project-root . --direction pull --force",
                    "writesRequired": True,
                },
                {
                    "order": 2,
                    "kind": "mutative_alternative",
                    "alternativeGroup": "memory_sync_direction",
                    "alternative": "push",
                    "command": "python scripts/cc.py memory work-sync --project-root . --direction push --force",
                    "writesRequired": True,
                },
            ],
            "commands": [
                "python scripts/cc.py memory work-status --project-root .",
                "python scripts/cc.py memory work-sync --project-root . --direction pull --force",
                "python scripts/cc.py memory work-sync --project-root . --direction push --force",
            ],
            "autoApplied": False,
        })
    if derived["views"]["stale"]:
        update_requests.append({
            "kind": "regenerate_project_plane_views",
            "priority": "review",
            "owner": "human_operator",
            "severity": "low",
            "reason": "Embedded ControlWork views are missing or drifted.",
            "recommendedAction": "manual_review",
            "writesRequired": False,
            "decisionRequired": False,
            "preconditions": ["Review the generated-view drift before regeneration."],
            "steps": [
                {
                    "order": 1,
                    "kind": "read_only_review",
                    "command": "python scripts/cc.py memory work-status --project-root .",
                    "writesRequired": False,
                },
                {
                    "order": 2,
                    "kind": "mutative_followup",
                    "command": "python scripts/cc.py memory work-views --project-root .",
                    "writesRequired": True,
                },
            ],
            "commands": ["python scripts/cc.py memory work-views --project-root ."],
            "autoApplied": False,
        })
    if derived["contextPackets"]["stale"]:
        update_requests.append({
            "kind": "build_project_plane_context_packet",
            "priority": "review",
            "owner": "human_operator",
            "severity": "low",
            "reason": "No Project Plane context packets are available.",
            "recommendedAction": "manual_review",
            "writesRequired": False,
            "decisionRequired": False,
            "preconditions": ["Review the missing context-packet status before building one."],
            "steps": [
                {
                    "order": 1,
                    "kind": "read_only_review",
                    "command": "python scripts/cc.py memory work-status --project-root .",
                    "writesRequired": False,
                },
                {
                    "order": 2,
                    "kind": "mutative_followup",
                    "command": "python scripts/cc.py memory work-context-pack --project-root . --scope general --topic \"current focus\"",
                    "writesRequired": True,
                },
            ],
            "commands": ["python scripts/cc.py memory work-context-pack --project-root . --scope general --topic \"current focus\""],
            "autoApplied": False,
        })
    if derived["obsidianProjection"]["stale"]:
        update_requests.append({
            "kind": "review_obsidian_projection",
            "priority": "review",
            "owner": "human_operator",
            "severity": "low",
            "reason": "Obsidian projection has drift.",
            "recommendedAction": "manual_review",
            "writesRequired": False,
            "decisionRequired": False,
            "preconditions": ["Review the Obsidian projection drift before syncing it."],
            "steps": [
                {
                    "order": 1,
                    "kind": "read_only_review",
                    "command": "python scripts/cc.py memory work-obsidian check --project-root .",
                    "writesRequired": False,
                },
                {
                    "order": 2,
                    "kind": "mutative_followup",
                    "command": "python scripts/cc.py memory work-obsidian sync --project-root .",
                    "writesRequired": True,
                },
            ],
            "commands": [
                "python scripts/cc.py memory work-obsidian check --project-root .",
                "python scripts/cc.py memory work-obsidian sync --project-root .",
            ],
            "autoApplied": False,
        })
    return {
        "embeddedFingerprint": embedded_fingerprint,
        "externalFingerprint": external_fingerprint,
        "embeddedVsExternalDifferent": embedded_vs_external,
        "semantic": semantic,
        "derivedArtifacts": derived,
        "updateRequests": update_requests,
        "policy": {
            "autoPull": False,
            "autoPush": False,
            "hiddenWrites": False,
        },
    }


def _copy_controlwork_into_project(
    source_project: Path,
    target_project: Path,
    force: bool,
    preserve_link: dict | None = None,
) -> tuple[bool, str, list[str]]:
    source_project = source_project.resolve()
    target_project = target_project.resolve()
    if source_project == target_project:
        return False, "source and target are the same project", []
    validation = _controlwork_validation(source_project)
    if not validation["ok"]:
        return False, "; ".join(validation["issues"]), []

    target_context = _controlwork_context_path(target_project)
    target_root = _controlwork_root(target_project)
    if not force and (target_context.exists() or target_root.exists()):
        return False, "embedded ControlWork already exists; use --force to overwrite", []

    if target_context.exists():
        target_context.unlink()
    if target_root.exists():
        shutil.rmtree(target_root)

    shutil.copy2(source_project / CONTROLWORK_CONTEXT_FILENAME, target_context)
    shutil.copytree(source_project / CONTROLWORK_DIRNAME, target_root)
    _write_json(_controlwork_config_path(target_project), _controlwork_config())
    if preserve_link is not None:
        _write_json(_controlwork_link_path(target_project), preserve_link)

    written = [
        _relative_path(target_project, target_context),
        _relative_path(target_project, target_root),
    ]
    return True, "", written


def _export_controlwork_from_project(
    source_project: Path,
    target_project: Path,
    force: bool,
) -> tuple[bool, str, list[str]]:
    source_context = _controlwork_context_path(source_project)
    source_root = _controlwork_root(source_project)
    if not source_context.exists() or not source_root.exists():
        return False, "embedded ControlWork is missing; run `cc memory work-init` first", []

    target_project = target_project.resolve()
    target_project.mkdir(parents=True, exist_ok=True)
    target_context = target_project / CONTROLWORK_CONTEXT_FILENAME
    target_root = target_project / CONTROLWORK_DIRNAME
    target_adapter = target_project / "AGENTS.md"

    if not force and (target_context.exists() or target_root.exists()):
        return False, "target already has ControlWork artifacts; use --force to overwrite", []

    if target_context.exists():
        target_context.unlink()
    if target_root.exists():
        shutil.rmtree(target_root)
    if force and target_adapter.exists():
        target_adapter.unlink()

    shutil.copy2(source_context, target_context)
    shutil.copytree(source_root, target_root)
    target_link = target_root / "link.json"
    if target_link.exists():
        target_link.unlink()
    _write_json(
        target_root / "config.json",
        _standalone_controlwork_config(exported_from=str(source_project.resolve())),
    )
    if not target_adapter.exists():
        target_adapter.write_text(
            _controlwork_agent_adapter(target_context.read_text(encoding="utf-8")),
            encoding="utf-8",
        )

    return True, "", [
        str(target_context),
        str(target_root),
        str(target_adapter),
    ]


def _ensure_controlwork_layout(project: Path) -> list[str]:
    created: list[str] = []
    root = _controlwork_root(project)
    for area in CONTROLWORK_MEMORY_AREAS:
        area_path = root / CONTROLWORK_MEMORY_DIRNAME / area
        area_path.mkdir(parents=True, exist_ok=True)
        keep = area_path / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
            created.append(_relative_path(project, keep))
    created.extend(work_features.ensure_feature_layout(project))
    return created


def cmd_memory_work_init(
    project: Path,
    project_name: str = "",
    purpose: str = "Project knowledge and work memory",
    force: bool = False,
    json_output: bool = False,
) -> int:
    created = _ensure_controlwork_layout(project)
    preserved: list[str] = []
    name = str(project_name or project.name or "ControlWork Project").strip()
    work_purpose = str(purpose or "Project knowledge and work memory").strip()

    config_path = _controlwork_config_path(project)
    config_payload = _controlwork_config()
    if force or not config_path.exists():
        _write_json(config_path, config_payload)
        created.append(_relative_path(project, config_path))
    else:
        preserved.append(_relative_path(project, config_path))

    scan_payload = work_features.refresh_file_index(project)
    analysis = work_features.build_scan_analysis(project, scan_payload)
    understanding = work_features.write_project_understanding(
        project,
        scan_payload,
        analysis=analysis,
        purpose_hint=work_purpose,
        distribution="embedded_controlcoding",
    )
    index_path = work_features.file_index_path(project)
    analysis_path = work_features.scan_analysis_path(project)
    work_features.write_json(analysis_path, analysis)
    created.extend([
        work_features.rel(project, index_path),
        work_features.rel(project, analysis_path),
        work_features.rel(project, work_features.project_understanding_path(project)),
    ])

    context_path = _controlwork_context_path(project)
    if force or not context_path.exists():
        context_path.write_text(
            _controlwork_context_template(name, work_purpose),
            encoding="utf-8",
        )
        created.append(_relative_path(project, context_path))
    else:
        preserved.append(_relative_path(project, context_path))

    base_document = work_features.ensure_project_base_document(
        project,
        project_name=name,
        purpose=work_features.project_understanding_summary(understanding, fallback=work_purpose),
        understanding=understanding,
    )
    if base_document["created"]:
        created.append(base_document["path"])
    else:
        preserved.append(base_document["path"])

    payload = {
        "ok": True,
        "projectRoot": str(project),
        "distribution": "embedded_controlcoding",
        "standaloneCompatible": True,
        "canonicalContext": CONTROLWORK_CONTEXT_FILENAME,
        "baseDocument": base_document["path"],
        "guidedSetup": {
            "status": understanding.get("status", "needs_human_confirmation"),
            "selectedKind": understanding.get("selectedKind", "archive_organization"),
            "primaryPurpose": understanding.get("primaryPurpose", "confirm_project_goal"),
            "questions": understanding.get("questions", []),
            "blockedUntilApproval": understanding.get("blockedUntilApproval", []),
        },
        "controlworkRoot": CONTROLWORK_DIRNAME,
        "created": created,
        "preserved": preserved,
        "hostAdapterNote": (
            f"ControlCoding host adapters are not overwritten. Use "
            f"`cc export host-context --source {CONTROLWORK_CONTEXT_FILENAME} --host <host>` "
            "only when you explicitly want a ControlWork projection."
        ),
    }
    _print_json_or_text(
        json_output,
        payload,
        (
            "Embedded ControlWork project plane initialized\n"
            f"  Canonical context: {CONTROLWORK_CONTEXT_FILENAME}\n"
            f"  ControlWork root: {CONTROLWORK_DIRNAME}\n"
            f"  Created: {len(created)}\n"
            f"  Preserved: {len(preserved)}"
        ),
    )
    return 0


def _work_status_payload(project: Path) -> dict:
    config = _read_json(_controlwork_config_path(project))
    context_exists = _controlwork_context_path(project).exists()
    memory_counts = _controlwork_memory_counts(_controlwork_root(project))
    link = _read_json(_controlwork_link_path(project))
    attachment = {}
    if link:
        external_path = _resolve_work_path(str(link.get("externalPath", "")))
        attachment = {
            **link,
            "externalPath": str(external_path),
            "validation": _controlwork_validation(external_path),
        }
    drift = _controlwork_drift_payload(project, attachment)
    understanding = work_features.read_project_understanding(project)

    embedded_ok = bool(config) and context_exists
    attached_ok = bool(attachment.get("validation", {}).get("ok"))
    payload = {
        "ok": embedded_ok or attached_ok,
        "projectRoot": str(project),
        "distribution": config.get("distribution", "") if config else "",
        "standaloneCompatible": bool(config.get("standaloneCompatible")) if config else False,
        "canonicalContext": CONTROLWORK_CONTEXT_FILENAME,
        "baseDocument": work_features.configured_base_document_path(project, config),
        "hasConfig": bool(config),
        "hasCanonicalContext": context_exists,
        "hasBaseDocument": (project / work_features.configured_base_document_path(project, config)).exists() if config else False,
        "guidedSetup": {
            "hasProjectUnderstanding": bool(understanding),
            "status": understanding.get("status", "not_generated") if understanding else "not_generated",
            "selectedKind": understanding.get("selectedKind", "") if understanding else "",
            "primaryPurpose": understanding.get("primaryPurpose", "") if understanding else "",
            "questions": understanding.get("questions", []) if understanding else [],
            "blockedUntilApproval": understanding.get("blockedUntilApproval", []) if understanding else [],
        },
        "memoryCounts": memory_counts,
        "features": work_features.feature_status_payload(project),
        "attached": bool(link),
        "attachment": attachment,
        "drift": drift,
    }
    return payload


def cmd_memory_work_status(project: Path, json_output: bool = False) -> int:
    payload = _work_status_payload(project)
    _print_json_or_text(
        json_output,
        payload,
        (
            "Embedded ControlWork project plane\n"
            f"  Status: {'ok' if payload['ok'] else 'missing'}\n"
            f"  Distribution: {payload['distribution'] or '(none)'}\n"
            f"  Standalone compatible: {payload['standaloneCompatible']}\n"
            f"  Embedded/external drift: {payload['drift']['embeddedVsExternalDifferent']}\n"
            f"  Actionable semantic drift: {payload['drift']['semantic'].get('actionableDrift')}\n"
            f"  Update requests: {len(payload['drift']['updateRequests'])}"
        ),
    )
    return 0 if payload["ok"] else 1


def _work_parity_projection(project: Path, distribution: str) -> dict:
    config_contract = _controlwork_shared_config_contract(project)
    shared_contract = {
        key: value for key, value in config_contract.items()
        if key != "distribution"
    }
    return {
        "projectRoot": str(project),
        "distribution": distribution or config_contract.get("distribution", ""),
        "hasConfig": bool(config_contract.get("exists")),
        "hasCanonicalContext": (project / CONTROLWORK_CONTEXT_FILENAME).is_file(),
        "canonicalContext": CONTROLWORK_CONTEXT_FILENAME,
        "memoryCounts": _controlwork_memory_counts(_controlwork_root(project)),
        "features": work_features.feature_status_payload(project),
        "sharedContractHash": _hash_json_payload(shared_contract),
    }


def _work_parity_payload(project: Path) -> dict:
    status = _work_status_payload(project)
    attachment = status.get("attachment", {}) if isinstance(status.get("attachment"), dict) else {}
    validation = attachment.get("validation", {}) if isinstance(attachment.get("validation"), dict) else {}
    external_ok = bool(validation.get("ok"))
    external_path = Path(str(attachment.get("externalPath", ""))) if attachment.get("externalPath") else None
    semantic = status.get("drift", {}).get("semantic", {})
    config = semantic.get("config", {}) if isinstance(semantic.get("config"), dict) else {}
    context = semantic.get("context", {}) if isinstance(semantic.get("context"), dict) else {}
    categories = semantic.get("categories", {}) if isinstance(semantic.get("categories"), dict) else {}
    memory_content = semantic.get("memoryContent", {}) if isinstance(semantic.get("memoryContent"), dict) else {}

    shared_config_aligned = external_ok and not bool(config.get("sharedContractDifferent"))
    context_compatible = external_ok and not bool(context.get("actionable"))
    category_registry_aligned = external_ok and not bool(categories.get("semanticDifferent"))
    memory_aligned = external_ok and not bool(memory_content.get("different"))
    contract_compatible = bool(status.get("ok")) and external_ok and shared_config_aligned and context_compatible and category_registry_aligned

    checks = [
        {
            "id": "embedded_controlwork",
            "status": "ok" if status.get("ok") else "fail",
            "detail": status.get("distribution", "") or "missing",
        },
        {
            "id": "external_controlwork",
            "status": "ok" if external_ok else "warn",
            "detail": str(external_path) if external_ok and external_path else "no valid attached standalone ControlWork project",
        },
        {
            "id": "shared_config_contract",
            "status": "ok" if shared_config_aligned else ("fail" if external_ok else "warn"),
            "detail": "aligned" if shared_config_aligned else "shared config contract differs or cannot be compared",
        },
        {
            "id": "context_contract",
            "status": "ok" if context_compatible else ("fail" if external_ok else "warn"),
            "detail": str(context.get("classification", "unavailable")),
        },
        {
            "id": "category_registry",
            "status": "ok" if category_registry_aligned else ("fail" if external_ok else "warn"),
            "detail": "aligned" if category_registry_aligned else "category registry differs or cannot be compared",
        },
        {
            "id": "memory_content",
            "status": "ok" if memory_aligned else ("warn" if external_ok else "warn"),
            "detail": "aligned" if memory_aligned else "memory content differs or cannot be compared",
        },
    ]

    if not status.get("ok"):
        state = "RED"
    elif not external_ok:
        state = "YELLOW"
    elif not contract_compatible:
        state = "RED"
    elif not memory_aligned:
        state = "YELLOW"
    else:
        state = "GREEN"

    external_projection = {}
    if external_ok and external_path:
        external_projection = _work_parity_projection(external_path, str(config.get("externalDistribution", "")))

    return {
        "ok": state != "RED",
        "state": state,
        "readOnly": True,
        "hiddenWrites": False,
        "workPlaneContract": CONTROLWORK_WORK_PLANE_CONTRACT,
        "embedded": _work_parity_projection(project, str(config.get("embeddedDistribution", status.get("distribution", "")))),
        "standalone": external_projection,
        "compatibility": {
            "contractCompatible": contract_compatible,
            "sharedConfigAligned": shared_config_aligned,
            "contextCompatible": context_compatible,
            "expectedDistributionContext": context.get("classification") == "expected_distribution_context",
            "categoryRegistryAligned": category_registry_aligned,
            "memoryAligned": memory_aligned,
        },
        "commandSurface": {
            "shared": CONTROLWORK_SHARED_COMMAND_SURFACE,
            "acceptedDivergences": CONTROLWORK_ACCEPTED_COMMAND_DIVERGENCES,
            "mechanicallyComparable": True,
        },
        "drift": {
            "available": bool(semantic.get("available")),
            "embeddedVsExternalDifferent": status.get("drift", {}).get("embeddedVsExternalDifferent"),
            "actionableSemanticDrift": bool(semantic.get("actionableDrift")),
            "recommendedAction": str(semantic.get("recommendedAction", "none")),
            "updateRequests": status.get("drift", {}).get("updateRequests", []),
            "policy": status.get("drift", {}).get("policy", {}),
        },
        "checks": checks,
    }


def cmd_memory_work_parity(project: Path, json_output: bool = False) -> int:
    payload = _work_parity_payload(project)
    _print_json_or_text(
        json_output,
        payload,
        (
            "ControlWork embedded/standalone parity\n"
            f"  State: {payload['state']}\n"
            f"  Read-only: {payload['readOnly']}\n"
            f"  Contract compatible: {payload['compatibility']['contractCompatible']}\n"
            f"  Memory aligned: {payload['compatibility']['memoryAligned']}\n"
            f"  Recommended action: {payload['drift']['recommendedAction']}\n"
            f"  Update requests: {len(payload['drift']['updateRequests'])}"
        ),
    )
    return 0 if payload["ok"] else 1


def _work_quickstart_next_commands(project: Path, scope: str, topic: str) -> list[str]:
    topic_arg = json.dumps(topic)
    return [
        f"python scripts\\cc.py memory work-quickstart --project-root {json.dumps(str(project))} --scope {scope} --topic {topic_arg}",
        f"python scripts\\cc.py memory work-query {topic_arg} --project-root {json.dumps(str(project))}",
        f"python scripts\\cc.py memory work-context-pack --project-root {json.dumps(str(project))} --scope {scope} --topic {topic_arg}",
        f"python scripts\\cc.py memory work-retrieve {topic_arg} --project-root {json.dumps(str(project))}",
        f"python scripts\\cc.py memory work-checkpoint --project-root {json.dumps(str(project))} --title \"Work checkpoint\"",
    ]


def _work_quickstart_stale_warnings(project: Path) -> list[str]:
    warnings: list[str] = []
    memory_root = project / work_features.MEMORY_ROOT
    view_index = memory_root / "views" / "index.md"
    source_files = [
        path
        for path in memory_root.glob("**/*")
        if path.is_file()
        and path.name != ".gitkeep"
        and "views" not in path.relative_to(memory_root).parts
    ] if memory_root.exists() else []

    if not view_index.exists():
        warnings.append("Generated Project Plane views are missing; run `cc memory work-views` or `cc memory work-quickstart`.")
    elif any(path.stat().st_mtime > view_index.stat().st_mtime for path in source_files):
        warnings.append("Generated Project Plane views may be stale compared with durable memory entries.")

    index_path = work_features.file_index_path(project)
    analysis_path = work_features.scan_analysis_path(project)
    if not index_path.exists():
        warnings.append("Project Plane file scan index is missing; run `cc memory work-scan` or `cc memory work-quickstart`.")
    elif not analysis_path.exists():
        warnings.append("Project Plane scan analysis is missing; run `cc memory work-analyze` or `cc memory work-quickstart`.")
    elif index_path.stat().st_mtime > analysis_path.stat().st_mtime:
        warnings.append("Project Plane scan analysis may be stale compared with the file scan index.")

    packet_root = project / work_features.CONTEXT_PACKET_ROOT
    if not packet_root.exists() or not list(packet_root.glob("*.md")):
        warnings.append("No Project Plane context packet exists yet for starting or resuming an AI chat.")
    return warnings


def _work_quickstart_dry_run_payload(project: Path, scope: str, topic: str) -> dict:
    expected_writes = [
        CONTROLWORK_CONTEXT_FILENAME,
        work_features.PROJECT_BASE_DOCUMENT_FILENAME,
        f"{CONTROLWORK_DIRNAME}/config.json",
        f"{CONTROLWORK_DIRNAME}/categories.json",
        f"{CONTROLWORK_DIRNAME}/ingestion/file-index.json",
        f"{CONTROLWORK_DIRNAME}/ingestion/scan-analysis.json",
        f"{CONTROLWORK_DIRNAME}/ingestion/project-understanding.json",
        f"{CONTROLWORK_DIRNAME}/memory/views/index.md",
        f"{CONTROLWORK_DIRNAME}/context-packets/quickstart-{work_features.slug(scope)}-{work_features.slug(topic or 'context')}.md",
    ]
    return {
        "ok": True,
        "dryRun": True,
        "projectRoot": str(project),
        "summary": "No files written. Embedded quickstart would initialize the Project Plane, scan files, infer the project kind, ask for human confirmation, analyze scan evidence, regenerate views, retrieve topic context, and write one scoped context packet.",
        "wouldWriteOrRefresh": sorted(set(expected_writes)),
        "staleViewWarnings": _work_quickstart_stale_warnings(project),
        "nextCommands": _work_quickstart_next_commands(project, scope, topic),
        "externalCalls": {
            "aiCalls": False,
            "networkCalls": False,
            "subprocessCalls": False,
        },
        "hostAdapterPolicy": (
            "Embedded quickstart does not overwrite ControlCoding host adapters. "
            f"Use `cc export host-context --source {CONTROLWORK_CONTEXT_FILENAME} --host <host>` only when an explicit ControlWork projection is wanted."
        ),
        "projectionPolicy": "Generated views, packets, scan indexes, and graph output are projections or review evidence. CONTROLWORK.md and reviewed .controlwork/memory entries remain authoritative.",
    }


def cmd_memory_work_quickstart(
    project: Path,
    project_name: str = "",
    purpose: str = "Project knowledge and work memory",
    scope: str = "general",
    topic: str = "project direction",
    limit: int = 10,
    scan_limit: int = 50,
    include_legacy: bool = False,
    checkpoint: bool = False,
    checkpoint_title: str = "",
    dry_run: bool = False,
    json_output: bool = False,
) -> int:
    project = project.resolve()
    scope = scope if scope in work_features.CONTEXT_SCOPES else "general"
    topic = str(topic or "project direction").strip() or "project direction"
    if dry_run:
        payload = _work_quickstart_dry_run_payload(project, scope, topic)
        _print_json_or_text(json_output, payload, payload["summary"])
        return 0

    written: list[str] = []
    written.extend(_ensure_controlwork_layout(project))
    if not _controlwork_config_path(project).exists():
        _write_json(_controlwork_config_path(project), _controlwork_config())
        written.append(_relative_path(project, _controlwork_config_path(project)))
    name = str(project_name or project.name or "ControlWork Project").strip()
    work_purpose = str(purpose or "Project knowledge and work memory").strip()

    scan_payload = work_features.refresh_file_index(project)
    analysis = work_features.build_scan_analysis(project, scan_payload, limit=scan_limit)
    index_path = work_features.file_index_path(project)
    written.append(work_features.rel(project, index_path))

    analysis_path = work_features.scan_analysis_path(project)
    work_features.write_json(analysis_path, analysis)
    written.append(work_features.rel(project, analysis_path))

    understanding = work_features.write_project_understanding(
        project,
        scan_payload,
        analysis=analysis,
        purpose_hint=work_purpose,
        distribution="embedded_controlcoding",
    )
    written.append(work_features.rel(project, work_features.project_understanding_path(project)))

    if not _controlwork_context_path(project).exists():
        _controlwork_context_path(project).write_text(
            _controlwork_context_template(name, work_purpose),
            encoding="utf-8",
        )
        written.append(_relative_path(project, _controlwork_context_path(project)))

    base_document = work_features.ensure_project_base_document(
        project,
        project_name=name,
        purpose=work_features.project_understanding_summary(understanding, fallback=work_purpose),
        understanding=understanding,
    )
    if base_document["created"]:
        written.append(base_document["path"])

    written.extend(work_features.write_views(project))

    retrieval = work_features.retrieve_matches(
        project,
        topic,
        limit=limit,
        include_legacy=include_legacy,
    )
    packet = work_features.build_context_pack(
        project,
        scope=scope,
        topic=topic,
        limit=limit,
        include_legacy=include_legacy,
    )
    packet_path = project / work_features.CONTEXT_PACKET_ROOT / f"quickstart-{work_features.slug(scope)}-{work_features.slug(topic or 'context')}.md"
    target = work_features.write_context_pack(project, packet, scope, topic, output=packet_path)
    written.append(work_features.rel(project, target))

    checkpoint_payload: dict = {"created": False, "reason": "not_requested", "written": []}
    if checkpoint:
        checkpoint_payload = work_features.create_checkpoint(
            project,
            title=checkpoint_title or "ControlCoding Project Plane quickstart checkpoint",
            reason="milestone",
            note="Explicit checkpoint requested by `cc memory work-quickstart --checkpoint`.",
        )
        written.extend(checkpoint_payload.get("written", []))

    payload = {
        "ok": True,
        "dryRun": False,
        "projectRoot": str(project),
        "written": sorted(set(written)),
        "status": _work_status_payload(project),
        "scanSummary": scan_payload.get("summary", {}),
        "scanAnalysisSummary": analysis.get("summary", {}),
        "projectUnderstanding": {
            "path": work_features.rel(project, work_features.project_understanding_path(project)),
            "status": understanding.get("status", "needs_human_confirmation"),
            "selectedKind": understanding.get("selectedKind", "archive_organization"),
            "primaryPurpose": understanding.get("primaryPurpose", "confirm_project_goal"),
            "questions": understanding.get("questions", []),
            "blockedUntilApproval": understanding.get("blockedUntilApproval", []),
        },
        "retrievalSummary": {
            "query": topic,
            "matches": len(retrieval.get("matches", [])),
            "excluded": retrieval.get("exclusionReport", {}).get("count", 0),
            "warnings": retrieval.get("scanEvidenceWarnings", {}).get("warnings", []),
        },
        "contextPacket": work_features.rel(project, target),
        "checkpoint": checkpoint_payload,
        "staleViewWarnings": _work_quickstart_stale_warnings(project),
        "nextCommands": _work_quickstart_next_commands(project, scope, topic),
        "externalCalls": {
            "aiCalls": False,
            "networkCalls": False,
            "subprocessCalls": False,
        },
        "hostAdapterPolicy": (
            "Embedded quickstart does not overwrite ControlCoding host adapters. "
            f"Use `cc export host-context --source {CONTROLWORK_CONTEXT_FILENAME} --host <host>` only when an explicit ControlWork projection is wanted."
        ),
        "projectionPolicy": "Generated views, packets, scan indexes, and graph output are projections or review evidence. CONTROLWORK.md and reviewed .controlwork/memory entries remain authoritative.",
    }
    _print_json_or_text(
        json_output,
        payload,
        (
            "Embedded ControlWork quickstart complete\n"
            f"  Written/refreshed: {len(payload['written'])}\n"
            f"  Context packet: {payload['contextPacket']}\n"
            f"  Retrieval matches: {payload['retrievalSummary']['matches']}"
        ),
    )
    return 0


def cmd_memory_work_attach(
    project: Path,
    path: str | Path,
    json_output: bool = False,
) -> int:
    external_path = _resolve_work_path(path)
    validation = _controlwork_validation(external_path)
    if not validation["ok"]:
        payload = {"ok": False, "externalPath": str(external_path), "issues": validation["issues"]}
        _print_json_or_text(json_output, payload, "Invalid ControlWork project: " + "; ".join(validation["issues"]))
        return 1

    _controlwork_root(project).mkdir(parents=True, exist_ok=True)
    link = {
        "schemaVersion": 1,
        "mode": "linked_external_controlwork",
        "externalPath": str(external_path),
        "externalContext": str(external_path / CONTROLWORK_CONTEXT_FILENAME),
        "syncPolicy": "manual_explicit",
        "attachedAt": _now_iso(),
    }
    _write_json(_controlwork_link_path(project), link)
    payload = {
        "ok": True,
        "projectRoot": str(project),
        "externalPath": str(external_path),
        "linkPath": f"{CONTROLWORK_DIRNAME}/link.json",
        "syncCommands": [
            "cc memory work-sync --direction pull --force",
            "cc memory work-sync --direction push --force",
        ],
    }
    _print_json_or_text(
        json_output,
        payload,
        (
            "Attached external ControlWork project\n"
            f"  External path: {external_path}\n"
            f"  Link: {CONTROLWORK_DIRNAME}/link.json\n"
            "  Sync policy: manual_explicit"
        ),
    )
    return 0


def cmd_memory_work_import(
    project: Path,
    path: str | Path,
    force: bool = False,
    json_output: bool = False,
) -> int:
    source_path = _resolve_work_path(path)
    ok_copy, error, written = _copy_controlwork_into_project(source_path, project, force=force)
    payload = {
        "ok": ok_copy,
        "projectRoot": str(project),
        "sourcePath": str(source_path),
        "written": written,
        "error": error,
    }
    _print_json_or_text(
        json_output,
        payload,
        (
            "Imported ControlWork into ControlCoding project\n"
            f"  Source: {source_path}\n"
            f"  Written: {len(written)}"
        ) if ok_copy else f"ControlWork import failed: {error}",
    )
    return 0 if ok_copy else 1


def cmd_memory_work_export(
    project: Path,
    target: str | Path,
    force: bool = False,
    json_output: bool = False,
) -> int:
    target_path = _resolve_work_path(target)
    ok_export, error, written = _export_controlwork_from_project(project, target_path, force=force)
    payload = {
        "ok": ok_export,
        "projectRoot": str(project),
        "targetPath": str(target_path),
        "written": written,
        "error": error,
    }
    _print_json_or_text(
        json_output,
        payload,
        (
            "Exported embedded ControlWork to standalone folder\n"
            f"  Target: {target_path}\n"
            f"  Written: {len(written)}"
        ) if ok_export else f"ControlWork export failed: {error}",
    )
    return 0 if ok_export else 1


def cmd_memory_work_sync(
    project: Path,
    direction: str,
    force: bool = False,
    json_output: bool = False,
) -> int:
    if direction not in {"pull", "push"}:
        payload = {"ok": False, "error": "direction must be pull or push"}
        _print_json_or_text(json_output, payload, payload["error"])
        return 1
    link = _read_json(_controlwork_link_path(project))
    if not link:
        payload = {"ok": False, "error": f"{CONTROLWORK_DIRNAME}/link.json is missing; run work-attach first"}
        _print_json_or_text(json_output, payload, payload["error"])
        return 1
    external_path = _resolve_work_path(str(link.get("externalPath", "")))
    validation = _controlwork_validation(external_path)
    if not validation["ok"]:
        payload = {"ok": False, "externalPath": str(external_path), "issues": validation["issues"]}
        _print_json_or_text(json_output, payload, "Invalid linked ControlWork project: " + "; ".join(validation["issues"]))
        return 1
    if not force:
        payload = {
            "ok": False,
            "error": "manual sync requires --force because it overwrites the destination ControlWork artifacts",
        }
        _print_json_or_text(json_output, payload, payload["error"])
        return 1

    if direction == "pull":
        ok_sync, error, written = _copy_controlwork_into_project(
            external_path,
            project,
            force=True,
            preserve_link=link,
        )
        action = "Pulled external ControlWork into ControlCoding project"
    else:
        ok_sync, error, written = _export_controlwork_from_project(project, external_path, force=True)
        action = "Pushed embedded ControlWork to external folder"

    payload = {
        "ok": ok_sync,
        "direction": direction,
        "projectRoot": str(project),
        "externalPath": str(external_path),
        "written": written,
        "error": error,
    }
    _print_json_or_text(
        json_output,
        payload,
        (
            f"{action}\n"
            f"  External path: {external_path}\n"
            f"  Written: {len(written)}"
        ) if ok_sync else f"ControlWork sync failed: {error}",
    )
    return 0 if ok_sync else 1


def cmd_memory_work_category_list(project: Path, status: str = "", json_output: bool = False) -> int:
    return work_features.cmd_category_list(Namespace(project_root=project, status=status))


def cmd_memory_work_category_propose(
    project: Path,
    name: str,
    area: str = "notes",
    description: str = "",
    json_output: bool = False,
) -> int:
    return work_features.cmd_category_propose(
        Namespace(project_root=project, name=name, area=area, description=description)
    )


def cmd_memory_work_category_add(
    project: Path,
    name: str,
    area: str = "notes",
    description: str = "",
    json_output: bool = False,
) -> int:
    return work_features.cmd_category_add(
        Namespace(project_root=project, name=name, area=area, description=description)
    )


def cmd_memory_work_category_approve(project: Path, category: str, json_output: bool = False) -> int:
    return work_features.cmd_category_approve(Namespace(project_root=project, category=category))


def cmd_memory_work_scan(project: Path, json_output: bool = False) -> int:
    return work_features.cmd_scan(Namespace(project_root=project, json_output=json_output))


def cmd_memory_work_analyze(project: Path, limit: int = 50, json_output: bool = False) -> int:
    return work_features.cmd_scan_analyze(Namespace(project_root=project, limit=limit, json_output=json_output))


def cmd_memory_work_review(
    project: Path,
    path: str = "",
    review_status: str = "",
    sensitivity: str = "",
    note: str = "",
    filter_name: str = "pending",
    batch: bool = False,
    item: str = "",
    action: str = "",
    canonical: str = "",
    fingerprint: str = "",
    proposal: bool = False,
    output: str | Path | None = None,
    force_output: bool = False,
    limit: int = 20,
    json_output: bool = False,
) -> int:
    return work_features.cmd_scan_review(
        Namespace(
            project_root=project,
            path=path,
            review_status=review_status,
            sensitivity=sensitivity,
            note=note,
            filter=filter_name,
            batch=batch,
            item=item,
            action=action,
            canonical=canonical,
            fingerprint=fingerprint,
            proposal=proposal,
            output=Path(output) if output else None,
            limit=limit,
            json_output=json_output,
        )
    )


def cmd_memory_work_import_source(
    project: Path,
    path: str,
    area: str = "sources",
    title: str = "",
    summary: str = "",
    lifecycle: str = "needs_review",
    category: str = "",
    max_chars: int = 12000,
    as_reference: bool = False,
    dry_run: bool = False,
    proposal: bool = False,
    output: str | Path | None = None,
    force: bool = False,
    json_output: bool = False,
) -> int:
    return work_features.cmd_scan_import(
        Namespace(
            project_root=project,
            path=path,
            area=area,
            title=title,
            summary=summary,
            lifecycle=lifecycle,
            category=category,
            max_chars=max_chars,
            as_reference=as_reference,
            dry_run=dry_run,
            proposal=proposal,
            output=Path(output) if output else None,
            force=force,
            json_output=json_output,
        )
    )


def cmd_memory_work_ocr(
    project: Path,
    action: str,
    source: str = "",
    sidecar: str | Path | None = None,
    output: str | Path | None = None,
    area: str = "sources",
    title: str = "",
    summary: str = "",
    lifecycle: str = "needs_review",
    category: str = "",
    import_source: bool = False,
    dry_run: bool = False,
    force: bool = False,
    limit: int = 20,
    json_output: bool = False,
) -> int:
    if action == "status":
        return work_features.cmd_ocr_status(Namespace(project_root=project, limit=limit, json_output=json_output))
    if action == "run":
        return work_features.cmd_ocr_run(
            Namespace(project_root=project, source=source, output=Path(output) if output else None, force=force)
        )
    if action == "import-sidecar":
        return work_features.cmd_ocr_import_sidecar(
            Namespace(
                project_root=project,
                source=source,
                sidecar=Path(sidecar) if sidecar else Path(""),
                output=Path(output) if output else None,
                area=area,
                title=title,
                summary=summary,
                lifecycle=lifecycle,
                category=category,
                import_source=import_source,
                dry_run=dry_run,
                force=force,
                json_output=json_output,
            )
        )
    print(json.dumps({"ok": False, "error": "unknown work-ocr action"}, indent=2))
    return 1


def cmd_memory_work_promote(
    project: Path,
    path: str,
    area: str = "sources",
    title: str = "",
    summary: str = "",
    lifecycle: str = "captured",
    category: str = "",
    include_excerpt: bool = False,
    force: bool = False,
    force_note: str = "",
    json_output: bool = False,
) -> int:
    return work_features.cmd_scan_promote(
        Namespace(
            project_root=project,
            path=path,
            area=area,
            title=title,
            summary=summary,
            lifecycle=lifecycle,
            category=category,
            include_excerpt=include_excerpt,
            force=force,
            force_note=force_note,
            json_output=json_output,
        )
    )


WORK_CAPTURE_LIFECYCLES = {"captured", "active", "needs_review", "superseded", "legacy"}


def cmd_memory_work_capture(
    project: Path,
    area: str,
    title: str,
    body: str,
    lifecycle: str = "captured",
    source: str = "",
    category: str = "",
    json_output: bool = False,
) -> int:
    project = project.resolve()
    if area not in work_features.CAPTURE_AREAS:
        print(json.dumps({"ok": False, "error": f"unknown memory area: {area}"}, indent=2))
        return 1
    if lifecycle not in WORK_CAPTURE_LIFECYCLES:
        print(json.dumps({"ok": False, "error": f"unknown lifecycle: {lifecycle}"}, indent=2))
        return 1
    _ensure_controlwork_layout(project)
    if not _controlwork_config_path(project).exists():
        _write_json(_controlwork_config_path(project), _controlwork_config())
    if not _controlwork_context_path(project).exists():
        _controlwork_context_path(project).write_text(
            _controlwork_context_template(project.name or "ControlWork Project", "Project knowledge and work memory"),
            encoding="utf-8",
        )
    approved_category = ""
    if category:
        try:
            approved_category = work_features.validate_category(project, category)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
    stamp = work_features.utc_stamp()
    filename = f"{stamp}-{work_features.slug(title)}.md"
    path = project / work_features.MEMORY_ROOT / area / filename
    lines = [
        f"# {title}",
        "",
        f"- **Area**: {area}",
        f"- **Lifecycle**: {lifecycle}",
        f"- **Captured**: {stamp}",
    ]
    if source:
        lines.append(f"- **Source**: {source}")
    if approved_category:
        lines.append(f"- **Category**: {approved_category}")
    lines.extend(["", "## Body", "", body.strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    payload = {"ok": True, "path": _relative_path(project, path)}
    _print_json_or_text(json_output, payload, f"Captured Work Plane memory: {payload['path']}")
    return 0


def cmd_memory_work_session(
    project: Path,
    action: str,
    session_id: str = "",
    topic: str = "",
    mode: str = "continue_previous_work",
    operator: str = "manual",
    summary: str = "",
    category: list[str] | None = None,
    status: str = "completed",
    followup: list[str] | None = None,
    decision: list[str] | None = None,
    link_type: str = "",
    target: str = "",
    target_type: str = "",
    text: str = "",
    kind: str = "note",
    topic_filter: str = "",
    limit: int = 20,
    json_output: bool = False,
) -> int:
    args = Namespace(
        project_root=project,
        session_id=session_id,
        topic=topic,
        mode=mode,
        operator=operator,
        summary=summary,
        category=category or [],
        status=status,
        followup=followup or [],
        decision=decision or [],
        type=link_type,
        target=target,
        target_type=target_type,
        text=text,
        kind=kind,
        limit=limit,
    )
    if action == "start":
        return work_features.cmd_session_start(args)
    if action == "close":
        return work_features.cmd_session_close(args)
    if action == "link":
        return work_features.cmd_session_link(args)
    if action == "note":
        return work_features.cmd_session_note(args)
    if action == "list":
        args.status = status
        args.topic = topic_filter
        return work_features.cmd_session_list(args)
    if action == "show":
        return work_features.cmd_session_show(args)
    print(json.dumps({"ok": False, "error": "unknown work-session action"}, indent=2))
    return 1


def cmd_memory_work_graph(
    project: Path,
    action: str,
    status: str = "suggested",
    confidence: str = "",
    limit: int = 50,
    suggestion_id: str = "",
    reason: str = "",
    selector: str = "",
    source_selector: str = "",
    target_selector: str = "",
    max_depth: int = 3,
    include_suggestions: bool = False,
    include_chunks: bool = False,
    no_scan: bool = False,
    baseline: str | Path | None = None,
    output: str | Path | None = None,
    output_format: str = "json",
    node_types: list[str] | None = None,
    lifecycles: list[str] | None = None,
    source: str = "",
    json_output: bool = False,
) -> int:
    args = Namespace(
        project_root=project,
        status=status,
        confidence=confidence,
        limit=limit,
        suggestion_id=suggestion_id,
        reason=reason,
        selector=selector,
        source_selector=source_selector,
        target_selector=target_selector,
        max_depth=max_depth,
        include_suggestions=include_suggestions,
        include_chunks=include_chunks,
        no_scan=no_scan,
        baseline=Path(baseline) if baseline else None,
        output=Path(output) if output else None,
        format=output_format,
        type=node_types or [],
        lifecycle=lifecycles or [],
        source=source,
    )
    if action == "status":
        return work_features.cmd_graph_status(args)
    if action == "suggestions":
        return work_features.cmd_graph_suggestions(args)
    if action == "accept":
        return work_features.cmd_graph_accept(args)
    if action == "reject":
        return work_features.cmd_graph_reject(args)
    if action == "explain":
        return work_features.cmd_graph_explain(args)
    if action == "path":
        return work_features.cmd_graph_path(args)
    if action == "neighbors":
        return work_features.cmd_graph_neighbors(args)
    if action == "stale":
        return work_features.cmd_graph_stale(args)
    if action == "unresolved":
        return work_features.cmd_graph_unresolved(args)
    if action == "diff":
        return work_features.cmd_graph_diff(args)
    if action == "export":
        return work_features.cmd_graph_export(args)
    print(json.dumps({"ok": False, "error": "unknown work-graph action"}, indent=2))
    return 1


def cmd_memory_work_dashboard(
    project: Path,
    output_format: str = "html",
    output: str | Path | None = None,
    limit: int = 20,
    scan_limit: int = 50,
    no_refresh_scan: bool = False,
    json_output: bool = False,
) -> int:
    return work_features.cmd_dashboard(
        Namespace(
            project_root=project,
            format=output_format,
            output=Path(output) if output else None,
            limit=limit,
            scan_limit=scan_limit,
            no_refresh_scan=no_refresh_scan,
        )
    )


def cmd_memory_work_retrieve(
    project: Path,
    query: str,
    limit: int = 10,
    include_legacy: bool = False,
    json_output: bool = False,
) -> int:
    return work_features.cmd_retrieve(
        Namespace(project_root=project, query=query, limit=limit, include_legacy=include_legacy)
    )


def cmd_memory_work_query(
    project: Path,
    query: str,
    limit: int = 10,
    include_legacy: bool = False,
    path_to: str = "",
    max_depth: int = 3,
    include_suggestions: bool = False,
    output: str | Path | None = None,
    stdout: bool = False,
    json_output: bool = False,
) -> int:
    return work_features.cmd_query(
        Namespace(
            project_root=project,
            query=query,
            limit=limit,
            include_legacy=include_legacy,
            path_to=path_to,
            max_depth=max_depth,
            include_suggestions=include_suggestions,
            output=Path(output) if output else None,
            stdout=stdout,
            json_output=json_output,
            command_surface="embedded",
        )
    )


def cmd_memory_work_rag_pack(
    project: Path,
    query: str,
    limit: int = 10,
    include_legacy: bool = False,
    output: str | Path | None = None,
    stdout: bool = False,
    json_output: bool = False,
) -> int:
    return work_features.cmd_rag_pack(
        Namespace(
            project_root=project,
            query=query,
            limit=limit,
            include_legacy=include_legacy,
            output=Path(output) if output else None,
            stdout=stdout,
            json_output=json_output,
        )
    )

def cmd_memory_work_views(project: Path, json_output: bool = False) -> int:
    return work_features.cmd_views_generate(Namespace(project_root=project))


def cmd_memory_work_checkpoint(
    project: Path,
    title: str = "",
    reason: str = "manual",
    note: str = "",
    auto: bool = False,
    check: bool = False,
    json_output: bool = False,
) -> int:
    return work_features.cmd_checkpoint(
        Namespace(
            project_root=project,
            title=title,
            reason=reason,
            note=note,
            auto=auto,
            check=check,
        )
    )


def cmd_memory_work_handoff(project: Path, output: str | Path | None = None, json_output: bool = False) -> int:
    output_path = Path(output) if output else None
    return work_features.cmd_handoff(Namespace(project_root=project, output=output_path))


def cmd_memory_work_context_pack(
    project: Path,
    scope: str = "general",
    topic: str = "",
    limit: int = 10,
    include_legacy: bool = False,
    output: str | Path | None = None,
    stdout: bool = False,
    json_output: bool = False,
) -> int:
    return work_features.cmd_context_pack(
        Namespace(
            project_root=project,
            scope=scope,
            topic=topic,
            limit=limit,
            include_legacy=include_legacy,
            output=Path(output) if output else None,
            stdout=stdout,
            json_output=json_output,
        )
    )


def cmd_memory_work_obsidian(project: Path, action: str, json_output: bool = False) -> int:
    args = Namespace(project_root=project)
    if action == "init":
        return work_features.cmd_obsidian_init(args)
    if action == "sync":
        return work_features.cmd_obsidian_sync(args)
    if action == "check":
        return work_features.cmd_obsidian_check(args)
    print(json.dumps({"ok": False, "error": "action must be init, sync, or check"}, indent=2))
    return 1


def cmd_memory_work_wiki(project: Path, action: str, review: bool = False, json_output: bool = False) -> int:
    args = Namespace(project_root=project, review=review)
    if action == "build":
        return work_features.cmd_wiki_build(args)
    if action == "import-edits":
        return work_features.cmd_wiki_import_edits(args)
    print(json.dumps({"ok": False, "error": "action must be build or import-edits"}, indent=2))
    return 1


def cmd_memory_work_mcp_tools(json_output: bool = False) -> int:
    return work_features.cmd_mcp_tools(Namespace())


def cmd_memory_work_mcp_call(
    project: Path,
    tool: str,
    args_file: str | Path | None = None,
    json_output: bool = False,
) -> int:
    return work_features.cmd_mcp_call(
        Namespace(project_root=project, tool=tool, args_file=Path(args_file) if args_file else None)
    )


def _dev_context_root(project: Path) -> Path:
    return project / CONTROL_DIRNAME / "context-packets"


def _context_source_excerpt(project: Path, max_chars: int = 2800) -> tuple[str, str]:
    for filename in ("CONTROLCODING.md", "AGENTS.md", "CLAUDE.md"):
        path = project / filename
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if len(text) > max_chars:
                text = text[:max_chars].rstrip() + "\n\n[Excerpt truncated]"
            return filename, text
    return "", "No ControlCoding host context file found."


def _format_dev_entity(entity: dict) -> list[str]:
    path = entity.get("path") or ""
    path_part = f" - `{path}`" if path else ""
    line = f"- **{entity.get('title') or entity.get('id')}** ({entity.get('type')}, {entity.get('lifecycle')}){path_part}"
    lines = [line]
    reasons = entity.get("_impactReasons")
    if isinstance(reasons, list) and reasons:
        lines.append(f"  - reasons: {'; '.join(str(reason) for reason in reasons[:4])}")
    body = str(entity.get("body") or "").strip().replace("\n", " ")
    if body:
        lines.append(f"  - summary: {body[:420]}")
    return lines


def _append_dev_group(lines: list[str], title: str, items: list[dict], empty: str) -> None:
    lines.extend(["", f"## {title}", ""])
    if not items:
        lines.append(f"- {empty}")
        return
    for entity in items[:10]:
        lines.extend(_format_dev_entity(entity))


def _build_dev_context_pack(project: Path, scope: str, topic: str, limit: int = 10) -> str:
    ok, message = _require_initialized(project)
    if not ok:
        raise RuntimeError(message)
    query = topic.strip() or scope.strip() or "project"
    entities = _impact_entities(project, query)
    groups = _group_impact(entities)
    context_label, context_excerpt = _context_source_excerpt(project)
    limit = max(1, min(int(limit or 10), 30))
    lines = [
        f"# ControlCoding Dev Context Packet: {query}",
        "",
        f"- **Generated**: {_now_iso()}",
        f"- **Scope**: {scope or 'general'}",
        f"- **Topic**: {topic or '(none)'}",
        f"- **Selection Rule**: use current decisions, docs, files, and agent evidence; treat lifecycle attention as warning-only until reviewed.",
        "",
        "## AI Use Contract",
        "",
        "- Use this packet for the current development or code task.",
        "- Check impacted files before editing to avoid duplicate or overwritten functions.",
        "- Prefer active decisions and scanned source files over stale notes.",
        "- Treat stale, conflicting, or needs_review records as warning material, not current truth.",
        "",
        f"## Operating Context Excerpt: {context_label or 'missing'}",
        "",
        context_excerpt,
    ]
    _append_dev_group(lines, "Impacted Files", groups.get("files", [])[:limit], "No file matches found. Run `cc memory scan` if needed.")
    _append_dev_group(lines, "Docs And Plans", groups.get("docsAndPlans", [])[:limit], "No related docs or plans found.")
    _append_dev_group(lines, "Decisions", groups.get("decisions", [])[:limit], "No related decisions found.")
    _append_dev_group(lines, "Notes And Ideas", groups.get("notesAndIdeas", [])[:limit], "No related notes or ideas found.")
    _append_dev_group(lines, "Consults And Agent Runs", (groups.get("consults", []) + groups.get("agentRuns", []))[:limit], "No related consults or agent runs found.")
    _append_dev_group(lines, "Tests And Benchmarks", groups.get("testsAndBenchmarks", [])[:limit], "No related tests or benchmarks found.")
    _append_dev_group(lines, "Lifecycle Attention", groups.get("staleOrNeedsReview", [])[:limit], "No stale, conflicting, or needs_review matches found.")
    lines.extend([
        "",
        "## Suggested Chat Opening",
        "",
        f"Use this dev context packet for scope `{scope or 'general'}` and topic `{query}`. Before editing, verify impacted files with code search and keep stale records separate from active truth.",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def cmd_memory_dev_context_pack(
    project: Path,
    scope: str = "general",
    topic: str = "",
    limit: int = 10,
    output: str | Path | None = None,
    stdout: bool = False,
    json_output: bool = False,
) -> int:
    try:
        content = _build_dev_context_pack(project, scope=scope, topic=topic, limit=limit)
    except RuntimeError as exc:
        payload = (
            {"ok": False, "error": "dev_context_pack_failed", "message": str(exc)}
            if json_output and stdout
            else {"ok": False, "error": str(exc)}
        )
        _print_json_or_text(json_output, payload, f"Dev context-pack failed: {exc}")
        return 1
    if json_output and stdout:
        print(json.dumps({
            "ok": True,
            "scope": scope,
            "topic": topic,
            "packetMarkdown": content,
        }, indent=2))
        return 0
    if stdout:
        print(content, end="")
        return 0
    if output:
        try:
            target = work_features.resolve_output_path(project, Path(output))
        except ValueError as exc:
            payload = {"ok": False, "error": str(exc)}
            _print_json_or_text(json_output, payload, f"Dev context-pack failed: {exc}")
            return 1
    else:
        target = _dev_context_root(project) / f"{_now_iso().replace(':', '').replace('-', '').replace('.', '')}-{work_features.slug(scope)}-{work_features.slug(topic or 'context')}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    payload = {"ok": True, "path": _relative_path(project, target), "scope": scope, "topic": topic}
    _print_json_or_text(json_output, payload, f"Generated dev context packet: {_relative_path(project, target)}")
    return 0


def cmd_memory_init(
    project: Path,
    mode: str = "full",
    project_short: str = "",
    profile: str = "project",
    json_output: bool = False,
) -> int:
    if mode not in VALID_INSTALL_MODES:
        message = f"invalid memory mode: {mode}"
        _print_json_or_text(
            json_output,
            {"ok": False, "error": "memory_init_invalid_mode", "message": message},
            f"Error: {message}",
        )
        return 1
    normalized_profile = str(profile or "project").strip().lower()
    if normalized_profile not in VALID_MEMORY_PROFILES:
        message = f"invalid memory profile: {profile}"
        _print_json_or_text(
            json_output,
            {"ok": False, "error": "memory_init_invalid_profile", "message": message},
            f"Error: {message}",
        )
        return 1
    if normalized_profile == "work" and mode != "document-only":
        message = "the work memory profile requires --mode document-only"
        _print_json_or_text(
            json_output,
            {
                "ok": False,
                "error": "memory_init_work_profile_requires_document_only",
                "message": message,
            },
            f"Error: {message}",
        )
        return 1

    created_directories = list(WORK_MEMORY_PROFILE_DIRS) if normalized_profile == "work" else []

    storage_mode = INSTALL_MODE_STORAGE[mode]
    with _memory_connection(project, compensate_committed_failure=True) as conn:
        short = _project_short(project, project_short)
        now = _now_iso()
        manifest_path = _manifest_path(project)
        previous = _read_json(manifest_path)
        created_at = str(previous.get("created_at") or now)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "install_mode": storage_mode,
            "profile": normalized_profile,
            "project_short": short,
            "enabled_modules": ["document_memory"]
            if mode == "document-only"
            else ["document_memory", "project_memory"],
            "work_memory_layout": created_directories if normalized_profile == "work" else [],
            "created_by": "cc memory init",
            "created_at": created_at,
            "updated_at": now,
            "controlcoding_version": CONTROLCODING_VERSION,
        }
        _ensure_schema(conn)
        _transactional_ensure_layout(conn, project)
        for relative in created_directories:
            _transactional_ensure_directory(conn, project / relative)
        _transactional_write_json(conn, manifest_path, manifest)
        _set_metadata(conn, "install_mode", storage_mode)
        _set_metadata(conn, "profile", normalized_profile)
        _set_metadata(conn, "project_short", short)
        _set_metadata(conn, "updated_at", now)
        _insert_event(
            conn,
            project,
            "create",
            "cc memory init",
            affected_paths=[
                f"{CONTROL_DIRNAME}/{MANIFEST_FILENAME}",
                f"{CONTROL_DIRNAME}/{MEMORY_DIRNAME}/{DB_FILENAME}",
                *created_directories,
            ],
            after_state={
                "install_mode": storage_mode,
                "profile": normalized_profile,
                "project_short": short,
                "work_memory_layout": created_directories,
            },
            reason="Initialize Project Memory Engine",
        )
        _generate_views_in_transaction(project, conn, command="cc memory init")

    payload = {
        "ok": True,
        "projectRoot": str(project),
        "manifest": f"{CONTROL_DIRNAME}/{MANIFEST_FILENAME}",
        "database": f"{CONTROL_DIRNAME}/{MEMORY_DIRNAME}/{DB_FILENAME}",
        "installMode": storage_mode,
        "profile": normalized_profile,
        "projectShort": short,
        "createdDirectories": created_directories,
    }
    directory_lines = ""
    if created_directories:
        directory_lines = "\n  Work layout: " + ", ".join(created_directories)
    _print_json_or_text(
        json_output,
        payload,
        (
            "Project memory initialized\n"
            f"  Manifest: {payload['manifest']}\n"
            f"  Database: {payload['database']}\n"
            f"  Mode: {storage_mode}\n"
            f"  Profile: {normalized_profile}\n"
            f"  Project short: {short}"
            f"{directory_lines}"
        ),
    )
    return 0


def cmd_memory_doctor(project: Path, json_output: bool = False) -> int:
    checks: list[dict[str, str]] = []
    issues = 0
    warnings = 0
    schema_payload: dict[str, object] = {
        "schemaState": "missing",
        "userVersion": 0,
        "metadataVersion": None,
        "targetVersion": SCHEMA_VERSION,
    }

    def add(name: str, status: str, detail: str = "") -> None:
        nonlocal issues, warnings
        checks.append({"name": name, "status": status, "detail": detail})
        if status == "fail":
            issues += 1
        elif status == "warn":
            warnings += 1

    manifest = _read_json(_manifest_path(project))
    if manifest:
        add("manifest", "ok", f"project_short={manifest.get('project_short', '')}; mode={manifest.get('install_mode', '')}")
    else:
        add("manifest", "fail", f"{CONTROL_DIRNAME}/{MANIFEST_FILENAME} missing or invalid")

    for rel_name, path in (
        ("memory_dir", _memory_dir(project)),
        ("logs_dir", _logs_dir(project)),
        ("views_dir", _views_dir(project)),
    ):
        add(rel_name, "ok" if path.is_dir() else "fail", path.as_posix())

    if _db_path(project).exists():
        try:
            with _readonly_memory_connection(project) as conn:
                inspection = inspect_schema(conn)
            schema_payload = inspection.to_payload()
            schema_detail = (
                f"schemaState={inspection.state}; "
                f"userVersion={inspection.user_version}; "
                f"metadataVersion={inspection.metadata_version}; "
                f"targetVersion={inspection.target_version}"
            )
            if inspection.state == "current":
                add("database_schema", "ok", schema_detail)
            elif inspection.state in {"legacy_adoptable", "old_compatible"}:
                add("database_schema", "warn", schema_detail + "; migration available")
            else:
                detail_parts = [schema_detail]
                detail_parts.extend(inspection.issues)
                for table in inspection.missing_tables:
                    detail_parts.append(f"missing table: {table}")
                for table, columns in inspection.missing_columns.items():
                    detail_parts.append(f"missing columns in {table}: {', '.join(columns)}")
                add("database_schema", "fail", "; ".join(detail_parts))
        except sqlite3.Error as exc:
            add("database_schema", "fail", str(exc))
    else:
        add("database_schema", "fail", f"{CONTROL_DIRNAME}/{MEMORY_DIRNAME}/{DB_FILENAME} missing")

    for log_name in REQUIRED_LOGS:
        issue = _validate_jsonl(_logs_dir(project) / log_name)
        add(f"log:{log_name}", "fail" if issue else "ok", issue or "valid JSONL")

    missing_views = [view for view in REQUIRED_VIEWS if not (_views_dir(project) / view).exists()]
    if missing_views:
        add("views", "fail", "missing: " + ", ".join(missing_views))
    else:
        add("views", "ok", f"{len(REQUIRED_VIEWS)} required views present")

    project_plane = _work_status_payload(project)
    if project_plane.get("ok"):
        drift = project_plane.get("drift", {})
        semantic = drift.get("semantic", {}) if isinstance(drift, dict) else {}
        derived = drift.get("derivedArtifacts", {}) if isinstance(drift, dict) else {}
        context_packets = derived.get("contextPackets", {}) if isinstance(derived, dict) else {}
        views = derived.get("views", {}) if isinstance(derived, dict) else {}
        readiness_gaps = []
        if semantic.get("actionableDrift"):
            readiness_gaps.append("actionable standalone/embedded drift")
        if views.get("stale"):
            readiness_gaps.append("missing or stale Project Plane views")
        if context_packets.get("stale"):
            readiness_gaps.append("missing Project Plane context packet")
        detail = (
            f"embedded={project_plane.get('hasCanonicalContext')}; "
            f"attached={project_plane.get('attached')}; "
            f"contextPackets={context_packets.get('count', 0)}"
        )
        if readiness_gaps:
            add("project_plane", "warn", detail + "; " + "; ".join(readiness_gaps))
        else:
            add("project_plane", "ok", detail)
    else:
        add("project_plane", "info", "not initialized; run `cc memory work-quickstart`")

    pytest_temp_dirs = diagnose_pytest_temp_dirs(project)
    temp_warning = format_pytest_temp_warning(pytest_temp_dirs)
    if temp_warning:
        add("pytest_temp_dirs", "warn", temp_warning)

    payload = {
        "ok": issues == 0,
        "issues": issues,
        "warnings": warnings,
        "checks": checks,
        "schema": schema_payload,
        "pytestTempDirs": pytest_temp_dirs,
    }
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Project Memory Doctor - {project}")
        for check in checks:
            label = check["status"].upper()
            detail = f" - {check['detail']}" if check["detail"] else ""
            print(f"  {label} {check['name']}{detail}")
        if issues == 0:
            suffix = f" ({warnings} warning(s))." if warnings else "."
            print(f"Memory is healthy{suffix}")
        else:
            print(f"{issues} memory issue(s) found.")
    return 0 if issues == 0 else 1


def add_memory_scan_parser(memory_sub, path_type, argparse_module) -> None:
    parser = memory_sub.add_parser("scan", help="Index project files and documents in place")
    parser.add_argument("--project-root", type=path_type, default=argparse_module.SUPPRESS, help="Project root directory (default: current directory)")
    parser.add_argument("--scope", default="full", help="Scan scope: full or governed. Use governed for ControlCoding/ControlWork governed surfaces only.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")


def cmd_memory_scan(project: Path, json_output: bool = False, scope: str = "full") -> int:
    normalized_scope = str(scope or "full").strip().lower()
    if normalized_scope not in {"full", "governed"}:
        message = f"invalid memory scan scope: {scope}. Choose one of: full, governed"
        _print_json_or_text(
            json_output,
            {"ok": False, "error": "memory_scan_invalid_scope", "message": message},
            f"Error: {message}",
        )
        return 2
    try:
        return _cmd_memory_scan_impl(project, json_output=json_output, scope=normalized_scope)
    except sqlite3.Error as exc:
        payload = _memory_db_error_payload(
            project,
            exc,
            operation="memory_scan",
            command=f"python scripts/cc.py memory scan --project-root . --scope {normalized_scope}",
        )
        _print_json_or_text(json_output, payload, _memory_db_error_text(payload))
        return 1


def _cmd_memory_scan_impl(project: Path, json_output: bool = False, scope: str = "full") -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_or_text(
            json_output,
            {"ok": False, "error": "memory_scan_memory_not_initialized", "message": message},
            f"Error: {message}",
        )
        return 1
    normalized_scope = str(scope or "full").strip().lower()
    if normalized_scope not in {"full", "governed"}:
        message = f"invalid memory scan scope: {scope}. Choose one of: full, governed"
        _print_json_or_text(
            json_output,
            {"ok": False, "error": "memory_scan_invalid_scope", "message": message},
            f"Error: {message}",
        )
        return 2

    project_short = _project_short(project)
    files = _iter_scannable_files(project, scope=normalized_scope)
    created = 0
    updated = 0
    unchanged = 0
    marked_missing = 0
    chunks_indexed = 0
    correlation_suggestions = 0
    seen_paths: set[str] = set()
    changed_ids: list[str] = []
    now = _now_iso()

    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        for path in files:
            rel_path = _relative_path(project, path)
            seen_paths.add(rel_path)
            entity_type, plane, lifecycle = _classify_path(rel_path)
            area = _area_from_relpath(rel_path)
            text, decoding_metadata = _safe_read_text_with_metadata(path)
            chunk_extra_metadata = (
                {"decoding_provenance": decoding_metadata}
                if decoding_metadata
                else None
            )
            title = _title_from_file(path)
            file_hash = _content_hash(path)
            classification = _classify_document(rel_path, text, entity_type, lifecycle, title=title)
            source_id = _upsert_source(conn, "file", rel_path, title)
            provenance = {
                "scanner": f"cc memory scan --scope {normalized_scope}",
                "source_kind": "path",
                "absorbs_content": False,
            }
            data = {
                "size_bytes": path.stat().st_size,
                "mtime": int(path.stat().st_mtime),
                "area": area,
                **classification,
            }
            existing = _find_entity_by_path(conn, rel_path)
            if existing is None:
                entity_id = _unique_entity_id(
                    conn,
                    project_short,
                    entity_type,
                    area,
                    str(Path(rel_path).with_suffix("")),
                    _today_yyyymmdd(),
                    path_hint=rel_path,
                )
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
                        title,
                        project_short,
                        plane,
                        rel_path,
                        lifecycle,
                        file_hash,
                        _json_dumps([source_id]),
                        _json_dumps(provenance),
                        "",
                        _json_dumps(data),
                        now,
                        now,
                    ),
                )
                created += 1
                changed_ids.append(entity_id)
                if text.strip() and _should_chunk_document(path):
                    chunks_indexed += _sync_semantic_chunks(
                        conn,
                        entity_id,
                        rel_path,
                        text,
                        lifecycle,
                        extra_metadata=chunk_extra_metadata,
                    )
                _insert_event(
                    conn,
                    project,
                    "create",
                    "cc memory scan",
                    target_entity_ids=[entity_id],
                    affected_paths=[rel_path],
                    after_state={"type": entity_type, "lifecycle": lifecycle},
                    reason="Indexed project file without moving it",
                    provenance=provenance,
                )
            else:
                entity_id = str(existing["id"])
                data_json = _json_dumps(data)
                if (
                    existing["content_hash"] != file_hash
                    or existing["lifecycle"] == "needs_review"
                    or existing["data"] != data_json
                    or existing["type"] != entity_type
                    or existing["title"] != title
                ):
                    before = {
                        "content_hash": existing["content_hash"],
                        "lifecycle": existing["lifecycle"],
                    }
                    conn.execute(
                        """
                        UPDATE entities
                        SET title = ?, type = ?, plane = ?, lifecycle = ?,
                            content_hash = ?, source_refs = ?, provenance = ?,
                            data = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            title,
                            entity_type,
                            plane,
                            lifecycle,
                            file_hash,
                            _json_dumps([source_id]),
                            _json_dumps(provenance),
                            data_json,
                            now,
                            entity_id,
                        ),
                    )
                    updated += 1
                    changed_ids.append(entity_id)
                    _insert_event(
                        conn,
                        project,
                        "edit",
                        "cc memory scan",
                        target_entity_ids=[entity_id],
                        affected_paths=[rel_path],
                        before_state=before,
                        after_state={"content_hash": file_hash, "lifecycle": lifecycle},
                        reason="Detected path-backed content update",
                        provenance=provenance,
                    )
                else:
                    unchanged += 1
                if text.strip() and _should_chunk_document(path):
                    chunks_indexed += _sync_semantic_chunks(
                        conn,
                        entity_id,
                        rel_path,
                        text,
                        lifecycle,
                        extra_metadata=chunk_extra_metadata,
                    )

        existing_path_rows = conn.execute(
            """
            SELECT id, path, lifecycle
            FROM entities
            WHERE path IS NOT NULL AND path != ''
            """
        ).fetchall()
        for row in existing_path_rows:
            rel_path = str(row["path"])
            stale_candidate = normalized_scope == "full" or _is_governed_relpath(rel_path)
            if (
                stale_candidate
                and rel_path
                and rel_path not in seen_paths
                and not rel_path.startswith(f"{CONTROL_DIRNAME}/")
            ):
                if row["lifecycle"] != "needs_review":
                    conn.execute(
                        "UPDATE entities SET lifecycle = ?, updated_at = ? WHERE id = ?",
                        ("needs_review", now, row["id"]),
                    )
                    conn.execute(
                        "UPDATE semantic_chunks SET lifecycle = ?, updated_at = ? WHERE source_path = ?",
                        ("needs_review", now, rel_path),
                    )
                    marked_missing += 1
                    _insert_event(
                        conn,
                        project,
                        "mark_stale",
                        "cc memory scan",
                        target_entity_ids=[str(row["id"])],
                        affected_paths=[rel_path],
                        before_state={"lifecycle": row["lifecycle"]},
                        after_state={"lifecycle": "needs_review"},
                        reason="Previously indexed path is missing",
                        review_required=True,
                    )

        _set_metadata(conn, "last_scan_at", now)
        _set_metadata(conn, "last_scan_scope", normalized_scope)
        _set_metadata(conn, "last_scan_file_count", str(len(files)))
        _set_metadata(conn, "last_scan_chunk_count", str(chunks_indexed))
        graph_refreshed = normalized_scope == "full"
        if graph_refreshed:
            correlation_suggestions = _refresh_correlation_suggestions(conn)
            _set_metadata(conn, "last_correlation_suggestion_count", str(correlation_suggestions))
        else:
            _set_metadata(conn, "last_governed_scan_at", now)
        vector_rebuild = _rebuild_vector_index(conn)
        retrieval_index = _rebuild_retrieval_index(conn)
        _insert_event(
            conn,
            project,
            "scan",
            f"cc memory scan --scope {normalized_scope}",
            target_entity_ids=changed_ids[:200],
            affected_paths=sorted(seen_paths)[:200],
            after_state={
                "scope": normalized_scope,
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "marked_missing": marked_missing,
                "chunks_indexed": chunks_indexed,
                "correlation_suggestions": correlation_suggestions,
                "graph_refreshed": graph_refreshed,
                "vector_rebuild": vector_rebuild,
                "retrieval_index": retrieval_index,
            },
            reason="Scanned project files in place",
        )

    payload = {
        "ok": True,
        "scope": normalized_scope,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "markedMissing": marked_missing,
        "chunksIndexed": chunks_indexed,
        "correlationSuggestions": correlation_suggestions,
        "graphRefreshed": normalized_scope == "full",
        "vectorRebuild": {
            "executed": True,
            **vector_rebuild,
        },
        "retrievalIndex": {
            "executed": True,
            **retrieval_index,
        },
        "scannedFiles": len(files),
    }
    _print_json_or_text(
        json_output,
        payload,
        (
            "Memory scan complete\n"
            f"  Scope: {normalized_scope}\n"
            f"  Scanned files: {len(files)}\n"
            f"  Created: {created}\n"
            f"  Updated: {updated}\n"
            f"  Unchanged: {unchanged}\n"
            f"  Needs review: {marked_missing}\n"
            f"  Semantic chunks: {chunks_indexed}\n"
            f"  Correlation suggestions: {correlation_suggestions}\n"
            f"  Vector rebuild: indexed={vector_rebuild['indexed']}, skipped={vector_rebuild['skipped']}\n"
            f"  Retrieval index: records={retrieval_index['recordCount']}, terms={retrieval_index['termRows']}"
        ),
    )
    return 0

def cmd_memory_intake_add(
    project: Path,
    title: str,
    summary: str = "",
    source_type: str = "other",
    lifecycle: str = "captured",
    path: str = "",
    source_refs: list[str] | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_intake_memory_not_initialized", message)
        return 1
    if not title.strip():
        _print_json_error_or_text(json_output, "intake_title_required", "intake add needs a title")
        return 1

    source_refs = source_refs or []
    relative_path = path.strip().replace("\\", "/")
    if relative_path:
        destination = project / relative_path
    else:
        slug = _slug_for_filename(title)
        destination = project / "docs" / "inbox" / f"{_today_yyyymmdd()}-{slug}.md"

    try:
        destination, _missing_destination_directories = _physical_file_path(
            destination,
            project,
        )
    except RuntimeError:
        _print_json_error_or_text(json_output, "intake_path_outside_project", "intake path must stay inside the project root")
        return 1

    if not relative_path:
        destination = _unique_project_file(destination)

    if destination.exists():
        _print_json_error_or_text(json_output, "intake_file_exists", f"intake file already exists: {_relative_path(project, destination)}")
        return 1

    source_lines = "\n".join(f"- {ref}" for ref in source_refs) or "- none recorded"
    body = summary.strip() or "Add extracted text, analysis, or a concise source summary here."
    content = (
        f"# {title.strip()}\n\n"
        f"Source type: {source_type.strip() or 'other'}\n"
        f"Lifecycle: {lifecycle}\n\n"
        "## Summary\n\n"
        f"{body}\n\n"
        "## Source References\n\n"
        f"{source_lines}\n"
    )
    rel_label = _relative_path(project, destination)
    try:
        entity = _record_entity(
            project,
            "note",
            title,
            body=summary,
            area="Inbox",
            lifecycle=lifecycle,
            path=rel_label,
            source_refs=source_refs,
            data={"source_type": source_type.strip() or "other"},
            event_type="import",
            command="cc memory intake add",
            transactional_filesystem_action=lambda conn: _transactional_write_text(
                conn,
                destination,
                content,
                require_absent=True,
            ),
            require_unique_path=True,
        )
    except FileExistsError:
        _print_json_error_or_text(
            json_output,
            "intake_file_exists",
            f"intake file or entity already exists: {rel_label}",
        )
        return 1
    payload = {"ok": True, "path": rel_label, "entity": entity}
    _print_json_or_text(json_output, payload, f"Recorded intake: {rel_label}")
    return 0

def cmd_memory_intake_promote(
    project: Path,
    selector: str,
    target: str,
    lifecycle: str = "",
    reason: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_intake_memory_not_initialized", message)
        return 1

    canonical_target = _canonical_intake_target(target)
    target_dir = INTAKE_PROMOTION_TARGET_DIRS.get(canonical_target)
    if not target_dir:
        choices = ", ".join(sorted({key.rstrip("s") for key in INTAKE_PROMOTION_TARGET_DIRS}))
        _print_json_error_or_text(json_output, "intake_target_invalid", f"invalid intake promotion target: {target}. Choose one of: {choices}")
        return 1

    final_lifecycle = str(lifecycle or "").strip() or INTAKE_PROMOTION_DEFAULT_LIFECYCLES.get(
        canonical_target,
        "triaged",
    )
    if final_lifecycle not in VALID_LIFECYCLES:
        _print_json_error_or_text(json_output, "intake_lifecycle_invalid", f"invalid lifecycle: {final_lifecycle}")
        return 1

    lookup_selector = selector
    selector_path = Path(selector.strip())
    if selector_path.is_absolute():
        try:
            normalized_selector, _missing_selector_directories = _physical_file_path(
                selector_path,
                project,
            )
            lookup_selector = normalized_selector.relative_to(
                Path(os.path.abspath(project))
            ).as_posix()
        except (RuntimeError, ValueError):
            _print_json_error_or_text(
                json_output,
                "intake_source_outside_project",
                "intake source must resolve to a physical file inside the project root",
            )
            return 1

    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        entity, entity_issue = _find_entity_for_lifecycle(
            conn,
            project,
            lookup_selector,
        )
        if entity is None:
            source_rel = _relative_path(project, lookup_selector)
            row = _find_entity_by_path(conn, source_rel)
            if row is None:
                _print_json_error_or_text(json_output, "intake_entity_not_found", str(entity_issue))
                return 1
            entity = _row_to_dict(row)
        source_rel = str(entity.get("path") or "").strip()
        if not source_rel:
            _print_json_error_or_text(json_output, "intake_entity_path_missing", f"entity has no path: {selector}")
            return 1

        source_candidate = project / Path(*PurePosixPath(source_rel).parts)
        try:
            source_abs, missing_source_directories = _physical_file_path(
                source_candidate,
                project,
            )
        except RuntimeError:
            _print_json_error_or_text(
                json_output,
                "intake_source_outside_project",
                "intake source must resolve to a physical file inside the project root",
            )
            return 1

        destination_candidate = project / target_dir / source_abs.name
        try:
            destination_abs, _missing_destination_directories = _physical_file_path(
                destination_candidate,
                project,
            )
        except RuntimeError:
            _print_json_error_or_text(
                json_output,
                "intake_promoted_path_outside_project",
                "promoted intake path must stay inside the project root",
            )
            return 1

        if missing_source_directories:
            _print_json_error_or_text(json_output, "intake_source_missing", f"intake source file does not exist: {source_rel}")
            return 1
        try:
            source_details = source_abs.lstat()
        except FileNotFoundError:
            _print_json_error_or_text(json_output, "intake_source_missing", f"intake source file does not exist: {source_rel}")
            return 1
        if (
            not stat.S_ISREG(source_details.st_mode)
            or getattr(source_details, "st_reparse_tag", 0)
        ):
            _print_json_error_or_text(
                json_output,
                "intake_source_not_file",
                f"intake promotion expects a physical regular file: {source_rel}",
            )
            return 1

        destination_abs = _unique_project_file(destination_abs)
        destination_rel = _relative_path(project, destination_abs)

        old_lifecycle = str(entity.get("lifecycle") or "")
        data = entity.get("data") if isinstance(entity.get("data"), dict) else {}
        updated_data = dict(data)
        updated_data["work_memory_target"] = canonical_target
        updated_data["promoted_from"] = source_rel
        updated_data["promoted_by"] = "cc memory intake promote"
        updated_data = _normalize_entity_data_for_lifecycle(updated_data, final_lifecycle)
        now = _now_iso()

        _transactional_move_file(conn, source_abs, destination_abs)
        file_hash = _content_hash(destination_abs)
        conn.execute(
            """
            UPDATE entities
            SET path = ?, lifecycle = ?, content_hash = ?, data = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                destination_rel,
                final_lifecycle,
                file_hash,
                _json_dumps(updated_data),
                now,
                entity["id"],
            ),
        )
        conn.execute(
            "UPDATE semantic_chunks SET source_path = ?, lifecycle = ?, updated_at = ? WHERE source_path = ?",
            (destination_rel, final_lifecycle, now, source_rel),
        )
        conn.execute(
            "UPDATE derived_vector_index SET source_path = ?, updated_at = ? WHERE source_path = ?",
            (destination_rel, now, source_rel),
        )
        _insert_event(
            conn,
            project,
            "promote",
            "cc memory intake promote",
            target_entity_ids=[str(entity["id"])],
            affected_paths=[source_rel, destination_rel],
            before_state={"path": source_rel, "lifecycle": old_lifecycle},
            after_state={
                "path": destination_rel,
                "lifecycle": final_lifecycle,
                "target": canonical_target,
            },
            reason=reason.strip() or f"Promoted intake to {target_dir}",
        )

    _generate_views(project, command="cc memory intake promote", quiet=True)
    payload = {
        "ok": True,
        "entityId": str(entity["id"]),
        "target": canonical_target,
        "fromPath": source_rel,
        "path": destination_rel,
        "lifecycle": final_lifecycle,
    }
    _print_json_or_text(
        json_output,
        payload,
        f"Promoted intake: {source_rel} -> {destination_rel}",
    )
    return 0

def cmd_memory_note_add(
    project: Path,
    title: str,
    body: str = "",
    area: str = "General",
    lifecycle: str = "captured",
    path: str = "",
    source_refs: list[str] | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_note_memory_not_initialized", message)
        return 1
    entity = _record_entity(
        project,
        "note",
        title,
        body=body,
        area=area,
        lifecycle=lifecycle,
        path=path,
        source_refs=source_refs,
        command="cc memory note add",
    )
    _print_json_or_text(json_output, {"ok": True, "entity": entity}, f"Recorded note: {entity['id']}")
    return 0

def cmd_memory_idea_add(
    project: Path,
    title: str,
    body: str = "",
    area: str = "General",
    lifecycle: str = "captured",
    path: str = "",
    source_refs: list[str] | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_idea_memory_not_initialized", message)
        return 1
    entity = _record_entity(
        project,
        "idea",
        title,
        body=body,
        area=area,
        lifecycle=lifecycle,
        path=path,
        source_refs=source_refs,
        command="cc memory idea add",
    )
    _print_json_or_text(json_output, {"ok": True, "entity": entity}, f"Recorded idea: {entity['id']}")
    return 0

def cmd_memory_decision_add(
    project: Path,
    title: str,
    body: str = "",
    rationale: str = "",
    area: str = "General",
    lifecycle: str = "active",
    path: str = "",
    source_refs: list[str] | None = None,
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_decision_memory_not_initialized", message)
        return 1
    combined_body = body.strip()
    if rationale.strip():
        combined_body = (combined_body + "\n\nRationale:\n" + rationale.strip()).strip()
    entity = _record_entity(
        project,
        "decision",
        title,
        body=combined_body,
        area=area,
        lifecycle=lifecycle,
        path=path,
        source_refs=source_refs,
        data={"rationale": rationale},
        event_type="record_decision",
        command="cc memory decision add",
        mirror_log="decision_log.jsonl",
    )
    _print_json_or_text(json_output, {"ok": True, "entity": entity}, f"Recorded decision: {entity['id']}")
    return 0

def cmd_memory_consult_record(
    project: Path,
    title: str = "",
    source_type: str = "manual_external",
    backend: str = "",
    question_summary: str = "",
    answer_summary: str = "",
    summary: str = "",
    raw_artifact_path: str = "",
    decision_outcome: str = "pending",
    status: str = "pending",
    affected: list[str] | None = None,
    lifecycle: str = "captured",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_consult_memory_not_initialized", message)
        return 1
    if not any([title, question_summary, answer_summary, summary]):
        _print_json_error_or_text(json_output, "consult_record_summary_required", "consult record needs --title, --question-summary, --answer-summary, or --summary")
        return 1
    consult_title = title or question_summary or summary or "External consult"
    body = summary or answer_summary or question_summary
    data = {
        "source_type": source_type,
        "backend": backend,
        "question_summary": question_summary,
        "answer_summary": answer_summary or summary,
        "raw_artifact_path": _relative_path(project, raw_artifact_path) if raw_artifact_path else "",
        "decision_outcome": decision_outcome,
        "status": status,
        "affected_entities": affected or [],
        "canonical_truth_owner": "controlcoding_memory",
        "note": "Consults inform memory but do not own canonical truth.",
    }
    entity = _record_entity(
        project,
        "consult",
        consult_title,
        body=body,
        area="Consult",
        lifecycle=lifecycle,
        data=data,
        event_type="record_consult",
        command="cc memory consult record",
        mirror_log="consult_log.jsonl",
    )
    _print_json_or_text(json_output, {"ok": True, "entity": entity}, f"Recorded consult: {entity['id']}")
    return 0

def cmd_memory_agent_run_record(
    project: Path,
    role: str = "",
    host: str = "",
    task: str = "",
    output_summary: str = "",
    input_context_ref: str = "",
    changed_files: list[str] | None = None,
    decisions_proposed: list[str] | None = None,
    verification: list[str] | None = None,
    status: str = "completed",
    linked_work_item: str = "",
    lifecycle: str = "captured",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_agent_run_memory_not_initialized", message)
        return 1
    if not any([role, task, output_summary]):
        _print_json_error_or_text(json_output, "agent_run_summary_required", "agent-run record needs --role, --task, or --summary")
        return 1
    title = f"{role or 'Agent'} - {task or output_summary[:60]}"
    data = {
        "role": role,
        "host": host,
        "task": task,
        "input_context_ref": input_context_ref,
        "changed_files": [_relative_path(project, item) for item in (changed_files or [])],
        "decisions_proposed": decisions_proposed or [],
        "verification_performed": verification or [],
        "status": status,
        "linked_work_item": linked_work_item,
        "canonical_truth_owner": "controlcoding_memory",
        "note": "Agent runs inform memory but do not own canonical truth.",
    }
    entity = _record_entity(
        project,
        "agent_run",
        title,
        body=output_summary,
        area="AgentRun",
        lifecycle=lifecycle,
        data=data,
        event_type="record_agent_run",
        command="cc memory agent-run record",
        mirror_log="agent_run_log.jsonl",
    )
    _print_json_or_text(json_output, {"ok": True, "entity": entity}, f"Recorded agent run: {entity['id']}")
    return 0
