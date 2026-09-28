# Read-only setup service contract

`cc_setup_service` is the first backend increment for the optional local panel.
It inspects selected setup inputs and previews canonical **minimal_init**. This
is not the complete Core installation wizard, an installed desktop panel, or an
apply API. Core remains usable without it. See [installation](../INSTALL_WIZARD.md)
and the separate [project-definition wizard](../PROJECT_SETUP_WIZARD.md).

## Python API and transport boundary

Use Python 3.11+ and the existing `scripts` import path (or the distribution's
module). This increment adds no CLI command, stdio server, desktop dependency,
HTTP listener or persisted configuration model.

```python
from cc_setup_service import (
    ReadPolicy, SetupServiceError, read_setup_state,
    validate_setup_request, preview_minimal_init,
)

request = {
    "schema_version": 1,
    "operation": "minimal_init",
    "project_root": "/absolute/path/to/existing/project",
    "central_hooks": False,
}
try:
    state = read_setup_state(request["project_root"])
    validation = validate_setup_request(request)
    preview = preview_minimal_init(request)
except SetupServiceError as error:
    response = error.as_dict()
```

On Windows, use a local absolute drive path such as `C:\Projects\Example`.
The service accepts an exact Python `dict`, with only the four keys above.
`schema_version`, `operation`, and `project_root` are required. `central_hooks`
defaults to false; its only supported value is the exact boolean `False`.
Boolean schema versions, integer/string booleans, nested extra controls, unknown
keys and unsupported versions fail. Values are never coerced to strings.
Root must be an existing ordinary directory; relative paths, `..`, NUL, more
than 64 path components or 4096 characters fail. Windows UNC/device namespaces,
alternate streams and trailing dot/space aliases are unsupported.

Validation checks the request and root only. It does not assert that existing
configuration is compatible; preview performs that check. Full `setup`, apply,
central hooks, project definition, role writes, connection tests and provider
operations are unsupported. No credential/backend is inferred from a key.

All successful responses have `schema_version: 1`, `service:
"cc-setup-service/1"`, `status: "ok"`, operation (`read`, `validate`, `preview`),
`scope: "minimal_init"`, `project_root`, `apply_supported: false`, runtime and
input fingerprints. Runtime reports Python version/minimum support, memory
`not_probed` and host delivery `unverified`. It does not attest SQLite memory
capability, a loaded host integration or installed provider.

## Selected inputs and projection

Read inspects exactly these six content paths, plus their parent metadata:

| Canonical | Legacy | Selection |
| --- | --- | --- |
| `.controlcoding/cc_config.json` | `.claude/cc_config.json` | Canonical first, otherwise legacy |
| `.controlcoding/settings.json` | `.claude/settings.json` | Canonical first, otherwise legacy |
| `CONTROLCODING.md` | `CLAUDE.md` | Report each existing context as unvalidated |

The returned `sources` records use fixed relative labels, state (`absent`,
`valid`, `present_unvalidated`) and a `selected` flag for JSON. Absent JSON does
not synthesize persisted defaults. Any present invalid source fails, including
a shadowed legacy source. This conservative service restriction is stricter than
CLI selection; it never silently discards malformed legacy data.

JSON must be a UTF-8 object. Configuration and settings container checks reuse
Core's `_validate_init_config` and `_validate_init_settings`. The only projected
configuration values are validated `hooks_location`, `documentation_mode`, and
`cc_artifact_mode`, when explicitly present. Other fields are counts:
`protected_zone_count` uses Core's `_normalize_protected_zones`;
`hook_event_count` and `mcp_server_count` count configured containers.

Protected paths/descriptions, hook commands, event names, server names, arbitrary
custom keys, settings content, tokens and context content are never returned.
Unknown keys remain untouched on disk and are preserved by the canonical plan.
Gateway, environment, credentials, setup intent, memory databases and receipts
are not read. There is no recursive scan or Git discovery.

## Canonical plan and identity

Preview calls `cc._plan_init` with an injected bounded observer and recheck.
Existing CLI callers retain their original observer, apply path and behavior.
The service does not call `cmd_init`, setup, doctor or an apply handler.

Each planned file reports a root-relative `path`, `action` (`create`/`keep`), the
canonical reason, byte count, intended `sha256` and `generated_sha256`. For a
retained file the intended hash describes the actual observed bytes, while the
generated hash describes Core's detached alternative, which apply does not
publish for a `keep`. They can differ. No contents or diffs are returned.
Directories report relative paths and create/keep actions. Git hook targets are
included when the canonical planner observes an existing ordinary `.git/hooks`;
a `.git` worktree indirection file is unsupported. Existing contexts, documents
and Git hooks retain Core's explicit unvalidated/unverified reasons.

The trusted source allowlist is fixed by the installed Core: its fourteen
`INIT_HOOKS` templates, context template, fitness script, `cc.py`, and this
service file. No project value can nominate a trusted source. Source-checkout
templates must be available; a wheel that lacks them is not made self-contained
by adding this Python module and cannot produce a fresh plan.

`inputs` includes path provenance, kind, absence/presence, filesystem identity,
and size/SHA-256 for observed files. `trusted_source_root` names the Core source
root. `planner_identity` separately hashes loaded planner/helper code and key
constants, and detects in-process drift during planning. Observed source hashes
are not an attestation that a caller's Python process or installation is trusted.

