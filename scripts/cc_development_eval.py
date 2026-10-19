"""Pinned representative tasks graded by the installed owner, never by workers.

Case directories separate inputs; they are not a filesystem or process sandbox.
Usage is adapter reported and budgets are checked after each invocation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

import cc_evidence_inputs as inputs
from cc_evidence_process import run_command
from cc_development_adapters import read_bytes, read_json, require, HEX, IDENTIFIER
from cc_layout import managed_relative


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _text(value, limit=8192):
    return type(value) is str and 0 < len(value) <= limit and "\x00" not in value


def _path(value):
    require(type(value) is str, "eval_path_invalid")
    inputs.parts(value)
    require(value.casefold() != "request.json" and not value.casefold().startswith(".controlcoding/"), "eval_path_reserved")


def _number(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def prepare(root, spec, change):
    config = spec["configuration"]
    require(set(config) in ({"evaluationVersion", "implementationActor", "graderActor", "suitePath", "configurationPath"},
                           {"evaluationVersion", "implementationActor", "graderActor", "suitePath", "configurationPath", "attemptToken"})
            and type(config["evaluationVersion"]) is int and config["evaluationVersion"] == 2,
            "eval_configuration_invalid")
    require(_text(config["implementationActor"], 200) and _text(config["graderActor"], 200)
            and config["implementationActor"] != config["graderActor"], "eval_grader_not_independent")
    require(config["implementationActor"] == change.get("candidate", {}).get("actor"), "eval_implementation_actor_changed")
    require(sum(a.count("{evalRequest}") for a in spec["argv"]) == 1, "eval_request_argument_missing")
    pins = spec["files"]
    indexed = {p["path"]: p["sha256"] for p in pins}
    for name in ("suitePath", "configurationPath"):
        require(config[name] in indexed, "eval_definition_unpinned")
    suite, task_digest = read_json(root, config["suitePath"])
    configuration, config_digest = read_json(root, config["configurationPath"])
    require(task_digest == indexed[config["suitePath"]] and config_digest == indexed[config["configurationPath"]], "eval_definition_changed")
    require(set(configuration) == {"schemaVersion", "model", "inputs", "budgets"}
            and type(configuration["schemaVersion"]) is int and configuration["schemaVersion"] == 1, "eval_configuration_invalid")
    model = configuration["model"]
    require(type(model) is dict and set(model) == {"provider", "id", "revision"}
            and all(_text(x, 200) for x in model.values()), "eval_model_invalid")
    require(all(x.strip().casefold() not in {"unknown", "unidentified", "unspecified", "latest", "auto", "default"}
                for x in model.values()), "eval_model_unpinned")
    budgets = configuration["budgets"]
    require(type(budgets) is dict and set(budgets) == {"totalTimeoutSeconds", "caseTimeoutSeconds", "maxCostMicros", "maxInputTokens", "maxOutputTokens"}
            and all(_number(x, 10**12) for x in budgets.values())
            and 1 <= budgets["caseTimeoutSeconds"] <= budgets["totalTimeoutSeconds"] <= 300, "eval_budget_invalid")
    require(type(configuration["inputs"]) is list and len(configuration["inputs"]) <= 64, "eval_inputs_invalid")
    seen = set()
    for pin in configuration["inputs"]:
        require(type(pin) is dict and set(pin) == {"kind", "path", "sha256"}
                and pin["kind"] in ("prompt", "skill", "hook", "model", "adapter")
                and pin["path"] not in seen and pin["path"] in indexed
                and HEX.fullmatch(str(pin["sha256"])) and pin["sha256"] == indexed[pin["path"]], "eval_input_unpinned")
        seen.add(pin["path"])
    require(type(suite) is dict and set(suite) == {"schemaVersion", "id", "version", "cases"}
            and type(suite["schemaVersion"]) is int and suite["schemaVersion"] in (1, 2)
            and _text(suite["id"], 200) and _text(suite["version"], 100)
            and type(suite["cases"]) is list and 1 <= len(suite["cases"]) <= 32, "eval_suite_invalid")
    ids = set()
    for case in suite["cases"]:
        case_fields = {"id", "prompt", "files", "checks"}
        if suite['schemaVersion'] == 2:
            case_fields |= {'planningDetail', 'planningBinding', 'detail_behavior'}
        require(type(case) is dict and set(case) == case_fields
                and type(case["id"]) is str and IDENTIFIER.fullmatch(case["id"]) and case["id"] not in ids
                and _text(case["prompt"]) and type(case["files"]) is list and len(case["files"]) <= 64
                and type(case["checks"]) is list and 1 <= len(case["checks"]) <= 64, "eval_case_invalid")
        ids.add(case["id"])
        if suite['schemaVersion'] == 2:
            detail, binding, behavior = case['planningDetail'], case['planningBinding'], case['detail_behavior']
            require(type(detail) is dict and detail.get('contract') == 'detailed_contract_v1'
                    and len(json.dumps(detail, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')) <= 128 * 1024
                    and type(binding) is dict and set(binding) == {'contract', 'profile_revision', 'selection_revision',
                        'artifact_revision', 'detail_revision', 'upstream_detail_revision', 'source_manifest_digest'}
                    and binding['contract'] == 'detailed_contract_v1', 'eval_planning_detail_invalid')
            require(all(HEX.fullmatch(str(binding[k])) for k in binding if k not in ('contract', 'upstream_detail_revision'))
                    and (binding['upstream_detail_revision'] == 'absent' or HEX.fullmatch(str(binding['upstream_detail_revision']))),
                    'eval_planning_binding_invalid')
            require(type(behavior) is dict and set(behavior) == {'task_id', 'field', 'path'}
                    and behavior['field'] in ('approach', 'deliverable'), 'eval_detail_behavior_invalid')
            tasks = [t for t in detail.get('task_details', []) if type(t) is dict and t.get('task_id') == behavior['task_id']]
            require(len(tasks) == 1 and _text(tasks[0].get(behavior['field']), 65536), 'eval_detail_behavior_invalid')
            _path(behavior['path'])
            # Independent expected bytes come from the richer request, never
            # from worker-reported success or an old base-plan-only case.
            case['checks'].append({'kind': 'file_sha256', 'path': behavior['path'],
                                   'sha256': digest(tasks[0][behavior['field']].encode('utf-8'))})
        paths = set()
        for item in case["files"]:
            require(type(item) is dict and set(item) == {"path", "content"}
                    and type(item["content"]) is str and len(item["content"].encode()) <= 65536, "eval_file_invalid")
            _path(item["path"])
            require(item["path"].casefold() not in paths, "eval_file_duplicate")
            paths.add(item["path"].casefold())
        for check in case["checks"]:
            require(type(check) is dict, "eval_check_invalid")
            kind = check.get("kind")
            fields = {"file_absent": {"kind", "path"}, "file_contains": {"kind", "path", "text"}, "file_sha256": {"kind", "path", "sha256"}}
            require(kind in fields and set(check) == fields[kind], "eval_check_invalid")
            _path(check["path"])
            if kind == "file_contains":
                require(_text(check["text"], 65536), "eval_check_invalid")
            if kind == "file_sha256":
                require(HEX.fullmatch(str(check["sha256"])), "eval_check_invalid")
    for pin in pins:
        require(digest(read_bytes(root, pin["path"])) == pin["sha256"], "eval_input_changed")
    return {"evaluationVersion": 2, "implementationActor": config["implementationActor"], "graderActor": config["graderActor"],
            "configDigest": config_digest, "taskDigest": task_digest, "graderDigest": digest(read_bytes(Path(__file__).parent, Path(__file__).name)),
            "requiredCases": [c["id"] for c in suite["cases"]], "pins": pins,
            "suite": suite, "configuration": configuration}


def _current(root, plan, executable_pins):
    require(digest(read_bytes(Path(__file__).parent, Path(__file__).name)) == plan["graderDigest"], "eval_grader_changed")
    for pin in plan["pins"]:
        require(digest(read_bytes(root, pin["path"])) == pin["sha256"], "eval_input_changed")
    for pin in executable_pins or []:
        path = Path(pin["path"])
        if not path.is_absolute():
            path = Path(root).absolute() / path
        from cc_development_jobs import _executable
        require(_executable(str(path), root)["sha256"] == pin["sha256"], "eval_executable_changed")


def _write_text(reader, relative, content):
    parts = inputs.parts(relative)
    with reader.directory("/".join(parts[:-1]), create=True) as parent:
        options = {} if os.name == "nt" else {"dir_fd": parent}
        target = parent / parts[-1] if os.name == "nt" else parts[-1]
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600, **options)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode())
            stream.flush()
            os.fsync(stream.fileno())
    reader.validate()


def execute(root, plan, argv, executable_pins=None, workspace_relative=None, receipt_id=None, output_limit=65536):
    """Run each required case once; all failures and unrun cases remain in result."""
    require(type(argv) is list and sum(a.count("{evalRequest}") for a in argv) == 1, "eval_request_argument_missing")
    _current(root, plan, executable_pins)
    workspace = workspace_relative or managed_relative(root, ".controlcoding/development_eval_runs/" + str(receipt_id))
    inputs.parts(workspace)
    require(type(receipt_id) is str and IDENTIFIER.fullmatch(receipt_id), "eval_receipt_id_invalid")
    allowed_workspaces = {
        managed_relative(root, ".controlcoding/development_eval_runs") + "/" + receipt_id,
        managed_relative(root, ".controlcoding/development_job_receipts") + "/" + receipt_id + "_cases",
    }
    require(workspace in allowed_workspaces, "eval_workspace_invalid")
    with inputs.SafeRoot(root) as reader:
        with reader.directory(workspace, create=True, exclusive=True):
            pass
    started = time.monotonic()
    budget = plan["configuration"]["budgets"]
    usage = {"costMicros": 0, "inputTokens": 0, "outputTokens": 0}
    rows, reasons = [], []
    for case in plan["suite"]["cases"]:
        row = {"id": case["id"], "status": "not_run", "reasons": [], "checks": []}
        rows.append(row)
        if reasons:
            row["reasons"] = ["evaluation_stopped"]
            continue
        remaining = budget["totalTimeoutSeconds"] - (time.monotonic() - started)
        if remaining <= 0:
            reasons.append("eval_total_timeout")
            row["reasons"] = reasons.copy()
            continue
        relative = workspace + "/" + case["id"]
        try:
            _current(root, plan, executable_pins)
            with inputs.SafeRoot(root) as reader:
                with reader.directory(relative, create=True, exclusive=True):
                    pass
                for item in case["files"]:
                    _write_text(reader, relative + "/" + item["path"], item["content"])
                request_payload = {"schemaVersion": plan['suite']['schemaVersion'], "caseId": case["id"],
                    "prompt": case["prompt"], "model": plan["configuration"]["model"], "files": [x["path"] for x in case["files"]]}
                if plan['suite']['schemaVersion'] == 2:
                    request_payload.update(planningDetail=case['planningDetail'], planningBinding=case['planningBinding'])
                reader.write_atomic(relative + "/request.json", request_payload, replace_existing=False)
            request = str(Path(root).absolute() / relative / "request.json")
            pinned_paths = {p["path"] for p in plan["pins"]}
            command = [str(Path(root).absolute() / a) if a in pinned_paths else a.replace("{evalRequest}", request) for a in argv]
            result = run_command(command, cwd=Path(root).absolute() / relative,
                                 timeout=min(remaining, budget["caseTimeoutSeconds"]),
                                 output_limit=output_limit, capture_limit=output_limit,
                                 env=__import__("cc_development_jobs")._environment())
            row["process"] = {k: v for k, v in result.items() if not k.startswith("_")}
            require(result["status"] == "passed" and result.get("_captureComplete"), "eval_worker_" + (result["error"] or "failed"))
            worker = json.loads(result["_captured"]["stdout"], object_pairs_hook=inputs._unique_object,
                                parse_constant=lambda _: require(False, "eval_worker_invalid"))
            require(type(worker) is dict and set(worker) == {"model", "usage"}
                    and worker["model"] == plan["configuration"]["model"], "eval_worker_model_invalid")
            reported = worker["usage"]
            require(type(reported) is dict and set(reported) == set(usage)
                    and all(_number(x, 10**12) for x in reported.values()), "eval_worker_usage_invalid")
            row["usage"] = reported
            row["model"] = worker["model"]
            for key in usage:
                usage[key] += reported[key]
            require(usage["costMicros"] <= budget["maxCostMicros"] and usage["inputTokens"] <= budget["maxInputTokens"]
                    and usage["outputTokens"] <= budget["maxOutputTokens"], "eval_usage_budget_exceeded")
            _current(root, plan, executable_pins)
            require(time.monotonic() - started <= budget["totalTimeoutSeconds"], "eval_total_timeout")
            with inputs.SafeRoot(Path(root).absolute() / relative) as reader:
                for check in case["checks"]:
                    found = reader.read(check["path"], inputs.Budget(total=2*1024*1024, per_file=2*1024*1024), missing=True)
                    passed = found is None if check["kind"] == "file_absent" else found is not None and (
                        digest(found[0]) == check["sha256"] if check["kind"] == "file_sha256" else check["text"].encode() in found[0])
                    row["checks"].append({"kind": check["kind"], "path": check["path"], "passed": bool(passed), "observedDigest": digest(found[0]) if found else None})
                reader.validate()
            row["status"] = "passed" if all(c["passed"] for c in row["checks"]) else "failed"
            if row["status"] == "failed":
                row["reasons"].append("eval_independent_check_failed")
        except (inputs.EvidenceError, OSError, ValueError, TypeError) as error:
            row["status"] = "failed"
            row["reasons"] = [str(error) if isinstance(error, inputs.EvidenceError) else "eval_worker_invalid"]
            reasons.extend(row["reasons"])
    if any(r["status"] != "passed" for r in rows):
        reasons.append("eval_required_cases_failed")
    return {"evaluationVersion": 2, "model": plan["configuration"]["model"], "usage": usage,
            "usageSource": "adapter-reported", "cases": rows, "status": "passed" if not reasons else "failed", "reasons": reasons,
            "configDigest": plan["configDigest"], "taskDigest": plan["taskDigest"], "graderDigest": plan["graderDigest"],
            "requiredCases": plan["requiredCases"], "workspace": workspace,
            "durationMs": int((time.monotonic() - started) * 1000)}


def assess(plan, observation, *, root=None):
    """Assess retained owner results, optionally re-reading current output bytes."""
    try:
        require(type(observation) is dict and observation.get("evaluationVersion") == 2,
                "eval_observation_invalid")
        for key in ("configDigest", "taskDigest", "graderDigest", "requiredCases"):
            require(observation.get(key) == plan[key], "eval_identity_changed")
        require(observation.get("model") == plan["configuration"]["model"], "eval_worker_model_invalid")
        rows = observation.get("cases")
        require(type(rows) is list and [r.get("id") for r in rows] == plan["requiredCases"], "eval_required_cases_missing")
        usage = observation.get("usage")
        budgets = plan["configuration"]["budgets"]
        require(_number(observation.get("durationMs"), budgets["totalTimeoutSeconds"] * 1000), "eval_total_timeout")
        require(type(usage) is dict and set(usage) == {"costMicros", "inputTokens", "outputTokens"}
                and all(_number(x, 10**12) for x in usage.values()), "eval_worker_usage_invalid")
        for key, limit in (("costMicros", "maxCostMicros"), ("inputTokens", "maxInputTokens"), ("outputTokens", "maxOutputTokens")):
            require(usage[key] <= budgets[limit], "eval_usage_budget_exceeded")
        if root is not None:
            _current(root, plan, None)
        accumulated = {"costMicros": 0, "inputTokens": 0, "outputTokens": 0}
        for case, row in zip(plan["suite"]["cases"], rows):
            require(row.get("status") == "passed" and not row.get("reasons")
                    and row.get("process", {}).get("status") == "passed"
                    and row.get("process", {}).get("returnCode") == 0
                    and row.get("process", {}).get("error") is None, "eval_required_cases_failed")
            require(_number(row["process"].get("durationMs"), budgets["caseTimeoutSeconds"] * 1000), "eval_case_timeout")
            require(row.get("model") == plan["configuration"]["model"] and type(row.get("usage")) is dict
                    and set(row["usage"]) == set(accumulated) and all(_number(x, 10**12) for x in row["usage"].values()), "eval_worker_usage_invalid")
            for key in accumulated:
                accumulated[key] += row["usage"][key]
            checks = row.get("checks")
            require(type(checks) is list and len(checks) == len(case["checks"]), "eval_checks_missing")
            for expected, actual in zip(case["checks"], checks):
                require(actual.get("kind") == expected["kind"] and actual.get("path") == expected["path"]
                        and actual.get("passed") is True, "eval_independent_check_failed")
                if expected["kind"] == "file_sha256":
                    require(actual.get("observedDigest") == expected["sha256"], "eval_independent_check_failed")
                if expected["kind"] == "file_absent":
                    require(actual.get("observedDigest") is None, "eval_independent_check_failed")
                if root is not None:
                    relative = observation["workspace"] + "/" + case["id"] + "/" + expected["path"]
                    with inputs.SafeRoot(root) as reader:
                        found = reader.read(relative, inputs.Budget(total=2*1024*1024, per_file=2*1024*1024), missing=True)
                        reader.validate()
                    require((digest(found[0]) if found else None) == actual.get("observedDigest"), "eval_output_changed")
                    if expected["kind"] == "file_contains":
                        require(found is not None and expected["text"].encode() in found[0], "eval_independent_check_failed")
        require(accumulated == usage, "eval_worker_usage_invalid")
        require(observation.get("status") == "passed" and not observation.get("reasons"), "eval_required_cases_failed")
        return {"state": "allowed", "reasons": []}
    except (inputs.EvidenceError, OSError, ValueError, TypeError, KeyError) as error:
        return {"state": "blocked", "reasons": [str(error) if isinstance(error, inputs.EvidenceError) else "eval_observation_invalid"]}
