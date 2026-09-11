#!/usr/bin/env python3
"""check_bash_writes.py - ControlCoding PostToolUse hook for Bash bypass detection.

PostToolUse hook for Claude Code: after every Bash command, checks if any
DENY-protected files were modified. If so, reverts them with git checkout
and emits a warning.

This closes the known limitation where Edit/Write hooks are bypassed by
writing files through Bash (cat, echo, tee, cp, python -c, etc.).

Exit 0 = always (PostToolUse hooks cannot block, only warn).

Protocol (Claude Code hooks):
- Receives JSON on stdin with tool_name and tool_input (the command that ran)
- Runs AFTER the Bash command has completed
- Uses git diff to detect what changed
"""

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import control_plane_path, find_project_root, normalize_protected_zones
except ImportError:
    from templates.hooks.hook_utils import control_plane_path, find_project_root, normalize_protected_zones

try:
    from feature_lock import check_module_perimeter
    HAS_FEATURE_LOCK = True
except ImportError:
    try:
        from templates.hooks.feature_lock import check_module_perimeter
        HAS_FEATURE_LOCK = True
    except ImportError:
        HAS_FEATURE_LOCK = False
        import sys as _sys
        print("[check_bash_writes] feature_lock.py not found - module perimeter checks disabled",
              file=_sys.stderr)


def _find_project_root():
    """Walk up from this file's directory looking for project root markers."""
    return find_project_root(__file__)


def _load_deny_zones(project_root):
    """Load DENY-level zones from cc_config.json or return empty list."""
    config_path = control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return [
            z["path"] for z in normalize_protected_zones(data.get("protected_zones", []))
            if z.get("level", "warn") == "deny"
        ]

    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _is_protected(file_path, deny_zones):
    """Check if a file path falls within any DENY zone."""
    norm = file_path.replace("\\", "/")
    for zone in deny_zones:
        zone_clean = zone.rstrip("/")
        # Check if file is inside the zone directory or matches the zone file
        if norm.startswith(zone_clean + "/") or norm == zone_clean:
            return True
        # Also check if any path segment matches (for relative paths)
        parts = norm.split("/")
        zone_parts = zone_clean.split("/")
        for i in range(len(parts) - len(zone_parts) + 1):
            if parts[i:i + len(zone_parts)] == zone_parts:
                return True
    return False


def _git_changed_files(project_root):
    """Get list of tracked files modified since last git state (unstaged changes)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _git_untracked_files(project_root):
    """Get list of new untracked files (not in git yet)."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _git_revert_file(project_root, file_path):
    """Revert a tracked file to its last committed state."""
    try:
        subprocess.run(
            ["git", "checkout", "--", file_path],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=10
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _remove_untracked_file(project_root, file_path):
    """Remove an untracked file that violates protection rules."""
    try:
        abs_path = Path(project_root) / file_path
        if abs_path.exists() and abs_path.is_file():
            abs_path.unlink()
            return True
    except OSError:
        pass
    return False


# Optional: import hook_logger if available
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_logger import log_event
    HAS_LOGGER = True
except ImportError:
    HAS_LOGGER = False


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if not isinstance(input_data, dict):
        sys.exit(0)

    # Only process Bash tool calls
    tool_name = input_data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    project_root = _find_project_root()
    deny_zones = _load_deny_zones(project_root)

    # Collect all files affected by the Bash command
    changed = _git_changed_files(project_root)  # tracked, modified
    untracked = _git_untracked_files(project_root)  # new, untracked

    if not changed and not untracked:
        sys.exit(0)

    # 1. Revert tracked files in DENY zones
    reverted = []
    if deny_zones:
        for f in changed:
            if _is_protected(f, deny_zones):
                if _git_revert_file(project_root, f):
                    reverted.append(f)
                    if HAS_LOGGER:
                        log_event(project_root, "REVERT", "bash_bypass", f,
                                  "Protected file modified via Bash - reverted")

    # 2. Remove untracked files in DENY zones
    removed_deny = []
    if deny_zones:
        for f in untracked:
            if _is_protected(f, deny_zones):
                if _remove_untracked_file(project_root, f):
                    removed_deny.append(f)
                    if HAS_LOGGER:
                        log_event(project_root, "REVERT", "bash_bypass_untracked", f,
                                  "Untracked file in protected zone via Bash - removed")

    if reverted or removed_deny:
        all_deny = reverted + removed_deny
        files_list = ", ".join(all_deny)
        print(json.dumps({
            "decision": "warn",
            "reason": (
                f"CONTROL CODING: Bash bypass detected. "
                f"Reverted/removed {len(all_deny)} protected file(s): {files_list}. "
                "DENY-protected files cannot be modified via Bash either. "
                "Use Edit/Write tools with a proper lift request if modification is necessary: "
                "python hooks/request_lift.py --file <path> --reason \"<why>\""
            ),
        }))

    # 3. Check module perimeter on remaining tracked files
    if HAS_FEATURE_LOCK:
        remaining = [f for f in changed if f not in reverted]
        fl_reverted = []
        for f in remaining:
            fl_result = check_module_perimeter(f, project_root)
            if fl_result["decision"] == "deny":
                if _git_revert_file(project_root, f):
                    fl_reverted.append(f)
                    if HAS_LOGGER:
                        log_event(project_root, "REVERT", "bash_module_perimeter",
                                  f, fl_result["message"])

        # 4. Check module perimeter on untracked files (NEW files created via Bash)
        remaining_untracked = [f for f in untracked if f not in removed_deny]
        fl_removed = []
        for f in remaining_untracked:
            fl_result = check_module_perimeter(f, project_root)
            if fl_result["decision"] == "deny":
                if _remove_untracked_file(project_root, f):
                    fl_removed.append(f)
                    if HAS_LOGGER:
                        log_event(project_root, "REVERT", "bash_module_perimeter_untracked",
                                  f, fl_result["message"])

        all_fl = fl_reverted + fl_removed
        if all_fl:
            fl_list = ", ".join(all_fl)
            print(json.dumps({
                "decision": "warn",
                "reason": (
                    f"CONTROL CODING: Module perimeter violation via Bash. "
                    f"Reverted/removed {len(all_fl)} file(s) outside active module: {fl_list}."
                ),
            }))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        try:
            _root = _find_project_root()
            if HAS_LOGGER:
                log_event(_root, "ERROR", Path(__file__).name,
                          str(exc), "Hook crashed - failing open")
        except Exception:
            pass
        # PostToolUse hooks fail open silently
        sys.exit(0)
