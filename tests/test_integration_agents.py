"""Integration tests - agents working together (Sprint S13).

These tests verify cross-agent workflows, not individual agent tools.
All LLM calls are mocked. The focus is on: correct context passing,
verbatim content integrity, structured output contracts, and
report propagation between agents.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, BaseAgent, BackendAdapter, ToolExecutor,
    ReportBuilder, ToolCall, ToolResult,
)
from concierge_agent import ConciergeAgent, ConciergeAgentState
from architect_agent import (
    ArchitectAgent, ArchitectState, _empty_kb, _save_kb,
    build_architect_report,
)
from coder_agent import CoderAgent, CoderState, build_coder_report
from reviewer_agent import ReviewerAgent, ReviewerState, build_reviewer_report
from debugger_agent import DebuggerAgent, DebuggerState, build_debugger_report
from socratic_agent import SocraticAgent, SocraticState, build_socratic_report
from expert_agent import ExpertAgent, ExpertState, build_expert_report


# ===========================================================================
# Helpers
# ===========================================================================

def _mock_agent_run(agent, responses):
    """Helper: mock backend.call() with a sequence of responses."""
    idx = [0]
    def mock_call(tools=None):
        resp = responses[min(idx[0], len(responses) - 1)]
        idx[0] += 1
        agent.backend_adapter.add_assistant_message(resp)
        return resp
    return mock_call


# ===========================================================================
# 1. Concierge -> Architect -> guide_coder
# ===========================================================================

class TestConciergeArchitectFlow:
    def test_architect_produces_structured_guidance(self, tmp_path):
        """Architect's guide_coder produces 8-field structured output
        that the Coder can consume."""
        architect = ArchitectAgent(backend="ollama")
        architect._project_root = tmp_path
        architect.state.standing_rules = ["stdlib only", "no global state"]
        architect._register_tools()

        result = architect._tool_guide_coder(
            task="Implement user authentication",
            constraints="stdlib only, no JWT library",
            files="src/auth.py, src/session.py",
        )
        guidance = json.loads(result)

        # Verify 8-field contract
        assert "task" in guidance
        assert "approach" in guidance
        assert "files_to_touch" in guidance
        assert "patterns_to_follow" in guidance
        assert "patterns_to_avoid" in guidance
        assert "constraints" in guidance
        assert "acceptance_criteria" in guidance
        assert "estimated_complexity" in guidance

        # Standing rules propagated
        assert "stdlib only" in guidance["constraints"]

        # Now feed this to CoderAgent
        coder = CoderAgent(backend="ollama")
        with patch.object(coder.backend_adapter, "call",
                          return_value="I'll implement auth"):
            coder.start(
                project_root=str(tmp_path),
                guidance=guidance,
            )

        # Coder should have parsed all fields
        assert coder.state.task == "Implement user authentication"
        assert "src/auth.py" in coder.state.target_files
        assert "stdlib only" in coder.state.constraints

    def test_architect_adr_flows_to_concierge(self, tmp_path):
        """ADR produced by Architect is captured in state for Concierge."""
        architect = ArchitectAgent(backend="ollama")
        architect._project_root = tmp_path
        architect._register_tools()

        architect._tool_produce_adr(
            title="Use repository pattern",
            context="Need data access abstraction",
            decision="Repository pattern for all DB access",
            consequences="More boilerplate but testable",
        )

        report = build_architect_report(architect.state)
        assert len(report["adrs"]) == 1
        assert report["adrs"][0]["title"] == "Use repository pattern"
        # Concierge would read this from the report


# ===========================================================================
# 2. Concierge -> Coder -> writes code -> report
# ===========================================================================

class TestConciergeCoderFlow:
    def test_coder_writes_and_reports(self, tmp_path):
        """Coder writes files and produces a report with change tracking."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("# old code\n")

        coder = CoderAgent(backend="ollama")
        coder._project_root = tmp_path
        coder._register_tools()

        # Simulate: read, write, edit, done
        coder._tool_read_file("src/app.py")
        coder._tool_write_file("src/app.py", "# new code\ndef main(): pass\n")
        coder._tool_write_file("src/utils.py", "def helper(): pass\n")

        report = build_coder_report(coder.state)
        assert "src/app.py" in report["files_modified"]
        assert "src/utils.py" in report["files_created"]

    def test_coder_test_results_in_report(self, tmp_path):
        """Coder's test results propagate to the report."""
        coder = CoderAgent(backend="ollama")
        coder._project_root = tmp_path
        coder._register_tools()

        # Simulate test run
        coder._tool_run_tests(command="echo '15 passed, 2 failed'")

        report = build_coder_report(coder.state)
        assert report["tests_passed"] >= 15
        assert report["tests_failed"] >= 2


