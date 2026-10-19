"""Representative tasks use real workers and independently retained output."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_development_eval as engine
from cc_evidence_inputs import EvidenceError


def fixture(tmp_path, body="Path('answer.txt').write_text('correct')", *, cases=1):
    model = {"provider": "fixture", "id": "worker", "revision": "1"}
    worker = "import json,sys,time\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[1]).read_text())\nassert 'checks' not in r\n" + body + "\nprint(json.dumps({'model':r['model'],'usage':{'costMicros':1,'inputTokens':2,'outputTokens':3}}))\n"
    suite = {"schemaVersion": 1, "id": "tasks", "version": "1", "cases": [
        {"id": f"case{i}", "prompt": "produce answer", "files": [{"path": "input.txt", "content": "given"}],
         "checks": [{"kind": "file_sha256", "path": "answer.txt", "sha256": hashlib.sha256(b"correct").hexdigest()},
                    {"kind": "file_contains", "path": "answer.txt", "text": "correct"},
                    {"kind": "file_absent", "path": "bad.txt"}]} for i in range(cases)]}
    config = {"schemaVersion": 1, "model": model, "inputs": [], "budgets": {
        "totalTimeoutSeconds": 5, "caseTimeoutSeconds": 2, "maxCostMicros": 10, "maxInputTokens": 10, "maxOutputTokens": 10}}
    for name, value in (("suite.json", suite), ("config.json", config)):
        (tmp_path / name).write_text(json.dumps(value))
    (tmp_path / "worker.py").write_text(worker)
    pins = [{"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in tmp_path.iterdir()]
    spec = {"files": pins, "argv": [sys.executable, "-B", "worker.py", "{evalRequest}"], "configuration": {
        "evaluationVersion": 2, "implementationActor": "implementer", "graderActor": "reviewer", "suitePath": "suite.json", "configurationPath": "config.json"}}
    plan = engine.prepare(tmp_path, spec, {"candidate": {"actor": "implementer"}})
    return spec, plan


def test_real_worker_independent_checks_and_retained_tamper(tmp_path):
    spec, plan = fixture(tmp_path, cases=2)
    result = engine.execute(tmp_path, plan, spec["argv"], receipt_id="one")
    assert result["status"] == "passed"
    assert result["usage"] == {"costMicros": 2, "inputTokens": 4, "outputTokens": 6}
    assert engine.assess(plan, result, root=tmp_path)["state"] == "allowed"
    (tmp_path / ".controlcoding/development_eval_runs/one/case0/answer.txt").write_text("wrong")
    assert engine.assess(plan, result, root=tmp_path)["state"] == "blocked"


def test_worker_success_cannot_grant_expected_success(tmp_path):
    spec, plan = fixture(tmp_path, "Path('answer.txt').write_text('wrong')", cases=2)
    result = engine.execute(tmp_path, plan, spec["argv"], receipt_id="fail")
    assert result["status"] == "failed"
    assert all(r["status"] == "failed" for r in result["cases"])
    assert "eval_independent_check_failed" in result["cases"][0]["reasons"]
    assert engine.assess(plan, result, root=tmp_path)["state"] == "blocked"


@pytest.mark.parametrize("body,reason", [
    ("time.sleep(4)", "eval_worker_timeout"),
    ("print('x'*5000)", "eval_worker_output_limit"),
    ("r['model']['revision']='wrong'", "eval_worker_model_invalid"),
    ("raise SystemExit(3)", "eval_worker_failed"),
    ("print('{}')", "eval_worker_invalid"),
])
def test_real_worker_failure_retains_unrun_case(tmp_path, body, reason):
    spec, plan = fixture(tmp_path, body, cases=2)
    result = engine.execute(tmp_path, plan, spec["argv"], receipt_id="fail", output_limit=1024)
    assert reason in result["cases"][0]["reasons"]
    assert result["cases"][1]["status"] == "not_run"


def test_mutated_pinned_input_blocks(tmp_path):
    spec, plan = fixture(tmp_path)
    (tmp_path / "suite.json").write_text("{}")
    with pytest.raises(EvidenceError, match="eval_input_changed"):
        engine.execute(tmp_path, plan, spec["argv"], receipt_id="no")
    assert not (tmp_path / "runs").exists()


def test_real_worker_mutation_of_grader_definition_blocks(tmp_path):
    spec, plan = fixture(tmp_path, f"Path({str(tmp_path / 'suite.json')!r}).write_text('{{}}')")
    result = engine.execute(tmp_path, plan, spec["argv"], receipt_id="mutation")
    assert "eval_input_changed" in result["cases"][0]["reasons"]


def test_cumulative_budget_stops_required_cases(tmp_path):
    spec, plan = fixture(tmp_path, cases=4)
    result = engine.execute(tmp_path, plan, spec["argv"], receipt_id="budget")
    assert "eval_usage_budget_exceeded" in result["cases"][3]["reasons"]
    assert result["usage"]["outputTokens"] == 12


@pytest.mark.parametrize("field,value", [("maxCostMicros", True), ("caseTimeoutSeconds", 0)])
def test_strict_budget_rejects_bool_and_zero_timeout(tmp_path, field, value):
    spec, _ = fixture(tmp_path)
    config = json.loads((tmp_path / "config.json").read_text())
    config["budgets"][field] = value
    (tmp_path / "config.json").write_text(json.dumps(config))
    next(p for p in spec["files"] if p["path"] == "config.json")["sha256"] = hashlib.sha256((tmp_path / "config.json").read_bytes()).hexdigest()
    with pytest.raises(EvidenceError, match="eval_budget_invalid"):
        engine.prepare(tmp_path, spec, {"candidate": {"actor": "implementer"}})


@pytest.mark.parametrize("revision", ["unknown", "latest", "auto", "default"])
def test_ambiguous_model_revision_rejected(tmp_path, revision):
    spec, _ = fixture(tmp_path)
    configuration = json.loads((tmp_path / "config.json").read_text())
    configuration["model"]["revision"] = revision
    (tmp_path / "config.json").write_text(json.dumps(configuration))
    next(p for p in spec["files"] if p["path"] == "config.json")["sha256"] = hashlib.sha256((tmp_path / "config.json").read_bytes()).hexdigest()
    with pytest.raises(EvidenceError, match="eval_model_unpinned"):
        engine.prepare(tmp_path, spec, {"candidate": {"actor": "implementer"}})


def test_workspace_is_managed_and_receipt_id_required(tmp_path):
    spec, plan = fixture(tmp_path)
    with pytest.raises(EvidenceError, match="eval_receipt_id_invalid"):
        engine.execute(tmp_path, plan, spec["argv"])
    with pytest.raises(EvidenceError, match="eval_workspace_invalid"):
        engine.execute(tmp_path, plan, spec["argv"], receipt_id="case", workspace_relative="source")
    assert not (tmp_path / "source").exists()


def test_retained_duration_budget_is_assessed(tmp_path):
    spec, plan = fixture(tmp_path)
    result = engine.execute(tmp_path, plan, spec["argv"], receipt_id="duration")
    result["durationMs"] = 5001
    assert "eval_total_timeout" in engine.assess(plan, result, root=tmp_path)["reasons"]
