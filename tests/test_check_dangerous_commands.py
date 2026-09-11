"""Unit tests for check_dangerous_commands.py - dangerous command blocker."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SOURCE_DIR = Path(__file__).parent.parent / "templates" / "hooks"
HOOK_PATH = HOOK_SOURCE_DIR / "check_dangerous_commands.py"
HOOK_UTILS_PATH = HOOK_SOURCE_DIR / "hook_utils.py"
HOOK_FEATURE_LOCK_PATH = HOOK_SOURCE_DIR / "feature_lock.py"

# Staged files - used by tests until user copies to HOOK_PATH.
# After copy, all paths resolve to HOOK_PATH and tests work the same.
_STAGED_V3 = Path(__file__).parent.parent / "dev" / "staging" / "check_dangerous_commands_v3.py"
_STAGED_V2 = Path(__file__).parent.parent / "dev" / "staging" / "check_dangerous_commands_v2.py"
if _STAGED_V3.exists():
    HOOK_V2_PATH = _STAGED_V3
elif _STAGED_V2.exists():
    HOOK_V2_PATH = _STAGED_V2
else:
    HOOK_V2_PATH = HOOK_PATH


@pytest.fixture(autouse=True)
def _isolated_hook_sources(tmp_path, monkeypatch):
    """Run direct hook checks against copies inside a temporary Git project."""
    project_root = tmp_path / "_isolated_dangerous_hook_project"
    hooks_dir = project_root / "hooks"
    hooks_dir.mkdir(parents=True)
    (project_root / ".git").mkdir()

    for filename in (
        "check_dangerous_commands.py",
        "feature_lock.py",
        "hook_logger.py",
        "hook_utils.py",
    ):
        shutil.copy2(HOOK_SOURCE_DIR / filename, hooks_dir / filename)

    isolated_v2_path = hooks_dir / "check_dangerous_commands_v2_source.py"
    shutil.copy2(HOOK_V2_PATH, isolated_v2_path)

    module = sys.modules[__name__]
    monkeypatch.setattr(
        module,
        "HOOK_PATH",
        hooks_dir / "check_dangerous_commands.py",
    )
    monkeypatch.setattr(module, "HOOK_V2_PATH", isolated_v2_path)
    monkeypatch.setattr(module, "HOOK_UTILS_PATH", hooks_dir / "hook_utils.py")
    monkeypatch.setattr(module, "HOOK_FEATURE_LOCK_PATH", hooks_dir / "feature_lock.py")

    yield

    for hook_log in tmp_path.rglob("cc_hook_log.jsonl"):
        assert hook_log.resolve().is_relative_to(tmp_path.resolve())


def run_hook(command):
    """Run check_dangerous_commands.py with simulated stdin."""
    inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=inp, capture_output=True, text=True,
    )
    return result.stdout.strip(), result.returncode


def _setup_isolated(tmp_path, hook_source=None):
    """Create minimal project with hook copy for isolated testing."""
    source = hook_source or HOOK_PATH
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude").mkdir()
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hook_content = source.read_text(encoding="utf-8")
    (hooks_dir / "check_dangerous_commands.py").write_text(hook_content, encoding="utf-8")
    (hooks_dir / "hook_utils.py").write_text(
        HOOK_UTILS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (hooks_dir / "feature_lock.py").write_text(
        HOOK_FEATURE_LOCK_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (hooks_dir / "hook_logger.py").write_text(
        "def log_event(*a, **kw): pass", encoding="utf-8"
    )
    return hooks_dir


def _run_isolated(hooks_dir, command):
    """Run hook from isolated temp directory."""
    inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        [sys.executable, str(hooks_dir / "check_dangerous_commands.py")],
        input=inp, capture_output=True, text=True,
    )
    return result.stdout.strip(), result.returncode


class TestDangerousCommands:
    def test_blocks_rm_rf_root(self):
        stdout, code = run_hook("rm -rf /etc")
        assert code == 2
        assert "catastrophic" in stdout.lower() or "block" in stdout.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf ./",
            "rm -fr .",
            "rm --recursive --force .",
            "echo ok && rm -rf ./",
            "rm --force --recursive .; echo should_not_run",
        ],
    )
    def test_blocks_rm_recursive_force_current_directory_variants(self, command):
        stdout, code = run_hook(command)
        assert code == 2
        assert "dangerous command" in stdout.lower() or "block" in stdout.lower()

    def test_blocks_rm_rf_dot(self):
        stdout, code = run_hook("rm -rf . ")
        assert code == 2

    def test_blocks_rm_rf_dot_at_end_of_command(self):
        stdout, code = run_hook("rm -rf .")
        assert code == 2

    def test_blocks_force_push(self):
        stdout, code = run_hook("git push --force origin main")
        assert code == 2
        assert "remote history" in stdout.lower() or "block" in stdout.lower()

    def test_allows_force_with_lease(self):
        stdout, code = run_hook("git push --force-with-lease origin main")
        assert code == 0

    def test_blocks_reset_hard(self):
        stdout, code = run_hook("git reset --hard HEAD~3")
        assert code == 2

    def test_blocks_git_clean(self):
        stdout, code = run_hook("git clean -f")
        assert code == 2

    def test_blocks_git_clean_combined_force_flags(self):
        stdout, code = run_hook("git clean -xdf")
        assert code == 2

    @pytest.mark.parametrize(
        "command",
        [
            "git clean -ffdx",
            "git clean --force -d -x",
            "printf ok; git clean --force -d -x",
        ],
    )
    def test_blocks_git_clean_force_delete_variants(self, command):
        stdout, code = run_hook(command)
        assert code == 2
        assert "git clean" in stdout.lower() or "dangerous command" in stdout.lower()

    def test_blocks_git_checkout_dot(self):
        stdout, code = run_hook("git checkout .")
        assert code == 2

    def test_blocks_branch_force_delete(self):
        stdout, code = run_hook("git branch -D feature-branch")
        assert code == 2

    def test_blocks_drop_table(self):
        stdout, code = run_hook("psql -c 'DROP TABLE users'")
        assert code == 2

    def test_blocks_drop_database(self):
        stdout, code = run_hook("DROP DATABASE production")
        assert code == 2

    def test_blocks_truncate(self):
        stdout, code = run_hook("TRUNCATE TABLE logs")
        assert code == 2


class TestSafeCommands:
    def test_allows_ls(self):
        _, code = run_hook("ls -la")
        assert code == 0

    def test_allows_git_status(self):
        _, code = run_hook("git status")
        assert code == 0

    def test_allows_git_diff(self):
        _, code = run_hook("git diff HEAD")
        assert code == 0

    def test_allows_git_clean_dry_run_without_force(self):
        _, code = run_hook("git clean -n -d -x")
        assert code == 0

    def test_allows_git_push(self):
        _, code = run_hook("git push origin main")
        assert code == 0

    def test_allows_git_log(self):
        _, code = run_hook("git log --oneline -5")
        assert code == 0

    def test_allows_python_run(self):
        _, code = run_hook("python -m pytest tests/")
        assert code == 0

    def test_allows_npm_install(self):
        _, code = run_hook("npm install express")
        assert code == 0


class TestEdgeCases:
    def test_empty_stdin(self):
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_no_command(self):
        inp = json.dumps({"tool_name": "Bash", "tool_input": {}})
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_case_insensitive_sql(self):
        """SQL commands should be caught regardless of case."""
        stdout, code = run_hook("drop table Users")
        assert code == 2


class TestFailOpen:
    def test_broken_stdin_exits_zero(self):
        """Malformed JSON on stdin should exit 0, not crash."""
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="{broken json", capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_hook_never_exits_one(self):
        """Hook should only exit 0 or 2, never 1."""
        for invalid_input in ["", "{}", "null"]:
            result = subprocess.run(
                [sys.executable, str(HOOK_PATH)],
                input=invalid_input, capture_output=True, text=True,
            )
            assert result.returncode in (0, 2), (
                f"Got exit code {result.returncode} for input: {invalid_input}"
            )

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf ./",
            "git clean --force -d -x",
            "git status --short",
        ],
    )
    def test_hook_never_exits_one_for_security_matrix(self, command):
        inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=inp, capture_output=True, text=True,
        )
        assert result.returncode in (0, 2)


class TestConfigLoading:
    """Test that dangerous_patterns load from cc_config.json with inline fallback.

    Uses V2 hook (staged or already copied).
    """

    def test_uses_config_dangerous_patterns(self, tmp_path):
        """When cc_config.json has dangerous_patterns, those are used."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {
            "dangerous_patterns": [
                {"pattern": "custom_forbidden", "description": "custom block"}
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        stdout, code = _run_isolated(hooks_dir, "custom_forbidden command")
        assert code == 2
        assert "custom block" in stdout

    def test_uses_canonical_config_dangerous_patterns(self, tmp_path):
        """Canonical cc_config.json should also drive the dangerous pattern set."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        config = {
            "dangerous_patterns": [
                {"pattern": "canonical_forbidden", "description": "canonical block"}
            ]
        }
        (control_dir / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        stdout, code = _run_isolated(hooks_dir, "canonical_forbidden command")
        assert code == 2
        assert "canonical block" in stdout

    def test_config_replaces_inline_patterns(self, tmp_path):
        """Config patterns REPLACE inline ones, not extend."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {
            "dangerous_patterns": [
                {"pattern": "only_this", "description": "only pattern"}
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # rm -rf is in inline fallback but NOT in config - should pass
        _, code = _run_isolated(hooks_dir, "rm -rf /etc")
        assert code == 0

    def test_fallback_when_no_config(self, tmp_path):
        """Without cc_config.json, inline DANGEROUS_PATTERNS are used."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        # No cc_config.json
        _, code = _run_isolated(hooks_dir, "rm -rf /etc")
        assert code == 2

    def test_fallback_when_config_has_no_section(self, tmp_path):
        """Config exists but lacks dangerous_patterns -> inline fallback."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {"protected_zones": {"deny": ["core/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "rm -rf /etc")
        assert code == 2

    def test_malformed_config_falls_back(self, tmp_path):
        """Malformed cc_config.json -> inline fallback."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        (tmp_path / ".claude" / "cc_config.json").write_text(
            "{broken json", encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "rm -rf /etc")
        assert code == 2


class TestWritePatternExpansion:
    """Test expanded write patterns: echo, printf, cp, mv, sed -i.

    Uses V2 hook (staged or already copied).
    """

    def _setup_deny(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {"protected_zones": {"deny": ["core/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        return hooks_dir

    def test_blocks_echo_redirect_to_deny_zone(self, tmp_path):
        hooks_dir = self._setup_deny(tmp_path)
        stdout, code = _run_isolated(hooks_dir, "echo 'hack' > core/module.py")
        assert code == 2
        assert "protected zone" in stdout.lower() or "deny" in stdout.lower()

    def test_blocks_printf_redirect_to_deny_zone(self, tmp_path):
        hooks_dir = self._setup_deny(tmp_path)
        _, code = _run_isolated(hooks_dir, "printf '%s' 'data' > core/file.py")
        assert code == 2

    def test_blocks_cp_to_deny_zone(self, tmp_path):
        hooks_dir = self._setup_deny(tmp_path)
        _, code = _run_isolated(hooks_dir, "cp /tmp/evil.py core/module.py")
        assert code == 2

    def test_blocks_mv_to_deny_zone(self, tmp_path):
        hooks_dir = self._setup_deny(tmp_path)
        _, code = _run_isolated(hooks_dir, "mv /tmp/evil.py core/module.py")
        assert code == 2

    def test_blocks_sed_inplace_in_deny_zone(self, tmp_path):
        hooks_dir = self._setup_deny(tmp_path)
        _, code = _run_isolated(hooks_dir, "sed -i 's/old/new/' core/module.py")
        assert code == 2

    def test_allows_echo_to_non_deny_zone(self, tmp_path):
        hooks_dir = self._setup_deny(tmp_path)
        _, code = _run_isolated(hooks_dir, "echo 'ok' > src/app.py")
        assert code == 0

    def test_allows_cp_to_warn_zone(self, tmp_path):
        """Write to WARN zone is allowed (only DENY zones are blocked)."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {"protected_zones": {"warn": ["lib/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "cp /tmp/file.py lib/utils.py")
        assert code == 0

    def test_blocks_cp_with_flags_to_deny_zone(self, tmp_path):
        hooks_dir = self._setup_deny(tmp_path)
        _, code = _run_isolated(hooks_dir, "cp -r /tmp/dir core/subdir")
        assert code == 2


