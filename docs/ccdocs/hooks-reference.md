# ControlCoding v2 - Hooks Reference

> Configuration and behavior of every ControlCoding hook.
> For the conceptual framework, see [Methodology](methodology.md).

---

## Overview

ControlCoding hooks are Python scripts that run at specific points in an AI host's lifecycle when that host exposes a hook protocol. On native-hook hosts such as Claude Code and supported Cline setups, they can block an operation before it lands. On non-inline hosts, equivalent protection is repo-side: pre-commit, review, and verification gates catch violations without pretending to intercept every editor write.

The current public hook templates use only Python stdlib (no pip dependencies).
For `check_boundaries.py`, invalid/non-object input and a missing `file_path`
allow with exit `0`; a reached outer exception reports a diagnostic and also
continues with exit `0`. This is a fail-open continuation, not a safe-write
claim or a statement about every hook. Hook self-protection is hardcoded and
precedes lift handling on its configured native route.

### Current boundary-hook input guard

`check_boundaries.py` parses stdin at the start of `main()`. Two guards handle
the malformed inputs covered by its implementation:

1. **JSONDecodeError / EOFError**: if stdin is not valid JSON, the hook exits 0 (allow).
2. **isinstance(dict) check**: if the parsed JSON is valid but not a dictionary (e.g. a bare string, array, or number), the hook exits 0 (allow) immediately, before accessing any keys.

```python
try:
    input_data = json.loads(sys.stdin.read())
except (json.JSONDecodeError, EOFError):
    sys.exit(0)

if not isinstance(input_data, dict):
    sys.exit(0)
```

The rationale is robustness: an unexpected host payload should not deadlock a
session. This exercised behavior does not show that a write was safe, that a
host delivered the hook, or that other hooks have identical error handling.

### Hook events

| Event | When it fires | Use in ControlCoding |
|---|---|---|
| `PreToolUse` (Edit/Write) | Before every file write | Boundary enforcement: blocks writes outside perimeter |
| `PreToolUse` (Bash) | Before every shell command | Blocks dangerous commands: no force-push, no reset --hard |
| `PreToolUse` (ExitPlanMode) | When AI exits plan mode | CodeWarden plan review: evaluates plan against CLAUDE.md |
| `PostToolUse` (Bash) | After a configured Bash event | Optional conservative protected-path reporting; no automatic recovery |
| `Stop` | When the AI finishes | Final verification: runs invariant tests, integrity check |

### Exit codes

All hooks exit 0 (allow) or 2 (block), never 1.

- **Exit 0**: operation allowed (may include warnings printed to stdout)
- **Exit 2**: operation blocked. Must print JSON with `decision` and `reason` fields.

---

## 1. check_boundaries.py (Boundary Enforcement)

**Purpose**: Verifies whether the AI is writing to an allowed file. The mechanical implementation of the condominium architecture's zone protection.

**Trigger event**: `PreToolUse` on `Edit|Write`

**Configuration**: Protected zones are defined in `.claude/cc_config.json` (shared config). The hook reads this file and checks every file write against the zone list.

### Full L4 implementation (with feature.lock YAML support) - planned for v2.2

The code below shows the full L4 implementation with per-feature `feature.lock` files. This is a forward-looking design - not yet shipped as a template. The template at `templates/hooks/check_boundaries.py` provides the current production version using a configurable `PROTECTED_ZONES` list with zero dependencies - suitable for L2/L3 and sufficient for most projects. Both follow the same protocol (JSON on stdin, exit 0 = allow, exit 2 = block).

