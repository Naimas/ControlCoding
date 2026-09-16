"""Local receipt lifecycle and conservative, read-only evidence assessment."""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
import stat
import uuid

import cc_evidence_inputs as inputs
from cc_evidence_process import run_command

SCHEMA_VERSION = 2
TYPES = {"verification": ("verify", "suites", "verification"),
         "invariants": ("invariants", "invariants", "invariant")}
ID = re.compile(r"(?:verify|invariants)_\d{8}T\d{12}Z_[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
STATES = {"running", "passed", "passed_subset", "failed", "incomplete"}
SELECTOR_KINDS = {
    "verification": {"targeted", "regression", "invariant", "smoke", "release"},
    "invariants": {"domain", "structural", "security", "consistency", "determinism", "performance", "fitness"},
}


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _folder(kind):
    return ".controlcoding/" + TYPES[kind][2] + "_receipts"


def _assessment(state, *reasons, current=False):
    return {"state": state, "currentRequiredPass": current, "reasons": list(reasons)}


def _ids(value):
    return isinstance(value, list) and len(value) <= 1000 and all(isinstance(v, str) and v and len(v) <= 200 for v in value) and len(set(value)) == len(value)


def normalize_requested(kind, ids, kinds, domains, all_entries, selected):
    """Persist effective selectors, respecting existing CLI precedence."""
    if kind == "verification":
        domains = []
        if all_entries:
            ids, kinds = [], []
        elif ids:
            kinds = []
    # Invariant filters intersect; inactive or filtered-out IDs have no effect.
    selected_ids = {row["id"] for row in selected}
    kinds = set(kinds) & {row["kind"] for row in selected}
    domains = set(domains) & {row.get("domain", "") for row in selected}
    return {"ids": sorted(set(ids) & selected_ids), "kinds": sorted(set(kinds)),
            "domains": sorted(set(domains)), "all": all_entries}


def selection_summary(sel):
    """Fresh allowlisted containers, called only after schema validation."""
    return {"requested": {**{k: list(sel["requested"][k]) for k in ("ids", "kinds", "domains")},
                          "all": sel["requested"]["all"]},
            **{k: list(sel[k]) for k in ("selected", "required", "executed", "omitted")},
            **{k: sel[k] for k in ("selectedCount", "requiredCount", "executedCount", "completeRequired")}}


def selection(plan, selected, outcomes, requested):
    required = [p["id"] for p in plan if p["required"]]
    executed = [p["id"] for p in outcomes]
    omitted = [p for p in required if p not in executed]
    return {"requested": requested, "selected": selected, "required": required,
            "executed": executed, "omitted": omitted, "selectedCount": len(selected),
            "requiredCount": len(required), "executedCount": len(executed),
            "completeRequired": bool(required) and not omitted and set(selected) <= set(executed)}


def stable(receipt):
    for section in ("inputs", "runner", "executionContext"):
        pair = receipt.get(section, {})
        before, after = pair.get("before", {}), pair.get("after", {})
        if not before.get("complete") or not after.get("complete") or before != after:
            return False
    return receipt.get("contracts") == receipt.get("contractsAfter")


def outcome(receipt):
    rows = receipt[TYPES[receipt["receiptType"]][1]]
    if receipt.get("errors") or len(rows) != len(receipt["selection"]["selected"]) or any(r["status"] == "incomplete" for r in rows):
        return "incomplete"
    if not stable(receipt):
        return "incomplete"
    if any(r["status"] == "failed" for r in rows):
        return "failed"
    return "passed" if receipt["selection"]["completeRequired"] else "passed_subset"


def selected_entries(kind, entries, requested):
    """Derive ordered intent from normalized contract entries, never outcomes."""
    if kind == "verification":
        if requested["all"]:
            return list(entries)
        if requested["ids"]:
            return [e for e in entries if e["id"] in requested["ids"]]
        if requested["kinds"]:
            return [e for e in entries if e["kind"] in requested["kinds"]]
        return [e for e in entries if e["required"]]
    return [e for e in entries
            if e["command"] and (requested["all"] or e["status"] == "active")
            and (not requested["ids"] or e["id"] in requested["ids"])
            and (not requested["kinds"] or e["kind"] in requested["kinds"])
            and (not requested["domains"] or e["domain"] in requested["domains"])]


def validate_receipt(value, expected_type, *, entries=None, contracts=None):
    """Check structure; check selector semantics only with matching contract context.

    Entries must be normalized from the supplied contract descriptors. Without
    that context, success is structural validation, not current certification.
    """
    if not isinstance(value, dict):
        return ["receipt_not_object"]
    if "schemaVersion" not in value or value["schemaVersion"] == 1:
        return ["legacy_receipt"]
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != SCHEMA_VERSION:
        return ["unsupported_schema"]
    try:
        if value["receiptType"] != expected_type or not ID.fullmatch(value["id"]) or not value["id"].startswith(TYPES[expected_type][0] + "_"):
            raise ValueError
        for key in ("createdAt", "finishedAt"):
            if key == "finishedAt" and value["executionState"] == "running":
                continue
            datetime.datetime.strptime(value[key], "%Y-%m-%dT%H:%M:%S.%fZ")
        if value["executionState"] not in {"running", "completed", "interrupted", "error"} or value["status"] not in STATES:
            raise ValueError
        if value["project"] != "." or value["tempDir"] != ".controlcoding/" + TYPES[expected_type][2] + "_tmp/" + value["id"]:
            raise ValueError
        plan = value["plan"]
        if not isinstance(plan, list) or not 0 < len(plan) <= 1000 or not _ids([r["id"] for r in plan]):
            raise ValueError
        for row in plan:
            if type(row["required"]) is not bool or not HEX.fullmatch(row["commandTemplateDigest"]):
                raise ValueError
        sel = value["selection"]
        if not isinstance(sel, dict) or set(sel) != {"requested", "selected", "required", "executed", "omitted", "selectedCount", "requiredCount", "executedCount", "completeRequired"}:
            raise ValueError
        for key in ("selected", "required", "executed", "omitted"):
            if not _ids(sel[key]):
                raise ValueError
        for key in ("selectedCount", "requiredCount", "executedCount"):
            if type(sel[key]) is not int or not 0 <= sel[key] <= 1000:
                raise ValueError
        if type(sel["completeRequired"]) is not bool:
            raise ValueError
        requested = sel["requested"]
        if not isinstance(requested, dict) or set(requested) != {"ids", "kinds", "domains", "all"}:
            raise ValueError
        if type(requested["all"]) is not bool or any(not _ids(requested[k]) for k in ("ids", "kinds", "domains")):
            raise ValueError
        if not set(requested["ids"]) <= {p["id"] for p in plan} or not set(requested["kinds"]) <= SELECTOR_KINDS[expected_type]:
            raise ValueError
        if requested["ids"] and set(requested["ids"]) != set(sel["selected"]):
            raise ValueError
        if entries is not None and contracts is not None and value["contracts"] == contracts:
            # Domain vocabulary belongs to the contract, including custom ones.
            # The compact plan omits kind/domain/status: contract identity must
            # match too. Changed contracts remain separately assessed as stale.
            required = {e["id"] for e in entries if (e["required"] if expected_type == "verification"
                        else e["status"] == "active" and e["command"])}
            current_plan = make_plan(entries, required)
            if current_plan == plan:
                if requested["domains"] and (expected_type != "invariants" or
                        not set(requested["domains"]) <= {e.get("domain", "") for e in entries}):
                    raise ValueError
                if sel["selected"] != [e["id"] for e in selected_entries(expected_type, entries, requested)]:
                    raise ValueError
        # Domains are contract-defined strings, not a fixed enum.
        if expected_type == "verification" and (requested["domains"] or
                (requested["all"] and (requested["ids"] or requested["kinds"])) or
                (requested["ids"] and requested["kinds"])):
            raise ValueError
        rows = value[TYPES[expected_type][1]]
        if not isinstance(rows, list) or not _ids([r["id"] for r in rows]) or not _ids(sel["selected"]) or not sel["selected"]:
            raise ValueError
        if not set(sel["selected"]) <= {p["id"] for p in plan} or not {r["id"] for r in rows} <= set(sel["selected"]):
            raise ValueError
        expected = selection(plan, sel["selected"], rows, sel["requested"])
        if expected != sel:
            raise ValueError
        if [r["id"] for r in rows] != sel["selected"][:len(rows)]:
            raise ValueError
        indexed = {p["id"]: p for p in plan}
        for row in rows:
            if row["status"] not in {"passed", "failed", "incomplete"} or row["stdoutTail"] != "" or row["stderrTail"] != "":
                raise ValueError
            if row["commandTemplateDigest"] != indexed[row["id"]]["commandTemplateDigest"] or not HEX.fullmatch(row["argvDigest"]):
                raise ValueError
            if type(row["durationMs"]) is not int or row["durationMs"] < 0:
                raise ValueError
            if row["status"] == "passed" and (type(row["returnCode"]) is not int or row["returnCode"] != 0 or row["error"] is not None):
                raise ValueError
            if row["status"] == "failed" and (type(row["returnCode"]) is not int or row["returnCode"] == 0 or row["error"] is not None):
                raise ValueError
            if row["status"] == "incomplete" and not row["error"]:
                raise ValueError
            output = row["output"]
            if output["policy"] != "metadata-only-v1" or output["textOmitted"] is not True:
                raise ValueError
            for count in ("stdoutBytes", "stderrBytes", "discardedBytes", "limitBytes"):
                if type(output[count]) is not int or output[count] < 0:
                    raise ValueError
            if output["discardedBytes"] != output["stdoutBytes"] + output["stderrBytes"]:
                raise ValueError
        if not isinstance(value["errors"], list):
            raise ValueError
        for section in ("inputs", "runner", "executionContext"):
            pair = value[section]
            sides = ("before",) if value["executionState"] == "running" else ("before", "after")
            for side in sides:
                measurement = pair[side]
                if type(measurement["complete"]) is not bool or not isinstance(measurement["reasons"], list):
                    raise ValueError
                if measurement["complete"]:
                    key = "contentDigest" if section == "inputs" else "digest"
                    if not HEX.fullmatch(measurement[key]) or measurement["reasons"]:
                        raise ValueError
        if not isinstance(value["contracts"], list) or not value["contracts"]:
            raise ValueError
        for descriptor in value["contracts"]:
            allowed = set(inputs.MANDATORY[:2]) | {".controlcoding/verification.json", ".claude/verification.json"}
            if descriptor["path"] not in allowed or (descriptor["sha256"] is not None and not HEX.fullmatch(descriptor["sha256"])):
                raise ValueError
        if value["executionState"] == "running":
            if value["status"] != "running":
                raise ValueError
        elif value["status"] != outcome(value):
            raise ValueError
        if value["status"] in ("passed", "passed_subset", "failed") and value["executionState"] != "completed":
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return ["receipt_structure_invalid"]
    return []


def assess_receipt(receipt, current_inputs, current_contracts, current_context, current_plan, *, entries=None):
    reasons = validate_receipt(receipt, receipt.get("receiptType", ""), entries=entries, contracts=current_contracts)
    if reasons:
        return _assessment("legacy" if reasons == ["legacy_receipt"] else "invalid", *reasons)
    if receipt["executionState"] != "completed" or receipt["status"] == "incomplete":
        return _assessment("incomplete", "attempt_incomplete")
    if not current_inputs["complete"] or any(not m["complete"] for m in current_context.values()):
        return _assessment("unknown", "current_identity_unknown")
    if receipt["inputs"]["after"] != current_inputs or receipt["contracts"] != current_contracts or receipt["plan"] != current_plan:
        return _assessment("stale", "inputs_or_contract_changed")
    if entries is None:
        return _assessment("unknown", "selector_context_unavailable")
    if any(receipt[name]["after"] != value for name, value in current_context.items()):
        return _assessment("context_changed", "runner_or_execution_context_changed")
    if receipt["status"] != "passed":
        return _assessment("current", "required_run_not_passed")
    return _assessment("current", current=True)


def make_plan(entries, required):
    return [{"id": e["id"], "required": e["id"] in required,
             "commandTemplateDigest": inputs.digest(e["command"])} for e in entries]


def contracts_now(project, kind):
    _, descriptor = inputs.read_contract(project, kind)
    descriptors = [descriptor]
    if kind == "verification":
        _, invariant = inputs.read_contract(project, "invariants")
        descriptors.append(invariant)
    return descriptors


def execute(project, kind, entries, selected, required, requested, policy, contracts,
            *, engine_dir, execute_command=None):
    execute_command = execute_command or run_command
    key = TYPES[kind][1]
    created = utc_now()
    receipt_id = TYPES[kind][0] + "_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex
    temp = ".controlcoding/" + TYPES[kind][2] + "_tmp/" + receipt_id
    relative = _folder(kind) + "/" + receipt_id + ".json"
    plan = make_plan(entries, required)
    receipt = {"schemaVersion": 2, "receiptType": kind, "id": receipt_id,
               "createdAt": created, "finishedAt": None, "executionState": "running", "status": "running",
               "project": ".", "tempDir": temp, "plan": plan, "contracts": contracts,
               "selection": selection(plan, [e["id"] for e in selected], [], requested),
               "inputs": {}, "runner": {}, "executionContext": {}, "errors": [], key: [],
               "outputPolicy": {"mode": "metadata-only-v1", "rawTextPersisted": False},
               "ci": {"verified": False, "present": os.environ.get("CI", "").lower() == "true"}}
    initialized = False
    try:
        with inputs.SafeRoot(project) as root:
            # Check the output parent before any child can emit E3 JUnit reports.
            with root.directory(_folder(kind), create=True):
                pass
            with root.directory(temp, create=True):
                initialized = True
            receipt["inputs"]["before"] = inputs.capture_inputs(project, policy)
            receipt["runner"]["before"] = inputs.runner_context(engine_dir)
            receipt["executionContext"]["before"] = inputs.execution_context(project, policy, entries)
            if contracts_now(project, kind) != contracts:
                receipt["errors"].append("contract_changed_before_execution")
            if not all(receipt[s]["before"]["complete"] for s in ("inputs", "runner", "executionContext")):
                receipt["errors"].append("initial_identity_unknown")
            root.write_atomic(relative, receipt, replace_existing=False)  # Never overwrite a colliding attempt.
            try:
                if not receipt["errors"]:
                    for entry in selected:
                        root.validate()
                        argv = inputs.executable_argv(inputs.command_args(entry["command"], project, root.path / temp), project)
                        result = execute_command(argv, cwd=str(root.path), timeout=entry.get("timeoutSeconds", 300))
                        row = {"id": entry["id"], "kind": entry["kind"],
                               "commandTemplateDigest": inputs.digest(entry["command"]),
                               "argvDigest": inputs.digest(argv), **result}
                        receipt[key].append(row)
                        if result["status"] == "incomplete":
                            break
            except KeyboardInterrupt:
                receipt["errors"].append("interrupted")
            except inputs.EvidenceError as exc:
                receipt["errors"].append(exc.code)
            except Exception:
                receipt["errors"].append("execution_error")
            finally:
                try:
                    root.remove_tree(temp)
                    initialized = False
                except (OSError, inputs.EvidenceError):
                    receipt["errors"].append("temp_cleanup_failed")
                receipt["inputs"]["after"] = inputs.capture_inputs(project, policy)
                receipt["runner"]["after"] = inputs.runner_context(engine_dir)
                receipt["executionContext"]["after"] = inputs.execution_context(project, policy, entries)
                try:
                    receipt["contractsAfter"] = contracts_now(project, kind)
                    root.validate()
                except (OSError, ValueError, inputs.EvidenceError):
                    receipt["contractsAfter"] = []
                    receipt["errors"].append("final_identity_unknown")
                receipt["selection"] = selection(plan, [e["id"] for e in selected], receipt[key], requested)
                receipt["status"] = outcome(receipt)
                interrupted = "interrupted" in receipt["errors"] or any(r.get("error") == "interrupted" for r in receipt[key])
                receipt["executionState"] = "interrupted" if interrupted else ("error" if receipt["status"] == "incomplete" else "completed")
                receipt["finishedAt"] = utc_now()
                root.write_atomic(relative, receipt)
    except Exception:
        # If finalization fails, keep the running marker. Never print a pass whose
        # durable receipt could not be written. Do not expose filesystem errors.
        return {"ok": False, "status": "incomplete", "issues": ["receipt_lifecycle_failed"],
                "receiptPath": relative, "temporaryCleanupPending": initialized}, 1
    code = 130 if receipt["executionState"] == "interrupted" else (0 if receipt["status"] in ("passed", "passed_subset") else 1)
    return {"ok": code == 0, "status": receipt["status"], "receiptPath": relative, "receipt": receipt}, code


def summarize_attempts(project, kind, *, policy, contracts, entries, required, engine_dir):
    """Bounded, read-only history. A bad/new partial attempt never exposes old green."""
    history = []
    unknown = _assessment("unknown", "no_receipt")
    try:
        with inputs.SafeRoot(project) as root:
            budget = inputs.Budget(total=32 * 1024 * 1024, per_file=2 * 1024 * 1024, entries=128)
            try:
                listing = root.listing(_folder(kind), budget)
            except FileNotFoundError:
                return {"assessment": unknown, "latestReceipts": [], "latest": None}
            candidates = [(name, info) for name, info in listing if name.endswith(".json")]
            candidates.sort(key=lambda pair: (pair[1].st_mtime_ns, pair[0]), reverse=True)
            # Inspect the entire bounded set before asserting a latest identity.
            for name, info in candidates:
                if not stat.S_ISREG(info.st_mode):
                    raise inputs.EvidenceError("unsafe_receipt")
                # Read failures invalidate the WHOLE history. Only fully read
                # bytes may be classified/ordered as malformed or legacy JSON.
                raw = root.read(_folder(kind) + "/" + name, budget)[0]
                try:
                    value = json.loads(raw, object_pairs_hook=inputs._unique_object)
                    reasons = validate_receipt(value, kind, entries=entries, contracts=contracts)
                except (ValueError, RecursionError, inputs.EvidenceError):
                    value, reasons = {}, ["receipt_invalid"]
                summary = {"path": _folder(kind) + "/" + name,
                           "status": "unknown", "id": "", "createdAt": "", TYPES[kind][1]: [],
                           "_observedAt": info.st_mtime_ns}
                state = "legacy" if reasons == ["legacy_receipt"] else "invalid"
                summary["assessment"] = _assessment(state, *reasons)
                if not reasons:
                    if name != value["id"] + ".json":
                        summary["assessment"] = _assessment("invalid", "receipt_name_mismatch")
                    else:
                        summary.update(id=value["id"], status=value["status"], createdAt=value["createdAt"],
                                       selection=selection_summary(value["selection"]), _receipt=value)
                        created = datetime.datetime.strptime(value["createdAt"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)
                        summary["_observedAt"] = int(created.timestamp() * 1_000_000) * 1000
                        summary[TYPES[kind][1]] = [{k: row[k] for k in ("id", "status", "returnCode", "durationMs")} for row in value[TYPES[kind][1]]]
                history.append(summary)
            root.validate()
        if not history:
            return {"assessment": unknown, "latestReceipts": [], "latest": None}
        # Use creation for EVERY valid attempt, even in a mixed history. An old
        # full run finishing after a newer subset must not become latest merely
        # because some unrelated legacy file also exists in the directory.
        history.sort(key=lambda h: (h["_observedAt"], h["id"]), reverse=True)
        latest = history[0]
        if "_receipt" in latest:
            current = inputs.capture_inputs(project, policy)
            context = {"runner": inputs.runner_context(engine_dir),
                       "executionContext": inputs.execution_context(project, policy, entries)}
            latest["assessment"] = assess_receipt(latest["_receipt"], current, contracts, context, make_plan(entries, required), entries=entries)
        for row in history:
            row.pop("_receipt", None)
            row.pop("_observedAt", None)
            if row is not latest and row["assessment"]["reasons"] == []:
                row["assessment"] = _assessment("unknown", "historical_attempt_not_reassessed")
        return {"assessment": latest["assessment"], "latestReceipts": history[:5], "latest": latest}
    except (OSError, ValueError, TypeError, inputs.EvidenceError):
        return {"assessment": _assessment("unknown", "history_inspection_incomplete"), "latestReceipts": [], "latest": None}
