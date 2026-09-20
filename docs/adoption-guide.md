# Getting Started with ControlCoding

> You have a software project. You use an AI coding assistant (Claude Code,
> Cursor, Copilot, Cline, Aider, or similar). You want to prevent the AI
> from breaking things. This guide tells you exactly what to do.
>
> Time: 30 minutes. Prerequisites: Python 3.11+ for Core, a git repository. Memory also requires a working SQLite deserialize API.

---

## What you need to do

Three things:

1. **Add a rules file** to your project that the AI reads every session
2. **Copy 3 Python scripts** that block the AI from touching protected files
3. **Write 3 tests** for properties of your system that must never break

That's it. The rest of ControlCoding is optional and you add it only when you need it.
In public packaging terms, this guide focuses on the `Core` baseline; `Agents`
and `Studio` are additive layers on top. In the current release story, any
`Agents` work should stay explicit on the chosen official host. API-backed
routed specialists belong to Version II, and `Studio` / CC UI remains later
optional work.

---

## Step 0 (optional): Discover your architecture

If you already know your project's architecture (modules, boundaries, invariants),
skip to Step 1. If you're starting a new project or inheriting a codebase and
aren't sure what to put in the rules file, use the Phase 0 discovery template:

1. Open a new AI session
2. Paste the contents of [`templates/phase0_discovery.md`](../templates/phase0_discovery.md)
3. Choose your path: "I have an idea" or "I have a codebase/document"
4. The AI will ask structured questions and produce a `CONTROLCODING.md` draft
5. Review and approve the draft, then continue with Step 1

---

## Step 0.5: Start each AI chat with memory

After ControlCoding is installed, start each substantial AI chat in the project
folder with a normal instruction:

```text
Start this project and load its memory before working.
```

The project host file tells the AI to run the startup tools itself when tools
are available. The AI should load active memory, prior work, current focus,
blockers, next steps, and Project Plane context before answering or editing,
instead of relying on chat history.

---

## Step 1: Add the rules file (10 minutes)

Every AI coding tool reads a rules file from your project. The file name
depends on your tool:

| Your tool | File to create |
|---|---|
| Claude Code | `CLAUDE.md` in project root |
| Cursor | `.cursor/rules/project.mdc` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| Cline | `.clinerules` in project root |
| Aider | `CONVENTIONS.md` in project root |
| Codex CLI, OpenCode, Amp | `AGENTS.md` in project root |
| Gemini CLI | `GEMINI.md` in project root |

The content is the same regardless of file name. Create the file and paste
this, replacing everything in `[brackets]` with your project's actual values:

```markdown
# [Your Project Name]

## What this project does
[2-3 sentences. Stack, purpose, key libraries.]

## Architecture rules
1. [Most important rule - e.g., "All DB access through repository classes"]
2. [Second rule - e.g., "No business logic in route handlers"]
3. [Third rule - add more if you have them]

## Protected zones (do NOT modify without approval)
- `[stable zone path]` - DENY - [e.g., "Core models, stable since v3.0"]
- `[stable zone path]` - DENY - [e.g., "Auth module, audited"]
- `[shared zone path]` - WARN - [e.g., "Shared utilities, check consumers"]

## Domain invariants (must always be true)
1. [Property that must never break - e.g., "sum of line items == invoice total"]
2. [Another property - e.g., "every user has exactly one role"]
3. [Another property - e.g., "no API endpoint without authentication"]

## Operative rules
- Before creating a new function, search the codebase for existing ones
- Before modifying a protected zone, stop and ask for explicit approval
- When a hook emits WARN on a protected zone, STOP and explain what you
  intend to modify and why. Wait for approval before proceeding.
- When a hook emits DENY, do NOT retry. Explain the constraint and propose
  an alternative approach.
- Run tests before committing

## Current focus
- [ ] [What you're working on now]
```

**Aim for 30-80 lines.** You can start with 20 and add more as you work.
The template at [`templates/CLAUDE.md.template`](../templates/CLAUDE.md.template)
has all sections with more detail.

