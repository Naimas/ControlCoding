"""Control Plane Utilities for ControlCoding v3.1 Agentic Ecosystem.

Shared utilities for atomic file writes, event logging, deduplication,
and ID generation used by all agents in the ecosystem.

Only Python stdlib - no external dependencies.
"""

import ctypes
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Schema constants (Appendix C)
# ---------------------------------------------------------------------------

DECISION_TYPES = frozenset({
    "architecture_choice",
    "plan_approval",
    "plan_amendment",
    "scope_change",
    "wont_fix",
    "override",
    "escalation_resolution",
})

EVENT_TYPES = frozenset({
    "agent_started",
    "agent_completed",
    "violations_detected",
    "violations_imported",
    "criterion_created",
    "criterion_updated",
    "criterion_passed",
    "criterion_failed",
    "criterion_regressed",
    "criterion_archived",
    "plan_created",
    "plan_approved",
    "plan_amended",
    "plan_drift_detected",
    "tandem_round_completed",
    "tandem_converged",
    "tandem_diverged",
    "verification_run",
    "audit_started",
    "audit_completed",
    "audit_imported",
    "decision_recorded",
    "escalation_triggered",
    "budget_warning",
    "budget_exhausted",
    "plan_auto_export",
    "engagement_loaded",
    "llm_call_gated",
    "llm_call_routed",
})

SEVERITY_LEVELS = frozenset({
    "critical",
    "high",
    "medium",
    "low",
})

CATEGORY_TYPES = frozenset({
    "architecture",
    "domain_invariant",
    "coding_convention",
    "security",
    "resource_management",
    "style",
})

# Prefix registry for generate_id
KNOWN_PREFIXES = frozenset({
    "PL",   # Planner criteria
    "CW",   # CodeWarden violations
    "VF",   # Verifier findings
    "AUD",  # Auditor findings
    "TD",   # Tandem divergences
    "DEC",  # Decision records
    "EVT",  # Event records
})


# ---------------------------------------------------------------------------
# atomic_write
# ---------------------------------------------------------------------------

def atomic_write(path, data):
    """Write *data* as JSON to *path* via temp file + atomic rename.

    - Encoding: UTF-8
    - JSON formatting: indent=2, ensure_ascii=False
    - On failure the temp file is removed.
    """
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # On Windows os.rename fails if target exists; use os.replace.
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------

def _lock_file(f):
    """Best-effort file locking (fcntl on POSIX, msvcrt on Windows)."""
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    except (ImportError, OSError):
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass  # fallback: append-only semantics


def _unlock_file(f):
    """Best-effort file unlocking."""
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):
        try:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass


def append_event(log_path, event_dict):
    """Append a single JSONL line to *log_path*.

    Creates the file (and parent dirs) if it does not exist.
    Uses file locking where available; falls back to append-only.
    """
    log_path = os.path.abspath(log_path)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    line = json.dumps(event_dict, ensure_ascii=False) + "\n"

    with open(log_path, "a", encoding="utf-8") as f:
        _lock_file(f)
        try:
            f.write(line)
            f.flush()
        finally:
            _unlock_file(f)


# ---------------------------------------------------------------------------
# canonical_dedup_key
# ---------------------------------------------------------------------------

def canonical_dedup_key(text, source_agent, file=None, line_range=None,
                        rule_id=None):
    """Return a SHA-256 hex digest that uniquely identifies a finding.

    The key is computed from the concatenation of:
    - normalized text (lowercased, whitespace-collapsed)
    - source agent name
    - file path (if applicable)
    - line range (if applicable)
    - rule identifier (if applicable)
    """
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    parts = [
        normalized,
        source_agent,
        file or "",
        str(line_range) if line_range is not None else "",
        rule_id or "",
    ]
    composite = "|".join(parts)
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# generate_id
# ---------------------------------------------------------------------------

def generate_id(prefix, existing_ids=None):
    """Generate the next auto-incremented ID for *prefix*.

    Format: ``{PREFIX}-{NNN}`` with zero-padded 3-digit suffix.
    *existing_ids* is an iterable of already-used IDs (any prefix).
    """
    if existing_ids is None:
        existing_ids = []

    max_num = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for eid in existing_ids:
        m = pattern.match(eid)
        if m:
            max_num = max(max_num, int(m.group(1)))

    return f"{prefix}-{max_num + 1:03d}"


# ---------------------------------------------------------------------------
# Convenience: timestamp helper
# ---------------------------------------------------------------------------

def utc_now_iso():
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Engagement / product-tier configuration
# ---------------------------------------------------------------------------

ENGAGEMENT_SCHEMA_VERSION = 2

PRODUCT_TIER_ORDER = ("core", "agents", "studio")

