#!/usr/bin/env python3
"""check_workflow.py - ControlCoding workflow enforcement hook.

PreToolUse hook for Claude Code: checks that workflow prerequisites are met
before allowing certain actions. For example, verifying that a planner was
consulted before writing source code, or that visual checks were performed
after rendering changes.

Exit 0 = allowed (with optional warning JSON on stdout).
Exit 2 = blocked (JSON on stdout for logs/tests, reason on stderr for Claude).

Protocol (Claude Code hooks):
- Receives JSON on stdin with tool_name and tool_input
- For Edit/Write, tool_input.file_path contains the target path

Detection: instead of maintaining separate state, this hook checks for
artifacts produced by other CC tools:
- Consultant: .controlcoding/consult_log.jsonl (legacy .claude fallback)
- Visual check: screenshots/ directory (written by visual_check.py)
- Checkpoints: devlog/ directory (written by mcp_session.py)
- Operations: .controlcoding/ops_log.jsonl (legacy .claude fallback)

If CC tools are not installed (no artifacts exist), the hook warns once
per rule and then allows. It does not block projects without full CC tooling.

Setup: configure in .controlcoding/settings.json (see settings.json.example)

Customization: add workflow_rules to .controlcoding/cc_config.json,
or edit the inline fallbacks below.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import control_plane_path, find_project_root as _find_control_plane_root
except ImportError:
    from templates.hooks.hook_utils import control_plane_path, find_project_root as _find_control_plane_root

# A screenshot counts as "recent" if modified within this many minutes
SCREENSHOT_RECENCY_MINUTES = 30


def _emit_block(reason: str) -> None:
    """Block in a way compatible with Claude exit-code hooks."""
    print(json.dumps({"decision": "block", "reason": reason}))
    print(reason, file=sys.stderr)
    sys.exit(2)


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

# --- INLINE FALLBACKS (used when cc_config.json has no workflow_rules section) ---

# Each rule defines:
#   name:       unique rule identifier
#   trigger:    which tool + path pattern activates this rule
#   check:      what artifact/condition to verify
#   after_count: only enforce after this many matching operations (0 = always)
#   mode:       "warn" (allow + notify) or "deny" (block)
#   message:    human-readable message when prerequisite not met

WORKFLOW_RULES = [
    {
        "name": "plan_before_code",
        "trigger": {"tools": ["Write", "Edit"], "path_patterns": ["src/**"]},
        "check": "planner_consulted",
        "after_count": 0,
        "mode": "warn",
        "message": (
            "No planning phase detected. Consider calling the planner consultant "
            "before writing source code. (consult role=planner)"
        ),
    },
    {
        "name": "visual_check_after_rendering",
        "trigger": {"tools": ["Write", "Edit"], "path_patterns": ["src/renderer/**", "src/shaders/**", "shaders/**"]},
        "check": "visual_checked_recently",
        "after_count": 3,
        "mode": "warn",
        "message": (
            "Multiple renderer/shader writes without visual verification. "
            "Run visual_check.py to verify rendering output."
        ),
    },
    {
        "name": "checkpoint_frequency",
        "trigger": {"tools": ["Write", "Edit"], "path_patterns": ["src/**"]},
        "check": "checkpoint_recent",
        "after_count": 20,
        "mode": "warn",
        "message": (
            "20+ source writes without a checkpoint. "
            "Call checkpoint() to save progress."
        ),
    },
]

# --- END INLINE FALLBACKS ---


def _load_config_rules(project_root):
    """Load workflow rules from cc_config.json.

    Returns list of rule dicts, or None if section missing.
    Each rule must have at minimum: name, trigger, check, mode, message.
    """
    config_path = control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        raw = data.get("workflow_rules")
        if raw is None:
            return None
        if not isinstance(raw, list):
            return None
        # Validate each rule has required fields
        result = []
        for rule in raw:
            if not isinstance(rule, dict):
                continue
            if "name" not in rule or "check" not in rule:
                continue
            # Apply defaults for optional fields
            result.append({
                "name": rule["name"],
                "trigger": rule.get("trigger", {"tools": [], "path_patterns": []}),
                "check": rule["check"],
                "after_count": rule.get("after_count", 0),
                "mode": rule.get("mode", "warn"),
                "message": rule.get("message", "Workflow prerequisite not met."),
            })
        return result if result else None
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None

# State file: tracks counters between hook invocations within a session.
# This avoids re-scanning the full ops_log on every call.
STATE_FILENAME = "workflow_state.json"

# Optional: import hook_logger if available
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_logger import log_event

    HAS_LOGGER = True
except ImportError:
    HAS_LOGGER = False


def find_project_root() -> Path:
    """Find project root by walking up looking for project root markers.
    Prefers .git/ (authoritative), then the active control plane."""
    return _find_control_plane_root(__file__)


def load_state(project_root: Path) -> dict:
    """Load or initialize workflow state."""
    state_path = control_plane_path(project_root, STATE_FILENAME)
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "planner_consulted": False,
        "visual_checked_recently": True,
        "checkpoint_recent": True,
        "renderer_writes": 0,
        "total_src_writes": 0,
        "warned_rules": [],
    }


def save_state(project_root: Path, state: dict) -> None:
    """Persist workflow state."""
    state_path = control_plane_path(project_root, STATE_FILENAME)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def check_planner_consulted(project_root: Path) -> bool:
    """Check if a planner consultation has been logged."""
    log_path = control_plane_path(project_root, "consult_log.jsonl")
    if not log_path.exists():
        return False
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("role") == "planner":
                        return True
                    if entry.get("role") == "architect":
                        return True
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return False


def check_visual_recently(project_root: Path) -> bool:
    """Check if a recent screenshot exists (evidence of visual verification).

    A screenshot is considered recent if its modification time is within
    SCREENSHOT_RECENCY_MINUTES. Old screenshots from previous sessions
    do not satisfy this check.
    """
    screenshots_dir = project_root / "screenshots"
    if not screenshots_dir.exists():
        return False
    cutoff = time.time() - SCREENSHOT_RECENCY_MINUTES * 60
    try:
        for f in screenshots_dir.iterdir():
            if f.is_file() and f.stat().st_mtime >= cutoff:
                return True
    except OSError:
        pass
    return False


def check_checkpoint_recent(project_root: Path) -> bool:
    """Check if devlog entries exist (evidence of checkpointing)."""
    devlog_dir = project_root / "devlog"
    if not devlog_dir.exists():
        return False
    try:
        files = list(devlog_dir.iterdir())
        return len(files) > 0
    except OSError:
        return False


CHECK_FUNCTIONS = {
    "planner_consulted": check_planner_consulted,
    "visual_checked_recently": check_visual_recently,
    "checkpoint_recent": check_checkpoint_recent,
}


def path_matches_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given glob patterns."""
    # Normalize to forward slashes
    norm = file_path.replace("\\", "/")
    # Try both the full path and relative path
    for pattern in patterns:
        if fnmatch(norm, pattern):
            return True
        # Also try matching just the relative portion after common prefixes
        parts = norm.split("/")
        for i in range(len(parts)):
            subpath = "/".join(parts[i:])
            if fnmatch(subpath, pattern):
                return True
    return False


