# Methodology History

This page records the public history behind ControlCoding Core.

ControlCoding was originated by Stefano Tonello. V1 was developed in 2024.
Core V1 is the cleaned public release surface.

It is not a roadmap. It does not mean every historical mechanism is part of the
current public Core. The current release surface is the code, docs, templates,
and tests in this repository.

## Origin

ControlCoding started in 2024 during AI-assisted development on a large
procedural-generation project. The first problem was not that AI could not write
code. It could write code quickly. The problem was that fast edits made the
project easier to damage:

- working functions were overwritten or reimplemented
- existing logic was duplicated under new names
- file organization drifted after many short sessions
- architectural decisions lived in chat history instead of the repository
- the AI restarted from partial context and treated old work as invisible
- large files absorbed new responsibilities because they were easy to edit

The early answer was architectural first. The codebase was divided into
subsystems by topic, feature, and domain. Logic needed by multiple subsystems
was promoted to a shared or higher-level place instead of being copied. Working
functions were treated as protected behavior, not as disposable text. The goal
was to prevent God files, duplicated functions, accidental rewrites, and bug
cascades by creating architectural firebreaks.

The original metaphor was the software condominium: foundations, shared
corridors, rooms, offices, and workspaces. The AI could work quickly inside a
room, but touching a shared corridor or a load-bearing wall required a different
level of awareness.

V1 also used support artifacts such as `CODEMAP`, `CONTRACTS`, `METRICS`,
`ChangeSpec`, and `RenameSpec` to keep that architectural map visible to the AI
and to make changes reviewable.

That support workflow was useful, but too heavy to maintain manually. Core keeps
the parts that survived: project memory, explicit architecture, protected zones,
invariants, reviewable change intent, and guardrails.

## V1 Problems And Core Direction

| Observed failure in early AI coding | V1 mechanism | Core direction |
|---|---|---|
| Code collapsed into God files | Condominium structure, subsystem separation | File organization rules, protected zones, feature/workspace separation |
| Errors propagated across unrelated areas | Architectural firebreaks, module boundaries | Module Feature Lock, boundary hooks, repo-side gates |
| Shared logic was copied into multiple places | Shared/global promotion rule | Shared zones, memory scan, anti-duplication rules |
| AI forgot what already existed | `CODEMAP`, function registry | Canonical context, generated host views, local project memory |
| AI overwrote working behavior | `CONTRACTS`, invariants | Protected zones, tests, review gates, controlled write path |
| AI renamed or moved symbols inconsistently | `RenameSpec` | Planning, review gates, smaller explicit structural changes |
| AI duplicated existing functions | `FUNCTION_REGISTRY` | Memory scan, code search, shared zones, anti-duplication rules |
| Planning and implementation mixed together | Planner / Surgeon split | Plan, review, execute, verify workflow in the active host |
| Rules were advisory only | Process documents | Hooks where available, repo-side gates everywhere else |
| Architecture drift was invisible | `METRICS`, review notes | Fitness checks, CodeWarden evidence, lifecycle memory |

The important change is not that every V1 document became a tool. Many V1
artifacts were intentionally collapsed. The direction is less manual process and
more repository-owned structure.

## What Remained Stable

The following ideas stayed central from the early methodology to Core:

- **Building / condominium architecture**: different areas of the codebase have
  different stability levels and change risks.
- **Protected zones**: stable and shared areas should not be edited with the
  same freedom as feature workspaces.
- **One Concept, One Place**: conceptual cohesion matters more than arbitrary
  file length limits.
- **Model vs View**: authoritative data and derived presentation data must stay
  separate.
- **Domain invariants**: the project must define what must never break.
- **Promotion path**: code should mature from experimental space to feature
  module, then shared utility, then stable foundation.
- **Determinism where the domain requires it**: reproducible systems need stable
  inputs, stable ordering, and testable outputs.

These ideas are not all unique in isolation. They overlap with known software
engineering traditions such as Clean Architecture, bounded contexts, design by
contract, property-based testing, and ADRs. ControlCoding's contribution is
their adaptation to AI-assisted development, where the code writer is often an
agent that can move quickly but has no implicit understanding of which parts of
the project are load-bearing.

