"""Deterministic document classification for the Project Memory Engine."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .schema import HOST_CONTEXT_FILES

_COMMON_WORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "and",
    "are",
    "before",
    "between",
    "but",
    "can",
    "code",
    "controlcoding",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "not",
    "only",
    "project",
    "should",
    "that",
    "the",
    "their",
    "this",
    "under",
    "use",
    "when",
    "with",
    "without",
}

_GENERIC_HEADINGS = {
    "background",
    "context",
    "goals",
    "notes",
    "overview",
    "status",
    "summary",
}


def _tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_/-]{2,}", text)
        if token.lower() not in _COMMON_WORDS
    ]


def _keyword_list(*parts: str, limit: int = 16) -> list[str]:
    counts = Counter(_tokenize(" ".join(parts)))
    return [token for token, _count in counts.most_common(limit)]


def _extract_markdown_headings(text: str) -> list[str]:
    headings: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if match:
            title = match.group(2).strip().strip("#").strip()
            if title:
                headings.append(title[:160])
    return headings


def _normalize_heading(value: str) -> str:
    tokens = [token for token in _tokenize(value) if token not in _GENERIC_HEADINGS]
    return " ".join(tokens[:12])


def _extract_reference_paths(text: str, source_path: str) -> list[str]:
    refs: set[str] = set()
    source_parent = Path(source_path).parent
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = target.strip().split("#", 1)[0].strip()
        if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        normalized = (source_parent / target).as_posix()
        refs.add(normalized)
    for target in re.findall(r"`([^`\n]+\.(?:md|rst|adoc|txt|py|ts|tsx|js|json|yaml|yml))`", text):
        target = target.strip().split("#", 1)[0]
        normalized = target if "/" in target or "\\" in target else (source_parent / target).as_posix()
        refs.add(normalized.replace("\\", "/"))
    return sorted(refs)


def _path_parts(path: Path) -> set[str]:
    return {part.lower() for part in path.parts}


def _classify_primary_type(rel_path: str, entity_type: str, headings: list[str], text: str) -> tuple[str, list[str]]:
    path = Path(rel_path)
    name = path.name.lower()
    stem = path.stem.lower()
    parts = _path_parts(path)
    heading_text = " ".join(headings).lower()
    sample = f"{name} {stem} {' '.join(parts)} {heading_text} {text[:2000].lower()}"
    reasons: list[str] = []

    if entity_type == "application_memory_component":
        return "application_memory_component", ["entity type indicates application-owned memory"]
    if path.name in HOST_CONTEXT_FILES:
        return "host_context_projection", ["host context filename"]
    if entity_type == "benchmark" or "benchmark" in parts:
        return "benchmark", ["benchmark path or entity type"]
    if entity_type == "test_node" or name.startswith("test_") or "tests" in parts:
        return "test_evidence", ["test path or entity type"]
    if entity_type == "decision" or "decision" in parts or "decisions" in parts or "decision" in stem:
        return "decision", ["decision path, filename, or entity type"]
    if entity_type == "plan" or "plans" in parts or "roadmap" in name or "plan" in stem:
        return "plan", ["plan path or filename"]
    if entity_type == "research_note" or "research" in parts or "research" in stem:
        return "research", ["research path or filename"]
    if entity_type == "handoff" or "handoff" in parts or "handoff" in stem:
        return "handoff", ["handoff path or filename"]
    if "design" in parts or "design" in stem or "architecture" in sample or "adr" in stem:
        return "design", ["design or architecture marker"]
    if name in {"status.md", "changelog.md"} or "release-notes" in stem:
        return "status", ["status, changelog, or release-note filename"]

    reasons.append("no stronger deterministic rule matched")
    return "general_document", reasons


def _classify_facets(rel_path: str, lifecycle: str, primary_type: str) -> list[str]:
    path = Path(rel_path)
    parts = _path_parts(path)
    facets: set[str] = set()
    if lifecycle in {"active", "implemented", "verified", "triaged"}:
        facets.add("active")
    if lifecycle in {"stale", "conflicting", "needs_review", "superseded", "archived", "legacy"}:
        facets.add(lifecycle)
    if parts & {"archive", "archived", "deprecated", "legacy", "old"}:
        facets.add("legacy")
    if parts & {"external", "vendor", "reference", "research"}:
        facets.add("external_source" if primary_type == "research" else "active")
    if ".controlcoding" in parts and "views" in parts:
        facets.add("generated_view")
    return sorted(facets)


def _classify_document(
    rel_path: str,
    text: str,
    entity_type: str,
    lifecycle: str,
    title: str = "",
) -> dict[str, Any]:
    headings = _extract_markdown_headings(text)
    primary_type, reasons = _classify_primary_type(rel_path, entity_type, headings, text)
    heading_keys = sorted(
        key
        for key in {_normalize_heading(heading) for heading in headings}
        if key
    )
    keywords = _keyword_list(rel_path, title, " ".join(headings), text[:4000])
    return {
        "document_type": primary_type,
        "document_facets": _classify_facets(rel_path, lifecycle, primary_type),
        "classification_reasons": reasons,
        "heading_titles": headings[:80],
        "heading_keys": heading_keys[:80],
        "keywords": keywords,
        "explicit_refs": _extract_reference_paths(text, rel_path),
    }


__all__ = [
    "_classify_document",
    "_extract_markdown_headings",
    "_keyword_list",
]
