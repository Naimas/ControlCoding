# Memory Graph Contract

Status: G1 contract baseline.

Contract version: `memory-graph-contract/v1`.

This document defines the shared memory graph contract for ControlCoding and
the later portable ControlWork subset. It is a schema and compatibility
contract. It is not a claim that every GraphRAG command, retriever, viewer, or
ControlWork port is implemented.

## Scope

The contract describes:

- graph node types
- graph edge types
- lifecycle states
- confidence values
- provenance fields
- JSON shapes for portable interchange
- aliases from the current ControlCoding SQLite schema

The current ControlCoding SQLite store remains compatible. G1 does not require
a destructive migration, table rewrite, or local memory reset.

## Ownership Planes

Every graph record must declare exactly one ownership plane.

| Plane | Owner | Current Storage | Rule |
|---|---|---|---|
| `controlcoding_dev` | ControlCoding development workflow | `.controlcoding/memory/memory.db` | May include coding decisions, source files, consults, agent runs, verification evidence, and generated Dev Plane views. |
| `controlwork_project` | Project owner through ControlWork | `.controlwork/` and `CONTROLWORK.md` | Portable work/project memory. It must remain usable without ControlCoding. |
| `application_memory` | The application being built | Application-defined | May be referenced as project context, but ControlCoding and ControlWork do not own runtime truth. |

Derived views, vector rows, graph exports, RAG packets, and viewer data must
declare `canonical: false` in provenance or export metadata.

## Contract Identity

Portable JSON records use these top-level fields:

```json
{
  "contractVersion": "memory-graph-contract/v1",
  "recordKind": "node",
  "id": "CC_Project_DEV_DOC_MemoryPlan_20260505",
  "plane": "controlcoding_dev"
}
```

Storage schemas may use different column names. Exported records should keep
the contract field names below.

## Node Types

### Core Portable Node Types

These node types are shared by ControlCoding and the later ControlWork port.

| Type | Meaning |
|---|---|
| `document` | Whole Markdown, text, or source-backed document. |
| `chunk` | Semantic section, paragraph group, table block, schema block, or continuation part from a source document. |
| `decision` | Accepted choice with rationale or reviewed status. |
| `plan` | Intended work, roadmap, workflow, or implementation sequence. |
| `source` | Original source material, source summary, imported reference, or research input. |
| `note` | Useful observation that is not yet a decision. |
| `idea` | Option, possibility, or brainstorm item. |
| `requirement` | Required behavior, constraint, acceptance condition, or obligation. |
| `question` | Open issue or unresolved uncertainty. |
| `claim` | Factual statement that may need evidence or review. |
| `evidence` | Receipt, test output, audit finding, source excerpt, benchmark, or proof record. |
| `module` | Product, work, or code subsystem. |
| `file` | Concrete repository file or project file. |
| `consult` | External, specialist, or manual review outcome. |
| `agent_run` | AI, host, or tool run with task, output, changes, and verification metadata. |
| `work_output` | Deliverable, report, draft, final artifact, or exported work product. |
| `session` | Explicit chat or work session record used for continuity and traceability. |

### Structural Subtypes

Structural subtypes are node metadata first. They become first-class graph nodes
only when the implementation stores them as separate records.

| Structural subtype | Meaning |
|---|---|
| `document` | Whole source document. |
| `chapter` | Large division exposed by the source. |
| `section` | Heading-level division. |
| `subsection` | Nested heading-level division. |
| `paragraph` | Atomic prose block. |
| `table` | Tabular block kept with headers and local notes. |
| `schema` | Data model, interface contract, diagram, or structured specification. |
| `page` | Page origin when the source exposes stable page markers. |
| `title` | Main document title. |
| `subtitle` | Secondary title or heading. |
| `annotation` | Comment, reviewer note, marginal note, or extracted annotation. |
| `topic` | Broad subject label. |
| `subtopic` | Specific subject under a topic. |
| `domain` | Knowledge domain such as software, legal, compliance, finance, research, product, or operations. |

### ControlCoding Extension Node Types

The current ControlCoding store contains practical entity types that map into
the portable contract without migration.

