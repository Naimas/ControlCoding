#!/usr/bin/env python3
"""Tests for cc organize command.

Covers: cmd_organize, _scan_devlog_files, _scan_project_docs,
_classify_root_doc, _generate_index, DEVLOG_SUBDIRS, ORGANIZE_PATTERNS.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
if "cc" in sys.modules:
    del sys.modules["cc"]
import cc


# ------------------------------------------------------------------ fixtures ---


def _assert_pure_json_object(stdout: str) -> dict:
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    assert stdout.lstrip().startswith("{")
    assert stdout.rstrip().endswith("}")
    decoded, end = json.JSONDecoder().raw_decode(stdout)
    assert isinstance(decoded, dict)
    assert stdout[end:].strip() == ""
    return payload


def _run_cc_main(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["cc", *(str(arg) for arg in args)])
    return cc.main()


def _make_misplaced_organize_project(root: Path) -> Path:
    """Create a minimal project with one misplaced devlog file."""
    devlog = root / "devlog"
    devlog.mkdir()
    for subdir in cc.DEVLOG_SUBDIRS:
        (devlog / subdir).mkdir()
    (devlog / "design-combat.md").write_text("# Combat", encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path):
    """Create a minimal project with devlog/."""
    (tmp_path / "devlog").mkdir()
    return tmp_path


@pytest.fixture
def project_with_files(tmp_path):
    """Project with various files in devlog/ root (misplaced)."""
    devlog = tmp_path / "devlog"
    devlog.mkdir()
    (devlog / "plan-v1-draft.json").write_text('{"v": 1}', encoding="utf-8")
    (devlog / "plan-v2-approved.json").write_text('{"v": 2}', encoding="utf-8")
    (devlog / "design-rendering.md").write_text("# Rendering", encoding="utf-8")
    (devlog / "design-overview.md").write_text("# Overview", encoding="utf-8")
    (devlog / "reasoning-phase-1.md").write_text("# Phase 1", encoding="utf-8")
    (devlog / "criteria-v1.md").write_text("# Criteria", encoding="utf-8")
    (devlog / "random-notes.md").write_text("# Notes", encoding="utf-8")
    return tmp_path


# ----------------------------------------------------------- test subdirs ---


def test_organize_creates_subdirs(project):
    """cc organize creates all standard subdirectories."""
    cc.cmd_organize(project, apply=True)
    for subdir in cc.DEVLOG_SUBDIRS:
        assert (project / "devlog" / subdir).is_dir(), f"Missing devlog/{subdir}"


def test_organize_creates_devlog_if_missing(tmp_path):
    """cc organize creates devlog/ if it does not exist."""
    cc.cmd_organize(tmp_path, apply=True)
    assert (tmp_path / "devlog").is_dir()


def test_organize_idempotent(project):
    """Running organize twice does not fail or duplicate dirs."""
    cc.cmd_organize(project, apply=True)
    cc.cmd_organize(project, apply=True)
    for subdir in cc.DEVLOG_SUBDIRS:
        assert (project / "devlog" / subdir).is_dir()


# ----------------------------------------------------------- test scanning ---


def test_scan_devlog_classifies_plans(project_with_files):
    """Plans in devlog/ root are classified as plans."""
    files = cc._scan_devlog_files(project_with_files)
    plans = [f for f in files if f["suggested_subdir"] == "plans"]
    assert len(plans) == 2
    names = {f["name"] for f in plans}
    assert "plan-v1-draft.json" in names
    assert "plan-v2-approved.json" in names


def test_scan_devlog_classifies_design(project_with_files):
    """Design docs in devlog/ root are classified as design."""
    files = cc._scan_devlog_files(project_with_files)
    design = [f for f in files if f["suggested_subdir"] == "design"]
    assert len(design) == 2


def test_scan_devlog_classifies_reasoning(project_with_files):
    """Reasoning logs in devlog/ root are classified as reasoning."""
    files = cc._scan_devlog_files(project_with_files)
    reasoning = [f for f in files if f["suggested_subdir"] == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0]["name"] == "reasoning-phase-1.md"


def test_scan_devlog_classifies_criteria(project_with_files):
    """Criteria files in devlog/ root are classified as criteria."""
    files = cc._scan_devlog_files(project_with_files)
    criteria = [f for f in files if f["suggested_subdir"] == "criteria"]
    assert len(criteria) == 1


def test_scan_devlog_unclassified_stays_none(project_with_files):
    """Files not matching any pattern have suggested_subdir=None."""
    files = cc._scan_devlog_files(project_with_files)
    unmatched = [f for f in files if f["suggested_subdir"] is None]
    assert len(unmatched) == 1
    assert unmatched[0]["name"] == "random-notes.md"


# --------------------------------------------------------- test root scan ---


def test_scan_project_docs_finds_stray_plans(tmp_path):
    """Plans in project root are detected as stray docs."""
    (tmp_path / "devlog").mkdir()
    (tmp_path / "plan-v1-approved.json").write_text("{}", encoding="utf-8")
    docs = cc._scan_project_docs(tmp_path)
    assert len(docs) == 1
    assert docs[0]["suggested_subdir"] == "plans"


def test_scan_project_docs_ignores_devlog(tmp_path):
    """Files already in devlog/ are not reported as stray."""
    devlog = tmp_path / "devlog"
    devlog.mkdir()
    (devlog / "plan-v1-draft.json").write_text("{}", encoding="utf-8")
    docs = cc._scan_project_docs(tmp_path)
    assert len(docs) == 0


# ----------------------------------------------------------- test moving ---


def test_organize_moves_files(project_with_files):
    """cc organize moves files to correct subdirectories."""
    cc.cmd_organize(project_with_files, apply=True)

    devlog = project_with_files / "devlog"
    # Plans moved
    assert (devlog / "plans" / "plan-v1-draft.json").exists()
    assert (devlog / "plans" / "plan-v2-approved.json").exists()
    # Design moved
    assert (devlog / "design" / "design-rendering.md").exists()
    assert (devlog / "design" / "design-overview.md").exists()
    # Reasoning moved
    assert (devlog / "reasoning" / "reasoning-phase-1.md").exists()
    # Criteria moved
    assert (devlog / "criteria" / "criteria-v1.md").exists()
    # Unclassified stays in root
    assert (devlog / "random-notes.md").exists()


def test_organize_does_not_overwrite(project_with_files):
    """If destination exists, the file is not moved (skip with warning)."""
    devlog = project_with_files / "devlog"
    plans = devlog / "plans"
    plans.mkdir()
    # Pre-place a file at the destination
    (plans / "plan-v1-draft.json").write_text('{"existing": true}',
                                              encoding="utf-8")
    cc.cmd_organize(project_with_files, apply=True)
    # Original at destination is preserved
    content = json.loads(
        (plans / "plan-v1-draft.json").read_text(encoding="utf-8")
    )
    assert content == {"existing": True}


# ----------------------------------------------------------- test dry-run ---


def test_organize_dry_run_no_moves(project_with_files):
    """Dry run proposes but does not execute moves."""
    cc.cmd_organize(project_with_files, dry_run=True)
    devlog = project_with_files / "devlog"
    # Files should still be in root
    assert (devlog / "plan-v1-draft.json").exists()
    assert not (devlog / "plans").exists() or not (
        devlog / "plans" / "plan-v1-draft.json"
    ).exists()


def test_organize_default_preview_no_moves(project_with_files):
    """Default organize is preview-only and does not move files."""
    cc.cmd_organize(project_with_files)
    devlog = project_with_files / "devlog"
    assert (devlog / "plan-v1-draft.json").exists()
    assert not (devlog / "plans").exists() or not (
        devlog / "plans" / "plan-v1-draft.json"
    ).exists()


# ------------------------------------------------------ test dev taxonomy ---


def test_organize_dev_taxonomy_creates_baseline_dirs(tmp_path):
    """--dev-taxonomy creates the baseline dev/ category tree."""
    cc.cmd_organize(tmp_path, dev_taxonomy=True, apply=True)

    assert (tmp_path / "dev" / "plans").is_dir()
    assert (tmp_path / "dev" / "plans" / "archive").is_dir()
    assert (tmp_path / "dev" / "plans" / "deprecated").is_dir()
    assert (tmp_path / "dev" / "design").is_dir()
    assert (tmp_path / "dev" / "design" / "archive").is_dir()
    assert (tmp_path / "dev" / "design" / "deprecated").is_dir()
    assert (tmp_path / "dev" / "criteria").is_dir()
    assert (tmp_path / "dev" / "criteria" / "archive").is_dir()
    assert (tmp_path / "dev" / "criteria" / "deprecated").is_dir()


def test_organize_dev_taxonomy_moves_clear_root_docs(tmp_path):
    """--dev-taxonomy moves clearly named root docs into dev/."""
    (tmp_path / "plan-release.md").write_text("# Plan", encoding="utf-8")
    (tmp_path / "architecture.md").write_text("# Architecture", encoding="utf-8")
    (tmp_path / "criteria-v1.md").write_text("# Criteria", encoding="utf-8")
    (tmp_path / "research-notes.md").write_text("# Research", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Public", encoding="utf-8")

    cc.cmd_organize(tmp_path, dev_taxonomy=True, apply=True)

    assert (tmp_path / "dev" / "plans" / "plan-release.md").exists()
    assert (tmp_path / "dev" / "design" / "architecture.md").exists()
    assert (tmp_path / "dev" / "criteria" / "criteria-v1.md").exists()
    assert (tmp_path / "dev" / "research" / "research-notes.md").exists()
    assert (tmp_path / "README.md").exists()


def test_organize_dev_taxonomy_moves_acceptance_root_docs_to_dev_criteria(tmp_path):
    """--dev-taxonomy moves acceptance docs into dev/criteria, not devlog/criteria."""
    (tmp_path / "acceptance-v2.md").write_text("# Acceptance", encoding="utf-8")

    cc.cmd_organize(tmp_path, dev_taxonomy=True, apply=True)

    assert (tmp_path / "dev" / "criteria" / "acceptance-v2.md").exists()
    assert not (tmp_path / "devlog" / "criteria" / "acceptance-v2.md").exists()


def test_organize_dev_taxonomy_does_not_move_public_docs_dir(tmp_path):
    """--dev-taxonomy leaves public docs/ files in place."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# Public architecture", encoding="utf-8")
    (docs / "design-public.md").write_text("# Public design", encoding="utf-8")

    cc.cmd_organize(tmp_path, dev_taxonomy=True, apply=True)

    assert (docs / "architecture.md").exists()
    assert (docs / "design-public.md").exists()
    assert not (tmp_path / "dev" / "design" / "architecture.md").exists()
    assert not (tmp_path / "devlog" / "design" / "design-public.md").exists()


