# AI document classification and plan references

In **ControlWork → Sources → AI document classification and plan codes**, set a
short project code such as `PLAT` and review the available document kinds. The
initial kinds include `DEV` (development plan) and `PROT` (prototype plan); you
can change their meanings or add project-specific kinds. `NONE` means the source
does not need a structured reference.

The project's enabled **Documentation** AI role analyzes each selected document.
If that role is disabled, the enabled **Concierge** role is used. The classifier
has its own bounded instructions; it does not rewrite either role's prompt or
require the coding assistant to follow this classification. Local Ollama and
configured API models use their existing provider connection and request limit.
Manual handoff is available in the same panel. For an API role, the selected
source excerpt, path/title and classification vocabulary leave the computer when
you request classification. No remote fallback occurs.

The AI proposes a kind, phase, exact supporting passage, explanation and up to
four topic tags with supporting passages. The source is limited to the first
24,000 characters and to the role's smaller context limit, if configured. The
panel marks partial reads. Invalid, fabricated or incomplete model replies are
rejected. Failed sources remain visible and can be retried explicitly.

A proposal is **not** a confirmed classification. Review the passage, edit the
kind, phase and reason if needed, then confirm or reject it. Confirmation
allocates the next project-wide number under the knowledge archive's writer
lock, for example `PROT-PLAT-F03-001`. The model never supplies that number.
The confirmed reference also appears on the document's Sources card.
`NONE` consumes no number. A private immutable identity remains stable if a
document is revised or moved to another phase. Previously allocated numbers
are not recycled. Changing the configured project code affects new references;
existing references keep their issued project code.

The optional **Analyze new or changed documents automatically** setting runs
while the desktop panel is open. Each 30-second refresh reconciles the selected
source scope and processes at most one eligible document, subject to the AI
role's request limit. It creates **proposals only**. It does not continue in
the headless Python worker after the desktop closes. A restored memory backup
turns this automatic setting off until you enable it again. A changed original
invalidates its confirmed classification and makes it eligible for a fresh
proposal. The source file itself is never renamed or edited.

For a newer plan, open **Plan succession** on its classified document, find the
earlier classified plan, explain the replacement and confirm the link. Both
documents must have current reviewed references. The earlier plan then shows
which plan replaced it. ControlCoding does not infer this relation from a title
or let an AI confirm it. If either original changes, the link needs review and
no longer guides GraphRAG. You can remove or replace the link explicitly.

In **Search & answers → Retrieval routes and scope**, enter up to eight exact
reviewed plan references, one per line. Search then considers only current
sources with those references, across text, local embeddings, wiki routing and
graph traversal. The filter combines with paths and topic tags. A stale or
unknown reference returns no matching current evidence; historical search does
not accept this current-state filter. Current reviewed succession links can
guide graph traversal, but neither the link nor the reference proves that a
plan was implemented.

Proposed topic tags appear separately under **Topic tags and document filters**.
Accepting them there makes them available for source filters and cited GraphRAG
retrieval. A structured plan reference is separate from those subject tags and
is not evidence of implementation, approval or test completion. The original
document remains readable through the existing source reader.

The archive stores classifications, proposal state and history privately.
`scripts/cc_knowledge.py` exposes `code-settings`, `code-configure`, `code-view`,
`code-next`, `code-prepare`, `code-proposal-save`, `code-decide`, `code-fail`,
`code-link-candidates`, `code-link-save` and `code-link-remove` for reviewed
integrations. No project-specific scheme is mandatory.
