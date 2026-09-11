# ControlCoding v3.0 - Tools Reference

> CLI tool, MCP servers, scripts, and project configuration.
> For the conceptual framework, see [Methodology](methodology.md).
> For hook details, see [Hooks Reference](hooks-reference.md).

---

## 0. Authorized Interfaces Only

ControlCoding is multi-vendor, but public-safe use is limited to authorized
interfaces:

- official local CLIs (for example Claude Code, Codex CLI)
- official APIs
- local runtimes such as Ollama
- vendor-approved connector flows inside the vendor's own product

ControlCoding does **not** support:

- embedding Claude.ai or ChatGPT consumer login in a CC-owned interface
- reusing consumer OAuth/session tokens across third-party products
- describing a vendor's consumer subscription as a generic transport that any
  third-party app may legally reuse

Non-interactive CLI transports such as `claude -p` are acceptable only when
used through the vendor's own official CLI and supported authentication flow.
If a local observer or future CC-owned UI visualizes an official-CLI path, any
Concierge thread shown there should be treated as a local routed transcript
with explicit origin labels, not as a replacement vendor chat surface.
Primary orchestration and coding should remain in the official host on those
paths. CC UI stays limited to observability, approvals, and bounded control
surfaces, and is outside the current public release path.

---

## 1. CLAUDE.md: the Project Constitution

### 1.1 Architecture Discovery (Phase 0)

The CLAUDE.md template (section 1.3) provides the structure. The question is: how do you fill it in? If you already know your architecture, you write it directly. But in many cases - new projects, inherited codebases, solo developers using AI for the first time - the architecture is implicit or does not exist yet.

Phase 0 is the structured process of discovering your architecture before writing a single line of code. Its output is a first draft of CLAUDE.md that the user reviews, corrects, and approves. Only after approval does Level 1 (documented) begin.

**Two paths, same output:**

**Path A - From an idea (new project):**

The user describes what they want to build. The AI asks structured questions to identify the architectural skeleton:

1. **Purpose and stack**: what does the software do, what language/framework/libraries
2. **Main entities**: what data does the system store or manipulate (users, orders, simulations, documents...)
3. **Main operations**: what can users do (CRUD, transformations, queries, real-time updates...)
4. **Data flow**: where does data come from, where does it go, what is authoritative vs derived
5. **Constraints**: performance requirements, external integrations, security boundaries
6. **What must never happen**: data loss scenarios, consistency violations, domain-specific catastrophes

From the answers, the AI produces:
- A suggested module structure (what folders/files, what depends on what)
- An initial zone classification (stable, shared, features, workspace)
- Candidate architecture rules (3-5 rules that emerge from the constraints)
- Candidate domain invariants (properties that must always hold)
- A first draft of CLAUDE.md

**Path B - From an existing document or codebase:**

The user provides a requirements document, a design spec, or an existing codebase. The AI analyzes it and extracts:

1. **Module map**: what components exist, how they relate
2. **Dependency graph**: what depends on what, where are the coupling points
3. **Hotspots**: large files, God Objects, files with many consumers
4. **Implicit rules**: patterns that are followed consistently (naming, data flow, error handling)
5. **Fragile points**: code that breaks often, areas with poor test coverage, undocumented constraints

From the analysis, the AI produces the same output as Path A: module structure, zone classification, rules, invariants, and a CLAUDE.md draft.

**Key principle**: Phase 0 output is always a draft. The user is the architect, the AI is the analyst. The AI proposes, the user decides. This is especially important for zone classification - only the user knows which code is truly stable and which is still evolving.

**When to skip Phase 0**: if you already have a clear architecture documented elsewhere (design docs, ADRs, existing README), you can write CLAUDE.md directly from those sources. Phase 0 is for projects where the architecture is in the developer's head or scattered across code without explicit documentation.

### 1.2 Unification of v1 documents

In v1 there were 3-4 separate documents the AI had to consult:
- CODEMAP.md (architecture map)
- CONTRACTS.md (interface contracts)
- METRICS.md (invariant thresholds)
- FUNCTION_REGISTRY.yml (function catalog)

In v2, the essential contents of all these converge into **CLAUDE.md** (or equivalent for other tools), which the AI model reads automatically at the start of every session.

### 1.3 CLAUDE.md template for ControlCoding

```markdown
# CLAUDE.md - [Project Name]

## Architecture
[Concise description of the system architecture.
What main components exist, how they communicate,
what is the source of truth and what are the derived views.]

## Inviolable Principles
- **Model vs View**: NEVER write authoritative data in derived views
- **Determinism**: [If applicable] Use seed-based PRNG. No wall-clock in calculations
- **Coherence**: [Domain-specific invariants, e.g. balances, conservation, consistency]
- **One Concept One Place**: Don't fragment cohesive algorithms

## Directory Structure
- stable/: PROTECTED. Do not modify without explicit approval
- shared/: reusable utilities. Consult before creating new functions
- features/: each feature is isolated with a single public API
- workspace/: sandbox for experiments

## Invariants (test thresholds)
[List of domain-specific invariant tests with numerical thresholds.
Examples:
- Balance consistency: debits == credits (0% drift)
- API response time p95: <= 200ms
- Test coverage: >= 80%
- No orphan links in the content graph]

## Key Functions (do not duplicate)
[List of critical functions or those with non-obvious semantics.
Only those the AI might confuse or recreate.
Format: name, file, brief note.]

## Protected Zones (enforcement: hooks)
[List of protected zones with WARN or DENY level.
Format: path, description, level.]
When a hook emits WARN on a protected zone, STOP and ask for explicit confirmation
from the user before proceeding. Show: which file, which zone,
why the modification is necessary. Never proceed automatically after a WARN.

## Conventions
[Namespace, naming, patterns used, code style,
build system, main dependencies.]
```

### 1.4 CLAUDE.md vs detailed documents

CLAUDE.md contains the operative summary - what the AI needs to know to work correctly. Detailed documents (ADR/, design docs, etc.) remain as references for deeper exploration, but the AI should not read them at every session.

Rule: if a piece of information is critical for every work session, it goes in CLAUDE.md. If it's only useful for specific decisions, it stays in the project documentation.

### 1.5 Operative Rules: extracted into CLAUDE.md

The CLAUDE.md template (section 1.3) defines *what* to write. This section explains *how* to extract operative rules from ControlCoding and place them in the CLAUDE.md so the AI applies them automatically at every session, without having to read the entire methodology document.

The extract is called **"ControlCoding Rules (operative principles)"** and should be inserted as a dedicated section in the CLAUDE.md.

What goes in Operative Rules (in the CLAUDE.md):

| Category | Content | Generic Example |
|---|---|---|
| Inviolable principles | 3-5 fundamental domain rules | Model vs View, Determinism, Conservation |
| Structural rules | Where to put new code, what not to do | "New modules in features/, NEVER in core" |
| Known problems | Technical debt with prohibition on worsening. Each file listed here should have a corresponding WARN in the hook | "AppController is a God Object, DO NOT add methods" |
| Invariants | Checklist of what to verify after each modification | Consistent balance, no orphan links, API < 200ms |
| Workflow | 5 operative points of the work cycle | Plan, read, test, document, don't over-engineer |

What does NOT go in Operative Rules (stays only in ControlCoding):

- Theory and rationale behind principles (why Model vs View, why determinism)
- Comparisons with other tools/frameworks (Kiro, Cursor, Aider, etc.)
- History of ControlCoding versions (from v1 to v2)
- Full specifications of feature.lock and hooks
- Extended examples for other domains
- CI pipeline with 3 gates (until implemented)

Why this pattern works:

1. Efficiency: the AI reads ~60 operative lines, not 1000+ lines of methodology
2. Action, not theory: every line in CLAUDE.md is something the AI must do or not do
3. Easy updates: operative rules change with the project; the methodology does not
4. Single source of truth for rules: CC documents the "why"; CLAUDE.md the "what"
5. No redundant reading: the AI does not need to read CC at every session

Generic template. The CLAUDE.md section has this structure:

```markdown
## ControlCoding Rules (operative principles)

> Operative extract from ControlCoding. Only actionable rules.
> For theory and rationale, read the complete document.

### Inviolable Principles
- **Model vs View**: NEVER write authoritative data in derived views
- **Determinism**: [If applicable] Seed-based PRNG, no wall-clock
- **Conservation**: [If applicable] Every transformation preserves quantities
- **One Concept One Place**: A cohesive algorithm belongs in a single file/class

### Structural Rules: where to put code
- **New features**: create in features/<name>/. NEVER in core/entry point.
- **Shared utilities**: use shared/. Consult before creating new ones.
- **Dependency direction**: [specify the project's hierarchy]

### Known Structural Problems (do NOT worsen)
- [List of technical debts with clear instruction on what NOT to do]

### Domain Invariants (to verify)
- [List of invariants with numerical thresholds if available]

### Workflow
1. Plan first  2. Read before modifying  3. Test after
4. Document significant commits  5. Don't over-engineer
```

The operative rules are a derivative of ControlCoding, not a substitute. Update the operative rules when:
- A new technical debt is discovered
- An invariant is implemented
- The project structure changes
- A known problem is resolved (remove it from the list)

ControlCoding remains the reference document for the complete methodology, rationale, and gradual adoption guidelines.

### 1.6 Onboarding: new AI sessions and new developers

CLAUDE.md is the ControlCoding onboarding tool. No meetings, wikis, or separate "getting started" documents are needed.

New AI session: the model reads CLAUDE.md automatically at the start of the session. If the operative rules are well-written (section 1.5), the AI knows immediately what to do and what not to do. It doesn't need to read the entire methodology document.

New human developer: reads CLAUDE.md as the first step. In 5 minutes they have an overview of the architecture, principles, structural rules, and invariants. If they want to understand the rationale behind a rule, they consult the complete ControlCoding document.

New project with a team that knows ControlCoding: copy the CLAUDE.md template from section 1.3, customize for the domain (invariants, structure, known technical debts), configure the boundary hook. Time: 1-2 hours to have active guardrails.

The measure of onboarding effectiveness is this: the first AI session on a new project should produce code that passes all invariant tests on the first commit, because the guardrails prevent violating principles even without prior knowledge of the project.

---

## 2. Feature.lock v2

> **Note**: feature.lock is an L4 concept planned for v2.2. The section below documents the design. Current boundary enforcement uses `cc_config.json` protected zones (see Section 1).

### 2.1 The feature.lock remains, becomes executable

The feature.lock is still the "lease agreement" for each feature, but now has a dual role:
1. Documentation: readable by humans and AI
2. Enforcement: read by hooks to block violations

### 2.2 v2 Format

```yaml
# features/Payments/feature.lock
feature: Payments
version: 2

# Modification perimeter
allow:
  - "features/Payments/src/**"
  - "features/Payments/tests/**"
  - "features/Payments/config/**"

deny:
  - "stable/**"
  - "shared/**"
  - "features/**/include/**"    # Other features' APIs

# Tests that must ALWAYS pass after every modification
tests:
  must_pass:
    - "test_payments_*"
    - "test_invariant_balance_consistency"
    - "test_invariant_idempotency"

# Quantitative thresholds (adapt to domain)
metrics:
  response_time_p95_ms_max: 200
  balance_drift_max: 0.0
  test_coverage_min: 0.80

# Internal sub-systems (for organization)
systems:
  TransactionProcessor:
    entry: "src/systems/TransactionProcessor/TransactionProcessor.h"
    internal: "src/systems/TransactionProcessor/**"
  RetryManager:
    entry: "src/systems/RetryManager/RetryManager.h"
    internal: "src/systems/RetryManager/**"
```

