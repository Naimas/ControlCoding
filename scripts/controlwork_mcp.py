#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Shield-1.0.0
# Copyright (c) 2026 Stefano Tonello.
"""Read-only ControlWork MCP server for embedded Project Plane memory.

Optional dependency:
    pip install fastmcp

Set CONTROLWORK_PROJECT_ROOT to the project root, or run from the project root.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastmcp import FastMCP

from cc_memory_lib import work_features as cw

PROJECT_ROOT = Path(os.environ.get("CONTROLWORK_PROJECT_ROOT", ".")).resolve()

mcp = FastMCP(
    "controlwork",
    instructions=(
        "Read-only ControlWork project memory tools. Tools are constrained to "
        f"the configured project root: {PROJECT_ROOT}. Write operations are not exposed."
    ),
)


def _root(project_root: str = "") -> Path:
    return Path(project_root).expanduser().resolve() if project_root else PROJECT_ROOT


@mcp.tool
def controlwork_status(project_root: str = "") -> dict:
    """Return ControlWork status and feature counts."""
    root = _root(project_root)
    return cw.feature_status_payload(root)


@mcp.tool
def controlwork_list_entries(project_root: str = "", area: str = "", lifecycle: str = "") -> dict:
    """List memory entries by optional area and lifecycle."""
    root = _root(project_root)
    entries = cw.iter_entries(root, area=area)
    if lifecycle:
        entries = [entry for entry in entries if entry.get("lifecycle") == lifecycle]
    return {"entries": entries}


@mcp.tool
def controlwork_read_entry(path: str, project_root: str = "") -> dict:
    """Read one project-relative memory or wiki file."""
    root = _root(project_root)
    return cw.controlwork_read_entry_payload(root, path)


@mcp.tool
def controlwork_search_memory(query: str, project_root: str = "", area: str = "", limit: int = 20) -> dict:
    """Search ControlWork memory entries."""
    root = _root(project_root)
    return {"matches": cw.search_memory(root, query=query, area=area, limit=limit)}


@mcp.tool
def controlwork_list_categories(project_root: str = "") -> dict:
    """List ControlWork categories."""
    root = _root(project_root)
    return {"categories": cw.read_category_registry(root).get("categories", [])}


@mcp.tool
def controlwork_context_pack(
    project_root: str = "",
    scope: str = "general",
    topic: str = "",
    limit: int = 10,
    include_legacy: bool = False,
) -> dict:
    """Build a scoped context packet without writing it."""
    root = _root(project_root)
    return {
        "packet": cw.build_context_pack(
            root,
            scope=scope,
            topic=topic,
            limit=limit,
            include_legacy=include_legacy,
            refresh_views=False,
        )
    }


if __name__ == "__main__":
    mcp.run()
