# ControlCoding Project Setup Wizard Contract

Use this file when an AI host chat should guide a user through project definition and kickoff setup after ControlCoding is already installed.

## Purpose

This wizard is for:

- reading the user's brief, notes, README, or existing design material
- deciding whether the project is greenfield or an existing mature repo
- preparing the first governed ControlCoding project package

It is **not** the ControlCoding installation flow.
If ControlCoding is not installed yet, the host should stop and switch to `INSTALL_WIZARD.md`.

## Wizard Behavior

1. Explain briefly, in the user's language:
   - that this is the project-definition and kickoff flow
   - that ControlCoding installation is assumed to be complete
   - that the goal is to establish a trustworthy design and implementation baseline before coding
2. Ask one question at a time.
3. Keep the actual next question in the visible reply, not only in reasoning.
4. Before asking the user to name a brief manually, search for likely
   candidates read-only, using only obvious bounded paths and filenames such as:
   - `project_brief.md`
   - `README.md`
   - files containing `brief`, `requirements`, `spec`, or `design` in the name
5. Do not move, rename, copy, OCR, or import candidate brief files during discovery.
6. If one strong candidate exists, ask whether that file should be treated as the brief.
7. If several plausible candidates exist, show a short list and ask which one should be treated as the brief.
8. Also inspect whether the repo already looks mature. If it does, offer the brownfield adoption path instead of pretending the project starts from zero.
9. Do not silently choose the major project-shape answers. Recommend defaults, but ask for confirmation first.
10. Present every major project-shape choice as a recommendation card before asking the question:
   - decision
   - recommended option
   - why it fits
   - one real alternative
   - when that alternative is better
   - exactly one user-facing question
11. Do not ask naked confirmation questions such as `confirm existing_brief?`, `confermi desktop?`, or `skip for now?`.
12. If local commands are available, the host may execute the project-setup apply flow itself.
13. After project setup succeeds, report:
   - what files were written or refreshed
   - which source material was treated as authoritative
   - what gaps or unresolved items remain

## Major Project-Shape Choices To Confirm

- source mode: existing brief/doc, interactive definition, or brownfield adoption
- selected brief/reference file
- kickoff readiness: idea, partial spec, or existing design direction
- brownfield adoption mode when applicable
- product form and runtime constraints
- target platforms
- stack direction
- architecture direction
- truth/view split when relevant

## Required Outcome

The project setup wizard should establish:

- a canonical setup intent snapshot in `.controlcoding/setup_intent.json`
- recommendation cards for the accepted major project-shape choices
- recommendation guidance examples for `core_greenfield` and `core_brownfield`
- source assessment
- consultation planning when source material is weak or ambiguous
- design package
- criteria and contracts
- implementation package
- progressive protection planning

Selected source files remain in their original locations by default. Project
setup may reference a selected source in generated project-definition artifacts.
Copying a reviewed summary or importing source material into a governed folder
requires separate confirmation.

For mature repos it should also produce:

- existing-project inventory
- document truth map
- architecture extraction
- maturity and gap assessment
- adoption plan

## Backend Apply Rule

From the target project root, the backend apply step is:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --answers-file handoff.json --apply-answers --project-root .
```

The host should save the confirmed project-shape answers as `handoff.json`
before running the local apply step. To print the project setup guide instead
of applying answers, use:

```bash
python /path/to/ControlCoding/scripts/cc.py setup-project --chat-guide --project-root .
```

The host should run the local apply itself when possible after confirmation.
Only hosts that truly cannot execute local commands should ask the user to run commands manually.

## Minimal Prompt To Give An AI Host

```text
Read PROJECT_SETUP_WIZARD.md from the ControlCoding repository and use it as the project setup contract.
Set up this project with ControlCoding after installation.
Look for an existing brief or design file before asking me to name one.
If the repo already looks mature, offer the brownfield adoption path.
Ask one question at a time and do not apply anything until I confirm the major project-shape choices.
```

## Recommendation Card Examples

Core greenfield:

```text
Decision: project_definition_mode
Recommended: `guided` because no authoritative brief is confirmed, so the safest Core path is to collect the minimum framing before generating kickoff docs.
Alternative: `existing_brief` - use it if there is a real brief, README, requirements file, or design document to treat as authoritative.
Question: Should we define this as a new Core project now, or use an existing source file as the brief?
```

Core brownfield:

```text
Decision: project_definition_mode
Recommended: `existing_project` because the repository already appears to contain source, tests, docs, or manifests, so setup should preserve existing truth before generating new plans.
Alternative: `existing_brief` - use it if there is one authoritative brief and you do not need a repo inventory or adoption pass.
Question: Should this be treated as a brownfield adoption pass for an existing project?
```
