# Project Document and feature planning

The experimental Product workflow saves a whole-app Project Document and carries
selected features through Request, Specification and Implementation plan.
Core commands work without the optional desktop, a model or initialized memory.
The desktop Product workspace provides forms and a readable review of each stage.

Optional [planning discussion](planning-discussion.md) retains questions and
bounded proposals using existing reviewed/direct/manual send policies. Assistance
remains experimental; replying or reviewing a proposal cannot approve scope or
development. The manual forms remain available without a model.

Start with **I already have a plan** to select Markdown/text sections or a legacy
Planner JSON record, or **Let's
build it together** to enter the document manually. Save is a draft. Accepting a
Project Document records desired scope, not delivered behavior. Execution remains
disabled until its separate explicit activation decision. Selecting one feature
creates an increment and its initial unapproved Request together.

Review and accept Request, Specification and Implementation plan separately.
Inherited facts and success criteria are carried forward. Review the criterion
mapping for meaning, not only IDs. An Implementation plan needs actual proposed
file scope and actionable steps/checks; observed existing files bind their exact
bytes, while proposed new files must remain absent. Approval is permission for
that exact plan, not proof that implementation or verification has happened.
Continue using the existing Development stages after plan acceptance.

Additional independent features can be added without changing earlier frozen
scope. A selection records exact feature revisions, linked changes and criterion
ownership. Shared criteria declare every contributor and a final integration
check. Each required criterion has one accountable change; individual verified
contributions need a current combined result before the increment is complete.

## Scope evolution and dependencies

When an accepted document changes selected behavior or shared conditions, affected
open work waits for an explicit impact decision. Keep-original retains its scope
and the considered revision with a reason. Adopt creates a new immutable selection
and updates affected upstream intents and review markers together. Unaffected
changes retain their decisions. A competing edit invalidates a stale preview;
review the fresh comparison before applying it. Stricter policy cannot be bypassed
by keeping an older scope. Defer and cancel remain explicit owner choices.

Retirement and succession preserve original selections, decisions and delivered
results. Closed changes stay closed; changing delivered scope needs a new linked
request. An incident follow-up may link to Product scope while preserving its
original incident and deduplication. Linking neither approves scope nor starts a
repair.

Typed dependencies bind the target change, exact feature and selection revisions,
required result and waiting gate. Required results are accepted specification,
verified or released; gates are Specification, Plan, Build, Verification,
Review/release and Maintain. Missing targets, cycles and older target evidence
block eligibility. Feature dependencies, change dependencies, plan task order
and calendar dates are distinct. Declaring a dependency does not create an
executable schedule, and progress displays cannot substitute for current proof.

Selection and scope decisions use the normal revision-bound preview/apply
boundary. Final integration review reads current verification/invariant receipts
for the combined candidate; a supplied pass flag cannot qualify it. Source or
evidence drift requires a fresh check at the actual consuming gate.

## Progress and task order

`cc product progress --project-root PATH --json` reads the same bounded Core
projection used by the optional desktop Overview and Product roadmap. It works
without initialized memory, a model or a loaded code map. An empty folder gives
an explicit empty result and a Product destination. Reading creates no project
registry, cache, memory database, approval or execution. An incompatible method
requires the ordinary reviewed migration; opening a view never migrates it.

The 0.10 to 0.11 migration preserves genuine detailed planning profiles and
their original creation identity, accepted early-stage decisions, retained
history and immutable companions. Missing or inconsistent historical profiles
cannot gain legacy applicability. Later delivery stages need current 0.11
owner qualification; preserving an old approval does not refresh its proof.

Progress distinguishes the current Project Document from accepted scope and
the exact selected feature revision. Outcomes are Not established, Planned,
In progress, Implemented, Verified and Released. Required and optional condition
counts remain separate; shared conditions count once. Unknown or unmapped
coverage cannot qualify higher delivery results. A saved scope or accepted
request can still establish planning progress while coverage remains unknown.
Current implementation needs applicable accepted build proof; current
verification also needs every required contribution and declared combined proof.
Released additionally needs qualified owner-observed release evidence. Prepared
packets, self-reports, rehearsals, calendar dates, legacy completion labels and
finished conversations do not establish delivery.

The projection keeps outcome, currentness and attention separate. Blockers and
next actions point to their existing Product or Development owner. Historical
releases retain their original scope, artifact and environment beside changed
current work. Working anchors can be partial; the history view reads original
retained events through the existing revision-bound history owner.

Use `--view feature|increment|change|history --entity-id ID` for a focused read.
Overview needs no entity. `--collection` selects one of that view's bounded
collections. A change's default task collection shows deterministic plan order,
owners, deliverables and predecessor relationships. Task completion is
`not_tracked`: a build or verified change does not mark every task done.
Supporting task relationships remain distinct from ordering dependencies.
Code-map observations and optional memory schedule dates keep their own owners.
Editing a legacy date or a coincidentally equal ID cannot change Product proof.

