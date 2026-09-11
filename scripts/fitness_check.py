#!/usr/bin/env python3
"""fitness_check.py - ControlCoding Architectural Fitness Functions.

Answers: "is the architecture degrading?" (different from invariant tests
which answer "does the system work?").

Metrics:
  1. Zone size distribution (file count per condominium zone)
  2. Files modified per commit (flags shotgun surgery)
  3. Co-change coupling across zones (files that always change together)
  4. Dependency direction violations (imports going the wrong way)
  5. Gateway-only call rules (configured or imported from cc_config gateway_modules)
  6. Dependency graph risks (fan-in, fan-out, import cycles)
  7. Temporal deltas (comparison with historical snapshots)

Language support:
  - Language-agnostic metrics: git + filesystem (always available)
  - Python imports: built-in via ast module (zero dependencies)
  - Other languages: regex-based, user-configured via cc_config or fitness.json

Usage:
    python fitness_check.py [--project-root PATH] [--ci] [--delta-only]
    python fitness_check.py [--project-root PATH] --wiring-matrix

Output:
  - Human-readable report on stdout (markdown)
  - JSONL record appended to .controlcoding/fitness_history.jsonl
  - Exit 0 = all clear, 1 = hard violations, 2 = warnings only

Zero external dependencies (stdlib only).
"""

import argparse
import copy
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# DEFAULT CONFIGURATION
# ============================================================

DEFAULT_CONFIG = {
    "zones": {},  # empty = auto-detect
    "import_patterns": {
        "python": "auto",  # uses ast module
        "cpp": "auto",
        "c": "auto",
    },
    "thresholds": {
        "files_per_commit_max": 5,
        "cochange_warn_threshold": 3,
        "zone_growth_warn_pct": 30,
        "unclassified_warn_pct": 20,
        "unclassified_fail_pct": 45,
        "instability_shared_max": 0.3,
        "god_file_warn_lines": 300,
        "god_file_fail_lines": 500,
        "god_file_baseline_max_slack": 50,
        "god_file_protected_files": [],
        "god_file_baselines": {},
        "god_file_exceptions": [],
        "god_function_warn_lines": 80,
        "god_class_warn_lines": 200,
        "god_class_warn_methods": 15,
        "god_file_warn_imports": 20,
        "import_fan_out_warn": 10,
        "import_fan_out_fail": 20,
        "import_fan_in_warn": 10,
        "import_fan_in_fail": 20,
        "dependency_cycle_warn_length": 2,
        "dependency_cycle_fail_length": 4,
    },
    "layer_rules": [],
    "gateway_rules": [],
    "ownership_rules": [],
    "mutation_rules": [],
    "git_history_commits": 100,
    "skip_dirs": [
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "dist", "dist-electron", "build", "target", "out", "bin", "obj",
        ".controlcoding", ".controlwork", ".claude", ".bridge",
        "devlog", ".tox", ".mypy_cache",
        ".pytest_cache", ".pytest-tmp", ".pytest-tmp-*",
        ".tmp", ".tmp_*", ".tmp-*", ".tmp_pytest*",
        "pytest-cache-files-*", "pytest_tmp*",
        "ui/dist", "ui/dist-electron", "ui/release",
        "release-bundle*", "release_bundle*", "release-bundles", "release_bundles",
        "scratch-test",
    ],
}

ALWAYS_SKIP_DIR_PATTERNS = (
    ".git",
    "__pycache__",
    ".controlcoding",
    ".controlwork",
    ".pytest_cache",
    ".pytest-tmp",
    ".pytest-tmp-*",
    ".tmp",
    ".tmp_*",
    ".tmp-*",
    ".tmp_pytest*",
    "pytest-cache-files-*",
    "pytest_tmp*",
    "ui/dist",
    "ui/dist-electron",
    "ui/release",
    "scratch-test",
)

SOURCE_KIND_LIVE_SOURCE = "live_source"
SOURCE_KIND_EXCLUDED_ARTIFACT = "excluded_artifact"
SOURCE_KIND_GENERATED_RELEASE = "generated_release"
SOURCE_KIND_TEMP_WORKSPACE = "temp_workspace"
SOURCE_KIND_TOOL_COPY = "tool_copy"

SOURCE_KIND_SORT_ORDER = {
    SOURCE_KIND_LIVE_SOURCE: 0,
    SOURCE_KIND_GENERATED_RELEASE: 1,
    SOURCE_KIND_TEMP_WORKSPACE: 2,
    SOURCE_KIND_TOOL_COPY: 3,
    SOURCE_KIND_EXCLUDED_ARTIFACT: 4,
}

TEMP_WORKSPACE_PATTERNS = (
    ".controlcoding",
    ".controlwork",
    ".claude",
    ".bridge",
    ".pytest_cache",
    ".pytest-tmp",
    ".pytest-tmp-*",
    ".tmp",
    ".tmp_*",
    ".tmp-*",
    ".tmp_pytest*",
    "pytest-cache-files-*",
    "pytest_tmp*",
    "scratch-test",
)

GENERATED_RELEASE_PATTERNS = (
    "dist",
    "dist-electron",
    "build",
    "out",
    "ui/dist",
    "ui/dist-electron",
    "ui/release",
    "release-bundle*",
    "release_bundle*",
    "release-bundles",
    "release_bundles",
)

TOOL_COPY_PATTERNS = (
    "tools",
    "tools/*",
)

EXCLUDED_ARTIFACT_PATTERNS = (
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    "target",
    "bin",
    "obj",
    "devlog",
)

# Zone hierarchy: index = stability level (higher = more stable)
ZONE_ORDER = ["workspace", "features", "shared", "stable"]

HISTORY_FILE = ".controlcoding/fitness_history.jsonl"
LEGACY_HISTORY_FILE = ".claude/fitness_history.jsonl"
CC_CONFIG_FILE = ".controlcoding/cc_config.json"
LEGACY_CC_CONFIG_FILE = ".claude/cc_config.json"
DEFAULT_GATEWAY_FILE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".kt", ".kts",
}
CPP_INCLUDE_PATTERN = r'^\s*#\s*include\s*[<"]([^">]+)[">]'

# ============================================================
# UTILITIES
# ============================================================