### Filled-in example (Django invoicing API)

```markdown
# InvoiceAPI

## What this project does
REST API for invoice management. Django 5.0 + DRF + PostgreSQL.

## Architecture rules
1. All database access goes through repository classes in src/repos/
2. No business logic in views or serializers - only in src/services/
3. All monetary values are Decimal, never float

## Protected zones (do NOT modify without approval)
- `src/core/models/` - DENY - Database models, stable since v3.0
- `src/auth/` - DENY - JWT authentication, audited
- `src/repos/` - WARN - Shared repository layer, check consumers
- `migrations/` - WARN - Never edit existing migrations

## Domain invariants (must always be true)
1. sum(line_items) == invoice.total for every invoice
2. Every model change creates an AuditLog entry
3. tax_amount == round(subtotal * tax_rate, 2)

## Operative rules
- Before creating a new function, search the codebase for existing ones
- Before modifying a protected zone, stop and ask for explicit approval
- When a hook emits WARN, STOP and explain. Wait for approval.
- When a hook emits DENY, do NOT retry. Propose an alternative.
- Run `pytest tests/invariants/ -x` before committing
- Invoice state machine: draft -> sent -> paid -> archived. Never skip states.

## Current focus
- [ ] Add recurring invoice support
```

---

## Step 2: Copy the hook scripts (10 minutes)

Hooks are small scripts that run automatically every time the AI tries to
edit a file or run a shell command. If the AI tries to modify a protected
file, the script blocks it.

**This works natively on Claude Code and on Cline where native hooks are
available.** For other tools, skip to "Alternative for other tools" below.

### What to copy

From this repository's `templates/hooks/` folder, copy **3 files** into
your project:

```
your-project/
  hooks/                          <-- create this folder
    check_boundaries.py           <-- copy from templates/hooks/
    check_dangerous_commands.py   <-- copy from templates/hooks/
    hook_logger.py                <-- copy from templates/hooks/
  CLAUDE.md                       <-- created in Step 1
  src/
  ...
```

### What to edit: the condominium model

Before editing the hook configuration, you need to know **what to protect
and how**. ControlCoding classifies all project code into 4 zones:

```
stable/      Foundation code. Doesn't change. Everything depends on it.
shared/      Reusable utilities. Changes are deliberate and announced.
features/    Active feature code. AI works freely within boundaries.
workspace/   Experiments and prototypes. No restrictions.
```

Each zone has a protection level:

| Zone | Hook action | What happens |
|---|---|---|
| stable | `deny` | AI is blocked. Cannot modify these files at all. |
| shared | `warn` | AI is alerted. Must explain the change and wait for approval. |
| features | (none) | AI works freely. Per-feature `feature.lock` boundaries planned for v2.2. |
| workspace | (none) | Full freedom. Sandbox for experiments. |

**You don't need to rename your folders.** Use this model to classify
your existing code:

| Your code | Zone | Why |
|---|---|---|
| Core models, base classes, DB schema | stable | Everything depends on them. Breakage cascades everywhere. |
| Auth, security, audited modules | stable | Reviewed and certified. Changes need human review. |
| Shared utilities, helper libraries | shared | Used across features. Changes can break multiple consumers. |
| Migrations, generated configs | shared | Should rarely change. When they do, it must be intentional. |
| Active feature code | features | Where the AI does its daily work. |
| Prototypes, scratch files | workspace | Disposable. AI can experiment freely. |

**Individual files with constraints**: some files live in features/ but have
known issues (God Objects, large init files, config files with rules). You
can add these as `warn` entries in `.controlcoding/cc_config.json` to force the AI
to pause before modifying them. This combines the hook (mechanical pause) with
your canonical `CONTROLCODING.md` rules (what to do about it). Neither is sufficient alone.

