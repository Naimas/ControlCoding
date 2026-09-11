"""Tests for CodeWarden Sprint 2a: structured output, domain detection,
auto-import, event logging.

Tests the new functions in violation_store.py, codewarden_backend.py,
and codewarden_review.py without calling actual LLM backends.
"""

import builtins
import io
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "templates" / "hooks"
SCRIPTS_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from violation_store import (
    CW_SEVERITIES, CW_CATEGORIES,
    record_structured_violations, parse_structured_review,
    record_violation, read_recent_violations,
)
from codewarden_backend import call_model, detect_domain, DOMAIN_CHECKLISTS


class TestProviderNeutralCodeWardenBackend:
    @pytest.mark.parametrize(
        "backend",
        [None, "", "   ", "fallback", "unknown-provider"],
    )
    def test_invalid_or_missing_backend_skips_without_adapter_call(
            self, monkeypatch, backend):
        if backend is None:
            monkeypatch.delenv("CODEWARDEN_BACKEND", raising=False)
        else:
            monkeypatch.setenv("CODEWARDEN_BACKEND", backend)
        monkeypatch.setenv("CODEWARDEN_CONSENT", "approved")
        with patch("codewarden_backend.call_claude") as claude, \
             patch("codewarden_backend.call_anthropic") as anthropic, \
             patch("codewarden_backend.call_openai") as openai, \
             patch("codewarden_backend.call_ollama") as ollama:
            result = call_model("review")
        assert result.startswith("[ERROR] CodeWarden skipped:")
        claude.assert_not_called()
        anthropic.assert_not_called()
        openai.assert_not_called()
        ollama.assert_not_called()

    def test_backend_selection_does_not_imply_consent(self, monkeypatch):
        monkeypatch.setenv("CODEWARDEN_BACKEND", "ollama")
        monkeypatch.delenv("CODEWARDEN_CONSENT", raising=False)
        with patch("codewarden_backend.call_ollama") as ollama:
            result = call_model("review")
        assert "approval is required" in result
        ollama.assert_not_called()

    def test_explicit_backend_and_consent_routes_once(self, monkeypatch):
        monkeypatch.setenv("CODEWARDEN_BACKEND", "openai")
        monkeypatch.setenv("CODEWARDEN_CONSENT", "approved")
        with patch("codewarden_backend.call_openai", return_value="ok") as openai:
            assert call_model("review") == "ok"
        openai.assert_called_once_with("review")


# ===========================================================================
# Helpers
# ===========================================================================

