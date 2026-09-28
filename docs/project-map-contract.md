# Project Map model contract (v1)

`cc_project_map_model` is a pure, stdlib-only Python model for a future Project
Map view. It accepts decoded observations; it does not scan repositories, read
designs, call AI, persist decisions, render a grid, execute checks or enforce
policy. Every projection and explanation has `canonical: false`. No CLI command
or desktop integration is introduced by this contract.

An adapter must establish source identity, provenance, assessment applicability,
freshness and required-set coverage from its authoritative inputs. The model
validates consistency, **not authenticity**. A caller can fabricate a hash or
mislabel synthetic data; passing validation does not authenticate that data.
Canonical feature, evidence, policy and memory contracts remain their owners'
responsibility. This is not a portable memory graph or a new receipt selector.

## API and failure behavior

```python
from cc_project_map_model import (
    MapPolicy, MapValidationError, validate_map_bundle,
    build_map_projection, explain_map_node,
)

validated = validate_map_bundle(bundle)  # {"bundle": ..., "diagnostics": [...]}
projection = build_map_projection(validated["bundle"])
detail = explain_map_node(projection, "module-1")
```

All three functions accept an optional keyword-only `policy`. `MapPolicy` is
frozen; its positive integer bounds may only tighten the defaults. Each call
returns detached JSON-compatible data and leaves its input unchanged. No global
cache, environment, clock, filesystem, subprocess, network, database, target
imports or provider access is used. Observation time is caller supplied.

Invalid input raises `MapValidationError`, with stable `code` and `location`
attributes and a message that does not echo input values. Codes are `policy`,
`limit`, `type`, `cycle` (input containers), `text`, `fields`, `id`, `enum`,
`duplicate`, `path`, `version`, `time`, `coverage`, `identity`, `synthetic`,
`reference`, `lifecycle`, `granularity`, `containment`, `parent`, `relation`,
`containment_cycle`, and `projection`. Locations identify a schema area, not
necessarily a particular offending record. No partially valid model is returned.

Objects require **exactly** the documented fields, including nullable fields;
unknown fields are rejected. Only built-in dict/list/str/int/bool/null values
are supported. Custom container/scalar subclasses, floats (including NaN and
infinity), integer values outside signed 64-bit bounds, and cyclic input
containers are rejected. Integers and booleans are distinct. Arrays representing
ID sets reject duplicates. Record IDs are unique within their record category;
they are caller-owned stable identifiers, never generated from titles.

## Limits and lexical locators

| Policy field | Default ceiling |
| --- | ---: |
| `max_input_bytes` | 2,097,152 |
| `max_output_bytes` | 786,432 |
| `max_nodes` | 2,000 |
| `max_edges` | 8,000 |
| `max_sources` | 4,000 |
| `max_source_refs` | 4,000 total references across nodes, edges, assessments and constraints |
| `max_assessments`, `max_constraints` | 4,000 each |
| `max_depth` | 24, root at depth 1, including object keys |
| `max_values` | 200,000, including containers and object keys |
| `max_id`, `max_title`, `max_path`, `max_text` | 128 / 160 / 1,024 / 4,096 characters |

An iterative preflight bounds depth, values, strings and compact JSON UTF-8 bytes
before JSON copying or graph processing. JSON escaping counts toward the byte
limit; object-key ordering does not affect size. Every public result, including
the validation wrapper and explanation, must also fit the output bounds.
Consequently an input below the input ceiling can still fail the output ceiling.
There is no silent truncation or automatic increase. Clients must request a
smaller explicit scope. Graph traversal is iterative and does not use Python
recursion for deeply nested work/structure chains.

IDs match `[A-Za-z0-9][A-Za-z0-9_.:-]*`. Text is nonempty unless explicitly
nullable. Text rejects ASCII/C1 control characters and invalid Unicode. Reasons
use `max_text`; titles and document fragments use `max_title`. Text, including
markup, is data: consumers must render it as text, never evaluate it as HTML.

A relative path is a nonempty forward-slash path, at most `max_path` characters.
Absolute paths, backslashes, colons (drive and alternate-stream syntax), empty,
`.` or `..` segments, control characters, and segments ending in dot/space are
rejected. A locator is `{path, fragment}`; `fragment` is a nonempty bounded string
or null and is an opaque document anchor, never evaluated. No URI decoding,
glob expansion, case folding, device-name interpretation or path resolution
occurs. **Lexical validation is not filesystem confinement**; trusted future
readers must separately address symlinks, reparse points and platform semantics.

