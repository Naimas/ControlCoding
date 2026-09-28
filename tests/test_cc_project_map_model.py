"""Observable Project Map contract and adversarial boundary tests."""

import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_project_map_model as model


def node(identifier, kind="component", **changes):
    result = {"id": identifier, "kind": kind, "title": identifier,
              "presence": "both", "origin": "adapter", "mapping": "confirmed",
              "sources": ["src"], "lifecycle": {"owner": "adapter", "state": "accepted"},
              "plan": "approved", "criteria": [] if kind == "criterion" else ["check"],
              "reason": "Supplied by the test adapter"}
    result.update(changes)
    return result


def assessment(subject, **changes):
    result = {"id": "a-" + subject, "subject": subject, "sources": ["src"],
              "outcome": "passed", "freshness": "current", "applicability": "applicable",
              "sufficiency": "complete", "coverage": "complete", "reason": "Required set assessed"}
    result.update(changes)
    return result


def edge(source, target, relation="depends_on", **changes):
    result = {"id": source + "-" + relation + "-" + target, "source": source, "target": target,
              "relation": relation, "origin": "adapter", "mapping": "confirmed", "sources": ["src"]}
    result.update(changes)
    return result


def constraint(identifier="guard", **changes):
    result = {"id": identifier, "subject": "unit", "kind": "policy", "operation": "edit",
              "scope": "scripts/example.py", "rule": "protected-source", "sources": ["src"],
              "applicability": "applicable", "coverage": "supported", "stage": "observed",
              "decision": "deny", "host": "fixture-host", "context": "fixture-context",
              "reason": "Operation denied for this scope", "responsible": "project-owner",
              "condition": "Obtain the required scoped authorization"}
    result.update(changes)
    return result


@pytest.fixture
def bundle():
    # These are explicitly demo records, never observations of the live project.
    return {"schema_version": 1, "mode": "demo", "project_id": "test", "snapshot_id": "snap",
            "observed_at": "2026-09-21T10:00:00Z",
            "coverage": {"state": "complete", "scope": ["unit"], "omissions": [], "reason": "Fixture scope"},
            "selection": {"kind": "component", "nodes": ["unit"]},
            "sources": [{"id": "src", "owner": "evidence", "locator": {"path": "docs/design.md", "fragment": "criterion"},
                         "identity": "sha256:" + "a" * 64, "adapter": "fixture-v1", "resolution": "resolved",
                         "reason": "Illustrative source", "synthetic": True}],
            "nodes": [node("unit"), node("check", "criterion")], "edges": [],
            "assessments": [assessment("unit"), assessment("check")], "constraints": []}


def status(bundle, identifier="unit", **kwargs):
    projection = model.build_map_projection(bundle, **kwargs)
    return next(s for s in projection["statuses"] if s["id"] == identifier)


def fails(bundle, code=None, **kwargs):
    with pytest.raises(model.MapValidationError) as caught:
        model.build_map_projection(bundle, **kwargs)
    if code:
        assert caught.value.code == code
    return caught.value


def test_accepted_protected_and_explanation(bundle):
    bundle["constraints"] = [constraint(), constraint("approval", kind="approval", decision="approval_required")]
    projection = model.build_map_projection(bundle)
    assert projection["canonical"] is False and projection["mode"] == "demo"
    assert projection["summary"] == {"kind": "component", "units": 1, "verified_units": 1,
                                      "criteria": 1, "verified_criteria": 1, "coverage": "complete",
                                      "declared_scope_verified": True}
    detail = model.explain_map_node(projection, "unit")
    assert detail["status"]["verified_accepted"] is True
    assert {c["effective_observation"] for c in detail["status"]["constraints"]} == {"deny", "approval_required"}
    assert detail["constraints"] == projection["bundle"]["constraints"]
    assert detail["sources"][0]["locator"]["fragment"] == "criterion"


