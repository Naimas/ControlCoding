"""Tests for planner.py - Sprint 4 deliverable.

Covers: state machine, maieutic rounds, domain adaptation, plan_approve
with SHA256, plan_refine, plan_add_feature, plan_export, zone mapping,
invariant export, decision logging, anti-compression. Minimum 35 tests.
"""

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import planner as pl
from planner import (
    ROUND_NAMES, VALID_STATES, VALID_TRANSITIONS,
    _empty_plan, _sha256_plan, _transition,
    load_plan, save_plan,
    plan_expand, plan_refine, plan_approve, plan_status,
    plan_add_feature, plan_export,
    export_zone_mapping, export_invariants,
    get_expansion_questions,
)


@pytest.fixture(autouse=True)
def _isolated_relative_defaults(tmp_path, monkeypatch):
    """Keep planner auto-export defaults inside the per-test directory."""
    (tmp_path / ".controlcoding").mkdir()
    (tmp_path / "devlog" / "criteria").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)


# ===========================================================================
# Helpers
# ===========================================================================

def _plan_dir(tmp_path):
    d = str(tmp_path / "devlog" / "plans")
    Path(d).mkdir(parents=True, exist_ok=True)
    return d


def _event_log(tmp_path):
    return str(tmp_path / "event_log.jsonl")


def _decision_log(tmp_path):
    return str(tmp_path / "decision_log.jsonl")


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    lines = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


def _expand_all_rounds(tmp_path, idea="Build a trading engine", domain="financial"):
    """Helper: expand through all 6 rounds to get a DRAFT plan."""
    pd = _plan_dir(tmp_path)
    el = _event_log(tmp_path)

    # Round 1: scope
    result = plan_expand(idea=idea, domain=domain, plan_dir=pd, event_log=el)
    assert result["next_round"] == "scope"

    responses = {
        "scope": "A trading engine for limit and market orders",
        "features": "Order entry\nOrder matching\nTrade settlement\nPosition tracking",
        "architecture": "core <- orders <- matching <- settlement",
        "invariants": "sum(debits) == sum(credits)\nno negative positions",
        "acceptance": "Order entry works\nMatching works\nSettlement works\nPositions accurate",
        "phasing": "Phase 1: Order types\nPhase 2: Matching\nPhase 3: Settlement",
    }

    for rn in ROUND_NAMES:
        result = plan_expand(domain=domain, responses={rn: responses[rn]},
                             plan_dir=pd, event_log=el)

    return result, pd


# ===========================================================================
# State machine
# ===========================================================================

class TestStateMachine:
    def test_valid_states(self):
        assert len(VALID_STATES) == 7
        assert "EMPTY" in VALID_STATES
        assert "APPROVED" in VALID_STATES

    def test_valid_transitions(self):
        assert "EXPANDING" in VALID_TRANSITIONS["EMPTY"]
        assert "APPROVED" in VALID_TRANSITIONS["DRAFT"]
        assert "AMENDED" in VALID_TRANSITIONS["APPROVED"]
        assert "APPROVED" in VALID_TRANSITIONS["AMENDED"]

    def test_transition_success(self):
        plan = _empty_plan()
        _transition(plan, "EXPANDING")
        assert plan["status"] == "EXPANDING"

    def test_transition_invalid_raises(self):
        plan = _empty_plan()
        with pytest.raises(ValueError, match="Invalid transition"):
            _transition(plan, "APPROVED")

    def test_transition_empty_to_draft_invalid(self):
        plan = _empty_plan()
        with pytest.raises(ValueError):
            _transition(plan, "DRAFT")

    def test_transition_completed_is_terminal(self):
        plan = {"status": "COMPLETED"}
        with pytest.raises(ValueError):
            _transition(plan, "EXPANDING")

    def test_draft_to_expanding_allowed(self):
        plan = {"status": "DRAFT"}
        _transition(plan, "EXPANDING")
        assert plan["status"] == "EXPANDING"

    def test_amended_to_approved_allowed(self):
        plan = {"status": "AMENDED"}
        _transition(plan, "APPROVED")
        assert plan["status"] == "APPROVED"


# ===========================================================================
# Plan persistence
# ===========================================================================

