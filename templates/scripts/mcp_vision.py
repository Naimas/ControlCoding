#!/usr/bin/env python3
"""mcp_vision.py - Vision Agent MCP server.

Exposes verification skills as MCP tools that other AI sessions
can call programmatically. Each tool wraps verification_agent.py
functions - no business logic here.

Dependencies: fastmcp (consistent with other CC MCP servers)
"""

import json
import sys
from pathlib import Path

from fastmcp import FastMCP

# Local imports (same directory)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verification_agent as va
from verification_report import generate_json_report, generate_markdown_report

mcp = FastMCP("vision")


@mcp.tool()
def vision_add_criterion(
    criterion_id: str,
    text: str,
    method: str = "visual",
    layer: int = 1,
    section: str = "",
    blocks: str = "",
    steps: str = "",
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Register a new verification criterion.

    The AI extracts criteria from the design document and calls this
    for each one. Text must be verbatim from the design doc.

    Args:
        criterion_id: Unique ID (e.g., C-01)
        text: Verbatim requirement text from design document
        method: Verification method (visual, functional, code, numerical, architectural)
        layer: Development layer (1=primitive, 2=structure, 3=polish, 0=no layers)
        section: Source section in design document
        blocks: Comma-separated IDs that depend on this criterion
        steps: Semicolon-separated verification steps
        criteria_path: Path to criteria.json
    """
    criteria = va.load_criteria(criteria_path)
    if va.find_criterion(criteria, criterion_id):
        return f"ERROR: criterion {criterion_id} already exists"
    if method not in va.VALID_METHODS:
        return f"ERROR: invalid method {method}"
    blocks_list = [b.strip() for b in blocks.split(",") if b.strip()] if blocks else []
    steps_list = [s.strip() for s in steps.split(";") if s.strip()] if steps else []
    c = va.Criterion(id=criterion_id, original_text=text,
                     source_section=section, verification_method=method,
                     verification_steps=steps_list, layer=layer, blocks=blocks_list)
    criteria.append(c)
    va.save_criteria(criteria, criteria_path)
    va._log_event("criterion_created", "verification_engine",
                  related_ids=[c.id],
                  details={"method": method, "layer": layer})
    return f"Added {c.id}: \"{text[:80]}...\" (method={method}, layer={layer})"


@mcp.tool()
def vision_update(
    criterion_id: str,
    status: str,
    evidence: str = "",
    notes: str = "",
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Update a criterion's verification status.

    Args:
        criterion_id: Criterion ID to update
        status: New status (pass, fail, blocked, wont_fix)
        evidence: Evidence path (required for pass)
        notes: Diagnosis notes (required for fail/blocked)
        criteria_path: Path to criteria.json
    """
    criteria = va.load_criteria(criteria_path)
    c = va.find_criterion(criteria, criterion_id)
    if not c:
        return f"ERROR: criterion {criterion_id} not found"
    if status not in va.VALID_STATUSES:
        return f"ERROR: invalid status {status}"
    if status == "pass" and not evidence:
        return "ERROR: evidence required when status=pass"
    if status in ("fail", "blocked") and not notes:
        return f"ERROR: notes required when status={status}"
    old = c.status
    c.status = status
    c.attempts += 1
    if evidence:
        c.evidence.append(evidence)
    if notes:
        c.notes = notes
    if status == "pass" and evidence:
        c.reference_screenshot = evidence
    va.resolve_blocked(criteria)
    va.save_criteria(criteria, criteria_path)
    event_type = {"pass": "criterion_passed", "fail": "criterion_failed"}.get(
        status, "criterion_updated")
    va._log_event(event_type, "verification_engine",
                  related_ids=[c.id],
                  details={"old_status": old, "new_status": status})
    return f"Updated {c.id}: {old} -> {status} (attempt {c.attempts})"


@mcp.tool()
def vision_next(
    layer: int = 0,
    max_attempts: int = va.DEFAULT_MAX_ATTEMPTS,
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Get the next criterion to implement/verify (build-mode).

    Returns the next criterion based on DAG dependencies, layer filter,
    and priority (functional before visual).

    Args:
        layer: Layer filter (0=all, 1/2/3=specific)
        max_attempts: Max attempts per criterion
        criteria_path: Path to criteria.json
    """
    criteria = va.load_criteria(criteria_path)
    if not criteria:
        return "No criteria found. Extract criteria first."
    va.resolve_blocked(criteria)
    va.save_criteria(criteria, criteria_path)
    ready = va.get_dag_ready(criteria, layer)
    pending = [c for c in ready if c.status == "pending"]
    failed = [c for c in ready if c.status == "fail" and c.attempts < max_attempts]
    mo = {"functional": 0, "code": 1, "numerical": 2, "architectural": 3, "visual": 4}
    pending.sort(key=lambda c: mo.get(c.verification_method, 99))
    failed.sort(key=lambda c: mo.get(c.verification_method, 99))
    candidates = pending + failed
    if not candidates:
        ls = va.layer_summary(criteria, layer)
        if ls["passed"] == ls["total"] and ls["total"] > 0:
            return ("ALL COMPLETE" if layer == 0 else
                    f"Layer {layer} complete ({ls['passed']}/{ls['total']}). "
                    f"Ready for Layer {layer + 1}.")
        return (f"No actionable criteria (layer={layer}). "
                f"{ls['blocked']} blocked, {ls['failed']} failed at max attempts.")
    c = candidates[0]
    lines = [f'{c.id}: "{c.original_text}"',
             f"Method: {c.verification_method} | Layer: {c.layer} | "
             f"Attempts: {c.attempts}/{max_attempts}"]
    if c.verification_steps:
        lines.append(f"Steps: {'; '.join(c.verification_steps)}")
    if c.verification_method == "architectural":
        lines.append("ARCHITECTURAL: Trace this pattern across the ENTIRE codebase.")
    return "\n".join(lines)


@mcp.tool()
def vision_check_regression(
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """List all passed criteria that should be re-verified for regression.

    Args:
        criteria_path: Path to criteria.json
    """
    criteria = va.load_criteria(criteria_path)
    passed = va.check_regression(criteria)
    if not passed:
        return "No passed criteria to check for regression."
    lines = [f"Regression check: {len(passed)} criteria to re-verify"]
    for c in passed:
        ref = f" (ref: {c.reference_screenshot})" if c.reference_screenshot else ""
        lines.append(f'  {c.id}: "{c.original_text[:60]}..."{ref}')
    return "\n".join(lines)


@mcp.tool()
def vision_report(
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
    output_dir: str = "devlog/",
    design: str = "",
) -> str:
    """Generate verification report (JSON + markdown).

    Args:
        criteria_path: Path to criteria.json
        output_dir: Directory for report files
        design: Design document path (for report metadata)
    """
    criteria = va.load_criteria(criteria_path)
    if not criteria:
        return "No criteria found."
    report = generate_json_report(criteria, design=design)
    od = Path(output_dir)
    od.mkdir(parents=True, exist_ok=True)
    json_path = od / "verification_report.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path = od / "verification.md"
    md_path.write_text(generate_markdown_report(report), encoding="utf-8")
    return (f"Total: {report['total_criteria']} | "
            f"Passed: {report['passed']} ({report['pass_rate']}) | "
            f"Failed: {report['failed']} | Blocked: {report['blocked']}\n"
            f"Reports: {json_path}, {md_path}")


@mcp.tool()
def vision_convergence_status(
    layer: int = 0,
    max_attempts: int = va.DEFAULT_MAX_ATTEMPTS,
    max_iterations: int = va.DEFAULT_MAX_ITERATIONS,
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Check convergence loop status and budget.

    Args:
        layer: Layer filter (0=all)
        max_attempts: Max attempts per criterion
        max_iterations: Max total iterations
        criteria_path: Path to criteria.json
    """
    criteria = va.load_criteria(criteria_path)
    budget = va.check_budget(criteria, max_attempts, max_iterations, 0, 0)
    ls = va.layer_summary(criteria, layer)
    lines = [f"Layer {layer}: {ls['passed']}/{ls['total']} pass ({ls['pass_rate']:.1f}%)",
             f"  {ls['pending']} pending, {ls['failed']} failed, {ls['blocked']} blocked",
             f"  Total attempts: {budget['total_attempts']}/{max_iterations}"]
    if budget["exhausted"]:
        lines.append(f"  BUDGET EXHAUSTED: {', '.join(budget['exhausted'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# New MCP tools (Sprint 1)
# ---------------------------------------------------------------------------

@mcp.tool()
def vision_import_violations(
    source: str,
    prefix: str = "CW",
    method: str = "code",
    layer: int = 2,
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Import CodeWarden JSONL violations into criteria.json.

    Args:
        source: Path to codewarden_violations.jsonl
        prefix: ID prefix (default CW)
        method: Verification method (default code)
        layer: Layer assignment (default 2)
        criteria_path: Path to criteria.json
    """
    result = va.import_violations(source, criteria_path=criteria_path,
                                  prefix=prefix, method=method, layer=layer)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return f"Imported {result['imported']} violations, skipped {result['skipped']} duplicates"


@mcp.tool()
def vision_import_audit(
    source: str,
    prefix: str = "AUD",
    severity_filter: str = "medium,high,critical",
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Import audit findings from AUDIT_REPORT.md into criteria.json.

    Args:
        source: Path to AUDIT_REPORT.md
        prefix: ID prefix (default AUD)
        severity_filter: Comma-separated severity levels to include
        criteria_path: Path to criteria.json
    """
    result = va.import_audit(source, criteria_path=criteria_path,
                             prefix=prefix, severity_filter=severity_filter)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return f"Imported {result['imported']} audit findings, skipped {result['skipped']}"


@mcp.tool()
def vision_import_plan(
    source: str,
    prefix: str = "PL",
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Import plan features from plan.current.json into criteria.json.

    Args:
        source: Path to plan.current.json
        prefix: ID prefix (default PL)
        criteria_path: Path to criteria.json
    """
    result = va.import_plan(source, criteria_path=criteria_path, prefix=prefix)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return f"Imported {result['imported']} plan features, skipped {result['skipped']}"


@mcp.tool()
def vision_import_verify(
    source: str,
    prefix: str = "VF",
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Import verifier findings from JSON report into criteria.json.

    Args:
        source: Path to verifier_report.json
        prefix: ID prefix (default VF)
        criteria_path: Path to criteria.json
    """
    result = va.import_verify(source, criteria_path=criteria_path, prefix=prefix)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return f"Imported {result['imported']} verifier findings, skipped {result['skipped']}"


@mcp.tool()
def vision_import_tandem(
    source: str,
    prefix: str = "TD",
    mode: str = "decision-only",
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Import tandem comparison report.

    Default mode (decision-only) logs divergences to decision_log and event_log
    but does NOT create criteria. Use criteria-for-unresolved mode to create
    criteria for divergences the user has not yet resolved.

    Args:
        source: Path to tandem_report.json
        prefix: ID prefix (default TD)
        mode: decision-only (default) or criteria-for-unresolved
        criteria_path: Path to criteria.json
    """
    if mode not in ("decision-only", "criteria-for-unresolved"):
        return "ERROR: mode must be 'decision-only' or 'criteria-for-unresolved'"
    result = va.import_tandem(source, criteria_path=criteria_path,
                              prefix=prefix, mode=mode)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    return (f"Logged {result['logged']} divergences, "
            f"created {result['imported']} criteria")


@mcp.tool()
def vision_clear_resolved(
    min_sessions: int = va.DEFAULT_CLEAR_SESSIONS,
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
    archive_path: str = va.DEFAULT_ARCHIVE_PATH,
) -> str:
    """Archive criteria that have been stable (pass) for enough sessions.

    Args:
        min_sessions: Minimum sessions with pass status (default 3)
        criteria_path: Path to criteria.json
        archive_path: Path to archive file
    """
    result = va.clear_resolved(criteria_path=criteria_path,
                               archive_path=archive_path,
                               min_sessions=min_sessions)
    return f"Archived {result['archived']} criteria"


@mcp.tool()
def vision_plan_drift(
    source: str,
    criteria_path: str = va.DEFAULT_CRITERIA_PATH,
) -> str:
    """Compare codebase against the approved plan and report discrepancies.

    Args:
        source: Path to plan.current.json
        criteria_path: Path to criteria.json
    """
    result = va.plan_drift(source, criteria_path=criteria_path)
    if result.get("error"):
        return f"ERROR: {result['error']}"
    discs = result["discrepancies"]
    if not discs:
        return "No discrepancies found - codebase matches the plan"
    lines = [f"{len(discs)} discrepancies found:"]
    for d in discs:
        lines.append(f"  [{d['severity']}] Phase {d['phase']}: {d['detail']}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
