#!/usr/bin/env python3
"""
cc - ControlCoding CLI

Setup and manage ControlCoding guardrails in any project.

Usage:
    python cc.py setup --chat-guide [--host-hint HOST] [--project-root PATH]
    python cc.py setup --answers-file answers.json [--apply-answers] [--project-root PATH]
    python cc.py setup --engagement --answers-file answers.json [--apply-answers] [--project-root PATH]
    python cc.py setup-project --chat-guide [--host-hint HOST] [--project-root PATH]
    python cc.py setup-project --answers-file answers.json [--apply-answers] [--project-root PATH]
    python cc.py consult <status> [options] [--project-root PATH]
    python cc.py consult-packet create [options] [--project-root PATH]
    python cc.py consult-packet show --packet-id ID [options] [--project-root PATH]
    python cc.py consult-result import [options] [--project-root PATH]
    python cc.py init [--project-root PATH]    (non-interactive)
    python cc.py install <pack> [--project-root PATH]
    python cc.py chat-start [--topic TOPIC] [--scope SCOPE] [--project-root PATH]
    python cc.py work-start [--topic TOPIC] [--scope SCOPE] [--project-root PATH]
    python cc.py work-close [--topic TOPIC] [--summary TEXT] [--project-root PATH]
    python cc.py release-doctor [--project-root PATH]
    python cc.py doctor [--project-root PATH] [--release]
    python cc.py truth <report|check> [--project-root PATH]
    python cc.py docs <audit|check|propose> [--project-root PATH]
    python cc.py verify <init|status|run> [--project-root PATH]
    python cc.py invariants <init|elicit|status|list|add|report|doctor|run|wire-ci> [--project-root PATH]
    python cc.py feature <init|status|start|show|verify|complete|abort> [--project-root PATH]
    python cc.py promote <plan|check|apply> PATH [--to ZONE] [--project-root PATH]
    python cc.py host <status|switch|compare|migrate-plan> [options]
    python cc.py context <check|diff|sync|adopt> [options]
    python cc.py write-path <status|enable|disable|prepare|check|apply> [options]
    python cc.py benchmark <run|compare|report> [options]
    python cc.py benchmark-matrix <generate> [options]
    python cc.py surface <status|claim|observe|request-takeover|resolve-takeover|heartbeat|release|run> [options]
    python cc.py memory <init|doctor|scan|status|bootstrap|op-index|startup|session|session-pack|graph|retrieve|rag-pack|evidence|eval|cross-pack|semantic|ocr|impact|context|dev-context-pack|chunks|layout|intake|lifecycle|vector|note|idea|decision|consult|agent-run|sync-report|cleanup-temp|views|work-init|work-status|work-parity|work-quickstart|work-attach|work-import|work-import-source|work-ocr|work-export|work-sync|work-category|work-scan|work-analyze|work-review|work-promote|work-capture|work-session|work-graph|work-query|work-retrieve|work-rag-pack|work-views|work-checkpoint|work-handoff|work-dashboard|work-context-pack|work-obsidian|work-wiki|work-mcp> [options]
    python cc.py review [--project-root PATH] [--stdout]
    python cc.py organize [--project-root PATH] [--dry-run] [--apply] [--json] [--check] [--dev-taxonomy]
    python cc.py export <agents-md|host-context> [--project-root PATH] [--host HOST] [--force] [--preview-only]

Packs: debug-tools, multi-agent, session-manager, dashboard, all
"""

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import cc_evidence
import cc_evidence_inputs

from cc_memory_lib.runtime import core_runtime_error

# Source-checkout execution must diagnose an unsupported interpreter/SQLite
# import before importing the rest of the application. Installed entry points
# also check in main(); package metadata enforces the interpreter minimum.
if __name__ == "__main__":
    _runtime_issue = core_runtime_error()
    if _runtime_issue is not None:
        print(f"Error: {_runtime_issue.message}", file=sys.stderr)
        sys.exit(1)

from cc_memory import (
    MEMORY_AUX_ROUTED_COMMANDS,
    add_memory_aux_parser,
    add_memory_scan_parser,
    SESSION_EDGE_TYPES,
    STARTUP_INTENTS,
    VALID_LIFECYCLES,
    VALID_SESSION_MODES,
    VALID_SESSION_STATUSES,
    cmd_memory_agent_run_record,
    cmd_memory_bootstrap,
    cmd_memory_chunks,
    cmd_memory_consult_record,
    cmd_memory_context,
    cmd_memory_cross_pack,
    cmd_memory_decision_add,
    cmd_memory_dev_context_pack,
    cmd_memory_doctor,
    cmd_memory_graph_accept,
    cmd_memory_graph_around,
    cmd_memory_graph_export,
    cmd_memory_graph_reject,
    cmd_memory_graph_status,
    cmd_memory_graph_suggestions,
    cmd_memory_idea_add,
    cmd_memory_impact,
    cmd_memory_init,
    cmd_memory_intake_add,
    cmd_memory_intake_promote,
    cmd_memory_layout,
    cmd_memory_lifecycle_conflict,
    cmd_memory_lifecycle_mark,
    cmd_memory_lifecycle_supersede,
    cmd_memory_note_add,
    cmd_memory_ocr_run,
    cmd_memory_ocr_status,
    cmd_memory_op_index,
    cmd_memory_rag_pack,
    cmd_memory_retrieve,
    cmd_memory_scan,
    cmd_memory_semantic_status,
    cmd_memory_session_close,
    cmd_memory_session_link,
    cmd_memory_session_list,
    cmd_memory_session_note,
    cmd_memory_session_pack,
    cmd_memory_session_show,
    cmd_memory_session_start,
    cmd_memory_session_views,
    cmd_memory_startup,
    cmd_memory_status,
    cmd_memory_sync_report,
    cmd_memory_temp_cleanup,
    cmd_memory_vector_rebuild,
    cmd_memory_vector_search,
    cmd_memory_views_generate,
    cmd_memory_work_attach,
    cmd_memory_work_analyze,
    cmd_memory_work_category_add,
    cmd_memory_work_category_approve,
    cmd_memory_work_category_list,
    cmd_memory_work_category_propose,
    cmd_memory_work_capture,
    cmd_memory_work_checkpoint,
    cmd_memory_work_dashboard,
    cmd_memory_work_export,
    cmd_memory_work_graph,
    cmd_memory_work_handoff,
    cmd_memory_work_context_pack,
    cmd_memory_work_import,
    cmd_memory_work_import_source,
    cmd_memory_work_init,
    cmd_memory_work_ocr,
    cmd_memory_work_promote,
    cmd_memory_work_query,
    cmd_memory_work_quickstart,
    cmd_memory_work_rag_pack,
    cmd_memory_work_retrieve,
    cmd_memory_work_review,
    cmd_memory_work_scan,
    cmd_memory_work_mcp_call,
    cmd_memory_work_mcp_tools,
    cmd_memory_work_obsidian,
    cmd_memory_work_parity,
    cmd_memory_work_session,
    cmd_memory_work_sync,
    cmd_memory_work_status,
    cmd_memory_work_views,
    cmd_memory_work_wiki,
    dispatch_memory_aux_command,
)
from cc_feature import (
    FEATURE_CAPABILITY_REGISTRY_ITEM,
    FEATURE_ROUTED_COMMANDS,
    add_feature_parser,
    dispatch_feature_command,
    feature_doctor_check,
)
from cc_docs import (
    DOCS_MAINTENANCE_CAPABILITY_REGISTRY_ITEM,
    DOCS_MAINTENANCE_ROUTED_COMMANDS,
    add_docs_parser,
    dispatch_docs_command,
    load_release_manifest_contract,
    release_manifest_path_matches,
)
from cc_review import (
    REVIEW_ROUTED_COMMANDS,
    add_review_parser,
    dispatch_review_command,
)
from cc_init_module import (
    INIT_MODULE_ROUTED_COMMANDS,
    add_init_module_parser,
    dispatch_init_module_command,
)
from cc_memory_lib.freshness_projection import (
    architecture_index_unavailable,
    capture_regular_file_bytes,
    capture_project_plane_inputs,
    guarded_project_plane_packet,
    observe_freshness,
    observed_architecture_index_payload,
    observe_project_plane_inputs,
    projected_retrieval_payload,
)
from cc_memory_lib.op_index import _memory_health_text_lines, _op_index_payload, _startup_payload, _work_start_startup_payloads
from cc_memory_lib import lockfile, work_features

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# cc.py lives in scripts/, templates/ is a sibling directory
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
HOOKS_DIR = TEMPLATES_DIR / "hooks"
SCRIPTS_DIR = TEMPLATES_DIR / "scripts"
HELPER_DIR = TEMPLATES_DIR / "helper-session"

# Files that cc init copies to hooks/
INIT_HOOKS = [
    "check_boundaries.py",
    "check_dangerous_commands.py",
    "check_bash_writes.py",
    "check_repo_boundaries.py",
    "codewarden_review.py",
    "codewarden_plan_review.py",
    "codewarden_backend.py",
    "hook_logger.py",
    "hook_utils.py",
    "feature_lock.py",
    "check_file_organization.py",
    "violation_store.py",
    "session_end_check.py",
    "check_workflow.py",
]

# Base settings.json for cc init (hooks only, no MCP)
BASE_SETTINGS = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python hooks/check_boundaries.py",
                    }
                ],
            },
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python hooks/check_workflow.py",
                    }
                ],
            },
            {
                "matcher": "Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python hooks/check_file_organization.py",
                    }
                ],
            },
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python hooks/check_dangerous_commands.py",
                    }
                ],
            },
            {
                "matcher": "ExitPlanMode",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python hooks/codewarden_plan_review.py",
                    }
                ],
            },
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "python hooks/codewarden_review.py",
                    }
                ]
            }
        ],
    }
}

# Consultant backends supported by the shipped debug-tools adapters. Discovery
# may report which of these appear usable locally, but it must never select one.
SUPPORTED_CONSULTATION_BACKENDS = frozenset({"claude", "ollama", "openai", "anthropic"})

# MCP server definitions for each pack
MCP_CONFIGS = {
    "debug-tools": {
        "debug-consultant": {
            "command": "python",
            "args": ["tools/mcp_consultant.py"],
        }
    },
    "multi-agent": {
        "bridge": {
            "command": "python",
            "args": ["tools/mcp_bridge.py"],
            "env": {
                "BRIDGE_AGENT_ID": "coder",
                "BRIDGE_DIR": ".bridge",
            },
        }
    },
    "session-manager": {
        "session-manager": {
            "command": "python",
            "args": ["tools/mcp_session.py"],
        },
        "handoff": {
            "command": "python",
            "args": ["tools/mcp_handoff.py"],
        },
        "agent-memory": {
            "command": "python",
            "args": ["tools/mcp_agent_memory.py"],
        },
    },
    "vision": {
        "vision": {
            "command": "python",
            "args": ["tools/mcp_vision.py"],
        }
    },
}

# Files to copy for each pack
PACK_FILES = {
    "debug-tools": {
        "tools/": [
            SCRIPTS_DIR / "mcp_consultant.py",
            SCRIPTS_DIR / "consult.py",
            SCRIPTS_DIR / "visual_check.py",
            SCRIPTS_DIR / "visual_test.py",
        ],
    },
    "multi-agent": {
        "tools/": [
            SCRIPTS_DIR / "mcp_bridge.py",
        ],
    },
    "session-manager": {
        "tools/": [
            SCRIPTS_DIR / "cc_lockfile.py",
            SCRIPTS_DIR / "mcp_session.py",
            SCRIPTS_DIR / "mcp_agent_memory.py",
            SCRIPTS_DIR / "mcp_handoff.py",
        ],
    },
    "dashboard": {
        "tools/": [
            SCRIPTS_DIR / "cc_dashboard.py",
        ],
    },
    "vision": {
        "tools/": [
            SCRIPTS_DIR / "verification_agent.py",
            SCRIPTS_DIR / "verification_report.py",
            SCRIPTS_DIR / "visual_check.py",
            SCRIPTS_DIR / "visual_check_utils.py",
            SCRIPTS_DIR / "visual_test.py",
            SCRIPTS_DIR / "mcp_vision.py",
        ],
    },
    "visual-check": {
        "tools/": [
            SCRIPTS_DIR / "visual_check.py",
            SCRIPTS_DIR / "visual_check_utils.py",
        ],
    },
}

ALL_PACKS = ["debug-tools", "multi-agent", "session-manager", "dashboard",
             "vision", "visual-check"]

# Gitignore block for CC local artifacts
# Uses specific file patterns (not hooks/) to avoid conflicts with user directories
GITIGNORE_MARKER_START = "# --- ControlCoding local artifacts (do not commit) ---"
GITIGNORE_MARKER_END = "# --- End ControlCoding ---"
GITIGNORE_BLOCK = """\
# --- ControlCoding local artifacts (do not commit) ---
# Hook scripts (local copies, sourced from CC installation)
hooks/check_boundaries.py
hooks/check_dangerous_commands.py
hooks/check_repo_boundaries.py
hooks/codewarden_*.py
hooks/hook_logger.py
hooks/hook_utils.py
hooks/violation_store.py
hooks/session_end_check.py
hooks/check_workflow.py
hooks/check_bash_writes.py
hooks/feature_lock.py

# CC runtime artifacts (canonical)
.controlcoding/deny_hashes.json
.controlcoding/cc_hook_log.jsonl
.controlcoding/codewarden_violations.jsonl
.controlcoding/codewarden_report.md
.controlcoding/session_counter.json
.controlcoding/hooks_lifted.json
.controlcoding/lift_request.json
.controlcoding/agents/
.controlcoding/sessions/agents/
.controlcoding/external_consultation/
.controlcoding/write_path_metrics.json
.controlcoding/write_path_events.jsonl
.controlcoding/write_path_manifests/
.controlcoding/write_path_receipts/
.controlcoding/write_path_shadow/
.controlcoding/verification_receipts/
.controlcoding/verification_tmp/
.controlcoding/invariant_receipts/
.controlcoding/invariant_tmp/
.controlcoding/promotions/
.controlcoding/settings.json
.controlcoding/module_manifest.json
.controlcoding/memory/
.controlcoding/logs/
.controlcoding/views/
.controlcoding/memory_bootstrap_receipt.json
.controlcoding/cc_surface_lock.json
.controlcoding/active_module.json
.controlcoding/launchers/

# CC runtime artifacts (legacy compatibility)
.claude/deny_hashes.json
.claude/cc_hook_log.jsonl
.claude/codewarden_violations.jsonl
.claude/codewarden_report.md
.claude/session_counter.json
.claude/hooks_lifted.json
.claude/lift_request.json
.claude/consult_log.jsonl
.claude/consult_packets/
.claude/consult_results/
.claude/agents/
.claude/sessions/agents/
.claude/external_consultation/
.claude/write_path_manifests/
.claude/write_path_receipts/
.claude/write_path_shadow/
.claude/verification_receipts/
.claude/verification_tmp/
.claude/invariant_receipts/
.claude/invariant_tmp/
.claude/promotions/
.claude/settings.local.json
.claude/cc_surface_lock.json
.claude/active_module.json
.claude/launchers/
.vscode/tasks.json

# CC optional tools (local copies)
tools/fitness_check.py
tools/mcp_*.py
tools/visual_check.py
tools/visual_test.py
tools/consult.py
tools/cc_dashboard.py
tools/cc_audit.py

# Visual check and multi-agent runtime
screenshots/
.bridge/
# --- End ControlCoding ---
"""

GITIGNORE_ALWAYS_LOCAL_BLOCK = """\

# CC local session memory (always local)
devlog/
"""

GITIGNORE_LOCAL_DOCS_BLOCK = """\

# CC working documents (local-only mode)
STATUS.md
ROADMAP.md
BUGS.md
"""

CONTROL_PLANE_DIRNAME = ".controlcoding"
LEGACY_CONTROL_PLANE_DIRNAME = ".claude"
SURFACE_LOCK_RELATIVE_PATH = Path(CONTROL_PLANE_DIRNAME) / "cc_surface_lock.json"
HOST_LAUNCHERS_RELATIVE_DIR = Path(CONTROL_PLANE_DIRNAME) / "launchers"
PUBLIC_ASSISTANT_RELATIVE_DIR = Path(CONTROL_PLANE_DIRNAME) / "public_assistant"
VSCODE_TASKS_RELATIVE_PATH = Path(".vscode") / "tasks.json"
HOST_INTEGRATION_MANIFEST_RELATIVE_PATH = HOST_LAUNCHERS_RELATIVE_DIR / "manifest.json"
HOST_INSTRUCTIONS_RELATIVE_PATH = HOST_LAUNCHERS_RELATIVE_DIR / "host_instructions.md"
PUBLIC_CHAT_STARTER_RELATIVE_PATH = PUBLIC_ASSISTANT_RELATIVE_DIR / "START_HERE_WITH_CHAT.md"
PUBLIC_INSTALL_CONTRACT_RELATIVE_PATH = PUBLIC_ASSISTANT_RELATIVE_DIR / "INSTALL_ASSISTANT_CONTRACT.md"
PUBLIC_PROJECT_SETUP_CONTRACT_RELATIVE_PATH = PUBLIC_ASSISTANT_RELATIVE_DIR / "PROJECT_SETUP_ASSISTANT_CONTRACT.md"
SURFACE_STALE_SECONDS = 90
VALID_SURFACE_TYPES = {"official_host", "cc_ui_api", "cc_ui_local"}
VALID_SURFACE_ROLES = {"primary", "observer"}
VALID_HOST_INSTRUCTION_MODES = {"preset_only", "recommended", "custom"}

# Hook files that should not be tracked in git
CC_HOOK_FILES = [
    "hooks/check_boundaries.py",
    "hooks/check_dangerous_commands.py",
    "hooks/check_bash_writes.py",
    "hooks/check_repo_boundaries.py",
    "hooks/codewarden_review.py",
    "hooks/codewarden_plan_review.py",
    "hooks/codewarden_backend.py",
    "hooks/hook_logger.py",
    "hooks/hook_utils.py",
    "hooks/feature_lock.py",
    "hooks/violation_store.py",
    "hooks/session_end_check.py",
    "hooks/check_workflow.py",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def green(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[31m{text}\033[0m"


def ok(msg: str):
    print(f"  {green('OK')}  {msg}")


def warn(msg: str):
    print(f"  {yellow('WARN')}  {msg}")


def fail(msg: str):
    print(f"  {red('FAIL')}  {msg}")


def info(msg: str):
    print(f"  ...  {msg}")


def copy_file(src: Path, dest: Path):
    """Copy a file, creating parent directories as needed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    info(f"Copied {dest.relative_to(dest.parent.parent) if dest.parent.parent.exists() else dest.name}")


def _control_plane_dir(project: Path) -> Path:
    return project / CONTROL_PLANE_DIRNAME


def _legacy_control_plane_dir(project: Path) -> Path:
    return project / LEGACY_CONTROL_PLANE_DIRNAME


def _control_plane_path(project: Path, *parts: str) -> Path:
    return _control_plane_dir(project).joinpath(*parts)


def _legacy_control_plane_path(project: Path, *parts: str) -> Path:
    return _legacy_control_plane_dir(project).joinpath(*parts)


def _control_plane_read_path(project: Path, *parts: str) -> Path:
    canonical = _control_plane_path(project, *parts)
    if canonical.exists():
        return canonical
    legacy = _legacy_control_plane_path(project, *parts)
    if legacy.exists():
        return legacy
    return canonical


def _control_plane_display_path(*parts: str) -> str:
    return str(Path(CONTROL_PLANE_DIRNAME).joinpath(*parts)).replace("\\", "/")


def load_settings(project: Path) -> dict:
    """Load existing control-plane settings or return empty dict."""
    settings_path = _control_plane_read_path(project, "settings.json")
    if settings_path.exists():
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_settings(project: Path, settings: dict):
    from cc_setup import _save_settings_atomic
    _save_settings_atomic(_control_plane_path(project, "settings.json"), settings, os_module=os, json_module=json)
    info(f"Updated {_control_plane_display_path('settings.json')}")


def merge_hooks(existing: dict, new_hooks: dict) -> dict:
    """Merge hook configurations without duplicating entries.

    Deduplicates by (matcher, command) pair since multiple hooks can share
    the same matcher (e.g. check_boundaries.py and check_workflow.py both
    match Edit|Write).
    """
    for event, hook_list in new_hooks.items():
        if event not in existing:
            existing[event] = []
        # Build set of (matcher, command) pairs already present
        existing_keys = set()
        for entry in existing[event]:
            matcher = entry.get("matcher", "__stop__")
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                # Normalize: extract script name from absolute or relative path
                script = cmd.rsplit("/", 1)[-1] if "/" in cmd else cmd
                existing_keys.add((matcher, script))
        for entry in hook_list:
            matcher = entry.get("matcher", "__stop__")
            hooks = entry.get("hooks", [])
            cmd = hooks[0].get("command", "") if hooks else ""
            script = cmd.rsplit("/", 1)[-1] if "/" in cmd else cmd
            if (matcher, script) not in existing_keys:
                existing[event].append(entry)
                existing_keys.add((matcher, script))
    return existing


def merge_mcp_servers(existing: dict, new_servers: dict) -> dict:
    """Merge MCP server configurations, skipping already-configured servers."""
    for name, config in new_servers.items():
        if name not in existing:
            existing[name] = config
            info(f"Added MCP server: {name}")
        else:
            info(f"MCP server '{name}' already configured, skipping")
    return existing


def _central_hooks_dir() -> Path:
    """Return the platform-appropriate central hooks directory."""
    home = Path.home()
    return home / ".controlcoding" / "hooks"


def _get_hooks_location(project: Path) -> str:
    """Read hooks_location from cc_config.json. Returns 'local' or 'central'."""
    cc_config = _control_plane_read_path(project, "cc_config.json")
    if cc_config.exists():
        try:
            config = json.loads(cc_config.read_text(encoding="utf-8"))
            return config.get("hooks_location", "local")
        except (json.JSONDecodeError, KeyError):
            pass
    return "local"


def _save_hooks_location(project: Path, location: str):
    """Save hooks_location to cc_config.json, preserving other fields."""
    cc_config = _control_plane_read_path(project, "cc_config.json")
    if not cc_config.exists():
        cc_config = _control_plane_path(project, "cc_config.json")
    config = {}
    if cc_config.exists():
        try:
            config = json.loads(cc_config.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    config["hooks_location"] = location
    cc_config.parent.mkdir(parents=True, exist_ok=True)
    cc_config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _build_gitignore_block(
    central_hooks: bool = False,
    documentation_mode: str = "managed",
    cc_artifact_mode: str = "local_only",
) -> str:
    """Build the managed .gitignore block for the current CC policy."""
    block_lines = []
    skip = False
    for line in GITIGNORE_BLOCK.splitlines():
        if central_hooks and line.startswith("# Hook scripts"):
            skip = True
            continue
        if central_hooks and skip and line == "":
            skip = False
            continue
        if central_hooks and skip and line.startswith("hooks/"):
            continue
        block_lines.append(line)

    block_lines.extend(GITIGNORE_ALWAYS_LOCAL_BLOCK.strip("\n").splitlines())

    if cc_artifact_mode == "local_only":
        block_lines.extend(GITIGNORE_LOCAL_DOCS_BLOCK.strip("\n").splitlines())
        if documentation_mode == "managed":
            block_lines.append("dev/")

    return "\n".join(block_lines) + "\n"


def _ensure_gitignore(project: Path, central_hooks: bool = False) -> bool:
    """Append CC gitignore block if not already present. Returns True if added."""
    gitignore = project / ".gitignore"
    documentation_mode = "managed"
    cc_artifact_mode = "local_only"

    cc_config = _control_plane_read_path(project, "cc_config.json")
    if cc_config.exists():
        try:
            config = json.loads(cc_config.read_text(encoding="utf-8"))
            documentation_mode = str(config.get("documentation_mode", "managed"))
            cc_artifact_mode = str(config.get("cc_artifact_mode", "local_only"))
        except json.JSONDecodeError:
            pass

    block = _build_gitignore_block(
        central_hooks=central_hooks,
        documentation_mode=documentation_mode,
        cc_artifact_mode=cc_artifact_mode,
    )

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if GITIGNORE_MARKER_START in content:
            return False  # already present
        # Ensure trailing newline before appending
        if content and not content.endswith("\n"):
            content += "\n"
        content += "\n" + block
        gitignore.write_text(content, encoding="utf-8")
    else:
        gitignore.write_text(block, encoding="utf-8")
    return True


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


CANONICAL_CONTEXT_FILENAME = "CONTROLCODING.md"
CONTROLWORK_CONTEXT_FILENAME = "CONTROLWORK.md"
LEGACY_CONTEXT_FILENAME = "CLAUDE.md"
HOST_PROFILE_SCHEMA_VERSION = 1

_HOST_CAPABILITY_CLASSES = frozenset({
    "native_inline_hooks",
    "sandbox_approval",
    "instruction_first",
})
_HOST_PERMISSION_GATES = frozenset({
    "official_host_permissions",
    "sandbox_approvals",
    "none",
})
_HOST_INLINE_BOUNDARY_GATES = frozenset({
    "native_hooks",
    "none",
})
_HOST_REPO_BOUNDARY_GATES = frozenset({
    "git_pre_commit",
})
_HOST_REVIEW_GATES = frozenset({
    "native_hooks",
    "post_commit_or_manual",
})
_HOST_VERIFICATION_GATES = frozenset({
    "criteria_and_invariants",
})
_HOST_PROTECTION_MODELS = frozenset({
    "inline_first",
    "repo_side",
    "review_driven",
})
_VALID_CONTROLLED_WRITE_MODES = frozenset({
    "off",
    "patch_gateway",
})
_VALID_CONTROLLED_WRITE_SCOPES = frozenset({
    "opt_in",
})
_VALID_CONTROLLED_WRITE_PATCH_FORMATS = frozenset({
    "unified_diff",
})
WRITE_PATH_METRICS_FILENAME = "write_path_metrics.json"
WRITE_PATH_EVENTS_FILENAME = "write_path_events.jsonl"
WRITE_PATH_MANIFEST_DIRNAME = "write_path_manifests"
WRITE_PATH_RECEIPT_DIRNAME = "write_path_receipts"
WRITE_PATH_SHADOW_DIRNAME = "write_path_shadow"
WRITE_PATH_MANIFEST_SCHEMA_VERSION = 1
WRITE_PATH_RECEIPT_SCHEMA_VERSION = 1
BENCHMARK_RUN_OUTPUT = Path("benchmarks") / "run.json"
BENCHMARK_REPORT_OUTPUT = Path("benchmarks") / "report.md"
BENCHMARK_MATRIX_OUTPUT = Path("benchmarks") / "multi-host-capability-matrix.md"
BENCHMARK_LOCAL_REPORT_OUTPUT = Path("benchmarks") / "local-dogfooding-evidence.md"
BENCHMARK_HOST_ORDER = [
    "claude_code",
    "codex_cli",
    "cursor",
    "windsurf",
    "vscode",
    "cline",
    "gemini_cli",
    "other",
]
HOST_BENCHMARK_EVIDENCE = {
    "claude_code": {
        "status": "reference_only",
        "notes": "Reference model for selected native hook routes; no named-host execution is attested here.",
        "artifacts": [],
    },
    "codex_cli": {
        "status": "curated_partial",
        "notes": "Curated historical external-workspace reports; these do not attest current hook delivery or host parity.",
        "artifacts": [
            "benchmarks/2026-04-11-cctest-3dcrawler-v11-scorecard.md",
            "benchmarks/2026-04-11-cctest-3dcrawler-v11-review-check.md",
            "benchmarks/2026-04-11-cctest-3dcrawler-v11-vs-claude-comparison.md",
            "benchmarks/2026-04-11-cctest-3dcrawler-v11-issue-register.md",
        ],
    },
    "cursor": {
        "status": "planned",
        "notes": "Capability model is explicit, but benchmark evidence is still pending.",
        "artifacts": [],
    },
    "windsurf": {
        "status": "planned",
        "notes": "Capability model is explicit, but benchmark evidence is still pending.",
        "artifacts": [],
    },
    "vscode": {
        "status": "planned",
        "notes": "VS Code is treated as an editor shell; real host evidence depends on the actual AI host used inside it.",
        "artifacts": [],
    },
    "cline": {
        "status": "platform_dependent",
        "notes": "Inline-hook support depends on platform/runtime. Benchmark claims must name the exact platform.",
        "artifacts": [],
    },
    "gemini_cli": {
        "status": "planned",
        "notes": "Repo-side/review-driven support is modeled, but benchmark evidence is still pending.",
        "artifacts": [],
    },
    "other": {
        "status": "advisory_only",
        "notes": "Generic fallback host. Use only as an explicit unknown-host bucket, not as evidence of support parity.",
        "artifacts": [],
    },
}
CONSULT_LOG_FILENAME = "consult_log.jsonl"
CONSULT_PACKET_DIRNAME = "consult_packets"
CONSULT_RESULT_DIRNAME = "consult_results"
EXTERNAL_CONSULTATION_DIRNAME = "external_consultation"
EXTERNAL_CONSULT_REQUEST_DIRNAME = "requests"
EXTERNAL_CONSULT_RESPONSE_DIRNAME = "responses"
EXTERNAL_CONSULT_IMPORT_DIRNAME = "imports"
EXTERNAL_CONSULT_THREAD_DIRNAME = "threads"
EXTERNAL_CONSULT_ROLE_MEMORY_DIRNAME = "role_memory"
EXTERNAL_CONSULT_RESOLUTION_DIRNAME = "resolution"
CONSULT_THREAD_STATE_FILENAME = "thread.json"
CONSULT_THREAD_MEMORY_FILENAME = "memory.md"
CONSULT_THREAD_RESUME_FILENAME = "resume_prompt.md"
CONSULT_THREAD_DECISIONS_FILENAME = "decisions.jsonl"
CONSULT_ROLE_MEMORY_STATE_FILENAME = "role.json"
CONSULT_ROLE_MEMORY_MARKDOWN_FILENAME = "memory.md"
CONSULT_CONVERGENCE_SUMMARY_FILENAME = "convergence_summary.json"
CONSULT_CONVERGENCE_SUMMARY_MARKDOWN_FILENAME = "convergence_summary.md"
CONSULT_RESOLUTION_PROMPT_FILENAME = "resolution_prompt.md"
_VALID_SPECIALIST_PERMISSION_MODES = frozenset({
    "user_mediated",
    "approval_required",
    "within_budget_auto",
    "disabled",
})
_VALID_SPECIALIST_EXECUTION_MODES = frozenset({
    "human_mediated",
    "cc_routed",
    "auto_bounded",
    "disabled",
})
_VALID_CONSULT_DECISIONS = frozenset({
    "accepted",
    "rejected",
    "partial",
})
_CONSULT_OBJECTIVE_LIMIT = 400
_CONSULT_CONTEXT_SUMMARY_LIMIT = 1600
_CONSULT_EXPECTED_ANSWER_LIMIT = 500
_CONSULT_LIST_ITEM_LIMIT = 320
_CONSULT_SUMMARY_LIMIT = 1600
_CONSULT_RATIONALE_LIMIT = 900
_CONSULT_NEXT_ACTION_LIMIT = 500
_CONSULT_MAX_LIST_ITEMS = 8

_CAPABILITY_STATES = frozenset({
    "shipped",
    "optional",
    "experimental",
    "planned",
    "private",
    "removed",
})
_CAPABILITY_CONTROL_LEVELS = frozenset({
    "mechanical",
    "conditional",
    "advisory",
    "unavailable",
})
_ROUTED_CLI_COMMANDS = frozenset({
    ("init",), ("install",),
    ("setup",),
    ("setup-project",),
    ("chat-start",),
    ("work-start",),
    ("work-close",),
    ("release-doctor",),
    ("doctor",),
    ("truth",),
    ("truth", "report"),
    ("truth", "check"),
    ("truth", "check-docs"),
    *DOCS_MAINTENANCE_ROUTED_COMMANDS,
    ("verify",),
    ("verify", "init"),
    ("verify", "status"),
    ("verify", "run"),
    ("invariants",),
    ("invariants", "init"),
    ("invariants", "elicit"),
    ("invariants", "status"),
    ("invariants", "list"),
    ("invariants", "add"),
    ("invariants", "report"),
    ("invariants", "doctor"),
    ("invariants", "run"),
    ("invariants", "wire-ci"),
    *FEATURE_ROUTED_COMMANDS,
    ("promote",),
    ("promote", "plan"),
    ("promote", "check"),
    ("promote", "apply"),
    ("host",),
    ("host", "status"),
    ("host", "switch"),
    ("host", "compare"),
    ("host", "migrate-plan"),
    ("context",),
    ("context", "check"),
    ("context", "diff"),
    ("context", "drift"),
    ("context", "sync"),
    ("context", "adopt"),
    ("write-path",),
    ("write-path", "status"),
    ("write-path", "receipts"),
    ("write-path", "receipt"),
    ("write-path", "enable"),
    ("write-path", "disable"),
    ("write-path", "prepare"),
    ("write-path", "check"),
    ("write-path", "apply"),
    ("memory",),
    ("memory", "init"),
    ("memory", "doctor"),
    ("memory", "scan"),
    ("memory", "status"),
    ("memory", "bootstrap"),
    ("memory", "op-index"),
    ("memory", "startup"),
    ("memory", "session"),
    ("memory", "session", "start"),
    ("memory", "session", "close"),
    ("memory", "session", "list"),
    ("memory", "session", "show"),
    ("memory", "session", "link"),
    ("memory", "session", "note"),
    ("memory", "session", "views"),
    ("memory", "session-pack"),
    ("memory", "graph"),
    ("memory", "graph", "status"),
    ("memory", "graph", "suggestions"),
    ("memory", "graph", "accept"),
    ("memory", "graph", "reject"),
    ("memory", "graph", "around"),
    ("memory", "graph", "export"),
    ("memory", "retrieve"),
    ("memory", "rag-pack"),
    *MEMORY_AUX_ROUTED_COMMANDS,
    ("memory", "cross-pack"),
    ("memory", "semantic"),
    ("memory", "ocr", "status"),
    ("memory", "ocr", "run"),
    ("memory", "impact"),
    ("memory", "context"),
    ("memory", "dev-context-pack"),
    ("memory", "chunks"),
    ("memory", "layout"),
    ("memory", "intake"),
    ("memory", "lifecycle"),
    ("memory", "lifecycle", "mark"),
    ("memory", "lifecycle", "supersede"),
    ("memory", "lifecycle", "conflict"),
    ("memory", "vector"),
    ("memory", "vector", "rebuild"),
    ("memory", "vector", "search"),
    ("memory", "note"),
    ("memory", "idea"),
    ("memory", "decision"),
    ("memory", "consult"),
    ("memory", "agent-run"),
    ("memory", "sync-report"),
    ("memory", "cleanup-temp"),
    ("memory", "views"),
    ("memory", "work-init"),
    ("memory", "work-status"),
    ("memory", "work-parity"),
    ("memory", "work-quickstart"),
    ("memory", "work-attach"),
    ("memory", "work-import"),
    ("memory", "work-import-source"),
    ("memory", "work-ocr"),
    ("memory", "work-ocr", "status"),
    ("memory", "work-ocr", "run"),
    ("memory", "work-ocr", "import-sidecar"),
    ("memory", "work-export"),
    ("memory", "work-sync"),
    ("memory", "work-category"),
    ("memory", "work-category", "add"),
    ("memory", "work-category", "propose"),
    ("memory", "work-category", "list"),
    ("memory", "work-category", "approve"),
    ("memory", "work-scan"),
    ("memory", "work-analyze"),
    ("memory", "work-review"),
    ("memory", "work-promote"),
    ("memory", "work-capture"),
    ("memory", "work-session"),
    ("memory", "work-session", "start"),
    ("memory", "work-session", "close"),
    ("memory", "work-session", "list"),
    ("memory", "work-session", "show"),
    ("memory", "work-session", "link"),
    ("memory", "work-session", "note"),
    ("memory", "work-graph"),
    ("memory", "work-graph", "status"),
    ("memory", "work-graph", "suggestions"),
    ("memory", "work-graph", "accept"),
    ("memory", "work-graph", "reject"),
    ("memory", "work-graph", "explain"),
    ("memory", "work-graph", "path"),
    ("memory", "work-graph", "neighbors"),
    ("memory", "work-graph", "stale"),
    ("memory", "work-graph", "unresolved"),
    ("memory", "work-graph", "diff"),
    ("memory", "work-graph", "export"),
    ("memory", "work-query"),
    ("memory", "work-retrieve"),
    ("memory", "work-rag-pack"),
    ("memory", "work-views"),
    ("memory", "work-checkpoint"),
    ("memory", "work-handoff"),
    ("memory", "work-dashboard"),
    ("memory", "work-context-pack"),
    ("memory", "work-obsidian"),
    ("memory", "work-obsidian", "init"),
    ("memory", "work-obsidian", "check"),
    ("memory", "work-wiki"),
    ("memory", "work-wiki", "import-edits"),
    ("memory", "work-mcp"),
    ("memory", "work-mcp", "tools"),
    ("memory", "work-mcp", "call"),
    *REVIEW_ROUTED_COMMANDS,
    *INIT_MODULE_ROUTED_COMMANDS,
    ("organize",),
    ("export", "agents-md"),
    ("export", "host-context"), ("resume",),
    ("agents",),
    ("index",),
    ("benchmark-matrix",),
    ("benchmark-matrix", "generate"),
    ("benchmark-matrix", "report-local"),
    ("benchmark",),
    ("benchmark", "run"),
    ("benchmark", "compare"),
    ("benchmark", "report"),
    ("surface",),
    ("surface", "status"),
    ("surface", "claim"),
    ("surface", "observe"),
    ("surface", "request-takeover"),
    ("surface", "resolve-takeover"),
    ("surface", "heartbeat"),
    ("surface", "release"),
    ("surface", "run"),
    ("consult",),
    ("consult", "status"),
    ("consult", "resolution"),
    ("consult-packet",),
    ("consult-packet", "create"),
    ("consult-packet", "show"),
    ("consult-result",),
    ("consult-result", "import"),
})
_TRUTH_DOC_COMMAND_FILES = (
    "README.md",
    "docs/quick-start.md",
    "docs/install-controlcoding-on-your-project.md",
)
_TRUTH_STRONG_CONTROL_CLAIM_PATTERN = re.compile(
    r"\b(mechanical(?:ly)?|enforce(?:d|able|ment|s|ing)?)\b",
    re.IGNORECASE,
)
_TRUTH_BLOCKING_CONTROL_CLAIM_PATTERN = re.compile(
    r"\b(block(?:s|ed|ing)?|prevent(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)
_TRUTH_BLOCKING_CONTROL_CONTEXT_PATTERN = re.compile(
    r"\b(write|edit|patch|command|zone|gate|hook|protected|deny|policy)\b",
    re.IGNORECASE,
)
_TRUTH_OPERATIONAL_CLAIM_CONTEXT_TERMS = (
    "hook",
    "hooks",
    "pre-commit",
    "pre-write",
    "write-path",
    "write path",
    "doctor",
    "context check",
    "context sync",
    "truth check",
    "codewarden",
    "gate",
    "gates",
    "git",
    "ci",
    "settings.json",
    "feature.lock",
    "deny",
    "warn",
    "host",
    "inline",
    "repo-side",
    "repo side",
    "boundary",
    "boundaries",
    "verification",
    "review",
    "controlled write",
    "manifest",
    "receipt",
    "surface",
    "authority",
    "lock",
    "command",
    "commands",
    "tests",
    "invariant",
    "invariants",
    "protected zone",
    "protected zones",
    "protected file",
    "protected files",
    "baseline",
    "fitness",
)
_TRUTH_CLI_ENTRYPOINTS = {
    "cc",
    "cc.py",
    "scripts/cc.py",
}
VERIFICATION_CONTRACT_FILENAME = "controlcoding.verification.json"
VERIFICATION_CONTRACT_SCHEMA_VERSION = 1
VERIFICATION_RECEIPTS_DIRNAME = "verification_receipts"
VERIFICATION_TEMP_DIRNAME = "verification_tmp"
INVARIANT_MANIFEST_FILENAME = "controlcoding.invariants.json"
INVARIANT_SCHEMA_VERSION = 1
INVARIANT_RECEIPTS_DIRNAME = "invariant_receipts"
INVARIANT_TEMP_DIRNAME = "invariant_tmp"
PROMOTION_DIRNAME = "promotions"
REPLACEMENT_DIRNAME = "replacements"
PROMOTION_SCHEMA_VERSION = 1
_VERIFICATION_KINDS = frozenset({
    "targeted",
    "regression",
    "invariant",
    "smoke",
    "release",
})
_DEFAULT_REQUIRED_VERIFICATION_KINDS = ("targeted", "regression", "invariant")
_INVARIANT_KINDS = frozenset({
    "domain",
    "structural",
    "security",
    "consistency",
    "determinism",
    "performance",
    "fitness",
})
_INVARIANT_SEVERITIES = frozenset({
    "blocking",
    "warning",
    "informational",
})
_INVARIANT_STATUSES = frozenset({
    "active",
    "draft",
    "retired",
})
_INVARIANT_DOMAIN_ALIASES = {
    "physical": "simulation",
    "physical-simulation": "simulation",
    "physical_simulation": "simulation",
    "web-api": "web",
    "web_api": "web",
    "api": "web",
    "content-graph": "content",
    "content_graph": "content",
    "game-state": "game",
    "game_state": "game",
    "pipeline": "data-pipeline",
    "data": "data-pipeline",
    "data_pipeline": "data-pipeline",
}
_INVARIANT_ELICITATION_PACKS = {
    "finance": {
        "label": "Finance",
        "questions": [
            "Which balances, ledgers, or totals must be conserved exactly?",
            "Which operations must be idempotent when retried with the same key?",
            "Which audit records must exist for every state change?",
            "Which monetary values have fixed ranges, currencies, or precision rules?",
            "Which failure modes must stop the operation instead of falling back silently?",
        ],
        "candidates": [
            {
                "id": "balance-consistency",
                "title": "Balance Consistency",
                "kind": "domain",
                "property": "Every accepted transaction preserves total debits and credits.",
                "threshold": "0 balance drift",
            },
            {
                "id": "transaction-idempotency",
                "title": "Transaction Idempotency",
                "kind": "consistency",
                "property": "Retrying the same transaction key produces the same final result without duplication.",
                "threshold": "0 duplicate accepted transactions for the same idempotency key",
            },
            {
                "id": "audit-trail-complete",
                "title": "Audit Trail Complete",
                "kind": "security",
                "property": "Every balance-affecting change has an immutable who, when, what, and why audit record.",
                "threshold": "100 percent audited state changes",
            },
        ],
    },
    "simulation": {
        "label": "Physical Simulation",
        "questions": [
            "Which quantities must be conserved across each simulation step?",
            "Which inputs and seeds must produce identical replay output?",
            "Which boundary conditions must remain continuous?",
            "Which numerical ranges indicate impossible or unstable states?",
            "Which performance budget is required per step or frame?",
        ],
        "candidates": [
            {
                "id": "deterministic-replay",
                "title": "Deterministic Replay",
                "kind": "determinism",
                "property": "The same seed and input sequence produce the same output hash.",
                "threshold": "identical replay hash",
            },
            {
                "id": "conservation-law",
                "title": "Conservation Law",
                "kind": "domain",
                "property": "The domain-specific conserved quantity is neither created nor destroyed by a step.",
                "threshold": "drift within the approved numerical tolerance",
            },
            {
                "id": "boundary-continuity",
                "title": "Boundary Continuity",
                "kind": "consistency",
                "property": "Adjacent regions remain continuous at their shared boundary.",
                "threshold": "boundary discontinuity below the domain threshold",
            },
        ],
    },
    "web": {
        "label": "Web API",
        "questions": [
            "Which endpoints expose protected data?",
            "Which operations require authentication, authorization, and rate limits?",
            "Which database relationships must never become orphaned?",
            "Which responses must stay within latency and size budgets?",
            "Which external calls must have timeout, retry, and failure visibility?",
        ],
        "candidates": [
            {
                "id": "protected-endpoints-authenticated",
                "title": "Protected Endpoints Authenticated",
                "kind": "security",
                "property": "Protected endpoints never expose data without authentication and authorization.",
                "threshold": "0 unauthenticated protected endpoints",
            },
            {
                "id": "no-orphan-records",
                "title": "No Orphan Records",
                "kind": "consistency",
                "property": "Persistent relationships never point to missing records.",
                "threshold": "0 orphan references",
            },
            {
                "id": "api-latency-budget",
                "title": "API Latency Budget",
                "kind": "performance",
                "property": "Critical API routes remain within the agreed response-time budget.",
                "threshold": "p95 latency within the project budget",
            },
        ],
    },
    "content": {
        "label": "Content Graph",
        "questions": [
            "Which content items must always be reachable?",
            "Which links, references, or embeds must resolve?",
            "Which taxonomies are closed sets?",
            "Which publication states are allowed to transition to each other?",
            "Which generated views must be derived from authoritative source content?",
        ],
        "candidates": [
            {
                "id": "no-orphan-content-links",
                "title": "No Orphan Content Links",
                "kind": "consistency",
                "property": "Every content link resolves to an existing target.",
                "threshold": "0 orphan links",
            },
            {
                "id": "all-published-content-reachable",
                "title": "All Published Content Reachable",
                "kind": "consistency",
                "property": "Every published content item is reachable from an approved entry point.",
                "threshold": "0 unreachable published items",
            },
            {
                "id": "valid-publication-transitions",
                "title": "Valid Publication Transitions",
                "kind": "domain",
                "property": "Content state transitions only follow the approved workflow.",
                "threshold": "0 invalid state transitions",
            },
        ],
    },
    "game": {
        "label": "Game State",
        "questions": [
            "Which player or world state values have valid ranges?",
            "Which gameplay outcomes must be deterministic from the same seed or replay input?",
            "Which entities must have one owner, parent, or lifecycle state?",
            "Which systems must communicate through events instead of direct coupling?",
            "Which frame or tick budgets must be preserved?",
        ],
        "candidates": [
            {
                "id": "state-range-valid",
                "title": "State Range Valid",
                "kind": "domain",
                "property": "Runtime game-state values stay inside their valid domain ranges.",
                "threshold": "0 out-of-range state values",
            },
            {
                "id": "replay-deterministic",
                "title": "Replay Deterministic",
                "kind": "determinism",
                "property": "The same replay input produces the same gameplay state sequence.",
                "threshold": "identical replay state hash",
            },
            {
                "id": "entity-lifecycle-consistent",
                "title": "Entity Lifecycle Consistent",
                "kind": "consistency",
                "property": "Entities never exist in impossible ownership or lifecycle states.",
                "threshold": "0 invalid entity lifecycle records",
            },
        ],
    },
    "data-pipeline": {
        "label": "Data Pipeline",
        "questions": [
            "Which input records must be conserved, deduplicated, or accounted for?",
            "Which transformations must be deterministic and replayable?",
            "Which schema, type, and nullability rules must never drift?",
            "Which late, failed, or partial records require visible quarantine?",
            "Which freshness and throughput budgets matter?",
        ],
        "candidates": [
            {
                "id": "record-accounting-complete",
                "title": "Record Accounting Complete",
                "kind": "consistency",
                "property": "Every input record is either processed, rejected with reason, or quarantined.",
                "threshold": "0 unaccounted input records",
            },
            {
                "id": "schema-contract-stable",
                "title": "Schema Contract Stable",
                "kind": "structural",
                "property": "Pipeline outputs conform to the approved schema contract.",
                "threshold": "0 schema contract violations",
            },
            {
                "id": "transform-deterministic",
                "title": "Transform Deterministic",
                "kind": "determinism",
                "property": "The same source batch and configuration produce the same output batch.",
                "threshold": "identical output hash",
            },
        ],
    },
    "generic": {
        "label": "Generic Project",
        "questions": [
            "What must never be lost, duplicated, or silently changed?",
            "What is the authoritative source of truth, and what is only a derived view?",
            "Which values have fixed ranges, closed sets, or monotonic behavior?",
            "Which operation must be idempotent or deterministic?",
            "Which architectural boundary must not be crossed?",
        ],
        "candidates": [
            {
                "id": "source-of-truth-preserved",
                "title": "Source Of Truth Preserved",
                "kind": "consistency",
                "property": "Derived views never become the authoritative source of truth.",
                "threshold": "0 source-of-truth violations",
            },
            {
                "id": "closed-set-complete",
                "title": "Closed Set Complete",
                "kind": "domain",
                "property": "All references to a closed domain set use approved values only.",
                "threshold": "0 unknown values",
            },
            {
                "id": "boundary-rule-preserved",
                "title": "Boundary Rule Preserved",
                "kind": "structural",
                "property": "Forbidden dependency or file-boundary relationships do not appear.",
                "threshold": "0 boundary violations",
            },
        ],
    },
}
_PROMOTION_ZONES = ("workspace", "features", "shared", "stable")
_PROMOTION_NEXT_ZONE = {
    "workspace": "features",
    "features": "shared",
    "shared": "stable",
}
_RELEASE_REQUIRED_DIRS = (
    "docs",
    "scripts",
    "templates",
    "tests",
)
_RELEASE_REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "COMMERCIAL_LICENSE.md",
    "NOTICE",
    "TRADEMARKS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "INSTALL_WIZARD.md",
    "PROJECT_SETUP_WIZARD.md",
    "controlcoding.release.json",
    "controlcoding.verification.json",
    "controlcoding.invariants.json",
    "scripts/cc.py",
    "scripts/cc_memory.py",
    "templates/CLAUDE.md.template",
    "templates/cc_config.json.example",
    "templates/adr.md",
    "templates/hooks/check_boundaries.py",
    "templates/hooks/check_dangerous_commands.py",
    "templates/hooks/settings.json.example",
    "docs/INDEX.md",
    "docs/quick-start.md",
    "docs/install-controlcoding-on-your-project.md",
    "tests/test_cc_cli.py",
    "tests/test_gitignore.py",
)
_RELEASE_MANIFEST_FILE = "controlcoding.release.json"
_RELEASE_FORBIDDEN_TRACKED_PREFIXES = (
    ".controlcoding/",
    ".claude/",
    ".bridge/",
    ".pytest_cache/",
    ".tmp",
    "devlog/",
    "screenshots/",
)
_RELEASE_FORBIDDEN_TRACKED_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "STATUS.md",
    "ROADMAP.md",
    "BUGS.md",
    "cc_hook_log.jsonl",
}
_RELEASE_FORBIDDEN_TRACKED_PATH_TERM_PARTS = (
    ("docs/", "external", "-graph-tool-gap-implementation-plan.md"),
    ("docs/", "product", "-polish-cleanup-plan.md"),
    ("docs/", "commercial", "ization-plan.md"),
)
_RELEASE_PUBLIC_HYGIENE_TERM_PARTS = (
    ("named external graph tool reference", ("gra", "phify")),
    ("competitive rank claim", ("super", "iority")),
)
_CORE_CAPABILITY_REGISTRY = [
    {
        "id": "context_sync",
        "label": "Canonical context and host projection sync",
        "state": "shipped",
        "control_level": "mechanical",
        "hosts": ["claude_code", "codex_cli", "gemini_cli", "cline"],
        "commands": ["context check", "context sync", "context adopt", "export host-context"],
        "evidence": ["exact generated-content comparison", "doctor.contextSync", "tests/test_cc_cli.py"],
    },
    {
        "id": "constitution_drift",
        "label": "Living project constitution drift check",
        "state": "shipped",
        "control_level": "conditional",
        "hosts": ["all"],
        "commands": ["context drift", "doctor"],
        "evidence": ["CONTROLCODING.md references", "doctor.constitutionDrift", "tests/test_cc_cli.py"],
    },
    {
        "id": "host_capability_model",
        "label": "Host capability model and gate contract",
        "state": "shipped",
        "control_level": "conditional",
        "hosts": ["claude_code", "codex_cli", "gemini_cli", "cline", "vscode", "other"],
        "commands": ["host status", "host switch", "doctor"],
        "evidence": ["hostProfile", "gateContract", "tests/test_cc_cli.py"],
    },
    {
        "id": "repo_boundary_gate",
        "label": "Repository-side boundary gate for non-inline hosts",
        "state": "shipped",
        "control_level": "conditional",
        "hosts": ["codex_cli", "gemini_cli", "vscode", "other"],
        "commands": ["init", "doctor"],
        "evidence": ["git pre-commit wiring", "check_repo_boundaries.py", "doctor repo_boundary_gate"],
    },
    {
        "id": "controlled_write_path",
        "label": "Opt-in controlled write path",
        "state": "experimental",
        "control_level": "conditional",
        "hosts": ["codex_cli", "gemini_cli", "vscode", "other"],
        "commands": ["write-path status", "write-path enable", "write-path check", "write-path apply"],
        "evidence": ["patch gateway receipts", "write path metrics", "tests/test_cc_cli.py"],
    },
    {
        "id": "project_memory",
        "label": "Project memory graph and local search",
        "state": "optional",
        "control_level": "advisory",
        "hosts": ["all"],
        "commands": ["chat-start", "work-start", "work-close", "memory init", "memory doctor", "memory scan", "memory status", "memory bootstrap", "memory op-index", "memory startup", "memory session", "memory session-pack", "memory retrieve", "memory rag-pack", "memory evidence", "memory eval", "memory cross-pack", "memory work-status", "memory work-parity", "memory work-quickstart", "memory work-scan", "memory work-analyze", "memory work-review", "memory work-import-source", "memory work-ocr", "memory work-promote", "memory work-capture", "memory work-session", "memory work-graph", "memory work-query", "memory work-retrieve", "memory work-rag-pack", "memory work-dashboard", "memory impact", "memory context", "memory chunks", "memory layout", "memory ocr status", "memory ocr run", "memory vector"],
        "evidence": ["scripts/cc_memory.py", "scripts/cc_memory_lib", "tests/test_cc_memory.py"],
    },
    {
        "id": "work_start_governance",
        "label": "Visible work lifecycle governance gates",
        "state": "experimental",
        "control_level": "advisory",
        "hosts": ["all"],
        "commands": ["chat-start", "work-start", "work-close"],
        "evidence": ["memory startup", "op-index", "context check", "architecture index", "tests/test_cc_cli.py"],
    },
    {
        "id": "truth_contract",
        "label": "Machine-readable capability truth contract",
        "state": "shipped",
        "control_level": "mechanical",
        "hosts": ["all"],
        "commands": ["truth report", "truth check", "truth check-docs", "doctor"],
        "evidence": ["built-in capability registry", "command route validation", "docs command route validation", "doctor claimIntegrity"],
    },
    {
        "id": "source_release_doctor",
        "label": "Source release publishability doctor",
        "state": "shipped",
        "control_level": "mechanical",
        "hosts": ["all"],
        "commands": ["release-doctor"],
        "evidence": ["controlcoding.release.json", "doctor --release compatibility", "release doctor checks", "operationalContract.releaseReady", "tests/test_cc_cli.py"],
    },
    DOCS_MAINTENANCE_CAPABILITY_REGISTRY_ITEM,
    {
        "id": "verification_contract",
        "label": "Verification contract for targeted, regression, invariant, smoke, and release checks",
        "state": "shipped",
        "control_level": "conditional",
        "hosts": ["all"],
        "commands": ["verify init", "verify status", "verify run", "doctor"],
        "evidence": ["controlcoding.verification.json", "verification receipts", "doctor verification_contract"],
    },
    {
        "id": "first_class_invariants",
        "label": "First-class invariant manifest and executable invariant runner",
        "state": "shipped",
        "control_level": "conditional",
        "hosts": ["all"],
        "commands": ["invariants init", "invariants elicit", "invariants status", "invariants list", "invariants add", "invariants report", "invariants doctor", "invariants run", "invariants wire-ci", "doctor"],
        "evidence": ["controlcoding.invariants.json", "invariant receipts", "CI wiring detection", "doctor invariant_manifest"],
    },
    FEATURE_CAPABILITY_REGISTRY_ITEM,
    {
        "id": "promotion_path",
        "label": "Executable workspace to features to shared to stable promotion path",
        "state": "experimental",
        "control_level": "conditional",
        "hosts": ["all"],
        "commands": ["promote plan", "promote check", "promote apply", "doctor"],
        "evidence": [".controlcoding/promotions", "promotion ADR generation", "doctor promotionPath"],
    },
]


def _canonical_context_path(project: Path) -> Path:
    return project / CANONICAL_CONTEXT_FILENAME


def _controlwork_context_path(project: Path) -> Path:
    return project / CONTROLWORK_CONTEXT_FILENAME


def _legacy_context_path(project: Path) -> Path:
    return project / LEGACY_CONTEXT_FILENAME


def _resolve_explicit_context_source(project: Path, source: str) -> Path | None:
    source_label = str(source or "").strip().replace("\\", "/")
    while source_label.startswith("./"):
        source_label = source_label[2:]
    if not source_label:
        return None
    candidate = Path(source_label)
    if not candidate.is_absolute():
        candidate = project / candidate
    try:
        resolved_project = project.resolve()
        resolved_candidate = candidate.resolve()
        resolved_candidate.relative_to(resolved_project)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved_candidate


def _read_context_source(project: Path, source: str = "") -> tuple[Path | None, str]:
    explicit_source = _resolve_explicit_context_source(project, source)
    if str(source or "").strip() and explicit_source is None:
        return None, ""
    if explicit_source is not None:
        if explicit_source.exists() and explicit_source.is_file():
            return explicit_source, explicit_source.read_text(encoding="utf-8")
        return None, ""

    canonical_path = _canonical_context_path(project)
    if canonical_path.exists():
        return canonical_path, canonical_path.read_text(encoding="utf-8")

    controlwork_path = _controlwork_context_path(project)
    if controlwork_path.exists():
        return controlwork_path, controlwork_path.read_text(encoding="utf-8")

    legacy_path = _legacy_context_path(project)
    if legacy_path.exists():
        return legacy_path, legacy_path.read_text(encoding="utf-8")

    return None, ""


def _render_context_for_target(source_text: str, source_label: str, target_label: str) -> str:
    if source_label == target_label:
        return source_text
    return source_text.replace(source_label, target_label)


def _capabilities_path(project: Path) -> Path:
    return _control_plane_read_path(project, "capabilities.json")


def _normalize_capability(raw: dict) -> dict:
    capability = raw if isinstance(raw, dict) else {}
    return {
        "id": str(capability.get("id", "")).strip(),
        "label": str(capability.get("label", "")).strip(),
        "state": str(capability.get("state", "")).strip(),
        "control_level": str(capability.get("control_level", "")).strip(),
        "hosts": [
            str(host).strip()
            for host in capability.get("hosts", [])
            if str(host).strip()
        ] if isinstance(capability.get("hosts", []), list) else [],
        "commands": [
            str(command).strip()
            for command in capability.get("commands", [])
            if str(command).strip()
        ] if isinstance(capability.get("commands", []), list) else [],
        "evidence": [
            str(evidence).strip()
            for evidence in capability.get("evidence", [])
            if str(evidence).strip()
        ] if isinstance(capability.get("evidence", []), list) else [],
    }


def _load_capability_registry(project: Path) -> tuple[list[dict], str, str]:
    registry_path = _capabilities_path(project)
    if registry_path.exists():
        try:
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return [], "invalid", str(registry_path)
        if isinstance(raw, dict):
            raw_capabilities = raw.get("capabilities", [])
        else:
            raw_capabilities = raw
        if not isinstance(raw_capabilities, list):
            return [], "invalid", str(registry_path)
        return [_normalize_capability(item) for item in raw_capabilities], "project", str(registry_path)
    return [_normalize_capability(item) for item in _CORE_CAPABILITY_REGISTRY], "builtin", "built-in Core registry"


def _truth_command_route_exists(command: str) -> bool:
    tokens = tuple(shlex.split(str(command).strip()))
    if not tokens:
        return False
    return tokens in _ROUTED_CLI_COMMANDS


def _truth_extract_route_from_tokens(tokens: list[str]) -> tuple[str, ...] | None:
    if not tokens:
        return None

    start = -1
    for index, token in enumerate(tokens):
        normalized = token.replace("\\", "/").strip().strip("\"'")
        basename = normalized.rsplit("/", 1)[-1]
        if basename in _TRUTH_CLI_ENTRYPOINTS:
            start = index + 1
            break

    if start < 0:
        return None

    route = []
    for token in tokens[start:]:
        cleaned = str(token).strip().strip("\"'")
        if not cleaned:
            continue
        if cleaned in {"--project-root", "--json", "--force", "--host", "--output", "--pack"}:
            break
        if cleaned.startswith("-"):
            break
        if cleaned in {"|", ">", ">>", "&&", ";"}:
            break
        if cleaned == "--":
            break
        route.append(cleaned)
        if (route_tuple := tuple(route)) in _ROUTED_CLI_COMMANDS:
            next_token = str(tokens[start + len(route)]).strip().strip("\"'") if start + len(route) < len(tokens) else ""
            candidate = tuple(route + [next_token]) if next_token else ()
            if candidate in _ROUTED_CLI_COMMANDS or any(len(item) > len(route_tuple) and item[:len(route_tuple)] == route_tuple for item in _ROUTED_CLI_COMMANDS):
                continue
            if not next_token or next_token.startswith("-") or next_token in {"|", ">", ">>", "&&", ";", "--"} or len(route_tuple) >= 2 or route_tuple in {("init-module",), ("install",)}:
                return route_tuple

    if route:
        return tuple(route)
    return None


def _truth_route_exists(route: tuple[str, ...]) -> bool:
    return route in _ROUTED_CLI_COMMANDS


def _truth_extract_doc_command(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    for prefix in ("$ ", "> "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].strip()
    if stripped.lower().startswith("ps "):
        marker = "> "
        if marker in stripped:
            stripped = stripped.split(marker, 1)[1].strip()
    if stripped.startswith("#"):
        return ""
    if " # " in stripped:
        stripped = stripped.split(" # ", 1)[0].strip()
    return stripped.rstrip("\\").strip()


_TRUTH_DOC_DIRECT_REFERENCE_PREFIX_PATTERN = re.compile(
    r"^\s*(?:(?:[-+*]|\d+[.)])\s+)?$"
)
_TRUTH_DOC_NON_PUBLIC_REFERENCE_PATTERN = re.compile(
    r"^\s+is\s+no\s+longer\s+a\s+public\s+command\s+surface\.(?=\s|$)",
    re.IGNORECASE,
)
_TRUTH_DOC_LIST_ITEM_PATTERN = re.compile(r"^(?:[-+*]|\d+[.)])\s+")


def _truth_doc_inline_reference_is_explicitly_non_public(
    lines: list[str], index: int, match
) -> bool:
    prefix = lines[index][:match.start()]
    if not _TRUTH_DOC_DIRECT_REFERENCE_PREFIX_PATTERN.fullmatch(prefix):
        return False

    declaration = lines[index][match.end():]
    cursor = index + 1
    while True:
        if _TRUTH_DOC_NON_PUBLIC_REFERENCE_PATTERN.match(declaration):
            return True
        if cursor >= len(lines):
            return False

        continuation = lines[cursor]
        stripped = continuation.strip()
        if (
            not stripped
            or stripped.startswith("```")
            or not continuation[0].isspace()
            or _TRUTH_DOC_LIST_ITEM_PATTERN.match(stripped)
        ):
            return False
        declaration += "\n" + continuation
        cursor += 1


def _truth_extract_doc_commands_from_text(text: str, relative_path: str) -> list[dict]:
    entries = []
    in_fence = False
    fence_lang = ""
    command_pattern = re.compile(
        r"`([^`]*(?:python\s+(?:scripts/)?cc\.py|cc\s+)[^`]*)`"
    )

    lines = text.splitlines()
    for index, line in enumerate(lines):
        line_number = index + 1
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                in_fence = False
                fence_lang = ""
            else:
                in_fence = True
                fence_lang = stripped[3:].strip().lower()
            continue

        candidates = []
        if in_fence and fence_lang in {"", "bash", "sh", "shell", "powershell", "ps1", "text"}:
            command = _truth_extract_doc_command(line)
            if command and (
                command.startswith("cc ")
                or re.match(r"python(?:\d+(?:\.\d+)?)?\s+(?:scripts/)?cc\.py\b", command)
            ):
                candidates.append(command)

        for match in command_pattern.finditer(line):
            if (
                not in_fence
                and _truth_doc_inline_reference_is_explicitly_non_public(
                    lines, index, match
                )
            ):
                continue
            candidates.append(match.group(1).strip())

        for command in candidates:
            try:
                tokens = shlex.split(command)
            except ValueError:
                entries.append({
                    "file": relative_path,
                    "line": line_number,
                    "command": command,
                    "route": [],
                    "state": "unparseable",
                    "detail": "could not parse command with shlex",
                })
                continue
            route = _truth_extract_route_from_tokens(tokens)
            if route is None:
                continue
            entries.append({
                "file": relative_path,
                "line": line_number,
                "command": command,
                "route": list(route),
                "state": "routed" if _truth_route_exists(route) else "missing",
                "detail": "route exists" if _truth_route_exists(route) else "route is not defined in CLI",
            })
    return entries


def _truth_doc_line_context(lines: list[str], index: int, radius: int = 2) -> str:
    start = index
    end = index

    if lines[index].strip().endswith(":"):
        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        while cursor < len(lines) and lines[cursor].strip():
            end = cursor
            cursor += 1
        return " ".join(line.strip() for line in lines[start:end + 1] if line.strip())

    while start > 0 and lines[start - 1].strip():
        start -= 1
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return " ".join(line.strip() for line in lines[start:end + 1] if line.strip())


def _truth_context_has_operational_term(context: str) -> bool:
    normalized = context.lower()
    for term in _TRUTH_OPERATIONAL_CLAIM_CONTEXT_TERMS:
        escaped = re.escape(term.lower())
        if re.search(rf"(?<![a-z0-9_-]){escaped}(?![a-z0-9_-])", normalized):
            return True
    return False


def _truth_extract_control_claims_from_text(text: str, relative_path: str) -> list[dict]:
    claims = []
    lines = text.splitlines()
    in_fence = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue

        terms = {
            match.group(0).lower()
            for match in _TRUTH_STRONG_CONTROL_CLAIM_PATTERN.finditer(stripped)
        }
        if _TRUTH_BLOCKING_CONTROL_CONTEXT_PATTERN.search(stripped):
            terms.update(
                match.group(0).lower()
                for match in _TRUTH_BLOCKING_CONTROL_CLAIM_PATTERN.finditer(stripped)
            )
        if not terms:
            continue

        context = _truth_doc_line_context(lines, index).lower()
        has_operational_context = _truth_context_has_operational_term(context)
        state = "contextualized" if has_operational_context else "unqualified"
        claims.append({
            "file": relative_path,
            "line": index + 1,
            "claim": stripped[:240],
            "terms": sorted(terms),
            "state": state,
            "detail": (
                "strong control claim has operational context"
                if has_operational_context
                else "strong control claim must cite a concrete gate, hook, command, host limit, or verification path"
            ),
        })

    return claims


_TRUTH_LIMITATIONS = [
    "Route recognition does not validate full arguments or preconditions, execute commands, or prove behavior.",
    "Evidence references are declarations; their existence and adequacy are not verified.",
    "Strong-claim checks use text heuristics, not semantic fact verification.",
]


def _truth_check_docs_payload(project: Path,
                              doc_paths: tuple[str, ...] = _TRUTH_DOC_COMMAND_FILES) -> dict:
    entries = []
    control_claims = []
    findings = []
    release_hygiene_findings = []
    for relative_path in doc_paths:
        path = project / relative_path
        if not path.exists():
            findings.append({
                "severity": "warn",
                "file": relative_path,
                "line": 0,
                "command": "",
                "message": "documentation file missing",
            })
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append({
                "severity": "fail",
                "file": relative_path,
                "line": 0,
                "command": "",
                "message": f"could not read documentation file: {exc}",
            })
            continue
        entries.extend(_truth_extract_doc_commands_from_text(text, relative_path))
        control_claims.extend(_truth_extract_control_claims_from_text(text, relative_path))

    seen = set()
    unique_entries = []
    for entry in entries:
        key = (entry["file"], entry["line"], entry["command"])
        if key not in seen:
            seen.add(key)
            unique_entries.append(entry)
            if entry["state"] != "routed":
                findings.append({
                    "severity": "fail",
                    "file": entry["file"],
                    "line": entry["line"],
                    "command": entry["command"],
                    "message": entry["detail"],
                })

    for claim in control_claims:
        if claim["state"] != "contextualized":
            findings.append({
                "severity": "fail",
                "file": claim["file"],
                "line": claim["line"],
                "command": "",
                "claim": claim["claim"],
                "message": claim["detail"],
            })

    if (project / _RELEASE_MANIFEST_FILE).is_file():
        tracked_files, tracked_error = _release_git_tracked_files(project)
        release_manifest = _release_manifest_payload(project, tracked_files, tracked_error)
        if tracked_error:
            findings.append({"severity": "fail", "file": _RELEASE_MANIFEST_FILE, "line": 0,
                             "command": "", "message": f"release hygiene could not enumerate shipped files: {tracked_error}"})
        else:
            release_hygiene_findings = _release_public_hygiene_findings(project, tracked_files, release_manifest)
            for release_finding in release_hygiene_findings:
                relative_path, separator, detail = release_finding.partition(": ")
                findings.append({"severity": "fail", "file": relative_path, "line": 0,
                                 "command": "", "message": detail if separator else release_finding})

    ok_state = not any(finding["severity"] == "fail" for finding in findings)
    return {
        "scope": "structural",
        "limitations": list(_TRUTH_LIMITATIONS),
        "ok": ok_state,
        "docPaths": list(doc_paths),
        "commands": unique_entries,
        "commandCount": len(unique_entries),
        "controlClaims": control_claims,
        "controlClaimCount": len(control_claims),
        "releaseHygieneFindings": release_hygiene_findings,
        "findings": findings,
    }


def _truth_check_payload(project: Path, include_docs: bool = False) -> dict:
    capabilities, registry_source, registry_path = _load_capability_registry(project)
    findings = []
    seen_ids = set()
    summary = {state: 0 for state in sorted(_CAPABILITY_STATES)}
    normalized_capabilities = []

    if registry_source == "invalid":
        findings.append({
            "severity": "fail",
            "capability": "registry",
            "message": f"capability registry is invalid: {registry_path}",
        })

    for index, capability in enumerate(capabilities):
        cap_id = capability["id"]
        state = capability["state"]
        control_level = capability["control_level"]
        normalized_capabilities.append(capability)

        if state in summary:
            summary[state] += 1

        if not cap_id:
            findings.append({
                "severity": "fail",
                "capability": f"index:{index}",
                "message": "capability id is missing",
            })
        elif cap_id in seen_ids:
            findings.append({
                "severity": "fail",
                "capability": cap_id,
                "message": "duplicate capability id",
            })
        else:
            seen_ids.add(cap_id)

        if not capability["label"]:
            findings.append({
                "severity": "fail",
                "capability": cap_id or f"index:{index}",
                "message": "capability label is missing",
            })
        if state not in _CAPABILITY_STATES:
            findings.append({
                "severity": "fail",
                "capability": cap_id or f"index:{index}",
                "message": f"invalid state '{state}'",
            })
        if control_level not in _CAPABILITY_CONTROL_LEVELS:
            findings.append({
                "severity": "fail",
                "capability": cap_id or f"index:{index}",
                "message": f"invalid control_level '{control_level}'",
            })
        if control_level == "mechanical" and not capability["evidence"]:
            findings.append({
                "severity": "fail",
                "capability": cap_id or f"index:{index}",
                "message": "mechanical control claim has no evidence",
            })

        if state in {"shipped", "optional", "experimental"}:
            if not capability["commands"]:
                findings.append({
                    "severity": "warn",
                    "capability": cap_id or f"index:{index}",
                    "message": "implemented capability declares no commands",
                })
            for command in capability["commands"]:
                if not _truth_command_route_exists(command):
                    findings.append({
                        "severity": "fail",
                        "capability": cap_id or f"index:{index}",
                        "message": f"declared command is not routed by the CLI: {command}",
                    })

    docs_payload = _truth_check_docs_payload(project) if include_docs else None
    if docs_payload is not None:
        for finding in docs_payload.get("findings", []):
            findings.append({
                "severity": finding.get("severity", "fail"),
                "capability": "docs",
                "message": (
                    f"{finding.get('file', '')}:{finding.get('line', 0)} "
                    f"{finding.get('command', '')} - {finding.get('message', '')}"
                ).strip(),
            })

    ok_state = not any(finding["severity"] == "fail" for finding in findings)
    return {
        "scope": "structural",
        "limitations": list(_TRUTH_LIMITATIONS),
        "ok": ok_state,
        "registrySource": registry_source,
        "registryPath": registry_path,
        "capabilities": normalized_capabilities,
        "summary": summary,
        "findings": findings,
        "docs": docs_payload,
    }


def _verification_contract_path(project: Path) -> Path:
    return project / VERIFICATION_CONTRACT_FILENAME


def _legacy_verification_contract_path(project: Path) -> Path:
    return _control_plane_read_path(project, "verification.json")


def _verification_receipts_dir(project: Path) -> Path:
    return _control_plane_path(project, VERIFICATION_RECEIPTS_DIRNAME)


def _verification_temp_root(project: Path) -> Path:
    return _control_plane_path(project, VERIFICATION_TEMP_DIRNAME)


def _verification_command_prefix(project: Path) -> str:
    if (project / "scripts" / "cc.py").exists():
        return "python scripts/cc.py"
    return "python /path/to/ControlCoding/scripts/cc.py"


def _default_verification_contract(project: Path) -> dict:
    cc_cmd = _verification_command_prefix(project)
    def pytest_command(targets: str, suite_id: str, temp_name: str) -> str:
        # Per-suite diagnostics must outlive the runner's temporary-directory cleanup.
        # These files are overwritten on rerun; the JSON receipt remains separate.
        return (
            f"python -m pytest {targets} -q --basetemp {{temp}}/{temp_name} "
            f"--junitxml {{project}}/.controlcoding/verification_receipts/{suite_id}.xml "
            "-o junit_logging=all -o junit_log_passing_tests=false"
        )

    pytest_targets = (
        "tests/test_cc_cli.py tests/test_gitignore.py"
        if (project / "tests" / "test_cc_cli.py").exists()
        else "tests"
    )
    if pytest_targets != "tests" and (project / "tests/test_ci_configuration.py").is_file():
        pytest_targets += " tests/test_ci_configuration.py"
    for evidence_test in ("test_cc_evidence.py", "test_cc_evidence_process.py", "test_cc_public_examples.py"):
        if pytest_targets != "tests" and (project / "tests" / evidence_test).is_file():
            pytest_targets += f" tests/{evidence_test}"
    core_regression_targets = (
        "tests/test_cc_memory.py "
        "tests/test_check_boundaries.py "
        "tests/test_check_dangerous_commands.py "
        "tests/test_check_repo_boundaries.py "
        "tests/test_check_file_organization.py "
        "tests/test_cc_organize.py "
        "tests/test_session.py"
        if (project / "tests" / "test_cc_memory.py").exists()
        else ""
    )
    suites = [
        {
            "id": "docs-maintenance",
            "kind": "targeted",
            "required": True,
            "command": f"{cc_cmd} docs check --project-root .",
            "description": "Run the release-aware architecture and system-document contract.",
        },
        {
            "id": "truth-docs",
            "kind": "targeted",
            "required": True,
            "command": f"{cc_cmd} truth check-docs --project-root .",
            "description": "Validate public documentation commands and strong control claims.",
        },
        {
            "id": "truth-capabilities",
            "kind": "targeted",
            "required": True,
            "command": f"{cc_cmd} truth check --project-root . --include-docs",
            "description": "Validate capability claims, routed commands, and public docs.",
        },
        {
            "id": "memory-eval-ranking", "kind": "targeted", "required": True,
            "command": f"{cc_cmd} memory eval --project-root . --json",
            "description": "Run the executable MemoryEval ranking fixture and emit scoring evidence.",
        },
        {
            "id": "cli-regression",
            "kind": "regression",
            "required": True,
            "command": pytest_command(pytest_targets, "cli-regression", "pytest"),
            "description": "Run the focused CLI and repository hygiene regression suite.",
        },
    ]
    if core_regression_targets:
        suites.append(
            {
                "id": "core-governance-regression",
                "kind": "regression",
                "required": True,
                "command": pytest_command(
                    core_regression_targets, "core-governance-regression", "pytest-core-governance"
                ),
                "description": (
                    "Run V1/Core memory, hook, session, and organization "
                    "regressions."
                ),
            }
        )
    for suite_id, candidates, description in (
        ("setup-runtime-regression", ("test_cc_setup.py", "test_cc_runtime.py"),
         "Run installation workflow and Core/runtime prerequisite regressions."),
        ("optional-hooks-regression", ("test_new_hooks.py",),
         "Run optional hook and Bash observation safety regressions."),
    ):
        targets = " ".join(f"tests/{name}" for name in candidates
                           if (project / "tests" / name).is_file())
        if targets:
            suites.append({
                "id": suite_id, "kind": "regression", "required": True,
                "command": pytest_command(targets, suite_id, f"pytest-{suite_id}"),
                "description": description,
            })
    suites.extend(
        [
            {
                "id": "invariant-manifest",
                "kind": "invariant",
                "required": True,
                "command": f"{cc_cmd} invariants run --project-root .",
                "description": "Run executable project invariants declared in controlcoding.invariants.json.",
            },
            {
                "id": "diff-check",
                "kind": "smoke",
                "required": False,
                "command": "git diff --check",
                "description": "Check staged and unstaged diff whitespace.",
            },
        ]
    )
    return {
        "schemaVersion": VERIFICATION_CONTRACT_SCHEMA_VERSION,
        "requiredKinds": list(_DEFAULT_REQUIRED_VERIFICATION_KINDS),
        "suites": suites,
    }


def _normalize_verification_suite(raw: dict) -> dict:
    suite = raw if isinstance(raw, dict) else {}
    return {
        "id": str(suite.get("id", "")).strip(),
        "kind": str(suite.get("kind", "")).strip(),
        "required": bool(suite.get("required", False)),
        "command": str(suite.get("command", "")).strip(),
        "description": str(suite.get("description", "")).strip(),
        "timeoutSeconds": int(suite.get("timeoutSeconds", 300) or 300),
        "when": [
            str(item).strip()
            for item in suite.get("when", [])
            if str(item).strip()
        ] if isinstance(suite.get("when", []), list) else [],
    }


def _evidence_prepare(project: Path, kind: str) -> dict:
    """Read exact contract bytes once for selection and execution identity."""
    path = project / (VERIFICATION_CONTRACT_FILENAME if kind == "verification" else INVARIANT_MANIFEST_FILENAME)
    try:
        contract, descriptor = cc_evidence_inputs.read_contract(project, kind)
        contracts = [descriptor]
        other = {}
        if kind == "verification":
            other, invariant_descriptor = cc_evidence_inputs.read_contract(project, "invariants")
            contracts.append(invariant_descriptor)
        policy = cc_evidence_inputs.input_policy(contract, other)
        collector = _collect_verification_contract_issues if kind == "verification" else _collect_invariant_manifest_issues
        normalize = _normalize_verification_suite if kind == "verification" else _normalize_invariant
        issues = collector(contract)
        raw = contract.get("suites" if kind == "verification" else "invariants", [])
        entries = [normalize(e) for e in raw] if isinstance(raw, list) else []
        for entry in entries:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", entry["id"]):
                issues.append("invalid check identifier")
            if len(entry["command"]) > 16384:
                issues.append("command exceeds evidence limit")
            shlex.split(entry["command"])
        if len(entries) > 1000:
            issues.append("too many checks")
        required = [e["id"] for e in entries if (e["required"] if kind == "verification" else e["status"] == "active" and e["command"])]
        executable = entries if kind == "verification" else [e for e in entries if e["command"]]
        source = "missing" if descriptor["sha256"] is None else ("local" if descriptor["path"].startswith(".") else "project")
        return {"contract": contract, "contracts": contracts, "policy": policy, "entries": entries,
                "executable": executable, "required": required, "issues": issues,
                "source": source, "path": project / descriptor["path"]}
    except (OSError, ValueError, TypeError, RecursionError, cc_evidence_inputs.EvidenceError):
        return {"contract": {}, "contracts": [], "policy": {}, "entries": [], "executable": [],
                "required": [], "issues": ["contract could not be safely validated"], "source": "invalid", "path": path}


def _evidence_history(project: Path, kind: str, prepared: dict) -> dict:
    if prepared["issues"]:
        return {"assessment": {"state": "unknown", "currentRequiredPass": False,
                               "reasons": ["contract_invalid"]}, "latestReceipts": [], "latest": None}
    return cc_evidence.summarize_attempts(
        project, kind, policy=prepared["policy"], contracts=prepared["contracts"],
        entries=prepared["executable"], required=prepared["required"], engine_dir=Path(__file__).parent,
    )


def _evidence_public_entries(entries: list[dict]) -> list[dict]:
    """Expose command presence and identity without copying command credentials."""
    return [{**entry, "command": "[omitted]" if entry["command"] else "",
             "commandOmitted": bool(entry["command"]),
             "commandTemplateDigest": cc_evidence_inputs.digest(entry["command"])}
            for entry in entries]


def _evidence_run(project: Path, kind: str, ids: list, kinds: list, all_entries: bool,
                  json_output: bool, domains: list | None = None) -> int:
    prepared = _evidence_prepare(project, kind)
    issues = prepared["issues"]
    failure = "invalid_contract" if kind == "verification" else "invalid_manifest"
    selected = []
    if not issues:
        if kind == "verification":
            selected, issues = _select_verification_suites(prepared["contract"], ids, kinds, all_entries)
        else:
            selected, issues = _select_invariants(prepared["contract"], ids, domains or [], kinds, all_entries)
        failure = "selection_failed"
        if not selected and not issues:
            issues = ["no executable checks selected"]
    if issues:
        payload, code = {"ok": False, "status": failure, "issues": issues}, 1
    else:
        payload, code = cc_evidence.execute(
            project, kind, prepared["executable"], selected, prepared["required"],
            cc_evidence.normalize_requested(kind, ids, kinds, domains or [], all_entries, selected),
            prepared["policy"], prepared["contracts"], engine_dir=Path(__file__).parent,
        )
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        (ok if code == 0 else fail)(f"{kind}: {payload['status']}")
        if payload.get("receiptPath"):
            info(f"Receipt: {payload['receiptPath']}")
        for issue in payload.get("issues", []):
            fail(issue)
    return code


def _load_verification_contract(project: Path) -> tuple[dict, str, Path]:
    prepared = _evidence_prepare(project, "verification")
    return prepared["contract"], prepared["source"], prepared["path"]

def _collect_verification_contract_issues(policy: dict) -> list[str]:
    issues = []
    if not policy:
        return ["verification contract is missing"]

    if policy.get("schemaVersion") != VERIFICATION_CONTRACT_SCHEMA_VERSION:
        issues.append(f"schemaVersion must be {VERIFICATION_CONTRACT_SCHEMA_VERSION}")

    raw_required_kinds = policy.get("requiredKinds", list(_DEFAULT_REQUIRED_VERIFICATION_KINDS))
    if not isinstance(raw_required_kinds, list):
        issues.append("requiredKinds must be a list")
        required_kinds = list(_DEFAULT_REQUIRED_VERIFICATION_KINDS)
    else:
        required_kinds = [str(kind).strip() for kind in raw_required_kinds if str(kind).strip()]
    for kind in required_kinds:
        if kind not in _VERIFICATION_KINDS:
            issues.append(f"requiredKinds contains invalid kind: {kind}")

    raw_suites = policy.get("suites", [])
    if not isinstance(raw_suites, list) or not raw_suites:
        issues.append("suites must be a non-empty list")
        return issues

    seen_ids = set()
    required_kinds_present = set()
    for index, raw_suite in enumerate(raw_suites):
        suite = _normalize_verification_suite(raw_suite)
        suite_id = suite["id"] or f"index:{index}"
        if not suite["id"]:
            issues.append(f"{suite_id}: id is missing")
        elif suite["id"] in seen_ids:
            issues.append(f"{suite_id}: duplicate id")
        else:
            seen_ids.add(suite["id"])
        if suite["kind"] not in _VERIFICATION_KINDS:
            issues.append(f"{suite_id}: invalid kind '{suite['kind']}'")
        if not suite["command"]:
            issues.append(f"{suite_id}: command is missing")
        if suite["required"] and suite["kind"] in _VERIFICATION_KINDS:
            required_kinds_present.add(suite["kind"])
        if suite["timeoutSeconds"] <= 0:
            issues.append(f"{suite_id}: timeoutSeconds must be positive")

    for kind in required_kinds:
        if kind not in required_kinds_present:
            issues.append(f"required kind has no required suite: {kind}")
    return issues


def _verification_status_payload(project: Path) -> dict:
    prepared = _evidence_prepare(project, "verification")
    policy = prepared["contract"]
    source, path = prepared["source"], prepared["path"]
    suites = prepared["entries"]
    issues = prepared["issues"]
    summary = {kind: 0 for kind in sorted(_VERIFICATION_KINDS)}
    required_summary = {kind: 0 for kind in sorted(_VERIFICATION_KINDS)}
    for suite in suites:
        kind = suite["kind"]
        if kind in summary:
            summary[kind] += 1
            if suite["required"]:
                required_summary[kind] += 1

    evidence = _evidence_history(project, "verification", prepared)
    receipts = evidence["latestReceipts"]

    return {
        "ok": not issues,
        "contractValid": not issues,
        "evidence": evidence,
        "source": source,
        "path": _project_relative_label(project, path),
        "schemaVersion": policy.get("schemaVersion"),
        "requiredKinds": policy.get("requiredKinds", list(_DEFAULT_REQUIRED_VERIFICATION_KINDS)),
        "suites": _evidence_public_entries(suites),
        "summary": summary,
        "requiredSummary": required_summary,
        "issues": issues,
        "latestReceipts": receipts,
    }


def _select_verification_suites(policy: dict,
                                suite_ids: list[str],
                                kinds: list[str],
                                all_suites: bool) -> tuple[list[dict], list[str]]:
    suites = [
        _normalize_verification_suite(item)
        for item in policy.get("suites", [])
        if isinstance(policy.get("suites", []), list)
    ]
    issues = []
    if all_suites:
        return suites, issues

    if suite_ids:
        requested = set(suite_ids)
        selected = [suite for suite in suites if suite["id"] in requested]
        found = {suite["id"] for suite in selected}
        for missing in sorted(requested - found):
            issues.append(f"unknown verification suite: {missing}")
        return selected, issues

    if kinds:
        invalid = [kind for kind in kinds if kind not in _VERIFICATION_KINDS]
        for kind in invalid:
            issues.append(f"invalid verification kind: {kind}")
        valid = set(kinds) - set(invalid)
        return [suite for suite in suites if suite["kind"] in valid], issues

    return [suite for suite in suites if suite["required"]], issues


def cmd_verify_init(project: Path, force: bool = False, json_output: bool = False) -> int:
    """Create a tracked verification contract for targeted/regression checks."""
    path = _verification_contract_path(project)
    if path.exists() and not force:
        if json_output:
            print(json.dumps({
                "ok": False,
                "path": _project_relative_label(project, path),
                "message": "verification contract already exists",
            }, indent=2))
        else:
            fail(f"{VERIFICATION_CONTRACT_FILENAME} already exists (use --force to overwrite)")
        return 1

    policy = _default_verification_contract(project)
    _write_json_atomic(path, policy)
    payload = _verification_status_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        ok(f"Created {VERIFICATION_CONTRACT_FILENAME}")
        info("Run `cc verify status` to inspect it and `cc verify run` after changes.")
    return 0


def cmd_verify_status(project: Path, json_output: bool = False, require_current: bool = False) -> int:
    """Validate configuration; optionally require a complete current pass."""
    payload = _verification_status_payload(project)
    assessment = payload["evidence"]["assessment"]
    passed = payload["ok"] and (not require_current or assessment["currentRequiredPass"])
    payload["requireCurrent"] = require_current
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        (ok if payload["ok"] else fail)("Verification contract is " + ("valid" if payload["ok"] else "invalid"))
        info("Evidence: " + assessment["state"] + "; current required pass=" + str(assessment["currentRequiredPass"]).lower())
        for issue in payload["issues"] + assessment["reasons"]:
            info(issue)
    return 0 if passed else 1

def cmd_verify_run(project: Path, suite_ids: list[str] | None = None,
                   kinds: list[str] | None = None, all_suites: bool = False,
                   json_output: bool = False) -> int:
    """Execute checks with bound v2 evidence; a subset is not a required pass."""
    return _evidence_run(project, "verification", suite_ids or [], kinds or [], all_suites, json_output)

def _invariant_manifest_path(project: Path) -> Path:
    return project / INVARIANT_MANIFEST_FILENAME


def _invariant_receipts_dir(project: Path) -> Path:
    return _control_plane_path(project, INVARIANT_RECEIPTS_DIRNAME)


def _invariant_temp_root(project: Path) -> Path:
    return _control_plane_path(project, INVARIANT_TEMP_DIRNAME)


def _invariant_domain_key(domain: str) -> str:
    raw = str(domain or "generic").strip().lower()
    normalized = re.sub(r"[\s_]+", "-", raw)
    return _INVARIANT_DOMAIN_ALIASES.get(normalized, normalized if normalized else "generic")


def _invariant_candidate_test_path(candidate_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", candidate_id.strip().lower()).strip("_")
    return f"tests/invariants/test_invariant_{slug or 'property'}.py"


def _invariant_elicitation_payload(domain: str) -> dict:
    domain_key = _invariant_domain_key(domain)
    pack = _INVARIANT_ELICITATION_PACKS.get(domain_key, _INVARIANT_ELICITATION_PACKS["generic"])
    candidates = []
    for raw in pack["candidates"]:
        candidate_id = raw["id"]
        test_path = _invariant_candidate_test_path(candidate_id)
        candidates.append({
            "id": candidate_id,
            "title": raw["title"],
            "domain": domain_key,
            "kind": raw["kind"],
            "severity": "blocking",
            "status": "draft",
            "property": raw["property"],
            "threshold": raw["threshold"],
            "suggestedTestPath": test_path,
            "suggestedCommand": f"python -m pytest {test_path} -q",
            "evidence": [test_path],
        })
    return {
        "ok": True,
        "domain": domain_key,
        "label": pack["label"],
        "questions": list(pack["questions"]),
        "candidates": candidates,
        "nextSteps": [
            "Answer the elicitation questions with project-specific facts.",
            "Select two to five candidate properties that would catch real damage.",
            "Create executable tests under tests/invariants/.",
            "Add only executable blocking invariants to the manifest as active.",
            "Run cc invariants run and then cc verify run.",
        ],
    }


def _resolve_invariant_elicitation_output(project: Path,
                                          domain_key: str,
                                          output_path: Path | None) -> tuple[Path, str, list[str]]:
    if output_path is None:
        candidate = project / "docs" / "invariants" / f"{domain_key}-elicitation.md"
    else:
        candidate = output_path
        if not candidate.is_absolute():
            candidate = project / candidate
    resolved = candidate.resolve()
    project_root = project.resolve()
    try:
        relative = resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved, "", ["output path is outside project root"]
    return resolved, relative, []


def _render_invariant_elicitation_markdown(payload: dict) -> str:
    lines = [
        f"# Invariant Elicitation - {payload['label']}",
        "",
        "This is a working draft. It does not enforce anything until the selected",
        "properties have executable tests and active entries in",
        f"`{INVARIANT_MANIFEST_FILENAME}`.",
        "",
        "## Questions",
        "",
    ]
    for index, question in enumerate(payload["questions"], start=1):
        lines.append(f"{index}. {question}")
    lines.extend(["", "## Candidate Invariants", ""])
    for candidate in payload["candidates"]:
        lines.extend([
            f"### {candidate['title']}",
            "",
            f"- id: `{candidate['id']}`",
            f"- domain: `{candidate['domain']}`",
            f"- kind: `{candidate['kind']}`",
            f"- severity: `{candidate['severity']}`",
            f"- status: `{candidate['status']}`",
            f"- property: {candidate['property']}",
            f"- threshold: {candidate['threshold']}",
            f"- suggested test: `{candidate['suggestedTestPath']}`",
            f"- suggested command: `{candidate['suggestedCommand']}`",
            "",
        ])
    lines.extend(["## Next Steps", ""])
    for index, step in enumerate(payload["nextSteps"], start=1):
        lines.append(f"{index}. {step}")
    lines.append("")
    return "\n".join(lines)


def _default_invariant_ci_command(project: Path) -> str:
    if (project / "scripts" / "cc.py").exists():
        return "python scripts/cc.py invariants run --project-root ."
    return "python -m pytest tests/invariants -q"


def _resolve_invariant_ci_output(project: Path,
                                 output_path: Path | None) -> tuple[Path, str, list[str]]:
    candidate = output_path or (project / ".github" / "workflows" / "controlcoding-invariants.yml")
    if not candidate.is_absolute():
        candidate = project / candidate
    resolved = candidate.resolve()
    project_root = project.resolve()
    try:
        relative = resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved, "", ["output path is outside project root"]
    return resolved, relative, []


def _render_github_invariant_workflow(command: str) -> str:
    return "\n".join([
        "name: ControlCoding Invariants",
        "",
        "on:",
        "  pull_request:",
        "  push:",
        "    branches:",
        "      - master",
        "      - main",
        "",
        "jobs:",
        "  invariants:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - name: Check out repository",
        "        uses: actions/checkout@v4",
        "      - name: Set up Python",
        "        uses: actions/setup-python@v5",
        "        with:",
        "          python-version: '3.13'",
        "      - name: Run ControlCoding invariant gate",
        f"        run: {command}",
        "",
    ])


def _invariant_wire_ci_payload(project: Path,
                               provider: str,
                               command: str,
                               output_path: Path | None) -> dict:
    status_payload = _invariant_status_payload(project)
    provider_key = str(provider or "github-actions").strip().lower()
    selected_command = command.strip() if command.strip() else _default_invariant_ci_command(project)
    target, relative, output_issues = _resolve_invariant_ci_output(project, output_path)
    issues = list(output_issues)
    if provider_key != "github-actions":
        issues.append(f"unsupported CI provider: {provider_key}")
    if status_payload["source"] == "missing":
        issues.append(f"{INVARIANT_MANIFEST_FILENAME} is missing")
    elif not status_payload["ok"]:
        issues.extend(status_payload.get("issues", []))
    executable = status_payload.get("summary", {}).get("executable", 0)
    active_blocking = status_payload.get("summary", {}).get("activeBlocking", 0)
    if status_payload["source"] != "missing" and executable == 0:
        issues.append("no executable invariants are declared")
    workflow = _render_github_invariant_workflow(selected_command)
    return {
        "ok": not issues,
        "provider": provider_key,
        "controlLevel": "mechanical_once_committed_and_ci_enabled",
        "scope": "local_configuration",
        "controlLevelMeaning": "Legacy conditional label; generated configuration does not demonstrate enforcement.",
        "hostedExecution": "unverified",
        "serverEnforcement": "unverified",
        "workflowPath": relative,
        "command": selected_command,
        "manifest": {
            "source": status_payload["source"],
            "ok": status_payload["ok"],
            "activeBlocking": active_blocking,
            "executable": executable,
        },
        "workflow": workflow,
        "issues": issues,
        "written": False,
        "_targetPath": target,
    }


def _default_invariants_for_domain(project: Path, domain: str) -> list[dict]:
    cc_cmd = _verification_command_prefix(project)
    normalized_domain = (domain or "tooling").strip().lower()
    if normalized_domain in {"tooling", "controlcoding", "core"}:
        return [
            {
                "id": "public-doc-command-truth",
                "title": "Public documented commands route to implemented CLI handlers",
                "domain": "trust-contract",
                "kind": "structural",
                "severity": "blocking",
                "status": "active",
                "property": "Every public Core command mentioned in README and onboarding docs must route to an implemented CLI command.",
                "threshold": "0 missing command routes and 0 unqualified strong control claims",
                "command": f"{cc_cmd} truth check-docs --project-root .",
                "evidence": [
                    "README.md",
                    "docs/quick-start.md",
                    "docs/install-controlcoding-on-your-project.md",
                ],
            },
            {
                "id": "capability-truth-contract",
                "title": "Capability states match shipped CLI behavior",
                "domain": "trust-contract",
                "kind": "consistency",
                "severity": "blocking",
                "status": "active",
                "property": "Shipped capabilities must cite routed commands and evidence; planned capabilities must not be described as shipped.",
                "threshold": "0 truth contract findings",
                "command": f"{cc_cmd} truth check --project-root . --include-docs",
                "evidence": [
                    "scripts/cc.py",
                    "README.md",
                    "docs/roadmap-10-10.md",
                ],
            },
            {
                "id": "verification-contract-valid",
                "title": "Required validation suites are declared",
                "domain": "verification",
                "kind": "consistency",
                "severity": "blocking",
                "status": "active",
                "property": "The repository must declare required targeted, regression, and invariant validation suites.",
                "threshold": "verification contract status is valid",
                "command": f"{cc_cmd} verify status --project-root .",
                "evidence": [
                    "controlcoding.verification.json",
                ],
            },
        ]

    templates = {
        "finance": {
            "id": "balance-consistency",
            "property": "Every transaction preserves total debits and credits.",
            "threshold": "0 balance drift",
            "kind": "domain",
        },
        "simulation": {
            "id": "deterministic-replay",
            "property": "The same seed and inputs produce the same output.",
            "threshold": "identical replay hash",
            "kind": "determinism",
        },
        "web": {
            "id": "protected-endpoints-authenticated",
            "property": "Protected endpoints never expose data without authentication.",
            "threshold": "0 unauthenticated protected endpoints",
            "kind": "security",
        },
        "content": {
            "id": "no-orphan-content-links",
            "property": "Every content link resolves to an existing target.",
            "threshold": "0 orphan links",
            "kind": "consistency",
        },
    }
    template = templates.get(normalized_domain, {
        "id": "core-domain-property",
        "property": "A project-specific property that must never break.",
        "threshold": "define a measurable threshold",
        "kind": "domain",
    })
    return [
        {
            "id": template["id"],
            "title": template["id"].replace("-", " ").title(),
            "domain": normalized_domain,
            "kind": template["kind"],
            "severity": "blocking",
            "status": "draft",
            "property": template["property"],
            "threshold": template["threshold"],
            "command": "",
            "evidence": [],
        }
    ]


def _default_invariant_manifest(project: Path, domain: str = "tooling") -> dict:
    invariants = _default_invariants_for_domain(project, domain)
    return {
        "schemaVersion": INVARIANT_SCHEMA_VERSION,
        "projectType": str(domain or "tooling").strip().lower(),
        "domains": sorted({str(item.get("domain", "")).strip() for item in invariants if str(item.get("domain", "")).strip()}),
        "invariants": invariants,
    }


def _normalize_invariant(raw: dict) -> dict:
    invariant = raw if isinstance(raw, dict) else {}
    return {
        "id": str(invariant.get("id", "")).strip(),
        "title": str(invariant.get("title", "")).strip(),
        "domain": str(invariant.get("domain", "")).strip(),
        "kind": str(invariant.get("kind", "")).strip(),
        "severity": str(invariant.get("severity", "blocking")).strip(),
        "status": str(invariant.get("status", "active")).strip(),
        "property": str(invariant.get("property", "")).strip(),
        "threshold": str(invariant.get("threshold", "")).strip(),
        "command": str(invariant.get("command", "")).strip(),
        "evidence": [
            str(item).strip()
            for item in invariant.get("evidence", [])
            if str(item).strip()
        ] if isinstance(invariant.get("evidence", []), list) else [],
        "rationale": str(invariant.get("rationale", "")).strip(),
    }


def _load_invariant_manifest(project: Path) -> tuple[dict, str, Path]:
    prepared = _evidence_prepare(project, "invariants")
    return prepared["contract"], prepared["source"], prepared["path"]

def _collect_invariant_manifest_issues(manifest: dict) -> list[str]:
    issues = []
    if not manifest:
        return ["invariant manifest is missing"]

    if manifest.get("schemaVersion") != INVARIANT_SCHEMA_VERSION:
        issues.append(f"schemaVersion must be {INVARIANT_SCHEMA_VERSION}")

    raw_invariants = manifest.get("invariants", [])
    if not isinstance(raw_invariants, list) or not raw_invariants:
        issues.append("invariants must be a non-empty list")
        return issues

    seen_ids = set()
    for index, raw in enumerate(raw_invariants):
        invariant = _normalize_invariant(raw)
        invariant_id = invariant["id"] or f"index:{index}"
        if not invariant["id"]:
            issues.append(f"{invariant_id}: id is missing")
        elif invariant["id"] in seen_ids:
            issues.append(f"{invariant_id}: duplicate id")
        else:
            seen_ids.add(invariant["id"])

        if not invariant["domain"]:
            issues.append(f"{invariant_id}: domain is missing")
        if invariant["kind"] not in _INVARIANT_KINDS:
            issues.append(f"{invariant_id}: invalid kind '{invariant['kind']}'")
        if invariant["severity"] not in _INVARIANT_SEVERITIES:
            issues.append(f"{invariant_id}: invalid severity '{invariant['severity']}'")
        if invariant["status"] not in _INVARIANT_STATUSES:
            issues.append(f"{invariant_id}: invalid status '{invariant['status']}'")
        if not invariant["property"]:
            issues.append(f"{invariant_id}: property is missing")
        if not invariant["threshold"]:
            issues.append(f"{invariant_id}: threshold is missing")
        if (
            invariant["status"] == "active"
            and invariant["severity"] == "blocking"
            and not invariant["command"]
        ):
            issues.append(f"{invariant_id}: active blocking invariant requires a command")
    return issues


def _invariant_status_payload(project: Path) -> dict:
    prepared = _evidence_prepare(project, "invariants")
    manifest = prepared["contract"]
    source, path = prepared["source"], prepared["path"]
    invariants = prepared["entries"]
    issues = prepared["issues"]
    by_status = {status: 0 for status in sorted(_INVARIANT_STATUSES)}
    by_kind = {kind: 0 for kind in sorted(_INVARIANT_KINDS)}
    by_severity = {severity: 0 for severity in sorted(_INVARIANT_SEVERITIES)}
    executable_count = 0
    active_blocking_count = 0
    for invariant in invariants:
        if invariant["status"] in by_status:
            by_status[invariant["status"]] += 1
        if invariant["kind"] in by_kind:
            by_kind[invariant["kind"]] += 1
        if invariant["severity"] in by_severity:
            by_severity[invariant["severity"]] += 1
        if invariant["status"] == "active" and invariant["command"]:
            executable_count += 1
        if invariant["status"] == "active" and invariant["severity"] == "blocking":
            active_blocking_count += 1

    evidence = _evidence_history(project, "invariants", prepared)
    receipts = evidence["latestReceipts"]

    return {
        "ok": not issues,
        "manifestValid": not issues,
        "evidence": evidence,
        "source": source,
        "path": _project_relative_label(project, path),
        "schemaVersion": manifest.get("schemaVersion"),
        "projectType": manifest.get("projectType", ""),
        "domains": manifest.get("domains", []),
        "invariants": _evidence_public_entries(invariants),
        "summary": {
            "total": len(invariants),
            "activeBlocking": active_blocking_count,
            "executable": executable_count,
            "byStatus": by_status,
            "byKind": by_kind,
            "bySeverity": by_severity,
        },
        "issues": issues,
        "latestReceipts": receipts,
    }


def _select_invariants(manifest: dict,
                       invariant_ids: list[str],
                       domains: list[str],
                       kinds: list[str],
                       all_invariants: bool) -> tuple[list[dict], list[str]]:
    raw_invariants = manifest.get("invariants", [])
    invariants = [
        _normalize_invariant(item)
        for item in raw_invariants
        if isinstance(raw_invariants, list)
    ]
    issues = []
    selected = invariants

    if invariant_ids:
        requested = set(invariant_ids)
        selected = [item for item in selected if item["id"] in requested]
        found = {item["id"] for item in selected}
        for missing in sorted(requested - found):
            issues.append(f"unknown invariant: {missing}")
    if domains:
        requested_domains = set(domains)
        selected = [item for item in selected if item["domain"] in requested_domains]
    if kinds:
        invalid = [kind for kind in kinds if kind not in _INVARIANT_KINDS]
        for kind in invalid:
            issues.append(f"invalid invariant kind: {kind}")
        valid = set(kinds) - set(invalid)
        selected = [item for item in selected if item["kind"] in valid]

    if not all_invariants:
        selected = [
            item for item in selected
            if item["status"] == "active" and item["command"]
        ]
    else:
        selected = [item for item in selected if item["command"]]

    return selected, issues


def cmd_invariants_init(project: Path,
                        domain: str = "tooling",
                        force: bool = False,
                        json_output: bool = False) -> int:
    """Create a tracked invariant manifest."""
    path = _invariant_manifest_path(project)
    if path.exists() and not force:
        if json_output:
            print(json.dumps({
                "ok": False,
                "path": _project_relative_label(project, path),
                "message": "invariant manifest already exists",
            }, indent=2))
        else:
            fail(f"{INVARIANT_MANIFEST_FILENAME} already exists (use --force to overwrite)")
        return 1

    manifest = _default_invariant_manifest(project, domain=domain)
    _write_json_atomic(path, manifest)
    payload = _invariant_status_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        ok(f"Created {INVARIANT_MANIFEST_FILENAME}")
        info("Run `cc invariants status` to inspect it and `cc invariants run` to execute active invariants.")
    return 0


def cmd_invariants_elicit(project: Path,
                          domain: str = "generic",
                          write: bool = False,
                          output_path: Path | None = None,
                          force: bool = False,
                          json_output: bool = False) -> int:
    """Generate domain questions and draft invariant candidates."""
    payload = _invariant_elicitation_payload(domain)
    payload["written"] = False
    payload["outputPath"] = ""
    issues = []

    if write:
        target, relative, issues = _resolve_invariant_elicitation_output(
            project,
            payload["domain"],
            output_path,
        )
        if not issues and target.exists() and not force:
            issues.append(f"output file already exists: {relative}")
        if issues:
            payload["ok"] = False
            payload["issues"] = issues
            payload["outputPath"] = relative
            if json_output:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                for issue in issues:
                    fail(issue)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_invariant_elicitation_markdown(payload), encoding="utf-8")
        payload["written"] = True
        payload["outputPath"] = relative

    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Invariant elicitation: {payload['label']}")
    print("")
    print("Questions")
    for index, question in enumerate(payload["questions"], start=1):
        print(f"{index}. {question}")
    print("")
    print("Candidate invariants")
    for candidate in payload["candidates"]:
        print(
            f"- {candidate['id']} [{candidate['domain']}/{candidate['kind']}, "
            f"{candidate['severity']}, {candidate['status']}]"
        )
        print(f"  Property: {candidate['property']}")
        print(f"  Threshold: {candidate['threshold']}")
        print(f"  Test: {candidate['suggestedTestPath']}")
        print(f"  Command: {candidate['suggestedCommand']}")
    if payload["written"]:
        ok(f"Wrote {payload['outputPath']}")
    else:
        info("Use --write to save this elicitation draft under docs/invariants/.")
    return 0


def cmd_invariants_status(project: Path, json_output: bool = False, require_current: bool = False) -> int:
    """Validate configuration; optionally require a complete current pass."""
    payload = _invariant_status_payload(project)
    assessment = payload["evidence"]["assessment"]
    passed = payload["ok"] and (not require_current or assessment["currentRequiredPass"])
    payload["requireCurrent"] = require_current
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        (ok if payload["ok"] else fail)("Invariant manifest is " + ("valid" if payload["ok"] else "invalid"))
        info("Evidence: " + assessment["state"] + "; current required pass=" + str(assessment["currentRequiredPass"]).lower())
        for issue in payload["issues"] + assessment["reasons"]:
            info(issue)
    return 0 if passed else 1

def cmd_invariants_list(project: Path, json_output: bool = False) -> int:
    """List declared invariants."""
    payload = _invariant_status_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["source"] != "missing" else 1
    if payload["source"] == "missing":
        fail(f"{INVARIANT_MANIFEST_FILENAME} is missing (run `cc invariants init`)")
        return 1
    for invariant in payload["invariants"]:
        command_state = "executable" if invariant["command"] else "documented"
        print(
            f"- {invariant['id']} [{invariant['kind']}, {invariant['severity']}, "
            f"{invariant['status']}, {command_state}]"
        )
        print(f"  {invariant['property']}")
    return 0


def _latest_invariant_receipt(project: Path) -> dict:
    """Return a sanitized, assessed attempt; never fall back to an older pass."""
    return _invariant_status_payload(project)["evidence"]["latest"] or {}

def _invariant_report_payload(project: Path) -> dict:
    status_payload = _invariant_status_payload(project)
    latest_receipt = status_payload["evidence"]["latest"] or {}
    latest_runs: dict[str, dict] = {}
    for entry in latest_receipt.get("invariants", []) if isinstance(latest_receipt.get("invariants"), list) else []:
        if not isinstance(entry, dict):
            continue
        invariant_id = str(entry.get("id", "")).strip()
        if not invariant_id:
            continue
        latest_runs[invariant_id] = {
            "receiptId": str(latest_receipt.get("id", "")).strip(),
            "assessment": latest_receipt.get("assessment"),
            "receiptPath": str(latest_receipt.get("path", "")).strip(),
            "createdAt": str(latest_receipt.get("createdAt", "")).strip(),
            "status": str(entry.get("status", "")).strip(),
            "returnCode": entry.get("returnCode"),
            "durationMs": entry.get("durationMs"),
        }

    protected_properties = []
    for invariant in status_payload.get("invariants", []):
        invariant_id = str(invariant.get("id", "")).strip()
        protected_properties.append({
            "id": invariant_id,
            "title": invariant.get("title", ""),
            "domain": invariant.get("domain", ""),
            "kind": invariant.get("kind", ""),
            "severity": invariant.get("severity", ""),
            "status": invariant.get("status", ""),
            "property": invariant.get("property", ""),
            "threshold": invariant.get("threshold", ""),
            "executable": bool(invariant.get("command")),
            "command": invariant.get("command", ""),
            "evidence": invariant.get("evidence", []),
            "latestRun": latest_runs.get(invariant_id),
        })

    return {
        "evidence": status_payload["evidence"],
        "ok": bool(status_payload.get("ok")),
        "source": status_payload.get("source"),
        "path": status_payload.get("path"),
        "summary": status_payload.get("summary", {}),
        "issues": status_payload.get("issues", []),
        "protectedProperties": protected_properties,
        "latestReceipt": {
            "id": str(latest_receipt.get("id", "")).strip(),
            "path": str(latest_receipt.get("path", "")).strip(),
            "status": str(latest_receipt.get("status", "")).strip(),
            "assessment": latest_receipt.get("assessment"),
            "selection": latest_receipt.get("selection"),
            "createdAt": str(latest_receipt.get("createdAt", "")).strip(),
        } if latest_receipt else None,
    }


def _invariant_ci_status_payload(project: Path) -> dict:
    workflow_dir = project / ".github" / "workflows"
    default_workflow = workflow_dir / "controlcoding-invariants.yml"
    workflow_paths: list[Path] = []
    if default_workflow.exists():
        workflow_paths.append(default_workflow)
    if workflow_dir.is_dir():
        for pattern in ("*.yml", "*.yaml"):
            for workflow_path in sorted(workflow_dir.glob(pattern)):
                if workflow_path not in workflow_paths:
                    workflow_paths.append(workflow_path)

    pattern_labels = {
        "invariants run": "cc invariants run",
        "tests/invariants": "pytest tests/invariants",
        "verify run": "cc verify run",
    }
    matches: list[dict] = []
    matched_patterns: set[str] = set()
    read_issues: list[str] = []
    for workflow_path in workflow_paths:
        try:
            content = workflow_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            read_issues.append(f"{_project_relative_label(project, workflow_path)}: {exc}")
            continue
        lower = content.lower()
        workflow_matches = [
            label for needle, label in pattern_labels.items()
            if needle in lower
        ]
        if workflow_matches:
            matched_patterns.update(workflow_matches)
            matches.append({
                "path": _project_relative_label(project, workflow_path),
                "patterns": workflow_matches,
            })

    tracked_files, tracked_error = _release_git_tracked_files(project)
    tracked_set = set(tracked_files)
    matched_paths = [entry["path"] for entry in matches]
    tracked_matches = [
        path for path in matched_paths
        if path in tracked_set
    ]
    committed = None
    if tracked_error:
        committed = None
    elif matched_paths:
        committed = bool(tracked_matches)

    issues = list(read_issues)
    if workflow_paths and not matches:
        issues.append("CI workflow files exist but none contain recognized invariant command patterns")
    if matches and committed is False:
        issues.append("invariant CI workflow exists but is not tracked by git")

    return {
        "workflowPath": _project_relative_label(project, default_workflow),
        "workflowExists": default_workflow.exists(),
        "workflowCount": len(workflow_paths),
        "scope": "local_configuration",
        "runsInvariantGate": bool(matches),
        "runsInvariantGateMeaning": "Legacy field: recognized command text patterns only; no execution observed.",
        "hostedExecution": "unverified",
        "serverEnforcement": "unverified",
        "matchedWorkflows": matches,
        "matchedPatterns": sorted(matched_patterns),
        "gitTracked": committed,
        "gitTrackingDetail": tracked_error or (
            f"{len(tracked_matches)} tracked matched workflow(s)"
            if matched_paths
            else "no matched workflow"
        ),
        "issues": issues,
    }


def _invariant_doctor_payload(project: Path) -> dict:
    status_payload = _invariant_status_payload(project)
    summary = status_payload.get("summary", {}) if isinstance(status_payload.get("summary"), dict) else {}
    executable_count = int(summary.get("executable", 0) or 0)
    active_blocking_count = int(summary.get("activeBlocking", 0) or 0)
    ci_payload = _invariant_ci_status_payload(project)
    latest_receipt = status_payload["evidence"]["latest"] or {}

    issues = list(status_payload.get("issues", []))
    recommendations: list[str] = []
    state = "missing"
    control_level = "unavailable"

    if status_payload.get("source") == "missing":
        state = "missing"
        recommendations.append("Run `cc invariants init --domain <domain>`.")
    elif not status_payload.get("ok"):
        state = "invalid"
        recommendations.append("Fix the invariant manifest before relying on invariant protection.")
    elif active_blocking_count == 0:
        state = "documented_only"
        control_level = "advisory"
        issues.append("no active blocking invariants are declared")
        recommendations.append("Promote selected draft properties to active blocking entries with executable commands.")
    elif executable_count == 0:
        state = "documented_only"
        control_level = "advisory"
        issues.append("no executable active invariants are declared")
        recommendations.append("Add commands to active blocking invariant entries.")
    elif ci_payload.get("runsInvariantGate") and ci_payload.get("gitTracked") is not False:
        state = "ci_wired"
        control_level = "mechanical_once_committed_and_ci_enabled"
        recommendations.append("Confirm the repository host runs the workflow on pull requests and protected branches.")
    else:
        state = "local_executable"
        control_level = "conditional"
        recommendations.append("Prepare and review `cc invariants wire-ci --write`; separately verify hosted execution and required server checks.")

    if ci_payload.get("issues"):
        issues.extend(ci_payload["issues"])
    if not latest_receipt and executable_count:
        recommendations.append("Run `cc invariants run` to create local execution evidence.")

    latest_summary = None
    if latest_receipt:
        latest_summary = {
            "id": str(latest_receipt.get("id", "")).strip(),
            "path": str(latest_receipt.get("path", "")).strip(),
            "status": str(latest_receipt.get("status", "")).strip(),
            "assessment": latest_receipt.get("assessment"),
            "selection": latest_receipt.get("selection"),
            "createdAt": str(latest_receipt.get("createdAt", "")).strip(),
            "invariantCount": len(latest_receipt.get("invariants", []))
            if isinstance(latest_receipt.get("invariants"), list)
            else 0,
        }

    return {
        "evidence": status_payload["evidence"],
        "ok": state in {"local_executable", "ci_wired"},
        "state": state,
        "controlLevel": control_level,
        "scope": "local_configuration",
        "stateMeaning": "Legacy state/controlLevel describe manifest and CI text configuration, not observed enforcement.",
        "manifest": {
            "source": status_payload.get("source"),
            "path": status_payload.get("path"),
            "ok": bool(status_payload.get("ok")),
        },
        "summary": summary,
        "ci": ci_payload,
        "latestReceipt": latest_summary,
        "issues": sorted(set(issues)),
        "recommendations": recommendations,
    }


def cmd_invariants_report(project: Path, json_output: bool = False) -> int:
    """Report protected invariant properties and latest execution evidence."""
    payload = _invariant_report_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] and payload["source"] != "missing" else 1

    print("Invariant report")
    if payload["source"] == "missing":
        fail(f"{INVARIANT_MANIFEST_FILENAME} is missing (run `cc invariants init`)")
        return 1
    if payload["ok"]:
        summary = payload["summary"]
        ok(
            f"{summary.get('total', 0)} invariant(s), "
            f"{summary.get('executable', 0)} executable, "
            f"{summary.get('activeBlocking', 0)} active blocking"
        )
    else:
        fail("Invariant manifest is invalid")
        for issue in payload["issues"]:
            fail(issue)

    latest = payload.get("latestReceipt")
    if latest:
        info(
            "Latest receipt: "
            f"{latest.get('id')} ({latest.get('status')}) at {latest.get('path')}"
        )
    else:
        info("Latest receipt: none")

    assessment = payload["evidence"]["assessment"]
    info(f"Evidence: {assessment['state']}; current required pass={assessment['currentRequiredPass']}")
    for invariant in payload["protectedProperties"]:
        command_state = "executable" if invariant["executable"] else "documented"
        latest_run = invariant.get("latestRun") or {}
        latest_status = latest_run.get("status", "not-run")
        print(
            f"- {invariant['id']} [{invariant['domain']}/{invariant['kind']}, "
            f"{invariant['severity']}, {invariant['status']}, {command_state}, "
            f"latest={latest_status}]"
        )
        print(f"  Property: {invariant['property']}")
        print(f"  Threshold: {invariant['threshold']}")
    return 0 if payload["ok"] else 1


def cmd_invariants_doctor(project: Path, json_output: bool = False) -> int:
    """Diagnose the operational invariant gate state."""
    payload = _invariant_doctor_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print("Invariant doctor")
    printer = ok if payload["ok"] else fail
    printer(f"State: {payload['state']}")
    info(f"Configured control level (legacy label): {payload['controlLevel']}")
    summary = payload.get("summary", {})
    info(
        "Manifest: "
        f"{summary.get('total', 0)} invariant(s), "
        f"{summary.get('activeBlocking', 0)} active blocking, "
        f"{summary.get('executable', 0)} executable"
    )
    ci_payload = payload.get("ci", {})
    ci_state = "yes" if ci_payload.get("runsInvariantGate") else "no"
    info(f"CI command patterns detected: {ci_state}; hosted execution and server enforcement unverified")
    latest = payload.get("latestReceipt")
    if latest:
        info(f"Latest receipt: {latest.get('id')} ({latest.get('status')})")
    else:
        info("Latest receipt: none")
    assessment = payload["evidence"]["assessment"]
    info(f"Evidence: {assessment['state']}; current required pass={assessment['currentRequiredPass']}")
    for issue in payload.get("issues", []):
        fail(issue)
    for recommendation in payload.get("recommendations", []):
        info(recommendation)
    return 0 if payload["ok"] else 1


def cmd_invariants_wire_ci(project: Path,
                           provider: str = "github-actions",
                           command: str = "",
                           output_path: Path | None = None,
                           write: bool = False,
                           force: bool = False,
                           json_output: bool = False) -> int:
    """Generate or write a CI workflow for the invariant gate."""
    payload = _invariant_wire_ci_payload(project, provider, command, output_path)
    target = payload.pop("_targetPath")
    if payload["ok"] and write:
        if target.exists() and not force:
            payload["ok"] = False
            payload["issues"].append(f"workflow already exists: {payload['workflowPath']}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload["workflow"], encoding="utf-8")
            payload["written"] = True

    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print("Invariant CI wiring (local configuration; hosted execution and server enforcement unverified)")
    if payload["ok"]:
        ok(f"Provider: {payload['provider']}")
        ok(f"Workflow: {payload['workflowPath']}")
        info(f"Command: {payload['command']}")
        info(f"Configured control level (legacy label): {payload['controlLevel']}")
        if payload["written"]:
            ok("Workflow written")
        else:
            info("Dry run only. Add --write to create the workflow file.")
    else:
        fail("Cannot wire invariant CI")
        for issue in payload["issues"]:
            fail(issue)
    return 0 if payload["ok"] else 1


def cmd_invariants_add(project: Path,
                       invariant_id: str,
                       domain: str,
                       property_text: str,
                       kind: str = "domain",
                       severity: str = "blocking",
                       status: str = "active",
                       command: str = "",
                       threshold: str = "",
                       evidence: list[str] | None = None,
                       title: str = "",
                       json_output: bool = False) -> int:
    """Add one invariant to the tracked manifest."""
    evidence = evidence or []
    manifest, source, path = _load_invariant_manifest(project)
    if source == "missing":
        if json_output:
            print(json.dumps({
                "ok": False,
                "message": f"{INVARIANT_MANIFEST_FILENAME} is missing",
            }, indent=2))
        else:
            fail(f"{INVARIANT_MANIFEST_FILENAME} is missing (run `cc invariants init`)")
        return 1

    invariants = manifest.get("invariants", [])
    if not isinstance(invariants, list):
        invariants = []
    if any(_normalize_invariant(item)["id"] == invariant_id for item in invariants):
        if json_output:
            print(json.dumps({
                "ok": False,
                "message": f"invariant already exists: {invariant_id}",
            }, indent=2))
        else:
            fail(f"invariant already exists: {invariant_id}")
        return 1

    invariants.append({
        "id": invariant_id,
        "title": title or invariant_id.replace("-", " ").title(),
        "domain": domain,
        "kind": kind,
        "severity": severity,
        "status": status,
        "property": property_text,
        "threshold": threshold or "defined by invariant command",
        "command": command,
        "evidence": evidence,
    })
    manifest["invariants"] = invariants
    domains = {
        str(item.get("domain", "")).strip()
        for item in invariants
        if isinstance(item, dict) and str(item.get("domain", "")).strip()
    }
    manifest["domains"] = sorted(domains)
    _write_json_atomic(path, manifest)
    payload = _invariant_status_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        ok(f"Added invariant {invariant_id}")
    return 0 if payload["ok"] else 1


def cmd_invariants_run(project: Path, invariant_ids: list[str] | None = None,
                       domains: list[str] | None = None, kinds: list[str] | None = None,
                       all_invariants: bool = False, json_output: bool = False) -> int:
    """Execute active invariants with bound v2 evidence."""
    return _evidence_run(project, "invariants", invariant_ids or [], kinds or [],
                         all_invariants, json_output, domains)

def _promotion_dir(project: Path) -> Path:
    return _control_plane_path(project, PROMOTION_DIRNAME)


def _replacement_dir(project: Path) -> Path:
    return _control_plane_path(project, REPLACEMENT_DIRNAME)


def _promotion_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:60] or "item"


def _promotion_manifest_id(source_relative: str, target_zone: str) -> str:
    stamp = _utc_now_iso().replace(":", "").replace(".", "").replace("Z", "Z")
    slug = _promotion_slug(f"{source_relative}-{target_zone}")
    return f"promotion_{stamp}_{slug}"


def _promotion_manifest_path(project: Path, manifest_id: str) -> Path:
    safe_id = _promotion_slug(manifest_id).replace("-", "_")
    return _promotion_dir(project) / f"{safe_id}.json"


def _replacement_manifest_id(old_relative: str, new_relative: str) -> str:
    stamp = _utc_now_iso().replace(":", "").replace(".", "").replace("Z", "Z")
    slug = _promotion_slug(f"{old_relative}-to-{new_relative}")
    return f"replacement_{stamp}_{slug}"


def _replacement_manifest_path(project: Path, manifest_id: str) -> Path:
    safe_id = _promotion_slug(manifest_id).replace("-", "_")
    return _replacement_dir(project) / f"{safe_id}.json"


def _promotion_resolve_project_path(project: Path, raw_path: str) -> tuple[Path, str, list[str]]:
    project_root = project.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved, "", [f"path is outside project root: {raw_path}"]
    return resolved, relative, []


def _promotion_zone_for_relative(relative_path: str) -> str:
    parts = PurePosixPath(relative_path).parts
    if not parts:
        return "unknown"
    first = parts[0]
    return first if first in _PROMOTION_ZONES else "unknown"


def _promotion_infer_target_zone(source_zone: str) -> str:
    return _PROMOTION_NEXT_ZONE.get(source_zone, "")


def _promotion_target_path(project: Path,
                           source_path: Path,
                           source_relative: str,
                           source_zone: str,
                           target_zone: str,
                           target_path: str = "",
                           feature_name: str = "") -> tuple[Path, str, list[str]]:
    issues: list[str] = []
    if target_path:
        resolved, relative, path_issues = _promotion_resolve_project_path(project, target_path)
        issues.extend(path_issues)
        if relative and _promotion_zone_for_relative(relative) != target_zone:
            issues.append(f"target path must be inside {target_zone}/")
        return resolved, relative, issues

    source_parts = list(PurePosixPath(source_relative).parts)
    suffix_parts = source_parts[1:] if source_zone in _PROMOTION_ZONES else source_parts
    if not suffix_parts:
        suffix_parts = [source_path.name]

    if target_zone == "features":
        feature = _promotion_slug(feature_name or Path(suffix_parts[0]).stem)
        if source_path.is_dir() and len(suffix_parts) == 1:
            target_relative = PurePosixPath("features") / feature
        elif suffix_parts[0] == feature and len(suffix_parts) > 1:
            target_relative = PurePosixPath("features") / feature / PurePosixPath(*suffix_parts[1:])
        else:
            target_relative = PurePosixPath("features") / feature / PurePosixPath(*suffix_parts)
    else:
        target_relative = PurePosixPath(target_zone) / PurePosixPath(*suffix_parts)

    target = project / Path(*target_relative.parts)
    return target, target_relative.as_posix(), issues


def _promotion_policy_payload(project: Path) -> dict:
    path = _control_plane_path(project, "promotion_policy.json")
    defaults = {
        "maxConsumerFanIn": 20,
        "maxRecentChurn": 20,
    }
    if not path.exists():
        return {"source": "default", "path": "", **defaults}
    data = _read_json_object(path)
    policy = {**defaults, **data}
    policy["source"] = "local"
    policy["path"] = _project_relative_label(project, path)
    return policy


def _promotion_text_file_candidates(project: Path) -> list[Path]:
    skipped_dirs = {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        ".controlcoding",
        ".controlwork",
    }
    text_suffixes = {
        "",
        ".adoc",
        ".cfg",
        ".css",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".py",
        ".rst",
        ".toml",
        ".ts",
        ".txt",
        ".yaml",
        ".yml",
    }
    files: list[Path] = []
    for path in project.rglob("*"):
        try:
            relative_parts = path.relative_to(project).parts
        except ValueError:
            continue
        if any(part in skipped_dirs for part in relative_parts):
            continue
        if path.is_file() and path.suffix.lower() in text_suffixes and path.stat().st_size <= 1024 * 1024:
            files.append(path)
    return files


def _promotion_consumer_fan_in(project: Path, source_relative: str) -> tuple[int, list[str]]:
    if not source_relative:
        return 0, []
    needle = source_relative.replace("\\", "/")
    matches: list[str] = []
    for path in _promotion_text_file_candidates(project):
        relative = _project_relative_label(project, path)
        if relative == needle:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle in content.replace("\\", "/"):
            matches.append(relative)
    return len(matches), matches[:20]


def _promotion_recent_churn(project: Path, source_relative: str) -> int:
    if not source_relative:
        return 0
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "log", "--since=30 days", "--format=%H", "--", source_relative],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0:
        return 0
    return len([line for line in (result.stdout or "").splitlines() if line.strip()])


def _promotion_check_payload(project: Path,
                             source_raw: str,
                             target_zone: str = "",
                             target_path: str = "",
                             feature_name: str = "") -> dict:
    source_path, source_relative, issues = _promotion_resolve_project_path(project, source_raw)
    source_zone = _promotion_zone_for_relative(source_relative)
    inferred_target = _promotion_infer_target_zone(source_zone)
    normalized_target = (target_zone or inferred_target).strip().lower()
    warnings: list[str] = []
    deferred_checks: list[str] = []

    if not source_path.exists():
        issues.append(f"source path does not exist: {source_raw}")
    if source_zone == "unknown" and source_relative:
        issues.append(
            "source path is not inside a ControlCoding promotion zone "
            "(workspace/, features/, shared/, stable/)"
        )
    if source_zone == "stable":
        issues.append("stable/ is already the final zone and cannot be promoted further")
    if not normalized_target:
        issues.append("target zone could not be inferred; pass --to features|shared|stable")
    elif normalized_target not in _PROMOTION_ZONES or normalized_target == "workspace":
        issues.append("target zone must be one of: features, shared, stable")
    elif source_zone in _PROMOTION_NEXT_ZONE and _PROMOTION_NEXT_ZONE[source_zone] != normalized_target:
        issues.append(
            f"promotion must follow the staged path: {source_zone} -> "
            f"{_PROMOTION_NEXT_ZONE[source_zone]}"
        )

    target, target_relative, target_issues = _promotion_target_path(
        project,
        source_path,
        source_relative,
        source_zone,
        normalized_target or "shared",
        target_path=target_path,
        feature_name=feature_name,
    )
    issues.extend(target_issues)
    if target.exists():
        issues.append(f"target path already exists: {target_relative}")

    policy = _promotion_policy_payload(project)
    try:
        max_consumer_fan_in = int(policy.get("maxConsumerFanIn", 20))
    except (TypeError, ValueError):
        max_consumer_fan_in = 20
    try:
        max_recent_churn = int(policy.get("maxRecentChurn", 20))
    except (TypeError, ValueError):
        max_recent_churn = 20
    fan_in_count, fan_in_examples = _promotion_consumer_fan_in(project, source_relative)
    recent_churn = _promotion_recent_churn(project, source_relative)
    if fan_in_count > max_consumer_fan_in:
        issues.append(
            f"consumer fan-in threshold exceeded: {fan_in_count} > {max_consumer_fan_in}"
        )
    if recent_churn > max_recent_churn:
        issues.append(
            f"recent churn threshold exceeded: {recent_churn} > {max_recent_churn}"
        )

    verification_status = None
    invariant_status = None
    if normalized_target == "stable":
        verification_status = _verification_status_payload(project)
        invariant_status = _invariant_status_payload(project)
        if not verification_status.get("ok"):
            issues.append("stable promotion requires a valid verification contract")
        if not invariant_status.get("ok"):
            issues.append("stable promotion requires a valid executable invariant manifest")
        warnings.append("apply toward stable/ must generate an ADR with --adr")

    checks = [
        {
            "id": "source_exists",
            "status": "passed" if source_path.exists() else "failed",
            "detail": source_relative or source_raw,
        },
        {
            "id": "known_source_zone",
            "status": "passed" if source_zone in _PROMOTION_ZONES and source_zone != "stable" else "failed",
            "detail": source_zone,
        },
        {
            "id": "staged_target",
            "status": "passed" if (
                source_zone in _PROMOTION_NEXT_ZONE
                and _PROMOTION_NEXT_ZONE[source_zone] == normalized_target
            ) else "failed",
            "detail": f"{source_zone} -> {normalized_target}",
        },
        {
            "id": "target_available",
            "status": "passed" if not target.exists() else "failed",
            "detail": target_relative,
        },
        {
            "id": "consumer_fan_in",
            "status": "passed" if fan_in_count <= max_consumer_fan_in else "failed",
            "detail": f"{fan_in_count} consumer reference(s)",
            "threshold": {"max": max_consumer_fan_in},
            "examples": fan_in_examples,
        },
        {
            "id": "recent_churn",
            "status": "passed" if recent_churn <= max_recent_churn else "failed",
            "detail": f"{recent_churn} commit(s) in the last 30 days",
            "threshold": {"max": max_recent_churn},
        },
        {
            "id": "rollback_simulation",
            "status": "passed",
            "detail": "target path can be left untouched until apply",
        },
    ]
    if normalized_target == "stable":
        checks.extend([
            {
                "id": "verification_contract",
                "status": "passed" if verification_status and verification_status.get("ok") else "failed",
                "detail": "required for stable promotion",
            },
            {
                "id": "invariant_manifest",
                "status": "passed" if invariant_status and invariant_status.get("ok") else "failed",
                "detail": "required for stable promotion",
            },
        ])

    return {
        "ok": not issues,
        "schemaVersion": PROMOTION_SCHEMA_VERSION,
        "sourcePath": source_relative or source_raw,
        "sourceZone": source_zone,
        "targetZone": normalized_target,
        "targetPath": target_relative,
        "policy": policy,
        "checks": checks,
        "issues": issues,
        "warnings": warnings,
        "deferredChecks": deferred_checks,
    }


def _write_promotion_manifest(project: Path, manifest: dict) -> Path:
    manifest_id = str(manifest.get("id", "")).strip() or _promotion_manifest_id(
        str(manifest.get("sourcePath", "item")),
        str(manifest.get("targetZone", "target")),
    )
    manifest["id"] = manifest_id
    manifest["schemaVersion"] = PROMOTION_SCHEMA_VERSION
    path = _promotion_manifest_path(project, manifest_id)
    _write_json_atomic(path, manifest)
    return path


def _load_promotion_manifests(project: Path) -> tuple[list[dict], list[str]]:
    manifests: list[dict] = []
    issues: list[str] = []
    directory = _promotion_dir(project)
    if not directory.exists():
        return manifests, issues
    for path in sorted(directory.glob("*.json")):
        data = _read_json_object(path)
        if not data:
            issues.append(f"{_project_relative_label(project, path)} is not valid JSON")
            continue
        if int(data.get("schemaVersion", 0) or 0) != PROMOTION_SCHEMA_VERSION:
            issues.append(f"{_project_relative_label(project, path)} has unsupported schemaVersion")
        if not str(data.get("sourcePath", "")).strip():
            issues.append(f"{_project_relative_label(project, path)} is missing sourcePath")
        if str(data.get("targetZone", "")).strip() not in _PROMOTION_ZONES:
            issues.append(f"{_project_relative_label(project, path)} has invalid targetZone")
        data["_path"] = _project_relative_label(project, path)
        manifests.append(data)
    return manifests, issues


def _load_replacement_manifests(project: Path) -> tuple[list[dict], list[str]]:
    manifests: list[dict] = []
    issues: list[str] = []
    directory = _replacement_dir(project)
    if not directory.exists():
        return manifests, issues
    for path in sorted(directory.glob("*.json")):
        data = _read_json_object(path)
        if not data:
            issues.append(f"{_project_relative_label(project, path)} is not valid JSON")
            continue
        if not str(data.get("oldPath", "")).strip():
            issues.append(f"{_project_relative_label(project, path)} is missing oldPath")
        if not str(data.get("newPath", "")).strip():
            issues.append(f"{_project_relative_label(project, path)} is missing newPath")
        data["_path"] = _project_relative_label(project, path)
        manifests.append(data)
    return manifests, issues


def _replacement_status_payload(project: Path) -> dict:
    manifests, issues = _load_replacement_manifests(project)
    by_status: dict[str, int] = {}
    for manifest in manifests:
        status = str(manifest.get("status", "unknown")).strip() or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
    latest = manifests[-1] if manifests else None
    return {
        "ok": not issues,
        "source": "local" if manifests else "missing",
        "path": _control_plane_display_path(REPLACEMENT_DIRNAME),
        "count": len(manifests),
        "byStatus": by_status,
        "latest": latest,
        "issues": issues,
    }


def _promotion_status_payload(project: Path) -> dict:
    manifests, issues = _load_promotion_manifests(project)
    replacements = _replacement_status_payload(project)
    issues = issues + [f"replacement: {issue}" for issue in replacements.get("issues", [])]
    by_status: dict[str, int] = {}
    for manifest in manifests:
        status = str(manifest.get("status", "unknown")).strip() or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
    latest = manifests[-1] if manifests else None
    return {
        "ok": not issues,
        "source": "local" if manifests else "missing",
        "path": _control_plane_display_path(PROMOTION_DIRNAME),
        "count": len(manifests),
        "byStatus": by_status,
        "latest": latest,
        "replacements": replacements,
        "issues": issues,
    }


def _write_promotion_adr(project: Path, manifest: dict, reason: str = "") -> Path:
    source = str(manifest.get("sourcePath", "")).strip()
    target = str(manifest.get("targetPath", "")).strip()
    slug = _promotion_slug(f"promote-{Path(target).stem or Path(source).stem}")
    date = datetime.date.today().isoformat()
    directory = project / "docs" / "adr"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"ADR-{date}-{slug}.md"
    if path.exists():
        for index in range(2, 100):
            candidate = directory / f"ADR-{date}-{slug}-{index}.md"
            if not candidate.exists():
                path = candidate
                break
    rationale = reason.strip() or "Promotion approved after ControlCoding promotion checks."
    content = (
        f"# ADR: Promote {target} to stable\n\n"
        "**Status**: Accepted\n"
        f"**Date**: {date}\n\n"
        "## Context\n\n"
        f"`{source}` is being promoted through the ControlCoding maturity path.\n"
        "Promotion toward `stable/` requires an explicit architecture decision record.\n\n"
        "## Decision\n\n"
        f"Promote the component to `{target}`.\n\n"
        "## Rationale\n\n"
        f"{rationale}\n\n"
        "## Promotion Evidence\n\n"
        f"- Source: `{source}`\n"
        f"- Target: `{target}`\n"
        "- Verification contract: required before apply\n"
        "- Invariant manifest: required before apply\n\n"
        "## Consequences\n\n"
        "- The promoted component is now treated as a foundation-level artifact.\n"
        "- Future changes should go through explicit review and verification.\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def cmd_promote_plan(project: Path,
                     source_path: str,
                     target_zone: str = "",
                     target_path: str = "",
                     feature_name: str = "",
                     reason: str = "",
                     json_output: bool = False) -> int:
    payload = _promotion_check_payload(
        project,
        source_path,
        target_zone=target_zone,
        target_path=target_path,
        feature_name=feature_name,
    )
    manifest = {
        "id": _promotion_manifest_id(payload["sourcePath"], payload["targetZone"] or "target"),
        "createdAt": _utc_now_iso(),
        "status": "planned" if payload["ok"] else "blocked",
        "reason": reason.strip(),
        **payload,
    }
    manifest_path = _write_promotion_manifest(project, manifest)
    result = {
        **payload,
        "status": manifest["status"],
        "manifestId": manifest["id"],
        "manifestPath": _project_relative_label(project, manifest_path),
    }
    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        printer = ok if payload["ok"] else fail
        printer(f"Promotion plan {manifest['status']}: {payload['sourcePath']} -> {payload['targetPath']}")
        info(f"Manifest: {_project_relative_label(project, manifest_path)}")
        for issue in payload["issues"]:
            fail(issue)
    return 0 if payload["ok"] else 1


def cmd_promote_check(project: Path,
                      source_path: str,
                      target_zone: str = "",
                      target_path: str = "",
                      feature_name: str = "",
                      json_output: bool = False) -> int:
    payload = _promotion_check_payload(
        project,
        source_path,
        target_zone=target_zone,
        target_path=target_path,
        feature_name=feature_name,
    )
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if payload["ok"]:
            ok(f"Promotion check passed: {payload['sourcePath']} -> {payload['targetPath']}")
        else:
            fail("Promotion check failed")
            for issue in payload["issues"]:
                fail(issue)
        for warning in payload["warnings"]:
            warn(warning)
    return 0 if payload["ok"] else 1


def cmd_promote_apply(project: Path,
                      source_path: str,
                      target_zone: str,
                      target_path: str = "",
                      feature_name: str = "",
                      reason: str = "",
                      create_adr: bool = False,
                      json_output: bool = False) -> int:
    payload = _promotion_check_payload(
        project,
        source_path,
        target_zone=target_zone,
        target_path=target_path,
        feature_name=feature_name,
    )
    if payload["targetZone"] == "stable" and not create_adr:
        payload["ok"] = False
        payload["issues"].append("stable promotion requires --adr")
    if not payload["ok"]:
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            fail("Promotion apply blocked")
            for issue in payload["issues"]:
                fail(issue)
        return 1

    source_abs = project / Path(*PurePosixPath(payload["sourcePath"]).parts)
    target_abs = project / Path(*PurePosixPath(payload["targetPath"]).parts)
    target_abs.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_abs), str(target_abs))

    manifest = {
        "id": _promotion_manifest_id(payload["sourcePath"], payload["targetZone"]),
        "createdAt": _utc_now_iso(),
        "appliedAt": _utc_now_iso(),
        "status": "applied",
        "reason": reason.strip(),
        **payload,
    }
    adr_path = None
    if payload["targetZone"] == "stable":
        adr_path = _write_promotion_adr(project, manifest, reason=reason)
        manifest["adrPath"] = _project_relative_label(project, adr_path)
    manifest_path = _write_promotion_manifest(project, manifest)
    result = {
        **payload,
        "status": "applied",
        "manifestId": manifest["id"],
        "manifestPath": _project_relative_label(project, manifest_path),
        "adrPath": _project_relative_label(project, adr_path) if adr_path else "",
    }
    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        ok(f"Promoted {payload['sourcePath']} -> {payload['targetPath']}")
        info(f"Manifest: {result['manifestPath']}")
        if result["adrPath"]:
            info(f"ADR: {result['adrPath']}")
    return 0


def cmd_replace_start(project: Path,
                      old_path: str,
                      new_path: str,
                      reason: str = "",
                      json_output: bool = False) -> int:
    old_resolved, old_relative, old_issues = _promotion_resolve_project_path(project, old_path)
    new_resolved, new_relative, new_issues = _promotion_resolve_project_path(project, new_path)
    issues = old_issues + new_issues
    if old_relative and not old_resolved.exists():
        issues.append(f"old path does not exist: {old_relative}")
    if new_relative and not new_resolved.exists():
        issues.append(f"new path does not exist: {new_relative}")
    if issues:
        payload = {"ok": False, "issues": issues}
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            fail("Replacement start blocked")
            for issue in issues:
                fail(issue)
        return 1
    manifest_id = _replacement_manifest_id(old_relative, new_relative)
    manifest = {
        "id": manifest_id,
        "schemaVersion": PROMOTION_SCHEMA_VERSION,
        "createdAt": _utc_now_iso(),
        "status": "active",
        "oldPath": old_relative,
        "newPath": new_relative,
        "reason": reason.strip(),
    }
    manifest_path = _replacement_manifest_path(project, manifest_id)
    _write_json_atomic(manifest_path, manifest)
    payload = {
        "ok": True,
        "status": "active",
        "manifestId": manifest_id,
        "manifestPath": _project_relative_label(project, manifest_path),
        "oldPath": old_relative,
        "newPath": new_relative,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        ok(f"Replacement started: {old_relative} -> {new_relative}")
        info(f"Manifest: {payload['manifestPath']}")
    return 0


def cmd_replace_status(project: Path, json_output: bool = False) -> int:
    payload = _replacement_status_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1
    print("Replacement manifests")
    print(f"Count: {payload['count']}")
    for status, count in sorted(payload["byStatus"].items()):
        print(f"- {status}: {count}")
    for issue in payload["issues"]:
        fail(issue)
    return 0 if payload["ok"] else 1


def cmd_replace_complete(project: Path,
                         manifest_id: str,
                         json_output: bool = False) -> int:
    normalized_id = str(manifest_id or "").strip()
    if not normalized_id:
        payload = {"ok": False, "error": "manifest id is required"}
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            fail(payload["error"])
        return 1
    path = _replacement_manifest_path(project, normalized_id)
    manifest = _read_json_object(path)
    if not manifest:
        payload = {"ok": False, "error": f"replacement manifest not found: {normalized_id}"}
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            fail(payload["error"])
        return 1
    manifest["status"] = "completed"
    manifest["completedAt"] = _utc_now_iso()
    _write_json_atomic(path, manifest)
    payload = {
        "ok": True,
        "status": "completed",
        "manifestId": normalized_id,
        "manifestPath": _project_relative_label(project, path),
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        ok(f"Replacement completed: {normalized_id}")
    return 0


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _parse_iso_timestamp(value: str) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _surface_lock_path(project: Path) -> Path:
    return project / SURFACE_LOCK_RELATIVE_PATH


def _normalize_enabled_hosts(raw_hosts, primary_host: str = "") -> list[str]:
    hosts: list[str] = []

    def _append(host_value) -> None:
        host = str(host_value).strip()
        if host in _GATEWAY_VALID_USER_HOSTS and host not in hosts:
            hosts.append(host)

    primary = str(primary_host).strip()
    if primary in _GATEWAY_VALID_USER_HOSTS:
        _append(primary)

    if isinstance(raw_hosts, list):
        for host_value in raw_hosts:
            _append(host_value)

    return hosts


def _normalize_gateway_hosts(gateway: dict) -> tuple[str, list[str]]:
    user_host = str(gateway.get("userHost", "other")).strip()
    if user_host not in _GATEWAY_VALID_USER_HOSTS:
        user_host = "other"
    enabled_hosts = _normalize_enabled_hosts(gateway.get("enabledHosts", []), user_host)
    return user_host, enabled_hosts


def _cline_inline_hooks_supported() -> bool:
    """Return the platform branch used by CC; this does not inspect Cline."""
    return os.name != "nt"


def _host_supports_inline_boundary(host_profile: dict) -> bool:
    return str(host_profile.get("inlineBoundaryGate", "none")) == "native_hooks"


def _derive_host_profile(user_host: str) -> dict:
    """Return the shipped CC integration model, not a host capability probe."""
    normalized_host = str(user_host).strip()
    if normalized_host not in _GATEWAY_VALID_USER_HOSTS:
        normalized_host = "other"

    host_label = _format_user_host_label(normalized_host)
    export_spec = _host_context_export_spec(normalized_host)
    context_file = export_spec["relative_path"] if export_spec is not None else None

    capability_class = "instruction_first"
    permission_gate = "none"
    inline_boundary_gate = "none"
    repo_boundary_gate = "git_pre_commit"
    review_gate = "post_commit_or_manual"
    protection_model = "review_driven"
    summary = (
        "CC uses an instruction-first integration without a CC native inline hook route. "
        "Keep context in sync and configure the repo boundary, review and verification gates."
    )

    if normalized_host == "claude_code":
        capability_class = "native_inline_hooks"
        permission_gate = "official_host_permissions"
        inline_boundary_gate = "native_hooks"
        review_gate = "native_hooks"
        protection_model = "inline_first"
        summary = (
            "CC models native hooks for selected tool routes. Configure and validate "
            "the matching host event and hook before relying on a pre-write gate."
        )
    elif normalized_host == "cline":
        if _cline_inline_hooks_supported():
            capability_class = "native_inline_hooks"
            inline_boundary_gate = "native_hooks"
            review_gate = "native_hooks"
            protection_model = "inline_first"
            summary = (
                "CC models a native-hook route for Cline on this platform. "
                "This branch does not establish adapter compatibility or hook delivery."
            )
        else:
            summary = (
                "CC selects its repo-side Cline fallback on Windows. "
                "This is CC integration policy, not a probe of the installed host. "
                "Configure the repo boundary, review and verification gates."
            )
            protection_model = "repo_side"
    elif normalized_host == "codex_cli":
        capability_class = "sandbox_approval"
        permission_gate = "sandbox_approvals"
        protection_model = "repo_side"
        summary = (
            "CC uses Codex sandbox/approval integration and a repo-side boundary route; "
            "CC does not implement a Codex native inline hook adapter. Choose this when "
            "Codex is the AI host, including inside an editor shell. Use AGENTS.md and "
            "configure the repo boundary, review and verification gates."
        )
    elif normalized_host in {"gemini_cli", "cursor", "windsurf"}:
        summary = (
            "CC provides a context export and repo-side workflow for this host, "
            "without a CC native inline hook adapter. Keep context aligned and "
            "configure the repo boundary, review and verification gates."
        )
    elif normalized_host in {"vscode", "other"}:
        summary = (
            "Manual or generic editor workflow without a host-native CC context export. "
            "Use generated launcher instructions plus the repo boundary gate, review gate, "
            "and verification gate. CC does not implement a native inline hook route."
        )

    return {
        "schemaVersion": HOST_PROFILE_SCHEMA_VERSION,
        "profileScope": "cc_integration_model",
        "userHost": normalized_host,
        "label": host_label,
        "capabilityClass": capability_class,
        "contextFile": context_file,
        "supportsHostHopping": True,
        "permissionGate": permission_gate,
        "inlineBoundaryGate": inline_boundary_gate,
        "repoBoundaryGate": repo_boundary_gate,
        "reviewGate": review_gate,
        "verificationGate": "criteria_and_invariants",
        "protectionModel": protection_model,
        "summary": summary + " Named-host integration remains unverified by this profile.",
    }


def _host_coverage_payload(host_profile: dict, gateway: dict | None = None) -> dict:
    """Describe evidence scope without probing a host or trusting stored claims."""
    if gateway is None:
        configuration = "not_inspected"
    else:
        declared = gateway.get("userHost")
        configuration = (
            "declared_unverified"
            if isinstance(declared, str) and declared.strip() in _GATEWAY_VALID_USER_HOSTS
            else "not_declared"
        )
    return {
        "platformCapability": {
            "status": "documented_separately",
            "reference": "docs/cross-tool-guide.md#14-dated-platform-documentation",
        },
        "ccIntegration": {
            "scope": "cc_integration_model",
            "inlineBoundary": (
                "modeled_unverified"
                if _host_supports_inline_boundary(host_profile)
                else "not_implemented"
            ),
            "contextFile": host_profile.get("contextFile"),
        },
        "projectConfiguration": {
            "status": configuration,
            "scope": (
                "Host declaration only; generated assets, enabled hooks and rule loading "
                "are not established by this report. Doctor asset checks are configuration checks."
            ),
        },
        "testedIntegration": {"status": "unverified", "artifacts": []},
    }


def _format_host_coverage_lines(coverage: dict) -> list[str]:
    return [
        "Platform capability: documented separately in docs/cross-tool-guide.md.",
        "CC integration model: inline boundary = " + coverage["ccIntegration"]["inlineBoundary"],
        "Project configuration: " + coverage["projectConfiguration"]["status"]
        + " (host declaration only; asset presence does not prove loading).",
        "Named-host integration: unverified; no host/version/OS/event/tool-path result is attested.",
    ]


def _derive_host_gate_contract(host_profile: dict) -> list[dict]:
    inline_supported = _host_supports_inline_boundary(host_profile)
    non_inline = not inline_supported

    contract = [
        {
            "id": "inline_gate",
            "label": "inline gate",
            "nature": "mechanical" if inline_supported else "unavailable",
            "requirement": "primary" if inline_supported else "not_supported",
            "location": "native host hooks" if inline_supported else "not_available",
            "primary": inline_supported,
            "summary": (
                "CC models blocking on selected routes when the hook is configured, loaded and invoked; delivery is unverified."
                if inline_supported
                else "CC does not implement a native pre-write hook route for this integration."
            ),
        },
        {
            "id": "repo_boundary_gate",
            "label": "repo boundary gate",
            "nature": "mechanical",
            "requirement": "required" if non_inline else "backstop",
            "location": "git pre-commit",
            "primary": non_inline,
            "summary": (
                "Primary boundary path for this host. Protected-path and fitness checks run at repository level."
                if non_inline
                else "Repository-level backstop behind the inline gate."
            ),
        },
        {
            "id": "review_gate",
            "label": "review gate",
            "nature": "mechanical" if host_profile.get("reviewGate") == "native_hooks" else "conditional",
            "requirement": "required" if non_inline else "backstop",
            "location": (
                "native host hooks"
                if host_profile.get("reviewGate") == "native_hooks"
                else "CodeWarden post-commit / manual / CI"
            ),
            "primary": False,
            "summary": (
                "CC models native review hooks; configuration and host delivery require separate validation."
                if host_profile.get("reviewGate") == "native_hooks"
                else "Review can be mechanically triggered repo-side, but findings depend on the configured review path/backend."
            ),
        },
        {
            "id": "verification_gate",
            "label": "verification gate",
            "nature": "conditional",
            "requirement": "required",
            "location": "fitness checks + project invariants in commit/CI",
            "primary": False,
            "summary": (
                "Mechanical only when fitness/invariant commands are actually wired into commit or CI. "
                "Otherwise this remains advisory."
            ),
        },
    ]
    for gate in contract:
        gate["scope"] = "cc_integration_model"
    return contract


def _format_host_gate_contract_lines(host_profile: dict) -> list[str]:
    lines: list[str] = []
    for gate in _derive_host_gate_contract(host_profile):
        lines.append(
            f"{gate['label']}: {gate['nature']} / {gate['requirement']} / "
            f"{gate['location']} - {gate['summary']}"
        )
    return lines


def _host_profile_matches(gateway_profile: dict, expected_profile: dict) -> bool:
    """Return True when a persisted gateway host profile matches the derived one."""
    if not isinstance(gateway_profile, dict):
        return False
    for key, expected_value in expected_profile.items():
        # Presentation changes must not force configuration regeneration. Consumers
        # derive current descriptions instead of trusting persisted coverage claims.
        if key in {"summary", "profileScope"}:
            continue
        if gateway_profile.get(key) != expected_value:
            return False
    return True


def _first_existing_path(*candidates: Path) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _hook_file_contains(path: Path, markers: tuple[str, ...], require_all: bool = False) -> bool:
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if require_all:
        return all(marker in content for marker in markers)
    return any(marker in content for marker in markers)


def _load_engagement_runtime_config(config_path: Path | str) -> dict:
    """Load the normalized engagement config via control_plane_utils."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from control_plane_utils import load_engagement

    return load_engagement(str(config_path))


def _assess_local_tandem_capacity(total_ram_gb=None, gpu_vram_gb=None) -> dict:
    """Assess local tandem viability via control_plane_utils."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from control_plane_utils import assess_local_tandem_capacity

    return assess_local_tandem_capacity(
        total_ram_gb=total_ram_gb,
        gpu_vram_gb=gpu_vram_gb,
    )


def _format_local_tandem_assessment(assessment: dict) -> str:
    """Format a concise local tandem diagnostic string."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from control_plane_utils import format_local_tandem_assessment

    return format_local_tandem_assessment(assessment)


def _normalize_specialist_paths(raw_paths: object) -> list[dict]:
    if not isinstance(raw_paths, list):
        return []
    normalized: list[dict] = []
    for index, item in enumerate(raw_paths, start=1):
        if not isinstance(item, dict):
            continue
        role_id = str(item.get("role_id") or item.get("roleId") or item.get("id") or f"specialist_{index}").strip() or f"specialist_{index}"
        label = str(item.get("label") or item.get("role") or role_id).strip() or role_id
        backend = str(item.get("backend") or item.get("backend_id") or "").strip()
        model = str(item.get("model") or "").strip()
        active = item.get("active", True) is not False
        permission = str(
            item.get("permission")
            or item.get("permission_mode")
            or ("disabled" if not active else "approval_required")
        ).strip().lower() or ("disabled" if not active else "approval_required")
        if permission not in _VALID_SPECIALIST_PERMISSION_MODES:
            permission = "disabled" if not active else "approval_required"
        execution_mode = str(
            item.get("execution_mode")
            or item.get("executionMode")
            or _infer_specialist_execution_mode(permission, active=active)
        ).strip().lower()
        if execution_mode not in _VALID_SPECIALIST_EXECUTION_MODES:
            execution_mode = _infer_specialist_execution_mode(permission, active=active)
        permission = _infer_specialist_permission(execution_mode, active=active)
        try:
            max_calls = int(item.get("max_calls", item.get("maxCalls", 0)))
        except (TypeError, ValueError):
            max_calls = 0
        if max_calls < 0:
            max_calls = 0
        normalized.append({
            "role_id": role_id,
            "label": label,
            "path_type": str(item.get("path_type") or item.get("pathType") or item.get("kind") or "specialist").strip() or "specialist",
            "active": active,
            "backend": backend,
            "model": model,
            "permission": "disabled" if not active else permission,
            "execution_mode": "disabled" if not active else execution_mode,
            "max_calls": max_calls,
        })
    return normalized


def _has_explicit_tandem_intent(engagement: dict, specialist_paths: list[dict]) -> bool:
    for path in specialist_paths:
        if path.get("active") is False:
            continue
        role_id = _normalize_role_key(path.get("role_id"))
        path_type = _normalize_role_key(path.get("path_type"))
        if role_id.startswith("tandem") or path_type == "tandem":
            return True

    tandem_cfg = engagement.get("tandem", {}) if isinstance(engagement.get("tandem"), dict) else {}
    return any(
        isinstance(tandem_cfg.get(field), str) and str(tandem_cfg.get(field)).strip()
        for field in ("backend_a", "backend_b", "model_a", "model_b")
    )


def _format_specialist_matrix_lines(paths: list[dict], global_max_calls: int) -> list[str]:
    lines: list[str] = []
    for path in paths:
        if path.get("active") is False:
            continue
        label = str(path.get("label") or path.get("role_id") or "Specialist").strip()
        backend = str(path.get("backend", "")).strip() or "(missing backend)"
        model = str(path.get("model", "")).strip()
        permission = str(path.get("permission", "approval_required")).strip()
        execution_mode = str(path.get("execution_mode", _infer_specialist_execution_mode(permission))).strip()
        max_calls = int(path.get("max_calls", 0)) if isinstance(path.get("max_calls", 0), int) else 0
        limit_text = "follow global budget" if max_calls == 0 else f"{max_calls} call(s)"
        if global_max_calls > 0 and max_calls == 0:
            limit_text = f"follow global budget ({global_max_calls} call(s))"
        line = f"{label}: {backend}"
        if model:
            line += f" / {model}"
        line += f" / {execution_mode} / {permission} / {limit_text}"
        lines.append(line)
    return lines


def _infer_specialist_execution_mode(permission: str, active: bool = True) -> str:
    if not active:
        return "disabled"
    normalized = str(permission or "").strip().lower()
    if normalized == "user_mediated":
        return "human_mediated"
    if normalized == "within_budget_auto":
        return "auto_bounded"
    return "cc_routed"


def _infer_specialist_permission(execution_mode: str, active: bool = True) -> str:
    if not active:
        return "disabled"
    normalized = str(execution_mode or "").strip().lower()
    if normalized == "human_mediated":
        return "user_mediated"
    if normalized == "auto_bounded":
        return "within_budget_auto"
    return "approval_required"


def _normalize_role_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _specialist_semantic_role(path: dict) -> str:
    label_key = _normalize_role_key(path.get("label", ""))
    if "architect" in label_key:
        return "architect"
    if "planner" in label_key or "plan" == label_key:
        return "planner"
    if "review" in label_key:
        return "reviewer"
    if "narrat" in label_key:
        return "narrator"
    if "codewarden" in label_key:
        return "codewarden"
    return _normalize_role_key(path.get("role_id", "")) or "specialist"


def _find_specialist_path(paths: list[dict], role_ref: str) -> dict | None:
    target = _normalize_role_key(role_ref)
    if not target:
        return None
    for path in paths:
        if path.get("active") is False:
            continue
        if _normalize_role_key(path.get("role_id")) == target:
            return path
        if _normalize_role_key(path.get("label")) == target:
            return path
        if _specialist_semantic_role(path) == target:
            return path
    return None


def _project_relative_label(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def _external_consultation_dir(project: Path) -> Path:
    return _control_plane_path(project, EXTERNAL_CONSULTATION_DIRNAME)


def _legacy_external_consultation_dir(project: Path) -> Path:
    return _legacy_control_plane_path(project, EXTERNAL_CONSULTATION_DIRNAME)


def _consult_packets_dir(project: Path) -> Path:
    return _external_consultation_dir(project) / EXTERNAL_CONSULT_REQUEST_DIRNAME


def _consult_results_dir(project: Path) -> Path:
    return _external_consultation_dir(project) / EXTERNAL_CONSULT_IMPORT_DIRNAME


def _consult_responses_dir(project: Path) -> Path:
    return _external_consultation_dir(project) / EXTERNAL_CONSULT_RESPONSE_DIRNAME


def _consult_threads_dir(project: Path) -> Path:
    return _external_consultation_dir(project) / EXTERNAL_CONSULT_THREAD_DIRNAME


def _consult_thread_dir(project: Path, thread_id: str) -> Path:
    return _consult_threads_dir(project) / thread_id


def _consult_thread_state_path(project: Path, thread_id: str) -> Path:
    return _consult_thread_dir(project, thread_id) / CONSULT_THREAD_STATE_FILENAME


def _consult_thread_memory_path(project: Path, thread_id: str) -> Path:
    return _consult_thread_dir(project, thread_id) / CONSULT_THREAD_MEMORY_FILENAME


def _consult_thread_resume_path(project: Path, thread_id: str) -> Path:
    return _consult_thread_dir(project, thread_id) / CONSULT_THREAD_RESUME_FILENAME


def _consult_thread_decisions_path(project: Path, thread_id: str) -> Path:
    return _consult_thread_dir(project, thread_id) / CONSULT_THREAD_DECISIONS_FILENAME


def _consult_role_memory_dir(project: Path) -> Path:
    return _external_consultation_dir(project) / EXTERNAL_CONSULT_ROLE_MEMORY_DIRNAME


def _consult_role_memory_role_dir(project: Path, semantic_role: str) -> Path:
    normalized = _normalize_role_key(semantic_role) or "specialist"
    return _consult_role_memory_dir(project) / normalized


def _consult_role_memory_state_path(project: Path, semantic_role: str) -> Path:
    return _consult_role_memory_role_dir(project, semantic_role) / CONSULT_ROLE_MEMORY_STATE_FILENAME


def _consult_role_memory_markdown_path(project: Path, semantic_role: str) -> Path:
    return _consult_role_memory_role_dir(project, semantic_role) / CONSULT_ROLE_MEMORY_MARKDOWN_FILENAME


def _consult_convergence_summary_path(project: Path) -> Path:
    return _external_consultation_dir(project) / CONSULT_CONVERGENCE_SUMMARY_FILENAME


def _consult_convergence_summary_markdown_path(project: Path) -> Path:
    return _external_consultation_dir(project) / CONSULT_CONVERGENCE_SUMMARY_MARKDOWN_FILENAME


def _consult_resolution_dir(project: Path) -> Path:
    return _external_consultation_dir(project) / EXTERNAL_CONSULT_RESOLUTION_DIRNAME


def _consult_resolution_topic_dir(project: Path, semantic_role: str, topic_key: str) -> Path:
    role_part = _normalize_role_key(semantic_role) or "specialist"
    topic_part = _normalize_consult_topic_key(topic_key, topic_key)
    return _consult_resolution_dir(project) / f"{role_part}__{topic_part}"


def _consult_resolution_state_path(project: Path, semantic_role: str, topic_key: str) -> Path:
    return _consult_resolution_topic_dir(project, semantic_role, topic_key) / "resolution.json"


def _consult_resolution_prompt_path(project: Path, semantic_role: str, topic_key: str) -> Path:
    return _consult_resolution_topic_dir(project, semantic_role, topic_key) / CONSULT_RESOLUTION_PROMPT_FILENAME


def _consult_log_path(project: Path) -> Path:
    canonical = _external_consultation_dir(project) / CONSULT_LOG_FILENAME
    if canonical.exists():
        return canonical
    legacy_external = _legacy_external_consultation_dir(project) / CONSULT_LOG_FILENAME
    if legacy_external.exists():
        return legacy_external
    legacy_root = _legacy_control_plane_path(project, CONSULT_LOG_FILENAME)
    if legacy_root.exists():
        return legacy_root
    return canonical


def _compact_utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _build_consult_artifact_id(role_id: str, kind: str) -> str:
    normalized_role = _normalize_role_key(role_id) or "specialist"
    normalized_kind = _normalize_role_key(kind) or "record"
    # Equal clock readings must not replace independent consultation records.
    # The suffix supplies identity, not chronology; keep explicit record links.
    return f"{normalized_role}_{normalized_kind}_{_compact_utc_stamp()}_{secrets.token_hex(16)}"


def _consult_packet_path(project: Path, packet_id: str) -> Path:
    filename = packet_id if packet_id.endswith(".json") else f"{packet_id}.json"
    canonical = _consult_packets_dir(project) / filename
    if canonical.exists():
        return canonical
    external_legacy = _legacy_external_consultation_dir(project) / EXTERNAL_CONSULT_REQUEST_DIRNAME / filename
    if external_legacy.exists():
        return external_legacy
    legacy = project / LEGACY_CONTROL_PLANE_DIRNAME / CONSULT_PACKET_DIRNAME / filename
    if legacy.exists():
        return legacy
    return canonical


def _truncate_summary(value: str, limit: int = 240) -> str:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _normalize_consult_topic_key(value: object, fallback_text: object = "") -> str:
    normalized = _normalize_role_key(value)
    if normalized:
        return normalized[:64]
    fallback = _normalize_role_key(fallback_text)
    if fallback:
        return fallback[:64]
    digest_source = " ".join(str(fallback_text or value or "").split()) or "general"
    return f"topic_{hashlib.sha1(digest_source.encode('utf-8')).hexdigest()[:10]}"


def _normalize_consult_text(value: object,
                            *,
                            field_name: str,
                            max_length: int,
                            required: bool = True) -> tuple[str, str | None]:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        if required:
            return "", f"{field_name} is required."
        return "", None
    if len(cleaned) > max_length:
        return "", f"{field_name} must stay concise ({max_length} characters max)."
    return cleaned, None


def _normalize_consult_list(values: list[str] | None,
                            *,
                            field_name: str,
                            item_limit: int = _CONSULT_LIST_ITEM_LIMIT,
                            max_items: int = _CONSULT_MAX_LIST_ITEMS,
                            required: bool = False) -> tuple[list[str], str | None]:
    items = [" ".join(str(value or "").split()) for value in (values or [])]
    items = [item for item in items if item]
    if required and not items:
        return [], f"At least one {field_name} is required."
    if len(items) > max_items:
        return [], f"{field_name} must contain at most {max_items} item(s)."
    for item in items:
        if len(item) > item_limit:
            return [], f"Each {field_name} item must stay concise ({item_limit} characters max)."
    return items, None


def _load_consult_json_dir(directory: Path) -> list[dict]:
    if not directory.exists():
        return []
    rows: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        payload = _read_json_object(path)
        if payload:
            rows.append(payload)
    return rows


def _load_consult_packets(project: Path) -> list[dict]:
    rows = _load_consult_json_dir(_consult_packets_dir(project))
    if rows:
        return rows
    external_legacy = _legacy_external_consultation_dir(project) / EXTERNAL_CONSULT_REQUEST_DIRNAME
    rows = _load_consult_json_dir(external_legacy)
    if rows:
        return rows
    return _load_consult_json_dir(project / LEGACY_CONTROL_PLANE_DIRNAME / CONSULT_PACKET_DIRNAME)


def _load_consult_results(project: Path) -> list[dict]:
    rows = _load_consult_json_dir(_consult_results_dir(project))
    if rows:
        return rows
    external_legacy = _legacy_external_consultation_dir(project) / EXTERNAL_CONSULT_IMPORT_DIRNAME
    rows = _load_consult_json_dir(external_legacy)
    if rows:
        return rows
    return _load_consult_json_dir(project / LEGACY_CONTROL_PLANE_DIRNAME / CONSULT_RESULT_DIRNAME)


def _consult_result_path(project: Path, result_id: str) -> Path:
    filename = result_id if result_id.endswith(".json") else f"{result_id}.json"
    canonical = _consult_results_dir(project) / filename
    if canonical.exists():
        return canonical
    external_legacy = _legacy_external_consultation_dir(project) / EXTERNAL_CONSULT_IMPORT_DIRNAME / filename
    if external_legacy.exists():
        return external_legacy
    legacy = project / LEGACY_CONTROL_PLANE_DIRNAME / CONSULT_RESULT_DIRNAME / filename
    if legacy.exists():
        return legacy
    return canonical


def _load_consult_threads(project: Path) -> list[dict]:
    rows: list[dict] = []
    directory = _consult_threads_dir(project)
    if not directory.exists():
        return rows
    for thread_dir in sorted(path for path in directory.iterdir() if path.is_dir()):
        payload = _read_json_object(thread_dir / CONSULT_THREAD_STATE_FILENAME)
        if payload:
            rows.append(payload)
    return rows


def _merge_unique_items(existing: list[str] | None, new_items: list[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in (existing or []) + (new_items or []):
        item = " ".join(str(raw or "").split())
        if not item or item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def _consult_thread_status_from_decision(decision: str) -> str:
    normalized = str(decision or "").strip().lower()
    if normalized == "accepted":
        return "accepted"
    if normalized == "rejected":
        return "rejected"
    return "imported"


def _render_consult_thread_memory(project: Path, thread: dict) -> str:
    role_label = str(thread.get("role_label") or thread.get("role_id") or "Specialist").strip() or "Specialist"
    lines = [
        f"# Manual Consultation Memory - {role_label}",
        "",
        f"- Thread ID: `{thread.get('thread_id', '')}`",
        f"- Status: `{thread.get('status', 'drafted')}`",
        f"- Semantic role: `{thread.get('semantic_role', 'specialist')}`",
        f"- Topic key: `{thread.get('topic_key', '') or 'general'}`",
        f"- Backend expectation: `{thread.get('backend', '') or '(manual external chat)'}`",
    ]
    model = str(thread.get("model", "")).strip()
    if model:
        lines.append(f"- Model hint: `{model}`")
    lines.extend(
        [
            f"- Latest packet: `{thread.get('latest_packet_id', '') or '(none)'}`",
            f"- Latest result: `{thread.get('latest_result_id', '') or '(none)'}`",
            "",
            "## Objective",
            "",
            str(thread.get("latest_objective") or "(not captured yet)"),
        ]
    )
    latest_context = str(thread.get("latest_context_summary") or "").strip()
    if latest_context:
        lines.extend(["", "## Local Context Summary", "", latest_context])
    constraints = thread.get("constraints", [])
    if isinstance(constraints, list) and constraints:
        lines.extend(["", "## Active Constraints", ""])
        lines.extend(f"- {item}" for item in constraints)
    open_questions = thread.get("open_questions", [])
    if isinstance(open_questions, list) and open_questions:
        lines.extend(["", "## Open Questions", ""])
        lines.extend(f"- {item}" for item in open_questions)
    decisions = thread.get("decision_history", [])
    if isinstance(decisions, list) and decisions:
        lines.extend(["", "## Decision History", ""])
        for item in decisions[-5:]:
            timestamp = str(item.get("imported_at", "")).strip() or "(time unknown)"
            decision = str(item.get("decision", "unknown")).strip() or "unknown"
            summary = str(item.get("summary", "")).strip() or "(missing summary)"
            next_action = str(item.get("next_action", "")).strip()
            lines.append(f"- {timestamp} - `{decision}` - {summary}")
            if next_action:
                lines.append(f"  Next action: {next_action}")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Thread state: `{_project_relative_label(project, _consult_thread_state_path(project, str(thread.get('thread_id', ''))))}`",
            f"- Resume prompt: `{_project_relative_label(project, _consult_thread_resume_path(project, str(thread.get('thread_id', ''))))}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_consult_thread_resume_prompt(thread: dict) -> str:
    role_label = str(thread.get("role_label") or thread.get("role_id") or "specialist").strip() or "specialist"
    lines = [
        f"You are continuing a bounded manual consultation as the {role_label}.",
        "",
        "Stay within the existing consultation scope. Do not redesign the whole project.",
        "",
        "Project-side thread metadata:",
        f"- Thread ID: {thread.get('thread_id', '')}",
        f"- Current thread status: {thread.get('status', 'drafted')}",
        f"- Semantic role: {thread.get('semantic_role', 'specialist')}",
        f"- Topic key: {thread.get('topic_key', '') or 'general'}",
        "",
        "Objective for this consultation thread:",
        str(thread.get("latest_objective") or "(not captured yet)"),
    ]
    latest_context = str(thread.get("latest_context_summary") or "").strip()
    if latest_context:
        lines.extend(["", "Local context summary:", latest_context])
    decisions = thread.get("decision_history", [])
    if isinstance(decisions, list) and decisions:
        lines.extend(["", "Already imported decisions:", ""])
        for item in decisions[-3:]:
            summary = str(item.get("summary", "")).strip() or "(missing summary)"
            decision = str(item.get("decision", "unknown")).strip() or "unknown"
            next_action = str(item.get("next_action", "")).strip()
            line = f"- {decision}: {summary}"
            if next_action:
                line += f" | next action: {next_action}"
            lines.append(line)
    constraints = thread.get("constraints", [])
    if isinstance(constraints, list) and constraints:
        lines.extend(["", "Constraints that must remain true:", ""])
        lines.extend(f"- {item}" for item in constraints)
    open_questions = thread.get("open_questions", [])
    if isinstance(open_questions, list) and open_questions:
        lines.extend(["", "Questions to answer in this round:", ""])
        lines.extend(f"- {item}" for item in open_questions)
    lines.extend(
        [
            "",
            "Answer format required by ControlCoding import:",
            "1. Decision: accepted | rejected | partial",
            "2. Concise summary",
            "3. Concise rationale summary",
            "4. Next action",
            "5. Optional evidence bullets",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _sync_consult_thread_artifacts(project: Path, thread: dict) -> None:
    thread_id = str(thread.get("thread_id", "")).strip()
    if not thread_id:
        return
    thread_dir = _consult_thread_dir(project, thread_id)
    thread_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(_consult_thread_state_path(project, thread_id), thread)
    memory_path = _consult_thread_memory_path(project, thread_id)
    memory_path.write_text(_render_consult_thread_memory(project, thread), encoding="utf-8")
    resume_path = _consult_thread_resume_path(project, thread_id)
    resume_path.write_text(_render_consult_thread_resume_prompt(thread), encoding="utf-8")
    decisions_path = _consult_thread_decisions_path(project, thread_id)
    if not decisions_path.exists():
        decisions_path.write_text("", encoding="utf-8")


def _create_consult_thread(project: Path,
                           *,
                           packet_id: str,
                           specialist_path: dict,
                           engagement: dict,
                           objective: str,
                           context_summary: str,
                           constraints: list[str],
                           questions: list[str],
                           topic_key: str,
                           topic_label: str) -> dict:
    thread_id = _build_consult_artifact_id(str(specialist_path.get("role_id", "specialist")), "thread")
    created_at = _utc_now_iso()
    thread = {
        "schema_version": 1,
        "thread_id": thread_id,
        "created_at": created_at,
        "updated_at": created_at,
        "tier": engagement.get("tier", "core"),
        "role_id": specialist_path.get("role_id", ""),
        "role_label": specialist_path.get("label", ""),
        "semantic_role": _specialist_semantic_role(specialist_path),
        "path_type": specialist_path.get("path_type", "specialist"),
        "permission": specialist_path.get("permission", "user_mediated"),
        "execution_mode": specialist_path.get("execution_mode", "human_mediated"),
        "backend": specialist_path.get("backend", ""),
        "model": specialist_path.get("model", ""),
        "status": "drafted",
        "title": _truncate_summary(objective, limit=96),
        "topic_key": topic_key,
        "topic_label": topic_label,
        "latest_objective": objective,
        "latest_context_summary": context_summary,
        "constraints": constraints,
        "open_questions": questions,
        "packet_ids": [packet_id],
        "result_ids": [],
        "latest_packet_id": packet_id,
        "latest_result_id": "",
        "decision_history": [],
    }
    _sync_consult_thread_artifacts(project, thread)
    return thread


def _load_consult_thread(project: Path, thread_id: str) -> dict | None:
    normalized = str(thread_id or "").strip()
    if not normalized:
        return None
    return _read_json_object(_consult_thread_state_path(project, normalized))


def _load_consult_role_memories(project: Path) -> list[dict]:
    rows: list[dict] = []
    directory = _consult_role_memory_dir(project)
    if not directory.exists():
        return rows
    for role_dir in sorted(path for path in directory.iterdir() if path.is_dir()):
        payload = _read_json_object(role_dir / CONSULT_ROLE_MEMORY_STATE_FILENAME)
        if payload:
            rows.append(payload)
    return rows


def _sorted_consult_decision_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            str(item.get("imported_at", "")),
            str(item.get("result_id", "")),
        ),
    )


def _build_consult_role_memory(threads: list[dict],
                               results: list[dict],
                               semantic_role: str) -> dict:
    normalized_role = _normalize_role_key(semantic_role) or "specialist"
    role_threads = [
        thread for thread in threads
        if _normalize_role_key(thread.get("semantic_role")) == normalized_role
    ]
    role_results = [
        result for result in results
        if _normalize_role_key(result.get("semantic_role")) == normalized_role
    ]
    latest_thread = role_threads[-1] if role_threads else None
    latest_result = role_results[-1] if role_results else None
    role_label = ""
    if latest_thread:
        role_label = str(latest_thread.get("role_label") or latest_thread.get("role_id") or "").strip()
    if not role_label and latest_result:
        role_label = str(latest_result.get("role_label") or latest_result.get("role_id") or "").strip()
    role_label = role_label or normalized_role.replace("_", " ").title()

    thread_statuses: dict[str, int] = {}
    unresolved_thread_ids: list[str] = []
    open_questions: list[str] = []
    constraints: list[str] = []
    recent_decisions: list[dict] = []
    topic_merges: dict[str, dict] = {}
    for thread in role_threads:
        status = str(thread.get("status", "drafted")).strip() or "drafted"
        thread_statuses[status] = thread_statuses.get(status, 0) + 1
        if status in {"drafted", "imported"}:
            unresolved_thread_ids.append(str(thread.get("thread_id", "")).strip())
        open_questions = _merge_unique_items(open_questions, thread.get("open_questions", []))
        constraints = _merge_unique_items(constraints, thread.get("constraints", []))
        thread_topic_key = _normalize_consult_topic_key(thread.get("topic_key", ""), thread.get("topic_label", "") or thread.get("title", ""))
        topic_merge = topic_merges.setdefault(
            thread_topic_key,
            {
                "topic_key": thread_topic_key,
                "topic_label": str(thread.get("topic_label", "")).strip() or str(thread.get("title", "")).strip() or thread_topic_key,
                "thread_ids": [],
                "result_ids": [],
                "decision_counts": {},
                "unresolved_thread_ids": [],
                "latest_decision": "",
                "latest_summary": "",
                "latest_next_action": "",
                "latest_imported_at": "",
                "status": "pending",
                "recent_decisions": [],
            },
        )
        topic_merge["thread_ids"] = _merge_unique_items(topic_merge.get("thread_ids", []), [str(thread.get("thread_id", "")).strip()])
        if status in {"drafted", "imported"}:
            topic_merge["unresolved_thread_ids"] = _merge_unique_items(
                topic_merge.get("unresolved_thread_ids", []),
                [str(thread.get("thread_id", "")).strip()],
            )
        for item in thread.get("decision_history", []):
            if not isinstance(item, dict):
                continue
            item_topic_key = _normalize_consult_topic_key(item.get("topic_key", ""), item.get("topic_label", "") or thread.get("topic_label", "") or thread.get("title", ""))
            item_topic_merge = topic_merges.setdefault(
                item_topic_key,
                {
                    "topic_key": item_topic_key,
                    "topic_label": str(item.get("topic_label", "")).strip() or str(thread.get("topic_label", "")).strip() or str(thread.get("title", "")).strip() or item_topic_key,
                    "thread_ids": [],
                    "result_ids": [],
                    "decision_counts": {},
                    "unresolved_thread_ids": [],
                    "latest_decision": "",
                    "latest_summary": "",
                    "latest_next_action": "",
                    "latest_imported_at": "",
                    "status": "pending",
                    "recent_decisions": [],
                },
            )
            item_topic_merge["thread_ids"] = _merge_unique_items(item_topic_merge.get("thread_ids", []), [str(thread.get("thread_id", "")).strip()])
            item_topic_merge["result_ids"] = _merge_unique_items(item_topic_merge.get("result_ids", []), [str(item.get("result_id", "")).strip()])
            item_decision = str(item.get("decision", "unknown")).strip() or "unknown"
            counts = item_topic_merge.get("decision_counts", {})
            counts[item_decision] = counts.get(item_decision, 0) + 1
            item_topic_merge["decision_counts"] = counts
            imported_at = str(item.get("imported_at", "")).strip()
            topic_recent = list(item_topic_merge.get("recent_decisions", []))
            topic_recent.append(
                {
                    "thread_id": str(thread.get("thread_id", "")).strip(),
                    "result_id": str(item.get("result_id", "")).strip(),
                    "packet_id": str(item.get("packet_id", "")).strip(),
                    "decision": item_decision,
                    "summary": str(item.get("summary", "")).strip(),
                    "rationale_summary": str(item.get("rationale_summary", "")).strip(),
                    "next_action": str(item.get("next_action", "")).strip(),
                    "imported_at": imported_at,
                    "source": str(item.get("source", "")).strip(),
                }
            )
            item_topic_merge["recent_decisions"] = _sorted_consult_decision_items(topic_recent)[-5:]
            if imported_at >= str(item_topic_merge.get("latest_imported_at", "")).strip():
                item_topic_merge["latest_imported_at"] = imported_at
                item_topic_merge["latest_decision"] = item_decision
                item_topic_merge["latest_summary"] = str(item.get("summary", "")).strip()
                item_topic_merge["latest_next_action"] = str(item.get("next_action", "")).strip()
            recent_decisions.append(
                {
                    "thread_id": str(thread.get("thread_id", "")).strip(),
                    "topic_key": item_topic_key,
                    "topic_label": str(item.get("topic_label", "")).strip() or str(thread.get("topic_label", "")).strip() or str(thread.get("title", "")).strip(),
                    "result_id": str(item.get("result_id", "")).strip(),
                    "packet_id": str(item.get("packet_id", "")).strip(),
                    "decision": item_decision,
                    "summary": str(item.get("summary", "")).strip(),
                    "rationale_summary": str(item.get("rationale_summary", "")).strip(),
                    "next_action": str(item.get("next_action", "")).strip(),
                    "imported_at": str(item.get("imported_at", "")).strip(),
                    "source": str(item.get("source", "")).strip(),
                    "thread_title": str(thread.get("title", "")).strip(),
                }
            )
    if not recent_decisions:
        for result in role_results:
            recent_decisions.append(
                {
                    "thread_id": "",
                    "topic_key": _normalize_consult_topic_key(result.get("topic_key", ""), result.get("topic_label", "") or result.get("source_packet_id", "")),
                    "topic_label": str(result.get("topic_label", "")).strip(),
                    "result_id": str(result.get("result_id", "")).strip(),
                    "packet_id": str(result.get("source_packet_id", "")).strip(),
                    "decision": str(result.get("decision", "unknown")).strip() or "unknown",
                    "summary": str(result.get("imported_answer_summary", "")).strip(),
                    "rationale_summary": str(result.get("rationale_summary", "")).strip(),
                    "next_action": str(result.get("next_action", "")).strip(),
                    "imported_at": str(result.get("imported_at", "")).strip(),
                    "source": str(result.get("source", "")).strip(),
                    "thread_title": "",
                }
            )
    recent_decisions = _sorted_consult_decision_items(recent_decisions)

    decision_counts: dict[str, int] = {}
    for result in role_results:
        decision = str(result.get("decision", "unknown")).strip() or "unknown"
        decision_counts[decision] = decision_counts.get(decision, 0) + 1

    divergence_signals: list[dict] = []
    if decision_counts.get("accepted", 0) > 0 and decision_counts.get("rejected", 0) > 0:
        divergence_signals.append(
            {
                "type": "decision_conflict",
                "message": "This role has both accepted and rejected imported decisions across its consultation history.",
            }
        )
    if len(unresolved_thread_ids) > 1:
        divergence_signals.append(
            {
                "type": "parallel_open_threads",
                "message": "This role has multiple unresolved manual consultation threads in flight.",
            }
        )
    merged_topics: list[dict] = []
    for topic_key, topic_merge in sorted(topic_merges.items()):
        counts = dict(topic_merge.get("decision_counts", {}))
        unresolved = list(topic_merge.get("unresolved_thread_ids", []))
        status = "aligned"
        if counts.get("accepted", 0) > 0 and counts.get("rejected", 0) > 0:
            status = "conflicted"
            divergence_signals.append(
                {
                    "type": "topic_conflict",
                    "topic_key": topic_key,
                    "topic_label": topic_merge.get("topic_label", topic_key),
                    "message": f"Topic '{topic_merge.get('topic_label', topic_key)}' has conflicting accepted and rejected imported decisions.",
                }
            )
        elif unresolved or counts.get("partial", 0) > 0:
            status = "needs_resolution"
        topic_merge["status"] = status
        merged_topics.append(topic_merge)

    return {
        "schema_version": 1,
        "generated_at": _utc_now_iso(),
        "semantic_role": normalized_role,
        "role_label": role_label,
        "threadCount": len(role_threads),
        "resultCount": len(role_results),
        "threadStatuses": thread_statuses,
        "decisionCounts": decision_counts,
        "unresolvedThreadIds": unresolved_thread_ids,
        "latestThreadId": str(latest_thread.get("thread_id", "")).strip() if latest_thread else "",
        "latestResultId": str(latest_result.get("result_id", "")).strip() if latest_result else "",
        "openQuestions": open_questions,
        "constraints": constraints,
        "divergenceSignals": divergence_signals,
        "recentDecisions": recent_decisions[-5:],
        "mergedTopics": merged_topics,
        "activeThreadTitles": [
            {
                "thread_id": str(thread.get("thread_id", "")).strip(),
                "title": str(thread.get("title", "")).strip(),
                "status": str(thread.get("status", "drafted")).strip() or "drafted",
            }
            for thread in role_threads
        ],
    }


def _render_consult_role_memory(project: Path, role_memory: dict) -> str:
    role_label = str(role_memory.get("role_label", "")).strip() or "Specialist"
    semantic_role = str(role_memory.get("semantic_role", "specialist")).strip() or "specialist"
    lines = [
        f"# Manual Consultation Role Memory - {role_label}",
        "",
        f"- Semantic role: `{semantic_role}`",
        f"- Threads tracked: `{role_memory.get('threadCount', 0)}`",
        f"- Imported results: `{role_memory.get('resultCount', 0)}`",
        f"- Latest thread: `{role_memory.get('latestThreadId', '') or '(none)'}`",
        f"- Latest result: `{role_memory.get('latestResultId', '') or '(none)'}`",
    ]
    thread_statuses = role_memory.get("threadStatuses", {})
    if isinstance(thread_statuses, dict) and thread_statuses:
        details = ", ".join(f"{status}={count}" for status, count in sorted(thread_statuses.items()))
        lines.append(f"- Thread states: `{details}`")
    decision_counts = role_memory.get("decisionCounts", {})
    if isinstance(decision_counts, dict) and decision_counts:
        details = ", ".join(f"{decision}={count}" for decision, count in sorted(decision_counts.items()))
        lines.append(f"- Imported decisions: `{details}`")
    divergence = role_memory.get("divergenceSignals", [])
    if isinstance(divergence, list) and divergence:
        lines.extend(["", "## Divergence Signals", ""])
        for signal in divergence:
            lines.append(f"- {signal.get('message', '(missing signal)')}")
    active_threads = role_memory.get("activeThreadTitles", [])
    if isinstance(active_threads, list) and active_threads:
        lines.extend(["", "## Threads In Scope", ""])
        for row in active_threads:
            lines.append(
                f"- `{row.get('thread_id', '')}` - `{row.get('status', 'drafted')}` - {row.get('title', '(untitled thread)')}"
            )
    constraints = role_memory.get("constraints", [])
    if isinstance(constraints, list) and constraints:
        lines.extend(["", "## Cross-Thread Constraints", ""])
        lines.extend(f"- {item}" for item in constraints)
    open_questions = role_memory.get("openQuestions", [])
    if isinstance(open_questions, list) and open_questions:
        lines.extend(["", "## Still-Open Questions", ""])
        lines.extend(f"- {item}" for item in open_questions)
    merged_topics = role_memory.get("mergedTopics", [])
    if isinstance(merged_topics, list) and merged_topics:
        lines.extend(["", "## Topic Merge", ""])
        for topic in merged_topics:
            lines.append(
                f"- `{topic.get('topic_key', 'general')}` - `{topic.get('status', 'pending')}` - {topic.get('topic_label', '(missing topic label)')}"
            )
            latest_decision = str(topic.get("latest_decision", "")).strip()
            if latest_decision:
                lines.append(f"  Latest decision: `{latest_decision}`")
            latest_next_action = str(topic.get("latest_next_action", "")).strip()
            if latest_next_action:
                lines.append(f"  Next action: {latest_next_action}")
    recent_decisions = role_memory.get("recentDecisions", [])
    if isinstance(recent_decisions, list) and recent_decisions:
        lines.extend(["", "## Recent Imported Decisions", ""])
        for item in recent_decisions:
            lines.append(
                f"- {item.get('imported_at', '(time unknown)')} - `{item.get('decision', 'unknown')}` - {item.get('summary', '(missing summary)')}"
            )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Role state: `{_project_relative_label(project, _consult_role_memory_state_path(project, semantic_role))}`",
            f"- Convergence summary: `{_project_relative_label(project, _consult_convergence_summary_markdown_path(project))}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _sync_consult_role_memory(project: Path, role_memory: dict) -> None:
    semantic_role = str(role_memory.get("semantic_role", "")).strip()
    if not semantic_role:
        return
    role_dir = _consult_role_memory_role_dir(project, semantic_role)
    role_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(_consult_role_memory_state_path(project, semantic_role), role_memory)
    _consult_role_memory_markdown_path(project, semantic_role).write_text(
        _render_consult_role_memory(project, role_memory),
        encoding="utf-8",
    )


def _build_consult_convergence_summary(threads: list[dict],
                                       results: list[dict],
                                       role_memories: list[dict]) -> dict:
    divergence_signals: list[dict] = []
    topic_merges: list[dict] = []
    for role_memory in role_memories:
        for signal in role_memory.get("divergenceSignals", []):
            divergence_signals.append(
                {
                    "semantic_role": role_memory.get("semantic_role", "specialist"),
                    "role_label": role_memory.get("role_label", ""),
                    "type": signal.get("type", "signal"),
                    "message": signal.get("message", ""),
                }
            )
        for topic in role_memory.get("mergedTopics", []):
            if not isinstance(topic, dict):
                continue
            topic_merges.append(
                {
                    "semantic_role": role_memory.get("semantic_role", "specialist"),
                    "role_label": role_memory.get("role_label", ""),
                    **topic,
                }
            )
    pending_threads = [
        {
            "thread_id": str(thread.get("thread_id", "")).strip(),
            "role_label": str(thread.get("role_label") or thread.get("role_id") or "").strip(),
            "semantic_role": str(thread.get("semantic_role", "")).strip(),
            "status": str(thread.get("status", "drafted")).strip() or "drafted",
            "title": str(thread.get("title", "")).strip(),
            "latest_objective": str(thread.get("latest_objective", "")).strip(),
        }
        for thread in threads
        if str(thread.get("status", "drafted")).strip() in {"drafted", "imported"}
    ]
    recent_decisions: list[dict] = []
    for role_memory in role_memories:
        for item in role_memory.get("recentDecisions", []):
            if not isinstance(item, dict):
                continue
            recent_decisions.append(
                {
                    "semantic_role": role_memory.get("semantic_role", "specialist"),
                    "role_label": role_memory.get("role_label", ""),
                    **item,
                }
            )
    recent_decisions = _sorted_consult_decision_items(recent_decisions)[-8:]
    return {
        "schema_version": 1,
        "generated_at": _utc_now_iso(),
        "threadCount": len(threads),
        "resultCount": len(results),
        "roleMemoryCount": len(role_memories),
        "pendingThreadCount": len(pending_threads),
        "divergenceSignalCount": len(divergence_signals),
        "topicMergeCount": len(topic_merges),
        "roleSummaries": [
            {
                "semantic_role": role_memory.get("semantic_role", "specialist"),
                "role_label": role_memory.get("role_label", ""),
                "threadCount": role_memory.get("threadCount", 0),
                "resultCount": role_memory.get("resultCount", 0),
                "unresolvedThreadCount": len(role_memory.get("unresolvedThreadIds", [])),
                "decisionCounts": role_memory.get("decisionCounts", {}),
                "divergenceSignalCount": len(role_memory.get("divergenceSignals", [])),
                "topicMergeCount": len(role_memory.get("mergedTopics", [])),
            }
            for role_memory in role_memories
        ],
        "pendingThreads": pending_threads,
        "divergenceSignals": divergence_signals,
        "topicMerges": topic_merges,
        "recentImportedDecisions": recent_decisions,
    }


def _render_consult_convergence_summary(project: Path, summary: dict) -> str:
    lines = [
        "# Manual Consultation Convergence Summary",
        "",
        f"- Generated at: `{summary.get('generated_at', '')}`",
        f"- Threads tracked: `{summary.get('threadCount', 0)}`",
        f"- Imported results: `{summary.get('resultCount', 0)}`",
        f"- Roles tracked: `{summary.get('roleMemoryCount', 0)}`",
        f"- Topic merges: `{summary.get('topicMergeCount', 0)}`",
        f"- Resolution topics: `{summary.get('resolutionTopicCount', 0)}`",
        f"- Pending threads: `{summary.get('pendingThreadCount', 0)}`",
        f"- Divergence signals: `{summary.get('divergenceSignalCount', 0)}`",
    ]
    role_summaries = summary.get("roleSummaries", [])
    if isinstance(role_summaries, list) and role_summaries:
        lines.extend(["", "## Role Coverage", ""])
        for row in role_summaries:
            lines.append(
                f"- `{row.get('semantic_role', 'specialist')}` - threads={row.get('threadCount', 0)}, results={row.get('resultCount', 0)}, unresolved={row.get('unresolvedThreadCount', 0)}, topics={row.get('topicMergeCount', 0)}, divergence={row.get('divergenceSignalCount', 0)}"
            )
    topic_merges = summary.get("topicMerges", [])
    if isinstance(topic_merges, list) and topic_merges:
        lines.extend(["", "## Topic Merge", ""])
        for topic in topic_merges:
            lines.append(
                f"- `{topic.get('semantic_role', 'specialist')}` / `{topic.get('topic_key', 'general')}` - `{topic.get('status', 'pending')}` - {topic.get('topic_label', '(missing topic label)')}"
            )
    divergence = summary.get("divergenceSignals", [])
    if isinstance(divergence, list) and divergence:
        lines.extend(["", "## Divergence Signals", ""])
        for signal in divergence:
            lines.append(
                f"- `{signal.get('semantic_role', 'specialist')}` - {signal.get('message', '(missing signal)')}"
            )
    pending_threads = summary.get("pendingThreads", [])
    if isinstance(pending_threads, list) and pending_threads:
        lines.extend(["", "## Pending Threads", ""])
        for thread in pending_threads:
            lines.append(
                f"- `{thread.get('thread_id', '')}` - `{thread.get('status', 'drafted')}` - {thread.get('title', '(untitled thread)')}"
            )
    recent_decisions = summary.get("recentImportedDecisions", [])
    if isinstance(recent_decisions, list) and recent_decisions:
        lines.extend(["", "## Recent Imported Decisions", ""])
        for item in recent_decisions:
            lines.append(
                f"- {item.get('imported_at', '(time unknown)')} - `{item.get('semantic_role', 'specialist')}` - `{item.get('decision', 'unknown')}` - {item.get('summary', '(missing summary)')}"
            )
    resolution_topics = summary.get("resolutionTopics", [])
    if isinstance(resolution_topics, list) and resolution_topics:
        lines.extend(["", "## Resolution Topics", ""])
        for topic in resolution_topics:
            lines.append(
                f"- `{topic.get('semantic_role', 'specialist')}` / `{topic.get('topic_key', 'general')}` - {topic.get('topic_label', '(missing topic label)')}"
            )
            lines.append(f"  Prompt: `{topic.get('resolutionPromptPath', '(missing path)')}`")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- JSON summary: `{_project_relative_label(project, _consult_convergence_summary_path(project))}`",
            f"- Role memories: `{_project_relative_label(project, _consult_role_memory_dir(project))}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_consult_resolution_prompt(topic: dict) -> str:
    semantic_role = str(topic.get("semantic_role", "specialist")).strip() or "specialist"
    role_label = str(topic.get("role_label", "")).strip() or semantic_role.replace("_", " ").title()
    topic_key = str(topic.get("topic_key", "")).strip() or "general"
    topic_label = str(topic.get("topic_label", "")).strip() or topic_key
    lines = [
        f"You are resolving a conflicting manual consultation for the {role_label} role.",
        "",
        "Stay strictly on the bounded topic below. Do not redesign the wider project.",
        "",
        f"Semantic role: {semantic_role}",
        f"Topic key: {topic_key}",
        f"Topic label: {topic_label}",
        "",
        "ControlCoding detected conflicting imported decisions on this same topic.",
    ]
    decision_counts = topic.get("decision_counts", {})
    if isinstance(decision_counts, dict) and decision_counts:
        details = ", ".join(f"{decision}={count}" for decision, count in sorted(decision_counts.items()))
        lines.append(f"Observed decisions: {details}")
    unresolved = topic.get("unresolved_thread_ids", [])
    if isinstance(unresolved, list) and unresolved:
        lines.append(f"Unresolved thread ids: {', '.join(unresolved)}")
    recent_decisions = topic.get("recent_decisions", [])
    if isinstance(recent_decisions, list) and recent_decisions:
        lines.extend(["", "Recent conflicting inputs:", ""])
        for item in recent_decisions[-4:]:
            lines.append(
                f"- {item.get('imported_at', '(time unknown)')} - `{item.get('decision', 'unknown')}` - {item.get('summary', '(missing summary)')}"
            )
    lines.extend(
        [
            "",
            "Required resolution output:",
            "1. Which decision should become the current baseline for this topic, and why",
            "2. Which prior guidance should be superseded or downgraded",
            "3. What concrete next action ControlCoding should follow",
            "4. Any constraint changes needed to keep future consultations aligned",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _sync_consult_resolution_artifacts(project: Path, summary: dict) -> dict:
    resolution_topics: list[dict] = []
    for topic in summary.get("topicMerges", []):
        if not isinstance(topic, dict):
            continue
        if str(topic.get("status", "")).strip() != "conflicted":
            continue
        semantic_role = str(topic.get("semantic_role", "specialist")).strip() or "specialist"
        topic_key = str(topic.get("topic_key", "")).strip() or "general"
        topic_dir = _consult_resolution_topic_dir(project, semantic_role, topic_key)
        topic_dir.mkdir(parents=True, exist_ok=True)
        resolution_payload = {
            "schema_version": 1,
            "generated_at": _utc_now_iso(),
            "semantic_role": semantic_role,
            "role_label": str(topic.get("role_label", "")).strip(),
            "topic_key": topic_key,
            "topic_label": str(topic.get("topic_label", "")).strip(),
            "status": "resolution_required",
            "decision_counts": topic.get("decision_counts", {}),
            "unresolved_thread_ids": topic.get("unresolved_thread_ids", []),
            "latest_decision": topic.get("latest_decision", ""),
            "latest_summary": topic.get("latest_summary", ""),
            "latest_next_action": topic.get("latest_next_action", ""),
            "recent_decisions": topic.get("recent_decisions", []),
            "resolution_question": f"Resolve the conflicting guidance for topic '{str(topic.get('topic_label', '')).strip() or topic_key}'.",
        }
        state_path = _consult_resolution_state_path(project, semantic_role, topic_key)
        prompt_path = _consult_resolution_prompt_path(project, semantic_role, topic_key)
        _write_json_atomic(state_path, resolution_payload)
        prompt_path.write_text(_render_consult_resolution_prompt(topic), encoding="utf-8")
        resolution_topics.append(
            {
                **resolution_payload,
                "resolutionJsonPath": _project_relative_label(project, state_path),
                "resolutionPromptPath": _project_relative_label(project, prompt_path),
            }
        )
    summary["resolutionTopics"] = resolution_topics
    summary["resolutionTopicCount"] = len(resolution_topics)
    return summary


def _sync_consult_global_memory(project: Path) -> dict:
    threads = _load_consult_threads(project)
    results = _load_consult_results(project)
    roles = sorted(
        {
            _normalize_role_key(thread.get("semantic_role"))
            for thread in threads
            if _normalize_role_key(thread.get("semantic_role"))
        }
        | {
            _normalize_role_key(result.get("semantic_role"))
            for result in results
            if _normalize_role_key(result.get("semantic_role"))
        }
    )
    role_memories: list[dict] = []
    for role in roles:
        role_memory = _build_consult_role_memory(threads, results, role)
        role_memories.append(role_memory)
        _sync_consult_role_memory(project, role_memory)
    summary = _build_consult_convergence_summary(threads, results, role_memories)
    summary = _sync_consult_resolution_artifacts(project, summary)
    _write_json_atomic(_consult_convergence_summary_path(project), summary)
    _consult_convergence_summary_markdown_path(project).write_text(
        _render_consult_convergence_summary(project, summary),
        encoding="utf-8",
    )
    return summary


def _consult_status_payload(project: Path, role_filter: str = "") -> dict:
    engagement = _load_engagement_runtime_config(_control_plane_read_path(project, "cc_engagement.json"))
    specialist_paths = _normalize_specialist_paths(engagement.get("specialist_paths", []))
    normalized_filter = _normalize_role_key(role_filter)
    if normalized_filter:
        specialist_paths = [
            path for path in specialist_paths
            if (
                _normalize_role_key(path.get("role_id")) == normalized_filter
                or _normalize_role_key(path.get("label")) == normalized_filter
                or _specialist_semantic_role(path) == normalized_filter
            )
        ]
    packets = _load_consult_packets(project)
    results = _load_consult_results(project)
    threads = _load_consult_threads(project)
    role_memories = _load_consult_role_memories(project)
    convergence_summary = _read_json_object(_consult_convergence_summary_path(project))
    if normalized_filter:
        packets = [
            packet for packet in packets
            if (
                _normalize_role_key(packet.get("role_id")) == normalized_filter
                or _normalize_role_key(packet.get("role_label")) == normalized_filter
                or _normalize_role_key(packet.get("semantic_role")) == normalized_filter
            )
        ]
        results = [
            result for result in results
            if (
                _normalize_role_key(result.get("role_id")) == normalized_filter
                or _normalize_role_key(result.get("role_label")) == normalized_filter
                or _normalize_role_key(result.get("semantic_role")) == normalized_filter
            )
        ]
        threads = [
            thread for thread in threads
            if (
                _normalize_role_key(thread.get("role_id")) == normalized_filter
                or _normalize_role_key(thread.get("role_label")) == normalized_filter
                or _normalize_role_key(thread.get("semantic_role")) == normalized_filter
            )
        ]
        role_memories = [
            role_memory for role_memory in role_memories
            if _normalize_role_key(role_memory.get("semantic_role")) == normalized_filter
        ]
        convergence_summary = _build_consult_convergence_summary(threads, results, role_memories)

    execution_summary = {
        "human_mediated": 0,
        "cc_routed": 0,
        "auto_bounded": 0,
    }
    for path in specialist_paths:
        mode = str(path.get("execution_mode", "")).strip()
        if mode in execution_summary and path.get("active") is not False:
            execution_summary[mode] += 1

    packets_by_status: dict[str, int] = {}
    for packet in packets:
        status = str(packet.get("status", "created")).strip() or "created"
        packets_by_status[status] = packets_by_status.get(status, 0) + 1

    decisions_by_type: dict[str, int] = {}
    for result in results:
        decision = str(result.get("decision", "")).strip() or "unknown"
        decisions_by_type[decision] = decisions_by_type.get(decision, 0) + 1

    threads_by_status: dict[str, int] = {}
    for thread in threads:
        status = str(thread.get("status", "drafted")).strip() or "drafted"
        threads_by_status[status] = threads_by_status.get(status, 0) + 1

    return {
        "tier": engagement.get("tier", "core"),
        "roleFilter": normalized_filter or "",
        "activeSpecialistPaths": specialist_paths,
        "executionSummary": execution_summary,
        "packetCount": len(packets),
        "resultCount": len(results),
        "threadCount": len(threads),
        "roleMemoryCount": len(role_memories),
        "packetsByStatus": packets_by_status,
        "decisionsByType": decisions_by_type,
        "threadsByStatus": threads_by_status,
        "latestPacket": packets[-1] if packets else None,
        "latestResult": results[-1] if results else None,
        "latestThread": threads[-1] if threads else None,
        "latestRoleMemory": role_memories[-1] if role_memories else None,
        "convergenceSummary": convergence_summary if isinstance(convergence_summary, dict) else None,
        "resolutionTopicCount": int(convergence_summary.get("resolutionTopicCount", 0)) if isinstance(convergence_summary, dict) else 0,
        "latestResolutionTopic": (
            (convergence_summary.get("resolutionTopics") or [])[-1]
            if isinstance(convergence_summary, dict) and (convergence_summary.get("resolutionTopics") or [])
            else None
        ),
        "consultLogPath": _project_relative_label(project, _consult_log_path(project)),
        "packetDir": _project_relative_label(project, _consult_packets_dir(project)),
        "resultDir": _project_relative_label(project, _consult_results_dir(project)),
        "threadDir": _project_relative_label(project, _consult_threads_dir(project)),
        "roleMemoryDir": _project_relative_label(project, _consult_role_memory_dir(project)),
        "resolutionDir": _project_relative_label(project, _consult_resolution_dir(project)),
        "convergenceSummaryPath": _project_relative_label(project, _consult_convergence_summary_path(project)),
        "convergenceSummaryMarkdownPath": _project_relative_label(project, _consult_convergence_summary_markdown_path(project)),
    }


def cmd_consult_packet_create(project: Path,
                              role: str,
                              objective: str,
                              questions: list[str],
                              context_summary: str = "",
                              constraints: list[str] | None = None,
                              expected_answer_shape: str = "",
                              topic_key: str = "",
                              thread_id: str = "",
                              json_output: bool = False) -> int:
    engagement_path = _control_plane_read_path(project, "cc_engagement.json")
    engagement = _load_engagement_runtime_config(engagement_path)
    specialist_paths = _normalize_specialist_paths(engagement.get("specialist_paths", []))
    specialist_path = _find_specialist_path(specialist_paths, role)

    if specialist_path is None:
        message = (
            "No active specialist path matches "
            f"'{role}'. Configure an Agents/Studio specialist matrix first."
        )
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    if specialist_path.get("execution_mode") != "human_mediated":
        message = (
            "consult-packet create only supports human-mediated specialist paths. "
            f"'{specialist_path['label']}' is configured as {specialist_path.get('execution_mode')}."
        )
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    cleaned_objective, error = _normalize_consult_text(
        objective,
        field_name="Objective",
        max_length=_CONSULT_OBJECTIVE_LIMIT,
    )
    if error:
        if json_output:
            print(json.dumps({"ok": False, "error": error}, indent=2, ensure_ascii=False))
        else:
            fail(error)
        return 1
    cleaned_questions, error = _normalize_consult_list(
        questions,
        field_name="question",
        required=True,
    )
    if error:
        if json_output:
            print(json.dumps({"ok": False, "error": error}, indent=2, ensure_ascii=False))
        else:
            fail(error)
        return 1
    cleaned_context_summary, error = _normalize_consult_text(
        context_summary,
        field_name="Context summary",
        max_length=_CONSULT_CONTEXT_SUMMARY_LIMIT,
        required=False,
    )
    if error:
        if json_output:
            print(json.dumps({"ok": False, "error": error}, indent=2, ensure_ascii=False))
        else:
            fail(error)
        return 1
    cleaned_constraints, error = _normalize_consult_list(
        constraints,
        field_name="constraint",
        required=False,
    )
    if error:
        if json_output:
            print(json.dumps({"ok": False, "error": error}, indent=2, ensure_ascii=False))
        else:
            fail(error)
        return 1
    cleaned_expected_answer_shape, error = _normalize_consult_text(
        expected_answer_shape,
        field_name="Expected answer shape",
        max_length=_CONSULT_EXPECTED_ANSWER_LIMIT,
        required=False,
    )
    if error:
        if json_output:
            print(json.dumps({"ok": False, "error": error}, indent=2, ensure_ascii=False))
        else:
            fail(error)
        return 1
    explicit_topic_key = _normalize_consult_topic_key(topic_key) if str(topic_key or "").strip() else ""
    normalized_topic_key = explicit_topic_key or _normalize_consult_topic_key("", cleaned_objective)
    topic_label = " ".join(str(topic_key or cleaned_objective).split()) or normalized_topic_key
    resolved_thread_id = str(thread_id or "").strip()
    thread_record = None
    if resolved_thread_id:
        thread_record = _load_consult_thread(project, resolved_thread_id)
        if not thread_record:
            message = f"Consultation thread not found: {resolved_thread_id}"
            if json_output:
                print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
            else:
                fail(message)
            return 1
        same_role = _normalize_role_key(thread_record.get("role_id")) == _normalize_role_key(specialist_path.get("role_id"))
        same_semantic_role = _normalize_role_key(thread_record.get("semantic_role")) == _specialist_semantic_role(specialist_path)
        if not same_role and not same_semantic_role:
            message = (
                "Consultation thread role mismatch. "
                f"Thread '{resolved_thread_id}' is bound to "
                f"{thread_record.get('role_label') or thread_record.get('role_id')}, not "
                f"{specialist_path.get('label') or specialist_path.get('role_id')}."
            )
            if json_output:
                print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
            else:
                fail(message)
            return 1
        existing_topic_key = _normalize_consult_topic_key(thread_record.get("topic_key", ""), thread_record.get("topic_label", ""))
        if existing_topic_key and explicit_topic_key and explicit_topic_key != existing_topic_key:
            message = (
                "Consultation thread topic mismatch. "
                f"Thread '{resolved_thread_id}' is bound to topic '{existing_topic_key}', not '{explicit_topic_key}'."
            )
            if json_output:
                print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
            else:
                fail(message)
            return 1
        normalized_topic_key = existing_topic_key or normalized_topic_key
        topic_label = str(thread_record.get("topic_label", "")).strip() or topic_label
    packet_id = _build_consult_artifact_id(str(specialist_path.get("role_id", role)), "packet")
    packet = {
        "schema_version": 1,
        "packet_id": packet_id,
        "created_at": _utc_now_iso(),
        "tier": engagement.get("tier", "agents"),
        "role_id": specialist_path["role_id"],
        "role_label": specialist_path["label"],
        "semantic_role": _specialist_semantic_role(specialist_path),
        "path_type": specialist_path.get("path_type", "specialist"),
        "permission": specialist_path.get("permission", "user_mediated"),
        "execution_mode": specialist_path.get("execution_mode", "human_mediated"),
        "backend": specialist_path.get("backend", ""),
        "model": specialist_path.get("model", ""),
        "topic_key": normalized_topic_key,
        "topic_label": topic_label,
        "objective": cleaned_objective,
        "local_context_summary": cleaned_context_summary,
        "constraints": cleaned_constraints,
        "questions": cleaned_questions,
        "expected_answer_shape": cleaned_expected_answer_shape,
        "status": "created",
        "thread_id": resolved_thread_id,
    }
    if thread_record is None:
        thread_record = _create_consult_thread(
            project,
            packet_id=packet_id,
            specialist_path=specialist_path,
            engagement=engagement,
            objective=cleaned_objective,
            context_summary=cleaned_context_summary,
            constraints=cleaned_constraints,
            questions=cleaned_questions,
            topic_key=normalized_topic_key,
            topic_label=topic_label,
        )
        packet["thread_id"] = thread_record["thread_id"]
    else:
        now = packet["created_at"]
        thread_record["updated_at"] = now
        thread_record["status"] = "drafted"
        thread_record["topic_key"] = normalized_topic_key
        thread_record["topic_label"] = topic_label
        thread_record["latest_packet_id"] = packet_id
        thread_record["latest_objective"] = cleaned_objective
        thread_record["latest_context_summary"] = cleaned_context_summary
        thread_record["constraints"] = _merge_unique_items(thread_record.get("constraints", []), cleaned_constraints)
        thread_record["open_questions"] = cleaned_questions
        packet_ids = list(thread_record.get("packet_ids", []))
        packet_ids.append(packet_id)
        thread_record["packet_ids"] = packet_ids
        _sync_consult_thread_artifacts(project, thread_record)
    packet_path = _consult_packets_dir(project) / f"{packet_id}.json"
    _write_json_atomic(packet_path, packet)
    convergence_summary = _sync_consult_global_memory(project)
    semantic_role = str(thread_record.get("semantic_role", "")).strip() or _specialist_semantic_role(specialist_path)
    response_payload = {
        "ok": True,
        "packetId": packet_id,
        "packetPath": _project_relative_label(project, packet_path),
        "threadId": thread_record.get("thread_id", ""),
        "topicKey": normalized_topic_key,
        "threadPath": _project_relative_label(project, _consult_thread_state_path(project, str(thread_record.get("thread_id", "")))),
        "resumePromptPath": _project_relative_label(project, _consult_thread_resume_path(project, str(thread_record.get("thread_id", "")))),
        "roleMemoryPath": _project_relative_label(project, _consult_role_memory_markdown_path(project, semantic_role)),
        "convergenceSummaryPath": _project_relative_label(project, _consult_convergence_summary_markdown_path(project)),
        "divergenceSignalCount": convergence_summary.get("divergenceSignalCount", 0),
        "specialistPath": specialist_path,
    }
    if json_output:
        print(json.dumps(response_payload, indent=2, ensure_ascii=False))
        return 0

    ok(
        "Created human-mediated consultation packet "
        f"for {specialist_path['label']}"
    )
    info(
        "Packet: "
        f"{_project_relative_label(project, packet_path)}"
    )
    info(
        "Thread: "
        f"{_project_relative_label(project, _consult_thread_state_path(project, str(thread_record.get('thread_id', ''))))}"
    )
    info(
        "Resume prompt: "
        f"{_project_relative_label(project, _consult_thread_resume_path(project, str(thread_record.get('thread_id', ''))))}"
    )
    info(
        "Role memory: "
        f"{_project_relative_label(project, _consult_role_memory_markdown_path(project, semantic_role))}"
    )
    info(
        "Convergence summary: "
        f"{_project_relative_label(project, _consult_convergence_summary_markdown_path(project))}"
    )
    info(
        "Next: carry out the consultation manually, then import the concise outcome with "
        f"`cc consult-result import --packet-id {packet_id} ...`"
    )
    return 0


def cmd_consult_result_import(project: Path,
                              packet_id: str,
                              summary: str,
                              decision: str,
                              rationale_summary: str,
                              next_action: str,
                              constraints: list[str] | None = None,
                              evidence: list[str] | None = None,
                              source: str = "manual_external_consult",
                              json_output: bool = False) -> int:
    packet_path = _consult_packet_path(project, packet_id)
    if not packet_path.exists():
        message = f"Consultation packet not found: {packet_id}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    packet = _read_json_object(packet_path)
    if not packet:
        message = f"Consultation packet is invalid: {_project_relative_label(project, packet_path)}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in _VALID_CONSULT_DECISIONS:
        message = (
            "Decision must be one of "
            + ", ".join(sorted(_VALID_CONSULT_DECISIONS))
        )
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    summary_text, error = _normalize_consult_text(
        summary,
        field_name="Summary",
        max_length=_CONSULT_SUMMARY_LIMIT,
    )
    if error:
        message = error
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    rationale_text, error = _normalize_consult_text(
        rationale_summary,
        field_name="Rationale summary",
        max_length=_CONSULT_RATIONALE_LIMIT,
    )
    if error:
        message = error
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    next_action_text, error = _normalize_consult_text(
        next_action,
        field_name="Next action",
        max_length=_CONSULT_NEXT_ACTION_LIMIT,
    )
    if error:
        message = error
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    cleaned_constraints, error = _normalize_consult_list(
        constraints,
        field_name="constraint",
        required=False,
    )
    if error:
        message = error
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    cleaned_evidence, error = _normalize_consult_list(
        evidence,
        field_name="evidence",
        required=False,
    )
    if error:
        message = error
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    result_id = _build_consult_artifact_id(str(packet.get("role_id", "specialist")), "result")
    imported_at = _utc_now_iso()
    result_payload = {
        "schema_version": 1,
        "result_id": result_id,
        "imported_at": imported_at,
        "source_packet_id": packet.get("packet_id", packet_id),
        "role_id": packet.get("role_id", ""),
        "role_label": packet.get("role_label") or packet.get("role_id", ""),
        "semantic_role": packet.get("semantic_role") or _normalize_role_key(packet.get("role_id", "")) or "specialist",
        "path_type": packet.get("path_type", "specialist"),
        "permission": packet.get("permission", "user_mediated"),
        "execution_mode": packet.get("execution_mode", "human_mediated"),
        "backend": packet.get("backend", ""),
        "model": packet.get("model", ""),
        "topic_key": packet.get("topic_key", ""),
        "topic_label": packet.get("topic_label", ""),
        "decision": normalized_decision,
        "imported_answer_summary": summary_text,
        "constraints": cleaned_constraints,
        "evidence_summary": cleaned_evidence,
        "rationale_summary": rationale_text,
        "next_action": next_action_text,
        "source": str(source or "manual_external_consult").strip() or "manual_external_consult",
    }
    result_path = _consult_results_dir(project) / f"{result_id}.json"
    _write_json_atomic(result_path, result_payload)

    packet["status"] = "imported"
    packet["last_result_id"] = result_id
    packet["last_imported_at"] = imported_at
    _write_json_atomic(packet_path, packet)

    thread_id = str(packet.get("thread_id", "")).strip()
    if thread_id:
        thread = _load_consult_thread(project, thread_id)
        if thread:
            thread["updated_at"] = imported_at
            thread["status"] = _consult_thread_status_from_decision(normalized_decision)
            thread["latest_result_id"] = result_id
            thread["latest_packet_id"] = packet.get("packet_id", packet_id)
            thread["topic_key"] = str(packet.get("topic_key", "")).strip() or str(thread.get("topic_key", "")).strip()
            thread["topic_label"] = str(packet.get("topic_label", "")).strip() or str(thread.get("topic_label", "")).strip()
            thread["latest_objective"] = str(packet.get("objective", "")).strip() or str(thread.get("latest_objective", ""))
            thread["latest_context_summary"] = str(packet.get("local_context_summary", "")).strip() or str(thread.get("latest_context_summary", ""))
            thread["constraints"] = _merge_unique_items(thread.get("constraints", []), cleaned_constraints)
            result_ids = list(thread.get("result_ids", []))
            result_ids.append(result_id)
            thread["result_ids"] = result_ids
            decision_history = list(thread.get("decision_history", []))
            decision_history.append(
                {
                    "thread_id": thread_id,
                    "topic_key": str(packet.get("topic_key", "")).strip(),
                    "topic_label": str(packet.get("topic_label", "")).strip(),
                    "result_id": result_id,
                    "packet_id": packet.get("packet_id", packet_id),
                    "decision": normalized_decision,
                    "summary": summary_text,
                    "rationale_summary": rationale_text,
                    "next_action": next_action_text,
                    "imported_at": imported_at,
                    "source": str(source or "manual_external_consult").strip() or "manual_external_consult",
                }
            )
            thread["decision_history"] = decision_history
            if normalized_decision == "partial":
                thread["open_questions"] = list(packet.get("questions", []))
            else:
                thread["open_questions"] = []
            _append_jsonl(
                _consult_thread_decisions_path(project, thread_id),
                {
                    "timestamp": imported_at,
                    "result_id": result_id,
                    "packet_id": packet.get("packet_id", packet_id),
                    "topic_key": str(packet.get("topic_key", "")).strip(),
                    "topic_label": str(packet.get("topic_label", "")).strip(),
                    "decision": normalized_decision,
                    "summary": summary_text,
                    "rationale_summary": rationale_text,
                    "next_action": next_action_text,
                    "source": str(source or "manual_external_consult").strip() or "manual_external_consult",
                },
            )
            _sync_consult_thread_artifacts(project, thread)

    _append_jsonl(
        _consult_log_path(project),
        {
            "timestamp": imported_at,
            "role": packet.get("semantic_role") or _normalize_role_key(packet.get("role_id", "")) or "specialist",
            "role_id": packet.get("role_id", ""),
            "backend": packet.get("backend", ""),
            "model": packet.get("model", ""),
            "execution_mode": packet.get("execution_mode", "human_mediated"),
            "permission": packet.get("permission", "user_mediated"),
            "packet_id": packet.get("packet_id", packet_id),
            "result_id": result_id,
            "prompt_chars": len(json.dumps(packet, ensure_ascii=False)),
            "response_chars": len(summary_text) + len(rationale_text),
            "problem_summary": _truncate_summary(str(packet.get("objective", ""))),
            "response_summary": _truncate_summary(summary_text),
            "decision": normalized_decision,
            "rationale_summary": _truncate_summary(rationale_text, limit=320),
            "evidence_summary": cleaned_evidence,
            "constraints_summary": cleaned_constraints,
            "next_action": next_action_text,
            "source": str(source or "manual_external_consult").strip() or "manual_external_consult",
            "call_number": "manual_import",
            "calls_remaining": "manual",
        },
    )
    convergence_summary = _sync_consult_global_memory(project)
    semantic_role = str(packet.get("semantic_role", "")).strip() or _normalize_role_key(packet.get("role_id", "")) or "specialist"

    response_payload = {
        "ok": True,
        "packetId": packet.get("packet_id", packet_id),
        "packetPath": _project_relative_label(project, packet_path),
        "resultId": result_id,
        "resultPath": _project_relative_label(project, result_path),
        "topicKey": str(packet.get("topic_key", "")).strip(),
        "roleMemoryPath": _project_relative_label(project, _consult_role_memory_markdown_path(project, semantic_role)),
        "convergenceSummaryPath": _project_relative_label(project, _consult_convergence_summary_markdown_path(project)),
        "divergenceSignalCount": convergence_summary.get("divergenceSignalCount", 0),
    }
    if thread_id:
        response_payload["threadId"] = thread_id
        response_payload["threadPath"] = _project_relative_label(project, _consult_thread_state_path(project, thread_id))
        response_payload["resumePromptPath"] = _project_relative_label(project, _consult_thread_resume_path(project, thread_id))
    if json_output:
        print(json.dumps(response_payload, indent=2, ensure_ascii=False))
        return 0

    ok(
        "Imported human-mediated consultation result "
        f"for {packet.get('role_label') or packet.get('role_id') or 'specialist'}"
    )
    info(
        "Result: "
        f"{_project_relative_label(project, result_path)}"
    )
    if thread_id:
        info(
            "Updated thread: "
            f"{_project_relative_label(project, _consult_thread_state_path(project, thread_id))}"
        )
        info(
            "Updated resume prompt: "
            f"{_project_relative_label(project, _consult_thread_resume_path(project, thread_id))}"
        )
    info(
        "Role memory: "
        f"{_project_relative_label(project, _consult_role_memory_markdown_path(project, semantic_role))}"
    )
    info(
        "Convergence summary: "
        f"{_project_relative_label(project, _consult_convergence_summary_markdown_path(project))}"
    )
    return 0


def cmd_consult_packet_show(project: Path,
                            packet_id: str,
                            json_output: bool = False) -> int:
    packet_path = _consult_packet_path(project, packet_id)
    if not packet_path.exists():
        message = f"Consultation packet not found: {packet_id}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    packet = _read_json_object(packet_path)
    if not packet:
        message = f"Consultation packet is invalid: {_project_relative_label(project, packet_path)}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    latest_result = None
    latest_result_id = str(packet.get("last_result_id", "")).strip()
    if latest_result_id:
        result_path = _consult_result_path(project, latest_result_id)
        latest_result = _read_json_object(result_path)
    thread = None
    thread_id = str(packet.get("thread_id", "")).strip()
    if thread_id:
        thread = _load_consult_thread(project, thread_id)
    semantic_role = str(packet.get("semantic_role", "")).strip() or _normalize_role_key(packet.get("role_id", "")) or "specialist"

    payload = {
        "packet": packet,
        "packetPath": _project_relative_label(project, packet_path),
        "latestResult": latest_result if latest_result else None,
        "thread": thread if thread else None,
        "threadPath": _project_relative_label(project, _consult_thread_state_path(project, thread_id)) if thread_id else "",
        "resumePromptPath": _project_relative_label(project, _consult_thread_resume_path(project, thread_id)) if thread_id else "",
        "roleMemoryPath": _project_relative_label(project, _consult_role_memory_markdown_path(project, semantic_role)),
        "convergenceSummaryPath": _project_relative_label(project, _consult_convergence_summary_markdown_path(project)),
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Packet: {packet.get('packet_id', packet_id)}")
    print(
        "Specialist path: "
        f"{packet.get('role_label') or packet.get('role_id', 'specialist')} "
        f"({packet.get('execution_mode', 'human_mediated')}, {packet.get('backend', '(missing backend)')})"
    )
    print(f"Status: {packet.get('status', 'created')}")
    if thread:
        print(f"Thread: {thread.get('thread_id', thread_id)} / {thread.get('status', 'drafted')}")
    print(f"Topic: {packet.get('topic_key', '') or 'general'}")
    print(f"Objective: {packet.get('objective', '')}")
    if packet.get("local_context_summary"):
        print(f"Context summary: {packet.get('local_context_summary')}")
    questions = packet.get("questions", [])
    if isinstance(questions, list) and questions:
        print("Questions:")
        for question in questions:
            print(f"  - {question}")
    constraints = packet.get("constraints", [])
    if isinstance(constraints, list) and constraints:
        print("Constraints:")
        for constraint in constraints:
            print(f"  - {constraint}")
    if packet.get("expected_answer_shape"):
        print(f"Expected answer shape: {packet.get('expected_answer_shape')}")
    print(f"Artifact: {_project_relative_label(project, packet_path)}")
    if thread_id:
        print(f"Resume prompt: {_project_relative_label(project, _consult_thread_resume_path(project, thread_id))}")
    print(f"Role memory: {_project_relative_label(project, _consult_role_memory_markdown_path(project, semantic_role))}")
    print(f"Convergence summary: {_project_relative_label(project, _consult_convergence_summary_markdown_path(project))}")
    if latest_result:
        print(
            "Latest imported result: "
            f"{latest_result.get('decision', 'unknown')} / {latest_result.get('next_action', '')}"
        )
    else:
        print("Latest imported result: none")
    return 0


def cmd_consult_status(project: Path,
                       role: str = "",
                       json_output: bool = False) -> int:
    payload = _consult_status_payload(project, role_filter=role)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Consult status: tier={payload['tier']}")
    if payload["roleFilter"]:
        print(f"Role filter: {payload['roleFilter']}")
    summary = payload["executionSummary"]
    print(
        "Execution modes: "
        f"human_mediated={summary['human_mediated']}, "
        f"cc_routed={summary['cc_routed']}, "
        f"auto_bounded={summary['auto_bounded']}"
    )
    print(
        "Artifacts: "
        f"packets={payload['packetCount']}, "
        f"results={payload['resultCount']}, "
        f"threads={payload['threadCount']}, "
        f"roles={payload['roleMemoryCount']}, "
        f"resolutions={payload['resolutionTopicCount']}"
    )
    packets_by_status = payload["packetsByStatus"]
    if packets_by_status:
        details = ", ".join(f"{status}={count}" for status, count in sorted(packets_by_status.items()))
        print(f"Packet states: {details}")
    decisions_by_type = payload["decisionsByType"]
    if decisions_by_type:
        details = ", ".join(f"{decision}={count}" for decision, count in sorted(decisions_by_type.items()))
        print(f"Imported decisions: {details}")
    threads_by_status = payload["threadsByStatus"]
    if threads_by_status:
        details = ", ".join(f"{status}={count}" for status, count in sorted(threads_by_status.items()))
        print(f"Thread states: {details}")
    active_paths = payload["activeSpecialistPaths"]
    if active_paths:
        print("Active specialist paths:")
        for path in active_paths:
            label = str(path.get("label") or path.get("role_id") or "specialist")
            backend = str(path.get("backend", "")).strip() or "(missing backend)"
            print(
                f"  - {label}: {path.get('execution_mode', 'unknown')} / "
                f"{path.get('permission', 'unknown')} / {backend}"
            )
    latest_packet = payload["latestPacket"]
    if latest_packet:
        print(
            "Latest packet: "
            f"{latest_packet.get('packet_id', '')} / {latest_packet.get('role_label') or latest_packet.get('role_id', '')} / {latest_packet.get('status', 'created')}"
        )
    latest_result = payload["latestResult"]
    if latest_result:
        print(
            "Latest result: "
            f"{latest_result.get('result_id', '')} / {latest_result.get('decision', 'unknown')} / {latest_result.get('next_action', '')}"
        )
    latest_thread = payload["latestThread"]
    if latest_thread:
        print(
            "Latest thread: "
            f"{latest_thread.get('thread_id', '')} / {latest_thread.get('role_label') or latest_thread.get('role_id', '')} / {latest_thread.get('status', 'drafted')}"
        )
    latest_role_memory = payload["latestRoleMemory"]
    if latest_role_memory:
        print(
            "Latest role memory: "
            f"{latest_role_memory.get('semantic_role', 'specialist')} / unresolved={len(latest_role_memory.get('unresolvedThreadIds', []))} / divergence={len(latest_role_memory.get('divergenceSignals', []))}"
        )
    convergence_summary = payload["convergenceSummary"]
    if isinstance(convergence_summary, dict):
        print(
            "Convergence summary: "
            f"pending={convergence_summary.get('pendingThreadCount', 0)} / divergence={convergence_summary.get('divergenceSignalCount', 0)}"
        )
    latest_resolution = payload["latestResolutionTopic"]
    if latest_resolution:
        print(
            "Latest resolution topic: "
            f"{latest_resolution.get('semantic_role', 'specialist')} / {latest_resolution.get('topic_key', 'general')} / {latest_resolution.get('topic_label', '')}"
        )
    print(f"Consult log: {payload['consultLogPath']}")
    print(f"Role memory dir: {payload['roleMemoryDir']}")
    print(f"Resolution dir: {payload['resolutionDir']}")
    print(f"Convergence summary doc: {payload['convergenceSummaryMarkdownPath']}")
    return 0


def cmd_consult_resolution_show(project: Path,
                                role: str,
                                topic_key: str,
                                json_output: bool = False) -> int:
    summary = _read_json_object(_consult_convergence_summary_path(project))
    if not isinstance(summary, dict):
        message = "No convergence summary is available yet. Import at least one manual consultation result first."
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    normalized_role = _normalize_role_key(role)
    normalized_topic = _normalize_consult_topic_key(topic_key, topic_key)
    resolution_topics = summary.get("resolutionTopics", [])
    if not isinstance(resolution_topics, list):
        resolution_topics = []
    topic = next(
        (
            item for item in resolution_topics
            if _normalize_role_key(item.get("semantic_role")) == normalized_role
            and _normalize_consult_topic_key(item.get("topic_key", ""), item.get("topic_label", "")) == normalized_topic
        ),
        None,
    )
    if topic is None:
        message = (
            "No resolution topic matches "
            f"role '{role}' and topic '{topic_key}'."
        )
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    payload = {
        "ok": True,
        "resolutionTopic": topic,
        "resolutionJsonPath": topic.get("resolutionJsonPath", ""),
        "resolutionPromptPath": topic.get("resolutionPromptPath", ""),
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(
        "Resolution topic: "
        f"{topic.get('semantic_role', 'specialist')} / {topic.get('topic_key', 'general')} / {topic.get('topic_label', '')}"
    )
    print(f"Prompt: {topic.get('resolutionPromptPath', '')}")
    decision_counts = topic.get("decision_counts", {})
    if isinstance(decision_counts, dict) and decision_counts:
        details = ", ".join(f"{decision}={count}" for decision, count in sorted(decision_counts.items()))
        print(f"Observed decisions: {details}")
    unresolved = topic.get("unresolved_thread_ids", [])
    if isinstance(unresolved, list) and unresolved:
        print(f"Unresolved threads: {', '.join(unresolved)}")
    return 0


def _load_gateway_surface_context(project: Path) -> tuple[str, str]:
    gateway = _read_json_object(_control_plane_read_path(project, "gateway_config.json"))
    ui_mode = str(gateway.get("uiMode", "visualizer"))
    if ui_mode not in _GATEWAY_VALID_UI_MODES:
        ui_mode = "visualizer"
    user_host, _enabled_hosts = _normalize_gateway_hosts(gateway)
    return ui_mode, user_host


def _default_surface_label(surface_type: str) -> str:
    if surface_type == "official_host":
        return "Official Host"
    if surface_type == "cc_ui_api":
        return "CC UI (API Studio)"
    return "CC UI (Local Studio)"


def _format_user_host_label(user_host: str) -> str:
    mapping = {
        "claude_code": "Claude Code",
        "codex_cli": "Codex CLI",
        "cursor": "Cursor",
        "windsurf": "Windsurf",
        "vscode": "VS Code",
        "cline": "Cline",
        "gemini_cli": "Gemini CLI",
        "other": "Other",
    }
    return mapping.get(user_host, "Other")


def _normalize_surface_record(raw: dict) -> dict | None:
    surface_type = str(raw.get("surfaceType", "")).strip()
    surface_id = str(raw.get("surfaceId", "")).strip()
    if surface_type not in VALID_SURFACE_TYPES or not surface_id:
        return None
    role = str(raw.get("role", "observer")).strip()
    desired_role = str(raw.get("desiredRole", "primary")).strip()
    now = _utc_now_iso()
    return {
        "surfaceId": surface_id,
        "surfaceType": surface_type,
        "label": str(raw.get("label") or _default_surface_label(surface_type)).strip() or _default_surface_label(surface_type),
        "role": role if role in VALID_SURFACE_ROLES else "observer",
        "desiredRole": desired_role if desired_role in VALID_SURFACE_ROLES else "primary",
        "active": raw.get("active", True) is not False,
        "synthetic": raw.get("synthetic", False) is True,
        "attachedAt": str(raw.get("attachedAt") or now),
        "lastSeenAt": str(raw.get("lastSeenAt") or str(raw.get("attachedAt") or now)),
    }


def _normalize_takeover_request(raw: object, surfaces: list[dict], ui_mode: str) -> dict | None:
    if ui_mode == "visualizer" or not isinstance(raw, dict):
        return None
    request_id = str(raw.get("requestId", "")).strip()
    requested_by = str(raw.get("requestedBySurfaceId", "")).strip()
    requested_label = str(raw.get("requestedByLabel", "")).strip()
    requested_type = str(raw.get("requestedByType", "")).strip()
    if not request_id or not requested_by or not requested_label or requested_type not in VALID_SURFACE_TYPES:
        return None
    requester = next((item for item in surfaces if item["surfaceId"] == requested_by), None)
    if requester is None:
        return None
    current_primary_id = str(raw.get("currentPrimarySurfaceId", "")).strip()
    current_primary = None
    if current_primary_id:
        current_primary = next((item for item in surfaces if item["surfaceId"] == current_primary_id), None)
    if current_primary is None:
        current_primary = next((item for item in surfaces if item["role"] == "primary"), None)
    if current_primary is None or current_primary["surfaceId"] == requester["surfaceId"]:
        return None
    return {
        "requestId": request_id,
        "requestedBySurfaceId": requester["surfaceId"],
        "requestedByLabel": requester["label"],
        "requestedByType": requester["surfaceType"],
        "currentPrimarySurfaceId": current_primary["surfaceId"],
        "currentPrimaryLabel": current_primary["label"],
        "currentPrimaryType": current_primary["surfaceType"],
        "reason": str(raw.get("reason", "")).strip() or None,
        "status": "pending",
        "requestedAt": str(raw.get("requestedAt") or _utc_now_iso()),
    }


def _sort_surfaces_for_lock(surfaces: list[dict]) -> list[dict]:
    return sorted(
        surfaces,
        key=lambda item: (0 if item.get("synthetic") else 1, item.get("label", "")),
    )


def _pick_explicit_official_host_primary(surfaces: list[dict]) -> dict | None:
    locked_hosts = sorted(
        [
            item
            for item in surfaces
            if item["surfaceType"] == "official_host" and item["role"] == "primary"
        ],
        key=lambda item: item["attachedAt"],
    )
    return locked_hosts[0] if locked_hosts else None


def _pick_desired_primary_surface(surfaces: list[dict]) -> dict | None:
    explicit = sorted(
        [item for item in surfaces if item["desiredRole"] == "primary"],
        key=lambda item: item["attachedAt"],
    )
    if explicit:
        return explicit[0]
    ordered = sorted(surfaces, key=lambda item: item["attachedAt"])
    return ordered[0] if ordered else None


def _format_surface_conflict_labels(surfaces: list[dict]) -> str:
    return ", ".join(sorted(str(item.get("label", "?")) for item in surfaces))


def _detect_surface_authority_conflict(
    surfaces: list[dict],
    ui_mode: str,
    takeover_request: dict | None,
) -> str | None:
    explicit_official_host_primaries = [
        item
        for item in surfaces
        if item["surfaceType"] == "official_host" and item["role"] == "primary"
    ]
    if len(explicit_official_host_primaries) > 1:
        return (
            "Ambiguous surface authority: multiple official hosts are marked primary "
            f"({_format_surface_conflict_labels(explicit_official_host_primaries)})."
        )

    if ui_mode == "visualizer":
        return None

    explicit_primaries = [item for item in surfaces if item["role"] == "primary"]
    if len(explicit_primaries) > 1:
        return (
            "Ambiguous surface authority: multiple surfaces are marked primary "
            f"({_format_surface_conflict_labels(explicit_primaries)})."
        )

    if isinstance(takeover_request, dict) and takeover_request.get("status") == "pending":
        return None

    desired_primaries = [item for item in surfaces if item["desiredRole"] == "primary"]
    if len(desired_primaries) > 1:
        return (
            "Ambiguous surface authority: multiple surfaces still claim primary intent "
            f"({_format_surface_conflict_labels(desired_primaries)})."
        )

    return None


def _build_surface_rationale(
    authority_mode: str,
    primary: dict | None,
    surfaces: list[dict],
    takeover_request: dict | None,
    blocked_reason: str | None = None,
) -> str:
    if authority_mode == "blocked":
        return blocked_reason or "Surface authority is blocked until the ambiguous lock state is repaired."
    if authority_mode == "host_locked":
        return "Official host owns the primary chat and orchestration path. CC UI surfaces remain observer-only."
    if authority_mode == "takeover_pending" and takeover_request:
        current_label = takeover_request.get("currentPrimaryLabel") or "Current primary"
        return (
            f"{takeover_request['requestedByLabel']} requested primary control. "
            f"{current_label} must approve or deny the takeover before authority changes."
        )
    if primary is None:
        return "No primary surface is attached. The next active CC surface can claim dispatch authority."
    observer_count = max(len(surfaces) - 1, 0)
    if observer_count > 0:
        return f"{primary['label']} owns dispatch authority. {observer_count} other surface(s) are observer-only."
    return f"{primary['label']} owns dispatch authority for this CC session."


def _reconcile_surface_lock(project: Path, raw_state: dict) -> dict:
    ui_mode, user_host = _load_gateway_surface_context(project)
    now = _utc_now_iso()
    stale_before = datetime.datetime.now(datetime.timezone.utc).timestamp() - SURFACE_STALE_SECONDS
    raw_surfaces = raw_state.get("surfaces", [])
    surfaces = []
    if isinstance(raw_surfaces, list):
        for item in raw_surfaces:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_surface_record(item)
            if normalized is None or normalized["synthetic"]:
                continue
            last_seen = _parse_iso_timestamp(normalized["lastSeenAt"])
            if last_seen is not None and last_seen < stale_before:
                continue
            normalized["active"] = True
            surfaces.append(normalized)

    takeover_request = _normalize_takeover_request(raw_state.get("takeoverRequest"), surfaces, ui_mode)
    authority_mode = "stable"
    primary = None
    blocked_reason = _detect_surface_authority_conflict(surfaces, ui_mode, takeover_request)

    explicit_official_host = _pick_explicit_official_host_primary(surfaces)
    if blocked_reason is not None:
        surfaces = [
            {
                **item,
                "role": "observer",
            }
            for item in surfaces
        ]
        authority_mode = "blocked"
        takeover_request = None
    elif explicit_official_host is not None:
        surfaces = [
            {**item, "role": "primary" if item["surfaceId"] == explicit_official_host["surfaceId"] else "observer"}
            for item in surfaces
        ]
        primary = explicit_official_host
        authority_mode = "host_locked"
        takeover_request = None
    elif ui_mode == "visualizer":
        synthetic_host = {
            "surfaceId": f"official_host:{user_host}",
            "surfaceType": "official_host",
            "label": f"Official Host ({_format_user_host_label(user_host)})",
            "role": "primary",
            "desiredRole": "primary",
            "active": True,
            "synthetic": True,
            "attachedAt": now,
            "lastSeenAt": now,
        }
        surfaces = [synthetic_host] + [{**item, "role": "observer"} for item in surfaces]
        primary = synthetic_host
        authority_mode = "host_locked"
        takeover_request = None
    elif takeover_request is not None:
        current_primary_id = takeover_request.get("currentPrimarySurfaceId")
        surfaces = [
            {
                **item,
                "role": "primary" if item["surfaceId"] == current_primary_id else "observer",
            }
            for item in surfaces
        ]
        primary = next((item for item in surfaces if item["surfaceId"] == current_primary_id), None)
        authority_mode = "takeover_pending"
    else:
        current_primary = next((item for item in surfaces if item["role"] == "primary"), None)
        desired_primary = current_primary or _pick_desired_primary_surface(surfaces)
        if desired_primary is not None:
            surfaces = [
                {
                    **item,
                    "role": "primary" if item["surfaceId"] == desired_primary["surfaceId"] else "observer",
                }
                for item in surfaces
            ]
        primary = next((item for item in surfaces if item["role"] == "primary"), None)
        authority_mode = "stable"

    surfaces = _sort_surfaces_for_lock(surfaces)
    return {
        "uiMode": ui_mode,
        "userHost": user_host,
        "authorityMode": authority_mode,
        "primarySurfaceId": primary["surfaceId"] if primary else None,
        "primarySurfaceType": primary["surfaceType"] if primary else None,
        "primarySurfaceLabel": primary["label"] if primary else None,
        "takeoverRequest": takeover_request,
        "rationale": _build_surface_rationale(authority_mode, primary, surfaces, takeover_request, blocked_reason),
        "surfaces": surfaces,
        "updatedAt": now,
    }


def _save_surface_lock(project: Path, state: dict):
    _write_json_atomic(_surface_lock_path(project), state)


def _load_surface_lock_state(project: Path) -> dict:
    return _reconcile_surface_lock(project, _read_json_object(_surface_lock_path(project)))


def _surface_row(state: dict, surface_id: str) -> dict | None:
    return next((item for item in state.get("surfaces", []) if item.get("surfaceId") == surface_id), None)


def _create_surface_takeover_request(requester: dict, current_primary: dict, reason: str = "") -> dict:
    entropy = f"{requester.get('surfaceId', '')}:{current_primary.get('surfaceId', '')}:{time.time_ns()}"
    request_suffix = hashlib.sha1(entropy.encode("utf-8")).hexdigest()[:5]
    return {
        "requestId": f"takeover_{int(time.time() * 1000)}_{request_suffix}",
        "requestedBySurfaceId": requester["surfaceId"],
        "requestedByLabel": requester["label"],
        "requestedByType": requester["surfaceType"],
        "currentPrimarySurfaceId": current_primary["surfaceId"],
        "currentPrimaryLabel": current_primary["label"],
        "currentPrimaryType": current_primary["surfaceType"],
        "reason": reason.strip() or None,
        "status": "pending",
        "requestedAt": _utc_now_iso(),
    }


def _print_surface_status(state: dict):
    primary_label = state.get("primarySurfaceLabel") or "-"
    primary_type = state.get("primarySurfaceType") or "-"
    print(f"Authority mode: {state.get('authorityMode', 'stable')}")
    print(f"Primary surface: {primary_label} ({primary_type})")
    print(f"User host: {state.get('userHost', 'other')}")
    print(f"UI mode: {state.get('uiMode', 'visualizer')}")
    print(f"Rationale: {state.get('rationale', '')}")
    takeover = state.get("takeoverRequest")
    if isinstance(takeover, dict) and takeover.get("status") == "pending":
        print(
            "Pending takeover: "
            f"{takeover.get('requestedByLabel', takeover.get('requestedBySurfaceId', '?'))} "
            f"waiting on {takeover.get('currentPrimaryLabel', takeover.get('currentPrimarySurfaceId', '?'))}"
        )
    print("\nSurfaces:")
    print(f"{'ROLE':<10} {'TYPE':<14} {'ID':<28} LABEL")
    print("-" * 88)
    for surface in state.get("surfaces", []):
        role = str(surface.get("role", "observer"))
        surface_type = str(surface.get("surfaceType", "-"))
        surface_id = str(surface.get("surfaceId", "-"))[:28]
        label = str(surface.get("label", "-"))
        if surface.get("synthetic"):
            label += " [synthetic]"
        print(f"{role:<10} {surface_type:<14} {surface_id:<28} {label}")


def _host_surface_profile(user_host: str) -> dict:
    profiles = {
        "claude_code": {
            "surface_id": "host:claude-code",
            "label": "Claude Code",
            "launcher_mode": "run",
            "host_command": ["claude"],
        },
        "codex_cli": {
            "surface_id": "host:codex-cli",
            "label": "Codex CLI",
            "launcher_mode": "run",
            "host_command": ["codex"],
        },
        "gemini_cli": {
            "surface_id": "host:gemini-cli",
            "label": "Gemini CLI",
            "launcher_mode": "run",
            "host_command": ["gemini"],
        },
        "cursor": {
            "surface_id": "host:cursor",
            "label": "Cursor",
            "launcher_mode": "claim_only",
        },
        "windsurf": {
            "surface_id": "host:windsurf",
            "label": "Windsurf",
            "launcher_mode": "claim_only",
        },
        "vscode": {
            "surface_id": "host:vscode",
            "label": "VS Code",
            "launcher_mode": "claim_only",
        },
        "cline": {
            "surface_id": "host:cline",
            "label": "Cline",
            "launcher_mode": "claim_only",
        },
        "other": {
            "surface_id": "host:other",
            "label": "Other Host",
            "launcher_mode": "claim_only",
        },
    }
    return profiles.get(user_host, profiles["other"])


def _host_editor_profile(user_host: str) -> dict:
    profiles = {
        "claude_code": {
            "preset_id": "claude_code",
            "label": "VS Code-compatible",
            "template_name": "claude-code.tasks.json",
            "auto_install_supported": False,
            "run_hint": "If you also open this project in a VS Code-compatible editor, run the generated ControlCoding task from the editor task runner.",
        },
        "codex_cli": {
            "preset_id": "codex_cli",
            "label": "VS Code-compatible",
            "template_name": "codex-cli.tasks.json",
            "auto_install_supported": False,
            "run_hint": "If you also open this project in a VS Code-compatible editor, run the generated ControlCoding task from the editor task runner.",
        },
        "gemini_cli": {
            "preset_id": "gemini_cli",
            "label": "VS Code-compatible",
            "template_name": "gemini-cli.tasks.json",
            "auto_install_supported": False,
            "run_hint": "If you also open this project in a VS Code-compatible editor, run the generated ControlCoding task from the editor task runner.",
        },
        "cursor": {
            "preset_id": "cursor",
            "label": "Cursor",
            "template_name": "cursor.tasks.json",
            "auto_install_supported": True,
            "run_hint": "In Cursor, open the Command Palette and run `Tasks: Run Task`.",
        },
        "windsurf": {
            "preset_id": "windsurf",
            "label": "Windsurf",
            "template_name": "windsurf.tasks.json",
            "auto_install_supported": True,
            "run_hint": "In Windsurf, open the command palette or task runner and run the generated ControlCoding task.",
        },
        "vscode": {
            "preset_id": "vscode",
            "label": "VS Code",
            "template_name": "vscode.tasks.json",
            "auto_install_supported": True,
            "run_hint": "In VS Code, use `Terminal > Run Task`.",
        },
        "cline": {
            "preset_id": "cline",
            "label": "Cline / VS Code",
            "template_name": "cline.tasks.json",
            "auto_install_supported": True,
            "run_hint": "In the underlying VS Code-compatible editor, open the task runner and launch the generated ControlCoding task.",
        },
        "other": {
            "preset_id": "other",
            "label": "VS Code-compatible",
            "template_name": "host.tasks.json",
            "auto_install_supported": False,
            "run_hint": "If you later attach a VS Code-compatible editor to this project, import the generated ControlCoding task preset manually.",
        },
    }
    return profiles.get(user_host, profiles["other"])


def _host_task_template_relpath(user_host: str) -> str:
    profile = _host_editor_profile(user_host)
    return str((HOST_LAUNCHERS_RELATIVE_DIR / profile["template_name"])).replace("\\", "/")


def _cmd_quote(parts: list[str]) -> str:
    return " ".join(f'"{part}"' for part in parts)


def _ps_quote(parts: list[str]) -> str:
    escaped = []
    for part in parts:
        safe = part.replace("'", "''")
        escaped.append(f"'{safe}'")
    return " ".join(escaped)


def _sh_quote(parts: list[str]) -> str:
    import shlex
    return " ".join(shlex.quote(part) for part in parts)


def _build_host_launcher_assets(project: Path, user_host: str) -> dict[str, str]:
    profile = _host_surface_profile(user_host)
    editor_profile = _host_editor_profile(user_host)
    launcher_dir = project / HOST_LAUNCHERS_RELATIVE_DIR
    python_exe = sys.executable
    cc_script = str((SCRIPT_DIR / "cc.py").resolve())
    project_root = str(project.resolve())
    surface_id = profile["surface_id"]
    label = profile["label"]
    launcher_mode = profile["launcher_mode"]
    status_args = [python_exe, cc_script, "surface", "status", "--project-root", project_root]
    claim_args = [
        python_exe, cc_script, "surface", "claim", "--project-root", project_root,
        surface_id, "--type", "official_host", "--label", label,
    ]
    release_args = [
        python_exe, cc_script, "surface", "release", "--project-root", project_root,
        surface_id,
    ]
    observe_args = [
        python_exe, cc_script, "surface", "observe", "--project-root", project_root,
        surface_id, "--type", "official_host", "--label", label,
    ]
    run_args = None
    if launcher_mode == "run":
        host_command = list(profile.get("host_command", []))
        run_args = [
            python_exe, cc_script, "surface", "run", "--project-root", project_root,
            surface_id, "--type", "official_host", "--role", "primary", "--label", label, "--",
            *host_command,
        ]

    files: dict[str, str] = {}
    readme_lines = [
        "ControlCoding host launcher assets",
        "",
        f"Configured user host: {label}",
        "",
        "Use these local scripts to keep Primary/Observer state consistent for this project.",
        "They are local-only artifacts generated from the current ControlCoding installation.",
        "",
    ]
    if run_args is not None:
        readme_lines.extend([
            "Recommended entrypoint:",
            "- launch_primary_host.* -> starts the configured official host through CC surface management",
            "",
        ])
    else:
        readme_lines.extend([
            "Recommended entrypoint:",
            "- claim_primary_host.* when you start working in this host/IDE",
            "- release_primary_host.* when you finish that session",
            "",
        ])
    readme_lines.extend([
        "Support scripts:",
        "- surface_status.* -> inspect the current Primary/Observer lock",
        "- observe_host.* -> explicitly attach this host as observer-only",
        f"- {editor_profile['template_name']} -> {editor_profile['label']} task preset for these launchers",
        "",
        "If the selected host supports workspace tasks and .vscode/tasks.json does not already exist, setup may also install local editor tasks automatically.",
        "",
        "If you rerun `cc setup`, these launchers are regenerated to match the current project host choice.",
        "",
    ])
    files[str((launcher_dir / "README.txt").relative_to(project))] = "\n".join(readme_lines)

    files[str((launcher_dir / "surface_status.cmd").relative_to(project))] = (
        "@echo off\r\n"
        "setlocal\r\n"
        f"{_cmd_quote(status_args)} %*\r\n"
    )
    files[str((launcher_dir / "surface_status.ps1").relative_to(project))] = (
        f"& {_ps_quote(status_args)} @args\n"
    )
    files[str((launcher_dir / "surface_status.sh").relative_to(project))] = (
        "#!/usr/bin/env sh\n"
        f"exec {_sh_quote(status_args)} \"$@\"\n"
    )

    files[str((launcher_dir / "claim_primary_host.cmd").relative_to(project))] = (
        "@echo off\r\n"
        "setlocal\r\n"
        f"{_cmd_quote(claim_args)}\r\n"
    )
    files[str((launcher_dir / "claim_primary_host.ps1").relative_to(project))] = (
        f"& {_ps_quote(claim_args)}\n"
    )
    files[str((launcher_dir / "claim_primary_host.sh").relative_to(project))] = (
        "#!/usr/bin/env sh\n"
        f"exec {_sh_quote(claim_args)}\n"
    )

    files[str((launcher_dir / "observe_host.cmd").relative_to(project))] = (
        "@echo off\r\n"
        "setlocal\r\n"
        f"{_cmd_quote(observe_args)}\r\n"
    )
    files[str((launcher_dir / "observe_host.ps1").relative_to(project))] = (
        f"& {_ps_quote(observe_args)}\n"
    )
    files[str((launcher_dir / "observe_host.sh").relative_to(project))] = (
        "#!/usr/bin/env sh\n"
        f"exec {_sh_quote(observe_args)}\n"
    )

    files[str((launcher_dir / "release_primary_host.cmd").relative_to(project))] = (
        "@echo off\r\n"
        "setlocal\r\n"
        f"{_cmd_quote(release_args)}\r\n"
    )
    files[str((launcher_dir / "release_primary_host.ps1").relative_to(project))] = (
        f"& {_ps_quote(release_args)}\n"
    )
    files[str((launcher_dir / "release_primary_host.sh").relative_to(project))] = (
        "#!/usr/bin/env sh\n"
        f"exec {_sh_quote(release_args)}\n"
    )

    if run_args is not None:
        files[str((launcher_dir / "launch_primary_host.cmd").relative_to(project))] = (
            "@echo off\r\n"
            "setlocal\r\n"
            f"{_cmd_quote(run_args)} %*\r\n"
        )
        files[str((launcher_dir / "launch_primary_host.ps1").relative_to(project))] = (
            f"& {_ps_quote(run_args)} @args\n"
        )
        files[str((launcher_dir / "launch_primary_host.sh").relative_to(project))] = (
            "#!/usr/bin/env sh\n"
            f"exec {_sh_quote(run_args)} \"$@\"\n"
        )

    return files


def _write_host_launcher_assets(project: Path, user_host: str) -> list[str]:
    assets = _build_host_launcher_assets(project, user_host)
    written: list[str] = []
    for relative_path, content in assets.items():
        target = project / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative_path.replace("\\", "/"))
    return sorted(written)


def _build_vscode_shell_task(label: str, launcher_name: str, detail: str) -> dict:
    launcher_win = f"${{workspaceFolder}}\\{CONTROL_PLANE_DIRNAME}\\launchers\\{launcher_name}.cmd"
    launcher_sh = f"${{workspaceFolder}}/{CONTROL_PLANE_DIRNAME}/launchers/{launcher_name}.sh"
    return {
        "label": label,
        "type": "shell",
        "command": "sh",
        "args": [launcher_sh],
        "windows": {
            "command": launcher_win,
        },
        "linux": {
            "command": "sh",
            "args": [launcher_sh],
        },
        "osx": {
            "command": "sh",
            "args": [launcher_sh],
        },
        "presentation": {
            "reveal": "always",
            "panel": "dedicated",
        },
        "problemMatcher": [],
        "detail": detail,
    }


def _build_vscode_tasks_manifest(user_host: str) -> dict:
    profile = _host_surface_profile(user_host)
    editor_profile = _host_editor_profile(user_host)
    host_label = profile["label"]
    tasks = []
    if profile["launcher_mode"] == "run":
        tasks.append(
            _build_vscode_shell_task(
                f"ControlCoding: Launch {host_label} as Primary",
                "launch_primary_host",
                f"Launch {host_label} through ControlCoding surface management.",
            )
        )
    else:
        tasks.append(
            _build_vscode_shell_task(
                f"ControlCoding: Claim {host_label} as Primary",
                "claim_primary_host",
                f"Mark {host_label} as the primary ControlCoding surface for this project.",
            )
        )

    tasks.extend([
        _build_vscode_shell_task(
            f"ControlCoding: Observe {host_label}",
            "observe_host",
            f"Attach {host_label} as observer-only for this project.",
        ),
        _build_vscode_shell_task(
            "ControlCoding: Surface Status",
            "surface_status",
            "Show the current Primary/Observer authority state for this project.",
        ),
        _build_vscode_shell_task(
            f"ControlCoding: Release {host_label}",
            "release_primary_host",
            f"Release the current {host_label} surface lock for this project.",
        ),
    ])

    return {
        "version": "2.0.0",
        "controlCodingPreset": editor_profile["preset_id"],
        "controlCodingHost": user_host,
        "tasks": tasks,
    }


def _normalize_host_instructions(host_instructions: dict | None) -> dict:
    raw = host_instructions if isinstance(host_instructions, dict) else {}
    mode = str(raw.get("mode", "recommended")).strip()
    if mode not in VALID_HOST_INSTRUCTION_MODES:
        mode = "recommended"
    custom_notes_raw = raw.get("customNotes", [])
    custom_notes: list[str] = []
    if isinstance(custom_notes_raw, list):
        for note in custom_notes_raw:
            if isinstance(note, str):
                trimmed = note.strip()
                if trimmed:
                    custom_notes.append(trimmed)
    return {
        "mode": mode,
        "customNotes": custom_notes[:12],
    }


def _build_host_editor_task_step(
    user_host: str,
    launcher_mode: str,
    editor_tasks_installed: bool,
    workspace_tasks_present: bool,
) -> str:
    surface_profile = _host_surface_profile(user_host)
    editor_profile = _host_editor_profile(user_host)
    primary_task_label = (
        f"ControlCoding: Launch {surface_profile['label']} as Primary"
        if launcher_mode == "run"
        else f"ControlCoding: Claim {surface_profile['label']} as Primary"
    )
    template_path = _host_task_template_relpath(user_host)

    if editor_tasks_installed:
        return f"{editor_profile['run_hint']} Choose `{primary_task_label}` from `.vscode/tasks.json`."
    if workspace_tasks_present:
        return (
            f"An existing `.vscode/tasks.json` was preserved; merge `{template_path}` manually "
            f"if you want the {editor_profile['label']} task preset too."
        )
    return (
        f"Optional {editor_profile['label']} task preset available at `{template_path}`. "
        "Import it manually if you later want workspace task shortcuts."
    )


def _build_host_next_steps(
    user_host: str,
    launcher_mode: str,
    editor_tasks_installed: bool,
    workspace_tasks_present: bool,
) -> list[str]:
    profile = _host_surface_profile(user_host)
    label = profile["label"]
    primary_launcher = (
        f"{CONTROL_PLANE_DIRNAME}/launchers/launch_primary_host.cmd"
        if launcher_mode == "run"
        else f"{CONTROL_PLANE_DIRNAME}/launchers/claim_primary_host.cmd"
    )
    steps = [
        f"Start from chat with `{PUBLIC_CHAT_STARTER_RELATIVE_PATH.as_posix()}` if you want the public no-command assistant flow.",
        f"Use `{primary_launcher}` as the primary entrypoint when you start working from {label}.",
        _build_host_editor_task_step(
            user_host,
            launcher_mode,
            editor_tasks_installed,
            workspace_tasks_present,
        ),
        f"Use `{CONTROL_PLANE_DIRNAME}/launchers/surface_status.cmd` to inspect the current Primary/Observer state.",
    ]
    return steps


def _build_host_instruction_lines(
    user_host: str,
    launcher_mode: str,
    editor_tasks_installed: bool,
    workspace_tasks_present: bool,
    host_instructions: dict | None,
) -> list[str]:
    normalized = _normalize_host_instructions(host_instructions)
    mode = normalized["mode"]
    custom_notes = normalized["customNotes"]
    profile = _host_surface_profile(user_host)
    lines = [
        f"Working host: {profile['label']}",
        "ControlCoding policy: one project/session has one Primary Surface; all other surfaces are Observer-only.",
        *_format_host_coverage_lines(_host_coverage_payload(_derive_host_profile(user_host))),
    ]
    if launcher_mode == "run":
        lines.append(
            f"Use the generated `launch_primary_host.*` wrapper when {profile['label']} should own the working session."
        )
        lines.append(
            "When this host is active on an official consumer/CLI path, keep the canonical chat and coding flow there."
        )
    else:
        lines.append(
            f"When you start working from {profile['label']}, claim it explicitly through `claim_primary_host.*` or the generated task."
        )
        lines.append(
            "If another official host already owns the session, keep this host observer-only until you perform an explicit takeover."
        )

    lines.append(
        _build_host_editor_task_step(
            user_host,
            launcher_mode,
            editor_tasks_installed,
            workspace_tasks_present,
        )
    )

    if mode in {"recommended", "custom"}:
        lines.extend([
            "Use `surface_status.*` before asking CC to orchestrate if you are unsure which surface is currently primary.",
            "Keep any CC UI open as an observer/control surface on official-host paths unless you intentionally switch to an API/local primary path.",
        ])

    if mode == "custom" and custom_notes:
        lines.append("User custom workflow notes:")
        lines.extend(f"- {note}" for note in custom_notes)

    return lines


def _build_host_instructions_document(
    user_host: str,
    launcher_mode: str,
    editor_tasks_installed: bool,
    workspace_tasks_present: bool,
    host_instructions: dict | None,
) -> str:
    normalized = _normalize_host_instructions(host_instructions)
    lines = _build_host_instruction_lines(
        user_host,
        launcher_mode,
        editor_tasks_installed,
        workspace_tasks_present,
        normalized,
    )
    doc_lines = [
        "# ControlCoding Host Instructions",
        "",
        f"- Host: `{_host_surface_profile(user_host)['label']}`",
        f"- Mode: `{normalized['mode']}`",
        "",
        "## Effective Instructions",
        "",
    ]
    for line in lines:
        if line.startswith("- "):
            doc_lines.append(line)
        else:
            doc_lines.append(f"- {line}")
    return "\n".join(doc_lines) + "\n"


def _build_public_chat_entry_document(user_host: str) -> str:
    label = _host_surface_profile(user_host)["label"]
    install_contract = PUBLIC_INSTALL_CONTRACT_RELATIVE_PATH.as_posix()
    project_setup_contract = PUBLIC_PROJECT_SETUP_CONTRACT_RELATIVE_PATH.as_posix()
    return (
        "# Start Here With Chat\n\n"
        "This is the public entrypoint for a normal user.\n\n"
        "You should be able to start from chat without memorizing CLI commands.\n"
        "Tell your AI host one of these intents in normal language:\n\n"
        f"- `Install ControlCoding in this project. Read `{install_contract}` and follow it.`\n"
        f"- `Set up this project with ControlCoding. Read `{project_setup_contract}` and follow it.`\n\n"
        "Working rules for the chat:\n\n"
        "- Ask one question at a time.\n"
        "- Explain important choices briefly before asking for confirmation.\n"
        "- If the host can execute local commands, it should do the local apply steps itself.\n"
        "- Only fall back to asking the user to run commands manually if the host truly cannot execute local commands.\n"
        "- Do not expose raw tool-call metadata or internal plumbing in user-facing replies.\n\n"
        f"Recommended host label for this project: `{label}`.\n"
    )


def _build_public_install_contract_document(user_host: str) -> str:
    label = _host_surface_profile(user_host)["label"]
    return (
        "# Public Install Assistant Contract\n\n"
        "Use this file when a chat host should install and configure ControlCoding for this project.\n\n"
        "## Purpose\n\n"
        "This flow is only for installing and configuring ControlCoding itself.\n"
        "It is not the project-definition or kickoff-doc flow.\n\n"
        "## User Experience Rules\n\n"
        "- Start in the user's language.\n"
        "- Briefly explain what ControlCoding is and how the install flow will work.\n"
        "- Ask one question at a time.\n"
        "- Keep the next question visible in the chat reply.\n"
        "- Treat a short install request as permission to start the flow, not permission to auto-apply everything.\n"
        "- Confirm the high-impact choices before writing files.\n"
        "- Do not force the recommended host; present it as a recommendation.\n"
        "- Do not narrate backend plumbing.\n\n"
        "## High-Impact Choices To Confirm\n\n"
        "- project name\n"
        f"- primary host, with `{label}` as the likely recommendation when appropriate\n"
        "- usage model\n"
        "- documentation mode\n"
        "- local Project Memory Engine and GraphRAG with governed-folder scope only\n"
        "- host workflow guidance\n"
        "- engagement tier and backend policy when relevant\n\n"
        "Project-local hooks are the public default.\n"
        "Do not surface central/shared hooks unless the user explicitly asks for an advanced multi-project override.\n\n"
        "## Core Memory Default\n\n"
        "Project Memory Engine and GraphRAG are Core defaults.\n"
        "The recommended answer is yes to local memory initialization with governed-folder scope only.\n"
        "Base setup must not run a full repository memory scan, OCR, document layout extraction, vector rebuild, file relocation, or graph promotion.\n\n"
        "## Apply Rule\n\n"
        "- If the host can execute local commands, it should run the local install, engagement, and doctor steps itself.\n"
        "- Only ask the user to run commands manually if the host truly cannot execute them.\n"
        "- After install succeeds, report what was written and whether doctor passed.\n"
        "- Only after install completes, ask whether the user wants to start the separate project setup flow.\n"
    )


def _build_public_project_setup_contract_document(user_host: str) -> str:
    label = _host_surface_profile(user_host)["label"]
    return (
        "# Public Project Setup Assistant Contract\n\n"
        "Use this file when a chat host should turn existing project inputs into the first governed ControlCoding project package.\n\n"
        "## Purpose\n\n"
        "This flow assumes ControlCoding is already installed.\n"
        "It is for project definition, design-development preparation, implementation planning, and protection planning.\n"
        "It is not for reinstalling ControlCoding.\n\n"
        "## User Experience Rules\n\n"
        "- Verify first whether ControlCoding is already installed.\n"
        "- Work in the user's language.\n"
        "- Ask one question at a time.\n"
        "- Before asking for a brief manually, look for likely brief candidates read-only, such as `project_brief.md`, `README.md`, and files with `brief`, `requirements`, `spec`, or `design` in the name.\n"
        "- Do not move, rename, copy, OCR, or import candidate brief files during discovery.\n"
        "- If one candidate looks right, ask whether that is the brief.\n"
        "- If several candidates look plausible, ask the user which one should be treated as the brief.\n"
        "- Also detect whether the repository already looks mature, and if so offer the brownfield adoption path.\n"
        "- Do not silently choose the major project-shape answers.\n"
        "- Do not revisit install/runtime questions unless the user explicitly asks.\n\n"
        "## Required Outcome\n\n"
        "- source assessment\n"
        "- consultation planning when the source material is still weak or ambiguous\n"
        "- design package\n"
        "- implementation package\n"
        "- progressive protection planning\n\n"
        "## Apply Rule\n\n"
        "- If the host can execute local commands, it should apply the project setup itself.\n"
        "- Only fall back to manual commands if the host truly cannot execute them.\n"
        "- After apply, report exactly which project-definition, design, criteria, contracts, and implementation docs were written or refreshed.\n\n"
        f"Recommended host label for explanations in this project: `{label}`.\n"
    )


def _build_host_integration_assets(project: Path, user_host: str, host_instructions: dict | None = None) -> dict[str, str]:
    assets = _build_host_launcher_assets(project, user_host)
    editor_profile = _host_editor_profile(user_host)
    task_template_path = _host_task_template_relpath(user_host)
    manifest = json.dumps(_build_vscode_tasks_manifest(user_host), indent=2, ensure_ascii=False) + "\n"
    assets[task_template_path] = manifest
    vscode_tasks = project / VSCODE_TASKS_RELATIVE_PATH
    workspace_tasks_present = vscode_tasks.exists()
    editor_tasks_installed = editor_profile["auto_install_supported"] and not workspace_tasks_present
    if editor_tasks_installed:
        assets[str(VSCODE_TASKS_RELATIVE_PATH).replace("\\", "/")] = manifest
    profile = _host_surface_profile(user_host)
    normalized_instructions = _normalize_host_instructions(host_instructions)
    primary_entry = (
        "launch_primary_host"
        if profile["launcher_mode"] == "run"
        else "claim_primary_host"
    )
    manifest_payload = {
        "userHost": user_host,
        "label": profile["label"],
        "surfaceId": profile["surface_id"],
        "launcherMode": profile["launcher_mode"],
        "primaryEntryPoint": {
            "windows": str((HOST_LAUNCHERS_RELATIVE_DIR / f"{primary_entry}.cmd")).replace("\\", "/"),
            "powershell": str((HOST_LAUNCHERS_RELATIVE_DIR / f"{primary_entry}.ps1")).replace("\\", "/"),
            "shell": str((HOST_LAUNCHERS_RELATIVE_DIR / f"{primary_entry}.sh")).replace("\\", "/"),
        },
        "supportEntryPoints": [
            str((HOST_LAUNCHERS_RELATIVE_DIR / "surface_status.cmd")).replace("\\", "/"),
            str((HOST_LAUNCHERS_RELATIVE_DIR / "observe_host.cmd")).replace("\\", "/"),
            str((HOST_LAUNCHERS_RELATIVE_DIR / "release_primary_host.cmd")).replace("\\", "/"),
        ],
        "editorTasks": {
            "presetId": editor_profile["preset_id"],
            "presetLabel": editor_profile["label"],
            "templatePath": task_template_path,
            "installPath": str(VSCODE_TASKS_RELATIVE_PATH).replace("\\", "/"),
            "autoInstallSupported": editor_profile["auto_install_supported"],
            "installed": editor_tasks_installed,
            "preexisting": workspace_tasks_present,
            "runHint": editor_profile["run_hint"],
        },
        "instructionMode": normalized_instructions["mode"],
        "instructionFilePath": str(HOST_INSTRUCTIONS_RELATIVE_PATH).replace("\\", "/"),
        "customInstructions": normalized_instructions["customNotes"],
        "effectiveInstructions": _build_host_instruction_lines(
            user_host,
            profile["launcher_mode"],
            editor_tasks_installed,
            workspace_tasks_present,
            normalized_instructions,
        ),
        "nextSteps": _build_host_next_steps(
            user_host,
            profile["launcher_mode"],
            editor_tasks_installed,
            workspace_tasks_present,
        ),
        "generatedFiles": [],
    }
    assets[str(HOST_INSTRUCTIONS_RELATIVE_PATH).replace("\\", "/")] = _build_host_instructions_document(
        user_host,
        profile["launcher_mode"],
        editor_tasks_installed,
        workspace_tasks_present,
        normalized_instructions,
    )
    assets[str(PUBLIC_CHAT_STARTER_RELATIVE_PATH).replace("\\", "/")] = _build_public_chat_entry_document(user_host)
    assets[str(PUBLIC_INSTALL_CONTRACT_RELATIVE_PATH).replace("\\", "/")] = _build_public_install_contract_document(user_host)
    assets[str(PUBLIC_PROJECT_SETUP_CONTRACT_RELATIVE_PATH).replace("\\", "/")] = _build_public_project_setup_contract_document(user_host)
    manifest_payload["publicAssistant"] = {
        "starterPath": str(PUBLIC_CHAT_STARTER_RELATIVE_PATH).replace("\\", "/"),
        "installContractPath": str(PUBLIC_INSTALL_CONTRACT_RELATIVE_PATH).replace("\\", "/"),
        "projectSetupContractPath": str(PUBLIC_PROJECT_SETUP_CONTRACT_RELATIVE_PATH).replace("\\", "/"),
    }
    manifest_payload["generatedFiles"] = sorted([
        *assets.keys(),
        str(HOST_INTEGRATION_MANIFEST_RELATIVE_PATH).replace("\\", "/"),
    ])
    assets[str(HOST_INTEGRATION_MANIFEST_RELATIVE_PATH).replace("\\", "/")] = (
        json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n"
    )
    return assets


def _write_host_integration_assets(project: Path, user_host: str, host_instructions: dict | None = None) -> list[str]:
    assets = _build_host_integration_assets(project, user_host, host_instructions)
    written: list[str] = []
    for relative_path, content in assets.items():
        target = project / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative_path.replace("\\", "/"))
    return sorted(written)


def _upsert_surface(project: Path, surface_id: str, surface_type: str, label: str, role: str) -> dict:
    state = _load_surface_lock_state(project)
    current_primary_type = str(state.get("primarySurfaceType") or "")
    current_primary_id = str(state.get("primarySurfaceId") or "")
    if (
        role == "primary"
        and surface_type != "official_host"
        and current_primary_type == "official_host"
        and current_primary_id
        and current_primary_id != surface_id
    ):
        raise ValueError(
            "An official host currently owns the primary surface. Release or demote it before promoting a CC UI surface."
        )

    now = _utc_now_iso()
    existing = _surface_row(state, surface_id)
    next_surfaces = [
        item for item in state.get("surfaces", [])
        if not item.get("synthetic") and item.get("surfaceId") != surface_id
    ]
    next_surfaces.append({
        "surfaceId": surface_id,
        "surfaceType": surface_type,
        "label": label or _default_surface_label(surface_type),
        "role": role,
        "desiredRole": role,
        "active": True,
        "synthetic": False,
        "attachedAt": existing.get("attachedAt") if isinstance(existing, dict) else now,
        "lastSeenAt": now,
    })
    next_state = _reconcile_surface_lock(project, {
        "surfaces": next_surfaces,
        "takeoverRequest": None,
    })
    _save_surface_lock(project, next_state)
    return next_state


def cmd_surface_status(project: Path, json_output: bool = False) -> int:
    state = _load_surface_lock_state(project)
    if json_output:
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return 0
    _print_surface_status(state)
    return 0


def cmd_surface_claim(project: Path, surface_id: str, surface_type: str, label: str) -> int:
    try:
        state = _upsert_surface(project, surface_id, surface_type, label, "primary")
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    primary_label = state.get("primarySurfaceLabel") or surface_id
    print(f"Primary surface set to: {primary_label}")
    return 0


def cmd_surface_observe(project: Path, surface_id: str, surface_type: str, label: str) -> int:
    _upsert_surface(project, surface_id, surface_type, label, "observer")
    print(f"Observer surface attached: {label or surface_id}")
    return 0


def cmd_surface_request_takeover(project: Path, surface_id: str, reason: str = "") -> int:
    state = _load_surface_lock_state(project)
    if state.get("authorityMode") == "host_locked":
        print("Error: Official host owns the primary surface on the current path. CC UI cannot request takeover here.")
        return 1

    requester = _surface_row(state, surface_id)
    if requester is None or requester.get("synthetic"):
        print(f"Error: surface not attached: {surface_id}")
        return 1

    if requester.get("role") == "primary":
        print(f"Surface already primary: {requester.get('label') or surface_id}")
        return 0

    pending = state.get("takeoverRequest")
    if isinstance(pending, dict) and pending.get("status") == "pending":
        if pending.get("requestedBySurfaceId") == surface_id:
            print(
                "Takeover already pending: "
                f"{pending.get('requestedByLabel', pending.get('requestedBySurfaceId', surface_id))}"
            )
            return 0
        print(
            "Error: takeover already pending for "
            f"{pending.get('requestedByLabel', pending.get('requestedBySurfaceId', '?'))}"
        )
        return 1

    current_primary = next(
        (
            item
            for item in state.get("surfaces", [])
            if not item.get("synthetic") and item.get("role") == "primary"
        ),
        None,
    )
    if current_primary is None:
        next_surfaces = []
        for item in state.get("surfaces", []):
            if item.get("synthetic"):
                continue
            next_surfaces.append({
                **item,
                "role": "primary" if item.get("surfaceId") == surface_id else "observer",
                "desiredRole": "primary" if item.get("surfaceId") == surface_id else "observer",
            })
        next_state = _reconcile_surface_lock(project, {
            "surfaces": next_surfaces,
            "takeoverRequest": None,
        })
        _save_surface_lock(project, next_state)
        primary_label = next_state.get("primarySurfaceLabel") or surface_id
        print(f"Primary surface set to: {primary_label}")
        return 0

    next_state = _reconcile_surface_lock(project, {
        "surfaces": [item for item in state.get("surfaces", []) if not item.get("synthetic")],
        "takeoverRequest": _create_surface_takeover_request(requester, current_primary, reason),
    })
    _save_surface_lock(project, next_state)
    next_pending = next_state.get("takeoverRequest") or {}
    print(
        "Takeover requested: "
        f"{next_pending.get('requestedByLabel', surface_id)} waiting on "
        f"{next_pending.get('currentPrimaryLabel', current_primary.get('label', current_primary.get('surfaceId', '?')))}"
    )
    return 0


def cmd_surface_resolve_takeover(
    project: Path,
    request_id: str,
    decision: str,
    resolved_by_surface_id: str,
) -> int:
    if decision not in {"approved", "denied"}:
        print(f"Error: invalid takeover decision: {decision}")
        return 1

    state = _load_surface_lock_state(project)
    pending = state.get("takeoverRequest")
    if not isinstance(pending, dict) or pending.get("status") != "pending":
        print("Error: no pending takeover request is active.")
        return 1

    if str(pending.get("requestId", "")).strip() != request_id:
        print("Error: takeover request id does not match the active pending takeover.")
        return 1

    current_primary_id = str(pending.get("currentPrimarySurfaceId", "")).strip()
    current_primary_label = str(pending.get("currentPrimaryLabel", current_primary_id)).strip()
    if resolved_by_surface_id.strip() != current_primary_id:
        print(f"Error: only the current primary surface ({current_primary_label}) may resolve this takeover.")
        return 1

    next_surfaces = []
    for item in state.get("surfaces", []):
        if item.get("synthetic"):
            continue
        row = dict(item)
        if decision == "approved":
            if item.get("surfaceId") == pending.get("requestedBySurfaceId"):
                row["role"] = "primary"
                row["desiredRole"] = "primary"
            elif item.get("surfaceId") == current_primary_id:
                row["role"] = "observer"
                row["desiredRole"] = "observer"
            else:
                row["role"] = "observer"
        else:
            if item.get("surfaceId") == pending.get("requestedBySurfaceId"):
                row["role"] = "observer"
                row["desiredRole"] = "observer"
        next_surfaces.append(row)

    next_state = _reconcile_surface_lock(project, {
        "surfaces": next_surfaces,
        "takeoverRequest": None,
    })
    _save_surface_lock(project, next_state)
    primary_label = next_state.get("primarySurfaceLabel") or next_state.get("primarySurfaceId") or "-"
    if decision == "approved":
        print(f"Takeover approved: {primary_label} is now primary.")
    else:
        print(f"Takeover denied: {primary_label} remains primary.")
    return 0


def cmd_surface_heartbeat(project: Path, surface_id: str, quiet: bool = False) -> int:
    state = _load_surface_lock_state(project)
    surface = _surface_row(state, surface_id)
    if surface is None or surface.get("synthetic"):
        if not quiet:
            print(f"Error: surface not attached: {surface_id}")
        return 1
    surface["lastSeenAt"] = _utc_now_iso()
    next_surfaces = [item for item in state.get("surfaces", []) if not item.get("synthetic")]
    next_state = _reconcile_surface_lock(project, {
        "surfaces": next_surfaces,
        "takeoverRequest": state.get("takeoverRequest"),
    })
    _save_surface_lock(project, next_state)
    if not quiet:
        print(f"Heartbeat recorded for: {surface_id}")
    return 0


def cmd_surface_release(project: Path, surface_id: str) -> int:
    state = _load_surface_lock_state(project)
    next_surfaces = [
        item for item in state.get("surfaces", [])
        if not item.get("synthetic") and item.get("surfaceId") != surface_id
    ]
    if len(next_surfaces) == len([item for item in state.get("surfaces", []) if not item.get("synthetic")]):
        print(f"Error: surface not attached: {surface_id}")
        return 1
    next_state = _reconcile_surface_lock(project, {
        "surfaces": next_surfaces,
        "takeoverRequest": None,
    })
    _save_surface_lock(project, next_state)
    print(f"Released surface: {surface_id}")
    return 0


lockfile.lock_surface_commands(globals())


def cmd_surface_run(
    project: Path,
    surface_id: str,
    surface_type: str,
    label: str,
    role: str,
    heartbeat_seconds: float,
    keep_lock: bool,
    command_args: list[str],
) -> int:
    if heartbeat_seconds <= 0:
        print("Error: --heartbeat-seconds must be > 0")
        return 1
    if role not in VALID_SURFACE_ROLES:
        print(f"Error: invalid role: {role}")
        return 1

    cleaned_command = list(command_args)
    if cleaned_command and cleaned_command[0] == "--":
        cleaned_command = cleaned_command[1:]
    if not cleaned_command:
        print("Error: surface run requires a command after '--'")
        return 1

    attach_fn = cmd_surface_claim if role == "primary" else cmd_surface_observe
    attach_result = attach_fn(project, surface_id, surface_type, label)
    if attach_result != 0:
        return attach_result

    env = os.environ.copy()
    env["CC_SURFACE_ID"] = surface_id
    env["CC_SURFACE_TYPE"] = surface_type
    env["CC_SURFACE_ROLE"] = role
    env["CC_PROJECT_ROOT"] = str(project)

    process = None
    exit_code = 1
    try:
        process = subprocess.Popen(
            cleaned_command,
            cwd=str(project),
            env=env,
        )
        while True:
            try:
                exit_code = process.wait(timeout=heartbeat_seconds)
                break
            except subprocess.TimeoutExpired:
                hb_result = cmd_surface_heartbeat(project, surface_id, quiet=True)
                if hb_result != 0:
                    print(f"Warning: failed to refresh heartbeat for {surface_id}")
                continue
    except OSError as exc:
        print(f"Error: failed to launch child command for {surface_id}: {exc}")
        exit_code = 1
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait(timeout=5)
        else:
            exit_code = 130
        print("Interrupted: released managed surface after stopping child process.")
    finally:
        if not keep_lock:
            cmd_surface_release(project, surface_id)
    return exit_code


def _resolve_hook_commands(settings: dict, hooks_dir: Path) -> dict:
    """Resolve generated shell-form hook script paths to absolute paths.

    Prevents deadlock when shell cwd differs from project root:
    hooks must be findable regardless of working directory. Generated commands
    use Claude Code's shell form, so quote only the script operand with POSIX
    shell quoting. The recognized grammar is ``python hooks/<script>`` with an
    optional opaque shell-argument tail; custom and absolute commands stay
    unchanged.
    """
    hooks_abs = str(hooks_dir.resolve()).replace("\\", "/")
    for _event, entries in settings.get("hooks", {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if cmd.startswith("python hooks/"):
                    suffix = cmd[len("python hooks/"):]
                    match = re.fullmatch(r"(?P<script>[^\s]+)(?P<tail>\s.*)?", suffix)
                    if match:
                        script_path = f"{hooks_abs}/{match.group('script')}"
                        tail = match.group("tail") or ""
                        hook["command"] = f"python {shlex.quote(script_path)}{tail}"
    return settings


def _build_repo_precommit_hook_script(hooks_dir: Path, fitness_script: Path | None) -> str:
    hooks_abs = hooks_dir.resolve().as_posix()
    fitness_abs = fitness_script.resolve().as_posix() if fitness_script is not None else ""
    lines = [
        "#!/bin/sh",
        "# ControlCoding repo boundary gate baseline",
        "# Generated by cc init. Safe to customize if you preserve the gate intent.",
        "",
        'PYTHON_BIN="${PYTHON:-python}"',
        f'REPO_BOUNDARY="{hooks_abs}/check_repo_boundaries.py"',
        f'FITNESS_CHECK="{fitness_abs}"' if fitness_abs else 'FITNESS_CHECK=""',
        "",
        'echo "Gate 1: repo boundary gate..."',
        '"$PYTHON_BIN" "$REPO_BOUNDARY" || exit 2',
        "",
        'if [ -n "$FITNESS_CHECK" ] && [ -f "$FITNESS_CHECK" ]; then',
        '  echo "Gate 2: verification gate (fitness baseline)..."',
        '  if "$PYTHON_BIN" "$FITNESS_CHECK" --project-root . --ci; then',
        '    :',
        '  else',
        '    fitness_status=$?',
        '    if [ "$fitness_status" -eq 1 ]; then',
        '      exit 2',
        '    elif [ "$fitness_status" -eq 2 ]; then',
        '      echo "Gate 2: fitness warnings recorded; continuing."',
        '    else',
        '      exit "$fitness_status"',
        '    fi',
        '  fi',
        "else",
        '  echo "Gate 2: fitness baseline not wired; add tools/fitness_check.py or wire verification in CI." >&2',
        "fi",
        "",
        "# Project-specific invariant commands stay project-managed.",
        "# Uncomment and adapt one of these when the project is ready:",
        '# "$PYTHON_BIN" -m pytest tests/ -x || exit 2',
        '# npm test -- --runInBand || exit 2',
        '# cargo test || exit 2',
        "",
    ]
    return "\n".join(lines)


def _build_repo_postcommit_hook_script(hooks_dir: Path) -> str:
    hooks_abs = hooks_dir.resolve().as_posix()
    lines = [
        "#!/bin/sh",
        "# ControlCoding review gate baseline",
        "# Generated by cc init. Runs CodeWarden after commit.",
        "",
        'PYTHON_BIN="${PYTHON:-python}"',
        f'REVIEW_SCRIPT="{hooks_abs}/codewarden_review.py"',
        "",
        'if [ -f "$REVIEW_SCRIPT" ]; then',
        '  "$PYTHON_BIN" "$REVIEW_SCRIPT" >/dev/null 2>&1 &',
        "fi",
        "",
        "exit 0",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _init_snapshot(path: Path, *, directory: bool = False):
    """Inspect lexical paths without accepting symlinks, reparse or special files."""
    from cc_setup import _pack_check_parents, _pack_safe_kind
    _pack_check_parents(path)
    entry = _pack_safe_kind(path, expected="dir" if directory else "file")
    if entry is None:
        return None
    identity = (entry.st_dev, entry.st_ino, entry.st_mode)
    if directory:
        return identity
    data = path.read_bytes()
    after = _pack_safe_kind(path, expected="file")
    signature = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if after is None or signature(entry) != signature(after):
        raise OSError(f"init input changed while reading: {path}")
    return (*signature(after), data)


def _init_observe(path: Path, observations: dict, *, directory: bool = False):
    for parent in reversed(path.parents):
        snapshot = _init_snapshot(parent, directory=True)
        item = (True, snapshot)
        if parent in observations and observations[parent] != item:
            raise OSError(f"init parent changed during preflight: {parent}")
        observations[parent] = item
    snapshot = _init_snapshot(path, directory=directory)
    item = (directory, snapshot)
    if path in observations and observations[path] != item:
        raise OSError(f"init path changed during preflight: {path}")
    observations[path] = item
    return snapshot


def _init_recheck(observations: dict):
    for path, (directory, snapshot) in observations.items():
        if _init_snapshot(path, directory=directory) != snapshot:
            raise OSError(f"init path changed after preflight: {path}; inspect and preview again")


def _plan_init(project: Path, central_hooks: bool = False):
    """Build detached init outputs, retaining every consumed input for revalidation."""
    from copy import deepcopy
    observations, files, directories = {}, {}, {}
    if _init_observe(project, observations, directory=True) is None:
        raise OSError(f"init requires an existing ordinary project directory: {project}")

    def directory(path):
        snapshot = _init_observe(path, observations, directory=True)
        directories[path] = "keep" if snapshot is not None else "create"

    def source(path):
        snapshot = _init_observe(path, observations)
        if snapshot is None:
            raise OSError(f"required init source missing: {path}")
        return snapshot[-1]

    def output(path, data, *, retain=False, reason="identical bytes"):
        snapshot = _init_observe(path, observations)
        if snapshot is not None and not retain and snapshot[-1] != data:
            raise OSError(f"init conflict at {path}: existing bytes differ; reconcile explicitly before init")
        files[path] = {"data": data, "action": "keep" if snapshot is not None else "create",
                       "reason": reason if snapshot is not None else "absent destination"}

    def json_input(name):
        target = _control_plane_path(project, name)
        legacy = _legacy_control_plane_path(project, name)
        canonical = _init_observe(target, observations)
        legacy_snapshot = _init_observe(legacy, observations)
        selected = canonical if canonical is not None else legacy_snapshot
        read_path = target if canonical is not None else legacy
        try:
            value = json.loads(selected[-1].decode("utf-8")) if selected is not None else {}
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"init requires valid UTF-8 JSON: {read_path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"init requires a JSON object: {read_path}")
        return target, canonical, value

    canonical = _canonical_context_path(project)
    legacy = _legacy_context_path(project)
    context_snapshot = _init_observe(canonical, observations)
    legacy_snapshot = _init_observe(legacy, observations)
    if context_snapshot is not None:
        output(canonical, context_snapshot[-1], retain=True, reason="existing context retained, contents not validated")
    if legacy_snapshot is not None:
        output(legacy, legacy_snapshot[-1], retain=True, reason="legacy context retained, contents not validated")
    if context_snapshot is None and legacy_snapshot is None:
        template = source(TEMPLATES_DIR / "CLAUDE.md.template").decode("utf-8")
        output(canonical, template.replace(LEGACY_CONTEXT_FILENAME, CANONICAL_CONTEXT_FILENAME).encode("utf-8"))

    documents = {
        "STATUS.md": (
            '# Project Status\n'
            '> Updated: (date) | Session: 0\n'
            '\n'
            '## Current State\n'
            'Project initialized with ControlCoding.\n'
            '\n'
            '## Next Steps\n'
            '- Configure CONTROLCODING.md as the canonical project context\n'
            '- Sync the host-native context files you actually use\n'
            '- Define module boundaries (stable/shared/features/workspace)\n'
            '- Add domain invariants\n'
            '\n'
            '## Blockers\n'
            'None\n'
        ),
        "ROADMAP.md": (
            '# Project Roadmap\n'
            '> Updated: (date)\n'
            '\n'
            '## Current State\n'
            '\n'
            'Project initialized with ControlCoding.\n'
            '\n'
            '## Active Items\n'
            '\n'
            '- [ ] Review and refine CONTROLCODING.md\n'
            '- [ ] Define real module boundaries\n'
            '- [ ] Build the first useful vertical slice\n'
            '- [ ] Add invariant-oriented verification\n'
        ),
        "BUGS.md": (
            '# Known Bugs\n'
            '> Updated: (date)\n'
            '\n'
            'No known bugs recorded yet.\n'
        ),
    }
    for name, text in documents.items():
        output(project / name, text.encode("utf-8"), retain=True, reason="existing document retained, contents not validated")
    directory(project / "devlog")
    hooks_dest = Path(os.path.abspath(_central_hooks_dir())) if central_hooks else project / "hooks"
    directory(hooks_dest)
    for name in INIT_HOOKS:
        output(hooks_dest / name, source(HOOKS_DIR / name))
    directory(project / "tools")
    fitness_dest = project / "tools" / "fitness_check.py"
    output(fitness_dest, source(SCRIPT_DIR / "fitness_check.py"))

    git_dir = project / ".git"
    _init_observe(git_dir, observations, directory=True)
    git_hooks = git_dir / "hooks"
    if _init_observe(git_hooks, observations, directory=True) is not None:
        for name, text in [("pre-commit", _build_repo_precommit_hook_script(hooks_dest, fitness_dest)),
                           ("post-commit", _build_repo_postcommit_hook_script(hooks_dest))]:
            output(git_hooks / name, text.encode("utf-8"), retain=True,
                   reason="existing Git hook retained; generated CC gate not installed here or verified")

    directory(_control_plane_dir(project))
    config_path, config_snapshot, config = json_input("cc_config.json")
    mode = "central" if central_hooks else "local"
    for key, default, allowed in [("hooks_location", "local", {"local", "central"}),
                                  ("documentation_mode", "managed", {"managed", "project_managed"}),
                                  ("cc_artifact_mode", "local_only", {"local_only", "shared_repo"})]:
        value = config.get(key, default)
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"init config conflict at {config_path}: invalid {key}")
    config_present = config_snapshot is not None or observations[_legacy_control_plane_path(project, "cc_config.json")][1] is not None
    if config_present and config.get("hooks_location", "local") != mode:
        raise ValueError(f"init config conflict at {config_path}: hooks_location must be {mode}; reconcile explicitly")
    protected_zones = config.get("protected_zones", [])
    if protected_zones is not None and not isinstance(protected_zones, (list, dict)):
        raise ValueError(f"init config conflict at {config_path}: protected_zones must be a list or deny/warn object")
    if isinstance(protected_zones, dict):
        for level in ("deny", "warn"):
            entries = protected_zones.get(level)
            if entries is not None and not isinstance(entries, list):
                raise ValueError(f"init config conflict at {config_path}: protected_zones.{level} must be a list")
    if config_snapshot is None:
        config = deepcopy(config)
        config.setdefault("documentation_mode", "managed")
        config.setdefault("cc_artifact_mode", "local_only")
        config.setdefault("hooks_location", mode)
        config.setdefault("protected_zones", [
            {"path": "src/core/", "description": "Core modules - stable zone (example)", "level": "deny"},
        ])
    output(config_path, (json.dumps(config, indent=2) + "\n").encode("utf-8"),
           retain=True, reason="compatible config retained, including custom fields")

    ignore = project / ".gitignore"
    block = _build_gitignore_block(central_hooks=central_hooks,
                                  documentation_mode=config.get("documentation_mode", "managed"),
                                  cc_artifact_mode=config.get("cc_artifact_mode", "local_only"))
    ignore_snapshot = _init_observe(ignore, observations)
    if ignore_snapshot is not None:
        lines = ignore_snapshot[-1].decode("utf-8").splitlines()
        required = block.splitlines()
        if not any(lines[i:i + len(required)] == required for i in range(len(lines))):
            raise ValueError(f"init conflict at {ignore}: complete required block missing; reconcile explicitly before init")
    output(ignore, block.encode("utf-8"), retain=True, reason="complete required ignore block retained")

    settings_path, settings_snapshot, settings = json_input("settings.json")
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"init settings conflict at {settings_path}: hooks must be an object")
    for entries in hooks.values():
        if not isinstance(entries, list):
            raise ValueError(f"init settings conflict at {settings_path}: hook events must contain lists")
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("matcher", ""), str)
                    or not isinstance(entry.get("hooks", []), list)):
                raise ValueError(f"init settings conflict at {settings_path}: invalid hook entry")
            for hook in entry.get("hooks", []):
                if not isinstance(hook, dict) or not isinstance(hook.get("command", ""), str):
                    raise ValueError(f"init settings conflict at {settings_path}: invalid hook command")
    if "mcpServers" in settings and not isinstance(settings["mcpServers"], dict):
        raise ValueError(f"init settings conflict at {settings_path}: mcpServers must be an object")
    # Resolve detached inputs consistently; retain settings for conflict comparison.
    merged = _resolve_hook_commands(deepcopy(settings), hooks_dest)
    base = _resolve_hook_commands(deepcopy(BASE_SETTINGS), hooks_dest)
    merged["hooks"] = merge_hooks(merged.get("hooks", {}), base["hooks"])
    if settings_snapshot is not None and merged != settings:
        raise ValueError(f"init conflict at {settings_path}: hook configuration needs changes; reconcile explicitly before init")
    output(settings_path, (json.dumps(merged, indent=2) + "\n").encode("utf-8"),
           retain=True, reason="semantically compatible settings retained")
    _init_recheck(observations)
    return {"observations": observations, "directories": directories, "files": files}


def _init_publish(path: Path, data: bytes, observations: dict, created: list):
    """Publish exclusively; never replace a destination or clean up a foreign stage."""
    import tempfile
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.init-", suffix=".tmp")
    stage = Path(name)
    identity = os.fstat(descriptor)
    owned = (identity.st_dev, identity.st_ino)
    try:
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(data)
            stream.flush()
        snapshot = _init_snapshot(stage)
        if snapshot is None or snapshot[:2] != owned or snapshot[-1] != data:
            raise OSError(f"init stage changed: {stage}")
        _init_recheck(observations)
        os.link(stage, path)
        created.append(path)
        published = _init_snapshot(path)
        if published is None or published[:2] != owned or published[-1] != data:
            raise OSError(f"init publication changed: {path}")
        observations[path] = (False, published)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        # Revalidate directory identities before accessing the lexical stage name.
        for parent in stage.parents:
            expected = observations.get(parent)
            if expected is not None and _init_snapshot(parent, directory=True) != expected[1]:
                raise OSError(f"init stage cleanup refused: parent changed; inspect {stage}")
        from cc_setup import _pack_safe_kind
        entry = _pack_safe_kind(stage, expected="file")
        if entry is not None:
            if (entry.st_dev, entry.st_ino) != owned:
                raise OSError(f"init stage cleanup refused: foreign replacement retained at {stage}")
            stage.unlink()
        # Unlinking the other hard link can change the published inode's ctime.
        if path in created:
            current = _init_snapshot(path)
            if current is None or current[:2] != owned or current[-1] != data:
                raise OSError(f"init output changed during cleanup: {path}")
            observations[path] = (False, current)


def _apply_init(plan: dict, created: list):
    observations = dict(plan["observations"])
    _init_recheck(observations)
    required = set(plan["directories"])
    for path in [*plan["directories"], *plan["files"]]:
        required.update(path.parents)
    for path in sorted(required, key=lambda p: (len(p.parts), str(p))):
        directory, snapshot = observations[path]
        if directory and snapshot is None:
            _init_recheck(observations)
            path.mkdir()
            created.append(path)
            observations[path] = (True, _init_snapshot(path, directory=True))
    for path, item in plan["files"].items():
        _init_recheck(observations)
        if item["action"] == "create":
            _init_publish(path, item["data"], observations, created)
    _init_recheck(observations)


def cmd_init(project: Path, central_hooks: bool = False, quiet: bool = False, *, preview_only: bool = False):
    """Preflight all minimal-init outputs and preserve existing files conservatively."""
    project = Path(os.path.abspath(project))
    if not quiet:
        print(f"\n{'Previewing' if preview_only else 'Initializing'} ControlCoding in: {project}\n")
    try:
        plan = _plan_init(project, central_hooks)
    except (OSError, ValueError, UnicodeError) as exc:
        fail(f"Init preflight conflict (no init writes): {exc}")
        return 1
    for path, action in plan["directories"].items():
        info(f"{action} directory: {path}")
    for path, item in plan["files"].items():
        info(f"{item['action']}: {path} ({item['reason']})")
    if preview_only:
        return 0
    created = []
    try:
        _apply_init(plan, created)
    except (OSError, ValueError) as exc:
        fail(f"Partial initialization: {exc}. No rollback was performed; inspect before retrying.")
        for path in created:
            info(f"Published/created earlier (inspect current state): {path}")
        if not created:
            info("No completed output creation was recorded.")
        return 1
    if not quiet:
        ok("Minimal initialization complete. Retained files are not an enforcement attestation.")
        info(f"Review {CANONICAL_CONTEXT_FILENAME}, module boundaries and host configuration; then run doctor.")
    return 0


def cmd_install(project: Path, pack: str, *, preview_only: bool = False):
    """Plan every selected pack before applying any of its side effects."""
    project = Path(os.path.abspath(project))
    if pack == "all":
        packs = ALL_PACKS
    elif pack in ALL_PACKS:
        packs = [pack]
    else:
        fail(f"Unknown pack: '{pack}'")
        print(f"  Available packs: {', '.join(ALL_PACKS)}, all")
        return 1

    print(f"\n{'Previewing' if preview_only else 'Installing'} pack(s): {', '.join(packs)}")
    print(f"Project: {project}\n")
    try:
        from cc_setup import _plan_pack_install, _apply_pack_install
        plan = _plan_pack_install(project, packs, PACK_FILES, MCP_CONFIGS, HELPER_DIR)
    except (OSError, ValueError, UnicodeError) as exc:
        fail(f"Pack preflight failed: {exc}")
        return 1

    for path, item in plan["files"].items():
        info(f"{item['decision']}: {path}")
    if plan["bridge"] is not None:
        info(f"{'skip' if plan['bridge'].is_dir() else 'create'} directory: {plan['bridge']}")
    if plan["helper"] is not None:
        info(f"{plan['helper']['action']} helper directory: {plan['helper']['path']}")
    info(f"{plan['settings']['action']} settings: {plan['settings']['path']}")
    if preview_only:
        return 0
    try:
        _apply_pack_install(plan, ok_callback=ok)
    except (OSError, ValueError) as exc:
        fail(f"Pack install stopped with partial changes possible: {exc}")
        return 1
    if "dashboard" in packs:
        print("\n  Run dashboard with: python tools/cc_dashboard.py --project-root .")
    print(f"{green('Done!')} Installed: {', '.join(packs)}")
    return 0


def _ask(prompt: str, default: str = "") -> str:
    """Prompt user for input with optional default."""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"  {prompt}: ").strip()


def _ask_yn(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt."""
    suffix = "[Y/n]" if default else "[y/N]"
    result = input(f"  {prompt} {suffix}: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes", "si")


def _generate_claude_md(project: Path, answers: dict):
    """Generate a tailored CLAUDE.md from user answers.

    Delegated to cc_setup.py. This wrapper exists for backward compatibility.
    """
    from cc_setup import _generate_claude_md as _gen
    return _gen(project, answers)


def cmd_setup(project: Path, answers_file: Path | None = None, apply_answers: bool = False):
    """Base setup executor for ControlCoding. Delegated to cc_setup.py."""
    from cc_setup import cmd_setup as _setup
    return _setup(project, answers_file=answers_file, apply_answers=apply_answers)


def cmd_setup_chat_guide(project: Path, host_hint: str = ""):
    """Print a copy/paste prompt for chat-guided setup."""
    from cc_setup import cmd_setup_chat_guide as _setup_chat_guide
    return _setup_chat_guide(project, host_hint=host_hint)


def cmd_setup_project(project: Path, answers_file: Path | None = None, apply_answers: bool = False):
    """Project-definition executor after base installation."""
    from cc_setup import cmd_setup_project as _setup_project
    return _setup_project(project, answers_file=answers_file, apply_answers=apply_answers)


def cmd_setup_project_chat_guide(project: Path, host_hint: str = ""):
    """Print a copy/paste prompt for chat-guided project setup."""
    from cc_setup import cmd_setup_project_chat_guide as _setup_project_chat_guide
    return _setup_project_chat_guide(project, host_hint=host_hint)


def cmd_setup_engagement(project: Path, answers_file: Path | None = None, apply_answers: bool = False):
    """Engagement level configuration. Delegated to cc_setup.py."""
    from cc_setup import cmd_setup_engagement as _engagement
    return _engagement(project, answers_file=answers_file, apply_answers=apply_answers)


_GATEWAY_VALID_UI_MODES = {"visualizer", "api_studio"}
_GATEWAY_VALID_USER_HOSTS = {
    "claude_code",
    "codex_cli",
    "cursor",
    "windsurf",
    "vscode",
    "cline",
    "gemini_cli",
    "other",
}
VALID_HOST_INSTRUCTION_MODES = {
    "preset_only",
    "recommended",
    "custom",
}
_VALID_DOCUMENTATION_MODES = {"managed", "project_managed"}
_VALID_CC_ARTIFACT_MODES = {"local_only", "shared_repo"}
_GATEWAY_FORBIDDEN_BACKEND_FIELDS = (
    "apiKey",
    "sessionToken",
    "oauth",
    "consumerLogin",
    "accessToken",
)


def _has_meaningful_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return bool(value)


def _is_local_endpoint(endpoint: str) -> bool:
    if not isinstance(endpoint, str) or not endpoint.strip():
        return False
    try:
        parsed = urlparse(endpoint)
        host = (parsed.hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}
    except ValueError:
        return "localhost" in endpoint or "127.0.0.1" in endpoint


def _infer_gateway_backend_class(backend: dict) -> str:
    backend_type = str(backend.get("type", "custom"))
    backend_class = backend.get("class")
    if isinstance(backend_class, str) and backend_class:
        return backend_class
    if backend_type == "claude_cli":
        return "official_cli"
    if backend_type == "ollama":
        return "local_runtime"
    if backend_type in {"anthropic", "openai"}:
        return "official_api"
    return "local_runtime" if _is_local_endpoint(backend.get("endpoint", "")) else "official_api"


def _collect_gateway_config_issues(gateway: dict) -> list[str]:
    issues = []

    user_host = gateway.get("userHost")
    if user_host is not None and user_host not in _GATEWAY_VALID_USER_HOSTS:
        issues.append(
            "gateway_config.userHost must be one of "
            + ", ".join(sorted(_GATEWAY_VALID_USER_HOSTS))
        )

    enabled_hosts = gateway.get("enabledHosts")
    if enabled_hosts is not None and not isinstance(enabled_hosts, list):
        issues.append("gateway_config.enabledHosts must be an array when provided")
    elif isinstance(enabled_hosts, list):
        invalid_hosts = [
            str(host_value)
            for host_value in enabled_hosts
            if str(host_value).strip() not in _GATEWAY_VALID_USER_HOSTS
        ]
        if invalid_hosts:
            issues.append(
                "gateway_config.enabledHosts must contain only supported host identifiers"
            )
        if user_host in _GATEWAY_VALID_USER_HOSTS:
            listed_hosts = [
                str(host_value).strip()
                for host_value in enabled_hosts
                if str(host_value).strip() in _GATEWAY_VALID_USER_HOSTS
            ]
            if str(user_host) not in listed_hosts:
                issues.append("gateway_config.enabledHosts must include gateway_config.userHost")

    host_instructions = gateway.get("hostInstructions")
    if host_instructions is not None and not isinstance(host_instructions, dict):
        issues.append("gateway_config.hostInstructions must be an object when provided")
    elif isinstance(host_instructions, dict):
        mode = str(host_instructions.get("mode", "recommended"))
        if mode not in VALID_HOST_INSTRUCTION_MODES:
            issues.append(
                "gateway_config.hostInstructions.mode must be one of "
                + ", ".join(sorted(VALID_HOST_INSTRUCTION_MODES))
            )
        custom_notes = host_instructions.get("customNotes")
        if custom_notes is not None and not isinstance(custom_notes, list):
            issues.append("gateway_config.hostInstructions.customNotes must be an array when provided")
        elif isinstance(custom_notes, list) and any(not isinstance(note, str) for note in custom_notes):
            issues.append("gateway_config.hostInstructions.customNotes must contain only strings")

    ui_mode = gateway.get("uiMode")
    if ui_mode is not None and ui_mode not in _GATEWAY_VALID_UI_MODES:
        issues.append(
            f"gateway_config.uiMode must be one of {', '.join(sorted(_GATEWAY_VALID_UI_MODES))}"
        )

    host_profile = gateway.get("hostProfile")
    if host_profile is not None and not isinstance(host_profile, dict):
        issues.append("gateway_config.hostProfile must be an object when provided")
    elif isinstance(host_profile, dict):
        normalized_host = str(user_host).strip()
        if normalized_host not in _GATEWAY_VALID_USER_HOSTS:
            normalized_host = "other"
        expected_profile = _derive_host_profile(normalized_host)
        if not _host_profile_matches(host_profile, expected_profile):
            issues.append(
                "gateway_config.hostProfile must match gateway_config.userHost; "
                "regenerate it via `cc setup` or `cc host switch <host>`"
            )

    backends = gateway.get("backends", {})
    if backends is None:
        backends = {}
    if not isinstance(backends, dict):
        return ["gateway_config.backends must be an object"]

    backend_classes = {}
    for backend_id, backend in backends.items():
        if not isinstance(backend, dict):
            issues.append(f"gateway_config.backends.{backend_id} must be an object")
            continue

        for field_name in _GATEWAY_FORBIDDEN_BACKEND_FIELDS:
            if _has_meaningful_value(backend.get(field_name)):
                issues.append(
                    f"gateway_config.backends.{backend_id}.{field_name} is not allowed"
                )

        backend_type = str(backend.get("type", ""))
        backend_class = _infer_gateway_backend_class(backend)
        backend_classes[str(backend_id)] = backend_class

        if backend_type == "claude_cli" and backend_class != "official_cli":
            issues.append(
                f"gateway_config.backends.{backend_id} must use class official_cli for claude_cli transport"
            )
        if backend_type == "ollama" and backend_class != "local_runtime":
            issues.append(
                f"gateway_config.backends.{backend_id} must use class local_runtime for ollama transport"
            )
        if backend_type in {"anthropic", "openai"} and backend_class != "official_api":
            issues.append(
                f"gateway_config.backends.{backend_id} must use class official_api for {backend_type} transport"
            )
        if backend_type == "custom" and backend_class == "official_cli":
            issues.append(
                f"gateway_config.backends.{backend_id} cannot use class official_cli for custom transport"
            )

    if ui_mode == "api_studio":
        agents = gateway.get("agents", {})
        if not isinstance(agents, dict):
            issues.append("gateway_config.agents must be an object when provided")
        else:
            refs = []
            main_coder = agents.get("mainCoder", {})
            if isinstance(main_coder, dict) and (
                main_coder.get("active") or main_coder.get("backend")
            ):
                refs.append(("gateway_config.agents.mainCoder", str(main_coder.get("backend", ""))))

            codewarden = agents.get("codewarden", {})
            if isinstance(codewarden, dict) and (
                codewarden.get("active") or codewarden.get("backend")
            ):
                refs.append(("gateway_config.agents.codewarden", str(codewarden.get("backend", ""))))

            narrator = agents.get("narrator", {})
            if isinstance(narrator, dict) and (
                narrator.get("active") or narrator.get("backend")
            ):
                refs.append(("gateway_config.agents.narrator", str(narrator.get("backend", ""))))

            consultants = agents.get("consultants", {})
            defaults = consultants.get("defaults", []) if isinstance(consultants, dict) else []
            if isinstance(defaults, list):
                for index, entry in enumerate(defaults):
                    if isinstance(entry, dict) and entry.get("backend"):
                        refs.append(
                            (
                                f"gateway_config.agents.consultants.defaults[{index}]",
                                str(entry.get("backend", "")),
                            )
                        )

            for label, backend_id in refs:
                backend_class = backend_classes.get(backend_id)
                if backend_class == "official_cli":
                    issues.append(
                        f"{label} uses official_cli backend '{backend_id}', which requires uiMode=visualizer"
                    )

    return issues


def _normalize_protected_zones(raw) -> list[dict]:
    """Normalize protected_zones to the canonical list-of-dicts shape."""
    normalized = []

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                normalized.append({"path": item, "level": "warn", "description": ""})
            elif isinstance(item, dict) and "path" in item:
                normalized.append({
                    "path": item.get("path", ""),
                    "level": item.get("level", "warn"),
                    "description": item.get("description", ""),
                })
        return normalized

    if isinstance(raw, dict):
        for level in ("deny", "warn"):
            entries = raw.get(level, [])
            if not isinstance(entries, list):
                continue
            for item in entries:
                if isinstance(item, str):
                    normalized.append({"path": item, "level": level, "description": ""})
                elif isinstance(item, dict) and "path" in item:
                    normalized.append({
                        "path": item.get("path", ""),
                        "level": item.get("level", level),
                        "description": item.get("description", ""),
                    })

    return normalized


def _default_controlled_write_path() -> dict:
    return {
        "enabled": False,
        "mode": "off",
        "scope": "opt_in",
        "patch_format": "unified_diff",
        "require_lift_for_deny": True,
        "measure_ux": True,
        "require_manifest_for_apply": False,
        "preflight_fitness_for_apply": False,
    }


def _normalize_controlled_write_path(raw) -> dict:
    """Normalize controlled_write_path to a stable opt-in config shape."""
    normalized = _default_controlled_write_path()

    if raw is None:
        return normalized

    if isinstance(raw, bool):
        normalized["enabled"] = raw
        normalized["mode"] = "patch_gateway" if raw else "off"
        return normalized

    if isinstance(raw, str):
        raw_mode = raw.strip()
        if raw_mode in _VALID_CONTROLLED_WRITE_MODES:
            normalized["mode"] = raw_mode
            normalized["enabled"] = raw_mode != "off"
        return normalized

    if not isinstance(raw, dict):
        return normalized

    if isinstance(raw.get("enabled"), bool):
        normalized["enabled"] = raw["enabled"]
    elif raw.get("enabled") is not None:
        normalized["enabled"] = bool(raw.get("enabled"))

    mode = str(raw.get("mode", "")).strip()
    if mode in _VALID_CONTROLLED_WRITE_MODES:
        normalized["mode"] = mode
    elif normalized["enabled"]:
        normalized["mode"] = "patch_gateway"

    scope = str(raw.get("scope", normalized["scope"])).strip()
    if scope in _VALID_CONTROLLED_WRITE_SCOPES:
        normalized["scope"] = scope

    patch_format = str(raw.get("patch_format", normalized["patch_format"])).strip()
    if patch_format in _VALID_CONTROLLED_WRITE_PATCH_FORMATS:
        normalized["patch_format"] = patch_format

    if isinstance(raw.get("require_lift_for_deny"), bool):
        normalized["require_lift_for_deny"] = raw["require_lift_for_deny"]
    if isinstance(raw.get("measure_ux"), bool):
        normalized["measure_ux"] = raw["measure_ux"]
    if isinstance(raw.get("require_manifest_for_apply"), bool):
        normalized["require_manifest_for_apply"] = raw["require_manifest_for_apply"]
    if isinstance(raw.get("preflight_fitness_for_apply"), bool):
        normalized["preflight_fitness_for_apply"] = raw["preflight_fitness_for_apply"]

    if normalized["mode"] != "off" and raw.get("enabled") is None:
        normalized["enabled"] = True

    if normalized["mode"] == "off":
        normalized["enabled"] = False
    elif normalized["enabled"]:
        normalized["mode"] = "patch_gateway"

    return normalized


def _load_controlled_write_path(project: Path, cc_config: dict | None = None) -> dict:
    config = cc_config if isinstance(cc_config, dict) else _read_json_object(_control_plane_read_path(project, "cc_config.json"))
    return _normalize_controlled_write_path(config.get("controlled_write_path"))


def _controlled_write_metrics_path(project: Path) -> Path:
    return _control_plane_path(project, WRITE_PATH_METRICS_FILENAME)


def _controlled_write_events_path(project: Path) -> Path:
    return _control_plane_path(project, WRITE_PATH_EVENTS_FILENAME)


def _controlled_write_manifest_dir(project: Path) -> Path:
    return _control_plane_path(project, WRITE_PATH_MANIFEST_DIRNAME)


def _controlled_write_receipt_dir(project: Path) -> Path:
    return _control_plane_path(project, WRITE_PATH_RECEIPT_DIRNAME)


def _controlled_write_shadow_dir(project: Path) -> Path:
    return _control_plane_path(project, WRITE_PATH_SHADOW_DIRNAME)


def _load_controlled_write_metrics(project: Path) -> dict:
    metrics = _read_json_object(_controlled_write_metrics_path(project))
    if not metrics:
        return {
            "schema_version": 1,
            "attempts": 0,
            "check_only_runs": 0,
            "prepared": 0,
            "preflight_runs": 0,
            "applied": 0,
            "blocked": 0,
            "invalid": 0,
            "warned": 0,
            "manifest_blocked": 0,
            "preflight_blocked": 0,
        }
    base = {
        "schema_version": 1,
        "attempts": 0,
        "check_only_runs": 0,
        "prepared": 0,
        "preflight_runs": 0,
        "applied": 0,
        "blocked": 0,
        "invalid": 0,
        "warned": 0,
        "manifest_blocked": 0,
        "preflight_blocked": 0,
    }
    base.update(metrics)
    return base


def _append_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _record_controlled_write_attempt(project: Path,
                                     *,
                                     operation: str,
                                     status: str,
                                     touched_files: list[str],
                                     deny_hits: list[dict],
                                     warn_hits: list[dict],
                                     validation_ms: int,
                                     apply_ms: int = 0,
                                     reason: str = "",
                                     manifest_id: str = ""):
    metrics = _load_controlled_write_metrics(project)
    metrics["attempts"] = int(metrics.get("attempts", 0)) + 1
    if operation == "check":
        metrics["check_only_runs"] = int(metrics.get("check_only_runs", 0)) + 1
    if operation == "prepare":
        metrics["prepared"] = int(metrics.get("prepared", 0)) + 1
    if operation == "preflight":
        metrics["preflight_runs"] = int(metrics.get("preflight_runs", 0)) + 1
    if status == "applied":
        metrics["applied"] = int(metrics.get("applied", 0)) + 1
    elif status == "blocked":
        metrics["blocked"] = int(metrics.get("blocked", 0)) + 1
    elif status == "invalid":
        metrics["invalid"] = int(metrics.get("invalid", 0)) + 1
    elif status == "manifest_blocked":
        metrics["manifest_blocked"] = int(metrics.get("manifest_blocked", 0)) + 1
    elif status == "preflight_blocked":
        metrics["preflight_blocked"] = int(metrics.get("preflight_blocked", 0)) + 1
    if warn_hits:
        metrics["warned"] = int(metrics.get("warned", 0)) + 1
    metrics["last_status"] = status
    metrics["last_operation"] = operation
    metrics["last_touched_files"] = touched_files
    metrics["last_touched_count"] = len(touched_files)
    metrics["last_validation_ms"] = validation_ms
    metrics["last_apply_ms"] = apply_ms
    metrics["last_run_at"] = _utc_now_iso()
    metrics["last_manifest_id"] = manifest_id
    _write_json_atomic(_controlled_write_metrics_path(project), metrics)

    _append_jsonl(
        _controlled_write_events_path(project),
        {
            "ts": metrics["last_run_at"],
            "operation": operation,
            "status": status,
            "touched_files": touched_files,
            "deny_hits": deny_hits,
            "warn_hits": warn_hits,
            "validation_ms": validation_ms,
            "apply_ms": apply_ms,
            "reason": reason,
            "manifest_id": manifest_id,
        },
    )


def _manifest_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_write_path_manifest_id(patch_text: str) -> str:
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"wpm_{timestamp}_{_manifest_sha256(patch_text)[:10]}"


def _write_path_manifest_path(project: Path, manifest_id: str) -> Path:
    filename = f"{manifest_id}.json"
    canonical = _controlled_write_manifest_dir(project) / filename
    legacy = _legacy_control_plane_path(project, WRITE_PATH_MANIFEST_DIRNAME, filename)
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def _write_path_receipt_path(project: Path, receipt_id: str) -> Path:
    filename = f"{receipt_id}.json"
    canonical = _controlled_write_receipt_dir(project) / filename
    legacy = _legacy_control_plane_path(project, WRITE_PATH_RECEIPT_DIRNAME, filename)
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def _load_write_path_manifest(project: Path, manifest_id: str) -> dict:
    if not manifest_id:
        return {}
    return _read_json_object(_write_path_manifest_path(project, manifest_id))


def _load_write_path_receipt(project: Path, receipt_id: str) -> dict:
    if not receipt_id:
        return {}
    return _read_json_object(_write_path_receipt_path(project, receipt_id))


def _list_write_path_manifests(project: Path) -> list[dict]:
    directory = _controlled_write_manifest_dir(project)
    manifests: list[dict] = []
    if not directory.exists():
        legacy_dir = _legacy_control_plane_path(project, WRITE_PATH_MANIFEST_DIRNAME)
        if legacy_dir.exists() and legacy_dir.is_dir():
            directory = legacy_dir
        else:
            return manifests
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = _read_json_object(path)
        if payload:
            manifests.append(payload)
    return manifests


def _list_write_path_receipts(project: Path) -> list[dict]:
    directory = _controlled_write_receipt_dir(project)
    receipts: list[dict] = []
    if not directory.exists():
        legacy_dir = _legacy_control_plane_path(project, WRITE_PATH_RECEIPT_DIRNAME)
        if legacy_dir.exists() and legacy_dir.is_dir():
            directory = legacy_dir
        else:
            return receipts
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = _read_json_object(path)
        if payload:
            receipts.append(payload)
    return receipts


def _latest_write_path_receipt(project: Path) -> dict:
    receipts = _list_write_path_receipts(project)
    return receipts[0] if receipts else {}


def _build_write_path_receipt_id(operation: str, patch_text: str) -> str:
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = str(operation or "event").strip().replace(" ", "_") or "event"
    return f"wpr_{prefix}_{timestamp}_{_manifest_sha256(patch_text)[:10]}"


def _summarize_write_path_receipt(receipt: dict) -> dict:
    if not isinstance(receipt, dict) or not receipt:
        return {}
    touched_files = receipt.get("touched_files", [])
    return {
        "receiptId": str(receipt.get("receipt_id", "")).strip(),
        "createdAt": str(receipt.get("created_at", "")).strip(),
        "operation": str(receipt.get("operation", "")).strip(),
        "status": str(receipt.get("status", "")).strip(),
        "manifestId": str(receipt.get("manifest_id", "")).strip(),
        "touchedCount": len(touched_files) if isinstance(touched_files, list) else 0,
        "preflightRan": bool(receipt.get("preflight", {}).get("ran")),
        "preflightOk": bool(receipt.get("preflight", {}).get("ok")),
    }


def _persist_write_path_receipt(project: Path, *, status: str, payload: dict) -> dict:
    operation = str(payload.get("operation", "")).strip() or "unknown"
    patch_file = Path(str(payload.get("patchFile", "")).strip()) if str(payload.get("patchFile", "")).strip() else None
    touched_files = payload.get("touchedFiles", [])
    patch_text_hash_source = json.dumps(
        {
            "operation": operation,
            "status": status,
            "patch_file": str(patch_file) if patch_file else "",
            "touched_files": touched_files,
            "manifest_id": str(payload.get("manifestId", "")).strip(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    receipt = {
        "schema_version": WRITE_PATH_RECEIPT_SCHEMA_VERSION,
        "receipt_id": _build_write_path_receipt_id(operation, patch_text_hash_source),
        "created_at": _utc_now_iso(),
        "operation": operation,
        "status": status,
        "mode": str(payload.get("mode", "")).strip(),
        "patch_file": _project_relative_label(project, patch_file) if patch_file else "",
        "manifest_id": str(payload.get("manifestId", "")).strip(),
        "manifest_verified": bool(payload.get("manifestVerified")),
        "touched_files": touched_files if isinstance(touched_files, list) else [],
        "deny_hits": payload.get("denyHits", []),
        "warn_hits": payload.get("warnHits", []),
        "validation_ok": bool(payload.get("validationOk")),
        "validation_detail": str(payload.get("validationDetail", "")).strip(),
        "validation_ms": int(payload.get("validationMs", 0) or 0),
        "apply_ms": int(payload.get("applyMs", 0) or 0),
        "reason": str(payload.get("reason", "")).strip(),
        "error": str(payload.get("error", "")).strip(),
        "preflight": {
            "configured": bool(payload.get("preflightFitnessConfigured")),
            "ran": bool(payload.get("preflightFitnessRan")),
            "ok": bool(payload.get("preflightFitnessOk")),
            "detail": str(payload.get("preflightFitnessDetail", "")).strip(),
            "warning": str(payload.get("preflightFitnessWarning", "")).strip(),
        },
    }
    receipt_path = _controlled_write_receipt_dir(project) / f"{receipt['receipt_id']}.json"
    _write_json_atomic(receipt_path, receipt)
    payload["receiptId"] = receipt["receipt_id"]
    payload["receiptPath"] = _project_relative_label(project, receipt_path)
    return receipt


def _snapshot_write_path_targets(project: Path, touched_files: list[str]) -> list[dict]:
    snapshot: list[dict] = []
    for relative_path in touched_files:
        normalized = relative_path.replace("\\", "/").lstrip("./")
        target = project / normalized
        if target.exists() and target.is_file():
            try:
                content = target.read_bytes()
            except OSError:
                snapshot.append({
                    "path": normalized,
                    "state": "unreadable",
                })
                continue
            snapshot.append({
                "path": normalized,
                "state": "present",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            })
        elif target.exists():
            snapshot.append({
                "path": normalized,
                "state": "non_file",
            })
        else:
            snapshot.append({
                "path": normalized,
                "state": "missing",
            })
    return snapshot


def _validate_write_path_target_snapshot(project: Path, baseline_snapshot: list[dict]) -> tuple[bool, list[dict]]:
    if not isinstance(baseline_snapshot, list):
        return False, [{"path": "", "reason": "invalid baseline snapshot"}]

    current_by_path = {
        entry["path"]: entry
        for entry in _snapshot_write_path_targets(
            project,
            [
                str(entry.get("path", "")).strip()
                for entry in baseline_snapshot
                if isinstance(entry, dict) and str(entry.get("path", "")).strip()
            ],
        )
    }
    mismatches: list[dict] = []
    for entry in baseline_snapshot:
        if not isinstance(entry, dict):
            mismatches.append({"path": "", "reason": "invalid baseline entry"})
            continue
        path = str(entry.get("path", "")).strip()
        if not path:
            mismatches.append({"path": "", "reason": "missing baseline path"})
            continue
        expected_state = str(entry.get("state", "")).strip()
        current = current_by_path.get(path, {"path": path, "state": "missing"})
        current_state = str(current.get("state", "")).strip()
        if expected_state != current_state:
            mismatches.append({
                "path": path,
                "reason": f"state drift ({expected_state} -> {current_state})",
            })
            continue
        if expected_state == "present":
            if str(entry.get("sha256", "")).strip() != str(current.get("sha256", "")).strip():
                mismatches.append({
                    "path": path,
                    "reason": "content drift since prepare",
                })
    return len(mismatches) == 0, mismatches


def _build_write_path_manifest(project: Path,
                               *,
                               patch_file: Path,
                               patch_text: str,
                               touched_files: list[str],
                               deny_hits: list[dict],
                               warn_hits: list[dict],
                               validation_ok: bool,
                               validation_detail: str,
                               validation_ms: int,
                               reason: str = "") -> dict:
    return {
        "schema_version": WRITE_PATH_MANIFEST_SCHEMA_VERSION,
        "manifest_id": _build_write_path_manifest_id(patch_text),
        "created_at": _utc_now_iso(),
        "patch_file": _project_relative_label(project, patch_file),
        "patch_sha256": _manifest_sha256(patch_text),
        "patch_size_bytes": len(patch_text.encode("utf-8")),
        "touched_files": touched_files,
        "baseline_snapshot": _snapshot_write_path_targets(project, touched_files),
        "deny_hits": deny_hits,
        "warn_hits": warn_hits,
        "validation_ok": bool(validation_ok),
        "validation_detail": validation_detail or "",
        "validation_ms": validation_ms,
        "reason": reason,
        "used": False,
        "used_at": "",
    }


def _save_write_path_manifest(project: Path, manifest: dict) -> Path:
    manifest_id = str(manifest.get("manifest_id", "")).strip()
    path = _write_path_manifest_path(project, manifest_id)
    _write_json_atomic(path, manifest)
    return path


def _mark_write_path_manifest_used(project: Path,
                                   manifest: dict,
                                   *,
                                   patch_file: Path) -> dict:
    updated = dict(manifest)
    updated["used"] = True
    updated["used_at"] = _utc_now_iso()
    updated["last_applied_patch_file"] = _project_relative_label(project, patch_file)
    updated["last_apply_event"] = "cc write-path apply"
    _write_json_atomic(_write_path_manifest_path(project, str(updated.get("manifest_id", "")).strip()), updated)
    return updated


def _validate_write_path_manifest(project: Path,
                                  manifest_id: str,
                                  *,
                                  patch_text: str,
                                  touched_files: list[str]) -> tuple[dict, str]:
    normalized_id = str(manifest_id or "").strip()
    if not normalized_id:
        return {}, ""

    manifest = _load_write_path_manifest(project, normalized_id)
    if not manifest:
        return {}, f"Manifest not found: {normalized_id}"

    try:
        schema_version = int(manifest.get("schema_version", 0))
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != WRITE_PATH_MANIFEST_SCHEMA_VERSION:
        return manifest, f"Manifest {normalized_id} uses an unsupported schema version."

    if manifest.get("used"):
        return manifest, f"Manifest {normalized_id} has already been used."

    if not manifest.get("validation_ok", False):
        return manifest, f"Manifest {normalized_id} was not prepared from a valid patch."

    expected_sha = str(manifest.get("patch_sha256", "")).strip()
    actual_sha = _manifest_sha256(patch_text)
    if expected_sha != actual_sha:
        return manifest, f"Manifest {normalized_id} does not match the supplied patch file hash."

    expected_files = sorted(
        str(path).replace("\\", "/").lstrip("./")
        for path in manifest.get("touched_files", [])
        if isinstance(path, str)
    )
    actual_files = sorted(path.replace("\\", "/").lstrip("./") for path in touched_files)
    if expected_files != actual_files:
        return manifest, f"Manifest {normalized_id} does not match the supplied patch file paths."

    baseline_ok, baseline_mismatches = _validate_write_path_target_snapshot(
        project,
        manifest.get("baseline_snapshot", []),
    )
    if not baseline_ok:
        first = baseline_mismatches[0]
        mismatch_path = str(first.get("path", "")).strip() or "(unknown path)"
        mismatch_reason = str(first.get("reason", "baseline drift")).strip()
        return manifest, (
            f"Manifest {normalized_id} no longer matches the current target baseline: "
            f"{mismatch_path} ({mismatch_reason}). Re-run `cc write-path prepare`."
        )

    return manifest, ""


def _safe_remove_write_path_shadow(project: Path, shadow_root: Path):
    allowed_root = _controlled_write_shadow_dir(project).resolve()
    try:
        resolved = shadow_root.resolve()
        resolved.relative_to(allowed_root)
    except (OSError, ValueError):
        return
    if resolved.exists():
        shutil.rmtree(resolved, ignore_errors=True)


def _mirror_current_file_to_shadow(project: Path, shadow_root: Path, relative_path: str):
    normalized = relative_path.replace("\\", "/").lstrip("./")
    source = project / normalized
    target = shadow_root / normalized
    try:
        target.resolve().relative_to(shadow_root.resolve())
    except ValueError:
        return
    if source.exists() and source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            try:
                target.unlink()
            except OSError:
                pass


def _copy_control_plane_inputs_to_shadow(project: Path, shadow_root: Path):
    shadow_control_dir = shadow_root / CONTROL_PLANE_DIRNAME
    for filename in ("cc_config.json", "gateway_config.json", "cc_engagement.json", "settings.json"):
        source = _control_plane_read_path(project, filename)
        if not source.exists() or not source.is_file():
            continue
        target = shadow_control_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    fitness_config = project / "fitness.json"
    if fitness_config.exists() and fitness_config.is_file():
        shutil.copy2(fitness_config, shadow_root / "fitness.json")


def _create_shadow_worktree(project: Path, shadow_root: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(shadow_root), "HEAD"],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except OSError as exc:
        return False, str(exc)
    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    return result.returncode == 0, detail


def _remove_shadow_worktree(project: Path, shadow_root: Path):
    if not shadow_root.exists():
        return
    try:
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(shadow_root)],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(project),
                capture_output=True,
                text=True,
                timeout=30,
            )
            return
    except OSError:
        pass
    _safe_remove_write_path_shadow(project, shadow_root)


def _run_shadow_fitness_preflight(project: Path,
                                  *,
                                  patch_text: str,
                                  touched_files: list[str]) -> dict:
    shadow_id = f"wps_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{_manifest_sha256(patch_text)[:8]}"
    shadow_root = _controlled_write_shadow_dir(project) / shadow_id
    ok_worktree, worktree_detail = _create_shadow_worktree(project, shadow_root)
    if not ok_worktree:
        return {
            "ok": False,
            "blocked": True,
            "detail": worktree_detail or "Could not create shadow worktree for preflight.",
            "shadowRoot": str(shadow_root),
            "warning": "",
        }

    try:
        _copy_control_plane_inputs_to_shadow(project, shadow_root)
        for relative_path in touched_files:
            _mirror_current_file_to_shadow(project, shadow_root, relative_path)

        apply_ok, apply_detail = _run_git_apply(shadow_root, patch_text, check_only=False)
        if not apply_ok:
            return {
                "ok": False,
                "blocked": True,
                "detail": apply_detail or "Patch could not be replayed in the shadow worktree.",
                "shadowRoot": str(shadow_root),
                "warning": "",
            }

        fitness_script = SCRIPT_DIR / "fitness_check.py"
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(fitness_script),
                    "--project-root",
                    str(shadow_root),
                    "--ci",
                    "--no-save",
                ],
                cwd=str(project),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except OSError as exc:
            return {
                "ok": False,
                "blocked": True,
                "detail": str(exc),
                "shadowRoot": str(shadow_root),
                "warning": "",
            }

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode == 1:
            return {
                "ok": False,
                "blocked": True,
                "detail": stdout or stderr or "Shadow preflight found hard fitness violations.",
                "shadowRoot": str(shadow_root),
                "warning": "",
            }
        if result.returncode not in {0, 2}:
            return {
                "ok": False,
                "blocked": True,
                "detail": stdout or stderr or f"Shadow preflight failed unexpectedly (exit {result.returncode}).",
                "shadowRoot": str(shadow_root),
                "warning": "",
            }
        return {
            "ok": True,
            "blocked": False,
            "detail": stdout or stderr or "Shadow preflight passed.",
            "shadowRoot": str(shadow_root),
            "warning": stdout if result.returncode == 2 else "",
        }
    finally:
        _remove_shadow_worktree(project, shadow_root)


def _normalize_patch_header_path(raw_path: str) -> str | None:
    candidate = str(raw_path or "").strip()
    if not candidate or candidate == "/dev/null":
        return None
    if candidate.startswith("a/") or candidate.startswith("b/"):
        candidate = candidate[2:]
    candidate = candidate.replace("\\", "/").lstrip("./")
    return candidate or None


def _extract_patch_paths(patch_text: str) -> list[str]:
    touched: set[str] = set()
    for raw_line in patch_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            for token in parts[2:4]:
                normalized = _normalize_patch_header_path(token)
                if normalized:
                    touched.add(normalized)
            continue
        for prefix in ("rename from ", "rename to ", "copy from ", "copy to "):
            if line.startswith(prefix):
                normalized = _normalize_patch_header_path(line[len(prefix):])
                if normalized:
                    touched.add(normalized)
                break
        else:
            if line.startswith("--- ") or line.startswith("+++ "):
                normalized = _normalize_patch_header_path(line[4:])
                if normalized:
                    touched.add(normalized)
    return sorted(touched)


def _path_matches_zone(relative_path: str, zone_path: str) -> bool:
    normalized_file = relative_path.replace("\\", "/").lstrip("./")
    normalized_zone = zone_path.replace("\\", "/").lstrip("./").rstrip("/")
    if not normalized_zone:
        return False
    return normalized_file == normalized_zone or normalized_file.startswith(normalized_zone + "/")


def _load_approved_lifts(project: Path) -> set[str]:
    lift_path = _control_plane_read_path(project, "lift_request.json")
    if not lift_path.exists():
        return set()
    try:
        payload = json.loads(lift_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()

    if str(payload.get("status", "")).strip().upper() != "APPROVED":
        return set()

    expires_at = str(payload.get("expires_at", "")).strip()
    if expires_at:
        expiry_ts = _parse_iso_timestamp(expires_at)
        if expiry_ts is None or time.time() > expiry_ts:
            return set()

    approved = set()
    for zone in payload.get("zones", []):
        if isinstance(zone, str) and zone.strip():
            approved.add(zone.strip().replace("\\", "/").rstrip("/"))
    return approved


def _evaluate_controlled_write_paths(paths: list[str],
                                     protected_zones: list[dict],
                                     approved_lifts: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    deny_hits: list[dict] = []
    warn_hits: list[dict] = []
    active_lifts = approved_lifts or set()

    for relative_path in paths:
        matched_denies: list[dict] = []
        matched_warns: list[dict] = []
        for zone in protected_zones:
            zone_path = str(zone.get("path", "")).strip()
            if not _path_matches_zone(relative_path, zone_path):
                continue
            normalized_zone = zone_path.replace("\\", "/").rstrip("/")
            if normalized_zone in active_lifts:
                continue
            entry = {
                "file": relative_path,
                "zone": zone_path,
                "description": str(zone.get("description", "")).strip(),
                "level": str(zone.get("level", "warn")).strip().lower(),
            }
            if entry["level"] == "deny":
                matched_denies.append(entry)
            else:
                matched_warns.append(entry)

        deny_hits.extend(matched_denies)
        if not matched_denies:
            warn_hits.extend(matched_warns)

    return deny_hits, warn_hits


def _format_zone_hit(entry: dict) -> str:
    detail = f" - {entry['description']}" if entry.get("description") else ""
    return f"{entry['file']} -> {entry['zone']}{detail}"


def _check_patch_gateway_prereqs(project: Path) -> str | None:
    if shutil.which("git") is None:
        return "git is not available; patch gateway needs git apply"
    if not (project / ".git").is_dir():
        return "project is not a git repository; patch gateway needs .git/"
    return None


def _check_write_path_preflight_prereqs(project: Path) -> str | None:
    prereq_issue = _check_patch_gateway_prereqs(project)
    if prereq_issue:
        return prereq_issue
    fitness_script = SCRIPT_DIR / "fitness_check.py"
    if not fitness_script.exists():
        return "fitness_check.py is not available; shadow preflight cannot run"
    return None


def _run_git_apply(project: Path, patch_text: str, *, check_only: bool) -> tuple[bool, str]:
    command = ["git", "apply", "--recount", "--whitespace=nowarn"]
    if check_only:
        command.append("--check")
    command.append("-")
    try:
        result = subprocess.run(
            command,
            cwd=str(project),
            input=patch_text,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError as exc:
        return False, str(exc)
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    return result.returncode == 0, stderr or stdout


def _controlled_write_status_payload(project: Path) -> dict:
    gateway = _read_json_object(_control_plane_read_path(project, "gateway_config.json"))
    primary_host, _enabled_hosts = _normalize_gateway_hosts(gateway)
    host_profile = _derive_host_profile(primary_host)
    cc_config = _read_json_object(_control_plane_read_path(project, "cc_config.json"))
    config = _load_controlled_write_path(project, cc_config)
    prereq_issue = _check_patch_gateway_prereqs(project) if config["enabled"] else ""
    preflight_issue = (
        _check_write_path_preflight_prereqs(project)
        if config["enabled"] and config.get("preflight_fitness_for_apply")
        else ""
    )
    manifests = _list_write_path_manifests(project)
    latest_receipt = _latest_write_path_receipt(project)
    return {
        "enabled": config["enabled"],
        "mode": config["mode"],
        "scope": config["scope"],
        "patchFormat": config["patch_format"],
        "requireLiftForDeny": config["require_lift_for_deny"],
        "measureUx": config["measure_ux"],
        "requireManifestForApply": config["require_manifest_for_apply"],
        "preflightFitnessForApply": config["preflight_fitness_for_apply"],
        "preflightFitnessBeforeApply": bool(
            config["enabled"] and config.get("preflight_fitness_for_apply") and not preflight_issue and not prereq_issue
        ),
        "applyPrereqIssue": prereq_issue or preflight_issue or "",
        "manifestRequirement": "required" if config["require_manifest_for_apply"] else "optional",
        "manifestStore": _project_relative_label(project, _controlled_write_manifest_dir(project)),
        "manifestCount": len(manifests),
        "receiptStore": _project_relative_label(project, _controlled_write_receipt_dir(project)),
        "receiptCount": len(_list_write_path_receipts(project)),
        "latestReceipt": _summarize_write_path_receipt(latest_receipt),
        "host": {
            "userHost": primary_host,
            "label": host_profile["label"],
            "inlineBoundary": host_profile["inlineBoundaryGate"],
            "protectionModel": host_profile["protectionModel"],
        },
        "preventsProtectedWritesBeforeApply": bool(config["enabled"] and not prereq_issue),
        "honestyNote": (
            "This is an opt-in CC-owned patch gateway, not implicit host parity. "
            "Writes are only screened when they pass through cc write-path."
        ),
        "prereqIssue": prereq_issue or "",
        "preflightPrereqIssue": preflight_issue or "",
        "metrics": _load_controlled_write_metrics(project),
    }


def _collect_cc_config_issues(config: dict) -> list[str]:
    issues = []
    if not isinstance(config, dict):
        return ["cc_config must be a JSON object"]

    documentation_mode = config.get("documentation_mode", "managed")
    if documentation_mode not in _VALID_DOCUMENTATION_MODES:
        issues.append(
            "cc_config.documentation_mode must be one of "
            + ", ".join(sorted(_VALID_DOCUMENTATION_MODES))
        )

    cc_artifact_mode = config.get("cc_artifact_mode", "local_only")
    if cc_artifact_mode not in _VALID_CC_ARTIFACT_MODES:
        issues.append(
            "cc_config.cc_artifact_mode must be one of "
            + ", ".join(sorted(_VALID_CC_ARTIFACT_MODES))
        )

    hooks_location = config.get("hooks_location", "local")
    if hooks_location not in {"local", "central"}:
        issues.append("cc_config.hooks_location must be 'local' or 'central'")

    protected_zones = config.get("protected_zones", [])
    if protected_zones is not None and not isinstance(protected_zones, (list, dict)):
        issues.append(
            "cc_config.protected_zones must be a list or legacy deny/warn object when provided"
        )
    elif isinstance(protected_zones, dict):
        for level in ("deny", "warn"):
            entries = protected_zones.get(level)
            if entries is not None and not isinstance(entries, list):
                issues.append(f"cc_config.protected_zones.{level} must be an array when provided")

    controlled_write = config.get("controlled_write_path")
    if controlled_write is not None:
        if isinstance(controlled_write, bool):
            pass
        elif isinstance(controlled_write, str):
            if controlled_write not in _VALID_CONTROLLED_WRITE_MODES:
                issues.append(
                    "cc_config.controlled_write_path string form must be one of "
                    + ", ".join(sorted(_VALID_CONTROLLED_WRITE_MODES))
                )
        elif not isinstance(controlled_write, dict):
            issues.append(
                "cc_config.controlled_write_path must be a boolean, mode string, or object when provided"
            )
        else:
            enabled = controlled_write.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                issues.append("cc_config.controlled_write_path.enabled must be a boolean when provided")

            mode = controlled_write.get("mode")
            if mode is not None and str(mode) not in _VALID_CONTROLLED_WRITE_MODES:
                issues.append(
                    "cc_config.controlled_write_path.mode must be one of "
                    + ", ".join(sorted(_VALID_CONTROLLED_WRITE_MODES))
                )

            scope = controlled_write.get("scope")
            if scope is not None and str(scope) not in _VALID_CONTROLLED_WRITE_SCOPES:
                issues.append(
                    "cc_config.controlled_write_path.scope must be one of "
                    + ", ".join(sorted(_VALID_CONTROLLED_WRITE_SCOPES))
                )

            patch_format = controlled_write.get("patch_format")
            if patch_format is not None and str(patch_format) not in _VALID_CONTROLLED_WRITE_PATCH_FORMATS:
                issues.append(
            "cc_config.controlled_write_path.patch_format must be one of "
                    + ", ".join(sorted(_VALID_CONTROLLED_WRITE_PATCH_FORMATS))
                )

            for bool_field in (
                "require_lift_for_deny",
                "measure_ux",
                "require_manifest_for_apply",
                "preflight_fitness_for_apply",
            ):
                value = controlled_write.get(bool_field)
                if value is not None and not isinstance(value, bool):
                    issues.append(
                        f"cc_config.controlled_write_path.{bool_field} must be a boolean when provided"
                    )

    planning = config.get("planning")
    if planning is not None:
        if not isinstance(planning, dict):
            issues.append("cc_config.planning must be an object when provided")
        else:
            tier = planning.get("tier", "core")
            if tier not in {"core", "agents", "studio"}:
                issues.append("cc_config.planning.tier must be one of core, agents, studio")
            mode = planning.get("planning_mode", "solo_structured")
            if mode not in {
                "solo_structured",
                "solo_structured_manual_consultation_ready",
                "orchestrated_specialists",
            }:
                issues.append("cc_config.planning.planning_mode is invalid")
            authority = planning.get("planning_authority", "single_author")
            if authority not in {
                "single_author",
                "single_author_with_manual_consultation",
                "orchestrated_multi_role",
            }:
                issues.append("cc_config.planning.planning_authority is invalid")
            manual = planning.get("manual_consultation_allowed", False)
            if not isinstance(manual, bool):
                issues.append("cc_config.planning.manual_consultation_allowed must be a boolean")

    return issues


def _release_git_tracked_files(project: Path) -> tuple[list[str], str]:
    if shutil.which("git") is None:
        return [], "git unavailable"
    if not (project / ".git").exists():
        return [], "not a git repository"
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return [], f"git ls-files failed: {exc}"
    if result.returncode != 0:
        return [], (result.stderr or result.stdout or "git ls-files failed").strip()
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()], ""


def _release_forbidden_tracked_files(tracked_files: list[str]) -> list[str]:
    forbidden: list[str] = []
    forbidden_path_terms = [
        "".join(parts).casefold()
        for parts in _RELEASE_FORBIDDEN_TRACKED_PATH_TERM_PARTS
    ]
    for tracked in tracked_files:
        folded_tracked = tracked.casefold()
        if tracked in _RELEASE_FORBIDDEN_TRACKED_FILES:
            forbidden.append(tracked)
            continue
        if any(tracked.startswith(prefix) for prefix in _RELEASE_FORBIDDEN_TRACKED_PREFIXES):
            forbidden.append(tracked)
            continue
        if any(term in folded_tracked for term in forbidden_path_terms):
            forbidden.append(tracked)
    return sorted(set(forbidden))


def _release_manifest_payload(project: Path, tracked_files: list[str], tracked_error: str) -> dict:
    contract = load_release_manifest_contract(project)
    payload = {
        "ok": False,
        "source": contract["source"],
        "path": _RELEASE_MANIFEST_FILE,
        "package": contract["package"],
        "allow": contract["allow"],
        "deny": contract["deny"],
        "required": contract["required"],
        "uncoveredTrackedFiles": [],
        "deniedTrackedFiles": [],
        "missingRequiredFiles": [],
        "untrackedRequiredFiles": [],
        "issues": list(contract["issues"]),
    }

    if not contract["present"]:
        payload["issues"].append(f"{_RELEASE_MANIFEST_FILE} is missing")
        return payload

    missing_required = [
        relative for relative in contract["required"]
        if not (project / relative).is_file()
    ]
    payload["missingRequiredFiles"] = missing_required
    payload["issues"].extend(f"required file is missing: {relative}" for relative in missing_required)

    if tracked_error:
        payload["issues"].append(f"git tracked files unavailable: {tracked_error}")
    else:
        tracked_set = set(tracked_files)
        denied = sorted({
            tracked for tracked in tracked_files
            if any(release_manifest_path_matches(tracked, entry["pattern"]) for entry in contract["deny"])
        })
        uncovered = sorted({
            tracked for tracked in tracked_files
            if not any(release_manifest_path_matches(tracked, entry["pattern"]) for entry in contract["allow"])
        })
        untracked_required = sorted({
            relative for relative in contract["required"]
            if relative not in tracked_set
        })
        payload["deniedTrackedFiles"] = denied
        payload["uncoveredTrackedFiles"] = uncovered
        payload["untrackedRequiredFiles"] = untracked_required
        payload["issues"].extend(f"tracked file matches deny pattern: {relative}" for relative in denied[:20])
        payload["issues"].extend(f"tracked file is not covered by allow patterns: {relative}" for relative in uncovered[:20])
        payload["issues"].extend(f"required file is not tracked: {relative}" for relative in untracked_required[:20])

    payload["ok"] = not payload["issues"]
    return payload


def _release_public_hygiene_needles() -> tuple[tuple[str, str], ...]:
    return tuple(
        (label, "".join(parts).casefold())
        for label, parts in _RELEASE_PUBLIC_HYGIENE_TERM_PARTS
    )


def _release_private_path_tokens() -> tuple[str, ...]:
    return "J:" + chr(92), "J:" + "/"


def _release_read_public_hygiene_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        if b"\x00" in raw:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _release_public_hygiene_findings(project: Path, tracked_files: list[str],
                                     manifest_payload: dict | None = None) -> list[str]:
    findings: list[str] = []
    needles = _release_public_hygiene_needles()
    private_path_tokens = _release_private_path_tokens()
    scan_files = tracked_files
    if manifest_payload is not None:
        allow_entries = manifest_payload.get("allow", [])
        deny_entries = manifest_payload.get("deny", [])
        scan_files = [tracked for tracked in tracked_files
                      if any(release_manifest_path_matches(tracked, entry["pattern"]) for entry in allow_entries)
                      and not any(release_manifest_path_matches(tracked, entry["pattern"]) for entry in deny_entries)]
    for tracked in scan_files:
        normalized = tracked.replace("\\", "/")
        folded_path = normalized.casefold()
        for label, needle in needles:
            if needle in folded_path:
                findings.append(f"{normalized}: {label} in path")
                break

        content = _release_read_public_hygiene_text(project / normalized)
        if content is None:
            continue
        folded_content = content.casefold()
        for label, needle in needles:
            if needle in folded_content:
                findings.append(f"{normalized}: {label}")
                break
        for token in private_path_tokens:
            if token in content:
                findings.append(f"{normalized}: forbidden private path token {token}")
    return sorted(set(findings))


_DOCTOR_STATUS_RANK = {
    "ok": 0,
    "info": 1,
    "warn": 2,
    "fail": 3,
}


def _doctor_select_check(checks: list[dict], name: str) -> dict | None:
    matches = [check for check in checks if check.get("name") == name]
    if not matches:
        return None
    return max(
        enumerate(matches),
        key=lambda item: (_DOCTOR_STATUS_RANK.get(str(item[1].get("status", "info")), 1), item[0]),
    )[1]


def _doctor_check_status(checks: list[dict], name: str) -> str:
    entry = _doctor_select_check(checks, name)
    if entry is None:
        return "missing"
    return str(entry.get("status", "info"))


def _doctor_check_detail(checks: list[dict], name: str) -> str:
    entry = _doctor_select_check(checks, name)
    if entry is None:
        return "not checked"
    return str(entry.get("detail", ""))


def _doctor_issue_reasons(checks: list[dict], include_warn: bool = True) -> list[str]:
    statuses = {"fail", "warn"} if include_warn else {"fail"}
    reasons = []
    for check in checks:
        status = str(check.get("status", "info"))
        if status not in statuses:
            continue
        detail = str(check.get("detail", "")).strip()
        name = str(check.get("name", "unknown"))
        reasons.append(f"{name}: {detail}" if detail else name)
    return reasons


def _doctor_effective_control_level(entry: dict | None, expected_level: str = "unavailable") -> str:
    if entry is None:
        return "unavailable"
    status = str(entry.get("status", "info"))
    detail = str(entry.get("detail", "")).lower()
    if status == "fail":
        return "unavailable"
    if status == "warn":
        return "advisory"
    if status == "info":
        if any(term in detail for term in ("not configured", "not wired", "missing", "disabled", "no true", "unavailable")):
            return "unavailable"
        return "advisory"
    if "mechanical" in detail:
        return "mechanical"
    if "conditional" in detail:
        return "conditional"
    if "advisory" in detail:
        return "advisory"
    return expected_level


def _doctor_gate_state(checks: list[dict],
                       check_name: str,
                       expected_level: str = "unavailable") -> dict:
    entry = _doctor_select_check(checks, check_name)
    return {
        "status": str(entry.get("status", "missing")) if entry else "missing",
        "controlLevel": _doctor_effective_control_level(entry, expected_level),
        "detail": str(entry.get("detail", "not checked")) if entry else "not checked",
    }


def _doctor_manifest_executable_count(invariant_manifest: dict | None) -> int:
    if not isinstance(invariant_manifest, dict):
        return 0
    summary = invariant_manifest.get("summary", {})
    if not isinstance(summary, dict):
        return 0
    try:
        return int(summary.get("executable", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _doctor_claim_gate_state(claim_integrity: dict | None) -> dict:
    if not isinstance(claim_integrity, dict):
        return {
            "status": "missing",
            "controlLevel": "unavailable",
            "detail": "not checked",
        }
    finding_count = len([
        finding for finding in claim_integrity.get("findings", [])
        if finding.get("severity") == "fail"
    ])
    ok_state = bool(claim_integrity.get("ok"))
    return {
        "status": "ok" if ok_state else "fail",
        "controlLevel": "conditional" if ok_state else "unavailable",
        "detail": (
            f"registrySource={claim_integrity.get('registrySource')}"
            if ok_state
            else f"{finding_count} blocking finding(s)"
        ),
    }


def _build_installed_operational_contract(*,
                                          issues: int,
                                          checks: list[dict],
                                          primary_host: str | None,
                                          enabled_hosts: list[str],
                                          host_profile: dict | None,
                                          gate_contract: list[dict],
                                          context_sync: dict | None,
                                          constitution_drift: dict | None,
                                          claim_integrity: dict | None,
                                          verification_contract: dict,
                                          invariant_manifest: dict,
                                          promotion_path: dict) -> dict:
    contract_by_id = {
        str(entry.get("id")): entry
        for entry in gate_contract
        if isinstance(entry, dict) and entry.get("id")
    }
    def _expected(gate_id: str) -> str:
        entry = contract_by_id.get(gate_id, {})
        return str(entry.get("nature", "unavailable"))

    inline_gate = _doctor_gate_state(checks, "inline_gate", _expected("inline_gate"))
    repo_boundary_gate = _doctor_gate_state(checks, "repo_boundary_gate", _expected("repo_boundary_gate"))
    review_gate = _doctor_gate_state(checks, "review_gate", _expected("review_gate"))
    verification_gate = _doctor_gate_state(checks, "verification_gate", _expected("verification_gate"))
    verification_contract_gate = _doctor_gate_state(checks, "verification_contract", "conditional")
    invariant_gate = _doctor_gate_state(checks, "invariant_manifest", "conditional")
    promotion_gate = _doctor_gate_state(checks, "promotion_path", "conditional")
    claim_gate = _doctor_claim_gate_state(claim_integrity)
    constitution_gate = _doctor_gate_state(checks, "constitution_drift", "conditional")

    context_gate = {
        "status": _doctor_check_status(checks, "context_sync"),
        "controlLevel": "mechanical" if context_sync and context_sync.get("sourceState") == "canonical" else "advisory",
        "detail": _doctor_check_detail(checks, "context_sync"),
    }
    if context_gate["status"] == "missing":
        context_gate["controlLevel"] = "unavailable"

    installed_ready = issues == 0
    verification_ok = bool(verification_contract.get("ok"))
    invariant_executable = _doctor_manifest_executable_count(invariant_manifest)
    invariant_ok = bool(invariant_manifest.get("ok")) and invariant_executable > 0
    claim_ok = bool(claim_integrity and claim_integrity.get("ok"))
    has_host_profile = isinstance(host_profile, dict) and bool(primary_host)

    boundary_gate_name = "inlineGate"
    boundary_ready = inline_gate["status"] == "ok" and inline_gate["controlLevel"] == "mechanical"
    if host_profile and not _host_supports_inline_boundary(host_profile):
        boundary_gate_name = "repoBoundaryGate"
        boundary_ready = repo_boundary_gate["status"] == "ok" and repo_boundary_gate["controlLevel"] == "mechanical"

    ai_assisted_reasons = []
    if not installed_ready:
        ai_assisted_reasons.append("installed-project doctor has warnings or failures")
    if not has_host_profile:
        ai_assisted_reasons.append("no primary host profile is configured")
    if not claim_ok:
        ai_assisted_reasons.append("claim integrity is missing or failing")
    if constitution_gate["status"] in {"warn", "fail"}:
        ai_assisted_reasons.append("canonical project constitution has drift findings")
    if not verification_ok:
        ai_assisted_reasons.append("verification contract is missing or invalid")
    if not invariant_ok:
        ai_assisted_reasons.append("no executable active invariant manifest is configured")

    autonomous_reasons = []
    if not boundary_ready:
        autonomous_reasons.append(f"{boundary_gate_name} is not mechanically ready")
    review_autonomous_ready = review_gate["status"] == "ok" and review_gate["controlLevel"] == "mechanical"
    if not review_autonomous_ready:
        autonomous_reasons.append("reviewGate is not mechanical or explicitly bounded")
    verification_autonomous_ready = (
        verification_gate["status"] == "ok"
        and verification_gate["controlLevel"] in {"mechanical", "conditional"}
    )
    if not verification_autonomous_ready:
        autonomous_reasons.append("verificationGate is not mechanical or explicitly bounded")

    safe_for_human = installed_ready
    safe_for_ai = installed_ready and has_host_profile and claim_ok and verification_ok and invariant_ok
    safe_for_autonomous = safe_for_ai and boundary_ready and review_autonomous_ready and verification_autonomous_ready

    residual_risks = []
    if review_gate["status"] == "ok" and review_gate["controlLevel"] == "conditional":
        residual_risks.append("reviewGate is wired, but finding quality depends on the configured review path or backend")
    if verification_gate["status"] == "ok" and verification_gate["controlLevel"] == "conditional":
        residual_risks.append("verificationGate has a mechanical baseline, but project-specific invariant strength depends on configured suites")
    if safe_for_ai and not safe_for_autonomous:
        residual_risks.extend(autonomous_reasons)

    return {
        "schemaVersion": 1,
        "mode": "installed_project",
        "installedProjectReady": installed_ready,
        "releaseReady": None,
        "safeForHumanWork": safe_for_human,
        "safeForAiAssistedWork": safe_for_ai,
        "safeForAutonomousWork": safe_for_autonomous,
        "primaryHost": primary_host,
        "enabledHosts": enabled_hosts,
        "hostControlLevel": {
            "userHost": primary_host,
            "capabilityClass": host_profile.get("capabilityClass") if isinstance(host_profile, dict) else None,
            "protectionModel": host_profile.get("protectionModel") if isinstance(host_profile, dict) else None,
            "boundaryGate": boundary_gate_name,
        },
        "gates": {
            "claimIntegrity": claim_gate,
            "contextSync": context_gate,
            "constitutionDrift": constitution_gate,
            "inlineGate": inline_gate,
            "repoBoundaryGate": repo_boundary_gate,
            "reviewGate": review_gate,
            "verificationGate": verification_gate,
            "verificationContract": verification_contract_gate,
            "invariantGate": invariant_gate,
            "promotionGate": promotion_gate,
        },
        "blockingReasons": _doctor_issue_reasons(checks),
        "aiAssistedGaps": ai_assisted_reasons,
        "autonomousGaps": autonomous_reasons if safe_for_ai else ai_assisted_reasons + autonomous_reasons,
        "residualRisks": residual_risks,
        "promotionPathReady": bool(promotion_path.get("ok")),
    }


def _build_release_operational_contract(*,
                                        issues: int,
                                        checks: list[dict],
                                        claim_integrity: dict,
                                        verification_contract: dict,
                                        invariant_manifest: dict,
                                        release_manifest: dict,
                                        promotion_path: dict) -> dict:
    release_ready = issues == 0
    invariant_executable = _doctor_manifest_executable_count(invariant_manifest)
    return {
        "schemaVersion": 1,
        "mode": "source_release",
        "installedProjectReady": False,
        "releaseReady": release_ready,
        "safeForHumanWork": release_ready,
        "safeForAiAssistedWork": False,
        "safeForAutonomousWork": False,
        "primaryHost": None,
        "enabledHosts": [],
        "hostControlLevel": {
            "userHost": None,
            "capabilityClass": None,
            "protectionModel": "not_assessed_by_release_profile",
            "boundaryGate": None,
        },
        "gates": {
            "claimIntegrity": _doctor_claim_gate_state(claim_integrity),
            "verificationContract": _doctor_gate_state(checks, "verification_contract", "conditional"),
            "invariantGate": _doctor_gate_state(checks, "invariant_manifest", "conditional"),
            "promotionGate": _doctor_gate_state(checks, "promotion_path", "conditional"),
            "architectureIndex": _doctor_gate_state(checks, "architecture_index", "mechanical"),
            "releaseManifest": _doctor_gate_state(checks, "release_manifest", "mechanical"),
            "releaseTrackedLocalArtifacts": _doctor_gate_state(checks, "release_tracked_local_artifacts", "mechanical"),
            "releasePublicHygiene": _doctor_gate_state(checks, "release_public_hygiene", "mechanical"),
        },
        "blockingReasons": _doctor_issue_reasons(checks, include_warn=False),
        "aiAssistedGaps": [
            "release profile does not certify installed-project host gates",
            "run normal doctor in the adopter or development project before AI-assisted work",
        ],
        "autonomousGaps": [
            "release profile does not certify installed-project host gates",
            "autonomous work requires normal doctor with boundary, review, verification, and invariant gates ready",
        ],
        "residualRisks": [
            "source-release doctor checks publishability, not copied hook runtime inside an adopter project",
        ],
        "promotionPathReady": bool(promotion_path.get("ok")),
        "releaseManifestReady": bool(release_manifest.get("ok")),
        "executableInvariantCount": invariant_executable,
    }


def _release_doctor_payload(project: Path) -> dict:
    checks: list[dict] = []
    issues = 0

    def _add(name: str, status: str, detail: str = "") -> None:
        nonlocal issues
        checks.append({"name": name, "status": status, "detail": detail})
        if status == "fail":
            issues += 1

    missing_dirs = [relative for relative in _RELEASE_REQUIRED_DIRS if not (project / relative).is_dir()]
    missing_files = [relative for relative in _RELEASE_REQUIRED_FILES if not (project / relative).is_file()]
    if missing_dirs:
        _add("release_required_dirs", "fail", ", ".join(missing_dirs))
    else:
        _add("release_required_dirs", "ok", f"{len(_RELEASE_REQUIRED_DIRS)} required dir(s)")
    if missing_files:
        _add("release_required_files", "fail", ", ".join(missing_files))
    else:
        _add("release_required_files", "ok", f"{len(_RELEASE_REQUIRED_FILES)} required file(s)")

    claim_integrity = _truth_check_payload(project, include_docs=True)
    if claim_integrity.get("ok"):
        _add("claim_integrity", "ok", f"registrySource={claim_integrity.get('registrySource')}")
    else:
        fail_count = len([
            finding for finding in claim_integrity.get("findings", [])
            if finding.get("severity") == "fail"
        ])
        _add("claim_integrity", "fail", f"{fail_count} blocking finding(s)")

    verification_contract = _verification_status_payload(project)
    if verification_contract.get("ok"):
        suite_count = len(verification_contract.get("suites", []))
        _add("verification_contract", "ok", f"{suite_count} suite(s); source={verification_contract.get('source')}")
    else:
        detail = "; ".join(verification_contract.get("issues", [])) or "invalid"
        _add("verification_contract", "fail", detail)

    invariant_manifest = _invariant_status_payload(project)
    invariant_doctor = _invariant_doctor_payload(project)
    if invariant_manifest.get("ok"):
        summary = invariant_manifest.get("summary", {})
        executable_count = int(summary.get("executable", 0) or 0)
        if executable_count:
            _add(
                "invariant_manifest",
                "ok",
                f"{invariant_doctor.get('state')}; {summary.get('total', 0)} invariant(s); "
                f"executable={executable_count}; {invariant_doctor.get('controlLevel')}",
            )
        else:
            _add("invariant_manifest", "fail", "no executable active invariants")
    else:
        detail = "; ".join(invariant_manifest.get("issues", [])) or "invalid"
        _add("invariant_manifest", "fail", detail)

    architecture_index = _architecture_index_payload(project)
    architecture_index_path = architecture_index.get("path", "dev/ARCHITECTURE_INDEX.md")
    architecture_index_result = "0 missing file(s)" if architecture_index.get("ok") else f"{architecture_index.get('missingCount', 0)} missing file(s)" if architecture_index.get("available") else "; ".join(architecture_index.get("issues", [])) or "unavailable"
    architecture_index_detail = f"{architecture_index_path}; {architecture_index_result}"
    _add("architecture_index", "ok" if architecture_index.get("ok") else "fail", architecture_index_detail)

    promotion_path = _promotion_status_payload(project)
    if promotion_path.get("ok"):
        _add("promotion_path", "ok" if promotion_path.get("count") else "info", f"{promotion_path.get('count', 0)} local manifest(s)")
    else:
        detail = "; ".join(promotion_path.get("issues", [])) or "invalid"
        _add("promotion_path", "fail", detail)

    tracked_files, tracked_error = _release_git_tracked_files(project)
    release_manifest = _release_manifest_payload(project, tracked_files, tracked_error)
    if release_manifest.get("ok"):
        _add("release_manifest", "ok", f"{len(release_manifest.get('allow', []))} allow pattern(s)")
    else:
        detail = "; ".join(release_manifest.get("issues", [])[:5]) or "invalid"
        _add("release_manifest", "fail", detail)

    forbidden_tracked = _release_forbidden_tracked_files(tracked_files)
    if forbidden_tracked:
        _add("release_tracked_local_artifacts", "fail", ", ".join(forbidden_tracked[:10]))
    elif tracked_error:
        checks.append({"name": "release_tracked_local_artifacts", "status": "info", "detail": tracked_error})
    else:
        _add("release_tracked_local_artifacts", "ok", "no local-only artifacts tracked")

    public_hygiene_findings = _release_public_hygiene_findings(
        project, tracked_files, release_manifest) if tracked_files else []
    if public_hygiene_findings:
        _add("release_public_hygiene", "fail", ", ".join(public_hygiene_findings[:10]))
    elif tracked_error:
        checks.append({"name": "release_public_hygiene", "status": "info", "detail": tracked_error})
    else:
        _add("release_public_hygiene", "ok", "no forbidden hygiene tokens in shipped files")

    operational_contract = _build_release_operational_contract(
        issues=issues,
        checks=checks,
        claim_integrity=claim_integrity,
        verification_contract=verification_contract,
        invariant_manifest=invariant_manifest,
        release_manifest=release_manifest,
        promotion_path=promotion_path,
    )

    return {
        "project": str(project),
        "doctorMode": "release",
        "issues": issues,
        "healthy": issues == 0,
        "operationalContract": operational_contract,
        "claimIntegrity": claim_integrity,
        "verificationContract": verification_contract,
        "invariantManifest": invariant_manifest,
        "invariantDoctor": invariant_doctor,
        "architectureIndex": architecture_index,
        "releaseManifest": release_manifest,
        "promotionPath": promotion_path,
        "checks": checks,
    }


def cmd_doctor_release(project: Path, json_output: bool = False) -> int:
    payload = _release_doctor_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["healthy"] else 1

    print(f"\nControlCoding Release Doctor - {project}\n")
    for check in payload["checks"]:
        status = check["status"]
        message = f"{check['name']}: {check['detail']}"
        if status == "ok":
            ok(message)
        elif status == "fail":
            fail(message)
        else:
            info(message)
    contract = payload["operationalContract"]
    info(
        "Operational contract: "
        f"release={'yes' if contract['releaseReady'] else 'no'}, "
        f"human={'yes' if contract['safeForHumanWork'] else 'no'}, "
        f"AI-assisted={'yes' if contract['safeForAiAssistedWork'] else 'no'}, "
        f"autonomous={'yes' if contract['safeForAutonomousWork'] else 'no'}"
    )
    print()
    if payload["healthy"]:
        print(f"{green('All release checks passed!')} Source repo is publishable.")
    else:
        issue_count = payload["issues"]
        print(f"{red(f'{issue_count} release issue(s) found.')} Fix before publishing.")
    return 0 if payload["healthy"] else 1


def cmd_release_doctor(project: Path, json_output: bool = False) -> int:
    """Explicit alias for source-release publishability checks."""
    return cmd_doctor_release(project, json_output=json_output)


def cmd_doctor(project: Path,
               json_output: bool = False,
               strict_claims: bool = False,
               strict_verification: bool = False,
               strict_invariants: bool = False,
               release_mode: bool = False):
    """Check project health.

    When json_output=True, prints a machine-readable JSON report
    instead of human-readable text (for UI integration).
    """
    if release_mode:
        return cmd_doctor_release(project, json_output=json_output)

    checks = []  # Collect all check results for JSON mode

    def _add_check(name, status, detail=""):
        checks.append({"name": name, "status": status, "detail": detail})

    # In JSON mode, suppress all human-readable output
    _ok = ok if not json_output else (lambda m: None)
    _warn = warn if not json_output else (lambda m: None)
    _fail = fail if not json_output else (lambda m: None)
    _info = info if not json_output else (lambda m: None)

    if not json_output:
        print(f"\nControlCoding Doctor - {project}\n")
    issues = 0

    # 1. Canonical/legacy context source
    context_source, content = _read_context_source(project)
    if context_source is not None:
        context_label = context_source.name
        lines = [line for line in content.splitlines() if line.strip()]
        if len(lines) >= 50:
            _ok(f"{context_label} exists ({len(lines)} non-empty lines)")
        else:
            _warn(f"{context_label} exists but only {len(lines)} non-empty lines (aim for 50+)")
            issues += 1

        # Check for key sections
        text = content.upper()
        for section in ["BOUNDAR", "INVARIANT", "ZONE", "STABLE"]:
            if section in text:
                _ok(f"{context_label} mentions '{section.lower()}'")
            else:
                _warn(f"{context_label} missing '{section.lower()}' section")
                issues += 1

        if context_label == CANONICAL_CONTEXT_FILENAME:
            if _legacy_context_path(project).exists():
                _info("Legacy CLAUDE.md compatibility file exists")
            else:
                _info("No legacy CLAUDE.md compatibility file present (fine unless Claude Code compatibility is needed)")
        else:
            _info(
                "Legacy CLAUDE.md is acting as the current context source. "
                f"Migrate to {CANONICAL_CONTEXT_FILENAME} when convenient."
            )
    else:
        _fail(f"No context source found (expected {CANONICAL_CONTEXT_FILENAME} or {LEGACY_CONTEXT_FILENAME})")
        issues += 1

    # 2. STATUS.md
    if (project / "STATUS.md").exists():
        _ok("STATUS.md exists")
    else:
        _warn("STATUS.md not found (recommended for session continuity)")
        issues += 1

    # 3. devlog/
    if (project / "devlog").is_dir():
        _ok("devlog/ directory exists")
    else:
        _warn("devlog/ not found (recommended for session history)")
        issues += 1

    # 4. Hooks
    hooks_location = _get_hooks_location(project)
    if hooks_location == "central":
        hooks_dir = _central_hooks_dir()
        if hooks_dir.is_dir():
            hook_files = list(hooks_dir.glob("*.py"))
            _ok(f"Central hooks at {hooks_dir} ({len(hook_files)} scripts)")
            for essential in ["check_boundaries.py", "check_dangerous_commands.py"]:
                if (hooks_dir / essential).exists():
                    _ok(f"central hooks/{essential} present")
                else:
                    _fail(f"central hooks/{essential} missing")
                    issues += 1
        else:
            _fail(f"Central hooks directory not found: {hooks_dir}")
            _info("Fix: run 'cc init --central-hooks --project-root .'")
            issues += 1
    else:
        hooks_dir = project / "hooks"
        if hooks_dir.is_dir():
            hook_files = list(hooks_dir.glob("*.py"))
            if len(hook_files) >= 2:
                _ok(f"hooks/ directory has {len(hook_files)} Python scripts")
            else:
                _warn(f"hooks/ has only {len(hook_files)} scripts (expected 2+)")
                issues += 1

            # Check essential hooks
            for essential in ["check_boundaries.py", "check_dangerous_commands.py"]:
                if (hooks_dir / essential).exists():
                    _ok(f"hooks/{essential} present")
                else:
                    _fail(f"hooks/{essential} missing (core guardrail)")
                    issues += 1
        else:
            _fail("hooks/ directory not found")
            issues += 1

    # 5. .controlcoding/settings.json
    settings_path = _control_plane_read_path(project, "settings.json")
    settings_label = _control_plane_display_path("settings.json") if settings_path == _control_plane_path(project, "settings.json") else str(Path(LEGACY_CONTROL_PLANE_DIRNAME) / "settings.json").replace("\\", "/")
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            _ok(f"{settings_label} is valid JSON")

            if "hooks" in settings:
                _ok("Hooks configured in settings.json")
                # Count hook entries
                hook_count = sum(
                    len(event_hooks)
                    for event_hooks in settings["hooks"].values()
                )
                _info(f"  {hook_count} hook entries across {len(settings['hooks'])} events")
            else:
                _fail("No hooks in settings.json (hooks won't run)")
                issues += 1

            if "mcpServers" in settings:
                servers = list(settings["mcpServers"].keys())
                _ok(f"MCP servers configured: {', '.join(servers)}")
            else:
                _info("No MCP servers configured (optional)")

        except json.JSONDecodeError as e:
            _fail(f"{settings_label} is invalid JSON: {e}")
            issues += 1
    else:
        _fail(f"{_control_plane_display_path('settings.json')} not found (hooks won't activate)")
        issues += 1

    # 6. Tools directory (optional)
    tools_dir = project / "tools"
    if tools_dir.is_dir():
        tool_files = list(tools_dir.glob("*.py"))
        _ok(f"tools/ directory has {len(tool_files)} scripts")
    else:
        _info("tools/ not found (install packs with: cc install <pack>)")

    # 7. Fitness check tool
    fitness_path = _first_existing_path(
        project / "tools" / "fitness_check.py",
        project / "scripts" / "fitness_check.py",
    )
    if fitness_path is not None:
        _ok(f"{fitness_path.relative_to(project).as_posix()} present")
    else:
        _info("tools/fitness_check.py not found (install with: cc init)")

    # 8. Repo-side hook baseline
    git_available = shutil.which("git") is not None
    git_repo_present = (project / ".git").is_dir()
    precommit_path = project / ".git" / "hooks" / "pre-commit"
    precommit_installed = precommit_path.exists()
    postcommit_path = project / ".git" / "hooks" / "post-commit"
    postcommit_installed = postcommit_path.exists()
    repo_boundary_script_path = hooks_dir / "check_repo_boundaries.py"
    review_script_path = hooks_dir / "codewarden_review.py"
    precommit_has_repo_boundary = _hook_file_contains(
        precommit_path,
        ("check_repo_boundaries.py",),
    )
    precommit_has_fitness = _hook_file_contains(
        precommit_path,
        ("fitness_check.py",),
    )
    postcommit_has_codewarden = _hook_file_contains(
        postcommit_path,
        ("codewarden_postcommit", "codewarden_review.py"),
    )
    if precommit_installed:
        _ok("Git pre-commit hook installed")
    else:
        _info("No git pre-commit hook (installed by: cc init)")
    if postcommit_installed:
        _ok("Git post-commit hook installed")
    else:
        _info("No git post-commit hook (installed by: cc init for CodeWarden review)")

    # 9. Gitignore CC block
    gitignore = project / ".gitignore"
    if gitignore.exists():
        gi_content = gitignore.read_text(encoding="utf-8")
        if GITIGNORE_MARKER_START in gi_content:
            _ok(".gitignore has ControlCoding block")
        else:
            _warn(".gitignore missing ControlCoding block (CC artifacts may leak into git)")
            _info("Fix: run 'cc init --project-root .' to add the block")
            issues += 1
    else:
        _warn(".gitignore not found (CC artifacts will be tracked by git)")
        _info("Fix: run 'cc init --project-root .' to create it")
        issues += 1

    # 10. Leaked CC files in git
    import subprocess
    try:
        result = subprocess.run(
            ["git", "ls-files"] + CC_HOOK_FILES,
            cwd=str(project), capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            tracked = [f for f in result.stdout.strip().splitlines() if f]
            if tracked:
                _warn(f"CC hook scripts tracked by git: {', '.join(tracked)}")
                _info("Fix: git rm --cached hooks/ && verify .gitignore")
                issues += 1
            else:
                _ok("No CC hook scripts tracked by git")
    except Exception:
        _info("Could not check git-tracked CC files (not a git repo?)")

    # 11. Domain invariants (look for test files mentioning invariant)
    test_dirs = [project / "tests", project / "test"]
    found_tests = False
    for td in test_dirs:
        if td.is_dir():
            test_files = list(td.rglob("*.py"))
            if test_files:
                found_tests = True
                _ok(f"Test directory found: {td.name}/ ({len(test_files)} files)")
    if not found_tests:
        _info("No tests/ directory found (add domain invariants for L3)")

    # Shared cc_config.json check
    cc_config = {}
    cc_config_path = _control_plane_read_path(project, "cc_config.json")
    cc_config_label = _control_plane_display_path("cc_config.json") if cc_config_path == _control_plane_path(project, "cc_config.json") else str(Path(LEGACY_CONTROL_PLANE_DIRNAME) / "cc_config.json").replace("\\", "/")
    if cc_config_path.exists():
        try:
            cc_config = json.loads(cc_config_path.read_text(encoding="utf-8"))
            cc_issues = _collect_cc_config_issues(cc_config)
            documentation_mode = str(cc_config.get("documentation_mode", "managed"))
            cc_artifact_mode = str(cc_config.get("cc_artifact_mode", "local_only"))
            hooks_location = str(cc_config.get("hooks_location", "local"))
            zone_count = len(_normalize_protected_zones(cc_config.get("protected_zones", [])))
            controlled_write = _normalize_controlled_write_path(cc_config.get("controlled_write_path"))
            planning = cc_config.get("planning", {})
            planning_detail = ""
            if isinstance(planning, dict) and planning:
                planning_detail = (
                    f"; planning={planning.get('tier', 'core')}/"
                    f"{planning.get('planning_mode', 'solo_structured')}/"
                    f"{planning.get('planning_authority', 'single_author')}/"
                    f"manual={planning.get('manual_consultation_allowed', False)}"
                )
            controlled_write_detail = (
                f"; write_path={controlled_write['mode']}"
                if controlled_write.get("enabled")
                else "; write_path=off"
            )
            if cc_issues:
                for issue in cc_issues:
                    _fail(issue)
                _add_check("cc_config", "fail", "; ".join(cc_issues))
                issues += len(cc_issues)
            else:
                _ok(
                    f"{cc_config_label} is valid "
                    f"(documentation_mode={documentation_mode}, cc_artifact_mode={cc_artifact_mode}, hooks_location={hooks_location}, zones={zone_count}{planning_detail}{controlled_write_detail})"
                )
                _add_check(
                    "cc_config",
                    "ok",
                    f"documentation_mode={documentation_mode}; cc_artifact_mode={cc_artifact_mode}; hooks_location={hooks_location}; zones={zone_count}{planning_detail}{controlled_write_detail}",
                )
                if documentation_mode == "project_managed":
                    _info(
                        "Documentation governance is project-managed; file-organization enforcement is advisory/off."
                    )
                try:
                    local_doc_paths = ["devlog/"]
                    if cc_artifact_mode == "local_only":
                        _info(
                            "CC working documents are configured as local-only; update them locally but keep them out of git."
                        )
                        local_doc_paths.extend(["STATUS.md", "ROADMAP.md", "BUGS.md"])
                        if documentation_mode == "managed":
                            local_doc_paths.append("dev/")
                    else:
                        _info(
                            "CC governed docs may be shared in git, but devlog remains local-only session memory."
                        )
                    result = subprocess.run(
                        ["git", "ls-files", "--"] + local_doc_paths,
                        cwd=str(project), capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0:
                        leaked = [f for f in result.stdout.strip().splitlines() if f]
                        if leaked:
                            _warn(
                                "CC local-only artifacts are tracked by git: "
                                + ", ".join(leaked[:8])
                            )
                            _info(
                                "Fix: untrack devlog entries. If tracked files include STATUS/ROADMAP/BUGS/dev and you want to keep them in git, switch cc_artifact_mode to shared_repo."
                            )
                            issues += 1
                        else:
                            _ok("No CC local-only artifacts are tracked by git")
                except Exception:
                    _info("Could not check tracked CC local-only artifacts (not a git repo?)")
        except json.JSONDecodeError:
            _fail(f"{cc_config_label} is invalid JSON")
            _add_check("cc_config", "fail", "invalid JSON")
            issues += 1
    else:
        _fail(f"{_control_plane_display_path('cc_config.json')} not found")
        _add_check("cc_config", "fail", "missing")
        issues += 1

    # Gateway config compliance check
    gateway: dict = {}
    gateway_compliant = False
    gateway_backend_ids: set[str] = set()
    doctor_primary_host = None
    doctor_enabled_hosts: list[str] = []
    doctor_host_profile = None
    doctor_gate_contract: list[dict] = []
    doctor_context_sync = None
    doctor_constitution_drift = None
    doctor_claim_integrity = _truth_check_payload(project, include_docs=True)
    doctor_verification_contract = _verification_status_payload(project)
    doctor_invariant_manifest = _invariant_status_payload(project)
    doctor_invariant_doctor = _invariant_doctor_payload(project)
    doctor_promotion_path = _promotion_status_payload(project)
    doctor_feature_doctor = feature_doctor_check(project)
    doctor_feature_state = doctor_feature_doctor["payload"]

    if strict_claims:
        if doctor_claim_integrity and doctor_claim_integrity.get("ok"):
            _ok("claim integrity: capability registry matches routed CLI commands")
            _add_check(
                "claim_integrity",
                "ok",
                f"registrySource={doctor_claim_integrity.get('registrySource')}",
            )
        else:
            fail_count = len([
                finding for finding in (doctor_claim_integrity or {}).get("findings", [])
                if finding.get("severity") == "fail"
            ])
            _fail(f"claim integrity: {fail_count} blocking finding(s)")
            _add_check("claim_integrity", "fail", f"{fail_count} blocking finding(s)")
            issues += max(1, fail_count)

    if doctor_verification_contract.get("ok"):
        suite_count = len(doctor_verification_contract.get("suites", []))
        _ok(f"verification contract: {suite_count} suite(s) configured")
        _add_check(
            "verification_contract",
            "ok",
            f"{suite_count} suite(s); source={doctor_verification_contract.get('source')}",
        )
    elif strict_verification:
        verification_issues = doctor_verification_contract.get("issues", [])
        detail = "; ".join(verification_issues) if verification_issues else "invalid"
        _fail(f"verification contract: {detail}")
        _add_check("verification_contract", "fail", detail)
        issues += max(1, len(verification_issues))
    elif doctor_verification_contract.get("source") == "missing":
        _info("verification contract: not configured (run `cc verify init`)")
        _add_check("verification_contract", "info", "not configured")
    else:
        verification_issues = doctor_verification_contract.get("issues", [])
        detail = "; ".join(verification_issues) if verification_issues else "invalid"
        _warn(f"verification contract: {detail}")
        _add_check("verification_contract", "warn", detail)
        issues += 1

    if doctor_invariant_doctor.get("ok"):
        invariant_summary = doctor_invariant_manifest.get("summary", {})
        executable_count = int(invariant_summary.get("executable", 0) or 0)
        total_count = int(invariant_summary.get("total", 0) or 0)
        _ok(
            "invariant manifest: "
            f"{total_count} invariant(s), {executable_count} executable, "
            f"state={doctor_invariant_doctor.get('state')}"
        )
        _add_check(
            "invariant_manifest",
            "ok",
            f"{doctor_invariant_doctor.get('state')}; {total_count} invariant(s); "
            f"executable={executable_count}; {doctor_invariant_doctor.get('controlLevel')}",
        )
    elif strict_invariants:
        invariant_issues = doctor_invariant_doctor.get("issues", []) or doctor_invariant_manifest.get("issues", [])
        detail = "; ".join(invariant_issues) if invariant_issues else "invalid"
        _fail(f"invariant manifest: {detail}")
        _add_check("invariant_manifest", "fail", detail)
        issues += max(1, len(invariant_issues))
    elif doctor_invariant_manifest.get("source") == "missing":
        _info("invariant manifest: not configured (run `cc invariants init`)")
        _add_check("invariant_manifest", "info", "not configured")
    elif doctor_invariant_doctor.get("state") == "documented_only":
        detail = (
            f"{doctor_invariant_doctor.get('state')}; "
            f"{doctor_invariant_doctor.get('controlLevel')}; "
            + "; ".join(doctor_invariant_doctor.get("issues", []))
        )
        _warn(f"invariant manifest: {detail}")
        _add_check("invariant_manifest", "warn", detail)
        issues += 1
    else:
        invariant_issues = doctor_invariant_doctor.get("issues", []) or doctor_invariant_manifest.get("issues", [])
        detail = "; ".join(invariant_issues) if invariant_issues else "invalid"
        _warn(f"invariant manifest: {detail}")
        _add_check("invariant_manifest", "warn", detail)
        issues += 1

    if doctor_promotion_path.get("ok"):
        promotion_count = int(doctor_promotion_path.get("count", 0) or 0)
        replacement_count = int(
            doctor_promotion_path.get("replacements", {}).get("count", 0) or 0
        )
        if promotion_count:
            by_status = doctor_promotion_path.get("byStatus", {})
            status_detail = ", ".join(
                f"{status}={count}" for status, count in sorted(by_status.items())
            )
            detail = (
                f"promotions={promotion_count}; replacements={replacement_count}; "
                f"{status_detail}"
            )
            _ok(f"promotion path: {promotion_count} local manifest(s) ({detail})")
            _add_check("promotion_path", "ok", detail)
        elif replacement_count:
            detail = f"promotions=0; replacements={replacement_count}"
            _ok(f"promotion path: {detail}")
            _add_check("promotion_path", "ok", detail)
        else:
            _info("promotion path: no local promotion manifests yet")
            _add_check("promotion_path", "info", "no local promotion manifests")
    else:
        promotion_issues = doctor_promotion_path.get("issues", [])
        detail = "; ".join(promotion_issues) if promotion_issues else "invalid"
        _warn(f"promotion path: {detail}")
        _add_check("promotion_path", "warn", detail)
        issues += 1

    feature_level = doctor_feature_doctor["level"]
    feature_message = doctor_feature_doctor["message"]
    if feature_level == "warn":
        _warn(f"feature state: {feature_message}")
        issues += doctor_feature_doctor["issueCount"]
    elif feature_level == "ok":
        _ok(f"feature state: {feature_message}")
    else:
        _info(f"feature state: {feature_message}")
    _add_check("feature_state", feature_level, doctor_feature_doctor["detail"])

    gateway_path = _control_plane_read_path(project, "gateway_config.json")
    gateway_label = _control_plane_display_path("gateway_config.json") if gateway_path == _control_plane_path(project, "gateway_config.json") else str(Path(LEGACY_CONTROL_PLANE_DIRNAME) / "gateway_config.json").replace("\\", "/")
    if gateway_path.exists():
        try:
            gateway = json.loads(gateway_path.read_text(encoding="utf-8"))
            gateway_issues = _collect_gateway_config_issues(gateway)
            if gateway_issues:
                for issue in gateway_issues:
                    _fail(issue)
                _add_check("gateway_config", "fail", "; ".join(gateway_issues))
                issues += len(gateway_issues)
            else:
                gateway_compliant = True
                backends = gateway.get("backends", {})
                if isinstance(backends, dict):
                    gateway_backend_ids = {
                        str(backend_id).strip()
                        for backend_id in backends.keys()
                        if str(backend_id).strip()
                    }
                _ok(f"{gateway_label} passes compliance checks")
                _add_check("gateway_config", "ok", "compliant")

                primary_host, _enabled_hosts = _normalize_gateway_hosts(gateway)
                host_profile = _derive_host_profile(primary_host)
                doctor_primary_host = primary_host
                doctor_enabled_hosts = _enabled_hosts
                doctor_host_profile = host_profile
                doctor_gate_contract = _derive_host_gate_contract(host_profile)
                protected_zone_count = len(_normalize_protected_zones(cc_config.get("protected_zones", [])))
                non_inline_host = not _host_supports_inline_boundary(host_profile)
                stored_profile = gateway.get("hostProfile")
                if stored_profile is None:
                    _warn(
                        "gateway_config.hostProfile is missing. "
                        "Persist the canonical enforcement profile via `cc setup` or `cc host switch <host>`."
                    )
                    _add_check("host_profile", "warn", "missing")
                    issues += 1
                else:
                    _ok(
                        "Host profile: "
                        f"{host_profile['label']} -> {host_profile['capabilityClass']} "
                        f"({host_profile['protectionModel']})"
                    )
                    _add_check(
                        "host_profile",
                        "ok",
                        f"{host_profile['label']}: {host_profile['capabilityClass']}/{host_profile['protectionModel']}",
                    )

                context_file = host_profile.get("contextFile")
                if isinstance(context_file, str) and context_file:
                    context_path = project / context_file
                    if context_path.exists():
                        _ok(f"Primary host context file present: {context_file}")
                    else:
                        _warn(
                            f"Primary host context file missing: {context_file}. "
                            f"Run `cc export host-context --host {primary_host} --force`."
                        )
                        issues += 1

                    doctor_context_sync = _context_sync_payload(project, host=primary_host)
                    sync_entries = doctor_context_sync.get("hosts", [])
                    sync_entry = sync_entries[0] if sync_entries else {}
                    sync_state = sync_entry.get("state", "missing")
                    if doctor_context_sync.get("sourceState") != "canonical":
                        _info(
                            "context sync: legacy context source in use; "
                            f"create {CANONICAL_CONTEXT_FILENAME} for canonical multi-host sync"
                        )
                        _add_check(
                            "context_sync",
                            "info",
                            f"sourceState={doctor_context_sync.get('sourceState')}",
                        )
                    elif sync_state == "current":
                        _ok(f"context sync: {context_file} is current from {CANONICAL_CONTEXT_FILENAME}")
                        _add_check(
                            "context_sync",
                            "ok",
                            f"{context_file} current from {CANONICAL_CONTEXT_FILENAME}",
                        )
                    else:
                        detail = sync_entry.get("detail", sync_state)
                        _warn(
                            f"context sync: {context_file} is {sync_state}. "
                            f"Run `cc context sync --host {primary_host}`."
                        )
                        _add_check("context_sync", "warn", detail)
                        issues += 1

                for line in _format_host_coverage_lines(_host_coverage_payload(host_profile, gateway)):
                    _info(line)
                _info("Host gate contract (CC integration model; configuration is not delivery evidence):")
                for line in _format_host_gate_contract_lines(host_profile):
                    _info(f"  {line}")

                if _host_supports_inline_boundary(host_profile):
                    _info("inline gate: CC native-hook model; host delivery unverified")
                    _add_check("inline_gate", "info", "CC native-hook model; host delivery unverified")
                else:
                    _info("inline gate: not implemented by this CC integration")
                    _add_check("inline_gate", "info", "CC native inline route not implemented")

                if non_inline_host:
                    if not git_available:
                        _fail(
                            "repo boundary gate: git is not available; non-inline hosts require a real repo-side boundary path"
                        )
                        _add_check("repo_boundary_gate", "fail", "git not available")
                        issues += 1
                    elif not git_repo_present:
                        _fail(
                            "repo boundary gate: .git/ is missing; non-inline hosts cannot be healthy without a repository boundary path"
                        )
                        _add_check("repo_boundary_gate", "fail", ".git missing")
                        issues += 1
                    elif not precommit_installed:
                        _fail(
                            "repo boundary gate: .git/hooks/pre-commit is missing; this is the primary boundary path for the selected non-inline host"
                        )
                        _add_check("repo_boundary_gate", "fail", "pre-commit missing")
                        issues += 1
                    elif not repo_boundary_script_path.exists():
                        _fail(
                            "repo boundary gate: check_repo_boundaries.py is missing from the hook package"
                        )
                        _add_check("repo_boundary_gate", "fail", "check_repo_boundaries.py missing")
                        issues += 1
                    elif not precommit_has_repo_boundary:
                        _fail(
                            "repo boundary gate: pre-commit exists but does not run check_repo_boundaries.py; boundary enforcement is not mechanically wired repo-side"
                        )
                        _add_check("repo_boundary_gate", "fail", "pre-commit missing repo boundary hook")
                        issues += 1
                    else:
                        _ok("repo boundary gate: mechanical via .git/hooks/pre-commit (primary boundary path)")
                        _add_check("repo_boundary_gate", "ok", "mechanical via pre-commit")
                elif precommit_has_repo_boundary:
                    _ok("repo boundary gate: mechanical backstop via .git/hooks/pre-commit")
                    _add_check("repo_boundary_gate", "ok", "mechanical backstop via pre-commit")
                else:
                    _info("repo boundary gate: optional backstop not wired for this inline-first host")
                    _add_check("repo_boundary_gate", "info", "optional backstop not wired")

                review_required = non_inline_host and protected_zone_count > 0
                review_failure = False
                if host_profile.get("reviewGate") == "native_hooks":
                    _info("review gate: CC native-hook model; host delivery unverified")
                    _add_check("review_gate", "info", "CC native-hook model; host delivery unverified")
                else:
                    if not review_script_path.exists():
                        review_failure = True
                        review_detail = "codewarden_review.py missing"
                        review_message = "review gate: CodeWarden review script is missing from the hook package"
                    elif not postcommit_installed:
                        review_failure = True
                        review_detail = "post-commit missing"
                        review_message = (
                            "review gate: .git/hooks/post-commit is missing; non-inline hosts should wire CodeWarden review repo-side"
                        )
                    elif not postcommit_has_codewarden:
                        review_failure = True
                        review_detail = "post-commit missing CodeWarden trigger"
                        review_message = (
                            "review gate: post-commit exists but does not trigger CodeWarden review"
                        )
                    else:
                        _ok("review gate: conditional via .git/hooks/post-commit -> CodeWarden")
                        _add_check(
                            "review_gate",
                            "ok",
                            "conditional via post-commit; findings depend on configured review path/backend",
                        )
                        review_failure = False
                    if review_failure:
                        if review_required:
                            _fail(review_message)
                            _add_check("review_gate", "fail", review_detail)
                            issues += 1
                        else:
                            _warn(review_message)
                            _add_check("review_gate", "warn", review_detail)

                verification_failure = False
                if non_inline_host:
                    if fitness_path is None:
                        verification_failure = True
                        verification_detail = "fitness_check.py missing"
                        verification_message = (
                            "verification gate: fitness_check.py is missing; non-inline hosts need a mechanical verification baseline in commit or CI"
                        )
                    elif not precommit_has_fitness:
                        verification_failure = True
                        verification_detail = "pre-commit missing fitness_check.py"
                        verification_message = (
                            "verification gate: pre-commit does not run fitness_check.py; verification is not mechanically wired for this non-inline host"
                        )
                    else:
                        _ok(
                            "verification gate: conditional baseline wired via fitness_check.py in pre-commit"
                        )
                        _add_check(
                            "verification_gate",
                            "ok",
                            "fitness baseline is mechanical; project-specific invariant commands remain project-managed",
                        )
                    if verification_failure:
                        _fail(verification_message)
                        _add_check("verification_gate", "fail", verification_detail)
                        issues += 1
                    elif found_tests:
                        _info(
                            "Project tests exist. Project-specific invariant commands still need explicit hook/CI wiring if you want them to become mechanical."
                        )
                elif fitness_path is not None and precommit_has_fitness:
                    _ok("verification gate: repo-side fitness backstop is wired")
                    _add_check("verification_gate", "ok", "fitness backstop wired")
                else:
                    _info(
                        "verification gate: advisory unless fitness/invariant commands are wired into commit or CI"
                    )
                    _add_check("verification_gate", "info", "advisory unless wired")

        except json.JSONDecodeError:
            _fail(f"{gateway_label} is invalid JSON")
            _add_check("gateway_config", "fail", "invalid JSON")
            issues += 1
    else:
        _info("No gateway config (optional until UI/gateway features are enabled)")
        _add_check("gateway_config", "info", "not configured")

    drift_host = doctor_primary_host or ""
    doctor_constitution_drift = _constitution_drift_payload(project, host=drift_host)
    drift_source_state = doctor_constitution_drift.get("sourceState")
    if drift_source_state != "canonical":
        _info(
            "constitution drift: not assessed because "
            f"{CANONICAL_CONTEXT_FILENAME} is not the active context source"
        )
        _add_check("constitution_drift", "info", f"sourceState={drift_source_state}")
    elif doctor_constitution_drift.get("ok"):
        _ok("constitution drift: canonical context matches active controls")
        _add_check("constitution_drift", "ok", "canonical context current")
    else:
        drift_findings = doctor_constitution_drift.get("findings", [])
        fail_count = len([finding for finding in drift_findings if finding.get("severity") == "fail"])
        warn_count = len([finding for finding in drift_findings if finding.get("severity") == "warn"])
        detail = f"{fail_count} fail, {warn_count} warn"
        if fail_count:
            _fail(f"constitution drift: {detail}")
            _add_check("constitution_drift", "fail", detail)
            issues += max(1, fail_count)
        else:
            _warn(f"constitution drift: {detail}")
            _add_check("constitution_drift", "warn", detail)
            issues += max(1, warn_count)

    controlled_write = _load_controlled_write_path(project, cc_config)
    controlled_write_host, _controlled_write_enabled_hosts = _normalize_gateway_hosts(gateway)
    controlled_write_host_profile = _derive_host_profile(controlled_write_host)
    controlled_write_non_inline_host = not _host_supports_inline_boundary(controlled_write_host_profile)
    if controlled_write.get("enabled"):
        prereq_issue = _check_patch_gateway_prereqs(project)
        preflight_issue = (
            _check_write_path_preflight_prereqs(project)
            if controlled_write.get("preflight_fitness_for_apply")
            else ""
        )
        if prereq_issue or preflight_issue:
            detail = prereq_issue or preflight_issue
            _fail(f"controlled write path: enabled but unusable ({detail})")
            _add_check("controlled_write_path", "fail", detail)
            issues += 1
        else:
            shadow_detail = (
                "shadow-preflight"
                if controlled_write.get("preflight_fitness_for_apply")
                else "no-shadow-preflight"
            )
            _ok(
                "controlled write path: opt-in patch gateway available "
                "(protected zones are checked before apply when routed through cc write-path)"
            )
            _add_check(
                "controlled_write_path",
                "ok",
                (
                    f"{controlled_write['mode']} / {controlled_write['patch_format']} / "
                    f"{'manifest-required' if controlled_write.get('require_manifest_for_apply') else 'manifest-optional'} / "
                    f"{shadow_detail} / opt-in"
                ),
            )
            if controlled_write_non_inline_host:
                _info(
                    "This improves edit-time control for non-inline hosts, but only for writes routed through cc write-path."
                )
    else:
        _info("controlled write path: optional advanced path disabled")
        _add_check("controlled_write_path", "info", "disabled")

    # Engagement config check
    engagement_path = _control_plane_read_path(project, "cc_engagement.json")
    if engagement_path.exists():
        try:
            json.loads(engagement_path.read_text(encoding="utf-8"))
            eng = _load_engagement_runtime_config(engagement_path)
            tier = eng.get("tier")
            level = eng.get("level", "?")
            backend_policy = eng.get("backend_policy", "local_only")
            planning_mode = eng.get("planning_mode", "")
            planning_authority = eng.get("planning_authority", "")
            manual_consultation_allowed = eng.get("manual_consultation_allowed", False)
            if not json_output:
                if tier:
                    _ok(
                        f"Engagement config: tier {tier}, legacy level {level}, "
                        f"backend_policy {backend_policy}, "
                        f"planning {planning_mode}/{planning_authority}, "
                        f"manual_consultation={manual_consultation_allowed}"
                    )
                else:
                    _ok(
                        f"Engagement config: level {level}, backend_policy {backend_policy}, "
                        f"planning {planning_mode}/{planning_authority}, "
                        f"manual_consultation={manual_consultation_allowed}"
                    )
            _add_check(
                "engagement",
                "ok",
                (
                    f"tier {tier}, level {level}, backend_policy {backend_policy}, "
                    f"planning_mode {planning_mode}, planning_authority {planning_authority}, "
                    f"manual_consultation {manual_consultation_allowed}"
                    if tier else
                    f"level {level}, backend_policy {backend_policy}, "
                    f"planning_mode {planning_mode}, planning_authority {planning_authority}, "
                    f"manual_consultation {manual_consultation_allowed}"
                ),
            )
            specialist_paths = _normalize_specialist_paths(eng.get("specialist_paths", []))
            global_max_calls = 0
            budget_policy = eng.get("budget_policy", {})
            if isinstance(budget_policy, dict):
                try:
                    global_max_calls = int(budget_policy.get("max_calls", 0))
                except (TypeError, ValueError):
                    global_max_calls = 0
                if global_max_calls < 0:
                    global_max_calls = 0
            if tier in {"agents", "studio"}:
                if not specialist_paths:
                    _fail(
                        "specialist consent matrix missing: Agents/Studio must persist explicit specialist/backend paths"
                    )
                    _add_check("specialist_consent", "fail", "missing explicit specialist/backend matrix")
                    issues += 1
                else:
                    _ok(
                        "specialist consent matrix: "
                        f"{len(specialist_paths)} active path(s) recorded"
                    )
                    _add_check("specialist_consent", "ok", f"{len(specialist_paths)} path(s)")
                    missing_backend_labels = [
                        str(path.get("label") or path.get("role_id") or "specialist")
                        for path in specialist_paths
                        if not str(path.get("backend", "")).strip()
                    ]
                    routed_paths = [
                        path
                        for path in specialist_paths
                        if str(path.get("execution_mode", "")).strip() in {"cc_routed", "auto_bounded"}
                    ]
                    if missing_backend_labels:
                        _fail(
                            "specialist consent matrix has active paths without backend/API labels: "
                            + ", ".join(missing_backend_labels)
                        )
                        _add_check(
                            "specialist_consent_backend",
                            "fail",
                            "missing backend/API labels",
                        )
                        issues += 1
                    elif routed_paths and not gateway_path.exists():
                        _fail(
                            "specialist runtime paths require gateway_config: "
                            "routed/automatic specialist execution cannot rely on implicit backend wiring"
                        )
                        _add_check(
                            "specialist_execution_paths",
                            "fail",
                            "gateway_config missing for routed/automatic specialist paths",
                        )
                        issues += 1
                    elif routed_paths and not gateway_compliant:
                        _fail(
                            "specialist runtime paths require a compliant gateway_config before routed/automatic execution can be considered healthy"
                        )
                        _add_check(
                            "specialist_execution_paths",
                            "fail",
                            "gateway_config is missing or invalid for routed/automatic specialist paths",
                        )
                        issues += 1
                    else:
                        unknown_routed_backends = [
                            str(path.get("label") or path.get("role_id") or "specialist")
                            for path in routed_paths
                            if str(path.get("backend", "")).strip() not in gateway_backend_ids
                        ]
                        if unknown_routed_backends:
                            _fail(
                                "specialist runtime paths reference backends not present in gateway_config.backends: "
                                + ", ".join(unknown_routed_backends)
                            )
                            _add_check(
                                "specialist_execution_paths",
                                "fail",
                                "routed/automatic specialist paths reference unknown gateway backends",
                            )
                            issues += 1
                        else:
                            if routed_paths:
                                _add_check(
                                    "specialist_execution_paths",
                                    "ok",
                                    f"{len(routed_paths)} routed/automatic path(s) wired through gateway_config",
                                )
                                if not json_output:
                                    _ok(
                                        "specialist execution paths: routed/automatic paths are wired through gateway_config"
                                    )
                            else:
                                _add_check(
                                    "specialist_execution_paths",
                                    "ok",
                                    "all active specialist paths are human-mediated",
                                )
                                if not json_output:
                                    _ok(
                                        "specialist execution paths: all active specialist paths are human-mediated"
                                    )
                    if not missing_backend_labels and not json_output:
                        for line in _format_specialist_matrix_lines(specialist_paths, global_max_calls):
                            _info(f"  specialist path: {line}")
            elif manual_consultation_allowed and not json_output:
                _info(
                    "Core manual consultation is explicit and separate from specialist orchestration."
                )

            tandem_active = False
            try:
                if str(SCRIPTS_DIR) not in sys.path:
                    sys.path.insert(0, str(SCRIPTS_DIR))
                from control_plane_utils import is_component_active
                tandem_active = (
                    is_component_active("tandem", config=eng)
                    and _has_explicit_tandem_intent(eng, specialist_paths)
                )
            except Exception:
                tandem_active = False

            if tandem_active and backend_policy == "local_only":
                assessment = _assess_local_tandem_capacity()
                detail = _format_local_tandem_assessment(assessment)
                tandem_cfg = eng.get("tandem", {}) if isinstance(eng.get("tandem"), dict) else {}
                model_a = str(tandem_cfg.get("model_a", "")).strip()
                model_b = str(tandem_cfg.get("model_b", "")).strip()
                if model_a and model_b and model_a != model_b:
                    _ok(f"Local tandem models configured: {model_a} vs {model_b}")
                    _add_check("local_tandem_models", "ok", f"{model_a} vs {model_b}")
                else:
                    _warn(
                        "Local tandem models are not configured as two distinct Ollama models; runtime will degrade to single-model consult."
                    )
                    _add_check("local_tandem_models", "warn", "missing or non-distinct local tandem models")
                if assessment.get("status") == "disabled":
                    _warn(f"Local tandem capacity: {detail}")
                    _info(
                        "Recommendation: keep local consultation single-model or add an approved second backend before relying on tandem."
                    )
                    issues += 1
                    _add_check("local_tandem", "warn", detail)
                elif assessment.get("status") == "risky":
                    _warn(f"Local tandem capacity: {detail}")
                    _info(
                        "Recommendation: prefer sequential/quantized dual-model runs rather than keeping two heavy local models loaded."
                    )
                    _add_check("local_tandem", "warn", detail)
                else:
                    _ok(f"Local tandem capacity: {detail}")
                    _add_check("local_tandem", "ok", detail)
            elif tandem_active:
                _info(
                    "Tandem is active, but backend_policy is not local_only; local hardware gating is advisory only."
                )
                _add_check("local_tandem", "info", "tandem active on non-local-only backend policy")
            else:
                _add_check("local_tandem", "info", "tandem inactive in current tier/config")
        except json.JSONDecodeError:
            if not json_output:
                _warn(f"{_control_plane_display_path('cc_engagement.json')} is invalid JSON")
            _add_check("engagement", "warn", "invalid JSON")
            issues += 1
    else:
        if not json_output:
            _info("No engagement config (using defaults, run: cc setup --engagement)")
        _add_check("engagement", "info", "not configured")

    operational_contract = _build_installed_operational_contract(
        issues=issues,
        checks=checks,
        primary_host=doctor_primary_host,
        enabled_hosts=doctor_enabled_hosts,
        host_profile=doctor_host_profile,
        gate_contract=doctor_gate_contract,
        context_sync=doctor_context_sync,
        constitution_drift=doctor_constitution_drift,
        claim_integrity=doctor_claim_integrity,
        verification_contract=doctor_verification_contract,
        invariant_manifest=doctor_invariant_manifest,
        promotion_path=doctor_promotion_path,
    )

    if not json_output:
        _info(
            "Operational contract: "
            f"human={'yes' if operational_contract['safeForHumanWork'] else 'no'}, "
            f"AI-assisted={'yes' if operational_contract['safeForAiAssistedWork'] else 'no'}, "
            f"autonomous={'yes' if operational_contract['safeForAutonomousWork'] else 'no'}"
        )
        if operational_contract["aiAssistedGaps"]:
            _info(
                "AI-assisted gaps: "
                + "; ".join(operational_contract["aiAssistedGaps"][:3])
            )
        if operational_contract["safeForAiAssistedWork"] and operational_contract["autonomousGaps"]:
            _info(
                "Autonomous gaps: "
                + "; ".join(operational_contract["autonomousGaps"][:3])
            )

    # JSON output mode
    if json_output:
        report = {
            "project": str(project),
            "issues": issues,
            "healthy": issues == 0,
            "operationalContract": operational_contract,
            "primaryHost": doctor_primary_host,
            "enabledHosts": doctor_enabled_hosts,
            "hostProfile": doctor_host_profile,
            "hostCoverage": _host_coverage_payload(doctor_host_profile or {}, gateway),
            "gateContract": doctor_gate_contract,
            "contextSync": doctor_context_sync,
            "constitutionDrift": doctor_constitution_drift,
            "claimIntegrity": doctor_claim_integrity,
            "verificationContract": doctor_verification_contract,
            "invariantManifest": doctor_invariant_manifest,
            "invariantDoctor": doctor_invariant_doctor,
            "promotionPath": doctor_promotion_path,
            "featureState": doctor_feature_state,
            "checks": checks,
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if issues == 0 else 1

    # Summary
    print()
    if issues == 0:
        print(f"{green('All checks passed!')} Project is healthy.")
    elif issues <= 3:
        print(f"{yellow(f'{issues} issue(s) found.')} Review warnings above.")
    else:
        print(f"{red(f'{issues} issues found.')} Run 'cc init' to set up basics.")
    return 0 if issues == 0 else 1


def cmd_review(project: Path, to_stdout: bool = False):
    """Generate a peer review prompt tailored to this project."""
    # 1. Read the current context source
    context_source, claude_md_text = _read_context_source(project)
    if context_source is None:
        fail(
            f"No context source found. Run 'cc init' or 'cc setup' first "
            f"(expected {CANONICAL_CONTEXT_FILENAME} or {LEGACY_CONTEXT_FILENAME})."
        )
        return 1
    context_label = context_source.name

    # Extract project name from first heading or directory name
    project_name = project.name
    for line in claude_md_text.splitlines():
        if line.startswith("# "):
            # Use heading text, strip markdown and common suffixes
            heading = line.lstrip("# ").strip().split(" - ")[0].strip()
            if heading:
                project_name = heading
            break

    # 2. Read template (Section A)
    template_path = TEMPLATES_DIR / "review_prompt_template.md"
    if not template_path.exists():
        fail(f"Review template not found: {template_path}")
        return 1
    section_a = template_path.read_text(encoding="utf-8")

    # 3. Read cc_config.json for protected zones
    cc_config_path = _control_plane_read_path(project, "cc_config.json")
    zones_text = ""
    if cc_config_path.exists():
        try:
            config = json.loads(cc_config_path.read_text(encoding="utf-8"))
            zones = _normalize_protected_zones(config.get("protected_zones", []))
            if zones:
                lines = []
                for z in zones:
                    level = z.get("level", "warn").upper()
                    desc = z.get("description", "")
                    path = z.get("path", "")
                    entry = f"- `{path}` [{level}]"
                    if desc:
                        entry += f" - {desc}"
                    lines.append(entry)
                zones_text = "\n".join(lines)
        except (json.JSONDecodeError, KeyError):
            pass

    # 4. Scan project files (grouped by directory, max 40)
    file_list = _collect_project_files(project)

    # 5. Historical context (Section C)
    section_c = ""
    last_review = project / "docs" / "last_review_summary.md"
    if last_review.exists():
        summary = last_review.read_text(encoding="utf-8").strip()
        section_c = (
            "\n## Previous Review Context\n\n"
            "A previous review was conducted. Here is a summary of its "
            "findings and the current status of each recommendation:\n\n"
            f"{summary}\n\n"
            "In this review, please:\n"
            "1. Assess whether the previous recommendations were addressed adequately\n"
            "2. Identify any new issues that have emerged since the last review\n"
            "3. Note if any previously-identified strengths have regressed\n"
        )

    # 6. Assemble Section B
    zones_block = ""
    if zones_text:
        zones_block = f"\n### Protected Zones (from cc_config.json)\n{zones_text}\n"

    section_b = (
        f"\n## Project-Specific Context\n\n"
        f"**Project**: {project_name}\n"
        f"\n## What to Read\n\n"
        f"Read the entire repository. Start with {context_label}, then:\n"
        f"{zones_block}"
        f"\n### Project Files ({len(file_list)} files)\n"
        f"{chr(10).join(file_list)}\n\n"
        f"Read all of them before forming conclusions.\n"
    )

    # 7. Assemble full prompt
    prompt = f"{section_a}\n{section_b}{section_c}"

    # 8. Output
    if to_stdout:
        print(prompt)
    else:
        docs_dir = project / "docs"
        docs_dir.mkdir(exist_ok=True)
        output_path = docs_dir / "review_prompt.md"
        output_path.write_text(prompt, encoding="utf-8")
        ok(f"Review prompt saved to docs/review_prompt.md ({len(prompt)} chars)")

        # Try clipboard copy (optional)
        try:
            import pyperclip
            pyperclip.copy(prompt)
            ok("Copied to clipboard")
        except Exception:
            pass

        print(
            "\n  Paste the prompt into a fresh Claude chat (not this session)"
            "\n  for an independent review."
        )
    return 0


def _collect_project_files(project: Path, max_files: int = 60) -> list:
    """Collect project files for review, respecting .gitignore."""
    import subprocess

    # Try git ls-files first (respects .gitignore)
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(project), capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            files = [f for f in result.stdout.strip().splitlines() if f]
        else:
            files = _collect_project_files_fallback(project)
    except Exception:
        files = _collect_project_files_fallback(project)

    # Filter out binary and noise files
    skip_ext = {".pyc", ".pyo", ".exe", ".dll", ".so", ".o", ".class",
                ".jar", ".pdf", ".png", ".jpg", ".gif", ".ico", ".woff"}
    files = [f for f in files if Path(f).suffix not in skip_ext]

    # Sort into priority tiers
    tier_top = []       # CLAUDE.md, STATUS.md, README.md, config
    tier_code = []      # scripts, hooks, tests, templates, docs
    tier_rest = []      # everything else (devlog, benchmarks, etc.)

    low_priority_dirs = {"devlog", "benchmarks", "_work"}

    for f in files:
        name = Path(f).name
        parts = Path(f).parts
        if name in ("CLAUDE.md", "STATUS.md", "README.md", "ROADMAP.md", "LICENSE"):
            tier_top.append(f)
        elif len(parts) > 1 and parts[0] in low_priority_dirs:
            tier_rest.append(f)
        else:
            tier_code.append(f)

    ordered = tier_top + tier_code + tier_rest
    if len(ordered) > max_files:
        result = [f"- `{f}`" for f in ordered[:max_files]]
        result.append(f"\n({len(ordered) - max_files} additional files excluded for context size)")
    else:
        result = [f"- `{f}`" for f in ordered]
    return result


def _collect_project_files_fallback(project: Path) -> list:
    """Fallback file collection when git is not available."""
    skip_dirs = {
        ".git", CONTROL_PLANE_DIRNAME, LEGACY_CONTROL_PLANE_DIRNAME, ".vscode", ".idea", "node_modules", "__pycache__",
        ".pytest_cache", "venv", ".venv", "env", ".env", "dist", "build",
        "_work", ".bridge",
    }
    files = []
    for root, dirs, filenames in os.walk(project):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        rel_root = Path(root).relative_to(project)
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            rel_path = rel_root / fname if str(rel_root) != "." else Path(fname)
            files.append(str(rel_path).replace("\\", "/"))
    return files


# ---------------------------------------------------------------------------
# cc organize
# ---------------------------------------------------------------------------

# Standard devlog subdirectories
DEVLOG_SUBDIRS = ["plans", "design", "reasoning", "criteria", "archive"]

# Patterns for classifying files into devlog subdirectories
# Each pattern: (target_subdir, filename_prefix_or_pattern)
ORGANIZE_PATTERNS = {
    "plans": lambda f: f.startswith("plan-") or f.startswith("plan_"),
    "design": lambda f: f.startswith("design-") or f.startswith("design_"),
    "reasoning": lambda f: f.startswith("reasoning") or f == "reasoning.md",
    "criteria": lambda f: f.startswith("criteria") or f.startswith("acceptance"),
}

# Optional dev/ taxonomy mode. Keep this narrower than a generic doc mover:
# public docs under docs/ are not moved, and only clear filename conventions move.
DEV_TAXONOMY_BASE_SUBDIRS = ["plans", "design", "criteria"]
DEV_TAXONOMY_OPTIONAL_SUBDIRS = [
    "research", "handoffs", "legal", "business", "prior-art",
]
DEV_TAXONOMY_SUBDIRS = (
    DEV_TAXONOMY_BASE_SUBDIRS + DEV_TAXONOMY_OPTIONAL_SUBDIRS
)
DEV_TAXONOMY_CHILD_DIRS = ["archive", "deprecated"]
DEV_TAXONOMY_ROOT_EXCLUDED_FILES = {
    "agents.md",
    "bugs.md",
    "claude.md",
    "controlcoding.md",
    "gemini.md",
    "index.md",
    "license",
    "license.md",
    "readme.md",
    "roadmap.md",
    "status.md",
}
DEV_TAXONOMY_DEV_EXCLUDED_FILES = {
    "architecture_index.md",
    "index.md",
    "methodology_full.md",
}
DEV_TAXONOMY_PATTERNS = {
    "plans": lambda f: (
        f.startswith(("plan-", "plan_", "planning-", "planning_"))
        or f.endswith(("-plan.md", "_plan.md", "-planning.md", "_planning.md"))
    ),
    "design": lambda f: (
        f in {
            "architecture.md",
            "design.md",
            "system-design.md",
            "technical-design.md",
        }
        or f.startswith((
            "architecture-",
            "architecture_",
            "design-",
            "design_",
            "dsn-",
        ))
        or f.endswith(("-design.md", "_design.md"))
    ),
    "criteria": lambda f: (
        f in {
            "acceptance.md",
            "acceptance-criteria.md",
            "acceptance_criteria.md",
            "criteria.md",
        }
        or f.startswith(("acceptance-", "acceptance_", "criteria-", "criteria_"))
        or f.endswith((
            "-acceptance.md",
            "_acceptance.md",
            "-criteria.md",
            "_criteria.md",
        ))
    ),
    "research": lambda f: (
        f.startswith((
            "research-", "research_", "audit-", "audit_", "study-", "study_",
        ))
        or f.endswith(("-research.md", "_research.md", "-audit.md", "_audit.md"))
    ),
    "handoffs": lambda f: (
        f.startswith(("handoff-", "handoff_", "handoffs-", "handoffs_"))
        or f.endswith(("-handoff.md", "_handoff.md"))
    ),
    "legal": lambda f: (
        f.startswith((
            "legal-",
            "legal_",
            "compliance-",
            "compliance_",
            "privacy-",
            "privacy_",
            "terms-",
            "terms_",
        ))
        or f.endswith(("-legal.md", "_legal.md", "-compliance.md", "_compliance.md"))
    ),
    "business": lambda f: (
        f.startswith((
            "business-",
            "business_",
            "market-",
            "market_",
            "monetization-",
            "monetization_",
            "positioning-",
            "positioning_",
            "pricing-",
            "pricing_",
        ))
        or f.endswith(("-business.md", "_business.md"))
    ),
    "prior-art": lambda f: (
        f.startswith((
            "prior-art-",
            "prior_art_",
            "prototype-",
            "prototype_",
            "spike-",
            "spike_",
            "experiment-",
            "experiment_",
        ))
        or f.endswith(("-prior-art.md", "_prior_art.md"))
    ),
}


def _classify_dev_taxonomy_doc(item: Path, *, root_file: bool = False) -> str | None:
    """Classify a file into the optional dev/ taxonomy."""
    fname = item.name.lower()
    if root_file and fname in DEV_TAXONOMY_ROOT_EXCLUDED_FILES:
        return None
    if not root_file and fname in DEV_TAXONOMY_DEV_EXCLUDED_FILES:
        return None
    for subdir, matcher in DEV_TAXONOMY_PATTERNS.items():
        if matcher(fname):
            return subdir
    return None


def _append_dev_taxonomy_move(
    project: Path,
    item: Path,
    suggested_subdir: str,
    results: list,
    reason: str,
) -> None:
    """Append one proposed move into dev/<category>/."""
    rel = item.relative_to(project)
    dest_rel = Path("dev") / suggested_subdir / item.name
    if rel == dest_rel:
        return
    results.append({
        "from": str(rel).replace("\\", "/"),
        "to": str(dest_rel).replace("\\", "/"),
        "reason": reason,
        "src": item,
        "dest": project / dest_rel,
    })


def _scan_dev_taxonomy_files(project: Path) -> list:
    """Find root or misplaced dev/ docs that clearly belong in dev/ taxonomy."""
    results = []
    doc_extensions = {".md", ".json", ".txt", ".yaml", ".yml"}

    for item in sorted(project.iterdir()):
        if not item.is_file() or item.suffix.lower() not in doc_extensions:
            continue
        suggested = _classify_dev_taxonomy_doc(item, root_file=True)
        if suggested is not None:
            _append_dev_taxonomy_move(
                project,
                item,
                suggested,
                results,
                f"belongs in dev/{suggested}/",
            )

    dev = project / "dev"
    if not dev.is_dir():
        return results

    for item in sorted(dev.rglob("*")):
        if not item.is_file() or item.suffix.lower() not in doc_extensions:
            continue
        rel_to_dev = item.relative_to(dev)
        parts = rel_to_dev.parts
        if not parts or item.name.startswith("."):
            continue
        if len(parts) > 1 and parts[1] in DEV_TAXONOMY_CHILD_DIRS:
            continue
        current_subdir = parts[0] if len(parts) > 1 else "root"
        suggested = _classify_dev_taxonomy_doc(item, root_file=False)
        if suggested is None or current_subdir == suggested:
            continue
        _append_dev_taxonomy_move(
            project,
            item,
            suggested,
            results,
            f"belongs in dev/{suggested}/",
        )

    return results


def _ensure_dev_taxonomy_dirs(
    project: Path,
    categories: set,
    dry_run: bool,
    report: dict,
    emit_human: bool = True,
) -> None:
    """Ensure the baseline dev/ taxonomy and requested categories exist."""
    dirs = [Path("dev")]
    for category in DEV_TAXONOMY_SUBDIRS:
        if category not in categories:
            continue
        category_dir = Path("dev") / category
        dirs.append(category_dir)
        for child in DEV_TAXONOMY_CHILD_DIRS:
            dirs.append(category_dir / child)

    for rel_dir in dirs:
        path = project / rel_dir
        rel_str = str(rel_dir).replace("\\", "/")
        if path.is_dir():
            continue
        if dry_run:
            if emit_human:
                info(f"Would create {rel_str}/")
        else:
            path.mkdir(parents=True, exist_ok=True)
            if emit_human:
                ok(f"Created {rel_str}/")
        report["dirs_created"].append(rel_str)


def _scan_devlog_files(project: Path) -> list:
    """Scan devlog/ for files and classify them.

    Returns a list of dicts with keys:
      - path: relative path from project root (str, forward slashes)
      - name: filename
      - size: file size in bytes
      - modified: ISO date string
      - current_subdir: current subdirectory within devlog/ (or "root")
      - suggested_subdir: where the file should go per naming conventions (or None)
    """
    devlog = project / "devlog"
    if not devlog.is_dir():
        return []

    results = []
    for item in sorted(devlog.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(project)
        rel_str = str(rel).replace("\\", "/")

        # Determine current subdirectory
        rel_to_devlog = item.relative_to(devlog)
        parts = rel_to_devlog.parts
        current_subdir = parts[0] if len(parts) > 1 else "root"

        # Classify by naming pattern
        fname = item.name.lower()
        suggested = None
        for subdir, matcher in ORGANIZE_PATTERNS.items():
            if matcher(fname):
                suggested = subdir
                break

        stat = item.stat()
        results.append({
            "path": rel_str,
            "name": item.name,
            "size": stat.st_size,
            "modified": datetime.datetime.fromtimestamp(
                stat.st_mtime
            ).strftime("%Y-%m-%d"),
            "current_subdir": current_subdir,
            "suggested_subdir": suggested,
        })
    return results


def _scan_project_docs(project: Path) -> list:
    """Scan the project root for document files that might belong in devlog/.

    Looks for plan, design, reasoning, and criteria files outside of devlog/.
    """
    results = []
    skip_dirs = {
        ".git", CONTROL_PLANE_DIRNAME, LEGACY_CONTROL_PLANE_DIRNAME, ".vscode", ".idea", "node_modules", "__pycache__",
        ".pytest_cache", "venv", ".venv", "env", ".env", "dist", "build",
        "_work", ".bridge", "devlog",
    }
    doc_extensions = {".md", ".json", ".txt", ".yaml", ".yml"}

    for item in sorted(project.iterdir()):
        if item.is_dir() and item.name in skip_dirs:
            continue
        if item.is_dir():
            # Check one level deep in non-skip directories
            for sub_item in sorted(item.iterdir()):
                if sub_item.is_file() and sub_item.suffix in doc_extensions:
                    _classify_root_doc(sub_item, project, results)
        elif item.is_file() and item.suffix in doc_extensions:
            _classify_root_doc(item, project, results)

    return results


def _classify_root_doc(item: Path, project: Path, results: list):
    """Check if a file outside devlog/ matches devlog naming patterns."""
    fname = item.name.lower()
    for subdir, matcher in ORGANIZE_PATTERNS.items():
        if matcher(fname):
            rel = item.relative_to(project)
            stat = item.stat()
            results.append({
                "path": str(rel).replace("\\", "/"),
                "name": item.name,
                "size": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(
                    stat.st_mtime
                ).strftime("%Y-%m-%d"),
                "current_location": str(rel.parent).replace("\\", "/"),
                "suggested_subdir": subdir,
            })
            break


def _generate_index(project: Path) -> str:
    """Generate devlog/index.md content from current devlog/ contents."""
    devlog = project / "devlog"
    lines = [
        "# Devlog Index",
        "",
        "> Auto-generated by `cc organize`. Do not edit manually.",
        "",
    ]

    for subdir_name in DEVLOG_SUBDIRS:
        subdir = devlog / subdir_name
        if not subdir.is_dir():
            continue
        files = sorted(subdir.iterdir())
        files = [f for f in files if f.is_file()]
        if not files:
            continue

        lines.append(f"## {subdir_name.capitalize()}")
        lines.append("")
        for f in files:
            stat = f.stat()
            size_kb = stat.st_size / 1024
            mod_date = datetime.datetime.fromtimestamp(
                stat.st_mtime
            ).strftime("%Y-%m-%d")
            lines.append(
                f"- [{f.name}]({subdir_name}/{f.name}) "
                f"({size_kb:.1f} KB, {mod_date})"
            )
        lines.append("")

    # Root-level devlog files
    root_files = sorted(
        f for f in devlog.iterdir()
        if f.is_file() and f.name != "index.md"
    )
    if root_files:
        lines.append("## Other")
        lines.append("")
        for f in root_files:
            stat = f.stat()
            size_kb = stat.st_size / 1024
            mod_date = datetime.datetime.fromtimestamp(
                stat.st_mtime
            ).strftime("%Y-%m-%d")
            lines.append(
                f"- [{f.name}]({f.name}) ({size_kb:.1f} KB, {mod_date})"
            )
        lines.append("")

    return "\n".join(lines)


def cmd_organize(project: Path, dry_run: bool = False, json_output: bool = False, check: bool = False, dev_taxonomy: bool = False, apply: bool = False) -> int:
    """Organize project files according to the CC file organization standard.

    Steps:
    1. Ensure devlog/ subdirectories exist
    2. Scan devlog/ for misplaced files
    3. Scan project root for files that belong in devlog/
    4. Propose moves (dry-run or with confirmation)
    5. Generate devlog/index.md
    6. Optionally organize clear root/dev docs into dev/ taxonomy
    7. Archive superseded documents
    """
    preview_only = dry_run or check or not apply
    emit_human = not json_output
    if emit_human:
        print(f"\nOrganizing files in: {project}\n")

    devlog = project / "devlog"
    report = {
        "dirs_created": [],
        "moves_proposed": [],
        "moves_executed": [],
        "index_generated": False,
    }

    # Step 1: Ensure devlog/ and subdirectories exist
    if not devlog.is_dir():
        if preview_only:
            if emit_human:
                info("Would create devlog/")
            report["dirs_created"].append("devlog")
        else:
            devlog.mkdir(exist_ok=True)
            if emit_human:
                ok("Created devlog/")
            report["dirs_created"].append("devlog")

    for subdir_name in DEVLOG_SUBDIRS:
        subdir = devlog / subdir_name
        if not subdir.is_dir():
            if preview_only:
                if emit_human:
                    info(f"Would create devlog/{subdir_name}/")
            else:
                subdir.mkdir(exist_ok=True)
                if emit_human:
                    ok(f"Created devlog/{subdir_name}/")
            report["dirs_created"].append(f"devlog/{subdir_name}")

    # Step 2: Scan devlog/ for misplaced files (in root, should be in subdir)
    devlog_files = _scan_devlog_files(project)
    moves = []
    for f_info in devlog_files:
        if (f_info["current_subdir"] == "root"
                and f_info["suggested_subdir"] is not None):
            src = project / f_info["path"]
            dest = devlog / f_info["suggested_subdir"] / f_info["name"]
            moves.append({
                "from": f_info["path"],
                "to": f"devlog/{f_info['suggested_subdir']}/{f_info['name']}",
                "reason": f"matches {f_info['suggested_subdir']} pattern",
                "src": src,
                "dest": dest,
            })

    # Step 3: Scan project root for stray doc files
    root_docs = _scan_project_docs(project)
    for f_info in root_docs:
        src = project / f_info["path"]
        if dev_taxonomy and _classify_dev_taxonomy_doc(src, root_file=True):
            continue
        dest = devlog / f_info["suggested_subdir"] / f_info["name"]
        moves.append({
            "from": f_info["path"],
            "to": f"devlog/{f_info['suggested_subdir']}/{f_info['name']}",
            "reason": f"belongs in devlog/{f_info['suggested_subdir']}/",
            "src": src,
            "dest": dest,
        })

    if dev_taxonomy:
        dev_taxonomy_moves = _scan_dev_taxonomy_files(project)
        target_categories = set(DEV_TAXONOMY_BASE_SUBDIRS)
        for move in dev_taxonomy_moves:
            target_categories.add(Path(move["to"]).parts[1])
        _ensure_dev_taxonomy_dirs(
            project, target_categories, preview_only, report, emit_human=emit_human
        )
        moves.extend(dev_taxonomy_moves)

    report["moves_proposed"] = [
        {"from": m["from"], "to": m["to"], "reason": m["reason"]}
        for m in moves
    ]

    # Check-only mode: report violations and exit
    if check:
        violations = len(moves)
        missing_dirs = len(report["dirs_created"])
        if json_output:
            failed = bool(violations or missing_dirs)
            payload = {
                "ok": not failed,
                "violations": violations,
                "missing_dirs": missing_dirs,
                "moves_proposed": report["moves_proposed"],
                "dirs_missing": report["dirs_created"],
            }
            if failed:
                payload["error"] = "organize_check_failed"
                payload["message"] = (
                    "File organization check failed: "
                    f"{violations} misplaced file(s), {missing_dirs} missing dir(s)."
                )
            print(json.dumps(payload, indent=2))
            return 1 if violations or missing_dirs else 0
        if violations or missing_dirs:
            warn(f"{violations} misplaced file(s), "
                 f"{missing_dirs} missing dir(s)")
            for m in moves:
                print(f"    {m['from']} -> {m['to']}")
            return 1
        ok("File organization check passed")
        return 0

    # Step 4: Propose and execute moves
    if moves:
        if emit_human:
            print(f"\n  Proposed moves ({len(moves)}):\n")
            for i, m in enumerate(moves, 1):
                print(f"    {i}. {m['from']} -> {m['to']}")
                print(f"       ({m['reason']})")

        if preview_only:
            if emit_human:
                info("\nPreview only - no files moved. Re-run with --apply to execute.")
        else:
            if emit_human:
                print()
            for m in moves:
                if m["dest"].exists():
                    if emit_human:
                        warn(f"Destination exists, skipping: {m['to']}")
                    continue
                m["dest"].parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(m["src"]), str(m["dest"]))
                if emit_human:
                    ok(f"Moved: {m['from']} -> {m['to']}")
                report["moves_executed"].append(
                    {"from": m["from"], "to": m["to"]}
                )
    else:
        if emit_human:
            info("No files need reorganization")

    # Step 5: Generate index
    if not preview_only and devlog.is_dir():
        index_content = _generate_index(project)
        index_path = devlog / "index.md"
        index_path.write_text(index_content, encoding="utf-8")
        if emit_human:
            ok("Generated devlog/index.md")
        report["index_generated"] = True
    elif preview_only:
        if emit_human:
            info("Would generate devlog/index.md")

    # Output
    if json_output:
        print(json.dumps(report, indent=2))

    if emit_human:
        summary_parts = []
        if report["dirs_created"]:
            summary_parts.append(f"{len(report['dirs_created'])} dirs created")
        if report["moves_executed"]:
            summary_parts.append(f"{len(report['moves_executed'])} files moved")
        elif report["moves_proposed"]:
            summary_parts.append(
                f"{len(report['moves_proposed'])} moves proposed"
            )
        if report["index_generated"]:
            summary_parts.append("index generated")
        if preview_only and (report["dirs_created"] or report["moves_proposed"]):
            summary_parts.append("preview only")
        summary = ", ".join(summary_parts) if summary_parts else "nothing to do"
        print(f"\n{green('Done!')} {summary}")

    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def cmd_init_module(project: Path, module_name: str, module_dir: str = None):
    """Create a new module directory with .feature-lock.json template."""
    # Validate module name
    if not module_name.strip():
        print("Error: module name cannot be empty")
        return 1

    # Check for duplicate module names across the repo
    sys.path.insert(0, str(TEMPLATES_DIR / "hooks"))
    try:
        from feature_lock import find_all_lock_files, parse_lock_file
    except ImportError:
        print("Error: feature_lock.py not found in templates/hooks/")
        return 1

    for lf in find_all_lock_files(project):
        data, err = parse_lock_file(lf)
        if err:
            continue
        if data["module"] == module_name:
            print(f"Error: module name '{module_name}' already exists in {lf}")
            return 1

    # Determine module directory (default: modules/<name>)
    if module_dir:
        rel_dir = module_dir.replace("\\", "/").strip("/")
        mod_dir = project / rel_dir
    else:
        rel_dir = f"modules/{module_name}"
        mod_dir = project / "modules" / module_name
    mod_dir.mkdir(parents=True, exist_ok=True)

    # Check if .feature-lock.json already exists
    lock_path = mod_dir / ".feature-lock.json"
    if lock_path.exists():
        print(f"Error: {lock_path} already exists. Will not overwrite.")
        return 1

    # Generate .feature-lock.json
    lock_data = {
        "version": 1,
        "module": module_name,
        "owns": [f"{rel_dir}/**"],
        "shared_write": [],
        "may_read": [],
    }
    lock_path.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

    print(f"Created module '{module_name}':")
    print(f"  Directory: {mod_dir}")
    print(f"  Lock file: {lock_path}")
    print()
    print("Edit .feature-lock.json to add shared_write and may_read entries.")
    return 0


# ---------------------------------------------------------------------------
# AGENTS.md export
# ---------------------------------------------------------------------------

# Sections from CLAUDE.md to include in AGENTS.md (explicit whitelist)
_AGENTS_MD_INCLUDE = (
    "Project Identity",
    "Architecture Rules",
    "Module Boundaries",
    "Domain Invariants",
    "Protected Zones",
)

# Sections from CLAUDE.md to exclude (for documentation; not used in code
# since we use a whitelist, but kept here for clarity)
_AGENTS_MD_EXCLUDE = {
    "Commit Ceremony",
    "Document Organization",
    "Session Start Ritual",
    "Operative Rules",
    "Current Focus",
    "Semantic Fidelity",
}

_CONTROLWORK_HOST_CONTEXT_SECTIONS = (
    "Project Identity",
    "Work Memory Rules",
    "Memory Areas",
    "Lifecycle",
    "Operative Rules",
    "Current Focus",
)

_HOST_CONTEXT_RUNTIME_SECTIONS = (
    "Project Identity",
    "Architecture Rules",
    "Module Boundaries",
    "Domain Invariants",
    "Protected Zones",
    "Session Start Ritual",
    "Operative Rules",
    "Current Focus",
)

_HOST_CONTEXT_EXPORTS = {
    "claude_code": {
        "relative_path": "CLAUDE.md",
        "file_label": "CLAUDE.md",
        "host_label": "Claude Code",
        "export_mode": "full_copy",
    },
    "codex_cli": {
        "relative_path": "AGENTS.md",
        "file_label": "AGENTS.md",
        "host_label": "Codex CLI",
        "export_mode": "section_export",
        "include_sections": _HOST_CONTEXT_RUNTIME_SECTIONS,
    },
    "gemini_cli": {
        "relative_path": "GEMINI.md",
        "file_label": "GEMINI.md",
        "host_label": "Gemini CLI",
        "export_mode": "section_export",
        "include_sections": _HOST_CONTEXT_RUNTIME_SECTIONS,
    },
    "cline": {
        "relative_path": ".clinerules",
        "file_label": ".clinerules",
        "host_label": "Cline",
        "export_mode": "section_export",
        "include_sections": _HOST_CONTEXT_RUNTIME_SECTIONS,
    },
    "cursor": {
        "relative_path": ".cursor/rules/project.mdc",
        "file_label": "project.mdc",
        "host_label": "Cursor",
        "export_mode": "section_export",
        "include_sections": _HOST_CONTEXT_RUNTIME_SECTIONS,
        "front_matter": ("---", "alwaysApply: true", "---"),
    },
    "windsurf": {
        "relative_path": ".windsurfrules",
        "file_label": ".windsurfrules",
        "host_label": "Windsurf",
        "export_mode": "section_export",
        "include_sections": _HOST_CONTEXT_RUNTIME_SECTIONS,
    },
}

_HOST_CONTEXT_NOT_APPLICABLE = frozenset({"vscode", "other"})

_ADAPTER_MARKER_OWNER = "ControlCoding"
_ADAPTER_MARKER_SCHEMA = "controlcoding.host-adapter-ownership"
_ADAPTER_MARKER_VERSION = 1
_ADAPTER_MARKER_PREFIX = "<!-- controlcoding-managed:"
_ADAPTER_MARKER_CANDIDATE = re.compile(
    r"controlcoding-managed(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_ADAPTER_FULL_COPY_PROVENANCE_LINES = (
    re.compile(r"> Generated from [^\r\n]+ by ControlCoding host-context export\."),
    re.compile(r"> Target host: [^\r\n]+\."),
    re.compile(r"> Canonical source of truth: [^\r\n]+\."),
    re.compile(r"> Selected source: [^\r\n]+ \(non-canonical\)\."),
)
_ADAPTER_NON_JSON_CONSTANT = object()
_ADAPTER_MANAGED_FORMATS = frozenset({
    "agents-md-portable-v1",
    "host-context-full-copy-v1",
    "host-context-section-export-v1",
})
_ADAPTER_NORMATIVE_STATES = frozenset({
    "target_absent",
    "owned_current",
    "owned_stale",
    "unmarked",
    "foreign",
    "invalid",
    "ambiguous",
    "unreadable_or_unsafe",
})
_AGENTS_MD_EXPORT_SPEC = {
    "relative_path": "AGENTS.md",
    "file_label": "AGENTS.md",
    "host_label": "Codex CLI",
    "export_mode": "agents_md_portable",
    "managed_format": "agents-md-portable-v1",
}


class _AdapterDuplicateMember(ValueError):
    """Signal an ambiguous JSON object before dictionary construction."""


class _AdapterIdentityDriftError(RuntimeError):
    """Signal an expected file-identity mismatch during adapter preflight."""


def _adapter_unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    marker: dict = {}
    for key, value in pairs:
        if key in marker:
            raise _AdapterDuplicateMember(
                f"duplicate decoded JSON member '{key}'"
            )
        marker[key] = value
    return marker


def _adapter_capture_non_json_constant(_constant: str):
    """Keep non-JSON constants visible until duplicate detection completes."""
    return _ADAPTER_NON_JSON_CONSTANT


def _adapter_contains_non_json_constant(value) -> bool:
    if value is _ADAPTER_NON_JSON_CONSTANT:
        return True
    if isinstance(value, dict):
        return any(_adapter_contains_non_json_constant(child) for child in value.values())
    if isinstance(value, list):
        return any(_adapter_contains_non_json_constant(child) for child in value)
    return False


def _adapter_managed_format(spec: dict) -> str:
    explicit = str(spec.get("managed_format", "")).strip()
    if explicit:
        return explicit
    if spec.get("export_mode") == "full_copy":
        return "host-context-full-copy-v1"
    return "host-context-section-export-v1"


def _adapter_source_identity(project: Path, source_path: Path) -> str:
    return _project_relative_label(project, source_path).replace("\\", "/")


def _normalize_adapter_marker_path(value: str) -> tuple[str, str]:
    raw_value = str(value).strip().replace("\\", "/")
    if not raw_value or "\x00" in raw_value:
        return "", "path is empty or contains a null byte"
    marker_path = PurePosixPath(raw_value)
    if marker_path.is_absolute() or (len(raw_value) >= 2 and raw_value[1] == ":"):
        return "", "path must be project-relative"
    if ".." in marker_path.parts:
        return "", "path must not traverse outside the project"
    normalized = str(marker_path)
    if normalized in {"", "."}:
        return "", "path must identify a project-relative file"
    return normalized, ""


def _adapter_declared_self_binding_issue(project: Path, marker: dict) -> tuple[str, str]:
    declared_source = project.joinpath(*PurePosixPath(str(marker["source"])).parts)
    declared_target = project.joinpath(*PurePosixPath(str(marker["target"])).parts)
    for label, path in (("source", declared_source), ("target", declared_target)):
        path_issue = _managed_path_issue(project, path)
        if path_issue:
            return "unreadable_or_unsafe", f"declared marker {label} path is unsafe: {path_issue}"

    source_key = os.path.normcase(os.path.normpath(os.path.abspath(str(declared_source))))
    target_key = os.path.normcase(os.path.normpath(os.path.abspath(str(declared_target))))
    if source_key == target_key:
        return "invalid", "ownership marker source and target resolve to the same project-local path"

    existing_keys: dict[str, tuple[int, int, int] | None] = {}
    for label, path in (("source", declared_source), ("target", declared_target)):
        try:
            path_stat = os.lstat(path)
        except FileNotFoundError:
            existing_keys[label] = None
        except OSError as exc:
            return "unreadable_or_unsafe", f"could not verify declared marker {label} path identity: {exc}"
        else:
            if not _transaction_stat_is_safe_regular(path_stat):
                return "unreadable_or_unsafe", f"declared marker {label} is not a safe regular file"
            try:
                existing_keys[label] = _filesystem_object_key(path_stat)
            except OSError as exc:
                return "unreadable_or_unsafe", f"could not verify declared marker {label} path identity: {exc}"

    if (
        existing_keys.get("source") is not None
        and existing_keys.get("source") == existing_keys.get("target")
    ):
        return "invalid", "ownership marker source and target identify the same filesystem file"
    return "", ""


def _adapter_proposed_source_target_issue(project: Path,
                                          source_path: Path,
                                          target: Path) -> tuple[str, str]:
    for label, path in (("source", source_path), ("target", target)):
        path_issue = _managed_path_issue(project, path)
        if path_issue:
            return "unreadable_or_unsafe", f"proposed adapter {label} path is unsafe: {path_issue}"

    source_key = os.path.normcase(os.path.normpath(os.path.abspath(str(source_path))))
    target_key = os.path.normcase(os.path.normpath(os.path.abspath(str(target))))
    if source_key == target_key:
        return "invalid", "context source and adapter target resolve to the same project-local path"

    existing_keys: dict[str, tuple[int, int, int] | None] = {}
    for label, path in (("source", source_path), ("target", target)):
        try:
            path_stat = os.lstat(path)
        except FileNotFoundError:
            existing_keys[label] = None
        except OSError as exc:
            return "unreadable_or_unsafe", f"could not verify proposed adapter {label} identity: {exc}"
        else:
            if not _transaction_stat_is_safe_regular(path_stat):
                return "unreadable_or_unsafe", f"proposed adapter {label} is not a safe regular file"
            try:
                existing_keys[label] = _filesystem_object_key(path_stat)
            except OSError as exc:
                return "unreadable_or_unsafe", f"could not verify proposed adapter {label} identity: {exc}"
    if (
        existing_keys.get("source") is not None
        and existing_keys.get("source") == existing_keys.get("target")
    ):
        return "invalid", "context source and adapter target identify the same filesystem file"
    return "", ""


def _adapter_marker_line(target: str,
                         host: str,
                         source: str,
                         managed_format: str) -> str:
    marker = {
        "format": managed_format,
        "host": str(host).strip(),
        "owner": _ADAPTER_MARKER_OWNER,
        "schema": _ADAPTER_MARKER_SCHEMA,
        "source": str(source).strip().replace("\\", "/"),
        "target": str(target).strip().replace("\\", "/"),
        "version": _ADAPTER_MARKER_VERSION,
    }
    return (
        f"{_ADAPTER_MARKER_PREFIX} "
        + json.dumps(marker, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + " -->"
    )


def _adapter_newline(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def _adapter_marker_candidates(content: str) -> list[tuple[int, str]]:
    """Enumerate ControlCoding candidate comments while preserving comment bounds."""
    candidates: list[tuple[int, str]] = []
    search_offset = 0
    while search_offset < len(content):
        opener_offset = content.find("<!--", search_offset)
        if opener_offset < 0:
            break
        closer_offset = content.find("-->", opener_offset + 4)
        if closer_offset < 0:
            comment = content[opener_offset:]
            search_offset = len(content)
        else:
            comment = content[opener_offset:closer_offset + 3]
            search_offset = closer_offset + 3
        if _ADAPTER_MARKER_CANDIDATE.search(comment):
            line_index = len(re.findall(r"\r\n|\r|\n", content[:opener_offset]))
            candidates.append((line_index, comment))
        if closer_offset < 0:
            break
    return candidates


def _adapter_front_matter_layout(content: str) -> tuple[int | None, int | None, str]:
    """Return the closing line and insertion offset for exact YAML front matter."""
    lines = content.splitlines(keepends=True)
    if not lines:
        return None, None, ""
    first_line = lines[0].rstrip("\r\n")
    if first_line != "---":
        if first_line.strip().startswith("---"):
            return None, None, "malformed YAML front matter opening delimiter"
        return None, None, ""
    offset = len(lines[0])
    for index, line in enumerate(lines[1:], start=1):
        delimiter = line.rstrip("\r\n")
        if delimiter in {"---", "..."}:
            return index, offset + len(line), ""
        if delimiter.strip() in {"---", "..."}:
            return None, None, "malformed YAML front matter closing delimiter"
        offset += len(line)
    return None, None, "unterminated YAML front matter"


def _adapter_full_copy_payload_line_index(content: str) -> int:
    """Locate the payload after the full-copy marker and recognized provenance."""
    lines = content.splitlines(keepends=True)
    line_index = 1
    while line_index < len(lines):
        line = lines[line_index].rstrip("\r\n")
        if line == "" or any(
            pattern.fullmatch(line)
            for pattern in _ADAPTER_FULL_COPY_PROVENANCE_LINES
        ):
            line_index += 1
            continue
        break
    return line_index


def _adapter_expected_marker_line_index(content: str,
                                        managed_format: str) -> tuple[int | None, str]:
    front_matter_end, _offset, front_matter_error = _adapter_front_matter_layout(content)
    if front_matter_error:
        return None, front_matter_error
    if front_matter_end is not None:
        return front_matter_end + 1, ""
    if managed_format == "host-context-full-copy-v1":
        lines = content.splitlines(keepends=True)
        payload_start = _adapter_full_copy_payload_line_index(content)
        payload_lines = lines[payload_start:]
        if payload_lines:
            following_front_matter = "".join(payload_lines)
            following_end, _following_offset, following_error = (
                _adapter_front_matter_layout(following_front_matter)
            )
            if following_end is not None or following_error:
                return None, "full-copy front matter must precede the managed header"
        return 0, ""
    if managed_format in {
        "agents-md-portable-v1",
        "host-context-section-export-v1",
    }:
        lines = content.splitlines()
        if len(lines) >= 3 and lines[0].startswith("# ") and lines[1] == "":
            return 2, ""
        return None, "managed adapter header must start with an H1 followed by a blank line"
    return None, "ownership marker format is unsupported"


def _adapter_foreign_header_indexes(content: str,
                                    managed_format: str = "") -> set[int]:
    front_matter_end, _offset, front_matter_error = _adapter_front_matter_layout(content)
    if front_matter_error:
        return set()
    if front_matter_end is not None:
        return {front_matter_end + 1}

    formats = (
        {managed_format}
        if managed_format in _ADAPTER_MANAGED_FORMATS
        else set(_ADAPTER_MANAGED_FORMATS)
    )
    indexes: set[int] = set()
    if "host-context-full-copy-v1" in formats:
        indexes.add(0)
    if formats.intersection({
        "agents-md-portable-v1",
        "host-context-section-export-v1",
    }):
        lines = content.splitlines()
        if len(lines) >= 3 and lines[0].startswith("# ") and lines[1] == "":
            indexes.add(2)
    return indexes


def _adapter_has_foreign_ownership_marker(content: str,
                                          managed_format: str = "") -> bool:
    """Recognize foreign ownership only at a format-derived managed header."""
    lines = content.splitlines()
    for line_index in _adapter_foreign_header_indexes(content, managed_format):
        if line_index >= len(lines):
            continue
        line = lines[line_index]
        normalized = line.strip().lower()
        if (
            normalized.startswith("<!--")
            and normalized.endswith("-->")
            and not _ADAPTER_MARKER_CANDIDATE.search(line)
            and any(token in normalized for token in ("managed-by", "generated-by", "ownership"))
        ):
            return True
    return False


def _adapter_insert_ownership_marker(content: str,
                                     marker_line: str,
                                     managed_format: str,
                                     target_label: str) -> str:
    """Insert the embedded marker at the format-specific managed-header position."""
    front_matter_end, front_matter_offset, front_matter_error = _adapter_front_matter_layout(content)
    if front_matter_error:
        raise ValueError(front_matter_error)
    newline = _adapter_newline(content)
    if front_matter_end is not None and front_matter_offset is not None:
        prefix = content[:front_matter_offset]
        if not prefix.endswith(("\n", "\r")):
            prefix += newline
        return prefix + marker_line + newline + newline + content[front_matter_offset:]
    if managed_format == "host-context-full-copy-v1":
        return marker_line + newline + newline + content
    if managed_format in {
        "agents-md-portable-v1",
        "host-context-section-export-v1",
    }:
        lines = content.splitlines(keepends=True)
        if (
            len(lines) >= 2
            and lines[0].rstrip("\r\n").startswith("# ")
            and lines[1].rstrip("\r\n") == ""
        ):
            offset = len(lines[0]) + len(lines[1])
            return content[:offset] + marker_line + newline + newline + content[offset:]
        title = PurePosixPath(target_label).name
        return f"# {title}{newline}{newline}{marker_line}{newline}{newline}" + content
    raise ValueError("ownership marker format is unsupported")


def _adapter_generated_content_issue(content: str,
                                     expected_target: str,
                                     expected_host: str,
                                     expected_source: str,
                                     expected_format: str) -> tuple[str, str]:
    marker, marker_state, marker_detail = _adapter_marker_payload(
        content,
        expected_format,
    )
    if marker is None:
        state = (
            marker_state
            if marker_state in {"invalid", "ambiguous", "unreadable_or_unsafe"}
            else "invalid"
        )
        return state, f"generated adapter output is not safely managed: {marker_detail}"
    expected_binding = {
        "owner": _ADAPTER_MARKER_OWNER,
        "schema": _ADAPTER_MARKER_SCHEMA,
        "version": _ADAPTER_MARKER_VERSION,
        "target": expected_target,
        "host": expected_host,
        "source": expected_source,
        "format": expected_format,
    }
    if marker != expected_binding:
        return "invalid", "generated adapter ownership marker does not match the planned binding"
    return "", ""


def _transaction_checkpoint(name: str, **context) -> None:
    """Private deterministic failure-injection boundary used by regressions."""


def _filesystem_object_key(file_stat: os.stat_result) -> tuple[int, int, int]:
    inode = getattr(file_stat, "st_ino", None)
    device = getattr(file_stat, "st_dev", None)
    mode = getattr(file_stat, "st_mode", None)
    if inode is None or int(inode) == 0 or device is None or mode is None:
        raise OSError("filesystem object identity is unavailable")
    return (int(device), int(inode), int(stat.S_IFMT(int(mode))))


def _filesystem_observation(file_stat: os.stat_result) -> tuple[int, ...]:
    ctime_ns = getattr(file_stat, "st_ctime_ns", None)
    if ctime_ns is None:
        ctime_ns = int(float(file_stat.st_ctime) * 1_000_000_000)
    mtime_ns = getattr(file_stat, "st_mtime_ns", None)
    if mtime_ns is None:
        mtime_ns = int(float(file_stat.st_mtime) * 1_000_000_000)
    return (
        *_filesystem_object_key(file_stat),
        stat.S_IMODE(int(file_stat.st_mode)),
        int(getattr(file_stat, "st_size", -1)),
        int(ctime_ns),
        int(mtime_ns),
    )


def _filesystem_is_reparse(file_stat: os.stat_result) -> bool:
    file_attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    return bool(reparse_flag and file_attributes & reparse_flag)


def _transaction_boundary_paths(project: Path, target: Path) -> tuple[Path, Path, tuple[Path, ...]]:
    project_path = project.absolute()
    target_path = target.absolute()
    try:
        relative = target_path.relative_to(project_path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise OSError("target is outside the project") from exc

    parent_paths: list[Path] = [project_path]
    current = project_path
    for part in relative.parts[:-1]:
        current = current / part
        parent_paths.append(current)
    return project_path, target_path, tuple(parent_paths)


def _snapshot_transaction_parents(project: Path, target: Path) -> list[dict]:
    _project_path, _target_path, parent_paths = _transaction_boundary_paths(project, target)
    snapshots: list[dict] = []
    missing_ancestor = False
    for parent in parent_paths:
        if missing_ancestor:
            snapshots.append({
                "path": str(parent),
                "state": "absent",
                "objectKey": None,
                "mode": None,
                "transactionOwned": False,
            })
            continue
        try:
            parent_stat = os.lstat(parent)
        except FileNotFoundError:
            missing_ancestor = True
            snapshots.append({
                "path": str(parent),
                "state": "absent",
                "objectKey": None,
                "mode": None,
                "transactionOwned": False,
            })
            continue
        if (
            stat.S_ISLNK(parent_stat.st_mode)
            or _filesystem_is_reparse(parent_stat)
            or not stat.S_ISDIR(parent_stat.st_mode)
        ):
            raise OSError(f"unsafe transaction parent: {parent}")
        snapshots.append({
            "path": str(parent),
            "state": "present",
            "objectKey": _filesystem_object_key(parent_stat),
            "mode": stat.S_IMODE(parent_stat.st_mode),
            "transactionOwned": False,
        })
    return snapshots


def _revalidate_transaction_parents(project: Path,
                                    target: Path,
                                    expected: list[dict]) -> None:
    current = _snapshot_transaction_parents(project, target)
    if len(current) != len(expected):
        raise RuntimeError(f"transaction parent set changed: {target}")
    for expected_parent, current_parent in zip(expected, current):
        if str(expected_parent.get("path")) != str(current_parent.get("path")):
            raise RuntimeError(f"transaction parent path changed: {target}")
        if expected_parent.get("state") != current_parent.get("state"):
            raise RuntimeError(
                f"transaction parent state changed: {expected_parent.get('path')}"
            )
        if expected_parent.get("state") == "present" and (
            tuple(expected_parent.get("objectKey") or ())
            != tuple(current_parent.get("objectKey") or ())
            or expected_parent.get("mode") != current_parent.get("mode")
        ):
            raise RuntimeError(
                f"transaction parent identity or mode changed: {expected_parent.get('path')}"
            )


def _identity_safe_file_snapshot(project: Path,
                                 path: Path,
                                 purpose: str,
                                 expected_parents: list[dict] | None = None) -> dict:
    parents = (
        _snapshot_transaction_parents(project, path)
        if expected_parents is None
        else [dict(item) for item in expected_parents]
    )
    if expected_parents is not None:
        _revalidate_transaction_parents(project, path, expected_parents)

    descriptor = -1
    try:
        before_path_stat = os.lstat(path)
    except FileNotFoundError:
        return {
            "state": "absent",
            "bytes": None,
            "hash": "missing",
            "mode": None,
            "objectKey": None,
            "parents": parents,
        }
    if (
        stat.S_ISLNK(before_path_stat.st_mode)
        or _filesystem_is_reparse(before_path_stat)
        or not stat.S_ISREG(before_path_stat.st_mode)
    ):
        raise OSError(f"{purpose} is not a safe regular file: {path}")
    before_key = _filesystem_object_key(before_path_stat)
    before_path_observation = _filesystem_observation(before_path_stat)
    open_flags = os.O_RDONLY
    open_flags |= int(getattr(os, "O_BINARY", 0) or 0)
    open_flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
    try:
        _transaction_checkpoint("before_file_open", path=path, purpose=purpose)
        descriptor = os.open(path, open_flags)
        before_descriptor_stat = os.fstat(descriptor)
        if (
            not _transaction_stat_is_safe_regular(before_descriptor_stat)
            or _filesystem_object_key(before_descriptor_stat) != before_key
        ):
            raise _AdapterIdentityDriftError(
                f"{purpose} identity drifted during open: {path}"
            )
        before_descriptor_observation = _filesystem_observation(
            before_descriptor_stat
        )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        _transaction_checkpoint("after_file_read", path=path, purpose=purpose)
        after_descriptor_stat = os.fstat(descriptor)
        after_path_stat = os.lstat(path)
        if (
            not _transaction_stat_is_safe_regular(after_descriptor_stat)
            or not _transaction_stat_is_safe_regular(after_path_stat)
            or _filesystem_object_key(after_descriptor_stat) != before_key
            or _filesystem_object_key(after_path_stat) != before_key
            or _filesystem_observation(after_descriptor_stat)
            != before_descriptor_observation
            or _filesystem_observation(after_path_stat) != before_path_observation
        ):
            raise _AdapterIdentityDriftError(
                f"{purpose} identity or state drifted while reading: {path}"
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _revalidate_transaction_parents(project, path, parents)
    return {
        "state": "present",
        "bytes": content,
        "hash": hashlib.sha256(content).hexdigest(),
        "mode": stat.S_IMODE(after_path_stat.st_mode),
        "objectKey": before_key,
        "parents": parents,
    }


def _transaction_snapshots_match(expected: dict, current: dict) -> bool:
    if expected.get("state") != current.get("state"):
        return False
    if expected.get("state") == "absent":
        return True
    return (
        tuple(expected.get("objectKey") or ())
        == tuple(current.get("objectKey") or ())
        and expected.get("hash") == current.get("hash")
        and expected.get("mode") == current.get("mode")
    )


def _private_snapshot_record(snapshot: dict) -> dict:
    return {
        "state": snapshot.get("_originalState", "unreadable"),
        "bytes": snapshot.get("_originalBytes"),
        "hash": snapshot.get("_originalHash", ""),
        "mode": snapshot.get("_originalMode"),
        "objectKey": snapshot.get("_originalObjectKey"),
        "parents": [dict(item) for item in snapshot.get("_parentSnapshots", [])],
    }


def _read_adapter_context_source(project: Path,
                                 source: str = "") -> tuple[Path | None, str, bytes, dict | None, str]:
    """Read an adapter source without silently using a legacy host adapter."""
    requested_source = str(source or "").strip()
    if requested_source:
        source_path = _resolve_explicit_context_source(project, requested_source)
        if source_path is None:
            return None, "", b"", None, "context source is outside the project or invalid"
    else:
        source_path = _canonical_context_path(project)

    try:
        source_issue = _managed_path_issue(project, source_path)
        if source_issue:
            return None, "", b"", None, f"context source path is unsafe: {source_issue}"
        source_snapshot = _identity_safe_file_snapshot(
            project,
            source_path,
            "adapter source",
        )
        if source_snapshot["state"] == "absent":
            return None, "", b"", source_snapshot, f"context source is missing: {_project_relative_label(project, source_path)}"
        source_bytes = source_snapshot["bytes"]
        source_text = source_bytes.decode("utf-8")
    except _AdapterIdentityDriftError as exc:
        return None, "", b"", None, f"context source is unreadable or unsafe: {exc}"
    except (OSError, UnicodeError) as exc:
        return None, "", b"", None, f"context source is unreadable: {exc}"
    return source_path, source_text, source_bytes, source_snapshot, ""


def _managed_path_issue(project: Path, target: Path) -> str:
    """Return a fail-closed reason for unsafe project-local write paths."""
    try:
        project_path, target_path, parent_paths = _transaction_boundary_paths(
            project,
            target,
        )
        relative = target_path.relative_to(project_path)
    except OSError as exc:
        return str(exc)

    inspected_paths = [*parent_paths]
    if relative.parts:
        inspected_paths.append(target_path)
    for index, current in enumerate(inspected_paths):
        try:
            file_stat = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return f"target path metadata is unreadable: {exc}"
        if stat.S_ISLNK(file_stat.st_mode):
            return f"target path contains a symbolic link: {_project_relative_label(project, current)}"
        if _filesystem_is_reparse(file_stat):
            return f"target path contains a reparse point: {_project_relative_label(project, current)}"
        try:
            _filesystem_object_key(file_stat)
        except OSError as exc:
            return f"target path identity is unavailable: {exc}"
        if index < len(inspected_paths) - 1 and not stat.S_ISDIR(file_stat.st_mode):
            return f"target parent is not a directory: {_project_relative_label(project, current)}"
    return ""


def _adapter_marker_payload(content: str,
                            managed_format: str = "") -> tuple[dict | None, str, str]:
    marker_candidates = _adapter_marker_candidates(content)
    if not marker_candidates:
        if _adapter_has_foreign_ownership_marker(content, managed_format):
            return None, "foreign", "explicit non-ControlCoding ownership marker"
        return None, "unmarked", "no ControlCoding ownership marker"
    if len(marker_candidates) != 1:
        return None, "ambiguous", "multiple ControlCoding ownership markers"

    marker_line_index, marker_comment = marker_candidates[0]
    marker_lines = content.splitlines()
    if (
        "\r" in marker_comment
        or "\n" in marker_comment
        or marker_line_index >= len(marker_lines)
        or marker_lines[marker_line_index] != marker_comment
        or not marker_comment.startswith(_ADAPTER_MARKER_PREFIX)
        or not marker_comment.endswith("-->")
    ):
        return None, "invalid", "malformed ControlCoding ownership marker"
    encoded = marker_comment[len(_ADAPTER_MARKER_PREFIX):-3].strip()
    try:
        marker = json.loads(
            encoded,
            object_pairs_hook=_adapter_unique_json_object,
            parse_constant=_adapter_capture_non_json_constant,
        )
        contains_non_json_constant = _adapter_contains_non_json_constant(marker)
    except _AdapterDuplicateMember as exc:
        return None, "ambiguous", f"ownership marker is ambiguous: {exc}"
    except RecursionError:
        return None, "unreadable_or_unsafe", "ownership marker exceeds safe JSON nesting depth"
    except (TypeError, ValueError):
        return None, "invalid", "ownership marker is not valid JSON"
    if contains_non_json_constant:
        return None, "invalid", "ownership marker contains a non-JSON constant"
    if not isinstance(marker, dict):
        return None, "invalid", "ownership marker must be a JSON object"

    required_types = {
        "owner": str,
        "schema": str,
        "version": int,
        "target": str,
        "host": str,
        "source": str,
        "format": str,
    }
    for key, expected_type in required_types.items():
        value = marker.get(key)
        if (
            not isinstance(value, expected_type)
            or (expected_type is int and isinstance(value, bool))
            or (expected_type is str and not value.strip())
        ):
            return None, "invalid", f"ownership marker field '{key}' is invalid"
    if marker["owner"] != _ADAPTER_MARKER_OWNER:
        return None, "invalid", "ownership marker owner is unsupported"
    if marker["schema"] != _ADAPTER_MARKER_SCHEMA:
        return None, "invalid", "ownership marker schema is unsupported"
    if marker["version"] != _ADAPTER_MARKER_VERSION:
        return None, "invalid", "ownership marker version is unsupported"
    if marker["format"] not in _ADAPTER_MANAGED_FORMATS:
        return None, "invalid", "ownership marker format is unsupported"
    for path_field in ("target", "source"):
        normalized_path, path_error = _normalize_adapter_marker_path(marker[path_field])
        if path_error:
            return None, "invalid", f"ownership marker field '{path_field}' {path_error}"
        marker[path_field] = normalized_path
    if marker["source"] == marker["target"]:
        return None, "invalid", "ownership marker source and target must be different files"
    expected_line_index, position_error = _adapter_expected_marker_line_index(
        content,
        marker["format"],
    )
    if position_error:
        return None, "invalid", f"ownership marker position is invalid: {position_error}"
    if marker_line_index != expected_line_index:
        return None, "invalid", "ownership marker is outside the format-specific managed header"
    return marker, "owned", "valid ControlCoding ownership marker"


def _adapter_target_snapshot(project: Path,
                             target: Path,
                             expected_content: str,
                             expected_target: str,
                             expected_host: str,
                             expected_source: str,
                             expected_format: str) -> dict:
    path_issue = _managed_path_issue(project, target)
    if path_issue:
        return {
            "state": "unreadable_or_unsafe",
            "ownership": "unreadable_or_unsafe",
            "detail": path_issue,
            "remediation": "Replace the unsafe path with a regular project-local file before retrying.",
            "marker": None,
            "bindingMatches": False,
            "bindingAllowed": False,
            "_originalBytes": None,
            "_originalHash": "",
            "_originalMode": None,
            "_originalObjectKey": None,
            "_originalState": "unreadable",
            "_parentSnapshots": [],
        }
    try:
        raw_snapshot = _identity_safe_file_snapshot(
            project,
            target,
            "adapter target and embedded marker",
        )
        if raw_snapshot["state"] == "absent":
            return {
                "state": "target_absent",
                "ownership": "none",
                "detail": "target does not exist",
                "remediation": "",
                "marker": None,
                "bindingMatches": False,
                "bindingAllowed": True,
                "_originalBytes": None,
                "_originalHash": "missing",
                "_originalMode": None,
                "_originalObjectKey": None,
                "_originalState": "absent",
                "_parentSnapshots": raw_snapshot["parents"],
            }
    except (_AdapterIdentityDriftError, OSError) as exc:
        return {
            "state": "unreadable_or_unsafe",
            "ownership": "unreadable_or_unsafe",
            "detail": str(exc),
            "remediation": "Restore read access to the regular target file before retrying.",
            "marker": None,
            "bindingMatches": False,
            "bindingAllowed": False,
            "_originalBytes": None,
            "_originalHash": "",
            "_originalMode": None,
            "_originalObjectKey": None,
            "_originalState": "unreadable",
            "_parentSnapshots": [],
        }

    original_bytes = raw_snapshot["bytes"]
    original_mode = raw_snapshot["mode"]
    try:
        current_content = original_bytes.decode("utf-8")
    except UnicodeError as exc:
        return {
            "state": "invalid",
            "ownership": "invalid",
            "detail": f"adapter target is not valid UTF-8: {exc}",
            "remediation": "Replace the target content with valid UTF-8 before retrying.",
            "marker": None,
            "bindingMatches": False,
            "bindingAllowed": False,
            "_originalBytes": original_bytes,
            "_originalHash": hashlib.sha256(original_bytes).hexdigest(),
            "_originalMode": original_mode,
            "_originalObjectKey": raw_snapshot["objectKey"],
            "_originalState": "present",
            "_parentSnapshots": raw_snapshot["parents"],
        }

    marker, marker_state, marker_detail = _adapter_marker_payload(
        current_content,
        expected_format,
    )
    original_hash = hashlib.sha256(original_bytes).hexdigest()
    if marker is None and marker_state in {"invalid", "ambiguous"}:
        return {
            "state": marker_state,
            "ownership": marker_state,
            "detail": marker_detail,
            "remediation": "Repair the ownership marker and its managed-header position before retrying.",
            "marker": None,
            "bindingMatches": False,
            "bindingAllowed": False,
            "_originalBytes": original_bytes,
            "_originalHash": original_hash,
            "_originalMode": original_mode,
            "_originalObjectKey": raw_snapshot["objectKey"],
            "_originalState": "present",
            "_parentSnapshots": raw_snapshot["parents"],
        }

    _front_matter_end, _front_matter_offset, front_matter_error = _adapter_front_matter_layout(
        current_content
    )
    if front_matter_error:
        return {
            "state": "unreadable_or_unsafe",
            "ownership": "unreadable_or_unsafe",
            "detail": front_matter_error,
            "remediation": "Repair the malformed front matter before retrying.",
            "marker": None,
            "bindingMatches": False,
            "bindingAllowed": False,
            "_originalBytes": original_bytes,
            "_originalHash": original_hash,
            "_originalMode": original_mode,
            "_originalObjectKey": raw_snapshot["objectKey"],
            "_originalState": "present",
            "_parentSnapshots": raw_snapshot["parents"],
        }
    if marker is None:
        return {
            "state": marker_state,
            "ownership": marker_state,
            "detail": marker_detail,
            "remediation": "Use `cc context adopt` for an unmarked file, or repair the marker explicitly.",
            "marker": None,
            "bindingMatches": False,
            "bindingAllowed": False,
            "_originalBytes": original_bytes,
            "_originalHash": original_hash,
            "_originalMode": original_mode,
            "_originalObjectKey": raw_snapshot["objectKey"],
            "_originalState": "present",
            "_parentSnapshots": raw_snapshot["parents"],
        }

    self_binding_state, self_binding_detail = _adapter_declared_self_binding_issue(project, marker)
    if self_binding_state:
        return {
            "state": self_binding_state,
            "ownership": self_binding_state,
            "detail": self_binding_detail,
            "remediation": "Regenerate the adapter from a distinct project-local canonical source file.",
            "marker": marker,
            "bindingMatches": False,
            "bindingAllowed": False,
            "bindingIssues": [self_binding_detail],
            "_originalBytes": original_bytes,
            "_originalHash": original_hash,
            "_originalMode": original_mode,
            "_originalObjectKey": raw_snapshot["objectKey"],
            "_originalState": "present",
            "_parentSnapshots": raw_snapshot["parents"],
        }

    expected_binding = {
        "owner": _ADAPTER_MARKER_OWNER,
        "schema": _ADAPTER_MARKER_SCHEMA,
        "version": _ADAPTER_MARKER_VERSION,
        "target": expected_target.replace("\\", "/"),
        "host": expected_host,
        "source": expected_source.replace("\\", "/"),
        "format": expected_format,
    }
    target_matches = marker.get("target") == expected_binding["target"]
    host_matches = marker.get("host") == expected_binding["host"]
    source_matches = marker.get("source") == expected_binding["source"]
    format_matches = marker.get("format") == expected_binding["format"]
    binding_matches = target_matches and host_matches and source_matches and format_matches
    binding_issues: list[str] = []
    if not target_matches:
        binding_issues.append(
            f"marker target '{marker.get('target')}' does not match '{expected_binding['target']}'"
        )
    if not host_matches:
        binding_issues.append(
            f"marker host '{marker.get('host')}' does not match '{expected_binding['host']}'"
        )
    if not format_matches:
        binding_issues.append(
            f"marker format '{marker.get('format')}' does not match '{expected_binding['format']}'"
        )
    if not source_matches:
        binding_issues.append(
            f"marker source '{marker.get('source')}' does not match '{expected_binding['source']}'"
        )
    if binding_issues:
        detail = "; ".join(binding_issues)
        return {
            "state": "invalid",
            "ownership": "invalid",
            "detail": detail,
            "remediation": "Restore an adapter whose embedded binding exactly matches its target, host, source, and format.",
            "marker": marker,
            "bindingMatches": False,
            "bindingAllowed": False,
            "bindingIssues": binding_issues,
            "_originalBytes": original_bytes,
            "_originalHash": original_hash,
            "_originalMode": original_mode,
            "_originalObjectKey": raw_snapshot["objectKey"],
            "_originalState": "present",
            "_parentSnapshots": raw_snapshot["parents"],
        }
    content_matches = original_bytes == expected_content.encode("utf-8")
    detail = "owned content matches generated output" if content_matches else "owned content differs from generated output"
    return {
        "state": "owned_current" if content_matches and binding_matches else "owned_stale",
        "ownership": "owned",
        "detail": detail,
        "remediation": "",
        "marker": marker,
        "bindingMatches": binding_matches,
        "bindingAllowed": True,
        "bindingIssues": binding_issues,
        "_originalBytes": original_bytes,
        "_originalHash": original_hash,
        "_originalMode": original_mode,
        "_originalObjectKey": raw_snapshot["objectKey"],
        "_originalState": "present",
        "_parentSnapshots": raw_snapshot["parents"],
    }


def _adapter_plan_entry(project: Path,
                        host: str,
                        spec: dict,
                        source_path: Path,
                        source_identity: str,
                        expected_content: str,
                        operation: str,
                        warnings: list[str] | None = None,
                        force: bool = False) -> dict:
    target_label = str(spec["relative_path"]).replace("\\", "/")
    target = project / target_label
    managed_format = _adapter_managed_format(spec)
    normalized_host = str(host).strip()
    snapshot = _adapter_target_snapshot(
        project,
        target,
        expected_content,
        target_label,
        normalized_host,
        source_identity,
        managed_format,
    )
    source_target_state, source_target_detail = _adapter_proposed_source_target_issue(
        project,
        source_path,
        target,
    )
    snapshot_is_ambiguous = snapshot.get("state") == "ambiguous"
    source_target_has_priority = bool(source_target_state) and not snapshot_is_ambiguous
    if source_target_has_priority:
        snapshot.update({
            "state": source_target_state,
            "ownership": source_target_state,
            "detail": source_target_detail,
            "remediation": "Choose a source file that is distinct from the generated adapter target.",
            "bindingMatches": False,
            "bindingAllowed": False,
        })

    if (
        operation != "adopt"
        and not snapshot_is_ambiguous
        and not source_target_has_priority
    ):
        generated_state, generated_detail = _adapter_generated_content_issue(
            expected_content,
            target_label,
            normalized_host,
            source_identity,
            managed_format,
        )
        if generated_state:
            snapshot.update({
                "state": generated_state,
                "ownership": generated_state,
                "detail": generated_detail,
                "remediation": "Remove embedded ownership-marker text from the canonical source before retrying.",
                "bindingMatches": False,
                "bindingAllowed": False,
            })

    state = snapshot["state"]
    if state not in _ADAPTER_NORMATIVE_STATES:
        state = "unreadable_or_unsafe"
        snapshot.update({
            "state": state,
            "ownership": state,
            "detail": "adapter classification did not produce a normative state",
            "bindingAllowed": False,
        })
    transaction_content = expected_content
    action = "block"
    if operation == "adopt":
        if state in {"unmarked", "foreign"}:
            marker_line = _adapter_marker_line(
                target_label,
                normalized_host,
                source_identity,
                managed_format,
            )
            original_text = snapshot["_originalBytes"].decode("utf-8")
            try:
                transaction_content = _adapter_insert_ownership_marker(
                    original_text,
                    marker_line,
                    managed_format,
                    target_label,
                )
            except ValueError as exc:
                state = "unreadable_or_unsafe"
                snapshot["state"] = state
                snapshot["ownership"] = state
                detail = f"adapter adoption is unsafe: {exc}"
            else:
                adoption_state, adoption_detail = _adapter_generated_content_issue(
                    transaction_content,
                    target_label,
                    normalized_host,
                    source_identity,
                    managed_format,
                )
                if adoption_state:
                    state = adoption_state
                    snapshot["state"] = state
                    snapshot["ownership"] = state
                    detail = adoption_detail
                else:
                    action = "adopt"
                    detail = "explicit adoption will add the ownership binding and preserve existing payload"
        elif state == "target_absent":
            action = "noop"
            detail = "adoption does not create an absent adapter target"
        elif state in {"owned_current", "owned_stale"} and snapshot.get("bindingAllowed"):
            action = "noop"
            detail = "adapter is already valid-owned; adoption is idempotent"
        else:
            detail = "adoption is allowed only for an existing safe unmarked or foreign target"
    elif not snapshot.get("bindingAllowed", False):
        detail = snapshot.get("detail", "adapter ownership binding is not valid for this target")
    elif state == "target_absent":
        action = "create"
        detail = "create managed adapter"
    elif state == "owned_current":
        action = "noop"
        detail = "owned adapter is already current"
    elif state == "owned_stale" and operation in {"sync", "switch", "setup"}:
        action = "update"
        detail = (
            snapshot.get("detail", "update valid-owned adapter after preview")
            if not snapshot.get("bindingMatches")
            else "update valid-owned adapter after preview"
        )
    elif state == "owned_stale" and operation == "export" and force:
        action = "replace"
        detail = (
            snapshot.get("detail", "force will replace the existing valid-owned adapter")
            if not snapshot.get("bindingMatches")
            else "force will replace the existing valid-owned adapter"
        )
    elif state == "owned_stale" and operation == "export":
        detail = "existing owned adapter requires --force before replacement"
    else:
        detail = snapshot.get("detail", "adapter preflight failed")

    replaces_existing = action in {"update", "replace", "adopt"}
    return {
        "kind": "adapter",
        "host": normalized_host,
        "label": str(spec.get("host_label", normalized_host)),
        "target": target_label,
        "path": target_label,
        "source": source_identity,
        "managedFormat": managed_format,
        "markerVersion": _ADAPTER_MARKER_VERSION,
        "state": state,
        "ownership": snapshot["ownership"],
        "action": action,
        "replacesExisting": replaces_existing,
        "current": state == "owned_current",
        "bindingMatches": bool(snapshot.get("bindingMatches")),
        "bindingAllowed": bool(snapshot.get("bindingAllowed")),
        "detail": detail,
        "remediation": snapshot.get("remediation", ""),
        "warnings": list(warnings or []),
        "_targetPath": str(target),
        "_expectedContent": transaction_content,
        "_expectedTarget": target_label,
        "_expectedHost": normalized_host,
        "_expectedSource": source_identity,
        "_expectedFormat": managed_format,
        "_preflightState": state,
        "_preflightOwnership": snapshot["ownership"],
        "_originalBytes": snapshot.get("_originalBytes"),
        "_originalHash": snapshot.get("_originalHash", ""),
        "_originalMode": snapshot.get("_originalMode"),
        "_originalObjectKey": snapshot.get("_originalObjectKey"),
        "_originalState": snapshot.get("_originalState", "unreadable"),
        "_parentSnapshots": snapshot.get("_parentSnapshots", []),
    }


def _adapter_public_value(value):
    if isinstance(value, dict):
        return {
            key: _adapter_public_value(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [_adapter_public_value(item) for item in value]
    return value


def _emit_adapter_preview(payload: dict, json_output: bool = False) -> None:
    public_payload = _adapter_public_value(payload)
    if json_output:
        print(json.dumps({"adapterPreview": public_payload}, ensure_ascii=False), file=sys.stderr)
        sys.stderr.flush()
        return
    print("Adapter preview")
    for entry in payload.get("entries", []):
        print(f"  Target: {entry.get('target') or '(none)'}")
        print(f"  Ownership: {entry.get('ownership')}")
        print(f"  State: {entry.get('state')}")
        print(f"  Action: {entry.get('action')}")
        print(f"  Replaces existing: {'yes' if entry.get('replacesExisting') else 'no'}")
        if entry.get("source"):
            print(f"  Source: {entry['source']}")
        if entry.get("managedFormat"):
            print(f"  Managed format: {entry['managedFormat']}")
        if entry.get("detail"):
            print(f"  Detail: {entry['detail']}")
        if entry.get("remediation"):
            print(f"  Remediation: {entry['remediation']}")
        for warning_message in entry.get("warnings", []):
            print(f"  Warning: source section '{warning_message}' is missing")
    for entry in payload.get("additionalWrites", []):
        print(f"  Target: {entry.get('target')}")
        print(f"  Ownership: {entry.get('ownership', 'not_applicable')}")
        print(f"  State: {entry.get('state')}")
        print(f"  Action: {entry.get('action')}")
        print(f"  Replaces existing: {'yes' if entry.get('replacesExisting') else 'no'}")
        if entry.get("detail"):
            print(f"  Detail: {entry['detail']}")
        for warning_message in entry.get("warnings", []):
            print(f"  Warning: {warning_message}")
    for error in payload.get("errors", []):
        print(f"  Error: {error}")
    print(f"Preflight: {'ready' if payload.get('preflightOk') else 'blocked'}")
    sys.stdout.flush()


def _text_target_snapshot(project: Path, target: Path) -> dict:
    target_label = _project_relative_label(project, target).replace("\\", "/")
    path_issue = _managed_path_issue(project, target)
    if path_issue:
        return {
            "target": target_label,
            "state": "ambiguous",
            "detail": path_issue,
            "_originalBytes": None,
            "_originalHash": "",
            "_originalMode": None,
            "_originalObjectKey": None,
            "_originalState": "unreadable",
            "_parentSnapshots": [],
        }
    try:
        raw_snapshot = _identity_safe_file_snapshot(
            project,
            target,
            "transaction target",
        )
        if raw_snapshot["state"] == "absent":
            return {
                "target": target_label,
                "state": "missing",
                "detail": "target does not exist",
                "_originalBytes": None,
                "_originalHash": "missing",
                "_originalMode": None,
                "_originalObjectKey": None,
                "_originalState": "absent",
                "_parentSnapshots": raw_snapshot["parents"],
            }
        original_bytes = raw_snapshot["bytes"]
        original_mode = raw_snapshot["mode"]
    except (OSError, UnicodeError) as exc:
        ambiguous = "not a safe regular file" in str(exc)
        return {
            "target": target_label,
            "state": "ambiguous" if ambiguous else "unreadable",
            "detail": str(exc),
            "_originalBytes": None,
            "_originalHash": "",
            "_originalMode": None,
            "_originalObjectKey": None,
            "_originalState": "unreadable",
            "_parentSnapshots": [],
        }
    return {
        "target": target_label,
        "state": "existing",
        "detail": "target exists",
        "_originalBytes": original_bytes,
        "_originalHash": hashlib.sha256(original_bytes).hexdigest(),
        "_originalMode": original_mode,
        "_originalObjectKey": raw_snapshot["objectKey"],
        "_originalState": "present",
        "_parentSnapshots": raw_snapshot["parents"],
    }


def _text_write_entry(project: Path,
                      target: Path,
                      content: str,
                      kind: str = "supporting",
                      snapshot_override: dict | None = None) -> dict:
    snapshot = dict(snapshot_override) if snapshot_override is not None else _text_target_snapshot(project, target)
    target_label = _project_relative_label(project, target).replace("\\", "/")
    snapshot_state = str(snapshot.get("state", "unreadable"))
    original_bytes = snapshot.get("_originalBytes")
    if snapshot_state in {"ambiguous", "unreadable"}:
        action = "block"
        state = snapshot_state
    elif snapshot_state == "missing":
        action = "create"
        state = "missing"
    else:
        state = "existing"
        action = "noop" if original_bytes == content.encode("utf-8") else "update"
    return {
        "kind": kind,
        "target": target_label,
        "ownership": "not_applicable",
        "state": state,
        "action": action,
        "replacesExisting": action == "update",
        "detail": (
            snapshot.get("detail", "supporting target preflight failed")
            if action == "block"
            else "supporting file is unchanged"
            if action == "noop"
            else "supporting file will be written"
        ),
        "_targetPath": str(target),
        "_expectedContent": content,
        "_originalBytes": original_bytes,
        "_originalHash": snapshot.get("_originalHash", ""),
        "_originalMode": snapshot.get("_originalMode"),
        "_originalObjectKey": snapshot.get("_originalObjectKey"),
        "_originalState": snapshot.get("_originalState", "unreadable"),
        "_parentSnapshots": snapshot.get("_parentSnapshots", []),
    }


def _adapter_registry_entry_issues(project: Path, host: str, spec) -> list[str]:
    issues: list[str] = []
    if host in _HOST_CONTEXT_NOT_APPLICABLE:
        return issues
    if not isinstance(spec, dict):
        return [f"adapter registry entry '{host}' is missing or invalid"]
    if spec.get("managed") is False:
        issues.append(f"adapter registry entry '{host}' is not managed")
    target_label, target_error = _normalize_adapter_marker_path(
        str(spec.get("relative_path", ""))
    )
    if target_error:
        issues.append(f"adapter registry entry '{host}' target {target_error}")
    elif _managed_path_issue(project, project / target_label):
        issues.append(f"adapter registry entry '{host}' target is unsafe")
    export_mode = str(spec.get("export_mode", ""))
    if export_mode not in {"full_copy", "section_export"}:
        issues.append(f"adapter registry entry '{host}' has an unsupported export mode")
    elif export_mode == "section_export" and not isinstance(
        spec.get("include_sections"),
        (list, tuple),
    ):
        issues.append(f"adapter registry entry '{host}' has invalid included sections")
    if not str(spec.get("file_label", "")).strip() or not str(spec.get("host_label", "")).strip():
        issues.append(f"adapter registry entry '{host}' has incomplete labels")
    if _adapter_managed_format(spec) not in _ADAPTER_MANAGED_FORMATS:
        issues.append(f"adapter registry entry '{host}' is not a managed adapter format")
    return issues


def _adapter_registry_issues(project: Path, hosts: list[str]) -> list[str]:
    issues: list[str] = []
    for host in hosts:
        issues.extend(
            _adapter_registry_entry_issues(
                project,
                host,
                _HOST_CONTEXT_EXPORTS.get(host),
            )
        )
    return issues


def _planned_transaction_path(project: Path,
                              target: Path,
                              suffix: str,
                              occupied: set[str]) -> Path:
    for _attempt in range(64):
        candidate = target.parent / (
            f".{target.name}.controlcoding.{secrets.token_hex(12)}{suffix}"
        )
        candidate_key = _transaction_path_key(candidate)
        if candidate_key in occupied:
            continue
        path_issue = _managed_path_issue(project, candidate)
        if path_issue:
            raise RuntimeError(f"planned transaction path is unsafe: {path_issue}")
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            occupied.add(candidate_key)
            return candidate
        except OSError as exc:
            raise RuntimeError(
                f"planned transaction path is unreadable: {candidate}: {exc}"
            ) from exc
    raise RuntimeError(f"could not plan a unique transaction path for {target}")


def _plan_transaction_action_set(project: Path, entries: list[dict]) -> list[str]:
    """Expand targets into one collision-checked physical action set."""
    issues: list[str] = []
    target_entries: list[dict] = [entry for entry in entries if entry.get("_targetPath")]
    occupied: set[str] = set()
    target_by_key: dict[str, dict] = {}
    object_roles: dict[tuple[int, int, int], str] = {}

    for entry in target_entries:
        target = Path(entry["_targetPath"])
        target_key = _transaction_path_key(target)
        if target_key in target_by_key:
            issues.append(f"transaction contains duplicate target: {entry.get('target', target)}")
        else:
            target_by_key[target_key] = entry
        occupied.add(target_key)
        original_key = entry.get("_originalObjectKey")
        if original_key is not None:
            object_key = tuple(original_key)
            previous_role = object_roles.get(object_key)
            if previous_role is not None and previous_role != str(target):
                issues.append(
                    f"selected targets are hardlink aliases: {previous_role} and {target}"
                )
            object_roles[object_key] = str(target)

    for entry in target_entries:
        source_path_value = str(entry.get("_sourcePath", ""))
        source_snapshot = entry.get("_sourceSnapshot")
        if not source_path_value or not isinstance(source_snapshot, dict):
            continue
        source_key = _transaction_path_key(Path(source_path_value))
        target_key = _transaction_path_key(Path(entry["_targetPath"]))
        if source_key == target_key:
            issues.append(f"adapter source-target alias: {entry.get('target')}")
        source_object = source_snapshot.get("objectKey")
        if source_object is not None:
            aliased_target = object_roles.get(tuple(source_object))
            if (
                aliased_target is not None
                and _transaction_path_key(Path(aliased_target)) != source_key
            ):
                issues.append(
                    f"adapter source and selected target identify the same filesystem file through a hardlink alias: {source_path_value} and {aliased_target}"
                )

    candidates = [
        entry
        for entry in target_entries
        if entry.get("action") in {"create", "update", "replace", "adopt"}
    ]
    for entry in candidates:
        try:
            target = Path(entry["_targetPath"])
            stage_path_value = str(entry.get("_stagePath", ""))
            stage_path = (
                Path(stage_path_value)
                if stage_path_value
                else _planned_transaction_path(project, target, ".stage", occupied)
            )
            if stage_path_value:
                stage_issue = _managed_path_issue(project, stage_path)
                if stage_issue:
                    raise RuntimeError(f"planned stage path is unsafe: {stage_issue}")
                try:
                    os.lstat(stage_path)
                except FileNotFoundError:
                    occupied.add(_transaction_path_key(stage_path))
                else:
                    raise RuntimeError(f"planned stage path is no longer absent: {stage_path}")
            entry["_stagePath"] = str(stage_path)
            backup_path: Path | None = None
            if entry.get("_originalBytes") is not None:
                backup_path_value = str(entry.get("_backupPath", ""))
                backup_path = (
                    Path(backup_path_value)
                    if backup_path_value
                    else _planned_transaction_path(project, target, ".backup", occupied)
                )
                if backup_path_value:
                    backup_issue = _managed_path_issue(project, backup_path)
                    if backup_issue:
                        raise RuntimeError(f"planned backup path is unsafe: {backup_issue}")
                    try:
                        os.lstat(backup_path)
                    except FileNotFoundError:
                        occupied.add(_transaction_path_key(backup_path))
                    else:
                        raise RuntimeError(f"planned backup path is no longer absent: {backup_path}")
                entry["_backupPath"] = str(backup_path)
            directory_actions = [
                {
                    "role": "directory",
                    "path": str(parent["path"]),
                    "expectedState": "absent",
                }
                for parent in entry.get("_parentSnapshots", [])
                if parent.get("state") == "absent"
            ]
            entry["_actionSet"] = [
                {
                    "role": "payload_marker" if entry.get("kind") == "adapter" else str(entry.get("kind", "payload")),
                    "path": str(target),
                    "markerEmbedded": entry.get("kind") == "adapter",
                },
                {"role": "stage", "path": str(stage_path)},
                *([{"role": "backup", "path": str(backup_path)}] if backup_path else []),
                *directory_actions,
            ]
        except (OSError, RuntimeError) as exc:
            issues.append(str(exc))

    action_paths: dict[str, str] = {}
    for entry in candidates:
        for action in entry.get("_actionSet", []):
            if action.get("role") == "directory":
                continue
            action_path = str(action.get("path", ""))
            action_key = _transaction_path_key(Path(action_path))
            role = f"{entry.get('target')}:{action.get('role')}"
            previous = action_paths.get(action_key)
            if previous is not None and previous != role:
                issues.append(f"transaction action path collision: {previous} and {role}")
            action_paths[action_key] = role
    return issues


def _transaction_provisional_record(path: Path,
                                    role: str,
                                    parent_snapshots: list[dict] | None = None) -> dict:
    """Create an internal lifecycle record before an exclusive filesystem create."""
    return {
        "path": str(path),
        "role": role,
        "creationState": "planned",
        "identityState": "unavailable",
        "cleanupState": "not_needed",
        "cleanupError": "",
        "identity": None,
        "objectKey": None,
        "mode": None,
        "parents": [dict(item) for item in (parent_snapshots or [])],
    }


def _record_transaction_cleanup(record: dict,
                                state: str,
                                reason: str = "") -> str:
    """Persist a cleanup outcome and return its path-qualified error, if any."""
    message = f"{record.get('path', '')}: {reason}" if reason else ""
    record["cleanupState"] = state
    record["cleanupError"] = message
    return message


def _transaction_record_object_key(record: dict) -> tuple[int, ...] | None:
    if record.get("identityState") == "unavailable":
        return None
    raw_key = record.get("objectKey") or record.get("identity")
    return tuple(raw_key) if raw_key else None


def _remove_empty_transaction_dirs(project: Path, directory_records: list[dict]) -> list[str]:
    cleanup_errors: list[str] = []
    for directory_record in reversed(directory_records):
        path = Path(str(directory_record.get("path", "")))
        creation_state = str(directory_record.get("creationState", "created"))
        if creation_state in {"planned", "not_created"}:
            _record_transaction_cleanup(directory_record, "not_needed")
            continue
        cleanup_state = str(directory_record.get("cleanupState", "pending"))
        if cleanup_state == "succeeded":
            continue
        if cleanup_state in {"failed", "preserved"}:
            cleanup_errors.append(
                str(directory_record.get("cleanupError"))
                or f"{path}: prior directory cleanup was incomplete"
            )
            continue
        parent_snapshots = directory_record.get("parents")
        if not isinstance(parent_snapshots, list) or not parent_snapshots:
            cleanup_errors.append(_record_transaction_cleanup(
                directory_record,
                "preserved",
                "created directory parent provenance is unavailable; directory was preserved",
            ))
            continue
        expected_object = _transaction_record_object_key(directory_record)
        if expected_object is None:
            cleanup_errors.append(_record_transaction_cleanup(
                directory_record,
                "preserved",
                "created directory identity is unavailable; directory was preserved",
            ))
            continue
        try:
            path_issue = _managed_path_issue(project, path)
            if path_issue:
                raise RuntimeError(f"created directory path is unsafe: {path_issue}")
            _revalidate_transaction_parents(project, path, parent_snapshots)
            path_stat = os.lstat(path)
            if (
                stat.S_ISLNK(path_stat.st_mode)
                or _filesystem_is_reparse(path_stat)
                or not stat.S_ISDIR(path_stat.st_mode)
                or _transaction_file_object_key(path_stat)
                != expected_object
            ):
                raise RuntimeError(
                    "created directory identity changed; concurrent directory was preserved"
                )
            _transaction_checkpoint(
                "before_directory_cleanup",
                path=path,
                record=directory_record,
            )
            path_issue = _managed_path_issue(project, path)
            if path_issue:
                raise RuntimeError(
                    f"created directory path became unsafe before cleanup: {path_issue}"
                )
            _revalidate_transaction_parents(project, path, parent_snapshots)
            immediate_stat = os.lstat(path)
            if (
                stat.S_ISLNK(immediate_stat.st_mode)
                or _filesystem_is_reparse(immediate_stat)
                or not stat.S_ISDIR(immediate_stat.st_mode)
                or _transaction_file_object_key(immediate_stat) != expected_object
            ):
                raise RuntimeError(
                    "created directory identity changed before cleanup; concurrent directory was preserved"
                )
            path.rmdir()
            _record_transaction_cleanup(directory_record, "succeeded")
        except FileNotFoundError:
            _record_transaction_cleanup(directory_record, "succeeded")
            continue
        except RuntimeError as exc:
            cleanup_errors.append(_record_transaction_cleanup(
                directory_record,
                "preserved",
                str(exc),
            ))
        except OSError as exc:
            cleanup_errors.append(_record_transaction_cleanup(
                directory_record,
                "failed",
                str(exc),
            ))
    return cleanup_errors


def _transaction_stat_is_safe_regular(file_stat: os.stat_result) -> bool:
    file_attributes = int(getattr(file_stat, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not (reparse_flag and file_attributes & reparse_flag)
    )


def _cleanup_reserved_transaction_path(temporary_path: Path,
                                       expected_object: tuple[int, ...] | None,
                                       *,
                                       project: Path | None = None,
                                       parent_snapshots: list[dict] | None = None) -> str:
    if expected_object is None:
        return "reserved file identity is unavailable; temporary path was preserved"
    try:
        if project is not None:
            path_issue = _managed_path_issue(project, temporary_path)
            if path_issue:
                raise RuntimeError(
                    f"reserved temporary path is unsafe: {path_issue}"
                )
        if project is not None and parent_snapshots is not None:
            _revalidate_transaction_parents(
                project,
                temporary_path,
                parent_snapshots,
            )
        path_stat = os.lstat(temporary_path)
    except FileNotFoundError:
        return ""
    except (OSError, RuntimeError) as exc:
        return f"could not inspect reserved temporary path: {exc}; temporary path was preserved"
    if (
        not _transaction_stat_is_safe_regular(path_stat)
        or _transaction_file_object_key(path_stat) != expected_object
    ):
        return "reserved temporary path identity changed; concurrent path was preserved"

    try:
        _transaction_checkpoint("before_reserved_cleanup", path=temporary_path)
        _transaction_checkpoint("before_cleanup", path=temporary_path)
        if project is not None and parent_snapshots is not None:
            _revalidate_transaction_parents(
                project,
                temporary_path,
                parent_snapshots,
            )
        immediate_stat = os.lstat(temporary_path)
        if (
            not _transaction_stat_is_safe_regular(immediate_stat)
            or _transaction_file_object_key(immediate_stat) != expected_object
        ):
            raise RuntimeError(
                "reserved temporary path identity changed before cleanup; concurrent path was preserved"
            )
        temporary_path.unlink()
        return ""
    except (OSError, RuntimeError) as exc:
        try:
            if project is not None and parent_snapshots is not None:
                _revalidate_transaction_parents(
                    project,
                    temporary_path,
                    parent_snapshots,
                )
            retry_stat = os.lstat(temporary_path)
            if (
                not _transaction_stat_is_safe_regular(retry_stat)
                or _transaction_file_object_key(retry_stat) != expected_object
            ):
                raise RuntimeError(
                    "reserved temporary path identity changed; concurrent path was preserved"
                )
            _transaction_checkpoint(
                "before_cleanup_chmod",
                path=temporary_path,
                expectedObject=expected_object,
            )
            if project is not None and parent_snapshots is not None:
                _revalidate_transaction_parents(
                    project,
                    temporary_path,
                    parent_snapshots,
                )
            immediate_chmod_stat = os.lstat(temporary_path)
            if (
                not _transaction_stat_is_safe_regular(immediate_chmod_stat)
                or _transaction_file_object_key(immediate_chmod_stat) != expected_object
            ):
                raise RuntimeError(
                    "reserved temporary path identity changed immediately before cleanup chmod; "
                    "concurrent path was preserved"
                )
            os.chmod(temporary_path, 0o600)
            chmod_stat = os.lstat(temporary_path)
            if (
                not _transaction_stat_is_safe_regular(chmod_stat)
                or _transaction_file_object_key(chmod_stat) != expected_object
            ):
                raise RuntimeError(
                    "reserved temporary path identity changed during cleanup chmod; "
                    "concurrent path was preserved"
                )
            _transaction_checkpoint(
                "before_reserved_cleanup_retry_unlink",
                path=temporary_path,
                expectedObject=expected_object,
            )
            if project is not None and parent_snapshots is not None:
                _revalidate_transaction_parents(
                    project,
                    temporary_path,
                    parent_snapshots,
                )
            immediate_unlink_stat = os.lstat(temporary_path)
            if (
                not _transaction_stat_is_safe_regular(immediate_unlink_stat)
                or _transaction_file_object_key(immediate_unlink_stat) != expected_object
            ):
                raise RuntimeError(
                    "reserved temporary path identity changed before cleanup retry; "
                    "concurrent path was preserved"
                )
            temporary_path.unlink()
            return ""
        except (OSError, RuntimeError) as retry_exc:
            return f"{exc}; retry failed: {retry_exc}"


def _stage_transaction_bytes(target: Path,
                             content: bytes,
                             suffix: str,
                             mode: int | None = None,
                             *,
                             planned_path: Path | None = None,
                             project: Path | None = None,
                             parent_snapshots: list[dict] | None = None,
                             lifecycle_record: dict | None = None) -> dict:
    descriptor = -1
    temporary_path: Path | None = None
    reserved_object: tuple[int, ...] | None = None
    temporary_record = lifecycle_record or _transaction_provisional_record(
        planned_path or target,
        suffix.lstrip(".") or "stage",
        parent_snapshots,
    )
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    open_flags |= int(getattr(os, "O_BINARY", 0) or 0)
    creation_mode = 0o666 if mode is None else 0o600
    candidates = (
        [planned_path]
        if planned_path is not None
        else [
            target.parent / (
                f".{target.name}.controlcoding.{secrets.token_hex(12)}{suffix}"
            )
            for _attempt in range(64)
        ]
    )
    for candidate in candidates:
        if candidate is None:
            continue
        temporary_record.update({
            "path": str(candidate),
            "creationState": "planned",
            "identityState": "unavailable",
            "cleanupState": "not_needed",
            "cleanupError": "",
            "identity": None,
            "objectKey": None,
            "mode": None,
        })
        try:
            if project is not None and parent_snapshots is not None:
                _revalidate_transaction_parents(project, candidate, parent_snapshots)
            _transaction_checkpoint(
                "before_stage_create",
                path=candidate,
                target=target,
                suffix=suffix,
            )
            if project is not None and parent_snapshots is not None:
                _revalidate_transaction_parents(project, candidate, parent_snapshots)
            descriptor = os.open(candidate, open_flags, creation_mode)
        except FileExistsError:
            temporary_record["creationState"] = "not_created"
            if planned_path is not None:
                raise
            continue
        except Exception:
            temporary_record["creationState"] = "not_created"
            raise
        temporary_path = candidate
        temporary_record.update({
            "creationState": "created",
            "cleanupState": "pending",
        })
        break
    if temporary_path is None:
        temporary_record["creationState"] = "not_created"
        raise FileExistsError(f"could not reserve a unique staged file for {target}")
    try:
        _transaction_checkpoint(
            "after_stage_create",
            path=temporary_path,
            target=target,
            suffix=suffix,
            record=temporary_record,
        )
        reserved_stat = os.fstat(descriptor)
        if not _transaction_stat_is_safe_regular(reserved_stat):
            raise OSError(f"reserved stage is not a regular file: {temporary_path}")
        reserved_object = _transaction_file_object_key(reserved_stat)
        temporary_record.update({
            "identityState": "known",
            "identity": reserved_object,
            "objectKey": reserved_object,
            "mode": stat.S_IMODE(reserved_stat.st_mode),
        })
        _transaction_checkpoint(
            "after_stage_identity",
            path=temporary_path,
            target=target,
            suffix=suffix,
            record=temporary_record,
        )
        content_view = memoryview(content)
        written = 0
        while written < len(content_view):
            write_count = os.write(descriptor, content_view[written:])
            if write_count <= 0:
                raise OSError(f"could not complete staged write for {target}")
            written += write_count
        os.fsync(descriptor)
        if mode is not None:
            fchmod = getattr(os, "fchmod", None)
            if callable(fchmod):
                fchmod(descriptor, mode)
            else:
                _transaction_checkpoint(
                    "before_stage_chmod",
                    path=temporary_path,
                    target=target,
                    suffix=suffix,
                    record=temporary_record,
                )
                if project is not None and parent_snapshots is not None:
                    _revalidate_transaction_parents(
                        project,
                        temporary_path,
                        parent_snapshots,
                    )
                created_stat = os.fstat(descriptor)
                path_stat = os.lstat(temporary_path)
                if (
                    not _transaction_stat_is_safe_regular(created_stat)
                    or not _transaction_stat_is_safe_regular(path_stat)
                    or _transaction_file_object_key(created_stat) != reserved_object
                    or _transaction_file_object_key(path_stat) != reserved_object
                ):
                    raise OSError(f"staged file identity changed before chmod: {temporary_path}")
                os.chmod(temporary_path, mode)
                chmod_descriptor_stat = os.fstat(descriptor)
                chmod_path_stat = os.lstat(temporary_path)
                if (
                    not _transaction_stat_is_safe_regular(chmod_descriptor_stat)
                    or not _transaction_stat_is_safe_regular(chmod_path_stat)
                    or _transaction_file_object_key(chmod_descriptor_stat) != reserved_object
                    or _transaction_file_object_key(chmod_path_stat) != reserved_object
                ):
                    raise OSError(f"staged file identity changed during chmod: {temporary_path}")

        descriptor_stat = os.fstat(descriptor)
        path_stat = os.lstat(temporary_path)
        if (
            not _transaction_stat_is_safe_regular(descriptor_stat)
            or not _transaction_stat_is_safe_regular(path_stat)
        ):
            raise OSError(f"staged path is not a regular file: {temporary_path}")
        created_object_key = _transaction_file_object_key(descriptor_stat)
        if created_object_key != _transaction_file_object_key(path_stat):
            raise OSError(f"staged file identity changed during creation: {temporary_path}")
        os.close(descriptor)
        descriptor = -1
        _transaction_checkpoint(
            "after_stage_close",
            path=temporary_path,
            target=target,
            suffix=suffix,
            record=temporary_record,
        )
        path_stat = os.lstat(temporary_path)
        if (
            not _transaction_stat_is_safe_regular(path_stat)
            or _transaction_file_object_key(path_stat) != created_object_key
        ):
            raise OSError(f"staged file identity changed after creation: {temporary_path}")
        temporary_record.update({
            "contentHash": hashlib.sha256(content).hexdigest(),
            "mode": stat.S_IMODE(path_stat.st_mode),
            "parents": [dict(item) for item in (parent_snapshots or [])],
        })
        if project is not None and parent_snapshots is not None:
            _revalidate_transaction_parents(project, temporary_path, parent_snapshots)
            _validate_transaction_temporary(
                project,
                temporary_record,
                content,
                f"prepared transaction {suffix.lstrip('.')}",
            )
    except Exception as exc:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        cleanup_error = _cleanup_reserved_transaction_path(
            temporary_path,
            reserved_object,
            project=project,
            parent_snapshots=parent_snapshots,
        )
        if cleanup_error:
            cleanup_state = "preserved" if "preserved" in cleanup_error else "failed"
            _record_transaction_cleanup(
                temporary_record,
                cleanup_state,
                cleanup_error,
            )
            raise
        _record_transaction_cleanup(temporary_record, "succeeded")
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return temporary_record


def _transaction_file_object_key(file_stat: os.stat_result) -> tuple[int, ...]:
    return _filesystem_object_key(file_stat)


def _validate_transaction_temporary(project: Path,
                                    temporary_record: dict,
                                    expected_content: bytes,
                                    purpose: str) -> Path:
    temporary_path = Path(str(temporary_record.get("path", "")))
    path_issue = _managed_path_issue(project, temporary_path)
    if path_issue:
        raise RuntimeError(f"{purpose} path is unsafe: {path_issue}")
    parent_snapshots = temporary_record.get("parents", [])
    if parent_snapshots:
        _revalidate_transaction_parents(
            project,
            temporary_path,
            parent_snapshots,
        )

    descriptor = -1
    try:
        path_stat = os.lstat(temporary_path)
        if not _transaction_stat_is_safe_regular(path_stat):
            raise OSError(f"{purpose} is not a regular file: {temporary_path}")
        open_flags = os.O_RDONLY
        open_flags |= int(getattr(os, "O_BINARY", 0) or 0)
        open_flags |= int(getattr(os, "O_NOFOLLOW", 0) or 0)
        descriptor = os.open(temporary_path, open_flags)
        before_stat = os.fstat(descriptor)
        if not _transaction_stat_is_safe_regular(before_stat):
            raise OSError(f"{purpose} opened object is not a regular file: {temporary_path}")
        expected_identity = tuple(
            temporary_record.get("objectKey")
            or temporary_record.get("identity", ())
        )
        before_identity = _transaction_file_object_key(before_stat)
        if (
            before_identity != expected_identity
            or _transaction_file_object_key(path_stat) != expected_identity
        ):
            raise RuntimeError(f"{purpose} identity changed after creation: {temporary_path}")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_stat = os.fstat(descriptor)
        after_path_stat = os.lstat(temporary_path)
        if (
            _transaction_file_object_key(after_stat) != before_identity
            or _transaction_file_object_key(after_path_stat) != before_identity
        ):
            raise RuntimeError(f"{purpose} identity changed while it was being validated: {temporary_path}")
        current_content = b"".join(chunks)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{purpose} disappeared before replacement: {temporary_path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    expected_hash = hashlib.sha256(expected_content).hexdigest()
    if str(temporary_record.get("contentHash", "")) != expected_hash:
        raise RuntimeError(f"{purpose} record does not match planned content: {temporary_path}")
    if hashlib.sha256(current_content).hexdigest() != expected_hash:
        raise RuntimeError(f"{purpose} content changed after creation: {temporary_path}")
    if stat.S_IMODE(after_stat.st_mode) != temporary_record.get("mode"):
        raise RuntimeError(f"{purpose} mode changed after creation: {temporary_path}")
    if parent_snapshots:
        _revalidate_transaction_parents(
            project,
            temporary_path,
            parent_snapshots,
        )
    return temporary_path


def _validate_transaction_staging_set(project: Path,
                                      candidates: list[dict],
                                      staged: dict[str, dict],
                                      backups: dict[str, dict]) -> None:
    for entry in candidates:
        staged_record = staged.get(entry["target"])
        if staged_record is not None:
            _validate_transaction_temporary(
                project,
                staged_record,
                entry["_expectedContent"].encode("utf-8"),
                f"transaction stage for {entry['target']}",
            )
        backup_record = backups.get(entry["target"])
        if backup_record is not None:
            original_bytes = entry.get("_originalBytes")
            if original_bytes is None:
                raise RuntimeError(f"rollback backup has no original content: {entry['target']}")
            _validate_transaction_temporary(
                project,
                backup_record,
                original_bytes,
                f"rollback backup for {entry['target']}",
            )


def _transaction_entry_snapshot(project: Path,
                                entry: dict,
                                committed: bool = False) -> dict:
    target = Path(entry["_targetPath"])
    if entry.get("kind") == "adapter":
        snapshot = _adapter_target_snapshot(
            project,
            target,
            entry["_expectedContent"],
            entry["_expectedTarget"],
            entry["_expectedHost"],
            entry["_expectedSource"],
            entry["_expectedFormat"],
        )
    else:
        snapshot = _text_target_snapshot(project, target)
    return snapshot


def _validate_transaction_adapter_snapshot(entry: dict,
                                           snapshot: dict,
                                           committed: bool = False) -> None:
    if entry.get("kind") != "adapter":
        return
    state = str(snapshot.get("state", "unreadable"))
    ownership = str(snapshot.get("ownership", "unreadable"))
    if committed:
        if (
            state != "owned_current"
            or ownership != "owned"
            or not snapshot.get("bindingAllowed")
            or not snapshot.get("bindingMatches")
        ):
            raise RuntimeError(
                f"committed adapter ownership is not current and fully bound: {entry['target']}"
            )
        return

    expected_state = str(entry.get("_preflightState", entry.get("state", "")))
    expected_ownership = str(
        entry.get("_preflightOwnership", entry.get("ownership", ""))
    )
    if state != expected_state or ownership != expected_ownership:
        raise RuntimeError(f"adapter ownership changed after preview: {entry['target']}")
    action = str(entry.get("action", ""))
    if action == "adopt":
        if state not in {"unmarked", "foreign"}:
            raise RuntimeError(f"adapter is no longer eligible for adoption: {entry['target']}")
    elif action == "create":
        if state != "target_absent" or not snapshot.get("bindingAllowed"):
            raise RuntimeError(f"adapter create target is no longer safely absent: {entry['target']}")
    elif not snapshot.get("bindingAllowed"):
        raise RuntimeError(f"adapter ownership binding is no longer allowed: {entry['target']}")


def _validate_transaction_adapter_source_target(project: Path, entry: dict) -> None:
    if entry.get("kind") != "adapter":
        return
    source_path_value = str(entry.get("_sourcePath", ""))
    target_path_value = str(entry.get("_targetPath", ""))
    if not source_path_value or not target_path_value:
        raise RuntimeError(f"adapter source-target identity is unavailable: {entry['target']}")
    alias_state, alias_detail = _adapter_proposed_source_target_issue(
        project,
        Path(source_path_value),
        Path(target_path_value),
    )
    if alias_state:
        raise RuntimeError(
            f"adapter source-target identity became unsafe for {entry['target']}: {alias_detail}"
        )


def _transaction_path_key(path: Path) -> str:
    return os.path.normcase(str(path.absolute()))


def _transaction_source_snapshot(project: Path,
                                 source_path: Path,
                                 expected: dict | None = None) -> dict:
    source_issue = _managed_path_issue(project, source_path)
    if source_issue:
        raise OSError(f"adapter source path is unsafe: {source_issue}")
    expected_parents = expected.get("parents", []) if isinstance(expected, dict) else None
    return _identity_safe_file_snapshot(
        project,
        source_path,
        "adapter source",
        expected_parents=expected_parents,
    )


def _revalidate_transaction_entry(project: Path,
                                  entry: dict,
                                  target_entries: dict[str, dict],
                                  committed_targets: set[str]) -> None:
    current_snapshot = _transaction_entry_snapshot(project, entry)
    _validate_transaction_adapter_snapshot(entry, current_snapshot)
    _validate_transaction_adapter_source_target(project, entry)
    expected_target = _private_snapshot_record(entry)
    current_target = _private_snapshot_record(current_snapshot)
    _revalidate_transaction_parents(
        project,
        Path(entry["_targetPath"]),
        entry.get("_parentSnapshots", []),
    )
    if not _transaction_snapshots_match(expected_target, current_target):
        raise RuntimeError(f"target identity or state changed after preview: {entry['target']}")

    source_path_value = str(entry.get("_sourcePath", ""))
    source_snapshot = entry.get("_sourceSnapshot")
    if not source_path_value or not isinstance(source_snapshot, dict):
        return
    source_path = Path(source_path_value)
    source_key = _transaction_path_key(source_path)
    source_entry = target_entries.get(source_key)
    expected_source = source_snapshot
    if source_entry is not None:
        if source_key in committed_targets:
            expected_source = {
                "state": "present",
                "bytes": source_entry["_expectedContent"].encode("utf-8"),
                "hash": hashlib.sha256(
                    source_entry["_expectedContent"].encode("utf-8")
                ).hexdigest(),
                "mode": source_entry.get("_writtenMode"),
                "objectKey": source_entry.get("_installedObjectKey"),
                "parents": source_entry.get("_parentSnapshots", []),
            }
        else:
            expected_source = _private_snapshot_record(source_entry)
    current_source = _transaction_source_snapshot(
        project,
        source_path,
        expected=expected_source,
    )
    if not _transaction_snapshots_match(expected_source, current_source):
        raise RuntimeError(
            f"adapter source identity or state changed after preview: {entry.get('source', source_path_value)}"
        )


def _cleanup_transaction_paths(project: Path, temporary_records: list[dict]) -> list[str]:
    cleanup_errors: list[str] = []
    for temporary_record in temporary_records:
        temporary_path = Path(str(temporary_record.get("path", "")))
        creation_state = str(temporary_record.get("creationState", "created"))
        if creation_state in {"planned", "not_created"}:
            _record_transaction_cleanup(temporary_record, "not_needed")
            continue
        cleanup_state = str(temporary_record.get("cleanupState", "pending"))
        if cleanup_state == "succeeded":
            continue
        if cleanup_state in {"failed", "preserved"}:
            cleanup_errors.append(
                str(temporary_record.get("cleanupError"))
                or f"{temporary_path}: prior temporary-file cleanup was incomplete"
            )
            continue
        expected_object = _transaction_record_object_key(temporary_record)
        if expected_object is None:
            cleanup_errors.append(_record_transaction_cleanup(
                temporary_record,
                "preserved",
                "temporary file identity is unavailable; path was preserved",
            ))
            continue
        cleanup_error = _cleanup_reserved_transaction_path(
            temporary_path,
            expected_object,
            project=project,
            parent_snapshots=temporary_record.get("parents", []),
        )
        if cleanup_error:
            cleanup_errors.append(_record_transaction_cleanup(
                temporary_record,
                "preserved" if "preserved" in cleanup_error else "failed",
                cleanup_error,
            ))
        else:
            _record_transaction_cleanup(temporary_record, "succeeded")
    return cleanup_errors


def _create_transaction_parent_dirs(project: Path,
                                    entry: dict,
                                    target_entries: dict[str, dict],
                                    committed_targets: set[str],
                                    created_dirs: list[dict]) -> None:
    missing_dirs = [
        Path(parent["path"])
        for parent in entry.get("_parentSnapshots", [])
        if parent.get("state") == "absent"
    ]
    for path in missing_dirs:
        parent_snapshots = _snapshot_transaction_parents(project, path)
        if not parent_snapshots:
            raise RuntimeError(
                f"transaction parent provenance is unavailable before creation: {path}"
            )
        parent_record = _transaction_provisional_record(
            path,
            "directory",
            parent_snapshots,
        )
        created_dirs.append(parent_record)
        _revalidate_transaction_entry(
            project,
            entry,
            target_entries,
            committed_targets,
        )
        _transaction_checkpoint("before_parent_create", path=path, entry=entry)
        _revalidate_transaction_entry(
            project,
            entry,
            target_entries,
            committed_targets,
        )
        try:
            _revalidate_transaction_parents(project, path, parent_snapshots)
            path.mkdir(exist_ok=False)
        except FileExistsError as exc:
            parent_record["creationState"] = "not_created"
            raise RuntimeError(
                f"transaction parent appeared before exclusive creation: {path}"
            ) from exc
        except Exception:
            parent_record["creationState"] = "not_created"
            raise
        parent_record.update({
            "creationState": "created",
            "cleanupState": "pending",
        })
        _transaction_checkpoint(
            "after_parent_create",
            path=path,
            entry=entry,
            record=parent_record,
        )
        path_issue = _managed_path_issue(project, path)
        if path_issue:
            raise RuntimeError(f"created transaction parent is unsafe: {path_issue}")
        created_stat = os.lstat(path)
        if (
            stat.S_ISLNK(created_stat.st_mode)
            or _filesystem_is_reparse(created_stat)
            or not stat.S_ISDIR(created_stat.st_mode)
        ):
            raise NotADirectoryError(f"created transaction parent is not a safe directory: {path}")
        created_key = _transaction_file_object_key(created_stat)
        parent_record.update({
            "identityState": "known",
            "identity": created_key,
            "objectKey": created_key,
            "mode": stat.S_IMODE(created_stat.st_mode),
        })
        _transaction_checkpoint(
            "after_parent_identity",
            path=path,
            entry=entry,
            record=parent_record,
        )
        for transaction_entry in target_entries.values():
            for expected_parent in transaction_entry.get("_parentSnapshots", []):
                if Path(str(expected_parent.get("path", ""))) == path:
                    expected_parent.update({
                        "state": "present",
                        "objectKey": created_key,
                        "mode": stat.S_IMODE(created_stat.st_mode),
                        "transactionOwned": True,
                    })


def _verify_completed_transaction_set(project: Path,
                                      entries: list[dict],
                                      committed_targets: set[str]) -> None:
    target_entries = {
        _transaction_path_key(Path(entry["_targetPath"])): entry
        for entry in entries
        if entry.get("_targetPath")
    }
    for entry in entries:
        target_path_value = str(entry.get("_targetPath", ""))
        if target_path_value:
            target_key = _transaction_path_key(Path(target_path_value))
            committed = target_key in committed_targets
            current_snapshot = _transaction_entry_snapshot(
                project,
                entry,
                committed=committed,
            )
            _validate_transaction_adapter_snapshot(
                entry,
                current_snapshot,
                committed=committed,
            )
            _validate_transaction_adapter_source_target(project, entry)
            if committed:
                expected_target = {
                    "state": "present",
                    "bytes": entry["_expectedContent"].encode("utf-8"),
                    "hash": hashlib.sha256(
                        entry["_expectedContent"].encode("utf-8")
                    ).hexdigest(),
                    "mode": entry.get("_writtenMode"),
                    "objectKey": entry.get("_installedObjectKey"),
                    "parents": entry.get("_parentSnapshots", []),
                }
            else:
                expected_target = _private_snapshot_record(entry)
            if not _transaction_snapshots_match(
                expected_target,
                _private_snapshot_record(current_snapshot),
            ):
                raise RuntimeError(
                    f"transaction target drifted before completion: {entry.get('target', target_path_value)}"
                )

        if entry.get("kind") == "adapter":
            source_path_value = str(entry.get("_sourcePath", ""))
            source_snapshot = entry.get("_sourceSnapshot")
            if source_path_value and isinstance(source_snapshot, dict):
                source_path = Path(source_path_value)
                source_key = _transaction_path_key(source_path)
                source_entry = target_entries.get(source_key)
                expected_source = source_snapshot
                if source_entry is not None and source_key in committed_targets:
                    expected_source = {
                        "state": "present",
                        "bytes": source_entry["_expectedContent"].encode("utf-8"),
                        "hash": hashlib.sha256(
                            source_entry["_expectedContent"].encode("utf-8")
                        ).hexdigest(),
                        "mode": source_entry.get("_writtenMode"),
                        "objectKey": source_entry.get("_installedObjectKey"),
                        "parents": source_entry.get("_parentSnapshots", []),
                    }
                current_source = _transaction_source_snapshot(
                    project,
                    source_path,
                    expected=expected_source,
                )
                if not _transaction_snapshots_match(expected_source, current_source):
                    raise RuntimeError(
                        f"adapter source drifted before transaction completion: "
                        f"{entry.get('source', source_path_value)}"
                    )


def _apply_text_transaction(project: Path, entries: list[dict]) -> dict:
    """Stage a write set and roll it back on handled replacement errors."""
    action_issues = _plan_transaction_action_set(project, entries)
    candidates = [
        entry
        for entry in entries
        if entry.get("action") in {"create", "update", "replace", "adopt"}
    ]
    if any(entry.get("action") == "block" for entry in entries):
        return {
            "ok": False,
            "applied": False,
            "attempted": False,
            "rolledBack": False,
            "rollbackOk": True,
            "cleanupOk": True,
            "error": "preflight is blocked",
            "written": [],
        }
    if action_issues:
        return {
            "ok": False,
            "applied": False,
            "attempted": False,
            "rolledBack": False,
            "rollbackOk": True,
            "cleanupOk": True,
            "error": "; ".join(action_issues),
            "rollbackErrors": [],
            "cleanupErrors": [],
            "written": [],
        }
    if not candidates:
        try:
            _verify_completed_transaction_set(project, entries, set())
        except Exception as exc:
            return {
                "ok": False,
                "applied": False,
                "attempted": False,
                "rolledBack": False,
                "rollbackOk": True,
                "cleanupOk": True,
                "error": str(exc),
                "rollbackErrors": [],
                "cleanupErrors": [],
                "written": [],
            }
        return {
            "ok": True,
            "applied": False,
            "attempted": False,
            "rolledBack": False,
            "rollbackOk": True,
            "cleanupOk": True,
            "written": [],
        }

    target_entries: dict[str, dict] = {}
    for entry in entries:
        target_path_value = str(entry.get("_targetPath", ""))
        if not target_path_value:
            continue
        target_key = _transaction_path_key(Path(target_path_value))
        if target_key in target_entries:
            return {
                "ok": False,
                "applied": False,
                "attempted": False,
                "rolledBack": False,
                "rollbackOk": True,
                "cleanupOk": True,
                "error": f"transaction contains duplicate target: {entry.get('target', target_path_value)}",
                "written": [],
            }
        target_entries[target_key] = entry

    try:
        for entry in entries:
            if entry.get("_targetPath"):
                _revalidate_transaction_entry(
                    project,
                    entry,
                    target_entries,
                    set(),
                )
    except Exception as exc:
        return {
            "ok": False,
            "applied": False,
            "attempted": False,
            "rolledBack": False,
            "rollbackOk": True,
            "cleanupOk": True,
            "error": str(exc),
            "rollbackErrors": [],
            "cleanupErrors": [],
            "written": [],
        }

    created_dirs: list[dict] = []
    staged: dict[str, dict] = {}
    backups: dict[str, dict] = {}
    committed: list[dict] = []
    committed_targets: set[str] = set()
    rollback_errors: list[str] = []
    unrestored_targets: set[str] = set()
    cleanup_errors: list[str] = []
    try:
        for entry in candidates:
            _create_transaction_parent_dirs(
                project,
                entry,
                target_entries,
                committed_targets,
                created_dirs,
            )

        for entry in candidates:
            target = Path(entry["_targetPath"])
            _transaction_checkpoint("before_prepare_entry", entry=entry, target=target)
            _revalidate_transaction_entry(
                project,
                entry,
                target_entries,
                committed_targets,
            )
            staged_record = _transaction_provisional_record(
                Path(entry["_stagePath"]),
                "stage",
                entry.get("_parentSnapshots", []),
            )
            staged[entry["target"]] = staged_record
            _stage_transaction_bytes(
                target,
                entry["_expectedContent"].encode("utf-8"),
                ".stage",
                mode=entry.get("_originalMode"),
                planned_path=Path(entry["_stagePath"]),
                project=project,
                parent_snapshots=entry.get("_parentSnapshots", []),
                lifecycle_record=staged_record,
            )
            entry["_writtenMode"] = staged_record["mode"]
            original_bytes = entry.get("_originalBytes")
            if original_bytes is not None:
                _revalidate_transaction_entry(
                    project,
                    entry,
                    target_entries,
                    committed_targets,
                )
                backup_record = _transaction_provisional_record(
                    Path(entry["_backupPath"]),
                    "backup",
                    entry.get("_parentSnapshots", []),
                )
                backups[entry["target"]] = backup_record
                _stage_transaction_bytes(
                    target,
                    original_bytes,
                    ".backup",
                    mode=entry.get("_originalMode"),
                    planned_path=Path(entry["_backupPath"]),
                    project=project,
                    parent_snapshots=entry.get("_parentSnapshots", []),
                    lifecycle_record=backup_record,
                )

        _transaction_checkpoint(
            "global_precommit",
            entries=entries,
            staged=staged,
            backups=backups,
        )
        for entry in entries:
            if entry.get("_targetPath"):
                _revalidate_transaction_entry(
                    project,
                    entry,
                    target_entries,
                    committed_targets,
                )

        _validate_transaction_staging_set(
            project,
            candidates,
            staged,
            backups,
        )

        for entry in candidates:
            target = Path(entry["_targetPath"])
            _validate_transaction_staging_set(
                project,
                candidates,
                staged,
                backups,
            )
            _revalidate_transaction_entry(
                project,
                entry,
                target_entries,
                committed_targets,
            )
            backup_record = backups.get(entry["target"])
            if backup_record is not None:
                original_bytes = entry.get("_originalBytes")
                if original_bytes is None:
                    raise RuntimeError(f"rollback backup has no original content: {entry['target']}")
                _validate_transaction_temporary(
                    project,
                    backup_record,
                    original_bytes,
                    f"rollback backup for {entry['target']}",
                )
            staged_record = staged[entry["target"]]
            staged_path = _validate_transaction_temporary(
                project,
                staged_record,
                entry["_expectedContent"].encode("utf-8"),
                f"transaction stage for {entry['target']}",
            )
            _transaction_checkpoint(
                "before_replace",
                entry=entry,
                source=staged_path,
                destination=target,
            )
            _revalidate_transaction_entry(
                project,
                entry,
                target_entries,
                committed_targets,
            )
            _validate_transaction_temporary(
                project,
                staged_record,
                entry["_expectedContent"].encode("utf-8"),
                f"transaction stage for {entry['target']}",
            )
            os.replace(staged_path, target)
            staged.pop(entry["target"], None)
            entry["_installedObjectKey"] = staged_record["objectKey"]
            committed.append(entry)
            committed_targets.add(_transaction_path_key(target))
            _transaction_checkpoint(
                "after_replace",
                entry=entry,
                destination=target,
            )
            installed_stat = os.lstat(target)
            if (
                not _transaction_stat_is_safe_regular(installed_stat)
                or _transaction_file_object_key(installed_stat)
                != tuple(staged_record["objectKey"])
            ):
                raise RuntimeError(
                    f"installed target identity does not match its stage: {entry['target']}"
                )
            installed_snapshot = _transaction_entry_snapshot(
                project,
                entry,
                committed=True,
            )
            expected_installed = {
                "state": "present",
                "bytes": entry["_expectedContent"].encode("utf-8"),
                "hash": hashlib.sha256(
                    entry["_expectedContent"].encode("utf-8")
                ).hexdigest(),
                "mode": entry.get("_writtenMode"),
                "objectKey": entry.get("_installedObjectKey"),
                "parents": entry.get("_parentSnapshots", []),
            }
            if not _transaction_snapshots_match(
                expected_installed,
                _private_snapshot_record(installed_snapshot),
            ):
                raise RuntimeError(
                    f"installed target state does not match its stage: {entry['target']}"
                )

        _verify_completed_transaction_set(
            project,
            entries,
            committed_targets,
        )
    except Exception as exc:
        for entry in reversed(committed):
            target = Path(entry["_targetPath"])
            backup = backups.get(entry["target"])
            try:
                _transaction_checkpoint(
                    "before_rollback",
                    entry=entry,
                    target=target,
                    backup=backup,
                )
                current_snapshot = _text_target_snapshot(project, target)
                current_record = _private_snapshot_record(current_snapshot)
                original_record = _private_snapshot_record(entry)
                if _transaction_snapshots_match(original_record, current_record):
                    continue
                installed_record = {
                    "state": "present",
                    "bytes": entry["_expectedContent"].encode("utf-8"),
                    "hash": hashlib.sha256(
                        entry["_expectedContent"].encode("utf-8")
                    ).hexdigest(),
                    "mode": entry.get("_writtenMode"),
                    "objectKey": entry.get("_installedObjectKey"),
                    "parents": entry.get("_parentSnapshots", []),
                }
                if not _transaction_snapshots_match(installed_record, current_record):
                    raise RuntimeError(
                        "target no longer has the object identity, bytes, and mode written by this transaction; "
                        "concurrent object was preserved"
                    )
                _revalidate_transaction_parents(
                    project,
                    target,
                    entry.get("_parentSnapshots", []),
                )
                if backup is not None:
                    original_bytes = entry.get("_originalBytes")
                    if original_bytes is None:
                        raise RuntimeError("rollback backup has no original content")
                    backup_path = _validate_transaction_temporary(
                        project,
                        backup,
                        original_bytes,
                        f"rollback backup for {entry['target']}",
                    )
                    immediate_target = _private_snapshot_record(
                        _text_target_snapshot(project, target)
                    )
                    if not _transaction_snapshots_match(installed_record, immediate_target):
                        raise RuntimeError(
                            "target identity or state changed immediately before rollback replace"
                        )
                    _validate_transaction_temporary(
                        project,
                        backup,
                        original_bytes,
                        f"rollback backup for {entry['target']}",
                    )
                    os.replace(backup_path, target)
                    backups.pop(entry["target"], None)
                    entry["_rollbackObjectKey"] = backup["objectKey"]
                    restored_stat = os.lstat(target)
                    if _transaction_file_object_key(restored_stat) != tuple(backup["objectKey"]):
                        raise RuntimeError(
                            "rollback destination identity does not match its backup"
                        )
                else:
                    immediate_target = _private_snapshot_record(
                        _text_target_snapshot(project, target)
                    )
                    if not _transaction_snapshots_match(installed_record, immediate_target):
                        raise RuntimeError(
                            "target identity or state changed immediately before rollback unlink"
                        )
                    _transaction_checkpoint(
                        "before_rollback_unlink",
                        entry=entry,
                        target=target,
                    )
                    _revalidate_transaction_parents(
                        project,
                        target,
                        entry.get("_parentSnapshots", []),
                    )
                    unlink_stat = os.lstat(target)
                    if _transaction_file_object_key(unlink_stat) != tuple(entry["_installedObjectKey"]):
                        raise RuntimeError(
                            "target identity changed immediately before rollback unlink"
                        )
                    target.unlink()
            except (OSError, RuntimeError) as rollback_exc:
                rollback_errors.append(f"{entry['target']}: {rollback_exc}")
                unrestored_targets.add(str(entry["target"]))
        for entry in committed:
            target = Path(entry["_targetPath"])
            final_snapshot = _text_target_snapshot(project, target)
            final_record = _private_snapshot_record(final_snapshot)
            original_record = _private_snapshot_record(entry)
            restored = _transaction_snapshots_match(original_record, final_record)
            rollback_key = entry.get("_rollbackObjectKey")
            if not restored and rollback_key is not None:
                restored = (
                    final_record.get("state") == "present"
                    and tuple(final_record.get("objectKey") or ()) == tuple(rollback_key)
                    and final_record.get("hash") == original_record.get("hash")
                    and final_record.get("mode") == original_record.get("mode")
                )
            if not restored:
                target_label = str(entry["target"])
                unrestored_targets.add(target_label)
                final_error = (
                    f"{target_label}: final rollback verification found identity or state drift"
                )
                if final_error not in rollback_errors:
                    rollback_errors.append(final_error)
        recoverable_backups = [
            record
            for target_label, record in backups.items()
            if target_label not in unrestored_targets
        ]
        cleanup_errors.extend(_cleanup_transaction_paths(
            project,
            [*staged.values(), *recoverable_backups],
        ))
        cleanup_errors.extend(_remove_empty_transaction_dirs(project, created_dirs))
        rollback_ok = not rollback_errors
        error_parts = [str(exc)]
        if rollback_errors:
            error_parts.append("rollback incomplete: " + "; ".join(rollback_errors))
        if cleanup_errors:
            error_parts.append("cleanup incomplete: " + "; ".join(cleanup_errors))
        return {
            "ok": False,
            "applied": bool(unrestored_targets),
            "attempted": bool(committed),
            "rolledBack": bool(committed) and not unrestored_targets,
            "rollbackOk": rollback_ok,
            "cleanupOk": not cleanup_errors,
            "error": "; ".join(error_parts),
            "rollbackErrors": rollback_errors,
            "cleanupErrors": cleanup_errors,
            "written": [
                entry["target"]
                for entry in committed
                if str(entry["target"]) in unrestored_targets
            ],
        }

    cleanup_errors.extend(_cleanup_transaction_paths(
        project,
        list(backups.values()),
    ))
    return {
        "ok": not cleanup_errors,
        "applied": True,
        "attempted": True,
        "rolledBack": False,
        "rollbackOk": True,
        "cleanupOk": not cleanup_errors,
        "error": (
            "transaction cleanup was incomplete: " + "; ".join(cleanup_errors)
            if cleanup_errors
            else ""
        ),
        "cleanupErrors": cleanup_errors,
        "written": [entry["target"] for entry in candidates],
    }


def _apply_adapter_transaction(project: Path, entries: list[dict]) -> dict:
    """Apply only registry-managed adapter entries as one adapter transaction."""
    invalid_entries: list[str] = []
    for entry in entries:
        host = str(entry.get("host", ""))
        target = str(entry.get("target", "")).replace("\\", "/")
        managed_format = str(entry.get("managedFormat", ""))
        spec = _HOST_CONTEXT_EXPORTS.get(host)
        registry_managed = bool(
            isinstance(spec, dict)
            and spec.get("managed") is not False
            and target == str(spec.get("relative_path", "")).replace("\\", "/")
            and managed_format == _adapter_managed_format(spec)
        )
        portable_agents = bool(
            host == "codex_cli"
            and target == _AGENTS_MD_EXPORT_SPEC["relative_path"]
            and managed_format == _AGENTS_MD_EXPORT_SPEC["managed_format"]
        )
        not_applicable = bool(
            host in _HOST_CONTEXT_NOT_APPLICABLE
            and not target
            and entry.get("action") == "noop"
        )
        if (
            entry.get("kind") != "adapter"
            or not (registry_managed or portable_agents or not_applicable)
        ):
            invalid_entries.append(target or "(unknown)")
    if invalid_entries:
        error = "adapter transaction rejected non-adapter entries: " + ", ".join(invalid_entries)
        return {
            "ok": False,
            "applied": False,
            "attempted": False,
            "rolledBack": False,
            "rollbackOk": True,
            "cleanupOk": True,
            "error": error,
            "rollbackErrors": [],
            "cleanupErrors": [],
            "written": [],
        }
    return _apply_text_transaction(project, entries)


def _parse_claude_md_sections(text: str) -> list[tuple[str, str]]:
    """Parse a context markdown file into (heading, body) pairs for H2 sections.

    Returns a list of (section_title, section_body) where section_title
    is the text after '## ' and section_body is everything until the next
    H2 heading or end of file.
    """
    import re
    sections = []
    # Split on H2 headings, keeping the heading
    parts = re.split(r"(?m)^(## .+)$", text)
    # parts[0] is content before first H2 (preamble)
    # Then alternating: heading, body, heading, body, ...
    i = 1
    while i < len(parts):
        heading_line = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        # Extract title from "## Title [hook-enforced]" -> "Title"
        title = heading_line[3:].strip()  # remove "## "
        # Strip enforcement markers like [hook-enforced], [advisory]
        clean_title = re.sub(r"\s*\[.*?\]\s*$", "", title).strip()
        sections.append((clean_title, body))
        i += 2
    return sections


def _host_context_export_spec(user_host: str) -> dict | None:
    """Return host-context export metadata for the selected user host."""
    return _HOST_CONTEXT_EXPORTS.get(str(user_host).strip())


def _host_context_include_sections(spec: dict, source_label: str) -> tuple[str, ...]:
    if source_label == CONTROLWORK_CONTEXT_FILENAME:
        return _CONTROLWORK_HOST_CONTEXT_SECTIONS
    return tuple(spec["include_sections"])


def _adapter_source_authority_line(source_identity: str) -> str:
    normalized_source = str(source_identity).strip().replace("\\", "/")
    if normalized_source == CANONICAL_CONTEXT_FILENAME:
        return f"> Canonical source of truth: {normalized_source}."
    return f"> Selected source: {normalized_source} (non-canonical)."


def _read_claude_md_sections(project: Path) -> tuple[Path | None, list[tuple[str, str]]]:
    """Read the current context source and return parsed H2 sections."""
    context_source, text = _read_context_source(project)
    if context_source is None:
        return None, []
    return context_source, _parse_claude_md_sections(text)


def _build_host_context_output(file_label: str,
                               target_label: str,
                               host: str,
                               host_label: str,
                               include_sections: tuple[str, ...],
                               sections: list[tuple[str, str]],
                               source_identity: str,
                               managed_format: str,
                               front_matter: tuple[str, ...] = ()) -> tuple[str, int, list[str]]:
    """Build a host-native context file from the selected context sections."""
    target_label = str(target_label).replace("\\", "/")
    output_parts = [
        f"# {file_label}",
        "",
    ]
    if not front_matter:
        output_parts.extend([
            _adapter_marker_line(target_label, host, source_identity, managed_format),
            "",
        ])
    output_parts.extend([
        f"> Generated from {source_identity} by ControlCoding host-context export.",
        f"> Target host: {host_label}.",
        _adapter_source_authority_line(source_identity),
        "",
    ])

    included_count = 0
    warnings = []
    for title in include_sections:
        found = False
        for section_title, section_body in sections:
            if section_title == title:
                output_parts.append(f"## {title}")
                output_parts.append(section_body.rstrip())
                output_parts.append("")
                included_count += 1
                found = True
                break
        if not found:
            warnings.append(title)

    content = "\n".join(output_parts) + "\n"
    if front_matter:
        content = "\n".join(front_matter) + "\n" + content
        content = _adapter_insert_ownership_marker(
            content,
            _adapter_marker_line(target_label, host, source_identity, managed_format),
            managed_format,
            target_label,
        )
    return content, included_count, warnings


def _build_full_host_context_output(file_label: str,
                                    target_label: str,
                                    host: str,
                                    host_label: str,
                                    source_text: str,
                                    source_label: str,
                                    source_identity: str,
                                    managed_format: str) -> str:
    """Build a full-copy host context file with an explicit provenance header."""
    rendered = _render_context_for_target(source_text, source_label, file_label).rstrip() + "\n"
    managed_header = "\n".join([
        _adapter_marker_line(target_label, host, source_identity, managed_format),
        "",
        f"> Generated from {source_identity} by ControlCoding host-context export.",
        f"> Target host: {host_label}.",
        _adapter_source_authority_line(source_identity),
    ])
    return _adapter_insert_ownership_marker(
        rendered,
        managed_header,
        managed_format,
        target_label,
    )


def _host_context_source_state(project: Path, source: str = "") -> str:
    explicit_source = _resolve_explicit_context_source(project, source)
    if str(source or "").strip() and explicit_source is None:
        return "missing"
    if explicit_source is not None:
        if not explicit_source.exists():
            return "missing"
        if explicit_source.absolute() == _canonical_context_path(project).absolute():
            return "canonical"
        return "explicit"
    if _canonical_context_path(project).exists():
        return "canonical"
    if _controlwork_context_path(project).exists():
        return "controlwork_only"
    if _legacy_context_path(project).exists():
        return "legacy_only"
    return "missing"


def _resolve_context_sync_hosts(project: Path,
                                host: str = "",
                                all_hosts: bool = False) -> tuple[list[str], list[str]]:
    """Resolve the host list for context check/sync commands."""
    requested_host = str(host).strip()
    if requested_host:
        return [requested_host], []

    if all_hosts:
        return list(_HOST_CONTEXT_EXPORTS.keys()), []

    gateway = _read_json_object(_control_plane_read_path(project, "gateway_config.json"))
    primary_host, enabled_hosts = _normalize_gateway_hosts(gateway)
    hosts: list[str] = []
    for candidate in [primary_host] + enabled_hosts:
        if (
            candidate in _HOST_CONTEXT_EXPORTS
            or candidate in _HOST_CONTEXT_NOT_APPLICABLE
        ) and candidate not in hosts:
            hosts.append(candidate)

    if hosts:
        return hosts, []

    return [], [
        "host not specified and no supported userHost found in "
        f"{_control_plane_display_path('gateway_config.json')}"
    ]


def _render_expected_host_context(project: Path,
                                  host: str,
                                  source_path: Path,
                                  source_text: str,
                                  source_identity: str) -> tuple[str | None, dict]:
    """Render one host adapter from an already resolved source snapshot."""
    normalized_host = str(host).strip()
    spec = _host_context_export_spec(normalized_host)
    entry = {
        "host": normalized_host,
        "label": _format_user_host_label(normalized_host),
        "path": "",
        "source": "",
        "state": "unknown",
        "current": False,
        "warnings": [],
        "detail": "",
    }
    if spec is None:
        if normalized_host in _HOST_CONTEXT_NOT_APPLICABLE:
            entry["state"] = "not_applicable"
            entry["current"] = True
            entry["detail"] = "manual workflow has no generated host-context file"
        else:
            entry["state"] = "unsupported"
            entry["detail"] = f"no host-context export is defined for host '{normalized_host}'"
        return None, entry

    entry["label"] = spec["host_label"]
    entry["path"] = spec["relative_path"]
    source_label = source_path.name
    entry["source"] = source_identity
    managed_format = _adapter_managed_format(spec)

    if spec.get("export_mode") == "full_copy":
        try:
            content = _build_full_host_context_output(
                spec["file_label"],
                spec["relative_path"],
                normalized_host,
                spec["host_label"],
                source_text,
                source_label,
                source_identity,
                managed_format,
            )
        except ValueError as exc:
            entry["state"] = "unreadable_or_unsafe"
            entry["detail"] = f"source front matter is unsafe: {exc}"
            return None, entry
        return content, entry

    sections = _parse_claude_md_sections(source_text)
    content, _included_count, warnings = _build_host_context_output(
        spec["file_label"],
        spec["relative_path"],
        normalized_host,
        spec["host_label"],
        _host_context_include_sections(spec, source_label),
        sections,
        source_identity,
        managed_format,
        tuple(spec.get("front_matter", ())),
    )
    entry["warnings"] = warnings
    return content, entry


def _expected_host_context(project: Path, host: str, source: str = "") -> tuple[str | None, dict]:
    """Return expected generated content plus metadata for one host."""
    source_path, source_text, _source_bytes, _source_snapshot, source_error = _read_adapter_context_source(project, source=source)
    if source_path is not None:
        return _render_expected_host_context(
            project,
            host,
            source_path,
            source_text,
            _adapter_source_identity(project, source_path),
        )

    normalized_host = str(host).strip()
    spec = _host_context_export_spec(normalized_host)
    if spec is None and normalized_host in _HOST_CONTEXT_NOT_APPLICABLE:
        return _render_expected_host_context(
            project,
            normalized_host,
            project / CANONICAL_CONTEXT_FILENAME,
            "",
            CANONICAL_CONTEXT_FILENAME,
        )
    return None, {
        "host": normalized_host,
        "label": str(spec.get("host_label", _format_user_host_label(normalized_host))) if spec else _format_user_host_label(normalized_host),
        "path": str(spec.get("relative_path", "")) if spec else "",
        "source": str(source or "").strip() or CANONICAL_CONTEXT_FILENAME,
        "state": "unreadable_or_unsafe",
        "current": False,
        "warnings": [],
        "detail": source_error or "context source is missing",
    }


def _adapter_plan_payload(project: Path,
                          host: str = "",
                          all_hosts: bool = False,
                          source: str = "",
                          operation: str = "sync",
                          force: bool = False) -> dict:
    """Build one complete adapter plan and preview for the candidate set."""
    hosts, errors = _resolve_context_sync_hosts(project, host=host, all_hosts=all_hosts)
    registry_issues = _adapter_registry_issues(project, hosts)
    errors.extend(registry_issues)
    requires_source = any(_host_context_export_spec(candidate) is not None for candidate in hosts)
    if requires_source:
        source_path, source_text, source_bytes, source_snapshot, source_error = _read_adapter_context_source(project, source=source)
        source_identity = _adapter_source_identity(project, source_path) if source_path is not None else (str(source or "").strip() or CANONICAL_CONTEXT_FILENAME)
    else:
        source_path = project / CANONICAL_CONTEXT_FILENAME
        source_text = ""
        source_bytes = b""
        source_snapshot = None
        source_identity = CANONICAL_CONTEXT_FILENAME
        source_error = ""
    source_hash = hashlib.sha256(source_bytes).hexdigest() if source_path is not None else ""
    if source_error:
        errors.append(source_error)

    entries: list[dict] = []
    for resolved_host in hosts:
        spec = _host_context_export_spec(resolved_host)
        entry_registry_issues = _adapter_registry_entry_issues(
            project,
            resolved_host,
            spec,
        )
        if entry_registry_issues:
            entries.append({
                "kind": "adapter",
                "host": resolved_host,
                "label": str(spec.get("host_label", resolved_host)) if isinstance(spec, dict) else resolved_host,
                "target": str(spec.get("relative_path", "")) if isinstance(spec, dict) else "",
                "path": str(spec.get("relative_path", "")) if isinstance(spec, dict) else "",
                "source": source_identity,
                "managedFormat": _adapter_managed_format(spec) if isinstance(spec, dict) else "",
                "markerVersion": _ADAPTER_MARKER_VERSION,
                "state": "ambiguous",
                "ownership": "ambiguous",
                "action": "block",
                "replacesExisting": False,
                "current": False,
                "bindingMatches": False,
                "detail": "; ".join(entry_registry_issues),
                "warnings": [],
            })
            continue
        if spec is None:
            if resolved_host in _HOST_CONTEXT_NOT_APPLICABLE:
                entries.append({
                    "kind": "adapter",
                    "host": resolved_host,
                    "label": _format_user_host_label(resolved_host),
                    "target": "",
                    "path": "",
                    "source": source_identity,
                    "managedFormat": "",
                    "markerVersion": _ADAPTER_MARKER_VERSION,
                    "state": "not_applicable",
                    "ownership": "not_applicable",
                    "action": "noop",
                    "replacesExisting": False,
                    "current": True,
                    "bindingMatches": True,
                    "detail": "manual workflow has no generated host-context file",
                    "warnings": [],
                })
            else:
                entries.append({
                    "kind": "adapter",
                    "host": resolved_host,
                    "label": _format_user_host_label(resolved_host),
                    "target": "",
                    "path": "",
                    "source": source_identity,
                    "managedFormat": "",
                    "markerVersion": _ADAPTER_MARKER_VERSION,
                    "state": "unsupported",
                    "ownership": "unknown",
                    "action": "block",
                    "replacesExisting": False,
                    "current": False,
                    "bindingMatches": False,
                    "detail": f"no host-context export is defined for host '{resolved_host}'",
                    "warnings": [],
                })
            continue
        if source_path is None:
            entries.append({
                "kind": "adapter",
                "host": resolved_host,
                "label": spec["host_label"],
                "target": str(spec["relative_path"]).replace("\\", "/"),
                "path": str(spec["relative_path"]).replace("\\", "/"),
                "source": source_identity,
                "managedFormat": _adapter_managed_format(spec),
                "markerVersion": _ADAPTER_MARKER_VERSION,
                "state": "unreadable_or_unsafe",
                "ownership": "unreadable_or_unsafe",
                "action": "block",
                "replacesExisting": False,
                "current": False,
                "bindingMatches": False,
                "detail": source_error or "context source is missing",
                "warnings": [],
            })
            continue
        expected_content, rendered_entry = _render_expected_host_context(
            project,
            resolved_host,
            source_path,
            source_text,
            source_identity,
        )
        if expected_content is None:
            rendered_state = str(rendered_entry.get("state", "unreadable_or_unsafe"))
            rendered_entry.update({
                "kind": "adapter",
                "target": rendered_entry.get("path", ""),
                "ownership": (
                    rendered_state
                    if rendered_state in _ADAPTER_NORMATIVE_STATES
                    else "unknown"
                ),
                "action": "block",
                "replacesExisting": False,
            })
            entries.append(rendered_entry)
            continue
        planned_entry = _adapter_plan_entry(
            project,
            resolved_host,
            spec,
            source_path,
            source_identity,
            expected_content,
            operation=operation,
            warnings=rendered_entry.get("warnings", []),
            force=force,
        )
        planned_entry["_sourcePath"] = str(source_path)
        planned_entry["_sourceHash"] = source_hash
        planned_entry["_sourceSnapshot"] = source_snapshot
        entries.append(planned_entry)

    target_counts: dict[str, int] = {}
    for entry in entries:
        target_label = str(entry.get("target", ""))
        if target_label:
            target_counts[target_label] = target_counts.get(target_label, 0) + 1
    for entry in entries:
        if entry.get("target") and target_counts.get(str(entry["target"]), 0) > 1:
            entry.update({
                "state": "ambiguous",
                "ownership": "ambiguous",
                "action": "block",
                "replacesExisting": False,
                "detail": "multiple planned adapters resolve to the same target",
            })

    action_issues = _plan_transaction_action_set(project, entries)
    if action_issues:
        errors.extend(action_issues)
        for entry in entries:
            if entry.get("state") == "not_applicable":
                continue
            entry.update({
                "state": (
                    entry.get("state")
                    if entry.get("state") in {
                        "invalid",
                        "ambiguous",
                        "unreadable_or_unsafe",
                    }
                    else "ambiguous"
                ),
                "ownership": (
                    entry.get("ownership")
                    if entry.get("ownership") in {
                        "invalid",
                        "ambiguous",
                        "unreadable_or_unsafe",
                    }
                    else "ambiguous"
                ),
                "action": "block",
                "replacesExisting": False,
                "detail": "; ".join(action_issues),
            })

    preflight_ok = bool(entries) and not errors and all(entry.get("action") != "block" for entry in entries)
    source_state = _host_context_source_state(project, source=source)
    return {
        "operation": operation,
        "preflightOk": preflight_ok,
        "sourceState": source_state,
        "sourcePath": source_identity,
        "entries": entries,
        "errors": errors,
    }


def _context_sync_public_entry(entry: dict) -> dict:
    public_entry = _adapter_public_value(entry)
    ownership_state = str(public_entry.get("state", "unknown"))
    legacy_state = {
        "owned_current": "current",
        "owned_stale": "stale",
        "target_absent": "missing",
        "unmarked": "stale",
        "foreign": "stale",
        "invalid": "stale",
        "ambiguous": "unreadable",
        "unreadable_or_unsafe": "unreadable",
    }.get(ownership_state, ownership_state)
    public_entry["state"] = legacy_state
    public_entry["ownershipState"] = ownership_state
    public_entry["current"] = legacy_state in {"current", "not_applicable"}
    return public_entry


def _context_sync_payload(project: Path,
                          host: str = "",
                          all_hosts: bool = False,
                          source: str = "") -> dict:
    """Build a deterministic context-sync status payload."""
    plan = _adapter_plan_payload(
        project,
        host=host,
        all_hosts=all_hosts,
        source=source,
        operation="sync",
    )
    entries = [_context_sync_public_entry(entry) for entry in plan["entries"]]
    source_state = plan["sourceState"]
    accepted_source_states = {"canonical"}
    if str(source or "").strip():
        accepted_source_states.update({"explicit", "legacy_only"})
    current_states = {"current", "not_applicable"}
    ok_state = (
        source_state in accepted_source_states
        and not plan["errors"]
        and bool(entries)
        and all(entry.get("state") in current_states for entry in entries)
    )
    return {
        "ok": ok_state,
        "sourceState": source_state,
        "sourcePath": plan["sourcePath"],
        "canonicalPath": CANONICAL_CONTEXT_FILENAME,
        "controlworkPath": CONTROLWORK_CONTEXT_FILENAME,
        "legacyPath": LEGACY_CONTEXT_FILENAME,
        "hosts": entries,
        "errors": plan["errors"],
    }


def _constitution_reference_matches(text: str, reference: str) -> bool:
    """Return whether a project context mentions a path or invariant token."""
    normalized = str(reference or "").strip().replace("\\", "/")
    if not normalized:
        return False
    normalized = normalized.lstrip("./").rstrip("/")
    if not normalized:
        return False

    haystack = text.replace("\\", "/").lower()
    candidates = {
        normalized,
        normalized + "/",
        f"`{normalized}`",
        f"`{normalized}/`",
    }
    return any(candidate.lower() in haystack for candidate in candidates)


def _constitution_add_finding(findings: list[dict],
                              severity: str,
                              check: str,
                              message: str,
                              file: str = "",
                              remediation: str = "") -> None:
    findings.append({
        "severity": severity,
        "check": check,
        "file": file,
        "message": message,
        "remediation": remediation,
    })


def _constitution_drift_payload(project: Path,
                                host: str = "",
                                all_hosts: bool = False) -> dict:
    """Check whether CONTROLCODING.md still reflects active project controls."""
    context_source, source_text = _read_context_source(project)
    source_state = _host_context_source_state(project)
    context_sync = _context_sync_payload(project, host=host, all_hosts=all_hosts)
    findings: list[dict] = []

    if source_state == "missing":
        _constitution_add_finding(
            findings,
            "fail",
            "canonical_source",
            f"No project context source found. Expected {CANONICAL_CONTEXT_FILENAME}.",
            CANONICAL_CONTEXT_FILENAME,
            f"Create {CANONICAL_CONTEXT_FILENAME} and generate host context files from it.",
        )
    elif source_state == "legacy_only":
        _constitution_add_finding(
            findings,
            "fail",
            "canonical_source",
            f"Only legacy {LEGACY_CONTEXT_FILENAME} exists.",
            LEGACY_CONTEXT_FILENAME,
            f"Migrate the source rules to {CANONICAL_CONTEXT_FILENAME}.",
        )

    for error in context_sync.get("errors", []):
        _constitution_add_finding(
            findings,
            "fail",
            "context_sync",
            str(error),
            "",
            "Pass --host <host>, --all-hosts, or configure gateway_config.userHost.",
        )

    for entry in context_sync.get("hosts", []):
        state = entry.get("state")
        ownership_state = entry.get("ownershipState", state)
        if state == "not_applicable":
            _constitution_add_finding(
                findings,
                "info",
                "context_sync",
                f"{entry.get('label') or entry.get('host') or 'host'} context is not applicable: {entry.get('detail', '')}",
                entry.get("path") or "",
                "",
            )
            continue
        if state != "current":
            path_label = entry.get("path") or "(no generated path)"
            severity = "fail" if ownership_state in {
                "unsupported",
                "unreadable_or_unsafe",
                "unmarked",
                "foreign",
                "invalid",
                "ambiguous",
            } else "warn"
            _constitution_add_finding(
                findings,
                severity,
                "context_sync",
                f"{entry.get('label') or entry.get('host') or 'host'} context is {state}: {entry.get('detail', '')}",
                path_label,
                "Run `cc context sync --host <host>` or `cc context sync --all-hosts`.",
            )

    protected_zones: list[dict] = []
    cc_config_path = _control_plane_read_path(project, "cc_config.json")
    cc_config_source = "missing"
    if cc_config_path.exists():
        cc_config_source = _project_relative_label(project, cc_config_path)
        cc_config = _read_json_object(cc_config_path)
        protected_zones = _normalize_protected_zones(cc_config.get("protected_zones", []))
        if not isinstance(cc_config, dict):
            _constitution_add_finding(
                findings,
                "fail",
                "protected_zone_config",
                "cc_config.json is not a JSON object.",
                cc_config_source,
                "Repair the control-plane config before trusting protected-zone checks.",
            )

    structural_zones = [
        zone
        for zone in _PROMOTION_ZONES
        if (project / zone).is_dir()
    ]

    invariant_status = _invariant_status_payload(project)
    active_invariants = [
        invariant
        for invariant in invariant_status.get("invariants", [])
        if invariant.get("status") == "active"
    ]

    if source_state == "canonical" and context_source is not None:
        source_label = _project_relative_label(project, context_source)
        for zone in protected_zones:
            zone_path = str(zone.get("path", "")).strip()
            if zone_path and not _constitution_reference_matches(source_text, zone_path):
                _constitution_add_finding(
                    findings,
                    "warn",
                    "protected_zone_reference",
                    f"Configured protected zone is not mentioned in {CANONICAL_CONTEXT_FILENAME}: {zone_path}",
                    source_label,
                    "Add the zone to the Protected Zones section or remove it from cc_config.json.",
                )

        for zone in structural_zones:
            if not _constitution_reference_matches(source_text, zone):
                _constitution_add_finding(
                    findings,
                    "warn",
                    "structural_zone_reference",
                    f"Repository contains `{zone}/` but the canonical context does not mention it.",
                    source_label,
                    "Add the zone to Module Boundaries or document why it is outside ControlCoding governance.",
                )

        if invariant_status.get("source") == "project" and not invariant_status.get("ok"):
            detail = "; ".join(invariant_status.get("issues", [])) or "invalid invariant manifest"
            _constitution_add_finding(
                findings,
                "fail",
                "invariant_manifest",
                detail,
                invariant_status.get("path", "controlcoding.invariants.json"),
                "Fix the invariant manifest before trusting invariant drift checks.",
            )
        for invariant in active_invariants:
            invariant_id = str(invariant.get("id", "")).strip()
            title = str(invariant.get("title", "")).strip()
            mentioned = (
                _constitution_reference_matches(source_text, invariant_id)
                or (title and title.lower() in source_text.lower())
            )
            if invariant_id and not mentioned:
                _constitution_add_finding(
                    findings,
                    "warn",
                    "invariant_reference",
                    f"Active invariant is not mentioned in {CANONICAL_CONTEXT_FILENAME}: {invariant_id}",
                    source_label,
                    "Add the invariant id or title to Domain Invariants, or retire the invariant.",
                )

    blocking_findings = [
        finding
        for finding in findings
        if finding.get("severity") in {"fail", "warn"}
    ]
    return {
        "ok": not blocking_findings,
        "sourceState": source_state,
        "sourcePath": _project_relative_label(project, context_source) if context_source else "",
        "contextSync": context_sync,
        "protectedZones": protected_zones,
        "protectedZoneConfig": cc_config_source,
        "structuralZones": structural_zones,
        "activeInvariantIds": [
            str(invariant.get("id", "")).strip()
            for invariant in active_invariants
            if str(invariant.get("id", "")).strip()
        ],
        "invariantManifestSource": invariant_status.get("source"),
        "findings": findings,
    }


def _render_agents_md_output(source_text: str,
                             source_identity: str) -> tuple[str, int, list[str]]:
    sections = _parse_claude_md_sections(source_text)
    output_parts = [
        "# AGENTS.md",
        "",
        _adapter_marker_line(
            "AGENTS.md",
            "codex_cli",
            source_identity,
            _AGENTS_MD_EXPORT_SPEC["managed_format"],
        ),
        "",
        f"> Generated from {source_identity} by ControlCoding (`cc export agents-md`).",
        "> This file provides project rules for AI coding agents.",
        _adapter_source_authority_line(source_identity),
        "",
    ]
    included_count = 0
    warnings: list[str] = []
    for title in _AGENTS_MD_INCLUDE:
        found = False
        for section_title, section_body in sections:
            if section_title == title:
                output_parts.append(f"## {title}")
                output_parts.append(section_body.rstrip())
                output_parts.append("")
                included_count += 1
                found = True
                break
        if not found:
            warnings.append(title)
    return "\n".join(output_parts) + "\n", included_count, warnings


def _agents_md_export_plan(project: Path,
                           force: bool = False,
                           source: str = "") -> dict:
    source_path, source_text, source_bytes, source_snapshot, source_error = _read_adapter_context_source(project, source=source)
    source_identity = (
        _adapter_source_identity(project, source_path)
        if source_path is not None
        else (str(source or "").strip() or CANONICAL_CONTEXT_FILENAME)
    )
    if source_path is None:
        entry = {
            "kind": "adapter",
            "host": "codex_cli",
            "label": "Codex CLI",
            "target": "AGENTS.md",
            "path": "AGENTS.md",
            "source": source_identity,
            "managedFormat": _AGENTS_MD_EXPORT_SPEC["managed_format"],
            "markerVersion": _ADAPTER_MARKER_VERSION,
            "state": "unreadable_or_unsafe",
            "ownership": "unreadable_or_unsafe",
            "action": "block",
            "replacesExisting": False,
            "current": False,
            "bindingMatches": False,
            "detail": source_error or "context source is missing",
            "warnings": [],
        }
        return {
            "operation": "export",
            "preflightOk": False,
            "sourceState": _host_context_source_state(project, source=source),
            "sourcePath": source_identity,
            "entries": [entry],
            "errors": [entry["detail"]],
        }
    content, included_count, warnings = _render_agents_md_output(source_text, source_identity)
    entry = _adapter_plan_entry(
        project,
        "codex_cli",
        _AGENTS_MD_EXPORT_SPEC,
        source_path,
        source_identity,
        content,
        operation="export",
        warnings=warnings,
        force=force,
    )
    entry["_sourceHash"] = hashlib.sha256(source_bytes).hexdigest()
    entry["_sourcePath"] = str(source_path)
    entry["_sourceSnapshot"] = source_snapshot
    entry["includedCount"] = included_count
    action_issues = _plan_transaction_action_set(project, [entry])
    if action_issues:
        blocked_state = (
            entry.get("state")
            if entry.get("state") in {
                "invalid",
                "ambiguous",
                "unreadable_or_unsafe",
            }
            else "ambiguous"
        )
        entry.update({
            "state": blocked_state,
            "ownership": blocked_state,
            "action": "block",
            "replacesExisting": False,
            "detail": "; ".join(action_issues),
        })
    return {
        "operation": "export",
        "preflightOk": entry["action"] != "block" and not action_issues,
        "sourceState": _host_context_source_state(project, source=source),
        "sourcePath": source_identity,
        "entries": [entry],
        "errors": action_issues,
    }


def cmd_export_agents_md(project: Path,
                         force: bool = False,
                         preview_only: bool = False,
                         source: str = ""):
    """Generate AGENTS.md from the project's current context source.

    Includes only portable sections (Project Identity, Architecture Rules,
    Module Boundaries, Domain Invariants, Protected Zones). Strips
    CC-specific sections (Commit Ceremony, Operative Rules, etc.).
    """
    plan = _agents_md_export_plan(project, force=force, source=source)
    _emit_adapter_preview(plan)
    if not plan["preflightOk"]:
        return 1
    if preview_only:
        info("Preview only - no adapter files changed.")
        return 0
    result = _apply_adapter_transaction(project, plan["entries"])
    if not result["ok"]:
        fail(f"Adapter export failed: {result.get('error', 'unknown error')}")
        if not result.get("rollbackOk", True):
            fail("Adapter rollback was incomplete.")
        return 1
    entry = plan["entries"][0]
    for warning_title in entry.get("warnings", []):
        print(f"Warning: section '{warning_title}' not found in {plan['sourcePath']}, skipped")
    if result.get("written"):
        ok(f"Generated {project / 'AGENTS.md'} ({entry.get('includedCount', 0)} sections)")
    else:
        ok("AGENTS.md is already current.")
    return 0


def cmd_export_host_context(project: Path,
                            host: str = "",
                            force: bool = False,
                            quiet: bool = False,
                            source: str = "",
                            preview_only: bool = False) -> int:
    """Generate the host-native context file for a supported user host."""
    resolved_host = str(host).strip()
    if not resolved_host:
        gateway = _read_json_object(_control_plane_read_path(project, "gateway_config.json"))
        resolved_host, _enabled_hosts = _normalize_gateway_hosts(gateway)
        if resolved_host == "other" and "userHost" not in gateway:
            resolved_host = ""

    spec = _host_context_export_spec(resolved_host)
    if spec is None:
        if not quiet:
            if resolved_host:
                print(f"Error: no host-context export is defined for host '{resolved_host}'")
            else:
                print(
                    "Error: host not specified and no userHost found in "
                    f"{_control_plane_display_path('gateway_config.json')}"
                )
        return 1

    plan = _adapter_plan_payload(
        project,
        host=resolved_host,
        source=source,
        operation="export",
        force=force,
    )
    _emit_adapter_preview(plan)
    if not plan["preflightOk"]:
        return 1
    if preview_only:
        if not quiet:
            info("Preview only - no adapter files changed.")
        return 0
    result = _apply_adapter_transaction(project, plan["entries"])
    if not result["ok"]:
        if not quiet:
            fail(f"Host-context export failed: {result.get('error', 'unknown error')}")
            if not result.get("rollbackOk", True):
                fail("Adapter rollback was incomplete.")
        return 1
    if not quiet:
        entry = plan["entries"][0]
        for missing in entry.get("warnings", []):
            warn(f"Section '{missing}' not found in {plan['sourcePath']}, skipped")
        if result.get("written"):
            ok(f"Generated {project / spec['relative_path']} from {plan['sourcePath']} for {spec['host_label']}")
        else:
            ok(f"{spec['file_label']} is already current for {spec['host_label']}.")
    return 0


def cmd_context_check(project: Path,
                      host: str = "",
                      all_hosts: bool = False,
                      source: str = "",
                      json_output: bool = False) -> int:
    """Check whether generated host context files match the selected context source."""
    payload = _context_sync_payload(project, host=host, all_hosts=all_hosts, source=source)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print("Context sync check")
    print(f"Source state: {payload['sourceState']}")
    source_path_label = str(payload.get("sourcePath") or "").replace("\\", "/")
    explicit_noncanonical = bool(str(source or "").strip()) and (
        source_path_label != CANONICAL_CONTEXT_FILENAME
    )
    if payload["sourceState"] in {"missing", "controlwork_only"}:
        expected_sources = (
            source.strip()
            if source.strip()
            else CANONICAL_CONTEXT_FILENAME
        )
        fail(
            "No context source found "
            f"(expected {expected_sources})"
        )
    elif payload["sourceState"] == "legacy_only":
        warn(
            f"Using legacy {LEGACY_CONTEXT_FILENAME}; create {CANONICAL_CONTEXT_FILENAME} "
            "to make host sync canonical."
        )
        if explicit_noncanonical:
            info(f"Selected explicit context source: {source_path_label}")
    elif explicit_noncanonical or payload["sourceState"] == "explicit":
        info(
            "Selected explicit context source: "
            f"{source_path_label or source.strip()}"
        )
    elif source_path_label == CANONICAL_CONTEXT_FILENAME:
        ok(f"{payload.get('canonicalPath') or CANONICAL_CONTEXT_FILENAME} is the canonical source")
    else:
        fail(f"{CANONICAL_CONTEXT_FILENAME} is not the selected canonical source")

    for error in payload["errors"]:
        fail(error)

    for entry in payload["hosts"]:
        host_label = entry.get("label") or entry.get("host") or "host"
        path_label = entry.get("path") or "(no generated path)"
        state = entry.get("state")
        detail = entry.get("detail", "")
        line = f"{host_label}: {path_label} -> {state}"
        if detail:
            line += f" ({detail})"
        if state == "current":
            ok(line)
        elif state == "not_applicable":
            info(line)
        elif state in {
            "missing",
            "stale",
            "unsupported",
            "unreadable",
        }:
            fail(line)
        else:
            warn(line)
        for missing in entry.get("warnings", []):
            warn(f"{host_label}: source section '{missing}' is missing")

    if payload["ok"]:
        ok("Host context files are current.")
        return 0

    info("Fix: run `cc context sync --host <host>` or `cc context sync --all-hosts`.")
    return 1


def cmd_context_diff(project: Path,
                     host: str = "",
                     all_hosts: bool = False,
                     source: str = "",
                     json_output: bool = False) -> int:
    """Show generated host context drift with unified diff lines."""
    payload = _context_sync_payload(project, host=host, all_hosts=all_hosts, source=source)
    for entry in payload.get("hosts", []):
        expected_content, _expected_entry = _expected_host_context(
            project,
            str(entry.get("host") or ""),
            source=source,
        )
        diff_lines: list[str] = []
        if expected_content is not None and entry.get("path"):
            target = project / str(entry["path"])
            try:
                current_content = target.read_text(encoding="utf-8") if target.exists() else ""
            except OSError:
                current_content = ""
            if current_content != expected_content:
                diff_lines = list(
                    difflib.unified_diff(
                        current_content.splitlines(),
                        expected_content.splitlines(),
                        fromfile=str(entry["path"]),
                        tofile=f"generated/{entry['path']}",
                        lineterm="",
                    )
                )
        entry["hasDiff"] = bool(diff_lines)
        entry["diff"] = diff_lines
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print("Context diff")
    for entry in payload.get("hosts", []):
        print(f"{entry.get('label') or entry.get('host')}: {entry.get('state')}")
        for line in entry.get("diff", []):
            print(line)
    return 0 if payload["ok"] else 1


def cmd_context_drift(project: Path,
                      host: str = "",
                      all_hosts: bool = False,
                      json_output: bool = False) -> int:
    """Check whether the canonical project constitution matches active controls."""
    payload = _constitution_drift_payload(project, host=host, all_hosts=all_hosts)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print("Project constitution drift check")
    print(f"Source state: {payload['sourceState']}")
    if payload["sourceState"] == "canonical":
        ok(f"{CANONICAL_CONTEXT_FILENAME} is the canonical source")
    else:
        fail(f"{CANONICAL_CONTEXT_FILENAME} is not the active canonical source")

    if payload["protectedZones"]:
        info(f"Configured protected zones: {len(payload['protectedZones'])}")
    else:
        info("Configured protected zones: none")

    if payload["activeInvariantIds"]:
        info(
            "Active invariants: "
            + ", ".join(payload["activeInvariantIds"][:8])
        )
    else:
        info("Active invariants: none")

    for finding in payload["findings"]:
        severity = finding.get("severity", "warn")
        message = finding.get("message", "")
        remediation = finding.get("remediation", "")
        location = finding.get("file", "")
        line = f"{location}: {message}" if location else message
        printer = fail if severity == "fail" else warn if severity == "warn" else info
        printer(line)
        if remediation:
            info(f"  Fix: {remediation}")

    if payload["ok"]:
        ok("Project constitution matches active context, zones, and invariants.")
        return 0

    info("Fix drift, then rerun `cc context sync --host <host>` and `cc doctor`.")
    return 1


def _context_sync_results(plan: dict,
                          transaction_ok: bool | None = None,
                          preview_only: bool = False) -> list[dict]:
    results = [
        {"host": "", "ok": False, "detail": str(error)}
        for error in plan.get("errors", [])
    ]
    for raw_entry in plan.get("entries", []):
        entry = _context_sync_public_entry(raw_entry)
        action = str(entry.get("action", "block"))
        entry_ok = action != "block" and transaction_ok is not False
        if action == "block":
            detail = str(entry.get("detail", "adapter preflight failed"))
        elif preview_only:
            detail = "preview only"
        elif transaction_ok is False:
            detail = "sync transaction failed"
        elif action == "noop":
            detail = str(entry.get("detail", "already current"))
        elif entry.get("state") == "not_applicable":
            detail = str(entry.get("detail", "no host-context export needed"))
        else:
            detail = "synced"
        results.append({
            "host": str(entry.get("host", "")),
            "label": str(entry.get("label", "")),
            "path": str(entry.get("path", "")),
            "ok": entry_ok,
            "detail": detail,
        })
    return results


def cmd_context_sync(project: Path,
                     host: str = "",
                     all_hosts: bool = False,
                     source: str = "",
                     json_output: bool = False,
                     preview_only: bool = False) -> int:
    """Regenerate host context files from the selected context source."""
    plan = _adapter_plan_payload(
        project,
        host=host,
        all_hosts=all_hosts,
        source=source,
        operation="sync",
    )
    _emit_adapter_preview(plan, json_output=json_output)
    if not plan["preflightOk"]:
        payload = {
            "ok": False,
            "results": _context_sync_results(plan, transaction_ok=False),
            "previewOnly": preview_only,
            "applied": False,
            "adapterPlan": _adapter_public_value(plan),
            "contextSync": _context_sync_payload(project, host=host, all_hosts=all_hosts, source=source),
        }
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            fail("Context sync blocked by adapter preflight.")
        return 1
    if preview_only:
        payload = {
            "ok": True,
            "results": _context_sync_results(plan, preview_only=True),
            "previewOnly": True,
            "applied": False,
            "adapterPlan": _adapter_public_value(plan),
        }
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            info("Preview only - no adapter files changed.")
        return 0

    transaction = _apply_adapter_transaction(project, plan["entries"])
    check_payload = _context_sync_payload(project, host=host, all_hosts=all_hosts, source=source)
    payload = {
        "ok": bool(transaction["ok"] and check_payload["ok"]),
        "results": _context_sync_results(plan, transaction_ok=bool(transaction["ok"])),
        "previewOnly": False,
        "applied": bool(transaction.get("applied")),
        "transaction": transaction,
        "adapterPlan": _adapter_public_value(plan),
        "contextSync": check_payload,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    if payload["ok"]:
        for path_label in transaction.get("written", []):
            ok(f"Synced {path_label}")
        ok("Context sync complete.")
        return 0
    fail(f"Context sync failed: {transaction.get('error', 'verification is not current')}")
    if not transaction.get("rollbackOk", True):
        fail("Context sync rollback was incomplete.")
    return 1


def cmd_context_adopt(project: Path,
                      host: str = "",
                      all_hosts: bool = False,
                      source: str = "",
                      apply: bool = False,
                      json_output: bool = False) -> int:
    """Adopt unmarked or explicitly foreign safe adapter targets as managed files."""
    plan = _adapter_plan_payload(
        project,
        host=host,
        all_hosts=all_hosts,
        source=source,
        operation="adopt",
    )
    _emit_adapter_preview(plan, json_output=json_output)
    if not plan["preflightOk"]:
        payload = {
            "ok": False,
            "previewOnly": not apply,
            "applied": False,
            "adapterPlan": _adapter_public_value(plan),
        }
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            fail("Adapter adoption blocked by preflight.")
        return 1
    if not apply:
        payload = {
            "ok": True,
            "previewOnly": True,
            "applied": False,
            "adapterPlan": _adapter_public_value(plan),
        }
        if json_output:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            info("Preview only - re-run with --apply to adopt the adapter target.")
        return 0

    transaction = _apply_adapter_transaction(project, plan["entries"])
    payload = {
        "ok": bool(transaction["ok"]),
        "previewOnly": False,
        "applied": bool(transaction.get("applied")),
        "transaction": transaction,
        "adapterPlan": _adapter_public_value(plan),
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1
    if payload["ok"]:
        for path_label in transaction.get("written", []):
            ok(f"Adopted {path_label} as a fully managed adapter")
        return 0
    fail(f"Adapter adoption failed: {transaction.get('error', 'unknown error')}")
    if not transaction.get("rollbackOk", True):
        fail("Adapter adoption rollback was incomplete.")
    return 1


def cmd_truth_report(project: Path, json_output: bool = False) -> int:
    """Report the machine-readable ControlCoding capability registry."""
    payload = _truth_check_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("Scope: structural")
    for limitation in payload["limitations"]:
        print(f"Limit: {limitation}")

    print("ControlCoding capability truth report")
    print(f"Registry source: {payload['registrySource']} ({payload['registryPath']})")
    print("Summary:")
    for state, count in payload["summary"].items():
        if count:
            print(f"  {state}: {count}")
    print()
    for capability in payload["capabilities"]:
        commands = ", ".join(capability["commands"]) if capability["commands"] else "no shipped command"
        print(
            f"- {capability['id']}: {capability['state']} / "
            f"{capability['control_level']} - {capability['label']}"
        )
        print(f"  commands: {commands}")
    if payload["findings"]:
        print()
        print("Findings:")
        for finding in payload["findings"]:
            print(
                f"  [{finding['severity']}] "
                f"{finding['capability']}: {finding['message']}"
            )
    return 0


def cmd_truth_check_docs(project: Path, json_output: bool = False) -> int:
    """Validate ControlCoding commands mentioned in public docs."""
    payload = _truth_check_docs_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print("Scope: structural")
    for limitation in payload["limitations"]:
        print(f"Limit: {limitation}")

    if payload["ok"]:
        ok(
            "Documentation truth check passed "
            f"({payload['commandCount']} command mention(s), "
            f"{payload['controlClaimCount']} strong control claim(s))"
        )
        return 0

    fail("Documentation truth check failed")
    for finding in payload["findings"]:
        printer = fail if finding["severity"] == "fail" else warn
        location = f"{finding['file']}:{finding['line']}" if finding.get("line") else finding["file"]
        subject = finding.get("command") or finding.get("claim", "")
        printer(f"{location}: {subject} - {finding['message']}")
    return 1


def cmd_truth_check(project: Path,
                    json_output: bool = False,
                    include_docs: bool = False) -> int:
    """Validate that declared shipped capabilities match routed CLI behavior."""
    payload = _truth_check_payload(project, include_docs=include_docs)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print("Scope: structural")
    for limitation in payload["limitations"]:
        print(f"Limit: {limitation}")

    if payload["ok"]:
        ok(
            "Capability truth check passed "
            f"({len(payload['capabilities'])} capabilities, source={payload['registrySource']})"
        )
        return 0

    fail("Capability truth check failed")
    for finding in payload["findings"]:
        printer = fail if finding["severity"] == "fail" else warn
        printer(f"{finding['capability']}: {finding['message']}")
    return 1


def cmd_write_path_status(project: Path, json_output: bool = False) -> int:
    payload = _controlled_write_status_payload(project)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(
        "Controlled write path: "
        + ("enabled" if payload["enabled"] else "disabled")
    )
    print(
        f"Mode: {payload['mode']} "
        f"(scope={payload['scope']}, patch_format={payload['patchFormat']})"
    )
    print(
        "Apply manifest requirement: "
        f"{payload['manifestRequirement']} "
        f"({payload['manifestCount']} stored manifest(s) in {payload['manifestStore']})"
    )
    print(
        "Receipts: "
        f"{payload['receiptCount']} stored receipt(s) in {payload['receiptStore']}"
    )
    host = payload["host"]
    print(
        "Host context: "
        f"{host['label']} ({host['userHost']}), "
        f"protection={host['protectionModel']}, inline={host['inlineBoundary']}"
    )
    if payload["enabled"] and payload["preventsProtectedWritesBeforeApply"]:
        print("Protected-zone check before apply: mechanical")
    elif payload["enabled"]:
        print(f"Protected-zone check before apply: unavailable ({payload['prereqIssue']})")
    else:
        print("Protected-zone check before apply: off")
    if payload["enabled"] and payload["preflightFitnessForApply"]:
        if payload["preflightFitnessBeforeApply"]:
            print("Shadow fitness preflight before apply: mechanical")
        else:
            print(
                "Shadow fitness preflight before apply: unavailable "
                f"({payload['preflightPrereqIssue'] or payload['prereqIssue'] or 'unknown issue'})"
            )
    else:
        print("Shadow fitness preflight before apply: off")
    print(payload["honestyNote"])
    metrics = payload["metrics"]
    print(
        "Metrics: "
        f"attempts={metrics.get('attempts', 0)}, "
        f"checks={metrics.get('check_only_runs', 0)}, "
        f"prepared={metrics.get('prepared', 0)}, "
        f"preflight_runs={metrics.get('preflight_runs', 0)}, "
        f"applied={metrics.get('applied', 0)}, "
        f"blocked={metrics.get('blocked', 0)}, "
        f"manifest_blocked={metrics.get('manifest_blocked', 0)}, "
        f"preflight_blocked={metrics.get('preflight_blocked', 0)}, "
        f"invalid={metrics.get('invalid', 0)}, "
        f"warned={metrics.get('warned', 0)}"
    )
    if metrics.get("last_run_at"):
        print(
            f"Last run: {metrics['last_run_at']} "
            f"({metrics.get('last_operation', 'unknown')} -> {metrics.get('last_status', 'unknown')})"
        )
    latest_receipt = payload.get("latestReceipt", {})
    if latest_receipt:
        print(
            "Latest receipt: "
            f"{latest_receipt.get('operation', 'unknown')} -> {latest_receipt.get('status', 'unknown')} "
            f"({latest_receipt.get('receiptId', '')})"
        )
    return 0


def _load_patch_file(patch_file: Path) -> str:
    return patch_file.read_text(encoding="utf-8")


def _set_controlled_write_path(project: Path, next_value: dict) -> tuple[Path, dict]:
    cc_config_path = _control_plane_read_path(project, "cc_config.json")
    if not cc_config_path.exists():
        cc_config_path = _control_plane_path(project, "cc_config.json")
    cc_config = _read_json_object(cc_config_path)
    cc_config["controlled_write_path"] = next_value
    _write_json_atomic(cc_config_path, cc_config)
    return cc_config_path, cc_config


def cmd_write_path_enable(project: Path,
                          mode: str = "patch_gateway",
                          patch_format: str = "unified_diff",
                          require_manifest_for_apply: bool = False,
                          preflight_fitness_for_apply: bool = False,
                          json_output: bool = False) -> int:
    requested = {
        "enabled": True,
        "mode": mode,
        "scope": "opt_in",
        "patch_format": patch_format,
        "require_lift_for_deny": True,
        "measure_ux": True,
        "require_manifest_for_apply": require_manifest_for_apply,
        "preflight_fitness_for_apply": preflight_fitness_for_apply,
    }
    normalized = _normalize_controlled_write_path(requested)
    if normalized["mode"] == "off":
        print("Error: controlled write mode must not be 'off' when enabling")
        return 1

    cc_config_path, _ = _set_controlled_write_path(project, normalized)
    payload = _controlled_write_status_payload(project)
    if json_output:
        print(json.dumps({"ok": True, "configPath": str(cc_config_path), "status": payload}, indent=2, ensure_ascii=False))
        return 0

    ok(
        "Enabled controlled write path "
        f"({normalized['mode']}, {normalized['patch_format']}, opt-in)"
    )
    info(
        (
            "Use `cc write-path prepare --patch-file <file>` before apply."
            if normalized["require_manifest_for_apply"]
            else "Use `cc write-path apply --patch-file <file>` to route a patch through CC before it lands."
        )
    )
    if normalized["require_manifest_for_apply"]:
        info("Apply now requires a prepared manifest id: `cc write-path apply --patch-file <file> --manifest-id <id>`.") 
    if normalized["preflight_fitness_for_apply"]:
        info("Apply now runs a shadow-worktree fitness preflight before the real patch lands.")
    info(payload["honestyNote"])
    if payload["prereqIssue"]:
        warn(f"Patch gateway prerequisites are not ready yet: {payload['prereqIssue']}")
    if payload["preflightPrereqIssue"]:
        warn(f"Shadow preflight prerequisites are not ready yet: {payload['preflightPrereqIssue']}")
    return 0


def cmd_write_path_disable(project: Path, json_output: bool = False) -> int:
    normalized = _normalize_controlled_write_path(False)
    cc_config_path, _ = _set_controlled_write_path(project, normalized)
    if json_output:
        print(json.dumps({"ok": True, "configPath": str(cc_config_path)}, indent=2, ensure_ascii=False))
        return 0
    ok("Disabled controlled write path")
    return 0


def cmd_write_path_prepare(project: Path,
                           patch_file: Path,
                           reason: str = "",
                           json_output: bool = False) -> int:
    patch_file = patch_file if patch_file.is_absolute() else project / patch_file
    status_payload = _controlled_write_status_payload(project)
    if not status_payload["enabled"]:
        message = (
            "Controlled write path is disabled. "
            "Run `cc write-path enable --mode patch_gateway` first."
        )
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    prereq_issue = status_payload["prereqIssue"]
    if prereq_issue:
        if json_output:
            print(json.dumps({"ok": False, "error": prereq_issue}, indent=2, ensure_ascii=False))
        else:
            fail(f"Controlled write path is enabled but unusable: {prereq_issue}")
        return 1

    if not patch_file.exists():
        message = f"Patch file not found: {patch_file}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    try:
        patch_text = _load_patch_file(patch_file)
    except OSError as exc:
        message = f"Could not read patch file: {exc}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    touched_files = _extract_patch_paths(patch_text)
    protected_zones = _normalize_protected_zones(
        _read_json_object(_control_plane_read_path(project, "cc_config.json")).get("protected_zones", [])
    )
    active_lifts = _load_approved_lifts(project)

    started = time.perf_counter()
    deny_hits, warn_hits = _evaluate_controlled_write_paths(
        touched_files,
        protected_zones,
        approved_lifts=active_lifts,
    )
    validation_ok, validation_detail = _run_git_apply(project, patch_text, check_only=True)
    validation_ms = int((time.perf_counter() - started) * 1000)

    result_payload = {
        "ok": False,
        "mode": "patch_gateway",
        "operation": "prepare",
        "patchFile": str(patch_file),
        "touchedFiles": touched_files,
        "denyHits": deny_hits,
        "warnHits": warn_hits,
        "validationOk": validation_ok,
        "validationDetail": validation_detail,
        "reason": reason,
        "manifestId": "",
        "manifestPath": "",
        "canApplyWithoutLift": not deny_hits,
        "validationMs": validation_ms,
    }

    if not touched_files:
        _record_controlled_write_attempt(
            project,
            operation="prepare",
            status="invalid",
            touched_files=touched_files,
            deny_hits=deny_hits,
            warn_hits=warn_hits,
            validation_ms=validation_ms,
            reason=reason,
        )
        result_payload["error"] = (
            "Patch gateway currently supports unified diff patches with file headers. "
            "No touched files could be extracted."
        )
        _persist_write_path_receipt(project, status="invalid", payload=result_payload)
        if json_output:
            print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        else:
            fail(result_payload["error"])
        return 1

    if not validation_ok:
        _record_controlled_write_attempt(
            project,
            operation="prepare",
            status="invalid",
            touched_files=touched_files,
            deny_hits=deny_hits,
            warn_hits=warn_hits,
            validation_ms=validation_ms,
            reason=reason,
        )
        result_payload["error"] = validation_detail or "git apply --check failed"
        _persist_write_path_receipt(project, status="invalid", payload=result_payload)
        if json_output:
            print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        else:
            fail("Patch failed git apply --check.")
            if validation_detail:
                print(validation_detail)
        return 1

    manifest = _build_write_path_manifest(
        project,
        patch_file=patch_file,
        patch_text=patch_text,
        touched_files=touched_files,
        deny_hits=deny_hits,
        warn_hits=warn_hits,
        validation_ok=validation_ok,
        validation_detail=validation_detail,
        validation_ms=validation_ms,
        reason=reason,
    )
    manifest_path = _save_write_path_manifest(project, manifest)
    _record_controlled_write_attempt(
        project,
        operation="prepare",
        status="prepared",
        touched_files=touched_files,
        deny_hits=deny_hits,
        warn_hits=warn_hits,
        validation_ms=validation_ms,
        reason=reason,
        manifest_id=manifest["manifest_id"],
    )
    result_payload.update({
        "ok": True,
        "manifestId": manifest["manifest_id"],
        "manifestPath": _project_relative_label(project, manifest_path),
        "validationMs": validation_ms,
    })
    _persist_write_path_receipt(project, status="prepared", payload=result_payload)

    if json_output:
        print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        return 0

    ok(f"Prepared write-path manifest {manifest['manifest_id']}.")
    info(f"Touched files: {', '.join(touched_files)}")
    if deny_hits:
        warn("This patch still needs an approved lift before apply:")
        for entry in deny_hits:
            print(f"  - {_format_zone_hit(entry)}")
    if warn_hits:
        warn("Patch touches WARN zones:")
        for entry in warn_hits:
            print(f"  - {_format_zone_hit(entry)}")
    info(
        "Apply with: "
        f"cc write-path apply --patch-file {patch_file} --manifest-id {manifest['manifest_id']}"
    )
    return 0


def _controlled_write_patch_gateway(project: Path,
                                    *,
                                    patch_file: Path,
                                    apply_patch: bool,
                                    manifest_id: str = "",
                                    reason: str = "",
                                    json_output: bool = False) -> int:
    patch_file = patch_file if patch_file.is_absolute() else project / patch_file
    status_payload = _controlled_write_status_payload(project)
    if not status_payload["enabled"]:
        message = (
            "Controlled write path is disabled. "
            "Run `cc write-path enable --mode patch_gateway` first."
        )
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    prereq_issue = status_payload["prereqIssue"]
    if prereq_issue:
        if json_output:
            print(json.dumps({"ok": False, "error": prereq_issue}, indent=2, ensure_ascii=False))
        else:
            fail(f"Controlled write path is enabled but unusable: {prereq_issue}")
        return 1

    if not patch_file.exists():
        message = f"Patch file not found: {patch_file}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    try:
        patch_text = _load_patch_file(patch_file)
    except OSError as exc:
        message = f"Could not read patch file: {exc}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1

    touched_files = _extract_patch_paths(patch_text)
    protected_zones = _normalize_protected_zones(
        _read_json_object(_control_plane_read_path(project, "cc_config.json")).get("protected_zones", [])
    )
    active_lifts = _load_approved_lifts(project)

    started = time.perf_counter()
    deny_hits, warn_hits = _evaluate_controlled_write_paths(
        touched_files,
        protected_zones,
        approved_lifts=active_lifts,
    )
    validation_ok, validation_detail = _run_git_apply(project, patch_text, check_only=True)
    validation_ms = int((time.perf_counter() - started) * 1000)

    result_payload = {
        "ok": False,
        "mode": "patch_gateway",
        "operation": "apply" if apply_patch else "check",
        "patchFile": str(patch_file),
        "touchedFiles": touched_files,
        "denyHits": deny_hits,
        "warnHits": warn_hits,
        "validationOk": validation_ok,
        "validationDetail": validation_detail,
        "blockedBeforeApply": bool(deny_hits),
        "applied": False,
        "reason": reason,
        "requireManifestForApply": status_payload["requireManifestForApply"],
        "manifestId": str(manifest_id or "").strip(),
        "manifestVerified": False,
        "preflightFitnessConfigured": status_payload["preflightFitnessForApply"],
        "preflightFitnessRan": False,
        "preflightFitnessOk": False,
        "preflightFitnessDetail": "",
        "preflightFitnessWarning": "",
        "validationMs": validation_ms,
        "applyMs": 0,
    }

    if not touched_files:
        _record_controlled_write_attempt(
            project,
            operation="apply" if apply_patch else "check",
            status="invalid",
            touched_files=touched_files,
            deny_hits=deny_hits,
            warn_hits=warn_hits,
            validation_ms=validation_ms,
            reason=reason,
        )
        result_payload["error"] = (
            "Patch gateway currently supports unified diff patches with file headers. "
            "No touched files could be extracted."
        )
        _persist_write_path_receipt(project, status="invalid", payload=result_payload)
        if json_output:
            print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        else:
            fail(result_payload["error"])
        return 1

    manifest = {}
    if apply_patch and status_payload["requireManifestForApply"] and not manifest_id:
        _record_controlled_write_attempt(
            project,
            operation="apply",
            status="manifest_blocked",
            touched_files=touched_files,
            deny_hits=deny_hits,
            warn_hits=warn_hits,
            validation_ms=validation_ms,
            reason=reason,
        )
        result_payload["error"] = (
            "Apply requires a prepared manifest. "
            "Run `cc write-path prepare --patch-file <file>` and retry with `--manifest-id <id>`."
        )
        _persist_write_path_receipt(project, status="manifest_blocked", payload=result_payload)
        if json_output:
            print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        else:
            fail(result_payload["error"])
        return 1

    if manifest_id:
        manifest, manifest_issue = _validate_write_path_manifest(
            project,
            manifest_id,
            patch_text=patch_text,
            touched_files=touched_files,
        )
        if manifest_issue:
            _record_controlled_write_attempt(
                project,
                operation="apply" if apply_patch else "check",
                status="manifest_blocked",
                touched_files=touched_files,
                deny_hits=deny_hits,
                warn_hits=warn_hits,
                validation_ms=validation_ms,
                reason=reason,
                manifest_id=str(manifest_id or "").strip(),
            )
            result_payload["error"] = manifest_issue
            _persist_write_path_receipt(project, status="manifest_blocked", payload=result_payload)
            if json_output:
                print(json.dumps(result_payload, indent=2, ensure_ascii=False))
            else:
                fail(manifest_issue)
            return 1
        result_payload["manifestVerified"] = True

    if deny_hits:
        _record_controlled_write_attempt(
            project,
            operation="apply" if apply_patch else "check",
            status="blocked",
            touched_files=touched_files,
            deny_hits=deny_hits,
            warn_hits=warn_hits,
            validation_ms=validation_ms,
            reason=reason,
        )
        result_payload["error"] = "Patch blocked before apply by protected-zone policy."
        _persist_write_path_receipt(project, status="blocked", payload=result_payload)
        if json_output:
            print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        else:
            fail("Controlled write path blocked the patch before apply.")
            for entry in deny_hits:
                print(f"  - {_format_zone_hit(entry)}")
            info("Request and approve a scoped lift before retrying if this change is intentional.")
        return 1

    if not validation_ok:
        _record_controlled_write_attempt(
            project,
            operation="apply" if apply_patch else "check",
            status="invalid",
            touched_files=touched_files,
            deny_hits=deny_hits,
            warn_hits=warn_hits,
            validation_ms=validation_ms,
            reason=reason,
        )
        result_payload["error"] = validation_detail or "git apply --check failed"
        _persist_write_path_receipt(project, status="invalid", payload=result_payload)
        if json_output:
            print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        else:
            fail("Patch failed git apply --check.")
            if validation_detail:
                print(validation_detail)
        return 1

    if apply_patch and status_payload["preflightFitnessForApply"]:
        preflight_issue = status_payload["preflightPrereqIssue"]
        if preflight_issue:
            _record_controlled_write_attempt(
                project,
                operation="preflight",
                status="preflight_blocked",
                touched_files=touched_files,
                deny_hits=deny_hits,
                warn_hits=warn_hits,
                validation_ms=validation_ms,
                reason=reason,
                manifest_id=str(manifest_id or "").strip(),
            )
            result_payload["error"] = preflight_issue
            result_payload["preflightFitnessDetail"] = preflight_issue
            _persist_write_path_receipt(project, status="preflight_blocked", payload=result_payload)
            if json_output:
                print(json.dumps(result_payload, indent=2, ensure_ascii=False))
            else:
                fail(f"Shadow fitness preflight is unavailable: {preflight_issue}")
            return 1

        preflight = _run_shadow_fitness_preflight(
            project,
            patch_text=patch_text,
            touched_files=touched_files,
        )
        result_payload["preflightFitnessRan"] = True
        result_payload["preflightFitnessOk"] = bool(preflight.get("ok"))
        result_payload["preflightFitnessDetail"] = str(preflight.get("detail", "")).strip()
        result_payload["preflightFitnessWarning"] = str(preflight.get("warning", "")).strip()
        _record_controlled_write_attempt(
            project,
            operation="preflight",
            status="preflight_ok" if preflight.get("ok") else "preflight_blocked",
            touched_files=touched_files,
            deny_hits=deny_hits,
            warn_hits=warn_hits,
            validation_ms=validation_ms,
            reason=reason,
            manifest_id=str(manifest_id or "").strip(),
        )
        if preflight.get("blocked"):
            result_payload["error"] = result_payload["preflightFitnessDetail"] or "Shadow fitness preflight blocked the patch."
            _persist_write_path_receipt(project, status="preflight_blocked", payload=result_payload)
            if json_output:
                print(json.dumps(result_payload, indent=2, ensure_ascii=False))
            else:
                fail("Shadow fitness preflight blocked the patch before apply.")
                if result_payload["preflightFitnessDetail"]:
                    print(result_payload["preflightFitnessDetail"])
            return 1

    apply_ms = 0
    if apply_patch:
        apply_started = time.perf_counter()
        apply_ok, apply_detail = _run_git_apply(project, patch_text, check_only=False)
        apply_ms = int((time.perf_counter() - apply_started) * 1000)
        if not apply_ok:
            _record_controlled_write_attempt(
                project,
                operation="apply",
                status="invalid",
                touched_files=touched_files,
                deny_hits=deny_hits,
                warn_hits=warn_hits,
                validation_ms=validation_ms,
                apply_ms=apply_ms,
                reason=reason,
                manifest_id=str(manifest_id or "").strip(),
            )
            result_payload["error"] = apply_detail or "git apply failed"
            result_payload["applyMs"] = apply_ms
            _persist_write_path_receipt(project, status="invalid", payload=result_payload)
            if json_output:
                print(json.dumps(result_payload, indent=2, ensure_ascii=False))
            else:
                fail("Patch passed validation but failed during apply.")
                if apply_detail:
                    print(apply_detail)
            return 1
        if manifest:
            _mark_write_path_manifest_used(project, manifest, patch_file=patch_file)
            result_payload["manifestMarkedUsed"] = True

    _record_controlled_write_attempt(
        project,
        operation="apply" if apply_patch else "check",
        status="applied" if apply_patch else "validated",
        touched_files=touched_files,
        deny_hits=deny_hits,
        warn_hits=warn_hits,
        validation_ms=validation_ms,
        apply_ms=apply_ms,
        reason=reason,
        manifest_id=str(manifest_id or "").strip(),
    )
    result_payload["ok"] = True
    result_payload["applied"] = apply_patch
    result_payload["validationMs"] = validation_ms
    result_payload["applyMs"] = apply_ms
    _persist_write_path_receipt(
        project,
        status="applied" if apply_patch else "validated",
        payload=result_payload,
    )

    if json_output:
        print(json.dumps(result_payload, indent=2, ensure_ascii=False))
        return 0

    ok(
        "Controlled write path validation passed."
        if not apply_patch
        else "Controlled write path applied the patch."
    )
    info(f"Touched files: {', '.join(touched_files)}")
    if warn_hits:
        warn("Patch touches WARN zones:")
        for entry in warn_hits:
            print(f"  - {_format_zone_hit(entry)}")
    if validation_detail:
        info(f"git apply --check: {validation_detail}")
    if result_payload["preflightFitnessRan"] and result_payload["preflightFitnessWarning"]:
        warn(f"Shadow fitness preflight warning: {result_payload['preflightFitnessWarning']}")
    return 0


def cmd_write_path_check(project: Path,
                         patch_file: Path,
                         manifest_id: str = "",
                         reason: str = "",
                         json_output: bool = False) -> int:
    return _controlled_write_patch_gateway(
        project,
        patch_file=patch_file,
        apply_patch=False,
        manifest_id=manifest_id,
        reason=reason,
        json_output=json_output,
    )


def cmd_write_path_apply(project: Path,
                         patch_file: Path,
                         manifest_id: str = "",
                         reason: str = "",
                         json_output: bool = False) -> int:
    return _controlled_write_patch_gateway(
        project,
        patch_file=patch_file,
        apply_patch=True,
        manifest_id=manifest_id,
        reason=reason,
        json_output=json_output,
    )


def cmd_write_path_receipts(project: Path,
                            limit: int = 10,
                            json_output: bool = False) -> int:
    resolved_limit = max(1, int(limit or 10))
    receipts = _list_write_path_receipts(project)
    summaries = [_summarize_write_path_receipt(receipt) for receipt in receipts[:resolved_limit]]
    payload = {
        "receiptStore": _project_relative_label(project, _controlled_write_receipt_dir(project)),
        "receiptCount": len(receipts),
        "receipts": summaries,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(
        "Write-path receipts: "
        f"{payload['receiptCount']} stored in {payload['receiptStore']}"
    )
    if not summaries:
        print("No write-path receipts found.")
        return 0
    for receipt in summaries:
        print(
            f"- {receipt.get('receiptId', '')}: "
            f"{receipt.get('operation', 'unknown')} -> {receipt.get('status', 'unknown')} "
            f"(files={receipt.get('touchedCount', 0)}, preflight={receipt.get('preflightRan', False)})"
        )
    return 0


def cmd_write_path_receipt_show(project: Path,
                                receipt_id: str,
                                json_output: bool = False) -> int:
    normalized_id = str(receipt_id or "").strip()
    if not normalized_id:
        message = "Receipt id is required."
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    receipt = _load_write_path_receipt(project, normalized_id)
    if not receipt:
        message = f"Receipt not found: {normalized_id}"
        if json_output:
            print(json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False))
        else:
            fail(message)
        return 1
    receipt_path = _write_path_receipt_path(project, normalized_id)
    payload = {
        "ok": True,
        "receiptPath": _project_relative_label(project, receipt_path),
        "receipt": receipt,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Receipt: {normalized_id}")
    print(f"Path: {_project_relative_label(project, receipt_path)}")
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


def _format_gate_cell(contract_by_id: dict[str, dict], gate_id: str) -> str:
    gate = contract_by_id.get(gate_id, {})
    nature = str(gate.get("nature", "?")).strip()
    requirement = str(gate.get("requirement", "?")).strip()
    location = str(gate.get("location", "?")).strip()
    return f"{nature} / {requirement} / {location}"


def _summarize_specialist_execution_modes(specialist_paths: list[dict]) -> dict[str, int]:
    specialist_execution_summary = {
        "human_mediated": 0,
        "cc_routed": 0,
        "auto_bounded": 0,
    }
    for path in specialist_paths:
        mode = str(path.get("execution_mode", "")).strip()
        if mode in specialist_execution_summary and path.get("active") is not False:
            specialist_execution_summary[mode] += 1
    return specialist_execution_summary


def _build_benchmark_matrix_rows() -> list[dict]:
    rows: list[dict] = []
    for host in BENCHMARK_HOST_ORDER:
        profile = _derive_host_profile(host)
        contract = _derive_host_gate_contract(profile)
        contract_by_id = {
            str(entry.get("id", "")).strip(): entry
            for entry in contract
            if isinstance(entry, dict)
        }
        evidence = HOST_BENCHMARK_EVIDENCE.get(host, HOST_BENCHMARK_EVIDENCE["other"])
        rows.append({
            "host": host,
            "label": profile["label"],
            "capabilityClass": profile["capabilityClass"],
            "inlineGate": _format_gate_cell(contract_by_id, "inline_gate"),
            "repoBoundaryGate": _format_gate_cell(contract_by_id, "repo_boundary_gate"),
            "reviewGate": _format_gate_cell(contract_by_id, "review_gate"),
            "verificationGate": _format_gate_cell(contract_by_id, "verification_gate"),
            "protectionModel": profile["protectionModel"],
            "summary": profile["summary"],
            "evidenceStatus": evidence["status"],
            "evidenceNotes": evidence["notes"],
            "evidenceArtifacts": list(evidence.get("artifacts", [])),
            "hostCoverage": _host_coverage_payload(profile),
        })
    return rows


def _build_local_benchmark_snapshot(project: Path) -> dict:
    gateway = _read_json_object(_control_plane_read_path(project, "gateway_config.json"))
    primary_host, enabled_hosts = _normalize_gateway_hosts(gateway)
    engagement = _load_engagement_runtime_config(_control_plane_read_path(project, "cc_engagement.json"))
    specialist_paths = _normalize_specialist_paths(engagement.get("specialist_paths", []))
    controlled_write = _controlled_write_status_payload(project)
    latest_receipt = controlled_write.get("latestReceipt", {})
    return {
        "primaryHost": primary_host,
        "enabledHosts": enabled_hosts,
        "tier": str(engagement.get("tier", "core")).strip() or "core",
        "backendPolicy": str(engagement.get("backend_policy", "")).strip(),
        "controlledWritePath": {
            "enabled": bool(controlled_write.get("enabled")),
            "mode": str(controlled_write.get("mode", "")).strip(),
            "manifestRequirement": str(controlled_write.get("manifestRequirement", "")).strip(),
            "preflightFitnessForApply": bool(controlled_write.get("preflightFitnessForApply")),
            "preflightFitnessBeforeApply": bool(controlled_write.get("preflightFitnessBeforeApply")),
            "latestReceipt": latest_receipt,
        },
        "specialistExecutionSummary": _summarize_specialist_execution_modes(specialist_paths),
        "controlPlaneArtifacts": [
            _project_relative_label(project, _control_plane_read_path(project, "cc_config.json")),
            _project_relative_label(project, _control_plane_read_path(project, "gateway_config.json")),
            _project_relative_label(project, _control_plane_read_path(project, "cc_engagement.json")),
        ],
    }


def _render_local_benchmark_snapshot_lines(local_snapshot: dict) -> list[str]:
    cw = local_snapshot.get("controlledWritePath", {})
    specialist_summary = local_snapshot.get("specialistExecutionSummary", {})
    latest_receipt = cw.get("latestReceipt", {})
    lines = [
        "## Local Dogfooding Snapshot",
        "",
        "This section reports the current local repo configuration. It is useful operational evidence, not a cross-host parity verdict.",
        "",
        f"- Primary host: `{local_snapshot.get('primaryHost', 'other')}`",
        f"- Enabled hosts: {', '.join(f'`{host}`' for host in local_snapshot.get('enabledHosts', [])) or '(none)'}",
        f"- Tier: `{local_snapshot.get('tier', 'core')}`",
        (
            "- Controlled write path: "
            f"`{cw.get('mode', 'off')} / {cw.get('manifestRequirement', 'optional')} / "
            f"{'shadow-preflight' if cw.get('preflightFitnessForApply') else 'no-shadow-preflight'}`"
        ),
        (
            "- Specialist execution summary: "
            f"human_mediated={specialist_summary.get('human_mediated', 0)}, "
            f"cc_routed={specialist_summary.get('cc_routed', 0)}, "
            f"auto_bounded={specialist_summary.get('auto_bounded', 0)}"
        ),
    ]
    if latest_receipt:
        lines.append(
            "- Latest write-path receipt: "
            f"`{latest_receipt.get('operation', 'unknown')}` -> `{latest_receipt.get('status', 'unknown')}` "
            f"({latest_receipt.get('receiptId', '')})"
        )
    else:
        lines.append("- Latest write-path receipt: none yet")
    artifact_list = ", ".join(
        f"`{artifact}`"
        for artifact in local_snapshot.get("controlPlaneArtifacts", [])
        if artifact
    )
    if artifact_list:
        lines.append(f"- Local control-plane artifacts: {artifact_list}")
    lines.append("")
    return lines


def _render_benchmark_matrix_markdown(rows: list[dict], local_snapshot: dict | None = None) -> str:
    lines = [
        "# Multi-Host Capability Matrix",
        "",
        f"Generated: {_utc_now_iso()}",
        "",
        "This matrix describes the CC integration model, not all vendor capabilities or tested host parity.",
        "Project configuration is not inspected. Named-host integration remains unverified; curated artifacts have their historical scope.",
        "See docs/cross-tool-guide.md for dated platform documentation and route-specific limitations.",
        "",
        "| Host | Capability Class | Inline Gate | Repo Boundary Gate | Review Gate | Verification Gate | Protection Model | Evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        host_label = f"{row['label']} (`{row['host']}`)"
        evidence_label = row["evidenceStatus"]
        if row["evidenceArtifacts"]:
            evidence_label += f" ({len(row['evidenceArtifacts'])} artifact(s))"
        lines.append(
            f"| {host_label} | {row['capabilityClass']} | {row['inlineGate']} | "
            f"{row['repoBoundaryGate']} | {row['reviewGate']} | {row['verificationGate']} | "
            f"{row['protectionModel']} | {evidence_label} |"
        )

    lines.extend([
        "",
        "## Notes",
        "",
    ])
    for row in rows:
        lines.append(f"### {row['label']} (`{row['host']}`)")
        lines.append("")
        lines.append(f"- Summary: {row['summary']}")
        lines.append(f"- Evidence: {row['evidenceStatus']} - {row['evidenceNotes']}")
        lines.extend(f"- {line}" for line in _format_host_coverage_lines(row["hostCoverage"]))
        if row["evidenceArtifacts"]:
            artifact_list = ", ".join(f"`{artifact}`" for artifact in row["evidenceArtifacts"])
            lines.append(f"- Artifacts: {artifact_list}")
        lines.append("")

    if local_snapshot:
        lines.extend(_render_local_benchmark_snapshot_lines(local_snapshot))

    return "\n".join(lines).rstrip() + "\n"


def _render_local_benchmark_report_markdown(local_snapshot: dict) -> str:
    lines = [
        "# Local Dogfooding Evidence",
        "",
        f"Generated: {_utc_now_iso()}",
        "",
        "This report captures the current local ControlCoding operating state for the repo. It is operational evidence for the configured host/workflow, not a cross-host parity claim.",
        "",
    ]
    lines.extend(_render_local_benchmark_snapshot_lines(local_snapshot))
    return "\n".join(lines).rstrip() + "\n"


def _benchmark_report_payload(project: Path) -> dict:
    rows = _build_benchmark_matrix_rows()
    local_snapshot = _build_local_benchmark_snapshot(project)
    return {
        "ok": True,
        "generatedAt": _utc_now_iso(),
        "matrixRows": rows,
        "localSnapshot": local_snapshot,
    }


def _benchmark_difference_rows(baseline: dict, current: dict) -> list[dict]:
    differences: list[dict] = []
    baseline_snapshot = baseline.get("localSnapshot", {}) if isinstance(baseline.get("localSnapshot"), dict) else {}
    current_snapshot = current.get("localSnapshot", {}) if isinstance(current.get("localSnapshot"), dict) else {}
    fields = sorted(set(baseline_snapshot) | set(current_snapshot))
    for field in fields:
        left = baseline_snapshot.get(field)
        right = current_snapshot.get(field)
        if left != right:
            differences.append({"field": field, "baseline": left, "current": right})
    return differences


def _render_benchmark_evidence_report(payload: dict) -> str:
    lines = [
        "# Benchmark Evidence Report",
        "",
        f"Generated: {payload.get('generatedAt', '')}",
        "",
        "## Host Evidence Summary",
        "",
        "| Host | Capability Class | Protection Model | Evidence |",
        "|---|---|---|---|",
    ]
    for row in payload.get("matrixRows", []):
        lines.append(
            f"| {row.get('host', '')} | {row.get('capabilityClass', '')} | "
            f"{row.get('protectionModel', '')} | {row.get('evidenceStatus', '')} |"
        )
    lines.extend(["", "## Local Snapshot", ""])
    lines.extend(_render_local_benchmark_snapshot_lines(payload.get("localSnapshot", {})))
    return "\n".join(lines).rstrip() + "\n"


def cmd_benchmark_run(project: Path,
                      output_path: Path | None = None,
                      json_output: bool = False) -> int:
    payload = _benchmark_report_payload(project)
    target = output_path or BENCHMARK_RUN_OUTPUT
    target = target if target.is_absolute() else project / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        ok(f"Generated benchmark run at {target}")
    return 0


def cmd_benchmark_compare(project: Path,
                          baseline_path: Path,
                          current_path: Path,
                          json_output: bool = False) -> int:
    baseline = _read_json_object(baseline_path)
    current = _read_json_object(current_path)
    differences = _benchmark_difference_rows(baseline, current)
    payload = {
        "ok": True,
        "baselinePath": str(baseline_path),
        "currentPath": str(current_path),
        "differences": differences,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Benchmark differences: {len(differences)}")
        for difference in differences:
            print(f"- {difference['field']}: {difference['baseline']} -> {difference['current']}")
    return 0


def cmd_benchmark_report(project: Path,
                         output_path: Path | None = None,
                         json_output: bool = False) -> int:
    payload = _benchmark_report_payload(project)
    target = output_path or BENCHMARK_REPORT_OUTPUT
    target = target if target.is_absolute() else project / target
    target.parent.mkdir(parents=True, exist_ok=True)
    markdown = _render_benchmark_evidence_report(payload)
    target.write_text(markdown, encoding="utf-8")
    result = {**payload, "outputPath": str(target)}
    if json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        ok(f"Generated benchmark evidence report at {target}")
    return 0


def cmd_benchmark_matrix_generate(project: Path,
                                  output_path: Path | None = None,
                                  json_output: bool = False) -> int:
    rows = _build_benchmark_matrix_rows()
    local_snapshot = _build_local_benchmark_snapshot(project)
    target = output_path or BENCHMARK_MATRIX_OUTPUT
    target = target if target.is_absolute() else project / target
    target.parent.mkdir(parents=True, exist_ok=True)
    markdown = _render_benchmark_matrix_markdown(rows, local_snapshot=local_snapshot)
    target.write_text(markdown, encoding="utf-8")

    payload = {
        "ok": True,
        "generatedAt": _utc_now_iso(),
        "outputPath": str(target),
        "rows": rows,
        "localSnapshot": local_snapshot,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    ok(f"Generated benchmark capability matrix at {target}")
    info("This is a CC integration model; named-host delivery is unverified and vendor capabilities are documented separately.")
    return 0


def cmd_benchmark_local_report(project: Path,
                               output_path: Path | None = None,
                               json_output: bool = False) -> int:
    local_snapshot = _build_local_benchmark_snapshot(project)
    target = output_path or BENCHMARK_LOCAL_REPORT_OUTPUT
    target = target if target.is_absolute() else project / target
    target.parent.mkdir(parents=True, exist_ok=True)
    markdown = _render_local_benchmark_report_markdown(local_snapshot)
    target.write_text(markdown, encoding="utf-8")

    payload = {
        "ok": True,
        "generatedAt": _utc_now_iso(),
        "outputPath": str(target),
        "localSnapshot": local_snapshot,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    ok(f"Generated local dogfooding evidence report at {target}")
    info("This is local operational evidence for the current repo configuration, not a cross-host benchmark verdict.")
    return 0


def cmd_host_status(project: Path, json_output: bool = False) -> int:
    """Show primary and enabled hosts for this project."""
    gateway = _read_json_object(_control_plane_read_path(project, "gateway_config.json"))
    primary_host, enabled_hosts = _normalize_gateway_hosts(gateway)
    host_profile = _derive_host_profile(primary_host)
    gate_contract = _derive_host_gate_contract(host_profile)
    engagement = _load_engagement_runtime_config(_control_plane_read_path(project, "cc_engagement.json"))
    specialist_paths = _normalize_specialist_paths(engagement.get("specialist_paths", []))
    specialist_execution_summary = _summarize_specialist_execution_modes(specialist_paths)
    payload = {
        "primaryHost": primary_host,
        "enabledHosts": enabled_hosts,
        "hostProfile": host_profile,
        "hostCoverage": _host_coverage_payload(host_profile, gateway),
        "gateContract": gate_contract,
        "controlledWritePath": _controlled_write_status_payload(project),
        "specialistConsentMatrix": specialist_paths,
        "specialistExecutionSummary": specialist_execution_summary,
        "derivedContextFiles": {
            host: _host_context_export_spec(host)["relative_path"]
            for host in enabled_hosts
            if _host_context_export_spec(host) is not None
        },
    }

    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Primary host: {_format_user_host_label(primary_host)} ({primary_host})")
    print(
        "Protection model: "
        f"{host_profile['protectionModel']} "
        f"({host_profile['capabilityClass']}, inline={host_profile['inlineBoundaryGate']}, "
        f"repo={host_profile['repoBoundaryGate']}, review={host_profile['reviewGate']})"
    )
    if host_profile.get("contextFile"):
        print(f"Primary context file: {host_profile['contextFile']}")
    print(f"Summary: {host_profile['summary']}")
    for line in _format_host_coverage_lines(payload["hostCoverage"]):
        print(line)
    print("Gate contract (CC integration model):")
    for line in _format_host_gate_contract_lines(host_profile):
        print(f"  - {line}")
    controlled_write = payload["controlledWritePath"]
    print(
        "Controlled write path: "
        f"{controlled_write['mode']} "
        f"({'enabled' if controlled_write['enabled'] else 'disabled'})"
    )
    if controlled_write["enabled"]:
        note = "mechanical before apply" if controlled_write["preventsProtectedWritesBeforeApply"] else controlled_write["prereqIssue"]
        print(f"  - {note}")
        if controlled_write.get("preflightFitnessForApply"):
            preflight_note = (
                "shadow fitness preflight before apply"
                if controlled_write.get("preflightFitnessBeforeApply")
                else f"shadow preflight unavailable: {controlled_write.get('preflightPrereqIssue') or controlled_write.get('applyPrereqIssue')}"
            )
            print(f"  - {preflight_note}")
    if enabled_hosts:
        labels = ", ".join(
            f"{_format_user_host_label(host)} ({host})"
            for host in enabled_hosts
        )
        print(f"Enabled hosts: {labels}")
    else:
        print("Enabled hosts: (none configured)")

    derived_files = payload["derivedContextFiles"]
    if derived_files:
        print("Derived context files:")
        for host, relative_path in derived_files.items():
            print(f"  - {host}: {relative_path}")
    else:
        print("Derived context files: none for the currently enabled hosts")
    if specialist_paths:
        try:
            global_max_calls = int(engagement.get("budget_policy", {}).get("max_calls", 0))
        except (TypeError, ValueError, AttributeError):
            global_max_calls = 0
        print(
            "Specialist execution modes: "
            f"human_mediated={specialist_execution_summary['human_mediated']}, "
            f"cc_routed={specialist_execution_summary['cc_routed']}, "
            f"auto_bounded={specialist_execution_summary['auto_bounded']}"
        )
        print("Specialist/backend consent matrix:")
        for line in _format_specialist_matrix_lines(specialist_paths, global_max_calls):
            print(f"  - {line}")
    elif engagement.get("tier") in {"agents", "studio"}:
        print("Specialist/backend consent matrix: missing for current Agents/Studio engagement config")
    return 0


def cmd_host_switch(project: Path,
                    host: str,
                    preview_only: bool = False) -> int:
    """Switch the primary user host and sync its derived context file."""
    target_host = str(host).strip()
    if target_host not in _GATEWAY_VALID_USER_HOSTS:
        print(
            "Error: host must be one of "
            + ", ".join(sorted(_GATEWAY_VALID_USER_HOSTS))
        )
        return 1

    gateway_path = _control_plane_path(project, "gateway_config.json")
    gateway = _read_json_object(gateway_path)
    previous_host, enabled_hosts = _normalize_gateway_hosts(gateway)

    gateway["userHost"] = target_host
    gateway["enabledHosts"] = _normalize_enabled_hosts(enabled_hosts, target_host)
    gateway["hostProfile"] = _derive_host_profile(target_host)

    adapter_plan = _adapter_plan_payload(
        project,
        host=target_host,
        operation="switch",
    )
    _emit_adapter_preview(adapter_plan)
    if not adapter_plan["preflightOk"]:
        fail("Host switch adapter preflight is blocked.")
        return 1

    if preview_only:
        info("Preview only - gateway, host assets, and adapters were not changed.")
        return 0

    _write_json_atomic(gateway_path, gateway)

    if target_host == previous_host:
        ok(f"Primary host remains {target_host}")
    else:
        ok(f"Switched primary host from {previous_host} to {target_host}")
    if target_host not in enabled_hosts:
        ok(f"Added {target_host} to enabledHosts")

    host_instructions = gateway.get("hostInstructions")
    launcher_files = _write_host_integration_assets(project, target_host, host_instructions)
    if launcher_files:
        ok(f"Regenerated {len(launcher_files)} host integration asset(s) for {target_host}")

    spec = _host_context_export_spec(target_host)
    if spec is None:
        info(
            f"No derived host-context file is defined for {target_host}; "
            f"{CANONICAL_CONTEXT_FILENAME} (or legacy {LEGACY_CONTEXT_FILENAME}) remains the canonical source."
        )
        return 0

    transaction = _apply_adapter_transaction(project, adapter_plan["entries"])
    if transaction["ok"]:
        ok(f"Synced {spec['file_label']} for {_format_user_host_label(target_host)}")
        return 0

    fail(f"Host switch adapter sync failed: {transaction.get('error', 'unknown error')}")
    if not transaction.get("rollbackOk", True):
        fail("Host switch adapter rollback was incomplete.")
    return 1


def cmd_host_compare(project: Path,
                     left_host: str,
                     right_host: str,
                     json_output: bool = False) -> int:
    left = _derive_host_profile(left_host)
    right = _derive_host_profile(right_host)
    fields = sorted(set(left) | set(right))
    differences = [
        {"field": field, "left": left.get(field), "right": right.get(field)}
        for field in fields
        if left.get(field) != right.get(field)
    ]
    payload = {
        "ok": True,
        "left": left,
        "right": right,
        "differences": differences,
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Host compare: {left['userHost']} -> {right['userHost']}")
        print("CC integration models only; project configuration not inspected and host delivery unverified.")
        for difference in differences:
            print(f"- {difference['field']}: {difference['left']} -> {difference['right']}")
    return 0


def cmd_host_migrate_plan(project: Path,
                          from_host: str,
                          to_host: str,
                          json_output: bool = False) -> int:
    target_spec = _host_context_export_spec(to_host)
    target_context_file = target_spec["relative_path"] if target_spec else ""
    payload = {
        "ok": True,
        "from": _derive_host_profile(from_host),
        "to": _derive_host_profile(to_host),
        "targetContextFile": target_context_file,
        "steps": [
            {
                "id": "compare",
                "command": f"cc host compare {from_host} {to_host}",
                "description": "Review host gate and context differences.",
            },
            {
                "id": "diff",
                "command": f"cc context diff --host {to_host}",
                "description": "Inspect generated target host context before switching.",
            },
            {
                "id": "switch",
                "command": f"cc host switch {to_host}",
                "description": "Switch primary host and regenerate target context.",
            },
            {
                "id": "doctor",
                "command": "cc doctor",
                "description": "Validate the resulting control-plane state.",
            },
        ],
    }
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Host migration plan: {from_host} -> {to_host}")
        for step in payload["steps"]:
            print(f"- {step['command']}")
    return 0


def _extract_file_description(filepath: Path, content: str | None = None) -> str:
    """Extract first docstring line or heading from a file for index stubs."""
    try:
        if content is None:
            content = filepath.read_text(encoding="utf-8")
        if filepath.suffix == ".py":
            import ast as _ast
            try:
                tree = _ast.parse(content)
                doc = _ast.get_docstring(tree)
                if doc:
                    return doc.split("\n")[0].strip()[:120]
            except SyntaxError:
                pass
        elif filepath.suffix == ".md":
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()[:120]
    except (OSError, UnicodeError):
        pass
    return "[description needed]"


_ARCHITECTURE_INDEX_TRACKED_AREAS = (
    (("templates", "scripts"), "*.py", "MCP Servers (`templates/scripts/`)"),
    (("templates", "hooks"), "*.py", "Hooks (`templates/hooks/`)"),
    (("dev", "design"), "*.md", "Design Documents (`dev/design/`)"),
    (("scripts",), "*.py", "CLI Tool (`scripts/cc.py`)"),
)
_ARCHITECTURE_INDEX_SKIP_NAMES = {"__init__.py", "__pycache__"}


def _capture_architecture_index_file(path: Path, label: str) -> tuple[bytes | None, dict | None, str]:
    return capture_regular_file_bytes(path, label)


def _architecture_index_relative_path(project: Path) -> Path:
    default_path = Path("dev/ARCHITECTURE_INDEX.md")
    if (project / default_path).exists() or (project / default_path).is_symlink():
        return default_path
    manifest_raw, _record, manifest_reason = capture_regular_file_bytes(project / _RELEASE_MANIFEST_FILE, "architecture_index_manifest")
    try:
        manifest = {} if manifest_reason or manifest_raw is None else json.loads(manifest_raw.decode("utf-8"))
        configured = manifest.get("architectureIndex") if isinstance(manifest, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        configured = None
    if configured is None: return default_path
    relative_path = Path(configured) if isinstance(configured, str) else Path()
    valid = isinstance(configured, str) and bool(configured) and configured == configured.strip() and "\\" not in configured and not relative_path.is_absolute() and not relative_path.drive and ".." not in relative_path.parts and len(relative_path.parts) >= 2 and relative_path.parts[0] == "docs" and relative_path.suffix.lower() == ".md" and relative_path.as_posix() == configured
    if not valid: raise ValueError("invalid release architectureIndex")
    return relative_path


def _observed_architecture_index_payload(project: Path) -> dict:
    relative_path = _architecture_index_relative_path(project)
    index_path = project / relative_path

    def capture_release_aware(path: Path, label: str):
        return _capture_architecture_index_file(index_path if label == "architecture_index" else path, label)

    payload = observed_architecture_index_payload(project, _ARCHITECTURE_INDEX_TRACKED_AREAS, _ARCHITECTURE_INDEX_SKIP_NAMES, _extract_file_description, capture_release_aware)
    payload["path"] = relative_path.as_posix()
    if not payload.get("available") and payload.get("issues"): payload["issues"] = [issue.replace("dev/ARCHITECTURE_INDEX.md", relative_path.as_posix()) for issue in payload["issues"]]
    return payload


def _architecture_index_payload(project: Path) -> dict:
    """Return read-only architecture index coverage status without leaking failures."""
    try:
        return _observed_architecture_index_payload(project)
    except Exception:
        return architecture_index_unavailable("architecture_index_unreadable")


def cmd_index(project: Path, fix: bool = False, check: bool = False) -> int:
    """Report architecture index gaps; optionally add stubs or fail for CI."""
    payload = _architecture_index_payload(project)
    if not payload["available"]: print(payload["issues"][0]); return 1

    index_path = project / payload["path"]
    index_content = index_path.read_text(encoding="utf-8")
    missing = payload["missing"]

    if not missing: print("ARCHITECTURE_INDEX.md is up to date - no missing files."); return 0

    print(f"Files not in ARCHITECTURE_INDEX.md: {len(missing)}")
    for item in missing:
        print(f"  [{item['section']}] {item['name']}")
        print(f"    {item['description']}")

    if not fix:
        print("\nRun `cc index --fix` to add stub entries automatically.")
        return 1 if check else 0

    # --fix: insert stub rows into the correct sections
    lines = index_content.split("\n")
    additions = 0

    for item in missing:
        name = item["name"]
        section_header = item["section"]
        desc = item["description"]
        # Find the section
        section_line = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("##") and section_header.split("(")[0].strip() in line:
                section_line = i
                break
        if section_line == -1:
            print(f"  WARNING: section '{section_header}' not found in index - skipping {name}")
            continue

        # Find the last table row in this section (before the next ## or end)
        last_row = -1
        for i in range(section_line + 1, len(lines)):
            if lines[i].startswith("## ") or lines[i].startswith("---"):
                break
            if lines[i].startswith("|") and not lines[i].startswith("| ---") and not lines[i].startswith("| File"):
                last_row = i

        stub_row = f"| `{name}` | [NEEDS_DESCRIPTION] | {desc} |"
        if last_row != -1:
            lines.insert(last_row + 1, stub_row)
            additions += 1
        else:
            print(f"  WARNING: could not find table in section for {name}")

    if additions > 0:
        index_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nAdded {additions} stub entries to ARCHITECTURE_INDEX.md.")
        print("Review and complete the [NEEDS_DESCRIPTION] entries.")

    return 1 if (check and missing) else 0


def _safe_work_start_payload(label: str, builder):
    try:
        return builder()
    except Exception as exc:  # defensive aggregation for startup diagnostics
        return {
            "ok": False,
            "available": False,
            "label": label,
            "message": str(exc),
        }


def _work_start_readiness(payload: dict) -> dict:
    red_reasons = []
    yellow_reasons = []
    startup = payload.get("startup", {})
    op_index = payload.get("opIndex", {})
    architecture_index = payload.get("architectureIndex", {})
    context = payload.get("context", {})
    retrieval = payload.get("retrieval", {})
    working_tree = payload.get("workingTree", {})
    project_plane = payload.get("projectPlane", {})

    if not startup.get("ok", False):
        red_reasons.append("memory startup payload could not be built")
    if not op_index.get("ok", False):
        red_reasons.append("operations index payload could not be built")

    if not architecture_index.get("ok", False):
        if architecture_index.get("available"):
            yellow_reasons.append(
                f"architecture index has {architecture_index.get('missingCount', 0)} missing file(s)"
            )
        else:
            yellow_reasons.append("architecture index is missing")
    if not context.get("ok", False):
        yellow_reasons.append("host context is missing, stale, unsupported, or not canonical")
    if project_plane.get("embeddedVsExternalDifferent"):
        yellow_reasons.append("embedded and standalone ControlWork content differs")
    if project_plane.get("actionableSemanticDrift"):
        yellow_reasons.append("Project Plane has actionable semantic drift")
    if project_plane.get("available") is False:
        yellow_reasons.append("Project Plane status is unknown because its inputs were not observed")
    if not retrieval.get("available", False):
        yellow_reasons.append(retrieval.get("message") or "memory retrieve unavailable")
    tracked_dirty_count = working_tree.get("trackedDirtyCount")
    if tracked_dirty_count is None:
        yellow_reasons.append("working-tree status is unknown because Git was not observed")
    elif isinstance(tracked_dirty_count, int) and not isinstance(tracked_dirty_count, bool) and tracked_dirty_count > 0:
        yellow_reasons.append(
            f"working tree has {tracked_dirty_count} file(s) to review"
        )
    for warning in op_index.get("warnings", []):
        yellow_reasons.append(str(warning))

    red_reasons = list(dict.fromkeys(red_reasons))
    yellow_reasons = list(dict.fromkeys(yellow_reasons))
    state = "RED" if red_reasons else "YELLOW" if yellow_reasons else "GREEN"
    return {
        "state": state,
        "ok": state != "RED",
        "redReasons": red_reasons,
        "yellowReasons": yellow_reasons,
    }


def _work_start_next_commands(payload: dict) -> list[str]:
    commands = []
    topic = payload.get("topic") or "current focus"
    scope = payload.get("scope") or "general"
    if not payload.get("architectureIndex", {}).get("ok", False):
        commands.append("python scripts/cc.py index --project-root .")
    if not payload.get("context", {}).get("ok", False):
        commands.append("python scripts/cc.py context check --project-root .")
    project_plane = payload.get("projectPlane", {})
    if project_plane.get("present") is True or project_plane.get("available") is False:
        commands.append("python scripts/cc.py memory work-status --project-root .")
    tracked_dirty_count = payload.get("workingTree", {}).get("trackedDirtyCount")
    if tracked_dirty_count is None or (
        isinstance(tracked_dirty_count, int)
        and not isinstance(tracked_dirty_count, bool)
        and tracked_dirty_count > 0
    ):
        commands.append("git status --short")
    commands.append(
        f"python scripts/cc.py memory retrieve --project-root . {json.dumps(str(topic))} --scope {scope} --limit {payload.get('limit', 10)}"
    )
    return list(dict.fromkeys(commands))


def _work_start_text(payload: dict) -> str:
    readiness = payload["readiness"]
    project_plane = payload.get("projectPlane", {})
    retrieval = payload.get("retrieval", {})
    def observed(value) -> str:
        return "unknown (not observed)" if value is None else str(value)

    lines = [
        "Work start readiness",
        f"  State: {readiness['state']}",
        "  Mode: read-only, no hidden writes",
        f"  Scope: {payload.get('scope')}",
        f"  Topic: {payload.get('topic') or '(none)'}",
        "",
        *_memory_health_text_lines(payload.get("health", {})),
        "",
        "Checks",
        f"  Memory startup: {'ok' if payload.get('startup', {}).get('ok') else 'failed'}",
        f"  Operations index: {'ok' if payload.get('opIndex', {}).get('ok') else 'failed'}",
        f"  Architecture index: {'ok' if payload.get('architectureIndex', {}).get('ok') else 'attention'}",
        f"  Context sync: {'ok' if payload.get('context', {}).get('ok') else 'attention'}",
        f"  Project Plane present: {observed(project_plane.get('present'))}",
        f"  ControlWork drift: {observed(project_plane.get('embeddedVsExternalDifferent'))}",
        f"  Memory retrieve: {'available' if retrieval.get('available') else 'unavailable'}",
        f"  Working tree dirty files: {'unknown (not observed)' if payload.get('workingTree', {}).get('trackedDirtyCount') is None else payload.get('workingTree', {}).get('trackedDirtyCount')}",
        "",
        "Attention",
    ]
    reasons = readiness["redReasons"] + readiness["yellowReasons"]
    if reasons:
        for reason in reasons[:12]:
            lines.append(f"  - {reason}")
    else:
        lines.append("  - none")
    lines.extend(["", "Next commands"])
    for command in payload.get("nextCommands", []):
        lines.append(f"  - {command}")
    return "\n".join(lines)


def _work_start_retrieve_payload(project: Path, topic: str, scope: str, limit: int, observation: dict | None = None) -> dict:
    return projected_retrieval_payload(project, topic, scope, observation=observation)


def _build_work_start_payload(
    project: Path,
    topic: str = "",
    scope: str = "dev",
    limit: int = 10,
) -> dict:
    project = project.resolve()
    scope = (scope or "dev").strip().lower() or "dev"
    topic = str(topic or "").strip()
    limit = max(1, min(int(limit or 10), 50))
    observation = observe_freshness(project)
    startup, op_index = _work_start_startup_payloads(project, scope=scope, topic=topic, observation=observation)
    architecture_index = _architecture_index_payload(project)
    context = _safe_work_start_payload(
        "context",
        lambda: _context_sync_payload(project),
    )
    retrieval = _work_start_retrieve_payload(project, topic, scope, limit, observation=observation)
    retrieval_matches = retrieval.get("matches") if isinstance(retrieval.get("matches"), list) else None

    planes = op_index.get("planes", {}) if isinstance(op_index, dict) else {}
    project_plane = planes.get("project", {}) if isinstance(planes.get("project"), dict) else {}
    working_tree = op_index.get("workingTree", {}) if isinstance(op_index, dict) else {}
    payload = {
        "ok": True,
        "command": "work-start",
        "projectRoot": str(project),
        "scope": scope,
        "topic": topic,
        "limit": limit,
        "readOnly": True,
        "hiddenWrites": False,
        "health": observation,
        "startup": startup,
        "opIndex": op_index,
        "architectureIndex": architecture_index,
        "context": context,
        "projectPlane": project_plane,
        "retrieval": {
            "ok": retrieval.get("ok", False),
            "available": retrieval.get("available", False),
            "message": retrieval.get("message", ""),
            "query": retrieval.get("query", topic),
            "scope": retrieval.get("scope", scope),
            "matchCount": len(retrieval_matches) if retrieval_matches is not None else None,
            "matches": retrieval_matches[:limit] if retrieval_matches is not None else None,
            "health": observation,
        },
        "workingTree": working_tree,
        "actionQueue": op_index.get("actionQueue", []) if isinstance(op_index, dict) else [],
    }
    payload["readiness"] = _work_start_readiness(payload)
    payload["nextCommands"] = _work_start_next_commands(payload)
    return payload


def cmd_work_start(
    project: Path,
    topic: str = "",
    scope: str = "dev",
    limit: int = 10,
    json_output: bool = False,
) -> int:
    """Read current work context before editing without mutating project state."""
    payload = _build_work_start_payload(project, topic=topic, scope=scope, limit=limit)

    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(_work_start_text(payload))
    return 0 if payload["readiness"]["ok"] else 1


def _chat_start_project_packet(
    project: Path,
    scope: str,
    topic: str,
    limit: int,
    include_legacy: bool,
) -> dict:
    project_scope = {
        "dev": "implementation",
        "code": "implementation",
        "coding": "implementation",
        "docs": "writing",
        "documentation": "writing",
        "architecture_review": "architecture",
    }.get(scope, scope)
    if project_scope not in work_features.CONTEXT_SCOPES:
        project_scope = "general"
    packet = guarded_project_plane_packet(
        project,
        lambda: work_features.build_context_pack(
            project, scope=project_scope, topic=topic, limit=limit,
            include_legacy=include_legacy, refresh_views=False,
        ),
        observer=observe_project_plane_inputs,
        capture=capture_project_plane_inputs,
    )
    return {**packet, "scope": project_scope}


def _chat_start_text(payload: dict) -> str:
    work_start = payload.get("workStart", {})
    readiness = work_start.get("readiness", {})
    project_packet = payload.get("projectPlanePacket", {})
    topic, scope = payload.get("topic") or "current work", payload.get("scope") or "general"
    retrieval = work_start.get("retrieval", {})

    lines = [
        "Chat start packet",
        f"  State: {readiness.get('state', 'UNKNOWN')}",
        "  Mode: AI-facing read-only startup, no hidden writes",
        "  User action: ask the AI to start the project and load memory; do not ask the user to run this command for routine startup",
        f"  Project: {payload.get('projectRoot')}",
        f"  Scope: {scope}",
        f"  Topic: {topic}",
        "",
        *_memory_health_text_lines(payload.get("health", {})),
        "",
        "AI startup contract",
        "  - Treat this packet as the first context to load before answering or editing.",
        "  - Keep current work, prior work, blockers, and next steps in context for this project folder.",
        "  - Use active/current memory first; treat needs_review, legacy, and superseded material as warning-only.",
        "  - If the topic is too broad or the packet is missing obvious context, ask one concise question before editing.",
        "  - Before code edits, inspect the relevant files and keep unrelated dirty work unchanged.",
        "  - Before closeout, run work-close with summary, tests, and next action.",
        "",
        "Loaded memory",
        "  - Dev memory retrieve: %s" % (f"available ({retrieval.get('matchCount')} match(es))" if retrieval.get("available") else "unavailable (no startup query was run)"),
        f"  - Project Plane packet: {'loaded' if project_packet.get('ok') else 'not loaded'}",
    ]
    if project_packet.get("message"):
        lines.append(f"  - Project Plane note: {project_packet.get('message')}")
    if project_packet.get("scope"):
        lines.append(f"  - Project Plane scope: {project_packet.get('scope')}")

    lines.extend(["", "Recommended closeout command"])
    escaped_topic = json.dumps(str(topic))
    lines.append(
        f"  python scripts/cc.py work-close --project-root . --topic {escaped_topic} --summary \"<what changed>\" --test \"<evidence>\" --next-action \"<next step>\""
    )

    lines.extend(["", "Work Start Readiness", "```text", _work_start_text(work_start), "```"])

    lines.extend(["", "Project Plane Context"])
    if project_packet.get("packetMarkdown"):
        lines.extend(["```markdown", project_packet["packetMarkdown"].rstrip(), "```"])
    else:
        lines.append(project_packet.get("message") or "No Project Plane context available.")

    return "\n".join(lines)


def cmd_chat_start(
    project: Path,
    topic: str = "",
    scope: str = "dev",
    limit: int = 10,
    include_legacy: bool = False,
    json_output: bool = False,
) -> int:
    """Print one AI-ready startup packet for a host AI without mutating state."""
    project = project.resolve()
    scope = (scope or "dev").strip().lower() or "dev"
    topic = str(topic or "").strip()
    limit = max(1, min(int(limit or 10), 30))
    work_start = _build_work_start_payload(project, topic=topic, scope=scope, limit=limit)
    project_packet = _chat_start_project_packet(
        project,
        scope=scope,
        topic=topic,
        limit=limit,
        include_legacy=include_legacy,
    )
    payload = {
        "ok": work_start.get("readiness", {}).get("ok", False),
        "command": "chat-start",
        "projectRoot": str(project),
        "scope": scope,
        "topic": topic,
        "limit": limit,
        "readOnly": True,
        "hiddenWrites": False,
        "health": work_start.get("health", {}),
        "includeLegacy": include_legacy,
        "workStart": work_start,
        "projectPlanePacket": project_packet,
    }
    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(_chat_start_text(payload))
    return 0 if payload["ok"] else 1


_HOST_CONTEXT_FILENAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".clinerules",
    ".windsurfrules",
}


def _work_close_file_areas(path: str) -> list[str]:
    normalized = str(path or "").replace("\\", "/")
    name = PurePosixPath(normalized).name
    areas = []
    if normalized == CANONICAL_CONTEXT_FILENAME:
        areas.append("canonical_context")
    if name in _HOST_CONTEXT_FILENAMES or normalized.startswith(".cursor/rules/"):
        areas.append("host_context")
    if normalized.startswith("scripts/") or normalized.startswith("templates/"):
        areas.append("code")
    if normalized.startswith("tests/"):
        areas.append("tests")
    if normalized == "README.md" or normalized.startswith("docs/"):
        areas.append("public_docs")
    if normalized.startswith("dev/"):
        areas.append("dev_docs")
    if normalized.startswith("devlog/"):
        areas.append("devlog")
    if normalized == CONTROLWORK_CONTEXT_FILENAME or normalized.startswith(".controlwork/"):
        areas.append("controlwork")
    if normalized.startswith(".controlcoding/") or normalized.startswith(".controlwork/"):
        areas.append("local_runtime_state")
    if not areas:
        areas.append("other")
    return areas


def _work_close_area_summary(changes: list[dict]) -> dict:
    summary: dict[str, list[str]] = {}
    for item in changes:
        path = str(item.get("path") or "")
        for area in _work_close_file_areas(path):
            summary.setdefault(area, []).append(path)
    return {key: sorted(set(paths)) for key, paths in sorted(summary.items())}


def _work_close_required_actions(payload: dict) -> list[dict]:
    actions = []
    areas = payload.get("areas", {})
    context = payload.get("context", {})
    architecture_index = payload.get("architectureIndex", {})
    provided = payload.get("providedEvidence", {})

    def add(kind: str, command: str, reason: str, required: bool = True) -> None:
        item = {"kind": kind, "command": command, "reason": reason, "required": required}
        if item not in actions:
            actions.append(item)

    if areas.get("code") or areas.get("tests"):
        add(
            "test",
            "python -m pytest tests/test_cc_cli.py -q -p no:cacheprovider",
            "code or CLI tests changed; run targeted validation or explain why another test is better",
        )
    if areas.get("code") or areas.get("dev_docs"):
        add(
            "architecture_index",
            "python scripts/cc.py index --project-root .",
            "code or durable development docs changed; check architecture index coverage",
        )
    if areas.get("canonical_context") or areas.get("host_context") or not context.get("ok", False):
        add(
            "context",
            "python scripts/cc.py context check --project-root . --host codex_cli",
            "canonical or host context changed; verify exported host context",
        )
    if areas.get("public_docs"):
        add(
            "truth",
            "python scripts/cc.py truth check --include-docs --project-root .",
            "public docs changed; verify command truth and public claims",
        )
    if areas.get("controlwork"):
        add(
            "controlwork",
            "python scripts/cc.py memory work-status --project-root .",
            "ControlWork state or context changed; inspect embedded/standalone drift",
        )
    if not provided.get("summary"):
        add(
            "summary",
            "python scripts/cc.py work-close --project-root . --topic <topic> --summary <summary>",
            "closeout summary is missing",
        )
    if (areas.get("code") or areas.get("tests")) and not provided.get("tests"):
        add(
            "test_evidence",
            "python scripts/cc.py work-close --project-root . --test <command>",
            "test evidence was not passed to work-close",
            required=False,
        )
    if not architecture_index.get("ok", False):
        add(
            "index_attention",
            "python scripts/cc.py index --project-root .",
            "architecture index currently reports attention",
            required=False,
        )
    return actions


def _work_close_readiness(payload: dict) -> dict:
    red_reasons = []
    yellow_reasons = []
    provided = payload.get("providedEvidence", {})
    context = payload.get("context", {})
    architecture_index = payload.get("architectureIndex", {})
    areas = payload.get("areas", {})
    working_tree = payload.get("workingTree", {})

    if not payload.get("opIndex", {}).get("ok", False):
        red_reasons.append("operations index payload could not be built")
    if areas.get("local_runtime_state"):
        yellow_reasons.append("local runtime state is present in the working tree; do not commit it")
    if not context.get("ok", False):
        yellow_reasons.append("host context check needs attention")
    if not architecture_index.get("ok", False):
        yellow_reasons.append(
            f"architecture index has {architecture_index.get('missingCount', 0)} missing file(s)"
        )
    if not provided.get("summary"):
        yellow_reasons.append("closeout summary was not provided")
    if (areas.get("code") or areas.get("tests")) and not provided.get("tests"):
        yellow_reasons.append("code or tests changed without test evidence passed to work-close")
    if int(working_tree.get("trackedDirtyCount") or 0):
        yellow_reasons.append(
            f"working tree has {working_tree.get('trackedDirtyCount')} file(s) to review"
        )

    state = "RED" if red_reasons else "YELLOW" if yellow_reasons else "GREEN"
    return {
        "state": state,
        "ok": state != "RED",
        "redReasons": red_reasons,
        "yellowReasons": yellow_reasons,
        "readyForCommit": state == "GREEN",
    }


def _work_close_text(payload: dict) -> str:
    readiness = payload["readiness"]
    provided = payload["providedEvidence"]
    working_tree = payload.get("workingTree", {})
    lines = [
        "Work close checklist",
        f"  State: {readiness['state']}",
        "  Mode: read-only, no commit, no push",
        f"  Topic: {payload.get('topic') or '(none)'}",
        f"  Summary provided: {bool(provided.get('summary'))}",
        f"  Tests reported: {len(provided.get('tests', []))}",
        f"  Working tree dirty files: {working_tree.get('trackedDirtyCount', 0)}",
        "",
        "Impacted areas",
    ]
    areas = payload.get("areas", {})
    if areas:
        for area, paths in areas.items():
            preview = ", ".join(paths[:5])
            suffix = " ..." if len(paths) > 5 else ""
            lines.append(f"  - {area}: {len(paths)} file(s){' - ' + preview + suffix if preview else ''}")
    else:
        lines.append("  - none")

    lines.extend(["", "Attention"])
    reasons = readiness["redReasons"] + readiness["yellowReasons"]
    if reasons:
        for reason in reasons[:12]:
            lines.append(f"  - {reason}")
    else:
        lines.append("  - none")

    lines.extend(["", "Required closeout actions"])
    actions = payload.get("requiredActions", [])
    if actions:
        for action in actions:
            marker = "required" if action.get("required") else "recommended"
            lines.append(f"  - [{marker}] {action.get('reason')}")
            lines.append(f"    {action.get('command')}")
    else:
        lines.append("  - none")

    lines.extend([
        "",
        "Commit policy",
        "  - Do not auto-commit.",
        "  - Do not auto-push.",
        "  - Commit only an approved slice after reviewing changed files.",
        "  - Never publish ControlCoding LAB as V1 release output.",
    ])
    return "\n".join(lines)


def cmd_work_close(
    project: Path,
    topic: str = "",
    summary: str = "",
    tests: list[str] | None = None,
    next_action: str = "",
    json_output: bool = False,
) -> int:
    """Read current closeout state and print required closure actions."""
    project = project.resolve()
    topic = str(topic or "").strip()
    summary = str(summary or "").strip()
    tests = [str(item).strip() for item in (tests or []) if str(item).strip()]
    next_action = str(next_action or "").strip()

    op_index = _safe_work_start_payload(
        "op-index",
        lambda: _op_index_payload(project, scope="dev", topic=topic),
    )
    context = _safe_work_start_payload(
        "context",
        lambda: _context_sync_payload(project),
    )
    architecture_index = _architecture_index_payload(project)
    working_tree = op_index.get("workingTree", {}) if isinstance(op_index, dict) else {}
    changes = working_tree.get("changes", []) if isinstance(working_tree.get("changes"), list) else []
    areas = _work_close_area_summary(changes)
    payload = {
        "ok": True,
        "command": "work-close",
        "projectRoot": str(project),
        "topic": topic,
        "readOnly": True,
        "hiddenWrites": False,
        "commit": {
            "automatic": False,
            "pushAutomatic": False,
            "policy": "commit only an approved slice after human review",
        },
        "providedEvidence": {
            "summary": summary,
            "tests": tests,
            "nextAction": next_action,
        },
        "opIndex": op_index,
        "context": context,
        "architectureIndex": architecture_index,
        "workingTree": working_tree,
        "changedFiles": changes,
        "areas": areas,
    }
    payload["requiredActions"] = _work_close_required_actions(payload)
    payload["readiness"] = _work_close_readiness(payload)

    if json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(_work_close_text(payload))
    return 0 if payload["readiness"]["ok"] else 1


def cmd_resume(project: Path, brief_only: bool = False, agent: str = "") -> int:
    """Print a provider-neutral context brief.

    Without --agent: loads the global warm.md brief (generated by generate_brief() MCP tool).
    With --agent NAME: loads the named agent's memory brief from its state + last log.

    ``--brief`` is retained for CLI compatibility. Resume never selects a
    provider or launcher and never starts a subprocess.
    """
    if agent:
        name = agent.lower().strip()
        persistent_agents = {"concierge", "architect", "coder", "reviewer", "debugger"}
        if name not in persistent_agents:
            print(
                f"Unknown resume agent '{name}'. Valid agents: "
                + ", ".join(sorted(persistent_agents))
            )
            return 1
        state_file = _control_plane_path(project, "agents", name, "state.json")
        if not state_file.exists():
            state_file = _control_plane_read_path(project, "sessions", "agents", name, "state.json")
        if not state_file.exists():
            print(f"No memory found for agent '{name}'.")
            print("Initialize the agent first by calling agent_start() via the MCP tool.")
            return 1

        state = json.loads(state_file.read_text(encoding="utf-8"))
        parts = [
            f"# Resume Brief: {name}",
            f"**Last updated**: {state.get('updated', 'unknown')}",
            "",
            "---",
            "",
            "## Current task",
            state.get("current_task", "_Not specified._"),
            "",
            f"**Task ID**: {state.get('current_task_id', '-')}",
            f"**Phase**: {state.get('phase', 'unknown')}",
            "",
        ]

        pending = state.get("pending", [])
        if pending:
            parts += ["## Pending", ""] + [f"- {p}" for p in pending] + [""]

        oq = state.get("open_questions", [])
        if oq:
            parts += ["## Open questions", ""] + [f"- {q}" for q in oq] + [""]

        ctx = state.get("context_note", "")
        if ctx:
            parts += ["## Context", ctx, ""]

        parts += ["---", ""]

        last_chat = state.get("last_chat", "")
        if last_chat:
            task_id = str(state.get("current_task_id", "")).strip()
            log_path = _control_plane_path(project, "agents", name, "tasks", task_id, "chats", last_chat, "log.md")
            if not log_path.exists():
                log_path = _control_plane_read_path(project, "sessions", "agents", name, "chats", last_chat, "log.md")
            if log_path.exists():
                parts += [f"## Last chat: {last_chat}", "", log_path.read_text(encoding="utf-8"), ""]

        brief = "\n".join(parts)
        print(brief)
        return 0

    # Global warm.md (no --agent)
    warm_path = _control_plane_read_path(project, "sessions", "warm.md")
    if not warm_path.exists():
        print("No context brief found.")
        print("Generate one first: call generate_brief() via the MCP tool in your current session.")
        return 1
    warm_content = warm_path.read_text(encoding="utf-8")
    print(warm_content)
    return 0


def cmd_agents(project: Path) -> int:
    """Show the status of all persistent agents."""
    agents_dir = _control_plane_path(project, "agents")
    if not agents_dir.exists():
        agents_dir = _control_plane_read_path(project, "sessions", "agents")
    persistent = ["concierge", "architect", "coder", "reviewer", "debugger"]
    stateless = ["expert", "socratic"]

    if not agents_dir.exists():
        print("No agent memory found.")
        print("Agents are initialized via agent_start() in the MCP session tool.")
        print(f"\nPersistent agents (have memory): {', '.join(persistent)}")
        print(f"Stateless agents (no memory by design): {', '.join(stateless)}")
        return 0

    print(f"{'AGENT':<12} {'TASK ID':<22} {'PHASE':<16} {'LAST CHAT':<26} {'PENDING':<9} TASK")
    print("-" * 109)
    for name in persistent:
        state_file = agents_dir / name / "state.json"
        if not state_file.exists():
            print(f"{name:<12} {'-':<22} {'no memory':<16} {'-':<26} {'-':<9} -")
            continue
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            phase = state.get("phase", "unknown")
            last_chat = state.get("last_chat", "-")
            pending = str(len(state.get("pending", [])))
            task = state.get("current_task", "")[:42]
            task_id = str(state.get("current_task_id", "-"))[:22]
            print(f"{name:<12} {task_id:<22} {phase:<16} {last_chat:<26} {pending:<9} {task}")
        except (json.JSONDecodeError, OSError):
            print(f"{name:<12} {'-':<22} {'error':<16} {'-':<26} {'-':<9} -")

    print(f"\nStateless agents (no memory by design): {', '.join(stateless)}")
    return 0


def main():
    runtime_issue = core_runtime_error()
    if runtime_issue is not None:
        print(f"Error: {runtime_issue.message}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(
        prog="cc",
        description="ControlCoding CLI - setup and manage guardrails",
    )
    sub = parser.add_subparsers(dest="command")

    # cc init
    p_init = sub.add_parser("init", help="Initialize ControlCoding in a project")
    p_init.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_init.add_argument(
        "--central-hooks", action="store_true",
        help="Store hooks in ~/.controlcoding/hooks/ (shared across projects)",
    )

    p_init.add_argument("--preview-only", action="store_true", help="Plan minimal init without writing files")

    # cc install
    p_install = sub.add_parser("install", help="Install a feature pack")
    p_install.add_argument(
        "pack", choices=ALL_PACKS + ["all"],
        help="Pack to install",
    )
    p_install.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_install.add_argument(
        "--preview-only", action="store_true",
        help="Inspect all selected pack changes and conflicts without writing",
    )

    # cc chat-start
    p_chat_start = sub.add_parser("chat-start", help="Print one AI-ready startup packet for host AI project startup")
    p_chat_start.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_chat_start.add_argument(
        "--topic", default="",
        help="Current chat or task topic for memory retrieval",
    )
    p_chat_start.add_argument(
        "--scope", default="dev",
        help="Memory scope for startup and retrieval (default: dev)",
    )
    p_chat_start.add_argument(
        "--limit", type=int, default=10,
        help="Maximum memory entries to include (default: 10)",
    )
    p_chat_start.add_argument(
        "--include-legacy", action="store_true",
        help="Include legacy and superseded Project Plane entries as warning-only context",
    )
    p_chat_start.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON report",
    )

    # cc work-start
    p_work_start = sub.add_parser("work-start", help="Read current governance context before editing")
    p_work_start.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_work_start.add_argument(
        "--topic", default="",
        help="Current task topic for memory retrieval and startup routing",
    )
    p_work_start.add_argument(
        "--scope", default="dev",
        help="Memory scope for startup and retrieval (default: dev)",
    )
    p_work_start.add_argument(
        "--limit", type=int, default=10,
        help="Maximum memory matches to include (default: 10)",
    )
    p_work_start.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON report",
    )

    # cc work-close
    p_work_close = sub.add_parser("work-close", help="Read current closeout checklist without committing")
    p_work_close.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_work_close.add_argument(
        "--topic", default="",
        help="Current task topic for closeout context",
    )
    p_work_close.add_argument(
        "--summary", default="",
        help="Short human summary of the completed work",
    )
    p_work_close.add_argument(
        "--test", action="append", default=[],
        help="Verification command or evidence item completed during this slice",
    )
    p_work_close.add_argument(
        "--next-action", default="",
        help="Next action or handoff note",
    )
    p_work_close.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON report",
    )

    # cc release-doctor
    p_release_doctor = sub.add_parser("release-doctor", help="Check source release publishability")
    p_release_doctor.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_release_doctor.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON report",
    )

    # cc setup
    p_setup = sub.add_parser("setup", help="Chat-first setup prompt or non-interactive setup apply")
    p_setup.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_setup.add_argument(
        "--engagement", action="store_true",
        help="Configure product tier / engagement runtime only",
    )
    p_setup.add_argument(
        "--chat-guide", action="store_true",
        help="Print a prompt that lets an IDE or host chat drive setup and execute the non-interactive handoff",
    )
    p_setup.add_argument(
        "--chat-wizard", dest="chat_guide", action="store_true",
        help=argparse.SUPPRESS,
    )
    p_setup.add_argument(
        "--host-hint",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        default="",
        help="Optional primary AI host hint for the chat-guided setup prompt",
    )
    p_setup.add_argument(
        "--answers-file", type=Path,
        help="JSON handoff file with prefilled `setup` and/or `engagement` answers",
    )
    p_setup.add_argument(
        "--apply-answers", action="store_true",
        help="Compatibility flag. Answers files are applied directly even without this flag.",
    )

    p_setup_project = sub.add_parser("setup-project", help="Chat-first project setup prompt or non-interactive kickoff apply")
    p_setup_project.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_setup_project.add_argument(
        "--chat-guide", action="store_true",
        help="Print a prompt that lets an IDE or host chat drive project setup and execute the non-interactive handoff",
    )
    p_setup_project.add_argument(
        "--chat-wizard", dest="chat_guide", action="store_true",
        help=argparse.SUPPRESS,
    )
    p_setup_project.add_argument(
        "--host-hint",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        default="",
        help="Optional primary AI host hint for the chat-guided project setup prompt",
    )
    p_setup_project.add_argument(
        "--answers-file", type=Path,
        help="JSON handoff file with a prefilled `project_setup` payload",
    )
    p_setup_project.add_argument(
        "--apply-answers", action="store_true",
        help="Compatibility flag. Answers files are applied directly even without this flag.",
    )

    p_consult = sub.add_parser(
        "consult",
        help="Inspect the human-mediated consultation control plane",
    )
    p_consult.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    consult_sub = p_consult.add_subparsers(dest="consult_command")

    p_consult_status = consult_sub.add_parser(
        "status",
        help="Show specialist execution modes and consultation artifacts",
    )
    p_consult_status.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_consult_status.add_argument(
        "--role", default="",
        help="Optional role filter (role id, label, or semantic role)",
    )
    p_consult_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_consult_resolution = consult_sub.add_parser(
        "resolution",
        help="Show the generated resolution artifact for a conflicted consultation topic",
    )
    p_consult_resolution.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_consult_resolution.add_argument(
        "--role", required=True,
        help="Semantic role or configured role id for the conflicted topic",
    )
    p_consult_resolution.add_argument(
        "--topic-key", required=True,
        help="Normalized topic key to resolve",
    )
    p_consult_resolution.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_consult_packet = sub.add_parser(
        "consult-packet",
        help="Create bounded packets for human-mediated specialist consultation",
    )
    p_consult_packet.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    consult_packet_sub = p_consult_packet.add_subparsers(dest="consult_packet_command")

    p_consult_packet_create = consult_packet_sub.add_parser(
        "create",
        help="Create a consultation packet for a human-mediated specialist path",
    )
    p_consult_packet_create.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_consult_packet_create.add_argument(
        "--role", required=True,
        help="Configured specialist role id, semantic role, or label (for example: consultant_1, architect, CodeWarden)",
    )
    p_consult_packet_create.add_argument(
        "--objective", required=True,
        help="Bounded objective for the external consultation",
    )
    p_consult_packet_create.add_argument(
        "--context-summary", default="",
        help="Concise local context summary for the packet",
    )
    p_consult_packet_create.add_argument(
        "--constraint", action="append", default=[],
        help="Constraint the answer must respect (repeatable)",
    )
    p_consult_packet_create.add_argument(
        "--question", action="append", default=[],
        help="Concrete question for the specialist (repeatable, at least one required)",
    )
    p_consult_packet_create.add_argument(
        "--expected-answer-shape", default="",
        help="Expected answer format or decision shape",
    )
    p_consult_packet_create.add_argument(
        "--topic-key", default="",
        help="Optional stable key for the decision topic so separate manual consultations can merge on the same question",
    )
    p_consult_packet_create.add_argument(
        "--thread-id", default="",
        help="Optional existing consultation thread id to continue instead of creating a new thread",
    )
    p_consult_packet_create.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_consult_packet_show = consult_packet_sub.add_parser(
        "show",
        help="Show a saved consultation packet and its latest imported result",
    )
    p_consult_packet_show.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_consult_packet_show.add_argument(
        "--packet-id", required=True,
        help="Consultation packet id to display",
    )
    p_consult_packet_show.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_consult_result = sub.add_parser(
        "consult-result",
        help="Import concise outcomes from a human-mediated specialist consultation",
    )
    p_consult_result.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    consult_result_sub = p_consult_result.add_subparsers(dest="consult_result_command")

    p_consult_result_import = consult_result_sub.add_parser(
        "import",
        help="Import a concise manual consultation outcome back into the control plane",
    )
    p_consult_result_import.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_consult_result_import.add_argument(
        "--packet-id", required=True,
        help="Consultation packet id to import against",
    )
    p_consult_result_import.add_argument(
        "--summary", required=True,
        help="Concise imported answer summary",
    )
    p_consult_result_import.add_argument(
        "--decision",
        choices=sorted(_VALID_CONSULT_DECISIONS),
        required=True,
        help="Decision outcome for the imported consultation",
    )
    p_consult_result_import.add_argument(
        "--rationale-summary", required=True,
        help="Short engineering rationale summary, not a raw transcript",
    )
    p_consult_result_import.add_argument(
        "--constraint", action="append", default=[],
        help="Constraint confirmed or raised by the imported result (repeatable)",
    )
    p_consult_result_import.add_argument(
        "--evidence", action="append", default=[],
        help="Short evidence summary line backing the import (repeatable)",
    )
    p_consult_result_import.add_argument(
        "--next-action", required=True,
        help="Concrete next action after importing the result",
    )
    p_consult_result_import.add_argument(
        "--source", default="manual_external_consult",
        help="Short source label for the manual external chat or host",
    )
    p_consult_result_import.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    # cc doctor
    p_doctor = sub.add_parser("doctor", help="Check project health")
    p_doctor.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_doctor.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON report",
    )
    p_doctor.add_argument(
        "--strict-claims",
        action="store_true",
        help="Fail when the capability truth contract does not match routed CLI behavior",
    )
    p_doctor.add_argument(
        "--strict-verification",
        action="store_true",
        help="Fail when the project has no valid verification contract",
    )
    p_doctor.add_argument(
        "--strict-invariants",
        action="store_true",
        help="Fail when the project has no executable invariant manifest",
    )
    p_doctor.add_argument(
        "--release",
        action="store_true",
        dest="release_mode",
        help="Validate a public ControlCoding Core source release instead of an installed adopter project",
    )

    # cc truth
    p_truth = sub.add_parser(
        "truth",
        help="Report or check the ControlCoding capability truth contract",
    )
    p_truth.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    truth_sub = p_truth.add_subparsers(dest="truth_command")

    p_truth_report = truth_sub.add_parser(
        "report",
        help="Show shipped, optional, experimental, and planned capabilities",
    )
    p_truth_report.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_truth_report.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_truth_check = truth_sub.add_parser(
        "check",
        help="Validate declared capabilities against routed CLI commands",
    )
    p_truth_check.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_truth_check.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_truth_check.add_argument(
        "--include-docs",
        action="store_true",
        help="Also validate public documentation command examples",
    )

    p_truth_check_docs = truth_sub.add_parser(
        "check-docs",
        help="Validate ControlCoding commands mentioned in public docs",
    )
    p_truth_check_docs.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_truth_check_docs.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_docs = add_docs_parser(sub, Path)

    # cc verify
    p_verify = sub.add_parser(
        "verify",
        help="Initialize, inspect, or run the project verification contract",
    )
    p_verify.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    verify_sub = p_verify.add_subparsers(dest="verify_command")

    p_verify_init = verify_sub.add_parser(
        "init",
        help="Create controlcoding.verification.json",
    )
    p_verify_init.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_verify_init.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing verification contract",
    )
    p_verify_init.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_verify_status = verify_sub.add_parser(
        "status",
        help="Validate the verification contract without running it",
    )
    p_verify_status.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_verify_status.add_argument("--require-current", action="store_true", help="Require a complete current execution pass")
    p_verify_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_verify_run = verify_sub.add_parser(
        "run",
        help="Run required or selected verification suites",
    )
    p_verify_run.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_verify_run.add_argument(
        "--suite", action="append", default=[],
        help="Suite id to run (repeatable). Default: all required suites",
    )
    p_verify_run.add_argument(
        "--kind", action="append", default=[],
        choices=sorted(_VERIFICATION_KINDS),
        help="Suite kind to run (repeatable)",
    )
    p_verify_run.add_argument(
        "--all", action="store_true", dest="all_suites",
        help="Run every suite in the contract",
    )
    p_verify_run.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    # cc invariants
    p_invariants = sub.add_parser(
        "invariants",
        help="Initialize, inspect, edit, or run the project invariant manifest",
    )
    p_invariants.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    invariants_sub = p_invariants.add_subparsers(dest="invariants_command")

    p_invariants_init = invariants_sub.add_parser(
        "init",
        help="Create controlcoding.invariants.json",
    )
    p_invariants_init.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_init.add_argument(
        "--domain", default="tooling",
        help="Template domain: tooling, finance, simulation, web, content, or generic",
    )
    p_invariants_init.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing invariant manifest",
    )
    p_invariants_init.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_elicit = invariants_sub.add_parser(
        "elicit",
        help="Generate domain questions and draft invariant candidates",
    )
    p_invariants_elicit.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_elicit.add_argument(
        "--domain", default="generic",
        help="Domain pack: finance, simulation, web, content, game, data-pipeline, or generic",
    )
    p_invariants_elicit.add_argument(
        "--write", action="store_true",
        help="Write the elicitation draft under docs/invariants/",
    )
    p_invariants_elicit.add_argument(
        "--output", type=Path, default=None,
        help="Output path for --write, relative to the project root",
    )
    p_invariants_elicit.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing elicitation draft",
    )
    p_invariants_elicit.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_status = invariants_sub.add_parser(
        "status",
        help="Validate the invariant manifest without running it",
    )
    p_invariants_status.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_status.add_argument("--require-current", action="store_true", help="Require a complete current execution pass")
    p_invariants_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_list = invariants_sub.add_parser(
        "list",
        help="List declared invariants",
    )
    p_invariants_list.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_list.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_report = invariants_sub.add_parser(
        "report",
        help="Report protected invariant properties and latest run evidence",
    )
    p_invariants_report.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_report.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_doctor = invariants_sub.add_parser(
        "doctor",
        help="Diagnose the operational invariant gate state",
    )
    p_invariants_doctor.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_doctor.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_wire_ci = invariants_sub.add_parser(
        "wire-ci",
        help="Generate or write CI wiring for the invariant gate",
    )
    p_invariants_wire_ci.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_wire_ci.add_argument(
        "--provider", default="github-actions",
        help="CI provider (currently: github-actions)",
    )
    p_invariants_wire_ci.add_argument(
        "--command", default="", dest="ci_command",
        help="Command to run in CI (default: project-local invariant runner)",
    )
    p_invariants_wire_ci.add_argument(
        "--output", type=Path, default=None,
        help="Workflow output path, relative to the project root",
    )
    p_invariants_wire_ci.add_argument(
        "--write", action="store_true",
        help="Write the workflow file. Without this, print a dry-run plan.",
    )
    p_invariants_wire_ci.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing workflow file",
    )
    p_invariants_wire_ci.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_add = invariants_sub.add_parser(
        "add",
        help="Add an invariant to controlcoding.invariants.json",
    )
    p_invariants_add.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_add.add_argument("--id", required=True, dest="invariant_id")
    p_invariants_add.add_argument("--domain", required=True)
    p_invariants_add.add_argument("--property", required=True, dest="property_text")
    p_invariants_add.add_argument("--kind", default="domain", choices=sorted(_INVARIANT_KINDS))
    p_invariants_add.add_argument("--severity", default="blocking", choices=sorted(_INVARIANT_SEVERITIES))
    p_invariants_add.add_argument("--status", default="active", choices=sorted(_INVARIANT_STATUSES))
    p_invariants_add.add_argument("--command", default="")
    p_invariants_add.add_argument("--threshold", default="")
    p_invariants_add.add_argument("--title", default="")
    p_invariants_add.add_argument(
        "--evidence", action="append", default=[],
        help="Evidence path or note for the invariant (repeatable)",
    )
    p_invariants_add.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_invariants_run = invariants_sub.add_parser(
        "run",
        help="Run executable active invariants",
    )
    p_invariants_run.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_invariants_run.add_argument(
        "--id", action="append", default=[], dest="invariant_id",
        help="Invariant id to run (repeatable)",
    )
    p_invariants_run.add_argument(
        "--domain", action="append", default=[],
        help="Invariant domain to run (repeatable)",
    )
    p_invariants_run.add_argument(
        "--kind", action="append", default=[], choices=sorted(_INVARIANT_KINDS),
        help="Invariant kind to run (repeatable)",
    )
    p_invariants_run.add_argument(
        "--all", action="store_true", dest="all_invariants",
        help="Run every invariant that has a command, including draft entries",
    )
    p_invariants_run.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_feature = add_feature_parser(sub, Path)

    # cc promote
    p_promote = sub.add_parser(
        "promote",
        help="Plan, check, or apply ControlCoding maturity-zone promotions",
    )
    p_promote.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    promote_sub = p_promote.add_subparsers(dest="promote_command")

    def _add_promotion_common_args(parser_obj):
        parser_obj.add_argument("path", help="Source path to promote")
        parser_obj.add_argument(
            "--to",
            choices=["features", "shared", "stable"],
            default="",
            help="Target maturity zone. Omit for the next staged zone.",
        )
        parser_obj.add_argument(
            "--target-path",
            default="",
            help="Explicit target path inside the selected target zone",
        )
        parser_obj.add_argument(
            "--feature",
            default="",
            help="Feature name to use when promoting from workspace/ to features/",
        )
        parser_obj.add_argument(
            "--json", action="store_true", dest="json_output",
            help="Output machine-readable JSON",
        )

    p_promote_plan = promote_sub.add_parser(
        "plan",
        help="Create a local promotion manifest under .controlcoding/promotions/",
    )
    _add_promotion_common_args(p_promote_plan)
    p_promote_plan.add_argument(
        "--reason",
        default="",
        help="Short rationale recorded in the local promotion manifest",
    )

    p_promote_check = promote_sub.add_parser(
        "check",
        help="Check whether a path can move to the next maturity zone",
    )
    _add_promotion_common_args(p_promote_check)

    p_promote_apply = promote_sub.add_parser(
        "apply",
        help="Move a path to the selected maturity zone after promotion checks",
    )
    _add_promotion_common_args(p_promote_apply)
    p_promote_apply.add_argument(
        "--reason",
        default="",
        help="Short rationale recorded in the applied promotion manifest",
    )
    p_promote_apply.add_argument(
        "--adr",
        action="store_true",
        help="Generate the required ADR when promoting into stable/",
    )

    # cc memory
    p_memory = sub.add_parser(
        "memory",
        help="Manage the local Project Memory Engine",
    )
    p_memory.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    memory_sub = p_memory.add_subparsers(dest="memory_command")

    p_memory_init = memory_sub.add_parser("init", help="Initialize project memory")
    p_memory_init.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_init.add_argument(
        "--mode",
        choices=["full", "document-only"],
        default="full",
        help="Install mode (default: full)",
    )
    p_memory_init.add_argument(
        "--profile",
        choices=["project", "work"],
        default="project",
        help="Memory profile. Use work with --mode document-only for non-code work repositories.",
    )
    p_memory_init.add_argument(
        "--project-short",
        default="",
        help="Short project identifier used in stable memory IDs",
    )
    p_memory_init.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_init = memory_sub.add_parser(
        "work-init",
        help="Initialize embedded ControlWork project-plane memory",
    )
    p_memory_work_init.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_init.add_argument(
        "--name",
        default="",
        help="Project name for CONTROLWORK.md",
    )
    p_memory_work_init.add_argument(
        "--purpose",
        default="Project knowledge and work memory",
        help="Purpose line for CONTROLWORK.md",
    )
    p_memory_work_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing CONTROLWORK.md and .controlwork/config.json",
    )
    p_memory_work_init.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_status = memory_sub.add_parser(
        "work-status",
        help="Show embedded ControlWork project-plane status",
    )
    p_memory_work_status.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_parity = memory_sub.add_parser(
        "work-parity",
        help="Compare embedded and attached standalone ControlWork compatibility",
    )
    p_memory_work_parity.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_parity.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_attach = memory_sub.add_parser(
        "work-attach",
        help="Attach an external standalone ControlWork project for manual sync",
    )
    p_memory_work_attach.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_attach.add_argument(
        "path",
        help="Path to the external ControlWork project",
    )
    p_memory_work_attach.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_import = memory_sub.add_parser(
        "work-import",
        help="Import a standalone ControlWork project into this ControlCoding project",
    )
    p_memory_work_import.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_import.add_argument(
        "path",
        help="Path to the source ControlWork project",
    )
    p_memory_work_import.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing embedded ControlWork artifacts",
    )
    p_memory_work_import.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_export = memory_sub.add_parser(
        "work-export",
        help="Export embedded ControlWork to a standalone work folder",
    )
    p_memory_work_export.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_export.add_argument(
        "target",
        help="Target folder for standalone ControlWork artifacts",
    )
    p_memory_work_export.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ControlWork artifacts in the target folder",
    )
    p_memory_work_export.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_sync = memory_sub.add_parser(
        "work-sync",
        help="Manually sync an attached external ControlWork project",
    )
    p_memory_work_sync.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_sync.add_argument(
        "--direction",
        choices=["pull", "push"],
        required=True,
        help="pull imports external work memory; push exports embedded work memory",
    )
    p_memory_work_sync.add_argument(
        "--force",
        action="store_true",
        help="Required for sync because destination artifacts are overwritten",
    )
    p_memory_work_sync.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_category = memory_sub.add_parser(
        "work-category",
        help="Manage embedded ControlWork categories",
    )
    work_category_sub = p_memory_work_category.add_subparsers(
        dest="work_category_command",
        required=True,
    )
    p_memory_work_category_list = work_category_sub.add_parser("list", help="List categories")
    p_memory_work_category_list.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_category_list.add_argument(
        "--status",
        choices=["approved", "proposed"],
        default="",
        help="Filter categories by status",
    )
    p_memory_work_category_list.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    for category_command, help_text in (
        ("propose", "Propose a project-specific category"),
        ("add", "Add an approved project-specific category"),
    ):
        p_memory_work_category_write = work_category_sub.add_parser(category_command, help=help_text)
        p_memory_work_category_write.add_argument("name", help="Category name")
        p_memory_work_category_write.add_argument(
            "--project-root", type=Path, default=argparse.SUPPRESS,
            help="Project root directory (default: current directory)",
        )
        p_memory_work_category_write.add_argument(
            "--area",
            choices=["inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy"],
            default="notes",
            help="Default memory area for the category",
        )
        p_memory_work_category_write.add_argument(
            "--description",
            default="",
            help="Category description",
        )
        p_memory_work_category_write.add_argument(
            "--json", action="store_true", dest="json_output",
            help="Output machine-readable JSON",
        )
    p_memory_work_category_approve = work_category_sub.add_parser(
        "approve",
        help="Approve a proposed category",
    )
    p_memory_work_category_approve.add_argument("category", help="Category slug or name")
    p_memory_work_category_approve.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_category_approve.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_scan = memory_sub.add_parser(
        "work-scan",
        help="Scan embedded ControlWork Project Plane files into a governed file index",
    )
    p_memory_work_scan.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_scan.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_analyze = memory_sub.add_parser(
        "work-analyze",
        help="Analyze embedded ControlWork scan index duplicates, versions, and conflicts",
    )
    p_memory_work_analyze.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_analyze.add_argument("--limit", type=int, default=50)
    p_memory_work_analyze.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_review = memory_sub.add_parser(
        "work-review",
        help="Review embedded ControlWork scan index records",
    )
    p_memory_work_review.add_argument("path", nargs="?", default="")
    p_memory_work_review.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_review.add_argument(
        "--review-status",
        choices=[
            "unreviewed",
            "reviewed",
            "ready_to_promote",
            "ignored",
            "needs_human",
            "needs_import",
            "imported",
            "promoted",
            "duplicate",
            "conflict",
            "superseded_candidate",
        ],
        default="",
    )
    p_memory_work_review.add_argument(
        "--sensitivity",
        choices=["unknown", "public", "internal", "confidential", "restricted"],
        default="",
    )
    p_memory_work_review.add_argument("--note", default="")
    p_memory_work_review.add_argument(
        "--filter",
        choices=[
            "pending",
            "all",
            "new",
            "changed",
            "missing",
            "unsupported",
            "unreviewed",
            "needs-human",
            "needs-import",
            "ready-to-promote",
            "duplicate",
            "conflict",
            "versions",
            "sensitive",
        ],
        default="pending",
    )
    p_memory_work_review.add_argument("--batch", action="store_true")
    p_memory_work_review.add_argument("--item", default="")
    p_memory_work_review.add_argument("--action", default="")
    p_memory_work_review.add_argument("--canonical", default="")
    p_memory_work_review.add_argument("--fingerprint", default="")
    p_memory_work_review.add_argument("--proposal", action="store_true")
    p_memory_work_review.add_argument("--output", type=Path, default=None)
    p_memory_work_review.add_argument("--force-output", action="store_true")
    p_memory_work_review.add_argument("--limit", type=int, default=20)
    p_memory_work_review.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_import_source = memory_sub.add_parser(
        "work-import-source",
        help="Import one scanned Project Plane source as needs-review memory",
    )
    p_memory_work_import_source.add_argument("path")
    p_memory_work_import_source.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_import_source.add_argument("--area", choices=["inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy"], default="sources")
    p_memory_work_import_source.add_argument("--title", default="")
    p_memory_work_import_source.add_argument("--summary", default="")
    p_memory_work_import_source.add_argument("--lifecycle", choices=["active", "captured", "legacy", "needs_review", "superseded"], default="needs_review")
    p_memory_work_import_source.add_argument("--category", default="")
    p_memory_work_import_source.add_argument("--max-chars", type=int, default=12000)
    p_memory_work_import_source.add_argument("--as-reference", action="store_true")
    p_memory_work_import_source.add_argument("--dry-run", action="store_true")
    p_memory_work_import_source.add_argument("--proposal", action="store_true")
    p_memory_work_import_source.add_argument("--output", type=Path, default=None)
    p_memory_work_import_source.add_argument("--force", action="store_true")
    p_memory_work_import_source.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_ocr = memory_sub.add_parser(
        "work-ocr",
        help="Manage embedded ControlWork OCR sidecars for scanned binary sources",
    )
    work_ocr_sub = p_memory_work_ocr.add_subparsers(dest="work_ocr_command", required=True)
    p_memory_work_ocr_status = work_ocr_sub.add_parser("status", help="Show Project Plane OCR sidecar status")
    p_memory_work_ocr_status.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_ocr_status.add_argument("--limit", type=int, default=20)
    p_memory_work_ocr_status.add_argument("--json", action="store_true", dest="json_output")
    p_memory_work_ocr_run = work_ocr_sub.add_parser("run", help="Report OCR adapter requirements for one Project Plane source")
    p_memory_work_ocr_run.add_argument("source")
    p_memory_work_ocr_run.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_ocr_run.add_argument("--output", type=Path, default=None)
    p_memory_work_ocr_run.add_argument("--force", action="store_true")
    p_memory_work_ocr_run.add_argument("--json", action="store_true", dest="json_output")
    p_memory_work_ocr_import = work_ocr_sub.add_parser("import-sidecar", help="Import a Project Plane OCR sidecar as needs-review evidence")
    p_memory_work_ocr_import.add_argument("source")
    p_memory_work_ocr_import.add_argument("--sidecar", type=Path, required=True)
    p_memory_work_ocr_import.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_ocr_import.add_argument("--area", choices=["inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy"], default="sources")
    p_memory_work_ocr_import.add_argument("--title", default="")
    p_memory_work_ocr_import.add_argument("--summary", default="")
    p_memory_work_ocr_import.add_argument("--lifecycle", choices=["active", "captured", "legacy", "needs_review", "superseded"], default="needs_review")
    p_memory_work_ocr_import.add_argument("--category", default="")
    p_memory_work_ocr_import.add_argument("--import-source", action="store_true")
    p_memory_work_ocr_import.add_argument("--dry-run", action="store_true")
    p_memory_work_ocr_import.add_argument("--force", action="store_true")
    p_memory_work_ocr_import.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_promote = memory_sub.add_parser(
        "work-promote",
        help="Promote one reviewed embedded ControlWork scan record into memory",
    )
    p_memory_work_promote.add_argument("path")
    p_memory_work_promote.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_promote.add_argument("--area", choices=["inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy"], default="sources")
    p_memory_work_promote.add_argument("--title", default="")
    p_memory_work_promote.add_argument("--summary", default="")
    p_memory_work_promote.add_argument("--lifecycle", choices=["active", "captured", "legacy", "needs_review", "superseded"], default="captured")
    p_memory_work_promote.add_argument("--category", default="")
    p_memory_work_promote.add_argument("--include-excerpt", action="store_true")
    p_memory_work_promote.add_argument("--force", action="store_true")
    p_memory_work_promote.add_argument("--force-note", default="")
    p_memory_work_promote.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_capture = memory_sub.add_parser(
        "work-capture",
        help="Capture embedded ControlWork Project Plane memory",
    )
    p_memory_work_capture.add_argument("area", choices=["inbox", "sources", "notes", "ideas", "decisions", "plans", "outputs", "legacy"])
    p_memory_work_capture.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_capture.add_argument("--title", required=True)
    p_memory_work_capture.add_argument("--body", required=True)
    p_memory_work_capture.add_argument("--lifecycle", choices=["active", "captured", "legacy", "needs_review", "superseded"], default="captured")
    p_memory_work_capture.add_argument("--source", default="")
    p_memory_work_capture.add_argument("--category", default="")
    p_memory_work_capture.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_session = memory_sub.add_parser(
        "work-session",
        help="Manage embedded ControlWork portable session records",
    )
    work_session_sub = p_memory_work_session.add_subparsers(dest="work_session_command", required=True)
    p_work_session_start = work_session_sub.add_parser("start", help="Start a Project Plane session record")
    p_work_session_start.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_session_start.add_argument("--topic", required=True)
    p_work_session_start.add_argument(
        "--mode",
        choices=[
            "continue_previous_work",
            "new_work",
            "planning",
            "research",
            "review",
            "implementation",
            "handoff",
            "maintenance",
        ],
        default="continue_previous_work",
    )
    p_work_session_start.add_argument("--operator", default="manual")
    p_work_session_start.add_argument("--id", dest="session_id", default="")
    p_work_session_start.add_argument("--summary", default="")
    p_work_session_start.add_argument("--category", action="append", default=[])
    p_work_session_close = work_session_sub.add_parser("close", help="Close a Project Plane session record")
    p_work_session_close.add_argument("session_id")
    p_work_session_close.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_session_close.add_argument("--status", choices=["completed", "needs_followup", "blocked", "superseded", "archived"], default="completed")
    p_work_session_close.add_argument("--summary", default="")
    p_work_session_close.add_argument("--followup", action="append", default=[])
    p_work_session_close.add_argument("--decision", action="append", default=[])
    p_work_session_link = work_session_sub.add_parser("link", help="Link a Project Plane session to memory evidence")
    p_work_session_link.add_argument("session_id")
    p_work_session_link.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_session_link.add_argument("--type", choices=["belongs_to_category", "references_entry", "changes_memory", "left_followup", "produced_packet", "references_decision", "uses_work_graphrag"], required=True)
    p_work_session_link.add_argument("--target", required=True)
    p_work_session_link.add_argument("--target-type", default="")
    p_work_session_note = work_session_sub.add_parser("note", help="Record a Project Plane session note")
    p_work_session_note.add_argument("session_id")
    p_work_session_note.add_argument("text")
    p_work_session_note.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_session_note.add_argument("--kind", choices=["note", "decision", "followup"], default="note")
    p_work_session_list = work_session_sub.add_parser("list", help="List Project Plane session records")
    p_work_session_list.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_session_list.add_argument("--status", choices=["all", "active", "completed", "needs_followup", "blocked", "superseded", "archived"], default="all")
    p_work_session_list.add_argument("--topic", default="")
    p_work_session_list.add_argument("--limit", type=int, default=20)
    p_work_session_show = work_session_sub.add_parser("show", help="Show one Project Plane session record")
    p_work_session_show.add_argument("session_id")
    p_work_session_show.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)

    p_memory_work_graph = memory_sub.add_parser(
        "work-graph",
        help="Inspect embedded ControlWork portable graph",
    )
    work_graph_sub = p_memory_work_graph.add_subparsers(dest="work_graph_command", required=True)
    p_work_graph_status = work_graph_sub.add_parser("status", help="Show Project Plane graph contract and counts")
    p_work_graph_status.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_suggestions = work_graph_sub.add_parser("suggestions", help="List Project Plane graph suggestions")
    p_work_graph_suggestions.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_suggestions.add_argument("--status", choices=["all", "suggested", "accepted", "rejected"], default="suggested")
    p_work_graph_suggestions.add_argument("--confidence", choices=["explicit_link", "strong_topic_overlap", "weak_topic_overlap", "human_reviewed"], default="")
    p_work_graph_suggestions.add_argument("--limit", type=int, default=50)
    p_work_graph_accept = work_graph_sub.add_parser("accept", help="Accept a Project Plane graph suggestion")
    p_work_graph_accept.add_argument("suggestion_id")
    p_work_graph_accept.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_accept.add_argument("--reason", required=True)
    p_work_graph_reject = work_graph_sub.add_parser("reject", help="Reject a Project Plane graph suggestion")
    p_work_graph_reject.add_argument("suggestion_id")
    p_work_graph_reject.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_reject.add_argument("--reason", required=True)
    p_work_graph_explain = work_graph_sub.add_parser("explain", help="Explain a Project Plane graph node, edge, or suggestion")
    p_work_graph_explain.add_argument("selector")
    p_work_graph_explain.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_explain.add_argument("--limit", type=int, default=10)
    p_work_graph_path = work_graph_sub.add_parser("path", help="Find an evidence path between two Project Plane graph nodes")
    p_work_graph_path.add_argument("source_selector")
    p_work_graph_path.add_argument("target_selector")
    p_work_graph_path.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_path.add_argument("--max-depth", type=int, default=3)
    p_work_graph_path.add_argument("--include-suggestions", action="store_true")
    p_work_graph_neighbors = work_graph_sub.add_parser("neighbors", help="List Project Plane graph neighbors for one node")
    p_work_graph_neighbors.add_argument("selector")
    p_work_graph_neighbors.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_neighbors.add_argument("--limit", type=int, default=20)
    p_work_graph_neighbors.add_argument("--include-suggestions", action="store_true")
    p_work_graph_neighbors.add_argument("--include-chunks", action="store_true")
    p_work_graph_stale = work_graph_sub.add_parser("stale", help="List non-current Project Plane records and stale scan attention")
    p_work_graph_stale.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_stale.add_argument("--limit", type=int, default=50)
    p_work_graph_stale.add_argument("--no-scan", action="store_true")
    p_work_graph_unresolved = work_graph_sub.add_parser("unresolved", help="List unresolved Project Plane records and pending scan review")
    p_work_graph_unresolved.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_unresolved.add_argument("--limit", type=int, default=50)
    p_work_graph_unresolved.add_argument("--no-scan", action="store_true")
    p_work_graph_diff = work_graph_sub.add_parser("diff", help="Compare current Project Plane graph with an exported baseline")
    p_work_graph_diff.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_diff.add_argument("--baseline", type=Path, required=True)
    p_work_graph_diff.add_argument("--limit", type=int, default=100)
    p_work_graph_export = work_graph_sub.add_parser("export", help="Export a derived Project Plane graph projection")
    p_work_graph_export.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_work_graph_export.add_argument("--format", choices=["json", "html"], default="json")
    p_work_graph_export.add_argument("--output", type=Path, required=True)
    p_work_graph_export.add_argument("--type", action="append", default=[])
    p_work_graph_export.add_argument("--lifecycle", action="append", default=[])
    p_work_graph_export.add_argument("--confidence", choices=["explicit_link", "strong_topic_overlap", "weak_topic_overlap", "human_reviewed"], default="")
    p_work_graph_export.add_argument("--source", default="")

    p_memory_work_query = memory_sub.add_parser("work-query", help="Run embedded Project Plane query-first retrieval with GraphRAG and graph hints")
    p_memory_work_query.add_argument("query")
    p_memory_work_query.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_query.add_argument("--limit", type=int, default=10)
    p_memory_work_query.add_argument("--include-legacy", action="store_true")
    p_memory_work_query.add_argument("--path-to", default="")
    p_memory_work_query.add_argument("--max-depth", type=int, default=3)
    p_memory_work_query.add_argument("--include-suggestions", action="store_true")
    p_memory_work_query.add_argument("--output", type=Path, default=None)
    p_memory_work_query.add_argument("--stdout", action="store_true")
    p_memory_work_query.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_retrieve = memory_sub.add_parser(
        "work-retrieve",
        help="Retrieve embedded ControlWork Project Plane memory with reasons",
    )
    p_memory_work_retrieve.add_argument("query")
    p_memory_work_retrieve.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_retrieve.add_argument("--limit", type=int, default=10)
    p_memory_work_retrieve.add_argument("--include-legacy", action="store_true")

    p_memory_work_rag_pack = memory_sub.add_parser("work-rag-pack", help="Build an embedded ControlWork Project Plane GraphRAG packet")
    p_memory_work_rag_pack.add_argument("query")
    p_memory_work_rag_pack.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_rag_pack.add_argument("--limit", type=int, default=10)
    p_memory_work_rag_pack.add_argument("--include-legacy", action="store_true")
    p_memory_work_rag_pack.add_argument("--output", type=Path, default=None)
    p_memory_work_rag_pack.add_argument("--stdout", action="store_true")
    p_memory_work_rag_pack.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_views = memory_sub.add_parser(
        "work-views",
        help="Generate embedded ControlWork views",
    )
    p_memory_work_views.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_views.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_checkpoint = memory_sub.add_parser(
        "work-checkpoint",
        help="Create an embedded ControlWork checkpoint receipt",
    )
    p_memory_work_checkpoint.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_checkpoint.add_argument(
        "--title",
        default="",
        help="Checkpoint title",
    )
    p_memory_work_checkpoint.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_handoff = memory_sub.add_parser(
        "work-handoff",
        help="Generate an embedded ControlWork handoff packet",
    )
    p_memory_work_handoff.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_handoff.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional copy target for the generated handoff packet",
    )
    p_memory_work_handoff.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_dashboard = memory_sub.add_parser(
        "work-dashboard",
        help="Generate an embedded ControlWork static dashboard",
    )
    p_memory_work_dashboard.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_dashboard.add_argument("--format", choices=["html", "md", "json"], default="html")
    p_memory_work_dashboard.add_argument("--output", type=Path, default=None)
    p_memory_work_dashboard.add_argument("--limit", type=int, default=20)
    p_memory_work_dashboard.add_argument(
        "--scan-limit",
        type=int,
        default=50,
        help="Maximum scan-analysis records to include while refreshing Project Map scan state",
    )
    p_memory_work_dashboard.add_argument(
        "--no-refresh-scan",
        action="store_true",
        help="Skip the explicit file scan refresh before dashboard generation",
    )
    p_memory_work_dashboard.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    context_scope_choices = [
        "general",
        "research",
        "planning",
        "analysis",
        "writing",
        "ux",
        "ui",
        "frontend",
        "backend",
        "architecture",
        "implementation",
        "bugfix",
        "refactor",
        "review",
        "handoff",
    ]

    p_memory_work_quickstart = memory_sub.add_parser(
        "work-quickstart",
        help="Run the safe first-pass embedded ControlWork Project Plane setup and context refresh",
    )
    p_memory_work_quickstart.add_argument("--project-root", type=Path, default=argparse.SUPPRESS)
    p_memory_work_quickstart.add_argument("--name", default="")
    p_memory_work_quickstart.add_argument("--purpose", default="Project knowledge and work memory")
    p_memory_work_quickstart.add_argument("--scope", choices=context_scope_choices, default="general")
    p_memory_work_quickstart.add_argument("--topic", default="project direction")
    p_memory_work_quickstart.add_argument("--limit", type=int, default=10)
    p_memory_work_quickstart.add_argument("--scan-limit", type=int, default=50)
    p_memory_work_quickstart.add_argument("--include-legacy", action="store_true")
    p_memory_work_quickstart.add_argument("--checkpoint", action="store_true")
    p_memory_work_quickstart.add_argument("--checkpoint-title", default="")
    p_memory_work_quickstart.add_argument("--dry-run", action="store_true")
    p_memory_work_quickstart.add_argument("--json", action="store_true", dest="json_output")

    p_memory_work_context_pack = memory_sub.add_parser(
        "work-context-pack",
        help="Build an embedded ControlWork scoped context packet",
    )
    p_memory_work_context_pack.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_context_pack.add_argument("--scope", choices=context_scope_choices, default="general")
    p_memory_work_context_pack.add_argument("--topic", default="")
    p_memory_work_context_pack.add_argument("--limit", type=int, default=10)
    p_memory_work_context_pack.add_argument("--include-legacy", action="store_true")
    p_memory_work_context_pack.add_argument("--output", type=Path, default=None)
    p_memory_work_context_pack.add_argument("--stdout", action="store_true")
    p_memory_work_context_pack.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_obsidian = memory_sub.add_parser(
        "work-obsidian",
        help="Initialize, sync, or check an embedded ControlWork Obsidian projection",
    )
    p_memory_work_obsidian.add_argument(
        "action",
        choices=["init", "sync", "check"],
        help="Obsidian projection action",
    )
    p_memory_work_obsidian.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_obsidian.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_wiki = memory_sub.add_parser(
        "work-wiki",
        help="Build or review the embedded ControlWork wiki projection",
    )
    p_memory_work_wiki.add_argument(
        "action",
        choices=["build", "import-edits"],
        help="Wiki projection action",
    )
    p_memory_work_wiki.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_wiki.add_argument(
        "--review",
        action="store_true",
        help="Write review proposals for edited wiki pages",
    )
    p_memory_work_wiki.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_work_mcp = memory_sub.add_parser(
        "work-mcp",
        help="Inspect or call embedded ControlWork read-only MCP tools locally",
    )
    work_mcp_sub = p_memory_work_mcp.add_subparsers(dest="work_mcp_command", required=True)
    p_memory_work_mcp_tools = work_mcp_sub.add_parser("tools", help="List read-only MCP tools")
    p_memory_work_mcp_tools.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (accepted for command-shape consistency)",
    )
    p_memory_work_mcp_tools.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_work_mcp_call = work_mcp_sub.add_parser("call", help="Call a read-only tool locally")
    p_memory_work_mcp_call.add_argument("tool", help="Tool name")
    p_memory_work_mcp_call.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_work_mcp_call.add_argument(
        "--args-file",
        type=Path,
        default=None,
        help="JSON file with tool arguments",
    )
    p_memory_work_mcp_call.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    for name, help_text in (
        ("doctor", "Validate project memory layout, schema, logs, and views"),
        ("status", "Show project memory counts and health summary"),
    ):
        p_memory_simple = memory_sub.add_parser(name, help=help_text)
        p_memory_simple.add_argument(
            "--project-root", type=Path, default=argparse.SUPPRESS,
            help="Project root directory (default: current directory)",
        )
        p_memory_simple.add_argument(
            "--json", action="store_true", dest="json_output",
            help="Output machine-readable JSON",
        )

    add_memory_scan_parser(memory_sub, Path, argparse)

    p_memory_bootstrap = memory_sub.add_parser(
        "bootstrap",
        help="Show read-only memory bootstrap status across Dev Plane and Project Plane",
    )
    p_memory_bootstrap.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_bootstrap.add_argument("--scope", default="general", help="Current work scope")
    p_memory_bootstrap.add_argument("--topic", default="", help="Current work topic")
    p_memory_bootstrap.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_op_index = memory_sub.add_parser(
        "op-index",
        help="Show RAG-O, the read-only operations index for memory and RAG routing",
    )
    p_memory_op_index.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_op_index.add_argument("--scope", default="general", help="Current work scope")
    p_memory_op_index.add_argument("--topic", default="", help="Current work topic")
    p_memory_op_index.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_startup = memory_sub.add_parser(
        "startup",
        help="Show the explicit read-only startup protocol for a new chat",
    )
    p_memory_startup.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_startup.add_argument("--scope", default="general", help="Current work scope")
    p_memory_startup.add_argument("--topic", default="", help="Current work topic")
    p_memory_startup.add_argument(
        "--intent",
        choices=sorted(STARTUP_INTENTS),
        default="ask",
        help="Startup intent: ask, continue_previous_work, or start_new_work",
    )
    p_memory_startup.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_session = memory_sub.add_parser("session", help="Manage explicit Session GraphRAG records")
    session_sub = p_memory_session.add_subparsers(dest="memory_session_command")
    p_memory_session_start = session_sub.add_parser("start", help="Start an explicit session record")
    p_memory_session_start.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_start.add_argument("--topic", required=True, help="Session topic")
    p_memory_session_start.add_argument(
        "--mode",
        choices=sorted(VALID_SESSION_MODES),
        default="continue_previous_work",
        help="Session mode",
    )
    p_memory_session_start.add_argument("--scope", default="dev", help="Session scope")
    p_memory_session_start.add_argument("--id", dest="session_id", default="", help="Optional explicit session id")
    p_memory_session_start.add_argument("--category", action="append", default=[], help="Session category")
    p_memory_session_start.add_argument("--summary", default="", help="Optional opening summary")
    p_memory_session_start.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_session_close = session_sub.add_parser("close", help="Close an explicit session record")
    p_memory_session_close.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_close.add_argument("session_id", help="Session id")
    p_memory_session_close.add_argument(
        "--status",
        choices=sorted(VALID_SESSION_STATUSES - {"active"}),
        default="completed",
        help="Final session status",
    )
    p_memory_session_close.add_argument("--summary", default="", help="Closing summary")
    p_memory_session_close.add_argument("--followup", action="append", default=[], help="Open follow-up")
    p_memory_session_close.add_argument("--decision", action="append", default=[], help="Decision captured")
    p_memory_session_close.add_argument("--commit", action="append", default=[], help="Linked commit SHA")
    p_memory_session_close.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_session_list = session_sub.add_parser("list", help="List session records")
    p_memory_session_list.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_list.add_argument(
        "--status",
        choices=["all", *sorted(VALID_SESSION_STATUSES)],
        default="all",
        help="Session status filter",
    )
    p_memory_session_list.add_argument("--topic", default="", help="Topic substring filter")
    p_memory_session_list.add_argument("--limit", type=int, default=20, help="Maximum records to show")
    p_memory_session_list.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_session_show = session_sub.add_parser("show", help="Show one session record")
    p_memory_session_show.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_show.add_argument("session_id", help="Session id")
    p_memory_session_show.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_session_link = session_sub.add_parser("link", help="Link a session to evidence")
    p_memory_session_link.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_link.add_argument("session_id", help="Session id")
    p_memory_session_link.add_argument("--type", choices=sorted(SESSION_EDGE_TYPES), dest="link_type", default="", help="Session link type")
    p_memory_session_link.add_argument("--target", default="", help="Generic link target")
    p_memory_session_link.add_argument("--target-type", default="", help="Optional target type")
    p_memory_session_link.add_argument("--commit", default="", help="Convenience link to a commit SHA")
    p_memory_session_link.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_session_note = session_sub.add_parser("note", help="Record a session note, decision, or follow-up")
    p_memory_session_note.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_note.add_argument("session_id", help="Session id")
    p_memory_session_note.add_argument("text", help="Note text")
    p_memory_session_note.add_argument(
        "--kind",
        choices=["note", "decision", "followup"],
        default="note",
        help="Session note kind",
    )
    p_memory_session_note.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_session_views = session_sub.add_parser("views", help="Generate derived session views")
    p_memory_session_views.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_views.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_session_pack = memory_sub.add_parser(
        "session-pack",
        help="Build a Session GraphRAG packet from session records",
    )
    p_memory_session_pack.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_session_pack.add_argument("--topic", default="", help="Topic substring filter")
    p_memory_session_pack.add_argument(
        "--mode",
        choices=["", *sorted(VALID_SESSION_MODES)],
        default="",
        help="Optional session mode filter",
    )
    p_memory_session_pack.add_argument(
        "--status",
        choices=["all", *sorted(VALID_SESSION_STATUSES)],
        default="all",
        help="Session status filter",
    )
    p_memory_session_pack.add_argument("--limit", type=int, default=10, help="Maximum sessions to include")
    p_memory_session_pack.add_argument("--output", type=Path, default=None, help="Optional Markdown output path")
    p_memory_session_pack.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_graph = memory_sub.add_parser("graph", help="Inspect the memory graph and review suggestions")
    graph_sub = p_memory_graph.add_subparsers(dest="memory_graph_command")
    p_memory_graph_status = graph_sub.add_parser("status", help="Show graph contract and type coverage")
    p_memory_graph_status.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_graph_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_graph_suggestions = graph_sub.add_parser("suggestions", help="List graph correlation suggestions")
    p_memory_graph_suggestions.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_graph_suggestions.add_argument(
        "--status",
        choices=["suggested", "accepted", "rejected", "confirmed", "all"],
        default="suggested",
        help="Suggestion status to list",
    )
    p_memory_graph_suggestions.add_argument("--limit", type=int, default=50, help="Maximum suggestions to show")
    p_memory_graph_suggestions.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_graph_accept = graph_sub.add_parser("accept", help="Accept a graph suggestion and create a canonical edge")
    p_memory_graph_accept.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_graph_accept.add_argument("suggestion_id", help="Correlation suggestion id")
    p_memory_graph_accept.add_argument("--reason", default="", help="Audit reason")
    p_memory_graph_accept.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_graph_reject = graph_sub.add_parser("reject", help="Reject a graph suggestion while preserving audit history")
    p_memory_graph_reject.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_graph_reject.add_argument("suggestion_id", help="Correlation suggestion id")
    p_memory_graph_reject.add_argument("--reason", default="", help="Audit reason")
    p_memory_graph_reject.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_graph_around = graph_sub.add_parser("around", help="Show graph neighborhood around an entity, chunk, or path")
    p_memory_graph_around.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_graph_around.add_argument("selector", help="Entity id, chunk id, title, or path")
    p_memory_graph_around.add_argument("--depth", type=int, default=1, help="Traversal depth, from 1 to 4")
    p_memory_graph_around.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_graph_export = graph_sub.add_parser("export", help="Export a derived graph projection or HTML viewer")
    p_memory_graph_export.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_graph_export.add_argument(
        "--format",
        choices=["json", "html"],
        default="json",
        dest="output_format",
        help="Export format",
    )
    p_memory_graph_export.add_argument("--output", type=Path, default=None, help="Optional export output path")
    p_memory_graph_export.add_argument("--type", action="append", default=[], dest="type_filters", help="Node type filter")
    p_memory_graph_export.add_argument("--lifecycle", action="append", default=[], dest="lifecycle_filters", help="Lifecycle filter")
    p_memory_graph_export.add_argument("--confidence", action="append", default=[], dest="confidence_filters", help="Edge or suggestion confidence filter")
    p_memory_graph_export.add_argument("--source", action="append", default=[], dest="source_filters", help="Source filter: dev, project, application, or path text")
    p_memory_graph_export.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON metadata",
    )

    p_memory_retrieve = memory_sub.add_parser(
        "retrieve",
        help="Run hybrid memory retrieval with explainable ranking",
    )
    p_memory_retrieve.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_retrieve.add_argument("query", help="Question, topic, path, or source identifier")
    p_memory_retrieve.add_argument("--scope", default="general", help="Retrieval scope: general, dev, project, or application")
    p_memory_retrieve.add_argument("--limit", type=int, default=10, help="Maximum matches to show")
    p_memory_retrieve.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived records in retrieval candidates",
    )
    p_memory_retrieve.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_rag_pack = memory_sub.add_parser(
        "rag-pack",
        help="Build a GraphRAG context packet from hybrid retrieval",
    )
    p_memory_rag_pack.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_rag_pack.add_argument("query", help="Question, topic, path, or source identifier")
    p_memory_rag_pack.add_argument("--scope", default="general", help="Packet scope: general, dev, project, or application")
    p_memory_rag_pack.add_argument("--limit", type=int, default=10, help="Maximum citations to include")
    p_memory_rag_pack.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived records in retrieval candidates",
    )
    p_memory_rag_pack.add_argument("--output", type=Path, default=None, help="Optional Markdown output path")
    p_memory_rag_pack.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_evidence = add_memory_aux_parser(memory_sub, Path, argparse)

    p_memory_cross_pack = memory_sub.add_parser(
        "cross-pack",
        help="Build a federated cross-plane GraphRAG packet",
    )
    p_memory_cross_pack.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_cross_pack.add_argument("query", help="Question, topic, path, or source identifier")
    p_memory_cross_pack.add_argument("--scope", default="general", help="Packet scope: general, dev, project, or application")
    p_memory_cross_pack.add_argument("--limit", type=int, default=10, help="Maximum citations per plane")
    p_memory_cross_pack.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived Dev Plane records in retrieval candidates",
    )
    p_memory_cross_pack.add_argument(
        "--include-legacy",
        action="store_true",
        help="Include additional legacy Project Plane records",
    )
    p_memory_cross_pack.add_argument("--output", type=Path, default=None, help="Optional Markdown output path")
    p_memory_cross_pack.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_semantic = memory_sub.add_parser(
        "semantic",
        help="Inspect optional semantic adapter configuration",
    )
    semantic_sub = p_memory_semantic.add_subparsers(dest="memory_semantic_command")
    p_memory_semantic_status = semantic_sub.add_parser("status", help="Show semantic adapter status")
    p_memory_semantic_status.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_semantic_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_ocr = memory_sub.add_parser(
        "ocr",
        help="Run explicit OCR/layout extraction adapters",
    )
    ocr_sub = p_memory_ocr.add_subparsers(dest="memory_ocr_command")
    p_memory_ocr_status = ocr_sub.add_parser("status", help="Show OCR adapter status")
    p_memory_ocr_status.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_ocr_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_ocr_run = ocr_sub.add_parser("run", help="Run the configured OCR adapter for one source")
    p_memory_ocr_run.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_ocr_run.add_argument("source", help="Source PDF or image path inside the project")
    p_memory_ocr_run.add_argument("--output", default=None, help="Output sidecar JSON path inside the project")
    p_memory_ocr_run.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")
    p_memory_ocr_run.add_argument("--force", action="store_true", help="Overwrite an existing OCR sidecar output file")

    for name, help_text in (
        ("impact", "Show related memory for a path or topic"),
        ("context", "Show a compact work context for a path or topic"),
    ):
        p_memory_lookup = memory_sub.add_parser(name, help=help_text)
        p_memory_lookup.add_argument(
            "--project-root", type=Path, default=argparse.SUPPRESS,
            help="Project root directory (default: current directory)",
        )
        p_memory_lookup.add_argument("query", help="Path or topic to inspect")
        p_memory_lookup.add_argument(
            "--json", action="store_true", dest="json_output",
            help="Output machine-readable JSON",
        )

    p_memory_dev_context_pack = memory_sub.add_parser(
        "dev-context-pack",
        help="Build a scoped development/code context packet from Project Memory Engine",
    )
    p_memory_dev_context_pack.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_dev_context_pack.add_argument("--scope", choices=context_scope_choices, default="general")
    p_memory_dev_context_pack.add_argument("--topic", default="")
    p_memory_dev_context_pack.add_argument("--limit", type=int, default=10)
    p_memory_dev_context_pack.add_argument("--output", type=Path, default=None)
    p_memory_dev_context_pack.add_argument("--stdout", action="store_true")
    p_memory_dev_context_pack.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_chunks = memory_sub.add_parser("chunks", help="Show semantic chunks for a document")
    p_memory_chunks.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_chunks.add_argument("path", help="Document path to inspect")
    p_memory_chunks.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_layout = memory_sub.add_parser("layout", help="Show first-class document layout nodes for a document")
    p_memory_layout.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_layout.add_argument("path", help="Document path to inspect")
    p_memory_layout.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_intake = memory_sub.add_parser("intake", help="Capture non-code work inputs into docs/inbox")
    memory_intake_sub = p_memory_intake.add_subparsers(dest="memory_intake_command")
    p_memory_intake_add = memory_intake_sub.add_parser("add", help="Add a summarized work input")
    p_memory_intake_add.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_intake_add.add_argument("title", help="Input title")
    p_memory_intake_add.add_argument("--summary", default="", help="Extracted text or concise summary")
    p_memory_intake_add.add_argument("--source-type", default="other", help="Source type such as chat, pdf, document, table, image, meeting, or research")
    p_memory_intake_add.add_argument(
        "--lifecycle",
        choices=sorted(VALID_LIFECYCLES),
        default="captured",
        help="Lifecycle state",
    )
    p_memory_intake_add.add_argument("--path", default="", help="Optional output path inside the project")
    p_memory_intake_add.add_argument(
        "--source-ref",
        action="append",
        default=[],
        help="Optional provenance source reference (repeatable)",
    )
    p_memory_intake_add.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_intake_promote = memory_intake_sub.add_parser(
        "promote",
        help="Promote an inbox item into a Work Memory target folder",
    )
    p_memory_intake_promote.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_intake_promote.add_argument("selector", help="Entity id, path, or unique title to promote")
    p_memory_intake_promote.add_argument(
        "--to",
        required=True,
        choices=["source", "extract", "research", "idea", "decision", "workflow", "output", "archive"],
        help="Work Memory target folder",
    )
    p_memory_intake_promote.add_argument(
        "--lifecycle",
        choices=sorted(VALID_LIFECYCLES),
        default="",
        help="Lifecycle state. Omit to use the target default.",
    )
    p_memory_intake_promote.add_argument("--reason", default="", help="Audit reason")
    p_memory_intake_promote.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_lifecycle = memory_sub.add_parser("lifecycle", help="Manage audited memory lifecycle transitions")
    lifecycle_sub = p_memory_lifecycle.add_subparsers(dest="memory_lifecycle_command")
    p_memory_lifecycle_mark = lifecycle_sub.add_parser("mark", help="Set lifecycle state for one memory entity")
    p_memory_lifecycle_mark.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_lifecycle_mark.add_argument("selector", help="Entity id, path, or unique title")
    p_memory_lifecycle_mark.add_argument(
        "--state",
        required=True,
        choices=sorted(VALID_LIFECYCLES),
        help="Lifecycle state to assign",
    )
    p_memory_lifecycle_mark.add_argument("--reason", default="", help="Audit reason for the lifecycle change")
    p_memory_lifecycle_mark.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_lifecycle_supersede = lifecycle_sub.add_parser(
        "supersede",
        help="Mark one entity superseded by another",
    )
    p_memory_lifecycle_supersede.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_lifecycle_supersede.add_argument("old", help="Entity id, path, or unique title to supersede")
    p_memory_lifecycle_supersede.add_argument("new", help="Replacement entity id, path, or unique title")
    p_memory_lifecycle_supersede.add_argument("--reason", default="", help="Audit reason for the supersession")
    p_memory_lifecycle_supersede.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_lifecycle_conflict = lifecycle_sub.add_parser(
        "conflict",
        help="Mark two entities as conflicting",
    )
    p_memory_lifecycle_conflict.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_lifecycle_conflict.add_argument("left", help="First entity id, path, or unique title")
    p_memory_lifecycle_conflict.add_argument("right", help="Second entity id, path, or unique title")
    p_memory_lifecycle_conflict.add_argument("--reason", default="", help="Audit reason for the conflict")
    p_memory_lifecycle_conflict.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_vector = memory_sub.add_parser("vector", help="Manage the derived local vector index")
    vector_sub = p_memory_vector.add_subparsers(dest="memory_vector_command")
    p_memory_vector_rebuild = vector_sub.add_parser("rebuild", help="Rebuild the derived local vector index")
    p_memory_vector_rebuild.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_vector_rebuild.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_memory_vector_search = vector_sub.add_parser("search", help="Search the derived local vector index")
    p_memory_vector_search.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_vector_search.add_argument("query", help="Search query")
    p_memory_vector_search.add_argument("--limit", type=int, default=10, help="Maximum matches to show")
    p_memory_vector_search.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    def _add_memory_entity_parser(parent, noun: str):
        noun_sub = parent.add_subparsers(dest=f"memory_{noun}_command")
        p_add = noun_sub.add_parser("add", help=f"Record a memory {noun}")
        p_add.add_argument(
            "--project-root", type=Path, default=argparse.SUPPRESS,
            help="Project root directory (default: current directory)",
        )
        p_add.add_argument("title", help=f"{noun.title()} title")
        p_add.add_argument("--body", default="", help="Short body or summary")
        p_add.add_argument("--area", default="General", help="Area or subsystem label")
        p_add.add_argument(
            "--lifecycle",
            choices=sorted(VALID_LIFECYCLES),
            default="active" if noun == "decision" else "captured",
            help="Lifecycle state",
        )
        p_add.add_argument("--path", default="", help="Optional related path")
        p_add.add_argument(
            "--source-ref",
            action="append",
            default=[],
            help="Optional provenance source reference (repeatable)",
        )
        if noun == "decision":
            p_add.add_argument("--rationale", default="", help="Decision rationale summary")
        p_add.add_argument(
            "--json", action="store_true", dest="json_output",
            help="Output machine-readable JSON",
        )

    p_memory_note = memory_sub.add_parser("note", help="Record project memory notes")
    _add_memory_entity_parser(p_memory_note, "note")
    p_memory_idea = memory_sub.add_parser("idea", help="Record project memory ideas")
    _add_memory_entity_parser(p_memory_idea, "idea")
    p_memory_decision = memory_sub.add_parser("decision", help="Record project memory decisions")
    _add_memory_entity_parser(p_memory_decision, "decision")

    p_memory_consult = memory_sub.add_parser("consult", help="Record manual or external consults")
    memory_consult_sub = p_memory_consult.add_subparsers(dest="memory_consult_command")
    p_memory_consult_record = memory_consult_sub.add_parser("record", help="Record a consult outcome")
    p_memory_consult_record.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_consult_record.add_argument("--title", default="", help="Consult title")
    p_memory_consult_record.add_argument("--source-type", default="manual_external", help="Consult source type")
    p_memory_consult_record.add_argument("--backend", default="", help="Visible backend or provider when known")
    p_memory_consult_record.add_argument("--question-summary", default="", help="Prompt or question summary")
    p_memory_consult_record.add_argument("--answer-summary", default="", help="Answer summary")
    p_memory_consult_record.add_argument("--summary", default="", help="Concise imported summary")
    p_memory_consult_record.add_argument("--artifact", default="", help="Optional raw artifact path")
    p_memory_consult_record.add_argument(
        "--decision-outcome",
        choices=["accepted", "rejected", "pending", "superseded"],
        default="pending",
        help="Reviewed outcome for the consult",
    )
    p_memory_consult_record.add_argument(
        "--status",
        choices=["accepted", "rejected", "pending", "superseded"],
        default="pending",
        help="Consult status",
    )
    p_memory_consult_record.add_argument(
        "--affected",
        action="append",
        default=[],
        help="Affected entity id or path (repeatable)",
    )
    p_memory_consult_record.add_argument(
        "--lifecycle",
        choices=sorted(VALID_LIFECYCLES),
        default="captured",
        help="Lifecycle state",
    )
    p_memory_consult_record.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_agent_run = memory_sub.add_parser("agent-run", help="Record agent or subagent runs")
    memory_agent_run_sub = p_memory_agent_run.add_subparsers(dest="memory_agent_run_command")
    p_memory_agent_run_record = memory_agent_run_sub.add_parser("record", help="Record an agent run")
    p_memory_agent_run_record.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_agent_run_record.add_argument("--role", default="", help="Agent role")
    p_memory_agent_run_record.add_argument("--host", default="", help="Host or runtime")
    p_memory_agent_run_record.add_argument("--task", default="", help="Task summary")
    p_memory_agent_run_record.add_argument("--summary", default="", help="Output summary")
    p_memory_agent_run_record.add_argument("--input-context-ref", default="", help="Input context reference")
    p_memory_agent_run_record.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file path (repeatable)",
    )
    p_memory_agent_run_record.add_argument(
        "--decision",
        action="append",
        default=[],
        help="Decision proposed by the run (repeatable)",
    )
    p_memory_agent_run_record.add_argument(
        "--verification",
        action="append",
        default=[],
        help="Verification performed (repeatable)",
    )
    p_memory_agent_run_record.add_argument("--status", default="completed", help="Run status")
    p_memory_agent_run_record.add_argument("--linked-work-item", default="", help="Linked work item id")
    p_memory_agent_run_record.add_argument(
        "--lifecycle",
        choices=sorted(VALID_LIFECYCLES),
        default="captured",
        help="Lifecycle state",
    )
    p_memory_agent_run_record.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_sync = memory_sub.add_parser("sync-report", help="Generate a memory sync report")
    p_memory_sync.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_sync.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_cleanup = memory_sub.add_parser(
        "cleanup-temp",
        help="Diagnose or remove verified pytest temp directories",
    )
    p_memory_cleanup.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_cleanup.add_argument(
        "--apply",
        action="store_true",
        help="Remove only verified pytest temp candidates inside the project root",
    )
    p_memory_cleanup.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_memory_views = memory_sub.add_parser("views", help="Generate project memory markdown views")
    p_memory_views.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    memory_views_sub = p_memory_views.add_subparsers(dest="memory_views_command")
    p_memory_views_generate = memory_views_sub.add_parser("generate", help="Generate all memory views")
    p_memory_views_generate.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_memory_views_generate.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    # cc host
    p_host = sub.add_parser("host", help="Manage primary host selection and host hopping")
    p_host.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    host_sub = p_host.add_subparsers(dest="host_command")

    p_host_status = host_sub.add_parser("status", help="Show current primary host and enabled hosts")
    p_host_status.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_host_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_host_switch = host_sub.add_parser("switch", help="Switch the primary host and sync its derived context file")
    p_host_switch.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_host_switch.add_argument(
        "host",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        help="Host identifier to make primary",
    )
    p_host_switch.add_argument(
        "--preview-only",
        action="store_true",
        help="Show the complete host-switch write plan without changing files",
    )
    p_host_compare = host_sub.add_parser("compare", help="Compare two host gate profiles")
    p_host_compare.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_host_compare.add_argument("left_host", choices=sorted(_GATEWAY_VALID_USER_HOSTS))
    p_host_compare.add_argument("right_host", choices=sorted(_GATEWAY_VALID_USER_HOSTS))
    p_host_compare.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_host_migrate_plan = host_sub.add_parser("migrate-plan", help="Plan a primary host migration")
    p_host_migrate_plan.add_argument(
        "--project-root", type=Path, default=argparse.SUPPRESS,
        help="Project root directory (default: current directory)",
    )
    p_host_migrate_plan.add_argument("from_host", choices=sorted(_GATEWAY_VALID_USER_HOSTS))
    p_host_migrate_plan.add_argument("to_host", choices=sorted(_GATEWAY_VALID_USER_HOSTS))
    p_host_migrate_plan.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    # cc context
    p_context = sub.add_parser("context", help="Check or sync generated host context files")
    p_context.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    context_sub = p_context.add_subparsers(dest="context_command")

    p_context_check = context_sub.add_parser(
        "check",
        help="Verify host context files match the selected context source",
    )
    p_context_check.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_context_check.add_argument(
        "--host",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        default="",
        help="Host identifier to check (default: primary/enabled hosts)",
    )
    p_context_check.add_argument(
        "--all-hosts",
        action="store_true",
        help="Check every supported host projection",
    )
    p_context_check.add_argument(
        "--source",
        default="",
        help=f"Optional context source file such as {CONTROLWORK_CONTEXT_FILENAME} or {CANONICAL_CONTEXT_FILENAME}",
    )
    p_context_check.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_context_diff = context_sub.add_parser(
        "diff",
        help="Show unified diffs for stale generated host context files",
    )
    p_context_diff.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_context_diff.add_argument(
        "--host",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        default="",
        help="Host identifier to diff (default: primary/enabled hosts)",
    )
    p_context_diff.add_argument(
        "--all-hosts",
        action="store_true",
        help="Diff every supported host projection",
    )
    p_context_diff.add_argument(
        "--source",
        default="",
        help=f"Optional context source file such as {CONTROLWORK_CONTEXT_FILENAME} or {CANONICAL_CONTEXT_FILENAME}",
    )
    p_context_diff.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_context_drift = context_sub.add_parser(
        "drift",
        help="Check CONTROLCODING.md against active zones, invariants, and host files",
    )
    p_context_drift.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_context_drift.add_argument(
        "--host",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        default="",
        help="Host identifier to check (default: primary/enabled hosts)",
    )
    p_context_drift.add_argument(
        "--all-hosts",
        action="store_true",
        help="Check every supported host projection",
    )
    p_context_drift.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_context_sync = context_sub.add_parser(
        "sync",
        help="Regenerate host context files from the selected context source",
    )
    p_context_sync.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_context_sync.add_argument(
        "--host",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        default="",
        help="Host identifier to sync (default: primary/enabled hosts)",
    )
    p_context_sync.add_argument(
        "--all-hosts",
        action="store_true",
        help="Sync every supported host projection",
    )
    p_context_sync.add_argument(
        "--source",
        default="",
        help=f"Optional context source file such as {CONTROLWORK_CONTEXT_FILENAME} or {CANONICAL_CONTEXT_FILENAME}",
    )
    p_context_sync.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_context_sync.add_argument(
        "--preview-only",
        action="store_true",
        help="Show the complete adapter plan without changing files",
    )

    p_context_adopt = context_sub.add_parser(
        "adopt",
        help="Explicitly adopt unmarked or explicitly foreign safe adapter targets",
        description=(
            "Explicitly adopt unmarked or explicitly foreign safe adapter targets. "
            "Invalid, ambiguous, and unreadable or unsafe targets remain blocked."
        ),
    )
    p_context_adopt.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_context_adopt.add_argument(
        "--host",
        choices=sorted(_GATEWAY_VALID_USER_HOSTS),
        default="",
        help="Host identifier to adopt (default: primary/enabled hosts)",
    )
    p_context_adopt.add_argument(
        "--all-hosts",
        action="store_true",
        help=(
            "Adopt every supported unmarked or explicitly foreign safe adapter "
            "target as one transaction"
        ),
    )
    p_context_adopt.add_argument(
        "--source",
        default="",
        help=f"Optional context source file such as {CONTROLWORK_CONTEXT_FILENAME} or {CANONICAL_CONTEXT_FILENAME}",
    )
    p_context_adopt.add_argument(
        "--apply",
        action="store_true",
        help="Apply the adoption plan; without this flag adoption is preview-only",
    )
    p_context_adopt.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    # cc write-path
    p_write_path = sub.add_parser(
        "write-path",
        help="Manage the opt-in controlled write path prototype",
    )
    p_write_path.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    write_path_sub = p_write_path.add_subparsers(dest="write_path_command")

    p_write_path_status = write_path_sub.add_parser("status", help="Show controlled write path status")
    p_write_path_status.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_write_path_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_write_path_receipts = write_path_sub.add_parser(
        "receipts",
        help="List recent write-path receipts",
    )
    p_write_path_receipts.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_write_path_receipts.add_argument(
        "--limit", type=int, default=10,
        help="Maximum number of receipts to show (default: 10)",
    )
    p_write_path_receipts.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_write_path_receipt = write_path_sub.add_parser(
        "receipt",
        help="Show a single write-path receipt",
    )
    p_write_path_receipt.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_write_path_receipt.add_argument(
        "--receipt-id", required=True,
        help="Stored write-path receipt id to display",
    )
    p_write_path_receipt.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_write_path_enable = write_path_sub.add_parser("enable", help="Enable the controlled write path")
    p_write_path_enable.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_write_path_enable.add_argument(
        "--mode",
        choices=sorted(mode for mode in _VALID_CONTROLLED_WRITE_MODES if mode != "off"),
        default="patch_gateway",
        help="Controlled write mode to enable (default: patch_gateway)",
    )
    p_write_path_enable.add_argument(
        "--patch-format",
        choices=sorted(_VALID_CONTROLLED_WRITE_PATCH_FORMATS),
        default="unified_diff",
        help="Patch format expected by the gateway (default: unified_diff)",
    )
    p_write_path_enable.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    p_write_path_disable = write_path_sub.add_parser("disable", help="Disable the controlled write path")
    p_write_path_disable.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_write_path_disable.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    for name, help_text in (
        ("prepare", "Validate a patch and persist a reusable manifest for later apply"),
        ("check", "Validate a patch through the controlled write path without applying it"),
        ("apply", "Validate and apply a patch through the controlled write path"),
    ):
        p_write_path_gate = write_path_sub.add_parser(name, help=help_text)
        p_write_path_gate.add_argument(
            "--project-root", type=Path, default=Path.cwd(),
            help="Project root directory (default: current directory)",
        )
        p_write_path_gate.add_argument(
            "--patch-file", type=Path, required=True,
            help="Unified diff patch file to validate/apply",
        )
        if name in {"check", "apply"}:
            p_write_path_gate.add_argument(
                "--manifest-id", default="",
                help="Optional prepared manifest id to verify against the patch; required on apply when manifest gating is enabled",
            )
        p_write_path_gate.add_argument(
            "--reason", default="",
            help="Optional short reason for the write-path event log",
        )
        p_write_path_gate.add_argument(
            "--json", action="store_true", dest="json_output",
            help="Output machine-readable JSON",
        )

    p_write_path_enable.add_argument(
        "--require-manifest",
        action="store_true",
        help="Require `cc write-path prepare` + `--manifest-id` before apply",
    )
    p_write_path_enable.add_argument(
        "--preflight-fitness",
        action="store_true",
        help="Run fitness_check.py in a shadow worktree before the real apply",
    )

    # cc benchmark
    p_benchmark_run_group = sub.add_parser(
        "benchmark",
        help="Capture and compare local benchmark evidence",
    )
    p_benchmark_run_group.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    benchmark_run_sub = p_benchmark_run_group.add_subparsers(dest="benchmark_run_command")
    p_benchmark_run = benchmark_run_sub.add_parser("run", help="Write a local benchmark JSON snapshot")
    p_benchmark_run.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_benchmark_run.add_argument(
        "--output", type=Path, default=BENCHMARK_RUN_OUTPUT,
        help=f"Output JSON path (default: {BENCHMARK_RUN_OUTPUT.as_posix()})",
    )
    p_benchmark_run.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_benchmark_compare = benchmark_run_sub.add_parser("compare", help="Compare two benchmark JSON snapshots")
    p_benchmark_compare.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_benchmark_compare.add_argument("baseline", type=Path)
    p_benchmark_compare.add_argument("current", type=Path)
    p_benchmark_compare.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_benchmark_report = benchmark_run_sub.add_parser("report", help="Write a benchmark evidence report")
    p_benchmark_report.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_benchmark_report.add_argument(
        "--output", type=Path, default=BENCHMARK_REPORT_OUTPUT,
        help=f"Output markdown path (default: {BENCHMARK_REPORT_OUTPUT.as_posix()})",
    )
    p_benchmark_report.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    # cc benchmark-matrix
    p_benchmark = sub.add_parser(
        "benchmark-matrix",
        help="Generate the multi-host capability/evidence matrix",
    )
    p_benchmark.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    benchmark_sub = p_benchmark.add_subparsers(dest="benchmark_command")
    p_benchmark_generate = benchmark_sub.add_parser("generate", help="Generate the multi-host capability matrix")
    p_benchmark_generate.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_benchmark_generate.add_argument(
        "--output", type=Path, default=BENCHMARK_MATRIX_OUTPUT,
        help=f"Output markdown path (default: {BENCHMARK_MATRIX_OUTPUT.as_posix()})",
    )
    p_benchmark_generate.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )
    p_benchmark_local = benchmark_sub.add_parser(
        "report-local",
        help="Generate a local dogfooding evidence report for the current repo",
    )
    p_benchmark_local.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_benchmark_local.add_argument(
        "--output", type=Path, default=BENCHMARK_LOCAL_REPORT_OUTPUT,
        help=f"Output markdown path (default: {BENCHMARK_LOCAL_REPORT_OUTPUT.as_posix()})",
    )
    p_benchmark_local.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    # cc surface
    p_surface = sub.add_parser(
        "surface",
        help="Manage the local Primary/Observer surface lock",
    )
    p_surface.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    surface_sub = p_surface.add_subparsers(dest="surface_command")

    p_surface_status = surface_sub.add_parser("status", help="Show surface authority status")
    p_surface_status.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_surface_status.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output machine-readable JSON",
    )

    for name, help_text in (
        ("claim", "Register a primary surface"),
        ("observe", "Register an observer surface"),
    ):
        p_surface_role = surface_sub.add_parser(name, help=help_text)
        p_surface_role.add_argument(
            "--project-root", type=Path, default=Path.cwd(),
            help="Project root directory (default: current directory)",
        )
        p_surface_role.add_argument("surface_id", help="Stable local surface identifier")
        p_surface_role.add_argument(
            "--type",
            dest="surface_type",
            choices=sorted(VALID_SURFACE_TYPES),
            default="official_host",
            help="Surface type (default: official_host)",
        )
        p_surface_role.add_argument(
            "--label",
            default="",
            help="Human-readable label for the surface",
        )

    p_surface_request_takeover = surface_sub.add_parser(
        "request-takeover",
        help="Request primary authority from an observer surface",
    )
    p_surface_request_takeover.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_surface_request_takeover.add_argument("surface_id", help="Observer surface requesting primary authority")
    p_surface_request_takeover.add_argument(
        "--reason",
        default="",
        help="Optional operator note recorded with the pending takeover",
    )

    p_surface_resolve_takeover = surface_sub.add_parser(
        "resolve-takeover",
        help="Approve or deny the active takeover request",
    )
    p_surface_resolve_takeover.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_surface_resolve_takeover.add_argument("request_id", help="Active takeover request id")
    p_surface_resolve_takeover.add_argument(
        "--decision",
        choices=["approved", "denied"],
        required=True,
        help="Whether the current primary approves or denies the takeover",
    )
    p_surface_resolve_takeover.add_argument(
        "--resolved-by",
        dest="resolved_by_surface_id",
        required=True,
        help="Surface id of the current primary resolving the takeover",
    )

    p_surface_heartbeat = surface_sub.add_parser("heartbeat", help="Refresh last-seen time for a surface")
    p_surface_heartbeat.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_surface_heartbeat.add_argument("surface_id", help="Stable local surface identifier")

    p_surface_release = surface_sub.add_parser("release", help="Detach a surface from the local lock")
    p_surface_release.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_surface_release.add_argument("surface_id", help="Stable local surface identifier")

    p_surface_run = surface_sub.add_parser(
        "run",
        help="Launch a host or CC-aware wrapper while managing claim/heartbeat/release automatically",
    )
    p_surface_run.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_surface_run.add_argument("surface_id", help="Stable local surface identifier")
    p_surface_run.add_argument(
        "--type",
        dest="surface_type",
        choices=sorted(VALID_SURFACE_TYPES),
        default="official_host",
        help="Surface type (default: official_host)",
    )
    p_surface_run.add_argument(
        "--role",
        choices=sorted(VALID_SURFACE_ROLES),
        default="primary",
        help="Surface role to claim before launch (default: primary)",
    )
    p_surface_run.add_argument(
        "--label",
        default="",
        help="Human-readable label for the surface",
    )
    p_surface_run.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=20.0,
        help="Heartbeat interval while the child process is running (default: 20)",
    )
    p_surface_run.add_argument(
        "--keep-lock",
        action="store_true",
        help="Keep the surface registered after the child process exits",
    )

    # cc review
    add_review_parser(sub)

    # cc init-module
    add_init_module_parser(sub)

    # cc organize
    p_organize = sub.add_parser("organize", help="Organize project files per CC standard")
    p_organize.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root directory (default: current directory)")
    p_organize.add_argument("--dry-run", action="store_true", help="Show proposed changes without executing them")
    p_organize.add_argument("--apply", action="store_true", help="Execute proposed moves and writes. Without this, organize is preview-only.")
    p_organize.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON report")
    p_organize.add_argument("--check", action="store_true", help="Check only: exit 1 if files violate the standard (for CI)")
    p_organize.add_argument("--dev-taxonomy", action="store_true", help="Also organize clearly classified root/dev docs into dev/ taxonomy")

    # cc export
    p_export = sub.add_parser("export", help="Export project data")
    p_export.add_argument(
        "format", choices=["agents-md", "host-context"],
        help="Export format (agents-md: generate AGENTS.md from the canonical context file; host-context: generate the selected host-native context file)",
    )
    p_export.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_export.add_argument(
        "--host", default="",
        choices=["", *_GATEWAY_VALID_USER_HOSTS],
        help=f"Target host for host-context export (default: read userHost from {_control_plane_display_path('gateway_config.json')})",
    )
    p_export.add_argument(
        "--source",
        default="",
        help=f"Optional context source file such as {CONTROLWORK_CONTEXT_FILENAME} or {CANONICAL_CONTEXT_FILENAME}",
    )
    p_export.add_argument(
        "--force", action="store_true",
        help="Replace an existing valid-owned adapter; never adopts an unowned file",
    )
    p_export.add_argument(
        "--preview-only", action="store_true",
        help="Show the complete adapter plan without changing files",
    )

    p_resume = sub.add_parser(
        "resume",
        help="Print a provider-neutral context brief without launching a provider or subprocess",
        description="Print a provider-neutral context brief without launching a provider or subprocess",
    )
    p_resume.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_resume.add_argument(
        "--brief", action="store_true",
        help="Accepted for CLI compatibility; the current command always prints the brief",
    )
    p_resume.add_argument(
        "--agent", default="",
        metavar="NAME",
        help="Resume a specific agent (concierge, architect, coder, reviewer, debugger)",
    )

    p_agents = sub.add_parser(
        "agents",
        help="Show status of all persistent agents",
    )
    p_agents.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )

    p_index = sub.add_parser(
        "index",
        help="Scan tracked directories for files missing from ARCHITECTURE_INDEX.md",
    )
    p_index.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    p_index.add_argument(
        "--fix", action="store_true",
        help="Automatically add stub entries for missing files",
    )
    p_index.add_argument(
        "--check", action="store_true",
        help="Exit 1 if any files are missing (for CI/pre-commit use)",
    )

    args, extra_args = parser.parse_known_args()

    if not args.command:
        parser.print_help()
        return 1

    if extra_args and not (args.command == "surface" and getattr(args, "surface_command", "") == "run"):
        parser.error(f"unrecognized arguments: {' '.join(extra_args)}")

    project = (Path(os.path.abspath(args.project_root)) if args.command in {"install", "init"}
               else args.project_root.resolve())
    if args.command != "init" and not project.is_dir() and not (
        args.command == "install" and args.preview_only and not project.exists()
    ):
        message = f"{project} is not a directory"
        if getattr(args, "json_output", False):
            print(json.dumps({
                "ok": False,
                "error": "invalid_project_root",
                "message": message,
                "projectRoot": str(project),
            }, indent=2, ensure_ascii=False))
        else:
            print(f"Error: {message}")
        return 1

    if args.command == "init":
        return cmd_init(project, central_hooks=getattr(args, "central_hooks", False), preview_only=args.preview_only)
    elif args.command == "setup":
        if getattr(args, "chat_guide", False):
            return cmd_setup_chat_guide(
                project,
                host_hint=getattr(args, "host_hint", ""),
            )
        if getattr(args, "engagement", False):
            return cmd_setup_engagement(
                project,
                answers_file=getattr(args, "answers_file", None),
                apply_answers=getattr(args, "apply_answers", False),
            )
        return cmd_setup(
            project,
            answers_file=getattr(args, "answers_file", None),
            apply_answers=getattr(args, "apply_answers", False),
        )
    elif args.command == "setup-project":
        if getattr(args, "chat_guide", False):
            return cmd_setup_project_chat_guide(
                project,
                host_hint=getattr(args, "host_hint", ""),
            )
        return cmd_setup_project(
            project,
            answers_file=getattr(args, "answers_file", None),
            apply_answers=getattr(args, "apply_answers", False),
        )
    elif args.command == "consult":
        consult_command = getattr(args, "consult_command", "")
        if consult_command == "status":
            return cmd_consult_status(
                project,
                role=getattr(args, "role", ""),
                json_output=getattr(args, "json_output", False),
            )
        if consult_command == "resolution":
            return cmd_consult_resolution_show(
                project,
                role=getattr(args, "role", ""),
                topic_key=getattr(args, "topic_key", ""),
                json_output=getattr(args, "json_output", False),
            )
        p_consult.print_help()
        return 1
    elif args.command == "consult-packet":
        consult_packet_command = getattr(args, "consult_packet_command", "")
        if consult_packet_command == "create":
            return cmd_consult_packet_create(
                project,
                role=getattr(args, "role"),
                objective=getattr(args, "objective"),
                questions=getattr(args, "question", []),
                context_summary=getattr(args, "context_summary", ""),
                constraints=getattr(args, "constraint", []),
                expected_answer_shape=getattr(args, "expected_answer_shape", ""),
                topic_key=getattr(args, "topic_key", ""),
                thread_id=getattr(args, "thread_id", ""),
                json_output=getattr(args, "json_output", False),
            )
        if consult_packet_command == "show":
            return cmd_consult_packet_show(
                project,
                packet_id=getattr(args, "packet_id"),
                json_output=getattr(args, "json_output", False),
            )
        p_consult_packet.print_help()
        return 1
    elif args.command == "consult-result":
        consult_result_command = getattr(args, "consult_result_command", "")
        if consult_result_command == "import":
            return cmd_consult_result_import(
                project,
                packet_id=getattr(args, "packet_id"),
                summary=getattr(args, "summary"),
                decision=getattr(args, "decision"),
                rationale_summary=getattr(args, "rationale_summary"),
                next_action=getattr(args, "next_action"),
                constraints=getattr(args, "constraint", []),
                evidence=getattr(args, "evidence", []),
                source=getattr(args, "source", "manual_external_consult"),
                json_output=getattr(args, "json_output", False),
            )
        p_consult_result.print_help()
        return 1
    elif args.command == "install":
        return cmd_install(project, args.pack, preview_only=args.preview_only)
    elif args.command == "chat-start":
        return cmd_chat_start(
            project,
            topic=getattr(args, "topic", ""),
            scope=getattr(args, "scope", "dev"),
            limit=getattr(args, "limit", 10),
            include_legacy=getattr(args, "include_legacy", False),
            json_output=getattr(args, "json_output", False),
        )
    elif args.command == "work-start":
        return cmd_work_start(
            project,
            topic=getattr(args, "topic", ""),
            scope=getattr(args, "scope", "dev"),
            limit=getattr(args, "limit", 10),
            json_output=getattr(args, "json_output", False),
        )
    elif args.command == "work-close":
        return cmd_work_close(
            project,
            topic=getattr(args, "topic", ""),
            summary=getattr(args, "summary", ""),
            tests=getattr(args, "test", []),
            next_action=getattr(args, "next_action", ""),
            json_output=getattr(args, "json_output", False),
        )
    elif args.command == "release-doctor":
        return cmd_release_doctor(
            project,
            json_output=getattr(args, "json_output", False),
        )
    elif args.command == "doctor":
        return cmd_doctor(
            project,
            json_output=getattr(args, "json_output", False),
            strict_claims=getattr(args, "strict_claims", False),
            strict_verification=getattr(args, "strict_verification", False),
            strict_invariants=getattr(args, "strict_invariants", False),
            release_mode=getattr(args, "release_mode", False),
        )
    elif args.command == "truth":
        truth_command = getattr(args, "truth_command", "")
        if truth_command == "report":
            return cmd_truth_report(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if truth_command == "check":
            return cmd_truth_check(
                project,
                json_output=getattr(args, "json_output", False),
                include_docs=getattr(args, "include_docs", False),
            )
        if truth_command == "check-docs":
            return cmd_truth_check_docs(
                project,
                json_output=getattr(args, "json_output", False),
            )
        p_truth.print_help()
        return 1
    elif args.command == "docs":
        return dispatch_docs_command(args, project, p_docs)
    elif args.command == "verify":
        # Preserve the supplied root spelling for no-follow evidence acquisition.
        project = Path(os.path.abspath(args.project_root))
        verify_command = getattr(args, "verify_command", "")
        if verify_command == "init":
            return cmd_verify_init(
                project,
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if verify_command == "status":
            return cmd_verify_status(
                project,
                require_current=getattr(args, "require_current", False),
                json_output=getattr(args, "json_output", False),
            )
        if verify_command == "run":
            return cmd_verify_run(
                project,
                suite_ids=getattr(args, "suite", []),
                kinds=getattr(args, "kind", []),
                all_suites=getattr(args, "all_suites", False),
                json_output=getattr(args, "json_output", False),
            )
        p_verify.print_help()
        return 1
    elif args.command == "invariants":
        project = Path(os.path.abspath(args.project_root))
        invariants_command = getattr(args, "invariants_command", "")
        if invariants_command == "init":
            return cmd_invariants_init(
                project,
                domain=getattr(args, "domain", "tooling"),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "elicit":
            return cmd_invariants_elicit(
                project,
                domain=getattr(args, "domain", "generic"),
                write=getattr(args, "write", False),
                output_path=getattr(args, "output", None),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "status":
            return cmd_invariants_status(
                project,
                require_current=getattr(args, "require_current", False),
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "list":
            return cmd_invariants_list(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "report":
            return cmd_invariants_report(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "doctor":
            return cmd_invariants_doctor(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "wire-ci":
            return cmd_invariants_wire_ci(
                project,
                provider=getattr(args, "provider", "github-actions"),
                command=getattr(args, "ci_command", ""),
                output_path=getattr(args, "output", None),
                write=getattr(args, "write", False),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "add":
            return cmd_invariants_add(
                project,
                invariant_id=getattr(args, "invariant_id", ""),
                domain=getattr(args, "domain", ""),
                property_text=getattr(args, "property_text", ""),
                kind=getattr(args, "kind", "domain"),
                severity=getattr(args, "severity", "blocking"),
                status=getattr(args, "status", "active"),
                command=getattr(args, "command", ""),
                threshold=getattr(args, "threshold", ""),
                evidence=getattr(args, "evidence", []),
                title=getattr(args, "title", ""),
                json_output=getattr(args, "json_output", False),
            )
        if invariants_command == "run":
            return cmd_invariants_run(
                project,
                invariant_ids=getattr(args, "invariant_id", []),
                domains=getattr(args, "domain", []),
                kinds=getattr(args, "kind", []),
                all_invariants=getattr(args, "all_invariants", False),
                json_output=getattr(args, "json_output", False),
            )
        p_invariants.print_help()
        return 1
    elif args.command == "feature":
        return dispatch_feature_command(args, project, p_feature)
    elif args.command == "promote":
        promote_command = getattr(args, "promote_command", "")
        if promote_command == "plan":
            return cmd_promote_plan(
                project,
                source_path=getattr(args, "path", ""),
                target_zone=getattr(args, "to", ""),
                target_path=getattr(args, "target_path", ""),
                feature_name=getattr(args, "feature", ""),
                reason=getattr(args, "reason", ""),
                json_output=getattr(args, "json_output", False),
            )
        if promote_command == "check":
            return cmd_promote_check(
                project,
                source_path=getattr(args, "path", ""),
                target_zone=getattr(args, "to", ""),
                target_path=getattr(args, "target_path", ""),
                feature_name=getattr(args, "feature", ""),
                json_output=getattr(args, "json_output", False),
            )
        if promote_command == "apply":
            return cmd_promote_apply(
                project,
                source_path=getattr(args, "path", ""),
                target_zone=getattr(args, "to", ""),
                target_path=getattr(args, "target_path", ""),
                feature_name=getattr(args, "feature", ""),
                reason=getattr(args, "reason", ""),
                create_adr=getattr(args, "adr", False),
                json_output=getattr(args, "json_output", False),
            )
        p_promote.print_help()
        return 1
    elif args.command == "memory":
        memory_command = getattr(args, "memory_command", "")
        if memory_command == "init":
            return cmd_memory_init(
                project,
                mode=getattr(args, "mode", "full"),
                project_short=getattr(args, "project_short", ""),
                profile=getattr(args, "profile", "project"),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-init":
            return cmd_memory_work_init(
                project,
                project_name=getattr(args, "name", ""),
                purpose=getattr(args, "purpose", "Project knowledge and work memory"),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-status":
            return cmd_memory_work_status(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-parity":
            return cmd_memory_work_parity(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-quickstart":
            return cmd_memory_work_quickstart(
                project,
                project_name=getattr(args, "name", ""),
                purpose=getattr(args, "purpose", "Project knowledge and work memory"),
                scope=getattr(args, "scope", "general"),
                topic=getattr(args, "topic", "project direction"),
                limit=getattr(args, "limit", 10),
                scan_limit=getattr(args, "scan_limit", 50),
                include_legacy=getattr(args, "include_legacy", False),
                checkpoint=getattr(args, "checkpoint", False),
                checkpoint_title=getattr(args, "checkpoint_title", ""),
                dry_run=getattr(args, "dry_run", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-attach":
            return cmd_memory_work_attach(
                project,
                path=getattr(args, "path", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-import":
            return cmd_memory_work_import(
                project,
                path=getattr(args, "path", ""),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-export":
            return cmd_memory_work_export(
                project,
                target=getattr(args, "target", ""),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-sync":
            return cmd_memory_work_sync(
                project,
                direction=getattr(args, "direction", ""),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-category":
            work_category_command = getattr(args, "work_category_command", "")
            if work_category_command == "list":
                return cmd_memory_work_category_list(
                    project,
                    status=getattr(args, "status", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if work_category_command == "propose":
                return cmd_memory_work_category_propose(
                    project,
                    name=getattr(args, "name", ""),
                    area=getattr(args, "area", "notes"),
                    description=getattr(args, "description", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if work_category_command == "add":
                return cmd_memory_work_category_add(
                    project,
                    name=getattr(args, "name", ""),
                    area=getattr(args, "area", "notes"),
                    description=getattr(args, "description", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if work_category_command == "approve":
                return cmd_memory_work_category_approve(
                    project,
                    category=getattr(args, "category", ""),
                    json_output=getattr(args, "json_output", False),
                )
        if memory_command == "work-scan":
            return cmd_memory_work_scan(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-analyze":
            return cmd_memory_work_analyze(
                project,
                limit=getattr(args, "limit", 50),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-review":
            return cmd_memory_work_review(
                project,
                path=getattr(args, "path", ""),
                review_status=getattr(args, "review_status", ""),
                sensitivity=getattr(args, "sensitivity", ""),
                note=getattr(args, "note", ""),
                filter_name=getattr(args, "filter", "pending"),
                batch=getattr(args, "batch", False),
                item=getattr(args, "item", ""),
                action=getattr(args, "action", ""),
                canonical=getattr(args, "canonical", ""),
                fingerprint=getattr(args, "fingerprint", ""),
                proposal=getattr(args, "proposal", False),
                output=getattr(args, "output", None),
                force_output=getattr(args, "force_output", False),
                limit=getattr(args, "limit", 20),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-import-source":
            return cmd_memory_work_import_source(
                project,
                path=getattr(args, "path", ""),
                area=getattr(args, "area", "sources"),
                title=getattr(args, "title", ""),
                summary=getattr(args, "summary", ""),
                lifecycle=getattr(args, "lifecycle", "needs_review"),
                category=getattr(args, "category", ""),
                max_chars=getattr(args, "max_chars", 12000),
                as_reference=getattr(args, "as_reference", False),
                dry_run=getattr(args, "dry_run", False),
                proposal=getattr(args, "proposal", False),
                output=getattr(args, "output", None),
                force=getattr(args, "force", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-ocr":
            return cmd_memory_work_ocr(
                project,
                action=getattr(args, "work_ocr_command", ""),
                source=getattr(args, "source", ""),
                sidecar=getattr(args, "sidecar", None),
                output=getattr(args, "output", None),
                area=getattr(args, "area", "sources"),
                title=getattr(args, "title", ""),
                summary=getattr(args, "summary", ""),
                lifecycle=getattr(args, "lifecycle", "needs_review"),
                category=getattr(args, "category", ""),
                import_source=getattr(args, "import_source", False),
                dry_run=getattr(args, "dry_run", False),
                force=getattr(args, "force", False),
                limit=getattr(args, "limit", 20),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-promote":
            return cmd_memory_work_promote(
                project,
                path=getattr(args, "path", ""),
                area=getattr(args, "area", "sources"),
                title=getattr(args, "title", ""),
                summary=getattr(args, "summary", ""),
                lifecycle=getattr(args, "lifecycle", "captured"),
                category=getattr(args, "category", ""),
                include_excerpt=getattr(args, "include_excerpt", False),
                force=getattr(args, "force", False),
                force_note=getattr(args, "force_note", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-capture":
            return cmd_memory_work_capture(
                project,
                area=getattr(args, "area", ""),
                title=getattr(args, "title", ""),
                body=getattr(args, "body", ""),
                lifecycle=getattr(args, "lifecycle", "captured"),
                source=getattr(args, "source", ""),
                category=getattr(args, "category", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-session":
            work_session_command = getattr(args, "work_session_command", "")
            return cmd_memory_work_session(
                project,
                action=work_session_command,
                session_id=getattr(args, "session_id", ""),
                topic=getattr(args, "topic", ""),
                mode=getattr(args, "mode", "continue_previous_work"),
                operator=getattr(args, "operator", "manual"),
                summary=getattr(args, "summary", ""),
                category=getattr(args, "category", []),
                status=getattr(args, "status", "completed"),
                followup=getattr(args, "followup", []),
                decision=getattr(args, "decision", []),
                link_type=getattr(args, "type", ""),
                target=getattr(args, "target", ""),
                target_type=getattr(args, "target_type", ""),
                text=getattr(args, "text", ""),
                kind=getattr(args, "kind", "note"),
                topic_filter=getattr(args, "topic", ""),
                limit=getattr(args, "limit", 20),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-graph":
            return cmd_memory_work_graph(
                project,
                action=getattr(args, "work_graph_command", ""),
                status=getattr(args, "status", "suggested"),
                confidence=getattr(args, "confidence", ""),
                limit=getattr(args, "limit", 50),
                suggestion_id=getattr(args, "suggestion_id", ""),
                reason=getattr(args, "reason", ""),
                selector=getattr(args, "selector", ""),
                source_selector=getattr(args, "source_selector", ""),
                target_selector=getattr(args, "target_selector", ""),
                max_depth=getattr(args, "max_depth", 3),
                include_suggestions=getattr(args, "include_suggestions", False),
                include_chunks=getattr(args, "include_chunks", False),
                no_scan=getattr(args, "no_scan", False),
                baseline=getattr(args, "baseline", None),
                output=getattr(args, "output", None),
                output_format=getattr(args, "format", "json"),
                node_types=getattr(args, "type", []),
                lifecycles=getattr(args, "lifecycle", []),
                source=getattr(args, "source", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-query":
            return cmd_memory_work_query(
                project,
                query=getattr(args, "query", ""),
                limit=getattr(args, "limit", 10),
                include_legacy=getattr(args, "include_legacy", False),
                path_to=getattr(args, "path_to", ""),
                max_depth=getattr(args, "max_depth", 3),
                include_suggestions=getattr(args, "include_suggestions", False),
                output=getattr(args, "output", None),
                stdout=getattr(args, "stdout", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-retrieve":
            return cmd_memory_work_retrieve(
                project,
                query=getattr(args, "query", ""),
                limit=getattr(args, "limit", 10),
                include_legacy=getattr(args, "include_legacy", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-rag-pack":
            return cmd_memory_work_rag_pack(
                project,
                query=getattr(args, "query", ""),
                limit=getattr(args, "limit", 10),
                include_legacy=getattr(args, "include_legacy", False),
                output=getattr(args, "output", None),
                stdout=getattr(args, "stdout", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-views":
            return cmd_memory_work_views(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-checkpoint":
            return cmd_memory_work_checkpoint(
                project,
                title=getattr(args, "title", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-handoff":
            return cmd_memory_work_handoff(
                project,
                output=getattr(args, "output", None),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-dashboard":
            return cmd_memory_work_dashboard(
                project,
                output_format=getattr(args, "format", "html"),
                output=getattr(args, "output", None),
                limit=getattr(args, "limit", 20),
                scan_limit=getattr(args, "scan_limit", 50),
                no_refresh_scan=getattr(args, "no_refresh_scan", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-context-pack":
            return cmd_memory_work_context_pack(
                project,
                scope=getattr(args, "scope", "general"),
                topic=getattr(args, "topic", ""),
                limit=getattr(args, "limit", 10),
                include_legacy=getattr(args, "include_legacy", False),
                output=getattr(args, "output", None),
                stdout=getattr(args, "stdout", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-obsidian":
            return cmd_memory_work_obsidian(
                project,
                action=getattr(args, "action", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-wiki":
            return cmd_memory_work_wiki(
                project,
                action=getattr(args, "action", ""),
                review=getattr(args, "review", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "work-mcp":
            work_mcp_command = getattr(args, "work_mcp_command", "")
            if work_mcp_command == "tools":
                return cmd_memory_work_mcp_tools(
                    json_output=getattr(args, "json_output", False),
                )
            if work_mcp_command == "call":
                return cmd_memory_work_mcp_call(
                    project,
                    tool=getattr(args, "tool", ""),
                    args_file=getattr(args, "args_file", None),
                    json_output=getattr(args, "json_output", False),
                )
        if memory_command == "doctor":
            return cmd_memory_doctor(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "scan":
            return cmd_memory_scan(
                project,
                json_output=getattr(args, "json_output", False),
                scope=getattr(args, "scope", "full"),
            )
        if memory_command == "status":
            return cmd_memory_status(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "bootstrap":
            return cmd_memory_bootstrap(
                project,
                scope=getattr(args, "scope", "general"),
                topic=getattr(args, "topic", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "op-index":
            return cmd_memory_op_index(
                project,
                scope=getattr(args, "scope", "general"),
                topic=getattr(args, "topic", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "startup":
            return cmd_memory_startup(
                project,
                scope=getattr(args, "scope", "general"),
                topic=getattr(args, "topic", ""),
                intent=getattr(args, "intent", "ask"),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "session":
            session_command = getattr(args, "memory_session_command", "")
            if session_command == "start":
                return cmd_memory_session_start(
                    project,
                    topic=getattr(args, "topic", ""),
                    mode=getattr(args, "mode", "continue_previous_work"),
                    scope=getattr(args, "scope", "dev"),
                    session_id=getattr(args, "session_id", ""),
                    categories=getattr(args, "category", []),
                    summary=getattr(args, "summary", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if session_command == "close":
                return cmd_memory_session_close(
                    project,
                    session_id=getattr(args, "session_id", ""),
                    status=getattr(args, "status", "completed"),
                    summary=getattr(args, "summary", ""),
                    followups=getattr(args, "followup", []),
                    decisions=getattr(args, "decision", []),
                    commits=getattr(args, "commit", []),
                    json_output=getattr(args, "json_output", False),
                )
            if session_command == "list":
                return cmd_memory_session_list(
                    project,
                    status=getattr(args, "status", "all"),
                    topic=getattr(args, "topic", ""),
                    limit=getattr(args, "limit", 20),
                    json_output=getattr(args, "json_output", False),
                )
            if session_command == "show":
                return cmd_memory_session_show(
                    project,
                    session_id=getattr(args, "session_id", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if session_command == "link":
                link_type = getattr(args, "link_type", "")
                target = getattr(args, "target", "")
                target_type = getattr(args, "target_type", "")
                if getattr(args, "commit", ""):
                    link_type = "produced_commit"
                    target = getattr(args, "commit", "")
                    target_type = "commit"
                return cmd_memory_session_link(
                    project,
                    session_id=getattr(args, "session_id", ""),
                    link_type=link_type,
                    target=target,
                    target_type=target_type,
                    json_output=getattr(args, "json_output", False),
                )
            if session_command == "note":
                return cmd_memory_session_note(
                    project,
                    session_id=getattr(args, "session_id", ""),
                    text=getattr(args, "text", ""),
                    kind=getattr(args, "kind", "note"),
                    json_output=getattr(args, "json_output", False),
                )
            if session_command == "views":
                return cmd_memory_session_views(
                    project,
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_session.print_help()
            return 1
        if memory_command == "session-pack":
            return cmd_memory_session_pack(
                project,
                topic=getattr(args, "topic", ""),
                mode=getattr(args, "mode", ""),
                status=getattr(args, "status", "all"),
                limit=getattr(args, "limit", 10),
                output=getattr(args, "output", None),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "graph":
            graph_command = getattr(args, "memory_graph_command", "")
            if graph_command == "status":
                return cmd_memory_graph_status(
                    project,
                    json_output=getattr(args, "json_output", False),
                )
            if graph_command == "suggestions":
                return cmd_memory_graph_suggestions(
                    project,
                    status=getattr(args, "status", "suggested"),
                    limit=getattr(args, "limit", 50),
                    json_output=getattr(args, "json_output", False),
                )
            if graph_command == "accept":
                return cmd_memory_graph_accept(
                    project,
                    suggestion_id=getattr(args, "suggestion_id", ""),
                    reason=getattr(args, "reason", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if graph_command == "reject":
                return cmd_memory_graph_reject(
                    project,
                    suggestion_id=getattr(args, "suggestion_id", ""),
                    reason=getattr(args, "reason", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if graph_command == "around":
                return cmd_memory_graph_around(
                    project,
                    selector=getattr(args, "selector", ""),
                    depth=getattr(args, "depth", 1),
                    json_output=getattr(args, "json_output", False),
                )
            if graph_command == "export":
                return cmd_memory_graph_export(
                    project,
                    output_format=getattr(args, "output_format", "json"),
                    output=getattr(args, "output", None),
                    type_filters=getattr(args, "type_filters", []),
                    lifecycle_filters=getattr(args, "lifecycle_filters", []),
                    confidence_filters=getattr(args, "confidence_filters", []),
                    source_filters=getattr(args, "source_filters", []),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_graph.print_help()
            return 1
        if memory_command == "retrieve":
            return cmd_memory_retrieve(
                project,
                query=getattr(args, "query", ""),
                scope=getattr(args, "scope", "general"),
                limit=getattr(args, "limit", 10),
                include_archived=getattr(args, "include_archived", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "rag-pack":
            return cmd_memory_rag_pack(
                project,
                query=getattr(args, "query", ""),
                scope=getattr(args, "scope", "general"),
                limit=getattr(args, "limit", 10),
                include_archived=getattr(args, "include_archived", False),
                output=getattr(args, "output", None),
                json_output=getattr(args, "json_output", False),
            )
        memory_aux_result = dispatch_memory_aux_command(args, project, p_memory_evidence)
        if memory_aux_result is not None:
            return memory_aux_result
        if memory_command == "cross-pack":
            return cmd_memory_cross_pack(
                project,
                query=getattr(args, "query", ""),
                scope=getattr(args, "scope", "general"),
                limit=getattr(args, "limit", 10),
                include_archived=getattr(args, "include_archived", False),
                include_legacy=getattr(args, "include_legacy", False),
                output=getattr(args, "output", None),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "semantic":
            semantic_command = getattr(args, "memory_semantic_command", "")
            if semantic_command == "status":
                return cmd_memory_semantic_status(
                    project,
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_semantic.print_help()
            return 1
        if memory_command == "ocr":
            ocr_command = getattr(args, "memory_ocr_command", "")
            if ocr_command == "status":
                return cmd_memory_ocr_status(
                    project,
                    json_output=getattr(args, "json_output", False),
                )
            if ocr_command == "run":
                return cmd_memory_ocr_run(
                    project,
                    source=getattr(args, "source", ""),
                    output=getattr(args, "output", None), force=getattr(args, "force", False),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_ocr.print_help()
            return 1
        if memory_command == "impact":
            return cmd_memory_impact(
                project,
                query=getattr(args, "query", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "context":
            return cmd_memory_context(
                project,
                query=getattr(args, "query", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "dev-context-pack":
            return cmd_memory_dev_context_pack(
                project,
                scope=getattr(args, "scope", "general"),
                topic=getattr(args, "topic", ""),
                limit=getattr(args, "limit", 10),
                output=getattr(args, "output", None),
                stdout=getattr(args, "stdout", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "chunks":
            return cmd_memory_chunks(
                project,
                path=getattr(args, "path", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "layout":
            return cmd_memory_layout(
                project,
                path=getattr(args, "path", ""),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "intake":
            memory_intake_command = getattr(args, "memory_intake_command", "")
            if memory_intake_command == "add":
                return cmd_memory_intake_add(
                    project,
                    title=getattr(args, "title", ""),
                    summary=getattr(args, "summary", ""),
                    source_type=getattr(args, "source_type", "other"),
                    lifecycle=getattr(args, "lifecycle", "captured"),
                    path=getattr(args, "path", ""),
                    source_refs=getattr(args, "source_ref", []),
                    json_output=getattr(args, "json_output", False),
                )
            if memory_intake_command == "promote":
                return cmd_memory_intake_promote(
                    project,
                    selector=getattr(args, "selector", ""),
                    target=getattr(args, "to", ""),
                    lifecycle=getattr(args, "lifecycle", ""),
                    reason=getattr(args, "reason", ""),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_intake.print_help()
            return 1
        if memory_command == "lifecycle":
            lifecycle_command = getattr(args, "memory_lifecycle_command", "")
            if lifecycle_command == "mark":
                return cmd_memory_lifecycle_mark(
                    project,
                    selector=getattr(args, "selector", ""),
                    lifecycle=getattr(args, "state", ""),
                    reason=getattr(args, "reason", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if lifecycle_command == "supersede":
                return cmd_memory_lifecycle_supersede(
                    project,
                    old_selector=getattr(args, "old", ""),
                    new_selector=getattr(args, "new", ""),
                    reason=getattr(args, "reason", ""),
                    json_output=getattr(args, "json_output", False),
                )
            if lifecycle_command == "conflict":
                return cmd_memory_lifecycle_conflict(
                    project,
                    left_selector=getattr(args, "left", ""),
                    right_selector=getattr(args, "right", ""),
                    reason=getattr(args, "reason", ""),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_lifecycle.print_help()
            return 1
        if memory_command == "vector":
            vector_command = getattr(args, "memory_vector_command", "")
            if vector_command == "rebuild":
                return cmd_memory_vector_rebuild(
                    project,
                    json_output=getattr(args, "json_output", False),
                )
            if vector_command == "search":
                return cmd_memory_vector_search(
                    project,
                    query=getattr(args, "query", ""),
                    limit=getattr(args, "limit", 10),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_vector.print_help()
            return 1
        if memory_command == "note":
            if getattr(args, "memory_note_command", "") == "add":
                return cmd_memory_note_add(
                    project,
                    title=getattr(args, "title", ""),
                    body=getattr(args, "body", ""),
                    area=getattr(args, "area", "General"),
                    lifecycle=getattr(args, "lifecycle", "captured"),
                    path=getattr(args, "path", ""),
                    source_refs=getattr(args, "source_ref", []),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_note.print_help()
            return 1
        if memory_command == "idea":
            if getattr(args, "memory_idea_command", "") == "add":
                return cmd_memory_idea_add(
                    project,
                    title=getattr(args, "title", ""),
                    body=getattr(args, "body", ""),
                    area=getattr(args, "area", "General"),
                    lifecycle=getattr(args, "lifecycle", "captured"),
                    path=getattr(args, "path", ""),
                    source_refs=getattr(args, "source_ref", []),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_idea.print_help()
            return 1
        if memory_command == "decision":
            if getattr(args, "memory_decision_command", "") == "add":
                return cmd_memory_decision_add(
                    project,
                    title=getattr(args, "title", ""),
                    body=getattr(args, "body", ""),
                    rationale=getattr(args, "rationale", ""),
                    area=getattr(args, "area", "General"),
                    lifecycle=getattr(args, "lifecycle", "active"),
                    path=getattr(args, "path", ""),
                    source_refs=getattr(args, "source_ref", []),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_decision.print_help()
            return 1
        if memory_command == "consult":
            if getattr(args, "memory_consult_command", "") == "record":
                return cmd_memory_consult_record(
                    project,
                    title=getattr(args, "title", ""),
                    source_type=getattr(args, "source_type", "manual_external"),
                    backend=getattr(args, "backend", ""),
                    question_summary=getattr(args, "question_summary", ""),
                    answer_summary=getattr(args, "answer_summary", ""),
                    summary=getattr(args, "summary", ""),
                    raw_artifact_path=getattr(args, "artifact", ""),
                    decision_outcome=getattr(args, "decision_outcome", "pending"),
                    status=getattr(args, "status", "pending"),
                    affected=getattr(args, "affected", []),
                    lifecycle=getattr(args, "lifecycle", "captured"),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_consult.print_help()
            return 1
        if memory_command == "agent-run":
            if getattr(args, "memory_agent_run_command", "") == "record":
                return cmd_memory_agent_run_record(
                    project,
                    role=getattr(args, "role", ""),
                    host=getattr(args, "host", ""),
                    task=getattr(args, "task", ""),
                    output_summary=getattr(args, "summary", ""),
                    input_context_ref=getattr(args, "input_context_ref", ""),
                    changed_files=getattr(args, "changed_file", []),
                    decisions_proposed=getattr(args, "decision", []),
                    verification=getattr(args, "verification", []),
                    status=getattr(args, "status", "completed"),
                    linked_work_item=getattr(args, "linked_work_item", ""),
                    lifecycle=getattr(args, "lifecycle", "captured"),
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_agent_run.print_help()
            return 1
        if memory_command == "sync-report":
            return cmd_memory_sync_report(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "cleanup-temp":
            return cmd_memory_temp_cleanup(
                project,
                apply=getattr(args, "apply", False),
                json_output=getattr(args, "json_output", False),
            )
        if memory_command == "views":
            if getattr(args, "memory_views_command", "") == "generate":
                return cmd_memory_views_generate(
                    project,
                    json_output=getattr(args, "json_output", False),
                )
            p_memory_views.print_help()
            return 1
        p_memory.print_help()
        return 1
    elif args.command == "host":
        host_command = getattr(args, "host_command", "")
        if host_command == "status":
            return cmd_host_status(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if host_command == "switch":
            return cmd_host_switch(
                project,
                getattr(args, "host"),
                preview_only=getattr(args, "preview_only", False),
            )
        if host_command == "compare":
            return cmd_host_compare(
                project,
                getattr(args, "left_host", ""),
                getattr(args, "right_host", ""),
                json_output=getattr(args, "json_output", False),
            )
        if host_command == "migrate-plan":
            return cmd_host_migrate_plan(
                project,
                getattr(args, "from_host", ""),
                getattr(args, "to_host", ""),
                json_output=getattr(args, "json_output", False),
            )
        p_host.print_help()
        return 1
    elif args.command == "context":
        context_command = getattr(args, "context_command", "")
        if context_command == "check":
            return cmd_context_check(
                project,
                host=getattr(args, "host", ""),
                all_hosts=getattr(args, "all_hosts", False),
                source=getattr(args, "source", ""),
                json_output=getattr(args, "json_output", False),
            )
        if context_command == "diff":
            return cmd_context_diff(
                project,
                host=getattr(args, "host", ""),
                all_hosts=getattr(args, "all_hosts", False),
                source=getattr(args, "source", ""),
                json_output=getattr(args, "json_output", False),
            )
        if context_command == "drift":
            return cmd_context_drift(
                project,
                host=getattr(args, "host", ""),
                all_hosts=getattr(args, "all_hosts", False),
                json_output=getattr(args, "json_output", False),
            )
        if context_command == "sync":
            return cmd_context_sync(
                project,
                host=getattr(args, "host", ""),
                all_hosts=getattr(args, "all_hosts", False),
                source=getattr(args, "source", ""),
                json_output=getattr(args, "json_output", False),
                preview_only=getattr(args, "preview_only", False),
            )
        if context_command == "adopt":
            return cmd_context_adopt(
                project,
                host=getattr(args, "host", ""),
                all_hosts=getattr(args, "all_hosts", False),
                source=getattr(args, "source", ""),
                apply=getattr(args, "apply", False),
                json_output=getattr(args, "json_output", False),
            )
        p_context.print_help()
        return 1
    elif args.command == "write-path":
        write_path_command = getattr(args, "write_path_command", "")
        if write_path_command == "status":
            return cmd_write_path_status(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if write_path_command == "receipts":
            return cmd_write_path_receipts(
                project,
                limit=getattr(args, "limit", 10),
                json_output=getattr(args, "json_output", False),
            )
        if write_path_command == "receipt":
            return cmd_write_path_receipt_show(
                project,
                receipt_id=getattr(args, "receipt_id", ""),
                json_output=getattr(args, "json_output", False),
            )
        if write_path_command == "enable":
            return cmd_write_path_enable(
                project,
                mode=getattr(args, "mode", "patch_gateway"),
                patch_format=getattr(args, "patch_format", "unified_diff"),
                require_manifest_for_apply=getattr(args, "require_manifest", False),
                preflight_fitness_for_apply=getattr(args, "preflight_fitness", False),
                json_output=getattr(args, "json_output", False),
            )
        if write_path_command == "disable":
            return cmd_write_path_disable(
                project,
                json_output=getattr(args, "json_output", False),
            )
        if write_path_command == "prepare":
            return cmd_write_path_prepare(
                project,
                patch_file=getattr(args, "patch_file"),
                reason=getattr(args, "reason", ""),
                json_output=getattr(args, "json_output", False),
            )
        if write_path_command == "check":
            return cmd_write_path_check(
                project,
                patch_file=getattr(args, "patch_file"),
                manifest_id=getattr(args, "manifest_id", ""),
                reason=getattr(args, "reason", ""),
                json_output=getattr(args, "json_output", False),
            )
        if write_path_command == "apply":
            return cmd_write_path_apply(
                project,
                patch_file=getattr(args, "patch_file"),
                manifest_id=getattr(args, "manifest_id", ""),
                reason=getattr(args, "reason", ""),
                json_output=getattr(args, "json_output", False),
            )
        p_write_path.print_help()
        return 1
    elif args.command == "benchmark":
        benchmark_run_command = getattr(args, "benchmark_run_command", "")
        if benchmark_run_command == "run":
            return cmd_benchmark_run(
                project,
                output_path=getattr(args, "output", BENCHMARK_RUN_OUTPUT),
                json_output=getattr(args, "json_output", False),
            )
        if benchmark_run_command == "compare":
            return cmd_benchmark_compare(
                project,
                baseline_path=getattr(args, "baseline"),
                current_path=getattr(args, "current"),
                json_output=getattr(args, "json_output", False),
            )
        if benchmark_run_command == "report":
            return cmd_benchmark_report(
                project,
                output_path=getattr(args, "output", BENCHMARK_REPORT_OUTPUT),
                json_output=getattr(args, "json_output", False),
            )
        p_benchmark_run_group.print_help()
        return 1
    elif args.command == "benchmark-matrix":
        benchmark_command = getattr(args, "benchmark_command", "")
        if benchmark_command == "generate":
            return cmd_benchmark_matrix_generate(
                project,
                output_path=getattr(args, "output", BENCHMARK_MATRIX_OUTPUT),
                json_output=getattr(args, "json_output", False),
            )
        if benchmark_command == "report-local":
            return cmd_benchmark_local_report(
                project,
                output_path=getattr(args, "output", BENCHMARK_LOCAL_REPORT_OUTPUT),
                json_output=getattr(args, "json_output", False),
            )
        p_benchmark.print_help()
        return 1
    elif args.command == "surface":
        surface_command = getattr(args, "surface_command", "")
        if surface_command == "status":
            return cmd_surface_status(project, json_output=getattr(args, "json_output", False))
        if surface_command == "claim":
            return cmd_surface_claim(
                project,
                getattr(args, "surface_id"),
                getattr(args, "surface_type"),
                getattr(args, "label", ""),
            )
        if surface_command == "observe":
            return cmd_surface_observe(
                project,
                getattr(args, "surface_id"),
                getattr(args, "surface_type"),
                getattr(args, "label", ""),
            )
        if surface_command == "request-takeover":
            return cmd_surface_request_takeover(
                project,
                getattr(args, "surface_id"),
                reason=getattr(args, "reason", ""),
            )
        if surface_command == "resolve-takeover":
            return cmd_surface_resolve_takeover(
                project,
                getattr(args, "request_id"),
                getattr(args, "decision"),
                getattr(args, "resolved_by_surface_id"),
            )
        if surface_command == "heartbeat":
            return cmd_surface_heartbeat(project, getattr(args, "surface_id"))
        if surface_command == "release":
            return cmd_surface_release(project, getattr(args, "surface_id"))
        if surface_command == "run":
            return cmd_surface_run(
                project,
                getattr(args, "surface_id"),
                getattr(args, "surface_type"),
                getattr(args, "label", ""),
                getattr(args, "role"),
                float(getattr(args, "heartbeat_seconds", 20.0)),
                bool(getattr(args, "keep_lock", False)),
                list(extra_args),
            )
        p_surface.print_help()
        return 1
    elif args.command == "review":
        return dispatch_review_command(args, project, cmd_review)
    elif args.command == "init-module":
        return dispatch_init_module_command(args, project, cmd_init_module)
    elif args.command == "organize":
        try:
            return cmd_organize(
                project,
                dry_run=getattr(args, "dry_run", False),
                json_output=getattr(args, "json_output", False),
                check=getattr(args, "check", False),
                dev_taxonomy=getattr(args, "dev_taxonomy", False),
                apply=getattr(args, "apply", False),
            )
        except (PermissionError, OSError) as exc:
            if not getattr(args, "check", False):
                raise
            if getattr(args, "json_output", False):
                payload = {"ok": False, "error": "organize_failed", "message": str(exc)}
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 1
            print(f"Error: organize failed: {exc}")
            return 1
    elif args.command == "export":
        fmt = getattr(args, "format", None)
        if fmt == "agents-md":
            return cmd_export_agents_md(
                project,
                force=getattr(args, "force", False),
                preview_only=getattr(args, "preview_only", False),
                source=getattr(args, "source", ""),
            )
        if fmt == "host-context":
            return cmd_export_host_context(
                project,
                host=getattr(args, "host", ""),
                force=getattr(args, "force", False),
                source=getattr(args, "source", ""),
                preview_only=getattr(args, "preview_only", False),
            )
    elif args.command == "resume":
        return cmd_resume(
            project,
            brief_only=getattr(args, "brief", False),
            agent=getattr(args, "agent", ""),
        )
    elif args.command == "agents":
        return cmd_agents(project)
    elif args.command == "index":
        return cmd_index(
            project,
            fix=getattr(args, "fix", False),
            check=getattr(args, "check", False),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
