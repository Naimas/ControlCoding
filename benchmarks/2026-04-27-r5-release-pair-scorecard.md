# R5 Release Pair Benchmark Scorecard

## Scope

This note records the curated outcome of the R5 release-pair benchmark runs
created under an external local workspace.

It covers two benchmark packages:

- `L3-trading-engine`
- `L5-dungeon-crawler`

Each package was run twice:

- Run A: no ControlCoding context
- Run B: ControlCoding context present through `AGENTS.md`, `.controlcoding/settings.json`, and local hook files

Raw benchmark projects remain outside this repo. This file captures the release
evidence and the residual limits without committing generated benchmark output.

## Benchmark Identity

- Date: `2026-04-27`
- External workspace: `<benchmark-workspace>`
- L3 Run A: `<benchmark-workspace>/L3-run-A`
- L3 Run B: `<benchmark-workspace>/L3-run-B`
- L5 Run A: `<benchmark-workspace>/L5-run-A`
- L5 Run B: `<benchmark-workspace>/L5-run-B`

## Verification Snapshot

| Run | Required or declared check | Result | Notes |
|---|---|---|---|
| L3 Run A | `pytest tests/test_invariants.py -v` | PASS | `RESULTS.md` reports 1 invariant test file and all 7 checkpoint runs passed |
| L3 Run B | `pytest tests/test_invariants.py -v` | PASS | `RESULTS.md` reports 5 tests passed |
| L3 Run B | `python -m compileall src tests` | PASS | Extra sanity check recorded by the run |
| L5 Run A | `pytest tests/test_invariants.py -v` | FAIL in local verification | Bare `pytest` cannot import `src` from this layout |
| L5 Run A | `python -m pytest tests/test_invariants.py -v` | PASS | `RESULTS.md` reports 6 tests passed |
| L5 Run B | `pytest tests/test_invariants.py -v` | PASS | `RESULTS.md` reports 9 tests passed |

Important L5 interpretation:

- The L5 benchmark prompt expects `pytest tests/test_invariants.py -v`.
- L5 Run A reports success with `python -m pytest`, not the exact required
  command.
- Therefore L5 Run A should be treated as functionally close but operationally
  weaker than L5 Run B.

## L3 Trading Engine Findings

| Area | Run A, no CC | Run B, with CC | Reading |
|---|---|---|---|
| Modification coverage | M1 through M7 completed | M1 through M7 completed | Both completed the requested feature sequence |
| Invariant tests | PASS, 1 test file | PASS, 5 tests | Run B has stronger test granularity |
| Compile check | Not separately recorded | PASS | Run B recorded an extra sanity check |
| Reporting and snapshots | Implemented inside `src/matching.py` | Split into `src/reporting.py` and `src/snapshot.py` | Run B has cleaner responsibility boundaries |
| Matching core pressure | `matching.py` grew to include reporting and snapshot behavior | `matching.py` stayed more focused | Run B better resists central-file growth |
| Money handling | Uses `Decimal` and reservation accounting | Uses `Decimal` and centralized balance service | Both preserve the core financial direction |

L3 result:

- Positive for ControlCoding.
- Run B is the stronger release-evidence result because it preserves module
  separation better while passing a broader test set.

## L5 Dungeon Crawler Findings

| Area | Run A, no CC | Run B, with CC | Reading |
|---|---|---|---|
| Modification coverage | M1 through M8 completed | M1 through M8 completed | Both completed the requested feature sequence |
| Exact benchmark command | Bare `pytest` failed local verification | Bare `pytest` passed | Run B is operationally stronger |
| Declared test result | 6 tests passed with `python -m pytest` | 9 tests passed with `pytest` | Run B has stronger and cleaner verification |
| System split | Separate pathfinding and transition modules | Pathfinding lives inside movement system; transitions are coordinated elsewhere | Run A has a cleaner split for these two concerns |
| Save/load | Thin wrapper around domain serialization | Larger dedicated save/load system | Run B is more robust and explicit |
| Game coordinator pressure | Smaller `core/game.py` | Larger `core/game.py` | Run A is slimmer, but Run B covers more behavior |
| Hook evidence | No native inline hook events recorded | No native inline hook events recorded | Do not claim inline hook parity from this host |

L5 result:

- Mixed but favorable for ControlCoding.
- Run B wins on exact-command reliability, test depth, and save/load robustness.
- Run A has some cleaner subsystem separation, especially for pathfinding and
  transition behavior, but the import-path failure makes it weaker as release
  evidence.

## Overall Scorecard

| Dimension | L3 Result | L5 Result | Release reading |
|---|---|---|---|
| Feature completion | Tie | Tie | All requested modifications were implemented |
| Invariant verification | Run B stronger | Run B stronger | With-CC runs produced stronger test evidence |
| Exact command reliability | Run B stronger | Run B stronger | L5 Run A failed the expected bare `pytest` path |
| Module boundaries | Run B stronger | Mixed | L3 clearly favors B; L5 has tradeoffs |
| God-object pressure | Run B stronger | Mixed | L3 B keeps matching cleaner; L5 B has a larger game coordinator |
| Public claim safety | Good with caveats | Good with caveats | Evidence supports repo-side workflow, not inline hook parity |

## Residual Limits

- These are local external benchmark artifacts, not committed generated projects.
- Run B used ControlCoding context and local hook files, but this host did not
  surface native inline WARN or DENY events.
- L5 Run A passes only when invoked as `python -m pytest`, which is not the
  exact benchmark command.
- The scorecard compares generated outcomes and local verification, but it does
  not replace a fully instrumented benchmark harness with automatic diff scoring.

## Release Relevance

This R5 pair is suitable as release evidence for the following claims:

- the release benchmark kit can produce repeatable paired workspaces
- the current `AGENTS.md` plus `.controlcoding/` setup is usable in real runs
- ControlCoding-context runs produced stronger verification in both L3 and L5
- the benchmark protocol exposed a real operational defect in the no-CC L5 run:
  import-path fragility under the exact required test command

This R5 pair should not be used to claim:

- full inline hook enforcement in this host
- automatic GraphRAG or canonical memory behavior
- a universal win across every architecture dimension

## Decision

Accept this as positive R5 release evidence with caveats.

Recommended next step:

- close the release evidence package by linking this scorecard from the
  benchmark index and by using it in the release checklist as the current R5
  benchmark result.
