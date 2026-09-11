#!/usr/bin/env python3
"""architect_agent.py - Permanent structure advisor for the agent system.

The ArchitectAgent is an architectural thinker that:
- Discovers and understands the project's technology stack
- Builds and maintains a persistent Knowledge Base (KB)
- Translates design documents into implementation guidance
- Proposes multiple options with trade-offs for decisions
- Produces Architecture Decision Records (ADRs)
- Guides the Coder with structured, actionable instructions

Does NOT write code, orchestrate agents, or read CLAUDE.md directly.
Receives project context from the Concierge.

v1 (Sprint S6): core + KB (11 tools)
v2 (future): consultation chain (call_consultant, tandem, peer, socratic, web_search)

Design document: dev/design/04_DSN_ArchitectAgent_InProgress.md
Plan: dev/plans/archive/02_DEV_AgentSystem_Completed.md (Sprint S6)
"""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from base_agent import (
    AgentStateBase, BaseAgent, BackendAdapter, ToolExecutor,
    ReportBuilder, ToolCall, ToolResult,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KB_SCHEMA_VERSION = 1
KB_HISTORY_CAP = 50

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "build", "dist", ".claude", ".tox", ".mypy_cache",
}

BLOCKED_COMMANDS = {"rm", "mv", "cp", "chmod", "chown", "del", "move", "ren"}

STACK_MARKERS = {
    "package.json": "Node.js/JavaScript",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java (Gradle)",
    "Makefile": "C/C++ (Make)",
    "CMakeLists.txt": "C/C++ (CMake)",
}

IMPORT_PATTERNS = {
    "py": [r"^\s*import\s+(\S+)", r"^\s*from\s+(\S+)\s+import"],
    "js": [r"""import\s+.*from\s+['"](\S+?)['"]""",
           r"""require\(\s*['"](\S+?)['"]\s*\)"""],
    "ts": [r"""import\s+.*from\s+['"](\S+?)['"]"""],
    "rs": [r"^\s*use\s+(\S+)", r"^\s*mod\s+(\S+)"],
    "go": [r"""import\s+\"(\S+?)\""""],
    "c": [r"""#include\s+\"(\S+?)\""""],
    "h": [r"""#include\s+\"(\S+?)\""""],
    "cpp": [r"""#include\s+\"(\S+?)\""""],
}


# ---------------------------------------------------------------------------
# ArchitectState
# ---------------------------------------------------------------------------

