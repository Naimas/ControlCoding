# ControlCoding v2 - Evidence Report

> Objective evidence from real adoption on two independent projects.
> Projects are anonymized for confidentiality. All numbers are real.
>
> Author: Stefano Tonello
> Date: February 2026 (updated March 2026)

---

## 1 Validation Projects

ControlCoding v2 was validated on two real projects, developed in parallel
by the same author with AI assistance.

| | Project A | Project B |
|---|---|---|
| **Language** | C++20, OpenGL 3.3 | Python 3.11 |
| **Domain** | Real-time scientific simulation with 3D rendering | Data analysis pipeline with multi-criteria scoring |
| **Complexity** | 86 source files, dual-topology architecture, rendering pipeline, physics simulation | 72 source files, modular pipeline, LLM integration, multi-stage processing |
| **Development** | ~6 months (from Sep 2025 to Feb 2026) | ~1 intensive week (Feb 2026), building on prior work from Aug 2025 |
| **CC Level adopted** | L2 (hooks + partial invariants) | L2 (hooks + complete invariants) |

---

## 2 Quantitative Metrics

### 2.1 Test Suite

| Metric | Project A | Project B |
|---|---|---|
| Total tests | 310 | 984 |
| Test files | 22 | 46 |
| Framework | Catch2 (C++) | pytest (Python) |
| Execution | Pre-commit hook + manual | Pre-commit hook + CI |
| Execution time | ~15s | ~5m 30s |
| Test growth | 210 to 310 over 6 months (+48%) | 604 to 984 in 5 days (+63%) |

### 2.2 Domain Invariants

| Metric | Project A | Project B |
|---|---|---|
| Dedicated invariant tests | 10 (2 files, growing) | 23 (complete) |
| Structural invariants | 4 (topology, continuity, determinism, area conservation) | 13 (data structure, completeness, partitions) |
| Domain invariants | 6 (physics conservation, round-trip, seam continuity, diffusion, elevation bounds) | 10 (scoring formulas, range, monotonicity) |
| Patterns covered (out of 5 from methodology section 10.4) | 4/5 | 5/5 |

### 2.3 Mechanical Enforcement (hooks)

| Metric | Project A | Project B |
|---|---|---|
| Active boundary hooks | 1 (Edit/Write) | 1 (Edit/Write) |
| Dangerous command hooks | 1 (Bash) | 1 (Bash) |
| Protected zones (DENY) | 1 (stable/) | 3 (core/, rules/, knowledge/) |
| Monitored zones (WARN) | 0 | 3 (same zones, WARN level) |
| Destructive patterns blocked | 6 | 9 |

### 2.4 Architectural Structure

| Metric | Project A | Project B |
|---|---|---|
| Condominium architecture | 4 levels (stable/shared/features/workspace) | 4 zones (core/tools/plugins/ui) |
| Isolated features | 5 (with feature.lock YAML) | 1 main plugin + extensions |
| Shared function registry | 16 entries, 6 consumers | Not needed (codebase small enough) |
| Dependency direction documented | Yes (feature.lock allow/deny/reads) | Yes (core <- tools <- plugins <- ui) |
| Direction violations found by audit | 0 | 4 (tools/ imports from plugins/) |

### 2.5 CC Artifacts in Use

| Artifact | Project A | Project B |
|---|---|---|
| CLAUDE.md | Yes (356 lines, 8+ CC sections) | Yes (331 lines, 8 CC sections) |
| feature.lock | Yes (5 files) | No (not needed at this scale) |
| FUNCTION_REGISTRY | Yes (YAML, 16 entries) | No (not needed at this scale) |
| CC adoption document | Yes (section 18, 6 subsections) | Yes (5 sections + skip list) |
| DevLog | Yes (39 entries) | Yes (21 entries with metrics) |
| Plan mode | Systematic for every non-trivial feature | Systematic |
| Audit script | Available (11 checks) | Available (10 checks) |

---

## 3 Gradual Adoption - Evidence of the Trigger Model

### 3.1 Project A (6 months of development)

Project A demonstrates gradual adoption with real triggers:

```
Phase 0 (month 1-2): Minimal setup
  Adopted: CLAUDE.md, plan mode, pre-commit hook, determinism
  Trigger for Phase 1: "we are about to add the first simulation module"

Phase 1 (month 3-4): Pre-simulation
  Adopted: Abstract interfaces, first 3 invariant tests
  Trigger for Phase 2: "we are implementing the first simulation module"

Phase 2 (month 5-6): With simulation
  Adopted: Separate modules, central container, read-only parameters,
           event bus stub, conservation tests
  Trigger for Phase 3: not yet reached
```

**Key evidence**: components not yet adopted (full Event Bus, formal condominium
structure, 3-gate CI pipeline) have documented triggers and measurable conditions.
No component was implemented "because the methodology says so" - only when
the trigger was actually reached.

### 3.2 Project B (1 intensive week)

Project B demonstrates that CC can be adopted even during rapid development:

