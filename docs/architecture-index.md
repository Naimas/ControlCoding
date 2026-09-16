# Public Architecture Index

This release-visible index inventories the Python implementation surfaces that
the `cc index --check` gate verifies. The release doctor treats missing or
incomplete coverage as a blocking issue.

The public source release excludes `dev/**`. Development workspaces that retain
`dev/ARCHITECTURE_INDEX.md` continue to use that LAB index instead.

## MCP Servers (`templates/scripts/`)

| File | Public role |
|---|---|
| `agent_runner.py` | Deferred agent subprocess runner for supported experimental flows. |
| `architect_agent.py` | Architecture specialist implementation. |
| `base_agent.py` | Shared agent lifecycle and tool-calling foundation. |
| `cc_audit.py` | Generic ControlCoding compliance audit utility. |
| `cc_dashboard.py` | Local dashboard implementation. |
| `cc_lockfile.py` | Stdlib-only adjacent lockfile guard. |
| `coder_agent.py` | Bounded implementation specialist. |
| `concierge.py` | Legacy direct orchestration workflow. |
| `concierge_agent.py` | Concierge workflow agent. |
| `consult.py` | Command-line consultant wrapper. |
| `consultation_protocol.py` | Structured consultation routing and convergence rules. |
| `control_plane_utils.py` | Shared control-plane utilities. |
| `debugger_agent.py` | Hypothesis-first debugging specialist. |
| `expert_agent.py` | Configurable domain specialist. |
| `mcp_agent_memory.py` | Per-agent memory MCP surface. |
| `mcp_bridge.py` | Filesystem message bridge for concurrent sessions. |
| `mcp_consultant.py` | Explicit multi-role consultant MCP surface. |
| `mcp_handoff.py` | Context handoff MCP surface. |
| `mcp_session.py` | Session documentation MCP surface. |
| `mcp_vision.py` | Visual inspection MCP surface. |
| `planner.py` | Planning expansion support. |
| `reviewer_agent.py` | Structured review specialist. |
| `session_protocol.py` | JSON-lines session protocol. |
| `socratic_agent.py` | Assumption and risk analysis specialist. |
| `verification_agent.py` | Verification orchestration specialist. |
| `verification_report.py` | Verification report generation. |
| `visual_agent.py` | Visual quality specialist. |
| `visual_check.py` | Screenshot-based visual checks. |
| `visual_check_utils.py` | Shared visual-check utilities. |
| `visual_test.py` | Visual test runner. |

## Hooks (`templates/hooks/`)

| File | Public role |
|---|---|
| `check_bash_writes.py` | Post-command boundary backstop. |
| `check_boundaries.py` | Protected-zone enforcement hook. |
| `check_dangerous_commands.py` | Dangerous command preflight hook. |
| `check_doc_compression.py` | Semantic-fidelity document guard. |
| `check_file_organization.py` | File-placement policy check. |
| `check_index_staleness.py` | Architecture index staleness advisory. |
| `check_repo_boundaries.py` | Repository-side staged boundary gate. |
| `check_workflow.py` | Workflow-state enforcement hook. |
| `codewarden_backend.py` | Shared CodeWarden backend support. |
| `codewarden_plan_review.py` | Plan review hook. |
| `codewarden_postcommit.py` | Post-commit CodeWarden check. |
| `codewarden_review.py` | Inline CodeWarden review hook. |
| `feature_lock.py` | Feature perimeter enforcement. |
| `hook_logger.py` | Shared hook logging. |
| `hook_utils.py` | Shared hook utilities. |
| `ops_logger.py` | File-operation audit logging. |
| `request_lift.py` | Scoped boundary-lift request workflow. |
| `session_end_check.py` | Session closeout consistency check. |
| `violation_store.py` | CodeWarden violation persistence. |

## CLI Tool and Supporting Modules (`scripts/`)

