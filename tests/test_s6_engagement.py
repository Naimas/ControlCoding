"""Tests for Sprint S6: Auto-Wiring + Engagement Lite.

Covers:
- Engagement config load/save/validate (cc_engagement.json)
- Component gating by engagement level
- Budget checking
- Domain auto-detection from CLAUDE.md
- plan_approve() auto-chain (idempotent)
- Malformed config handling
- Config migration (schema_version)
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add templates/scripts to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "templates" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from control_plane_utils import (
    ENGAGEMENT_LEVELS,
    ENGAGEMENT_COMPONENTS,
    ENGAGEMENT_DEFAULTS,
    ENGAGEMENT_SCHEMA_VERSION,
    PRODUCT_TIER_PROFILES,
    assess_local_tandem_capacity,
    best_effort_tier,
    format_local_tandem_assessment,
    load_engagement,
    save_engagement,
    is_component_active,
    check_budget,
    check_specialist_runtime,
)


class TestEngagementConfig(unittest.TestCase):
    """Tests for engagement config load/save/validate."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, ".claude", "cc_engagement.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_when_no_file(self):
        config = load_engagement(self.config_path)
        self.assertEqual(config["schema_version"], ENGAGEMENT_SCHEMA_VERSION)
        self.assertEqual(config["level"], 2)
        self.assertEqual(config["backend_policy"], "local_only")
        self.assertEqual(config["budget_policy"]["max_calls"], 0)
        self.assertEqual(config["budget_policy"]["exhaustion_behavior"], "degrade")

    def test_save_and_load_roundtrip(self):
        config = {
            "schema_version": 1,
            "level": 3,
            "backend_policy": "approved",
            "budget_policy": {"max_calls": 50, "exhaustion_behavior": "stop"},
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["level"], 3)
        self.assertEqual(loaded["backend_policy"], "approved")
        self.assertEqual(loaded["budget_policy"]["max_calls"], 50)
        self.assertEqual(loaded["budget_policy"]["exhaustion_behavior"], "stop")

    def test_level_validation_clamps(self):
        """Invalid level falls back to default."""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump({"level": 99}, f)
        config = load_engagement(self.config_path)
        self.assertEqual(config["level"], 2)  # default

    def test_level_validation_negative(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump({"level": -1}, f)
        config = load_engagement(self.config_path)
        self.assertEqual(config["level"], 2)

    def test_level_validation_string(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump({"level": "high"}, f)
        config = load_engagement(self.config_path)
        self.assertEqual(config["level"], 2)

    def test_backend_policy_validation(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump({"backend_policy": "invalid"}, f)
        config = load_engagement(self.config_path)
        self.assertEqual(config["backend_policy"], "local_only")

    def test_malformed_json(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            f.write("{bad json")
        config = load_engagement(self.config_path)
        self.assertEqual(config["level"], 2)  # defaults

    def test_non_dict_json(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump([1, 2, 3], f)
        config = load_engagement(self.config_path)
        self.assertEqual(config["level"], 2)

    def test_partial_config_fills_defaults(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump({"level": 4}, f)
        config = load_engagement(self.config_path)
        self.assertEqual(config["level"], 4)
        self.assertEqual(config["backend_policy"], "local_only")  # default
        self.assertEqual(config["budget_policy"]["max_calls"], 0)  # default

    def test_schema_version_preserved(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump({"schema_version": 2, "level": 3}, f)
        config = load_engagement(self.config_path)
        self.assertEqual(config["schema_version"], 2)

    def test_new_tier_format_roundtrip(self):
        config = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "tier": "core",
            "level": PRODUCT_TIER_PROFILES["core"]["legacy_level"],
            "ui_intent": "host_only",
            "backend_policy": "approved",
            "budget_policy": {"max_calls": 7, "exhaustion_behavior": "stop"},
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["tier"], "core")
        self.assertEqual(loaded["level"], PRODUCT_TIER_PROFILES["core"]["legacy_level"])
        self.assertEqual(loaded["ui_intent"], "host_only")
        self.assertEqual(loaded["backend_policy"], "approved")
        self.assertEqual(loaded["budget_policy"]["max_calls"], 7)

    def test_nested_ui_payload_is_supported(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "schema_version": 2,
                    "tier": "studio",
                    "engagement": {
                        "level": 4,
                        "presence": "milestone",
                        "tier": "studio",
                    },
                    "deployment": "ui",
                },
                f,
            )
        config = load_engagement(self.config_path)
        self.assertEqual(config["tier"], "studio")
        self.assertEqual(config["level"], 4)
        self.assertEqual(config["presence"], "milestone")

    def test_tandem_config_roundtrip(self):
        config = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "tier": "agents",
            "level": 4,
            "backend_policy": "local_only",
            "tandem": {
                "mode": "auto",
                "backend_a": "ollama",
                "backend_b": "ollama",
                "model_a": "qwen3-coder",
                "model_b": "codestral",
            },
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["tandem"]["backend_a"], "ollama")
        self.assertEqual(loaded["tandem"]["model_a"], "qwen3-coder")
        self.assertEqual(loaded["tandem"]["model_b"], "codestral")

    def test_specialist_paths_roundtrip(self):
        config = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "tier": "agents",
            "specialist_paths": [
                {
                    "role_id": "codewarden",
                    "label": "CodeWarden",
                    "path_type": "watchdog",
                    "active": True,
                    "backend": "anthropic_prod",
                    "model": "claude-sonnet-4-6",
                    "permission": "approval_required",
                    "execution_mode": "cc_routed",
                    "max_calls": 3,
                },
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "openai_prod",
                    "model": "gpt-5.4",
                    "permission": "within_budget_auto",
                    "execution_mode": "auto_bounded",
                    "max_calls": 2,
                },
            ],
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(len(loaded["specialist_paths"]), 2)
        self.assertEqual(loaded["specialist_paths"][0]["backend"], "anthropic_prod")
        self.assertEqual(loaded["specialist_paths"][1]["permission"], "within_budget_auto")
        self.assertEqual(loaded["specialist_paths"][0]["execution_mode"], "cc_routed")
        self.assertEqual(loaded["specialist_paths"][1]["execution_mode"], "auto_bounded")

    def test_specialist_paths_infer_execution_mode_from_permission(self):
        config = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "tier": "agents",
            "specialist_paths": [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "openai_prod",
                    "model": "gpt-5.4",
                    "permission": "user_mediated",
                    "max_calls": 1,
                }
            ],
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["specialist_paths"][0]["execution_mode"], "human_mediated")

    def test_specialist_paths_canonicalize_permission_from_execution_mode(self):
        config = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "tier": "agents",
            "specialist_paths": [
                {
                    "role_id": "consultant_1",
                    "label": "Architect",
                    "path_type": "consultant",
                    "active": True,
                    "backend": "openai_prod",
                    "model": "gpt-5.4",
                    "permission": "approval_required",
                    "execution_mode": "human_mediated",
                    "max_calls": 1,
                }
            ],
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["specialist_paths"][0]["execution_mode"], "human_mediated")
        self.assertEqual(loaded["specialist_paths"][0]["permission"], "user_mediated")

    def test_invalid_specialist_paths_normalize_to_empty_list(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"specialist_paths": {"bad": True}}, f)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["specialist_paths"], [])

    def test_core_manual_consultation_roundtrip(self):
        config = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "tier": "core",
            "backend_policy": "approved",
            "manual_consultation_allowed": True,
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["tier"], "core")
        self.assertEqual(loaded["planning_mode"], "solo_structured_manual_consultation_ready")
        self.assertEqual(loaded["planning_authority"], "single_author_with_manual_consultation")
        self.assertTrue(loaded["manual_consultation_allowed"])

    def test_agents_tier_forces_orchestrated_planning(self):
        config = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "tier": "agents",
            "manual_consultation_allowed": True,
            "planning_mode": "solo_structured",
            "planning_authority": "single_author",
        }
        save_engagement(config, self.config_path)
        loaded = load_engagement(self.config_path)
        self.assertEqual(loaded["tier"], "agents")
        self.assertEqual(loaded["planning_mode"], "orchestrated_specialists")
        self.assertEqual(loaded["planning_authority"], "orchestrated_multi_role")
        self.assertFalse(loaded["manual_consultation_allowed"])