# ===========================================================================
# 3. Concierge -> Reviewer -> verdict
# ===========================================================================

class TestConciergeReviewerFlow:
    def test_reviewer_evaluates_coder_output(self, tmp_path):
        """Reviewer checks criteria against code the Coder wrote."""
        (tmp_path / "auth.py").write_text(
            "import hashlib\n"
            "def hash_password(pw):\n"
            "    return hashlib.sha256(pw.encode()).hexdigest()\n"
        )

        reviewer = ReviewerAgent(backend="ollama")
        reviewer._project_root = tmp_path
        reviewer._register_tools()
        reviewer.state.criteria = [
            "Passwords are hashed",
            "No plaintext storage",
        ]

        # Read the file
        content = reviewer._tool_read_file("auth.py")
        assert "hashlib" in content

        # Check criteria
        reviewer._tool_check_criterion(
            "Passwords are hashed", "pass",
            evidence="hashlib.sha256 used in hash_password()",
        )
        reviewer._tool_check_criterion(
            "No plaintext storage", "pass",
            evidence="Only hash returned, no raw password stored",
        )
        reviewer._tool_verdict("pass", "All criteria met")

        report = build_reviewer_report(reviewer.state)
        assert report["verdict"] == "pass"
        assert len(report["criteria_results"]) == 2
        assert all(r["status"] == "pass" for r in report["criteria_results"])

    def test_reviewer_finds_issues(self, tmp_path):
        """Reviewer logs issues and fails verdict."""
        (tmp_path / "auth.py").write_text("password = 'admin123'\n")

        reviewer = ReviewerAgent(backend="ollama")
        reviewer._project_root = tmp_path
        reviewer._register_tools()

        reviewer._tool_log_issue(
            "critical", "auth.py:1",
            "Hardcoded password in source code",
            "Use environment variable or secrets manager",
        )
        reviewer._tool_check_criterion(
            "No hardcoded secrets", "fail",
            evidence="auth.py:1 contains hardcoded password",
        )
        reviewer._tool_verdict("fail", "Critical security issue found")

        report = build_reviewer_report(reviewer.state)
        assert report["verdict"] == "fail"
        assert report["blocking_count"] == 1


# ===========================================================================
# 4. Concierge -> VisualAgent -> report back
# ===========================================================================

class TestConciergeVisualFlow:
    def test_visual_agent_criteria_results_in_report(self):
        """VisualAgent criteria results propagate to report."""
        from visual_agent import VisualAgent, VisualAgentState, build_visual_report

        agent = VisualAgent(backend="ollama")
        agent._register_tools()

        # Simulate criteria marking
        agent.state.criteria = [
            {"id": "C-01", "text": "Login form visible",
             "status": "pending", "evidence": [], "attempts": 0},
            {"id": "C-02", "text": "Dashboard loads",
             "status": "pending", "evidence": [], "attempts": 0},
        ]
        agent._tool_mark_criterion(
            criterion_id="C-01", status="pass",
            evidence="Screenshot shows login form",
        )
        agent._tool_mark_criterion(
            criterion_id="C-02", status="fail",
            evidence="Dashboard returned 500 error",
        )

        report = build_visual_report(agent.state)
        cs = report["criteria_summary"]
        assert cs["pass"] == 1
        assert cs["fail"] == 1


# ===========================================================================
# 5. Concierge -> Debugger -> diagnosis
# ===========================================================================

