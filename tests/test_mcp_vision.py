"""Tests for mcp_vision.py - Vision Agent MCP server."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import verification_agent as va
from verification_agent import Criterion, save_criteria, load_criteria

# Import MCP tools as regular functions (they work without fastmcp server)
import mcp_vision


@pytest.fixture(autouse=True)
def _isolated_relative_defaults(tmp_path, monkeypatch):
    """Keep the verification event log inside the per-test directory."""
    (tmp_path / ".controlcoding").mkdir()
    monkeypatch.chdir(tmp_path)


class TestVisionAddCriterion:
    def test_add_basic(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        result = mcp_vision.vision_add_criterion(
            criterion_id="C-01", text="A red button",
            method="visual", layer=1, criteria_path=path)
        assert "Added C-01" in result
        loaded = load_criteria(path)
        assert len(loaded) == 1
        assert loaded[0].original_text == "A red button"

    def test_add_duplicate(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        result = mcp_vision.vision_add_criterion(
            criterion_id="C-01", text="y", criteria_path=path)
        assert "ERROR" in result

    def test_add_invalid_method(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        result = mcp_vision.vision_add_criterion(
            criterion_id="C-01", text="x", method="magic", criteria_path=path)
        assert "ERROR" in result

    def test_add_with_blocks_and_steps(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        result = mcp_vision.vision_add_criterion(
            criterion_id="C-01", text="Sword visible", blocks="C-02,C-03",
            steps="take screenshot; check blade visible", criteria_path=path)
        loaded = load_criteria(path)
        assert loaded[0].blocks == ["C-02", "C-03"]
        assert len(loaded[0].verification_steps) == 2


class TestVisionUpdate:
    def test_update_pass(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        result = mcp_vision.vision_update(
            criterion_id="C-01", status="pass",
            evidence="shot.png", criteria_path=path)
        assert "pass" in result
        loaded = load_criteria(path)
        assert loaded[0].status == "pass"
        assert loaded[0].reference_screenshot == "shot.png"

    def test_update_pass_no_evidence(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        result = mcp_vision.vision_update(
            criterion_id="C-01", status="pass", criteria_path=path)
        assert "ERROR" in result

    def test_update_fail_no_notes(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        result = mcp_vision.vision_update(
            criterion_id="C-01", status="fail", criteria_path=path)
        assert "ERROR" in result

    def test_update_fail_with_notes(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([Criterion(id="C-01", original_text="x")], path)
        result = mcp_vision.vision_update(
            criterion_id="C-01", status="fail",
            notes="button missing", criteria_path=path)
        assert "fail" in result
        event_log = tmp_path / ".controlcoding" / "event_log.jsonl"
        assert event_log.exists()
        assert event_log.resolve().is_relative_to(tmp_path.resolve())

    def test_update_not_found(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([], path)
        result = mcp_vision.vision_update(
            criterion_id="C-99", status="pass",
            evidence="x", criteria_path=path)
        assert "ERROR" in result


class TestVisionNext:
    def test_next_returns_pending(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="functional thing",
                      verification_method="functional"),
            Criterion(id="C-02", original_text="visual thing",
                      verification_method="visual"),
        ], path)
        result = mcp_vision.vision_next(criteria_path=path)
        assert "C-01" in result  # functional first

    def test_next_all_complete(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="x", status="pass"),
        ], path)
        result = mcp_vision.vision_next(criteria_path=path)
        assert "COMPLETE" in result or "complete" in result

    def test_next_empty(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        result = mcp_vision.vision_next(criteria_path=path)
        assert "No criteria" in result


class TestVisionCheckRegression:
    def test_regression_lists_passed(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="passed thing", status="pass",
                      reference_screenshot="ref.png"),
        ], path)
        result = mcp_vision.vision_check_regression(criteria_path=path)
        assert "C-01" in result
        assert "ref.png" in result

    def test_regression_empty(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="x", status="fail"),
        ], path)
        result = mcp_vision.vision_check_regression(criteria_path=path)
        assert "No passed" in result


class TestVisionReport:
    def test_report_generates_files(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="Sword visible", status="pass",
                      evidence=["sword.png"], attempts=1),
        ], path)
        result = mcp_vision.vision_report(
            criteria_path=path, output_dir=str(tmp_path / "out"))
        assert "Passed: 1" in result
        assert (tmp_path / "out" / "verification_report.json").exists()
        assert (tmp_path / "out" / "verification.md").exists()

    def test_report_empty(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        result = mcp_vision.vision_report(criteria_path=path)
        assert "No criteria" in result


class TestVisionConvergenceStatus:
    def test_status(self, tmp_path):
        path = str(tmp_path / "criteria.json")
        save_criteria([
            Criterion(id="C-01", original_text="x", status="pass", attempts=1),
            Criterion(id="C-02", original_text="y", status="fail", attempts=2),
        ], path)
        result = mcp_vision.vision_convergence_status(criteria_path=path)
        assert "1/2 pass" in result
        assert "50.0%" in result


class TestMCPToolRegistration:
    """Verify that all tools are registered with the MCP server."""

    def test_server_has_tools(self):
        # FastMCP stores tools internally; check they're callable
        assert callable(mcp_vision.vision_add_criterion)
        assert callable(mcp_vision.vision_update)
        assert callable(mcp_vision.vision_next)
        assert callable(mcp_vision.vision_check_regression)
        assert callable(mcp_vision.vision_report)
        assert callable(mcp_vision.vision_convergence_status)

    def test_tool_count(self):
        """At least 6 tools registered."""
        tools = [name for name in dir(mcp_vision)
                 if name.startswith("vision_") and callable(getattr(mcp_vision, name))]
        assert len(tools) >= 6