@pytest.mark.parametrize("raw,display,impediment", [
    ("planned", "planned", False), ("active", "active", False), ("verifying", "active", False),
    ("passing", "review", False), ("blocked", "active", True), ("completed", "accepted", False),
    ("aborted", "aborted", False)])
def test_feature_lifecycle(bundle, raw, display, impediment):
    bundle["nodes"][0].update(kind="feature", lifecycle={"owner": "feature", "state": raw})
    bundle["selection"]["kind"] = "feature"
    actual = status(bundle)
    assert actual["delivery"] == display
    assert bool(actual["impediments"]) is impediment
    assert actual["reported_lifecycle"]["state"] == raw
    assert actual["verified_accepted"] is (raw == "completed")


@pytest.mark.parametrize("field,value", [
    ("outcome", "failed"), ("outcome", "incomplete"), ("outcome", "unknown"),
    ("outcome", "not_run"), ("outcome", "partial"), ("outcome", "unsupported"),
    ("freshness", "stale"), ("freshness", "unknown"), ("sufficiency", "subset"),
    ("sufficiency", "unknown"), ("coverage", "partial"), ("coverage", "unknown"),
    ("applicability", "unknown"), ("applicability", "not_applicable")])
def test_insufficient_evidence_never_green(bundle, field, value):
    bundle["assessments"][0][field] = value
    actual = status(bundle)
    assert not actual["verified_accepted"]
    assert not actual["verification"]["sufficient_current"]
    assert actual["delivery"] == "accepted"


def test_criteria_and_conflicting_assessments(bundle):
    bundle["assessments"][1]["outcome"] = "failed"
    assert "criteria_unsatisfied" in status(bundle)["reasons"]
    bundle["assessments"][1]["outcome"] = "passed"
    bundle["assessments"].append(assessment("unit", id="other", outcome="failed"))
    assert not status(bundle)["verified_accepted"]
    bundle["assessments"][-1]["applicability"] = "not_applicable"
    assert status(bundle)["verified_accepted"]


@pytest.mark.parametrize("presence", ["intended", "missing", "unknown"])
def test_confirmed_design_is_not_implementation(bundle, presence):
    bundle["nodes"][0].update(origin="ai", presence=presence, plan="detailed")
    actual = status(bundle)
    assert not actual["verified_accepted"]
    assert "implementation_not_observed" in actual["reasons"]
    assert actual["plan"] == "detailed"


def test_code_only_requires_criteria_and_acceptance(bundle):
    bundle["nodes"][0].update(origin="code", presence="observed", criteria=[], plan="absent",
                               lifecycle={"owner": "adapter", "state": "unknown"})
    actual = status(bundle)
    assert actual["presence"] == "observed" and actual["delivery"] == "unknown"
    assert set(actual["reasons"]) == {"delivery_not_accepted", "criteria_unmapped"}


@pytest.mark.parametrize("mapping", ["proposed", "rejected", "conflicted"])
def test_mapping_is_independent(bundle, mapping):
    bundle["nodes"][0]["mapping"] = mapping
    before = status(bundle)
    bundle["nodes"][0]["mapping"] = "confirmed"
    after = status(bundle)
    assert not before["verified_accepted"] and after["verified_accepted"]
    for dimension in ("reported_lifecycle", "verification", "constraints"):
        assert before[dimension] == after[dimension]
    bundle["assessments"] = []
    assert not status(bundle)["verified_accepted"]


@pytest.mark.parametrize("field,value", [("stage", "configured"), ("stage", "predicted"),
    ("coverage", "unknown"), ("coverage", "unsupported"), ("coverage", "conflicting"),
    ("applicability", "unknown"), ("applicability", "not_applicable")])
def test_constraints_never_infer_observed_denial(bundle, field, value):
    bundle["constraints"] = [constraint(**{field: value})]
    actual = status(bundle)
    assert actual["verified_accepted"]
    assert actual["constraints"] == [{"id": "guard", "effective_observation": "unknown"}]
    assert model.explain_map_node(model.build_map_projection(bundle), "unit")["constraints"][0][field] == value


