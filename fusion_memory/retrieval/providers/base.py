from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from fusion_memory.core.models import Candidate, Scope


@dataclass
class RecallContext:
    service: Any
    query: str
    scope: Scope
    plan: Any
    per_source_limit: int
    enabled_sources: set[str] | None
    include_session: bool
    event_milestone_group: Callable[[Any], str | None]
    prior_candidates: list[Candidate] = field(default_factory=list)


class RecallProvider(Protocol):
    provider_id: str
    source_family: str
    production_default: bool
    shadow_only: bool
    graph_related: bool
    supported_query_types: frozenset[str] | None
    output_sources: frozenset[str]
    replay_categories: frozenset[str]

    def recall(self, context: RecallContext) -> list[Candidate]:
        ...


def provider_applies_to_query_type(provider: RecallProvider, query_type: str) -> bool:
    supported = provider.supported_query_types
    return supported is None or query_type in supported