PRODUCT_TIER_PROFILES = {
    "core": {
        "label": "Core",
        "summary": (
            "Structured ControlCoding baseline: hooks, session continuity, "
            "CodeWarden baseline, verification tracking, no specialist-agent "
            "workflow as the center of gravity."
        ),
        "legacy_level": 3,
        "ui_intent": "host_only",
    },
    "agents": {
        "label": "Agents",
        "summary": (
            "Core plus bounded specialist help such as consultant roles, "
            "planning, tandem, and orchestrated workflows."
        ),
        "legacy_level": 4,
        "ui_intent": "host_assist",
    },
    "studio": {
        "label": "Studio",
        "summary": (
            "Agents plus the full CC UX layer: visualizer or API/local "
            "studio surfaces, approval panels, and routed transcripts."
        ),
        "legacy_level": 4,
        "ui_intent": "studio",
    },
}

PRODUCT_TIER_COMPONENTS = {
    "core": frozenset({
        "codewarden",
        "session",
        "verification",
        "auto_import",
    }),
    "agents": frozenset({
        "codewarden",
        "session",
        "verification",
        "auto_import",
        "consultant",
        "planner",
        "tandem",
        "concierge",
    }),
    "studio": frozenset({
        "codewarden",
        "session",
        "verification",
        "auto_import",
        "consultant",
        "planner",
        "tandem",
        "concierge",
    }),
}

VALID_UI_INTENTS = frozenset({"host_only", "host_assist", "studio"})
VALID_PLANNING_MODES = frozenset({
    "solo_structured",
    "solo_structured_manual_consultation_ready",
    "orchestrated_specialists",
})
VALID_PLANNING_AUTHORITIES = frozenset({
    "single_author",
    "single_author_with_manual_consultation",
    "orchestrated_multi_role",
})
VALID_SPECIALIST_PERMISSION_MODES = frozenset({
    "user_mediated",
    "approval_required",
    "within_budget_auto",
    "disabled",
})
VALID_SPECIALIST_EXECUTION_MODES = frozenset({
    "human_mediated",
    "cc_routed",
    "auto_bounded",
    "disabled",
})
SUPPORTED_RUNTIME_ADAPTERS = frozenset({
    "anthropic", "claude", "ollama", "openai", "session",
})

ENGAGEMENT_LEVELS = {
    1: "conservative",   # Boundary hooks only, strict zero LLM
    2: "guided",         # + CodeWarden, Session Manager, Consultant
    3: "active",         # + Verification Engine, auto-import
    4: "full",           # + Planner, Tandem, Concierge
}

# Which components are active at each level
ENGAGEMENT_COMPONENTS = {
    1: frozenset(),
    2: frozenset({"codewarden", "session", "consultant"}),
    3: frozenset({"codewarden", "session", "consultant",
                  "verification", "auto_import"}),
    4: frozenset({"codewarden", "session", "consultant",
                  "verification", "auto_import",
                  "planner", "tandem", "concierge"}),
}

ENGAGEMENT_DEFAULTS = {
    "schema_version": ENGAGEMENT_SCHEMA_VERSION,
    "level": 2,
    "tier": None,
    "ui_intent": None,
    "presence": None,
    "planning_mode": "solo_structured",
    "planning_authority": "single_author",
    "manual_consultation_allowed": False,
    "backend_policy": "local_only",
    "tandem": {
        "mode": "auto",
        "backend_a": "",
        "backend_b": "",
        "model_a": "",
        "model_b": "",
    },
    "budget_policy": {
        "max_calls": 0,
        "exhaustion_behavior": "degrade",
    },
    "specialist_paths": [],
}

DEFAULT_ENGAGEMENT_PATH = ".controlcoding/cc_engagement.json"
DEFAULT_GATEWAY_PATH = ".controlcoding/gateway_config.json"

LOCAL_TANDEM_MIN_RAM_GB = 32.0
LOCAL_TANDEM_RECOMMENDED_RAM_GB = 64.0
LOCAL_TANDEM_MIN_VRAM_GB = 12.0
LOCAL_TANDEM_RECOMMENDED_VRAM_GB = 20.0

SPECIALIST_RUNTIME_ROLE_FAMILIES = {
    "consultant": frozenset({
        "consultant", "architect", "reviewer", "debug",
        "socratic", "scientist", "expert", "planner",
    }),
    "tandem": frozenset({"tandem"}),
    "codewarden": frozenset({"codewarden"}),
    "narrator": frozenset({"narrator"}),
}


def normalize_product_tier(value):
    """Return the normalized product tier name or None."""
    if not isinstance(value, str):
        return None
    tier = value.strip().lower()
    return tier if tier in PRODUCT_TIER_PROFILES else None


def normalize_ui_intent(value):
    """Return the normalized UI intent or None."""
    if not isinstance(value, str):
        return None
    ui_intent = value.strip().lower()
    return ui_intent if ui_intent in VALID_UI_INTENTS else None


