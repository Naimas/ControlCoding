"""Project Memory Engine facade for the ControlCoding CLI.

This module preserves the public `from cc_memory import ...` surface while the
implementation lives in smaller stdlib-only modules under `cc_memory_lib`.
The memory plane remains local to `.controlcoding/` and separate from any
application-owned runtime memory.
"""

from __future__ import annotations

from cc_memory_lib.commands import (
    add_memory_scan_parser,
    cmd_memory_agent_run_record,
    cmd_memory_consult_record,
    cmd_memory_decision_add,
    cmd_memory_dev_context_pack,
    cmd_memory_doctor,
    cmd_memory_idea_add,
    cmd_memory_init,
    cmd_memory_intake_add,
    cmd_memory_intake_promote,
    cmd_memory_note_add,
    cmd_memory_scan,
    cmd_memory_work_attach,
    cmd_memory_work_analyze,
    cmd_memory_work_category_add,
    cmd_memory_work_category_approve,
    cmd_memory_work_category_list,
    cmd_memory_work_category_propose,
    cmd_memory_work_capture,
    cmd_memory_work_checkpoint,
    cmd_memory_work_dashboard,
    cmd_memory_work_export,
    cmd_memory_work_graph,
    cmd_memory_work_handoff,
    cmd_memory_work_context_pack,
    cmd_memory_work_import,
    cmd_memory_work_import_source,
    cmd_memory_work_init,
    cmd_memory_work_ocr,
    cmd_memory_work_promote,
    cmd_memory_work_query,
    cmd_memory_work_quickstart,
    cmd_memory_work_rag_pack,
    cmd_memory_work_retrieve,
    cmd_memory_work_review,
    cmd_memory_work_scan,
    cmd_memory_work_mcp_call,
    cmd_memory_work_mcp_tools,
    cmd_memory_work_obsidian,
    cmd_memory_work_parity,
    cmd_memory_work_session,
    cmd_memory_work_sync,
    cmd_memory_work_status,
    cmd_memory_work_views,
    cmd_memory_work_wiki,
)
from cc_memory_lib.cross_plane import CROSS_PLANE_PACKET_TYPE, cmd_memory_cross_pack
from cc_memory_lib.chunks import cmd_memory_chunks, cmd_memory_layout
from cc_memory_lib.evidence import EVIDENCE_REF_VERSION, cmd_memory_evidence_list, cmd_memory_evidence_show
from cc_memory_lib.memory_eval import MEMORY_EVAL_VERSION, cmd_memory_eval_run
from cc_memory_lib.bootstrap import cmd_memory_bootstrap
from cc_memory_lib.graph import (
    EDGE_TYPE_TO_GRAPH_EDGE,
    ENTITY_TYPE_TO_GRAPH_NODE,
    GRAPH_CONTRACT_VERSION,
    SUGGESTION_TYPE_TO_GRAPH_EDGE,
    cmd_memory_graph_accept,
    cmd_memory_graph_around,
    cmd_memory_graph_reject,
    cmd_memory_graph_status,
    cmd_memory_graph_suggestions,
)
from cc_memory_lib.export import cmd_memory_graph_export
from cc_memory_lib.ids import make_memory_entity_id
from cc_memory_lib.impact import cmd_memory_context, cmd_memory_impact, cmd_memory_status
from cc_memory_lib.lifecycle import (
    cmd_memory_lifecycle_conflict,
    cmd_memory_lifecycle_mark,
    cmd_memory_lifecycle_supersede,
    cmd_memory_temp_cleanup,
)
from cc_memory_lib.op_index import STARTUP_INTENTS, cmd_memory_op_index, cmd_memory_startup
from cc_memory_lib.ocr import OCR_ADAPTER_INTERFACE_VERSION, cmd_memory_ocr_run, cmd_memory_ocr_status
from cc_memory_lib.privacy import PRIVACY_SCRUB_VERSION
from cc_memory_lib.retrieve import cmd_memory_retrieve
from cc_memory_lib.rag_pack import cmd_memory_rag_pack
from cc_memory_lib.semantic import SEMANTIC_ADAPTER_INTERFACE_VERSION, cmd_memory_semantic_status
from cc_memory_lib.sessions import (
    cmd_memory_session_close,
    cmd_memory_session_link,
    cmd_memory_session_list,
    cmd_memory_session_note,
    cmd_memory_session_pack,
    cmd_memory_session_show,
    cmd_memory_session_start,
    cmd_memory_session_views,
    session_status_payload,
)
from cc_memory_lib.schema import (
    CORRELATION_CONFIDENCE_LEVELS,
    CONTROLCODING_VERSION,
    CONTROL_DIRNAME,
    DB_FILENAME,
    DOCUMENT_LAYOUT_EDGE_TYPES,
    DOCUMENT_LAYOUT_NODE_TYPES,
    ENTITY_TYPE_CODES,
    EVENT_TYPES,
    HOST_CONTEXT_FILES,
    INSTALL_MODE_STORAGE,
    LOGS_DIRNAME,
    MANIFEST_FILENAME,
    MEMORY_DIRNAME,
    REQUIRED_LOGS,
    REQUIRED_VIEWS,
    SCHEMA_VERSION,
    SESSION_EDGE_TYPES,
    SESSION_RECORD_SCHEMA_VERSION,
    SKIP_DIRS,
    TEXT_EXTENSIONS,
    VALID_DOCUMENT_FACETS,
    VALID_DOCUMENT_TYPES,
    VALID_INSTALL_MODES,
    VALID_LIFECYCLES,
    VALID_MEMORY_PROFILES,
    VALID_SESSION_MODES,
    VALID_SESSION_STATUSES,
    VIEWS_DIRNAME,
    WORK_MEMORY_PROFILE_DIRS,
)
from cc_memory_lib.vector import (
    LOCAL_SPARSE_ADAPTER,
    cmd_memory_vector_rebuild,
    cmd_memory_vector_search,
)
from cc_memory_lib.views import cmd_memory_sync_report, cmd_memory_views_generate