```
Day 1: 263 files, 35K lines, 604 tests - initial commit
  Adopted: CLAUDE.md, zone structure, base tests

Day 3: Boundary hooks activated, CC adoption document written
  Trigger: "the codebase has surpassed 40 files and zones have clear boundaries"

Day 5: 23 domain invariants, 984 tests, 15 commits
  Trigger: "we found a bug that an invariant would have caught"
```

**Key evidence**: the feedback-to-invariant cycle worked. A real bug
in the scoring system motivated the creation of 10 scoring invariants that
now permanently protect that logic.

---

## 4 Feedback from Real Adoption

During Project B development, the AI assistant working on the project
spontaneously produced 5 observations about ControlCoding adoption. This
feedback was evaluated and led to 4 concrete improvements in the methodology:

| Feedback | Assessment | Action |
|---|---|---|
| Trigger reminder: how to remind the AI to check triggers | Valid - real gap in the document | Added section 13.5 (3 activation strategies) |
| Question-to-test table for extracting domain invariants | Excellent - the most valuable feedback | Added section 10.4 (5 questions to test patterns) |
| WARN vs DENY principle for hooks | Useful - the distinction was missing | Added section 5.6 (decision principle) |
| Adoption document template per project | Useful - structures the "what we adopt" section | Added section 13.6 (3-section template) |
| Facts vs recommendations distinction in subagents | Valid - important operative principle | Added section 9.6 (delegation boundary) |

**Key evidence**: the methodology improved *during* real adoption,
not in the abstract. Each new section corresponds to a problem encountered in practice.

---

## 5 Real-World CC Activation Patterns

Both projects produced concrete examples of CC practices preventing bugs,
enabling ambitious refactoring, and guiding architectural decisions.
All examples are anonymized but factual.

### 5.1 Invariants Before Refactoring (Project A)

Before a major restructuring (70 files moved between zones in 7 atomic
commits), the team wrote domain invariant tests first: noise determinism
(bit-exact comparison with same seed), area conservation across LOD
levels (< 0.1% drift), elevation bounds, and diffusion conservation.

Result: 310 tests passed after the restructure, zero regressions. Without
the invariant safety net, subtle physics breaks would have gone undetected
until visual artifacts appeared in the rendering.

### 5.2 Truth vs View Caught a Real Bug (Project B)

A quality check function used a per-record field populated by an LLM
(unreliable, the "View") instead of the authoritative global parameter
computed by a deterministic detector (the "Truth"). This caused a
regression in one specific check that passed before but failed after
an unrelated improvement to the LLM prompt.

The CC principle "Truth vs View" directly identified the root cause:
the function was reading the View when it should have been reading the
Truth. Fix: rewire the function to use the authoritative parameter.

### 5.3 Design Document Before Code (Project A)

Before implementing a complex physics simulation module, the team wrote
a complete design document covering: scientific theory, mathematical
equations, comparison with published approaches, and explicit scope
limits ("no X, no Y, only the essential for a recognizable result").

This document served as the specification against which the implementation
was validated. CC Plan Mode at full strength: not just "plan the task"
but "write the specification, then implement against it."

### 5.4 Organic Adoption Recognized (Project B)

The CC adoption document for Project B explicitly states: "the project
was already at CC level ~1.5 without knowing it." The project already
had conventional commits, per-commit devlog entries, quality checks as
functional tests, and multiple backends for resilience.

Formalizing CC added: boundary enforcement hooks, domain invariant tests
(distinct from existing functional tests), and documented principles in
CLAUDE.md. The methodology recognized and built on existing good practices
rather than replacing them.

### 5.5 Skip List as Evidence of Gradual Adoption (Project B)

The CC adoption document lists 6 components explicitly NOT adopted, each
with a concrete reason:

- Feature locks: only 1 active plugin, not needed until 3+
- Event Bus: all logic within one plugin, no cross-module reactions
- Function Registry: codebase small enough to navigate directly
- Seed-based determinism: not a simulation, determinism comes from rules
- Formal ChangeSpec: single developer + AI, DevLog is sufficient
- PostToolUse logging: git log is sufficient as audit trail

This demonstrates that gradual adoption is not theoretical advice. A real
project evaluated each CC component against its actual needs and made
explicit accept/reject decisions with documented rationale.

### 5.6 Scope Boundaries as a CC Practice (Project A)

Multiple design documents explicitly state what will NOT be implemented
and why. Examples from simulation modules:

- "No slab pull, no complex rifting, no multi-layer subduction"
- "Nearest-neighbor search is brute-force O(N); accepted at current scale,
  kd-tree documented as future solution"
- "Single-threaded by design; no mutex, just a boolean flag with exception"

Each boundary is a deliberate architectural choice, documented and defended.
This prevents scope creep and premature optimization, both common failure
modes in AI-assisted development where the AI is eager to "improve" things.

### 5.7 Feature Locks Enforce Unidirectional Dependencies (Project A)