### 2.3 Differences from v1

- Added `version: 2` to distinguish from previous format
- `contracts` field eliminated (redundant with principles in CLAUDE.md)
- `metrics` field simplified (only thresholds, no descriptions)
- Enforcement via automatic hook, not dependent on AI discipline

---

## 3. Function Registry (Lightweight)

### 3.1 Why still a registry

Opus 4.6 can explore the codebase and find existing functions. But for large projects (>100 files), the search can be slow or imprecise. The registry serves for:
- Functions with **non-obvious semantics** from the name
- Functions with **similar alternatives** the AI might confuse
- Functions in **shared/** that all features should know about

### 3.2 v2 Format (lightweight)

```yaml
# shared/FUNCTION_REGISTRY.yml
# Only "non-obvious" or critical functions. The AI explores the rest autonomously.

auth:
  verifyToken:
    file: "shared/auth/TokenVerifier.h"
    note: "Verifies JWT with key rotation. DO NOT use jwt.decode() directly."

  hashPassword:
    file: "shared/auth/PasswordHasher.h"
    note: "Uses bcrypt with salt. DO NOT create alternative hashes."

data:
  bulkUpsert:
    file: "shared/db/BulkOperations.h"
    note: "Batch upsert with automatic chunking. Max 1000 per chunk. Idempotent."

  buildQueryFilter:
    file: "shared/db/QueryBuilder.h"
    note: "Sanitizes SQL input. ALWAYS use this instead of string concatenation."

cache:
  invalidatePattern:
    file: "shared/cache/CacheManager.h"
    note: "Pattern-based glob invalidation. Asynchronous. Does not guarantee immediate invalidation."
```

Compared to v1: no `signature`, `used_by`, `performance_notes` fields. We keep only `file` and `note`. The AI finds the rest by reading the file.

---

## 4. cc.py (CLI Tool)

### 4.1 Commands

| Command | What it does |
|---|---|
| `cc setup` | Chat-first setup prompt or non-interactive apply from a handoff file |
| `cc init` | Non-interactive: copy hooks, create settings.json, generate CLAUDE.md skeleton |
| `cc install debug-tools` | Add visual_check, consultant MCP, session manager |
| `cc install bridge` | Add multi-agent bridge MCP |
| `cc surface status|claim|observe|request-takeover|resolve-takeover|heartbeat|release|run` | Manage the local Primary/Observer surface lock for CC-aware hosts, wrappers, and UI surfaces |
| `cc write-path status|enable|disable|prepare|check|apply` | Manage the opt-in controlled write path prototype for non-inline hosts |
| `cc memory init|scan|status|bootstrap|op-index|startup|session|session-pack|graph|retrieve|rag-pack|impact|context|chunks|layout|lifecycle|vector|note|idea|decision|consult|agent-run|sync-report|cleanup-temp|views|work-*` | Manage the local Project Memory Engine under `.controlcoding/` and the compatible ControlWork Project Plane under `.controlwork/` |
| `cc docs audit|check|propose` | Audit architecture and system-document maintenance state without rewriting canonical docs |
| `cc benchmark run|compare|report` | Capture, compare, and render local benchmark evidence with the current 3.x interface |
| `cc benchmark-matrix generate` | Generate the multi-host capability/evidence matrix from the canonical host model |
| `cc organize` | Preview file-organization changes by default; apply only with `--apply` |
| `cc resume` | Print a provider-neutral context brief without launching a provider or subprocess |
| `cc doctor` | Verify all components are correctly installed |
| `cc review` | Generate a peer review prompt tailored to the project |

### 4.2 cc setup (chat-first apply flow)

`cc setup` now works in two supported ways:

1. Print the official chat-guided setup contract with `cc setup --chat-guide`
2. Apply a chat-generated handoff with `cc setup --answers-file handoff.json`

The chat-guided install flow covers the current base-install decision set:

1. Project identity (name only)
2. Documentation ownership selection (`managed` vs `project_managed`)
3. User Host selection (`claude_code`, `codex_cli`, `cursor`, `vscode`, `cline`, `gemini_cli`, or `other`)
4. Host workflow guidance
5. ControlCoding usage model (`Core`, `Core + manual consultation`, `Agents`, or `Studio`)
6. Directory scanning with zone classification (stable/shared/features)
7. Protected zone configuration (DENY vs WARN per zone)
8. Optional pack selection
9. Hook location
10. Engagement tier/runtime confirmation
11. Backend policy for engagement
12. Confirmation

It does not collect stack, architecture, or kickoff-doc answers in base setup.
Those belong to the separate `cc setup-project` flow after installation.

Base setup generates a tailored CLAUDE.md with filled-in values instead of placeholders, creates or updates `cc_config.json` with `documentation_mode`, `cc_artifact_mode`, real zones, and `hooks_location`, records the selected `userHost` plus local `hostInstructions` preferences in `.controlcoding/gateway_config.json`, writes local host integration assets in `.controlcoding/launchers/` (including a host-specific task preset such as `cursor.tasks.json` or `claude-code.tasks.json`, `manifest.json`, and `host_instructions.md`), runs `init` and selected `install` commands, and finishes with `doctor`.
It also generates local launcher helpers in `.controlcoding/launchers/` so the chosen
host for that project has stable Primary/Observer entrypoints without requiring
the user to remember raw `cc surface ...` commands.

The separate `cc setup-project` flow writes `.controlcoding/setup_intent.json`.
That file is the canonical setup-intent snapshot for the current project setup:
source mode, environment/install policy, product framing, technical direction,
governance choices, module boundaries, protection bootstrap, and the phase graph
used by chat-first setup surfaces. It also records recommendation cards for the
accepted major project-shape choices, so source mode, selected reference,
kickoff readiness, product form, stack direction, architecture direction,
truth/view split, planning basis, documentation mode, and install policy retain
the recommendation, reason, alternative, and user-facing question that led to
the accepted setup intent.
The setup-intent snapshot additionally records `recommendationGuidance` with
the active recommendation profile and canonical `core_greenfield` /
`core_brownfield` examples. These examples are also printed by
`cc setup-project --chat-guide`, so chat hosts have concrete copy to follow
instead of falling back to weak confirmation prompts.

Current release note:

- `Core + manual consultation` stays inside the public `Core` story
- the current public `Agents` path stays explicit on the chosen official host
- API-backed routed specialists belong to the later Version II path
- `Studio` / CC UI is a later optional extra, not part of the current release path

### 4.3 cc init (non-interactive)

`cc init` copies all hooks to `hooks/`, creates `.controlcoding/settings.json` with correct hook wiring (absolute paths), generates the canonical context template, and initializes the base working docs (`STATUS.md`, `ROADMAP.md`, `BUGS.md`). Legacy `.claude/settings.json` remains readable for older repos. The `install` subcommands add optional tools (MCP servers, scripts) on top of the base setup.

**`--central-hooks` flag**: when passed, hooks are installed to `~/.controlcoding/hooks/` instead of the project-local `hooks/` directory. This is useful for shared or multi-project setups where you want a single set of hook scripts used by all projects. The hooks_location preference is saved to `.controlcoding/cc_config.json` so `cc doctor` knows where to check, with legacy `.claude/cc_config.json` fallback. When using central hooks, the auto-generated `.gitignore` block omits the hook file patterns (since the hooks are not in the project directory).

**Auto-gitignore**: `cc init` automatically appends a ControlCoding block to the project's `.gitignore` file (creating it if needed). The block covers hook scripts in `hooks/`, CC runtime artifacts in `.controlcoding/` (with legacy `.claude/` fallback patterns where compatibility still matters), local editor helpers such as `.vscode/tasks.json`, optional tool copies in `tools/`, and visual check / multi-agent runtime directories (`screenshots/`, `.bridge/`). `devlog/` is always kept local as session memory. In the recommended adopter setting (`cc_artifact_mode = local_only`), the block also excludes governed CC working docs such as `STATUS.md`, `ROADMAP.md`, `BUGS.md`, and `dev/` when documentation is CC-managed. The block is delimited by marker comments (`# --- ControlCoding local artifacts ---` / `# --- End ControlCoding ---`) and is only added once. This prevents CC artifacts from leaking into the git repository.

### 4.3b cc organize

`cc organize` previews repairs to the local ControlCoding documentation
structure without opening a setup wizard. The default is preview-only. Add
`--apply` to create the `devlog/` category folders, move clearly named devlog
files such as `plan-*`, `design-*`, `reasoning*`, and `criteria*`, and refresh
`devlog/index.md`.

Use `--dev-taxonomy` when a project already has loose planning or architecture
documents and you want to align them with the managed `dev/` taxonomy:

```bash
python scripts/cc.py organize --project-root . --dev-taxonomy --dry-run
python scripts/cc.py organize --project-root . --dev-taxonomy --apply
```

The dev taxonomy mode creates the baseline `dev/plans/` and `dev/design/`
trees, including `archive/` and `deprecated/`, and moves only clearly
classified root-level or misplaced `dev/` files. It does not move or rewrite
public `docs/` files. Use `--check --dev-taxonomy` in CI or a manual gate when
you want a non-zero exit if local planning docs are still misplaced.

### 4.3c cc docs

`cc docs` is a proposal-first maintenance command for architecture and
system-document drift. It does not rewrite `CONTROLCODING.md`, architecture
docs, memory docs, or release docs automatically.

Use it when a feature changes command routing, memory behavior, ControlWork
compatibility, documentation governance, release boundaries, or other system
behavior that should be reflected in project truth.

```bash
python scripts/cc.py docs audit --project-root .
python scripts/cc.py docs check --project-root .
python scripts/cc.py docs propose --project-root .
```

Subcommands:

- `audit`: read-only architecture and system-document audit.
- `check`: CI-friendly audit. It fails on errors and can fail on warnings with
  `--strict`.
- `propose`: writes a review artifact under
  `.controlcoding/docs/proposals/` by default.

The command complements `cc truth check-docs`, `cc context drift`,
`cc index --check`, `cc organize --check`, and `cc memory views generate`.
Those commands keep their separate responsibilities.

### 4.3d cc memory

`cc memory` manages a project-local development memory under `.controlcoding/`.
This is ControlCoding state for working on the project. It is separate from any
runtime memory, vector store, document store, or application-owned data that the
project itself may create.

Detailed guide: [Project Memory Engine](../project-memory-engine.md).

The memory engine stores a small SQLite canonical store plus generated Markdown
views. It tracks entities, edges, sources, events, semantic document chunks,
first-class document-layout nodes, and correlation suggestions for project work
such as documents, decisions, ideas, consults, agent runs, lifecycle state, and
path/topic impact lookup. Because it lives under `.controlcoding/`, it can be
removed with the rest of the ControlCoding local control plane when a clean
project checkout is needed.

Use `full` mode for normal ControlCoding projects:

```bash
python scripts/cc.py memory init --project-root . --mode full --project-short MyProj
python scripts/cc.py memory scan --project-root .
python scripts/cc.py memory context --project-root . src/subsystem/file.py
python scripts/cc.py memory impact --project-root . "billing"
python scripts/cc.py memory chunks --project-root . docs/plan.md
python scripts/cc.py memory layout --project-root . docs/plan.md
python scripts/cc.py memory lifecycle mark --project-root . docs/old-plan.md --state legacy --reason "Historical reference."
python scripts/cc.py memory vector rebuild --project-root .
python scripts/cc.py memory vector search --project-root . "billing reconciliation"
python scripts/cc.py memory views generate --project-root .
```

Use the startup protocol at the beginning of a new chat when the host can run
tools visibly:

```bash
python scripts/cc.py memory startup --project-root . --scope dev --topic "GraphRAG"
```

The startup protocol is read-only. It asks whether the user is continuing prior
work or starting fresh, then prints visible commands for RAG-O, Session
GraphRAG, and Dev GraphRAG. It does not create sessions automatically.

Use RAG-O at the start of a session when you need the read-only control view
across Memory Bootstrap, Dev GraphRAG, Work GraphRAG routes, Project Plane drift,
packet pointers, and commit hygiene:

```bash
python scripts/cc.py memory op-index --project-root . --scope dev --topic "GraphRAG"
```

RAG-O means RAG Operations Index. It coordinates routes and maintenance status;
it is not a retrieval or generation engine.

Build a cross-plane packet when a chat needs Dev GraphRAG, Project Plane
ControlWork context, Session GraphRAG, and RAG-O routes in one read-only
artifact:

```bash
python scripts/cc.py memory cross-pack --project-root . "GraphRAG" --limit 10
```

`cross-pack` keeps each citation tagged with its memory plane. It does not sync
ControlWork, refresh views, create sessions, or merge application-owned memory
into ControlCoding truth.

Use Session GraphRAG commands when a chat or work session should be recorded
explicitly:

```bash
python scripts/cc.py memory session start --project-root . --topic "GraphRAG implementation" --mode continue_previous_work
python scripts/cc.py memory session link --project-root . <session-id> --commit <sha>
python scripts/cc.py memory session note --project-root . <session-id> "Follow up on RAG-O integration." --kind followup
python scripts/cc.py memory session close --project-root . <session-id> --status needs_followup --summary "Implemented the session CLI base."
python scripts/cc.py memory session views --project-root .
python scripts/cc.py memory session-pack --project-root . --topic "GraphRAG implementation" --status all
```

Session commands store auditable session records, links, views, and packets.
RAG-O reads session status without writing, and Dev GraphRAG retrieval includes
session records with lower source trust. Session commands do not save raw chat
transcripts, sync ControlWork, or mutate memory unless the command is run
explicitly.

Use `document-only` mode when you only want the document/work-memory module
without adopting the full ControlCoding workflow:

```bash
python scripts/cc.py memory init --project-root . --mode document-only --project-short Docs
```

`document-only` creates the memory database, logs, and generated views. It does
not install hooks, MCP servers, agents, source folders, or full ControlCoding
project structure.

Use embedded ControlWork when the project needs a Project Plane for research,
requirements, source summaries, documents, planning, and handoff material that
can also exist as a standalone ControlWork repository:

```bash
python scripts/cc.py memory work-quickstart --project-root . --dry-run
python scripts/cc.py memory work-quickstart --project-root .
python scripts/cc.py memory work-init --project-root . --name "Project Name"
python scripts/cc.py memory work-status --project-root .
python scripts/cc.py memory work-category list --project-root .
python scripts/cc.py memory work-scan --project-root .
python scripts/cc.py memory work-analyze --project-root .
python scripts/cc.py memory work-review docs/brief.md --project-root . --review-status ready_to_promote --sensitivity internal
python scripts/cc.py memory work-promote docs/brief.md --project-root . --area sources --title "Reviewed brief" --summary "Reviewed source summary."
python scripts/cc.py memory work-import-source docs/brief.docx --project-root . --area sources --summary "Imported DOCX for review."
python scripts/cc.py memory work-ocr status --project-root .
python scripts/cc.py memory work-query "project topic" --project-root .
python scripts/cc.py memory work-graph explain CONTROLWORK.md --project-root .
python scripts/cc.py memory work-graph neighbors CONTROLWORK.md --project-root .
python scripts/cc.py memory work-graph stale --project-root .
python scripts/cc.py memory work-graph unresolved --project-root .
python scripts/cc.py memory work-graph diff --project-root . --baseline .controlwork/graph-baseline.json
python scripts/cc.py memory work-retrieve "project topic" --project-root .
python scripts/cc.py memory work-rag-pack "project topic" --project-root .
python scripts/cc.py memory work-views --project-root .
python scripts/cc.py memory work-checkpoint --project-root . --title "Session checkpoint"
python scripts/cc.py memory work-handoff --project-root .
python scripts/cc.py memory work-dashboard --project-root .
python scripts/cc.py memory work-dashboard --project-root . --format json --output .controlwork/dashboard/project-map.json
python scripts/cc.py memory work-context-pack --project-root . --scope planning --topic "Project direction"
```

`work-quickstart` is the fastest embedded Project Plane setup path. Dry-run
mode reports the files it would create without writing. Write mode initializes
the Project Plane, scans files, writes derived views, and creates a deterministic
context packet without external calls.

`work-query` is the normal query-first Project Plane surface after quickstart.
It returns top matches, a GraphRAG packet, graph explain targets, optional graph
path probing, and next commands without AI calls, network calls, or hidden
subprocesses.
`work-graph neighbors|stale|unresolved|diff` turns graph output into a practical
attention surface for connected records, stale material, unresolved review, and
baseline comparison.
`work-dashboard` writes a static HTML or Markdown Project Plane dashboard under
`.controlwork/dashboard/` by default. With `--format json`, it writes the
`controlwork-project-map/v1` Project Map contract for the local cockpit. Each
explicit generation refreshes the local scan index and scan analysis first,
unless `--no-refresh-scan` is supplied. Both forms are projections and run
without external calls.

The scan/import/review/promotion path is governed. `work-scan` indexes files
without importing them as truth. `work-review --review-status ready_to_promote`
requires readiness blockers to be clear. `work-promote --force` requires
`--force-note` when it overrides blockers or duplicate promotion checks.

Bridge a standalone ControlWork folder explicitly:

```bash
python scripts/cc.py memory work-import --project-root . ../MyWorkProject
python scripts/cc.py memory work-attach --project-root . ../MyWorkProject
python scripts/cc.py memory work-sync --project-root . --direction pull --force
python scripts/cc.py memory work-sync --project-root . --direction push --force
python scripts/cc.py memory work-export --project-root . ../MyWorkOnlyFolder
```

`work-status` keeps raw fingerprint drift visible, then classifies whether the
drift is semantically actionable. Expected embedded versus standalone
distribution text and category audit timestamp differences do not create sync
requests when the shared contract and memory content match. Pull or push sync
commands are recommended only when Project Plane memory content actually
differs.

Optional Project Plane integrations:

```bash
python scripts/cc.py memory work-obsidian init --project-root .
python scripts/cc.py memory work-wiki import-edits --project-root . --review
python scripts/cc.py memory work-mcp tools
python scripts/cc.py memory work-mcp call controlwork_list_categories --project-root .
python scripts/cc.py memory dev-context-pack --project-root . --scope backend --topic "billing import"
```

Working records can be added directly:

```bash
python scripts/cc.py memory note add --project-root . "Investigate cache invalidation" --area Backend
python scripts/cc.py memory idea add --project-root . "Visual wiki view" --area Memory
python scripts/cc.py memory decision add --project-root . "Keep memory under .controlcoding" --rationale "Development memory must stay removable."
python scripts/cc.py memory consult record --project-root . --title "External architecture review" --summary "Keep the orchestrator in the CLI."
python scripts/cc.py memory agent-run record --project-root . --role reviewer --task "Review memory MVP" --summary "No blocking issue found."
```

Development-memory IDs use the `CC_<ProjectShort>_DEV_...` prefix. If the
project application has its own internal memory, keep it separate and give it a
project-owned prefix such as `<ProjectShort>_MEM_...`.

Use these workflows:

- notes for observations that are useful but not decisions
- ideas as an inbox for later plans or decisions
- research intake as scanned documents plus a concise note or decision
- semantic chunks for heading-based document recall
- correlation suggestions for explicit links, shared headings, and shared terms
- lifecycle workflows for stale, legacy, superseded, and conflicting records
- local sparse vector search rebuilt from semantic chunks
- decisions for canonical project choices
- consult records for non-authoritative external/manual review outcomes
- agent-run records for what an agent attempted, changed, and verified
- generated views for handoff and work context

On Windows, interrupted pytest or sandboxed smoke tests can leave ACL-locked
local temp directories. Diagnose without deleting:

```bash
python scripts/cc.py memory cleanup-temp --project-root .
```

Remove only verified pytest temp candidates inside the project root:

```bash
python scripts/cc.py memory cleanup-temp --project-root . --apply
```

`cc memory doctor` also warns when it sees known pytest temp candidates such as
`.pytest-local-*`, `.pytest-tmp`, or `.tmp_pytest*`.

### 4.4 Behavioral constraints

The setup flow collects deterministic behavioral rules for the AI agent. These are project-specific constraints that go into CLAUDE.md's Operative Rules section. Unlike boundary zones (which are mechanically enforced by hooks), behavioral rules are advisory - they depend on agent compliance. Examples:

- "No random without seed in simulation code" (determinism)
- "All database changes require a migration file" (process)
- "Never call external APIs without timeout" (resilience)
- "All state mutations go through the Store" (architecture)

These rules define the agent's behavioral profile for the project. They are most effective when they are specific, testable, and few. A CLAUDE.md with 5 clear behavioral rules outperforms one with 50 vague suggestions. For rules that must be enforced mechanically, configure them as workflow rules in `check_workflow.py` instead.

### 4.4b cc surface (local surface authority lock)

`cc surface` manages the runtime lock in `.controlcoding/cc_surface_lock.json`
(legacy `.claude` fallback). Use it
when you have more than one CC-aware surface and you want authority to be
explicit rather than inferred.

Typical commands:

```bash
python scripts/cc.py surface claim host:claude-vscode --type official_host --label "Claude Code (VS Code)"
python scripts/cc.py surface observe ui:local-observer --type cc_ui_local --label "CC Local Observer"
python scripts/cc.py surface request-takeover host:codex-cli --reason "Switching primary host"
python scripts/cc.py surface resolve-takeover takeover_123 --decision approved --resolved-by host:claude-vscode
python scripts/cc.py surface run host:claude-vscode --type official_host --label "Claude Code (VS Code)" -- claude
python scripts/cc.py surface heartbeat host:claude-vscode
python scripts/cc.py surface release host:claude-vscode
python scripts/cc.py surface status --json
```

What they mean:

- `claim`: declare that surface the current primary
- `observe`: attach a surface in observer-only mode
- `request-takeover`: open an explicit pending takeover instead of silently switching primary authority
- `resolve-takeover`: let the current primary approve or deny the pending takeover
- `run`: wrap a child process and manage claim, heartbeat, and release automatically while it runs
- `heartbeat`: refresh a long-running surface so it does not go stale
- `release`: remove a surface from the local authority lock
- `status`: inspect the current authority state and active surfaces

On consumer-auth paths, the conservative default is to `claim` the official
host and leave CC UI surfaces as `observe`. Treat CC-owned primary chat/UI
paths as later API/local work, not as the current public release baseline.

### 4.4c cc write-path (controlled write path prototype)

`cc write-path` is the first serious prototype for a CC-owned write path on
hosts that do not expose native inline hooks.

What it is:
- opt-in only
- currently `patch_gateway` mode only
- currently expects `unified_diff` patches
- validates protected-zone targets before `git apply` lands the patch
- can optionally run a shadow-worktree `fitness_check.py` preflight before apply
- records local metrics/events so the UX cost is measured instead of guessed
- writes terminal receipts to `.controlcoding/write_path_receipts/` for local audit

What it is not:
- not implicit interception of normal editor writes
- not a claim of Claude-style parity
- not yet a shadow workspace or cross-platform filesystem lock layer

Typical flow:

```bash
python scripts/cc.py write-path enable --mode patch_gateway
python scripts/cc.py write-path prepare --patch-file proposed.diff
python scripts/cc.py write-path check --patch-file proposed.diff --manifest-id <manifest_id>
python scripts/cc.py write-path apply --patch-file proposed.diff --manifest-id <manifest_id> --reason "wire approved refactor"
python scripts/cc.py write-path status
```

When enabled, `cc doctor` and `cc host status` expose the advanced path as an
explicit, optional layer on top of the normal host capability model.

If `--require-manifest` is enabled, apply is blocked unless the exact patch
matches a prepared manifest, including the baseline snapshot of the touched
files. This strengthens the CC-owned path without pretending to intercept
normal editor writes.
If `--preflight-fitness` is enabled, `apply` first replays the patch in a
shadow git worktree and runs `fitness_check.py` there. Exit code `1` blocks the
real apply; warning-only results do not.

`cc benchmark-matrix generate` now also appends a local dogfooding snapshot.
That section reports the current repo config and latest write-path receipt; it
is operational evidence, not a cross-host parity claim.

For a dedicated local evidence artifact:

```bash
python scripts/cc.py benchmark-matrix report-local
```

Useful receipt inspection commands:

```bash
python scripts/cc.py write-path receipts
python scripts/cc.py write-path receipt --receipt-id <receipt_id>
```

### 4.4d cc benchmark-matrix

`cc benchmark-matrix generate` writes a capability/evidence matrix for all
supported hosts. It is generated from the same host profile model used by
`doctor` and `host status`, plus curated evidence metadata.

Typical flow:

```bash
python scripts/cc.py benchmark-matrix generate
python scripts/cc.py benchmark-matrix generate --output benchmarks/custom-matrix.md
```

The matrix is intentionally honest:

- it distinguishes inline vs repo-side vs review-driven protection models
- it reports which gates are mechanical vs conditional vs unavailable
- it does not claim Claude-style parity for hosts without inline hooks

For `Agents / human_mediated`, the adjacent manual specialist flow is:

```bash
python scripts/cc.py consult-packet create --role architect --objective "Validate repo-side boundary split" --question "Should gateway branching move into a dedicated adapter?"
python scripts/cc.py consult-packet show --packet-id <packet_id>
python scripts/cc.py consult-packet create --role architect --thread-id <thread_id> --objective "Follow up on the adapter split" --question "What should move first?"
python scripts/cc.py consult-packet create --role architect --topic-key adapter_split --objective "Compare alternate adapter split" --question "Should option B be rejected?"
python scripts/cc.py consult-result import --packet-id <packet_id> --summary "Keep the split, isolate adapter logic." --decision partial --rationale-summary "Current split works, but gateway branching is leaking across layers." --next-action "Extract the adapter module."
python scripts/cc.py consult resolution --role architect --topic-key adapter_split
python scripts/cc.py consult status
```

That flow is intentionally honest:

- the specialist path is configured in `.controlcoding/cc_engagement.json`
- `execution_mode=human_mediated` means CC prepares and imports, but does not execute the external call itself
- `execution_mode=cc_routed` and `execution_mode=auto_bounded` are now runtime-gated against the persisted specialist consent matrix and `gateway_config.json`; missing or unknown routed backends are blocked instead of silently bypassed
- `execution_mode=auto_bounded` also checks the configured runtime budget before the backend call is attempted
- import artifacts persist concise engineering summaries and next actions, not verbose raw chain-of-thought transcripts
- summary fields are bounded to stay concise, so the audit log does not become a hidden transcript dump
- manual threads keep `thread.json`, `memory.md`, and `resume_prompt.md` under `.controlcoding/external_consultation/threads/`, and `--thread-id` continues the same bounded consultation instead of restarting it
- role-level continuity is summarized under `.controlcoding/external_consultation/role_memory/`, with a repo-local `convergence_summary.md` to surface unresolved or conflicting manual guidance
- `--topic-key` lets separate manual consultations merge onto the same decision topic so conflicts are detected per issue, not only per role
- `cc consult resolution --role ... --topic-key ...` shows the generated resolution artifact when a merged topic enters explicit conflict

This is the current release-oriented `Agents` path. Routed/API-backed
specialist execution belongs to the later Version II path.

### 4.4e cc benchmark

The current benchmark interface is:

```bash
python scripts/cc.py benchmark run
python scripts/cc.py benchmark compare <baseline> <current>
python scripts/cc.py benchmark report
```

`compare` takes the baseline and current JSON paths as positional arguments.
The former `--baseline`, `--current`, and report `--input` interface from the
`v2.5.2` line is not part of `3.0.0`. `run` writes
`benchmarks/run.json` by default, and `report` writes
`benchmarks/report.md` by default. Both output paths can be changed with
`--output`.

### 4.4f cc resume and cc replace migration

`cc resume` prints a provider-neutral context brief. It does not select or
launch Claude, another provider, or any subprocess. `--brief` remains accepted
for CLI compatibility but does not change the current print-only behavior.

The public `cc replace start/status/complete` command was removed in the
`3.0.0` major line. There is no direct replacement command; choose a workflow
that fits the specific migration instead of relying on an invented alias.

### 4.5 cc review (peer review prompt)

`cc review` generates a tailored prompt for an independent peer review of the project. The prompt is designed to be pasted into a fresh Claude session (not the development session) so the reviewer has no development bias.

**How it works**:
1. Reads CLAUDE.md for project identity
2. Reads `.controlcoding/cc_config.json` for protected zones (if present, with legacy `.claude/cc_config.json` fallback)
3. Scans the project tree via `git ls-files` (respects .gitignore)
4. Loads `docs/last_review_summary.md` for historical context (if present)
5. Assembles a structured review prompt from a fixed template plus project-specific data
6. Saves to `docs/review_prompt.md` (gitignored) and copies to clipboard if pyperclip is available

**Usage**:
```bash
python scripts/cc.py review --project-root /path/to/project
python scripts/cc.py review --stdout    # print to terminal instead of file
```

**When to run a review**: after significant batches of changes, before releases, when the architecture feels uncertain, or when you want an honest external assessment.

**What to do with the output**: paste the prompt into a fresh Claude chat. The reviewer will read the entire repository and produce a structured critique. After the review, create `docs/last_review_summary.md` with the recommendations and their status (DONE / IN PROGRESS / NOT STARTED). The next `cc review` run will include this context so the reviewer can track progress.

**Limitation**: the reviewer cannot execute code. Runtime bugs, integration issues, and performance problems will not be caught. The review focuses on design, code quality, architecture, and documentation.

### 4.6 cc doctor (health check)

`cc doctor` runs 11 checks to verify that all CC components are correctly installed and configured:

| # | Check | Pass condition |
|---|---|---|
| 1 | CLAUDE.md | Exists with 50+ non-empty lines, mentions boundary/invariant/zone/stable |
| 2 | STATUS.md | Exists (recommended for session continuity) |
| 3 | devlog/ | Directory exists (recommended for session history) |
| 4 | Hooks | `hooks/` directory has 2+ Python scripts, or central hooks at `~/.controlcoding/hooks/` are present. Essential files: `check_boundaries.py`, `check_dangerous_commands.py` |
| 5 | settings.json | `.controlcoding/settings.json` is valid JSON with hooks configured (legacy `.claude/settings.json` also accepted) |
| 6 | Tools directory | `tools/` directory exists with Python scripts (optional) |
| 7 | Fitness check | `tools/fitness_check.py` present (optional) |
| 8 | Git pre-commit | `.git/hooks/pre-commit` installed (optional) |
| 9 | Gitignore block | `.gitignore` contains the ControlCoding marker block. Warns if missing (CC artifacts may leak into git) |
| 10 | Leaked CC files | Runs `git ls-files` on CC hook file paths. Warns if any hook scripts are tracked by git when they should be gitignored |
| 11 | Domain invariants | `tests/` or `test/` directory with Python test files (needed for L3) |

When using central hooks (`hooks_location=central` in `cc_config.json`), checks 4 and 10 adapt to check `~/.controlcoding/hooks/` instead of the local `hooks/` directory.

---

## 5. phase0_discover.py (Codebase Discovery)

**Purpose**: Automates Phase 0 Path B by scanning an existing project and generating a CLAUDE.md draft.

**Usage**:
```bash
python phase0_discover.py --project-root /path/to/project --output CLAUDE.md.draft
```

**What it does**: Detects languages, frameworks, and build tools; suggests module boundaries based on directory structure and git history; and extracts candidate invariants from test files. The output is always a draft for human review.

---

## 6. fitness_check.py (Architectural Fitness Functions)

**Purpose**: Measures architectural health metrics defined in Section 6.6 of the methodology.

**Key metrics**:
- Dependency direction violations (must be 0)
- Efferent coupling of stable/ (must be 0)
- Instability of shared/ modules (<= 0.3)
- Configurable God-file / God-object heuristics
- Configurable layer-rule violations
- Configurable ownership / mutation rule violations by source zone
- Gateway-only call rule violations (including imported `gateway_modules`)
- Fan-in / fan-out hotspots in the internal dependency graph
- Import cycles in the internal dependency graph
- Files modified per task

**CodeWarden integration**:
- `codewarden_review.py` can now consume these repo-side fitness signals as review evidence
- architecture findings stay in `category=architecture` and can add an `architecture_tag` such as layer violation, God-file risk, coupling regression, or misplaced logic
- this strengthens semantic review on non-inline hosts without claiming inline-write parity
- fitness reports now include a `Configuration Evidence` section listing the
  loaded config sources and active rule-pack counts, so CodeWarden and CI output
  can distinguish built-in defaults, shared `.controlcoding/cc_config.json`
  rules, and local `fitness.json` overrides
- repo-side smoke coverage now exercises a Codex-style non-inline fixture where
  shared `architecture_fitness` produces layer-rule, gateway-rule, and
  mutation-rule failures in one CI run; this is repo-side evidence, not
  inline-write parity

**Configuration**:
- shared project rules live in `.controlcoding/cc_config.json` under `architecture_fitness`
- local experiments can still use `fitness.json`, which overrides shared project settings
- supported shared settings include `zones`, `thresholds`, `layer_rules`, `gateway_rules`, `ownership_rules`, `mutation_rules`, `skip_dirs`, and `git_history_commits`

**Dependencies**: Python stdlib only.

---

## 7. metrics_collector.py (Unified Metrics)

**Purpose**: Collects and aggregates metrics from various CC log files (ops_log.jsonl, codewarden_violations.jsonl, consult_log.jsonl) into a unified report.

---

## 8. MCP Servers

### 8.1 mcp_consultant.py (External Consultation)

The consultant MCP server exposes a `consult` tool for external AI consultation. It is the Level 3 implementation of the debug escalation system (see [Methodology](methodology.md) section on debug escalation).

**Configuration**:
```json
{
  "mcpServers": {
    "debug-consultant": {
      "command": "python",
      "args": ["tools/mcp_consultant.py"]
    }
  }
}
```

Fresh installation of the `debug-tools` pack leaves both
`CONSULT_BACKEND` and `CONSULT_FALLBACK_BACKEND` unset. PATH checks and API
key presence are discovery signals only. Configure a primary backend
explicitly in the MCP environment or pass `backend` to each `consult()` call.
Configure a fallback separately only when that fallback is intentional.

**Usage**:
```
consult(
  problem="Floor quad vertices at y=0, indices [0,1,2,2,3,0], GL_CULL_FACE enabled, camera at y=1.6. Floor invisible.",
  code_snippets="void Renderer::drawFloor() { ... }",
  tried_already="Increased ambient light to 0.8 - no effect. Changed texture binding order - no effect.",
  backend="ollama"
)
```

The external model returns hypotheses ranked by likelihood, with specific diagnostic steps.

**Consultant roles**: 5 specialized roles, each with a tailored system prompt:

| Role | Purpose | When to use |
|---|---|---|
| debug | Root cause analysis, diagnostic hypotheses | Default. Bug hunting, unexpected behavior |
| architect | Architectural review, patterns, trade-offs | Design decisions, structural changes |
| planner | Feasibility assessment, task decomposition | Before implementation, dependency analysis |
| reviewer | Code review (no web tools, pure analysis) | Quality check on isolated code snippets |
| socratic | Lateral thinking, challenge assumptions | When the AI converges too quickly on one solution |

The **socratic** role deserves specific explanation. AI models have a convergence bias: once they find a working solution, they stop exploring alternatives. In human-AI collaboration, the human's value is often in forcing exploration of the solution space - asking "why are we limiting ourselves to this approach?" or "what if we did the opposite?". The socratic role encodes this questioning behavior: it proposes alternatives, challenges assumptions, searches for prior art in other domains, and prevents premature convergence. It does not push toward any specific direction (not "simplify" or "preserve complexity") - it forces exploration.

**Backend options** (pick one as primary, optionally set a fallback):

1. **claude** - optional adapter for an independently configured official Claude Code CLI (`claude -p`). ControlCoding does not reuse consumer login, OAuth, or session tokens.
2. **ollama** - free, local. Requires Ollama running with a pulled model.
3. **openai** - OpenAI-compatible API. Works with OpenAI APIs, Azure OpenAI, LM Studio, Google Gemini via compatible endpoints, or any service with a `/v1/chat/completions` endpoint. Set `CONSULT_OPENAI_API_BASE` to point to non-OpenAI services.
4. **anthropic** - Anthropic API directly. Pay-per-token.

Within the fresh-install, setup-handoff, and `consult()` path covered here, no
option is selected from PATH, API-key discovery, the user host, or a launcher.
A non-empty explicit `backend` argument takes precedence over a non-empty
`CONSULT_BACKEND`. If neither is configured, `consult()` returns a
configuration error before governance gates, adapters, counters, or logs.
This narrow contract does not claim backend neutrality for `consult_verify`,
`consult_tandem`, or the Agent runtime.

**Authorization boundary**: the `claude` backend is tied to the official Claude
Code CLI path only. Do not reuse Claude consumer OAuth/session tokens in a
third-party product or CC-owned interface. For OpenAI consumer-plan coding workflows,
use the vendor's official CLI/app path; for product integrations, use API keys.

**Agent-mode consultants** (claude backend only): When using the claude backend, consultants are not just LLM calls - they are agents with tools. The subprocess gets `--allowedTools "WebSearch,WebFetch"` so it can search the web and fetch documentation while maintaining project isolation (no Read, Bash, Edit, or file access). Non-claude backends (ollama, openai, anthropic) remain text-only since those APIs do not have built-in agent loop capabilities via CLI.

**Public-safe default orchestration**: consultant calls should be initiated by
the human or by the Concierge with explicit manual approval. Recursive external
agent chains are out of scope for the public default profile. In the public UX,
consultants report back through the Concierge rather than talking directly to
the user in separate hidden flows.

**UI rendering rule**:

- if a future CC-owned UI uses API/local backends, rendering full worker chat
  history is acceptable
- if a future CC-owned UI visualizes workers that are actually running through
  the official Claude Code CLI, treat those panels as local traces/transcripts
  of CLI tasks, not as independent Claude.ai-style chat clients
- never add vendor consumer login or consumer-token reuse to make those panels
  interactive

**Domain auto-specialization**: the consultant adapts its language and risk flags to the project's domain. When called with `domain="finance"`, the system prompt prepends domain-specific terminology, common pitfalls, and relevant risk categories. The AI extracts the domain from the project context and passes it automatically. Supported domains include web/API, finance, scientific simulation, game development, embedded systems, data pipeline, and others. Unknown domains fall back to a generic engineering prompt.

**Per-role tool configuration**: each role gets tools appropriate for its function. The reviewer role has no tools (pure code analysis, external knowledge adds noise). All other roles get WebSearch and WebFetch.

**Per-role model configuration**: each role can use a different model optimized for its function. Set `CONSULT_MODEL_<ROLE>` environment variables (e.g. `CONSULT_MODEL_DEBUG`, `CONSULT_MODEL_ARCHITECT`). Empty = use backend default. This allows using a stronger model for architecture review and a faster model for code review.

**Prompt size limits**: the default limit is 32K characters (configurable via `CONSULT_MAX_PROMPT`). Per-role overrides are supported via `CONSULT_MAX_PROMPT_<ROLE>`. The reviewer role defaults to 16K since it reviews isolated code snippets. The limit prevents the local AI from dumping the entire codebase, not from sending substantial focused data.

**CallCounter**: thread-safe counter tracking real calls. Configurable via `CONSULT_MAX_CALLS` env var (0 = unlimited). Response includes `[Consultation N/MAX]` for cost transparency. Logs to `.claude/consult_log.jsonl` with call_number and calls_remaining.

**Requirement**: `pip install fastmcp` for the MCP server. The CLI fallback (`consult.py`) has no additional dependencies.

### 8.2 mcp_session.py (Session Management)

The Session Manager automates structured session documentation. It maintains three artifacts:

- **STATUS.md**: current project state, updated at every significant milestone. Contains: what was done, what's next, blockers. Read by the AI at session start to understand where the project is.
- **STATUS_HISTORY.md**: archive of all previous STATUS.md snapshots. Each update archives the current status before overwriting. Provides a timeline of project evolution.
- **devlog/**: directory of per-milestone entries. Each entry is a separate markdown file named `YYYY-MM-DD_NNN_slug.md` containing: summary, decisions made, problems encountered, files changed.

**Tools**:

| Tool | Parameters | What it does |
|---|---|---|
| `checkpoint` | summary, decisions, problems, files_changed, what_next, blockers | Combined: archives STATUS.md to history, writes new STATUS.md, creates devlog entry, refreshes `devlog/index.md`, reviews `CONTROLCODING.md`, and reviews the active host-derived context file |
| `update_status` | what_done, what_next, blockers, notes | Archives current STATUS.md to history, writes new STATUS.md |
| `write_devlog` | summary, decisions, problems, files_changed | Creates a new devlog entry file |
| `read_status` | (none) | Returns current STATUS.md content |
| `read_history` | last_n (default 5) | Returns last N entries from STATUS_HISTORY.md |
| `read_devlog` | last_n (default 3) | Returns content of last N devlog files |

The `checkpoint` tool is the primary interface. It should be called at every significant milestone: feature complete, bug fixed, refactor done. It combines status update, devlog creation, devlog index refresh, canonical context review, and host-context review in a single call.

**The session start ritual**: every new session should begin with `read_status()` and `read_devlog(last_n=3)`. This gives the AI full context of where the project is and what happened recently, without requiring the human to explain.

**Context review check**: `checkpoint()` reviews `CONTROLCODING.md` first and then the active derived host file (`CLAUDE.md`, `AGENTS.md`, etc.). When changed files overlap referenced project truth, it flags a review recommendation so canonical and host-native context stay aligned.

**Session counter**: the server tracks session numbers (auto-incrementing counter in `.claude/session_counter.json`). Each STATUS.md and devlog entry includes the session number, providing a chronological reference.

**Configuration**:
```json
{
  "mcpServers": {
    "session-manager": {
      "command": "python",
      "args": ["tools/mcp_session.py"],
      "env": {
        "SESSION_PROJECT_ROOT": ".",
        "SESSION_DEVLOG_DIR": "devlog",
        "SESSION_STATUS_FILE": "STATUS.md",
        "SESSION_HISTORY_FILE": "STATUS_HISTORY.md"
      }
    }
  }
}
```

### 8.3 mcp_bridge.py (Multi-Agent Communication)

The Bridge MCP server enables communication between AI sessions via a shared filesystem directory (`.bridge/`). Messages are JSON files in `.bridge/messages/`, with each session identified by an agent ID (e.g., "coder", "helper").

**Tools**:

| Tool | Parameters | What it does |
|---|---|---|
| `send` | to, content, reply_to (optional) | Creates a message file for the target agent |
| `receive` | (none) | Returns unread messages for this agent, marks them as read |
| `wait_reply` | message_id, timeout (default 120s) | Waits for a reply to a specific message. Uses file watcher for instant notification with polling fallback |
| `list_agents` | (none) | Shows all registered agents and their last-seen timestamps |
| `history` | last_n (default 10) | Returns recent bridge messages for context |

**Message format**: each message is a JSON file named `{id}_{from}_to_{to}.json` containing id, from, to, content, timestamp, status (pending/read), and optional reply_to for threaded conversations.

**Agent registration**: agents register automatically when they first use the bridge. The `agents.json` file tracks all known agents with their last-seen timestamps.

**Helper session**: a ready-to-use template (`templates/helper-session/`) provides the CLAUDE.md, setup guide, and bridge configuration for a helper agent. The helper has no project file access - it communicates exclusively through the bridge. This enforces architectural isolation: the helper cannot modify code, only advise.

**Helper role variants**:

| Variant | Purpose | When to use |
|---|---|---|
| helper (default) | General-purpose analysis, reasoning, advice | Default for most tasks |
| sentinel | Monitors bridge traffic, flags deviations from plan, architectural violations | Long autonomous sessions where the coder might drift |
| socratic | Persistent lateral thinking, challenges assumptions, proposes alternatives | Complex design decisions where premature convergence is a risk |

The **sentinel** variant is distinct from CodeWarden. CodeWarden is an automatic hook that runs at fixed points (plan time, edit time, session end) and checks diffs against CLAUDE.md rules. The sentinel is an interactive session that reads bridge messages in real time, can ask questions, and reasons about the overall direction - not just individual file changes.

The **socratic** helper variant is the persistent counterpart of the socratic consultant role (section 8.1). The consultant gives a single response per call. The socratic helper maintains a continuous dialogue, building on previous exchanges and tracking how the exploration evolves.

**Configuration profiles**: the multi-agent system supports 5 configuration profiles depending on the user's available backends:

| Profile | Coder model | Helper model | Consultant backend |
|---|---|---|---|
| Full (official CLI) | Opus (native) | Opus (CLI) | claude |
| Hybrid | Opus (native) | Opus (CLI) | ollama (local) |
| API-only | API model | API model | anthropic/openai |
| Local-only | Ollama | Ollama | ollama |
| Mixed | Native official CLI | Ollama helper | openai consultant |

The bridge protocol is tool-agnostic: any AI session with MCP support can participate. The helper does not need to be the same tool or model as the coder.

---

## 9. cc_dashboard.py (Monitoring Dashboard)

The dashboard is a read-only Gradio web interface for monitoring ControlCoding activity. It reads existing log files and artifacts - it never writes to project files.

**Tabs**:

| Tab | Data source | What it shows |
|---|---|---|
| Agents | `.bridge/agents.json` | Registered agents, last-seen timestamps, online/offline status |
| Bridge | `.bridge/messages/*.json` | Message history with filter by agent, shows from/to/status/content |
| Session | `STATUS.md`, `devlog/`, `STATUS_HISTORY.md` | Current project status, recent devlogs, status timeline |
| Debug | `.claude/consult_log.jsonl`, `.claude/codewarden_violations.jsonl`, `screenshots/` | Consultation history, violations, visual check screenshots |
| Metrics | `.claude/ops_log.jsonl`, `.claude/consult_log.jsonl` | Operations by tool, most edited files, stuck loop detection, consultation statistics |

**Stuck loop detection**: the Metrics tab flags files that were edited 5+ consecutive times in the ops log. This pattern typically indicates the AI is stuck in a debug loop and should have escalated. It also flags files with 5+ total edits in a session as potentially problematic.

**Launch**:
```bash
python tools/cc_dashboard.py --project-root . --bridge-dir .bridge --port 7860
```

**Requirements**: `pip install gradio`. The dashboard is a single Python file with no additional dependencies beyond Gradio.

---

## 10. visual_check.py (Visual Feedback Loop)

**Purpose**: Automates the Level 2 debug escalation - the AI takes control of the application to verify its own visual output.

**Usage**:
```bash
python tools/visual_check.py \
  --exe build/Release/MyApp.exe \
  --build-cmd "cmake --build build --config Release" \
  --delay 3 \
  --output screenshots/check.png
```

**The cycle**:
1. Build the project
2. Launch the executable
3. Wait for rendering (configurable delay)
4. Capture a screenshot
5. Terminate the process
6. The AI reads the screenshot (multimodal analysis)
7. Assess: does the output match expectations?
8. If not: modify code, return to step 1

**Critical requirement: visual acceptance criteria.** The screenshot is useless without defined expectations. Before implementing a visual feature, the AI must write down what the screenshot should show. This is the visual equivalent of writing tests before code.

Good acceptance criteria (specific and verifiable from a screenshot):
- "Walls should show visible light gradients (brighter near light sources, darker far away)"
- "Floor and ceiling should be visible with distinct textures"
- "At least 3 colored light pools should be distinguishable"

Bad criteria (too vague to verify from a screenshot):
- "Lighting should work"
- "The scene should look good"

**Flags**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--exe` | string | (required) | Path to the executable to launch |
| `--build-cmd` | string | None | Optional build command to run before launching |
| `--delay` | float | 3.0 | Seconds to wait before taking screenshot |
| `--output` | string | `screenshots/visual_check.png` | Screenshot output path |
| `--kill-existing` | string | None | Process name to kill before launching (e.g. `MyApp.exe`) |
| `--multi` | string | None | Comma-separated delays for multi-screenshot mode (e.g. `1,3,5`) |
| `--json` | flag | off | Write a structured JSON report alongside screenshots |

**`--multi` (multi-screenshot mode)**: captures multiple screenshots at specified delays within a single run. For example, `--multi 1,3,5` captures screenshots at 1s, 3s, and 5s after launch. Output filenames are auto-generated with delay suffixes (e.g. `check_001s.png`, `check_003s.png`, `check_005s.png`). If `--multi` has a single value, it behaves like `--delay`. When `--multi` is used together with `--delay`, the `--multi` value takes precedence (a warning is printed).

**`--json` (structured report)**: writes a JSON report next to the screenshot output path (same name with `.json` extension). The report contains: timestamp, platform, exe path, build command and result (if any), screenshot results (path, delay, size, captured status), process alive state at termination, process exit code, and any errors encountered. This is useful for automated pipelines where the AI needs structured data rather than parsing log output.

**Return type**: screenshot capture functions return a `tuple[str, bool]` where the string is an error message (empty on success) and the bool indicates success/failure.

**Platform requirements**: Windows (PowerShell), Linux (scrot), macOS (screencapture). No additional Python packages.

---

## 10b. visual_check_utils.py (Shared Visual Utilities)

**Purpose**: Shared utility module used by both `visual_check.py` and `visual_test.py`. Contains platform-specific screenshot capture, process lifecycle management, build helpers, JSON report generation, and path utilities.

**Functions**:

| Function | Signature | Description |
|---|---|---|
| `take_screenshot` | `(output_path: str) -> tuple[str, bool]` | Platform-appropriate screenshot capture. Dispatches to Windows (PowerShell), Linux (scrot), or macOS (screencapture). Returns `("", True)` on success or `(error_message, False)` on failure |
| `take_screenshot_windows` | `(output_path: str) -> tuple[str, bool]` | Windows screenshot via PowerShell `System.Drawing` API. Captures the primary screen |
| `take_screenshot_linux` | `(output_path: str) -> tuple[str, bool]` | Linux screenshot via `scrot` command |
| `take_screenshot_macos` | `(output_path: str) -> tuple[str, bool]` | macOS screenshot via `screencapture -x` (silent mode) |
| `parse_multi_delays` | `(value: str) -> list[float]` | Parses comma-separated delay string into a sorted list of positive floats. Raises `ValueError` on negative or non-numeric values |
| `generate_multi_paths` | `(base_output: str, delays: list[float]) -> list[str]` | Generates output paths with delay suffixes for multi-screenshot mode. Example: `("check.png", [1,3,5])` produces `["check_001s.png", "check_003s.png", "check_005s.png"]` |
| `build_json_report` | `(*, exe, build_cmd, build_success, build_duration_ms, screenshots, process_alive_at_end, process_exit_code, errors) -> dict` | Builds the structured JSON report dictionary. All parameters are keyword-only |
| `write_json_report` | `(report: dict, base_output: str) -> str` | Writes report dict to a JSON file next to the base output path (same name, `.json` extension). Returns the written file path |
| `run_build` | `(build_cmd: str) -> tuple[bool, float]` | Runs a build command with `shell=True`. Returns `(success, duration_ms)` |
| `launch_process` | `(exe_path: str) -> subprocess.Popen` | Launches an executable with `cwd` set to its parent directory. Uses process groups on Unix for clean termination |
| `kill_process` | `(proc: subprocess.Popen) -> int | None` | Terminates a process tree. Uses `taskkill /F /T` on Windows, `os.killpg` with SIGTERM/SIGKILL on Unix. Returns exit code or None. Waits up to 5s before force-killing |
| `find_window_by_pid` | `(pid: int) -> int | None` | Find the main visible window handle (HWND) for a process PID. Windows only via ctypes; returns None on other platforms or if no window found |
| `get_window_rect` | `(hwnd: int) -> tuple[int, int, int, int] | None` | Get window bounding rectangle (left, top, right, bottom). Windows only; returns None on failure |
| `take_screenshot_window` | `(pid: int, output_path: str) -> tuple[str, bool]` | Capture screenshot of a specific window by PID. Uses Pillow `ImageGrab.grab(bbox=...)` when available, otherwise PowerShell region capture. Falls back to full-screen if window not found |
| `compare_screenshots` | `(path1: str, path2: str, threshold: float) -> tuple[bool, float]` | Compare two screenshots for visual difference. Returns `(changed, difference_ratio)`. Uses Pillow grayscale resize + pixel diff; falls back to byte comparison if Pillow unavailable. Default threshold: 0.02 |
| `run_output_test` | `(cmd: str, expected: str | None, timeout: float) -> tuple[bool, str]` | Run a shell command and check output. Returns `(success, actual_output)`. If `expected` is given, checks it appears in stdout+stderr. Default timeout: 30s |
| `run_http_test` | `(url: str, expected_status: int, expected_contains: str | None, timeout: float) -> tuple[bool, str]` | HTTP GET test via urllib. Returns `(success, response_body)`. Default expected status: 200, timeout: 10s |
| `run_file_test` | `(path: str, expected_exists: bool, expected_contains: str | None) -> tuple[bool, str]` | Check file existence and optional content match. Returns `(success, detail_message)` |

**Dependencies**: Python stdlib only (+ optional Pillow for window capture and screenshot comparison). Window capture and comparison degrade gracefully without Pillow.

---

## 10c. visual_test.py (Interactive Visual Testing)

**Purpose**: Interactive visual testing with simulated keyboard and mouse input. Builds, launches, and interacts with an application via a generic action system, capturing screenshots at specified points. This is the Level 2+ debug escalation tool for applications that require user interaction to reach the state being tested.

**Usage**:
```bash
# Inline actions
python tools/visual_test.py \
  --exe build/Release/MyApp.exe \
  --actions "wait:3,screenshot:init.png,key:w,wait:1,screenshot:after_move.png"

# Actions from JSON file
python tools/visual_test.py \
  --exe build/Release/MyApp.exe \
  --actions-file test_sequence.json \
  --json
```

**Flags**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--exe` | string | (required) | Path to the executable to launch |
| `--build-cmd` | string | None | Build command to run before launching |
| `--actions` | string | None | Comma-separated inline actions (mutually exclusive with `--actions-file`) |
| `--actions-file` | string | None | Path to a JSON file containing the action sequence (mutually exclusive with `--actions`) |
| `--output-dir` | string | `screenshots` | Directory for screenshot output |
| `--delay` | float | 3.0 | Initial delay (seconds) after launch before executing actions |
| `--kill-existing` | string | None | Process name to kill before launching |
| `--window-title` | string | None | Window title fragment to focus before actions (defaults to exe stem) |
| `--json` | flag | off | Write a structured JSON report to `<output-dir>/visual_test.json` |

| `--auto-screenshot-after-action` | flag | off | Automatically take a screenshot after every non-screenshot action. Files named `auto_NNN_actionname.png` |
| `--timeout` | float | 300 | Maximum seconds for the action sequence (0 = unlimited). On timeout: diagnostic screenshot, remaining actions marked "skipped: timeout" |

**Action system**: actions are specified as `action:param` pairs, either inline (comma-separated) or in a JSON file. Action names are aligned with the Claude Computer Use Tool vocabulary.

| Action | Param | Description |
|---|---|---|
| `wait` | seconds (default 1.0) | Sleep for the specified duration |
| `screenshot` | filename (default `screenshot.png`) | Capture screenshot, saved to `--output-dir` |
| `key` | key name (e.g. `w`, `space`, `enter`) | Press a single key via pyautogui |
| `keys` | key combo (e.g. `ctrl+s`, `alt+f4`) | Press a key combination (hotkey) via pyautogui |
| `type` | text string | Type text character by character (0.02s interval) |
| `left_click` | `x,y` or empty | Left-click at absolute coordinates, or at current position if empty |
| `left_click_relative` | `dx,dy` | Left-click relative to screen center |
| `right_click` | `x,y` or empty | Right-click at coordinates or current position |
| `middle_click` | `x,y` or empty | Middle-click at coordinates or current position |
| `mouse_move` | `x,y` | Move cursor to absolute coordinates |
| `mouse_move_relative` | `dx,dy` | Move cursor relative to current position |
| `left_click_drag` | `x,y` | Drag from current position to target coordinates (0.5s duration) |
| `scroll` | amount (integer) | Scroll wheel by the specified amount (positive = up, negative = down) |
| `focus` | title fragment | Bring window with matching title to foreground (Windows only) |
| `compare` | `before.png,after.png` | Compare two screenshots in output-dir. Returns success if images differ (action had visible effect), fails if identical |
| `output_test` | `cmd` or `cmd|expected` | Run a shell command. If `expected` given, checks it appears in stdout+stderr. Otherwise checks exit code 0 |
| `http_test` | `url` or `url|status` or `url|status|contains` | HTTP GET test. Default expected status: 200 |
| `file_test` | `path` or `path|expected_content` | Check file exists and optionally contains expected string |

**Window-only capture**: when visual_test.py has a running process, screenshots automatically capture only that application's window (via PID lookup) instead of the full desktop. This prevents false positives from other windows. On Windows, uses ctypes + Pillow ImageGrab; falls back to PowerShell region capture or full-screen on other platforms.

**Screenshot comparison**: the `compare` action enables before/after verification. Take a screenshot, perform an action, take another screenshot, then compare. If images are identical (below the threshold), the action had no visible effect and the compare reports failure.

**JSON actions file format** (with optional `expect` field):
```json
[
  {"action": "wait", "param": "3"},
  {"action": "screenshot", "param": "initial.png",
   "expect": "3D scene with stone walls, torch lighting, health bar top-left"},
  {"action": "key", "param": "w"},
  {"action": "wait", "param": "1"},
  {"action": "screenshot", "param": "after_move.png",
   "expect": "Camera moved forward - walls should be closer than initial.png"}
]
```

The `expect` field is optional. When present, it is included verbatim in the JSON report and printed in the structured output alongside each screenshot. The AI compares each screenshot against its expectation during analysis.

**Verification Strategy format**: the actions file also accepts entries from the Concierge's Verification Strategy Generator. Entries with a `method` field are auto-converted to the appropriate action:

```json
[
  {"method": "visual", "action": "screenshot", "param": "ui.png",
   "expect": "Login form with email and password fields"},
  {"method": "command", "cmd": "echo hello", "expected": "hello"},
  {"method": "http", "url": "http://localhost:3000/api/health",
   "expected_status": 200, "expected_contains": "ok"},
  {"method": "file", "path": "output/result.txt",
   "expected_contains": "success"},
  {"method": "test", "cmd": "pytest tests/test_api.py"}
]
```

Method mapping: `visual` -> `screenshot`, `command`/`test` -> `output_test`, `http` -> `http_test`, `file` -> `file_test`. Direct action format and verification strategy entries can be mixed in the same file.

**Structured output**: after all actions execute, prints a `[L2+]` formatted summary listing all screenshots with sizes, timing, and expectations. Includes analysis instructions for the AI.

**Platform warnings**: at startup, detects and warns about: macOS Screen Recording permission, Windows DPI scaling > 100%, Linux Wayland session (pyautogui requires X11).

**JSON report**: when `--json` is used, the report is written to `<output-dir>/visual_test.json` and includes: timestamp, platform, exe, build info, process state, all action results (action, param, result, success, and optional expect), total action count, screenshot count, auto-screenshot entries, and errors.

**Dependencies**: `pip install pyautogui` (required for input actions like key, click, type). Screenshot capture uses stdlib only (via `visual_check_utils.py`). Optional: Pillow for window-only capture and screenshot comparison. The `output_test`, `http_test`, `file_test`, and `compare` actions use stdlib only and do not require pyautogui.

---

## 10d. verification_agent.py (Verification Orchestrator)

**Purpose**: Tracks verification state for design-document compliance. The AI extracts criteria from the design doc, verifies each one, and the script provides the mechanical infrastructure: state persistence, dependency DAG, budget enforcement, anti-compression safeguards.

**Key concept**: this is a D-script (mechanical orchestrator), not an autonomous agent. The AI provides intelligence (criteria extraction, visual analysis, diagnosis). The script provides mechanics (state, budget, DAG, reports). Functions like "extract criteria" do not exist in the code - the AI does this mentally and calls `add-criterion` for each one.

**Install**: `python scripts/cc.py install vision`

**Subcommands**:

```bash
# Register a criterion (AI calls this for each requirement found in design doc)
python tools/verification_agent.py add-criterion \
  --id C-01 --text "A sword with a visible blade and hilt" \
  --method visual --layer 1 --section "## Weapons" \
  --blocks C-04,C-05 --steps "take screenshot; check blade+hilt visible"

# Update status after verification (evidence required for pass, notes for fail)
python tools/verification_agent.py update \
  --id C-01 --status pass --evidence screenshots/sword.png

python tools/verification_agent.py update \
  --id C-02 --status fail --notes "button missing in top-left area"

# Get next criterion to work on (DAG-aware, functional before visual)
python tools/verification_agent.py next --layer 1

# List passed criteria for regression re-verification
python tools/verification_agent.py check-regression

# Check convergence status and budget
python tools/verification_agent.py convergence-status \
  --max-attempts 3 --max-iterations 100 --time-budget 60

# Generate JSON + markdown reports
python tools/verification_agent.py report \
  --output devlog/verification_report.json --md-output devlog/verification.md
```

**Criterion fields**: id, original_text, original_text_hash (SHA256), source_section, verification_method (visual/functional/code/numerical), verification_steps, status (pending/pass/fail/blocked), attempts, evidence, reference_screenshot, notes, layer (1=primitive, 2=structure, 3=polish, 0=no layers), blocks (dependency list).

**Anti-compression safeguards**:
1. SHA256 hash on original_text - drift detected on every load (exits with code 2)
2. Evidence mandatory for pass - "it looks correct" is not evidence
3. Notes mandatory for fail/blocked - must reference specific code locations
4. Original text appears in all reports, never compressed

**DAG resolution**: if criterion A (status=fail) lists B in its `blocks` field, B is auto-BLOCKED. The `next` command skips blocked criteria and prioritizes functional methods before visual within the same layer.

**Two operating modes**:
- **Build mode**: AI implements one criterion at a time, verifies immediately after each. Uses `next --layer N` to drive development. Recommended for visual projects.
- **Audit mode**: AI builds the project first, then batch-verifies all criteria. Uses `report` for pass/fail summary. Recommended for backend projects.

**Layer system**: Layer 1 (primitives - boxes, basic shapes), Layer 2 (structure - proportions, hierarchy), Layer 3 (polish - textures, animations). Use `--layer 0` or omit to disable layers for non-visual projects.

**Dependencies**: Python stdlib only.

---

## 10e. verification_report.py (Report Generation)

**Purpose**: Generates JSON and markdown reports from criteria data.

**Functions**:

| Function | Description |
|---|---|
| `generate_json_report(criteria, design)` | Builds structured JSON with timestamp, pass/fail/blocked counts, pass_rate, layers_completed, and full criterion details |
| `generate_markdown_report(report)` | Produces human-readable markdown with Passed/Failed/Blocked/Pending sections, each showing original text, evidence, and diagnosis |

**JSON report fields**: timestamp, design_document, total_criteria, passed, failed, blocked, pending, pass_rate, layers_completed, criteria (array with id, original_text, source_section, method, status, attempts, layer, evidence, reference_screenshot, notes, blocks).

**Dependencies**: Python stdlib only.

---

## 10f. mcp_vision.py (Vision Agent MCP Server)

**Purpose**: Exposes verification skills as MCP tools that other AI sessions can call programmatically. Thin wrapper around verification_agent.py - no business logic.

**Install**: `python scripts/cc.py install vision` (copies files + adds MCP config to settings.json)

**MCP tools**:

| Tool | Description |
|---|---|
| `vision_add_criterion` | Register a new verification criterion with verbatim text, method, layer, dependencies |
| `vision_update` | Update criterion status (pass/fail/blocked) with evidence or notes |
| `vision_next` | Get the next criterion to implement (DAG-aware priority, functional before visual) |
| `vision_check_regression` | List all passed criteria that should be re-verified |
| `vision_report` | Generate JSON + markdown verification reports |
| `vision_convergence_status` | Check convergence loop status and budget utilization |

**Build-mode workflow** (visual projects - implement one criterion at a time):
```
1. AI reads design doc, extracts criteria -> vision_add_criterion (for each)
2. AI asks "what next?" -> vision_next --layer 1
3. AI implements ONLY that criterion
4. AI verifies -> vision_update --status pass/fail
5. AI checks regression -> vision_check_regression
6. Repeat from step 2 until layer complete
7. Advance to next layer
```

**Audit-mode workflow** (backend projects - verify after build):
```
1. AI builds the project
2. AI extracts all criteria -> vision_add_criterion (for each)
3. AI verifies all -> vision_update (for each)
4. AI generates report -> vision_report
5. AI fixes failures, re-verifies
```

**Dependencies**: fastmcp (consistent with other CC MCP servers).

---

## 11. consult.py (CLI Fallback for Consultation)

**Purpose**: CLI fallback for environments without MCP support. Provides the same consultation functionality as mcp_consultant.py but callable via Bash.

**No additional dependencies** beyond Python stdlib.

`--backend` is required. The runtime gate may allow or deny the requested
route, but an unknown backend returned by that gate is rejected before any
provider adapter runs.

```bash
python tools/consult.py --backend ollama --prompt "Describe the isolated problem and relevant evidence."
```

---

## 12. Agentic Workflow Patterns

### 12.1 Autonomous cycles with guardrails

The central pattern of agentic work:

```
repeat:
  1. Identify the next task
  2. Plan the changes (plan mode)
  3. Implement
  4. Run tests
  5. If tests fail: analyze error, fix, go to 4
  6. If tests pass: commit, next task
until: all tasks completed
```

ControlCoding makes this cycle robust through mechanical enforcement:

- PreToolUse hooks fire on every single call to Edit/Write/Bash, not just at commit time. The agent cannot accumulate out-of-perimeter modifications during the correction cycle.
- The feature.lock confines the blast radius: if the agent enters a correction loop (fix A breaks B, fix B breaks C), the damage stays within the current feature.
- Retry limit: if after 3 correction attempts the same test continues to fail, the agent must stop and report. This should be specified in CLAUDE.md (e.g. "max_correction_attempts: 3").

Critical case - oscillating fix: when fixing test A breaks test B and vice versa, the problem is not in the cycle but in the design. It almost always indicates hidden coupling between components that should be separated.

### 12.2 Debug protocol: hypothesis before parameter tweaking

A recurring failure pattern: when a feature doesn't work, the AI adjusts parameters without verifying the root cause. The modification compiles, tests pass, but the underlying problem remains masked.

**The debug protocol** (include in CLAUDE.md as operative rule):

```
When a feature doesn't produce the expected visual or runtime result:
1. VERIFY the component exists (render with debug color, add log output)
2. VERIFY data flow (check values at each stage of the pipeline)
3. ONLY THEN adjust parameters
Never skip to parameter tweaking without confirming the hypothesis.
```

**Structured debug sequence for common categories:**

For **rendering issues**: set object to bright solid color -> confirm geometry exists -> check normals -> check textures -> check lighting values. If bright color is invisible, the problem is geometry or culling, not lighting.

For **logic issues**: add logging at input/output boundaries of the suspected function -> confirm values flow correctly -> check edge cases. If input is correct but output is wrong, the bug is in the function. If input is already wrong, trace upstream.

For **integration issues** (system A doesn't affect system B): verify the wiring call exists -> verify it's called at the right time -> verify the data format matches. If the call exists but has no effect, the interface contract is broken.

The common thread: **observe before modifying**. Each debug step should produce diagnostic output that either confirms or eliminates one hypothesis.

### 12.3 Debug escalation: three levels

```
Bug reported or observed
        |
        v
   L1: Debug Protocol
   Hypothesis -> verify -> isolate
        |
        | (visual issue, or L1 inconclusive)
        v
   L2: Visual Feedback Loop (visual_check.py)
   Build -> launch -> screenshot -> analyze -> iterate
        |
        | (stuck after 2-3 visual iterations)
        v
   L3: External Consultation (mcp_consultant.py / consult.py)
   Prepare data package -> consult -> apply suggestions
        |
        | (still stuck)
        v
   Human intervention required
```

**Escalation triggers** (advisory, include in CLAUDE.md):
```
Debug Escalation:
- L1 (Protocol): always. Follow the debug protocol
- L2 (Visual): if the issue is visual and L1 did not resolve it, use visual_check.py
- L3 (Consult): if you have attempted 3+ fixes for the same issue without
  improvement, use consult.py to get an external opinion
- Never skip levels: L1 must be attempted before L2, L2 before L3
```

### 12.4 Sub-agent coordination

Current public status: sub-agent work is host-mediated and explicit. `cc agents`
shows persistent agent-memory status only; it does not start or route agent
execution. Gateway runtime agent execution through `agent_runner.py` remains a
deferred internal path for the current scope.

When to use sub-agents or helper sessions:
- Parallel codebase search
- Implementation of independent features on separate modules
- Impact analysis on multiple components simultaneously
- Build + test in background while working on something else

Rules for sub-agents in ControlCoding:
1. Every sub-agent inherits the project's guardrails (hooks, CLAUDE.md)
2. Different sub-agents must not touch the same files
3. Each sub-agent's result must pass invariant tests independently
4. The orchestrator validates global coherence after sub-agents complete

**Background agents are read-only**: Only the main agent writes code. This prevents write conflicts and maintains coherence.

### 12.5 Delegation boundary: facts vs decisions

When delegating to sub-agents:

**Accept without filtering** (factual data): code inventories, pattern searches, measurements.

**Filter before presenting** (recommendations): architectural suggestions, design proposals, priority assessments.

Every sub-agent recommendation must be verified against CLAUDE.md and the project's architectural principles before presenting it to the user. Sub-agents reason in the abstract, without project context. A recommendation valid in general may violate specific constraints.

Summary: **sub-agent = data collection, main agent = architectural decisions**. Never relay sub-agent recommendations without filtering.

---

## 13. External Connection Control

AI-generated code can introduce unauthorized external connections - HTTP calls, subprocess spawning, telemetry sockets - that activate without explicit human authorization.

### The 6 principles

**P1 - Explicit authorization for external connections.** Every connection to an LLM, API, or external service must be explicitly authorized by the user (interactive) or pre-configured (auto mode). Never as a side-effect of an import, a default value, or a silent fallback.

**P2 - Default-OFF (fail-closed).** All settings that enable external connections must default to OFF. The user must opt-in consciously.

```python
# Correct: default is no connection
scorer_backend: ScorerBackend = ScorerBackend.MOCK
enable_consultant: bool = False
rag_enabled: bool = False
```

**P3 - Three operational states.** Every system that makes external calls must handle three states explicitly:

```python
if settings.auto_mode:
    # (1) AUTO: pre-authorized unattended execution
    if not call_counter.can_call():
        return safe_fallback  # limit reached, stop
    return configured_backend

if not sys.stdin.isatty():
    # (2) NON-INTERACTIVE without auto: nobody can respond
    return safe_fallback

# (3) INTERACTIVE: a human is at the terminal
# Ask confirmation with clear banner + model choice
```

Auto mode does not mean "do whatever you want". It means "proceed within the limits I set before leaving": `auto_max_calls=20`, `auto_model=opus`, thread-safe `CallCounter` tracking every real call.

**P4 - Cost transparency.** Before every connection, declare: what it will do, which model, how many estimated calls. Even when monetary cost is zero.

**P5 - No cost-multiplication fallbacks.** `cost_fallback <= cost_original` or ask for explicit confirmation.

```python
# WRONG: silent cost multiplication
except Exception:
    for item in items:
        result = self.evaluate(item)  # N subprocesses

# CORRECT: visible failure
except Exception as e:
    raise RuntimeError(
        f"Batch operation failed after {retries} attempts: {e}"
    ) from e
```

**P6 - Visible failure.** If an operation fails, propagate the error with `raise`. Never mask it with a silent alternative.

### Telemetry protection

Third-party libraries can open sockets at import time (e.g., chromadb, posthog). Block this before the import:

```python
import os
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("POSTHOG_DISABLED", "1")
import chromadb  # no socket opened
```

### How CC tools implement these principles

`mcp_consultant.py` is the reference implementation:

| Principle | Implementation |
|-----------|---------------|
| P1 (explicit auth) | Tool only runs when AI explicitly calls `consult()` |
| P2 (default OFF) | Server only starts when configured in settings.json |
| P3 (operational states) | `CONSULT_MAX_CALLS` enforces limits in unattended mode |
| P4 (cost transparency) | Response includes `[Consultation N/MAX]`, logs track call count |
| P5 (no cost multiplication) | `_CallCounter` blocks calls beyond the limit |
| P6 (visible failure) | Errors returned as clear strings, never masked with fallbacks |

### Public release profile

For public-facing ControlCoding documentation and defaults:

- use only official CLIs, official APIs, local runtimes, or vendor-approved connectors
- do not offer vendor consumer login inside a CC-owned interface
- keep a single user-facing chat; on public official-host paths, that chat is
  the chosen User Host, while Concierge-shaped routing remains an internal or
  later optional surface
- keep the current public release centered on the chosen official host
- if a visualizer mirrors Concierge traffic on an official-CLI path, label each
  routed event with its origin (`User Host`, `CC UI`, `CC Structure`,
  `Worker`, or `System`)
- require manual approval for external specialist calls in interactive mode
- show backend, model, cost/call budget, and write-permission scope before each external specialist call
- on `Agents` and any later API/UI paths, persist a visible specialist/backend consent matrix in `.controlcoding/cc_engagement.json`
- allow auto mode only when it is pre-authorized, bounded, and transparent
- do not allow recursive external agent chains without returning to the human or the Concierge

### Checklist for new projects

When a project makes external connections (LLM calls, API requests, subprocess spawning):

- [ ] All connection settings default to OFF?
- [ ] Every connection point has opt-in (env var, config, or interactive prompt)?
- [ ] The system handles the 3 states (interactive / non-interactive / auto)?
- [ ] All non-local LLM paths use official CLIs or official APIs only?
- [ ] Fallbacks have cost <= the original operation?
- [ ] Third-party imports are protected from silent telemetry?
- [ ] Auto mode has configurable limits (max calls, model, timeout)?
- [ ] Connection errors are propagated, not masked with silent alternatives?

---

## 14. Adaptation for Specific Tools

### 14.1 Claude Code (CLI / VS Code Extension)

| ControlCoding Concept | Claude Code Implementation |
|---|---|
| CLAUDE.md (constitution) | `CLAUDE.md` file in the project root (read natively) |
| Plan mode | `plan` command or automatic trigger on complex tasks |
| Boundary enforcement | `PreToolUse` hook on Edit/Write |
| Dangerous commands | `PreToolUse` hook on Bash |
| Final invariant tests | `Stop` hook |
| Sub-agents | Task tool with `subagent_type` |
| Feature.lock | YAML file read by the Python hook |

Setup:
```
.claude/
  settings.json          # Hooks configuration
  CLAUDE.md             # Or in the project root
hooks/
  check_boundaries.py    # Boundary enforcement (DENY/WARN zones)
  check_dangerous_commands.py  # Dangerous command blocking
  check_workflow.py      # Workflow enforcement (plan before code, etc.)
  ops_logger.py          # Operation logging (PostToolUse)
  session_end_check.py   # DENY integrity verification (SessionEnd)
tools/
  mcp_consultant.py      # External consultation (L3 debug escalation)
  mcp_session.py         # Session management (checkpoint, devlog, status)
  visual_check.py        # Visual verification (L2 debug escalation)
```

### 14.2 Cursor

| ControlCoding Concept | Cursor Implementation |
|---|---|
| CLAUDE.md | `.cursorrules` in the project root |
| Plan mode | Agent mode with instructions |
| Boundary enforcement | `.cursorignore` for protected files + instructions in `.cursorrules` |
| Invariant tests | Git pre-commit hooks |
| Feature.lock | Referenced in `.cursorrules` |

Difference from Claude Code: Cursor does not have executable hooks with exit codes. Boundary enforcement is advisory (the agent reads `.cursorrules` and follows them, but is not mechanically blocked). To compensate, the git pre-commit hook becomes the only mechanical line of defense.

Minimal setup:
```
# .cursorrules (in the project root)
DO NOT modify files in stable/. If you must, stop and ask for confirmation.
Before every commit, run: npm test -- --grep "invariant"
Read feature.lock before modifying files inside features/.
```

```
# .cursorignore (files protected from direct modification)
src/stable/**
```

Limitation: `.cursorignore` prevents Cursor from seeing files (it doesn't show them in context), not from writing to them. If the agent reconstructs the path manually, it can still write. The pre-commit hook remains essential.

### 14.3 Aider

| ControlCoding Concept | Aider Implementation |
|---|---|
| CLAUDE.md | `.aider.conf.yml` + `CONVENTIONS.md` |
| Boundary enforcement | `.aiderignore` for protected files |
| Invariant tests | `--test-cmd` for automatic tests after every modification |
| Feature.lock | Instructions in the project prompt |

Aider has a specific advantage: the `--test-cmd` flag runs tests after every modification and, if they fail, automatically asks the agent to fix. This natively implements the autonomous cycle with guardrails without additional configuration.

```bash
# Launch with automatic invariant tests
aider --test-cmd "pytest tests/invariants/ -x" --auto-test
```

### 14.4 Kiro (Spec-Driven Development)

Kiro is the tool most naturally aligned with ControlCoding because its workflow starts from specs, not code:

| ControlCoding Concept | Kiro Implementation |
|---|---|
| ChangeSpec | Native spec files (requirements -> design -> tasks) |
| Plan mode | Integrated spec-first workflow |
| Invariants | Acceptance criteria in specs + property-based testing |
| Feature.lock | Not natively present (add via hook) |

Recommended integration: use Kiro's acceptance criteria to codify domain invariants. Where Kiro generates functional tests ("the function returns the correct result"), manually add invariant tests ("the total sum doesn't change", "no NaN in the result"). Kiro doesn't generate them on its own because it doesn't know the physical properties of the domain.

### 14.5 Tool-agnostic (pre-commit hooks)

For any AI tool, git pre-commit hooks are the **bare minimum** of enforcement:

```bash
#!/bin/bash
# .git/hooks/pre-commit

# Gate 1: Build
echo "Gate 1: Building..."
npm run build || exit 1

# Gate 2: Invariants
echo "Gate 2: Running invariant tests..."
npm test -- --grep "invariant" || exit 1

# Gate 3: Performance (warning, non-blocking)
echo "Gate 3: Performance checks..."
npm test -- --grep "perf" || echo "WARNING: Performance test failed"
```

---

## 15. Migration from v1 to v2

### 15.1 What to eliminate

- RenameSpec.yaml: no longer necessary. Modern models don't rename without request.
- Separate AI sessions: native plan mode replaces the Planner/Surgeon separation.
- Detailed "for AI" instructions (section E of v1 document, 2024): replaced by CLAUDE.md.

### 15.2 What to transform

- CODEMAP.md + CONTRACTS.md + METRICS.md: unified in CLAUDE.md (operative summary)
- FUNCTION_REGISTRY.yml: lightweight version (only non-obvious functions)
- feature.lock: same format, add hook for mechanical enforcement
- ChangeSpec.yaml: simplified format, used as documentation not process

### 15.3 What to add

- hooks/: directory with enforcement scripts
- .claude/settings.json: hooks configuration (or equivalent for your tool)
- CI pipeline: 3 gates (build, invariants, performance)
- Invariant tests: if they don't exist, create them immediately for key domain metrics

### 15.4 Recommended order

1. Write CLAUDE.md (30 minutes)
2. Create boundary check hook for stable/ (1 hour)
3. Migrate METRICS.md into the CLAUDE.md section (15 minutes)
4. Simplify FUNCTION_REGISTRY (30 minutes)
5. Eliminate RenameSpec from workflow (immediate)
6. Configure pre-commit hook for invariants (1 hour)

---

## Further Reading

- [Methodology](methodology.md) - conceptual framework and architecture
- [Hooks Reference](hooks-reference.md) - hook configuration and behavior
- [Cookbook](cookbook.md) - worked examples by domain
