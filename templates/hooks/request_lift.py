#!/usr/bin/env python3
"""request_lift.py - ControlCoding scoped lift request tool.

Creates a PENDING lift request that requires human approval before activation.
The AI cannot self-approve lifts - this is a mechanical constraint.

Flow:
1. AI runs: python hooks/request_lift.py --file <path> --reason "<why>"
2. Script creates a PENDING request and prints an approval command
3. Human reviews and runs: python hooks/request_lift.py --approve
4. Only then does check_boundaries.py allow the operation

The approval step requires --approve flag which the AI must NOT use.
The CLAUDE.md rule states: "Never run request_lift.py --approve yourself."
In autonomous mode (no human), lifts cannot be approved.

Usage:
    python hooks/request_lift.py --file core/models.py --reason "fix zero discount bug"
    python hooks/request_lift.py --approve          # HUMAN ONLY
    python hooks/request_lift.py --status            # check current request
    python hooks/request_lift.py --clear             # remove request
"""

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import control_plane_path, find_project_root, normalize_protected_zones
except ImportError:
    from templates.hooks.hook_utils import control_plane_path, find_project_root, normalize_protected_zones


def _find_project_root():
    """Walk up from this file's directory looking for project root markers."""
    return find_project_root(__file__, env_var="CONTROLCODING_PROJECT_ROOT")


def _lift_path(project_root):
    return control_plane_path(project_root, "lift_request.json")


def request_lift(project_root, file_path, reason):
    """Create a PENDING lift request. Does NOT activate it."""
    lp = _lift_path(project_root)

    # Determine which zone this file belongs to
    # Normalize the file path relative to project root
    try:
        rel = Path(file_path).resolve().relative_to(project_root)
    except ValueError:
        rel = Path(file_path)
    rel_str = str(rel).replace("\\", "/")

    # Try to find the matching zone from cc_config.json
    zone = rel_str
    config_path = control_plane_path(project_root, "cc_config.json")
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            for z in normalize_protected_zones(config.get("protected_zones", [])):

                zp = z["path"].rstrip("/")
                if rel_str.startswith(zp):
                    zone = z["path"]
                    break
        except (json.JSONDecodeError, KeyError):
            pass

    data = {
        "status": "PENDING",
        "zones": [zone],
        "file": rel_str,
        "reason": reason,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": "",  # Set on approval
        "approval_token": secrets.token_urlsafe(8),
    }

    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print("=" * 60)
    print("LIFT REQUEST CREATED (status: PENDING)")
    print("=" * 60)
    print(f"  Zone:   {zone}")
    print(f"  File:   {rel_str}")
    print(f"  Reason: {reason}")
    print()
    print("This lift is NOT active yet. To approve, the HUMAN must run:")
    print()
    print(f"  python hooks/request_lift.py --approve")
    print()
    print("Approval requires an interactive terminal and this token:")
    print(f"  {data['approval_token']}")
    print()
    print("The AI must NOT run --approve itself.")
    print("After approval, the lift expires in 1 hour or after the")
    print("first successful Edit/Write to the protected zone.")
    print("=" * 60)


def approve_lift(project_root):
    """Activate a PENDING lift request. HUMAN ONLY."""
    lp = _lift_path(project_root)
    if not lp.exists():
        print("No pending lift request found.")
        sys.exit(1)

    try:
        data = json.loads(lp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("Error reading lift request file.")
        sys.exit(1)

    if data.get("status") == "APPROVED":
        exp = data.get("expires_at", "unknown")
        print(f"Lift is already APPROVED (expires: {exp})")
        return

    if data.get("status") != "PENDING":
        print(f"Unexpected lift status: {data.get('status')}")
        sys.exit(1)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Lift approval requires an interactive human terminal.")
        print("Non-interactive or programmatic approval is blocked.")
        sys.exit(2)

    token = str(data.get("approval_token", "")).strip()
    expected = token or "APPROVE LIFT"
    print("Human approval required.")
    print(f"Type this approval token exactly: {expected}")
    typed = input("Approval token: ").strip()
    if typed != expected:
        print("Approval token did not match. Lift remains PENDING.")
        sys.exit(2)

    # Approve: set status and expiry
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    data["status"] = "APPROVED"
    data["approved_at"] = datetime.now(timezone.utc).isoformat()
    data["expires_at"] = expires.isoformat()

    lp.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print("=" * 60)
    print("LIFT APPROVED")
    print("=" * 60)
    print(f"  Zone:    {', '.join(data.get('zones', []))}")
    print(f"  File:    {data.get('file', 'unknown')}")
    print(f"  Reason:  {data.get('reason', 'unknown')}")
    print(f"  Expires: {expires.isoformat()}")
    print()
    print("The AI can now Edit/Write to this zone until expiry.")
    print("=" * 60)


def show_status(project_root):
    """Show current lift request status."""
    lp = _lift_path(project_root)
    if not lp.exists():
        print("No lift request found.")
        return

    try:
        data = json.loads(lp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("Error reading lift request file.")
        return

    status = data.get("status", "unknown")
    print(f"Status:  {status}")
    print(f"Zone:    {', '.join(data.get('zones', []))}")
    print(f"File:    {data.get('file', 'unknown')}")
    print(f"Reason:  {data.get('reason', 'unknown')}")
    if data.get("expires_at"):
        print(f"Expires: {data['expires_at']}")


def clear_lift(project_root):
    """Remove lift request file."""
    lp = _lift_path(project_root)
    if lp.exists():
        lp.unlink()
        print("Lift request cleared.")
    else:
        print("No lift request to clear.")


def main():
    parser = argparse.ArgumentParser(description="ControlCoding lift request tool")
    parser.add_argument("--file", help="File path that needs modification")
    parser.add_argument("--reason", help="Why this modification is necessary")
    parser.add_argument("--approve", action="store_true",
                        help="HUMAN ONLY: approve a pending lift request")
    parser.add_argument("--status", action="store_true",
                        help="Show current lift request status")
    parser.add_argument("--clear", action="store_true",
                        help="Remove lift request")
    args = parser.parse_args()

    project_root = _find_project_root()

    if args.approve:
        approve_lift(project_root)
    elif args.status:
        show_status(project_root)
    elif args.clear:
        clear_lift(project_root)
    elif args.file and args.reason:
        request_lift(project_root, args.file, args.reason)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