| File | Public role |
|---|---|
| `cc.py` | Main ControlCoding CLI and governance command router. |
| `cc_evidence.py` | Local verification/invariant receipts, coverage and current-evidence assessment. |
| `cc_evidence_inputs.py` | Bounded input snapshots, confined file reads and runner/context identity. |
| `cc_evidence_process.py` | Bounded command execution and metadata-only pipe capture. |
| `cc_docs.py` | Documentation maintenance commands. |
| `cc_feature.py` | Feature lifecycle commands. |
| `cc_init_module.py` | Module initialization commands. |
| `cc_memory.py` | Project Memory Engine facade. |
| `cc_review.py` | Review command helpers. |
| `cc_setup.py` | Setup workflow helpers. |
| `codewarden_summary.py` | CodeWarden summary reporting. |
| `controlwork_mcp.py` | Optional read-only ControlWork MCP surface. |
| `fitness_check.py` | Architectural fitness checks. |
| `metrics_collector.py` | Local metrics collection. |
| `phase0_discover.py` | Phase-zero codebase discovery. |

### Project Memory Engine (`scripts/cc_memory_lib/`)

| File | Public role |
|---|---|
| `cc_memory_lib/bootstrap.py` | Memory initialization and bootstrap. |
| `cc_memory_lib/chunks.py` | Chunk extraction and storage support. |
| `cc_memory_lib/classifier.py` | Memory classification. |
| `cc_memory_lib/commands.py` | Memory command implementation. |
| `cc_memory_lib/correlation.py` | Cross-record correlation. |
| `cc_memory_lib/cross_plane.py` | Cross-plane compatibility support. |
| `cc_memory_lib/entities.py` | Memory entity contracts. |
| `cc_memory_lib/evidence.py` | Evidence handling. |
| `cc_memory_lib/export.py` | Memory export support. |
| `cc_memory_lib/extractors.py` | Structured content extraction. |
| `cc_memory_lib/freshness_projection.py` | Read-only freshness and index observation. |
| `cc_memory_lib/graph.py` | Memory graph operations. |
| `cc_memory_lib/ids.py` | Stable identifier helpers. |
| `cc_memory_lib/impact.py` | Change impact analysis. |
| `cc_memory_lib/ledger.py` | Transactional memory ledger. |
| `cc_memory_lib/lifecycle.py` | Memory lifecycle transitions. |
| `cc_memory_lib/lockfile.py` | Memory write locking. |
| `cc_memory_lib/memory_eval.py` | Memory evaluation suite. |
| `cc_memory_lib/migrations.py` | Memory schema migrations. |
| `cc_memory_lib/ocr.py` | OCR workflow support. |
| `cc_memory_lib/op_index.py` | Operations index and startup packets. |
| `cc_memory_lib/privacy.py` | Privacy filtering. |
| `cc_memory_lib/rag_pack.py` | Retrieval packet construction. |
| `cc_memory_lib/retrieve.py` | Memory retrieval. |
| `cc_memory_lib/scanner.py` | Project scanning. |
| `cc_memory_lib/schema.py` | Memory schema definitions. |
| `cc_memory_lib/scoring.py` | Retrieval scoring. |
| `cc_memory_lib/semantic.py` | Optional semantic retrieval support. |
| `cc_memory_lib/sessions.py` | Session memory lifecycle. |
| `cc_memory_lib/store.py` | SQLite-backed memory storage. |
| `cc_memory_lib/vector.py` | Sparse vector indexing. |
| `cc_memory_lib/views.py` | Derived memory views. |
| `cc_memory_lib/work_dashboard.py` | Work-plane dashboard projection. |
| `cc_memory_lib/work_features.py` | Work feature tracking. |
| `cc_memory_lib/work_graph_ops.py` | Work graph operations. |
| `cc_memory_lib/work_project_map.py` | Project map projection. |
| `cc_memory_lib/work_query.py` | Work-plane query support. |
| `cc_memory_lib/work_review_queue.py` | Work review queue. |

## Verification

Run:

```text
cc index --check
cc doctor --strict-claims --strict-verification --strict-invariants --release
```

Both commands fail closed when the configured public index is missing,
unreadable, or incomplete.