def run_git(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(
            ["git"] + args, capture_output=True, text=True,
            cwd=str(cwd), timeout=30,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _skip_pattern_matches(root: Path, dirpath: str, dirname: str,
                          pattern: str) -> bool:
    pattern_ref = str(pattern).replace("\\", "/").strip("/")
    if fnmatch.fnmatchcase(dirname, pattern_ref):
        return True

    candidate = Path(dirpath) / dirname
    try:
        rel_ref = str(candidate.relative_to(root)).replace("\\", "/")
    except ValueError:
        try:
            rel_ref = str(
                candidate.resolve().relative_to(root.resolve())
            ).replace("\\", "/")
        except ValueError:
            rel_ref = dirname
    return fnmatch.fnmatchcase(rel_ref, pattern_ref)


def source_files(root: Path, skip_dirs: list[str]) -> list[Path]:
    """Collect all source files, skipping configured directories."""
    skip_patterns = [str(pattern) for pattern in skip_dirs]
    skip_patterns.extend(ALWAYS_SKIP_DIR_PATTERNS)
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname for dirname in dirnames
            if not any(
                _skip_pattern_matches(root, dirpath, dirname, pattern)
                for pattern in skip_patterns
            )
        ]
        for f in filenames:
            result.append(Path(dirpath) / f)
    return result


def _project_path_ref(root: Path, filepath: Path) -> str:
    try:
        rel = filepath.resolve().relative_to(root.resolve())
    except ValueError:
        try:
            rel = filepath.relative_to(root)
        except ValueError:
            rel = filepath
    return str(rel).replace("\\", "/").strip("/")


def _source_kind_pattern_matches(path_ref: str, pattern: str) -> bool:
    pattern_ref = str(pattern).replace("\\", "/").strip("/")
    if not pattern_ref:
        return False
    if path_ref == pattern_ref or path_ref.startswith(f"{pattern_ref}/"):
        return True
    if fnmatch.fnmatchcase(path_ref, pattern_ref):
        return True
    if "/" not in pattern_ref:
        parts = path_ref.split("/")
        return any(fnmatch.fnmatchcase(part, pattern_ref) for part in parts)
    return fnmatch.fnmatchcase(path_ref, f"{pattern_ref}/*")


def classify_source_kind(root: Path, filepath: Path) -> str:
    """Classify a project file for live-source fitness ranking."""
    path_ref = _project_path_ref(root, filepath)
    if any(
        _source_kind_pattern_matches(path_ref, pattern)
        for pattern in TEMP_WORKSPACE_PATTERNS
    ):
        return SOURCE_KIND_TEMP_WORKSPACE
    if any(
        _source_kind_pattern_matches(path_ref, pattern)
        for pattern in GENERATED_RELEASE_PATTERNS
    ):
        return SOURCE_KIND_GENERATED_RELEASE
    if any(
        _source_kind_pattern_matches(path_ref, pattern)
        for pattern in TOOL_COPY_PATTERNS
    ):
        return SOURCE_KIND_TOOL_COPY
    if any(
        _source_kind_pattern_matches(path_ref, pattern)
        for pattern in EXCLUDED_ARTIFACT_PATTERNS
    ):
        return SOURCE_KIND_EXCLUDED_ARTIFACT
    return SOURCE_KIND_LIVE_SOURCE


def _god_file_risk_sort_key(risk: dict) -> tuple:
    severity_rank = {"fail": 0, "warn": 1}.get(
        risk.get("severity", "warn"), 2
    )
    source_kind = risk.get("source_kind", SOURCE_KIND_LIVE_SOURCE)
    source_rank = SOURCE_KIND_SORT_ORDER.get(source_kind, 99)
    overage_rank = 0 if int(risk.get("over_by", 0) or 0) > 0 else 1
    logical_count = int(risk.get("current", risk.get("logical_lines", 0)) or 0)
    import_count = int(risk.get("imports", 0) or 0)
    return (
        source_rank,
        severity_rank,
        overage_rank,
        -logical_count,
        -import_count,
        risk.get("file", ""),
    )


def _config_path_ref(value: object) -> str:
    """Return a normalized project-relative path reference."""
    return str(value or "").replace("\\", "/").strip("/")


def _parse_positive_int(value: object) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _parse_nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _god_file_baseline_for(file_ref: str, baselines: dict) -> int | None:
    if file_ref not in baselines:
        return None
    return _parse_positive_int(baselines.get(file_ref))


def _god_file_policy_failure(file_ref: str, message: str,
                             current: int | None = None,
                             baseline: int | None = None,
                             protected: bool = False,
                             exception_id: str | None = None) -> dict:
    risk = {
        "file": file_ref,
        "zone": None,
        "severity": "fail",
        "source_kind": SOURCE_KIND_LIVE_SOURCE,
        "protected": protected,
        "reasons": [message],
    }
    if current is not None:
        risk["current"] = current
        risk["logical_lines"] = current
    if baseline is not None:
        risk["baseline"] = baseline
        if current is not None:
            if current > baseline:
                risk["over_by"] = current - baseline
            else:
                risk["remaining"] = baseline - current
    elif protected:
        risk["baseline"] = None
    if exception_id:
        risk["exception_id"] = exception_id
    return risk


def _validate_god_file_exception(entry: object, today) -> tuple[dict | None, dict | None]:
    if not isinstance(entry, dict):
        return None, _god_file_policy_failure(
            "<invalid>",
            "god_file_exceptions entries must be objects",
        )

    exception_id = str(entry.get("id") or "").strip()
    file_ref = _config_path_ref(entry.get("file"))
    reason = str(entry.get("reason") or "").strip()
    expires_ref = str(entry.get("expires") or "").strip()
    allowed_over_by = _parse_positive_int(entry.get("allowed_over_by"))
    errors: list[str] = []

    if not exception_id:
        errors.append("missing id")
    if not file_ref:
        errors.append("missing file")
    if not reason:
        errors.append("missing reason")
    if allowed_over_by is None:
        errors.append("missing or invalid allowed_over_by")

    expires_date = None
    if not expires_ref:
        errors.append("missing expires")
    else:
        try:
            expires_date = datetime.strptime(expires_ref, "%Y-%m-%d").date()
        except ValueError:
            errors.append("expires must use YYYY-MM-DD")
    if expires_date is not None and expires_date < today:
        errors.append(f"expired on {expires_ref}")

    if errors:
        return None, _god_file_policy_failure(
            file_ref or "<invalid>",
            f"malformed god-file exception"
            f"{f' {exception_id}' if exception_id else ''}: {', '.join(errors)}",
            protected=True,
            exception_id=exception_id or None,
        )

    return {
        "id": exception_id,
        "file": file_ref,
        "reason": reason,
        "expires": expires_ref,
        "allowed_over_by": allowed_over_by,
    }, None


def _god_file_exceptions_by_file(thresholds: dict) -> tuple[dict[str, dict], list[dict]]:
    today = datetime.now(timezone.utc).date()
    configured = thresholds.get("god_file_exceptions", [])
    if not isinstance(configured, list):
        return {}, [
            _god_file_policy_failure(
                "<invalid>",
                "god_file_exceptions must be a list",
            )
        ]

    by_file: dict[str, dict] = {}
    failures: list[dict] = []
    for entry in configured:
        parsed, failure = _validate_god_file_exception(entry, today)
        if failure is not None:
            failures.append(failure)
            continue
        assert parsed is not None
        file_ref = parsed["file"]
        if file_ref in by_file:
            failures.append(
                _god_file_policy_failure(
                    file_ref,
                    f"duplicate god-file exception for {file_ref}",
                    protected=True,
                    exception_id=parsed["id"],
                )
            )
            continue
        by_file[file_ref] = parsed
    return by_file, failures


def _apply_god_file_budget_fields(risk: dict, baseline: int | None,
                                  current: int, protected: bool) -> None:
    risk["protected"] = protected
    if baseline is None:
        if protected:
            risk["baseline"] = None
        return
    risk["baseline"] = baseline
    if current > baseline:
        risk["over_by"] = current - baseline
    else:
        risk["remaining"] = baseline - current


# ============================================================
# ZONE DETECTION
# ============================================================

def detect_zones(root: Path, config_zones: dict) -> dict[str, Path]:
    """Return mapping of zone_name -> absolute path.

    If config_zones is provided, use those. Otherwise auto-detect
    by looking for directories named stable/, shared/, features/,
    workspace/ (or src/stable/, src/shared/, etc.).
    """
    if config_zones:
        return {name: (root / path).resolve() for name, path in config_zones.items()}

    zones = {}
    candidates = [root, root / "src"]
    for base in candidates:
        if not base.is_dir():
            continue
        for zone_name in ZONE_ORDER:
            zone_dir = base / zone_name
            if zone_dir.is_dir() and zone_name not in zones:
                zones[zone_name] = zone_dir.resolve()

    return zones


def classify_file(filepath: Path, zones: dict[str, Path]) -> str | None:
    """Return the zone name a file belongs to, or None if outside all zones."""
    resolved = filepath.resolve()
    for name, zone_path in zones.items():
        try:
            resolved.relative_to(zone_path)
            return name
        except ValueError:
            continue
    return None


def _module_name_from_file(root: Path, filepath: Path) -> str | None:
    try:
        rel = filepath.relative_to(root)
    except ValueError:
        return None
    return ".".join(rel.with_suffix("").parts)


def _match_project_module(imported_module: str, project_modules: set[str]) -> str | None:
    parts = [part for part in imported_module.split(".") if part]
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in project_modules:
            return candidate
    return None


def _resolve_project_import_targets(node: ast.AST,
                                    project_modules: set[str]) -> list[str]:
    matches: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            match = _match_project_module(alias.name, project_modules)
            if match:
                matches.append(match)
    elif isinstance(node, ast.ImportFrom) and node.module:
        base_match = _match_project_module(node.module, project_modules)
        if base_match:
            matches.append(base_match)
        for alias in node.names:
            if alias.name == "*":
                continue
            qualified = f"{node.module}.{alias.name}"
            match = _match_project_module(qualified, project_modules)
            if match:
                matches.append(match)

    deduped: list[str] = []
    seen: set[str] = set()
    for match in matches:
        if match not in seen:
            deduped.append(match)
            seen.add(match)
    return deduped


# ============================================================
# METRIC 1: ZONE SIZE
# ============================================================

def count_zone_files(root: Path, zones: dict[str, Path],
                     skip_dirs: list[str]) -> dict[str, int]:
    """Count source files in each zone."""
    counts = {name: 0 for name in zones}
    counts["unclassified"] = 0

    for f in source_files(root, skip_dirs):
        zone = classify_file(f, zones)
        if zone:
            counts[zone] += 1
        else:
            counts["unclassified"] += 1

    return counts


# ============================================================
# METRIC 2 & 3: GIT ANALYSIS
# ============================================================

def analyze_git(root: Path, zones: dict[str, Path],
                max_commits: int) -> dict:
    """Analyze git history for files-per-commit and co-change coupling."""
    result = {
        "files_per_commit": [],      # list of (commit_hash, count, files)
        "cochange_pairs": Counter(), # (zone_a:file, zone_b:file) -> count
        "cross_zone_commits": 0,
        "total_commits": 0,
    }

    log = run_git(
        ["log", f"-{max_commits}", "--pretty=format:COMMIT:%H", "--name-only"],
        root,
    )
    if not log:
        return result

    current_hash = None
    current_files = []

    def process_commit():
        if not current_hash or not current_files:
            return
        result["total_commits"] += 1
        result["files_per_commit"].append(
            (current_hash[:8], len(current_files), current_files[:10])
        )

        # Classify files by zone
        zones_touched = defaultdict(list)
        for f in current_files:
            fpath = root / f
            zone = classify_file(fpath, zones)
            if zone:
                zones_touched[zone].append(f)

        if len(zones_touched) > 1:
            result["cross_zone_commits"] += 1

        # Co-change: pairs of files from different zones in same commit
        zone_names = sorted(zones_touched.keys())
        for i, z1 in enumerate(zone_names):
            for z2 in zone_names[i + 1:]:
                for f1 in zones_touched[z1]:
                    for f2 in zones_touched[z2]:
                        pair = tuple(sorted([f"{z1}:{f1}", f"{z2}:{f2}"]))
                        result["cochange_pairs"][pair] += 1

    for line in log.splitlines():
        if line.startswith("COMMIT:"):
            process_commit()
            current_hash = line[7:]
            current_files = []
        elif line.strip():
            current_files.append(line.strip())

    process_commit()  # last commit

    return result


# ============================================================
# METRIC 4: IMPORT / DEPENDENCY ANALYSIS
# ============================================================

def analyze_python_imports(root: Path, zones: dict[str, Path],
                           skip_dirs: list[str]) -> dict:
    """Analyze Python imports for dependency direction violations.

    Returns dict with violations list and coupling metrics.
    """
    result = {
        "violations": [],       # imports going wrong direction
        "efferent_stable": [],  # stable/ importing from less-stable zones
        "module_coupling": defaultdict(lambda: {"Ca": 0, "Ce": 0}),
        "cross_zone_imports": [],
        "imports_of": defaultdict(set),
        "imported_by": defaultdict(set),
        "module_files": {},
        "module_zones": {},
    }

    # Build mapping: module_name -> zone
    module_zones = {}
    py_files = [f for f in source_files(root, skip_dirs) if f.suffix == ".py"]
    project_modules: set[str] = set()

    for f in py_files:
        zone = classify_file(f, zones)
        if zone is None:
            continue
        module_name = _module_name_from_file(root, f)
        if not module_name:
            continue
        project_modules.add(module_name)
        module_zones[module_name] = zone

    # Analyze each Python file
    for f in py_files:
        zone = classify_file(f, zones)
        if zone is None:
            continue

        module_name = _module_name_from_file(root, f)
        if not module_name:
            continue

        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(f))
        except (SyntaxError, ValueError):
            continue

        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        file_ref = str(rel).replace("\\", "/")
        zone_level = ZONE_ORDER.index(zone) if zone in ZONE_ORDER else -1
        result["module_files"][module_name] = file_ref
        result["module_zones"][module_name] = zone

        for node in ast.walk(tree):
            target_modules = _resolve_project_import_targets(node, project_modules)
            if not target_modules:
                continue

            for target_module in target_modules:
                if target_module == module_name:
                    continue

                result["imports_of"][module_name].add(target_module)
                result["imported_by"][target_module].add(module_name)

                target_zone = module_zones.get(target_module)
                if target_zone is None or target_zone == zone:
                    continue

                target_level = (ZONE_ORDER.index(target_zone)
                               if target_zone in ZONE_ORDER else -1)
                edge = {
                    "file": file_ref,
                    "from_zone": zone,
                    "imports": target_module,
                    "target_module": target_module,
                    "target_zone": target_zone,
                    "line": getattr(node, "lineno", "?"),
                }
                result["cross_zone_imports"].append(edge)

                # Track coupling
                result["module_coupling"][file_ref]["Ce"] += 1

                # Dependency direction: can only import from MORE stable (higher level)
                # Violation: importing from LESS stable (lower level)
                if target_level < zone_level:
                    violation = dict(edge)
                    result["violations"].append(violation)

                    if zone == "stable":
                        result["efferent_stable"].append(violation)

    return result


