#!/usr/bin/env python3
"""violation_store.py - Cross-session violation memory for CodeWarden.

Provides a lightweight JSONL-based persistence layer that records every
violation CodeWarden finds. Enables cross-session awareness: CodeWarden
can report "this rule was violated 3 times in the last 2 weeks" and
optionally escalate severity for chronic violations.

Storage: .controlcoding/codewarden_violations.jsonl (one JSON object per line).

Not a standalone script. Imported by codewarden_review.py and
codewarden_plan_review.py.

Design principles:
  - Zero dependencies beyond stdlib (json, pathlib, datetime)
  - Fail-safe: if store is missing or corrupt, CodeWarden works as before
  - Lightweight: JSONL parses line-by-line, no indexing needed
  - Privacy: store stays local in the control-plane dir, never committed to git

Environment variables:
  CODEWARDEN_ESCALATE   "true" to enable severity escalation (default: off)
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Store location relative to project root
STORE_FILENAME = ".controlcoding/codewarden_violations.jsonl"
LEGACY_STORE_FILENAME = ".claude/codewarden_violations.jsonl"

# How far back to look for violation history
HISTORY_DAYS = 30

# Escalation thresholds (only active when CODEWARDEN_ESCALATE=true)
ESCALATE_WARN_TO_DENY = 3   # violations of same rule in HISTORY_DAYS
ESCALATE_CHRONIC = 5         # violations to flag as "chronic"

# Valid severity levels (v3.1 CWViolation schema)
CW_SEVERITIES = {"critical", "high", "medium", "low"}
# Valid categories (v3.1 CWViolation schema)
CW_CATEGORIES = {
    "architecture", "domain_invariant", "coding_convention",
    "security", "resource_management", "style", "impact",
}
CW_ARCHITECTURE_TAGS = {
    "boundary_violation",
    "layer_violation",
    "cohesion_smell",
    "god_file_risk",
    "coupling_regression",
    "contract_erosion",
    "misplaced_logic",
    "responsibility_accretion",
}


def _store_path(project_root: Path) -> Path:
    """Return the full path to the violation store."""
    canonical = project_root / STORE_FILENAME
    legacy = project_root / LEGACY_STORE_FILENAME
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    if canonical.parent.exists():
        return canonical
    if legacy.parent.exists():
        return legacy
    return canonical


def record_violation(
    project_root: Path,
    hook: str,
    rule_quoted: str,
    file: str,
    severity: str,
    backend: str,
) -> None:
    """Append a violation record to the store.

    Silently does nothing if the store directory doesn't exist or the
    write fails. CodeWarden must never crash because of the store.
    """
    store = _store_path(project_root)
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hook": hook,
        "rule_quoted": rule_quoted[:200],  # truncate to keep store manageable
        "file": file,
        "severity": severity,
        "backend": backend,
    }

    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        with open(store, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # fail-safe: store write failures are silent


def read_recent_violations(project_root: Path) -> list[dict]:
    """Read violations from the last HISTORY_DAYS days.

    Returns a list of dicts. Returns empty list if store is missing,
    empty, or corrupt. Skips individual corrupt lines silently.
    """
    store = _store_path(project_root)
    if not store.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    violations = []

    try:
        with open(store, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("timestamp", "") >= cutoff_str:
                        violations.append(record)
                except (json.JSONDecodeError, KeyError):
                    continue  # skip corrupt lines
    except OSError:
        return []

    return violations


def summarize_history(project_root: Path) -> str:
    """Generate a human-readable summary of recent violations.

    Returns a markdown section suitable for injection into the CodeWarden
    review prompt. Returns empty string if no recent violations.
    """
    violations = read_recent_violations(project_root)
    if not violations:
        return ""

    # Group by rule
    by_rule: dict[str, list[dict]] = {}
    for v in violations:
        rule = v.get("rule_quoted", "unknown")
        by_rule.setdefault(rule, []).append(v)

    lines = [
        f"## Recent Violation History (last {HISTORY_DAYS} days)",
        "",
    ]

    for rule, entries in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        count = len(entries)
        files = sorted(set(e.get("file", "?") for e in entries))
        files_str = ", ".join(files[:5])
        if len(files) > 5:
            files_str += f" (+{len(files) - 5} more)"
        last = max(e.get("timestamp", "") for e in entries)
        lines.append(f'- Rule "{rule}" violated {count}x (files: {files_str}) - last: {last}')

    lines.append("")
    lines.append("Consider escalating severity for repeatedly violated rules.")

    return "\n".join(lines)


def should_escalate(project_root: Path, rule_quoted: str) -> str | None:
    """Check if a rule should be escalated based on violation history.

    Returns:
      - "deny" if the rule should be escalated from warn to deny
      - "chronic" if the rule is chronically violated
      - None if no escalation needed

    Only active when CODEWARDEN_ESCALATE=true.
    """
    if os.environ.get("CODEWARDEN_ESCALATE", "").lower() != "true":
        return None

    violations = read_recent_violations(project_root)
    count = sum(1 for v in violations if v.get("rule_quoted") == rule_quoted)

    if count >= ESCALATE_CHRONIC:
        return "chronic"
    if count >= ESCALATE_WARN_TO_DENY:
        return "deny"
    return None


# ---------------------------------------------------------------------------
# Structured CWViolation support (v3.1 / Sprint 2a)
# ---------------------------------------------------------------------------

def _max_cw_num(project_root: Path) -> int:
    """Find the highest CW-NNN number from existing violations."""
    violations = read_recent_violations(project_root)
    max_num = 0
    for v in violations:
        vid = v.get("id", "")
        m = re.match(r"^CW-(\d+)$", vid)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return max_num


def record_structured_violations(
    project_root: Path,
    violations: list[dict],
    hook: str = "review",
    backend: str = "",
) -> list[dict]:
    """Record a list of structured CWViolation records.

    Each violation dict should have: description, rule, file, line,
    severity, category, suggested_fix, claude_md_section.
    Missing fields get defaults. Returns the list with assigned IDs.

    Backward compatible: each record also contains the legacy fields
    (hook, rule_quoted, timestamp, backend) so older code can read it.
    """
    store = _store_path(project_root)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    next_num = _max_cw_num(project_root) + 1

    for v in violations:
        cw_id = f"CW-{next_num:03d}"
        next_num += 1

        severity = v.get("severity", "medium").lower()
        if severity not in CW_SEVERITIES:
            severity = "medium"

        category = v.get("category", "architecture").lower()
        if category not in CW_CATEGORIES:
            category = "architecture"

        architecture_tag = str(v.get("architecture_tag", "")).strip().lower()
        if category != "architecture" or architecture_tag not in CW_ARCHITECTURE_TAGS:
            architecture_tag = ""
        evidence = str(v.get("evidence", "")).strip()
        evidence_source = str(v.get("evidence_source", "")).strip()

        record = {
            # v3.1 CWViolation fields
            "id": cw_id,
            "rule": v.get("rule", ""),
            "file": v.get("file", ""),
            "line": v.get("line", 0),
            "severity": severity,
            "category": category,
            "description": v.get("description", ""),
            "suggested_fix": v.get("suggested_fix", ""),
            "claude_md_section": v.get("claude_md_section", ""),
            # Legacy fields (backward compat)
            "timestamp": ts,
            "hook": hook,
            "rule_quoted": v.get("description", "")[:200],
            "backend": backend,
        }
        if architecture_tag:
            record["architecture_tag"] = architecture_tag
        if evidence:
            record["evidence"] = evidence[:500]
        if evidence_source:
            record["evidence_source"] = evidence_source[:100]
        results.append(record)

    # Write all at once
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        with open(store, "a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
    except OSError:
        pass  # fail-safe

    return results


def parse_structured_review(raw_response: str) -> list[dict] | None:
    """Parse an LLM response containing structured JSON violations.

    The LLM is prompted to return a JSON object with a "violations" array.
    This function extracts it, handling markdown code fences and partial JSON.

    Returns:
      - list[dict]: parsed violations (may be empty [] if JSON valid but no violations)
      - None: if no valid JSON structure was found (triggers legacy fallback)
    """
    text = raw_response.strip()

    # Try to extract JSON from code fences
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()

    # Try parsing as JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "violations" in data:
            return data["violations"]
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Fallback: try to find a JSON array in the text
    match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None  # no valid JSON found - caller uses legacy path
