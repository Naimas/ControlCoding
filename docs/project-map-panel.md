# Project Map desktop observer

CC-MAP-03 connects the optional desktop panel to the
[bounded source observer](project-map-sources.md) and
[pure map model](project-map-contract.md). This is an implemented development
candidate, not a released installer or full project-management system. Core
commands continue to work without it.

CC-MAP-04 adds [reviewed mapping choices](project-map-definition.md): confirm,
reject, label rename, explicit source relink, and group/split/merge. Every save
requires a concrete preview and matching source/definition revisions. These
choices change only the map definition, never code, completion or permissions.

CC-MAP-05 adds [canonical control observations](project-map-controls.md) through
a separate explicit preview/read: feature lifecycle, project gate assessments,
module perimeters, protected operations and scoped pending approvals.

CC-MAP-06 adds [quality and dependency analysis](project-map-analysis.md) through
its own preview/read: bounded Python and JS/TS syntax measurements, duplicate and
cycle review findings, explicit architecture rules, expected-file comparisons
and existing report links. Quality markers stay independent of completion.

## Using the map

Open a project with the panel's native folder chooser and select **Project Map**.
Navigation alone does not scan code. Choose **Preview read scope**, inspect the
listed bounds/exclusions, then **Observe project**. Optionally use **Select
design documents** to choose up to 32 Markdown/text files inside that root;
selection invalidates the old map and prepares a new scope preview. Excluded
private/hidden documents remain unsupported. Clear documents to return to code
inventory alone.

The three perspectives share the selected element:

- **Roadmap & ownership:** collapsible proposed software containment, from the
  root through folder modules, files and Python symbols. Work phases, owners and
  dates are explicitly unobserved until an authoritative adapter supplies them.
  Filesystem containment is not a delivery schedule or a confirmed service map.
- **Architecture & delivery:** rows identify software elements; columns separate
  observed presence, plan, delivery, verification and controls. The table scrolls
  inside its boundary at narrow widths. No fabricated waterfall stages are added.
- **Code completion:** one square per file or per Python symbol, grouped by the
  nearest module. Switching granularity changes the unit count; it never mixes
  file and symbol completion denominators. Equal squares do not imply equal work.

Hover or focus a square for a popup explaining its element and status. Click,
tap or Enter pins the inspector. The inspector shows ownership breadcrumbs,
status reasons, source references/hashes, static metadata, typed related links
and scoped controls when supplied. Escape dismisses popup/selection. Arrow keys,
Home and End switch the perspective tabs. Ordinary keyboard navigation reaches
units, tree toggles, filters and details. Search filters titles, not source bodies.

Green means sufficient current verified acceptance in the model; purple review,
blue active, amber stale, red relevant current failure and neutral unverified.
Text/symbols and the inspector accompany color. The current source reader returns
**proposed mappings, unknown delivery, partial coverage and zero verified units**.
Ordinary file presence never fills a square green. The legend describes states;
it is not evidence that those states exist in a selected project.

Choose **Preview controls & evidence**, then **Observe controls & evidence** to
add canonical signals. Reported feature completion remains separate from verified
code; reported blockers appear in the tree and matrix. L/A/P grid markers show
predicted restrictions, scoped approval requests and perimeter configuration.
Empty constraint arrays display **Not assessed**, not an assertion that editing
is allowed or nothing is blocked. Host enforcement remains unverified.
Mapping acceptance is separate from these controls. No permission grant, unlock,
source edit or execution button exists.

## Refresh, scope and failure

**Refresh map** obtains a fresh bound preview for the same selected documents
and repeats observation. The top Refresh button follows the current page: on
Project Map it previews an unprepared scope or refreshes a prepared map. Changing
documents requires reviewing the new scope before reading. The preview timestamp
is main supplied and remains part of that scope digest; the helper response time
labels when the operation ran. The panel never uses either as evidence freshness.

While observing, the previous map is cleared. Failure, timeout or cancellation
leaves no apparently current old map. A project switch clears map, scope, document
selection and view selection; late old-generation replies are dropped. A failed
setup observation does not masquerade as a map result. Setup and map observations
have separate result/error/time fields while sharing one owned helper at a time.
The map's 60-second age reminder is a request to refresh, not a file watcher or
an assertion that younger data still matches disk. There is no automatic refresh,
cache, observation history or persistence of source bodies. Reviewed mapping
choices alone persist through the separate preview/save workflow.

Compact mode shows summary and an Expand action; full linked views are in expanded
mode. Both themes use the existing panel palette. Matrix content can scroll
horizontally within its own panel; page-level horizontal overflow is not intended.
Screenshots/zoom tests are renderer tests, not screen-reader certification or
coverage of all Windows system-scale settings.

