# ControlCoding System Architecture

Status: Current architecture map.

This document is an overview and navigation layer. It does not replace the
canonical operational context, methodology, memory schema, release model, or
ControlWork contract. Each section points to the detailed documents that own
the deeper rules.

Use this page before planning non-trivial changes, especially changes that
touch ControlWork, Project Memory Engine, GraphRAG, documentation governance, or
ControlCoding release packaging.

## Source Of Truth Map

ControlCoding uses explicit source-of-truth layers. Generated files, views,
packets, graph exports, and host adapters are projections unless a detailed
contract says otherwise.

| Area | Canonical owner | Detailed documents |
|---|---|---|
| ControlCoding operational rules | `CONTROLCODING.md` | [`../CONTROLCODING.md`](../CONTROLCODING.md) |
| Methodology and architecture principles | Public methodology docs | [`ccdocs/methodology.md`](./ccdocs/methodology.md), [`methodology-history.md`](./methodology-history.md) |
| Public release split | Release model | [`release-model.md`](./release-model.md) |
| Dev Plane memory | Project Memory Engine | [`project-memory-engine.md`](./project-memory-engine.md), [`memory-system-schema.md`](./memory-system-schema.md) |
| Project Plane memory | Embedded ControlWork contract | [`project-memory-engine.md`](./project-memory-engine.md), [`work-plane-compatibility-contract.md`](./work-plane-compatibility-contract.md) |
| Shared graph contract | Memory graph contract | [`memory-graph-contract.md`](./memory-graph-contract.md) |
| Documentation governance | File organization standard | [`file-organization-standard.md`](./file-organization-standard.md), [`adoption-guide.md`](./adoption-guide.md) |
| Documentation maintenance | Experimental docs maintenance command reference | [`docs-maintenance-plan.md`](./docs-maintenance-plan.md) |
| Host/tool compatibility | Cross-tool and install docs | [`cross-tool-guide.md`](./cross-tool-guide.md), [`install-controlcoding-on-your-project.md`](./install-controlcoding-on-your-project.md) |

### Where Does This Go?

Use this decision tree before creating a plan, decision, audit, receipt,
generated view, or release note:

1. Is it runtime configuration, a local receipt, or a machine-readable contract
   used by ControlCoding commands? Put it under `.controlcoding/` or in the
   root JSON contract explicitly named by the command.
2. Is it an engineering design, implementation plan, acceptance checklist, or
   technical decision for the codebase? Put it under `dev/` in the matching
   category.
3. Is it project knowledge, research intake, a work decision, a handoff packet,
   or portable ControlWork memory? Put it under `.controlwork/`.
4. Is it reviewed public guidance for adopters, release notes, architecture
   explanation, or a stable compatibility contract? Put it under `docs/`.
5. Is it reviewed release evidence intended for public consumption? Put the
   maintained summary under `docs/` or `benchmarks/`; keep raw audit output in
   an external workbench rather than in the V1/Core source package.
6. Is it a chronological local record of what happened in a session? Put it
   under `devlog/` and treat it as local history, not release source.
7. Is it generated from another source? Keep it near the generating system,
   mark it as a projection, and do not promote it as canonical truth without a
   review step.

### Canonical Owner Table

| Artifact type | Canonical location | Notes |
|---|---|---|
| Runtime config, local receipts, lift state, verification receipts | `.controlcoding/` | Runtime state and command-owned local artifacts. Do not copy initialized workspace state into V1 release artifacts. |
| Root release and verification contracts | Root JSON contracts such as `controlcoding.release.json`, `controlcoding.verification.json`, and `controlcoding.invariants.json` | Tracked when they define the release or verification contract. |
| Engineering plans, designs, acceptance criteria, technical decisions | `dev/` | Maintainer workbench. In V1/Core release work, promote only reviewed public outputs, not raw LAB plans. |
| Project knowledge, work memory, checkpoints, context packets, handoffs | `.controlwork/` | Portable Project Plane memory. It can summarize or link to other locations, but does not replace source code or public docs. |
| Public/adopter docs, release notes, stable contracts | `docs/` | Release-visible guidance. Plans in `docs/` need an explicit status such as current, plan, proposal, internal, superseded, or archive. |
| Reviewed public release evidence | `docs/` or curated `benchmarks/` summaries | Raw audit output is not part of the V1/Core source package. |
| Chronological local session history | `devlog/` | Local history and ceremony output. Not a release requirement by default. |
| Application source, tests, package files | Source tree, `tests/`, package/build files | The application or framework code remains authoritative for behavior. |

