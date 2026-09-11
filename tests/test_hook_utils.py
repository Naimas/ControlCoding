#!/usr/bin/env python3
"""Tests for hook_utils.py - shared hook utilities."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "hooks"))
import hook_utils


class TestNormalizeProtectedZones:
    """Tests for normalize_protected_zones()."""

    # --- list-of-dict format (standard) ---

    def test_list_of_dict_passthrough(self):
        """Standard list-of-dict format is returned normalized."""
        raw = [{"path": "src/core/", "level": "deny", "description": "core"}]
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 1
        assert result[0] == {"path": "src/core/", "level": "deny", "description": "core"}

    def test_list_of_dict_defaults(self):
        """Missing level defaults to warn, missing description to empty."""
        raw = [{"path": "src/core/"}]
        result = hook_utils.normalize_protected_zones(raw)
        assert result[0]["level"] == "warn"
        assert result[0]["description"] == ""

    def test_list_of_dict_multiple(self):
        """Multiple entries in list format."""
        raw = [
            {"path": "src/core/", "level": "deny"},
            {"path": "docs/", "level": "warn"},
        ]
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 2
        assert result[0]["path"] == "src/core/"
        assert result[1]["path"] == "docs/"

    # --- dict-with-levels format ---

    def test_dict_deny_strings(self):
        """Dict format with deny list of strings."""
        raw = {"deny": ["templates/hooks/", "src/core/"], "warn": []}
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 2
        assert result[0] == {"path": "templates/hooks/", "level": "deny", "description": ""}
        assert result[1] == {"path": "src/core/", "level": "deny", "description": ""}

    def test_dict_warn_strings(self):
        """Dict format with warn list of strings."""
        raw = {"deny": [], "warn": ["docs/"]}
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 1
        assert result[0]["level"] == "warn"
        assert result[0]["path"] == "docs/"

    def test_dict_mixed_deny_warn(self):
        """Dict format with both deny and warn entries."""
        raw = {"deny": ["src/core/"], "warn": ["docs/"]}
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 2
        deny = [z for z in result if z["level"] == "deny"]
        warn = [z for z in result if z["level"] == "warn"]
        assert len(deny) == 1
        assert len(warn) == 1

    def test_dict_missing_deny_key(self):
        """Dict format with only warn key (deny missing)."""
        raw = {"warn": ["docs/"]}
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 1
        assert result[0]["level"] == "warn"

    def test_dict_missing_warn_key(self):
        """Dict format with only deny key (warn missing)."""
        raw = {"deny": ["src/core/"]}
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 1
        assert result[0]["level"] == "deny"

    def test_dict_empty(self):
        """Dict format with empty lists."""
        raw = {"deny": [], "warn": []}
        result = hook_utils.normalize_protected_zones(raw)
        assert result == []

    def test_dict_with_dict_entries(self):
        """Dict format where entries are dicts instead of strings."""
        raw = {"deny": [{"path": "src/core/", "description": "core modules"}]}
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 1
        assert result[0]["path"] == "src/core/"
        assert result[0]["level"] == "deny"
        assert result[0]["description"] == "core modules"

    # --- mixed list format ---

    def test_mixed_list_strings_and_dicts(self):
        """List with both strings and dicts."""
        raw = ["src/core/", {"path": "docs/", "level": "deny"}]
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 2
        assert result[0] == {"path": "src/core/", "level": "warn", "description": ""}
        assert result[1] == {"path": "docs/", "level": "deny", "description": ""}

    # --- invalid/edge cases ---

    def test_none_input(self):
        """None input returns empty list."""
        result = hook_utils.normalize_protected_zones(None)
        assert result == []

    def test_empty_list(self):
        """Empty list returns empty list."""
        result = hook_utils.normalize_protected_zones([])
        assert result == []

    def test_invalid_list_entries_skipped(self):
        """Invalid entries (no path key, numbers, etc.) are skipped."""
        raw = [{"level": "deny"}, 42, None, {"path": "valid/"}]
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 1
        assert result[0]["path"] == "valid/"

    def test_does_not_mutate_input(self):
        """Input data is never mutated."""
        raw = [{"path": "src/core/"}]
        original = [{"path": "src/core/"}]
        hook_utils.normalize_protected_zones(raw)
        assert raw == original

    def test_dict_does_not_mutate_input(self):
        """Dict input is never mutated."""
        entry = {"path": "src/", "description": "test"}
        raw = {"deny": [entry]}
        hook_utils.normalize_protected_zones(raw)
        assert "level" not in entry  # no setdefault mutation

    # --- realistic CC project format ---

    def test_cc_project_format(self):
        """The actual format used by the CC project itself."""
        raw = {"deny": [], "warn": []}
        result = hook_utils.normalize_protected_zones(raw)
        assert result == []

    def test_cc_init_format(self):
        """The format created by cc init."""
        raw = [{"path": "src/core/", "description": "Core modules - stable zone (example)", "level": "deny"}]
        result = hook_utils.normalize_protected_zones(raw)
        assert len(result) == 1
        assert result[0]["path"] == "src/core/"
        assert result[0]["level"] == "deny"


class TestControlPlaneHelpers:
    def test_control_plane_path_prefers_canonical_existing_file(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        legacy_dir = tmp_path / ".claude"
        control_dir.mkdir()
        legacy_dir.mkdir()
        (control_dir / "cc_config.json").write_text("{}", encoding="utf-8")
        (legacy_dir / "cc_config.json").write_text('{"legacy": true}', encoding="utf-8")

        result = hook_utils.control_plane_path(tmp_path, "cc_config.json")

        assert result == control_dir / "cc_config.json"

    def test_control_plane_path_falls_back_to_legacy_existing_file(self, tmp_path):
        legacy_dir = tmp_path / ".claude"
        legacy_dir.mkdir()
        (legacy_dir / "lift_request.json").write_text("{}", encoding="utf-8")

        result = hook_utils.control_plane_path(tmp_path, "lift_request.json")

        assert result == legacy_dir / "lift_request.json"

    def test_control_plane_write_path_uses_canonical_for_new_repo(self, tmp_path):
        result = hook_utils.control_plane_write_path(tmp_path, "settings.json")
        assert result == tmp_path / ".controlcoding" / "settings.json"

    def test_find_project_root_accepts_canonical_control_plane(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "settings.json").write_text("{}", encoding="utf-8")

        nested = tmp_path / "hooks" / "check_boundaries.py"
        nested.parent.mkdir()
        nested.write_text("# stub", encoding="utf-8")

        result = hook_utils.find_project_root(nested)

        assert result == tmp_path
