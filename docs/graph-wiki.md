# Graph retrieval and reviewed project wiki

This development implementation extends the ordinary legacy/contained desktop
and the embedded knowledge CLI. Previously published desktop archives do not
acquire these changes automatically. The distinct external observer retains its
documented lexical-only interface.

## Retrieval

In **Memory → Search & answers**, open **Retrieval routes and scope**. Text search,
optional configured local embeddings, explicit graph links and wiki routing can
be compared with the same ten-passage output budget. Automatic question routing
distinguishes lookup, decisions, work, architecture and synthesis; you can override
it. Relative path/kind filters constrain original evidence. Planned/current work
filters accept only structured canonical Dev lifecycle states; ordinary prose is
not silently assigned a lifecycle.

Graph expansion follows up to two hops through explicit document references,
canonical Work/Dev links, current approved work relations, reviewed plan
succession and reviewed wiki findings. Both endpoints must remain available and in scope. Stale or proposed
relations do not expand evidence. Up to forty originals and two hundred examined
edges are visited; cycles and duplicate passages are collapsed. Graph and wiki
discoveries reserve up to two of the ten slots, alongside the existing lexical
and semantic reservations. A reached neighbor is a candidate, not proof that its
text answers the question.

Up to four matching current wiki summaries guide retrieval to original source
passages. Unreviewed AI drafts and stale summaries are excluded. Generated prose
is never returned as an independent source citation. **Why these sources?**
shows selected routes, exclusions and budget limits. A truncated traversal is
not an exhaustive project search. The configured AI receives originals and
labelled relation metadata; review classifications do not prove entailment.

The CLI `query` action accepts the existing JSON request and optional `retrieval`:

```json
{"text":"Why was this design chosen?","semantic":false,"retrieval":{"graph":true,"wiki":true,"intent":"decision","paths":["docs"],"kinds":[],"lifecycle":"all","before":null}}
```

Use `document_codes: ["DEV-PLAT-F03-007"]` in `retrieval` to restrict current
evidence to exact, human-confirmed references. Up to eight references may be
combined with paths and topic tags. Stale or unknown references contribute no
evidence. Historical search rejects this current-state filter.

For recorded history, set `intent` to `history` and `before` to an ISO timestamp
with timezone. This returns the latest retained original revision before the
cutoff, scanning at most 16 MiB of retained original text with an explicit truncation flag, not a reconstructed Git tree or proof of past project membership. Current
locators are shown; removed/out-of-scope originals remain excluded. Historical
results cannot be sent through the current-evidence AI flow. Search current
sources again before generating an answer.

## Wiki synthesis and review

**Memory → Wiki → Load reviewed wiki** exposes eleven structures: overview,
workflow, timeline, glossary, open questions, architecture/as-built, concepts,
requirements, decisions/rationale, sources and outputs/evidence. Generated source
indexes remain labelled as quoted material. They do not claim to be AI summaries.

To create a synthesis, open a page and choose **Retrieve evidence for synthesis**
(up to twenty original paths). Inspect the returned evidence, then use the
configured Concierge or the existing manual chat exchange. Save a cited AI answer
as a wiki draft, open it and choose **Submit this synthesis to a reviewed page**.
Manual cited prose can also be proposed directly. Both paths create pending
proposals; accept/reject is explicit. Citations must resolve to current originals
and prose paragraphs must carry source markers. Mechanical citation validation
does not establish factual correctness.

Approved sections survive source refresh. Changed or deleted dependencies mark
them stale and prevent their use as current wiki retrieval guides. A concurrent
page/review change rejects an old proposal rather than overwriting new prose.

## Connections, findings and revisions

**Inspect sources and findings** lists original anchors, reverse page dependencies,
work roles and reviewed links. Opening a source uses the formatted document view.
This connects objectives, activities, decisions and evidence without promoting
canonical work states or declaring a receipt valid on the reader's behalf.

Inspection proposes exact repeated assertions and differently valued assertions
with the same key. These are conservative text-pattern candidates, not a general
semantic contradiction detector. A reviewer or an AI-assisted operator can propose
additional findings with two to eight exact original anchors. Classify them as
conflict, different scope, different version, duplicate or dismissed, with a
rationale. Decisions persist, retain review history and become stale when their
originals change. Recency alone never resolves a disagreement. Current reviewed
findings can guide graph retrieval; pending findings cannot.