Responses use schema version 1 and return at most 32 whole rows and 512 KiB of
UTF-8 JSON, including projection metadata and cursor. Large collections page;
an indivisible oversized record fails explicitly. Known totals require complete
evaluation and zero conditions never imply full completion. Cursors bind the
project, installed method, scope, control revision and observed external inputs.
Pass both `--cursor` and its `--observation` to continue the same collection.
Changed inputs or expiry require discarding accumulated pages and starting a
fresh read. Cross-view drilldown uses a new observation. Cursor validity is at
most 60 seconds and may end earlier when applicable proof expires.

Each observation has one 45-second deadline, at most 10,000 read entries and
256 MiB aggregate input, including validation, replay, owner reads, rechecks
and at most one drift retry. Existing stricter owner/file limits still apply.
Resolved command executables bind their actual SHA-256 bytes, with the evidence
owner's finite 128 MiB per-binary limit. Their initial reads and rechecks count
against the same aggregate progress budget. A changed executable can invalidate
current proof while project source and control revision remain unchanged.
Receipts also bind the runner and execution context. Changing the installed
distribution, dependencies, or Python isolation can preserve historical evidence
while invalidating its use as current delivery proof.
Repeated drift or exhausted limits yield an explicit unavailable/incomplete
result or bounded error; unavailable evidence is never empty successful proof.
Current means qualified at the stated observation, with its observation time
and any known proof deadline, rather than continuous monitoring or execution
permission. Refresh after relevant scope, source or evidence changes.

## Selected sources

Product reads explicitly selected UTF-8 Markdown/text files, supported legacy
Planner JSON records or pasted text, with a 128 KiB bound per source and 128
total inventoried items, including unselected and unsupported JSON material. JSON
also has a depth limit of 16 and a 4,096-node limit; duplicate keys and nonfinite
numbers are rejected. Pasted text is labeled
user-provided. Unsupported formats and unread URLs are not confirmed evidence.
No repository scan, OCR, indexing or provider call starts by opening Product.

Each selected section must be mapped, reference-only, or excluded with a reason.
Unresolved selected sections block source review. The interface distinguishes
selected coverage from whole-document coverage; reviewing two of twenty sections
does not mean the whole brief was reviewed. Changed source bytes require a fresh
comparison. Original documents remain owned by their project and are never
rewritten by import. Observed implementation is not newly verified capability.

Legacy IDs and statuses remain source observations. Historical approved/completed
labels never create Development decisions. Raw source hashes are independent of
the legacy status hash. Unknown material stays visible for disposition; an
oversized or unsupported source needs an explicitly prepared partial extract or
manual reference, with its limited coverage stated.

## Detailed Specification and Implementation plan

New Product changes use `detailed_contract_v1`. The Specification saves stable
requirement and criterion associations, source-condition mappings, decisions,
assumptions and conflicts. Splitting a condition requires reviewed mappings to
all required resulting criteria. Reordering does not change those associations.
Unresolved mandatory conflicts and weakened required conditions block acceptance.

Each plan task has an approach, deliverable, scoped files and owners, criterion
references and proof obligations. Supporting work needs a delivery relationship
and rationale; file-free work needs a reason. Automated checks must name actual
inspected verification/invariant contracts. Human checks ask a concrete review
question. Internal task predecessors remain separate from cross-change gates.

Well-formed incomplete detail can be saved and resumed. Saving a draft grants no
stage acceptance. One stage decision binds both the base artifact and immutable
detail, source observations and selected scope. Changing detail with unchanged
base artifact bytes requires fresh review and execution qualification. Approved
detail reaches the hashed manual packet and actual supported worker request;
base-plan-only adapter qualification cannot authorize a detailed request.

Authentic retained 0.9 changes keep their historical applicability. Enabling
detail uses an explicit `product_planning_upgrade` action for one exact change
and selection; affected Specification/plan qualifications need fresh review.
Active or unreconciled effects block the upgrade. Other changes retain their
own applicability. Each detailed companion is limited to 128 KiB canonical
encoded data, with existing stricter schema and storage limits still applying.
Coverage reports deterministic references and obligations; they do not prove
arbitrary semantic equivalence or replace the independent agent evaluation gate.

## Readable exports and external edits

For managed documentation, the suggested product file is
`dev/product/product.md`, or `cc/dev/product/product.md` in contained layout.
For project-managed documentation, explicitly choose a destination, for example
`docs/controlcoding/product.md`; contained runtime storage does not relocate it.
Existing files need review of their exact current hash and explicit replacement.
Paths are confined to the selected project and links/shared files are rejected.