## Input envelope

| Field | Required value |
| --- | --- |
| `schema_version` | Integer `1` |
| `mode` | `observation` or `demo` |
| `project_id`, `snapshot_id` | IDs |
| `observed_at` | Valid UTC calendar timestamp `YYYY-MM-DDTHH:MM:SSZ`, seconds required |
| `coverage` | Object below |
| `selection` | Object below |
| `sources`, `nodes`, `edges`, `assessments`, `constraints` | Arrays of the exact record types below |

All node references are local to this envelope/project; undeclared external
nodes fail validation. `coverage` has `state` (`complete`, `partial`, `unknown`),
`scope` (unique node IDs), `omissions` (bounded reason strings), and `reason`.
Scope/omission arrays have at most `max_nodes` entries. Omissions are sorted and
deduplicated. `complete` cannot carry omissions. Completeness is a caller claim
relative to the declared scope, never evidence about an entire repository.

`selection` has `kind` (one allowed node kind) and `nodes` (unique node IDs, at
most `max_nodes`). Every selected node must belong to `coverage.scope` and have
the specified kind. This makes summary granularity explicit: files and symbols,
or services and tasks, cannot enter the same completion count. No descendant
expansion, LOC weighting or inferred selection occurs.

## Source records

Exact fields: `id`, `owner`, `locator`, `identity`, `adapter`, `resolution`,
`reason`, `synthetic`.

- `owner`: `core`, `feature`, `evidence`, `policy`, `design`, `code`, or `user`.
- `locator`: the lexical `{path, fragment}` object above.
- `identity`: lowercase `sha256:` followed by 64 hexadecimal digits, or null.
- `adapter`: bounded ID identifying the adapter and its contract/version.
- `resolution`: `resolved` or `unresolved`; unresolved requires a null identity.
- `reason`: nonempty bounded explanation, including why identity is unavailable.
- `synthetic`: boolean; true is accepted only in demo mode.

All source-reference arrays must refer to declared source IDs. An unavailable
source must be represented explicitly, never silently dropped. Unresolved or
identity-less sources generate `source_unknown` diagnostics and cannot support
verified acceptance or an effective observed constraint decision.

## Node records

Exact fields: `id`, `kind`, `title`, `presence`, `origin`, `mapping`, `sources`,
`lifecycle`, `plan`, `criteria`, `reason`.

| Dimension | Allowed values |
| --- | --- |
| Structural `kind` | `system`, `subsystem`, `service`, `application`, `module`, `component`, `file`, `symbol` |
| Work `kind` | `feature`, `phase`, `task`, `milestone`, `criterion` |
| `presence` | `intended`, `observed`, `both`, `missing`, `unknown` |
| `origin` | `design`, `code`, `ai`, `user`, `adapter`, `synthetic` |
| `mapping` | `proposed`, `confirmed`, `rejected`, `conflicted` |
| `plan` | `absent`, `draft`, `detailed`, `approved`, `stale`, `unknown` |

`sources` is a unique source-ID array; empty means provenance is unavailable.
`criteria` is a unique array of criterion-node IDs, bounded by `max_nodes`.
Criterion nodes must have an empty criteria array; criteria cannot recursively
depend on acceptance of other criteria. `title` and `reason` are required text.
`synthetic` node/edge origins are rejected in observation mode.

`lifecycle` has exactly `owner` and `state`. Owner `adapter` allows display states
`unknown`, `planned`, `active`, `review`, `accepted`, `aborted`. Owner `feature`
is valid only for feature nodes and preserves the existing feature-engine states:

| Feature state | Display delivery | Additional interpretation |
| --- | --- | --- |
| `planned` | `planned` | Reported planning |
| `active`, `verifying` | `active` | Raw state retained |
| `passing` | `review` | Not acceptance |
| `blocked` | `active` | `feature_reported_blocked` impediment |
| `completed` | `accepted` | Reported acceptance; evidence still required |
| `aborted` | `aborted` | No verified acceptance |

