#!/usr/bin/env python3
"""check_dangerous_commands.py - ControlCoding dangerous command blocker.

PreToolUse hook for Claude Code: blocks destructive shell commands that
could cause irreversible damage (data loss, force pushes, etc.).

Exit 0 = allowed.
Exit 2 = blocked (JSON on stdout for logs/tests, reason on stderr for Claude).

Protocol (Claude Code hooks):
- Receives JSON on stdin with tool_name and tool_input
- For Bash, tool_input.command contains the shell command

Setup: configure in .controlcoding/settings.json. Claude Code reads the generated
.claude/settings.local.json adapter.

Customization: add dangerous_patterns and write_patterns to .controlcoding/cc_config.json,
or edit the inline fallbacks below.
"""

import json
import os
import posixpath
import re
import sys
from datetime import datetime, timezone
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
        print("[check_dangerous_commands] feature_lock.py not found - module perimeter checks disabled",
              file=sys.stderr)

# --- INLINE FALLBACKS (used when cc_config.json has no corresponding section) ---

# Each entry: (regex_pattern, description)
# The pattern is matched against the full command string.

DANGEROUS_PATTERNS = [
    (
        r"\brm\b(?=[^;&|]*(?:\s|^)(?:--recursive\b|-[A-Za-z]*r[A-Za-z]*))"
        r"(?=[^;&|]*(?:\s|^)(?:--force\b|-[A-Za-z]*f[A-Za-z]*))"
        r"[^;&|]*\s+/\S*",
        "rm recursive force with absolute path - catastrophic deletion risk",
    ),
    (
        r"\brm\b(?=[^;&|]*(?:\s|^)(?:--recursive\b|-[A-Za-z]*r[A-Za-z]*))"
        r"(?=[^;&|]*(?:\s|^)(?:--force\b|-[A-Za-z]*f[A-Za-z]*))"
        r"[^;&|]*\s+\.\/?(?:\s|$|[;&|])",
        "rm recursive force on current directory - data loss risk",
    ),
    (r"git\s+push\s+.*--force(?!-with-lease)", "git push --force - can overwrite remote history"),
    (r"git\s+reset\s+--hard", "git reset --hard - discards all uncommitted changes"),
    (r"\bgit\s+clean\b(?=.*(?:^|\s)(?:-[A-Za-z]*f[A-Za-z]*|--force)\b).*", "git clean -f - deletes untracked files permanently"),
    (r"git\s+checkout\s+\.\s*$", "git checkout . - discards all local modifications"),
    (r"git\s+branch\s+-D", "git branch -D - force-deletes branch without merge check"),
    (r"drop\s+table", "DROP TABLE - irreversible database operation"),
    (r"drop\s+database", "DROP DATABASE - irreversible database operation"),
    (r"truncate\s+table", "TRUNCATE TABLE - irreversible data deletion"),
    # Add your own patterns below:
]

# --- BASH WRITE DETECTION ---
# Patterns that indicate file-writing operations via Bash.
# Used to detect attempts to bypass Edit/Write hooks.
# Only catches obvious cases; check_bash_writes.py (PostToolUse) is the reliable backstop.
WRITE_TARGET = r"((?:\"[^\"]+\"|'[^']+'|[^\s;|&]+))"
WRITE_PATTERNS = [
    (rf"\bcat\s*>\s*{WRITE_TARGET}", "cat > file"),
    (rf"\btee\s+(?:-a\s+)?{WRITE_TARGET}", "tee to file"),
    (rf"\becho\b.*?>\s*{WRITE_TARGET}", "echo redirect to file"),
    (rf"\bprintf\b.*?>\s*{WRITE_TARGET}", "printf redirect to file"),
    (rf"\bcp\s+(?:-\w+\s+)*\S+\s+{WRITE_TARGET}", "cp to target"),
    (rf"\bmv\s+(?:-\w+\s+)*\S+\s+{WRITE_TARGET}", "mv to target"),
    (rf"\bsed\s+.*-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+{WRITE_TARGET}", "sed in-place edit"),
]

MANDATORY_DENY_ZONES = [
    "templates/hooks/",
    "dev/methodology_full.md",
]

# --- END INLINE FALLBACKS ---


