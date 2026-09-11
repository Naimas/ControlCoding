"""Tests for .gitignore CC block management and central hooks in cc.py."""

import json
import sys
from pathlib import Path
from unittest import mock

# Add scripts/ to path so we can import cc
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cc import (
    GITIGNORE_BLOCK,
    GITIGNORE_ALWAYS_LOCAL_BLOCK,
    GITIGNORE_LOCAL_DOCS_BLOCK,
    GITIGNORE_MARKER_START,
    GITIGNORE_MARKER_END,
    INIT_HOOKS,
    _build_gitignore_block,
    _central_hooks_dir,
    _ensure_gitignore,
    _get_hooks_location,
    _save_hooks_location,
)


def test_creates_gitignore_when_missing(tmp_path):
    """Creates .gitignore with CC block when file does not exist."""
    assert _ensure_gitignore(tmp_path) is True
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert GITIGNORE_MARKER_START in gi
    assert GITIGNORE_MARKER_END in gi
    assert "hooks/check_boundaries.py" in gi
    assert ".claude/deny_hashes.json" in gi


def test_appends_to_existing_gitignore(tmp_path):
    """Appends CC block to existing .gitignore without overwriting content."""
    existing = "node_modules/\n*.pyc\n"
    (tmp_path / ".gitignore").write_text(existing, encoding="utf-8")

    assert _ensure_gitignore(tmp_path) is True
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # Existing content preserved
    assert gi.startswith("node_modules/\n*.pyc\n")
    # CC block appended
    assert GITIGNORE_MARKER_START in gi
    assert GITIGNORE_MARKER_END in gi


def test_idempotent_no_duplicate(tmp_path):
    """Running twice does not duplicate the CC block."""
    _ensure_gitignore(tmp_path)
    _ensure_gitignore(tmp_path)

    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gi.count(GITIGNORE_MARKER_START) == 1
    assert gi.count(GITIGNORE_MARKER_END) == 1


def test_idempotent_returns_false(tmp_path):
    """Returns False when CC block already present."""
    _ensure_gitignore(tmp_path)
    assert _ensure_gitignore(tmp_path) is False


def test_preserves_existing_content_no_trailing_newline(tmp_path):
    """Handles existing .gitignore without trailing newline."""
    (tmp_path / ".gitignore").write_text("*.log", encoding="utf-8")
    _ensure_gitignore(tmp_path)

    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # Should have newline between old content and CC block
    assert "*.log\n" in gi
    assert GITIGNORE_MARKER_START in gi


def test_block_contains_all_runtime_artifacts(tmp_path):
    """Verify the block covers all known CC runtime artifacts."""
    expected_patterns = [
        ".claude/deny_hashes.json",
        ".claude/cc_hook_log.jsonl",
        ".claude/codewarden_violations.jsonl",
        ".claude/codewarden_report.md",
        ".claude/session_counter.json",
        ".claude/hooks_lifted.json",
        ".claude/lift_request.json",
        ".claude/consult_log.jsonl",
        ".controlcoding/agents/",
        ".controlcoding/sessions/agents/",
        ".controlcoding/external_consultation/",
        ".claude/consult_packets/",
        ".claude/consult_results/",
        ".claude/agents/",
        ".claude/sessions/agents/",
        ".claude/external_consultation/",
        ".controlcoding/write_path_metrics.json",
        ".controlcoding/write_path_events.jsonl",
        ".controlcoding/write_path_manifests/",
        ".controlcoding/write_path_receipts/",
        ".controlcoding/write_path_shadow/",
        ".controlcoding/verification_receipts/",
        ".controlcoding/verification_tmp/",
        ".controlcoding/invariant_receipts/",
        ".controlcoding/invariant_tmp/",
        ".controlcoding/promotions/",
        ".claude/write_path_manifests/",
        ".claude/write_path_receipts/",
        ".claude/write_path_shadow/",
        ".claude/verification_receipts/",
        ".claude/verification_tmp/",
        ".claude/invariant_receipts/",
        ".claude/invariant_tmp/",
        ".claude/promotions/",
        ".controlcoding/settings.json",
        ".controlcoding/module_manifest.json",
        ".controlcoding/memory/",
        ".controlcoding/logs/",
        ".controlcoding/views/",
        ".controlcoding/memory_bootstrap_receipt.json",
        ".claude/settings.local.json",
        ".claude/cc_surface_lock.json",
        ".claude/launchers/",
        ".vscode/tasks.json",
        "tools/fitness_check.py",
        "screenshots/",
        ".bridge/",
    ]
    for pattern in expected_patterns:
        assert pattern in GITIGNORE_BLOCK, f"Missing from gitignore block: {pattern}"


