"""Real local release jobs and offline hosted-observation integration."""
import copy
import contextlib
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_development_jobs as jobs
import cc_development_lib as development
import cc_development_release as release
import cc_development_github as github
import cc_evidence_inputs as inputs
from test_cc_development_core import ready_for_release, decide, act
from test_cc_development_adapters import put
from test_cc_development_release import fixture as pr_fixture


def deployment(root, body=None, timeout=5):
    candidate = ready_for_release(root)
    decide(root, "review_release", "ReleaseOwner")
    script = "import json,sys\nfrom pathlib import Path\n" + (body or "assert Path('.controlcoding/development.lock/owner.json').exists()\nPath('deployed.marker').write_text('effect')\n")
    script += "print(json.dumps({'candidateDigest':sys.argv[1],'environment':'local','deployedDigest':sys.argv[2],'status':'deployed'}))\n"
    (root / "dist").mkdir()
    script = script.replace("'deployed.marker'", "'dist/deployed.marker'")
    (root / "deploy.py").write_text(script)
    spec = {"id": "deploy", "kind": "deploy", "argv": [sys.executable, "-B", "deploy.py", "{candidateDigest}", "{artifactDigest}"],
            "timeoutSeconds": timeout, "outputLimitBytes": 8192,
            "files": [{"path": "deploy.py", "sha256": hashlib.sha256((root / "deploy.py").read_bytes()).hexdigest()}],
            "configuration": {"environment": "local", "artifactDigest": candidate}}
    put(root, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    import cc
    for run in (cc.cmd_verify_run, cc.cmd_invariants_run):
        with contextlib.redirect_stdout(io.StringIO()):
            assert run(root, json_output=True) == 0
    return development.read(root)["changes"][0], {"kind": "deploy", "adapter_id": "deploy"}, spec


def refresh_checks(root):
    import cc
    for run in (cc.cmd_verify_run, cc.cmd_invariants_run):
        with contextlib.redirect_stdout(io.StringIO()):
            assert run(root, json_output=True) == 0


def test_actual_deploy_holds_core_lock_and_preserves_exact_target(tmp_path):
    change, value, spec = deployment(tmp_path)
    before = (tmp_path / ".controlcoding/development.json").read_bytes()
    preview = jobs.prepare(tmp_path, value, change)
    assert not (tmp_path / "dist/deployed.marker").exists()
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert result["state"] == "allowed", result
    assert (tmp_path / "dist/deployed.marker").read_text() == "effect"
    assert (tmp_path / ".controlcoding/development.json").read_bytes() == before
    assert not (tmp_path / ".controlcoding/development.lock").exists()
    assert jobs.assess_job_observation(tmp_path, result, change, "deploy")["state"] == "allowed"
    assert jobs.execute(tmp_path, value, change, preview["approval_id"])["replayed"]


def test_changed_target_blocks_before_effect_marker(tmp_path):
    change, value, spec = deployment(tmp_path)
    preview = jobs.prepare(tmp_path, value, change)
    spec["configuration"]["artifactDigest"] = "f" * 64
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    with pytest.raises(inputs.EvidenceError, match="release_artifact_unapproved"):
        jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert not (tmp_path / "dist/deployed.marker").exists()


def test_current_core_rejection_overrules_previously_approved_caller_view(tmp_path):
    change, value, _ = deployment(tmp_path)
    preview = jobs.prepare(tmp_path, value, change)
    act(tmp_path, {"kind": "decide", "change_id": "C1", "stage": "review_release", "decision": "rejected", "actor": "ReleaseOwner", "reason": "Reject before deployment"})
    with pytest.raises(inputs.EvidenceError, match="release_gate_not_accepted"):
        jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert not (tmp_path / "dist/deployed.marker").exists()


def test_wrong_observed_artifact_cannot_establish_deployment(tmp_path):
    change, value, spec = deployment(tmp_path, "Path('deployed.marker').write_text('effect')\nsys.argv[2]='f'*64\n")
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert result["state"] == "blocked" and result["observation"] is None
    assert jobs.assess_job_observation(tmp_path, result, change, "deploy")["state"] != "allowed"


def test_timeout_effect_requires_later_explicit_status_reconciliation(tmp_path):
    change, value, spec = deployment(tmp_path, "import time\nPath('deployed.marker').write_text('unknown effect')\ntime.sleep(2)\n", timeout=1)
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert result["state"] == "blocked"
    spec["configuration"]["attemptToken"] = "retry2"
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    with pytest.raises(inputs.EvidenceError, match="reconciliation_required"):
        jobs.prepare(tmp_path, value, change)


def test_later_actual_status_allows_only_separately_reviewed_retry(tmp_path):
    change, value, spec = deployment(tmp_path, "import time\nPath('deployed.marker').write_text('unknown effect')\ntime.sleep(2)\n", timeout=1)
    preview = jobs.prepare(tmp_path, value, change)
    unknown = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert unknown["state"] == "blocked"
    # A reviewed adapter amendment fixes the local fixture's deliberate timeout.
    (tmp_path / "deploy.py").write_text("import json,sys\nfrom pathlib import Path\nassert Path('.controlcoding/development.lock/owner.json').exists()\nPath('dist/deployed.marker').write_text('effect')\nprint(json.dumps({'candidateDigest':sys.argv[1],'environment':'local','deployedDigest':sys.argv[2],'status':'deployed'}))\n")
    spec["files"][0]["sha256"] = hashlib.sha256((tmp_path / "deploy.py").read_bytes()).hexdigest()
    spec["configuration"]["attemptToken"] = "retry-reviewed"
    (tmp_path / "status.py").write_text("import json,sys\nfrom pathlib import Path\nassert Path('dist/deployed.marker').read_text()=='unknown effect'\nprint(json.dumps({'candidateDigest':sys.argv[1],'environment':'local','deployedDigest':sys.argv[2],'status':'not_deployed'}))\n")
    status_spec = {"id": "status", "kind": "release_status", "argv": [sys.executable, "-B", "status.py", "{candidateDigest}", "{artifactDigest}"],
                   "timeoutSeconds": 5, "outputLimitBytes": 8192, "files": [{"path": "status.py", "sha256": hashlib.sha256((tmp_path / "status.py").read_bytes()).hexdigest()}],
                   "configuration": {"environment": "local", "artifactDigest": change["candidate"]["digest"]}}
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec, status_spec]})
    refresh_checks(tmp_path)
    current = development.read(tmp_path)["changes"][0]
    status_value = {"kind": "release_status", "adapter_id": "status"}
    status_plan = jobs.prepare(tmp_path, status_value, current)
    status = jobs.execute(tmp_path, status_value, current, status_plan["approval_id"])
    assert status["state"] == "allowed", status
    retry = {**value, "status_receipt_id": status["receiptId"]}
    reviewed = jobs.prepare(tmp_path, retry, current)
    assert reviewed["statusReconciliation"]["attempts"] == [unknown["receiptId"]]
    assert (tmp_path / "dist/deployed.marker").read_text() == "unknown effect"
    result = jobs.execute(tmp_path, retry, current, reviewed["approval_id"])
    assert result["state"] == "allowed", result
    assert (tmp_path / "dist/deployed.marker").read_text() == "effect"
    assert jobs.assess_job_observation(tmp_path, status, current, "release_status")["state"] != "allowed"
    # A completed, retained reconciled retry covers precisely its prior unknown
    # attempt; the same approved retry replays without demanding fresh status.
    assert jobs.execute(tmp_path, retry, current, reviewed["approval_id"])["replayed"]


