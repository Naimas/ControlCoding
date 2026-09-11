# File Organization Standard

> This document defines the canonical folder structure, filename convention,
> lifecycle, and indexing rules for ControlCoding itself and for projects that
> adopt ControlCoding.

## 1. Scope

This standard applies at two levels:

1. **ControlCoding repository**
   The methodology/product repo needs a rich internal structure because it
   contains public docs, internal design, plans, research, business/legal
   notes, handoffs, and historical prior art.
2. **Adopter projects**
   Projects using ControlCoding can use the same logic, but may start
   from a smaller baseline and add categories only when needed.

### 1.1 Documentation ownership modes

ControlCoding itself always uses:

- `documentation_mode = managed`

Adopter projects must choose one of two explicit modes during setup:

- `managed`
  ControlCoding owns the governed-document taxonomy for that repo. It may
  create the canonical folders, expect canonical filenames, maintain local
  indexes, and enforce the file-organization hook.
- `project_managed`
  The adopter keeps its own documentation structure and lifecycle. ControlCoding
  must not auto-reorganize documentation, rename governed docs, or block
  non-canonical documentation paths just because they differ from this standard.

Rule:

- ControlCoding's own repo is always `managed`
- adopter projects must be asked explicitly at setup time
- when an adopter declines, ControlCoding must respect the project's native
  documentation conventions
- this choice affects documentation ownership only; it does not disable the
  standard Commit Ceremony

### 1.2 CC artifact storage modes

Documentation ownership and version-control policy are separate concerns.

Adopter projects should choose an explicit `cc_artifact_mode`:

- `local_only`
  Default for adopter projects. CC working documents remain local and
  gitignored. Update them at each significant milestone, but do not treat them
  as repository content by default.
- `shared_repo`
  Explicit opt-in. The team intentionally tracks CC working documents in git.

Default recommendation:

- adopter projects: `cc_artifact_mode = local_only`
- ControlCoding's own repository: keep maintainer working docs local-only and
  publish only the public framework surface

Repository policy split:

- **ControlCoding repository**: maintainer working docs such as `CLAUDE.md`,
  `STATUS.md`, `ROADMAP.md`, `BUGS.md`, `dev/`, `knowledge/`, and `devlog/` stay local-only
  and are not part of the published git history
- **Adopter projects**: those same CC working docs are local-only by default
  and become shared only with explicit `cc_artifact_mode = shared_repo`

Non-negotiable devlog rule:

- `devlog/` is local session memory
- it is created and updated during the Commit Ceremony
- it should not be treated as shared repository content by default
- `cc_artifact_mode` governs governed CC working docs, not the devlog itself

In `local_only` mode, the usual local CC working docs are:

- `STATUS.md`
- `ROADMAP.md`
- `BUGS.md`
- `dev/` when the project is also `documentation_mode = managed`
- `knowledge/` when the project enables managed system or domain knowledge

The Commit Ceremony still updates these artifacts locally. `local_only` changes
whether governed CC docs are tracked in git, not whether they exist. `devlog/`
remains local session memory in both storage modes.

### 1.3 Control plane isolation for adopter projects

The Project Memory Engine target keeps ControlCoding working memory
project-local but isolated under:

```text
.controlcoding/
```

This directory is the ControlCoding control plane. It may be deleted to return
the project folder to a clean application-only state.

The planes are separate:

- **project plane**: source code, tests, package files, product docs, and other
  files that belong to the actual application
- **ControlCoding control plane**: `.controlcoding/` configuration, development
  memory, generated views, sessions, handoffs, logs, plans, research, and
  indexes
- **application memory plane**: product-owned runtime or domain memory such as
  corpora, vector stores, application knowledge graphs, caches, ledgers, or
  user data
- **host integration plane**: host-required files such as `.claude/`,
  `.vscode/`, `AGENTS.md`, `CLAUDE.md`, or other derived host projections

Rule:

- ControlCoding may reference application memory components as project
  components
