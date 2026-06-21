from __future__ import annotations

from dataclasses import dataclass, field

from fusion_memory.core.models import Candidate


_ALLOWED_STAGES = {"recalled", "scored", "filtered", "rescued", "selected", "packed"}


@dataclass(frozen=True)
class CandidateLifecycleRecord:
    candidate_id: str
    candidate_type: str
    candidate_source: str
    stage: str
    reason_code: str
    source_span_ids: tuple[str, ...] = ()
    scores: dict[str, float] = field(default_factory=dict)
    contributed: bool | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "candidate_id": self.candidate_id,
            "candidate_type": self.candidate_type,
            "candidate_source": self.candidate_source,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "source_span_ids": list(self.source_span_ids),
        }
        if self.scores:
            out["scores"] = dict(self.scores)
        if self.contributed is not None:
            out["contributed"] = self.contributed
        return out


class CandidateLifecycleRecorder:
    def __init__(self) -> None:
        self._records: list[CandidateLifecycleRecord] = []

    def record(
        self,
        candidate: Candidate,
        stage: str,
        reason_code: str,
        contributed: bool | None = None,
        scores: dict[str, float] | None = None,
    ) -> None:
        if stage not in _ALLOWED_STAGES:
            raise ValueError(f"unsupported lifecycle stage: {stage}")
        numeric_scores = {
            str(key): float(value)
            for key, value in (scores or candidate.scores or {}).items()
            if isinstance(value, (int, float, bool))
        }
        self._records.append(
            CandidateLifecycleRecord(
                candidate_id=str(candidate.id),
                candidate_type=str(candidate.type),
                candidate_source=str(candidate.source),
                stage=stage,
                reason_code=str(reason_code),
                source_span_ids=tuple(str(span_id) for span_id in candidate.source_span_ids if span_id),
                scores=numeric_scores,
                contributed=contributed,
            )
        )

    def extend(self, candidates: list[Candidate], stage: str, reason_code: str) -> None:
        for candidate in candidates:
            self.record(candidate, stage, reason_code)

    def to_trace(self, limit: int = 200) -> list[dict[str, object]]:
        return [record.to_dict() for record in self._records[: max(0, int(limit))]]

    def summary(self) -> dict[str, object]:
        stage_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        contributed_count = 0
        for record in self._records:
            stage_counts[record.stage] = stage_counts.get(record.stage, 0) + 1
            source_counts[record.candidate_source] = source_counts.get(record.candidate_source, 0) + 1
            reason_counts[record.reason_code] = reason_counts.get(record.reason_code, 0) + 1
            if record.contributed:
                contributed_count += 1
        return {
            "record_count": len(self._records),
            "stage_counts": stage_counts,
            "source_counts": source_counts,
            "reason_counts": reason_counts,
            "contributed_count": contributed_count,
        }