| Current entity type | ID code | Contract node type | Notes |
|---|---|---|---|
| `agent_run` | `AGENTRUN` | `agent_run` | ControlCoding-only in Dev Plane until a portable work-run equivalent exists. |
| `application_memory_component` | `MEMCOMP` | `source` | Must keep `plane: application_memory` and must not be treated as ControlCoding-owned runtime truth. |
| `benchmark` | `BENCH` | `evidence` | Benchmark evidence or scorecard. |
| `chunk_node` | `CHUNK` | `chunk` | Compatibility alias for future extracted chunk entities. Current chunks live in `semantic_chunks`. |
| `consult` | `CONSULT` | `consult` | Consults inform memory but do not own canonical decisions. |
| `decision` | `DECISION` | `decision` | High-authority memory when active or verified. |
| `design` | `DESIGN` | `document` | Use `semanticRole: design` or `documentType: design`. |
| `doc_node` | `DOC` | `document` | General path-backed document entity. |
| `file_node` | `FILE` | `file` | Code or project file. |
| `handoff` | `HANDOFF` | `work_output` | Handoff packet or handoff document. |
| `idea` | `IDEA` | `idea` | Lower authority than decisions and active plans. |
| `note` | `NOTE` | `note` | Observation or summary. |
| `plan` | `PLAN` | `plan` | Roadmap, plan, workflow, or intended work. |
| `research_note` | `RESEARCH` | `source` | Source summary or research input. |
| `session` | `SESSION` | `session` | Explicit Session GraphRAG record. Stored in `session_records` in the current implementation. |
| `test_node` | `TEST` | `evidence` | Test file, test evidence, or verification artifact. |
| `work_item` | `TASK` | `plan` | Task or work item. Use `semanticRole: task` when exported. |

## Edge Types

### Core Portable Edge Types

| Type | Meaning |
|---|---|
| `contains` | Parent document, entry, or source contains a child chunk or extracted node. |
| `references` | Source record explicitly references a target record. |
| `mentions` | Source record names a concept without asserting a stronger relation. |
| `supports` | Source, evidence, or note supports a claim, decision, or requirement. |
| `questions` | Question challenges or asks about a claim, plan, requirement, or decision. |
| `answers` | Decision, source, or note answers an open question. |
| `implements` | File, module, plan, or output implements a requirement or decision. |
| `depends_on` | Source record requires another record to remain valid. |
| `derived_from` | Summary, note, chunk, output, or packet derives from source material. |
| `supersedes` | Newer record replaces an older record. |
| `superseded_by` | Older record points to its replacement. |
| `conflicts_with` | Records cannot both be active or true in the same context. |
| `evidence_for` | Evidence specifically supports a claim, requirement, or decision. |
| `next_chunk` | Chunk points to the following chunk in source order. |
| `previous_chunk` | Chunk points to the previous chunk in source order. |
| `continues_to` | First part of an oversized semantic unit points to a continuation chunk. |
| `continues_from` | Continuation chunk points back to the first chunk of the semantic unit. |
| `same_section_as` | Split chunks belong to the same heading section or structural unit. |

### Current ControlCoding Edge Compatibility

| Current edge or suggestion type | Contract mapping | Notes |
|---|---|---|
| `contains` | `contains` | Already canonical. |
| `references` | `references` | Explicit links are also promoted as canonical edges today. |
| `next_chunk` | `next_chunk` | Already canonical. |
| `previous_chunk` | `previous_chunk` | Already canonical. |
| `continues_to` | `continues_to` | Already canonical. |
| `continues_from` | `continues_from` | Already canonical. |
| `same_section_as` | `same_section_as` | Already canonical. |
| `supersedes` | `supersedes` | Already canonical from lifecycle workflow. |
| `superseded_by` | `superseded_by` | Already canonical from lifecycle workflow. |
| `conflicts_with` | `conflicts_with` | Already canonical from lifecycle workflow. |
| `related_heading` | `mentions` | Current suggestion alias. Keep `inferenceKind: related_heading`. |
| `related_terms` | `mentions` | Current suggestion alias. Keep `inferenceKind: related_terms`. |
| `continues_session` | `continues_from` | Session GraphRAG edge. |
| `references_session` | `references` | Session GraphRAG edge. |
| `changes_file` | `implements` | Session GraphRAG evidence link to a changed file. |
| `changes_doc` | `references` | Session GraphRAG evidence link to a changed document. |
| `uses_command` | `references` | Session GraphRAG command link. |
| `verified_by` | `evidence_for` | Session GraphRAG test or verification link. |
| `produced_commit` | `evidence_for` | Session GraphRAG commit link. |
| `produced_packet` | `derived_from` | Session GraphRAG packet link. |
| `references_decision` | `references` | Session GraphRAG decision link. |
| `left_followup` | `questions` | Session GraphRAG unresolved follow-up link. |
| `belongs_to_category` | `mentions` | Session GraphRAG categorization link. |
| `uses_rag_o` | `references` | Session GraphRAG link to a RAG-O status or action queue. |
| `uses_dev_graphrag` | `references` | Session GraphRAG link to Dev GraphRAG retrieval or packet context. |
| `uses_work_graphrag` | `references` | Session GraphRAG link to Work GraphRAG context. |