def _emit_block(reason: str) -> None:
    """Block in a way compatible with Claude exit-code hooks."""
    print(json.dumps({"decision": "block", "reason": reason}))
    print(reason, file=sys.stderr)
    sys.exit(2)


def _find_project_root():
    """Walk up from this file's directory looking for project root markers.
    Prefers .git/ (authoritative), then the active control plane."""
    return find_project_root(__file__)


def _check_lift(project_root):
    """Check if this hook is temporarily lifted via the active hooks_lifted.json."""
    lift_path = control_plane_path(project_root, "hooks_lifted.json")
    if not lift_path.exists():
        return False
    try:
        data = json.loads(lift_path.read_text(encoding="utf-8"))
        lifted = data.get("lifted", [])
        if Path(__file__).name not in lifted:
            return False
        lifted_at = data.get("lifted_at", "")
        if lifted_at:
            lift_time = datetime.fromisoformat(lifted_at)
            if lift_time.tzinfo is None:
                lift_time = lift_time.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - lift_time).total_seconds()
            max_hours = float(os.environ.get("HOOK_LIFT_HOURS", "1"))
            if max_hours > 0 and elapsed > max_hours * 3600:
                return False
        return True
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _load_config_patterns(project_root, section):
    """Load pattern list from cc_config.json section.

    Returns list of (pattern, description) tuples, or None if section missing.
    Accepts both dict format ({"pattern": ..., "description": ...}) and
    list-of-two-strings format (["pattern", "description"]).
    """
    config_path = control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        raw = data.get(section)
        if raw is None:
            return None
        result = []
        for item in raw:
            if isinstance(item, dict) and "pattern" in item:
                result.append((item["pattern"], item.get("description", "")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                result.append((item[0], item[1]))
        return result if result else None
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None


def _normalized_path_text(path_value) -> str:
    """Return a stable forward-slash path string after dot-segment cleanup."""
    raw = str(path_value).strip().replace("\\", "/")
    normalized = posixpath.normpath(raw)
    if normalized == ".":
        return ""
    return normalized


def _filesystem_path(path_value) -> Path:
    """Convert slash or backslash text to a Path for the current filesystem."""
    raw = str(path_value).strip().replace("\\", os.sep).replace("/", os.sep)
    return Path(os.path.normpath(raw))


def _split_path_parts(path_value) -> list[str]:
    return [
        part for part in _normalized_path_text(path_value).split("/")
        if part and part != "."
    ]


def _target_abs_path(file_path: str, project_root: Path) -> Path:
    normalized = _filesystem_path(file_path)
    if normalized.is_absolute():
        return normalized
    return project_root / normalized


def _candidate_path_parts(file_path: str, project_root: Path) -> list[list[str]]:
    """Return path segment candidates for requested and resolved target paths."""
    candidates = []
    seen = set()

    def add(path_value) -> None:
        normalized = _normalized_path_text(path_value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        parts = _split_path_parts(normalized)
        if parts:
            candidates.append(parts)

    add(file_path)
    abs_path = _target_abs_path(file_path, project_root)
    add(abs_path)

    try:
        resolved_abs = abs_path.resolve()
    except OSError:
        resolved_abs = abs_path.absolute()
    add(resolved_abs)

    try:
        project_root_resolved = project_root.resolve()
    except OSError:
        project_root_resolved = project_root.absolute()

    for path_value in (abs_path, resolved_abs):
        try:
            add(path_value.relative_to(project_root_resolved))
            continue
        except ValueError:
            pass
        try:
            add(path_value.relative_to(project_root))
        except ValueError:
            pass

    return candidates


def _parts_match_zone(parts: list[str], segment: str) -> bool:
    """Check if parts contain a protected zone as whole path segments.

    Handles both single-segment zones ("core/") and multi-segment zones ("src/core/").
    Avoids false positives: zone "core" must NOT match "coredump/file.py".
    """
    zone_clean = segment.rstrip("/")
    zone_parts = [
        part for part in zone_clean.replace("\\", "/").split("/")
        if part and part != "."
    ]
    if not zone_parts or not parts:
        return False

    if len(zone_parts) == 1:
        if segment.endswith("/"):
            return zone_parts[0] in parts
        return parts[-1] == zone_parts[0] or zone_parts[0] in parts

    for i in range(len(parts) - len(zone_parts) + 1):
        if parts[i:i + len(zone_parts)] == zone_parts:
            return True

    return False


def _path_in_zone(target_path, zone, project_root: Path) -> bool:
    """Check all normalized target candidates against a protected zone."""
    return any(
        _parts_match_zone(parts, zone)
        for parts in _candidate_path_parts(target_path, project_root)
    )


def _load_effective_deny_zones(project_root: Path) -> list[str]:
    """Return mandatory DENY zones merged with config DENY zones."""
    deny_zones = list(MANDATORY_DENY_ZONES)
    config_path = control_plane_path(project_root, "cc_config.json")
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            deny_zones.extend(
                z["path"] for z in normalize_protected_zones(cfg.get("protected_zones", []))
                if z.get("level", "warn") == "deny"
            )
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            pass

    result = []
    seen = set()
    for zone in deny_zones:
        if not isinstance(zone, str):
            continue
        normalized = _normalized_path_text(zone)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(zone)
    return result


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

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)

    project_root = _find_project_root()

    # Check for temporary lift
    if _check_lift(project_root):
        print(f"[LIFTED] {Path(__file__).name} is temporarily disabled")
        sys.exit(0)

    # 1. Check dangerous patterns (cc_config.json -> inline fallback)
    dangerous = _load_config_patterns(project_root, "dangerous_patterns")
    if dangerous is None:
        dangerous = DANGEROUS_PATTERNS

    for pattern, desc in dangerous:
        try:
            if re.search(pattern, command, re.IGNORECASE):
                if HAS_LOGGER:
                    log_event(project_root, "DENY", "dangerous_command", command, desc)

                _emit_block(
                    f"CONTROL CODING: Blocked dangerous command. {desc}. "
                    "If this is intentional, ask the user for explicit approval."
                )
        except re.error:
            # Invalid regex in config - skip this pattern, continue checking others
            if HAS_LOGGER:
                log_event(project_root, "ERROR", "dangerous_command",
                          pattern, f"Invalid regex pattern, skipped: {desc}")
            continue

    # 2. Check for Bash writes to DENY-protected zones
    deny_zones = _load_effective_deny_zones(project_root)

    if deny_zones:
        # Load write patterns (cc_config.json -> inline fallback)
        write_pats = _load_config_patterns(project_root, "write_patterns")
        if write_pats is None:
            write_pats = WRITE_PATTERNS

        for wp_pattern, wp_desc in write_pats:
            try:
                m = re.search(wp_pattern, command)
            except re.error:
                # Invalid regex in write pattern - skip
                continue
            if m:
                try:
                    target = m.group(1).strip().strip("'").strip('"')
                except IndexError:
                    # Pattern has no capture group - skip
                    continue
                for zone in deny_zones:
                    if _path_in_zone(target, zone, project_root):
                        if HAS_LOGGER:
                            log_event(project_root, "DENY", "bash_write_attempt",
                                      command, f"Write to protected zone {zone}")
                        _emit_block(
                            f"CONTROL CODING: Bash write to DENY-protected zone '{zone}' detected. "
                            "Boundary protection applies to all tools, not just Edit/Write. "
                            "Use Edit/Write with a proper lift request if modification is necessary."
                        )

    # 3. Check for Bash writes outside module perimeter (feature lock)
    if HAS_FEATURE_LOCK:
        write_pats_fl = _load_config_patterns(project_root, "write_patterns")
        if write_pats_fl is None:
            write_pats_fl = WRITE_PATTERNS
        for wp_pattern, wp_desc in write_pats_fl:
            try:
                m = re.search(wp_pattern, command)
            except re.error:
                continue
            if m:
                try:
                    target = m.group(1).strip().strip("'").strip('"')
                except IndexError:
                    continue
                fl_result = check_module_perimeter(target, project_root)
                if fl_result["decision"] == "deny":
                    if HAS_LOGGER:
                        log_event(project_root, "DENY", "bash_module_perimeter",
                                  command, fl_result["message"])
                    _emit_block(f"CONTROL CODING: {fl_result['message']}")

    # Command is safe - allow
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
        print(f"[HOOK ERROR] {Path(__file__).name} crashed: {exc} - failing open (allow)")
        sys.exit(0)
