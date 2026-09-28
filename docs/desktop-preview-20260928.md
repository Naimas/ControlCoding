# Desktop development preview — September 28, 2026

[Download the preview and source archives](https://github.com/Naimas/ControlCoding/releases/tag/desktop-preview-20260928).

This publication makes the current development work available for inspection and
testing. It does not close the full product plan or declare a stable release.
The Core source remains in this repository. The optional desktop source, tests
and dependency lock are supplied separately as `controlcoding-desktop-source-20260928.zip`.
The portable app is `observer-work-gantt-20260928.zip`; it includes its exact
Core/UI source inputs and the pinned Windows x64 Electron runtime.

## Start the portable app

Extract the portable ZIP into a new empty folder. Python 3.11+ must already be
installed. From that folder in PowerShell:

```powershell
.\Launch-Observer.ps1 -Python 'C:\path\to\python.exe' -Check
.\Launch-Observer.ps1 -Python 'C:\path\to\python.exe'
```

The check verifies the packaged manifest and runtime prerequisites. No installer
or signature is provided. The existing PowerShell execution policy applies.
Choose the project in the app, and select **ControlWork → Work plan & Gantt →
Refresh work plan** for planning. Memory must be enabled for the chosen scope;
only recorded work and current approved relations become tasks/dependencies.
See [work planning](work-schedule.md), [unified knowledge](unified-knowledge.md)
and [memory consolidation](knowledge-consolidation.md) for configuration.

Existing knowledge archives are not silently migrated. Consolidation requires
an explicit verified backup/migration and opt-in curator/execution settings.
Extract later builds into another folder and retain the old version for rollback;
do not overwrite a project or an existing package.

## Verification and identity

The release includes `SHA256SUMS.txt` for both downloads. The portable archive
contains a per-file `MANIFEST.json`, and its build records input/output hashes.
The actual packaged app/Core/Electron passed 15 work-planning desktop checks, including persistence,
dependency order, source invalidation and a 320 CSS-pixel viewport. The related
Python suites passed 39 checks on each of Python 3.11 and 3.13; related Node suites
passed 36 checks. Strict TypeScript, build and UI distribution inventory passed.

The preceding consolidation candidate passed 324 knowledge tests per Python
runtime with one unavailable Windows symlink case skipped, 97 relevant Node
checks, 18 packaged desktop checks and 61 package tests. These are separately
bound receipts; they are not a fresh full-suite result for the Gantt artifact.
The local model completed four final synthetic consolidation scenarios; earlier
operational failures remain recorded. These samples are not human quality grades.

The publication package retains the verified application/runtime and includes
the reviewed publication documentation and transaction-guard test correction.
Its internal `unsigned-local-candidate-review-pending` status means that publishing
it as a prerelease does not imply independent review approval. The source archive
and portable ZIP checksums are attached to the release.

## Open acceptance work

Independent answer/citation grading, 20 real task resumptions, seven elapsed days
on software and document adopters, a live remote API trial and wider plan/release
review remain open. Process parallelism is constrained by recorded dependencies;
the Gantt does not optimize available people or agents. AI proposals require
review and do not autonomously certify completion or edit project code.