def test_constraints_do_not_propagate_or_override_each_other(bundle):
    bundle["nodes"].append(node("child"))
    bundle["edges"] = [edge("unit", "child", "contains_structure")]
    bundle["constraints"] = [constraint(), constraint("other", decision="allow", operation="read")]
    assert status(bundle, "child")["constraints"] == []
    assert len(status(bundle)["constraints"]) == 2


def test_many_to_many_work_and_shared_criteria_count_once(bundle):
    bundle["nodes"] += [node("second"), node("feature", "feature"), node("phase", "phase"), node("subphase", "phase")]
    bundle["edges"] = [edge("unit", "feature", "implements"), edge("second", "feature", "implements"),
                       edge("feature", "phase", "contains_work"), edge("phase", "subphase", "contains_work")]
    bundle["assessments"].append(assessment("second"))
    bundle["selection"]["nodes"].append("second")
    bundle["coverage"]["scope"].append("second")
    projection = model.build_map_projection(bundle)
    assert projection["summary"]["units"] == 2
    assert projection["summary"]["verified_units"] == 2
    assert projection["summary"]["criteria"] == 1
    assert model.explain_map_node(projection, "subphase")["ancestors"] == ["phase", "feature"]
    assert len(model.explain_map_node(projection, "feature")["edges"]) == 3
    bundle["selection"]["nodes"].append("check")
    bundle["coverage"]["scope"].append("check")
    fails(bundle, "granularity")


@pytest.mark.parametrize("state", ["partial", "unknown"])
def test_coverage_prevents_scope_success_but_preserves_observation(bundle, state):
    bundle["coverage"].update(state=state, omissions=["Unscanned service"])
    projection = model.build_map_projection(bundle)
    assert status(bundle)["verified_accepted"]
    assert not projection["summary"]["declared_scope_verified"]


def test_selection_not_entire_declared_scope_and_empty_scope(bundle):
    bundle["coverage"]["scope"].append("check")
    assert not model.build_map_projection(bundle)["summary"]["declared_scope_verified"]
    bundle["coverage"]["scope"] = []
    bundle["selection"]["nodes"] = []
    assert not model.build_map_projection(bundle)["summary"]["declared_scope_verified"]


def test_dependency_cycle_preserved_with_finite_detail(bundle):
    bundle["nodes"] += [node("second"), node("third")]
    bundle["edges"] = [edge("unit", "second"), edge("second", "unit"), edge("third", "third")]
    projection = model.build_map_projection(bundle)
    assert projection["diagnostics"] == [{"code": "dependency_cycle", "members": ["second", "unit"]},
                                         {"code": "dependency_cycle", "members": ["third"]}]
    assert len(projection["bundle"]["edges"]) == 3
    detail = model.explain_map_node(projection, "unit")
    assert len(detail["edges"]) == 2 and len(detail["diagnostics"]) == 1
    assert len(json.dumps(detail)) < 10000


@pytest.mark.parametrize("case", ["parent", "cycle", "orphan", "cross", "implements", "criterion"])
def test_invalid_relationships(bundle, case):
    bundle["nodes"] += [node("second"), node("phase", "phase")]
    if case == "parent":
        bundle["edges"] = [edge("unit", "second", "contains_structure"), edge("second", "second", "contains_structure")]
    elif case == "cycle":
        bundle["edges"] = [edge("unit", "second", "contains_structure"), edge("second", "unit", "contains_structure")]
    elif case == "orphan":
        bundle["edges"] = [edge("unit", "foreign-project-node")]
    elif case == "cross":
        bundle["edges"] = [edge("phase", "unit", "contains_work")]
    elif case == "implements":
        bundle["edges"] = [edge("phase", "unit", "implements")]
    else:
        bundle["edges"] = [edge("unit", "second", "verified_by")]
    fails(bundle)


