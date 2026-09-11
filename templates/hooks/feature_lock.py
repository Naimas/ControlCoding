#!/usr/bin/env python3
"""feature_lock.py - Module Feature Lock engine for ControlCoding.

Provides mechanical write isolation per module. An agent working on module A
cannot write files belonging to module B. Perimeters are defined in
.feature-lock.json files (versioned, per-module) and the active module is
set at runtime via CC_ACTIVE_MODULE env var or
.controlcoding/active_module.json (legacy .claude fallback).

This file contains ALL feature lock logic:
- Parsing and validation of .feature-lock.json
- Active module resolution (env var + file, conflict handling)
- Path canonicalization and glob matching
- Enforcement decisions (allow/warn/deny)

The 3 hook files (check_boundaries, check_dangerous_commands, check_bash_writes)
import this engine and delegate decisions to it. They never contain feature lock
logic themselves.

Only Python stdlib. No pip dependencies.
"""

import fnmatch
import json
import os
import re
import sys
import threading
from pathlib import Path

CONTROL_PLANE_DIR = ".controlcoding"
LEGACY_CONTROL_PLANE_DIR = ".claude"


# ---------------------------------------------------------------------------
# Path canonicalization - ONE implementation, used everywhere
# ---------------------------------------------------------------------------

def canonical_path(file_path, project_root):
    """Resolve to canonical repo-relative path with forward slashes.

    Handles symlinks, .., absolute/relative, mixed separators.
    Returns None if path resolves outside the repo (different drive on Windows).
    """
    root_str = str(project_root)
    if os.path.isabs(file_path):
        abs_path = os.path.realpath(file_path)
    else:
        abs_path = os.path.realpath(os.path.join(root_str, file_path))
    try:
        rel = os.path.relpath(abs_path, root_str)
    except ValueError:
        # Different drive on Windows
        return None
    result = rel.replace("\\", "/")
    # Reject paths that escape the repo
    if result.startswith("../") or result == "..":
        return None
    return result


# ---------------------------------------------------------------------------
# .feature-lock.json parser and validator
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {"version", "module", "owns"}
_GLOB_CHARS = {"*", "?", "["}


def parse_lock_file(lock_path):
    """Parse and validate a .feature-lock.json file.

    Returns (data_dict, None) on success, (None, error_string) on failure.
    """
    try:
        raw = Path(lock_path).read_text(encoding="utf-8")
    except OSError as e:
        return None, f"Cannot read {lock_path}: {e}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"Malformed JSON in {lock_path}: {e}"

    if not isinstance(data, dict):
        return None, f"Expected JSON object in {lock_path}, got {type(data).__name__}"

    # Required fields
    for field in _REQUIRED_FIELDS:
        if field not in data:
            return None, f"Missing required field '{field}' in {lock_path}"

    # version must be integer 1
    if not isinstance(data["version"], int) or data["version"] != 1:
        return None, f"Unsupported version in {lock_path}: expected integer 1, got {data['version']!r}"

    # module must be non-empty string
    if not isinstance(data["module"], str) or not data["module"].strip():
        return None, f"Field 'module' must be a non-empty string in {lock_path}"

    # owns must be a list (can be empty)
    if not isinstance(data["owns"], list):
        return None, f"Field 'owns' must be a list in {lock_path}"
    for item in data["owns"]:
        if not isinstance(item, str):
            return None, f"Each 'owns' entry must be a string in {lock_path}"

    # shared_write: optional, list of exact paths (no globs)
    shared_write = data.get("shared_write", [])
    if not isinstance(shared_write, list):
        return None, f"Field 'shared_write' must be a list in {lock_path}"
    for item in shared_write:
        if not isinstance(item, str):
            return None, f"Each 'shared_write' entry must be a string in {lock_path}"
        if any(c in item for c in _GLOB_CHARS):
            return None, (
                f"Glob pattern found in shared_write: '{item}' in {lock_path}. "
                "shared_write accepts only exact file paths, never globs."
            )

    # may_read: optional, declarative, not validated strictly
    may_read = data.get("may_read", [])
    if not isinstance(may_read, list):
        return None, f"Field 'may_read' must be a list in {lock_path}"

    # Normalize: ensure defaults
    data.setdefault("shared_write", [])
    data.setdefault("may_read", [])

    return data, None


