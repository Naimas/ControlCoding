# ControlCoding

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue) ![License Source Available NC](https://img.shields.io/badge/License-Source--Available%20Noncommercial-yellow)

**Repository governance for AI-assisted development in complex, long-lived codebases.**

ControlCoding is a source-available reference implementation and toolkit for
keeping AI-assisted changes inside explicit architectural, authorization, and
verification boundaries.

## Who It Is For

| Use ControlCoding when | The full framework is usually unnecessary when |
|---|---|
| The codebase is long-lived, modular, or governed by domain invariants | The project is a disposable prototype, small CRUD app, or familiar one-off build |
| AI work spans multiple sessions, contributors, or coding hosts | One short session and ordinary tests provide enough control |
| Some files or behaviors require protected boundaries and auditable release evidence | The cost of a structural mistake is lower than the governance overhead |

Start with the smallest useful maturity level. Adopt stronger gates only when
the project risk justifies them.

## Three Differentiators

1. **One canonical project contract.** `CONTROLCODING.md` is the source for
   host-native views such as `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and
   `.clinerules`.
2. **Honest enforcement boundaries.** Every protection is classified as
   mechanical, conditional, advisory, or unavailable instead of being presented
   as stronger than the host can enforce.
3. **Evidence before release claims.** Repository checks, invariants,
   verification contracts, and receipts connect public claims to inspectable
   results.

## Quick Example

```bash
cd /path/to/your-project
python /path/to/ControlCoding/scripts/cc.py setup --project-root .
python /path/to/ControlCoding/scripts/cc.py setup --engagement --project-root .
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

The result is a canonical project context, matching host adapters, local
configuration, and a `doctor` report showing which protections are real for the
selected host.

## Evidence

Maintainer-run case studies cover two AI-assisted projects: a C++20/OpenGL
codebase used continuously for six months and a Python scoring pipeline used
intensively for one week. They demonstrate applied use, not independent or
universal validation. See the [anonymized evidence report](docs/evidence.md) for
metrics, adoption patterns, costs, and limitations.

Start with the [installation guide](docs/install-controlcoding-on-your-project.md)
or [Quick Start](docs/quick-start.md). For scope and licensing boundaries, see
the [Release Model](docs/release-model.md) and [LICENSE](LICENSE).

## 3.0.1 Public Release

ControlCoding V1/Core `3.0.1` is the current public release. It incorporates a
deterministic correction to the stage identity regression test. That correction
did not modify production code.

The clean public repository begins with the verified `3.0.1` source snapshot.
Earlier private development commits and tags remain outside the public
repository.

Version 3 is a major release line because four public behaviors differ from the
stable `v2.5.2` line:

- `cc replace start/status/complete` is no longer a public command surface.
- `cc benchmark compare` uses positional baseline and current inputs, while
  benchmark run and report defaults are now `benchmarks/run.json` and
  `benchmarks/report.md`.
- `cc organize` is preview-only by default and requires `--apply` to change
  files.
- `cc resume` prints a provider-neutral context brief and does not launch a
  provider or subprocess.

Migration details, including the explicit adoption path for historical host
adapters, are in the [Quick Start](docs/quick-start.md),
[cross-tool guide](docs/cross-tool-guide.md), and
[CLI reference](docs/ccdocs/tools-reference.md).

## What Setup Creates

After running the quick example, inspect:

- `CONTROLCODING.md` as the canonical project rules
- the generated host adapter for your tool, such as `AGENTS.md`, `CLAUDE.md`,
  `GEMINI.md`, or `.clinerules`
- `.controlcoding/cc_config.json` for local configuration
- the `doctor` output, especially whether each gate is mechanical,
  conditional, advisory, or unavailable

Generated host files, views, receipts, packets, and dashboards are projections
or evidence. They help the AI and the maintainer work from the same state, but
they do not replace the canonical project context, source code, tests, or human
review.

## What ControlCoding Is

ControlCoding is a framework for keeping AI-assisted "vibe coding" structured
as a project grows. It gives AI coding hosts a canonical project brief, file
organization rules, protected zones, repository-side checks, optional inline
hooks, local project memory, and verification habits so the model does not
treat a large codebase as a blank canvas every session.

It is built for developers who want AI speed without losing control of project
structure: stable modules stay stable, files have clear places to live,
architecture boundaries are explicit, and large features are less likely to
collapse into God files, duplicated logic, or scattered one-off scripts.

## Canonical Context And Project Setup Docs

`CONTROLCODING.md` is ControlCoding's host-agnostic project context source. It
is the source used to generate host-specific files such as `CLAUDE.md`,
`AGENTS.md`, `GEMINI.md`, or `.clinerules`. Those generated files adapt the same
rules to the AI tool that will read them.

`CONTROLCODING.md` should contain the working rules the AI needs at session
start: project identity, architecture rules, module boundaries, protected zones,
domain invariants, operative rules, and current focus. It is not meant to be the
entire master design document for a complex product.

Project design and development documentation is a separate layer. When the user
has an initial brief, requirements file, spec, README, or design document,
ControlCoding can use it as source material for a governed project setup. When
the user has only an idea, ControlCoding can guide the missing questions before
implementation starts.

That project setup flow is handled by `cc setup-project`, not by the base
install flow. Its purpose is to organize the project into project-definition
docs, design docs, implementation docs, acceptance and verification docs, and
contracts where the project needs machine-readable baselines.

In short: `CONTROLCODING.md` tells AI hosts how to work inside the project. The
design and implementation documents define what the project is, what should be
built, in which order, and under which acceptance criteria.

## The Problem

AI coding assistants handle simple projects well. A website, a portal, an app with a few modules - the AI can build it alone, self-correcting as it goes. The code is linear, dependencies are few, and when something breaks, the AI notices immediately.

The problem arrives with complex systems: dozens of interacting modules, shared state, domain invariants, overlapping subsystems. Here the AI makes structural errors:

- **Puts logic in the wrong place** because it doesn't "see" the architecture
- **Breaks stable modules** because it doesn't know they're stable
- **Bypasses architectural patterns** because it takes shortcuts under pressure
- **Loses coherence across long sessions** as context drifts

There are two kinds of AI-assisted projects:

- **Autopilot projects** - CRUD apps, portfolios, dashboards, clones of well-known patterns. The AI has seen 10,000 of these in its training data. It knows what to do. It self-corrects. You don't need guardrails.
- **Directional projects** - the developer has a specific architecture, specific domain invariants, specific decisions that diverge from the AI's defaults. The AI is a powerful executor but must stay on rails defined by the human. This is where ControlCoding applies.

AI models get smarter every month, but structural complexity doesn't get solved by intelligence. A smarter model makes fewer syntax errors but can still break the architecture if it has no constraints. Prompt engineering alone doesn't fix structural problems. You need explicit control points: native hooks where the host supports them, and repo-side gates plus verification where it does not.

## The Solution

ControlCoding doesn't replace the AI's capability - it contains it. It tells the AI: "work freely here, don't touch there", "if you modify this file, stop and explain", "these domain properties must always be true".

It works across AI coding assistants through official CLIs, official APIs, local runtimes, and vendor-approved connectors. On Claude Code and Cline, the inline gate can run before a write. On Codex CLI, the real protection path is repo-side: repo boundary gate, review gate, and verification gate. On instruction-first hosts such as Gemini CLI, Cursor, Copilot, Aider, or whatever comes next, the structure stays the same but enforcement is repo-side and review-driven. ControlCoding does not claim Claude-style pre-write parity on hosts that do not expose native inline hooks. No install required. Python 3.10+ for hooks. No vendor lock-in.

### Authorized Interfaces Only

ControlCoding is AI-agnostic, not credential-agnostic.

- Official local CLIs such as Claude Code and Codex CLI are supported entrypoints
- Official APIs and local model runtimes are supported integration paths
- Vendor-approved connector systems are acceptable when used inside the vendor's own product flow
- The user's host surface (Claude Code, Codex CLI, VS Code, Cursor, or another chosen tool) is external to ControlCoding
- The current V1/Core source package does not include the desktop Studio/gateway UI
- Any future ControlCoding-owned UI must use official API or local-runtime integration and preserve an explicit primary-surface boundary
- ControlCoding does **not** support embedding Claude.ai or ChatGPT consumer login inside a CC-owned interface
- ControlCoding does **not** support reusing consumer OAuth/session tokens across third-party products
- ControlCoding does **not** support hidden automatic agent calls with opaque backend, cost, or permission boundaries
- Non-interactive CLI modes such as `claude -p` are acceptable only when used through the official CLI provided by the vendor

### Public Usage Advice

For public-safe usage, keep one rule simple:

- one project/session has one visible **Primary Surface**
- every other surface is **Observer-only**

Typical examples:

| Scenario | Primary Surface | Public-safe status |
|---|---|---|
| User works in Claude Code, Codex CLI, or another official host | Official host | Recommended |
| User works through an official API or local runtime integration | The explicitly selected integration surface | Supported when authorization is explicit |
| Two surfaces both dispatch prompts or specialist calls | Ambiguous | Not supported |
| A third-party surface embeds consumer login or reuses consumer session tokens | Ambiguous | Not supported |

Practical advice for adopters:

- use your own official host account when working through official CLI or desktop products
- use your own API keys or your own local runtime for an authorized integration
- do not use CC as a proxy for someone else's consumer account
- do not treat "works with tool X" as permission to bypass that tool's supported auth model
- if more than one surface is open, pick one primary surface and demote the rest to observers
- if another CC-aware surface wants to become primary, require an explicit takeover instead of silently switching authority
- use `cc surface request-takeover ...` and `cc surface resolve-takeover ...` to make that handoff explicit in the local authority lock
- when authority is ambiguous, fail closed and keep every non-primary surface observer-only
- if you automate host attachment, register it explicitly via `cc surface claim ...` or `cc surface observe ...` instead of assuming CC can infer intent
- for wrappers/launchers, prefer `cc surface run ... -- <host command>` so claim, heartbeat, and release happen through one local entrypoint
- sequential host hopping is fine if you keep one primary host per session and resync the target host context before working
- during setup, let ControlCoding generate local project launchers and editor tasks instead of inventing custom wrappers by hand

ControlCoding aims to make the supported path clear through surface authority commands and local lock state. It does not claim to remove all policy or account risk in every vendor ecosystem.

### Public Product Tiers

ControlCoding's public packaging is:

| Tier | What it includes | Typical use |
|---|---|---|
| **Core** | The V1 source package: public contract, hooks, CLI/setup, memory, tests, and verification | Default baseline for teams or solo developers |
| **Agents** | A possible additive helper layer with separate consent and verification requirements | Not a package shipped by this release |
| **Studio** | A possible desktop UI layer | Not shipped by this release |

Current implementation note:

- the runtime still uses engagement config and UI modes under the hood
- those are implementation knobs, not the preferred public product menu
- for the current public release order, keep the promised `Agents` story explicit and host-chat-defined: prompt, folder, and behavior contract first
- API-backed routed specialists belong to the later Version II path, not the current release baseline
- `local_only` is a policy boundary, not a provider selection; it never selects Ollama or any other adapter
- tandem stays off until both backend fields are configured explicitly; the two roles may use the same registered backend when that is an intentional choice
- external calls expose backend, model, consent context, and a compatible `costStatus`; unavailable configured backends fail without a silent provider fallback

Recommended release order:

- publish `Core` first as the default serious baseline
- keep `Core + manual consultation` inside that `Core` release story
- if `Agents` are exposed in the release story, keep them explicit on the chosen official host through prompt, folder, and behavior contracts
- move API-backed routed specialists to the later Version II path
- keep `Studio` / CC UI as the last optional extra, not as part of the current release path

For the public packaging split and the current release position of each layer, see [docs/release-model.md](docs/release-model.md).

### Core Components

| Component | What it does | Cost |
|---|---|---|
| **Canonical Context Source** | `CONTROLCODING.md` holds the project rules once; host-native files such as `CLAUDE.md` and `AGENTS.md` are derived from it | Free, just markdown files |
| **Condominium Architecture** | Separates code into stable/shared/features/workspace zones | Free, just conventions |
| **Hooks** (boundary enforcement) | Mechanically prevents AI from touching protected zones | Free, Python scripts |
| **Invariant Manifest** | `cc invariants` elicits domain properties, records the project properties that must not break, reports which properties are protected, diagnoses whether they are only documented, locally executable, or CI-wired, runs executable invariant commands, and can wire a CI gate | Free, local commands |
| **Verification Contract** | `cc verify` defines required targeted, regression, and invariant checks, runs them, and stores local receipts | Free, local commands |
| **Promotion Gate** | `cc promote` plans, checks, and applies staged movement from workspace to features to shared to stable, with ADR generation for stable promotion | Free, local commands |
| **Adoption Triggers** | Clear conditions for when to add each guardrail | Free, just a table |
| **Commit Ceremony** | AI realigns code plus local working artifacts for every significant commit | Free, AI-automated |
| **Debug Escalation** | 3-level system: protocol, visual feedback, external consultation | Free (L1-L2), optional API cost (L3) |
| **External Consultation** | MCP server with 5 specialized roles and agent-mode (web search) | Official CLI session, official API, or Ollama |
| **Session Management** | Automated local STATUS/devlog continuity and session history via MCP | Free, fastmcp required |
| **Project Memory Engine** | Local `.controlcoding/` development memory for project context, docs, decisions, ideas, consults, agent runs, semantic chunks, correlation suggestions, sparse vector search, and impact views | Free, Python stdlib SQLite and Markdown |
| **ControlWork Project Plane** | Embedded `CONTROLWORK.md` and `.controlwork/` work memory for research, requirements, source summaries, plans, scoped context packets, handoff packets, Obsidian projection, categories, checkpoints, and standalone ControlWork compatibility | Free for noncommercial use; commercial use follows ControlWork terms |
| **Multi-Agent Bridge** | Filesystem-based communication between AI sessions | Free, fastmcp required |
| **Concierge Pattern** | Single-voice orchestration pattern for internal/lab agent surfaces: specialists report back through the Concierge, and a visualizer can mirror a routed local transcript without replacing the host chat. The public default remains host-mediated helper workflows | Free, uses existing backends |
| **Planner** | Maieutic expansion of ideas into phased plans with acceptance criteria | Free, Concierge internal function |
| **Tandem Debate** | Two AI backends debate architecture decisions, identify agreements/divergences | Backend cost x2 |
| **Runtime Engagement** | Internal 4-mode runtime config (`conservative` to `full`) that currently controls component activation behind the public tiers | Free, just config |

All listed local components are free for noncommercial use. Commercial use,
resale, paid hosting, paid consulting packages, commercial SaaS use, or
inclusion in a commercial product or service requires a separate written
commercial license from Stefano Tonello.

### Memory Layers

ControlCoding uses two local memory layers that should not be confused.

| Layer | Storage | Purpose | Release/runtime status |
|---|---|---|---|
| **Project Memory Engine** | `.controlcoding/memory/` plus generated `.controlcoding/views/` | Development memory for coding workflow: decisions, notes, plans, consults, agent runs, semantic chunks, graph edges, sparse vector search, impact/context views | Implemented as code. A fresh clone does not contain `memory.db`; initialize it with `cc memory init` in an adopted project. |
| **ControlWork Project Plane** | `CONTROLWORK.md` plus `.controlwork/` | Portable work/project memory for research, requirements, source summaries, work decisions, plans, outputs, handoff, and chat continuity | Embedded when explicitly initialized, and also available as standalone ControlWork. ControlWork keeps its own product identity and compatible memory contract. Local `.controlwork/` state is not the Project Memory Engine. |
| **Application-owned memory** | Defined by the application | Runtime user data, domain memory, app RAG corpora, app vector stores, application knowledge graphs | Outside ControlCoding ownership. ControlCoding may reference project documents, but the application owns runtime truth. |

The older `document-only` Work Memory profile is a ControlCoding memory mode
under `.controlcoding/`. Standalone ControlWork is different: it is the
portable Project Plane under `.controlwork/` and `CONTROLWORK.md`.

For clean public releases, ship the implementation and docs, not local runtime
state. Do not copy initialized `.controlcoding/` or `.controlwork/` workspace
state into a clean source release unless the release plan explicitly says so.

### What it looks like in practice

When the AI tries to edit a protected file, the hook blocks it mechanically:

```
> Edit src/core/physics_engine.py

{"decision": "block", "reason": "CONTROL CODING: Core engine.
This zone is DENY-protected. Modification blocked.
If this modification is genuinely necessary (bug fix, design flaw),
explain to the user WHAT you need to change and WHY, then run:
python hooks/request_lift.py --file <path> --reason \"<why>\""}
```

The AI cannot proceed until a human approves the lift. No amount of prompt creativity bypasses this - it is a mechanical gate, not an instruction.

### Maturity Levels

| Level | Name | What you have | Typical trigger |
|---|---|---|---|
| **L1** | Documented | Canonical context source with rules (> 50 actionable lines), synced into the host-native file you use | Any project with AI assistance |
| **L2** | Enforced | Hooks that mechanically prevent boundary violations | First time AI breaks stable code |
| **L3** | Verified | 3+ domain invariants with automated testing | First "it compiles but it's wrong" bug |
| **L4** | Isolated | Feature locks, full zone isolation, promotion path | 3+ independent modules or contributors |

## Quick Start

Primary install guide: [Install ControlCoding On Your Project](docs/install-controlcoding-on-your-project.md)

If you just downloaded ControlCoding and want to apply it to **your own project**, the normal path is:

```text
Install ControlCoding in this project using the local ControlCoding repository.
Read INSTALL_WIZARD.md and follow it.
Ask one question at a time.
```

That is the public user-facing ceremony.
Only use raw commands yourself when the host truly cannot execute local commands.

Backend apply sequence:

```bash
cd /path/to/your-project
python /path/to/ControlCoding/scripts/cc.py setup --project-root .
python /path/to/ControlCoding/scripts/cc.py setup --engagement --project-root .
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

That is the default serious baseline. Do **not** start with manual file copying unless you have a specific reason.

Canonical chat contracts:

- [`INSTALL_WIZARD.md`](INSTALL_WIZARD.md)
- [`PROJECT_SETUP_WIZARD.md`](PROJECT_SETUP_WIZARD.md)

### What the user will see

The install assistant flow asks for:

- project identity
- user host: `Codex CLI`, `Claude Code`, `Gemini CLI`, `Cursor`, and so on
- ControlCoding usage model: `Core`, `Core + manual consultation`, `Agents`, or `Studio`
- documentation ownership: `managed` vs `project_managed`
- host workflow guidance

The base install flow no longer asks for project framing or kickoff-doc content.
That now belongs to the separate project-setup flow:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --project-root .
```

`cc.py setup-project` asks for:

- source mode: existing brief/doc vs interactive guidance
- project framing in plain language
- implementation stack and system shape as optional/provisional inputs

The project-definition package follows the usage model already chosen in base setup:

- `Core`: direct structured planning by the main chat
- `Core` with manual consultation: same direct kickoff, but one bounded external consultation path is allowed if a blocking uncertainty remains
- `Agents`: specialist-assisted planning with explicit host-chat helper roles and explicit provenance
- `Studio`: future/internal UI path, not part of the current public release story

If you run `setup-project`, CC can also scaffold the first engineering documents from that input.

CC working documents stay local-only in the public install path. The base setup no longer asks the adopter to decide that.

No bundled graphical installer is included in this release. Project framing
and kickoff docs remain a separate `setup-project` step after installation.

`cc.py setup --engagement` opens the tier/runtime wizard. It asks for:

- product tier: `Core`, `Agents`, or `Studio`
- backend policy
- optional tandem/local model details when relevant
- on `Agents` / `Studio`, an explicit specialist/backend consent matrix:
  active specialist paths, backend/model per path, permission envelope,
  execution mode, and call limits
- advanced packs or specialist/backend-heavy paths only when they are actually relevant

`Core + manual consultation` stays separate from that matrix. It is a bounded,
user-mediated planning escape hatch, not hidden specialist orchestration.

Current release freeze:

- `Core + manual consultation` is the public manual second-opinion path
- the current public `Agents` path is explicit helper work on the chosen host through prompt, folder, and behavior contracts
- API-backed routed specialists belong to the later Version II path
- `Studio` / CC UI is deferred and remains a later optional extra

`cc.py doctor` then validates that the installation is coherent.
For the selected host it reports the four gate slots explicitly:

- `inline gate`
- `repo boundary gate`
- `review gate`
- `verification gate`

For non-inline hosts, `doctor` will not report the project as healthy if the real repo-side boundary path is missing.

For non-inline hosts there is now also an optional advanced path:

```bash
python /path/to/ControlCoding/scripts/cc.py write-path enable --mode patch_gateway --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path prepare --patch-file proposed.diff --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path apply --patch-file proposed.diff --manifest-id <manifest_id> --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path status --project-root .
```

This does not create fake host parity. It enables an explicit, CC-owned patch
gateway that can validate protected-zone writes before `git apply` lands them,
but only for writes that are intentionally routed through `cc write-path`.
If you enable `--require-manifest`, apply becomes a two-step flow: `prepare`
creates a signed manifest from the exact patch, then `apply` verifies the patch
hash/file list plus the current baseline state of the touched files before
running `git apply`.
If you also enable `--preflight-fitness`, `apply` first replays the patch in a
shadow git worktree and runs `fitness_check.py` there, so hard architectural
violations can block the real apply before it touches the live tree.
Each terminal `prepare/check/apply` outcome also writes a local receipt in
`.controlcoding/write_path_receipts/`, so the CC-owned path is auditable without
pretending to intercept normal editor writes.
You can inspect them with `cc write-path receipts` and
`cc write-path receipt --receipt-id <id>`.
For a standalone local evidence artifact, use
`cc benchmark-matrix report-local`.

For the implemented `Agents / human_mediated` bridge path, the bounded manual
loop is:

```bash
python /path/to/ControlCoding/scripts/cc.py consult-packet create --project-root . --role architect --objective "Validate repo-side boundary split" --question "Should gateway branching move into a dedicated adapter?"
python /path/to/ControlCoding/scripts/cc.py consult-packet show --project-root . --packet-id <packet_id>
python /path/to/ControlCoding/scripts/cc.py consult-result import --project-root . --packet-id <packet_id> --summary "Keep the split, isolate adapter logic." --decision partial --rationale-summary "Current split works, but gateway-specific branching is leaking across layers." --next-action "Extract the adapter module."
python /path/to/ControlCoding/scripts/cc.py consult status --project-root .
```

The import stores concise engineering summaries only. It does not persist raw
chat transcripts or claim hidden routed/automatic orchestration parity. This
bridge path matches the current release-oriented `Agents` story: explicit helper
roles on the chosen host with prompt, folder, and behavior contracts.
API-backed routed specialists belong to the later Version II path.

Runtime gate behavior now matches that distinction:

- `human_mediated` specialist paths block auto-execution and require the packet/import flow
- `cc_routed` and `auto_bounded` specialist paths only run through an explicit specialist consent path plus a valid `gateway_config.json` backend reference
- `auto_bounded` also consumes the configured runtime call budget; blocked attempts are logged, but do not silently masquerade as successful routed calls

You can also generate the current multi-host capability/evidence matrix with:

```bash
python /path/to/ControlCoding/scripts/cc.py benchmark-matrix generate --project-root .
```

### What the user gets

After the standard setup flow, the project should have:

- `CONTROLCODING.md` as the canonical project context
- the derived host-native file such as `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or `.clinerules`
- `.controlcoding/` control-plane files
- hooks and host launcher assets
- `ROADMAP.md` and `BUGS.md`
- if you later run `setup-project`: an initial design document and an initial implementation plan

### Which path to choose

- Use `setup` for a new project.
- Use `setup` for an existing project that does not have ControlCoding yet.
- Use `init` only for a minimal, non-interactive bootstrap or scripting flow.

In product-tier terms, this gets you to the `Core` baseline first. In methodology
terms, maturity levels `L1-L4` still advance only when their triggers fire.

New to ControlCoding? Start with [`docs/install-controlcoding-on-your-project.md`](docs/install-controlcoding-on-your-project.md). For a compact version, see [`docs/quick-start.md`](docs/quick-start.md). If you want the optional specialist/runtime layers after the base setup, continue with [`docs/ecosystem-quickstart.md`](docs/ecosystem-quickstart.md). For the full walkthrough with domain examples, see [`docs/adoption-guide.md`](docs/adoption-guide.md).

If you want a CLI fallback that prints the install prompt contract, use:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .
```

and paste the printed prompt into your host chat. That path is intended only as a backend fallback for environments that still need the prompt printed explicitly.
If the host can execute local commands, it should apply the setup itself with `--answers-file ... --apply-answers` instead of sending you back through the terminal questionnaire. Only hosts that truly cannot run local commands should fall back to asking you to run the commands manually.
After install, the chat may offer the separate `setup-project` wizard if you explicitly want kickoff docs.

Short user-facing install ceremony:

```text
Install ControlCoding in this project using the local repository at /path/to/ControlCoding.
Act as the official conversational ControlCoding installation wizard.
Briefly explain what ControlCoding is and how the wizard will work in the user's language.
Ask one question at a time.
Keep the actual next question in the visible chat reply rather than only in reasoning.
Treat the likely current host as a recommendation, not as a forced choice.
Explain the practical difference between Core, Core + manual consultation, Agents, and Studio before asking me to choose.
Recommend Core first.
Do not silently choose the major setup options for me. Ask me to confirm the main choices before applying anything.
If local commands are available, create handoff.json, run setup, setup --engagement, and doctor, then tell me exactly what files were written and whether doctor passed.
```

If you want canonical files for AI-guided install and project setup instead of embedding long prompts each time, use [`INSTALL_WIZARD.md`](INSTALL_WIZARD.md) and [`PROJECT_SETUP_WIZARD.md`](PROJECT_SETUP_WIZARD.md).

Kickoff planning behavior is chosen before the first design/plan package is written and now follows the selected ControlCoding usage model:

- `Core`: direct structured planning by the main chat
- `Core` with manual consultation: same direct kickoff, but one bounded external consultation path is allowed if a blocking uncertainty remains
- `Agents` / `Studio`: specialist-assisted kickoff planning with explicit provenance in the generated docs

That kickoff choice now happens in `setup-project`, not in the base install flow.

### Repo Policy Split

ControlCoding distinguishes between:

- **the framework repository itself**
  where published content is the public framework surface (`README.md`,
  `docs/`, `scripts/`, `templates/`, `tests/`, and explicitly allowlisted
  benchmark summaries), while
  maintainer working docs such as `CLAUDE.md`, `STATUS.md`, `ROADMAP.md`,
  `BUGS.md`, `dev/`, and `devlog/` remain local-only
- **adopter repositories**
  where CC working docs stay local-only by default unless the team explicitly
  opts into `cc_artifact_mode = shared_repo`

Across both cases, `devlog/` is local session memory and is not meant for the
online git history. The same local-only rule also applies to the framework's
own internal working docs.

Maintainers should validate the public Core source repo with:

```bash
python scripts/cc.py release-doctor --project-root .
python scripts/cc.py verify run --project-root .
```

`release-doctor` checks the publishable source contract. `doctor --release`
uses the same release profile for compatibility. The release profile reads
`controlcoding.release.json`, which records the public package allowlist,
denylist, and required source files. Publishable releases are promoted from that
explicit scope; they are not broad copies of a local development workspace. The
release profile does not require installed-project local state such as
`.controlcoding/cc_config.json`, `STATUS.md`, `devlog/`, or copied local hook
files.

Normal installed-project `doctor --json` and `doctor --release --json` both
include an `operationalContract` block. That block is the machine-readable
answer to four separate questions: whether the project is ready for ordinary
human maintenance, whether it is ready for AI-assisted work, whether it is safe
enough for autonomous AI work, and whether a source release is publishable. A
green `healthy=true` is intentionally not treated as the whole story.

Normal installed-project `doctor --json` also includes `constitutionDrift`.
That check verifies that the canonical `CONTROLCODING.md` is not merely present:
it must still line up with generated host files, configured protected zones, and
active invariant ids. You can run the same check directly with
`cc context drift --host <host>` after changing `CONTROLCODING.md`,
`cc_config.json`, or `controlcoding.invariants.json`.

## Cross-Tool Compatibility

ControlCoding works with any AI coding tool that reads a context file, provided you use that tool through an authorized interface. It was designed and validated on Claude Code, but the core methodology (condominium architecture, maturity levels, domain invariants, gradual adoption) is tool-agnostic.

Across hosts, CC keeps the same structural slots:

- `context_gate` via a host-native file derived from `CONTROLCODING.md`
- `inline_boundary_gate` on hosts with native hooks
- `repo_boundary_gate` via git pre-commit or equivalent
- `review_gate` via CodeWarden or equivalent review trigger
- `verification_gate` via criteria tracking and invariant tests

What changes per host is the **location of the boundary/review gates**, not the overall structure. `cc host status` and `cc doctor` now expose the active protection model for the selected host.

For Class B/C hosts, `cc write-path` is the optional serious experiment for a
future CC-owned write path: opt-in, explicit, and measured. It does not claim
that normal editor/host writes are intercepted.

Tested or documented for: Claude Code, Cline, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, Aider, OpenCode, and self-hosted stacks (Ollama + local models). See [`docs/cross-tool-guide.md`](docs/cross-tool-guide.md) for setup instructions per tool and a full compatibility matrix.

[AGENTS.md](https://github.com/anthropics/agent-conventions) is converging as a cross-tool standard for AI context files. CC adopters can maintain an AGENTS.md alongside other host files, but the ControlCoding source of truth is now `CONTROLCODING.md`.
For supported hosts, `cc setup` and `cc export host-context --host <host>` can generate the host-native context file from the canonical `CONTROLCODING.md` (or from legacy `CLAUDE.md` until a project migrates).
For projects that move between hosts over time, `cc host switch <host>` updates the primary host, preserves the enabled host set, and resyncs the target host-native context file.

## Building Metaphor And Origin

ControlCoding treats a codebase like a building under construction.

Some parts are foundations or load-bearing walls: stable code, public contracts,
core models, and architecture decisions. They should not be casually moved
because everything else depends on them.

Some parts are corridors, halls, and shared services: common utilities,
integration points, registries, adapters, and APIs used by many rooms. They can
change, but every change needs awareness of who walks through that corridor.

Some parts are rooms or offices: feature modules where focused work can happen
quickly because ownership is clear. Inside a room, the AI can move fast.
Crossing into another room, changing a corridor, or touching a load-bearing wall
should require a gate, a review, or an explicit decision.

ControlCoding originated in 2024 during AI-assisted work on a large
procedural-generation project. The first problem was not whether the AI could
write code. It could. The problem was that fast AI edits made it easy for a
project to lose structure: working functions were overwritten or reimplemented,
existing logic was duplicated under new names, responsibilities drifted across
files, and architectural decisions lived only in chat history.

The original aims were:

- keep large AI-assisted projects divided into understandable subsystems
- give every file and module a clear architectural place and responsibility
- prevent God files, God objects, and catch-all scripts from absorbing unrelated
  work
- make the AI reuse or promote shared logic instead of duplicating functions in
  parallel areas
- protect working functions from accidental rewrites or conflicting replacements
- let the AI know what had already been built before adding new code
- isolate bugs, experiments, and partial failures so they stayed local
- create architectural firebreaks between foundations, shared services, feature
  rooms, and experimental workspaces
- keep the project understandable across multiple AI sessions instead of relying
  on chat history

See [docs/methodology-history.md](docs/methodology-history.md) for the public
history and the distinction between historical mechanisms and the current Core.

## What's Original

ControlCoding builds on well-known foundations such as Clean Architecture, DDD
bounded contexts, design by contract, property-based testing, ADRs, hook
systems, and repository checks. Context files, hooks, CLI scaffolding, local
memory, and MCP-style tools are not unique by themselves.

The stronger novelty claim is the combination and the target problem:

- architecture-first vibe coding: the framework is aimed at preventing God
  files, misplaced files, duplicated logic, and boundary drift, not only at
  making the AI write code faster
- building model as an operating system for architecture: foundations,
  corridors, rooms, and offices map to stable/shared/features/workspace zones
  and different change risks
- project-specific invariants as operational checks: architectural and domain
  rules are treated as properties to preserve mechanically where possible
- protected zones with explicit escalation: touching stable code, shared
  corridors, or architecture-critical files requires a different level of
  attention than editing a feature room
- canonical context with generated host views: `CONTROLCODING.md` is the source
  of truth, while `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.clinerules` are
  derived views
- honest host capability model: each host gets an explicit statement of which
  protections are mechanical, conditional, advisory, or unavailable
- repo-side fallback for non-inline hosts: ControlCoding does not pretend every
  host can block writes before they happen
- local memory with lifecycle and impact context: memory records decisions,
  entities, lifecycle state, and project impact, not just loose reminders
- controlled write path with receipts: optional patch manifests, baseline
  checks, and receipts make file changes more auditable
- manual consultation packets: external AI review is packaged as a bounded
  packet/import flow rather than hidden recursive agent orchestration
- authorization boundary as a product rule: official CLIs, official APIs, local
  runtimes, and vendor-approved connectors stay separate from consumer-login
  reuse
- temporary lift protocol: protected files can be changed through narrow,
  explicit, auditable exceptions instead of leaving protections disabled
- mechanical vs advisory distinction: the framework separates rules that can
  block writes, commits, or tests from rules that only guide model behavior
- promotion path: experimental code can move from workspace to feature, shared
  utility, and stable foundation as it matures

See [docs/ccdocs/methodology.md](docs/ccdocs/methodology.md) for the complete list of contributions including promotion paths, delegation boundaries, commit ceremony, and cross-session persistence.

## Detailed Evidence

Validated on 2 real projects (6 months continuous + 1 intensive week) of AI-assisted development:

| Metric | Project A (C++20, OpenGL) | Project B (Python, scoring pipeline) |
|---|---|---|
| Source files | 86 | 72 |
| Test count | 310 | 984 |
| Domain invariants | 10 | 23 |
| CC Maturity Level | L2 (Enforced) | L2 (Enforced) |
| Hook enforcement | Active (DENY on stable/) | Active (WARN on core/) |

See [docs/evidence.md](docs/evidence.md) for the full anonymized report with adoption patterns, real-world examples, and cost analysis.

## Repository Structure

```
Published repository surface

README.md                         # This file
CHANGELOG.md                      # Version history with detailed changes
docs/
  install-controlcoding-on-your-project.md  # Primary install page for adopters
  quick-start.md                  # Compact setup summary
  adoption-guide.md               # Detailed walkthrough with domain-specific examples
  methodology-history.md          # Public origin and historical mechanism notes
  evidence.md                     # Validation data from 2 real projects (anonymized)
  cross-tool-guide.md             # Setup guides for Claude Code, Codex, Gemini, Cursor, etc.
  ccdocs/                         # Split methodology documentation
    methodology.md                # Architecture, principles, enforcement model
    hooks-reference.md            # Hook scripts, configuration, exit codes
    tools-reference.md            # CLI, MCP servers, debug escalation
    cookbook.md                    # Worked examples by domain
scripts/
  cc.py                           # CLI tool: cc setup, cc init, cc install, cc doctor, cc review
  phase0_discover.py              # Codebase scanner, generates a draft CONTROLCODING.md
  codewarden_summary.py           # Cumulative violation report generator
  metrics_collector.py            # Unified metrics collector for CC projects
  fitness_check.py                # Architectural fitness checks: layer drift, God files, ownership/mutation rules, gateway rules, fan-in/out, cycles
templates/
  CLAUDE.md.template              # Starter project-context template used to derive host files
  phase0_discovery.md             # Architecture discovery guide (Phase 0)
  review_prompt_template.md       # Peer review prompt template (used by cc review)
  adr.md                          # ADR template (Nygard format)
  hooks/
    check_boundaries.py           # Boundary enforcement hook (PreToolUse)
    check_dangerous_commands.py   # Dangerous command blocker (PreToolUse)
    codewarden_backend.py         # Shared LLM backend for CodeWarden hooks
    codewarden_review.py          # Guardian review hook (Stop): diff + impact scan + fitness evidence -> structured architecture findings
    codewarden_plan_review.py     # Plan review hook (PreToolUse on ExitPlanMode)
    session_end_check.py          # SessionEnd integrity verification (Stop)
    codewarden_postcommit         # Git post-commit wrapper, bash (for non-Claude tools)
    codewarden_postcommit.py      # Git post-commit wrapper, Python (cross-platform)
    violation_store.py            # Cross-session violation memory (JSONL)
    hook_logger.py                # Logging module for hooks
    check_workflow.py             # Workflow enforcement hook (PreToolUse, advisory)
    settings.json.example         # Control-plane hook configuration example
  scripts/
    mcp_consultant.py             # External consultation MCP server (5 roles, agent-mode)
    mcp_session.py                # Session management MCP server (checkpoint, devlog)
    mcp_bridge.py                 # Multi-agent communication MCP server
    cc_dashboard.py               # Gradio monitoring dashboard (read-only)
    consult.py                    # CLI fallback for consultation (no MCP needed)
    visual_check.py               # Visual feedback loop helper (L2 debug)
    visual_check_utils.py         # Shared utilities (screenshot, process lifecycle, JSON reports)
    visual_test.py                # Interactive visual testing with input control (L2+)
    cc_audit.py                   # Compliance audit script (generic, customize per project)
  helper-session/
    CLAUDE.md                     # Rules for the helper AI session
    SETUP.md                      # Setup guide with 5 configuration profiles
  tests/
    test_domain_invariants.py.example  # Invariant test template with examples by domain
prompts/                          # Prompt scripts for multi-step development tasks
tests/                            # Test suite; publish a current count only from a verified final release run
  test_check_boundaries.py        # Boundary enforcement tests
  test_check_dangerous_commands.py # Dangerous command blocking tests
  test_check_workflow.py          # Workflow enforcement tests
  test_gitignore.py               # Auto-gitignore tests
  test_session_end_check.py       # Session-end integrity tests
  test_session.py                 # Session management tests
  test_visual_check.py            # Visual feedback loop tests
  test_visual_test.py             # Interactive visual testing tests
benchmarks/
  2026-04-11-cctest-3dcrawler-v11-scorecard.md
  2026-04-11-cctest-3dcrawler-v11-review-check.md
  2026-04-27-r5-release-pair-scorecard.md

Local maintainer-only working docs (gitignored, not published)

CLAUDE.md                         # Local host-native context for this repo (maintainer legacy)
STATUS.md                         # Local project state
ROADMAP.md                        # Local roadmap
BUGS.md                           # Local defect log
dev/                              # Local plans, design, research, legal/business, prior art
devlog/                           # Local chronological working memory
```

## History

- **v1 (2024)**: Created for a procedural generation project. Heavy on process: CODEMAP, CONTRACTS, METRICS, RenameSpec, ChangeSpec. Five separate files to maintain.
- **v2 (February 2026)**: Generalized and simplified. Less process, more automation. Mechanical enforcement via hooks replaced trust-based enforcement. Domain-agnostic. One CLAUDE.md instead of five files.
- **v2.1 (March 2026)**: Added session management (MCP), multi-agent communication (bridge), external consultation with 5 agent-mode roles, monitoring dashboard, SessionEnd integrity verification, peer review prompt generation (`cc review`), central hooks, auto-gitignore, interactive visual testing, and 472 automated tests.
- **v3.1.0-alpha (March 2026)**: Agent-layer prototype with BaseAgent implementations for Concierge, Architect, Coder, Reviewer, Debugger, Socratic, Visual, Expert, and verification orchestration, plus hook-based CodeWarden review. Planner with maieutic expansion, Tandem debate protocol, Verification Engine with DAG-based criteria tracking. 4-level engagement system, authority/precedence model, 3 deepened auditors with 15 operative skill files. 977 automated tests.

Key insight from v1 to v2: AI models got smarter, but structural guardrails are still necessary. The nature of the guardrails changed, from "remind the AI what exists" to "prevent the AI from breaking what's stable."

Key insight from v2 to v3: a single AI session cannot self-correct structural errors. Explicit helper roles (Architect advice, Coder implementation, Reviewer verification) plus mechanical gating can improve outcomes, but the current public release does not promise automatic routed agent execution.

## Independent Validation

Several 2025-2026 industry developments independently arrived at similar patterns:

| CC Concept | Industry Equivalent |
|---|---|
| ChangeSpec | Kiro Spec-Driven Development |
| CODEMAP.md | CLAUDE.md in Claude Code |
| Planner/Surgeon | Plan Mode in Claude Code and Cursor |
| feature.lock | PreToolUse hooks (feature.lock planned for v2.2) |
| "Context Architecture" | "Context Engineering" (2026 buzzword) |

ControlCoding was born from real-world AI-assisted development experience since 2024. The contribution is the integrated methodology with empirical data, not any single idea.

## License

ControlCoding is source-available for noncommercial use only.

- **Markdown documentation, methodology text, public context templates, and
  host instruction templates**: `CC-BY-NC-SA-4.0`.
- **Software, tests, workflows, JSON/YAML configuration examples, executable
  templates under `templates/scripts/` and `templates/hooks/`, and all code
  file types listed in `LICENSE`**: `PolyForm-Noncommercial-1.0.0`.
- **Commercial use**: commercial use, resale, paid hosting, commercial
  redistribution, commercial SaaS use, paid consulting packages, or inclusion
  in a commercial product or service requires a separate written commercial
  license from Stefano Tonello.
  See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for the request path.

ControlWork originated as the Project Plane inside ControlCoding and is now a
standalone source-available noncommercial product that remains compatible with
embedded ControlWork in ControlCoding. Compatibility does not make either
product open source or permit commercial use without written permission.

Historical note: versions previously released under MIT, CC-BY-SA, or other
terms remain governed by the terms under which those versions were published.
This repository state and future versions use the noncommercial license unless
a later release states otherwise.

See [LICENSE](LICENSE) for the exact repository license boundary and
[COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for commercial permission
requests.

## Author

Created by Stefano Tonello (Naimas).

---

ControlCoding is a source-available noncommercial methodology and structural framework. No vendor lock-in. The core (`CONTROLCODING.md` as canonical source, derived host files, hooks, and invariants) requires only Python 3.10+. Optional MCP tools require `pip install fastmcp`. The dashboard requires `pip install gradio`. Everything works with official CLIs, official APIs, local models, or vendor-approved connector flows. Commercial use requires separate written permission.
