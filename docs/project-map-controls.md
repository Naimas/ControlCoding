# Project Map canonical control observations

CC-MAP-05 adds a read-only projection to the optional desktop Project Map.
It reuses the feature registry, installed policy definitions and canonical
evidence assessment. It does not change these owners, execute project checks,
run hook entry points, grant permissions or consume lift tokens.

After observing sources, choose **Preview controls & evidence**, inspect the
additional scope, then **Observe controls & evidence**. This is a broader read
than the code inventory. Ordinary map refresh, document changes and project
switches clear these signals; they require another explicit controls preview.
There is no watcher or continuing freshness guarantee.

## Independent signals

| Signal | Authority and interpretation |
| --- | --- |
| Feature lifecycle | `.controlcoding/features/features.json`, validated against the installed feature owner. Reported `completed` is distinct from verified acceptance; `blocked` is labelled **Reported blocked**. |
| Criteria | Criterion identities and hierarchy are visible; free-text bodies and feature-local receipts are omitted. No criterion-specific pass is fabricated. |
| Code relationships | Feature `scope` is free text. It cannot assign lifecycle or completion to files, modules or symbols. Work dates, phases and responsible people are not invented. |
| Verification/invariant gates | Existing `cc._evidence_prepare` and `cc._evidence_history` perform the canonical source/context/selection assessment. Results apply to the project, never automatically to a file or feature. |
| Module perimeter | Canonical active-module file precedes its supported legacy fallback. Sanitized panel-process `CC_ACTIVE_MODULE` is compared with file context. Matching uses the installed feature-lock owner's owns/shared-write functions. `may_read` remains declarative. |
| Protected operations | Installed Core self-protection patterns and mandatory zones plus the selected project's configured zones are matched to observed file/symbol paths. The operation is `write`; decisions are predictions with unknown host coverage. |
| Pending approval | Only a pending request naming the exact relative file adds an approval marker. Tokens, reason bodies and active lift evaluation are omitted. Pending approval is not permission. |

The code grid retains its completion color. **L** marks a predicted restriction,
**A** a scoped approval request, and **P** other perimeter/configuration records.
When several apply, A takes marker precedence; the inspector shows every scoped
record. Popup text names decisions and rules. Pinned details show operation,
scope, reason, coverage, host/context, proceed condition and owner/source hashes.
An allow prediction cannot override a protection rule or establish host delivery.
No control record is presented as an observed editor denial.

Missing, duplicated or conflicting module context produces an explicit context
conflict, not a manufactured global denial. Unsupported or malformed input is
unknown/invalid or a failed bounded observation. Invalid custom zone config does
not remove the installed mandatory protections. The trusted template is labelled
as installed Core policy, not as an observed adopter hook.

Canonical evidence assessment handles full required passes, newer subsets,
failures, stale input, changed context, invalid/legacy/incomplete history and
unknown inputs. There is no fallback to an older green receipt and no second
freshness engine. Receipt command text, captured output and environment values
never enter the projection. The helper's restricted environment can differ from
the environment that produced a receipt, preventing a current pass. A current
project gate still does not establish per-file verified acceptance.

## Read scope and consistency

Preview lists fixed control-plane paths, receipt folders, declared extra evidence
paths and environment variable names. It prepares the named contracts without
reading receipt history or running evidence commands. Read observes the reviewed
source map, then retains the control files and listings while deriving signals.
Canonical evidence may inspect raw inputs outside the code-map inventory and
use its own private safe Git projection. No target Git filters or project code
are executed. Its private temporary Git files are outside the selected project.

Control reads allow 1 MiB/file, 8 MiB total, 256 retained entries, at most 64 module
locks, 5,000 directory entries and depth 24. Receipt-folder enumeration is bounded
to 128 entries. The existing evidence owner separately allows input limits of
32 MiB/file, 256 MiB total and 10,000 entries. The adapter's tighter retained-file
budget can reject history that the standalone owner accepts. The adapter checks
a 12-second cooperative control budget; the shared desktop helper retains its
15-second process deadline, even when an owner's internal budget is longer.
The complete map stays within the existing 768 KiB output limit.

Main retains selected root, source preview/time, source snapshot and definition
revision. Controls preview binds those plus contract descriptors and process
module context into a scope digest. Read reobserves the base map, rejects stale
source/revision bindings, pins root identity and control files, rechecks retained
bytes/listings and contract scope before returning. Failure/cancellation clears
the previous map and controls preview. Late replies cannot cross project
generations. Observation is not a globally atomic filesystem snapshot; base-map
source handles close before the additional controls pass. Manual refresh is
needed after edits, and the UI observation timestamp is not receipt freshness.

## Transport and verification

The preload methods `controlsPreview()` and `controlsRead()` accept no arguments.
Main verifies the sender and owns all request paths and binding fields.
`map_controls_preview_v1` adds `snapshot` and `revision` to map-read fields;
`map_controls_read_v1` additionally requires `scope_id`. Preview returns
`controls_scope`; read returns the usual reviewed `map` with `controls` and
model-validated nodes, assessments and constraints. Unknown fields are rejected.
Only these operations forward sanitized `CC_ACTIVE_MODULE`; invalid raw values
are represented as invalid context without disclosure. No general environment
forwarding, command dispatcher, lifecycle editor or unlock API is added.

Disposable-fixture tests compare real generated receipt assessments with the
canonical owner on Python 3.11 and 3.13, exercise unsafe/changing inputs and
process transport, and check Node generation/binding behavior. Real Electron
tests cover lifecycle/criteria, matrix consistency, independent code markers,
popup/provenance, themes, narrow layout, IPC rejection and source preservation.
The source and reviewed-mapping Electron regressions remain separate checks.
This does not certify host hook delivery, all platform behavior, accessibility
or a project's actual health. Independent review remains pending.

See [desktop contract](project-map-panel.md), [model contract](project-map-contract.md),
[definition contract](project-map-definition.md) and [evidence owner](verification-evidence.md).
