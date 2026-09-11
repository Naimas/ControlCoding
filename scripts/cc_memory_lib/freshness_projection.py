"""Derived, byte-read-only health projection for Project Memory Engine.

The projection is not a second source of truth.  A writer creates it only
after committing SQLite, and observers validate it without opening SQLite.
"""

from __future__ import annotations

import hashlib
import fnmatch
import json
import math
import os
import stat
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .migrations import TARGET_SCHEMA_VERSION, inspect_schema
from .schema import (
    CONTROL_DIRNAME,
    DB_FILENAME,
    ENTITY_TYPE_CODES,
    MANIFEST_FILENAME,
    MEMORY_DIRNAME,
    SESSION_RECORD_SCHEMA_VERSION,
    VALID_LIFECYCLES,
    VALID_SESSION_MODES,
    VALID_SESSION_STATUSES,
)


HEALTH_PROJECTION_FILENAME = "freshness_projection.json"
HEALTH_PROJECTION_SCHEMA_VERSION = 2
HEALTH_PROJECTION_KIND = "controlcoding_memory_health_projection"
WRITER_LOCK_FILENAME = ".controlcoding-memory.writer.lock"
CAPABILITY_PROFILE_FILENAME = "capability_profile.json"
CAPABILITY_PROFILE_SCHEMA_VERSION = 1
FILESYSTEM_FINGERPRINT_SCHEMA_VERSION = 3
# Bound observer-controlled JSON before allocation and parsing.  Eight MiB is
# intentionally much larger than a normal health projection while still
# keeping pathological inputs bounded.
HEALTH_PROJECTION_MAX_BYTES = 8 * 1024 * 1024
_OBSERVER_JSON_MAX_BYTES = 1024 * 1024
_HASH_CHUNK_SIZE = 1024 * 1024
_WRITER_LOCK_TIMEOUT_SECONDS = 30.0

_CONTROLWORK_DIRNAME = ".controlwork"
_CONTROLWORK_CONTEXT_FILENAME = "CONTROLWORK.md"
_CONTROLWORK_MEMORY_AREAS = (
    "inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy", "views",
)
_CONTROLWORK_LIFECYCLES = ("captured", "active", "needs_review", "superseded", "legacy")
_CONTROLWORK_DISTRIBUTIONS = {"embedded_controlcoding", "standalone_repo"}
_CONTROLWORK_EXPECTED_VIEW_FILENAMES = {
    "index.md",
    "active-decisions.md",
    "open-questions.md",
    "source-ledger.md",
    "handoff-packet.md",
    "category-registry.md",
}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_loads(raw: str) -> Any:
    return json.loads(raw, parse_constant=_reject_json_constant)


def _read_bounded_json_object(
    path: Path,
    *,
    maximum_bytes: int,
    unreadable_reason: str,
    malformed_reason: str,
    too_large_reason: str,
) -> tuple[dict[str, Any] | None, str]:
    """Read one strict JSON object without trusting pathname size or type."""
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None, unreadable_reason
        if before.st_size > maximum_bytes:
            return None, too_large_reason
        raw_bytes = bytearray()
        while len(raw_bytes) <= maximum_bytes:
            chunk = os.read(descriptor, min(_HASH_CHUNK_SIZE, maximum_bytes + 1 - len(raw_bytes)))
            if not chunk:
                break
            raw_bytes.extend(chunk)
        if len(raw_bytes) > maximum_bytes:
            return None, too_large_reason
        after = os.fstat(descriptor)
        if not _stat_matches(before, after):
            return None, unreadable_reason
        raw = bytes(raw_bytes).decode("utf-8")
        payload = _strict_json_loads(raw)
    except FileNotFoundError:
        return None, unreadable_reason
    except (OSError, UnicodeDecodeError):
        return None, unreadable_reason
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, malformed_reason
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not isinstance(payload, dict):
        return None, malformed_reason
    return payload, ""


def projection_path(project: Path) -> Path:
    return project / CONTROL_DIRNAME / MEMORY_DIRNAME / HEALTH_PROJECTION_FILENAME


def writer_lock_path(project: Path) -> Path:
    # The lock parent must pre-exist independently of generated memory state.
    return project / WRITER_LOCK_FILENAME


def capability_profile_path(project: Path) -> Path:
    return project / CONTROL_DIRNAME / MEMORY_DIRNAME / CAPABILITY_PROFILE_FILENAME