**Specification documents**: if your project has requirement documents, design
specs, or rubrics that the AI should read but never rewrite, add them as
`deny` entries. This prevents **semantic drift by compression** - the AI
tendency to "simplify" rich requirements into telegraphic shorthand that loses
contextual meaning across successive rewrites. See Section 9.14 of the
methodology for structural defenses (atomic requirements with IDs, acceptance
criteria with examples, anti-examples) that make specs compression-resistant.

Open `.controlcoding/cc_config.json` (created by `cc init`) and configure your
protected zones. Map your stable code to `"deny"` and your shared code to
`"warn"`:

```json
{
  "protected_zones": [
    {"path": "src/core/models/", "description": "Core data models - stable", "level": "deny"},
    {"path": "src/auth/", "description": "Authentication module - stable", "level": "deny"},
    {"path": "migrations/", "description": "Never edit existing migrations", "level": "warn"},
    {"path": "src/utils/", "description": "Shared utilities - check consumers first", "level": "warn"}
  ]
}
```

The rule: **deny** for code that must never change without human review.
**warn** for code that occasionally changes but the AI should think twice.

Code moves only in one direction: **workspace -> features -> shared -> stable**.
An experiment that works gets promoted to a feature. A utility used by 2+
features gets promoted to shared. Code that's been stable for months with a
fixed API gets promoted to stable. Never the reverse.

Open `hooks/check_dangerous_commands.py` - the default patterns block
`rm -rf`, `git push --force`, `git reset --hard`, and similar destructive
commands. You can add your own patterns or leave the defaults.

### How to activate

Create a file at `.controlcoding/settings.json` in your project root (create the
`.controlcoding/` directory if it doesn't exist):

```json
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
      }
    ]
  }
}
```

You can also copy this file from `templates/hooks/settings.json.example`.

**Test it**: start a new AI session and ask it to edit a file inside one of
your protected zones. It should be blocked with a "CONTROL CODING" message.

### Alternative for other tools

If your tool doesn't support native hooks, use a git pre-commit hook instead.
This includes Codex CLI, Gemini CLI, Cursor, Copilot, Aider, OpenCode, and
Cline on platforms where native hooks are unavailable. This catches violations
when you commit, not when the AI edits.

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
# Block commits that modify protected zones
PROTECTED_ZONES="src/core/models/ src/auth/ migrations/"

for zone in $PROTECTED_ZONES; do
    if git diff --cached --name-only | grep -q "^$zone"; then
        echo "BLOCKED: Commit modifies protected zone: $zone"
        echo "If intentional, use: git commit --no-verify"
        exit 1
    fi
done
```

Make it executable: `chmod +x .git/hooks/pre-commit`

---

## Step 3: Write 3 invariant tests (10 minutes)

An invariant is a property of your system that must **always** be true, no
matter what features you add. Regular tests check "does function X return
the right value." Invariant tests check "does the system still obey its
fundamental rules."

Ask yourself: **"What would be catastrophically wrong if it broke, even if
all other tests passed?"** Write a test for each answer.

### How to find your invariants

| Question about your system | If yes, write a test that checks... |
|---|---|
| Is there a value with a fixed range? (e.g., score 0-100) | The range holds in all code paths |
| Is there a set that must be complete? (e.g., all categories) | No missing or extra items |
| Is there a formula that must be consistent? (e.g., total = sum of parts) | Formula holds for all valid inputs |
| Is there a partition? (e.g., items split into groups) | Complete coverage, no overlaps |
| Is there an operation that goes only one direction? (e.g., only decreases) | Monotonicity is never violated |

### Examples by language

**Python (pytest)**:

```python
# tests/invariants/test_domain.py

def test_line_items_sum_to_total(db, sample_invoices):
    """Every invoice's line items must sum to its total."""
    for inv in sample_invoices:
        line_sum = sum(item.amount for item in inv.line_items.all())
        assert line_sum == inv.total

def test_no_user_without_role(db):
    """Every user must have exactly one role."""
    from myapp.models import User
    for user in User.objects.all():
        assert user.roles.count() == 1
```

**JavaScript (Jest)**:

```javascript
// tests/invariants/domain.test.js

test('cart total equals sum of items', () => {
  const cart = createSampleCart();
  const expected = cart.items.reduce(
    (sum, item) => sum + item.price * item.quantity, 0
  );
  expect(cart.total).toBe(expected);
});
```

**C++ (Catch2)**:

```cpp
// tests/invariants/test_conservation.cpp

TEST_CASE("Energy is conserved across simulation steps") {
    auto sim = createTestSimulation(SEED);
    double e0 = sim.totalEnergy();
    sim.step(100);
    REQUIRE(std::abs(sim.totalEnergy() - e0) / e0 < 0.001);
}
```

Put invariant tests in a dedicated directory (e.g., `tests/invariants/`)
so you can run them separately: `pytest tests/invariants/ -x`

---

## Step 4: Manage CC files in version control (2 minutes)

ControlCoding creates files in your project. Some belong in your repo, others
do not. Understanding the difference prevents accidental commits of runtime
artifacts and keeps your repo clean.

This section describes the **default policy for adopter projects**.
The same principle also applies to ControlCoding itself: the framework keeps
its internal maintainer docs local and publishes only the public framework
surface.

### What to commit

| File | Why |
|---|---|
| `CONTROLCODING.md` | Canonical project rules. Every collaborator or host file derives from it. |
| Host-native context file (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.clinerules`, ...) | The file your chosen AI host actually reads. |
| `.controlcoding/settings.json` | Hook configuration. Must be shared so hooks run for everyone. |
| `.controlcoding/cc_config.json` | Protected zones, hooks_location, documentation_mode. Shared project config. |
| `.controlcoding/gateway_config.json` | If you use advanced surface features or later UI/API paths, this stores the selected User Host plus surface/backend state. |

