# Unified project knowledge

For bounded additional retrieval after an insufficient first result, see
[follow-up search](knowledge-followup.md).

The optional desktop **Memory > Memory, wiki & conversations** workspace coordinates
project documents, portable ControlWork records, existing Dev memory, conversations,
source-bound wiki pages and hybrid retrieval. Enable it by saving its source and
retention settings, then select **Refresh sources & wiki**.

This is an embedded ControlCoding extension. `CONTROLWORK.md`, `.controlwork/` and
the `controlwork-work-plane/1.0.0` portable contract are unchanged. The coordinator
reads those records; it does not become their canonical writer or silently port
changes into standalone ControlWork.

## Sources and updates

The optional **Follow DOCX, XLSX and PDF: root + docs/** scope follows rich files
in the project root and recursively inside `docs/`. It is disabled by default.
Within that scope, **Rich documents to follow** selects all supported files or
only an explicit list (up to 128 project-relative paths, one per line). Choose files
through **Choose documents to follow**, or edit the list manually.
The native picker replaces only the draft list; **Save memory settings** applies
it. Cancelling preserves the draft. External, hidden, linked and unsupported files
are rejected; a result from a previously selected project is discarded. Use exact
path spelling and forward slashes, for example `docs/design.docx`. Saving the
list marks reconciliation required. Removing a path stops future following and
removes its active projection on refresh; originals, historical revisions and
approved portable captures remain. Missing selected files are treated as removed,
not as permission to scan other locations. This selection is retained in backups;
restoring still leaves automatic updates and the worker disabled.

**Followed document status** shows the saved selection, or recorded rich sources
when following the whole scope. Each row reports its stored revision, observation
date, active passage count and embedding count. States distinguish indexed at the
last scan, refresh required, absent at the last successful scan and disabled
following. This is not a live filesystem probe: an unsaved disk edit becomes known
on reconciliation. A failed refresh preserves the earlier generation and updates
the UI's dirty status. Indexed rows can open their corresponding derived page.
The register displays at most 128 rows with the total and an explicit limit notice;
in whole-scope mode, not-yet-scanned files cannot appear in this stored register.
After saving the scope, manual refresh, the enabled 30-second refresh, the worker
and commit-ceremony reconciliation use the same extraction pipeline. Each update
binds the original bytes, optional adjacent `filename.pdf.ocr.json` bytes and
extractor version to a new derived revision. Deletions remove active retrieval;
previous revisions and human notes remain in the archive. Approved portable
imports remain independent historical copies and are never silently rewritten.

Rich originals are limited to 1 MiB each, OCR sidecars to 64 KiB and extracted
text to 32 KiB, within the existing shared 5,000-file/128 MiB acquisition budget.
Scanned PDFs require a user-supplied sidecar bound to the original SHA-256;
no OCR engine is installed or invoked. Formatting and full spreadsheet/PDF
coverage are not guaranteed; extraction warnings remain visible in the derived
page. Citation line numbers refer to extracted text, not original page positions.
An extraction failure preserves the last coherent generation and marks refresh
required. Scope changes do not automatically erase retained historical content.

Select any combination of root Markdown plus `docs/`, Work memory and sessions,
local plans, local handoffs, generated Dev views, and canonical Dev records.
The Dev adapter uses the existing filesystem-inert SQLite snapshot reader and
preserves upstream lifecycle/provenance. Its scope also includes recorded test
receipts, explicitly labelled as observed rather than currently certified.

Data lives in `.controlcoding/knowledge/knowledge.db`. A single OS lock serializes
writers. SQLite transactions atomically advance source revisions, passages,
explicit graph edges and wiki pages. Interrupted transactions roll back; unfinished
jobs become retryable. A failed source scan retains the previous generation and
marks reconciliation required. No partial new scan is advertised as current.

Changed-source passage preparation has private recovery checkpoints. After an
initial immediately durable source, acquisition and passage checkpoints are saved
in transactions of at most 32 sources, with a 1 MiB batching target (a single
bounded source can exceed that target). A crash may require reprocessing the
uncommitted batch of at most 32 sources; earlier complete batches survive. Progress
is committed with the matching rows. Canonical publication remains atomic.
After an interruption, a fresh source capture must match the checkpoint's project, policy,
HEAD, generation and content identities before prepared work is reused. Sources
are checked again before publication. **Maintenance receipts and limits** shows
remaining checkpoint counts; **Refresh sources & wiki** retries the operation.
These checkpoints are not searchable evidence, are excluded from backups and
are cleared when their conversations are forgotten or reduced to summary-only.
Content acquisition also checkpoints captured files. Readers close each file and
its ancestor handles before proceeding to the next, rather than keeping the
whole corpus open. A fresh metadata inventory guards checkpoint reuse, and an
uncached content capture precedes publication of changes. Directory discovery
still restarts; the documented scan and transport limits have not been raised.
Scope changes and conversation privacy operations clear both checkpoint types.

Stored source identities remain bound to their current paths across later scans.
A content-preserving move is inferred only when the old and new observations are
unambiguous; repeated moves preserve the same identity and human notes. A new
document at the old pathname receives a separate identity. Duplicate content is
not sufficient to merge documents or transfer their notes.

Unchanged content keeps its index. Changed content invalidates its passages and
derived page. Deleted or out-of-scope sources leave current retrieval and wiki;
their revision history remains stored. Unambiguous same-content renames retain
identity. Ambiguous renames are recorded as removal/addition. Branch/HEAD changes
are observed alongside working-tree documents; no branch is silently checked out.

Automatic mode reconciles every 30 seconds while the panel is open and performs
one embedding batch when needed. Reopening resumes reconciliation. The optional
worker continues after the panel closes; it is off by default, requires explicit
enablement, has a per-project process lease and installs no startup service.
Disabling it stops it after its current bounded request/cycle. Reboot requires
restarting the worker or panel. Without either process, no background work occurs.

Commit ceremony integration observes the changed sources and HEAD, not the commit
message alone. An explicit ceremony completion command is also available:

```powershell
python scripts/cc_knowledge.py --project-root . --event commit-ceremony sync
```

Run this from the distribution containing the helper, with the intended project
root. No protected hooks are altered. No neural inference runs inside Git, and
observing a commit never certifies ceremony compliance. Completed desktop checks
request reconciliation when automatic memory is enabled.

## Conversations and continuity

The conversation importer accepts portable JSON, Markdown with User/Assistant
(or Utente/Assistente) labels, and plain text. Preview and redact the export
before saving. Unlabelled text is explicitly marked as having an unverified
speaker; code fences do not become role markers. Markdown/text import IDs are
content-derived so identical reviewed imports do not duplicate sessions. No
attachments or hidden host history are collected. Archive retention rules still
apply; imports never establish approved decisions.

Retention choices are no retention, summary only, or visible transcript plus
summary. Role conversations and reviewed manual handoff replies use this policy.
Direct AI also requires its Archive option. Provider/model, context identity,
prompt identity where available, and incomplete-response provenance accompany
visible turns. Private internal model reasoning is never collected.

Open a retained conversation and choose **Resume in agent chat**. A bounded recent
history is restored to its role, with the same persistent conversation identity
and next event sequence. History outside the provider context budget remains in
the archive. Summaries are contextual notes, not approved project decisions.

The portable JSON import preview permits inspection/redaction before save. Imports
are explicitly marked external evidence. Identical event IDs replay idempotently;
conflicting event content is rejected transactionally. Export is selectable JSON
in the conversation view. **Forget** removes the conversation and its derived
passages/wiki history; it does not erase separately owned imports, backups or OS
storage remnants. Changing the global retention policy affects subsequent saves,
not existing archives.

## Wiki and GraphRAG

The internal wiki has source pages, automatic topic digests and explicitly saved
AI synthesis drafts. Topic digests explain their ordered classification rule and
quote source text with line ranges and revision identities. They group sessions,
decisions, plans, verification, architecture, operations, knowledge and reference
material; an unmatched document goes to the explicitly labelled reference group.
Digests show at most 80 sources per topic; the source library retains the full scope.
Human notes are stored separately and survive source refreshes. Open a source to
read its white-paper Markdown page and headings. The original full document/image
reader remains available in the documentation map; wiki excerpts are limited to
6,000 characters and never fetch images from the network.

After generating a Concierge answer, **Save cited synthesis as wiki draft** stores
its provider/model, source ledger and revision. Every prose paragraph must include
a known citation, and its supplied passage must match the current indexed source.
This validates binding, not factual entailment: the page remains an **unreviewed AI
synthesis**. It is never indexed as independent evidence. Source changes flag the
draft for review instead of silently rewriting it. **Check wiki sources** reports
missing/changed sources and broken anchors; it does not claim contradiction detection.
Historical page views also show their current source mismatches. Each page exposes
its source reader links and selectable Markdown export, including human notes and
the source ledger. Forgetting a conversation removes derived topic/draft revisions
that quoted it, as well as its original transcript projection.

Retrieval combines lexical ranking, actual local neural embeddings and explicit
one-hop recorded relationships. Embeddings use the local Ollama `/api/embed`
endpoint with truncation disabled; model name and digest bind the cache. Replacing
a model invalidates the old vector space. There is no automatic download, remote
fallback or API key in the index. An unavailable or incomplete semantic index is
reported explicitly; lexical coverage remains available.
Query inference releases the writer lease, allowing conversation saves during a
model request. A generation, policy or pending-source change during inference
rejects the answer packet and requires a new query.

Query ranking streams passage text and vectors instead of loading the complete
corpus into Python objects. Only selected evidence passages are hydrated for the
answer. Scalar ranking data still grows with passage count; this change does not
raise ingestion limits or establish full-corpus performance targets.

Queries first reconcile the selected source scope. Results include source path,
revision, line range and the actual passage. Conversation and derived-view evidence
retain their lower-trust labels. The configured Concierge can synthesize an answer
after the user reviews the outgoing evidence and explicitly sends it. The UI checks
citation identifiers; this does **not** prove that every generated claim follows
from its citation. The cited passages remain available for human inspection.

### Use the same evidence with another agent or an external chat

Retrieve evidence in **ControlWork > Search & answers**, then select **Use this
evidence in AI & Sessions**. Set a role's context to **Selected cited memory
evidence**, or choose that context in Direct AI. The role can use a configured
local model, an API provider or manual handoff. Changing transport does not grant
file editing, command execution or automatic approval authority.

The outgoing preview includes original source passages, evidence kinds, paths,
revision hashes and line anchors. It excludes the generated answer. Complete
passages are included within the selected character budget; an explicit count
reports omitted passages. The app does not silently substitute unrelated legacy
Work evidence when unified memory is enabled but its packet is unavailable.

Before a role/Direct AI send, manual clipboard export or manual reply acceptance,
the app reconciles the source scope again. Changed/deleted inputs, a pending
refresh or a changed project invalidate the packet. Retrieve fresh evidence and
review the new request. Archived role/direct exchanges retain the selected source
lineage. With unified memory disabled, the existing portable Work packet remains
available. Provider selection and outgoing review remain explicit.

## Limits and operations

The Sources, Wiki and Conversations libraries use server-side search and windows
of at most 50 records. Totals and previous/next controls are explicit; search
covers the whole selected library, not just the loaded window. Source search
matches title/path, wiki search matches title, and conversation search also matches
the retained summary. A changed source generation, policy, wiki draft or saved
conversation invalidates navigation; the panel reloads a coherent first window.
The documentation-map canvas remains continuous and uses its existing bounded
catalog. Paged lists alone do not raise the ingestion or map capacity limits.

- Ordinary stable local filesystems, Windows desktop and portable Python service.
  Concurrent hostile junction/mount replacement is outside the declared guarantee.
- Up to 5,000 file sources, 256 KiB each, 128 MiB per scan, 50,000 passages, 128 Dev
  records, 1,000 conversations and bounded per-conversation JSON. Exceeding a budget
  fails explicitly; narrow the scope or split the conversation.
- Office/PDF/OCR ingestion uses the existing explicit Work import/extraction
  workflow. This coordinator indexes the resulting text records; it does not
  silently parse arbitrary binaries or collect other apps' chat databases.
- Source status is an observation time, not a claim of continuous file freshness.
  The archive is private local state and must not be committed or published.
- All-generation retention grows the database. At 2 GiB the coordinator refuses
  further access rather than silently pruning history. Logical backups are limited
  to 2 GiB, with at most 4 MiB per serialized row/value. These limits do not
  establish the larger completion-plan scale target.

**Backup and recovery** saves a consistent logical `.ccmemory` archive under the
writer lease, including retained conversations, source snapshots, wiki history
and notes. Choose a new external filename; an existing file is never overwritten.
Rebuildable passage/vector and derived-edge caches are omitted from new exports;
they are reconstructed after restore. Version-one archives that contain those
caches remain readable, and restore still discards them before reconciliation.
The checksum detects accidental corruption, not authenticity. Restore only trusted
backups into a project without an embedded archive. Export serializes rows into
a temporary file beside the selected destination; restore parses incrementally
into a disposable SQLite temporary database. Fixed table/column shapes, policy
and checksum are validated before creating the target archive; imported SQL is
never executed. Unmigrated archives retain the `CCMEMORY/1` JSON shape. Explicit
[consolidation migration](knowledge-consolidation.md) adds reviewed jobs and
claims and execution records with `CCMEMORY/3`; schema-2 manual archives remain
readable and explicitly migratable. Opening an archive does not migrate
it. Older versions with the
64 MiB ceiling still reject larger exports. A process killed during final file
copy may leave an incomplete output; its checksum prevents successful restore.
Restore invalidates source/vector caches and disables the worker
and automatic refresh. Review the scope and reconcile original files before using
retrieval. Original project files, upstream Work/Dev stores and provider secrets
are not included. Backups remain separate from conversation deletion.

Before attempting a desktop transcript save, frozen conversation/event identities
enter an ordered private outbox in the panel profile, partitioned by the physical
project path. Pending saves can be recovered after restart and explicitly retried;
idempotent events prevent duplicate turns after a lost acknowledgement. The outbox
is capped at 64 entries / 512 KiB, contains no transcript turns under summary-only
retention and is removed after successful saves. It is not encrypted and is not
part of a project-memory backup until its events reach the archive. A change of
project cannot replay another project's queue. Forget also clears pending events
for that conversation.

The CLI uses an explicit root, fixed actions and JSON stdin for configuration and
records: `status`, `configure`, `sync`, `index`, `catalog`, `library`, `page`, `notes`,
`conversation`, `conversation-read`, `query`, `forget`, `worker`, `wiki-lint`,
`wiki-draft`. Native backup/recovery uses the separate `backup` / `restore` actions
with `--archive ABSOLUTE_EXTERNAL_PATH`; renderer requests cannot supply a path.
The desktop bridge
owns the selected root and exposes no generic shell or filesystem write route.

Implementation verification is separate from release acceptance and from the full
ControlWork completion plan. The small local-model evaluation corpus is a regression
check, not a general accuracy benchmark or an independent release review.

## Map catalog transport

The desktop requests a snapshot-bound graph view with at most 200 source rows,
plus at most 81 focused neighbor records and 1,000 visible relationships. Topic
summaries count the complete indexed scope. Search runs against the archive;
paging replaces the detail window on the same canvas. Main and renderer do not
assemble the full catalog. Outside-window relationships are counted explicitly.
The original reader resolves a source by its indexed ID even outside this window.
The bridge retains its 1 MiB response ceiling. The legacy catalog-window API
remains available for explicit export/compatibility consumers.

## Source recovery and reviewed OCR

Acquisition records up to 128 per-path diagnostics, including input identity,
attempt count, timestamp and recovery state. Recoverable extraction failures are
collected across the scope. The last coherent generation remains stored, while
current retrieval is disabled until reconciliation succeeds. Retry reuses valid
checkpoints and verifies the complete scope; it never silently publishes a mixed
generation. A pending recovery becomes resolved only after coherent publication.

In the UI, reviewed OCR text can be previewed and approved for a followed PDF.
The private record binds the original SHA-256 and takes precedence over an
adjacent sidecar. The PDF is not modified. A changed PDF invalidates the record.
This manual workflow accepts up to 24,000 UTF-8 bytes and makes no AI call; users
can obtain transcription from their chosen local/API/external OCR tool. Page
boundaries and transcription accuracy require human review. Existing portable
ControlWork OCR adapters remain a separate explicitly configured CLI workflow.

## Reviewed work traceability

With the Dev records scope enabled, feature and criterion observations reuse
the existing Project Map owner adapter and the same node identities. Canonical
blocked states, declared acceptance criteria and feature-to-criterion edges
remain source-bound. Dev records retain their existing engine identities. The
adapter does not change feature state or certify a receipt; missing owners are
shown as not recorded. Up to 128 feature/criterion projections are admitted.

The Work traceability panel records proposed and reviewed links between observed
sources: part_of, depends_on, blocks, decides, evidences and conflicts. Users
explicitly assign objective, phase, activity, blocker, decision, evidence or
document roles and provide a rationale. These are owned review overlays, never
implicit promotion of canonical Work/Dev records or certification of completion.

Approval binds both source revisions. Changed/deleted sources invalidate the
link; rebinding saves a new proposal that needs another approval. Dependency and
parent cycles are rejected. Conflict resolution is a distinct recorded review,
not acknowledgement alone. Review history, source revisions and explanations are
persistent and included in backups. The register is limited to 128 links, 64
review transitions per link and 128 KiB total. Current approved links appear in
the graph and retrieval context; stale or proposed links are excluded.

## Capacity and exact vector ranking

The service now permits the combined 5,000-file / 50,000-passage / 1,000-session
scope, with 6,144 total observed sources including other authorized record kinds.
Acquisition readers reuse at most eight source handles at a time. Successful
source checkpoints can reference already stored bodies instead of copying them.
SQLite indexes cover source deletion and pending embeddings. Unchanged generations
do not rebuild wiki pages and edges unnecessarily.

Exact vector scoring streams batches of 128. An already installed NumPy runtime
accelerates the computation; the standard-library fallback remains available.
No model or dependency is installed automatically. Latency depends on hardware,
model and corpus; configured ceilings are not a universal performance guarantee.
The external combined benchmark records real embeddings, repeated refresh/query
timings, process RSS and interruption recovery. Provider/GPU memory must be
measured separately from Python process RSS.

## Quality evaluation

Retrieval ranks current passages using length-normalized BM25 and available
semantic ranks, selecting up to ten candidates. Graph edges remain inspectable
metadata; linked documents do not automatically become relevant evidence. The
packet explicitly marks answerability as not assessed. The configured Concierge
may return a cited draft or an explicit insufficient-evidence response. Honest
abstention needs no fabricated citation and cannot be saved as a wiki synthesis
through the answer panel. Citation validation is not entailment verification.

Lexical ranking also uses the source title (first 180 characters, fixed weight
two), so a factual passage can be found when it omits its document's subject.
Identifier underscores split into whole search terms, without substring matching.
With a complete semantic index, selection preserves up to three top candidates
from each signal before filling the ten slots by fused rank. An incomplete or
invalid semantic index falls back to the exact lexical selection with a warning.
These are transparent retrieval heuristics, not confidence or factual-support scores.

The [frozen quality evaluator](knowledge-evaluation.md) validates version-bound
corpus, run and citation-judgment packets and reports held-out recall, abstention,
anchor validity, isolation observations and human grade coverage. Missing evidence
cannot become a passing score. It is measurement infrastructure, not an automatic
claim of independent review or completion of the wider adoption/release gates.
