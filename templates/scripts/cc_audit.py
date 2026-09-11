#!/usr/bin/env python3
"""cc_audit.py - ControlCoding Compliance Audit (generic template).

READ-ONLY script. Analyzes a project for ControlCoding v2 compliance and
produces a markdown report on stdout. Does NOT modify any files.

Usage: python cc_audit.py [project_root]
       If project_root is not specified, uses the current directory.

Output: markdown report on stdout.

Customization:
  1. Edit the CONFIGURATION section below to match your project
  2. Rename to cc_audit_<yourproject>.py
  3. Run periodically to track CC adoption progress
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

# ============================================================
# CONFIGURATION - Edit these to match your project
# ============================================================

# Project name (used in report header)
PROJECT_NAME = "[YOUR_PROJECT]"

# How to detect project root (looks for any of these files)
ROOT_MARKERS = ["pyproject.toml", "package.json", "Cargo.toml", "go.mod", "CLAUDE.md"]

# Expected CC artifacts (relative path -> description)
EXPECTED_ARTIFACTS = {
    "CLAUDE.md": "Project constitution (CLAUDE.md)",
    ".claude/settings.local.json": "Claude Code hook settings",
    "hooks/check_boundaries.py": "Boundary enforcement hook",
    "hooks/check_dangerous_commands.py": "Dangerous command blocker",
    # Uncomment / add your own:
    # "docs/STATUS.md": "Project status document",
    # "docs/DEVLOG.md": "Development log",
}

# Zone mapping: path -> (cc_level, description)
# cc_level is one of: "stable", "shared", "features", "workspace"
ZONE_MAPPING = {
    # "src/core/": ("stable", "Core data models and interfaces"),
    # "src/auth/": ("stable", "Authentication module"),
    # "src/utils/": ("shared", "Shared utility functions"),
    # "src/features/": ("features", "Active development"),
    # "src/experimental/": ("workspace", "Prototypes"),
}

# Import direction rules (Python projects only).
# For each zone, list the zones it must NOT import from.
# Example: core should not import from plugins or ui.
# Set to empty dict {} to skip this check.
#
# Package name used in import statements (e.g., "from mypackage.core import ...")
PACKAGE_NAME = ""  # e.g., "mypackage"
SOURCE_DIR = ""  # e.g., "src/mypackage" (relative to project root)
IMPORT_RULES = {
    # "core": {"config", "tools", "plugins", "ui"},
    # "config": {"tools", "plugins", "ui"},
    # "tools": {"plugins", "ui"},
}

# Expected sections in CLAUDE.md (substring -> section name)
# Works with both English and localized CLAUDE.md files.
CLAUDE_MD_SECTIONS = {
    "Architecture Rules": ["architecture rules", "regole architettura"],
    "Module Boundaries": ["module boundaries", "zone protett"],
    "Domain Invariants": ["domain invariants", "invarianti"],
    "Operative Rules": ["operative rules", "regole operative"],
    "Commit Ceremony": ["commit ceremony", "commit"],
}

# ============================================================
# END CONFIGURATION
# ============================================================


def find_project_root(start: str = ".") -> Path:
    """Find project root by looking for marker files."""
    p = Path(start).resolve()
    for marker in ROOT_MARKERS:
        if (p / marker).exists():
            return p
    if len(sys.argv) > 1:
        p = Path(sys.argv[1]).resolve()
        for marker in ROOT_MARKERS:
            if (p / marker).exists():
                return p
    print(
        f"ERROR: No project root marker found ({', '.join(ROOT_MARKERS)}). "
        "Run from project root or pass the path as argument.",
        file=sys.stderr,
    )
    sys.exit(1)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def count_pattern(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))


# ============================================================
# CHECK 1: CC artifacts present
# ============================================================
def check_artifacts(root: Path) -> tuple[list[str], int]:
    results = []
    issues = 0
    for path, desc in EXPECTED_ARTIFACTS.items():
        if (root / path).exists():
            results.append(f"  - [OK] {desc} (`{path}`)")
        else:
            results.append(f"  - [MISSING] {desc} (`{path}`)")
            issues += 1
    return results, issues


# ============================================================
# CHECK 2: Zone mapping
# ============================================================
def check_zone_mapping(root: Path) -> list[str]:
    results = []
    if not ZONE_MAPPING:
        return ["  - [SKIP] No zones configured. Edit ZONE_MAPPING to enable."]
    for path, (cc_level, desc) in ZONE_MAPPING.items():
        full = root / path
        if full.exists():
            py_count = len(list(full.rglob("*.py")))
            results.append(
                f"  - [OK] `{path}` ({cc_level}) - {py_count} .py files - {desc}"
            )
        else:
            results.append(f"  - [MISSING] `{path}` ({cc_level}) - {desc}")
    return results


# ============================================================
# CHECK 3: Hook configuration
# ============================================================
def check_hooks(root: Path) -> list[str]:
    results = []
    settings_path = root / ".claude" / "settings.local.json"
    if not settings_path.exists():
        return ["  - [MISSING] .claude/settings.local.json not found"]

    try:
        settings = json.loads(read_text(settings_path))
    except Exception as e:
        return [f"  - [ERROR] Parse settings: {e}"]

    hooks = settings.get("hooks", {})
    pre = hooks.get("PreToolUse", [])

    if not pre:
        results.append("  - [MISSING] No PreToolUse hooks configured")
    else:
        for h in pre:
            matcher = h.get("matcher", "?")
            inner_hooks = h.get("hooks", [])
            cmd = inner_hooks[0].get("command", "?") if inner_hooks else "?"
            results.append(
                f"  - [OK] PreToolUse: matcher=`{matcher}`, command=`{cmd}`"
            )

    # Check boundary hook
    boundary_path = root / "hooks" / "check_boundaries.py"
    if boundary_path.exists():
        text = read_text(boundary_path)
        # Count configured protected zones (non-comment lines with tuples)
        zone_count = count_pattern(text, r'^\s+\("[^#]', )
        results.append(f"  - [INFO] Boundary hook: {zone_count} protected zone(s)")
    else:
        results.append("  - [MISSING] hooks/check_boundaries.py")

    # Check dangerous commands hook
    danger_path = root / "hooks" / "check_dangerous_commands.py"
    if danger_path.exists():
        text = read_text(danger_path)
        blocked = count_pattern(text, r'\(r"')
        results.append(f"  - [INFO] Dangerous commands hook: {blocked} blocked pattern(s)")
    else:
        results.append("  - [MISSING] hooks/check_dangerous_commands.py")

    return results


# ============================================================
# CHECK 4: Import direction analysis (Python)
# ============================================================
def check_import_direction(root: Path) -> list[str]:
    results = []
    if not IMPORT_RULES or not PACKAGE_NAME or not SOURCE_DIR:
        return ["  - [SKIP] Import rules not configured. Edit IMPORT_RULES to enable."]

    violations = []
    src = root / SOURCE_DIR
    if not src.exists():
        return [f"  - [ERROR] {SOURCE_DIR}/ not found"]

    import_re = re.compile(rf"(?:from|import)\s+{re.escape(PACKAGE_NAME)}\.(\w+)")

    for zone, forbidden_zones in IMPORT_RULES.items():
        zone_path = src / zone
        if not zone_path.exists():
            continue
        for py_file in zone_path.rglob("*.py"):
            text = read_text(py_file)
            for match in import_re.finditer(text):
                imported_zone = match.group(1)
                if imported_zone in forbidden_zones:
                    rel = py_file.relative_to(root).as_posix()
                    line_num = text[: match.start()].count("\n") + 1
                    violations.append(
                        f"    `{rel}:{line_num}` imports from `{imported_zone}` "
                        f"({zone} -> {imported_zone})"
                    )

    if violations:
        results.append(
            f"  - [VIOLATION] {len(violations)} import direction violation(s):"
        )
        results.extend(violations[:20])
        if len(violations) > 20:
            results.append(f"    ... and {len(violations) - 20} more")
    else:
        results.append("  - [OK] No import direction violations detected")

    return results


# ============================================================
# CHECK 5: Domain invariant tests
# ============================================================
def check_domain_invariants(root: Path) -> list[str]:
    results = []
    inv_path = root / "tests" / "test_domain_invariants.py"
    if not inv_path.exists():
        return ["  - [MISSING] tests/test_domain_invariants.py"]

    text = read_text(inv_path)
    test_count = count_pattern(text, r"def test_")
    class_count = count_pattern(text, r"class Test")

    results.append(
        f"  - [OK] test_domain_invariants.py: {test_count} test(s) in {class_count} class(es)"
    )

    if test_count >= 30:
        results.append(
            f"  - [TRIGGER] {test_count} invariant tests >= 30: consider automated Stop hook"
        )
    else:
        results.append(
            f"  - [INFO] {test_count}/30 toward Stop hook automation trigger"
        )

    return results


# ============================================================
# CHECK 6: Test suite
# ============================================================
def check_tests(root: Path) -> list[str]:
    results = []
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return ["  - [MISSING] tests/ directory not found"]

    test_files = list(tests_dir.rglob("test_*.py"))
    total_tests = 0
    for f in test_files:
        text = read_text(f)
        total_tests += count_pattern(text, r"(?:def test_|async def test_)")

    results.append(
        f"  - [INFO] {len(test_files)} test file(s), ~{total_tests} test function(s)"
    )

    conftest = tests_dir / "conftest.py"
    if conftest.exists():
        results.append("  - [OK] conftest.py present")

    return results


# ============================================================
# CHECK 7: CLAUDE.md quality
# ============================================================
def check_claude_md(root: Path) -> list[str]:
    results = []
    claude_path = root / "CLAUDE.md"
    if not claude_path.exists():
        return ["  - [MISSING] CLAUDE.md"]

    text = read_text(claude_path)
    lines = text.count("\n")
    results.append(f"  - [INFO] CLAUDE.md: {lines} lines")

    if lines > 200:
        results.append(
            f"  - [WARN] CLAUDE.md exceeds 200 lines ({lines}). "
            "Consider extracting details into linked docs."
        )

    for section, keywords in CLAUDE_MD_SECTIONS.items():
        found = any(kw in text.lower() for kw in keywords)
        status = "OK" if found else "MISSING"
        results.append(f"  - [{status}] Section: {section}")

    return results


# ============================================================
# CHECK 8: General metrics
# ============================================================
def check_metrics(root: Path) -> list[str]:
    results = []

    # Count source files (exclude hidden dirs, __pycache__, node_modules, etc.)
    exclude = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"}
    py_files = [
        f
        for f in root.rglob("*.py")
        if not any(part in exclude for part in f.relative_to(root).parts)
        and not f.name.startswith(".")
    ]
    total_lines = sum(read_text(f).count("\n") for f in py_files)

    results.append(f"  - {len(py_files)} .py file(s) in project")
    results.append(f"  - ~{total_lines:,} total lines")

    # Per-zone breakdown if zones are configured
    if ZONE_MAPPING:
        for path in ZONE_MAPPING:
            zone_path = root / path
            if zone_path.exists():
                zf = list(zone_path.rglob("*.py"))
                zl = sum(read_text(f).count("\n") for f in zf)
                results.append(f"  - {path}: {len(zf)} file(s), ~{zl:,} lines")

    return results


# ============================================================
# REPORT
# ============================================================
def main():
    root = find_project_root(sys.argv[1] if len(sys.argv) > 1 else ".")

    print(f"# ControlCoding Compliance Audit - {PROJECT_NAME}")
    print(f"**Project**: {root.name}")
    print(f"**Root**: `{root}`")
    print(f"**Date**: {date.today()}")
    print()

    sections = [
        ("1. ControlCoding Artifacts", check_artifacts),
        ("2. Zone Mapping", check_zone_mapping),
        ("3. Hook Configuration", check_hooks),
        ("4. Import Direction (Python)", check_import_direction),
        ("5. Domain Invariant Tests", check_domain_invariants),
        ("6. Test Suite", check_tests),
        ("7. CLAUDE.md Quality", check_claude_md),
        ("8. General Metrics", check_metrics),
    ]

    all_issues = 0
    for title, fn in sections:
        print(f"## {title}")
        print()
        result = fn(root)
        if isinstance(result, tuple):
            lines, issues = result
            all_issues += issues
        else:
            lines = result
        for line in lines:
            print(line)
            if "[VIOLATION]" in line or "[ERROR]" in line or "[MISSING]" in line:
                all_issues += 1
        print()

    print("---")
    print()
    if all_issues == 0:
        print("**Result**: No issues found. Project is ControlCoding v2 compliant.")
    else:
        print(
            f"**Result**: {all_issues} issue(s) found. "
            "Review sections marked [VIOLATION], [ERROR], or [MISSING]."
        )
    print()
    print("*Report generated by cc_audit.py (read-only, no files modified)*")


if __name__ == "__main__":
    main()