def analyze_regex_imports(root: Path, zones: dict[str, Path],
                          skip_dirs: list[str],
                          patterns: dict[str, str]) -> list[dict]:
    """Regex-based import analysis for non-Python languages.

    patterns: {"javascript": "import|require", ...}
    Returns list of potential violations (approximate).
    """
    ext_map = {
        "javascript": [".js", ".mjs", ".cjs", ".jsx"],
        "typescript": [".ts", ".tsx"],
        "cpp": [".cpp", ".cc", ".cxx", ".h", ".hpp"],
        "c": [".c", ".h"],
        "java": [".java"],
        "go": [".go"],
        "rust": [".rs"],
    }
    auto_patterns = {
        "javascript": r"\b(import|require)\b",
        "typescript": r"\b(import|require)\b",
        "cpp": CPP_INCLUDE_PATTERN,
        "c": CPP_INCLUDE_PATTERN,
        "java": r"^\s*import\s+[A-Za-z0-9_.*]+\s*;",
        "go": r"^\s*import\b",
        "rust": r"^\s*use\b",
    }

    def normalize_target_key(target: str) -> str:
        text = str(target).strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return text.lower()

    def build_path_zone_index(exts: list[str]) -> dict[str, set[str]]:
        index: dict[str, set[str]] = defaultdict(set)
        for filepath in source_files(root, skip_dirs):
            if filepath.suffix.lower() not in {ext.lower() for ext in exts}:
                continue
            zone = classify_file(filepath, zones)
            if zone is None:
                continue
            try:
                rel = filepath.relative_to(root)
            except ValueError:
                continue
            rel_parts = [part for part in rel.parts if part]
            for start in range(len(rel_parts)):
                suffix = normalize_target_key("/".join(rel_parts[start:]))
                if suffix:
                    index[suffix].add(zone)
        return index

    violations = []
    for lang, pattern_str in patterns.items():
        if lang == "python":
            continue
        exts = ext_map.get(lang, [])
        if not exts:
            continue

        if pattern_str == "auto":
            pattern_str = auto_patterns.get(lang, "")
        if not pattern_str:
            continue

        regex = re.compile(pattern_str)
        ext_set = {ext.lower() for ext in exts}
        files = [f for f in source_files(root, skip_dirs)
                 if f.suffix.lower() in ext_set]
        path_zone_index = build_path_zone_index(exts) if lang in {"cpp", "c"} else {}

        for f in files:
            zone = classify_file(f, zones)
            if zone is None:
                continue
            zone_level = ZONE_ORDER.index(zone) if zone in ZONE_ORDER else -1

            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for lineno, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    target_zones: set[str] = set()
                    if lang in {"cpp", "c"}:
                        match = regex.search(line)
                        if match and match.lastindex:
                            target_key = normalize_target_key(match.group(1))
                            target_zones.update(path_zone_index.get(target_key, set()))

                    if not target_zones:
                        # Fallback: check if the import references a less-stable zone by name.
                        for target_zone in ZONE_ORDER:
                            if target_zone in line:
                                target_zones.add(target_zone)

                    for target_zone in sorted(target_zones):
                        target_level = ZONE_ORDER.index(target_zone) if target_zone in ZONE_ORDER else -1
                        if target_level < zone_level:
                            try:
                                rel = f.relative_to(root)
                            except ValueError:
                                rel = f
                            violations.append({
                                "file": str(rel).replace("\\", "/"),
                                "from_zone": zone,
                                "target_zone": target_zone,
                                "line": lineno,
                                "content": line.strip()[:120],
                            })
    return violations


def _node_span(node: ast.AST) -> int:
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if isinstance(start, int) and isinstance(end, int):
        return max(1, end - start + 1)
    return 1


def analyze_python_structure(root: Path, zones: dict[str, Path],
                             skip_dirs: list[str],
                             thresholds: dict) -> dict:
    """Flag early God-file / God-object heuristics for Python projects."""
    result = {
        "god_file_risks": [],
        "excluded_god_file_risks": [],
        "god_function_risks": [],
        "god_class_risks": [],
    }
    py_files = [f for f in source_files(root, skip_dirs) if f.suffix == ".py"]

    file_warn = int(thresholds.get("god_file_warn_lines", 300))
    file_fail = int(thresholds.get("god_file_fail_lines", 500))
    func_warn = int(thresholds.get("god_function_warn_lines", 80))
    class_warn_lines = int(thresholds.get("god_class_warn_lines", 200))
    class_warn_methods = int(thresholds.get("god_class_warn_methods", 15))
    file_warn_imports = int(thresholds.get("god_file_warn_imports", 20))
    god_file_baselines = (
        thresholds.get("god_file_baselines", {})
        if isinstance(thresholds.get("god_file_baselines", {}), dict)
        else {}
    )
    protected_files = {
        _config_path_ref(path)
        for path in thresholds.get("god_file_protected_files", [])
        if _config_path_ref(path)
    } if isinstance(thresholds.get("god_file_protected_files", []), list) else set()
    baseline_max_slack = _parse_nonnegative_int(
        thresholds.get("god_file_baseline_max_slack", 50),
        50,
    )
    god_file_exceptions, exception_policy_failures = _god_file_exceptions_by_file(
        thresholds
    )
    result["god_file_risks"].extend(exception_policy_failures)

    for f in py_files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        file_ref = str(rel).replace("\\", "/")
        zone = classify_file(f, zones)
        source_kind = classify_source_kind(root, f)

        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(f))
        except (SyntaxError, ValueError, OSError):
            continue

        lines = source.splitlines()
        logical_lines = [
            line for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
        import_count = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )

        severity = None
        reasons: list[str] = []
        logical_count = len(logical_lines)
        protected = file_ref in protected_files
        baseline_limit = _god_file_baseline_for(file_ref, god_file_baselines)
        baseline_slack = (
            baseline_limit - logical_count
            if baseline_limit is not None
            else None
        )
        exception = god_file_exceptions.get(file_ref)
        exception_id = None

        if protected and baseline_limit is None:
            severity = "fail"
            reasons.append(
                "protected god file has no valid configured baseline"
            )
        elif baseline_slack is not None and baseline_slack > baseline_max_slack:
            severity = "fail"
            reasons.append(
                f"configured baseline slack {baseline_slack} exceeds "
                f"max {baseline_max_slack}"
            )
        elif baseline_limit is not None and logical_count > baseline_limit:
            over_by = logical_count - baseline_limit
            if exception is not None:
                exception_id = exception["id"]
                allowed_over_by = int(exception["allowed_over_by"])
                if over_by <= allowed_over_by:
                    severity = "warn"
                    reasons.append(
                        f"{logical_count} logical lines exceeds configured "
                        f"baseline {baseline_limit} by {over_by}, allowed by "
                        f"exception {exception_id}"
                    )
                else:
                    severity = "fail"
                    reasons.append(
                        f"{logical_count} logical lines exceeds configured "
                        f"baseline {baseline_limit} by {over_by}; exception "
                        f"{exception_id} allows {allowed_over_by}"
                    )
            else:
                severity = "fail"
                reasons.append(
                    f"{logical_count} logical lines exceeds configured baseline {baseline_limit}"
                )
        elif protected and baseline_limit is not None:
            severity = "warn"
            reasons.append(
                f"{logical_count} logical lines within protected baseline {baseline_limit}"
            )
        elif baseline_limit is not None and logical_count >= file_warn:
            severity = "warn"
            reasons.append(
                f"{logical_count} logical lines within configured baseline {baseline_limit}"
            )
        elif logical_count >= file_fail:
            severity = "fail"
            reasons.append(f"{logical_count} logical lines")
        elif logical_count >= file_warn:
            severity = "warn"
            reasons.append(f"{logical_count} logical lines")
        if import_count >= file_warn_imports:
            severity = severity or "warn"
            reasons.append(f"{import_count} imports")
        if reasons:
            risk = {
                "file": file_ref,
                "zone": zone,
                "severity": severity or "warn",
                "source_kind": source_kind,
                "current": logical_count,
                "logical_lines": logical_count,
                "imports": import_count,
                "reasons": reasons,
            }
            _apply_god_file_budget_fields(
                risk,
                baseline_limit,
                logical_count,
                protected,
            )
            if exception_id:
                risk["exception_id"] = exception_id
            if source_kind == SOURCE_KIND_LIVE_SOURCE:
                result["god_file_risks"].append(risk)
            else:
                result["excluded_god_file_risks"].append(risk)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = _node_span(node)
                if span >= func_warn:
                    result["god_function_risks"].append({
                        "file": file_ref,
                        "name": node.name,
                        "zone": zone,
                        "severity": "warn",
                        "lines": span,
                    })
            elif isinstance(node, ast.ClassDef):
                span = _node_span(node)
                methods = sum(
                    1 for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                if span >= class_warn_lines or methods >= class_warn_methods:
                    reasons = []
                    if span >= class_warn_lines:
                        reasons.append(f"{span} lines")
                    if methods >= class_warn_methods:
                        reasons.append(f"{methods} methods")
                    result["god_class_risks"].append({
                        "file": file_ref,
                        "name": node.name,
                        "zone": zone,
                        "severity": "warn",
                        "reasons": reasons,
                    })

    result["god_file_risks"].sort(key=_god_file_risk_sort_key)
    result["excluded_god_file_risks"].sort(key=_god_file_risk_sort_key)
    return result


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str):
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])

        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while stack:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)

    return components