- ControlCoding must not absorb application memory contents as ControlCoding
  truth
- application code, build, tests, and runtime behavior must not depend on
  `.controlcoding/`
- root-level ControlCoding docs are compatibility projections or explicit
  opt-in shared artifacts, not required application files

## 2. Canonical Structures

### 2.1 ControlCoding maintainer local structure

This is the local working structure used by framework maintainers. It is not
the published git surface.

```text
CLAUDE.md
STATUS.md
ROADMAP.md
BUGS.md
devlog/
  index.md
  YYYY-MM-DD_NNN_slug.md
dev/
  ARCHITECTURE_INDEX.md
  methodology_full.md
  plans/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
  design/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
  research/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
  handoffs/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
  legal/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
  business/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
  prior-art/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
knowledge/
  INDEX.md
  system/
    INDEX.md
  domain/
    INDEX.md
```

### 2.1b ControlCoding published repository surface

```text
docs/
  INDEX.md
  ...
scripts/
templates/
tests/
benchmarks/
ui/
```

### 2.2 Adopter project baseline (`managed` mode)

The current compatibility baseline may still create or recognize:

```text
CLAUDE.md
STATUS.md
dev/
  plans/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
  design/
    INDEX.md
    archive/
      INDEX.md
    deprecated/
      INDEX.md
devlog/
  index.md
```

In the recommended adopter configuration (`cc_artifact_mode = local_only`),
these files and folders normally exist as local working state and are
gitignored by the managed CC block.

The Project Memory Engine target moves governed ControlCoding working memory
into the isolated control plane:

```text
.controlcoding/
  views/
    STATUS.md
    ROADMAP.md
    HANDOFF.md
  dev/
    plans/
      INDEX.md
      archive/
        INDEX.md
      deprecated/
        INDEX.md
    design/
      INDEX.md
      archive/
        INDEX.md
      deprecated/
        INDEX.md
    research/
      INDEX.md
    handoffs/
      INDEX.md
  knowledge/
    system/
      INDEX.md
    domain/
      INDEX.md
  memory/
    memory.db
    indexes/
    generated/
  logs/
  sessions/
```

Root-level `STATUS.md`, `ROADMAP.md`, `dev/`, or `knowledge/` files should be
treated as projections or explicit shared artifacts once the Project Memory
Engine is available.

### 2.3 Optional adopter categories

Add these only when they become necessary:

In the Project Memory Engine target, these governed categories live under
`.controlcoding/` unless the project explicitly opts into shared projections.

- `dev/research/` for scientific study, audits, external comparisons, review notes
- `dev/handoffs/` for inter-session or inter-team transfer packages
- `dev/legal/` for compliance, licensing, privacy, policy, terms
- `dev/business/` for positioning, roadmap strategy, monetization, market notes
- `dev/prior-art/` for exploratory or superseded material worth keeping as reference
- `knowledge/system/` for stable as-built system knowledge promoted from design work
- `knowledge/domain/` for source-driven external corpora, claims, and syntheses when the project truly depends on them

## 3. Category Meanings

| Category | Purpose | Typical contents |
|---|---|---|
| `plans/` | HOW work will be executed | task plans, sequencing, acceptance criteria |
| `design/` | WHAT/WHY the system is designed that way | architecture, contracts, state models, rationale |
| `research/` | Evidence and investigation | reviews, comparative analysis, references, feasibility notes |
| `handoffs/` | Transfer packages | restart notes, phase handoff docs, operational context |
| `legal/` | Compliance and policy | licensing, ToS notes, privacy, obligations |
| `business/` | Product and strategy | audience, scope, monetization, release framing |
| `prior-art/` | Historical reference, not active truth | archived prototypes, old explorations, superseded concepts |
| `knowledge/system/` | Stable system knowledge | canonical runtime model, architecture baseline, invariants, flows, glossary |
| `knowledge/domain/` | Optional external corpus | sources, claims, concepts, syntheses, provenance-bearing notes |
| `devlog/` | What actually happened | chronological entries per significant change, local session memory rather than shared repo content |

