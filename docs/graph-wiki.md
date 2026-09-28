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
canonical Work/Dev links, current approved work relations and reviewed wiki
findings. Both endpoints must remain available and in scope. Stale or proposed
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
