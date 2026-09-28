# Guided panel configuration

The development desktop has a three-step Setup page for two decision phases:
direct project choices, then optional AI proposals and review. The last step
previews either a draft save or a bounded Core installation. This increment does
not implement the complete CLI installation wizard or change the accepted RC3
observer archive. Independent acceptance of the development candidate is pending.

## Saved state and actions

- `.controlcoding/panel-setup-draft.json`: version 1 project choices and decision
  records. Each decision keeps its mode, value, review status, rationale, relative
  evidence paths and analysis request identity. Saving this file alone does not
  install hooks, initialize memory, restructure source files or run commands.
- `.controlcoding/panel-setup-receipt.json`: hashes of outputs from an applied
  plan. It identifies unchanged panel-generated files for a later reviewed
  update. It is local bookkeeping, not authentication or verification evidence.
- Existing `.controlcoding/cc_config.json` and `gateway_config.json` remain the
  executable configuration owners. The draft does not silently override them.

Project kind and intent are descriptive. Stack, architecture, working rules and
invariants feed the canonical context through the shared Core renderer. Boundary
rows use `path | stable/shared/features | none/warn/deny`; accepted rows populate
module classifications and protected zones. Existing protections and unrelated
configuration keys are preserved. Removal of old protections requires separate
reconciliation. Advisory verification commands are recorded, never executed.

Core installation uses local hooks, local-only artifacts, the Core planning tier
and deferred memory. It reuses detached minimal-init planning, context rendering,
gateway configuration and host-asset generation. The page shows file actions,
source-bound hashes, chosen settings, conflicts and coverage before confirmation.
Git gates are generated only when an ordinary `.git/hooks` directory already
exists. An existing Git hook is retained; retaining it does not prove that it
delivers a ControlCoding gate. No repository is initialized.

Existing foreign context or host assets that differ block application. Previously
generated files may be updated only when their current bytes still match the
last panel receipt. Existing hook/config compatibility conflicts, a foreign
`.gitignore` without the complete Core block, Git worktree indirection and central
hook installations require explicit reconciliation outside this bounded flow.
There is no force-overwrite button. The draft can be saved while installation is
blocked. A context-only or draft-only state must not be described as verified.

## AI handoff and review

The panel can prepare a packet for the user's current chat/editor. It uses the
bounded Project Map inventory plus up to 16 selected ordinary project-relative
design documents. Its source references carry the adapter's identities and
coverage limitations. Selected design excerpts are included (64 KiB source limit,
4,000 characters each, 12,000 total), explicitly marked when truncated and bound
to the request identity. The packet includes choices and references, not the full
codebase. The external AI host needs its own authorized access to the project.
No provider is contacted and no project data is automatically transmitted.

An imported JSON response must match the request identity, current draft and
observed sources. Only fields marked for AI can receive proposals. Every proposal
needs a rationale and evidence from the packet inventory. Import never accepts
a decision or writes project files. The user can accept, reject and defer, or
edit a proposal as a direct decision. Unreviewed AI fields block Core application,
but not a draft save. Accepted decisions persist as user choices; their stored
analysis identity is provenance, not a promise of continuing source freshness.

## Boundaries and failure handling

The renderer cannot choose write destinations, executables or command lines.
The main process retains the selected project, draft revision and reviewed plan;
final confirmation takes no renderer-supplied approval or path arguments. Edits
invalidate the pending plan. Project switching clears its state and is refused
during a write. Normal application exit is postponed while committing. Unexpected
process/OS termination remains possible.

Python regenerates the plan before committing and compares its identity. The
writer then revalidates snapshots, pins ancestors, exclusively opens existing
outputs, creates absent files with exclusive creation, and records original
contents in `.controlcoding/panel-setup-transaction.json`. A normal failure rolls
back owned file contents. Empty created directories can remain. Success removes
the journal. A process interruption may leave partial files and the journal:
subsequent setup operations refuse to proceed until manual reconciliation. The
journal contains backups and must not be blindly deleted. This is recoverable
multi-file writing, not a filesystem-wide atomic transaction.

Writes are Windows-only on ordinary local filesystems. Linked/special files and
hardlinks are rejected through retained snapshots. The helper has a 15-second
deadline, a 64 KiB request limit and a 1 MiB response limit. Drafts are limited to
48 KiB, imported proposals to 16,000 bytes at the desktop boundary, and analysis
packets to 192 KiB. Setup uses the existing 1 MiB/file, 8 MiB target, 8 MiB trusted
source and 256-entry observation budgets. No runtime, library or model is installed.

Portable archive initialization/import is available separately in
[ControlWork management](controlwork-management.md). The separate
[execution workflows](panel-execution.md) now provide the canonical Core wizard,
governed Dev Plane initialization, doctor/tests and advisory provider conversations
with optional per-exchange archival. Host-delivery validation, autonomous code
editing, signing and publication remain separate.

## Verification entry points

- `tests/test_cc_panel_configuration.py`: save/reopen, install/repeat per host,
  stale inputs, foreign preservation, linked-file refusal, proposal review,
  normal rollback and actual process interruption with recovery diagnosis.
- `ui/tests/configuration-unit.cjs`: bounded contracts, main-owned approvals,
  project switching, delayed replies, edits and errors.
- `ui/tests/configuration-desktop.cjs`: actual Electron/isolated Python,
  mouse/keyboard save and installation, proposal import/review, reloading,
  external edits, foreign conflicts and all three stages at 320 CSS pixels.

All fixtures, profiles, screenshots and reports belong in an external workbench.
No test should apply configuration to the development repository or a real adopter.