## 4. Filename Convention

### 4.1 Canonical pattern

All governed documents should use:

```text
NN_KIND_ShortTitle_Status.md
```

Where:

- `NN` = stable sequence number inside the category, usually 2 digits
- `KIND` = short type code
- `ShortTitle` = concise identifier without spaces
- `Status` = lifecycle state

Examples:

- `02_DEV_AggNomenclature_Plan.md`
- `02_DEV_AggNomenclature_InProgress.md`
- `02_DEV_AggNomenclature_Completed.md`

### 4.2 Allowed kind codes

Recommended codes:

| Code | Meaning |
|---|---|
| `DEV` | development plan / implementation work |
| `DSN` | design |
| `RSH` | research |
| `HOF` | handoff |
| `LGL` | legal |
| `BIZ` | business |
| `PAT` | prior art |

Projects may add other short codes if they stay documented and consistent.

### 4.3 ShortTitle rules

- No spaces
- Use ASCII only
- Prefer `CamelCase` or another single repo-wide convention
- Keep it stable even when the document changes status

### 4.4 Status tokens

Canonical ControlCoding status tokens are:

- `Plan`
- `InProgress`
- `Completed`
- `Archived`
- `Deprecated`

Meaning:

| Status | Meaning |
|---|---|
| `Plan` | defined but not started |
| `InProgress` | actively being worked on, or actively maintained as the current living source of truth |
| `Completed` | work finished; typically archived for plans, handoffs, reports, and other closed documents |
| `Archived` | historical but valid record, moved out of active area |
| `Deprecated` | abandoned, replaced, or explicitly no longer to be followed |

### 4.5 Legacy filenames

Legacy documents may continue to exist temporarily with older names.

Rule:

- do not rename everything blindly
- migrate filenames when a document is touched substantially or when a category
  is explicitly reorganized

Tool support:

- `cc organize` is preview-only by default and reports proposed `devlog/`
  repairs without changing files
- `cc organize --apply` is required to execute proposed moves and writes
- `cc organize --dev-taxonomy` previews alignment of clearly named root-level
  or misplaced `dev/` files with the `dev/` taxonomy; add `--apply` to execute
  the reviewed plan
- `--dry-run` remains an explicit preview, and `--check` remains a non-applying
  control that reports violations through its exit status
- `--dev-taxonomy` does not move public `docs/`; public documentation movement
  remains an explicit editorial decision

## 5. Lifecycle and Folder Movement

### 5.1 Standard flow for plans

```text
Plan -> InProgress -> Completed -> archive/
```

Example:

```text
plans/02_DEV_AggNomenclature_Plan.md
plans/02_DEV_AggNomenclature_InProgress.md
plans/archive/02_DEV_AggNomenclature_Completed.md
```

### 5.1b Standard flow for design documents

Design documents follow a different operational pattern from plans.

Typical flow:

```text
InProgress (current design truth) -> archive/ when superseded
```

Interpretation:

- active design docs usually stay in the category root while they remain the
  current source of truth
- `InProgress` does not imply instability; it means the design is still the
  live governing document
- `Completed` is more appropriate for closed design-side outputs such as review
  reports, audit reports, or retired design snapshots that are being moved to
  `archive/`

### 5.2 Deprecated flow

If a document is abandoned, invalidated, or replaced without completion:

```text
Plan or InProgress -> deprecated/
```

Example:

```text
plans/deprecated/02_DEV_AggNomenclature_Deprecated.md
```

### 5.3 `archive/` vs `deprecated/`

This distinction is mandatory.

| Folder | Meaning |
|---|---|
| `archive/` | completed, superseded, or historically valid documents worth preserving as part of project truth/history |
| `deprecated/` | abandoned, replaced, or no longer recommended documents that should not guide current work |

Rule of thumb:

