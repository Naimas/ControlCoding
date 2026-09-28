# Follow-up search in project knowledge

In **ControlWork > Search & answers**, retrieve evidence, then open **Deepen the
search**. **Find additional evidence** sends only the displayed question to the
configured Concierge, using its provider/model and saved generation settings.
It consumes one Concierge request, including on failure. This operation uses a
fixed search-planning instruction rather than the editable advisory role prompt.

The model proposes one or two alternative queries. ControlCoding searches the
existing authorized index; it does not read additional folders or expand access.
Source reconciliation runs before and after the operation. Changed projects,
configuration or source generations invalidate the operation. The total deadline
is 60 seconds, with at most two retrievals and one model request. Cancel stops
the operation. There is one round per displayed packet; start a new retrieval
for a new round. Manual handoff remains a separate workflow.

**Answer and deepen if insufficient** first sends the already displayed evidence
for an answer. If the model explicitly abstains (or the initial retrieval is
empty), it starts the bounded follow-up search automatically. This explicit
action authorizes at most two provider calls: initial answer and search planning.
New evidence still requires review before a further answer send. A provider
error is not treated as an abstention and never triggers an automatic retry.

If initial reconciliation advances the source generation (including archival of
the previous answer), old passages are discarded before the question-only plan
request. The follow-up pins the fresh generation; further changes during that
round invalidate its result. This prevents combining old and new evidence.

The preview shows attempted queries, warnings, elapsed time and the number of
new passages included. Up to ten passages are retained, giving new evidence
priority and renumbering citations. No answer is generated automatically from
new passages. Review them, then use **Send evidence and generate cited answer**.
The same updated evidence can also be used through AI & Sessions, including its
manual handoff path.

A completed search is not exhaustive and does not establish that the evidence
answers the question. Empty results do not prove the answer is absent everywhere.
An access or reconciliation error is reported as a stopped search, not as proof
that no answer exists. The trace belongs to the current UI packet; it is not a
persistent audit log. Answer archival follows the existing retention policy.

Verification includes helper/state tests, an actual Electron/Python bridge
scenario with an excluded private folder, and a small installed-Qwen synthetic
probe. These do not replace a real-corpus search-quality evaluation or human
acceptance. The fixed evidence-only probe results remain unchanged.
