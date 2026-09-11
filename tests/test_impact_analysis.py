"""Tests for Impact Analysis functions (Section 9.18).

Tests extract_fingerprints, grep_codebase, read_file_identifiers,
and format_impact_section from codewarden_backend.py.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent / "templates" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from codewarden_backend import (
    extract_fingerprints,
    format_impact_section,
    grep_codebase,
    load_fitness_review_context,
    read_file_identifiers,
)


# ---------------------------------------------------------------------------
# extract_fingerprints tests
# ---------------------------------------------------------------------------


class TestExtractFingerprintsDiff:
    """Tests for extract_fingerprints in diff mode."""

    def test_extract_function_defs(self):
        diff = """\
+def calculate_score(voice, evidence):
+    pass
+def _helper():
+    pass
"""
        result = extract_fingerprints(diff, mode="diff")
        assert "calculate_score" in result
        assert "_helper" in result

    def test_extract_class_names(self):
        diff = "+class ClaudeAccountScorer(LLMScorer):"
        result = extract_fingerprints(diff, mode="diff")
        assert "ClaudeAccountScorer" in result
        assert "LLMScorer" in result

    def test_extract_imports(self):
        diff = "+from astera_ai.plugins.icc.claude_cli import build_diagnostics"
        result = extract_fingerprints(diff, mode="diff")
        assert "astera_ai.plugins.icc.claude_cli" in result

    def test_extract_subprocess_commands(self):
        diff = """\
+    result = subprocess.run(["claude", "--print", "--model", "haiku"])
"""
        result = extract_fingerprints(diff, mode="diff")
        assert "claude" in result

    def test_extract_env_vars(self):
        diff = """\
+BACKEND = os.environ.get("CODEWARDEN_BACKEND", "ollama")
+KEY = os.environ["ANTHROPIC_API_KEY"]
"""
        result = extract_fingerprints(diff, mode="diff")
        assert "CODEWARDEN_BACKEND" in result
        assert "ANTHROPIC_API_KEY" in result

    def test_extract_constants(self):
        diff = """\
+MAX_RETRIES = 3
+HAIKU_MODEL = "haiku"
"""
        result = extract_fingerprints(diff, mode="diff")
        assert "MAX_RETRIES" in result
        assert "HAIKU_MODEL" in result

    def test_filters_common_names(self):
        diff = """\
