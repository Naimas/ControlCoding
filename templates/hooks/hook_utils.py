#!/usr/bin/env python3
"""Shared utilities for ControlCoding hook scripts.

All hooks use only Python stdlib (no pip dependencies).
This module is copied to adopter projects by cc init alongside the hooks.
"""

import os
from pathlib import Path


CONTROL_PLANE_DIRS = (".controlcoding", ".claude")


def normalize_protected_zones(raw):
    """Normalize protected_zones to list-of-dict format.

    Accepts:
      - list-of-dict: [{"path": "x", "level": "deny", "description": "..."}]
      - dict with levels: {"deny": ["x", ...], "warn": ["y", ...]}
      - mixed list: ["x", {"path": "y", "level": "deny"}]
    Returns:
      - [{"path": "...", "level": "...", "description": "..."}]

    Invalid entries (missing "path" key, non-string, non-dict) are skipped.
    Input is never mutated.
    """
    result = []

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                result.append({"path": item, "level": "warn", "description": ""})
            elif isinstance(item, dict) and "path" in item:
                result.append({
                    "path": item.get("path", ""),
                    "level": item.get("level", "warn"),
                    "description": item.get("description", ""),
                })
        return result

    if isinstance(raw, dict):
        for level in ("deny", "warn"):
            for item in raw.get(level, []):
                if isinstance(item, str):
                    result.append({"path": item, "level": level, "description": ""})
                elif isinstance(item, dict) and "path" in item:
                    result.append({
                        "path": item.get("path", ""),
                        "level": item.get("level", level),
                        "description": item.get("description", ""),
                    })
        return result

    return []


def find_project_root(start=None, *, env_var: str = ""):
    """Find the project root using .git or control-plane markers."""
    if env_var:
        env_root = os.environ.get(env_var)
        if env_root:
            candidate = Path(env_root)
            if candidate.exists():
                return candidate.resolve()

    origin = Path(start).resolve() if start is not None else Path.cwd().resolve()
    here = origin.parent if origin.is_file() else origin

    for candidate in [here, *here.parents[:5]]:
        if (candidate / ".git").is_dir():
            return candidate
        for dirname in CONTROL_PLANE_DIRS:
            control_dir = candidate / dirname
            if (control_dir / "settings.json").exists() or (control_dir / "cc_config.json").exists():
                return candidate

    return here


def control_plane_existing_path(project_root, relative_name: str):
    """Return the existing control-plane file, preferring canonical over legacy."""
    project_root = Path(project_root)
    for dirname in CONTROL_PLANE_DIRS:
        candidate = project_root / dirname / relative_name
        if candidate.exists():
            return candidate
    return None


def control_plane_write_path(project_root, relative_name: str):
    """Return the preferred control-plane path for new writes.

    Uses `.controlcoding/` when present or when no legacy-only repo exists.
    Preserves `.claude/` only for legacy repos that do not yet have the canonical
    control-plane directory.
    """
    project_root = Path(project_root)
    canonical_dir = project_root / CONTROL_PLANE_DIRS[0]
    legacy_dir = project_root / CONTROL_PLANE_DIRS[1]
    if canonical_dir.exists() or not legacy_dir.exists():
        return canonical_dir / relative_name
    return legacy_dir / relative_name


def control_plane_path(project_root, relative_name: str):
    """Return the best control-plane path for reading/updating a file."""
    existing = control_plane_existing_path(project_root, relative_name)
    if existing is not None:
        return existing
    return control_plane_write_path(project_root, relative_name)


def control_plane_display_path(project_root, relative_name: str):
    """Return a normalized relative display path for the active control-plane file."""
    path = control_plane_path(project_root, relative_name)
    try:
        rel = path.relative_to(project_root)
    except ValueError:
        rel = path
    return str(rel).replace("\\", "/")
