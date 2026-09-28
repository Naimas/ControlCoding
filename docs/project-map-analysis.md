# Project Map quality and dependency analysis

CC-MAP-06 adds explicit static analysis to the optional desktop Project Map.
Observe a project, choose **Preview quality & dependencies**, inspect the scope,
then **Analyze quality & dependencies**. The parser reads source text without
executing the project, importing its modules, loading plugins or running checks.
The model, source reader, canonical control/evidence owners and mapping writer
retain their contracts. Core remains usable without the panel or parser.

If canonical controls have already been previewed and observed, analysis refreshes
them under that bound scope and displays both sets of signals. Otherwise analysis
does not silently read the broader control/evidence scope. Normal map refresh,
mapping review, document changes, another controls read or project switch clears
the analysis binding. Failed/cancelled/stale reads clear previous results. There
is no watcher, ongoing freshness assertion or automatic repair.

## Supported observations

| Adapter | Coverage and meaning |
| --- | --- |
| `size/v1` | Physical source lines and byte count for source-reader code extensions. More than 500 lines is a review signal, not a god-file diagnosis. Comments/blank lines contribute to physical size. |
| `python-ast/v1` | Installed Python AST, `.py`/`.pyi`. Named functions and lambdas have separate branch counts. More than 80 physical function lines or 12 branch points triggers review. Syntax unsupported by the running Python version is unavailable. |
| `babel-static/v1` | Babel parser 7.29.7 for `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`. Function/class/method/arrow syntax yields proposed symbols and bounded metrics. JSX/TS syntax plugins are selected by extension; project Babel/TS configurations are not loaded. |
| `duplicates/v1` | Function syntax comparison, ignoring locations/comments and outer function names while preserving identifiers, parameters and literal values. Minimum eight physical lines and five direct body statements. Python leading docstrings are omitted. Matching groups are review signals, not proof of semantic redundancy. |
| `imports/v1` | Local candidate links from Python imports and JS/TS static import/export declarations, plus dependency-cycle findings. Dynamic import/require references remain unresolved. Cycles are review signals, not runtime failure claims. |
| `architecture/v1` | Explicit file-to-file forbidden dependency declarations and expected-file comparison. No architectural intent is inferred from prose or naming conventions. |
| `report-links/v1` | Exact declared UTF-8 JUnit/Cobertura XML reports, retained hash and reported aggregate counts only. No source-match/current-test or acceptance claim. |

Branch metrics start at one per function. Python adds conditional/loop/exception
handlers, conditional expressions, Boolean alternatives, comprehensions and
match cases. JavaScript adds conditional/loop/exception constructs, non-default
switch cases and logical alternatives. Nested functions have separate counters.
These are versioned syntax metrics, not a universal cyclomatic-complexity score.
Missing syntax, type checking and runtime analysis remain explicit limitations.

Findings contain analyzer/rule identity, review or measured classification,
severity, open lifecycle, source hashes, file/range locations and limits.
Function findings attach to the file and its matching symbol when available.
No acknowledgment, waiver, resolution writer or refactor action is supplied.
The optional parser identity and Python runtime version are recorded in scope
and result provenance. Source bodies, literal values and raw parser errors do
not enter the renderer; duplicate comparison exposes hashes, not normalized code.

### Static dependency boundaries

Python module roots default to the selected project root. Explicit roots may
add a `src` layout. Lookup considers exact `.py` and package `__init__.py` names
in the observed inventory, including relative imports. Multiple matches are
ambiguous. `from package import name` with a named package links the package
candidate; it does not prove whether `name` is a submodule or exported object.
For `from . import a, b`, each name has a separate candidate lookup.

JS/TS relative references match an exact explicit filename or bounded
extension/index candidates when the extension is omitted. More than one match
is ambiguous; the adapter does not guess runtime extension precedence. It does
not rewrite `.js` specifiers to `.ts`, resolve package exports, tsconfig aliases,
workspace package names, bundler plugins, install state or remote imports.
Case variants are not normalized into proven module identity. Out-of-root,
dynamic, unsupported and absent candidates remain unresolved. None is labelled
automatically as a broken dependency. Even a unique local candidate is only a
static relationship; import execution and binding validity are unverified.

## Optional coding declaration

The adapter reads the fixed project-root file `controlcoding.architecture.json`
when it exists. It never creates or edits it. This narrow coding contract does
not replace a design document, work plan, feature registry or ControlWork schema.
Unknown fields and unsafe paths are rejected. The declaration itself supplies
the source identity for intended-file comparisons and explicit rule findings.

```json
{
  "schemaVersion": 1,
  "pythonRoots": ["", "src"],
  "expectedFiles": ["src/api.py", "frontend/dashboard.tsx"],
  "forbiddenDependencies": [
    {"id": "ui-to-storage", "from": "frontend/dashboard.tsx", "to": "storage/db.ts"}
  ],
  "reports": [
    {"kind": "junit", "path": "reports/unit.xml"},
    {"kind": "cobertura", "path": "coverage/coverage.xml"}
  ]
}
```