- if the document describes a path that was actually executed or remains useful as historical truth, use `archive/`
- if the document describes a path that was discarded and should not guide future work, use `deprecated/`

### 5.4 Legacy filename traceability

When a governed document is renamed to the canonical convention:

- keep the git history, do not recreate the document from scratch
- add a `Legacy filename` field near the document header when practical
- record the old-to-new mapping in the local archive `INDEX.md`

This keeps historical references discoverable even after the active filename has
changed.

## 6. Local Index Requirement

Every important documentation folder must contain a local `INDEX.md`.

At minimum, each local index should have these sections:

1. **Purpose**
2. **Active / current documents**
3. **Archive**
4. **Deprecated**
5. **Migration notes** (if legacy files still coexist)

### 6.1 Example local index

```markdown
# Plans Index

## Purpose
Implementation plans for the project.

## Active
- `02_DEV_AggNomenclature_InProgress.md` - document naming migration

## Archive
- `archive/01_DEV_PublicationHardening_Completed.md`

## Deprecated
- `deprecated/03_DEV_OldUIMerge_Deprecated.md`

## Migration Notes
- Legacy `plan-*.md` files still exist and will be renamed when touched.
```

## 7. ControlCoding-Specific Guidance

### 7.1 Public docs vs internal docs

- `docs/` contains public/adopter-facing documentation
- `dev/` contains internal planning, design, research, legal/business notes, and prior art
- `knowledge/` contains project knowledge that should not be mixed with work-control docs:
  `knowledge/system/` for stable as-built system explanation and
  `knowledge/domain/` for optional external corpus knowledge
- `devlog/` contains the append-only historical record
- in the framework repo, `dev/`, `knowledge/`, `devlog/`, `CLAUDE.md`, `STATUS.md`,
  `ROADMAP.md`, and `BUGS.md` are local maintainer docs rather than published
  repository content

### 7.2 Prior art placement

Exploratory material that is not active truth should live under `dev/prior-art/`.

Examples:

- UI prototype explorations
- superseded ecosystem sketches
- brainstorm dumps retained for traceability

### 7.3 Legal and business

`dev/legal/` and `dev/business/` are first-class categories, not ad hoc dumping
grounds. Create them when the project contains:

- policy interpretation
- release/compliance constraints
- licensing analysis
- commercial framing
- positioning or publication scope decisions

## 8. Adopter-Project Guidance

ControlCoding should encourage, not force, documentation complexity.

### 8.1 Managed vs project-managed

- In `managed` mode, ControlCoding may scaffold and maintain the canonical
  documentation structure.
- In `project_managed` mode, ControlCoding should limit itself to advisory
  guidance unless the user explicitly asks for migration.
- The `check_file_organization.py` hook should enforce naming/layout only for
  repos that remain in `managed` mode.

### 8.2 Minimal starter structure

Use only:

- `dev/plans/`
- `dev/design/`
- `devlog/`

### 8.3 Add categories only when justified

Add:

- `research/` when the project depends on technical or scientific investigation
- `legal/` when compliance or licensing matters
- `business/` when product strategy matters
- `prior-art/` when prototypes or old paths need explicit separation
- `knowledge/system/` when stable system explanations would otherwise get mixed into `dev/design/`
- `knowledge/domain/` only when the project truly depends on a meaningful external corpus

## 9. Migration Policy

When a repo already contains mixed historical material:

1. create the target category with `INDEX.md`
2. add `archive/` and `deprecated/` with their own `INDEX.md`
3. document what is still pending migration
4. move documents category by category, not in one destructive bulk rewrite
5. preserve links or update them in the same change

## 10. Non-Negotiable Rules

1. Never delete governed documents just because they are old.
2. Never mix active and deprecated material in the same index section.
3. Never leave a category folder without a local `INDEX.md`.
4. Never use ambiguous filenames like `notes.md`, `misc.md`, `new-plan.md`.
5. Never collapse historical documents into summaries; move them, rename them, and index them.
