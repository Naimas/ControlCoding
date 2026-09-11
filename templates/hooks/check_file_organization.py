#!/usr/bin/env python3
"""check_file_organization.py - ControlCoding file organization enforcer.

PreToolUse hook for Claude Code: blocks creation of working documents
(plans, design docs, reasoning logs, criteria) outside the standard
dev/ and devlog/ subdirectory structure.

Files that match known patterns but are being written to the wrong
location are blocked with a suggestion for the correct path.

Exit 0 = allowed (file doesn't match patterns, or is in the right place).
Exit 2 = blocked (file matches a pattern but is in the wrong directory).

When `.controlcoding/cc_config.json` sets `"documentation_mode": "project_managed"`,
this hook becomes a no-op and leaves documentation layout to the project.

Protocol (Claude Code hooks):
- Receives JSON on stdin with tool_name and tool_input
- tool_input.file_path contains the target file path
- Only triggers on Write (new files), not Edit (existing files)

Setup: configure in .controlcoding/settings.json:
    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Write",
            "hooks": [
              {
                "type": "command",
                "command": "python hooks/check_file_organization.py"
              }
            ]
          }
        ]
      }
    }
"""

import json
import os
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import control_plane_path, find_project_root
except ImportError:
    from templates.hooks.hook_utils import control_plane_path, find_project_root


# Patterns that identify working documents and their required subdirectory.
# Each entry: (pattern_function, required_subdir, description)
#
# required_subdir is used ONLY for the suggestion message when blocking.
# The actual check is against ALLOWED_PATHS below.
FILE_ORG_RULES = [
    (
        lambda f: f.startswith("plan-") or f.startswith("plan_"),
        "dev/plans",
        "plan files",
    ),
    (
        lambda f: (f.startswith("design-") or f.startswith("design_"))
        and f.endswith(".md"),
        "dev/design",
        "design documents",
    ),
    (
        lambda f: f.startswith("reasoning") and f.endswith(".md"),
        "devlog/reasoning",
        "reasoning logs",
    ),
    (
        lambda f: (f.startswith("criteria") or f.startswith("acceptance"))
        and f.endswith(".md"),
        "dev/criteria",
        "criteria documents",
    ),
]

# Paths that are always allowed even if the filename matches a pattern.
# These are directories where matched files are acceptable.
#
# Two hierarchies exist (see CLAUDE.md "Document Organization"):
#   dev/    = project as it IS and SHOULD BE (plans, design, architecture)
#   devlog/ = history of WHAT HAPPENED (chronological, append-only)
ALLOWED_PATHS = [
    # dev/ hierarchy (plans, design, architecture)
    # Each subfolder has its own archive/ for superseded versions.
    "dev/plans/",
    "dev/plans/archive/",
    "dev/plans/deprecated/",
    "dev/design/",
    "dev/design/archive/",
    "dev/design/deprecated/",
    "dev/criteria/",
    "dev/criteria/archive/",
    "dev/criteria/deprecated/",
    # devlog/ hierarchy (chronological records)
    "devlog/plans/",
    "devlog/design/",
    "devlog/reasoning/",
    "devlog/criteria/",
    "devlog/archive/",
]


def _emit_block(reason: str) -> None:
    """Block in a way compatible with Claude exit-code hooks."""
    print(json.dumps({"decision": "block", "reason": reason}))
    print(reason, file=sys.stderr)
    sys.exit(2)


def _find_project_root():
    """Walk up from this file's directory looking for project root markers."""
    env_root = os.environ.get("CC_PROJECT_ROOT")
    if env_root:
        candidate = Path(env_root)
        if candidate.exists():
            return candidate.resolve()
    return find_project_root(__file__)


def _check_lift(project_root):
    """Check if this hook is temporarily lifted."""
    lift_path = control_plane_path(project_root, "hooks_lifted.json")
    if not lift_path.exists():
        return False
    try:
        data = json.loads(lift_path.read_text(encoding="utf-8"))
        hook_name = Path(__file__).name
        if hook_name in data.get("lifted", []):
            from datetime import datetime, timezone
            lifted_at = data.get("lifted_at", "")
            if lifted_at:
                ts = datetime.fromisoformat(lifted_at)
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age < 3600:
                    return True
        return False
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _get_documentation_mode(project_root):
    """Load documentation_mode from cc_config.json, defaulting to managed."""
    config_path = control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return "managed"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "managed"
    if not isinstance(data, dict):
        return "managed"
    return data.get("documentation_mode", "managed")


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if not isinstance(input_data, dict):
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Write":
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    project_root = _find_project_root()

    # Check lift
    if _check_lift(project_root):
        print(f"[LIFTED] {Path(__file__).name} is temporarily disabled")
        sys.exit(0)

    if _get_documentation_mode(project_root) == "project_managed":
        sys.exit(0)

    # Normalize path relative to project root
    norm = os.path.normpath(file_path).replace("\\", "/")
    try:
        rel = str(Path(file_path).resolve().relative_to(project_root))
    except ValueError:
        rel = norm
    rel = rel.replace("\\", "/")

    filename = os.path.basename(rel).lower()

    # Check if filename matches any known pattern
    for matcher, required_dir, description in FILE_ORG_RULES:
        if not matcher(filename):
            continue

        # File matches a pattern - check if it's in the right place
        # Use normalized full path to check for allowed directory segments
        norm_lower = norm.lower()
        in_allowed = False
        for allowed in ALLOWED_PATHS:
            # Check if the allowed path appears as a directory segment
            # e.g., "/project/devlog/plans/file.json" contains "devlog/plans/"
            if ("/" + allowed) in norm_lower or norm_lower.startswith(allowed):
                in_allowed = True
                break

        if in_allowed:
            sys.exit(0)

        # File is in the wrong place - block
        suggested_path = f"{required_dir}/{os.path.basename(rel)}"
        _emit_block(
            f"CONTROL CODING: File organization violation. "
            f"{os.path.basename(rel)} matches the pattern for "
            f"{description} but is not in {required_dir}/. "
            f"Create it at: {suggested_path}"
        )

    # No pattern matched - allow
    sys.exit(0)


if __name__ == "__main__":
    main()
