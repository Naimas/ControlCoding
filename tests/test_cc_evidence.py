"""Receipt trust and input confinement; all mutations use pytest fixtures."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cc
import cc_evidence as evidence
import cc_evidence_inputs as inputs


@pytest.mark.parametrize("kind,variant", [
    ("verification", "all"), ("verification", "kind"),
    ("invariants", "all"), ("invariants", "all_domain"),
])
def test_semantics_saved_omitted_match(tmp_path, kind, variant):
    value = contract(tmp_path, kind=kind)
    key = "suites" if kind == "verification" else "invariants"
    value[key][1]["required"] = False
    if kind == "invariants":
        value[key][1]["status"] = "draft"
    (tmp_path / f"controlcoding.{kind}.json").write_text(json.dumps(value))
    code, result = invoke(tmp_path, kind)
    assert code == 0 and result["receipt"]["selection"]["selected"] == ["check-0"]
    receipt = result["receipt"]
    requested = receipt["selection"]["requested"]
    if variant == "kind":
        requested["kinds"] = ["targeted"]
    else:
        requested["all"] = True
    if variant == "all_domain":
        requested["domains"] = ["testing"]
    # Independent CLI oracle, with an explicit expected ordered list.
    if kind == "verification":
        selected, issues = cc._select_verification_suites(value, [], requested["kinds"], requested["all"])
    else:
        selected, issues = cc._select_invariants(value, [], requested["domains"], [], requested["all"])
    assert not issues and [e["id"] for e in selected] == ["check-0", "check-1"]
    prepared = cc._evidence_prepare(tmp_path, kind)
    assert evidence.validate_receipt(receipt, kind, entries=prepared["executable"], contracts=prepared["contracts"])
    (tmp_path / result["receiptPath"]).write_text(json.dumps(receipt))
    assert_semantics_invalid_readers(tmp_path, kind)


def assert_semantics_invalid_readers(project, kind):
    for strict in (False, True):
        code, payload = status(project, kind, strict=strict)
        assert code == int(strict)  # Configuration-only success is preserved.
        assessment = payload["evidence"]["assessment"]
        assert assessment["state"] == "invalid" and not assessment["currentRequiredPass"]
        assert "selection" not in payload["evidence"]["latest"]
    if kind == "invariants":
        for reader in (cc._invariant_report_payload, cc._invariant_doctor_payload):
            payload = reader(project)
            assert payload["evidence"]["assessment"]["state"] == "invalid"
            assert "selection" not in payload["evidence"]["latest"]


SEMANTICS_ROUTES = [
    ("verification", {}, ["z", "q", "c"]),
    ("verification", {"suite_ids": ["c", "z", "z"], "kinds": ["inactive-invalid"]}, ["z", "c"]),
    ("verification", {"kinds": ["targeted", "targeted"]}, ["z", "a", "c"]),
    ("verification", {"all_suites": True, "suite_ids": ["inactive-id"], "kinds": ["inactive-invalid"]}, ["z", "a", "q", "b", "c"]),
    ("invariants", {}, ["z", "q", "c"]),
    ("invariants", {"invariant_ids": ["c", "z", "z", "a"]}, ["z", "c"]),
    ("invariants", {"kinds": ["domain", "domain"]}, ["z", "c"]),
    ("invariants", {"domains": ["custom/customer-domain", "inactive-domain"]}, ["z", "c"]),
    ("invariants", {"all_invariants": True}, ["z", "a", "q", "b", "c"]),
    ("invariants", {"all_invariants": True, "domains": ["custom/customer-domain"]}, ["z", "a", "c"]),
    ("invariants", {"all_invariants": True, "invariant_ids": ["c", "a", "z", "q"], "domains": ["custom/customer-domain"], "kinds": ["domain"]}, ["z", "a", "c"]),
]


def semantics_contract(project, kind):
    value = contract(project, ["pass"] * 5, kind=kind)
    key = "suites" if kind == "verification" else "invariants"
    for row, name in zip(value[key], ["z", "a", "q", "b", "c"]):
        row.update(id=name, required=name in ("z", "q", "c"))
        row["kind"] = ("targeted" if kind == "verification" else "domain") if name in ("z", "a", "c") else ("regression" if kind == "verification" else "security")
        if kind == "invariants":
            row.update(status="active" if row["required"] else "draft",
                       domain="custom/customer-domain" if name in ("z", "a", "c") else "testing")
    if kind == "invariants":
        value[key].append(dict(value[key][0], id="documented", status="draft", command=""))
    (project / f"controlcoding.{kind}.json").write_text(json.dumps(value))
    return value


@pytest.mark.parametrize("kind,kwargs,expected", SEMANTICS_ROUTES)
@pytest.mark.parametrize("mutation", ["genuine", "omit", "reorder"])
def test_semantics_ordered_routes(tmp_path, cheap_context, kind, kwargs, expected, mutation):
    semantics_contract(tmp_path, kind)
    code, result = invoke(tmp_path, kind, **kwargs)
    assert code == 0
    receipt = result["receipt"]
    assert receipt["selection"]["selected"] == expected
    prepared = cc._evidence_prepare(tmp_path, kind)
    if mutation == "genuine":
        assert evidence.validate_receipt(receipt, kind, entries=prepared["executable"], contracts=prepared["contracts"]) == []
        assert status(tmp_path, kind, strict=True)[0] == int(receipt["status"] == "passed_subset")
        return
    # Keep outcomes, counts and coverage self-consistent: only selector intent
    # correspondence detects the omitted/reordered declaration.
    ids = expected[:-1] if mutation == "omit" else list(reversed(expected))
    key = "suites" if kind == "verification" else "invariants"
    by_id = {row["id"]: row for row in receipt[key]}
    receipt[key] = [by_id[name] for name in ids]
    receipt["selection"] = evidence.selection(receipt["plan"], ids, receipt[key], receipt["selection"]["requested"])
    receipt["status"] = evidence.outcome(receipt)
    assert evidence.validate_receipt(receipt, kind, entries=prepared["executable"], contracts=prepared["contracts"])
    (tmp_path / result["receiptPath"]).write_text(json.dumps(receipt))
    assert_semantics_invalid_readers(tmp_path, kind)


@pytest.mark.parametrize("kind,field,new_value", [
    ("verification", "kind", "smoke"), ("invariants", "kind", "security"),
    ("invariants", "domain", "changed-domain"), ("invariants", "status", "retired"),
])
def test_semantics_contract_metadata_drift(tmp_path, cheap_context, kind, field, new_value):
    value = semantics_contract(tmp_path, kind)
    kwargs = {"kinds": ["targeted" if kind == "verification" else "domain"]}
    if field == "domain":
        kwargs = {"domains": ["custom/customer-domain"]}
    if field == "status":
        kwargs = {"all_invariants": True}
    _, result = invoke(tmp_path, kind, **kwargs)
    old = cc._evidence_prepare(tmp_path, kind)
    key = "suites" if kind == "verification" else "invariants"
    value[key][1 if field == "status" else 0][field] = new_value
    (tmp_path / f"controlcoding.{kind}.json").write_text(json.dumps(value))
    current = cc._evidence_prepare(tmp_path, kind)
    assert not current["issues"]
    assert evidence.make_plan(current["executable"], current["required"]) == result["receipt"]["plan"]
    assert current["contracts"] != old["contracts"]
    assert evidence.validate_receipt(result["receipt"], kind, entries=current["executable"], contracts=current["contracts"]) == []
    for strict in (False, True):
        code, payload = status(tmp_path, kind, strict=strict)
        assert code == int(strict)
        assert payload["evidence"]["assessment"]["state"] == "stale"


@pytest.mark.parametrize("kind", ["verification", "invariants"])
def test_semantics_missing_context_cannot_certify(tmp_path, cheap_context, kind):
    contract(tmp_path, kind=kind)
    _, result = invoke(tmp_path, kind)
    receipt = result["receipt"]
    assert evidence.validate_receipt(receipt, kind) == []
    assessment = evidence.assess_receipt(receipt, receipt["inputs"]["after"], receipt["contracts"],
        {k: receipt[k]["after"] for k in ("runner", "executionContext")}, receipt["plan"])
    assert assessment == {"state": "unknown", "currentRequiredPass": False, "reasons": ["selector_context_unavailable"]}


@pytest.mark.parametrize("kind", ["verification", "invariants"])
def test_semantics_interrupted_intent(tmp_path, cheap_context, monkeypatch, kind):
    semantics_contract(tmp_path, kind)
    run = evidence.run_command
    calls = []
    def interrupt(argv, **kwargs):
        if calls:
            raise KeyboardInterrupt
        calls.append(argv)
        return run(argv, **kwargs)
    monkeypatch.setattr(evidence, "run_command", interrupt)
    code, result = invoke(tmp_path, kind, **{"all_suites" if kind == "verification" else "all_invariants": True})
    assert code == 130
    receipt = result["receipt"]
    assert receipt["selection"]["selected"] == ["z", "a", "q", "b", "c"]
    assert receipt["selection"]["executed"] == ["z"]
    prepared = cc._evidence_prepare(tmp_path, kind)
    assert evidence.validate_receipt(receipt, kind, entries=prepared["executable"], contracts=prepared["contracts"]) == []
    assert status(tmp_path, kind, strict=True)[1]["evidence"]["assessment"]["state"] == "incomplete"


@pytest.mark.parametrize("kind", ["verification", "invariants"])
@pytest.mark.parametrize("limit", ["file", "total", "deadline", "entries", "race", "confinement", "unreadable"])
def test_rework_history_incomplete_cannot_recover_full(tmp_path, monkeypatch, kind, limit):
    # Real runner/context and commands, including the reviewed older-mtime case.
    contract(tmp_path, kind=kind)
    assert invoke(tmp_path, kind)[0] == 0
    selector = {"suite_ids" if kind == "verification" else "invariant_ids": ["check-0"]}
    _, subset = invoke(tmp_path, kind, **selector)
    assert status(tmp_path, kind, strict=True)[0] == 1
    path = tmp_path / subset["receiptPath"]
    if limit == "file":
        raw = path.read_bytes()
        path.write_bytes(raw + b" " * (2097153 - len(raw)))
        os.utime(path, (1, 1))
    elif limit == "total":
        # Ensure the pass is inspected before exhaustion; remove only owned subset.
        path.unlink()
        for i in range(18):
            older = path.with_name(f"older-{i}.json")
            older.write_bytes(b"{}" + b" " * (2097152 - 2))
            os.utime(older, (1, 1))
    elif limit == "entries":
        for i in range(128):
            path.with_name(f"older-{i}.json").write_text("{}")
    else:
        original = inputs.SafeRoot.read
        reached = []
        def fail_history(self, relative, budget, **kwargs):
            if "_receipts/" in relative:
                reached.append(relative)
                if limit == "unreadable":
                    raise PermissionError("PRIVATE_SENTINEL")
                if limit == "deadline":
                    budget.deadline = 0
                    return original(self, relative, budget, **kwargs)
                raise inputs.EvidenceError({"deadline": "snapshot_timeout", "race": "file_changed", "confinement": "unsafe_file_type"}[limit])
            return original(self, relative, budget, **kwargs)
        monkeypatch.setattr(inputs.SafeRoot, "read", fail_history)
    for strict in (False, True):
        code, result = status(tmp_path, kind, strict=strict)
        assert code == int(strict)
        assert result["evidence"]["assessment"] == {"state": "unknown", "currentRequiredPass": False, "reasons": ["history_inspection_incomplete"]}
        assert result["evidence"]["latest"] is None
        assert "PRIVATE_SENTINEL" not in json.dumps(result)
    if kind == "invariants":
        for reader in (cc._invariant_report_payload, cc._invariant_doctor_payload):
            assert reader(tmp_path)["evidence"]["assessment"]["state"] == "unknown"
    if limit in ("deadline", "race", "confinement", "unreadable"):
        assert reached


@pytest.mark.parametrize("kind", ["verification", "invariants"])
@pytest.mark.parametrize("aggregate", [False, True])
def test_rework_history_exact_byte_boundary(tmp_path, kind, aggregate):
    contract(tmp_path, ["pass"], kind=kind)
    _, full = invoke(tmp_path, kind)
    path = tmp_path / full["receiptPath"]
    raw = path.read_bytes()
    path.write_bytes(raw + b" " * (2097152 - len(raw)))
    if aggregate:
        for i in range(15):
            older = path.with_name(f"older-{i}.json")
            older.write_bytes(b"{}" + b" " * (2097152 - 2))
            os.utime(older, (1, 1))
    assert status(tmp_path, kind, strict=True)[0] == 0


@pytest.mark.parametrize("kind", ["verification", "invariants"])
@pytest.mark.parametrize("field,bad", [
    ("completeRequired", 1), ("selectedCount", True), ("selectedCount", 1.0),
    ("requiredCount", "1"), ("executedCount", None), ("executedCount", -1),
    ("selectedCount", 1001), ("requested.ids", {"PRIVATE_SENTINEL": "secret"}),
    ("requested.all", "PRIVATE_SENTINEL"), ("requested.all", 1),
    ("requested.extra", {"PRIVATE_SENTINEL": "secret"}),
    ("requested.kinds", ["PRIVATE_SENTINEL"]), ("requested.ids", ["absent"]),
    ("requested.domains", [None]), ("requested.domains", ["x" * 201]),
    ("requested.ids", [str(i) for i in range(1001)]),
    ("selected", ["absent"]), ("omitted", ["absent"]),
    ("extra", {"PRIVATE_SENTINEL": "secret"}),
])
def test_rework_selector_fields_reject_independently(tmp_path, kind, field, bad):
    contract(tmp_path, ["pass"], kind=kind)
    _, full = invoke(tmp_path, kind)
    receipt = full["receipt"]
    target = receipt["selection"]
    if field.startswith("requested."):
        target = target["requested"]
        field = field.split(".")[1]
    target[field] = bad
    assert evidence.validate_receipt(receipt, kind) == ["receipt_structure_invalid"]
    (tmp_path / full["receiptPath"]).write_text(json.dumps(receipt))
    for strict in (False, True):
        code, result = status(tmp_path, kind, strict=strict)
        assert code == int(strict)
        assert not result["evidence"]["assessment"]["currentRequiredPass"]
        assert "PRIVATE_SENTINEL" not in json.dumps(result)
    if kind == "invariants":
        for reader in (cc._invariant_report_payload, cc._invariant_doctor_payload):
            result = reader(tmp_path)
            assert not result["evidence"]["assessment"]["currentRequiredPass"]
            assert "PRIVATE_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["verification", "invariants"])
@pytest.mark.parametrize("variant", ["default", "ids", "kinds", "all", "combined", "domain"])
def test_rework_legitimate_selectors(tmp_path, kind, variant):
    value = contract(tmp_path, kind=kind)
    kwargs = {}
    id_key = "suite_ids" if kind == "verification" else "invariant_ids"
    all_key = "all_suites" if kind == "verification" else "all_invariants"
    kind_value = "targeted" if kind == "verification" else "domain"
    if variant == "ids": kwargs[id_key] = ["check-0", "check-0"]
    if variant == "kinds": kwargs["kinds"] = [kind_value, kind_value]
    if variant == "all": kwargs[all_key] = True
    if variant == "combined":
        kwargs = {id_key: ["check-0", "check-0"], "kinds": [kind_value], all_key: True}
        if kind == "verification": kwargs["kinds"] = ["inactive-invalid-kind"]
    if variant == "domain" and kind == "invariants":
        for row in value["invariants"]: row["domain"] = "custom/customer-domain"
        (tmp_path / "controlcoding.invariants.json").write_text(json.dumps(value))
        kwargs["domains"] = ["custom/customer-domain", "custom/customer-domain", "inactive-domain"]
    code, result = invoke(tmp_path, kind, **kwargs)
    assert code == 0, result
    assert evidence.validate_receipt(result["receipt"], kind) == []
    assert status(tmp_path, kind, strict=True)[0] == (result["status"] == "passed_subset")


@pytest.mark.parametrize("kind", ["verification", "invariants"])
def test_rework_selector_identity_and_contract_domain(tmp_path, kind):
    contract(tmp_path, kind=kind)
    selector = {"suite_ids" if kind == "verification" else "invariant_ids": ["check-0"]}
    _, result = invoke(tmp_path, kind, **selector)
    receipt = result["receipt"]
    receipt["selection"]["requested"]["ids"] = ["check-1"]
    assert evidence.validate_receipt(receipt, kind)
    if kind == "invariants":
        receipt["selection"]["requested"]["ids"] = ["check-0"]
        receipt["selection"]["requested"]["domains"] = ["PRIVATE_SENTINEL"]
        (tmp_path / result["receiptPath"]).write_text(json.dumps(receipt))
        for reader in (lambda p: status(p, kind)[1], cc._invariant_report_payload, cc._invariant_doctor_payload):
            value = reader(tmp_path)
            assert not value["evidence"]["assessment"]["currentRequiredPass"]
            assert "PRIVATE_SENTINEL" not in json.dumps(value)


def rework_git_fixture(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    contract(project, ["pass"])
    git(project, "add", "controlcoding.verification.json")
    git(project, "commit", "-qm", "synthetic base")
    one = git(project, "rev-parse", "HEAD").decode().strip()
    git(project, "commit", "--allow-empty", "-qm", "synthetic second")
    two = git(project, "rev-parse", "HEAD").decode().strip()
    return project, one, two


@pytest.mark.parametrize("metadata", ["refs", "refs/heads", "objects", "info", "config", "HEAD", "index"])
def test_rework_git_metadata_junction_precedes_subprocess(tmp_path, monkeypatch, metadata):
    project, one, two = rework_git_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if metadata == "refs": (outside / "heads").mkdir()
    ref = outside / ("heads/main" if metadata == "refs" else "main")
    original = project / ".git" / metadata
    original.rename(original.with_name(original.name + "-saved"))
    junction(original, outside)
    calls = []
    real = inputs.run_command
    def observe(*args, **kwargs):
        calls.append(kwargs["cwd"])
        return real(*args, **kwargs)
    monkeypatch.setattr(inputs, "run_command", observe)
    for oid in (one, two):
        ref.write_text(oid + "\n")
        result = inputs.capture_inputs(project, inputs.input_policy({}))
        assert not result["complete"] and result["scopeKind"] != "archive"
    assert calls == []  # No Git process could consume the external ref.
    assert invoke(project)[0] == 1
    assert status(project, strict=True)[0] == 1


@pytest.mark.parametrize("kind", ["verification", "invariants"])
def test_rework_real_history_leaf_race(tmp_path, monkeypatch, kind):
    contract(tmp_path, ["pass"], kind=kind)
    _, result = invoke(tmp_path, kind)
    receipt = tmp_path / result["receiptPath"]
    real = inputs.SafeRoot._stat
    reached = []
    def replace_after_stat(self, parent, name):
        info = real(self, parent, name)
        if self.path == tmp_path and name == receipt.name and not reached:
            # Between lstat and open: the real reader must reject the mismatch.
            receipt.write_bytes(receipt.read_bytes() + b" ")
            reached.append(True)
        return info
    monkeypatch.setattr(inputs.SafeRoot, "_stat", replace_after_stat)
    code, payload = status(tmp_path, kind, strict=True)
    assert reached and code == 1
    assert payload["evidence"]["assessment"]["reasons"] == ["history_inspection_incomplete"]


@pytest.mark.parametrize("boundary", ["parent", "leaf", "after_projection"])
def test_rework_git_replacement_is_confined(tmp_path, monkeypatch, boundary):
    project, one, two = rework_git_fixture(tmp_path)
    outside = tmp_path / "outside"
    (outside / "heads").mkdir(parents=True)
    (outside / "heads/main").write_text(one + "\n")
    reached = []
    if boundary == "parent":
        real = inputs.SafeRoot.directory
        @contextlib.contextmanager
        def replace(self, relative="", **kwargs):
            if self.path == project and relative == ".git/refs" and not reached:
                (project / ".git/refs").rename(project / ".git/refs-saved")
                junction(project / ".git/refs", outside)
                reached.append(True)
            with real(self, relative, **kwargs) as handle:
                yield handle
        monkeypatch.setattr(inputs.SafeRoot, "directory", replace)
    elif boundary == "leaf":
        real = inputs.SafeRoot.read
        def replace(self, relative, budget, **kwargs):
            if self.path == project and relative == ".git/refs/heads/main" and not reached:
                leaf = project / relative
                leaf.rename(leaf.with_name("main-saved"))
                junction(leaf, outside)
                reached.append(True)
            return real(self, relative, budget, **kwargs)
        monkeypatch.setattr(inputs.SafeRoot, "read", replace)
    else:
        real = inputs.SafeRoot.git
        def replace(self, *args, **kwargs):
            assert self.path != project
            if not reached:
                (project / ".git/refs").rename(project / ".git/refs-saved")
                junction(project / ".git/refs", outside)
                reached.append(True)
            return real(self, *args, **kwargs)
        monkeypatch.setattr(inputs.SafeRoot, "git", replace)
    result = inputs.capture_inputs(project, inputs.input_policy({}))
    assert reached
    if boundary == "after_projection":
        assert result["complete"] and result["revision"]["head"] == two
        assert not inputs.capture_inputs(project, inputs.input_policy({}))["complete"]
    else:
        assert not result["complete"]


@pytest.mark.parametrize("layout", ["unborn", "packed", "sha256", "include", "ignore", "alternates", "extension"])
def test_rework_git_supported_and_indirect_layouts(tmp_path, layout):
    if layout in ("unborn", "sha256"):
        args = ["init", "-q", "-b", "main"]
        if layout == "sha256": args.append("--object-format=sha256")
        git(tmp_path, *args)
        project = tmp_path
        (project / "source").write_text("fixture")
        if layout == "sha256":
            git(project, "add", "source"); git(project, "commit", "-qm", "fixture")
    else:
        project, _, _ = rework_git_fixture(tmp_path)
    if layout == "packed":
        git(project, "pack-refs", "--all")
        git(project, "repack", "-ad")
    if layout == "include":
        external = tmp_path / "external-config"
        external.write_text("INVALID PRIVATE_SENTINEL CONFIG\n")
        git(project, "config", "include.path", str(external))
        with (project / ".git/config").open("a") as stream:
            stream.write('\n[core]\nworktree = "' + tmp_path.as_posix() + '"\n')
    if layout == "ignore":
        (project / ".gitignore").write_text("ignored\n")
        (project / ".git/info/exclude").write_text("excluded\n")
        (project / "nested").mkdir()
        (project / "nested/.gitignore").write_text("local\n")
        before = inputs.capture_inputs(project, inputs.input_policy({}))
        for name in ("ignored", "excluded", "nested/local"):
            (project / name).write_text("does not affect identity")
    if layout == "alternates":
        (project / ".git/objects/info/alternates").write_text(str(tmp_path))
    if layout == "extension":
        git(project, "config", "extensions.worktreeConfig", "true")
    result = inputs.capture_inputs(project, inputs.input_policy({}))
    assert result["complete"] == (layout not in ("alternates", "extension")), result
    if layout == "ignore": assert result == before
    if layout == "unborn": assert result["revision"]["head"] is None
    if layout == "sha256": assert result["revision"]["objectFormat"] == "sha256"


def contract(project, commands=None, *, kind="verification", extras=None):
    commands = commands or ["pass", "pass"]
    rows = [{"id": f"check-{i}", "kind": "targeted", "required": True,
             "command": f'"{Path(sys.executable).as_posix()}" -B -c "{code}"'} for i, code in enumerate(commands)]
    value = {"schemaVersion": 1, "requiredKinds": ["targeted"], "suites": rows}
    if kind == "invariants":
        rows = [{**row, "kind": "domain", "domain": "testing", "severity": "warning",
                 "status": "active", "property": "fixture property", "threshold": "zero failures"} for row in rows]
        value = {"schemaVersion": 1, "invariants": rows}
    if extras is not None:
        value["evidenceInputs"] = extras
    (project / f"controlcoding.{kind}.json").write_text(json.dumps(value), encoding="utf-8")
    return value


def invoke(project, kind="verification", **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = (cc.cmd_verify_run if kind == "verification" else cc.cmd_invariants_run)(project, json_output=True, **kwargs)
    return code, json.loads(output.getvalue())


def status(project, kind="verification", strict=False):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = (cc.cmd_verify_status if kind == "verification" else cc.cmd_invariants_status)(project, json_output=True, require_current=strict)
    return code, json.loads(output.getvalue())


@pytest.fixture
def cheap_context(monkeypatch):
    # Unit tests isolate receipt state machines; real engine identity is tested
    # separately and by CLI integration tests without this fixture.
    value = {"complete": True, "digest": inputs.digest("fixture-engine"), "reasons": []}
    monkeypatch.setattr(inputs, "runner_context", lambda _: copy.deepcopy(value))
    return value


@pytest.mark.parametrize("kind", ["verification", "invariants"])
def test_subset_full_and_newer_subset_gate(tmp_path, cheap_context, kind):
    contract(tmp_path, kind=kind)
    selector = {"suite_ids" if kind == "verification" else "invariant_ids": ["check-0"]}
    rc, value = invoke(tmp_path, kind, **selector)
    assert rc == 0 and value["status"] == "passed_subset"
    assert value["receipt"]["selection"]["omitted"] == ["check-1"]
    assert status(tmp_path, kind, strict=True)[0] == 1
    rc, full = invoke(tmp_path, kind)
    assert rc == 0 and full["status"] == "passed"
    assert status(tmp_path, kind, strict=True)[0] == 0
    invoke(tmp_path, kind, **selector)
    assert status(tmp_path, kind)[0] == 0  # Configuration-only compatibility.
    assert status(tmp_path, kind, strict=True)[0] == 1


@pytest.mark.parametrize("change", ["edit", "same_mtime", "add", "delete", "rename", "contract", "mandatory", "extra"])
def test_input_changes_invalidate_receipt(tmp_path, cheap_context, change):
    contract(tmp_path, extras={"schemaVersion": 1, "extraPaths": [".env"], "environmentNames": []})
    source = tmp_path / "source.txt"
    source.write_text("one")
    assert invoke(tmp_path)[0] == 0
    before = source.stat()
    if change in ("edit", "same_mtime"):
        source.write_text("two")
        if change == "same_mtime":
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "add":
        (tmp_path / "added.txt").write_text("added")
    elif change == "delete":
        source.unlink()
    elif change == "rename":
        source.rename(tmp_path / "renamed.txt")
    elif change == "contract":
        contract(tmp_path, ["print(1)", "pass"])
    elif change == "mandatory":
        (tmp_path / ".controlcoding/settings.json").write_text("{}")
    else:
        (tmp_path / ".env").write_text("PRIVATE_SECRET")
    rc, value = status(tmp_path, strict=True)
    assert rc == 1 and value["evidence"]["assessment"]["state"] == "stale"
    assert "PRIVATE_SECRET" not in json.dumps(value)


def test_mtime_and_excluded_runtime_do_not_invalidate(tmp_path, cheap_context):
    contract(tmp_path)
    source = tmp_path / "source.txt"
    source.write_text("one")
    assert invoke(tmp_path)[0] == 0
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1000000000))
    (tmp_path / ".controlcoding/verification_receipts/fixture.xml").write_text("diagnostic")
    (tmp_path / "_work").mkdir()
    (tmp_path / "_work/ignored.txt").write_text("ignored")
    assert status(tmp_path, strict=True)[0] == 0


def test_changed_input_during_zero_exit_is_incomplete(tmp_path, cheap_context):
    contract(tmp_path, ["from pathlib import Path; Path('changed.txt').write_text('changed')"])
    rc, value = invoke(tmp_path)
    assert rc == 1 and value["status"] == "incomplete"
    assert value["receipt"]["suites"][0]["returnCode"] == 0


@pytest.mark.parametrize("reason", ["environment", "runner", "move"])
def test_context_changes_invalidate(tmp_path, cheap_context, monkeypatch, reason):
    project = tmp_path / "project"
    project.mkdir()
    contract(project, extras={"schemaVersion": 1, "environmentNames": ["F6_DECLARED"]})
    assert invoke(project)[0] == 0
    if reason == "environment":
        monkeypatch.setenv("F6_DECLARED", "PRIVATE_SECRET")
    elif reason == "runner":
        cheap_context["digest"] = inputs.digest("different-runtime")
    else:
        project.rename(tmp_path / "moved")
        project = tmp_path / "moved"
    rc, value = status(project, strict=True)
    assert rc == 1 and value["evidence"]["assessment"]["state"] == "context_changed"
    assert "PRIVATE_SECRET" not in json.dumps(value)


@pytest.mark.parametrize("kind", ["verification", "invariants"])
def test_real_receipt_metadata_has_no_expanded_args_or_output(tmp_path, cheap_context, monkeypatch, kind):
    monkeypatch.setenv("F6_TOKEN", "PRIVATE_SECRET")
    contract(tmp_path, ["print('$F6_TOKEN')"], kind=kind)
    rc, value = invoke(tmp_path, kind)
    assert rc == 0
    text = json.dumps(value)
    assert "PRIVATE_SECRET" not in text and str(tmp_path) not in text
    assert "$F6_TOKEN" not in text and sys.executable not in text
    row = value["receipt"]["suites" if kind == "verification" else "invariants"][0]
    assert row["output"]["stdoutBytes"] > 0


@pytest.mark.parametrize("variant", ["legacy", "future", "malformed", "duplicate", "oversized", "running", "missing_outcome", "coverage_lie", "failed"])
def test_bad_newer_attempt_never_falls_back(tmp_path, cheap_context, variant):
    contract(tmp_path)
    _, value = invoke(tmp_path)
    path = tmp_path / value["receiptPath"]
    if variant == "legacy":
        path.with_name("newer.json").write_text('{"status":"passed","stdoutTail":"PRIVATE_SECRET"}')
    elif variant == "future":
        receipt = value["receipt"]; receipt["schemaVersion"] = 999
        path.write_text(json.dumps(receipt))
    elif variant == "malformed":
        path.write_text("not-json PRIVATE_SECRET")
    elif variant == "duplicate":
        path.write_text('{"schemaVersion":2,"schemaVersion":1}')
    elif variant == "oversized":
        path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    elif variant == "failed":
        receipt = value["receipt"]
        receipt["suites"][0].update(status="failed", returnCode=4)
        receipt["status"] = "failed"
        path.write_text(json.dumps(receipt))
    else:
        receipt = value["receipt"]
        if variant == "running":
            receipt.update(status="running", executionState="running", finishedAt=None)
        elif variant == "missing_outcome":
            receipt["suites"].pop()
        else:
            receipt["selection"]["required"] = []
        path.write_text(json.dumps(receipt))
    rc, current = status(tmp_path, strict=True)
    assert rc == 1
    assert not current["evidence"]["assessment"]["currentRequiredPass"]
    assert "PRIVATE_SECRET" not in json.dumps(current)


def test_history_budget_is_unknown_read_only(tmp_path, cheap_context):
    contract(tmp_path)
    invoke(tmp_path)
    directory = tmp_path / ".controlcoding/verification_receipts"
    for i in range(129):
        (directory / f"old-{i}.json").write_text("{}")
    before = filesystem(tmp_path)
    assert status(tmp_path, strict=True)[1]["evidence"]["assessment"]["state"] == "unknown"
    assert filesystem(tmp_path) == before


def filesystem(project):
    return {str(p.relative_to(project)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in project.rglob("*") if p.is_file()}


def test_status_report_doctor_without_receipts_are_read_only(tmp_path):
    contract(tmp_path)
    contract(tmp_path, kind="invariants")
    before = filesystem(tmp_path)
    assert status(tmp_path)[0] == status(tmp_path, "invariants")[0] == 0
    cc._invariant_report_payload(tmp_path)
    cc._invariant_doctor_payload(tmp_path)
    assert filesystem(tmp_path) == before
    assert not (tmp_path / ".controlcoding").exists()


def test_real_engine_identity_measures_owned_modules(tmp_path):
    context = inputs.runner_context(ROOT / "scripts")
    assert context["complete"] and context["engineFileCount"] > 20
    unknown = inputs.runner_context(tmp_path)
    assert not unknown["complete"]


def git(project, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT="0")
    result = subprocess.run(["git", "-c", "safe.directory=" + str(project), "-c", "user.name=Fixture",
                             "-c", "user.email=fixture@example.invalid", *args], cwd=project,
                            env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_git_raw_dirty_index_head_and_filters(tmp_path, cheap_context):
    git(tmp_path, "init", "-q")
    contract(tmp_path)
    (tmp_path / "source.txt").write_bytes(b"first\r\n")
    (tmp_path / ".gitattributes").write_text("source.txt -text filter=tripwire\n")
    git(tmp_path, "add", "controlcoding.verification.json", "source.txt", ".gitattributes")
    git(tmp_path, "commit", "-qm", "fixture")
    marker = tmp_path / "FILTER_WAS_RUN"
    command = f'"{Path(sys.executable).as_posix()}" -c "from pathlib import Path; Path(\'FILTER_WAS_RUN\').touch()"'
    git(tmp_path, "config", "filter.tripwire.clean", command)
    git(tmp_path, "config", "filter.tripwire.process", command)
    git(tmp_path, "config", "core.fsmonitor", command)
    (tmp_path / "source.txt").write_bytes(b"dirty\r\n")
    index_before = (tmp_path / ".git/index").read_bytes()
    rc, value = invoke(tmp_path)
    assert rc == 0, value
    assert value["receipt"]["inputs"]["before"]["dirty"]["worktreeVsIndex"] is True
    assert status(tmp_path, strict=True)[0] == 0
    assert not marker.exists() and (tmp_path / ".git/index").read_bytes() == index_before
    git(tmp_path, "config", "--unset", "filter.tripwire.clean")
    git(tmp_path, "config", "--unset", "filter.tripwire.process")
    git(tmp_path, "config", "core.fsmonitor", "false")
    git(tmp_path, "add", "source.txt")
    assert status(tmp_path, strict=True)[0] == 1
    assert invoke(tmp_path)[0] == 0
    git(tmp_path, "commit", "-qm", "new fixture revision")
    assert status(tmp_path, strict=True)[0] == 1


def test_broken_git_is_not_an_archive(tmp_path):
    (tmp_path / ".git").mkdir()
    value = inputs.capture_inputs(tmp_path, inputs.input_policy({}))
    assert not value["complete"] and value["scopeKind"] != "archive"


def test_archive_does_not_borrow_ancestor_git(tmp_path):
    git(tmp_path, "init", "-q")
    child = tmp_path / "child"
    child.mkdir()
    value = inputs.capture_inputs(child, inputs.input_policy({}))
    assert value["complete"] and value["scopeKind"] == "archive"


@pytest.mark.parametrize("path", ["../outside", "/outside", "a/../b", ".git/config", "*.txt", "C:/secret", "a\\b"])
def test_invalid_extra_path_rejected(path):
    with pytest.raises(inputs.EvidenceError):
        inputs.input_policy({"evidenceInputs": {"schemaVersion": 1, "extraPaths": [path]}})


def test_byte_budget_and_growth_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "source"
    path.write_bytes(b"1234")
    original = os.read
    reads = []
    def growing(fd, count):
        reads.append(count)
        value = original(fd, count)
        with path.open("ab") as stream:
            stream.write(b"GROWTH")
        return value
    with inputs.SafeRoot(tmp_path) as root:
        budget = inputs.Budget(total=4, per_file=4)
        monkeypatch.setattr(inputs.os, "read", growing)
        with pytest.raises(inputs.EvidenceError, match="file_changed"):
            root.read("source", budget)
        assert sum(reads) == 4 and budget.remaining == 0


def junction(link, target):
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
    else:
        # Fixed argv, synthetic paths only; cmd is used solely for its mklink builtin.
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        assert result.returncode == 0, result.stderr


def test_parent_junction_never_reads_outside(tmp_path, monkeypatch):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir(); outside.mkdir()
    (outside / "secret").write_bytes(b"EXTERNAL_SECRET")
    junction(project / "link", outside)
    read = inputs.os.read
    touched = []
    def tracked(fd, count):
        value = read(fd, count); touched.append(value); return value
    monkeypatch.setattr(inputs.os, "read", tracked)
    with inputs.SafeRoot(project) as root:
        with pytest.raises((inputs.EvidenceError, OSError)):
            root.read("link/secret", inputs.Budget())
    assert not touched
    assert not inputs.capture_inputs(project, inputs.input_policy({}))["complete"]


def test_root_substitution_cannot_read_replacement(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    (project / "source").write_text("original")
    with inputs.SafeRoot(project) as root:
        if os.name == "nt":
            with pytest.raises(OSError):
                project.rename(tmp_path / "old")
        else:
            project.rename(tmp_path / "old")
            project.mkdir(); (project / "source").write_text("EXTERNAL")
            assert root.read("source", inputs.Budget())[0] == b"original"
            with pytest.raises(inputs.EvidenceError, match="root_changed"):
                root.validate()


def test_receipt_output_junction_rejected(tmp_path, cheap_context):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir(); outside.mkdir(); contract(project)
    (project / ".controlcoding").mkdir()
    junction(project / ".controlcoding/verification_receipts", outside)
    assert invoke(project)[0] == 1
    assert status(project, strict=True)[0] == 1
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO requires a POSIX runner")
def test_fifo_substitution_reaches_leaf_open_without_blocking(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.write_text("regular")
    original = os.open
    reached = []
    def replace_before_open(path, flags, *args, **kwargs):
        if path == "source":
            source.unlink(); os.mkfifo(source); reached.append(flags)
        return original(path, flags, *args, **kwargs)
    with inputs.SafeRoot(tmp_path) as root:
        monkeypatch.setattr(inputs.os, "open", replace_before_open)
        with pytest.raises(inputs.EvidenceError):
            root.read("source", inputs.Budget())
    assert reached and reached[0] & os.O_NONBLOCK


def test_concurrent_ids_and_running_marker_before_child(tmp_path, cheap_context, monkeypatch):
    contract(tmp_path, ["pass"])
    seen = []
    real = evidence.run_command
    def inspect(argv, **kwargs):
        markers = list((tmp_path / ".controlcoding/verification_receipts").glob("*.json"))
        running = [json.loads(p.read_text()) for p in markers if json.loads(p.read_text())["status"] == "running"]
        assert len(running) == 1
        seen.append(running[0]["id"])
        return real(argv, **kwargs)
    monkeypatch.setattr(evidence, "run_command", inspect)
    assert invoke(tmp_path)[0] == invoke(tmp_path)[0] == 0
    assert len(set(seen)) == 2


def test_child_missing_and_interrupt_are_incomplete(tmp_path, cheap_context, monkeypatch):
    value = contract(tmp_path, ["pass"])
    value["suites"][0]["command"] = "missing-F6-executable PRIVATE_SECRET"
    (tmp_path / "controlcoding.verification.json").write_text(json.dumps(value))
    rc, output = invoke(tmp_path)
    assert rc == 1 and output["status"] == "incomplete" and "PRIVATE_SECRET" not in json.dumps(output)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(evidence, "run_command", interrupt)
    contract(tmp_path, ["pass"])
    rc, output = invoke(tmp_path)
    assert rc == 130 and output["status"] == "incomplete"
    assert status(tmp_path, strict=True)[0] == 1


def test_nested_invariant_status_has_no_first_run_cycle(tmp_path, cheap_context):
    python, script = Path(sys.executable).as_posix(), (ROOT / "scripts/cc.py").as_posix()
    inv = contract(tmp_path, ["pass"], kind="invariants")
    inv["invariants"][0]["command"] = f'"{python}" -B "{script}" verify status --project-root {{project}} --json'
    (tmp_path / "controlcoding.invariants.json").write_text(json.dumps(inv))
    ver = contract(tmp_path, ["pass"])
    ver["suites"][0]["command"] = f'"{python}" -B "{script}" invariants run --project-root {{project}} --json'
    (tmp_path / "controlcoding.verification.json").write_text(json.dumps(ver))
    rc, value = invoke(tmp_path)
    assert rc == 0, value
    assert status(tmp_path, strict=True)[0] == 0


def test_cli_strict_flag_and_adopter_imports(tmp_path):
    contract(tmp_path, ["pass"])
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/cc.py"), "verify", "status",
                             "--project-root", str(tmp_path), "--require-current", "--json"], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["contractValid"] is True
    adopter = tmp_path / "adopter"; adopter.mkdir()
    for name in ("cc_evidence", "cc_evidence_inputs", "cc_evidence_process"):
        shutil.copyfile(ROOT / "scripts" / (name + ".py"), adopter / (name + ".py"))
    result = subprocess.run([sys.executable, "-B", "-c", "import cc_evidence,cc_evidence_inputs,cc_evidence_process"], cwd=adopter, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field,value", [("command", "python -c 'pass'"), ("required", False),
                                        ("kind", "regression"), ("timeoutSeconds", 42)])
def test_contract_byte_change_is_stale_even_if_commands_pass(tmp_path, cheap_context, field, value):
    raw = contract(tmp_path)
    assert invoke(tmp_path)[0] == 0
    raw["suites"][1][field] = value
    (tmp_path / "controlcoding.verification.json").write_text(json.dumps(raw))
    rc, payload = status(tmp_path, strict=True)
    assert rc == 1 and payload["evidence"]["assessment"]["state"] == "stale"


def test_atomic_begin_does_not_overwrite_collision(tmp_path):
    with inputs.SafeRoot(tmp_path) as root:
        root.write_atomic("receipts/attempt.json", {"original": True}, replace_existing=False)
        with pytest.raises(FileExistsError):
            root.write_atomic("receipts/attempt.json", {"overwritten": True}, replace_existing=False)
    assert json.loads((tmp_path / "receipts/attempt.json").read_text()) == {"original": True}
    assert [p.name for p in (tmp_path / "receipts").iterdir()] == ["attempt.json"]


@pytest.mark.parametrize("kind", ["verification", "invariants"])
@pytest.mark.parametrize("boundary", ["before_invocation", "before_acquisition"])
@pytest.mark.parametrize("existing_receipt", [False, True])
def test_attempt_acquisition_preserves_foreign_directory(
        tmp_path, monkeypatch, kind, boundary, existing_receipt, record_property):
    contract(tmp_path, ["pass"], kind=kind)
    fixed = evidence.datetime.datetime.now(evidence.datetime.timezone.utc)
    real_datetime = evidence.datetime.datetime
    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)
    chosen = evidence.uuid.UUID("00000000-0000-4000-8000-000000000015")
    monkeypatch.setattr(evidence.datetime, "datetime", Frozen)
    monkeypatch.setattr(evidence.uuid, "uuid4", lambda: chosen)
    prefix, _, folder = evidence.TYPES[kind]
    ident = prefix + "_" + fixed.strftime("%Y%m%dT%H%M%S%fZ") + "_" + chosen.hex
    relative = ".controlcoding/" + folder + "_tmp/" + ident
    directory = tmp_path / relative
    sentinel = directory / "foreign.txt"
    receipt = tmp_path / evidence._folder(kind) / (ident + ".json")
    def occupy():
        directory.mkdir(parents=True)
        sentinel.write_bytes(b"FOREIGN before acquisition")
    if existing_receipt:
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(b'{"foreign":"receipt bytes"}')
    if boundary == "before_invocation":
        occupy()
    else:
        original = inputs.SafeRoot.directory
        @contextlib.contextmanager
        def collide(self, path="", **kwargs):
            if path == relative and kwargs.get("create"):
                occupy()
            with original(self, path, **kwargs) as held:
                yield held
        monkeypatch.setattr(inputs.SafeRoot, "directory", collide)
    executed = []
    def forbidden(*args, **kwargs):
        executed.append(True)
        raise AssertionError("foreign attempt must not execute commands")
    monkeypatch.setattr(evidence, "run_command", forbidden)
    code, result = invoke(tmp_path, kind)
    strict_code, current = status(tmp_path, kind, strict=True)
    record_property("acquisition", json.dumps({
        "code": code, "status": result["status"], "executed": executed,
        "foreignExists": sentinel.exists(), "strictCode": strict_code,
        "assessment": current["evidence"]["assessment"]}))
    assert sentinel.read_bytes() == b"FOREIGN before acquisition"
    assert not executed
    assert code == 1 and result["status"] == "incomplete"
    assert result["temporaryCleanupPending"] is False
    assert strict_code == 1 and not current["evidence"]["assessment"]["currentRequiredPass"]
    if existing_receipt:
        assert receipt.read_bytes() == b'{"foreign":"receipt bytes"}'
    else:
        assert not receipt.exists()


@pytest.mark.parametrize("exit_path", ["normal", "collision", "body_error"])
def test_exclusive_directory_releases_handles(tmp_path, monkeypatch, exit_path):
    opened, closed = [], []
    if os.name == "nt":
        original_open = inputs.SafeRoot._win_open
        def track_open(self, *args, **kwargs):
            handle = original_open(self, *args, **kwargs)
            original_close = self._close
            def track_close(value):
                closed.append(value)
                return original_close(value)
            self._close = track_close
            opened.append(handle)
            return handle
        monkeypatch.setattr(inputs.SafeRoot, "_win_open", track_open)
    else:
        original_open, original_close = os.open, os.close
        def track_open(*args, **kwargs):
            handle = original_open(*args, **kwargs)
            opened.append(handle)
            return handle
        def track_close(handle):
            closed.append(handle)
            return original_close(handle)
        monkeypatch.setattr(os, "open", track_open)
        monkeypatch.setattr(os, "close", track_close)
    (tmp_path / "shared").mkdir()
    if exit_path == "collision":
        (tmp_path / "shared/attempt").mkdir()
    with contextlib.ExitStack() as stack:
        if exit_path != "normal":
            stack.enter_context(pytest.raises(inputs.EvidenceError if exit_path == "collision" else RuntimeError))
        with inputs.SafeRoot(tmp_path) as root:
            with root.directory("shared/attempt", create=True, exclusive=True):
                if exit_path == "body_error":
                    raise RuntimeError("fixture body failure")
    assert opened and sorted(opened) == sorted(closed)


def test_real_timeout_is_incomplete_and_strictly_rejected(tmp_path, record_property):
    kind = "verification"
    value = contract(tmp_path, ["import time; time.sleep(30)", "pass"], kind=kind)
    key = "suites" if kind == "verification" else "invariants"
    value[key][0]["timeoutSeconds"] = 1
    (tmp_path / f"controlcoding.{kind}.json").write_text(json.dumps(value))
    code, result = invoke(tmp_path, kind)
    receipt = result["receipt"]
    row = receipt[key][0]
    record_property("timeout_receipt", json.dumps(receipt))
    assert code == 1 and receipt["status"] == "incomplete"
    assert row["error"] == "timeout" and row["returnCode"] is not None
    assert 1000 <= row["durationMs"] < 5000
    assert len(receipt[key]) == 1
    assert receipt["selection"]["omitted"] == ["check-1"]
    assert not (tmp_path / receipt["tempDir"]).exists()
    assert json.loads((tmp_path / result["receiptPath"]).read_text())["status"] == "incomplete"
    strict_code, current = status(tmp_path, kind, strict=True)
    assert strict_code == 1
    assert "attempt_incomplete" in current["evidence"]["assessment"]["reasons"]


def test_unexpected_execution_exception_finalizes_without_secret(tmp_path, cheap_context, monkeypatch):
    contract(tmp_path)
    def unexpected(*args, **kwargs):
        raise RuntimeError("PRIVATE_EXCEPTION_DETAIL")
    monkeypatch.setattr(evidence, "run_command", unexpected)
    code, value = invoke(tmp_path)
    assert code == 1 and value["status"] == "incomplete"
    assert value["receipt"]["executionState"] == "error"
    assert "PRIVATE_EXCEPTION_DETAIL" not in json.dumps(value)


def test_zero_byte_over_budget_is_not_read(tmp_path, monkeypatch):
    (tmp_path / "large").write_bytes(b"0123456789")
    def forbidden(*args):
        pytest.fail("oversize content was read")
    with inputs.SafeRoot(tmp_path) as root:
        monkeypatch.setattr(inputs.os, "read", forbidden)
        with pytest.raises(inputs.EvidenceError, match="byte_limit"):
            root.read("large", inputs.Budget(per_file=4))


def test_inventory_entry_and_time_limits_are_incomplete(tmp_path):
    (tmp_path / "source").write_text("fixture")
    for budget in (inputs.Budget(entries=0), inputs.Budget(seconds=-1)):
        assert not inputs.capture_inputs(tmp_path, inputs.input_policy({}), budget=budget)["complete"]


def test_parent_swap_rejected_before_external_read(tmp_path, monkeypatch):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir(); outside.mkdir(); (project / "parent").mkdir()
    (project / "parent/source").write_text("original")
    (outside / "source").write_text("EXTERNAL")
    with inputs.SafeRoot(project) as root:
        original = root.directory
        changed = []
        @contextlib.contextmanager
        def swap(relative="", **kwargs):
            if relative == "parent" and not changed:
                (project / "parent").rename(project / "former")
                junction(project / "parent", outside)
                changed.append(True)
            with original(relative, **kwargs) as result:
                yield result
        monkeypatch.setattr(root, "directory", swap)
        with pytest.raises((OSError, inputs.EvidenceError)):
            root.read("parent/source", inputs.Budget())
    assert changed


@pytest.mark.parametrize("directory", [False, True])
def test_real_symlink_policy_is_rejection(tmp_path, directory):
    outside = tmp_path / "outside"
    if directory:
        outside.mkdir(); (outside / "source").write_text("EXTERNAL")
    else:
        outside.write_text("EXTERNAL")
    project = tmp_path / "project"; project.mkdir()
    link = project / "link"
    try:
        link.symlink_to(outside, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and exc.winerror == 1314:
            pytest.skip("Windows symlink creation unavailable: WinError 1314")
        raise
    with inputs.SafeRoot(project) as root:
        with pytest.raises((OSError, inputs.EvidenceError)):
            root.read("link/source" if directory else "link", inputs.Budget())


def test_unmerged_index_is_incomplete(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "source").write_text("one")
    git(tmp_path, "add", "source"); git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "side")
    (tmp_path / "source").write_text("two")
    git(tmp_path, "commit", "-qam", "side")
    git(tmp_path, "checkout", "-q", "-")
    (tmp_path / "source").write_text("three")
    git(tmp_path, "commit", "-qam", "main")
    # A merge conflict is an intentional fixture state, not an active-repo mutation.
    try:
        git(tmp_path, "merge", "side", "--no-edit")
    except AssertionError:
        pass
    assert git(tmp_path, "ls-files", "--unmerged")
    value = inputs.capture_inputs(tmp_path, inputs.input_policy({}))
    assert not value["complete"] and value["reasons"] == ["git_index_unsupported"]


def test_git_inventory_names_are_nul_delimited(tmp_path):
    git(tmp_path, "init", "-q")
    name = "space name.txt" if os.name == "nt" else "newline\nname.txt"
    (tmp_path / name).write_text("one")
    first = inputs.capture_inputs(tmp_path, inputs.input_policy({}))
    assert first["complete"]
    (tmp_path / name).write_text("two")
    second = inputs.capture_inputs(tmp_path, inputs.input_policy({}))
    assert second["complete"] and first["contentDigest"] != second["contentDigest"]


def test_status_and_report_omit_literal_command_credentials(tmp_path, cheap_context):
    contract(tmp_path, ["print('PRIVATE_COMMAND_SECRET')"])
    contract(tmp_path, ["print('PRIVATE_COMMAND_SECRET')"], kind="invariants")
    for kind in ("verification", "invariants"):
        assert invoke(tmp_path, kind)[0] == 0
        assert "PRIVATE_COMMAND_SECRET" not in json.dumps(status(tmp_path, kind)[1])
    assert "PRIVATE_COMMAND_SECRET" not in json.dumps(cc._invariant_report_payload(tmp_path))
    assert "PRIVATE_COMMAND_SECRET" not in json.dumps(cc._invariant_doctor_payload(tmp_path))


def test_contract_changed_between_parse_and_snapshot_cannot_execute(tmp_path, cheap_context, monkeypatch):
    contract(tmp_path)
    real = inputs.capture_inputs
    def change(project, policy, **kwargs):
        contract(project, ["print('changed')"])
        return real(project, policy, **kwargs)
    monkeypatch.setattr(inputs, "capture_inputs", change)
    def forbidden(*args, **kwargs):
        pytest.fail("old command executed with new contract identity")
    monkeypatch.setattr(evidence, "run_command", forbidden)
    code, value = invoke(tmp_path)
    assert code == 1 and value["receipt"]["suites"] == []


def test_runner_source_digest_changes_with_owned_module(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    scripts = tmp_path / "scripts"; scripts.mkdir()
    source = scripts / "cc.py"; source.write_text("VALUE=1\n")
    before = inputs.runner_context(scripts)
    source.write_text("VALUE=2\n")
    after = inputs.runner_context(scripts)
    assert before["complete"] and after["complete"]
    assert before["engineDigest"] != after["engineDigest"]


def test_abrupt_death_leaves_running_receipt(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    contract(project, ["pass"])
    code = (
        f"import sys,os; sys.path.insert(0,{str(ROOT / 'scripts')!r}); import cc,cc_evidence; "
        "from pathlib import Path; cc_evidence.run_command=lambda *a,**k: os._exit(17); "
        f"cc.cmd_verify_run(Path({str(project)!r}),json_output=True)"
    )
    child = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, capture_output=True, timeout=30)
    assert child.returncode == 17
    receipts = list((project / ".controlcoding/verification_receipts").glob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["executionState"] == "running"
    assert status(project, strict=True)[0] == 1


def test_real_simultaneous_attempts_have_separate_receipts(tmp_path, record_property):
    project = tmp_path / "project"; project.mkdir()
    contract(project, ["import time; time.sleep(0.2)"])
    argv = [sys.executable, "-B", str(ROOT / "scripts/cc.py"), "verify", "run", "--project-root", str(project), "--json"]
    children = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    results = []
    try:
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
    assert len({r["receiptPath"] for r in results}) == 2
    local_code, local_status = status(project, strict=True)
    child = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/cc.py"),
                            "verify", "status", "--project-root", str(project),
                            "--require-current", "--json"], capture_output=True, text=True, timeout=30)
    cli_status = json.loads(child.stdout)
    diagnostic = {"receipts": results, "inProcess": local_status, "cli": cli_status,
                  "currentRunner": inputs.runner_context(ROOT / "scripts")}
    record_property("concurrent_receipt_assessments", json.dumps(diagnostic))
    assert child.returncode == 0, diagnostic
    assert local_code == 0, diagnostic


@pytest.mark.parametrize("command", ["verify", "invariants"])
def test_cli_does_not_resolve_away_root_junction(tmp_path, command):
    outside = tmp_path / "outside"; outside.mkdir()
    contract(outside, kind="verification" if command == "verify" else "invariants")
    link = tmp_path / "alias"; junction(link, outside)
    child = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/cc.py"), command, "status",
                            "--project-root", str(link), "--json"], capture_output=True, text=True)
    assert child.returncode == 1
    assert json.loads(child.stdout)["evidence"]["assessment"]["currentRequiredPass"] is False


def test_suite_receives_local_git_configuration(tmp_path, cheap_context):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "evidence.fixture", "preserved")
    code = "import subprocess; assert subprocess.check_output(['git','config','--get','evidence.fixture']).strip()==b'preserved'"
    contract(tmp_path, [code])
    assert invoke(tmp_path)[0] == 0


def test_older_completion_cannot_hide_subset_in_mixed_history(tmp_path, cheap_context):
    contract(tmp_path)
    _, full = invoke(tmp_path)
    _, partial = invoke(tmp_path, suite_ids=["check-0"])
    directory = tmp_path / ".controlcoding/verification_receipts"
    legacy = directory / "old-legacy.json"
    legacy.write_text('{"status":"passed"}')
    os.utime(legacy, (1, 1))
    # Simulate a long-running older full attempt finishing after the subset.
    os.utime(tmp_path / full["receiptPath"], None)
    value = status(tmp_path, strict=True)[1]
    assert not value["evidence"]["assessment"]["currentRequiredPass"]
    assert value["evidence"]["latest"]["id"] == partial["receipt"]["id"]


def test_launched_executable_matches_resolved_context(tmp_path, cheap_context, monkeypatch):
    value = contract(tmp_path, ["pass"])
    value["suites"][0]["command"] = "python -B -c pass"
    (tmp_path / "controlcoding.verification.json").write_text(json.dumps(value))
    real = evidence.run_command
    seen = []
    def inspect(argv, **kwargs):
        assert Path(argv[0]).is_absolute()
        assert argv[0] == inputs.executable_argv(["python"], tmp_path)[0]
        seen.append(argv[0])
        return real(argv, **kwargs)
    monkeypatch.setattr(evidence, "run_command", inspect)
    assert invoke(tmp_path)[0] == 0
    assert len(seen) == 1 and status(tmp_path, strict=True)[0] == 0


def test_unselected_missing_optional_executable_does_not_block_required_run(tmp_path, cheap_context):
    value = contract(tmp_path, ["pass"])
    value["suites"].append({"id": "optional", "kind": "targeted", "required": False,
                            "command": "nonexistent-F6-executable"})
    (tmp_path / "controlcoding.verification.json").write_text(json.dumps(value))
    assert invoke(tmp_path)[0] == 0 and status(tmp_path, strict=True)[0] == 0
    code, payload = invoke(tmp_path, all_suites=True)
    assert code == 1 and payload["status"] == "incomplete"
    assert "executable_unresolved" in payload["receipt"]["errors"]


def test_runner_dependency_discovery_deduplicates_search_locations(tmp_path, monkeypatch):
    metadata = tmp_path / "h15_fixture-1.0.dist-info"
    metadata.mkdir()
    description = metadata / "METADATA"
    description.write_text("Metadata-Version: 2.1\nName: h15-fixture\nVersion: 1.0\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    before = inputs.runner_context(ROOT / "scripts")
    monkeypatch.syspath_prepend(str(tmp_path / "."))
    repeated = inputs.runner_context(ROOT / "scripts")
    assert before["complete"] and repeated == before
    description.write_text("Metadata-Version: 2.1\nName: h15-fixture\nVersion: 2.0\n")
    changed = inputs.runner_context(ROOT / "scripts")
    assert changed["complete"] and changed["dependenciesDigest"] != before["dependenciesDigest"]
    other = tmp_path / "other"
    other.mkdir()
    duplicate = other / metadata.name
    duplicate.mkdir()
    (duplicate / "METADATA").write_bytes(description.read_bytes())
    monkeypatch.syspath_prepend(str(other))
    distinct = inputs.runner_context(ROOT / "scripts")
    assert distinct["complete"] and distinct["dependencyCount"] == changed["dependencyCount"] + 1


def test_safe_cleanup_removes_owned_readonly_file(tmp_path):
    directory = tmp_path / "owned"
    directory.mkdir()
    target = directory / "object"
    target.write_bytes(b"owned")
    target.chmod(stat.S_IREAD)
    with inputs.SafeRoot(tmp_path) as root:
        root.remove_tree("owned")
    assert not directory.exists()


def test_safe_cleanup_preserves_replacement_observed_after_listing(tmp_path, monkeypatch):
    directory = tmp_path / "owned"
    directory.mkdir()
    target = directory / "object"
    target.write_bytes(b"original")
    real_stat = inputs.SafeRoot._stat
    real_remove = getattr(inputs.SafeRoot, "_win_remove_file", None)
    changed = []
    observations = []
    def replace():
        target.unlink()
        target.write_bytes(b"FOREIGN replacement bytes")
        changed.append(True)
    def observed(self, parent, name):
        if name == "object":
            observations.append(name)
            if len(observations) == 2:
                replace()
        return real_stat(self, parent, name)
    def remove(self, parent, name, expected):
        replace()
        return real_remove(self, parent, name, expected)
    if os.name == "nt" and real_remove is not None:
        monkeypatch.setattr(inputs.SafeRoot, "_win_remove_file", remove)
    else:
        monkeypatch.setattr(inputs.SafeRoot, "_stat", observed)
    with inputs.SafeRoot(tmp_path) as root, pytest.raises(inputs.EvidenceError, match="cleanup_entry_changed"):
        root.remove_tree("owned")
    assert changed and target.read_bytes() == b"FOREIGN replacement bytes"


def test_safe_cleanup_budget_failure_keeps_unvisited_files(tmp_path, monkeypatch):
    directory = tmp_path / "owned"
    directory.mkdir()
    for name in ("one", "two"):
        (directory / name).write_bytes(b"preserved")
    real = inputs.Budget
    monkeypatch.setattr(inputs, "Budget", lambda: real(entries=1))
    with inputs.SafeRoot(tmp_path) as root, pytest.raises(inputs.EvidenceError, match="entry_limit"):
        root.remove_tree("owned")
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == {
        "one": b"preserved", "two": b"preserved"}


def test_cleanup_failure_has_stable_reason_and_never_current_pass(tmp_path, cheap_context, monkeypatch):
    contract(tmp_path, ["pass"])
    def refuse(*args, **kwargs):
        raise inputs.EvidenceError("cleanup_unsafe_entry")
    monkeypatch.setattr(inputs.SafeRoot, "remove_tree", refuse)
    code, result = invoke(tmp_path)
    assert code == 1 and result["status"] == "incomplete"
    assert result["receipt"]["errors"] == ["temp_cleanup_failed", "temp_cleanup_cleanup_unsafe_entry"]
    assert status(tmp_path, strict=True)[0] == 1


def test_safe_cleanup_refuses_link_to_foreign_directory(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "sentinel"
    sentinel.write_bytes(b"FOREIGN")
    link = owned / "link"
    if os.name == "nt":
        junction(link, foreign)
    else:
        link.symlink_to(foreign, target_is_directory=True)
    with inputs.SafeRoot(tmp_path) as root, pytest.raises(inputs.EvidenceError, match="cleanup_unsafe_entry"):
        root.remove_tree("owned")
    assert sentinel.read_bytes() == b"FOREIGN" and link.is_dir()


def test_safe_cleanup_does_not_change_foreign_hardlink_metadata(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"FOREIGN hardlink")
    target = owned / "link"
    os.link(foreign, target)
    foreign.chmod(stat.S_IREAD)
    before = foreign.stat()
    with inputs.SafeRoot(tmp_path) as root:
        if os.name == "nt":
            with pytest.raises(inputs.EvidenceError, match="cleanup_shared_file"):
                root.remove_tree("owned")
            assert target.exists()
        else:
            root.remove_tree("owned")
    assert foreign.read_bytes() == b"FOREIGN hardlink"
    assert foreign.stat().st_mode == before.st_mode


def test_safe_cleanup_preserves_replaced_attempt_root(tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    expected = owned.stat()
    owned.rename(tmp_path / "displaced")
    owned.mkdir()
    (owned / "foreign").write_bytes(b"FOREIGN replacement root")
    with inputs.SafeRoot(tmp_path) as root, pytest.raises(inputs.EvidenceError, match="cleanup_entry_changed"):
        root.remove_tree("owned", expected=expected)
    assert (owned / "foreign").read_bytes() == b"FOREIGN replacement root"


def test_lifecycle_preserves_replaced_attempt_root(tmp_path, cheap_context, monkeypatch):
    contract(tmp_path, ["pass"])
    real = evidence.run_command
    replacements = []
    def execute(*args, **kwargs):
        directory = next((tmp_path / ".controlcoding/verification_tmp").iterdir())
        directory.rename(tmp_path / "displaced-attempt")
        directory.mkdir()
        (directory / "foreign").write_bytes(b"FOREIGN attempt")
        replacements.append(directory)
        return real(*args, **kwargs)
    monkeypatch.setattr(evidence, "run_command", execute)
    code, result = invoke(tmp_path)
    assert code == 1 and result["status"] == "incomplete"
    assert "temp_cleanup_cleanup_entry_changed" in result["receipt"]["errors"]
    assert len(replacements) == 1 and (replacements[0] / "foreign").read_bytes() == b"FOREIGN attempt"
    assert status(tmp_path, strict=True)[0] == 1
