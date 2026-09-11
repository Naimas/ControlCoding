"""Unit tests for verification_agent.py - D-script mechanics."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import verification_agent as va
from verification_agent import (
    Criterion, _sha256, load_criteria, save_criteria, find_criterion,
    resolve_blocked, get_dag_ready, filter_by_layer, layer_summary,
    check_regression, check_budget,
)


@pytest.fixture(autouse=True)
def _isolated_relative_defaults(tmp_path, monkeypatch):
    """Keep default criteria and event-log writes inside tmp_path."""
    (tmp_path / ".controlcoding").mkdir()
    (tmp_path / "devlog" / "criteria").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)


# ===========================================================================
# D-S1: Criterion dataclass
# ===========================================================================

class TestCriterion:
    def test_create_basic(self):
        c = Criterion(id="C-01", original_text="A red button")
        assert c.id == "C-01"
        assert c.original_text == "A red button"
        assert c.status == "pending"
        assert c.attempts == 0
        assert c.layer == 1

    def test_hash_auto_computed(self):
        c = Criterion(id="C-01", original_text="test text")
        assert c.original_text_hash == _sha256("test text")
        assert len(c.original_text_hash) == 64

    def test_to_dict_roundtrip(self):
        c = Criterion(id="C-01", original_text="A sword", layer=2,
                      blocks=["C-02"], evidence=["img.png"])
        d = c.to_dict()
        c2 = Criterion.from_dict(d)
        assert c2.id == c.id
        assert c2.original_text == c.original_text
        assert c2.layer == 2
        assert c2.blocks == ["C-02"]
        assert c2.evidence == ["img.png"]

    def test_from_dict_ignores_extra_keys(self):
        d = {"id": "C-01", "original_text": "test", "unknown_field": True}
        c = Criterion.from_dict(d)
        assert c.id == "C-01"

    def test_default_fields(self):
        c = Criterion(id="C-01", original_text="x")
        assert c.verification_method == "visual"
        assert c.verification_steps == []
        assert c.evidence == []
        assert c.blocks == []
        assert c.reference_screenshot == ""
        assert c.notes == ""


# ===========================================================================
# D-S2: JSON serialization + SHA256 drift
# ===========================================================================

class TestSerialization:
    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        c = Criterion(id="C-01", original_text="Save test")
        save_criteria([c], path)
        loaded = load_criteria(path)
        assert len(loaded) == 1
        assert loaded[0].id == "C-01"
        assert loaded[0].original_text == "Save test"

    def test_load_nonexistent_returns_empty(self, tmp_path):
        assert load_criteria(str(tmp_path / "nope.json")) == []

    def test_drift_detection_exits(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        c = Criterion(id="C-01", original_text="original text")
        save_criteria([c], path)
        # Tamper with the text
        data = json.loads(Path(path).read_text())
        data[0]["original_text"] = "TAMPERED text"
        Path(path).write_text(json.dumps(data))
        with pytest.raises(SystemExit) as exc:
            load_criteria(path)
        assert exc.value.code == 2

    def test_no_drift_on_clean_load(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="clean")], path)
        loaded = load_criteria(path)
        assert len(loaded) == 1

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "deep" / "dir" / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="deep")], path)
        assert Path(path).exists()


# ===========================================================================
# D-S3: DAG resolution
# ===========================================================================

class TestDAG:
    def test_resolve_blocked_basic(self):
        c1 = Criterion(id="C-01", original_text="base", status="fail",
                        blocks=["C-02"])
        c2 = Criterion(id="C-02", original_text="dependent", status="pending")
        changed = resolve_blocked([c1, c2])
        assert c2.status == "blocked"
        assert "C-01" in c2.notes
        assert changed >= 1

    def test_resolve_blocked_pass_does_not_block(self):
        c1 = Criterion(id="C-01", original_text="base", status="pass",
                        blocks=["C-02"])
        c2 = Criterion(id="C-02", original_text="dependent", status="pending")
        resolve_blocked([c1, c2])
        assert c2.status == "pending"

    def test_get_dag_ready_skips_blocked(self):
        c1 = Criterion(id="C-01", original_text="a", status="fail",
                        blocks=["C-02"])
        c2 = Criterion(id="C-02", original_text="b", status="blocked")
        c3 = Criterion(id="C-03", original_text="c", status="pending")
        ready = get_dag_ready([c1, c2, c3])
        ids = [c.id for c in ready]
        assert "C-02" not in ids
        assert "C-03" in ids

    def test_get_dag_ready_filters_layer(self):
        c1 = Criterion(id="C-01", original_text="a", status="pending", layer=1)
        c2 = Criterion(id="C-02", original_text="b", status="pending", layer=2)
        ready = get_dag_ready([c1, c2], layer=1)
        assert len(ready) == 1
        assert ready[0].id == "C-01"

    def test_get_dag_ready_layer_0_returns_all(self):
        c1 = Criterion(id="C-01", original_text="a", status="pending", layer=1)
        c2 = Criterion(id="C-02", original_text="b", status="pending", layer=2)
        ready = get_dag_ready([c1, c2], layer=0)
        assert len(ready) == 2


# ===========================================================================
# D-S6: Layer filtering
# ===========================================================================

class TestLayerFiltering:
    def test_filter_by_layer(self):
        cs = [Criterion(id="C-01", original_text="a", layer=1),
              Criterion(id="C-02", original_text="b", layer=2)]
        assert len(filter_by_layer(cs, 1)) == 1
        assert len(filter_by_layer(cs, 0)) == 2

    def test_layer_summary(self):
        cs = [Criterion(id="C-01", original_text="a", status="pass", layer=1),
              Criterion(id="C-02", original_text="b", status="fail", layer=1),
              Criterion(id="C-03", original_text="c", status="pending", layer=2)]
        s = layer_summary(cs, 1)
        assert s["total"] == 2
        assert s["passed"] == 1
        assert s["failed"] == 1
        assert s["pass_rate"] == 50.0


# ===========================================================================
# D-S7: Regression
# ===========================================================================

class TestRegression:
    def test_check_regression_returns_passed(self):
        cs = [Criterion(id="C-01", original_text="a", status="pass"),
              Criterion(id="C-02", original_text="b", status="fail")]
        result = check_regression(cs)
        assert len(result) == 1
        assert result[0].id == "C-01"

    def test_check_regression_empty(self):
        assert check_regression([]) == []


# ===========================================================================
# D-S10: Budget enforcement
# ===========================================================================

class TestBudget:
    def test_budget_not_exhausted(self):
        cs = [Criterion(id="C-01", original_text="a", attempts=2)]
        b = check_budget(cs, 3, 100, 60, time.time())
        assert b["exhausted"] == []

    def test_budget_iterations_exhausted(self):
        cs = [Criterion(id="C-01", original_text="a", attempts=100)]
        b = check_budget(cs, 3, 100, 0, 0)
        assert any("max_iterations" in e for e in b["exhausted"])

    def test_budget_time_exhausted(self):
        old_time = time.time() - 7200  # 2 hours ago
        cs = [Criterion(id="C-01", original_text="a", attempts=1)]
        b = check_budget(cs, 3, 100, 60, old_time)
        assert any("time_budget" in e for e in b["exhausted"])

    def test_over_max_attempts(self):
        cs = [Criterion(id="C-01", original_text="a", attempts=5, status="fail")]
        b = check_budget(cs, 3, 100, 0, 0)
        assert "C-01" in b["over_max_attempts"]


# ===========================================================================
# D-S4/D-S9: CLI subcommands
# ===========================================================================

class TestCLI:
    def test_add_criterion(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "add-criterion", "--id", "C-01",
                                 "--text", "A red button",
                                 "--method", "visual", "--layer", "1"]):
            ret = va.main()
        assert ret == 0
        loaded = load_criteria(path)
        assert len(loaded) == 1
        assert loaded[0].original_text == "A red button"

    def test_add_criterion_duplicate(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "add-criterion", "--id", "C-01",
                                 "--text", "y", "--method", "visual"]):
            ret = va.main()
        assert ret == 1

    def test_update_pass_requires_evidence(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "update", "--id", "C-01",
                                 "--status", "pass"]):
            ret = va.main()
        assert ret == 1  # no evidence

    def test_update_fail_requires_notes(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "update", "--id", "C-01",
                                 "--status", "fail"]):
            ret = va.main()
        assert ret == 1  # no notes

    def test_update_pass_with_evidence(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "update", "--id", "C-01",
                                 "--status", "pass",
                                 "--evidence", "screenshot.png"]):
            ret = va.main()
        assert ret == 0
        loaded = load_criteria(path)
        assert loaded[0].status == "pass"
        assert loaded[0].reference_screenshot == "screenshot.png"
        assert "screenshot.png" in loaded[0].evidence

    def test_update_fail_with_notes(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "update", "--id", "C-01",
                                 "--status", "fail",
                                 "--notes", "button missing"]):
            ret = va.main()
        assert ret == 0
        loaded = load_criteria(path)
        assert loaded[0].status == "fail"
        assert loaded[0].notes == "button missing"

    def test_update_increments_attempts(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "update", "--id", "C-01",
                                 "--status", "fail", "--notes", "n"]):
            va.main()
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "update", "--id", "C-01",
                                 "--status", "fail", "--notes", "n2"]):
            va.main()
        loaded = load_criteria(path)
        assert loaded[0].attempts == 2

    def test_next_returns_pending(self, tmp_path, capsys):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="First criterion",
                      verification_method="functional"),
            Criterion(id="C-02", original_text="Second criterion",
                      verification_method="visual"),
        ], path)
        with patch("sys.argv", ["va", "--criteria-path", path, "next"]):
            ret = va.main()
        assert ret == 0
        out = capsys.readouterr().out
        assert "C-01" in out  # functional first

    def test_next_all_pass(self, tmp_path, capsys):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="x", status="pass"),
        ], path)
        with patch("sys.argv", ["va", "--criteria-path", path, "next"]):
            va.main()
        out = capsys.readouterr().out
        assert "All criteria verified" in out or "complete" in out.lower()

    def test_check_regression_lists_passed(self, tmp_path, capsys):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="passed criterion", status="pass",
                      reference_screenshot="ref.png"),
        ], path)
        with patch("sys.argv", ["va", "--criteria-path", path, "check-regression"]):
            ret = va.main()
        assert ret == 0
        out = capsys.readouterr().out
        assert "C-01" in out
        assert "ref.png" in out
        event_log = tmp_path / ".controlcoding" / "event_log.jsonl"
        assert event_log.exists()
        assert event_log.resolve().is_relative_to(tmp_path.resolve())

    def test_convergence_status(self, tmp_path, capsys):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="x", status="pass", attempts=1),
            Criterion(id="C-02", original_text="y", status="fail", attempts=2),
        ], path)
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "convergence-status"]):
            ret = va.main()
        assert ret == 0
        out = capsys.readouterr().out
        assert "1/2 pass" in out


# ===========================================================================
# D-S8: Report generation
# ===========================================================================

class TestReport:
    def test_report_generates_files(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="A visible sword", status="pass",
                      evidence=["sword.png"], attempts=1),
            Criterion(id="C-02", original_text="Sparks on wall hit", status="fail",
                      notes="particle bug", attempts=3),
        ], path)
        json_out = str(tmp_path / "report.json")
        md_out = str(tmp_path / "report.md")
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "report", "--output", json_out,
                                 "--md-output", md_out,
                                 "--design", "CLAUDE.md"]):
            ret = va.main()
        assert Path(json_out).exists()
        assert Path(md_out).exists()
        data = json.loads(Path(json_out).read_text())
        assert data["total_criteria"] == 2
        assert data["passed"] == 1
        assert data["failed"] == 1
        md = Path(md_out).read_text()
        assert "A visible sword" in md
        assert "Sparks on wall hit" in md
        assert "FAIL" in md

    def test_report_pass_rate(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id=f"C-{i:02d}", original_text=f"criterion {i}",
                      status="pass", evidence=["e.png"], attempts=1)
            for i in range(10)
        ], path)
        json_out = str(tmp_path / "report.json")
        with patch("sys.argv", ["va", "--criteria-path", path,
                                 "report", "--output", json_out,
                                 "--md-output", str(tmp_path / "r.md")]):
            ret = va.main()
        assert ret == 0  # 100% >= 90%
        data = json.loads(Path(json_out).read_text())
        assert data["pass_rate"] == "100.0%"


# ===========================================================================
# D-S13: Next subcommand with DAG-aware ordering
# ===========================================================================

class TestNextDAG:
    def test_next_skips_blocked(self, tmp_path, capsys):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="base", status="fail",
                      blocks=["C-02"]),
            Criterion(id="C-02", original_text="dependent", status="pending"),
            Criterion(id="C-03", original_text="independent", status="pending"),
        ], path)
        with patch("sys.argv", ["va", "--criteria-path", path, "next"]):
            va.main()
        out = capsys.readouterr().out
        # C-02 should be blocked, C-01 is fail (returned as candidate), C-03 pending
        assert "C-03" in out or "C-01" in out
        assert "C-02" not in out.split("[NEXT]")[1] if "[NEXT]" in out else True

    def test_next_functional_before_visual(self, tmp_path, capsys):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="visual check",
                      verification_method="visual"),
            Criterion(id="C-02", original_text="functional check",
                      verification_method="functional"),
        ], path)
        with patch("sys.argv", ["va", "--criteria-path", path, "next"]):
            va.main()
        out = capsys.readouterr().out
        assert "C-02" in out  # functional first
