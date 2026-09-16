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
- **PostToolUse**: runs after a tool call has completed. The optional Bash
  inspector reports observations; it does not undo writes or prevent that call.

Exit codes: `0` = allow, `2` = block. Never `1`. Blocking hooks keep JSON on
`stdout` for tests/logs and mirror the human-readable reason on `stderr`, which
is the visible channel Claude Code surfaces to the agent on exit code `2`.

## Hook Files

| File | Type | Purpose |
|---|---|---|
| `check_boundaries.py` | PreToolUse | Blocks writes to protected zones (self-protection, global deny, module perimeter) |
| `check_dangerous_commands.py` | PreToolUse | Blocks destructive shell commands and bash writes to protected zones |
| `check_bash_writes.py` | PostToolUse | Reports conservative protected-path observations without restoring or deleting user files |
| `check_doc_compression.py` | PreToolUse | Blocks edits that reduce document size by >20% |
| `codewarden_plan_review.py` | PreToolUse | Reviews the AI plan against project context rules + impact analysis |
| `codewarden_review.py` | Stop | Reviews the session diff against project context rules + impact analysis + architectural fitness evidence |
| `codewarden_backend.py` | Library | Shared LLM backend + impact analysis functions |
| `violation_store.py` | Library | Cross-session violation memory (JSONL) |
| `hook_logger.py` | Library | Logging infrastructure (JSONL events) |
| `hook_utils.py` | Library | Shared utilities (zone normalization) |
| `request_lift.py` | CLI tool | Scoped lift request/approval system |
| `feature_lock.py` | Library | Module Feature Lock engine |

## Optional Bash Working-Tree Inspector

`templates/hooks/settings.json.example` registers `check_bash_writes.py` for
`PostToolUse` on `Bash`. Minimal `cc init` does not register this optional hook.
It compares protected regular files with their staged index objects as raw
bytes, and reports deletions, type changes and unmerged protected paths. Tracked
symlinks and submodules produce an incomplete-inspection diagnostic rather than
an unbounded or indirect content read. It also lists non-ignored untracked
entries. Paths in configured DENY zones or denied by the active module perimeter
are reported. Staged-only content whose raw worktree bytes match the index,
absent skip-worktree entries, ignored untracked entries, file-mode-only changes
and writes outside the configured event path are outside this inspection.

The observed changes may predate the command or belong to another actor. The
inspector never attributes them to the current command, restores files, deletes
files or updates the Git index. A raw mismatch can be reported even when Git
would consider a normalized or smudge-filtered worktree file clean. It may repeat
a warning while the observation remains. Review the paths and their diffs with
the user before deciding how to resolve them. An optional hook-log entry records
the observation as `WARN`.

Tracked paths come from the index-only command `git --no-optional-locks -c
core.fsmonitor= ls-files --stage -v -z --`; the same-invocation override prevents
a configured filesystem-monitor callback. Git content conversion is not used,
so configured `clean` and `process` filters are not executed. Non-ignored
untracked paths use `ls-files --others --exclude-standard -z` under the same
fsmonitor override. A regular-file comparison reads at most the size observed
on its validated open handle: every read request is capped by the remaining
8 MiB per-file and shared 32 MiB event budgets, and returned bytes stay charged
even if that entry later changes or fails. The tracked phase acquires one root
boundary before listing the index and retains it through the last entry. On
Windows, directory handles without delete sharing keep the root and its ancestor
components from being renamed or replaced during that phase. The root chain
must consist of accessible plain directories; reparse components or acquisition
failures produce an incomplete result. Each opened content handle's final path
is checked against the held root handle before reading. On supported POSIX
systems, index Git enters the held root through an inherited `/proc/self/fd` or
`/dev/fd` directory alias; no-follow traversal starts from that same root
descriptor, with a nonblocking leaf open. Root pathname replacement therefore
retains inspection of the original directory and index. Missing descriptor-alias
support produces an incomplete result. This boundary covers the tracked phase;
untracked enumeration and policy/configuration reads remain separate operations,
and the index and file contents are not a transactional snapshot. If required
primitives are unavailable or a content path escapes the held boundary, that
entry is not read and inspection is incomplete. These byte and open controls are not a
universal execution-time guarantee. Missing Git, timeout, nonzero or malformed
path output, a concurrent read change, an unreadable entry, a tracked symlink or
submodule, or a limit being reached produces an incomplete-inspection warning.
Results already obtained from the other phase are still reported. Invalid JSON
input, non-object input and non-Bash events exit 0 without inspection. The hook
exits 0 after reporting; the Bash action has already occurred. Activity that
starts after a path was inspected may only be visible on a later event.

Warnings use a single JSON object with `systemMessage` for user feedback and
`hookSpecificOutput` containing `hookEventName: "PostToolUse"` and
`additionalContext` for agent feedback, following the
[Claude Code hook output contract](https://code.claude.com/docs/en/hooks#posttooluse-decision-control).
Local subprocess tests validate the output envelope and file preservation;
they do not certify delivery in a particular installed host version.

## Self-Protection

These files are self-protected on the configured native Edit/Write route:

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

Self-protection is evaluated before both global and scoped lifts, so the lift
system does not override it. Change these files only through a separately
authorized maintenance path; this reference does not claim coverage for other
write routes.

## Module Feature Lock

On covered native write routes, Module Feature Lock rejects writes outside the
active module's allowed paths. The optional Bash inspector reports dirty paths
after the action; it does not provide pre-write isolation for arbitrary commands.

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

Per-zone local request/approval workflow:

1. AI requests: `python hooks/request_lift.py --file <path> --reason "<why>"`
2. Human approves: `python hooks/request_lift.py --approve`
3. The boundary reader may consume matching local state before the host tool
   operation is known to have completed.

The request state lives in `.controlcoding/lift_request.json` when the canonical
control plane is present, with legacy fallback to `.claude/lift_request.json`
for older repos.

Module perimeter blocks are also liftable via the scoped lift system.

`--approve` rejects a non-interactive terminal and asks for a stored token. This
is the intended human-approval step, not an independent identity or
authorization boundary against a process or user with equivalent local access.
Pending, unrelated, malformed, or expired scoped state is not active. The
template also retains a legacy whole-hook lift (`hooks_lifted.json`), whose
scope and expiry semantics differ from the scoped request; do not treat the
two as equivalent. The local state transition neither proves a successful edit
nor establishes exactly-once use, and failed persistence or concurrent access
are not characterized here.

## Boundary Input and Create/Edit Semantics

`check_boundaries.py` exits `0` without a decision for invalid JSON,
non-object input, or a missing `file_path`; a reached outer exception emits an
error diagnostic and also exits `0`. This fail-open continuation avoids a hook
deadlock, but does not certify that a write is safe or that every hook has the
same error behavior.

On the covered native route, self-protection is checked first, then legacy
whole-hook lift state. The current template allows a missing target after it
checks mandatory DENY zones: an existing target in a configured custom `DENY`
zone is blocked, while a missing custom target follows the preserved allow
path. New files in mandatory DENY zones and self-protected paths remain
blocked. This create/edit distinction is template behavior, not a universal
policy for repository-stage checks.

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
