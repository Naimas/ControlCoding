# ControlCoding Installation Wizard Contract

Use this file when an AI host chat should guide a user through installing ControlCoding on a project.

## Purpose

This wizard is for installing and configuring ControlCoding itself.

It is **not** the project-framing or kickoff-doc wizard.
Project setup is a separate second step that may be offered only after ControlCoding installation succeeds.
The canonical file for that second step is `PROJECT_SETUP_WIZARD.md`.

## Wizard Behavior

1. Explain briefly, in the user's language:
   - what ControlCoding is
   - what it is for
   - how this installation wizard will work
2. Ask one question at a time.
3. Keep the actual next question in the visible reply, not only in reasoning.
4. Do not batch multiple wizard questions into one message.
5. A short install request is permission to start the wizard, not permission to auto-apply all defaults silently.
6. Before applying anything, ask the user to confirm the main installation choices.
7. If local commands are available, the host may execute the install itself.
8. After installation succeeds, report:
   - what files were written
   - whether `doctor` passed
9. Only after that, ask whether the user wants to start the separate project setup wizard.

## Main Choices To Confirm

The installation wizard must confirm these choices before applying:

- project name
- primary host
- usage model
- documentation mode
- Project Memory Engine and GraphRAG default memory policy
- host workflow guidance
- engagement tier
- backend policy when relevant

Use project-local hooks as the public default.
Do not surface central/shared hooks unless the user explicitly asks for an advanced multi-project override.

## Core Memory Default

Project Memory Engine and GraphRAG are part of ControlCoding Core.

Base install should initialize local project memory by default with
governed-folder scope only. That means setup may create the local
`.controlcoding/` memory layout and index only governed ControlCoding or
ControlWork surfaces:

- `CONTROLCODING.md`
- generated host context files such as `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.clinerules`
- `CONTROLWORK.md`, when embedded Project Plane exists
- `project-definition/`, `design/`, `criteria/`, and `contracts/`
- `.controlwork/memory/`

Default install must not run a full repository scan, OCR, document layout
adapters, vector rebuild, file relocation, graph promotion, or broad
source/document indexing.

Ask this explicit question before applying:

```text
Initialize local project memory now with governed-folder scope only?
```

Recommended default: yes.

## Usage Models

- `Core`
  - baseline serious structure and enforcement
  - recommended default
- `Core + manual consultation`
  - same baseline as `Core`
  - one bounded manual external consultation path may be used when planning gets blocked
- `Agents`
  - `Core` plus explicit specialist/runtime paths
  - requires more backend/runtime configuration
- `Studio`
  - `Agents` plus the full CC UI/control surface

## Host Contract Reminder

The wizard should explain the four gate slots using the selected host's real behavior:

- `inline gate`
- `repo boundary gate`
- `review gate`
- `verification gate`

On hosts without native pre-write hooks, the wizard must say clearly that there is no Claude-style inline parity.

## Canonical Install Commands

From the target project root:

```bash
python /path/to/ControlCoding/scripts/cc.py setup --project-root .
python /path/to/ControlCoding/scripts/cc.py setup --engagement --project-root .
python /path/to/ControlCoding/scripts/cc.py doctor --project-root .
```

Optional second step after installation:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --chat-guide --project-root .
```

These backend commands are for the host to execute when local command execution is available.
They are not meant to be the primary user-facing ceremony.

## Minimal Prompt To Give An AI Host

```text
Read INSTALL_WIZARD.md from the ControlCoding repository and use it as the installation contract.
Install ControlCoding in this project from the local ControlCoding repository.
Act as the official installation wizard.
Ask one question at a time and do not apply anything until I confirm the main choices.
```
