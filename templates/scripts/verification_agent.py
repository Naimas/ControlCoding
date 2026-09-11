#!/usr/bin/env python3
"""verification_agent.py - Verification orchestrator (D-script).
Mechanical state tracking, DAG resolution, budget enforcement.
AI provides intelligence; this script provides mechanics.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

# Sprint 0 shared utilities
try:
    from control_plane_utils import (
        append_event, atomic_write, canonical_dedup_key, generate_id,
        utc_now_iso, SEVERITY_LEVELS,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from control_plane_utils import (
        append_event, atomic_write, canonical_dedup_key, generate_id,
        utc_now_iso, SEVERITY_LEVELS,
    )

DEFAULT_CRITERIA_PATH = "devlog/criteria/criteria.json"
DEFAULT_ARCHIVE_PATH = "devlog/criteria/criteria_archive.json"
CONTROL_PLANE_DIR = ".controlcoding"
DEFAULT_EVENT_LOG = f"{CONTROL_PLANE_DIR}/event_log.jsonl"
DEFAULT_DECISION_LOG = f"{CONTROL_PLANE_DIR}/decision_log.jsonl"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_ITERATIONS = 100
DEFAULT_TIME_BUDGET = 60  # minutes, 0 = unlimited
DEFAULT_MIN_PASS_RATE = 90
DEFAULT_CLEAR_SESSIONS = 3
VALID_STATUSES = {"pending", "pass", "fail", "blocked", "wont_fix"}
VALID_METHODS = {"visual", "functional", "code", "numerical", "architectural"}
SUPPORTED_EXTERNAL_BACKENDS = {"anthropic", "claude", "ollama", "openai"}

# Type mapping for plan import
PLAN_TYPE_TO_METHOD = {
    "visual": "visual",
    "ui": "visual",
    "behavior": "functional",
    "functional": "functional",
    "architecture": "code",
    "code": "code",
    "invariant": "numerical",
    "numerical": "numerical",
}


@dataclass
class Criterion:
    id: str
    original_text: str
    original_text_hash: str = ""
    source_section: str = ""
    verification_method: str = "visual"
    verification_steps: list = field(default_factory=list)
    status: str = "pending"
    attempts: int = 0
    evidence: list = field(default_factory=list)
    reference_screenshot: str = ""
    notes: str = ""
    layer: int = 1
    blocks: list = field(default_factory=list)
    # v3.1 fields
    external_verification: bool = False
    external_backend: str = ""
    source_agent: str = ""
    source_history: list = field(default_factory=list)

    def __post_init__(self):
        if not self.original_text_hash:
            self.original_text_hash = _sha256(self.original_text)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Criterion":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def load_criteria(path: str) -> list[Criterion]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    criteria = [Criterion.from_dict(d) for d in data]
    # Drift check (D-S9)
    for c in criteria:
        expected = _sha256(c.original_text)
        if c.original_text_hash != expected:
            print(f"[VERIFY] CRITERIA DRIFT DETECTED: {c.id} original_text modified")
            print(f"[VERIFY] Expected hash: {c.original_text_hash}")
            print(f"[VERIFY] Current hash:  {expected}")
            sys.exit(2)
    return criteria

def save_criteria(criteria: list[Criterion], path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([c.to_dict() for c in criteria], indent=2),
                 encoding="utf-8")

def find_criterion(criteria: list[Criterion], cid: str) -> Criterion | None:
    for c in criteria:
        if c.id == cid:
            return c
    return None

def resolve_blocked(criteria: list[Criterion]) -> int:
    """Auto-BLOCKED propagation. Returns count of newly blocked criteria."""
    by_id = {c.id: c for c in criteria}
    changed = 0
    for c in criteria:
        if c.status == "blocked":
            continue
        for dep_id in c.blocks:
            dep = by_id.get(dep_id)
            if dep and dep.status == "pending" and c.status == "fail":
                dep.status = "blocked"
                dep.notes = f"blocked by {c.id} (status=fail)"
                changed += 1
    # Also check: if a criterion's dependencies (criteria that block IT) are not pass
    # Reverse lookup: for each criterion, find which criteria list it in their blocks
    blocked_by = {}
    for c in criteria:
        for b in c.blocks:
            blocked_by.setdefault(b, []).append(c.id)
    for c in criteria:
        if c.status in ("blocked", "pass"):
            continue
        deps = blocked_by.get(c.id, [])
        for dep_id in deps:
            dep = by_id.get(dep_id)
            if dep and dep.status == "fail":
                if c.status != "blocked":
                    c.status = "blocked"
                    c.notes = f"blocked by {dep_id} (status=fail)"
                    changed += 1
                    break
    return changed

def get_dag_ready(criteria: list[Criterion], layer: int = 0) -> list[Criterion]:
    """Get criteria that are ready to work on (not blocked, correct layer)."""
    by_id = {c.id: c for c in criteria}
    ready = []
    for c in criteria:
        if layer > 0 and c.layer != layer:
            continue
        if c.status in ("pass", "blocked"):
            continue
        # Check all dependencies (criteria that list c.id in blocks) are pass
        blocked_by_fail = False
        for other in criteria:
            if c.id in other.blocks and other.status != "pass":
                blocked_by_fail = True
                break
        if not blocked_by_fail:
            ready.append(c)
    return ready

def filter_by_layer(criteria: list[Criterion], layer: int) -> list[Criterion]:
    if layer == 0:
        return criteria
    return [c for c in criteria if c.layer == layer]

def layer_summary(criteria: list[Criterion], layer: int) -> dict:
    filtered = filter_by_layer(criteria, layer)
    total = len(filtered)
    passed = sum(1 for c in filtered if c.status == "pass")
    failed = sum(1 for c in filtered if c.status == "fail")
    blocked = sum(1 for c in filtered if c.status == "blocked")
    pending = sum(1 for c in filtered if c.status == "pending")
    rate = (passed / total * 100) if total > 0 else 0
    return {"total": total, "passed": passed, "failed": failed,
            "blocked": blocked, "pending": pending, "pass_rate": rate}

def check_regression(criteria: list[Criterion]) -> list[Criterion]:
    """Find criteria that were pass but might have regressed.
    Returns list of pass criteria that should be re-verified."""
    return [c for c in criteria if c.status == "pass"]

def check_budget(criteria: list[Criterion], max_attempts: int,
                 max_iterations: int, time_budget: int,
                 start_time: float) -> dict:
    total_attempts = sum(c.attempts for c in criteria)
    elapsed_min = (time.time() - start_time) / 60 if start_time > 0 else 0
    exhausted = []
    if max_iterations > 0 and total_attempts >= max_iterations:
        exhausted.append(f"max_iterations ({max_iterations})")
    if time_budget > 0 and elapsed_min >= time_budget:
        exhausted.append(f"time_budget ({time_budget}min)")
    # Check per-criterion max attempts
    over_max = [c for c in criteria if c.attempts >= max_attempts
                and c.status == "fail"]
    return {"total_attempts": total_attempts, "elapsed_min": elapsed_min,
            "exhausted": exhausted, "over_max_attempts": [c.id for c in over_max]}


# ---------------------------------------------------------------------------
# Event logging helper
# ---------------------------------------------------------------------------

def _log_event(event_type, agent, related_ids=None, details=None,
               event_log=DEFAULT_EVENT_LOG):
    """Append an event to the event log."""
    evt_id = _next_event_id(event_log)
    event = {
        "id": evt_id,
        "ts": utc_now_iso(),
        "agent": agent,
        "event": event_type,
        "details": details or {},
        "related_ids": related_ids or [],
    }
    append_event(event_log, event)
    return evt_id


def _next_event_id(event_log):
    """Read existing EVT- IDs from the log to generate the next one."""
    existing = []
    log_path = Path(event_log)
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "id" in obj:
                    existing.append(obj["id"])
            except json.JSONDecodeError:
                pass
    return generate_id("EVT", existing)


def _log_decision(decision_dict, decision_log=DEFAULT_DECISION_LOG):
    """Append a decision to the decision log."""
    append_event(decision_log, decision_dict)


# ---------------------------------------------------------------------------
# Dedup helper
# ---------------------------------------------------------------------------

def _dedup_key(text, source_agent, file=None, line=None, rule=None):
    """Compute canonical dedup key for a finding.

    Uses: normalized text + source_agent + file + line + rule.
    The same function is used both when importing new findings and when
    building the dedup set from existing criteria, ensuring consistency.
    """
    line_range = str(line) if line is not None else None
    return canonical_dedup_key(text, source_agent, file=file,
                               line_range=line_range, rule_id=rule)


def _build_dedup_set(criteria):
    """Build a set of canonical dedup keys from existing criteria.

    Recomputes keys using (original_text, source_agent) which are the
    fields preserved in the criterion after import. File/line/rule info
    is not stored separately, so we use source_agent as the discriminator.
    """
    keys = set()
    for c in criteria:
        key = _dedup_key(c.original_text, c.source_agent or "")
        keys.add(key)
    return keys


# ---------------------------------------------------------------------------
# Import: violations (from CodeWarden)
# ---------------------------------------------------------------------------

def import_violations(source_path, criteria_path=DEFAULT_CRITERIA_PATH,
                      prefix="CW", method="code", layer=2,
                      event_log=DEFAULT_EVENT_LOG):
    """Import CodeWarden JSONL violations into criteria.json."""
    criteria = load_criteria(criteria_path)
    existing_ids = [c.id for c in criteria]
    dedup_keys = _build_dedup_set(criteria)

    source = Path(source_path)
    if not source.exists():
        return {"imported": 0, "skipped": 0, "error": "source file not found"}

    violations = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            violations.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    imported = 0
    skipped = 0
    new_ids = []

    for v in violations:
        desc = v.get("description", v.get("message", ""))
        if not desc:
            skipped += 1
            continue
        file_path = v.get("file", "")
        line_num = v.get("line")
        rule = v.get("rule", "")

        dk = _dedup_key(desc, prefix.lower())
        if dk in dedup_keys:
            # Add evidence to existing criterion with same dedup key
            for c in criteria:
                ck = _dedup_key(c.original_text, c.source_agent or "")
                if ck == dk:
                    if {"agent": prefix.lower(), "id": v.get("id", "")} not in c.source_history:
                        c.source_history.append({"agent": prefix.lower(),
                                                  "id": v.get("id", "")})
                    break
            skipped += 1
            continue

        dedup_keys.add(dk)
        cid = generate_id(prefix, existing_ids + new_ids)
        new_ids.append(cid)

        ts = v.get("timestamp", utc_now_iso())
        c = Criterion(
            id=cid,
            original_text=desc,
            source_section=f"CodeWarden violation {ts}",
            verification_method=method,
            verification_steps=[
                "Read the file mentioned in the violation",
                "Verify the architectural rule is now respected",
                "Run related tests",
            ],
            status="pending",
            layer=layer,
            source_agent=prefix.lower(),
            source_history=[{"agent": prefix.lower(), "id": v.get("id", cid)}],
        )
        criteria.append(c)
        imported += 1

    save_criteria(criteria, criteria_path)

    _log_event("violations_imported", "verification_engine",
               related_ids=new_ids,
               details={"source": str(source_path), "count": len(violations),
                         "new": imported, "duplicates_skipped": skipped},
               event_log=event_log)

    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Import: audit (from Auditors)
# ---------------------------------------------------------------------------

_AUDIT_FINDING_RE = re.compile(
    r"\[([A-Z]+-\d+)\]"
    r"\s*\*\*Severity\*\*:\s*(\w+)"
    r"\s*\*\*Problem\*\*:\s*(.+?)"
    r"\s*\*\*Fix\*\*:\s*(.+?)"
    r"(?:\s*\*\*Impact\*\*:\s*(.+?))?(?=\n\[|\n##|\Z)",
    re.DOTALL,
)

# Simpler pattern: line-based parsing
_FINDING_HEADER_RE = re.compile(r"^\[([A-Z]+-\d+)\]")


def _parse_audit_report(text):
    """Parse an AUDIT_REPORT.md into a list of finding dicts."""
    findings = []
    current = None
    for line in text.splitlines():
        header = _FINDING_HEADER_RE.match(line.strip())
        if header:
            if current:
                findings.append(current)
            current = {"id": header.group(1), "severity": "", "problem": "",
                        "fix": "", "impact": ""}
            rest = line[header.end():].strip()
            if rest:
                current["problem"] = rest
            continue
        if current is None:
            continue
        low = line.strip().lower()
        if low.startswith("**severity**:") or low.startswith("severity:"):
            current["severity"] = line.split(":", 1)[1].strip().strip("*").lower()
        elif low.startswith("**problem**:") or low.startswith("problem:"):
            current["problem"] = line.split(":", 1)[1].strip().strip("*")
        elif low.startswith("**fix**:") or low.startswith("fix:"):
            current["fix"] = line.split(":", 1)[1].strip().strip("*")
        elif low.startswith("**impact**:") or low.startswith("impact:"):
            current["impact"] = line.split(":", 1)[1].strip().strip("*")
        elif current["problem"] and not current["fix"]:
            # continuation of problem
            current["problem"] += " " + line.strip()
    if current:
        findings.append(current)
    return findings


def import_audit(source_path, criteria_path=DEFAULT_CRITERIA_PATH,
                 prefix="AUD", severity_filter=None,
                 event_log=DEFAULT_EVENT_LOG):
    """Import audit findings from AUDIT_REPORT.md into criteria.json."""
    if severity_filter is None:
        severity_filter = {"medium", "high", "critical"}
    elif isinstance(severity_filter, str):
        severity_filter = {s.strip().lower() for s in severity_filter.split(",")}

    criteria = load_criteria(criteria_path)
    existing_ids = [c.id for c in criteria]
    dedup_keys = _build_dedup_set(criteria)

    source = Path(source_path)
    if not source.exists():
        return {"imported": 0, "skipped": 0, "error": "source file not found"}

    findings = _parse_audit_report(source.read_text(encoding="utf-8"))

    imported = 0
    skipped = 0
    new_ids = []

    for f in findings:
        sev = f["severity"]
        if sev not in severity_filter:
            skipped += 1
            continue
        problem = f["problem"]
        if not problem:
            skipped += 1
            continue

        dk = canonical_dedup_key(problem, prefix.lower())
        if dk in dedup_keys:
            skipped += 1
            continue
        dedup_keys.add(dk)

        cid = generate_id(prefix, existing_ids + new_ids)
        new_ids.append(cid)

        fix_steps = [s.strip() for s in f["fix"].split(";") if s.strip()] if f["fix"] else []

        c = Criterion(
            id=cid,
            original_text=problem,
            source_section=f"Audit finding {f['id']}",
            verification_method="code",
            verification_steps=fix_steps or ["Review and fix the finding"],
            status="pending",
            layer=2,
            notes=f["impact"] if f["impact"] else "",
            source_agent=prefix.lower(),
            source_history=[{"agent": prefix.lower(), "id": f["id"]}],
        )
        criteria.append(c)
        imported += 1

    save_criteria(criteria, criteria_path)

    _log_event("audit_imported", "verification_engine",
               related_ids=new_ids,
               details={"source": str(source_path), "total_findings": len(findings),
                         "imported": imported, "skipped": skipped},
               event_log=event_log)

    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Import: plan (from Planner Agent)
# ---------------------------------------------------------------------------

def import_plan(source_path, criteria_path=DEFAULT_CRITERIA_PATH,
                prefix="PL", event_log=DEFAULT_EVENT_LOG):
    """Import plan features from plan.current.json into criteria.json."""
    criteria = load_criteria(criteria_path)
    existing_ids = [c.id for c in criteria]
    dedup_keys = _build_dedup_set(criteria)

    source = Path(source_path)
    if not source.exists():
        return {"imported": 0, "skipped": 0, "error": "source file not found"}

    plan = json.loads(source.read_text(encoding="utf-8"))
    phases = plan.get("phases", [])

    imported = 0
    skipped = 0
    new_ids = []
    id_mapping = {}  # plan feature id -> criterion id, for dependency resolution

    for phase in phases:
        phase_num = phase.get("phase", 1)
        layer = min(phase_num, 3)  # cap at 3
        features = phase.get("features", [])

        for feat in features:
            ac = feat.get("acceptance_criterion", feat.get("description", ""))
            if not ac:
                skipped += 1
                continue

            dk = canonical_dedup_key(ac, prefix.lower())
            if dk in dedup_keys:
                skipped += 1
                continue
            dedup_keys.add(dk)

            cid = generate_id(prefix, existing_ids + new_ids)
            new_ids.append(cid)

            feat_type = feat.get("type", "functional").lower()
            method = PLAN_TYPE_TO_METHOD.get(feat_type, "functional")

            steps = feat.get("verification_steps", [])
            if isinstance(steps, str):
                steps = [s.strip() for s in steps.split(";") if s.strip()]

            feat_id = feat.get("id", "")
            if feat_id:
                id_mapping[feat_id] = cid

            c = Criterion(
                id=cid,
                original_text=ac,
                source_section=f"Plan phase {phase_num}",
                verification_method=method,
                verification_steps=steps,
                status="pending",
                layer=layer,
                source_agent=prefix.lower(),
                source_history=[{"agent": prefix.lower(), "id": feat_id or cid}],
            )
            criteria.append(c)
            imported += 1

    # Resolve dependencies (blocks)
    for phase in phases:
        for feat in phase.get("features", []):
            feat_id = feat.get("id", "")
            cid = id_mapping.get(feat_id)
            if not cid:
                continue
            deps = feat.get("dependencies", [])
            c = find_criterion(criteria, cid)
            if c:
                for dep_feat_id in deps:
                    dep_cid = id_mapping.get(dep_feat_id)
                    if dep_cid:
                        # The dependency criterion must be done before this one
                        dep_c = find_criterion(criteria, dep_cid)
                        if dep_c and cid not in dep_c.blocks:
                            dep_c.blocks.append(cid)

    save_criteria(criteria, criteria_path)

    _log_event("criterion_created", "verification_engine",
               related_ids=new_ids,
               details={"source": str(source_path), "imported": imported,
                         "skipped": skipped, "import_type": "plan"},
               event_log=event_log)

    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Import: verify (from Verifier Agent)
# ---------------------------------------------------------------------------

def import_verify(source_path, criteria_path=DEFAULT_CRITERIA_PATH,
                  prefix="VF", event_log=DEFAULT_EVENT_LOG):
    """Import verifier findings from JSON report into criteria.json."""
    criteria = load_criteria(criteria_path)
    existing_ids = [c.id for c in criteria]
    dedup_keys = _build_dedup_set(criteria)

    source = Path(source_path)
    if not source.exists():
        return {"imported": 0, "skipped": 0, "error": "source file not found"}

    report = json.loads(source.read_text(encoding="utf-8"))
    findings = report.get("findings", [])

    imported = 0
    skipped = 0
    new_ids = []

    for f in findings:
        desc = f.get("description", "")
        if not desc:
            skipped += 1
            continue

        dk = _dedup_key(desc, prefix.lower())
        if dk in dedup_keys:
            skipped += 1
            continue
        dedup_keys.add(dk)

        cid = generate_id(prefix, existing_ids + new_ids)
        new_ids.append(cid)

        steps = f.get("fix_steps", f.get("recommended_fix", []))
        if isinstance(steps, str):
            steps = [steps]

        backend = f.get("backend", "external")
        ts = f.get("timestamp", utc_now_iso())

        c = Criterion(
            id=cid,
            original_text=desc,
            source_section=f"Verifier finding {ts}",
            verification_method="code",
            verification_steps=steps or ["Review the finding", "Apply fix", "Verify"],
            status="pending",
            layer=2,
            notes=f"Independent verification by {backend}",
            source_agent=prefix.lower(),
            source_history=[{"agent": prefix.lower(), "id": f.get("id", cid)}],
        )
        criteria.append(c)
        imported += 1

    save_criteria(criteria, criteria_path)

    _log_event("criterion_created", "verification_engine",
               related_ids=new_ids,
               details={"source": str(source_path), "imported": imported,
                         "skipped": skipped, "import_type": "verifier"},
               event_log=event_log)

    return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Import: tandem (from Tandem Agent)
# ---------------------------------------------------------------------------

def import_tandem(source_path, criteria_path=DEFAULT_CRITERIA_PATH,
                  prefix="TD", mode="decision-only",
                  event_log=DEFAULT_EVENT_LOG,
                  decision_log=DEFAULT_DECISION_LOG):
    """Import tandem comparison report.

    mode='decision-only' (default): log divergences to decision_log + event_log,
        do NOT create criteria.
    mode='criteria-for-unresolved': create criteria for unresolved divergences.
    """
    source = Path(source_path)
    if not source.exists():
        return {"imported": 0, "logged": 0, "error": "source file not found"}

    report = json.loads(source.read_text(encoding="utf-8"))
    divergences = report.get("divergences", [])
    agreements = report.get("agreements", [])

    # Always log agreements as events
    for a in agreements:
        _log_event("tandem_converged", "tandem",
                   details={"topic": a.get("topic", ""),
                             "confidence": a.get("confidence", "")},
                   event_log=event_log)

    # Log all divergences to decision_log and event_log
    logged = 0
    existing_decisions = _load_decision_ids(decision_log)

    for d in divergences:
        topic = d.get("topic", d.get("description", ""))
        dec_id = generate_id("DEC", existing_decisions)
        existing_decisions.append(dec_id)

        decision = {
            "id": dec_id,
            "timestamp": utc_now_iso(),
            "type": "architecture_choice",
            "context": topic,
            "alternatives": [
                d.get("position_a", "Position A"),
                d.get("position_b", "Position B"),
            ],
            "chosen": "",
            "chosen_by": "",
            "source": "tandem",
            "source_id": d.get("id", ""),
            "rationale": "",
            "impact": [],
            "requires_reapproval": False,
        }
        _log_decision(decision, decision_log=decision_log)

        _log_event("tandem_diverged", "tandem",
                   related_ids=[dec_id],
                   details={"topic": topic,
                             "position_a": d.get("position_a", ""),
                             "position_b": d.get("position_b", "")},
                   event_log=event_log)
        logged += 1

    # In criteria-for-unresolved mode, create criteria for unresolved divergences
    imported = 0
    if mode == "criteria-for-unresolved":
        criteria = load_criteria(criteria_path)
        existing_ids = [c.id for c in criteria]
        dedup_keys = _build_dedup_set(criteria)
        new_ids = []

        # Check which divergences have been resolved (have a DEC- with chosen != "")
        resolved_topics = set()
        dec_path = Path(decision_log)
        if dec_path.exists():
            for line in dec_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    dec = json.loads(line)
                    if dec.get("chosen") and dec.get("source") == "tandem":
                        resolved_topics.add(dec.get("context", ""))
                except json.JSONDecodeError:
                    pass

        for d in divergences:
            topic = d.get("topic", d.get("description", ""))
            if topic in resolved_topics:
                continue

            dk = canonical_dedup_key(topic, prefix.lower())
            if dk in dedup_keys:
                continue
            dedup_keys.add(dk)

            cid = generate_id(prefix, existing_ids + new_ids)
            new_ids.append(cid)

            pos_a = d.get("position_a", "Position A")
            pos_b = d.get("position_b", "Position B")

            c = Criterion(
                id=cid,
                original_text=topic,
                source_section="Tandem divergence",
                verification_method="code",
                verification_steps=[
                    "Review both perspectives",
                    "Choose the approach",
                    "Implement and verify",
                ],
                status="pending",
                layer=2,
                notes=f"Claude position: {pos_a}. GPT position: {pos_b}. User must decide.",
                source_agent=prefix.lower(),
                source_history=[{"agent": prefix.lower(), "id": d.get("id", cid)}],
            )
            criteria.append(c)
            imported += 1

        if imported > 0:
            save_criteria(criteria, criteria_path)
            _log_event("criterion_created", "verification_engine",
                       related_ids=new_ids,
                       details={"import_type": "tandem", "imported": imported},
                       event_log=event_log)

    return {"imported": imported, "logged": logged}


def _load_decision_ids(decision_log):
    """Load existing decision IDs from the decision log."""
    ids = []
    dec_path = Path(decision_log)
    if dec_path.exists():
        for line in dec_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "id" in obj:
                    ids.append(obj["id"])
            except json.JSONDecodeError:
                pass
    return ids


# ---------------------------------------------------------------------------
# clear-resolved
# ---------------------------------------------------------------------------

def clear_resolved(criteria_path=DEFAULT_CRITERIA_PATH,
                   archive_path=DEFAULT_ARCHIVE_PATH,
                   min_sessions=DEFAULT_CLEAR_SESSIONS,
                   event_log=DEFAULT_EVENT_LOG):
    """Archive criteria with status=pass that have been stable."""
    criteria = load_criteria(criteria_path)
    if not criteria:
        return {"archived": 0}

    # Load existing archive
    archive = []
    ap = Path(archive_path)
    if ap.exists():
        archive = json.loads(ap.read_text(encoding="utf-8"))

    to_archive = []
    remaining = []

    for c in criteria:
        # Archive if: pass, enough attempts (proxy for sessions), not wont_fix
        if c.status == "pass" and c.attempts >= min_sessions:
            to_archive.append(c.to_dict())
        else:
            remaining.append(c)

    if not to_archive:
        return {"archived": 0}

    archive.extend(to_archive)
    archived_ids = [d["id"] for d in to_archive]

    # Save both files
    save_criteria(remaining, criteria_path)
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(archive, indent=2), encoding="utf-8")

    _log_event("criterion_archived", "verification_engine",
               related_ids=archived_ids,
               details={"count": len(to_archive),
                         "archive_path": str(archive_path)},
               event_log=event_log)

    return {"archived": len(to_archive)}


# ---------------------------------------------------------------------------
# plan_drift
# ---------------------------------------------------------------------------

def plan_drift(plan_path, criteria_path=DEFAULT_CRITERIA_PATH,
               event_log=DEFAULT_EVENT_LOG):
    """Compare codebase against the approved plan. Return discrepancies."""
    plan_p = Path(plan_path)
    if not plan_p.exists():
        return {"discrepancies": [], "error": "plan file not found"}

    plan = json.loads(plan_p.read_text(encoding="utf-8"))
    phases = plan.get("phases", [])

    discrepancies = []

    for phase in phases:
        phase_num = phase.get("phase", 1)
        for feat in phase.get("features", []):
            expected_files = feat.get("expected_files", [])
            feat_desc = feat.get("acceptance_criterion",
                                 feat.get("description", "unknown"))

            for fp in expected_files:
                if not Path(fp).exists():
                    discrepancies.append({
                        "phase": phase_num,
                        "feature": feat_desc[:80],
                        "type": "missing_file",
                        "detail": f"Expected file not found: {fp}",
                        "severity": "high",
                    })

            zones = feat.get("zones", [])
            for zone in zones:
                if not Path(zone).exists():
                    discrepancies.append({
                        "phase": phase_num,
                        "feature": feat_desc[:80],
                        "type": "missing_zone",
                        "detail": f"Expected zone not found: {zone}",
                        "severity": "medium",
                    })

    if discrepancies:
        _log_event("plan_drift_detected", "verification_engine",
                   details={"plan": str(plan_path),
                             "discrepancy_count": len(discrepancies)},
                   event_log=event_log)

    return {"discrepancies": discrepancies}


# ---------------------------------------------------------------------------
# External verification routing (Sprint 5a)
# ---------------------------------------------------------------------------

def get_external_criteria(criteria_path=DEFAULT_CRITERIA_PATH):
    """Find criteria that require external verification."""
    criteria = load_criteria(criteria_path)
    return [c for c in criteria
            if c.external_verification and c.status == "pending"]


def route_external_verification(criteria_path=DEFAULT_CRITERIA_PATH,
                                diff="", claude_md="", plan_json="",
                                event_log=DEFAULT_EVENT_LOG):
    """Route criteria with external_verification=True to the verifier.

    Groups criteria by backend to avoid redundant LLM calls: one
    verify_code() call per unique backend, not per criterion.

    Returns dict with verification results.
    """
    ext_criteria = get_external_criteria(criteria_path)
    if not ext_criteria:
        return {"verified": 0, "message": "No criteria need external verification"}

    by_backend = {}
    for criterion in ext_criteria:
        backend = (criterion.external_backend or "").strip().lower()
        if backend == "fallback":
            backend = ""
        if not backend:
            return {
                "verified": 0,
                "error": f"Criterion {criterion.id} requires an explicit external backend",
                "callStarted": False,
            }
        if backend not in SUPPORTED_EXTERNAL_BACKENDS:
            return {
                "verified": 0,
                "error": (
                    f"Criterion {criterion.id} has unsupported external backend "
                    f"'{backend}'"
                ),
                "callStarted": False,
            }
        by_backend.setdefault(backend, []).append(criterion)

    # Import only after every criterion has a valid explicit backend.
    try:
        scripts_dir = Path(__file__).resolve().parent
        sys.path.insert(0, str(scripts_dir))
        from mcp_consultant import verify_code, save_verifier_report
    except ImportError:
        return {"verified": 0, "error": "mcp_consultant not available"}

    results = []
    launched_any = False
    launched_groups = 0
    for backend, criteria_group in by_backend.items():
        report = verify_code(
            diff=diff,
            claude_md=claude_md,
            plan_json=plan_json,
            domain="",
            backend=backend,
        )

        if report.get("callStarted") is not False:
            launched_any = True
            launched_groups += 1
            # Save only after an external call was actually launched.
            try:
                save_verifier_report(report)
            except Exception:
                pass
        else:
            continue

        findings_count = len(report.get("findings", []))
        assessment = report.get("summary", {}).get("overall_assessment", "")

        for c in criteria_group:
            results.append({
                "criterion_id": c.id,
                "backend": backend,
                "findings": findings_count,
                "assessment": assessment,
            })

    if launched_any:
        _log_event("verification_run", "verification_engine",
                   related_ids=[c.id for c in ext_criteria],
                   details={"external_criteria": len(ext_criteria),
                             "backends_called": len(by_backend),
                             "results": len(results)},
                   event_log=event_log)

    return {"verified": len(results), "results": results,
            "backends_called": launched_groups}


# ---------------------------------------------------------------------------
# CLI subcommands (original)
# ---------------------------------------------------------------------------

def cmd_add_criterion(args) -> int:
    criteria = load_criteria(args.criteria_path)
    if find_criterion(criteria, args.id):
        print(f"[VERIFY] ERROR: criterion {args.id} already exists")
        return 1
    if args.method not in VALID_METHODS:
        print(f"[VERIFY] ERROR: invalid method {args.method}")
        return 1
    blocks = [b.strip() for b in args.blocks.split(",") if b.strip()] if args.blocks else []
    steps = [s.strip() for s in args.steps.split(";") if s.strip()] if args.steps else []
    c = Criterion(id=args.id, original_text=args.text,
                  source_section=args.section or "", verification_method=args.method,
                  verification_steps=steps, layer=args.layer, blocks=blocks)
    criteria.append(c)
    save_criteria(criteria, args.criteria_path)

    _log_event("criterion_created", "verification_engine",
               related_ids=[c.id],
               details={"method": args.method, "layer": args.layer},
               event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG))

    print(f"[VERIFY] Added {c.id}: \"{c.original_text[:80]}...\" "
          f"(method={c.verification_method}, layer={c.layer})")
    return 0

def cmd_update(args) -> int:
    criteria = load_criteria(args.criteria_path)
    c = find_criterion(criteria, args.id)
    if not c:
        print(f"[VERIFY] ERROR: criterion {args.id} not found")
        return 1
    if args.status not in VALID_STATUSES:
        print(f"[VERIFY] ERROR: invalid status {args.status}")
        return 1
    # Anti-compression: evidence mandatory for pass (D-S9)
    if args.status == "pass" and not args.evidence:
        print("[VERIFY] ERROR: --evidence required when status=pass")
        return 1
    # Anti-compression: notes mandatory for fail/blocked (D-S9)
    if args.status in ("fail", "blocked") and not args.notes:
        print(f"[VERIFY] ERROR: --notes required when status={args.status}")
        return 1
    old_status = c.status
    c.status = args.status
    c.attempts += 1
    if args.evidence:
        c.evidence.append(args.evidence)
    if args.notes:
        c.notes = args.notes
    if args.status == "pass" and args.evidence:
        c.reference_screenshot = args.evidence
    # Re-resolve DAG
    resolve_blocked(criteria)
    save_criteria(criteria, args.criteria_path)

    event_type = {"pass": "criterion_passed", "fail": "criterion_failed"}.get(
        args.status, "criterion_updated")
    _log_event(event_type, "verification_engine",
               related_ids=[c.id],
               details={"old_status": old_status, "new_status": args.status,
                         "attempt": c.attempts},
               event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG))

    print(f"[VERIFY] Updated {c.id}: {old_status} -> {c.status} "
          f"(attempt {c.attempts})")
    return 0

def cmd_next(args) -> int:
    criteria = load_criteria(args.criteria_path)
    if not criteria:
        print("[NEXT] No criteria found. Extract criteria first.")
        return 1
    layer = args.layer
    resolve_blocked(criteria)
    save_criteria(criteria, args.criteria_path)
    ready = get_dag_ready(criteria, layer)
    # Priority: pending first (functional before visual), then failed with attempts remaining
    pending = [c for c in ready if c.status == "pending"]
    failed = [c for c in ready if c.status == "fail" and c.attempts < args.max_attempts]
    # Sort: functional > code > numerical > architectural > visual within each group
    method_order = {"functional": 0, "code": 1, "numerical": 2,
                    "architectural": 3, "visual": 4}
    pending.sort(key=lambda c: method_order.get(c.verification_method, 99))
    failed.sort(key=lambda c: method_order.get(c.verification_method, 99))
    candidates = pending + failed
    if not candidates:
        ls = layer_summary(criteria, layer)
        if ls["passed"] == ls["total"] and ls["total"] > 0:
            if layer > 0:
                print(f"[NEXT] Layer {layer} complete ({ls['passed']}/{ls['total']} pass). "
                      f"Ready to advance to Layer {layer + 1}.")
            else:
                print(f"[NEXT] All criteria verified ({ls['passed']}/{ls['total']}). "
                      "Run: verification_agent.py report")
        else:
            print(f"[NEXT] No actionable criteria (layer={layer}). "
                  f"{ls['blocked']} blocked, {ls['failed']} failed at max attempts.")
        return 0
    c = candidates[0]
    print(f'[NEXT] {c.id}: "{c.original_text}"')
    print(f"[NEXT] Method: {c.verification_method} | Layer: {c.layer} | "
          f"Attempts: {c.attempts}/{args.max_attempts}")
    if c.verification_steps:
        print(f"[NEXT] Steps: {'; '.join(c.verification_steps)}")
    # Architectural guidance
    if c.verification_method == "architectural":
        print("[NEXT] ARCHITECTURAL: Trace this pattern across the ENTIRE codebase, "
              "not just a single file.")
    # Show dependency status
    by_id = {cr.id: cr for cr in criteria}
    deps = [cid for cid, cr in by_id.items() if c.id in cr.blocks]
    if deps:
        dep_str = ", ".join(f"{d} ({by_id[d].status})" for d in deps if d in by_id)
        if dep_str:
            print(f"[NEXT] Depends on: {dep_str}")
    return 0

def cmd_check_regression(args) -> int:
    criteria = load_criteria(args.criteria_path)
    passed = check_regression(criteria)
    if not passed:
        print("[VERIFY] No passed criteria to check for regression.")
        return 0
    print(f"[VERIFY] Regression check: {len(passed)} criteria to re-verify")
    for c in passed:
        ref = f" (ref: {c.reference_screenshot})" if c.reference_screenshot else ""
        print(f"[VERIFY]   {c.id}: \"{c.original_text[:60]}...\"{ref}")
    print("[VERIFY] Re-verify each criterion above. If any regressed, use:")
    print("[VERIFY]   verification_agent.py update --id <ID> --status fail --notes \"regressed: ...\"")

    _log_event("verification_run", "verification_engine",
               related_ids=[c.id for c in passed],
               details={"type": "regression_check", "criteria_count": len(passed)},
               event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG))

    return 0

def cmd_report(args) -> int:
    criteria = load_criteria(args.criteria_path)
    if not criteria:
        print("[VERIFY] No criteria found.")
        return 1
    from verification_report import generate_json_report, generate_markdown_report
    output = args.output or "devlog/verification_report.json"
    md_output = args.md_output or "devlog/verification.md"
    report = generate_json_report(criteria, design=args.design or "")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = generate_markdown_report(report)
    Path(md_output).parent.mkdir(parents=True, exist_ok=True)
    Path(md_output).write_text(md, encoding="utf-8")
    total = report["total_criteria"]
    passed = report["passed"]
    rate = report["pass_rate"]
    print(f"[VERIFY] === VERIFICATION COMPLETE ===")
    print(f"[VERIFY] Total criteria: {total}")
    print(f"[VERIFY] Passed: {passed} ({rate})")
    print(f"[VERIFY] Failed: {report['failed']}")
    print(f"[VERIFY] Blocked: {report['blocked']}")
    print(f"[VERIFY] See: {output}")
    print(f"[VERIFY] See: {md_output}")
    return 0 if float(rate.rstrip("%")) >= DEFAULT_MIN_PASS_RATE else 1

def cmd_convergence_status(args) -> int:
    criteria = load_criteria(args.criteria_path)
    budget = check_budget(criteria, args.max_attempts, args.max_iterations,
                          args.time_budget, args.start_time or 0)
    ls = layer_summary(criteria, args.layer)
    print(f"[VERIFY] Convergence status (layer={args.layer}):")
    print(f"[VERIFY]   {ls['passed']}/{ls['total']} pass ({ls['pass_rate']:.1f}%)")
    print(f"[VERIFY]   {ls['pending']} pending, {ls['failed']} failed, {ls['blocked']} blocked")
    print(f"[VERIFY]   Total attempts: {budget['total_attempts']}/{args.max_iterations}")
    if budget["exhausted"]:
        print(f"[VERIFY]   BUDGET EXHAUSTED: {', '.join(budget['exhausted'])}")
        return 1
    if budget["over_max_attempts"]:
        print(f"[VERIFY]   Over max attempts: {', '.join(budget['over_max_attempts'])}")
    return 0


# ---------------------------------------------------------------------------
# CLI subcommands (new - Sprint 1)
# ---------------------------------------------------------------------------

def cmd_import_violations(args) -> int:
    result = import_violations(
        args.source, criteria_path=args.criteria_path,
        prefix=args.prefix, method=args.method, layer=args.layer,
        event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG),
    )
    if result.get("error"):
        print(f"[VERIFY] ERROR: {result['error']}")
        return 1
    print(f"[VERIFY] import-violations: {result['imported']} imported, "
          f"{result['skipped']} skipped")
    return 0


def cmd_import_audit(args) -> int:
    result = import_audit(
        args.source, criteria_path=args.criteria_path,
        prefix=args.prefix, severity_filter=args.severity_filter,
        event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG),
    )
    if result.get("error"):
        print(f"[VERIFY] ERROR: {result['error']}")
        return 1
    print(f"[VERIFY] import-audit: {result['imported']} imported, "
          f"{result['skipped']} skipped")
    return 0


def cmd_import_plan(args) -> int:
    result = import_plan(
        args.source, criteria_path=args.criteria_path,
        prefix=args.prefix,
        event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG),
    )
    if result.get("error"):
        print(f"[VERIFY] ERROR: {result['error']}")
        return 1
    print(f"[VERIFY] import-plan: {result['imported']} imported, "
          f"{result['skipped']} skipped")
    return 0


def cmd_import_verify(args) -> int:
    result = import_verify(
        args.source, criteria_path=args.criteria_path,
        prefix=args.prefix,
        event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG),
    )
    if result.get("error"):
        print(f"[VERIFY] ERROR: {result['error']}")
        return 1
    print(f"[VERIFY] import-verify: {result['imported']} imported, "
          f"{result['skipped']} skipped")
    return 0


def cmd_import_tandem(args) -> int:
    result = import_tandem(
        args.source, criteria_path=args.criteria_path,
        prefix=args.prefix, mode=args.mode,
        event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG),
        decision_log=getattr(args, "decision_log", DEFAULT_DECISION_LOG),
    )
    if result.get("error"):
        print(f"[VERIFY] ERROR: {result['error']}")
        return 1
    print(f"[VERIFY] import-tandem: {result['logged']} divergences logged, "
          f"{result['imported']} criteria created")
    return 0


def cmd_clear_resolved(args) -> int:
    result = clear_resolved(
        criteria_path=args.criteria_path,
        archive_path=args.archive_path,
        min_sessions=args.min_sessions,
        event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG),
    )
    print(f"[VERIFY] clear-resolved: {result['archived']} criteria archived")
    return 0


def cmd_plan_drift(args) -> int:
    result = plan_drift(
        args.source, criteria_path=args.criteria_path,
        event_log=getattr(args, "event_log", DEFAULT_EVENT_LOG),
    )
    if result.get("error"):
        print(f"[VERIFY] ERROR: {result['error']}")
        return 1
    discs = result["discrepancies"]
    if not discs:
        print("[VERIFY] plan_drift: no discrepancies found")
        return 0
    print(f"[VERIFY] plan_drift: {len(discs)} discrepancies found")
    for d in discs:
        print(f"[VERIFY]   [{d['severity']}] Phase {d['phase']}: {d['detail']}")
    return 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Verification agent orchestrator")
    parser.add_argument("--criteria-path", default=DEFAULT_CRITERIA_PATH)
    sub = parser.add_subparsers(dest="command")

    # add-criterion
    p = sub.add_parser("add-criterion")
    p.add_argument("--id", required=True); p.add_argument("--text", required=True)
    p.add_argument("--section", default=""); p.add_argument("--method", default="visual")
    p.add_argument("--layer", type=int, default=1)
    p.add_argument("--blocks", default=""); p.add_argument("--steps", default="")

    # update
    p = sub.add_parser("update")
    p.add_argument("--id", required=True); p.add_argument("--status", required=True)
    p.add_argument("--evidence", default=""); p.add_argument("--notes", default="")

    # next
    p = sub.add_parser("next")
    p.add_argument("--layer", type=int, default=0)
    p.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)

    # check-regression
    sub.add_parser("check-regression")

    # report
    p = sub.add_parser("report")
    p.add_argument("--output", default=None); p.add_argument("--md-output", default=None)
    p.add_argument("--design", default="")

    # convergence-status
    p = sub.add_parser("convergence-status")
    p.add_argument("--layer", type=int, default=0)
    p.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    p.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    p.add_argument("--time-budget", type=int, default=DEFAULT_TIME_BUDGET)
    p.add_argument("--start-time", type=float, default=0)

    # import-violations
    p = sub.add_parser("import-violations")
    p.add_argument("--source", required=True)
    p.add_argument("--prefix", default="CW")
    p.add_argument("--method", default="code")
    p.add_argument("--layer", type=int, default=2)

    # import-audit
    p = sub.add_parser("import-audit")
    p.add_argument("--source", required=True)
    p.add_argument("--prefix", default="AUD")
    p.add_argument("--severity-filter", default="medium,high,critical")

    # import-plan
    p = sub.add_parser("import-plan")
    p.add_argument("--source", required=True)
    p.add_argument("--prefix", default="PL")

    # import-verify
    p = sub.add_parser("import-verify")
    p.add_argument("--source", required=True)
    p.add_argument("--prefix", default="VF")

    # import-tandem
    p = sub.add_parser("import-tandem")
    p.add_argument("--source", required=True)
    p.add_argument("--prefix", default="TD")
    p.add_argument("--mode", default="decision-only",
                   choices=["decision-only", "criteria-for-unresolved"])

    # clear-resolved
    p = sub.add_parser("clear-resolved")
    p.add_argument("--archive-path", default=DEFAULT_ARCHIVE_PATH)
    p.add_argument("--min-sessions", type=int, default=DEFAULT_CLEAR_SESSIONS)

    # plan_drift
    p = sub.add_parser("plan_drift")
    p.add_argument("--source", required=True)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    cmds = {
        "add-criterion": cmd_add_criterion,
        "update": cmd_update,
        "next": cmd_next,
        "check-regression": cmd_check_regression,
        "report": cmd_report,
        "convergence-status": cmd_convergence_status,
        "import-violations": cmd_import_violations,
        "import-audit": cmd_import_audit,
        "import-plan": cmd_import_plan,
        "import-verify": cmd_import_verify,
        "import-tandem": cmd_import_tandem,
        "clear-resolved": cmd_clear_resolved,
        "plan_drift": cmd_plan_drift,
    }
    return cmds[args.command](args)

if __name__ == "__main__":
    sys.exit(main())