def _db_path(project: Path) -> Path:
    return project / CONTROL_DIRNAME / MEMORY_DIRNAME / DB_FILENAME


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_projection_object(path: Path) -> tuple[dict[str, Any], str]:
    """Read the projection twice and reject pathname replacement or mutation."""
    first_record, first_raw, reason = _capture_file_observation(
        path,
        "projection",
        capture_bytes=True,
        maximum_bytes=HEALTH_PROJECTION_MAX_BYTES,
    )
    if reason:
        return {}, "projection_too_large" if reason.endswith("_too_large") else "projection_unreadable"
    if not first_record or first_record.get("type") != "regular" or first_raw is None:
        return {}, "projection_unreadable"

    second_record, second_raw, reason = _capture_file_observation(
        path,
        "projection",
        capture_bytes=True,
        maximum_bytes=HEALTH_PROJECTION_MAX_BYTES,
    )
    if reason:
        return {}, "projection_too_large" if reason.endswith("_too_large") else "projection_unreadable"
    if not second_record or second_record.get("type") != "regular" or second_raw is None:
        return {}, "projection_unreadable"
    if first_record != second_record or first_raw != second_raw:
        return {}, "projection_changed_during_read"

    try:
        payload = _strict_json_loads(first_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return {}, "projection_malformed"
    if not isinstance(payload, dict):
        return {}, "projection_malformed"
    return payload, ""


_CATEGORY_STATUS_VALUES = {"approved", "proposed"}


def _feature_status_health(state: str, reason: str = "", message: str = "") -> dict[str, str]:
    return {"state": state, "reason": reason, "message": message}


def _read_category_registry_for_feature_status(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, "category_registry_missing"
    except OSError:
        return None, "category_registry_unreadable"
    except UnicodeDecodeError:
        return None, "category_registry_invalid"
    try:
        payload = _strict_json_loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, "category_registry_invalid"
    return (payload, "") if isinstance(payload, dict) else (None, "category_registry_invalid")


def _valid_category_registry_for_feature_status(payload: dict[str, Any], capture_areas: set[str]) -> bool:
    if set(payload) != {"schemaVersion", "categories"}:
        return False
    if not _is_non_negative_int(payload["schemaVersion"]) or payload["schemaVersion"] < 1 or not isinstance(payload["categories"], list):
        return False
    slugs: set[str] = set()
    required = {"slug", "name", "area", "description", "status", "builtin", "createdAt"}
    for item in payload["categories"]:
        if not isinstance(item, dict) or not required.issubset(item):
            return False
        if not all(isinstance(item[key], str) and item[key].strip() for key in ("slug", "name", "area", "description", "createdAt")):
            return False
        if item["area"] not in capture_areas or item["status"] not in _CATEGORY_STATUS_VALUES or not isinstance(item["builtin"], bool):
            return False
        if any(key in item and not isinstance(item[key], str) for key in ("approvedAt", "updatedAt")) or item["slug"] in slugs:
            return False
        slugs.add(item["slug"])
    return True


def _markdown_inventory_for_feature_status(
    path: Path,
) -> tuple[dict[str, Any] | None, str]:
    """Capture one stable, closed inventory of immediate markdown files."""
    before, capture_reason = _capture_metadata_path(path, "feature_status_context_packets")
    if capture_reason or before is None:
        return None, capture_reason
    if not before["exists"]:
        return {"root": before, "entries": []}, ""
    if before["type"] != "directory":
        return None, "source_feature_status_context_packets_not_directory"
    try:
        root_lstat = path.lstat()
    except OSError:
        return None, "source_feature_status_context_packets_unreadable"
    if stat.S_ISLNK(root_lstat.st_mode) or (
        getattr(root_lstat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        return None, "source_feature_status_context_packets_not_directory"
    try:
        candidates = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError:
        return None, "source_feature_status_context_packets_unreadable"
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if item.suffix.lower() != ".md":
            continue
        try:
            item_lstat = item.lstat()
        except OSError:
            return None, "source_feature_status_context_packets_unreadable"
        if stat.S_ISLNK(item_lstat.st_mode) or (
            getattr(item_lstat, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            return None, "source_feature_status_context_packets_entry_not_regular"
        record, item_reason = _capture_file(
            item,
            f"feature_status_context_packets_entry_{index}",
        )
        if item_reason or record is None or not record["exists"]:
            return None, item_reason or "source_feature_status_context_packets_path_replaced"
        entries.append({
            "name": item.name,
            "identity": record["identity"],
            "sizeBytes": record["sizeBytes"],
            "mtimeNs": record["mtimeNs"],
            "sha256": record["sha256"],
        })
    after, capture_reason = _capture_metadata_path(path, "feature_status_context_packets")
    if capture_reason or after is None:
        return None, capture_reason
    if before != after:
        return None, "source_feature_status_context_packets_changed_during_capture"
    return {"root": after, "entries": entries}, ""


def _stable_markdown_inventory_for_feature_status(
    path: Path,
    reason: str,
) -> tuple[dict[str, Any] | None, str]:
    """Return markdown packets only after two identical stable inventories."""
    first, first_reason = _markdown_inventory_for_feature_status(path)
    if first_reason or first is None:
        return None, reason
    second, second_reason = _markdown_inventory_for_feature_status(path)
    if second_reason or second is None or second != first:
        return None, reason
    return second, ""


def _markdown_directory_count_for_feature_status(path: Path, reason: str) -> tuple[int | None, str]:
    """Count markdown packets only after two identical stable inventories."""
    inventory, inventory_reason = _stable_markdown_inventory_for_feature_status(path, reason)
    if inventory_reason or inventory is None:
        return None, reason
    return len(inventory["entries"]), ""


def _directory_exists_for_feature_status(path: Path, reason: str) -> tuple[bool | None, str]:
    try:
        return path.exists(), ""
    except OSError:
        return None, reason


def category_feature_status_payload(
    category_path: Path,
    context_packets_path: Path,
    wiki_path: Path,
    capture_areas: set[str],
) -> dict[str, Any]:
    """Read category feature facts without turning unavailable observations into zeroes."""
    registry, registry_reason = _read_category_registry_for_feature_status(category_path)
    if registry_reason or registry is None or not _valid_category_registry_for_feature_status(registry, capture_areas):
        reason = registry_reason or "category_registry_invalid"
        return {"available": False, "categories": {"available": False, "approved": None, "proposed": None}, "contextPackets": None, "hasWiki": None, "health": _feature_status_health("unknown", reason, "Category registry facts are not available for read-only status.")}
    context_packets, context_reason = _markdown_directory_count_for_feature_status(context_packets_path, "context_packets_unreadable")
    has_wiki, wiki_reason = _directory_exists_for_feature_status(wiki_path, "wiki_unreadable")
    reason = context_reason or wiki_reason
    available = not reason
    return {
        "available": available,
        "categories": {"available": True, "approved": sum(item["status"] == "approved" for item in registry["categories"]), "proposed": sum(item["status"] == "proposed" for item in registry["categories"])},
        "contextPackets": context_packets,
        "hasWiki": has_wiki,
        "health": _feature_status_health("observed" if available else "unknown", reason, "" if available else "One or more filesystem feature facts could not be observed."),
    }


def _identity(stat_result: os.stat_result) -> dict[str, int] | None:
    """Return filesystem identity where the platform exposes a useful pair."""
    device = getattr(stat_result, "st_dev", None)
    inode = getattr(stat_result, "st_ino", None)
    if (
        not isinstance(device, int)
        or isinstance(device, bool)
        or device < 0
        or not isinstance(inode, int)
        or isinstance(inode, bool)
        or inode <= 0
    ):
        return None
    return {"device": device, "inode": inode}


def _stat_matches(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
        and _identity(left) == _identity(right)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _fingerprint_record(stat_result: os.stat_result, digest: str) -> dict[str, Any]:
    return {
        "exists": True,
        "type": "regular",
        "identity": _identity(stat_result),
        "sizeBytes": int(stat_result.st_size),
        "mtimeNs": int(stat_result.st_mtime_ns),
        "sha256": digest,
    }


def _capture_file_observation(
    path: Path,
    label: str,
    *,
    capture_bytes: bool = False,
    maximum_bytes: int | None = None,
    retained_prefix_bytes: int | None = None,
) -> tuple[dict[str, Any] | None, bytes | None, str]:
    """Capture one stable regular file and optionally retain its exact bytes."""
    try:
        path_stat = os.stat(path, follow_symlinks=True)
    except FileNotFoundError:
        return {
            "exists": False,
            "type": "absent",
            "identity": None,
            "sizeBytes": 0,
            "mtimeNs": 0,
            "sha256": "",
        }, None, ""
    except OSError:
        return None, None, f"source_{label}_unreadable"
    if not stat.S_ISREG(path_stat.st_mode):
        return None, None, f"source_{label}_not_regular"
    if _identity(path_stat) is None:
        return None, None, f"source_{label}_identity_unavailable"
    if maximum_bytes is not None and path_stat.st_size > maximum_bytes:
        return None, None, f"source_{label}_too_large"

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        before = os.fstat(descriptor)
        if _identity(before) is None:
            return None, None, f"source_{label}_identity_unavailable"
        if not _stat_matches(path_stat, before):
            return None, None, f"source_{label}_path_replaced"
        digest = hashlib.sha256()
        raw_bytes = bytearray() if capture_bytes else None
        while True:
            chunk = os.read(descriptor, _HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            if raw_bytes is not None:
                if retained_prefix_bytes is None:
                    raw_bytes.extend(chunk)
                    if maximum_bytes is not None and len(raw_bytes) > maximum_bytes:
                        return None, None, f"source_{label}_too_large"
                elif len(raw_bytes) < retained_prefix_bytes:
                    raw_bytes.extend(chunk[:retained_prefix_bytes - len(raw_bytes)])
        after = os.fstat(descriptor)
        if _identity(after) is None:
            return None, None, f"source_{label}_identity_unavailable"
        if not _stat_matches(before, after):
            return None, None, f"source_{label}_changed_during_capture"
        try:
            current = os.stat(path, follow_symlinks=True)
        except FileNotFoundError:
            return None, None, f"source_{label}_path_replaced"
        except OSError:
            return None, None, f"source_{label}_unreadable"
        if _identity(current) is None:
            return None, None, f"source_{label}_identity_unavailable"
        if not _stat_matches(before, current):
            return None, None, f"source_{label}_path_replaced"
        return _fingerprint_record(after, digest.hexdigest()), bytes(raw_bytes) if raw_bytes is not None else None, ""
    except OSError:
        return None, None, f"source_{label}_unreadable"
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _capture_file(path: Path, label: str) -> tuple[dict[str, Any] | None, str]:
    """Capture one stable path from an FD.  Never leak observer exceptions."""
    record, _raw_bytes, reason = _capture_file_observation(path, label)
    return record, reason


def _capture_file_bytes(
    path: Path,
    label: str,
    *,
    maximum_bytes: int,
) -> tuple[dict[str, Any] | None, bytes | None, str]:
    return _capture_file_observation(
        path,
        label,
        capture_bytes=True,
        maximum_bytes=maximum_bytes,
    )


def _capture_file_sample(
    path: Path,
    label: str,
    *,
    sample_bytes: int,
) -> tuple[dict[str, Any] | None, bytes | None, str]:
    return _capture_file_observation(
        path,
        label,
        capture_bytes=True,
        retained_prefix_bytes=sample_bytes,
    )


def _directory_stability_record(stat_result: os.stat_result) -> dict[str, Any]:
    """Return metadata used only while guarding an absent WAL pathname."""
    return {
        "type": "directory",
        "identity": _identity(stat_result),
        "mtimeNs": int(stat_result.st_mtime_ns),
        "ctimeNs": int(getattr(stat_result, "st_ctime_ns", 0)),
    }


def _capture_directory(path: Path, label: str) -> tuple[dict[str, Any] | None, str]:
    """Capture directory identity and metadata without traversing its contents."""
    try:
        result = os.stat(path, follow_symlinks=True)
    except FileNotFoundError:
        return None, f"source_{label}_absent"
    except OSError:
        return None, f"source_{label}_unreadable"
    if not stat.S_ISDIR(result.st_mode):
        return None, f"source_{label}_not_directory"
    if _identity(result) is None:
        return None, f"source_{label}_identity_unavailable"
    return _directory_stability_record(result), ""


def capture_source_fingerprint(project: Path) -> tuple[dict[str, Any] | None, str]:
    """Return a complete stable DB/WAL capture or a deterministic reason code.

    A missing WAL is a normal SQLite state, but it is not a proof that the
    pathname stayed absent while the database was captured.  Guard it with two
    observations of both the WAL pathname and its containing directory.  An
    event that leaves neither observation changed is intentionally outside the
    observable contract.
    """
    database = _db_path(project)
    wal_path = Path(f"{database}-wal")
    try:
        database_probe = os.stat(database, follow_symlinks=True)
    except FileNotFoundError:
        return None, "source_db_absent"
    except OSError:
        return None, "source_db_unreadable"
    if not stat.S_ISREG(database_probe.st_mode):
        return None, "source_db_not_regular"
    directory_before, reason = _capture_directory(database.parent, "directory")
    if reason:
        return None, reason
    wal_before, reason = _capture_file(wal_path, "wal")
    if reason:
        return None, reason
    database_record, reason = _capture_file(database, "db")
    if reason:
        return None, reason
    # A projection can never be fresh without the database itself.  An absent
    # WAL is valid, but an absent DB is a source-state failure rather than a
    # meaningful fingerprint to compare with an old projection.
    if not database_record or not database_record.get("exists"):
        return None, "source_db_absent"
    wal_after, reason = _capture_file(wal_path, "wal")
    if reason:
        return None, reason
    directory_after, reason = _capture_directory(database.parent, "directory")
    if reason:
        return None, reason
    if wal_before != wal_after:
        return None, "source_wal_changed_during_capture"
    if directory_before != directory_after:
        return None, "source_directory_changed_during_capture"
    return {
        "algorithm": "sha256",
        "files": {
            DB_FILENAME: database_record,
            f"{DB_FILENAME}-wal": wal_after,
        },
        "sourceDirectory": {
            "type": directory_after["type"],
            "identity": directory_after["identity"],
        },
    }, ""


def source_fingerprint(project: Path) -> dict[str, Any]:
    """Compatibility helper.  Callers needing failure detail use capture_source_fingerprint."""
    fingerprint, _reason = capture_source_fingerprint(project)
    return fingerprint or {}


def _absent_metadata_record() -> dict[str, Any]:
    return {"exists": False, "type": "absent", "identity": None, "sizeBytes": 0, "mtimeNs": 0}


def _metadata_record(stat_result: os.stat_result) -> dict[str, Any]:
    kind = "regular" if stat.S_ISREG(stat_result.st_mode) else "directory"
    return {
        "exists": True,
        "type": kind,
        "identity": _identity(stat_result),
        "sizeBytes": int(stat_result.st_size),
        "mtimeNs": int(stat_result.st_mtime_ns),
    }


def _directory_stat_matches(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and _identity(left) is not None
        and _identity(left) == _identity(right)
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and getattr(left, "st_ctime_ns", 0) == getattr(right, "st_ctime_ns", 0)
    )


def _capture_metadata_path(path: Path, label: str) -> tuple[dict[str, Any] | None, str]:
    """Capture stable presence/type metadata without reading file content."""
    try:
        before = os.stat(path, follow_symlinks=True)
    except FileNotFoundError:
        return _absent_metadata_record(), ""
    except OSError:
        return None, f"source_{label}_unreadable"
    if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
        return None, f"source_{label}_unsupported_type"
    if _identity(before) is None:
        return None, f"source_{label}_identity_unavailable"

    descriptor: int | None = None
    try:
        if stat.S_ISREG(before.st_mode):
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            descriptor_before = os.fstat(descriptor)
            if _identity(descriptor_before) is None:
                return None, f"source_{label}_identity_unavailable"
            if not _stat_matches(before, descriptor_before):
                return None, f"source_{label}_path_replaced"
            descriptor_after = os.fstat(descriptor)
            if not _stat_matches(descriptor_before, descriptor_after):
                return None, f"source_{label}_changed_during_capture"
            stable = descriptor_after
        else:
            stable = before
        try:
            current = os.stat(path, follow_symlinks=True)
        except FileNotFoundError:
            return None, f"source_{label}_path_replaced"
        except OSError:
            return None, f"source_{label}_unreadable"
        if _identity(current) is None:
            return None, f"source_{label}_identity_unavailable"
        matches = _stat_matches(stable, current) if stat.S_ISREG(stable.st_mode) else _directory_stat_matches(stable, current)
        if not matches:
            return None, f"source_{label}_path_replaced"
        return _metadata_record(current), ""
    except OSError:
        return None, f"source_{label}_unreadable"
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def capture_regular_file_bytes(path: Path, label: str) -> tuple[bytes | None, dict[str, Any] | None, str]:
    """Capture exact bytes from one stable regular file using observer reasons."""
    record, raw, reason = _capture_file_observation(path, label, capture_bytes=True)
    if reason:
        return None, None, reason.removeprefix("source_")
    if record is None or not record.get("exists") or raw is None:
        return None, None, f"{label}_absent"
    return raw, record, ""


def _capture_architecture_inventory(
    project: Path,
    tracked_areas: tuple,
    skip_names: set[str],
    capture_file,
) -> tuple[list[dict[str, Any]] | None, dict[str, bytes] | None, str]:
    inventory: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    for area_index, (parts, pattern, _section) in enumerate(tracked_areas):
        root = project.joinpath(*parts)
        label = f"architecture_area_{area_index}"
        before, reason = _capture_metadata_path(root, label)
        if reason or before is None:
            return None, None, reason.removeprefix("source_")
        if not before["exists"]:
            inventory.append({"root": "/".join(parts), "exists": False, "files": []})
            continue
        if before["type"] != "directory":
            return None, None, f"{label}_not_directory"
        try:
            candidates = sorted(
                (item for item in root.iterdir() if fnmatch.fnmatch(item.name, pattern)),
                key=lambda item: item.name,
            )
        except OSError:
            return None, None, f"{label}_unreadable"
        files: list[dict[str, Any]] = []
        for file_index, filepath in enumerate(candidates):
            if filepath.name in skip_names or filepath.name.startswith("__"):
                continue
            raw, record, reason = capture_file(filepath, f"{label}_file_{file_index}")
            if reason or raw is None or record is None:
                return None, None, reason or f"{label}_unreadable"
            relative = filepath.relative_to(project).as_posix()
            files.append({"path": relative, **record})
            contents[relative] = raw
        after, reason = _capture_metadata_path(root, label)
        if reason or after is None or after != before:
            return None, None, reason.removeprefix("source_") if reason else f"{label}_unstable"
        inventory.append({"root": "/".join(parts), **after, "files": files})
    return inventory, contents, ""


def architecture_index_unavailable(reason: str) -> dict[str, Any]:
    if reason == "architecture_index_absent":
        issue = "No dev/ARCHITECTURE_INDEX.md found. Create it first."
    elif reason == "architecture_index_not_regular":
        issue = "dev/ARCHITECTURE_INDEX.md is not a regular file."
    elif reason == "architecture_index_malformed":
        issue = (
            "dev/ARCHITECTURE_INDEX.md is malformed or incomplete. "
            "A title, scoped sections, and valid File tables are required."
        )
    elif reason.endswith(("_unstable", "_path_replaced", "_changed_during_capture")):
        issue = "Architecture index inputs changed while coverage was being observed."
    else:
        issue = "Architecture index inputs could not be observed safely."
    return {
        "ok": False,
        "available": False,
        "path": "dev/ARCHITECTURE_INDEX.md",
        "missingCount": None,
        "missing": None,
        "issues": [issue],
    }


def _architecture_inline_code_tokens(value: str) -> list[str]:
    """Return closed single-backtick tokens from one Markdown line."""
    parts = value.split("`")
    if len(parts) % 2 == 0:
        return []
    return [parts[index] for index in range(1, len(parts), 2)]


def _architecture_table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = [cell.strip() for cell in stripped[1:-1].split("|")]
    return cells if len(cells) >= 2 else None


def _architecture_table_separator(cells: list[str], column_count: int) -> bool:
    if len(cells) != column_count:
        return False
    for cell in cells:
        marker = cell.removeprefix(":").removesuffix(":")
        if len(marker) < 3 or set(marker) != {"-"}:
            return False
    return True


def _architecture_entry_token(cell: str) -> str | None:
    """Return one safe path token from a File table's first cell."""
    if len(cell) < 3 or not cell.startswith("`") or not cell.endswith("`"):
        return None
    if cell.count("`") != 2:
        return None
    token = cell[1:-1]
    if not token or token != token.strip() or "\\" in token or ":" in token:
        return None
    token = token.removeprefix("./")
    path = PurePosixPath(token)
    if path.is_absolute() or not path.parts or ".." in path.parts or token.endswith("/"):
        return None
    return path.as_posix()


def _architecture_file_table_entries(lines: list[str]) -> tuple[set[str], bool] | None:
    """Parse valid File tables in one H2 section without accepting prose hits."""
    entries: set[str] = set()
    found_table = False
    line_index = 0
    while line_index < len(lines):
        header = _architecture_table_cells(lines[line_index])
        if header is None or header[0].casefold() != "file":
            line_index += 1
            continue
        found_table = True
        if line_index + 1 >= len(lines):
            return None
        separator = _architecture_table_cells(lines[line_index + 1])
        if separator is None or not _architecture_table_separator(separator, len(header)):
            return None
        line_index += 2
        row_count = 0
        while line_index < len(lines):
            row = _architecture_table_cells(lines[line_index])
            if row is None:
                break
            generated_stub = (
                len(row) == len(header) + 1
                and len(row) > 1
                and row[1] == "[NEEDS_DESCRIPTION]"
            )
            if len(row) != len(header) and not generated_stub:
                return None
            token = _architecture_entry_token(row[0])
            if token is None:
                return None
            entries.add(token)
            row_count += 1
            line_index += 1
        if row_count == 0:
            return None
    return entries, found_table


def _architecture_scope_matches(root: str, token: str) -> bool:
    if "\\" in token or ":" in token:
        return False
    normalized = token.removeprefix("./").rstrip("/")
    if not normalized:
        return False
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return False
    normalized = path.as_posix()
    return normalized == root or normalized.startswith(f"{root}/")


def _architecture_index_entries(
    index_content: str,
    inventory: list[dict[str, Any]],
) -> dict[str, set[str]] | None:
    """Validate the index shape and resolve scoped File entries to project paths."""
    lines = index_content.splitlines()
    first_content = next((line.lstrip("\ufeff") for line in lines if line.strip()), "")
    if not first_content.startswith("# ") or "architecture index" not in first_content[2:].casefold():
        return None

    sections: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            if current_heading is not None:
                sections.append((current_heading, current_lines))
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)
    if current_heading is not None:
        sections.append((current_heading, current_lines))
    if not sections:
        return None

    resolved: dict[str, set[str]] = {}
    for area in inventory:
        files = area.get("files", [])
        if not files:
            continue
        root = str(area["root"])
        scoped_sections = [
            (heading, section_lines)
            for heading, section_lines in sections
            if any(
                _architecture_scope_matches(root, token)
                for token in _architecture_inline_code_tokens(heading)
            )
        ]
        if not scoped_sections:
            return None
        area_entries: set[str] = set()
        found_table = False
        for heading, section_lines in scoped_sections:
            for scope_token in _architecture_inline_code_tokens(heading):
                if not _architecture_scope_matches(root, scope_token):
                    continue
                normalized_scope = PurePosixPath(
                    scope_token.removeprefix("./").rstrip("/"),
                ).as_posix()
                if not scope_token.endswith("/") and normalized_scope != root:
                    area_entries.add(normalized_scope)
            parsed = _architecture_file_table_entries(section_lines)
            if parsed is None:
                return None
            tokens, section_has_table = parsed
            found_table = found_table or section_has_table
            for token in tokens:
                if token == root or token.startswith(f"{root}/"):
                    area_entries.add(token)
                else:
                    area_entries.add(f"{root}/{token}")
        if not found_table:
            return None
        resolved[root] = area_entries
    return resolved


def observed_architecture_index_payload(
    project: Path,
    tracked_areas: tuple,
    skip_names: set[str],
    describe_file,
    capture_file=capture_regular_file_bytes,
) -> dict[str, Any]:
    """Build index coverage only from stable bytes and inventories."""
    index_path = project / "dev" / "ARCHITECTURE_INDEX.md"
    index_raw, index_record, reason = capture_file(index_path, "architecture_index")
    if reason or index_raw is None or index_record is None:
        return architecture_index_unavailable(reason or "architecture_index_unreadable")
    try:
        index_content = index_raw.decode("utf-8")
    except UnicodeDecodeError:
        return architecture_index_unavailable("architecture_index_unreadable")
    inventory, contents, reason = _capture_architecture_inventory(
        project, tracked_areas, skip_names, capture_file,
    )
    if reason or inventory is None or contents is None:
        return architecture_index_unavailable(reason or "architecture_inventory_unreadable")
    index_after_raw, index_after_record, reason = capture_file(index_path, "architecture_index")
    if reason or index_after_raw != index_raw or index_after_record != index_record:
        return architecture_index_unavailable("architecture_index_unstable")
    inventory_after, _contents_after, reason = _capture_architecture_inventory(
        project, tracked_areas, skip_names, capture_file,
    )
    if reason or inventory_after != inventory:
        return architecture_index_unavailable("architecture_inventory_unstable")
    indexed_paths = _architecture_index_entries(index_content, inventory)
    if indexed_paths is None:
        return architecture_index_unavailable("architecture_index_malformed")
    missing: list[dict[str, str]] = []
    for area, (_parts, _pattern, section) in zip(inventory, tracked_areas):
        area_entries = indexed_paths.get(area["root"], set())
        for record in area["files"]:
            if record["path"] not in area_entries:
                filepath = project / record["path"]
                missing.append({
                    "name": filepath.name,
                    "section": section,
                    "description": describe_file(
                        filepath,
                        contents[record["path"]].decode("utf-8", errors="replace"),
                    ),
                })
    return {
        "ok": not missing,
        "available": True,
        "path": "dev/ARCHITECTURE_INDEX.md",
        "missingCount": len(missing),
        "missing": missing,
        "issues": [],
    }


def latest_stable_markdown_file(root: Path, label: str) -> Path | None:
    """Return the latest matching regular file, or fail on unobservable input."""
    before, reason = _capture_metadata_path(root, label)
    if reason or before is None:
        raise RuntimeError(f"{label} inventory is unavailable: {reason}")
    if not before["exists"]:
        after, after_reason = _capture_metadata_path(root, label)
        if after_reason or after != before:
            raise RuntimeError(f"{label} inventory is unstable")
        return None
    if before["type"] != "directory":
        raise RuntimeError(f"{label} root is not a directory")
    try:
        candidates = sorted(item for item in root.iterdir() if fnmatch.fnmatch(item.name, "*.md"))
    except OSError as exc:
        raise RuntimeError(f"{label} inventory is unavailable: {exc}") from exc
    for index, candidate in enumerate(candidates):
        record, reason = _capture_file(candidate, f"{label}_{index}")
        if reason or record is None or not record.get("exists"):
            raise RuntimeError(f"{label} input is unavailable: {reason or 'path_replaced'}")
    after, reason = _capture_metadata_path(root, label)
    if reason or after != before:
        raise RuntimeError(f"{label} inventory is unstable")
    return candidates[-1] if candidates else None


def _filesystem_entry(
    project: Path,
    path: Path,
    record: dict[str, Any],
    *,
    digest: str | None,
) -> dict[str, Any]:
    return {
        "path": _relative_path(project, path),
        "type": record["type"],
        "identity": record["identity"],
        "sizeBytes": record["sizeBytes"],
        "mtimeNs": record["mtimeNs"],
        "sha256": digest,
    }


def _capture_inventory(
    project: Path,
    root: Path,
    label: str,
    *,
    mode: str,
) -> tuple[dict[str, Any] | None, str]:
    """Capture the exact immediate entries consumed by bootstrap formatters."""
    try:
        before = os.stat(root, follow_symlinks=True)
    except FileNotFoundError:
        return {
            "exists": False,
            "type": "absent",
            "identity": None,
            "mode": mode,
            "entries": [],
            "graphPacketPaths": [],
        }, ""
    except OSError:
        return None, f"source_{label}_unreadable"
    if _identity(before) is None:
        return None, f"source_{label}_identity_unavailable"
    if stat.S_ISREG(before.st_mode):
        return None, f"source_{label}_not_directory"
    if not stat.S_ISDIR(before.st_mode):
        return None, f"source_{label}_unsupported_type"

    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return None, f"source_{label}_unreadable"
    entries: list[dict[str, Any]] = []
    graph_packet_paths: list[str] = []
    for index, child in enumerate(children):
        if mode != "project_area" and child.suffix.lower() != ".md":
            continue
        if mode == "project_area" and child.name == ".gitkeep":
            continue
        entry_label = f"{label}_entry_{index}"
        metadata, reason = _capture_metadata_path(child, entry_label)
        if reason or metadata is None:
            return None, reason
        if not metadata["exists"]:
            return None, f"source_{entry_label}_path_replaced"
        # A markdown-named wrong-type entry is not an observable zero.  The
        # status readers consume only regular markdown files, so fail closed.
        if mode != "project_area" and metadata["type"] != "regular":
            return None, f"source_{entry_label}_not_regular"
        digest: str | None = None
        if mode in {"dev_context_packets", "project_context_packets"} and metadata["type"] == "regular":
            if mode == "dev_context_packets":
                content_record, sample, reason = _capture_file_sample(
                    child,
                    entry_label,
                    sample_bytes=4096,
                )
            else:
                content_record, reason = _capture_file(child, entry_label)
                sample = None
            if reason or content_record is None:
                return None, reason
            metadata = {key: content_record[key] for key in ("exists", "type", "identity", "sizeBytes", "mtimeNs")}
            digest = str(content_record["sha256"])
            if mode == "dev_context_packets" and "ControlCoding GraphRAG Packet" in (
                sample or b""
            ).decode("utf-8", errors="replace")[:512]:
                graph_packet_paths.append(_relative_path(project, child))
        entries.append(_filesystem_entry(project, child, metadata, digest=digest))
    try:
        after = os.stat(root, follow_symlinks=True)
    except FileNotFoundError:
        return None, f"source_{label}_path_replaced"
    except OSError:
        return None, f"source_{label}_unreadable"
    if _identity(after) is None:
        return None, f"source_{label}_identity_unavailable"
    if not _directory_stat_matches(before, after):
        return None, f"source_{label}_changed_during_capture"
    return {
        "exists": True,
        "type": "directory",
        "identity": _identity(after),
        "mode": mode,
        "entries": entries,
        "graphPacketPaths": graph_packet_paths,
    }, ""


def _valid_controlwork_config_payload(payload: Any) -> bool:
    """Validate the shared v1 ControlWork config facts consumed by status."""
    if not isinstance(payload, dict):
        return False
    if (
        not _is_int(payload.get("schemaVersion"))
        or payload["schemaVersion"] != 1
        or payload.get("product") != "ControlWork"
        or payload.get("distribution") not in _CONTROLWORK_DISTRIBUTIONS
        or payload.get("canonicalContext") != _CONTROLWORK_CONTEXT_FILENAME
    ):
        return False
    distribution = payload["distribution"]
    if distribution == "embedded_controlcoding":
        if not isinstance(payload.get("standaloneCompatible"), bool):
            return False
    elif not isinstance(payload.get("embeddedCompatible"), bool):
        return False
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        return False
    if (
        memory.get("root") != f"{_CONTROLWORK_DIRNAME}/memory"
        or memory.get("areas") != list(_CONTROLWORK_MEMORY_AREAS)
        or memory.get("lifecycles") != list(_CONTROLWORK_LIFECYCLES)
    ):
        return False
    features = payload.get("features")
    expected_features = {
        "categories", "checkpoints", "views", "obsidianProjection", "mcpReadOnly", "contextPackets",
    }
    return isinstance(features, dict) and all(
        isinstance(features.get(name), bool) for name in expected_features
    )


def _absent_controlwork_config_facts() -> dict[str, Any]:
    return {
        "exists": False,
        "schemaVersion": None,
        "product": None,
        "distribution": None,
        "standaloneCompatible": None,
        "canonicalContext": None,
    }


def _capture_controlwork_config(
    path: Path,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """Hash, parse, and validate config from one stable FD byte capture."""
    record, raw_bytes, reason = _capture_file_bytes(
        path,
        label,
        maximum_bytes=_OBSERVER_JSON_MAX_BYTES,
    )
    if reason or record is None:
        if reason.endswith("_too_large"):
            reason = "filesystem_project_config_too_large"
        return None, None, reason
    if not record["exists"]:
        return record, _absent_controlwork_config_facts(), ""
    try:
        payload = _strict_json_loads((raw_bytes or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None, None, "filesystem_project_config_malformed"
    if not isinstance(payload, dict):
        return None, None, "filesystem_project_config_malformed"
    if not _valid_controlwork_config_payload(payload):
        return None, None, "filesystem_project_config_incompatible"
    return record, {
        "exists": True,
        "schemaVersion": payload["schemaVersion"],
        "product": payload["product"],
        "distribution": payload["distribution"],
        "standaloneCompatible": bool(payload.get("standaloneCompatible")),
        "canonicalContext": payload["canonicalContext"],
    }, ""


def _capture_optional_filesystem_json(
    path: Path,
    label: str,
    *,
    parser: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """Capture and parse optional JSON from the same stable file bytes."""
    record, raw_bytes, reason = _capture_file_bytes(
        path,
        maximum_bytes=_OBSERVER_JSON_MAX_BYTES,
        label=f"filesystem_{label}",
    )
    if reason or record is None:
        return None, None, reason
    if not record["exists"]:
        return record, {}, ""
    try:
        payload = parser((raw_bytes or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None, None, f"filesystem_{label}_malformed"
    if not isinstance(payload, dict):
        return None, None, f"filesystem_{label}_malformed"
    return record, payload, ""


def _capture_content_file(path: Path, label: str) -> tuple[dict[str, Any] | None, str]:
    """Compatibility wrapper for callers that only need a stable file record."""
    return _capture_file(path, label)


def capture_filesystem_fingerprint(project: Path) -> tuple[dict[str, Any] | None, str]:
    """Capture every filesystem input used by ``build_legacy_status_snapshot``.

    Content-derived inputs are hashed.  Count-only Project Plane areas carry
    only deterministic immediate inventory metadata and are never opened for
    content reads.
    """
    project = project.resolve()
    root_before, reason = _capture_directory(project, "filesystem_project_directory")
    if reason or root_before is None:
        return None, reason

    controlwork_root = project / _CONTROLWORK_DIRNAME
    controlwork_root_before, reason = _capture_metadata_path(
        controlwork_root,
        "filesystem_controlwork_root",
    )
    if reason or controlwork_root_before is None:
        return None, reason
    if controlwork_root_before["exists"] and controlwork_root_before["type"] != "directory":
        return None, "source_filesystem_controlwork_root_not_directory"

    manifest_record, manifest, reason = _capture_optional_filesystem_json(
        project / CONTROL_DIRNAME / MANIFEST_FILENAME,
        "manifest",
        parser=json.loads,
    )
    if reason or manifest_record is None or manifest is None:
        return None, reason
    if manifest_record["exists"] and not _valid_manifest(manifest):
        return None, "filesystem_manifest_incompatible"
    link_record, link, reason = _capture_optional_filesystem_json(
        project / _CONTROLWORK_DIRNAME / "link.json",
        "project_link",
        parser=json.loads,
    )
    if reason or link_record is None or link is None:
        return None, reason
    if link_record["exists"] and not _valid_controlwork_link_facts(link):
        return None, "filesystem_project_link_incompatible"
    content_files: dict[str, Any] = {
        f"{CONTROL_DIRNAME}/{MANIFEST_FILENAME}": manifest_record,
        f"{_CONTROLWORK_DIRNAME}/link.json": link_record,
    }

    config_record, config_facts, reason = _capture_controlwork_config(
        controlwork_root / "config.json",
        "filesystem_project_config",
    )
    if reason or config_record is None or config_facts is None:
        return None, reason
    content_files[f"{_CONTROLWORK_DIRNAME}/config.json"] = config_record

    canonical_context_path = project / _CONTROLWORK_CONTEXT_FILENAME
    canonical_context, reason = _capture_metadata_path(
        canonical_context_path,
        "filesystem_project_context",
    )
    if reason or canonical_context is None:
        return None, reason
    if canonical_context["exists"] and canonical_context["type"] != "regular":
        return None, "source_filesystem_project_context_not_regular"

    inventory_specs: list[tuple[str, Path, str, str]] = [
        (f"{CONTROL_DIRNAME}/views", project / CONTROL_DIRNAME / "views", "filesystem_dev_views", "markdown_metadata"),
        (f"{CONTROL_DIRNAME}/context-packets", project / CONTROL_DIRNAME / "context-packets", "filesystem_dev_context_packets", "dev_context_packets"),
        (f"{_CONTROLWORK_DIRNAME}/context-packets", project / _CONTROLWORK_DIRNAME / "context-packets", "filesystem_project_context_packets", "project_context_packets"),
    ]
    inventory_specs.extend(
        (
            f"{_CONTROLWORK_DIRNAME}/memory/{area}",
            project / _CONTROLWORK_DIRNAME / "memory" / area,
            f"filesystem_project_area_{area}",
            "project_area",
        )
        for area in _CONTROLWORK_MEMORY_AREAS
    )
    inventories: dict[str, Any] = {}
    for key, path, label, mode in inventory_specs:
        inventory, reason = _capture_inventory(project, path, label, mode=mode)
        if reason or inventory is None:
            return None, reason
        inventories[key] = inventory

    external_attachment: dict[str, Any] = {
        "configured": False,
        "root": "",
        "rootRecord": _absent_metadata_record(),
        "paths": {},
    }
    if link:
        raw_external = link.get("externalPath")
        if not isinstance(raw_external, str) or not raw_external.strip():
            return None, "filesystem_attachment_invalid"
        try:
            external_root = Path(raw_external).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None, "filesystem_attachment_unverifiable"
        external_root_record, reason = _capture_metadata_path(external_root, "filesystem_attachment_root")
        if reason or external_root_record is None:
            return None, reason
        if not external_root_record["exists"] or external_root_record["type"] != "directory":
            return None, "filesystem_attachment_unverifiable"
        external_paths = {
            _CONTROLWORK_CONTEXT_FILENAME: external_root / _CONTROLWORK_CONTEXT_FILENAME,
            f"{_CONTROLWORK_DIRNAME}/config.json": external_root / _CONTROLWORK_DIRNAME / "config.json",
            f"{_CONTROLWORK_DIRNAME}/memory": external_root / _CONTROLWORK_DIRNAME / "memory",
        }
        external_records: dict[str, Any] = {}
        for index, (key, path) in enumerate(external_paths.items()):
            record, reason = _capture_metadata_path(path, f"filesystem_attachment_path_{index}")
            if reason or record is None:
                return None, reason
            expected_type = "directory" if key.endswith("/memory") else "regular"
            if record["exists"] and record["type"] != expected_type:
                return None, "filesystem_attachment_unverifiable"
            external_records[key] = record
        external_attachment = {
            "configured": True,
            "root": str(external_root),
            "rootRecord": external_root_record,
            "paths": external_records,
        }

    root_after, reason = _capture_directory(project, "filesystem_project_directory")
    if reason or root_after is None:
        return None, reason
    controlwork_root_after, reason = _capture_metadata_path(
        controlwork_root,
        "filesystem_controlwork_root",
    )
    if reason or controlwork_root_after is None:
        return None, reason
    if controlwork_root_after["exists"] and controlwork_root_after["type"] != "directory":
        return None, "source_filesystem_controlwork_root_not_directory"
    if root_before != root_after:
        return None, "source_filesystem_project_directory_changed_during_capture"
    if controlwork_root_before != controlwork_root_after:
        return None, "source_filesystem_controlwork_root_changed_during_capture"
    return {
        "schemaVersion": FILESYSTEM_FINGERPRINT_SCHEMA_VERSION,
        "algorithm": "sha256",
        "projectDirectory": {"type": root_after["type"], "identity": root_after["identity"]},
        "controlWorkRoot": controlwork_root_after,
        "contentFiles": content_files,
        "manifestFacts": manifest,
        "linkFacts": link,
        "configFacts": config_facts,
        "presencePaths": {_CONTROLWORK_CONTEXT_FILENAME: canonical_context},
        "inventories": inventories,
        "externalAttachment": external_attachment,
    }, ""


def filesystem_fingerprint(project: Path) -> dict[str, Any]:
    fingerprint, _reason = capture_filesystem_fingerprint(project)
    return fingerprint or {}


def _project_plane_observation(available: bool, state: str, reason: str, message: str) -> dict[str, Any]:
    return {"available": available, "state": state, "reason": reason, "message": message}


def _project_reason_from_source(reason: str, label: str) -> str:
    if reason.endswith("_not_regular") or reason.endswith("_not_directory") or reason.endswith("_unsupported_type"):
        return f"project_plane_{label}_wrong_type"
    if reason.endswith("_identity_unavailable") or reason.endswith("_unreadable"):
        return f"project_plane_{label}_unreadable"
    return f"project_plane_{label}_unreadable"


def _capture_project_json_source(
    path: Path,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    record, reason = _capture_file(path, f"project_plane_{label}")
    if reason or record is None:
        return None, None, _project_reason_from_source(reason, label)
    if not record["exists"]:
        return record, {}, ""
    payload, reason = _read_bounded_json_object(
        path,
        maximum_bytes=_OBSERVER_JSON_MAX_BYTES,
        unreadable_reason=f"project_plane_{label}_unreadable",
        malformed_reason=f"project_plane_{label}_malformed",
        too_large_reason=f"project_plane_{label}_malformed",
    )
    if reason or payload is None:
        return None, None, reason
    after, reason = _capture_file(path, f"project_plane_{label}")
    if reason or after is None:
        return None, None, _project_reason_from_source(reason, label)
    if record != after:
        return None, None, f"project_plane_{label}_changed_during_capture"
    return after, payload, ""


def _capture_project_text_source(path: Path, label: str) -> tuple[dict[str, Any] | None, str]:
    record, reason = _capture_file(path, f"project_plane_{label}")
    if reason or record is None:
        return None, _project_reason_from_source(reason, label)
    if not record["exists"]:
        return record, ""
    try:
        path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None, f"project_plane_{label}_unreadable"
    after, reason = _capture_file(path, f"project_plane_{label}")
    if reason or after is None:
        return None, _project_reason_from_source(reason, label)
    if record != after:
        return None, f"project_plane_{label}_changed_during_capture"
    return after, ""


def _capture_recursive_project_memory(project: Path, label_prefix: str) -> tuple[dict[str, Any] | None, str]:
    result: dict[str, Any] = {}
    for area in _CONTROLWORK_MEMORY_AREAS:
        if area == "views":
            continue
        root = project / _CONTROLWORK_DIRNAME / "memory" / area
        before, reason = _capture_metadata_path(root, f"project_plane_{label_prefix}_{area}")
        if reason or before is None:
            return None, _project_reason_from_source(reason, f"{label_prefix}_memory")
        if not before["exists"]:
            result[area] = {"root": before, "entries": []}
            continue
        if before["type"] != "directory":
            return None, f"project_plane_{label_prefix}_memory_wrong_type"
        try:
            candidates = sorted(root.rglob("*"), key=lambda item: item.relative_to(project).as_posix())
        except OSError:
            return None, f"project_plane_{label_prefix}_memory_unreadable"
        entries: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            entry_label = f"project_plane_{label_prefix}_{area}_entry_{index}"
            metadata, entry_reason = _capture_metadata_path(candidate, entry_label)
            if entry_reason or metadata is None:
                return None, f"project_plane_{label_prefix}_memory_unreadable"
            if not metadata["exists"]:
                return None, f"project_plane_{label_prefix}_memory_changed_during_capture"
            if metadata["type"] == "regular":
                if candidate.name == ".gitkeep":
                    continue
                content, entry_reason = _capture_file(candidate, entry_label)
                if entry_reason or content is None:
                    return None, f"project_plane_{label_prefix}_memory_unreadable"
                entries.append(_filesystem_entry(project, candidate, content, digest=str(content["sha256"])))
            elif metadata["type"] == "directory":
                entries.append(_filesystem_entry(project, candidate, metadata, digest=None))
            else:
                return None, f"project_plane_{label_prefix}_memory_wrong_type"
        after, reason = _capture_metadata_path(root, f"project_plane_{label_prefix}_{area}")
        if reason or after is None:
            return None, f"project_plane_{label_prefix}_memory_unreadable"
        if before != after:
            return None, f"project_plane_{label_prefix}_memory_changed_during_capture"
        result[area] = {"root": after, "entries": entries}
    return result, ""


def _configured_base_document(config: dict[str, Any]) -> str:
    candidates = ("PROJECT.md", "README.md", "Project.md", "project.md", "docs/PROJECT.md", "docs/project.md")
    values: list[Any] = [config.get(key) for key in ("baseDocument", "projectDocument", "projectBaseDocument")]
    project_section = config.get("project")
    if isinstance(project_section, dict):
        values.extend((project_section.get("baseDocumentPath"), project_section.get("documentPath")))
    for value in values:
        if isinstance(value, str) and value.strip():
            normalized = value.replace("\\", "/").strip().strip("/")
            if not normalized or normalized == ".." or normalized.startswith("../") or "/../" in normalized:
                return "PROJECT.md"
            if normalized.startswith(f"{_CONTROLWORK_DIRNAME}/") or normalized == _CONTROLWORK_CONTEXT_FILENAME:
                return "PROJECT.md"
            return normalized
    return ""


def _capture_project_plane_instance(
    project: Path,
    label_prefix: str,
    *,
    embedded: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    root = project / _CONTROLWORK_DIRNAME
    root_record, reason = _capture_metadata_path(root, f"project_plane_{label_prefix}_root")
    if reason or root_record is None:
        return None, None, f"project_plane_{label_prefix}_root_unreadable"
    if root_record["exists"] and root_record["type"] != "directory":
        return None, None, f"project_plane_{label_prefix}_root_wrong_type"
    memory_root_record, reason = _capture_metadata_path(
        root / "memory",
        f"project_plane_{label_prefix}_memory_root",
    )
    if reason or memory_root_record is None:
        return None, None, f"project_plane_{label_prefix}_memory_unreadable"
    if memory_root_record["exists"] and memory_root_record["type"] != "directory":
        return None, None, f"project_plane_{label_prefix}_memory_wrong_type"

    context, reason = _capture_project_text_source(project / _CONTROLWORK_CONTEXT_FILENAME, f"{label_prefix}_context")
    if reason or context is None:
        return None, None, reason
    config_record, config, reason = _capture_project_json_source(root / "config.json", f"{label_prefix}_config")
    if reason or config_record is None or config is None:
        return None, None, reason
    if config_record["exists"]:
        distribution = config.get("distribution")
        compatibility_key = {
            "embedded_controlcoding": "standaloneCompatible",
            "standalone_repo": "embeddedCompatible",
        }.get(distribution)
        if (
            compatibility_key is None
            or not isinstance(config.get(compatibility_key), bool)
        ):
            return None, None, f"project_plane_{label_prefix}_config_malformed"
    categories_record, categories, reason = _capture_project_json_source(root / "categories.json", f"{label_prefix}_categories")
    if reason or categories_record is None or categories is None:
        return None, None, reason
    if categories_record["exists"] and not _valid_category_registry_for_feature_status(
        categories, set(_CONTROLWORK_MEMORY_AREAS) - {"views"},
    ):
        return None, None, f"project_plane_{label_prefix}_categories_malformed"
    memory, reason = _capture_recursive_project_memory(project, label_prefix)
    if reason or memory is None:
        return None, None, reason

    payload: dict[str, Any] = {
        "root": root_record,
        "memoryRoot": memory_root_record,
        "context": context,
        "config": config_record,
        "categories": categories_record,
        "memoryContent": memory,
    }
    if not embedded:
        return payload, config, ""

    understanding_record, understanding, reason = _capture_project_json_source(
        root / "ingestion" / "project-understanding.json",
        f"{label_prefix}_project_understanding",
    )
    if reason or understanding_record is None or understanding is None:
        return None, None, reason
    if understanding_record["exists"] and (
        understanding.get("schemaVersion") != "controlwork-project-understanding/v1"
        or any(key in understanding and not isinstance(understanding[key], str) for key in ("status", "selectedKind", "primaryPurpose"))
        or any(key in understanding and not isinstance(understanding[key], list) for key in ("questions", "blockedUntilApproval"))
    ):
        return None, None, f"project_plane_{label_prefix}_project_understanding_malformed"

    candidate_paths = ["PROJECT.md", "README.md", "Project.md", "project.md", "docs/PROJECT.md", "docs/project.md"]
    configured = _configured_base_document(config)
    if configured and configured not in candidate_paths:
        candidate_paths.append(configured)
    base_documents: dict[str, Any] = {}
    project_root = project.resolve()
    for index, relative in enumerate(candidate_paths):
        try:
            target = (project / relative).resolve()
            target.relative_to(project_root)
        except (OSError, RuntimeError, ValueError):
            return None, None, f"project_plane_{label_prefix}_base_document_unverifiable"
        record, capture_reason = _capture_metadata_path(target, f"project_plane_{label_prefix}_base_{index}")
        if capture_reason or record is None:
            return None, None, f"project_plane_{label_prefix}_base_document_unreadable"
        if record["exists"] and record["type"] != "regular":
            return None, None, f"project_plane_{label_prefix}_base_document_wrong_type"
        base_documents[relative] = record

    context_packets, reason = _capture_inventory(
        project,
        root / "context-packets",
        f"project_plane_{label_prefix}_context_packets",
        mode="markdown_metadata",
    )
    if reason or context_packets is None:
        return None, None, _project_reason_from_source(reason, f"{label_prefix}_context_packets")
    views, reason = _capture_inventory(
        project,
        root / "memory" / "views",
        f"project_plane_{label_prefix}_views",
        mode="project_area",
    )
    if reason or views is None:
        return None, None, _project_reason_from_source(reason, f"{label_prefix}_views")
    if any(
        PurePosixPath(entry["path"]).name in _CONTROLWORK_EXPECTED_VIEW_FILENAMES
        and entry["type"] != "regular"
        for entry in views["entries"]
    ):
        return None, None, f"project_plane_{label_prefix}_views_wrong_type"
    handoff, reason = _capture_file(
        root / "memory" / "views" / "handoff-packet.md",
        f"project_plane_{label_prefix}_handoff",
    )
    if reason or handoff is None:
        return None, None, _project_reason_from_source(reason, f"{label_prefix}_handoff")
    checkpoints, reason = _capture_inventory(
        project,
        root / "checkpoints",
        f"project_plane_{label_prefix}_checkpoints",
        mode="project_context_packets",
    )
    if reason or checkpoints is None:
        return None, None, _project_reason_from_source(reason, f"{label_prefix}_checkpoints")
    wiki, reason = _capture_metadata_path(project / "wiki", f"project_plane_{label_prefix}_wiki")
    if reason or wiki is None:
        return None, None, f"project_plane_{label_prefix}_wiki_unreadable"
    if wiki["exists"] and wiki["type"] != "directory":
        return None, None, f"project_plane_{label_prefix}_wiki_wrong_type"
    payload.update({
        "projectUnderstanding": understanding_record,
        "baseDocuments": base_documents,
        "contextPackets": context_packets,
        "views": views,
        "handoff": handoff,
        "checkpoints": checkpoints,
        "wiki": wiki,
    })
    return payload, config, ""


def capture_project_plane_inputs(project: Path) -> tuple[dict[str, Any] | None, str]:
    """Capture the full filesystem surface consumed by the legacy work-status adapter."""
    try:
        project = project.resolve()
    except (OSError, RuntimeError):
        return None, "project_plane_root_unreadable"
    boundary_before, reason = _capture_directory(project, "project_plane_project_directory")
    if reason or boundary_before is None:
        return None, "project_plane_root_unreadable"
    embedded, _config, reason = _capture_project_plane_instance(project, "embedded", embedded=True)
    if reason or embedded is None:
        return None, reason.replace("project_plane_embedded_", "project_plane_", 1)
    link_record, link, reason = _capture_project_json_source(
        project / _CONTROLWORK_DIRNAME / "link.json",
        "link",
    )
    if reason or link_record is None or link is None:
        return None, reason
    if any(key in link and not isinstance(link[key], str) for key in ("externalPath", "syncPolicy", "attachedAt")):
        return None, "project_plane_link_malformed"
    external: dict[str, Any] | None = None
    external_root = ""
    external_project_directory = _absent_metadata_record()
    if link_record["exists"]:
        external_value = link.get("externalPath")
        if not isinstance(external_value, str) or not external_value.strip():
            return None, "project_plane_link_malformed"
        try:
            resolved_external = Path(external_value).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None, "project_plane_attachment_unverifiable"
        external_root = str(resolved_external)
        external_record, capture_reason = _capture_metadata_path(resolved_external, "project_plane_attachment_root")
        if capture_reason or external_record is None or not external_record["exists"] or external_record["type"] != "directory":
            return None, "project_plane_attachment_unverifiable"
        external_project_directory = external_record
        external, _external_config, reason = _capture_project_plane_instance(
            resolved_external,
            "external",
            embedded=False,
        )
        if reason or external is None:
            return None, "project_plane_attachment_unverifiable" if reason.endswith(("_root_unreadable", "_root_wrong_type")) else reason
    boundary_after, reason = _capture_directory(project, "project_plane_project_directory")
    if reason or boundary_after is None:
        return None, "project_plane_root_unreadable"
    if boundary_before != boundary_after:
        return None, "project_plane_inputs_changed_during_capture"
    return {
        "schemaVersion": 1,
        "projectDirectory": boundary_after,
        "embedded": embedded,
        "link": link_record,
        "externalRoot": external_root,
        "externalProjectDirectory": external_project_directory,
        "external": external,
    }, ""


def _observe_project_json(path: Path, label: str) -> tuple[dict[str, Any] | None, str]:
    try:
        path_stat = os.stat(path, follow_symlinks=True)
    except FileNotFoundError:
        return {}, ""
    except OSError:
        return None, f"project_plane_{label}_unreadable"
    if not stat.S_ISREG(path_stat.st_mode):
        return None, f"project_plane_{label}_wrong_type"
    if _identity(path_stat) is None:
        return None, f"project_plane_{label}_unreadable"
    payload, reason = _read_bounded_json_object(
        path,
        maximum_bytes=_OBSERVER_JSON_MAX_BYTES,
        unreadable_reason=f"project_plane_{label}_unreadable",
        malformed_reason=f"project_plane_{label}_malformed",
        too_large_reason=f"project_plane_{label}_malformed",
    )
    return payload, reason


def observe_project_plane_inputs(project: Path) -> dict[str, Any]:
    """Observe legacy Project Plane inputs without SQLite or filesystem writes."""
    fingerprint, reason = capture_project_plane_inputs(project)
    if reason or fingerprint is None:
        return _project_plane_observation(
            False, "unknown", reason or "project_plane_inputs_unreadable", "Project Plane inputs could not be observed safely.",
        )
    embedded = fingerprint["embedded"]
    if (
        not embedded["root"]["exists"]
        and not embedded["context"]["exists"]
        and not fingerprint["link"]["exists"]
    ):
        return _project_plane_observation(
            True, "absent", "project_plane_absent", "Project Plane inputs are observably absent.",
        )
    return _project_plane_observation(
        True, "observed", "", "Project Plane inputs were observed without SQLite or writes.",
    )


def guarded_project_plane_packet(
    project: Path,
    builder: Any,
    *,
    observer: Any = None,
    capture: Any = None,
) -> dict[str, Any]:
    """Build a packet only across a stable, fully observable Project Plane."""
    observer = observer or observe_project_plane_inputs
    capture = capture or capture_project_plane_inputs

    def unknown(observation: dict[str, Any], reason: str, message: str, *, present: bool | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "available": False,
            "present": present,
            "health": {
                **observation,
                "available": False,
                "state": "unknown",
                "color": "YELLOW",
                "reason": reason,
            },
            "message": message,
            "packetMarkdown": None,
        }

    try:
        observation = observer(project)
    except Exception as exc:
        observation = _project_plane_observation(
            False, "unknown", "project_plane_observer_failed", f"Project Plane inputs could not be observed safely: {exc}",
        )
    if not isinstance(observation, dict):
        observation = _project_plane_observation(
            False, "unknown", "project_plane_observer_invalid", "Project Plane observer returned an invalid result.",
        )
    if observation.get("available") is not True:
        return {
            "ok": False,
            "available": False,
            "present": None,
            "health": observation,
            "message": str(observation.get("message") or "Project Plane inputs are not observable; no context packet was built."),
            "packetMarkdown": None,
        }
    try:
        before, reason = capture(project)
    except Exception as exc:
        return unknown(observation, "project_plane_capture_failed", f"Project Plane inputs could not be captured safely: {exc}")
    if reason or before is None:
        return unknown(observation, reason or "project_plane_inputs_unreadable", "Project Plane inputs could not be captured stably before packet construction.")
    embedded = before.get("embedded") if isinstance(before, dict) else None
    if not isinstance(embedded, dict):
        return unknown(observation, "project_plane_capture_invalid", "Project Plane capture returned an invalid result.")
    present = bool(
        isinstance(embedded.get("root"), dict) and embedded["root"].get("exists")
        or isinstance(embedded.get("context"), dict) and embedded["context"].get("exists")
    )
    if not present:
        return {
            "ok": False,
            "available": True,
            "present": False,
            "health": observation,
            "message": "Project Plane memory is not initialized. Run `python scripts/cc.py memory work-quickstart --project-root . --topic \"<topic>\"` when durable project memory is needed.",
            "packetMarkdown": "",
        }
    try:
        packet = builder()
    except Exception as exc:
        return unknown(observation, "project_plane_packet_unavailable", f"Project Plane context packet unavailable: {exc}", present=True)
    try:
        after, reason = capture(project)
    except Exception:
        after, reason = None, "project_plane_capture_failed"
    if reason or after is None or before != after:
        return unknown(
            observation,
            reason or "project_plane_inputs_changed_during_packet",
            "Project Plane inputs changed or became unobservable while the context packet was being built.",
        )
    return {
        "ok": True,
        "available": True,
        "present": True,
        "health": observation,
        "message": "Project Plane context packet loaded read-only.",
        "packetMarkdown": packet,
    }


@contextmanager
def projection_writer_lock(project: Path, timeout_seconds: float = _WRITER_LOCK_TIMEOUT_SECONDS):
    """Serialize coordinated memory writers without stale-lock recovery."""
    try:
        project_details = project.lstat()
    except FileNotFoundError:
        project.mkdir(parents=True, exist_ok=True)
        project_details = project.lstat()
    if not stat.S_ISDIR(project_details.st_mode) or getattr(project_details, "st_reparse_tag", 0):
        raise RuntimeError(f"memory projection project root must be physical: {project}")
    path = writer_lock_path(project)
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    descriptor: int | None = None
    token = f"{os.getpid()}:{uuid.uuid4().hex}\n".encode("ascii", errors="strict")
    identity: tuple[int, int] | None = None
    token_ready = False
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for memory projection writer lock: {path}")
            time.sleep(0.01)

    def append_secondary_error(target: BaseException, error: BaseException) -> None:
        if target is error:
            return
        nested_notes = tuple(getattr(error, "__notes__", ()))
        target.add_note(f"{error.__class__.__name__}: {error}")
        for note in nested_notes:
            target.add_note(f"nested: {note}")

    def release_owned_pathname() -> None:
        tombstone = path.parent / f".{path.name}.{uuid.uuid4().hex}.release"

        def entry_exists(candidate: Path) -> bool:
            try:
                candidate.lstat()
            except FileNotFoundError:
                return False
            return True

        try:
            os.rename(path, tombstone)
        except FileNotFoundError as exc:
            raise RuntimeError(f"memory projection writer lock disappeared: {path}") from exc
        try:
            check_descriptor = os.open(tombstone, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            check_close_error: BaseException | None = None

            def close_check_descriptor() -> None:
                if check_descriptor == descriptor:
                    # A mocked close can release the writer handle and then
                    # raise, allowing the OS to reuse its integer descriptor.
                    # Closing that distinct inspection handle through fdopen
                    # keeps the writer os.close attempt observably single.
                    os.fdopen(check_descriptor, "rb", closefd=True).close()
                    return
                os.close(check_descriptor)

            try:
                observed = os.fstat(check_descriptor)
                observed_identity = (observed.st_dev, observed.st_ino)
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(check_descriptor, 4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                pathname_details = tombstone.lstat()
                pathname_identity = (pathname_details.st_dev, pathname_details.st_ino)
            except BaseException as inspection_error:
                try:
                    close_check_descriptor()
                except BaseException as close_error:
                    append_secondary_error(inspection_error, close_error)
                raise
            else:
                try:
                    close_check_descriptor()
                except BaseException as close_error:
                    check_close_error = close_error

            try:
                if (
                    identity is None
                    or not stat.S_ISREG(observed.st_mode)
                    or getattr(pathname_details, "st_reparse_tag", 0)
                    or observed_identity != identity
                    or pathname_identity != identity
                    or (token_ready and b"".join(chunks) != token)
                ):
                    if entry_exists(path):
                        raise RuntimeError(f"memory projection writer lock path became occupied: {path}")
                    os.rename(tombstone, path)
                    raise RuntimeError(f"memory projection writer lock ownership changed: {path}")
                tombstone.unlink()
            except BaseException as pathname_error:
                if check_close_error is not None:
                    append_secondary_error(check_close_error, pathname_error)
                    raise check_close_error
                raise
            if check_close_error is not None:
                raise check_close_error
        except BaseException as exc:
            try:
                if entry_exists(tombstone) and not entry_exists(path):
                    os.rename(tombstone, path)
            except BaseException as restore_exc:
                append_secondary_error(exc, restore_exc)
            raise

    def release_writer_lock(primary_error: BaseException | None) -> None:
        first_error = primary_error

        def record(error: BaseException) -> None:
            nonlocal first_error
            if first_error is None:
                first_error = error
                return
            append_secondary_error(first_error, error)

        try:
            os.close(descriptor)
        except BaseException as close_error:
            record(close_error)

        try:
            release_owned_pathname()
        except BaseException as pathname_error:
            if first_error is None:
                raise
            record(pathname_error)

        if primary_error is None and first_error is not None:
            raise first_error

    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RuntimeError(f"memory projection writer lock is not regular: {path}")
        identity = (details.st_dev, details.st_ino)
        offset = 0
        while offset < len(token):
            written = os.write(descriptor, token[offset:])
            if written <= 0:
                raise OSError(f"memory projection writer lock write made no progress: {path}")
            offset += written
        os.fsync(descriptor)
        token_ready = True
        yield
    except BaseException as primary_error:
        release_writer_lock(primary_error)
        raise
    else:
        release_writer_lock(None)


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _metadata(conn: Any) -> dict[str, str]:
    if not _table_exists(conn, "metadata"):
        return {}
    return {
        str(row["key"]): str(row["value"])
        for row in conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    }


def _count_rows(conn: Any, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    query = f"SELECT COUNT(*) AS count FROM {table}"
    if where:
        query += f" WHERE {where}"
    row = conn.execute(query, params).fetchone()
    return int(row["count"] if row else 0)


def _count_by(conn: Any, table: str, field: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT {field} AS key, COUNT(*) AS count FROM {table} GROUP BY {field} ORDER BY {field}"
    ).fetchall()
    return {str(row["key"]): int(row["count"]) for row in rows}


def _entity_summary(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(str(row["data"] or "{}"))
    except (TypeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    facets = data.get("document_facets")
    return {
        "id": str(row["id"]),
        "type": str(row["type"]),
        "title": str(row["title"]),
        "path": str(row["path"] or ""),
        "lifecycle": str(row["lifecycle"]),
        "updatedAt": str(row["updated_at"]),
        "documentType": str(data.get("document_type") or ""),
        "documentFacets": facets if isinstance(facets, list) else [],
    }


def _recent_entities(conn: Any, where: str = "", params: tuple[Any, ...] = (), limit: int = 8) -> list[dict[str, Any]]:
    query = "SELECT id, type, title, path, lifecycle, data, updated_at FROM entities"
    if where:
        query += f" WHERE {where}"
    query += " ORDER BY updated_at DESC, title ASC LIMIT ?"
    return [_entity_summary(row) for row in conn.execute(query, (*params, limit)).fetchall()]


def _relative_path(project: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _file_info(project: Path, path: Path) -> dict[str, Any]:
    try:
        stat_result = path.stat()
    except OSError:
        return {}
    return {
        "path": _relative_path(project, path),
        "exists": path.is_file(),
        "isFile": path.is_file(),
        "isDirectory": path.is_dir(),
        "sizeBytes": int(stat_result.st_size) if path.is_file() else None,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat_result.st_mtime)),
    }


def _list_markdown_files(root: Path) -> list[Path]:
    try:
        return sorted(path for path in root.glob("*.md") if path.is_file()) if root.is_dir() else []
    except OSError:
        return []


def _recognized_rag_packets(root: Path) -> list[Path]:
    packets: list[Path] = []
    for path in _list_markdown_files(root):
        try:
            sample = path.read_text(encoding="utf-8", errors="replace")[:512]
        except OSError:
            continue
        if "ControlCoding GraphRAG Packet" in sample:
            packets.append(path)
    return packets


def _latest_file(project: Path, files: list[Path]) -> dict[str, Any]:
    if not files:
        return {}
    try:
        return _file_info(project, max(files, key=lambda path: path.stat().st_mtime_ns))
    except OSError:
        return {}


def _derived_artifacts(project: Path, metadata: dict[str, str], vectors: dict[str, Any], entity_count: int) -> dict[str, Any]:
    views_root = project / CONTROL_DIRNAME / MEMORY_DIRNAME / "views"
    dev_context_root = project / CONTROL_DIRNAME / "context-packets"
    work_context_root = project / ".controlwork" / "context-packets"
    work_views_root = project / ".controlwork" / "memory" / "views"
    view_files = _list_markdown_files(views_root)
    packet_files = _recognized_rag_packets(dev_context_root)
    return {
        "views": {
            "path": _relative_path(project, views_root),
            "exists": views_root.exists(),
            "fileCount": len(view_files),
            "lastGeneratedAt": metadata.get("last_views_generated_at", ""),
            "latestFile": _latest_file(project, view_files),
            "stale": bool(entity_count and (not view_files or not metadata.get("last_views_generated_at"))),
        },
        "vectors": vectors,
        "devContextPackets": {
            "path": _relative_path(project, dev_context_root),
            "exists": dev_context_root.exists(),
            "fileCount": len(_list_markdown_files(dev_context_root)),
            "latestFile": _latest_file(project, _list_markdown_files(dev_context_root)),
        },
        "graphPackets": {
            "path": _relative_path(project, dev_context_root),
            "exists": dev_context_root.exists(),
            "fileCount": len(packet_files),
            "latestFile": _latest_file(project, packet_files),
            "implemented": True,
            "stale": False if packet_files else None,
            "note": "GraphRAG packets are explicit rag-pack snapshots stored with Dev Plane context packets.",
        },
        "projectPlaneViews": {
            "path": _relative_path(project, work_views_root),
            "exists": work_views_root.exists(),
            "fileCount": len(_list_markdown_files(work_views_root)),
            "latestFile": _latest_file(project, _list_markdown_files(work_views_root)),
        },
        "projectPlaneContextPackets": {
            "path": _relative_path(project, work_context_root),
            "exists": work_context_root.exists(),
            "fileCount": len(_list_markdown_files(work_context_root)),
            "latestFile": _latest_file(project, _list_markdown_files(work_context_root)),
        },
        "lastScanAt": metadata.get("last_scan_at", ""),
    }


def _project_plane(project: Path) -> dict[str, Any]:
    root = project / ".controlwork"
    context_path = project / "CONTROLWORK.md"
    config_path = root / "config.json"
    link_path = root / "link.json"
    config = _read_json_object(config_path)
    link = _read_json_object(link_path)
    return {
        "hasControlWorkRoot": root.exists(),
        "hasCanonicalContext": context_path.exists(),
        "hasConfig": bool(config),
        "canonicalContext": "CONTROLWORK.md",
        "distribution": str(config.get("distribution") or "") if config else "",
        "standaloneCompatible": bool(config.get("standaloneCompatible")) if config else False,
        "attachedExternal": bool(link),
        "link": {
            "exists": bool(link),
            "path": _relative_path(project, link_path),
            "syncPolicy": str(link.get("syncPolicy") or ""),
            "externalPath": str(link.get("externalPath") or ""),
            "attachedAt": str(link.get("attachedAt") or ""),
            "validation": {},
        },
    }


def _recommendations(project: Path, dev_plane: dict[str, Any], project_plane: dict[str, Any], derived: dict[str, Any], topic: str, scope: str) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    if not derived.get("lastScanAt"):
        recommendations.append({"reason": "Dev Plane has no recorded scan timestamp.", "command": "python scripts/cc.py memory scan --project-root .", "writes": True})
    if derived["vectors"].get("stale"):
        recommendations.append({"reason": "Sparse vectors are stale or missing for current chunks.", "command": "python scripts/cc.py memory vector rebuild --project-root .", "writes": True})
    if derived["views"].get("stale"):
        recommendations.append({"reason": "Generated Dev Plane views are stale or missing.", "command": "python scripts/cc.py memory views generate --project-root .", "writes": True})
    suggested = int(dev_plane.get("suggestionCounts", {}).get("suggested") or 0)
    if suggested:
        recommendations.append({"reason": f"{suggested} graph correlation suggestion(s) need review.", "command": "python scripts/cc.py memory graph suggestions --project-root .", "writes": False})
    if project_plane["hasControlWorkRoot"] or project_plane["hasCanonicalContext"]:
        recommendations.append({"reason": "Project Plane is present and should be inspected explicitly.", "command": "python scripts/cc.py memory work-status --project-root .", "writes": False})
        if not derived["projectPlaneContextPackets"]["fileCount"]:
            command_topic = topic or "current focus"
            recommendations.append({"reason": "No Project Plane context packet is available for this topic.", "command": f"python scripts/cc.py memory work-context-pack --project-root . --scope {scope or 'general'} --topic \"{command_topic}\"", "writes": True})
    if int(dev_plane.get("counts", {}).get("applicationMemoryComponents") or 0):
        recommendations.append({"reason": "Application-owned memory references exist; keep runtime truth in the application.", "command": "Use application-approved adapters only.", "writes": False})
    return recommendations


def _warnings(dev_plane: dict[str, Any], project_plane: dict[str, Any], derived: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if derived["vectors"].get("stale"):
        warnings.append("Sparse vector index is stale or missing.")
    if derived["views"].get("stale"):
        warnings.append("Dev Plane generated views are stale or missing.")
    if project_plane.get("attachedExternal"):
        warnings.append("ControlWork sync is manual and explicit; bootstrap did not pull or push.")
    if int(dev_plane.get("counts", {}).get("applicationMemoryComponents") or 0):
        warnings.append("Application-owned memory is referenced for project context only, not owned by ControlCoding.")
    return warnings


def build_legacy_status_snapshot(
    project: Path,
    conn: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Capture the final writer snapshot that will be published after commit."""
    project = project.resolve()
    inspection = inspect_schema(conn)
    schema = inspection.to_payload()
    if not inspection.has_queryable_current_shape:
        raise RuntimeError(f"memory schema is not queryable: {inspection.state}")

    # Reuse the legacy bootstrap's filesystem-only formatters.  Database data
    # is assembled below from *this* committed connection, so this never opens
    # a second SQLite connection or races the snapshot being published.
    from .bootstrap import (
        GRAPH_CONTRACT_VERSION,
        _artifact_status as _bootstrap_artifact_status,
        _project_plane as _bootstrap_project_plane,
        _recommendations as _bootstrap_recommendations,
        _warnings as _bootstrap_warnings,
    )
    from .graph import _status_payload as _graph_status_payload
    from .ids import _project_short
    from .lifecycle import LIFECYCLE_ATTENTION_STATES
    from .sessions import _all_session_payloads, _session_view_status
    from .store import _read_json
    from .vector import _vector_health

    metadata = _metadata(conn)
    vectors = _vector_health(conn)
    sessions = _all_session_payloads(conn)
    attention_states = tuple(sorted(LIFECYCLE_ATTENTION_STATES))
    placeholders = ",".join("?" for _item in attention_states)
    entity_count = _count_rows(conn, "entities")
    counts = {
        "entities": entity_count,
        "semanticChunks": _count_rows(conn, "semantic_chunks"),
        "edges": _count_rows(conn, "edges"),
        "events": _count_rows(conn, "events"),
        "vectorRows": _count_rows(conn, "derived_vector_index"),
        "applicationMemoryComponents": _count_rows(conn, "entities", "type = ?", ("application_memory_component",)),
    }
    attention = _count_rows(conn, "entities", f"lifecycle IN ({placeholders})", attention_states)
    graph = _graph_status_payload(conn)
    manifest_path = project / CONTROL_DIRNAME / MANIFEST_FILENAME
    manifest = _read_json(manifest_path)
    recent_entities = _recent_entities(
        conn,
        "path IS NOT NULL AND path != '' AND path NOT LIKE '.controlwork/%' AND path != 'CONTROLWORK.md'",
        limit=8,
    )
    active_focus = _recent_entities(
        conn,
        "lifecycle IN ('active', 'implemented', 'triaged', 'needs_review') "
        "AND type IN ('decision', 'plan', 'idea', 'note', 'work_item')",
        limit=8,
    )
    application_records = _recent_entities(
        conn,
        "type = ?",
        ("application_memory_component",),
        limit=8,
    )
    entity_types = _count_by(conn, "entities", "type")
    edge_types = _count_by(conn, "edges", "type")
    db_data = {
        "metadata": metadata,
        "counts": counts,
        "entityTypes": entity_types,
        "lifecycles": _count_by(conn, "entities", "lifecycle"),
        "suggestionCounts": _count_by(conn, "correlation_suggestions", "status"),
        "graphContractVersion": GRAPH_CONTRACT_VERSION,
        "sqliteSchemaVersion": metadata.get("schema_version") or "",
        "schema": schema,
        "recentEntities": recent_entities,
        "activeFocus": active_focus,
        "applicationMemoryComponents": application_records,
        "vectorHealth": vectors,
    }
    derived = _bootstrap_artifact_status(project, db_data, metadata)
    project_plane = _bootstrap_project_plane(project)
    dev_plane = {
        "hasControlDir": (project / CONTROL_DIRNAME).exists(),
        "hasManifest": manifest_path.exists(),
        "hasDatabase": _db_path(project).exists(),
        "initialized": bool(manifest_path.exists() and _db_path(project).exists()),
        "manifest": manifest,
        "databasePath": _relative_path(project, _db_path(project)),
        "graphContractVersion": GRAPH_CONTRACT_VERSION,
        "sqliteSchemaVersion": metadata.get("schema_version") or "",
        "schema": schema,
        "counts": counts,
        "entityTypes": db_data["entityTypes"],
        "lifecycles": db_data["lifecycles"],
        "suggestionCounts": db_data["suggestionCounts"],
    }
    bootstrap = {
        "ok": True,
        "projectRoot": str(project),
        "scope": "general",
        "topic": "",
        "mode": "read_only_no_sync_no_hidden_writes",
        "devPlane": dev_plane,
        "projectPlane": project_plane,
        "applicationOwnedMemory": {
            "referencedComponents": counts["applicationMemoryComponents"],
            "records": application_records,
            "boundary": "Application runtime memory remains application-owned; ControlCoding may reference approved project documents only.",
        },
        "derivedArtifacts": derived,
        "hotDocuments": recent_entities,
        "activeFocus": active_focus,
    }
    bootstrap["recommendations"] = _bootstrap_recommendations(
        project,
        dev_plane,
        project_plane,
        derived,
        "",
        "general",
    )
    bootstrap["warnings"] = _bootstrap_warnings(dev_plane, project_plane, derived, [])
    open_followups = [
        {"sessionId": str(session.get("id") or ""), "topic": str(session.get("topic") or ""), "status": str(session.get("status") or ""), "text": str(followup or "")}
        for session in sessions for followup in (session.get("followups") or [])
    ]
    legacy_status = {
        "ok": True,
        "projectShort": _project_short(project),
        "schema": schema,
        "entities": entity_count,
        "lifecycleAttention": attention,
        "staleOrNeedsReview": attention,
        "byType": dev_plane["entityTypes"],
        "byLifecycle": dev_plane["lifecycles"],
        "lastScanAt": metadata.get("last_scan_at", ""),
        "lastViewsGeneratedAt": metadata.get("last_views_generated_at", ""),
        "generatedViews": _count_rows(conn, "views"),
        "vectors": vectors,
        "sessions": {
            "available": True,
            "message": "",
            "sessionCount": len(sessions),
            "activeCount": sum(1 for session in sessions if session.get("status") == "active"),
            "latestSession": sessions[0] if sessions else {},
            "openFollowups": open_followups,
            "views": _session_view_status(conn),
        },
        "graph": graph,
        "hotDocuments": recent_entities,
        "bootstrap": bootstrap,
    }
    session_evidence = [
        {
            "id": str(session.get("id") or ""),
            "topic": str(session.get("topic") or ""),
            "status": str(session.get("status") or ""),
            "followups": [str(item or "") for item in (session.get("followups") or [])],
        }
        for session in sessions
    ]
    graph_evidence = {
        "entityTypes": entity_types,
        "edgeTypes": edge_types,
    }
    return legacy_status, session_evidence, graph_evidence


def _projection_payload(
    legacy_status: dict[str, Any],
    session_evidence: list[dict[str, Any]],
    graph_evidence: dict[str, dict[str, int]],
    database_fingerprint: dict[str, Any],
    filesystem_fingerprint_value: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": HEALTH_PROJECTION_SCHEMA_VERSION,
        "kind": HEALTH_PROJECTION_KIND,
        "sourceOfTruth": False,
        "databaseFingerprint": database_fingerprint,
        "filesystemFingerprint": filesystem_fingerprint_value,
        "sessionEvidence": session_evidence,
        "graphEvidence": graph_evidence,
        "legacyStatus": legacy_status,
    }


def _compensate_default_projection_publication(
    path: Path,
    baseline: Any,
    publication_state: list[Any],
    primary_error: BaseException,
) -> None:
    from .store import (
        _append_exception_notes,
        _atomic_replace_bytes,
        _file_rollback_state,
        _identity_bound_unlink,
        _restore_owned_file_bytes,
        _same_rollback_state,
    )

    try:
        current = _file_rollback_state(path)
        if (
            baseline.parent_generation is None
            or current.parent_generation is None
            or baseline.parent_generation.identity
            != current.parent_generation.identity
        ):
            raise RuntimeError(
                f"default projection compensation parent changed: {path.parent}"
            )
        if _same_rollback_state(current, baseline, include_parent=False):
            return

        owned = publication_state[0]
        if baseline.existed:
            if not current.existed:
                if (
                    owned is None
                    or owned.existed
                    or owned.parent_generation is None
                    or owned.parent_generation.identity
                    != current.parent_generation.identity
                ):
                    raise RuntimeError(
                        f"default projection authorized absence is unavailable: {path}"
                    )
                restored_state: list[Any] = [None]
                _atomic_replace_bytes(
                    path,
                    baseline.content,
                    mode=baseline.mode,
                    replace_existing=False,
                    expected_state=current,
                    publication_state=restored_state,
                )
                restored = restored_state[0]
                if restored is None or restored.identity is None:
                    raise RuntimeError(
                        f"default projection restoration ownership is unavailable: {path}"
                    )
                _restore_owned_file_bytes(
                    path,
                    restored.identity,
                    baseline.content,
                    baseline.content,
                    mode=baseline.mode,
                    atime_ns=baseline.atime_ns,
                    mtime_ns=baseline.mtime_ns,
                )
                return
            if (
                owned is not None
                and owned.existed
                and owned.identity is not None
                and _same_rollback_state(current, owned)
            ):
                _restore_owned_file_bytes(
                    path,
                    owned.identity,
                    owned.content,
                    baseline.content,
                    mode=baseline.mode,
                    atime_ns=baseline.atime_ns,
                    mtime_ns=baseline.mtime_ns,
                )
                return
        else:
            if not current.existed:
                return
            if (
                owned is not None
                and owned.existed
                and owned.identity is not None
                and owned.generation is not None
                and _same_rollback_state(current, owned)
            ):
                _identity_bound_unlink(
                    path,
                    owned.identity,
                    expected_content=owned.content,
                    expected_generation=owned.generation,
                    expected_parent_generation=owned.parent_generation,
                )
                return
        raise RuntimeError(
            f"default projection changed outside compensation ownership: {path}"
        )
    except BaseException as compensation_error:
        primary_error.add_note("default projection compensation incomplete")
        _append_exception_notes(primary_error, compensation_error)


def _atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    expected_state: Any = None,
    publication_state: list[Any] | None = None,
) -> None:
    from .store import _atomic_replace_bytes, _file_rollback_state

    content = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = expected_state if expected_state is not None else _file_rollback_state(path)
    caller_manages_publication = publication_state is not None
    effective_publication_state = publication_state if publication_state is not None else [None]
    try:
        _atomic_replace_bytes(
            path,
            content,
            mode=baseline.mode if baseline.existed else None,
            replace_existing=baseline.existed if expected_state is not None else True,
            expected_state=baseline,
            publication_state=effective_publication_state,
        )
    except BaseException as primary_error:
        if not caller_manages_publication:
            _compensate_default_projection_publication(
                path,
                baseline,
                effective_publication_state,
                primary_error,
            )
        raise


def publish_freshness_projection(
    project: Path,
    legacy_status: dict[str, Any],
    session_evidence: list[dict[str, Any]],
    graph_evidence: dict[str, dict[str, int]],
    database_fingerprint: dict[str, Any],
    filesystem_fingerprint_value: dict[str, Any],
    *,
    expected_state: Any = None,
    publication_state: list[Any] | None = None,
) -> Path:
    """Atomically publish writer-captured DB and filesystem fingerprints."""
    path = projection_path(project)
    _atomic_write_json(
        path,
        _projection_payload(
            legacy_status,
            session_evidence,
            graph_evidence,
            database_fingerprint,
            filesystem_fingerprint_value,
        ),
        expected_state=expected_state,
        publication_state=publication_state,
    )
    return path


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _has_exact_keys(value: Any, expected: set[str] | frozenset[str]) -> bool:
    return isinstance(value, dict) and set(value) == set(expected)


def _valid_nonempty_key(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _valid_string_list(value: Any, *, nonempty_items: bool = False) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and (bool(item) if nonempty_items else True)
        for item in value
    )


def _valid_json_value(value: Any) -> bool:
    """Validate values for explicitly dynamic application-owned JSON maps."""
    if isinstance(value, float):
        return math.isfinite(value)
    if value is None or isinstance(value, (str, bool)) or _is_int(value):
        return True
    if isinstance(value, list):
        return all(_valid_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(_valid_nonempty_key(key) and _valid_json_value(item) for key, item in value.items())
    return False


def _valid_dynamic_json_map(value: Any) -> bool:
    return isinstance(value, dict) and _valid_json_value(value)


def _valid_identity(value: Any) -> bool:
    return value is None or (
        _has_exact_keys(value, {"device", "inode"})
        and _is_non_negative_int(value["device"])
        and _is_non_negative_int(value["inode"])
        and value["inode"] > 0
    )


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_file_record(record: Any) -> bool:
    expected = {"exists", "type", "identity", "sizeBytes", "mtimeNs", "sha256"}
    if (
        not _has_exact_keys(record, expected)
        or not isinstance(record["exists"], bool)
        or not isinstance(record["type"], str)
        or not _valid_identity(record["identity"])
        or not _is_non_negative_int(record["sizeBytes"])
        or not _is_non_negative_int(record["mtimeNs"])
        or not isinstance(record["sha256"], str)
    ):
        return False
    if record["exists"]:
        return (
            record["type"] == "regular"
            and record["identity"] is not None
            and _valid_sha256(record["sha256"])
        )
    return (
        record["type"] == "absent"
        and record["identity"] is None
        and record["sizeBytes"] == 0
        and record["mtimeNs"] == 0
        and record["sha256"] == ""
    )


def _valid_directory_record(value: Any) -> bool:
    return (
        _has_exact_keys(value, {"type", "identity"})
        and value["type"] == "directory"
        and value["identity"] is not None
        and _valid_identity(value["identity"])
    )


def _valid_fingerprint(value: Any) -> bool:
    if (
        not _has_exact_keys(value, {"algorithm", "files", "sourceDirectory"})
        or value["algorithm"] != "sha256"
        or not _valid_directory_record(value["sourceDirectory"])
    ):
        return False
    files = value["files"]
    expected = {DB_FILENAME, f"{DB_FILENAME}-wal"}
    if not isinstance(files, dict) or set(files) != expected or not all(_valid_file_record(files[name]) for name in expected):
        return False
    # The database is the source.  An absent WAL is a normal stable state, but
    # a projection cannot make an absent database look fresh.
    return files[DB_FILENAME]["exists"] is True and files[DB_FILENAME]["type"] == "regular"


def _valid_project_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and not path.anchor and all(part not in {"", ".", ".."} for part in path.parts)


def _valid_metadata_record(value: Any) -> bool:
    expected = {"exists", "type", "identity", "sizeBytes", "mtimeNs"}
    if (
        not _has_exact_keys(value, expected)
        or not isinstance(value["exists"], bool)
        or not _valid_identity(value["identity"])
        or not _is_non_negative_int(value["sizeBytes"])
        or not _is_non_negative_int(value["mtimeNs"])
    ):
        return False
    if not value["exists"]:
        return (
            value["type"] == "absent"
            and value["identity"] is None
            and value["sizeBytes"] == 0
            and value["mtimeNs"] == 0
        )
    return value["type"] in {"regular", "directory"} and value["identity"] is not None


def _valid_controlwork_config_facts(value: Any) -> bool:
    expected = {
        "exists", "schemaVersion", "product", "distribution", "standaloneCompatible", "canonicalContext",
    }
    if not _has_exact_keys(value, expected) or not isinstance(value["exists"], bool):
        return False
    if not value["exists"]:
        return value == _absent_controlwork_config_facts()
    return (
        _is_int(value["schemaVersion"])
        and value["schemaVersion"] == 1
        and value["product"] == "ControlWork"
        and value["distribution"] in _CONTROLWORK_DISTRIBUTIONS
        and isinstance(value["standaloneCompatible"], bool)
        and value["canonicalContext"] == _CONTROLWORK_CONTEXT_FILENAME
    )


def _valid_captured_json_facts(value: Any) -> bool:
    return isinstance(value, dict) and _valid_json_value(value)


def _valid_controlwork_link_facts(value: Any) -> bool:
    if value == {}:
        return True
    expected = {
        "schemaVersion", "mode", "externalPath", "externalContext",
        "syncPolicy", "attachedAt",
    }
    if (
        not _has_exact_keys(value, expected)
        or value["schemaVersion"] != 1
        or value["mode"] != "linked_external_controlwork"
        or not all(
            isinstance(value[key], str) and value[key].strip()
            for key in ("externalPath", "externalContext", "syncPolicy", "attachedAt")
        )
        or not _valid_timestamp(value["attachedAt"])
    ):
        return False
    try:
        external_root = Path(value["externalPath"]).expanduser().resolve()
        external_context = Path(value["externalContext"]).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return external_context == external_root / _CONTROLWORK_CONTEXT_FILENAME


def _valid_filesystem_entry(value: Any, *, content_hashed: bool) -> bool:
    expected = {"path", "type", "identity", "sizeBytes", "mtimeNs", "sha256"}
    if (
        not _has_exact_keys(value, expected)
        or not _valid_project_relative_path(value["path"])
        or value["type"] not in {"regular", "directory"}
        or value["identity"] is None
        or not _valid_identity(value["identity"])
        or not _is_non_negative_int(value["sizeBytes"])
        or not _is_non_negative_int(value["mtimeNs"])
    ):
        return False
    if content_hashed:
        return value["type"] == "regular" and _valid_sha256(value["sha256"])
    return value["sha256"] is None


def _valid_filesystem_inventory(value: Any, *, expected_mode: str, root: str) -> bool:
    expected = {"exists", "type", "identity", "mode", "entries", "graphPacketPaths"}
    if (
        not _has_exact_keys(value, expected)
        or not isinstance(value["exists"], bool)
        or value["mode"] != expected_mode
        or not isinstance(value["entries"], list)
        or not _valid_string_list(value["graphPacketPaths"], nonempty_items=True)
        or not _valid_identity(value["identity"])
    ):
        return False
    if not value["exists"]:
        return (
            value["type"] == "absent"
            and value["identity"] is None
            and value["entries"] == []
            and value["graphPacketPaths"] == []
        )
    if value["identity"] is None or value["type"] != "directory":
        return False
    content_hashed = expected_mode in {"dev_context_packets", "project_context_packets"}
    entries = value["entries"]
    if not all(_valid_filesystem_entry(item, content_hashed=content_hashed) for item in entries):
        return False
    paths = [item["path"] for item in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        return False
    graph_packet_paths = value["graphPacketPaths"]
    if (
        graph_packet_paths != sorted(graph_packet_paths)
        or len(graph_packet_paths) != len(set(graph_packet_paths))
        or not set(graph_packet_paths).issubset(paths)
        or (expected_mode != "dev_context_packets" and graph_packet_paths)
    ):
        return False
    root_path = PurePosixPath(root)
    for item in entries:
        entry_path = PurePosixPath(item["path"])
        if entry_path.parent != root_path:
            return False
        if expected_mode != "project_area" and entry_path.suffix.lower() != ".md":
            return False
        if expected_mode == "project_area" and entry_path.name == ".gitkeep":
            return False
    return True


def _valid_filesystem_fingerprint(value: Any) -> bool:
    expected = {
        "schemaVersion", "algorithm", "projectDirectory", "contentFiles",
        "controlWorkRoot", "manifestFacts", "linkFacts", "configFacts",
        "presencePaths", "inventories", "externalAttachment",
    }
    if (
        not _has_exact_keys(value, expected)
        or not _is_int(value["schemaVersion"])
        or value["schemaVersion"] != FILESYSTEM_FINGERPRINT_SCHEMA_VERSION
        or value["algorithm"] != "sha256"
        or not _valid_directory_record(value["projectDirectory"])
        or not _valid_metadata_record(value["controlWorkRoot"])
        or (
            value["controlWorkRoot"]["exists"]
            and value["controlWorkRoot"]["type"] != "directory"
        )
        or not _valid_controlwork_config_facts(value["configFacts"])
        or not _valid_captured_json_facts(value["manifestFacts"])
        or not _valid_captured_json_facts(value["linkFacts"])
        or not _valid_controlwork_link_facts(value["linkFacts"])
    ):
        return False
    expected_content = {
        f"{CONTROL_DIRNAME}/{MANIFEST_FILENAME}",
        f"{_CONTROLWORK_DIRNAME}/config.json",
        f"{_CONTROLWORK_DIRNAME}/link.json",
    }
    content = value["contentFiles"]
    if not isinstance(content, dict) or set(content) != expected_content or not all(
        _valid_file_record(content[path]) for path in expected_content
    ):
        return False
    config_path = f"{_CONTROLWORK_DIRNAME}/config.json"
    if content[config_path]["exists"] is not value["configFacts"]["exists"]:
        return False
    manifest_path = f"{CONTROL_DIRNAME}/{MANIFEST_FILENAME}"
    link_path = f"{_CONTROLWORK_DIRNAME}/link.json"
    if content[manifest_path]["exists"] is not (value["manifestFacts"] != {}):
        return False
    if content[link_path]["exists"] is not (value["linkFacts"] != {}):
        return False
    presence = value["presencePaths"]
    if not _has_exact_keys(presence, {_CONTROLWORK_CONTEXT_FILENAME}) or not _valid_metadata_record(
        presence[_CONTROLWORK_CONTEXT_FILENAME]
    ):
        return False
    expected_inventories = {
        f"{CONTROL_DIRNAME}/views": "markdown_metadata",
        f"{CONTROL_DIRNAME}/context-packets": "dev_context_packets",
        f"{_CONTROLWORK_DIRNAME}/context-packets": "project_context_packets",
        **{
            f"{_CONTROLWORK_DIRNAME}/memory/{area}": "project_area"
            for area in _CONTROLWORK_MEMORY_AREAS
        },
    }
    inventories = value["inventories"]
    if not isinstance(inventories, dict) or set(inventories) != set(expected_inventories):
        return False
    if not all(
        _valid_filesystem_inventory(inventories[root], expected_mode=mode, root=root)
        for root, mode in expected_inventories.items()
    ):
        return False
    if not value["controlWorkRoot"]["exists"]:
        if content[f"{_CONTROLWORK_DIRNAME}/config.json"]["exists"]:
            return False
        if content[f"{_CONTROLWORK_DIRNAME}/link.json"]["exists"]:
            return False
        if any(
            inventory["exists"]
            for root, inventory in inventories.items()
            if root.startswith(f"{_CONTROLWORK_DIRNAME}/")
        ):
            return False
    attachment = value["externalAttachment"]
    if not _has_exact_keys(attachment, {"configured", "root", "rootRecord", "paths"}) or not isinstance(attachment["configured"], bool):
        return False
    if not _valid_metadata_record(attachment["rootRecord"]):
        return False
    if not attachment["configured"]:
        return (
            attachment["root"] == ""
            and attachment["rootRecord"] == _absent_metadata_record()
            and attachment["paths"] == {}
        )
    expected_external = {
        _CONTROLWORK_CONTEXT_FILENAME,
        f"{_CONTROLWORK_DIRNAME}/config.json",
        f"{_CONTROLWORK_DIRNAME}/memory",
    }
    return (
        isinstance(attachment["root"], str)
        and bool(attachment["root"])
        and attachment["rootRecord"]["exists"] is True
        and attachment["rootRecord"]["type"] == "directory"
        and isinstance(attachment["paths"], dict)
        and set(attachment["paths"]) == expected_external
        and all(_valid_metadata_record(attachment["paths"][path]) for path in expected_external)
    )


def _valid_status_map(value: Any) -> bool:
    return isinstance(value, dict) and all(
        _valid_nonempty_key(key) and _is_non_negative_int(count)
        for key, count in value.items()
    )


def _valid_schema(value: Any) -> bool:
    expected = {
        "schemaState", "userVersion", "metadataVersion", "targetVersion",
        "effectiveVersion", "schemaVersionRows", "missingTables",
        "missingColumns", "issues",
    }
    if not _has_exact_keys(value, expected):
        return False
    state = value["schemaState"]
    if state not in {"current", "legacy_adoptable"}:
        return False
    if not _is_non_negative_int(value["userVersion"]) or value["userVersion"] > TARGET_SCHEMA_VERSION:
        return False
    if value["metadataVersion"] is not None and (
        not _is_non_negative_int(value["metadataVersion"])
        or value["metadataVersion"] > TARGET_SCHEMA_VERSION
    ):
        return False
    if (
        not _is_non_negative_int(value["targetVersion"])
        or value["targetVersion"] != TARGET_SCHEMA_VERSION
        or not _is_non_negative_int(value["effectiveVersion"])
        or value["effectiveVersion"] > TARGET_SCHEMA_VERSION
    ):
        return False
    if value["missingTables"] != [] or value["missingColumns"] != {} or not _valid_string_list(value["issues"], nonempty_items=True):
        return False
    rows = value["schemaVersionRows"]
    if not isinstance(rows, list) or not all(
        _is_non_negative_int(item) and item <= TARGET_SCHEMA_VERSION for item in rows
    ):
        return False
    markers_current = (
        value["userVersion"] == TARGET_SCHEMA_VERSION
        and value["metadataVersion"] == TARGET_SCHEMA_VERSION
        and value["effectiveVersion"] == TARGET_SCHEMA_VERSION
        and TARGET_SCHEMA_VERSION in rows
    )
    if state == "current":
        return markers_current and value["issues"] == []
    # inspect_schema() emits legacy_adoptable only for a current physical
    # shape with at least one marker mismatch and an explanatory issue.
    marker_misaligned = (
        value["userVersion"] != TARGET_SCHEMA_VERSION
        or value["metadataVersion"] != TARGET_SCHEMA_VERSION
        or TARGET_SCHEMA_VERSION not in rows
    )
    return marker_misaligned and bool(value["issues"])


def _valid_entity_summary(value: Any) -> bool:
    expected = {"id", "type", "title", "path", "lifecycle", "updatedAt", "documentType", "documentFacets"}
    return (
        _has_exact_keys(value, expected)
        and all(isinstance(value[key], str) for key in expected - {"documentFacets"})
        and value["lifecycle"] in VALID_LIFECYCLES
        and _valid_string_list(value["documentFacets"])
    )


def _valid_entity_list(value: Any) -> bool:
    return isinstance(value, list) and all(_valid_entity_summary(item) for item in value)


def _valid_vectors(value: Any) -> bool:
    expected = {
        "adapter", "rowCount", "semanticChunks", "indexableChunks", "skippedChunks",
        "lastRebuiltAt", "stale", "staleReasons", "missingRows",
        "contentHashMismatches", "orphanRows", "postingRows", "postingSources",
        "vectorTermRows", "missingPostingSources", "orphanPostingSources", "rebuildCommand",
    }
    if not _has_exact_keys(value, expected):
        return False
    if not isinstance(value["adapter"], str) or not isinstance(value["lastRebuiltAt"], str):
        return False
    if not isinstance(value["stale"], bool) or not isinstance(value["rebuildCommand"], str):
        return False
    count_keys = (
        "rowCount", "semanticChunks", "indexableChunks", "skippedChunks",
        "missingRows", "contentHashMismatches", "orphanRows", "postingRows",
        "postingSources", "vectorTermRows", "missingPostingSources", "orphanPostingSources",
    )
    if not _valid_string_list(value["staleReasons"]) or not all(
        _is_non_negative_int(value[key]) for key in count_keys
    ):
        return False
    return (
        value["indexableChunks"] + value["skippedChunks"] == value["semanticChunks"]
        and value["missingRows"] <= value["indexableChunks"]
        and value["contentHashMismatches"] <= value["indexableChunks"]
        and value["orphanRows"] <= value["rowCount"]
        and value["stale"] is bool(value["staleReasons"])
        and (bool(value["rebuildCommand"]) is value["stale"])
    )


def _valid_timestamp(value: Any, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    if value == "":
        return allow_empty
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _valid_session_link(value: Any) -> bool:
    """Accept only the exact current or documented legacy link shapes.

    Session rows retain the serialized list created by prior releases.  Older
    records can legitimately omit the metadata added by the current command,
    but they must never turn the link list into an open-ended map.
    """
    if not isinstance(value, dict):
        return False
    keys = set(value)
    allowed = (
        {"type", "target"},
        {"type", "target", "targetType"},
        {"type", "target", "createdAt"},
        {"type", "target", "targetType", "createdAt"},
    )
    return (
        any(keys == expected for expected in allowed)
        and isinstance(value.get("type"), str)
        and bool(value["type"].strip())
        and isinstance(value.get("target"), str)
        and bool(value["target"].strip())
        and all(isinstance(value[key], str) for key in ("targetType", "createdAt") if key in value)
        and ("createdAt" not in value or _valid_timestamp(value["createdAt"]))
    )


def _valid_session_edge(value: Any, session_id: str) -> bool:
    expected = {"id", "sessionId", "type", "target", "targetType", "data", "createdAt"}
    return (
        _has_exact_keys(value, expected)
        and all(isinstance(value[key], str) for key in expected - {"data"})
        and all(bool(value[key].strip()) for key in ("id", "sessionId", "type", "target"))
        and value["sessionId"] == session_id
        and _valid_timestamp(value["createdAt"])
        and _valid_dynamic_json_map(value["data"])
    )


def _valid_session_record(value: Any) -> bool:
    expected = {
        "id", "schemaVersion", "startedAt", "endedAt", "mode", "scope", "topic", "status", "summary",
        "categories", "filesChanged", "docsChanged", "commandsRun", "testsRun", "commits", "packets",
        "decisions", "followups", "links", "data", "createdAt", "updatedAt", "edges",
    }
    string_fields = {
        "id", "startedAt", "endedAt", "mode", "scope", "topic", "status", "summary", "createdAt", "updatedAt",
    }
    text_list_fields = {
        "categories", "filesChanged", "docsChanged", "commandsRun", "testsRun", "commits", "packets", "decisions", "followups",
    }
    if (
        not _has_exact_keys(value, expected)
        or not _is_int(value["schemaVersion"])
        or value["schemaVersion"] != SESSION_RECORD_SCHEMA_VERSION
        or not all(isinstance(value[key], str) for key in string_fields)
        or not value["id"].strip()
        or value["mode"] not in VALID_SESSION_MODES
        or value["status"] not in VALID_SESSION_STATUSES
        or not all(_valid_timestamp(value[key]) for key in ("startedAt", "createdAt", "updatedAt"))
        or not _valid_timestamp(value["endedAt"], allow_empty=True)
        or (value["status"] == "active" and value["endedAt"] != "")
        or (value["status"] != "active" and value["endedAt"] == "")
        or not all(_valid_string_list(value[key]) for key in text_list_fields)
        or not isinstance(value["links"], list)
        or not all(_valid_session_link(item) for item in value["links"])
        or not _valid_dynamic_json_map(value["data"])
        or not isinstance(value["edges"], list)
    ):
        return False
    return all(_valid_session_edge(item, value["id"]) for item in value["edges"])


def _valid_open_followup(value: Any) -> bool:
    expected = {"sessionId", "topic", "status", "text"}
    return (
        _has_exact_keys(value, expected)
        and all(isinstance(value[key], str) and bool(value[key].strip()) for key in expected)
        and value["status"] in VALID_SESSION_STATUSES
    )


def _valid_session_views(value: Any, session_count: int) -> bool:
    expected = {
        "implemented", "requiredViewCount", "generatedViewCount", "latestSessionUpdatedAt",
        "oldestGeneratedAt", "stale", "paths",
    }
    if not (
        _has_exact_keys(value, expected)
        and value["implemented"] is True
        and _is_non_negative_int(value["requiredViewCount"])
        and value["requiredViewCount"] > 0
        and _is_non_negative_int(value["generatedViewCount"])
        and value["generatedViewCount"] <= value["requiredViewCount"]
        and _valid_timestamp(value["latestSessionUpdatedAt"], allow_empty=True)
        and _valid_timestamp(value["oldestGeneratedAt"], allow_empty=True)
        and isinstance(value["stale"], bool)
        and _valid_string_list(value["paths"], nonempty_items=True)
        and len(value["paths"]) == value["requiredViewCount"]
        and len(value["paths"]) == len(set(value["paths"]))
        and all(_valid_project_relative_path(path) for path in value["paths"])
    ):
        return False
    if session_count == 0:
        if value["latestSessionUpdatedAt"] != "":
            return False
    elif value["latestSessionUpdatedAt"] == "":
        return False
    if value["generatedViewCount"] == 0:
        if value["oldestGeneratedAt"] != "":
            return False
    elif value["oldestGeneratedAt"] == "":
        return False
    expected_stale = bool(
        session_count
        and (
            value["generatedViewCount"] < value["requiredViewCount"]
            or not value["oldestGeneratedAt"]
            or value["latestSessionUpdatedAt"] > value["oldestGeneratedAt"]
        )
    )
    return value["stale"] is expected_stale


def _valid_session_evidence(value: Any) -> bool:
    expected = {"id", "topic", "status", "followups"}
    if not isinstance(value, list):
        return False
    if not all(
        _has_exact_keys(item, expected)
        and isinstance(item["id"], str)
        and bool(item["id"].strip())
        and isinstance(item["topic"], str)
        and item["status"] in VALID_SESSION_STATUSES
        and _valid_string_list(item["followups"])
        for item in value
    ):
        return False
    ids = [item["id"] for item in value]
    return len(ids) == len(set(ids))


def _valid_sessions(value: Any, session_evidence: Any) -> bool:
    expected = {"available", "message", "sessionCount", "activeCount", "latestSession", "openFollowups", "views"}
    if not _has_exact_keys(value, expected) or not _valid_session_evidence(session_evidence):
        return False
    if value["available"] is not True or value["message"] != "":
        return False
    if not _is_non_negative_int(value["sessionCount"]) or not _is_non_negative_int(value["activeCount"]):
        return False
    if value["activeCount"] > value["sessionCount"] or not isinstance(value["openFollowups"], list):
        return False
    if value["sessionCount"] != len(session_evidence):
        return False
    if value["activeCount"] != sum(item["status"] == "active" for item in session_evidence):
        return False
    latest = value["latestSession"]
    if value["sessionCount"] == 0:
        if latest != {} or value["activeCount"] != 0 or value["openFollowups"] != []:
            return False
    elif not _valid_session_record(latest):
        return False
    if value["sessionCount"]:
        latest_active = latest["status"] == "active"
        if latest_active and value["activeCount"] == 0:
            return False
        if value["activeCount"] == value["sessionCount"] and not latest_active:
            return False
        if value["sessionCount"] == 1 and value["activeCount"] != int(latest_active):
            return False
    if not all(_valid_open_followup(item) for item in value["openFollowups"]):
        return False
    expected_open_followups = [
        {
            "sessionId": session["id"],
            "topic": session["topic"],
            "status": session["status"],
            "text": text,
        }
        for session in session_evidence
        for text in session["followups"]
    ]
    if value["openFollowups"] != expected_open_followups:
        return False
    if value["sessionCount"]:
        expected_latest = session_evidence[0]
        if any(
            latest[key] != expected_latest[key]
            for key in ("id", "topic", "status", "followups")
        ):
            return False
    if not _valid_session_views(value["views"], value["sessionCount"]):
        return False
    return not value["sessionCount"] or value["views"]["latestSessionUpdatedAt"] == latest["updatedAt"]


def _valid_graph(
    value: Any,
    *,
    entity_types: dict[str, int],
    edge_types: dict[str, int],
) -> bool:
    from .graph import EDGE_TYPE_TO_GRAPH_EDGE, ENTITY_TYPE_TO_GRAPH_NODE, GRAPH_CONTRACT_VERSION

    expected = {
        "ok", "contractVersion", "sqliteSchemaVersion", "entityTypeCoverage",
        "edgeTypeCoverage", "nodeCounts", "edgeCounts", "suggestionCounts", "semanticChunks",
    }
    if not _has_exact_keys(value, expected):
        return False
    if (
        value["ok"] is not True
        or value["contractVersion"] != GRAPH_CONTRACT_VERSION
        or not _is_non_negative_int(value["sqliteSchemaVersion"])
    ):
        return False
    if not _valid_status_map(value["nodeCounts"]) or not _valid_status_map(value["edgeCounts"]) or not _valid_status_map(value["suggestionCounts"]) or not _is_non_negative_int(value["semanticChunks"]):
        return False
    expected_node_counts: dict[str, int] = {}
    for entity_type, count in entity_types.items():
        graph_type = ENTITY_TYPE_TO_GRAPH_NODE.get(entity_type, "unknown")
        expected_node_counts[graph_type] = expected_node_counts.get(graph_type, 0) + count
    if value["semanticChunks"]:
        expected_node_counts["chunk"] = (
            expected_node_counts.get("chunk", 0) + value["semanticChunks"]
        )

    expected_edge_counts: dict[str, int] = {}
    for edge_type, count in edge_types.items():
        graph_type = EDGE_TYPE_TO_GRAPH_EDGE.get(edge_type, "unknown")
        expected_edge_counts[graph_type] = expected_edge_counts.get(graph_type, 0) + count

    entity_coverage = value["entityTypeCoverage"]
    edge_coverage = value["edgeTypeCoverage"]
    expected_entity_coverage = {
        "knownCurrentTypes": sorted(ENTITY_TYPE_CODES),
        "mappedTypes": sorted(ENTITY_TYPE_TO_GRAPH_NODE),
        "unmappedTypes": sorted(
            entity_type for entity_type in entity_types if entity_type not in ENTITY_TYPE_TO_GRAPH_NODE
        ),
    }
    expected_edge_coverage = {
        "mappedTypes": sorted(EDGE_TYPE_TO_GRAPH_EDGE),
        "unmappedTypes": sorted(
            edge_type for edge_type in edge_types if edge_type not in EDGE_TYPE_TO_GRAPH_EDGE
        ),
    }
    return (
        entity_coverage == expected_entity_coverage
        and edge_coverage == expected_edge_coverage
        and value["nodeCounts"] == dict(sorted(expected_node_counts.items()))
        and value["edgeCounts"] == dict(sorted(expected_edge_counts.items()))
    )


def _valid_file_info(value: Any) -> bool:
    if value == {}:
        return True
    if not isinstance(value, dict):
        return False
    expected = {"path", "exists", "isFile", "isDirectory", "sizeBytes", "updatedAt"}
    if not _has_exact_keys(value, expected):
        return False
    return (
        _valid_project_relative_path(value["path"])
        and value["exists"] is True
        and isinstance(value["isFile"], bool)
        and isinstance(value["isDirectory"], bool)
        and value["isFile"] is not value["isDirectory"]
        and (value["sizeBytes"] is None or _is_non_negative_int(value["sizeBytes"]))
        and ((value["isFile"] and _is_non_negative_int(value["sizeBytes"])) or (value["isDirectory"] and value["sizeBytes"] is None))
        and _valid_timestamp(value["updatedAt"])
    )


def _valid_artifact_directory(value: Any) -> bool:
    expected = {"path", "exists", "fileCount", "latestFile"}
    if not (
        _has_exact_keys(value, expected)
        and _valid_project_relative_path(value["path"])
        and isinstance(value["exists"], bool)
        and _is_non_negative_int(value["fileCount"])
        and _valid_file_info(value["latestFile"])
    ):
        return False
    if not value["exists"] and (value["fileCount"] != 0 or value["latestFile"] != {}):
        return False
    if value["fileCount"] == 0:
        return value["latestFile"] == {}
    latest = value["latestFile"]
    return (
        latest != {}
        and latest["isFile"] is True
        and latest["isDirectory"] is False
        and PurePosixPath(latest["path"]).parent == PurePosixPath(value["path"])
    )


def _valid_views_artifact(value: Any) -> bool:
    expected = {"path", "exists", "fileCount", "lastGeneratedAt", "latestFile", "stale"}
    return (
        _has_exact_keys(value, expected)
        and _valid_artifact_directory({
            "path": value["path"],
            "exists": value["exists"],
            "fileCount": value["fileCount"],
            "latestFile": value["latestFile"],
        })
        and isinstance(value["lastGeneratedAt"], str)
        and isinstance(value["stale"], bool)
    )


def _valid_graph_packets_artifact(value: Any) -> bool:
    expected = {"path", "exists", "fileCount", "latestFile", "implemented", "stale", "note"}
    if not (
        _has_exact_keys(value, expected)
        and _valid_artifact_directory({
            "path": value["path"],
            "exists": value["exists"],
            "fileCount": value["fileCount"],
            "latestFile": value["latestFile"],
        })
        and value["implemented"] is True
        and (value["stale"] is None or isinstance(value["stale"], bool))
        and isinstance(value["note"], str)
    ):
        return False
    if value["fileCount"] == 0:
        return value["stale"] is None
    return value["stale"] is False and value["latestFile"] != {}


def _valid_derived_artifacts(value: Any) -> bool:
    expected = {
        "views", "vectors", "devContextPackets", "graphPackets",
        "projectPlaneViews", "projectPlaneContextPackets", "lastScanAt",
    }
    if not _has_exact_keys(value, expected) or not isinstance(value["lastScanAt"], str):
        return False
    return (
        _valid_views_artifact(value["views"])
        and _valid_vectors(value["vectors"])
        and _valid_artifact_directory(value["devContextPackets"])
        and _valid_graph_packets_artifact(value["graphPackets"])
        and _valid_artifact_directory(value["projectPlaneViews"])
        and _valid_artifact_directory(value["projectPlaneContextPackets"])
    )


def _inventory_markdown_entries(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in inventory["entries"]
        if entry["type"] == "regular" and PurePosixPath(entry["path"]).suffix.lower() == ".md"
    ]


def _inventory_latest_file(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {}
    latest = max(entries, key=lambda entry: entry["mtimeNs"])
    return {
        "path": latest["path"],
        "exists": True,
        "isFile": True,
        "isDirectory": False,
        "sizeBytes": latest["sizeBytes"],
        "updatedAt": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(latest["mtimeNs"] / 1_000_000_000),
        ),
    }


def _artifact_matches_inventory(
    artifact: dict[str, Any],
    inventory: dict[str, Any],
    root: str,
    *,
    graph_packets: bool = False,
) -> bool:
    entries = _inventory_markdown_entries(inventory)
    if graph_packets:
        selected = set(inventory["graphPacketPaths"])
        entries = [entry for entry in entries if entry["path"] in selected]
    return (
        artifact["path"] == root
        and artifact["exists"] is inventory["exists"]
        and artifact["fileCount"] == len(entries)
        and artifact["latestFile"] == _inventory_latest_file(entries)
    )


def _valid_projection_filesystem_coherence(
    legacy_status: dict[str, Any],
    filesystem: dict[str, Any],
) -> bool:
    """Bind every filesystem-derived status fact to the captured inventory."""
    bootstrap = legacy_status["bootstrap"]
    project_plane = bootstrap["projectPlane"]
    derived = bootstrap["derivedArtifacts"]
    inventories = filesystem["inventories"]
    config = filesystem["configFacts"]
    manifest_record = filesystem["contentFiles"][f"{CONTROL_DIRNAME}/{MANIFEST_FILENAME}"]
    manifest = filesystem["manifestFacts"]
    link_record = filesystem["contentFiles"][f"{_CONTROLWORK_DIRNAME}/link.json"]
    link_facts = filesystem["linkFacts"]
    attachment = filesystem["externalAttachment"]
    controlwork_root = filesystem["controlWorkRoot"]
    canonical_context = filesystem["presencePaths"][_CONTROLWORK_CONTEXT_FILENAME]
    dev_plane = bootstrap["devPlane"]

    manifest_exists = manifest_record["exists"]
    if not (
        dev_plane["hasManifest"] is manifest_exists
        and dev_plane["manifest"] == (manifest if manifest_exists else {})
        and dev_plane["initialized"] is (manifest_exists and dev_plane["hasDatabase"])
    ):
        return False

    if not (
        project_plane["hasControlWorkRoot"] is controlwork_root["exists"]
        and project_plane["hasCanonicalContext"] is canonical_context["exists"]
        and project_plane["hasConfig"] is config["exists"]
        and project_plane["canonicalContext"] == _CONTROLWORK_CONTEXT_FILENAME
        and project_plane["distribution"] == (config["distribution"] or "")
        and (
            project_plane["standaloneCompatible"]
            == (config["standaloneCompatible"] if config["exists"] else False)
        )
    ):
        return False

    projected_link = project_plane["link"]
    link_exists = link_record["exists"]
    if projected_link["path"] != f"{_CONTROLWORK_DIRNAME}/link.json":
        return False
    if not link_exists:
        if not (
            link_facts == {}
            and project_plane["attachedExternal"] is False
            and projected_link["exists"] is False
            and projected_link["syncPolicy"] == ""
            and projected_link["externalPath"] == ""
            and projected_link["attachedAt"] == ""
            and projected_link["validation"] == {}
            and attachment["configured"] is False
        ):
            return False
    else:
        raw_external = link_facts.get("externalPath")
        if (
            not isinstance(raw_external, str)
            or not raw_external.strip()
            or any(
                key in link_facts and not isinstance(link_facts[key], str)
                for key in ("syncPolicy", "attachedAt")
            )
        ):
            return False
        try:
            resolved_external = str(Path(raw_external).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            return False
        if not (
            project_plane["attachedExternal"] is True
            and projected_link["exists"] is True
            and projected_link["path"] == f"{_CONTROLWORK_DIRNAME}/link.json"
            and projected_link["syncPolicy"] == str(link_facts.get("syncPolicy") or "")
            and projected_link["externalPath"] == resolved_external
            and projected_link["attachedAt"] == str(link_facts.get("attachedAt") or "")
            and attachment["configured"] is True
            and attachment["root"] == resolved_external
        ):
            return False
        validation = projected_link["validation"]
        external_paths = attachment["paths"]
        expected_validation = {
            "path": resolved_external,
            "hasCanonicalContext": (
                external_paths[_CONTROLWORK_CONTEXT_FILENAME]["exists"]
                and external_paths[_CONTROLWORK_CONTEXT_FILENAME]["type"] == "regular"
            ),
            "hasConfig": (
                external_paths[f"{_CONTROLWORK_DIRNAME}/config.json"]["exists"]
                and external_paths[f"{_CONTROLWORK_DIRNAME}/config.json"]["type"] == "regular"
            ),
            "hasMemoryRoot": (
                external_paths[f"{_CONTROLWORK_DIRNAME}/memory"]["exists"]
                and external_paths[f"{_CONTROLWORK_DIRNAME}/memory"]["type"] == "directory"
            ),
        }
        expected_validation["ok"] = all(
            expected_validation[key]
            for key in ("hasCanonicalContext", "hasConfig", "hasMemoryRoot")
        )
        if validation != expected_validation:
            return False

    for area in _CONTROLWORK_MEMORY_AREAS:
        inventory = inventories[f"{_CONTROLWORK_DIRNAME}/memory/{area}"]
        regular_count = sum(entry["type"] == "regular" for entry in inventory["entries"])
        expected_count = regular_count if inventory["exists"] else None
        if project_plane["memoryCounts"][area] != expected_count:
            return False

    artifact_specs = (
        ("views", f"{CONTROL_DIRNAME}/views", False),
        ("devContextPackets", f"{CONTROL_DIRNAME}/context-packets", False),
        ("graphPackets", f"{CONTROL_DIRNAME}/context-packets", True),
        ("projectPlaneViews", f"{_CONTROLWORK_DIRNAME}/memory/views", False),
        ("projectPlaneContextPackets", f"{_CONTROLWORK_DIRNAME}/context-packets", False),
    )
    if not all(
        _artifact_matches_inventory(
            derived[name],
            inventories[root],
            root,
            graph_packets=graph_packets,
        )
        for name, root, graph_packets in artifact_specs
    ):
        return False

    from .sessions import SESSION_VIEW_FILENAMES

    session_views = legacy_status["sessions"]["views"]
    expected_paths = [
        f"{CONTROL_DIRNAME}/views/{filename}"
        for filename in SESSION_VIEW_FILENAMES
    ]
    regular_view_paths = {
        entry["path"]
        for entry in _inventory_markdown_entries(inventories[f"{CONTROL_DIRNAME}/views"])
    }
    generated_regular_views = sum(path in regular_view_paths for path in expected_paths)
    return (
        session_views["requiredViewCount"] == len(expected_paths)
        and session_views["paths"] == expected_paths
        and session_views["generatedViewCount"] == generated_regular_views
    )


def _valid_link_validation(value: Any) -> bool:
    if value == {}:
        return True
    expected = {"path", "hasCanonicalContext", "hasConfig", "hasMemoryRoot", "ok"}
    return (
        _has_exact_keys(value, expected)
        and isinstance(value["path"], str)
        and all(isinstance(value[key], bool) for key in ("hasCanonicalContext", "hasConfig", "hasMemoryRoot", "ok"))
        and value["ok"] is all(value[key] for key in ("hasCanonicalContext", "hasConfig", "hasMemoryRoot"))
    )


_PROJECT_MEMORY_COUNT_KEYS = {
    "inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy", "views",
}


def _valid_project_plane(value: Any) -> bool:
    expected = {
        "hasControlWorkRoot", "hasCanonicalContext", "hasConfig", "canonicalContext",
        "distribution", "standaloneCompatible", "memoryCounts", "attachedExternal", "link",
    }
    if not _has_exact_keys(value, expected):
        return False
    if not all(isinstance(value[key], bool) for key in ("hasControlWorkRoot", "hasCanonicalContext", "hasConfig", "standaloneCompatible", "attachedExternal")):
        return False
    if not isinstance(value["canonicalContext"], str) or not isinstance(value["distribution"], str):
        return False
    memory_counts = value["memoryCounts"]
    if not _has_exact_keys(memory_counts, _PROJECT_MEMORY_COUNT_KEYS) or not all(
        count is None or _is_non_negative_int(count)
        for count in memory_counts.values()
    ):
        return False
    link = value["link"]
    expected_link = {"exists", "path", "syncPolicy", "externalPath", "attachedAt", "validation"}
    if (
        not _has_exact_keys(link, expected_link)
        or not isinstance(link["exists"], bool)
        or not all(isinstance(link[key], str) for key in ("path", "syncPolicy", "externalPath", "attachedAt"))
        or not _valid_link_validation(link["validation"])
        or link["exists"] is not value["attachedExternal"]
    ):
        return False
    validation = link["validation"]
    if link["externalPath"] == "":
        return validation == {}
    return validation != {} and validation["path"] == link["externalPath"]


def _valid_recommendations(value: Any) -> bool:
    expected = {"reason", "command", "writes"}
    return isinstance(value, list) and all(
        _has_exact_keys(item, expected)
        and isinstance(item["reason"], str)
        and isinstance(item["command"], str)
        and isinstance(item["writes"], bool)
        for item in value
    )


def _valid_manifest(value: Any) -> bool:
    expected = {
        "schema_version", "install_mode", "profile", "project_short", "enabled_modules",
        "work_memory_layout", "created_by", "created_at", "updated_at", "controlcoding_version",
    }
    return (
        _has_exact_keys(value, expected)
        and _is_non_negative_int(value["schema_version"])
        and all(isinstance(value[key], str) for key in (
            "install_mode", "profile", "project_short", "created_by", "created_at", "updated_at", "controlcoding_version",
        ))
        and _valid_string_list(value["enabled_modules"], nonempty_items=True)
        and _valid_string_list(value["work_memory_layout"])
    )


_DEV_PLANE_COUNT_KEYS = {
    "entities", "semanticChunks", "edges", "events", "vectorRows", "applicationMemoryComponents",
}


def _valid_dev_plane(value: Any) -> bool:
    from .graph import GRAPH_CONTRACT_VERSION

    expected = {
        "hasControlDir", "hasManifest", "hasDatabase", "initialized", "manifest",
        "databasePath", "graphContractVersion", "sqliteSchemaVersion", "schema", "counts",
        "entityTypes", "lifecycles", "suggestionCounts",
    }
    if not _has_exact_keys(value, expected):
        return False
    if not all(isinstance(value[key], bool) for key in ("hasControlDir", "hasManifest", "hasDatabase", "initialized")):
        return False
    if value["hasControlDir"] is not True:
        return False
    if value["initialized"] != (value["hasManifest"] and value["hasDatabase"]):
        return False
    if not all(isinstance(value[key], str) for key in ("databasePath", "graphContractVersion", "sqliteSchemaVersion")):
        return False
    if (
        not _valid_project_relative_path(value["databasePath"])
        or value["graphContractVersion"] != GRAPH_CONTRACT_VERSION
    ):
        return False
    manifest = value["manifest"]
    if (value["hasManifest"] and not _valid_manifest(manifest)) or (not value["hasManifest"] and manifest != {}):
        return False
    counts = value["counts"]
    return (
        _has_exact_keys(counts, _DEV_PLANE_COUNT_KEYS)
        and all(_is_non_negative_int(count) for count in counts.values())
        and _valid_schema(value["schema"])
        and _valid_status_map(value["entityTypes"])
        and _valid_status_map(value["lifecycles"])
        and set(value["lifecycles"]).issubset(VALID_LIFECYCLES)
        and _valid_status_map(value["suggestionCounts"])
    )


def _valid_application_owned_memory(value: Any) -> bool:
    expected = {"referencedComponents", "records", "boundary"}
    return (
        _has_exact_keys(value, expected)
        and _is_non_negative_int(value["referencedComponents"])
        and _valid_entity_list(value["records"])
        and all(record["type"] == "application_memory_component" for record in value["records"])
        and isinstance(value["boundary"], str)
    )


def _valid_bootstrap(value: Any) -> bool:
    expected = {
        "ok", "projectRoot", "scope", "topic", "mode", "devPlane", "projectPlane",
        "applicationOwnedMemory", "derivedArtifacts", "hotDocuments", "activeFocus",
        "recommendations", "warnings",
    }
    return (
        _has_exact_keys(value, expected)
        and value["ok"] is True
        and all(isinstance(value[key], str) for key in ("projectRoot", "scope", "topic", "mode"))
        and _valid_dev_plane(value["devPlane"])
        and _valid_project_plane(value["projectPlane"])
        and _valid_application_owned_memory(value["applicationOwnedMemory"])
        and _valid_derived_artifacts(value["derivedArtifacts"])
        and _valid_entity_list(value["hotDocuments"])
        and _valid_entity_list(value["activeFocus"])
        and _valid_recommendations(value["recommendations"])
        and _valid_string_list(value["warnings"])
    )


def _valid_legacy_status(
    value: Any,
    *,
    session_evidence: Any,
    graph_evidence: Any,
) -> bool:
    from .lifecycle import LIFECYCLE_ATTENTION_STATES

    expected = {
        "ok", "projectShort", "schema", "entities", "lifecycleAttention", "staleOrNeedsReview", "byType", "byLifecycle",
        "lastScanAt", "lastViewsGeneratedAt", "generatedViews", "vectors", "sessions", "graph", "hotDocuments", "bootstrap",
    }
    if not _has_exact_keys(value, expected) or value["ok"] is not True or not isinstance(value["projectShort"], str):
        return False
    if not all(_is_non_negative_int(value[key]) for key in ("entities", "lifecycleAttention", "staleOrNeedsReview", "generatedViews")):
        return False
    if not isinstance(value["lastScanAt"], str) or not isinstance(value["lastViewsGeneratedAt"], str):
        return False
    if (
        not _valid_schema(value["schema"])
        or not _valid_status_map(value["byType"])
        or not _valid_status_map(value["byLifecycle"])
        or not _valid_vectors(value["vectors"])
        or not _valid_sessions(value["sessions"], session_evidence)
        or not _valid_entity_list(value["hotDocuments"])
        or not _valid_bootstrap(value["bootstrap"])
    ):
        return False
    if (
        not _has_exact_keys(graph_evidence, {"entityTypes", "edgeTypes"})
        or not _valid_status_map(graph_evidence["entityTypes"])
        or not _valid_status_map(graph_evidence["edgeTypes"])
        or graph_evidence["entityTypes"] != value["byType"]
    ):
        return False
    bootstrap = value["bootstrap"]
    dev_plane = bootstrap["devPlane"]
    derived = bootstrap["derivedArtifacts"]
    app_memory = bootstrap["applicationOwnedMemory"]
    graph = value["graph"]
    counts = dev_plane["counts"]
    entities = value["entities"]
    views = derived["views"]
    if not _valid_graph(
        graph,
        entity_types=graph_evidence["entityTypes"],
        edge_types=graph_evidence["edgeTypes"],
    ):
        return False
    canonical_attention = sum(
        value["byLifecycle"].get(lifecycle, 0)
        for lifecycle in LIFECYCLE_ATTENTION_STATES
    )
    return (
        value["hotDocuments"] == bootstrap["hotDocuments"]
        and value["schema"] == dev_plane["schema"]
        and entities == counts["entities"]
        and sum(value["byType"].values()) == entities
        and sum(value["byLifecycle"].values()) == entities
        and set(value["byLifecycle"]).issubset(VALID_LIFECYCLES)
        and value["lifecycleAttention"] == canonical_attention
        and value["staleOrNeedsReview"] == canonical_attention
        and value["byType"] == dev_plane["entityTypes"]
        and value["byLifecycle"] == dev_plane["lifecycles"]
        and value["vectors"] == derived["vectors"]
        and value["lastScanAt"] == derived["lastScanAt"]
        and value["lastViewsGeneratedAt"] == derived["views"]["lastGeneratedAt"]
        and views["stale"] is bool(entities and (views["fileCount"] == 0 or not views["lastGeneratedAt"]))
        and app_memory["referencedComponents"] == counts["applicationMemoryComponents"]
        and counts["applicationMemoryComponents"] <= entities
        and len(app_memory["records"]) <= app_memory["referencedComponents"]
        and (app_memory["referencedComponents"] != 0 or app_memory["records"] == [])
        and graph["semanticChunks"] == counts["semanticChunks"]
        and value["vectors"]["semanticChunks"] == counts["semanticChunks"]
        and sum(graph["nodeCounts"].values()) == counts["entities"] + counts["semanticChunks"]
        and sum(graph["edgeCounts"].values()) == counts["edges"]
        and graph["suggestionCounts"] == dev_plane["suggestionCounts"]
        and graph["contractVersion"] == dev_plane["graphContractVersion"]
        and graph["sqliteSchemaVersion"] == dev_plane["schema"]["targetVersion"]
        and dev_plane["sqliteSchemaVersion"] == str(graph["sqliteSchemaVersion"])
    )


def _validate_projection(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if not payload:
        return None, "projection_malformed"
    expected = {
        "schemaVersion", "kind", "sourceOfTruth", "databaseFingerprint",
        "filesystemFingerprint", "sessionEvidence", "graphEvidence", "legacyStatus",
    }
    if set(payload) != expected:
        return None, "projection_incoherent"
    if not _is_int(payload.get("schemaVersion")) or payload["schemaVersion"] != HEALTH_PROJECTION_SCHEMA_VERSION:
        return None, "projection_schema_incompatible"
    if payload.get("kind") != HEALTH_PROJECTION_KIND or payload.get("sourceOfTruth") is not False:
        return None, "projection_incoherent"
    fingerprint = payload.get("databaseFingerprint")
    filesystem = payload.get("filesystemFingerprint")
    legacy_status = payload.get("legacyStatus")
    if (
        not _valid_fingerprint(fingerprint)
        or not _valid_filesystem_fingerprint(filesystem)
        or not _valid_legacy_status(
            legacy_status,
            session_evidence=payload["sessionEvidence"],
            graph_evidence=payload["graphEvidence"],
        )
    ):
        return None, "projection_incoherent"
    if not legacy_status["bootstrap"]["devPlane"]["hasDatabase"]:
        return None, "projection_incoherent"
    if not _valid_projection_filesystem_coherence(legacy_status, filesystem):
        return None, "projection_incoherent"
    return payload, ""


_CAPABILITY_CHECKOUTS = {"controlcoding_lab", "controlcoding_v1", "controlwork_standalone"}
_CAPABILITY_NAMES = {"memoryHealthProjection", "controlWorkStandaloneVector", "vector"}


def _capability_profile(project: Path) -> tuple[dict[str, Any], str]:
    """Read a profile strictly, distinguishing absence from malformed input."""
    path = capability_profile_path(project)
    try:
        exists = path.exists()
    except OSError:
        return {}, "capability_profile_unreadable"
    if not exists:
        return {}, ""
    profile, reason = _read_bounded_json_object(
        path,
        maximum_bytes=_OBSERVER_JSON_MAX_BYTES,
        unreadable_reason="capability_profile_unreadable",
        malformed_reason="capability_profile_malformed",
        too_large_reason="capability_profile_malformed",
    )
    if reason or profile is None:
        return {}, reason
    if set(profile) != {"schemaVersion", "checkout", "capabilities"}:
        return {}, "capability_profile_incompatible"
    if not _is_int(profile["schemaVersion"]) or profile["schemaVersion"] != CAPABILITY_PROFILE_SCHEMA_VERSION:
        return {}, "capability_profile_incompatible"
    if not isinstance(profile["checkout"], str) or profile["checkout"] not in _CAPABILITY_CHECKOUTS:
        return {}, "capability_profile_incompatible"
    capabilities = profile["capabilities"]
    if not isinstance(capabilities, dict) or not capabilities or not set(capabilities).issubset(_CAPABILITY_NAMES):
        return {}, "capability_profile_incompatible"
    if "vector" in capabilities and "controlWorkStandaloneVector" in capabilities:
        return {}, "capability_profile_incompatible"
    if not all(isinstance(value, str) and value in {"available", "not_applicable"} for value in capabilities.values()):
        return {}, "capability_profile_incompatible"
    return profile, ""


def _capabilities(profile: dict[str, Any]) -> dict[str, str]:
    configured = profile.get("capabilities", {}) if isinstance(profile, dict) else {}
    return {
        "memoryHealthProjection": str(configured.get("memoryHealthProjection", "available")),
        "controlWorkStandaloneVector": str(configured.get("controlWorkStandaloneVector", configured.get("vector", "available"))),
    }


def _not_applicable_observation(project: Path, profile: dict[str, Any], capabilities: dict[str, str]) -> dict[str, Any] | None:
    if profile.get("checkout") == "controlcoding_v1" and capabilities.get("memoryHealthProjection") == "not_applicable":
        return {
            "state": "not_applicable", "color": "GRAY", "reason": "capability_profile_not_applicable",
            "message": "Memory health projection is not applicable for this explicitly profiled checkout.",
            "remediation": "Use the capability set supported by this checkout.",
            "readOnly": True, "sourceOfTruth": False, "projectionPath": str(projection_path(project)),
            "legacyStatus": None, "capabilities": capabilities,
        }
    return None


def _unknown_observation(project: Path, capabilities: dict[str, str], reason: str) -> dict[str, Any]:
    return {
        "state": "unknown", "color": "YELLOW", "reason": reason,
        "message": "Memory health is unknown because the derived projection is missing, incompatible, unstable, or no longer matches its SQLite and filesystem inputs.",
        "remediation": "Run a successful explicit mutating memory command to publish a new health projection. No automatic scan, rebuild, repair, checkpoint, or sync was run.",
        "readOnly": True, "sourceOfTruth": False, "projectionPath": str(projection_path(project)),
        "legacyStatus": None, "capabilities": capabilities,
    }


def observe_freshness(project: Path) -> dict[str, Any]:
    """Read and validate health without SQLite, locking, repair, or writes."""
    project = project.resolve()
    profile, profile_reason = _capability_profile(project)
    capabilities = _capabilities(profile)
    if profile_reason:
        return _unknown_observation(project, capabilities, profile_reason)
    not_applicable = _not_applicable_observation(project, profile, capabilities)
    if not_applicable:
        return not_applicable
    first, reason = capture_source_fingerprint(project)
    if reason:
        return _unknown_observation(project, capabilities, reason)
    filesystem_first, reason = capture_filesystem_fingerprint(project)
    if reason:
        return _unknown_observation(project, capabilities, reason)
    path = projection_path(project)
    try:
        projection_is_file = path.is_file()
        projection_exists = path.exists()
    except OSError:
        return _unknown_observation(project, capabilities, "projection_unreadable")
    if projection_is_file:
        payload, reason = _read_projection_object(path)
        if reason:
            return _unknown_observation(project, capabilities, reason)
    else:
        payload = {}
    if not payload:
        return _unknown_observation(project, capabilities, "projection_missing" if not projection_exists else "projection_malformed")
    try:
        validated, reason = _validate_projection(payload)
    except (KeyError, TypeError, ValueError, OSError, OverflowError, RecursionError):
        validated, reason = None, "projection_incoherent"
    if reason:
        return _unknown_observation(project, capabilities, reason)
    second, reason = capture_source_fingerprint(project)
    if reason:
        return _unknown_observation(project, capabilities, reason)
    filesystem_second, reason = capture_filesystem_fingerprint(project)
    if reason:
        return _unknown_observation(project, capabilities, reason)
    if first != second:
        return _unknown_observation(project, capabilities, "source_changed_during_validation")
    if filesystem_first != filesystem_second:
        return _unknown_observation(project, capabilities, "filesystem_changed_during_validation")
    if (
        validated is None
        or validated["databaseFingerprint"] != first
        or validated["filesystemFingerprint"] != filesystem_first
    ):
        return _unknown_observation(project, capabilities, "projection_outdated")
    return {
        "state": "fresh", "color": "GREEN", "reason": "",
        "message": "Health projection matches the current stable SQLite database, WAL, and filesystem-derived inputs.", "remediation": "",
        "readOnly": True, "sourceOfTruth": False, "projectionPath": str(path),
        "legacyStatus": validated["legacyStatus"], "capabilities": capabilities,
    }


def projected_status_payload(project: Path) -> dict[str, Any]:
    observation = observe_freshness(project)
    if observation["state"] == "fresh":
        payload = dict(observation["legacyStatus"])
        payload.update({"available": True, "readOnly": True, "hiddenWrites": False, "health": observation})
        return payload
    return {
        "ok": True, "available": False, "readOnly": True, "hiddenWrites": False,
        "health": observation,
    }


def projected_retrieval_payload(
    project: Path,
    topic: str,
    scope: str,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observation = observation or observe_freshness(project)
    return {
        "ok": True, "available": False,
        "message": "Startup does not query SQLite. Run the explicit memory retrieve command after reviewing health.",
        "query": topic, "scope": scope, "matches": None, "health": observation,
    }