Unknown owner states are errors, never defaults. Plan status, presence, mapping,
delivery, verification and constraints remain separate. Confirming an AI mapping
does not change any source lifecycle, assessment or permission.

## Edge records and containment

Exact fields: `id`, `source`, `target`, `relation`, `origin`, `mapping`, `sources`.
Both endpoints must be declared node IDs. Origin, mapping and sources follow the
node rules. Allowed relations:

| Relation | Endpoint rule / meaning |
| --- | --- |
| `contains_structure` | Structural parent -> structural child |
| `contains_work` | Work parent -> work child |
| `implements` | Structural node -> work node; many-to-many |
| `depends_on` | Dependent -> prerequisite node |
| `verified_by` | Node -> criterion node; navigational |
| `constrained_by`, `references`, `supersedes` | Node -> node; navigational |

Each containment tree allows at most one incoming primary-parent edge per child
and no cycles, including self-loops. Proposed/rejected/conflicted relations are
retained with their review state and undergo the same structural validation;
they do not become confirmed by appearing in the tree. A caller must resolve
competing primary-parent candidates before submitting this model. The model
does not impose a fixed rank ordering on structural kinds or work kinds.

Dependency cycles are retained intact and reported as sorted strongly connected
components, including self-dependencies. No false topological ordering is
invented. Other navigational cycles are allowed. `verified_by` is not evidence
and does not add required criteria: the node's explicit `criteria` array is the
acceptance mapping. `constrained_by` is not an enforcement rule: the explicit
constraint records carry scoped observations. Review/source uncertainty stays
visible in each relation; relations carry no independent claim of health.

## Assessment records and verified acceptance

Exact fields: `id`, `subject`, `sources`, `outcome`, `freshness`, `applicability`,
`sufficiency`, `coverage`, `reason`. Subject is a declared node ID.

| Field | Values |
| --- | --- |
| `outcome` | `not_run`, `partial`, `passed`, `failed`, `incomplete`, `unsupported`, `unknown` |
| `freshness` | `current`, `stale`, `unknown` |
| `applicability` | `applicable`, `not_applicable`, `unknown` |
| `sufficiency` | `complete`, `subset`, `unknown` |
| `coverage` | `complete`, `partial`, `unknown` |

Adapters supply their selected assessment, not raw commands or receipt history.
A subset pass is `outcome: passed, sufficiency: subset`, never a full gate pass.
The model does not select a later favorable receipt to erase a failure.
All assessments except explicitly `not_applicable` ones participate. At least
one must participate, and **all** participating assessments must be passed,
current, applicable, complete in required-set sufficiency and coverage, with
nonempty known source identities. Unknown applicability prevents sufficiency.
Outcomes and freshness are retained separately in the original records.

`verified_accepted` requires reported delivery `accepted`, presence `observed`
or `both`, mapping `confirmed`, nonempty resolved identified node sources, and
sufficient current evidence. Non-criterion nodes additionally need at least
one mapped criterion, and every mapped criterion must itself be verified
accepted. Evidence alone, file existence, an approved plan, or mapping approval
is insufficient. The result is acceptance **within the supplied evidence
scope**, not a universal code-health verdict or an authorization to edit.
Dependency findings and protected status remain independently visible.

Reason codes explain the failed conditions: `delivery_not_accepted`,
`implementation_not_observed`, `mapping_not_confirmed`, `source_identity_unknown`,
`verification_insufficient`, `criteria_unmapped`, `criteria_unsatisfied`.

## Scoped constraint records

Exact fields: `id`, `subject`, `kind`, `operation`, `scope`, `rule`, `sources`,
`applicability`, `coverage`, `stage`, `decision`, `host`, `context`, `reason`,
`responsible`, `condition`.

- `subject`: declared node ID. `kind`: `policy`, `feature_perimeter`, `approval`,
  or `gate`. These types do not create global precedence rules.
- `operation`, `rule`, `host`, `context`, `responsible`, `condition`: nonempty
  text bounded by `max_text`; `condition` describes what is needed to proceed.
- `scope`: one exact lexical relative path. No globs, recursive inheritance,
  active-module identity inference or expansion from a label occurs.
- `sources`: at least one declared source reference.
- `applicability`: `applicable`, `not_applicable`, `unknown`.
- `coverage`: `supported`, `unsupported`, `conflicting`, `unknown` for the named
  host/context, not repository scan completeness.