`plan_fingerprint` hashes the complete projected preview except itself, including
scope, service/planner identity, root, runtime and observed inputs. It is stable
for identical bounded inputs in the same runtime/process code context; it is
not portable across Python versions or installation paths. Changed inputs cause
a new fingerprint or a conflict. Responses say `freshness: "observed_only"`:
they describe the bounded observation and never claim continuing freshness after
return. There is no apply token or authorization grant. A future consumer must
observe again; this slice cannot authorize an apply operation.

## Budgets and containment

`ReadPolicy` permits lowering these positive integer limits, never increasing
them. Exact booleans are not integers for this API.

| Resource | Hard ceiling |
| --- | --- |
| One consumed file | 1 MiB |
| Unique target content per operation | 8 MiB |
| Unique trusted source content per preview | Separate 8 MiB |
| Observed entries, including roots, ancestors and missing entries | 256 |
| JSON structure per file | 10,000 value nodes; depth 32 |
| Serialized request | 1 MiB; fixed field/path limits are tighter |
| Generated plan data | 1 MiB per file, 8 MiB total |

Reads use at most 64 KiB chunks and at most one extra byte to detect growth.
Aggregate accounting counts each unique snapshot once, including shadowed
sources. Read performs one final content recheck; preview performs Core's final
recheck and another before return. Each recheck reads no more than the original
size plus one. This bounds recheck traffic separately from retained snapshot
memory. Canonical JSON generation occurs after bounded parsing; an expanded
output over the generation budget fails rather than returning a truncated plan.

Every root/parent component is inspected from the filesystem anchor. Symlinks,
junctions, reparse points, special files, multiply-linked files and inaccessible
entries fail before content consumption. Ordinary unrelated files are not read.
Windows uses read handles without write/delete sharing and opens the final
entry with `FILE_FLAG_OPEN_REPARSE_POINT`; directory handles are retained too,
preventing rename and write access used for reparse edits. POSIX uses retained
directory descriptors, `dir_fd`, `O_NOFOLLOW` and `O_NONBLOCK`. Opened descriptor
identity must match inspected metadata before reading. Final checks compare
path/descriptor identities and bytes; absent entries are checked again.

Windows ctime is not compared because `lstat` and `fstat` can expose different
ctime meanings in the supported interpreters. File identity, size, mtime, pinned
handles and exact content rechecks remain required. Access time is not a
preservation guarantee: filesystem reads may update atime. There is no project
write, metadata setter, process launch, socket/provider traffic or memory init.
Ordinary filesystem availability still governs synchronous I/O; this API does
not promise a wall-clock deadline or defense against privileged filesystem/drive
remapping. The desktop transport will require separate timeout/concurrency policy.

The Windows API semantics are documented in
[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew);
the POSIX descriptor interface is documented in
[Python os.open](https://docs.python.org/3/library/os.html#os.open).
Platform implementation is not platform execution evidence. Local validation
for this candidate is recorded in its implementation handoff; skipped symlink
or FIFO controls must not be inferred to pass from junction results.

## Safe failures

`SetupServiceError` exposes `code`, a fixed relative source label (or a request
control label), and `as_dict()`. It never forwards source values, unknown key
names, parser excerpts, OS exception text or retained file content. Operations
raise rather than returning an empty successful state after a failed read.

| Code | Meaning and next action |
| --- | --- |
| `invalid_request`, `invalid_type`, `invalid_root`, `unknown_control` | Correct the request's fixed schema/types/root; remove unknown controls |
| `unsupported_version`, `unsupported_operation`, `unsupported_platform` | Choose an implemented schema/operation/platform; no fallback execution |
| `missing_root` | Select an existing directory; no implicit creation |
| `unsupported_path` | Select ordinary local paths; inspect links/special files explicitly |
| `inaccessible` | Inspect permissions or a conflicting open writer; retry deliberately |
| `invalid_json`, `invalid_config` | Repair the identified source; original bytes are preserved |
| `invalid_policy` | Use positive integer limits within the hard ceiling |
| `too_large`, `input_limit`, `structure_limit`, `request_limit`, `output_limit` | Reduce the identified input/plan; no silent truncation |
| `missing_source` | Restore the required installed service/planner source |
| `plan_conflict` | Reconcile the named canonical init conflict; missing template sources also fail here |
| `changed_input` | Observe and preview again after concurrent changes stop |

Python callers should handle `SetupServiceError` as an operation result. Process
termination, interpreter exhaustion and programmer errors are not converted into
valid setup state. No diagnostic logging is performed by the service.

## Verification and remaining product scope

`tests/test_cc_setup_service.py` covers canonical bytes/actions, existing custom
configuration, legacy settings, provenance, canary suppression, complete fixture
preservation, strict requests, limits, conflicting inputs, descriptor/path races
and forbidden execution/read boundaries. Relevant existing init and setup
regressions cover the shared pure seams. Fixtures and test outputs belong outside
the source checkout. A successful service test is not whole-panel acceptance,
host delivery evidence, installed-wheel validation or closure of Core findings.

Desktop/IPC, complete setup preview/apply, credentials/connections, agent review
and desktop packaging are later increments with separate acceptance gates.
