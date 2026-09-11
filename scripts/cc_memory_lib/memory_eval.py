"""Small executable evaluation fixture for the Project Memory Engine."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from .commands import cmd_memory_init, cmd_memory_scan, cmd_memory_work_init
from .cross_plane import _build_cross_pack
from .evidence import _show_payload
from .op_index import _op_index_payload
from .rag_pack import _build_rag_pack
from .retrieve import _retrieve_payload
from .scoring import retrieval_scoring_config_payload
from .sessions import cmd_memory_session_link, cmd_memory_session_start
from .store import _now_iso, _print_json_or_text, _relative_path
from .vector import cmd_memory_vector_rebuild

MEMORY_EVAL_VERSION = "cc-memory-eval/v1"
MEMORY_EVAL_QUERY = "payment settlement ledger evidence"
MEMORY_EVAL_EXPECTED_DOCUMENT_ORDER = ["docs/decision.md", "docs/plan.md"]
# Synthetic fixture used to verify redaction; this is not a usable API credential.
_EVAL_SECRET = "sk-evalsecretvalue1234567890"


def _run_silent(command: Callable[..., int], *args: Any, **kwargs: Any) -> tuple[int, str]:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        result = command(*args, **kwargs)
    return result, stream.getvalue()


def _add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    passed: bool,
    detail: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    checks.append({
        "id": check_id,
        "passed": bool(passed),
        "detail": detail,
        "evidence": evidence or {},
    })


def _safe_slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-").lower() or "run"


def _make_fixture_root(project: Path) -> Path:
    stamp = _safe_slug(_now_iso())
    return project / ".controlcoding" / "tmp" / "memory-eval" / stamp


def _cleanup_empty_eval_dirs(project: Path) -> None:
    for path in [
        project / ".controlcoding" / "tmp" / "memory-eval",
        project / ".controlcoding" / "tmp",
    ]:
        try:
            path.rmdir()
        except OSError:
            pass


def _write_fixture_files(fixture: Path) -> None:
    docs = fixture / "docs"
    legacy = docs / "legacy"
    corpus = fixture / "corpus"
    docs.mkdir(parents=True, exist_ok=True)
    legacy.mkdir(parents=True, exist_ok=True)
    corpus.mkdir(parents=True, exist_ok=True)
    (docs / "plan.md").write_text(
        "# Payment Plan\n\n"
        "This plan references [Payment Decision](decision.md).\n\n"
        "## Reconciliation\n\n"
        "Payment settlement reconciliation requires ledger evidence and source citations.\n",
        encoding="utf-8",
    )
    (docs / "decision.md").write_text(
        "# Payment Decision\n\n"
        "## Reconciliation\n\n"
        "Use the local ledger for payment settlement evidence.\n"
        f"OPENAI_API_KEY={_EVAL_SECRET}\n",
        encoding="utf-8",
    )
    (legacy / "old-payment.md").write_text(
        "# Old Payment Plan\n\n"
        "Legacy settlement process kept for audit warning coverage.\n",
        encoding="utf-8",
    )
    (docs / "unrelated.md").write_text(
        "# UX Notes\n\n"
        "Toolbar spacing and menu density are tracked separately.\n",
        encoding="utf-8",
    )
    (corpus / "runtime-note.txt").write_text(
        "Application-owned memory component for payment RAG runtime data.\n",
        encoding="utf-8",
    )


def _write_project_plane_fixture(fixture: Path) -> None:
    plan = fixture / ".controlwork" / "memory" / "plans" / "payment-plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "# Payment Plan\n\n"
        "- **Area**: plans\n"
        "- **Lifecycle**: active\n\n"
        "## Body\n\n"
        "Payment settlement ledger evidence and graph requirements belong to the Project Plane.\n",
        encoding="utf-8",
    )


def _write_semantic_runtime_fixture(fixture: Path) -> None:
    runtime = fixture / ".controlcoding" / "memory" / "semantic_runtime.py"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(
        "import json, sys\n"
        "payload = json.loads(sys.stdin.read() or '{}')\n"
        "scores = []\n"
        "for candidate in payload.get('candidates', []):\n"
        "    path = candidate.get('path', '')\n"
        "    if path == 'docs/decision.md':\n"
        "        scores.append({\n"
        "            'id': candidate.get('id'),\n"
        "            'score': 0.96,\n"
        "            'reason': 'semantic ranking fixture: decision evidence',\n"
        "        })\n"
        "    elif path == 'docs/plan.md':\n"
        "        scores.append({\n"
        "            'id': candidate.get('id'),\n"
        "            'score': 0.18,\n"
        "            'reason': 'semantic ranking fixture: supporting plan',\n"
        "        })\n"
        "print(json.dumps({'scores': scores}))\n",
        encoding="utf-8",
    )
    config_path = fixture / ".controlcoding" / "memory" / "semantic_adapters.json"
    config_path.write_text(
        json.dumps({
            "activeAdapter": "local_runtime_v1",
            "localRuntime": {
                "enabled": True,
                "command": [sys.executable, str(runtime)],
                "timeoutSeconds": 10,
                "maxCandidates": 50,
            },
        }),
        encoding="utf-8",
    )


def _fixture_setup(fixture: Path, checks: list[dict[str, Any]]) -> bool:
    _write_fixture_files(fixture)
    setup_steps: list[tuple[str, Callable[..., int], tuple[Any, ...], dict[str, Any]]] = [
        ("memory_init", cmd_memory_init, (fixture,), {"mode": "document-only", "project_short": "Eval"}),
        ("memory_scan", cmd_memory_scan, (fixture,), {}),
        ("vector_rebuild", cmd_memory_vector_rebuild, (fixture,), {}),
        (
            "session_start",
            cmd_memory_session_start,
            (fixture,),
            {
                "topic": "Payment settlement ledger evidence",
                "mode": "continue_previous_work",
                "scope": "dev",
                "session_id": "session-memory-eval-payment",
                "categories": ["payment"],
                "summary": "Reviewed payment settlement ledger evidence and linked the decision document.",
            },
        ),
        (
            "session_link_doc",
            cmd_memory_session_link,
            (fixture, "session-memory-eval-payment"),
            {"link_type": "changes_doc", "target": "docs/decision.md", "target_type": "doc"},
        ),
        (
            "session_link_commit",
            cmd_memory_session_link,
            (fixture, "session-memory-eval-payment"),
            {"link_type": "produced_commit", "target": "abc1234", "target_type": "commit"},
        ),
        (
            "work_init",
            cmd_memory_work_init,
            (fixture,),
            {"project_name": "MemoryEval Work", "purpose": "Project plane evaluation fixture"},
        ),
    ]
    for step_id, command, args, kwargs in setup_steps:
        result, output = _run_silent(command, *args, **kwargs)
        if result != 0:
            _add_check(
                checks,
                "fixture_setup",
                False,
                f"{step_id} returned {result}",
                {"stdoutTail": output[-500:]},
            )
            return False
        if step_id == "memory_init":
            _write_semantic_runtime_fixture(fixture)
    _write_project_plane_fixture(fixture)
    _add_check(checks, "fixture_setup", True, "isolated memory fixture initialized")
    return True


def _check_retrieval(fixture: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    payload = _retrieve_payload(fixture, MEMORY_EVAL_QUERY, "general", 10, False)
    matches = payload.get("matches", [])
    match_paths = {str(match.get("path") or "") for match in matches}
    ranked_document_paths = []
    for match in matches:
        path = str(match.get("path") or "")
        record_type = str(match.get("recordType") or "")
        if path and record_type == "entity" and path not in ranked_document_paths:
            ranked_document_paths.append(path)
    semantic_adapter = payload.get("semanticAdapter", {})
    semantic_ranked_paths: list[str] = []
    semantic_points_by_path: dict[str, float] = {}
    for match in matches:
        path = str(match.get("path") or "")
        semantic_signal = match.get("signals", {}).get("semantic", {})
        points = float(semantic_signal.get("points") or 0.0) if isinstance(semantic_signal, dict) else 0.0
        if path and points > 0:
            semantic_points_by_path[path] = points
            if path not in semantic_ranked_paths:
                semantic_ranked_paths.append(path)
    demotions = payload.get("demotionReport", {}).get("counts", {})
    _add_check(
        checks,
        "retrieval_target",
        "docs/decision.md" in match_paths,
        "query retrieves the active decision document",
        {"matchPaths": sorted(path for path in match_paths if path)[:8]},
    )
    _add_check(
        checks,
        "sparse_vector_signal",
        bool(payload.get("vectorIndex", {}).get("used")),
        "local sparse vector index participates in ranking",
        {"adapter": payload.get("vectorIndex", {}).get("adapter", "")},
    )
    vector_index = payload.get("vectorIndex", {})
    _add_check(
        checks,
        "bounded_vector_prefilter",
        vector_index.get("usedFullVectorScan") is False
        and int(vector_index.get("scoredVectorRows", 0) or 0)
        <= int(vector_index.get("prefilterCap", 0) or 0),
        "sparse vector scoring is bounded by the postings prefilter",
        {
            "prefilterMode": vector_index.get("prefilterMode", ""),
            "prefilterCap": vector_index.get("prefilterCap", 0),
            "scoredVectorRows": vector_index.get("scoredVectorRows", 0),
            "usedFullVectorScan": vector_index.get("usedFullVectorScan", True),
        },
    )
    candidate_filter = payload.get("candidateFilter", {})
    _add_check(
        checks,
        "indexed_candidate_filter",
        candidate_filter.get("usedFullScan") is False
        and candidate_filter.get("mode") in {"fts5", "sql_inverted"}
        and int(candidate_filter.get("candidateCountBeforeScoring", 0) or 0)
        <= int(candidate_filter.get("candidateCap", 0) or 0) + int(candidate_filter.get("protectedCandidateCount", 0) or 0),
        "retrieval uses the indexed candidate filter before final scoring",
        {
            "mode": candidate_filter.get("mode", ""),
            "candidateCountBeforeScoring": candidate_filter.get("candidateCountBeforeScoring", 0),
            "candidateCap": candidate_filter.get("candidateCap", 0),
            "usedFullScan": candidate_filter.get("usedFullScan", True),
            "fallbackReason": candidate_filter.get("fallbackReason", ""),
        },
    )
    _add_check(
        checks,
        "ranking_expected_document_order",
        ranked_document_paths[: len(MEMORY_EVAL_EXPECTED_DOCUMENT_ORDER)] == MEMORY_EVAL_EXPECTED_DOCUMENT_ORDER,
        "ranking keeps active decision evidence ahead of the supporting plan fixture",
        {
            "expectedPrefix": MEMORY_EVAL_EXPECTED_DOCUMENT_ORDER,
            "actualDocumentPaths": ranked_document_paths[:6],
            "scoringConfigVersion": payload.get("scoring", {}).get("schemaVersion", ""),
        },
    )
    _add_check(
        checks,
        "semantic_ranking_expected_order",
        semantic_adapter.get("used") is True
        and semantic_adapter.get("adapter") == "local_runtime_v1"
        and semantic_points_by_path.get("docs/decision.md", 0.0) > 0
        and payload.get("scoring", {}).get("schemaVersion", "") == "cc-memory-retrieval-scoring/v1"
        and ranked_document_paths[: len(MEMORY_EVAL_EXPECTED_DOCUMENT_ORDER)] == MEMORY_EVAL_EXPECTED_DOCUMENT_ORDER,
        "local runtime semantic scoring participates while preserving the expected ranking prefix",
        {
            "adapter": semantic_adapter,
            "expectedPrefix": MEMORY_EVAL_EXPECTED_DOCUMENT_ORDER,
            "actualDocumentPaths": ranked_document_paths[:6],
            "semanticRankedPaths": semantic_ranked_paths[:6],
            "semanticPointsByPath": semantic_points_by_path,
            "scoringConfigVersion": payload.get("scoring", {}).get("schemaVersion", ""),
        },
    )
    _add_check(
        checks,
        "lifecycle_demotion",
        int(demotions.get("lifecycle_stale", 0) or 0) >= 1,
        "stale or legacy memory is demoted, not treated as current truth",
        {"counts": demotions},
    )
    _add_check(
        checks,
        "application_boundary_demotion",
        int(demotions.get("application-owned_memory_boundary", 0) or 0) >= 1,
        "application-owned runtime memory is visible but demoted outside application scope",
        {"counts": demotions},
    )
    _add_check(
        checks,
        "session_trace_demotion",
        int(demotions.get("session_trace_not_canonical", 0) or 0) >= 1,
        "Session GraphRAG is trace evidence, not canonical project truth",
        {"counts": demotions},
    )
    return payload


def _check_rag_and_evidence(fixture: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    payload = _build_rag_pack(fixture, MEMORY_EVAL_QUERY, "general", 10, False)
    payload_json = json.dumps(payload, ensure_ascii=False)
    citations = payload.get("citations", [])
    decision_node_id = next(
        (
            str(citation.get("nodeId") or "")
            for citation in citations
            if citation.get("path") == "docs/decision.md"
        ),
        "",
    )
    _add_check(
        checks,
        "rag_pack_evidence_refs",
        bool(payload.get("evidenceRefs")) and bool(decision_node_id),
        "GraphRAG packet carries drill-down evidence refs",
        {"evidenceRefCount": len(payload.get("evidenceRefs", [])), "decisionNodeIdPresent": bool(decision_node_id)},
    )
    _add_check(
        checks,
        "rag_pack_privacy_scrub",
        _EVAL_SECRET not in payload_json and bool(payload.get("privacyReceipt", {}).get("enabled")),
        "GraphRAG packet does not leak secret-shaped text and returns a scrub receipt",
        {
            "replacementCount": payload.get("privacyReceipt", {}).get("replacementCount", 0),
            "secretValuesIncluded": False,
        },
    )
    if decision_node_id:
        evidence_payload = _show_payload(fixture, decision_node_id)
    else:
        evidence_payload = {"ok": False, "privacyReceipt": {"replacementCount": 0}}
    evidence_json = json.dumps(evidence_payload, ensure_ascii=False)
    _add_check(
        checks,
        "evidence_drilldown",
        bool(evidence_payload.get("ok"))
        and bool(evidence_payload.get("readOnly"))
        and evidence_payload.get("source", {}).get("available") is True,
        "evidence ref resolves to a local read-only source excerpt",
        {"nodeId": decision_node_id, "readOnly": evidence_payload.get("readOnly", False)},
    )
    _add_check(
        checks,
        "evidence_privacy_scrub",
        _EVAL_SECRET not in evidence_json
        and int(evidence_payload.get("privacyReceipt", {}).get("replacementCount", 0) or 0) >= 1,
        "evidence drill-down redacts secret-shaped text",
        {
            "replacementCount": evidence_payload.get("privacyReceipt", {}).get("replacementCount", 0),
            "secretValuesIncluded": False,
        },
    )
    return payload


def _check_cross_plane(fixture: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    packet_root = fixture / ".controlcoding" / "context-packets"
    before = {path.name for path in packet_root.glob("*.md")} if packet_root.exists() else set()
    payload = _build_cross_pack(fixture, MEMORY_EVAL_QUERY, "general", 10, False, False)
    after = {path.name for path in packet_root.glob("*.md")} if packet_root.exists() else set()
    planes = {str(citation.get("plane") or "") for citation in payload.get("citations", [])}
    _add_check(
        checks,
        "cross_plane_boundaries",
        payload.get("mode") == "federated_read_only_no_sync_no_hidden_writes"
        and payload.get("sourceOfTruth") is False
        and {"Dev Plane", "Project Plane", "Session GraphRAG"}.issubset(planes),
        "cross-pack federates reviewed planes without becoming source of truth",
        {"planes": sorted(planes), "mode": payload.get("mode", "")},
    )
    _add_check(
        checks,
        "cross_plane_no_hidden_packet_write",
        before == after,
        "cross-pack does not write context packets unless an output path is explicit",
        {"beforePacketCount": len(before), "afterPacketCount": len(after)},
    )
    _add_check(
        checks,
        "cross_plane_evidence_refs",
        bool(payload.get("evidenceRefs")) and bool(payload.get("privacyReceipt", {}).get("enabled")),
        "cross-pack preserves Dev Plane evidence refs and privacy receipt",
        {"evidenceRefCount": len(payload.get("evidenceRefs", []))},
    )
    return payload


def _check_operations_index(fixture: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    payload = _op_index_payload(fixture, scope="general", topic=MEMORY_EVAL_QUERY)
    routes = payload.get("routes", [])
    has_evidence_route = any(
        route.get("plane") == "Dev Plane" and "memory evidence show" in str(route.get("command") or "")
        for route in routes
    )
    health = payload.get("indexHealth", {}).get("evidenceRefs", {})
    _add_check(
        checks,
        "rago_evidence_route",
        has_evidence_route and health.get("available") is True,
        "RAG-O advertises the evidence drill-down route as read-only coordination",
        {"storage": health.get("storage", ""), "routeCount": len(routes)},
    )
    return payload


def _memory_eval_payload(project: Path, keep_fixture: bool = False) -> dict[str, Any]:
    project = project.resolve()
    fixture = _make_fixture_root(project)
    checks: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "ok": False,
        "schemaVersion": MEMORY_EVAL_VERSION,
        "query": MEMORY_EVAL_QUERY,
        "generatedAt": _now_iso(),
        "projectRoot": str(project),
        "fixturePath": "",
        "fixtureKept": bool(keep_fixture),
        "checks": checks,
        "summary": {"passed": 0, "failed": 0},
        "secretValuesIncluded": False,
    }
    try:
        fixture.mkdir(parents=True, exist_ok=False)
        if _fixture_setup(fixture, checks):
            retrieval = _check_retrieval(fixture, checks)
            rag_pack = _check_rag_and_evidence(fixture, checks)
            cross_pack = _check_cross_plane(fixture, checks)
            op_index = _check_operations_index(fixture, checks)
            payload["observations"] = {
                "retrievalMatchCount": len(retrieval.get("matches", [])),
                "retrievalTopPaths": [
                    str(match.get("path") or match.get("id") or "")
                    for match in retrieval.get("matches", [])[:6]
                ],
                "semanticAdapter": retrieval.get("semanticAdapter", {}),
                "semanticRankingPaths": [
                    str(match.get("path") or "")
                    for match in retrieval.get("matches", [])
                    if isinstance(match.get("signals", {}).get("semantic"), dict)
                    and float(match["signals"]["semantic"].get("points") or 0.0) > 0
                ][:6],
                "scoringConfig": retrieval.get("scoring", retrieval_scoring_config_payload()),
                "ragEvidenceRefCount": len(rag_pack.get("evidenceRefs", [])),
                "crossPlaneCitationCount": len(cross_pack.get("citations", [])),
                "operationRouteCount": len(op_index.get("routes", [])),
            }
    except Exception as exc:
        _add_check(checks, "memory_eval_exception", False, str(exc.__class__.__name__), {"message": str(exc)[:500]})
    finally:
        if keep_fixture:
            payload["fixturePath"] = _relative_path(project, fixture)
        else:
            shutil.rmtree(fixture, ignore_errors=True)
            _cleanup_empty_eval_dirs(project)

    passed = sum(1 for check in checks if check.get("passed"))
    failed = len(checks) - passed
    payload["summary"] = {"passed": passed, "failed": failed, "total": len(checks)}
    payload["ok"] = failed == 0 and bool(checks)
    return payload


def _memory_eval_text(payload: dict[str, Any]) -> str:
    state = "passed" if payload.get("ok") else "failed"
    summary = payload.get("summary", {})
    lines = [
        f"MemoryEval {state}",
        f"Schema: {payload.get('schemaVersion', '')}",
        f"Query: {payload.get('query', '')}",
        (
            f"Checks: {summary.get('passed', 0)} passed, "
            f"{summary.get('failed', 0)} failed, {summary.get('total', 0)} total"
        ),
        f"Fixture: {payload.get('fixturePath') or 'removed after run'}",
        "",
        "Checks",
    ]
    for check in payload.get("checks", []):
        label = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- {label} {check.get('id', '')}: {check.get('detail', '')}")
    return "\n".join(lines)


def cmd_memory_eval_run(project: Path, keep_fixture: bool = False, json_output: bool = False) -> int:
    """Run a small isolated memory quality fixture and report structured checks."""
    payload = _memory_eval_payload(project, keep_fixture=keep_fixture)
    _print_json_or_text(json_output, payload, _memory_eval_text(payload))
    return 0 if payload.get("ok") else 1


__all__ = [
    "MEMORY_EVAL_VERSION",
    "cmd_memory_eval_run",
]