These are your project's CC configuration. Commit them.

`documentation_mode` controls whether ControlCoding actively governs the
project's documentation taxonomy:

- `managed`: ControlCoding may scaffold and enforce its canonical documentation structure
- `project_managed`: the project keeps its own documentation system, and the file-organization hook should not block non-canonical doc paths

`cc_artifact_mode` is separate from `documentation_mode`:

- `local_only`: default recommendation for adopter projects. CC working docs stay local and gitignored.
- `shared_repo`: explicit opt-in if your team wants governed CC working docs tracked in git.

`devlog/` is not part of that switch:

- keep `devlog/` local
- update it during the Commit Ceremony
- do not treat it as shared repository content by default

### What NOT to commit

| File / pattern | Why |
|---|---|
| `hooks/check_boundaries.py`, `hooks/codewarden_*.py`, etc. | Copied from templates. Each developer gets them via `cc init`. |
| `.controlcoding/deny_hashes.json` | Runtime state (approved boundary overrides). Per-developer. |
| `.controlcoding/codewarden_violations.jsonl` | Session-specific violation log. |
| `.controlcoding/codewarden_report.md` | Generated report. Recreated each session. |
| `.controlcoding/cc_hook_log.jsonl` | Hook execution log. Debug artifact. |
| `.controlcoding/session_counter.json` | Session tracking. Per-developer. |
| `.controlcoding/hooks_lifted.json` | Temporary lift state. Expires automatically. |
| `.controlcoding/lift_request.json` | Lift request. Transient. |
| `.controlcoding/consult_log.jsonl` | Consultant call log. Per-developer. |
| `.controlcoding/cc_surface_lock.json` | Local surface authority lock (Primary/Observer). Runtime-only. |
| `tools/fitness_check.py` | Generated fitness script. |
| `screenshots/` | Visual check captures. |
| `.bridge/` | Inter-session communication. Ephemeral. |

### Automatic .gitignore management

When you run `cc init`, ControlCoding automatically adds a managed block to
your `.gitignore` covering all the patterns above. The block is marked with
start/end comments so it can be detected and updated:

```
# --- ControlCoding managed (do not edit) ---
hooks/check_boundaries.py
hooks/codewarden_*.py
...
# --- /ControlCoding managed ---
```

