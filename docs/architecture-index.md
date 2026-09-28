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
| `cc_external.py` | Separate external-source knowledge/evidence facade, original provenance and stale evidence checks. |
| `cc_external_boundary.py` | Ordinary disjoint paths and permanent process/network/write refusal for the trusted external service. |
| `cc_docs.py` | Documentation maintenance commands. |
| `cc_document_reader.py` | Hash-bound Markdown reader with bounded project-local images. |
| `cc_documentation_observer.py` | Read-only Markdown inventory for the document map. |
| `cc_feature.py` | Feature lifecycle commands. |
| `cc_init_module.py` | Module initialization commands. |
| `cc_knowledge.py` | Embedded knowledge commands and opt-in maintenance worker. |
| `cc_knowledge_adoption.py` | Read-only adoption observations; no automatic acceptance. |
| `cc_knowledge_evaluate.py` | Offline scoring of frozen retrieval evaluation packets. |
| `cc_knowledge_review.py` | Convert blinded human citation judgments for an exact run. |
| `cc_memory.py` | Project Memory Engine facade. |
| `cc_panel_jobs.py` | Fixed desktop Core jobs: reviewed install/engagement/project setup, governed memory, doctor and canonical verification with process results. |
| `cc_panel_bridge.py` | One-request private stdio transport for the optional desktop observer; bounded versioned requests and sanitized results. |
| `cc_panel_configuration.py` | Versioned guided setup drafts, AI proposal handoff/review, detached Core plans and source-bound explicit application. |
| `cc_panel_transaction.py` | Windows exclusive writer for reviewed setup plans, rollback and interruption journal; no renderer-chosen destinations. |
| `cc_controlwork_manage.py` | Reviewed portable ControlWork initialization, selected text imports, captures and session summaries; bounded shared transaction writes. |
| `cc_controlwork_observer.py` | Bounded read-only ControlWork records, sessions, graph and retrieval over retained inputs; no initialization, indexing or provider calls. |
| `cc_project_map_model.py` | Pure, bounded Project Map validation, status projection and node explanations; no project reads or enforcement. |
| `cc_project_map_controls.py` | Read-only feature, evidence and scoped policy projections using canonical owners; no lifecycle edits, project commands or permission grants. |
| `cc_project_map_analyzers.py` | Pure bounded Python syntax metrics and conservative local import candidate matching; no filesystem reads or project execution. |
| `cc_project_map_analysis.py` | Retained-input quality/dependency projection, explicit coding-intent comparison and report links; no acceptance promotion or source writes. |
| `cc_project_map_definition.py` | Reviewed map identities and bounded definition persistence; source-bound preview and exclusive Windows commit, no lifecycle or permission edits. |
| `cc_project_map_sources.py` | Bounded read-only source inventory and static structural proposals for Project Map; no target execution or writes. |
| `cc_review.py` | Review command helpers. |
| `cc_setup.py` | Setup workflow helpers. |
| `cc_setup_service.py` | Bounded read-only setup observations and canonical minimal-init previews; no project installation or memory initialization. |
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
| `cc_memory_lib/knowledge_adoption.py` | Adoption receipt observations and elapsed-coverage checks. |
| `cc_memory_lib/knowledge_archive_stream.py` | Streaming logical archive serialization. |
| `cc_memory_lib/knowledge_backup.py` | Bounded backup and restore into an absent archive. |
| `cc_memory_lib/knowledge_catalog.py` | Bounded knowledge graph transport. |
| `cc_memory_lib/knowledge_consolidation.py` | Bounded persistent manual consolidation proposals and source snapshots. |
| `cc_memory_lib/knowledge_consolidation_privacy.py` | Forget propagation through consolidation jobs and derived claims. |
| `cc_memory_lib/knowledge_consolidation_review.py` | Atomic selected wiki publication and guarded undo. |
| `cc_memory_lib/knowledge_consolidation_store.py` | Explicit schema migration and durable consolidation tables. |
| `cc_memory_lib/knowledge_consolidation_validation.py` | Structural and semantic archive validation before backup, restore and review commits. |
| `cc_memory_lib/knowledge_consolidation_execution.py` | Durable bounded attempts, strict proposal imports and event queue execution policy. |
| `cc_memory_lib/knowledge_consolidation_execution_validation.py` | Version-three attempt, queue, progress and metadata integrity checks. |
| `cc_memory_lib/knowledge_consolidation_selection.py` | Incremental original-passage selection and revision-aware analysis progress. |
| `cc_memory_lib/knowledge_consolidation_context.py` | Current approved-memory context, original citations, backlinks and bounded history. |
| `cc_memory_lib/knowledge_consolidation_prior.py` | Bounded relevant approved-memory snapshots backed by current original evidence. |
| `cc_memory_lib/knowledge_checkpoints.py` | Durable acquisition checkpoint batches. |
| `cc_memory_lib/knowledge_dev.py` | Read-only canonical Dev projections. |
| `cc_memory_lib/knowledge_evaluation.py` | Frozen evaluation contracts and scoring. |
| `cc_memory_lib/knowledge_following.py` | Operator source-status ledger. |
| `cc_memory_lib/knowledge_graph.py` | Snapshot-bound graph windows. |
| `cc_memory_lib/knowledge_identity.py` | Stable source identity and unambiguous rename detection. |
| `cc_memory_lib/knowledge_import.py` | Rich-document extraction from verified bytes. |
| `cc_memory_lib/knowledge_library.py` | Server-side source, wiki and conversation pagination. |
| `cc_memory_lib/knowledge_ocr.py` | Reviewed OCR sidecars bound to original PDF bytes. |
| `cc_memory_lib/knowledge_query.py` | Ranked current-passage retrieval with graph metadata. |
| `cc_memory_lib/knowledge_ranking.py` | Streaming lexical statistics and BM25 ranks. |
| `cc_memory_lib/knowledge_read_batches.py` | Pinned-ancestor reuse for bounded file batches. |
| `cc_memory_lib/knowledge_scan.py` | Resumable source acquisition. |
| `cc_memory_lib/knowledge_semantic.py` | Opt-in loopback neural embeddings. |
| `cc_memory_lib/knowledge_service.py` | Embedded source, wiki, conversation and query coordinator. |
| `cc_memory_lib/knowledge_source_errors.py` | Persistent acquisition diagnostics and retry state. |
| `cc_memory_lib/knowledge_sources.py` | Approved-source snapshots and passage identities. |
| `cc_memory_lib/knowledge_staging.py` | Private preparation checkpoints before publication. |
| `cc_memory_lib/knowledge_store.py` | Serialized local archive transactions. |
| `cc_memory_lib/knowledge_vector_rank.py` | Bounded-batch exact vector ranking. |
| `cc_memory_lib/knowledge_wiki.py` | Source-bound topic pages and unreviewed AI drafts. |
| `cc_memory_lib/knowledge_retrieval.py` | Bounded graph/wiki retrieval, filters and recorded history. |
| `cc_memory_lib/knowledge_wiki_tools.py` | Revision comparison, reviewed findings, backlinks and section recovery. |
| `cc_memory_lib/knowledge_wiki_review.py` | Protected human sections and revision-bound review proposals. |
| `cc_memory_lib/knowledge_work.py` | Reviewed work relations over observed canonical sources. |
| `cc_memory_lib/knowledge_work_controls.py` | Shared feature-owner projections for work views. |
| `cc_memory_lib/knowledge_work_schedule.py` | Revision-bound work planning overlay, process dependencies, calendar estimates, blockers and critical path. |
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
| `cc_layout.py` | Validated, opt-in contained storage routing shared with installed hooks. |
| `cc_layout_cli.py` | Storage initialization, explicit backed-up migration and guarded recovery. |

## Verification

Run:

```text
cc index --check
cc doctor --strict-claims --strict-verification --strict-invariants --release
```

Both commands fail closed when the configured public index is missing,
unreadable, or incomplete.
