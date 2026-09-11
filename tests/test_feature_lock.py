"""Comprehensive tests for feature_lock.py - Module Feature Lock engine."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "templates" / "hooks"))

from feature_lock import (
    canonical_path,
    parse_lock_file,
    find_all_lock_files,
    check_duplicate_modules,
    resolve_active_module,
    find_lock_for_module,
    check_module_perimeter,
    _match_owns,
    _match_shared_write,
    _reset_resolver_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the resolver session cache before each test."""
    _reset_resolver_cache()
    yield
    _reset_resolver_cache()


@pytest.fixture
def project(tmp_path):
    """Create a minimal project structure."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude").mkdir()
    return tmp_path


@pytest.fixture
def lock_file_data():
    """Standard valid lock file data."""
    return {
        "version": 1,
        "module": "my-module",
        "owns": ["modules/my-module/**"],
        "shared_write": ["shared/utils.py"],
        "may_read": ["core/knowledge/"],
    }


def write_lock(path, data):
    """Helper: write a .feature-lock.json file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data), encoding="utf-8")


def write_active_module(project_root, data, *, legacy=False):
    """Helper: write active_module.json in canonical or legacy location."""
    control_dir = ".claude" if legacy else ".controlcoding"
    am_path = Path(project_root) / control_dir / "active_module.json"
    am_path.parent.mkdir(parents=True, exist_ok=True)
    am_path.write_text(json.dumps(data), encoding="utf-8")


# ===========================================================================
# PARSER TESTS
# ===========================================================================

