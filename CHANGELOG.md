# Changelog

All notable changes to ControlCoding are documented in this file.

## Unreleased

### Fixed

- Replace automatic recovery in the optional Bash post-tool inspector with
  protected-path observations. It no longer restores or deletes inspected files,
  avoids Git content-conversion callbacks, and bounds tracked content reads.
  Root and path identity checks retain explicit incomplete-inspection limits.
- Check Core runtime prerequisites before affected setup and memory writes, and
  propagate setup failures. Core requires Python 3.11 or later; memory also
  requires SQLite deserialize support.
- Preflight selected pack and init outputs, expose read-only previews, preserve
  existing custom files, and refuse conflicts before planned writes. Preserve
  supported zone maps and legacy hook routing. These bounds do not make the
  complete setup/update/removal workflow transactional.
- Quote resolved hook script operands for spaced paths while preserving supported
  argument tails, and generate Cursor rules with leading alwaysApply metadata.
- Retry Windows lock-file creation access denial within the existing deadline,
  allowing a delete-pending handle to close while keeping persistent denial an
  error. Retain richer worker diagnostics; the earlier CI incident is not
  retrospectively attributed to this demonstrated failure mode.
- Prevent consultation thread ID collisions from overwriting earlier decisions.
- Restore Windows rollback permissions and timestamps through the validated,
  locked file handle on Python 3.11 and 3.13.

### Changed

- Bind verification evidence to source, execution context and the complete
  ordered selection; distinguish current complete passes from partial, stale,
  legacy, invalid and incomplete evidence. Add public-example characterization.
- Separate semantic-backend configuration, availability, requests and use;
  report effective candidate/time limits. The API adapter remains unavailable,
  and a configured local subprocess is not a network sandbox.
- Clarify privacy/redaction, host delivery, repository-gate and approval limits;
  strengthen tests that bind redaction assertions to retrieved source content.
- Expand required regression coverage and configure Windows/Ubuntu CI with
  Python 3.11/3.13, an explicit Python 3.10 rejection runtime, hashed dependency
  pins and retained verification diagnostics.

### Validation and limitations

