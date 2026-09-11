"""Milestone A: Core Ecosystem Vertical Slice.

End-to-end test of the complete ControlCoding agent cycle:

  idea -> /plan -> approve -> criteria
  -> coding diff -> CodeWarden structured -> auto-import
  -> verify_code() -> import-verify
  -> tandem -> decision_log
  -> event_log reflects everything

All LLM backends mocked. All file I/O real (via tmp_path).
This is a GATE test: if it fails, the ecosystem is not ready.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT_DIR = Path(__file__).parent.parent / "templates" / "scripts"
HOOKS_DIR = Path(__file__).parent.parent / "templates" / "hooks"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(HOOKS_DIR))


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    lines = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


class TestMilestoneA:
    """End-to-end vertical slice. Each test method is one step.
    They run in order via a shared tmp_path fixture at class scope.
    """

    @pytest.fixture(autouse=True, scope="class")
    def setup_project(self, tmp_path_factory):
        """Create shared project root for all steps."""
        root = tmp_path_factory.mktemp("milestone_a")
        cls = type(self)
        cls.root = root
        cls.plan_dir = str(root / "devlog" / "plans")
        cls.criteria_path = str(root / "devlog" / "criteria" / "criteria.json")
        cls.event_log = str(root / ".controlcoding" / "event_log.jsonl")
        cls.decision_log = str(root / ".controlcoding" / "decision_log.jsonl")
        (root / ".controlcoding").mkdir(parents=True)
        (root / "devlog" / "plans").mkdir(parents=True)
        (root / "devlog" / "criteria").mkdir(parents=True)
        # Create the canonical project context file
        (root / "CONTROLCODING.md").write_text(
            "# Trading Engine\n"
            "## Architecture Rules\n"
            "- All monetary values use Decimal, never float\n"
            "- Order book is the single source of truth\n"
            "- No direct database access from controllers\n"
            "## Domain Invariants\n"
            "- sum(debits) == sum(credits) at all times\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Step 1: Raw idea -> plan_expand() through all rounds -> DRAFT
    # ------------------------------------------------------------------

    def test_step1_plan_expand(self):
        from planner import plan_expand, ROUND_NAMES

        # Start expansion
        result = plan_expand(
            idea="Build a trading engine with order book and matching",
            domain="financial",
            plan_dir=self.plan_dir,
            event_log=self.event_log,
        )
        assert result["plan"]["status"] == "EXPANDING"
        assert result["next_round"] == "scope"

        # Complete all 6 rounds
        responses = {
            "scope": "Order book trading engine with limit and market orders",
            "features": "Order entry\nOrder matching\nTrade settlement",
            "architecture": "core <- orders <- matching <- settlement",
            "invariants": "sum(debits) == sum(credits)\nno negative positions",
            "acceptance": "Orders validated\nMatching correct\nSettlement atomic",
            "phasing": "Phase 1: Order types\nPhase 2: Matching engine\nPhase 3: Settlement",
        }
        for rn in ROUND_NAMES:
            result = plan_expand(
                domain="financial",
                responses={rn: responses[rn]},
                plan_dir=self.plan_dir,
                event_log=self.event_log,
            )

        assert result["complete"] is True
        assert result["plan"]["status"] == "DRAFT"
        plan = result["plan"]
        assert len(plan["phases"]) >= 2
        assert len(plan["invariants_global"]) >= 1

    # ------------------------------------------------------------------
    # Step 2: Approve plan -> immutable snapshot with SHA256
    # ------------------------------------------------------------------

    def test_step2_plan_approve(self):
        from planner import plan_approve, load_plan

        original_cwd = os.getcwd()
        try:
            os.chdir(self.root)
            result = plan_approve(
                rationale="Reviewed and approved for implementation",
                plan_dir=self.plan_dir,
                event_log=self.event_log,
                decision_log=self.decision_log,
                auto_export=False,
            )
        finally:
            os.chdir(original_cwd)
        assert result["plan"]["status"] == "APPROVED"
        assert result["plan"]["status_hash"]
        assert len(result["plan"]["status_hash"]) == 64
        assert Path(result["snapshot"]).exists()

        # Verify hash integrity on reload
        plan = load_plan(self.plan_dir)
        assert plan["status"] == "APPROVED"

        # Decision logged
        decisions = _read_jsonl(self.decision_log)
        assert any(d["type"] == "plan_approval" for d in decisions)

    # ------------------------------------------------------------------
    # Step 3: import-plan -> criteria created from plan features
    # ------------------------------------------------------------------

    def test_step3_import_plan(self):
        import verification_agent as va

        plan_path = str(Path(self.plan_dir) / "plan.current.json")
        result = va.import_plan(
            plan_path,
            criteria_path=self.criteria_path,
            event_log=self.event_log,
        )
        assert result["imported"] >= 1

        criteria = va.load_criteria(self.criteria_path)
        assert len(criteria) >= 1
        # All criteria are pending
        assert all(c.status == "pending" for c in criteria)
        # IDs have PL- prefix
        assert all(c.id.startswith("PL-") for c in criteria)

        type(self).plan_criteria_count = len(criteria)

    # ------------------------------------------------------------------
    # Step 4: Simulate coding diff with a known violation
    # ------------------------------------------------------------------

    def test_step4_codewarden_structured(self):
        from violation_store import (
            parse_structured_review, record_structured_violations,
        )

        # Simulate what CodeWarden's LLM would return for a bad diff
        mock_llm_response = json.dumps({"violations": [
            {
                "rule": "Decimal precision",
                "file": "src/orders/limit.py",
                "line": 15,
                "severity": "critical",
                "category": "domain_invariant",
                "description": "Using float for price field. CLAUDE.md requires Decimal for all monetary values.",
                "suggested_fix": "Change price: float to price: Decimal",
                "claude_md_section": "Architecture Rules",
            },
            {
                "rule": "No direct DB access",
                "file": "src/orders/api.py",
                "line": 42,
                "severity": "high",
                "category": "architecture",
                "description": "Direct database query in API handler bypasses repository layer.",
                "suggested_fix": "Use OrderRepository.get() instead of raw SQL",
                "claude_md_section": "Architecture Rules",
            },
        ]})

        # Parse and record
        parsed = parse_structured_review(mock_llm_response)
        assert len(parsed) == 2

        records = record_structured_violations(
            self.root, parsed, hook="review", backend="ollama",
        )
        assert len(records) == 2
        assert records[0]["id"] == "CW-001"
        assert records[1]["id"] == "CW-002"
        assert records[0]["severity"] == "critical"

    # ------------------------------------------------------------------
    # Step 5: Auto-import violations -> criteria.json updated
    # ------------------------------------------------------------------

    def test_step5_auto_import_violations(self):
        import verification_agent as va

        store_path = str(self.root / ".controlcoding" / "codewarden_violations.jsonl")
        result = va.import_violations(
            store_path,
            criteria_path=self.criteria_path,
            event_log=self.event_log,
        )
        assert result["imported"] >= 1

        criteria = va.load_criteria(self.criteria_path)
        cw_criteria = [c for c in criteria if c.id.startswith("CW-")]
        assert len(cw_criteria) >= 1

        # Total criteria = plan criteria + CW criteria
        total = len(criteria)
        assert total > self.plan_criteria_count

        type(self).post_cw_count = total

    # ------------------------------------------------------------------
    # Step 6: verify_code() produces independent findings
    # ------------------------------------------------------------------

    def test_step6_verify_code(self):
        import mcp_consultant

        mock_response = json.dumps({
            "findings": [
                {
                    "id": "VF-001",
                    "dimension": "sanity",
                    "severity": "high",
                    "file": "src/orders/limit.py",
                    "line": 22,
                    "description": "Price comparison uses == on float without epsilon",
                    "suggested_fix": "Use abs(a-b) < 1e-10",
                    "evidence": "if self.price == other.price:",
                },
                {
                    "id": "VF-002",
                    "dimension": "wiring",
                    "severity": "medium",
                    "file": "src/orders/market.py",
                    "line": 5,
                    "description": "MarketOrder imports MatchingEngine directly, violating dependency direction",
                    "suggested_fix": "Remove import, pass matching result as parameter",
                    "evidence": "from src.matching.engine import MatchingEngine",
                },
            ],
            "summary": {
                "total_findings": 2,
                "by_dimension": {"sanity": 1, "wiring": 1},
                "by_severity": {"high": 1, "medium": 1},
                "overall_assessment": "Float comparison bug and dependency violation found",
            },
        })

        with patch("mcp_consultant._call_ollama", return_value=mock_response), \
             patch("mcp_consultant._call_openai", return_value=mock_response), \
             patch("mcp_consultant._call_anthropic", return_value=mock_response), \
             patch("mcp_consultant._call_claude", return_value=mock_response), \
             patch.object(
                 mcp_consultant, "LOG_DIR",
                 str(self.root / ".controlcoding"),
             ):

            mcp_consultant._call_counter._count = 0

            report = mcp_consultant.verify_code(
                diff="+class LimitOrder:\n+    def __init__(self, price: float):",
                claude_md=(self.root / "CONTROLCODING.md").read_text(encoding="utf-8"),
                domain="financial",
                backend="ollama",
            )

        assert len(report["findings"]) == 2
        assert report["findings"][0]["dimension"] == "sanity"
        assert report["summary"]["total_findings"] == 2

        consult_log = self.root / ".controlcoding" / "consult_log.jsonl"
        assert consult_log.exists()
        assert consult_log.resolve().is_relative_to(self.root.resolve())

        # Save report
        report_path = mcp_consultant.save_verifier_report(
            report, project_root=str(self.root))
        assert Path(report_path).exists()

    # ------------------------------------------------------------------
    # Step 7: import-verify -> verifier findings become criteria
    # ------------------------------------------------------------------

    def test_step7_import_verify(self):
        import verification_agent as va

        report_path = str(self.root / ".controlcoding" / "verifier_report.json")
        result = va.import_verify(
            report_path,
            criteria_path=self.criteria_path,
            event_log=self.event_log,
        )
        assert result["imported"] >= 1

        criteria = va.load_criteria(self.criteria_path)
        vf_criteria = [c for c in criteria if c.id.startswith("VF-")]
        assert len(vf_criteria) >= 1
        assert all(c.status == "pending" for c in vf_criteria)

        type(self).post_vf_count = len(criteria)
        assert self.post_vf_count > self.post_cw_count

    # ------------------------------------------------------------------
    # Step 8: Tandem comparison -> divergences in decision_log
    # ------------------------------------------------------------------

    def test_step8_tandem_divergence(self):
        from mcp_consultant import run_tandem

        classify_result = {
            "agreements": [
                {"topic": "Price-time priority for matching",
                 "position": "Both agree FIFO at same price",
                 "confidence": "high"},
            ],
            "divergences": [
                {"topic": "Partial fills in v1",
                 "position_a": "Allow partial fills with remainder in book",
                 "position_b": "Reject partial fills in v1, add in v2",
                 "analysis": "Trade-off: completeness vs simplicity",
                 "user_decision_needed": True},
            ],
        }

        def mock_backend(*args, **kwargs):
            prompt = args[1] if len(args) > 1 else kwargs.get("prompt", "")
            if "JSON classifier" in str(args[2] if len(args) > 2 else ""):
                return json.dumps(classify_result)
            return "Some AI response about trading"

        with patch("mcp_consultant._tandem_call_backend",
                   side_effect=mock_backend):
            from mcp_consultant import _call_counter
            _call_counter._count = 0

            report = run_tandem(
                problem="Should the order book support partial fills in v1?",
                backend_a="ollama",
                backend_b="ollama",
                role="architect",
                domain="financial",
                rounds=1,
                event_log=self.event_log,
                decision_log=self.decision_log,
                classify_backend="ollama",
            )

        assert report["consensus_reached"] is False
        assert len(report["divergences"]) >= 1
        assert len(report["agreements"]) >= 1

        # Divergence logged to decision_log (NOT criteria)
        decisions = _read_jsonl(self.decision_log)
        tandem_decisions = [d for d in decisions
                           if d.get("source") == "tandem"]
        assert len(tandem_decisions) >= 1
        assert tandem_decisions[0]["type"] == "architecture_choice"

    # ------------------------------------------------------------------
    # Step 9: Event log reflects all key steps
    # ------------------------------------------------------------------

    def test_step9_event_log_complete(self):
        events = _read_jsonl(self.event_log)
        event_types = {e.get("event") for e in events}

        # Plan events
        assert "plan_created" in event_types
        assert "plan_approved" in event_types

        # Import events
        assert "criterion_created" in event_types

        # Violation events
        assert "violations_imported" in event_types

        # Tandem events
        tandem_events = {e.get("event") for e in events
                        if e.get("agent") == "tandem"}
        assert "tandem_diverged" in tandem_events or "tandem_converged" in tandem_events

        # All events have valid IDs
        for e in events:
            assert e.get("id", "").startswith("EVT-")
            assert e.get("ts")
            assert e.get("agent")

    # ------------------------------------------------------------------
    # Step 10: Final state verification
    # ------------------------------------------------------------------

    def test_step10_final_state(self):
        import verification_agent as va
        from planner import plan_status

        # Plan is APPROVED
        status = plan_status(self.plan_dir)
        assert status["status"] == "APPROVED"
        assert status["hash_valid"] is True

        # Criteria reflect all sources
        criteria = va.load_criteria(self.criteria_path)
        sources = {c.id.split("-")[0] for c in criteria}
        assert "PL" in sources    # from plan
        assert "CW" in sources    # from CodeWarden
        assert "VF" in sources    # from Verifier

        # All criteria have valid status
        for c in criteria:
            assert c.status in {"pending", "pass", "fail", "blocked", "wont_fix"}

        # Decision log has plan approval + tandem decision
        decisions = _read_jsonl(self.decision_log)
        dec_types = {d.get("type") for d in decisions}
        assert "plan_approval" in dec_types
        assert "architecture_choice" in dec_types

        # Summary
        total_criteria = len(criteria)
        total_events = len(_read_jsonl(self.event_log))
        total_decisions = len(decisions)
        print(f"\n{'='*60}")
        print(f"MILESTONE A: VERTICAL SLICE COMPLETE")
        print(f"{'='*60}")
        print(f"Criteria: {total_criteria} "
              f"(PL: {sum(1 for c in criteria if c.id.startswith('PL-'))}, "
              f"CW: {sum(1 for c in criteria if c.id.startswith('CW-'))}, "
              f"VF: {sum(1 for c in criteria if c.id.startswith('VF-'))})")
        print(f"Events: {total_events}")
        print(f"Decisions: {total_decisions}")
        print(f"Plan: APPROVED (hash valid)")
        print(f"{'='*60}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
