# Hooks Reference

> Complete reference for ControlCoding hook scripts.
> All hooks use Python stdlib only (no pip dependencies).

## Hook Architecture

ControlCoding hooks are Python scripts invoked by native-hook hosts such as
Claude Code at two points. The canonical CC config is
`.controlcoding/settings.json`; Claude Code runs the generated local adapter
`.claude/settings.local.json`. On non-inline hosts, use the repo-side
pre-commit, review, and verification gates described in the cross-tool guide
instead of claiming pre-write hook parity:

- **PreToolUse**: runs before a tool call (Edit, Write, Bash). Can block (exit 2) or allow (exit 0).
- **PostToolUse**: runs after a tool call. Cannot block, only warn and revert.

Exit codes: `0` = allow, `2` = block. Never `1`. Blocking hooks keep JSON on
`stdout` for tests/logs and mirror the human-readable reason on `stderr`, which
is the visible channel Claude Code surfaces to the agent on exit code `2`.

## Hook Files

| File | Type | Purpose |
|---|---|---|
| `check_boundaries.py` | PreToolUse | Blocks writes to protected zones (self-protection, global deny, module perimeter) |
| `check_dangerous_commands.py` | PreToolUse | Blocks destructive shell commands and bash writes to protected zones |
| `check_bash_writes.py` | PostToolUse | Detects and reverts protected file changes made via Bash |
| `check_doc_compression.py` | PreToolUse | Blocks edits that reduce document size by >20% |
| `codewarden_plan_review.py` | PreToolUse | Reviews the AI plan against project context rules + impact analysis |
| `codewarden_review.py` | Stop | Reviews the session diff against project context rules + impact analysis + architectural fitness evidence |
| `codewarden_backend.py` | Library | Shared LLM backend + impact analysis functions |
| `violation_store.py` | Library | Cross-session violation memory (JSONL) |
| `hook_logger.py` | Library | Logging infrastructure (JSONL events) |
| `hook_utils.py` | Library | Shared utilities (zone normalization) |
| `request_lift.py` | CLI tool | Scoped lift request/approval system |
| `feature_lock.py` | Library | Module Feature Lock engine |

## Self-Protection

These files are self-protected and cannot be modified by AI via Edit/Write:

- `check_boundaries.py`
- `check_dangerous_commands.py`
- `check_bash_writes.py`
- `hook_logger.py`
- `request_lift.py`
- `.controlcoding/settings.json`
- `.claude/settings.json`
- `.claude/settings.local.json`
- `.feature-lock.json` (any directory)
- `active_module.json`

To modify self-protected files, use the lift system (see below).

## Module Feature Lock

Module Feature Lock provides mechanical write isolation per module. An agent working
on module A cannot write files belonging to module B.

### Schema: `.feature-lock.json`

Lives in the module root directory. Tracked in git. Self-protected.

```json
{
  "version": 1,
  "module": "my-module",
  "owns": ["modules/my-module/**"],
  "shared_write": ["shared/utils.py"],
  "may_read": ["core/knowledge/"]
}
```

| Field | Required | Type | Description |
|---|---|---|---|
| `version` | yes | integer | Must be `1` |
| `module` | yes | string | Unique module identifier |
| `owns` | yes | list | Glob patterns for owned paths (write allowed) |
| `shared_write` | no | list | Exact file paths (no globs) writable outside own tree |
| `may_read` | no | list | Declarative only, not enforced by hooks |

Rules:
- `shared_write` accepts only exact file paths. Globs are rejected.
- `may_read` is informational - hooks never enforce it.
- Module names must be unique across the repo.
- Module names must be alphanumeric with hyphens, underscores, or dots.
- `owns` patterns use fnmatch (Python stdlib) for glob matching. On case-sensitive
  filesystems (Linux/Mac), patterns are case-sensitive. Use consistent casing.

### Runtime State: `.controlcoding/active_module.json`

Written by the Concierge or human before dispatching an agent. Gitignored.

```json
{
  "module": "my-module",
  "mode": "enforce",
  "session_id": "2026-03-24T14:20:00Z",
  "set_by": "concierge"
}
```

Modes:
- `enforce` - block writes outside perimeter (exit 2)
- `warn` - allow but log warning
- `audit` - log silently

### Environment Variable: `CC_ACTIVE_MODULE`

Optional override. Set before launching the agent:

```bash
CC_ACTIVE_MODULE=my-module claude ...
```

If both env var and file exist with different modules:
- `enforce` mode: blocks (ambiguous state is unsafe)
- `warn` mode: uses env var + warning
- `audit` mode: uses env var + log

### Enforcement Precedence

1. **Self-protection** - always wins, never overridable
2. **Global deny** (cc_config.json protected_zones) - always wins
3. **Module perimeter** - owns/shared_write check
4. **Zone protection** (warn-level zones)