def normalize_planning_mode(value):
    """Return the normalized planning mode or None."""
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    return mode if mode in VALID_PLANNING_MODES else None


def normalize_planning_authority(value):
    """Return the normalized planning authority or None."""
    if not isinstance(value, str):
        return None
    authority = value.strip().lower()
    return authority if authority in VALID_PLANNING_AUTHORITIES else None


def _normalize_specialist_role_id(value, fallback):
    if not isinstance(value, str):
        return fallback
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or fallback


def _normalize_specialist_label(role_id, raw_label):
    if isinstance(raw_label, str) and raw_label.strip():
        return raw_label.strip()
    return role_id.replace("_", " ").title()


def infer_specialist_execution_mode(permission, active=True):
    """Infer the default execution mode from the permission model."""
    if not active:
        return "disabled"
    if permission == "user_mediated":
        return "human_mediated"
    if permission == "within_budget_auto":
        return "auto_bounded"
    return "cc_routed"


def infer_specialist_permission(execution_mode, active=True):
    """Infer the canonical permission envelope from the execution mode."""
    if not active:
        return "disabled"
    if execution_mode == "human_mediated":
        return "user_mediated"
    if execution_mode == "auto_bounded":
        return "within_budget_auto"
    return "approval_required"


def normalize_specialist_paths(value):
    """Normalize explicit specialist/backend consent paths."""
    if not isinstance(value, list):
        return []

    normalized = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        fallback_role_id = f"specialist_{index}"
        role_id = _normalize_specialist_role_id(
            item.get("role_id") or item.get("id") or item.get("role") or "",
            fallback_role_id,
        )
        label = _normalize_specialist_label(role_id, item.get("label") or item.get("role"))
        path_type = str(item.get("path_type") or item.get("kind") or "specialist").strip().lower()
        if not path_type:
            path_type = "specialist"
        active = item.get("active", True) is not False
        backend = str(item.get("backend") or item.get("backend_id") or "").strip()
        model = str(item.get("model") or "").strip()
        permission = str(
            item.get("permission")
            or item.get("permission_mode")
            or ("disabled" if not active else "approval_required")
        ).strip().lower()
        if permission not in VALID_SPECIALIST_PERMISSION_MODES:
            permission = "disabled" if not active else "approval_required"
        execution_mode = str(
            item.get("execution_mode")
            or item.get("executionMode")
            or infer_specialist_execution_mode(permission, active=active)
        ).strip().lower()
        if execution_mode not in VALID_SPECIALIST_EXECUTION_MODES:
            execution_mode = infer_specialist_execution_mode(permission, active=active)
        permission = infer_specialist_permission(execution_mode, active=active)
        raw_max_calls = item.get("max_calls", item.get("maxCalls", 0))
        try:
            max_calls = int(raw_max_calls)
        except (TypeError, ValueError):
            max_calls = 0
        if max_calls < 0:
            max_calls = 0
        normalized.append({
            "role_id": role_id,
            "label": label,
            "path_type": path_type,
            "active": active,
            "backend": backend,
            "model": model,
            "permission": "disabled" if not active else permission,
            "execution_mode": "disabled" if not active else execution_mode,
            "max_calls": max_calls,
        })
    return normalized


def planning_profile_for_tier(tier, manual_consultation_allowed=False):
    """Return the canonical planning profile for a product tier."""
    normalized = normalize_product_tier(tier)
    if normalized == "core":
        if manual_consultation_allowed:
            return {
                "planning_mode": "solo_structured_manual_consultation_ready",
                "planning_authority": "single_author_with_manual_consultation",
                "manual_consultation_allowed": True,
            }
        return {
            "planning_mode": "solo_structured",
            "planning_authority": "single_author",
            "manual_consultation_allowed": False,
        }
    if normalized in {"agents", "studio"}:
        return {
            "planning_mode": "orchestrated_specialists",
            "planning_authority": "orchestrated_multi_role",
            "manual_consultation_allowed": False,
        }
    return {
        "planning_mode": ENGAGEMENT_DEFAULTS["planning_mode"],
        "planning_authority": ENGAGEMENT_DEFAULTS["planning_authority"],
        "manual_consultation_allowed": ENGAGEMENT_DEFAULTS["manual_consultation_allowed"],
    }


def legacy_level_for_tier(tier):
    """Return the compatibility legacy level for a product tier."""
    normalized = normalize_product_tier(tier)
    if normalized is None:
        return ENGAGEMENT_DEFAULTS["level"]
    return PRODUCT_TIER_PROFILES[normalized]["legacy_level"]


