"""Tests for verification_agent.py Sprint 1 extensions.

Covers: new Criterion fields, architectural method, 7 import subcommands,
event logging, dedup, backward compatibility. Minimum 30 tests.
"""

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import verification_agent as va
from verification_agent import (
    Criterion, load_criteria, save_criteria, find_criterion,
    import_violations, import_audit, import_plan, import_verify,
    import_tandem, clear_resolved, plan_drift,
    VALID_METHODS, VALID_STATUSES,
)
from control_plane_utils import canonical_dedup_key


# ===========================================================================
# Helpers
# ===========================================================================

def _criteria_path(tmp_path):
    return str(tmp_path / "criteria.json")


def _event_log(tmp_path):
    return str(tmp_path / "event_log.jsonl")


def _decision_log(tmp_path):
    return str(tmp_path / "decision_log.jsonl")


def _read_jsonl(path):
    lines = []
    p = Path(path)
    if not p.exists():
        return lines
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


def _write_jsonl(path, items):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")


# ===========================================================================
# New Criterion fields + backward compatibility
# ===========================================================================

class TestCriterionV31Fields:
    def test_new_fields_defaults(self):
        c = Criterion(id="C-01", original_text="test")
        assert c.external_verification is False
        assert c.external_backend == ""
        assert c.source_agent == ""
        assert c.source_history == []

    def test_new_fields_set(self):
        c = Criterion(id="C-01", original_text="test",
                      external_verification=True,
                      external_backend="anthropic",
                      source_agent="codewarden",
                      source_history=[{"agent": "codewarden", "id": "CW-001"}])
        assert c.external_verification is True
        assert c.external_backend == "anthropic"
        assert c.source_agent == "codewarden"
        assert len(c.source_history) == 1

    def test_to_dict_includes_new_fields(self):
        c = Criterion(id="C-01", original_text="test", source_agent="cw")
        d = c.to_dict()
        assert "external_verification" in d
        assert "external_backend" in d
        assert "source_agent" in d
        assert "source_history" in d

    def test_backward_compat_load(self, tmp_path):
        """Old criteria.json without new fields should load fine."""
        path = _criteria_path(tmp_path)
        old_data = [{"id": "C-01", "original_text": "old criterion",
                     "original_text_hash": "",
                     "status": "pending", "attempts": 0,
                     "verification_method": "visual",
                     "verification_steps": [], "evidence": [],
                     "reference_screenshot": "", "notes": "",
                     "layer": 1, "blocks": [], "source_section": ""}]
        # Compute hash manually
        import hashlib
        old_data[0]["original_text_hash"] = hashlib.sha256(
            "old criterion".encode()).hexdigest()
        Path(path).write_text(json.dumps(old_data, indent=2), encoding="utf-8")
        criteria = load_criteria(path)
        assert len(criteria) == 1
        assert criteria[0].external_verification is False
        assert criteria[0].source_agent == ""

    def test_roundtrip_with_new_fields(self, tmp_path):
        path = _criteria_path(tmp_path)
        c = Criterion(id="C-01", original_text="roundtrip",
                      external_verification=True, source_agent="verifier",
                      source_history=[{"agent": "verifier", "id": "VF-001"}])
        save_criteria([c], path)
        loaded = load_criteria(path)
        assert loaded[0].external_verification is True
        assert loaded[0].source_agent == "verifier"
        assert loaded[0].source_history == [{"agent": "verifier", "id": "VF-001"}]


# ===========================================================================
# Architectural method
# ===========================================================================

class TestArchitecturalMethod:
    def test_architectural_is_valid(self):
        assert "architectural" in VALID_METHODS

    def test_wont_fix_is_valid_status(self):
        assert "wont_fix" in VALID_STATUSES

    def test_create_architectural_criterion(self, tmp_path):
        path = _criteria_path(tmp_path)
        c = Criterion(id="C-01", original_text="All state via Store",
                      verification_method="architectural")
        save_criteria([c], path)
        loaded = load_criteria(path)
        assert loaded[0].verification_method == "architectural"

    def test_next_shows_architectural_guidance(self, tmp_path, capsys):
        from unittest.mock import patch
        path = _criteria_path(tmp_path)
        save_criteria([
            Criterion(id="C-01", original_text="Store pattern everywhere",
                      verification_method="architectural"),
        ], path)
        with patch("sys.argv", ["va", "--criteria-path", path, "next"]):
            va.main()
        out = capsys.readouterr().out
        assert "ARCHITECTURAL" in out
        assert "ENTIRE codebase" in out


# ===========================================================================
# import-violations
# ===========================================================================

