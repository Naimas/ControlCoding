# ControlCoding v2 - Methodology

> Conceptual framework and architectural philosophy.
> For hands-on setup, see [Install ControlCoding On Your Project](../install-controlcoding-on-your-project.md).

---

## 1 Introduction

### 1.1 What is Control Coding

Control Coding is a software development paradigm that balances the creative speed of AI with the quality and robustness of enterprise code. The word "Control" means maintaining spontaneity and creative flow, but with **invisible guardrails** that prevent systemic errors.

The founding metaphor: like a jazz musician who improvises freely within a defined harmonic structure, the developer and the AI can explore creative solutions rapidly, but always within safe and coherent architectural boundaries.

### 1.2 Why v2

ControlCoding v1 (2024) was written for AI models that:
- Forgot context after a few iterations
- Renamed symbols randomly
- Duplicated existing functions
- Mixed planning and implementation
- Did not respect architectural boundaries without explicit enforcement

In 2026 the landscape has changed:

| v1 Problem | v1 Solution | 2026 Status |
|---|---|---|
| AI forgets context | CODEMAP, REGISTRY, CONTRACTS | Models with 200K+ tokens. External memory useful but less critical |
| AI exceeds scope | feature.lock allow/deny | Models much more disciplined. Hooks as mechanical safety net |
| AI renames randomly | RenameSpec.yaml mandatory | Opus 4.6 is conservative. RenameSpec no longer necessary |
| AI duplicates functions | Detailed FUNCTION_REGISTRY | AI searches the codebase before creating. Lightweight registry is enough |
| Planning mixed with implementation | Planner/Surgeon in separate sessions | Native plan mode in Claude Code and Cursor |
| Enforcement based on "AI must read the docs" | Advisory documents | Executable hooks: the AI cannot physically violate boundaries |
| AI introduces non-determinism | Explicit rules + tests | Models are more aware of the problem, but tests remain essential |

The guiding principle of v2: less process, more automation. Architectural principles remain; procedural scaffolding shrinks and becomes mechanical.

### 1.3 What changes from v1 to v2

| Component | v1 | v2 |
|---|---|---|
| Planner / Surgeon | Two separate AI sessions | **Native plan mode** (same session) |
| feature.lock | Advisory YAML document | **Executable hooks** (mechanical enforcement) |
| CODEMAP + CONTRACTS + METRICS | 3 separate files | **Unified CLAUDE.md** (read natively) |
| FUNCTION_REGISTRY | Detailed catalog of every function | **Lightweight registry** (only non-obvious functions) |
| RenameSpec.yaml | Mandatory for every rename | **Eliminated** (modern AI is conservative) |
| ChangeSpec.yaml | Handoff between Planner and Surgeon | **Documentation artifact** (not process) |
| Enforcement | AI "must read and follow" | **Hooks + CI** (mechanical, inevitable) |
| Multi-agent | Not planned | **Agentic orchestration** (parallel tasks) |

### 1.4 Note on convergences

Those familiar with software engineering will notice parallels between ControlCoding and prior work: the inward dependency rule recalls Robert C. Martin's Clean Architecture (2012), module boundaries recall Bounded Contexts from Domain-Driven Design (Evans, 2003), invariant tests are a form of Design by Contract (Meyer, 1986) and property-based testing (QuickCheck, Claessen & Hughes, 2000).

ControlCoding applies established software-engineering practices to
AI-assisted development. Similarities to earlier frameworks provide context;
they do not, by themselves, establish originality or effectiveness. The
[evidence report](../evidence.md) describes the available project observations
and their limitations.

The difference from academic frameworks is the context: ControlCoding is designed for an environment where the entity writing code is an AI agent that has no implicit domain knowledge and doesn't "know" that certain things shouldn't be touched until a mechanical constraint prevents it.

### 1.5 What remains unchanged

The fundamental principles of ControlCoding are **technology-independent** and remain the core of the system:

1. **"Condominium" architecture**: modular isolation with protection levels
2. **Model vs View**: separation of authoritative data from presentation data
3. **One Concept, One Place**: conceptual cohesion before file length
4. **Domain invariants**: automated tests on the fundamental properties of the system
5. **Promotion path**: workspace > features > shared > stable
6. **Determinism and coherence**: reproducibility of results
7. **Event Bus & Parameter Registry**: decoupled communication

---

## 2 The Software Condominium Architecture

### 2.1 The four levels

```
project/
  stable/              # Foundations. Untouchable without extraordinary review.
  shared/              # Reusable utilities. Lightweight registry.
  features/            # Isolated apartments. Each feature = capsule.
  workspace/           # Experimental garage. Free sandbox.
```

