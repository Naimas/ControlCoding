#!/usr/bin/env python3
"""check_doc_compression.py - Semantic Fidelity enforcement hook.

PostToolUse hook for Claude Code: after every Edit/Write on documents in
dev/ or devlog/, checks if the file has been significantly shortened.
A significant reduction in size indicates compression/summarization,
which violates the Semantic Fidelity principle (methodology Section 9.14).

Exit 0 = allowed (with optional warning JSON on stdout).
Exit 2 = blocked - document was compressed.

How it works:
1. Reads the tool result from stdin (PostToolUse JSON)
2. Checks if the file is in a monitored directory (dev/, devlog/)
3. Compares current file size against the pre-edit size stored by git
4. If the file shrank by more than the threshold, emits DENY

The threshold is configurable:
- COMPRESSION_THRESHOLD env var (default 0.20 = 20% reduction triggers block)
- Files under MIN_SIZE_BYTES are exempt (default 500 bytes)

Does NOT block:
- New files (no previous version to compare)
- Files outside monitored directories
- Files below minimum size
- Files that grew or stayed the same size
"""

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import find_project_root
except ImportError:
    from templates.hooks.hook_utils import find_project_root


COMPRESSION_THRESHOLD = float(os.environ.get("COMPRESSION_THRESHOLD", "0.20"))
MIN_SIZE_BYTES = int(os.environ.get("COMPRESSION_MIN_SIZE", "500"))

MONITORED_DIRS = ["dev/", "devlog/"]


def _emit_block(reason: str) -> None:
    """Block in a way compatible with Claude exit-code hooks."""
    print(json.dumps({"decision": "block", "reason": reason}))
    print(reason, file=sys.stderr)
    sys.exit(2)


def _find_project_root():
    """Walk up from this file looking for .git/ or control-plane markers."""
    return find_project_root(__file__)


def _get_git_file_size(project_root: Path, rel_path: str) -> int | None:
    """Get the size of a file in the last git commit. Returns None if not tracked."""
    try:
        result = subprocess.run(
            ["git", "cat-file", "-s", f"HEAD:{rel_path}"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _is_monitored(rel_path: str) -> bool:
    """Check if the file is in a monitored directory."""
    normalized = rel_path.replace("\\", "/")
    return any(normalized.startswith(d) for d in MONITORED_DIRS)


def _is_document(file_path: str) -> bool:
    """Check if the file is a document (markdown or text)."""
    return file_path.endswith((".md", ".txt", ".json"))


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    project_root = _find_project_root()
    try:
        rel_path = str(Path(file_path).resolve().relative_to(project_root))
    except ValueError:
        sys.exit(0)

    rel_path_posix = rel_path.replace("\\", "/")

    if not _is_monitored(rel_path_posix):
        sys.exit(0)

    if not _is_document(rel_path_posix):
        sys.exit(0)

    # Get previous size from git
    old_size = _get_git_file_size(project_root, rel_path_posix)
    if old_size is None:
        # New file or not tracked - allow
        sys.exit(0)

    if old_size < MIN_SIZE_BYTES:
        # Too small to worry about compression
        sys.exit(0)

    # Get current size
    current_path = Path(file_path)
    if not current_path.exists():
        sys.exit(0)

    new_size = current_path.stat().st_size

    if new_size >= old_size:
        # File grew or stayed same - no compression
        sys.exit(0)

    reduction = (old_size - new_size) / old_size

    if reduction < COMPRESSION_THRESHOLD:
        # Reduction below threshold - allow
        sys.exit(0)

    # BLOCK: significant compression detected
    pct = int(reduction * 100)
    _emit_block(
        f"SEMANTIC FIDELITY VIOLATION: document shortened by {pct}% "
        f"({old_size} -> {new_size} bytes). "
        f"File: {rel_path_posix}. "
        f"Documents in {', '.join(MONITORED_DIRS)} must never be compressed or summarized. "
        f"If the document needs restructuring, split into linked sub-documents instead. "
        f"See methodology Section 9.14."
    )


if __name__ == "__main__":
    main()