def test_unresolved_source_is_explicit_unknown(bundle):
    bundle["sources"][0].update(identity=None, resolution="unresolved", reason="Source unavailable")
    projection = model.build_map_projection(bundle)
    assert projection["diagnostics"] == [{"code": "source_unknown", "members": ["src"]}]
    assert not status(bundle)["verified_accepted"]
    assert model.explain_map_node(projection, "unit")["sources"][0]["reason"] == "Source unavailable"
    bundle["nodes"][0]["sources"] = ["not-declared"]
    fails(bundle, "reference")


@pytest.mark.parametrize("target", ["sources", "nodes", "edges"])
def test_synthetic_rejected_in_observation(bundle, target):
    bundle["mode"] = "observation"
    bundle["sources"][0]["synthetic"] = target == "sources"
    if target == "nodes":
        bundle["nodes"][0]["origin"] = "synthetic"
    if target == "edges":
        bundle["edges"] = [edge("unit", "check", origin="synthetic")]
    fails(bundle, "synthetic")


def test_observation_is_noncanonical_and_not_an_authentication_claim(bundle):
    bundle["mode"] = "observation"
    bundle["sources"][0]["synthetic"] = False
    assert model.build_map_projection(bundle)["canonical"] is False


@pytest.mark.parametrize("path", ["/abs", "C:/x", "C:x", "//host/share", "a\\b", "../x", "a/../b", "a/./b", "a//b", "a/", "a:stream", "a\x00b", "a\nb", "a. /x", "a./x"])
def test_hostile_paths(bundle, path):
    bundle["sources"][0]["locator"]["path"] = path
    fails(bundle)


def test_strings_remain_data_and_errors_do_not_echo_input(bundle):
    bundle["nodes"][0]["title"] = '<img src=x onerror="alert(1)">'
    assert model.explain_map_node(model.build_map_projection(bundle), "unit")["node"]["title"].startswith("<img")
    bundle["sources"][0]["locator"]["path"] = "../secret-token"
    assert "secret-token" not in str(fails(bundle))


@pytest.mark.parametrize("case", ["version", "bool", "float", "nan", "inf", "extra", "missing", "state", "kind", "id", "dupe", "unicode", "time", "source", "criteria", "omission"])
def test_strict_schema(bundle, case):
    if case in ("version", "bool", "float", "nan", "inf"):
        bundle["schema_version"] = {"version": 2, "bool": True, "float": 1.0, "nan": float("nan"), "inf": float("inf")}[case]
    elif case == "extra":
        bundle["nodes"][0]["command"] = "must not execute"
    elif case == "missing":
        del bundle["coverage"]["reason"]
    elif case == "state":
        bundle["nodes"][0]["lifecycle"]["state"] = "done"
    elif case == "kind":
        bundle["nodes"][0]["kind"] = "arbitrary"
    elif case == "id":
        bundle["nodes"][0]["id"] = "../node"
    elif case == "dupe":
        bundle["nodes"].append(deepcopy(bundle["nodes"][0]))
    elif case == "unicode":
        bundle["nodes"][0]["title"] = "\ud800"
    elif case == "time":
        bundle["observed_at"] = "2026-02-30T00:00:00Z"
    elif case == "source":
        bundle["sources"][0].update(resolution="unresolved")
    elif case == "criteria":
        bundle["nodes"][1]["criteria"] = ["check"]
    else:
        bundle["coverage"]["omissions"] = ["Missing"]
    fails(bundle)


def test_decoded_input_types_cycles_and_depth(bundle):
    class Hostile(dict):
        def items(self):
            pytest.fail("Custom methods must not run")
    fails(Hostile(bundle), "type")
    bundle["extra"] = bundle
    fails(bundle, "cycle")
    del bundle["extra"]
    nested = []
    bundle["extra"] = nested
    for _ in range(25):
        child = []
        nested.append(child)
        nested = child
    fails(bundle, "limit")


