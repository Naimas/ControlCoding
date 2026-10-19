"""Owner evidence and bounded jobs use only external pytest fixture projects."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc
import cc_development_adapters as adapters
import cc_development_jobs as jobs
import cc_evidence_inputs as inputs
from cc_development_lib.evaluator import candidate_identity, upstream
from cc_development_lib.method import IDENTITY


def put(root, path, value):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value), encoding="utf-8")


def change_fixture(root):
    # The adapter artifacts below deliberately isolate evidence semantics, but
    # their unlinked origin must belong to the real canonical change registry.
    from test_cc_development_core import act
    from cc_development_lib import read
    state = read(root)
    if not state["enabled"]:
        state = act(root, {"kind": "activate", "profile": "routine"})
    if not any(change["id"] == "change-1" for change in state["changes"]):
        act(root, {"kind": "create", "change_id": "change-1", "title": "Adapter evidence fixture",
                   "owner": "human-owner", "risk": "routine"})
    (root / "app.py").write_text("print('application')\n", encoding="utf-8")
    criterion = {"id": "criterion-1", "requirement_id": "requirement-1", "text": "Declared check succeeds",
                 "required": True, "proof": "automated", "checks": [{"kind": "verification", "id": "check-1"}]}
    design = {"revision": "design-1", "digest": "d" * 64, "content": {"criteria": [criterion]}, "upstream": {}}
    plan = {"revision": "plan-1", "digest": "e" * 64, "content": {"files": ["app.py"]}, "upstream": {"design": design["digest"]}}
    change = {"id": "change-1", "revision": "revision-1", "method": IDENTITY, "artifacts": {"design": [design], "plan": [plan]},
              "decisions": [{"stage": "plan", "decision": "accepted", "artifact_revision": plan["revision"],
                             "upstream": plan["upstream"], "actor": "human-owner"}], "criterionDecisions": []}
    raw = (root / "app.py").read_bytes()
    files = [{"path": "app.py", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}]
    change["candidate"] = {"digest": candidate_identity(change, files), "files": files, "actor": "implementation-agent", "upstream": upstream(change, "build")}
    return change


def contracts(root, two=False):
    runner = root / "pass.py"
    runner.write_text("pass\n", encoding="utf-8")
    command = '"' + sys.executable + '" pass.py'
    suites = [{"id": "check-1", "name": "One", "kind": "targeted", "required": True, "command": command, "timeoutSeconds": 10}]
    if two:
        suites.append({**suites[0], "id": "check-2"})
    put(root, "controlcoding.verification.json", {"schemaVersion": 1, "requiredKinds": ["targeted"], "suites": suites})
    put(root, "controlcoding.invariants.json", {"schemaVersion": 1, "invariants": [{"id": "invariant-1", "domain": "testing", "kind": "domain", "severity": "blocking", "status": "active", "property": "Works", "threshold": "pass", "command": command}]})


def run_receipts(root, suite_ids=None):
    assert cc.cmd_verify_run(root, suite_ids=suite_ids, json_output=True) == 0
    assert cc.cmd_invariants_run(root, json_output=True) == 0


def test_canonical_required_receipts_pass_and_claims_cannot_replace_them(tmp_path):
    change = change_fixture(tmp_path)
    contracts(tmp_path)
    assert adapters.assess_evidence(tmp_path, change, [{"passed": True}])["state"] != "allowed"
    run_receipts(tmp_path)
    result = adapters.assess_evidence(tmp_path, change)
    assert result["state"] == "allowed", result
    assert result["criteria"][0]["state"] == "allowed"
    assert adapters.manual_handoff(tmp_path, change)["executionObserved"] is False


@pytest.mark.parametrize("mutation", ["source", "candidate", "contract", "malformed", "subset", "mapping", "human"])
def test_wrong_stale_partial_or_claimed_evidence_blocks(tmp_path, mutation):
    change = change_fixture(tmp_path)
    contracts(tmp_path, two=True)
    run_receipts(tmp_path)
    assert adapters.assess_evidence(tmp_path, change)["state"] == "allowed"
    if mutation == "source":
        (tmp_path / "pass.py").write_text("# source changed\n", encoding="utf-8")
    elif mutation == "candidate":
        change["candidate"]["digest"] = "0" * 64
    elif mutation == "contract":
        contract = json.loads((tmp_path / "controlcoding.verification.json").read_text())
        contract["suites"][0]["name"] = "Changed"
        put(tmp_path, "controlcoding.verification.json", contract)
    elif mutation == "malformed":
        put(tmp_path, ".controlcoding/verification_receipts/newest.json", {"status": "passed"})
    elif mutation == "subset":
        assert cc.cmd_verify_run(tmp_path, suite_ids=["check-1"], json_output=True) == 0
    elif mutation == "mapping":
        change["artifacts"]["design"][-1]["content"]["criteria"][0]["checks"][0]["id"] = "unknown-check"
    else:
        change["artifacts"]["design"][-1]["content"]["criteria"][0]["proof"] = "human"
    claims = [{"kind": "human", "criterionId": "criterion-1", "candidateDigest": change["candidate"]["digest"],
               "observation": "I checked it", "accepted": True, "passed": True}]
    result = adapters.assess_evidence(tmp_path, change, claims)
    assert result["state"] != "allowed", result


def test_manual_packet_requires_current_plan_decision_and_candidate(tmp_path):
    change = change_fixture(tmp_path)
    assert adapters.manual_handoff(tmp_path, change)["packetDigest"]
    change["decisions"][-1]["decision"] = "rejected"
    with pytest.raises(inputs.EvidenceError, match="plan_approval_missing"):
        adapters.manual_handoff(tmp_path, change)


def test_manual_prebuild_packet_binds_existing_and_absent_scope(tmp_path):
    change = change_fixture(tmp_path)
    change["candidate"] = None
    change["artifacts"]["plan"][-1]["content"]["files"].append("future/module.py")
    packet = adapters.manual_handoff(tmp_path, change)
    assert packet["candidateDigest"] is None and not packet["files"]
    assert packet["baseline"][0]["state"] == "existing"
    assert packet["baseline"][1] == {"path": "future/module.py", "state": "absent"}
    assert packet["executionObserved"] is False
    (tmp_path / "app.py").write_text("changed\n", encoding="utf-8")
    assert adapters.manual_handoff(tmp_path, change)["baselineDigest"] != packet["baselineDigest"]


def test_planner_current_hash_and_feature_passing_are_read_only(tmp_path):
    plan = {"version": 1, "status": "APPROVED", "status_hash": "", "approved_at": "2026-10-01T00:00:00Z",
            "scope": "Example", "phases": [], "invariants_global": [], "architecture": {}, "expansion_state": {}, "created_at": ""}
    content = dict(plan)
    content.pop("status_hash")
    plan["status_hash"] = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    put(tmp_path, "devlog/plans/plan.current.json", plan)
    before = (tmp_path / "devlog/plans/plan.current.json").read_bytes()
    assert adapters.planner_binding(tmp_path)["state"] == "allowed"
    assert (tmp_path / "devlog/plans/plan.current.json").read_bytes() == before
    plan["scope"] = "Unapproved drift"
    put(tmp_path, "devlog/plans/plan.current.json", plan)
    assert adapters.planner_binding(tmp_path)["reasons"] == ["planner_hash_mismatch"]
    put(tmp_path, ".controlcoding/features/features.json", {"schemaVersion": "cc-feature-state/v1", "wipLimit": 1, "features": [{"id": "feature-1", "state": "passing"}]})
    binding = adapters.feature_binding(tmp_path, ["feature-1"])
    assert binding["state"] == "allowed" and binding["gateEvidence"] is False


def monitor_fixture():
    config = {"deployedDigest": "d" * 64, "window": "window-1", "metrics": [{"id": "latency", "baseline": 100, "maxDelta": 20, "minimumSamples": 3}]}
    observation = {"deployedDigest": "d" * 64, "window": "window-1", "metrics": {"latency": {"value": 121, "samples": 3, "citation": "metrics:window-1"}}}
    return config, observation


def test_monitor_incident_dedup_does_not_hide_breach():
    config, observation = monitor_fixture()
    first = adapters.monitor_metrics(config, observation)
    assert first["state"] == "blocked" and len(first["proposals"]) == 1
    incident = first["proposals"][0]
    assert incident["deployedDigest"] == config["deployedDigest"] and not incident["accepted"]
    second = adapters.monitor_metrics(config, observation, [incident["id"]])
    assert second["state"] == "blocked" and not second["proposals"]
    observation["metrics"]["latency"]["value"] = 119
    assert adapters.monitor_metrics(config, observation)["state"] == "allowed"


@pytest.mark.parametrize("mutation", ["missing", "nan", "baseline", "few", "release", "window", "citation"])
def test_monitor_unknown_for_invalid_or_absent_observation(mutation):
    config, observation = monitor_fixture()
    if mutation == "missing": observation["metrics"] = {}
    elif mutation == "nan": observation["metrics"]["latency"]["value"] = float("nan")
    elif mutation == "baseline": config["metrics"][0]["baseline"] = "100"
    elif mutation == "few": observation["metrics"]["latency"]["samples"] = 2
    elif mutation == "release": observation["deployedDigest"] = "f" * 64
    elif mutation == "window": observation["window"] = "old"
    else: observation["metrics"]["latency"]["citation"] = ""
    assert adapters.monitor_metrics(config, observation)["state"] == "unknown"


def job_fixture(root, change, kind="agent_eval", output_change=None):
    configuration = {"implementationActor": change["candidate"]["actor"], "graderActor": "independent-grader",
                     "configDigest": "", "taskDigest": "", "graderDigest": "", "requiredCases": ["case-1", "case-2"]}
    if kind == "agent_eval":
        for field, path in (("configDigest", "agent-config.json"), ("taskDigest", "agent-task.json"), ("graderDigest", "agent-grader.json")):
            put(root, path, {"identity": path})
            configuration[field] = hashlib.sha256((root / path).read_bytes()).hexdigest()
        observation = {**{k: configuration[k] for k in ("configDigest", "taskDigest", "graderDigest", "graderActor")},
                       "candidateDigest": change["candidate"]["digest"], "cases": [{"id": "case-1", "passed": True}, {"id": "case-2", "passed": True}]}
    else:
        configuration = {"environment": "fixture", "artifactDigest": "d" * 64}
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        observation = {"candidateDigest": change["candidate"]["digest"], "base": head, "head": head,
                       "environment": "fixture", "deployedDigest": "d" * 64, "status": "deployed"}
    if output_change:
        observation.update(output_change)
    put(root, "observed.json", observation)
    (root / "adapter.py").write_text("import pathlib\nprint(pathlib.Path('observed.json').read_text())\n", encoding="utf-8")
    paths = ["adapter.py", "observed.json"] + (["agent-config.json", "agent-task.json", "agent-grader.json"] if kind == "agent_eval" else [])
    spec = {"id": "adapter-1", "kind": kind, "argv": [sys.executable, "adapter.py"], "timeoutSeconds": 5,
            "outputLimitBytes": 8192, "files": [{"path": p, "sha256": hashlib.sha256((root / p).read_bytes()).hexdigest()} for p in paths],
            "configuration": configuration}
    put(root, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    return {"kind": kind, "adapter_id": "adapter-1"}, spec


def evaluation_v2_fixture(root, change, mode="pass"):
    worker = """import json, pathlib, sys