## From Historical V2 To Public Core

Older internal drafts often used the phrase "ControlCoding v2" and were written
around Claude Code as the reference host. The public release model is narrower:

| Area | Public Core stance |
|---|---|
| Source of truth | `CONTROLCODING.md` is canonical. Host files are generated views. |
| Host support | Cross-tool model with capability classes and honest limitations. |
| Hooks | Implemented where the host supports them. Repo-side gates remain required. |
| Memory | Local project memory is included, runtime data is gitignored by default. |
| Agents | Optional helpers exist under templates, but hidden orchestration is not the public default story. |
| UI / Studio | Excluded from Core V1. Future/private work only. |
| Benchmarks | Raw benchmark workspaces are excluded. Publish only redacted evidence. |

This split matters. It keeps the public release honest: Core is the
repository-owned structure, not a claim that every future agent or UI layer is
already part of the public product.

## Historical Concepts Worth Preserving

Several concepts from the older methodology are still valuable as public
language, even when they are not all default Core features.

### Mechanical vs Advisory Enforcement

ControlCoding separates rules that can be enforced mechanically from rules that
depend on model compliance.

- Mechanical rules are enforced by hooks, repo gates, tests, or CI.
- Advisory rules live in context files and depend on the AI following them.

This distinction is important because a rule written in Markdown is not the same
thing as a rule that can block a write, reject a commit, or fail a test.

### Temporary Lift Protocol

Protected files sometimes need legitimate changes. The correct pattern is not to
remove protection permanently, but to request a narrow exception:

1. Try an architectural alternative first.
2. Declare the exact file and reason.
3. Keep the change small and mostly wiring-oriented.
4. Do not use the approval to make unrelated edits.
5. Restore or consume the lift immediately.

Core includes scoped lift support for this reason.

### External Connection Control

External AI calls, APIs, subprocesses, telemetry, and provider fallbacks are
side effects. They can leak data, consume money, multiply cost, or make results
hard to reproduce. ControlCoding treats them as architecture and safety
concerns, not as convenience details.

The public rule is simple: external connections should be explicit, authorized,
visible, bounded, and logged.

### Commit Ceremony

The historical methodology used the term "commit ceremony" for the moment when
code, project context, status, and human-readable notes are brought back into
alignment.

Core does not require a heavy ceremony for every small change. The useful
principle remains: after a meaningful architectural change, update the project
context and evidence so the next AI session does not reason from stale facts.

### Debug Escalation

The old methodology described a three-level debug escalation:

1. Hypothesis-first debugging.
2. Visual or runtime feedback when the result cannot be verified from code.
3. Bounded external consultation when the local agent is stuck.

Core keeps the safe version of this idea: manual consultation packets and
optional local tools. It does not make hidden recursive external agents the
default public workflow.

## When ControlCoding Is Useful

ControlCoding is most useful when a project has at least one of these
properties:

- multiple modules that interact
- stable areas that should not be casually rewritten
- long-running AI-assisted development
- domain invariants such as balances, conservation, deterministic outputs, or
  referential integrity
- multiple AI hosts or multiple sessions touching the same repository
- a recurring tendency toward God files, duplicate utilities, or unclear file
  ownership

## When ControlCoding Is Not Needed

ControlCoding is likely unnecessary for:

- one-off scripts
- throwaway prototypes
- single-file projects
- static landing pages without meaningful domain invariants
- experiments where speed matters more than continuity

For these cases, a short project instruction file and normal tests are usually
enough. The full ControlCoding model is meant for projects where structure,
continuity, and controlled evolution matter.

## Public Publication Note

The older methodology draft is useful as source material, but should not be
published verbatim. It mixes historical notes, Claude-specific implementation
details, benchmark observations, future agent ideas, and public methodology.

The safer publication model is what the public repository uses:

- publish Core as inspectable code and docs
- keep raw benchmark notes private or heavily redacted
- keep future Agents and Studio separate from the Core promise
- avoid unsupported market claims
- document limitations before capabilities
