# Reviewed Project Map choices

CC-MAP-04 adds manual review to the optional desktop map. It is a development
candidate. The source/model contracts remain separate; Core needs none of this
UI. Select an element, choose **Confirm mapping**, **Reject mapping**, or prepare
a label/group change. Inspect **Review changes before saving**, expand the exact
before/after diff, then choose **Save reviewed mappings** or **Discard preview**.
Opening, observation and preview never create a definition.

## Choices and identity

Only `.controlcoding/project-map/definition.json` stores reviewed choices. Its
strict version-1 schema contains `schema_version`, retained `root_identity` and
`entries`. An entry has exactly `id`, `kind`, `title`, `decision`, `bindings`,
`sources`, `members`, `supersedes`. Decisions are confirmed or rejected;
source references contain relative path and historical content hash only.
No lifecycle, evidence outcome, permission, secret, model configuration or source
body belongs in this file. Unknown fields and duplicate JSON keys are rejected.

Reviewed entries receive stable `map:` IDs. Display renaming does not rename
code. Rejected entries stay visible as rejected on refresh. A removed observed
element remains missing; it is outside the current file-completion denominator.
Explicit **Relink missing source** attaches an unreviewed same-kind replacement
to its existing map identity. There is no heuristic rename matching. Old binding
IDs remain aliases; if old and new sources coexist, the choice is conflicted.
The historical source list reflects the latest explicitly selected binding.

**Create a code group** links selected observed/reviewed elements with
`references`; it does not rewrite physical directory containment. This creates
a manual component, not a canonical service/work-plan record. **Split this group**
partitions every member into exactly one new group; the desktop offers two
partitions, while the service supports two to eight. **Merge groups** deduplicates
members. Both preserve old group IDs and explicit `supersedes` links. Older
groups remain navigable. Groups do not multiply file-completion credit.

Limits: 256 KiB definition, 128 entries including history/member identities,
64 unique members per group, eight aliases per observed entry, eight merge
inputs/partitions, 160 characters per display name. No automatic pruning.
Source/model/output bounds still apply after the overlay. A map remains
noncanonical for software truth; this file owns only reviewed mapping choices.
Copying a definition to a different root identity conflicts. Root relocation
and implicit identity migration are unsupported.

## Source-bound preview and transport

`map_read_v1` overlays the saved definition on a fresh bounded source observation.
Its `review` object reports definition revision, original source snapshot,
reviewed entries and whether native saving is supported. Read failures clear the
current map rather than silently discarding choices.

The new exact-field messages extend the existing map scope request:

| Operation | Additional fields beyond read scope/time/preview ID |
| --- | --- |
| `map_review_preview_v1` | `snapshot`, `revision`, `change` |
| `map_review_apply_v1` | The same fields plus `approval_id` |

Main owns the selected root, original observation time/design scope, raw source
snapshot and expected definition revision. Renderer `reviewMapping(change)`
accepts only a bounded operation schema: accept, reject, rename, alias, group,
split or merge. It cannot submit paths, snapshots, definitions or verdicts.
`saveMapping()` and `discardMapping()` accept no arguments. Save uses only the
private pending preview. Refresh, project switch, new preview and discard
invalidate it. Commit blocks project switching/cancellation until its result;
application exit/timeout can still interrupt it and require diagnosis.

Preview reobserves the source scope, checks snapshot/revision and computes the
exact before/after entries. Commit repeats that preparation and requires the
same digest. Digests bind data; they do not authenticate a human or authorize
unrelated operations. The existing 64 KiB request, 1 MiB response, 768 KiB map
and 15-second helper supervision limits remain. No generic setup apply exists.
After a successful save the UI refreshes from disk; if that refresh fails it
keeps the save receipt but shows no current map. Failed saves never retry.

## Native write contract and interruption

Saving is supported on ordinary local Windows drive paths. Other platforms can
observe/preview but fail closed for persistence. UNC/device paths, reparse
points, symlinks, hardlinked definitions and unsafe ancestors are rejected.
Retained root/ancestor handles preserve the selected path chain. Observed
content hashes are rechecked with retained source handles before the write.

Existing definitions are opened without read/write/delete sharing, checked as
ordinary single-link files, and compared to the expected byte digest under
that same handle. New definitions use exclusive creation, never replacement.
An exclusively created `transaction.json` arbitrates map writers and contains
a flushed preimage, expected after-hash and preview identity before modifying
the definition. In-place writes use the retained exclusive target handle and
are flushed; only then is the owned journal marked for deletion by its handle.
Atomic rename is not used as a substitute for revision comparison.

The native semantics follow Microsoft's [CreateFile sharing contract](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [handle-based file disposition](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle).
Equivalent-user malicious code, privileged volume remapping, mapped-file writes,
storage hardware failure and a globally atomic filesystem snapshot are outside
the guarantee. The snapshot matches the last completed observation and pinned
selected content; later new directory entries need a fresh observation.

If the process stops during save, the target may contain old, new or partial
bytes. A retained journal prevents ordinary reads/saves from treating any of
those as current. **Recovery required** means preserve both files and inspect
the journal preimage and after-hash with a responsible maintainer. There is no
automatic stale-lock stealing, rollback or recovery button. A partially written
journal must not be trusted as a usable backup. Preflight failures may leave
new empty map directories; they never establish a successful save.

## Verification scope

Focused tests cover preview/no-write, refreshed choices, missing/alias identity,
split/merge history, stale sources/revisions, foreign writes, Windows native
sharing exclusion, abrupt process interruption, malformed/bounded files and
project isolation. Electron tests use real renderer/main/Python operations on
external disposable projects; only native chooser results are stubbed. This is
not independent review, POSIX write support, canonical gate certification,
screen-reader certification or a released installer.
