"""Read-only owner adapters for the controlled development method.

Local hashes identify bytes, not authenticated people or signed attestations.
Project code is never imported and provider connections are never activated.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

import cc_evidence_inputs as inputs
from cc_layout import managed_relative

HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")


def require(condition, code):
    if not condition:
        raise inputs.EvidenceError(code)


def read_bytes(root, relative, *, limit=2 * 1024 * 1024):
    inputs.parts(relative)
    with inputs.SafeRoot(root) as reader:
        raw, _ = reader.read(relative, inputs.Budget(total=limit, per_file=limit))
        reader.validate()
    return raw


def read_json(root, relative):
    raw = read_bytes(root, relative)
    value = json.loads(raw, object_pairs_hook=inputs._unique_object,
                      parse_constant=lambda _: require(False, "non_json_number"))
    require(type(value) is dict, "owner_record_not_object")
    return value, hashlib.sha256(raw).hexdigest()


def latest_artifact(change, stage):
    rows = change.get("artifacts", {}).get(stage, [])
    return rows[-1] if isinstance(rows, list) and rows else {}


def _result(state, reasons=(), **extra):
    return {"state": state, "reasons": list(reasons), **extra}


def candidate_current(root, candidate):
    """Additional exact-file binding; canonical receipt inventory remains required."""
    if not isinstance(candidate, dict) or not HEX.fullmatch(str(candidate.get("digest", ""))):
        return _result("unknown", ["candidate_missing"])
    rows = candidate.get("files")
    if not isinstance(rows, list) or not rows or len(rows) > inputs.MAX_ENTRIES:
        return _result("blocked", ["candidate_inventory_invalid"])
    try:
        names = []
        with inputs.SafeRoot(root) as reader:
            budget = inputs.Budget()
            for row in rows:
                require(type(row) is dict and set(row) == {"path", "sha256", "size"}, "candidate_inventory_invalid")
                inputs.parts(row["path"])
                require(HEX.fullmatch(str(row["sha256"])) and type(row["size"]) is int and row["size"] >= 0,
                        "candidate_inventory_invalid")
                names.append(row["path"].casefold())
                raw, _ = reader.read(row["path"], budget)
                require(len(raw) == row["size"] and hashlib.sha256(raw).hexdigest() == row["sha256"], "candidate_changed")
            require(len(set(names)) == len(names), "candidate_inventory_invalid")
            reader.validate()
        return _result("allowed")
    except (OSError, ValueError, KeyError, TypeError, inputs.EvidenceError) as exc:
        return _result("blocked", [getattr(exc, "code", "candidate_unreadable")])


def assess_evidence(root, change, records=None):
    """Assess canonical latest attempts, never supplied passing claims.

    A newer incomplete/subset/malformed attempt cannot reveal an older pass.
    Check IDs must exist in the *current required* contract and be executed.
    Human evidence is intentionally a reported observation requiring a separate
    candidate-bound criterion acceptance decision in the authoritative history.
    """
    import cc
    root = Path(root)
    reasons = []
    candidate = candidate_current(root, change.get("candidate"))
    reasons.extend(candidate["reasons"])
    if change.get("candidate"):
        from cc_development_lib.evaluator import candidate_identity, upstream
        if (candidate_identity(change, change["candidate"]["files"]) != change["candidate"]["digest"]
                or change["candidate"].get("upstream") != upstream(change, "build")):
            reasons.append("candidate_identity_changed")
    checks = {}
    current = {}
    for kind in ("verification", "invariants"):
        prepared = cc._evidence_prepare(root, kind)
        history = cc._evidence_history(root, kind, prepared)
        assessment = history["assessment"]
        checks[kind] = {"assessment": assessment, "required": list(prepared["required"]),
                        "latest": history.get("latest")}
        current[kind] = {r["id"] for r in (history.get("latest") or {}).get(
            "suites" if kind == "verification" else "invariants", []) if r["status"] == "passed"}
        if not assessment["currentRequiredPass"]:
            reasons.extend(kind + ":" + r for r in assessment["reasons"] or ["required_run_not_passed"])
    evaluations = []
    for record in records or []:
        if isinstance(record, dict) and record.get("kind") == "agent_eval":
            from cc_development_jobs import assess_job_observation
            observed = assess_job_observation(root, record, change, "agent_eval")
            result = observed
            evaluations.append(result)
            if result["state"] != "allowed":
                reasons.extend("agent_eval:" + reason for reason in result["reasons"])
    criteria = latest_artifact(change, "design").get("content", {}).get("criteria", [])
    coverage = []
    seen = set()
    if not isinstance(criteria, list) or not criteria:
        reasons.append("criteria_missing")
        criteria = []
    for row in criteria:
        why = []
        if not isinstance(row, dict) or not IDENTIFIER.fullmatch(str(row.get("id", ""))) or row.get("id") in seen:
            reasons.append("criteria_invalid")
            continue
        seen.add(row["id"])
        required = row.get("required", True)
        if type(required) is not bool:
            why.append("criterion_required_invalid")
        proof = row.get("proof", "automated")
        if proof not in ("automated", "human", "both"):
            why.append("criterion_proof_invalid")
        mapped = row.get("checks", [])
        if proof in ("automated", "both"):
            if not isinstance(mapped, list) or not mapped:
                why.append("criterion_checks_missing")
            else:
                for check in mapped:
                    if (not isinstance(check, dict) or set(check) != {"kind", "id"}
                            or check.get("kind") not in checks):
                        why.append("criterion_check_invalid")
                        continue
                    kind = check["kind"]
                    if check["id"] not in checks[kind]["required"]:
                        why.append("criterion_check_not_required:" + str(check["id"]))
                    elif not checks[kind]["assessment"]["currentRequiredPass"] or check["id"] not in current[kind]:
                        why.append("criterion_check_unproved:" + check["id"])
        if proof in ("human", "both"):
            # The adapter does not convert imported or model-authored prose to
            # acceptance. A separately recorded human criterion decision owns it.
            matches = [r for r in (records or []) if isinstance(r, dict) and r.get("kind") == "human"
                       and r.get("criterionId") == row["id"]
                       and r.get("candidateDigest") == (change.get("candidate") or {}).get("digest")
                       and isinstance(r.get("observation"), str) and r["observation"].strip()]
            decisions = change.get("criterionDecisions", [])
            accepted = any(isinstance(d, dict) and d.get("criterionId") == row["id"]
                           and d.get("candidateDigest") == (change.get("candidate") or {}).get("digest")
                           and d.get("decision") == "accept" and d.get("actor")
                           and d.get("evidenceDigest") in {inputs.digest(r) for r in matches}
                           for d in decisions)
            if not accepted:
                why.append("human_criterion_acceptance_missing")
        coverage.append({"id": row["id"], "required": required, "state": "blocked" if why else "allowed", "reasons": why})
        if required:
            reasons.extend(row["id"] + ":" + reason for reason in why)
    state = "allowed" if not reasons else "blocked"
    if reasons and candidate["state"] == "unknown":
        state = "unknown"
    return _result(state, reasons, checks=checks, criteria=coverage, candidate=candidate, agentEvaluations=evaluations)


def planner_binding(root, relative=None, expected_digest=None):
    """Read Planner's own hash/approval schema without running its export chain."""
    relative = relative or managed_relative(root, "devlog/plans/plan.current.json")
    try:
        plan, digest = read_json(root, relative)
        required = {"version", "status", "status_hash", "approved_at", "scope", "phases", "invariants_global", "architecture", "expansion_state", "created_at"}
        require(required <= set(plan), "planner_schema_invalid")
        require(type(plan["version"]) is int and plan["version"] > 0 and type(plan["phases"]) is list,
                "planner_schema_invalid")
        require(plan["status"] == "APPROVED" and isinstance(plan["approved_at"], str) and plan["approved_at"], "planner_approval_missing")
        require(type(plan["architecture"]) is dict and type(plan["invariants_global"]) is list
                and type(plan["expansion_state"]) is dict, "planner_schema_invalid")
        content = dict(plan)
        content.pop("status_hash")
        owner_hash = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        require(plan["status_hash"] == owner_hash, "planner_hash_mismatch")
        require(expected_digest is None or digest == expected_digest, "planner_source_changed")
        return _result("allowed", owner="planner", path=relative, sourceDigest=digest,
                       ownerHash=owner_hash, revision=plan["version"], approvalMode="local_record", readOnly=True)
    except (OSError, ValueError, KeyError, TypeError, inputs.EvidenceError) as exc:
        return _result("unknown", [getattr(exc, "code", "planner_unavailable")], owner="planner", readOnly=True)


