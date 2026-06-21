from __future__ import annotations

from typing import Any, Callable

from fusion_memory.core.models import Candidate, Scope
from fusion_memory.retrieval.pipeline import RecallOrchestrator
from fusion_memory.retrieval.pipeline import RetrievalExecutionContext
from fusion_memory.retrieval.pipeline import query_understanding_result_from_plan


def build_candidate_lists(
    service: Any,
    query: str,
    scope: Scope,
    plan: Any,
    per_source_limit: int,
    enabled_sources: list[str] | set[str] | None = None,
    include_session: bool = False,
    *,
    event_milestone_group: Callable[[Any], str | None],
) -> list[list[Candidate]]:
    query_understanding = query_understanding_result_from_plan(plan=plan, query=query)
    context = RetrievalExecutionContext(
        service=service,
        query=query,
        scope=scope,
        options={},
        query_understanding=query_understanding,
        include_session=include_session,
        per_source_limit=per_source_limit,
        enabled_sources=enabled_sources,
        mode="fast",
        limit=0,
        rerank_top_n=0,
        event_milestone_group=event_milestone_group,
    )
    return RecallOrchestrator().run(context).candidate_lists