def analyze_dependency_graph(import_data: dict, thresholds: dict) -> dict:
    """Evaluate internal dependency graph risks from Python imports."""
    imports_of = import_data.get("imports_of", {})
    imported_by = import_data.get("imported_by", {})
    module_files = import_data.get("module_files", {})
    module_zones = import_data.get("module_zones", {})

    result = {
        "fan_out_risks": [],
        "fan_in_risks": [],
        "dependency_cycles": [],
    }

    modules = sorted(
        set(module_files.keys()) |
        set(imports_of.keys()) |
        set(imported_by.keys())
    )
    if not modules:
        return result

    fan_out_warn = int(thresholds.get("import_fan_out_warn", 10))
    fan_out_fail = int(thresholds.get("import_fan_out_fail", 20))
    fan_in_warn = int(thresholds.get("import_fan_in_warn", 10))
    fan_in_fail = int(thresholds.get("import_fan_in_fail", 20))
    cycle_warn = max(2, int(thresholds.get("dependency_cycle_warn_length", 2)))
    cycle_fail = max(cycle_warn, int(thresholds.get("dependency_cycle_fail_length", 4)))

    for module_name in modules:
        file_ref = module_files.get(module_name, module_name.replace(".", "/") + ".py")
        zone = module_zones.get(module_name)
        fan_out = sorted(imports_of.get(module_name, set()))
        fan_in = sorted(imported_by.get(module_name, set()))

        if len(fan_out) >= fan_out_warn:
            result["fan_out_risks"].append({
                "module": module_name,
                "file": file_ref,
                "zone": zone,
                "severity": "fail" if len(fan_out) >= fan_out_fail else "warn",
                "count": len(fan_out),
                "targets": fan_out[:10],
            })
        if len(fan_in) >= fan_in_warn:
            result["fan_in_risks"].append({
                "module": module_name,
                "file": file_ref,
                "zone": zone,
                "severity": "fail" if len(fan_in) >= fan_in_fail else "warn",
                "count": len(fan_in),
                "sources": fan_in[:10],
            })

    graph = {module: set(imports_of.get(module, set())) for module in modules}
    for component in _strongly_connected_components(graph):
        is_self_cycle = (
            len(component) == 1
            and component[0] in graph.get(component[0], set())
        )
        if len(component) < 2 and not is_self_cycle:
            continue
        cycle_size = len(component)
        if cycle_size < cycle_warn:
            continue
        zones_in_cycle = sorted(
            {
                zone for zone in (module_zones.get(module) for module in component)
                if zone
            }
        )
        result["dependency_cycles"].append({
            "modules": component,
            "files": [module_files.get(module, module.replace(".", "/") + ".py") for module in component],
            "zones": zones_in_cycle,
            "severity": "fail" if cycle_size >= cycle_fail else "warn",
            "size": cycle_size,
        })

    return result


def _normalize_gateway_rule(rule: dict) -> dict | None:
    if not isinstance(rule, dict):
        return None
    pattern = str(rule.get("pattern", "")).strip()
    if not pattern:
        return None

    allowed_files = rule.get("allowed_files", rule.get("gateway_files", rule.get("file", [])))
    if isinstance(allowed_files, str):
        allowed_files = [allowed_files]
    if not isinstance(allowed_files, list):
        allowed_files = []
    normalized_allowed = [
        str(value).replace("\\", "/").strip()
        for value in allowed_files
        if str(value).strip()
    ]
    if not normalized_allowed:
        return None

    extensions = rule.get("extensions", [])
    if isinstance(extensions, str):
        extensions = [extensions]
    if not isinstance(extensions, list):
        extensions = []
    normalized_extensions = []
    for value in extensions:
        text = str(value).strip().lower()
        if not text:
            continue
        normalized_extensions.append(text if text.startswith(".") else f".{text}")

    severity = str(rule.get("severity", "fail")).strip().lower()
    if severity not in {"warn", "fail"}:
        severity = "fail"

    return {
        "pattern": pattern,
        "allowed_files": normalized_allowed,
        "extensions": normalized_extensions,
        "severity": severity,
        "message": str(rule.get("message", "")).strip(),
        "description": str(rule.get("description", "")).strip(),
        "regex": bool(rule.get("regex", False)),
        "ignore_case": bool(rule.get("ignore_case", False)),
    }


def _compile_text_rule_pattern(pattern: str,
                               regex: bool = False,
                               ignore_case: bool = False):
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        return re.compile(pattern, flags)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", pattern):
        return re.compile(rf"\b{re.escape(pattern)}\b", flags)
    return re.compile(re.escape(pattern), flags)


def _normalize_scoped_pattern_rule(rule: dict, rule_type: str) -> dict | None:
    if not isinstance(rule, dict):
        return None

    patterns = rule.get("patterns", rule.get("pattern", []))
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list):
        patterns = []
    normalized_patterns = [
        str(value).strip()
        for value in patterns
        if str(value).strip()
    ]
    if not normalized_patterns:
        return None

    source_zones = rule.get("source_zones", rule.get("source_zone", []))
    if isinstance(source_zones, str):
        source_zones = [source_zones]
    if not isinstance(source_zones, list):
        source_zones = []
    normalized_source_zones = [
        str(value).strip()
        for value in source_zones
        if str(value).strip()
    ]
    if not normalized_source_zones:
        return None

    allowed_files = rule.get("allowed_files", rule.get("file", []))
    if isinstance(allowed_files, str):
        allowed_files = [allowed_files]
    if not isinstance(allowed_files, list):
        allowed_files = []
    normalized_allowed = [
        str(value).replace("\\", "/").strip()
        for value in allowed_files
        if str(value).strip()
    ]

    extensions = rule.get("extensions", [])
    if isinstance(extensions, str):
        extensions = [extensions]
    if not isinstance(extensions, list):
        extensions = []
    normalized_extensions = []
    for value in extensions:
        text = str(value).strip().lower()
        if not text:
            continue
        normalized_extensions.append(text if text.startswith(".") else f".{text}")

    severity = str(rule.get("severity", "fail")).strip().lower()
    if severity not in {"warn", "fail"}:
        severity = "fail"

    message = str(rule.get("message", "")).strip()
    if not message:
        joined_zones = ", ".join(normalized_source_zones)
        message = f"{rule_type} rule violated in zone(s): {joined_zones}"

    return {
        "rule_type": rule_type,
        "patterns": normalized_patterns,
        "source_zones": normalized_source_zones,
        "allowed_files": normalized_allowed,
        "extensions": normalized_extensions,
        "severity": severity,
        "message": message,
        "description": str(rule.get("description", "")).strip(),
        "regex": bool(rule.get("regex", False)),
        "ignore_case": bool(rule.get("ignore_case", False)),
    }


def _load_scoped_pattern_rules_from_cc_config(root: Path,
                                              config_key: str,
                                              rule_type: str) -> list[dict]:
    config_path = root / CC_CONFIG_FILE
    if not config_path.exists():
        legacy = root / LEGACY_CC_CONFIG_FILE
        if legacy.exists():
            config_path = legacy
    if not config_path.exists():
        return []

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    raw_rules = config.get(config_key, [])
    if not isinstance(raw_rules, list):
        return []

    rules: list[dict] = []
    for item in raw_rules:
        normalized = _normalize_scoped_pattern_rule(item, rule_type)
        if normalized:
            rules.append(normalized)
    return rules


def _load_gateway_rules_from_cc_config(root: Path) -> list[dict]:
    config_path = root / CC_CONFIG_FILE
    if not config_path.exists():
        legacy = root / LEGACY_CC_CONFIG_FILE
        if legacy.exists():
            config_path = legacy
    if not config_path.exists():
        return []

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    raw_modules = config.get("gateway_modules", [])
    if isinstance(raw_modules, dict):
        raw_modules = [
            {"pattern": key, "file": value}
            for key, value in raw_modules.items()
        ]
    if not isinstance(raw_modules, list):
        return []

    rules: list[dict] = []
    for item in raw_modules:
        normalized = _normalize_gateway_rule(item)
        if not normalized:
            continue
        if not normalized["message"]:
            gateway_file = normalized["allowed_files"][0]
            description = normalized["description"]
            normalized["message"] = (
                description
                or f"`{normalized['pattern']}` must go through approved gateway `{gateway_file}`"
            )
        rules.append(normalized)
    return rules


def evaluate_gateway_rules(root: Path, skip_dirs: list[str],
                           gateway_rules: list[dict]) -> list[dict]:
    """Flag direct usage of configured gateway-only patterns outside approved files."""
    if not isinstance(gateway_rules, list):
        return []

    violations: list[dict] = []
    files = source_files(root, skip_dirs)

    for rule in gateway_rules:
        normalized = _normalize_gateway_rule(rule)
        if not normalized:
            continue

        pattern = normalized["pattern"]
        try:
            compiled = _compile_text_rule_pattern(
                pattern,
                regex=normalized.get("regex", False),
                ignore_case=normalized.get("ignore_case", False),
            )
        except re.error:
            continue

        allowed_files = set(normalized["allowed_files"])
        allowed_extensions = (
            set(normalized["extensions"])
            if normalized["extensions"]
            else DEFAULT_GATEWAY_FILE_EXTENSIONS
        )

        for filepath in files:
            if allowed_extensions and filepath.suffix.lower() not in allowed_extensions:
                continue
            try:
                rel = filepath.relative_to(root)
            except ValueError:
                continue
            file_ref = str(rel).replace("\\", "/")
            if file_ref in allowed_files:
                continue
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                if not compiled.search(line):
                    continue
                violations.append({
                    "file": file_ref,
                    "line": lineno,
                    "pattern": pattern,
                    "severity": normalized["severity"],
                    "allowed_files": normalized["allowed_files"],
                    "message": normalized["message"] or f"`{pattern}` must go through an approved gateway file",
                })
                break

    return violations