Suggestions are not canonical edges unless accepted by an explicit workflow or
created by a deterministic rule such as an explicit file reference.

## Confidence

Confidence is a named value plus an optional numeric score. The named value is
required because it remains readable in SQLite, JSON, Markdown views, and audit
events.

| Confidence | Band | Meaning |
|---|---:|---|
| `explicit_link` | 0.95 | The source text directly links or names a concrete target path or ID. |
| `human_confirmed_relation` | 0.90 | A human accepted or recorded the relation. |
| `exact_id_or_path_match` | 0.85 | Deterministic match on a stable ID or normalized path. |
| `strong_title_or_heading_match` | 0.70 | Strong deterministic title or heading overlap. |
| `weak_lexical_similarity` | 0.35 | Weak keyword or lexical overlap. |
| `derived_sequence` | 0.80 | Deterministic chunk sequence or continuation edge. |
| `system_lifecycle` | 0.90 | Lifecycle edge written by an explicit lifecycle command. |
| `unknown` | 0.00 | Legacy or incomplete record where confidence was not recorded. |

Accepting a suggestion should preserve the original confidence and add
`reviewStatus: accepted` plus `reviewConfidence: human_confirmed_relation`.
Rejecting a suggestion should preserve the row as audit evidence with
`reviewStatus: rejected`.

## Lifecycle

### Core Portable Lifecycle States

| State | Meaning |
|---|---|
| `captured` | Stored but not reviewed. |
| `draft` | Being shaped, not accepted as current truth. |
| `active` | Current and usable. |
| `needs_review` | Useful but uncertain, incomplete, stale, or requiring human review. |
| `stale` | Likely outdated but retained for traceability. |
| `conflicting` | Conflicts with another active or relevant record. |
| `superseded` | Replaced by newer material. |
| `legacy` | Kept for history, not current working truth. |
| `archived` | Deliberately archived. |
| `rejected` | Reviewed and rejected, retained for auditability. |

### ControlCoding Extended Lifecycle States

| State | Portable mapping | Meaning |
|---|---|---|
| `triaged` | `active` with `reviewStage: triaged` | Reviewed enough to keep in active working context. |
| `implemented` | `active` with `implementationStatus: implemented` | Implemented in code or project artifacts. |
| `verified` | `active` with `verificationStatus: verified` | Verified by tests, review, or receipts. |
| `merged` | `archived` with `mergeStatus: merged` | Duplicates or branches merged into another record. |

Lifecycle transitions must be audited through events. They must not delete the
old record, source path, or suggestion row.

## Provenance

Every node, edge, suggestion, derived index entry, and packet selection should
carry provenance.

Required provenance fields:

| Field | Meaning |
|---|---|
| `sourceKind` | `path`, `manual`, `cli`, `import`, `derived`, `adapter`, or `application_reference`. |
| `sourceRef` | Path, source ID, external reference, or application-owned adapter reference. |
| `sourcePath` | Project-relative source path when available. |
| `command` | CLI command or explicit workflow that wrote the record. |
| `actorType` | `human`, `cli`, `host`, `agent`, `adapter`, or `system`. |
| `canonical` | Boolean. False for views, vector rows, graph exports, and packets. |
| `absorbsContent` | Boolean. False when ControlCoding only references external or application-owned material. |
| `createdAt` | ISO 8601 timestamp. |
| `updatedAt` | ISO 8601 timestamp when mutable. |

