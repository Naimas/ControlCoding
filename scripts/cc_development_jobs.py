"""Explicit preview/execute of project-manifest development adapter commands.

The approved command runs with user privileges, not in a sandbox. Fixed argv,
bounded output and time limit constrain invocation, not program behavior.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import cc_evidence_inputs as inputs
from cc_evidence_process import run_command
from cc_layout import managed_relative
from cc_development_adapters import (HEX, IDENTIFIER, candidate_current, read_bytes,
                                    read_json, require)

KINDS = ("agent_eval", "pull_request", "release_status", "deploy", "rollback", "monitor", "diagnosis")
MANIFEST = ".controlcoding/development-adapters.json"
RECEIPTS = ".controlcoding/development_job_receipts"
FORBIDDEN = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
             "sh", "bash", "zsh", "fish", "csh", "wscript", "cscript"}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _seal(receipt):
    receipt["integrity"] = inputs.digest({k: v for k, v in receipt.items() if k != "integrity"})
    return receipt


def _validate_receipt(receipt):
    require(type(receipt) is dict and receipt.get("integrity") == inputs.digest({k: v for k, v in receipt.items() if k != "integrity"}), "job_receipt_integrity_changed")
    require(type(receipt.get("createdAt")) is str and receipt.get("state") in {"running", "allowed", "blocked"}, "job_receipt_invalid")
    created = datetime.datetime.fromisoformat(receipt["createdAt"])
    require(created.tzinfo is not None, "job_receipt_timestamp_invalid")
    if receipt["state"] != "running":
        require(type(receipt.get("finishedAt")) is str, "job_receipt_timestamp_invalid")
        finished = datetime.datetime.fromisoformat(receipt["finishedAt"])
        require(finished.tzinfo is not None and finished >= created, "job_receipt_timestamp_invalid")
    require(receipt.get("observationDigest") == inputs.digest(receipt.get("observation")), "job_observation_changed")
    return receipt


def _project_identity(root):
    from cc_development_lib.storage import identity
    with inputs.SafeRoot(root) as reader:
        return identity(reader)


def _control_digests(root):
    result = {}
    with inputs.SafeRoot(root) as reader:
        budget = inputs.Budget(total=4 * 1024 * 1024, per_file=2 * 1024 * 1024)
        for logical in (".controlcoding/development.json", ".controlcoding/development-policy.json", ".controlcoding/development-release.json"):
            path = managed_relative(root, logical)
            item = reader.read(path, budget, missing=True)
            result[path] = _digest(item[0]) if item else None
        reader.validate()
    return result


def manifest(root):
    relative = managed_relative(root, MANIFEST)
    value, digest = read_json(root, relative)
    require(set(value) == {"schemaVersion", "adapters"} and type(value["schemaVersion"]) is int
            and value["schemaVersion"] == 1 and type(value["adapters"]) is list
            and len(value["adapters"]) <= 64, "adapter_manifest_invalid")
    seen = set()
    for item in value["adapters"]:
        require(type(item) is dict and set(item) == {"id", "kind", "argv", "timeoutSeconds", "outputLimitBytes", "files", "configuration"},
                "adapter_manifest_invalid")
        require(type(item["id"]) is str and IDENTIFIER.fullmatch(item["id"]) and item["id"] not in seen
                and item["kind"] in KINDS, "adapter_manifest_invalid")
        seen.add(item["id"])
        argv = item["argv"]
        require(type(argv) is list and 1 <= len(argv) <= 64 and all(type(a) is str and a
                and len(a) <= 4096 and not any(c in a for c in ("\x00", "\r", "\n")) for a in argv), "adapter_argv_invalid")
        require(Path(argv[0]).name.lower() not in FORBIDDEN, "adapter_shell_forbidden")
        require(not any(a.lower() in ("-c", "-command", "-encodedcommand", "/c", "/k", "--eval", "-e")
                        for a in argv[1:]), "adapter_inline_code_forbidden")
        require(type(item["timeoutSeconds"]) is int and 1 <= item["timeoutSeconds"] <= 300
                and type(item["outputLimitBytes"]) is int and 1024 <= item["outputLimitBytes"] <= 131072,
                "adapter_bounds_invalid")
        require(type(item["files"]) is list and 1 <= len(item["files"]) <= 64
                and type(item["configuration"]) is dict, "adapter_manifest_invalid")
        if "attemptToken" in item["configuration"]:
            token = item["configuration"]["attemptToken"]
            require(type(token) is str and IDENTIFIER.fullmatch(token), "adapter_attempt_token_invalid")
        paths = set()
        for pin in item["files"]:
            require(type(pin) is dict and set(pin) == {"path", "sha256"}
                    and HEX.fullmatch(str(pin["sha256"])), "adapter_pin_invalid")
            inputs.parts(pin["path"])
            require(pin["path"].casefold() not in paths, "adapter_pin_invalid")
            paths.add(pin["path"].casefold())
        for arg in argv[1:]:
            if arg.lower().endswith((".py", ".js", ".cjs", ".mjs", ".json")):
                inputs.parts(arg)
                require(arg.casefold() in paths, "adapter_argument_unpinned")
        require(len(json.dumps(item)) <= 48000, "adapter_manifest_invalid")
    return value, digest, relative


def _executable(argv0, root):
    if Path(argv0).is_absolute():
        executable = Path(argv0)
    elif "/" in argv0 or "\\" in argv0:
        inputs.parts(argv0)
        executable = Path(root) / argv0
    else:
        found = shutil.which(argv0)
        require(found is not None, "adapter_executable_unavailable")
        executable = Path(found)
    executable = executable.resolve(strict=True)
    require(executable.suffix.lower() not in (".bat", ".cmd", ".ps1")
            and executable.name.lower() not in FORBIDDEN, "adapter_shell_forbidden")
    if os.name == "nt" and inputs.progress_observation() is None:
        # Windows path stat marks .exe executable; CRT fstat does not. Use the
        # same confined no-reparse handles, comparing object identity/content
        # metadata rather than that suffix-derived permission distinction.
        import msvcrt
        with inputs.SafeRoot(executable.parent) as reader:
            with reader.directory() as parent:
                before = reader._stat(parent, executable.name)
                require(stat.S_ISREG(before.st_mode) and not getattr(before, "st_file_attributes", 0) & 0x400,
                        "adapter_executable_unsafe")
                handle = reader._win_open(parent / executable.name, directory=False)
                try:
                    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                except BaseException:
                    reader._close(handle)
                    raise
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, stat.S_IFMT(s.st_mode))
                    require(key(before) == key(opened), "adapter_executable_changed")
                    raw = stream.read(64 * 1024 * 1024 + 1)
                    require(len(raw) <= 64 * 1024 * 1024 and key(opened) == key(os.fstat(stream.fileno()))
                            and len(raw) == opened.st_size, "adapter_executable_changed")
            reader.validate()
    else:
        raw = read_bytes(executable.parent, executable.name, limit=64 * 1024 * 1024)
    return {"path": str(executable), "sha256": _digest(raw)}


def _plan_approved(change):
    from cc_development_lib.evaluator import accepted
    return accepted(change, "plan")


def _prepare_legacy_eval(config, change, pins):
    require(set(config) - {"attemptToken"} == {"implementationActor", "graderActor", "configDigest", "taskDigest", "graderDigest", "requiredCases"}, "eval_configuration_invalid")
    require(config["implementationActor"] == change["candidate"].get("actor")
            and type(config["graderActor"]) is str and config["graderActor"]
            and config["graderActor"] != config["implementationActor"], "eval_grader_not_independent")
    require(all(config[k] in {p["sha256"] for p in pins} for k in ("configDigest", "taskDigest", "graderDigest")), "eval_identity_unpinned")
    require(type(config["requiredCases"]) is list and config["requiredCases"]
            and len(set(config["requiredCases"])) == len(config["requiredCases"])
            and all(type(c) is str and IDENTIFIER.fullmatch(c) for c in config["requiredCases"]), "eval_cases_invalid")


def _canonical_change(root, change_id, *, locked=False, evaluate_gates=True):
    from cc_development_lib import storage
    from cc_development_lib.evaluator import evaluate
    with inputs.SafeRoot(root) as reader:
        state, _ = storage.load(reader, locked=locked)
        matches = [c for c in state["changes"] if c["id"] == change_id]
        require(len(matches) == 1, "job_canonical_change_missing")
        change = inputs.copy_json(matches[0]) if hasattr(inputs, "copy_json") else json.loads(json.dumps(matches[0]))
        if evaluate_gates:
            change["gates"] = evaluate(reader, change)
        reader.validate()
    return change


def _live(kind, configuration):
    return kind in ("deploy", "rollback") and not (kind == "rollback" and configuration.get("mode") == "rehearsal")


def _github_worker(root, spec):
    """Only an exact installed-byte copy receives native authentication context."""
    argv = spec["argv"]
    require(len(argv) >= 9 and Path(argv[0]).name.casefold().startswith("python")
            and argv[1:3] == ["-I", "-B"] and argv[4] == "{pullRequestRequest}", "pr_worker_argv_invalid")
    inputs.parts(argv[3])
    import cc_development_github
    worker_path = Path(cc_development_github.__file__)
    trusted = _digest(read_bytes(worker_path.parent, worker_path.name))
    pins = {p["path"]: p["sha256"] for p in spec["files"]}
    require(pins.get(argv[3]) == trusted and _digest(read_bytes(root, argv[3])) == trusted, "pr_worker_untrusted")
    flags = {}
    require(len(argv[5:]) % 2 == 0, "pr_worker_argv_invalid")
    for key, value in zip(argv[5::2], argv[6::2]):
        require(key in {"--gh", "--gh-sha256", "--timeoutSeconds", "--outputLimitBytes"}
                and key not in flags, "pr_worker_argv_invalid")
        flags[key] = value
    require("--gh" in flags and "--gh-sha256" in flags and Path(flags["--gh"]).is_absolute()
            and HEX.fullmatch(flags["--gh-sha256"]), "pr_worker_argv_invalid")
    binary = _executable(flags["--gh"], root)
    require(binary["path"] == str(Path(flags["--gh"]).resolve())
            and binary["sha256"] == flags["--gh-sha256"], "pr_native_binary_changed")
    for flag, low, high in (("--timeoutSeconds", 1, 300), ("--outputLimitBytes", 4096, 2 * 1024 * 1024)):
        if flag in flags:
            require(flags[flag].isdigit() and low <= int(flags[flag]) <= high, "pr_worker_bounds_invalid")
    require(set(spec["configuration"]) <= {"attemptToken"}, "pr_worker_configuration_invalid")
    return binary


def _attempts(root, kind, change, environment=None):
    """Inspect retained attempts, including failures and unfinished effects."""
    relative = managed_relative(root, RECEIPTS)
    result = []
    with inputs.SafeRoot(root) as reader:
        try:
            listing = reader.listing(relative, inputs.Budget(entries=4096))
        except FileNotFoundError:
            return []
        for name, metadata in listing:
            if not name.endswith(".json") or not stat.S_ISREG(metadata.st_mode):
                continue
            receipt, _ = read_json(root, relative + "/" + name)
            _validate_receipt(receipt)
            plan = receipt.get("plan", {})
            if receipt.get("kind") != kind or plan.get("changeId") != change["id"]:
                continue
            if environment is None and kind != "diagnosis" and plan.get("candidateDigest") != change["candidate"]["digest"]:
                continue
            if environment is not None and plan.get("configuration", {}).get("environment") != environment:
                continue
            require(receipt.get("id") == name[:-5] and receipt.get("state") in {"running", "allowed", "blocked"}
                    and type(receipt.get("createdAt")) is str, "job_attempt_invalid")
            stamp = datetime.datetime.fromisoformat(receipt["createdAt"])
            require(stamp.tzinfo is not None, "job_attempt_invalid")
            result.append((stamp, receipt))
        reader.validate()
    result.sort(key=lambda pair: pair[0])
    require(not any(a[0] == b[0] for a, b in zip(result, result[1:])), "job_attempt_order_ambiguous")
    return [r for _, r in result]


def _latest_required(root, receipt, change, active_receipt=None):
    kind = receipt["kind"]
    if kind not in {"pull_request", "release_status", "deploy", "rollback", "diagnosis", "monitor"}:
        return
    environment = receipt["plan"]["configuration"].get("environment") if kind != "pull_request" else None
    if kind == "monitor":
        attempts = [r for r in _attempts(root, kind, change, environment)
                    if r['plan'].get('adapterId') == receipt['plan'].get('adapterId')
                    and r['plan']['configuration'].get('window') == receipt['plan']['configuration'].get('window')]
    elif kind == "diagnosis":
        attempts = [r for r in _attempts(root, kind, change) if r["plan"]["configuration"].get("incidentId") == receipt["plan"]["configuration"].get("incidentId")]
    elif kind == "pull_request":
        attempts = _attempts(root, kind, change, environment)
    else:
        attempts = []
        for related in ("release_status", "deploy", "rollback"):
            attempts.extend(r for r in _attempts(root, related, change, environment)
                            if r["plan"]["configuration"].get("mode", "live") != "rehearsal")
        attempts.sort(key=lambda r: datetime.datetime.fromisoformat(r["createdAt"]))
        require(not any(datetime.datetime.fromisoformat(a["createdAt"]) == datetime.datetime.fromisoformat(b["createdAt"])
                        for a, b in zip(attempts, attempts[1:])), "job_attempt_order_ambiguous")
        if kind == "rollback" and receipt["plan"]["configuration"].get("mode") == "rehearsal":
            attempts = [r for r in _attempts(root, kind, change, environment)
                        if r["plan"]["configuration"].get("mode") == "rehearsal"]
    attempts = [r for r in attempts if r["id"] != active_receipt]
    require(attempts and attempts[-1]["id"] == receipt["id"], "job_observation_superseded")


def _unknown_effect(receipt):
    return receipt.get("state") in {"running", "blocked"}


def _reconciliation(root, value, change, plan, active_receipt=None):
    effects = _attempts(root, "deploy", change, plan["configuration"]["environment"]) + _attempts(root, "rollback", change, plan["configuration"]["environment"])
    effects = [r for r in effects if r["plan"]["configuration"].get("mode", "live") != "rehearsal"]
    covered, retained_reconciliations = set(), []
    for effect in effects:
        retained_plan = effect["plan"]
        reconciliation = retained_plan.get("statusReconciliation")
        if effect["state"] != "allowed" or not reconciliation or retained_plan["configuration"].get("artifactDigest") != plan["configuration"]["artifactDigest"]:
            continue
        bound = {k: v for k, v in retained_plan.items() if k != "approval_id"}
        require(inputs.digest(bound) == effect["approvalId"] == retained_plan["approval_id"], "job_preview_binding_invalid")
        require(effect.get("command", {}).get("status") == "passed" and effect["command"].get("returnCode") == 0
                and effect.get("observation", {}).get("deployedDigest") == plan["configuration"]["artifactDigest"], "release_reconciliation_history_invalid")
        prior_status, _ = read_json(root, managed_relative(root, RECEIPTS) + "/" + reconciliation["receiptId"] + ".json")
        _validate_receipt(prior_status)
        require(inputs.digest(prior_status) == reconciliation["digest"] and prior_status["state"] == "allowed"
                and prior_status["kind"] == "release_status"
                and prior_status["plan"]["configuration"].get("environment") == plan["configuration"]["environment"]
                and prior_status["plan"]["configuration"].get("artifactDigest") == plan["configuration"]["artifactDigest"], "release_reconciliation_history_invalid")
        status_plan = prior_status["plan"]
        require(inputs.digest({k: v for k, v in status_plan.items() if k != "approval_id"}) == prior_status["approvalId"] == status_plan["approval_id"]
                and prior_status.get("command", {}).get("status") == "passed" and prior_status["command"].get("returnCode") == 0
                and prior_status.get("observation", {}).get("environment") == plan["configuration"]["environment"]
                and prior_status["observation"].get("status") in {"deployed", "rolled_back", "not_deployed"}, "release_reconciliation_history_invalid")
        require(type(reconciliation.get("attempts")) is list, "release_reconciliation_history_invalid")
        for attempt_id in reconciliation["attempts"]:
            matches = [r for r in effects if r["id"] == attempt_id and _unknown_effect(r)]
            require(len(matches) == 1 and datetime.datetime.fromisoformat(matches[0].get("finishedAt", matches[0]["createdAt"]))
                    < datetime.datetime.fromisoformat(prior_status["finishedAt"])
                    <= datetime.datetime.fromisoformat(effect["createdAt"]), "release_reconciliation_history_invalid")
            covered.add(attempt_id)
        retained_reconciliations.append(reconciliation)
    unresolved = [r for r in effects if _unknown_effect(r) and r["id"] != active_receipt and r["id"] not in covered]
    if not unresolved:
        if "status_receipt_id" in value:
            matches = [r for r in retained_reconciliations if r["receiptId"] == value["status_receipt_id"]]
            require(matches and all(r == matches[0] for r in matches), "release_status_not_required")
            plan["statusReconciliation"] = json.loads(json.dumps(matches[0]))
        return
    require("status_receipt_id" in value, "release_effect_reconciliation_required")
    status_id = value["status_receipt_id"]
    require(type(status_id) is str and len(status_id) == 48 and status_id.startswith("development_job_")
            and all(c in "0123456789abcdef" for c in status_id[16:]), "job_receipt_invalid")
    status, _ = read_json(root, managed_relative(root, RECEIPTS) + "/" + status_id + ".json")
    record = {"receiptId": status.get("id"), "observation": status.get("observation")}
    assessment = assess_job_observation(root, record, change, "release_status", _active_receipt=active_receipt)
    require(assessment["state"] == "allowed", "release_status_reconciliation_unproved")
    require(status["plan"]["configuration"].get("environment") == plan["configuration"]["environment"]
            and status["plan"]["configuration"].get("artifactDigest") == plan["configuration"]["artifactDigest"]
            and status["observation"].get("status") in {"deployed", "rolled_back", "not_deployed"}, "release_status_reconciliation_invalid")
    finished = datetime.datetime.fromisoformat(status["finishedAt"])
    require(all(finished > datetime.datetime.fromisoformat(r.get("finishedAt", r["createdAt"])) for r in unresolved), "release_status_reconciliation_stale")
    plan["statusReconciliation"] = {"receiptId": status["id"], "digest": inputs.digest(status),
                                     "attempts": sorted(r["id"] for r in unresolved)}


def prepare(root_value, value, change, *, _locked=False, _active_receipt=None):
    """Read-only preview; absence of a manifest never initializes an adapter."""
    root = Path(root_value).absolute()
    require(type(value) is dict and {"kind", "adapter_id"} <= set(value)
            and set(value) <= {"kind", "adapter_id", "release_packet_id", "status_receipt_id"}
            and value["kind"] in KINDS and type(value["adapter_id"]) is str, "invalid_development_job")
    require(not (set(value) - {"kind", "adapter_id"}) or value["kind"] in ("deploy", "rollback", "release_status"), "invalid_development_job")
    declaration, digest, relative = manifest(root)
    matches = [a for a in declaration["adapters"] if a["id"] == value["adapter_id"] and a["kind"] == value["kind"]]
    require(len(matches) == 1, "adapter_not_configured")
    spec = matches[0]
    if _live(value["kind"], spec["configuration"]):
        from cc_development_lib.evaluator import accepted
        require(accepted(change, "review_release")
                and change.get("gates", {}).get("review_release", {}).get("outcome") == "allowed", "release_gate_not_accepted")
    if value["kind"] == "pull_request" or _live(value["kind"], spec["configuration"]):
        change = _canonical_change(root, change["id"], locked=_locked)
        require(not change["closed"], "release_change_closed")
    if value["kind"] == "diagnosis":
        change = _canonical_change(root, change["id"], locked=_locked, evaluate_gates=False)
    if value['kind']!='diagnosis':
        from cc_product_evolution import guard
        trusted_gates=change.get('gates') if value['kind']=='pull_request' or _live(value['kind'],spec['configuration']) else None
        change=guard(root,change,'review_release' if _live(value['kind'],spec['configuration']) else 'build')
        if trusted_gates is not None:change={**change,'gates':trusted_gates}
    planning_detail = None
    if value['kind'] != 'diagnosis':
        from cc_development_lib.storage import load
        from cc_product_planning import approved_detail
        with inputs.SafeRoot(root) as reader:
            state, _ = load(reader, locked=True)
            planning_detail = approved_detail(reader, state, change['id'], 'plan')
            reader.validate()
    require(candidate_current(root, change.get("candidate"))["state"] == "allowed", "candidate_not_current")
    from cc_development_lib.evaluator import candidate_identity, upstream
    require(change["candidate"]["digest"] == candidate_identity(change, change["candidate"]["files"])
            and change["candidate"].get("upstream") == upstream(change, "build"), "candidate_identity_changed")
    require(_plan_approved(change), "plan_approval_missing")
    pins = []
    for pin in spec["files"]:
        raw = read_bytes(root, pin["path"], limit=32 * 1024 * 1024)
        require(_digest(raw) == pin["sha256"], "adapter_pin_changed")
        pins.append({**pin, "bytes": len(raw)})
    config = spec["configuration"]
    evaluation_plan = None
    pull_request_plan = None
    packet = None
    native_github = None
    diagnosis_request = None
    if value["kind"] == "diagnosis":
        import cc_development_diagnosis as diagnosis
        import cc_development_operations as operations
        require(config.get("mode") in ("deterministic", "agent"), "diagnosis_mode_invalid")
        fields = {"incidentId", "mode"} | ({"configurationPath", "evaluationId"} if config["mode"] == "agent" else set())
        require(set(config) - {"attemptToken"} == fields, "diagnosis_configuration_invalid")
        require(spec["argv"].count("{diagnosisRequest}") == 1, "diagnosis_request_argument_missing")
        diagnosis_request = operations.diagnosis_request(root, change, config["incidentId"])
        diagnosis.assess_request(diagnosis_request)
        if config["mode"] == "agent":
            from cc_development_lib.policy import automation_qualification
            configuration, configuration_digest = read_json(root, config["configurationPath"])
            require(any(p["path"] == config["configurationPath"] and p["sha256"] == configuration_digest for p in pins), "diagnosis_configuration_unpinned")
            qualified = automation_qualification(root, change, {"evaluation_id": config["evaluationId"], "configuration_digest": configuration_digest})
            require(qualified["state"] == "allowed" and qualified["evaluation"]["evaluationPlan"]["configuration"] == configuration, "diagnosis_configuration_unqualified")
    if value["kind"] == "pull_request":
        import cc_development_release
        native_github = _github_worker(root, spec)
        pull_request_plan = cc_development_release.prepare_observation(root, change)
    if value["kind"] == "agent_eval":
        if config.get("evaluationVersion") == 2:
            import cc_development_eval
            evaluation_plan = cc_development_eval.prepare(root, spec, change)
            if planning_detail is not None:
                require(evaluation_plan['suite']['schemaVersion'] == 2, 'eval_planning_protocol_required')
            require(evaluation_plan["configuration"]["budgets"]["totalTimeoutSeconds"] <= spec["timeoutSeconds"], "eval_adapter_timeout_exceeded")
        else:
            require(planning_detail is None, 'eval_planning_protocol_required')
            _prepare_legacy_eval(config, change, pins)
    if value["kind"] in ("release_status", "deploy", "rollback"):
        require(type(config.get("environment")) is str and config["environment"], "release_environment_unconfigured")
        require(HEX.fullmatch(str(config.get("artifactDigest", ""))), "release_target_unconfigured")
    if value["kind"] in ("deploy", "rollback"):
        from cc_development_lib.evaluator import accepted
        rehearsal = value["kind"] == "rollback" and config.get("mode") == "rehearsal"
        if rehearsal:
            from cc_product_evolution import for_change
            from cc_development_store import read_state
            with inputs.SafeRoot(root) as reader:product=(read_state(reader)[0] or {}).get('product',{})
            if for_change(product,change['id']) is not None:change=_canonical_change(root,change['id'],locked=_locked)
            require(accepted(change, "verify")
                    and change.get("gates", {}).get("verify", {}).get("outcome") == "allowed", "rehearsal_verify_gate_not_accepted")
            require(HEX.fullmatch(str(config.get("knownArtifactDigest", ""))), "rehearsal_known_artifact_missing")
            reviews = [r for r in change.get("reviews", []) if r.get("candidate_identity") == change["candidate"]["digest"]
                       and r.get("actor") != change["candidate"].get("actor")]
            require(reviews and not any(f.get("severity") in ("major", "critical") and f.get("disposition") != "resolved"
                                       for f in reviews[-1].get("findings", [])), "rehearsal_independent_review_missing")
            require(change.get("artifacts", {}).get("review_release"), "rehearsal_release_artifact_missing")
        else:
            require(config.get("mode", "live") == "live", "release_execution_mode_invalid")
            require(accepted(change, "review_release")
                    and change.get("gates", {}).get("review_release", {}).get("outcome") == "allowed", "release_gate_not_accepted")
        release = change["artifacts"]["review_release"][-1]["content"]
        require(release.get("environment") == config["environment"], "release_environment_unapproved")
        if not rehearsal:
            require(release.get("release_artifact_identity") == config["artifactDigest"], "release_artifact_unapproved")
            import cc_development_release
            selected = cc_development_release.policy(root, change)
            if selected["required"]:
                require("release_packet_id" in value, "release_packet_required")
    if "release_packet_id" in value:
        import cc_development_release
        packet = cc_development_release.packet_assess(root, change, value["release_packet_id"])
        require(packet["state"] == "allowed", "release_packet_not_current")
        require(packet["binding"]["environment"] == config.get("environment"), "release_packet_wrong_environment")
        require(packet["binding"]["artifactDigest"] == config.get("artifactDigest"), "release_packet_wrong_artifact")
    binary = _executable(spec["argv"][0], root)
    argv = [binary["path"], *spec["argv"][1:]]
    replacements = {"{candidateDigest}": change["candidate"]["digest"], "{changeId}": change["id"],
                    **({"{environment}": config["environment"]} if "environment" in config else {}),
                    **({"{artifactDigest}": config["artifactDigest"]} if "artifactDigest" in config else {})}
    argv = [replacements.get(a, a) for a in argv]
    placeholders = {"{evalRequest}"} if evaluation_plan is not None else set()
    if pull_request_plan is not None:
        placeholders.add("{pullRequestRequest}")
    if diagnosis_request is not None:
        placeholders.add("{diagnosisRequest}")
    require(not any("{" in a or "}" in a for a in argv if a not in placeholders), "adapter_placeholder_invalid")
    engine = Path(__file__).absolute().parent
    engine_pins = {name: _digest(read_bytes(engine, name)) for name in
                   ("cc_development_jobs.py", "cc_development_adapters.py", "cc_evidence_process.py",
                    "cc_development_lib/method.py", "cc_development_lib/evaluator.py", "cc_development_lib/storage.py")}
    if evaluation_plan is not None:
        engine_pins["cc_development_eval.py"] = _digest(read_bytes(engine, "cc_development_eval.py"))
    if pull_request_plan is not None or packet is not None or _live(value["kind"], config):
        engine_pins["cc_development_release.py"] = _digest(read_bytes(engine, "cc_development_release.py"))
    if pull_request_plan is not None:
        engine_pins["cc_development_github.py"] = _digest(read_bytes(engine, "cc_development_github.py"))
    if diagnosis_request is not None:
        for name in ("cc_development_diagnosis.py", "cc_development_operations.py"):
            engine_pins[name] = _digest(read_bytes(engine, name))
    plan = {"schemaVersion": 1, "kind": value["kind"], "adapterId": value["adapter_id"],
            "root": str(root), "changeId": change["id"], "changeRevision": change.get("revision"),
            "projectIdentity": _project_identity(root),
            "controlDigests": _control_digests(root),
            "candidateDigest": change["candidate"]["digest"], "method": change.get("method"),
            "changeDigest": inputs.digest({k: v for k, v in change.items() if k != "gates"}) if value["kind"] == "pull_request" or _live(value["kind"], config) else inputs.digest(change), "manifestPath": relative, "manifestDigest": digest,
            "argv": argv, "executable": binary, "pins": pins, "enginePins": engine_pins,
            "configuration": spec["configuration"], "timeoutSeconds": spec["timeoutSeconds"],
            "outputLimitBytes": spec["outputLimitBytes"], "choices": value,
            "effects": "Execute exactly the project-approved adapter argv with user privileges. The program may modify files or external systems. No shell, provider activation or credential injection is supplied by ControlCoding. Timeout stops the owned child, not its descendants. Structured output is retained locally; review its privacy before running."}
    if diagnosis_request is not None:
        plan["diagnosisRequest"] = diagnosis_request
    if planning_detail is not None:
        plan['planningDetail'] = planning_detail['content']
        plan['planningBinding'] = planning_detail['binding']
    if evaluation_plan is not None:
        plan["evaluationPlan"] = evaluation_plan
    if pull_request_plan is not None:
        plan["pullRequestPlan"] = pull_request_plan
        plan["secondaryExecutable"] = native_github
    if packet is not None:
        plan["releasePacket"] = packet
    if _live(value["kind"], config):
        plan["releaseTarget"] = {"environment": config["environment"], "artifactDigest": release["release_artifact_identity"],
                                 "restoredDigest": config.get("knownArtifactDigest") if value["kind"] == "rollback" else None}
        if value["kind"] == "rollback":
            require(config.get("knownArtifactDigest") == config["artifactDigest"], "rollback_known_artifact_missing")
        _reconciliation(root, value, change, plan, _active_receipt)
    plan["approval_id"] = inputs.digest(plan)
    return plan


def _environment(*, github=False):
    # Explicit minimal inheritance. A project executable can still access host
    # files or credential stores; this is not an isolation boundary.
    keys = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    if github:
        keys += ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    return {k: os.environ[k] for k in keys if k in os.environ}


def execute(root_value, value, change, approval_id):
    declaration, _, _ = manifest(root_value)
    matches = [a for a in declaration["adapters"] if a["id"] == value.get("adapter_id") and a["kind"] == value.get("kind")]
    if len(matches) == 1 and _live(value["kind"], matches[0]["configuration"]):
        from cc_development_lib.storage import transaction
        with inputs.SafeRoot(root_value) as reader:
            with transaction(reader, operation_id="development_release_job"):
                return _execute(root_value, value, change, approval_id, _locked=True)
    return _execute(root_value, value, change, approval_id)


def _execute(root_value, value, change, approval_id, *, _locked=False):
    active_receipt = "development_job_" + inputs.digest(["development-job-v1", approval_id])[:32]
    plan = prepare(root_value, value, change, _locked=_locked, _active_receipt=active_receipt)
    require(plan["approval_id"] == approval_id, "preview_mismatch")
    root = Path(root_value).absolute()
    receipt_id = "development_job_" + inputs.digest(["development-job-v1", approval_id])[:32]
    relative = managed_relative(root, RECEIPTS) + "/" + receipt_id + ".json"
    with inputs.SafeRoot(root) as reader:
        previous = reader.read(relative, inputs.Budget(total=2 * 1024 * 1024, per_file=2 * 1024 * 1024), missing=True)
        if previous is not None:
            retained = json.loads(previous[0], object_pairs_hook=inputs._unique_object)
            _validate_receipt(retained)
            require(retained.get("approvalId") == approval_id and retained.get("plan") == plan, "job_attempt_conflict")
            require(retained.get("state") != "running", "job_attempt_recovery_required")
            _latest_required(root, retained, change)
            return {"receiptId": receipt_id, "receiptPath": relative, "kind": value["kind"],
                    "candidateDigest": plan["candidateDigest"], "state": retained["state"],
                    "reasons": retained["reasons"], "provenance": "observed_command",
                    "observation": retained["observation"], "replayed": True,
                    **({"releasePacketId": value["release_packet_id"]} if "release_packet_id" in value else {})}
        started = {"schemaVersion": 1, "id": receipt_id,
                   "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "kind": value["kind"], "adapterId": value["adapter_id"], "approvalId": approval_id,
                   "plan": plan, "command": None, "observation": None,
                   "observationDigest": inputs.digest(None), "provenance": "observed_command",
                   "state": "running", "reasons": ["job_running"]}
        # Exclusive creation serializes the same approved attempt before effects.
        # An interrupted record remains blocked; it never authorizes a retry.
        reader.write_atomic(relative, _seal(started), replace_existing=False)
        reader.validate()
    if "evaluationPlan" in plan:
        import cc_development_eval
        observation = cc_development_eval.execute(
            root, plan["evaluationPlan"], plan["argv"], executable_pins=[plan["executable"]],
            workspace_relative=managed_relative(root, RECEIPTS) + "/" + receipt_id + "_cases",
            receipt_id=receipt_id, output_limit=plan["outputLimitBytes"])
        observation["candidateDigest"] = plan["candidateDigest"]
        observation["provenance"] = "observed_command"
        assessment = cc_development_eval.assess(plan["evaluationPlan"], observation, root=root)
        reasons = list(assessment["reasons"]) if assessment["state"] != "allowed" else []
        observed = {"status": "passed" if not reasons else "failed", "returnCode": 0 if not reasons else 1,
                    "error": None, "evaluationVersion": 2}
    else:
        argv = plan["argv"]
        if "pullRequestPlan" in plan:
            workspace = managed_relative(root, RECEIPTS) + "/" + receipt_id + "_input"
            with inputs.SafeRoot(root) as reader:
                with reader.directory(workspace, create=True, exclusive=True):
                    pass
                reader.write_atomic(workspace + "/request.json", plan["pullRequestPlan"]["request"], replace_existing=False)
                reader.validate()
            request_path = str(root / workspace / "request.json")
            argv = [request_path if arg == "{pullRequestRequest}" else arg for arg in argv]
        if "diagnosisRequest" in plan:
            workspace = managed_relative(root, RECEIPTS) + "/" + receipt_id + "_input"
            with inputs.SafeRoot(root) as reader:
                with reader.directory(workspace, create=True, exclusive=True):
                    pass
                reader.write_atomic(workspace + "/request.json", plan["diagnosisRequest"], replace_existing=False)
                reader.validate()
            argv = [str(root / workspace / "request.json") if arg == "{diagnosisRequest}" else arg for arg in argv]
        observed = run_command(argv, cwd=root, timeout=plan["timeoutSeconds"],
                               output_limit=plan["outputLimitBytes"], capture_limit=plan["outputLimitBytes"],
                               env=_environment(github="pullRequestPlan" in plan))
        captured = observed.pop("_captured", {})
        complete = observed.pop("_captureComplete", False)
        observation = None
        reasons = []
        if observed["status"] != "passed" or not complete:
            reasons.append("adapter_command_failed_or_incomplete")
        else:
            try:
                observation = json.loads(captured.get("stdout", b""), object_pairs_hook=inputs._unique_object,
                                         parse_constant=lambda _: require(False, "non_json_number"))
                require(type(observation) is dict, "adapter_output_invalid")
                if value["kind"] != "diagnosis":
                    require(observation.get("candidateDigest") == plan["candidateDigest"], "adapter_wrong_candidate")
                # Provenance is assigned by this owner only after the actual bounded
                # command succeeds; output text cannot grant its own provenance.
                if value["kind"] not in ("pull_request", "diagnosis"):
                    observation["provenance"] = "observed_command"
                if value["kind"] == "agent_eval":
                    from cc_development_adapters import assess_agent_eval
                    require(assess_agent_eval(change, plan["configuration"], observation)["state"] == "allowed", "eval_result_failed")
                elif value["kind"] == "diagnosis":
                    import cc_development_diagnosis
                    cc_development_diagnosis.assess_result(plan["diagnosisRequest"], observation)
                elif value["kind"] == "monitor":
                    from cc_development_adapters import monitor_metrics
                    result = monitor_metrics(plan["configuration"], observation)
                    require(result["state"] != "unknown", "monitor_result_incomplete")
                elif value["kind"] == "pull_request":
                    import cc_development_release
                    assessment = cc_development_release.assess_observation(root, change, plan["pullRequestPlan"], observation,
                            datetime.datetime.now(datetime.timezone.utc).isoformat())
                    if assessment["state"] != "allowed":
                        reasons.extend(assessment["reasons"])
                else:
                    require(observation.get("environment") == plan["configuration"]["environment"], "release_wrong_environment")
                    require(HEX.fullmatch(str(observation.get("deployedDigest", ""))), "release_digest_invalid")
                    if "releaseTarget" in plan:
                        require(observation["deployedDigest"] == plan["releaseTarget"]["artifactDigest"], "release_wrong_target_artifact")
                        if value["kind"] == "deploy":
                            require(observation.get("status") == "deployed", "deployment_unobserved")
                        else:
                            require(observation.get("status") == "rolled_back" and observation.get("mode", "live") == "live"
                                    and observation.get("restoredDigest") == plan["releaseTarget"]["restoredDigest"], "rollback_restore_unobserved")
                    if value["kind"] == "rollback" and plan["configuration"].get("mode") == "rehearsal":
                        require(observation.get("mode") == "rehearsal"
                                and observation.get("status") == "rolled_back"
                                and observation.get("restoredDigest") == plan["configuration"]["knownArtifactDigest"],
                                "rehearsal_restore_unobserved")
            except (ValueError, UnicodeError, inputs.EvidenceError):
                observation = None
                reasons.append("adapter_output_invalid")
    try:
        after = prepare(root_value, value, change, _locked=_locked, _active_receipt=receipt_id)
        require(after["approval_id"] == approval_id, "adapter_inputs_changed_during_run")
    except (OSError, ValueError, inputs.EvidenceError):
        reasons.append("adapter_inputs_changed_during_run")
    receipt = {"schemaVersion": 1, "id": receipt_id,
               "createdAt": started["createdAt"],
               "finishedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "kind": value["kind"], "adapterId": value["adapter_id"],
               "approvalId": approval_id, "plan": plan, "command": observed,
               "observation": observation, "observationDigest": inputs.digest(observation),
               "provenance": "observed_command", "state": "blocked" if reasons else "allowed", "reasons": reasons}
    with inputs.SafeRoot(root) as reader:
        reader.write_atomic(relative, _seal(receipt))
        reader.validate()
    return {"receiptId": receipt_id, "receiptPath": relative, "kind": value["kind"],
            "candidateDigest": plan["candidateDigest"], "state": receipt["state"],
            "reasons": reasons, "provenance": "observed_command", "observation": observation,
            **({"releasePacketId": value["release_packet_id"]} if "release_packet_id" in value else {})}


def assess_job_observation(root, record, change, kind, *, _active_receipt=None, _locked=False):
    """Only canonical retained command output establishes observed provenance."""
    try:
        require(type(record) is dict and kind in KINDS, "job_observation_invalid")
        receipt_id = record.get("receiptId", "")
        require(type(receipt_id) is str and len(receipt_id) == 48
                and receipt_id.startswith("development_job_")
                and all(c in "0123456789abcdef" for c in receipt_id[16:]), "job_receipt_invalid")
        relative = managed_relative(root, RECEIPTS) + "/" + receipt_id + ".json"
        receipt, _ = read_json(root, relative)
        _validate_receipt(receipt)
        version_two = kind == "agent_eval" and receipt.get("plan", {}).get("configuration", {}).get("evaluationVersion") == 2
        require(receipt.get("schemaVersion") == 1 and receipt.get("id") == receipt_id
                and receipt.get("kind") == kind and receipt.get("state") in (("allowed", "blocked") if version_two else ("allowed",))
                and receipt.get("provenance") == "observed_command", "job_receipt_invalid")
        plan = receipt["plan"]
        approval = dict(plan)
        approval.pop("approval_id")
        require(inputs.digest(approval) == receipt["approvalId"] == plan["approval_id"], "job_preview_binding_invalid")
        require(plan["projectIdentity"] == _project_identity(root)
                and plan["root"] == str(Path(root).absolute()), "job_wrong_project")
        require(plan["candidateDigest"] == change["candidate"]["digest"]
                and plan["changeId"] == change["id"] and plan["method"] == change.get("method"), "job_wrong_candidate")
        if kind != "diagnosis":
            from cc_development_lib.storage import load
            from cc_product_planning import approved_detail
            with inputs.SafeRoot(root) as reader:
                # Assessment is also called by the canonical evaluator while its
                # transaction lock is held. This pure observation never acquires,
                # releases or writes the registry; load still validates pending
                # effects, the committed state and retained replay.
                state, _ = load(reader, locked=True)
                current_detail = approved_detail(reader, state, change["id"], "plan")
                reader.validate()
            require((current_detail is None and "planningDetail" not in plan and "planningBinding" not in plan)
                    or (current_detail is not None and plan.get("planningDetail") == current_detail["content"]
                        and plan.get("planningBinding") == current_detail["binding"]), "job_planning_detail_stale")
        from cc_development_lib.evaluator import candidate_identity, upstream
        require(change["candidate"]["digest"] == candidate_identity(change, change["candidate"]["files"])
                and change["candidate"].get("upstream") == upstream(change, "build"), "job_candidate_identity_changed")
        require(receipt["observationDigest"] == inputs.digest(receipt["observation"]), "job_observation_changed")
        require(record.get("observation") == receipt["observation"], "job_observation_changed")
        _latest_required(root, receipt, change, _active_receipt)
        declaration, digest, _ = manifest(root)
        require(digest == plan["manifestDigest"], "job_manifest_changed")
        for pin in plan["pins"]:
            require(_digest(read_bytes(root, pin["path"], limit=32 * 1024 * 1024)) == pin["sha256"], "job_pin_changed")
        require(candidate_current(root, change["candidate"])["state"] == "allowed", "candidate_not_current")
        command = receipt["command"]
        if not version_two:
            require(command.get("status") == "passed" and command.get("returnCode") == 0
                    and command.get("error") is None, "job_command_unpassed")
        if kind == "rollback" and plan["configuration"].get("mode") == "rehearsal":
            require(receipt["observation"].get("mode") == "rehearsal"
                    and receipt["observation"].get("status") == "rolled_back"
                    and receipt["observation"].get("restoredDigest") == plan["configuration"].get("knownArtifactDigest"),
                    "rehearsal_restore_unobserved")
        binary = _executable(plan["argv"][0], root)
        require(binary == plan["executable"], "job_executable_changed")
        engine = Path(__file__).absolute().parent
        require(all(_digest(read_bytes(engine, name)) == digest for name, digest in plan["enginePins"].items()),
                "job_engine_changed")
        if kind == "diagnosis":
            import cc_development_diagnosis
            current = prepare(root, plan["choices"], change, _locked=_locked)
            require(current == plan, "diagnosis_inputs_changed")
            result = cc_development_diagnosis.assess_result(plan["diagnosisRequest"], receipt["observation"])
            return {**result, "observation": receipt["observation"], "incidentRevision": plan["diagnosisRequest"]["incidentRevision"], "receiptId": receipt_id, "provenance": "observed_command"}
        if kind == "pull_request":
            import cc_development_release
            matches = [item for item in declaration["adapters"] if item["id"] == plan["adapterId"] and item["kind"] == kind]
            require(len(matches) == 1 and _github_worker(root, matches[0]) == plan.get("secondaryExecutable"), "pr_worker_changed")
            current = cc_development_release.prepare_observation(root, change)
            require(current == plan.get("pullRequestPlan"), "pr_observation_inputs_changed")
            result = cc_development_release.assess_observation(root, change, current, receipt["observation"], receipt.get("finishedAt"))
            return {**result, "observation": receipt["observation"], "configuration": plan["configuration"],
                    "provenance": "observed_command", "receiptId": receipt_id}
        if "releaseTarget" in plan:
            from cc_development_lib.evaluator import accepted, latest
            artifact = latest(change, "review_release")
            require(artifact and accepted(change, "review_release")
                    and artifact["content"].get("environment") == plan["releaseTarget"]["environment"]
                    and artifact["content"].get("release_artifact_identity") == plan["releaseTarget"]["artifactDigest"], "release_target_changed")
            require(receipt["observation"].get("deployedDigest") == plan["releaseTarget"]["artifactDigest"], "release_wrong_target_artifact")
        if "releasePacket" in plan:
            import cc_development_release
            packet_id = plan["choices"]["release_packet_id"]
            result = cc_development_release.packet_assess(root, change, packet_id)
            require(result["state"] == "allowed" and result == plan["releasePacket"], "release_packet_not_current")
        if version_two:
            import cc_development_eval
            matches = [item for item in declaration["adapters"] if item["id"] == plan["adapterId"] and item["kind"] == kind]
            require(len(matches) == 1, "adapter_not_configured")
            current_evaluation = cc_development_eval.prepare(root, matches[0], change)
            require(current_evaluation == plan.get("evaluationPlan"), "eval_identity_changed")
            require(receipt["observation"].get("workspace") == managed_relative(root, RECEIPTS) + "/" + receipt_id + "_cases", "eval_workspace_binding_invalid")
            result = cc_development_eval.assess(current_evaluation, receipt["observation"], root=root)
            require(receipt["state"] == result["state"] or receipt["state"] == "blocked", "eval_assessment_changed")
            qualified = result["state"] == "allowed" and receipt["state"] == "allowed"
            combined_reasons = list(dict.fromkeys(result["reasons"] + receipt["reasons"]))
            return {**result, "state": "allowed" if qualified else "blocked", "reasons": combined_reasons,
                    "qualification": qualified,
                    "qualificationReasons": [] if qualified else combined_reasons,
                    "qualificationIdentity": inputs.digest(current_evaluation), "evaluationPlan": current_evaluation,
                    "configurationDigest": current_evaluation["configDigest"], "suiteDigest": current_evaluation["taskDigest"],
                    "model": current_evaluation["configuration"]["model"], "usage": receipt["observation"].get("usage"),
                    "cases": receipt["observation"].get("cases"), "budgets": current_evaluation["configuration"]["budgets"],
                    "observation": receipt["observation"], "configuration": plan["configuration"],
                    "provenance": "observed_command", "receiptId": receipt_id}
        return {"state": "allowed", "reasons": [], "observation": receipt["observation"],
                "qualification": False, "qualificationReasons": ["legacy_eval_not_qualification"] if kind == "agent_eval" else [],
                "configuration": plan["configuration"], "provenance": "observed_command", "receiptId": receipt_id,
                **({"releasePacketId": plan["choices"]["release_packet_id"]} if "release_packet_id" in plan["choices"] else {})}
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as exc:
        return {"state": "unknown", "reasons": [getattr(exc, "code", "job_receipt_unavailable")], "observation": None}