def best_effort_tier(config):
    """Infer the best public product tier from a config dict."""
    tier = normalize_product_tier(config.get("tier"))
    if tier is not None:
        return tier

    ui_intent = normalize_ui_intent(config.get("ui_intent"))
    if ui_intent == "studio":
        return "studio"

    level = config.get("level", ENGAGEMENT_DEFAULTS["level"])
    if isinstance(level, int):
        if level >= 4:
            return "agents"
        if level >= 2:
            return "agents"
    return "core"


def normalize_engagement_config(data):
    """Normalize a raw engagement payload into the canonical flat shape."""
    config = dict(ENGAGEMENT_DEFAULTS)
    config["budget_policy"] = dict(ENGAGEMENT_DEFAULTS["budget_policy"])
    config["tandem"] = dict(ENGAGEMENT_DEFAULTS["tandem"])
    config["specialist_paths"] = list(ENGAGEMENT_DEFAULTS["specialist_paths"])

    if not isinstance(data, dict):
        return config

    nested = data.get("engagement")
    nested = nested if isinstance(nested, dict) else {}

    if "schema_version" in data:
        config["schema_version"] = data["schema_version"]

    level_value = data.get("level", nested.get("level"))
    if isinstance(level_value, int) and 1 <= level_value <= 4:
        config["level"] = level_value

    tier_value = data.get("tier", nested.get("tier"))
    tier = normalize_product_tier(tier_value)
    if tier is not None:
        config["tier"] = tier
        config["level"] = legacy_level_for_tier(tier)

    ui_intent = normalize_ui_intent(data.get("ui_intent"))
    if ui_intent is None and tier is not None:
        ui_intent = PRODUCT_TIER_PROFILES[tier]["ui_intent"]
    config["ui_intent"] = ui_intent

    presence = data.get("presence", nested.get("presence"))
    if presence in ("step-by-step", "milestone", "autonomous"):
        config["presence"] = presence

    raw_manual_consultation_allowed = data.get(
        "manual_consultation_allowed",
        nested.get("manual_consultation_allowed"),
    )
    planning_mode = normalize_planning_mode(data.get("planning_mode", nested.get("planning_mode")))
    planning_authority = normalize_planning_authority(
        data.get("planning_authority", nested.get("planning_authority"))
    )
    if tier is not None:
        manual_for_tier = False
        if tier == "core":
            if planning_mode == "solo_structured_manual_consultation_ready":
                manual_for_tier = True
            elif isinstance(raw_manual_consultation_allowed, bool):
                manual_for_tier = raw_manual_consultation_allowed
        planning = planning_profile_for_tier(tier, manual_for_tier)
        config["planning_mode"] = planning["planning_mode"]
        config["planning_authority"] = planning["planning_authority"]
        config["manual_consultation_allowed"] = planning["manual_consultation_allowed"]
    else:
        if planning_mode is not None:
            config["planning_mode"] = planning_mode
        if planning_authority is not None:
            config["planning_authority"] = planning_authority
        if isinstance(raw_manual_consultation_allowed, bool):
            config["manual_consultation_allowed"] = raw_manual_consultation_allowed

    if "backend_policy" in data:
        bp = data["backend_policy"]
        if bp in ("local_only", "approved", "all"):
            config["backend_policy"] = bp

    tandem_raw = data.get("tandem")
    if isinstance(tandem_raw, dict):
        mode = tandem_raw.get("mode")
        if mode in ("auto", "off"):
            config["tandem"]["mode"] = mode
        for key in ("backend_a", "backend_b", "model_a", "model_b"):
            value = tandem_raw.get(key, "")
            if isinstance(value, str):
                config["tandem"][key] = value.strip()

    if "budget_policy" in data and isinstance(data["budget_policy"], dict):
        bp = data["budget_policy"]
        if "max_calls" in bp and isinstance(bp["max_calls"], int):
            config["budget_policy"]["max_calls"] = bp["max_calls"]
        if "exhaustion_behavior" in bp:
            eb = bp["exhaustion_behavior"]
            if eb in ("degrade", "stop"):
                config["budget_policy"]["exhaustion_behavior"] = eb

    if "specialist_paths" in data:
        config["specialist_paths"] = normalize_specialist_paths(data["specialist_paths"])

    return config


def load_engagement(config_path=DEFAULT_ENGAGEMENT_PATH):
    """Load engagement configuration from JSON file.

    Returns the config dict with defaults applied for missing keys.
    If the file does not exist, returns defaults.
    """
    path = os.path.abspath(config_path)
    config = normalize_engagement_config({})

    if not os.path.exists(path):
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return config

    return normalize_engagement_config(data)


def save_engagement(config, config_path=DEFAULT_ENGAGEMENT_PATH):
    """Save engagement configuration to JSON file using atomic write."""
    atomic_write(config_path, normalize_engagement_config(config))


