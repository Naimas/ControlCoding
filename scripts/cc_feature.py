"""Feature work contracts and WIP state machine for ControlCoding."""

from __future__ import annotations

import datetime
import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

FEATURE_STATE_SCHEMA_VERSION = "cc-feature-state/v1"
FEATURE_STATES = {"planned", "active", "verifying", "passing", "blocked", "completed", "aborted"}
FEATURE_IN_PROGRESS_STATES = {"active", "verifying", "passing", "blocked"}
FEATURE_ROUTED_COMMANDS = frozenset({
    ("feature",),
    ("feature", "init"),
    ("feature", "status"),
    ("feature", "start"),
    ("feature", "show"),
    ("feature", "verify"),
    ("feature", "complete"),
    ("feature", "abort"),
})
FEATURE_CAPABILITY_REGISTRY_ITEM = {
    "id": "feature_state_machine",
    "label": "Feature work contract and WIP state machine",
    "state": "experimental",
    "control_level": "conditional",
    "hosts": ["all"],
    "commands": ["feature init", "feature status", "feature start", "feature show", "feature verify", "feature complete", "feature abort"],
    "evidence": [".controlcoding/features/features.json", "feature verification receipts", "tests/test_cc_cli.py"],
}


def _now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _registry_path(project: Path) -> Path:
    return project / ".controlcoding" / "features" / "features.json"