MEMORY_AUX_ROUTED_COMMANDS = frozenset({
    ("memory", "evidence"),
    ("memory", "evidence", "show"),
    ("memory", "evidence", "list"),
    ("memory", "eval"),
})


def add_memory_aux_parser(memory_sub, path_type, argparse_module):
    p_memory_evidence = memory_sub.add_parser(
        "evidence",
        help="Resolve derived evidence refs from Dev GraphRAG packets",
    )
    evidence_sub = p_memory_evidence.add_subparsers(dest="memory_evidence_command")
    p_memory_evidence_show = evidence_sub.add_parser(
        "show",
        help="Show one local evidence ref by nodeId, evidenceRefId, id, or path",
    )
    p_memory_evidence_show.add_argument("--project-root", type=path_type, default=argparse_module.SUPPRESS, help="Project root directory (default: current directory)")
    p_memory_evidence_show.add_argument("selector", help="nodeId, evidenceRefId, record id, or source path")
    p_memory_evidence_show.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")
    p_memory_evidence_list = evidence_sub.add_parser("list", help="List recent derived local evidence refs")
    p_memory_evidence_list.add_argument("--project-root", type=path_type, default=argparse_module.SUPPRESS, help="Project root directory (default: current directory)")
    p_memory_evidence_list.add_argument("--limit", type=int, default=20, help="Maximum refs to list")
    p_memory_evidence_list.add_argument("--record-type", default="", help="Filter by record type, type, or evidence kind")
    p_memory_evidence_list.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")

    p_memory_eval = memory_sub.add_parser("eval", help="Run a small executable MemoryEval fixture")
    p_memory_eval.add_argument("--project-root", type=path_type, default=argparse_module.SUPPRESS, help="Project root directory (default: current directory)")
    p_memory_eval.add_argument("--keep-fixture", action="store_true", help="Keep the isolated eval fixture under .controlcoding/tmp for inspection")
    p_memory_eval.add_argument("--json", action="store_true", dest="json_output", help="Output machine-readable JSON")
    return p_memory_evidence


def dispatch_memory_aux_command(args, project, evidence_parser):
    memory_command = getattr(args, "memory_command", "")
    if memory_command == "evidence":
        evidence_command = getattr(args, "memory_evidence_command", "")
        if evidence_command == "show":
            return cmd_memory_evidence_show(project, selector=getattr(args, "selector", ""), json_output=getattr(args, "json_output", False))
        if evidence_command == "list":
            return cmd_memory_evidence_list(project, limit=getattr(args, "limit", 20), record_type=getattr(args, "record_type", ""), json_output=getattr(args, "json_output", False))
        evidence_parser.print_help()
        return 1
    if memory_command == "eval":
        return cmd_memory_eval_run(project, keep_fixture=getattr(args, "keep_fixture", False), json_output=getattr(args, "json_output", False))
    return None

