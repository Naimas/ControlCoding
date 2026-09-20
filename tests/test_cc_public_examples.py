"""Frozen public snippets exercised as reviewed argv on bounded archive fixtures.

This is not shell, installation, host, or full Core verification coverage.
Companion scenarios are negative controls, not additional published examples.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = "docs/install-controlcoding-on-your-project.md"
EVIDENCE = "docs/verification-evidence.md"
SCENARIOS = {
    "status": ("valid manifest/contracts, no receipts", "configuration valid, currentRequiredPass false"),
    "strict": ("no receipt, then complete pass, then source/contract drift", "exit 1/0/1/1; status read-only"),
    "run": ("three bounded required commands; no recursive pytest contract", "one passed receipt, all three IDs, temp attempt cleaned"),
    "elicit": ("valid fixture, no prior draft", "finance draft only; active manifest unchanged"),
    "report": ("manifest before and after complete invariant run", "properties and separate false/true current assessment"),
    "doctor": ("manifest before and after complete invariant run", "configuration and separate false/true current assessment"),
    "preview": ("valid active manifest, no workflow", "local CI plan; no writes"),
    "wire": ("valid active manifest, no workflow", "only expected workflow with default pytest command; no hosted execution"),
    "guide": ("existing and absent root", "readable installation guide / prerequisite rejection; neither writes"),
}


@dataclass(frozen=True)
class Example:
    id: str
    document: str
    sections: tuple[str, ...]
    snippet: str
    argv: tuple[str, ...]
    scenario: str
    expected_exit: int = 0
    allowed_files: tuple[str, ...] = ()
    # Only these three placeholders are replaced; flags are fixed in argv.
    substitutions: tuple[str, ...] = ("python -> sys.executable", "script -> ROOT/scripts/cc.py",
                                     "project root -> synthetic fixture")


CATALOG = (
    Example("B01-contributing", "CONTRIBUTING.md", ("## Running Tests",),
            "python scripts/cc.py verify status --project-root .",
            ("verify", "status", "--project-root", "{project}"), "status"),
    Example("B01-verify", EVIDENCE, ("## Outcomes and current evidence",),
            "python scripts/cc.py verify status --project-root . --json",
            ("verify", "status", "--project-root", "{project}", "--json"), "status"),
    Example("B01-invariants", EVIDENCE, ("## Outcomes and current evidence",),
            "python scripts/cc.py invariants status --project-root . --json",
            ("invariants", "status", "--project-root", "{project}", "--json"), "status"),
    Example("B02-contributing", "CONTRIBUTING.md", ("## Running Tests",),
            "python scripts/cc.py verify run --project-root . --json",
            ("verify", "run", "--project-root", "{project}", "--json"), "run", 0,
            (".controlcoding/verification_receipts/*.json",)),
    Example("B02-contract", "CONTROLCODING.md", ("## Verification Contracts",),
            "python scripts/cc.py verify run --project-root .",
            ("verify", "run", "--project-root", "{project}"), "run", 0,
            (".controlcoding/verification_receipts/*.json",)),
    Example("B02-readme", "README.md", ("### Repo Policy Split",),
            "python scripts/cc.py verify run --project-root .",
            ("verify", "run", "--project-root", "{project}"), "run", 0,
            (".controlcoding/verification_receipts/*.json",)),
    Example("B02-release", "docs/release-model.md", ("## Source Release Verification",),
            "python scripts/cc.py verify run --project-root .",
            ("verify", "run", "--project-root", "{project}"), "run", 0,
            (".controlcoding/verification_receipts/*.json",)),
    Example("B03-verify", EVIDENCE, ("## Outcomes and current evidence",),
            "python scripts/cc.py verify status --project-root . --require-current --json",
            ("verify", "status", "--project-root", "{project}", "--require-current", "--json"), "strict", 1),
    Example("B03-invariants", EVIDENCE, ("## Outcomes and current evidence",),
            "python scripts/cc.py invariants status --project-root . --require-current --json",
            ("invariants", "status", "--project-root", "{project}", "--require-current", "--json"), "strict", 1),
    Example("B05-elicit", INSTALL, ("## Manual Path",),
            "python /path/to/ControlCoding/scripts/cc.py invariants elicit --domain finance --write --project-root .",
            ("invariants", "elicit", "--domain", "finance", "--write", "--project-root", "{project}"),
            "elicit", 0, ("docs/invariants/finance-elicitation.md",)),
    Example("B06-report", INSTALL, ("## Manual Path",),
            "python /path/to/ControlCoding/scripts/cc.py invariants report --project-root .",
            ("invariants", "report", "--project-root", "{project}"), "report"),
    Example("B06-doctor", INSTALL, ("## Manual Path",),
            "python /path/to/ControlCoding/scripts/cc.py invariants doctor --project-root .",
            ("invariants", "doctor", "--project-root", "{project}"), "doctor"),
    Example("B07-preview", INSTALL, ("## Manual Path",),
            "python /path/to/ControlCoding/scripts/cc.py invariants wire-ci --project-root .",
            ("invariants", "wire-ci", "--project-root", "{project}"), "preview"),
    Example("B07-write", INSTALL, ("## Manual Path",),
            "python /path/to/ControlCoding/scripts/cc.py invariants wire-ci --write --project-root .",
            ("invariants", "wire-ci", "--write", "--project-root", "{project}"), "wire", 0,
            (".github/workflows/controlcoding-invariants.yml",)),
    Example("B08-guide", INSTALL,
            ("## Step 1: Installation Contract", "## Use It From An IDE Chat",
             "### Recommended user-facing install ceremony"),
            "python /path/to/ControlCoding/scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .",
            ("setup", "--chat-guide", "--host-hint", "codex_cli", "--project-root", "{project}"), "guide"),
    Example("B08-powershell-argv", INSTALL, ("## Complete Fresh-Project Example",),
            "& $CcPython $CcScript setup --chat-guide --host-hint codex_cli --project-root $CcTarget",
            ("setup", "--chat-guide", "--host-hint", "codex_cli", "--project-root", "{project}"), "guide"),
    Example("B08-posix-argv", INSTALL, ("## Complete Fresh-Project Example",),
            '"$CC_PYTHON" "$CC_SCRIPT" setup --chat-guide --host-hint codex_cli --project-root "$CC_TARGET" || exit $?',
            ("setup", "--chat-guide", "--host-hint", "codex_cli", "--project-root", "{project}"), "guide"),
)


def validate_binding(example, text):
    """Exact full-line identity and section/occurrence binding; never parse argv."""
    heading = ""
    found = []
    for line in text.splitlines():
        if line.startswith("#"):
            heading = line
        if line == example.snippet:
            found.append(heading)
    assert tuple(found) == example.sections, (example.id, found, example.sections)


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else "directory" for p in root.rglob("*")} if root.exists() else {}


def cli(project, argv, expected=0, allowed=()):
    assert project != ROOT and not project.is_relative_to(ROOT)
    before = inventory(project)
    command = [sys.executable, "-B", str(ROOT / "scripts/cc.py"),
               *(str(project) if a == "{project}" else a for a in argv)]
    result = subprocess.run(command, cwd=project.parent, capture_output=True, text=True,
                            encoding="utf-8", timeout=45)
    after = inventory(project)
    changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
    record = {"argv": command, "runtime": sys.version, "fixture": str(project),
              "before": before, "after": after, "changed": sorted(changed),
              "allowed": allowed, "expectedExit": expected, "exit": result.returncode,
              "stdout": result.stdout, "stderr": result.stderr}
    # External evidence beside the fixture; never part of the measured project.
    with (project.parent / "cli-evidence.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    assert result.returncode == expected, record
    for p in changed:
        assert p not in before, ("existing path changed/removed", record)
        assert any(Path(p).match(pattern) or
                   (after[p] == "directory" and pattern.startswith(p + "/"))
                   for pattern in allowed), record
    return result.stdout


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "synthetic project"
    root.mkdir()
    (root / "check.py").write_text("import sys\nprint('bounded fixture check')\nsys.exit(int(sys.argv[1]))\n")
    command = '"' + sys.executable.replace("\\", "/") + '" -B check.py 0'
    suites = [{"id": name, "kind": kind, "required": True, "command": command}
              for name, kind in (("first", "targeted"), ("second", "regression"), ("third", "invariant"))]
    invariants = [{"id": name, "title": name, "domain": "fixture", "kind": "domain",
                   "severity": "blocking", "status": "active", "property": "Bounded fixture exits zero",
                   "threshold": "0 failures", "command": command, "evidence": ["check.py"]}
                  for name in ("first", "second")]
    (root / "controlcoding.verification.json").write_text(json.dumps(
        {"schemaVersion": 1, "requiredKinds": ["targeted", "regression", "invariant"], "suites": suites}))
    (root / "controlcoding.invariants.json").write_text(json.dumps(
        {"schemaVersion": 1, "domains": ["fixture"], "invariants": invariants}))
    (root / "docs").mkdir()
    for name in ("README.md", "docs/quick-start.md", "docs/install-controlcoding-on-your-project.md"):
        (root / name).write_text("Run `cc truth report`.\n")
    return root


def run_receipt(project, kind, extra=(), expected=0):
    directory = "verification_receipts" if kind == "verify" else "invariant_receipts"
    output = cli(project, (kind, "run", "--project-root", "{project}", "--json", *extra), expected,
                 (f".controlcoding/{directory}/*.json", f".controlcoding/{'verification' if kind == 'verify' else 'invariant'}_tmp",))
    return json.loads(output)["receipt"]


@pytest.mark.parametrize("example", CATALOG, ids=lambda e: e.id)
def test_documented_example(example, project):
    validate_binding(example, (ROOT / example.document).read_text(encoding="utf-8"))
    (project.parent / "binding.json").write_text(json.dumps({
        **example.__dict__, "prerequisites": SCENARIOS[example.scenario][0],
        "observable": SCENARIOS[example.scenario][1],
        "snippetSha256": hashlib.sha256(example.snippet.encode()).hexdigest(),
    }, indent=2), encoding="utf-8")
    # Prepared manifest with no receipt is the default prerequisite.
    allowed = example.allowed_files
    if example.scenario == "run":
        allowed += (".controlcoding/verification_tmp",)
    output = cli(project, example.argv, example.expected_exit, allowed)
    if example.scenario in {"status", "strict"}:
        if "--json" in example.argv:
            payload = json.loads(output)
            assert payload["ok"] is True
            assert payload["evidence"]["assessment"]["currentRequiredPass"] is False
        else:
            assert "current required pass=false" in output
    if example.scenario == "run":
        receipts = list((project / ".controlcoding/verification_receipts").glob("*.json"))
        assert len(receipts) == 1
        receipt = json.loads(receipts[0].read_text())
        assert receipt["status"] == "passed"
        assert receipt["selection"]["selected"] == ["first", "second", "third"]
        assert not (project / receipt["tempDir"]).exists()
    if example.scenario == "strict":
        kind = example.argv[0]
        run_receipt(project, kind)
        assert json.loads(cli(project, example.argv))["evidence"]["assessment"]["currentRequiredPass"]
        original = (project / "check.py").read_bytes()
        (project / "check.py").write_bytes(original + b"# changed input\n")
        assert not json.loads(cli(project, example.argv, 1))["evidence"]["assessment"]["currentRequiredPass"]
        (project / "check.py").write_bytes(original)
        contract = project / ("controlcoding.verification.json" if kind == "verify" else "controlcoding.invariants.json")
        contract.write_bytes(contract.read_bytes() + b"\n")
        assert not json.loads(cli(project, example.argv, 1))["evidence"]["assessment"]["currentRequiredPass"]
    if example.scenario == "elicit":
        draft = (project / example.allowed_files[0]).read_text()
        assert "draft" in draft.lower() and "balance-consistency" in draft
    if example.scenario in {"report", "doctor"}:
        assert "current required pass=False" in output
        run_receipt(project, "invariants")
        assert "current required pass=True" in cli(project, example.argv)
    if example.scenario in {"preview", "wire"}:
        assert "hosted execution and server enforcement unverified" in output
        if example.scenario == "wire":
            workflow = (project / example.allowed_files[0]).read_text()
            assert "run: python -m pytest tests/invariants -q" in workflow
            assert "pull_request:" in workflow and "push:" in workflow
    if example.scenario == "guide":
        assert "chat-guided ControlCoding installation assistant" in output
        assert "Codex" in output
        absent = project.parent / "absent"
        cli(absent, example.argv, 1)
        assert not absent.exists()


@pytest.mark.parametrize("kind", ["verify", "invariants"])
def test_B04_subset_and_real_failure(project, kind):
    selector = "--suite" if kind == "verify" else "--id"
    receipt = run_receipt(project, kind, (selector, "first"))
    assert receipt["status"] == "passed_subset"
    assert receipt["selection"]["selected"] == ["first"]
    assert receipt["selection"]["omitted"] == (["second", "third"] if kind == "verify" else ["second"])
    status = (kind, "status", "--require-current", "--project-root", "{project}", "--json")
    assert not json.loads(cli(project, status, 1))["evidence"]["assessment"]["currentRequiredPass"]
    (project / "check.py").write_text("raise SystemExit(7)\n")
    failed = run_receipt(project, kind, expected=1)
    assert failed["status"] == "failed"
    assert not json.loads(cli(project, status, 1))["evidence"]["assessment"]["currentRequiredPass"]


@pytest.mark.parametrize("route", [("report",), ("check",), ("check-docs",), ("check", "--include-docs")])
def test_B09_truth_route_scenarios(project, route):
    argv = ("truth", *route, "--project-root", "{project}", "--json")
    payload = json.loads(cli(project, argv))
    assert payload["scope"] == "structural" and len(payload["limitations"]) == 3
    if "--include-docs" in route:
        assert payload["docs"]["scope"] == "structural"
    human = cli(project, argv[:-1])
    assert "Scope: structural" in human and "Evidence references are declarations" in human
    (project / ".controlcoding").mkdir(exist_ok=True)
    registry = project / ".controlcoding/capabilities.json"
    entry = {"id": "fixture", "label": "Fixture", "state": "shipped", "control_level": "mechanical",
             "commands": ["truth report"], "evidence": ["invented-does-not-exist"]}
    registry.write_text(json.dumps({"capabilities": [entry]}))
    assert json.loads(cli(project, argv))["ok"]
    entry["evidence"] = []
    entry["commands"] = ["ghost command"]
    registry.write_text(json.dumps({"capabilities": [entry]}))
    (project / "README.md").write_text("Run `cc ghost command`.\nControlCoding guarantees safety.\n")
    bad = json.loads(cli(project, argv, 0 if route == ("report",) else 1))
    assert bad["scope"] == "structural" and not bad["ok"] and bad["findings"]


@pytest.mark.parametrize("argv, diagnostic", [
    (("invariants", "elicit", "--domain", "finance", "--write", "--unknown-option"), "unrecognized arguments: --unknown-option"),
    (("invariants", "add", "--domain", "fixture", "--property", "Missing ID"), "required: --id"),
])
def test_B10_real_parser_rejects_before_writes(project, argv, diagnostic):
    cli(project, (*argv, "--project-root", "{project}"), 2)
    record = json.loads((project.parent / "cli-evidence.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert diagnostic in record["stderr"]


@pytest.mark.parametrize("case", ["invented", "empty", "unknown-route"])
def test_B09_registry_findings_are_independent(project, case):
    control = project / ".controlcoding"
    control.mkdir()
    entry = {"id": "fixture", "label": "Fixture", "state": "shipped", "control_level": "mechanical",
             "commands": ["ghost command" if case == "unknown-route" else "truth report"],
             "evidence": [] if case == "empty" else ["invented-does-not-exist"]}
    (control / "capabilities.json").write_text(json.dumps({"capabilities": [entry]}))
    payload = json.loads(cli(project, ("truth", "check", "--project-root", "{project}", "--json"),
                             0 if case == "invented" else 1))
    assert payload["scope"] == "structural"
    findings = payload["findings"]
    if case == "invented":
        assert findings == []
        assert not (project / "invented-does-not-exist").exists()
    else:
        assert len(findings) == 1
        assert ("no evidence" if case == "empty" else "not routed") in findings[0]["message"]


@pytest.mark.parametrize("mutation", ["option", "missing", "duplicate"])
def test_B11_catalog_drift(mutation):
    example = CATALOG[0]
    text = (ROOT / example.document).read_text(encoding="utf-8")
    validate_binding(example, text)
    replacement = {"option": example.snippet + " --unknown-option", "missing": "",
                   "duplicate": example.snippet + "\n" + example.snippet}[mutation]
    with pytest.raises(AssertionError):
        validate_binding(example, text.replace(example.snippet + "\n", replacement + "\n"))


def test_B12_pattern_text_is_only_configuration(project):
    workflow = project / ".github/workflows/comment.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("# verify run - comment only, not executable YAML\n")
    output = json.loads(cli(project, ("invariants", "doctor", "--project-root", "{project}", "--json")))
    assert output["ci"]["runsInvariantGate"] is True  # retained heuristic
    assert output["ci"]["scope"] == "local_configuration"
    assert output["ci"]["hostedExecution"] == output["ci"]["serverEnforcement"] == "unverified"
    assert "Legacy" in output["ci"]["runsInvariantGateMeaning"]
    assert not output["evidence"]["assessment"]["currentRequiredPass"]