Revision comparison shows a unified difference between a retained page revision
and the current page. Section recovery creates a new pending replacement from a
retained approved section; it never rewinds the archive or overwrites later work.
Recovery is rejected when its original anchors or destination revision changed.
Then write an updated proposal against current sources. The bounded recovery
journal retains up to twenty snapshots within 512 KiB; the inspector shows at
most forty section entries. Existing page history remains separately available.

Wiki lint checks original links, explicit duplicate anchor IDs, orphan drafts,
duplicate citation IDs and source freshness. A link outside the authorized index
is reported as outside-index-or-missing, not asserted to be a nonexistent file.
Findings and section snapshots are private archive metadata, included in logical
backup. Forgetting a conversation removes their dependent text as well.

These functions have technical regression coverage. Independent human answer
quality, broad semantic detection, seven-day adoption and release approval are
separate acceptance gates; no provider or background automation is enabled by
opening this page.
## Reading the wiki and its original documents

### Document function, topic and original wording

In **Sources**, choose a category card to filter the entire selected archive by
function: design, implementation plan, requirements, source material, chat conversation,
work session record,
agent instructions, technical contract, verification, decision, usage guide or
work record. **Type to clarify** collects records with missing or conflicting
clues. Each card gives a defining question; selecting it shows its purpose and
examples. Counts include matching records beyond the current library page. Zero
means no included source in that category, not that the project has no such files.

A document's *type* describes its function; a *topic* groups its subject matter.
**Sources** means all selected inputs available to memory. **Source material** is
one particular document role. Being cited does not turn a plan into verification
evidence or a conversation into an approved decision.

**Stored in** identifies the collection separately. A verification report stored
in ControlWork's sources collection remains a verification document when its
name or title provides that specific clue. A structured work-session log is a
**Work session record**, not automatically a chat transcript.

Cards and the original reader show the role, its purpose and **Why this type?**.
Explicit archive kinds are marked recorded. Filename conventions are identified;
inferences from names, titles and directories are marked suggested. This bounded
classifier does not perform an AI semantic review or infer progress and approval.
Unknown author labels, such as numbered increments, are preserved without a
fabricated explanation. Historical citations do not borrow a newer source's title
to classify the old document.

Open a source card's **Confirm or change category** to select its function and
give a reason. ControlCoding saves this choice in private archive metadata for
that exact observed source version; it does not edit, rename or move the original.
The saved category applies consistently to filters, counts and wiki source cards.
**Use automatic assignment** removes the saved choice. Structured chat and session
records retain their archive kind.

After a source refresh detects changed content, the old confirmation becomes
invalid and a visible notice requests review; the current automatic suggestion
is shown meanwhile. Concurrent changes reject a stale form. Classification
metadata is included in archive backup, validated during restore and removed
when its conversation is forgotten. Category confirmation is not approval of
the document's claims or evidence.

**Full original document** means the complete author's Markdown, formatted for
reading. The wiki card contains a short excerpt. Neither view is a generated
explanation of its technical terminology. Citation line numbers are available in
the reader's expandable provenance details.

Instruction classification does not change ingestion permissions. `AGENTS.md`
remains excluded by the existing memory source policy, and no instruction file is
executed by this view. An empty instruction category does not establish that the
project contains no agent instruction files.

### Open a cited original

In **ControlWork → Memory, wiki & conversations → Wiki**, select a topic.
The introduction explains what the topic covers. Source cards show the document
title, its exact path and a short quoted passage. These automatic excerpts are
an index of your selected sources, not an AI explanation or proof that planned
work has been implemented. Saved AI drafts and human-reviewed sections are
identified separately.

Choose **Open original document** on a source card. The same screen opens a
read-only side panel with the complete Markdown file, white paper formatting,
local images and a collapsible **Contents** index. Relative links to other indexed
Markdown documents open in that panel, including documents outside the current
catalog window. Missing or unselected links explain why they cannot open; they do
not silently add files to the authorized scope. External links are labeled and
do not launch a browser. Other source formats expose their indexed text instead
of claiming to display the complete original file.

**Source S1**, for example, labels a quoted passage. Its line numbers locate the
passage in the source text. **Why this source appears and version details** explains
the classification and contains the SHA-256 version fingerprint. The fingerprint
detects source changes; it is not a password or a task identifier. The full file
must match the cited version before it is displayed. If it changed, refresh the
sources and select the current wiki page; a historical citation never silently
opens newer content as though it were the old version. Historical excerpts, human
notes and accepted review text remain available under the existing retention rules.