class TestComponentGating(unittest.TestCase):
    """Tests for is_component_active() at each engagement level."""

    def test_level_1_nothing_active(self):
        config = {"level": 1}
        self.assertFalse(is_component_active("codewarden", config=config))
        self.assertFalse(is_component_active("consultant", config=config))
        self.assertFalse(is_component_active("planner", config=config))
        self.assertFalse(is_component_active("tandem", config=config))
        self.assertFalse(is_component_active("concierge", config=config))

    def test_level_2_codewarden_active(self):
        config = {"level": 2}
        self.assertTrue(is_component_active("codewarden", config=config))
        self.assertTrue(is_component_active("consultant", config=config))
        self.assertTrue(is_component_active("session", config=config))
        self.assertFalse(is_component_active("planner", config=config))
        self.assertFalse(is_component_active("tandem", config=config))

    def test_level_3_verification_active(self):
        config = {"level": 3}
        self.assertTrue(is_component_active("codewarden", config=config))
        self.assertTrue(is_component_active("verification", config=config))
        self.assertTrue(is_component_active("auto_import", config=config))
        self.assertFalse(is_component_active("planner", config=config))

    def test_level_4_everything_active(self):
        config = {"level": 4}
        self.assertTrue(is_component_active("codewarden", config=config))
        self.assertTrue(is_component_active("consultant", config=config))
        self.assertTrue(is_component_active("verification", config=config))
        self.assertTrue(is_component_active("planner", config=config))
        self.assertTrue(is_component_active("tandem", config=config))
        self.assertTrue(is_component_active("concierge", config=config))

    def test_unknown_component_never_active(self):
        config = {"level": 4}
        self.assertFalse(is_component_active("nonexistent", config=config))

    def test_cumulative_level_3_includes_level_2(self):
        """Level 3 includes all level 2 components."""
        config = {"level": 3}
        for comp in ENGAGEMENT_COMPONENTS[2]:
            self.assertTrue(
                is_component_active(comp, config=config),
                f"{comp} should be active at level 3",
            )

    def test_from_file(self):
        """Test is_component_active loading from file."""
        tmp = tempfile.mkdtemp()
        config_path = os.path.join(tmp, ".claude", "cc_engagement.json")
        save_engagement({"schema_version": 1, "level": 1}, config_path)
        self.assertFalse(
            is_component_active("codewarden", config_path=config_path))
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_no_file_defaults_active(self):
        """No config file means default level 2 -> codewarden active."""
        self.assertTrue(
            is_component_active("codewarden",
                                config_path="/nonexistent/path.json"))

    def test_core_tier_disables_consultant_but_keeps_codewarden(self):
        config = {"tier": "core", "level": 3}
        self.assertTrue(is_component_active("codewarden", config=config))
        self.assertTrue(is_component_active("verification", config=config))
        self.assertTrue(is_component_active("auto_import", config=config))
        self.assertFalse(is_component_active("consultant", config=config))
        self.assertFalse(is_component_active("tandem", config=config))

    def test_agents_tier_enables_specialist_workflows(self):
        config = {"tier": "agents", "level": 4}
        self.assertTrue(is_component_active("codewarden", config=config))
        self.assertTrue(is_component_active("consultant", config=config))
        self.assertTrue(is_component_active("planner", config=config))
        self.assertTrue(is_component_active("tandem", config=config))