- `stage`: `configured`, `predicted`, `observed`.
- `decision`: `allow`, `deny`, `approval_required`, `unknown`.

An `effective_observation` repeats the supplied decision only for an observed,
applicable, supported record with known source identities. Otherwise it is
`unknown`; the full configured/predicted/unsupported record remains available.
All records are retained, even opposing decisions. No merge algorithm infers
global permission, a bypass, a denial on another operation, or propagation to
children. Even an observed `allow` is historical data, **not permission**.
A verified accepted component may remain policy protected. Failed verification
must be represented by the evidence adapter, not inferred from a policy label.

## Projection, summaries and explanations

Projection fields are exactly `schema_version`, `canonical`, `mode`, `bundle`,
`statuses`, `summary`, `diagnostics`. The normalized bundle preserves source
observations. Records and ID arrays are sorted lexicographically; object keys
are sorted. Reordering input records does not change the projection.

Each status has `id`, `presence`, `mapping`, `plan`, `delivery`,
`reported_lifecycle`, `verification` (`sufficient_current`, `assessments` IDs),
`impediments`, `constraints` (objects with `id`, `effective_observation`),
`verified_accepted`, `reasons`. Detailed evidence/freshness and constraint reasons
are available from the associated bundle records or node explanation.

The summary has `kind`, `units`, `verified_units`, `criteria`,
`verified_criteria`, `coverage`, `declared_scope_verified`. Selected units are
counted once; their shared criteria are counted once. When selecting criterion
nodes, those nodes themselves form the criterion set. No percentage is produced
for consumers to confuse with a whole-project completion claim.
`declared_scope_verified` requires a nonempty selection exactly equal to the
declared scope, complete coverage and all selected units verified accepted.
Partial/unknown scope coverage prevents that flag, while individual bounded
observations remain visible. This flag does not assert absence of dependency
cycles, architectural issues, god files or broken imports: this slice has no
analyzers for those conditions.

Diagnostics have exactly `code` and `members`: `source_unknown` names one source
ID; `dependency_cycle` names all nodes in one cycle component. Members and
diagnostics are sorted. Other malformed references are fatal, not diagnostics.

`explain_map_node` first validates and recomputes the supplied projection;
tampered derived fields fail with `projection`. Result fields are
`schema_version`, `canonical`, `mode`, `node`, `status`, `ancestors`, `edges`,
`criteria`, `assessments`, `constraints`, `sources`, `diagnostics`, `coverage`.
Ancestors run from immediate parent to root in the supplied candidate tree;
their IDs do not imply confirmed mapping. Edges are all incident original
relations. Criteria contain their derived statuses. Assessments/constraints
belong to the requested subject. Sources cover the node, incident edges,
assessments, constraints and directly mapped criterion nodes. Diagnostics are
filtered to the node or these source references. It is a finite one-node view,
not recursive expansion of every dependency or criterion's evidence history.

## Minimal illustrative input

This demo describes an intended module with no implementation or evidence. It
is accepted as input and remains unverified. It does not inspect this repository.

```json
{
  "schema_version": 1,
  "mode": "demo",
  "project_id": "example",
  "snapshot_id": "design-1",
  "observed_at": "2026-09-21T10:00:00Z",
  "coverage": {"state": "partial", "scope": ["module-1"], "omissions": ["Code has not been observed"], "reason": "Design-only proposal"},
  "selection": {"kind": "module", "nodes": ["module-1"]},
  "sources": [{"id": "design", "owner": "design", "locator": {"path": "docs/design.md", "fragment": "module"}, "identity": null, "adapter": "example-v1", "resolution": "unresolved", "reason": "Illustrative design reference", "synthetic": true}],
  "nodes": [{"id": "module-1", "kind": "module", "title": "Proposed module", "presence": "intended", "origin": "ai", "mapping": "proposed", "sources": ["design"], "lifecycle": {"owner": "adapter", "state": "planned"}, "plan": "draft", "criteria": [], "reason": "Awaiting mapping review and implementation"}],
  "edges": [],
  "assessments": [],
  "constraints": []
}
```

Later readers must establish actual observations before this model can feed a
live grid, ownership roadmap, constraint overlay or AI mapping proposal workflow.
Desktop integration and persistence are separate implementation slices.
