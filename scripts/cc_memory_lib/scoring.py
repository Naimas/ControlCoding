"""Observable retrieval scoring defaults for the Project Memory Engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCORING_CONFIG_VERSION = "cc-memory-retrieval-scoring/v1"


@dataclass(frozen=True)
class CapConfig:
    min_cap: int
    max_cap: int
    limit_multiplier: int

    def payload(self) -> dict[str, int]:
        return {
            "minCap": self.min_cap,
            "maxCap": self.max_cap,
            "limitMultiplier": self.limit_multiplier,
        }


@dataclass(frozen=True)
class GraphEdgeRowConfig:
    min_rows: int
    max_rows: int
    node_multiplier: int

    def payload(self) -> dict[str, int]:
        return {
            "minRows": self.min_rows,
            "maxRows": self.max_rows,
            "nodeMultiplier": self.node_multiplier,
        }


@dataclass(frozen=True)
class SemanticRuntimeConfig:
    default_timeout_seconds: int
    max_timeout_seconds: int
    default_max_candidates: int
    max_candidates_limit: int
    score_multiplier: float

    def payload(self) -> dict[str, int | float]:
        return {
            "defaultTimeoutSeconds": self.default_timeout_seconds,
            "maxTimeoutSeconds": self.max_timeout_seconds,
            "defaultMaxCandidates": self.default_max_candidates,
            "maxCandidatesLimit": self.max_candidates_limit,
            "scoreMultiplier": self.score_multiplier,
        }


@dataclass(frozen=True)
class RetrievalScoringConfig:
    schema_version: str
    text_points: dict[str, float]
    confidence_points: dict[str, float]
    confidence_fallback_points: float
    candidate_filter: CapConfig
    graph_adjacency_edge_rows: GraphEdgeRowConfig
    sparse_vector_retrieval_multiplier: float
    vector_prefilter: CapConfig
    semantic_runtime: SemanticRuntimeConfig
    graph_suggestion_edge_points: dict[str, float]
    graph_default_edge_points: dict[str, float]
    graph_suggestion_fallback_points: float
    graph_default_fallback_points: float
    lifecycle_points: dict[str, float]
    recency_day_points: tuple[tuple[int, float], ...]
    source_trust_type_points: dict[str, float]
    session_evidence_link_points: float
    source_refs_points: float
    generated_view_points: float
    project_plane_scope_points: float
    project_plane_boundary_points: float
    application_scope_points: float
    application_boundary_points: float

    def payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "text": dict(self.text_points),
            "confidence": {
                "points": dict(self.confidence_points),
                "fallbackPoints": self.confidence_fallback_points,
            },
            "candidateFilter": self.candidate_filter.payload(),
            "graph": {
                "adjacencyEdgeRows": self.graph_adjacency_edge_rows.payload(),
                "suggestionEdgePoints": dict(self.graph_suggestion_edge_points),
                "defaultEdgePoints": dict(self.graph_default_edge_points),
                "suggestionFallbackPoints": self.graph_suggestion_fallback_points,
                "defaultFallbackPoints": self.graph_default_fallback_points,
            },
            "sparseVector": {
                "retrievalMultiplier": self.sparse_vector_retrieval_multiplier,
                "prefilter": self.vector_prefilter.payload(),
            },
            "semantic": {
                "localRuntime": self.semantic_runtime.payload(),
            },
            "lifecycle": dict(self.lifecycle_points),
            "recency": {
                "dayThresholdPoints": [
                    {"maxDays": max_days, "points": points}
                    for max_days, points in self.recency_day_points
                ],
            },
            "sourceTrust": {
                "typePoints": dict(self.source_trust_type_points),
                "sessionEvidenceLinkPoints": self.session_evidence_link_points,
                "sourceRefsPoints": self.source_refs_points,
                "generatedViewPoints": self.generated_view_points,
                "projectPlaneScopePoints": self.project_plane_scope_points,
                "projectPlaneBoundaryPoints": self.project_plane_boundary_points,
                "applicationScopePoints": self.application_scope_points,
                "applicationBoundaryPoints": self.application_boundary_points,
            },
        }


DEFAULT_RETRIEVAL_SCORING = RetrievalScoringConfig(
    schema_version=SCORING_CONFIG_VERSION,
    text_points={
        "exactPathOrId": 42.0,
        "exactTitle": 42.0,
        "queryInTitle": 24.0,
        "queryInPath": 20.0,
        "queryInHeading": 18.0,
        "queryInText": 12.0,
        "tokenInTitle": 8.0,
        "tokenInHeading": 7.0,
        "tokenInPath": 5.0,
        "tokenInText": 3.0,
    },
    confidence_points={
        "explicit_link": 12.0,
        "exact_id_or_path_match": 10.0,
        "human_confirmed_relation": 12.0,
        "strong_title_or_heading_match": 6.0,
        "weak_lexical_similarity": 2.0,
        "canonical": 8.0,
    },
    confidence_fallback_points=4.0,
    candidate_filter=CapConfig(min_cap=50, max_cap=250, limit_multiplier=25),
    graph_adjacency_edge_rows=GraphEdgeRowConfig(min_rows=50, max_rows=5000, node_multiplier=16),
    sparse_vector_retrieval_multiplier=35.0,
    vector_prefilter=CapConfig(min_cap=50, max_cap=250, limit_multiplier=25),
    semantic_runtime=SemanticRuntimeConfig(
        default_timeout_seconds=30,
        max_timeout_seconds=300,
        default_max_candidates=200,
        max_candidates_limit=1000,
        score_multiplier=30.0,
    ),
    graph_suggestion_edge_points={
        "references": 14.0,
        "related_heading": 10.0,
        "related_terms": 6.0,
    },
    graph_default_edge_points={
        "references": 16.0,
        "mentions": 12.0,
        "conflicts_with": 10.0,
        "supersedes": 8.0,
        "superseded_by": 8.0,
        "contains": 7.0,
        "continues_to": 5.0,
        "continues_from": 5.0,
        "same_section_as": 5.0,
        "next_chunk": 3.0,
        "previous_chunk": 3.0,
    },
    graph_suggestion_fallback_points=5.0,
    graph_default_fallback_points=4.0,
    lifecycle_points={
        "active": 10.0,
        "implemented": 9.0,
        "verified": 9.0,
        "triaged": 7.0,
        "captured": 5.0,
        "draft": 4.0,
        "merged": 4.0,
        "needs_review": -8.0,
        "stale": -12.0,
        "conflicting": -16.0,
        "legacy": -10.0,
        "superseded": -14.0,
        "archived": -18.0,
    },
    recency_day_points=((7, 8.0), (30, 5.0), (90, 2.0)),
    source_trust_type_points={
        "decision": 10.0,
        "plan": 6.0,
        "research_note": 6.0,
        "handoff": 6.0,
        "doc_node": 6.0,
        "chunk": 5.0,
        "session": -3.0,
        "test_node": 4.0,
        "benchmark": 4.0,
        "file_node": 3.0,
    },
    session_evidence_link_points=3.0,
    source_refs_points=3.0,
    generated_view_points=-10.0,
    project_plane_scope_points=4.0,
    project_plane_boundary_points=-8.0,
    application_scope_points=5.0,
    application_boundary_points=-15.0,
)


def _validate_cap_config(name: str, config: CapConfig) -> None:
    if config.min_cap <= 0 or config.max_cap < config.min_cap or config.limit_multiplier <= 0:
        raise ValueError(f"invalid {name} cap configuration")


def _validate_scoring_config(config: RetrievalScoringConfig) -> None:
    _validate_cap_config("candidate filter", config.candidate_filter)
    _validate_cap_config("vector prefilter", config.vector_prefilter)
    graph_rows = config.graph_adjacency_edge_rows
    if graph_rows.min_rows <= 0 or graph_rows.max_rows < graph_rows.min_rows or graph_rows.node_multiplier <= 0:
        raise ValueError("invalid graph adjacency edge-row configuration")
    semantic = config.semantic_runtime
    if (
        semantic.default_timeout_seconds <= 0
        or semantic.max_timeout_seconds < semantic.default_timeout_seconds
        or semantic.default_max_candidates <= 0
        or semantic.max_candidates_limit < semantic.default_max_candidates
        or semantic.score_multiplier <= 0
    ):
        raise ValueError("invalid semantic runtime scoring configuration")
    for name, points in {
        **config.text_points,
        **config.confidence_points,
        **config.graph_suggestion_edge_points,
        **config.graph_default_edge_points,
        **config.lifecycle_points,
        **config.source_trust_type_points,
    }.items():
        if not isinstance(name, str) or not isinstance(points, (int, float)):
            raise ValueError("invalid scoring point map")
    for max_days, points in config.recency_day_points:
        if max_days <= 0 or not isinstance(points, (int, float)):
            raise ValueError("invalid recency scoring configuration")


def retrieval_scoring_config_payload() -> dict[str, Any]:
    return DEFAULT_RETRIEVAL_SCORING.payload()


_validate_scoring_config(DEFAULT_RETRIEVAL_SCORING)
