"""Required-suite selection and retained CI diagnostic evidence."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cc


@pytest.mark.parametrize("source", ["tracked", "generated"])
def test_default_selection_covers_remediation_surfaces(source):
    contract = (json.loads((ROOT / "controlcoding.verification.json").read_text(encoding="utf-8"))
                if source == "tracked" else cc._default_verification_contract(ROOT))
    selected, issues = cc._select_verification_suites(contract, [], [], False)
    assert not issues
    budgets = {s["id"]: s["timeoutSeconds"] for s in selected}
    assert budgets.pop("cli-regression") == 390
    assert budgets.pop("core-governance-regression") == 420
    assert set(budgets.values()) == {300}
    # pytest owns an external, short fixture tree; receipts stay in the project.
    assert all("--basetemp" not in suite["command"] for suite in selected)
    targets = {token for suite in selected for token in shlex.split(suite["command"])
               if token.startswith("tests/") and token.endswith(".py")}
    assert {
        "tests/test_cc_setup.py", "tests/test_cc_runtime.py", "tests/test_new_hooks.py",
        "tests/test_ci_configuration.py", "tests/test_cc_evidence.py", "tests/test_cc_evidence_process.py", "tests/test_session.py", "tests/test_cc_memory.py",
        "tests/test_cc_cli.py", "tests/test_gitignore.py", "tests/test_check_boundaries.py",
        "tests/test_cc_public_examples.py",
        "tests/test_check_dangerous_commands.py", "tests/test_check_repo_boundaries.py",
        "tests/test_check_file_organization.py", "tests/test_cc_organize.py",
    } <= targets


def test_generated_optional_targets_exist_and_tracked_suites_agree(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cc_setup.py").write_text("", encoding="utf-8")
    generated = cc._default_verification_contract(tmp_path)
    optional = {s["id"]: s for s in generated["suites"]
                if s["id"] in {"setup-runtime-regression", "optional-hooks-regression"}}
    assert set(optional) == {"setup-runtime-regression"}
    assert "tests/test_cc_setup.py" in optional["setup-runtime-regression"]["command"]
    assert "tests/test_cc_runtime.py" not in optional["setup-runtime-regression"]["command"]
    tracked = json.loads((ROOT / "controlcoding.verification.json").read_text(encoding="utf-8"))
    def checks(policy):
        return {s["id"]: (s["kind"], s["required"], s["command"], s.get("timeoutSeconds", 300))
                for s in policy["suites"] if s["required"]}
    assert checks(tracked) == checks(cc._default_verification_contract(ROOT))


def test_generated_catalog_target_requires_existing_file(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cc_cli.py").write_text("", encoding="utf-8")
    def support_command():
        return next((s["command"] for s in cc._default_verification_contract(tmp_path)["suites"]
                     if s["id"] == "verification-support-regression"), "")
    assert "tests/test_cc_public_examples.py" not in support_command()
    (tests / "test_cc_public_examples.py").write_text("", encoding="utf-8")
    assert "tests/test_cc_public_examples.py" in support_command()


def test_failed_pytest_junit_survives_verifier_temp_cleanup(tmp_path, capsys):
    target = tmp_path / "project with spaces"
    target.mkdir()
    tests = target / "tests"
    tests.mkdir()
    (tests / "test_cc_setup.py").write_text(
        "def test_failure():\n"
        "    print('FULL_WORKER_TRACE_START:' + 'diagnostic ' * 1500 + ':FULL_WORKER_TRACE_END')\n"
        "    assert False, 'intentional fixture failure'\n", encoding="utf-8")
    policy = cc._default_verification_contract(target)
    suite = next(s for s in policy["suites"] if s["id"] == "setup-runtime-regression")
    # Use the running interpreter; preserve the generated command's actual report options.
    suite["command"] = '"' + sys.executable.replace("\\", "/") + '"' + suite["command"][len("python"):]
    (target / "controlcoding.verification.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredKinds": ["regression"], "suites": [suite]}),
        encoding="utf-8")
    assert cc.cmd_verify_run(target, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed" and payload["receipt"]["suites"][0]["returnCode"] == 1
    assert not (target / payload["receipt"]["tempDir"]).exists()
    report = target / ".controlcoding/verification_receipts/setup-runtime-regression.xml"
    xml = ET.parse(report).getroot()
    case = next(xml.iter("testcase"))
    assert case.find("failure") is not None
    captured = "".join(xml.itertext())
    assert "FULL_WORKER_TRACE_START:" in captured and ":FULL_WORKER_TRACE_END" in captured
    assert "FULL_WORKER_TRACE_START:" not in payload["receipt"]["suites"][0]["stdoutTail"]


def test_ci_workflow_matrix_pins_and_explicit_failure_artifacts():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/controlcoding-verification.yml").read_text())
    job = workflow["jobs"]["verification"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    assert set(job["strategy"]["matrix"]["python-version"]) == {"3.11", "3.13"}
    assert job["strategy"]["fail-fast"] is False
    assert workflow["permissions"] == {"contents": "read"}
    steps = {step["name"]: step for step in job["steps"]}
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", step["uses"])
    assert steps["Check out repository with a spaced path"]["with"]["persist-credentials"] is False
    assert steps["Set up unsupported Python for rejection test"]["with"]["python-version"] == "3.10"
    assert "CC_TEST_PYTHON310" not in job["env"]
    assert steps["Run verification contract"]["env"]["CC_TEST_PYTHON310"] == "${{ steps.python310.outputs.python-path }}"
    verification_env = steps["Run verification contract"]["env"]
    assert verification_env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert verification_env["GIT_CONFIG_COUNT"] == "0"
    assert verification_env["GIT_CONFIG_GLOBAL"] == "${{ runner.os == 'Windows' && 'NUL' || '/dev/null' }}"
    upload = steps["Upload verification evidence"]
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["include-hidden-files"] is True
    assert upload["with"]["if-no-files-found"] == "error"
    assert set(upload["with"]["path"].splitlines()) == {
        "controlcoding lab/.controlcoding/verification_receipts/*.json",
        "controlcoding lab/.controlcoding/verification_receipts/*.xml",
        "controlcoding lab/.controlcoding/invariant_receipts/*.json",
        "controlcoding lab/.controlcoding/ci-environment.json",
    }


def test_ci_lock_has_exact_versions_and_hashes():
    text = (ROOT / ".github/requirements-ci.lock").read_text(encoding="utf-8")
    logical = text.replace("\\\n", " ").splitlines()
    packages = []
    for line in logical:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert re.match(r"[A-Za-z0-9_.-]+==[^ ;]+", line), line
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", line), line
        packages.append(line.split("==")[0].lower())
    assert {"pytest", "fastmcp", "pyyaml", "setuptools", "wheel", "pip"} <= set(packages)
    assert not re.search(r"[A-Z]:[\\/]|https?://[^\s]+@", text)


def test_ci_environment_output_is_explicit_and_does_not_export_secrets(tmp_path):
    output = tmp_path / "reports with spaces" / "environment.json"
    env = dict(os.environ, CI_TEST_SECRET="not-for-artifacts", GITHUB_TOKEN="not-for-artifacts")
    result = subprocess.run([sys.executable, "-B", str(ROOT / ".github/scripts/record_ci_environment.py"),
                             "--output", str(output)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["python"]["executable"] == sys.executable
    assert len(data["inputs"]["controlcoding.verification.json"]) == 64
    assert "not-for-artifacts" not in output.read_text(encoding="utf-8")
    assert data["distributions"] and "environment" not in data


@pytest.mark.parametrize("source", ["tracked", "generated"])
def test_cli_groups_have_disjoint_targets_and_reports(source):
    contract = (json.loads((ROOT / "controlcoding.verification.json").read_text(encoding="utf-8"))
                if source == "tracked" else cc._default_verification_contract(ROOT))
    selected, issues = cc._select_verification_suites(contract, [], [], False)
    assert not issues
    groups = {suite["id"]: suite for suite in selected}
    expected = {
        "cli-regression": {"tests/test_cc_cli.py", "tests/test_gitignore.py"},
        "verification-support-regression": {
            "tests/test_ci_configuration.py", "tests/test_cc_evidence.py",
            "tests/test_cc_evidence_process.py", "tests/test_cc_public_examples.py",
        },
    }
    seen = set()
    reports = set()
    for group_id, targets in expected.items():
        suite = groups[group_id]
        tokens = shlex.split(suite["command"])
        actual = [token for token in tokens if token.startswith("tests/")]
        assert len(actual) == len(set(actual)) and set(actual) == targets
        assert not seen.intersection(actual)
        seen.update(actual)
        assert suite["required"] is True and suite["kind"] == "regression"
        assert suite["timeoutSeconds"] == (390 if group_id == "cli-regression" else 300)
        report = tokens[tokens.index("--junitxml") + 1]
        assert report == "{project}/.controlcoding/verification_receipts/" + group_id + ".xml"
        reports.add(report)
    assert len(reports) == 2
    for suite in selected:
        if suite["id"] not in expected:
            assert not seen.intersection(shlex.split(suite["command"]))


@pytest.mark.parametrize("available", [
    (), ("test_cc_evidence.py",), ("test_cc_public_examples.py",),
    ("test_ci_configuration.py", "test_cc_evidence.py", "test_cc_evidence_process.py",
     "test_cc_public_examples.py"),
])
def test_generated_support_group_only_covers_available_files(tmp_path, available):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("test_cc_cli.py", "test_gitignore.py", *available):
        (tests / name).write_text("", encoding="utf-8")
    selected, issues = cc._select_verification_suites(
        cc._default_verification_contract(tmp_path), [], [], False)
    assert not issues
    groups = {suite["id"]: suite for suite in selected}
    cli_targets = [t for t in shlex.split(groups["cli-regression"]["command"])
                   if t.startswith("tests/")]
    assert cli_targets == ["tests/test_cc_cli.py", "tests/test_gitignore.py"]
    if available:
        support = groups["verification-support-regression"]
        assert [t for t in shlex.split(support["command"]) if t.startswith("tests/")] == [
            "tests/" + name for name in available]
        assert support["required"] is True
    else:
        assert "verification-support-regression" not in groups


def test_generated_tests_fallback_does_not_duplicate_support_group(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cc_public_examples.py").write_text("", encoding="utf-8")
    groups = {s["id"]: s for s in cc._default_verification_contract(tmp_path)["suites"]}
    assert "verification-support-regression" not in groups
    assert shlex.split(groups["cli-regression"]["command"])[3] == "tests"


@pytest.mark.parametrize("outcome", ["passed", "subset", "support-failure"])
def test_split_cli_real_receipts_require_both_groups(tmp_path, capsys, outcome):
    project = tmp_path / "split project with spaces"
    project.mkdir()
    policy = cc._default_verification_contract(ROOT)
    groups = [dict(s) for s in policy["suites"]
              if s["id"] in {"cli-regression", "verification-support-regression"}]
    assert len(groups) == 2
    for suite in groups:
        tokens = shlex.split(suite["command"])
        for token in tokens:
            if token.startswith("tests/"):
                path = project / token
                path.parent.mkdir(exist_ok=True)
                should_fail = outcome == "support-failure" and path.name == "test_cc_evidence.py"
                path.write_text("def test_present():\n    assert " + str(not should_fail) + "\n",
                                encoding="utf-8")
        suite["command"] = '"' + sys.executable.replace("\\", "/") + '"' + suite["command"][len("python"):]
    (project / "controlcoding.verification.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredKinds": ["regression"], "suites": groups,
    }), encoding="utf-8")
    code = cc.cmd_verify_run(project, suite_ids=["cli-regression"] if outcome == "subset" else [],
                             json_output=True)
    result = json.loads(capsys.readouterr().out)
    assert code == (1 if outcome == "support-failure" else 0)
    assert result["status"] == {"passed": "passed", "subset": "passed_subset",
                                "support-failure": "failed"}[outcome]
    receipt = result["receipt"]
    ids = [s["id"] for s in groups]
    assert receipt["selection"]["required"] == ids
    assert receipt["selection"]["executed"] == (ids[:1] if outcome == "subset" else ids)
    assert not (project / receipt["tempDir"]).exists()
    reports = project / ".controlcoding/verification_receipts"
    first = ET.parse(reports / "cli-regression.xml").findall(".//testcase")
    assert len(first) == 2 and all(c.find("failure") is None for c in first)
    support = reports / "verification-support-regression.xml"
    assert support.exists() is (outcome != "subset")
    if support.exists():
        cases = ET.parse(support).findall(".//testcase")
        assert len(cases) == 4
        assert sum(c.find("failure") is not None for c in cases) == (outcome == "support-failure")
    strict = cc.cmd_verify_status(project, json_output=True, require_current=True)
    status = json.loads(capsys.readouterr().out)
    assert strict == (0 if outcome == "passed" else 1)
    assert status["evidence"]["assessment"]["currentRequiredPass"] is (outcome == "passed")


@pytest.mark.parametrize("source", ["tracked", "generated"])
@pytest.mark.parametrize("outcome", ["passed", "subset", "core-failure", "timeout"])
def test_core_budget_reaches_runner_and_preserves_receipts(tmp_path, capsys, monkeypatch,
                                                         source, outcome):
    project = tmp_path / "core budget project with spaces"
    project.mkdir()
    policy = (json.loads((ROOT / "controlcoding.verification.json").read_text(encoding="utf-8"))
              if source == "tracked" else cc._default_verification_contract(ROOT))
    core = dict(next(s for s in policy["suites"] if s["id"] == "core-governance-regression"))
    for token in shlex.split(core["command"]):
        if token.startswith("tests/"):
            path = project / token
            path.parent.mkdir(exist_ok=True)
            if path.name == "test_cc_memory.py" and outcome == "timeout":
                body = "import time\ndef test_present():\n    time.sleep(30)\n"
            else:
                passed = not (path.name == "test_cc_memory.py" and outcome == "core-failure")
                body = "def test_present():\n    assert " + str(passed) + "\n"
            path.write_text(body, encoding="utf-8")
    interpreter = '"' + sys.executable.replace("\\", "/") + '"'
    core["command"] = interpreter + core["command"][len("python"):]
    if outcome == "timeout":
        core["timeoutSeconds"] = 1  # Exercise real interruption without a long wait.
    following = {
        "id": "following-default", "kind": "regression", "required": True,
        "command": interpreter + ' -c "from pathlib import Path; '
                   "Path('.controlcoding/verification_receipts/following.txt').write_text('ran')\"",
    }
    (project / "controlcoding.verification.json").write_text(json.dumps({
        "schemaVersion": 1, "requiredKinds": ["regression"], "suites": [core, following],
    }), encoding="utf-8")
    observed = []
    original = cc.cc_evidence.run_command

    def capture_deadline(*args, **kwargs):
        observed.append(kwargs["timeout"])
        return original(*args, **kwargs)

    monkeypatch.setattr(cc.cc_evidence, "run_command", capture_deadline)
    code = cc.cmd_verify_run(project, suite_ids=[core["id"]] if outcome == "subset" else [],
                             json_output=True)
    result = json.loads(capsys.readouterr().out)
    assert observed == ({"passed": [420, 300], "subset": [420],
                         "core-failure": [420, 300], "timeout": [1]}[outcome])
    assert code == (1 if outcome in {"core-failure", "timeout"} else 0)
    assert result["status"] == {"passed": "passed", "subset": "passed_subset",
                                "core-failure": "failed", "timeout": "incomplete"}[outcome]
    receipt = result["receipt"]
    ids = [core["id"], following["id"]]
    partial = outcome in {"subset", "timeout"}
    assert receipt["selection"]["required"] == ids
    assert receipt["selection"]["selected"] == (ids[:1] if outcome == "subset" else ids)
    assert receipt["selection"]["executed"] == (ids[:1] if partial else ids)
    assert receipt["selection"]["omitted"] == (ids[1:] if partial else [])
    assert not (project / receipt["tempDir"]).exists()
    reports = project / ".controlcoding/verification_receipts"
    assert (reports / "following.txt").exists() is (not partial)
    report = reports / "core-governance-regression.xml"
    if outcome == "timeout":
        assert receipt["suites"][0]["error"] == "timeout"
        assert not report.exists()
    else:
        cases = ET.parse(report).findall(".//testcase")
        assert len(cases) == 7
        assert not any(c.find("error") is not None or c.find("skipped") is not None for c in cases)
        assert sum(c.find("failure") is not None for c in cases) == (outcome == "core-failure")
    strict = cc.cmd_verify_status(project, json_output=True, require_current=True)
    status = json.loads(capsys.readouterr().out)
    assert strict == (0 if outcome == "passed" else 1)
    assert status["evidence"]["assessment"]["currentRequiredPass"] is (outcome == "passed")
