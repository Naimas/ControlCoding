# Docs Index

## Purpose

This index maps the public and adopter-facing documentation shipped with the
ControlCoding V1/Core source release.

## Read First

1. [Install ControlCoding On Your Project](./install-controlcoding-on-your-project.md)
2. [Quick Start](./quick-start.md)
3. [Release Model](./release-model.md)
4. [System Architecture](./controlcoding-system-architecture.md)
5. [Project Memory Engine](./project-memory-engine.md), when local project
   memory is part of the setup

## Release Scope

[Verification evidence](./verification-evidence.md) defines receipt outcomes,
current-evidence gates, input identity and execution limits.

This source release ships Core implementation, public contracts, tests,
templates, and selected documentation. It does not ship the desktop
Studio/gateway UI, auditor skill packs, raw benchmark workspaces, audit outputs,
or obsolete internal execution plans.

Only the explicitly listed curated benchmark summaries are part of the public
evidence surface. They are evidence with stated limits, not universal product
performance claims.

## Status Labels

| Status | Meaning |
|---|---|
| Current | Current public or adopter-facing guidance. |
| Contract | A behavior, file shape, or compatibility promise backed by implementation or tests. |
| Plan | Public planning material that is not a shipped-feature promise. |
| Reference | Supporting technical or historical material. |

## Current Documents

| File | Status | Purpose |
|---|---|---|
| [panel-ai-roles.md](./panel-ai-roles.md) | Development candidate | Per-project local/API role assignments, policy enforcement, persistence and execution limits. |
| [install-controlcoding-on-your-project.md](./install-controlcoding-on-your-project.md) | Current | Primary installation guide. |
| [quick-start.md](./quick-start.md) | Current | Compact setup and startup path. |
| [release-model.md](./release-model.md) | Current | Exact V1/Core package boundary and exclusions. |
| [controlcoding-system-architecture.md](./controlcoding-system-architecture.md) | Current | Architecture, ownership planes, memory, and verification map. |
| [architecture-index.md](./architecture-index.md) | Contract | Release-visible inventory checked by `cc index --check` and the release doctor. |
| [project-memory-engine.md](./project-memory-engine.md) | Current | Local Project Memory Engine and practical workflows. |
| [controlwork-management.md](./controlwork-management.md) | Development candidate | Portable archive initialization, document import and manual knowledge/session capture with reviewed writes. |
| [controlwork-panel.md](./controlwork-panel.md) | Development candidate | Optional desktop ControlWork views, bounded read scope, session summaries and Core graph retrieval. |
| [memory-system-schema.md](./memory-system-schema.md) | Current | Memory and GraphRAG architecture with shipped and planned states distinguished. |
| [memory-graph-contract.md](./memory-graph-contract.md) | Contract | Portable graph shapes, lifecycle, confidence, and compatibility aliases. |
| [memory-graphrag-release-notes.md](./memory-graphrag-release-notes.md) | Reference | Memory GraphRAG hardening history and limitations. |
| [work-plane-compatibility-contract.md](./work-plane-compatibility-contract.md) | Contract | Embedded and standalone Work Plane command compatibility. |
| [docs-maintenance-plan.md](./docs-maintenance-plan.md) | Reference | Guide to the experimental documentation-maintenance commands. |
| [roadmap-10-10.md](./roadmap-10-10.md) | Reference | Trust and verification mechanisms and limits. |
| [methodology-history.md](./methodology-history.md) | Reference | Public methodology history and current Core boundary. |
| [adoption-guide.md](./adoption-guide.md) | Current | Broader onboarding and worked adoption guidance. |
| [ecosystem-quickstart.md](./ecosystem-quickstart.md) | Current | Authorized integration and optional helper guidance. |
| [cross-tool-guide.md](./cross-tool-guide.md) | Current | Host-aware use through official interfaces. |
| [hooks-reference.md](./hooks-reference.md) | Current | Hook behavior, limits, and exit codes. |
| [file-organization-standard.md](./file-organization-standard.md) | Reference | Documentation naming, lifecycle, and organization rules. |
| [feature-state-machine.md](./feature-state-machine.md) | Reference | Feature lifecycle and transition model. |
| [evidence.md](./evidence.md) | Reference | Supporting methodology evidence and limitations. |

## Secondary Reference Set

| File | Status | Purpose |
|---|---|---|
| [ccdocs/INDEX.md](./ccdocs/INDEX.md) | Reference | Index for the split methodology reference. |
| [ccdocs/SUMMARY.md](./ccdocs/SUMMARY.md) | Reference | Reading order for the split reference. |
| [ccdocs/methodology.md](./ccdocs/methodology.md) | Reference | Detailed methodology and enforcement model. |
| [ccdocs/hooks-reference.md](./ccdocs/hooks-reference.md) | Reference | Detailed hook configuration reference. |
| [ccdocs/tools-reference.md](./ccdocs/tools-reference.md) | Reference | Detailed CLI and tooling reference. |
| [ccdocs/cookbook.md](./ccdocs/cookbook.md) | Reference | Worked examples. |

## Curated Benchmark Summaries

- [R5 release-pair scorecard](../benchmarks/2026-04-27-r5-release-pair-scorecard.md)
- [3D crawler scorecard](../benchmarks/2026-04-11-cctest-3dcrawler-v11-scorecard.md)
- [3D crawler review check](../benchmarks/2026-04-11-cctest-3dcrawler-v11-review-check.md)

Raw benchmark packages, generated local evidence, issue registers, comparison
work notes, and benchmark execution plans are intentionally excluded.

[Desktop execution and providers](./panel-execution.md) describes canonical Core
installation, Checks, advisory AI and opt-in exchange archival.

[Guided panel configuration](./panel-configuration.md) describes versioned drafts,
manual AI proposal review, explicit Core application and interruption recovery.

[Unified project knowledge](./unified-knowledge.md) describes incremental source
memory, conversation continuity, source-bound wiki, neural GraphRAG and optional
background maintenance with commit observation.

[Reviewed wiki](./knowledge-wiki-review.md), [refresh controls](./knowledge-automation.md)
and [follow-up search](./knowledge-followup.md) describe the operator workflows
and their boundaries. [Validation](./knowledge-validation.md) separates measured
checks from human acceptance and elapsed adoption evidence.

[Reviewed memory consolidation](./knowledge-consolidation.md) describes the
local/API/manual analysis, incremental queue, selected review, approved-memory
context and graph, explicit archive migration and current limits.

[Work plan and Gantt](./work-schedule.md) describes hierarchical work blocks,
process order, dependency arrows, calendar estimates, buffers and critical path.