## Public Source Roles

The public source package separates implementation, runtime state, and portable
project knowledge.

| Surface | Role | Rule |
|---|---|---|
| Tracked Core source | Public implementation, templates, docs, tests, and contracts | Must satisfy the release manifest and verification contracts. |
| Local runtime state | Configuration, receipts, databases, generated views, and caches | Must not be copied into a clean source release. |
| ControlWork-compatible Project Plane | Portable work and project knowledge initialized by an adopter | Remains separate from ControlCoding development memory and application data. |

[`release-model.md`](./release-model.md) defines the exact package boundary.

## Product Layers

ControlCoding has four product layers.

| Layer | Meaning | Detailed documents |
|---|---|---|
| User Host | External official surface or IDE chosen by the user | [`cross-tool-guide.md`](./cross-tool-guide.md), [`CONTROLCODING.md`](../CONTROLCODING.md) |
| CC Structure | Mandatory core: hooks, CLI, config, ceremony, gates, logs, orchestration rules | [`ccdocs/methodology.md`](./ccdocs/methodology.md), [`ccdocs/tools-reference.md`](./ccdocs/tools-reference.md), [`hooks-reference.md`](./hooks-reference.md) |
| CC Agents | Optional specialist/helper layer | [`release-model.md`](./release-model.md), [`ecosystem-quickstart.md`](./ecosystem-quickstart.md), [`ccdocs/tools-reference.md`](./ccdocs/tools-reference.md) |
| CC UI | Optional product layer, not shipped in the V1/Core candidate | [`release-model.md`](./release-model.md) |

Rules:

- The User Host is not the ControlCoding UI.
- Core must remain honest about what is installed and mechanically enforced.
- API-backed routed specialists and Studio/UI belong to later release slices
  unless the release model explicitly promotes them.

### V1/Core Context And Containers

```text
Person: maintainer or AI-assisted developer
  -> Official User Host or CLI
     -> ControlCoding Core
        -> project repository source, tests, docs
        -> .controlcoding/ runtime config, receipts, memory, verification state
        -> optional .controlwork/ project knowledge
        -> local verification commands and repo-side gates
```

Container responsibilities:

| Container | Responsibility | V1/Core status |
|---|---|---|
| Official User Host or CLI | User interaction and command execution surface. | Required external surface. |
| ControlCoding Core | Structure, setup, hooks, command truth, verification, and release checks. | Required V1/Core package. |
| Project repository | Source code, tests, package files, and reviewed public docs. | User/project owned. |
| `.controlcoding/` | Local runtime state, receipts, generated views, and optional Dev Plane memory. | Local, not copied into V1 artifacts. |
| `.controlwork/` | Optional portable Project Plane memory. | Optional. Not required for V1/Core publication. |
| Agents and Studio | Specialist/helper and UI layers above Core. | Deferred or additive, not a V1/Core release gate. |

## Ownership Planes

ControlCoding separates project work, development memory, code behavior, and
application-owned runtime memory.

| Plane | Storage | Owner | Purpose |
|---|---|---|---|
| Project Plane | `CONTROLWORK.md` and `.controlwork/` | ControlWork contract | Research, requirements, source summaries, work decisions, plans, outputs, handoffs, checkpoints, chat continuity |
| Dev Plane | `.controlcoding/memory/memory.db` and `.controlcoding/views/` | ControlCoding | Development decisions, plans, consults, agent runs, document graph, GraphRAG, impact/context views |
| Code Plane | Source tree, tests, package/build files | Application/project | Actual product code and verification |
| Application Memory Plane | Application-defined stores | Application | Runtime data, app RAG, vector stores, app knowledge graphs, caches, user data |
| Host Integration Plane | `AGENTS.md`, `CLAUDE.md`, `.claude/`, `.cursor/`, etc. | Host adapters | Generated or host-required projections for AI tools |