# ---------------------------------------------------------------------------
# .feature-lock.json discovery (scan repo for all lock files)
# ---------------------------------------------------------------------------

def find_all_lock_files(project_root):
    """Walk the repo and find all .feature-lock.json files.

    Returns list of absolute paths. Skips .git, node_modules, __pycache__.
    """
    skip_dirs = {".git", CONTROL_PLANE_DIR, LEGACY_CONTROL_PLANE_DIR, "node_modules", "__pycache__", ".venv", "venv"}
    result = []
    root_str = str(project_root)
    try:
        for dirpath, dirnames, filenames in os.walk(root_str):
            # Prune skipped directories in-place
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            if ".feature-lock.json" in filenames:
                result.append(os.path.join(dirpath, ".feature-lock.json"))
    except OSError:
        pass  # Permission denied or inaccessible - return partial results
    return result


def check_duplicate_modules(project_root):
    """Scan repo for duplicate module names across .feature-lock.json files.

    Returns (None, None) if no duplicates found.
    Returns (module_name, [path1, path2, ...]) for the first duplicate found.
    """
    lock_files = find_all_lock_files(project_root)
    seen = {}  # module_name -> lock_file_path
    for lf in lock_files:
        data, err = parse_lock_file(lf)
        if err:
            continue  # Skip unparseable files
        module_name = data["module"]
        if module_name in seen:
            return module_name, [seen[module_name], lf]
        seen[module_name] = lf
    return None, None


# ---------------------------------------------------------------------------
# Active module resolver
# ---------------------------------------------------------------------------

_VALID_MODES = {"enforce", "warn", "audit"}

# Session-level cache for duplicate check (avoids re-scanning per call)
_cache_lock = threading.Lock()
_duplicate_check_done = False
_duplicate_error = None

# Module name format: alphanumeric, hyphens, underscores, dots
_MODULE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


def _reset_resolver_cache():
    """Reset the session-level duplicate check cache. For testing only."""
    global _duplicate_check_done, _duplicate_error
    with _cache_lock:
        _duplicate_check_done = False
        _duplicate_error = None


def _active_module_path(project_root):
    """Return canonical active_module path with legacy fallback."""
    root = Path(project_root)
    canonical = root / CONTROL_PLANE_DIR / "active_module.json"
    legacy = root / LEGACY_CONTROL_PLANE_DIR / "active_module.json"
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def _read_active_module_file(project_root):
    """Read .controlcoding/active_module.json with legacy .claude fallback."""
    am_path = _active_module_path(project_root)
    if not am_path.exists():
        return None, None  # Not present, not an error

    try:
        raw = am_path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"Cannot read active_module.json: {e}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"Malformed JSON in active_module.json: {e}"

    if not isinstance(data, dict):
        return None, "active_module.json must be a JSON object"

    if "module" not in data or not isinstance(data["module"], str) or not data["module"].strip():
        return None, "active_module.json: 'module' must be a non-empty string"

    if "mode" not in data or data["mode"] not in _VALID_MODES:
        return None, (
            f"active_module.json: 'mode' must be one of {sorted(_VALID_MODES)}, "
            f"got {data.get('mode')!r}"
        )

    return data, None


