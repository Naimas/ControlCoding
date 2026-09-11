"""Tests for consultation_protocol.py - Consultation Protocol (v2.2).

Tests cover:
- Message format dataclasses and to_prompt() rendering
- Routing table and route() function
- Convergence logic (verdict point parsing, strategies)
- Anti-loop protection
- ConsultationPlan and ConsultationResult
- BaseAgent.consult() integration
- Failure handling and edge cases
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from consultation_protocol import (
    Brief, DesignReview, AuditRequest, PatchRequest,
    ConsensusReport, QuickQuestion,
    FORMAT_CLASSES,
    parse_verdict_points, VERDICT_INSTRUCTION,
    ROUTING_TABLE, route,
    ConsultationPlan, ConsultationResult,
    check_convergence, derive_confidence,
    AntiLoopTracker, cap_audit_trail,
)


# ===========================================================================
# Message Format Tests
# ===========================================================================

class TestBrief:
    def test_basic_rendering(self):
        b = Brief(
            context="We need to refactor auth",
            constraints=["Python stdlib only"],
            questions=["Is this approach sound?"],
        )
        prompt = b.to_prompt()
        assert "## Context" in prompt
        assert "We need to refactor auth" in prompt
        assert "## Constraints" in prompt
        assert "- Python stdlib only" in prompt
        assert "## Questions" in prompt
        assert "1. Is this approach sound?" in prompt

    def test_with_artifacts(self):
        b = Brief(
            context="Review this code",
            artifacts={"Code": "def foo(): pass"},
        )
        prompt = b.to_prompt()
        assert "## Code" in prompt
        assert "def foo(): pass" in prompt
        assert "```" in prompt

    def test_empty_fields(self):
        b = Brief(context="Just context")
        prompt = b.to_prompt()
        assert "## Context" in prompt
        assert "## Constraints" not in prompt
        assert "## Questions" not in prompt

    def test_multiple_questions(self):
        b = Brief(
            context="ctx",
            questions=["Q1?", "Q2?", "Q3?"],
        )
        prompt = b.to_prompt()
        assert "1. Q1?" in prompt
        assert "2. Q2?" in prompt
        assert "3. Q3?" in prompt


class TestDesignReview:
    def test_basic_rendering(self):
        dr = DesignReview(
            document="# My Design\nContent here",
            focus_areas=["security", "performance"],
            constraints=["Must work with existing hooks"],
            questions=["Is the schema correct?"],
        )
        prompt = dr.to_prompt()
        assert "## Design Document" in prompt
        assert "# My Design" in prompt
        assert "## Focus Areas" in prompt
        assert "- security" in prompt
        assert "## Constraints" in prompt
        assert "## Questions" in prompt

    def test_empty_optional_fields(self):
        dr = DesignReview(document="doc")
        prompt = dr.to_prompt()
        assert "## Design Document" in prompt
        assert "## Focus Areas" not in prompt


class TestAuditRequest:
    def test_basic_rendering(self):
        ar = AuditRequest(
            scope="New auth module",
            files=["auth.py", "tests/test_auth.py"],
            focus=["security", "coverage"],
            test_results="12 passed, 0 failed",
        )
        prompt = ar.to_prompt()
        assert "## Audit Scope" in prompt
        assert "## Files" in prompt
        assert "- auth.py" in prompt
        assert "## Focus" in prompt
        assert "## Test Results" in prompt
        assert "12 passed" in prompt


class TestPatchRequest:
    def test_basic_rendering(self):
        pr = PatchRequest(
            findings=[{
                "id": "ENG-001",
                "description": "Missing null check",
                "severity": "high",
                "location": "auth.py:42",
            }],
            priority="must_fix",
            constraints=["No breaking changes"],
        )
        prompt = pr.to_prompt()
        assert "## Priority: must_fix" in prompt
        assert "ENG-001" in prompt
        assert "[high]" in prompt
        assert "@ auth.py:42" in prompt
        assert "## Constraints" in prompt

    def test_with_artifacts(self):
        pr = PatchRequest(
            findings=[],
            priority="should_fix",
            artifacts={"Failing Test": "assert x == 1"},
        )
        prompt = pr.to_prompt()
        assert "## Failing Test" in prompt


class TestConsensusReport:
    def test_basic_rendering(self):
        cr = ConsensusReport(
            topic="Auth approach",
            sources=[{
                "source": "ArchitectAgent",
                "verdict": "approve",
                "key_points": ["Schema is sound", "Good separation"],
            }],
            agreements=["Schema design"],
            disagreements=["Error handling"],
            resolution=["Use ArchitectAgent's approach"],
            final_decision="Proceed with schema v2",
        )
        prompt = cr.to_prompt()
        assert "## Topic" in prompt
        assert "## Sources" in prompt
        assert "### ArchitectAgent: approve" in prompt
        assert "## Agreements" in prompt
        assert "## Disagreements" in prompt
        assert "## Resolution" in prompt
        assert "## Final Decision" in prompt

    def test_empty_sources(self):
        cr = ConsensusReport(topic="test")
        prompt = cr.to_prompt()
        assert "## Topic" in prompt
        assert "## Sources" not in prompt


class TestQuickQuestion:
    def test_with_context(self):
        qq = QuickQuestion(
            question="What is the best ORM?",
            context="Python project",
        )
        prompt = qq.to_prompt()
        assert "What is the best ORM?" in prompt
        assert "Context: Python project" in prompt

    def test_without_context(self):
        qq = QuickQuestion(question="What is the best ORM?")
        prompt = qq.to_prompt()
        assert "What is the best ORM?" in prompt
        assert "Context" not in prompt


class TestFormatRegistry:
    def test_all_formats_registered(self):
        assert "Brief" in FORMAT_CLASSES
        assert "DesignReview" in FORMAT_CLASSES
        assert "AuditRequest" in FORMAT_CLASSES
        assert "PatchRequest" in FORMAT_CLASSES
        assert "ConsensusReport" in FORMAT_CLASSES
        assert "QuickQuestion" in FORMAT_CLASSES

    def test_format_count(self):
        assert len(FORMAT_CLASSES) == 6


# ===========================================================================
# Verdict Point Parsing Tests
# ===========================================================================

class TestVerdictPointParsing:
    def test_parse_valid_points(self):
        response = (
            "Some analysis text.\n\n"
            "## Verdict\n"
            "- POINT: Schema design | POSITION: agree | REASON: Well structured\n"
            "- POINT: Error handling | POSITION: disagree | REASON: Missing edge cases\n"
        )
        points = parse_verdict_points(response)
        assert len(points) == 2
        assert points[0]["point"] == "Schema design"
        assert points[0]["position"] == "agree"
        assert points[0]["reason"] == "Well structured"
        assert points[1]["position"] == "disagree"

    def test_parse_uncertain(self):
        response = (
            "## Verdict\n"
            "- POINT: Performance | POSITION: uncertain | REASON: Need benchmarks\n"
        )
        points = parse_verdict_points(response)
        assert len(points) == 1
        assert points[0]["position"] == "uncertain"

    def test_no_verdict_section(self):
        response = "Just free text, no verdict section."
        points = parse_verdict_points(response)
        assert len(points) == 0

    def test_empty_verdict_section(self):
        response = "## Verdict\n\nNothing structured here.\n\n## Next"
        points = parse_verdict_points(response)
        assert len(points) == 0

    def test_case_insensitive_position(self):
        response = (
            "## Verdict\n"
            "- POINT: X | POSITION: Agree | REASON: yes\n"
            "- POINT: Y | POSITION: DISAGREE | REASON: no\n"
        )
        points = parse_verdict_points(response)
        assert len(points) == 2
        assert points[0]["position"] == "agree"
        assert points[1]["position"] == "disagree"

    def test_verdict_instruction_constant(self):
        assert "## Verdict" in VERDICT_INSTRUCTION
        assert "POINT:" in VERDICT_INSTRUCTION
        assert "POSITION:" in VERDICT_INSTRUCTION


# ===========================================================================
# Routing Table Tests
# ===========================================================================

class TestRoutingTable:
    def test_all_situations_present(self):
        expected = {
            "architecture_decision", "design_review", "security_concern",
            "bug_diagnosis", "code_review", "assumption_challenge",
            "implementation_guidance", "spec_validation", "plan_review",
            "critical_decision", "domain_question", "consensus_needed",
        }
        assert set(ROUTING_TABLE.keys()) == expected

    def test_routing_entry_structure(self):
        for situation, entry in ROUTING_TABLE.items():
            assert len(entry) == 5, f"{situation} has wrong entry length"
            primary, secondary, min_a, max_a, path = entry
            assert isinstance(primary, str)
            assert secondary is None or isinstance(secondary, str)
            assert isinstance(min_a, int)
            assert isinstance(max_a, int)
            assert min_a <= max_a
            assert path in ("agent", "external", "both")

    def test_design_review_has_both_path(self):
        _, _, _, _, path = ROUTING_TABLE["design_review"]
        assert path == "both"

    def test_code_review_single_agent(self):
        _, _, min_a, max_a, _ = ROUTING_TABLE["code_review"]
        assert min_a == 1
        assert max_a == 1


# ===========================================================================
# Route Function Tests
# ===========================================================================

class TestRoute:
    def test_low_criticality(self):
        plan = route("design_review", "low")
        assert plan.format == QuickQuestion
        assert plan.convergence == "first_answer"
        assert len(plan.agents) == 1
        assert plan.external is False
        assert plan.use_tandem is False

    def test_normal_criticality(self):
        plan = route("design_review", "normal")
        assert plan.format == DesignReview
        assert plan.external is True  # design_review path is "both"
        assert len(plan.agents) >= 2

    def test_high_criticality(self):
        plan = route("architecture_decision", "high")
        assert plan.convergence == "consensus"
        assert plan.external is True
        assert plan.use_tandem is True
        assert len(plan.agents) >= 2

    def test_critical_criticality(self):
        plan = route("critical_decision", "critical")
        assert plan.convergence == "unanimous"
        assert plan.external is True
        assert plan.use_tandem is True
        assert "SocraticAgent" in plan.agents

    def test_unknown_situation_fallback(self):
        plan = route("nonexistent_situation")
        assert plan.format == QuickQuestion
        assert plan.convergence == "first_answer"
        assert len(plan.agents) == 1
        assert plan.agents[0] == "ArchitectAgent"

    def test_unknown_situation_with_domain(self):
        plan = route("nonexistent_situation", domain="security")
        assert plan.agents[0] == "ExpertAgent"
        assert plan.domain == "security"

    def test_domain_suffix_for_expert(self):
        plan = route("security_concern", "normal", domain="security")
        assert plan.agents[0] == "ExpertAgent:security"

    def test_domain_question_routing(self):
        plan = route("domain_question")
        assert "ExpertAgent" in plan.agents[0]

    def test_max_calls_scales_with_criticality(self):
        low = route("design_review", "low")
        normal = route("design_review", "normal")
        high = route("design_review", "high")
        critical = route("design_review", "critical")
        assert low.max_calls <= normal.max_calls
        assert normal.max_calls <= high.max_calls
        assert high.max_calls <= critical.max_calls

    def test_consensus_needed(self):
        plan = route("consensus_needed", "normal")
        assert len(plan.agents) >= 2
        assert plan.external is True

    def test_implementation_guidance_is_quick(self):
        plan = route("implementation_guidance", "normal")
        assert plan.format == QuickQuestion


# ===========================================================================
# Convergence Tests
# ===========================================================================

class TestCheckConvergence:
    def test_first_answer_always_converges(self):
        converged, disagreements = check_convergence(
            [{"agent": "A", "verdict_points": []}],
            "first_answer",
        )
        assert converged is True
        assert disagreements == []

    def test_consensus_all_agree(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "Schema", "position": "agree", "reason": "OK"},
            ]},
            {"agent": "B", "verdict_points": [
                {"point": "Schema", "position": "agree", "reason": "Fine"},
            ]},
        ]
        converged, disagreements = check_convergence(
            responses, "consensus")
        assert converged is True
        assert len(disagreements) == 0

    def test_consensus_disagree(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "Schema", "position": "agree", "reason": "OK"},
            ]},
            {"agent": "B", "verdict_points": [
                {"point": "Schema", "position": "disagree", "reason": "Bad"},
            ]},
        ]
        converged, disagreements = check_convergence(
            responses, "consensus")
        assert converged is False
        assert len(disagreements) == 1
        assert "Schema" in disagreements[0]

    def test_majority_2_of_3_agree(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "X", "position": "agree", "reason": "OK"},
            ]},
            {"agent": "B", "verdict_points": [
                {"point": "X", "position": "agree", "reason": "OK"},
            ]},
            {"agent": "C", "verdict_points": [
                {"point": "X", "position": "disagree", "reason": "No"},
            ]},
        ]
        converged, _ = check_convergence(responses, "majority")
        assert converged is True

    def test_majority_fails_when_split(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "X", "position": "agree", "reason": "OK"},
            ]},
            {"agent": "B", "verdict_points": [
                {"point": "X", "position": "disagree", "reason": "No"},
            ]},
            {"agent": "C", "verdict_points": [
                {"point": "X", "position": "uncertain", "reason": "?"},
            ]},
        ]
        converged, _ = check_convergence(responses, "majority")
        assert converged is False

    def test_unanimous_all_agree(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "X", "position": "agree", "reason": "OK"},
            ]},
            {"agent": "B", "verdict_points": [
                {"point": "X", "position": "agree", "reason": "OK"},
            ]},
        ]
        converged, _ = check_convergence(responses, "unanimous")
        assert converged is True

    def test_unanimous_fails_with_uncertain(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "X", "position": "agree", "reason": "OK"},
            ]},
            {"agent": "B", "verdict_points": [
                {"point": "X", "position": "uncertain", "reason": "?"},
            ]},
        ]
        converged, _ = check_convergence(responses, "unanimous")
        assert converged is False

    def test_no_verdict_points_fallback(self):
        responses = [
            {"agent": "A", "verdict_points": []},
            {"agent": "B", "verdict_points": []},
        ]
        converged, _ = check_convergence(responses, "consensus")
        assert converged is True  # fallback: 2+ responses = converged

    def test_empty_responses(self):
        converged, disagreements = check_convergence([], "consensus")
        assert converged is False
        assert "no responses" in disagreements

    def test_multiple_points(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "Schema", "position": "agree", "reason": "OK"},
                {"point": "Error handling", "position": "disagree", "reason": "Bad"},
            ]},
            {"agent": "B", "verdict_points": [
                {"point": "Schema", "position": "agree", "reason": "Fine"},
                {"point": "Error handling", "position": "agree", "reason": "OK"},
            ]},
        ]
        converged, disagreements = check_convergence(
            responses, "consensus")
        assert converged is False
        assert len(disagreements) == 1
        assert "Error handling" in disagreements[0]


class TestDeriveConfidence:
    def test_high_confidence(self):
        assert derive_confidence(True, 3, False, "normal") == "high"

    def test_medium_single_source(self):
        assert derive_confidence(True, 1, False, "normal") == "medium"

    def test_medium_budget_exceeded(self):
        assert derive_confidence(True, 3, True, "normal") == "medium"

    def test_low_no_agreement(self):
        assert derive_confidence(False, 3, False, "normal") == "low"

    def test_low_single_source_high_criticality(self):
        assert derive_confidence(True, 1, False, "high") == "low"

    def test_low_single_source_critical(self):
        assert derive_confidence(True, 1, False, "critical") == "low"


# ===========================================================================
# Anti-Loop Tests
# ===========================================================================

class TestAntiLoopTracker:
    def test_initial_state(self):
        tracker = AntiLoopTracker()
        assert tracker.consultation_count == 0
        assert tracker.current_depth == 0

    def test_max_consultations(self):
        tracker = AntiLoopTracker(max_consultations=2)
        result = ConsultationResult(answer="test")
        tracker.record("Q1", result)
        tracker.record("Q2", result)
        allowed, reason = tracker.check_allowed("Q3")
        assert allowed is False
        assert "max consultations" in reason

    def test_topic_cache(self):
        tracker = AntiLoopTracker()
        result = ConsultationResult(answer="cached answer")
        tracker.record("Same question", result)
        allowed, reason = tracker.check_allowed("Same question")
        assert allowed is False
        assert "cached" in reason

    def test_get_cached(self):
        tracker = AntiLoopTracker()
        result = ConsultationResult(answer="cached")
        tracker.record("Q", result)
        cached = tracker.get_cached("Q")
        assert cached is not None
        assert cached.answer == "cached"

    def test_get_cached_miss(self):
        tracker = AntiLoopTracker()
        assert tracker.get_cached("nonexistent") is None

    def test_depth_limit(self):
        tracker = AntiLoopTracker(max_depth=2)
        tracker.enter_depth()
        tracker.enter_depth()
        allowed, reason = tracker.check_allowed("Q")
        assert allowed is False
        assert "depth limit" in reason

    def test_depth_enter_exit(self):
        tracker = AntiLoopTracker()
        tracker.enter_depth()
        assert tracker.current_depth == 1
        tracker.exit_depth()
        assert tracker.current_depth == 0

    def test_depth_exit_floor(self):
        tracker = AntiLoopTracker()
        tracker.exit_depth()
        assert tracker.current_depth == 0

    def test_tandem_single_use(self):
        tracker = AntiLoopTracker()
        result = ConsultationResult(answer="tandem result")
        tracker.record("Architecture Q", result, used_tandem=True)
        allowed, reason = tracker.check_allowed(
            "Architecture Q", use_tandem=True)
        assert allowed is False
        assert "tandem" in reason or "cached" in reason

    def test_reset_for_new_evidence(self):
        tracker = AntiLoopTracker()
        result = ConsultationResult(answer="old")
        tracker.record("Q", result)
        tracker.reset_for_new_evidence("Q")
        allowed, _ = tracker.check_allowed("Q")
        assert allowed is True

    def test_different_questions_allowed(self):
        tracker = AntiLoopTracker(max_consultations=5)
        result = ConsultationResult(answer="test")
        tracker.record("Q1", result)
        allowed, _ = tracker.check_allowed("Q2")
        assert allowed is True

    def test_case_insensitive_hash(self):
        tracker = AntiLoopTracker()
        result = ConsultationResult(answer="test")
        tracker.record("Same Question", result)
        allowed, _ = tracker.check_allowed("same question")
        assert allowed is False


# ===========================================================================
# ConsultationPlan Tests
# ===========================================================================

class TestConsultationPlan:
    def test_defaults(self):
        plan = ConsultationPlan()
        assert plan.agents == []
        assert plan.external is False
        assert plan.format == Brief
        assert plan.use_tandem is False
        assert plan.max_rounds == 3
        assert plan.convergence == "first_answer"
        assert plan.max_calls == 10
        assert plan.timeout_seconds == 300
        assert plan.domain == ""


# ===========================================================================
# ConsultationResult Tests
# ===========================================================================

class TestConsultationResult:
    def test_defaults(self):
        r = ConsultationResult()
        assert r.answer == ""
        assert r.confidence == "medium"
        assert r.agreement_reached is False
        assert r.budget_exceeded is False
        assert r.escalated is False

    def test_to_dict(self):
        r = ConsultationResult(
            answer="test",
            confidence="high",
            agreement_reached=True,
            calls_used=3,
        )
        d = r.to_dict()
        assert d["answer"] == "test"
        assert d["confidence"] == "high"
        assert d["agreement_reached"] is True
        assert d["calls_used"] == 3
        assert "audit_trail_length" in d


# ===========================================================================
# Audit Trail Cap Tests
# ===========================================================================

class TestCapAuditTrail:
    def test_under_cap(self):
        trail = [{"step": 1}, {"step": 2}]
        result = cap_audit_trail(trail, max_chars=10000)
        assert len(result) == 2

    def test_over_cap(self):
        trail = [{"data": "x" * 2000} for _ in range(10)]
        result = cap_audit_trail(trail, max_chars=5000)
        assert len(result) < 10
        assert result[-1].get("_truncated") is True

    def test_empty_trail(self):
        result = cap_audit_trail([])
        assert result == []


# ===========================================================================
# BaseAgent.consult() Integration Tests
# ===========================================================================

class TestBaseAgentConsult:
    """Test the consult() method on BaseAgent via a mock concrete agent."""

    @pytest.fixture
    def agent(self, tmp_path):
        """Create a minimal concrete agent for testing."""
        from base_agent import BaseAgent, AgentStateBase

        class TestAgent(BaseAgent):
            def _create_state(self):
                return AgentStateBase()
            def _build_system_prompt(self, **ctx):
                return "test"
            def _register_tools(self):
                pass
            def _on_start(self, **ctx):
                return "start"

        agent = TestAgent(backend="ollama", model="test")
        agent.state.agent_type = "test"
        agent.state.project_root = str(tmp_path)
        return agent

    @patch("base_agent.BackendAdapter.call")
    def test_consult_returns_result(self, mock_call, agent):
        """consult() returns a ConsultationResult."""
        mock_call.return_value = (
            "Analysis complete.\n\n"
            "## Verdict\n"
            "- POINT: Schema | POSITION: agree | REASON: Sound design\n"
        )
        result = agent.consult(
            situation="design_review",
            question="Is the schema correct?",
        )
        assert hasattr(result, "answer")
        assert hasattr(result, "confidence")
        assert hasattr(result, "sources")
        assert result.calls_used > 0

    @patch("base_agent.BackendAdapter.call")
    def test_consult_anti_loop_blocks_repeat(self, mock_call, agent):
        """Repeated consultation on same topic returns cached result."""
        mock_call.return_value = "OK"
        first = agent.consult(situation="code_review", question="Review X")
        result = agent.consult(situation="code_review", question="Review X")
        # Second call should return cached result (same answer), not re-call LLM
        assert result.answer == first.answer
        # Mock should only be called once (for the first consult)
        # The second consult returns cached, so no new LLM calls
        assert mock_call.call_count == 1

    @patch("base_agent.BackendAdapter.call")
    def test_consult_low_criticality(self, mock_call, agent):
        """Low criticality uses QuickQuestion format."""
        mock_call.return_value = "Simple answer"
        result = agent.consult(
            situation="implementation_guidance",
            question="How to import?",
            criticality="low",
        )
        assert result.format_used == "QuickQuestion"
        assert result.agreement_mode == "first_answer"

    @patch("base_agent.BackendAdapter.call")
    def test_consult_with_domain(self, mock_call, agent):
        """Domain parameter is passed through."""
        mock_call.return_value = "Security analysis"
        result = agent.consult(
            situation="security_concern",
            question="Is this secure?",
            domain="security",
        )
        assert result.calls_used >= 1

    def test_consult_blocks_human_mediated_runtime_path(self, agent, tmp_path):
        control_dir = tmp_path / ".controlcoding"
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / "cc_engagement.json").write_text(
            json.dumps({
                "tier": "agents",
                "specialist_paths": [{
                    "role_id": "consultant_1",
                    "label": "Architect Consultant",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "anthropic_prod",
                    "model": "claude-sonnet",
                    "permission": "user_mediated",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }],
            }),
            encoding="utf-8",
        )
        agent.state.project_root = str(tmp_path)
        result = agent.consult(
            situation="code_review",
            question="Review this with a human-mediated consultant",
            criticality="low",
        )
        assert result.answer.startswith("[GATED]")
        assert "consult-packet create" in result.answer

    @patch("base_agent.BackendAdapter.call")
    def test_consult_surfaces_fail_closed_runtime_gate(
            self, mock_call, agent):
        with patch.dict(sys.modules, {"control_plane_utils": None}):
            result = agent.consult(
                situation="code_review",
                question="Review this with unavailable governance utilities",
                criticality="low",
            )

        assert result.answer.startswith(
            "[GATED] control_plane_utils_unavailable:")
        assert "refusing specialist runtime path" in result.answer
        assert result.sources[0]["response"].startswith(
            "[GATED] control_plane_utils_unavailable:")
        assert agent.last_governance_denial["reason"] == (
            "control_plane_utils_unavailable")
        mock_call.assert_not_called()

    @patch("base_agent.BackendAdapter.call")
    def test_consult_budget_exceeded(self, mock_call, agent):
        """Budget exceeded flag is set correctly."""
        mock_call.return_value = "Response"
        # Route to critical which has max_calls=15
        # With only one backend mock, calls_used should be much less
        result = agent.consult(
            situation="code_review",
            question="Unique question for budget test",
            criticality="low",
        )
        # Low criticality has max_calls=2, and we use 1 call
        # so budget should NOT be exceeded
        assert result.budget_exceeded is False


class TestAgentNameToRole:
    """Test the static mapping from agent names to mcp roles."""

    def test_known_agents(self):
        from base_agent import BaseAgent
        assert BaseAgent._agent_name_to_role("ArchitectAgent") == "architect"
        assert BaseAgent._agent_name_to_role("ReviewerAgent") == "reviewer"
        assert BaseAgent._agent_name_to_role("DebuggerAgent") == "debug"
        assert BaseAgent._agent_name_to_role("SocraticAgent") == "socratic"
        assert BaseAgent._agent_name_to_role("ExpertAgent") == "scientist"
        assert BaseAgent._agent_name_to_role("ConciergeAgent") == "planner"

    def test_domain_suffix_stripped(self):
        from base_agent import BaseAgent
        assert BaseAgent._agent_name_to_role("ExpertAgent:security") == "scientist"

    def test_unknown_agent_fallback(self):
        from base_agent import BaseAgent
        assert BaseAgent._agent_name_to_role("UnknownAgent") == "architect"


# ===========================================================================
# Path Selection Tests
# ===========================================================================

class TestPathSelection:
    def test_agent_only_path(self):
        plan = route("code_review", "normal")
        assert plan.external is False

    def test_both_path_normal(self):
        plan = route("design_review", "normal")
        assert plan.external is True

    def test_high_always_external(self):
        plan = route("code_review", "high")
        assert plan.external is True

    def test_critical_always_external(self):
        plan = route("bug_diagnosis", "critical")
        assert plan.external is True

    def test_low_never_external(self):
        plan = route("design_review", "low")
        assert plan.external is False


# ===========================================================================
# Failure Handling Tests
# ===========================================================================

class TestFailureHandling:
    def test_convergence_with_single_source(self):
        responses = [
            {"agent": "A", "verdict_points": [
                {"point": "X", "position": "agree", "reason": "OK"},
            ]},
        ]
        converged, disagreements = check_convergence(responses, "consensus")
        # Single source cannot achieve consensus - need 2+ parties
        assert converged is False
        assert any("insufficient" in d for d in disagreements)

    def test_convergence_no_verdict_single_source(self):
        responses = [
            {"agent": "A", "verdict_points": []},
        ]
        converged, _ = check_convergence(responses, "consensus")
        assert converged is False

    def test_result_to_dict_serializable(self):
        """ConsultationResult.to_dict() must produce JSON-serializable output."""
        r = ConsultationResult(
            answer="test",
            sources=[{"agent": "A", "path": "A"}],
            disagreements=["X vs Y"],
            audit_trail=[{"step": 1}],
        )
        d = r.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)


# ===========================================================================
# End-to-End Path A Test (mock agent with verdict points + convergence)
# ===========================================================================

class TestEndToEndPathA:
    """Test the full consultation flow: agent calls consult(), protocol
    routes to mock agents, agents respond with verdict points,
    convergence check runs, structured result returns."""

    @pytest.fixture
    def agent(self, tmp_path):
        from base_agent import BaseAgent, AgentStateBase

        class TestAgent(BaseAgent):
            def _create_state(self):
                return AgentStateBase()
            def _build_system_prompt(self, **ctx):
                return "test"
            def _register_tools(self):
                pass
            def _on_start(self, **ctx):
                return "start"

        agent = TestAgent(backend="ollama", model="test")
        agent.state.agent_type = "test"
        agent.state.project_root = str(tmp_path)
        return agent

    @patch("base_agent.BackendAdapter.call")
    def test_two_agents_agree_consensus_reached(self, mock_call, agent):
        """Two agents respond with agreeing verdict points -> consensus."""
        mock_call.return_value = (
            "The design looks solid.\n\n"
            "## Verdict\n"
            "- POINT: Schema design | POSITION: agree "
            "| REASON: Well structured\n"
            "- POINT: Error handling | POSITION: agree "
            "| REASON: Comprehensive\n"
        )
        result = agent.consult(
            situation="design_review",
            question="Review the auth module design",
            criticality="normal",
        )
        assert result.agreement_reached is True
        assert result.agreement_mode == "consensus"
        assert len(result.disagreements) == 0
        assert result.confidence in ("high", "medium")
        assert result.calls_used >= 2
        assert len(result.sources) >= 2

    @patch("base_agent.BackendAdapter.call")
    def test_two_agents_disagree_consensus_fails(self, mock_call, agent):
        """Two agents with conflicting verdicts -> consensus fails."""
        responses = [
            (
                "Looks good.\n\n"
                "## Verdict\n"
                "- POINT: Caching strategy | POSITION: agree "
                "| REASON: Redis is fine\n"
            ),
            (
                "I have concerns.\n\n"
                "## Verdict\n"
                "- POINT: Caching strategy | POSITION: disagree "
                "| REASON: Redis adds complexity\n"
            ),
        ]
        mock_call.side_effect = responses
        result = agent.consult(
            situation="design_review",
            question="Review caching design unique_e2e_test",
            criticality="normal",
        )
        assert result.agreement_reached is False
        assert len(result.disagreements) >= 1
        assert "Caching strategy" in result.disagreements[0]

    @patch("base_agent.BackendAdapter.call")
    def test_critical_adds_socratic_and_external(self, mock_call, agent):
        """Critical criticality adds SocraticAgent and external path."""
        mock_call.return_value = (
            "Analysis.\n\n"
            "## Verdict\n"
            "- POINT: Security | POSITION: agree | REASON: OK\n"
        )
        result = agent.consult(
            situation="critical_decision",
            question="Should we open-source unique_critical_test",
            criticality="critical",
        )
        # Critical should use unanimous convergence
        assert result.agreement_mode == "unanimous"
        # Should have multiple sources (agents + external)
        assert result.calls_used >= 3
        agent_names = [s["agent"] for s in result.sources]
        assert "external" in agent_names

    @patch("base_agent.BackendAdapter.call")
    def test_verdict_points_parsed_in_result(self, mock_call, agent):
        """Verdict points from response are parsed and used for convergence."""
        mock_call.return_value = (
            "My review.\n\n"
            "## Verdict\n"
            "- POINT: API contract | POSITION: agree "
            "| REASON: Follows REST conventions\n"
            "- POINT: Rate limiting | POSITION: uncertain "
            "| REASON: Need load test data\n"
        )
        result = agent.consult(
            situation="code_review",
            question="Review API endpoints unique_verdict_test",
            criticality="low",
        )
        # Low criticality: first_answer, single agent
        assert result.agreement_reached is True
        assert result.format_used == "QuickQuestion"
        assert result.calls_used == 1


# ===========================================================================
# target_agent + instructions extension tests
# ===========================================================================

class TestValidateTargetAgent:
    """Test the validate_target_agent function."""

    def test_valid_agents(self):
        from consultation_protocol import validate_target_agent
        for name in ["ArchitectAgent", "ReviewerAgent", "DebuggerAgent",
                     "SocraticAgent", "ExpertAgent", "ConciergeAgent",
                     "CoderAgent", "CodeWarden"]:
            valid, msg = validate_target_agent(name)
            assert valid is True, f"{name} should be valid"
            assert msg == ""

    def test_domain_suffix_valid(self):
        from consultation_protocol import validate_target_agent
        valid, msg = validate_target_agent("ExpertAgent:security")
        assert valid is True

    def test_invalid_agent(self):
        from consultation_protocol import validate_target_agent
        valid, msg = validate_target_agent("FakeAgent")
        assert valid is False
        assert "FakeAgent" in msg
        assert "Valid agents:" in msg

    def test_none_input(self):
        from consultation_protocol import validate_target_agent
        valid, msg = validate_target_agent(None)
        assert valid is False
        assert "non-empty string" in msg

    def test_empty_string(self):
        from consultation_protocol import validate_target_agent
        valid, msg = validate_target_agent("")
        assert valid is False

    def test_whitespace_stripped(self):
        from consultation_protocol import validate_target_agent
        valid, msg = validate_target_agent("  ArchitectAgent  ")
        assert valid is True


class TestConsultTargetAgent:
    """Test target_agent parameter in BaseAgent.consult()."""

    @pytest.fixture
    def agent(self, tmp_path):
        from base_agent import BaseAgent, AgentStateBase

        class TestAgent(BaseAgent):
            def _create_state(self):
                return AgentStateBase()
            def _build_system_prompt(self, **ctx):
                return "test"
            def _register_tools(self):
                pass
            def _on_start(self, **ctx):
                return "start"

        agent = TestAgent(backend="ollama", model="test")
        agent.state.agent_type = "test"
        agent.state.project_root = str(tmp_path)
        return agent

    @patch("base_agent.BackendAdapter.call")
    def test_target_agent_bypasses_routing(self, mock_call, agent):
        """target_agent should bypass the routing table."""
        mock_call.return_value = "Reviewed."
        result = agent.consult(
            situation="code_review",
            question="Review this unique_target_test",
            target_agent="ArchitectAgent",
        )
        assert result.calls_used >= 1
        # The source should be ArchitectAgent, not ReviewerAgent
        # (routing table maps code_review to ReviewerAgent)
        agent_names = [s["agent"] for s in result.sources]
        assert "ArchitectAgent" in agent_names

    @patch("base_agent.BackendAdapter.call")
    def test_invalid_target_agent_returns_error(self, mock_call, agent):
        """Invalid target_agent should return error result."""
        result = agent.consult(
            situation="code_review",
            question="Review this unique_invalid_target",
            target_agent="NonExistentAgent",
        )
        assert "invalid target_agent" in result.answer
        assert result.confidence == "low"
        # Should not have called the backend at all
        assert mock_call.call_count == 0

    @patch("base_agent.BackendAdapter.call")
    def test_no_target_agent_uses_routing(self, mock_call, agent):
        """Without target_agent, routing table is used (backward compat)."""
        mock_call.return_value = "OK"
        result = agent.consult(
            situation="code_review",
            question="Review this unique_no_target_test",
        )
        # code_review routes to ReviewerAgent
        agent_names = [s["agent"] for s in result.sources]
        assert "ReviewerAgent" in agent_names


class TestConsultInstructions:
    """Test instructions parameter in BaseAgent.consult()."""

    @pytest.fixture
    def agent(self, tmp_path):
        from base_agent import BaseAgent, AgentStateBase

        class TestAgent(BaseAgent):
            def _create_state(self):
                return AgentStateBase()
            def _build_system_prompt(self, **ctx):
                return "test"
            def _register_tools(self):
                pass
            def _on_start(self, **ctx):
                return "start"

        agent = TestAgent(backend="ollama", model="test")
        agent.state.agent_type = "test"
        agent.state.project_root = str(tmp_path)
        return agent

    @patch("base_agent.BackendAdapter.call")
    @patch("base_agent.BackendAdapter.add_user_message")
    def test_instructions_in_prompt(self, mock_add_msg, mock_call, agent):
        """instructions should appear in the generated prompt."""
        mock_call.return_value = "Noted."
        agent.consult(
            situation="code_review",
            question="Review this unique_instructions_test",
            instructions="Focus on error handling in lines 50-70",
        )
        # Capture all prompts sent via add_user_message
        prompts = [call.args[0] for call in mock_add_msg.call_args_list
                   if call.args]
        prompt_text = " ".join(prompts)
        assert "Additional Instructions" in prompt_text
        assert "Focus on error handling in lines 50-70" in prompt_text

    @patch("base_agent.BackendAdapter.call")
    @patch("base_agent.BackendAdapter.add_user_message")
    def test_no_instructions_no_section(self, mock_add_msg, mock_call, agent):
        """Without instructions, no Additional Instructions section."""
        mock_call.return_value = "OK."
        agent.consult(
            situation="code_review",
            question="Review this unique_no_instructions_test",
        )
        prompts = [call.args[0] for call in mock_add_msg.call_args_list
                   if call.args]
        prompt_text = " ".join(prompts)
        assert "Additional Instructions" not in prompt_text

    @patch("base_agent.BackendAdapter.call")
    @patch("base_agent.BackendAdapter.add_user_message")
    def test_target_agent_plus_instructions(self, mock_add_msg, mock_call,
                                            agent):
        """target_agent + instructions work together."""
        mock_call.return_value = "Done."
        result = agent.consult(
            situation="code_review",
            question="Review this unique_combined_test",
            target_agent="ArchitectAgent",
            instructions="Check for dependency coupling",
        )
        # Should use ArchitectAgent, not ReviewerAgent
        agent_names = [s["agent"] for s in result.sources]
        assert "ArchitectAgent" in agent_names
        # Instructions should be in prompt
        prompts = [call.args[0] for call in mock_add_msg.call_args_list
                   if call.args]
        prompt_text = " ".join(prompts)
        assert "Check for dependency coupling" in prompt_text