class TestImportViolations:
    def test_basic_import(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        source = str(tmp_path / "violations.jsonl")
        _write_jsonl(source, [
            {"id": "CW-001", "description": "Direct mutation", "file": "a.py",
             "line": 10, "rule": "store_pattern", "timestamp": "2026-01-01T00:00:00Z"},
        ])
        result = import_violations(source, criteria_path=cp, event_log=el)
        assert result["imported"] == 1
        assert result["skipped"] == 0
        criteria = load_criteria(cp)
        assert len(criteria) == 1
        assert criteria[0].id == "CW-001"
        assert criteria[0].source_agent == "cw"

    def test_dedup_skips_existing(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        source = str(tmp_path / "violations.jsonl")
        _write_jsonl(source, [
            {"description": "Direct mutation", "file": "a.py", "line": 10, "rule": "r1"},
        ])
        import_violations(source, criteria_path=cp, event_log=el)
        result = import_violations(source, criteria_path=cp, event_log=el)
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_missing_source(self, tmp_path):
        result = import_violations("/nonexistent.jsonl",
                                   criteria_path=_criteria_path(tmp_path),
                                   event_log=_event_log(tmp_path))
        assert result["error"] == "source file not found"

    def test_empty_source(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "empty.jsonl")
        Path(source).write_text("", encoding="utf-8")
        result = import_violations(source, criteria_path=cp,
                                   event_log=_event_log(tmp_path))
        assert result["imported"] == 0

    def test_event_logged(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        source = str(tmp_path / "violations.jsonl")
        _write_jsonl(source, [{"description": "A violation"}])
        import_violations(source, criteria_path=cp, event_log=el)
        events = _read_jsonl(el)
        assert len(events) >= 1
        assert events[-1]["event"] == "violations_imported"

    def test_multiple_violations(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        source = str(tmp_path / "violations.jsonl")
        _write_jsonl(source, [
            {"description": "Violation 1", "file": "a.py"},
            {"description": "Violation 2", "file": "b.py"},
            {"description": "Violation 3", "file": "c.py"},
        ])
        result = import_violations(source, criteria_path=cp, event_log=el)
        assert result["imported"] == 3
        criteria = load_criteria(cp)
        assert [c.id for c in criteria] == ["CW-001", "CW-002", "CW-003"]


# ===========================================================================
# import-audit
# ===========================================================================

class TestImportAudit:
    def _write_audit(self, path, findings):
        lines = []
        for f in findings:
            lines.append(f"[{f['id']}] {f.get('problem', '')}")
            lines.append(f"**Severity**: {f.get('severity', 'medium')}")
            lines.append(f"**Problem**: {f.get('problem', '')}")
            lines.append(f"**Fix**: {f.get('fix', '')}")
            if f.get("impact"):
                lines.append(f"**Impact**: {f['impact']}")
            lines.append("")
        Path(path).write_text("\n".join(lines), encoding="utf-8")

    def test_basic_import(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        source = str(tmp_path / "AUDIT_REPORT.md")
        self._write_audit(source, [
            {"id": "ENG-001", "severity": "high", "problem": "No error handling",
             "fix": "Add try/except; Add logging", "impact": "Crashes on bad input"},
        ])
        result = import_audit(source, criteria_path=cp, event_log=el)
        assert result["imported"] == 1
        criteria = load_criteria(cp)
        assert criteria[0].source_section == "Audit finding ENG-001"

    def test_severity_filter(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "AUDIT_REPORT.md")
        self._write_audit(source, [
            {"id": "ENG-001", "severity": "low", "problem": "Style issue",
             "fix": "Reformat"},
            {"id": "ENG-002", "severity": "critical", "problem": "SQL injection",
             "fix": "Use parameterized queries"},
        ])
        result = import_audit(source, criteria_path=cp,
                              severity_filter="critical",
                              event_log=_event_log(tmp_path))
        assert result["imported"] == 1
        criteria = load_criteria(cp)
        assert "SQL injection" in criteria[0].original_text

    def test_missing_source(self, tmp_path):
        result = import_audit("/nope.md",
                              criteria_path=_criteria_path(tmp_path),
                              event_log=_event_log(tmp_path))
        assert result["error"] == "source file not found"


# ===========================================================================
# import-plan
# ===========================================================================

class TestImportPlan:
    def _write_plan(self, path, phases):
        plan = {"phases": phases}
        Path(path).write_text(json.dumps(plan, indent=2), encoding="utf-8")

    def test_basic_import(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "plan.json")
        self._write_plan(source, [
            {"phase": 1, "features": [
                {"id": "F1", "acceptance_criterion": "Button renders",
                 "type": "visual", "verification_steps": ["Check UI"]},
                {"id": "F2", "acceptance_criterion": "Click handler works",
                 "type": "functional", "verification_steps": ["Click test"]},
            ]},
        ])
        result = import_plan(source, criteria_path=cp,
                             event_log=_event_log(tmp_path))
        assert result["imported"] == 2
        criteria = load_criteria(cp)
        assert criteria[0].verification_method == "visual"
        assert criteria[1].verification_method == "functional"

    def test_layer_from_phase(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "plan.json")
        self._write_plan(source, [
            {"phase": 2, "features": [
                {"id": "F1", "acceptance_criterion": "Polish feature",
                 "type": "ui"},
            ]},
        ])
        import_plan(source, criteria_path=cp, event_log=_event_log(tmp_path))
        criteria = load_criteria(cp)
        assert criteria[0].layer == 2

    def test_dependencies_become_blocks(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "plan.json")
        self._write_plan(source, [
            {"phase": 1, "features": [
                {"id": "F1", "acceptance_criterion": "Base feature", "type": "code"},
                {"id": "F2", "acceptance_criterion": "Dependent feature",
                 "type": "code", "dependencies": ["F1"]},
            ]},
        ])
        import_plan(source, criteria_path=cp, event_log=_event_log(tmp_path))
        criteria = load_criteria(cp)
        # F1's blocks should contain F2's criterion ID
        f1 = criteria[0]
        f2 = criteria[1]
        assert f2.id in f1.blocks

    def test_missing_source(self, tmp_path):
        result = import_plan("/nope.json",
                             criteria_path=_criteria_path(tmp_path),
                             event_log=_event_log(tmp_path))
        assert result["error"] == "source file not found"


# ===========================================================================
# import-verify
# ===========================================================================

class TestImportVerify:
    def test_basic_import(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "verifier.json")
        Path(source).write_text(json.dumps({
            "findings": [
                {"id": "V1", "description": "Race condition in handler",
                 "file": "handler.py", "fix_steps": ["Add lock", "Test"],
                 "backend": "gpt-4o"},
            ]
        }), encoding="utf-8")
        result = import_verify(source, criteria_path=cp,
                               event_log=_event_log(tmp_path))
        assert result["imported"] == 1
        criteria = load_criteria(cp)
        assert criteria[0].status == "pending"
        assert "gpt-4o" in criteria[0].notes

    def test_dedup(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "verifier.json")
        Path(source).write_text(json.dumps({
            "findings": [
                {"description": "Issue X", "file": "a.py"},
            ]
        }), encoding="utf-8")
        import_verify(source, criteria_path=cp, event_log=_event_log(tmp_path))
        result = import_verify(source, criteria_path=cp,
                               event_log=_event_log(tmp_path))
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_empty_findings(self, tmp_path):
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "verifier.json")
        Path(source).write_text(json.dumps({"findings": []}), encoding="utf-8")
        result = import_verify(source, criteria_path=cp,
                               event_log=_event_log(tmp_path))
        assert result["imported"] == 0


# ===========================================================================
# import-tandem
# ===========================================================================

class TestImportTandem:
    def _write_tandem(self, path, divergences=None, agreements=None):
        report = {
            "divergences": divergences or [],
            "agreements": agreements or [],
        }
        Path(path).write_text(json.dumps(report), encoding="utf-8")

    def test_decision_only_no_criteria(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        source = str(tmp_path / "tandem.json")
        self._write_tandem(source, divergences=[
            {"topic": "Use REST vs GraphQL", "position_a": "REST",
             "position_b": "GraphQL"},
        ])
        result = import_tandem(source, criteria_path=cp, mode="decision-only",
                               event_log=el, decision_log=dl)
        assert result["logged"] == 1
        assert result["imported"] == 0
        # No criteria created
        criteria = load_criteria(cp)
        assert len(criteria) == 0
        # But decision was logged
        decisions = _read_jsonl(dl)
        assert len(decisions) == 1
        assert decisions[0]["type"] == "architecture_choice"

    def test_criteria_for_unresolved(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        source = str(tmp_path / "tandem.json")
        self._write_tandem(source, divergences=[
            {"topic": "Use REST vs GraphQL", "position_a": "REST",
             "position_b": "GraphQL"},
        ])
        result = import_tandem(source, criteria_path=cp,
                               mode="criteria-for-unresolved",
                               event_log=el, decision_log=dl)
        assert result["imported"] == 1
        criteria = load_criteria(cp)
        assert len(criteria) == 1
        assert criteria[0].id == "TD-001"
        assert "User must decide" in criteria[0].notes

    def test_agreements_logged_as_events(self, tmp_path):
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        source = str(tmp_path / "tandem.json")
        self._write_tandem(source, agreements=[
            {"topic": "Use TypeScript", "confidence": "high"},
        ])
        import_tandem(source, criteria_path=_criteria_path(tmp_path),
                      event_log=el, decision_log=dl)
        events = _read_jsonl(el)
        assert any(e["event"] == "tandem_converged" for e in events)

    def test_missing_source(self, tmp_path):
        result = import_tandem("/nope.json",
                               criteria_path=_criteria_path(tmp_path),
                               event_log=_event_log(tmp_path),
                               decision_log=_decision_log(tmp_path))
        assert result["error"] == "source file not found"

    def test_resolved_divergence_not_imported(self, tmp_path):
        """If a divergence already has a resolved decision, skip it."""
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        dl = _decision_log(tmp_path)
        # Pre-populate decision log with resolved decision
        _write_jsonl(dl, [{
            "id": "DEC-001", "type": "architecture_choice",
            "context": "Use REST vs GraphQL",
            "chosen": "REST", "chosen_by": "user",
            "source": "tandem", "rationale": "Simpler",
        }])
        source = str(tmp_path / "tandem.json")
        self._write_tandem(source, divergences=[
            {"topic": "Use REST vs GraphQL", "position_a": "REST",
             "position_b": "GraphQL"},
        ])
        result = import_tandem(source, criteria_path=cp,
                               mode="criteria-for-unresolved",
                               event_log=el, decision_log=dl)
        # Divergence is resolved, so no criteria created
        assert result["imported"] == 0


# ===========================================================================
# clear-resolved
# ===========================================================================

class TestClearResolved:
    def test_archives_stable_pass(self, tmp_path):
        cp = _criteria_path(tmp_path)
        ap = str(tmp_path / "archive.json")
        el = _event_log(tmp_path)
        save_criteria([
            Criterion(id="C-01", original_text="stable", status="pass", attempts=5),
            Criterion(id="C-02", original_text="recent pass", status="pass", attempts=1),
            Criterion(id="C-03", original_text="pending", status="pending", attempts=0),
        ], cp)
        result = clear_resolved(criteria_path=cp, archive_path=ap,
                                min_sessions=3, event_log=el)
        assert result["archived"] == 1
        remaining = load_criteria(cp)
        assert len(remaining) == 2
        assert all(c.id != "C-01" for c in remaining)
        archive = json.loads(Path(ap).read_text(encoding="utf-8"))
        assert len(archive) == 1
        assert archive[0]["id"] == "C-01"

    def test_nothing_to_archive(self, tmp_path):
        cp = _criteria_path(tmp_path)
        save_criteria([
            Criterion(id="C-01", original_text="pending", status="pending"),
        ], cp)
        result = clear_resolved(criteria_path=cp,
                                archive_path=str(tmp_path / "a.json"),
                                event_log=_event_log(tmp_path))
        assert result["archived"] == 0

    def test_event_logged(self, tmp_path):
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        save_criteria([
            Criterion(id="C-01", original_text="stable", status="pass", attempts=5),
        ], cp)
        clear_resolved(criteria_path=cp,
                       archive_path=str(tmp_path / "archive.json"),
                       min_sessions=3, event_log=el)
        events = _read_jsonl(el)
        assert any(e["event"] == "criterion_archived" for e in events)


# ===========================================================================
# plan_drift
# ===========================================================================

class TestPlanDrift:
    def test_no_drift(self, tmp_path):
        plan_path = str(tmp_path / "plan.json")
        # Create expected files
        (tmp_path / "existing.py").write_text("pass", encoding="utf-8")
        Path(plan_path).write_text(json.dumps({
            "phases": [{"phase": 1, "features": [
                {"description": "Feature", "expected_files": [
                    str(tmp_path / "existing.py")]}
            ]}]
        }), encoding="utf-8")
        result = plan_drift(plan_path, event_log=_event_log(tmp_path))
        assert len(result["discrepancies"]) == 0

    def test_missing_file_detected(self, tmp_path):
        plan_path = str(tmp_path / "plan.json")
        Path(plan_path).write_text(json.dumps({
            "phases": [{"phase": 1, "features": [
                {"description": "Feature",
                 "expected_files": [str(tmp_path / "nonexistent.py")]}
            ]}]
        }), encoding="utf-8")
        result = plan_drift(plan_path, event_log=_event_log(tmp_path))
        assert len(result["discrepancies"]) == 1
        assert result["discrepancies"][0]["type"] == "missing_file"
        assert result["discrepancies"][0]["severity"] == "high"

    def test_missing_plan(self, tmp_path):
        result = plan_drift("/nope.json", event_log=_event_log(tmp_path))
        assert result["error"] == "plan file not found"

    def test_drift_event_logged(self, tmp_path):
        el = _event_log(tmp_path)
        plan_path = str(tmp_path / "plan.json")
        Path(plan_path).write_text(json.dumps({
            "phases": [{"phase": 1, "features": [
                {"description": "F", "expected_files": [str(tmp_path / "missing_drift_target.py")]}
            ]}]
        }), encoding="utf-8")
        plan_drift(plan_path, event_log=el)
        events = _read_jsonl(el)
        assert any(e["event"] == "plan_drift_detected" for e in events)


# ===========================================================================
# Event logging on existing commands
# ===========================================================================

class TestEventLogging:
    def test_add_criterion_logs_event(self, tmp_path):
        from unittest.mock import patch
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        # Patch DEFAULT_EVENT_LOG to use our temp path
        old = va.DEFAULT_EVENT_LOG
        va.DEFAULT_EVENT_LOG = el
        try:
            with patch("sys.argv", ["va", "--criteria-path", cp,
                                     "add-criterion", "--id", "C-01",
                                     "--text", "Test criterion",
                                     "--method", "code"]):
                va.main()
        finally:
            va.DEFAULT_EVENT_LOG = old
        events = _read_jsonl(el)
        assert any(e["event"] == "criterion_created" for e in events)

    def test_update_logs_event(self, tmp_path):
        from unittest.mock import patch
        cp = _criteria_path(tmp_path)
        el = _event_log(tmp_path)
        save_criteria([Criterion(id="C-01", original_text="x")], cp)
        old = va.DEFAULT_EVENT_LOG
        va.DEFAULT_EVENT_LOG = el
        try:
            with patch("sys.argv", ["va", "--criteria-path", cp,
                                     "update", "--id", "C-01",
                                     "--status", "pass",
                                     "--evidence", "proof.png"]):
                va.main()
        finally:
            va.DEFAULT_EVENT_LOG = old
        events = _read_jsonl(el)
        assert any(e["event"] == "criterion_passed" for e in events)


# ===========================================================================
# CLI integration for new subcommands
# ===========================================================================

class TestCLINewSubcommands:
    def test_import_violations_cli(self, tmp_path, capsys):
        from unittest.mock import patch
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "v.jsonl")
        _write_jsonl(source, [{"description": "V1"}])
        old_el = va.DEFAULT_EVENT_LOG
        va.DEFAULT_EVENT_LOG = _event_log(tmp_path)
        try:
            with patch("sys.argv", ["va", "--criteria-path", cp,
                                     "import-violations", "--source", source]):
                ret = va.main()
        finally:
            va.DEFAULT_EVENT_LOG = old_el
        assert ret == 0
        out = capsys.readouterr().out
        assert "1 imported" in out

    def test_clear_resolved_cli(self, tmp_path, capsys):
        from unittest.mock import patch
        cp = _criteria_path(tmp_path)
        save_criteria([
            Criterion(id="C-01", original_text="done", status="pass", attempts=5),
        ], cp)
        ap = str(tmp_path / "archive.json")
        old_el = va.DEFAULT_EVENT_LOG
        va.DEFAULT_EVENT_LOG = _event_log(tmp_path)
        try:
            with patch("sys.argv", ["va", "--criteria-path", cp,
                                     "clear-resolved",
                                     "--archive-path", ap]):
                ret = va.main()
        finally:
            va.DEFAULT_EVENT_LOG = old_el
        assert ret == 0
        out = capsys.readouterr().out
        assert "1 criteria archived" in out

    def test_plan_drift_cli(self, tmp_path, capsys):
        from unittest.mock import patch
        cp = _criteria_path(tmp_path)
        source = str(tmp_path / "plan.json")
        Path(source).write_text(json.dumps({
            "phases": [{"phase": 1, "features": [
                {"description": "F", "expected_files": [str(tmp_path / "missing_cli_drift_target.py")]}
            ]}]
        }), encoding="utf-8")
        old_el = va.DEFAULT_EVENT_LOG
        va.DEFAULT_EVENT_LOG = _event_log(tmp_path)
        try:
            with patch("sys.argv", ["va", "--criteria-path", cp,
                                     "plan_drift", "--source", source]):
                ret = va.main()
        finally:
            va.DEFAULT_EVENT_LOG = old_el
        assert ret == 1  # discrepancies found
        out = capsys.readouterr().out
        assert "discrepancies" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
