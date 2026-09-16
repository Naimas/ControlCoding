# Verification evidence

`verify run` and `invariants run` write local schema-v2 receipts. A receipt records
what ran and the input and runtime identities observed before and after execution.
It is not a signature, a source attestation, an atomic filesystem snapshot or a
hermetic build. Commands remain authorized project commands, not sandboxed code.

## Outcomes and current evidence

| Attempt | Receipt status | Exit |
|---|---|---|
| Complete required selection passes with stable, measurable inputs | `passed` | 0 |
| Explicit subset passes with stable, measurable inputs | `passed_subset` | 0 |
| A selected command returns nonzero | `failed` | 1 |
| Identity changes/is unknown, timeout, capture/spawn error, missing outcome or output limit | `incomplete` | 1 |
| Interrupted execution | `incomplete` | 130 |

Incomplete identity takes precedence over ordinary command failure. Each command's
observed return code remains separate. Selected optional checks can fail a run.
For invariants, the required set is every active executable entry, including
warning-severity entries. `--all` additionally selects executable drafts.
Suite completeness does not establish that every nested test ran: inspect JUnit
skips and separate platform/host evidence.

Ordinary status validates configuration and remains usable before any receipt:

```bash
python scripts/cc.py verify status --project-root . --json
python scripts/cc.py invariants status --project-root . --json
```

To require current execution evidence, use:

```bash
python scripts/cc.py verify status --project-root . --require-current --json
python scripts/cc.py invariants status --project-root . --require-current --json
```

The strict status exits zero only for a complete required pass matching current
inputs, contract, revision, runner and execution context. Configuration validity
is reported separately as `contractValid` or `manifestValid`; the default `ok`
continues to describe configuration. Evidence assessment uses `current`, `stale`,
`context_changed`, `unknown`, `legacy`, `invalid` or `incomplete`, with reason codes
and `currentRequiredPass`. Report and doctor expose the same assessment alongside
historical outcomes. Existing doctor strict flags still validate configuration;
they do not acquire the strict status execution requirement.

A newer partial, failed, unreadable or malformed attempt does not fall back to an
older green result. Valid v2 attempts are ordered by creation time and ID, rather
than completion time, including in mixed histories. Legacy/invalid entries use
their filesystem modification time conservatively. Unversioned/v1 receipts remain historical and unbound;
future schemas and inconsistent v2 claims are invalid. No automatic migration
or rewrite occurs. Status, report and doctor do not initialize receipt storage.
Any history read failure (including a per-file or aggregate byte limit, deadline,
entry limit, unsafe path, race or unreadable file) makes the entire assessment
`unknown`, with `history_inspection_incomplete` and `currentRequiredPass=false`.
An uninspected file's mtime cannot justify selecting an older success. Fully read
malformed or legacy data retains the distinct classification described above.

## Structural checks and public examples

Truth report/check/check-docs, including nested include-docs results, expose
`scope: structural` and explicit limitations in JSON and human output. Recognized
routes do not validate complete arguments or preconditions, execute examples, or
prove behavior. Evidence references remain declarations; even an invented nonempty
reference can satisfy that structural requirement. Existing missing-route,
empty-evidence and strong-claim findings still fail the corresponding checks.

Invariant doctor CI observations use `scope: local_configuration`. The legacy
`runsInvariantGate` field means recognized command text was found, including text
in comments; its companion meaning explains this limit. Legacy doctor state and
control-level labels describe configuration. Hosted execution and server
enforcement remain explicitly unverified. Current local receipt assessment is
separate from these structural and configuration observations.

`tests/test_cc_public_examples.py` binds reviewed argv to frozen literal command
lines, document sections and occurrence counts before executing the real CLI in
small synthetic projects. It covers the scoped ordinary/strict status and default
verify-run examples here and in contributor/release docs, plus installation-guide
elicitation, report, doctor, wire-ci and chat-guide invocations. Required fixture
commands are small passing/failing processes; they do not run the full Core suite.
Subset, parser, truth-route, snippet-drift and CI-comment cases are companion
controls. Inline truth route references are not claimed as executed examples.