Detailed documents:

- [`memory-system-schema.md`](./memory-system-schema.md) is the main technical
  map for Dev Plane, Project Plane, Session GraphRAG, RAG-O, and cross-plane
  packet behavior.
- [`project-memory-engine.md`](./project-memory-engine.md) explains the
  difference between `.controlcoding/`, embedded ControlWork, standalone
  ControlWork, and application-owned memory.
- [`memory-graph-contract.md`](./memory-graph-contract.md) defines graph record
  planes, lifecycle states, confidence values, and provenance rules.

## Coding Governance

Coding governance is the ControlCoding layer that keeps AI-assisted development
inside project boundaries.

Main mechanisms:

- canonical context and generated host adapters;
- protected zones;
- hooks and repo-side checks;
- CodeWarden review paths;
- `doctor` gates;
- verification and invariant contracts;
- controlled write path for patch validation;
- feature state machine for scoped work.

Detailed documents:

- [`CONTROLCODING.md`](../CONTROLCODING.md) defines active local operational
  rules, module boundaries, protected zones, pre-design protocol, and commit
  ceremony.
- [`ccdocs/methodology.md`](./ccdocs/methodology.md) explains the architectural
  model: condominium architecture, Model vs View, invariants, and promotion
  path.
- [`hooks-reference.md`](./hooks-reference.md) and
  [`ccdocs/hooks-reference.md`](./ccdocs/hooks-reference.md) explain hooks,
  exit codes, and host adaptation.
- [`feature-state-machine.md`](./feature-state-machine.md) explains the feature
  workflow contract.
- [`roadmap-10-10.md`](./roadmap-10-10.md) summarizes current trust and
  verification mechanisms, evidence boundaries, and limits.

## Documentation Governance

Documentation governance is separate from code governance. It controls which
documents are canonical, generated, local-only, shared, archived, deprecated,
or host-specific.

Main mechanisms:

- `documentation_mode = managed` or `project_managed`;
- `cc_artifact_mode = local_only` or `shared_repo`;
- local indexes for governed documentation categories;
- lifecycle states such as active, archive, deprecated, superseded;
- semantic fidelity rules for requirements and design docs;
- commit ceremony realignment of code, status, devlog, canonical context, and
  affected design/planning documents.

Detailed documents:

- [`file-organization-standard.md`](./file-organization-standard.md) owns the
  taxonomy, storage modes, local-only rules, and adopter project modes.
- [`docs-maintenance-plan.md`](./docs-maintenance-plan.md) describes the
  experimental `docs audit`, `docs check`, and `docs propose` maintenance
  loop and its boundaries.
- [`adoption-guide.md`](./adoption-guide.md) explains how documentation
  ownership is selected during adoption.
- [`quick-start.md`](./quick-start.md) and
  [`install-controlcoding-on-your-project.md`](./install-controlcoding-on-your-project.md)
  explain the user-facing setup path.
- [`CONTROLCODING.md`](../CONTROLCODING.md) defines the public Core contract,
  enforcement boundaries, and security posture.

## Memory And GraphRAG Architecture

ControlCoding has multiple memory and retrieval layers. They must remain
separate unless a command explicitly builds a federated packet.

| Layer | Role | Commands | Detailed documents |
|---|---|---|---|
| Dev GraphRAG | Retrieval over ControlCoding development memory and project documents | `cc memory retrieve`, `cc memory rag-pack`, `cc memory graph`, `cc memory evidence` | [`memory-system-schema.md`](./memory-system-schema.md), [`memory-graph-contract.md`](./memory-graph-contract.md) |
| Project Plane / Work GraphRAG | Retrieval over ControlWork memory and governed source evidence | `cw.py retrieve`, `cw.py rag-pack`, `cc memory work-scan`, `cc memory work-review`, `cc memory work-promote`, `cc memory work-graph`, `cc memory work-context-pack` | [`project-memory-engine.md`](./project-memory-engine.md), [`work-plane-compatibility-contract.md`](./work-plane-compatibility-contract.md) |
| Session GraphRAG | Traceability and retrieval over explicit session records | `cc memory session`, `cc memory session-pack`, `cw.py session` | [`memory-system-schema.md`](./memory-system-schema.md), [`memory-graphrag-release-notes.md`](./memory-graphrag-release-notes.md) |
| RAG-O | Read-only operations index and route coordinator | `cc memory op-index`, `cc memory startup`, `cc memory bootstrap` | [`memory-system-schema.md`](./memory-system-schema.md), [`project-memory-engine.md`](./project-memory-engine.md) |
| Cross-plane packet | Federated read-only context packet across implemented planes | `cc memory cross-pack` | [`memory-system-schema.md`](./memory-system-schema.md) |