def _project_relative_label(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def _empty_registry() -> dict[str, Any]:
    return {
        "schemaVersion": FEATURE_STATE_SCHEMA_VERSION,
        "createdAt": _now_iso(),
        "updatedAt": "",
        "wipLimit": 1,
        "features": [],
    }


def _read_registry(project: Path) -> tuple[dict[str, Any], bool, list[str]]:
    path = _registry_path(project)
    if not path.exists():
        return _empty_registry(), False, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _empty_registry(), True, [f"invalid feature registry JSON: {exc}"]
    if not isinstance(raw, dict):
        return _empty_registry(), True, ["feature registry root must be an object"]
    registry = _empty_registry()
    registry.update(raw)
    if not isinstance(registry.get("features"), list):
        registry["features"] = []
    return registry, True, _validate_registry(registry)


def _write_registry(project: Path, registry: dict[str, Any]) -> Path:
    path = _registry_path(project)
    registry["schemaVersion"] = FEATURE_STATE_SCHEMA_VERSION
    registry["updatedAt"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _normalize_id(feature_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(feature_id or "").strip().lower()).strip("-._")
    return normalized[:80]


def _normalize_list(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _find_feature(registry: dict[str, Any], feature_id: str) -> dict[str, Any] | None:
    normalized = _normalize_id(feature_id)
    for feature in registry.get("features", []):
        if isinstance(feature, dict) and str(feature.get("id") or "") == normalized:
            return feature
    return None


def _in_progress_features(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        feature
        for feature in registry.get("features", [])
        if isinstance(feature, dict) and str(feature.get("state") or "") in FEATURE_IN_PROGRESS_STATES
    ]


def _validate_registry(registry: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    seen: set[str] = set()
    for index, feature in enumerate(registry.get("features", [])):
        if not isinstance(feature, dict):
            issues.append(f"feature entry {index} is not an object")
            continue
        feature_id = str(feature.get("id") or "").strip()
        if not feature_id:
            issues.append(f"feature entry {index} has no id")
        elif feature_id in seen:
            issues.append(f"duplicate feature id: {feature_id}")
        seen.add(feature_id)
        state = str(feature.get("state") or "").strip()
        if state not in FEATURE_STATES:
            issues.append(f"feature {feature_id or index} has invalid state: {state}")
    in_progress = _in_progress_features(registry)
    if len(in_progress) > int(registry.get("wipLimit") or 1):
        ids = ", ".join(str(feature.get("id") or "") for feature in in_progress)
        issues.append(f"WIP limit exceeded: {ids}")
    return issues


def _summary(registry: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {state: 0 for state in sorted(FEATURE_STATES)}
    for feature in registry.get("features", []):
        if isinstance(feature, dict):
            state = str(feature.get("state") or "")
            counts[state] = counts.get(state, 0) + 1
    in_progress = _in_progress_features(registry)
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "inProgressCount": len(in_progress),
        "activeFeatureIds": [str(feature.get("id") or "") for feature in in_progress],
        "wipLimit": int(registry.get("wipLimit") or 1),
    }


def _event(kind: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "at": _now_iso(),
        "kind": kind,
        "message": message,
        "data": data or {},
    }


def _append_event(feature: dict[str, Any], kind: str, message: str, data: dict[str, Any] | None = None) -> None:
    events = feature.get("events")
    if not isinstance(events, list):
        events = []
    events.append(_event(kind, message, data))
    feature["events"] = events[-100:]


def _feature_payload(project: Path, registry: dict[str, Any], initialized: bool, issues: list[str]) -> dict[str, Any]:
    summary = _summary(registry)
    in_progress = _in_progress_features(registry)
    blocked = [
        feature
        for feature in registry.get("features", [])
        if isinstance(feature, dict) and str(feature.get("state") or "") == "blocked"
    ]
    return {
        "ok": not issues,
        "readOnly": True,
        "initialized": initialized,
        "schemaVersion": FEATURE_STATE_SCHEMA_VERSION,
        "registryPath": _project_relative_label(project, _registry_path(project)),
        "summary": summary,
        "currentFeature": in_progress[0] if in_progress else {},
        "blockedFeatureIds": [str(feature.get("id") or "") for feature in blocked],
        "issues": issues,
        "features": registry.get("features", []),
        "commands": {
            "status": "python scripts/cc.py feature status --project-root .",
            "show": "python scripts/cc.py feature show --project-root . <feature-id>",
            "start": "python scripts/cc.py feature start <feature-id> --project-root . --title \"<title>\" --objective \"<objective>\" --acceptance \"<criterion>\" --check \"<verification command>\"",
            "verify": "python scripts/cc.py feature verify <feature-id> --project-root . --run \"<verification command>\"",
            "complete": "python scripts/cc.py feature complete <feature-id> --project-root . --summary \"<summary>\"",
        },
    }


def _print_json_or_text(json_output: bool, payload: dict[str, Any], text: str, ok_exit: bool = True) -> int:
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(text)
    return 0 if ok_exit else 1


def _status_text(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "Feature state machine",
        f"Registry: {payload.get('registryPath', '')}",
        f"Initialized: {payload.get('initialized')}",
        f"WIP: {summary.get('inProgressCount', 0)}/{summary.get('wipLimit', 1)}",
    ]
    issues = payload.get("issues", [])
    if issues:
        lines.append("Issues:")
        lines.extend(f"- {issue}" for issue in issues)
    features = payload.get("features", [])
    if features:
        lines.append("Features:")
        for feature in features:
            lines.append(f"- {feature.get('id')} [{feature.get('state')}] {feature.get('title', '')}")
    else:
        lines.append("Features: none")
    return "\n".join(lines)


def feature_status_payload(project: Path) -> dict[str, Any]:
    """Return a read-only feature registry status payload."""
    registry, initialized, issues = _read_registry(project)
    return _feature_payload(project, registry, initialized, issues)


def cmd_feature_init(project: Path, force: bool = False, json_output: bool = False) -> int:
    registry, initialized, issues = _read_registry(project)
    path = _registry_path(project)
    if initialized and not force and not issues:
        payload = _feature_payload(project, registry, initialized=True, issues=[])
        payload["written"] = False
        return _print_json_or_text(json_output, payload, f"Feature registry already exists: {payload['registryPath']}")
    registry = _empty_registry()
    path = _write_registry(project, registry)
    payload = _feature_payload(project, registry, initialized=True, issues=[])
    payload["written"] = True
    payload["registryPath"] = _project_relative_label(project, path)
    return _print_json_or_text(json_output, payload, f"Feature registry initialized: {payload['registryPath']}")


def cmd_feature_status(project: Path, json_output: bool = False) -> int:
    payload = feature_status_payload(project)
    return _print_json_or_text(json_output, payload, _status_text(payload), ok_exit=bool(payload.get("ok")))


def cmd_feature_show(project: Path, feature_id: str, json_output: bool = False) -> int:
    registry, initialized, issues = _read_registry(project)
    normalized = _normalize_id(feature_id)
    feature = _find_feature(registry, normalized)
    if not initialized:
        issues.append("feature registry is missing")
    if feature is None:
        issues.append(f"feature not found: {normalized or feature_id}")
    payload = {
        "ok": not issues,
        "schemaVersion": FEATURE_STATE_SCHEMA_VERSION,
        "registryPath": _project_relative_label(project, _registry_path(project)),
        "feature": feature or {},
        "issues": issues,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if feature:
            print(f"Feature {feature['id']} [{feature['state']}] {feature.get('title', '')}")
            print(f"Objective: {feature.get('objective', '')}")
        for issue in issues:
            print(f"Issue: {issue}")
    return 0 if payload["ok"] else 1


def cmd_feature_start(
    project: Path,
    feature_id: str,
    title: str,
    objective: str,
    scope: str = "",
    acceptance: list[str] | None = None,
    non_goal: list[str] | None = None,
    check: list[str] | None = None,
    json_output: bool = False,
) -> int:
    registry, _initialized, issues = _read_registry(project)
    normalized = _normalize_id(feature_id)
    title = str(title or "").strip()
    objective = str(objective or "").strip()
    acceptance_items = _normalize_list(acceptance)
    non_goal_items = _normalize_list(non_goal)
    check_items = _normalize_list(check)
    if not normalized:
        issues.append("feature id is required")
    if not title:
        issues.append("feature title is required")
    if not objective:
        issues.append("feature objective is required")
    if not acceptance_items:
        issues.append("at least one acceptance criterion is required")
    if not check_items:
        issues.append("at least one verification check is required")
    in_progress = [feature for feature in _in_progress_features(registry) if feature.get("id") != normalized]
    if in_progress:
        current = ", ".join(str(feature.get("id") or "") for feature in in_progress)
        issues.append(f"WIP limit is 1; finish or abort current feature first: {current}")
    if issues:
        payload = _feature_payload(project, registry, initialized=_registry_path(project).exists(), issues=issues)
        return _print_json_or_text(json_output, payload, _status_text(payload), ok_exit=False)

    now = _now_iso()
    feature = _find_feature(registry, normalized)
    if feature is None:
        feature = {
            "id": normalized,
            "title": title,
            "state": "active",
            "objective": objective,
            "scope": str(scope or "").strip(),
            "acceptanceCriteria": acceptance_items,
            "nonGoals": non_goal_items,
            "verificationChecks": check_items,
            "verificationReceipts": [],
            "events": [],
            "createdAt": now,
            "updatedAt": now,
        }
        registry["features"].append(feature)
        _append_event(feature, "start", "feature started")
    else:
        feature.update({
            "title": title,
            "state": "active",
            "objective": objective,
            "scope": str(scope or "").strip(),
            "acceptanceCriteria": acceptance_items,
            "nonGoals": non_goal_items,
            "verificationChecks": check_items,
            "updatedAt": now,
        })
        _append_event(feature, "restart", "feature work restarted")
    path = _write_registry(project, registry)
    payload = _feature_payload(project, registry, initialized=True, issues=[])
    payload["feature"] = feature
    payload["registryPath"] = _project_relative_label(project, path)
    return _print_json_or_text(
        json_output,
        payload,
        f"Feature active: {feature['id']} [{feature['state']}]\nRegistry: {payload['registryPath']}",
    )


def _run_check(project: Path, command: str) -> dict[str, Any]:
    started = time.perf_counter()
    result_payload = {
        "command": command,
        "status": "failed",
        "returnCode": -1,
        "durationMs": 0,
        "stdoutTail": "",
        "stderrTail": "",
    }
    try:
        args = shlex.split(command)
        result = subprocess.run(args, cwd=str(project), capture_output=True, text=True, timeout=300)
        result_payload["returnCode"] = result.returncode
        result_payload["stdoutTail"] = (result.stdout or "")[-4000:]
        result_payload["stderrTail"] = (result.stderr or "")[-4000:]
        result_payload["status"] = "passed" if result.returncode == 0 else "failed"
    except Exception as exc:
        result_payload["stderrTail"] = str(exc)
    result_payload["durationMs"] = int((time.perf_counter() - started) * 1000)
    return result_payload


def cmd_feature_verify(
    project: Path,
    feature_id: str,
    run_checks: list[str] | None = None,
    evidence: list[str] | None = None,
    json_output: bool = False,
) -> int:
    registry, initialized, issues = _read_registry(project)
    normalized = _normalize_id(feature_id)
    feature = _find_feature(registry, normalized)
    run_items = _normalize_list(run_checks)
    evidence_items = _normalize_list(evidence)
    if not initialized:
        issues.append("feature registry is missing")
    if feature is None:
        issues.append(f"feature not found: {normalized or feature_id}")
    if not run_items and not evidence_items:
        issues.append("verification requires at least one --run or --evidence item")
    if issues:
        payload = _feature_payload(project, registry, initialized, issues)
        return _print_json_or_text(json_output, payload, _status_text(payload), ok_exit=False)

    assert feature is not None
    now = _now_iso()
    results = [_run_check(project, command) for command in run_items]
    has_failed_run = any(result["status"] != "passed" for result in results)
    has_run_checks = bool(results)
    next_state = "blocked" if has_failed_run else "passing" if has_run_checks else "verifying"
    receipt = {
        "id": "feature_verify_" + now.replace(":", "").replace(".", "").replace("Z", "Z"),
        "createdAt": now,
        "status": "passed" if next_state == "passing" else "failed" if next_state == "blocked" else "manual_evidence_recorded",
        "runChecks": results,
        "evidence": evidence_items,
    }
    receipts = feature.get("verificationReceipts")
    if not isinstance(receipts, list):
        receipts = []
    receipts.append(receipt)
    feature["verificationReceipts"] = receipts[-50:]
    feature["state"] = next_state
    feature["updatedAt"] = now
    _append_event(feature, "verify", f"verification recorded with state {next_state}", {"receiptId": receipt["id"]})
    path = _write_registry(project, registry)
    payload = _feature_payload(project, registry, initialized=True, issues=[])
    payload["feature"] = feature
    payload["receipt"] = receipt
    payload["registryPath"] = _project_relative_label(project, path)
    text = f"Feature verification: {feature['id']} -> {feature['state']}\nReceipt: {receipt['id']}"
    return _print_json_or_text(json_output, payload, text, ok_exit=next_state != "blocked")


def cmd_feature_complete(project: Path, feature_id: str, summary: str = "", json_output: bool = False) -> int:
    registry, initialized, issues = _read_registry(project)
    normalized = _normalize_id(feature_id)
    feature = _find_feature(registry, normalized)
    if not initialized:
        issues.append("feature registry is missing")
    if feature is None:
        issues.append(f"feature not found: {normalized or feature_id}")
    elif feature.get("state") != "passing":
        issues.append("feature must be in passing state before completion")
    if issues:
        payload = _feature_payload(project, registry, initialized, issues)
        return _print_json_or_text(json_output, payload, _status_text(payload), ok_exit=False)
    assert feature is not None
    feature["state"] = "completed"
    feature["completedAt"] = _now_iso()
    feature["updatedAt"] = feature["completedAt"]
    if str(summary or "").strip():
        feature["completionSummary"] = str(summary or "").strip()
    _append_event(feature, "complete", "feature completed", {"summary": str(summary or "").strip()})
    path = _write_registry(project, registry)
    payload = _feature_payload(project, registry, initialized=True, issues=[])
    payload["feature"] = feature
    payload["registryPath"] = _project_relative_label(project, path)
    return _print_json_or_text(json_output, payload, f"Feature completed: {feature['id']}")


def cmd_feature_abort(project: Path, feature_id: str, reason: str, json_output: bool = False) -> int:
    registry, initialized, issues = _read_registry(project)
    normalized = _normalize_id(feature_id)
    feature = _find_feature(registry, normalized)
    reason = str(reason or "").strip()
    if not initialized:
        issues.append("feature registry is missing")
    if feature is None:
        issues.append(f"feature not found: {normalized or feature_id}")
    if not reason:
        issues.append("abort reason is required")
    if issues:
        payload = _feature_payload(project, registry, initialized, issues)
        return _print_json_or_text(json_output, payload, _status_text(payload), ok_exit=False)
    assert feature is not None
    feature["state"] = "aborted"
    feature["abortedAt"] = _now_iso()
    feature["updatedAt"] = feature["abortedAt"]
    feature["abortReason"] = reason
    _append_event(feature, "abort", "feature aborted", {"reason": reason})
    path = _write_registry(project, registry)
    payload = _feature_payload(project, registry, initialized=True, issues=[])
    payload["feature"] = feature
    payload["registryPath"] = _project_relative_label(project, path)
    return _print_json_or_text(json_output, payload, f"Feature aborted: {feature['id']}")


def add_feature_parser(subparsers: Any, path_type: Any) -> Any:
    p_feature = subparsers.add_parser(
        "feature",
        help="Manage feature work contracts and WIP state",
    )
    p_feature.add_argument(
        "--project-root", type=path_type, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    feature_sub = p_feature.add_subparsers(dest="feature_command")

    p_feature_init = feature_sub.add_parser("init", help="Create the feature state registry")
    p_feature_init.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_feature_init.add_argument("--force", action="store_true", help="Recreate the feature registry")
    p_feature_init.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_feature_status = feature_sub.add_parser("status", help="Show feature WIP and state")
    p_feature_status.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_feature_status.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_feature_start = feature_sub.add_parser("start", help="Start or restart one active feature contract")
    p_feature_start.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_feature_start.add_argument("feature_id", help="Stable feature id")
    p_feature_start.add_argument("--title", required=True, help="Short feature title")
    p_feature_start.add_argument("--objective", required=True, help="Bounded feature objective")
    p_feature_start.add_argument("--scope", default="", help="Scope or ownership summary")
    p_feature_start.add_argument("--acceptance", action="append", default=[], help="Acceptance criterion (repeatable)")
    p_feature_start.add_argument("--non-goal", action="append", default=[], help="Explicit non-goal (repeatable)")
    p_feature_start.add_argument("--check", action="append", default=[], help="Required verification check (repeatable)")
    p_feature_start.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_feature_show = feature_sub.add_parser("show", help="Show one feature contract")
    p_feature_show.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_feature_show.add_argument("feature_id", help="Feature id")
    p_feature_show.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_feature_verify = feature_sub.add_parser("verify", help="Run or record verification for a feature")
    p_feature_verify.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_feature_verify.add_argument("feature_id", help="Feature id")
    p_feature_verify.add_argument("--run", action="append", default=[], dest="run_checks", help="Verification command to execute (repeatable)")
    p_feature_verify.add_argument("--evidence", action="append", default=[], help="Evidence reference or summary (repeatable)")
    p_feature_verify.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_feature_complete = feature_sub.add_parser("complete", help="Complete a passing feature")
    p_feature_complete.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_feature_complete.add_argument("feature_id", help="Feature id")
    p_feature_complete.add_argument("--summary", default="", help="Completion summary")
    p_feature_complete.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_feature_abort = feature_sub.add_parser("abort", help="Abort a feature and free WIP")
    p_feature_abort.add_argument("--project-root", type=path_type, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_feature_abort.add_argument("feature_id", help="Feature id")
    p_feature_abort.add_argument("--reason", required=True, help="Abort reason")
    p_feature_abort.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")
    return p_feature


def dispatch_feature_command(args: Any, project: Path, parser: Any) -> int:
    feature_command = getattr(args, "feature_command", "")
    if feature_command == "init":
        return cmd_feature_init(project, force=getattr(args, "force", False), json_output=getattr(args, "json_output", False))
    if feature_command == "status":
        return cmd_feature_status(project, json_output=getattr(args, "json_output", False))
    if feature_command == "start":
        return cmd_feature_start(
            project,
            feature_id=getattr(args, "feature_id", ""),
            title=getattr(args, "title", ""),
            objective=getattr(args, "objective", ""),
            scope=getattr(args, "scope", ""),
            acceptance=getattr(args, "acceptance", []),
            non_goal=getattr(args, "non_goal", []),
            check=getattr(args, "check", []),
            json_output=getattr(args, "json_output", False),
        )
    if feature_command == "show":
        return cmd_feature_show(project, feature_id=getattr(args, "feature_id", ""), json_output=getattr(args, "json_output", False))
    if feature_command == "verify":
        return cmd_feature_verify(
            project,
            feature_id=getattr(args, "feature_id", ""),
            run_checks=getattr(args, "run_checks", []),
            evidence=getattr(args, "evidence", []),
            json_output=getattr(args, "json_output", False),
        )
    if feature_command == "complete":
        return cmd_feature_complete(project, feature_id=getattr(args, "feature_id", ""), summary=getattr(args, "summary", ""), json_output=getattr(args, "json_output", False))
    if feature_command == "abort":
        return cmd_feature_abort(project, feature_id=getattr(args, "feature_id", ""), reason=getattr(args, "reason", ""), json_output=getattr(args, "json_output", False))
    parser.print_help()
    return 1


def feature_doctor_check(project: Path) -> dict[str, Any]:
    payload = feature_status_payload(project)
    summary = payload.get("summary", {})
    current = payload.get("currentFeature", {})
    blocked = payload.get("blockedFeatureIds", [])
    if not payload.get("initialized"):
        return {"payload": payload, "level": "info", "detail": "not configured", "message": "not configured (run `cc feature init` or `cc feature start`)", "issueCount": 0}
    if not payload.get("ok"):
        issues = payload.get("issues", [])
        detail = "; ".join(issues) if issues else "invalid"
        return {"payload": payload, "level": "warn", "detail": detail, "message": detail, "issueCount": 1}
    if blocked:
        detail = "blocked=" + ", ".join(blocked)
        return {"payload": payload, "level": "warn", "detail": detail, "message": detail, "issueCount": 1}
    if summary.get("inProgressCount"):
        detail = f"active={current.get('id', '')}; state={current.get('state', '')}; WIP={summary.get('inProgressCount')}/{summary.get('wipLimit')}"
        return {"payload": payload, "level": "ok", "detail": detail, "message": detail, "issueCount": 0}
    return {"payload": payload, "level": "info", "detail": "no active feature", "message": "no active feature", "issueCount": 0}


__all__ = [
    "FEATURE_CAPABILITY_REGISTRY_ITEM",
    "FEATURE_IN_PROGRESS_STATES",
    "FEATURE_ROUTED_COMMANDS",
    "FEATURE_STATE_SCHEMA_VERSION",
    "FEATURE_STATES",
    "add_feature_parser",
    "cmd_feature_abort",
    "cmd_feature_complete",
    "cmd_feature_init",
    "cmd_feature_show",
    "cmd_feature_start",
    "cmd_feature_status",
    "cmd_feature_verify",
    "dispatch_feature_command",
    "feature_doctor_check",
    "feature_status_payload",
]
