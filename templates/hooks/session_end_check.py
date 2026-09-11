#!/usr/bin/env python3
"""session_end_check.py - SessionEnd integrity verification hook.

Runs at session end (Stop hook) to verify that all DENY-protected files
are unchanged from their baseline state. Detects if boundary protection
was bypassed during the session.

First run: creates baseline hashes in .controlcoding/deny_hashes.json
Subsequent runs: compares current hashes against baseline, reports changes

Hook config in .controlcoding/settings.json:
    {
      "hooks": {
        "Stop": [
          {
            "matcher": "",
            "hooks": [
              {
                "type": "command",
                "command": "python hooks/session_end_check.py"
              }
            ]
          }
        ]
      }
    }
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import (
        control_plane_display_path,
        control_plane_path,
        find_project_root,
        normalize_protected_zones,
    )
except ImportError:
    from templates.hooks.hook_utils import (
        control_plane_display_path,
        control_plane_path,
        find_project_root,
        normalize_protected_zones,
    )


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


def _report_lift_requests(project_root):
    """Report any active zone-scoped lift requests found at session end."""
    lift_path = control_plane_path(project_root, "lift_request.json")
    lift_label = control_plane_display_path(project_root, "lift_request.json")
    if not lift_path.exists():
        return
    try:
        data = json.loads(lift_path.read_text(encoding="utf-8"))
        zones = data.get("zones", [])
        reason = data.get("reason", "no reason given")
        expires_at = data.get("expires_at", "unknown")
        expired = False
        if expires_at and expires_at != "unknown":
            try:
                exp_time = datetime.fromisoformat(expires_at)
                if exp_time.tzinfo is None:
                    exp_time = exp_time.replace(tzinfo=timezone.utc)
                expired = datetime.now(timezone.utc) > exp_time
            except ValueError:
                pass
        status = "EXPIRED" if expired else "ACTIVE"
        print(f"[SessionEnd] NOTICE: Zone-scoped lift was {status} during this session:")
        print(f"  Zones: {', '.join(zones)}")
        print(f"  Reason: {reason}")
        print(f"  Expires at: {expires_at}")
        if expired:
            print(f"  Action: Lift has expired. Delete {lift_label} to clean up.")
        else:
            print(f"  Action: Review changes in lifted zones. Delete {lift_label} when done.")
    except (json.JSONDecodeError, OSError):
        print(f"[SessionEnd] WARNING: {lift_label} exists but could not be parsed.")


def _load_deny_from_config(project_root):
    """Load DENY patterns from the active cc_config.json if it exists."""
    config_path = control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        zones = normalize_protected_zones(data.get("protected_zones", []))


        return [z["path"] for z in zones if z.get("level", "").lower() == "deny"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _get_deny_from_source():
    """Fallback: extract DENY patterns by parsing check_boundaries.py source."""
    boundaries_path = Path(__file__).parent / "check_boundaries.py"
    if not boundaries_path.exists():
        return []

    deny_files = []
    content = boundaries_path.read_text(encoding="utf-8")

    in_zones = False
    for line in content.split("\n"):
        stripped = line.strip()
        if "PROTECTED_ZONES" in stripped and "=" in stripped:
            in_zones = True
            continue
        if in_zones:
            if stripped.startswith("]"):
                break
            if '"DENY"' in stripped or "'DENY'" in stripped:
                parts = stripped.split('"')
                if len(parts) >= 2:
                    deny_files.append(parts[1])
                else:
                    parts = stripped.split("'")
                    if len(parts) >= 2:
                        deny_files.append(parts[1])

    return deny_files


def get_deny_files(project_root):
    """Get DENY file patterns. Tries cc_config.json first, falls back to source parsing."""
    patterns = _load_deny_from_config(project_root)
    if patterns is not None:
        return patterns
    return _get_deny_from_source()


def find_matching_files(project_root, patterns):
    """Find actual files matching DENY patterns.

    Skips __pycache__ directories and compiled bytecode (.pyc, .pyo)
    to avoid false positives from files that change on every run.
    """
    skip_dirs = {"__pycache__", ".git", "node_modules"}
    skip_ext = {".pyc", ".pyo"}
    matched = []
    for pattern in patterns:
        for root_dir, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if os.path.splitext(f)[1] in skip_ext:
                    continue
                full_path = os.path.join(root_dir, f)
                norm = os.path.normpath(full_path).replace("\\", "/")
                if pattern in norm:
                    matched.append(norm)
    return matched


def compute_hash(filepath):
    """SHA256 hash of a file."""
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _is_git_tracked(project_root, filepath):
    """Check if a file is tracked by git."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", filepath],
            cwd=str(project_root), capture_output=True, text=True,
        )
        return result.returncode == 0
    except OSError:
        return False


