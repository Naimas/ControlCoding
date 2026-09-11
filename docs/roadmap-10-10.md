# Trust and Verification Reference

Status: Current reference. The historical filename is retained for existing links.

This document summarizes the current trust and verification mechanisms. The
production implementation, routed command set, capability registry, and
machine-readable contracts remain authoritative.

## Authority and Scope

The current public contract is distributed across:

- the built-in capability registry and command routing in
  [`scripts/cc.py`](../scripts/cc.py);
- the experimental docs-maintenance registry entry in
  [`scripts/cc_docs.py`](../scripts/cc_docs.py);
- [`controlcoding.verification.json`](../controlcoding.verification.json);
- [`controlcoding.invariants.json`](../controlcoding.invariants.json);
- [`controlcoding.release.json`](../controlcoding.release.json);
- the operational boundaries in [`CONTROLCODING.md`](../CONTROLCODING.md).

Capability state and control level are separate properties. A capability may be
`shipped`, `optional`, `experimental`, `planned`, `private`, or `removed`,
while its control may be `mechanical`, `conditional`, `advisory`, or
`unavailable`. A mechanical label applies only to the named mechanism and
evidence. It is not a general product guarantee.

Documentation cannot promote a capability. Features absent from the routed
surface, or classified as planned, private, or removed, are outside the current
public command contract.

## Command and Capability Truth

The production registry records each capability's state, control level, hosts,
commands, and evidence. The current truth mechanism is shipped and classifies
its own route and evidence checks as mechanical.

- `cc truth report` presents the normalized registry.
- `cc truth check` validates registry fields, duplicate identifiers, accepted
  states and control levels, evidence for mechanical claims, and routed commands
  for shipped, optional, and experimental capabilities.
- `cc truth check-docs` inspects selected public documents for ControlCoding
  command references and strong control claims.
- `cc truth check --include-docs` combines registry and public-document
  findings.
- Strict doctor mode can include the resulting claim-integrity state.

These checks establish structural consistency. Route validation proves that a
documented command reaches an implemented handler; it does not prove the
handler's functional correctness. The prose check requires operational context
for strong claims, but it is not a general natural-language fact checker.

## Verification Contracts and Invariants

The verification contract declares required `targeted`, `regression`, and
`invariant` suite kinds. It also contains an optional smoke suite.
`cc verify status` validates the contract, while `cc verify run` executes the
selected suites and stores local receipts.

The invariant manifest separates protected properties from generic test names.
The current Core manifest declares active invariants for:

- public documented command truth;
- capability truth;
- verification-contract validity.

The invariant commands can report whether properties are documented,
executable locally, or detected in tracked CI wiring. CI-file detection does
not prove that a repository host has enabled or successfully run the workflow.

A valid contract states what must be checked. An executable command states that
a check can run. Only a current result or receipt provides evidence for that
specific execution.

## Release Controls

`cc release-doctor` evaluates the source-release profile. Its checks include:

- required source directories and files;
- command and capability truth;
- verification-contract validity;
- an executable invariant manifest;
- architecture-index status;
- promotion-manifest status;
- release-manifest allow, deny, required, and Git-tracking rules;
- tracked local-artifact leakage and configured public-hygiene findings.

The release manifest defines the source-distribution boundary. Required files
must exist and be tracked, denied paths must not be tracked, and tracked files
must be covered by an allow rule.

Release doctor validates publishable source state; it does not run the suites
declared by the verification contract, certify an installed adopter project,
change repository visibility, publish a release, or approve legal text. Its
release-profile operational contract deliberately leaves AI-assisted and
autonomous installed-project readiness unassessed.

## Context Ownership and Host Limits

`CONTROLCODING.md` is the canonical ControlCoding context. Supported
host-native files are managed projections. Context checks compare generated
content with the canonical source, context drift checks examine selected
configuration and invariant references, and synchronization changes projections
only when explicitly invoked.

Existing managed adapters carry a machine-checkable ownership binding. Normal
replacement requires a valid binding. An invalid, ambiguous, foreign, or
unmarked existing target fails closed; an unmarked file requires the separate,
explicit adoption path. Forced synchronization does not grant ownership to an
unrelated file.

Host control is capability-dependent:

- a native-hook host can run supported boundary checks before selected writes;
- a host without native inline hooks relies on permission controls,
  repository-side gates, review, verification, and CI;
- instructions remain advisory unless a named mechanism enforces them;
- no host gate intercepts arbitrary writes outside its supported path.

Normal `cc doctor` reports the installed project's active host, protection
model, gate states, gaps, and residual risks. A healthy status alone is not a
universal safety claim, and one host's result does not imply parity with another
host.

## Promotion Path

The production registry classifies the promotion path as `experimental` with
a `conditional` control level. The routed surface is `cc promote plan`,
`cc promote check`, and `cc promote apply`.

Promotion follows the staged path:

```text
workspace -> features -> shared -> stable
```

The checks cover source and target placement, target availability, configured
consumer fan-in and recent-churn thresholds, and the next permitted zone.
Promotion into `stable/` also requires a valid verification contract and a
valid executable invariant manifest. Applying a stable promotion requires an
ADR.

Planning writes a local promotion manifest, checking reports the current
preconditions, and applying performs the move. These mechanisms do not prove
domain correctness, execute all verification suites, or provide a complete
rollback analysis. Their passing state must not be presented as release
validation.

## Evidence Boundaries

Implementation and validation are distinct:

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Routed code and registry entry | A command or capability is implemented and classified. | Correctness, effectiveness, or active host wiring. |
| Verification or invariant contract | Required checks and protected properties are declared. | That the checks passed for the current candidate. |
| Current result or receipt | One identified execution produced the recorded outcome. | Future results or general effectiveness. |
| Release-doctor result | The inspected source tree met its configured release checks at that time. | Publication, legal approval, installed-project safety, or scientific validation. |

The [evidence report](./evidence.md) contains project observations and states
its limitations: two projects, one developer, no formal control group,
untracked hook-block counts, and possible confirmation bias. Those observations
are not statistically generalizable and must not be treated as certification
or a general guarantee.

No numerical score is assigned by this reference. Trust claims remain
proportional to the mechanism, current evidence, host boundary, and candidate
actually inspected.
