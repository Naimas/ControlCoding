# Assisted planning discussion

Planning assistance is experimental. Protocol checks and human review cannot
establish a model's semantic quality. Live qualification needs a separately
authorized named configuration and independent retained semantic grades.

The optional desktop Product discussion reuses configured roles and existing
Ollama/OpenAI connections or manual external exchange. Saved reviewed mode shows
the recipient, model, selected material and readable messages before sending.
Saved direct mode sends on the explicit submit action. Opening, typing, importing
or previewing never sends. Manual exchange calls no provider in ControlCoding;
external model identity, limits and charges remain unverified. No model or memory
is needed for the ordinary Product forms and stage review.

Discussion sessions are durable and independent of Development control revisions.
Questions, choices, prepared requests, attempts and replies can be resumed. A
reply is conversation input. **Review proposal** loads a bounded Product,
feature, Specification or Implementation plan draft for ordinary review. It
cannot approve scope, execution, tests or release. Source-derived edits cite
selected sections; new ideas identify assumptions or proposed decisions.
Omitted material and conflicts remain visible. Unrelated content is retained
rather than regenerated for an independent feature addition.

Requests bind the project, session, source and artifact revisions, exact saved
role policy, template and extraction identities. Recipient/model/configuration or
source changes require another preview. Stale replies remain inspectable but
cannot apply. A reviewed draft is revalidated when saved; manual conversion
explicitly detaches its assisted binding and still grants no approval.

Each explicit send reserves one durable attempt before provider I/O. Failed,
cancelled and rejected attempts remain in task accounting. Restarting a reserved
attempt leaves an unknown outcome until explicit reconciliation; it never sends
again automatically. Timeout, invalid or incomplete output cannot silently
repair, switch provider or partially apply. Cancellation does not certify remote
provider termination or absence of charges.

A discussion is one usage task. **New discussion** explicitly starts a separate
task; earlier discussions remain selectable with their own retained usage.
Its totals do not claim the cost of every discussion for an entire feature.

Selected context is bounded to 48,000 bytes of serialized UTF-8 messages and
typed proposal output to 32 KiB of UTF-8 text and 12,000 characters. Actual
output-token and timeout limits are passed to the provider;
lower saved-role/provider/manual caps prevail. Character, byte and token limits
are different. Reduce selection or split a request when it is too large; no
source is silently truncated. Reported tokens, elapsed time and reviewer effort
are retained. Missing usage or cost stays unknown. Sessions have explicit
64-event, 256 KiB and 256-combined-session limits; capacity exhaustion is visible.
Prepared requests hold a computed terminal storage credit, including actual
binding size, accepted output/error bounds, serialization overhead and the
reserve event. A request that cannot fit within the session bound is rejected
before sending. Quota exhaustion cannot consume its retained completion capacity.
The original text-session format retains its 64-turn bound.

## Local Core transport

Core planning commands persist and validate local requests; they do not connect
to any provider. Use `python scripts/cc.py planning read --project-root PATH
--json` to list sessions, or add `--session-id ID` to inspect one. Other commands
are `create`, `append`, `preview`, `prepare`, `reserve`, `finish` and `review`, with
`--request-file FILE --json`. The JSON file must contain the exact operation's
fields. Its optional `operation` must match the selected command. Duplicate keys,
extra authority fields, unsafe paths and excessive requests are rejected.

Creating a planning session in a fresh project initializes a disabled empty Core
registry. It does not activate Development, change source, initialize memory or
accept a document. Preview is read-only; prepare durably retains its reviewed
request. Review returns ordinary draft actions, not an approval receipt.

## Independent quality reference

`tests/fixtures/planning-quality-corpus-v1.json` freezes twelve semantic tasks,
covering new/existing apps, omitted sections, conflicting authority, independent
additions, shared criteria, security, observed code and misleading instructions.
Only each task's input and evidence belong in a model packet. Required facts and
rubric are separate grader reference material. Every mandatory/security condition
must be retained or explicitly escalated, with no fabricated source or approval,
at least 90% of other requirements represented and no omitted-source completeness
claim. Useful questions, preserved conflicts, assumptions and actionable scope
also need semantic review.

`tests/planning_quality_records.py` validates retained human rating records and
aggregates declared results. It cannot authenticate actors or judge semantic
equivalence. Fake records test that protocol only and never qualify assistance.
Each input digest must match its canonical UTF-8 model packet (sorted JSON keys,
compact separators, preserved Unicode). Output digests are declarations until
actual retained output bytes are supplied and matched. A live assessment label
requires those artifacts; the adapter still cannot verify authorization or human
semantic ratings independently.
Changed model, prompt, extraction policy or material context builder invalidates
the corresponding quality claim. The earlier two-participant human usability
gate remains a separate pending acceptance, not a model-quality result.
