# ControlWork desktop memory

The optional desktop **ControlWork** section reads the existing embedded Project
Plane. It replaces the Memory placeholder. The separate **Manage project memory**
workflow adds reviewed portable archive writes; see [management contract](controlwork-management.md).
Opening this section does not initialize either product. The saved internal `Memory` preference remains
compatible. Standalone ControlWork is a separate product and is not modified.

## Workflow and views

Choose a project, open ControlWork, select **Preview memory scope**, then
**Read project memory**. Navigation and scope preview do not read memory bodies.
The preview names the paths and content limits. Reading exposes local text in
this desktop session; external references are displayed as inert text and never
followed. No network, provider, model or external application is invoked.

- Overview: observed document/session/edge counts, review labels and coverage.
- Documents: source text previews, recorded category/lifecycle and relative paths.
- Connections: interactive keyboard-accessible graph and source inspector.
  Solid lines are recorded edges; dashed lines are unaccepted topic suggestions.
  The diagram shows up to 36 records; the complete captured node list remains
  accessible. Rejected suggestions and audit-only history remain distinguishable.
- Sessions: explicit session summaries, decisions, notes, references and follow-ups.
  Sessions may also hold explicit user/assistant notes from opted-in panel
  conversations. External host chats are not automatically collected.
- Decisions: a view of the canonical memory decisions area.
- Search & RAG: the Core graph retriever ranks titles, headings and metadata,
  then builds a cited context packet in memory. This is not full-text semantic
  search or a generated answer. Legacy/superseded records are excluded by default.

Labels express recorded state, not independently verified truth, project
completion or installation health. Sources/ingestion freshness are not assessed.
The Dev Plane SQLite store, source-file index, categories registry, external
documents, wiki views and application-owned RAG are outside this read scope.
Absent memory has a dedicated state; opening it creates no archive or database.

## Read and provenance contract

`scripts/cc_controlwork_observer.py` owns the bounded snapshot adapter. It reads:

- `CONTROLWORK.md`;
- direct Markdown records in `.controlwork/memory/{inbox,sources,notes,ideas,decisions,plans,outputs,legacy}`;
- direct JSON records in `.controlwork/sessions`;
- direct Markdown checkpoints and context packets;
- `.controlwork/graph-suggestions.json` review history.

The adapter reuses pure entry, graph, retrieval and packet functions extracted
from `cc_memory_lib/work_features.py`; existing CLI wrappers keep their file-based
behavior and schema. Core entry order is preserved because suggestion IDs bind
ordered endpoints. Extra checkpoint/packet records extend this observation only.
Rejected and audit-only suggestions do not contribute to desktop retrieval;
the existing standalone/CLI retrieval behavior is not changed by this increment.

The retained source reader rejects linked directories/files, hardlinks, special
entries, unsafe roots and concurrent source changes. Directory membership is
bounded and rechecked. Limits: 64 KiB per source, 4 MiB aggregate, 96 captured
files, 512 enumerated direct entries, 256 retained entries including ancestors,
512 generated chunks and 768 KiB response projection. Text previews show at most
8,000 characters, with an explicit truncation notice. Entire-record limits fail
closed; there is no silently incomplete inventory. The adapter checks an
eight-second cooperative budget; the helper also has a 15-second process limit.

Scope identity binds the selected root identity, fixed paths and policy. Content
identities are separately included in the observed snapshot. Each search/refresh
rereads the authorized scope; it does not certify continuing freshness. Unsupported
or malformed session/review JSON produces a safe error without raw source text.

## Desktop boundary

Private operations are `work_preview_v1` and `work_read_v1`; read adds only a
main-owned scope digest and a text query of at most 240 characters. Main owns the
selected root. The renderer cannot supply arbitrary paths or run CLI commands.
Portable writes use a separate bounded preview/commit contract. Existing exact-sender, isolated Python, output and timeout guards
remain in force; success response shape is validated before becoming UI state.

Project switches invalidate old requests and clear knowledge. Failed reads clear
previous results. Query/body text is not stored in activity or preferences.
Rendering is plain React text; HTML-like documents do not become executable HTML.
Read results are held only for the current desktop session. Explicit portable
archive writes persist through Manage project memory and then refresh this scope.
This section does not offer a code scan, lifecycle promotion, graph approval,
Dev Plane database initialization or provider calls from this section. Those
operations are available separately in the [execution workflows](panel-execution.md).