This is argv-level coverage. Shell quoting, surrounding shell orchestration,
installation, setup apply, host integration, adjacent release-doctor commands and
all other public examples remain outside this catalog. It does not replace the
production verification contract, hosted/platform evidence or independent review.

## Input scope, policy version 1

Git scope includes the union of HEAD paths, index paths and nonignored untracked
paths, with missing-file markers. Raw bytes, relative names and executable modes
feed a SHA-256 digest through sorted, length-prefixed JSON records. Git HEAD,
object format and semantic index entries are separate identity fields. On Windows,
the executable bit comes from the index for indexed files; archive/untracked
files have no executable bit. Raw CRLF differences count as changes. A stable
dirty checkout may have current evidence for those exact dirty bytes; a subsequent
index or HEAD change invalidates it. A timestamp-only change does not.

Inspection uses bounded Git inventory commands with optional locks, replacement
objects, lazy fetching and fsmonitor disabled. It does not call `git diff`, clean
or process filters. System/global Git configuration and global excludes are
excluded from inventory inspection; local ignore rules remain in effect. This
isolation applies to inspection only, not to suite processes.

Git runs exclusively in a private temporary projection built with confined reads.
The projection contains HEAD, refs/packed refs, index/shared indexes, object
metadata, empty worktree placeholders and local `.gitignore`/`info/exclude` bytes.
Worktree source files are not copied; Git object files can contain encoded source
blobs and remain subject to the same private-storage and byte-budget limits.
All Git calls in one capture share that
projection; replacing an original metadata directory or leaf cannot redirect
Git's reads outside it. Source contents are subsequently hashed through SafeRoot.
The projection is removed after inspection; status does not modify the project.

Original repository configuration is parsed as data. Generated inspection config
retains only supported object format and ignore-case settings. Includes, worktree
paths, executable settings and external excludes are not followed. Unsupported
config syntax or extensions, alternate object stores and reparse metadata fail
closed. Ordinary/unborn repositories, packed objects/refs and SHA-256 repositories
are supported within the same budgets. Metadata bytes and placeholder inventory
consume those budgets, so a large object store can be incomplete. Temporary
storage must be outside the inspected project. Private temporary storage is not
a security boundary against another process with the same user's access rights.

Archive scope applies only when the target root has no `.git` entry; it never
borrows an ancestor repository. Broken Git metadata, gitfiles/worktrees, alternate
object stores, submodules, sparse or unmerged indexes and nested
repositories are unsupported evidence inputs. They yield incomplete identity.

The following component names are always excluded, including when tracked:
`.git`, `verification_receipts`, `invariant_receipts`, `verification_tmp`,
`invariant_tmp`. Untracked/archive discovery additionally excludes `_work`,
`.controlwork`, `.controlcoding`, `.claude`, `.codex`, `.agents`, `__pycache__`,
`.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.venv`, `venv`, `node_modules`,
`build`, `dist`, `.tox`, `.nox`, `*.egg-info`, `*.pyc`, `*.pyo`, `AGENTS.md` and
`.env`. Ordinary tracked files remain included despite this second list.

Mandatory inputs override discovery exclusions and bind presence/absence of:

- `controlcoding.verification.json` and `controlcoding.invariants.json`;
- `verification.json`, `capabilities.json`, `cc_config.json` and `settings.json`
  under both `.controlcoding/` and `.claude/`.

Contract schema remains 1. Either contract may explicitly extend the input scope:

```json
"evidenceInputs": {
  "schemaVersion": 1,
  "extraPaths": [".env", "config/local-policy.json"],
  "environmentNames": ["CC_TEST_PROFILE"]
}
```

Extra paths are literal root-relative files, including absence markers. Escapes,
globs, Git internals and receipt/temp paths are rejected. Verification uses the
union of both contracts' declarations. Environment values are only hashed.
This is not automatic dependency discovery: undeclared external services, files
and environment dependencies remain outside the claim.