def resolve_active_module(project_root):
    """Resolve which module is active and in which mode.

    Returns a dict:
    {
        "module": str or None,
        "mode": "enforce" | "warn" | "audit" | None,
        "source": "env" | "file" | "conflict" | "disabled",
        "error": str or None
    }

    When module is None, feature lock is disabled (no isolation).
    When error is set, the caller should treat it as a blocking error in enforce mode.
    """
    global _duplicate_check_done, _duplicate_error

    env_raw = os.environ.get("CC_ACTIVE_MODULE", "").strip()
    if env_raw and not _MODULE_NAME_RE.match(env_raw):
        return {
            "module": None,
            "mode": None,
            "source": "env",
            "error": (
                f"CC_ACTIVE_MODULE value '{env_raw}' contains invalid characters. "
                "Module names must be alphanumeric with hyphens, underscores, or dots."
            ),
        }
    env_module = env_raw or None
    file_data, file_err = _read_active_module_file(project_root)

    # If active_module.json is malformed, report error
    if file_err:
        return {
            "module": None,
            "mode": None,
            "source": "disabled",
            "error": file_err,
        }

    # CASE 1: Neither source exists -> disabled
    if env_module is None and file_data is None:
        return {
            "module": None,
            "mode": None,
            "source": "disabled",
            "error": None,
        }

    # Determine mode (from file if available, default enforce for env-only)
    mode = file_data["mode"] if file_data else "enforce"

    # CASE: Only env var
    if env_module is not None and file_data is None:
        source = "env"
        module = env_module
    # CASE: Only file
    elif env_module is None and file_data is not None:
        source = "file"
        module = file_data["module"]
    # CASE: Both exist
    else:
        file_module = file_data["module"]
        if env_module == file_module:
            # Agreement
            source = "file"
            module = file_module
        else:
            # CASE 2: Conflict
            if mode == "enforce":
                return {
                    "module": None,
                    "mode": "enforce",
                    "source": "conflict",
                    "error": (
                        f"Conflicting module sources: CC_ACTIVE_MODULE='{env_module}' "
                        f"vs active_module.json='{file_module}'. "
                        "Resolve before proceeding."
                    ),
                }
            else:
                # warn/audit: use env var
                source = "conflict"
                module = env_module

    # CASE 3: Duplicate module name check (once per session, thread-safe)
    with _cache_lock:
        if not _duplicate_check_done:
            _duplicate_check_done = True
            dup_name, dup_paths = check_duplicate_modules(project_root)
            if dup_name:
                _duplicate_error = (
                    f"Duplicate module name '{dup_name}' found in: "
                    + ", ".join(str(p) for p in dup_paths)
                )

    if _duplicate_error:
        return {
            "module": None,
            "mode": mode,
            "source": "conflict",
            "error": _duplicate_error,
        }

    return {
        "module": module,
        "mode": mode,
        "source": source,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Lock file loader (find the lock file for a given module)
# ---------------------------------------------------------------------------

def find_lock_for_module(project_root, module_name):
    """Find the .feature-lock.json that declares the given module name.

    Returns (parsed_data, lock_path) or (None, None) if not found.
    """
    lock_files = find_all_lock_files(project_root)
    for lf in lock_files:
        data, err = parse_lock_file(lf)
        if err:
            continue
        if data["module"] == module_name:
            return data, lf
    return None, None


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------

def _match_owns(target_path, owns_patterns):
    """Check if target_path matches any owns glob pattern.

    Uses fnmatch for glob matching. Patterns and paths are compared
    with forward slashes. The match is segment-aware via fnmatch.
    """
    for pattern in owns_patterns:
        pat = pattern.replace("\\", "/")
        if fnmatch.fnmatch(target_path, pat):
            return True
    return False


def _match_shared_write(target_path, shared_write_paths):
    """Check if target_path exactly matches any shared_write entry.

    Exact match only - no globs, no prefix matching.
    Both sides are normalized to forward slashes.
    """
    for sw in shared_write_paths:
        sw_norm = sw.replace("\\", "/")
        if target_path == sw_norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Enforcement engine
# ---------------------------------------------------------------------------

def _make_result(decision, reason_code, message, active_module, source):
    """Build the standard output dict."""
    return {
        "decision": decision,
        "reason_code": reason_code,
        "message": message,
        "active_module": active_module,
        "source": source,
    }


def check_module_perimeter(file_path, project_root,
                           self_protected_paths=None,
                           global_deny_paths=None):
    """Main enforcement entry point. Check if writing to file_path is allowed.

    Args:
        file_path: Path to the file being written (absolute or relative).
        project_root: Project root directory.
        self_protected_paths: Optional list of paths that are self-protected.
            If provided and the target matches, returns deny with
            self_protection_override reason_code.
        global_deny_paths: Optional list of global deny zone paths.
            If provided and the target matches, returns deny with
            global_deny_override reason_code.

    Returns the standard output dict:
    {
        "decision": "allow" | "warn" | "deny",
        "reason_code": str,
        "message": str,
        "active_module": str or None,
        "source": "env" | "file" | "conflict" | "disabled"
    }
    """
    # Canonicalize the target path
    target = canonical_path(file_path, project_root)
    if target is None:
        return _make_result(
            "deny", "foreign_path",
            f"Path '{file_path}' resolves outside the repository.",
            None, "disabled",
        )

    # Resolve active module
    resolved = resolve_active_module(project_root)

    # No module active -> feature lock disabled, everything passes
    if resolved["module"] is None and resolved["error"] is None:
        return _make_result(
            "allow", "disabled",
            "No active module - feature lock disabled.",
            None, "disabled",
        )

    # Error in resolution (malformed file, conflict in enforce mode, duplicates)
    if resolved["error"]:
        mode = resolved["mode"] or "enforce"
        if mode == "enforce":
            return _make_result(
                "deny", "conflict",
                f"Feature lock error: {resolved['error']}",
                None, resolved["source"],
            )
        elif mode == "warn":
            return _make_result(
                "warn", "conflict",
                f"Feature lock warning: {resolved['error']}",
                None, resolved["source"],
            )
        else:  # audit
            return _make_result(
                "allow", "conflict",
                f"Feature lock audit: {resolved['error']}",
                None, resolved["source"],
            )

    module_name = resolved["module"]
    mode = resolved["mode"]
    source = resolved["source"]

    # Self-protection override: these paths are ALWAYS denied regardless of owns
    if self_protected_paths:
        for sp in self_protected_paths:
            sp_norm = sp.replace("\\", "/")
            # Filename match or path segment match
            target_parts = target.split("/")
            sp_parts = sp_norm.split("/")
            if len(sp_parts) == 1:
                if target_parts[-1] == sp_parts[0]:
                    return _make_result(
                        "deny", "self_protection_override",
                        f"Path '{target}' is self-protected. Module ownership does not override self-protection.",
                        module_name, source,
                    )
            else:
                for i in range(len(target_parts) - len(sp_parts) + 1):
                    if target_parts[i:i + len(sp_parts)] == sp_parts:
                        return _make_result(
                            "deny", "self_protection_override",
                            f"Path '{target}' is self-protected. Module ownership does not override self-protection.",
                            module_name, source,
                        )

    # Global deny override: these paths are ALWAYS denied regardless of owns
    if global_deny_paths:
        for gd in global_deny_paths:
            gd_clean = gd.rstrip("/").replace("\\", "/")
            gd_parts = gd_clean.split("/")
            target_parts = target.split("/")
            matched = False
            if len(gd_parts) == 1:
                matched = gd_parts[0] in target_parts
            else:
                for i in range(len(target_parts) - len(gd_parts) + 1):
                    if target_parts[i:i + len(gd_parts)] == gd_parts:
                        matched = True
                        break
            if matched:
                return _make_result(
                    "deny", "global_deny_override",
                    f"Path '{target}' is in a global deny zone. Module ownership does not override global deny.",
                    module_name, source,
                )

    # Find the lock file for this module
    lock_data, lock_path = find_lock_for_module(project_root, module_name)

    if lock_data is None:
        # Module declared but no lock file found
        msg = f"No .feature-lock.json found for module '{module_name}'."
        if mode == "enforce":
            return _make_result("deny", "foreign_path", msg, module_name, source)
        elif mode == "warn":
            return _make_result("warn", "foreign_path", msg, module_name, source)
        else:
            return _make_result("allow", "foreign_path", msg, module_name, source)

    # Check owns
    if _match_owns(target, lock_data.get("owns", [])):
        return _make_result(
            "allow", "owns_match",
            f"Path '{target}' is owned by module '{module_name}'.",
            module_name, source,
        )

    # Check shared_write
    if _match_shared_write(target, lock_data.get("shared_write", [])):
        return _make_result(
            "allow", "shared_write_match",
            f"Path '{target}' is in shared_write for module '{module_name}'.",
            module_name, source,
        )

    # Everything else: outside perimeter
    msg = (
        f"Path '{target}' is outside the perimeter of module '{module_name}'. "
        f"Owned paths: {lock_data.get('owns', [])}. "
        f"Shared write: {lock_data.get('shared_write', [])}."
    )
    if mode == "enforce":
        return _make_result("deny", "foreign_path", msg, module_name, source)
    elif mode == "warn":
        return _make_result("warn", "foreign_path", msg, module_name, source)
    else:  # audit
        return _make_result("allow", "foreign_path", msg, module_name, source)