The block is idempotent - running `cc init` again will not duplicate it. If you
need to regenerate it, delete the block (both marker lines and everything
between them) and run `cc init` again.

`cc doctor` checks two things related to this:
- **Check 9**: the .gitignore block is present
- **Check 10**: no CC runtime artifacts are tracked by git (leaked files)

### Default adopter policy: local-only CC working docs

For adopter projects, the recommended default is:

- `cc_artifact_mode = local_only`
- keep CC working docs out of git
- update them locally at each significant milestone anyway

In this mode, the managed `.gitignore` block keeps these local by default:

- `STATUS.md`
- `ROADMAP.md`
- `BUGS.md`
- `dev/` when the project is also `documentation_mode = managed`

The managed block also keeps `devlog/` local regardless of artifact mode.

### Local surface authority lock

If you use advanced surface features, later/internal UI work, or build
CC-aware wrappers around official hosts,
use the local surface lock instead of guessing which surface is primary.

Typical commands:

```bash
python scripts/cc.py surface claim host:claude-vscode --type official_host --label "Claude Code (VS Code)"
python scripts/cc.py surface observe ui:local-observer --type cc_ui_local --label "CC Local Observer"
python scripts/cc.py surface request-takeover host:codex-cli --reason "Switching primary host"
python scripts/cc.py surface resolve-takeover takeover_123 --decision approved --resolved-by host:claude-vscode
python scripts/cc.py surface run host:claude-vscode --type official_host --label "Claude Code (VS Code)" -- claude
python scripts/cc.py surface heartbeat host:claude-vscode
python scripts/cc.py surface release host:claude-vscode
python scripts/cc.py surface status
```

Guidance:

- `claim` makes that surface the explicit `Primary Surface`
- `observe` registers a surface as observer-only
- `run` is the preferred launcher/wrapper path because it manages claim, heartbeat, and release around the child process automatically
- `heartbeat` refreshes the local lock for long-running surfaces
- `release` detaches a surface when it closes
- do not assume ControlCoding can infer the user's intent from open windows alone
- for the current public release path, keep the chosen official host primary
  and leave any local CC UI surface observer-only

When you run `cc setup`, ControlCoding also generates local launcher helpers in
`.controlcoding/launchers/` for the selected `User Host`. CLI hosts get a
`launch_primary_host.*` wrapper; editor-style hosts get claim/release helpers
for the current project session. It also writes a VS Code-compatible task
template named for the selected host (for example
`.controlcoding/launchers/vscode.tasks.json`, `.controlcoding/launchers/cursor.tasks.json`,
or `.controlcoding/launchers/claude-code.tasks.json`). For editor-style hosts such as
VS Code, Cursor, Windsurf, and Cline, setup also installs `.vscode/tasks.json`
automatically when that workspace file is still missing. A local
`.controlcoding/launchers/manifest.json` records the generated entrypoints, the chosen
task preset, whether workspace editor tasks were installed or left alone, plus
the recommended next step for that host. Setup can also emit host workflow
guidance in three modes:

- `preset_only`: just the generated wrappers, task template, and manifest
- `recommended`: preset assets plus CC's built-in host workflow notes
- `custom`: recommended guidance plus project-local user notes

Those notes stay local to the project and must not be used to weaken ControlCoding's compliance or Primary/Observer rules.

This means the Commit Ceremony still happens, but the working docs remain local
session memory rather than repository content.

### Optional: what to commit at your discretion

| File | When to commit |
|---|---|
| `STATUS.md`, `ROADMAP.md`, `BUGS.md`, `dev/` | Only if your team explicitly chooses `cc_artifact_mode = shared_repo` |
| `devlog/` | Keep local. It is session memory, not shared repo content. |
| `docs/adr/` | Architecture Decision Records - usually worth committing |

---

## Step 5: Central hooks (optional)

By default, `cc init` copies hook scripts into your project's `hooks/` folder.
This is simple and works well for single projects. If you work on multiple CC
projects, you can install hooks once in a central location instead.