## Repository documentation circuit map

ControlWork also exposes **Explore the project's knowledge**, a read-only map
of existing Markdown files alongside explicitly observed portable archive
records. A repository can contain documentation even when `.controlwork` has
not been initialized. Source-file, archived-document and recorded-session counts
are separate; an unobserved count is a dash, not a claimed zero. Handoffs are
documents, not reconstructed conversations.

Use **Read document map** with one of three fixed scopes:

- Root Markdown (excluding `AGENTS.md`) plus `docs/` recursively.
- Local `_work/plans/*.md`.
- Local `_work/handoff/*.md`.

Each scope is a separate bounded observation, not a whole-project inventory.
The viewer does not copy or import these sources into memory and does not add
them to the existing RAG index. Read the archive's separate scope below to add
saved documents and session summaries to the map. Sources with the same path
as an observed archive record are deduplicated in the graph.

The circuit is one continuous canvas: macro areas contain named topic hubs,
with plain, fully titled document rectangles radiating around each central hub
in concentric rings. Decorative pins and silicon icons are not rendered.
Examples include Development plans, Project Map and Memory & GraphRAG. Nothing
is replaced by a separate record page. Topics come from leading `cc-topic`
metadata, a recorded archive category, or whole subject terms in the title or
filename. Recognized title evidence takes precedence over filename evidence:
a retained historical roadmap filename cannot override a current verification
reference heading. Specific subject rules outrank generic interface/schema terms; ties
and unsupported subjects stay in **Needs classification**. Folder names alone
do not establish a subject. Selecting a topic immediately opens a member panel inside the map, with its
purpose, exact count, original document titles and project-relative file paths.
Each row exposes its membership reason. Linked documents outside the group are
listed separately and do not contribute to its member count. The list remains
available when a member is selected or read; a single click, Enter or Space opens its original file in the formatted reader. On compact screens the panel occupies the lower
part of the map with its own scroll area. The inspector allows
a topic assignment for this view. Suggestions are deterministic rules, not AI
classification or certification of the document's contents. Topic membership is
organization, not a newly inferred dependency. There is no eight-document limit
per topic; all current observed records remain on the canvas.

Use pointer-centered wheel/trackpad zoom, drag pan, chip focus, keyboard arrows,
plus/minus, Home and the minimap. Approaching a chip reveals labels and detail
in place. Search dims non-matching chips and the source navigator provides a
complete scrollable list. Selection and refresh preserve the camera where
identities survive. Six labelled macro-area colors propagate to their contained
chips and tracks; none uses the green selection color. Selecting an area or
topic highlights its members and their directly observed neighbors in
green. Single-clicking a document selects it and immediately opens the formatted reader
without moving the camera. Enter and Space also open it for keyboard users. The source navigator uses the same gestures.
This is one-hop selection, not transitive reachability or a completion status.
Unrelated chips, groups and tracks dim; **Dim unrelated** disables that treatment
without discarding the selection. **Clear selection** and **Overview** restore
the full context. Dimmed records remain selectable.

Hover temporarily shows only the hovered document's membership edge and direct
observed references, in green; all other tracks are hidden. Hovering a topic or
macro area shows only its membership and directly connected references. Leaving
restores the previous selection and display settings without moving the camera.
The default view shows topic stars; references appear for the selection/hover.
**All document references** restores the complete bounded reference overlay.
Light trails are off initially and remain optional.

Tracks use distinct endpoint ports and rounded orthogonal corridors checked
against chip/header obstacles. Routing penalizes shared segments, and dark casings
make remaining crossings distinguishable. A dense graph is not guaranteed to be
crossing-free. Crowded routes without a clear candidate are explicitly counted
and left undrawn instead of crossing chip labels. At lower zoom, reference tracks
aggregate actual cross-topic references. Higher zoom shows document links,
bounded to 240 reference routes with selected relationships prioritized. The HUD
reports display omissions; the individual record inspector retains all observed
neighbors. Routing changes with the displayed scope; containment remains visually
and semantically separate from captured references.
Macro areas retain archive lifecycle and explicit local-scope categories. For
repository files outside the explicit local plans scope, current recognized
subjects can correct a Plans area inferred solely from a historical filename.
The display and plan count use that correction; observed source records are not
modified. Classification does not establish completion. The inspector shows bounded source text, provenance and content hashes.