Optional provenance fields:

| Field | Meaning |
|---|---|
| `evidenceRefs` | Supporting source IDs, paths, receipts, or event IDs. |
| `eventIds` | Audit events that created or changed the record. |
| `adapter` | Local sparse, local runtime, or explicitly configured official API adapter. |
| `model` | Model or runtime identifier when an optional semantic adapter is used. |
| `reviewedBy` | Human or workflow identity for accepted or rejected suggestions. |
| `reviewReason` | Reason supplied during accept, reject, lifecycle, or override action. |

## JSON Shapes

### Node

```json
{
  "contractVersion": "memory-graph-contract/v1",
  "recordKind": "node",
  "id": "CC_Demo_DEV_DOC_DocsPlan_20260505",
  "type": "document",
  "aliases": {
    "controlcodingEntityType": "doc_node",
    "controlcodingIdCode": "DOC"
  },
  "plane": "controlcoding_dev",
  "title": "Docs Plan",
  "path": "docs/plan.md",
  "lifecycle": "active",
  "sourceRefs": ["SRC_1234ABCD"],
  "provenance": {
    "sourceKind": "path",
    "sourceRef": "docs/plan.md",
    "sourcePath": "docs/plan.md",
    "command": "cc memory scan",
    "actorType": "cli",
    "canonical": true,
    "absorbsContent": false,
    "createdAt": "2026-05-05T10:00:00Z",
    "updatedAt": "2026-05-05T10:00:00Z"
  },
  "data": {
    "documentType": "plan",
    "documentFacets": ["active"],
    "semanticRole": "plan",
    "topics": ["memory"],
    "domains": ["software"]
  }
}
```

### Chunk Node Or Chunk Projection

Current ControlCoding stores chunks in `semantic_chunks`. A portable export may
emit them as `recordKind: node` with `type: chunk`.

```json
{
  "contractVersion": "memory-graph-contract/v1",
  "recordKind": "node",
  "id": "CHUNK_ABCDEF123456",
  "type": "chunk",
  "plane": "controlcoding_dev",
  "documentId": "CC_Demo_DEV_DOC_DocsPlan_20260505",
  "sourcePath": "docs/plan.md",
  "sourceTitle": "Docs Plan",
  "headingPath": "Docs Plan > Implementation",
  "headingLevel": 2,
  "structuralLevel": "section",
  "blockType": "prose",
  "semanticRole": "rationale",
  "topics": ["memory"],
  "subtopics": ["graph contract"],
  "domains": ["software"],
  "ordinal": 2,
  "sequenceIndex": 1,
  "sourceLocation": {
    "lineStart": null,
    "lineEnd": null,
    "page": null
  },
  "parentChunkId": "",
  "previousChunkId": "",
  "nextChunkId": "CHUNK_NEXT123",
  "contentHash": "sha256...",
  "summary": "Define the portable graph contract.",
  "keywords": ["memory", "graph", "contract"],
  "extractionConfidence": "explicit_link",
  "contextPolicy": "include_parent_heading",
  "lifecycle": "active"
}
```

### Edge

```json
{
  "contractVersion": "memory-graph-contract/v1",
  "recordKind": "edge",
  "id": "EDGE_ABCDEF123456",
  "sourceId": "CC_Demo_DEV_DOC_DocsPlan_20260505",
  "targetId": "CHUNK_ABCDEF123456",
  "type": "contains",
  "confidence": "derived_sequence",
  "status": "canonical",
  "lifecycle": "active",
  "reason": "Document contains semantic chunk from heading-aware scan.",
  "provenance": {
    "sourceKind": "derived",
    "sourceRef": "semantic_chunks",
    "sourcePath": "docs/plan.md",
    "command": "cc memory scan",
    "actorType": "cli",
    "canonical": true,
    "absorbsContent": false,
    "createdAt": "2026-05-05T10:00:00Z",
    "updatedAt": "2026-05-05T10:00:00Z"
  },
  "data": {
    "relationStrength": "strong",
    "contextPolicy": "include_parent_heading"
  }
}
```