def test_trusted_github_copy_and_secondary_executable_are_bound(tmp_path):
    (tmp_path / "reader.py").write_bytes(Path(github.__file__).read_bytes())
    digest = hashlib.sha256((tmp_path / "reader.py").read_bytes()).hexdigest()
    executable = jobs._executable(sys.executable, tmp_path)
    spec = {"argv": [sys.executable, "-I", "-B", "reader.py", "{pullRequestRequest}", "--gh", executable["path"], "--gh-sha256", executable["sha256"]],
            "files": [{"path": "reader.py", "sha256": digest}], "configuration": {}}
    assert jobs._github_worker(tmp_path, spec) == executable
    (tmp_path / "reader.py").write_bytes(b"spoof")
    with pytest.raises(inputs.EvidenceError, match="untrusted"):
        jobs._github_worker(tmp_path, spec)


def test_native_auth_environment_is_only_host_profile(monkeypatch):
    for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_CONFIG_DIR", "PRIVATE_CANARY"):
        monkeypatch.setenv(key, key)
    trusted = jobs._environment(github=True)
    assert all(trusted.get(key) == key for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME"))
    assert not set(trusted) & {"GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_CONFIG_DIR", "PRIVATE_CANARY"}
    assert "USERPROFILE" not in jobs._environment()


@pytest.mark.parametrize("field,value", [("createdAt", "2027-01-01T00:00:00Z"), ("state", "allowed"), ("command", {"status": "passed"})])
def test_receipt_metadata_tamper_rejected(field, value):
    receipt = jobs._seal({"state": "running", "createdAt": "2026-10-02T00:00:00Z", "command": None,
                          "observation": None, "observationDigest": inputs.digest(None)})
    receipt[field] = value
    with pytest.raises(inputs.EvidenceError, match="integrity_changed"):
        jobs._validate_receipt(receipt)


def test_latest_failed_and_running_attempts_override_earlier_success(tmp_path):
    change, value, spec = deployment(tmp_path)
    preview = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    retained = json.loads((tmp_path / result["receiptPath"]).read_text())
    retained.update(id="development_job_" + "e" * 32, state="running", createdAt=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)).isoformat())
    jobs._seal(retained)
    put(tmp_path, jobs.RECEIPTS + "/" + retained["id"] + ".json", retained)
    assert jobs.assess_job_observation(tmp_path, result, change, "deploy")["state"] != "allowed"
    with pytest.raises(inputs.EvidenceError):
        jobs.execute(tmp_path, value, change, preview["approval_id"])


