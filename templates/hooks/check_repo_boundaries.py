#!/usr/bin/env python3
"""check_repo_boundaries.py - Repo-side boundary enforcement for staged changes.

Runs in git pre-commit and blocks commits that touch DENY-protected paths.
WARN-protected paths are reported but do not block the commit.

Exit 0 = allowed (with optional warnings).
Exit 2 = blocked because staged changes hit a DENY zone or the check could not run.
"""

from __future__ import annotations

import json
import posixpath
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import normalize_protected_zones
except ImportError:
    from templates.hooks.hook_utils import normalize_protected_zones


CONTROL_PLANE_DIRS = (".controlcoding", ".claude")


def _find_project_root() -> Path:
    starts = [Path.cwd(), Path(__file__).resolve().parent]
    for start in starts:
        for candidate in [start, *start.parents]:
            if (candidate / ".git").is_dir():
                return candidate
            for dirname in CONTROL_PLANE_DIRS:
                if (candidate / dirname / "cc_config.json").exists():
                    return candidate
    return Path.cwd()


def _control_plane_path(project_root: Path, relative_name: str) -> Path:
    for dirname in CONTROL_PLANE_DIRS:
        candidate = project_root / dirname / relative_name
        if candidate.exists():
            return candidate
    return project_root / CONTROL_PLANE_DIRS[0] / relative_name


def _load_protected_zones(project_root: Path) -> list[dict]:
    config_path = _control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return []
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return normalize_protected_zones(payload.get("protected_zones", []))


def _load_approved_lifts(project_root: Path) -> set[str]:
    lift_path = _control_plane_path(project_root, "lift_request.json")
    if not lift_path.exists():
        return set()
    try:
        payload = json.loads(lift_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()

    if payload.get("status") != "APPROVED":
        return set()

    expires_at = str(payload.get("expires_at", "")).strip()
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expiry:
                return set()
        except ValueError:
            return set()

    approved = set()
    for zone in payload.get("zones", []):
        if isinstance(zone, str) and zone.strip():
            normalized = _normalize_relative_path(zone)
            if normalized:
                approved.add(normalized)
    return approved


def _consume_approved_lifts(project_root: Path, consumed_zones: set[str]) -> None:
    if not consumed_zones:
        return
    lift_path = _control_plane_path(project_root, "lift_request.json")
    if not lift_path.exists():
        return
    try:
        payload = json.loads(lift_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if payload.get("status") != "APPROVED":
        return
    normalized_consumed = {
        normalized for normalized in (
            _normalize_relative_path(zone) for zone in consumed_zones
        )
        if normalized
    }
    remaining = []
    for zone in payload.get("zones", []):
        if not isinstance(zone, str):
            continue
        if _normalize_relative_path(zone) not in normalized_consumed:
            remaining.append(zone)
    if remaining:
        payload["zones"] = remaining
    else:
        payload["zones"] = []
        payload["status"] = "CONSUMED"
        payload["consumed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        lift_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return


def _staged_paths(project_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRD"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff --cached failed")
    return [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _normalize_relative_path(path: str) -> str:
    normalized = posixpath.normpath(str(path).strip().replace("\\", "/"))
    if normalized == ".":
        return ""
    return normalized.lstrip("/")


def _matches_zone(relative_path: str, zone_path: str) -> bool:
    normalized_file = _normalize_relative_path(relative_path)
    normalized_zone = _normalize_relative_path(zone_path)
    if not normalized_zone:
        return False
    return normalized_file == normalized_zone or normalized_file.startswith(normalized_zone + "/")


def evaluate_staged_paths(staged_paths: list[str],
                          protected_zones: list[dict],
                          approved_lifts: set[str] | None = None,
                          consumed_lifts: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    deny_hits: list[dict] = []
    warn_hits: list[dict] = []
    active_lifts = {
        normalized for normalized in (
            _normalize_relative_path(zone) for zone in (approved_lifts or set())
        )
        if normalized
    }

    for relative_path in staged_paths:
        matched_warns: list[dict] = []
        matched_denies: list[dict] = []
        for zone in protected_zones:
            zone_path = str(zone.get("path", "")).strip()
            if not _matches_zone(relative_path, zone_path):
                continue
            normalized_zone = _normalize_relative_path(zone_path)
            if normalized_zone in active_lifts:
                if consumed_lifts is not None:
                    consumed_lifts.add(normalized_zone)
                continue
            level = str(zone.get("level", "warn")).strip().lower()
            entry = {
                "file": relative_path,
                "zone": zone_path,
                "description": str(zone.get("description", "")).strip(),
                "level": level,
            }
            if level == "deny":
                matched_denies.append(entry)
            else:
                matched_warns.append(entry)

        deny_hits.extend(matched_denies)
        if not matched_denies:
            warn_hits.extend(matched_warns)

    return deny_hits, warn_hits


def _format_entry(entry: dict) -> str:
    description = f" - {entry['description']}" if entry.get("description") else ""
    return f"{entry['file']} -> {entry['zone']}{description}"


def main() -> int:
    project_root = _find_project_root()
    protected_zones = _load_protected_zones(project_root)
    if not protected_zones:
        return 0

    try:
        staged = _staged_paths(project_root)
    except RuntimeError as exc:
        print(f"CONTROL CODING repo boundary gate: failed to inspect staged files: {exc}", file=sys.stderr)
        return 2

    if not staged:
        return 0

    consumed_lifts: set[str] = set()
    deny_hits, warn_hits = evaluate_staged_paths(
        staged,
        protected_zones,
        approved_lifts=_load_approved_lifts(project_root),
        consumed_lifts=consumed_lifts,
    )

    for entry in warn_hits:
        print(
            "CONTROL CODING repo boundary gate: WARN staged change in protected path: "
            + _format_entry(entry),
            file=sys.stderr,
        )

    if deny_hits:
        print("CONTROL CODING repo boundary gate: commit blocked.", file=sys.stderr)
        for entry in deny_hits:
            print(f"  - {_format_entry(entry)}", file=sys.stderr)
        print(
            "Request and approve a scoped lift before committing if this protected change is intentional.",
            file=sys.stderr,
        )
        return 2

    _consume_approved_lifts(project_root, consumed_lifts)

    return 0


if __name__ == "__main__":
    sys.exit(main())