The feature.lock YAML files define allow/deny/reads rules per module.
Example: a simulation module can READ data structures from another module
but cannot WRITE to it. This enforces one-way dependency at the tool level,
not just by convention.

If an AI assistant tries to create a circular dependency, the boundary hook
blocks the edit before it happens.

### 5.8 Documentary Rule Bypass Under Pressure (Project A)

During implementation of a visualization feature, the AI hardcoded a
`set*()` call directly in the application init code, bypassing the
established UI state pattern (AppState field -> UI toggle -> render
loop sync -> renderer). The feature worked, but the user had no way
to turn it off - the setting was always on, with no toggle.

This violated two rules documented in CLAUDE.md:
- "Do NOT add new logic to main.cpp" (the file is a known God Object)
- "New UI parameters must go through AppState, not as globals in main.cpp"

**Why CC didn't catch it**: main.cpp lives in features/, not in stable/
or shared/. The boundary hook only monitors zone-level protection. The
rules about main.cpp were purely documentary (L1) - written in CLAUDE.md,
with no mechanical enforcement (L2).

**How it was detected**: in a later session, the AI noted the hardcoded state
but did not flag it as a rule violation. The developer, reading the agent's
reasoning, made the CC connection and identified the methodology violation.
The CC framework gave the developer the vocabulary and criteria to challenge
the agent's shortcut.

**Root cause**: long session with multiple features to implement. Under
time pressure, the agent took a shortcut that worked functionally but
broke the architectural pattern. Documentary rules degrade when the
agent is under cognitive load.

**Fix applied**: removed the hardcode, added the parameter as an AppState
field, created a UI toggle, wired the render loop sync.

**Lesson**: files with known constraints need a WARN hook even when they
live in features/. The combination of L1 (CLAUDE.md says what to do) +
L2 (hook forces the agent to pause) is much stronger than L1 alone.
This led to the "file-level WARN" concept: adding specific files
to the hook's PROTECTED_ZONES list with `warn` action and a description
of the constraint.

---

## 6 Comparative Analysis: With CC vs Without CC

There is no formal control group (same project developed with and without CC).
However, Project A offers a longitudinal comparison: the first 2 months were
developed with CC v1 (more procedural, less mechanical), the following 4 months
with CC v2 (hooks, invariants, gradual adoption).

Qualitative observations (not formal metrics):

| Aspect | Pre-CC v2 (months 1-2) | With CC v2 (months 3-6) |
|---|---|---|
| Regressions after refactoring | Frequent, discovered manually | Rare, caught by invariant tests |
| AI modifying wrong files | Occasional | Zero (boundary hook blocks it) |
| Time spent "fixing up" after AI session | ~20% of session time | ~5% of session time |
| Confidence in ambitious refactoring | Low (fear of breaking things) | High (invariants as safety net) |
| Decision documentation | Sporadic | Systematic (DevLog + plan mode) |

**Honesty note**: these are subjective estimates by the author, not instrumental
measurements. They are reported as directional indicators, not scientific proof.

---

## 7 Adoption Costs

### 7.1 Setup Time

| Activity | Estimated time | One-time / Recurring |
|---|---|---|
| Write initial CLAUDE.md | 1-2 hours | One-time (then incremental updates) |
| Configure boundary hook | 30 min | One-time |
| Configure dangerous commands hook | 15 min | One-time |
| Write first 3 invariant tests | 1-2 hours | One-time (then incremental growth) |
| Write CC adoption document | 30 min | One-time |
| **Total initial setup** | **3-5 hours** | |

### 7.2 Recurring Cost

| Activity | Frequency | Time |
|---|---|---|
| Update CLAUDE.md | On each architectural change | 5-15 min |
| Add invariant tests | On each feature with domain properties | 15-30 min |
| Update feature.lock | On each new feature (if used) | 5 min |
| Update adoption document | On each milestone | 10 min |
| **Average weekly overhead** | | **~30 min** |

### 7.3 Zero Cost

Some CC practices have no additional cost because they replace activities you would do anyway:

- **Plan mode**: replaces the "think before you code" that an experienced developer already does
- **Hooks**: once configured, they work without intervention
- **DevLog**: replaces detailed commit messages
- **CLAUDE.md**: replaces the documentation that the AI should read anyway

---

## 8 Known Limitations

1. **Sample of 2 projects, 1 developer**. The evidence is real but not statistically
   generalizable. Independent adoptions from other developers would be needed.

2. **No control group**. We cannot claim "CC reduces bugs by X%" with scientific
   rigor. The observations are before/after on the same project.

3. **Developer = methodology author**. The author knows CC intimately. The
   experience of an external adopter could be different (learning curve,
   process resistance, divergent interpretations).

4. **Hook block metrics not tracked**. We know the hooks are active, but we
   don't count how many times they blocked an action during a session. This is
   data that audit scripts could collect in the future.

5. **Confirmation bias**. The author is motivated to demonstrate that the
   methodology works. The qualitative observations (section 6) should be read
   with this filter in mind.