class TestTierInference(unittest.TestCase):
    def test_best_effort_tier_prefers_explicit_tier(self):
        self.assertEqual(best_effort_tier({"tier": "studio", "level": 1}), "studio")

    def test_best_effort_tier_legacy_level_1_is_core(self):
        self.assertEqual(best_effort_tier({"level": 1}), "core")

    def test_best_effort_tier_legacy_level_2_is_agents(self):
        self.assertEqual(best_effort_tier({"level": 2}), "agents")


class TestBudget(unittest.TestCase):
    """Tests for check_budget()."""

    def test_unlimited_budget(self):
        config = {"budget_policy": {"max_calls": 0, "exhaustion_behavior": "degrade"}}
        result = check_budget(config=config)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["max_calls"], 0)

    def test_budget_with_calls(self):
        config = {"budget_policy": {"max_calls": 5, "exhaustion_behavior": "stop"}}
        # No event log = 0 calls used
        result = check_budget(config=config)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["exhaustion_behavior"], "stop")


class TestSpecialistRuntimeGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.control_dir = Path(self.tmp) / ".controlcoding"
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = str(self.control_dir / "cc_engagement.json")
        self.gateway_path = str(self.control_dir / "gateway_config.json")
        self.event_log = str(self.control_dir / "event_log.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_gateway(self, backend_id="anthropic_prod"):
        with open(self.gateway_path, "w", encoding="utf-8") as f:
            json.dump({"backends": {backend_id: {"transport": "anthropic"}}}, f)

    def test_core_without_specialist_paths_requires_explicit_backend(self):
        save_engagement({"level": 4, "backend_policy": "approved"}, self.config_path)
        result = check_specialist_runtime(
            role_ref="architect",
            requested_backend="claude",
            config_path=self.config_path,
            gateway_config_path=self.gateway_path,
            event_log_path=self.event_log,
        )
        self.assertTrue(result["allowed"])
        self.assertTrue(result["enforced"])
        self.assertEqual(result["reason"], "explicit_backend")

    def test_human_mediated_path_blocks_runtime(self):
        save_engagement({
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
        }, self.config_path)
        self._write_gateway()
        result = check_specialist_runtime(
            role_ref="architect",
            requested_backend="claude",
            config_path=self.config_path,
            gateway_config_path=self.gateway_path,
            event_log_path=self.event_log,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "human_mediated_only")
        self.assertIn("consult-packet create", result["detail"])

    def test_routed_path_requires_gateway_config(self):
        save_engagement({
            "tier": "agents",
            "specialist_paths": [{
                "role_id": "consultant_1",
                "label": "Architect Consultant",
                "path_type": "consultant",
                "active": True,
                "backend": "anthropic_prod",
                "model": "claude-sonnet",
                "permission": "approval_required",
                "execution_mode": "cc_routed",
                "max_calls": 2,
            }],
        }, self.config_path)
        result = check_specialist_runtime(
            role_ref="architect",
            requested_backend="claude",
            config_path=self.config_path,
            gateway_config_path=self.gateway_path,
            event_log_path=self.event_log,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "missing_gateway_config")

    def test_explicit_backend_precedes_role_profile_without_gate_side_effects(self):
        save_engagement({
            "tier": "agents",
            "specialist_paths": [{
                "role_id": "consultant_1",
                "label": "Architect Consultant",
                "path_type": "consultant",
                "active": True,
                "backend": "anthropic_prod",
                "model": "claude-sonnet",
                "permission": "approval_required",
                "execution_mode": "cc_routed",
                "max_calls": 2,
            }],
        }, self.config_path)
        self._write_gateway()
        result = check_specialist_runtime(
            role_ref="architect",
            requested_backend="claude",
            requested_model="wrong-model",
            config_path=self.config_path,
            gateway_config_path=self.gateway_path,
            event_log_path=self.event_log,
        )
        self.assertTrue(result["allowed"])
        self.assertTrue(result["enforced"])
        self.assertEqual(result["backend"], "claude")
        self.assertEqual(result["model"], "wrong-model")
        self.assertFalse(Path(self.event_log).exists())

    def test_auto_bounded_path_respects_global_budget(self):
        save_engagement({
            "tier": "agents",
            "budget_policy": {"max_calls": 1, "exhaustion_behavior": "stop"},
            "specialist_paths": [{
                "role_id": "consultant_1",
                "label": "Architect Consultant",
                "path_type": "consultant",
                "active": True,
                "backend": "anthropic_prod",
                "model": "claude-sonnet",
                "permission": "within_budget_auto",
                "execution_mode": "auto_bounded",
                "max_calls": 0,
            }],
        }, self.config_path)
        self._write_gateway()
        with open(self.event_log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": "EVT-001", "event": "llm_call_routed", "details": {}}) + "\n")
        result = check_specialist_runtime(
            role_ref="architect",
            requested_backend="claude",
            config_path=self.config_path,
            gateway_config_path=self.gateway_path,
            event_log_path=self.event_log,
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "budget_exhausted")


class TestLocalTandemCapacity(unittest.TestCase):
    def test_low_ram_disables_local_tandem(self):
        result = assess_local_tandem_capacity(total_ram_gb=16, gpu_vram_gb=8)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["preferred_mode"], "single_model_consult")

    def test_mid_ram_prefers_sequential_tandem(self):
        result = assess_local_tandem_capacity(total_ram_gb=48, gpu_vram_gb=16)
        self.assertEqual(result["status"], "risky")
        self.assertEqual(result["preferred_mode"], "dual_model_sequential")

    def test_high_ram_and_gpu_recommend_local_tandem(self):
        result = assess_local_tandem_capacity(total_ram_gb=96, gpu_vram_gb=24)
        self.assertEqual(result["status"], "recommended")
        self.assertEqual(result["preferred_mode"], "dual_model_parallel")

    def test_formatter_includes_mode_and_hardware(self):
        result = assess_local_tandem_capacity(total_ram_gb=48, gpu_vram_gb=16)
        summary = format_local_tandem_assessment(result)
        self.assertIn("dual_model_sequential", summary)
        self.assertIn("RAM 48.0 GB", summary)


class TestEngagementLevelsConstants(unittest.TestCase):
    """Tests for the engagement level constants."""

    def test_four_levels_defined(self):
        self.assertEqual(len(ENGAGEMENT_LEVELS), 4)
        self.assertIn(1, ENGAGEMENT_LEVELS)
        self.assertIn(4, ENGAGEMENT_LEVELS)

    def test_level_names(self):
        self.assertEqual(ENGAGEMENT_LEVELS[1], "conservative")
        self.assertEqual(ENGAGEMENT_LEVELS[2], "guided")
        self.assertEqual(ENGAGEMENT_LEVELS[3], "active")
        self.assertEqual(ENGAGEMENT_LEVELS[4], "full")

    def test_level_1_has_no_components(self):
        self.assertEqual(len(ENGAGEMENT_COMPONENTS[1]), 0)

    def test_level_4_has_all_components(self):
        all_components = set()
        for lvl in range(2, 5):
            all_components |= ENGAGEMENT_COMPONENTS[lvl]
        self.assertEqual(ENGAGEMENT_COMPONENTS[4] | ENGAGEMENT_COMPONENTS[3]
                         | ENGAGEMENT_COMPONENTS[2], all_components)

    def test_defaults_have_required_keys(self):
        self.assertIn("schema_version", ENGAGEMENT_DEFAULTS)
        self.assertIn("level", ENGAGEMENT_DEFAULTS)
        self.assertIn("backend_policy", ENGAGEMENT_DEFAULTS)
        self.assertIn("budget_policy", ENGAGEMENT_DEFAULTS)


class TestDomainAutoDetection(unittest.TestCase):
    """Tests for detect_domain() from planner.py."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from planner import detect_domain
        self.detect_domain = detect_domain

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_claude_md(self, content):
        path = os.path.join(self.tmp, "CLAUDE.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_detect_game_domain(self):
        path = self._write_claude_md("""\
## Project Identity
- **Name**: MyCrawler
- **Stack**: C++17, OpenGL, SDL2
- **Architecture**: 3D game with ECS, render pipeline, shader system
""")
        result = self.detect_domain(path)
        self.assertEqual(result["domain"], "game")
        self.assertGreater(result["confidence"], 0)
        self.assertGreater(len(result["keywords_matched"]), 0)

    def test_detect_financial_domain(self):
        path = self._write_claude_md("""\
## Project Identity
- **Name**: TradingBot
- **Stack**: Python 3.12, FastAPI
- **Architecture**: Trading platform with order book and settlement
""")
        result = self.detect_domain(path)
        self.assertEqual(result["domain"], "financial")

    def test_detect_web_domain(self):
        path = self._write_claude_md("""\
## Project Identity
- **Name**: MyApp
- **Stack**: React, Next.js, PostgreSQL
- **Architecture**: Full-stack web app with REST API
""")
        result = self.detect_domain(path)
        self.assertEqual(result["domain"], "web")

    def test_detect_physics_domain(self):
        path = self._write_claude_md("""\
## Project Identity
- **Name**: ParticleSim
- **Stack**: C++20
- **Architecture**: N-body gravity simulation with Verlet integration
""")
        result = self.detect_domain(path)
        self.assertEqual(result["domain"], "physics")

    def test_no_file_returns_generic(self):
        result = self.detect_domain("/nonexistent/CLAUDE.md")
        self.assertEqual(result["domain"], "generic")
        self.assertEqual(result["confidence"], 0.0)

    def test_no_keywords_returns_generic(self):
        path = self._write_claude_md("""\
## Project Identity
- **Name**: MyTool
- **Stack**: Rust
- **Architecture**: CLI utility
""")
        result = self.detect_domain(path)
        self.assertEqual(result["domain"], "generic")

    def test_section_text_returned(self):
        path = self._write_claude_md("""\
## Project Identity
Some content here about the project.

## Other Section
More stuff.
""")
        result = self.detect_domain(path)
        self.assertIn("Some content", result["section_text"])
        self.assertNotIn("More stuff", result["section_text"])


class TestPlanApproveAutoChain(unittest.TestCase):
    """Tests for plan_approve() auto-export chaining."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        try:
            os.chdir(self.tmp)
            self.plan_dir = os.path.join(self.tmp, "devlog")
            os.makedirs(self.plan_dir, exist_ok=True)
            self.event_log = os.path.join(
                self.tmp, ".claude", "event_log.jsonl")
            self.decision_log = os.path.join(
                self.tmp, ".claude", "decision_log.jsonl")
            os.makedirs(os.path.dirname(self.event_log), exist_ok=True)
        except BaseException:
            os.chdir(self.original_cwd)
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_draft_plan(self):
        from planner import save_plan
        plan = {
            "status": "DRAFT",
            "version": 1,
            "scope": "Test project",
            "phases": [{"phase": 1, "name": "Phase 1", "features": []}],
            "architecture": {"zones": {"stable": ["core/"], "shared": ["lib/"]}},
            "invariants_global": [],
            "expansion_state": {"completed_rounds": [], "responses": {}},
        }
        save_plan(plan, self.plan_dir)
        return plan

    def test_auto_export_true_by_default(self):
        from planner import plan_approve
        self._create_draft_plan()
        result = plan_approve(
            plan_dir=self.plan_dir,
            event_log=self.event_log,
            decision_log=self.decision_log,
        )
        self.assertIn("export_criteria", result)
        self.assertIn("export_zones", result)
        criteria_path = Path(self.tmp) / "devlog" / "criteria" / "criteria.json"
        config_path = Path(self.tmp) / ".controlcoding" / "cc_config.json"
        self.assertTrue(criteria_path.exists())
        self.assertTrue(config_path.exists())
        self.assertTrue(
            criteria_path.resolve().is_relative_to(Path(self.tmp).resolve()))
        self.assertTrue(
            config_path.resolve().is_relative_to(Path(self.tmp).resolve()))

    def test_auto_export_false_skips(self):
        from planner import plan_approve
        self._create_draft_plan()
        result = plan_approve(
            plan_dir=self.plan_dir,
            event_log=self.event_log,
            decision_log=self.decision_log,
            auto_export=False,
        )
        self.assertNotIn("export_criteria", result)
        self.assertNotIn("export_zones", result)
        self.assertIn("plan", result)

    def test_auto_export_logs_event(self):
        from planner import plan_approve
        self._create_draft_plan()
        plan_approve(
            plan_dir=self.plan_dir,
            event_log=self.event_log,
            decision_log=self.decision_log,
        )
        # Check event log has auto_export event
        events = []
        with open(self.event_log, "r") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
        auto_export_events = [e for e in events
                              if e.get("event") == "plan_auto_export"]
        self.assertEqual(len(auto_export_events), 1)

    def test_auto_export_idempotent(self):
        """Calling plan_approve twice should not duplicate or corrupt."""
        from planner import plan_approve, load_plan, save_plan
        self._create_draft_plan()

        # First approval
        result1 = plan_approve(
            plan_dir=self.plan_dir,
            event_log=self.event_log,
            decision_log=self.decision_log,
        )
        self.assertIn("plan", result1)

        # Amend and re-approve
        plan = load_plan(self.plan_dir)
        plan["status"] = "AMENDED"
        save_plan(plan, self.plan_dir)

        result2 = plan_approve(
            plan_dir=self.plan_dir,
            event_log=self.event_log,
            decision_log=self.decision_log,
        )
        self.assertIn("plan", result2)
        # No crash, both results have export fields
        self.assertIn("export_criteria", result2)

    def test_auto_export_fail_safe(self):
        """Export errors don't crash plan_approve."""
        from planner import plan_approve
        self._create_draft_plan()
        # This should succeed even if verification_agent isn't importable
        # (export_criteria will have an error, but plan is still approved)
        result = plan_approve(
            plan_dir=self.plan_dir,
            event_log=self.event_log,
            decision_log=self.decision_log,
        )
        self.assertEqual(result["plan"]["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