def evaluate_scoped_pattern_rules(root: Path, zones: dict[str, Path],
                                  skip_dirs: list[str],
                                  rules: list[dict],
                                  rule_type: str) -> list[dict]:
    """Evaluate configurable ownership/mutation rules against files in selected zones."""
    if not isinstance(rules, list):
        return []

    violations: list[dict] = []
    files = source_files(root, skip_dirs)

    for rule in rules:
        normalized = _normalize_scoped_pattern_rule(rule, rule_type)
        if not normalized:
            continue

        compiled_patterns = []
        invalid_rule = False
        for pattern in normalized["patterns"]:
            try:
                compiled_patterns.append(
                    (pattern, _compile_text_rule_pattern(
                        pattern,
                        regex=normalized.get("regex", False),
                        ignore_case=normalized.get("ignore_case", False),
                    ))
                )
            except re.error:
                invalid_rule = True
                break
        if invalid_rule:
            continue

        allowed_files = set(normalized["allowed_files"])
        allowed_extensions = (
            set(normalized["extensions"])
            if normalized["extensions"]
            else DEFAULT_GATEWAY_FILE_EXTENSIONS
        )
        source_zones = set(normalized["source_zones"])

        for filepath in files:
            if allowed_extensions and filepath.suffix.lower() not in allowed_extensions:
                continue
            try:
                rel = filepath.relative_to(root)
            except ValueError:
                continue
            file_ref = str(rel).replace("\\", "/")
            if file_ref in allowed_files:
                continue
            zone = classify_file(filepath, zones)
            if zone not in source_zones:
                continue
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = False
            for lineno, line in enumerate(content.splitlines(), 1):
                for pattern, compiled in compiled_patterns:
                    if not compiled.search(line):
                        continue
                    violations.append({
                        "rule_type": rule_type,
                        "file": file_ref,
                        "line": lineno,
                        "zone": zone,
                        "pattern": pattern,
                        "severity": normalized["severity"],
                        "allowed_files": normalized["allowed_files"],
                        "message": normalized["message"],
                    })
                    found = True
                    break
                if found:
                    break

    return violations


def evaluate_layer_rules(import_data: dict, layer_rules: list[dict]) -> list[dict]:
    """Evaluate configurable layer rules against cross-zone imports."""
    if not isinstance(layer_rules, list):
        return []

    violations: list[dict] = []
    cross_zone_imports = import_data.get("cross_zone_imports", [])
    if not isinstance(cross_zone_imports, list):
        return violations

    for rule in layer_rules:
        if not isinstance(rule, dict):
            continue
        source_zone = str(rule.get("source_zone", "")).strip()
        target_zones = rule.get("forbidden_target_zones", rule.get("target_zones", []))
        if isinstance(target_zones, str):
            target_zones = [target_zones]
        if not source_zone or not isinstance(target_zones, list):
            continue
        normalized_targets = {
            str(value).strip()
            for value in target_zones
            if str(value).strip()
        }
        if not normalized_targets:
            continue
        severity = str(rule.get("severity", "fail")).strip().lower()
        if severity not in {"warn", "fail"}:
            severity = "fail"
        message = str(rule.get("message", "")).strip()

        for edge in cross_zone_imports:
            if edge.get("from_zone") != source_zone:
                continue
            if edge.get("target_zone") not in normalized_targets:
                continue
            violations.append({
                "file": edge.get("file"),
                "from_zone": source_zone,
                "target_zone": edge.get("target_zone"),
                "imports": edge.get("imports"),
                "line": edge.get("line"),
                "severity": severity,
                "message": message or f"{source_zone} cannot import {edge.get('target_zone')}",
            })

    return violations


# ============================================================
# METRIC 5: SHARED/ INSTABILITY
# ============================================================

