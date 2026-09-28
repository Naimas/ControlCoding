# AI role assignments

This local desktop development increment adds **AI & Sessions -> AI role
assignments**. It is separate from the accepted, frozen RC3 observer package.

## Configure and use

1. Select a project. Connect Ollama, OpenAI, or both; connection lists models
   without submitting project content. OpenAI credentials are session-only.
2. Choose Concierge, Documentation & wiki, Architecture & design, Review &
   control, Development, or Semantic embeddings. Enable only the roles you need.
3. Assign a provider and a compatible installed/account model per role. Set the
   allowed context, sending mode, timeout and request limit. Chat roles also
   expose output-token and added-context limits. Save the assignments.
4. Enter a request for the selected role. Reviewed mode displays all outgoing
   messages before a second Send. Direct mode sends on your explicit submission
   using the saved policy. Neither mode starts an unattended agent.

For an external chat operated by the user, choose **Manual · copy/paste with
another chat** instead. This works without a connected provider or model.

## Manual chat ping-pong

1. Enable a chat role, select manual execution, configure its instructions and
   allowed context, and save. Embeddings do not support this conversational mode.
2. Enter the task and **Prepare manual prompt packet**. Inspect the effective
   system instructions, selected context and role conversation in the packet.
3. **Copy packet**, then paste it into the chat you choose. The panel only writes
   the clipboard after your click; it does not open, contact or monitor that chat.
4. Paste the complete returned text, including the first line
   `CC-REPLY: <request ID>`, into **External reply**. The packet asks the other
   chat to include this marker. An absent or mismatched marker blocks import.
5. **Review pasted reply**, then **Accept reply into conversation**. Editing the
   pasted text invalidates the review. Acceptance adds externally supplied text
   to the role history; it does not apply suggestions or verify claimed actions.
6. Enter a follow-up to create another round, or **Return reply to concierge**.
   Returning attaches the accepted reply to a new concierge request; you still
   explicitly prepare/send that request, using the concierge's saved mode. The
   concierge must be enabled before submitting. It can use manual or API mode.

Each packet has a fresh correlation ID. The marker associates the pasted answer
with the pending request; it is not authentication of a model or author. The
panel cannot enforce the external chat's model, reasoning effort, token budget,
deadline or actual tools. Generation preferences in the packet are requests for
the user/external chat, not API settings applied by ControlCoding.

There is one pending manual packet and one retained accepted external reply.
Preparing a replacement packet supersedes the pending one. Packet creation
counts against the role's session quota; copying again does not count again.
Pasted answers are limited to 12,000 characters plus the marker, and packets to
64,000 UTF-8 bytes. Source/policy changes invalidate copying or accepting a stale
packet. Replayed replies cannot be accepted twice. A failed paste preserves the
existing conversation. Clearing role conversations or changing projects removes
all manual state; closing the app also loses these session-only exchanges.

Saving policies clears histories and pending packets but retains the last
accepted external reply as an explicitly selectable, unverified source. This
lets you enable/configure the concierge and then return the reply to it. That
reference carries no Setup import authority: the existing proposal path still
requires the original current project/policy/source binding and permission.
No manual reply automatically enters the persistent memory/archive.

Local and API connections coexist. An unavailable model produces an error;
there is no provider fallback, model installation or automatic retry. Listing a
model does not establish chat/embedding compatibility. The older **Direct AI
conversation** remains a separate connection and conversation workflow with its
existing optional archival; it does not inherit role policies.

## Policy and authority

Each chat role has a visible preset instruction editor (up to 4,000 characters),
**Restore preset prompt**, and a full effective system-prompt view. The latter
includes the read-only runtime boundary and the current proposal/JSON rules.
Edits take effect only after saving; saving clears previous histories/previews.
An instruction claiming access cannot grant tools or override application checks.

The model options expose only controls verified by this adapter:

- Reasoning effort and, for supported OpenAI models, standard/pro reasoning mode.
- Exact Ollama thinking values from `/api/show`; on older servers the UI offers
  only `On` for a generic thinking capability, or low/medium/high for `gptoss`.
  It does not invent a Qwen effort level or promise that thinking can be disabled.
