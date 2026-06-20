from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fusion_memory.core.models import Candidate


@dataclass(frozen=True)
class QueryUnderstandingRecord:
    language: str
    intent: str
    features: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "intent": self.intent,
            "features": list(self.features),
        }


@dataclass(frozen=True)
class CandidateRecallRecord:
    source_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {"source_counts": dict(self.source_counts)}


@dataclass(frozen=True)
class CandidateFusionRecord:
    selected_sources: tuple[str, ...]
    dropped_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_sources": list(self.selected_sources),
            "dropped_count": int(self.dropped_count),
        }


@dataclass(frozen=True)
class EvidencePackBuilderRecord:
    source_span_count: int
    coverage_insufficient: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_span_count": int(self.source_span_count),
            "coverage_insufficient": bool(self.coverage_insufficient),
        }


@dataclass(frozen=True)
class RetrievalPipelineRecord:
    query_type: str
    mode: str
    query_understanding: QueryUnderstandingRecord
    candidate_recall: CandidateRecallRecord
    candidate_fusion: CandidateFusionRecord
    evidence_output: EvidencePackBuilderRecord

    def pipeline_layers(self) -> dict[str, Any]:
        return {
            "QueryUnderstanding": self.query_understanding.to_dict(),
            "CandidateRecall": self.candidate_recall.to_dict(),
            "CandidateFusion": self.candidate_fusion.to_dict(),
            "EvidencePackBuilder": self.evidence_output.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_type": self.query_type,
            "mode": self.mode,
            "pipeline_layers": self.pipeline_layers(),
            "query_understanding": self.query_understanding.to_dict(),
            "candidate_recall": self.candidate_recall.to_dict(),
            "candidate_fusion": self.candidate_fusion.to_dict(),
            "evidence_output": self.evidence_output.to_dict(),
        }


def build_pipeline_record(
    query_type: str,
    mode: str,
    *,
    language: str,
    intent: str,
    features: list[str],
    recalled: list[Candidate],
    selected: list[Candidate],
    dropped_count: int,
    source_span_count: int,
    coverage_insufficient: bool,
) -> RetrievalPipelineRecord:
    source_counts: dict[str, int] = {}
    for candidate in recalled:
        source_counts[candidate.source] = source_counts.get(candidate.source, 0) + 1
    selected_sources = tuple(dict.fromkeys(candidate.source for candidate in selected))
    return RetrievalPipelineRecord(
        query_type=query_type,
        mode=mode,
        query_understanding=QueryUnderstandingRecord(
            language=language,
            intent=intent,
            features=tuple(features),
        ),
        candidate_recall=CandidateRecallRecord(source_counts=source_counts),
        candidate_fusion=CandidateFusionRecord(
            selected_sources=selected_sources,
            dropped_count=dropped_count,
        ),
        evidence_output=EvidencePackBuilderRecord(
            source_span_count=source_span_count,
            coverage_insufficient=coverage_insufficient,
        ),
    )