def _find_project_root():
    """Walk up from this file's directory looking for project root markers.
    Prefers .git/ (authoritative), then the active control plane."""
    return find_project_root(__file__)


def _commit_ceremony_reminder(project_root):
    """Check for uncommitted work and remind about commit ceremony."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root), capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        if not lines:
            return
        modified = [l for l in lines if l.startswith(" M") or l.startswith("M ")]
        added = [l for l in lines if l.startswith("??")]
        staged = [l for l in lines if l[0] in "MADRC" and l[0] != "?"]
        print(f"[SessionEnd] UNCOMMITTED WORK: {len(lines)} file(s) changed")
        if modified:
            print(f"  Modified: {len(modified)} | New: {len(added)} | Staged: {len(staged)}")
        print("[SessionEnd] COMMIT CEREMONY REMINDER:")
        print("  1. Review changes: git diff")
        print("  2. Update STATUS.md with current state")
        print("  3. Write devlog entry for this session")
        print("  4. Commit and push")
    except (OSError, subprocess.TimeoutExpired):
        pass


def main():
    project_root = _find_project_root()
    hash_file = control_plane_path(project_root, "deny_hashes.json")
    hash_file.parent.mkdir(parents=True, exist_ok=True)

    # Report any zone-scoped lift usage (always, regardless of deny patterns)
    _report_lift_requests(project_root)

    # Check for temporary lift
    if _check_lift(project_root):
        print(f"[LIFTED] {Path(__file__).name} is temporarily disabled")
        sys.exit(0)

    # Get DENY patterns
    deny_patterns = get_deny_files(project_root)
    if not deny_patterns:
        # No DENY files configured, nothing to check
        sys.exit(0)

    # Find actual files
    deny_files = find_matching_files(str(project_root), deny_patterns)
    if not deny_files:
        sys.exit(0)

    # Compute current hashes
    current_hashes = {}
    for fp in deny_files:
        h = compute_hash(fp)
        if h:
            current_hashes[fp] = h

    # Load baseline
    if hash_file.exists():
        try:
            baseline = json.loads(hash_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            baseline = {}
    else:
        baseline = {}

    if not baseline:
        # First run: create baseline
        hash_file.write_text(
            json.dumps(current_hashes, indent=2), encoding="utf-8"
        )
        print(f"[SessionEnd] Baseline created for {len(current_hashes)} DENY files.")
        sys.exit(0)

    # Compare
    changed = []
    for fp, current_hash in current_hashes.items():
        baseline_hash = baseline.get(fp)
        if baseline_hash and baseline_hash != current_hash:
            changed.append(fp)

    new_files = [fp for fp in current_hashes if fp not in baseline]

    # Update baseline for next session
    hash_file.write_text(
        json.dumps(current_hashes, indent=2), encoding="utf-8"
    )

    if changed:
        print(f"[SessionEnd] WARNING: {len(changed)} DENY file(s) were modified during this session:")
        for fp in changed:
            short = fp.replace(str(project_root).replace("\\", "/") + "/", "")
            tracked = _is_git_tracked(project_root, fp)
            if tracked:
                print(f"  - {short} (git-tracked, review and commit or restore manually)")
            else:
                print(f"  - {short} (not tracked by git, manual review needed)")
        print("[SessionEnd] Action required: review changes, then commit or `git checkout --` to restore.")
    else:
        print(f"[SessionEnd] All {len(current_hashes)} DENY files intact.")

    if new_files:
        print(f"[SessionEnd] {len(new_files)} new DENY-zone file(s) created (added to baseline).")

    # Commit ceremony reminder: check for uncommitted work
    _commit_ceremony_reminder(project_root)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[HOOK ERROR] {Path(__file__).name} crashed: {exc} - failing open (allow)")
        sys.exit(0)