```python
#!/usr/bin/env python3
"""
hooks/check_boundaries.py
Mechanical boundary enforcement for features.
Exit 0 = allowed. Exit 2 = blocked (with reason).

This is the full L4 version with feature.lock support.
See templates/hooks/check_boundaries.py for the simpler L2/L3 template.
"""
import sys, json, os, fnmatch

def load_feature_lock(feature_dir):
    """Load the feature.lock for the current feature."""
    lock_path = os.path.join(feature_dir, "feature.lock")
    if not os.path.exists(lock_path):
        return None
    import yaml
    with open(lock_path) as f:
        return yaml.safe_load(f)

def is_allowed(file_path, lock):
    """Check if the file is in the allow list and not in the deny list."""
    if lock is None:
        return True  # No lock = everything allowed

    # Deny takes precedence
    for pattern in lock.get("deny", []):
        if fnmatch.fnmatch(file_path, pattern):
            return False

    # Check allow
    for pattern in lock.get("allow", []):
        if fnmatch.fnmatch(file_path, pattern):
            return True

    return False  # Default: not allowed if a lock exists

def normalize(path):
    """Normalize to forward slashes for cross-platform comparison."""
    return os.path.normpath(path).replace(os.sep, "/")

def has_segment(norm_path, segment):
    """Check if a directory segment appears in the path."""
    return segment in norm_path.split("/")

def main():
    input_data = json.loads(sys.stdin.read())
    tool_input = input_data.get("tool_input", {})

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    norm = normalize(file_path)

    # stable/ is FROZEN - writes blocked
    if has_segment(norm, "stable"):
        print(json.dumps({
            "decision": "deny",
            "reason": "CONTROL CODING: stable/ is FROZEN. Extraordinary review required."
        }))
        sys.exit(2)

    # shared/ has controlled evolution - warning (not blocking)
    # For total blocking change "warn" to "deny" and sys.exit(2)
    if has_segment(norm, "shared"):
        print(json.dumps({
            "decision": "warn",
            "reason": "CONTROL CODING: shared/ has controlled evolution. "
                      "Verify the change doesn't break existing consumers."
        }))
        sys.exit(0)

    # Check feature.lock for files inside features/
    parts = norm.split("/")
    if "features" in parts:
        idx = parts.index("features")
        if idx + 1 < len(parts):
            feature_dir = os.path.join("features", parts[idx + 1])
            lock = load_feature_lock(feature_dir)
            if not is_allowed(norm, lock):
                print(json.dumps({
                    "decision": "deny",
                    "reason": f"CONTROL CODING: file outside perimeter for {parts[idx+1]}. "
                              f"See {feature_dir}/feature.lock"
                }))
                sys.exit(2)

    sys.exit(0)

if __name__ == "__main__":
    main()
```

### Hook self-protection

The template `check_boundaries.py` has a hardcoded constant `HOOK_SELF_PROTECTION = True` that protects its own filename. Self-protection runs before lift and new-file checks on the configured native route. Scoped or legacy local lift state does not override that order; this does not establish coverage for another host route.

### Multi-segment zone matching

For patterns like `src/core/`, the hook checks consecutive path segments, not just substring matching. This prevents false positives (e.g. a file at `test_src/core_utils/` would not match `src/core/`).

### Current scoped and legacy lifts

The legacy `hooks_lifted.json` mechanism disables the current hook subject to its
local expiry check. The current `request_lift.py` workflow stores local scoped
request state for targeted zones. They have different scope and consumption
semantics and must not be described as interchangeable.

Create `.controlcoding/lift_request.json` (legacy `.claude/lift_request.json` still works in older repos):
```json
{
  "zones": ["src/core/", "shared/utils/"],
  "reason": "Refactoring core models and their shared helpers",
  "expires_at": "2025-09-15T15:30:00+00:00"
}
```

Current behavior:
- an active scoped request requires `status: "APPROVED"`, matching zone state,
  and unexpired local data; pending, unrelated, malformed, and expired data do
  not activate the scoped path;
- `request_lift.py --approve` rejects non-interactive use and requests a local
  token. That is an intended human workflow, not independent authentication or
  authorization against equivalent local access;
- the boundary reader updates/consumes local scoped state before the host
  operation is known to succeed. It does not prove exactly-once use or a
  completed edit, especially across failed persistence or concurrent access;
- self-protected files remain before lift handling on the covered native path.

### Edge cases and known limitations

- The hook only applies on a configured, delivered Edit/Write route. File writes via the Bash tool and other uncovered routes are outside that boundary. A local pre-commit check separately evaluates staged paths at its invocation; it does not establish server enforcement or accepted remote history.
- Regex-based path matching is inherently incomplete. A file can be constructed via variables or aliases in ways no pattern list can cover.
- When the shell working directory changes, relative paths in hook commands break. Always use absolute paths (see Resilience section below).