def get_active_components(config=None, config_path=DEFAULT_ENGAGEMENT_PATH):
    """Return the active component set for the current config."""
    if config is None:
        config = load_engagement(config_path)

    tier = normalize_product_tier(config.get("tier"))
    if tier is not None:
        return set(PRODUCT_TIER_COMPONENTS[tier])

    level = config.get("level", ENGAGEMENT_DEFAULTS["level"])
    if not isinstance(level, int) or level < 2:
        return set()

    active = set()
    for lvl in range(2, min(level, 4) + 1):
        active |= ENGAGEMENT_COMPONENTS.get(lvl, frozenset())
    return active


def is_component_active(component, config=None, config_path=DEFAULT_ENGAGEMENT_PATH):
    """Check if a component is active at the current engagement level.

    Args:
        component: Component name (e.g., "codewarden", "planner", "concierge")
        config: Pre-loaded config dict, or None to load from disk.
        config_path: Path to engagement config file.

    Returns:
        True if the component is active at the current engagement level.
    """
    return component in get_active_components(config=config, config_path=config_path)


def check_budget(config=None, config_path=DEFAULT_ENGAGEMENT_PATH):
    """Check if budget allows another LLM call.

    Returns:
        dict with keys: allowed (bool), max_calls, exhaustion_behavior
    """
    if config is None:
        config = load_engagement(config_path)

    bp = config.get("budget_policy", {})
    max_calls = bp.get("max_calls", 0)
    behavior = bp.get("exhaustion_behavior", "degrade")

    # max_calls == 0 means unlimited
    if max_calls == 0:
        return {"allowed": True, "max_calls": 0,
                "exhaustion_behavior": behavior}

    # Count calls from event log
    event_log = os.path.join(os.path.dirname(os.path.abspath(config_path)),
                             "event_log.jsonl")
    call_count = 0
    if os.path.exists(event_log):
        try:
            with open(event_log, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                        event_name = str(evt.get("event", "")).strip()
                        if event_name.startswith("llm_call") and event_name != "llm_call_gated":
                            call_count += 1
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    allowed = call_count < max_calls
    return {
        "allowed": allowed,
        "current_calls": call_count,
        "max_calls": max_calls,
        "exhaustion_behavior": behavior,
    }


def _normalize_specialist_role_ref(value):
    """Normalize a specialist role reference for matching."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def specialist_semantic_role(path):
    """Return the semantic specialist role for a normalized path."""
    if not isinstance(path, dict):
        return "specialist"
    role_id = _normalize_specialist_role_ref(path.get("role_id") or path.get("id") or "")
    path_type = _normalize_specialist_role_ref(path.get("path_type") or path.get("kind") or "")
    if role_id.startswith("consultant_") or path_type == "consultant":
        return "consultant"
    if role_id.startswith("codewarden") or path_type == "codewarden":
        return "codewarden"
    if role_id.startswith("narrator") or path_type == "narrator":
        return "narrator"
    if role_id.startswith("tandem") or path_type == "tandem":
        return "tandem"
    return role_id or path_type or "specialist"


def _specialist_runtime_component(role_ref):
    """Map a runtime role reference to the gated component name."""
    normalized = _normalize_specialist_role_ref(role_ref)
    for component, aliases in SPECIALIST_RUNTIME_ROLE_FAMILIES.items():
        if normalized in aliases:
            return component
    return normalized or "consultant"


def find_specialist_path(role_ref, paths, requested_backend=""):
    """Return the best-matching active specialist path for a runtime role."""
    normalized = _normalize_specialist_role_ref(role_ref)
    if not normalized or not isinstance(paths, list):
        return None

    requested_backend = str(requested_backend or "").strip()

    def _path_score(path):
        role_id = _normalize_specialist_role_ref(path.get("role_id") or "")
        label = _normalize_specialist_role_ref(path.get("label") or "")
        path_type = _normalize_specialist_role_ref(path.get("path_type") or "")
        semantic = specialist_semantic_role(path)
        if normalized in {role_id, label}:
            return 0
        if normalized in {semantic, path_type}:
            return 1
        family = _specialist_runtime_component(normalized)
        if family and family in {semantic, path_type}:
            return 2
        return 99

    matches = []
    for path in paths:
        if not isinstance(path, dict):
            continue
        if path.get("active", True) is False:
            continue
        score = _path_score(path)
        if score >= 99:
            continue
        backend = str(path.get("backend") or "").strip()
        backend_match = requested_backend and backend == requested_backend
        matches.append((score, 0 if backend_match else 1, path))

    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1]))
    return matches[0][2]


def _safe_load_json_object(path):
    """Best-effort JSON object loader that returns {} on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _event_log_ids(event_log_path):
    ids = []
    if not os.path.exists(event_log_path):
        return ids
    try:
        with open(event_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_id = obj.get("id")
                if isinstance(event_id, str) and event_id:
                    ids.append(event_id)
    except OSError:
        return ids
    return ids


def _append_runtime_event(event_log_path, event_name, details):
    """Append a control-plane runtime event when the log path is available."""
    if not event_log_path:
        return
    event_log_path = os.path.abspath(event_log_path)
    event = {
        "id": generate_id("EVT", _event_log_ids(event_log_path)),
        "ts": utc_now_iso(),
        "agent": "control_plane",
        "event": event_name,
        "details": details,
        "related_ids": [],
    }
    append_event(event_log_path, event)


def count_specialist_runtime_calls(role_id, event_log_path):
    """Count routed runtime specialist calls for a single role id."""
    normalized_role = _normalize_specialist_role_ref(role_id)
    if not normalized_role or not event_log_path or not os.path.exists(event_log_path):
        return 0

    count = 0
    try:
        with open(event_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event") != "llm_call_routed":
                    continue
                details = obj.get("details", {})
                if not isinstance(details, dict):
                    continue
                if _normalize_specialist_role_ref(details.get("specialist_role_id")) == normalized_role:
                    count += 1
    except OSError:
        return 0

    return count


def check_specialist_runtime(role_ref,
                             requested_backend="",
                             requested_model="",
                             component=None,
                             config=None,
                             config_path=DEFAULT_ENGAGEMENT_PATH,
                             gateway_config_path=None,
                             event_log_path=None):
    """Evaluate whether a runtime specialist/backend call is currently allowed.

    The gate becomes authoritative when either:
    - explicit specialist_paths are configured, or
    - the project tier is Agents/Studio.

    Returns a dict with at least:
      allowed, enforced, reason, detail, component, role_ref,
      backend, model, execution_mode, specialist_path
    """
    config_path = os.path.abspath(config_path)
    config_was_provided = config is not None
    config_file_exists = os.path.exists(config_path)
    if config is None:
        config = load_engagement(config_path)

    normalized_role = _normalize_specialist_role_ref(role_ref) or "specialist"
    component_name = component or _specialist_runtime_component(normalized_role)
    gateway_config_path = os.path.abspath(
        gateway_config_path
        or os.path.join(os.path.dirname(config_path), "gateway_config.json")
    )
    event_log_path = os.path.abspath(
        event_log_path
        or os.path.join(os.path.dirname(config_path), "event_log.jsonl")
    )

    explicit_backend = str(requested_backend or "").strip()
    result = {
        "allowed": False,
        "enforced": True,
        "reason": "missing_backend",
        "detail": "",
        "component": component_name,
        "role_ref": normalized_role,
        "backend": explicit_backend,
        "backend_profile": "",
        "model": str(requested_model or "").strip(),
        "execution_mode": "",
        "specialist_path": None,
        "calls_used": 0,
        "call_limit": 0,
    }

    tier = normalize_product_tier(config.get("tier"))
    specialist_paths = normalize_specialist_paths(config.get("specialist_paths"))
    specialist_required = bool(specialist_paths) or tier in {"agents", "studio"}
    if not specialist_required and not config_was_provided and not config_file_exists:
        if explicit_backend:
            result.update({
                "allowed": True,
                "reason": "explicit_backend",
                "detail": f"Explicit backend '{explicit_backend}' selected for this call.",
            })
        else:
            result["detail"] = "No backend was configured explicitly for this runtime call."
        return result

    if component_name and not is_component_active(component_name, config=config, config_path=config_path):
        detail = (
            f"{component_name} is disabled at the current engagement level. "
            f"Update cc_engagement.json before invoking this runtime path."
        )
        result.update({
            "allowed": False,
            "enforced": True,
            "reason": "component_disabled",
            "detail": detail,
        })
        return result

    if not specialist_required:
        if explicit_backend:
            result.update({
                "allowed": True,
                "reason": "explicit_backend",
                "detail": f"Explicit backend '{explicit_backend}' selected for this call.",
            })
        else:
            result["detail"] = "No backend was configured explicitly for this runtime call."
        return result

    specialist_path = find_specialist_path(
        normalized_role,
        specialist_paths,
        requested_backend=requested_backend,
    )
    if specialist_path is None:
        detail = (
            f"No active specialist path matches '{normalized_role}'. "
            f"Persist an explicit specialist path in cc_engagement.json "
            f"before using this runtime path."
        )
        result.update({
            "allowed": False,
            "reason": "missing_specialist_path",
            "detail": detail,
        })
        return result

    result["specialist_path"] = specialist_path
    role_id = str(specialist_path.get("role_id") or normalized_role).strip() or normalized_role
    label = str(specialist_path.get("label") or role_id).strip() or role_id
    execution_mode = str(
        specialist_path.get("execution_mode")
        or infer_specialist_execution_mode(
            specialist_path.get("permission"),
            active=specialist_path.get("active", True) is not False,
        )
    ).strip().lower()
    result["execution_mode"] = execution_mode

    if specialist_path.get("active", True) is False or execution_mode == "disabled":
        detail = f"{label} is disabled in the specialist consent matrix."
        result.update({
            "allowed": False,
            "reason": "specialist_disabled",
            "detail": detail,
        })
        return result

    if execution_mode == "human_mediated":
        detail = (
            f"{label} is configured as human_mediated. "
            f"Use `cc consult-packet create --role {role_id}` and import the concise result."
        )
        result.update({
            "allowed": False,
            "reason": "human_mediated_only",
            "detail": detail,
        })
        return result

    resolved_backend = str(requested_backend or specialist_path.get("backend") or "").strip()
    resolved_model = str(requested_model or specialist_path.get("model") or "").strip()
    result["backend"] = resolved_backend
    result["model"] = resolved_model

    if not resolved_backend:
        detail = f"{label} has no configured backend/API label for routed runtime use."
        result.update({
            "allowed": False,
            "reason": "missing_backend",
            "detail": detail,
        })
        return result

    gateway = _safe_load_json_object(gateway_config_path)
    gateway_backends = gateway.get("backends", {})
    gateway_backends = gateway_backends if isinstance(gateway_backends, dict) else {}
    if not os.path.exists(gateway_config_path):
        detail = (
            f"gateway_config.json is required for {execution_mode} specialist paths. "
            f"Configure the gateway before using {label}."
        )
        result.update({
            "allowed": False,
            "reason": "missing_gateway_config",
            "detail": detail,
        })
        return result

    if (
        gateway_backends
        and resolved_backend not in gateway_backends
        and resolved_backend not in SUPPORTED_RUNTIME_ADAPTERS
    ):
        detail = (
            f"{label} references gateway backend '{resolved_backend}', "
            "but that backend is not registered in gateway_config.json."
        )
        result.update({
            "allowed": False,
            "reason": "unknown_gateway_backend",
            "detail": detail,
        })
        return result

    backend_profile = gateway_backends.get(resolved_backend, {})
    backend_profile = backend_profile if isinstance(backend_profile, dict) else {}
    adapter = resolved_backend
    if resolved_backend not in SUPPORTED_RUNTIME_ADAPTERS:
        adapter = str(
            backend_profile.get("adapter")
            or backend_profile.get("type")
            or backend_profile.get("transport")
            or resolved_backend
        ).strip().lower()
        adapter = {
            "claude_cli": "claude",
            "openai_compatible": "openai",
        }.get(adapter, adapter)
    result["backend_profile"] = resolved_backend
    result["backend"] = adapter
    resolved_backend = adapter

    call_limit = specialist_path.get("max_calls", 0)
    try:
        call_limit = int(call_limit)
    except (TypeError, ValueError):
        call_limit = 0
    if call_limit < 0:
        call_limit = 0
    calls_used = count_specialist_runtime_calls(role_id, event_log_path)
    result["calls_used"] = calls_used
    result["call_limit"] = call_limit

    if call_limit > 0 and calls_used >= call_limit:
        detail = (
            f"{label} has reached its per-session call cap "
            f"({calls_used}/{call_limit})."
        )
        result.update({
            "allowed": False,
            "reason": "path_call_limit_reached",
            "detail": detail,
        })
        return result

    if execution_mode == "auto_bounded":
        budget = check_budget(config=config, config_path=config_path)
        if not budget["allowed"]:
            detail = (
                f"Global session budget is exhausted "
                f"({budget.get('current_calls', 0)}/{budget.get('max_calls', 0)}). "
                f"Auto-bounded path {label} cannot run."
            )
            result.update({
                "allowed": False,
                "reason": "budget_exhausted",
                "detail": detail,
            })
            return result

    result.update({
        "allowed": True,
        "reason": "specialist_path_allowed",
        "detail": (
            f"Routed via {label} ({execution_mode}) "
            f"on backend '{resolved_backend}'."
        ),
    })
    return result


def _safe_round_gb(value):
    """Round GB values consistently for user-facing diagnostics."""
    if value is None:
        return None
    return round(float(value), 1)


def detect_total_ram_gb():
    """Best-effort total system RAM detection in GiB."""
    override = os.environ.get("CC_TOTAL_RAM_GB")
    if override:
        try:
            return _safe_round_gb(float(override))
        except ValueError:
            pass

    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return _safe_round_gb(status.ullTotalPhys / (1024 ** 3))

    try:
        page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else None
        phys_pages = os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else None
    except (ValueError, OSError, AttributeError):
        page_size = None
        phys_pages = None
    if isinstance(page_size, int) and isinstance(phys_pages, int):
        return _safe_round_gb((page_size * phys_pages) / (1024 ** 3))
    return None


def detect_primary_gpu_vram_gb():
    """Best-effort primary GPU VRAM detection in GiB."""
    override = os.environ.get("CC_GPU_VRAM_GB")
    if override:
        try:
            return _safe_round_gb(float(override))
        except ValueError:
            pass

    try:
        if os.name == "nt":
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_VideoController | "
                    "Where-Object {$_.AdapterRAM -gt 0} | "
                    "Measure-Object -Maximum AdapterRAM).Maximum",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                text = result.stdout.strip()
                if text.isdigit():
                    return _safe_round_gb(int(text) / (1024 ** 3))

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            values = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    values.append(int(line))
            if values:
                return _safe_round_gb(max(values) / 1024.0)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def assess_local_tandem_capacity(total_ram_gb=None, gpu_vram_gb=None):
    """Assess whether the machine is a good fit for dual-model local tandem."""
    total_ram_gb = (
        _safe_round_gb(total_ram_gb)
        if total_ram_gb is not None
        else detect_total_ram_gb()
    )
    gpu_vram_gb = (
        _safe_round_gb(gpu_vram_gb)
        if gpu_vram_gb is not None
        else detect_primary_gpu_vram_gb()
    )

    status = "recommended"
    preferred_mode = "dual_model_parallel"
    reasons = []

    if total_ram_gb is None:
        status = "risky"
        preferred_mode = "single_model_consult"
        reasons.append("System RAM could not be detected automatically.")
    elif total_ram_gb < LOCAL_TANDEM_MIN_RAM_GB:
        status = "disabled"
        preferred_mode = "single_model_consult"
        reasons.append(
            f"Total RAM below {int(LOCAL_TANDEM_MIN_RAM_GB)} GB makes two local models impractical."
        )
    elif total_ram_gb < LOCAL_TANDEM_RECOMMENDED_RAM_GB:
        status = "risky"
        preferred_mode = "dual_model_sequential"
        reasons.append(
            f"Total RAM between {int(LOCAL_TANDEM_MIN_RAM_GB)} and "
            f"{int(LOCAL_TANDEM_RECOMMENDED_RAM_GB)} GB favors sequential tandem, not an aggressive dual-load."
        )
    else:
        reasons.append(
            f"Total RAM is at or above {int(LOCAL_TANDEM_RECOMMENDED_RAM_GB)} GB."
        )

    if gpu_vram_gb is None:
        if preferred_mode == "dual_model_parallel":
            preferred_mode = "dual_model_sequential"
        reasons.append("GPU VRAM not detected; assume sequential loading unless proven otherwise.")
    elif gpu_vram_gb < LOCAL_TANDEM_MIN_VRAM_GB:
        if status == "recommended":
            status = "risky"
        preferred_mode = (
            "single_model_consult" if status == "disabled" else "dual_model_sequential"
        )
        reasons.append(
            f"GPU VRAM below {int(LOCAL_TANDEM_MIN_VRAM_GB)} GB is usually not enough for two medium local models at once."
        )
    elif gpu_vram_gb < LOCAL_TANDEM_RECOMMENDED_VRAM_GB:
        if status == "recommended":
            status = "risky"
        preferred_mode = "dual_model_sequential"
        reasons.append(
            f"GPU VRAM below {int(LOCAL_TANDEM_RECOMMENDED_VRAM_GB)} GB suggests quantized or staggered loading."
        )
    else:
        reasons.append(
            f"GPU VRAM is at or above {int(LOCAL_TANDEM_RECOMMENDED_VRAM_GB)} GB."
        )

    if status == "disabled":
        summary = "Prefer single-model local consult; do not rely on tandem."
    elif status == "risky":
        summary = "Local tandem is possible, but keep it sequential/quantized."
    else:
        summary = "Local tandem looks viable on this machine."

    return {
        "status": status,
        "preferred_mode": preferred_mode,
        "total_ram_gb": total_ram_gb,
        "gpu_vram_gb": gpu_vram_gb,
        "summary": summary,
        "reasons": reasons,
    }


def format_local_tandem_assessment(assessment):
    """Render a concise one-line summary for a local tandem assessment."""
    ram_text = (
        f"RAM {assessment['total_ram_gb']} GB"
        if assessment.get("total_ram_gb") is not None
        else "RAM unknown"
    )
    gpu_text = (
        f"GPU VRAM {assessment['gpu_vram_gb']} GB"
        if assessment.get("gpu_vram_gb") is not None
        else "GPU VRAM unknown"
    )
    return (
        f"{assessment.get('status', 'unknown')} / "
        f"{assessment.get('preferred_mode', 'unknown')} / "
        f"{ram_text} / {gpu_text} - "
        f"{assessment.get('summary', '').strip()}"
    )
