#!/usr/bin/env python3
"""verification_report.py - Report generation for verification agent.

Produces JSON and markdown reports from criteria data.
Python stdlib only - no external dependencies.
"""

from datetime import datetime


def generate_json_report(criteria: list, design: str = "") -> dict:
    """Build structured JSON report dict from criteria list."""
    total = len(criteria)
    passed = sum(1 for c in criteria if c.status == "pass")
    failed = sum(1 for c in criteria if c.status == "fail")
    blocked = sum(1 for c in criteria if c.status == "blocked")
    pending = sum(1 for c in criteria if c.status == "pending")
    rate = f"{passed / total * 100:.1f}%" if total > 0 else "0.0%"
    layers = set(c.layer for c in criteria if c.layer > 0)
    layers_done = 0
    for layer in sorted(layers):
        lc = [c for c in criteria if c.layer == layer]
        if all(c.status == "pass" for c in lc):
            layers_done += 1
        else:
            break
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "design_document": design,
        "total_criteria": total,
        "passed": passed,
        "failed": failed,
        "blocked": blocked,
        "pending": pending,
        "pass_rate": rate,
        "layers_completed": layers_done,
        "criteria": [_criterion_to_report(c) for c in criteria],
    }


def _criterion_to_report(c) -> dict:
    return {
        "id": c.id,
        "original_text": c.original_text,
        "source_section": c.source_section,
        "method": c.verification_method,
        "status": c.status,
        "attempts": c.attempts,
        "layer": c.layer,
        "evidence": c.evidence,
        "reference_screenshot": c.reference_screenshot,
        "notes": c.notes,
        "blocks": c.blocks,
    }


def generate_markdown_report(report: dict) -> str:
    """Generate human-readable markdown report."""
    lines = [
        f"# Verification Report - {report['timestamp']}",
        "",
        "## Summary",
        f"- Design document: {report.get('design_document', 'N/A')}",
        f"- Criteria extracted: {report['total_criteria']}",
        f"- Passed: {report['passed']} ({report['pass_rate']})",
        f"- Failed: {report['failed']}",
        f"- Blocked: {report['blocked']}",
        f"- Pending: {report['pending']}",
        f"- Layers completed: {report['layers_completed']}",
        "",
    ]
    # Passed
    passed = [c for c in report["criteria"] if c["status"] == "pass"]
    if passed:
        lines.append("## Passed Criteria")
        for c in passed:
            ev = ", ".join(c["evidence"]) if c["evidence"] else "N/A"
            lines.append(f'- [{c["id"]}] "{c["original_text"]}" - PASS')
            lines.append(f"  Evidence: {ev}")
        lines.append("")
    # Failed
    failed = [c for c in report["criteria"] if c["status"] == "fail"]
    if failed:
        lines.append("## Failed Criteria")
        for c in failed:
            ev = ", ".join(c["evidence"]) if c["evidence"] else "N/A"
            lines.append(f'- [{c["id"]}] "{c["original_text"]}" - FAIL ({c["attempts"]} attempts)')
            lines.append(f"  Evidence: {ev}")
            if c["notes"]:
                lines.append(f"  Diagnosis: {c['notes']}")
        lines.append("")
    # Blocked
    blocked = [c for c in report["criteria"] if c["status"] == "blocked"]
    if blocked:
        lines.append("## Blocked Criteria")
        for c in blocked:
            lines.append(f'- [{c["id"]}] "{c["original_text"]}" - BLOCKED')
            if c["notes"]:
                lines.append(f"  Reason: {c['notes']}")
        lines.append("")
    # Pending
    pending = [c for c in report["criteria"] if c["status"] == "pending"]
    if pending:
        lines.append("## Pending Criteria")
        for c in pending:
            lines.append(f'- [{c["id"]}] "{c["original_text"]}" - PENDING')
        lines.append("")
    return "\n".join(lines)
