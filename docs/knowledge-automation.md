# Knowledge refresh controls

In **ControlWork > Maintenance receipts and limits**, **Pause automatic refresh**
persists `automatic: false`. **Resume automatic refresh** restores it. The panel
timer and optional worker honor this setting after restart. This does not cancel
an operation already running or prevent explicit searches and manual refreshes
from reconciling their source evidence. **Retry source reconciliation** starts a
new manual refresh; it preserves the earlier failed job receipt.

Startup, timer, source change, conversation, worker and commit-ceremony refreshes
share the reconciliation service. A reason label records the trigger; it is not
proof that every host installed an event listener. While the panel is open, its
observer detects project changes. The separately enabled worker can observe HEAD
changes with the panel closed. No OS startup service is installed automatically.
If both observers are stopped, a commit cannot update the archive until the next
explicit operation or observer start.

Configuration, conversations, accepted wiki sections and job results persist in
the local archive. Reconciliation updates authorized sources and derived pages;
approved wiki prose remains protected and becomes stale when its anchors change.
An interrupted writer is recovered through the archive's existing serialized
transaction and checkpoint mechanisms. Per-source failure and retry controls are
separate from the global automatic-refresh switch.

The focused automation regression exercises seven refresh reasons against a
changed source, verifies exclusion of the old passage, and checks state from a
new process. Worker interruption, HEAD transitions and desktop controls have
separate tests. These checks do not establish seven days of real adoption,
cross-platform host integration or a general-purpose background job scheduler.
