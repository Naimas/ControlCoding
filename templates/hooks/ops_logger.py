#!/usr/bin/env python3
"""ops_logger.py - Log every file operation with timestamp.

PostToolUse hook: records each Edit/Write/Bash operation to
.controlcoding/ops_log.jsonl (legacy .claude fallback).
Useful for post-run analysis of what was modified and in what order.
Also used by CodeWarden watchdog to detect file changes in real time.

Always exits 0 (never blocks).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CANONICAL_CONTROL_DIR = ".controlcoding"
LEGACY_CONTROL_DIR = ".claude"
OPS_LOG_FILENAME = "ops_log.jsonl"


def control_plane_log_path(project_root: Path) -> Path:
    canonical_dir = project_root / CANONICAL_CONTROL_DIR
    legacy_dir = project_root / LEGACY_CONTROL_DIR
    if canonical_dir.exists() or not legacy_dir.exists():
        return canonical_dir / OPS_LOG_FILENAME
    return legacy_dir / OPS_LOG_FILENAME


def append_log_entry(project_root: Path, entry: dict) -> None:
    log_file = control_plane_log_path(project_root)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Extract file path depending on tool
    file_path = ""
    action = ""
    if tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "")
        action = tool_name.lower()
    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        action = "bash"
        file_path = command[:120]  # truncate for log readability
    else:
        sys.exit(0)

    if not file_path and not action:
        sys.exit(0)

    # Normalize path
    norm = os.path.normpath(file_path).replace("\\", "/") if file_path else ""

    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "tool": tool_name,
        "action": action,
        "file": norm,
    }

    # Write to .controlcoding/ops_log.jsonl (legacy .claude fallback)
    project_root = Path(__file__).resolve().parent.parent

    try:
        append_log_entry(project_root, entry)
    except OSError:
        pass  # never fail

    sys.exit(0)


if __name__ == "__main__":
    main()