@pytest.mark.parametrize("changes", [
    {"max_input_bytes": 50}, {"max_output_bytes": 50}, {"max_nodes": 1},
    {"max_source_refs": 1}, {"max_assessments": 1}, {"max_values": 10},
    {"max_depth": 2}, {"max_id": 2}, {"max_title": 2}, {"max_path": 2}, {"max_text": 20}])
def test_tightened_bounds(bundle, changes):
    fails(bundle, policy=replace(model.MapPolicy(), **changes))


def test_policy_is_immutable_and_cannot_relax_limits(bundle):
    with pytest.raises(FrozenInstanceError):
        model.MapPolicy().max_nodes = 9999
    for value in (0, True, 2001):
        fails(bundle, "policy", policy=replace(model.MapPolicy(), max_nodes=value))
    fails(bundle, "policy", policy={})


@pytest.mark.parametrize("group,bound", [("sources", "max_sources"), ("edges", "max_edges"),
                                           ("constraints", "max_constraints")])
def test_record_count_bounds(bundle, group, bound):
    if group == "edges":
        bundle[group] = [edge("unit", "check"), edge("check", "unit")]
    elif group == "constraints":
        bundle[group] = [constraint(), constraint("other")]
    else:
        bundle[group].append(dict(bundle[group][0], id="other"))
    fails(bundle, "limit", policy=replace(model.MapPolicy(), **{bound: 1}))


@pytest.mark.parametrize("group", ["sources", "edges", "assessments", "constraints"])
def test_duplicate_record_ids(bundle, group):
    if group == "edges":
        bundle[group] = [edge("unit", "check")]
    if group == "constraints":
        bundle[group] = [constraint()]
    bundle[group].append(deepcopy(bundle[group][0]))
    fails(bundle, "duplicate")


def test_graph_and_reference_boundaries(bundle):
    bundle["nodes"] = [node(f"n{i}", "phase", criteria=[], sources=[]) for i in range(2001)]
    fails(bundle, "limit")


def test_budget_reserves_pending_values_before_expansion(bundle):
    # Aliased containers are legal JSON input, but count each occurrence.
    wide = [None] * 50
    bundle["extra"] = [wide] * 50
    fails(bundle, "limit", policy=replace(model.MapPolicy(), max_values=1000))


def test_exact_compact_input_byte_boundary(bundle):
    size = len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert model.build_map_projection(bundle, policy=replace(model.MapPolicy(), max_input_bytes=size))
    fails(bundle, "limit", policy=replace(model.MapPolicy(), max_input_bytes=size - 1))


def test_all_criteria_must_be_verified_and_missing_refs_fail(bundle):
    bundle["nodes"].append(node("check2", "criterion"))
    bundle["nodes"][0]["criteria"].append("check2")
    assert "criteria_unsatisfied" in status(bundle)["reasons"]
    bundle["assessments"].append(assessment("check2"))
    assert status(bundle)["verified_accepted"]
    bundle["nodes"][0]["criteria"].append("absent")
    fails(bundle, "reference")


def test_feature_unknown_owner_state_and_identityless_source(bundle):
    bundle["nodes"][0].update(kind="feature", lifecycle={"owner": "feature", "state": "review"})
    fails(bundle, "enum")
    bundle["nodes"][0]["lifecycle"]["state"] = "completed"
    bundle["selection"]["kind"] = "feature"
    bundle["sources"][0]["identity"] = None
    assert not status(bundle)["verification"]["sufficient_current"]


def test_explanation_missing_node_and_unknown_constraint_provenance(bundle):
    projection = model.build_map_projection(bundle)
    with pytest.raises(model.MapValidationError, match="reference"):
        model.explain_map_node(projection, "absent")
    bundle["sources"].append(dict(bundle["sources"][0], id="policy", identity=None))
    bundle["constraints"] = [constraint(sources=["policy"])]
    actual = status(bundle)
    assert actual["verified_accepted"]
    assert actual["constraints"][0]["effective_observation"] == "unknown"


