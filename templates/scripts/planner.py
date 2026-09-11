#!/usr/bin/env python3
"""planner.py - Maieutic planning module for ControlCoding v3.1.

Transforms raw user ideas into discretized implementation plans through
structured Socratic dialogue. NOT an MCP server - orchestrates existing
tools (consultant, verification_agent import-plan).

State machine:
  EMPTY -> EXPANDING -> DRAFT -> APPROVED -> IN_PROGRESS -> COMPLETED
  APPROVED -> AMENDED -> APPROVED (re-approval)
  DRAFT -> EXPANDING (re-enter dialogue)

Only Python stdlib (no pip dependencies).
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Sprint 0 shared utilities
try:
    from control_plane_utils import (
        append_event, atomic_write, generate_id, utc_now_iso,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from control_plane_utils import (
        append_event, atomic_write, generate_id, utc_now_iso,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLAN_DIR = os.environ.get("PLANNER_PLAN_DIR", "devlog/plans")
CONTROL_PLANE_DIR = ".controlcoding"
LEGACY_CONTROL_PLANE_DIR = ".claude"
PRIMARY_CONTEXT_FILENAME = "CONTROLCODING.md"
LEGACY_CONTEXT_FILENAME = "CLAUDE.md"
DEFAULT_EVENT_LOG = f"{CONTROL_PLANE_DIR}/event_log.jsonl"
DEFAULT_DECISION_LOG = f"{CONTROL_PLANE_DIR}/decision_log.jsonl"
DEFAULT_CC_CONFIG = f"{CONTROL_PLANE_DIR}/cc_config.json"

VALID_STATES = {
    "EMPTY", "EXPANDING", "DRAFT", "APPROVED",
    "IN_PROGRESS", "COMPLETED", "AMENDED",
}

# Valid state transitions: {from_state: {to_state, ...}}
VALID_TRANSITIONS = {
    "EMPTY":       {"EXPANDING"},
    "EXPANDING":   {"EXPANDING", "DRAFT"},
    "DRAFT":       {"EXPANDING", "APPROVED"},
    "APPROVED":    {"IN_PROGRESS", "AMENDED"},
    "IN_PROGRESS": {"IN_PROGRESS", "COMPLETED"},
    "AMENDED":     {"APPROVED"},
    "COMPLETED":   set(),
}

PLANNER_MAX_ROUNDS = int(os.environ.get("PLANNER_MAX_ROUNDS", "10"))
MAX_FEATURES_PER_PHASE = 15
MAX_PHASES = 10

ROUND_NAMES = [
    "scope",        # Round 1: Scope definition
    "features",     # Round 2: Feature enumeration
    "architecture", # Round 3: Architecture
    "invariants",   # Round 4: Domain invariants
    "acceptance",   # Round 5: Acceptance criteria
    "phasing",      # Round 6: Phasing
]

# ---------------------------------------------------------------------------
# Domain adaptation - questions per round
# ---------------------------------------------------------------------------

DOMAIN_QUESTIONS = {
    "financial": {
        "scope": [
            "What financial instruments does this system handle? (equities, derivatives, FX, crypto)",
            "Who are the users? (retail traders, institutional, market makers)",
            "What regulatory requirements apply? (MiFID II, SEC, none)",
        ],
        "features": [
            "What order types are supported? (limit, market, stop-loss, iceberg)",
            "Is there a matching engine? What matching algorithm? (price-time, pro-rata)",
            "What settlement model? (T+0, T+1, T+2)",
        ],
        "architecture": [
            "How is the order book structured? (in-memory, persistent, replicated)",
            "What are the latency requirements? (microseconds, milliseconds, seconds)",
            "Is there an event sourcing or CQRS pattern?",
        ],
        "invariants": [
            "Conservation of value: sum(debits) == sum(credits) at all times?",
            "Order integrity: no order lost, no duplicate fill, no negative quantity?",
            "Decimal precision: what precision for prices and quantities?",
        ],
        "acceptance": [
            "How do we verify order matching correctness? (test vectors, property tests)",
            "How do we verify settlement correctness?",
            "What performance benchmarks must be met?",
        ],
        "phasing": [
            "Data model first, then matching, then settlement?",
            "Can matching and settlement be developed in parallel?",
            "What is the minimum viable trading flow?",
        ],
    },
    "game": {
        "scope": [
            "What genre? (RPG, platformer, puzzle, simulation, strategy)",
            "What platforms? (PC, web, mobile, console)",
            "Single player, multiplayer, or both?",
        ],
        "features": [
            "What are the core game mechanics?",
            "Is there an inventory/equipment system? What are the constraints?",
            "Is there a save/load system? What state must be preserved?",
        ],
        "architecture": [
            "What is the game loop structure? (fixed timestep, variable, hybrid)",
            "How is game state managed? (ECS, scene graph, monolithic)",
            "What rendering approach? (2D sprite, 3D, voxel)",
        ],
        "invariants": [
            "Save/load fidelity: deserialize(serialize(state)) == state?",
            "Frame independence: behavior identical at 30fps and 60fps?",
            "Resource lifecycle: every loaded resource eventually freed?",
        ],
        "acceptance": [
            "How do we verify visual correctness? (screenshot comparison, manual)",
            "How do we verify game logic? (deterministic replay, unit tests)",
            "What frame rate targets must be met?",
        ],
        "phasing": [
            "Core loop first, then content, then polish?",
            "Can art and code be developed in parallel?",
            "What is the minimum playable slice?",
        ],
    },
    "physics": {
        "scope": [
            "What type of simulation? (Newtonian, fluid, rigid body, soft body)",
            "What is the target accuracy? (real-time approximation, scientific precision)",
            "Is visualization required or is this headless?",
        ],
        "features": [
            "What physical forces are modeled? (gravity, friction, collision, drag)",
            "What integration method? (Euler, Verlet, RK4)",
            "Is there multi-body interaction? How many bodies?",
        ],
        "architecture": [
            "Is there a spatial partitioning structure? (octree, grid, BVH)",
            "How is the simulation stepped? (fixed dt, adaptive)",
            "Is parallelism needed? (SIMD, multithreaded, GPU)",
        ],
        "invariants": [
            "Conservation of energy: total energy constant within tolerance?",
            "Conservation of momentum: total momentum constant within tolerance?",
            "Determinism: same seed + same input = identical output, bit-for-bit?",
        ],
        "acceptance": [
            "What numerical tolerance is acceptable?",
            "How do we verify conservation laws? (property tests over N steps)",
            "What performance targets? (bodies per second, step time)",
        ],
        "phasing": [
            "Single body first, then pairwise, then N-body?",
            "Visualization separate from simulation?",
            "What is the minimum demonstrable simulation?",
        ],
    },
    "web": {
        "scope": [
            "What type of web application? (SPA, SSR, API-only, full-stack)",
            "Who are the users? What authentication model?",
            "What scale? (personal project, startup, enterprise)",
        ],
        "features": [
            "What are the main user flows?",
            "What data does the application manage? What is the data model?",
            "Is there real-time functionality? (websockets, SSE, polling)",
        ],
        "architecture": [
            "What stack? (framework, database, cache, message queue)",
            "What API style? (REST, GraphQL, gRPC)",
            "How is state managed on the frontend?",
        ],
        "invariants": [
            "Input validation: all user input sanitized before use?",
            "Authentication: every protected endpoint requires valid token?",
            "Data integrity: no orphaned records, no broken foreign keys?",
        ],
        "acceptance": [
            "How do we verify each endpoint? (integration tests, contract tests)",
            "How do we verify the UI? (e2e tests, visual regression)",
            "What response time targets?",
        ],
        "phasing": [
            "Data model first, then API, then frontend?",
            "Can backend and frontend be developed in parallel?",
            "What is the minimum viable flow?",
        ],
    },
}

# Generic fallback questions
GENERIC_QUESTIONS = {
    "scope": [
        "What does this system do in one paragraph?",
        "Who are the primary users?",
        "What problem does it solve?",
    ],
    "features": [
        "What are the must-have features for v1?",
        "What features are nice-to-have but can wait?",
        "Are there features that are explicitly out of scope?",
    ],
    "architecture": [
        "What are the major components or modules?",
        "How do they communicate? (function calls, API, events, files)",
        "What are the boundaries that should not be crossed?",
    ],
    "invariants": [
        "What must always be true, regardless of the system state?",
        "What consistency rules exist between components?",
        "What error conditions must never occur?",
    ],
    "acceptance": [
        "For each feature: how do we know it is done?",
        "What does 'correct' look like for the most complex feature?",
        "Are there performance or quality requirements?",
    ],
    "phasing": [
        "What must be built first? (dependencies)",
        "What can be built in parallel?",
        "What is the minimum viable version?",
    ],
}


# ---------------------------------------------------------------------------
# Domain auto-detection
# ---------------------------------------------------------------------------

# Keywords that map to known domains (checked against the project context file Project Identity)
_DOMAIN_KEYWORDS = {
    "financial": [
        "trading", "trader", "finance", "fintech", "payment", "bank",
        "order book", "settlement", "ledger", "accounting", "invoice",
        "portfolio", "exchange", "stock", "crypto", "defi", "blockchain",
    ],
    "game": [
        "game", "3d", "2d", "opengl", "vulkan", "directx", "unity",
        "unreal", "godot", "raylib", "sdl", "sfml", "pygame", "sprite",
        "render", "shader", "ecs", "scene graph", "platformer", "rpg",
    ],
    "physics": [
        "physics", "simulation", "particle", "rigid body", "fluid",
        "nbody", "n-body", "gravity", "verlet", "rk4", "collision",
        "conservation", "newtonian",
    ],
    "web": [
        "react", "vue", "angular", "next.js", "nuxt", "django", "flask",
        "fastapi", "express", "nest.js", "spa", "rest api", "graphql",
        "frontend", "backend", "fullstack", "full-stack", "web app",
    ],
}


def _context_doc_path(claude_md_path: str) -> Path:
    p = Path(claude_md_path)
    if p.exists():
        return p
    if claude_md_path == PRIMARY_CONTEXT_FILENAME:
        legacy = Path(LEGACY_CONTEXT_FILENAME)
        if legacy.exists():
            return legacy
    if claude_md_path == LEGACY_CONTEXT_FILENAME:
        canonical = Path(PRIMARY_CONTEXT_FILENAME)
        if canonical.exists():
            return canonical
    return p


def detect_domain(claude_md_path=PRIMARY_CONTEXT_FILENAME):
    """Auto-detect project domain from the project context file Project Identity section.

    Scans the Project Identity section for keywords that map to known domains.
    Returns the best-matching domain name, or "generic" if no match.

    Args:
        claude_md_path: Path to the project context file.

    Returns:
        dict with keys: domain, confidence, keywords_matched, section_text
    """
    p = _context_doc_path(claude_md_path)
    if not p.exists():
        return {"domain": "generic", "confidence": 0.0,
                "keywords_matched": [], "section_text": ""}

    text = p.read_text(encoding="utf-8")

    # Extract Project Identity section (between ## Project Identity and next ##)
    section = ""
    in_section = False
    for line in text.splitlines():
        if re.match(r"^##\s+Project\s+Identity", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^##\s+", line):
            break
        if in_section:
            section += line + "\n"

    # If no section found, scan the whole file (first 50 lines)
    if not section.strip():
        section = "\n".join(text.splitlines()[:50])

    section_lower = section.lower()

    # Score each domain by keyword matches
    scores = {}
    matched = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in section_lower]
        scores[domain] = len(hits)
        matched[domain] = hits

    if not any(scores.values()):
        return {"domain": "generic", "confidence": 0.0,
                "keywords_matched": [], "section_text": section.strip()}

    best = max(scores, key=scores.get)
    total_kw = len(_DOMAIN_KEYWORDS[best])
    confidence = min(1.0, scores[best] / max(total_kw * 0.3, 1))

    return {
        "domain": best,
        "confidence": round(confidence, 2),
        "keywords_matched": matched[best],
        "section_text": section.strip()[:500],
    }


# ---------------------------------------------------------------------------
# Plan data model
# ---------------------------------------------------------------------------

def _sha256_plan(plan_dict):
    """Compute SHA256 of the plan content (excluding status_hash)."""
    d = dict(plan_dict)
    d.pop("status_hash", None)
    raw = json.dumps(d, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _empty_plan():
    """Return a fresh empty plan structure."""
    return {
        "version": 0,
        "status": "EMPTY",
        "status_hash": "",
        "scope": "",
        "created_at": "",
        "approved_at": "",
        "phases": [],
        "invariants_global": [],
        "architecture": {
            "zones": {"stable": [], "shared": [], "features": [], "workspace": []},
            "dependency_direction": "",
            "deny_zones": [],
            "warn_zones": [],
        },
        "expansion_state": {
            "completed_rounds": [],
            "current_round": 0,
            "responses": {},
        },
    }


def _slugify(text, max_len=40):
    """Convert text to a filename-safe slug (lowercase, hyphens, no spaces)."""
    if not text:
        return "untitled"
    # Lowercase, replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    # Remove leading/trailing hyphens, collapse multiple hyphens
    slug = re.sub(r"-+", "-", slug).strip("-")
    # Truncate to max_len at a word boundary
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "untitled"


def _plan_path(version, state, plan_dir=PLAN_DIR, slug=""):
    """Generate versioned plan file path.

    Format: plan-v{N}-{state}-{slug}.json
    Example: plan-v1-approved-trading-platform.json
    """
    if slug:
        return str(Path(plan_dir) / f"plan-v{version}-{state}-{slug}.json")
    return str(Path(plan_dir) / f"plan-v{version}-{state}.json")


def _current_plan_path(plan_dir=PLAN_DIR):
    return str(Path(plan_dir) / "plan.current.json")


def load_plan(plan_dir=PLAN_DIR):
    """Load the current plan. Returns empty plan if none exists."""
    p = Path(_current_plan_path(plan_dir))
    if not p.exists():
        return _empty_plan()
    plan = json.loads(p.read_text(encoding="utf-8"))
    # Verify hash integrity (anti-compression)
    if plan.get("status") in ("APPROVED", "IN_PROGRESS", "COMPLETED"):
        expected = _sha256_plan(plan)
        if plan.get("status_hash") and plan["status_hash"] != expected:
            raise ValueError(
                f"Plan integrity check failed. Expected hash "
                f"{plan['status_hash']}, got {expected}. "
                f"Plan content was modified without plan_approve()."
            )
    return plan


def save_plan(plan, plan_dir=PLAN_DIR):
    """Save plan to plan.current.json."""
    p = Path(plan_dir)
    p.mkdir(parents=True, exist_ok=True)
    path = _current_plan_path(plan_dir)
    atomic_write(path, plan)


def _save_snapshot(plan, state_label, plan_dir=PLAN_DIR):
    """Save an immutable versioned snapshot.

    Filename includes a slug derived from the plan scope (idea).
    Example: plan-v1-approved-trading-platform.json
    """
    version = plan.get("version", 1)
    slug = _slugify(plan.get("scope", ""))
    path = _plan_path(version, state_label, plan_dir, slug=slug)
    atomic_write(path, plan)
    return path


def _transition(plan, new_state):
    """Validate and execute a state transition."""
    current = plan.get("status", "EMPTY")
    allowed = VALID_TRANSITIONS.get(current, set())
    if new_state not in allowed:
        raise ValueError(
            f"Invalid transition: {current} -> {new_state}. "
            f"Allowed: {allowed or 'none'}"
        )
    plan["status"] = new_state


# ---------------------------------------------------------------------------
# plan_expand() - maieutic dialogue
# ---------------------------------------------------------------------------

def get_expansion_questions(domain, round_name):
    """Get domain-adapted questions for a specific round."""
    domain_lower = domain.lower() if domain else ""
    domain_qs = DOMAIN_QUESTIONS.get(domain_lower, {})
    return domain_qs.get(round_name, GENERIC_QUESTIONS.get(round_name, []))


def plan_expand(idea="", domain="", responses=None, plan_dir=PLAN_DIR,
                event_log=DEFAULT_EVENT_LOG):
    """Start or continue maieutic expansion.

    Args:
        idea: Raw idea text (for first call). Ignored on subsequent calls.
        domain: Project domain for adapted questions.
        responses: Dict of {round_name: answer_text} for answered rounds.
        plan_dir: Directory for plan files.
        event_log: Path to event log.

    Returns:
        dict with keys:
        - plan: the current plan dict
        - next_round: name of the next round to fill (or None if complete)
        - questions: list of questions for the next round
        - complete: bool
    """
    plan = load_plan(plan_dir)
    responses = responses or {}

    # Start new plan
    if plan["status"] == "EMPTY":
        if not idea:
            return {"plan": plan, "next_round": None,
                    "questions": [], "complete": False,
                    "error": "Provide an idea to start planning"}
        _transition(plan, "EXPANDING")
        plan["scope"] = idea
        plan["created_at"] = utc_now_iso()
        plan["version"] = 1
        plan["expansion_state"] = {
            "completed_rounds": [],
            "current_round": 0,
            "responses": {},
        }
    elif plan["status"] not in ("EXPANDING", "DRAFT"):
        # Can re-enter expansion from DRAFT
        if plan["status"] == "DRAFT":
            _transition(plan, "EXPANDING")
        else:
            return {"plan": plan, "next_round": None,
                    "questions": [], "complete": False,
                    "error": f"Cannot expand plan in state {plan['status']}"}

    exp = plan.get("expansion_state", {})
    completed = exp.get("completed_rounds", [])
    stored_responses = exp.get("responses", {})

    # Process new responses
    for round_name, answer in responses.items():
        if round_name in ROUND_NAMES and round_name not in completed:
            stored_responses[round_name] = answer
            completed.append(round_name)
            _apply_round_response(plan, round_name, answer, domain)

    exp["completed_rounds"] = completed
    exp["responses"] = stored_responses

    # Determine next round
    next_round = None
    questions = []
    for rn in ROUND_NAMES:
        if rn not in completed:
            next_round = rn
            questions = get_expansion_questions(domain, rn)
            break

    complete = next_round is None
    if complete:
        _transition(plan, "DRAFT")
        plan["expansion_state"]["current_round"] = len(ROUND_NAMES)

    exp["current_round"] = ROUND_NAMES.index(next_round) if next_round else len(ROUND_NAMES)
    plan["expansion_state"] = exp

    save_plan(plan, plan_dir)

    if complete:
        _log_event("plan_created", "planner",
                   details={"version": plan["version"],
                            "phases": len(plan.get("phases", [])),
                            "features": sum(len(p.get("features", []))
                                           for p in plan.get("phases", []))},
                   event_log=event_log)

    return {"plan": plan, "next_round": next_round,
            "questions": questions, "complete": complete}


def _apply_round_response(plan, round_name, answer, domain):
    """Apply a round's response to the plan structure."""
    if round_name == "scope":
        plan["scope"] = answer

    elif round_name == "features":
        # Parse features from answer (simple: one per line)
        if not plan.get("phases"):
            plan["phases"] = [{"id": "P1", "name": "Core", "description": "",
                                "depends_on": [], "features": [],
                                "zone_mapping": {}}]
        features = []
        for i, line in enumerate(answer.strip().splitlines(), 1):
            line = line.strip().lstrip("- *0123456789.)")
            if line:
                features.append({
                    "id": f"P1-F{i}",
                    "name": line[:80],
                    "description": line,
                    "acceptance_criterion": "",
                    "verification_method": "functional",
                    "verification_steps": [],
                    "invariants": [],
                    "dependencies": [],
                    "type": "functional",
                })
        plan["phases"][0]["features"] = features[:MAX_FEATURES_PER_PHASE]

    elif round_name == "architecture":
        if answer.strip():
            plan["architecture"]["dependency_direction"] = answer.strip()

    elif round_name == "invariants":
        invariants = []
        for i, line in enumerate(answer.strip().splitlines(), 1):
            line = line.strip().lstrip("- *0123456789.)")
            if line:
                invariants.append({
                    "id": f"INV-{i}",
                    "text": line,
                    "type": "domain",
                    "test_sketch": f"Test that: {line}",
                })
        plan["invariants_global"] = invariants

    elif round_name == "acceptance":
        # Apply acceptance criteria to features
        lines = [l.strip() for l in answer.strip().splitlines() if l.strip()]
        for phase in plan.get("phases", []):
            for i, feat in enumerate(phase.get("features", [])):
                if i < len(lines):
                    feat["acceptance_criterion"] = lines[i]

    elif round_name == "phasing":
        # Parse phasing info - create multiple phases if described
        lines = [l.strip() for l in answer.strip().splitlines() if l.strip()]
        if len(lines) > 1 and len(plan.get("phases", [])) == 1:
            # User described multiple phases - restructure
            existing_features = plan["phases"][0].get("features", [])
            plan["phases"] = []
            for pi, line in enumerate(lines[:MAX_PHASES], 1):
                line_clean = line.lstrip("- *0123456789.)")
                plan["phases"].append({
                    "id": f"P{pi}",
                    "name": line_clean[:80],
                    "description": line_clean,
                    "depends_on": [f"P{pi-1}"] if pi > 1 else [],
                    "features": [],
                    "zone_mapping": {},
                    "phase": pi,
                })
            # Distribute features across phases
            if existing_features:
                per_phase = max(1, len(existing_features) // len(plan["phases"]))
                for pi, phase in enumerate(plan["phases"]):
                    start = pi * per_phase
                    end = start + per_phase if pi < len(plan["phases"]) - 1 else len(existing_features)
                    phase_feats = existing_features[start:end]
                    for fi, f in enumerate(phase_feats, 1):
                        f["id"] = f"P{pi+1}-F{fi}"
                    phase["features"] = phase_feats
        # Add phase numbers
        for pi, phase in enumerate(plan.get("phases", []), 1):
            phase["phase"] = pi


# ---------------------------------------------------------------------------
# plan_refine()
# ---------------------------------------------------------------------------

def plan_refine(target_id, changes, plan_dir=PLAN_DIR,
                event_log=DEFAULT_EVENT_LOG):
    """Refine a specific phase or feature.

    Args:
        target_id: Feature or phase ID (e.g., "P2-F3" or "P2").
        changes: Dict of fields to update.

    Returns:
        dict with status and updated plan.
    """
    plan = load_plan(plan_dir)

    if plan["status"] == "EMPTY":
        return {"error": "No plan exists to refine"}

    was_approved = plan["status"] in ("APPROVED", "IN_PROGRESS")
    if was_approved:
        _transition(plan, "AMENDED")

    found = False
    for phase in plan.get("phases", []):
        if phase.get("id") == target_id:
            phase.update({k: v for k, v in changes.items()
                         if k in ("name", "description", "depends_on",
                                  "zone_mapping")})
            found = True
            break
        for feat in phase.get("features", []):
            if feat.get("id") == target_id:
                feat.update({k: v for k, v in changes.items()
                            if k in ("name", "description",
                                     "acceptance_criterion",
                                     "verification_method",
                                     "verification_steps", "invariants",
                                     "dependencies", "type")})
                found = True
                break
        if found:
            break

    if not found:
        return {"error": f"Target {target_id} not found in plan"}

    # Clear hash if was approved (requires re-approval)
    if was_approved:
        plan["status_hash"] = ""

    save_plan(plan, plan_dir)

    _log_event("plan_amended", "planner",
               related_ids=[target_id],
               details={"target": target_id, "changes": list(changes.keys()),
                         "was_approved": was_approved},
               event_log=event_log)

    return {"plan": plan, "amended": was_approved, "target": target_id}


# ---------------------------------------------------------------------------
# plan_approve()
# ---------------------------------------------------------------------------

def plan_approve(rationale="", plan_dir=PLAN_DIR,
                 event_log=DEFAULT_EVENT_LOG,
                 decision_log=DEFAULT_DECISION_LOG,
                 auto_export=True):
    """Lock the plan with SHA256 hash, create immutable snapshot.

    When auto_export is True (default), automatically chains:
      1. plan_export() - criteria to verification engine
      2. export_zone_mapping() - zones to cc_config.json

    Both chained calls are idempotent (safe to call multiple times).

    Returns:
        dict with plan, snapshot path, and export results.
    """
    plan = load_plan(plan_dir)

    if plan["status"] not in ("DRAFT", "AMENDED"):
        return {"error": f"Cannot approve plan in state {plan['status']}. "
                         f"Must be DRAFT or AMENDED."}

    # Bump version on re-approval
    if plan["status"] == "AMENDED":
        plan["version"] = plan.get("version", 1) + 1

    _transition(plan, "APPROVED")
    plan["approved_at"] = utc_now_iso()
    plan["status_hash"] = _sha256_plan(plan)

    # Save immutable snapshot
    snapshot_path = _save_snapshot(plan, "approved", plan_dir)
    save_plan(plan, plan_dir)

    # Log decision
    _log_decision({
        "type": "plan_approval",
        "context": f"Plan v{plan['version']} approved",
        "chosen": "approve",
        "chosen_by": "user",
        "rationale": rationale or "User approved the plan",
        "plan_version": plan["version"],
        "plan_hash": plan["status_hash"],
    }, decision_log=decision_log)

    _log_event("plan_approved", "planner",
               details={"version": plan["version"],
                         "hash": plan["status_hash"],
                         "snapshot": snapshot_path},
               event_log=event_log)

    result = {"plan": plan, "snapshot": snapshot_path}

    # Auto-chain: export criteria + zone mapping (idempotent, fail-safe)
    if auto_export:
        try:
            export_result = plan_export(plan_dir=plan_dir,
                                        event_log=event_log)
            result["export_criteria"] = export_result
        except Exception as e:
            result["export_criteria"] = {"error": str(e)}

        try:
            zone_result = export_zone_mapping(plan_dir=plan_dir)
            result["export_zones"] = zone_result
        except Exception as e:
            result["export_zones"] = {"error": str(e)}

        _log_event("plan_auto_export", "planner",
                   details={
                       "criteria_ok": "error" not in result.get(
                           "export_criteria", {}),
                       "zones_ok": "error" not in result.get(
                           "export_zones", {}),
                   },
                   event_log=event_log)

    return result


# ---------------------------------------------------------------------------
# plan_status()
# ---------------------------------------------------------------------------

def plan_status(plan_dir=PLAN_DIR):
    """Report the current plan state.

    Returns:
        dict with plan metadata and progress info.
    """
    plan = load_plan(plan_dir)

    if plan["status"] == "EMPTY":
        return {"status": "EMPTY", "message": "No plan exists. Use plan_expand() to start."}

    phases = plan.get("phases", [])
    total_features = sum(len(p.get("features", [])) for p in phases)
    exp = plan.get("expansion_state", {})
    completed_rounds = exp.get("completed_rounds", [])

    result = {
        "status": plan["status"],
        "version": plan.get("version", 0),
        "scope": plan.get("scope", "")[:200],
        "phases": len(phases),
        "total_features": total_features,
        "completed_rounds": completed_rounds,
        "remaining_rounds": [r for r in ROUND_NAMES if r not in completed_rounds],
        "created_at": plan.get("created_at", ""),
        "approved_at": plan.get("approved_at", ""),
        "invariants": len(plan.get("invariants_global", [])),
    }

    # Hash verification for approved plans
    if plan["status"] in ("APPROVED", "IN_PROGRESS"):
        expected = _sha256_plan(plan)
        result["hash_valid"] = plan.get("status_hash") == expected

    return result


# ---------------------------------------------------------------------------
# plan_add_feature()
# ---------------------------------------------------------------------------

def plan_add_feature(phase_id, feature, plan_dir=PLAN_DIR,
                     event_log=DEFAULT_EVENT_LOG,
                     decision_log=DEFAULT_DECISION_LOG):
    """Add a feature to an approved plan (creates AMENDED state).

    Args:
        phase_id: Target phase ID (e.g., "P2").
        feature: Dict with feature fields (name, description, etc.).

    Returns:
        dict with amended plan.
    """
    plan = load_plan(plan_dir)

    if plan["status"] not in ("APPROVED", "IN_PROGRESS", "AMENDED"):
        return {"error": f"Cannot add feature in state {plan['status']}. "
                         f"Plan must be APPROVED or AMENDED."}

    if plan["status"] != "AMENDED":
        _transition(plan, "AMENDED")

    target_phase = None
    for phase in plan.get("phases", []):
        if phase.get("id") == phase_id:
            target_phase = phase
            break

    if target_phase is None:
        return {"error": f"Phase {phase_id} not found"}

    # Check feature limit
    if len(target_phase.get("features", [])) >= MAX_FEATURES_PER_PHASE:
        return {"error": f"Phase {phase_id} has {MAX_FEATURES_PER_PHASE} features. "
                         f"Consider splitting the phase."}

    # Generate feature ID
    existing = [f["id"] for p in plan.get("phases", [])
                for f in p.get("features", [])]
    feat_num = len(target_phase.get("features", [])) + 1
    feat_id = f"{phase_id}-F{feat_num}"
    while feat_id in existing:
        feat_num += 1
        feat_id = f"{phase_id}-F{feat_num}"

    new_feature = {
        "id": feat_id,
        "name": feature.get("name", ""),
        "description": feature.get("description", ""),
        "acceptance_criterion": feature.get("acceptance_criterion", ""),
        "verification_method": feature.get("verification_method", "functional"),
        "verification_steps": feature.get("verification_steps", []),
        "invariants": feature.get("invariants", []),
        "dependencies": feature.get("dependencies", []),
        "type": feature.get("type", "functional"),
    }

    target_phase.setdefault("features", []).append(new_feature)

    # Clear hash (requires re-approval)
    plan["status_hash"] = ""
    # Bump version
    plan["version"] = plan.get("version", 1) + 1

    # Save amended snapshot
    _save_snapshot(plan, "amended", plan_dir)
    save_plan(plan, plan_dir)

    _log_decision({
        "type": "plan_amendment",
        "context": f"Added feature {feat_id} to {phase_id}",
        "chosen": "add_feature",
        "chosen_by": "user",
        "rationale": feature.get("description", ""),
        "plan_version": plan["version"],
    }, decision_log=decision_log)

    _log_event("plan_amended", "planner",
               related_ids=[feat_id],
               details={"action": "add_feature", "phase": phase_id,
                         "feature_id": feat_id, "version": plan["version"]},
               event_log=event_log)

    return {"plan": plan, "feature_id": feat_id}


# ---------------------------------------------------------------------------
# plan_export()
# ---------------------------------------------------------------------------

def plan_export(plan_dir=PLAN_DIR, criteria_path="devlog/criteria/criteria.json",
                event_log=DEFAULT_EVENT_LOG):
    """Export plan to Verification Engine criteria via import-plan.

    Returns:
        dict with export result.
    """
    plan = load_plan(plan_dir)

    if plan["status"] not in ("APPROVED", "IN_PROGRESS", "AMENDED"):
        return {"error": f"Cannot export plan in state {plan['status']}. "
                         f"Approve the plan first."}

    plan_path = _current_plan_path(plan_dir)

    # Call import-plan from verification_agent
    try:
        import verification_agent as va
        result = va.import_plan(plan_path, criteria_path=criteria_path,
                                event_log=event_log)
        return result
    except ImportError:
        return {"error": "verification_agent not available"}


# ---------------------------------------------------------------------------
# Zone mapping export
# ---------------------------------------------------------------------------

def export_zone_mapping(plan_dir=PLAN_DIR, config_path=DEFAULT_CC_CONFIG):
    """Export plan's architecture zones to cc_config.json."""
    plan = load_plan(plan_dir)
    arch = plan.get("architecture", {})
    zones = arch.get("zones", {})

    deny = arch.get("deny_zones", zones.get("stable", []))
    warn = arch.get("warn_zones", zones.get("shared", []))

    config = {}
    config_p = Path(config_path)
    if config_p.exists():
        config = json.loads(config_p.read_text(encoding="utf-8"))

    config["protected_zones"] = {
        "deny": deny,
        "warn": warn,
    }

    config_p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, config)
    return {"deny": deny, "warn": warn, "path": config_path}