Markdown inline references are resolved only against already observed file paths.
External URLs and out-of-scope targets are never followed. Unresolved references
may simply be outside scope; they are not labelled broken. Reference-style links,
anchors and semantic relationships are outside this adapter. Unaccepted archive
suggestions do not become recorded links. Optional luminous trails are explicitly
a visualization effect over grouping/reference tracks, not live ingestion, wiki
maintenance or model telemetry. At most 36 trails animate; the toggle pauses
them and reduced-motion preferences suppress them and camera easing.

**Refresh document map** detects additions, edits and removals. Optional polling
rereads the selected source scope every 30 seconds while ControlWork is mounted
and the window is visible, skipping busy operations. It stops on scope selection,
project change or leaving the page. This is session-only polling, not an app-closed
watcher or commit-ceremony integration. Archive records refresh separately.
Stable source IDs preserve selection; failed refreshes clear source results.

The separate [unified knowledge coordinator](unified-knowledge.md) now adds
persistent ingestion, conversation continuity, wiki revisions, neural retrieval
and optional app-closed maintenance. Its approved source catalog also populates
this circuit, including saved conversation and Dev record nodes. Selecting a
real Markdown source opens the original formatted reader; selecting a projected
record opens its source-bound wiki document. The original observer mode remains
available when persistent memory has not been enabled.

Private `documentation_read_v1` accepts only a fixed scope token and the
main-owned project root. The isolated reader retains the existing path protections
and enforces 200 documents, 256 KiB per file, 8 MiB aggregate, 256 retained entries
including ancestors, 4,096 enumerated entries, five recursive docs levels,
2,000 references, 1,200-character excerpts, 768 KiB output and an eight-second
cooperative limit. The existing helper process timeout also applies. Unsupported,
changed or oversized inputs fail visibly without a partial-success inventory.
No provider, installation, project initialization or new persistent database is
required by this viewer.

## Latest documents and revision history

An unambiguous replacement family shows only its latest observed document chip.
Its small history badge opens the same reader with a revision dropdown, slider,
and Newer/Older controls. Earlier files remain intact and load through their own
observed IDs and hashes. Historical reads are labelled and preserve the camera.
Original counts include historical files; the map reports how many are folded.
References belonging to an older file are not reassigned to the latest revision.

Automatic grouping uses recorded `supersedes` / `superseded_by` archive edges,
explicit `cc-supersedes` project-relative paths, or a named `cc-series` with unique
positive integer `cc-revision` values. For example:

```markdown
---
cc-topic: Development plans
cc-series: Delivery plan
cc-revision: 3
---
# Delivery plan
```

These fields must fit a complete leading frontmatter block in the already
observed excerpt (1,200 characters for repository documents). Values are plain
or quoted scalars; `cc-supersedes` also accepts a JSON array of up to 32 paths.
The viewer does not run a general YAML processor. A series name identifies one
family across the selected observation, so unrelated families need distinct names.
Missing targets, branches, cycles and duplicate revision numbers keep the affected
family expanded with a notice. Filename patterns such as `design-v1.md` and
`design-v2.md` are only suggestions: **Group as revisions in this view** requires
an explicit choice tied to the observed file identities. Dates and similar titles
alone never collapse documents.

Topic overrides and filename confirmations are session-only and reset on project
change. Refresh advances declared families when a newer revision enters the
observation. This is observed-file history, not retrieval of deleted files or Git
history. No project document is changed or imported into an archive by grouping.

## Full document reader

Click a Markdown document rectangle, member-list row or source-navigator entry
to immediately open its formatted reader. Enter and Space also open it. Selecting
a topic opens its member list; hover only highlights connections. Inspector
reference entries open their documents in the same reader. Explicit Open buttons and
the history badge remain available.
After closing it, **Open selected document** on the circuit or **Open document**
in its inspector reopens it. A side panel opens over the same canvas;
closing it preserves camera and selection. The white paper surface formats
headings, paragraphs, emphasis, lists, quotes, fenced code, tables and images.
**Contents** opens a collapsible heading index with keyboard navigation and unique
anchors for duplicate headings. It reflects source headings, not AI-inferred
concepts. **Markdown source** shows the complete decoded UTF-8 source, including
content beyond inventory excerpts. Observed local document links open in this
panel; external links remain labelled, inert targets. The reader does not edit
files or generate a PDF. Declared ControlCoding frontmatter appears in a separate
Source metadata disclosure; Markdown source still includes every original byte
decoded as UTF-8. Recorded JSON sessions keep their existing inspector.

