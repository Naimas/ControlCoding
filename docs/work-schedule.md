# Work plan and Gantt

Development candidate in the optional desktop panel. Open **ControlWork**, then
**Work plan & Gantt → Refresh work plan**. Project memory must be enabled. Include
the Dev records scope to observe canonical work, or assign objective, phase and
activity roles to documents and approve their relations in **Work traceability**.
Filenames and prose alone do not create tasks or dependencies.

## Two views of the same work

- **Process order** displays dependency stages without requiring dates or
  estimates. Expand a main block to see subtasks. Items in the same stage have no
  precedence constraint between them; each item waits for its own predecessors,
  not for every item in the preceding stage.
- **Calendar Gantt** displays estimated elapsed time, optional calendar dates,
  group spans, explicit buffers and calculated total float. Choose a project
  start date to replace relative days with dates. Zoom and scroll stay within
  the view. Unknown or invalid timing is shown as unscheduled.

Select a row to inspect predecessors, successors, recorded state, owner and
blockers. Hover highlights its direct dependency connections. Enable **All
dependency arrows** for the wider graph; use **Highlight critical path** to dim
noncritical work. Double-click a task or select **Open original work record**
to read its source in the formatted document drawer.

Containment means “belongs to this block,” not “must finish before.” Dependency
arrows mean finish-to-start. A dependency between groups expands to their leaf
work items. Canonical contains/part-of and precedes/depends-on links are observed;
current approved part-of, depends-on and blocks relations are also used.
Unapproved or stale relations cannot silently become current dependencies.

## Estimates, buffers and critical path

Select a leaf task and expand **Planning estimates and buffers**. Enter its
duration, optional buffer, start-no-earlier-than date and target deadline, then
provide a reason and save. Groups roll up their children. These values do not
change canonical completion, ownership or task lifecycle.

Duration and buffer use calendar days, including weekends. Fractional days are
supported. Start dates round down and exclusive finish boundaries round up to
enclose fractional intervals; numeric calculations retain the fractions.

- **Planned buffer** is an explicit reserve added to that task's duration.
- **Total float** is the calculated delay the task can absorb without delaying
  the estimated finish of the current graph.
- **Critical work** has zero total float. Multiple critical branches are possible.

Timing and critical-path calculations require a complete, current, acyclic graph
and current explicit estimates for every leaf. Date constraints require a project
anchor. Missing data, cycles, unresolved endpoints and stale revisions are
reported. Deadlines produce findings; they do not silently compress durations.

This is dependency-based planning, not resource optimization. Shared owners in a
parallel stage or overlapping estimates generate findings. A recorded blocker
propagates to dependent work. Estimated dates assume those blockers can be
resolved; they are not promises of executable dates or completion. Dependency
waiting is displayed separately from an actual blocker.

## Persistence and refresh

The existing project knowledge archive stores a bounded `work_schedule_v1`
metadata record containing IDs, source revisions, numbers and dates. It contains
no copied document text or second task state machine. Saving checks the current
snapshot and source revision; a concurrent change requires a refresh. Normal
archive backup/restore preserves the overlay, but restored sources must be
reconciled before timing becomes usable.

Loading or saving reconciles the authorized sources first. A loaded panel plan
refreshes when normal memory reconciliation advances its generation or a work
relation changes. This includes commit observation when automatic memory refresh
is enabled; it does not install another Git hook. Changing project clears the
view and discards late results. Changed sources invalidate estimates and approved
relations that refer to older revisions; review them explicitly.

The first version bounds the projection to 512 nodes and 2,048 dependency edges,
using the existing reviewed-relation limit. Truncation is reported and disables
readiness/timing claims. There is no automatic task creation, agent execution,
business-day calendar, resource leveling, start-to-start link or percentage
completion inferred from document text.

Service/CLI actions are `work-schedule-view` and `work-schedule-save`. They use
the same project scope as the other knowledge commands and do not require an AI
provider or additional runtime dependency.
