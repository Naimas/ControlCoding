# Project Memory Engine

The Project Memory Engine is an optional local work-memory module for
ControlCoding. It helps an AI-assisted development session remember project
context across time without mixing that context with the application's own
runtime memory.

Minimal `init` does not initialize memory. Guided `setup` uses the reviewed
handoff's memory policy: `governed_scope` initializes local memory and scans only
governed surfaces; `deferred` leaves memory uninitialized. See the
[complete installation example](install-controlcoding-on-your-project.md#complete-fresh-project-example).

## The Short Version

ControlCoding has a development memory. Your application may also have its own
memory. They are separate.

| Area | Owner | Storage | Typical ID | Purpose |
|---|---|---|---|---|
| ControlCoding development memory | ControlCoding workflow | `.controlcoding/` | `CC_<ProjectShort>_DEV_...` | Decisions, notes, plans, consult summaries, agent-run summaries, document references, handoff views |
| Application-owned memory | The application being built | Defined by the application | `<ProjectShort>_MEM_...` or project-specific IDs | Runtime/domain memory, user data, embeddings, RAG corpus, application vector stores |

ControlCoding may index or point to application-owned memory documents as
project context, but it does not become their source of truth.

## What "Memory" Means Here

In this document, memory means local development memory for the project workflow.
It stores information that helps the human and the AI continue work:

- why a decision was made
- which plan replaced an older plan
- which document is stale, active, legacy, superseded, or conflicting
- what an external review or consultation concluded
- what an agent run attempted and verified
- which documents are related to a file or topic

It is not application state. It should not store user data unless the project
owner intentionally puts such data in project documents and accepts that risk.

## RAG And Search

ControlCoding memory includes a local document graph and a derived local sparse
search index. It can be used as a retrieval layer over ControlCoding development
memory and project documents.

That is not the same as application RAG.

| Question | Answer |
|---|---|
| Does ControlCoding memory provide retrieval over development context? | Yes. It indexes local project documents, notes, decisions, chunks, and generated views. |
| Does it use remote embeddings? | No. The included vector index is local, sparse, stdlib-only, and rebuildable. |
| Is it the source of truth for application-owned RAG data? | No. Application RAG belongs to the application. |
| Can it reference application-owned memory files? | Yes, as project context. The application still owns those files. |
| Can an adopted project build its own embeddings/vector DB separately? | Yes. That is application-owned memory, not ControlCoding development memory. |

The rule is simple: ControlCoding can remember the work around the project. The
application owns the memory it needs at runtime.

## What Gets Installed

Initialize memory inside an adopted project with:

```bash
python /path/to/ControlCoding/scripts/cc.py memory init --project-root .
```

This creates local runtime state under `.controlcoding/`, including:

- `.controlcoding/memory/memory.db`
- `.controlcoding/logs/`
- `.controlcoding/views/`
- `.controlcoding/module_manifest.json`

The database is SQLite. Core requires Python 3.11+; memory also needs a working
SQLite deserialize API. Initialization probes that operation using a private
in-memory image before creating project state. The implementation uses Python
stdlib only. Existing read-side snapshot, lock and sidecar checks still apply.

Guided setup defaults to `governed_scope` if the handoff omits the memory policy.
Choose `deferred` explicitly if the project only needs context files and hooks.
A deferred setup can write a policy receipt without creating a memory database.

## Install Modes

Use `full` mode when the project already uses the broader ControlCoding workflow:

```bash
python scripts/cc.py memory init --project-root . --mode full --project-short MyProj
```

Use `document-only` mode when you only want the document and work-memory module:

```bash
python scripts/cc.py memory init --project-root . --mode document-only --project-short MyProj
```

For a non-code Work Memory repository, use the work profile:

```bash
python scripts/cc.py memory init --project-root . --mode document-only --profile work --project-short WorkProj
```

`document-only` creates the memory database, logs, and views. It does not
install hooks, MCP servers, agents, source folders, or full ControlCoding
project structure. `--profile work` also creates the non-code document layout
described below.

## Work Memory Profile

The same document-only mode can also support a non-code Work Memory profile: a
repository for complex work that involves research, documents, chat summaries,
PDF extracts, tables, diagrams, decisions, workflows, and deliverables.

A Work Memory repository uses the ControlCoding memory engine without requiring
software-development gates. It keeps compatibility with full ControlCoding by
using the same local memory layout, lifecycle states, document graph, sparse
search index, generated views, and evidence-first habit.

This is not the same thing as standalone ControlWork. The Work Memory profile
is a ControlCoding memory mode under `.controlcoding/`. Standalone ControlWork
is the portable Project Plane contract under `.controlwork/` and
`CONTROLWORK.md`.

Use this distinction:

| Layer | Storage | Role |
|---|---|---|
| ControlCoding Work Memory profile | `.controlcoding/` | Document-oriented mode of the ControlCoding Project Memory Engine. Useful as a laboratory for advanced memory features. |
| Embedded ControlWork Project Plane | `.controlwork/` and `CONTROLWORK.md` inside a ControlCoding project | Portable work/project memory attached to a coding project. |
| Standalone ControlWork | Separate folder or repository with `.controlwork/` and `CONTROLWORK.md` | Independent work memory for research, documents, planning, decisions, outputs, and AI chat continuity. |

When a Work Memory improvement is portable and belongs to the Project Plane, it
should be classified and ported into ControlWork. When the improvement depends
on code, hooks, agent runs, verification receipts, or other ControlCoding-only
runtime behavior, it stays in ControlCoding.

Suggested repository layout:

```text
docs/
  inbox/        unprocessed notes, exports, and rough captures
  sources/      original source files and source references
  extracts/     extracted text from PDFs, images, tables, or chat logs
  research/     analysis notes and source syntheses
  ideas/        raw and developed ideas
  decisions/    canonical choices with rationale
  workflows/    procedures, operating models, checklists
  outputs/      reports, texts, deliverables, and final drafts
  archive/      legacy or superseded material kept for history
```

The core rule is that raw chats, PDFs, images, and tables are inputs, not
canonical truth. They become useful memory only after the project records an
extract, summary, note, decision, lifecycle state, or conflict marker.

Use `cc memory intake add` for summarized non-code inputs:

```bash
python scripts/cc.py memory intake add --project-root . "Market research notes" --source-type research --summary "Short evidence-backed summary." --source-ref docs/sources/report.pdf
```

Promote inbox material once it becomes useful working memory:

```bash
python scripts/cc.py memory intake promote --project-root . docs/inbox/market-research.md --to research --reason "Source summary reviewed"
```

The promotion command moves the file into the selected Work Memory folder,
updates the canonical memory record, writes an audit event, and regenerates the
Work Memory views.

A Work Memory repository can remain independent forever, or later become the
memory plane for a full ControlCoding project. The upgrade path should preserve
`.controlcoding/memory/` and add the development layer around it rather than
migrating to a second schema.

## Detachable By Design

ControlCoding has two compatible local memory layers.

- `.controlcoding/` is the Development Memory Engine. It tracks coding workflow,
  implementation decisions, consults, agent runs, document graph state, and
  impact/context views.
- `.controlwork/` plus `CONTROLWORK.md` is the Project Plane. It stores
  product or work knowledge that can exist before code, outside code, or inside
  a coding project.

Both are detachable. Removing `.controlcoding/` removes ControlCoding
development memory state. Removing `.controlwork/` and `CONTROLWORK.md` removes
the embedded project-plane memory. Neither operation should delete application
source files.

## ControlWork Project Plane

Use ControlWork when the durable knowledge is research, documents, requirements,
analysis, planning, source summaries, decisions, or handoff material that should
remain useful even if no code exists yet.

ControlWork is the portable work-memory module that ControlCoding can embed as
its Project Plane. It is not only an internal ControlCoding implementation
detail: it can also live as a standalone repository. In the embedded form,
ControlWork holds the project/work knowledge while ControlCoding adds Dev Plane
and Code Plane behavior around it.

ControlWork originated as the Project Plane inside ControlCoding and was spun
out as a standalone, source-available product. The embedded and standalone
forms remain contract-compatible, but they are not automatically the same
product identity. ControlCoding and ControlWork both use PolyForm Shield.
Permitted internal and noncompeting commercial use is allowed, while providing
a competing product or service requires separate written permission.

Shared Project Plane improvements should flow both ways deliberately:

- Prototype advanced memory behavior in ControlCoding when it needs the richer
  `.controlcoding/` store or development workflow evidence.
- Classify the behavior as portable or ControlCoding-only.
- Port portable behavior into ControlWork when it does not require
  ControlCoding, codebase state, hooks, agent runs, or a specific AI host.
- Keep standalone ControlWork and embedded ControlWork contract-compatible.
- Rebuild derived graph, search, and view state after import, attach, sync, or
  export instead of treating projections as canonical truth.

Initialize embedded ControlWork inside a ControlCoding project:

```bash
python scripts/cc.py memory work-quickstart --project-root . --dry-run
python scripts/cc.py memory work-quickstart --project-root .
python scripts/cc.py memory work-init --project-root . --name "Project Name"
python scripts/cc.py memory work-status --project-root .
```

Connect standalone ControlWork and ControlCoding explicitly:

```bash
# Import a standalone ControlWork folder into the coding project
python scripts/cc.py memory work-import --project-root . ../MyWorkProject

# Attach an external ControlWork folder for explicit manual sync
python scripts/cc.py memory work-attach --project-root . ../MyWorkProject
python scripts/cc.py memory work-sync --project-root . --direction pull --force
python scripts/cc.py memory work-sync --project-root . --direction push --force

# Export embedded project-plane memory to a separate work-only folder
python scripts/cc.py memory work-export --project-root . ../MyWorkOnlyFolder
```

The embedded Project Plane supports the shared ControlWork feature contract:

```bash
python scripts/cc.py memory work-category list --project-root .
python scripts/cc.py memory work-scan --project-root .
python scripts/cc.py memory work-analyze --project-root .
python scripts/cc.py memory work-review docs/brief.md --project-root . --review-status ready_to_promote --sensitivity internal
python scripts/cc.py memory work-promote docs/brief.md --project-root . --area sources --title "Reviewed brief" --summary "Reviewed source summary."
python scripts/cc.py memory work-import-source docs/brief.docx --project-root . --area sources --summary "Imported DOCX for review."
python scripts/cc.py memory work-ocr status --project-root .
python scripts/cc.py memory work-category propose normative --project-root . --area sources
python scripts/cc.py memory work-category approve normative --project-root .
python scripts/cc.py memory work-query "project topic" --project-root .
python scripts/cc.py memory work-graph explain CONTROLWORK.md --project-root .
python scripts/cc.py memory work-graph neighbors CONTROLWORK.md --project-root .
python scripts/cc.py memory work-graph stale --project-root .
python scripts/cc.py memory work-graph unresolved --project-root .
python scripts/cc.py memory work-graph diff --project-root . --baseline .controlwork/graph-baseline.json
python scripts/cc.py memory work-retrieve "project topic" --project-root .
python scripts/cc.py memory work-rag-pack "project topic" --project-root .
python scripts/cc.py memory work-views --project-root .
python scripts/cc.py memory work-checkpoint --project-root . --title "Session checkpoint"
python scripts/cc.py memory work-handoff --project-root .
python scripts/cc.py memory work-dashboard --project-root .
python scripts/cc.py memory work-context-pack --project-root . --scope planning --topic "SGC architecture"
python scripts/cc.py memory work-obsidian init --project-root .
python scripts/cc.py memory work-mcp tools
```

Categories are minimal by default. Project-specific categories such as
`normative` are optional and should be added only when the project needs them.

Use `work-context-pack` when a chat needs the Project Plane for research,
requirements, sources, plans, or handoff. The packet separates active context,
needs-review material, and legacy/superseded warnings.

`work-scan` writes a governed file inventory under `.controlwork/ingestion/`.
It does not import files into memory, run OCR, or promote graph truth.
`work-review` records review state and can mark `ready_to_promote` only when
duplicate, conflict, version, source-change, and OCR sidecar blockers are clear.
`work-promote` writes reviewed evidence into `.controlwork/memory/`; any forced
override over blockers or duplicate promotion checks requires `--force-note`.
`work-import-source` and `work-ocr import-sidecar` create `needs_review`
evidence with provenance rather than active truth. `work-graph`,
`work-query`, `work-retrieve`, and `work-rag-pack` inspect the portable Project
Plane graph and packets as derived views over reviewed memory. `work-query` is
the query-first surface: it combines retrieval, a GraphRAG packet, and next
`work-graph explain/path` commands without external calls.
Use `work-graph neighbors` to inspect directly connected records, `stale` to
find legacy/superseded material and stale scan attention, `unresolved` to find
open questions and pending review, and `diff` to compare the current derived
graph with a previously exported baseline.
`work-dashboard` writes a static HTML or Markdown Project Plane dashboard under
`.controlwork/dashboard/` by default. It summarizes active decisions, open
questions, needs-review sources, stale entries, recent checkpoints, graph
health, and handoff readiness as a local projection.

The optional read-only MCP server for embedded ControlWork lives at
`scripts/controlwork_mcp.py` and requires `fastmcp`. The CLI commands above do
not require `fastmcp`.

Synchronization is manual and explicit. Pull and push require `--force` because
they overwrite the destination ControlWork artifacts.

### Current Sync Model

ControlCoding does not currently maintain a constant background connection to
ControlWork, and it does not automatically rewrite ControlWork when plans,
decisions, or documents change.

Current behavior is command-driven:

- `cc memory work-status` reports the embedded Project Plane status.
- `cc memory work-quickstart --dry-run` previews the safe first-pass setup
  without writing files.
- `cc memory work-quickstart` initializes the embedded Project Plane, scans
  project files, analyzes scan evidence, refreshes views, retrieves topic
  context, and writes one scoped context packet without AI calls, network calls,
  OCR, or hidden subprocesses.
- `cc memory work-query` is the normal query-first surface after quickstart. It
  returns top matches, a GraphRAG packet, graph explain targets, optional graph
  path probing, and the next commands to inspect evidence.
- `cc memory work-attach` records an external standalone ControlWork project for
  manual synchronization.
- `cc memory work-sync --direction pull|push --force` copies between embedded
  and external ControlWork only when explicitly requested.
- `cc memory work-context-pack` builds a scoped packet from current Project
  Plane files.
- `cc memory work-views` regenerates embedded ControlWork views.
- `cc memory work-dashboard` generates a static local dashboard projection for
  active decisions, open questions, needs-review sources, stale entries, recent
  checkpoints, graph health, and handoff readiness.
- `cc memory work-obsidian check` checks projection drift for the generated
  wiki projection, not full Project Plane truth.
- `cc memory scan`, `vector rebuild`, and `views generate` refresh the
  ControlCoding Project Memory Engine, not standalone ControlWork.

`cc memory work-status` also reports drift awareness:

- embedded versus linked standalone ControlWork fingerprint differences as a raw
  audit signal
- semantic actionable drift for shared config, context, category registry, and
  Project Plane memory content
- stale or missing embedded ControlWork views
- missing Project Plane context packets
- Obsidian projection drift when a wiki projection exists
- explicit update requests with maintenance commands

`cc memory doctor` includes a `project_plane` readiness check when Project
Memory Engine is initialized. It reports whether the embedded Project Plane
exists, whether an external standalone ControlWork project is attached, whether
Project Plane views are stale, and whether at least one context packet is
available for handoff or chat restart.

The drift report never pulls, pushes, syncs, regenerates, or writes by itself.
Expected distribution differences between embedded ControlCoding Project Plane
text and standalone ControlWork product text remain visible in the raw
fingerprint, but they are not treated as actionable sync drift when the shared
contract, categories, and memory content match. Category audit timestamps are
ignored during semantic comparison.

The intended future model is not silent automatic synchronization. ControlCoding
should detect drift, stale derived graph state, and changed plans, then surface
explicit update requests or maintenance commands. A human should decide whether
to update the embedded Project Plane, pull from standalone ControlWork, push to
standalone ControlWork, or intentionally let the two diverge.

## Practical Workflows

### Naming

Use this public terminology consistently:

| Name | Command or scope | Meaning |
|---|---|---|
| Chat Start | AI-facing `cc chat-start` | One-command, read-only startup packet that the host AI runs at the start of work. It combines work-start checks, Dev memory retrieval, Project Plane context, warnings, and closeout guidance. |
| RAG-O | `cc memory op-index` | RAG Operations Index. A read-only coordination layer for status, routes, packets, drift, and action queue. It is not a retrieval or generation engine. |
| Memory Bootstrap | `cc memory bootstrap` | Fast read-only startup status for memory planes and stale artifacts. |
| Memory Startup Protocol | `cc memory startup` | Visible read-only startup checklist for new AI chats. It asks whether work should continue or start fresh and prints explicit next commands. |
| Session GraphRAG | `cc memory session`, `cc memory session-pack`, `cc memory retrieve` | Explicit session traceability for chats and work sessions. Storage, CLI, views, RAG-O status, Dev GraphRAG retrieval, and packets are implemented in ControlCoding. |
| Dev GraphRAG | `cc memory retrieve` and `cc memory rag-pack` | ControlCoding Dev Plane retrieval and packet building over `.controlcoding/memory/`. |
| Evidence refs | `cc memory evidence` | Read-only drill-down from packet `nodeId` or `evidenceRefId` back to local indexed source evidence. Refs are derived from the memory index and do not create a new source of truth. |
| MemoryEval | `cc memory eval` | Small fixed synthetic fixture that checks selected retrieval, demotion, evidence-ref, privacy-scrub, cross-plane, and RAG-O behaviors. It is not a project-corpus audit or privacy certification. |
| Work GraphRAG | `cc memory work-context-pack`, standalone `cw.py retrieve`, and standalone `cw.py rag-pack` | Project Plane retrieval and packet building over ControlWork memory. |
| Cross-Plane GraphRAG Packet | `cc memory cross-pack` | Federated read-only packet that combines Dev GraphRAG, Work GraphRAG, Session GraphRAG, and RAG-O routes without merging sources of truth. |
| ControlWork | `CONTROLWORK.md` and `.controlwork/` | Portable Project Plane memory contract, embedded in ControlCoding or standalone. |
| App Memory Boundary | application-owned adapters or approved project documents | Runtime or domain memory owned by the application, not ControlCoding. |

RAG-O coordinates these surfaces. It does not replace Dev GraphRAG, Work
GraphRAG, Memory Bootstrap, ControlWork, or application-owned memory.

### Bootstrap Status

Use Chat Start as the default memory gateway at the start of AI work. The user
should only need a conversational instruction such as "Start this project and
load its memory before working." The host context tells the AI to run
`chat-start` itself when tools are available and then load the packet before it
answers or edits.

Use `bootstrap` or `startup` when you need the lower-level memory status
without the combined chat packet. These commands inspect memory planes without
creating files, rebuilding indexes, or synchronizing ControlWork:

```bash
python scripts/cc.py memory bootstrap --project-root . --scope dev --topic "GraphRAG"
python scripts/cc.py memory startup --project-root . --scope dev --topic "GraphRAG"
```

The command reports Dev Plane memory, embedded or linked Project Plane memory,
application-owned memory references, stale views, stale vectors, graph packet
status, hot documents, active focus, and recommended maintenance commands. It
exits successfully when memory is missing so a new chat can still learn what to
do next.

`startup` wraps the same read-only status into a host-friendly protocol. It
does not create a session automatically. It prints the question to ask, the
visible commands to run, and the action queue. Use `--intent
continue_previous_work` only after the user or current task indicates that the
session should continue prior work.

Use RAG-O when a session needs the broader operational control view:

```bash
python scripts/cc.py memory op-index --project-root . --scope dev --topic "GraphRAG"
```

RAG-O is read-only and derived. It combines Memory Bootstrap status, graph
status, Project Plane drift, feature WIP state, RAG routes, packet pointers,
action queue, and commit hygiene. It does not retrieve answers, generate
answers, initialize memory, regenerate views, create packets, sync ControlWork,
start or complete features, accept suggestions, or stage files.

### Session GraphRAG Base

Use `session` commands when a chat or work session should be recorded
explicitly and auditable:

```bash
python scripts/cc.py memory session start --project-root . --topic "GraphRAG implementation" --mode continue_previous_work
python scripts/cc.py memory session link --project-root . <session-id> --commit <sha>
python scripts/cc.py memory session note --project-root . <session-id> "Follow up on RAG-O integration." --kind followup
python scripts/cc.py memory session close --project-root . <session-id> --status needs_followup --summary "Implemented the session CLI base."
python scripts/cc.py memory session list --project-root . --topic "GraphRAG"
python scripts/cc.py memory session show --project-root . <session-id>
python scripts/cc.py memory session views --project-root .
python scripts/cc.py memory session-pack --project-root . --topic "GraphRAG" --status all
```

The current implementation stores records in additive SQLite tables
`session_records` and `session_edges`, and writes audit events for explicit
start, close, link, note, and view-generation actions. RAG-O reads session
status without writing, and Dev GraphRAG retrieval includes session records as
lower-trust traceability evidence. Session commands do not save raw chat
transcripts or mutate ControlWork.

### Scan And Context

```bash
python scripts/cc.py memory scan --project-root .
python scripts/cc.py memory context --project-root . "billing"
python scripts/cc.py memory impact --project-root . src/billing/service.py
python scripts/cc.py memory dev-context-pack --project-root . --scope backend --topic "billing import"
```

Use `context` before a work session to recover relevant notes, decisions,
consults, and path-backed documents. Use `impact` before edits to see nearby
records and stale items.

Use `retrieve` when you need an explainable hybrid ranking across text,
rebuildable sparse vectors, optional explicit semantic adapter scoring, graph
proximity, lifecycle, confidence, recency, and source trust:

```bash
python scripts/cc.py memory retrieve --project-root . "payment reconciliation evidence" --limit 10
```

The output includes matched citations plus an exclusion and demotion report. It
does not make application-owned memory authoritative; application memory
references are demoted unless the retrieval scope is explicitly application.

Build a Dev GraphRAG packet when you need a portable prompt/context artifact with
citations, graph edges used, stale or conflict warnings, excluded records, and
next reads:

```bash
python scripts/cc.py memory rag-pack --project-root . "payment reconciliation evidence" --limit 10 --output .controlcoding/context-packets/payment-rag.md
```

Without `--output`, the packet is printed as Markdown. With `--json`, the
command returns the Markdown packet plus structured citations, edges, warnings,
excluded records, evidence refs, and next-read recommendations.

Each packet citation has a packet-local `citationId` such as `C1` and a stable
derived `nodeId` or `evidenceRefId` such as `ccref:dev:chunk:<hash>`. Use the
packet-local `citationId` only inside that packet. Use the evidence ref when a
later step needs to drill down to the indexed local source:

```bash
python scripts/cc.py memory evidence show --project-root . ccref:dev:chunk:<hash>
python scripts/cc.py memory evidence list --project-root . --limit 20
```

Evidence refs are read-only and derived from `.controlcoding/memory/`. They do
not capture raw chat transcripts, replay sessions, sync ControlWork, or promote
Project Plane material. Successful `rag-pack`, `evidence show`, and `cross-pack`
payloads pass through a pattern-based local privacy scrub. Packet Markdown is
assembled before that scrub, and an explicitly written packet uses the scrubbed
Markdown. The current patterns cover private-key blocks, authorization headers,
environment and inline secret assignments, OpenAI-shaped keys, GitHub tokens,
Slack tokens, AWS access-key IDs, and JWT-shaped values.

The scrub recursively examines string values in lists and dictionaries. It does
not inspect dictionary keys or infer sensitivity from a key such as `password`,
and non-string values pass through unchanged. Each matched occurrence is counted,
including repeated text in structured fields and packet Markdown. Pattern-shaped
benign text can therefore be replaced, while sensitive values with an unmatched
shape can remain in the returned payload.

The `cc-privacy-scrub/v1` receipt is metadata: it reports whether the helper ran,
replacement and category counts, and the constant field
`secretValuesIncluded: false`. A zero replacement count, or that constant field,
does not establish that the returned payload is free of sensitive content. The
receipt does not inspect all possible secrets, record consent, or certify
encryption, deletion, storage cleanliness, or export safety.

This scrub is an output-processing step after retrieval. An enabled local
semantic adapter can receive the query and bounded candidate fields before the
packet is assembled and scrubbed. Scrubbing a returned packet does not rewrite
the source document or memory index. `evidence list` follows a separate,
unscreened metadata path, while `outputPath` and the text `Written:` line are
added after packet scrubbing. Treat paths and every CLI surface not named above
as outside this measured output boundary.

Run the local MemoryEval fixture when you need a quick regression signal for the
Phase 1 memory behavior:

```bash
python scripts/cc.py memory eval --project-root .
python scripts/cc.py memory eval --project-root . --json
```

`memory eval` creates an isolated fixture under `.controlcoding/tmp`, initializes
the memory planes inside that fixture, checks retrieval ranking, stale and
application-owned demotions, Session GraphRAG demotion, GraphRAG evidence refs,
one fixed synthetic secret shape, cross-plane packet boundaries, and the RAG-O
evidence route, then removes the fixture by default. Use `--keep-fixture` only
when you need to inspect the generated fixture. It does not inspect the active
project corpus for secrets or test universal detection, consent, storage, or
export handling. The command is a small regression check, not a benchmark claim
or application RAG quality score.

Use `cross-pack` when the chat needs a single federated packet across Dev
GraphRAG, Project Plane ControlWork context, Session GraphRAG continuity, and
RAG-O routes:

```bash
python scripts/cc.py memory cross-pack --project-root . "payment reconciliation evidence" --limit 10
```

`cross-pack` is read-only unless `--output` is provided. It does not sync
ControlWork, refresh views, accept graph suggestions, create sessions, or turn
application-owned memory into ControlCoding truth. It keeps each citation
tagged with its originating plane.

Use `dev-context-pack` at the start of a development task. It builds a Markdown
packet from the development memory graph, impacted files, docs, decisions,
consults, agent runs, tests, and lifecycle warnings.

### Notes

```bash
python scripts/cc.py memory note add --project-root . "Cache invalidation risk" --area Backend --body "The read model may lag after bulk import."
```

Use notes for observations that are useful but not decisions.

### Ideas

```bash
python scripts/cc.py memory idea add --project-root . "Wiki graph view" --area Memory --body "Later visual layer, not part of V1."
```

Use ideas as an inbox. Promote them later into plans or decisions when they
become actionable.

### Research Intake

For research documents, place the document in a project-owned location, run
`scan`, then record the intake summary as a note or decision:

```bash
python scripts/cc.py memory scan --project-root .
python scripts/cc.py memory note add --project-root . "Research intake: chunking" --area Memory --path docs/research/chunking.md --body "Use semantic sections, not blind token windows."
```

The engine indexes file metadata, deterministic document classification,
semantic chunk metadata, explicit references, and correlation suggestions. The
SQLite store remains the canonical ledger. Generated indexes and Markdown views
can be rebuilt with `scan` and `views generate`.

### Document Graph And Local Search

The current memory engine adds a local document graph layer on top of the
original ledger:

- deterministic document classification from path, filename, headings, and
  lifecycle
- semantic chunks based on headings and coherent text blocks
- deterministic chunk metadata for structural level, block type, semantic role,
  topics, domains, relation strength, and context policy
- first-class document-layout nodes for document, title, chapter, section,
  subsection, paragraph, table, table cell, schema, schema field, page,
  annotation, figure, image, caption, footnote, and margin note
- optional explicit PDF/OCR layout sidecar ingestion from `.layout.json`,
  `.ocr.json`, `.pdf.layout.json`, or `.pdf.ocr.json`, preserving page
  coordinates, bounding boxes, source document paths, and OCR confidence
- basic stdlib-only text extraction from simple unencrypted PDFs with readable
  content streams
- continuation links when one semantic section is too large
- suggested cross-document correlations from explicit links, shared headings,
  and shared keywords
- impact/context output with ranked reasons
- a rebuildable local sparse vector index derived from semantic chunks

Inspect chunks for a document with:

```bash
python scripts/cc.py memory chunks --project-root . docs/plan.md
```

Inspect first-class layout nodes for a document with:

```bash
python scripts/cc.py memory layout --project-root . docs/plan.md
```

For PDF or OCR sources, place an explicit extraction sidecar next to the
project document and run `scan`:

```bash
python scripts/cc.py memory scan --project-root .
python scripts/cc.py memory layout --project-root . docs/report.pdf.layout.json
```

The sidecar must be project-owned JSON with a `pages` list and page `blocks`.
ControlCoding preserves supplied coordinates and metadata. The sidecar path
does not execute OCR or derive native PDF coordinates by itself.

Minimal sidecar shape:

```json
{
  "schemaVersion": 1,
  "title": "Report title",
  "sourcePath": "docs/report.pdf",
  "sourceType": "pdf",
  "extractionMethod": "pdf_native_text",
  "coordinateSystem": "pdf_points",
  "pages": [
    {
      "page": 1,
      "width": 612,
      "height": 792,
      "blocks": [
        {
          "id": "fig1",
          "type": "figure",
          "text": "Revenue trend chart.",
          "bbox": [100, 120, 300, 260]
        },
        {
          "id": "cap1",
          "type": "caption",
          "text": "Figure 1: Revenue trend chart.",
          "targetBlockId": "fig1",
          "bbox": [100, 270, 310, 294]
        }
      ]
    }
  ]
}
```

Recognized block types include `paragraph`, `table`, `schema`, `annotation`,
`figure`, `image`, `caption`, `footnote`, and `margin_note`. Markdown tables
also emit `table_cell` child nodes, and JSON or simple key/value schema blocks
emit `schema_field` child nodes. A recognized sidecar without a valid `pages`
list is indexed as a path-backed entity but does not create semantic chunks or
layout nodes.

Sidecars may provide a unified page `blocks` list or page-level collections
such as `tables`, `figures`, `images`, `captions`, `footnotes`, `annotations`,
and `marginNotes`. Table blocks may include structured `cells`, `tableCells`,
`headers`, or `rows`; those entries become `table_cell` child layout nodes with
row, column, header, and coordinate metadata when supplied. Schema blocks may
include `fields` or `schemaFields`, which become `schema_field` child layout
nodes.

When sidecar blocks include bounding boxes, ControlCoding also emits bounded
page-local visual relation edges for nearest above, below, left, right, and
overlapping blocks. These edges are derived evidence for retrieval, not a
replacement for source documents.

ControlCoding can also scan simple unencrypted text PDFs directly. This is a
best-effort stdlib parser for readable PDF content streams. It extracts text
into page-marked chunks, but it does not provide native PDF bounding boxes or
OCR.

Run an explicit local OCR/layout adapter when project configuration provides a
runtime command:

```bash
python scripts/cc.py memory ocr status --project-root .
python scripts/cc.py memory ocr run --project-root . docs/report.pdf --output docs/report.pdf.ocr.json
python scripts/cc.py memory ocr run --project-root . docs/report.pdf --force
python scripts/cc.py memory scan --project-root .
```

`ocr status` does not execute adapters. It reports command executability,
accepted source types, timeout, and configuration issues. `ocr run` sends a
bounded JSON request to the configured local command with source type, source
size, SHA-256 digest, output path, and sidecar schema metadata. It validates
the sidecar JSON returned on stdout, rejects mismatched `sourcePath` values,
writes only the requested sidecar inside the project root, does not overwrite
an existing sidecar unless `--force` is passed, and requires a later explicit
`scan`.

`cc memory scan` refreshes chunk records and correlation suggestions. Weak
keyword correlations remain suggestions, not canonical graph edges.

Inspect graph inventory and govern suggestions with:

```bash
python scripts/cc.py memory graph status --project-root .
python scripts/cc.py memory graph suggestions --project-root .
python scripts/cc.py memory graph accept --project-root . <suggestion-id> --reason "Reviewed relation"
python scripts/cc.py memory graph reject --project-root . <suggestion-id> --reason "False relation"
python scripts/cc.py memory graph around --project-root . docs/plan.md --depth 2
```

`graph accept` promotes a suggestion to a canonical edge and keeps the reviewed
suggestion row for audit. `graph reject` keeps the rejected suggestion row so a
future scan does not recreate it as a new unreviewed suggestion.

Build and search the derived local vector index with:

```bash
python scripts/cc.py memory vector rebuild --project-root .
python scripts/cc.py memory vector search --project-root . "payment reconciliation evidence"
```

The vector index is local, sparse, stdlib-only, and rebuildable. It does not use
remote embeddings, it does not add a new source of truth, and it can be rebuilt
from the canonical SQLite chunk records.

Inspect optional semantic adapter configuration with:

```bash
python scripts/cc.py memory semantic status --project-root .
```

The default adapter is the rebuildable local sparse index. In status output,
`requestedAdapter` is the configured selection and `activeAdapter` is that
selection only when this build supports it and its required configuration makes
it eligible for an execution attempt. An empty `activeAdapter` means the request
was rejected; sparse retrieval remains available as the baseline. Retrieval
reports separately identify the requested adapter, the adapter eligible for
dispatch, whether an adapter process was attempted, and whether returned scores
were used. Configuration or eligibility does not prove that a command exists,
will succeed, or was used.

Optional local runtime scoring is eligible only when `semantic_adapters.json`
explicitly enables `local_runtime_v1` with a command. `cc memory retrieve` sends
the child process a JSON object containing the interface version, adapter ID,
query, and candidates. Each candidate contains `id`, `recordType`, `type`,
`title`, `path`, `headingPath`, `lifecycle`, and `text`; candidate text is
truncated to 4,000 characters before this step. The effective `maxCandidates`
shown by JSON and text status defaults to 200 and is clamped to 1 through 1,000.
The effective `timeoutSeconds` shown in both formats defaults to 30 seconds and
is clamped to 1 through 300 seconds. Numeric `0` selects the default; string
`"0"` converts to zero and then clamps to 1. The count limit is not a total-byte
limit or a confidentiality boundary.

The local command runs with the ambient environment, current working directory,
network access, filesystem access, and other process permissions provided by
the operating system. This module does not impose a network sandbox, environment
isolation, or working-directory isolation. Explicit project configuration is
required, but it does not establish per-call human consent, secret filtering,
safe storage, host delivery, or network isolation. Review the command and the
data it can receive before opting in. `cc memory semantic status` reads
configuration only: it does not probe or execute the command, validate it, or
create memory state to support its report.

`official_api_v1` scoring is unimplemented in this build and is always
unavailable, even when provider, model, environment-variable name, and a value
for that variable are present. Status reports the non-secret configuration and
environment-variable presence without printing the value or treating it as a
valid credential. No API request is made, and consumer login sessions or tokens
are not reused.

Export a derived graph projection or lightweight HTML viewer with:

```bash
python scripts/cc.py memory graph export --project-root . --format html --output graph-view.html --type decision --lifecycle active
```

Filters support `--type`, `--lifecycle`, `--confidence`, and `--source`.
`--source` accepts `dev`, `project`, `application`, or path text. The export is
only a projection: SQLite memory and source files remain authoritative.

### Lifecycle Workflows

Memory lifecycle changes are explicit and auditable. Use `lifecycle mark` for a
single entity, `lifecycle supersede` when a newer record replaces an older one,
and `lifecycle conflict` when two records cannot both be true.

```bash
python scripts/cc.py memory lifecycle mark --project-root . docs/old-plan.md --state legacy --reason "Kept for historical reference."
python scripts/cc.py memory lifecycle supersede --project-root . docs/old-plan.md docs/new-plan.md --reason "The new plan replaces the old plan."
python scripts/cc.py memory lifecycle conflict --project-root . docs/decision-a.md docs/decision-b.md --reason "The decisions select incompatible defaults."
```

These commands update entity lifecycle state, update semantic chunks for the
same document path, write event records, and add graph edges for supersession or
conflict. They do not delete source documents.

### Decisions

```bash
python scripts/cc.py memory decision add --project-root . "Keep memory local" --area Memory --body "Development memory stays under .controlcoding." --rationale "It must be removable and separate from application runtime memory."
```

Use decisions for canonical project choices. Generated views surface recent
decisions in handoff and work context.

### Consults

```bash
python scripts/cc.py memory consult record --project-root . --title "Manual architecture review" --backend "manual" --summary "Reviewer recommends keeping consult records non-authoritative." --status accepted --decision-outcome accepted
```

Consult records inform memory. They do not own canonical truth. Convert accepted
consult outcomes into explicit decisions when they change the project.

### Agent Runs

```bash
python scripts/cc.py memory agent-run record --project-root . --role reviewer --host codex --task "Review memory refactor" --summary "No public CLI regression found." --changed-file scripts/cc_memory.py --verification "pytest targeted"
```

Agent-run records preserve what was attempted, what changed, and what was
verified. They are evidence, not authority.

### Handoff

```bash
python scripts/cc.py memory views generate --project-root .
python scripts/cc.py memory sync-report --project-root .
```

Use generated views for session handoff:

- `.controlcoding/views/HANDOFF.md`
- `.controlcoding/views/WORK_CONTEXT.md`
- `.controlcoding/views/IMPACT_MAP.md`
- `.controlcoding/views/STALE_INDEX.md`
- `.controlcoding/views/CONSULT_LEDGER.md`
- `.controlcoding/views/AGENT_RUN_LEDGER.md`
- `.controlcoding/views/GRAPH_INDEX.md`
- `.controlcoding/views/VECTOR_INDEX.md`

## Local Temp Diagnostics

On Windows, interrupted pytest or sandboxed smoke tests can leave ACL-locked
local temp directories such as `.pytest-local-*`, `.pytest-tmp`, or
`.tmp_pytest*`. Diagnose them with:

```bash
python scripts/cc.py memory cleanup-temp --project-root .
```

Remove only verified candidates inside the project root with:

```bash
python scripts/cc.py memory cleanup-temp --project-root . --apply
```

If removal is blocked, the command prints a manual PowerShell step for the exact
verified path. Confirm no test process is using the directory before running a
manual cleanup.

## Migration, Rebuild, And Privacy Notes

Graph, retrieval, packet, vector, view, and export artifacts are rebuildable
projections. The canonical Dev Plane source is `.controlcoding/memory/memory.db`
plus project source files. The canonical Project Plane source is
`CONTROLWORK.md` plus `.controlwork/memory/`.

After changing project documents or memory entries, rebuild derived state
explicitly:

```bash
python scripts/cc.py memory scan --project-root .
python scripts/cc.py memory vector rebuild --project-root .
python scripts/cc.py memory views generate --project-root .
python scripts/cc.py memory work-status --project-root .
```

Use `work-status` to inspect Project Plane drift before pull or push sync. It
reports update requests, but it does not auto-sync or write hidden changes.

Memory stays local by default. Optional semantic API adapters require explicit
official API configuration. Do not store secrets, personal data, customer data,
or confidential material in ControlCoding or ControlWork memory unless the
project owner has approved that storage.
