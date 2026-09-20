# Install ControlCoding On Your Project

> **Get the updated code:** [Download current Core (ZIP)](https://github.com/Naimas/ControlCoding/archive/refs/heads/master.zip).
> The `v3.0.2` archives under GitHub Releases predate the September 2026 updates.
> This is development source; see [download status and validation](../README.md#download-current-core).

This guide is for a human developer. It explains what ControlCoding installs,
which files it changes, what is optional, and how to verify that the install is
actually active.

If you are asking an AI coding host to do the install, give it this page and ask
it to follow the steps exactly. The AI should report every command it ran and
every file it changed.

## What You Are Installing

ControlCoding Core adds a local project control layer around your existing
codebase. It does not replace your AI coding tool and it does not own your
application code.

The base install creates:

- a canonical project context file: `CONTROLCODING.md`
- a host-specific context file, such as `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
  or `.clinerules`
- local ControlCoding state under `.controlcoding/`
- hook scripts under `hooks/`
- repo-side git hooks when the project is a git repository
- a `cc_config.json` file where protected zones and workflow rules live
- Project Memory Engine and GraphRAG as local Core capabilities

The Core memory default is local and governed-scope first. Base setup initializes
the `.controlcoding/` memory layout and indexes only governed ControlCoding or
ControlWork surfaces:

- `CONTROLCODING.md`
- generated host context files such as `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`,
  and `.clinerules`
- `CONTROLWORK.md`, when embedded Project Plane exists
- `project-definition/`, `design/`, `criteria/`, and `contracts/`
- `.controlwork/memory/`

The base install does not run a full repository memory scan, OCR, document
layout extraction, vector rebuild, file reorganization, document relocation, or
graph promotion. Those actions require a separate explicit command or wizard
confirmation.

The base install does not automatically create application features, modify your
business logic, publish anything, or enable remote AI calls.

## Requirements

- Python 3.11+ for Core; memory also requires a working SQLite deserialize API
- git
- a local checkout of the ControlCoding repository
- an authorized AI host or manual shell access

Use official CLIs, official APIs, local runtimes, or vendor-approved connectors
only. Do not reuse consumer chat login cookies, browser sessions, or private
tokens as an integration mechanism.

## Recommended Install

The primary public flow should start from chat.

Tell your AI host:

```text
Install ControlCoding in this project using the local ControlCoding repository.
Read INSTALL_WIZARD.md and follow it.
Ask one question at a time.
```

Only fall back to raw commands if the host truly cannot execute local commands.

Backend apply sequence from the project root:

```bash
python "/path/to/ControlCoding/scripts/cc.py" setup --answers-file "./handoff.json" --apply-answers --project-root . &&
python "/path/to/ControlCoding/scripts/cc.py" setup --engagement --answers-file "./handoff.json" --apply-answers --project-root . &&
python "/path/to/ControlCoding/scripts/cc.py" doctor --project-root .
```

This Bash sequence requires the reviewed handoff below. It is not a terminal
questionnaire. Use the PowerShell variant below when working in PowerShell.

Replace `/path/to/ControlCoding/` with the real path where you downloaded this repository.

Use `setup` for a guided install. Use `init` only when you intentionally want a
minimal non-interactive install:

```bash
python /path/to/ControlCoding/scripts/cc.py init --project-root .
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

This is the correct path for:

- a brand-new project
- an existing project that does not use ControlCoding yet

Do not start with manual file copying unless you have a specific reason.

Canonical chat contracts:

- [`../INSTALL_WIZARD.md`](../INSTALL_WIZARD.md)
- [`../PROJECT_SETUP_WIZARD.md`](../PROJECT_SETUP_WIZARD.md)

## Every New Chat After Install

Once a project is initialized, the simplest startup path is conversational.
Open the AI chat in the project folder and say:

```text
Start this project and load its memory before working.
```

The host context file tells the AI to run the ControlCoding startup tools
itself before answering, editing, or continuing prior work. Startup is
read-only and loads one AI-ready packet containing checks, relevant memory,
Project Plane context when `.controlwork/` exists, warnings, and expected
closeout guidance.

If the AI reports that Project Plane memory is missing and you want durable
cross-chat project memory, ask it to initialize ControlWork memory for the
project. The AI can then run the setup tool explicitly:

```bash
python /path/to/ControlCoding/scripts/cc.py memory work-quickstart --project-root . --topic "project bootstrap"
```

The V1/Core package does not include a bundled desktop installer or Studio UI.
Use the same chat-guided or CLI setup logic documented here. Project setup
remains a separate second flow after installation.
For the public packaging split, see [release-model.md](./release-model.md).

## Complete Fresh-Project Example

Selected-memory setup probes deserialize in private memory before writing to the
target. Unsupported Python or SQLite capability produces an actionable error.
If initialization or scanning fails later, setup reports partial completion and
returns nonzero; already-created files remain for inspection. It does not roll
back the whole installation. Optional package/runtime installation is separate.

Use an existing ControlCoding checkout and a separate adopter directory. Before
applying, inspect the target for `CONTROLCODING.md`, host instruction files,
`.controlcoding/`, `.claude/`, `hooks/` and custom Git hooks. If any conflict with
existing work, stop this fresh-install example and review ownership and changes
first. Direct apply can replace canonical context; foreign adapters can cause
partial setup. It is not a transactional installer or an automatic merge of
arbitrary customizations. Repeated setup is not a preservation guarantee.

Review the following choices and save the JSON as `handoff.json` outside any
protected target folder. It deliberately selects Core, a Codex configuration,
local hooks and no specialist agents or extra backend activation. `local_only`
is the policy for extra backends, not a claim about the user's host running
locally. No live host session or model call is needed to apply this handoff.

<!-- cc-install-handoff -->
```json
{
  "setup": {
    "name": "Example Project",
    "user_host": "codex_cli",
    "documentation_mode": "managed",
    "host_instruction_mode": "recommended",
    "host_custom_notes": [],
    "hooks_location": "local",
    "memory_default_policy": "governed_scope",
    "planning": {"tier": "core", "manual_consultation_allowed": false},
    "configure_advanced_packs": false,
    "selected_packs": [],
    "stable": [],
    "shared": [],
    "features": [],
    "behavioral_rules": [],
    "project_definition_mode": "skip"
  },
  "engagement": {
    "tier": "core",
    "manual_consultation_allowed": false,
    "backend_policy": "local_only",
    "tandem": {"mode": "off"},
    "specialist_paths": []
  }
}
```

The empty boundary lists classify no application folders; review and fill them
for your project. Governed memory indexes only the surfaces described above.
To postpone memory, explicitly change `memory_default_policy` to `deferred`:
setup records that choice without creating a memory database. Minimal `init`
also leaves memory uninitialized. Some handoff fields have defaults; this sample
is not a claim that every omitted field will be rejected.

Set the four absolute paths in the appropriate block. The target must already
exist. The interpreter must meet the requirements above. The first command only
prints guidance; review the handoff before executing the two apply commands.
**An answers file applies immediately, even without `--apply-answers`.** There
is no setup dry-run; the review step is a human inspection of choices and effects.
Setup can initialize a target Git repository and write context, configuration,
hooks, local launchers and the selected memory state.

PowerShell:

<!-- cc-install-powershell -->
```powershell
$CcPython = 'C:/path/to/python.exe'
$CcScript = 'C:/path/to/ControlCoding/scripts/cc.py'
$CcTarget = 'C:/path/to/your-project'
$CcHandoff = 'C:/path/to/handoff.json'
& $CcPython $CcScript setup --chat-guide --host-hint codex_cli --project-root $CcTarget
if ($LASTEXITCODE -ne 0) { throw 'Setup guide failed' }
& $CcPython $CcScript setup --answers-file $CcHandoff --apply-answers --project-root $CcTarget
if ($LASTEXITCODE -ne 0) { throw 'Base setup failed; inspect the output before continuing' }
& $CcPython $CcScript setup --engagement --answers-file $CcHandoff --apply-answers --project-root $CcTarget
if ($LASTEXITCODE -ne 0) { throw 'Engagement setup failed' }
& $CcPython $CcScript doctor --project-root $CcTarget
if ($LASTEXITCODE -ne 0) { throw 'Doctor reported a failure' }
```

Bash (including Git Bash on Windows, with paths readable by that shell):

<!-- cc-install-bash -->
```bash
CC_PYTHON='/path/to/python'
CC_SCRIPT='/path/to/ControlCoding/scripts/cc.py'
CC_TARGET='/path/to/your-project'
CC_HANDOFF='/path/to/handoff.json'
"$CC_PYTHON" "$CC_SCRIPT" setup --chat-guide --host-hint codex_cli --project-root "$CC_TARGET" || exit $?
"$CC_PYTHON" "$CC_SCRIPT" setup --answers-file "$CC_HANDOFF" --apply-answers --project-root "$CC_TARGET" || exit $?
"$CC_PYTHON" "$CC_SCRIPT" setup --engagement --answers-file "$CC_HANDOFF" --apply-answers --project-root "$CC_TARGET" || exit $?
"$CC_PYTHON" "$CC_SCRIPT" doctor --project-root "$CC_TARGET" || exit $?
```

Check the actual files and all command exit codes. Confirm the selected host and
memory policy in local configuration, the Core engagement choice, and the memory
bootstrap receipt when requested. A completed receipt must report governed scope
without a full scan. If any step fails, inspect partial output before retrying.
Doctor and generated `AGENTS.md` do not prove that a live host loads context or
executes hooks. The optional Bash observation hook is not enabled by minimal init.

## Step 1: Installation Contract

If you need the CLI fallback that prints the install prompt contract, run:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .
```

This prints the official install prompt contract. Paste it into your AI host and let the host collect the required answers, generate `handoff.json`, and run the non-interactive apply step.

If you already have a handoff file, run:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --answers-file handoff.json --project-root .
```

### What it asks first

- project identity
- user host: `Codex CLI`, `Claude Code`, `Gemini CLI`, `Cursor`, and so on
- ControlCoding usage model: `Core`, `Core + manual consultation`, `Agents`, or `Studio`
- documentation ownership: `managed` vs `project_managed`
- local project memory initialized with `memory_default_policy = governed_scope`
- host workflow guidance

CC working documents stay local-only in the public install path. The base setup no longer asks you to decide that.
Project Memory Engine and GraphRAG are Core defaults. The setup path prepares
local memory and a governed-scope bootstrap receipt, but does not treat that as
permission for a full repo scan or OCR.

### Separate Project Setup Flow

The install flow configures ControlCoding. It does not define the product, write
the master design package, or plan the implementation phases.

After install, start the second flow from chat:

```text
Set up this project with ControlCoding.
Read PROJECT_SETUP_WIZARD.md and follow it.
Look for project_brief.md or another likely brief first.
If the repo already looks mature, offer the brownfield adoption path.
```

CLI fallback that prints the project-setup prompt contract:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --chat-guide --host-hint codex_cli --project-root .
```

Or, if the host already produced the kickoff handoff file:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --answers-file handoff.json --project-root .
```

That second flow covers:

- source mode: existing brief/doc vs interactive guidance vs brownfield adoption
- project framing in plain language
- implementation stack and system shape as optional/provisional inputs

It now scaffolds the project in stages:

- `.controlcoding/setup_intent.json`
  - canonical setup-intent snapshot and phase graph for the project setup pass
- `project-definition/`
  - source assessment
  - consultation planning
  - existing-project inventory, truth map, architecture extraction, maturity/gap assessment, and adoption plan when the repo already exists
  - manual consultation packets for external design help in `Core`
  - specialist role specs for specialist-assisted planning
- `design/`
  - design baseline
  - architecture and subsystem docs
- `criteria/` and `contracts/`
  - traceability, verification, and coverage governance
- implementation planning artifacts
  - master implementation plan
  - progressive protection plan
  - feature implementation docs

That means `setup-project` is no longer just a doc generator. It first evaluates the quality of the source material, and for mature repos it also maps current authority, drift, architecture, and protection bootstrap candidates before the design baseline is trusted.

It can scaffold:

- initial design document
- initial implementation plan
- `ROADMAP.md`
- `BUGS.md`

The planning behavior follows the usage model you already selected during install:

- `Core` can plan directly in one strong structured chat
- `Core` may also allow one bounded manual consultation path
- `Agents` uses explicit helper roles on the chosen host by design
- `Studio` remains a future/internal UI path, not the current public release story

This is the recommended path when you want ControlCoding to help turn one of these into real project docs:

- a project idea
- a short brief
- an existing design direction that still needs to be turned into real project docs

`CONTROLCODING.md` remains the host-agnostic rule/context source for AI tools.
The design and implementation documents are the deeper project planning layer.

## Step 2: Run The Engagement Apply

Run:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --engagement --answers-file handoff.json --project-root .
```

This applies the tier/runtime handoff generated by the chat-guided install flow.

### What it asks

- product tier: `Core`, `Agents`, or `Studio`
- backend policy
- local tandem/model details when relevant
- on `Agents` / `Studio`, an explicit specialist/backend matrix:
  active paths, backend/model per role, permission envelope, execution mode,
  and call limits
- advanced packs or specialist/backend-heavy paths only when they are actually relevant

`Core + manual consultation` stays separate from that matrix and remains
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
That is also the publish-first public package.

## Step 3: Validate The Installation

Run:

```bash
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

`doctor` checks that the installation is coherent: context files, hooks, config,
host profile, and other required pieces.

## Claude Code Hook Activation

Claude Code is the reference host for true inline hooks.

ControlCoding keeps its canonical hook configuration in:

```text
.controlcoding/settings.json
```

Claude Code does not read that file directly. For Claude Code, setup also writes
this local adapter:

```text
.claude/settings.local.json
```

That adapter is what makes Claude Code actually invoke the hook scripts. It
contains machine-specific absolute paths and should not be committed.

After setup, verify:

```bash
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

For Claude Code, also check that `.claude/settings.local.json` exists. If the
hook scripts exist but the Claude adapter does not, Claude Code will not run the
hooks.

## Other AI Hosts

Some hosts do not provide true pre-write hooks. In those hosts, ControlCoding
uses a different protection path:

- host-native context instructions
- git pre-commit boundary checks
- post-commit or manual review gates
- project tests and invariants
- optional explicit patch gateway through `cc write-path`

Do not describe non-inline hosts as having Claude-style pre-write protection.

## Use It From An IDE Chat

If you work from Codex in VS Code, Claude in VS Code, or another IDE-hosted chat, use the official chat-guided setup path.

Print a ready-to-paste chat-guided setup prompt with:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .
```

Replace `codex_cli` with `claude_code`, `gemini_cli`, or another supported host when needed.

The intended flow is:

- the chat reads the printed prompt
- the chat asks one setup question at a time
- the chat helps translate non-technical answers into provisional setup values when needed
- the chat emits a handoff JSON payload for `--answers-file`
- if the host can execute local commands, the chat runs `setup`, `setup --engagement`, and `doctor` itself with `--apply-answers`
- after install, the chat may offer the separate `setup-project` flow, but only if you explicitly want it
- the default memory bootstrap remains governed-scope only unless you opt into a broader scan
- only if the host truly cannot execute local commands does it fall back to asking you to run them manually
- the chat reviews the generated files after each step

### Recommended user-facing install ceremony

If you want a short instruction you can give directly to a host chat, use this:

```text
Install ControlCoding in this project using the local repository at /path/to/ControlCoding.
Act as the official chat-guided ControlCoding installation assistant.
Briefly explain what ControlCoding is and how the setup flow will work in the user's language.
Ask one question at a time.
Keep the actual next question in the visible reply, not only in reasoning.
Treat the likely current host as a recommendation, not as a forced choice.
Explain briefly the difference between Core, Core + manual consultation, Agents, and Studio before asking me to choose.
Recommend Core first.
Do not silently choose the major setup options for me. Ask me to confirm the main choices before applying anything.
If local commands are available, create handoff.json, run setup, setup --engagement, and doctor, then tell me exactly what files were written and whether doctor passed.
```

Replace:

- `/path/to/ControlCoding` with the real local path

If you want the repo to print the stricter official setup contract for benchmark
or debugging use, run:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --chat-guide --host-hint codex_cli --project-root .
```

### Recommended first choices

For most adopters:

- choose `Core` first
- choose `managed` documentation mode
- accept local project memory with governed-folder scope only
- choose `recommended` host workflow guidance
- choose `local` hooks
- stop after installation unless you already want CC to help define the project now

Choose `Core + manual consultation` when you want the `Core` baseline but also
want CC to prepare manual external consultation packets when a blocking question
appears. That path stays file-based and user-mediated under
`.controlcoding/external_consultation/`.

Choose `Agents` only when you are ready to work with explicit helper roles on
the chosen host through prompt, folder, and behavior contracts.

Choose `Studio` only for future/internal UI work after the lower layers are
already solid.

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

only when you intentionally want:

- a minimal bootstrap
- a non-interactive/scripted flow
- a recovery path when the full chat-guided setup flow is not the right tool

`init` is not the preferred first-time onboarding path for a normal project.

`init --preview-only` uses the same complete preflight as application. The target
must already be an ordinary directory. Preview reports planned creates and keeps,
or a conflict path and reason, without creating files, directories, Git state or
memory. Use the same `--central-hooks` choice for preview and apply:

```bash
python /path/to/ControlCoding/scripts/cc.py init --preview-only --project-root .
python /path/to/ControlCoding/scripts/cc.py init --project-root .
```

Minimal init preserves existing context, status, roadmap, bug documents and Git
hook scripts. Keeping a file does not validate its contents or demonstrate hook
enforcement. A retained Git script means init did not install its generated CC
gate there. Canonical context/config/settings take precedence; ordinary legacy
inputs remain unchanged, and absent canonical configuration can be created using
their custom fields. A legacy-only `CLAUDE.md` remains the context source.

Hook and fitness copies must be absent or byte-identical. Different copies
conflict regardless of mtime. Existing `cc_config.json` must be valid and
compatible with the requested local/central mode. Existing settings must already
contain the effective hook configuration; custom and foreign MCP entries remain
intact. An existing `.gitignore` must contain the complete block for the selected
config, including its artifact policy; a start marker alone is insufficient.
Compatible files retain their exact bytes and metadata, including CRLF.

This conservative policy can require manual reconciliation before init works:

1. Run preview and inspect the reported file locally. Keep custom data and rules.
2. For settings, compare the hook entries with `BASE_SETTINGS` and the selected
   hook directory in `scripts/cc.py`. Generated shell commands use quoted absolute
   script paths; `_resolve_hook_commands` defines their spelling. Add or reconcile
   the required entries deliberately without removing foreign hooks or MCP data.
3. For `.gitignore`, compare with `_build_gitignore_block` in that source, using
   the effective `documentation_mode`, `cc_artifact_mode` and local/central choice.
   Preserve unrelated patterns while reconciling the complete required block.
   For configuration, review the reported mode/value conflict explicitly.
4. Run preview again, then apply only after the conflicts are resolved.

Do not delete user settings, remove custom Git hooks, or disable protection to
bypass a conflict. Familiar filenames, markers, old shipped text and timestamps
are not permission to replace a file. Init has no force-overwrite option.

Absent files are published exclusively. A concurrent destination causes failure
and remains intact; unsupported publication fails without a replacement fallback.
Init rejects symlinks, junctions/reparse paths and special files in relevant
roots, parents, inputs and destinations. A `.git` indirection file is unsupported;
init does not follow it into another Git directory. These checks are bounded
local preservation measures, not a universal hostile-filesystem guarantee.

Preflight is not rollback: a late I/O failure can leave earlier created outputs.
The nonzero result reports partial initialization and the recorded output paths;
inspect those paths and any reported temporary-stage cleanup problem before
retrying. Base `setup` stops after a nonzero init result, before host assets,
adapter sync, memory, packs, backend settings changes and doctor. **Earlier setup
context/config/Git work may already remain.** Canonical-context regeneration and
other setup stages have separate behavior; whole setup is not preservation-safe
or atomic under this minimal-init contract. Update/removal behavior is also
outside this contract.

## Migrating A Historical Host Adapter

Do not use `--force` to claim an existing adapter. Start with a preview:

```bash
python /path/to/ControlCoding/scripts/cc.py context sync --host <host> --preview-only --project-root .
python /path/to/ControlCoding/scripts/cc.py context adopt --host <host> --project-root .
```

The first command reports the ownership state. A valid-owned adapter can be
synced after review. An `unmarked` or explicitly `foreign` target requires the
separate adoption preview above and, only when the preflight permits it, this
explicit apply:

```bash
python /path/to/ControlCoding/scripts/cc.py context adopt --host <host> --apply --project-root .
python /path/to/ControlCoding/scripts/cc.py context sync --host <host> --project-root .
```

Invalid, ambiguous, and unreadable or unsafe targets remain blocked and cannot
be adopted. The flow does not promise partial modification or rollback beyond
the verified adapter transaction contract.

## Manual Path

If you deliberately want to bootstrap by hand, you can still:

1. create `CONTROLCODING.md`
2. derive the host-native file with `cc export host-context --host <host>`
3. wire hooks manually
4. run `cc doctor`

But that is the advanced/manual path, not the recommended path for a new adopter.

`cc doctor` is the canonical check after setup. It now reports the selected
host's `inline gate`, `repo boundary gate`, `review gate`, and
`verification gate` explicitly, including what is mechanical versus
conditional/advisory.

For automation or audits, use `cc doctor --json`. The JSON report contains an
`operationalContract` block with explicit readiness fields:
`safeForHumanWork`, `safeForAiAssistedWork`, `safeForAutonomousWork`, and
`releaseReady`. Treat those fields as the operational truth. A project can pass
basic health checks while still being unready for autonomous AI work if the host
profile, verification contract, invariant manifest, boundary gate, review gate,
or verification gate is missing.

The same JSON report includes `constitutionDrift` when a canonical
`CONTROLCODING.md` exists. This check catches a stale project constitution: host
files generated from old rules, protected zones configured in `cc_config.json`
but absent from the context, or active invariant ids that are not mentioned in
the Domain Invariants section. Run it directly with:

```bash
python /path/to/ControlCoding/scripts/cc.py context drift --host <host> --project-root .
```

To turn domain knowledge into candidate invariant tests, use the elicitation
command before editing the invariant manifest:

```bash
python /path/to/ControlCoding/scripts/cc.py invariants elicit --domain finance --write --project-root .
```

This writes a draft under `docs/invariants/` with domain questions, candidate
properties, suggested thresholds, test paths, and pytest commands. It is still
documentation until you create executable tests and add active manifest entries.

To inspect the protected properties themselves, use:

```bash
python /path/to/ControlCoding/scripts/cc.py invariants report --project-root .
```

This prints the invariant ids, domains, properties, thresholds, executable
status, and latest local run evidence. It is the human-readable view of what
the invariant manifest is actually protecting.

To diagnose the operational state of the invariant gate, use:

```bash
python /path/to/ControlCoding/scripts/cc.py invariants doctor --project-root .
```

This reports local manifest configuration and recognized CI command text patterns.
Legacy state/control-level labels do not establish execution or enforcement.
Current local receipt assessment is separate; hosted execution and required
server checks remain unverified.

To prepare CI enforcement for the invariant gate, use a dry run first:

```bash
python /path/to/ControlCoding/scripts/cc.py invariants wire-ci --project-root .
```

Then write the GitHub Actions workflow when the command and manifest are correct:

```bash
python /path/to/ControlCoding/scripts/cc.py invariants wire-ci --write --project-root .
```

This creates `.github/workflows/controlcoding-invariants.yml`, a generated CI plan.
Review and commit the workflow, then separately verify successful hosted execution
and required server checks. Generating or detecting the file proves neither.

For hosts without native inline hooks, the project should not be considered
healthy until the repo-side boundary path is actually wired.

If you want a stricter advanced path for a non-inline host, enable the optional
controlled write prototype:

```bash
python /path/to/ControlCoding/scripts/cc.py write-path enable --mode patch_gateway --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path prepare --patch-file proposed.diff --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path apply --patch-file proposed.diff --manifest-id <manifest_id> --project-root .
python /path/to/ControlCoding/scripts/cc.py write-path status --project-root .
```

That path is opt-in and explicit. It can block protected-zone writes before a
patch is applied, but only when the write actually goes through `cc write-path`.
If you enable manifest gating, `prepare` and `apply --manifest-id` become the
mechanical two-step path, including a baseline snapshot of the files touched by
the patch.
If you enable `--preflight-fitness`, `apply` first runs the patch through a
shadow-worktree `fitness_check.py` preflight and blocks the real apply on hard
architectural violations.
Each terminal outcome is also written to
`.controlcoding/write_path_receipts/` for local auditability.

For `Agents / human_mediated`, the bounded manual loop is:

```bash
python /path/to/ControlCoding/scripts/cc.py consult-packet create --project-root . --role architect --objective "Validate repo-side boundary split" --question "Should gateway branching move into a dedicated adapter?"
python /path/to/ControlCoding/scripts/cc.py consult-packet show --project-root . --packet-id <packet_id>
python /path/to/ControlCoding/scripts/cc.py consult-packet create --project-root . --role architect --thread-id <thread_id> --objective "Follow up on the adapter split" --question "What should move first?"
python /path/to/ControlCoding/scripts/cc.py consult-packet create --project-root . --role architect --topic-key adapter_split --objective "Compare alternate adapter split" --question "Should option B be rejected?"
python /path/to/ControlCoding/scripts/cc.py consult-result import --project-root . --packet-id <packet_id> --summary "Keep the split, isolate adapter logic." --decision partial --rationale-summary "Current split works, but gateway branching is leaking across layers." --next-action "Extract the adapter module."
python /path/to/ControlCoding/scripts/cc.py consult resolution --project-root . --role architect --topic-key adapter_split
python /path/to/ControlCoding/scripts/cc.py consult status --project-root .
```

This path keeps the helper role explicit and host-driven. It does not claim
hidden routed orchestration parity. API-backed routed specialists belong to the
later Version II path.

For the current host capability/evidence overview:

```bash
python /path/to/ControlCoding/scripts/cc.py benchmark-matrix generate --project-root .
```

The import stores concise engineering summaries only. It does not persist raw
chat transcripts or imply routed/automatic specialist execution parity.
Manual consultation artifacts live under `.controlcoding/external_consultation/`.
Each manual thread also writes `thread.json`, `memory.md`, and
`resume_prompt.md` under `.controlcoding/external_consultation/threads/` so a
new external chat can resume from the current bounded state.
Cross-consultation continuity is summarized automatically in
`.controlcoding/external_consultation/role_memory/` and
`.controlcoding/external_consultation/convergence_summary.md`.
Use `--topic-key` when separate manual consultations should merge around the
same normalized decision topic instead of staying isolated.
If that merged topic becomes conflicted, `cc consult resolution` shows the
generated resolution artifact and prompt path for the next bounded manual chat.

## After Base Install

### Optional feature packs and existing files

Preview a pack before installing it, including all packs at once:

```bash
python scripts/cc.py install all --preview-only --project-root "/path/to/project"
python scripts/cc.py install session-manager --project-root "/path/to/project"
```

The preview reads sources and the target project but creates no files or
directories, even when the target project does not exist. Mutating installation
requires an existing project directory. Preview reports proposed files, identical-file skips, settings actions,
and the `multi-agent` `.bridge` and sibling helper paths. A conflict returns a
nonzero result. Installation checks every selected pack before writing. An
existing pack file with identical bytes is left untouched; a differing file or
unsafe path blocks installation, even if its name matches a shipped template.
Reconcile such a file explicitly before retrying. There is no force overwrite or
automatic upgrade of edited pack files. Existing helper directories are left
intact, and existing MCP server entries are retained.

Pack installation never rewrites an existing `.controlcoding/settings.json`.
If all required MCP entries are already present, it skips that file and preserves
its exact bytes and metadata, including custom configuration. If entries are
missing, both preview and apply return a settings conflict before any selected
pack file, directory, bridge or helper is created. Sequential pack additions and
some advanced-pack `setup` flows therefore require explicit settings reconciliation.
Review the selected pack's entries in `MCP_CONFIGS` in `scripts/cc.py` and deliberately
add the needed configuration to your settings while retaining custom/MCP data,
then preview again. Do not delete settings to bypass this conflict. No force
overwrite or automatic merge is provided. When canonical settings are absent,
installation retains legacy settings as input, leaves the legacy file untouched,
and creates canonical settings exclusively.

The preflight prevents known conflicts in later selected packs from changing
earlier ones. An I/O failure during application can leave a partial install;
inspect the reported paths before retrying. New pack files and canonical settings
use exclusive publication: a concurrent destination is not replaced. Existing
settings are only checked and skipped; changed snapshots cause a conflict.
These checks do not establish protection against every hostile process,
filesystem or platform race. Base `setup` can already have written other output
before a pack conflict; it stops dependent steps and reports partial setup without
rollback. Base `init`, and later update/removal work, have separate boundaries.

- If you only want the structured baseline, stop after `Core` and start working.
- If you want the optional specialist/runtime layers after base setup, continue with [ecosystem-quickstart.md](./ecosystem-quickstart.md).
- For a compact install summary, see [quick-start.md](./quick-start.md).
- For the broader walkthrough with zone classification and domain examples, see [adoption-guide.md](./adoption-guide.md).
