# Project Map source observation (v1)

`cc_project_map_sources` builds a read-only structural proposal for the
[Project Map model](project-map-contract.md). It inventories a selected root,
observes bounded source identities and parses supported metadata. It does not
execute the project, infer completion or add a CLI/desktop route. This optional
module is not required by Core commands.

## API

```python
from cc_project_map_sources import (
    SourcePolicy, MapSourceError,
    preview_project_map_scope, observe_project_map,
)

request = {
    "project_root": selected_absolute_root,
    "project_id": "my-project",
    "observed_at": "2026-09-21T12:00:00Z",
    "design_paths": ["docs/design.md"],
}
preview = preview_project_map_scope(request)
observation = observe_project_map(request, expected_preview=preview["preview_id"])
projection = observation["projection"]
```

The exact request fields are shown above; unknown fields are rejected. Root is
a nonempty absolute native path without traversal, UNC/device/ADS spellings on
Windows, unsafe segments or excessive length. Project ID follows the model's
ID grammar. Observation time is a valid caller-supplied UTC timestamp with
seconds, not a claim that this module authenticates wall-clock time.
`design_paths` is a unique list of explicit root-relative `.md`/`.txt` paths;
use an empty list to observe code without reading documents.

The preview is pure: it validates the selection without opening files. It
returns `schema_version`, `adapter`, `preview_id`, sorted `design_paths`,
`code_extensions`, `manifests`, `excluded_names`, `hidden_paths_excluded`,
`sensitive_override_supported`, `limits` and `detail_policy`. The current detail
policy is `file-overview-with-bounded-symbols-v2`. Its digest binds normalized
request, policy, detail policy and adapter version. The observer's optional `expected_preview` rejects
a different request/policy before reading. This is selection consistency, not
an approval token or filesystem snapshot. UI selection/preview delivery belongs
to a later integration slice.

Both functions accept keyword-only `policy=SourcePolicy(...)`. The frozen
policy accepts positive integers no greater than these defaults:

| Field | Default |
| --- | ---: |
| `max_entries` | 5,000 names encountered, including exclusions |
| `max_depth` | 24 directory levels below the selected root |
| `file_bytes` | 1,048,576 |
| `target_bytes` | 33,554,432 aggregate retained content bytes |
| `input_count` | 384 retained reader entries, including ancestors |
| `max_ast_nodes` | 20,000 AST or manifest-container values per file |
| `max_design_files` | 32 |
| `seconds` | 10, cooperative monotonic budget |

No dependencies are installed. Parser and model bounds are additional limits.
The entire returned object must fit 768 KiB compact UTF-8 JSON, not just its
inner map. The model also limits nodes to 2,000 and source references to 4,000.
Per-file AST/manifest budget exhaustion keeps that file's retained identity and
reports `parse_limit`, without publishing partial parser output. If optional
symbol expansion exceeds node/output bounds, all symbol nodes are omitted with
`symbol_detail_omitted`, retaining the complete file inventory and available import
declarations. One bounded projection retry uses the same retained source snapshot;
it does not reread files or increase limits. The UI explicitly reports limited detail.
If the file inventory itself exceeds a limit, a smaller selected root is still
required. There is no pagination, automatic limit increase or silently truncated
file inventory. The 384-entry retained-handle
cap is deliberately lower than the enumeration ceiling to bound OS resources.

The time budget is checked during traversal, AST walking and before/after
rechecks and projection. One OS I/O or parser call is not forcibly interrupted;
this is not a hard real-time or hostile-parser process-isolation guarantee.
Parser input bytes and output traversal are bounded. No performance assertion
follows merely from these engineering budgets.

## Selection and privacy

Hidden path components are excluded, including `.git`, `.controlcoding`,
`.controlwork`, `.ssh`, `.aws`, `.env` and hidden caches. Named dependency/build/
runtime/private stores such as `node_modules`, `vendor`, `dist`, `build`, `target`,
`__pycache__`, `venv`, `env`, `coverage`, `logs`, `secrets`, `credentials`,
`private`, `memory`, `data`, `storage`, `runtime`, `scratch`, `exports` and
`reports`, `_work` and `devlog` are excluded. The latter two keep maintainer working
material out of source maps. Prefixes `secret`, `credential`, `id_rsa`, `id_ed25519`
and credential/database/log suffixes `.pem`, `.key`, `.p12`, `.pfx`, `.db`,
`.sqlite`, `.sqlite3`, `.log`, `.env` are excluded. The selected root itself
cannot be one of these excluded names or lie under a hidden, secrets,
credentials or private ancestor. Root ancestors named scratch/reports are
permitted so external development workbenches remain usable.

Exclusions are conservative defaults, not a general secret classifier. Explicit
selection cannot override them in this version: excluded/sensitive documents
are unsupported. Nonhidden unselected documents and unknown extensions receive
metadata-only inventory, without content reads. Filenames and code identifiers
are intentional metadata; consumers must treat them as text. Document bodies,
headings, docstrings, string literals, commands, package descriptions, dependency
values and arbitrary source excerpts never enter returned observations.

Relative paths reject absolute/drive/ADS forms, backslashes, control/surrogate
characters, traversal/empty segments, wildcard/Win32 alias characters, trailing
dot/space segments and reserved DOS device basenames. Invalid discovered names
are omitted with a generic diagnostic. Selected invalid names fail validation.
The absolute root is not echoed into the result or errors.

## Observation and adapters

