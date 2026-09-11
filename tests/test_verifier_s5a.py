"""Tests for Verifier (Sprint 5a).

Tests: verify_code() wrapper, structured JSON output, 6 dimensions,
domain adaptation, parse_verifier_response, external_verification routing,
report saving, event logging, import-verify compatibility.
Minimum 25 tests. All backend calls mocked.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import mcp_consultant as mc
from mcp_consultant import (
    verify_code, save_verifier_report, _parse_verifier_response,
    VERIFIER_DIMENSIONS, VERIFIER_DOMAIN_CHECKLISTS, ROLE_PROMPTS, ROLE_CONFIG,
)


@pytest.fixture(autouse=True)
def _isolated_runtime_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mc, "PROJECT_ROOT", tmp_path)


# ===========================================================================
# Helpers
# ===========================================================================

def _mock_backend_response(findings=None, summary=None):
    """Build a valid verifier JSON response string."""
    if findings is None:
        findings = [{
            "id": "VF-001",
            "dimension": "sanity",
            "severity": "high",
            "file": "src/main.py",
            "line": 42,
            "description": "Float comparison without epsilon",
            "suggested_fix": "Use abs(a-b) < 1e-10",
            "evidence": "if price == target:",
        }]
    if summary is None:
        summary = {
            "total_findings": len(findings),
            "by_dimension": {"sanity": len(findings)},
            "by_severity": {"high": len(findings)},
            "overall_assessment": "Found issues",
        }
    return json.dumps({"findings": findings, "summary": summary})


def _patch_all_backends(response_text):
    """Return patches for all 4 backends to return the same response."""
    return [
        patch.object(mc, '_call_ollama', return_value=response_text),
        patch.object(mc, '_call_openai', return_value=response_text),
        patch.object(mc, '_call_anthropic', return_value=response_text),
        patch.object(mc, '_call_claude', return_value=response_text),
    ]


@pytest.fixture(autouse=True)
def _isolated_consult_log(tmp_path, monkeypatch):
    """Keep verifier consultation logs out of the repository control plane."""
    control_dir = tmp_path / ".controlcoding"
    control_dir.mkdir()
    monkeypatch.setattr(mc, "LOG_DIR", str(control_dir))


# ===========================================================================
# Role configuration
# ===========================================================================

class TestVerifierRole:
    def test_verifier_in_role_prompts(self):
        assert "verifier" in ROLE_PROMPTS
        prompt = ROLE_PROMPTS["verifier"]
        assert "independent" in prompt.lower()
        assert "NEVER seen this code" in prompt

    def test_verifier_in_role_config(self):
        assert "verifier" in ROLE_CONFIG
        assert ROLE_CONFIG["verifier"]["tools"] == []

    def test_verifier_prompt_mentions_6_dimensions(self):
        prompt = ROLE_PROMPTS["verifier"]
        assert "Plan alignment" in prompt
        assert "Architecture alignment" in prompt
        assert "Sanity" in prompt
        assert "Security" in prompt
        assert "Elegance" in prompt
        assert "Wiring" in prompt

    def test_verifier_prompt_requests_json(self):
        prompt = ROLE_PROMPTS["verifier"]
        assert "JSON" in prompt
        assert "findings" in prompt


# ===========================================================================
# Domain checklists
# ===========================================================================

class TestVerifierDomainChecklists:
    def test_financial_checklist(self):
        cl = VERIFIER_DOMAIN_CHECKLISTS["financial"]
        assert "decimal" in cl.lower() or "precision" in cl.lower()

    def test_web_checklist(self):
        cl = VERIFIER_DOMAIN_CHECKLISTS["web"]
        assert "sql injection" in cl.lower()

    def test_physics_checklist(self):
        cl = VERIFIER_DOMAIN_CHECKLISTS["physics"]
        assert "conservation" in cl.lower()

    def test_generic_is_empty(self):
        assert VERIFIER_DOMAIN_CHECKLISTS["generic"] == ""

    def test_all_domains_present(self):
        for d in ["financial", "web", "game", "physics", "data", "generic"]:
            assert d in VERIFIER_DOMAIN_CHECKLISTS


# ===========================================================================
# VERIFIER_DIMENSIONS
# ===========================================================================

class TestVerifierDimensions:
    def test_six_dimensions(self):
        assert len(VERIFIER_DIMENSIONS) == 6
        assert "plan_alignment" in VERIFIER_DIMENSIONS
        assert "sanity" in VERIFIER_DIMENSIONS
        assert "security" in VERIFIER_DIMENSIONS
        assert "wiring" in VERIFIER_DIMENSIONS


# ===========================================================================
# _parse_verifier_response
# ===========================================================================

class TestParseVerifierResponse:
    def test_valid_json(self):
        raw = _mock_backend_response()
        report = _parse_verifier_response(raw, "openai")
        assert report["backend"] == "openai"
        assert len(report["findings"]) == 1
        assert report["findings"][0]["id"] == "VF-001"

    def test_json_in_code_fence(self):
        raw = "```json\n" + _mock_backend_response() + "\n```"
        report = _parse_verifier_response(raw, "openai")
        assert len(report["findings"]) == 1

    def test_invalid_json_fallback(self):
        report = _parse_verifier_response("Not JSON at all", "openai")
        assert report["findings"] == []
        assert "Could not parse" in report["summary"]["overall_assessment"]

    def test_assigns_missing_ids(self):
        raw = json.dumps({"findings": [
            {"dimension": "sanity", "description": "Bug"},
            {"dimension": "security", "description": "Vuln"},
        ]})
        report = _parse_verifier_response(raw, "openai")
        assert report["findings"][0]["id"] == "VF-001"
        assert report["findings"][1]["id"] == "VF-002"

    def test_validates_dimension(self):
        raw = json.dumps({"findings": [
            {"id": "VF-001", "dimension": "invalid_dim", "description": "Bug"},
        ]})
        report = _parse_verifier_response(raw, "openai")
        assert report["findings"][0]["dimension"] == "sanity"  # default

    def test_validates_severity(self):
        raw = json.dumps({"findings": [
            {"id": "VF-001", "severity": "extreme", "description": "Bug"},
        ]})
        report = _parse_verifier_response(raw, "openai")
        assert report["findings"][0]["severity"] == "medium"  # default

    def test_builds_summary_if_missing(self):
        raw = json.dumps({"findings": [
            {"dimension": "sanity", "severity": "high", "description": "A"},
            {"dimension": "security", "severity": "critical", "description": "B"},
        ]})
        report = _parse_verifier_response(raw, "openai")
        summary = report["summary"]
        assert summary["total_findings"] == 2
        assert summary["by_dimension"]["sanity"] == 1
        assert summary["by_severity"]["critical"] == 1

    def test_extracts_files(self):
        raw = json.dumps({"findings": [
            {"file": "a.py", "description": "Bug"},
            {"file": "b.py", "description": "Bug"},
            {"file": "a.py", "description": "Another bug"},
        ]})
        report = _parse_verifier_response(raw, "openai")
        assert report["files_reviewed"] == ["a.py", "b.py"]


# ===========================================================================
# verify_code()
# ===========================================================================

class TestVerifyCode:
    @pytest.mark.parametrize("backend", ["", "   ", "fallback", "unknown"])
    def test_invalid_backend_has_zero_side_effects(self, tmp_path, backend):
        before = set(tmp_path.rglob("*"))
        with patch.object(mc, "_call_ollama") as ollama, \
             patch.object(mc, "_call_openai") as openai, \
             patch.object(mc, "_call_anthropic") as anthropic, \
             patch.object(mc, "_call_claude") as claude:
            report = verify_code(diff="+change", backend=backend)
        assert report["callStarted"] is False
        assert set(tmp_path.rglob("*")) == before
        ollama.assert_not_called()
        openai.assert_not_called()
        anthropic.assert_not_called()
        claude.assert_not_called()

    def test_basic_call(self):
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            mc._call_counter._count = 0
            report = verify_code(
                diff="+import os\n+os.system('rm -rf /')",
                claude_md="# Rules\n- No shell commands",
                backend="ollama",
            )
            assert "findings" in report
            assert "summary" in report
            assert report["backend"] == "ollama"
        finally:
            for p in patches:
                p.stop()

    def test_with_plan(self):
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            report = verify_code(
                diff="+some change",
                plan_json='{"phases": []}',
                backend="ollama",
            )
            assert report["findings"] is not None
        finally:
            for p in patches:
                p.stop()

    def test_with_domain(self):
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            report = verify_code(
                diff="+float_money = 3.14",
                domain="financial",
                backend="ollama",
            )
            assert "findings" in report
        finally:
            for p in patches:
                p.stop()

    def test_backend_error_handled(self):
        with patch.object(mc, '_call_openai', side_effect=ConnectionError("down")):
            report = verify_code(diff="+change", backend="openai")
            assert report.get("error")
            assert report["findings"] == []

    def test_focus_dimensions(self):
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            report = verify_code(
                diff="+change",
                focus=["sanity", "security"],
                backend="ollama",
            )
            assert "findings" in report
        finally:
            for p in patches:
                p.stop()


# ===========================================================================
# save_verifier_report
# ===========================================================================

class TestSaveVerifierReport:
    def test_save_and_load(self, tmp_path):
        report = {
            "timestamp": "2026-03-19T10:00:00Z",
            "backend": "openai",
            "findings": [{"id": "VF-001", "description": "Bug"}],
            "summary": {"total_findings": 1},
        }
        path = save_verifier_report(report, project_root=str(tmp_path))
        assert Path(path).exists()
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        assert loaded["findings"][0]["id"] == "VF-001"


# ===========================================================================
# import-verify compatibility
# ===========================================================================

class TestImportVerifyCompat:
    def test_report_importable(self, tmp_path):
        """Verifier report can be imported via import_verify."""
        import verification_agent as va

        # Create a verifier report
        report = {
            "timestamp": "2026-03-19T10:00:00Z",
            "backend": "openai",
            "findings": [
                {"id": "VF-001", "description": "Float comparison",
                 "file": "main.py", "fix_steps": ["Use epsilon"]},
                {"id": "VF-002", "description": "Missing validation",
                 "file": "api.py", "fix_steps": ["Add validation"]},
            ],
        }
        report_path = str(tmp_path / "verifier_report.json")
        Path(report_path).write_text(json.dumps(report), encoding="utf-8")

        criteria_path = str(tmp_path / "criteria.json")
        event_log = str(tmp_path / "event_log.jsonl")

        result = va.import_verify(report_path, criteria_path=criteria_path,
                                  event_log=event_log)
        assert result["imported"] == 2

        criteria = va.load_criteria(criteria_path)
        assert len(criteria) == 2
        assert criteria[0].id == "VF-001"
        assert criteria[0].status == "pending"


# ===========================================================================
# external_verification routing
# ===========================================================================

class TestExternalVerificationRouting:
    def test_get_external_criteria(self, tmp_path):
        import verification_agent as va
        cp = str(tmp_path / "criteria.json")
        va.save_criteria([
            va.Criterion(id="C-01", original_text="Normal",
                         external_verification=False),
            va.Criterion(id="C-02", original_text="External",
                         external_verification=True),
            va.Criterion(id="C-03", original_text="External but passed",
                         external_verification=True, status="pass"),
        ], cp)
        ext = va.get_external_criteria(cp)
        assert len(ext) == 1
        assert ext[0].id == "C-02"

    def test_no_external_criteria(self, tmp_path):
        import verification_agent as va
        cp = str(tmp_path / "criteria.json")
        va.save_criteria([
            va.Criterion(id="C-01", original_text="Normal"),
        ], cp)
        result = va.route_external_verification(criteria_path=cp, diff="+x")
        assert result["verified"] == 0

    @pytest.mark.parametrize("backend", ["", "   ", "fallback", "unknown"])
    def test_invalid_backend_has_no_runtime_side_effects(
            self, tmp_path, monkeypatch, backend):
        import verification_agent as va
        cp = str(tmp_path / "criteria.json")
        event_log = tmp_path / "event_log.jsonl"
        va.save_criteria([
            va.Criterion(
                id="C-01",
                original_text="External",
                external_verification=True,
                external_backend=backend,
            ),
        ], cp)
        imported = False
        original_import = __import__

        def guarded_import(name, *args, **kwargs):
            nonlocal imported
            if name == "mcp_consultant":
                imported = True
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", guarded_import)
        result = va.route_external_verification(
            criteria_path=cp,
            diff="+code",
            event_log=str(event_log),
        )

        assert result["verified"] == 0
        assert result["callStarted"] is False
        assert imported is False
        assert not event_log.exists()


# ===========================================================================
# MCP tool wrapper
# ===========================================================================

class TestConsultVerifyMCPTool:
    def test_returns_json(self, tmp_path, monkeypatch):
        control_dir = tmp_path / ".controlcoding"
        monkeypatch.chdir(tmp_path)
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            mc._call_counter._count = 0
            result_str = mc.consult_verify(
                diff="+change",
                backend="ollama",
            )
            result = json.loads(result_str)
            assert "findings" in result
            assert "summary" in result
            assert "raw_response" not in result  # stripped
            assert (control_dir / "verifier_report.json").exists()
            assert (control_dir / "event_log.jsonl").exists()
            assert (control_dir / "consult_log.jsonl").exists()
        finally:
            for p in patches:
                p.stop()


# ===========================================================================
# S5a.1 additions: batching, truncation, routing
# ===========================================================================

class TestBatchRouting:
    def test_batches_by_backend(self, tmp_path):
        """Multiple criteria with same backend = 1 verify_code call."""
        import verification_agent as va
        cp = str(tmp_path / "criteria.json")
        va.save_criteria([
            va.Criterion(id="C-01", original_text="Check A",
                         external_verification=True, external_backend="openai"),
            va.Criterion(id="C-02", original_text="Check B",
                         external_verification=True, external_backend="openai"),
            va.Criterion(id="C-03", original_text="Check C",
                         external_verification=True, external_backend="ollama"),
        ], cp)

        call_log = []
        def mock_verify_code(**kwargs):
            call_log.append(kwargs.get("backend", ""))
            return {"findings": [], "summary": {"total_findings": 0,
                    "by_dimension": {}, "by_severity": {},
                    "overall_assessment": "OK"}}

        # Mock mcp_consultant at module level so dynamic import finds it
        mock_mc = MagicMock()
        mock_mc.verify_code = mock_verify_code
        mock_mc.save_verifier_report = MagicMock()
        old = sys.modules.get("mcp_consultant")
        sys.modules["mcp_consultant"] = mock_mc
        try:
            result = va.route_external_verification(
                criteria_path=cp, diff="+code",
                event_log=str(tmp_path / "el.jsonl"))
        finally:
            if old is not None:
                sys.modules["mcp_consultant"] = old
            else:
                sys.modules.pop("mcp_consultant", None)

        assert result["verified"] == 3
        assert result["backends_called"] == 2
        # 2 calls: one for openai group, one for ollama group
        assert len(call_log) == 2
        assert "openai" in call_log
        assert "ollama" in call_log

    def test_correct_backend_per_criterion(self, tmp_path):
        """Each criterion's external_backend is respected."""
        import verification_agent as va
        cp = str(tmp_path / "criteria.json")
        va.save_criteria([
            va.Criterion(id="C-01", original_text="Check",
                         external_verification=True,
                         external_backend="anthropic"),
        ], cp)

        called_with = []
        def mock_vc(**kwargs):
            called_with.append(kwargs.get("backend"))
            return {"findings": [], "summary": {"total_findings": 0,
                    "by_dimension": {}, "by_severity": {},
                    "overall_assessment": "OK"}}

        old = sys.modules.get("mcp_consultant")
        mock_mc = MagicMock()
        mock_mc.verify_code = mock_vc
        mock_mc.save_verifier_report = lambda r: None
        sys.modules["mcp_consultant"] = mock_mc
        try:
            va.route_external_verification(
                criteria_path=cp, diff="+code",
                event_log=str(tmp_path / "el.jsonl"))
        finally:
            if old is not None:
                sys.modules["mcp_consultant"] = old
            else:
                sys.modules.pop("mcp_consultant", None)

        assert called_with == ["anthropic"]


