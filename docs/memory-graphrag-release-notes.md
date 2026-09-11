# Memory GraphRAG Release Notes

Status: G0 through G10 hardening notes, G11 Session GraphRAG notes, and the
current document-layout, semantic runtime, and cross-plane packet increments
for the ControlCoding laboratory build and the portable ControlWork subset.

This is not a claim that an autonomous cross-plane answer engine is complete.
The current release hardens the implemented ControlCoding Dev Plane features,
the portable ControlWork Project Plane subset, and the read-only federated
packet builder.

## Implemented In ControlCoding

- Shared graph contract documentation and runtime constants.
- Suggestion persistence for accepted and rejected correlation suggestions.
- Graph CLI for status, suggestions, accept, reject, around, and derived export.
- Structural chunk metadata for documents, headings, tables, schemas,
  annotations, topics, domains, semantic roles, relation strength, and context
  policy.
- Rebuildable first-class document-layout nodes and edges for document, title,
  chapter, section, subsection, paragraph, table, table cell, schema, schema
  field, page, annotation, figure, image, caption, footnote, and margin note
  through `cc memory layout`.
- G14.1 explicit PDF/OCR layout sidecar ingestion through `.layout.json`,
  `.ocr.json`, `.pdf.layout.json`, and `.pdf.ocr.json`, preserving page
  coordinates, normalized bounding boxes, source document path, extraction
  method, block ids, caption or footnote targets, and OCR confidence.
- G14.1 nested sidecar layout ingestion for page-level `tables`, `figures`,
  `images`, `captions`, `footnotes`, `annotations`, and `marginNotes`
  collections, including structured table cells and schema fields.
- Bounded visual relation edges from sidecar bounding boxes: nearest above,
  below, left, right, and overlapping blocks on the same page.
- Basic stdlib-only text extraction from simple unencrypted PDFs with readable
  content streams during `cc memory scan`.
- Explicit local OCR/layout adapter execution through `cc memory ocr status`
  and `cc memory ocr run`, disabled unless `ocr_adapters.json` selects a local
  runtime command.
- Memory Bootstrap status across Dev Plane, Project Plane, derived artifacts,
  and application-owned memory references.
- RAG-O through `cc memory op-index` for action queue, RAG routing, packet
  pointers, Project Plane drift, and commit hygiene. RAG-O is a read-only
  operations index, not a retrieval or generation engine.
- Dev GraphRAG retrieval with text, local sparse vector, graph proximity, lifecycle,
  confidence, recency, source trust, exclusions, and demotions.
- Dev GraphRAG packet builder with Markdown and JSON output, citations, edges
  used, warnings, excluded records, and next reads.
- Cross-plane GraphRAG packet builder through `cc memory cross-pack`, which
  federates Dev GraphRAG, Project Plane ControlWork context, Session GraphRAG,
  and RAG-O routes without merging sources of truth.
- Optional semantic adapter status interface with local sparse default, optional
  local runtime configuration, and explicit official API configuration.
- Optional OCR adapter status and run interface with explicit local runtime
  configuration.
- G14.2 OCR adapter hardening: command executability checks, accepted source
  type reporting, source hash and size metadata in adapter requests, sourcePath
  validation on adapter output, and `--force` protection before sidecar
  overwrite.
- Explicit local runtime semantic scoring for `cc memory retrieve` when
  `semantic_adapters.json` selects `local_runtime_v1`.
- Derived graph export and lightweight HTML viewer.
- Project Plane drift awareness through `cc memory work-status`.
- Session GraphRAG storage, CLI, views, RAG-O status integration, Dev GraphRAG
  retrieval as lower-trust traceability evidence, and session packets through
  `cc memory session`, `cc memory session-pack`, and `cc memory retrieve`.
- Host startup protocol through `cc memory startup`, which prints a visible
  read-only checklist for new chats and cannot force hidden command execution.
- G11.9 release hardening documentation, verification command list, and clean
  release boundary notes.

## Ported To ControlWork

- Portable graph contract subset.
- File-derived Project Plane graph over `CONTROLWORK.md` and
  `.controlwork/memory/`.
- Deterministic Markdown chunking.
- Deterministic topic suggestions.
- Work GraphRAG retrieve.
- Work GraphRAG rag-pack.
- Portable Session GraphRAG records under `.controlwork/sessions/`, integrated
  into ControlWork graph, retrieve, and rag-pack.
- Portable graph suggestion accept/reject persistence under
  `.controlwork/graph-suggestions.json`.
