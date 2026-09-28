# ControlCoding Panel - desktop observer

PANEL-03 configuration development increment over PANEL-02 / CC-MAP-09. This
optional Windows desktop observes project structure and supports reviewed draft
save/Core application. It also offers the canonical Core installation/project wizard, explicit Checks
execution and provider conversations; see [execution scope](../docs/panel-execution.md). The accepted RC3
archive remains separate and unchanged.

For the separate unsigned Windows portable package, prerequisites, integrity
check and side-by-side update/removal, see [Observer distribution](DISTRIBUTION.md).
Core's release manifest still excludes this optional desktop distribution.

## Launch from this workspace

```powershell
.\ui\Start-Panel.ps1
```

The launcher builds local assets outside the checkout and opens the panel. Its
parameters select existing `-Python`, `-Electron`, `-Node`, `-Modules`, `-Output`,
and `-Profile` paths. `-Project` optionally selects an existing absolute folder.
`-Parser` names an optional installed Babel parser 7.29.7 package; this workstation
reuses the standalone dependency under `A:\AI Coding\cursor\resources\app\node_modules\@babel\parser`.
Only that MIT library is bundled, with its license, not editor application code.
An absent package explicitly leaves JS/TS parsing unavailable; no installation runs.
Defaults refer to the current workstation's installed runtimes and dedicated
external workbench. No dependency installation or project setup occurs on launch.
The launcher clears an inherited `ELECTRON_RUN_AS_NODE` flag for the child, then
restores its own environment. It does not change a global system setting.

The default development assets/profile live under
`J:\ProgettiAI\Workbenchs\ControlCoding_Workbench\panel-02-20260920-a`.
The underlying Electron entrypoint also supports a per-user profile at
`appData/ControlCoding Panel` when `--profile` is omitted. The profile contains UI
preferences, Chromium runtime data and project-specific AI role policies, never
canonical project setup configuration. Role policy files contain the physical
project identity, but no credentials, activity history or conversations.

## What you can use

- **AI role assignments:** separate local/API providers, model selection, context,
  limits and sending mode for seven roles, saved per project in the app profile.
  Chat roles also support manual prompt copy/paste with another chat, reviewed
  reply import, follow-up rounds and explicit return to the concierge.
  See [role configuration and authority](../docs/panel-ai-roles.md). These roles
  provide advisory responses or explicit embeddings; code execution requires a
  separate coding host.

- Open or change a project through the native folder picker.
- **Status:** bounded setup observation, counts and explicit coverage limits.
- **Project Map:** preview the read scope, optionally select project design
  documents, then observe code through the bounded source adapter. Linked
  roadmap, architecture matrix and file/symbol grid share element selection.
  Hover/focus explains a square; click/tap pins source/status detail. Refresh
  can be manual or explicitly enabled for the session, with bounded filesystem/
  Git notifications and 30-second reconciliation. Current inventory does not
  establish completion or policy delivery.
  See [Project Map desktop contract](../docs/project-map-panel.md).
- **Mapping review:** confirm/reject candidates, rename labels, explicitly relink
  missing sources, create code groups and split/merge them. Inspect the exact
  diff before saving `.controlcoding/project-map/definition.json`. Source and
  saved-definition conflicts preserve foreign changes. See the
  [definition and recovery contract](../docs/project-map-definition.md).
- **Setup:** direct choices, optional current-chat AI proposal handoff/review,
  project-local draft save and explicit Core installation after preview. See
  [guided configuration](../docs/panel-configuration.md) for conflicts, receipts,
  recovery and the remaining wizard scope. Host delivery remains unverified.
- **Quality & dependencies:** explicit static analysis of Python and supported
  JS/TS syntax, review findings, local import candidates/cycles, intended/observed
  comparison and report links. Amber ! markers do not change completion colors.
  See [analysis scope and limitations](../docs/project-map-analysis.md).
- **ControlCoding map signals:** separately preview and observe feature lifecycle,
  canonical project gates, module perimeters, protected operations and pending
  approval scopes. L/A/P markers remain independent of code completion color.
  Read [the controls contract](../docs/project-map-controls.md) for provenance,
  broader evidence input scope and unverified editing-host coverage.
- **Activity:** at most 100 actions from this panel, for the current project and
  session. External editor activity is not monitored.
- **Checks:** reviewed doctor, canonical verification status/run and invariants,
  with live progress, cancellation, exit status and retained logs. Project commands
  execute with user access; preview is not a sandbox.
- Pin/unpin, compact/expanded layout, minimize, hide to tray, show and exit.
- Recall shortcut: Ctrl+Shift+Space or Ctrl+Alt+C; unavailable registration is
  reported. The tray remains available when a shortcut conflicts.
- Dark/light theme and window/tab preferences; off-screen bounds are clamped to
  an available display. OS DPI rounding may slightly alter the requested size.