@dataclass
class ArchitectState(AgentStateBase):
    """Deterministic state for the Architect Agent."""
    agent_type: str = "architect"

    # Context (received from Concierge, NOT read directly)
    architecture_rules: str = ""
    module_boundaries: str = ""
    domain_invariants: str = ""
    project_description: str = ""
    project_root: str = ""

    # Knowledge Base reference
    kb_path: str = ""
    kb_dirty: bool = False

    # Accumulated knowledge (in-memory, synced to KB on disk)
    stack: str = ""
    module_map: dict = field(default_factory=dict)
    patterns: list = field(default_factory=list)
    antipatterns: list = field(default_factory=list)
    standing_rules: list = field(default_factory=list)

    # Decisions and outputs
    adrs: list = field(default_factory=list)
    guidance_history: list = field(default_factory=list)
    user_decisions: list = field(default_factory=list)

    def to_context_summary(self) -> str:
        """Rich summary for rebuilding LLM awareness after compression."""
        lines = [
            f"=== ARCHITECT STATE SUMMARY (step {self.current_step}) ===",
            f"Agent: {self.agent_type} | Mode: {self.mode} | Phase: {self.phase}",
            f"Steps: {self.total_steps} | LLM calls: {self.total_llm_calls}",
        ]
        if self.stack:
            lines.append(f"\nStack: {self.stack}")
        if self.module_map:
            lines.append("\nModule map:")
            for path, desc in list(self.module_map.items())[:15]:
                lines.append(f"  {path}: {desc}")
        if self.standing_rules:
            lines.append("\nStanding rules:")
            for rule in self.standing_rules[:10]:
                lines.append(f"  - {rule}")
        if self.adrs:
            lines.append(f"\nADRs produced ({len(self.adrs)}):")
            for adr in self.adrs[-5:]:
                title = adr.get("title", "?")
                status = adr.get("status", "?")
                lines.append(f"  [{status}] {title}")
                rationale = adr.get("rationale", "")
                if rationale:
                    lines.append(f"    Rationale: {rationale[:200]}")
        if self.guidance_history:
            lines.append(f"\nRecent guidance ({len(self.guidance_history)} total):")
            for g in self.guidance_history[-3:]:
                task = g.get("task", "?")
                lines.append(f"  - {task[:150]}")
        if self.patterns:
            lines.append(f"\nPatterns ({len(self.patterns)}):")
            for p in self.patterns[:5]:
                lines.append(
                    f"  - {p.get('pattern', '?')} @ {p.get('where', '?')}")
        if self.user_decisions:
            lines.append(f"\nUser decisions ({len(self.user_decisions)}):")
            for d in self.user_decisions[-5:]:
                lines.append(f"  - {d[:200]}" if isinstance(d, str)
                             else f"  - {json.dumps(d)[:200]}")
        if self.findings:
            lines.append(f"\nFindings: {len(self.findings)}")
        if self.errors:
            lines.append(
                f"\nRecent errors: {'; '.join(self.errors[-3:])}")
        lines.append("=== END ARCHITECT STATE SUMMARY ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Knowledge Base helpers
# ---------------------------------------------------------------------------

def _empty_kb() -> dict:
    """Return a fresh empty KB structure."""
    return {
        "schema_version": KB_SCHEMA_VERSION,
        "last_updated": "",
        "stack": "",
        "module_map": {},
        "patterns": [],
        "antipatterns": [],
        "standing_rules": [],
        "decisions": [],
        "guidance_history": [],
        "guidance_history_archived": [],
        "consultations": [],
        "consultations_archived": [],
    }


def _load_kb(path: str) -> dict | None:
    """Load KB from disk. Returns None if not found or invalid."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        version = data.get("schema_version", 0)
        if version > KB_SCHEMA_VERSION:
            return None  # newer schema, start fresh
        if version < KB_SCHEMA_VERSION:
            data = _migrate_kb(data, version)
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _migrate_kb(data: dict, from_version: int) -> dict:
    """Migrate KB from older schema versions."""
    # v0 -> v1: add schema_version and archived lists
    if from_version < 1:
        data["schema_version"] = 1
        data.setdefault("guidance_history_archived", [])
        data.setdefault("consultations_archived", [])
    return data


def _save_kb(data: dict, path: str) -> bool:
    """Atomic write KB to disk. Returns True on success."""
    data["last_updated"] = datetime.now().isoformat(timespec="seconds")
    tmp_path = path + ".tmp"
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        Path(tmp_path).write_text(
            json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
        return True
    except OSError:
        # Clean up tmp if replace failed
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _cap_list(lst: list, cap: int = KB_HISTORY_CAP) -> list:
    """Return items that overflow the cap (to be archived)."""
    if len(lst) <= cap:
        return []
    overflow = lst[:-cap]
    del lst[:-cap]
    return overflow


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

ARCHITECT_SYSTEM_PROMPT = """\
{behavioral_rules}

## Identity

You are the Architect Agent - a permanent structure advisor for this project.

You are NOT a code analyzer. You are an architectural thinker who:
- Discovers the project's technology stack from the codebase
- Translates design vision into practical implementation guidance
- Maintains a Knowledge Base of patterns, decisions, and rules
- Produces ADRs for significant architectural decisions
- Guides the Coder with precise, actionable, structured instructions

You do NOT write code. You do NOT orchestrate agents. You advise.

## Decision protocol

For every significant architectural decision:
1. Present multiple options with pro/contra
2. State your recommendation with reasoning
3. Wait for the user/Concierge to choose (interactive mode) or choose \
the best option with full rationale logged (autonomous mode)
4. Record the decision via write_knowledge and log_to_devlog

## Tool call format

Write a JSON block in a markdown fence:

```json
{{"tool": "tool_name", "params": {{"key": "value"}}}}
```

## Available tools

{tool_descriptions}
"""


def _build_architect_prompt(state: ArchitectState,
                             tool_descriptions: str) -> str:
    """Assemble the full system prompt from layers."""
    parts = [ARCHITECT_SYSTEM_PROMPT.format(
        behavioral_rules=BaseAgent._base_behavioral_rules(),
        tool_descriptions=tool_descriptions)]

    # Layer 3: Project context (from Concierge)
    if (state.project_description or state.architecture_rules
            or state.module_boundaries):
        parts.append("\n## Project Context (from Concierge)\n")
        if state.project_description:
            parts.append(
                f"### Project\n{state.project_description}\n")
        if state.architecture_rules:
            parts.append(
                f"### Architecture Rules\n{state.architecture_rules}\n")
        if state.module_boundaries:
            parts.append(
                f"### Module Boundaries\n{state.module_boundaries}\n")
        if state.domain_invariants:
            parts.append(
                f"### Domain Invariants\n{state.domain_invariants}\n")

    # Layer 4: Knowledge Base
    if state.stack or state.module_map or state.standing_rules:
        parts.append("\n## Knowledge Base\n")
        if state.stack:
            parts.append(f"Stack: {state.stack}\n")
        if state.module_map:
            parts.append("Module map:")
            for p, d in list(state.module_map.items())[:20]:
                parts.append(f"  {p}: {d}")
            parts.append("")
        if state.standing_rules:
            parts.append("Standing rules:")
            for r in state.standing_rules[:15]:
                parts.append(f"  - {r}")
            parts.append("")
        if state.adrs:
            parts.append(f"ADRs ({len(state.adrs)}):")
            for adr in state.adrs[-5:]:
                parts.append(
                    f"  - [{adr.get('status', '?')}] {adr.get('title', '?')}")
            parts.append("")
        if state.patterns:
            parts.append(f"Patterns ({len(state.patterns)}):")
            for p in state.patterns[:10]:
                parts.append(f"  - {p.get('pattern', '?')}")
            parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# ArchitectAgent
# ---------------------------------------------------------------------------

class ArchitectAgent(BaseAgent):
    """Permanent structure advisor. Inherits from BaseAgent.

    v1 tools: analyze_structure, read_file, run_command, check_coupling,
    read_knowledge, write_knowledge, review_design, produce_adr,
    guide_coder, log_to_devlog, done.

    v2 tools (consultation chain): call_consultant, run_tandem,
    call_peer_architect, web_search, call_socratic.
    All v2 tools have explicit fallback for unavailable backends.
    """

    def __init__(self, backend: str = "", model: str = "",
                 mode: str = "interactive"):
        self._done_flag = False
        self._project_root: Path | None = None
        self._kb_data: dict = _empty_kb()
        self._agent_caller = None      # concierge.py AgentCaller (lazy)
        self._prompt_generator = None   # concierge.py PromptGenerator (lazy)
        super().__init__(backend=backend, model=model, mode=mode)

    def _create_state(self) -> ArchitectState:
        return ArchitectState()

    def _build_system_prompt(self, **context) -> str:
        tool_desc = self.tools.get_tool_descriptions()
        return _build_architect_prompt(self.state, tool_desc)

    def _register_tools(self):
        # Analysis
        self.tools.register(
            "analyze_structure", self._tool_analyze_structure,
            "Scan project tree, discover stack and module map "
            "(path, max_depth)")
        self.tools.register(
            "read_file", self._tool_read_file,
            "Read a project file (path, max_lines)")
        self.tools.register(
            "run_command", self._tool_run_command,
            "Run read-only shell command (command, timeout)")
        self.tools.register(
            "check_coupling", self._tool_check_coupling,
            "Analyze import coupling between two modules "
            "(module_a, module_b)")
        # Knowledge
        self.tools.register(
            "read_knowledge", self._tool_read_knowledge,
            "Read KB or a section (section: all|stack|patterns|"
            "rules|decisions)")
        self.tools.register(
            "write_knowledge", self._tool_write_knowledge,
            "Write observation to KB (section, key, value)")
        # Output
        self.tools.register(
            "review_design", self._tool_review_design,
            "Evaluate a design proposal (design_text, context)")
        self.tools.register(
            "produce_adr", self._tool_produce_adr,
            "Generate ADR (title, context, decision, consequences)")
        self.tools.register(
            "guide_coder", self._tool_guide_coder,
            "Produce structured coder instructions "
            "(task, constraints, files)")
        self.tools.register(
            "log_to_devlog", self._tool_log_to_devlog,
            "Write decision/observation to devlog "
            "(content, entry_type)")
        self.tools.register(
            "done", self._tool_done,
            "Signal consultation complete (summary)")

        # v2: Consultation chain
        self.tools.register(
            "call_consultant", self._tool_call_consultant,
            "Call domain expert (domain, question, context)")
        self.tools.register(
            "run_tandem", self._tool_run_tandem,
            "Multi-model debate (problem, role, domain)")
        self.tools.register(
            "call_peer_architect", self._tool_call_peer_architect,
            "Cross-validate with another AI (question, context)")
        self.tools.register(
            "web_search", self._tool_web_search,
            "Search web for technical info (query)")
        self.tools.register(
            "call_socratic", self._tool_call_socratic,
            "Escalation: challenge assumptions (topic, context)")

    def _on_start(self, **context) -> str:
        # Store project context in state
        self.state.architecture_rules = context.get(
            "architecture_rules", "")
        self.state.module_boundaries = context.get(
            "module_boundaries", "")
        self.state.domain_invariants = context.get(
            "domain_invariants", "")
        self.state.project_description = context.get(
            "project_description", "")

        project_root = context.get("project_root", ".")
        self._project_root = Path(project_root).resolve()
        self.state.project_root = str(self._project_root)

        # Set KB path
        kb_path = self._project_root / ".claude" / "architect_kb.json"
        self.state.kb_path = str(kb_path)

        # Load existing KB
        existing = _load_kb(str(kb_path))
        if existing:
            self._kb_data = existing
            self._sync_kb_to_state()

        # Initialize concierge.py mechanics for v2 tools
        self._init_concierge_mechanics()

        # Build initial message
        question = context.get("question", "")
        ctx = context.get("context", "")

        parts = ["Architect session started."]
        if self.state.stack:
            parts.append(f"Stack: {self.state.stack}")
        if self.state.adrs:
            parts.append(f"KB loaded: {len(self.state.adrs)} ADRs, "
                         f"{len(self.state.patterns)} patterns.")
        else:
            parts.append("No existing KB found. Run analyze_structure "
                         "to discover the project.")
        if question:
            parts.append(f"\nQuestion: {question}")
        if ctx:
            parts.append(f"\nContext: {ctx}")
        if not question:
            parts.append("\nWhat architectural question can I help with?")

        return " ".join(parts)

    def _is_done(self, response: str) -> bool:
        return self._done_flag

    def _on_finish(self):
        self._sync_state_to_kb()
        self._flush_kb()

    # --- KB sync helpers ---

    def _sync_kb_to_state(self):
        """Load KB data into state fields."""
        self.state.stack = self._kb_data.get("stack", "")
        self.state.module_map = self._kb_data.get("module_map", {})
        self.state.patterns = self._kb_data.get("patterns", [])
        self.state.antipatterns = self._kb_data.get("antipatterns", [])
        self.state.standing_rules = self._kb_data.get(
            "standing_rules", [])
        self.state.adrs = self._kb_data.get("decisions", [])
        self.state.guidance_history = self._kb_data.get(
            "guidance_history", [])

    def _sync_state_to_kb(self):
        """Write state fields back to KB data."""
        self._kb_data["stack"] = self.state.stack
        self._kb_data["module_map"] = self.state.module_map
        self._kb_data["patterns"] = self.state.patterns
        self._kb_data["antipatterns"] = self.state.antipatterns
        self._kb_data["standing_rules"] = self.state.standing_rules
        self._kb_data["decisions"] = self.state.adrs
        self._kb_data["guidance_history"] = self.state.guidance_history
        # Cap and archive
        archived_g = _cap_list(self._kb_data["guidance_history"])
        if archived_g:
            self._kb_data.setdefault(
                "guidance_history_archived", []).extend(archived_g)

    def _flush_kb(self):
        """Write KB to disk. Sets kb_dirty on failure."""
        if not self.state.kb_path:
            return
        self._sync_state_to_kb()
        if _save_kb(self._kb_data, self.state.kb_path):
            self.state.kb_dirty = False
        else:
            self.state.kb_dirty = True
            self.state.log_error(
                "KB write failed, data in memory only")

    # --- v1 Tool implementations ---

    def _tool_analyze_structure(self, path: str = ".",
                                 max_depth: int = 5) -> str:
        """Scan project tree, discover stack."""
        if not self._project_root:
            return "Error: no project root set"
        max_depth = min(max_depth, 8)
        target = self._project_root / path

        # Build tree command
        skip_args = " ".join(
            f"-I '{d}'" for d in SKIP_DIRS)
        cmd = f"tree -L {max_depth} {skip_args} --noreport"
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=str(target))
            tree_output = result.stdout or result.stderr
        except (subprocess.TimeoutExpired, OSError) as e:
            tree_output = f"tree command failed: {e}"

        # Cap output
        lines = tree_output.split("\n")
        if len(lines) > 500:
            tree_output = "\n".join(lines[:500]) + (
                "\n... (truncated at 500 lines, use deeper path)")
        file_count = len([l for l in lines if "." in l and not l.strip().startswith(".")])

        # Detect stack from marker files
        markers_found = []
        stacks = []
        for marker, stack_name in STACK_MARKERS.items():
            # Check in target and one level down
            if (target / marker).exists():
                markers_found.append(marker)
                stacks.append(stack_name)
            else:
                for child in target.iterdir():
                    if child.is_dir() and child.name not in SKIP_DIRS:
                        if (child / marker).exists():
                            markers_found.append(
                                f"{child.name}/{marker}")
                            if stack_name not in stacks:
                                stacks.append(stack_name)
                            break

        stack_str = ", ".join(stacks) if stacks else "unknown"

        # Update state
        self.state.stack = stack_str

        return json.dumps({
            "tree": tree_output[:3000],
            "stack": stack_str,
            "marker_files": markers_found,
            "file_count": file_count,
        }, indent=2)

    def _tool_read_file(self, path: str,
                         max_lines: int = 200) -> str:
        """Read a project file."""
        if not self._project_root:
            return "Error: no project root set"
        target = self._project_root / path
        if not target.exists():
            return f"Error: file not found: {path}"
        try:
            content = target.read_text(encoding="utf-8",
                                        errors="replace")
            lines = content.split("\n")
            if len(lines) > max_lines:
                content = "\n".join(lines[:max_lines]) + (
                    f"\n... (truncated at {max_lines} lines, "
                    f"{len(lines)} total)")
            return content
        except OSError as e:
            return f"Error reading {path}: {e}"

    def _tool_run_command(self, command: str,
                           timeout: int = 30) -> str:
        """Run read-only shell command."""
        # Block destructive commands
        first_word = command.strip().split()[0] if command.strip() else ""
        if first_word.lower() in BLOCKED_COMMANDS:
            return (f"Error: command '{first_word}' is blocked. "
                    f"Only read-only commands allowed.")

        cwd = str(self._project_root) if self._project_root else "."
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=min(timeout, 60), cwd=cwd)
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            # Cap output
            lines = output.split("\n")
            if len(lines) > 500:
                output = "\n".join(lines[:500]) + "\n... (truncated)"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except OSError as e:
            return f"Error: {e}"

    def _tool_check_coupling(self, module_a: str,
                               module_b: str) -> str:
        """Grep-based import coupling analysis."""
        if not self._project_root:
            return "Error: no project root set"

        path_a = self._project_root / module_a
        path_b = self._project_root / module_b

        if not path_a.exists():
            return f"Error: module not found: {module_a}"
        if not path_b.exists():
            return f"Error: module not found: {module_b}"

        a_imports_b = self._find_imports(path_a, module_b)
        b_imports_a = self._find_imports(path_b, module_a)

        total = len(a_imports_b) + len(b_imports_a)
        circular = bool(a_imports_b and b_imports_a)

        if total == 0:
            score = "none"
        elif circular:
            score = "circular"
        elif total <= 2:
            score = "low"
        elif total <= 5:
            score = "moderate"
        else:
            score = "high"

        return json.dumps({
            "a_imports_b": a_imports_b,
            "b_imports_a": b_imports_a,
            "circular": circular,
            "coupling_score": score,
        }, indent=2)

    def _find_imports(self, source_dir: Path,
                      target_name: str) -> list[str]:
        """Find import references from source_dir to target_name."""
        results = []
        target_parts = target_name.replace("\\", "/").rstrip("/").split("/")
        target_base = target_parts[-1]

        for fpath in source_dir.rglob("*"):
            if not fpath.is_file():
                continue
            suffix = fpath.suffix.lstrip(".")
            if suffix not in IMPORT_PATTERNS:
                continue
            try:
                content = fpath.read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern in IMPORT_PATTERNS[suffix]:
                for match in re.finditer(pattern, content, re.MULTILINE):
                    imported = match.group(1)
                    if target_base in imported:
                        rel = str(fpath.relative_to(source_dir))
                        results.append(
                            f"{rel}: {match.group(0).strip()}")
                        break  # one match per pattern per file
        return results

    def _tool_read_knowledge(self, section: str = "all") -> str:
        """Read KB or a section."""
        if not self._kb_data.get("stack") and not self.state.adrs:
            return ("No Knowledge Base found. Use analyze_structure "
                    "to discover the project first.")

        if section == "all":
            # Return summary, not full dump
            parts = []
            if self._kb_data.get("stack"):
                parts.append(f"Stack: {self._kb_data['stack']}")
            if self._kb_data.get("module_map"):
                parts.append(f"Modules: {len(self._kb_data['module_map'])}")
            if self._kb_data.get("patterns"):
                parts.append(f"Patterns: {len(self._kb_data['patterns'])}")
            if self._kb_data.get("standing_rules"):
                parts.append(
                    f"Rules: {len(self._kb_data['standing_rules'])}")
            if self._kb_data.get("decisions"):
                parts.append(
                    f"ADRs: {len(self._kb_data['decisions'])}")
            return " | ".join(parts) if parts else "KB is empty."

        data = self._kb_data.get(section)
        if data is None:
            valid = [k for k in self._kb_data if not k.startswith("_")]
            return (f"Unknown section: {section}. "
                    f"Valid: {', '.join(valid)}")
        return json.dumps(data, indent=2) if not isinstance(
            data, str) else data

    def _tool_write_knowledge(self, section: str, key: str,
                                value: str) -> str:
        """Write observation to KB."""
        if section == "stack":
            self._kb_data["stack"] = value
            self.state.stack = value
        elif section == "module_map":
            self._kb_data.setdefault("module_map", {})[key] = value
            self.state.module_map[key] = value
        elif section == "patterns":
            entry = {"pattern": key, "where": value,
                     "rationale": "", "date": datetime.now().isoformat(
                         timespec="seconds")}
            self._kb_data.setdefault("patterns", []).append(entry)
            self.state.patterns.append(entry)
        elif section == "antipatterns":
            entry = {"issue": key, "severity": "medium",
                     "rationale": value}
            self._kb_data.setdefault("antipatterns", []).append(entry)
            self.state.antipatterns.append(entry)
        elif section == "standing_rules":
            self._kb_data.setdefault("standing_rules", []).append(value)
            self.state.standing_rules.append(value)
        elif section == "decisions":
            entry = {"id": key, "title": value, "status": "Proposed",
                     "date": datetime.now().isoformat(timespec="seconds"),
                     "rationale": ""}
            self._kb_data.setdefault("decisions", []).append(entry)
            self.state.adrs.append(entry)
        else:
            return (f"Unknown section: {section}. Valid: stack, "
                    f"module_map, patterns, antipatterns, "
                    f"standing_rules, decisions")

        # Flush periodically
        if self.state.current_step % 10 == 0:
            self._flush_kb()

        return f"KB updated: {section}.{key}"

    def _tool_review_design(self, design_text: str,
                              context: str = "") -> str:
        """Evaluate a design. Returns structured output.

        Note: the actual review is done by the LLM in conversation.
        This tool records the review request and returns a template
        for the LLM to fill in its response.
        """
        self.state.log_finding(
            f"Design review requested ({len(design_text)} chars)",
            severity="info")

        return json.dumps({
            "review_requested": True,
            "design_length": len(design_text),
            "context_length": len(context),
            "output_format": {
                "verdict": "approve | reject | revise",
                "blocking_issues": [
                    {"issue": "...", "location": "...",
                     "suggestion": "..."}],
                "non_blocking_issues": [
                    {"issue": "...", "severity": "low|medium",
                     "suggestion": "..."}],
                "options": [
                    {"option": "A", "description": "...",
                     "pro": ["..."], "contra": ["..."]}],
                "recommendation": "...",
                "assumptions": ["..."],
                "required_followups": ["..."],
            },
            "instruction": ("Analyze the design text above. Fill in "
                            "the output_format structure with your "
                            "review. Use the tool guide_coder if "
                            "implementation guidance is needed."),
        }, indent=2)

    def _tool_produce_adr(self, title: str, context: str,
                            decision: str,
                            consequences: str) -> str:
        """Generate ADR in Nygard format."""
        adr_id = f"ADR-{len(self.state.adrs) + 1:03d}"
        date = datetime.now().strftime("%Y-%m-%d")

        adr_text = (
            f"# {adr_id}: {title}\n\n"
            f"**Status**: Proposed\n"
            f"**Date**: {date}\n\n"
            f"## Context\n\n{context}\n\n"
            f"## Decision\n\n{decision}\n\n"
            f"## Consequences\n\n{consequences}\n"
        )

        entry = {
            "id": adr_id,
            "title": title,
            "status": "Proposed",
            "date": date,
            "rationale": decision[:500],
            "context": context[:500],
            "consequences": consequences[:500],
        }
        self.state.adrs.append(entry)
        self._kb_data.setdefault("decisions", []).append(entry)

        self.state.log_finding(
            f"ADR produced: {adr_id} - {title}", severity="info")

        return adr_text

    def _tool_guide_coder(self, task: str, constraints: str = "",
                            files: str = "") -> str:
        """Produce structured implementation instructions."""
        guidance = {
            "task": task,
            "approach": "",
            "files_to_touch": [f.strip() for f in files.split(",")
                               if f.strip()] if files else [],
            "patterns_to_follow": [],
            "patterns_to_avoid": [],
            "constraints": [c.strip() for c in constraints.split(",")
                            if c.strip()] if constraints else [],
            "acceptance_criteria": [],
            "estimated_complexity": "medium",
        }

        # Add standing rules as constraints
        for rule in self.state.standing_rules[:5]:
            if rule not in guidance["constraints"]:
                guidance["constraints"].append(rule)

        # Record in history
        record = {
            "task": task,
            "step": self.state.current_step,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "guidance": guidance,
        }
        self.state.guidance_history.append(record)

        self.state.log_finding(
            f"Coder guidance produced: {task[:100]}", severity="info")

        return json.dumps(guidance, indent=2)

    def _tool_log_to_devlog(self, content: str,
                              entry_type: str = "decision") -> str:
        """Write to project devlog."""
        if not self._project_root:
            return "Error: no project root set"

        devlog_dir = self._project_root / "devlog"
        if not devlog_dir.exists():
            return ("Error: devlog/ directory not found. "
                    "Decision recorded in state only.")

        date_str = datetime.now().strftime("%Y-%m-%d")
        # Find next entry number for today
        existing = list(devlog_dir.glob(f"{date_str}_*.md"))
        num = len(existing) + 1
        slug = entry_type.lower().replace(" ", "-")[:30]
        entry_path = devlog_dir / f"{date_str}_{num:03d}_{slug}.md"

        entry_text = (
            f"# {entry_type.title()}: {date_str}\n\n"
            f"> Type: {entry_type}\n"
            f"> Agent: architect\n"
            f"> Step: {self.state.current_step}\n\n"
            f"{content}\n"
        )

        try:
            entry_path.write_text(entry_text, encoding="utf-8")
            return f"Logged to devlog: {entry_path.name}"
        except OSError as e:
            # Record in state as fallback
            self.state.user_decisions.append(content[:500])
            return (f"Devlog write failed ({e}), "
                    f"recorded in state instead.")

    def _tool_done(self, summary: str = "") -> str:
        """Signal consultation complete."""
        self._done_flag = True
        if summary:
            self.state.log_finding(
                f"Consultation complete: {summary}", severity="info")
        return "Done. Generating report."

    # --- v2 Tools: Consultation chain ---

    def _init_concierge_mechanics(self):
        """Lazy-load concierge.py for consultation tools."""
        if not self._project_root:
            return
        try:
            from concierge import (
                ProjectContext, PromptGenerator, AgentCaller)
            pc = ProjectContext(self._project_root)
            self._prompt_generator = PromptGenerator(pc)
            self._agent_caller = AgentCaller(self._project_root)
        except ImportError:
            pass  # v2 tools work in degraded mode

    def _record_consultation(self, ctype: str, topic: str,
                               result: str,
                               path: str = "legacy") -> dict:
        """Record a consultation in state and KB."""
        record = {
            "type": ctype,
            "path": path,
            "topic": topic[:200],
            "result": result[:500] if isinstance(result, str)
            else str(result)[:500],
            "step": self.state.current_step,
            "date": datetime.now().isoformat(timespec="seconds"),
        }
        self._kb_data.setdefault("consultations", []).append(record)
        self.state.log_finding(
            f"Consultation ({ctype}): {topic[:100]}", severity="info")
        return record

    def _tool_call_consultant(self, domain: str, question: str,
                                context: str = "") -> str:
        """Call a domain-specific consultant.

        Routes through the Consultation Protocol to ExpertAgent.
        Falls back to direct concierge.py mechanics if protocol fails.
        """
        # Try Consultation Protocol
        used_path = "protocol"
        try:
            result_obj = self.consult(
                situation="domain_question",
                question=question,
                context=context,
                domain=domain,
            )
            result = result_obj.answer
            if result.startswith("ERROR:") or result.startswith("[FALLBACK]"):
                raise RuntimeError(result)
        except (RuntimeError, OSError, ImportError):
            # Fallback: direct mechanics
            used_path = "legacy"
            if self._prompt_generator and self._agent_caller:
                prompt = self._prompt_generator.generate(
                    role="expert",
                    task=(f"Domain: {domain}\n\n{question}"
                          f"\n\nContext: {context}" if context
                          else f"Domain: {domain}\n\n{question}"),
                )
                try:
                    result = self._agent_caller.call_consult(
                        role="expert",
                        prompt=prompt,
                        backend=os.environ.get(
                            "ARCHITECT_CONSULTANT_BACKEND", "").strip(),
                    )
                except Exception as e:
                    result = (f"[FALLBACK] Consultant ({domain}) "
                              f"unavailable: {e}. Proceeding without "
                              f"external input.")
            else:
                result = (f"[FALLBACK] No consultation backend available. "
                          f"Would consult {domain} expert about: "
                          f"{question[:200]}")

        self._record_consultation(
            f"consultant-{domain}", question, result, path=used_path)
        return result if isinstance(result, str) else json.dumps(result)

    def _tool_run_tandem(self, problem: str, role: str = "architect",
                           domain: str = "") -> str:
        """Multi-model Tandem debate.

        Routes through the Consultation Protocol at 'high' criticality
        to enable multi-source + external consultation.
        Falls back to direct concierge.py mechanics.
        """
        _role_to_situation = {
            "architect": "critical_decision",
            "reviewer": "design_review",
            "debug": "bug_diagnosis",
            "socratic": "assumption_challenge",
        }

        used_path = "protocol"
        try:
            situation = _role_to_situation.get(role, "critical_decision")
            result_obj = self.consult(
                situation=situation,
                question=problem,
                criticality="high",
                domain=domain,
            )
            if result_obj.answer.startswith("ERROR:"):
                raise RuntimeError(result_obj.answer)
            result = result_obj.to_dict()
        except (RuntimeError, OSError, ImportError):
            used_path = "legacy"
            if self._agent_caller:
                try:
                    result = self._agent_caller.call_tandem(
                        problem=problem,
                        role=role,
                        domain=domain,
                    )
                except Exception as e:
                    result = {
                        "fallback": True,
                        "reason": f"Tandem failed: {e}",
                        "note": ("Single-model analysis only. "
                                 "Second model was unavailable."),
                    }
            else:
                result = {
                    "fallback": True,
                    "reason": "concierge.py not available",
                    "note": "Tandem requires concierge.py mechanics.",
                }

        self._record_consultation("tandem", problem, str(result),
                                  path=used_path)
        return json.dumps(result, indent=2) if isinstance(
            result, dict) else str(result)

    def _tool_call_peer_architect(self, question: str,
                                    context: str = "") -> str:
        """Cross-validate with another AI as peer Architect.

        Routes through the Consultation Protocol as 'design_review'
        with external path to get a different-model opinion.
        Falls back to direct backend call.
        """
        used_path = "protocol"
        try:
            result_obj = self.consult(
                situation="design_review",
                question=question,
                criticality="normal",
                context=context,
            )
            result = result_obj.answer
            if result.startswith("ERROR:") or result.startswith("[FALLBACK]"):
                raise RuntimeError(result)
        except (RuntimeError, OSError, ImportError):
            # Fallback: direct peer call
            used_path = "legacy"
            peer_backend = os.environ.get(
                "ARCHITECT_PEER_BACKEND", ""
            ).strip().lower()
            if not peer_backend:
                result = (
                    "[FALLBACK] Peer architect unavailable: no backend "
                    "configured explicitly. Proposal remains unvalidated."
                )
                self._record_consultation(
                    "peer-architect", question, result, path=used_path)
                return result

            peer_prompt = (
                f"You are a senior software architect reviewing a "
                f"colleague's proposal.\n\n"
                f"Question: {question}\n"
            )
            if context:
                peer_prompt += f"\nContext: {context}\n"
            peer_prompt += (
                "\nGive your honest assessment. Identify risks, "
                "alternative approaches, and whether you agree "
                "with the proposed direction."
            )

            if self._prompt_generator and self._agent_caller:
                try:
                    result = self._agent_caller.call_consult(
                        role="architect",
                        prompt=peer_prompt,
                        backend=peer_backend,
                    )
                except Exception as e:
                    result = (f"[FALLBACK] Peer architect ({peer_backend})"
                              f" unavailable: {e}. Decision marked as "
                              f"unvalidated.")
            else:
                result = (f"[FALLBACK] No peer backend available. "
                          f"Decision unvalidated. Question was: "
                          f"{question[:200]}")

        self._record_consultation(
            "peer-architect", question, result, path=used_path)
        return result if isinstance(result, str) else json.dumps(result)

    def _tool_web_search(self, query: str) -> str:
        """Search the web for technical information.

        Fallback: if no search capability, states training cutoff.
        """
        launcher = os.environ.get("ARCHITECT_WEB_LAUNCHER", "").strip().lower()
        backend = os.environ.get("ARCHITECT_WEB_BACKEND", "").strip().lower()
        if launcher != "claude" or backend != "claude":
            result = (
                "[FALLBACK] Web search unavailable: configure both "
                "ARCHITECT_WEB_BACKEND=claude and ARCHITECT_WEB_LAUNCHER=claude "
                "explicitly. Verify current facts outside this agent."
            )
            self._record_consultation("web-search", query, result, path="explicit-only")
            return result

        # Resolve the explicitly selected launcher.
        claude_exe = None
        try:
            import shutil as _sh
            claude_exe = _sh.which("claude") or _sh.which("claude.cmd")
        except Exception:
            pass

        if claude_exe:
            try:
                search_prompt = (
                    f"Search the web for: {query}\n\n"
                    f"Return only factual results. Include URLs "
                    f"if available. Be concise."
                )
                result = subprocess.run(
                    [claude_exe, "-p", "-",
                     "--allowedTools", "WebSearch,WebFetch",
                     "--output-format", "text"],
                    input=search_prompt,
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0 and result.stdout.strip():
                    output = result.stdout.strip()
                    self._record_consultation(
                        "web_search", query, output)
                    return output
            except (subprocess.TimeoutExpired, OSError):
                pass

        # Fallback
        fallback = (
            f"[FALLBACK] Web search unavailable (no Claude CLI "
            f"or timeout). Cannot verify: {query}. "
            f"Using training data only (cutoff may be outdated)."
        )
        self._record_consultation("web_search", query, fallback)
        return fallback

    def _tool_call_socratic(self, topic: str,
                              context: str = "") -> str:
        """Escalation: call Socratic agent to challenge assumptions.

        Routes through the Consultation Protocol as
        'assumption_challenge'. Falls back to devil's advocate mode.
        """
        used_path = "protocol"
        try:
            result_obj = self.consult(
                situation="assumption_challenge",
                question=topic,
                context=context,
            )
            result = result_obj.answer
            if result.startswith("ERROR:") or result.startswith("[FALLBACK]"):
                raise RuntimeError(result)
        except (RuntimeError, OSError, ImportError):
            # Fallback: direct socratic call or self-challenge
            used_path = "legacy"
            if self._prompt_generator and self._agent_caller:
                socratic_prompt = (
                    f"You are the Socratic Agent. Challenge assumptions.\n\n"
                    f"Topic: {topic}\n"
                )
                if context:
                    socratic_prompt += f"\nContext: {context}\n"
                socratic_prompt += (
                    "\nAsk 3-5 hard questions that expose:\n"
                    "- Hidden assumptions\n"
                    "- Unconsidered edge cases\n"
                    "- Potential failure modes\n"
                    "- Alternative framings of the problem\n"
                    "Do NOT solve the problem."
                )
                try:
                    result = self._agent_caller.call_consult(
                        role="socratic",
                        prompt=socratic_prompt,
                        backend=os.environ.get(
                            "ARCHITECT_SOCRATIC_BACKEND", "").strip(),
                    )
                except Exception as e:
                    result = self._socratic_fallback(
                        topic, context, str(e))
            else:
                result = self._socratic_fallback(
                    topic, context, "no backend")

        self._record_consultation("socratic", topic, result,
                                  path=used_path)
        return result if isinstance(result, str) else json.dumps(result)

    def _socratic_fallback(self, topic: str, context: str,
                            reason: str) -> str:
        """Devil's advocate mode: self-generate challenge questions."""
        return (
            f"[FALLBACK] Socratic agent unavailable ({reason}). "
            f"Entering devil's advocate mode.\n\n"
            f"Self-challenge questions for '{topic}':\n"
            f"1. What is the strongest argument AGAINST this approach?\n"
            f"2. What assumption am I making that could be wrong?\n"
            f"3. What happens if this fails - what is the blast radius?\n"
            f"4. Is there a simpler approach I'm overlooking because of "
            f"sunk cost?\n"
            f"5. Who would disagree with this decision, and why?\n"
        )


# ---------------------------------------------------------------------------
# Report extension
# ---------------------------------------------------------------------------

def build_architect_report(state: ArchitectState) -> dict:
    """Extended report with Architect-specific data."""
    base = ReportBuilder.build(state)
    base["stack"] = state.stack
    base["module_map"] = state.module_map
    base["adrs"] = state.adrs
    base["guidance_count"] = len(state.guidance_history)
    base["patterns_count"] = len(state.patterns)
    base["standing_rules_count"] = len(state.standing_rules)
    base["kb_dirty"] = state.kb_dirty
    base["user_decisions"] = state.user_decisions
    base["consultations_count"] = len(
        [f for f in state.findings
         if isinstance(f, dict) and "Consultation" in f.get("description", "")
         ] if state.findings else [])
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for standalone Architect sessions."""
    import argparse
    parser = argparse.ArgumentParser(
        description="ArchitectAgent - permanent structure advisor")
    parser.add_argument("--project-root", default=".",
                        help="Project root directory")
    parser.add_argument("--backend", required=True,
                        help="LLM backend")
    parser.add_argument("--model", default="",
                        help="Model name")
    parser.add_argument("--mode", default="interactive",
                        choices=["autonomous", "interactive"],
                        help="Operating mode")
    parser.add_argument("--question", default="",
                        help="Question to answer")
    parser.add_argument("--output", default="",
                        help="Output report path")
    args = parser.parse_args()

    agent = ArchitectAgent(
        backend=args.backend,
        model=args.model,
        mode=args.mode,
    )

    if args.mode == "autonomous" and args.question:
        report = agent.run(
            project_root=args.project_root,
            question=args.question,
        )
        if args.output:
            ReportBuilder.write(report, args.output)
            print(f"Report written to {args.output}")
        else:
            print(json.dumps(report, indent=2))
    else:
        # Interactive mode
        response = agent.start(
            project_root=args.project_root,
            question=args.question,
        )
        print(f"Architect: {response}")
        try:
            while True:
                user_input = input("\nYou: ").strip()
                if not user_input or user_input.lower() in (
                        "quit", "exit", "done"):
                    break
                response = agent.step(user_input)
                print(f"\nArchitect: {response}")
        except (KeyboardInterrupt, EOFError):
            pass
        report = agent.finish()
        if args.output:
            ReportBuilder.write(report, args.output)
        print(f"\nSession ended. Steps: {report['total_steps']}, "
              f"ADRs: {len(agent.state.adrs)}")


if __name__ == "__main__":
    main()