def test_organize_dev_taxonomy_corrects_wrong_dev_category(tmp_path):
    """--dev-taxonomy can move a classified doc from a wrong dev/ bucket."""
    misc = tmp_path / "dev" / "misc"
    misc.mkdir(parents=True)
    (misc / "plan-release.md").write_text("# Plan", encoding="utf-8")

    cc.cmd_organize(tmp_path, dev_taxonomy=True, apply=True)

    assert (tmp_path / "dev" / "plans" / "plan-release.md").exists()
    assert not (misc / "plan-release.md").exists()


def test_organize_dev_taxonomy_check_reports_misplaced_files(tmp_path):
    """--check --dev-taxonomy reports misplaced files without moving them."""
    plan = tmp_path / "plan-release.md"
    plan.write_text("# Plan", encoding="utf-8")

    ret = cc.cmd_organize(tmp_path, check=True, dev_taxonomy=True)

    assert ret == 1
    assert plan.exists()
    assert not (tmp_path / "dev" / "plans" / "plan-release.md").exists()


# --------------------------------------------------------- test index.md ---


def test_organize_generates_index(project):
    """cc organize generates devlog/index.md."""
    cc.cmd_organize(project, apply=True)
    assert (project / "devlog" / "index.md").exists()


def test_index_contains_files(project_with_files):
    """Generated index lists files in subdirectories."""
    cc.cmd_organize(project_with_files, apply=True)
    index = (project_with_files / "devlog" / "index.md").read_text(
        encoding="utf-8"
    )
    assert "plan-v1-draft.json" in index
    assert "design-rendering.md" in index
    assert "Auto-generated" in index