The renderer sends only an observed record ID and project generation. Main owns
its path/hash, parses image references from verified source, and rejects late
responses after a project switch. Private `document_read_v1` checks the observed
SHA-256 using descriptor-backed snapshots; edited/deleted sources require a map
or archive refresh. A page already open remains that version and warns if a new
map observation differs. Full text and images are not added to global snapshots,
preferences, project memory or provider context.

Limits: 256 KiB UTF-8 Markdown, 30,000 formatting tokens, nesting depth 20, and
24 distinct image references / 384 KiB combined image bytes. Local PNG, common
JPEG, GIF, nonanimated WebP and restricted SVG are supported. Raster dimensions
are bounded to 8,192 per axis and 16 million pixels. Missing, unsupported,
oversized or remote images have visible placeholders. Images cannot escape the
project through traversal, namespace/short-name aliases, symlinks, junctions or
hardlinks. SVG accepts a restricted static drawing vocabulary, with no scripts,
foreign content, CSS or external resources. HTML in Markdown remains literal;
nonstandard Markdown extensions are not interpreted. The versioned standalone
Markdown parser and license are documented in `ui/vendor/README.md`; rendering
uses React elements from an explicit allowlist, never injected HTML.

## Verification

`tests/test_cc_document_reader.py` and `ui/tests/document-reader-unit.cjs` cover
identity binding, confinement, unsafe images, budgets, complete source, parser
structure and project isolation. `ui/tests/document-reader-desktop.cjs` verifies
the real IPC/renderer path, formatting and image decoding, full source equality,
keyboard TOC, in-panel links, camera preservation and 320 CSS pixel layout.

`tests/test_cc_controlwork_observer.py` checks read preservation, Core compatibility,
scope binding, privacy, malformed records and source limits. Existing memory and
bridge regressions cover the extracted algorithms. `ui/tests/controlwork-unit.cjs`
checks protocol, state clearing and late responses. The real Electron harness
`ui/tests/controlwork-desktop.cjs` uses canonical external fixtures to exercise
navigation, text inspection, graph, sessions, RAG, inert HTML and all views at
320 CSS pixels. No production memory is initialized by these tests.

`tests/test_cc_documentation_observer.py`, `ui/tests/documentation-unit.cjs` and
`ui/tests/documentation-desktop.cjs` cover fixed source scopes, links, isolation,
read-only behavior, changed input, response contracts, source navigation,
visible-page polling and narrow layout for the repository map.
`ui/tests/knowledge-layout-unit.cjs` checks complete deterministic layout and
camera geometry. `ui/tests/knowledge-circuit-desktop.cjs` exercises a 120-document
fixture with native wheel/drag input, in-place cluster focus, pause/reduced motion,
camera/selection preservation and narrow layout.

`ui/tests/circuit-routing-unit.cjs` checks obstacle detours, separate fan-out ports,
coincident segments, deterministic routing and direct-neighbor selection without
transitive flooding. `ui/tests/circuit-selection-desktop.cjs` checks the six colors,
green relationships, area and keyboard neighborhood selection, reversible dimming,
deliberate document opening, camera preservation and 320px controls.

`ui/tests/knowledge-topics-unit.cjs` checks explainable grouping, replacement
chains, ambiguity, identity-bound confirmation and nonoverlapping radial geometry.
`ui/tests/topic-history-desktop.cjs` checks latest-only chips, keyboard history,
exact historical Markdown, refresh, project isolation and 320px revision controls.

`ui/tests/topic-interaction-desktop.cjs` verifies full wrapped titles, plain nodes,
per-member topic evidence, a quiet initial graph, native pointer hover showing
only incident green routes, leaving restoration, native single/double clicks,
stationary selection targets and keyboard select/open semantics.
