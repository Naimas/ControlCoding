# ControlCoding Release Model

This page defines the public package boundary for the current ControlCoding
V1/Core candidate.

## Current Package

The current public package is **Core**, distributed as source. Users run it
from a checkout or archive with Python 3.10 or later. It is not represented as
a bundled desktop application.

Core includes:

- CLI, setup, doctor, truth, verification, invariants, and release checks;
- Project Memory Engine implementation and public memory contracts;
- hook and adopter templates;
- public documentation and regression tests;
- package, pytest, fitness, release, verification, and invariant contracts;
- three explicitly allowlisted curated benchmark summaries.

## 3.0.1 Candidate Position

ControlCoding `3.0.1` is the current V1/Core candidate, not a published release.
It is a major release candidate because the public CLI is incompatible with
the stable `v2.5.2` line in four ways: `cc replace start/status/complete` was
removed, `cc benchmark` changed its interface and default output names,
`cc organize` became preview-only unless `--apply` is supplied, and `cc resume`
became a provider-neutral print-only command.

The `v3.0.0` tag remains an immutable historical reference. Candidate `3.0.1`
supersedes it as the recommended release after a deterministic correction to
the stage identity regression test. That correction did not modify production
behavior.

Migration guidance is documented in the [Quick Start](./quick-start.md),
[installation guide](./install-controlcoding-on-your-project.md),
[cross-tool guide](./cross-tool-guide.md), and
[CLI tools reference](./ccdocs/tools-reference.md).

Promotion remains curated. Level C validation, release preparation, commit,
the future `v3.0.1` tag, push, and publication are separate gates. Candidate
metadata does not authorize or attest any of those actions.

## Explicit Exclusions

| Surface | V1/Core status |
|---|---|
| Desktop Studio/gateway UI under `ui/` | Not shipped in this candidate. |
| UI runtime and surface smoke tests | Not shipped in this candidate. |
| Auditor skill packs and auditor-specific tests | Not shipped in this candidate. |
| Raw L3/L5 benchmark packages and benchmark setup tests | Not shipped in this candidate. |
| Local dogfooding evidence, issue registers, comparison work notes, and benchmark plans | Not shipped in this candidate. |
| Audit reports, improvement backlogs, workspace context, and obsolete release or memory plans | Not shipped in this candidate. |
| Initialized `.controlcoding/` or `.controlwork/` state | Never part of the clean source package. |

The absence of these files is intentional. Their historical presence in a
development repository must not be interpreted as a public feature claim.

## Core And Optional Helpers

Core remains usable without specialist orchestration. Bounded manual
consultation is part of the Core usage story only when the user explicitly
chooses it and mediates the exchange.

Agent or consultation helpers that remain under allowlisted source and template
paths are optional components. They do not imply hidden routing, background
backend execution, or autonomous recursive agent loops.

Auditor packs are a separate development surface and are excluded from this
candidate.

## Agents And Studio

`Agents` and `Studio` describe possible additive product layers, not packages
shipped by this candidate.

- An Agents release would require an explicit manifest, documented runtime
  boundary, consent configuration, and verification evidence.
- A Studio release would require a separately promoted UI implementation and
  its own build, security, and integration gates.
- Neither layer is a prerequisite for Core.

## Curated Benchmark Evidence

The public evidence surface is limited to:

- [R5 release-pair scorecard](../benchmarks/2026-04-27-r5-release-pair-scorecard.md)
- [3D crawler scorecard](../benchmarks/2026-04-11-cctest-3dcrawler-v11-scorecard.md)
- [3D crawler review check](../benchmarks/2026-04-11-cctest-3dcrawler-v11-review-check.md)

These are curated summaries with explicit caveats. Raw benchmark projects and
generated local evidence are excluded. The summaries do not establish a
universal performance or host-parity claim.

## Security Boundary

- Use official CLIs, official APIs, local runtimes, or vendor-approved
  connectors.
- Do not embed or replay consumer login, OAuth, or session tokens in another
  product.
- External specialist execution requires visible approval and clear backend,
  model, permission, and cost context.
- On official CLI paths, the official user host remains the canonical chat
  surface.

## Source Release Verification

The release scope is defined by
[`controlcoding.release.json`](../controlcoding.release.json). Required checks
are defined by
[`controlcoding.verification.json`](../controlcoding.verification.json) and
[`controlcoding.invariants.json`](../controlcoding.invariants.json).

```bash
python scripts/cc.py release-doctor --project-root .
python scripts/cc.py verify run --project-root .
```

## Related Pages

- [Install ControlCoding On Your Project](./install-controlcoding-on-your-project.md)
- [Quick Start](./quick-start.md)
- [System Architecture](./controlcoding-system-architecture.md)
- [Project Memory Engine](./project-memory-engine.md)
