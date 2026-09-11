# Ecosystem Quick Start (v3.1)

> Set up the optional agent/runtime ecosystem in 10 minutes.
>
> This guide starts **after** the base project install path:
>
> 1. `cc.py setup`
> 2. `cc.py setup --engagement`
> 3. `cc.py doctor`
>
> If you have not done that yet, start with [install-controlcoding-on-your-project.md](install-controlcoding-on-your-project.md) first.
>
> Authorized interfaces only: these examples assume official CLIs, official APIs,
> local models, or vendor-approved connector flows. They do not imply consumer
> account login inside a CC-owned interface or reuse of consumer OAuth/session tokens
> across products.

## 0.1 User Host vs CC UI

The tool or IDE the user chooses remains outside ControlCoding's own UI layer.

- `Claude Code`, `Codex CLI`, `Cursor`, `VS Code`, and similar tools are **User Hosts**
- ControlCoding's core is the **CC Structure**: hooks, CLI, config, gateway, ceremony
- `CC Agents` are optional helpers called by that structure
- `CC UI` is optional

On official CLI or consumer-auth paths, the canonical chat remains in the User
Host. On official API or local-runtime paths, CC UI may provide the primary
chat surface. Gateway/UI setup should persist the selected User Host separately
from the CC UI mode so the boundary is explicit in configuration as well as in docs.
On those official-CLI paths, orchestration and coding stay in the User Host too;
CC UI remains a mirror/control surface rather than an initiating chat surface.
When CC UI runs in `visualizer` mode, the Concierge panel should show only a
local routed transcript with explicit origins (`User Host`, `CC UI`,
`CC Structure`, `Worker`, `System`), never a fake replacement for the vendor
chat.

## 0.2 Primary Surface Rule

Use one primary surface per project/session.

| If you are working through... | Primary chat/orchestration surface | What the other surfaces should do |
|---|---|---|
| Claude Code, Codex CLI, or another official consumer-auth host | The official host | Observe, show approvals, show spend/trace, but do not dispatch |
| Anthropic/OpenAI API through CC UI | CC UI | Any other surface stays observer-only |
| Ollama or another local runtime through CC UI | CC UI | Any other surface stays observer-only |
| Multiple surfaces at once | User must choose one primary surface | All others become observer-only |

Recommended public-safe default:

- official host present on a consumer-auth path -> treat it as the primary surface
- API/local runtime path -> CC UI may be the primary surface
- never let two surfaces dispatch prompts or specialist calls at the same time
- if another CC-aware surface requests control, resolve it as an explicit takeover instead of switching silently
- if a wrapper or launcher is CC-aware, register the chosen role explicitly with `cc surface claim ...` or `cc surface observe ...`

Example local authority commands:

```bash
python scripts/cc.py surface claim host:claude-vscode --type official_host --label "Claude Code (VS Code)"
python scripts/cc.py surface observe ui:local-dashboard --type cc_ui_local --label "CC Local Visualizer"
python scripts/cc.py surface run host:claude-vscode --type official_host --label "Claude Code (VS Code)" -- claude
python scripts/cc.py surface status --json
```

## 1. Choose your product tier

```bash
python scripts/cc.py setup --project-root .
```

Pick a tier first:

| Tier | What you get | When to use |
|---|---|---|
| `Core` | Structured ControlCoding baseline: hooks, CLI/setup, session continuity, CodeWarden baseline, criteria tracking, host separation | Default serious baseline when you want the framework without making specialist agents the center of the workflow |
| `Agents` | `Core` plus bounded helper roles kept explicit on the chosen host through prompt, folder, and behavior contracts | When you want extra reasoning, comparison, or guided role-specific support |
| `Studio` | Optional CC UI layer kept outside the current public release path | Future/internal UX work only after the lower layers are stable |

Kickoff planning follows that same split:

- `Core` writes the first design/plan package directly in the main chat
- `Core` may optionally allow one bounded manual consultation path during planning
- `Agents` can use explicit helper-role kickoff planning and should record that provenance in the generated docs
- `Studio` remains a future/internal UI path, not the current public release story

Current implementation note:

- the public tier model is now `Core` / `Agents` / `Studio`
- the runtime still uses `.controlcoding/cc_engagement.json` plus UI-mode config under the hood
- treat those runtime knobs as advanced implementation detail, not as the public packaging menu
- current public `Agents` work should stay explicit and host-chat-defined; API-backed routed specialists belong to the later Version II path
- if you choose `Agents`/`Studio` with `backend_policy=local_only`, use local tandem only when the machine can sustain it: under `32 GB RAM` stay single-model, from `32-63 GB` keep tandem sequential/quantized, and low GPU VRAM should be treated as a further constraint

If you only want `Core`, stop after base setup and follow
[`docs/install-controlcoding-on-your-project.md`](install-controlcoding-on-your-project.md). The rest of this guide focuses on optional `Agents` work and future/internal UI notes.

If you need the current runtime settings explicitly:

```bash
python scripts/cc.py setup --engagement --project-root .
```

On `Agents`/`Studio` with `backend_policy=local_only`, the engagement flow now
lets you record two distinct local `Ollama` models for tandem. If those models
are missing, identical, or the machine is too weak, runtime degrades to
single-model consult instead of pretending a real two-voice tandem exists.
The same engagement step now also persists an explicit specialist/backend
consent matrix for `Agents` / `Studio`: which specialist paths are active,
which backend/model each path uses, what permission envelope applies, and what
call limit applies. `Core + manual consultation` remains a separate
user-mediated planning path and is not part of that matrix.