# --------------------------------------------------------- test json out ---


def test_organize_json_output(project_with_files, capsys):
    """JSON output mode produces valid JSON with report keys."""
    cc.cmd_organize(project_with_files, json_output=True, apply=True)
    report = _assert_pure_json_object(capsys.readouterr().out)
    assert "dirs_created" in report
    assert "moves_proposed" in report
    assert "moves_executed" in report
    assert "index_generated" in report


# ----------------------------------------------------- test pattern match ---


def test_organize_patterns_plan_variants():
    """ORGANIZE_PATTERNS matches plan files."""
    assert cc.ORGANIZE_PATTERNS["plans"]("plan-v1-draft.json")
    assert cc.ORGANIZE_PATTERNS["plans"]("plan-v3-approved.json")
    assert cc.ORGANIZE_PATTERNS["plans"]("plan_old.json")
    assert not cc.ORGANIZE_PATTERNS["plans"]("planner.py")


def test_organize_patterns_design_variants():
    """ORGANIZE_PATTERNS matches design files."""
    assert cc.ORGANIZE_PATTERNS["design"]("design-combat.md")
    assert cc.ORGANIZE_PATTERNS["design"]("design-overview.md")
    assert not cc.ORGANIZE_PATTERNS["design"]("designer.py")


def test_organize_patterns_reasoning_variants():
    """ORGANIZE_PATTERNS matches reasoning files."""
    assert cc.ORGANIZE_PATTERNS["reasoning"]("reasoning-phase-1.md")
    assert cc.ORGANIZE_PATTERNS["reasoning"]("reasoning-2026-03-20.md")
    assert cc.ORGANIZE_PATTERNS["reasoning"]("reasoning.md")