### When to use central hooks

- You have 2+ projects using ControlCoding on the same machine
- You want to keep your project repo free of hook scripts entirely
- You want hook updates to apply to all projects at once

### How to enable

**Option A: CLI flag**

```bash
python scripts/cc.py init --project-root . --central-hooks
```

**Option B: Chat-guided setup flow**

```bash
python scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .
```

Step 6 ("Hook Location") lets you choose between local and central.

### What happens

- Hook scripts are installed to `~/.controlcoding/hooks/` instead of `hooks/`
- `.controlcoding/settings.json` references the central path
- `.controlcoding/cc_config.json` records `"hooks_location": "central"`
- The .gitignore block omits hook file patterns (since no local hooks exist)
- `cc doctor` validates hooks at the central location

### Migration: local to central

If you already have a local CC setup and want to switch to central hooks:

1. Run `cc init --project-root . --central-hooks`
2. Delete the old `hooks/` folder from your project (if it only contains CC hooks)
3. Delete the old .gitignore CC block and run `cc init` again to regenerate it
4. Run `cc doctor` to verify everything is correct

### Migration: central to local

1. Run `cc init --project-root .` (without `--central-hooks`)
2. The .gitignore block will be regenerated with hook patterns included
3. Run `cc doctor` to verify

---

## Done

Your project now has:

| What | Where | What it does |
|---|---|---|
| Rules file | `CONTROLCODING.md` plus the host-native file your tool reads | AI reads your rules at every session start |
| Zone classification | `CONTROLCODING.md` "Protected zones" section | Code classified as stable (deny) / shared (warn) / features / workspace |
| Boundary hooks | `hooks/*.py` + `.controlcoding/settings.json` | AI is mechanically blocked from modifying stable and shared code |
| Invariant tests | `tests/invariants/` | Your system's fundamental properties are verified |
| Clean .gitignore | `.gitignore` CC block | Runtime artifacts and hooks excluded from version control |

**This is enough for most projects.** Don't add more ControlCoding components
unless you hit a specific problem. Here's when to add what:

| Problem you hit | What to add |
|---|---|
| A bug that an invariant test would have caught | Write that invariant test |
| 3+ modules keep invading each other's code | Add protected zones in `cc_config.json` (per-feature `feature.lock` planned for v2.2) |
| AI keeps recreating functions that already exist | Add a lightweight function registry |
| Codebase grew past 50 files with interacting modules | Reorganize folders into explicit stable/shared/features/workspace directories |
| Team works in parallel on branches | Add invariant tests as a blocking CI check |
| AI bypasses rules documented in `CONTROLCODING.md` (e.g., hardcodes state in a God Object) | Add CodeWarden review hook (see below) |

### CodeWarden: guardian agent (optional, advanced)

If your project has architectural rules in `CONTROLCODING.md` that hooks cannot enforce
(because they require understanding context, not just checking file paths), you
can add the CodeWarden review hook. It runs at session end, sends the git diff
and your `CONTROLCODING.md` rules to a local LLM (via Ollama), and generates a violation
report.

Setup:
1. Copy `templates/hooks/codewarden_review.py` to `hooks/codewarden_review.py`
2. Install Ollama and pull a model: `ollama pull qwen2.5-coder:7b`
3. The hook is already configured in `settings.json.example` as a Stop hook

The report is saved to `.controlcoding/codewarden_report.md`. Read it before your next
session. For cloud backends (Anthropic, OpenAI), set the appropriate environment
variables - see the script header for details.

**Plan review** (optional): CodeWarden can also review AI plans before code is
written. Copy `templates/hooks/codewarden_plan_review.py` to `hooks/` and add
the ExitPlanMode matcher to your settings (see `settings.json.example`). This
catches architectural violations at the cheapest possible point. Works only on
tools with plan mode (Claude Code, Cline).

### Vision Agent: design-document verification (optional)