def compute_instability(import_data: dict, zones: dict[str, Path],
                        root: Path, skip_dirs: list[str]) -> dict[str, float]:
    """Compute instability = Ce / (Ca + Ce) for shared/ modules.

    Ce = efferent coupling (how many external modules this module imports)
    Ca = afferent coupling (how many external modules import this module)
    """
    if "shared" not in zones:
        return {}

    shared_path = zones["shared"]
    py_files = [f for f in source_files(root, skip_dirs) if f.suffix == ".py"]

    # Ca: count how many files outside shared/ import something in shared/
    ca_counts = Counter()  # shared_module -> count
    ce_counts = Counter()  # shared_module -> count

    for f in py_files:
        zone = classify_file(f, zones)
        if zone is None:
            continue

        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(f))
        except (SyntaxError, ValueError):
            continue

        try:
            rel = f.relative_to(root)
        except ValueError:
            continue

        for node in ast.walk(tree):
            imported_module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_module = alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_module = node.module

            if not imported_module:
                continue

            # If this file is in shared/ and imports something outside
            if zone == "shared":
                ce_counts[str(rel).replace("\\", "/")] += 1

            # If this file is outside shared/ and imports something in shared/
            if zone != "shared":
                parts = imported_module.split(".")
                for zn in ["shared"]:
                    if zn in parts:
                        # Count as afferent coupling for the shared module
                        ca_counts[imported_module] += 1

    # Compute instability per shared/ file
    instability = {}
    shared_files = [f for f in py_files if classify_file(f, zones) == "shared"]
    for f in shared_files:
        try:
            key = str(f.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        ca = ca_counts.get(key, 0)
        ce = ce_counts.get(key, 0)
        if ca + ce > 0:
            instability[key] = round(ce / (ca + ce), 3)

    return instability


# ============================================================
# TEMPORAL: HISTORY AND DELTAS
# ============================================================

def load_history(root: Path) -> list[dict]:
    """Load fitness history from JSONL."""
    path = root / HISTORY_FILE
    if not path.exists():
        legacy = root / LEGACY_HISTORY_FILE
        if legacy.exists():
            path = legacy
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def save_snapshot(root: Path, snapshot: dict):
    """Append current snapshot to fitness history."""
    path = root / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


def compute_deltas(current: dict, history: list[dict],
                   thresholds: dict) -> list[dict]:
    """Compare current snapshot with the most recent historical one."""
    deltas = []
    if not history:
        return deltas

    prev = history[-1]
    prev_zones = prev.get("zone_files", {})
    curr_zones = current.get("zone_files", {})

    warn_pct = thresholds.get("zone_growth_warn_pct", 30)

    for zone, count in curr_zones.items():
        if zone == "unclassified":
            continue
        prev_count = prev_zones.get(zone, 0)
        if prev_count > 0:
            growth = ((count - prev_count) / prev_count) * 100
            if growth > warn_pct:
                deltas.append({
                    "type": "zone_growth",
                    "zone": zone,
                    "previous": prev_count,
                    "current": count,
                    "growth_pct": round(growth, 1),
                })

    prev_violations = prev.get("direction_violations", 0)
    curr_violations = current.get("direction_violations", 0)
    if curr_violations > prev_violations:
        deltas.append({
            "type": "violations_increase",
            "previous": prev_violations,
            "current": curr_violations,
        })

    return deltas


def evaluate_zone_hygiene(zone_files: dict[str, int],
                          thresholds: dict) -> list[dict]:
    """Flag configurations where too much code sits outside the declared zones."""
    if not isinstance(zone_files, dict) or not zone_files:
        return []

    unclassified = int(zone_files.get("unclassified", 0) or 0)
    if unclassified <= 0:
        return []

    total = sum(
        int(count or 0)
        for zone, count in zone_files.items()
        if zone != "unclassified"
    ) + unclassified
    if total <= 0:
        return []

    ratio = (unclassified / total) * 100.0
    fail_pct = float(thresholds.get("unclassified_fail_pct", 45))
    warn_pct = float(thresholds.get("unclassified_warn_pct", 20))
    severity = ""
    if ratio >= fail_pct:
        severity = "fail"
    elif ratio >= warn_pct:
        severity = "warn"
    if not severity:
        return []

    return [{
        "severity": severity,
        "unclassified": unclassified,
        "total": total,
        "ratio_pct": round(ratio, 1),
        "message": "Too many source files are outside the declared condominium zones",
    }]


def check_fitness_tool_drift(root: Path) -> list[dict]:
    """Return a hard violation when the ignored hook copy diverges."""
    script_path = root / "scripts" / "fitness_check.py"
    tool_path = root / "tools" / "fitness_check.py"
    if not tool_path.exists():
        return []

    try:
        script_bytes = script_path.read_bytes()
        tool_bytes = tool_path.read_bytes()
    except OSError as exc:
        return [{
            "severity": "fail",
            "file": "tools/fitness_check.py",
            "expected": "scripts/fitness_check.py",
            "message": f"could not compare fitness check copies: {exc}",
        }]

    if script_bytes == tool_bytes:
        return []
    return [{
        "severity": "fail",
        "file": "tools/fitness_check.py",
        "expected": "scripts/fitness_check.py",
        "message": (
            "tools/fitness_check.py must be byte-identical to "
            "scripts/fitness_check.py"
        ),
    }]


# ============================================================
# REPORT
# ============================================================

def _format_god_file_risk_line(risk: dict) -> str:
    reasons = ", ".join(risk.get("reasons", []))
    zone = risk.get("zone") or "unclassified"
    source_kind = risk.get("source_kind", SOURCE_KIND_LIVE_SOURCE)
    metrics = []
    metrics.append(f"protected={str(bool(risk.get('protected', False))).lower()}")
    if "current" in risk:
        metrics.append(f"current={risk['current']}")
    if "baseline" in risk:
        baseline = risk["baseline"]
        baseline_ref = baseline if baseline is not None else "missing"
        metrics.append(f"baseline={baseline_ref}")
    if "remaining" in risk:
        metrics.append(f"remaining={risk['remaining']}")
    if "over_by" in risk:
        metrics.append(f"over_by={risk['over_by']}")
    if "exception_id" in risk:
        metrics.append(f"exception_id={risk['exception_id']}")
    if "imports" in risk:
        metrics.append(f"imports={risk['imports']}")
    detail = f" -> {reasons}" if reasons else ""
    metric_ref = f"; {', '.join(metrics)}" if metrics else ""
    return (
        f"- [{risk.get('severity', 'warn').upper()}] `{risk['file']}` "
        f"({zone}, {source_kind}{metric_ref}){detail}"
    )


def format_report(snapshot: dict, deltas: list[dict],
                  git_data: dict, import_data: dict,
                  structure_data: dict,
                  layer_rule_violations: list[dict],
                  instability: dict[str, float],
                  thresholds: dict) -> str:
    """Generate human-readable markdown report."""
    lines = []
    lines.append("# Fitness Check Report")
    lines.append(f"\n**Date**: {snapshot['timestamp']}")
    lines.append(f"**Project**: {snapshot.get('project_root', '?')}")

    config_sources = snapshot.get("config_sources", [])
    rule_counts = snapshot.get("configured_rule_counts", {})
    if config_sources or rule_counts:
        lines.append("\n## Configuration Evidence\n")
        if config_sources:
            lines.append("Sources:")
            for source in config_sources:
                lines.append(
                    "- "
                    f"`{source.get('source', '?')}` "
                    f"({source.get('key', '?')}) - {source.get('status', '?')}"
                )
            lines.append("")
        if rule_counts:
            lines.append("| Rule pack | Count |")
            lines.append("|---|---:|")
            for name in [
                "zones",
                "thresholds",
                "layer_rules",
                "gateway_rules",
                "ownership_rules",
                "mutation_rules",
            ]:
                if name in rule_counts:
                    lines.append(f"| `{name}` | {rule_counts.get(name, 0)} |")

    # Zone sizes
    lines.append("\n## Zone Size Distribution\n")
    lines.append("| Zone | Files |")
    lines.append("|------|-------|")
    for zone in ZONE_ORDER:
        count = snapshot["zone_files"].get(zone, "-")
        if count != "-":
            lines.append(f"| {zone}/ | {count} |")
    unclassified = snapshot["zone_files"].get("unclassified", 0)
    if unclassified:
        lines.append(f"| (unclassified) | {unclassified} |")

    # Git metrics
    if git_data.get("total_commits"):
        lines.append("\n## Git Metrics\n")
        lines.append(f"- Commits analyzed: {git_data['total_commits']}")
        lines.append(f"- Cross-zone commits: {git_data['cross_zone_commits']}")

        # Files per commit - flag large ones
        max_fpc = thresholds.get("files_per_commit_max", 5)
        large_commits = [
            (h, c, fs) for h, c, fs in git_data["files_per_commit"]
            if c > max_fpc
        ]
        if large_commits:
            lines.append(f"\n### Large commits (> {max_fpc} files)\n")
            lines.append("| Commit | Files | Sample |")
            lines.append("|--------|-------|--------|")
            for h, c, fs in large_commits[:10]:
                sample = ", ".join(fs[:3])
                if c > 3:
                    sample += ", ..."
                lines.append(f"| {h} | {c} | {sample} |")

        # Co-change coupling
        cochange_thresh = thresholds.get("cochange_warn_threshold", 3)
        hot_pairs = [
            (pair, count) for pair, count in git_data["cochange_pairs"].items()
            if count >= cochange_thresh
        ]
        if hot_pairs:
            hot_pairs.sort(key=lambda x: -x[1])
            lines.append(f"\n### Cross-zone co-change pairs (>= {cochange_thresh} times)\n")
            lines.append("| File A | File B | Times |")
            lines.append("|--------|--------|-------|")
            for (a, b), count in hot_pairs[:15]:
                lines.append(f"| {a} | {b} | {count} |")

    # Dependency direction violations
    violations = import_data.get("violations", [])
    lines.append(f"\n## Dependency Direction Violations: {len(violations)}\n")
    if violations:
        lines.append("| File | Zone | Imports | Target Zone | Line |")
        lines.append("|------|------|---------|-------------|------|")
        for v in violations[:20]:
            lines.append(
                f"| {v['file']} | {v['from_zone']} | {v['imports']} "
                f"| {v['target_zone']} | {v['line']} |"
            )
        if len(violations) > 20:
            lines.append(f"\n... and {len(violations) - 20} more.")
    else:
        lines.append("None found.")

    # Efferent coupling of stable/
    efferent = import_data.get("efferent_stable", [])
    if efferent:
        lines.append(f"\n## Efferent Coupling of stable/: {len(efferent)} violations\n")
        for v in efferent:
            lines.append(f"- `{v['file']}` imports `{v['imports']}` ({v['target_zone']}/)")

    # Instability of shared/
    if instability:
        max_inst = thresholds.get("instability_shared_max", 0.3)
        lines.append(f"\n## Instability of shared/ modules (threshold: {max_inst})\n")
        lines.append("| Module | Instability |")
        lines.append("|--------|-------------|")
        for mod, val in sorted(instability.items(), key=lambda x: -x[1]):
            flag = " **!!**" if val > max_inst else ""
            lines.append(f"| {mod} | {val}{flag} |")

    god_file_risks = structure_data.get("god_file_risks", [])
    excluded_god_file_risks = structure_data.get("excluded_god_file_risks", [])
    god_function_risks = structure_data.get("god_function_risks", [])
    god_class_risks = structure_data.get("god_class_risks", [])
    zone_hygiene_alerts = structure_data.get("zone_hygiene_alerts", [])
    gateway_rule_violations = structure_data.get("gateway_rule_violations", [])
    ownership_rule_violations = structure_data.get("ownership_rule_violations", [])
    mutation_rule_violations = structure_data.get("mutation_rule_violations", [])
    fan_out_risks = structure_data.get("fan_out_risks", [])
    fan_in_risks = structure_data.get("fan_in_risks", [])
    dependency_cycles = structure_data.get("dependency_cycles", [])
    tool_drift_violations = structure_data.get("tool_drift_violations", [])
    if zone_hygiene_alerts:
        lines.append("\n## Zone Hygiene\n")
        for alert in zone_hygiene_alerts[:10]:
            lines.append(
                f"- [{alert.get('severity', 'warn').upper()}] "
                f"{alert['message']}: {alert['unclassified']} / {alert['total']} files "
                f"({alert['ratio_pct']}%)"
            )
    if (
        god_file_risks
        or excluded_god_file_risks
        or god_function_risks
        or god_class_risks
    ):
        lines.append("\n## Structural Fitness Heuristics\n")
        if god_file_risks:
            lines.append("\n### God-file risks\n")
            for risk in god_file_risks[:20]:
                lines.append(_format_god_file_risk_line(risk))
        if excluded_god_file_risks:
            lines.append("\n### Excluded God-file artifact summary\n")
            counts = Counter(
                risk.get("source_kind", SOURCE_KIND_EXCLUDED_ARTIFACT)
                for risk in excluded_god_file_risks
            )
            for source_kind in [
                SOURCE_KIND_GENERATED_RELEASE,
                SOURCE_KIND_TEMP_WORKSPACE,
                SOURCE_KIND_TOOL_COPY,
                SOURCE_KIND_EXCLUDED_ARTIFACT,
            ]:
                if counts.get(source_kind):
                    lines.append(f"- `{source_kind}`: {counts[source_kind]}")
            lines.append("")
            for risk in excluded_god_file_risks[:10]:
                lines.append(_format_god_file_risk_line(risk))
        if god_function_risks:
            lines.append("\n### Oversized functions\n")
            for risk in god_function_risks[:20]:
                lines.append(f"- [WARN] `{risk['file']}` :: `{risk['name']}()` -> {risk['lines']} lines")
        if god_class_risks:
            lines.append("\n### God-object risks\n")
            for risk in god_class_risks[:20]:
                reasons = ", ".join(risk.get("reasons", []))
                lines.append(f"- [WARN] `{risk['file']}` :: `{risk['name']}` -> {reasons}")

    if tool_drift_violations:
        lines.append("\n## Fitness Tool Drift\n")
        for violation in tool_drift_violations[:20]:
            lines.append(
                f"- [{violation.get('severity', 'fail').upper()}] "
                f"`{violation['file']}` differs from `{violation.get('expected', '?')}`: "
                f"{violation['message']}"
            )

    if gateway_rule_violations:
        lines.append("\n## Gateway Rule Violations\n")
        for violation in gateway_rule_violations[:20]:
            allowed = ", ".join(violation.get("allowed_files", []))
            lines.append(
                f"- [{violation.get('severity', 'fail').upper()}] `{violation['file']}` line {violation['line']}: "
                f"{violation['message']} (allowed: {allowed})"
            )

    if ownership_rule_violations:
        lines.append("\n## Ownership Rule Violations\n")
        for violation in ownership_rule_violations[:20]:
            allowed = ", ".join(violation.get("allowed_files", []))
            suffix = f" (allowed: {allowed})" if allowed else ""
            lines.append(
                f"- [{violation.get('severity', 'fail').upper()}] `{violation['file']}` line {violation['line']}: "
                f"{violation['message']}{suffix}"
            )

    if mutation_rule_violations:
        lines.append("\n## Mutation Rule Violations\n")
        for violation in mutation_rule_violations[:20]:
            allowed = ", ".join(violation.get("allowed_files", []))
            suffix = f" (allowed: {allowed})" if allowed else ""
            lines.append(
                f"- [{violation.get('severity', 'fail').upper()}] `{violation['file']}` line {violation['line']}: "
                f"{violation['message']}{suffix}"
            )

    if fan_out_risks or fan_in_risks or dependency_cycles:
        lines.append("\n## Dependency Graph Fitness\n")
        if fan_out_risks:
            lines.append("\n### Fan-out hotspots\n")
            for risk in fan_out_risks[:20]:
                zone = risk.get("zone") or "unclassified"
                sample = ", ".join(risk.get("targets", [])[:3])
                if risk.get("count", 0) > 3 and sample:
                    sample += ", ..."
                suffix = f" -> {sample}" if sample else ""
                lines.append(
                    f"- [{risk.get('severity', 'warn').upper()}] `{risk['file']}` ({zone}) depends on "
                    f"{risk['count']} internal modules{suffix}"
                )
        if fan_in_risks:
            lines.append("\n### Fan-in hotspots\n")
            for risk in fan_in_risks[:20]:
                zone = risk.get("zone") or "unclassified"
                sample = ", ".join(risk.get("sources", [])[:3])
                if risk.get("count", 0) > 3 and sample:
                    sample += ", ..."
                suffix = f" <- {sample}" if sample else ""
                lines.append(
                    f"- [{risk.get('severity', 'warn').upper()}] `{risk['file']}` ({zone}) is depended on by "
                    f"{risk['count']} internal modules{suffix}"
                )
        if dependency_cycles:
            lines.append("\n### Import cycles\n")
            for cycle in dependency_cycles[:20]:
                modules = ", ".join(cycle.get("modules", []))
                zones = ", ".join(cycle.get("zones", [])) or "unclassified"
                lines.append(
                    f"- [{cycle.get('severity', 'warn').upper()}] {cycle.get('size', 0)}-module cycle "
                    f"across [{zones}]: {modules}"
                )

    if layer_rule_violations:
        lines.append("\n## Configured Layer Rule Violations\n")
        for violation in layer_rule_violations[:20]:
            lines.append(
                f"- [{violation.get('severity', 'fail').upper()}] `{violation['file']}` line {violation['line']}: "
                f"{violation['message']} (`{violation['imports']}` -> {violation['target_zone']})"
            )

    # Temporal deltas
    if deltas:
        lines.append("\n## Trend Alerts\n")
        for d in deltas:
            if d["type"] == "zone_growth":
                lines.append(
                    f"- **{d['zone']}/** grew {d['growth_pct']}% "
                    f"({d['previous']} -> {d['current']} files)"
                )
            elif d["type"] == "violations_increase":
                lines.append(
                    f"- **Dependency violations increased**: "
                    f"{d['previous']} -> {d['current']}"
                )
    elif snapshot.get("has_history"):
        lines.append("\n## Trend Alerts\n\nNo trend alerts. Architecture stable.")

    # Summary
    has_hard = (
        len(violations) > 0
        or len(efferent) > 0
        or any(alert.get("severity") == "fail" for alert in zone_hygiene_alerts)
        or any(risk.get("severity") == "fail" for risk in god_file_risks)
        or any(v.get("severity") == "fail" for v in gateway_rule_violations)
        or any(v.get("severity") == "fail" for v in ownership_rule_violations)
        or any(v.get("severity") == "fail" for v in mutation_rule_violations)
        or any(risk.get("severity") == "fail" for risk in fan_out_risks)
        or any(risk.get("severity") == "fail" for risk in fan_in_risks)
        or any(cycle.get("severity") == "fail" for cycle in dependency_cycles)
        or any(v.get("severity") == "fail" for v in layer_rule_violations)
    )
    has_warn = (
        len(deltas) > 0
        or any(alert.get("severity") == "warn" for alert in zone_hygiene_alerts)
        or len(god_function_risks) > 0
        or len(god_class_risks) > 0
        or any(risk.get("severity") == "warn" for risk in god_file_risks)
        or any(v.get("severity") == "warn" for v in gateway_rule_violations)
        or any(v.get("severity") == "warn" for v in ownership_rule_violations)
        or any(v.get("severity") == "warn" for v in mutation_rule_violations)
        or any(risk.get("severity") == "warn" for risk in fan_out_risks)
        or any(risk.get("severity") == "warn" for risk in fan_in_risks)
        or any(cycle.get("severity") == "warn" for cycle in dependency_cycles)
        or any(v.get("severity") == "warn" for v in layer_rule_violations)
    )
    lines.append("\n---")
    if has_hard:
        lines.append("\n**RESULT: VIOLATIONS FOUND** (exit 1)")
    elif has_warn:
        lines.append("\n**RESULT: WARNINGS** (exit 2)")
    else:
        lines.append("\n**RESULT: ALL CLEAR** (exit 0)")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def _merge_fitness_config(config: dict, user: dict) -> None:
    """Merge user-provided architecture fitness settings into config."""
    if not isinstance(user, dict):
        return

    for key in ["zones", "import_patterns", "thresholds"]:
        value = user.get(key)
        if isinstance(value, dict):
            config[key].update(value)

    for key in ["layer_rules", "gateway_rules", "ownership_rules", "mutation_rules"]:
        value = user.get(key)
        if isinstance(value, list):
            config[key] = value

    if "git_history_commits" in user:
        config["git_history_commits"] = user["git_history_commits"]
    if isinstance(user.get("skip_dirs"), list):
        config["skip_dirs"] = user["skip_dirs"]


def _fitness_source_label(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _find_cc_config_path(root: Path) -> Path | None:
    config_path = root / CC_CONFIG_FILE
    if config_path.exists():
        return config_path
    legacy = root / LEGACY_CC_CONFIG_FILE
    if legacy.exists():
        return legacy
    return None


def _load_architecture_fitness_from_cc_config(root: Path,
                                              evidence: list[dict] | None = None) -> dict:
    config_path = _find_cc_config_path(root)
    if config_path is None:
        return {}

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        if evidence is not None:
            evidence.append({
                "source": _fitness_source_label(root, config_path),
                "key": "architecture_fitness",
                "status": "invalid",
            })
        return {}

    user = config.get("architecture_fitness")
    key = "architecture_fitness"
    if user is None:
        user = config.get("fitness")
        key = "fitness"
    if isinstance(user, dict):
        if evidence is not None:
            evidence.append({
                "source": _fitness_source_label(root, config_path),
                "key": key,
                "status": "loaded",
            })
        return user
    if evidence is not None:
        evidence.append({
            "source": _fitness_source_label(root, config_path),
            "key": key,
            "status": "missing",
        })
    return {}


def load_config_with_evidence(root: Path) -> tuple[dict, list[dict]]:
    """Load config and return evidence for the sources that shaped it."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    evidence: list[dict] = [
        {
            "source": "built-in defaults",
            "key": "DEFAULT_CONFIG",
            "status": "loaded",
        }
    ]
    _merge_fitness_config(
        config,
        _load_architecture_fitness_from_cc_config(root, evidence),
    )

    config_path = root / "fitness.json"
    if config_path.exists():
        try:
            user = json.loads(config_path.read_text(encoding="utf-8"))
            _merge_fitness_config(config, user)
            evidence.append({
                "source": "fitness.json",
                "key": "local_override",
                "status": "loaded",
            })
        except (json.JSONDecodeError, KeyError):
            evidence.append({
                "source": "fitness.json",
                "key": "local_override",
                "status": "invalid",
            })
            print("WARNING: fitness.json is invalid, using shared/default config",
                  file=sys.stderr)
    return config, evidence


def load_config(root: Path) -> dict:
    """Load shared cc_config fitness settings, then local fitness.json overrides."""
    config, _evidence = load_config_with_evidence(root)
    return config


def configured_rule_counts(config: dict,
                           gateway_rules: list[dict] | None = None,
                           ownership_rules: list[dict] | None = None,
                           mutation_rules: list[dict] | None = None) -> dict:
    """Return a small evidence summary for active architecture rule packs."""
    thresholds = config.get("thresholds", {}) if isinstance(config, dict) else {}
    zones = config.get("zones", {}) if isinstance(config, dict) else {}
    return {
        "zones": len(zones) if isinstance(zones, dict) else 0,
        "thresholds": len(thresholds) if isinstance(thresholds, dict) else 0,
        "layer_rules": len(config.get("layer_rules", []) or []),
        "gateway_rules": len(
            gateway_rules
            if gateway_rules is not None
            else config.get("gateway_rules", []) or []
        ),
        "ownership_rules": len(
            ownership_rules
            if ownership_rules is not None
            else config.get("ownership_rules", []) or []
        ),
        "mutation_rules": len(
            mutation_rules
            if mutation_rules is not None
            else config.get("mutation_rules", []) or []
        ),
    }


# ============================================================
# WIRING MATRIX
# ============================================================

def generate_wiring_matrix(root: Path, skip_dirs: list[str],
                           output_path: Path = None) -> str:
    """Generate a module connectivity matrix as Markdown.

    For each Python module, shows:
    - What it imports (from the project)
    - What imports it
    - Config dependencies (env vars, config file reads)

    Returns the Markdown content as a string.
    """
    py_files = [f for f in source_files(root, skip_dirs) if f.suffix == ".py"]

    # Build import graph
    imports_of = defaultdict(set)    # module -> set of modules it imports
    imported_by = defaultdict(set)   # module -> set of modules that import it
    config_deps = defaultdict(set)   # module -> set of config references

    # Map of known project modules
    project_modules = set()
    for f in py_files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        mod_name = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")
        project_modules.add(mod_name)

    for f in py_files:
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        mod_name = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")

        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(f))
        except (SyntaxError, ValueError):
            continue

        for node in ast.walk(tree):
            imported_module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_module = alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_module = node.module

            if not imported_module:
                continue

            # Check if this is a project-internal import
            matched = None
            parts = imported_module.split(".")
            for i in range(len(parts), 0, -1):
                prefix = ".".join(parts[:i])
                if prefix in project_modules:
                    matched = prefix
                    break
            if matched and matched != mod_name:
                imports_of[mod_name].add(matched)
                imported_by[matched].add(mod_name)

            # Detect config dependencies
            if isinstance(node, ast.Call):
                _extract_config_deps(node, mod_name, config_deps)

        # Also scan for os.environ / os.getenv patterns
        for node in ast.walk(tree):
            _extract_config_deps(node, mod_name, config_deps)

    # Generate markdown
    lines = [
        "# Wiring Matrix",
        "",
        "> Auto-generated by `fitness_check.py --wiring-matrix`. Do not edit.",
        "",
        "| Module | Imports | Imported by | Config deps |",
        "|--------|---------|-------------|-------------|",
    ]

    all_modules = sorted(
        project_modules & (
            set(imports_of.keys()) | set(imported_by.keys())
            | set(config_deps.keys())
        )
    )

    for mod in all_modules:
        imp = ", ".join(sorted(imports_of.get(mod, set()))) or "-"
        imp_by = ", ".join(sorted(imported_by.get(mod, set()))) or "-"
        cfg = ", ".join(sorted(config_deps.get(mod, set()))) or "-"
        # Truncate long cells
        if len(imp) > 60:
            imp = imp[:57] + "..."
        if len(imp_by) > 60:
            imp_by = imp_by[:57] + "..."
        lines.append(f"| `{mod}` | {imp} | {imp_by} | {cfg} |")

    lines.append("")
    content = "\n".join(lines)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

    return content


def _extract_config_deps(node, mod_name: str, config_deps: dict):
    """Extract config/env var references from AST nodes."""
    # os.environ["KEY"] or os.environ.get("KEY")
    if isinstance(node, ast.Subscript):
        if (isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "os"
                and node.value.attr == "environ"):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                config_deps[mod_name].add(node.slice.value)
    # os.getenv("KEY") or os.environ.get("KEY")
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("getenv", "get"):
            if (isinstance(func.value, ast.Name) and func.value.id == "os"
                    and func.attr == "getenv"):
                if node.args and isinstance(node.args[0], ast.Constant):
                    config_deps[mod_name].add(node.args[0].value)
            elif (isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "os"
                    and func.value.attr == "environ"
                    and func.attr == "get"):
                if node.args and isinstance(node.args[0], ast.Constant):
                    config_deps[mod_name].add(node.args[0].value)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="ControlCoding Architectural Fitness Functions"
    )
    parser.add_argument(
        "--project-root", default=".",
        help="Root of the project to analyze (default: current directory)"
    )
    parser.add_argument(
        "--ci", action="store_true",
        help="CI mode: exit code reflects violations"
    )
    parser.add_argument(
        "--delta-only", action="store_true",
        help="Only show temporal deltas (requires history)"
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Do not save snapshot to history"
    )
    parser.add_argument(
        "--wiring-matrix", action="store_true",
        help="Generate devlog/wiring-matrix.md and exit"
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    config, config_sources = load_config_with_evidence(root)

    # Wiring matrix mode: generate and exit
    if args.wiring_matrix:
        output = root / "devlog" / "wiring-matrix.md"
        content = generate_wiring_matrix(root, config["skip_dirs"], output)
        print(f"Generated {output}")
        print(content)
        sys.exit(0)
    thresholds = config["thresholds"]

    # Detect zones
    zones = detect_zones(root, config["zones"])
    if not zones:
        print("WARNING: No condominium zones detected. "
              "Configure zones in fitness.json or create "
              "stable/, shared/, features/, workspace/ directories.",
              file=sys.stderr)
        # Still run git metrics even without zones
        zones = {}

    # Metric 1: Zone sizes
    zone_files = count_zone_files(root, zones, config["skip_dirs"]) if zones else {}

    # Metric 2 & 3: Git analysis
    git_data = analyze_git(root, zones, config["git_history_commits"])

    # Metric 4: Import analysis
    import_data = {"violations": [], "efferent_stable": [],
                   "module_coupling": {}}
    regex_violations = []

    if zones:
        # Python (built-in)
        if config["import_patterns"].get("python") == "auto":
            has_python = any(
                f.suffix == ".py"
                for f in source_files(root, config["skip_dirs"])
            )
            if has_python:
                import_data = analyze_python_imports(
                    root, zones, config["skip_dirs"]
                )

        # Other languages (regex)
        other_patterns = {
            k: v for k, v in config["import_patterns"].items()
            if k != "python"
        }
        if other_patterns:
            regex_violations = analyze_regex_imports(
                root, zones, config["skip_dirs"], other_patterns
            )
            import_data["violations"].extend(
                {**v, "imports": v.get("content", "?")}
                for v in regex_violations
            )

    # Metric 5: Instability
    instability = {}
    if zones and "shared" in zones:
        instability = compute_instability(
            import_data, zones, root, config["skip_dirs"]
        )

    structure_data = {
        "god_file_risks": [],
        "excluded_god_file_risks": [],
        "god_function_risks": [],
        "god_class_risks": [],
        "zone_hygiene_alerts": evaluate_zone_hygiene(zone_files, thresholds),
        "gateway_rule_violations": [],
        "ownership_rule_violations": [],
        "mutation_rule_violations": [],
        "fan_out_risks": [],
        "fan_in_risks": [],
        "dependency_cycles": [],
        "tool_drift_violations": [],
    }
    layer_rule_violations: list[dict] = []
    has_python = any(
        f.suffix == ".py"
        for f in source_files(root, config["skip_dirs"])
    )
    if has_python:
        structure_data = analyze_python_structure(
            root, zones, config["skip_dirs"], thresholds
        )
        structure_data.update(analyze_dependency_graph(import_data, thresholds))
    structure_data["tool_drift_violations"] = check_fitness_tool_drift(root)
    gateway_rules = list(config.get("gateway_rules", [])) + _load_gateway_rules_from_cc_config(root)
    structure_data["gateway_rule_violations"] = evaluate_gateway_rules(
        root, config["skip_dirs"], gateway_rules
    )
    ownership_rules = list(config.get("ownership_rules", [])) + _load_scoped_pattern_rules_from_cc_config(
        root, "ownership_rules", "ownership"
    )
    mutation_rules = list(config.get("mutation_rules", [])) + _load_scoped_pattern_rules_from_cc_config(
        root, "mutation_rules", "mutation"
    )
    structure_data["ownership_rule_violations"] = evaluate_scoped_pattern_rules(
        root, zones, config["skip_dirs"], ownership_rules, "ownership"
    )
    structure_data["mutation_rule_violations"] = evaluate_scoped_pattern_rules(
        root, zones, config["skip_dirs"], mutation_rules, "mutation"
    )
    layer_rule_violations = evaluate_layer_rules(
        import_data, config.get("layer_rules", [])
    )

    # Build snapshot
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_root": str(root),
        "zone_files": zone_files,
        "direction_violations": len(import_data.get("violations", [])),
        "efferent_stable": len(import_data.get("efferent_stable", [])),
        "god_file_risks": len(structure_data.get("god_file_risks", [])),
        "excluded_god_file_risks": len(structure_data.get("excluded_god_file_risks", [])),
        "god_function_risks": len(structure_data.get("god_function_risks", [])),
        "god_class_risks": len(structure_data.get("god_class_risks", [])),
        "zone_hygiene_alerts": len(structure_data.get("zone_hygiene_alerts", [])),
        "gateway_rule_violations": len(structure_data.get("gateway_rule_violations", [])),
        "ownership_rule_violations": len(structure_data.get("ownership_rule_violations", [])),
        "mutation_rule_violations": len(structure_data.get("mutation_rule_violations", [])),
        "fan_out_risks": len(structure_data.get("fan_out_risks", [])),
        "fan_in_risks": len(structure_data.get("fan_in_risks", [])),
        "dependency_cycles": len(structure_data.get("dependency_cycles", [])),
        "tool_drift_violations": len(structure_data.get("tool_drift_violations", [])),
        "layer_rule_violations": len(layer_rule_violations),
        "cross_zone_commits": git_data.get("cross_zone_commits", 0),
        "total_commits_analyzed": git_data.get("total_commits", 0),
        "config_sources": config_sources,
        "configured_rule_counts": configured_rule_counts(
            config,
            gateway_rules=gateway_rules,
            ownership_rules=ownership_rules,
            mutation_rules=mutation_rules,
        ),
    }

    # Temporal deltas
    history = load_history(root)
    snapshot["has_history"] = len(history) > 0
    deltas = compute_deltas(snapshot, history, thresholds)

    if args.delta_only and not history:
        print("No history found. Run without --delta-only first.",
              file=sys.stderr)
        sys.exit(0)

    # Save snapshot
    if not args.no_save:
        save_snapshot(root, snapshot)

    # Report
    report = format_report(
        snapshot, deltas, git_data, import_data, structure_data,
        layer_rule_violations, instability, thresholds
    )
    print(report)

    # Exit code
    has_hard = (len(import_data.get("violations", [])) > 0
                or len(import_data.get("efferent_stable", [])) > 0
                or any(risk.get("severity") == "fail" for risk in structure_data.get("god_file_risks", []))
                or any(v.get("severity") == "fail" for v in structure_data.get("gateway_rule_violations", []))
                or any(v.get("severity") == "fail" for v in structure_data.get("ownership_rule_violations", []))
                or any(v.get("severity") == "fail" for v in structure_data.get("mutation_rule_violations", []))
                or any(risk.get("severity") == "fail" for risk in structure_data.get("fan_out_risks", []))
                or any(risk.get("severity") == "fail" for risk in structure_data.get("fan_in_risks", []))
                or any(cycle.get("severity") == "fail" for cycle in structure_data.get("dependency_cycles", []))
                or any(v.get("severity") == "fail" for v in structure_data.get("tool_drift_violations", []))
                or any(v.get("severity") == "fail" for v in layer_rule_violations))
    has_warn = (len(deltas) > 0
                or len(structure_data.get("god_function_risks", [])) > 0
                or len(structure_data.get("god_class_risks", [])) > 0
                or any(risk.get("severity") == "warn" for risk in structure_data.get("god_file_risks", []))
                or any(v.get("severity") == "warn" for v in structure_data.get("gateway_rule_violations", []))
                or any(v.get("severity") == "warn" for v in structure_data.get("ownership_rule_violations", []))
                or any(v.get("severity") == "warn" for v in structure_data.get("mutation_rule_violations", []))
                or any(risk.get("severity") == "warn" for risk in structure_data.get("fan_out_risks", []))
                or any(risk.get("severity") == "warn" for risk in structure_data.get("fan_in_risks", []))
                or any(cycle.get("severity") == "warn" for cycle in structure_data.get("dependency_cycles", []))
                or any(v.get("severity") == "warn" for v in layer_rule_violations))

    if args.ci:
        if has_hard:
            sys.exit(1)
        elif has_warn:
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