- Temperature, output verbosity, JSON object output and an optional provider
  reasoning summary where included in the capability catalog. The summary is
  separate from the answer; raw hidden reasoning is not exposed or archived.

Selecting a local model inspects its metadata without inference. Inspection can
also be refreshed explicitly. OpenAI uses an exact-model-ID catalog dated
2026-09-24, not capability discovery from `/models`. Unknown IDs retain basic
requests with provider defaults, with advanced controls disabled. This is a
conservative supported subset, not an exhaustive list of everything a model can
do. Model availability/account acceptance remains the provider's decision.

Provider/model changes reset generation options to provider defaults. Default
means the parameter is omitted, not that reasoning is disabled. Incompatible
values are rejected by the main process and provider adapter. Where sampling
requires non-reasoning operation, temperature requires an explicit `none`
effort. Inspect again after replacing a local model; cached metadata does not
establish that the underlying model bytes have remained unchanged.

The request preview includes the effective instructions and API parameters.
JSON mode requests an object and validates complete returned output; it does not
guarantee a particular schema. Output-token budgets also cover reasoning on
supporting APIs, and the existing request deadline still applies. An exhausted
budget with no final text is reported explicitly; it is never shown as success.
No option enables web search, tools, file access or autonomous code execution.

Protocol references: [OpenAI reasoning controls](https://developers.openai.com/api/docs/guides/reasoning),
[GPT-6 guidance](https://developers.openai.com/api/docs/guides/latest-model),
[GPT-5.2 verbosity and sampling](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2),
[JSON output](https://developers.openai.com/api/docs/guides/structured-outputs),
and [Ollama thinking metadata](https://docs.ollama.com/capabilities/thinking).
Exact effort sets/snapshots are taken from the linked official model pages for
GPT-6 Astra/Sol/Luna, GPT-5.6 Sol/Terra/Luna, GPT-5.5, GPT-5.4, GPT-5.2,
GPT-5.1 and GPT-5; GPT-4.1 has no reasoning-effort selector. Other advanced
capabilities stay disabled until added and verified.

The main process enforces the selected model/provider, allowed context, output
limit (128–8,192 tokens), deadline (5–120 seconds), added-context limit
(0–16,000 characters), and per-role request ceiling (1–100 per project/app
session). Failed and cancelled inference requests count. Clearing conversations,
saving settings or switching away and back does not reset usage. Restarting the
app does reset it: this is a session quota, not a monetary billing cap.

Context is entered text and that role's session history, optionally plus the
current prepared ControlWork RAG packet or Setup analysis packet. Nothing scans
the entire repository automatically. Requests are bounded to 48 KiB of encoded
message/input content and 24 prior history messages. Changing a policy clears
all role histories. Switching projects clears histories, results and pending
previews while retaining explicit provider connections. Source/policy changes
invalidate an outstanding preview. Each role has its own conversation.

Concierge and Architecture may be allowed to import returned Setup JSON into the
existing human proposal-review path. Import requires the same project, policy
and source packet and a complete answer; Core validates the proposal. Import
does not accept or apply suggestions. Other roles cannot enable this permission.

All chat roles return advisory text. They have no tool, shell or file-editing
authority. These settings do not configure or start the separate legacy Python
specialist runners or a coding host. Documentation is not automatically written
to a wiki, role conversations are not automatically archived, and no background
control/design/development orchestration is claimed.

## Connections and execution loops

The read-only graph at the top of **AI & Sessions** shows the six saved role
policies, their allowed context, API preview gates, provider routes, responses
and manual chat handoffs. Select a chip or connection to inspect its meaning;
focus a role, zoom, or use the keyboard-accessible connection list. The explicit
**Open role settings** action navigates to its existing policy editor. Unsaved
edits are labelled and do not change the graph.

Role status distinguishes disabled policies, missing context, context budget,
exhausted session quota, missing connections, incompatible models and unsupported
generation options. Availability is a projection of current app state, not a
guarantee that a provider will accept a future request. Runtime policy revisions,
conversation bounds and request checks remain authoritative at submission.

Rounded return tracks describe explicit API iteration, manual copy/paste/review,
return of an accepted external reply to concierge, and separate Setup proposal
review. Animation is limited to a role's pending API request and stops on
response, error or cancellation. Provider metadata discovery and clipboard
copy do not animate as inference. External chat delivery, execution and model
identity are always unobserved. Reduced-motion preferences disable animation.

The optional **Show planned orchestration** layer labels automatic delegation,
a coding executor/review loop, persistent role-chat archival and embedding-to-RAG
indexing as **Planned**. Those nodes confer no functionality or authority. The
separate Direct AI conversation's existing opt-in archive is unchanged.

The event trail contains at most 40 session-only app events: role, event kind,
provider when applicable, timestamp and sequence ID. It contains no prompt,
answer, key or internal reasoning. Project change and **Clear role conversations**
clear it. A Setup import event records the request, not successful application.
Graph interactions do not call providers or write project/configuration files.

`ui/tests/agent-graph-unit.cjs` verifies the projection and planned/current
boundary. `ui/tests/agent-graph-desktop.cjs` checks actual renderer/IPC operation,
keyboard selection, no graph-triggered requests/writes, active/cancelled flows,
manual provenance, project isolation and 320 CSS-pixel layout with fixture
providers. This does not certify live provider availability or autonomous agents.

## Persistence and credentials

The seventh role, **Memory curator**, powers the separate
[consolidation workflow](knowledge-consolidation.md). It defaults to disabled,
adds a visible editable preset, and uses stricter limits: up to 12,000 context
characters, 4,096 output tokens and six requests. Its local/API/manual packets,
durable attempts and source-bound pending proposals are managed in ControlWork.
Only selected human review publishes them to the wiki.

**Save role assignments** writes a versioned JSON file beneath the disjoint app
profile: `ai-roles/<SHA-256 of physical project path>.json`. The file contains
`schemaVersion: 4`, the project identity, and the seven role policies, including
system instructions and generation options. It contains no API keys, user
requests, conversations or source packets. It is local app configuration,
not a portable project setup manifest; moving the project changes its identity.

Version-1 policies are validated and upgraded in memory with preset instructions
and default generation options; version-2 policies retain their prompts/options.
Version-3 policies retain all six assignments and gain a disabled Memory curator
with its visible preset. Original files are unchanged until explicit Save writes
version 4. Manual mode is supported from version 3. Role files are limited to
128 KiB. Do not put credentials in instructions:
the instruction editor is intentionally persisted and sent to the chosen model.

Writes use an exclusive temporary file and rename, with an expected-revision
check. Invalid, oversized, linked or hard-linked policy files are rejected;
foreign changes are preserved and block saving/sending until the project is
reopened with a valid file. Profile/project overlap remains prohibited. Ordinary
local filesystem behavior is assumed; hostile concurrent mount/path replacement
is not covered. A new app session restores assignments but reconnects providers
explicitly. All roles default to disabled when no policy exists.

Keys stay in main-process provider memory after connection until disconnect or
exit. They are omitted from snapshots and saved files. Provider responses still
follow the providers' own data handling terms. Existing transport bounds reject
redirects, cap responses at 512 KiB, and render model text without executing HTML.

## Embeddings and verification boundary

The embedding role submits only explicitly entered text through
[Ollama embed](https://docs.ollama.com/api/embed) or
[OpenAI embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create).
It validates finite vectors (1–16,384 dimensions) and displays dimensions and a
short sample. It does not replace the current RAG index or initiate an ingestion
pipeline. An embedding-capable model must already be available.
Its operating contract is visible in the UI. System instructions and reasoning
parameters are inapplicable to these embedding endpoints and cannot be saved.

`ui/tests/roles-unit.cjs` covers persistence, malformed files, permissions,
provider routing, isolated history, stale requests, usage and embeddings.
`ui/tests/roles-desktop.cjs` exercises real renderer controls, IPC, saved policies,
project switches, response rendering and 320 CSS-pixel layout. Protocol fixtures
do not establish live OpenAI account acceptance or model-quality guarantees.
`ui/tests/manual-desktop.cjs` covers clipboard export, pasted-response review,
multiple manual rounds, explicit concierge return, correlation, zero provider
calls, inert text rendering and project isolation.