Request, Specification and Implementation plan have their own readable exports:
`dev/changes/<change-id>/intent.md`, `spec.md`, and `plan.md`, prefixed by `cc/`
in contained managed layout. Project-managed paths are explicitly selected.
Stage status is derived from the real current Core gate; an earlier acceptance
with current blockers is labeled `accepted_blocked`, not current approval.
Stage files are projections; external edits do not import Development decisions.

Planning is bounded. A new planning control-state write may expand to at most
32 MiB and 16,384 traversal visits, counting repeated immutable references and
wrapper parts. Crossing that bound reports `planning_replay_limit` and does not
commit the draft. Existing valid graphs retain the reader's 128 MiB/32,768 bounds
and remain readable/migratable; the new writer limit does not relabel them corrupt.
Separate planning sessions retain their existing 64-turn/256 KiB-per-session
bounds and the shared 64 MiB planning storage quota. Physical object scans and
manifest limits can block growth earlier. Shared bytes do not imply unlimited
logical replay capacity, and no automatic garbage collection is promised.

Exports include stable versioned markers and the canonical base revision.
Editing an export produces a draft import proposal, never an approval. Roundtrip
compares the actual file against its retained Core base and current document.
Renaming/reordering retains IDs. Added free text is retained as notes; missing
entities and competing edits require explicit keep/adopt decisions. Duplicate,
unknown or malformed markers need file repair before import. Arbitrary Markdown
is a new source rather than a lossless roundtrip format.

An import proposal binds its exact file hash, comparison base and proposed
document digest. Save rechecks those under the Core lock. Changed file bytes,
a newer canonical revision or a silently edited proposal require a new comparison
or an explicitly separate manual draft. Refreshing the interface never rebases
unsaved edits onto newer work automatically.

Document save and file export are separate effects. If export fails, the saved
document remains canonical and the interface reports that export needs attention.
Retry the reviewed export, or preview a new export after a competing edit. Retry
cannot overwrite new owner bytes. A file name or export receipt never satisfies
a Development approval gate.

## Core CLI

```bash
python scripts/cc.py product status --project-root /path/to/project
python scripts/cc.py product progress --project-root /path/to/project --json
python scripts/cc.py product progress --project-root /path/to/project --view feature --entity-id Accounts --collection criteria --json
python scripts/cc.py product progress --project-root /path/to/project --view change --entity-id Accounts --collection tasks --json
python scripts/cc.py product progress --project-root /path/to/project --view history --entity-id Accounts --json
python scripts/cc.py product source --project-root /path/to/project --path docs/brief.md --json
python scripts/cc.py product files --project-root /path/to/project --path src/accounts.py --json
python scripts/cc.py product template --project-root /path/to/project --change-id Accounts --stage design --json
python scripts/cc.py product adoption-preview --project-root /path/to/project --source devlog/plans/plan.current.json --json
python scripts/cc.py product planning-preview --project-root /path/to/project --change-id Accounts --stage plan --json
python scripts/cc.py product export-preview --project-root /path/to/project --ownership project_managed --path docs/controlcoding/product.md --json
python scripts/cc.py product roundtrip --project-root /path/to/project --path docs/controlcoding/product.md --json
python scripts/cc.py product change-export-preview --project-root /path/to/project --change-id Accounts --stage plan --json
python scripts/cc.py product stage-export-preview --project-root /path/to/project --change-id Accounts --stage plan --mode project_managed --path docs/controlcoding/plan.md --json
python scripts/cc.py product stage-import-preview --project-root /path/to/project --change-id Accounts --stage plan --path docs/controlcoding/plan.md --json
```

Typed Product mutations use `product preview --action-file ... --revision ...`
and `product apply --action-file ... --revision ... --approval-id ...`. They use
the same Core service and revision checks as the desktop. Export publication uses
`product export --expected-hash ... --preview-id ...`, with the same reviewed
destination/ownership and `--replace-reviewed` only for an existing file.

Detailed draft actions are `product_adoption_save`, `product_specification_v2`
and `product_plan_v2`; they use that same typed preview/apply boundary. CLI
authoring uses action files rather than a new interactive editor. Detailed stage
export commit uses `product stage-export --preview-file ...` with the exact saved
JSON preview and the same change, stage, mode and destination. Import previews
produce a reviewed v2 draft proposal. Missing/stale markers, unknown prose and
readable content that disagrees with its bound payload need explicit resolution;
they cannot silently recover approval from the marker.

This candidate does not establish live assistance quality or real adopter
suitability. The two-participant formative human gate is separate from automated
desktop checks and remains pending until actual sessions are recorded.