**stable/**: immutable foundations. Contains the base components, core interfaces, and invariant laws of the system. The AI cannot touch anything in stable/. Solid foundations allow for free experimentation on the upper floors.

**shared/**: common utilities. Functions and algorithms reusable by all features. Each tool is documented in the registry. The AI must reuse these utilities rather than duplicate.

**features/**: isolated apartments. Each feature lives in its own module with a single entry point: the public API. Behind that door, the AI can completely restructure the interior as long as the public interface remains consistent.

**workspace/**: experimental garage. Space for experiments and prototypes. The AI can "get its hands dirty" without fear. If a prototype succeeds, it is promoted gradually.

### 2.2 Feature structure

```
features/<FeatureName>/
  include/<Project>/<Feature>/<Feature>.h   # SINGLE public API (thin)
  src/
    systems/         # Cohesive sub-systems (pattern "System as Directory")
    components/      # Internal data structures
    algorithms/      # Feature-specific algorithms
  tests/
    unit/            # Functional tests
    invariants/      # Domain invariant tests
  feature.lock       # Perimeter and metrics (planned for v2.2, currently via cc_config.json + hooks)
```

> **Adaptation note**: The structure above is a model for C++ projects with separate headers. For other languages, adapt the structure while maintaining the principle: one clear public API, free internal implementation, tests with domain invariants, and boundary enforcement via `.claude/cc_config.json` + hooks (or `feature.lock` at L4, planned for v2.2).

### 2.3 Isolation as defense in depth

The condominium architecture serves as a multi-level defense system:

1. Immediate level: hooks prevent unauthorized modifications (mechanical)
2. Commit level: invariant tests catch domain violations
3. Review level: the ChangeSpec forces reflection before implementation
4. Structural level: the code structure itself makes certain errors impossible

The AI can "repaint the walls and rearrange the furniture" in its module, but cannot touch common plumbing or break the floor of the apartment below.

---

## 3 Architectural Principles

### 3.1 One Concept, One Place

Each concept or feature resides in a **single place** in the codebase. Conceptual cohesion takes precedence over arbitrary metrics like line count.

**When to keep together** (even if long):
- Monolithic algorithm with strong internal coupling
- Locality/performance benefit
- A single logical responsibility

**When to split** (System as Directory):
- Multiple distinct responsibilities
- Parts grow independently
- Different interfaces emerge
- Different actors (human/AI) work in parallel

There is no "magic line limit": what counts is cohesion and single responsibility.

### 3.2 Model vs View (separating authoritative data from presentation)

Clearly separate **authoritative data** (source of truth) from **presentation data** (derived views).

This principle applies to any system with dual representation:

- **Scientific simulation**: The simulation model is the truth; the visualization is a projection
- **Backend to Frontend**: The database is the truth; the UI is a derived view
- **Domain model to API response**: The domain model is the truth; the response is a serialized view
- **State store to Render state**: The application state is the truth; the render is a view
- **MVVM**: The Model is the truth; the ViewModel and View are derivations

The rule: every calculation, validation, and mutation happens on the source of truth. Presentation data is derived, never edited directly. If a user modifies something in the view, the modification must transit through the authoritative model and then propagate back to the view.

### 3.3 Event Bus & Parameter Registry

> **Note**: These are architectural patterns to implement in your project, not tools provided by ControlCoding. Create them per your project needs at L3+.

Decoupled communication between components:
- Modules communicate by publishing and listening to events on a common Event Bus
- Shared parameters reside in a central Parameter Registry
- No direct calls between features, only controlled channels

### 3.4 Determinism and Coherence

Two fundamental laws for systems that require reproducibility:

- **Determinism**: Given the same inputs, identical results. Seed-based PRNG per module, stable iteration orders, no wall-clock in calculations.
- **Coherence**: The invariant properties of the system are maintained through every transformation. If the domain has conservation laws (mass, energy, counts, balances), every operation preserves them.

> **Note**: Not all projects require strict determinism or conservation. Adapt to the domain: a financial system conserves balances; a physics simulator conserves energy; a CMS might not have numerical invariants but could have consistency invariants (no orphan links, every page has a parent, etc.).

### 3.5 Anti-Corruption Layer (external integrations)

When a system integrates with external services (LLM providers, third-party APIs, cloud platforms), a dedicated adapter must translate external concepts into domain concepts. The domain never knows the format, naming conventions, or error shapes of the external service. Only the adapter changes when the external API changes.

This is the complement of Model vs View: Model vs View protects the internal representation (authoritative vs derived), the Anti-Corruption Layer protects the domain from external contamination. In CC terms, the adapter lives in shared/ (it serves multiple features) and is the only code that imports the external SDK.

Example: a project using multiple LLM backends (Ollama, Anthropic, OpenAI) has a single `llm_backend.py` in shared/ that exposes `call(prompt, model) -> str`. Each backend's specifics (API format, auth headers, response structure) are isolated in the adapter. When a provider changes their API, one file changes - the domain is untouched.

### 3.6 User Host Boundary and CC Surfaces

ControlCoding distinguishes the user's chosen tool surface from ControlCoding's
own optional UI surfaces.

Canonical stack:

```text
User
  -> User Host
       -> CC Structure
            -> CC Agents (optional)
            -> CC UI (optional)
```

Definitions:

- **User Host**: the user-selected official surface or IDE, such as Claude
  Code, Codex CLI, VS Code, Cursor, Cline, or another supported host.
- **CC Structure**: the mandatory core of ControlCoding - hooks, CLI, config,
  ceremony, gateway, logs, and orchestration rules.
- **CC Agents**: optional helpers such as consultants, reviewer, architect, or
  narrator invoked through the structure layer.
- **CC UI**: optional ControlCoding-owned dashboard, visualizer, or studio.

Operational consequences:

- On `official_cli` or other consumer-auth host paths, the canonical chat stays
  in the User Host. Any CC UI is observability/control only, not a replacement
  vendor chat client.
- On `official_api` and `local_runtime` paths, CC UI may provide the primary
  chat surface because ControlCoding owns the integration layer.
- Consumer OAuth/session tokens must not be embedded, replayed, or proxied by a
  CC-owned UI. OAuth may terminate at the User Host, not inside the CC UI.

---

## 4 Modern Workflow (v2)

### 4.1 From dual roles to native plan mode

**v1**: Two separate AI sessions (Planner produces ChangeSpec, then Surgeon implements it).

**v2**: A single AI agent with **integrated plan mode**:

```
1. PLAN   -> The AI explores the codebase, analyzes impact, proposes a plan
2. REVIEW -> The human approves, modifies, or rejects the plan
3. EXECUTE -> The AI implements the approved plan
4. VERIFY -> Automated tests validate the result
```

This is the same conceptual separation as Planner/Surgeon, but:
- Same session: no handoff, no context loss
- Same memory: the AI remembers everything it planned
- Human checkpoint: the plan is approvable before code
- Native in the tool: Claude Code, Cursor, Kiro all support a plan mode

#### Mini-design slice gate

For any non-trivial feature, the approved plan is followed by a mini-design
slice before implementation starts. The slice is deliberately smaller than a
formal ChangeSpec, but it must be concrete enough to prevent duplicate systems
and accidental overlap.

A mini-design slice records:

- Existing components audited for overlap
- Extension points and integration boundaries
- Non-goals and behavior that must not be duplicated
- Affected files, commands, interfaces, or data contracts
- Acceptance criteria and verification commands

Skip this gate only for small local fixes with no new behavior, no cross-layer
impact, and no architectural risk. If the change touches memory, agents,
harnesses, host context, public commands, or ControlWork promotion, the gate
applies.

#### Feature state machine

For implementation work that is larger than a local fix, track the active
feature with `cc feature`. This creates a local work contract in
`.controlcoding/features/features.json` and keeps WIP at one active feature.

Use:

```bash
python scripts/cc.py feature start <feature-id> --project-root . --title "<title>" --objective "<objective>" --acceptance "<criterion>" --check "<verification command>"
python scripts/cc.py feature verify <feature-id> --project-root . --run "<verification command>" --evidence "<evidence summary>"
python scripts/cc.py feature complete <feature-id> --project-root . --summary "<completion summary>"
```

`feature verify` moves a feature to `passing` only when one or more verification
commands execute successfully. Manual evidence without a command leaves the
feature in `verifying`, so it cannot be completed accidentally.

The active feature state is read by `doctor`, `memory op-index`, and `memory
startup`. These operational surfaces can show the current WIP item or blocked
feature without mutating the registry.

### 4.2 When a ChangeSpec is still needed

The ChangeSpec.yaml is no longer a handoff mechanism between sessions, but remains useful as a documentation artifact for:

- Complex changes that touch multiple modules
- Changes that impact domain invariants
- Work that will be reviewed by other developers
- Operations on stable/ or shared/ (which require extra caution)

**Simplified v2 format**:

```yaml
# ChangeSpec.yaml (v2 - simplified)
title: "Payments: Add retry logic for failed transactions"
intent: "Reduce lost transactions with exponential retry and idempotency key"
touches:
  - "features/Payments/src/systems/TransactionProcessor/**"
  - "features/Payments/tests/**"
no_touch:
  - "stable/**"
  - "features/Payments/include/**"
invariants_to_preserve:
  - "balance_consistency: sum(debits) == sum(credits)"
  - "idempotency: retry(same_key) produces same result"
  - "determinism: same input -> same output"
rollback: "git revert --no-edit <commit>"
```

Note: compared to v1, the `contracts` (redundant with `invariants`), `metrics_expect` (moved to tests), and procedural details fields have been eliminated.

### 4.3 The complete cycle (v2)

```
                    +------------------+
                    | Human request    |
                    +--------+---------+
                             |
                    +--------v---------+
                    | AI: Plan Mode    |
                    | - Reads CLAUDE.md|
                    | - Explores code  |
                    | - Proposes plan  |
                    +--------+---------+
                             |
                    +--------v---------+
                    | CodeWarden:      |
                    | Plan Review      |
                    | (if configured)  |
                    +--------+---------+
                             |
                    +--------v---------+
                    | Human: Approve?  |---No---> Re-plan
                    +--------+---------+
                             | Yes
                    +--------v---------+
                    | AI: Implement    |
                    | - Hooks active   |
                    | - Allowed files  |
                    +--------+---------+
                             |
                    +--------v---------+
                    | Automated tests  |
                    | - Invariants     |
                    | - Performance    |
                    | - Functional     |
                    +--------+---------+
                             |
                   +---------+---------+
                   |                   |
              Pass |              Fail |
                   |                   |
            +------v------+    +-------v-------+
            | Commit / PR |    | Fix or Rollback|
            +------+------+    +---------------+
                   |
            +------v--------------+
            | Commit Ceremony     |
            | - Update CLAUDE.md  |
            | - Update STATUS     |
            | - Write DEVLOG      |
            | - Update BUGS.md    |
            | - Update ROADMAP    |
            +---------------------+
```

### 4.4 Structured commit protocol (Commit Ceremony)

The commit is not just a code backup. It is the moment when the documented state of the project realigns with the actual state. Without this realignment, the AI in the next session starts with stale context - and stale context produces wrong decisions.

The Commit Ceremony is a mandatory workflow rule for every significant commit.
It is not optional.

The adopter project's `documentation_mode` (`managed` vs `project_managed`)
changes who owns the documentation taxonomy. It does **not** disable the commit
ceremony. Even when the adopter keeps its own documentation structure, the
standard ceremony still applies and all impacted project documents must be
realigned.

Storage is a separate choice:

- `cc_artifact_mode = local_only | shared_repo`
- this governs governed CC working docs such as `STATUS.md`, `ROADMAP.md`,
  `BUGS.md`, and CC-managed `dev/`
- `devlog/` remains local session memory and should not be treated as shared
  repository content by default

Repository split:

- in the **ControlCoding framework repo**, maintainer working docs such as
  `CLAUDE.md`, `STATUS.md`, `ROADMAP.md`, `BUGS.md`, `dev/`, and `devlog/`
  remain local-only; the published repo contains the public framework surface
- in **adopter repos**, those same CC working docs should default to local-only
  unless the team explicitly opts into `shared_repo`

Every significant commit must produce the core outputs below and then update all
other impacted documents in the same ceremony:

```
1. Code           -> the actual changes (source files, tests, config)
2. STATUS.md      -> current project state for the next session
3. DEVLOG entry   -> what changed, why, decisions, problems
4. CLAUDE.md      -> update to the project constitution when impacted
5. BUGS.md        -> new bugs found, fixed bugs with commit ref and resolution
6. ROADMAP        -> mark completed tasks, add new ones discovered during work
7. Dev docs       -> plans, design docs, research, handoffs, indexes, archive/deprecated moves affected by the change
```

**Step 1 - Code.** The actual changes. Tests pass, hooks don't block.

**Step 2 - CLAUDE.md.** If the commit changes something architectural (new interface, new directory, new constraint, completed feature), the CLAUDE.md must reflect the change. The AI reads it at every session: if it's not updated, it will reason about an architecture that no longer exists. This step should be done by the AI itself as part of the commit, not by the developer manually - the AI knows what it changed and can update its own context.

**Step 3 - STATUS.md.** Update of the roadmap state: what is complete, what is in progress, what is blocked, updated metrics (test count, files, coverage). This serves three purposes: (a) the AI in the next session knows where to resume, (b) the developer has an always-updated overview, (c) it produces longitudinal data for the evidence report.

**Step 4 - DEVLOG entry.** A brief description, in non-technical language, of what was done and why. It's not a technical changelog ("fix: resolved race condition in mutex lock") but an educational explanation ("Fixed a problem where two parts of the program were trying to write the same data simultaneously. Added a turn mechanism: whoever arrives first writes, the other waits."). The DevLog is local session memory with historical value for communication, onboarding, and decision traceability. It is created at the ceremony even when it is not committed.

**Step 5 - BUGS.md.** A running log of known bugs and their resolution. When a bug is found during a session, add an entry with: description, how to reproduce, severity. When a bug is fixed, update the entry with: commit ref, what was changed, how it was verified. This serves two purposes: (a) the AI in the next session knows what bugs exist and avoids reintroducing them, (b) the developer has a searchable history of defects and fixes.

**Step 6 - ROADMAP update.** Mark completed tasks, update status of in-progress items, add new tasks discovered during work. If the commit closes a roadmap item, mark it done. If the commit reveals new work needed, add it. Without this step, the roadmap drifts from reality and the AI plans against outdated priorities.

**Step 7 - Affected development documents.** If the commit changes project truth,
design, research, handoff state, legal/compliance assumptions, historical
traceability, or document lifecycle, all corresponding documents must be updated
in the same ceremony. This includes local `INDEX.md` files and any required
`archive/` or `deprecated/` moves.

**Closure rule.** A commit is not complete until all impacted documents are
realigned. The minimum ceremony artifacts are mandatory, and impact determines
which additional documents must be updated.

**Why the commit message is not enough:**

The commit message is a single line. The structured commit protocol produces up to 6 artifacts that serve different audiences:

| Artifact | Primary audience | Function |
|---|---|---|
| Commit message | Developer (git log) | Quick identification |
| CLAUDE.md | AI assistant | Context for next session |
| STATUS.md | Developer + AI | Overview and metrics |
| DEVLOG | Non-technical, future developers | Understanding and onboarding |
| BUGS.md | Developer + AI | Known defects and resolution history |
| ROADMAP | Developer + AI | What is done, what remains, what was discovered |

**When to apply it:** not on every micro-commit (typo fix, formatting), but on every commit that changes something significant - new feature, important bug fix, refactoring, new dependency, architectural decision. In practice, if the commit deserves a multi-line message, it deserves the full protocol. `BUGS.md`, `ROADMAP`, and other affected development docs are impact-driven; the ceremony itself is never optional.

---

## 5 Enforcement Levels: Mechanical vs Advisory

### 5.1 The qualitative leap: from advisory to mechanical

The most important difference between v1 and v2 is the shift from **advisory** enforcement (the AI must read the documents and follow them) to **mechanical or repo-side** enforcement. On native-hook hosts, the AI can be blocked before violating boundaries. On non-inline hosts, the boundary moves to pre-commit, review, and verification gates, so violations cannot be accepted silently even though normal editor writes are not intercepted.

**v1**: "The feature.lock says you can't touch stable/. Follow it."
**v2**: A native hook or repo-side gate checks the attempted change and blocks it if the file is out of perimeter for that host's real capability class.

### 5.2 Choosing between WARN and DENY

The boundary hook can emit WARN (warning, the operation proceeds) or DENY (block, the operation fails). The decision principle:

- DENY when the action is irreversible (destructive commands: `git push --force`, `rm -rf`) or when the actor may not understand the consequences (teams with juniors, onboarding new AI agents).
- WARN when the developer understands the consequences but needs a reminder of awareness. Typical for stable zones in small teams: the developer should be able to modify core/, but the WARN forces them to pause and confirm the change is intentional.

In practice: destructive commands are always DENY. Stable zones start as WARN for solo developers and become DENY when the team grows or when the project reaches Level 4 maturity.

**DENY semantics change with context:**

In interactive sessions (human present), DENY acts as an **approval gate**: the AI stops, explains what it needs to change and why, and the human can authorize the operation manually. The file is protected, not untouchable.

In autonomous sessions (no human, e.g. `--dangerously-skip-permissions` with a prompt), DENY becomes a **hard block**: no one is available to approve, so the AI must design around the constraint from the start. This has two consequences:

1. **Stronger upfront design pressure** - the AI must anticipate all future needs before writing protected files, since it cannot iterate on them later. This is the intended benefit: constraints force forward-thinking architecture.
2. **Higher failure cost** - if a protected file has a bug or missing interface, the AI cannot fix it. The AI cannot self-debug protected files because there is no human to approve the exception.

**Recommended enforcement level by mode:**

| Mode | Stable zones | Rationale |
|---|---|---|
| **Interactive** (human present) | DENY | The human acts as approval gate. Can authorize exceptions case by case. |
| **Autonomous** (no human) | WARN | The AI can self-correct. Design pressure comes from CLAUDE.md rules + WARN signals. The ops_logger records all modifications for post-run audit. |
| **Benchmark** (measuring methodology) | DENY | Measures upfront design quality. Build failure = valid data point. No recovery by design. |

Why not DENY in autonomous mode: a hard block with no approval gate means any mistake in a protected file is unrecoverable. Adding escape mechanisms (debug sub-agents, automatic relaxation after N failures, patch files) either weakens the design pressure or introduces advisory constraints that contradict CC's mechanical enforcement philosophy. The cleaner solution is WARN + audit: the AI receives the signal to be careful, can self-correct if needed, and every modification is logged for human review after the run.

Note: the boundary hook only intercepts Edit and Write tools. An AI can bypass the constraint via Bash (e.g. heredoc writes). This is a known limitation of tool-level enforcement. For stricter guarantees, combine hooks with git pre-commit checks.

### 5.3 Temporary Lift Protocol (interactive mode)

In interactive sessions, the AI will sometimes need to modify a DENY-protected file. The standard response is to stop and ask the human for approval (section 5.2, "approval gate" pattern). However, the interaction between AI and human needs structure to prevent the exception from becoming a loophole.

**Observed behavior**: during benchmark testing, an AI encountering DENY blocks followed this sequence: (1) implemented everything possible without touching protected files, (2) stopped and documented exactly what was blocked and why, (3) provided minimal change estimates, (4) after human approval, read the hook configuration to understand the mechanism, (5) changed DENY to WARN, made surgical edits, and restored DENY afterward. This pattern is transparent and traceable, but requires a formal protocol to prevent abuse.

**The five rules of temporary lift:**

1. **Alternative-first**: before requesting a lift, the AI must explore architectural alternatives. Can the feature be implemented via new files, new classes, delegation patterns, or interface extensions that don't touch the protected file? Present both options: "I can do X without touching the protected file" vs "I need N lines of wiring in the protected file because Y." The AI must justify why the protected file must change.

2. **Scoped request**: the AI declares the exact changes before getting permission. List specific functions to modify, estimate line count, describe the nature of each change. No open-ended "let me edit engine.cpp" - the human must know exactly what will change.

3. **Wiring-only rule**: changes to DENY files should be minimal wiring - only delegation calls to external systems (e.g. `movement_->jump()`, `renderer_->renderWeapon(progress)`). No business logic inline: no physics calculations, no item creation, no state machine logic. If the change requires more than ~15 lines, there is likely an architectural alternative.

4. **No chain modifications**: the temporary lift applies ONLY to the declared scope. The AI cannot use an approved lift to modify unrelated parts of the same file. Each additional change needs separate justification and approval.

5. **Mandatory restore**: DENY must be restored immediately after the approved changes. The AI should not leave protections down while continuing to work on other features.

**Verification**: to ensure rule 5 is respected, add a session-end check that verifies boundary rules are intact. Options include:
- A SessionEnd hook that parses the boundary configuration and warns if any DENY was downgraded
- Checksumming the hook configuration file at session start vs end
- Version-controlling the boundary configuration so changes are visible in git diff

**Example from benchmark data** - engine.cpp after temporary lift:

Non-CC AI (no boundaries): added ~30 lines of inline logic to engine.cpp - gravity calculations, item creation, timer decay, flash decay. Three new state variables in Engine class (m_on_ground, m_jump_velocity, m_swing_timer). God Object growth.

CC AI (with temporary lift protocol): added ~6 delegation calls - `movement_->jump()`, `movement_->updateVertical(dt)`, `combat_->triggerSwing()`, `combat_->updateSwing(dt)`, `renderer_->renderWeapon(progress)`, vertical offset in camera position. Zero new state variables in Engine class. All business logic in MovementSystem and CombatSystem.

The temporary lift protocol preserved the architectural benefit of DENY (forcing logic into dedicated systems) while allowing the minimal wiring needed to connect those systems.

**AI reaction to WARN - mandatory instruction in CLAUDE.md:**

The hook emits the signal, but it's the CLAUDE.md that tells the AI how to react. Without explicit instruction, the AI receives the WARN and proceeds anyway - defeating the guardrail. For this reason the project's CLAUDE.md must contain an operative rule like:

```
When a hook emits WARN on a protected zone, STOP and ask for explicit confirmation
from the user before proceeding. Show: which file, which zone, why the modification
is necessary. Never proceed automatically after a WARN.
```

The pattern is intentionally two-level: the hook is mechanical (cannot be ignored at the tool level), the reaction is contextual (the AI reads it in CLAUDE.md at every session). This separates *detection* from *response* - the hook detects, the CLAUDE.md prescribes the behavior.

### 5.4 The compliance matrix

ControlCoding rules fall into two categories with very different compliance characteristics:

**Mechanical rules** are enforced by hooks. The AI cannot bypass them at the tool level. Examples: DENY zones (check_boundaries.py), dangerous command blocking (check_dangerous_commands.py), workflow prerequisites (check_workflow.py). These work regardless of what CLAUDE.md says, what the prompt contains, or whether the AI "agrees" with the constraint.

**Advisory rules** are described in CLAUDE.md. They depend on the AI reading, understanding, and voluntarily following them. Examples: debug protocol ("hypothesis first"), code style preferences, architecture patterns, "use the planner before coding."

**Key finding from benchmarks**: advisory rules have near-zero compliance in autonomous mode.

| Enforcement type | Interactive | Autonomous | Autonomous + prompt mention |
|-----------------|-------------|------------|----------------------------|
| Hook DENY | 100% | 100% | 100% |
| Hook WARN | ~80% | ~80% | ~80% |
| CLAUDE.md "MUST" | ~90% | **0%** | ~70% |
| CLAUDE.md "should" | ~60% | **0%** | ~50% |

The critical column is "Autonomous" - when the AI runs without a human present, it ignores all CLAUDE.md instructions that conflict with its efficiency drive. Even "MUST" language has zero effect. The AI acknowledges the instruction in its thinking trace, then proceeds to do what it considers optimal.

**The prompt matters**: when the launch prompt explicitly mentions CC tools and grades their usage ("How many times did you use visual_check?"), compliance rises to ~70%. This is because the prompt is the AI's primary instruction - CLAUDE.md is supplementary context that gets deprioritized.

**The rule**: if a behavior must happen in autonomous mode, it needs a hook. Text instructions are only reliable when a human is present to enforce them, or when the prompt explicitly demands them.

### 5.5 WARN on individual files (God Objects and constrained files)

Not all files that require attention are in stable/ or shared/. Some files live in features/ (or in the project root) but have documented constraints: God Object with rule "don't add methods", init file with rule "use AppState for new state", configuration file with rule "don't hardcode values".

These files change regularly - they can't be in stable/ - but certain modifications are risky. The solution is to add them as `warn` entries in the hook's PROTECTED_ZONES:

```python
PROTECTED_ZONES = [
    # Zones (directories)
    ("src/core/", "Core models - stable zone", "deny"),
    ("src/shared/", "Shared utilities", "warn"),
    # Constrained individual files
    ("main.cpp", "God Object - new state goes in AppState, don't add logic", "warn"),
]
```

This creates two-level enforcement:
- **L2 (mechanical)**: the hook emits WARN, the agent is forced to stop
- **L1 (documentary)**: the CLAUDE.md explains *what* to do (use AppState, create UI toggle, etc.)

Neither level is sufficient alone. L1 alone gets ignored under time pressure (long sessions, many features). L2 alone says "be careful" but doesn't explain what to do. The combination is much stronger than the individual components.

**When to use file-level WARN**: for files that satisfy all these conditions:
1. They change regularly (can't be deny)
2. They have documented constraints in the CLAUDE.md
3. Violations of those constraints have already happened or are likely

---

## 6 Invariant Tests

### 6.1 The heart of ControlCoding

Invariant tests are what distinguishes ControlCoding from other frameworks. While functional tests verify that code does what it should, invariants verify that code does not violate domain properties: conservation, balances, continuity, determinism.

These tests are domain-specific: no generic tool knows them. They must be explicitly written and maintained as part of the project.

### 6.2 Categories of invariants

Every project has different invariants. The most common categories, with examples by domain:

#### Physical/scientific simulations
| Invariant | Test | Threshold | Why |
|---|---|---|---|
| **Conservation** | Sum of quantity before/after transformation | drift <= 0.1% | Mass/energy are neither created nor destroyed |
| **Continuity** | Differences at boundaries between adjacent regions | RMS <= threshold | No visual or numerical discontinuity |
| **Determinism** | Same parameters -> compare output hash between runs | identical | Scientific reproducibility |
| **Performance** | Time per simulation step (p95/p99) | <= budget ms | Real-time budget |

#### Financial systems
| Invariant | Test | Threshold | Why |
|---|---|---|---|
| **Balance** | sum(debits) == sum(credits) for each transaction | 0% drift | Money is neither created nor lost |
| **Idempotency** | Retry of the same operation produces same result | identical | No transaction duplication |
| **Audit trail** | Every change has a who/when/what record | 100% coverage | Compliance and traceability |

#### Web applications / APIs
| Invariant | Test | Threshold | Why |
|---|---|---|---|
| **Data consistency** | No orphan references in the DB | 0 orphans | Referential integrity |
| **Security** | No endpoint exposes data without authentication | 0 open endpoints | User data protection |
| **Performance** | Response time p95 | <= 200ms | User experience |
| **Rate limiting** | No endpoint without rate limiter | 0 unprotected endpoints | Abuse prevention |

#### Content management
| Invariant | Test | Threshold | Why |
|---|---|---|---|
| **Consistent graph** | No orphan links, every page reachable | 0 isolated nodes | Navigability |
| **Encoding** | All content is valid UTF-8 | 0 encoding errors | Correct rendering |

### 6.3 How to write new invariants

When you add a new feature or system, identify:

1. **What quantities must be conserved?** (mass, energy, money, counts...)
2. **What continuity must be maintained?** (at boundaries, at transitions, over time...)
3. **What properties must be reproducible?** (output from seed, iteration order...)
4. **What budgets must be respected?** (time, memory, rate, sizes...)
5. **What structural relationships must hold?** (no orphans, every node reachable, no cycles...)

For each, write a test with a precise numerical threshold.

### 6.4 Practical elicitation: 5 questions about the domain

The 5 questions above apply to scientific and infrastructure systems. For application domains (scoring, compliance, classification, workflow), a more direct approach: answer these 5 questions about your domain. Every "yes" generates an invariant test.

| Question | Test pattern | Example |
|---|---|---|
| Do you have a value with a fixed range? | Verify the range holds in all constructors and all assignment paths | Voice score [0-4], total [0-100] |
| Do you have a closed set of entities? | Verify all are defined (no more, no less) in every structure that references them | 20 icosahedron faces, 6 cube faces, N scoring categories |
| Do you have a deterministic formula? | Verify the result is consistent for all valid input combinations | final_score = max(component_A, component_B) for each item |
| Do you have a hierarchy or partition? | Verify complete coverage without overlap (union = everything, intersection = empty) | K families partition the N items without overlap |
| Do you have an operation that "only lowers" or "only raises"? | Verify monotonicity: the operation never reverses direction | Cap only lowers, never raises; erosion only reduces, never increases |

This approach is independent of language, framework, and application domain. In a Python project it translates to pytest tests; in C++ to Catch2 tests; in Go to standard tests. The test structure is the same.

### 6.5 CI Pipeline with 3 gates

```
Gate 1: Build + Lint + Visibility
  - Compilation / build without errors
  - Clean linter (eslint, flake8, clang-tidy, etc.)
  - No private symbols exposed

Gate 2: Invariants (blocking)
  - All domain invariant tests
  - Data consistency
  - Determinism (if applicable)
  - Conservation (if applicable)

Gate 3: Performance (quarantine if fails)
  - Response time / time per operation
  - Memory
  - Throughput

If Gate 2 fails: commit rejected, no exceptions.
If Gate 3 fails: label "quarantine", optimization iteration.
```

### 6.6 Architectural Fitness Functions

Invariant tests (section 6.1) verify domain correctness: "does the system work right?" Fitness functions verify architectural health: "is the architecture degrading?" A project can pass all invariant tests while accumulating coupling, fan-out explosion, or silent dependency direction violations. These problems manifest months later as rigidity and fragility.

#### CC-specific metrics

The condominium architecture has precise rules that translate directly into measurable properties:

| Metric | What it measures | Threshold |
|--------|-----------------|-----------|
| Dependency direction violations | Imports from right to left only (stable <- shared <- features <- workspace) | Must be 0 |
| Efferent coupling of stable/ | stable/ must not depend on anything to its right | Must be 0 |
| Instability of shared/ | Ce / (Ca + Ce) for shared/ modules | Should be <= 0.3 |
| Fan-in of shared/ modules | How many features depend on each shared/ module | Track trend, flag if growing > 2x per quarter |
| Files modified per task | Number of files touched in a single logical change | Flag if > 5 for a single feature modification |

#### The temporal dimension

A fitness function is not just "coupling is X today" but "coupling grew Y% over the last N sessions". The CI threshold is a delta, not only an absolute value. This absorbs trend monitoring into the fitness function framework:

```
Gate 1.5: Architecture (between Gate 1 and Gate 2)
  - dependency_direction_violations == 0          (absolute)
  - efferent_coupling(stable/) == 0               (absolute)
  - instability(shared/) <= 0.3                   (absolute)
  - fan_in_growth(shared/) <= 20% per release     (trend)
  - avg_files_per_task <= historical_avg * 1.5     (trend)

If Gate 1.5 fails: commit rejected with architectural explanation.
```

Trend data comes from the same JSONL files CC already produces (ops_log.jsonl, codewarden_violations.jsonl). The fitness function script reads historical data and compares against the current state.

#### Tooling by language

The metrics above require dependency analysis. Suggested tools:

- **Python**: `import-linter` with `independence` contracts, `pydeps` for visualization
- **JavaScript/TypeScript**: `dependency-cruiser` with `.dependency-cruiser.cjs` rules
- **Java/Kotlin**: `ArchUnit` with layered architecture tests
- **C/C++**: `include-what-you-use` + custom header dependency analysis
- **Language-agnostic**: git-based analysis of co-change patterns (files that always change together indicate hidden coupling)

#### When to skip

For projects under 20 modules or solo developers in early development, fitness functions add overhead without proportional value. The trigger table (section 8.1) should include: "Add fitness functions when the project has 20+ modules across 3+ condominium zones."

---

## 7 Promotion Path

### 7.1 The code lifecycle

```
workspace/        "I have an idea, let me try it"
     |
     v (it works and serves the feature)
features/X/       "Private function, used only here"
     |
     v (used by >= 2 features)
shared/           "Reusable utility, in the registry"
     |
     v (stable, fixed API, used by >= 3 features)
stable/           "Foundation. Untouchable."
```

### 7.2 Promotion rules

**From workspace to features**: The experiment works. Create the feature structure (public API, tests, feature.lock). Normal commit.

**From features to shared**: The function is reused or reusable by at least 2 features. Move to shared/, add to FUNCTION_REGISTRY, update tests.

```yaml
# PromotionSpec.yaml (optional, for significant promotions)
source: "features/Auth/src/utils/TokenRotation.py"
target: "shared/auth/TokenRotation.py"
motivation: "Reuse in Auth, Payments and Notifications"
tests_must_pass:
  - "test_auth_token_rotation"
  - "test_payments_auth_integration"
```

**From shared to stable**: The utility is mature, API stable, used by 3+ features without recent changes. Requires extraordinary review and an ADR (Architecture Decision Record) documenting the decision.

ADR format (adapted from Nygard):

```markdown
# ADR-NNN: [Title]

**Status**: Proposed | Accepted | Deprecated | Superseded by ADR-MMM
**Date**: YYYY-MM-DD

## Context
What situation or problem prompted this decision?

## Decision
What was decided and why?

## Consequences
What follows from this decision? (positive, negative, neutral)
```

Store ADRs in `docs/adr/` with sequential numbering. The status lifecycle is: Proposed (under review) -> Accepted (approved, in effect) -> Deprecated (no longer relevant) -> Superseded (replaced by a newer ADR). ADRs are also required for Strangler Fig replacements (section 7.4). See `templates/adr.md` for the template.

### 7.3 Note on dependency direction

```
stable/ <--- shared/ <--- features/ <--- workspace/
```

Dependencies go ONLY from right to left. Never the opposite. If stable/ needed something from features/, there's an architectural problem.

### 7.4 Demotion and Replacement (Strangler Fig)

The promotion path goes in one direction: workspace -> features -> shared -> stable. But long-lived projects inevitably need to replace stable components - an ORM becomes unmaintained, a core interface needs breaking changes, a design decision proves wrong at scale.

Without a formal demotion path, replacements happen as chaotic exceptions (Temporary Lift abused for weeks) or as giant refactors that bypass all CC guardrails. The Strangler Fig pattern (Fowler, 2004) provides a controlled alternative.

**The process**:

1. **New component in features/**: build the replacement as a regular feature, unconstrained by DENY. It must pass all invariant tests from the start.

2. **Adapter in shared/ (WARN)**: create a facade that routes calls to either the old or new implementation. Consumers import the adapter, not the implementations directly.

3. **Gradual migration**: move consumers one at a time from old to new. Each migration is a normal commit. The adapter tracks which consumers still use the old path.

4. **Removal**: when the last consumer migrates, remove the old component from stable/ and promote the new one through the normal promotion path (features -> shared -> stable).

**Rules**:
- The old component in stable/ keeps its DENY protection throughout
- The new component in features/ follows normal CC constraints
- The adapter in shared/ is WARN (it changes as consumers migrate)
- An ADR documenting the replacement decision is required (see 7.2)
- The adapter must never change the interface shape - only route between implementations

**When to use**: only when a component in stable/ needs full replacement. For minor changes, Temporary Lift Protocol (section 5.3) is sufficient. The Strangler Fig is for "this whole module needs to be rewritten" scenarios.

---

## 8 Anti-patterns and Red Lines

### 8.1 Absolute prohibitions

These are **hardcoded** in the process. Hooks block them mechanically where possible.

| Anti-pattern | Why | Enforcement |
|---|---|---|
| Writing authoritative data in the view | Violates Model vs View | Hook + CLAUDE.md |
| Inverse dependencies (stable -> features) | Inverts the stability pyramid | Hook + review |
| Implicit non-determinism (if the domain requires determinism) | Thread order, wall-clock, unordered containers | Determinism test |
| Exceeding budget "just a little" | "Just a little" accumulates | Gate 3 CI |
| Mutable global state | Not testable, not thread-safe | Code review |
| Duplicating existing utilities in shared/ | Divergence and bugs | CLAUDE.md + registry |
| Bypassing validation/sanitization | Security vulnerability | Lint + review |

### 8.2 Warning signs

If during development you notice one of these patterns, stop and replan:

- "I need to modify stable/ to make my feature work": the interface is insufficient. Extend it, don't bypass it.
- "This function already exists in shared/ but doesn't do exactly what I need": extend the existing function, don't duplicate it.
- "I need to touch 5+ files in different features": the scope is too large. Split into smaller tasks.
- "Tests pass but the result doesn't look right": add an invariant that captures the problem.
- "It works if I disable this test": the test is right. Find the error in the code, not the test.
- "I did a hardcoded set*() in init to make the feature work quickly": state goes in AppState/WorldState with UI toggle. Hardcoding bypasses user control and violates the architectural pattern.

### 8.3 Rollback and recovery

Even with guardrails, something can slip through. The rollback procedure in ControlCoding follows a principle: the commit is the atomic unit of change.

If a commit passes all gates but then causes problems in production or in subsequent integration, `git revert <commit>` undoes the changes without losing history. After the revert, invariant tests are re-run to confirm the previous state is healthy.

If the problem is in the most recent commit, the revert is trivial. If it's in a commit from 3 days ago, the revert may have conflicts with subsequent commits. In that case the procedure is: revert, resolve conflicts, re-run all invariant tests. If tests fail after resolving conflicts, the merge is wrong. Reject and retry.

What not to do: `git reset --hard` to "go back". Destroys history and makes it impossible to understand what happened. The boundary hook blocks it explicitly (see [Hooks Reference](hooks-reference.md)).

For large restructurings (like adopting the condominium structure), the commit plan should provide for individually revertible atomic commits. A commit that moves 70 files and changes 40 includes in one shot is risky but revertible as a whole if necessary. Smaller commits (first the files, then the includes, then CMakeLists) are safer but create intermediate states that don't compile. The choice depends on the project.

---

## 9 Gradual Implementation

The most common error when adopting ControlCoding is implementing everything at once. The system is designed for progressive adoption: each component has a trigger (the condition that justifies its implementation) and a prerequisite (what must exist before).

Principle: implementing a component before its trigger is over-engineering. Implementing it after (when the trigger has already fired) is technical debt. The right moment is when the trigger fires, not before and not after.

### 9.1 Trigger table

| CC Component | Trigger | Prerequisite | Estimated Cost |
|---|---|---|---|
| CLAUDE.md | Project start | None | 30 min |
| Operative rules in CLAUDE.md | First work session | Base CLAUDE.md | 1 hour |
| Domain invariant tests | Before any refactoring or significant new feature | Knowledge of domain invariants | 2-4 hours |
| Abstract interfaces (IXxxSource) | When two modules need to communicate without coupling | At least 2 modules | 1-2 hours |
| Event Bus (stub) | When 2+ modules need to react to the same events | At least 1 active module | 2-4 hours |
| Event Bus (complete) | When structured payloads, idempotency, timeline are needed | Working Event Bus stub + 3+ modules | 1-2 days |
| ParamRegistry | When configuration parameters are used by 2+ modules | At least 2 modules + external config | 1-2 hours |
| WorldState / AppState | When globals/scattered state become unmanageable | God Object identified | 3-6 hours |
| Condominium structure | When the codebase exceeds ~50 files with 2+ interacting features | feature.lock at least drafted | 1 day |
| feature.lock | When 3+ independent modules coexist and risk invading each other's boundaries | Clear directory structure | 2-4 hours |
| Boundary hooks | When feature.lock exists and mechanical enforcement is desired | feature.lock + pre-commit hooks | 2-4 hours |
| Function registry | When the codebase exceeds ~100 files and the AI struggles to find functions | Mature codebase | 1-2 hours |
| CI pipeline 3 gates | When the team (human + AI) works in parallel on branches | Stable invariant tests | 1 day |
| Fitness functions (Gate 1.5) | When the project has 20+ modules across 3+ condominium zones | CI pipeline + boundary hooks | 2-4 hours |
| ADR lifecycle | When stable/ contains components that may need replacement | Promotion path in use | 1 hour |
| Strangler Fig process | When a component in stable/ needs full replacement | ADR + working invariant tests | Varies |

### 9.2 Adoption levels

The recommended pattern has 4 levels, each activated by a complexity threshold:

```
Level 1, "Documentary" (any project, day 1):
  CLAUDE.md with architecture, principles, conventions
  CC operative rules in CLAUDE.md
  Pre-commit hook (build + test)
  DevLog or changelog for significant decisions

Level 2, "Structural" (first domain module):
  TRIGGER: "we are adding the first module with domain logic"
  Extract global state into AppState/WorldState
  Create interfaces for decoupling (IXxxSource)
  Domain invariant tests (2-5 critical tests)
  ParamRegistry if config parameters are shared

Level 3, "Communication" (2+ interacting modules):
  TRIGGER: "two modules need to exchange data or react to common events"
  Event Bus (stub, then complete)
  Evaluate condominium structure
  Cross-module invariant tests

Level 4, "Governance" (mature system with stable modules):
  TRIGGER: "we have modules we consider 'stable' and don't want to break"
  feature.lock for stable modules
  Boundary hooks (mechanical enforcement)
  Function registry (if large codebase)
  CI pipeline with 3 gates
  Promotion path (workspace > features > shared > stable)
```

### 9.3 When the Event Bus is needed

The Event Bus is often undervalued ("my project is a linear pipeline, no pub/sub needed"). In reality, even linear pipelines develop Event Bus needs when:

1. A single event affects multiple modules. An asteroid impact influences erosion (crater), climate (dust), biology (extinction). Without a bus, the impact module would need to know all consumers (NxN coupling).

2. The user can inject events. "What if" scenarios, interactive editing, custom bombardment. These events must reach all relevant modules.

3. Deterministic replay is required. The Event Bus with `idempotency_key` ensures a repeated simulation produces identical results.

4. The system has a timeline of planned events. Future events read from a file (timeline.json) and published when the timestamp is reached.

The Event Bus for scientific simulations is typically synchronous and deterministic (a `std::vector<Event>` with type-safe subscribe/publish), not an asynchronous message broker. Implementation complexity is low (~100-200 lines).

### 9.4 When the ParamRegistry is needed

The ParamRegistry is needed when:

- 2+ modules read the same configuration parameters (planet radius, gravitational constant, orbital parameters, etc.)
- Parameters are R/O during simulation but W during setup/editor phase
- Function signatures become unmanageable (10+ parameters passed manually)

Implementation: a struct with const accessors, passed as a reference to modules. No framework needed; a singleton or dependency injection suffices. ~50 lines.

### 9.5 Activating triggers

Triggers are useful only if someone checks them. In practice nobody periodically re-reads the trigger table. Mechanisms are needed that activate them automatically or semi-automatically.

Three strategies, in order of increasing automation:

**Strategy 1: Line in the AI constitution file** (zero cost, high effectiveness for projects with AI assistant). Add a line to the CLAUDE.md in the operative rules:

```
After structural changes (new modules, new files in shared/, new directories),
check the CC trigger table in docs/CONTROLCODING.md (or section 13 of the
methodology) and flag if a trigger has fired.
```

Also insert reminders with the "CC:" prefix next to each roadmap milestone:

```markdown
7. **Tectonics** (plates, collisions)
   - CC: Create src/simulation/tectonics/ (first separate module)
   - CC: Implement Event Bus stub + ParamRegistry
8. **Erosion** (hydraulic + thermal)
   - CC: Event Bus active (tectonics publishes > erosion consumes)
   - CC: Evaluate condominium structure if codebase > 50 files and 2+ interacting modules
```

This way every AI session reads the CLAUDE.md and knows what to implement alongside the code.

**Strategy 2: Audit script** (low cost, high effectiveness for projects with CI or disciplined developers). A read-only Python script that counts files per directory, counts plugins, counts invariant tests, and flags fired triggers. The script produces a report and modifies nothing. It can be run manually or integrated into CI as an informational (non-blocking) step.

**Strategy 3: Manual checklist in PR template or pre-commit** (minimal cost, medium effectiveness). Add to the PR template or pre-commit workflow a checklist:

```
- [ ] Checked: source file count (trigger promotion path: >100)
- [ ] Checked: active plugin count (trigger feature.lock: >=3)
- [ ] Checked: invariant test count (trigger Stop hook: >30)
  (Note: ControlCoding's own test suite has 227 tests across 8 files as of March 2026.
   Your project's threshold depends on domain complexity.)
```

Strategy 1 is sufficient for most projects. Strategy 2 becomes useful when the project has CI/CD. Strategy 3 is the fallback for projects without AI assistant and without CI.

### 9.6 Per-project adoption document

Every project that adopts ControlCoding should create its own adoption document (e.g. `docs/CONTROLCODING.md`) with 3 sections:

```markdown
## 1. What we adopt now

CC level adopted, mapping between project directories and CC hierarchy
(stable/shared/features), artifacts created (hooks, invariant tests,
operative rules in the AI constitution file).

## 2. What we will adopt in the future (with triggers)

Table of CC components not yet implemented, with:
- Trigger: measurable condition that justifies implementation
- Current value: where we are relative to the trigger
- How to measure: command or metric to verify if the trigger has fired

Example:
| Component | Trigger | Current Value | How to Measure |
|---|---|---|---|
| feature.lock | >= 3 active plugins | 1 | ls src/plugins/*/plugin.py |
| Promotion path | > 100 source files | ~80 | find src/ -name "*.py" | wc -l |
| Stop hook | > 30 invariant tests | 24 | grep -c "def test_" tests/test_domain_invariants.py |