The Core update was merged into `master` on September 20, 2026 as
[`75e2dd67`](https://github.com/Naimas/ControlCoding/commit/75e2dd67b805fe34d7ca587b23459a84a2d6d073).
Its [post-merge CI run](https://github.com/Naimas/ControlCoding/actions/runs/35520938199)
passed all four Windows/Ubuntu and Python 3.11/3.13 jobs on the first attempt.
Each job accounted for the same 2,078 canonical test cases and passed all ten
required checks. Windows reported 2,069 passes/nine platform skips per runtime;
Ubuntu reported 2,047 passes/31 platform skips. Every skipped case passed on
the opposite OS in the same runtime. Locked dependency and candidate installation
passed, with complete verification and invariant receipts. Applicable POSIX/FIFO,
symlink-policy and unsupported-Python-3.10 rejection controls were executed.

These results are bound to that exact commit and the maintained required
contract, which is a subset of the repository tests. They do not constitute
stable-release approval or named editor-host loading/event-delivery evidence.
Whole-setup preservation, update/removal, consent/data handling and representative
memory evaluation remain bounded or unverified. ControlWork remains separate.

The [current Core download](README.md#download-current-core) includes these
updates. The `v3.0.2` tagged archives predate them. Entry-point documentation and
the historical release notice now distinguish those downloads explicitly;
no new version, tag or release is introduced by this documentation update.

## 3.0.2

### Changed - Unified PolyForm Shield Licensing
- Licensed current ControlCoding material and embedded ControlWork components,
  including documentation and templates, under PolyForm Shield 1.0.0.
- Permitted internal and noncompeting commercial use of both products while
  reserving competing, white-label, OEM, resale, and hosted-product uses for a
  separate written license.

### Added - Memory V2 Core
- Added deterministic document classification for scanned project documents.
- Added semantic chunk records with continuation links for large sections.
- Added cross-document correlation suggestions from explicit links, shared
  headings, and shared keywords.
- Added `cc memory chunks` and generated `GRAPH_INDEX.md` output.
- Added impact/context reason strings so ranked memory results explain why they
  were selected.
- Added audited lifecycle workflows for mark, supersede, and conflict actions.
- Added a rebuildable local sparse vector index adapter with vector rebuild and
  search commands.

## 3.0.1

`3.0.1` is the first release published from the clean public-source repository.
The stage identity regression test was made deterministic by keeping the
original file alive while creating its replacement. Production behavior did
not change.

ControlCoding software is published under PolyForm Shield 1.0.0, allowing
internal and noncompeting commercial use. Documentation and methodology are
published under CC BY-SA 4.0. Embedded and standalone ControlWork components
retain their separate noncommercial license boundary.

## 3.0.0

The `3.0.0` section is retained for migration context. The clean public
repository begins at `3.0.1` and does not contain earlier private-repository
tags. Release `3.0.1` supersedes `3.0.0`; the major-release history follows.

### Changed - Major Public CLI Alignment
- Removed the public `cc replace start/status/complete` routes. There is no
  direct replacement command.
- Changed `cc benchmark compare` to accept positional baseline and current JSON
  paths. `cc benchmark report` now builds its report from the current project
  evidence rather than accepting the former `--input` option.
- Changed the default benchmark outputs to `benchmarks/run.json` and
  `benchmarks/report.md`.
- Made `cc organize` preview-only by default. Use `--apply` to execute proposed
  moves and writes; `--dry-run` and `--check` remain non-applying paths.
- Made `cc resume` provider-neutral and print-only. It does not select or launch
  Claude or another provider, and `--brief` remains accepted for CLI
  compatibility without changing current behavior.

### Changed - Adapter And Release Contracts
- Added ownership-marked adapter handling with mandatory preview, owned-only
  `--force`, and explicit adoption through `cc context adopt` followed by
  `cc context adopt --apply` when the target is eligible. Invalid, ambiguous,
  and unsafe targets remain blocked.
- Pinned the embedded Work Plane conformance profile to
  `controlwork-work-plane/1.0.0`.
- Required `NOTICE` and `TRADEMARKS.md` in the public release package contract.

See [Quick Start](docs/quick-start.md),
[Install ControlCoding On Your Project](docs/install-controlcoding-on-your-project.md),
[Cross-Tool Guide](docs/cross-tool-guide.md), and
[CLI Tools Reference](docs/ccdocs/tools-reference.md) for migration steps.

## v2.4.0 (April 2026)

### Added - Project Memory Engine
- Added local Project Memory Engine storage under `.controlcoding/`.
- Added `cc memory` CLI commands for init, doctor, scan, status, impact,
  context, notes, ideas, decisions, consults, agent runs, generated views, and
  sync reports.
- Added `document-only` mode for detachable document/work-memory use without
  adopting the full ControlCoding workflow.
- Added stable memory ID conventions for ControlCoding development memory and
  application-owned memory references.
- Added targeted tests for the Project Memory Engine command surface.
- Added public documentation for the initial memory workflow.

### Boundary
- ControlCoding development memory remains separate from application runtime
  memory, vector stores, RAG corpora, and application-owned data.

## v3.1.0-alpha (March 2026)

### Added - Agentic Ecosystem
- **Concierge Orchestrator** (`concierge.py`): state machine (10 states), main entry point for multi-agent workflow. Architect as permanent expert, Planner as internal function, Tandem as debate protocol.
- **Planner** (`planner.py`): maieutic planning module. 6-round Socratic expansion, domain-adapted questions (financial, game, physics, web), state machine (EMPTY->DRAFT->APPROVED->IN_PROGRESS->COMPLETED).
- **Tandem Debate** (`mcp_consultant.py`): cross-AI comparison protocol. Ping-pong rounds, convergence detection, Socratic escalation on deadlock.
- **Verification Engine** (`verification_agent.py`): DAG-based criteria tracking, 5 import sources (plan, audit, violations, verify, tandem), budget enforcement.
- **CodeWarden Structured Output**: LLM code review with CWViolation JSON format, auto-import to verification engine.
- **Control Plane** (`control_plane_utils.py`): shared utilities for atomic writes, event logging, ID generation, engagement config.
- **Scientist Role** in mcp_consultant.py: validates implementation against theoretical spec.

### Added - Engagement System (S6)
- **Engagement Levels** (1-4): Conservative (zero LLM), Guided, Active, Full. Configured via `.claude/cc_engagement.json`.
- **Authority/Precedence Model**: hooks > engagement config > Concierge > manual lift. Documented in methodology section 16b.
- **plan_approve() Auto-Chain**: on approval, automatically exports criteria + zone mapping (idempotent, fail-safe).
- **Domain Auto-Detection**: parses CLAUDE.md Project Identity for stack/domain keywords.
- **`cc setup --engagement`**: interactive wizard (3 questions: level, backend, budget).
- **Engagement Gating**: hooks and MCP servers skip LLM calls at level 1.

### Added - Concierge Integration (S8)
- **Concierge Engagement Gating**: respects cc_engagement.json, skips disabled agents (not error).
- **State Recovery**: phase-entry/exit markers, operation status tracking for crash resume.
- **Budget Integration**: centralized counter check before LLM calls.
- **`cc doctor --json`**: machine-readable health report output.

### Added - Auditor Deepening (S7)
- **3 Agent Profiles**: Engineering, Security, Design Intent (with identity, competencies, limitations).
- **15 Skill Files**: operative procedures with grep patterns, checklists, report formats.
  - Engineering: dependency-analysis, error-handling-review, test-assessment, architecture-conformance, configuration-audit
  - Security: injection-scan, secrets-detection, dependency-audit, file-system-review, auth-assessment
  - Design Intent: intent-extraction, implementation-tracing, deviation-classification (9 types), cross-cutting-analysis, ai-smell-detection (8 smells)
- **Auto-Import**: audit commands call `import-audit` after report generation.
- **Concierge Audit Invocation**: engineering audit at FINAL_REVIEW phase boundary.

### Changed
- Test count: 472 -> 858 (+386 tests across S0-S7 sprints)
- Concierge replaces manual agent orchestration
- Reviewer produces structured JSON (PASS/FAIL per criterion), not free-text
- Dashboard (`cc_dashboard.py`) deprecated in favor of planned UI-Wizard

### Architecture
- 8 thinking agents: Concierge, Architect, Coder, Reviewer, Debugger, Socratic, CodeWarden, Expert on-demand
- 2 protocols: Planner (Concierge function), Tandem (debate protocol)
- 13 hooks, 5 MCP servers, 1 CLI tool (cc.py with 6 commands)
- Milestone A: PASSED (10/10 steps, 719 tests at gate)

---

## v2.1 (March 2026)

### Added
- **Session Management MCP** (`mcp_session.py`): checkpoint, devlog, history, automated STATUS.md
- **Multi-Agent Bridge** (`mcp_bridge.py`): filesystem-based inter-session communication
- **External Consultation MCP** (`mcp_consultant.py`): 5 specialized roles (debug, architect, planner, reviewer, socratic), agent-mode with web search, 4 backends (Claude CLI, Ollama, OpenAI-compatible, Anthropic API)
- **Monitoring Dashboard** (`cc_dashboard.py`): read-only Gradio web UI for agents, metrics, debug activity
- **Vision Agent** (`mcp_vision.py`, `verification_agent.py`): DAG-based verification orchestrator with budget system
- **Interactive Visual Testing** (`visual_test.py`): generic action system with input control for L2+ debug
- **SessionEnd Integrity Hook** (`session_end_check.py`): detects DENY file tampering, rollback on Stop
- **Peer Review Prompt** (`cc review`): generates review prompts from git diff against CLAUDE.md
- **Central Hooks** (`--central-hooks`): shared hook installation for multi-project setups
- **Auto-gitignore**: `cc init` generates `.gitignore` entries for CC artifacts
- **Workflow Enforcement Hook** (`check_workflow.py`): advisory prerequisites for Edit/Write
- **CodeWarden Plan Review** (`codewarden_plan_review.py`): PreToolUse on ExitPlanMode, reviews plan against CLAUDE.md
- **Bash Bypass Protection** (`check_bash_writes.py`): PostToolUse hook detects and reverts writes to DENY zones via Bash
- **Scoped Lift System** (`request_lift.py`): temporary zone unlock with human approval, single-use, 1-hour expiry
- **Multi-zone lift**: per-zone consumption instead of global (each zone is single-use independently)
- **Fitness Functions** (`fitness_check.py`): architectural fitness checks with delta thresholds
- **Metrics Collector** (`metrics_collector.py`): unified metrics for CC projects
- **472 automated tests** across 18 test files

### Changed
- Hooks load protected zones from `cc_config.json` instead of inline lists
- Self-protection list expanded: check_bash_writes.py, request_lift.py added
- CLAUDE.md.template updated with all v2.1 sections (199 lines)
- Documentation split into methodology, hooks-reference, tools-reference, cookbook
- Cross-tool guide expanded: Codex CLI, Gemini CLI, GitHub Copilot, OpenCode added

### Fixed
- Multi-zone lift consuming entire lift on first operation
- Python3 shebang in codewarden_postcommit
- Visual check tests cross-platform compatibility
- Bridge test artifacts redirected to system temp

## v2.0 (February 2026)

### Added
- Complete rewrite from v1: one CLAUDE.md replaces five separate files
- Condominium Architecture: stable/shared/features/workspace zones
- Maturity Levels L1-L4 with explicit adoption triggers
- Boundary enforcement hooks (PreToolUse on Edit/Write)
- Dangerous command blocking (PreToolUse on Bash)
- Domain invariants as mandatory gates
- CodeWarden guardian agent (Stop hook with LLM review)
- Debug escalation: L1 protocol, L2 visual feedback, L3 external consultation
- Violation store (JSONL cross-session memory)
- CLI tool (`cc.py`): setup, init, install, doctor, review
- Phase 0 auto-discovery (`phase0_discover.py`)
- ADR template (Nygard format)
- Gradual adoption model with calibrated triggers

### Changed
- From process-heavy (v1) to automation-first (v2)
- From trust-based enforcement to mechanical enforcement
- From five files (CODEMAP, CONTRACTS, METRICS, RenameSpec, ChangeSpec) to one (CLAUDE.md)

## v1.0 (2024)

- Initial version for procedural generation project
- Process-oriented: CODEMAP, CONTRACTS, METRICS, RenameSpec, ChangeSpec
- Trust-based enforcement (documentary rules only)