# ---------------------------------------------------------------------------
# Invariant export -> pytest skeleton
# ---------------------------------------------------------------------------

def export_invariants(plan_dir=PLAN_DIR, output_path="tests/test_invariants.py"):
    """Export global invariants as a pytest skeleton file."""
    plan = load_plan(plan_dir)
    invariants = plan.get("invariants_global", [])

    if not invariants:
        return {"error": "No invariants to export", "count": 0}

    lines = [
        '"""Auto-generated invariant test skeleton from plan.',
        '',
        'Each test corresponds to a global invariant from the approved plan.',
        'Fill in the implementation to make these tests pass.',
        '"""',
        '',
        'import pytest',
        '',
        '',
    ]

    for inv in invariants:
        inv_id = inv.get("id", "INV-?")
        text = inv.get("text", "")
        sketch = inv.get("test_sketch", "")
        # Sanitize for function name
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", text[:60]).strip("_").lower()
        safe_name = re.sub(r"_+", "_", safe_name)

        lines.append(f'class Test{inv_id.replace("-", "")}:')
        lines.append(f'    """{text}"""')
        lines.append('')
        lines.append(f'    def test_{safe_name}(self):')
        lines.append(f'        # {sketch}')
        lines.append(f'        raise NotImplementedError("TODO: implement invariant test")')
        lines.append('')
        lines.append('')

    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)
    output_p.write_text("\n".join(lines), encoding="utf-8")

    return {"count": len(invariants), "path": output_path}


# ---------------------------------------------------------------------------
# Event/decision logging helpers
# ---------------------------------------------------------------------------

def _log_event(event_type, agent, related_ids=None, details=None,
               event_log=DEFAULT_EVENT_LOG):
    existing = []
    p = Path(event_log)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if "id" in obj:
                        existing.append(obj["id"])
                except json.JSONDecodeError:
                    pass

    evt_id = generate_id("EVT", existing)
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


def _log_decision(decision_dict, decision_log=DEFAULT_DECISION_LOG):
    existing = []
    p = Path(decision_log)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if "id" in obj:
                        existing.append(obj["id"])
                except json.JSONDecodeError:
                    pass

    dec_id = generate_id("DEC", existing)
    decision_dict["id"] = dec_id
    decision_dict["timestamp"] = utc_now_iso()
    append_event(decision_log, decision_dict)
    return dec_id
