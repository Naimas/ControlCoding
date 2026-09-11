#!/usr/bin/env python3
"""hook_logger.py -Logging module for ControlCoding hooks.

Appends a JSON record for each hook event (DENY, WARN, ALLOW, LIFT, ERROR) to a
cc_hook_log.jsonl file in the project root. Zero external dependencies.

Usage: import and call log_event() from your hooks.

    from hook_logger import log_event
    log_event(root, "DENY", "boundary", "src/stable/models.py", "protected zone")

The .jsonl file can be read by the metrics_collector.py script or analyzed manually.
Add cc_hook_log.jsonl to your .gitignore.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_FILENAME = "cc_hook_log.jsonl"


def log_event(
    project_root: str | Path,
    decision: str,
    hook_type: str,
    target: str,
    reason: str = "",
) -> None:
    """Append an event to the JSONL log.

    Args:
        project_root: project root directory (where to write the log)
        decision: "DENY", "WARN", "ALLOW", "LIFT", or "ERROR"
        hook_type: "boundary", "dangerous_command", or hook filename
        target: file path or command that triggered the hook
        reason: reason for the block/warning (optional for ALLOW)
    """
    log_path = Path(project_root) / LOG_FILENAME

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "hook": hook_type,
        "target": target,
    }
    if reason:
        record["reason"] = reason

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        # If we can't write the log, don't block the hook.
        # Logging is best-effort.
        pass
