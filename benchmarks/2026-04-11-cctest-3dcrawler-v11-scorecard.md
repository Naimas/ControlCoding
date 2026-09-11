# Benchmark Scorecard: CCTEST-3dcrawler-CC-v11

## Scope

This note records a curated benchmark result from an external local workspace.
It is intentionally narrower than a full A/B benchmark package:

- one real run with ControlCoding enabled
- public-safe profile only
- raw workspace and bulky artifacts remain outside this repo

This file exists to provide release evidence without committing the generated
benchmark project itself.

Use it together with `2026-04-11-cctest-3dcrawler-v11-review-check.md`.
This scorecard proves the technical run; the review check covers prompt/workflow
closure and explicitly records what was only partial or not proven.

## Benchmark Identity

- Date: `2026-04-11`
- Workspace: `<benchmark-workspace>`
- Build target: `DungeonCrawler3D v11`
- Session log: `<benchmark-workspace>/SESSION_BENCHMARK_LOG.md`
- Raw verification report: `<benchmark-workspace>/benchmark-build/verify/verification_report.json`
- Raw verification screenshot: `<benchmark-workspace>/benchmark-build/verify/verification_scene.png`
- Raw test log: `<benchmark-workspace>/benchmark-build/Testing/Temporary/LastTest.log`

## ControlCoding Setup Profile

Bootstrap was executed from this repo with the standard setup flow. Replace
`<controlcoding-root>` with the absolute path to the ControlCoding checkout:

```powershell
python "<controlcoding-root>/scripts/cc.py" setup --project-root .
python "<controlcoding-root>/scripts/cc.py" setup --engagement --project-root .
python "<controlcoding-root>/scripts/cc.py" doctor --project-root .
```

Wizard choices recorded in the external session log:

- host: `Codex CLI`
- tier: `Core`
- document management: active
- hooks: local
- artifact storage: local
- backend policy: `approved`
- budget: `100`

Generated CC artifacts in the benchmark workspace included:

- `CONTROLCODING.md`
- `AGENTS.md`
- `.claude/settings.json`
- `.claude/cc_config.json`
- `.claude/cc_engagement.json`
- `.claude/gateway_config.json`

## Execution Commands

Fresh benchmark execution was run from the external workspace with:

```powershell
cmake --preset debug
cmake --build --preset debug --parallel 1
ctest --preset debug --output-on-failure
.\benchmark-build\bin\Debug\dungeoncrawler3d.exe --verify-run .\benchmark-build\verify\verification_report.json
```

## Result Summary

| Check | Result | Evidence |
|---|---|---|
| CC bootstrap | PASS | session log |
| `cc doctor` | PASS | session log |
| Configure | PASS | session log |
| Build | PASS | session log |
| Tests | PASS (`7/7`) | `LastTest.log` |
| Runtime verification | PASS | `verification_report.json` + screenshot |
| Manual GUI validation | PASS (general positive outcome) | session log |

## Timing Scorecard

| Step | Time |
|---|---:|
| `cc_doctor` | `0.233 s` |
| `cmake_configure_fresh` | `11.259 s` |
| `cmake_build_debug_fresh` | `32.916 s` |
| `ctest_debug_fresh` | `0.165 s` |
| `runtime_verify_fresh` | `1.709 s` |
| **Total** | **`46.282 s`** |

## Automated Verification Snapshot

Values below come from the raw verification report:

| Metric | Value |
|---|---|
| `cameraVerified` | `true` |
| `movementVerified` | `true` |
| `interactionVerified` | `true` |
| `doorsOpened` | `1` |
| `chestsOpened` | `1` |
| `attacksLanded` | `0` |
| `enemiesKilled` | `0` |
| `secretsRevealed` | `0` |
| `won` | `false` |
| `elapsedTime` | `4.6000 s` |

## Manual Validation Note

The session log records a separate manual GUI inspection by the user after the
automated run. The recorded outcome was positive at a general product level,
and explicitly treated as separate evidence from the automated checks.

## Residual Limits

These limits were explicitly recorded in the external session log and should be
kept visible in release framing:

- the automated gameplay script does not yet complete the whole level
- the automated report does not yet register a successful melee hit
- manual validation confirms general usability and presentation, but does not replace more granular gameplay verification

## Release Relevance

This run is useful release evidence for the following claims:

- a fresh project can bootstrap ControlCoding from a blank folder
- the `Codex CLI + Core` path is operational in a real project
- the generated host/context artifacts are sufficient to complete a non-trivial build
- the resulting project passes build, test, and runtime verification in one session

This run is not a complete benchmark protocol and is not an A/B comparison
against a no-ControlCoding run.