## Private transport and IPC

The observation renderer calls no-argument map methods exposed by preload:
`mapPreview`, `mapRead`, `mapRefresh`, `mapCancel`, `chooseDesign`, `clearDesign`.
Mapping review additionally exposes bounded `reviewMapping(change)` and
no-argument `saveMapping()` / `discardMapping()`; see the definition contract.
It cannot submit root paths, document paths, executable names, arbitrary reads,
commands or evidence verdicts. Electron main owns the selected root and native
document dialog result. Main verifies document containment before deriving
relative paths; the Python reader independently rejects unsafe/excluded content.
Every IPC handler retains exact webContents/main-frame/local-URL checks and
argument validation. Navigation, remote requests, popups, downloads and Node in
the renderer remain disabled. All project strings are rendered as React text.

Existing setup messages and operations remain unchanged. Map messages require
exact fields:

| Operation | Fields in addition to `version:1`, `id`, `operation`, `project_root` |
| --- | --- |
| `map_preview_v1` | `design_paths`, `observed_at` |
| `map_read_v1` | `design_paths`, `observed_at`, `preview_id` |
| `map_review_preview_v1` | Read fields plus `snapshot`, `revision`, `change` |
| `map_review_apply_v1` | Preview fields plus `approval_id` |
| `map_controls_preview_v1` | Read fields plus `snapshot`, `revision` |
| `map_controls_read_v1` | Controls preview fields plus `scope_id` |
| `map_analysis_preview_v1` | Read fields plus `snapshot`, `revision`, nullable `controls_scope_id` |
| `map_analysis_read_v1` | Analysis preview fields plus `scope_id` |

Observation time is a UTC timestamp with seconds. The Python bridge uses project
ID `selected-project`; source identity still binds the actual retained root and
relative locators. The source adapter returns a preview digest of the exact
normalized scope/policy. Reads require the corresponding lowercase SHA-256
digest. It is consistency checking, not identity authentication or an approval
token. Unsupported versions/fields are rejected.

Success replies retain envelope `version`, `id`, `operation`, `observed_at`,
`status` and `result`. Result contains `project_root` plus `scope` for preview or
`map` for observation, `review_preview` for a mapping diff, or `review_saved` for
a completed definition write. Errors are correlated safe code/source records without
raw exception or source text. The client validates identity, root, operation,
map version/observation mode/noncanonical marker and consumed payload shape.
Analysis preview returns `analysis_scope`; read adds an `analysis` projection.
Its no-argument preload calls are `analysisPreview()` and `analysisRead()`.
An already-previewed controls scope may be included; no renderer runtime/path
arguments are accepted. Analysis can launch only its installed trusted parser
worker, which receives retained text and never executes project code.
Demo responses cannot enter the real map view.

The isolated helper still uses an absolute trusted Python/script path, `-I -B`,
array arguments, no shell, a bounded environment and a trusted working directory.
Limits remain 64 KiB request, 1 MiB reply plus newline, 16 KiB discarded stderr,
15-second owned subprocess timeout and one-second predecessor-reap guard. The
whole map payload also remains at most 768 KiB compact UTF-8. Map JSON uses UTF-8
without ASCII expansion so the bound remains meaningful for Unicode filenames.
No HTTP/WebSocket server, provider, project process or command dispatcher is added.

## Verification and limitations

Python bridge tests exercise real isolated processes and unchanged model/source
regressions. Node tests cover state clearing, generation changes, preview binding,
invalid helper output and inherited process bounds. The Electron map smoke uses
the actual BrowserWindow, renderer and Python reader on external fixtures; only
the native chooser result is stubbed. It compares fixture identities, exercises
three views, hover/focus/click/keyboard, both themes, zoom/compact layout and safe
failure, then captures real screenshots. General panel controls retain their
separate smoke test.

The source reader's lexical/identity/race/parser limitations still apply. No
signed packaging, installer, physical global-key/tray delivery, POSIX execution,
full accessibility, real-project health, AI proposals or policy enforcement is
established by these UI checks. This slice introduces no canonical owner changes,
source writes or project setup. MAP-04 persists reviewed mapping choices through
its separate definition contract. MAP-05 controls have their own broader read
scope, limits and canonical-owner semantics; see the controls contract. Normal
map refresh clears their observation and requires a new controls preview/read.
MAP-07 adds an explicitly enabled session-only automatic refresh route which can
repeat already read, unchanged scopes. It observes file/Git metadata hints and
reconciles periodically, preserves surviving layout/selection, and keeps bounded
historical summaries. See [refresh and history](project-map-refresh.md) for
commit-ceremony semantics, read authorization, failure behavior and limits.