def test_organize_check_passes_when_clean(tmp_path):
    """--check returns 0 when devlog structure is correct and no misplaced files."""
    devlog = tmp_path / "devlog"
    devlog.mkdir()
    for sd in cc.DEVLOG_SUBDIRS:
        (devlog / sd).mkdir()
    ret = cc.cmd_organize(tmp_path, check=True)
    assert ret == 0


def test_organize_check_fails_missing_dirs(tmp_path):
    """--check returns 1 when devlog subdirectories are missing."""
    # devlog doesn't exist at all
    ret = cc.cmd_organize(tmp_path, check=True)
    assert ret == 1


def test_organize_check_fails_misplaced_files(tmp_path):
    """--check returns 1 when files are misplaced in devlog root."""
    devlog = tmp_path / "devlog"
    devlog.mkdir()
    for sd in cc.DEVLOG_SUBDIRS:
        (devlog / sd).mkdir()
    # Put a plan file in devlog root instead of devlog/plans/
    (devlog / "plan-v1-draft.json").write_text("{}", encoding="utf-8")
    ret = cc.cmd_organize(tmp_path, check=True)
    assert ret == 1


def test_organize_check_json_stdout_is_exact_json(tmp_path, monkeypatch, capsys):
    """--check --json emits exactly one JSON object on stdout."""
    _make_misplaced_organize_project(tmp_path)

    ret = _run_cc_main(
        monkeypatch, "organize", "--project-root", tmp_path, "--check", "--json"
    )
    captured = capsys.readouterr()

    assert ret == 1
    data = _assert_pure_json_object(captured.out)
    assert data["ok"] is False
    assert data["error"] == "organize_check_failed"
    assert data["message"] == (
        "File organization check failed: 1 misplaced file(s), 0 missing dir(s)."
    )
    assert data["violations"] == 1
    assert data["moves_proposed"][0]["from"].endswith("design-combat.md")
    assert captured.err == ""


def test_organize_check_non_json_remains_human(tmp_path, monkeypatch, capsys):
    """--check without --json preserves human diagnostics on stdout."""
    _make_misplaced_organize_project(tmp_path)

    ret = _run_cc_main(monkeypatch, "organize", "--project-root", tmp_path, "--check")
    captured = capsys.readouterr()

    assert ret == 1
    assert "Organizing files in:" in captured.out
    assert "misplaced file(s)" in captured.out
    assert "design-combat.md -> devlog/design/design-combat.md" in captured.out
    assert captured.err == ""


def test_organize_check_json_permission_error_is_pure_json(
    tmp_path, monkeypatch, capsys
):
    """--check --json maps filesystem errors to one JSON object."""
    def raise_permission_error(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(cc, "cmd_organize", raise_permission_error)

    ret = _run_cc_main(
        monkeypatch, "organize", "--project-root", tmp_path, "--check", "--json"
    )
    captured = capsys.readouterr()

    assert ret == 1
    data = _assert_pure_json_object(captured.out)
    assert data["ok"] is False
    assert data["error"] == "organize_failed"
    assert data["message"] == "permission denied"
    assert captured.err == ""


def test_organize_check_non_json_permission_error_has_no_traceback(
    tmp_path, monkeypatch, capsys
):
    """--check without --json reports filesystem errors without traceback."""
    def raise_permission_error(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(cc, "cmd_organize", raise_permission_error)

    ret = _run_cc_main(monkeypatch, "organize", "--project-root", tmp_path, "--check")
    captured = capsys.readouterr()

    assert ret == 1
    assert "Error: organize failed: permission denied" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert captured.err == ""


def test_organize_check_does_not_move_files(tmp_path):
    """--check never moves files even when violations exist."""
    devlog = tmp_path / "devlog"
    devlog.mkdir()
    for sd in cc.DEVLOG_SUBDIRS:
        (devlog / sd).mkdir()
    plan = devlog / "plan-v1-draft.json"
    plan.write_text("{}", encoding="utf-8")
    cc.cmd_organize(tmp_path, check=True)
    # File should still be in the wrong place
    assert plan.exists()
    assert not (devlog / "plans" / "plan-v1-draft.json").exists()