---

## 2. check_dangerous_commands.py (Command Blocking)

**Purpose**: Blocks dangerous shell commands that could cause irreversible damage.

**Trigger event**: `PreToolUse` on `Bash`

```python
#!/usr/bin/env python3
"""
hooks/check_dangerous_commands.py
Blocks dangerous shell commands.
"""
import sys, json, re

BLOCKED_PATTERNS = [
    r"git\s+push\s+(--force|-f)\b",
    r"git\s+push\s+.*--force",           # catches: git push origin main --force
    r"git\s+reset\s+--hard",
    r"git\s+checkout\s+\.",
    r"git\s+restore\s+\.",                # modern equivalent of checkout .
    r"git\s+clean\s+.*-f",               # catches: git clean -fd, git clean -f
    r"rm\s+(-\w*r\w*\s+)*-\w*f",         # catches: rm -rf, rm -r -f, rm -fr
    r"rm\s+(-\w*f\w*\s+)*-\w*r",         # catches: rm -f -r (reversed order)
    r"git\s+branch\s+(-D|--delete\s+--force)",
]

def main():
    input_data = json.loads(sys.stdin.read())
    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command):
            result = {
                "decision": "deny",
                "reason": f"CONTROL CODING: command blocked ({pattern}). "
                          "Ask the human for explicit confirmation."
            }
            print(json.dumps(result))
            sys.exit(2)

    sys.exit(0)

if __name__ == "__main__":
    main()
```

### Known limitations

Regex-based filtering is inherently incomplete. A command can be constructed via variables, aliases, pipes or external scripts in ways no pattern list can cover. The purpose of this hook is not absolute security but raising the barrier against accidental errors by the AI agent, which in practice almost always uses standard command forms. For protection against deliberately evasive actions, system-level permissions are needed (user without push rights, server-side protected branches).

---

## 3. check_bash_writes.py (Optional Bash Working-Tree Inspector)

**Purpose**: Reports conservative path observations in DENY zones or denied by
the active module perimeter after a Bash event. The observations may predate the
command or belong to another actor. The hook never attributes them to the
command and never restores or deletes user files.

**Trigger event**: `PostToolUse` on `Bash`

**Installation**: Opt-in through `templates/hooks/settings.json.example`.
Minimal `cc init` does not register this hook.

**Mechanism**: After a configured Bash event, the hook:

1. Enumerates index entries with `git --no-optional-locks -c core.fsmonitor=
   ls-files --stage -v -z --`. The same-invocation override prevents a configured
   filesystem-monitor callback; this index-only operation does not run Git
   content conversion.
2. Uses bounded standard-library reads to compare only protected regular files
   with their staged Git object IDs as raw bytes. Deletions, type changes and
   unmerged protected paths are observations. Tracked symlinks, protected
   submodules and entries that cannot be inspected are incomplete-inspection
   diagnostics; their content is not read.
3. Lists non-ignored untracked paths with `ls-files --others --exclude-standard
   -z` under the same fsmonitor override. NUL-delimited Git records preserve
   spaces and non-ASCII names.
4. Loads DENY zones from `.controlcoding/cc_config.json`, with the existing
   legacy-path fallback, and checks the active module perimeter.
5. Emits one JSON warning/context object listing protected observations and any
   incomplete-inspection diagnostics. Optional logs use `WARN` for observations
   and `ERROR` for inspection failures. The Git index is left unchanged.

**Exit code**: Always 0. The command has already run. Invalid/non-object JSON
input and non-Bash events are ignored. This hook performs no automatic recovery.