class TestDiffTruncation:
    def test_short_diff_no_truncation(self):
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            mc._call_counter._count = 0
            report = verify_code(diff="+short change", backend="ollama")
            assert "truncated" not in report
            assert report.get("scope") == "incremental"
        finally:
            for p in patches:
                p.stop()

    def test_long_diff_marked_truncated(self):
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            mc._call_counter._count = 0
            long_diff = "+" + "x" * 20000
            report = verify_code(diff=long_diff, backend="ollama")
            assert "truncated" in report
            assert report["scope"] == "partial"
            assert any("diff" in t for t in report["truncated"])
        finally:
            for p in patches:
                p.stop()

    def test_long_claude_md_marked_truncated(self):
        response = _mock_backend_response()
        patches = _patch_all_backends(response)
        for p in patches:
            p.start()
        try:
            mc._call_counter._count = 0
            long_md = "# Rules\n" + "rule\n" * 5000
            report = verify_code(diff="+x", claude_md=long_md, backend="ollama")
            assert "truncated" in report
            assert any("CLAUDE.md" in t for t in report["truncated"])
        finally:
            for p in patches:
                p.stop()


class TestFocusDimensions:
    def test_focus_limits_prompt_content(self):
        """When focus excludes plan_alignment, plan_json should not appear in prompt."""
        captured_prompt = []

        def capture_ollama(prompt, model, system_prompt, image=None):
            captured_prompt.append(prompt)
            return _mock_backend_response()

        with patch.object(mc, '_call_ollama', side_effect=capture_ollama):
            mc._call_counter._count = 0
            verify_code(
                diff="+code",
                plan_json='{"phases": []}',
                claude_md="# Rules",
                focus=["security", "sanity"],
                backend="ollama",
            )

        assert len(captured_prompt) == 1
        prompt = captured_prompt[0]
        # Plan should NOT be in prompt since plan_alignment not in focus
        assert "Implementation Plan" not in prompt
        # Architecture should NOT be in prompt since architecture_alignment not in focus
        assert "Architecture Rules" not in prompt
        # Focus should mention selected dimensions
        assert "security" in prompt
        assert "sanity" in prompt