## 3. What we don't need (and why)

Explicit list of CC components the project has evaluated and decided
not to implement, with rationale. This prevents every new work session
(human or AI) from wondering "why don't we have X?" and wasting time
re-evaluating it.

Example:
| Component | Why not needed |
|---|---|
| Rename directories | Current names are semantically meaningful for the domain. The mapping is documented. |
| Formal ChangeSpec | Solo developer + AI. The DevLog and plan mode are sufficient. |
| Seed-based determinism | The domain is not a simulation. Determinism comes from the rules. |
```

Sections 2 and 3 work as a periodic self-assessment: at every significant milestone, re-read the trigger table and verify if something has changed. The AI assistant, if instructed in the CLAUDE.md, can do this check automatically after structural changes.

### 9.7 Cost of ControlCoding

Every methodology has overhead. Declaring it openly avoids surprises.

Initial setup (Level 1, documentary): about half a day. Write CLAUDE.md with principles and operative rules, configure the boundary hook, define 2-3 domain invariant tests. It's a one-time investment that pays off from the second work session.

Per-session maintenance: negligible. CLAUDE.md is updated when the project structure changes (not every session). Hooks don't require maintenance until the directory structure changes. Invariant tests are updated when new modules are added or new invariants are discovered.

Per-commit overhead: the pre-commit hook adds a few seconds of testing. In a project with 200 invariant tests, the time is on the order of 10-30 seconds. Acceptable.

Cognitive cost: medium-low. ControlCoding rules are few and intuitive (don't touch stable, test invariants, plan first). Process complexity is low; the complexity is in the domain, where it should be.

Where cost grows: the full condominium structure (Level 4) requires significant codebase restructuring: moving files, updating imports/includes, reconfiguring the build system. For a project with 50-100 files, the cost is 1-2 days. It's an investment justified only if the project truly has 2+ interacting modules that risk corrupting each other.

Where cost is not justified: small projects with a single module, throwaway prototypes, simple automation scripts. In these cases, Level 1 (CLAUDE.md + basic hook) is sufficient and has no significant overhead.

### 9.8 CLI tooling (cc.py)

The `cc.py` CLI tool automates ControlCoding setup and maintenance. It provides two setup modes, central hooks for multi-project environments, automatic gitignore management, and a health check command.

#### cc setup (chat-first apply flow)

`cc setup` is the recommended entry point for new projects. The intended path is chat-first: print the setup contract with `cc setup --chat-guide`, let the host collect answers, then apply the resulting handoff with `cc setup --answers-file handoff.json`. The flow still covers:

1. **Project info**: name, stack, architecture description, truth/view representations
2. **Documentation ownership**: asks whether ControlCoding should manage the repo's documentation taxonomy (`managed`) or leave documentation ownership to the adopter (`project_managed`)
3. **Directory scanning**: auto-detects top-level directories and asks the user to classify each as stable, shared, feature, or skip
4. **Backend discovery**: reports available consultant backends from PATH and API-key presence without selecting one
5. **Protected zones**: for each stable directory, asks whether to protect with DENY (block) or WARN (allow + notify), then writes the result to `.claude/cc_config.json`
6. **Pack selection**: offers optional packs (debug-tools, session-manager, multi-agent, dashboard) with yes/no for each
7. **Hook location**: local (`hooks/` in the project, gitignored) or central (`~/.controlcoding/hooks/`, shared across projects)
8. **Behavioral constraints**: free-form rules that go into CLAUDE.md as `[advisory]` rules (e.g. "no random without seed", "all API calls need auth")
9. **Consultant backend**: if debug-tools is selected, applies only an explicit supported `backend_pref`; absent or empty means configure later, even when providers are discovered

After confirmation, `cc setup` generates the canonical context and host-derived
projection, writes setup configuration, then calls `cc init` internally to
copy hooks, create settings.json, and set up gitignore. A confirmed handoff with
`configure_advanced_packs=true` installs its `selected_packs` exactly. The
fresh `debug-tools` pack has no primary or fallback provider configuration;
setup persists a consultant backend only when `backend_pref` is non-empty and
supported.

#### cc init (non-interactive)

`cc init` is the non-interactive counterpart. It copies hook scripts, generates a default CLAUDE.md from template, creates `cc_config.json` with example zones, writes `settings.json` with absolute hook paths, and appends the gitignore block. It skips any file that already exists, making it safe to re-run.

#### Central hooks

By default, `cc init` copies hook scripts into the project's `hooks/` directory (gitignored). For developers working on multiple projects, maintaining separate hook copies in each project is redundant. The `--central-hooks` flag changes the installation target:

- Hook scripts are copied to `~/.controlcoding/hooks/` instead of `hooks/`
- `settings.json` hook commands point to the central directory (absolute paths)
- `.claude/cc_config.json` records `"hooks_location": "central"` so that `cc doctor` knows where to look
- The gitignore block omits hook file patterns (since hooks are not in the project tree)

Central hooks are appropriate when:
- A single developer uses CC across 3+ projects
- A team wants to standardize hook versions without per-project copies
- Hook updates should propagate to all projects by updating one directory

Central hooks are NOT appropriate when:
- Different projects need different hook configurations (custom PROTECTED_ZONES, custom WORKFLOW_RULES)
- The project must be self-contained (e.g. for CI or for contributors who don't have CC installed globally)

#### Gitignore auto-generation

`cc init` appends a managed block to `.gitignore` that excludes ControlCoding artifacts from version control. The block is delimited by markers:

```
# --- ControlCoding local artifacts (do not commit) ---
hooks/check_boundaries.py
hooks/check_dangerous_commands.py
hooks/codewarden_*.py
hooks/hook_logger.py
hooks/violation_store.py
hooks/session_end_check.py
hooks/check_workflow.py

