# Review Check: CCTEST-3dcrawler-CC-v11

## Purpose

This note reviews the same external run described in
`2026-04-11-cctest-3dcrawler-v11-scorecard.md`, but with a different question:

- not just "did the project bootstrap/build/test/run?"
- also "did the session execute the requested ControlCoding workflow and close the prompt requirements?"

The answer is mixed: the run is a strong technical proof for `Codex CLI + Core`,
but it is **not** a full closure proof for every workflow step and every prompt
requirement.

## Overall Verdict

- `Technical run`: **PASS**
- `Prompt/workflow closure`: **PARTIAL**
- `Full benchmark A/B conclusion`: **NOT YET**

Read this file together with the scorecard. The scorecard proves viability. This
review matrix shows what was actually closed, what was only implied, and what
was never explicitly proven.

## A. ControlCoding Workflow Review

### A1. What the log proves

| Workflow requirement | Status | Evidence | Note |
|---|---|---|---|
| Base setup wizard was run | `PROVED` | session log commands | `cc.py setup --project-root .` is explicitly recorded |
| Engagement wizard was run | `PROVED` | session log commands | `cc.py setup --engagement --project-root .` is explicitly recorded |
| `cc doctor` was run after bootstrap | `PROVED` | session log commands | explicit doctor step recorded |
| Benchmark used `Codex CLI` | `PROVED` | session log wizard choices | host recorded as `Codex CLI` |
| Benchmark used `Core` tier | `PROVED` | session log wizard choices | tier recorded as `Core` |
| Required CC files were generated | `PROVED` | session log + workspace | `CONTROLCODING.md`, `AGENTS.md`, `.claude/*` recorded |
| Runtime output was verified beyond compile-only | `PROVED` | report + screenshot + manual GUI note | this is stronger than a build-only claim |

### A2. What `Core` really includes

Current runtime mapping in `templates/scripts/control_plane_utils.py` is:

- `Core` includes `codewarden`, `session`, `verification`, `auto_import`
- `Agents` adds `consultant`, `planner`, `tandem`, `concierge`
- `Studio` adds the full UX layer on top of `Agents`

That means:

- missing gameplay features in this run are **not** explained by "it was only Core"
- `Agents` would add specialist help and orchestration
- `Agents` would **not** magically unlock doors, torches, shield logic, or visual polish

### A3. What the log does not prove

| Workflow requirement | Status | Evidence gap | Note |
|---|---|---|---|
| Human-visible wizard guidance was reviewed step by step | `NOT PROVED` | no wizard transcript or screenshots | the log records commands and final choices, not the interactive wording quality |
| `CONTROLCODING.md` / `AGENTS.md` were fully read before planning | `NOT PROVED` | not directly observable | the prompt required it, but the session log does not prove the reading step |
| CodeWarden visibly intervened during the run | `NOT PROVED` | no logged CodeWarden event | `Core` enables it structurally, but this run did not explicitly exercise a violation |
| Session continuity tooling was visibly used | `NOT PROVED` | no logged resume/decision handoff usage | enabled by tier, not observed in this single run |
| Verification tracker / auto-import were visibly inspected | `PARTIAL` | runtime verify exists, tracker behavior not reviewed | the run used verification output, but not a documented criteria-tracker audit |
| Prompt state machine checkpoints were executed as explicit named phases | `PARTIAL` | planning/review phases were not instrumented separately | bootstrap, coding, testing, debugging, and final state are visible; planning/review are only implied |

## B. Prompt Requirement Review

Status legend:

- `PROVED`: directly evidenced by the run, logs, tests, report, or source
- `PARTIAL`: implemented or implied, but not cleanly closed by evidence
- `NOT PROVED`: no solid evidence from this run
- `OUT OF SCOPE`: clarified in-session as not actually required by the prompt

### B1. Core product requirements

| Prompt requirement | Status | Basis | Note |
|---|---|---|---|
| 3D dungeon with walls, floor, ceiling, corridors, rooms | `PROVED` | map layout + renderer + runtime rendering note | authored map and textured geometry exist |
| Textured world rendering | `PROVED` | source + log | textures were loaded and the log records textured rendering |
| First-person camera and movement | `PROVED` | verification report | `cameraVerified` and `movementVerified` are true |
| Collision against walls | `PROVED` | tests + source | invariant test exists and passed |
| Doors or gated transitions | `PROVED` | verification report + tests + map | `doorsOpened = 1` and a keyed door exists |
| Enemies/NPC entities in dungeon space | `PROVED` | source + map | enemy entities exist in authored data and runtime code |
| Collectible items | `PROVED` | source + map | gold/potion items exist and collection logic is present |
| Chests or interactable loot containers | `PROVED` | verification report + tests | chest open path is verified |
| Secret passages or hidden interactables | `PARTIAL` | source + map | secret system exists, but `secretsRevealed = 0` in the automated run |
| HUD with player state information | `PARTIAL` | source + session summary | present in code, but not separately validated in the benchmark evidence |
| Minimap or equivalent navigation aid | `PARTIAL` | source + session summary | present in code, but not separately validated in the benchmark evidence |
| Visual feedback effects where feasible | `PARTIAL` | source + manual positive note | some feedback exists, but this run does not close quality criteria rigorously |
| Lighting or atmosphere where feasible | `PARTIAL` | source + session summary | present as a feature direction, but no explicit quality acceptance check was closed |
| Player weapon visible on screen (`sword and/or shield`) | `PARTIAL` | source | sword rendering code exists; no explicit evidence of shield support and no dedicated screenshot acceptance check |
| Attack/use key support | `PARTIAL` | source + report | interaction path is proved; attack exists in code, but `attacksLanded = 0` in automation |
| Save-ready architecture | `PARTIAL` | source | JSON serialization/state export exists, but no save/load benchmark step was run |
| At least 5 invariant-oriented tests | `PROVED` | test log | the run passed `7/7` tests |
| Visual output must be verified, not just compile success | `PROVED` | report + screenshot + manual GUI review | clearly satisfied |

### B2. Clarified requirements that should not be over-scored

The session log also records a requirement clarification. The following were
explicitly treated as **not mandatory** for this prompt:

| Item | Status | Why |
|---|---|---|
| Photorealistic textures | `OUT OF SCOPE` | not explicitly required by the prompt |
| Wall-mounted torches as a mandatory element | `OUT OF SCOPE` | not explicitly required by the prompt |
| Shield parry mechanic | `OUT OF SCOPE` | not explicitly required by the prompt |

Important consequence:

- if torches, shadows, or shield-parry are absent, that is **not automatically a prompt failure**
- if they were wanted as benchmark criteria, they must be written into the benchmark definition explicitly

## C. What This Means for Benchmark Framing

This run is defensible as evidence for:

- fresh-folder bootstrap of the new CC flow
- `Codex CLI + Core` viability
- real build/test/runtime success in a non-trivial project

This run is **not** enough to claim:

- full workflow observability of every CC subsystem
- full prompt closure on every feature-quality dimension
- A/B outcome comparison against previous Claude-based benchmark runs

To make it comparable against the older Claude benchmark corpus, the next step
should be a normalized comparison sheet with the same scoring frame:

- setup friction
- prompt compliance
- feature closure
- architectural cleanliness
- runtime quality
- residual gaps

## D. Recommended Next Artifact

For this benchmark family, the clean next step is:

- keep the technical scorecard as-is
- keep this review check as the prompt/workflow audit
- add one comparison note against the earlier Claude runs, using the same rubric for both