class TestPlanPersistence:
    def test_load_empty(self, tmp_path):
        plan = load_plan(str(tmp_path / "devlog"))
        assert plan["status"] == "EMPTY"

    def test_save_and_load(self, tmp_path):
        pd = _plan_dir(tmp_path)
        plan = _empty_plan()
        plan["scope"] = "Test project"
        plan["status"] = "EXPANDING"
        save_plan(plan, pd)
        loaded = load_plan(pd)
        assert loaded["scope"] == "Test project"
        assert loaded["status"] == "EXPANDING"

    def test_hash_integrity_check(self, tmp_path):
        pd = _plan_dir(tmp_path)
        plan = _empty_plan()
        plan["status"] = "APPROVED"
        plan["scope"] = "Locked plan"
        plan["status_hash"] = _sha256_plan(plan)
        save_plan(plan, pd)
        # Load should succeed
        loaded = load_plan(pd)
        assert loaded["status"] == "APPROVED"

    def test_hash_tampering_detected(self, tmp_path):
        pd = _plan_dir(tmp_path)
        plan = _empty_plan()
        plan["status"] = "APPROVED"
        plan["scope"] = "Locked plan"
        plan["status_hash"] = _sha256_plan(plan)
        save_plan(plan, pd)
        # Tamper
        path = Path(pd) / "plan.current.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["scope"] = "TAMPERED"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        with pytest.raises(ValueError, match="integrity"):
            load_plan(pd)


# ===========================================================================
# Domain adaptation
# ===========================================================================

class TestDomainAdaptation:
    def test_financial_questions(self):
        qs = get_expansion_questions("financial", "scope")
        assert len(qs) >= 2
        assert any("financial" in q.lower() or "instrument" in q.lower() for q in qs)

    def test_game_questions(self):
        qs = get_expansion_questions("game", "features")
        assert len(qs) >= 2
        assert any("inventory" in q.lower() or "mechanic" in q.lower() for q in qs)

    def test_physics_questions(self):
        qs = get_expansion_questions("physics", "invariants")
        assert len(qs) >= 2
        assert any("conservation" in q.lower() for q in qs)

    def test_web_questions(self):
        qs = get_expansion_questions("web", "architecture")
        assert len(qs) >= 2

    def test_unknown_domain_uses_generic(self):
        qs = get_expansion_questions("robotics", "scope")
        assert len(qs) >= 2
        # Should be generic questions
        assert any("system" in q.lower() or "problem" in q.lower() for q in qs)


# ===========================================================================
# plan_expand() - maieutic dialogue
# ===========================================================================

