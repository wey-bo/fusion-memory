from __future__ import annotations

from fusion_memory.core.models import Candidate, QueryPlan


def score_candidate(candidate: Candidate, plan: QueryPlan) -> Candidate:
    scores = dict(candidate.scores)
    exact = max(scores.get("exact_signal", 0.0), scores.get("value_exact_signal", 0.0))
    semantic = max(scores.get("semantic_score", 0.0), exact * 0.85)
    bm25 = max(scores.get("bm25_score", 0.0), exact)
    entity = scores.get("entity_overlap", 0.0)
    temporal = scores.get("temporal_fit", 0.0)
    graph = scores.get("graph_proximity", 0.0)
    view = scores.get("view_or_profile_prior", 0.0)
    rrf = scores.get("rrf_score", 0.0)
    source_prior = _source_prior(candidate, plan)
    if candidate.type == "view":
        view = max(view, 0.8)
    if candidate.type == "profile":
        view = max(view, 0.45)
    if candidate.type == "event":
        graph = max(graph, 0.35)
    if candidate.type == "span":
        scores["source_quality"] = 0.8
    if exact > 0:
        scores["exact_signal"] = exact
        if candidate.scores.get("value_exact_signal", 0.0) > 0:
            scores["value_exact_signal"] = candidate.scores.get("value_exact_signal", 0.0)
        scores["semantic_score"] = semantic
        scores["bm25_score"] = bm25
    if plan.query_type == "event_ordering" and candidate.type == "span":
        temporal = max(temporal, float(scores.get("milestone_score", 0.0)))
    weights = {
        "semantic": 0.28,
        "bm25": 0.18,
        "entity": 0.06,
        "temporal": 0.12,
        "graph": 0.08,
        "view": 0.05,
        "rrf": 0.08,
        "source": 0.15,
    }
    if plan.query_type in {"temporal_lookup", "event_ordering"}:
        weights.update({"semantic": 0.22, "bm25": 0.14, "entity": 0.03, "temporal": 0.18, "graph": 0.14, "view": 0.01, "rrf": 0.06, "source": 0.22})
    elif plan.query_type in {"preference", "instruction"}:
        weights.update({"view": 0.18, "temporal": 0.05, "source": 0.20})
    elif plan.query_type in {"contradiction_resolution", "knowledge_update"}:
        weights.update({"temporal": 0.16, "graph": 0.14, "source": 0.20})
    elif plan.query_type == "abstention":
        weights.update({"bm25": 0.26, "semantic": 0.18, "source": 0.10})
    utility = (
        weights["semantic"] * semantic
        + weights["bm25"] * bm25
        + weights["entity"] * entity
        + weights["temporal"] * temporal
        + weights["graph"] * graph
        + weights["view"] * view
        + weights["rrf"] * rrf
        + weights["source"] * source_prior
    )
    scores["source_prior"] = source_prior
    scores["utility_score"] = utility
    return Candidate(
        id=candidate.id,
        type=candidate.type,
        text=candidate.text,
        source=candidate.source,
        scores=scores,
        source_span_ids=candidate.source_span_ids,
        metadata=candidate.metadata,
    )


def _source_prior(candidate: Candidate, plan: QueryPlan) -> float:
    query_type = plan.query_type
    if query_type == "event_ordering":
        if candidate.type == "span":
            milestone = float(candidate.scores.get("milestone_score", 0.0))
            speaker = float(candidate.scores.get("speaker_prior", 0.5))
            return min(1.0, 0.45 + (0.35 * milestone) + (0.20 * speaker))
        return {
            "event": 1.0,
            "fact": 0.10,
            "view": 0.0,
            "profile": 0.0,
        }.get(candidate.type, 0.0)
    if query_type == "temporal_lookup":
        return {
            "event": 0.95,
            "span": 0.60,
            "fact": 0.50,
            "view": 0.0,
            "profile": 0.0,
        }.get(candidate.type, 0.0)
    if query_type in {"preference", "instruction"}:
        return {
            "view": 1.0,
            "fact": 0.75,
            "profile": 0.55,
            "span": 0.40,
            "event": 0.20,
        }.get(candidate.type, 0.0)
    if query_type in {"contradiction_resolution", "knowledge_update"}:
        return {
            "fact": 0.95,
            "event": 0.60,
            "span": 0.55,
            "view": 0.25,
            "profile": 0.0,
        }.get(candidate.type, 0.0)
    return {
        "fact": 0.80,
        "span": 0.85 if "exact_filter" in candidate.source and candidate.scores.get("exact_signal", 0.0) >= 0.75 else 0.65,
        "event": 0.40,
        "view": 0.10,
        "profile": 0.05,
    }.get(candidate.type, 0.0)