Each snapshot allows 10,000 inventory operations, 32 MiB per file, 256 MiB total
file reads and a cooperative 30-second deadline. Individual kernel filesystem
calls can still block; this is not a hard wall-clock guarantee. Git commands additionally have a 10-second,
4-MiB bound within that deadline. Read budgets are shared and never refunded;
growth or replacement produces incomplete evidence, not an unbounded retry.
Windows directory handles retain the root and its ancestors and reject reparse
components. POSIX operations use directory descriptors and no-follow, nonblocking
leaf opens. Symlinks, junctions, special files and unsupported names are rejected.
Endpoint agreement cannot rule out an intervening change that was reverted.

## Runner, output and storage

Runner identity hashes owned Python modules from the source `scripts` tree or
the installed distribution's explicit inventory, plus Python implementation and
version, OS/architecture and installed distribution name/version fingerprints.
Unknown layouts or failed measurements are incomplete. This is not a byte-level
attestation of third-party wheels. Execution context binds the project location,
expanded argv and executable resolution; `{temp}` is normalized for comparison.
Moving a checkout or changing measured runtime, modules, dependencies, declared
environment or command expansion changes context. CI presence is unverified
local metadata; no hosted result or server-side enforcement is inferred.

Receipts contain digests, IDs, counts, durations, return codes and structured
errors. They omit source contents, per-file inventories, expanded commands,
environment values and exception messages. `stdoutTail`/`stderrTail` remain empty
compatibility fields, with explicit omission and emitted-byte metadata. Command
templates are omitted from status/report output too; inspect the local contract
for command text. Command
output is concurrently drained with a shared 16-MiB emission ceiling and at most
64 KiB in a suite read buffer. Timeout/overflow terminates the owned child; pipe
cleanup is bounded even if a descendant retains a pipe. This does not guarantee
termination of every descendant. Limits are versioned constants, not contract
overrides in this schema.

An atomic `running` receipt precedes the first child. Normal completion, errors
and interrupts finalize the same unique attempt; abrupt process death can leave
`running`, which cannot pass a strict gate. Read limits are 2 MiB per receipt,
32 MiB total and 128 directory entries. Unsafe output paths or exceeded history
bounds yield unknown/invalid evidence. A finalization failure cannot return a
successful run. Receipt IDs include UTC time and a random UUID.

Selection claims require exact booleans, integer counts from 0 to 1,000 and
unique string lists of at most 1,000 elements, each 1 to 200 characters. Counts,
required/selected/executed/omitted IDs and coverage must agree. Requested selectors
contain exactly `ids`, `kinds`, `domains` and boolean `all`; nested objects and
extra fields are invalid. Kinds follow the CLI enums and invariant domains follow
the contract, including custom domains. Writers normalize repeated or inactive
selectors while preserving CLI precedence. Readers reject malformed persisted
claims and construct summaries from validated allowlisted fields.

With matching contract descriptors and plan, readers derive the entire ordered
selection from normalized contract entries and require exact equality with the
declared selected IDs. Verification uses all, otherwise IDs, otherwise kinds,
otherwise required entries. Invariant IDs, domains and kinds intersect; default
selection includes active executable entries, while all also includes executable
nonactive entries. Contract order is preserved. Interrupted execution retains
the full selected intent separately from its executed prefix.

The compact plan alone cannot establish selector context: it omits kind, domain
and status metadata. Contract changes remain a separate stale assessment even
when that plan is unchanged. The validator's success without matching entries
and contract descriptors establishes structure only; current certification also
requires semantic validation and matching input, contract, plan and runtime
identities. A consumer without selector context cannot certify current evidence.

CI JUnit files remain separate diagnostics. They can contain full failed-test
output, are overwritten per suite, and require appropriate fixture data and
artifact access controls. JSON metadata omission does not sanitize JUnit or
arbitrary files written by a suite. See [contribution guidance](../CONTRIBUTING.md).
