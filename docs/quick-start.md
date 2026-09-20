# ControlCoding Quick Start

> **Get the updated code:** [Download current Core (ZIP)](https://github.com/Naimas/ControlCoding/archive/refs/heads/master.zip).
> The `v3.0.2` archives under GitHub Releases predate the September 2026 updates.
> This is development source; see [download status and validation](../README.md#download-current-core).

> If you just downloaded ControlCoding and want to apply it to your own project, this is the correct path.
>
> Requires: Python 3.11+ for Core and Git. Memory also requires a working SQLite deserialize API. Use the PowerShell or Bash variant in the installation guide for your shell.

Primary onboarding page: [install-controlcoding-on-your-project.md](./install-controlcoding-on-your-project.md)
Public packaging split: [release-model.md](./release-model.md)

## Start Here

Start from chat, not from a terminal questionnaire.

Tell your AI host:

```text
Install ControlCoding in this project using the local ControlCoding repository.
Read INSTALL_WIZARD.md and follow it.
Ask one question at a time.
```

Only use raw commands yourself when the host truly cannot execute local commands.

Before applying, review the [complete handoff](install-controlcoding-on-your-project.md#complete-fresh-project-example)
and save the confirmed `setup` and `engagement` sections as `handoff.json`.
Inspect existing context/configuration conflicts before running the fresh-target
example. Supplying answers applies immediately with or without `--apply-answers`.
The guide contains both PowerShell and Bash variants. Bash apply sequence:

```bash
python "/path/to/ControlCoding/scripts/cc.py" setup --answers-file "./handoff.json" --apply-answers --project-root . &&
python "/path/to/ControlCoding/scripts/cc.py" setup --engagement --answers-file "./handoff.json" --apply-answers --project-root . &&
python "/path/to/ControlCoding/scripts/cc.py" doctor --project-root .
```

If ControlCoding lives on a different path on your machine, replace `/path/to/ControlCoding/` with the real location of the downloaded repository.

This is the normal install path for:

- a brand-new project
- an existing project that does not use ControlCoding yet

Use `init` only if you intentionally want a minimal non-interactive bootstrap.

## Adoption Profiles

Start with the smallest profile that matches the project:

| Profile | Use when | First commands |
|---|---|---|
| Minimal | You need the core context, hooks, and `doctor` baseline only. | `init`, then `doctor` |
| Governed | You want setup questions, host-aware guidance, boundary gates, and verification discipline. | `setup --chat-guide`, apply the handoff, then `doctor` |
| Enterprise memory | You need durable project knowledge, retrieval, handoff, or ControlWork memory after Core is healthy. | Core setup first, then `memory init` or `memory work-init` |

First-contact vocabulary stays small:

- Runtime: local config, hooks, receipts, verification state.
- Engineering: plans, designs, acceptance criteria, code, tests.
- Knowledge: project memory and optional ControlWork records.
- Docs: reviewed public or adopter-facing guidance.
- Evidence: audits, receipts, test output, and verification records.

The V1/Core package does not include a bundled desktop installer or Studio UI.
Use the chat-guided or CLI setup path above. Project setup remains a separate
step after installation.

Canonical chat contracts:

- [`../INSTALL_WIZARD.md`](../INSTALL_WIZARD.md)
- [`../PROJECT_SETUP_WIZARD.md`](../PROJECT_SETUP_WIZARD.md)

## Every New Chat

Inside an initialized project, the user should not need to run a Python command
to restore project memory. Open the AI chat in the project folder and say:

```text
Start this project and load its memory before working.
```

The project host file tells the AI to run the ControlCoding startup tools
itself when tools are available. The AI should load the read-only startup packet
before answering, editing, deciding, or continuing prior work.

That packet keeps these surfaces in front of the chat:

- active Dev GraphRAG memory
- Project Plane or ControlWork GraphRAG memory when `.controlwork/` exists
- current work, prior work, blockers, and next steps
- startup warnings and closeout guidance

If tools are unavailable, the AI should say that memory startup could not be
run and ask whether to continue prior work or start a new task.

## Step 1: Installation Contract

If you still need a CLI fallback that prints the install prompt contract, use:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .
```

This prints the official install prompt contract. Paste it into your AI host and let the host collect the required answers, generate `handoff.json`, and run the non-interactive apply step.

If you already have a handoff file, run:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --answers-file handoff.json --project-root .
```

The install flow covers:

- project identity
- user host: `Codex CLI`, `Claude Code`, `Gemini CLI`, `Cursor`, and so on
- ControlCoding usage model: `Core`, `Core + manual consultation`, `Agents`, or `Studio`
- documentation ownership: `managed` vs `project_managed`
- host workflow guidance

CC working documents stay local-only in the public install path. The base setup does not ask adopters to decide that.

### Separate project setup

After installation, start the second flow from chat too:

```text
Set up this project with ControlCoding.
Read PROJECT_SETUP_WIZARD.md and follow it.
Look for project_brief.md or another likely brief first.
```

CLI fallback that prints the project-setup prompt contract:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --chat-guide --host-hint codex_cli --project-root .
```

Or, if the host already generated a kickoff handoff file:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --answers-file handoff.json --project-root .
```

That second flow asks for:

- source mode: existing brief/doc vs interactive guidance vs brownfield adoption
- project framing in plain language
- implementation stack and system shape as optional/provisional inputs

It now scaffolds the project in stages:

- `.controlcoding/setup_intent.json`
  - canonical setup-intent snapshot and phase graph for the project setup pass
- `project-definition/`
  - source assessment
  - consultation planning
  - existing-project inventory, truth map, architecture extraction, maturity/gap assessment, and adoption plan when the repo is already mature
  - manual consultation packets for bounded external design help
  - specialist role specs when specialist-assisted planning is active
- `design/`
  - design baseline
  - system overview
  - architecture and subsystem docs
- `criteria/` and `contracts/`
  - requirement, verification, and coverage governance
- implementation planning artifacts
  - master implementation plan
  - progressive protection plan
  - feature implementation docs

The important rule is:

- `setup-project` should not treat a weak brief as a complete design baseline
- `setup-project` should not treat an existing repo as design-truth without first mapping authority, drift, and current maturity

It can scaffold:

- initial design document
- initial implementation plan
- `ROADMAP.md`
- `BUGS.md`

Those docs are written after CC already knows the usage model chosen in base setup:

- direct `Core` planning
- direct `Core` planning with bounded manual consultation readiness
- specialist-assisted `Agents` planning through explicit host-chat helper roles
- `Studio` only as a future/internal UI path

This is the recommended path when you want ControlCoding to help turn one of these into real project docs:

- a project idea
- a short brief
- an existing design direction that still needs to be normalized into project docs

## Step 2: Engagement Apply

Run:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --engagement --answers-file handoff.json --project-root .
```

This applies the engagement/runtime handoff generated by the chat-guided install flow. It covers:

- product tier: `Core`, `Agents`, or `Studio`
- backend policy
- local tandem/model details when relevant
- on `Agents` / `Studio`, an explicit specialist/backend matrix:
  active paths, backend/model per role, permission envelope, execution mode,
  and call limits
- advanced packs or specialist/backend-heavy paths only when relevant

`Core + manual consultation` remains separate from that matrix and stays
explicitly user-mediated.

Current release freeze:

- `Core + manual consultation` is the public manual second-opinion path
- the current public `Agents` path is explicit helper work on the chosen host through prompt, folder, and behavior contracts
- API-backed routed specialists belong to the later Version II path
- `Studio` / CC UI remains a later optional extra

### Which tier to choose first

- Choose `Core` if you want the structured framework baseline first.
- Choose `Agents` only if you want bounded helper roles with explicit prompt/folder/behavior contracts on the chosen host.
- Treat API-backed routed specialists as later Version II work.
- Choose `Studio` only for future/internal UI work, not for the current public release path.

For most first installs, start with `Core`.

## Step 3: Validation

Run:

```bash
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

`doctor` checks that the installation is coherent: context files, hooks, config,
host profile, and other required pieces.

It also reports the active `inline gate`, `repo boundary gate`, `review gate`,
and `verification gate` for the selected host, and tells you which ones are
mechanical vs conditional/advisory.

For non-inline hosts such as Codex CLI or Gemini CLI, the project is not
considered healthy if the real repo-side boundary path is missing.

Optional advanced path for non-inline hosts:

```bash
python /path/to/ControlCoding/scripts/cc.py write-path enable --mode patch_gateway --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path prepare --patch-file proposed.diff --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path apply --patch-file proposed.diff --manifest-id <manifest_id> --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path status --project-root .
```

This is an explicit CC-owned patch gateway. It can screen protected-zone writes
before apply, but it does not imply normal host-write interception. If you
enable manifest gating, `prepare` signs the exact patch hash/file list and
records the baseline state of the touched files. `apply` refuses to run unless
that manifest still matches.
If you enable `--preflight-fitness`, `apply` also runs a shadow-worktree
`fitness_check.py` pass before the real patch lands.
Terminal outcomes are saved as local receipts in
`.controlcoding/write_path_receipts/`.

For a human-mediated specialist consultation:

```bash
python /path/to/ControlCoding/scripts/cc.py consult-packet create --project-root . --role architect --objective "Validate repo-side boundary split" --question "Should gateway branching move into a dedicated adapter?"
python /path/to/ControlCoding/scripts/cc.py consult-packet show --project-root . --packet-id <packet_id>
python /path/to/ControlCoding/scripts/cc.py consult-packet create --project-root . --role architect --thread-id <thread_id> --objective "Follow up on the adapter split" --question "What should move first?"
python /path/to/ControlCoding/scripts/cc.py consult-packet create --project-root . --role architect --topic-key adapter_split --objective "Compare alternate adapter split" --question "Should option B be rejected?"
python /path/to/ControlCoding/scripts/cc.py consult-result import --project-root . --packet-id <packet_id> --summary "Keep the split, isolate adapter logic." --decision partial --rationale-summary "Current split works, but gateway branching is leaking across layers." --next-action "Extract the adapter module."
python /path/to/ControlCoding/scripts/cc.py consult resolution --project-root . --role architect --topic-key adapter_split
python /path/to/ControlCoding/scripts/cc.py consult status --project-root .
```

That flow stores concise rationale summaries and next actions. It does not
persist raw chat transcripts or imply CC-routed specialist execution.
Manual artifacts live under `.controlcoding/external_consultation/`.
Each manual thread now keeps a local `thread.json`, `memory.md`, and
`resume_prompt.md` under `.controlcoding/external_consultation/threads/` so a
follow-up chat can continue from bounded context instead of restarting from
zero.
Cross-thread continuity is also summarized automatically in
`.controlcoding/external_consultation/role_memory/` and
`.controlcoding/external_consultation/convergence_summary.md`.
Use `--topic-key` when different manual consultations are really about the same
decision and should converge on a shared normalized topic.
If a topic becomes conflicted, `cc consult resolution --role ... --topic-key ...`
prints the generated resolution prompt location for the follow-up chat.

To generate the current multi-host capability/evidence matrix:

```bash
python /path/to/ControlCoding/scripts/cc.py benchmark-matrix generate --project-root .
```

## Use It From An IDE Chat

If you want Codex, Claude, or another IDE-hosted chat to handle installation, print a ready-to-paste prompt with:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .
```

Then paste the printed prompt into your host chat. The intended behavior is one question at a time, chat-generated handoff JSON, direct answers-file execution when the host can run local commands, and a final review after `doctor`.
Only if the host truly cannot execute local commands should the flow fall back to asking you to run `setup`, `setup --engagement`, and `doctor` manually.
After install, the chat may optionally offer the separate `setup-project` flow.

Minimal instruction to give the host chat:

```text
Install ControlCoding in this project using the local repository at /path/to/ControlCoding.
Act as the official chat-guided ControlCoding installation assistant.
Briefly explain what ControlCoding is and how the setup flow will work in the user's language.
Ask one question at a time.
Keep the actual next question in the visible chat reply rather than only in reasoning.
Treat the likely current host as a recommendation, not as a forced choice.
Explain the difference between Core, Core + manual consultation, Agents, and Studio before asking me to choose.
Recommend Core first.
Do not silently choose the major setup options for me. Ask me to confirm the main choices before applying anything.
If local commands are available, create handoff.json, run setup, setup --engagement, and doctor, then tell me exactly what files were written and whether doctor passed.
```

## What You Get

After the standard setup flow, your project should have:

- `CONTROLCODING.md` as the canonical context source
- the derived host-native file for your selected host, such as `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or `.clinerules`
- `.controlcoding/` control-plane files
- hook wiring and host launcher assets
- `ROADMAP.md` and `BUGS.md`
- if you later run `setup-project`: design/plan docs

## When To Use `init`

Use:

```bash
python /path/to/ControlCoding/scripts/cc.py init --project-root .
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

only when you want:

- a minimal bootstrap
- a non-interactive/scripted flow
- a recovery path when the full chat-guided setup flow is not the right tool

`init` is not the preferred first-time onboarding path for a normal project.

## Migrating From v2.5.2

Preview and verify adapter ownership before applying the `3.0.2` release:

```bash
python /path/to/ControlCoding/scripts/cc.py context sync --host <host> --preview-only --project-root .
python /path/to/ControlCoding/scripts/cc.py context adopt --host <host> --project-root .
python /path/to/ControlCoding/scripts/cc.py context adopt --host <host> --apply --project-root .
python /path/to/ControlCoding/scripts/cc.py context sync --host <host> --project-root .
```

The adoption commands are needed only for an eligible historical unmarked or
explicitly foreign adapter. The first adoption command is preview-only. Invalid,
ambiguous, and unsafe files remain blocked, and normal sync never overwrites a
file that ControlCoding does not own.

## Manual Path

If you deliberately want to bootstrap by hand, you can still:

1. create `CONTROLCODING.md`
2. derive the host-native file with `cc export host-context --host <host>`
3. wire hooks manually
4. run `cc doctor`

But this is the advanced/manual path, not the recommended path for a new adopter.

## After Base Setup

If you only want the structured baseline, stop after `Core` and start working.

If you want the optional specialist/runtime layers after base setup, continue with
[ecosystem-quickstart.md](ecosystem-quickstart.md).

For a deeper walkthrough with zone classification and domain-specific examples,
see [adoption-guide.md](adoption-guide.md).
