"""Lifecycle diagnostics and local artifact cleanup helpers."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import sqlite3
import shutil
from pathlib import Path
from typing import Any

from .entities import _row_to_dict
from .ledger import _insert_event
from .schema import VALID_LIFECYCLES
from .store import (
    _ensure_schema,
    _json_dumps,
    _memory_connection,
    _now_iso,
    _print_json_error_or_text,
    _relative_path,
    _require_initialized,
)

PYTEST_TEMP_PATTERNS = (
    ".pytest-local-*",
    ".pytest-tmp",
    ".tmp_pytest*",
    "pytest-of-*",
)

LIFECYCLE_ATTENTION_STATES = {"conflicting", "legacy", "needs_review", "stale", "superseded"}
_LIFECYCLE_FACETS = {
    "active",
    "archived",
    "conflicting",
    "legacy",
    "needs_review",
    "stale",
    "superseded",
}


def _edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    seed = f"{source_id}:{target_id}:{edge_type}"
    return "EDGE_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20].upper()


def _lifecycle_event_type(lifecycle: str) -> str:
    return {
        "archived": "archive",
        "conflicting": "mark_conflicting",
        "legacy": "mark_legacy",
        "stale": "mark_stale",
        "superseded": "supersede",
    }.get(lifecycle, "review")


def _normalize_entity_data_for_lifecycle(data: dict[str, Any], lifecycle: str) -> dict[str, Any]:
    updated = dict(data)
    facets = updated.get("document_facets")
    facet_set = {str(item) for item in facets} if isinstance(facets, list) else set()
    facet_set.difference_update(_LIFECYCLE_FACETS)
    if lifecycle in _LIFECYCLE_FACETS:
        facet_set.add(lifecycle)
    elif lifecycle in {"active", "implemented", "verified", "triaged"}:
        facet_set.add("active")
    updated["document_facets"] = sorted(facet_set)
    return updated


def _find_entity_for_lifecycle(
    conn: sqlite3.Connection,
    project: Path,
    selector: str,
) -> tuple[dict[str, Any] | None, str]:
    selector = selector.strip()
    if not selector:
        return None, "empty selector"
    rel_selector = _relative_path(project, selector)
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (selector,)).fetchone()
    if row:
        return _row_to_dict(row), ""
    row = conn.execute("SELECT * FROM entities WHERE path = ?", (rel_selector,)).fetchone()
    if row:
        return _row_to_dict(row), ""
    rows = conn.execute("SELECT * FROM entities WHERE title = ? ORDER BY updated_at DESC", (selector,)).fetchall()
    if len(rows) == 1:
        return _row_to_dict(rows[0]), ""
    if len(rows) > 1:
        return None, f"ambiguous selector: {selector}"
    return None, f"entity not found: {selector}"


def _insert_memory_edge(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    edge_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO edges(id, source_id, target_id, type, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            _edge_id(source_id, target_id, edge_type),
            source_id,
            target_id,
            edge_type,
            _json_dumps(data or {}),
            _now_iso(),
        ),
    )


def _set_entity_lifecycle(
    conn: sqlite3.Connection,
    project: Path,
    entity: dict[str, Any],
    lifecycle: str,
    command: str,
    reason: str,
    event_type: str = "",
    after_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if lifecycle not in VALID_LIFECYCLES:
        raise ValueError(f"invalid lifecycle: {lifecycle}")
    now = _now_iso()
    entity_id = str(entity["id"])
    path = str(entity.get("path") or "")
    old_lifecycle = str(entity.get("lifecycle") or "")
    data = entity.get("data") if isinstance(entity.get("data"), dict) else {}
    updated_data = _normalize_entity_data_for_lifecycle(data, lifecycle)
    conn.execute(
        "UPDATE entities SET lifecycle = ?, data = ?, updated_at = ? WHERE id = ?",
        (lifecycle, _json_dumps(updated_data), now, entity_id),
    )
    if path:
        conn.execute(
            "UPDATE semantic_chunks SET lifecycle = ?, updated_at = ? WHERE source_path = ?",
            (lifecycle, now, path),
        )
    after_state: dict[str, Any] = {"lifecycle": lifecycle}
    if after_extra:
        after_state.update(after_extra)
    _insert_event(
        conn,
        project,
        event_type or _lifecycle_event_type(lifecycle),
        command,
        target_entity_ids=[entity_id],
        affected_paths=[path] if path else [],
        before_state={"lifecycle": old_lifecycle},
        after_state=after_state,
        reason=reason or f"Marked entity lifecycle as {lifecycle}",
        review_required=lifecycle in LIFECYCLE_ATTENTION_STATES,
    )
    result = dict(entity)
    result["lifecycle"] = lifecycle
    result["data"] = updated_data
    return result


def cmd_memory_lifecycle_mark(
    project: Path,
    selector: str,
    lifecycle: str,
    reason: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_lifecycle_memory_not_initialized", message)
        return 1
    if lifecycle not in VALID_LIFECYCLES:
        _print_json_error_or_text(json_output, "lifecycle_invalid", f"invalid lifecycle: {lifecycle}")
        return 1
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        entity, error = _find_entity_for_lifecycle(conn, project, selector)
        if error:
            _print_json_error_or_text(json_output, "lifecycle_entity_not_found", str(error))
            return 1
        assert entity is not None
        updated = _set_entity_lifecycle(
            conn,
            project,
            entity,
            lifecycle,
            "cc memory lifecycle mark",
            reason,
        )
    payload = {"ok": True, "entity": updated}
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Marked lifecycle: {updated['id']} -> {lifecycle}")
    return 0


def cmd_memory_lifecycle_supersede(
    project: Path,
    old_selector: str,
    new_selector: str,
    reason: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_lifecycle_memory_not_initialized", message)
        return 1
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        old_entity, old_error = _find_entity_for_lifecycle(conn, project, old_selector)
        new_entity, new_error = _find_entity_for_lifecycle(conn, project, new_selector)
        if old_error or new_error:
            _print_json_error_or_text(json_output, "lifecycle_entity_not_found", str(old_error or new_error))
            return 1
        assert old_entity is not None
        assert new_entity is not None
        old_id = str(old_entity["id"])
        new_id = str(new_entity["id"])
        if old_id == new_id:
            _print_json_error_or_text(json_output, "lifecycle_supersede_same_entity", "supersede requires two different entities")
            return 1
        edge_data = {"reason": reason, "command": "cc memory lifecycle supersede"}
        _insert_memory_edge(conn, new_id, old_id, "supersedes", edge_data)
        _insert_memory_edge(conn, old_id, new_id, "superseded_by", edge_data)
        updated = _set_entity_lifecycle(
            conn,
            project,
            old_entity,
            "superseded",
            "cc memory lifecycle supersede",
            reason,
            event_type="supersede",
            after_extra={"superseded_by": new_id},
        )
    payload = {"ok": True, "superseded": updated, "replacement": new_entity}
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Superseded: {old_id}")
        print(f"Replacement: {new_id}")
    return 0


def cmd_memory_lifecycle_conflict(
    project: Path,
    left_selector: str,
    right_selector: str,
    reason: str = "",
    json_output: bool = False,
) -> int:
    ok, message = _require_initialized(project)
    if not ok:
        _print_json_error_or_text(json_output, "memory_lifecycle_memory_not_initialized", message)
        return 1
    with _memory_connection(project) as conn:
        _ensure_schema(conn)
        left_entity, left_error = _find_entity_for_lifecycle(conn, project, left_selector)
        right_entity, right_error = _find_entity_for_lifecycle(conn, project, right_selector)
        if left_error or right_error:
            _print_json_error_or_text(json_output, "lifecycle_entity_not_found", str(left_error or right_error))
            return 1
        assert left_entity is not None
        assert right_entity is not None
        left_id = str(left_entity["id"])
        right_id = str(right_entity["id"])
        if left_id == right_id:
            _print_json_error_or_text(json_output, "lifecycle_conflict_same_entity", "conflict requires two different entities")
            return 1
        edge_data = {"reason": reason, "command": "cc memory lifecycle conflict"}
        _insert_memory_edge(conn, left_id, right_id, "conflicts_with", edge_data)
        _insert_memory_edge(conn, right_id, left_id, "conflicts_with", edge_data)
        left_updated = _set_entity_lifecycle(
            conn,
            project,
            left_entity,
            "conflicting",
            "cc memory lifecycle conflict",
            reason,
            event_type="mark_conflicting",
            after_extra={"conflicts_with": right_id},
        )
        right_updated = _set_entity_lifecycle(
            conn,
            project,
            right_entity,
            "conflicting",
            "cc memory lifecycle conflict",
            reason,
            event_type="mark_conflicting",
            after_extra={"conflicts_with": left_id},
        )
    payload = {"ok": True, "left": left_updated, "right": right_updated}
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Marked conflict: {left_id} <-> {right_id}")
    return 0


def _is_known_pytest_temp_name(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in PYTEST_TEMP_PATTERNS)


def _is_safe_project_child(project: Path, candidate: Path) -> bool:
    try:
        project_resolved = project.resolve(strict=False)
        candidate_resolved = candidate.resolve(strict=False)
        candidate_resolved.relative_to(project_resolved)
    except (OSError, ValueError):
        return False
    return _is_known_pytest_temp_name(candidate.name)


def _manual_remove_command(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    return f"Remove-Item -LiteralPath '{escaped}' -Recurse -Force"


def diagnose_pytest_temp_dirs(project: Path) -> list[dict[str, Any]]:
    """Return shallow diagnostics for pytest temp directories under project.

    The function deliberately avoids recursive traversal. ACL-locked pytest
    directories are detected by probing only the candidate directory itself.
    """
    diagnostics: list[dict[str, Any]] = []
    try:
        children = list(project.iterdir())
    except OSError as exc:
        return [
            {
                "path": project.as_posix(),
                "status": "blocked",
                "reason": f"cannot list project root: {exc}",
                "manualStep": "Inspect the project root permissions before cleanup.",
            }
        ]

    for child in sorted(children, key=lambda item: item.name.lower()):
        if not _is_known_pytest_temp_name(child.name):
            continue
        safe = _is_safe_project_child(project, child)
        status = "candidate"
        reason = "known pytest temp directory"
        if not safe:
            diagnostics.append(
                {
                    "path": child.as_posix(),
                    "status": "skipped",
                    "reason": "path failed project-root safety verification",
                    "manualStep": "Do not delete automatically. Inspect the path manually.",
                }
            )
            continue
        try:
            if not child.is_dir():
                diagnostics.append(
                    {
                        "path": child.as_posix(),
                        "status": "skipped",
                        "reason": "candidate is not a directory",
                        "manualStep": "No cleanup required.",
                    }
                )
                continue
            try:
                next(child.iterdir(), None)
            except StopIteration:
                pass
        except OSError as exc:
            status = "blocked"
            reason = f"directory probe failed: {exc}"
        diagnostics.append(
            {
                "path": child.as_posix(),
                "status": status,
                "reason": reason,
                "manualStep": _manual_remove_command(child.resolve(strict=False)),
            }
        )
    return diagnostics


def cleanup_pytest_temp_dirs(project: Path, apply: bool = False) -> list[dict[str, Any]]:
    """Dry-run or remove verified pytest temp directories under project."""
    results: list[dict[str, Any]] = []
    for entry in diagnose_pytest_temp_dirs(project):
        path = Path(str(entry["path"]))
        result = dict(entry)
        if result["status"] == "skipped":
            results.append(result)
            continue
        if not apply:
            result["cleanup"] = "dry-run"
            results.append(result)
            continue
        if result["status"] != "candidate":
            result["cleanup"] = "not-removed"
            results.append(result)
            continue
        if not _is_safe_project_child(project, path):
            result["status"] = "skipped"
            result["reason"] = "path failed project-root safety verification before removal"
            result["cleanup"] = "not-removed"
            results.append(result)
            continue
        try:
            shutil.rmtree(path)
            result["status"] = "removed"
            result["cleanup"] = "removed"
            result["manualStep"] = ""
        except OSError as exc:
            result["status"] = "blocked"
            result["reason"] = f"removal failed: {exc}"
            result["cleanup"] = "not-removed"
            result["manualStep"] = _manual_remove_command(path.resolve(strict=False))
        results.append(result)
    return results


def format_pytest_temp_warning(diagnostics: list[dict[str, Any]]) -> str:
    if not diagnostics:
        return ""
    blocked = [item for item in diagnostics if item.get("status") == "blocked"]
    count_text = f"{len(diagnostics)} pytest temp candidate(s) found"
    if blocked:
        first = blocked[0]
        return (
            f"{count_text}; {len(blocked)} blocked by filesystem permissions. "
            f"Manual cleanup example after confirming no test process is using it: {first.get('manualStep', '')}"
        )
    return f"{count_text}. Run `cc memory cleanup-temp --project-root . --apply` to remove verified candidates."


def cmd_memory_temp_cleanup(project: Path, apply: bool = False, json_output: bool = False) -> int:
    results = cleanup_pytest_temp_dirs(project, apply=apply)
    payload = {
        "ok": True,
        "apply": apply,
        "candidates": results,
        "blocked": sum(1 for item in results if item.get("status") == "blocked"),
        "removed": sum(1 for item in results if item.get("status") == "removed"),
    }
    if json_output:
        import json

        print(json.dumps(payload, indent=2))
        return 0
    if not results:
        print("No verified pytest temp cleanup candidates found.")
        return 0
    heading = "Pytest temp cleanup" if apply else "Pytest temp cleanup dry-run"
    print(heading)
    for item in results:
        print(f"  {str(item.get('status', '')).upper()} {item.get('path')} - {item.get('reason', '')}")
        if item.get("status") == "blocked" and item.get("manualStep"):
            print(f"    Manual step: {item['manualStep']}")
    if not apply:
        print("Run again with --apply to remove only verified candidates inside the project root.")
    return 0