Self-protection and global deny override module ownership. A module cannot write
to self-protected files or global deny zones even if they are inside its `owns`.

### CLI: `cc init-module <name>`

Creates a new module with a `.feature-lock.json` template:

```bash
python scripts/cc.py init-module my-module
python scripts/cc.py init-module my-module --dir src/packages/my-module
```

Default directory is `modules/<name>/`. Use `--dir` to specify a custom path.

This creates:
- The module directory (if it does not exist)
- `.feature-lock.json` with `owns` pointing to the directory

The command checks for duplicate module names and refuses to overwrite existing lock files.

## Lift System

Two lift mechanisms exist:

### Global Hook Lift (`hooks_lifted.json`)

Disables an entire hook temporarily (1-hour auto-expiry):

```json
{
  "lifted": ["check_boundaries.py"],
  "lifted_at": "2026-03-24T14:20:00Z"
}
```

### Scoped Lift (`request_lift.py`)

Per-zone, single-use lift with human approval:

1. AI requests: `python hooks/request_lift.py --file <path> --reason "<why>"`
2. Human approves: `python hooks/request_lift.py --approve`
3. Single-use: consumed after one successful operation

The request state lives in `.controlcoding/lift_request.json` when the canonical
control plane is present, with legacy fallback to `.claude/lift_request.json`
for older repos.

Module perimeter blocks are also liftable via the scoped lift system.

## CodeWarden (LLM-based Review)

CodeWarden is an LLM-based guardian that reviews changes against the canonical project context (`CONTROLCODING.md`, or a derived host file when applicable) at
two temporal levels:

- **Plan time** (`codewarden_plan_review.py`): PreToolUse on ExitPlanMode. Reviews the plan before code is written. Mode: WARN (default) or DENY.
- **Session end** (`codewarden_review.py`): Stop hook. Reviews the full session diff. Produces a report in `.controlcoding/codewarden_report.md`.

CodeWarden has no default backend. `CODEWARDEN_BACKEND` must name a supported,
configured adapter and `CODEWARDEN_CONSENT=approved` must be present for the
review call. Backend selection is not consent. Missing, blank, fallback-only,
or unknown values produce a structured skip with no adapter call. A configured
but unavailable backend fails without silently switching providers.

### Impact Analysis (Section 9.18)

Both CodeWarden hooks include **Impact Analysis**: before calling the LLM, the hook
extracts "concept fingerprints" from the plan or diff (function names, imports,
subprocess commands, constants) and greps the codebase for other files containing
the same patterns. The results are injected into the review prompt.

This catches partial edits: if the AI changes `llm_backend.py` but `blocks.py` also
calls the same function directly, Impact Analysis flags it: "blocks.py contains
matching patterns and is not in the plan."

### Gateway Modules (optional)

Register single-point-of-contact patterns in `.controlcoding/cc_config.json`:

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

When configured, these patterns are always included in the impact scan regardless
of what the current plan mentions.

### Architectural Fitness Evidence

`codewarden_review.py` and `codewarden_plan_review.py` can also consume
repo-side signals from `fitness_check.py` when that tool is present in the
project. These signals are used as **evidence**, not as automatic verdicts.

Examples of evidence fed into CodeWarden:
- layer-rule violations
- gateway-rule violations
- God-file / God-object heuristics
- ownership / mutation rule violations
- fan-in / fan-out hotspots and import cycles

Project-shared fitness rules belong in `.controlcoding/cc_config.json` under
`architecture_fitness`. A local `fitness.json` can still override those settings
for experiments or one-off tuning.

Fitness reports now include `Configuration Evidence`: loaded config sources and
active rule-pack counts for zones, thresholds, layer rules, gateway rules,
ownership rules, and mutation rules. This makes CodeWarden evidence auditable:
reviewers can see whether a finding came from built-in defaults, shared project
rules, or a local override.

When CodeWarden turns one of these into a finding, the structured review can
keep `category="architecture"` and add an `architecture_tag` such as:
- `boundary_violation`
- `layer_violation`
- `cohesion_smell`
- `god_file_risk`
- `coupling_regression`
- `contract_erosion`
- `misplaced_logic`
- `responsibility_accretion`

This deepens semantic architecture review, especially for non-inline hosts,
without implying false pre-write enforcement parity.

## Logging

All hook events are logged to `cc_hook_log.jsonl` via `hook_logger.py`:

```json
{"ts": "2026-03-24T14:20:00Z", "decision": "DENY", "hook": "module_perimeter", "target": "other/file.py", "reason": "..."}
```

Decision values: `DENY`, `WARN`, `ALLOW`, `LIFT`, `REVERT`, `ERROR`.