def test_explanation_does_not_conflate_source_and_node_id_namespaces(bundle):
    bundle["sources"].append(dict(bundle["sources"][0], id="unit", identity=None))
    projection = model.build_map_projection(bundle)
    assert projection["diagnostics"] == [{"code": "source_unknown", "members": ["unit"]}]
    assert model.explain_map_node(projection, "unit")["diagnostics"] == []


def test_aggregate_bytes_and_output_are_separately_bounded(bundle):
    bundle["coverage"]["omissions"] = [str(i) + "x" * 4000 for i in range(530)]
    bundle["coverage"]["state"] = "partial"
    fails(bundle, "limit")
    bundle["coverage"]["omissions"] = [str(i) + "x" * 4000 for i in range(200)]
    fails(bundle, "limit")  # Below input ceiling, above public output ceiling.


def test_deep_graph_without_python_recursion(bundle):
    # More than Python's usual recursion depth, while staying within byte bounds.
    count = 1100
    bundle["nodes"] = [node(f"n{i}", "phase", criteria=[], sources=[], title="n") for i in range(count)]
    bundle["edges"] = [edge(f"n{i}", f"n{i+1}", "contains_work", sources=[]) for i in range(count - 1)]
    bundle["assessments"] = []
    bundle["coverage"]["scope"] = []
    bundle["selection"] = {"kind": "phase", "nodes": []}
    # Validation has less derived output than projection; tests graph traversal itself.
    normalized = model.validate_map_bundle(bundle)
    assert len(normalized["bundle"]["nodes"]) == count
    bundle["edges"].append(edge(f"n{count-1}", "n0", "contains_work", sources=[]))
    fails(bundle, "containment_cycle")


def test_determinism_detachment_and_explanation_tampering(bundle):
    bundle["nodes"].append(node("other"))
    bundle["edges"] = [edge("unit", "other"), edge("other", "check")]
    before = deepcopy(bundle)
    first = model.build_map_projection(bundle)
    assert bundle == before
    for name in ("nodes", "sources", "edges", "assessments", "constraints"):
        bundle[name].reverse()
    assert json.dumps(first) == json.dumps(model.build_map_projection(bundle))
    result = model.explain_map_node(first, "unit")
    result["node"]["title"] = "changed"
    assert first["bundle"]["nodes"] != [result["node"]]
    assert next(n for n in first["bundle"]["nodes"] if n["id"] == "unit")["title"] == "unit"
    first["canonical"] = 0  # Even bool/int equality must not admit altered fields.
    with pytest.raises(model.MapValidationError, match="projection"):
        model.explain_map_node(first, "unit")


def test_no_filesystem_process_network_or_database_access(bundle, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure model attempted external access")
    with monkeypatch.context() as patch:
        for owner, name in ((builtins, "open"), (Path, "open"), (Path, "stat"),
                            (subprocess, "Popen"), (socket, "socket"), (sqlite3, "connect")):
            patch.setattr(owner, name, forbidden)
        validated = model.validate_map_bundle(bundle)
        projection = model.build_map_projection(validated["bundle"])
        assert model.explain_map_node(projection, "unit")["status"]["verified_accepted"]


def test_public_contract_example_and_package_registration():
    root = Path(__file__).resolve().parents[1]
    contract = (root / "docs/project-map-contract.md").read_text(encoding="utf-8")
    example = json.loads(contract.split("```json\n", 1)[1].split("\n```", 1)[0])
    projection = model.build_map_projection(example)
    assert projection["mode"] == "demo" and not projection["summary"]["declared_scope_verified"]
    import tomllib
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "cc_project_map_model" in metadata["tool"]["setuptools"]["py-modules"]
    assert metadata["project"]["dependencies"] == []
    assert "`cc_project_map_model.py`" in (root / "docs/architecture-index.md").read_text(encoding="utf-8")
