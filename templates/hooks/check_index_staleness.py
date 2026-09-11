#!/usr/bin/env python3
"""check_index_staleness.py - ControlCoding architecture index staleness detector.

PostToolUse hook for Claude Code: fires after every Write tool call.
If the new file is in a tracked directory (templates/scripts/, templates/hooks/,
dev/design/, scripts/), warns when the filename is not present in
dev/ARCHITECTURE_INDEX.md.

This is advisory only - always exits 0. The warning is printed to stderr
so Claude Code picks it up and surfaces it to the AI.

Exit 0 always (advisory, never blocks).

Protocol (Claude Code hooks):
- Receives JSON on stdin with tool_name and tool_input
- tool_input.file_path contains the target file path
- Only triggers on Write (new files)

Setup: configure in .controlcoding/settings.json under PostToolUse Write matcher.
"""

import json
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import find_project_root
except ImportError:
    from templates.hooks.hook_utils import find_project_root


def _find_project_root():
    """Walk up from cwd to find the project root (.git or control-plane marker)."""
    return find_project_root(Path.cwd())


def _in_tracked_dir(file_path: Path, project_root: Path) -> bool:
    """Return True if file_path is inside a tracked directory."""
    try:
        rel = file_path.relative_to(project_root)
    except ValueError:
        return False
    parts = rel.parts
    if len(parts) < 2:
        return False
    # tracked: templates/scripts/*.py, templates/hooks/*.py, dev/design/*.md, scripts/*.py
    if parts[0] == "templates" and parts[1] == "scripts" and file_path.suffix == ".py":
        return True
    if parts[0] == "templates" and parts[1] == "hooks" and file_path.suffix == ".py":
        return True
    if parts[0] == "dev" and parts[1] == "design" and file_path.suffix == ".md":
        return True
    if parts[0] == "scripts" and file_path.suffix == ".py":
        return True
    return False


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = data.get("tool_name", "") or data.get("tool", "")
    if tool_name != "Write":
        sys.exit(0)

    tool_input = data.get("tool_input", data.get("input", {}))
    file_path_str = tool_input.get("file_path", "")
    if not file_path_str:
        sys.exit(0)

    file_path = Path(file_path_str)
    project_root = _find_project_root()

    if not _in_tracked_dir(file_path, project_root):
        sys.exit(0)

    fname = file_path.name
    # Skip internal names
    if fname in {"__init__.py", "__pycache__"}:
        sys.exit(0)

    index_path = project_root / "dev" / "ARCHITECTURE_INDEX.md"
    if not index_path.exists():
        sys.exit(0)

    index_content = index_path.read_text(encoding="utf-8")
    if fname not in index_content:
        print(
            f"\n[INDEX WARNING] New file '{fname}' is not in dev/ARCHITECTURE_INDEX.md.\n"
            f"Run `cc index --fix` to add a stub entry, or add it manually.\n"
            f"Section: {_guess_section(file_path, project_root)}",
            file=sys.stderr,
        )

    sys.exit(0)


def _guess_section(file_path: Path, project_root: Path) -> str:
    try:
        rel = file_path.relative_to(project_root)
    except ValueError:
        return "unknown"
    parts = rel.parts
    if len(parts) >= 2:
        if parts[0] == "templates" and parts[1] == "scripts":
            return "MCP Servers (`templates/scripts/`)"
        if parts[0] == "templates" and parts[1] == "hooks":
            return "Hooks (`templates/hooks/`)"
        if parts[0] == "dev" and parts[1] == "design":
            return "Design Documents (`dev/design/`)"
        if parts[0] == "scripts":
            return "CLI Tool (`scripts/cc.py`)"
    return "unknown"


if __name__ == "__main__":
    main()
