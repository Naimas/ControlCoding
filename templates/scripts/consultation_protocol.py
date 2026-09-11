#!/usr/bin/env python3
"""consultation_protocol.py - Structured inter-agent consultation protocol.

A library (not an agent) that agents import to structure their consultations.
Provides:
1. Message formats - structured request/response templates
2. Routing table - situation-to-agent mapping
3. Escalation logic - when to add more consultants
4. Convergence criteria - when to stop

Two consultation paths:
  Path A (agent-to-agent): primary, project-aware
  Path B (agent-to-external): via mcp_consultant.py transport, isolated

Design document: dev/design/11_DSN_ConsultationProtocol_InProgress.md
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime


# ---------------------------------------------------------------------------
# Message Formats
# ---------------------------------------------------------------------------

@dataclass
class Brief:
    """Structured consultation request with context and questions."""
    context: str
    constraints: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)

    def to_prompt(self) -> str:
        sections = [f"## Context\n{self.context}"]
        if self.constraints:
            sections.append(
                "## Constraints\n"
                + "\n".join(f"- {c}" for c in self.constraints))
        if self.artifacts:
            for name, content in self.artifacts.items():
                sections.append(f"## {name}\n```\n{content}\n```")
        if self.questions:
            sections.append(
                "## Questions\n"
                + "\n".join(
                    f"{i+1}. {q}" for i, q in enumerate(self.questions)))
        return "\n\n".join(sections)


@dataclass
class DesignReview:
    """Request to review a design document."""
    document: str
    focus_areas: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    questions: list = field(default_factory=list)

    def to_prompt(self) -> str:
        sections = [f"## Design Document\n{self.document}"]
        if self.focus_areas:
            sections.append(
                "## Focus Areas\n"
                + "\n".join(f"- {a}" for a in self.focus_areas))
        if self.constraints:
            sections.append(
                "## Constraints\n"
                + "\n".join(f"- {c}" for c in self.constraints))
        if self.questions:
            sections.append(
                "## Questions\n"
                + "\n".join(
                    f"{i+1}. {q}" for i, q in enumerate(self.questions)))
        return "\n\n".join(sections)


@dataclass
class AuditRequest:
    """Post-implementation review request."""
    scope: str
    files: list = field(default_factory=list)
    focus: list = field(default_factory=list)
    test_results: str = ""

    def to_prompt(self) -> str:
        sections = [f"## Audit Scope\n{self.scope}"]
        if self.files:
            sections.append(
                "## Files\n"
                + "\n".join(f"- {f}" for f in self.files))
        if self.focus:
            sections.append(
                "## Focus\n"
                + "\n".join(f"- {f}" for f in self.focus))
        if self.test_results:
            sections.append(
                f"## Test Results\n```\n{self.test_results}\n```")
        return "\n\n".join(sections)


@dataclass
class PatchRequest:
    """Request specific fixes for identified issues."""
    findings: list = field(default_factory=list)
    priority: str = "should_fix"
    constraints: list = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)

    def to_prompt(self) -> str:
        sections = [f"## Priority: {self.priority}"]
        if self.findings:
            lines = ["## Findings"]
            for f in self.findings:
                fid = f.get("id", "?")
                desc = f.get("description", "")
                sev = f.get("severity", "medium")
                loc = f.get("location", "")
                lines.append(
                    f"- **{fid}** [{sev}] {desc}"
                    + (f" @ {loc}" if loc else ""))
            sections.append("\n".join(lines))
        if self.constraints:
            sections.append(
                "## Constraints\n"
                + "\n".join(f"- {c}" for c in self.constraints))
        if self.artifacts:
            for name, content in self.artifacts.items():
                sections.append(f"## {name}\n```\n{content}\n```")
        return "\n\n".join(sections)


@dataclass
class ConsensusReport:
    """Synthesis of multiple consultation results."""
    topic: str
    sources: list = field(default_factory=list)
    agreements: list = field(default_factory=list)
    disagreements: list = field(default_factory=list)
    resolution: list = field(default_factory=list)
    final_decision: str = ""

    def to_prompt(self) -> str:
        sections = [f"## Topic\n{self.topic}"]
        if self.sources:
            lines = ["## Sources"]
            for s in self.sources:
                src = s.get("source", "?")
                verdict = s.get("verdict", "")
                points = s.get("key_points", [])
                lines.append(f"### {src}: {verdict}")
                for p in points:
                    lines.append(f"  - {p}")
            sections.append("\n".join(lines))
        if self.agreements:
            sections.append(
                "## Agreements\n"
                + "\n".join(f"- {a}" for a in self.agreements))
        if self.disagreements:
            sections.append(
                "## Disagreements\n"
                + "\n".join(f"- {d}" for d in self.disagreements))
        if self.resolution:
            sections.append(
                "## Resolution\n"
                + "\n".join(f"- {r}" for r in self.resolution))
        if self.final_decision:
            sections.append(f"## Final Decision\n{self.final_decision}")
        return "\n\n".join(sections)


@dataclass
class QuickQuestion:
    """Simple question, minimal ceremony."""
    question: str
    context: str = ""

    def to_prompt(self) -> str:
        if self.context:
            return f"{self.question}\n\nContext: {self.context}"
        return self.question


# Format registry for lookup by name
FORMAT_CLASSES = {
    "Brief": Brief,
    "DesignReview": DesignReview,
    "AuditRequest": AuditRequest,
    "PatchRequest": PatchRequest,
    "ConsensusReport": ConsensusReport,
    "QuickQuestion": QuickQuestion,
}


# ---------------------------------------------------------------------------
# Verdict Point Parsing
# ---------------------------------------------------------------------------

# Pattern: - POINT: ... | POSITION: agree/disagree/uncertain | REASON: ...
_VERDICT_PATTERN = re.compile(
    r"-\s*POINT:\s*(?P<point>[^|]+?)\s*\|\s*"
    r"POSITION:\s*(?P<position>agree|disagree|uncertain)\s*\|\s*"
    r"REASON:\s*(?P<reason>.+)",
    re.IGNORECASE,
)


def parse_verdict_points(response: str) -> list[dict]:
    """Extract structured verdict points from a response.

    Looks for a ## Verdict section with lines like:
    - POINT: key decision | POSITION: agree | REASON: one line
    """
    points = []
    # Find the Verdict section
    verdict_match = re.search(
        r"##\s*Verdict\s*\n(.*?)(?:\n##|\Z)", response, re.DOTALL)
    if not verdict_match:
        return points

    verdict_text = verdict_match.group(1)
    for m in _VERDICT_PATTERN.finditer(verdict_text):
        points.append({
            "point": m.group("point").strip(),
            "position": m.group("position").strip().lower(),
            "reason": m.group("reason").strip(),
        })
    return points


VERDICT_INSTRUCTION = (
    "\n\nAt the end of your response, include a VERDICT section:\n"
    "## Verdict\n"
    "- POINT: [key decision point] | POSITION: [agree/disagree/uncertain]"
    " | REASON: [one line]\n"
    "- POINT: [key decision point] | POSITION: [agree/disagree/uncertain]"
    " | REASON: [one line]\n"
)


# ---------------------------------------------------------------------------
# Routing Table
# ---------------------------------------------------------------------------

# Situation -> (primary_agent, secondary_agent, min_agents, max_agents, path)
# path: "agent" = Path A, "external" = Path B, "both" = A then B
ROUTING_TABLE = {
    "architecture_decision": ("ArchitectAgent", "SocraticAgent", 2, 3, "agent"),
    "design_review":         ("ArchitectAgent", "ReviewerAgent", 2, 3, "both"),
    "security_concern":      ("ExpertAgent:security", None, 1, 2, "agent"),
    "bug_diagnosis":         ("DebuggerAgent", None, 1, 2, "agent"),
    "code_review":           ("ReviewerAgent", None, 1, 1, "agent"),
    "assumption_challenge":  ("SocraticAgent", None, 1, 1, "agent"),
    "implementation_guidance": ("ArchitectAgent", None, 1, 1, "agent"),
    "spec_validation":       ("ExpertAgent", None, 1, 1, "agent"),
    "plan_review":           ("ConciergeAgent", "ArchitectAgent", 1, 2, "agent"),
    "critical_decision":     ("ArchitectAgent", "SocraticAgent", 2, 3, "both"),
    "domain_question":       ("ExpertAgent", None, 1, 1, "agent"),
    "consensus_needed":      ("ArchitectAgent", "ReviewerAgent", 3, 3, "both"),
}

# Situation -> default format class
_SITUATION_FORMATS = {
    "architecture_decision": Brief,
    "design_review":         DesignReview,
    "security_concern":      Brief,
    "bug_diagnosis":         Brief,
    "code_review":           AuditRequest,
    "assumption_challenge":  Brief,
    "implementation_guidance": QuickQuestion,
    "spec_validation":       Brief,
    "plan_review":           Brief,
    "critical_decision":     Brief,
    "domain_question":       QuickQuestion,
    "consensus_needed":      Brief,
}


# ---------------------------------------------------------------------------
# ConsultationPlan
# ---------------------------------------------------------------------------

@dataclass
class ConsultationPlan:
    """Plan for a consultation: who, how, format, convergence."""
    agents: list = field(default_factory=list)
    external: bool = False
    format: type = Brief
    use_tandem: bool = False
    max_rounds: int = 3
    convergence: str = "first_answer"
    max_calls: int = 10
    timeout_seconds: int = 300
    domain: str = ""


# ---------------------------------------------------------------------------
# ConsultationResult
# ---------------------------------------------------------------------------

@dataclass
class ConsultationResult:
    """Result of a structured consultation."""
    answer: str = ""
    confidence: str = "medium"
    sources: list = field(default_factory=list)
    agreement_mode: str = "first_answer"
    agreement_reached: bool = False
    disagreements: list = field(default_factory=list)
    format_used: str = ""
    rounds: int = 0
    calls_used: int = 0
    budget_exceeded: bool = False
    escalated: bool = False
    audit_trail: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "confidence": self.confidence,
            "sources": self.sources,
            "agreement_mode": self.agreement_mode,
            "agreement_reached": self.agreement_reached,
            "disagreements": self.disagreements,
            "format_used": self.format_used,
            "rounds": self.rounds,
            "calls_used": self.calls_used,
            "budget_exceeded": self.budget_exceeded,
            "escalated": self.escalated,
            "audit_trail_length": len(self.audit_trail),
        }


# ---------------------------------------------------------------------------
# Convergence Logic
# ---------------------------------------------------------------------------

def check_convergence(
    responses: list[dict],
    strategy: str,
) -> tuple[bool, list[str]]:
    """Check if consultation responses converge.

    Each response dict has: {"agent": str, "response": str, "verdict_points": list}

    Returns (converged: bool, disagreements: list[str]).
    """
    if not responses:
        return False, ["no responses"]

    if strategy == "first_answer":
        return True, []

    # Collect all verdict points across responses
    all_points = set()
    for r in responses:
        for vp in r.get("verdict_points", []):
            all_points.add(vp["point"])

    if not all_points:
        # No structured verdict points - fallback: treat as converged
        # if all responded (can't compare free-text mechanically)
        return len(responses) >= 2, []

    disagreements = []
    for point in sorted(all_points):
        positions = {}
        for r in responses:
            agent = r.get("agent", "?")
            for vp in r.get("verdict_points", []):
                if vp["point"] == point:
                    positions[agent] = vp["position"]

        unique_positions = set(positions.values()) - {"uncertain"}
        if len(unique_positions) > 1:
            detail = ", ".join(
                f"{a}={p}" for a, p in sorted(positions.items()))
            disagreements.append(f"{point}: {detail}")

    if strategy == "consensus":
        # Consensus requires 2+ sources - a single voice is not consensus
        if len(responses) < 2:
            return False, disagreements or ["insufficient sources for consensus"]
        return len(disagreements) == 0, disagreements
    elif strategy == "majority":
        # Majority: for each point, at least 2/3 agree
        for point in sorted(all_points):
            positions = []
            for r in responses:
                for vp in r.get("verdict_points", []):
                    if vp["point"] == point:
                        positions.append(vp["position"])
            if positions:
                from collections import Counter
                counts = Counter(positions)
                most_common_count = counts.most_common(1)[0][1]
                if most_common_count < len(positions) * 2 / 3:
                    return False, disagreements
        return True, disagreements
    elif strategy == "unanimous":
        # Unanimous: all agree, no uncertain
        for point in sorted(all_points):
            positions = set()
            for r in responses:
                for vp in r.get("verdict_points", []):
                    if vp["point"] == point:
                        positions.add(vp["position"])
            if "uncertain" in positions or len(positions) > 1:
                return False, disagreements
        return len(disagreements) == 0, disagreements

    return len(disagreements) == 0, disagreements


def derive_confidence(
    agreement_reached: bool,
    source_count: int,
    budget_exceeded: bool,
    criticality: str,
) -> str:
    """Derive confidence from measurable factors.

    - high: agreement + 2+ sources + no budget exceeded
    - medium: agreement but only 1 source, or budget exceeded with partial
    - low: no agreement, or single source on high/critical question
    """
    if not agreement_reached:
        return "low"
    if budget_exceeded:
        return "medium"
    if source_count >= 2:
        return "high"
    if source_count == 1 and criticality in ("high", "critical"):
        return "low"
    return "medium"


# ---------------------------------------------------------------------------
# Anti-Loop Protection
# ---------------------------------------------------------------------------

AUDIT_TRAIL_CAP = 5000  # chars


class AntiLoopTracker:
    """Tracks consultation state to prevent infinite loops.

    Rules:
    1. Max consultations per task: max_consultations (default 5)
    2. Max 1 tandem per decision point
    3. No recursive consultation on same topic (hash-based cache)
    4. Depth limit: 3
    """

    def __init__(self, max_consultations: int = 5, max_depth: int = 3):
        self.max_consultations = max_consultations
        self.max_depth = max_depth
        self._consultation_count: int = 0
        self._tandem_topics: set = set()
        self._topic_cache: dict = {}  # topic_hash -> ConsultationResult
        self._current_depth: int = 0

    def _topic_hash(self, question: str) -> str:
        """Hash a question for deduplication.

        Uses exact-match after normalization (lowercase + strip).
        Near-identical questions ("Is this secure?" vs "Is this secure
        enough?") produce different hashes and are treated as distinct
        topics. This is intentional: different wording may reflect
        different intent or new evidence. Use reset_for_new_evidence()
        to explicitly clear the cache when context changes.
        """
        normalized = question.strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def check_allowed(self, question: str, use_tandem: bool = False
                      ) -> tuple[bool, str]:
        """Check if a consultation is allowed.

        Returns (allowed: bool, reason: str).
        If not allowed, reason explains why.
        """
        if self._consultation_count >= self.max_consultations:
            return False, (
                f"max consultations reached ({self.max_consultations})")

        if self._current_depth >= self.max_depth:
            return False, f"depth limit reached ({self.max_depth})"

        topic_h = self._topic_hash(question)
        if topic_h in self._topic_cache:
            return False, "same topic already consulted (cached)"

        if use_tandem and topic_h in self._tandem_topics:
            return False, "tandem already used for this topic"

        return True, ""

    def get_cached(self, question: str) -> ConsultationResult | None:
        """Return cached result for a question, or None."""
        topic_h = self._topic_hash(question)
        return self._topic_cache.get(topic_h)

    def record(self, question: str, result: ConsultationResult,
               used_tandem: bool = False):
        """Record a completed consultation."""
        self._consultation_count += 1
        topic_h = self._topic_hash(question)
        self._topic_cache[topic_h] = result
        if used_tandem:
            self._tandem_topics.add(topic_h)

    def enter_depth(self):
        """Increment depth counter (for nested consultations)."""
        self._current_depth += 1

    def exit_depth(self):
        """Decrement depth counter."""
        self._current_depth = max(0, self._current_depth - 1)

    def reset_for_new_evidence(self, question: str):
        """Clear cache for a question when new evidence arrives."""
        topic_h = self._topic_hash(question)
        self._topic_cache.pop(topic_h, None)

    @property
    def consultation_count(self) -> int:
        return self._consultation_count

    @property
    def current_depth(self) -> int:
        return self._current_depth


# ---------------------------------------------------------------------------
# Route Function
# ---------------------------------------------------------------------------

def route(situation: str, criticality: str = "normal",
          domain: str = "") -> ConsultationPlan:
    """Determine who to ask, how many, and in what format.

    Args:
        situation: key from ROUTING_TABLE (e.g., "design_review")
        criticality: "low" | "normal" | "high" | "critical"
        domain: optional domain for ExpertAgent

    Returns:
        ConsultationPlan with agents, format, convergence strategy, etc.
    """
    entry = ROUTING_TABLE.get(situation)
    if entry is None:
        # Unknown situation - fallback to single QuickQuestion
        return ConsultationPlan(
            agents=["ExpertAgent"] if domain else ["ArchitectAgent"],
            format=QuickQuestion,
            convergence="first_answer",
            max_rounds=1,
            max_calls=2,
            domain=domain,
        )

    primary, secondary, min_agents, max_agents, path = entry

    # Resolve domain suffix
    if domain and primary == "ExpertAgent":
        primary = f"ExpertAgent:{domain}"
    elif domain and ":security" not in primary:
        # domain param but primary isn't ExpertAgent - keep primary,
        # domain goes to plan
        pass

    # Build agent list
    agents = [primary]
    if secondary and (min_agents >= 2 or criticality in ("high", "critical")):
        agents.append(secondary)

    # Criticality escalation
    if criticality == "low":
        fmt = QuickQuestion
        convergence = "first_answer"
        agents = [primary]
        external = False
        use_tandem = False
        max_rounds = 1
        max_calls = 2
        timeout = 120
    elif criticality == "normal":
        fmt = _SITUATION_FORMATS.get(situation, Brief)
        convergence = "first_answer" if len(agents) == 1 else "consensus"
        external = path == "both" or path == "external"
        use_tandem = False
        max_rounds = 3
        max_calls = 6
        timeout = 300
    elif criticality == "high":
        fmt = _SITUATION_FORMATS.get(situation, Brief)
        convergence = "consensus"
        # High: use max agents + external + tandem
        if secondary and secondary not in agents:
            agents.append(secondary)
        while len(agents) < max_agents:
            if "SocraticAgent" not in agents:
                agents.append("SocraticAgent")
                break
            break
        external = True
        use_tandem = True
        max_rounds = 5
        max_calls = 10
        timeout = 300
    elif criticality == "critical":
        fmt = _SITUATION_FORMATS.get(situation, Brief)
        convergence = "unanimous"
        # Critical: max agents + external + socratic challenge
        if secondary and secondary not in agents:
            agents.append(secondary)
        if "SocraticAgent" not in agents:
            agents.append("SocraticAgent")
        while len(agents) < max_agents:
            break
        external = True
        use_tandem = True
        max_rounds = 5
        max_calls = 15
        timeout = 300
    else:
        # Unknown criticality - treat as normal
        fmt = _SITUATION_FORMATS.get(situation, Brief)
        convergence = "first_answer"
        external = False
        use_tandem = False
        max_rounds = 3
        max_calls = 6
        timeout = 300

    return ConsultationPlan(
        agents=agents,
        external=external,
        format=fmt,
        use_tandem=use_tandem,
        max_rounds=max_rounds,
        convergence=convergence,
        max_calls=max_calls,
        timeout_seconds=timeout,
        domain=domain,
    )


# ---------------------------------------------------------------------------
# Valid agent names (for target_agent validation)
# ---------------------------------------------------------------------------

VALID_AGENTS = {
    "ConciergeAgent",
    "ArchitectAgent",
    "CoderAgent",
    "ReviewerAgent",
    "DebuggerAgent",
    "SocraticAgent",
    "CodeWarden",
    "ExpertAgent",
}


def validate_target_agent(agent_name: str) -> tuple[bool, str]:
    """Validate a target_agent name.

    Accepts bare names ("ArchitectAgent") and domain-suffixed names
    ("ExpertAgent:security"). Returns (valid, error_message).
    """
    if not agent_name or not isinstance(agent_name, str):
        return False, "target_agent must be a non-empty string"
    base = agent_name.strip().split(":")[0]
    if base in VALID_AGENTS:
        return True, ""
    return False, (
        f"unknown agent '{agent_name}'. "
        f"Valid agents: {', '.join(sorted(VALID_AGENTS))}"
    )


def cap_audit_trail(trail: list, max_chars: int = AUDIT_TRAIL_CAP) -> list:
    """Cap audit trail to max_chars total."""
    total = 0
    result = []
    for entry in trail:
        entry_str = json.dumps(entry) if isinstance(entry, dict) else str(entry)
        total += len(entry_str)
        if total > max_chars:
            result.append({"_truncated": True,
                           "remaining_entries": len(trail) - len(result)})
            break
        result.append(entry)
    return result