def test_pull_request_real_local_output_keeps_wire_exact(tmp_path, monkeypatch):
    change, observation_plan, observation = pr_fixture(tmp_path)
    canonical = development.read(tmp_path)
    assert canonical["changes"][0]["id"] == change["id"]
    assert not canonical["product"]["increments"]
    change["closed"] = False
    monkeypatch.setattr(jobs, "_canonical_change", lambda *a, **kw: copy.deepcopy(change))
    # The standalone reader's native transport is separately tested with local
    # fake CLI processes. This fixture substitutes transport only, then runs the
    # real controller bounded parser and semantic owner on its actual output.
    executable = jobs._executable(sys.executable, tmp_path)
    monkeypatch.setattr(jobs, "_github_worker", lambda *a: executable)
    put(tmp_path, "output.json", observation)
    (tmp_path / "reader.py").write_text("import pathlib\nprint(pathlib.Path('output.json').read_text())\n")
    pins = [{"path": p, "sha256": hashlib.sha256((tmp_path / p).read_bytes()).hexdigest()} for p in ("reader.py", "output.json")]
    spec = {"id": "pr", "kind": "pull_request", "argv": [sys.executable, "-B", "reader.py", "{pullRequestRequest}"],
            "timeoutSeconds": 5, "outputLimitBytes": 8192, "files": pins, "configuration": {}}
    put(tmp_path, jobs.MANIFEST, {"schemaVersion": 1, "adapters": [spec]})
    value = {"kind": "pull_request", "adapter_id": "pr"}
    preview = jobs.prepare(tmp_path, value, change)
    assert preview["pullRequestPlan"] == observation_plan
    result = jobs.execute(tmp_path, value, change, preview["approval_id"])
    assert result["state"] == "allowed", result
    assert "provenance" not in result["observation"]
    assert result["observation"] == observation
    assert jobs.assess_job_observation(tmp_path, result, change, "pull_request")["state"] == "allowed"
    assert jobs.execute(tmp_path, value, change, preview["approval_id"])["replayed"]
    # Selecting the first canonical snapshot changes the raw approval context.
    # During its refresh the old selected proof becomes superseded. A computed
    # gate change must not invalidate the refresh's own bound raw inputs.
    change["artifacts"]["review_release"] = [{"content": {"observation_provenance": {"mode": "github_pr", "receiptId": result["receiptId"]}}}]
    def canonical(*args, **kwargs):
        row = copy.deepcopy(change)
        selected = release.assessment(tmp_path, row)
        row["gates"] = {"review_release": {"outcome": selected["state"]}}
        return row
    monkeypatch.setattr(jobs, "_canonical_change", canonical)
    fresh = jobs.prepare(tmp_path, value, change)
    refreshed = jobs.execute(tmp_path, value, change, fresh["approval_id"])
    assert refreshed["state"] == "allowed", refreshed
    assert refreshed["receiptId"] != result["receiptId"]
    assert jobs.assess_job_observation(tmp_path, result, change, "pull_request")["state"] != "allowed"
