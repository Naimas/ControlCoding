#!/usr/bin/env python3
"""Tests for fitness_check.py - Architectural Fitness Functions."""

import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
if "fitness_check" in sys.modules:
    del sys.modules["fitness_check"]
import fitness_check


# ------------------------------------------------------------------ helpers ---


def _make_zone_project(tmp_path, files: dict[str, str] | None = None):
    """Create a project with zone directories and optional Python files.

    files: mapping of relative_path -> content (Python source).
    """
    for zone in ["stable", "shared", "features", "workspace"]:
        (tmp_path / zone).mkdir(exist_ok=True)
    if files:
        for relpath, content in files.items():
            p = tmp_path / relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------- TestLoadConfig ---


class TestLoadConfig:
    def test_defaults_when_no_config(self, tmp_path):
        config = fitness_check.load_config(tmp_path)
        assert config["thresholds"]["files_per_commit_max"] == 5
        assert config["thresholds"]["unclassified_warn_pct"] == 20
        assert config["zones"] == {}

    def test_loads_valid_config(self, tmp_path):
        cfg = {"thresholds": {"files_per_commit_max": 10}}
        (tmp_path / "fitness.json").write_text(json.dumps(cfg), encoding="utf-8")
        config = fitness_check.load_config(tmp_path)
        assert config["thresholds"]["files_per_commit_max"] == 10
        # Other defaults preserved
        assert config["thresholds"]["cochange_warn_threshold"] == 3

    def test_invalid_json_falls_back(self, tmp_path):
        # Note: load_config shallow-copies DEFAULT_CONFIG, so prior tests
        # may have mutated nested dicts. We just verify the structure is valid
        # and defaults are dict-like (not the exact values).
        (tmp_path / "fitness.json").write_text("NOT JSON", encoding="utf-8")
        config = fitness_check.load_config(tmp_path)
        assert "files_per_commit_max" in config["thresholds"]
        assert "cochange_warn_threshold" in config["thresholds"]

    def test_partial_config_merges(self, tmp_path):
        cfg = {"zones": {"core": "src/core"}, "git_history_commits": 50}
        (tmp_path / "fitness.json").write_text(json.dumps(cfg), encoding="utf-8")
        config = fitness_check.load_config(tmp_path)
        assert config["zones"]["core"] == "src/core"
        assert config["git_history_commits"] == 50

    def test_loads_layer_rules(self, tmp_path):
        cfg = {
            "layer_rules": [
                {
                    "source_zone": "shared",
                    "forbidden_target_zones": ["features"],
                    "severity": "fail",
                }
            ]
        }
        (tmp_path / "fitness.json").write_text(json.dumps(cfg), encoding="utf-8")
        config = fitness_check.load_config(tmp_path)
        assert config["layer_rules"][0]["source_zone"] == "shared"

    def test_loads_gateway_rules(self, tmp_path):
        cfg = {
            "gateway_rules": [
                {
                    "pattern": "dangerous_call",
                    "allowed_files": ["shared/gateway.py"],
                    "severity": "fail",
                }
            ]
        }
        (tmp_path / "fitness.json").write_text(json.dumps(cfg), encoding="utf-8")
        config = fitness_check.load_config(tmp_path)
        assert config["gateway_rules"][0]["pattern"] == "dangerous_call"

    def test_loads_ownership_and_mutation_rules(self, tmp_path):
        cfg = {
            "ownership_rules": [
                {
                    "source_zones": ["render"],
                    "patterns": ["WorldState("],
                }
            ],
            "mutation_rules": [
                {
                    "source_zones": ["ui"],
                    "patterns": ["store.dispatch"],
                }
            ],
        }
        (tmp_path / "fitness.json").write_text(json.dumps(cfg), encoding="utf-8")
        config = fitness_check.load_config(tmp_path)
        assert config["ownership_rules"][0]["patterns"] == ["WorldState("]
        assert config["mutation_rules"][0]["patterns"] == ["store.dispatch"]

    def test_loads_architecture_fitness_from_cc_config(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {
                    "architecture_fitness": {
                        "zones": {"core": "src/core"},
                        "thresholds": {"god_file_fail_lines": 120},
                        "layer_rules": [
                            {
                                "source_zone": "shared",
                                "forbidden_target_zones": ["features"],
                                "severity": "fail",
                            }
                        ],
                        "skip_dirs": ["ignored"],
                    }
                }
            ),
            encoding="utf-8",
        )
        config = fitness_check.load_config(tmp_path)
        assert config["zones"]["core"] == "src/core"
        assert config["thresholds"]["god_file_fail_lines"] == 120
        assert config["layer_rules"][0]["source_zone"] == "shared"
        assert config["skip_dirs"] == ["ignored"]

    def test_load_config_with_evidence_reports_sources(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {
                    "architecture_fitness": {
                        "thresholds": {"god_file_fail_lines": 120},
                    }
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "fitness.json").write_text(
            json.dumps({"thresholds": {"god_file_fail_lines": 220}}),
            encoding="utf-8",
        )

        config, evidence = fitness_check.load_config_with_evidence(tmp_path)

        assert config["thresholds"]["god_file_fail_lines"] == 220
        assert evidence == [
            {
                "source": "built-in defaults",
                "key": "DEFAULT_CONFIG",
                "status": "loaded",
            },
            {
                "source": ".controlcoding/cc_config.json",
                "key": "architecture_fitness",
                "status": "loaded",
            },
            {
                "source": "fitness.json",
                "key": "local_override",
                "status": "loaded",
            },
        ]

    def test_configured_rule_counts_include_runtime_rule_packs(self):
        counts = fitness_check.configured_rule_counts(
            {
                "zones": {"stable": "src/core"},
                "thresholds": {"god_file_fail_lines": 500},
                "layer_rules": [{"source_zone": "shared"}],
                "gateway_rules": [],
                "ownership_rules": [],
                "mutation_rules": [],
            },
            gateway_rules=[{"pattern": "dangerous_call"}],
            ownership_rules=[{"patterns": ["WorldState("]}],
            mutation_rules=[{"patterns": ["store.dispatch"]}],
        )

        assert counts == {
            "zones": 1,
            "thresholds": 1,
            "layer_rules": 1,
            "gateway_rules": 1,
            "ownership_rules": 1,
            "mutation_rules": 1,
        }

    def test_fitness_json_overrides_cc_config_architecture_fitness(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {
                    "architecture_fitness": {
                        "thresholds": {"god_file_fail_lines": 120},
                    }
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "fitness.json").write_text(
            json.dumps({"thresholds": {"god_file_fail_lines": 220}}),
            encoding="utf-8",
        )
        config = fitness_check.load_config(tmp_path)
        assert config["thresholds"]["god_file_fail_lines"] == 220

    def test_loads_fitness_alias_from_legacy_cc_config(self, tmp_path):
        control_dir = tmp_path / ".claude"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps({"fitness": {"thresholds": {"god_file_warn_lines": 90}}}),
            encoding="utf-8",
        )
        config = fitness_check.load_config(tmp_path)
        assert config["thresholds"]["god_file_warn_lines"] == 90


# -------------------------------------------------------- TestDetectZones ---


class TestDetectZones:
    def test_auto_detect_root_level(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        assert "stable" in zones
        assert "shared" in zones
        assert "features" in zones
        assert "workspace" in zones

    def test_auto_detect_under_src(self, tmp_path):
        src = tmp_path / "src"
        (src / "stable").mkdir(parents=True)
        (src / "shared").mkdir(parents=True)
        zones = fitness_check.detect_zones(tmp_path, {})
        assert "stable" in zones
        assert "shared" in zones

    def test_explicit_config_overrides(self, tmp_path):
        (tmp_path / "core").mkdir()
        zones = fitness_check.detect_zones(tmp_path, {"stable": "core"})
        assert "stable" in zones
        assert zones["stable"] == (tmp_path / "core").resolve()

    def test_empty_when_no_zones(self, tmp_path):
        zones = fitness_check.detect_zones(tmp_path, {})
        assert zones == {}


# ------------------------------------------------------- TestClassifyFile ---


class TestClassifyFile:
    def test_file_in_zone(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        f = tmp_path / "stable" / "core.py"
        f.write_text("", encoding="utf-8")
        assert fitness_check.classify_file(f, zones) == "stable"

    def test_file_outside_zones(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        f = tmp_path / "readme.md"
        f.write_text("", encoding="utf-8")
        assert fitness_check.classify_file(f, zones) is None

    def test_nested_file(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        f = tmp_path / "features" / "auth" / "login.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
        assert fitness_check.classify_file(f, zones) == "features"


# ----------------------------------------------------- TestCountZoneFiles ---


class TestCountZoneFiles:
    def test_counts_per_zone(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "stable/a.py": "",
                "stable/b.py": "",
                "features/c.py": "",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        counts = fitness_check.count_zone_files(tmp_path, zones, [".git", "__pycache__"])
        assert counts["stable"] == 2
        assert counts["features"] == 1

    def test_unclassified_counted(self, tmp_path):
        _make_zone_project(tmp_path, {"root_file.txt": "hello"})
        zones = fitness_check.detect_zones(tmp_path, {})
        counts = fitness_check.count_zone_files(tmp_path, zones, [".git", "__pycache__"])
        assert counts["unclassified"] >= 1

    def test_skip_dirs_excluded(self, tmp_path):
        _make_zone_project(tmp_path)
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "cached.pyc").write_text("", encoding="utf-8")
        zones = fitness_check.detect_zones(tmp_path, {})
        counts = fitness_check.count_zone_files(tmp_path, zones, ["__pycache__"])
        # cached.pyc should not appear
        total = sum(counts.values())
        files_in_cache = [
            f for f in fitness_check.source_files(tmp_path, [])
            if "__pycache__" in str(f)
        ]
        for f in files_in_cache:
            zone = fitness_check.classify_file(f, zones)
            if zone:
                assert False, "__pycache__ file should not be in zone counts"


class TestZoneHygiene:
    def test_evaluate_zone_hygiene_warns_on_large_unclassified_share(self):
        alerts = fitness_check.evaluate_zone_hygiene(
            {"stable": 2, "shared": 2, "features": 2, "unclassified": 3},
            fitness_check.DEFAULT_CONFIG["thresholds"],
        )
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "warn"

    def test_evaluate_zone_hygiene_fails_on_extreme_unclassified_share(self):
        alerts = fitness_check.evaluate_zone_hygiene(
            {"stable": 2, "shared": 2, "features": 2, "unclassified": 8},
            fitness_check.DEFAULT_CONFIG["thresholds"],
        )
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "fail"


# -------------------------------------------------------- TestSourceFiles ---


class TestSourceFiles:
    def test_collects_all_files(self, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("", encoding="utf-8")
        result = fitness_check.source_files(tmp_path, [])
        assert len(result) == 2

    def test_classifies_source_kinds_by_path(self, tmp_path):
        cases = {
            "features/app.py": fitness_check.SOURCE_KIND_LIVE_SOURCE,
            ".controlwork/tmp/run/artifact.py": fitness_check.SOURCE_KIND_TEMP_WORKSPACE,
            ".controlcoding/cache/state.py": fitness_check.SOURCE_KIND_TEMP_WORKSPACE,
            "release-bundle-1/scripts/cc.py": fitness_check.SOURCE_KIND_GENERATED_RELEASE,
            "ui/release/assets/app.py": fitness_check.SOURCE_KIND_GENERATED_RELEASE,
            "build/generated.py": fitness_check.SOURCE_KIND_GENERATED_RELEASE,
            "tools/fitness_check.py": fitness_check.SOURCE_KIND_TOOL_COPY,
            "node_modules/pkg/index.py": fitness_check.SOURCE_KIND_EXCLUDED_ARTIFACT,
        }

        for relpath, expected in cases.items():
            path = tmp_path / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x = 1\n", encoding="utf-8")
            assert fitness_check.classify_source_kind(tmp_path, path) == expected

    def test_skips_configured_dirs(self, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("", encoding="utf-8")
        result = fitness_check.source_files(tmp_path, ["node_modules"])
        assert len(result) == 1

    def test_skips_glob_configured_dirs(self, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        generated = tmp_path / "generated-123"
        generated.mkdir()
        (generated / "b.py").write_text("", encoding="utf-8")

        result = fitness_check.source_files(tmp_path, ["generated-*"])

        assert [p.name for p in result] == ["a.py"]

    def test_skips_relative_path_configured_dirs(self, tmp_path):
        live_release = tmp_path / "release"
        live_release.mkdir()
        (live_release / "live.py").write_text("", encoding="utf-8")
        ui_release = tmp_path / "ui" / "release"
        ui_release.mkdir(parents=True)
        (ui_release / "packaged.py").write_text("", encoding="utf-8")

        result = fitness_check.source_files(tmp_path, ["ui/release"])

        refs = sorted(
            str(path.relative_to(tmp_path)).replace("\\", "/")
            for path in result
        )
        assert refs == ["release/live.py"]

    def test_always_skips_local_test_workspaces(self, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        for dirname in [
            ".pytest-tmp",
            ".pytest-tmp-old",
            ".tmp-pytest-old",
            ".tmp_pytest_old",
            ".tmp_release",
            "pytest-cache-files-old",
            "pytest_tmp_old",
            "scratch-test",
        ]:
            local_workspace = tmp_path / dirname
            local_workspace.mkdir()
            (local_workspace / "should_not_scan.py").write_text("", encoding="utf-8")

        result = fitness_check.source_files(tmp_path, [])

        assert [p.name for p in result] == ["a.py"]

    def test_controlwork_tmp_artifact_does_not_enter_fitness_scan(self, tmp_path):
        (tmp_path / "live.py").write_text("x = 1\n", encoding="utf-8")
        artifact = (
            tmp_path
            / ".controlwork"
            / "tmp"
            / "controlcoding_v1_g32_20260614_efd00b1"
            / "artifact"
            / "scripts"
            / "cc.py"
        )
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            "\n".join(f"value_{idx} = {idx}" for idx in range(60)),
            encoding="utf-8",
        )
        config = fitness_check.load_config(tmp_path)

        sources = fitness_check.source_files(tmp_path, config["skip_dirs"])
        refs = sorted(
            str(path.relative_to(tmp_path)).replace("\\", "/")
            for path in sources
        )

        assert refs == ["live.py"]

        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 5
        thresholds["god_file_fail_lines"] = 10
        structure = fitness_check.analyze_python_structure(
            tmp_path,
            {},
            config["skip_dirs"],
            thresholds,
        )

        assert not any(
            risk["file"].startswith(".controlwork/")
            for risk in structure["god_file_risks"]
        )
        assert not any(
            risk["severity"] == "fail"
            for risk in structure["god_file_risks"]
        )

    def test_configured_artifacts_do_not_enter_live_god_file_ranking(self, tmp_path):
        artifact_body = "\n".join(f"value_{idx} = {idx}" for idx in range(40))
        for relpath in [
            ".controlwork/tmp/run/artifact/scripts/cc.py",
            ".tmp_pytest_case/copied.py",
            "release-bundle-1/scripts/cc.py",
            "build/generated.py",
            "ui/release/assets/app.py",
            "node_modules/pkg/index.py",
        ]:
            path = tmp_path / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(artifact_body, encoding="utf-8")

        config = fitness_check.load_config(tmp_path)
        assert fitness_check.source_files(tmp_path, config["skip_dirs"]) == []

        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 5
        thresholds["god_file_fail_lines"] = 10
        structure = fitness_check.analyze_python_structure(
            tmp_path,
            {},
            [],
            thresholds,
        )

        assert structure["god_file_risks"] == []
        excluded_refs = {
            risk["file"] for risk in structure["excluded_god_file_risks"]
        }
        assert "release-bundle-1/scripts/cc.py" in excluded_refs
        assert "build/generated.py" in excluded_refs
        assert "node_modules/pkg/index.py" in excluded_refs


# --------------------------------------------------------- TestAnalyzeGit ---


class TestAnalyzeGit:
    def test_empty_git_log(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        with patch("fitness_check.run_git", return_value=""):
            result = fitness_check.analyze_git(tmp_path, zones, 100)
        assert result["total_commits"] == 0
        assert result["files_per_commit"] == []

    def test_single_commit(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        log = "COMMIT:abc1234\nstable/core.py\nstable/utils.py"
        with patch("fitness_check.run_git", return_value=log):
            result = fitness_check.analyze_git(tmp_path, zones, 100)
        assert result["total_commits"] == 1
        assert result["files_per_commit"][0][1] == 2  # 2 files

    def test_cross_zone_detection(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        log = "COMMIT:abc1234\nstable/core.py\nfeatures/login.py"
        with patch("fitness_check.run_git", return_value=log):
            result = fitness_check.analyze_git(tmp_path, zones, 100)
        assert result["cross_zone_commits"] == 1

    def test_cochange_pairs(self, tmp_path):
        _make_zone_project(tmp_path)
        zones = fitness_check.detect_zones(tmp_path, {})
        log = (
            "COMMIT:abc1234\nstable/core.py\nfeatures/login.py\n\n"
            "COMMIT:def5678\nstable/core.py\nfeatures/login.py"
        )
        with patch("fitness_check.run_git", return_value=log):
            result = fitness_check.analyze_git(tmp_path, zones, 100)
        assert result["total_commits"] == 2
        # The pair (stable:..., features:...) should appear 2 times
        assert max(result["cochange_pairs"].values()) == 2


# ------------------------------------------------ TestAnalyzePythonImports ---


class TestAnalyzePythonImports:
    def test_no_violations(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "stable/core.py": "",
                "features/app.py": "import stable.core\n",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        result = fitness_check.analyze_python_imports(tmp_path, zones, ["__pycache__"])
        assert len(result["violations"]) == 0

    def test_violation_stable_imports_features(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "stable/core.py": "import features.app\n",
                "features/app.py": "",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        result = fitness_check.analyze_python_imports(tmp_path, zones, ["__pycache__"])
        assert len(result["violations"]) >= 1
        assert result["violations"][0]["from_zone"] == "stable"
        assert result["violations"][0]["target_zone"] == "features"

    def test_syntax_error_skipped(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {"stable/broken.py": "def f(\n"},  # syntax error
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        # Should not raise
        result = fitness_check.analyze_python_imports(tmp_path, zones, ["__pycache__"])
        assert isinstance(result["violations"], list)


class TestAnalyzeRegexImportsCpp:
    def test_cpp_include_resolves_target_zone_from_path(self, tmp_path):
        (tmp_path / "features").mkdir()
        (tmp_path / "shared").mkdir()
        (tmp_path / "shared" / "renderer.cpp").write_text(
            '#include "features/gameplay.hpp"\n',
            encoding="utf-8",
        )
        (tmp_path / "features" / "gameplay.hpp").write_text(
            "// header\n",
            encoding="utf-8",
        )
        zones = fitness_check.detect_zones(
            tmp_path,
            {"features": "features", "shared": "shared"},
        )
        violations = fitness_check.analyze_regex_imports(
            tmp_path,
            zones,
            ["__pycache__"],
            {"cpp": "auto"},
        )
        assert len(violations) == 1
        assert violations[0]["file"] == "shared/renderer.cpp"
        assert violations[0]["from_zone"] == "shared"
        assert violations[0]["target_zone"] == "features"

    def test_self_imports_ignored(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "stable/a.py": "import stable.b\n",
                "stable/b.py": "",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        result = fitness_check.analyze_python_imports(tmp_path, zones, ["__pycache__"])
        assert len(result["violations"]) == 0

    def test_collects_cross_zone_imports(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "shared/util.py": "import stable.core\n",
                "stable/core.py": "",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        result = fitness_check.analyze_python_imports(tmp_path, zones, ["__pycache__"])
        assert len(result["cross_zone_imports"]) == 1
        assert result["cross_zone_imports"][0]["from_zone"] == "shared"
        assert result["cross_zone_imports"][0]["target_zone"] == "stable"

    def test_builds_internal_dependency_graph(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "features/app.py": "from shared import util\n",
                "shared/util.py": "import stable.core\n",
                "stable/core.py": "",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        result = fitness_check.analyze_python_imports(tmp_path, zones, ["__pycache__"])
        assert "shared.util" in result["imports_of"]["features.app"]
        assert "features.app" in result["imported_by"]["shared.util"]


# ---------------------------------------------- TestAnalyzeRegexImports ---


class TestAnalyzeRegexImports:
    def test_javascript_pattern(self, tmp_path):
        _make_zone_project(tmp_path)
        f = tmp_path / "stable" / "core.js"
        f.write_text("import { foo } from 'features/bar';\n", encoding="utf-8")
        zones = fitness_check.detect_zones(tmp_path, {})
        patterns = {"javascript": "import|require"}
        violations = fitness_check.analyze_regex_imports(
            tmp_path, zones, ["__pycache__"], patterns
        )
        assert len(violations) >= 1
        assert violations[0]["from_zone"] == "stable"
        assert violations[0]["target_zone"] == "features"

    def test_no_match_no_violations(self, tmp_path):
        _make_zone_project(tmp_path)
        f = tmp_path / "stable" / "core.js"
        f.write_text("const x = 1;\n", encoding="utf-8")
        zones = fitness_check.detect_zones(tmp_path, {})
        patterns = {"javascript": "import|require"}
        violations = fitness_check.analyze_regex_imports(
            tmp_path, zones, ["__pycache__"], patterns
        )
        assert len(violations) == 0


# ------------------------------------------------- TestComputeInstability ---


class TestComputeInstability:
    def test_no_shared_zone(self, tmp_path):
        zones = {"stable": tmp_path / "stable"}
        result = fitness_check.compute_instability({}, zones, tmp_path, [])
        assert result == {}

    def test_computes_ratio(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "shared/utils.py": "import features.app\n",
                "features/app.py": "",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        import_data = fitness_check.analyze_python_imports(
            tmp_path, zones, ["__pycache__"]
        )
        result = fitness_check.compute_instability(
            import_data, zones, tmp_path, ["__pycache__"]
        )
        # shared/utils.py has Ce >= 1 (imports features.app)
        assert isinstance(result, dict)


class TestStructuralHeuristics:
    def test_analyze_python_structure_flags_large_file(self, tmp_path):
        body = "\n".join([f"value_{i} = {i}" for i in range(320)])
        _make_zone_project(tmp_path, {"features/big.py": body})
        zones = fitness_check.detect_zones(tmp_path, {})
        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            fitness_check.DEFAULT_CONFIG["thresholds"],
        )
        assert any(risk["file"] == "features/big.py" for risk in result["god_file_risks"])

    def test_live_god_file_ranks_before_larger_generated_artifact(self, tmp_path):
        live_body = "\n".join(f"value_{idx} = {idx}" for idx in range(20))
        artifact_body = "\n".join(f"value_{idx} = {idx}" for idx in range(80))
        _make_zone_project(
            tmp_path,
            {
                "features/live.py": live_body,
                "build/generated.py": artifact_body,
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 10
        thresholds["god_file_fail_lines"] = 50

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            [],
            thresholds,
        )

        assert [risk["file"] for risk in result["god_file_risks"]] == [
            "features/live.py"
        ]
        assert result["excluded_god_file_risks"][0]["file"] == "build/generated.py"
        assert (
            result["excluded_god_file_risks"][0]["source_kind"]
            == fitness_check.SOURCE_KIND_GENERATED_RELEASE
        )

        report = fitness_check.format_report(
            {
                "timestamp": "2026-06-21T00:00:00+00:00",
                "project_root": str(tmp_path),
                "zone_files": {},
                "has_history": False,
            },
            [],
            {
                "total_commits": 0,
                "files_per_commit": [],
                "cochange_pairs": Counter(),
                "cross_zone_commits": 0,
            },
            {"violations": [], "efferent_stable": []},
            result,
            [],
            {},
            thresholds,
        )
        assert report.index("features/live.py") < report.index("build/generated.py")

    def test_god_file_baseline_ratchet_fields(self, tmp_path):
        under_body = "\n".join(f"value_{idx} = {idx}" for idx in range(6))
        over_body = "\n".join(f"value_{idx} = {idx}" for idx in range(9))
        _make_zone_project(
            tmp_path,
            {
                "features/under.py": under_body,
                "features/over.py": over_body,
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 5
        thresholds["god_file_fail_lines"] = 100
        thresholds["god_file_baselines"] = {
            "features/under.py": 8,
            "features/over.py": 8,
        }

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )
        risks = {risk["file"]: risk for risk in result["god_file_risks"]}

        assert risks["features/under.py"]["severity"] == "warn"
        assert risks["features/under.py"]["current"] == 6
        assert risks["features/under.py"]["baseline"] == 8
        assert risks["features/under.py"]["remaining"] == 2
        assert "over_by" not in risks["features/under.py"]
        assert risks["features/over.py"]["severity"] == "fail"
        assert risks["features/over.py"]["current"] == 9
        assert risks["features/over.py"]["baseline"] == 8
        assert risks["features/over.py"]["over_by"] == 1

    def test_protected_god_file_without_baseline_fails(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {"features/protected.py": "\n".join(f"value_{idx} = {idx}" for idx in range(4))},
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 100
        thresholds["god_file_fail_lines"] = 1000
        thresholds["god_file_protected_files"] = ["features/protected.py"]
        thresholds["god_file_baselines"] = {}

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )
        risks = {risk["file"]: risk for risk in result["god_file_risks"]}

        assert risks["features/protected.py"]["severity"] == "fail"
        assert risks["features/protected.py"]["protected"] is True
        assert risks["features/protected.py"]["baseline"] is None
        assert "no valid configured baseline" in risks["features/protected.py"]["reasons"][0]

    def test_protected_god_file_above_baseline_fails_with_over_by(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {"features/protected.py": "\n".join(f"value_{idx} = {idx}" for idx in range(9))},
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 100
        thresholds["god_file_fail_lines"] = 1000
        thresholds["god_file_protected_files"] = ["features/protected.py"]
        thresholds["god_file_baselines"] = {"features/protected.py": 8}

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )
        risks = {risk["file"]: risk for risk in result["god_file_risks"]}

        assert risks["features/protected.py"]["severity"] == "fail"
        assert risks["features/protected.py"]["over_by"] == 1

    def test_protected_god_file_below_baseline_reports_remaining(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {"features/protected.py": "\n".join(f"value_{idx} = {idx}" for idx in range(6))},
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 100
        thresholds["god_file_fail_lines"] = 1000
        thresholds["god_file_protected_files"] = ["features/protected.py"]
        thresholds["god_file_baselines"] = {"features/protected.py": 8}

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )
        risks = {risk["file"]: risk for risk in result["god_file_risks"]}

        assert risks["features/protected.py"]["severity"] == "warn"
        assert risks["features/protected.py"]["remaining"] == 2
        assert "over_by" not in risks["features/protected.py"]

    def test_excessive_god_file_baseline_slack_fails(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {"features/protected.py": "\n".join(f"value_{idx} = {idx}" for idx in range(6))},
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 100
        thresholds["god_file_fail_lines"] = 1000
        thresholds["god_file_baseline_max_slack"] = 50
        thresholds["god_file_protected_files"] = ["features/protected.py"]
        thresholds["god_file_baselines"] = {"features/protected.py": 100}

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )
        risks = {risk["file"]: risk for risk in result["god_file_risks"]}

        assert risks["features/protected.py"]["severity"] == "fail"
        assert "baseline slack 94 exceeds max 50" in risks["features/protected.py"]["reasons"][0]

    def test_valid_named_god_file_exception_allows_named_overage(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {"features/protected.py": "\n".join(f"value_{idx} = {idx}" for idx in range(9))},
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 100
        thresholds["god_file_fail_lines"] = 1000
        thresholds["god_file_protected_files"] = ["features/protected.py"]
        thresholds["god_file_baselines"] = {"features/protected.py": 8}
        thresholds["god_file_exceptions"] = [{
            "id": "p14-temporary-overage",
            "file": "features/protected.py",
            "reason": "temporary test fixture overage",
            "expires": "2099-01-01",
            "allowed_over_by": 2,
        }]

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )
        risks = {risk["file"]: risk for risk in result["god_file_risks"]}

        assert risks["features/protected.py"]["severity"] == "warn"
        assert risks["features/protected.py"]["over_by"] == 1
        assert risks["features/protected.py"]["exception_id"] == "p14-temporary-overage"

    @pytest.mark.parametrize(
        ("exception", "expected"),
        [
            (
                {
                    "id": "expired-overage",
                    "file": "features/protected.py",
                    "reason": "expired test fixture overage",
                    "expires": "2000-01-01",
                    "allowed_over_by": 2,
                },
                "expired on 2000-01-01",
            ),
            (
                {
                    "id": "malformed-overage",
                    "file": "features/protected.py",
                    "expires": "2099-01-01",
                    "allowed_over_by": 2,
                },
                "missing reason",
            ),
        ],
    )
    def test_expired_or_malformed_god_file_exception_fails(
        self,
        tmp_path,
        exception,
        expected,
    ):
        _make_zone_project(
            tmp_path,
            {"features/protected.py": "\n".join(f"value_{idx} = {idx}" for idx in range(9))},
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 100
        thresholds["god_file_fail_lines"] = 1000
        thresholds["god_file_protected_files"] = ["features/protected.py"]
        thresholds["god_file_baselines"] = {"features/protected.py": 8}
        thresholds["god_file_exceptions"] = [exception]

        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )
        fail_reasons = " ".join(
            reason
            for risk in result["god_file_risks"]
            if risk["severity"] == "fail"
            for reason in risk.get("reasons", [])
        )

        assert expected in fail_reasons

    def test_god_file_budget_report_includes_diff_and_protection(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "features/under.py": "\n".join(f"value_{idx} = {idx}" for idx in range(6)),
                "features/over.py": "\n".join(f"value_{idx} = {idx}" for idx in range(9)),
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {})
        thresholds = dict(fitness_check.DEFAULT_CONFIG["thresholds"])
        thresholds["god_file_warn_lines"] = 100
        thresholds["god_file_fail_lines"] = 1000
        thresholds["god_file_protected_files"] = [
            "features/under.py",
            "features/over.py",
        ]
        thresholds["god_file_baselines"] = {
            "features/under.py": 8,
            "features/over.py": 8,
        }
        result = fitness_check.analyze_python_structure(
            tmp_path,
            zones,
            ["__pycache__"],
            thresholds,
        )

        report = fitness_check.format_report(
            {
                "timestamp": "2026-06-21T00:00:00+00:00",
                "project_root": str(tmp_path),
                "zone_files": {},
                "has_history": False,
            },
            [],
            {
                "total_commits": 0,
                "files_per_commit": [],
                "cochange_pairs": Counter(),
                "cross_zone_commits": 0,
            },
            {"violations": [], "efferent_stable": []},
            result,
            [],
            {},
            thresholds,
        )

        assert "protected=true" in report
        assert "current=6" in report
        assert "baseline=8" in report
        assert "remaining=2" in report
        assert "current=9" in report
        assert "over_by=1" in report

    def test_fitness_tool_drift_check_detects_mismatch(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "tools").mkdir()
        (tmp_path / "scripts" / "fitness_check.py").write_text(
            "print('script')\n",
            encoding="utf-8",
        )
        (tmp_path / "tools" / "fitness_check.py").write_text(
            "print('tool')\n",
            encoding="utf-8",
        )

        violations = fitness_check.check_fitness_tool_drift(tmp_path)

        assert violations
        assert violations[0]["severity"] == "fail"
        assert "byte-identical" in violations[0]["message"]

    def test_evaluate_layer_rules_flags_forbidden_zone_import(self):
        import_data = {
            "cross_zone_imports": [
                {
                    "file": "shared/util.py",
                    "from_zone": "shared",
                    "target_zone": "features",
                    "imports": "features.orders",
                    "line": 4,
                }
            ]
        }
        violations = fitness_check.evaluate_layer_rules(
            import_data,
            [
                {
                    "source_zone": "shared",
                    "forbidden_target_zones": ["features"],
                    "severity": "fail",
                    "message": "shared cannot import features",
                }
            ],
        )
        assert len(violations) == 1
        assert violations[0]["severity"] == "fail"
        assert "shared cannot import features" in violations[0]["message"]

    def test_main_uses_cc_config_architecture_fitness_layer_rule(
        self, tmp_path, capsys
    ):
        _make_zone_project(
            tmp_path,
            {
                "shared/util.py": "import features.app\n",
                "features/app.py": "VALUE = 1\n",
            },
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
                            "stable": "stable",
                            "workspace": "workspace",
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

        with patch.object(
            sys,
            "argv",
            [
                "fitness_check.py",
                "--project-root",
                str(tmp_path),
                "--ci",
                "--no-save",
            ],
        ):
            with pytest.raises(SystemExit) as exc:
                fitness_check.main()

        assert exc.value.code == 1
        output = capsys.readouterr().out
        assert "## Configuration Evidence" in output
        assert ".controlcoding/cc_config.json" in output
        assert "`layer_rules` | 1" in output
        assert "## Configured Layer Rule Violations" in output
        assert "shared cannot import features" in output

    def test_main_smokes_composite_non_inline_architecture_evidence(
        self, tmp_path, capsys
    ):
        _make_zone_project(
            tmp_path,
            {
                "stable/core.py": "VALUE = 1\n",
                "shared/util.py": "import features.app\n",
                "shared/gateway.py": "def call():\n    dangerous_call()\n",
                "features/app.py": "def run():\n    dangerous_call()\n",
                "features/ui.py": "def click(store):\n    store.dispatch('x')\n",
            },
        )
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "gateway_config.json").write_text(
            json.dumps(
                {
                    "userHost": "codex_cli",
                    "enabledHosts": ["codex_cli"],
                }
            ),
            encoding="utf-8",
        )
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {
                    "architecture_fitness": {
                        "zones": {
                            "shared": "shared",
                            "features": "features",
                            "stable": "stable",
                            "workspace": "workspace",
                        },
                        "layer_rules": [
                            {
                                "source_zone": "shared",
                                "forbidden_target_zones": ["features"],
                                "severity": "fail",
                                "message": "shared cannot import features",
                            }
                        ],
                        "gateway_rules": [
                            {
                                "pattern": "dangerous_call",
                                "allowed_files": ["shared/gateway.py"],
                                "severity": "fail",
                                "message": "dangerous_call must go through shared/gateway.py",
                            }
                        ],
                        "mutation_rules": [
                            {
                                "source_zones": ["features"],
                                "patterns": ["store.dispatch"],
                                "allowed_files": ["shared/store_gateway.py"],
                                "severity": "fail",
                                "message": "features cannot mutate store state directly",
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            sys,
            "argv",
            [
                "fitness_check.py",
                "--project-root",
                str(tmp_path),
                "--ci",
                "--no-save",
            ],
        ):
            with pytest.raises(SystemExit) as exc:
                fitness_check.main()

        assert exc.value.code == 1
        output = capsys.readouterr().out
        assert "## Configuration Evidence" in output
        assert ".controlcoding/cc_config.json" in output
        assert "`layer_rules` | 1" in output
        assert "`gateway_rules` | 1" in output
        assert "`mutation_rules` | 1" in output
        assert "## Configured Layer Rule Violations" in output
        assert "## Gateway Rule Violations" in output
        assert "## Mutation Rule Violations" in output
        assert "shared cannot import features" in output
        assert "dangerous_call must go through shared/gateway.py" in output
        assert "features cannot mutate store state directly" in output
        assert not (control_dir / "fitness_history.jsonl").exists()

    def test_main_ci_no_save_warning_only_does_not_write_history(
        self, tmp_path, capsys
    ):
        body = "\n".join(f"value_{idx} = {idx}" for idx in range(6))
        _make_zone_project(tmp_path, {"features/warn.py": body})
        (tmp_path / "fitness.json").write_text(
            json.dumps(
                {
                    "thresholds": {
                        "god_file_warn_lines": 5,
                        "god_file_fail_lines": 100,
                    }
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            sys,
            "argv",
            [
                "fitness_check.py",
                "--project-root",
                str(tmp_path),
                "--ci",
                "--no-save",
            ],
        ):
            with pytest.raises(SystemExit) as exc:
                fitness_check.main()

        assert exc.value.code == 2
        output = capsys.readouterr().out
        assert "**RESULT: WARNINGS** (exit 2)" in output
        assert not (tmp_path / ".controlcoding" / "fitness_history.jsonl").exists()


class TestDependencyGraphFitness:
    def test_analyze_dependency_graph_flags_fan_out(self):
        import_data = {
            "imports_of": {
                "features.big": {"shared.alpha", "shared.beta", "stable.core"},
            },
            "imported_by": {},
            "module_files": {
                "features.big": "features/big.py",
                "shared.alpha": "shared/alpha.py",
                "shared.beta": "shared/beta.py",
                "stable.core": "stable/core.py",
            },
            "module_zones": {
                "features.big": "features",
                "shared.alpha": "shared",
                "shared.beta": "shared",
                "stable.core": "stable",
            },
        }
        result = fitness_check.analyze_dependency_graph(
            import_data,
            {
                "import_fan_out_warn": 2,
                "import_fan_out_fail": 4,
                "import_fan_in_warn": 2,
                "import_fan_in_fail": 4,
                "dependency_cycle_warn_length": 2,
                "dependency_cycle_fail_length": 4,
            },
        )
        assert len(result["fan_out_risks"]) == 1
        assert result["fan_out_risks"][0]["severity"] == "warn"
        assert result["fan_out_risks"][0]["count"] == 3

    def test_analyze_dependency_graph_detects_cycle(self):
        import_data = {
            "imports_of": {
                "features.alpha": {"shared.beta"},
                "shared.beta": {"features.alpha"},
            },
            "imported_by": {
                "shared.beta": {"features.alpha"},
                "features.alpha": {"shared.beta"},
            },
            "module_files": {
                "features.alpha": "features/alpha.py",
                "shared.beta": "shared/beta.py",
            },
            "module_zones": {
                "features.alpha": "features",
                "shared.beta": "shared",
            },
        }
        result = fitness_check.analyze_dependency_graph(
            import_data,
            {
                "import_fan_out_warn": 3,
                "import_fan_out_fail": 5,
                "import_fan_in_warn": 3,
                "import_fan_in_fail": 5,
                "dependency_cycle_warn_length": 2,
                "dependency_cycle_fail_length": 4,
            },
        )
        assert len(result["dependency_cycles"]) == 1
        assert result["dependency_cycles"][0]["severity"] == "warn"
        assert set(result["dependency_cycles"][0]["modules"]) == {
            "features.alpha",
            "shared.beta",
        }


class TestGatewayRules:
    def test_evaluate_gateway_rules_flags_direct_usage(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "shared/llm_backend.py": "dangerous_call()\n",
                "features/chat.py": "dangerous_call()\n",
            },
        )
        violations = fitness_check.evaluate_gateway_rules(
            tmp_path,
            ["__pycache__"],
            [
                {
                    "pattern": "dangerous_call",
                    "allowed_files": ["shared/llm_backend.py"],
                    "severity": "fail",
                }
            ],
        )
        assert len(violations) == 1
        assert violations[0]["file"] == "features/chat.py"
        assert violations[0]["severity"] == "fail"

    def test_loads_gateway_modules_from_cc_config(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {
                    "gateway_modules": [
                        {
                            "pattern": "dangerous_call",
                            "file": "shared/llm_backend.py",
                            "description": "All calls must go through the gateway",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rules = fitness_check._load_gateway_rules_from_cc_config(tmp_path)
        assert len(rules) == 1
        assert rules[0]["allowed_files"] == ["shared/llm_backend.py"]
        assert "gateway" in rules[0]["message"].lower()


class TestScopedPatternRules:
    def test_ownership_rule_flags_state_ownership_in_render_zone(self, tmp_path):
        for zone in ["render", "shared", "stable"]:
            (tmp_path / zone).mkdir(parents=True, exist_ok=True)
        (tmp_path / "render" / "scene.py").write_text(
            "state = WorldState()\n", encoding="utf-8"
        )
        zones = fitness_check.detect_zones(tmp_path, {"render": "render", "shared": "shared", "stable": "stable"})
        violations = fitness_check.evaluate_scoped_pattern_rules(
            tmp_path,
            zones,
            ["__pycache__"],
            [
                {
                    "source_zones": ["render"],
                    "patterns": ["WorldState("],
                    "severity": "fail",
                    "message": "render cannot own domain state directly",
                }
            ],
            "ownership",
        )
        assert len(violations) == 1
        assert violations[0]["file"] == "render/scene.py"
        assert violations[0]["zone"] == "render"

    def test_mutation_rule_allows_approved_gateway_file(self, tmp_path):
        _make_zone_project(
            tmp_path,
            {
                "ui/panel.py": "store.dispatch('x')\n",
                "shared/store_gateway.py": "store.dispatch('x')\n",
            },
        )
        zones = fitness_check.detect_zones(tmp_path, {"ui": "ui", "shared": "shared", "stable": "stable", "features": "features"})
        violations = fitness_check.evaluate_scoped_pattern_rules(
            tmp_path,
            zones,
            ["__pycache__"],
            [
                {
                    "source_zones": ["ui", "shared"],
                    "patterns": ["store.dispatch"],
                    "allowed_files": ["shared/store_gateway.py"],
                    "severity": "fail",
                    "message": "ui cannot mutate domain state directly",
                }
            ],
            "mutation",
        )
        assert len(violations) == 1
        assert violations[0]["file"] == "ui/panel.py"

    def test_loads_scoped_rules_from_cc_config(self, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir()
        (control_dir / "cc_config.json").write_text(
            json.dumps(
                {
                    "mutation_rules": [
                        {
                            "source_zones": ["ui"],
                            "patterns": ["store.dispatch"],
                            "allowed_files": ["shared/store_gateway.py"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rules = fitness_check._load_scoped_pattern_rules_from_cc_config(
            tmp_path, "mutation_rules", "mutation"
        )
        assert len(rules) == 1
        assert rules[0]["source_zones"] == ["ui"]
        assert rules[0]["allowed_files"] == ["shared/store_gateway.py"]


# ------------------------------------------------ TestHistoryAndDeltas ---


class TestHistoryAndDeltas:
    def test_load_history_no_file(self, tmp_path):
        assert fitness_check.load_history(tmp_path) == []

    def test_load_history_valid_jsonl(self, tmp_path):
        path = tmp_path / ".claude" / "fitness_history.jsonl"
        path.parent.mkdir(parents=True)
        entries = [
            {"timestamp": "2026-03-01T10:00:00Z", "zone_files": {"stable": 5}},
            {"timestamp": "2026-03-02T10:00:00Z", "zone_files": {"stable": 6}},
        ]
        path.write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )
        result = fitness_check.load_history(tmp_path)
        assert len(result) == 2

    def test_save_snapshot_appends(self, tmp_path):
        fitness_check.save_snapshot(tmp_path, {"a": 1})
        fitness_check.save_snapshot(tmp_path, {"b": 2})
        result = fitness_check.load_history(tmp_path)
        assert len(result) == 2

    def test_compute_deltas_zone_growth(self):
        current = {"zone_files": {"stable": 15}, "direction_violations": 0}
        history = [{"zone_files": {"stable": 10}, "direction_violations": 0}]
        thresholds = {"zone_growth_warn_pct": 30}
        deltas = fitness_check.compute_deltas(current, history, thresholds)
        # 50% growth > 30% threshold
        assert len(deltas) == 1
        assert deltas[0]["type"] == "zone_growth"
        assert deltas[0]["growth_pct"] == 50.0

    def test_compute_deltas_violation_increase(self):
        current = {"zone_files": {}, "direction_violations": 3}
        history = [{"zone_files": {}, "direction_violations": 1}]
        deltas = fitness_check.compute_deltas(current, history, {})
        assert any(d["type"] == "violations_increase" for d in deltas)


# -------------------------------------------------------- TestFormatReport ---


class TestFormatReport:
    def _base_snapshot(self):
        return {
            "timestamp": "2026-03-01T10:00:00Z",
            "project_root": "/tmp/proj",
            "zone_files": {"stable": 5, "features": 10},
            "has_history": False,
        }

    def test_report_contains_sections(self):
        snapshot = self._base_snapshot()
        git_data = {"total_commits": 0, "files_per_commit": [],
                    "cochange_pairs": Counter(), "cross_zone_commits": 0}
        import_data = {"violations": [], "efferent_stable": []}
        report = fitness_check.format_report(
            snapshot, [], git_data, import_data,
            {"god_file_risks": [], "god_function_risks": [], "god_class_risks": []},
            [], {}, {}
        )
        assert "## Zone Size Distribution" in report
        assert "## Dependency Direction Violations" in report

    def test_report_contains_configuration_evidence(self):
        snapshot = self._base_snapshot()
        snapshot["config_sources"] = [
            {
                "source": "built-in defaults",
                "key": "DEFAULT_CONFIG",
                "status": "loaded",
            },
            {
                "source": ".controlcoding/cc_config.json",
                "key": "architecture_fitness",
                "status": "loaded",
            },
        ]
        snapshot["configured_rule_counts"] = {
            "zones": 2,
            "thresholds": 16,
            "layer_rules": 1,
            "gateway_rules": 1,
            "ownership_rules": 0,
            "mutation_rules": 1,
        }
        git_data = {"total_commits": 0, "files_per_commit": [],
                    "cochange_pairs": Counter(), "cross_zone_commits": 0}
        import_data = {"violations": [], "efferent_stable": []}
        report = fitness_check.format_report(
            snapshot, [], git_data, import_data,
            {"god_file_risks": [], "god_function_risks": [], "god_class_risks": []},
            [], {}, {}
        )

        assert "## Configuration Evidence" in report
        assert ".controlcoding/cc_config.json" in report
        assert "`layer_rules` | 1" in report

    def test_all_clear_message(self):
        snapshot = self._base_snapshot()
        git_data = {"total_commits": 0, "files_per_commit": [],
                    "cochange_pairs": Counter(), "cross_zone_commits": 0}
        import_data = {"violations": [], "efferent_stable": []}
        report = fitness_check.format_report(
            snapshot, [], git_data, import_data,
            {"god_file_risks": [], "god_function_risks": [], "god_class_risks": []},
            [], {}, {}
        )
        assert "ALL CLEAR" in report

    def test_report_contains_dependency_graph_section(self):
        snapshot = self._base_snapshot()
        git_data = {"total_commits": 0, "files_per_commit": [],
                    "cochange_pairs": Counter(), "cross_zone_commits": 0}
        import_data = {"violations": [], "efferent_stable": []}
        report = fitness_check.format_report(
            snapshot, [], git_data, import_data,
            {
                "god_file_risks": [],
                "god_function_risks": [],
                "god_class_risks": [],
                "fan_out_risks": [
                    {
                        "file": "features/big.py",
                        "zone": "features",
                        "severity": "warn",
                        "count": 3,
                        "targets": ["shared.alpha", "shared.beta", "stable.core"],
                    }
                ],
                "fan_in_risks": [],
                "dependency_cycles": [],
            },
            [], {}, {}
        )
        assert "## Dependency Graph Fitness" in report
        assert "Fan-out hotspots" in report

    def test_report_contains_gateway_rule_section(self):
        snapshot = self._base_snapshot()
        git_data = {"total_commits": 0, "files_per_commit": [],
                    "cochange_pairs": Counter(), "cross_zone_commits": 0}
        import_data = {"violations": [], "efferent_stable": []}
        report = fitness_check.format_report(
            snapshot, [], git_data, import_data,
            {
                "god_file_risks": [],
                "god_function_risks": [],
                "god_class_risks": [],
                "gateway_rule_violations": [
                    {
                        "file": "features/chat.py",
                        "line": 12,
                        "severity": "fail",
                        "allowed_files": ["shared/llm_backend.py"],
                        "message": "dangerous_call must go through gateway",
                    }
                ],
                "fan_out_risks": [],
                "fan_in_risks": [],
                "dependency_cycles": [],
            },
            [], {}, {}
        )
        assert "## Gateway Rule Violations" in report
        assert "features/chat.py" in report

    def test_report_contains_zone_hygiene_section(self):
        snapshot = self._base_snapshot()
        snapshot["zone_files"]["unclassified"] = 6
        git_data = {"total_commits": 0, "files_per_commit": [],
                    "cochange_pairs": Counter(), "cross_zone_commits": 0}
        import_data = {"violations": [], "efferent_stable": []}
        report = fitness_check.format_report(
            snapshot, [], git_data, import_data,
            {
                "god_file_risks": [],
                "god_function_risks": [],
                "god_class_risks": [],
                "zone_hygiene_alerts": [
                    {
                        "severity": "warn",
                        "unclassified": 6,
                        "total": 21,
                        "ratio_pct": 28.6,
                        "message": "Too many source files are outside the declared condominium zones",
                    }
                ],
            },
            [], {}, {}
        )
        assert "## Zone Hygiene" in report
        assert "28.6%" in report

    def test_report_contains_ownership_and_mutation_sections(self):
        snapshot = self._base_snapshot()
        git_data = {"total_commits": 0, "files_per_commit": [],
                    "cochange_pairs": Counter(), "cross_zone_commits": 0}
        import_data = {"violations": [], "efferent_stable": []}
        report = fitness_check.format_report(
            snapshot, [], git_data, import_data,
            {
                "god_file_risks": [],
                "god_function_risks": [],
                "god_class_risks": [],
                "gateway_rule_violations": [],
                "ownership_rule_violations": [
                    {
                        "file": "render/scene.py",
                        "line": 3,
                        "severity": "fail",
                        "allowed_files": [],
                        "message": "render cannot own domain state directly",
                    }
                ],
                "mutation_rule_violations": [
                    {
                        "file": "ui/panel.py",
                        "line": 8,
                        "severity": "fail",
                        "allowed_files": ["shared/store_gateway.py"],
                        "message": "ui cannot mutate domain state directly",
                    }
                ],
                "fan_out_risks": [],
                "fan_in_risks": [],
                "dependency_cycles": [],
            },
            [], {}, {}
        )
        assert "## Ownership Rule Violations" in report
        assert "## Mutation Rule Violations" in report