class TestParser:
    def test_valid_full(self, tmp_path, lock_file_data):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, lock_file_data)
        data, err = parse_lock_file(lf)
        assert err is None
        assert data["version"] == 1
        assert data["module"] == "my-module"
        assert data["owns"] == ["modules/my-module/**"]
        assert data["shared_write"] == ["shared/utils.py"]
        assert data["may_read"] == ["core/knowledge/"]

    def test_malformed_json(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        lf.write_text("{invalid json", encoding="utf-8")
        data, err = parse_lock_file(lf)
        assert data is None
        assert "Malformed JSON" in err

    def test_missing_module(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "owns": []})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "module" in err

    def test_missing_owns(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x"})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "owns" in err

    def test_missing_version(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"module": "x", "owns": []})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "version" in err

    def test_wrong_version_number(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 2, "module": "x", "owns": []})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "version" in err.lower() or "Unsupported" in err

    def test_version_as_string(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": "1", "module": "x", "owns": []})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "version" in err.lower() or "integer" in err.lower()

    def test_shared_write_with_glob_star(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": [], "shared_write": ["shared/*.py"]})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "glob" in err.lower() or "Glob" in err

    def test_shared_write_with_glob_doublestar(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": [], "shared_write": ["shared/**"]})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "glob" in err.lower() or "Glob" in err

    def test_shared_write_with_glob_question(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": [], "shared_write": ["file?.py"]})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "glob" in err.lower() or "Glob" in err

    def test_empty_module_name(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "", "owns": []})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "non-empty" in err

    def test_whitespace_module_name(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "   ", "owns": []})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "non-empty" in err

    def test_owns_empty_list(self, tmp_path):
        """Empty owns is valid - module with only shared_write."""
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": []})
        data, err = parse_lock_file(lf)
        assert err is None
        assert data["owns"] == []

    def test_extra_fields_ignored(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": [], "extra": "value", "another": 42})
        data, err = parse_lock_file(lf)
        assert err is None
        assert data["extra"] == "value"

    def test_defaults_for_optional_fields(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": ["src/**"]})
        data, err = parse_lock_file(lf)
        assert err is None
        assert data["shared_write"] == []
        assert data["may_read"] == []

    def test_file_not_found(self, tmp_path):
        lf = tmp_path / "nonexistent.json"
        data, err = parse_lock_file(lf)
        assert data is None
        assert "Cannot read" in err

    def test_not_a_dict(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        lf.write_text('["list", "not", "dict"]', encoding="utf-8")
        data, err = parse_lock_file(lf)
        assert data is None
        assert "object" in err.lower()

    def test_owns_non_string_entry(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": [123]})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "string" in err

    def test_shared_write_non_string_entry(self, tmp_path):
        lf = tmp_path / ".feature-lock.json"
        write_lock(lf, {"version": 1, "module": "x", "owns": [], "shared_write": [42]})
        data, err = parse_lock_file(lf)
        assert data is None
        assert "string" in err


# ===========================================================================
# RESOLVER TESTS
# ===========================================================================

class TestResolver:
    def test_no_env_no_file(self, project):
        """CASE 1: No module active -> disabled."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = resolve_active_module(project)
        assert result["module"] is None
        assert result["source"] == "disabled"
        assert result["error"] is None

    def test_env_only(self, project):
        """Only env var set."""
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = resolve_active_module(project)
        assert result["module"] == "mod-a"
        assert result["source"] == "env"
        assert result["mode"] == "enforce"  # default when no file

    def test_file_only(self, project):
        """Only active_module.json set."""
        write_active_module(project, {
            "module": "mod-b",
            "mode": "warn",
            "session_id": "test",
        })
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = resolve_active_module(project)
        assert result["module"] == "mod-b"
        assert result["source"] == "file"
        assert result["mode"] == "warn"

    def test_both_agree(self, project):
        """Env var and file agree on module name."""
        write_active_module(project, {
            "module": "mod-a",
            "mode": "enforce",
        })
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = resolve_active_module(project)
        assert result["module"] == "mod-a"
        assert result["error"] is None

    def test_conflict_enforce_blocks(self, project):
        """CASE 2: Conflict in enforce mode -> block."""
        write_active_module(project, {
            "module": "mod-b",
            "mode": "enforce",
        })
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = resolve_active_module(project)
        assert result["module"] is None
        assert result["source"] == "conflict"
        assert result["error"] is not None
        assert "Conflicting" in result["error"]

    def test_conflict_warn_uses_env(self, project):
        """CASE 2: Conflict in warn mode -> use env var + warning."""
        write_active_module(project, {
            "module": "mod-b",
            "mode": "warn",
        })
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = resolve_active_module(project)
        assert result["module"] == "mod-a"
        assert result["source"] == "conflict"

    def test_conflict_audit_uses_env(self, project):
        """CASE 2: Conflict in audit mode -> use env var + log."""
        write_active_module(project, {
            "module": "mod-b",
            "mode": "audit",
        })
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = resolve_active_module(project)
        assert result["module"] == "mod-a"
        assert result["source"] == "conflict"

    def test_malformed_active_module_json(self, project):
        """Malformed active_module.json -> error."""
        am_path = project / ".controlcoding" / "active_module.json"
        am_path.parent.mkdir(parents=True, exist_ok=True)
        am_path.write_text("{bad json", encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = resolve_active_module(project)
        assert result["module"] is None
        assert result["error"] is not None
        assert "Malformed" in result["error"]

    def test_legacy_active_module_json_still_works(self, project):
        write_active_module(project, {
            "module": "mod-a",
            "mode": "enforce",
        }, legacy=True)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = resolve_active_module(project)
        assert result["module"] == "mod-a"
        assert result["source"] == "file"

    def test_invalid_mode(self, project):
        """active_module.json with invalid mode -> error."""
        write_active_module(project, {
            "module": "mod-a",
            "mode": "strict",  # invalid
        })
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = resolve_active_module(project)
        assert result["error"] is not None
        assert "mode" in result["error"]

    def test_duplicate_module_name(self, project):
        """CASE 3: Duplicate module name -> error."""
        # Create two lock files with same module name
        dir_a = project / "modules" / "a"
        dir_b = project / "modules" / "b"
        write_lock(dir_a / ".feature-lock.json", {
            "version": 1, "module": "same-name", "owns": ["modules/a/**"]
        })
        write_lock(dir_b / ".feature-lock.json", {
            "version": 1, "module": "same-name", "owns": ["modules/b/**"]
        })
        write_active_module(project, {"module": "same-name", "mode": "enforce"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = resolve_active_module(project)
        assert result["error"] is not None
        assert "Duplicate" in result["error"]

    def test_empty_env_var_treated_as_absent(self, project):
        """Empty CC_ACTIVE_MODULE is treated as not set."""
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": ""}):
            result = resolve_active_module(project)
        assert result["module"] is None
        assert result["source"] == "disabled"

    def test_whitespace_env_var_treated_as_absent(self, project):
        """Whitespace-only CC_ACTIVE_MODULE is treated as not set."""
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "   "}):
            result = resolve_active_module(project)
        assert result["module"] is None
        assert result["source"] == "disabled"

    def test_env_var_special_chars_rejected(self, project):
        """CC_ACTIVE_MODULE with special characters -> error."""
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod;drop"}):
            result = resolve_active_module(project)
        assert result["error"] is not None
        assert "invalid characters" in result["error"]

    def test_env_var_slash_rejected(self, project):
        """CC_ACTIVE_MODULE with slashes -> error."""
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod/sub"}):
            result = resolve_active_module(project)
        assert result["error"] is not None
        assert "invalid characters" in result["error"]

    def test_env_var_dots_and_underscores_allowed(self, project):
        """CC_ACTIVE_MODULE with dots and underscores -> valid."""
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "my_module.v2"}):
            result = resolve_active_module(project)
        assert result["error"] is None
        assert result["module"] == "my_module.v2"


# ===========================================================================
# PATH MATCHING TESTS
# ===========================================================================

class TestPathMatching:
    def test_owns_glob_match(self):
        assert _match_owns("modules/icc/scorer.py", ["modules/icc/**"]) is True

    def test_owns_glob_nested(self):
        assert _match_owns("modules/icc/sub/file.py", ["modules/icc/**"]) is True

    def test_owns_no_match_similar_prefix(self):
        """modules/icc/** must NOT match modules/icc-v2/file.py."""
        assert _match_owns("modules/icc-v2/file.py", ["modules/icc/**"]) is False

    def test_owns_single_star(self):
        assert _match_owns("modules/icc/file.py", ["modules/icc/*"]) is True

    def test_owns_single_star_matches_nested_via_fnmatch(self):
        """fnmatch's * matches path separators too - this is expected."""
        assert _match_owns("modules/icc/sub/file.py", ["modules/icc/*"]) is True

    def test_shared_write_exact_match(self):
        assert _match_shared_write("shared/utils.py", ["shared/utils.py"]) is True

    def test_shared_write_no_prefix_match(self):
        assert _match_shared_write("shared/utils.py.bak", ["shared/utils.py"]) is False

    def test_shared_write_no_partial_match(self):
        assert _match_shared_write("shared/util", ["shared/utils.py"]) is False

    def test_shared_write_backslash_normalization(self):
        assert _match_shared_write("shared/utils.py", ["shared\\utils.py"]) is True


# ===========================================================================
# CANONICAL PATH TESTS
# ===========================================================================

class TestCanonicalPath:
    def test_relative_path(self, project):
        result = canonical_path("src/main.py", project)
        assert result == "src/main.py"

    def test_backslash_normalization(self, project):
        result = canonical_path("src\\main.py", project)
        assert result == "src/main.py"

    def test_dotdot_resolution(self, project):
        result = canonical_path("src/../src/main.py", project)
        assert result == "src/main.py"

    def test_absolute_path(self, project):
        abs_path = str(project / "src" / "main.py")
        result = canonical_path(abs_path, project)
        assert result == "src/main.py"

    def test_outside_repo_returns_none(self, project):
        # Go above the repo root
        result = canonical_path("../../outside.py", project)
        assert result is None


# ===========================================================================
# ENFORCEMENT TESTS
# ===========================================================================

class TestEnforcement:
    def _setup_module(self, project, module_name="my-module",
                      mode="enforce", owns=None, shared_write=None):
        """Helper: set up a module with lock file and active module."""
        if owns is None:
            owns = [f"modules/{module_name}/**"]
        if shared_write is None:
            shared_write = []
        # Create lock file
        mod_dir = project / "modules" / module_name
        write_lock(mod_dir / ".feature-lock.json", {
            "version": 1,
            "module": module_name,
            "owns": owns,
            "shared_write": shared_write,
        })
        # Create active module
        write_active_module(project, {
            "module": module_name,
            "mode": mode,
        })

    def test_path_in_owns_allow(self, project):
        """Path inside owns -> allow."""
        self._setup_module(project)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "modules/my-module/file.py", project
            )
        assert result["decision"] == "allow"
        assert result["reason_code"] == "owns_match"
        assert result["active_module"] == "my-module"

    def test_shared_write_allow(self, project):
        """Path in shared_write -> allow."""
        self._setup_module(project, shared_write=["shared/utils.py"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "shared/utils.py", project
            )
        assert result["decision"] == "allow"
        assert result["reason_code"] == "shared_write_match"

    def test_foreign_path_enforce_deny(self, project):
        """Path outside perimeter + enforce -> deny."""
        self._setup_module(project, mode="enforce")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "modules/other/file.py", project
            )
        assert result["decision"] == "deny"
        assert result["reason_code"] == "foreign_path"

    def test_foreign_path_warn_allow(self, project):
        """Path outside perimeter + warn -> allow + warning."""
        self._setup_module(project, mode="warn")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "modules/other/file.py", project
            )
        assert result["decision"] == "warn"
        assert result["reason_code"] == "foreign_path"

    def test_foreign_path_audit_allow(self, project):
        """Path outside perimeter + audit -> allow + log."""
        self._setup_module(project, mode="audit")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "modules/other/file.py", project
            )
        assert result["decision"] == "allow"
        assert result["reason_code"] == "foreign_path"

    def test_no_module_active_allow(self, project):
        """No module active -> allow with source=disabled."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "any/file.py", project
            )
        assert result["decision"] == "allow"
        assert result["reason_code"] == "disabled"
        assert result["active_module"] is None
        assert result["source"] == "disabled"

    def test_self_protection_overrides_owns(self, project):
        """CASE 4: Self-protected path inside owns -> deny."""
        self._setup_module(project, owns=["modules/my-module/**"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "modules/my-module/check_boundaries.py", project,
                self_protected_paths=["check_boundaries.py"],
            )
        assert result["decision"] == "deny"
        assert result["reason_code"] == "self_protection_override"

    def test_global_deny_overrides_owns(self, project):
        """CASE 4: Global deny path inside owns -> deny."""
        self._setup_module(project, owns=["core/**"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                "core/models.py", project,
                global_deny_paths=["core/"],
            )
        assert result["decision"] == "deny"
        assert result["reason_code"] == "global_deny_override"

    def test_output_contract_all_fields(self, project):
        """Every result has all 5 required fields."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter("any.py", project)
        assert "decision" in result
        assert "reason_code" in result
        assert "message" in result
        assert "active_module" in result
        assert "source" in result

    def test_output_decision_values(self, project):
        """decision is always one of allow/warn/deny."""
        self._setup_module(project, mode="enforce")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            r1 = check_module_perimeter("modules/my-module/f.py", project)
            r2 = check_module_perimeter("outside.py", project)
        assert r1["decision"] in ("allow", "warn", "deny")
        assert r2["decision"] in ("allow", "warn", "deny")

    def test_conflict_enforce_denies(self, project):
        """Conflict in enforce mode -> deny."""
        write_active_module(project, {"module": "mod-b", "mode": "enforce"})
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = check_module_perimeter("any.py", project)
        assert result["decision"] == "deny"
        assert result["reason_code"] == "conflict"

    def test_conflict_warn_warns(self, project):
        """Conflict in warn mode -> uses env var, proceeds to enforcement."""
        # Create lock for mod-a so enforcement can proceed
        mod_dir = project / "modules" / "mod-a"
        write_lock(mod_dir / ".feature-lock.json", {
            "version": 1, "module": "mod-a",
            "owns": ["modules/mod-a/**"], "shared_write": [],
        })
        write_active_module(project, {"module": "mod-b", "mode": "warn"})
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = check_module_perimeter("outside.py", project)
        assert result["decision"] == "warn"
        assert result["reason_code"] == "foreign_path"
        assert result["source"] == "conflict"

    def test_conflict_audit_allows(self, project):
        """Conflict in audit mode -> uses env var, proceeds to enforcement."""
        # Create lock for mod-a so enforcement can proceed
        mod_dir = project / "modules" / "mod-a"
        write_lock(mod_dir / ".feature-lock.json", {
            "version": 1, "module": "mod-a",
            "owns": ["modules/mod-a/**"], "shared_write": [],
        })
        write_active_module(project, {"module": "mod-b", "mode": "audit"})
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "mod-a"}):
            result = check_module_perimeter("outside.py", project)
        assert result["decision"] == "allow"
        assert result["reason_code"] == "foreign_path"
        assert result["source"] == "conflict"


# ===========================================================================
# PATH MATCHING EDGE CASES (segment-aware)
# ===========================================================================

class TestSegmentAwareMatching:
    def test_core_not_match_coredump(self):
        """'core' must NOT match 'coredump'."""
        assert _match_owns("coredump/file.py", ["core/**"]) is False

    def test_core_matches_core_sub(self):
        """'core/**' matches 'core/sub/file.py'."""
        assert _match_owns("core/sub/file.py", ["core/**"]) is True

    def test_core_matches_core_file(self):
        """'core/**' matches 'core/file.py'."""
        assert _match_owns("core/file.py", ["core/**"]) is True

    def test_icc_not_match_icc_v2(self):
        """'modules/icc/**' must NOT match 'modules/icc-v2/file.py'."""
        assert _match_owns("modules/icc-v2/file.py", ["modules/icc/**"]) is False

    def test_backslash_path_normalized(self):
        """Backslash paths are handled correctly."""
        result = _match_shared_write("shared/file.py", ["shared\\file.py"])
        assert result is True


# ===========================================================================
# LOCK FILE DISCOVERY
# ===========================================================================

class TestDiscovery:
    def test_find_all_lock_files(self, project):
        dir_a = project / "modules" / "a"
        dir_b = project / "modules" / "b"
        write_lock(dir_a / ".feature-lock.json", {
            "version": 1, "module": "a", "owns": []
        })
        write_lock(dir_b / ".feature-lock.json", {
            "version": 1, "module": "b", "owns": []
        })
        result = find_all_lock_files(project)
        assert len(result) == 2

    def test_skips_git_directory(self, project):
        git_dir = project / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        write_lock(git_dir / ".feature-lock.json", {
            "version": 1, "module": "git-internal", "owns": []
        })
        result = find_all_lock_files(project)
        assert len(result) == 0

    def test_no_duplicates_found(self, project):
        dir_a = project / "modules" / "a"
        dir_b = project / "modules" / "b"
        write_lock(dir_a / ".feature-lock.json", {
            "version": 1, "module": "a", "owns": []
        })
        write_lock(dir_b / ".feature-lock.json", {
            "version": 1, "module": "b", "owns": []
        })
        dup_name, dup_paths = check_duplicate_modules(project)
        assert dup_name is None
        assert dup_paths is None

    def test_duplicates_detected(self, project):
        dir_a = project / "modules" / "a"
        dir_b = project / "modules" / "b"
        write_lock(dir_a / ".feature-lock.json", {
            "version": 1, "module": "same", "owns": []
        })
        write_lock(dir_b / ".feature-lock.json", {
            "version": 1, "module": "same", "owns": []
        })
        dup_name, dup_paths = check_duplicate_modules(project)
        assert dup_name == "same"
        assert len(dup_paths) == 2

    def test_find_lock_for_module(self, project):
        dir_a = project / "modules" / "a"
        write_lock(dir_a / ".feature-lock.json", {
            "version": 1, "module": "alpha", "owns": ["modules/a/**"]
        })
        data, path = find_lock_for_module(project, "alpha")
        assert data is not None
        assert data["module"] == "alpha"

    def test_find_lock_for_nonexistent_module(self, project):
        data, path = find_lock_for_module(project, "nonexistent")
        assert data is None
        assert path is None


# ===========================================================================
# INTEGRATION: FULL FLOW
# ===========================================================================

class TestFullFlow:
    def test_module_allows_own_files(self, project):
        """Full flow: module writes to its own directory -> allow."""
        mod_dir = project / "modules" / "alpha"
        write_lock(mod_dir / ".feature-lock.json", {
            "version": 1, "module": "alpha",
            "owns": ["modules/alpha/**"],
            "shared_write": [],
        })
        write_active_module(project, {"module": "alpha", "mode": "enforce"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter("modules/alpha/src/main.py", project)
        assert result["decision"] == "allow"
        assert result["reason_code"] == "owns_match"
        assert result["active_module"] == "alpha"

    def test_module_blocked_from_other(self, project):
        """Full flow: module writes to other module's directory -> deny."""
        mod_a = project / "modules" / "alpha"
        mod_b = project / "modules" / "beta"
        write_lock(mod_a / ".feature-lock.json", {
            "version": 1, "module": "alpha",
            "owns": ["modules/alpha/**"],
            "shared_write": [],
        })
        write_lock(mod_b / ".feature-lock.json", {
            "version": 1, "module": "beta",
            "owns": ["modules/beta/**"],
            "shared_write": [],
        })
        write_active_module(project, {"module": "alpha", "mode": "enforce"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter("modules/beta/src/main.py", project)
        assert result["decision"] == "deny"
        assert result["reason_code"] == "foreign_path"

    def test_shared_write_across_modules(self, project):
        """Full flow: module writes to shared file it declares -> allow."""
        mod_dir = project / "modules" / "alpha"
        write_lock(mod_dir / ".feature-lock.json", {
            "version": 1, "module": "alpha",
            "owns": ["modules/alpha/**"],
            "shared_write": ["shared/scoring.py"],
        })
        write_active_module(project, {"module": "alpha", "mode": "enforce"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter("shared/scoring.py", project)
        assert result["decision"] == "allow"
        assert result["reason_code"] == "shared_write_match"

    def test_env_var_activates_module(self, project):
        """Full flow: env var sets active module."""
        mod_dir = project / "modules" / "alpha"
        write_lock(mod_dir / ".feature-lock.json", {
            "version": 1, "module": "alpha",
            "owns": ["modules/alpha/**"],
            "shared_write": [],
        })
        with mock.patch.dict(os.environ, {"CC_ACTIVE_MODULE": "alpha"}):
            result = check_module_perimeter("modules/alpha/f.py", project)
        assert result["decision"] == "allow"
        assert result["source"] == "env"

    def test_missing_lock_file_enforce_denies(self, project):
        """Module active but no lock file found -> deny in enforce."""
        write_active_module(project, {"module": "ghost", "mode": "enforce"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter("any/file.py", project)
        assert result["decision"] == "deny"
        assert "No .feature-lock.json" in result["message"]

    def test_missing_lock_file_warn_warns(self, project):
        """Module active but no lock file found -> warn in warn mode."""
        write_active_module(project, {"module": "ghost", "mode": "warn"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter("any/file.py", project)
        assert result["decision"] == "warn"

    def test_missing_lock_file_audit_allows(self, project):
        """Module active but no lock file found -> allow in audit mode."""
        write_active_module(project, {"module": "ghost", "mode": "audit"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter("any/file.py", project)
        assert result["decision"] == "allow"

    def test_self_protection_multi_segment(self, project):
        """Self-protection with multi-segment path like .claude/settings.json."""
        mod_dir = project / "modules" / "alpha"
        write_lock(mod_dir / ".feature-lock.json", {
            "version": 1, "module": "alpha",
            "owns": ["**"],  # owns everything
            "shared_write": [],
        })
        write_active_module(project, {"module": "alpha", "mode": "enforce"})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CC_ACTIVE_MODULE", None)
            result = check_module_perimeter(
                ".claude/settings.json", project,
                self_protected_paths=[".claude/settings.json"],
            )
        assert result["decision"] == "deny"
        assert result["reason_code"] == "self_protection_override"
