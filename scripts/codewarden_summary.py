#!/usr/bin/env python3
"""codewarden_summary.py - Cumulative report from CodeWarden violation log.

Reads .controlcoding/codewarden_violations.jsonl (with legacy .claude fallback)
and produces a markdown summary with statistics, structured categories,
architecture tags, evidence sources, trends, and escalation candidates.

Usage:
    python scripts/codewarden_summary.py <project_root> [--period 30]

Output goes to stdout (redirect to file if needed).
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

CANONICAL_STORE_FILENAME = ".controlcoding/codewarden_violations.jsonl"
LEGACY_STORE_FILENAME = ".claude/codewarden_violations.jsonl"
ESCALATE_WARN_TO_DENY = 3
ESCALATE_CHRONIC = 5


def _format_label(value: str) -> str:
    return str(value or "unknown").replace("_", " ")


def control_plane_store_path(project_root: Path) -> Path:
    canonical = project_root / CANONICAL_STORE_FILENAME
    legacy = project_root / LEGACY_STORE_FILENAME
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def read_violations(store_path: Path, cutoff: datetime) -> list[dict]:
    """Read violations newer than cutoff. Skip corrupt lines."""
    if not store_path.exists():
        return []

    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    violations = []

    try:
        with open(store_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("timestamp", "") >= cutoff_str:
                        violations.append(record)
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        return []

    return violations


def generate_report(violations: list[dict], period_days: int,
                    store_path: Path) -> str:
    """Generate markdown report from violation records."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=period_days)
    lines = []

    lines.append("# CodeWarden Summary")
    lines.append("")
    lines.append(f"Period: {start.strftime('%Y-%m-%d')} to "
                 f"{now.strftime('%Y-%m-%d')} ({period_days} days)")
    lines.append(f"Store: {store_path}")
    lines.append("")

    if not violations:
        lines.append("No violations recorded in this period.")
        return "\n".join(lines)

    # --- Overview ---
    severity_counts = Counter(v.get("severity", "?") for v in violations)
    hook_counts = Counter(v.get("hook", "?") for v in violations)
    rules = set(v.get("rule_quoted", "?") for v in violations)

    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Total violations: {len(violations)}")
    lines.append(f"- Unique rules violated: {len(rules)}")

    sev_parts = [f"{c} {s}" for s, c in severity_counts.most_common()]
    lines.append(f"- Severity breakdown: {', '.join(sev_parts)}")

    hook_labels = {"review": "session end", "plan_review": "plan time"}
    hook_parts = [f"{c} {hook_labels.get(h, h)}"
                  for h, c in hook_counts.most_common()]
    lines.append(f"- Hook breakdown: {', '.join(hook_parts)}")
    lines.append("")

    # --- Structured review signals ---
    has_structured = any(
        v.get("category") or v.get("architecture_tag") or v.get("evidence_source")
        for v in violations
    )
    if has_structured:
        category_counts = Counter(
            str(v.get("category") or "legacy").strip().lower()
            for v in violations
        )
        architecture_tags = Counter(
            str(v.get("architecture_tag")).strip().lower()
            for v in violations
            if str(v.get("architecture_tag", "")).strip()
        )
        evidence_sources = Counter(
            str(v.get("evidence_source")).strip()
            for v in violations
            if str(v.get("evidence_source", "")).strip()
        )

        lines.append("## Structured Review Signals")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|---|---|")
        for category, count in category_counts.most_common():
            lines.append(f"| {_format_label(category)} | {count} |")
        lines.append("")

        if architecture_tags:
            lines.append("### Architecture Tags")
            lines.append("")
            lines.append("| Tag | Count |")
            lines.append("|---|---|")
            for tag, count in architecture_tags.most_common():
                lines.append(f"| {_format_label(tag)} | {count} |")
            lines.append("")

        if evidence_sources:
            lines.append("### Evidence Sources")
            lines.append("")
            lines.append("| Source | Count |")
            lines.append("|---|---|")
            for source, count in evidence_sources.most_common():
                lines.append(f"| {source} | {count} |")
            lines.append("")

    # --- By Rule ---
    by_rule: dict[str, list[dict]] = defaultdict(list)
    for v in violations:
        by_rule[v.get("rule_quoted", "?")].append(v)

    lines.append("## Violations by Rule")
    lines.append("")
    lines.append("| Rule | Count | Severity | Last seen | Files |")
    lines.append("|---|---|---|---|---|")

    for rule, entries in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        count = len(entries)
        severities = sorted(set(e.get("severity", "?") for e in entries))
        last = max(e.get("timestamp", "")[:10] for e in entries)
        files = sorted(set(e.get("file", "?") for e in entries))
        files_str = ", ".join(files[:3])
        if len(files) > 3:
            files_str += f" (+{len(files) - 3})"
        rule_short = rule[:60] + "..." if len(rule) > 60 else rule
        lines.append(f'| "{rule_short}" | {count} | {"/".join(severities)} '
                     f"| {last} | {files_str} |")

    lines.append("")

    # --- By File ---
    by_file: dict[str, list[dict]] = defaultdict(list)
    for v in violations:
        by_file[v.get("file", "?")].append(v)

    lines.append("## Violations by File")
    lines.append("")
    lines.append("| File | Count | Rules |")
    lines.append("|---|---|---|")

    for file, entries in sorted(by_file.items(), key=lambda x: -len(x[1])):
        count = len(entries)
        file_rules = sorted(set(e.get("rule_quoted", "?")[:40] for e in entries))
        rules_str = ", ".join(f'"{r}"' for r in file_rules[:3])
        if len(file_rules) > 3:
            rules_str += f" (+{len(file_rules) - 3})"
        lines.append(f"| {file} | {count} | {rules_str} |")

    lines.append("")

    # --- Escalation Candidates ---
    candidates = []
    for rule, entries in by_rule.items():
        count = len(entries)
        if count >= ESCALATE_WARN_TO_DENY:
            level = "CHRONIC" if count >= ESCALATE_CHRONIC else "DENY"
            candidates.append((rule, count, level))

    if candidates:
        lines.append("## Escalation Candidates")
        lines.append("")
        lines.append("Rules that would escalate with `CODEWARDEN_ESCALATE=true`:")
        lines.append("")
        for rule, count, level in sorted(candidates, key=lambda x: -x[1]):
            rule_short = rule[:60] + "..." if len(rule) > 60 else rule
            lines.append(f'- "{rule_short}": {count} violations -> {level}')
        lines.append("")

    # --- Timeline (weekly) ---
    lines.append("## Timeline (weekly)")
    lines.append("")
    lines.append("| Week | Violations |")
    lines.append("|---|---|")

    week_counts: dict[str, int] = defaultdict(int)
    for v in violations:
        ts = v.get("timestamp", "")[:10]
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d")
            week_start = dt - timedelta(days=dt.weekday())
            week_label = (f"{week_start.strftime('%b %d')}-"
                         f"{(week_start + timedelta(days=6)).strftime('%b %d')}")
            week_counts[week_start.strftime("%Y-%m-%d") + "|" + week_label] += 1
        except ValueError:
            continue

    for key in sorted(week_counts.keys(), reverse=True):
        label = key.split("|", 1)[1]
        lines.append(f"| {label} | {week_counts[key]} |")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a cumulative CodeWarden violation report.")
    parser.add_argument("project_root", type=Path,
                        help="Path to the project root (where .controlcoding/ lives)")
    parser.add_argument("--period", type=int, default=30,
                        help="Days to look back (default: 30)")
    args = parser.parse_args()

    if not args.project_root.is_dir():
        print(f"Error: {args.project_root} is not a directory",
              file=sys.stderr)
        sys.exit(1)

    store_path = control_plane_store_path(args.project_root)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.period)
    violations = read_violations(store_path, cutoff)
    report = generate_report(violations, args.period, store_path)
    print(report)


if __name__ == "__main__":
    main()
