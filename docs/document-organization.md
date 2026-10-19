# Optional project document organization

In **ControlWork → Sources → Organize project documents**, load the selected
archive and choose a document. ControlCoding does not move or rename originals.
No organization is required to use the project or its coding assistant.

The ten sections are:

| Section | Purpose |
| --- | --- |
| Project direction | Scope, goals and overall design and architecture |
| System areas | Responsibilities and design of individual areas |
| Area plans | Planned evolution of an area |
| Requirements and specifications | Expected behavior and acceptance conditions |
| Directives and decisions | Working rules, choices and their reasons |
| Phase execution plans | Concrete steps and dependencies for a phase |
| Implementation and deliverables | What was implemented and delivered |
| Release and operations | Installation, updates, operation and recovery |
| Tests and results | Planned checks and actual results, identified in the source |
| History and provenance | Previous versions, inherited material and origins |

A document also has optional areas, a phase, an approval status, currency,
provenance notes and links to other sources. Areas and phases are chosen by the
user. The existing document-purpose category remains independent of these fields.
An approval label here is a user's description of the document, not a code gate
or proof of successful implementation. Unknown and unassigned are valid choices.

Confirming saves private metadata bound to the document version. Editing the
original, or a linked document, expires that confirmation. Previous values remain
visible for review but no longer count as current assignments. Remove organization
resets the assignment. Original contents and existing document categories remain
unchanged. The library loads 25 records per page; filters cover the selected archive.
The link picker offers documents on that page; previously saved links are retained.

## Local AI suggestions

Choose an installed Ollama model and select **Suggest organization**. This sends
only the selected source's saved text and organization metadata to the local
Ollama endpoint, with a 24,000-character source-text limit. Truncation is disclosed.
There is no automatic model installation, remote fallback or background AI sweep.
The model's instructions are shown with the returned draft. Source text is treated
as data, not instructions. Invalid or incomplete responses are rejected.

The response is an unsaved proposal. **Use as editable draft** fills the form;
**Confirm organization** is still required to persist it. Source changes during
analysis invalidate the proposal. Suggestions do not change concierge prompts,
agent policies, project folders, build steps or execution permissions. Users may
keep their own organization or ignore the feature entirely.

## Read and ask

Markdown documents open in the original-document reader. Other selected sources
open their saved/extracted text; extraction and reader limits still apply. The
question action retrieves passages restricted to the selected document, through
the existing retrieval and cited-answer workflow. It does not send a question to
an AI automatically. The user can then request an answer from their configured
local/API concierge or use the existing manual handoff.

Confirmed related-document links can guide GraphRAG exploration in broader
searches. Both endpoint versions must remain current; normal scope filters and
graph budgets still apply. Links guide retrieval, they do not establish truth.
Stale links are excluded. Forgetting a conversation removes its organization and
assignments explicitly linked to it.

Document organization does not extract executable tasks from prose or populate
the project Gantt automatically. Those remain distinct operations.

Optional subject labels are managed separately: [topic tags](./document-tags.md).
AI-reviewed document kinds and stable plan references are described in
[document classification and plan references](./document-codes.md).
