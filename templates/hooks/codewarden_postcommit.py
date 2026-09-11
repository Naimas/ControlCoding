#!/usr/bin/env python3
"""codewarden_postcommit.py - Cross-platform post-commit hook.

Python alternative to the bash codewarden_postcommit script.
Works natively on Windows, macOS, and Linux without requiring bash.

Runs codewarden_review.py in background after each commit.

Install as git hook:
  cp hooks/codewarden_postcommit.py .git/hooks/post-commit
  chmod +x .git/hooks/post-commit   # Unix only

Or with a hook manager (husky, pre-commit framework):
  Add "python hooks/codewarden_postcommit.py" as a post-commit hook.
"""

import json
import subprocess
import sys
from pathlib import Path


def _check_engagement():
    """Return True if CodeWarden is allowed at the current engagement level."""
    try:
        # Walk up to find project root
        d = Path(__file__).resolve().parent
        for _ in range(10):
            if (d / ".git").exists() or (d / ".claude").exists():
                break
            d = d.parent
        config = d / ".claude" / "cc_engagement.json"
        if not config.exists():
            return True  # No config = default (active)
        data = json.loads(config.read_text(encoding="utf-8"))
        return data.get("level", 2) >= 2
    except Exception:
        return True


def main():
    # Engagement gating: skip at level 1 (Conservative)
    if not _check_engagement():
        return

    script_dir = Path(__file__).resolve().parent
    review_script = script_dir / "codewarden_review.py"

    if not review_script.exists():
        # Look two levels up from .git/hooks/ into project hooks/
        alt_path = script_dir.parent.parent / "hooks" / "codewarden_review.py"
        if alt_path.exists():
            review_script = alt_path
        else:
            return

    # Run in background so the commit returns immediately.
    # The review report will be ready by the next session.
    try:
        subprocess.Popen(
            [sys.executable, str(review_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


if __name__ == "__main__":
    main()