Rules:

- RAG-O coordinates status and routes. It is not a retrieval engine and must not
  mutate memory.
- Dev GraphRAG does not become Project Plane truth.
- Project Plane graph output is a projection over ControlWork memory, not the
  source of truth.
- Application-owned memory stays outside ControlCoding ownership unless the
  application exposes an approved adapter.

## ControlWork Embedded And Standalone

ControlWork exists in two distribution forms:

1. Standalone `ControlWork`: independent product with `cw.py`,
   `CONTROLWORK.md`, and `.controlwork/`.
2. Embedded ControlWork inside ControlCoding: Project Plane governed through
   `cc memory work-*`.

ControlWork originated as the Project Plane inside ControlCoding and is now a
standalone, source-available noncommercial product. The embedded form remains
compatible with the standalone memory contract, but compatibility does not
permit commercial use of ControlCoding or ControlWork without separate written
permission.

Shared rules:

- The embedded form must remain compatible with the standalone memory contract.
- Portable behavior should be implemented in standalone ControlWork first or
  ported there after laboratory hardening.
- Scan evidence stays outside canonical Project Plane memory until reviewed.
  `cc memory work-review --review-status ready_to_promote` can be applied only
  when readiness blockers are clear; forced promotion requires `--force-note`.
- ControlCoding-only behavior stays outside ControlWork when it depends on
  code impact, hooks, agent runs, verification receipts, Dev Plane SQLite
  state, or ControlCoding-specific runtime evidence.

Detailed documents:

- [`project-memory-engine.md`](./project-memory-engine.md) explains the
  detachable design and embedded Project Plane.
- [`work-plane-compatibility-contract.md`](./work-plane-compatibility-contract.md)
  defines portable command and data-shape compatibility.

## Canonical Versus Derived Artifacts

ControlCoding follows Model vs View. Derived artifacts are useful, but they do
not replace canonical memory or project truth.

| Canonical or authoritative | Derived or projection |
|---|---|
| `CONTROLCODING.md` | `AGENTS.md`, `CLAUDE.md`, generated host files |
| `CONTROLWORK.md` and `.controlwork/memory/` | `.controlwork/memory/views/`, wiki projection, context packets, handoff packets |
| `.controlcoding/memory/memory.db` plus source files | `.controlcoding/views/`, graph exports, RAG packets, vector index |
| Application source code and tests | dashboards, reports, screenshots, generated summaries |

Detailed documents:

- [`ccdocs/methodology.md`](./ccdocs/methodology.md) explains Model vs View.
- [`memory-system-schema.md`](./memory-system-schema.md) marks memory views,
  graph exports, packets, and vector rows as derived where applicable.
- [`memory-graph-contract.md`](./memory-graph-contract.md) requires portable
  graph exports to declare provenance and non-canonical status.

## Glossary

| Term | Meaning |
|---|---|
| Runtime | Local configuration, hooks, receipts, verification state, and command-owned generated state. |
| Engineering | Plans, designs, criteria, source code, tests, and implementation decisions. |
| Knowledge | Durable project memory, research, source summaries, decisions, and optional ControlWork records. |
| Docs | Reviewed public or adopter-facing guidance under `docs/`. |
| Evidence | Audit reports, verification receipts, test output, command receipts, and cited source material. |
| Plan | A proposed or active execution sequence. It needs a status when placed under `docs/`. |
| Decision | A recorded choice with rationale and scope. |
| Handoff | A transfer packet for another maintainer, assistant, or session. |
| Context packet | A generated, scoped read packet for a chat or review session. |
| Session | An explicit continuity record for work performed over time. |
| Receipt | Machine-readable or human-readable proof that a command or workflow ran. |
| Source of truth | The canonical location that owns a fact, rule, behavior, or contract. |
| Derived artifact | A projection, view, export, packet, dashboard, or generated host file derived from a source of truth. |