class TestConfigWritePatterns:
    """Test that write_patterns can be overridden via config.

    Uses V2 hook (staged or already copied).
    """

    def test_config_write_patterns_override(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {
            "protected_zones": {"deny": ["secret/"]},
            "write_patterns": [
                {"pattern": "\\bmy_write\\s+(\\S+)", "description": "custom write"}
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # Custom pattern should block
        _, code = _run_isolated(hooks_dir, "my_write secret/file.py")
        assert code == 2
        # Standard cat > should NOT block (config replaces inline)
        _, code2 = _run_isolated(hooks_dir, "cat > secret/file.py")
        assert code2 == 0


class TestSegmentAwareMatching:
    """ENG-001: zone matching must be segment-aware, not substring-based.

    Uses V2 hook (staged or already copied).
    """

    def test_zone_core_does_not_match_coredump(self, tmp_path):
        """Zone 'core' must NOT block writes to 'coredump/'."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {"protected_zones": {"deny": ["core/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "echo x > coredump/file.py")
        assert code == 0, "zone 'core/' should not match 'coredump/'"

    def test_zone_core_still_matches_core_subdir(self, tmp_path):
        """Zone 'core' must still block writes to 'core/module.py'."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {"protected_zones": {"deny": ["core/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "echo x > core/module.py")
        assert code == 2

    def test_zone_lib_does_not_match_stdlib(self, tmp_path):
        """Zone 'lib' must NOT block writes to 'stdlib/utils.py'."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {"protected_zones": {"deny": ["lib/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "echo x > stdlib/utils.py")
        assert code == 0

    def test_multi_segment_zone(self, tmp_path):
        """Multi-segment zone 'src/core' must match 'src/core/file.py'."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {"protected_zones": {"deny": ["src/core/"]}}
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "cp evil.py src/core/file.py")
        assert code == 2


class TestMandatoryBashWriteDenyZones:
    def test_blocks_traversal_path_into_mandatory_hooks_zone(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        _, code = _run_isolated(
            hooks_dir,
            "echo x > templates/hooks/../hooks/file.py",
        )
        assert code == 2

    def test_blocks_dot_relative_path_into_mandatory_hooks_zone(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        _, code = _run_isolated(hooks_dir, "echo x > ./templates/hooks/file.py")
        assert code == 2

    def test_blocks_backslash_path_into_mandatory_hooks_zone(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        _, code = _run_isolated(hooks_dir, "echo x > templates\\hooks\\file.py")
        assert code == 2

    def test_blocks_quoted_path_with_spaces_into_mandatory_hooks_zone(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        _, code = _run_isolated(hooks_dir, 'echo x > "templates/hooks/file name.py"')
        assert code == 2

    def test_blocks_mandatory_methodology_file(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        _, code = _run_isolated(hooks_dir, "echo x > dev/methodology_full.md")
        assert code == 2

    def test_empty_config_does_not_disable_mandatory_deny_zones(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps({"protected_zones": {"deny": [], "warn": []}}),
            encoding="utf-8",
        )

        _, code = _run_isolated(hooks_dir, "echo x > templates/hooks/file.py")
        assert code == 2

    def test_similar_path_does_not_match_mandatory_hooks_zone(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        _, code = _run_isolated(hooks_dir, "echo x > templates/hookside/file.py")
        assert code == 0

    def test_blocks_symlink_targeting_mandatory_hooks_zone(self, tmp_path):
        hooks_dir = _setup_isolated(tmp_path)
        protected_dir = tmp_path / "templates" / "hooks"
        protected_dir.mkdir(parents=True)
        (protected_dir / "linked.py").write_text("# linked", encoding="utf-8")
        link_dir = tmp_path / "linked_hooks"
        try:
            link_dir.symlink_to(protected_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation is not reliable in this environment: {exc}")

        _, code = _run_isolated(hooks_dir, f'echo x > "{link_dir / "linked.py"}"')
        assert code == 2


class TestInvalidRegexHandling:
    """ENG-002: invalid regex in config must not crash the hook.

    Uses V2 hook (staged or already copied).
    """

    def test_invalid_dangerous_pattern_skipped(self, tmp_path):
        """Invalid regex in dangerous_patterns: skip it, check remaining."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {
            "dangerous_patterns": [
                {"pattern": "(?P<broken", "description": "bad regex"},
                {"pattern": "rm\\s+-rf\\s+/", "description": "valid pattern"},
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # Bad regex skipped, valid pattern still blocks
        _, code = _run_isolated(hooks_dir, "rm -rf /etc")
        assert code == 2

    def test_all_invalid_patterns_fail_open(self, tmp_path):
        """All patterns invalid: hook allows command (fail open)."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {
            "dangerous_patterns": [
                {"pattern": "(?P<broken", "description": "bad1"},
                {"pattern": "[unclosed", "description": "bad2"},
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        _, code = _run_isolated(hooks_dir, "rm -rf /etc")
        assert code == 0

    def test_invalid_write_pattern_skipped(self, tmp_path):
        """Invalid regex in write_patterns: skip it, don't crash."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {
            "protected_zones": {"deny": ["core/"]},
            "write_patterns": [
                {"pattern": "[bad", "description": "broken"},
                {"pattern": "\\becho\\b.*?>\\s*([^\\s;|&]+)", "description": "valid echo"},
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # Bad write pattern skipped, valid one still blocks
        _, code = _run_isolated(hooks_dir, "echo hack > core/file.py")
        assert code == 2

    def test_write_pattern_no_capture_group_skipped(self, tmp_path):
        """Write pattern without capture group: skip, don't crash."""
        hooks_dir = _setup_isolated(tmp_path, hook_source=HOOK_V2_PATH)
        config = {
            "protected_zones": {"deny": ["core/"]},
            "write_patterns": [
                {"pattern": "\\bcat\\s*>\\s*[^\\s;|&]+", "description": "no capture group"},
            ]
        }
        (tmp_path / ".claude" / "cc_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        # No capture group -> IndexError caught -> skip -> allow
        _, code = _run_isolated(hooks_dir, "cat > core/file.py")
        assert code == 0
