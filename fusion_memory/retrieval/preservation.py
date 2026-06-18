from __future__ import annotations

from typing import Any

from fusion_memory.core.models import Candidate


_MUST_PRESERVE_REASON_KEY = "must_preserve_reason"
_EVIDENCE_ROLE_KEY = "evidence_role"


def mark_must_preserve(candidate: Candidate, reason: str, evidence_role: str = "answer") -> Candidate:
    metadata = dict(candidate.metadata)
    reasons = list(metadata.get(_MUST_PRESERVE_REASON_KEY, []))
    if reason not in reasons:
        reasons.append(reason)
    metadata[_MUST_PRESERVE_REASON_KEY] = reasons
    metadata[_EVIDENCE_ROLE_KEY] = evidence_role
    return _clone_candidate(candidate, metadata=metadata)


def must_preserve_reasons(candidate: Candidate) -> list[str]:
    reasons = candidate.metadata.get(_MUST_PRESERVE_REASON_KEY, [])
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons]


def preserve_required_candidates(
    candidates: list[Candidate],
    selected: list[Candidate],
    limit: int,
) -> tuple[list[Candidate], list[dict[str, object]]]:
    preserved = list(selected[:limit])
    dropped: list[dict[str, object]] = []
    selected_ids = {candidate.id for candidate in preserved}
    required = [candidate for candidate in candidates if must_preserve_reasons(candidate) and candidate.id not in selected_ids]

    for candidate in required:
        if len(preserved) < limit:
            preserved.append(candidate)
            selected_ids.add(candidate.id)
            continue
        dropped.append(
            {
                "candidate_id": candidate.id,
                "reason": "budget_limit",
                "must_preserve_reasons": must_preserve_reasons(candidate),
                "evidence_role": candidate.metadata.get(_EVIDENCE_ROLE_KEY, "answer"),
                "source": candidate.source,
            }
        )
    return preserved, dropped


def _clone_candidate(candidate: Candidate, metadata: dict[str, Any] | None = None) -> Candidate:
    return Candidate(
        id=candidate.id,
        type=candidate.type,
        text=candidate.text,
        source=candidate.source,
        scores=dict(candidate.scores),
        source_span_ids=list(candidate.source_span_ids),
        metadata=dict(candidate.metadata) if metadata is None else metadata,
    )
