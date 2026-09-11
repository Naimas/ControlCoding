# ControlCoding Public Core Contract

This document defines the public contract for the ControlCoding V1/Core source
release. It describes the shipped architecture, behavioral boundaries, and
security posture. Executable code, tests, and machine-readable contracts remain
the authority for actual behavior.

## Public Release Scope

ControlCoding V1/Core is a source release intended to run from a checkout or
source archive with Python 3.10 or later.

The release includes:

- the `scripts/` CLI, Project Memory Engine, and supporting local tools;
- public templates and hook examples under `templates/`;
- public documentation under `docs/`;
- regression, invariant, and contract tests under `tests/`;
- the release, verification, invariant, fitness, packaging, and pytest
  contracts at the repository root;
- explicitly allowlisted curated benchmark summaries;
- the license, notice, trademark policy, and contribution guidance.

The release does not include initialized project memory, local receipts,
maintainer workspaces, audit outputs, auditor skill packs, raw benchmark
workspaces, obsolete execution plans, or the desktop Studio/gateway UI.

## Architecture

ControlCoding separates four concerns:

1. **User Host** - the official CLI, IDE integration, API client, local runtime,
   or vendor-approved connector chosen by the user. It remains external to
   ControlCoding.
2. **CC Structure** - the Core layer shipped here: CLI commands, configuration,
   hooks, repository gates, verification contracts, documentation, and local
   memory tooling.
3. **CC Agents** - optional helper or specialist components. They must remain
   explicit, bounded, and subordinate to the Structure layer. Their presence in
   source does not imply autonomous orchestration.
4. **CC UI** - an optional product layer. The desktop Studio/gateway UI is not
   part of this V1/Core release.

The public dependency direction is:

```text
User Host -> CC Structure -> optional CC Agents -> optional CC UI
```

CC Structure is mandatory. Optional layers must never be treated as a
prerequisite for Core.

## Core Behavioral Contracts

- `cc setup`, `cc setup-project`, and `cc doctor` provide the public setup and
  health-check path.
- `cc truth`, `cc verify`, and `cc invariants` expose executable trust and
  verification contracts.
- `cc memory` manages local development memory, retrieval, graph, session, and
  Project Plane compatibility features.
- `cc release-doctor` evaluates the publishable source contract declared in
  `controlcoding.release.json`.
- Generated host context, runtime state, receipts, databases, and caches are
  local artifacts. They are not part of a clean source release unless a public
  contract explicitly says otherwise.

## Enforcement Boundaries

ControlCoding reports the enforcement model that the active host can actually
provide.

- Native-hook hosts can run supported boundary hooks before selected writes.
- Hosts without native inline hooks rely on repository gates, review,
  verification, and CI as the mechanical backstop.
- Instructions and prompts are advisory unless a named hook, repository gate,
  command, or CI check enforces them.
- Boundary hooks do not intercept arbitrary writes performed outside the host's
  supported hook path.

Public documentation must not claim universal pre-write prevention or parity
between hosts with different capabilities.

## Public Invariants

1. Hook scripts return `0` to allow and `2` to block. They do not use `1` as a
   policy result.
2. Public hook scripts use Python standard-library imports, except for local
   hook modules.
3. Boundary-hook self-protection is hardcoded and cannot be disabled through a
   generated host context file.
4. Public command examples must route to implemented CLI handlers.
5. Shipped, optional, experimental, and planned capability states must agree
   with code and evidence.
6. The verification contract must declare required targeted, regression, and
   invariant suites.

## Security And Authorization

- Use official vendor CLIs, official APIs, local runtimes, or vendor-approved
  connectors.
- Do not embed a vendor consumer login in a ControlCoding-owned interface.
- Do not copy, replay, or reuse consumer OAuth tokens or session credentials
  across products.
- External specialist calls require visible human approval and clear backend,
  model, permission, and cost context.
- Hidden backend execution and recursive external-agent loops are not the
  public default.
- Keep secrets, personal data, customer data, and confidential material out of
  project memory unless the project has explicitly approved that storage and
  provider path.
- A project should have one visible primary user surface. Other connected
  surfaces must remain observers unless an explicit authority handoff occurs.

## Memory And Data Ownership

The Project Memory Engine is local project tooling backed by repository files,
Markdown, and SQLite. A clean clone contains implementation and documentation,
not an initialized memory database or private project state.

Project Plane data and application-owned runtime memory remain separate:

- ControlCoding memory supports development context and traceability.
- ControlWork-compatible Project Plane records support portable project and
  work knowledge when explicitly initialized.
- Application data, application RAG corpora, and application knowledge graphs
  remain owned by the application.

Generated views and packets are projections. They do not replace their
canonical source records.

## Verification Contracts

- [Release allowlist and denylist](./controlcoding.release.json)
- [Verification suites](./controlcoding.verification.json)
- [Executable invariants](./controlcoding.invariants.json)
- [Fitness thresholds](./fitness.json)

The canonical release gate is:

```bash
python scripts/cc.py release-doctor --project-root .
python scripts/cc.py verify run --project-root .
```

Fitness uses three result classes: `0` for clean, `2` for warnings-only, and
`1` for blocking violations. A release policy may require the warnings-only
result when the tracked baseline intentionally contains ratcheted warnings.

## Public Documentation

- [README](./README.md)
- [Documentation index](./docs/INDEX.md)
- [Release model](./docs/release-model.md)
- [System architecture](./docs/controlcoding-system-architecture.md)
- [Project Memory Engine](./docs/project-memory-engine.md)
- [Hooks reference](./docs/hooks-reference.md)
- [Cross-tool guide](./docs/cross-tool-guide.md)