If your project has a design document (`CONTROLCODING.md`, specs/*.md) and you want to verify that the built project matches it, install the Vision Agent:

```bash
python scripts/cc.py install vision
```

This copies 6 files to `tools/` and configures the `mcp_vision` MCP server:
- `verification_agent.py` - criterion tracking, DAG resolution, budget enforcement
- `verification_report.py` - JSON + markdown report generation
- `mcp_vision.py` - MCP server wrapping verification as callable tools
- `visual_check.py` + `visual_check_utils.py` - screenshot capture (L2 debug)
- `visual_test.py` - interactive testing with keyboard/mouse simulation

For projects that only need screenshot capture (no full verification):
```bash
python scripts/cc.py install visual-check
```

See [tools-reference.md](ccdocs/tools-reference.md) sections 10d-10f for usage and [cookbook.md](ccdocs/cookbook.md) section 8 for worked examples.

---

## File summary

Files from this repository that you use in your project:

| File | Copy to | Edit what |
|---|---|---|
| `templates/hooks/check_boundaries.py` | `hooks/check_boundaries.py` | Nothing (zones configured in `.controlcoding/cc_config.json`) |
| `templates/hooks/check_dangerous_commands.py` | `hooks/check_dangerous_commands.py` | `DANGEROUS_PATTERNS` list (optional) |
| `templates/hooks/hook_logger.py` | `hooks/hook_logger.py` | Nothing (works as-is) |
| `templates/hooks/codewarden_review.py` | `hooks/codewarden_review.py` | Backend config via env vars (optional) |
| `templates/hooks/codewarden_plan_review.py` | `hooks/codewarden_plan_review.py` | Review mode via env vars (optional) |
| `templates/hooks/codewarden_postcommit` | `.git/hooks/post-commit` | Nothing (for non-Claude Code tools) |
| `templates/hooks/settings.json.example` | `.controlcoding/settings.json` | Nothing (works as-is) |
| `templates/CLAUDE.md.template` | `CONTROLCODING.md` and the derived host-native file | Everything in `[brackets]` |
| `templates/scripts/verification_agent.py` | `tools/verification_agent.py` | Nothing (via `cc install vision`) |
| `templates/scripts/mcp_vision.py` | `tools/mcp_vision.py` | Nothing (via `cc install vision`) |
| `templates/scripts/visual_check.py` | `tools/visual_check.py` | Nothing (via `cc install vision` or `visual-check`) |

---

## Creating Custom Packs

The built-in packs (debug-tools, session-manager, multi-agent, dashboard, vision) cover common use cases. You can create your own packs by following the same structure.

A pack is a set of files to copy + optional MCP server config. To add a custom pack:

1. Add your scripts to `templates/scripts/` (or a custom directory)
2. In `scripts/cc.py`, add entries to:
   - `PACK_FILES` - maps pack name to files to copy (key: destination dir, value: list of source paths)
   - `MCP_CONFIGS` - maps pack name to MCP server definitions (optional, only if the pack includes an MCP server)
   - `ALL_PACKS` - add the pack name to the list
3. Run `cc install <your-pack> --project-root <project>`

Example for a hypothetical "metrics" pack:

```python
# In PACK_FILES:
"metrics": {
    "tools/": [
        SCRIPTS_DIR / "my_metrics_server.py",
    ],
},

# In MCP_CONFIGS:
"metrics": {
    "metrics-server": {
        "command": "python",
        "args": ["tools/my_metrics_server.py"],
    }
},
```

Pack scripts should follow the same conventions as built-in scripts: Python stdlib only for hooks, fastmcp for MCP servers, graceful error handling.

---

## Further reading

- [README.md](../README.md) - Overview, maturity levels, evidence
- [cross-tool-guide.md](cross-tool-guide.md) - Setup for every major AI coding tool
- [docs/ccdocs/](ccdocs/) - Split methodology documentation (methodology, hooks, tools, cookbook)
- [evidence.md](evidence.md) - Validation data from 2 real projects
