from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fusion_memory.retrieval.pipeline import (
    CandidateFusionRecord,
    CandidateRecallRecord,
    EvidencePackBuilderRecord,
    QueryUnderstandingRecord,
    RetrievalPipelineRecord,
)


PIPELINE_LAYER_ORDER = (
    ("QueryUnderstanding", "query_understanding"),
    ("CandidateRecall", "candidate_recall"),
    ("CandidateFusion", "candidate_fusion"),
    ("EvidencePackBuilder", "evidence_output"),
)


@dataclass
class RetrievalTraceBuilder:
    query_type: str
    mode: str
    _sections: dict[str, Any] = field(default_factory=dict)

    def query_understanding(self, *, language: str, intent: str, features: list[str]) -> None:
        self._sections["query_understanding"] = {
            "language": language,
            "intent": intent,
            "features": list(features),
        }

    def candidate_recall(self, *, source_counts: dict[str, int]) -> None:
        self._sections["candidate_recall"] = {
            "source_counts": dict(source_counts),
        }

    def candidate_fusion(self, *, selected_sources: list[str], dropped_count: int) -> None:
        self._sections["candidate_fusion"] = {
            "selected_sources": list(selected_sources),
            "dropped_count": int(dropped_count),
        }

    def evidence_output(self, *, source_span_count: int, coverage_insufficient: bool) -> None:
        self._sections["evidence_output"] = {
            "source_span_count": int(source_span_count),
            "coverage_insufficient": bool(coverage_insufficient),
        }

    def pipeline_layers(self) -> dict[str, object]:
        record = self.pipeline_record()
        if record is not None:
            return record.pipeline_layers()
        return {layer_name: self._sections.get(section_name, {}) for layer_name, section_name in PIPELINE_LAYER_ORDER}

    def pipeline_record(self) -> RetrievalPipelineRecord | None:
        query_understanding = self._sections.get("query_understanding")
        candidate_recall = self._sections.get("candidate_recall")
        candidate_fusion = self._sections.get("candidate_fusion")
        evidence_output = self._sections.get("evidence_output")
        if not all([query_understanding, candidate_recall, candidate_fusion, evidence_output]):
            return None
        return RetrievalPipelineRecord(
            query_type=self.query_type,
            mode=self.mode,
            query_understanding=QueryUnderstandingRecord(
                language=str(query_understanding.get("language", "")),
                intent=str(query_understanding.get("intent", "")),
                features=tuple(str(feature) for feature in query_understanding.get("features", [])),
            ),
            candidate_recall=CandidateRecallRecord(
                source_counts={
                    str(source): int(count)
                    for source, count in candidate_recall.get("source_counts", {}).items()
                }
            ),
            candidate_fusion=CandidateFusionRecord(
                selected_sources=tuple(str(source) for source in candidate_fusion.get("selected_sources", [])),
                dropped_count=int(candidate_fusion.get("dropped_count", 0)),
            ),
            evidence_output=EvidencePackBuilderRecord(
                source_span_count=int(evidence_output.get("source_span_count", 0)),
                coverage_insufficient=bool(evidence_output.get("coverage_insufficient", False)),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        record = self.pipeline_record()
        if record is not None:
            return record.to_dict()
        return {"query_type": self.query_type, "mode": self.mode, "pipeline_layers": self.pipeline_layers(), **self._sections}