Closing the window hides it while a tray exists. **Exit panel** or the tray's
**Exit** stops the owned helper and quits, except while a reviewed write is in
progress. The panel never terminates editor,
provider or model processes. Pin defaults off. An observation older than 60
seconds displays a refresh recommendation; this is an age threshold, not a file
watcher or proof that newer observations still match disk. Project Map has its
own opt-in refresh and bounded session history; changed read scopes require
another explicit preview/read before those overlays resume.

**ControlWork** replaces the former Memory placeholder. Preview the explicit
local read scope, then inspect existing documents, decisions, session summaries,
checkpoints and saved context packets. The connections view uses the Core's
portable graph; search produces cited RAG packets in memory. See the
[ControlWork observer contract](../docs/controlwork-panel.md) for bounds and
coverage. **Manage project memory** previews and saves portable initialization,
selected Markdown/text imports, knowledge records and completed session summaries.
Successful saves refresh the read scope; see the [write contract](../docs/controlwork-management.md).
The full Core wizard separately offers governed Dev Plane initialization.
AI & Sessions can archive opted-in panel exchanges automatically.

The saved Agents tab now opens **AI & Sessions**: local Ollama or OpenAI API,
explicit outgoing context preview, advisory roles, optional exchange archival
and Setup proposal import/review. Keys stay in main-process memory. There is no
autonomous code editing or automatic external-host history collection. Manual
AI handoff remains available; Core works without this UI.

## Boundaries and protocol

The renderer uses React/TypeScript and local compiled assets. Electron enables
sandboxing and context isolation, disables Node integration, denies permissions,
downloads, new windows and navigation, and restricts asset/network requests to
the local allowlist. CSP blocks remote scripts, frames and connections. Project
strings are rendered as text; no raw HTML or Markdown execution is used.

The preload exposes snapshot, native project/document choice, setup read/preview,
map scope/read/refresh/cancel, document-selection clearing, window controls,
constrained preferences and state subscription. Mapping review/save/discard
methods are separately bounded; no arbitrary definition or root comes from the
renderer. Every main
handler validates the sender, main frame, exact local URL and arguments. The
renderer cannot supply a root, executable, shell command or arbitrary file path.

For each read/preview, main starts the absolute configured Python executable
with `-I -B` and the trusted `scripts/cc_panel_bridge.py` path. It uses argument
arrays, `shell: false`, a hidden helper window, a trusted working directory and
an environment containing only Windows runtime/temp values and Python encoding/
bytecode controls. Controls operations additionally forward sanitized process
`CC_ACTIVE_MODULE`; arbitrary environment values are not forwarded. There is no
local HTTP/WebSocket server.

The existing one-shot setup message is `{version:1,id,operation,project_root}`
with `read`/`preview`. Version-named `map_preview_v1` and `map_read_v1` add
main-owned observation time, design paths and a matching read-scope digest as
documented in the map contract. The helper consumes at most 64 KiB; its response is at most
1 MiB. Main bounds stdout to 1 MiB plus newline, stderr to 16 KiB and elapsed
time to 15 seconds. Raw stderr is discarded. Responses must match ID, operation
and root. Replacement waits for the owned predecessor to close, with a one-second
reap guard; uncertainty returns an error instead of spawning another helper.
Project selection invalidates old state immediately and drops late responses
from an older generation. Failed refresh clears stale results. No automatic
retry, provider fallback or continuing freshness claim is made.

Project containment, snapshots, canary-safe projections and resource budgets
remain owned by [the setup service](../docs/setup-service-contract.md) and the
[Project Map source adapter](../docs/project-map-sources.md). UI profile
storage is local, best-effort and separate from project state; it does not protect
against hostile same-user code, a compromised installation or privileged drive
remapping. The executable/Core/build paths are trusted launcher configuration,
not renderer inputs.

## Development and verification

Versions are pinned in `package.json` / `package-lock.json`. This workstation
reuses Electron 44.0.0 from its existing A: runtime and keeps React 19.3.0,
esbuild 0.28.2 and TypeScript 7.0.2 under `A:\DevTools\ControlCoding-Panel`.
No vendor app's source is imported. Install tooling only after inventory; keep
node_modules, caches and outputs outside the repository.

```powershell
node ui/build.cjs <absolute-external-node_modules> <absolute-external-output> <optional-absolute-babel-parser-package>
node --test ui/tests/unit.cjs ui/tests/map-unit.cjs ui/tests/map-review-unit.cjs ui/tests/map-controls-unit.cjs ui/tests/map-analysis-unit.cjs
python -B -m pytest tests/test_cc_panel_bridge.py tests/test_cc_setup_service.py -q --basetemp <external-fixtures>
python -B -m pytest tests/test_cc_project_map_analysis.py tests/test_cc_project_map_controls.py tests/test_cc_project_map_definition.py tests/test_cc_panel_bridge.py tests/test_cc_project_map_sources.py tests/test_cc_project_map_model.py -q --basetemp <external-map-fixtures>
```