- Portable derived graph export/viewer through standalone ControlWork
  `cw.py graph export`.

The ControlWork port intentionally excludes ControlCoding-only features:

- code impact
- hook receipts
- agent runs
- verification receipts
- Dev Plane SQLite state

## Rebuild And Migration Commands

ControlCoding Dev Plane:

```bash
python scripts/cc.py memory scan --project-root .
python scripts/cc.py memory vector rebuild --project-root .
python scripts/cc.py memory views generate --project-root .
python scripts/cc.py memory graph status --project-root .
python scripts/cc.py memory bootstrap --project-root . --scope dev --topic "GraphRAG"
python scripts/cc.py memory startup --project-root . --scope dev --topic "GraphRAG"
python scripts/cc.py memory op-index --project-root . --scope dev --topic "GraphRAG"
python scripts/cc.py memory cross-pack --project-root . "GraphRAG" --limit 10
```

Standalone ControlWork checkout only:

```bash
python scripts/cw.py graph status --project-root .
python scripts/cw.py graph suggestions --project-root .
python scripts/cw.py retrieve "project topic" --project-root .
python scripts/cw.py rag-pack "project topic" --project-root .
python scripts/cw.py views generate --project-root .
```

Run those commands from the root of the standalone ControlWork repository.
`scripts/cw.py` is not included in ControlCoding V1. For the five operations
above, the verified embedded ControlCoding equivalents are:

```bash
python scripts/cc.py memory work-graph status --project-root .
python scripts/cc.py memory work-graph suggestions --project-root .
python scripts/cc.py memory work-retrieve "project topic" --project-root .
python scripts/cc.py memory work-rag-pack "project topic" --project-root .
python scripts/cc.py memory work-views --project-root .
```

Run the embedded equivalents from the root of a ControlCoding checkout. The
embedded command names are not evidence that every standalone-only ControlWork
operation has a ControlCoding equivalent.

Bridge and drift checks:

```bash
python scripts/cc.py memory work-status --project-root .
python scripts/cc.py memory work-views --project-root .
python scripts/cc.py memory work-context-pack --project-root . --scope general --topic "current focus"
```

`work-status` reports both raw fingerprint drift and semantic actionable drift.
Distribution-only differences between embedded ControlCoding Project Plane text
and standalone ControlWork product text stay audit-visible but do not create
sync requests when the shared contract, categories, and memory content match.

## Privacy And Local Data

- ControlCoding memory state stays local under `.controlcoding/`.
- ControlWork Project Plane state stays local under `.controlwork/` and
  `CONTROLWORK.md`.
- Derived exports, views, packets, wiki files, and vector rows are projections.
- Application-owned memory remains owned by the application.
- Optional official API semantic adapters are disabled unless explicitly
  configured with official API credentials.
- Consumer login reuse and token reuse are not supported.
- Do not place secrets, personal data, customer data, or confidential material
  in memory unless the project owner has approved that storage.

## Known Limits

- Cross-plane GraphRAG is implemented as a federated read-only packet builder,
  not as an autonomous answer generation engine.
- Robust native PDF layout parsing and built-in OCR are not implemented.
  PDF/OCR page coordinates and bounding boxes are supported only when supplied
  through an explicit project-owned layout sidecar. Simple unencrypted text
  PDFs can be scanned directly as best-effort text sources. OCR execution is
  available only through an explicitly configured local runtime adapter.
- ControlWork retrieval is file-derived and local; it does not use SQLite.
- ControlWork graph export/viewer is a lightweight derived projection, not full
  ControlCoding viewer parity.
- ControlWork session records are file-based and portable; they do not include
  ControlCoding-only code impact, hooks, agent runs, or verification receipts.
- Session startup protocol is advisory and visible. Host context files cannot
  technically force automatic command execution.
- G11.9 does not change memory runtime behavior. It documents the release
  boundary and verifies the implemented subset.
- Drift awareness reports update requests but does not auto-sync.
- Official API semantic adapters remain explicit-only and are not the default
  retrieval path.

## Verification

Release hardening uses:

- `python -m pytest tests/test_cc_memory.py -q`
- `python -m unittest tests.test_controlwork_release`
- `python scripts/cc.py truth check --project-root . --include-docs`
- `python scripts/cc.py truth check-docs --project-root .`
- `git diff --check`

This source distribution contains tracked implementation, tests, and public
documentation. Local project memory, generated receipts, host configuration,
and runtime state are excluded by `controlcoding.release.json`.
