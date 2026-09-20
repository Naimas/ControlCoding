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
    def cli_command():
        return next(s["command"] for s in cc._default_verification_contract(tmp_path)["suites"]
                    if s["id"] == "cli-regression")
    assert "tests/test_cc_public_examples.py" not in cli_command()
    (tests / "test_cc_public_examples.py").write_text("", encoding="utf-8")
    assert "tests/test_cc_public_examples.py" in cli_command()


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
