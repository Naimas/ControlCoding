# Project Map refresh and observation history

The optional desktop observer can refresh an already observed map automatically.
First preview and observe the project, then select **Enable automatic refresh**.
This is a session setting for the selected project and design-document scope.
Pause stops its watches/timers; project or design selection clears it. Closing
to the tray keeps the observer running. Exiting or losing the renderer stops it.
The Status tab's setup observation remains a separate manual action.

## Commit ceremony

The [commit ceremony](ccdocs/methodology.md#44-structured-commit-protocol-commit-ceremony)
realigns source, status and impacted development documents. The map observes the
resulting inputs; it does not execute Git, certify ceremony completion or infer
verified acceptance from a commit. Select supported design documents explicitly.
STATUS, DEVLOG and private documents are not automatically imported as plans.

While automatic refresh is enabled, file/Git metadata events invalidate the
map. Ordinary Git commit activity is one source of these hints, including a
commit with no code-content change. Periodic reconciliation recovers missed
events and changes in directories that are not watched. Reopening a closed panel
requires a new observation; no resident background daemon or offline history is
installed. There is no synchronous transaction with Git or delivery guarantee
for every individual commit. Multiple rapid commits can coalesce into one
observation of their resulting state.

## Scope and scheduling

Electron main owns at most ten nonrecursive subscriptions: the project root,
ordinary `.git`, `.git/refs`, `.git/refs/heads`, `.git/logs`, `.controlcoding`,
and its `features`, `project-map`, `verification_receipts` and
`invariant_receipts` directories. The watcher inspects directory metadata, never
file bodies or event-provided paths. Symbolic/junction roots and ancestors are
rejected conservatively. Watch creation is not a retained-handle security
boundary; watcher events never authorize reads or validate an observation.
The existing Python confined readers remain the authority for every scan.

Changes coalesce behind a 750 ms delay, with at least three seconds between
automatic scan starts. There is one active chain and one pending invalidation,
not an event queue. Reconciliation runs every 30 seconds and rearms watchers.
Its timer alone does not cancel an already running bounded scan. Real write
hints cancel the owned helper and invalidate that scan's generation. Deep
directories, newly created metadata directories, linked Git worktree metadata,
watch failures and dropped events rely on reconciliation. Manual **Refresh map**
remains an explicit full refresh.

Every chain performs a fresh bounded source preview/read. Previously *read*
controls and analysis may recur, in that order. Their fresh previews must match
all previously approved scope fields except source snapshot, mapping revision
and the derived scope ID. Changed contracts, report paths, analyzer identity,
limits or analysis declarations stop that overlay until another explicit
preview/read. A preview alone does not authorize recurring reads. No additional
provider, project-code execution, check run or write is introduced.

Base, controls and analysis use two, four or six serial bridge calls. Existing
15-second helper deadlines, predecessor-reap guard, parser bounds and source
limits remain. These are bounded complete observations, not cached partial
filesystem scans. Incremental behavior is in scheduling, stable presentation
and source-aware diffs; no latency or maximum-project performance guarantee is
implied by the 30-second timer.
The parser retains its independent MAP-06 deadline and can briefly outlive a
cancelled Python owner; cancellation is not an instantaneous process-tree kill.

## Currentness and history

An invalidation immediately removes the current map and any pending mapping
save preview. The UI shows stale/updating status. An automatic chain publishes
only its final validated result after checking the selected project generation,
invalidation generation and root filesystem identity. Failure leaves no current
map; the previous history summaries remain explicitly historical. Replacing or
removing the root stops automatic refresh and clears history and read scopes.
Reopen the project and preview again. Late old-root replies cannot publish.

Surviving node IDs keep their display order and selection; new IDs append.
Reviewed mappings continue to come from the existing definition owner. History
retains at most 20 summaries in memory, one fingerprint index of at most 2,000
elements, the current ordering and up to 100 changed IDs per summary. Summaries
name current/previous source snapshots and accepted-definition revisions, counts
of added/changed/removed elements, reason and observed overlay coverage. Node
fingerprints include sources, relationships, projected states, constraints and
quality findings. Observation timestamps alone do not change node fingerprints.
Switching overlay coverage can change these counts; they are not a Git diff or
completion percentage. Removed IDs have no current navigation target.

No historical full-map copies, source bodies, disk cache, SQLite state, project
files or memory initialization are written. History is lost on exit, project
switch or design-scope change. It cannot serve as current evidence or restore
green after a failed scan. Existing canonical gate freshness and uncertainty
semantics remain unchanged.

## Validation boundary

`ui/tests/map-refresh-unit.cjs` exercises deterministic scheduling, cancellation,
late replies, scope authorization, root identity changes, churn and history
bounds. `ui/tests/map-refresh-desktop.cjs` uses real Electron/Python/Babel and a
disposable Git repository; only the chooser result is stubbed. It exercises a
commit, source/feature/report changes, deliberately lost watcher delivery,
periodic recovery, changed scope, retained mapping choices, resource failure,
root replacement, junction rejection and narrow layouts. Test artifacts belong
outside the product checkout. Named local results do not establish other OSes,
network filesystems, universal Git delivery or release acceptance.