__all__ = [
    "add_memory_aux_parser",
    "add_memory_scan_parser",
    "CONTROLCODING_VERSION",
    "CORRELATION_CONFIDENCE_LEVELS",
    "CONTROL_DIRNAME",
    "CROSS_PLANE_PACKET_TYPE",
    "DB_FILENAME",
    "DOCUMENT_LAYOUT_EDGE_TYPES",
    "DOCUMENT_LAYOUT_NODE_TYPES",
    "EDGE_TYPE_TO_GRAPH_EDGE",
    "ENTITY_TYPE_CODES",
    "ENTITY_TYPE_TO_GRAPH_NODE",
    "EVENT_TYPES",
    "EVIDENCE_REF_VERSION",
    "GRAPH_CONTRACT_VERSION",
    "HOST_CONTEXT_FILES",
    "INSTALL_MODE_STORAGE",
    "LOCAL_SPARSE_ADAPTER",
    "LOGS_DIRNAME",
    "MANIFEST_FILENAME",
    "MEMORY_DIRNAME",
    "MEMORY_EVAL_VERSION",
    "MEMORY_AUX_ROUTED_COMMANDS",
    "OCR_ADAPTER_INTERFACE_VERSION",
    "PRIVACY_SCRUB_VERSION",
    "REQUIRED_LOGS",
    "REQUIRED_VIEWS",
    "SCHEMA_VERSION",
    "SEMANTIC_ADAPTER_INTERFACE_VERSION",
    "SESSION_EDGE_TYPES",
    "SESSION_RECORD_SCHEMA_VERSION",
    "SKIP_DIRS",
    "STARTUP_INTENTS",
    "SUGGESTION_TYPE_TO_GRAPH_EDGE",
    "TEXT_EXTENSIONS",
    "VALID_DOCUMENT_FACETS",
    "VALID_DOCUMENT_TYPES",
    "VALID_INSTALL_MODES",
    "VALID_LIFECYCLES",
    "VALID_MEMORY_PROFILES",
    "VALID_SESSION_MODES",
    "VALID_SESSION_STATUSES",
    "VIEWS_DIRNAME",
    "WORK_MEMORY_PROFILE_DIRS",
    "cmd_memory_agent_run_record",
    "cmd_memory_bootstrap",
    "cmd_memory_consult_record",
    "cmd_memory_context",
    "cmd_memory_cross_pack",
    "cmd_memory_chunks",
    "cmd_memory_decision_add",
    "cmd_memory_dev_context_pack",
    "cmd_memory_doctor",
    "cmd_memory_evidence_list",
    "cmd_memory_evidence_show",
    "cmd_memory_eval_run",
    "cmd_memory_graph_accept",
    "cmd_memory_graph_around",
    "cmd_memory_graph_export",
    "cmd_memory_graph_reject",
    "cmd_memory_graph_status",
    "cmd_memory_graph_suggestions",
    "cmd_memory_idea_add",
    "cmd_memory_impact",
    "cmd_memory_init",
    "cmd_memory_intake_add",
    "cmd_memory_intake_promote",
    "cmd_memory_lifecycle_conflict",
    "cmd_memory_lifecycle_mark",
    "cmd_memory_lifecycle_supersede",
    "cmd_memory_layout",
    "cmd_memory_note_add",
    "cmd_memory_ocr_run",
    "cmd_memory_ocr_status",
    "cmd_memory_op_index",
    "cmd_memory_rag_pack",
    "cmd_memory_retrieve",
    "cmd_memory_scan",
    "cmd_memory_semantic_status",
    "cmd_memory_session_close",
    "cmd_memory_session_link",
    "cmd_memory_session_list",
    "cmd_memory_session_note",
    "cmd_memory_session_pack",
    "cmd_memory_session_show",
    "cmd_memory_session_start",
    "cmd_memory_session_views",
    "cmd_memory_startup",
    "cmd_memory_status",
    "cmd_memory_sync_report",
    "cmd_memory_temp_cleanup",
    "cmd_memory_vector_rebuild",
    "cmd_memory_vector_search",
    "cmd_memory_views_generate",
    "cmd_memory_work_attach",
    "cmd_memory_work_category_add",
    "cmd_memory_work_category_approve",
    "cmd_memory_work_category_list",
    "cmd_memory_work_category_propose",
    "cmd_memory_work_checkpoint",
    "cmd_memory_work_dashboard",
    "cmd_memory_work_export",
    "cmd_memory_work_handoff",
    "cmd_memory_work_context_pack",
    "cmd_memory_work_import",
    "cmd_memory_work_init",
    "cmd_memory_work_mcp_call",
    "cmd_memory_work_mcp_tools",
    "cmd_memory_work_obsidian",
    "cmd_memory_work_parity",
    "cmd_memory_work_query",
    "cmd_memory_work_quickstart",
    "cmd_memory_work_sync",
    "cmd_memory_work_status",
    "cmd_memory_work_views",
    "cmd_memory_work_wiki",
    "dispatch_memory_aux_command",
    "make_memory_entity_id",
    "session_status_payload",
]
