# Reviewed knowledge wiki

Opening reviewed wiki for the first time creates eleven project pages: overview, workflows, timeline, glossary, open questions, architecture/as-built, concepts, requirements, decisions, sources and outputs/evidence. Later source refreshes update them. Until this opt-in, existing topic pages and counts are unchanged. Each page begins with a bounded, deterministic list of quoted source lines. The compiler classifies lines by visible text rules; it does not infer a project's history or resolve conflicting statements. Entries carry original source paths, line numbers, and revisions. Wiki pages never enter the original-source retrieval index.

Editors can add or replace a human-reviewed section through a proposal. A proposal names one page and section key, contains a title and Markdown body, cites one to eight current source anchors, and binds to the current page revision. Accepting it records the decision and renders the approved text after generated entries. Rejecting it retains the decision without changing the page. A proposal becomes ineligible for acceptance if its page or cited sources change; the editor must submit a new proposal against the current revision.

Refresh never rewrites an approved human section. If a cited source changes or disappears, the section remains visible with a review warning and the page is stale. A human must compare the new evidence and explicitly propose a replacement. The source lint checks revision and anchor binding, not whether the prose is true or complete. Disagreements and contradictions are therefore review tasks, not automatic merge decisions.

The current and historical rendered Markdown pages reside in the local `wiki` and `wiki_history` records. Section ownership and the bounded proposal/decision log reside in local knowledge metadata and are included by the existing backup format. Forgetting a retained conversation removes reviewed content and proposal records tied to that transcript, as well as its derived wiki history. Exported copies outside the archive are the operator's responsibility.

The combined approved-section and proposal record is limited to 384 KiB. Restore checks this limit in a temporary candidate before creating the target archive. An oversized backup is rejected with `wiki_review_budget`; its original file is unchanged. If an existing archive contains oversized review state, source reconciliation fails atomically with the same error and retains the prior generation and review records. This version does not migrate, truncate, or repair oversized review state; such an archive requires a separately reviewed migration path.

The service actions are `wiki-review-view`, `wiki-review-propose`, and `wiki-review-decide`. Proposal input uses `page_type`, `key`, `title`, `body`, `citations`, and `base_revision`. Each citation supplies `source`, `path`, `revision`, `line`, `end_line`, and `excerpt`; the body cites them as `[S1]` through `[S8]`. A decision supplies the proposal `id` and `decision` (`accept` or `reject`). `wiki-review-view` returns the eleven page identities, current revisions, approved sections with freshness issues, and the retained proposal log.

The pages are an operator aid and exportable record of reviewed prose. They do not certify source entailment, broad AI synthesis, or independent evidence quality.

Each generated page previews at most 32 source excerpts; use the source library
for the complete authorized inventory. Review storage currently allows 20
approved sections per page and 100 retained proposals, with a combined 384 KiB
content budget. Exceeding a budget rejects the change without partial approval.
These are explicit limits, not a claim of unbounded wiki synthesis or history.

Graph-guided retrieval, synthesis adoption, findings, reverse dependencies, revision comparison and recovery are described in [Graph retrieval and reviewed project wiki](graph-wiki.md).
