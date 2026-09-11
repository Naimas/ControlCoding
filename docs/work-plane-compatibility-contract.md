# Work Plane Compatibility Contract

Contract ID: `controlwork-work-plane`

Contract version: `1.0.0`

Contract identity: `controlwork-work-plane/1.0.0`

Canonical authority: ControlWork repository,
`docs/work-plane-compatibility-contract.md`

Profile: `ControlCoding embedded conformance profile`

Status: `non-canonical`

Technical effective date: `2026-09-02`

ControlWork standalone and the ControlCoding embedded Project Plane are two
distribution forms of the same Work Plane contract.

This document is the ControlCoding embedded conformance profile. It is not the
canonical contract source. The release manifest pins the exact canonical
identity `controlwork-work-plane/1.0.0`, independently of the ControlCoding
product version, `schemaVersion`, `sharedMemoryContractVersion`, package
versions, and Git identities.

## Shared Contract

The shared contract is `CONTROLWORK.md` plus `.controlwork/`. Shared features
must preserve file layout, lifecycle names, category registry behavior,
checkpoint receipts, context packets, session records, graph suggestions,
retrieval semantics, RAG packet structure, wiki/Obsidian projection rules, and
read-only MCP inspection behavior.

## Command Parity

Standalone ControlWork exposes the shared contract through `cw.py`.
ControlCoding exposes the embedded form through `cc memory work-*` commands.

| Shared feature | Standalone command | Embedded ControlCoding command |
|---|---|---|
| Initialize Work Plane | `cw.py init` | `cc memory work-init` |
| Status and drift | `cw.py status` | `cc memory work-status` |
| Safe first-pass setup | `cw.py quickstart` | `cc memory work-quickstart` |
| File scan index | `cw.py scan` | `cc memory work-scan` |
| Scan analysis | `cw.py scan-analyze` | `cc memory work-analyze` |
| Scan review gate and readiness blockers | `cw.py scan-review --review-status ready_to_promote` | `cc memory work-review --review-status ready_to_promote` |
| Review queue proposals and batch status | `cw.py scan-review --proposal`, `cw.py scan-review --batch` | `cc memory work-review --proposal`, `cc memory work-review --batch` |
| Rich source import to needs-review memory | `cw.py scan-import` | `cc memory work-import-source` |
| OCR sidecar import | `cw.py ocr status`, `cw.py ocr run`, `cw.py ocr import-sidecar` | `cc memory work-ocr status`, `cc memory work-ocr run`, `cc memory work-ocr import-sidecar` |
| Reviewed promotion with override note | `cw.py scan-promote --force-note ...` | `cc memory work-promote --force-note ...` |
| Capture memory | `cw.py capture` | `cc memory work-capture` |
| Categories | `cw.py category` | `cc memory work-category` |
| Views | `cw.py views generate` | `cc memory work-views` |
| Dashboard and Project Map contract | `cw.py dashboard --format html|md|json` | `cc memory work-dashboard --format html|md|json` |
| Checkpoints | `cw.py checkpoint` | `cc memory work-checkpoint` |
| Handoff | `cw.py handoff` | `cc memory work-handoff` |
| Context packet | `cw.py context-pack` | `cc memory work-context-pack` |
| Session continuity | `cw.py session` | `cc memory work-session` |
| Portable graph | `cw.py graph` | `cc memory work-graph` |
| Graph explain/path | `cw.py graph explain`, `cw.py graph path` | `cc memory work-graph explain`, `cc memory work-graph path` |
| Query-first retrieval | `cw.py query` | `cc memory work-query` |
| Retrieval | `cw.py retrieve` | `cc memory work-retrieve` |
| RAG packet | `cw.py rag-pack` | `cc memory work-rag-pack` |
| Obsidian/wiki projection | `cw.py obsidian`, `cw.py wiki` | `cc memory work-obsidian`, `cc memory work-wiki` |
| MCP inspection | `cw.py mcp` | `cc memory work-mcp` |

## Profile-Specific Extensions

The following capabilities are ControlCoding embedded-only extensions. They
are not shared portable requirements:

- Dev Plane
- Code Plane
- code impact
- developer graph routing
- coding agents and agent runs
- verification receipts
- hook and boundary enforcement
- release doctor integration
- code-specific GraphRAG
- `work-attach`
- `work-import`
- `work-export`
- `work-sync`
- `work-parity`

The following capabilities are ControlWork standalone-only extensions. They
are not shared portable requirements:

- ControlWork product documentation maintenance
- `install-guide`
- `maintain` command and maintenance carousel
- standalone host adapter maintenance

Profile-specific extensions do not become shared requirements merely because
they integrate with the Work Plane.

## Change Rule

When a feature manipulates `CONTROLWORK.md` or `.controlwork/` in a way that is
useful outside a code repository, implement or port it in both forms. If a
feature is coding-specific, keep it in ControlCoding and document why it is not
portable.

Shared features should land with parity tests so the two command surfaces do
not drift silently.

Normative changes are versioned by the canonical ControlWork contract. This
embedded profile may retain the same contract version for non-normative
editorial changes.