class TestConciergeDebuggerFlow:
    def test_debugger_hypothesis_to_diagnosis(self, tmp_path):
        """Debugger forms hypotheses, tests them, produces diagnosis."""
        (tmp_path / "app.log").write_text(
            "[ERROR] Connection refused: port 5432\n"
            "[ERROR] Database unreachable\n"
        )

        debugger = DebuggerAgent(backend="ollama")
        debugger._project_root = tmp_path
        debugger._register_tools()
        debugger.state.bug_description = "App crashes on startup"
        debugger.state.error_output = "Connection refused: port 5432"

        # Form and test hypothesis
        debugger._tool_form_hypothesis(
            "Database not running",
            "Error mentions port 5432, which is PostgreSQL default",
        )

        # Read logs for evidence
        log_content = debugger._tool_read_logs("app.log", tail_lines=10)
        assert "Connection refused" in log_content

        # Test hypothesis
        with patch("debugger_agent.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="pg_isready: no response\n", stderr="")
            debugger._tool_test_hypothesis(
                hypothesis_id=1,
                command="pg_isready",
                expected="no response",
            )

        assert debugger.state.hypotheses[0]["status"] == "validated"

        # Diagnose
        debugger._tool_diagnosis(
            root_cause="PostgreSQL service not running on port 5432",
            suggested_fix="Start PostgreSQL: sudo systemctl start postgresql",
            confidence="high",
        )

        report = build_debugger_report(debugger.state)
        assert report["confidence"] == "high"
        assert report["validated_count"] == 1
        assert "PostgreSQL" in report["root_cause"]


# ===========================================================================
# 6. Architect -> Socratic escalation
# ===========================================================================

class TestArchitectSocraticEscalation:
    def test_socratic_challenges_and_identifies_risks(self, tmp_path):
        """Socratic session produces risks and recommendation."""
        socratic = SocraticAgent(backend="ollama")
        socratic._project_root = tmp_path
        socratic._register_tools()
        socratic.state.topic = "Using microservices for a 3-person team"

        socratic._tool_ask_question(
            "What is the operational overhead of microservices "
            "for a team of 3?",
            target="Team size assumption",
        )
        socratic._tool_probe_deeper(
            previous_answer="We'll use Kubernetes",
            followup="Who manages the Kubernetes cluster when all 3 "
                     "developers are writing features?",
        )
        socratic._tool_identify_risk(
            risk="Operational overhead exceeds team capacity",
            severity="high",
            mitigation="Start monolith, extract services when team grows",
        )
        socratic._tool_summarize_session(
            recommendation="reconsider",
            reasoning="3-person team cannot sustain microservices ops",
        )

        report = build_socratic_report(socratic.state)
        assert report["recommendation"] == "reconsider"
        assert report["risks_count"] == 1
        assert report["questions_count"] == 1
        # Architect would read this and adjust the design


# ===========================================================================
# 7. Full cycle: PLANNING -> CODING -> REVIEWING -> DONE
# ===========================================================================

class TestFullCycle:
    def test_concierge_full_workflow(self, tmp_path):
        """Concierge manages a complete workflow cycle."""
        # Setup: create a minimal project
        (tmp_path / "CLAUDE.md").write_text(
            "# Project\n## Project Identity\nTest project\n"
            "## Architecture Rules\nstdlib only\n"
        )

        concierge = ConciergeAgent(backend="ollama", mode="autonomous")

        responses = [
            # Start
            "Project loaded. I'll begin planning.",
            # PLANNING
            '```json\n{"tool": "transition_phase", "params": {"to_phase": "PLANNING", "reason": "Starting"}}\n```',
            '```json\n{"tool": "log_decision", "params": {"decision": "Build auth module", "reasoning": "User requirement"}}\n```',
            # CODING
            '```json\n{"tool": "transition_phase", "params": {"to_phase": "CODING", "reason": "Plan ready"}}\n```',
            '```json\n{"tool": "instruct_coder", "params": {"task": "Build auth", "constraints": "stdlib only"}}\n```',
            # REVIEWING
            '```json\n{"tool": "transition_phase", "params": {"to_phase": "REVIEWING", "reason": "Code done"}}\n```',
            # DONE
            '```json\n{"tool": "transition_phase", "params": {"to_phase": "DONE", "reason": "Review passed"}}\n```',
            '```json\n{"tool": "done", "params": {"summary": "Auth module complete"}}\n```',
        ]

        with patch.object(concierge.backend_adapter, "call",
                          side_effect=_mock_agent_run(concierge, responses)):
            report = concierge.run(
                project_root=str(tmp_path),
                task="Build auth module",
            )

        assert report["phase"] == "done"
        # Verify phase transitions happened
        phases_visited = [h["to"] for h in concierge.state.phase_history]
        assert "PLANNING" in phases_visited
        assert "CODING" in phases_visited
        assert "REVIEWING" in phases_visited
        assert "DONE" in phases_visited
        # Decision was logged
        assert len(concierge.state.decisions) >= 1

    def test_concierge_decision_log_persisted(self, tmp_path):
        """Decisions persist to JSONL during full workflow."""
        (tmp_path / "CLAUDE.md").write_text("# Project\n")

        concierge = ConciergeAgent(backend="ollama", mode="autonomous")

        responses = [
            "Starting",
            '```json\n{"tool": "log_decision", "params": {"decision": "Use REST API", "reasoning": "Standard approach"}}\n```',
            '```json\n{"tool": "log_decision", "params": {"decision": "PostgreSQL for DB", "reasoning": "Team experience"}}\n```',
            '```json\n{"tool": "done", "params": {"summary": "Decisions made"}}\n```',
        ]

        with patch.object(concierge.backend_adapter, "call",
                          side_effect=_mock_agent_run(concierge, responses)):
            concierge.run(project_root=str(tmp_path))

        # Check JSONL on disk
        log_path = tmp_path / ".controlcoding" / "concierge_decisions.jsonl"
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["decision"] == "Use REST API"
        assert json.loads(lines[1])["decision"] == "PostgreSQL for DB"


# ===========================================================================
# 8. Verbatim verification
# ===========================================================================

class TestVerbatimIntegrity:
    def test_content_hash_roundtrip(self):
        """Content tagged by one agent can be verified by another."""
        # Concierge tags content
        original = "Architecture rule: all hooks use stdlib only."
        tagged = BaseAgent._tag_verbatim(original)

        # Simulate passing to another agent
        received_content = tagged["content"]
        received_hash = tagged["_verbatim_hash"]

        # Receiving agent verifies
        assert BaseAgent._hash_content(received_content) == received_hash
        assert BaseAgent._verify_verbatim(original, received_content)

    def test_tampered_content_detected(self):
        """Modified content fails verbatim verification."""
        original = "Constraint: no external dependencies."
        tagged = BaseAgent._tag_verbatim(original)

        # Simulate tampering (summarization)
        tampered = "No external deps."  # summarized!
        assert not BaseAgent._verify_verbatim(original, tampered)

    def test_guide_coder_output_stable(self, tmp_path):
        """guide_coder output is deterministic for same input."""
        architect = ArchitectAgent(backend="ollama")
        architect._project_root = tmp_path
        architect._register_tools()

        r1 = architect._tool_guide_coder(
            task="Implement auth", constraints="stdlib", files="auth.py")
        r2 = architect._tool_guide_coder(
            task="Implement auth", constraints="stdlib", files="auth.py")

        # Same input -> same structure (task, constraints fields)
        d1 = json.loads(r1)
        d2 = json.loads(r2)
        assert d1["task"] == d2["task"]
        assert d1["constraints"] == d2["constraints"]
        assert d1["files_to_touch"] == d2["files_to_touch"]


# ===========================================================================
# 9. Expert multi-domain
# ===========================================================================

class TestExpertMultiDomain:
    def test_security_and_engineering_different_prompts(self, tmp_path):
        """Different domains produce different system prompts."""
        sec = ExpertAgent(backend="ollama", domain="security")
        sec._project_root = tmp_path
        sec._register_tools()
        sec_prompt = sec._build_system_prompt()

        eng = ExpertAgent(backend="ollama", domain="engineering")
        eng._project_root = tmp_path
        eng._register_tools()
        eng_prompt = eng._build_system_prompt()

        assert "OWASP" in sec_prompt
        assert "OWASP" not in eng_prompt
        assert "maintainability" in eng_prompt.lower()

    def test_custom_domain_works(self, tmp_path):
        """Custom domain (volcanology) produces a valid prompt."""
        expert = ExpertAgent(backend="ollama", domain="volcanology")
        expert._project_root = tmp_path
        expert._register_tools()
        prompt = expert._build_system_prompt()
        assert "volcanology" in prompt.lower()

    def test_expert_findings_in_report(self, tmp_path):
        """Expert findings and recommendations appear in report."""
        expert = ExpertAgent(backend="ollama", domain="security")
        expert._project_root = tmp_path
        expert._register_tools()

        expert._tool_analyze(
            "SQL injection", "Unparameterized query in login()",
            "critical", "auth.py:42")
        expert._tool_recommend(
            "Use parameterized queries", "critical", "small")
        expert._tool_done("1 critical finding")

        report = build_expert_report(expert.state)
        assert report["domain"] == "security"
        assert report["critical_findings"] == 1
        assert report["recommendations_count"] == 1


# ===========================================================================
# 10. Cross-agent state isolation
# ===========================================================================

class TestStateIsolation:
    def test_agents_have_independent_state(self, tmp_path):
        """Multiple agents spawned simultaneously don't share state."""
        coder = CoderAgent(backend="ollama")
        coder._project_root = tmp_path
        coder._register_tools()
        coder.state.task = "Task A"
        coder._tool_write_file("a.py", "# A\n")

        reviewer = ReviewerAgent(backend="ollama")
        reviewer._project_root = tmp_path
        reviewer._register_tools()
        reviewer.state.review_scope = "Review B"

        # States are independent
        assert coder.state.task == "Task A"
        assert reviewer.state.review_scope == "Review B"
        assert coder.state.agent_type == "coder"
        assert reviewer.state.agent_type == "reviewer"
        assert coder.state.files_created == ["a.py"]
        assert reviewer.state.issues == []

    def test_backend_adapters_independent(self):
        """Each agent has its own backend adapter (no shared messages)."""
        a1 = CoderAgent(backend="ollama")
        a2 = ReviewerAgent(backend="ollama")

        a1.backend_adapter.add_user_message("msg for coder")
        a2.backend_adapter.add_user_message("msg for reviewer")

        assert a1.backend_adapter.get_message_count() == 1
        assert a2.backend_adapter.get_message_count() == 1
        assert "coder" in a1.backend_adapter.messages[0]["content"]
        assert "reviewer" in a2.backend_adapter.messages[0]["content"]