Only `schemaVersion: 1` is required; omitted arrays use empty defaults except
`pythonRoots`, which defaults to `[""]`. Empty string denotes the project root.
Roots and expected/rule endpoints are ordinary confined relative paths, without
globs, hidden/private stores or path escapes. The contract allows at most eight
roots, 128 expected files, 128 uniquely named rules and eight distinct reports.

Expected files present in the inventory become both intended and observed;
others appear as intended, **not observed**. This does not prove absence beyond
the bounded inventory, implementation correctness or accepted delivery. Intended
units may appear in the grid but do not increase the observed-file counter or
verified-unit count. Their declaration remains linked even in a design-only
project. Markdown text is not automatically converted into this contract.

A measured architecture violation requires a unique static candidate matching
the exact forbidden source/target pair. Finding provenance includes hashes for
both code endpoints and the declaring rule source. Absence of such a finding
does not establish architectural compliance outside supported references.

Report paths must end in `.xml`. Ordinary paths are allowed; the named `reports/`,
`coverage/`, `.controlcoding/verification_receipts/` and
`.controlcoding/invariant_receipts/` stores may override source-inventory exclusion
only for the declared paths. Hidden/private/sensitive subtrees remain excluded.
Preview lists these extra reads explicitly. Links show content identity and
**currentness unassessed**. JUnit requires aggregate `tests` attributes on leaf
suites; other counts appear only when reported. Cobertura requires line totals.
Missing, unsupported or malformed reports stay missing/unavailable; XML DTDs
and entity declarations are rejected, and testcase/output bodies are omitted.
Only canonical evidence owners can establish current required passes.

## UI behavior

The quality section provides findings, navigable dependency endpoints, an
intended/observed comparison, existing report links and per-file parser coverage.
The architecture matrix adds a quality column. An amber **!** on a code square
indicates attached findings, independent of completion color and L/A/P controls.
Hover/focus names relevant rules; pinning reveals measurements, exact ranges,
source hashes and limits. Selection is shared with the existing views.

Zero findings is not presented as project health. Unsupported languages can
have size measurements while semantic analysis remains unavailable. JavaScript
symbols and newly intended nodes are analysis-only candidates; their mapping
writer is deliberately unavailable because MAP-04's authoritative base reader
does not own them. Existing file/Python mapping review remains unchanged.
All source/project/report strings are rendered as text.

## Trusted parser and bounded execution

The development build accepts an optional third absolute argument naming an
installed `@babel/parser` 7.29.7 package. It validates package name/version/license,
bundles only that standalone MIT library into `map-language-worker.cjs`, and
copies its license beside the output. The UI's installed TypeScript compiler
remains separate. No vendor editor application code is imported. Without the
parser package, the build explicitly records parser unavailability and Python
analysis still works. An incompatible supplied parser version fails the build.

The launcher has a trusted `-Parser` path option. Main derives the parser-worker
path from its own build, never from renderer or project JSON. The isolated Python
helper receives trusted Node/worker launch arguments only for analysis operations.
The worker receives retained text through stdin and has no project-root/file
loading API. It parses on a worker thread with a 128 MiB old-generation heap
limit, inside a 192 MiB Node old-generation limit. These are heap limits, not
measured process-RSS ceilings. Its responsive parent enforces a 4.5-second deadline;
Python bounds it to six seconds. After helper cancellation the parser may finish
briefly under its own deadline; immediate process-tree kill is not claimed.

Input is limited to 12 MiB encoded parser JSON; parser reply is at most 1 MiB.
Only runtime/temp variables plus Electron's Node mode are forwarded to this
worker. No shell, arbitrary environment, provider, target imports or project
processes are used. Raw parser diagnostics are not exposed.

Analysis retains at most 1 MiB/file, 8 MiB aggregate, 256 entries and 128 code
files. Parsing is bounded to 20,000 AST nodes/file, 512 symbols/import records
per file, 256 findings and 1,024 dependency references. Duplicate groups are
bounded to 32 locations. Named XML reports have at most 20,000 elements. Existing
source traversal and model/output caps still apply, including a 768 KiB map and
15-second desktop helper timeout. The analysis phase checks a ten-second
cooperative budget. Exhaustion fails explicitly; findings are not silently cut.

Preview binds root/request, base source snapshot, mapping revision, optional
controls scope, coding declaration, thresholds and parser identity. Read retains
and rechecks all analysis bytes, compares source hashes, then repeats the base
inventory to reject source/definition changes before publishing. This is not a
globally atomic filesystem snapshot. Unsafe paths, hardlinks, junctions, changed
inputs and stale scope cannot reuse old current analysis.

## Verification scope

Python tests cover known positives/negatives, supported syntax, branch scopes,
duplicate boundaries, exact import candidates, cycles, coding declarations,
report parsing, input races and confinement on disposable external fixtures.
Node tests check private request ownership, invalidation, bounded parser behavior
and an actual deadline with unclosed input. Real Electron tests use the actual
worker/Python/renderer, both themes, shared selection, 320 CSS-pixel layout,
source preservation, privacy and stale-scope behavior. Only native chooser
results are stubbed. Independent review and wider platform/accessibility coverage
remain separate; these tests are not a health assessment of an adopter project.

See [desktop contract](project-map-panel.md), [source scope](project-map-sources.md),
[controls](project-map-controls.md) and [mapping persistence](project-map-definition.md).