+def main():
+    data = get_data()
+    result = process(data)
+    return result
"""
        result = extract_fingerprints(diff, mode="diff")
        # These are in _COMMON_NAMES and should be filtered
        lower_results = [r.lower() for r in result]
        assert "main" not in lower_results
        assert "data" not in lower_results
        assert "result" not in lower_results

    def test_empty_input_returns_empty(self):
        assert extract_fingerprints("", mode="diff") == []
        assert extract_fingerprints("", mode="plan") == []

    def test_dedup_and_cap(self):
        # Generate more than MAX_FINGERPRINTS unique patterns
        lines = [f"+def function_{i}(x):\n" for i in range(30)]
        diff = "".join(lines)
        result = extract_fingerprints(diff, mode="diff")
        # Should be capped
        assert len(result) <= 20
        # Should be deduplicated (all unique)
        assert len(result) == len(set(r.lower() for r in result))

    def test_min_length_filter(self):
        diff = "+def ab():\n+def abc():\n"
        result = extract_fingerprints(diff, mode="diff")
        # "ab" is too short (< 3 chars)
        assert "ab" not in result
        assert "abc" in result


class TestExtractFingerprintsPlan:
    """Tests for extract_fingerprints in plan mode."""

    def test_extract_file_paths(self):
        plan = "Modify `src/scoring_engine.py` and `utils/helper.js`."
        result = extract_fingerprints(plan, mode="plan")
        assert "src/scoring_engine.py" in result
        assert "utils/helper.js" in result

    def test_extract_backtick_identifiers(self):
        plan = "Change `call_model` to use the new `OllamaBackend`."
        result = extract_fingerprints(plan, mode="plan")
        assert "call_model" in result
        assert "OllamaBackend" in result

    def test_extract_quoted_identifiers(self):
        plan = 'Set "ROUTING_BACKEND" to "ollama" in config.'
        result = extract_fingerprints(plan, mode="plan")
        assert "ROUTING_BACKEND" in result
        assert "ollama" in result

    def test_extract_action_verb_targets(self):
        plan = "Update calculate_score to handle the new evidence format."
        result = extract_fingerprints(plan, mode="plan")
        assert "calculate_score" in result

    def test_multiple_verb_patterns(self):
        plan = (
            "Modify llm_backend to add Ollama support. "
            "Change call_model signature. "
            "Fix retry_logic for timeouts."
        )
        result = extract_fingerprints(plan, mode="plan")
        assert "llm_backend" in result
        assert "call_model" in result
        assert "retry_logic" in result


# ---------------------------------------------------------------------------
# grep_codebase tests
# ---------------------------------------------------------------------------


class TestGrepCodebase:
    """Tests for grep_codebase using temporary git repos."""

    @pytest.fixture
    def git_repo(self, tmp_path):
        """Create a temporary git repo with test files."""
        subprocess.run(
            ["git", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=5,
        )

        # Create test files
        (tmp_path / "backend.py").write_text(
            "def call_model(prompt):\n    return 'result'\n",
            encoding="utf-8",
        )
        (tmp_path / "scorer.py").write_text(
            "from backend import call_model\n"
            "result = call_model('test')\n",
            encoding="utf-8",
        )
        (tmp_path / "retriever.py").write_text(
            "from backend import call_model\n"
            "data = call_model('query')\n",
            encoding="utf-8",
        )
        (tmp_path / "unrelated.py").write_text(
            "def other_function():\n    pass\n",
            encoding="utf-8",
        )

        # Stage and commit
        subprocess.run(
            ["git", "add", "."],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=10,
        )

        return tmp_path

    def test_grep_finds_matches(self, git_repo):
        result = grep_codebase(git_repo, ["call_model"])
        assert "call_model" in result
        assert len(result["call_model"]) >= 2  # scorer.py, retriever.py, backend.py

    def test_grep_excludes_specified_files(self, git_repo):
        result = grep_codebase(
            git_repo, ["call_model"], exclude_files=["backend.py"]
        )
        assert "call_model" in result
        # backend.py should be excluded
        assert "backend.py" not in result["call_model"]

    def test_grep_no_match_returns_empty(self, git_repo):
        result = grep_codebase(git_repo, ["nonexistent_pattern_xyz"])
        assert result == {}

    def test_grep_non_git_returns_empty(self, tmp_path):
        # tmp_path is not a git repo
        (tmp_path / "file.py").write_text("call_model()", encoding="utf-8")
        result = grep_codebase(tmp_path, ["call_model"])
        # git grep should fail, returning empty
        assert result == {}

    def test_grep_multiple_patterns(self, git_repo):
        result = grep_codebase(git_repo, ["call_model", "other_function"])
        assert "call_model" in result
        assert "other_function" in result
        assert "unrelated.py" in result["other_function"]

    def test_grep_empty_patterns_returns_empty(self, git_repo):
        result = grep_codebase(git_repo, [])
        assert result == {}


# ---------------------------------------------------------------------------
# read_file_identifiers tests
# ---------------------------------------------------------------------------


class TestReadFileIdentifiers:
    """Tests for read_file_identifiers."""

    def test_reads_python_function_defs(self, tmp_path):
        (tmp_path / "module.py").write_text(
            "def calculate_score(voice):\n"
            "    pass\n\n"
            "def _internal_helper():\n"
            "    pass\n\n"
            "class ScoringEngine:\n"
            "    pass\n",
            encoding="utf-8",
        )
        result = read_file_identifiers(tmp_path, "module.py")
        assert "calculate_score" in result
        assert "_internal_helper" in result
        assert "ScoringEngine" in result

    def test_reads_constants(self, tmp_path):
        (tmp_path / "config.py").write_text(
            "MAX_RETRIES = 3\n"
            "DEFAULT_MODEL = 'haiku'\n"
            "TIMEOUT_SECONDS = 30\n",
            encoding="utf-8",
        )
        result = read_file_identifiers(tmp_path, "config.py")
        assert "MAX_RETRIES" in result
        assert "DEFAULT_MODEL" in result
        assert "TIMEOUT_SECONDS" in result

    def test_nonexistent_file_returns_empty(self, tmp_path):
        result = read_file_identifiers(tmp_path, "does_not_exist.py")
        assert result == []

    def test_filters_common_names(self, tmp_path):
        (tmp_path / "common.py").write_text(
            "def main():\n    pass\n"
            "def get():\n    pass\n"
            "def calculate_important():\n    pass\n",
            encoding="utf-8",
        )
        result = read_file_identifiers(tmp_path, "common.py")
        lower_results = [r.lower() for r in result]
        assert "main" not in lower_results
        assert "calculate_important" in result


# ---------------------------------------------------------------------------
# format_impact_section tests
# ---------------------------------------------------------------------------


class TestFormatImpactSection:
    """Tests for format_impact_section."""

    def test_formats_basic_results(self):
        impact = {
            "call_model": ["scorer.py", "retriever.py"],
            "claude --print": ["blocks.py"],
        }
        result = format_impact_section(impact, ["backend.py"])
        assert "## Impact Scan Results" in result
        assert "call_model" in result
        assert "scorer.py" in result
        assert "retriever.py" in result
        assert "blocks.py" in result

    def test_empty_results_returns_empty(self):
        assert format_impact_section({}, ["backend.py"]) == ""

    def test_excludes_plan_files_from_results(self):
        impact = {
            "call_model": ["backend.py", "scorer.py"],
        }
        result = format_impact_section(impact, ["backend.py"])
        # backend.py is in the plan, should not appear as unmentioned
        assert "backend.py" not in result.split("call_model")[1]

    def test_all_files_in_plan_returns_empty(self):
        impact = {
            "call_model": ["backend.py"],
        }
        result = format_impact_section(impact, ["backend.py"])
        assert result == ""

    def test_truncates_long_file_lists(self):
        impact = {
            "pattern": [f"file_{i}.py" for i in range(10)],
        }
        result = format_impact_section(impact, [])
        assert "(+5 more)" in result

    def test_contains_guidance_text(self):
        impact = {"call_model": ["scorer.py"]}
        result = format_impact_section(impact, [])
        assert "MAY need changes" in result


class TestFitnessReviewContext:
    def test_missing_fitness_script_returns_empty(self, tmp_path):
        context = load_fitness_review_context(tmp_path, focus_files=["shared/api.py"])
        assert context["signal_count"] == 0
        assert context["prompt_section"] == ""

    def test_collects_layer_signal_for_focus_file(self, tmp_path):
        fitness_src = Path(__file__).resolve().parent.parent / "scripts" / "fitness_check.py"
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "fitness_check.py").write_text(
            fitness_src.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        (tmp_path / "shared").mkdir()
        (tmp_path / "features").mkdir()
        (tmp_path / "shared" / "api.py").write_text(
            "import features.orders\n",
            encoding="utf-8",
        )
        (tmp_path / "features" / "orders.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
        (tmp_path / "fitness.json").write_text(
            json.dumps({
                "layer_rules": [
                    {
                        "source_zone": "shared",
                        "forbidden_target_zones": ["features"],
                        "severity": "fail",
                        "message": "shared cannot import features",
                    }
                ]
            }),
            encoding="utf-8",
        )

        context = load_fitness_review_context(
            tmp_path,
            focus_files=["shared/api.py"],
        )

        assert context["signal_count"] >= 1
        assert context["focus_signal_count"] >= 1
        assert "Architectural Fitness Signals" in context["prompt_section"]
        assert "shared/api.py" in context["prompt_section"]
        assert "shared cannot import features" in context["prompt_section"]
        assert "layer violation" in context["prompt_section"].lower()

    def test_collects_layer_signal_from_shared_architecture_fitness_config(
        self, tmp_path
    ):
        fitness_src = Path(__file__).resolve().parent.parent / "scripts" / "fitness_check.py"
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "fitness_check.py").write_text(
            fitness_src.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        (tmp_path / "shared").mkdir()
        (tmp_path / "features").mkdir()
        (tmp_path / "shared" / "api.py").write_text(
            "import features.orders\n",
            encoding="utf-8",
        )
        (tmp_path / "features" / "orders.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {
                    "architecture_fitness": {
                        "zones": {
                            "shared": "shared",
                            "features": "features",
                        },
                        "layer_rules": [
                            {
                                "source_zone": "shared",
                                "forbidden_target_zones": ["features"],
                                "severity": "fail",
                                "message": "shared cannot import features",
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        context = load_fitness_review_context(
            tmp_path,
            focus_files=["shared/api.py"],
        )

        assert context["signal_count"] >= 1
        assert context["focus_signal_count"] >= 1
        assert "Architectural Fitness Signals" in context["prompt_section"]
        assert "shared/api.py" in context["prompt_section"]
        assert "shared cannot import features" in context["prompt_section"]
        assert "layer violation" in context["prompt_section"].lower()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests for the full impact analysis pipeline."""

    @pytest.fixture
    def git_project(self, tmp_path):
        """Create a git repo that mimics a real project with cross-cutting patterns."""
        subprocess.run(
            ["git", "init"], cwd=str(tmp_path), capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path), capture_output=True, timeout=5,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path), capture_output=True, timeout=5,
        )

        # Create files mimicking the Astera pattern
        (tmp_path / "llm_backend.py").write_text(
            "import subprocess\n\n"
            "def call_model(prompt, model='opus'):\n"
            "    result = subprocess.run(['claude', '--print', '--model', model])\n"
            "    return result.stdout\n\n"
            "HAIKU_MODEL = 'haiku'\n"
            "SONNET_MODEL = 'sonnet'\n",
            encoding="utf-8",
        )
        (tmp_path / "scorer.py").write_text(
            "from llm_backend import call_model\n\n"
            "def score_voice(voice, evidence):\n"
            "    return call_model(build_prompt(voice, evidence))\n",
            encoding="utf-8",
        )
        (tmp_path / "blocks.py").write_text(
            "import subprocess\n\n"
            "def keyword_verify(text):\n"
            "    # BYPASS: calls claude directly, not through llm_backend\n"
            "    result = subprocess.run(['claude', '--print', '--model', 'haiku'])\n"
            "    return result.stdout\n",
            encoding="utf-8",
        )
        (tmp_path / "arbiter.py").write_text(
            "import subprocess\n\n"
            "def disambiguate(context):\n"
            "    # Another bypass\n"
            "    result = subprocess.run(['claude', '--print', '--model', 'sonnet'])\n"
            "    return result.stdout\n",
            encoding="utf-8",
        )

        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path), capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path), capture_output=True, timeout=10,
        )

        return tmp_path

    def test_plan_mentioning_backend_finds_bypass_files(self, git_project):
        """When plan mentions llm_backend.py, impact scan finds blocks.py and arbiter.py."""
        plan = "Modify `llm_backend.py` to route haiku calls to Ollama."

        # Extract fingerprints from plan
        fingerprints = extract_fingerprints(plan, mode="plan")
        # Also read the referenced file's identifiers
        fingerprints.extend(
            read_file_identifiers(git_project, "llm_backend.py")
        )

        # Grep codebase
        impact = grep_codebase(
            git_project, fingerprints, exclude_files=["llm_backend.py"]
        )

        # Should find call_model in scorer.py (import-based dep)
        all_files = [f for files in impact.values() for f in files]
        assert "scorer.py" in all_files

        # Should find 'claude' pattern in blocks.py and arbiter.py (bypass)
        assert any("blocks.py" in files for files in impact.values())
        assert any("arbiter.py" in files for files in impact.values())

    def test_diff_fingerprints_find_unchanged_files(self, git_project):
        """When diff modifies call_model, impact scan finds all callers."""
        diff = """\
diff --git a/llm_backend.py b/llm_backend.py
--- a/llm_backend.py
+++ b/llm_backend.py
@@ -3,4 +3,6 @@
 def call_model(prompt, model='opus'):
-    result = subprocess.run(['claude', '--print', '--model', model])
+    # Route to Ollama for haiku
+    if model == 'haiku':
+        return call_ollama(prompt)
+    result = subprocess.run(['claude', '--print', '--model', model])
     return result.stdout
"""
        fingerprints = extract_fingerprints(diff, mode="diff")
        diff_files = ["llm_backend.py"]

        impact = grep_codebase(
            git_project, fingerprints, exclude_files=diff_files
        )

        # call_model should be found in scorer.py
        all_files = [f for files in impact.values() for f in files]
        assert "scorer.py" in all_files

    def test_gateway_modules_from_config(self, git_project):
        """Gateway modules registered in cc_config.json are included in scan."""
        # Create cc_config.json with gateway_modules
        (git_project / ".claude").mkdir(exist_ok=True)
        (git_project / ".claude" / "cc_config.json").write_text(
            json.dumps({
                "gateway_modules": [
                    {
                        "pattern": "claude",
                        "file": "llm_backend.py",
                        "description": "All claude CLI calls through llm_backend"
                    }
                ]
            }),
            encoding="utf-8",
        )

        # Start with an empty plan - only gateway patterns
        fingerprints = ["claude"]

        impact = grep_codebase(
            git_project, fingerprints, exclude_files=["llm_backend.py"]
        )

        # blocks.py and arbiter.py both have "claude" and should be flagged
        all_files = [f for files in impact.values() for f in files]
        assert "blocks.py" in all_files
        assert "arbiter.py" in all_files

    def test_impact_scan_failure_is_silent(self, tmp_path):
        """Impact analysis failures should return empty, never raise."""
        # Non-git directory with no files
        result = grep_codebase(tmp_path, ["nonexistent"])
        assert result == {}

        result = format_impact_section({}, [])
        assert result == ""

        result = read_file_identifiers(tmp_path, "nonexistent.py")
        assert result == []

    def test_impact_category_in_violation_store(self):
        """The 'impact' category should be valid in CW_CATEGORIES."""
        from violation_store import CW_CATEGORIES
        assert "impact" in CW_CATEGORIES
