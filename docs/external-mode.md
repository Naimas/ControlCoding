# External read-only source mode

External mode is a distinct, opt-in knowledge and evidence workspace. It reads
explicitly selected original files and keeps all CC-owned state outside the
product. It does not initialize or migrate a legacy/contained installation.
This source change is not included in previously published desktop archives.
This experimental option is currently deferred and is not offered on the ordinary
desktop welcome screen. Its implementation and explicit development CLI/launcher
entry points are retained; ordinary project adoption does not activate it.

| Mode | CC storage | Product integration |
| --- | --- | --- |
| Legacy | Existing project locations | Selected host/Git integrations |
| Contained | `<project>/cc/` | Declared host, Git and editor exceptions |
| External | Separate workspace and desktop profile | No product files, hooks, setup, or commands |

The product can start, test, build and ship independently of CC. External mode
does not change its permissions, Git configuration, ignore files or dependencies.
Existing CC files/hooks are left untouched; selecting this mode does not uninstall
them or stop another CC installation. Close other CC sessions on the product when
a no-writes session is required.

## CLI

Use Core installed outside the product and an installed Python 3.11+.
Source, workspace and Core/runtime must use ordinary, physically separate local
directories. Initialization requires a new or empty workspace. No environment
variable silently changes ordinary Core command routing.

```powershell
python -I -B C:\Tools\ControlCoding\scripts\cc_external.py init --workspace C:\CCWorkspaces\Example --source C:\Projects\Example --include README.md --include docs --include src
python -I -B C:\Tools\ControlCoding\scripts\cc_external.py refresh --workspace C:\CCWorkspaces\Example
python -I -B C:\Tools\ControlCoding\scripts\cc_external.py query --workspace C:\CCWorkspaces\Example --value "What is the deployment boundary?"
python -I -B C:\Tools\ControlCoding\scripts\cc_external.py status --workspace C:\CCWorkspaces\Example
```

Each `--include` is an existing source-relative file or directory. Directories
include descendants and hidden files except `.git`, `.hg`, `.svn`, `node_modules`
and `__pycache__`. Select only authorized material: scope selection is not a secret
detector and does not interpret `.gitignore`. Traversal, device/UNC paths,
links/reparse points, hardlinked files, ambiguous names and overlaps are refused.
Ordinary files are hashed; only UTF-8 Markdown/plain text are indexed. Other code
and binary files can anchor evidence but are not parsed or executed.

`external.json` binds source directory identity, absolute paths and scope. Moving
the source/workspace or changing scope requires a new workspace. No implicit
rebinding, archive merging or source cleanup occurs. Reopen with `status` or
`refresh`, not `init`; retain the previous workspace for its history.

## Desktop

The ordinary panel currently hides this deferred option. For explicit development
use, the separate launcher remains available:

```powershell
.\Launch-Observer.ps1 -Python C:\Python313\python.exe -External
.\Launch-Observer.ps1 -Python C:\Python313\python.exe -External -Source C:\Projects\Example -Workspace C:\CCWorkspaces\Example
```

The UI picker uses existing folders; create an empty external folder first.
Review source/workspace, enter relative scope paths, initialize and load the
catalog. This window has dedicated preload, IPC and backend action allowlists.
It does not register ordinary setup/job/provider/export/host integration routes.

The Electron profile is separate from product and workspace; `-Profile` selects
it. Supplied paths and descriptor bindings are checked before profile creation
and again before source selection/backend requests. Python starts with isolated
imports and bytecode disabled; cwd, temporary/cache files and SQLite sidecars
remain external.

## Functions and freshness

- Capture selected sources into a text projection outside the product. Originals
  are data; the projection is not an executable checkout.
- Reuse the embedded engine for lexical retrieval with citations, extractive wiki,
  explicit document links and manual conversation archival. Retrieved passages
  are not automatically correct answers or approved facts.
- Read Markdown with formatted local content. Remote resources and inline HTML
  execution are blocked; images are omitted in this external view.
- Create objective, phase, task, blocker, decision, evidence and note records,
  binding original relative paths and SHA-256 values. Optional directed links
  refer to earlier supporting records. A manual record does not certify a test
  execution or task completion.

Every service operation checks selected original bytes. Citations distinguish
original source root/path/hash from cached engine locator/revision. Changes,
deletion, unreadability and budget failure invalidate evidence. Queries, wiki
and documents refuse stale results until refresh succeeds; status and records
remain inspectable. Invalidation propagates through supporting record links and
is sticky: refresh does not reapprove a claim. Create a new reviewed record against
the current revision.

The open desktop checks status every 15 seconds when idle. It detects changes;
**Refresh original sources** updates the snapshot/index. An external scheduler
may call CLI refresh. CC installs no scheduler or commit hook, and does not claim
commit blocking, editor enforcement or commit attestation. No background update
continues after closing the external app.

Refresh marks state incomplete before replacing copies/index data. Interrupted
refreshes remain unavailable for retrieval and can be retried. Archive bytes are
sealed after trusted operations; unexpected archive changes are refused. Preserve
such a workspace and create a new one or restore a trusted complete copy. Hashes
detect drift, not publisher identity or replacement of both data and seal.

## Deliberate limits

Disabled: installation/apply, host adapters, hooks/commit enforcement, source code
changes, arbitrary commands, build/tests, provider agents, embeddings/semantic
search, OCR, rich extraction and automatic consolidation. Advanced Project Map,
portable ControlWork management, Gantt and role configuration remain in the
ordinary legacy/contained panel. They are not silently redirected at copies and
presented as work on the product. Manual transcript retention is explicit; other
chat applications are not harvested.

The dedicated service accepts only fixed actions. Its process-wide Python audit
guard refuses subprocess/exec/startfile and networking and confines Python
filesystem mutations and SQLite connections to the workspace. No project script,
plugin, Git binary or native extension from the product is loaded. This is a
constrained trusted service, **not an OS sandbox for arbitrary/native code**.
Hostile concurrent junction/mount substitution, malicious Core/runtime replacement
and writes by other apps are outside its claim. OS access-time bookkeeping is not
controlled.

Bounds: 5,000 files, 60,000 entries, 128 MiB selected bytes, 16 MiB per ordinary
file, 256 KiB per indexed document, selected directory depth five and 45 seconds
per capture. The engine keeps its passage limits; the external archive seal is
bounded to 512 MiB. A limit failure invalidates the observation; narrow scope
instead of treating a partial scan as complete.

## Storage and removal

`external.json` holds the binding; `state.json` holds revisions and governance
records; `projection/docs/source/` holds text copies;
`projection/.controlcoding/knowledge/` holds the engine's index, wiki and
conversations. `operation.lock` serializes operations and `tmp/` holds temporary
files. All are private external data. Back up the whole workspace while no
operation runs. Closing/removing this external workspace and profile requires no
product change; no automatic removal is performed.
