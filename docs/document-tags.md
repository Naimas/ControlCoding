# Optional topic tags for project documents

The desktop ControlWork **Sources → Topic tags and document filters** panel lets
you label an indexed document with topics such as `authentication` or
`accessibility`. A tag describes subject matter. It does not identify the
document's purpose, area, project phase, approval, code completion or priority.
The existing document category and the ten organization sections remain
independent. No tags are required to use ControlCoding or a coding assistant.

Select an indexed document, enter comma-separated tags and a reason, then choose
**Save reviewed tags**. Existing names can be reused; equivalent Unicode,
whitespace and case forms cannot create duplicate tags. The project can hold up
to 1,000 distinct names, each at most 64 characters; one source can have up to
24 tags. The original file is never edited. Assignments and their bounded
history live in the private knowledge archive and are included in a logical
memory backup. A source change leaves old labels visible for review but removes
them from current filtering and retrieval until they are confirmed again.

Use the tag checkboxes to filter the Sources library by **any** or **every**
selected tag. Text, document category, organization section and area can be
combined with that filter. Library navigation and result counts are bound to
an archive snapshot, so a concurrent edit requires a fresh page. The document
map loads a bounded window of currently tagged nodes without changing its
topics or inventing connections. A result still opens the original formatted
document; its tag chips can return directly to the filtered source list.

Search & answers has an optional tag scope. It limits the current original
passages *before* ranking and GraphRAG traversal. Two current documents with a
shared tag can provide a bounded discovery hint; this is a topic association,
not a dependency or proof. Normal source/path limits still apply. An answer
must cite source passages, never a tag. Historical retrieval does not accept
current tag filters.

## Local AI suggestions

**Suggest tags locally** uses only the chosen installed Ollama model. It reads
at most the first 24,000 characters of the selected indexed text and the
current project tag vocabulary. Each proposed name must include an exact quote
from that text and an explanation. The proposal records source revision, model
and whether the read was truncated. It persists as pending, accepted, rejected
or stale. No proposal becomes a tag without a separate user action. You may
accept one, reject it, or put it into the manual editor and change it before
saving. A changed source or tag vocabulary requires revalidation. The
Concierge, its provider and its prompts are unaffected; no remote fallback or
automatic archive-wide tagging occurs.

## Shared tag management

The panel shows current usage counts. **Preview change** lists affected
assignments before a rename, merge or removal. A rename colliding with an
existing name requires an explicit merge. The apply step checks that the
preview is still current and writes a receipt; the latest change can be undone
if no conflicting tag edit followed it. Missing or deleted sources are included
in management so their retained assignments cannot silently point to removed
tag IDs. Forgetting a conversation removes its assignments, proposals and tag
history, and drops labels that were used only by that conversation.

The tag API is also available through `scripts/cc_knowledge.py` (`tag-view`,
`tag-save`, `tag-proposal-save`, `tag-manage-preview`, `tag-manage-apply`,
`tag-undo` and related actions). JSON requests go through stdin. These
commands obey the configured knowledge scope and do not discover files outside
it. The desktop feature is a development candidate until release review.

The project-configured Documentation or Concierge AI can also propose topic tags
while classifying a document for a [plan reference](./document-codes.md). Those
tags still require separate review in this panel; a plan reference is not a
topic tag or independent evidence.