## Extension And Anti-Duplication Rules

Before implementing a new feature, classify it by ownership plane and compare
it with existing commands.

Use this checklist:

1. Is the feature Project Plane behavior that works without ControlCoding?
   Implement or port it to ControlWork.
2. Does it depend on code, hooks, verification, agent runs, or Dev Plane SQLite
   state? Keep it in ControlCoding.
3. Does an equivalent command already exist in `cw.py` or `cc memory`? Extend it
   instead of adding a parallel command.
4. Is the output canonical memory or a derived projection? Mark the artifact and
   command behavior accordingly.
5. Does the feature import, infer, summarize, or extract source material? Put
   unreviewed output in inbox, sources, proposals, or needs-review state.
6. Does it affect public docs? Run documentation command truth checks.
7. Does it affect both standalone and embedded ControlWork? Add conformance
   tests or smoke checks for both forms.

Useful comparison points:

- `cw.py retrieve` and `cc memory retrieve` are different plane-specific
  retrievers.
- `cw.py rag-pack` and `cc memory rag-pack` build packets over different
  memory ownership planes.
- `cc memory cross-pack` is federated and read-only; it should not merge planes
  into one source of truth.
- `cc memory scan` is Dev Plane indexing. A future ControlWork scan should be a
  Project Plane proposal/import workflow unless explicitly designed otherwise.
- `cc memory intake promote` exists for ControlCoding Work Memory profile under
  `.controlcoding/`. A ControlWork promote command must operate on
  `.controlwork/` and remain portable.

Detailed documents:

- [`CONTROLCODING.md`](../CONTROLCODING.md) defines the Mini-Design Slice Gate.
- [`project-memory-engine.md`](./project-memory-engine.md) explains when to
  port behavior to ControlWork and when to keep it in ControlCoding.
- [`memory-system-schema.md`](./memory-system-schema.md) explains cross-plane
  interaction and release boundaries.
- [`docs-maintenance-plan.md`](./docs-maintenance-plan.md) documents the
  current experimental commands for architecture and system-document drift.

## Graph Tool Comparison Context

External graph tools can be useful prior art, but ControlCoding and ControlWork
should not copy graph-first behavior blindly.

Adoptable ideas usually belong in one of these forms:

- proposal-first scan or import;
- explicit source provenance;
- confidence and review status;
- explainable retrieval;
- graph path or neighborhood inspection;
- static visualizations that remain projections;
- query-first host guidance that does not bypass canonical memory.

Non-goals unless separately designed:

- treating generated graph output as canonical truth;
- automatic mass capture during setup;
- hidden network or AI calls;
- moving or reorganizing user files without explicit approval;
- merging Project Plane, Dev Plane, and application memory into one store.

Detailed documents:

- [`memory-graph-contract.md`](./memory-graph-contract.md) for confidence,
  provenance, planes, and portable graph shapes.
- [`memory-system-schema.md`](./memory-system-schema.md) for current Dev
  GraphRAG, Work GraphRAG, RAG-O, and cross-plane boundaries.
- [`project-memory-engine.md`](./project-memory-engine.md) for the public
  Project Plane boundary and practical workflows.

## Verification Map

Use targeted checks based on what changed.

| Change type | Suggested checks |
|---|---|
| Public docs or command examples | `python scripts/cc.py truth check-docs --project-root .` |
| Capability registry or command routing | `python scripts/cc.py truth check --include-docs --project-root .` |
| Memory or GraphRAG behavior | `python -m pytest tests/test_cc_memory.py -q` |
| Setup and host compatibility | `python -m pytest tests/test_cc_setup.py tests/test_cc_cli.py -q` |
| ControlWork standalone behavior | Run the corresponding tests in the standalone `ControlWork` repository |
| Release packaging | Run `python scripts/cc.py release-doctor --project-root .` and follow [`release-model.md`](./release-model.md) |

For non-trivial changes, record a mini-design slice before implementation and
include the audited commands, integration points, non-goals, duplicate-risk
checks, and acceptance checks.