def _project_root(tmp_path):
    """Create a minimal project root with .claude/ dir."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    return tmp_path


def _read_store(tmp_path):
    """Read all violations from the JSONL store."""
    store = tmp_path / ".claude" / "codewarden_violations.jsonl"
    if not store.exists():
        return []
    lines = []
    for line in store.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


def _blocked_control_plane_import(real_import):
    def blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "control_plane_utils":
            raise ImportError("blocked control_plane_utils")
        return real_import(name, globals, locals, fromlist, level)

    return blocked


def _json_stdout_reason(captured):
    return json.loads(captured.out.strip())["reason"]


# ===========================================================================
# Schema constants
# ===========================================================================

class TestSchemaConstants:
    def test_severities(self):
        assert CW_SEVERITIES == {"critical", "high", "medium", "low"}

    def test_categories(self):
        assert "architecture" in CW_CATEGORIES
        assert "security" in CW_CATEGORIES
        assert "domain_invariant" in CW_CATEGORIES
        assert "impact" in CW_CATEGORIES
        assert len(CW_CATEGORIES) == 7


# ===========================================================================
# record_structured_violations
# ===========================================================================

class TestRecordStructuredViolations:
    def test_basic_record(self, tmp_path):
        root = _project_root(tmp_path)
        violations = [{
            "rule": "Store pattern",
            "file": "src/main.py",
            "line": 42,
            "severity": "high",
            "category": "architecture",
            "description": "Direct state mutation bypasses Store",
            "suggested_fix": "Use Store.dispatch()",
            "claude_md_section": "Section 6.4",
        }]
        results = record_structured_violations(root, violations, backend="ollama")
        assert len(results) == 1
        assert results[0]["id"] == "CW-001"
        assert results[0]["severity"] == "high"
        assert results[0]["category"] == "architecture"

    def test_id_auto_increment(self, tmp_path):
        root = _project_root(tmp_path)
        v1 = [{"description": "First violation"}]
        v2 = [{"description": "Second violation"}]
        record_structured_violations(root, v1)
        results = record_structured_violations(root, v2)
        assert results[0]["id"] == "CW-002"

    def test_invalid_severity_defaults_to_medium(self, tmp_path):
        root = _project_root(tmp_path)
        results = record_structured_violations(root, [
            {"description": "Bad severity", "severity": "extreme"},
        ])
        assert results[0]["severity"] == "medium"

    def test_invalid_category_defaults_to_architecture(self, tmp_path):
        root = _project_root(tmp_path)
        results = record_structured_violations(root, [
            {"description": "Bad category", "category": "unknown"},
        ])
        assert results[0]["category"] == "architecture"

    def test_backward_compat_fields(self, tmp_path):
        root = _project_root(tmp_path)
        results = record_structured_violations(root, [
            {"description": "Test violation"},
        ], hook="review", backend="ollama")
        r = results[0]
        # Legacy fields present
        assert "timestamp" in r
        assert r["hook"] == "review"
        assert r["backend"] == "ollama"
        assert r["rule_quoted"] == "Test violation"

    def test_architecture_tag_and_evidence_preserved(self, tmp_path):
        root = _project_root(tmp_path)
        results = record_structured_violations(root, [
            {
                "description": "UI mutates domain state directly",
                "category": "architecture",
                "architecture_tag": "misplaced_logic",
                "evidence": "fitness_check flagged ui/view.py line 12",
                "evidence_source": "fitness_check",
            },
        ], hook="review", backend="ollama")
        assert results[0]["architecture_tag"] == "misplaced_logic"
        assert results[0]["evidence"] == "fitness_check flagged ui/view.py line 12"
        assert results[0]["evidence_source"] == "fitness_check"

    def test_written_to_jsonl(self, tmp_path):
        root = _project_root(tmp_path)
        record_structured_violations(root, [
            {"description": "Stored violation", "severity": "critical"},
        ])
        stored = _read_store(tmp_path)
        assert len(stored) == 1
        assert stored[0]["severity"] == "critical"
        assert stored[0]["id"] == "CW-001"

    def test_multiple_violations(self, tmp_path):
        root = _project_root(tmp_path)
        violations = [
            {"description": "V1", "severity": "high"},
            {"description": "V2", "severity": "low"},
            {"description": "V3", "severity": "critical"},
        ]
        results = record_structured_violations(root, violations)
        assert len(results) == 3
        ids = [r["id"] for r in results]
        assert ids == ["CW-001", "CW-002", "CW-003"]

    def test_empty_violations_list(self, tmp_path):
        root = _project_root(tmp_path)
        results = record_structured_violations(root, [])
        assert results == []

    def test_coexists_with_legacy_records(self, tmp_path):
        root = _project_root(tmp_path)
        # Write legacy record first
        record_violation(root, hook="review", rule_quoted="old violation",
                         file="test.py", severity="warn", backend="ollama")
        # Now write structured
        record_structured_violations(root, [
            {"description": "New structured", "severity": "high"},
        ])
        stored = _read_store(tmp_path)
        assert len(stored) == 2
        # Legacy record has no "id" field
        assert "id" not in stored[0]
        # Structured record has "id" field
        assert stored[1]["id"] == "CW-001"


# ===========================================================================
# parse_structured_review
# ===========================================================================

class TestParseStructuredReview:
    def test_valid_json(self):
        raw = json.dumps({
            "violations": [
                {"rule": "R1", "file": "a.py", "line": 10,
                 "severity": "high", "description": "Bad"},
            ]
        })
        result = parse_structured_review(raw)
        assert len(result) == 1
        assert result[0]["rule"] == "R1"

    def test_json_in_code_fence(self):
        raw = '```json\n{"violations": [{"description": "Test"}]}\n```'
        result = parse_structured_review(raw)
        assert len(result) == 1

    def test_empty_violations(self):
        raw = '{"violations": []}'
        result = parse_structured_review(raw)
        assert result == []

    def test_bare_array(self):
        raw = '[{"description": "Test"}]'
        result = parse_structured_review(raw)
        assert len(result) == 1

    def test_invalid_json_returns_none(self):
        result = parse_structured_review("This is just text, no JSON here")
        assert result is None

    def test_json_with_surrounding_text(self):
        raw = 'Here is my analysis:\n```json\n{"violations": [{"rule": "R1"}]}\n```\nDone.'
        result = parse_structured_review(raw)
        assert len(result) == 1

    def test_no_violations_key_returns_none(self):
        raw = '{"analysis": "looks good"}'
        result = parse_structured_review(raw)
        assert result is None

    def test_embedded_array(self):
        raw = 'Some preamble [{"description": "Found it"}] some postamble'
        result = parse_structured_review(raw)
        assert len(result) == 1


# ===========================================================================
# detect_domain
# ===========================================================================

class TestDetectDomain:
    def test_financial(self):
        text = "This is a trading system with an order book and settlement engine."
        assert detect_domain(text) == "financial"

    def test_web(self):
        text = "A web application using REST API with authentication and frontend SPA."
        assert detect_domain(text) == "web"

    def test_game(self):
        text = "A 3D game with player inventory, rendering sprites, and save/load system."
        assert detect_domain(text) == "game"

    def test_physics(self):
        text = "A physics simulation modeling rigid body dynamics with conservation of energy."
        assert detect_domain(text) == "physics"

    def test_data(self):
        text = "A data pipeline using Kafka for stream ingestion with schema evolution."
        assert detect_domain(text) == "data"

    def test_generic_fallback(self):
        text = "A small utility script that does nothing special."
        assert detect_domain(text) == "generic"

    def test_case_insensitive(self):
        text = "TRADING SYSTEM with ORDER BOOK"
        assert detect_domain(text) == "financial"


# ===========================================================================
# DOMAIN_CHECKLISTS
# ===========================================================================

class TestDomainChecklists:
    def test_financial_checklist(self):
        cl = DOMAIN_CHECKLISTS["financial"]
        assert "decimal" in cl.lower() or "precision" in cl.lower()
        assert "transaction" in cl.lower()

    def test_web_checklist(self):
        cl = DOMAIN_CHECKLISTS["web"]
        assert "sql injection" in cl.lower()
        assert "xss" in cl.lower()

    def test_generic_is_empty(self):
        assert DOMAIN_CHECKLISTS["generic"] == ""

    def test_all_domains_present(self):
        for domain in ["financial", "web", "game", "physics", "data", "generic"]:
            assert domain in DOMAIN_CHECKLISTS


# ===========================================================================
# Integration: structured review -> store -> readable
# ===========================================================================

class TestIntegration:
    def test_parse_then_record(self, tmp_path):
        root = _project_root(tmp_path)
        raw = json.dumps({"violations": [
            {"rule": "No float money", "file": "ledger.py", "line": 15,
             "severity": "critical", "category": "domain_invariant",
             "description": "Using float for monetary value",
             "suggested_fix": "Use Decimal",
             "claude_md_section": "Section 3.1"},
            {"rule": "Missing validation", "file": "api.py", "line": 42,
             "severity": "medium", "category": "security",
             "description": "No input validation on endpoint",
             "suggested_fix": "Add validation middleware",
             "claude_md_section": "Section 5.2"},
        ]})
        parsed = parse_structured_review(raw)
        assert len(parsed) == 2

        records = record_structured_violations(root, parsed, backend="ollama")
        assert records[0]["id"] == "CW-001"
        assert records[1]["id"] == "CW-002"
        assert records[0]["severity"] == "critical"
        assert records[1]["category"] == "security"

        # Verify stored correctly
        stored = _read_store(tmp_path)
        assert len(stored) == 2
        assert stored[0]["file"] == "ledger.py"
        assert stored[1]["line"] == 42

    def test_stored_violations_readable_by_legacy(self, tmp_path):
        """Structured records can be read by read_recent_violations()."""
        root = _project_root(tmp_path)
        record_structured_violations(root, [
            {"description": "Test", "severity": "high"},
        ])
        recent = read_recent_violations(root)
        assert len(recent) == 1
        assert recent[0]["hook"] == "review"
        # Has both new and legacy fields
        assert "id" in recent[0]
        assert "rule_quoted" in recent[0]

    def test_severity_distribution(self, tmp_path):
        root = _project_root(tmp_path)
        violations = [
            {"description": "V1", "severity": "critical"},
            {"description": "V2", "severity": "high"},
            {"description": "V3", "severity": "high"},
            {"description": "V4", "severity": "medium"},
        ]
        records = record_structured_violations(root, violations)
        dist = {}
        for r in records:
            s = r["severity"]
            dist[s] = dist.get(s, 0) + 1
        assert dist == {"critical": 1, "high": 2, "medium": 1}


# ===========================================================================
# codewarden_review.py main() with mocked backend
# ===========================================================================

class TestCodewardenReviewMain:
    def _setup_project(self, tmp_path):
        """Create a minimal project structure for testing main()."""
        root = tmp_path / "project"
        root.mkdir()
        (root / ".claude").mkdir()
        (root / "CLAUDE.md").write_text(
            "# Test Project\n## Architecture Rules\n- No direct DB access\n",
            encoding="utf-8",
        )
        # Create a git repo with a diff
        (root / ".git").mkdir()
        return root

    @patch("codewarden_review.get_git_diff")
    @patch("codewarden_review.call_model")
    @patch("codewarden_review.get_project_root")
    def test_structured_output_recorded(self, mock_root, mock_model,
                                         mock_diff, tmp_path):
        root = self._setup_project(tmp_path)
        mock_root.return_value = root
        mock_diff.return_value = "+import os\n+os.system('rm -rf /')"

        structured_response = json.dumps({"violations": [
            {"rule": "No direct DB", "file": "app.py", "line": 5,
             "severity": "high", "category": "architecture",
             "architecture_tag": "layer_violation",
             "description": "Direct DB access in controller",
             "suggested_fix": "Use repository pattern",
             "claude_md_section": "Architecture Rules",
             "evidence": "fitness_check flagged a layer rule violation",
             "evidence_source": "fitness_check"},
        ]})
        mock_model.return_value = structured_response

        import codewarden_review as cr
        with pytest.raises(SystemExit) as exc:
            cr.main()
        assert exc.value.code == 0

        # Check violations were stored
        stored = _read_store(root)
        assert len(stored) >= 1
        assert stored[-1]["id"].startswith("CW-")
        assert stored[-1]["severity"] == "high"
        assert stored[-1]["architecture_tag"] == "layer_violation"
        assert stored[-1]["evidence_source"] == "fitness_check"

    @patch("codewarden_review.get_git_diff")
    @patch("codewarden_review.call_model")
    @patch("codewarden_review.get_project_root")
    def test_legacy_fallback_on_non_json(self, mock_root, mock_model,
                                          mock_diff, tmp_path):
        root = self._setup_project(tmp_path)
        mock_root.return_value = root
        mock_diff.return_value = "+some change"
        mock_model.return_value = "Found a violation in line 10 of foo.py"

        import codewarden_review as cr
        with pytest.raises(SystemExit):
            cr.main()

        stored = _read_store(root)
        assert len(stored) >= 1
        # Legacy record (no structured id)
        assert "rule_quoted" in stored[-1]

    @patch("codewarden_review.get_git_diff")
    @patch("codewarden_review.get_project_root")
    def test_no_diff_exits_cleanly(self, mock_root, mock_diff, tmp_path):
        root = self._setup_project(tmp_path)
        mock_root.return_value = root
        mock_diff.return_value = ""

        import codewarden_review as cr
        with pytest.raises(SystemExit) as exc:
            cr.main()
        assert exc.value.code == 0

    @patch("codewarden_review.get_git_diff")
    @patch("codewarden_review.call_model")
    @patch("codewarden_review.get_project_root")
    def test_report_saved(self, mock_root, mock_model, mock_diff, tmp_path):
        root = self._setup_project(tmp_path)
        mock_root.return_value = root
        mock_diff.return_value = "+change"
        mock_model.return_value = '{"violations": []}'

        import codewarden_review as cr
        with pytest.raises(SystemExit):
            cr.main()

        report = root / ".claude" / "codewarden_report.md"
        assert report.exists()
        content = report.read_text(encoding="utf-8")
        assert "CodeWarden Review Report" in content

    @patch("codewarden_review.load_fitness_review_context")
    @patch("codewarden_review.get_git_diff")
    @patch("codewarden_review.call_model")
    @patch("codewarden_review.get_project_root")
    def test_prompt_includes_fitness_signals(self, mock_root, mock_model,
                                             mock_diff, mock_fitness, tmp_path):
        root = self._setup_project(tmp_path)
        mock_root.return_value = root
        mock_diff.return_value = "+change"
        mock_fitness.return_value = {
            "signal_count": 1,
            "focus_signal_count": 1,
            "prompt_section": "## Architectural Fitness Signals\n\n- [layer violation] [FAIL] `app.py`:5 - shared cannot import features",
            "report_section": "",
        }
        mock_model.return_value = '{"violations": []}'

        import codewarden_review as cr
        with pytest.raises(SystemExit):
            cr.main()

        prompt = mock_model.call_args[0][0]
        assert "Architectural Fitness Signals" in prompt
        assert "shared cannot import features" in prompt

    def test_save_report_separates_architecture_findings(self, tmp_path):
        root = self._setup_project(tmp_path)
        import codewarden_review as cr

        report_path = cr.save_report(
            root,
            '{"violations": []}',
            backend="ollama",
            diff_lines=3,
            fitness_review_section="## Architectural Fitness Evidence\n\n- [layer violation] [FAIL] `app.py`:5 - shared cannot import features",
            structured_violations=[
                {
                    "id": "CW-001",
                    "severity": "high",
                    "category": "architecture",
                    "architecture_tag": "layer_violation",
                    "description": "Shared layer imported features layer directly",
                    "file": "app.py",
                    "line": 5,
                    "rule": "Shared cannot import features",
                    "suggested_fix": "Move orchestration back into features/",
                    "evidence": "fitness_check flagged shared/api.py importing features/orders.py",
                    "evidence_source": "fitness_check",
                },
                {
                    "id": "CW-002",
                    "severity": "medium",
                    "category": "security",
                    "description": "Endpoint missing input validation",
                    "file": "api.py",
                    "line": 12,
                    "rule": "Validate external input",
                    "suggested_fix": "Add validation middleware",
                },
            ],
        )

        content = Path(report_path).read_text(encoding="utf-8")
        assert "## Architectural Fitness Evidence" in content
        assert "## Semantic Architecture Findings" in content
        assert "### Layer Violation" in content
        assert "## Other Structured Findings" in content

    def test_save_report_prefers_canonical_control_plane(self, tmp_path):
        root = self._setup_project(tmp_path)
        (root / ".controlcoding").mkdir()
        import codewarden_review as cr

        report_path = cr.save_report(
            root,
            '{"violations": []}',
            backend="ollama",
            diff_lines=1,
        )

        assert Path(report_path) == root / ".controlcoding" / "codewarden_report.md"

    def test_control_plane_utils_unavailable_blocks_without_model(
            self, tmp_path, capsys):
        root = self._setup_project(tmp_path)
        import codewarden_review as cr

        real_import = builtins.__import__
        with patch("codewarden_review.get_project_root", return_value=root), \
                patch("codewarden_review.get_git_diff") as mock_diff, \
                patch("codewarden_review.call_model") as mock_model, \
                patch(
                    "builtins.__import__",
                    side_effect=_blocked_control_plane_import(real_import),
                ):
            with pytest.raises(SystemExit) as exc:
                cr.main()

        assert exc.value.code == 2
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert _json_stdout_reason(captured) == "control_plane_utils_unavailable"
        assert "control_plane_utils_unavailable" in captured.err
        mock_diff.assert_not_called()
        mock_model.assert_not_called()

    def test_governance_check_exception_blocks_without_model(
            self, tmp_path, capsys):
        root = self._setup_project(tmp_path)
        import codewarden_review as cr

        with patch("codewarden_review.get_project_root", return_value=root), \
                patch("codewarden_review.get_git_diff") as mock_diff, \
                patch("codewarden_review.call_model") as mock_model, \
                patch(
                    "control_plane_utils.is_component_active",
                    side_effect=RuntimeError("boom"),
                ):
            with pytest.raises(SystemExit) as exc:
                cr.main()

        assert exc.value.code == 2
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert _json_stdout_reason(captured) == "governance_check_failed"
        assert "governance_check_failed" in captured.err
        mock_diff.assert_not_called()
        mock_model.assert_not_called()

    def test_component_inactive_skips_without_governance_failure(
            self, tmp_path, capsys):
        root = self._setup_project(tmp_path)
        import codewarden_review as cr

        with patch("codewarden_review.get_project_root", return_value=root), \
                patch("codewarden_review.get_git_diff") as mock_diff, \
                patch("codewarden_review.call_model") as mock_model, \
                patch(
                    "control_plane_utils.is_component_active",
                    return_value=False,
                ):
            with pytest.raises(SystemExit) as exc:
                cr.main()

        assert exc.value.code == 0
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert "governance_check_failed" not in captured.out
        assert "governance_check_failed" not in captured.err
        mock_diff.assert_not_called()
        mock_model.assert_not_called()


class TestCodewardenPlanReviewMain:
    def _setup_project(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        (root / ".claude").mkdir()
        (root / "CLAUDE.md").write_text(
            "# Test Project\n## Architecture Rules\n- No direct DB access\n",
            encoding="utf-8",
        )
        return root

    def test_malformed_stdin_exits_zero_without_traceback(
            self, tmp_path, capsys):
        root = self._setup_project(tmp_path)
        import codewarden_plan_review as pr

        real_import = builtins.__import__
        with patch("sys.stdin", io.StringIO("{broken json")), \
                patch("codewarden_plan_review.get_project_root",
                      return_value=root), \
                patch("codewarden_plan_review.call_model") as mock_model, \
                patch(
                    "builtins.__import__",
                    side_effect=_blocked_control_plane_import(real_import),
                ):
            with pytest.raises(SystemExit) as exc:
                pr.main()

        assert exc.value.code == 0
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Traceback" not in captured.err
        mock_model.assert_not_called()

    def test_control_plane_utils_unavailable_blocks_without_model(
            self, tmp_path, capsys):
        root = self._setup_project(tmp_path)
        import codewarden_plan_review as pr

        real_import = builtins.__import__
        with patch("sys.stdin", io.StringIO("{}")), \
                patch("codewarden_plan_review.get_project_root",
                      return_value=root), \
                patch("codewarden_plan_review.find_plan_file") as mock_find, \
                patch("codewarden_plan_review.call_model") as mock_model, \
                patch(
                    "builtins.__import__",
                    side_effect=_blocked_control_plane_import(real_import),
                ):
            with pytest.raises(SystemExit) as exc:
                pr.main()

        assert exc.value.code == 2
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert _json_stdout_reason(captured) == "control_plane_utils_unavailable"
        assert "control_plane_utils_unavailable" in captured.err
        mock_find.assert_not_called()
        mock_model.assert_not_called()

    def test_governance_check_exception_blocks_without_model(
            self, tmp_path, capsys):
        root = self._setup_project(tmp_path)
        import codewarden_plan_review as pr

        with patch("sys.stdin", io.StringIO("{}")), \
                patch("codewarden_plan_review.get_project_root",
                      return_value=root), \
                patch("codewarden_plan_review.find_plan_file") as mock_find, \
                patch("codewarden_plan_review.call_model") as mock_model, \
                patch(
                    "control_plane_utils.is_component_active",
                    side_effect=RuntimeError("boom"),
                ):
            with pytest.raises(SystemExit) as exc:
                pr.main()

        assert exc.value.code == 2
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert _json_stdout_reason(captured) == "governance_check_failed"
        assert "governance_check_failed" in captured.err
        mock_find.assert_not_called()
        mock_model.assert_not_called()

    def test_component_inactive_skips_without_governance_failure(
            self, tmp_path, capsys):
        root = self._setup_project(tmp_path)
        import codewarden_plan_review as pr

        with patch("sys.stdin", io.StringIO("{}")), \
                patch("codewarden_plan_review.get_project_root",
                      return_value=root), \
                patch("codewarden_plan_review.find_plan_file") as mock_find, \
                patch("codewarden_plan_review.call_model") as mock_model, \
                patch(
                    "control_plane_utils.is_component_active",
                    return_value=False,
                ):
            with pytest.raises(SystemExit) as exc:
                pr.main()

        assert exc.value.code == 0
        assert exc.value.code in (0, 2)
        captured = capsys.readouterr()
        assert "governance_check_failed" not in captured.out
        assert "governance_check_failed" not in captured.err
        mock_find.assert_not_called()
        mock_model.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