Directories propose `module` nodes below a `system` root. Files propose `file`
nodes; this filesystem grouping is not a claim that a folder is a service or
business subsystem. All mappings start `proposed`, all delivery and plan states
remain `unknown`, and no criteria, acceptance, assessment or constraint is
invented. Presence means an element was observed, not that it was implemented
correctly. Canonical lifecycle, receipt, perimeter and gate reading is separate.

Content is read for the code extensions returned in the preview, `package.json`,
`pyproject.toml` and selected design documents. Each source record carries a
SHA-256 of retained, rechecked bytes, owner, relative locator and adapter ID.
Metadata-only sources explicitly lack a content identity. No project code or
plugin is imported, and no commands, tests, builds, Git filters, package scripts,
providers, database/memory initialization or writes are invoked.

| Adapter | Returned metadata |
| --- | --- |
| `python-static` | Class/function/async-function symbol nodes when within map bounds, lexical parent links, line numbers, direct import module/relative-level declarations, observed dynamic-import marker, dependency resolution `unsupported`; `symbol_detail: omitted` marks bounded file-overview fallback |
| `manifest` | Ecosystem (`python`/`javascript`), dependency declaration count and script declaration count; no names/values/commands |
| `design-document` | UTF-8 byte count, line count and Markdown heading count; no excerpts or invented phases/features |
| `file-identity` | Byte count and semantic analysis `unsupported`, including JS/TS |
| `inventory` | Whether content was read; optional parse state `unavailable` or `limit`; retained byte count when parsing exceeded its limit |

Python parsing uses `ast.parse` without execution and honors Python's source
encoding rules. Qualified ASCII symbol names are limited to 160 characters;
unsupported identifiers or syntax/encoding errors yield unavailable parsing,
not guessed structure. Imports are declarations, not proof of resolution:
relative, dynamic, external, aliased and build-dependent imports are never
called broken merely because this adapter cannot resolve them. Recognized
`__import__`/`import_module` call syntax produces a limited marker; absence of
that marker does not prove absence of dynamic imports.

The JS manifest count sums dependencies/dev/peer/optional groups as declarations,
not unique installed packages. Python currently counts only `[project]`
`dependencies`; other tool-specific metadata and optional dependency groups
are not assessed. Python script count is null (not analyzed), not zero. JSON duplicate
keys/nonfinite constants and unsupported manifest shapes reject parsing.
Selected design documents remain references for later manual/AI mapping;
headings alone are not acceptance criteria or delivery phases.

## Identity, rechecks and coverage

Root identity hashes normalized absolute location plus observed filesystem
identity. Node IDs hash that identity plus relative locator; Python symbol IDs
also include qualified name and occurrence ordinal. Labels and source line
offsets do not determine identity. Identical labels in different roots cannot
share IDs. Renames/root replacement change identity; no rename matching or
persistent alias is inferred. Snapshot IDs also bind source observations and
caller observation time. Repeating unchanged input/time returns the same data.

The observer reuses the setup service's **private** `_Snapshots` read seam,
without changing setup allowlists or APIs. The separate source policy supplies
its bounded file/aggregate/handle allowances. Ancestors and traversed directories
are retained, ordinary files are read through retained descriptors, and their
identities/bytes are rechecked. Directory memberships and metadata-only file
identities are rechecked separately. Windows retained handles deny write/delete
sharing; POSIX uses descriptor-relative no-follow opens. Reparse points,
symlinks, special files and multiply linked files are rejected before content
reads. Unsupported entries are reported, never traversed.

Rechecks detect observed races or fail closed. They do not make a transactional
filesystem snapshot or promise immutability after handles close. Windows is the
initial exercised host; POSIX execution needs its own actual test results.
The private setup/model helper dependencies need coordinated regression tests
if later refactored; they are not new public extension APIs.

Every successful result has `canonical: false`, `mode: observation`, and
**partial** coverage: static inventory never establishes all semantic/lifecycle/
verification/control dimensions. Exclusions and unsupported sources remain
explicit. An empty project has no completed units. The inner model may emit
`source_unknown` for metadata-only content; no source existence becomes green.

## Result and errors

The result has `schema_version: 1`, `adapter: cc-project-map-sources/v1`,
`canonical`, `mode`, `root_identity`, `preview_id`, `projection`, `observations`,
`findings`, `counts`. Projection follows the model contract. Each observation
has `source` (source ID), `node` (file node ID), `adapter`, `details` as above.
Counts are `enumerated_entries`, `content_bytes`, `retained_entries`; byte counts
describe retained content once, excluding the repeated validation reads.

Findings have `code` and `source` (relative path or generic `scope`):
`excluded_path`, `unsafe_name_omitted`, `unsupported_path`,
`content_not_selected`, `selected_design_unavailable`, `parse_unavailable`,
`parse_limit`, `symbol_detail_omitted`, `dynamic_import_unresolved`,
`semantic_analysis_unsupported`. They are scope/
capability observations, not proven code defects. Sorted finding codes also
appear in coverage omissions beside the unassessed canonical dimensions.

Fatal errors raise `MapSourceError` with only its safe `code`:
`invalid_policy`, `invalid_request`, `invalid_path`, `excluded_design`,
`excluded_root`, `preview_mismatch`, `root_unavailable`, `scope_limit`,
`time_limit`, `changed_input`, `unsupported_path`,
`unsupported_platform`, `source_unavailable`. No raw parser/OS message or partial
current projection is returned on fatal failure. Per-file parser omissions and
whole-map symbol-detail fallback are explicit successful partial observations,
not suppression of I/O, identity, time or non-limit model errors.
Callers must invalidate a previous current view
on failure. Refresh/cancellation/UI delivery and persistence are separate work.
