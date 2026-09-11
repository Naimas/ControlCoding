"""RAG-O, the read-only operations index for memory and RAG routing."""

from __future__ import annotations

import datetime
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .commands import _work_status_payload
from .freshness_projection import (
    capture_project_plane_inputs,
    observe_freshness,
    observe_project_plane_inputs,
)
from .store import _print_json_or_text


LOCAL_ONLY_PATTERNS = [
    ".controlcoding/**",
    ".controlwork/**",
    ".tmp_pytest_*",
    ".tmp-pytest-*",
    "generated Dev Plane views",
    "generated Project Plane views",
    "context packets",
    "SQLite memory databases",
]

RELEASE_EXPORT_EXCLUSIONS = [
    ".controlcoding/**",
    ".controlwork/**",
    "CONTROLWORK.md",
]

STARTUP_INTENTS = {"ask", "continue_previous_work", "start_new_work"}
_FEATURE_REGISTRY_RELATIVE_PATH = Path(".controlcoding") / "features" / "features.json"
_FEATURE_REGISTRY_MAX_BYTES = 1024 * 1024


def _shell_quote(text: str) -> str:
    cleaned = str(text or "").replace('"', '\\"')
    return f'"{cleaned}"'


def _topic_slug(topic: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", topic.strip()).strip("-").lower()
    return slug[:48] or "current-focus"


def _git_root(project: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "isProjectGitRoot": False,
            "root": "",
            "message": f"git root unavailable: {exc}",
        }
    if result.returncode != 0:
        return {
            "available": False,
            "isProjectGitRoot": False,
            "root": "",
            "message": (result.stderr or result.stdout or "not a git repository").strip(),
        }
    root = Path(result.stdout.strip()).resolve()
    project_root = project.resolve()
    return {
        "available": True,
        "isProjectGitRoot": root == project_root,
        "root": str(root),
        "message": "" if root == project_root else "project root is inside another git worktree",
    }


def _parse_git_status_line(line: str) -> dict[str, Any]:
    status = line[:2]
    path = line[3:] if len(line) > 3 else ""
    return {
        "status": status,
        "path": path,
        "staged": status[0] not in (" ", "?"),
        "unstaged": status[1] not in (" ", "?"),
        "untracked": status == "??",
    }


def _working_tree(project: Path) -> dict[str, Any]:
    def unavailable(root_payload: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "isProjectGitRoot": root_payload["isProjectGitRoot"],
            "root": root_payload["root"],
            "message": root_payload["message"],
            "trackedDirtyCount": None,
            "changes": None,
            "health": {
                "state": "unknown",
                "reason": reason,
                "message": root_payload["message"],
            },
            "commitPolicy": {
                "reviewBeforeCommit": [],
                "doNotCommit": LOCAL_ONLY_PATTERNS,
                "releaseExportExclusions": RELEASE_EXPORT_EXCLUSIONS,
            },
        }

    root = _git_root(project)
    if not root["available"] or not root["isProjectGitRoot"]:
        return unavailable(root, "git_root_unavailable" if not root["available"] else "git_root_not_project")
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "status", "--short"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        failed_root = {**root, "message": f"git status unavailable: {exc}"}
        return unavailable(failed_root, "git_status_unavailable")
    if result.returncode != 0:
        failed_root = {**root, "message": (result.stderr or result.stdout or "git status failed").strip()}
        return unavailable(failed_root, "git_status_failed")
    changes = [
        _parse_git_status_line(line)
        for line in result.stdout.splitlines()
        if line.strip()
    ]
    return {
        "available": True,
        "isProjectGitRoot": True,
        "root": root["root"],
        "message": "",
        "trackedDirtyCount": len(changes),
        "changes": changes,
        "health": {"state": "observed", "reason": "", "message": ""},
        "commitPolicy": {
            "reviewBeforeCommit": [item["path"] for item in changes],
            "doNotCommit": LOCAL_ONLY_PATTERNS,
            "releaseExportExclusions": RELEASE_EXPORT_EXCLUSIONS,
        },
    }


def _graph_status(observation: dict[str, Any]) -> dict[str, Any]:
    status = observation.get("legacyStatus", {})
    if observation.get("state") != "fresh":
        return {
            "available": False,
            "ok": True,
            "message": observation.get("message", "Memory health is unknown."),
            "suggestionCounts": None,
            "semanticChunks": None,
            "health": observation,
        }
    projected = status.get("graph", {}) if isinstance(status.get("graph"), dict) else {}
    vectors = status.get("vectors", {}) if isinstance(status.get("vectors"), dict) else {}
    return {
        **projected,
        "available": True,
        "ok": bool(projected.get("ok", status.get("ok", True))),
        "message": "",
        "suggestionCounts": projected.get("suggestionCounts", {}),
        "semanticChunks": projected.get("semanticChunks", vectors.get("semanticChunks")),
        "health": observation,
    }


def _safe_startup_payload(label: str, builder):
    try:
        return builder()
    except Exception as exc:  # defensive aggregation for startup diagnostics
        return {
            "ok": False,
            "available": False,
            "label": label,
            "message": str(exc),
        }


def _feature_status_unavailable_payload(
    exc: BaseException,
    *,
    reason: str = "feature_status_unavailable",
) -> dict[str, Any]:
    error = {
        "type": type(exc).__name__,
        "message": str(exc),
    }
    warning = {
        "code": "feature_status_unavailable",
        "message": "Feature status could not be observed by the read-only startup adapter.",
        "error": error,
    }
    return {
        "initialized": None,
        "available": False,
        "features": None,
        "summary": None,
        "currentFeature": None,
        "blockedFeatureIds": None,
        "warnings": [warning],
        "error": error,
        "health": {
            "state": "unknown",
            "color": "YELLOW",
            "reason": reason,
            "message": warning["message"],
        },
    }


def _feature_registry_identity(value: os.stat_result) -> tuple[int, int] | None:
    device = getattr(value, "st_dev", None)
    inode = getattr(value, "st_ino", None)
    if (
        not isinstance(device, int)
        or isinstance(device, bool)
        or device < 0
        or not isinstance(inode, int)
        or isinstance(inode, bool)
        or inode <= 0
    ):
        return None
    return device, inode


def _feature_registry_signature(value: os.stat_result) -> tuple[object, ...] | None:
    identity = _feature_registry_identity(value)
    if not stat.S_ISREG(value.st_mode) or identity is None:
        return None
    return identity, int(value.st_size), int(value.st_mtime_ns)


def _reject_feature_registry_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _capture_feature_registry(project: Path) -> tuple[Any, dict[str, Any] | None, str]:
    """Read the feature registry from one stable regular-file descriptor."""
    path = project / _FEATURE_REGISTRY_RELATIVE_PATH
    descriptor: int | None = None
    try:
        path_before = os.stat(path, follow_symlinks=True)
    except FileNotFoundError:
        return None, {"exists": False}, ""
    except OSError:
        return None, None, "feature_registry_unreadable"
    signature = _feature_registry_signature(path_before)
    if signature is None:
        if stat.S_ISREG(path_before.st_mode):
            return None, None, "feature_registry_identity_unavailable"
        return None, None, "feature_registry_wrong_type"

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        descriptor_before = os.fstat(descriptor)
        if _feature_registry_signature(descriptor_before) != signature:
            return None, None, "feature_registry_unstable"
        raw = bytearray()
        while len(raw) <= _FEATURE_REGISTRY_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, _FEATURE_REGISTRY_MAX_BYTES + 1 - len(raw)),
            )
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > _FEATURE_REGISTRY_MAX_BYTES:
            return None, None, "feature_registry_too_large"
        descriptor_after = os.fstat(descriptor)
        if _feature_registry_signature(descriptor_after) != signature:
            return None, None, "feature_registry_unstable"
        try:
            path_after = os.stat(path, follow_symlinks=True)
        except FileNotFoundError:
            return None, None, "feature_registry_unstable"
        except OSError:
            return None, None, "feature_registry_unreadable"
        if _feature_registry_signature(path_after) != signature:
            return None, None, "feature_registry_unstable"
        raw_bytes = bytes(raw)
        try:
            payload = json.loads(
                raw_bytes.decode("utf-8"),
                parse_constant=_reject_feature_registry_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            return None, None, "feature_registry_malformed"
        return payload, {
            "exists": True,
            "identity": signature[0],
            "sizeBytes": signature[1],
            "mtimeNs": signature[2],
            "raw": raw_bytes,
        }, ""
    except OSError:
        return None, None, "feature_registry_unreadable"
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _valid_canonical_feature_id(value: Any) -> bool:
    """Accept exactly the IDs that the canonical feature producer can store."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 80
        and value == value.lower()
        and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value) is not None
        and (len(value) == 80 or value[-1] not in "-._")
    )


def _valid_feature_timestamp(value: Any, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    if not value:
        return allow_empty
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _canonical_feature_receipt_id(created_at: str) -> str:
    return "feature_verify_" + created_at.replace(":", "").replace(".", "")


def _valid_feature_receipt_reference(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"feature_verify_(\d{4}-\d{2}-\d{2}T\d{9}Z)", value)
    if match is None:
        return False
    compact = match.group(1)
    timestamp = (
        f"{compact[:13]}:{compact[13:15]}:{compact[15:17]}."
        f"{compact[17:20]}Z"
    )
    return _valid_feature_timestamp(timestamp)


def _valid_feature_run_check(result: Any) -> bool:
    expected = {
        "command", "status", "returnCode", "durationMs", "stdoutTail", "stderrTail",
    }
    if not isinstance(result, dict) or set(result) != expected:
        return False
    command = result.get("command")
    status_value = result.get("status")
    return_code = result.get("returnCode")
    duration_ms = result.get("durationMs")
    if (
        not isinstance(command, str)
        or not command.strip()
        or status_value not in {"passed", "failed"}
        or not isinstance(return_code, int)
        or isinstance(return_code, bool)
        or not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms < 0
        or not isinstance(result.get("stdoutTail"), str)
        or not isinstance(result.get("stderrTail"), str)
    ):
        return False
    return (status_value == "passed") is (return_code == 0)


def _valid_feature_receipt(receipt: Any) -> bool:
    expected = {"id", "createdAt", "status", "runChecks", "evidence"}
    if not isinstance(receipt, dict) or set(receipt) != expected:
        return False
    receipt_id = receipt.get("id")
    created_at = receipt.get("createdAt")
    status_value = receipt.get("status")
    run_checks = receipt.get("runChecks")
    evidence = receipt.get("evidence")
    if (
        not isinstance(receipt_id, str)
        or not isinstance(created_at, str)
        or not _valid_feature_timestamp(created_at)
        or not _valid_feature_receipt_reference(receipt_id)
        or receipt_id != _canonical_feature_receipt_id(created_at)
        or status_value not in {"passed", "failed", "manual_evidence_recorded"}
        or not isinstance(run_checks, list)
        or not all(_valid_feature_run_check(result) for result in run_checks)
        or not isinstance(evidence, list)
        or not all(isinstance(item, str) and item.strip() for item in evidence)
    ):
        return False
    if run_checks:
        expected_status = (
            "failed"
            if any(result["status"] == "failed" for result in run_checks)
            else "passed"
        )
        return status_value == expected_status
    return status_value == "manual_evidence_recorded" and bool(evidence)


def _valid_feature_event(
    event: Any,
    *,
    receipt_statuses: dict[str, set[str]],
    oldest_retained_receipt_id: str,
    receipts_at_retention_limit: bool,
) -> bool:
    expected = {"at", "kind", "message", "data"}
    if not isinstance(event, dict) or set(event) != expected:
        return False
    if not _valid_feature_timestamp(event.get("at")):
        return False
    kind = event.get("kind")
    message = event.get("message")
    data = event.get("data")
    if not isinstance(message, str) or not message.strip() or not isinstance(data, dict):
        return False
    if kind == "start":
        return message == "feature started" and data == {}
    if kind == "restart":
        return message == "feature work restarted" and data == {}
    if kind == "complete":
        return (
            message == "feature completed"
            and set(data) == {"summary"}
            and isinstance(data.get("summary"), str)
            and data["summary"] == data["summary"].strip()
        )
    if kind == "abort":
        return (
            message == "feature aborted"
            and set(data) == {"reason"}
            and isinstance(data.get("reason"), str)
            and bool(data["reason"].strip())
            and data["reason"] == data["reason"].strip()
        )
    if kind != "verify" or set(data) != {"receiptId"}:
        return False
    receipt_id = data.get("receiptId")
    if not _valid_feature_receipt_reference(receipt_id):
        return False
    prefix = "verification recorded with state "
    verification_state = message[len(prefix):] if message.startswith(prefix) else ""
    expected_receipt_status = {
        "passing": "passed",
        "blocked": "failed",
        "verifying": "manual_evidence_recorded",
    }.get(verification_state)
    if expected_receipt_status is None or message != prefix + verification_state:
        return False
    known_statuses = receipt_statuses.get(receipt_id, set())
    if known_statuses:
        return expected_receipt_status in known_statuses
    return (
        receipts_at_retention_limit
        and bool(oldest_retained_receipt_id)
        and receipt_id < oldest_retained_receipt_id
    )


def _valid_feature_registry(
    registry: Any,
    *,
    schema_version: str,
    states: set[str] | frozenset[str],
    in_progress_states: set[str] | frozenset[str],
) -> bool:
    expected = {"schemaVersion", "createdAt", "updatedAt", "wipLimit", "features"}
    if not isinstance(registry, dict) or set(registry) != expected:
        return False
    wip_limit = registry.get("wipLimit")
    features = registry.get("features")
    if (
        registry.get("schemaVersion") != schema_version
        or not _valid_feature_timestamp(registry.get("createdAt"))
        or not isinstance(wip_limit, int)
        or isinstance(wip_limit, bool)
        or wip_limit < 1
        or not isinstance(features, list)
    ):
        return False
    updated_at = registry.get("updatedAt")
    if not _valid_feature_timestamp(
        updated_at,
        allow_empty=not features,
    ):
        return False

    identifiers: set[str] = set()
    in_progress_count = 0
    base_record_keys = {
        "id", "title", "state", "objective", "scope", "acceptanceCriteria",
        "nonGoals", "verificationChecks", "verificationReceipts", "events",
        "createdAt", "updatedAt",
    }
    terminal_record_keys = {"completedAt", "completionSummary", "abortedAt", "abortReason"}
    for record in features:
        if not isinstance(record, dict) or not base_record_keys.issubset(record):
            return False
        feature_id = record.get("id")
        state = record.get("state")
        if (
            not isinstance(feature_id, str)
            or not _valid_canonical_feature_id(feature_id)
            or feature_id in identifiers
            or not isinstance(state, str)
            or state not in states
        ):
            return False
        identifiers.add(feature_id)
        if state in in_progress_states:
            in_progress_count += 1
        if not all(
            isinstance(record.get(key), str) and record[key].strip()
            for key in ("title", "objective")
        ):
            return False
        if (
            not _valid_feature_timestamp(record.get("createdAt"))
            or not _valid_feature_timestamp(record.get("updatedAt"))
            or not isinstance(record.get("scope"), str)
        ):
            return False
        for key in ("acceptanceCriteria", "nonGoals", "verificationChecks"):
            values = record.get(key)
            if not isinstance(values, list) or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                return False
        if not record["acceptanceCriteria"] or not record["verificationChecks"]:
            return False
        receipts = record.get("verificationReceipts")
        events = record.get("events")
        if (
            not isinstance(receipts, list)
            or len(receipts) > 50
            or not all(_valid_feature_receipt(item) for item in receipts)
            or not isinstance(events, list)
            or len(events) > 100
        ):
            return False
        receipt_statuses: dict[str, set[str]] = {}
        for receipt in receipts:
            receipt_statuses.setdefault(receipt["id"], set()).add(receipt["status"])
        oldest_retained_receipt_id = min(receipt_statuses, default="")
        if not all(
            _valid_feature_event(
                item,
                receipt_statuses=receipt_statuses,
                oldest_retained_receipt_id=oldest_retained_receipt_id,
                receipts_at_retention_limit=len(receipts) == 50,
            )
            for item in events
        ):
            return False

        record_keys = set(record)
        if not record_keys <= base_record_keys | terminal_record_keys:
            return False
        completed_at = record.get("completedAt")
        completion_summary = record.get("completionSummary")
        aborted_at = record.get("abortedAt")
        abort_reason = record.get("abortReason")
        if (
            ("completedAt" in record and not _valid_feature_timestamp(completed_at))
            or (
                "completionSummary" in record
                and (
                    "completedAt" not in record
                    or not isinstance(completion_summary, str)
                    or not completion_summary.strip()
                )
            )
            or (("abortedAt" in record) is not ("abortReason" in record))
            or (
                "abortedAt" in record
                and (
                    not _valid_feature_timestamp(aborted_at)
                    or not isinstance(abort_reason, str)
                    or not abort_reason.strip()
                )
            )
        ):
            return False
        if state == "completed":
            terminal_summary = events[-1]["data"].get("summary") if events else None
            if (
                "completedAt" not in record
                or completed_at != record["updatedAt"]
                or not receipts
                or len(events) < 2
                or events[-1]["kind"] != "complete"
                or events[-2]["kind"] != "verify"
                or events[-2]["data"]["receiptId"] != receipts[-1]["id"]
                or events[-2]["message"] != "verification recorded with state passing"
                or (
                    terminal_summary
                    and completion_summary != terminal_summary
                )
            ):
                return False
        elif state == "aborted":
            if (
                "abortedAt" not in record
                or aborted_at != record["updatedAt"]
                or not events
                or events[-1]["kind"] != "abort"
                or events[-1]["data"]["reason"] != abort_reason
            ):
                return False
        elif state == "active" and (
            not events or events[-1]["kind"] not in {"start", "restart"}
        ):
            return False

        expected_receipt_status = {
            "verifying": "manual_evidence_recorded",
            "passing": "passed",
            "blocked": "failed",
            "completed": "passed",
        }.get(state)
        if expected_receipt_status is not None and (
            not receipts or receipts[-1]["status"] != expected_receipt_status
        ):
            return False
        if state in {"verifying", "passing", "blocked"}:
            if (
                not events
                or events[-1]["kind"] != "verify"
                or events[-1]["data"]["receiptId"] != receipts[-1]["id"]
                or events[-1]["message"] != f"verification recorded with state {state}"
            ):
                return False
    return in_progress_count <= wip_limit


def _feature_status_matches_registry(
    status: Any,
    registry: dict[str, Any],
    *,
    initialized: bool,
    schema_version: str,
    states: set[str] | frozenset[str],
    in_progress_states: set[str] | frozenset[str],
) -> bool:
    if not isinstance(status, dict):
        return False
    features = registry["features"]
    counts = {state: 0 for state in sorted(states)}
    in_progress: list[dict[str, Any]] = []
    blocked: list[str] = []
    for record in features:
        state = record["state"]
        counts[state] += 1
        if state in in_progress_states:
            in_progress.append(record)
        if state == "blocked":
            blocked.append(record["id"])
    expected_summary = {
        "total": len(features),
        "counts": counts,
        "inProgressCount": len(in_progress),
        "activeFeatureIds": [record["id"] for record in in_progress],
        "wipLimit": registry["wipLimit"],
    }
    return (
        status.get("ok") is True
        and status.get("readOnly") is True
        and status.get("initialized") is initialized
        and status.get("schemaVersion") == schema_version
        and status.get("features") == features
        and status.get("summary") == expected_summary
        and status.get("currentFeature") == (in_progress[0] if in_progress else {})
        and status.get("blockedFeatureIds") == blocked
        and status.get("issues") == []
        and isinstance(status.get("commands"), dict)
    )


def _observed_feature_status_payload(project: Path) -> dict[str, Any]:
    try:
        from cc_feature import (
            FEATURE_IN_PROGRESS_STATES,
            FEATURE_STATE_SCHEMA_VERSION,
            FEATURE_STATES,
            feature_status_payload,
        )
    except ImportError as exc:
        return _feature_status_unavailable_payload(exc)

    registry, before, reason = _capture_feature_registry(project)
    if reason or before is None:
        return _feature_status_unavailable_payload(
            ValueError(reason or "feature registry could not be captured"),
            reason=reason or "feature_registry_unreadable",
        )
    initialized = before.get("exists") is True
    if initialized:
        if not _valid_feature_registry(
            registry,
            schema_version=FEATURE_STATE_SCHEMA_VERSION,
            states=FEATURE_STATES,
            in_progress_states=FEATURE_IN_PROGRESS_STATES,
        ):
            return _feature_status_unavailable_payload(
                ValueError("feature registry schema or records are invalid"),
                reason="feature_registry_invalid",
            )
        validated_registry = registry
    else:
        validated_registry = {
            "wipLimit": 1,
            "features": [],
        }
    try:
        status = feature_status_payload(project)
    except Exception as exc:
        return _feature_status_unavailable_payload(exc)
    registry_after, after, reason = _capture_feature_registry(project)
    if reason or after is None or after != before:
        return _feature_status_unavailable_payload(
            ValueError(reason or "feature registry changed while status was observed"),
            reason=reason or "feature_registry_unstable",
        )
    if initialized and registry_after != registry:
        return _feature_status_unavailable_payload(
            ValueError("feature registry changed while status was observed"),
            reason="feature_registry_unstable",
        )
    if not _feature_status_matches_registry(
        status,
        validated_registry,
        initialized=initialized,
        schema_version=FEATURE_STATE_SCHEMA_VERSION,
        states=FEATURE_STATES,
        in_progress_states=FEATURE_IN_PROGRESS_STATES,
    ):
        return _feature_status_unavailable_payload(
            ValueError("feature status adapter returned data inconsistent with the observed registry"),
            reason="feature_registry_invalid",
        )
    return status


def _feature_status_payload(project: Path) -> dict[str, Any]:
    """Return feature status only when its stable registry and adapter agree."""
    try:
        return _observed_feature_status_payload(project)
    except Exception as exc:
        return _feature_status_unavailable_payload(exc)


def _project_plane_unknown_payload(
    project: Path,
    observation: dict[str, Any] | None = None,
    *,
    reason: str = "",
    message: str = "",
) -> dict[str, Any]:
    """Return a null-preserving work-status shape for an unobservable plane."""
    observed = observation if isinstance(observation, dict) else {}
    observed_reason = str(reason or observed.get("reason") or "project_plane_unavailable")
    observed_message = str(message or observed.get("message") or "Project Plane inputs could not be observed safely.")
    health = {
        **observed,
        "available": False,
        "state": "unknown",
        "color": str(observed.get("color") or "YELLOW"),
        "reason": observed_reason,
        "message": observed_message,
    }
    return {
        "ok": True,
        "available": False,
        "projectRoot": str(project),
        "distribution": None,
        "standaloneCompatible": None,
        "canonicalContext": None,
        "baseDocument": None,
        "hasConfig": None,
        "hasCanonicalContext": None,
        "hasBaseDocument": None,
        "guidedSetup": None,
        "memoryCounts": None,
        "features": None,
        "attached": None,
        "attachment": None,
        "drift": None,
        "health": health,
    }


def _observed_work_status_payload(project: Path) -> dict[str, Any]:
    """Gate the legacy Project Plane adapter behind a filesystem-only observer."""
    try:
        observation = observe_project_plane_inputs(project)
    except Exception as exc:  # defensive boundary: startup must remain observational
        return _project_plane_unknown_payload(
            project,
            reason="project_plane_observer_failed",
            message=f"Project Plane inputs could not be observed safely: {exc}",
        )
    if not isinstance(observation, dict):
        return _project_plane_unknown_payload(
            project,
            reason="project_plane_observer_invalid",
            message="Project Plane observer returned an invalid result.",
        )
    if observation.get("available") is not True:
        return _project_plane_unknown_payload(project, observation)

    before, reason = capture_project_plane_inputs(project)
    if reason or before is None:
        return _project_plane_unknown_payload(
            project,
            observation,
            reason=reason or "project_plane_inputs_unreadable",
            message="Project Plane inputs could not be captured stably before the legacy adapter ran.",
        )
    try:
        projected = _work_status_payload(project)
    except Exception as exc:  # legacy adapter errors are uncertainty, not startup failures
        return _project_plane_unknown_payload(
            project,
            observation,
            reason="project_plane_adapter_failed",
            message=f"Project Plane status could not be read safely: {exc}",
        )
    if not isinstance(projected, dict):
        return _project_plane_unknown_payload(
            project,
            observation,
            reason="project_plane_adapter_invalid",
            message="Project Plane status adapter returned an invalid result.",
        )
    after, reason = capture_project_plane_inputs(project)
    if reason or after is None:
        return _project_plane_unknown_payload(
            project,
            observation,
            reason=reason or "project_plane_inputs_unreadable",
            message="Project Plane inputs could not be captured stably after the legacy adapter ran.",
        )
    if before != after:
        return _project_plane_unknown_payload(
            project,
            observation,
            reason="project_plane_inputs_changed_during_adapter",
            message="Project Plane inputs changed while the legacy status adapter was reading them.",
        )
    return {
        **projected,
        "available": True,
        "health": {
            **observation,
            "available": True,
            "state": str(observation.get("state") or "observed"),
            "reason": str(observation.get("reason") or ""),
            "message": str(observation.get("message") or ""),
        },
    }


def _work_start_schema_gate(project: Path, observation: dict[str, Any] | None = None) -> dict[str, Any]:
    observation = observation or observe_freshness(project)
    status = observation.get("legacyStatus")
    status = status if isinstance(status, dict) else {}
    return {
        "ok": observation.get("state") == "fresh",
        "state": status.get("schema", {}).get("schemaState", observation.get("state", "unknown")),
        "message": observation.get("message", ""),
        "schema": status.get("schema", {}),
        "allow_mutating_read_helpers": observation.get("state") == "fresh",
    }


def _work_start_schema_limited_payload(label: str, schema_gate: dict[str, Any]) -> dict[str, Any]:
    state = schema_gate.get("state", "unknown")
    message = (
        f"{label} skipped to preserve read-only startup semantics: "
        f"schemaState={state}; use explicit mutating maintenance before startup helpers."
    )
    return {
        "ok": True,
        "available": False,
        "label": label,
        "message": message,
        "readOnly": True,
        "hiddenWrites": False,
        "schema": schema_gate.get("schema", {}),
        "warnings": [message],
    }


def _work_start_startup_payloads(
    project: Path,
    scope: str,
    topic: str,
    observation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Both payloads are projection- and filesystem-only.  They remain useful
    # when health is unknown and never fall back to a live SQLite read.
    observation = observation or observe_freshness(project)
    return (
        _safe_startup_payload(
            "startup",
            lambda: _startup_payload(project, scope=scope, topic=topic, intent="ask", observation=observation),
        ),
        _safe_startup_payload(
            "op-index",
            lambda: _op_index_payload(project, scope=scope, topic=topic, observation=observation),
        ),
    )


def _routes(topic: str, scope: str) -> list[dict[str, Any]]:
    query = topic or "current focus"
    quoted_query = _shell_quote(query)
    project_scope = scope or "general"
    return [
        {
            "plane": "Dev Plane",
            "purpose": "coding, implementation, verification, source impact, and Dev GraphRAG",
            "command": f"python scripts/cc.py memory retrieve --project-root . {quoted_query} --scope {project_scope} --limit 10",
            "writes": False,
        },
        {
            "plane": "Dev Plane",
            "purpose": "Dev GraphRAG packet with citations and edge evidence",
            "command": f"python scripts/cc.py memory rag-pack --project-root . {quoted_query} --scope {project_scope} --limit 10",
            "writes": False,
        },
        {
            "plane": "Dev Plane",
            "purpose": "Drill down from a rag-pack citation nodeId or evidenceRefId to local indexed evidence",
            "command": "python scripts/cc.py memory evidence show --project-root . <node-id>",
            "writes": False,
        },
        {
            "plane": "Cross-Plane GraphRAG",
            "purpose": "Federated packet across Dev GraphRAG, Project Plane, Session GraphRAG, and RAG-O routes",
            "command": f"python scripts/cc.py memory cross-pack --project-root . {quoted_query} --scope {project_scope} --limit 10",
            "writes": False,
        },
        {
            "plane": "Project Plane",
            "purpose": "Work GraphRAG route for project requirements, research, plans, decisions, outputs, and handoff context",
            "command": f"python scripts/cc.py memory work-context-pack --project-root . --scope {project_scope} --topic {quoted_query}",
            "writes": True,
        },
        {
            "plane": "Session GraphRAG",
            "purpose": "session continuity, active work, prior chat context, and open follow-ups",
            "command": f"python scripts/cc.py memory session list --project-root . --topic {quoted_query}",
            "writes": False,
        },
        {
            "plane": "Application-owned memory",
            "purpose": "runtime or product data owned by the application",
            "command": "Use an application-approved adapter or project-approved source document.",
            "writes": False,
        },
    ]


def _add_action(
    actions: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    *,
    source: str,
    priority: str,
    reason: str,
    command: str = "",
    commands: list[str] | None = None,
    writes: bool = False,
    action_contract: dict[str, Any] | None = None,
) -> None:
    command_key = command or "|".join(commands or [])
    key = (command_key, "") if command_key else (reason, "")
    if key in seen:
        return
    seen.add(key)
    action = {
        "source": source,
        "priority": priority,
        "reason": reason,
        "command": command,
        "commands": commands or [],
        "writes": writes,
    }
    if action_contract:
        action.update(action_contract)
    actions.append(action)


def _projected_recommendations(bootstrap: dict[str, Any], topic: str, scope: str) -> list[dict[str, Any]]:
    """Use projected recommendation facts while preserving caller-specific packet text.

    The writer snapshots the legacy bootstrap with its neutral scope/topic.
    Only the user-facing work-context command depends on the current reader
    request, and it can be rendered from already validated projection facts.
    """
    raw = bootstrap.get("recommendations", [])
    if not isinstance(raw, list):
        return []
    packet_scopes = {
        "general", "research", "planning", "analysis", "writing", "ux", "ui",
        "frontend", "backend", "architecture", "implementation", "bugfix",
        "refactor", "review", "handoff",
    }
    normalized_scope = scope if scope in packet_scopes else "general"
    command_topic = topic or "current focus"
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        if str(copy.get("reason") or "") == "No Project Plane context packet is available for this topic.":
            copy["command"] = (
                "python scripts/cc.py memory work-context-pack --project-root . "
                f"--scope {normalized_scope} --topic \"{command_topic}\""
            )
        result.append(copy)
    return result


def _action_queue(
    bootstrap: dict[str, Any],
    work_status: dict[str, Any],
    graph_status: dict[str, Any],
    session_status: dict[str, Any],
    feature_status: dict[str, Any],
    working_tree: dict[str, Any],
    topic: str,
    scope: str = "general",
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    recommendations = _projected_recommendations(bootstrap, topic, scope)
    for item in recommendations if isinstance(recommendations, list) else []:
        if not isinstance(item, dict):
            continue
        _add_action(
            actions,
            seen,
            source="bootstrap",
            priority="setup" if "init" in str(item.get("command", "")) else "maintenance",
            reason=str(item.get("reason", "")),
            command=str(item.get("command", "")),
            writes=bool(item.get("writes")),
        )
    work_status_available = work_status.get("available") is not False
    if not work_status_available:
        work_health = work_status.get("health") if isinstance(work_status.get("health"), dict) else {}
        _add_action(
            actions,
            seen,
            source="work-status",
            priority="review",
            reason=str(work_health.get("message") or "Project Plane drift is unknown because its inputs were not observed."),
            command="python scripts/cc.py memory work-status --project-root .",
            writes=False,
        )
    drift = work_status.get("drift", {}) if work_status_available and isinstance(work_status.get("drift"), dict) else {}
    update_requests = drift.get("updateRequests", [])
    update_requests = update_requests if isinstance(update_requests, list) else []
    if update_requests:
        _add_action(
            actions,
            seen,
            source="work-parity",
            priority="review",
            reason="Embedded and standalone ControlWork have drift requests; inspect read-only parity before any sync.",
            command="python scripts/cc.py memory work-parity --project-root .",
            writes=False,
        )
    for request in update_requests:
        commands = [
            str(command)
            for command in request.get("commands", [])
            if str(command)
        ]
        _add_action(
            actions,
            seen,
            source="work-status",
            priority=str(request.get("priority", "review")),
            reason=str(request.get("reason", "Project Plane update request")),
            commands=commands,
            writes=bool(request.get("writesRequired", False)),
            action_contract=request,
        )
    suggestion_counts = graph_status.get("suggestionCounts", {}) if isinstance(graph_status.get("suggestionCounts"), dict) else {}
    suggested = int(suggestion_counts.get("suggested") or 0)
    if suggested:
        _add_action(
            actions,
            seen,
            source="graph-status",
            priority="review",
            reason=f"{suggested} graph suggestion(s) are waiting for accept or reject review.",
            command="python scripts/cc.py memory graph suggestions --project-root .",
            writes=False,
        )
    session_views = session_status.get("views") if isinstance(session_status.get("views"), dict) else {}
    if session_views.get("stale"):
        _add_action(
            actions,
            seen,
            source="session-status",
            priority="maintenance",
            reason="Session views are stale or missing.",
            command="python scripts/cc.py memory session views --project-root .",
            writes=True,
        )
    open_followups = session_status.get("openFollowups") if isinstance(session_status.get("openFollowups"), list) else []
    if open_followups:
        _add_action(
            actions,
            seen,
            source="session-status",
            priority="review",
            reason=f"{len(open_followups)} session follow-up(s) remain open.",
            command="python scripts/cc.py memory session list --project-root . --status needs_followup",
            writes=False,
        )
    feature_current = feature_status.get("currentFeature", {}) if isinstance(feature_status.get("currentFeature"), dict) else {}
    feature_summary = feature_status.get("summary", {}) if isinstance(feature_status.get("summary"), dict) else {}
    if feature_status.get("initialized") and feature_status.get("blockedFeatureIds"):
        _add_action(
            actions,
            seen,
            source="feature-state",
            priority="review",
            reason="A feature is blocked and needs review before new work starts.",
            command="python scripts/cc.py feature status --project-root .",
            writes=False,
        )
    elif feature_status.get("initialized") and int(feature_summary.get("inProgressCount") or 0):
        feature_id = str(feature_current.get("id") or "")
        _add_action(
            actions,
            seen,
            source="feature-state",
            priority="review",
            reason=f"Feature WIP is active: {feature_id} [{feature_current.get('state', '')}].",
            command=f"python scripts/cc.py feature show --project-root . {feature_id}" if feature_id else "python scripts/cc.py feature status --project-root .",
            writes=False,
        )
    derived = bootstrap.get("derivedArtifacts", {}) if isinstance(bootstrap.get("derivedArtifacts"), dict) else {}
    graph_packets = derived.get("graphPackets", {}) if isinstance(derived.get("graphPackets"), dict) else {}
    packet_count = graph_packets.get("fileCount")
    if topic and isinstance(packet_count, int) and not isinstance(packet_count, bool) and packet_count == 0:
        slug = _topic_slug(topic)
        _add_action(
            actions,
            seen,
            source="rag-pack",
            priority="packet",
            reason="No Dev GraphRAG packet exists yet for this workspace.",
            command=(
                "python scripts/cc.py memory rag-pack --project-root . "
                f"{_shell_quote(topic)} --limit 10 --output .controlcoding/context-packets/{slug}-rag.md"
            ),
            writes=True,
        )
    tracked_dirty_count = working_tree.get("trackedDirtyCount")
    if tracked_dirty_count is None:
        _add_action(
            actions,
            seen,
            source="git",
            priority="review",
            reason="Working-tree status is unknown because Git was not observed.",
            command="git status --short",
            writes=False,
        )
    elif isinstance(tracked_dirty_count, int) and not isinstance(tracked_dirty_count, bool) and tracked_dirty_count > 0:
        _add_action(
            actions,
            seen,
            source="git",
            priority="review",
            reason="Working tree has files to review before commit.",
            command="git status --short",
            writes=False,
        )
    return actions


def _render_action_queue_item(item: dict[str, Any]) -> list[str]:
    """Render a queue action without turning alternatives into a command chain."""
    priority = str(item.get("priority", "review"))
    reason = str(item.get("reason", "Project Plane update request"))
    write_marker = "write" if item.get("writes") else "read"
    lines = [f"  - [{priority}, {write_marker}] {reason}"]
    steps = item.get("steps")
    if not isinstance(steps, list) or not steps:
        command = item.get("command") or "; ".join(item.get("commands", []))
        if command:
            lines.append(f"    {command}")
        return lines

    read_steps = [
        step for step in steps
        if isinstance(step, dict) and not step.get("writesRequired") and step.get("command")
    ]
    for step in read_steps:
        lines.append(f"    Review (read-only): {step['command']}")
    preconditions = item.get("preconditions")
    if isinstance(preconditions, list) and preconditions:
        lines.append("    Preconditions:")
        lines.extend(f"      - {precondition}" for precondition in preconditions)

    alternatives: dict[str, list[dict[str, Any]]] = {}
    followups: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict) or not step.get("writesRequired") or not step.get("command"):
            continue
        group = step.get("alternativeGroup")
        if group:
            alternatives.setdefault(str(group), []).append(step)
        else:
            followups.append(step)

    for group, group_steps in alternatives.items():
        lines.append(
            f"    Choice required: select exactly one mutative alternative in '{group}' after authority decision:"
        )
        for step in sorted(group_steps, key=lambda candidate: (candidate.get("order", 0), str(candidate.get("alternative", "")))):
            alternative = str(step.get("alternative", "option"))
            lines.append(f"      - [write, alternative {alternative}] {step['command']}")
    for step in followups:
        lines.append(f"    Mutative follow-up [write]: {step['command']}")
    return lines


def _unknown_op_index_payload(project: Path, scope: str, topic: str, observation: dict[str, Any], work_status: dict[str, Any], feature_status: dict[str, Any], working_tree: dict[str, Any]) -> dict[str, Any]:
    """Represent unavailable Dev Plane facts without inventing zero values."""
    health_action = {
        "source": "health-projection",
        "priority": "review",
        "reason": str(observation.get("message") or "Memory health is unknown."),
        "command": "",
        "commands": [],
        "writes": False,
    }
    work_status_available = work_status.get("available") is not False
    drift = work_status.get("drift", {}) if work_status_available and isinstance(work_status.get("drift"), dict) else {}
    update_requests = drift.get("updateRequests", [])
    update_requests = update_requests if isinstance(update_requests, list) else []
    project_plane = {
        "available": work_status_available,
        "present": (
            bool(work_status.get("hasConfig") or work_status.get("hasCanonicalContext"))
            if work_status_available else None
        ),
        "attachedExternal": bool(work_status.get("attached")) if work_status_available else None,
        "embeddedVsExternalDifferent": drift.get("embeddedVsExternalDifferent") if work_status_available else None,
        "actionableSemanticDrift": drift.get("semantic", {}).get("actionableDrift") if work_status_available and isinstance(drift.get("semantic"), dict) else None,
        "updateRequests": len(update_requests) if work_status_available else None,
        "health": work_status.get("health"),
    }
    filesystem_actions = _action_queue(
        {"recommendations": [], "derivedArtifacts": {"graphPackets": {"fileCount": None}}},
        work_status,
        {"suggestionCounts": None},
        {"views": None, "openFollowups": None},
        feature_status,
        working_tree,
        topic,
        scope,
    )
    warnings: list[Any] = [str(observation.get("message") or "Memory health is unknown.")]
    if not work_status_available:
        work_health = work_status.get("health") if isinstance(work_status.get("health"), dict) else {}
        warnings.append(str(work_health.get("message") or "Project Plane status is unknown because its inputs were not observed."))
    feature_warnings = feature_status.get("warnings", []) if isinstance(feature_status, dict) else []
    if isinstance(feature_warnings, list):
        warnings.extend(feature_warnings)
    return {
        "ok": True,
        "name": "RAG Operations Index",
        "alias": "RAG-O",
        "kind": "operations_index",
        "ragEngine": False,
        "role": "read-only coordination layer for memory status, RAG routes, packets, drift, and action queue",
        "projectRoot": str(project),
        "scope": scope or "general",
        "topic": topic,
        "mode": "read_only_no_sync_no_hidden_writes",
        "readOnly": True,
        "hiddenWrites": False,
        "sourceOfTruth": False,
        "planes": {
            "dev": {"available": False, "initialized": None, "databasePath": None, "graphContractVersion": None, "counts": None},
            "project": project_plane,
            "applicationOwned": {"available": False, "referencedComponents": None, "records": None, "boundary": "Application runtime memory remains application-owned; ControlCoding may reference approved project documents only."},
        },
        "indexHealth": {
            "available": False,
            "health": observation,
            # Route capability is static and remains observable even when the
            # derived health measures are unavailable.  This does not claim
            # that any evidence row or count was observed.
            "evidenceRefs": {
                "available": True,
                "schemaVersion": "cc-evidence-ref/v1",
                "storage": "derived_from_memory_index_no_table",
                "showCommand": "python scripts/cc.py memory evidence show --project-root . <node-id>",
            },
        },
        "graph": _graph_status(observation),
        "sessions": {"available": False, "message": observation.get("message", "Memory health is unknown."), "sessionCount": None, "activeCount": None, "latestSession": None, "openFollowups": None, "views": None, "health": observation},
        "health": observation,
        "featureState": feature_status,
        "workStatus": work_status,
        "workingTree": working_tree,
        "actionQueue": [health_action, *filesystem_actions],
        "routes": _routes(topic, scope),
        "nextReads": {"available": False, "health": observation},
        "warnings": warnings,
    }


def _op_index_payload(
    project: Path,
    scope: str = "general",
    topic: str = "",
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project = project.resolve()
    observation = observation or observe_freshness(project)
    work_status = _observed_work_status_payload(project)
    feature_status = _feature_status_payload(project)
    working_tree = _working_tree(project)
    fresh = observation.get("state") == "fresh"
    if not fresh:
        return _unknown_op_index_payload(project, scope, topic, observation, work_status, feature_status, working_tree)

    legacy_status = observation["legacyStatus"]
    bootstrap = legacy_status["bootstrap"]
    vectors = legacy_status["vectors"]
    sessions = legacy_status["sessions"]
    dev_plane = bootstrap["devPlane"]
    derived = bootstrap["derivedArtifacts"]
    app_memory = bootstrap["applicationOwnedMemory"]
    graph_status = _graph_status(observation)
    session_status = dict(sessions)
    session_status["health"] = observation
    action_queue = _action_queue(bootstrap, work_status, graph_status, session_status, feature_status, working_tree, topic, scope)
    warnings = list(bootstrap["warnings"])
    if work_status.get("available") is False:
        work_health = work_status.get("health") if isinstance(work_status.get("health"), dict) else {}
        warnings.append(str(work_health.get("message") or "Project Plane status is unknown because its inputs were not observed."))
    if isinstance(feature_status, dict):
        warnings.extend(feature_status.get("warnings", []) or [])
    payload = {
        "ok": True,
        "name": "RAG Operations Index",
        "alias": "RAG-O",
        "kind": "operations_index",
        "ragEngine": False,
        "role": "read-only coordination layer for memory status, RAG routes, packets, drift, and action queue",
        "projectRoot": str(project),
        "scope": scope or "general",
        "topic": topic,
        "mode": "read_only_no_sync_no_hidden_writes",
        "readOnly": True,
        "hiddenWrites": False,
        "sourceOfTruth": False,
        "planes": {
            "dev": {
                "available": True,
                "initialized": bool(dev_plane["initialized"]),
                "databasePath": dev_plane["databasePath"],
                "graphContractVersion": dev_plane["graphContractVersion"],
                "counts": dev_plane["counts"],
            },
            "project": {
                "available": work_status.get("available") is not False,
                "present": bool(bootstrap["projectPlane"]["hasControlWorkRoot"] or bootstrap["projectPlane"]["hasCanonicalContext"]) if work_status.get("available") is not False else None,
                "attachedExternal": bool(bootstrap["projectPlane"]["attachedExternal"]) if work_status.get("available") is not False else None,
                "embeddedVsExternalDifferent": work_status.get("drift", {}).get("embeddedVsExternalDifferent") if work_status.get("available") is not False and isinstance(work_status.get("drift"), dict) else None,
                "actionableSemanticDrift": work_status.get("drift", {}).get("semantic", {}).get("actionableDrift") if work_status.get("available") is not False and isinstance(work_status.get("drift"), dict) and isinstance(work_status.get("drift", {}).get("semantic"), dict) else None,
                "updateRequests": len(work_status.get("drift", {}).get("updateRequests", [])) if work_status.get("available") is not False and isinstance(work_status.get("drift"), dict) and isinstance(work_status.get("drift", {}).get("updateRequests"), list) else None,
                "health": work_status.get("health"),
            },
            "applicationOwned": {"available": True, **app_memory},
        },
        "indexHealth": {
            "available": True,
            "lastScanAt": derived["lastScanAt"],
            "views": derived["views"],
            "vectors": derived["vectors"],
            "evidenceRefs": {
                "available": True,
                "schemaVersion": "cc-evidence-ref/v1",
                "storage": "derived_from_memory_index_no_table",
                "showCommand": "python scripts/cc.py memory evidence show --project-root . <node-id>",
            },
            "graphPackets": derived["graphPackets"],
            "devContextPackets": derived["devContextPackets"],
            "projectPlaneViews": derived["projectPlaneViews"],
            "projectPlaneContextPackets": derived["projectPlaneContextPackets"],
        },
        "graph": graph_status,
        "sessions": session_status,
        "health": observation,
        "featureState": feature_status,
        "workStatus": work_status,
        "workingTree": working_tree,
        "actionQueue": action_queue,
        "routes": _routes(topic, scope),
        "nextReads": {
            "hotDocuments": bootstrap["hotDocuments"],
            "activeFocus": bootstrap["activeFocus"],
            "latestPackets": {
                "devGraphRag": derived["graphPackets"]["latestFile"],
                "projectPlane": derived["projectPlaneContextPackets"]["latestFile"],
            },
        },
        "warnings": warnings,
    }
    return payload


def _op_index_text(payload: dict[str, Any]) -> str:
    planes = payload["planes"]
    health = payload["indexHealth"]
    sessions = payload.get("sessions", {})
    feature_state = payload.get("featureState", {})
    feature_summary = feature_state.get("summary") if isinstance(feature_state, dict) else {}
    feature_summary = feature_summary if isinstance(feature_summary, dict) else {}
    current_feature = feature_state.get("currentFeature") if isinstance(feature_state, dict) else {}
    current_feature = current_feature if isinstance(current_feature, dict) else {}
    working = payload["workingTree"]
    vectors = health.get("vectors", {}) if isinstance(health.get("vectors"), dict) else {}
    session_views = sessions.get("views") if isinstance(sessions.get("views"), dict) else {}
    projection_health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    def observed(value: Any) -> str:
        return "unknown (not observed)" if value is None else str(value)

    lines = [
        "RAG-O - RAG Operations Index",
        "  Role: read-only coordination layer, not a retrieval or generation engine",
        f"  Scope: {payload['scope']}",
        f"  Topic: {payload['topic'] or '(none)'}",
        "  Mode: read-only, no sync, no hidden writes",
        "  Source of truth: no",
        "  Health projection: "
        f"{str(projection_health.get('state') or 'unknown').upper()}/"
        f"{str(projection_health.get('color') or 'YELLOW').upper()} "
        f"[{str(projection_health.get('reason') or 'none')}]",
        "",
        "Plane status",
        f"  Dev Plane initialized: {observed(planes['dev']['initialized'])}",
        f"  Project Plane present: {observed(planes['project']['present'])}",
        f"  Project Plane attached external: {observed(planes['project']['attachedExternal'])}",
        f"  Project Plane drift: {observed(planes['project']['embeddedVsExternalDifferent'])}",
        f"  Project Plane actionable drift: {observed(planes['project']['actionableSemanticDrift'])}",
        f"  Application-owned refs: {observed(planes['applicationOwned'].get('referencedComponents'))}",
        "",
        "Index health",
        f"  Last scan: {observed(health.get('lastScanAt')) if health.get('lastScanAt') is None else health.get('lastScanAt') or '(none)'}",
        f"  Views stale: {observed(health.get('views', {}).get('stale'))}",
        f"  Vectors stale: {observed(health.get('vectors', {}).get('stale'))}",
        *([f"  Vector rebuild command: {vectors.get('rebuildCommand')}"] if vectors.get("stale") else []),
        f"  Graph packets: {observed(health.get('graphPackets', {}).get('fileCount'))}",
        f"  Project Plane packets: {observed(health.get('projectPlaneContextPackets', {}).get('fileCount'))}",
        "",
        "Session GraphRAG",
        f"  Available: {observed(sessions.get('available'))}",
        f"  Session records: {observed(sessions.get('sessionCount'))}",
        f"  Active sessions: {observed(sessions.get('activeCount'))}",
        f"  Open follow-ups: {len(sessions.get('openFollowups', [])) if isinstance(sessions.get('openFollowups'), list) else 'unknown (not observed)'}",
        f"  Session views stale: {observed(session_views.get('stale'))}",
    ]
    latest = sessions.get("latestSession") if isinstance(sessions.get("latestSession"), dict) else {}
    if latest:
        lines.append(f"  Latest session: {latest.get('id')} [{latest.get('status')}] {latest.get('topic')}")
    lines.extend([
        "",
        "Feature state",
        f"  Initialized: {observed(feature_state.get('initialized'))}",
        f"  WIP: {observed(feature_summary.get('inProgressCount'))}/{observed(feature_summary.get('wipLimit'))}",
    ])
    if current_feature:
        lines.append(
            f"  Current feature: {current_feature.get('id')} "
            f"[{current_feature.get('state')}] {current_feature.get('title', '')}"
        )
    else:
        lines.append("  Current feature: unknown (not observed)" if feature_state.get("available") is False else "  Current feature: none")
    lines.extend([
        "",
        "Action queue",
    ])
    if payload["actionQueue"]:
        for item in payload["actionQueue"]:
            lines.extend(_render_action_queue_item(item))
    else:
        lines.append("  - none")
    lines.extend(["", "RAG routes"])
    for route in payload["routes"]:
        write_marker = "write" if route["writes"] else "read"
        lines.append(f"  - {route['plane']} [{write_marker}]: {route['command']}")
    lines.extend(["", "Commit hygiene"])
    lines.append(f"  Tracked dirty files: {observed(working.get('trackedDirtyCount'))}")
    if working.get("available") and working.get("isProjectGitRoot"):
        for item in (working["changes"] or [])[:12]:
            lines.append(f"  - {item['status']} {item['path']}")
    else:
        lines.append(f"  Git status: {working.get('message') or 'not available for this project root'}")
    commit_policy = working.get("commitPolicy") if isinstance(working.get("commitPolicy"), dict) else {}
    lines.append("  Do not commit:")
    for pattern in commit_policy.get("doNotCommit", LOCAL_ONLY_PATTERNS):
        lines.append(f"  - {pattern}")
    lines.append("  Release export excludes:")
    for pattern in commit_policy.get("releaseExportExclusions", RELEASE_EXPORT_EXCLUSIONS):
        lines.append(f"  - {pattern}")
    lines.extend(["", "Warnings"])
    if payload["warnings"]:
        for warning in payload["warnings"]:
            lines.append(f"  - {warning}")
    else:
        lines.append("  - none")
    return "\n".join(lines)


def _memory_health_text_lines(health: Any) -> list[str]:
    """Render shared startup provenance without presenting unavailable facts as measures."""
    health = health if isinstance(health, dict) else {}
    def observed(value: Any) -> str:
        return "unknown (not observed)" if value is None else str(value)

    return [
        "Memory health",
        f"  State: {str(health.get('state', 'unknown')).upper()} ({health.get('color', 'YELLOW')})",
        f"  Reason: {health.get('reason') or '(none)'}",
        f"  Provenance: readOnly={observed(health.get('readOnly'))}, sourceOfTruth={observed(health.get('sourceOfTruth'))}, projectionPath={health.get('projectionPath') or 'unknown (not observed)'}",
    ]


def _startup_payload(
    project: Path,
    scope: str = "general",
    topic: str = "",
    intent: str = "ask",
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intent = intent if intent in STARTUP_INTENTS else "ask"
    observation = observation or observe_freshness(project)
    op_index = _op_index_payload(project, scope=scope, topic=topic, observation=observation)
    sessions = op_index.get("sessions", {})
    feature_state = op_index.get("featureState", {})
    feature_summary = feature_state.get("summary") if isinstance(feature_state, dict) else {}
    feature_summary = feature_summary if isinstance(feature_summary, dict) else {}
    current_feature = feature_state.get("currentFeature") if isinstance(feature_state, dict) else {}
    current_feature = current_feature if isinstance(current_feature, dict) else {}
    latest = sessions.get("latestSession") if isinstance(sessions.get("latestSession"), dict) else {}
    query = topic or str(latest.get("topic") or "current focus")
    quoted_query = _shell_quote(query)
    latest_id = str(latest.get("id") or "")
    vector_health = op_index.get("indexHealth", {}).get("vectors", {})
    vector_health = vector_health if isinstance(vector_health, dict) else {}
    fresh = op_index.get("health", {}).get("state") == "fresh"

    visible_commands: list[dict[str, Any]] = [
        {
            "purpose": "Read the operations index for memory planes, routes, stale artifacts, and action queue.",
            "command": f"python scripts/cc.py memory op-index --project-root . --scope {scope or 'general'} --topic {quoted_query}",
            "writes": False,
        },
        {
            "purpose": "List related session records without mutating memory.",
            "command": f"python scripts/cc.py memory session list --project-root . --topic {quoted_query}",
            "writes": False,
        },
        {
            "purpose": "Retrieve Dev GraphRAG context with explainable ranking.",
            "command": f"python scripts/cc.py memory retrieve --project-root . {quoted_query} --scope {scope or 'general'} --limit 10",
            "writes": False,
        },
        {
            "purpose": "Build an on-screen Session GraphRAG packet when prior session context is needed.",
            "command": f"python scripts/cc.py memory session-pack --project-root . --topic {quoted_query} --status all",
            "writes": False,
        },
    ]
    if latest_id:
        visible_commands.insert(2, {
            "purpose": "Inspect the latest session record before continuing.",
            "command": f"python scripts/cc.py memory session show --project-root . {latest_id}",
            "writes": False,
        })
    if feature_state.get("initialized"):
        visible_commands.append({
            "purpose": "Inspect the current feature work contract and WIP state.",
            "command": "python scripts/cc.py feature status --project-root .",
            "writes": False,
        })
        feature_id = str(current_feature.get("id") or "")
        if feature_id:
            visible_commands.append({
                "purpose": "Inspect the active feature before continuing implementation.",
                "command": f"python scripts/cc.py feature show --project-root . {feature_id}",
                "writes": False,
            })
    if intent == "start_new_work":
        visible_commands.append({
            "purpose": "Optional explicit write only after the user confirms a new tracked session.",
            "command": f"python scripts/cc.py memory session start --project-root . --topic {quoted_query} --mode new_work",
            "writes": True,
        })

    feature_available = feature_state.get("available") is not False
    return {
        "ok": True,
        "name": "Memory Startup Protocol",
        "kind": "startup_protocol",
        "readOnly": True,
        "hiddenWrites": False,
        "health": observation,
        "intent": intent,
        "scope": scope or "general",
        "topic": topic,
        "question": "Continue previous work or start new work?" if intent == "ask" else "",
        "rules": [
            "Host context can request this protocol, but it cannot force hidden command execution.",
            "Run startup commands visibly when tools are available.",
            "Do not create, close, sync, rebuild, or mutate memory unless the user explicitly asks.",
            "Use Session GraphRAG as traceability evidence, not as canonical project truth.",
        ],
        "status": {
            "devPlaneInitialized": op_index.get("planes", {}).get("dev", {}).get("initialized") if fresh else None,
            "projectPlanePresent": op_index.get("planes", {}).get("project", {}).get("present") if fresh else None,
            "sessionRecords": sessions.get("sessionCount") if fresh else None,
            "activeSessions": sessions.get("activeCount") if fresh else None,
            "openFollowups": len(sessions.get("openFollowups", [])) if fresh and isinstance(sessions.get("openFollowups"), list) else None,
            "sessionViewsStale": sessions.get("views", {}).get("stale") if fresh and isinstance(sessions.get("views"), dict) else None,
            "vectorStale": vector_health.get("stale") if fresh else None,
            "vectorHealth": vector_health if fresh else None,
            "latestSession": latest if fresh else None,
            "featureState": {
                "initialized": feature_state.get("initialized") if feature_available else None,
                "inProgressCount": feature_summary.get("inProgressCount") if feature_available else None,
                "wipLimit": feature_summary.get("wipLimit") if feature_available else None,
                "currentFeature": current_feature if feature_available else None,
                "blockedFeatureIds": feature_state.get("blockedFeatureIds") if feature_available else None,
            },
        },
        "visibleCommands": visible_commands,
        "actionQueue": op_index.get("actionQueue", []),
        "nextReads": op_index.get("nextReads", {}),
        "warnings": op_index.get("warnings", []),
    }


def _startup_text(payload: dict[str, Any]) -> str:
    status = payload.get("status", {})
    latest = status.get("latestSession") if isinstance(status.get("latestSession"), dict) else {}
    feature = status.get("featureState") if isinstance(status.get("featureState"), dict) else {}
    current_feature = feature.get("currentFeature") if isinstance(feature.get("currentFeature"), dict) else {}
    health = payload.get("health", {}) if isinstance(payload.get("health"), dict) else {}
    def observed(value: Any) -> str:
        return "unknown (not observed)" if value is None else str(value)
    lines = [
        "Memory startup protocol",
        f"  Intent: {payload.get('intent')}",
        f"  Scope: {payload.get('scope')}",
        f"  Topic: {payload.get('topic') or '(none)'}",
        "  Mode: read-only, no hidden writes",
        "",
        "Memory health",
        f"  State: {str(health.get('state', 'unknown')).upper()} ({health.get('color', 'YELLOW')})",
        f"  Reason: {health.get('reason') or '(none)'}",
        f"  Provenance: readOnly={observed(health.get('readOnly'))}, sourceOfTruth={observed(health.get('sourceOfTruth'))}, projectionPath={health.get('projectionPath') or 'unknown (not observed)'}",
        "",
        "Startup question",
    ]
    question = str(payload.get("question") or "")
    lines.append(f"  {question}" if question else "  Intent already selected by caller.")
    lines.extend(["", "Rules"])
    for rule in payload.get("rules", []):
        lines.append(f"  - {rule}")
    lines.extend([
        "",
        "Current memory status",
        f"  Dev Plane initialized: {observed(status.get('devPlaneInitialized'))}",
        f"  Project Plane present: {observed(status.get('projectPlanePresent'))}",
        f"  Session records: {observed(status.get('sessionRecords'))}",
        f"  Active sessions: {observed(status.get('activeSessions'))}",
        f"  Open follow-ups: {observed(status.get('openFollowups'))}",
        f"  Session views stale: {observed(status.get('sessionViewsStale'))}",
        f"  Vectors stale: {observed(status.get('vectorStale'))}",
    ])
    vector_health = status.get("vectorHealth") if isinstance(status.get("vectorHealth"), dict) else {}
    if vector_health.get("stale"):
        lines.append(f"  Vector rebuild command: {vector_health.get('rebuildCommand')}")
    if latest:
        lines.append(f"  Latest session: {latest.get('id')} [{latest.get('status')}] {latest.get('topic')}")
    lines.extend([
        "",
        "Feature state",
        f"  Initialized: {observed(feature.get('initialized'))}",
        f"  WIP: {observed(feature.get('inProgressCount'))}/{observed(feature.get('wipLimit'))}",
    ])
    if current_feature:
        lines.append(
            f"  Current feature: {current_feature.get('id')} "
            f"[{current_feature.get('state')}] {current_feature.get('title', '')}"
        )
    else:
        lines.append("  Current feature: unknown (not observed)" if feature.get("initialized") is None else "  Current feature: none")
    lines.extend(["", "Visible startup commands"])
    for item in payload.get("visibleCommands", []):
        marker = "write" if item.get("writes") else "read"
        lines.append(f"  - [{marker}] {item.get('purpose')}")
        lines.append(f"    {item.get('command')}")
    lines.extend(["", "Action queue"])
    if payload.get("actionQueue"):
        for item in payload.get("actionQueue", [])[:10]:
            lines.extend(_render_action_queue_item(item))
    else:
        lines.append("  - none")
    lines.extend(["", "Warnings"])
    if payload.get("warnings"):
        for warning in payload.get("warnings", []):
            lines.append(f"  - {warning}")
    else:
        lines.append("  - none")
    return "\n".join(lines)


def cmd_memory_op_index(
    project: Path,
    scope: str = "general",
    topic: str = "",
    json_output: bool = False,
) -> int:
    payload = _op_index_payload(project, scope=scope, topic=topic)
    _print_json_or_text(json_output, payload, _op_index_text(payload))
    return 0


def cmd_memory_startup(
    project: Path,
    scope: str = "general",
    topic: str = "",
    intent: str = "ask",
    json_output: bool = False,
) -> int:
    payload = _startup_payload(project, scope=scope, topic=topic, intent=intent)
    _print_json_or_text(json_output, payload, _startup_text(payload))
    return 0


__all__ = ["STARTUP_INTENTS", "cmd_memory_op_index", "cmd_memory_startup"]
