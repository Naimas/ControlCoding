# Documentation Maintenance

Status: Current reference for the experimental docs-maintenance commands.

The production capability registry classifies `docs_maintenance` as
`experimental` with a `conditional` control level. The commands are routed
and covered by repository contracts, but this classification does not promote
them to a stable or generally guaranteed capability.

## Command Behavior

### `docs audit`

`cc docs audit` performs a read-only inspection of architecture and
system-document maintenance state. It reports errors, warnings, and
informational findings in text or JSON. Its `--since` option selects the Git
reference used for changed-file drift checks.

The audit covers:

- required architecture and system documents;
- configured references from `docs/INDEX.md` and
  `docs/controlcoding-system-architecture.md`;
- likely documentation owners for changed code and system files;
- public command and control-claim checks when a release manifest is present;
- status markers on plan-like documents;
- duplicate audit content and audit-like reports outside their intended area.

Errors make the audit fail. Warnings remain non-blocking audit findings.

### `docs check`

`cc docs check` evaluates the same audit payload in a CI-oriented form. It
fails on errors. With `--strict`, warnings are converted into a blocking
finding. The repository verification contract includes this command as a
required targeted suite.

The command inspects documentation state only. It does not repair documents,
run the broader verification contract, or apply proposals.

### `docs propose`

`cc docs propose` runs the audit and writes a review artifact. By default the
proposal is stored under `.controlcoding/docs/proposals/`; `--output` may
select another location inside the project root.

Proposal creation is separate from audit success. A proposal can record
blocking audit findings, but it does not update canonical documents. A human
must review the findings and make any source changes deliberately.

## Inspection, Proposal, and Human Change

The maintenance flow separates three responsibilities:

1. `audit` and `check` inspect current state without writing files.
2. `propose` records the findings in a non-canonical local artifact.
3. A human reviews the proposal and edits the owning documents through the
   normal repository workflow.

No command in this family rewrites `CONTROLCODING.md`, the system architecture,
memory documentation, or generated host context.

## Release-Aware Manifest Behavior

When `controlcoding.release.json` is absent, the audit uses the development
document contract. When the manifest is present, the audit uses the release
profile and validates the manifest before relying on it.

For a valid release manifest:

- denied documents are not required as files or architecture references;
- required Markdown files are added to the required-document set;
- configured system documents that are not denied remain required;
- release-specific public-truth checks are included.

An invalid manifest produces errors and fails closed. Repository role is not
inferred from a directory name or from a missing host-context file. The docs
commands read the manifest but do not modify it.

## Reference Checks and Their Limits

Required-document checks establish whether configured files exist. Reference
checks establish whether configured relative reference strings appear in
`docs/INDEX.md` and the system architecture overview, after release deny rules
are applied.

These checks do not:

- crawl every Markdown link;
- resolve anchors or external URLs;
- prove that a linked document is complete or semantically correct;
- establish that every implementation change has the right documentation;
- replace editorial or architectural review.

Changed-file ownership findings use configured path-to-document mappings and
are maintenance signals, not proof of semantic drift.

## Boundaries With Neighboring Commands

| Command family | Separate responsibility |
|---|---|
| `cc context` | Checks, synchronizes, and explicitly adopts managed host-context projections. Docs maintenance does not regenerate them. |
| `cc index` | Checks architecture-index membership and owns any explicit fix path. Docs maintenance only reports related document state. |
| `cc organize` | Previews taxonomy changes by default and moves files only with `--apply`. Docs maintenance never moves files. |
| `cc memory` | Owns local memory, generated views, retrieval, and Project Plane operations. Docs maintenance does not rebuild memory state. |
| `cc truth` | Owns the command and capability truth contract. Release-profile docs audits complement those checks but do not replace them. |

The audit may read existing ControlWork output paths when checking for duplicate
audit artifacts. It does not call ControlWork, synchronize repositories, write
ControlWork state, or expose equivalent ControlWork maintenance commands.

## Operational Limits

The commands scan only inside the selected project root. They do not call
network services or an LLM. If Git status is unavailable, changed-file drift is
reported as not evaluated while the remaining document checks continue.

Generated proposals and other local runtime artifacts are excluded from the
public source distribution by the release manifest. Canonical documentation
changes remain ordinary reviewed source changes.
