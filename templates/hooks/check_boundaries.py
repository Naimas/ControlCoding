#!/usr/bin/env python3
"""check_boundaries.py -ControlCoding boundary enforcement hook.

PreToolUse hook for Claude Code: checks if an Edit/Write operation
targets a protected zone of the project. Emits WARN or DENY depending
on zone configuration.

Exit 0 = allowed (with optional warning JSON on stdout).
Exit 2 = blocked (JSON on stdout for logs/tests, reason on stderr for Claude).

Protocol (Claude Code hooks):
- Receives JSON on stdin with tool_name and tool_input
- For Edit/Write, tool_input.file_path contains the target path

Setup: configure in .controlcoding/settings.json. Claude Code reads the generated
.claude/settings.local.json adapter.

Customization: edit PROTECTED_ZONES below or use .controlcoding/cc_config.json.
Mandatory DENY zones are always enforced before custom zones.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- HOOK SELF-PROTECTION (do not remove) ---
# The enforcement mechanism protects itself. Without this, an AI can
# modify the hook file to disable DENY rules, making all boundary
# protection advisory rather than mechanical.
# This check is hardcoded and independent of PROTECTED_ZONES.
HOOK_SELF_PROTECTION = True
PROTECTED_HOOK_PATTERNS = [
    "check_boundaries.py",
    "check_dangerous_commands.py",
    "check_bash_writes.py",
    "hook_logger.py",
    "request_lift.py",
    ".controlcoding/settings.json",
    ".controlcoding/cc_config.json",
    ".controlcoding/hooks_lifted.json",
    ".controlcoding/lift_request.json",
    ".claude/settings.json",
    ".claude/cc_config.json",
    ".claude/hooks_lifted.json",
    ".claude/lift_request.json",
    ".claude/settings.local.json",
    ".feature-lock.json",
    "active_module.json",
]

# --- CONFIGURE THESE FOR YOUR PROJECT ---

# Each entry: (path_segment, description, action)
# action: "warn" = allow but notify (exit 0), "deny" = block (exit 2)
#
# "warn" is appropriate for single-developer projects where you want
# awareness but not hard blocks.
# "deny" is appropriate for team projects or truly critical zones.

PROTECTED_ZONES = [
    # Examples - replace with your actual protected zones:
    #
    # Zone-level protection (directories):
    # ("src/core/", "Core data models - stable zone", "deny"),
    # ("src/auth/", "Authentication module - stable zone", "deny"),
    # ("src/shared/", "Shared utilities - modify with care", "warn"),
    #
    # File-level protection (God Objects, constrained files):
    # ("main.cpp", "God Object - use AppState for new state", "warn"),
    # ("app_controller.py", "Minimize changes, route logic elsewhere", "warn"),
]

# Mandatory project safety contract. These zones cannot be disabled by an
# empty or partial cc_config.json; custom config zones are merged after them.
MANDATORY_DENY_ZONES = [
    (
        "templates/hooks/",
        "Hook infrastructure is a mandatory protected zone",
        "deny",
    ),
    (
        "dev/methodology_full.md",
        "Methodology document is a mandatory protected file",
        "deny",
    ),
]

# --- END CONFIGURATION ---


def _emit_block(reason: str) -> None:
    """Block in a way compatible with Claude exit-code hooks.

    Claude Code uses stderr as the visible feedback channel when a hook exits 2.
    Keep stdout JSON for logs and tests, but mirror the human-readable reason to
    stderr so the agent receives useful feedback.
    """
    print(json.dumps({"decision": "block", "reason": reason}))
    print(reason, file=sys.stderr)
    sys.exit(2)


def _find_project_root():
    """Walk up from this file's directory looking for project root markers.
    Prefers .git/ (authoritative), then the active control plane."""
    return find_project_root(__file__)


def _normalized_path_text(path_value) -> str:
    """Return a stable forward-slash path string after dot-segment cleanup."""
    return os.path.normpath(str(path_value)).replace("\\", "/")


def _split_path_parts(path_value) -> list[str]:
    return [
        part for part in _normalized_path_text(path_value).split("/")
        if part and part != "."
    ]


def _target_abs_path(file_path: str, project_root: Path) -> Path:
    normalized = Path(os.path.normpath(file_path))
    if normalized.is_absolute():
        return normalized
    return project_root / normalized


def _candidate_path_parts(file_path: str, project_root: Path) -> list[list[str]]:
    """Return path segment candidates for requested and resolved target paths."""
    candidates = []
    seen = set()

    def add(path_value) -> None:
        normalized = _normalized_path_text(path_value)
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append(_split_path_parts(normalized))

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
        except ValueError:
            try:
                add(path_value.relative_to(project_root))
            except ValueError:
                pass

    return candidates


def _parts_match_pattern(parts: list[str], pattern: str) -> bool:
    pattern_parts = [
        part for part in pattern.replace("\\", "/").split("/")
        if part and part != "."
    ]
    if not pattern_parts or not parts:
        return False
    if len(pattern_parts) == 1:
        return parts[-1] == pattern_parts[0]
    for i in range(len(parts) - len(pattern_parts) + 1):
        if parts[i:i + len(pattern_parts)] == pattern_parts:
            return True
    return False


def _parts_match_zone(parts: list[str], segment: str) -> bool:
    seg_clean = segment.rstrip("/")
    seg_parts = [
        part for part in seg_clean.replace("\\", "/").split("/")
        if part and part != "."
    ]
    if not seg_parts or not parts:
        return False
    if len(seg_parts) == 1:
        if segment.endswith("/"):
            return seg_parts[0] in parts
        return parts[-1] == seg_parts[0] or seg_parts[0] in parts
    for i in range(len(parts) - len(seg_parts) + 1):
        if parts[i:i + len(seg_parts)] == seg_parts:
            return True
    return False


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


def _check_zone_lift(project_root, zone_path):
    """Check if a specific zone has an APPROVED scoped lift.

    Returns True only if:
    - lift_request.json exists
    - status is "APPROVED" (not "PENDING" - pending needs human approval)
    - the zone matches
    - the lift has not expired

    Self-protected files never reach this function (blocked earlier).
    After allowing one operation, the lift is consumed (single-use).
    """
    lift_path = control_plane_path(project_root, "lift_request.json")
    if not lift_path.exists():
        return False
    try:
        data = json.loads(lift_path.read_text(encoding="utf-8"))
        # CRITICAL: only APPROVED lifts are active.
        # PENDING lifts need human approval via request_lift.py --approve.
        if data.get("status") != "APPROVED":
            return False

        zones = data.get("zones", [])
        if not zones:
            return False
        # Normalize and check if the blocked zone is in the lift list
        zone_norm = zone_path.rstrip("/")
        if zone_norm not in [z.rstrip("/") for z in zones]:
            return False
        # Check declarative expiry
        expires_at = data.get("expires_at", "")
        if expires_at:
            exp_time = datetime.fromisoformat(expires_at)
            if exp_time.tzinfo is None:
                exp_time = exp_time.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp_time:
                # Expired: clean up
                try:
                    lift_path.unlink()
                except OSError:
                    pass
                return False

        # Single-use per zone: remove consumed zone, mark CONSUMED when all gone
        remaining = [z for z in zones if z.rstrip("/") != zone_norm]
        if remaining:
            data["zones"] = remaining
        else:
            data["status"] = "CONSUMED"
            data["consumed_at"] = datetime.now(timezone.utc).isoformat()
        try:
            lift_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


        return True
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _load_config_zones(project_root):
    """Load protected zones from the active cc_config.json if it exists.
    Returns list of (path, description, level) tuples, or None if no config."""
    config_path = control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        zones = normalize_protected_zones(data.get("protected_zones", []))
        return [(z["path"], z.get("description", ""), z.get("level", "warn")) for z in zones]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _load_effective_zones(project_root):
    """Return mandatory zones merged with config or inline fallback zones."""
    zones = _load_config_zones(project_root)
    if zones is None:
        zones = PROTECTED_ZONES
    return list(MANDATORY_DENY_ZONES) + list(zones)


def _matching_zone(zones, path_parts_candidates):
    for segment, desc, action in zones:
        matched = any(
            _parts_match_zone(parts, segment)
            for parts in path_parts_candidates
        )
        if matched:
            return segment, desc, action
    return None


def _emit_zone_decision(project_root, norm, segment, desc, action) -> None:
    action = (action or "warn").lower()
    if HAS_LOGGER:
        decision_label = "DENY" if action == "deny" else "WARN"
        log_event(project_root, decision_label, "boundary", norm, desc)

    if action == "deny":
        # Check zone-scoped lift (self-protected files never reach here)
        if _check_zone_lift(project_root, segment):
            if HAS_LOGGER:
                log_event(project_root, "LIFT", "boundary", norm,
                          f"Zone lift active for {segment}")
            print(json.dumps({
                "decision": "warn",
                "reason": (
                    f"CONTROL CODING: {desc}. "
                    f"Zone '{segment}' has an active scoped lift. "
                    "Proceeding with WARNING. Review changes at session end."
                ),
            }))
            sys.exit(0)

        _emit_block(
            f"CONTROL CODING: {desc}. "
            "This zone is DENY-protected. Modification blocked. "
            "If this modification is genuinely necessary (bug fix, design flaw), "
            "explain to the user WHAT you need to change and WHY, then run: "
            "python hooks/request_lift.py --file <path> --reason \"<why>\" "
            "-- The user must then approve with: "
            "python hooks/request_lift.py --approve "
            "-- Do NOT run --approve yourself. Do NOT bypass via Bash. "
            "The lift is single-use: protection restores after one operation."
        )

    print(
        json.dumps(
            {
                "decision": "warn",
                "reason": (
                    f"CONTROL CODING: {desc}. "
                    "Verify that this modification is intentional and necessary."
                ),
            }
        )
    )
    sys.exit(0)


# Optional: import hook_logger if available
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_logger import log_event

    HAS_LOGGER = True
except ImportError:
    HAS_LOGGER = False

try:
    from hook_utils import (
        control_plane_path,
        find_project_root,
        normalize_protected_zones,
    )
except ImportError:
    from templates.hooks.hook_utils import (
        control_plane_path,
        find_project_root,
        normalize_protected_zones,
    )

try:
    from feature_lock import check_module_perimeter
    HAS_FEATURE_LOCK = True
except ImportError:
    try:
        from templates.hooks.feature_lock import check_module_perimeter
        HAS_FEATURE_LOCK = True
    except ImportError:
        HAS_FEATURE_LOCK = False
        print("[check_boundaries] feature_lock.py not found - module perimeter checks disabled",
              file=sys.stderr)


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if not isinstance(input_data, dict):
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    project_root = _find_project_root()
    norm = _normalized_path_text(file_path)
    path_parts_candidates = _candidate_path_parts(file_path, project_root)

    # 1. Self-protection: block modifications to hook files and settings
    # even when a legacy global hook lift is active.
    if HOOK_SELF_PROTECTION:
        for pattern in PROTECTED_HOOK_PATTERNS:
            if any(_parts_match_pattern(parts, pattern) for parts in path_parts_candidates):
                if HAS_LOGGER:
                    log_event(project_root, "DENY", "self-protection", norm,
                              "Hook infrastructure is self-protected")
                _emit_block(
                    "CONTROL CODING: Hook infrastructure is self-protected. "
                    "Boundary enforcement files cannot be modified by AI. "
                    "Edit these files manually outside of Claude Code if needed."
                )

    # 2. Check for temporary lift after self-protection.
    if _check_lift(project_root):
        print(f"[LIFTED] {Path(__file__).name} is temporarily disabled")
        sys.exit(0)

    zones = _load_effective_zones(project_root)

    # 3. Allow creation of new files except in mandatory DENY zones.
    abs_path = _target_abs_path(file_path, project_root).resolve()
    if not abs_path.exists():
        mandatory_match = _matching_zone(MANDATORY_DENY_ZONES, path_parts_candidates)
        if mandatory_match is not None:
            _emit_zone_decision(project_root, norm, *mandatory_match)
        sys.exit(0)

    # 3b. Module perimeter check (feature lock)
    if HAS_FEATURE_LOCK:
        fl_result = check_module_perimeter(file_path, project_root)
        if fl_result["decision"] == "deny":
            if HAS_LOGGER:
                log_event(project_root, "DENY", "module_perimeter",
                          norm, fl_result["message"])
            _emit_block(f"CONTROL CODING: {fl_result['message']}")
        elif fl_result["decision"] == "warn":
            if HAS_LOGGER:
                log_event(project_root, "WARN", "module_perimeter",
                          norm, fl_result["message"])
            print(fl_result["message"], file=sys.stderr)
            # Continue to zone checks

    # 4. Check mandatory zones first, then custom config or inline fallback.
    for segment, desc, action in zones:
        # Use segment-aware matching to avoid false positives.
        # "core/" must match "/project/core/file.py" but NOT "/project/hardcore/file.py"
        # Multi-segment patterns like "src/core/" use consecutive segment matching.
        matched = any(
            _parts_match_zone(parts, segment)
            for parts in path_parts_candidates
        )

        if matched:
            _emit_zone_decision(project_root, norm, segment, desc, action)

    # File outside protected zones -all OK
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