**Output**: `systemMessage` carries user feedback; `hookSpecificOutput` contains
`hookEventName: "PostToolUse"` and `additionalContext` for agent feedback. See the
[Claude Code output contract](https://code.claude.com/docs/en/hooks#posttooluse-decision-control).
Subprocess tests cover the output shape and data preservation. Actual delivery
in a named host version requires a separate integration check.

**Relationship to other hooks**: `check_dangerous_commands.py` checks selected
patterns before a Bash call; `check_bash_writes.py` observes the working tree
afterward. Neither combination establishes universal shell-write prevention.

**Limits and resolution**: Requires Git and a Git working tree. Configured
`clean` and `process` filters are not executed. Raw comparison deliberately does
not reproduce Git normalization or smudge conversion, so it can report a path
that Git considers clean. Staged-only content whose raw bytes match the index,
absent skip-worktree entries, ignored untracked entries and file-mode-only
changes are outside the scan. A regular-file comparison reads at most the size
observed on its validated open handle. Every read request is capped by the
remaining 8 MiB per-file and shared 32 MiB event budgets; returned bytes remain
charged if the entry later changes or fails. One root boundary is acquired before
tracked index enumeration and retained through every tracked entry. Windows
holds directory handles without delete sharing for the root and its ancestors,
preventing their rename/replacement during that phase. It requires accessible
plain directory components and refuses root-chain reparse points or acquisition
failures. Content-handle final paths are checked against the held root handle.
On supported POSIX systems, index Git enters an inherited `/proc/self/fd` or
`/dev/fd` alias of the held root descriptor. Directory-relative no-follow
traversal starts from that descriptor and uses a nonblocking leaf open. A root
pathname replacement retains the original directory/index; missing usable
descriptor-alias support is incomplete. Untracked enumeration and policy/config
reads remain separate operations; the boundary is not an atomic index/content
snapshot. Missing confinement primitives and content paths outside that held
boundary produce an incomplete result before reading. Missing Git, timeout, nonzero or malformed
path output, unreadable or concurrently changed entries, tracked symlinks,
protected submodules and exceeded limits also generate an incomplete-inspection
warning; results already obtained from another phase remain visible. These byte
and open controls are not a universal execution-time guarantee. Review reported
paths and diffs with the user before deciding how to resolve them; repeated
warnings are possible. Activity that starts after a path was inspected may only
be visible on a later event.

---

## 4. check_workflow.py (Workflow Enforcement)

**Purpose**: Mechanically enforces workflow prerequisites during development.

**Trigger event**: `PreToolUse` on `Edit|Write`

### Rules

- **plan_before_code**: warns on first Write to `src/` if no planner consultation was logged
- **visual_check_after_rendering**: warns after 3+ writes to `src/renderer/` or `src/shaders/` without visual verification
- **checkpoint_frequency**: warns after 20+ source writes without a checkpoint

### Detection mechanism

Detection is artifact-based: the hook checks for files produced by CC tools (`.claude/consult_log.jsonl`, `screenshots/`, `devlog/`) rather than maintaining separate state. If CC tools are not installed, the hook warns once and allows - it does not block projects without full tooling.

### Screenshot recency threshold

The `visual_check_after_rendering` rule does not simply check whether any screenshot exists. It checks whether a **recent** screenshot exists, using a configurable constant:

```python
SCREENSHOT_RECENCY_MINUTES = 30
```

The hook compares each file's modification time in the `screenshots/` directory against `time.time() - SCREENSHOT_RECENCY_MINUTES * 60`. Only screenshots modified within this window satisfy the check. Old screenshots from previous sessions do not count. This prevents the scenario where a stale screenshot from hours ago gives false assurance that the current rendering output has been verified.

To change the threshold, edit the `SCREENSHOT_RECENCY_MINUTES` constant at the top of `check_workflow.py`.

### Configuration

Rules are configured in the hook file (same pattern as PROTECTED_ZONES in check_boundaries.py). Each rule can be set to "warn" (allow + notify) or "deny" (block).

---

## 5. session_end_check.py (SessionEnd Integrity Verification)

**Purpose**: Verifies that DENY-protected files were not modified during the session. This catches bypasses that the boundary hook cannot prevent (e.g., file writes via Bash).

**Trigger event**: `Stop`

### How it works

1. On first run, computes SHA256 hashes of all DENY-protected files and stores them in `.claude/deny_hashes.json`
2. On subsequent runs, recomputes hashes and compares against stored values
3. Reports any files that changed, indicating a boundary bypass

This is the last line of defense. The boundary hook (PreToolUse) prevents most violations. The integrity check (Stop) catches anything that slipped through.

### File discovery filtering

When scanning the project for DENY-protected files, `find_matching_files()` skips directories and file types that produce false positives:

- **Skipped directories**: `__pycache__`, `.git`, `node_modules`. These contain auto-generated or framework-managed files that change on every run and are never meaningful DENY targets.
- **Skipped file extensions**: `.pyc`, `.pyo`. Python bytecode files are regenerated by the interpreter and would trigger false "modified" alerts on every session.

```python
skip_dirs = {"__pycache__", ".git", "node_modules"}
skip_ext = {".pyc", ".pyo"}
```

Without this filtering, a project with Python source files in a DENY zone would report hash mismatches for bytecode files every session, making the integrity check noisy and unreliable.

### Session-end rollback

When the integrity check detects modified DENY files, it can perform `git checkout --` on those files to restore them to their pre-session state. This ensures that even if a bypass occurred during the session, the final state of the repository is clean.

### Configuration

```json
{
  "hooks": {
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python hooks/session_end_check.py"
      }]
    }]
  }
}
```

---

## 6. codewarden_plan_review.py (Plan-Time Review)

**Purpose**: Reviews the AI's implementation plan against the project's CLAUDE.md architectural rules before any code is written. Shift-left enforcement.

**Trigger event**: `PreToolUse` on `ExitPlanMode`

### How it works

When the AI presents an implementation plan, this hook:

1. Sends the plan + CLAUDE.md to an LLM for evaluation
2. **Impact Analysis** (Section 9.18): extracts "concept fingerprints" from the plan (file paths, function names, subprocess commands), reads referenced files to find their exported identifiers, greps the codebase for all occurrences, and injects results into the review prompt

Architectural violations are caught before a single line of code exists. Impact Analysis flags files that may be affected by the planned change but are not mentioned in the plan. Cost of correction: rewrite the plan (nearly zero).

### Modes

- **WARN** (default): the plan proceeds to the user with CodeWarden's observations alongside. The user decides.
- **DENY**: the plan is blocked. The AI must revise and resubmit. The user only sees plans that respect the rules.

WARN is recommended for most projects. DENY is useful when the project has strict rules that should never reach the user as a proposal (e.g.: "never propose modifications to stable/ without a migration plan").

### Backend

Uses `codewarden_backend.py` shared module for LLM routing. Backends: Ollama (free, local), Anthropic API, OpenAI-compatible.

---

## 7. codewarden_review.py (Session-End Review)

**Purpose**: Reviews the complete session diff against CLAUDE.md rules at the end of a session.

**Trigger event**: `Stop`

### How it works

1. Collects git diff from the session
2. **Impact Analysis**: extracts concept fingerprints from the diff (function definitions, imports, subprocess commands, constants), greps the codebase for same patterns in files NOT in the diff, injects results into the review prompt
3. Loads CC rules from CLAUDE.md (architectural sections)
4. Sends diff + CLAUDE.md + impact results to LLM via configured backend (Ollama, Anthropic, OpenAI)
5. Generates report: violations found (including "impact" category for cascade risks), severity, violated rule
6. Saves report to `.claude/codewarden_report.md`

### What makes CodeWarden different from a linter

A linter applies generic rules (cyclomatic complexity, style, unused imports). CodeWarden applies the rules **specific to your project** defined in the CLAUDE.md: "state goes in AppState", "don't add logic to the God Object", "simulation modules don't write to rendering modules".

These rules are not expressible in a traditional linter. They require understanding of architectural context, which only an LLM can provide.

### Cross-session violation memory

By default, each CodeWarden review is stateless: it sees the current diff, the current CLAUDE.md, and nothing else. The violation store adds persistent memory across sessions.

How it works:

1. After each review (plan time or session end), if CodeWarden finds violations, it appends a record to `.claude/codewarden_violations.jsonl`.
2. Each record contains: timestamp, hook type, violated rule (quoted), affected file, severity, backend used.
3. Before the next review, CodeWarden reads the store and injects a summary of recent violations (last 30 days) into the review prompt.
4. The reviewing LLM sees patterns: "this rule was violated 3 times in the last 2 weeks" and can adjust its assessment accordingly.

The store is a JSONL file (one JSON object per line) in `.claude/`. It is local, never committed to git, and has zero dependencies beyond Python stdlib.

Design principle: fail-safe. If the store is missing, corrupt, or unwritable, CodeWarden works exactly as before. The store is an enhancement, not a dependency.

### Severity escalation

When enabled via `CODEWARDEN_ESCALATE=true`, the violation store can automatically escalate severity for repeated violations:

- A rule violated 3+ times in 30 days: escalated from WARN to DENY
- A rule violated 5+ times in 30 days: flagged as "chronic"

This is opt-in and off by default. It addresses a specific problem: rules that are technically WARN (the user decides) but are violated so frequently that the "decision" is always to ignore. Escalation makes the pattern visible and forces attention.

Thresholds are configurable in `violation_store.py` (constants `ESCALATE_WARN_TO_DENY` and `ESCALATE_CHRONIC`).

### Impact Analysis (Section 9.18)

Both CodeWarden hooks include **Impact Analysis**: a mechanical guard that prevents partial edits by scanning the codebase for files related to the planned change.

**How it works:**
1. **Fingerprint extraction**: regex-based extraction of function names, class names, imports, subprocess commands, config keys, and constants from the plan text or git diff
2. **File reading**: if the plan references specific files, those files are read and their exported identifiers (function/class/constant definitions, subprocess commands) are added to the fingerprint list
3. **Codebase scan**: `git grep` searches for all fingerprints across tracked files, excluding files already in the plan/diff
4. **Prompt injection**: results are formatted and injected into the CodeWarden review prompt as an "Impact Scan Results" section
5. **LLM evaluation**: the reviewing LLM flags files that match patterns but are not covered by the plan

**Example**: a plan says "modify `llm_backend.py` to route Haiku calls to Ollama." Impact Analysis reads `llm_backend.py`, extracts `call_model`, `claude`, `HAIKU_MODEL`. It greps the codebase and finds `scorer.py`, `blocks.py`, `arbiter.py` also contain these patterns. The review warns: "3 files contain matching patterns and are not in the plan."

**Fail-safe**: Impact Analysis is best-effort. If `git grep` fails, times out, or the repository is not a git repo, the review proceeds without impact data (same behavior as before the feature existed).

### Gateway Modules

Optional registration of single-point-of-contact patterns in
`.controlcoding/cc_config.json` with legacy `.claude/cc_config.json` fallback:

```json
{
  "gateway_modules": [
    {
      "pattern": "call_model",
      "file": "shared/llm_backend.py",
      "description": "All LLM calls must go through call_model()"
    }
  ]
}
```

When `gateway_modules` is configured, these patterns are **always** included in the impact scan, regardless of what the current plan or diff mentions. This provides persistent protection for cross-cutting concerns: if any file other than the registered gateway uses the pattern, Impact Analysis flags it.

See methodology Section 9.18 for the full design rationale and Section 3.5 for the Anti-Corruption Layer concept that Gateway Modules extend.

### Architectural Fitness Evidence

CodeWarden can consume repo-side signals from `fitness_check.py` as review
evidence. These signals are not automatic verdicts; the reviewer escalates them
only when the current plan or diff contributes to the drift, touches the
hotspot, or leaves a documented contract unresolved.

Project-shared fitness rules belong in `.controlcoding/cc_config.json` under
`architecture_fitness`. A local `fitness.json` can still override those settings
for experiments or one-off tuning.

Supported evidence includes layer-rule violations, gateway-rule violations,
God-file / God-object heuristics, ownership or mutation rule violations, fan-in
/ fan-out hotspots, and import cycles.

### Architecture overview

```
                        ControlCoding Enforcement
                        =========================

  CLAUDE.md (rules)          .claude/settings.json (hooks config)
       |                              |
       v                              v
  +------------------------------------------------------------+
  |                    Hook Engine (Claude Code)                |
  +------------------------------------------------------------+
       |                    |                      |
       v                    v                      v
  [Plan time]          [Edit time]           [Session end]
  PreToolUse:          PreToolUse:           Stop hook
  ExitPlanMode         Edit/Write
       |                    |                      |
       v                    v                      v
  codewarden_          check_                codewarden_
  plan_review.py       boundaries.py         review.py
       |                    |                      |
       |                    v                      |
       |              Mechanical check:            |
       |              zone boundaries,             |
       |              feature.lock                 |
       |              (no LLM needed)              |
       v                                           v
  +-------------------------------------------------+
  |            codewarden_backend.py                |
  |  Shared module: prompt building, LLM routing    |
  |  Backends: Ollama (free) | Anthropic | OpenAI   |
  +-------------------------------------------------+
       |                                           |
       v                                           v
  LLM reviews plan                        LLM reviews diff
  against CLAUDE.md                        against CLAUDE.md
       |                                           |
       v                                           v
  WARN: show observations        WARN: save report to
  DENY: block the plan           .claude/codewarden_report.md
       |                                           |
       +-------------------+-----------------------+
                           |
                           v
                  violation_store.py
                  Records violations to
                  .claude/codewarden_violations.jsonl
                           |
                           v
                  Next review reads history
                  and injects into prompt
                  (feedback loop)
```

The feedback loop between the store and the review prompt is what gives CodeWarden cross-session awareness without requiring a persistent agent or external database.

### Adoption triggers

CodeWarden follows ControlCoding's gradual model. It should not be adopted from day 1.

- Phase 1 (Stop hook with local model): when the project already has CLAUDE.md with architectural rules and at least one documented case of a violation not caught by hooks
- Phase 2 (Stop hook with API model): when the cost of local Ollama is a problem (machines without GPU) or rules require more sophisticated reasoning
- Phase 3 (Native subagent): when you already use the Task tool for other purposes and want a quick pre-commit review
- Phase 4 (Multi-agent orchestrator): when the project is mature enough to justify a dedicated orchestrator

**Key principle**: the guardian reads the same rules the coding agent should follow (CLAUDE.md), but evaluates them from the outside, without the contextual bias of the one who just wrote the code. The separation between who codes and who watches is the fundamental value.

---

## Hook Resilience

### The deadlock problem (empirical)

During a session, the shell working directory changed to a different repository. The hook commands in `settings.json` used relative paths (`python hooks/check_boundaries.py`), so Python could not find the scripts. The process exited with code 2, which Claude Code interpreted as DENY. Every tool - Edit, Write, Bash - was blocked. The session was completely unrecoverable without manual file editing outside Claude Code.

**Lesson**: a crashed hook is a configuration problem, not a security violation. Hooks must fail-open on errors.

### Fail-open wrapper

Every hook wraps its `main()` in a top-level try/except. `SystemExit` (raised by `sys.exit`) passes through normally. Any other `Exception` exits 0 with a visible `[HOOK ERROR]` warning and logs the crash as `ERROR` to `cc_hook_log.jsonl` for post-session review. This prevents deadlocks while keeping crashes visible.

### Absolute paths in settings.json

Hook commands in `settings.json` must use absolute paths (e.g., `python /project/hooks/check_boundaries.py`). The `cc.py init` command generates these automatically. This ensures hooks are findable regardless of the shell's current working directory.

---

## Hook Configuration

### settings.json example

```json
// .claude/settings.json
// See templates/hooks/settings.json.example for a ready-to-use version.
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python hooks/check_boundaries.py"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python hooks/check_dangerous_commands.py"
          }
        ]
      },
      {
        "matcher": "ExitPlanMode",
        "hooks": [
          {
            "type": "command",
            "command": "python hooks/codewarden_plan_review.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python hooks/check_bash_writes.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python hooks/codewarden_review.py"
          }
        ]
      },
      {
        "hooks": [
          {
            "type": "command",
            "command": "python hooks/session_end_check.py"
          }
        ]
      }
    ]
  }
}
```

### Hooks in other tools (Cursor, Aider)

The concept of mechanical enforcement translates differently for different tools:

**Cursor**: Uses `.cursorrules` with explicit instructions + git pre-commit hooks for invariant tests.

**Aider**: Uses `--edit-format` to control the format of modifications + `.aiderignore` to exclude protected files.

**Kiro**: Uses specs as a mandatory gate before implementation.

**Generic (any AI)**: Git pre-commit hooks that run invariant tests. If they fail, the commit is rejected.

---

## Further Reading

- [Methodology](methodology.md) - conceptual framework and architecture
- [Tools Reference](tools-reference.md) - CLI, MCP servers, scripts
- [Cookbook](cookbook.md) - worked examples by domain
