# ControlCoding Release Model

This page defines the public package boundary for the current ControlCoding
V1/Core release.

## Current Download and Tagged Releases

The [current Core source ZIP](https://github.com/Naimas/ControlCoding/archive/refs/heads/master.zip) follows `master` and contains the
September 2026 preservation, setup and verification updates. It is development
source; [download status and the exact verified baseline](../README.md#download-current-core)
are maintained in the README.

`v3.0.2` is the latest tagged release, retained as an older snapshot. Its release
assets predate those updates. A new numbered release has not been published.
Use the current source download for the updated code; keep historical release
results associated with their original tag.

## Current Package

The current public package is **Core**, distributed as source. Users run it
from a checkout or archive with Python 3.11 or later. Memory additionally requires
a working SQLite deserialize API, checked before initialization. The Core
minimum is separate from any hook-only compatibility claim. It is not represented as
a bundled desktop application.

Core includes:

- CLI, setup, doctor, truth, verification, invariants, and release checks;
- Project Memory Engine implementation and public memory contracts;
- hook and adopter templates;
- public documentation and regression tests;
- package, pytest, fitness, release, verification, and invariant contracts;
- three explicitly allowlisted curated benchmark summaries.

## 3.0.2 Release Position

ControlCoding `3.0.2` is the latest tagged V1/Core release, preceding the current
source update described above. It belongs to
the Version 3 major release line because the public CLI is incompatible with the
stable `v2.5.2` line in four ways: `cc replace start/status/complete` was
removed, `cc benchmark` changed its interface and default output names,
`cc organize` became preview-only unless `--apply` is supplied, and `cc resume`
became a provider-neutral print-only command.

The clean public repository begins at `3.0.1`. The `3.0.0` material retained in
the changelog provides migration context rather than an earlier public-repository
tag. Release `3.0.2` applies PolyForm Shield consistently to current
ControlCoding and embedded ControlWork material. It retains the deterministic
stage identity regression test introduced in `3.0.1`; that correction did not
modify production behavior.

Migration guidance is documented in the [Quick Start](./quick-start.md),
[installation guide](./install-controlcoding-on-your-project.md),
[cross-tool guide](./cross-tool-guide.md), and
[CLI tools reference](./ccdocs/tools-reference.md).

Publication remains curated. Level C validation, a reviewed commit, an annotated
tag, a controlled push, CI verification, and the GitHub Release record are
separate gates. Version metadata alone does not authorize or attest those
actions.

## Explicit Exclusions

| Surface | V1/Core status |
|---|---|
| Desktop Studio/gateway UI under `ui/` | Not shipped in this release. |
| UI runtime and surface smoke tests | Not shipped in this release. |
| Auditor skill packs and auditor-specific tests | Not shipped in this release. |
| Raw L3/L5 benchmark packages and benchmark setup tests | Not shipped in this release. |
| Local dogfooding evidence, issue registers, comparison work notes, and benchmark plans | Not shipped in this release. |
| Audit reports, improvement backlogs, workspace context, and obsolete release or memory plans | Not shipped in this release. |
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
release.

## Agents And Studio

`Agents` and `Studio` describe possible additive product layers, not packages
shipped by this release.

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

Schema-v2 receipts distinguish complete required execution from passing subsets
and compare the observed inputs and runtime with the current checkout. Use
`verify status --require-current` when a current complete pass is required.
Ordinary status and doctor validity are configuration checks. Receipt identity
does not establish hosted matrix success or server-side enforcement; see
[verification evidence](./verification-evidence.md).

- [Install ControlCoding On Your Project](./install-controlcoding-on-your-project.md)
- [Quick Start](./quick-start.md)
- [System Architecture](./controlcoding-system-architecture.md)
- [Project Memory Engine](./project-memory-engine.md)