class TestRoutingIntegration:
    def test_route_logs_event(self, tmp_path):
        """route_external_verification logs verification_run event."""
        import verification_agent as va
        cp = str(tmp_path / "criteria.json")
        el = str(tmp_path / "el.jsonl")
        va.save_criteria([
            va.Criterion(id="C-01", original_text="Ext check",
                         external_verification=True, external_backend="ollama"),
        ], cp)

        mock_mc = MagicMock()
        mock_mc.verify_code = lambda **kw: {
            "findings": [], "summary": {"total_findings": 0,
            "by_dimension": {}, "by_severity": {},
            "overall_assessment": "OK"}}
        mock_mc.save_verifier_report = MagicMock()
        old = sys.modules.get("mcp_consultant")
        sys.modules["mcp_consultant"] = mock_mc
        try:
            va.route_external_verification(
                criteria_path=cp, diff="+code", event_log=el)
        finally:
            if old is not None:
                sys.modules["mcp_consultant"] = old
            else:
                sys.modules.pop("mcp_consultant", None)

        events = []
        if Path(el).exists():
            for line in Path(el).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        assert any(e["event"] == "verification_run" for e in events)


class TestCheckRegressionEvent:
    def test_regression_logs_event(self, tmp_path):
        """check-regression now logs a verification_run event."""
        import verification_agent as va
        from unittest.mock import patch as _patch
        cp = str(tmp_path / "criteria.json")
        el = str(tmp_path / "el.jsonl")
        va.save_criteria([
            va.Criterion(id="C-01", original_text="passed",
                         status="pass", attempts=2),
        ], cp)
        old_el = va.DEFAULT_EVENT_LOG
        va.DEFAULT_EVENT_LOG = el
        try:
            with _patch("sys.argv", ["va", "--criteria-path", cp,
                                      "check-regression"]):
                va.main()
        finally:
            va.DEFAULT_EVENT_LOG = old_el

        events = []
        if Path(el).exists():
            for line in Path(el).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        assert any(e.get("details", {}).get("type") == "regression_check"
                   for e in events)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
