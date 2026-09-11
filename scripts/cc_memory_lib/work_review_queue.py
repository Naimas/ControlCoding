# SPDX-License-Identifier: PolyForm-Shield-1.0.0
"""Deterministic managed review queue for embedded ControlWork evidence.

The queue is a projection of the current file index and scan analysis. Only
human relational decisions are persisted, under ``file-index.json`` review
state; queue items themselves are never stored as canonical state.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


QUEUE_SCHEMA_VERSION = "controlwork-review-queue/v1"
STATE_SCHEMA_VERSION = "controlwork-review-state/v1"
RELATIONAL_KINDS = {"duplicate", "version", "conflict"}


class ReviewQueueError(ValueError):
    """A requested managed-review operation is invalid."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_path(value: Any) -> str:
    normalized = str(value or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.casefold()


def _record_evidence(record: dict) -> dict:
    return {
        "path": str(record.get("path", "")).replace("\\", "/"),
        "contentHash": str(record.get("contentHash", "")),
        "normalizedTextHash": str(record.get("normalizedTextHash", "")),
        "sourceRecord": str(record.get("sourceRecord", "")),
        "scanStatus": str(record.get("scanStatus", "")),
        "reviewStatus": str(record.get("reviewStatus", "unreviewed")),
    }


def _decision_status(kind: str, action: str) -> str:
    if kind == "conflict" and action == "acknowledge":
        return "in_review"
    if action in {"select_canonical", "accept_parallel"}:
        return "resolved"
    return "in_review" if action == "defer" else "open"


def _queue_item(
    kind: str,
    identity: dict,
    *,
    severity: str,
    reason: str,
    paths: list[str],
    evidence: list[dict],
    blocked_paths: list[str],
    recommended_action: str,
    allowed_actions: list[str],
    steps: list[str],
) -> dict:
    fingerprint = _fingerprint({"kind": kind, "identity": identity})
    return {
        "id": f"CW_REVIEW_{fingerprint[:20].upper()}",
        "fingerprint": fingerprint,
        "kind": kind,
        "status": "open",
        "severity": severity,
        "owner": "human_operator",
        "decisionRequired": True,
        "reason": reason,
        "paths": sorted(set(paths), key=lambda value: (_normalized_path(value), value)),
        "evidence": evidence,
        "blockedPaths": sorted(set(blocked_paths), key=lambda value: (_normalized_path(value), value)),
        "recommendedAction": recommended_action,
        "allowedActions": allowed_actions,
        "preconditions": ["Review the current evidence and fingerprint before acting."],
        "steps": steps,
    }


def _candidate_decisions(payload: dict) -> list[dict]:
    state = payload.get("reviewState", {})
    if not isinstance(state, dict):
        return []
    decisions = state.get("candidateDecisions", [])
    if not isinstance(decisions, list):
        return []
    return [decision for decision in decisions if isinstance(decision, dict)]


def _effective_decisions(decisions: list[dict]) -> list[dict]:
    """Retain one deterministic decision for each candidate and fingerprint."""
    selected: dict[tuple[str, str], tuple[int, dict]] = {}
    for position, decision in enumerate(decisions):
        candidate_id = str(decision.get("candidateId", ""))
        fingerprint = str(decision.get("fingerprint", ""))
        kind = str(decision.get("kind", ""))
        if kind not in RELATIONAL_KINDS or not candidate_id or not fingerprint:
            continue
        key = (candidate_id, fingerprint)
        previous = selected.get(key)
        sort_key = (str(decision.get("decidedAt", "")), position)
        if previous is None or sort_key >= (str(previous[1].get("decidedAt", "")), previous[0]):
            selected[key] = (position, decision)
    return [decision for _position, decision in sorted(selected.values(), key=lambda value: value[0])]


def _project_decisions(items: list[dict], decisions: list[dict]) -> list[dict]:
    effective = _effective_decisions(decisions)
    decisions_by_key = {
        (str(decision.get("candidateId", "")), str(decision.get("fingerprint", ""))): decision
        for decision in effective
    }
    current_keys = {(item["id"], item["fingerprint"]) for item in items}
    for item in items:
        decision = decisions_by_key.get((item["id"], item["fingerprint"]))
        if decision:
            item["decision"] = decision
            item["status"] = _decision_status(item["kind"], str(decision.get("action", "")))

    used_ids = {str(item.get("id", "")) for item in items}
    for decision in effective:
        kind = str(decision.get("kind", ""))
        fingerprint = str(decision.get("fingerprint", ""))
        candidate_id = str(decision.get("candidateId", ""))
        if (
            kind not in RELATIONAL_KINDS
            or not fingerprint
            or not candidate_id
            or (candidate_id, fingerprint) in current_keys
        ):
            continue
        obsolete_id = candidate_id
        if obsolete_id in used_ids:
            suffix = _fingerprint({"candidateId": candidate_id, "fingerprint": fingerprint})[:12].upper()
            obsolete_id = f"{candidate_id}_OBSOLETE_{suffix}"
        used_ids.add(obsolete_id)
        items.append({
            "id": obsolete_id,
            "fingerprint": fingerprint,
            "kind": kind,
            "status": "obsolete",
            "severity": str(decision.get("severity", "medium")),
            "owner": "human_operator",
            "decisionRequired": False,
            "reason": "A prior decision no longer matches the current candidate fingerprint.",
            "paths": [str(path) for path in decision.get("paths", []) if str(path)],
            "evidence": list(decision.get("evidence", [])),
            "blockedPaths": list(decision.get("blockedPaths", [])),
            "recommendedAction": "Review the current candidate; this historical decision is retained for traceability.",
            "allowedActions": [],
            "preconditions": [],
            "steps": [],
            "decision": decision,
        })
    return items


def build_review_queue(payload: dict, analysis: dict, *, generated_at: str, index_path: str) -> dict:
    """Build the complete queue before any caller applies filtering or limits."""
    files = [record for record in payload.get("files", []) if isinstance(record, dict)]
    items: list[dict] = []
    covered_paths: set[str] = set()

    for group in analysis.get("duplicateGroups", []):
        records = [_record_evidence(record) for record in group.get("records", []) if isinstance(record, dict)]
        ordered = sorted(records, key=lambda record: (_normalized_path(record["path"]), record["contentHash"]))
        paths = [record["path"] for record in ordered]
        covered_paths.update(_normalized_path(path) for path in paths)
        shared_hash = str(group.get("hash", ""))
        items.append(_queue_item(
            "duplicate",
            {
                "hash": shared_hash,
                "members": [
                    {"path": _normalized_path(record["path"]), "hash": record["contentHash"]}
                    for record in ordered
                ],
            },
            severity="medium",
            reason=str(group.get("reason", "Multiple scanned files share the same content.")),
            paths=paths,
            evidence=[{"sharedHash": shared_hash, "members": ordered}],
            blocked_paths=paths,
            recommended_action="Select one canonical source or explicitly accept the parallel copies.",
            allowed_actions=["select_canonical", "accept_parallel", "defer"],
            steps=["Compare the members.", "Record a human decision without importing or promoting content."],
        ))

    for group in analysis.get("versionCandidates", []):
        records = [_record_evidence(record) for record in group.get("records", []) if isinstance(record, dict)]
        ordered = sorted(records, key=lambda record: (_normalized_path(record["path"]), record["contentHash"]))
        paths = [record["path"] for record in ordered]
        covered_paths.update(_normalized_path(path) for path in paths)
        family = str(group.get("family", ""))
        items.append(_queue_item(
            "version",
            {
                "family": family,
                "members": [
                    {"path": _normalized_path(record["path"]), "hash": record["contentHash"]}
                    for record in ordered
                ],
            },
            severity="medium",
            reason=str(group.get("reason", "Similar filenames have different content hashes.")),
            paths=paths,
            evidence=[{"family": family, "members": ordered}],
            blocked_paths=paths,
            recommended_action="Select a canonical version or accept parallel versions with a human note.",
            allowed_actions=["select_canonical", "accept_parallel", "defer"],
            steps=[
                "Compare the version family.",
                "Record the decision without renaming, moving, importing, or promoting files.",
            ],
        ))

    for conflict in analysis.get("conflictCandidates", []):
        subtype = str(conflict.get("kind", ""))
        if subtype == "parallel_versions":
            continue
        paths = (
            [str(conflict.get("path", ""))]
            if conflict.get("path")
            else [str(path) for path in conflict.get("paths", [])]
        )
        records = []
        for path in paths:
            matching = next(
                (record for record in files if _normalized_path(record.get("path")) == _normalized_path(path)),
                None,
            )
            if matching:
                records.append(_record_evidence(matching))
        source_record = str(conflict.get("sourceRecord", ""))
        identity = {
            "subtype": subtype,
            "members": [
                {"path": _normalized_path(record["path"]), "hash": record["contentHash"]}
                for record in sorted(records, key=lambda record: _normalized_path(record["path"]))
            ],
            "sourceRecord": source_record,
        }
        covered_paths.update(_normalized_path(path) for path in paths)
        items.append(_queue_item(
            "conflict",
            identity,
            severity=str(conflict.get("severity", "high")),
            reason=str(conflict.get("reason", "Conflicting scan evidence requires human review.")),
            paths=paths,
            evidence=[{"subtype": subtype, "sourceRecord": source_record, "records": records}],
            blocked_paths=paths,
            recommended_action="Acknowledge the evidence or defer it; acknowledgement does not resolve the underlying conflict.",
            allowed_actions=["acknowledge", "defer"],
            steps=["Inspect the conflict evidence.", "Resolve the source outside this queue when appropriate."],
        ))

    resolved_statuses = {"reviewed", "ignored", "promoted", "imported"}
    for record in sorted(
        files,
        key=lambda value: (_normalized_path(value.get("path")), str(value.get("contentHash", ""))),
    ):
        path = str(record.get("path", ""))
        if (
            not path
            or _normalized_path(path) in covered_paths
            or str(record.get("reviewStatus", "unreviewed")) in resolved_statuses
        ):
            continue
        evidence = _record_evidence(record)
        items.append(_queue_item(
            "pending_review",
            {"path": _normalized_path(path), "contentHash": evidence["contentHash"]},
            severity="high" if evidence["scanStatus"] == "missing" else "medium",
            reason="This scanned source still needs its existing per-file review status.",
            paths=[path],
            evidence=[evidence],
            blocked_paths=[path],
            recommended_action="Use the existing path-scoped --review-status action.",
            allowed_actions=["review_status"],
            steps=[
                "Review the source.",
                "Use scan-review <path> --review-status ... if a per-file status is appropriate.",
            ],
        ))

    items = _project_decisions(items, _candidate_decisions(payload))
    items.sort(key=lambda item: (item["status"] == "obsolete", item["kind"], item["id"]))
    open_items = [item for item in items if item["status"] in {"open", "in_review"}]
    affected_paths = {
        path
        for item in items
        if item["status"] != "obsolete"
        for path in item["paths"]
    }

    def counts(key: str) -> dict[str, int]:
        values: dict[str, int] = {}
        for item in items:
            value = str(item.get(key, ""))
            values[value] = values.get(value, 0) + 1
        return dict(sorted(values.items()))

    return {
        "schemaVersion": QUEUE_SCHEMA_VERSION,
        "generatedAt": generated_at,
        "source": {
            "indexPath": index_path,
            "indexGeneratedAt": str(payload.get("generatedAt", "")),
            "indexFingerprint": _fingerprint(payload),
        },
        "summary": {
            "openItemCount": len(open_items),
            "affectedRecordCount": len(affected_paths),
            "byKind": counts("kind"),
            "byStatus": counts("status"),
            "bySeverity": counts("severity"),
        },
        "items": items,
    }


def filter_queue_items(
    queue: dict,
    filter_name: str,
    *,
    selected_paths: set[str] | None = None,
) -> list[dict]:
    kind_by_filter = {
        "duplicate": "duplicate",
        "versions": "version",
        "conflict": "conflict",
        "pending": "pending_review",
    }
    items = list(queue.get("items", []))
    if filter_name in kind_by_filter:
        return [item for item in items if item.get("kind") == kind_by_filter[filter_name]]
    if filter_name == "all":
        return items
    if selected_paths is None:
        return []
    normalized_paths = {_normalized_path(path) for path in selected_paths}
    return [
        item
        for item in items
        if item.get("status") != "obsolete"
        and any(_normalized_path(path) in normalized_paths for path in item.get("paths", []))
    ]


def _acquire_lock(lock_path: Path, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            return
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ReviewQueueError("timed out waiting for the file index review lock")
            time.sleep(0.02)


def _atomic_json_replace(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_transaction_payload(index_path: Path) -> dict:
    if not index_path.exists():
        return {}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewQueueError(f"could not load file index: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewQueueError("file index must contain a JSON object")
    return payload


def validate_review_state(payload: dict) -> None:
    """Validate persisted relational state before any replacement."""
    if "reviewState" not in payload:
        return
    state = payload["reviewState"]
    if not isinstance(state, dict):
        raise ReviewQueueError("reviewState must be an object")
    if state.get("schemaVersion") != STATE_SCHEMA_VERSION:
        raise ReviewQueueError(f"reviewState.schemaVersion must be {STATE_SCHEMA_VERSION!r}")
    if not isinstance(state.get("candidateDecisions"), list):
        raise ReviewQueueError("reviewState.candidateDecisions must be a list")


def mutate_file_index(
    index_path: Path,
    mutate: Callable[[dict], Any],
    *,
    timeout_seconds: float = 5.0,
) -> Any:
    """Lock, reload, validate, mutate, atomically replace, then unlock."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = index_path.with_name(f"{index_path.name}.lock")
    _acquire_lock(lock_path, timeout_seconds=timeout_seconds)
    try:
        payload = _load_transaction_payload(index_path)
        validate_review_state(payload)
        result = mutate(payload)
        if not isinstance(payload, dict):  # pragma: no cover - callback contract guard
            raise ReviewQueueError("file index mutation must retain a JSON object")
        validate_review_state(payload)
        _atomic_json_replace(index_path, payload)
        return result
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def refresh_scan_index(index_path: Path, rebuild_payload: Callable[[dict], dict]) -> dict:
    """Rebuild a scan projection from the payload reloaded under lock."""
    def rebuild(current: dict) -> dict:
        refreshed = rebuild_payload(current)
        current.clear()
        current.update(refreshed)
        return current

    return mutate_file_index(index_path, rebuild)


def mutate_scan_record(
    index_path: Path,
    target_path: str,
    *,
    expected_content_hash: str,
    operation: str,
    schema_version: str,
    record_locator: Callable[[dict, str], tuple[dict | None, int]],
    mutate: Callable[[dict, dict], Any],
) -> Any:
    """Reload and revalidate one scan record before applying a writer."""
    normalized_path = target_path.replace("\\", "/").strip()

    def apply(current: dict) -> Any:
        if current.get("schemaVersion") != schema_version:
            raise ReviewQueueError("file index not found; run scan first")
        current_record, _index = record_locator(current, normalized_path)
        if not current_record:
            raise ReviewQueueError(f"path is no longer in file index: {normalized_path}")
        if str(current_record.get("contentHash", "")) != expected_content_hash:
            raise ReviewQueueError(f"{operation} precondition changed for {normalized_path}; rerun command")
        return mutate(current, current_record)

    return mutate_file_index(index_path, apply)


def apply_relational_decision(
    index_path: Path,
    queue_builder: Callable[[dict], dict],
    *,
    item_id: str,
    action: str,
    canonical_path: str,
    note: str,
    fingerprint: str,
    decided_at: str,
) -> dict:
    """Validate and apply one relation-level decision inside a transaction."""
    if not fingerprint.strip():
        raise ReviewQueueError(
            "--fingerprint is required and must be non-empty for item-scoped relational actions"
        )

    def apply(payload: dict) -> dict:
        queue = queue_builder(payload)
        item = next((candidate for candidate in queue.get("items", []) if candidate.get("id") == item_id), None)
        if not item:
            raise ReviewQueueError(f"unknown review queue item: {item_id}")
        if item.get("status") == "obsolete":
            raise ReviewQueueError("cannot act on an obsolete review queue item")
        if item.get("kind") not in RELATIONAL_KINDS:
            raise ReviewQueueError("item-scoped relational actions are not available for pending_review")
        if action not in item.get("allowedActions", []):
            raise ReviewQueueError(f"action {action!r} is not allowed for {item.get('kind')}")
        if fingerprint != item.get("fingerprint"):
            raise ReviewQueueError("stale fingerprint for the current review queue item")
        if action == "select_canonical" and canonical_path not in item.get("paths", []):
            raise ReviewQueueError("--canonical must name a current member path of the review queue item")
        if action == "accept_parallel" and not note.strip():
            raise ReviewQueueError("--note is required for accept_parallel")

        state = payload.get("reviewState")
        if state is None:
            state = {"schemaVersion": STATE_SCHEMA_VERSION, "candidateDecisions": []}
            payload["reviewState"] = state
        validate_review_state(payload)
        decisions = state["candidateDecisions"]
        current_keys = {
            (str(candidate.get("candidateId", candidate.get("id", ""))), str(candidate.get("fingerprint", "")))
            for candidate in queue.get("items", [])
            if candidate.get("status") != "obsolete"
        }
        retained = []
        for existing in _effective_decisions([value for value in decisions if isinstance(value, dict)]):
            key = (str(existing.get("candidateId", "")), str(existing.get("fingerprint", "")))
            retained.append({
                **existing,
                "status": existing.get("status", "open") if key in current_keys else "obsolete",
            })
        decision = {
            "candidateId": item["id"],
            "fingerprint": item["fingerprint"],
            "kind": item["kind"],
            "action": action,
            "status": _decision_status(item["kind"], action),
            "canonicalPath": canonical_path if action == "select_canonical" else "",
            "note": note,
            "decidedAt": decided_at,
            "paths": item["paths"],
            "evidence": item["evidence"],
            "blockedPaths": item["blockedPaths"],
            "severity": item["severity"],
        }
        decision_key = (decision["candidateId"], decision["fingerprint"])
        retained = [
            existing
            for existing in retained
            if (str(existing.get("candidateId", "")), str(existing.get("fingerprint", ""))) != decision_key
        ]
        retained.append(decision)
        state["candidateDecisions"] = retained
        return {"decision": decision, "queue": queue_builder(payload), "payload": payload}

    return mutate_file_index(index_path, apply)


def run_scan_review(args, features) -> int:
    read_file_index = features.read_file_index
    scan_filter_names = features.SCAN_REVIEW_FILTERS
    build_scan_analysis = features.build_scan_analysis
    utc_iso = features.utc_iso
    file_index_relative_path = features.FILE_INDEX_PATH
    file_index_path = features.file_index_path
    scan_review_summary = features.scan_review_summary
    rel = features.rel
    batch_review_statuses = features.SCAN_BATCH_REVIEW_STATUSES
    select_scan_review_records = features.select_scan_review_records
    scan_promotion_blockers = features.scan_promotion_blockers
    promotable_review_statuses = features.SCAN_PROMOTABLE_REVIEW_STATUSES
    write_scan_review_proposal = features.write_scan_review_proposal
    scan_schema_version = features.SCAN_SCHEMA_VERSION
    scan_record_by_path = features.scan_record_by_path
    scan_review_flags = features.scan_review_flags
    scan_review_statuses = features.SCAN_REVIEW_STATUSES
    scan_promotion_warnings = features.scan_promotion_warnings

    project = args.project_root.resolve()
    payload = read_file_index(project)
    if not payload:
        print(json.dumps({"ok": False, "error": "file index not found; run scan first"}, indent=2))
        return 1
    filter_name = str(getattr(args, "filter", "pending") or "pending")
    if filter_name not in scan_filter_names:
        print(json.dumps({"ok": False, "error": f"unknown review filter: {filter_name}"}, indent=2))
        return 1
    item_id = str(getattr(args, "item", "") or "").strip()
    action = str(getattr(args, "action", "") or "").strip()
    if item_id:
        if bool(getattr(args, "batch", False)):
            print(json.dumps({"ok": False, "error": "--batch is valid only for homogeneous pending_review operations"}, indent=2))
            return 1
        if not action:
            print(json.dumps({"ok": False, "error": "--action is required with --item"}, indent=2))
            return 1
        fingerprint = str(getattr(args, "fingerprint", "") or "").strip()
        if not fingerprint:
            print(json.dumps({
                "ok": False,
                "error": "--fingerprint is required and must be non-empty for item-scoped relational actions",
            }, indent=2))
            return 1

        def queue_builder(current_payload: dict) -> dict:
            analysis = build_scan_analysis(
                project,
                current_payload,
                limit=max(len(current_payload.get("files", [])), 1),
            )
            return build_review_queue(
                current_payload,
                analysis,
                generated_at=utc_iso(),
                index_path=file_index_relative_path.as_posix(),
            )

        try:
            applied = apply_relational_decision(
                file_index_path(project),
                queue_builder,
                item_id=item_id,
                action=action,
                canonical_path=str(getattr(args, "canonical", "") or "").strip().replace("\\", "/"),
                note=str(getattr(args, "note", "") or "").strip(),
                fingerprint=fingerprint,
                decided_at=utc_iso(),
            )
        except ReviewQueueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
        queue = applied["queue"]
        queue["items"] = filter_queue_items(queue, "all")[: max(1, getattr(args, "limit", 20))]
        payload = applied["payload"]
        summary = scan_review_summary(
            payload,
            limit=getattr(args, "limit", 20),
            project=project,
            filter_name=filter_name,
        )
        summary["managedReviewQueue"] = queue["summary"]
        print(json.dumps({
            "ok": True,
            "indexPath": rel(project, file_index_path(project)),
            "decision": applied["decision"],
            "summary": summary,
            "queue": queue,
        }, indent=2))
        return 0

    target_path = str(getattr(args, "path", "") or "").strip().replace("\\", "/")
    if not target_path:
        review_status = str(getattr(args, "review_status", "") or "").strip()
        batch = bool(getattr(args, "batch", False))
        if batch:
            if not review_status:
                print(json.dumps({"ok": False, "error": "--review-status is required with --batch"}, indent=2))
                return 1
            if review_status not in batch_review_statuses:
                print(json.dumps({"ok": False, "error": f"review status cannot be batch-applied: {review_status}"}, indent=2))
                return 1
            sensitivity = str(getattr(args, "sensitivity", "") or "").strip()
            note = str(getattr(args, "note", "") or "").strip()
            batch_failure: dict | None = None
            try:
                def apply_batch(current: dict) -> dict:
                    nonlocal batch_failure
                    if current.get("schemaVersion") != scan_schema_version:
                        raise ReviewQueueError("file index not found; run scan first")
                    selected, flags = select_scan_review_records(current, project, filter_name)
                    analysis = build_scan_analysis(
                        project,
                        current,
                        limit=max(len(current.get("files", [])), 1),
                    )
                    managed_queue = build_review_queue(
                        current,
                        analysis,
                        generated_at=utc_iso(),
                        index_path=file_index_relative_path.as_posix(),
                    )
                    managed_kinds_by_path: dict[str, set[str]] = {}
                    for item in managed_queue.get("items", []):
                        if item.get("status") == "obsolete":
                            continue
                        kind = str(item.get("kind", ""))
                        for item_path in item.get("paths", []):
                            normalized = str(item_path).replace("\\", "/").casefold()
                            managed_kinds_by_path.setdefault(normalized, set()).add(kind)
                    invalid_records = []
                    for record in selected:
                        record_path = str(record.get("path", "")).replace("\\", "/")
                        managed_kinds = managed_kinds_by_path.get(record_path.casefold(), set())
                        if managed_kinds != {"pending_review"}:
                            invalid_records.append({"path": record_path, "managedKinds": sorted(managed_kinds)})
                    if invalid_records:
                        raise ReviewQueueError(
                            "--batch is valid only for homogeneous pending_review operations; every selected record "
                            "must belong exclusively to a managed pending_review item, so relational candidates "
                            "require an item-scoped action"
                        )
                    if review_status == "ready_to_promote":
                        blocked = []
                        for record in selected:
                            record_flags = flags.get(str(record.get("path", "")), set())
                            blockers = scan_promotion_blockers(project, current, record, record_flags)
                            if blockers:
                                blocked.append({
                                    "path": str(record.get("path", "")),
                                    "reviewFlags": sorted(record_flags),
                                    "promotionBlockers": blockers,
                                })
                        if blocked:
                            batch_failure = {
                                "ok": False,
                                "error": "ready_to_promote cannot be applied while promotion blockers exist",
                                "filter": filter_name,
                                "blocked": blocked,
                                "blockedCount": len(blocked),
                            }
                            raise ReviewQueueError("ready_to_promote cannot be applied while promotion blockers exist")
                    now = utc_iso()
                    for record in selected:
                        record["reviewStatus"] = review_status
                        if sensitivity:
                            record["sensitivity"] = sensitivity
                        record["reviewedAt"] = now
                        if note:
                            record["reviewNote"] = note
                        record["reviewFlags"] = sorted(flags.get(str(record.get("path", "")), set()))
                        if review_status in promotable_review_statuses:
                            record["reviewedContentHash"] = str(record.get("contentHash", ""))
                        else:
                            record.pop("reviewedContentHash", None)
                    return {"payload": current, "selected": selected}

                batch_result = mutate_file_index(file_index_path(project), apply_batch)
            except ReviewQueueError as exc:
                print(json.dumps(batch_failure or {"ok": False, "error": str(exc)}, indent=2))
                return 1
            payload = batch_result["payload"]
            selected = batch_result["selected"]
        analysis = build_scan_analysis(project, payload, limit=max(len(payload.get("files", [])), 1))
        queue = build_review_queue(
            payload,
            analysis,
            generated_at=utc_iso(),
            index_path=file_index_relative_path.as_posix(),
        )
        filtered_records, _filtered_flags = select_scan_review_records(payload, project, filter_name)
        selected_paths = {str(record.get("path", "")) for record in filtered_records}
        queue["items"] = filter_queue_items(
            queue,
            filter_name,
            selected_paths=selected_paths,
        )[: max(1, getattr(args, "limit", 20))]
        summary = scan_review_summary(
            payload,
            limit=getattr(args, "limit", 20),
            project=project,
            filter_name=filter_name,
        )
        summary["managedReviewQueue"] = queue["summary"]
        result = {
            "ok": True,
            "indexPath": rel(project, file_index_path(project)),
            "summary": summary,
            "queue": queue,
        }
        if batch:
            result["batchReview"] = {
                "filter": filter_name,
                "reviewStatus": review_status,
                "updated": len(selected),
            }
        if getattr(args, "proposal", False):
            try:
                proposal = write_scan_review_proposal(project, result["summary"], getattr(args, "output", None))
            except ValueError as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
                return 1
            result["proposalPath"] = rel(project, proposal)
        print(json.dumps(result, indent=2))
        return 0
    review_status = str(getattr(args, "review_status", "") or "").strip()
    if not review_status:
        print(json.dumps({"ok": False, "error": "--review-status is required when reviewing one path"}, indent=2))
        return 1
    if review_status not in scan_review_statuses:
        print(json.dumps({"ok": False, "error": f"unknown review status: {review_status}"}, indent=2))
        return 1
    sensitivity = str(getattr(args, "sensitivity", "") or "").strip()
    note = str(getattr(args, "note", "") or "").strip()
    path_failure: dict | None = None
    try:
        def apply_path_review(current: dict) -> dict:
            nonlocal path_failure
            if current.get("schemaVersion") != scan_schema_version:
                raise ReviewQueueError("file index not found; run scan first")
            record, _index = scan_record_by_path(current, target_path)
            if not record:
                raise ReviewQueueError(f"path is not in file index: {target_path}")
            record_flags = scan_review_flags(project, current).get(target_path, set())
            if review_status == "ready_to_promote":
                blockers = scan_promotion_blockers(project, current, record, record_flags)
                if blockers:
                    path_failure = {
                        "ok": False,
                        "error": "ready_to_promote cannot be applied while promotion blockers exist",
                        "path": target_path,
                        "reviewFlags": sorted(record_flags),
                        "promotionBlockers": blockers,
                    }
                    raise ReviewQueueError("ready_to_promote cannot be applied while promotion blockers exist")
            record["reviewStatus"] = review_status
            if sensitivity:
                record["sensitivity"] = sensitivity
            record["reviewedAt"] = utc_iso()
            if note:
                record["reviewNote"] = note
            if review_status in promotable_review_statuses:
                record["reviewedContentHash"] = str(record.get("contentHash", ""))
            else:
                record.pop("reviewedContentHash", None)
            return {"payload": current, "record": record}

        path_result = mutate_file_index(file_index_path(project), apply_path_review)
    except ReviewQueueError as exc:
        print(json.dumps(path_failure or {"ok": False, "error": str(exc)}, indent=2))
        return 1
    payload = path_result["payload"]
    record = path_result["record"]
    current_flags = scan_review_flags(project, payload).get(target_path, set())
    print(json.dumps({
        "ok": True,
        "path": target_path,
        "reviewStatus": record["reviewStatus"],
        "sensitivity": record.get("sensitivity", "unknown"),
        "reviewFlags": sorted(current_flags),
        "promotionBlockers": scan_promotion_blockers(project, payload, record, current_flags),
        "promotionWarnings": scan_promotion_warnings(project, record),
        "indexPath": rel(project, file_index_path(project)),
    }, indent=2))
    return 0