.claude/deny_hashes.json
.claude/cc_hook_log.jsonl
.claude/codewarden_violations.jsonl
.claude/codewarden_report.md
.claude/session_counter.json
.claude/hooks_lifted.json
.claude/lift_request.json
.claude/consult_log.jsonl

tools/fitness_check.py
tools/mcp_*.py
tools/visual_check.py
tools/visual_test.py
tools/consult.py
tools/cc_dashboard.py
tools/cc_audit.py

screenshots/
.bridge/
# --- End ControlCoding ---
```

The block covers three categories: hook scripts (local copies sourced from the CC installation), runtime artifacts (violation logs, deny hashes, session state), and optional tools (MCP servers, visual check, dashboard). The `screenshots/` and `.bridge/` directories are runtime outputs from visual check and multi-agent communication respectively.

**Idempotency**: the function checks for the presence of `GITIGNORE_MARKER_START` in the existing `.gitignore`. If the marker is found, no changes are made. This makes `cc init` safe to run multiple times without duplicating the block.

**Central hooks variant**: when `--central-hooks` is used, the gitignore block omits the `hooks/` section (lines starting with `hooks/`), since hook scripts live outside the project tree and do not need to be gitignored.

#### Template marker system

The CLAUDE.md template (`templates/CLAUDE.md.template`) uses HTML comment markers instead of literal text placeholders. The old format used inline text like `[PROJECT_NAME]` or `{{PROJECT_STACK}}`. The new format uses HTML comments:

```markdown
<!-- CC:PROJECT_NAME -->
<!-- CC:PROJECT_STACK -->
<!-- CC:ARCH_RULES -->
<!-- CC:STABLE_ZONES -->
```

The change was made for two reasons:

1. **Validity as-is**: HTML comments are invisible in rendered Markdown. A user who copies the template without running `cc setup` gets a valid, readable document with empty sections rather than visible placeholder strings like `[TODO: fill in]`.
2. **Machine-parseable**: the `cc setup` apply flow and `_generate_claude_md()` function can find and replace markers reliably using exact string matching, without risk of collision with user content.

The `cc setup` apply flow reads the template, replaces each `<!-- CC:KEY -->` marker with the user's answers, and writes the result as the project's CLAUDE.md.

### 9.9 Impact Analysis

When an AI agent modifies a file to fix a bug or change behavior, it frequently edits only the file it considers "obvious" and declares the task complete. It does not search the codebase for other files that contain the same concept. The change does not propagate, causing silent failures that surface only at runtime.

Advisory rules ("grep for all callsites before editing") have near-zero compliance in autonomous mode (Section 5.4 compliance matrix). The solution must be mechanical.

#### Concept fingerprints

A **concept fingerprint** is a pattern extractable from a plan or code diff that identifies related code elsewhere in the codebase: function definitions, import targets, subprocess commands, configuration keys. When the AI plans to modify a file, the Impact Analysis hook extracts fingerprints from that file and searches the codebase for other files that reference the same patterns. Files that match but are not mentioned in the plan are flagged.

#### Enforcement cascade

Impact Analysis operates at two of CodeWarden's temporal levels:

**Plan time** (PreToolUse on ExitPlanMode): extracts fingerprints from the plan text and referenced files, greps codebase, injects results into the CodeWarden review prompt. The reviewing LLM warns: "your plan changes file A, but files B and C contain the same patterns and are not covered."

**Session end** (Stop hook): extracts fingerprints from the git diff, greps for matching patterns in unchanged files, injects results into the session-end review.

Both use a single LLM call (no extra cost). The impact data is added to the existing prompt.

#### Gateway Modules

Section 3.5 describes the Anti-Corruption Layer: "a dedicated adapter must translate external concepts into domain concepts." Gateway Modules extend this with mechanical enforcement. A gateway module is a single-point-of-contact function for a cross-cutting concern. When registered in `.claude/cc_config.json`, Impact Analysis always scans for direct usage of the underlying pattern outside the gateway file:

```json
{
  "gateway_modules": [
    {"pattern": "call_model", "file": "shared/llm_backend.py",
     "description": "All LLM calls must go through call_model()"}
  ]
}
```

See the [Hooks Reference](hooks-reference.md) for implementation details and configuration.

---

## 10 Conclusions

### 10.1 ControlCoding in the 2026 context

Several ControlCoding v1 (2024) concepts appear in current industry practices under different names:

| ControlCoding v1 Concept (2024) | 2026 Industry Equivalent | Notes |
|---|---|---|
| ChangeSpec.yaml (spec before code) | Spec-Driven Development (Kiro, cc-sdd, GitHub Spec Kit) | Now widespread approach, with dedicated IDEs like Kiro |
| Planner / Surgeon (two AI roles) | Plan Mode in Claude Code, multi-agent | Planning/execution separation now native in tools |
| feature.lock (allow/deny per file) | PreToolUse hooks in Claude Code | Hooks with exit codes to block writes on specific files |
| CODEMAP.md (project memory for AI) | CLAUDE.md | Project context file read natively by the tool |
| FUNCTION_REGISTRY.yml (function catalog) | Repository Maps (Aider), tree-sitter AST | Automatic code mapping via AST |
| "Context Architecture" (structured docs for AI) | Context Engineering | Discipline now recognized as distinct from prompt engineering |
| Invariant tests as gate | CI guardrails + hook enforcement | Hooks that block commits if tests don't pass |
| "Software condominium" (modular isolation) | Feature capsules, bounded contexts | Equivalent concept, different terminology |

v2 doesn't change the philosophy, it changes the implementation: less manual process, more automation; mechanical enforcement instead of advisory.

In the current landscape, ControlCoding occupies a precise niche: architectural governance based on domain invariants. Frameworks like GitHub Spec Kit (2026) cover security constraints (CWE mapping, MUST/SHOULD/MAY), Kiro covers spec-implementation correspondence with property-based testing. ControlCoding covers what neither addresses: the physical, financial, or causal properties specific to a system, which only domain experts can identify and codify. The three approaches are complementary.

### 10.2 The three immutable pillars

Regardless of the tool, the AI model, or the year, ControlCoding rests on three pillars:

1. **Isolation**: the software condominium prevents error propagation
2. **Invariants**: domain tests catch violations no generic tool knows about
3. **Controlled promotion**: code matures through levels of increasing stability

These three pillars are **technology-independent** and will remain valid even when AI models are 10x more capable than today.

### 10.3 When ControlCoding is NOT needed

ControlCoding adds value proportional to the project's complexity. It is not needed for:
- One-off scripts or simple utilities
- Throwaway prototypes
- Single-file projects
- Projects without domain invariants (e.g. static landing pages)

It is needed for:
- Physical/scientific simulations (determinism, conservation)
- Systems with multiple representations (model vs view)
- Financial systems (balances, audit, idempotency)
- Projects with 10+ interacting modules
- Software that must maintain global properties while evolving locally
- Teams (humans + AI) working in parallel on the same codebase
- API backends with security and performance requirements

### 10.4 Adapting to your project

This document is a **generalist template**. To use it in your project:

1. Identify your domain's invariants. What must NEVER break in your system? Write tests for these properties.

2. Define the source of truth. What is your "Model" and what are the "Views"? This determines where data is modified and from where it is only read.

3. Calibrate the process level. A scientific project with 50 modules needs more structure than a web app with 5 features. Use only the parts of ControlCoding that add value to your case.

4. Start light. CLAUDE.md + one hook for stable/ + one domain invariant. Add structure only when needed.

---

## Appendix A: Comparative Analysis v1 vs v2 and State of the Art

### A.1 Evaluation of ControlCoding v1

v1 (2024) contained solid architectural ideas that remain valid: the condominium metaphor, domain invariants, the workspace-to-stable promotion path, the Model vs View principle. Some of these ideas appear today, under different names, in mainstream AI-assisted development tools (table A.2 lists the correspondences).

The main limitations of v1 concerned the weight of the process: too many files to maintain manually (CODEMAP, REGISTRY, CONTRACTS as separate files), purely advisory enforcement ("the AI should read this file") and the forced Planner/Surgeon separation into different sessions, which 2026 tools have made superfluous thanks to native plan mode.

### A.2 Correspondences between v1 and current practices

| ControlCoding v1 Concept (2024) | 2026 Industry Equivalent | Notes |
|---|---|---|
| ChangeSpec.yaml (spec before code) | Spec-Driven Development (Kiro, cc-sdd, GitHub Spec Kit) | Now widespread approach, with dedicated IDEs like Kiro |
| Planner / Surgeon (two AI roles) | Plan Mode in Claude Code, multi-agent | Planning/execution separation now native in tools |
| feature.lock (allow/deny per file) | PreToolUse hooks in Claude Code | Hooks with exit codes to block writes on specific files |
| CODEMAP.md (project memory for AI) | CLAUDE.md | Project context file read natively by the tool |
| FUNCTION_REGISTRY.yml (function catalog) | Repository Maps (Aider), tree-sitter AST | Automatic code mapping via AST |
| "Context Architecture" (structured docs for AI) | Context Engineering | Discipline now recognized as distinct from prompt engineering |
| Invariant tests as gate | CI guardrails + hook enforcement | Hooks that block commits if tests don't pass |
| "Software condominium" (modular isolation) | Feature capsules, bounded contexts | Equivalent concept, different terminology |

### A.3 Aspects not covered by generic tools

**A.3.1 Domain-specific invariants**

Generic tools do not know the invariant properties of a specific system. Mass conservation, financial balances, referential integrity, numerical continuity: these rules must be identified and codified by domain experts. ControlCoding provides the structure to do so; the content remains the engineer's responsibility.

**A.3.2 Code promotion path**

The workspace-features-shared-stable pipeline is a code governance model with levels of increasing stability. Generic tools offer file boundaries but not a maturation path.

**A.3.3 Model vs View as architectural principle**

In systems with dual representation (authoritative data + visual/UI derivatives), establishing which is the source of truth and forbidding writes in the wrong direction prevents an entire class of bugs. Generic tools don't distinguish between the two.

### A.4 Comparison with 2026 methodologies

| Aspect | ControlCoding v2 | Kiro SDD | GitHub Spec Kit | BMAD Method | Cursor Rules | Native Claude Code |
|---|---|---|---|---|---|---|
| Spec before code | ChangeSpec (documentation) | Spec -> Design -> Tasks | Constitution -> Specify -> Plan | PRD + Architecture | .cursor/rules/*.mdc | CLAUDE.md + plan mode |
| File boundaries | Executable hooks | Not present | Not present | Not present | Glob patterns | PreToolUse hooks |
| Invariant testing | Domain tests (physical, financial) | Property-based testing | Security constraints (CWE) | Via TEA module | Not present | Not present |
| AI memory | Unified CLAUDE.md | .kiro/steering/ (conditional) | Constitution file | Agent personas + KB | .cursor/rules/*.mdc | CLAUDE.md |
| Promotion path | workspace > features > shared > stable | Not present | Not present | Not present | Not present | Not present |
| Model vs View | Explicit architectural principle | Not present | Not present | Not present | Not present | Not present |
| Enforcement | Hooks + CI (mechanical) | Hooks on file events | Advisory (MUST/SHOULD/MAY) | Advisory (role-based) | Hooks + rules | Hooks (mechanical) |
| Gradual adoption | Explicit conditional triggers | Not present | Not present | Not present | Not present | Not present |
| Session persistence | STATUS.md + devlog + history (MCP) | Not present | Not present | Not present | Not present | Not present |
| Multi-agent | Bridge MCP + helper variants | Not present | Not present | Multi-role personas | Not present | Not present |
| Debug escalation | 3 levels (protocol, visual, consultation) | Not present | Not present | Not present | Not present | Not present |
| External consultation | 5 roles + agent-mode + domain adapt | Not present | Not present | Not present | Not present | Not present |
| Monitoring dashboard | Gradio web UI (read-only) | Not present | Not present | Not present | Not present | Not present |
| Tool-agnostic | Yes (methodology) | No (its own IDE) | Yes (methodology) | Partial | No (its own IDE) | No (its own CLI) |
| Process overhead | Medium | Medium | Medium | High | Low | Low |
| Primary focus | Domain invariants + architecture | Spec-fidelity + PBT | Security by construction | Multi-role governance | Productivity | Speed |

ControlCoding, Kiro, and GitHub Spec Kit cover different areas and are largely complementary: ControlCoding focuses on domain invariants and architectural governance, Kiro on spec-implementation correspondence through property-based testing, Spec Kit on security constraints with CWE mapping.

### A.5 Motivations for v1 -> v2 changes

| Change | Motivation |
|---|---|
| Planner/Surgeon -> Plan mode | Native plan mode eliminates the complexity of inter-session handoff without losing the conceptual separation |
| Advisory feature.lock -> executable hooks | Mechanical enforcement eliminates dependence on agent discipline. The AI doesn't "should" respect boundaries, it "cannot" violate them |
| 3 documents -> unified CLAUDE.md | A single file read natively by the tool is more effective than 3 files the AI must remember to consult |
| Detailed REGISTRY -> lightweight | Opus 4.6 explores the codebase autonomously. The registry is needed only for functions with non-obvious semantics |
| RenameSpec eliminated | 2026 models don't rename without explicit request. The overhead is no longer justified |
| ChangeSpec simplified | From handoff mechanism to documentation artifact. Fewer fields, same utility |
| Added agentic workflow | Agentic work (autonomous cycles, sub-agents) didn't exist in 2024. Guardrails must cover this scenario too |
| Added multi-tool adaptation | In 2024 the landscape was less mature. Now Claude Code, Cursor, Aider, Kiro each have their own mechanisms |

### A.6 Summary

v1 had solid architectural ideas but a process too heavy for 2026 models. v2 preserves the core concepts (condominium, invariants, promotion, Model vs View) and makes them operational with current tools, replacing advisory enforcement with mechanical enforcement and manual process with native tool integration.

---

## Further Reading

- [Hooks Reference](hooks-reference.md) - hook configuration and behavior
- [Tools Reference](tools-reference.md) - CLI, MCP servers, scripts
- [Cookbook](cookbook.md) - worked examples by domain
- [Install ControlCoding On Your Project](../install-controlcoding-on-your-project.md) - Primary install path for a new adopter
- [Quick Start](../quick-start.md) - Compact setup summary
- [Adoption Guide](../adoption-guide.md) - when to adopt each component
- [Cross-Tool Guide](../cross-tool-guide.md) - setup for non-Claude-Code tools
