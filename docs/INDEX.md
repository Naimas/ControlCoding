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
| [install-controlcoding-on-your-project.md](./install-controlcoding-on-your-project.md) | Current | Primary installation guide. |
| [quick-start.md](./quick-start.md) | Current | Compact setup and startup path. |
| [release-model.md](./release-model.md) | Current | Exact V1/Core package boundary and exclusions. |
| [controlcoding-system-architecture.md](./controlcoding-system-architecture.md) | Current | Architecture, ownership planes, memory, and verification map. |
| [architecture-index.md](./architecture-index.md) | Contract | Release-visible inventory checked by `cc index --check` and the release doctor. |
| [project-memory-engine.md](./project-memory-engine.md) | Current | Local Project Memory Engine and practical workflows. |
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