| Runtime knob | Current meaning | Public-facing interpretation |
|---|---|---|
| `conservative` | Minimal internal activation | Internal detail only; not the public definition of `Core` |
| `guided` | Adds some specialist support | Partial implementation path toward `Agents` |
| `active` | Adds stronger verification-oriented support | Another implementation path toward `Agents` |
| `full` | Broadest internal activation | Often used with `Studio`, but not equivalent to `Studio` by itself |

UI mode remains a separate implementation detail today:

- `visualizer`
- `api_studio`

## 2. Install the optional agent pack

```bash
python scripts/cc.py install all --project-root .
```

This copies all MCP servers and tools to your project.

## 3. Use the Planner (Agents/Studio workflows)

Start a planning session in Claude Code:

```
/plan Build a REST API with user auth, CRUD, and admin panel
```

The Planner asks 6 rounds of Socratic questions, then produces a phased plan in `devlog/plan.current.json`. On approval, it auto-exports:
- Verification criteria to `devlog/criteria.json`
- Zone mapping to `.controlcoding/cc_config.json`

Planner-driven flows belong to the optional agent layers. In the current
runtime they may require the higher engagement configurations, but in the
public model they belong to `Agents`/`Studio`, not to a separate fourth
product tier.

## 4. Use the Concierge pattern

When ControlCoding owns a visible chat surface, that surface should be
Concierge-shaped and single-voice. For the current public release, keep the
primary coding/chat work in the chosen official host. Any CC UI remains future
or optional, not part of the current release promise.

For the current public scope, treat Concierge as a single-voice pattern and
LAB/internal implementation surface, not as an installed public command.
`cc agents` is status-only for persistent agent memory. Fully routed Gateway
agent execution through `agent_runner.py` is deferred.

The legacy direct implementation (`templates/scripts/concierge.py`) and the
BaseAgent wrapper (`templates/scripts/concierge_agent.py`) model the cycle:
1. **PLANNING** - expands the idea into phases
2. **ARCHITECTURE_REVIEW** - Architect reviews interfaces
3. **CODING** - Coder implements each phase
4. **TESTING** - visual_test.py for functional testing
5. **REVIEWING** - structured PASS/FAIL review
6. **FINAL_REVIEW** - Scientist + Engineering audit

Public UX default: one visible chat only. The user speaks to the Concierge, and
specialist agents return their findings to that same chat through the
Concierge.

Public-safe default: keep external specialist calls human-approved in
interactive sessions, with visible backend/model/budget/permission context.
Use unattended or batch orchestration only when you have explicitly
pre-authorized the backends, budgets, and call limits.

If you later add a local dashboard, keep the public model simple:

- one visible Concierge chat for the user
- API/local workers may have richer internal panels
- CLI-backed workers should be shown as task traces, transcripts, or result
  threads, not as separate vendor-style chat windows
- if you mirror a CLI-backed Concierge thread in CC UI, label the origin of
  each routed message so the host boundary stays obvious

## 5. Use the Consultant (Agents/Studio)

In Claude Code, the Consultant is available as an MCP tool:

```
Use consult with role="debug" to ask an external model about this error
Use consult with role="architect" to validate this design decision
Use consult_tandem to compare Claude vs GPT on this architecture question
```

Recommended public default: consultants are called either directly by the human
or by the Concierge with explicit approval, then their result is routed back to
the same visible Concierge chat. Do not use open-ended recursive agent loops.

For purely local tandem:

- `RAM < 32 GB`: keep `consultant` single-model and leave tandem off
- `RAM 32-63 GB`: tandem is possible, but prefer sequential/quantized dual-model runs
- low or unknown GPU VRAM: do not assume two medium models can stay loaded comfortably

## 6. Run an independent audit

The V1/Core package does not ship the internal auditor skill packs or raw audit
reports. Run an independent engineering and security review with your approved
tooling, then retain only reviewed public evidence in the release documentation.

## 7. Check project health

```bash
python scripts/cc.py doctor --project-root .
python scripts/cc.py doctor --project-root . --json  # machine-readable
```

## Architecture Overview

```
User
  -> User Host (Claude Code / Codex CLI / Cursor / VS Code / ...)
       -> CC Structure
            +-> Concierge
            +-> Planner (internal function)
            +-> Architect (permanent expert)
            +-> Coder (worker, receives instructions)
            +-> Reviewer (structured PASS/FAIL)
            +-> Debugger (hypothesis-first)
            +-> Socratic (challenge assumptions)
            +-> CodeWarden (hook-based async code review, not a runnable BaseAgent role)
            +-> Expert on-demand (domain specialists)
            +-> Tandem (debate protocol, not agent)
            +-> visual_test.py (functional testing tool)
            +-> CC UI (optional visualizer or API/local studio)
```

Authority: hooks > runtime config > Concierge > manual lift.
Current runtime note: the runtime layer is still implemented primarily through
engagement config plus UI mode, even though the public packaging now uses
tiers.
In the public model, the Concierge is the only orchestration voice exposed by
ControlCoding itself. On official-CLI paths, the user still speaks through the
User Host rather than a CC-owned replacement chat.

## Files created by the ecosystem

| File | Purpose |
|------|---------|
| `.controlcoding/cc_engagement.json` | Internal engagement/runtime config currently used behind the public tier model, plus backend and budget config |
| `.controlcoding/concierge_state.json` | Concierge state (survives crashes) |
| `.controlcoding/event_log.jsonl` | Control plane event log |
| `.controlcoding/decision_log.jsonl` | Architecture decision records |
| `devlog/plan.current.json` | Current plan (if using Planner) |
| `devlog/criteria.json` | Verification criteria (auto-populated) |
