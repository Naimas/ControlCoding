# Reviewed memory consolidation

Development candidate: consolidation analyzes original project evidence through
local Ollama, OpenAI API, or a manual external-chat exchange. It saves proposed
memory changes for explicit review. Approved sections become traceable derived
memory and supplement grounded answers. Model output never approves itself.

## Desktop workflow

Open **ControlWork → Memory → Consolidation**. Project memory must be enabled.
Use **Choose backup and migrate** for an older archive: choose a new external
`.ccmemory` file. The application verifies the backup before upgrading to schema
3. Merely opening the panel does not migrate or enable model requests.

1. In **AI & Sessions → AI role assignments**, enable **Memory curator**. Its
   preset prompt is visible and editable. Choose a provider, model, supported
   generation options and limits. Manual exchange needs no connected provider.
2. Prepare a consolidation job. Its evidence window contains original passages
   not yet analyzed at their current revisions. The panel reports remaining
   sources and passages separately from approved proposals.
3. Choose **Manual external chat**, **Local AI**, or **API**, then **Prepare
   pinned analysis packet**. Inspect its effective prompt, settings, exact
   excerpts and revisions before sending or exporting.
4. Press **Send pinned request** for local/API execution. For manual exchange,
   copy/export the packet, give it to the external chat and paste its structured
   JSON reply into **Validate and import reply**. Saved packets survive restart.
5. Compare pending proposals with existing wiki sections and original evidence.
   Select proposals to accept or reject. Acceptance publishes the selected
   sections and claim records together in one transaction.

Original documents are never rewritten. The manual source-bound proposal editor
also remains available. Claims distinguish observations, decisions, plans,
inferences, disputed evidence and unknowns. Conflicts retain both sources;
recency alone does not settle a conflict. Same-job prerequisites must already be
accepted or included in the same acceptance transaction.

Acceptance rechecks project identity, source generation, Git head, policy,
privacy state and target wiki revisions. Changed evidence prevents publication.
Exact anchors establish the supplied evidence; a person must still judge whether
the interpretation is correct.

**Undo published changes in this job** restores the state before the job's first
acceptance, including later accepted batches. It refuses after subsequent edits
or context changes. Identical decision retries do not publish twice.

## Incremental analysis and automatic queue

Successful analysis records the exact analyzed prefix of a packet in a passage
ledger. Unread passages remain eligible, including the rest of a long document.
An unrelated edit does not reset completed revisions. Changed documents make
their new passages eligible again. Failed, canceled or zero-coverage attempts do
not consume progress. Analyzed means examined, not verified or approved.

The curator can compare new passages with up to two relevant current approved
claims, within a separate bounded context window. Their complete original
anchors must fit the packet; stale or replaced claims are excluded. Prior
memory is labelled as derived context and cannot serve as an independent source.
Coverage counts new passages separately from these reference anchors.

Under **Automatic queue and policy**, select source changes, retained
conversation changes, commit transitions and/or a daily window in a named time
zone. Policies default to disabled. **Queue only** never sends to a model.
Automatic local/API sends require a matching saved role, prompt, model and send
policy. API execution also requires a positive daily request cap.

The memory worker observes source/session/commit changes and enqueues work after
reconciliation. A commit ceremony is observed through its Git HEAD transition
on the next refresh; no protected Git hook is added. The open panel also checks
the daily window. Missed windows coalesce once per local date, including a
repeated daylight-saving hour. Without an executor, work stays visibly queued.

An automatic episode continues only after successful analysis with positive
coverage. It is bounded by the configured request limit (at most six), the API
daily cap, source/policy consistency and archive capacity. A large archive is
not necessarily analyzed in one episode. Failed sends are never silently retried
or sent to a different provider. Publication still requires review.

## Recovery and execution limits

One durable project lease prevents simultaneous sends by two consumers. An
interruption after transport starts has unknown usage and requires explicit
retry with a new request ID. Pause/cancel aborts the active transport; retry
consumes request budget. Timeout, malformed JSON, incomplete response and
reasoning-only output are operational failures, not insufficient-evidence
findings. Import validates the entire batch before saving any proposal. Exact
reply replay is idempotent.

API credentials stay in application memory. Restore disables schedules/sending,
invalidates saved requests and clears analysis progress. The Python refresh
worker never calls a remote chat model.

An explicit local-only executor can run one prepared packet with an existing
Node runtime while the panel is closed:

```powershell
node ui/consolidation-headless.cjs --project=ABSOLUTE_PROJECT --python=ABSOLUTE_PYTHON --core=ABSOLUTE_CONTROLCODING --profile=ABSOLUTE_APP_PROFILE --job=JOB_ID
```

Quote arguments containing spaces. It requires the same saved enabled role,
prompt, model and limits as the packet and connects only to local Ollama. This
one-shot command is not an installed background model service.

Capacity bounds:

- Twenty retained jobs; twenty proposals per job; five proposals per imported
  reply; 128 KiB job/proposal content and a 256 KiB detail response.
- Up to eight original anchors per packet within a 6,000-character anchor
  budget, shared between new whole passages and approved-memory references.
  Up to two whole prior claims and two additional original anchors fit within
  a separate 2,000-character claim context budget and the shared anchor budget.
  Passages and claims are not silently truncated. The ledger supports
  the existing 6,144-source/50,000-passage ceiling.
- Up to 12,000 input characters, 4,096 output tokens, 120 seconds per request
  and six requests per job/episode, subject to saved lower limits. Protocol and
  prompt overhead count toward input limits.
- Five reviewed wiki pages, twenty approved sections per page and 384 KiB of
  shared wiki review content. Proposal bodies contain at most 3,000 characters.

Safe pruning removes terminal diagnostics while protecting pending proposals,
current claim provenance and execution accounting. Capacity exhaustion is
reported; pending review is not silently deleted to make room.

## Approved memory, graph and answers

Consolidation includes a bounded navigable map of approved claims and original
sources, source backlinks and explicit history. Selecting a node highlights its
connections; sources open in the document reader. History is separate from
current evidence. This map complements the existing document map.

Grounded answer context can include relevant current approved claims alongside
hydrated original citations. Generated claim prose is never indexed as original
evidence. Changed sources, removed evidence or replaced reviewed sections
exclude claims from current context. Disputed claims retain their status;
retrieval does not resolve them. Context and history windows are bounded.

## Archives, privacy and validation

New and unmodified archives stay schema 1. Explicit migration upgrades schema 1
or 2 directly to 3 after verifying an external backup. Exports retain their
version (`CCMEMORY/1`, `/2`, `/3`); restore validates a temporary candidate before
creating its target. Older clients cannot open schema 3.

Forgetting a conversation removes affected packets, proposals, receipts,
progress and derived claim history. Claims whose earlier revisions used it are
removed even if their latest revision cites other evidence. Privacy removal
takes precedence over undo. Exported backups and external-chat copies remain
separate copies.

The CLI exposes consolidation view/create/propose/decide/undo, settings,
prepare/attempt/import/fail, queue/prune/context and separate migration.
Migration uses `--archive` for a new external backup. Desktop file pickers own
migration/export paths; generic renderer actions cannot supply arbitrary paths.

Local-model experiments and automated checks exercise protocol, recovery and
source binding. They do not establish human answer quality, remote production
provider acceptance, seven-day adoption or all ControlCoding release gates.
Model-dependent failures remain reported in trial evidence.

See [reviewed wiki](knowledge-wiki-review.md), [unified knowledge](unified-knowledge.md)
and [AI role assignments](panel-ai-roles.md).
