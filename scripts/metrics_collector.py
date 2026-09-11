#!/usr/bin/env python3
"""metrics_collector.py - Unified ControlCoding metrics collection.

READ-ONLY script. Analyzes one or more projects and produces a unified report
with quantitative metrics for the Evidence Report.

Data sources:
  1. cc_hook_log.jsonl  - hook events (DENY, WARN, ALLOW)
  2. Static analysis     - files, tests, CC artifacts, structure
  3. Git log             - commit count, date range, contributor count

Usage:
  python metrics_collector.py <project_root> [<project_root2> ...]
  python metrics_collector.py /path/to/project1 /path/to/project2

Output: markdown report on stdout. Redirect to file with > report.md

Does NOT modify any files. Zero external dependencies (stdlib only).
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# ============================================================
# UTILITIES
# ============================================================

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def count_pattern(text: str, pattern: str, flags: int = 0) -> int:
    return len(re.findall(pattern, text, flags))


def glob_files(root: Path, pattern: str) -> list[Path]:
    return sorted(root.glob(pattern))


def run_cmd(cmd: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd), timeout=30)
        return r.stdout.strip()
    except Exception:
        return ""


# ============================================================
# HOOK LOG ANALYSIS
# ============================================================

def analyze_hook_log(root: Path) -> dict:
    """Analyze cc_hook_log.jsonl and return statistics."""
    log_path = root / "cc_hook_log.jsonl"
    result = {
        "exists": log_path.exists(),
        "total_events": 0,
        "by_decision": Counter(),
        "by_hook": Counter(),
        "by_target": Counter(),
        "first_event": None,
        "last_event": None,
        "events": [],
    }

    if not log_path.exists():
        return result

    for line in read_text(log_path).strip().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        result["total_events"] += 1
        result["by_decision"][record.get("decision", "UNKNOWN")] += 1
        result["by_hook"][record.get("hook", "unknown")] += 1

        target = record.get("target", "")
        # Normalize target to relative path
        target_short = target.replace("\\", "/")
        for prefix in [str(root).replace("\\", "/") + "/"]:
            if target_short.startswith(prefix):
                target_short = target_short[len(prefix):]
        result["by_target"][target_short] += 1

        ts = record.get("ts", "")
        if ts:
            if result["first_event"] is None or ts < result["first_event"]:
                result["first_event"] = ts
            if result["last_event"] is None or ts > result["last_event"]:
                result["last_event"] = ts

        result["events"].append(record)

    return result


# ============================================================
# STATIC ANALYSIS
# ============================================================

def analyze_project(root: Path) -> dict:
    """Generic static analysis of a project."""
    data = {
        "name": root.name,
        "root": str(root),
        "type": None,  # "cpp" or "python"
    }

    # Detect project type
    if (root / "CMakeLists.txt").exists():
        data["type"] = "cpp"
    elif (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        data["type"] = "python"

    # --- CC Artifacts ---
    cc_artifacts = {
        "CLAUDE.md": root / "CLAUDE.md",
        "check_boundaries.py": root / "hooks" / "check_boundaries.py",
        "check_dangerous_commands.py": root / "hooks" / "check_dangerous_commands.py",
        "hook_logger.py": root / "hooks" / "hook_logger.py",
        "settings.json": None,
        "devlog": None,
        "status": None,
    }

    # Find settings file
    for name in ["settings.local.json", "settings.json"]:
        p = root / ".claude" / name
        if p.exists():
            cc_artifacts["settings.json"] = p
            break

    # Find devlog
    for pattern in ["DEVLOG*", "docs/DEVLOG*", "Documenti/DEVLOG*"]:
        found = glob_files(root, pattern)
        if found:
            cc_artifacts["devlog"] = found[0]
            break

    # Find status
    for pattern in ["STATUS*", "docs/STATUS*", "Documenti/STATUS*"]:
        found = glob_files(root, pattern)
        if found:
            cc_artifacts["status"] = found[0]
            break

    data["artifacts"] = {}
    for name, path in cc_artifacts.items():
        if path is None:
            data["artifacts"][name] = {"exists": False}
        elif isinstance(path, Path):
            data["artifacts"][name] = {
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
            }

    # --- CLAUDE.md quality ---
    claude_md = read_text(root / "CLAUDE.md")
    cc_sections = [
        "principles", "protected zones", "invariants", "structure",
        "conventions", "dependencies", "workflow", "trigger",
        # Also check Italian variants for backwards compatibility
        "principi", "zone protette", "invarianti", "struttura",
    ]
    data["claude_md_lines"] = claude_md.count("\n") + 1 if claude_md else 0
    data["claude_md_sections"] = sum(
        1 for s in cc_sections if s.lower() in claude_md.lower()
    )
    data["claude_md_has_warn_instruction"] = (
        ("stop" in claude_md.lower() or "fermarsi" in claude_md.lower())
        and "warn" in claude_md.lower()
    )

    # --- Source files ---
    if data["type"] == "cpp":
        src_files = glob_files(root, "src/**/*.cpp") + glob_files(root, "src/**/*.h")
        test_files = glob_files(root, "tests/**/*.cpp")
        # Count Catch2 TEST_CASE
        test_count = 0
        for tf in test_files:
            test_count += count_pattern(read_text(tf), r"TEST_CASE\s*\(")
        data["src_files"] = len(src_files)
        data["test_files"] = len(test_files)
        data["test_count"] = test_count
    elif data["type"] == "python":
        EXCLUDE_DIRS = {"venv", ".venv", "env", ".env", "__pycache__", "node_modules",
                        ".git", "dist", ".eggs", ".tox", ".mypy_cache"}

        def is_excluded(p: Path) -> bool:
            parts = p.relative_to(root).parts
            # Check exact directory name matches
            if any(ex in parts for ex in EXCLUDE_DIRS):
                return True
            # Check for .egg-info directories (suffix match, not glob)
            if any(part.endswith(".egg-info") for part in parts):
                return True
            # Only exclude "build" as a direct child of the project root
            if parts and parts[0] == "build":
                return True
            return False

        src_files = glob_files(root, "src/**/*.py")
        src_files = [f for f in src_files if not is_excluded(f)
                     and "test" not in f.name.lower()]
        test_files = glob_files(root, "tests/**/*.py")
        test_files = [f for f in test_files if not is_excluded(f)]
        # Count test functions
        test_count = 0
        for tf in test_files:
            test_count += count_pattern(read_text(tf), r"def test_")
        data["src_files"] = len(src_files)
        data["test_files"] = len(test_files)
        data["test_count"] = test_count
    else:
        data["src_files"] = 0
        data["test_files"] = 0
        data["test_count"] = 0

    # --- Feature locks ---
    feature_locks = glob_files(root, "**/feature.lock")
    data["feature_locks"] = len(feature_locks)

    # --- Function registry ---
    registry = root / "FUNCTION_REGISTRY.yml"
    if not registry.exists():
        registry = root / "FUNCTION_REGISTRY.yaml"
    data["has_function_registry"] = registry.exists()
    if registry.exists():
        reg_text = read_text(registry)
        data["registry_entries"] = count_pattern(reg_text, r"^- name:", re.MULTILINE) or \
                                    count_pattern(reg_text, r"^  name:", re.MULTILINE) or \
                                    count_pattern(reg_text, r"name:")
    else:
        data["registry_entries"] = 0

    # --- Domain invariant tests ---
    invariant_count = 0
    for tf in glob_files(root, "**/test_*invariant*") + glob_files(root, "**/test_domain*"):
        invariant_count += count_pattern(read_text(tf), r"def test_|TEST_CASE\s*\(")
    data["invariant_tests"] = invariant_count

    # --- Hooks configured ---
    settings_content = ""
    for name in ["settings.local.json", "settings.json"]:
        p = root / ".claude" / name
        if p.exists():
            content = read_text(p)
            settings_content += content
    data["hooks_configured"] = "PreToolUse" in settings_content

    # --- Git stats ---
    data["git"] = {}
    if (root / ".git").exists():
        commit_count = run_cmd(["git", "rev-list", "--count", "HEAD"], root)
        data["git"]["commits"] = int(commit_count) if commit_count.isdigit() else 0

        first_commit = run_cmd(["git", "log", "--reverse", "--format=%ai", "-1"], root)
        last_commit = run_cmd(["git", "log", "--format=%ai", "-1"], root)
        data["git"]["first_commit"] = first_commit[:10] if first_commit else "N/A"
        data["git"]["last_commit"] = last_commit[:10] if last_commit else "N/A"

        authors = run_cmd(["git", "shortlog", "-sn", "--no-merges", "HEAD"], root)
        data["git"]["authors"] = len(authors.strip().splitlines()) if authors else 0
    else:
        data["git"]["commits"] = 0
        data["git"]["first_commit"] = "N/A"
        data["git"]["last_commit"] = "N/A"
        data["git"]["authors"] = 0

    # --- Hook log ---
    data["hook_log"] = analyze_hook_log(root)

    return data


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(projects: list[dict]) -> str:
    """Generate unified markdown report."""
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines.append("# ControlCoding - Metrics Report")
    lines.append(f"\n> Generated automatically by metrics_collector.py")
    lines.append(f"> Date: {now}")
    lines.append(f"> Projects analyzed: {len(projects)}")
    lines.append("")

    # --- Summary table ---
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | " + " | ".join(p["name"] for p in projects) + " |")
    lines.append("|---|" + "|".join("---" for _ in projects) + "|")

    rows = [
        ("Project type", lambda p: p.get("type", "N/A")),
        ("Source files", lambda p: str(p.get("src_files", 0))),
        ("Test files", lambda p: str(p.get("test_files", 0))),
        ("Total tests", lambda p: str(p.get("test_count", 0))),
        ("Domain invariant tests", lambda p: str(p.get("invariant_tests", 0))),
        ("Feature locks", lambda p: str(p.get("feature_locks", 0))),
        ("Function registry entries", lambda p: str(p.get("registry_entries", 0))),
        ("CLAUDE.md lines", lambda p: str(p.get("claude_md_lines", 0))),
        ("CLAUDE.md CC sections", lambda p: f"{p.get('claude_md_sections', 0)}/8"),
        ("WARN instruction in CLAUDE.md", lambda p: "Yes" if p.get("claude_md_has_warn_instruction") else "No"),
        ("Hooks configured", lambda p: "Yes" if p.get("hooks_configured") else "No"),
        ("Git commits", lambda p: str(p["git"]["commits"])),
        ("Development period", lambda p: f"{p['git']['first_commit']} - {p['git']['last_commit']}"),
    ]

    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(fn(p) for p in projects) + " |")

    lines.append("")

    # --- Hook log section ---
    lines.append("## Hook Events")
    lines.append("")

    any_events = any(p["hook_log"]["total_events"] > 0 for p in projects)
    if not any_events:
        lines.append("No hook events recorded. Possible causes:")
        lines.append("- Logging was installed recently")
        lines.append("- The AI did not attempt to violate boundaries (correct behavior)")
        lines.append("- Hooks have not triggered yet")
        lines.append("")
    else:
        lines.append("| Metric | " + " | ".join(p["name"] for p in projects) + " |")
        lines.append("|---|" + "|".join("---" for _ in projects) + "|")

        lines.append("| Total events | " + " | ".join(
            str(p["hook_log"]["total_events"]) for p in projects) + " |")
        lines.append("| DENY | " + " | ".join(
            str(p["hook_log"]["by_decision"].get("DENY", 0)) for p in projects) + " |")
        lines.append("| WARN | " + " | ".join(
            str(p["hook_log"]["by_decision"].get("WARN", 0)) for p in projects) + " |")
        lines.append("| First event | " + " | ".join(
            (p["hook_log"]["first_event"] or "N/A")[:19] for p in projects) + " |")
        lines.append("| Last event | " + " | ".join(
            (p["hook_log"]["last_event"] or "N/A")[:19] for p in projects) + " |")
        lines.append("")

        # Detail per project
        for p in projects:
            if p["hook_log"]["total_events"] > 0:
                lines.append(f"### {p['name']} - event detail")
                lines.append("")
                lines.append("| Timestamp | Decision | Hook | Target | Reason |")
                lines.append("|---|---|---|---|---|")
                for evt in p["hook_log"]["events"]:
                    ts = evt.get("ts", "")[:19]
                    dec = evt.get("decision", "")
                    hook = evt.get("hook", "")
                    target = evt.get("target", "").replace("\\", "/")
                    # Shorten target
                    for prefix in [str(Path(p["root"])).replace("\\", "/") + "/"]:
                        if target.startswith(prefix):
                            target = target[len(prefix):]
                    reason = evt.get("reason", "")
                    lines.append(f"| {ts} | {dec} | {hook} | {target} | {reason} |")
                lines.append("")

    # --- Artifacts section ---
    lines.append("## ControlCoding Artifacts")
    lines.append("")
    lines.append("| Artifact | " + " | ".join(p["name"] for p in projects) + " |")
    lines.append("|---|" + "|".join("---" for _ in projects) + "|")

    artifact_names = [
        "CLAUDE.md", "check_boundaries.py", "check_dangerous_commands.py",
        "hook_logger.py", "settings.json", "devlog", "status",
    ]
    for art in artifact_names:
        cells = []
        for p in projects:
            info = p.get("artifacts", {}).get(art, {})
            if info.get("exists"):
                size = info.get("size", 0)
                if size > 0:
                    cells.append(f"Yes ({size // 1024}KB)")
                else:
                    cells.append("Yes")
            else:
                cells.append("No")
        lines.append(f"| {art} | " + " | ".join(cells) + " |")

    lines.append("")

    # --- CC Maturity estimate ---
    lines.append("## CC Maturity Estimate")
    lines.append("")
    for p in projects:
        level = estimate_maturity(p)
        lines.append(f"**{p['name']}**: Level {level['level']} - {level['label']}")
        lines.append("")
        for note in level["notes"]:
            lines.append(f"  - {note}")
        lines.append("")

    return "\n".join(lines)


def estimate_maturity(p: dict) -> dict:
    """Estimate the CC maturity level of a project."""
    score = 0
    notes = []

    # L1: CLAUDE.md exists and is substantial
    if p.get("claude_md_lines", 0) > 50:
        score += 1
        notes.append("[L1] CLAUDE.md present and substantial ({} lines)".format(
            p["claude_md_lines"]))
    else:
        notes.append("[L1] CLAUDE.md absent or minimal")

    # L2: Hooks configured
    if p.get("hooks_configured"):
        score += 1
        notes.append("[L2] Boundary hooks configured")
    else:
        notes.append("[L2] Hooks not configured")

    # L2+: WARN instruction
    if p.get("claude_md_has_warn_instruction"):
        notes.append("[L2+] WARN reaction instruction present in CLAUDE.md")

    # L3: Domain invariant tests
    if p.get("invariant_tests", 0) >= 3:
        score += 1
        notes.append("[L3] Domain invariant tests present ({})".format(
            p["invariant_tests"]))
    else:
        notes.append("[L3] Insufficient invariant tests ({})".format(
            p.get("invariant_tests", 0)))

    # L4: Feature locks or condominium structure
    if p.get("feature_locks", 0) >= 2:
        score += 1
        notes.append("[L4] Feature locks active ({})".format(p["feature_locks"]))
    elif p.get("has_function_registry"):
        score += 1
        notes.append("[L4] Function registry present ({} entries)".format(
            p["registry_entries"]))
    else:
        notes.append("[L4] No feature locks or function registry")

    labels = {
        0: "Not adopted",
        1: "Base (CLAUDE.md)",
        2: "Operational (hooks active)",
        3: "Consolidated (invariants + hooks)",
        4: "Mature (full isolation)",
    }

    return {
        "level": score,
        "label": labels.get(score, "Unknown"),
        "notes": notes,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python metrics_collector.py <project_root> [<project_root2> ...]",
              file=sys.stderr)
        print("Example: python metrics_collector.py /path/to/project1 /path/to/project2",
              file=sys.stderr)
        sys.exit(1)

    projects = []
    for arg in sys.argv[1:]:
        root = Path(arg).resolve()
        if not root.is_dir():
            print(f"WARN: {arg} is not a directory, skipping", file=sys.stderr)
            continue
        print(f"Analyzing {root.name}...", file=sys.stderr)
        data = analyze_project(root)
        projects.append(data)

    if not projects:
        print("No projects found.", file=sys.stderr)
        sys.exit(1)

    report = generate_report(projects)
    print(report)


if __name__ == "__main__":
    main()