`ui/tests/desktop-smoke.cjs` runs in Electron with absolute `--build`, `--core`,
`--python`, `--profile`, `--artifacts`, `--fixtures` arguments. It uses a native
folder-picker boundary stub and real renderer IPC/Python operations, exercises
window lifecycle and saves actual rendered screenshots. It is a development
test, not a production IPC or automation backdoor. Keep every path external and
use a fresh profile/fixture/artifact directory per run. Remove an inherited
`ELECTRON_RUN_AS_NODE` from the test process environment before launching Electron.

`ui/tests/map-desktop-smoke.cjs` uses the same external arguments to test the
three real map views, native document-selection boundary, isolated helper,
status truth, selection, hover/focus, cancellation and errors. Its fixture source
is illustrative, but the displayed observations are generated by the real reader.
No synthetic statuses enter the production panel. Keep the unchanged general
panel smoke as a separate regression run.

`ui/tests/map-review-desktop.cjs` uses the same external arguments for actual
review/preview/save, groups/aliases, conflict preservation, reopening and narrow
layout. It writes only its disposable fixture definitions; production code and
transport are used without a persistence mock.

`ui/tests/map-controls-desktop.cjs` uses the same external arguments to test
real canonical controls, explicit preview/read, reported feature states,
independent markers, popup explanations/provenance, source conflicts and narrow
layout. It asserts that observation leaves fixture bytes unchanged.

`ui/tests/map-analysis-desktop.cjs` adds actual worker/Python quality findings,
static relationships, intended-file gaps, historical report links and independent
markers. It uses the same external arguments and fresh fixtures/profile.
For Python/Node parser tests, set `CC_MAP_TEST_NODE` to the trusted Node executable
and `CC_MAP_TEST_WORKER` to the external built `map-language-worker.cjs`; otherwise
parser-dependent tests are explicitly skipped. Keep those skips distinct from a
fully exercised JS/TS run. Named Python fixture timings/allocation peaks are
recorded using JUnit properties (`-o junit_family=xunit1`).

MAP-06 local evidence, 2026-09-21: Electron 44.0.0; Python 3.11.9 and 3.13.15
each pass 321 targeted analysis/controls/definition/bridge/source/model tests with
two retained source OS skips. All 39 Node tests pass, including actual parser
coverage; TypeScript/build, 29 analysis assertions, 28 unchanged controls
assertions, 24 unchanged review assertions and 38 unchanged map-observer assertions
pass. The analysis preview button now wraps within narrow layouts; the unchanged
review test verifies 320 CSS-pixel fit after that correction.
The general window smoke's native pin
failure was previously reproduced on both MAP-03 and its before baseline and
remains unresolved. No whole-panel pass is claimed. Renderer zoom at
125/150/200% and 320 CSS pixels does not certify OS-wide display scaling,
screen readers, physical shortcuts or human tray-menu interaction. Independent
review and wider platform/accessibility acceptance remain.

Electron API decisions follow its [security guidance](https://www.electronjs.org/docs/latest/tutorial/security),
[BrowserWindow API](https://www.electronjs.org/docs/latest/api/browser-window),
[tray API](https://www.electronjs.org/docs/latest/api/tray), and
[global shortcuts](https://www.electronjs.org/docs/latest/api/global-shortcut).
These references describe API semantics, not this candidate's acceptance status.

The Core release allowlist/denylist remains unchanged, including its exclusion
of `ui/**`. There is no installer, signed desktop package, update mechanism,
public release or claim of installed-wheel template availability in this slice.

## Automatic Project Map refresh (MAP-07)

After observing a project, use **Enable automatic refresh** in Project Map.
It watches bounded filesystem/Git metadata hints and reconciles every 30 seconds
while the panel runs, including in the tray. Scope authorization is session-only.
Previously read controls and quality analysis repeat only while their previewed
scope stays unchanged; changed contracts/declarations require explicit reapproval
through preview/read. A commit never supplies missing verification evidence.
Pause, manual refresh and at most 20 source/revision-bound history summaries are
available. Project/design changes clear automatic scope and history.

See [refresh contract](../docs/project-map-refresh.md). The new
`ui/tests/map-refresh-unit.cjs` and `ui/tests/map-refresh-desktop.cjs` cover
scheduling and actual desktop behavior. The desktop harness accepts the same
external build/artifacts/fixtures/profile arguments as the other map tests and
creates commits only inside its disposable fixture. It waits for a real
30-second reconciliation after intentionally disabling watcher delivery.
MAP-09 integrated release acceptance and pending independent reviews remain
separate; this feature does not make the desktop part of the Core release.

MAP-07 local evidence, 2026-09-21: 58 Node tests, strict TypeScript/build,
144 map desktop assertions and 35 general desktop assertions pass. The general
smoke now passes pin/unpin after opening/recall explicitly focuses web contents;
the earlier MAP-03/MAP-06 pin failure statements above describe prior candidates.
Python 3.11/3.13 each pass 321 targeted tests with two inherited OS skips.
The two existing architecture-index omissions and pending integrated acceptance
are not closed by these checks. Raw failures, diagnostics and final results are
retained in the external `map-07-20260921-a` workbench.
