"""Project Map contract projection for portable ControlWork state."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import work_features


PROJECT_MAP_CONTRACT_VERSION = "controlwork-project-map/v1"

MEMORY_AREAS = ["inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy", "views"]
SUPPORT_FOLDERS = [
    ".controlwork/ingestion",
    ".controlwork/extracts",
    ".controlwork/checkpoints",
    ".controlwork/context-packets",
    ".controlwork/sessions",
    ".controlwork/proposals",
    ".controlwork/dashboard",
]
LIFECYCLES = ["captured", "active", "needs_review", "superseded", "legacy"]
PORTABLE_RELATION_TYPES = ["contains", "references", "mentions", "related_topic", "supersedes", "superseded_by"]
PORTABLE_CONFIDENCE_VALUES = ["explicit_link", "strong_topic_overlap", "weak_topic_overlap", "human_reviewed"]
EDGE_GROUPS = ["documental", "thematic", "lifecycle", "session"]
SCAN_DOCUMENT_LIMIT = 5000
SCAN_FOLDER_DOCUMENT_LIMIT = 1200
SCAN_INVENTORY_FILE_LIMIT = 5000
INFRASTRUCTURE_SCOPE_VALUES = [
    "project_corpus",
    "project_work_infrastructure",
    "software_design_infrastructure",
    "internal_project_layer",
    "controlwork_runtime",
    "controlcoding_runtime",
]

AREA_DETAILS = {
    "inbox": "Captured material waiting for classification.",
    "sources": "Source summaries, imported references, and provenance notes.",
    "notes": "Clean observations and reusable context.",
    "ideas": "Options, concepts, and possible directions.",
    "decisions": "Accepted decisions and rationale.",
    "plans": "Work plans, workflows, and implementation briefs.",
    "outputs": "Deliverables and final artifacts.",
    "legacy": "Superseded or archived material kept for traceability.",
    "views": "Generated projections, not canonical truth.",
}

SESSION_LINK_RELATION_MAP = {
    "belongs_to_category": "mentions",
    "references_entry": "references",
    "references_decision": "references",
    "changes_memory": "references",
    "produced_packet": "references",
    "left_followup": "references",
    "uses_work_graphrag": "references",
}


def _read_config(project: Path) -> dict:
    return work_features.read_json(project / ".controlwork" / "config.json")


def _controlwork_metadata(project: Path) -> dict:
    path = project / "CONTROLWORK.md"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    title = project.name or "ControlWork Project"
    purpose = "Local ControlWork project memory."
    boundary = "Local ControlWork Project Plane."
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and title == (project.name or "ControlWork Project"):
            title = stripped[2:].strip()
        elif stripped.lower().startswith("- **name**:"):
            title = stripped.split(":", 1)[1].strip() or title
        elif stripped.lower().startswith("- **purpose**:"):
            purpose = stripped.split(":", 1)[1].strip() or purpose
        elif stripped.lower().startswith("- **product boundary**:"):
            boundary = stripped.split(":", 1)[1].strip() or boundary
    return {
        "title": title,
        "description": purpose if boundary in purpose else f"{purpose} {boundary}".strip(),
        "contextExists": path.exists(),
    }


def _project_base_document(project: Path, config: dict, metadata: dict) -> dict:
    relative_path = work_features.configured_base_document_path(project, config)
    path = project / relative_path
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    title = _read_file_title(path, metadata.get("title", "Project Base Document")) if path.exists() else metadata.get("title", "Project Base Document")
    return {
        "id": work_features.stable_graph_id("CW_NODE", relative_path),
        "title": title,
        "type": "project_document",
        "area": "context",
        "lifecycle": "active" if path.exists() else "needs_review",
        "path": relative_path,
        "summary": _markdown_summary(text, fallback=metadata.get("description", "")),
        "sourceKind": "project_document",
        "sourcePath": relative_path,
        "topics": _entry_topics({
            "title": title,
            "body": text or metadata.get("description", ""),
            "source": relative_path,
            "category": "project",
            "path": relative_path,
        }),
        "reviewStatus": "reviewed" if path.exists() else "needs_review",
        "updatedAt": _modified_at(project, relative_path),
        "degree": 0,
        "projectScope": "project_infrastructure",
        "projectRole": "base_document",
        "infrastructureScope": "project_work_infrastructure",
        "infrastructureLayer": "project_work",
        "isBaseDocument": True,
        "internal": False,
    }


def _format_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size or 0)} B"


def _scan_record_title(path: str) -> str:
    name = Path(str(path or "")).name
    return name or str(path or "Scanned file")


def _scan_record_topics(record: dict, limit: int = 5) -> list[str]:
    path = str(record.get("path", ""))
    parts = path.replace("\\", "/").split("/")
    text = " ".join([path, " ".join(Path(path).parts), str(record.get("kind", ""))])
    tokens = work_features.graph_tokens(text)
    ignored = {"extra", "controlwork", "project", "file", "main", "master", "unknown"}
    topics: list[str] = []
    for token, _count in Counter(token for token in tokens if token not in ignored).most_common():
        label = _topic_label(token)
        if label and label.lower() not in {item.lower() for item in topics}:
            topics.append(label)
        if len(topics) >= limit:
            break
    if parts and parts[0] not in {".controlwork", "CONTROLWORK.md", "PROJECT.md"}:
        folder = _topic_label(parts[0])
        if folder and folder not in topics:
            topics.insert(0, folder)
    return topics[:limit]


def _scan_lifecycle(record: dict) -> str:
    review_status = str(record.get("reviewStatus", "unreviewed"))
    scan_status = str(record.get("scanStatus", ""))
    if scan_status == "missing":
        return "legacy"
    if review_status in {"reviewed", "ready_to_promote", "promoted"}:
        return "captured"
    if review_status in {"duplicate", "conflict", "rejected", "ignored"}:
        return "needs_review"
    return "needs_review"


def _scan_corpus_subtype(record: dict) -> str:
    path = str(record.get("path", "")).replace("\\", "/")
    lowered = path.lower()
    suffix = Path(path).suffix.lower()
    kind = str(record.get("kind", "unknown"))
    name = Path(path).name.lower()
    if name in {"readme.md", "project.md"}:
        return "index_or_outline"
    if kind == "pdf":
        return "study_source"
    if suffix in {".ipynb", ".py", ".r", ".jl"}:
        return "notebook_or_code"
    if kind == "csv" or "dataset" in lowered or "datasets" in lowered:
        return "dataset"
    if kind == "image":
        return "asset"
    if any(token in lowered for token in ("corso", "course", "lezione", "lesson", "slide", "giorno", "universit", "fondamenti", "tutorial")):
        return "course_material"
    if kind in {"markdown", "text", "html"}:
        return "study_note"
    return "source_material"


def _scan_project_role(record: dict) -> str:
    return "study_research"


def _is_controlcoding_distribution(distribution: str) -> bool:
    return "controlcoding" in str(distribution or "").lower()


def _scan_infrastructure_scope(record: dict, distribution: str) -> str:
    path = str(record.get("path", "")).replace("\\", "/")
    lowered = path.lower()
    suffix = Path(path).suffix.lower()
    name = Path(path).name.lower()
    first = lowered.split("/", 1)[0]

    if lowered == "controlwork.md" or lowered.startswith(".controlwork/"):
        return "controlwork_runtime"

    if _is_controlcoding_distribution(distribution):
        if lowered == "controlcoding.md" or first in {".controlcoding", ".claude", ".github", "templates"}:
            return "controlcoding_runtime"
        if first == "docs" and any(token in lowered for token in ("architecture", "design", "adr", "spec", "roadmap")):
            return "software_design_infrastructure"
        if first in {"src", "tests", "scripts", "config", "configs"}:
            return "internal_project_layer"
        if name in {"agents.md", "claude.md", "gemini.md", ".clinerules"}:
            return "controlcoding_runtime"

    if name in {
        "project.md",
        "readme.md",
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "makefile",
        "dockerfile",
        "compose.yaml",
        "docker-compose.yml",
    }:
        return "project_work_infrastructure"
    if first in {"docs", "documentation"} and suffix in {".md", ".txt", ".rst"}:
        return "project_work_infrastructure"

    return "project_corpus"


def _scan_infrastructure_layer(record: dict, distribution: str) -> str:
    scope = _scan_infrastructure_scope(record, distribution)
    return {
        "project_corpus": "project_corpus",
        "project_work_infrastructure": "project_work",
        "software_design_infrastructure": "software_design",
        "internal_project_layer": "project_internal",
        "controlwork_runtime": "controlwork_runtime",
        "controlcoding_runtime": "controlcoding_runtime",
    }.get(scope, "project_corpus")


def _project_scope_for_infrastructure(scope: str) -> str:
    if scope == "project_corpus":
        return "project_corpus"
    if scope == "controlwork_runtime":
        return "controlwork_state"
    return "project_infrastructure"


def _scan_priority(record: dict) -> tuple[int, int, str]:
    path = str(record.get("path", "")).replace("\\", "/")
    kind = str(record.get("kind", "unknown"))
    suffix = Path(path).suffix.lower()
    depth = path.count("/")
    kind_rank = {
        "pdf": 0,
        "office": 1,
        "markdown": 2,
        "text": 3,
        "csv": 4,
        "html": 5,
        "json": 6,
        "yaml": 7,
        "unknown": 8,
        "image": 9,
    }.get(kind, 10)
    if Path(path).name.lower() in {"readme.md", "project.md"}:
        kind_rank = min(kind_rank, 2)
    if suffix == ".ipynb":
        kind_rank = 2
    return (kind_rank, depth, path.lower())


def _is_project_scan_record(record: dict, base_path: str, canonical_context: str) -> bool:
    path = str(record.get("path", "")).replace("\\", "/")
    if not path or path.startswith(".controlwork/"):
        return False
    if path in {base_path, canonical_context, "CONTROLWORK.md"}:
        return False
    return True


def _compact_scan_file(record: dict, distribution: str, document_id: str = "") -> dict:
    path = str(record.get("path", "")).replace("\\", "/")
    folder = str(Path(path).parent).replace("\\", "/")
    if folder == ".":
        folder = ""
    infrastructure_scope = _scan_infrastructure_scope(record, distribution)
    return {
        "path": path,
        "title": _scan_record_title(path),
        "folder": folder,
        "kind": str(record.get("kind", "unknown")),
        "sizeBytes": int(record.get("sizeBytes") or 0),
        "sizeLabel": _format_size(int(record.get("sizeBytes") or 0)),
        "modifiedAt": str(record.get("modifiedAt", "")),
        "scanStatus": str(record.get("scanStatus", "")),
        "reviewStatus": str(record.get("reviewStatus", "unreviewed")),
        "sensitivity": str(record.get("sensitivity", "unknown")),
        "sourceRecord": str(record.get("sourceRecord", "")),
        "documentId": document_id,
        "projectRole": _scan_project_role(record),
        "corpusSubtype": _scan_corpus_subtype(record),
        "infrastructureScope": infrastructure_scope,
        "infrastructureLayer": _scan_infrastructure_layer(record, distribution),
        "topics": _scan_record_topics(record),
    }


def _folder_for_path(path: str) -> str:
    folder = str(Path(str(path or "")).parent).replace("\\", "/")
    return folder if folder != "." else "project root"


def _folder_key(path: str) -> str:
    value = str(path or "").replace("\\", "/").strip("/")
    return value if value else "."


def _scan_inventory(project: Path, base_path: str, canonical_context: str, distribution: str) -> dict:
    payload = work_features.read_file_index(project)
    analysis = work_features.read_json(project / ".controlwork" / "ingestion" / "scan-analysis.json")
    records = [
        item for item in payload.get("files", [])
        if isinstance(item, dict) and _is_project_scan_record(item, base_path, canonical_context)
    ]
    folder_map: dict[str, dict] = {}
    for record in records:
        path = str(record.get("path", "")).replace("\\", "/")
        parts = path.split("/")[:-1]
        for index in range(len(parts) + 1):
            folder = "/".join(parts[:index])
            if not folder:
                folder = "."
            row = folder_map.setdefault(folder, {
                "path": "" if folder == "." else folder,
                "fileCount": 0,
                "totalBytes": 0,
                "kinds": Counter(),
                "reviewStatuses": Counter(),
                "latestModifiedAt": "",
            })
            row["fileCount"] += 1
            row["totalBytes"] += int(record.get("sizeBytes") or 0)
            row["kinds"][str(record.get("kind", "unknown"))] += 1
            row["reviewStatuses"][str(record.get("reviewStatus", "unreviewed"))] += 1
            modified_at = str(record.get("modifiedAt", ""))
            if modified_at > row["latestModifiedAt"]:
                row["latestModifiedAt"] = modified_at
    folders = []
    for row in folder_map.values():
        folders.append({
            "path": row["path"],
            "fileCount": row["fileCount"],
            "totalBytes": row["totalBytes"],
            "sizeLabel": _format_size(row["totalBytes"]),
            "kinds": dict(row["kinds"].most_common()),
            "reviewStatuses": dict(row["reviewStatuses"].most_common()),
            "latestModifiedAt": row["latestModifiedAt"],
        })
    folders.sort(key=lambda item: (-int(item["fileCount"]), str(item["path"])))
    compact_files = [_compact_scan_file(record, distribution) for record in sorted(records, key=_scan_priority)]
    return {
        "summary": payload.get("summary", {}),
        "analysisSummary": analysis.get("summary", {}),
        "folders": folders,
        "files": compact_files[:SCAN_INVENTORY_FILE_LIMIT],
        "countsByFolder": {item["path"] or ".": item["fileCount"] for item in folders},
        "countsByFileKind": dict(Counter(str(item.get("kind", "unknown")) for item in records).most_common()),
        "countsByScanStatus": dict(Counter(str(item.get("scanStatus", "")) for item in records).most_common()),
        "countsByReviewStatus": dict(Counter(str(item.get("reviewStatus", "unreviewed")) for item in records).most_common()),
        "countsByInfrastructureScope": dict(Counter(str(item.get("infrastructureScope", "project_corpus")) for item in compact_files).most_common()),
        "duplicateGroups": analysis.get("duplicateGroups", [])[:50] if isinstance(analysis.get("duplicateGroups"), list) else [],
        "versionCandidates": analysis.get("versionCandidates", [])[:50] if isinstance(analysis.get("versionCandidates"), list) else [],
        "conflictCandidates": analysis.get("conflictCandidates", [])[:50] if isinstance(analysis.get("conflictCandidates"), list) else [],
        "policy": "Scanned files are local evidence from .controlwork/ingestion/file-index.json. They remain needs-review until promoted or reviewed.",
        "limited": len(records) > SCAN_INVENTORY_FILE_LIMIT,
        "totalProjectFiles": len(records),
    }


def _scan_document(record: dict) -> dict:
    path = str(record.get("path", "")).replace("\\", "/")
    kind = str(record.get("kind", "unknown"))
    topics = _scan_record_topics(record)
    infrastructure_scope = str(record.get("infrastructureScope") or "project_corpus")
    infrastructure_layer = str(record.get("infrastructureLayer") or "project_corpus")
    return {
        "id": work_features.stable_graph_id("CW_SCAN_FILE", path),
        "title": _scan_record_title(path),
        "type": "scanned_file",
        "area": "sources",
        "lifecycle": _scan_lifecycle(record),
        "path": path,
        "summary": (
            f"Detected {kind} file from local scan in "
            f"{_folder_for_path(path)}. "
            f"Review status {record.get('reviewStatus', 'unreviewed')}; size {_format_size(int(record.get('sizeBytes') or 0))}."
        ),
        "sourceKind": kind,
        "sourcePath": path,
        "topics": topics,
        "reviewStatus": str(record.get("reviewStatus", "unreviewed")),
        "updatedAt": str(record.get("modifiedAt", "")),
        "degree": 0,
        "projectScope": _project_scope_for_infrastructure(infrastructure_scope),
        "projectRole": _scan_project_role(record),
        "corpusSubtype": _scan_corpus_subtype(record),
        "infrastructureScope": infrastructure_scope,
        "infrastructureLayer": infrastructure_layer,
        "isScanEvidence": True,
        "scanStatus": str(record.get("scanStatus", "")),
        "sensitivity": str(record.get("sensitivity", "unknown")),
    }


def _scan_document_candidates(scan_inventory: dict) -> list[dict]:
    records = []
    for file_record in scan_inventory.get("files", []):
        source = {
            "path": file_record.get("path", ""),
            "kind": file_record.get("kind", "unknown"),
            "sizeBytes": file_record.get("sizeBytes", 0),
            "modifiedAt": file_record.get("modifiedAt", ""),
            "scanStatus": file_record.get("scanStatus", ""),
            "reviewStatus": file_record.get("reviewStatus", "unreviewed"),
            "sensitivity": file_record.get("sensitivity", "unknown"),
            "infrastructureScope": file_record.get("infrastructureScope", "project_corpus"),
            "infrastructureLayer": file_record.get("infrastructureLayer", "project_corpus"),
            "corpusSubtype": file_record.get("corpusSubtype", "source_material"),
        }
        records.append(source)
    return [_scan_document(record) for record in sorted(records, key=_scan_priority)[:SCAN_DOCUMENT_LIMIT]]


def _folder_document(record: dict) -> dict:
    path = _folder_key(str(record.get("path", "")))
    title = "Project Root" if path == "." else Path(path).name
    topics = _scan_record_topics({"path": path, "kind": "folder"}, limit=5)
    file_count = int(record.get("fileCount") or 0)
    return {
        "id": work_features.stable_graph_id("CW_SCAN_FOLDER", path),
        "title": title,
        "type": "project_folder",
        "area": "context",
        "lifecycle": "active",
        "path": path,
        "summary": (
            f"Detected project folder containing {file_count} scanned file(s). "
            f"Latest update {record.get('latestModifiedAt') or 'not recorded'}."
        ),
        "sourceKind": "folder",
        "sourcePath": path,
        "topics": topics,
        "reviewStatus": "scan_evidence",
        "updatedAt": str(record.get("latestModifiedAt", "")),
        "degree": 0,
        "projectScope": "project_infrastructure",
        "projectRole": "project_folder",
        "corpusSubtype": "folder",
        "infrastructureScope": "project_work_infrastructure",
        "infrastructureLayer": "project_work",
        "isFolderEvidence": True,
        "fileCount": file_count,
        "sizeLabel": str(record.get("sizeLabel", "")),
        "kinds": record.get("kinds", {}),
    }


def _folder_document_candidates(scan_inventory: dict) -> list[dict]:
    folders = scan_inventory.get("folders", [])
    if not any(_folder_key(str(folder.get("path", ""))) == "." for folder in folders if isinstance(folder, dict)):
        folders = [{
            "path": "",
            "fileCount": int(scan_inventory.get("totalProjectFiles") or 0),
            "totalBytes": 0,
            "sizeLabel": "",
            "kinds": scan_inventory.get("countsByFileKind", {}),
            "reviewStatuses": scan_inventory.get("countsByReviewStatus", {}),
            "latestModifiedAt": "",
        }, *folders]
    return [_folder_document(folder) for folder in folders[:SCAN_FOLDER_DOCUMENT_LIMIT] if isinstance(folder, dict)]


def _project_profile(scan_inventory: dict) -> dict:
    counts = Counter(str(item.get("projectRole", "study_research")) for item in scan_inventory.get("files", []))
    subtype_counts = Counter(str(item.get("corpusSubtype", "source_material")) for item in scan_inventory.get("files", []))
    kind_counts = Counter()
    for kind, count in scan_inventory.get("countsByFileKind", {}).items():
        kind_counts[str(kind)] = int(count or 0)
    evidence: list[str] = []
    if kind_counts.get("pdf", 0):
        evidence.append(f"{kind_counts['pdf']} PDF study/source file(s)")
    if subtype_counts.get("notebook_or_code", 0):
        evidence.append(f"{subtype_counts['notebook_or_code']} notebook/code file(s)")
    if subtype_counts.get("course_material", 0):
        evidence.append(f"{subtype_counts['course_material']} course-structured file(s)")
    if any("Univers" in str(folder.get("path", "")) for folder in scan_inventory.get("folders", [])):
        evidence.append("university-oriented folder structure")
    detected = "archive_organization"
    primary = "organize_archive"
    secondary = ["create_wiki"]
    if kind_counts.get("pdf", 0) or subtype_counts.get("course_material", 0) or subtype_counts.get("notebook_or_code", 0):
        detected = "study_learning"
        primary = "build_learning_path"
        secondary = ["organize_archive", "create_wiki", "research_sources"]
    return {
        "detectedKind": detected,
        "confidence": "suggested",
        "primaryPurpose": primary,
        "secondaryPurposes": secondary,
        "projectCorpusPolicy": "Raw project files are classified as study/research corpus evidence. corpusSubtype is a navigation filter only. ControlWork decisions, plans, views, and sessions are work-state metadata, not file classifications.",
        "infrastructurePolicy": "Project infrastructure, ControlWork runtime state, and embedded ControlCoding layers are separate from the user's source corpus.",
        "evidence": evidence[:8],
        "needsHumanConfirmation": True,
        "countsByProjectRole": dict(counts.most_common()),
        "countsByCorpusSubtype": dict(subtype_counts.most_common()),
    }


def _infrastructure_profile(distribution: str, base_document: dict, scan_inventory: dict) -> dict:
    distribution_value = str(distribution or "standalone_controlwork")
    embedded_controlcoding = _is_controlcoding_distribution(distribution_value)
    scope_counts = Counter(str(item.get("infrastructureScope", "project_corpus")) for item in scan_inventory.get("files", []))
    if base_document:
        scope_counts[str(base_document.get("infrastructureScope", "project_work_infrastructure"))] += 1
    layers = [
        {
            "id": "project_corpus",
            "label": "Project Corpus",
            "kind": "project",
            "appliesTo": "controlwork",
            "status": "detected",
            "fileCount": int(scope_counts.get("project_corpus", 0)),
            "description": "User project material such as study sources, courses, notebooks, datasets, assets, and deliverables.",
        },
        {
            "id": "project_work_infrastructure",
            "label": "Project Work Infrastructure",
            "kind": "project",
            "appliesTo": "controlwork",
            "status": "active",
            "fileCount": int(scope_counts.get("project_work_infrastructure", 0)),
            "description": "The master project document, project indexes, README files, and approved folder organization.",
        },
        {
            "id": "controlwork_runtime",
            "label": "ControlWork Runtime",
            "kind": "controlwork",
            "appliesTo": "controlwork",
            "status": "internal",
            "fileCount": int(scope_counts.get("controlwork_runtime", 0)),
            "description": "CONTROLWORK.md, .controlwork memory, sessions, dashboard output, ingestion records, checkpoints, and packets.",
        },
    ]
    if embedded_controlcoding:
        layers.extend([
            {
                "id": "controlcoding_runtime",
                "label": "ControlCoding Runtime",
                "kind": "controlcoding",
                "appliesTo": "embedded_controlcoding",
                "status": "internal",
                "fileCount": int(scope_counts.get("controlcoding_runtime", 0)),
                "description": "Host context, templates, CLI contracts, documentation rules, agents, and repository guardrails.",
            },
            {
                "id": "software_design_infrastructure",
                "label": "Software Design Infrastructure",
                "kind": "software_design",
                "appliesTo": "embedded_controlcoding",
                "status": "detected",
                "fileCount": int(scope_counts.get("software_design_infrastructure", 0)),
                "description": "Architecture, design, ADR, roadmap, and specification material when the project is a software project.",
            },
            {
                "id": "internal_project_layer",
                "label": "Internal Project Layer",
                "kind": "project_internal",
                "appliesTo": "embedded_controlcoding",
                "status": "detected",
                "fileCount": int(scope_counts.get("internal_project_layer", 0)),
                "description": "Source code, tests, scripts, configuration, and internal implementation structure when present.",
            },
        ])
    return {
        "distribution": distribution_value,
        "classificationValues": INFRASTRUCTURE_SCOPE_VALUES,
        "countsByScope": dict(scope_counts.most_common()),
        "policy": "Do not mix ControlWork or ControlCoding operating files with the user's project corpus. In standalone ControlWork, the project infrastructure is only the work infrastructure around the user's project. In embedded ControlCoding, additional ControlCoding, software design, and internal project layers may be shown separately.",
        "project": {
            "baseDocumentPath": str(base_document.get("path") or "PROJECT.md"),
            "corpusScope": "project_corpus",
            "workInfrastructureScope": "project_work_infrastructure",
            "description": "The user's project is explained from the master document and confirmed corpus organization.",
        },
        "controlWork": {
            "present": True,
            "scope": "controlwork_runtime",
            "paths": ["CONTROLWORK.md", ".controlwork/"],
            "description": "Internal operating context and generated work-state projections.",
        },
        "controlCoding": {
            "present": embedded_controlcoding,
            "scope": "controlcoding_runtime",
            "description": "Embedded host layer for ControlCoding projects. Empty in standalone ControlWork.",
        },
        "layers": layers,
    }


def _top_level_scan_roots(scan_inventory: dict, limit: int = 8) -> list[dict]:
    roots: dict[str, dict] = {}
    for file_record in scan_inventory.get("files", []):
        path = str(file_record.get("path", "")).replace("\\", "/")
        root = path.split("/", 1)[0] if "/" in path else "project root"
        row = roots.setdefault(root, {
            "path": "" if root == "project root" else root,
            "fileCount": 0,
            "kinds": Counter(),
            "projectRoles": Counter(),
            "corpusSubtypes": Counter(),
        })
        row["fileCount"] += 1
        row["kinds"][str(file_record.get("kind", "unknown"))] += 1
        row["projectRoles"][str(file_record.get("projectRole", "study_research"))] += 1
        row["corpusSubtypes"][str(file_record.get("corpusSubtype", "source_material"))] += 1
    result = []
    for row in roots.values():
        result.append({
            "path": row["path"],
            "fileCount": row["fileCount"],
            "kinds": dict(row["kinds"].most_common(5)),
            "projectRoles": dict(row["projectRoles"].most_common(5)),
            "corpusSubtypes": dict(row["corpusSubtypes"].most_common(5)),
        })
    return sorted(result, key=lambda item: (-int(item["fileCount"]), str(item["path"])))[:limit]


def _organization_workflow(project_profile: dict, scan_inventory: dict) -> dict:
    total_files = int(scan_inventory.get("totalProjectFiles") or 0)
    subtype_counts = project_profile.get("countsByCorpusSubtype", {}) if isinstance(project_profile.get("countsByCorpusSubtype"), dict) else {}
    unclassified = total_files
    current_state = "raw_or_partially_organized" if total_files else "empty_or_not_scanned"
    suggested_kind = str(project_profile.get("detectedKind") or "unknown")
    return {
        "currentState": current_state,
        "approvalRequired": True,
        "suggestedProjectKind": suggested_kind,
        "suggestedPrimaryPurpose": str(project_profile.get("primaryPurpose") or "needs_human_confirmation"),
        "unclassifiedCorpusFiles": unclassified,
        "rawIntakeFolder": "Raw/sources",
        "currentTopLevelRoots": _top_level_scan_roots(scan_inventory),
        "proposedFolders": [
            {"path": "Raw/sources", "purpose": "Initial holding area for unclassified source files before review."},
            {"path": "Study/sources", "purpose": "Reviewed PDFs, books, papers, slide decks, and reference sources."},
            {"path": "Study/courses", "purpose": "Course folders, lessons, tutorials, and learning modules."},
            {"path": "Study/notebooks", "purpose": "Notebook and code exercises."},
            {"path": "Study/datasets", "purpose": "Datasets and tabular inputs."},
            {"path": "Study/assets", "purpose": "Images and supporting media."},
            {"path": "Wiki", "purpose": "Human-readable topic pages derived from reviewed material."},
            {"path": "Outputs", "purpose": "Generated learning paths, summaries, and deliverables."},
        ],
        "workflowSteps": [
            {"id": "scan", "label": "Scan local folder", "status": "completed", "detail": f"{total_files} project file(s) detected."},
            {"id": "confirm-purpose", "label": "Confirm project purpose", "status": "needs_human", "detail": f"Suggested: {suggested_kind} / {project_profile.get('primaryPurpose', 'needs classification')}."},
            {"id": "create-master", "label": "Create or update master document", "status": "drafted", "detail": "PROJECT.md is the working master document."},
            {"id": "classify-corpus", "label": "Classify source corpus", "status": "needs_human", "detail": f"Initial subtype counts: {dict(subtype_counts)}"},
            {"id": "organize-files", "label": "Move or copy files into approved folders", "status": "blocked_until_approval", "detail": "No file move is performed automatically."},
            {"id": "promote-reviewed", "label": "Promote reviewed sources into ControlWork memory", "status": "pending", "detail": "Only reviewed files become durable source records."},
            {"id": "project-flow", "label": "Render project flow from master document", "status": "available", "detail": "Flow lanes follow the master document sections and study/research corpus."},
        ],
        "pendingQuestions": [
            "Should this project be managed as a study/learning path, an archive, a wiki, or a research corpus?",
            "Should ControlWork reorganize files physically, or only create a virtual map first?",
            "Which folder names should be used for the approved project structure?",
            "Which sources are authoritative enough to promote first?",
        ],
        "movePolicy": "No source file is moved, copied, renamed, deleted, or expanded without explicit human approval.",
    }


def _master_document(project: Path, base_document: dict, project_profile: dict, scan_inventory: dict) -> dict:
    role_counts = project_profile.get("countsByProjectRole", {}) if isinstance(project_profile.get("countsByProjectRole"), dict) else {}
    subtype_counts = project_profile.get("countsByCorpusSubtype", {}) if isinstance(project_profile.get("countsByCorpusSubtype"), dict) else {}
    sections = [
        {
            "id": "overview",
            "title": "Project Overview",
            "purpose": "Explain what this workspace is and what the user wants to achieve.",
            "source": base_document.get("path", "PROJECT.md"),
            "status": "draft",
        },
        {
            "id": "study-research-corpus",
            "title": "Study And Research Corpus",
            "purpose": "All raw project files are study/research material until a human promotes a more specific role.",
            "projectRole": "study_research",
            "documentCount": int(role_counts.get("study_research", 0)),
            "status": "needs_review",
        },
        {
            "id": "source-material-index",
            "title": "Source Material Index",
            "purpose": "PDFs, books, slide decks, references, and source-like files to review.",
            "corpusSubtype": "study_source",
            "documentCount": int(subtype_counts.get("study_source", 0)) + int(subtype_counts.get("source_material", 0)),
            "status": "needs_review",
        },
        {
            "id": "course-map",
            "title": "Course And Module Map",
            "purpose": "Courses, lessons, tutorials, and README outlines organized into learning modules.",
            "corpusSubtype": "course_material",
            "documentCount": int(subtype_counts.get("course_material", 0)) + int(subtype_counts.get("index_or_outline", 0)) + int(subtype_counts.get("study_note", 0)),
            "status": "needs_review",
        },
        {
            "id": "notebooks-code",
            "title": "Notebooks And Code Exercises",
            "purpose": "Notebook and code practice material connected to the learning path.",
            "corpusSubtype": "notebook_or_code",
            "documentCount": int(subtype_counts.get("notebook_or_code", 0)),
            "status": "needs_review",
        },
        {
            "id": "datasets-assets",
            "title": "Datasets And Assets",
            "purpose": "Datasets, images, and supporting files.",
            "corpusSubtype": "dataset",
            "documentCount": int(subtype_counts.get("dataset", 0)) + int(subtype_counts.get("asset", 0)),
            "status": "needs_review",
        },
        {
            "id": "wiki-index",
            "title": "Wiki And Topic Index",
            "purpose": "Future reviewed knowledge base generated from promoted sources.",
            "status": "pending",
        },
        {
            "id": "work-plan",
            "title": "Work Plan",
            "purpose": "Next review, classification, and organization steps approved by the user.",
            "status": "draft",
        },
    ]
    return {
        "path": str(base_document.get("path") or "PROJECT.md"),
        "title": str(base_document.get("title") or project.name or "Project Master Document"),
        "status": "draft_from_scan",
        "summary": "Master working document for the user's project. The Project view and flow should follow this outline after human confirmation.",
        "tableOfContents": sections,
        "sourceArtifacts": [".controlwork/ingestion/file-index.json", ".controlwork/ingestion/scan-analysis.json"],
        "renderPolicy": "The project flow follows this master document outline and the confirmed study/research corpus. File subtypes are navigation filters, not accepted project classifications.",
    }


def _summary(text: str, fallback: str = "") -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return fallback
    if len(clean) <= 220:
        return clean
    return clean[:217].rstrip() + "..."


def _markdown_summary(text: str, fallback: str = "") -> str:
    lines: list[str] = []
    in_overview = False
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith("# "):
            continue
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            if heading in {"project overview", "overview", "summary"}:
                in_overview = True
                continue
            if in_overview and lines:
                break
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            if lines:
                break
            continue
        if not in_overview and lines:
            break
        lines.append(stripped)
    return _summary(" ".join(lines), fallback=fallback)


def _source_kind(path_or_source: str, area: str = "") -> str:
    value = str(path_or_source or "").lower()
    suffix = Path(value).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".zip":
        return "zip"
    if suffix in {".md", ".markdown", ".rst"}:
        return "markdown"
    if suffix in {".txt", ".csv", ".json", ".yaml", ".yml"}:
        return suffix.lstrip(".")
    if value.startswith("http://") or value.startswith("https://"):
        return "link"
    if area == "sources":
        return "source_record"
    if value:
        return "reference"
    return "manual"


def _topic_label(token: str) -> str:
    return str(token or "").replace("_", " ").replace("-", " ").strip().title()


def _entry_topics(entry: dict, limit: int = 4) -> list[str]:
    tokens = work_features.graph_tokens(" ".join([
        str(entry.get("title", "")),
        str(entry.get("body", "")),
        str(entry.get("source", "")),
        str(entry.get("category", "")),
        str(entry.get("path", "")),
    ]))
    ignored = {"memory", "source", "project", "controlwork", "entry", "body", "captured", "active"}
    topics: list[str] = []
    for token, _count in Counter(token for token in tokens if token not in ignored).most_common():
        label = _topic_label(token)
        if label and label.lower() not in {item.lower() for item in topics}:
            topics.append(label)
        if len(topics) >= limit:
            break
    category = str(entry.get("category", "")).strip()
    if category and category not in topics:
        topics.insert(0, _topic_label(category))
    return topics[:limit]


def _node_type_to_document_type(node_type: str) -> str:
    return {
        "project_context": "document",
        "source": "source",
        "note": "note",
        "idea": "idea",
        "decision": "decision",
        "plan": "plan",
        "output": "output",
        "legacy": "legacy",
    }.get(node_type, node_type or "document")


def _review_status(lifecycle: str, source_kind: str = "") -> str:
    if lifecycle == "active":
        return "reviewed"
    if lifecycle in {"needs_review", "captured"}:
        return "needs_review"
    if lifecycle in {"superseded", "legacy"}:
        return lifecycle
    if source_kind in {"pdf", "zip"}:
        return "needs_review"
    return "captured"


def _strength_label(confidence: str, status: str = "") -> str:
    if status == "rejected":
        return "weak"
    if confidence in {"human_reviewed", "explicit_link"}:
        return "strong"
    if confidence == "strong_topic_overlap":
        return "medium"
    return "weak"


def _edge_group(relation_type: str, status: str = "", native_type: str = "") -> str:
    if native_type in SESSION_LINK_RELATION_MAP:
        return "session"
    if relation_type in {"supersedes", "superseded_by"}:
        return "lifecycle"
    if relation_type in {"related_topic", "mentions"} or status == "suggested":
        return "thematic"
    return "documental"


def _relation_status(edge: dict) -> str:
    status = str(edge.get("status") or "canonical")
    if status == "accepted" or edge.get("reviewedAt") or edge.get("confidence") == "human_reviewed":
        return "human_reviewed"
    if status == "suggested":
        return "suggested"
    if status == "rejected":
        return "suggested"
    return "canonical"


def _project_map_edge(edge: dict, node_ids: set[str], source_artifact: str, native_type: str = "") -> dict | None:
    source_id = str(edge.get("sourceId") or edge.get("from") or "")
    target_id = str(edge.get("targetId") or edge.get("to") or "")
    relation_type = str(edge.get("type") or "references")
    if relation_type not in PORTABLE_RELATION_TYPES:
        relation_type = SESSION_LINK_RELATION_MAP.get(relation_type, "references")
    if source_id not in node_ids or target_id not in node_ids:
        return None
    confidence = str(edge.get("confidence") or "explicit_link")
    if confidence not in PORTABLE_CONFIDENCE_VALUES:
        confidence = "human_reviewed" if confidence.startswith("human") else "weak_topic_overlap"
    relation_status = _relation_status(edge)
    canonical = relation_status in {"canonical", "human_reviewed"}
    group = _edge_group(relation_type, str(edge.get("status", "")), native_type)
    return {
        "id": str(edge.get("id") or work_features.stable_graph_id("CW_PM_EDGE", source_id, target_id, relation_type)),
        "from": source_id,
        "to": target_id,
        "relationType": relation_type,
        "nativeRelationType": native_type or relation_type,
        "confidence": confidence,
        "strengthLabel": _strength_label(confidence, str(edge.get("status", ""))),
        "group": group,
        "canonical": canonical,
        "relationStatus": relation_status,
        "reason": str(edge.get("reason") or ""),
        "sourceCommandOrArtifact": source_artifact,
    }


def _read_file_title(path: Path, fallback: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return fallback


def _modified_at(project: Path, relative_path: str) -> str:
    candidate = project / str(relative_path or "").replace("\\", "/")
    try:
        if candidate.exists() and candidate.is_file():
            return datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return ""
    return ""


def _generated_view_records(project: Path) -> list[dict]:
    records: list[dict] = []
    candidates = [
        ("source-ledger", project / ".controlwork" / "memory" / "views" / "source-ledger.md"),
        ("handoff-packet", project / ".controlwork" / "memory" / "views" / "handoff-packet.md"),
        ("active-decisions", project / ".controlwork" / "memory" / "views" / "active-decisions.md"),
        ("open-questions", project / ".controlwork" / "memory" / "views" / "open-questions.md"),
    ]
    for view_id, path in candidates:
        records.append({
            "id": f"view:{view_id}",
            "title": _read_file_title(path, view_id.replace("-", " ").title()) if path.exists() else view_id.replace("-", " ").title(),
            "path": work_features.rel(project, path),
            "available": path.exists(),
            "kind": view_id,
        })
    return records


def _checkpoint_records(project: Path) -> list[dict]:
    root = project / work_features.CHECKPOINT_ROOT
    if not root.exists():
        return []
    records = []
    for path in sorted(root.glob("*.md"), reverse=True):
        records.append({
            "id": f"checkpoint:{path.stem}",
            "title": _read_file_title(path, path.stem),
            "path": work_features.rel(project, path),
        })
    return records


def _context_packet_records(project: Path) -> list[dict]:
    root = project / work_features.CONTEXT_PACKET_ROOT
    if not root.exists():
        return []
    records = []
    for path in sorted(root.glob("*.md"), reverse=True):
        records.append({
            "id": f"packet:{path.stem}",
            "title": _read_file_title(path, path.stem),
            "path": work_features.rel(project, path),
        })
    return records


def _session_date(session: dict) -> str:
    value = str(session.get("startedAt") or session.get("createdAt") or session.get("updatedAt") or "")
    return value[:10] if value else ""


def _session_duration(session: dict) -> str:
    started = str(session.get("startedAt") or "")
    ended = str(session.get("endedAt") or "")
    if not started or not ended:
        return ""
    return f"{started} to {ended}"


def _session_record(project: Path, session: dict) -> dict:
    session_id = str(session.get("id", ""))
    links = session.get("links", []) if isinstance(session.get("links"), list) else []
    referenced = [str(link.get("target", "")) for link in links if link.get("type") in {"references_entry", "references_decision"}]
    changed = [str(link.get("target", "")) for link in links if link.get("type") == "changes_memory"]
    memory_changed = session.get("memoryChanged")
    documents_changed = changed
    if not documents_changed and isinstance(memory_changed, list):
        documents_changed = list(memory_changed)
    packets = list(session.get("packets", [])) if isinstance(session.get("packets"), list) else []
    if not packets:
        packets = [str(link.get("target", "")) for link in links if link.get("type") == "produced_packet"]
    return {
        "id": work_features.stable_graph_id("CW_SESSION", session_id),
        "recordId": session_id,
        "title": str(session.get("topic") or session_id),
        "date": _session_date(session),
        "duration": _session_duration(session),
        "operator": str(session.get("operator") or session.get("client") or session.get("aiOperator") or "manual"),
        "mode": str(session.get("mode") or "manual"),
        "status": str(session.get("status") or "captured"),
        "summary": str(session.get("summary") or ""),
        "completedWork": list(session.get("completedWork", [])) if isinstance(session.get("completedWork"), list) else [],
        "decisionsMade": list(session.get("decisions", [])) if isinstance(session.get("decisions"), list) else [],
        "documentsReferenced": referenced,
        "documentsChanged": documents_changed,
        "followups": list(session.get("followups", [])) if isinstance(session.get("followups"), list) else [],
        "packetsOrCheckpointsProduced": packets,
        "path": work_features.rel(project, project / work_features.SESSION_ROOT / f"{work_features.slug(session_id)}.json"),
        "projectScope": "controlwork_state",
        "infrastructureScope": "controlwork_runtime",
        "infrastructureLayer": "controlwork_runtime",
        "links": links,
    }


def _session_edge_targets(
    session: dict,
    session_node_id: str,
    node_ids: set[str],
    path_to_id: dict[str, str],
    generated_nodes: list[dict],
) -> list[dict]:
    edges: list[dict] = []
    links = session.get("links", []) if isinstance(session.get("links"), list) else []
    for link in links:
        native_type = str(link.get("type") or "")
        target = str(link.get("target") or "")
        normalized = target.replace("\\", "/")
        target_id = path_to_id.get(normalized) or (target if target in node_ids else "")
        if not target_id and native_type in {"produced_packet", "left_followup", "uses_work_graphrag"}:
            slugged = work_features.slug(target or native_type)
            prefix = "packet" if native_type == "produced_packet" else "followup" if native_type == "left_followup" else "view"
            target_id = f"{prefix}:{slugged}"
            generated_nodes.append({
                "id": target_id,
                "title": target or native_type.replace("_", " "),
                "type": prefix,
                "area": "sessions" if prefix == "followup" else "views",
                "lifecycle": "needs_review" if prefix == "followup" else "active",
                "path": target,
                "summary": f"Session-linked {native_type.replace('_', ' ')}.",
            })
            node_ids.add(target_id)
        if not target_id:
            continue
        edges.append({
            "id": work_features.stable_graph_id("CW_PM_SESSION_EDGE", session_node_id, target_id, native_type),
            "sourceId": session_node_id,
            "targetId": target_id,
            "type": SESSION_LINK_RELATION_MAP.get(native_type, "references"),
            "confidence": "explicit_link",
            "status": "canonical",
            "reason": f"Session link {native_type} recorded in .controlwork/sessions.",
            "nativeType": native_type,
        })
    return edges


def _file_system_edges(base_document: dict, folder_documents: list[dict], scan_documents: list[dict]) -> list[dict]:
    edges: list[dict] = []
    folder_ids = {_folder_key(str(document.get("path", ""))): str(document.get("id", "")) for document in folder_documents}
    file_ids = {str(document.get("path", "")).replace("\\", "/"): str(document.get("id", "")) for document in scan_documents}
    root_id = folder_ids.get(".")
    if root_id:
        edges.append({
            "id": work_features.stable_graph_id("CW_PM_FS_EDGE", base_document["id"], root_id, "project-root"),
            "sourceId": base_document["id"],
            "targetId": root_id,
            "type": "references",
            "confidence": "explicit_link",
            "status": "suggested",
            "reason": "The master document anchors the generated project folder map. The folder exists in local scan evidence.",
            "nativeType": "project_map_root",
        })

    for folder in folder_documents:
        path = _folder_key(str(folder.get("path", "")))
        folder_id = str(folder.get("id", ""))
        if path == "." or not folder_id:
            continue
        parent = _folder_key(str(Path(path).parent).replace("\\", "/"))
        parent_id = folder_ids.get(parent) or root_id
        if not parent_id:
            continue
        edges.append({
            "id": work_features.stable_graph_id("CW_PM_FS_EDGE", parent_id, folder_id, "contains"),
            "sourceId": parent_id,
            "targetId": folder_id,
            "type": "contains",
            "confidence": "explicit_link",
            "status": "canonical",
            "reason": "Folder containment comes directly from the latest local file scan.",
            "nativeType": "filesystem_contains",
        })

    for document in scan_documents:
        path = str(document.get("path", "")).replace("\\", "/")
        document_id = file_ids.get(path)
        if not document_id:
            continue
        parent = _folder_key(str(Path(path).parent).replace("\\", "/"))
        parent_id = folder_ids.get(parent) or root_id
        if not parent_id:
            continue
        edges.append({
            "id": work_features.stable_graph_id("CW_PM_FS_EDGE", parent_id, document_id, "contains"),
            "sourceId": parent_id,
            "targetId": document_id,
            "type": "contains",
            "confidence": "explicit_link",
            "status": "canonical",
            "reason": "File containment comes directly from the latest local file scan.",
            "nativeType": "filesystem_contains",
        })
    return edges


def _scan_analysis_edges(scan_inventory: dict, path_to_id: dict[str, str]) -> list[dict]:
    edges: list[dict] = []
    for group in scan_inventory.get("duplicateGroups", []):
        records = group.get("records", []) if isinstance(group, dict) else []
        paths = [str(item.get("path", "")).replace("\\", "/") for item in records if isinstance(item, dict)]
        if len(paths) < 2:
            continue
        source_id = path_to_id.get(paths[0])
        if not source_id:
            continue
        for path in paths[1:12]:
            target_id = path_to_id.get(path)
            if not target_id:
                continue
            edges.append({
                "id": work_features.stable_graph_id("CW_PM_SCAN_ANALYSIS_EDGE", source_id, target_id, "duplicate"),
                "sourceId": source_id,
                "targetId": target_id,
                "type": "related_topic",
                "confidence": "strong_topic_overlap",
                "status": "suggested",
                "reason": group.get("reason") or "Duplicate candidate from scan analysis. Needs human review.",
                "nativeType": "duplicate_candidate",
            })

    for family in scan_inventory.get("versionCandidates", []):
        records = family.get("records", []) if isinstance(family, dict) else []
        ordered_paths = [str(item.get("path", "")).replace("\\", "/") for item in records if isinstance(item, dict)]
        if len(ordered_paths) < 2:
            continue
        latest_path = str(family.get("latestPath") or ordered_paths[0]).replace("\\", "/")
        source_id = path_to_id.get(latest_path)
        if not source_id:
            continue
        for path in ordered_paths:
            target_id = path_to_id.get(path)
            if not target_id or target_id == source_id:
                continue
            edges.append({
                "id": work_features.stable_graph_id("CW_PM_SCAN_ANALYSIS_EDGE", source_id, target_id, "version"),
                "sourceId": source_id,
                "targetId": target_id,
                "type": "supersedes",
                "confidence": "strong_topic_overlap",
                "status": "suggested",
                "reason": family.get("reason") or "Possible newer/older version relation from filename and content hash analysis. Needs human review.",
                "nativeType": "version_candidate",
            })
    return edges


def build_project_map_payload(
    project: Path,
    distribution: str = "embedded_controlcoding",
    scan_refresh: dict | None = None,
) -> dict:
    project = project.resolve()
    config = _read_config(project)
    metadata = _controlwork_metadata(project)
    base_document = _project_base_document(project, config, metadata)
    canonical_context = str(config.get("canonicalContext") or "CONTROLWORK.md")
    scan_inventory = _scan_inventory(project, base_document["path"], canonical_context, distribution)
    project_profile = _project_profile(scan_inventory)
    infrastructure_profile = _infrastructure_profile(distribution, base_document, scan_inventory)
    project_understanding = work_features.read_project_understanding(project)
    if not project_understanding:
        project_understanding = work_features.build_project_understanding(
            project,
            work_features.read_file_index(project),
            analysis=work_features.read_json(project / ".controlwork" / "ingestion" / "scan-analysis.json"),
            distribution=distribution,
        )
    organization_workflow = _organization_workflow(project_profile, scan_inventory)
    master_document = _master_document(project, base_document, project_profile, scan_inventory)
    graph = work_features.portable_graph(project)
    graph_nodes = {str(node.get("id", "")): node for node in graph.get("nodes", [])}
    entries = work_features.portable_graph_entries(project)
    node_ids: set[str] = set()
    path_to_id: dict[str, str] = {}
    documents: list[dict] = []
    topic_counter: Counter[str] = Counter()
    topic_areas: dict[str, set[str]] = {}

    documents.append(base_document)
    node_ids.add(base_document["id"])
    path_to_id[base_document["path"].replace("\\", "/")] = base_document["id"]
    for topic in base_document["topics"]:
        topic_counter[topic] += 1
        topic_areas.setdefault(topic, set()).add(base_document["area"])

    folder_documents = _folder_document_candidates(scan_inventory)
    for document in folder_documents:
        documents.append(document)
        node_ids.add(document["id"])
        path_to_id[_folder_key(str(document.get("path", "")))] = document["id"]
        for topic in document["topics"]:
            topic_counter[topic] += 1
            topic_areas.setdefault(topic, set()).add(document["area"])

    scan_documents = _scan_document_candidates(scan_inventory)
    for document in scan_documents:
        documents.append(document)
        node_ids.add(document["id"])
        path_to_id[document["path"].replace("\\", "/")] = document["id"]
        for topic in document["topics"]:
            topic_counter[topic] += 1
            topic_areas.setdefault(topic, set()).add(document["area"])

    for entry in entries:
        if entry.get("type") == "session":
            continue
        doc_id = str(entry.get("id", ""))
        path = str(entry.get("path", ""))
        if path.replace("\\", "/") == str(config.get("canonicalContext") or "CONTROLWORK.md"):
            continue
        if doc_id == base_document["id"] or path.replace("\\", "/") == base_document["path"]:
            continue
        area = str(entry.get("area") or "context")
        lifecycle = str(entry.get("lifecycle") or "captured")
        source_path = str(entry.get("source") or "")
        source_kind = _source_kind(source_path or path, area)
        topics = _entry_topics(entry)
        for topic in topics:
            topic_counter[topic] += 1
            topic_areas.setdefault(topic, set()).add(area)
        node_ids.add(doc_id)
        path_to_id[path.replace("\\", "/")] = doc_id
        documents.append({
            "id": doc_id,
            "title": str(entry.get("title") or Path(path).stem or "Untitled"),
            "type": _node_type_to_document_type(str(entry.get("type") or "")),
            "area": area,
            "lifecycle": lifecycle if lifecycle in LIFECYCLES else "captured",
            "path": path,
            "summary": _summary(entry.get("body", ""), fallback=str(entry.get("title") or "")),
            "sourceKind": source_kind,
            "sourcePath": source_path,
            "topics": topics,
            "reviewStatus": _review_status(lifecycle, source_kind),
            "updatedAt": _modified_at(project, path),
            "degree": 0,
            "projectScope": "controlwork_state",
            "projectRole": "controlwork_memory",
            "infrastructureScope": "controlwork_runtime",
            "infrastructureLayer": "controlwork_runtime",
            "isControlWorkRecord": True,
        })

    generated_nodes: list[dict] = []
    sessions = [_session_record(project, session) for session in work_features.iter_sessions(project)]
    for session in sessions:
        node_ids.add(session["id"])
        path_to_id[session["path"].replace("\\", "/")] = session["id"]

    views = _generated_view_records(project)
    for view in views:
        node_ids.add(view["id"])

    checkpoint_records = _checkpoint_records(project)
    packet_records = _context_packet_records(project)
    for item in [*checkpoint_records, *packet_records]:
        node_ids.add(item["id"])
        path_to_id[str(item.get("path", "")).replace("\\", "/")] = item["id"]

    raw_edges: list[dict] = []
    raw_edges.extend(graph.get("edges", []))
    for suggestion in graph.get("suggestions", []):
        if suggestion.get("status") == "rejected":
            continue
        raw_edges.append(suggestion)
    for session_payload in work_features.iter_sessions(project):
        session_node_id = work_features.stable_graph_id("CW_SESSION", str(session_payload.get("id", "")))
        raw_edges.extend(_session_edge_targets(session_payload, session_node_id, node_ids, path_to_id, generated_nodes))
    raw_edges.extend(_file_system_edges(base_document, folder_documents, scan_documents))
    raw_edges.extend(_scan_analysis_edges(scan_inventory, path_to_id))

    for node in generated_nodes:
        node_ids.add(node["id"])

    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    for edge in raw_edges:
        native_type = str(edge.get("nativeType") or edge.get("type") or "")
        item = _project_map_edge(edge, node_ids, "cw.py graph export / cc memory work-graph export", native_type=native_type)
        if item:
            edge_key = (item["from"], item["to"], item["relationType"], item["nativeRelationType"])
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append(item)

    degree_counter: Counter[str] = Counter()
    for edge in edges:
        degree_counter[edge["from"]] += 1
        degree_counter[edge["to"]] += 1
    for document in documents:
        document["degree"] = degree_counter[document["id"]]

    topics = [
        {
            "id": f"topic:{work_features.slug(label)}",
            "label": label,
            "summary": f"Generated topic candidate from ControlWork titles, source metadata, summaries, and reviewed entries.",
            "relatedAreas": sorted(topic_areas.get(label, set())),
            "documentCount": count,
        }
        for label, count in topic_counter.most_common(24)
    ]
    topic_ids = {topic["id"] for topic in topics}
    node_ids.update(topic_ids)

    by_area = Counter(document["area"] for document in documents)
    by_lifecycle = Counter(document["lifecycle"] for document in documents)
    by_source_kind = Counter(document["sourceKind"] for document in documents)
    by_topic = Counter(topic for document in documents for topic in document["topics"])
    by_project_role = Counter(str(document.get("projectRole", "")) for document in documents if document.get("projectRole"))
    by_corpus_subtype = Counter(str(document.get("corpusSubtype", "")) for document in documents if document.get("corpusSubtype"))
    by_infrastructure_scope = Counter(str(document.get("infrastructureScope", "")) for document in documents if document.get("infrastructureScope"))
    memory_root = str(config.get("memory", {}).get("root") or ".controlwork/memory")

    scan_document_ids = {document["path"]: document["id"] for document in scan_documents}
    for file_record in scan_inventory.get("files", []):
        path = str(file_record.get("path", ""))
        if path in scan_document_ids:
            file_record["documentId"] = scan_document_ids[path]

    payload = {
        "projectMapContractVersion": PROJECT_MAP_CONTRACT_VERSION,
        "generatedAt": work_features.utc_iso(),
        "sourceOfTruth": False,
        "scanRefresh": scan_refresh or {
            "enabled": False,
            "policy": "No file scan refresh metadata was provided for this Project Map generation.",
        },
        "externalCalls": {
            "aiCalls": False,
            "networkCalls": False,
            "subprocessCalls": False,
            "automaticLocalCommandExecution": False,
        },
        "project": {
            "title": metadata["title"],
            "rootPath": str(project),
            "description": metadata["description"],
            "baseDocumentPath": base_document["path"],
            "baseDocumentId": base_document["id"],
            "baseDocumentExists": bool((project / base_document["path"]).exists()),
            "canonicalContextPath": canonical_context,
            "internalContextPath": canonical_context,
            "memoryRootPath": memory_root,
            "distribution": str(config.get("distribution") or distribution),
        },
        "structure": {
            "memoryAreas": [
                {"id": area, "label": area.replace("_", " ").title(), "path": f"{memory_root}/{area}", "detail": AREA_DETAILS[area]}
                for area in MEMORY_AREAS
            ],
            "otherControlWorkFolders": SUPPORT_FOLDERS,
            "countsByArea": dict(sorted(by_area.items())),
            "countsByLifecycle": dict(sorted(by_lifecycle.items())),
            "countsBySourceKind": dict(sorted(by_source_kind.items())),
            "countsByTopic": dict(by_topic.most_common(24)),
            "countsByProjectRole": dict(by_project_role.most_common()),
            "countsByCorpusSubtype": dict(by_corpus_subtype.most_common()),
            "countsByInfrastructureScope": dict(by_infrastructure_scope.most_common()),
            "scannedFolders": scan_inventory["folders"],
            "scannedFiles": scan_inventory["files"],
            "countsByFolder": scan_inventory["countsByFolder"],
            "countsByFileKind": scan_inventory["countsByFileKind"],
            "countsByScanStatus": scan_inventory["countsByScanStatus"],
            "countsByReviewStatus": scan_inventory["countsByReviewStatus"],
            "scanAnalysis": {
                "summary": scan_inventory["analysisSummary"],
                "duplicateGroups": scan_inventory["duplicateGroups"],
                "versionCandidates": scan_inventory["versionCandidates"],
                "conflictCandidates": scan_inventory["conflictCandidates"],
                "policy": scan_inventory["policy"],
                "limited": scan_inventory["limited"],
                "totalProjectFiles": scan_inventory["totalProjectFiles"],
            },
        },
        "documents": documents,
        "topics": topics,
        "edges": edges,
        "sessions": sessions,
        "projectProfile": project_profile,
        "projectUnderstanding": project_understanding,
        "infrastructure": infrastructure_profile,
        "organizationWorkflow": organization_workflow,
        "masterDocument": master_document,
        "views": {
            "availableGeneratedViews": views,
            "sourceLedger": next((item for item in views if item["kind"] == "source-ledger"), {}),
            "handoffPacket": next((item for item in views if item["kind"] == "handoff-packet"), {}),
            "activeDecisions": next((item for item in views if item["kind"] == "active-decisions"), {}),
            "openQuestions": next((item for item in views if item["kind"] == "open-questions"), {}),
            "checkpoints": checkpoint_records,
            "contextPackets": packet_records,
            "generatedSessionNodes": generated_nodes,
        },
        "graphRag": {
            "mode": "metadata_graph_only",
            "contentGraphRagAvailable": any(
                document.get("isControlWorkRecord") and document.get("sourceKind") not in {"", "manual"}
                for document in documents
            ),
            "nodes": len(node_ids),
            "documents": len(documents),
            "scanEvidenceDocuments": len(scan_documents),
            "folderEvidenceDocuments": len(folder_documents),
            "edges": len(edges),
            "filesystemEdges": sum(1 for edge in edges if edge.get("nativeRelationType") == "filesystem_contains"),
            "scanAnalysisEdges": sum(1 for edge in edges if edge.get("nativeRelationType") in {"duplicate_candidate", "version_candidate"}),
            "suggestions": sum(1 for edge in edges if edge.get("relationStatus") == "suggested"),
            "sourceArtifacts": [
                ".controlwork/ingestion/file-index.json",
                ".controlwork/ingestion/scan-analysis.json",
                "cw.py graph export / cc memory work-graph export",
            ],
            "policy": "This is a local filesystem and metadata graph, not content GraphRAG over extracted document text. It does not call a model and does not accept suggested scan-analysis relations as truth.",
        },
        "contract": {
            "lifecycleValues": LIFECYCLES,
            "portableRelationTypes": PORTABLE_RELATION_TYPES,
            "portableConfidenceValues": PORTABLE_CONFIDENCE_VALUES,
            "infrastructureScopeValues": INFRASTRUCTURE_SCOPE_VALUES,
            "edgeGroups": EDGE_GROUPS,
            "relationPolicy": "Suggested relations are review evidence only until accepted or human-reviewed.",
            "colorPolicy": "Node color encodes ControlWork area or role. Edge color encodes relation type. Weight and opacity encode confidence.",
        },
        "sourceArtifacts": {
            "dashboard": "cw.py dashboard / cc memory work-dashboard",
            "query": "cw.py query / cc memory work-query",
            "graph": "cw.py graph export / cc memory work-graph export",
            "fileIndex": ".controlwork/ingestion/file-index.json",
            "scanAnalysis": ".controlwork/ingestion/scan-analysis.json",
            "sessionRoot": ".controlwork/sessions/",
        },
    }
    return payload


def validate_project_map_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("project", "structure", "documents", "topics", "edges", "sessions", "views", "projectUnderstanding", "infrastructure"):
        if key not in payload:
            errors.append(f"missing top-level key: {key}")
    if payload.get("projectMapContractVersion") != PROJECT_MAP_CONTRACT_VERSION:
        errors.append("unexpected project map contract version")
    project_info = payload.get("project", {}) if isinstance(payload.get("project"), dict) else {}
    base_document_path = str(project_info.get("baseDocumentPath", ""))
    if not base_document_path:
        errors.append("missing project base document path")
    if base_document_path == str(project_info.get("canonicalContextPath", "")) or base_document_path == "CONTROLWORK.md":
        errors.append("project base document must be separate from CONTROLWORK.md")

    node_ids = {str(document.get("id", "")) for document in payload.get("documents", [])}
    node_ids.update(str(topic.get("id", "")) for topic in payload.get("topics", []))
    node_ids.update(str(session.get("id", "")) for session in payload.get("sessions", []))
    views = payload.get("views", {}) if isinstance(payload.get("views"), dict) else {}
    node_ids.update(str(view.get("id", "")) for view in views.get("availableGeneratedViews", []))
    node_ids.update(str(view.get("id", "")) for view in views.get("checkpoints", []))
    node_ids.update(str(view.get("id", "")) for view in views.get("contextPackets", []))
    node_ids.update(str(view.get("id", "")) for view in views.get("generatedSessionNodes", []))
    node_ids.discard("")

    for document in payload.get("documents", []):
        if document.get("path") == "CONTROLWORK.md":
            errors.append("CONTROLWORK.md must not be presented as a project document")
        area = str(document.get("area", ""))
        if area not in {*MEMORY_AREAS, "context"}:
            errors.append(f"unknown document area: {area}")
        lifecycle = str(document.get("lifecycle", ""))
        if lifecycle not in LIFECYCLES:
            errors.append(f"unknown document lifecycle: {lifecycle}")
        infrastructure_scope = str(document.get("infrastructureScope", "project_corpus"))
        if infrastructure_scope not in INFRASTRUCTURE_SCOPE_VALUES:
            errors.append(f"unknown infrastructure scope: {infrastructure_scope}")
        if document.get("isControlWorkRecord") and infrastructure_scope != "controlwork_runtime":
            errors.append("ControlWork records must use controlwork_runtime infrastructure scope")
        if document.get("isBaseDocument") and infrastructure_scope == "controlwork_runtime":
            errors.append("project base document must not use ControlWork runtime infrastructure scope")

    session_ids = {str(session.get("id", "")) for session in payload.get("sessions", [])}
    for edge in payload.get("edges", []):
        edge_id = str(edge.get("id", ""))
        relation_type = str(edge.get("relationType", ""))
        confidence = str(edge.get("confidence", ""))
        group = str(edge.get("group", ""))
        if relation_type not in PORTABLE_RELATION_TYPES:
            errors.append(f"unknown relation type for {edge_id}: {relation_type}")
        if confidence not in PORTABLE_CONFIDENCE_VALUES:
            errors.append(f"unknown confidence for {edge_id}: {confidence}")
        if group not in EDGE_GROUPS:
            errors.append(f"unknown edge group for {edge_id}: {group}")
        if str(edge.get("from", "")) not in node_ids:
            errors.append(f"orphan edge source for {edge_id}: {edge.get('from', '')}")
        if str(edge.get("to", "")) not in node_ids:
            errors.append(f"orphan edge target for {edge_id}: {edge.get('to', '')}")
        if edge.get("canonical") and str(edge.get("relationStatus")) == "suggested":
            errors.append(f"suggested edge marked canonical: {edge_id}")
    for session_id in session_ids:
        if session_id and session_id not in node_ids:
            errors.append(f"session is not selectable as a graph root: {session_id}")
    return errors


def project_map_json(payload: dict) -> str:
    return json.dumps(payload, indent=2) + "\n"