### Suggestion

```json
{
  "contractVersion": "memory-graph-contract/v1",
  "recordKind": "suggestion",
  "id": "CORR_ABCDEF123456",
  "sourceId": "CC_Demo_DEV_DOC_DocsPlan_20260505",
  "targetId": "CC_Demo_DEV_DECISION_MemoryLocal_20260505",
  "type": "mentions",
  "aliases": {
    "controlcodingSuggestionType": "related_heading"
  },
  "confidence": "strong_title_or_heading_match",
  "status": "suggested",
  "reason": "shared heading: implementation",
  "provenance": {
    "sourceKind": "derived",
    "sourceRef": "correlation_suggestions",
    "command": "cc memory scan",
    "actorType": "cli",
    "canonical": true,
    "absorbsContent": false,
    "createdAt": "2026-05-05T10:00:00Z",
    "updatedAt": "2026-05-05T10:00:00Z"
  },
  "review": {
    "reviewStatus": "unreviewed",
    "reviewedBy": "",
    "reviewedAt": "",
    "reviewReason": ""
  }
}
```

Accepted and rejected suggestions remain audit records. A refresh may update
their `updatedAt` or evidence fields, but it must not delete them or recreate
them as new unreviewed suggestions.

## SQLite Compatibility Rules

The current SQLite schema is contract-compatible with these rules:

- `entities.type` maps through the alias table above.
- `entities.plane` maps directly when it is `controlcoding_dev` or
  `application_memory`.
- `semantic_chunks` exports as `chunk` nodes or chunk projections.
- `edges.type` maps directly for implemented edge types.
- `correlation_suggestions.type` maps through the suggestion alias table.
- `correlation_suggestions.status` remains review state, not lifecycle.
- `derived_vector_index` is derived retrieval state with `canonical: false`.
- `views` are derived presentation state with `canonical: false`.
- `events` are the audit ledger for lifecycle, review, scan, rebuild, and view
  generation actions.

No G1 behavior may require dropping current tables, rewriting IDs, or deleting
accepted or rejected suggestions.

## Portability Rules

Portable to ControlWork after ControlCoding stabilization:

- graph contract constants
- lifecycle core
- document and work-entry nodes
- chunk nodes and structural metadata
- source, note, idea, decision, plan, requirement, claim, question, evidence,
  and work output nodes
- suggestion governance
- retrieval reasons
- GraphRAG packet citations

ControlCoding-only unless explicitly reclassified:

- source-code impact analysis
- hook receipts
- verification receipts
- agent-run changed-file evidence
- consult records tied to development workflow
- Code Plane module proximity

Application-owned and not portable as ControlCoding or ControlWork truth:

- production user data
- application vector stores
- application embedding stores
- application knowledge graphs
- runtime RAG corpora

## Implementation Status

| Area | Status |
|---|---|
| Contract document | Current. |
| SQLite compatibility mapping | Implemented with compatible runtime mappings. |
| Suggestion accept/reject persistence | Implemented with audited suggestion state. |
| `cc memory graph status` | Implemented. |
| Dev GraphRAG retrieval | Implemented for the ControlCoding Dev Plane. |
| Dev GraphRAG packet builder | Implemented for the ControlCoding Dev Plane. |
| RAG-O operations index | Implemented as `cc memory op-index`; read-only coordination, not retrieval or generation. |
| ControlWork port | Implemented for the portable Project Plane subset. |

## Work Plane Command Parity

Portable ControlWork graph, session, retrieval, scan review, source import, OCR
sidecar import, and RAG packet behavior is available standalone through `cw.py`
and embedded through `cc memory work-*` commands. The embedded gate includes
`work-review --review-status ready_to_promote`, readiness blockers for
duplicates, conflicts, version ambiguity, changed sources, and OCR sidecar
problems, plus `work-promote --force-note` for audited overrides.

Project Plane scan review states such as `needs_human`, `duplicate`,
`conflict`, `superseded_candidate`, and `ready_to_promote` are review-gate
metadata, not canonical graph edges. They can inform retrieval warnings, graph
explanations, and future review dashboards only after the evidence is kept
distinct from promoted memory.
