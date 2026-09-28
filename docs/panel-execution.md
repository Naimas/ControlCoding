# Desktop execution, providers and sessions

These optional desktop development workflows extend the local observer. They
are not covered by the frozen RC3 approval or archive. Core remains usable
without the UI. No model, runtime or library is installed by the panel.

## Installation and project setup

Open Setup -> **Full Core installation and project setup**. The existing draft
workflow remains available for conservative, ownership-checked updates. The full
wizard invokes the canonical CLI behavior and has different write semantics:

1. Install ControlCoding: name, primary coding host, documentation ownership,
   governed/deferred memory, host guidance, Core/manual consultation, boundary
   folders and behavioral rules. Hooks are project-local; advanced packs and
   autonomous specialists are not enabled. Saved draft choices can prefill the
   form; inspect them again before applying.
2. Confirm Core engagement: direct planning or manual consultation. This writes
   the canonical engagement configuration, with extra backends `local_only`.
3. Define the project: guided, existing-brief or existing-project adoption, with
   purpose, users, scope, exclusions, correctness criteria, references, stack and
   architecture. The Core creates the initial project/design/planning package.

Governed memory initialization is also available as a separate operation. It
uses the existing Core bootstrap: Dev Plane initialization plus a governed-scope
scan, without full-repository scanning, OCR or file relocation. Portable
ControlWork initialization remains a separate, reviewed archive operation.

Each step shows confirmed answers, the canonical command, existing control-file
inventory and effects before **Confirm and execute**. The reviewed handoff is
retained in the external app profile; canonical project configuration files
represent the installed state. The full installer may replace existing context,
initialize Git and install hooks. This is a command/effects preview, not a complete
generated-file diff or a dry-run: the Core has no full setup dry-run. A failure
can leave partial setup. No rollback guarantee from the conservative draft writer
applies to these canonical jobs. Doctor output does not attest live host delivery.

## Checks and process ownership

Checks offers doctor, verification status, required or selected verification
suites and invariant execution. The preview displays the selected command
templates and invariant contract. Selection uses the Core selector; executing a
subset is not a complete required pass. The Core owns validation and canonical
receipt creation; the UI reports the actual exit code, output and completion,
failure, cancellation, timeout or output-limit state.

`cc_panel_jobs.py` accepts a fixed set of typed workflows. The renderer cannot
submit an arbitrary executable or shell command. Project verification contracts
can themselves contain executable commands: confirming them authorizes those
commands with the user's access, not in a sandbox. Preview identities bind the
selected control files, choices, root identity and named service sources; they
are not an immutable snapshot of every project file. Inputs are rechecked before
execution. Project switches and normal exit are blocked while a job is active.

The main process starts an isolated Python helper with a restricted environment
that excludes API keys. It retains at most 2 MiB of output, displays the latest
64,000 characters and imposes a 30-minute outer deadline. Individual Core check
deadlines still apply. Cancel terminates the owned Windows process tree; it does
not undo writes. Logs, reviewed handoffs and result JSON stay in the disjoint app
profile under `jobs/<id>`. Preview helpers have a 15-second deadline. Concurrent
hostile filesystem replacement and recovery of partially completed Core commands
remain outside the ordinary local-filesystem workflow.

## AI and conversation records

The page now also offers [AI role assignments](./panel-ai-roles.md), with independent
local/API connections and saved per-project policies. The following preview,
fixed-token and archival behavior describes the separate **Direct AI conversation**
workflow. Role requests use their own saved limits and sending mode, and do not
automatically archive exchanges.

The former Agents placeholder opens **AI & Sessions**. Two adapters are provided:

- Ollama at `http://127.0.0.1:11434`, listing existing models and using its
  [chat API](https://docs.ollama.com/api/chat). The selected server/model determines
  where inference runs; the panel does not pull models or configure cloud routing.
- OpenAI at `https://api.openai.com/v1`, using the
  [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create).
  The API key is entered explicitly and held only in main-process memory until
  disconnect/exit. It is never included in renderer snapshots, jobs, preferences
  or project records. Select a chat-capable model from the account's model list.

Connect lists models without sending project content. Sending requires a concrete
preview of provider, endpoint, model, all outgoing messages and optional archival.
Requests include no tools, limit output to 2,048 tokens and use `store:false` for
OpenAI; that field is not a blanket claim about provider data retention. Requests
have a two-minute deadline and 512 KiB response limit, reject redirects and do
not automatically retry billable calls. Incomplete responses remain labeled.
No live OpenAI account acceptance is established by fixture tests.

The assistant has advisory roles only. It cannot write code, run tests or accept
its own suggestions. Context is restricted to the reviewed conversation plus an
explicitly selected ControlWork RAG packet or prepared Setup packet. Setup packets
include bounded excerpts from selected design files and source identities.
Returned JSON proposals pass the existing request/source-binding validation and
remain proposed until the user accepts them in Setup. Project text is evidence,
not authorization to execute instructions embedded in it.

When enabled before sending, each exchange is automatically archived as one
completed ControlWork session with outgoing user text/context and the visible
assistant answer. Conversation/turn labels connect the records. Hidden reasoning
is not stored. Failed archival retains the reply and offers retry; it does not
pretend memory was saved. The portable archive must already be initialized.
The existing 96-record budget applies, with 12,000 characters per message and
a 40 KiB encoded management request. Conversations are limited to 24 messages
and a 48 KiB outgoing request. Start a new conversation when the limit is reached.

This automatic collection covers panel conversations only. Other host chats can
be imported as explicitly selected text documents in ControlWork; the UI does
not silently inspect host databases, browser history or unrelated chat folders.
Switching projects clears conversation content and pending approvals, retaining
only the explicitly connected provider/model list. A new app session reconnects
explicitly; it does not read credentials from project environment files.

## Verification and release boundary

Tests cover real disposable-project installation, engagement, governed memory,
project framing, canonical check success/failure and receipts, stale previews,
owned-process cancellation, selected design excerpts and Core conversation reads.
Provider tests use bounded local HTTP fixtures and injected protocol responses;
the dated workbench additionally records a live Ollama synthetic prompt without
project content. Desktop tests exercise mouse/keyboard interactions, inert model
text, opt-in archival and 320 CSS-pixel layouts. See `tests/test_cc_panel_jobs.py`,
`ui/tests/{ai-unit,jobs-unit,completion-desktop}.cjs` and the existing regressions.

Independent release review, live OpenAI credentials/account testing, host hook
delivery, autonomous coding agents, external-host automatic capture, signing and
publication remain separate. No production project is initialized by these tests.
