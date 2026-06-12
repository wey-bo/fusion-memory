from __future__ import annotations

from collections import defaultdict

from fusion_memory.core.models import Candidate


def reciprocal_rank_fusion(candidate_lists: list[list[Candidate]], k: int = 60) -> list[Candidate]:
    by_id: dict[tuple[str, str], Candidate] = {}
    scores: dict[tuple[str, str], float] = defaultdict(float)
    sources: dict[tuple[str, str], list[str]] = defaultdict(list)
    merged_scores: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    merged_metadata: dict[tuple[str, str], dict] = defaultdict(dict)
    for candidates in candidate_lists:
        for rank, candidate in enumerate(candidates, start=1):
            key = (candidate.type, candidate.id)
            by_id[key] = candidate
            scores[key] += 1.0 / (k + rank)
            sources[key].append(candidate.source)
            merged_scores[key] = _merge_scores(merged_scores[key], candidate.scores)
            merged_metadata[key] = {**merged_metadata[key], **candidate.metadata}
    fused: list[Candidate] = []
    for key, candidate in by_id.items():
        merged = Candidate(
            id=candidate.id,
            type=candidate.type,
            text=candidate.text,
            source="+".join(sorted(set(sources[key]))),
            scores={**merged_scores[key], "rrf_score": scores[key]},
            source_span_ids=candidate.source_span_ids,
            metadata=merged_metadata[key],
        )
        fused.append(merged)
    fused.sort(key=lambda c: c.scores.get("rrf_score", 0.0), reverse=True)
    return fused


def _merge_scores(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    merged = dict(left)
    for key, value in right.items():
        if not isinstance(value, int | float):
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = float(value)
        elif key in {"score", "semantic_score", "bm25_score", "temporal_fit", "graph_proximity", "milestone_score", "exact_signal", "value_exact_signal"}:
            merged[key] = max(float(current), float(value))
        else:
            merged[key] = float(value)
    return merged