def is_renderer_path(file_path: str) -> bool:
    """Check if path is in renderer/shader directories."""
    return path_matches_patterns(file_path, ["src/renderer/**", "src/shaders/**", "shaders/**"])


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if not isinstance(input_data, dict):
        sys.exit(0)
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    project_root = find_project_root()

    # Check for temporary lift
    if _check_lift(project_root):
        print(f"[LIFTED] {Path(__file__).name} is temporarily disabled")
        sys.exit(0)
    norm = os.path.normpath(file_path).replace("\\", "/")

    state = load_state(project_root)

    # Track writes for counter-based rules
    is_src = path_matches_patterns(norm, ["src/**"])
    is_renderer = is_renderer_path(norm)

    if is_src and tool_name in ("Write", "Edit"):
        state["total_src_writes"] = state.get("total_src_writes", 0) + 1

    if is_renderer and tool_name in ("Write", "Edit"):
        state["renderer_writes"] = state.get("renderer_writes", 0) + 1
        state["visual_checked_recently"] = False

    # Check: Write on existing non-empty file in hooks/ or tools/ -> WARN
    if tool_name == "Write":
        abs_path = (
            Path(file_path).resolve()
            if Path(file_path).is_absolute()
            else (project_root / file_path).resolve()
        )
        if abs_path.exists() and abs_path.stat().st_size > 0:
            rel = norm.lower()
            if any(seg in rel for seg in ("hooks/", "tools/")):
                msg = (
                    f"CONTROL CODING WORKFLOW: Write will OVERWRITE existing "
                    f"infrastructure file {abs_path.name} "
                    f"({abs_path.stat().st_size} bytes). "
                    f"Use Edit to modify, or Read the file first to verify."
                )
                if HAS_LOGGER:
                    log_event(project_root, "WARN", "workflow", norm,
                              f"overwrite_existing: {abs_path.name}")
                print(json.dumps({"decision": "warn", "reason": msg}))
                save_state(project_root, state)
                sys.exit(0)

    # Load rules: cc_config.json -> inline fallback
    rules = _load_config_rules(project_root)
    if rules is None:
        rules = WORKFLOW_RULES

    # Evaluate rules
    for rule in rules:
        trigger = rule["trigger"]

        # Check tool match
        if tool_name not in trigger.get("tools", []):
            continue

        # Check path match
        if not path_matches_patterns(norm, trigger.get("path_patterns", [])):
            continue

        # Check after_count threshold
        after_count = rule.get("after_count", 0)
        if after_count > 0:
            check_name = rule["check"]
            if check_name == "visual_checked_recently":
                current_count = state.get("renderer_writes", 0)
            elif check_name == "checkpoint_recent":
                current_count = state.get("total_src_writes", 0)
            else:
                current_count = state.get("total_src_writes", 0)

            if current_count < after_count:
                continue

        # Run the check function
        check_name = rule["check"]
        check_fn = CHECK_FUNCTIONS.get(check_name)
        if check_fn is None:
            continue

        prerequisite_met = check_fn(project_root)

        # Also check state for recently-reset conditions
        if check_name == "visual_checked_recently" and state.get("visual_checked_recently"):
            prerequisite_met = True
        if check_name == "checkpoint_recent" and state.get("checkpoint_recent"):
            prerequisite_met = True

        if prerequisite_met:
            continue

        # For plan_before_code: only warn once (not on every write)
        if rule["name"] == "plan_before_code":
            if rule["name"] in state.get("warned_rules", []):
                continue

        # Prerequisite not met - enforce the rule
        mode = rule.get("mode", "warn")
        message = rule.get("message", "Workflow prerequisite not met.")

        if HAS_LOGGER:
            decision_label = "DENY" if mode == "deny" else "WARN"
            log_event(project_root, decision_label, "workflow", norm,
                      f"{rule['name']}: {message}")

        # Track that we warned for this rule (avoid repeated warnings)
        warned = state.get("warned_rules", [])
        if rule["name"] not in warned:
            warned.append(rule["name"])
            state["warned_rules"] = warned

        save_state(project_root, state)

        if mode == "deny":
            _emit_block(f"CONTROL CODING WORKFLOW: {message}")
        else:
            print(json.dumps({
                "decision": "warn",
                "reason": f"CONTROL CODING WORKFLOW: {message}",
            }))
            sys.exit(0)

    # All rules passed
    save_state(project_root, state)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        try:
            _root = find_project_root()
            if HAS_LOGGER:
                log_event(_root, "ERROR", Path(__file__).name,
                          str(exc), "Hook crashed - failing open")
        except Exception:
            pass
        print(f"[HOOK ERROR] {Path(__file__).name} crashed: {exc} - failing open (allow)")
        sys.exit(0)