class TestPlanExpand:
    def test_first_call_needs_idea(self, tmp_path):
        pd = _plan_dir(tmp_path)
        result = plan_expand(plan_dir=pd, event_log=_event_log(tmp_path))
        assert result.get("error")

    def test_first_call_transitions_to_expanding(self, tmp_path):
        pd = _plan_dir(tmp_path)
        result = plan_expand(idea="Build a game", plan_dir=pd,
                             event_log=_event_log(tmp_path))
        assert result["plan"]["status"] == "EXPANDING"
        assert result["next_round"] == "scope"
        assert len(result["questions"]) >= 2

    def test_round_progression(self, tmp_path):
        pd = _plan_dir(tmp_path)
        el = _event_log(tmp_path)
        plan_expand(idea="Build a game", domain="game", plan_dir=pd, event_log=el)

        result = plan_expand(domain="game",
                             responses={"scope": "A platformer game"},
                             plan_dir=pd, event_log=el)
        assert "scope" in result["plan"]["expansion_state"]["completed_rounds"]
        assert result["next_round"] == "features"

    def test_all_rounds_complete_to_draft(self, tmp_path):
        result, pd = _expand_all_rounds(tmp_path)
        assert result["complete"] is True
        assert result["plan"]["status"] == "DRAFT"

    def test_features_parsed(self, tmp_path):
        result, pd = _expand_all_rounds(tmp_path)
        plan = result["plan"]
        total_features = sum(len(p.get("features", []))
                            for p in plan.get("phases", []))
        assert total_features >= 1

    def test_invariants_parsed(self, tmp_path):
        result, pd = _expand_all_rounds(tmp_path)
        plan = result["plan"]
        assert len(plan.get("invariants_global", [])) >= 1
        assert plan["invariants_global"][0]["text"]

    def test_phasing_creates_multiple_phases(self, tmp_path):
        result, pd = _expand_all_rounds(tmp_path)
        plan = result["plan"]
        assert len(plan.get("phases", [])) >= 2

    def test_cannot_expand_approved_plan(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        plan_approve(plan_dir=pd, decision_log=_decision_log(tmp_path),
                     event_log=_event_log(tmp_path))
        result = plan_expand(plan_dir=pd, event_log=_event_log(tmp_path))
        assert result.get("error")


# ===========================================================================
# plan_approve()
# ===========================================================================

class TestPlanApprove:
    def test_approve_draft(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        result = plan_approve(rationale="Looks good", plan_dir=pd,
                              event_log=el, decision_log=dl)
        assert result["plan"]["status"] == "APPROVED"
        assert result["plan"]["status_hash"]
        assert result["plan"]["approved_at"]
        assert "snapshot" in result
        criteria_path = tmp_path / "devlog" / "criteria" / "criteria.json"
        config_path = tmp_path / ".controlcoding" / "cc_config.json"
        assert criteria_path.exists()
        assert config_path.exists()
        assert criteria_path.resolve().is_relative_to(tmp_path.resolve())
        assert config_path.resolve().is_relative_to(tmp_path.resolve())

    def test_approve_creates_snapshot(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        result = plan_approve(plan_dir=pd,
                              event_log=_event_log(tmp_path),
                              decision_log=_decision_log(tmp_path))
        snapshot = result["snapshot"]
        assert Path(snapshot).exists()
        data = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        assert data["status"] == "APPROVED"

    def test_approve_logs_decision(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        dl = _decision_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=_event_log(tmp_path),
                     decision_log=dl)
        decisions = _read_jsonl(dl)
        assert len(decisions) >= 1
        assert decisions[-1]["type"] == "plan_approval"

    def test_approve_logs_event(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=el,
                     decision_log=_decision_log(tmp_path))
        events = _read_jsonl(el)
        assert any(e["event"] == "plan_approved" for e in events)

    def test_cannot_approve_empty(self, tmp_path):
        pd = _plan_dir(tmp_path)
        result = plan_approve(plan_dir=pd,
                              event_log=_event_log(tmp_path),
                              decision_log=_decision_log(tmp_path))
        assert result.get("error")

    def test_hash_validates_after_approval(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        plan_approve(plan_dir=pd, event_log=_event_log(tmp_path),
                     decision_log=_decision_log(tmp_path))
        # Load should succeed (hash is valid)
        plan = load_plan(pd)
        assert plan["status"] == "APPROVED"


# ===========================================================================
# plan_status()
# ===========================================================================

class TestPlanStatus:
    def test_empty_status(self, tmp_path):
        pd = _plan_dir(tmp_path)
        status = plan_status(pd)
        assert status["status"] == "EMPTY"

    def test_draft_status(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        status = plan_status(pd)
        assert status["status"] == "DRAFT"
        assert status["total_features"] >= 1
        assert status["phases"] >= 1

    def test_approved_status_has_hash(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        plan_approve(plan_dir=pd, event_log=_event_log(tmp_path),
                     decision_log=_decision_log(tmp_path))
        status = plan_status(pd)
        assert status["status"] == "APPROVED"
        assert status["hash_valid"] is True


# ===========================================================================
# plan_refine()
# ===========================================================================

class TestPlanRefine:
    def test_refine_feature(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        plan = load_plan(pd)
        # Get first feature ID
        feat_id = plan["phases"][0]["features"][0]["id"]
        result = plan_refine(feat_id,
                             {"description": "Updated description"},
                             plan_dir=pd, event_log=_event_log(tmp_path))
        assert result["target"] == feat_id
        updated = load_plan(pd)
        feat = None
        for p in updated["phases"]:
            for f in p.get("features", []):
                if f["id"] == feat_id:
                    feat = f
        assert feat["description"] == "Updated description"

    def test_refine_approved_creates_amended(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        plan_approve(plan_dir=pd, event_log=_event_log(tmp_path),
                     decision_log=_decision_log(tmp_path))
        plan = load_plan(pd)
        feat_id = plan["phases"][0]["features"][0]["id"]
        result = plan_refine(feat_id, {"description": "Changed"},
                             plan_dir=pd, event_log=_event_log(tmp_path))
        assert result["amended"] is True
        updated = load_plan(pd)
        assert updated["status"] == "AMENDED"

    def test_refine_nonexistent_target(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        result = plan_refine("P99-F99", {"description": "x"},
                             plan_dir=pd, event_log=_event_log(tmp_path))
        assert result.get("error")

    def test_refine_empty_plan(self, tmp_path):
        pd = _plan_dir(tmp_path)
        result = plan_refine("P1-F1", {"description": "x"},
                             plan_dir=pd, event_log=_event_log(tmp_path))
        assert result.get("error")


# ===========================================================================
# plan_add_feature()
# ===========================================================================

class TestPlanAddFeature:
    def test_add_feature_to_approved(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=el, decision_log=dl)
        plan = load_plan(pd)
        phase_id = plan["phases"][0]["id"]
        result = plan_add_feature(
            phase_id,
            {"name": "New feature", "description": "A new capability",
             "acceptance_criterion": "It works"},
            plan_dir=pd, event_log=el, decision_log=dl)
        assert "feature_id" in result
        updated = load_plan(pd)
        assert updated["status"] == "AMENDED"

    def test_add_feature_bumps_version(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=el, decision_log=dl)
        plan = load_plan(pd)
        old_version = plan["version"]
        phase_id = plan["phases"][0]["id"]
        plan_add_feature(phase_id, {"name": "New"},
                         plan_dir=pd, event_log=el, decision_log=dl)
        updated = load_plan(pd)
        assert updated["version"] == old_version + 1

    def test_add_feature_logs_decision(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=el, decision_log=dl)
        plan = load_plan(pd)
        phase_id = plan["phases"][0]["id"]
        plan_add_feature(phase_id, {"name": "New"},
                         plan_dir=pd, event_log=el, decision_log=dl)
        decisions = _read_jsonl(dl)
        assert any(d["type"] == "plan_amendment" for d in decisions)

    def test_add_feature_to_nonexistent_phase(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=el, decision_log=dl)
        result = plan_add_feature("P99", {"name": "New"},
                                  plan_dir=pd, event_log=el, decision_log=dl)
        assert result.get("error")

    def test_cannot_add_feature_to_draft(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        result = plan_add_feature("P1", {"name": "New"},
                                  plan_dir=pd,
                                  event_log=_event_log(tmp_path),
                                  decision_log=_decision_log(tmp_path))
        assert result.get("error")

    def test_re_approve_after_amendment(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=el, decision_log=dl)
        plan = load_plan(pd)
        phase_id = plan["phases"][0]["id"]
        plan_add_feature(phase_id, {"name": "New"},
                         plan_dir=pd, event_log=el, decision_log=dl)
        # Re-approve
        result = plan_approve(plan_dir=pd, event_log=el, decision_log=dl)
        assert result["plan"]["status"] == "APPROVED"
        assert result["plan"]["status_hash"]


# ===========================================================================
# Zone mapping export
# ===========================================================================

class TestZoneMapping:
    def test_export_zones(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        plan = load_plan(pd)
        plan["architecture"]["deny_zones"] = ["src/core/"]
        plan["architecture"]["warn_zones"] = ["src/shared/"]
        save_plan(plan, pd)

        config_path = str(tmp_path / "cc_config.json")
        result = export_zone_mapping(plan_dir=pd, config_path=config_path)
        assert result["deny"] == ["src/core/"]
        assert result["warn"] == ["src/shared/"]
        assert Path(config_path).exists()
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        assert config["protected_zones"]["deny"] == ["src/core/"]

    def test_export_zones_preserves_existing_config(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        config_path = str(tmp_path / "cc_config.json")
        # Pre-existing config
        Path(config_path).write_text(json.dumps({"other_key": "value"}),
                                     encoding="utf-8")
        export_zone_mapping(plan_dir=pd, config_path=config_path)
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        assert "other_key" in config
        assert "protected_zones" in config


# ===========================================================================
# Invariant export
# ===========================================================================

class TestInvariantExport:
    def test_export_invariants(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        output = str(tmp_path / "test_inv.py")
        result = export_invariants(plan_dir=pd, output_path=output)
        assert result["count"] >= 1
        assert Path(output).exists()
        content = Path(output).read_text(encoding="utf-8")
        assert "pytest" in content
        assert "NotImplementedError" in content
        assert "INV" in content

    def test_export_empty_invariants(self, tmp_path):
        pd = _plan_dir(tmp_path)
        plan = _empty_plan()
        plan["status"] = "DRAFT"
        save_plan(plan, pd)
        result = export_invariants(plan_dir=pd,
                                   output_path=str(tmp_path / "t.py"))
        assert result.get("error")
        assert result["count"] == 0

    def test_invariant_test_names_valid_python(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        output = str(tmp_path / "test_inv.py")
        export_invariants(plan_dir=pd, output_path=output)
        content = Path(output).read_text(encoding="utf-8")
        # Should compile without syntax errors
        compile(content, output, "exec")


# ===========================================================================
# plan_export()
# ===========================================================================

class TestPlanExport:
    def test_export_creates_criteria(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        plan_approve(plan_dir=pd, event_log=el, decision_log=dl)
        criteria_path = str(tmp_path / "criteria.json")
        result = plan_export(plan_dir=pd, criteria_path=criteria_path,
                             event_log=el)
        assert result.get("imported", 0) >= 1
        assert Path(criteria_path).exists()

    def test_cannot_export_empty_plan(self, tmp_path):
        pd = _plan_dir(tmp_path)
        result = plan_export(plan_dir=pd)
        assert result.get("error")


# ===========================================================================
# Anti-compression / SHA256
# ===========================================================================

class TestAntiCompression:
    def test_sha256_consistent(self):
        plan = _empty_plan()
        plan["scope"] = "Test"
        h1 = _sha256_plan(plan)
        h2 = _sha256_plan(plan)
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_changes_on_modification(self):
        plan = _empty_plan()
        plan["scope"] = "Test"
        h1 = _sha256_plan(plan)
        plan["scope"] = "Modified"
        h2 = _sha256_plan(plan)
        assert h1 != h2

    def test_sha256_ignores_status_hash(self):
        plan = _empty_plan()
        plan["scope"] = "Test"
        plan["status_hash"] = "garbage"
        h1 = _sha256_plan(plan)
        plan["status_hash"] = "different_garbage"
        h2 = _sha256_plan(plan)
        assert h1 == h2


# ===========================================================================
# Plan output format
# ===========================================================================

class TestPlanFormat:
    def test_draft_has_all_fields(self, tmp_path):
        result, pd = _expand_all_rounds(tmp_path)
        plan = result["plan"]
        required = {"version", "status", "scope", "phases",
                    "invariants_global", "architecture", "created_at"}
        assert required.issubset(set(plan.keys()))

    def test_phases_have_features(self, tmp_path):
        result, _ = _expand_all_rounds(tmp_path)
        plan = result["plan"]
        for phase in plan["phases"]:
            assert "id" in phase
            assert "features" in phase
            assert isinstance(phase["features"], list)

    def test_approved_has_hash(self, tmp_path):
        _, pd = _expand_all_rounds(tmp_path)
        result = plan_approve(plan_dir=pd,
                              event_log=_event_log(tmp_path),
                              decision_log=_decision_log(tmp_path))
        plan = result["plan"]
        assert plan["status_hash"]
        assert len(plan["status_hash"]) == 64


class TestSlugify:
    def test_simple_text(self):
        from planner import _slugify
        assert _slugify("Trading Platform") == "trading-platform"

    def test_special_chars(self):
        from planner import _slugify
        assert _slugify("My App! (v2.0)") == "my-app-v2-0"

    def test_empty(self):
        from planner import _slugify
        assert _slugify("") == "untitled"

    def test_none(self):
        from planner import _slugify
        assert _slugify(None) == "untitled"

    def test_truncation(self):
        from planner import _slugify
        result = _slugify("a very long project name that exceeds the limit", max_len=20)
        assert len(result) <= 20
        assert "-" not in result[-1:]  # no trailing hyphen

    def test_collapses_hyphens(self):
        from planner import _slugify
        assert _slugify("hello   world") == "hello-world"


class TestSnapshotNaming:
    def test_snapshot_includes_slug(self, tmp_path):
        pd = _plan_dir(tmp_path)
        _, pd = _expand_all_rounds(tmp_path)
        plan_approve(plan_dir=pd,
                     event_log=_event_log(tmp_path),
                     decision_log=_decision_log(tmp_path))
        # Check that snapshot file has slug in name (not just version+state)
        files = list(Path(pd).glob("plan-v1-approved-*.json"))
        assert len(files) == 1
        # Slug should be non-empty (derived from scope)
        name = files[0].name
        # Remove prefix "plan-v1-approved-" and suffix ".json"
        slug_part = name.replace("plan-v1-approved-", "").replace(".json", "")
        assert len(slug_part) > 0
        assert slug_part != "untitled"

    def test_snapshot_without_idea_has_untitled(self, tmp_path):
        pd = _plan_dir(tmp_path)
        # Create a plan manually with no scope
        plan = _empty_plan()
        plan["status"] = "DRAFT"
        plan["version"] = 1
        plan["scope"] = ""
        save_plan(plan, pd)
        plan_approve(plan_dir=pd,
                     event_log=_event_log(tmp_path),
                     decision_log=_decision_log(tmp_path))
        files = list(Path(pd).glob("plan-v1-approved-*.json"))
        assert len(files) == 1
        assert "untitled" in files[0].name

    def test_current_plan_name_unchanged(self, tmp_path):
        """plan.current.json keeps its fixed name (operational file)."""
        pd = _plan_dir(tmp_path)
        _expand_all_rounds(tmp_path)
        assert (Path(pd) / "plan.current.json").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