request = json.loads(pathlib.Path(sys.argv[1]).read_text())
pathlib.Path('actual.txt').write_text('correct' if MODE == 'pass' else 'incorrect')
print(json.dumps({'model': request['model'], 'usage': {'costMicros': 1, 'inputTokens': 2, 'outputTokens': 3}}))
""".replace("MODE", repr(mode))
    (root / "worker.py").write_text(worker, encoding="utf-8")
    extras = []
    for kind in ("prompt", "skill", "hook", "model"):
        path = kind + ".txt"
        (root / path).write_text("pinned " + kind, encoding="utf-8")
        extras.append({"kind": kind, "path": path, "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()})
    model = {"provider": "local-fixture", "id": "fixture-worker", "revision": "1"}
    put(root, "evaluation-config.json", {"schemaVersion": 1, "model": model, "inputs": extras,
        "budgets": {"totalTimeoutSeconds": 10, "caseTimeoutSeconds": 3, "maxCostMicros": 10,
                    "maxInputTokens": 10, "maxOutputTokens": 10}})
    put(root, "evaluation-suite.json", {"schemaVersion": 1, "id": "fixture-suite", "version": "1", "cases": [
        {"id": case, "prompt": "Write the requested value", "files": [],
         "checks": [{"kind": "file_sha256", "path": "actual.txt", "sha256": hashlib.sha256(b"correct").hexdigest()}]}
        for case in ("case-1", "case-2")]})
    paths = ["worker.py", "evaluation-config.json", "evaluation-suite.json"] + [p["path"] for p in extras]
    spec = {"id": "evaluation-2", "kind": "agent_eval", "argv": [sys.executable, "worker.py", "{evalRequest}"],
        "timeoutSeconds": 10, "outputLimitBytes": 8192,
        "files": [{"path": path, "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()} for path in paths],
        "configuration": {"evaluationVersion": 2, "implementationActor": change["candidate"]["actor"],
                          "graderActor": "independent-owner", "suitePath": "evaluation-suite.json", "configurationPath": "evaluation-config.json"}}
    put(root, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    return {"kind": "agent_eval", "adapter_id": "evaluation-2"}, spec


def test_v2_real_cases_independent_qualification_and_replay(tmp_path):
    change = change_fixture(tmp_path)
    value, spec = evaluation_v2_fixture(tmp_path, change)
    plan = jobs.prepare(tmp_path, value, change)
    assert plan["evaluationPlan"]["requiredCases"] == ["case-1", "case-2"]
    assert "cc_development_eval.py" in plan["enginePins"]
    result = jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert result["state"] == "allowed", result
    assessment = jobs.assess_job_observation(tmp_path, result, change, "agent_eval")
    assert assessment["qualification"] is True, assessment
    assert assessment["configurationDigest"] == plan["evaluationPlan"]["configDigest"]
    assert assessment["observation"]["usageSource"] == "adapter-reported"
    assert len(assessment["cases"]) == 2
    assert jobs.execute(tmp_path, value, change, plan["approval_id"])["replayed"] is True


def test_v2_failed_outputs_retained_cannot_self_report_success(tmp_path):
    change = change_fixture(tmp_path)
    value, spec = evaluation_v2_fixture(tmp_path, change, "wrong")
    plan = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert result["state"] == "blocked"
    assert len(result["observation"]["cases"]) == 2
    assessment = jobs.assess_job_observation(tmp_path, result, change, "agent_eval")
    assert assessment["state"] == "blocked" and assessment["qualification"] is False
    assert assessment["observation"] is not None
    assert all(not check["passed"] for row in assessment["cases"] for check in row["checks"])


@pytest.mark.parametrize("path", ["prompt.txt", "skill.txt", "hook.txt", "model.txt", "worker.py", "evaluation-config.json", "evaluation-suite.json"])
def test_v2_current_qualification_invalidated_by_each_configuration_input(tmp_path, path):
    change = change_fixture(tmp_path)
    value, spec = evaluation_v2_fixture(tmp_path, change)
    plan = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert result["state"] == "allowed", result
    with (tmp_path / path).open("a", encoding="utf-8") as stream:
        stream.write("\nchanged")
    assessed = jobs.assess_job_observation(tmp_path, result, change, "agent_eval")
    assert assessed["state"] == "unknown" and not assessed.get("qualification", False)


def test_v2_retained_pass_bits_do_not_replace_actual_case_outputs(tmp_path):
    change = change_fixture(tmp_path)
    value, spec = evaluation_v2_fixture(tmp_path, change)
    plan = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert result["state"] == "allowed", result
    (tmp_path / result["observation"]["workspace"] / "case-1" / "actual.txt").write_text("wrong", encoding="utf-8")
    assessed = jobs.assess_job_observation(tmp_path, result, change, "agent_eval")
    assert not assessed.get("qualification", False), assessed


@pytest.mark.parametrize("mode", ["timeout", "overbudget", "claimed_pass", "wrong_model", "malformed"])
def test_v2_unqualified_attempt_retains_case_failures_and_replays_once(tmp_path, mode):
    change = change_fixture(tmp_path)
    value, spec = evaluation_v2_fixture(tmp_path, change)
    worker = (tmp_path / "worker.py").read_text()
    if mode == "timeout":
        worker = "import time\ntime.sleep(3)\n"
        config = json.loads((tmp_path / "evaluation-config.json").read_text())
        config["budgets"]["caseTimeoutSeconds"] = 1
        put(tmp_path, "evaluation-config.json", config)
    elif mode == "overbudget":
        worker = worker.replace("'costMicros': 1", "'costMicros': 100")
    elif mode == "claimed_pass":
        worker = worker.replace("'usage': {", "'passed': True, 'cases': [{'id': 'case-1', 'passed': True}], 'usage': {")
    elif mode == "wrong_model":
        worker = worker.replace("request['model']", "{'provider':'other','id':'other','revision':'other'}")
    else:
        worker = "print('not JSON')\n"
    (tmp_path / "worker.py").write_text(worker, encoding="utf-8")
    for pin in spec["files"]:
        pin["sha256"] = hashlib.sha256((tmp_path / pin["path"]).read_bytes()).hexdigest()
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    plan = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert result["state"] == "blocked", result
    assessed = jobs.assess_job_observation(tmp_path, result, change, "agent_eval")
    assert assessed["state"] == "blocked" and assessed["qualification"] is False, assessed
    assert len(assessed["cases"]) == 2 and assessed["observation"] is not None
    assert any(row["status"] != "passed" for row in assessed["cases"])
    replay = jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert replay["replayed"] is True and replay["observation"] == result["observation"]


def test_v2_latest_failed_stable_evaluation_id_supersedes_previous_pass(tmp_path):
    from cc_development_lib.policy import job_assessments, automation_qualification
    change = change_fixture(tmp_path)
    change.update(releases=[], observations=[], evidence=[])
    value, spec = evaluation_v2_fixture(tmp_path, change)
    plan = jobs.prepare(tmp_path, value, change)
    passed = jobs.execute(tmp_path, value, change, plan["approval_id"])
    change["evidence"].append({"candidate_identity": change["candidate"]["digest"],
                              "records": [{"id": "stable-evaluation", **passed}]})
    selection = {"mode": "automated", "evaluation_id": "stable-evaluation",
                 "configuration_digest": plan["evaluationPlan"]["configDigest"]}
    assert automation_qualification(tmp_path, change, selection)["state"] == "allowed"
    value, spec = evaluation_v2_fixture(tmp_path, change, "wrong")
    spec["configuration"]["attemptToken"] = "new-reviewed-attempt"
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    preview = jobs.prepare(tmp_path, value, change)
    failed = jobs.execute(tmp_path, value, change, preview["approval_id"])
    change["evidence"].append({"candidate_identity": change["candidate"]["digest"],
                              "records": [{"id": "stable-evaluation", **failed}]})
    assessments = job_assessments(tmp_path, change)["agent_evaluations"]
    assert not assessments[0]["current"] and not assessments[0]["qualification"]
    assert assessments[1]["current"] and not assessments[1]["qualification"]
    assert assessments[1]["observation"]["cases"]
    assert automation_qualification(tmp_path, change, selection)["state"] == "blocked"


def test_actual_bounded_eval_command_and_canonical_receipt(tmp_path):
    change = change_fixture(tmp_path)
    value, spec = job_fixture(tmp_path, change)
    plan = jobs.prepare(tmp_path, value, change)
    assert not (tmp_path / jobs.RECEIPTS).exists()
    result = jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert result["state"] == "allowed", result
    assessed = jobs.assess_job_observation(tmp_path, result, change, "agent_eval")
    assert assessed["state"] == "allowed", assessed
    assert assessed["qualification"] is False
    assert assessed["qualificationReasons"] == ["legacy_eval_not_qualification"]
    assert adapters.assess_agent_eval(change, spec["configuration"], assessed["observation"])["state"] == "allowed"
    forged = {**result, "observation": {"passed": True}}
    assert jobs.assess_job_observation(tmp_path, forged, change, "agent_eval")["state"] == "unknown"
    assert jobs.execute(tmp_path, value, change, plan["approval_id"])["replayed"] is True


@pytest.mark.parametrize("mode", ["timeout", "output_limit", "non_json"])
def test_bounded_failures_retain_once_and_require_new_attempt(tmp_path, mode):
    change = change_fixture(tmp_path)
    value, spec = job_fixture(tmp_path, change)
    script = {"timeout": "import time\ntime.sleep(3)\n", "output_limit": "print('x' * 10000)\n", "non_json": "print('not JSON')\n"}[mode]
    (tmp_path / "adapter.py").write_text(script, encoding="utf-8")
    spec["files"][0]["sha256"] = hashlib.sha256((tmp_path / "adapter.py").read_bytes()).hexdigest()
    spec["timeoutSeconds"] = 1
    spec["outputLimitBytes"] = 1024
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert result["state"] == "blocked"
    repeated = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert repeated["replayed"] and repeated["receiptId"] == result["receiptId"]
    retained = json.loads((tmp_path / result["receiptPath"]).read_text())
    assert retained["command"]["error"] == (mode if mode != "non_json" else None)
    retained["state"] = "running"
    jobs._seal(retained)
    put(tmp_path, result["receiptPath"], retained)
    with pytest.raises(inputs.EvidenceError, match="job_attempt_recovery_required"):
        jobs.execute(tmp_path, value, change, preview["approval_id"])
    spec["configuration"]["attemptToken"] = "explicit-attempt-2"
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    next_preview = jobs.prepare(tmp_path, value, change)
    assert next_preview["approval_id"] != preview["approval_id"]


@pytest.mark.parametrize("mutation", ["script", "manifest", "preview", "shell", "inline", "unapproved", "grader", "pin", "bounds"])
def test_jobs_reject_stale_unbounded_unapproved_invocations(tmp_path, mutation):
    change = change_fixture(tmp_path)
    value, spec = job_fixture(tmp_path, change)
    plan = jobs.prepare(tmp_path, value, change)
    if mutation == "script": (tmp_path / "adapter.py").write_text("pass\n", encoding="utf-8")
    elif mutation == "manifest": spec["timeoutSeconds"] = 6
    elif mutation == "preview": plan["approval_id"] = "0" * 64
    elif mutation == "shell": spec["argv"] = ["cmd.exe", "/c", "echo passed"]
    elif mutation == "inline": spec["argv"] = [sys.executable, "-c", "print('passed')"]
    elif mutation == "unapproved": change["decisions"][-1]["decision"] = "rejected"
    elif mutation == "grader": spec["configuration"]["graderActor"] = change["candidate"]["actor"]
    elif mutation == "pin": spec["configuration"]["taskDigest"] = "0" * 64
    else: spec["timeoutSeconds"] = 999
    if mutation not in ("script", "preview", "unapproved"):
        put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    with pytest.raises(inputs.EvidenceError):
        jobs.execute(tmp_path, value, change, plan["approval_id"])
    assert not (tmp_path / jobs.RECEIPTS).exists()


@pytest.mark.parametrize("change_output", [{"candidateDigest": "0" * 64}, {"cases": [{"id": "case-1", "passed": True}]}, {"cases": [{"id": "case-1", "passed": True}, {"id": "case-2", "passed": False}]}])
def test_actual_eval_failure_retained_cannot_promote(tmp_path, change_output):
    change = change_fixture(tmp_path)
    value, _ = job_fixture(tmp_path, change, output_change=change_output)
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert result["state"] == "blocked"
    assert (tmp_path / result["receiptPath"]).exists()
    assert jobs.assess_job_observation(tmp_path, result, change, "agent_eval")["state"] == "unknown"


def init_git(root):
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "-c", "core.autocrlf=false", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "core.hooksPath=", "commit", "--quiet", "-m", "Fixture"], cwd=root, check=True)


def test_release_actual_fixture_output_exact_git_binding(tmp_path):
    change = change_fixture(tmp_path)
    init_git(tmp_path)
    value, _ = job_fixture(tmp_path, change, "release_status")
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    action = {"observation": result, "environment": "fixture", "artifact_digest": "d" * 64}
    assessment = adapters.assess_release(tmp_path, change, action)
    assert assessment["state"] == "allowed", assessment
    assert assessment["localGit"]["hostedStatus"] == "unknown"
    action["observation"] = {"provenance": "observed_command", "observation": result["observation"]}
    assert adapters.assess_release(tmp_path, change, action)["state"] == "unknown"


def test_release_rejects_dirty_candidate_even_if_local_head_exists(tmp_path):
    change = change_fixture(tmp_path)
    init_git(tmp_path)
    (tmp_path / "app.py").write_text("print('changed')\n", encoding="utf-8")
    raw = (tmp_path / "app.py").read_bytes()
    change["candidate"]["files"][0].update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    change["candidate"]["digest"] = candidate_identity(change, change["candidate"]["files"])
    value, _ = job_fixture(tmp_path, change, "release_status")
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert adapters.assess_release(tmp_path, change, {"observation": result, "environment": "fixture", "artifact_digest": "d" * 64})["state"] == "unknown"


def test_deploy_job_requires_candidate_bound_accepted_release_gate(tmp_path):
    change = change_fixture(tmp_path)
    init_git(tmp_path)
    value, _ = job_fixture(tmp_path, change, "deploy")
    with pytest.raises(inputs.EvidenceError, match="release_gate_not_accepted"):
        jobs.prepare(tmp_path, value, change)


def test_rollback_rehearsal_requires_current_verified_independent_scope(tmp_path):
    change = change_fixture(tmp_path)
    init_git(tmp_path)
    verification = {"revision": "verified-1", "digest": "f" * 64, "content": {}, "upstream": upstream(change, "verify")}
    change["artifacts"]["verify"] = [verification]
    change["decisions"].append({"stage": "verify", "decision": "accepted", "artifact_revision": verification["revision"],
                                "upstream": verification["upstream"], "candidate_identity": change["candidate"]["digest"], "actor": "verifier"})
    change["gates"] = {"verify": {"outcome": "allowed"}}
    change["reviews"] = [{"candidate_identity": change["candidate"]["digest"], "actor": "reviewer", "findings": []}]
    change["artifacts"]["review_release"] = [{"content": {"environment": "fixture"}}]
    value, spec = job_fixture(tmp_path, change, "rollback", {"mode": "rehearsal", "status": "rolled_back", "restoredDigest": "b" * 64})
    spec["configuration"].update(mode="rehearsal", knownArtifactDigest="b" * 64)
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assessed = jobs.assess_job_observation(tmp_path, result, change, "rollback")
    assert assessed["state"] == "allowed", assessed
    assert assessed["observation"]["restoredDigest"] == "b" * 64
    release = adapters.assess_release(tmp_path, change, {"observation": result, "environment": "fixture", "artifact_digest": "d" * 64})
    assert release["state"] == "unknown" and "release_rehearsal_not_deployment" in release["reasons"]
    change["reviews"][0]["actor"] = change["candidate"]["actor"]
    with pytest.raises(inputs.EvidenceError, match="rehearsal_independent_review_missing"):
        jobs.prepare(tmp_path, value, change)


def test_copied_job_receipt_cannot_observe_another_project(tmp_path):
    change = change_fixture(tmp_path)
    value, _ = job_fixture(tmp_path, change)
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    import shutil
    copied = tmp_path / "another-project"
    copied.mkdir()
    for path in ("app.py", "adapter.py", "observed.json", "agent-config.json", "agent-task.json", "agent-grader.json", jobs.MANIFEST, result["receiptPath"]):
        target = copied / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_path / path, target)
    assert jobs.assess_job_observation(copied, result, change, "agent_eval")["reasons"] == ["job_wrong_project"]


def test_actual_monitor_observation_cites_deployed_release_and_deduplicates(tmp_path):
    change = change_fixture(tmp_path)
    init_git(tmp_path)
    release_value, release_spec = job_fixture(tmp_path, change, "release_status")
    release_spec["argv"][1] = "release-adapter.py"
    (tmp_path / "release-adapter.py").write_text("import pathlib\nprint(pathlib.Path('release-output.json').read_text())\n", encoding="utf-8")
    (tmp_path / "release-output.json").write_bytes((tmp_path / "observed.json").read_bytes())
    release_spec["files"] = [{"path": path, "sha256": hashlib.sha256((tmp_path / path).read_bytes()).hexdigest()}
                             for path in ("release-adapter.py", "release-output.json")]
    configuration, observation = monitor_fixture()
    observation["candidateDigest"] = change["candidate"]["digest"]
    put(tmp_path, "monitor-output.json", observation)
    (tmp_path / "monitor-adapter.py").write_text("import pathlib\nprint(pathlib.Path('monitor-output.json').read_text())\n", encoding="utf-8")
    monitor_spec = {"id": "monitor-1", "kind": "monitor", "argv": [sys.executable, "monitor-adapter.py"], "timeoutSeconds": 5,
                    "outputLimitBytes": 8192, "configuration": configuration,
                    "files": [{"path": path, "sha256": hashlib.sha256((tmp_path / path).read_bytes()).hexdigest()}
                              for path in ("monitor-adapter.py", "monitor-output.json")]}
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [release_spec, monitor_spec]})
    preview = jobs.prepare(tmp_path, release_value, change)
    released = jobs.execute(tmp_path, release_value, change, preview["approval_id"])
    change["releases"] = [{"environment": "fixture", "artifact_digest": "d" * 64, "status": "observed", "observation": released}]
    monitor_value = {"kind": "monitor", "adapter_id": "monitor-1"}
    preview = jobs.prepare(tmp_path, monitor_value, change)
    monitored = jobs.execute(tmp_path, monitor_value, change, preview["approval_id"])
    assessment = adapters.assess_monitor(tmp_path, change, monitored)
    assert assessment["state"] == "blocked" and len(assessment["proposals"]) == 1, assessment
    duplicate = adapters.assess_monitor(tmp_path, change, monitored, [assessment["proposals"][0]["id"]])
    assert duplicate["state"] == "blocked" and not duplicate["proposals"]
    change["releases"][0]["status"] = "self_reported"
    assert adapters.assess_monitor(tmp_path, change, monitored)["state"] == "unknown"


def test_owner_binding_reference_requires_current_reviewed_source(tmp_path):
    change = change_fixture(tmp_path)
    registry = {"schemaVersion": "cc-feature-state/v1", "wipLimit": 1, "features": [{"id": "feature-1", "state": "passing"}]}
    put(tmp_path, ".controlcoding/features/features.json", registry)
    binding = adapters.feature_binding(tmp_path, ["feature-1"])
    change["artifacts"]["design"][-1]["content"]["source_references"] = [
        {"owner": "features", "featureIds": ["feature-1"], "sourceDigest": binding["sourceDigest"]}]
    assert adapters.assess_owner_bindings(tmp_path, change)["state"] == "allowed"
    registry["features"][0]["state"] = "active"
    put(tmp_path, ".controlcoding/features/features.json", registry)
    assert adapters.assess_owner_bindings(tmp_path, change)["reasons"] == ["feature_source_changed"]


def test_concurrent_canonical_control_mutation_invalidates_observed_job(tmp_path):
    change = change_fixture(tmp_path)
    value, spec = job_fixture(tmp_path, change)
    (tmp_path / "adapter.py").write_text(
        "import pathlib\npathlib.Path('.controlcoding/development.json').write_text('{}')\n"
        "print(pathlib.Path('observed.json').read_text())\n", encoding="utf-8")
    spec["files"][0]["sha256"] = hashlib.sha256((tmp_path / "adapter.py").read_bytes()).hexdigest()
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert result["state"] == "blocked" and "adapter_inputs_changed_during_run" in result["reasons"]
    assert jobs.assess_job_observation(tmp_path, result, change, "agent_eval")["state"] == "unknown"