def feature_binding(root, feature_ids, expected_digest=None):
    """Observe feature ownership/WIP; legacy passing is never lifecycle evidence."""
    import cc_feature
    relative = managed_relative(root, ".controlcoding/features/features.json")
    try:
        registry, digest = read_json(root, relative)
        require(registry.get("schemaVersion") == cc_feature.FEATURE_STATE_SCHEMA_VERSION,
                "feature_schema_invalid")
        require(type(registry.get("features")) is list and type(registry.get("wipLimit")) is int
                and registry["wipLimit"] > 0, "feature_schema_invalid")
        require(not cc_feature._validate_registry(registry), "feature_registry_invalid")
        require(type(feature_ids) is list and feature_ids and len(set(feature_ids)) == len(feature_ids), "feature_ids_invalid")
        found = {r["id"]: r for r in registry["features"]}
        require(all(i in found for i in feature_ids), "feature_reference_missing")
        require(expected_digest is None or expected_digest == digest, "feature_source_changed")
        return _result("allowed", owner="features", path=relative, sourceDigest=digest,
                       featureIds=list(feature_ids), readOnly=True, gateEvidence=False,
                       states={i: found[i]["state"] for i in feature_ids})
    except (OSError, ValueError, KeyError, TypeError, inputs.EvidenceError) as exc:
        return _result("unknown", [getattr(exc, "code", "feature_unavailable")], owner="features", readOnly=True)