def test_local_docs_block_contains_expected_patterns():
    assert "devlog/" not in GITIGNORE_LOCAL_DOCS_BLOCK
    assert "STATUS.md" in GITIGNORE_LOCAL_DOCS_BLOCK
    assert "ROADMAP.md" in GITIGNORE_LOCAL_DOCS_BLOCK
    assert "BUGS.md" in GITIGNORE_LOCAL_DOCS_BLOCK


def test_always_local_block_contains_devlog():
    assert "devlog/" in GITIGNORE_ALWAYS_LOCAL_BLOCK


def test_block_uses_specific_hook_patterns():
    """Verify we use specific hook filenames, not a blanket hooks/ pattern."""
    assert "hooks/check_boundaries.py" in GITIGNORE_BLOCK
    # Should NOT have a blanket hooks/ that would conflict with user dirs
    lines = GITIGNORE_BLOCK.strip().splitlines()
    bare_hooks = [l for l in lines if l.strip() == "hooks/"]
    assert len(bare_hooks) == 0, "Should not use blanket hooks/ pattern"


# --- Central hooks tests ---


def test_central_hooks_dir_is_under_home():
    """Central hooks dir should be ~/.controlcoding/hooks/."""
    d = _central_hooks_dir()
    assert d.parts[-2:] == (".controlcoding", "hooks")
    assert d.parent.parent == Path.home()


def test_central_hooks_gitignore_skips_hook_patterns(tmp_path):
    """When central_hooks=True, .gitignore should not include hook patterns."""
    _ensure_gitignore(tmp_path, central_hooks=True)
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert GITIGNORE_MARKER_START in gi
    assert "hooks/check_boundaries.py" not in gi
    assert "hooks/codewarden_*.py" not in gi
    # Runtime artifacts should still be present
    assert ".claude/deny_hashes.json" in gi
    assert "screenshots/" in gi


def test_central_hooks_gitignore_local_includes_hooks(tmp_path):
    """When central_hooks=False (default), hooks are included in .gitignore."""
    _ensure_gitignore(tmp_path, central_hooks=False)
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "hooks/check_boundaries.py" in gi


def test_build_gitignore_block_local_only_managed_adds_dev_docs():
    block = _build_gitignore_block(
        central_hooks=False,
        documentation_mode="managed",
        cc_artifact_mode="local_only",
    )
    assert "STATUS.md" in block
    assert "devlog/" in block
    assert "dev/" in block


def test_build_gitignore_block_local_only_project_managed_skips_dev_dir():
    block = _build_gitignore_block(
        central_hooks=False,
        documentation_mode="project_managed",
        cc_artifact_mode="local_only",
    )
    assert "STATUS.md" in block
    assert "devlog/" in block
    assert "dev/" not in block


def test_build_gitignore_block_shared_repo_skips_local_docs():
    block = _build_gitignore_block(
        central_hooks=False,
        documentation_mode="managed",
        cc_artifact_mode="shared_repo",
    )
    assert "STATUS.md" not in block
    assert "devlog/" in block


def test_save_and_get_hooks_location(tmp_path):
    """Save and retrieve hooks_location from cc_config.json."""
    (tmp_path / ".claude").mkdir()
    _save_hooks_location(tmp_path, "central")
    assert _get_hooks_location(tmp_path) == "central"


def test_save_hooks_location_preserves_existing(tmp_path):
    """Saving hooks_location should not overwrite other cc_config fields."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    config = {"protected_zones": [{"path": "src/", "level": "deny"}]}
    (claude_dir / "cc_config.json").write_text(json.dumps(config), encoding="utf-8")

    _save_hooks_location(tmp_path, "central")

    result = json.loads((claude_dir / "cc_config.json").read_text(encoding="utf-8"))
    assert result["hooks_location"] == "central"
    assert result["protected_zones"] == [{"path": "src/", "level": "deny"}]


def test_get_hooks_location_default(tmp_path):
    """Default hooks_location is 'local' when not set."""
    assert _get_hooks_location(tmp_path) == "local"


def test_get_hooks_location_missing_key(tmp_path):
    """Returns 'local' when cc_config exists but has no hooks_location."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "cc_config.json").write_text('{"protected_zones": []}', encoding="utf-8")
    assert _get_hooks_location(tmp_path) == "local"