def manual_handoff(root, change):
    """An explicit approved plan packet; a response remains self-reported work."""
    from cc_product_evolution import guard
    change=guard(root,change,'build')
    plan = latest_artifact(change, "plan")
    from cc_development_lib.evaluator import accepted
    approved = accepted(change, "plan")
    require(plan and approved, "plan_approval_missing")
    candidate = change.get("candidate")
    require(candidate is None or candidate_current(root, candidate)["state"] == "allowed", "candidate_not_current")
    paths = plan.get("content", {}).get("files", [])
    require(type(paths) is list and paths and len(paths) <= 256
            and all(type(p) is str for p in paths) and len(set(paths)) == len(paths), "handoff_scope_invalid")
    baseline = []
    with inputs.SafeRoot(root) as reader:
        budget = inputs.Budget(total=64 * 1024 * 1024, per_file=8 * 1024 * 1024, entries=256)
        for path in sorted(paths):
            budget.entry()
            inputs.parts(path)
            item = reader.read(path, budget, missing=True)
            if item is None:
                baseline.append({"path": path, "state": "absent"})
            else:
                raw, _ = item
                baseline.append({"path": path, "state": "existing", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
        reader.validate()
    packet = {"schemaVersion": 1, "kind": "manual_handoff", "changeId": change.get("id"),
              "changeRevision": change.get("revision"), "candidateDigest": candidate["digest"] if candidate else None,
              "method": change.get("method"), "planDigest": plan["digest"], "plan": plan.get("content"),
              "files": candidate["files"] if candidate else [], "baseline": baseline,
              "baselineDigest": inputs.digest(baseline), "upstream": plan.get("upstream"), "executionMode": "manual",
              "executionObserved": False, "responseEvidence": "self_reported"}
    from cc_development_lib.storage import identity
    with inputs.SafeRoot(root) as reader:
        packet["projectIdentity"] = identity(reader)
        from cc_development_lib.storage import load
        from cc_product_planning import approved_detail
        state, _ = load(reader, locked=True)
        detail = approved_detail(reader, state, change['id'], 'plan')
        if detail is not None:
            packet['planningDetail'] = detail['content']
            packet['planningBinding'] = detail['binding']
        reader.validate()
    return {**packet, "packetDigest": inputs.digest(packet)}


def assess_agent_eval(change, specification, observation):
    """Independent grader/config/task identity, with every required case observed."""
    try:
        require(type(specification) is dict and type(observation) is dict, "eval_invalid")
        require(specification["implementationActor"] == change["candidate"].get("actor"), "eval_implementation_actor_changed")
        require(specification["implementationActor"] != specification["graderActor"], "eval_grader_not_independent")
        for name in ("configDigest", "taskDigest", "graderDigest"):
            require(HEX.fullmatch(str(specification[name])) and specification[name] == observation.get(name), "eval_identity_changed")
        require(observation.get("candidateDigest") == change["candidate"]["digest"], "eval_wrong_candidate")
        require(observation.get("graderActor") == specification["graderActor"], "eval_grader_changed")
        require(observation.get("provenance") == "observed_command", "eval_unobserved")
        cases = specification["requiredCases"]
        require(type(cases) is list and cases and len(set(cases)) == len(cases)
                and all(type(c) is str and IDENTIFIER.fullmatch(c) for c in cases), "eval_cases_invalid")
        rows = observation["cases"]
        require(type(rows) is list and len(rows) == len(cases)
                and {r["id"] for r in rows} == set(cases), "eval_cases_incomplete")
        require(all(set(r) == {"id", "passed"} and r["passed"] is True for r in rows), "eval_case_failed")
        return _result("allowed", provenance="observed_command", specificationDigest=inputs.digest(specification),
                       qualification=False, qualificationReasons=["legacy_eval_not_qualification"])
    except (ValueError, TypeError, KeyError, inputs.EvidenceError) as exc:
        return _result("blocked", [getattr(exc, "code", "eval_invalid")])


def monitor_metrics(configuration, observation, prior_incident_ids=()):
    """Deterministic baseline/window assessment; no samples means unknown."""
    try:
        require(type(configuration) is dict and type(observation) is dict, "monitor_invalid")
        require(HEX.fullmatch(str(configuration["deployedDigest"])), "monitor_release_identity_invalid")
        require(observation.get("deployedDigest") == configuration["deployedDigest"], "monitor_wrong_release")
        require(observation.get("window") == configuration["window"] and type(configuration["window"]) is str
                and configuration["window"], "monitor_wrong_window")
        rules = configuration["metrics"]
        samples = observation["metrics"]
        require(type(rules) is list and rules and type(samples) is dict, "monitor_data_missing")
        proposals, seen = [], set()
        for rule in rules:
            require(type(rule) is dict and set(rule) == {"id", "baseline", "maxDelta", "minimumSamples"}, "monitor_rule_invalid")
            name = rule["id"]
            require(type(name) is str and IDENTIFIER.fullmatch(name) and name not in seen, "monitor_rule_invalid")
            seen.add(name)
            require(type(rule["minimumSamples"]) is int and rule["minimumSamples"] > 0, "monitor_baseline_invalid")
            require(all(type(rule[k]) in (int, float) and math.isfinite(rule[k]) for k in ("baseline", "maxDelta"))
                    and rule["maxDelta"] >= 0, "monitor_baseline_invalid")
            row = samples.get(name)
            require(type(row) is dict and set(row) == {"value", "samples", "citation"}, "monitor_data_missing")
            require(type(row["value"]) in (int, float) and math.isfinite(row["value"])
                    and type(row["samples"]) is int and row["samples"] >= rule["minimumSamples"]
                    and type(row["citation"]) is str and row["citation"].strip(), "monitor_data_insufficient")
            if row["value"] > rule["baseline"] + rule["maxDelta"]:
                key = inputs.digest({"deployedDigest": configuration["deployedDigest"], "window": configuration["window"], "metric": name})
                if key not in prior_incident_ids:
                    proposals.append({"id": key, "kind": "incident_proposal", "metric": name,
                                      "deployedDigest": configuration["deployedDigest"], "window": configuration["window"],
                                      "value": row["value"], "baseline": rule["baseline"], "citation": row["citation"],
                                      "accepted": False, "origin": "monitor"})
        require(set(samples) == seen, "monitor_metric_coverage_mismatch")
        breached = any(samples[r["id"]]["value"] > r["baseline"] + r["maxDelta"] for r in rules)
        return _result("blocked" if breached else "allowed", ["monitor_threshold_breached"] if breached else [],
                       proposals=proposals, configurationDigest=inputs.digest(configuration))
    except (ValueError, TypeError, KeyError, inputs.EvidenceError) as exc:
        return _result("unknown", [getattr(exc, "code", "monitor_invalid")], proposals=[])


def release_binding(root, record, candidate):
    """Verify exact local base/head objects without fetch, checkout, or ref writes."""
    try:
        require(type(record) is dict, "release_invalid")
        require(record.get("candidateDigest") == candidate.get("digest"), "release_wrong_candidate")
        # Only assess_release supplies output after validating the canonical
        # job receipt. This helper establishes local Git relation only.
        require(all(isinstance(record.get(k), str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", record[k])
                    for k in ("base", "head")), "release_commit_invalid")
        with inputs.SafeRoot(root) as reader:
            budget = inputs.Budget()
            with reader.git_projection(budget) as projection:
                for name in ("base", "head"):
                    code, raw = projection.git(["rev-parse", "--verify", record[name] + "^{commit}"], budget=budget)
                    require(code == 0 and raw.decode("ascii").strip() == record[name], "release_commit_missing")
                code, raw = projection.git(["rev-parse", "--verify", "HEAD"], budget=budget)
                require(code == 0 and raw.decode("ascii").strip() == record["head"], "release_head_changed")
                code, _ = projection.git(["merge-base", "--is-ancestor", record["base"], record["head"]], budget=budget)
                require(code == 0, "release_base_not_ancestor")
                for item in candidate["files"]:
                    code, raw = projection.git(["cat-file", "blob", record["head"] + ":" + item["path"]], budget=budget)
                    require(code == 0 and len(raw) == item["size"] and hashlib.sha256(raw).hexdigest() == item["sha256"],
                            "release_candidate_not_committed")
            reader.validate()
        require(candidate_current(root, candidate)["state"] == "allowed", "release_candidate_changed")
        return _result("allowed", provenance="observed_local_git", hostedStatus="unknown",
                       base=record["base"], head=record["head"])
    except (OSError, ValueError, TypeError, KeyError, inputs.EvidenceError) as exc:
        return _result("unknown", [getattr(exc, "code", "release_unavailable")], provenance="self_reported")


def assess_release(root, change, action):
    """Validate a retained deployment/status observation and its exact Git pair."""
    from cc_development_jobs import assess_job_observation
    try:
        observation = action.get("observation")
        require(type(observation) is dict, "release_observation_missing")
        kind = action.get("adapter_kind", observation.get("kind", "release_status"))
        require(kind in ("release_status", "deploy", "rollback"), "release_adapter_kind_invalid")
        job = assess_job_observation(root, observation, change, kind)
        require(job["state"] == "allowed", "release_job_unobserved")
        observed = job["observation"]
        require(job["configuration"].get("mode", "live") != "rehearsal", "release_rehearsal_not_deployment")
        local = release_binding(root, observed, change["candidate"])
        require(local["state"] == "allowed", "release_git_relation_unverified")
        require(observed.get("environment") == action.get("environment")
                and observed.get("deployedDigest") == action.get("artifact_digest"), "release_observation_mismatch")
        require(observed.get("status") in ("deployed", "released", "rolled_back"), "release_status_unobserved")
        require(job["configuration"].get("environment") == action.get("environment"), "release_environment_unapproved")
        require(HEX.fullmatch(str(observed.get("deployedDigest", ""))), "release_digest_invalid")
        from cc_development_release import assessment as assess_pull_request, packet_assess
        pull_request = assess_pull_request(root, change)
        require(pull_request['state'] == 'allowed', 'release_pr_proof_stale')
        if pull_request.get('required'):
            require(job.get('releasePacketId'), 'release_packet_missing')
            packet = packet_assess(root, change, job['releasePacketId'])
            require(packet['state'] == 'allowed', 'release_packet_stale')
            require(packet['binding']['environment'] == action.get('environment')
                    and packet['binding']['artifactDigest'] == action.get('artifact_digest'), 'release_packet_target_mismatch')
        return _result("allowed", provenance="observed_command", observation=observed,
                       receiptId=job["receiptId"], localGit=local)
    except (TypeError, KeyError, ValueError, inputs.EvidenceError) as exc:
        return _result("unknown", [getattr(exc, "code", "release_unavailable")], provenance="self_reported")


def assess_monitor(root, change, record, prior_incident_ids=()):
    """Owner-observed metrics bound to an actually observed retained release."""
    from cc_development_jobs import assess_job_observation
    observed = assess_job_observation(root, record, change, "monitor")
    if observed["state"] != "allowed":
        return _result("unknown", observed["reasons"], proposals=[])
    deployed = observed["configuration"].get("deployedDigest")
    releases = [r for r in change.get("releases", []) if isinstance(r, dict)
                and r.get("status") == "observed" and r.get("artifact_digest") == deployed]
    if not releases:
        return _result("unknown", ["monitor_observed_release_missing"], proposals=[])
    if not any(assess_release(root, change, release)["state"] == "allowed" for release in releases):
        return _result("unknown", ["monitor_release_observation_stale"], proposals=[])
    result = monitor_metrics(observed["configuration"], observed["observation"], prior_incident_ids)
    return {**result, "provenance": "observed_command", "receiptId": observed["receiptId"]}


def assess_owner_bindings(root, change):
    """Opt-in design source references retain the existing owner's identity.

    Reference schema: {owner:'planner',path,sourceDigest} or
    {owner:'features',featureIds,sourceDigest}. Other citations remain citations.
    """
    references = latest_artifact(change, "design").get("content", {}).get("source_references", [])
    results = []
    for reference in references if isinstance(references, list) else []:
        if not isinstance(reference, dict):
            continue
        if reference.get("owner") == "planner":
            if set(reference) != {"owner", "path", "sourceDigest"} or not HEX.fullmatch(str(reference.get("sourceDigest", ""))):
                results.append(_result("blocked", ["planner_reference_invalid"]))
            else:
                results.append(planner_binding(root, reference["path"], reference["sourceDigest"]))
        elif reference.get("owner") == "features":
            if set(reference) != {"owner", "featureIds", "sourceDigest"} or not HEX.fullmatch(str(reference.get("sourceDigest", ""))):
                results.append(_result("blocked", ["feature_reference_invalid"]))
            else:
                results.append(feature_binding(root, reference["featureIds"], reference["sourceDigest"]))
    reasons = [reason for result in results if result["state"] != "allowed" for reason in result["reasons"]]
    return _result("blocked" if reasons else "allowed", reasons, bindings=results)
