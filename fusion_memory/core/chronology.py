from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fusion_memory.core.models import Scope


@dataclass
class ChronologyTopic:
    topic_id: str
    scope: Scope
    canonical_label: str
    aliases: list[str]
    language: str
    taxonomy_tags: list[str]
    source_span_ids: list[str]
    confidence: float
    created_at: datetime


@dataclass
class ChronologyPhase:
    phase_id: str
    topic_id: str
    phase_type: str
    order_hint: int | None
    source_span_ids: list[str]
    confidence: float
    created_at: datetime


@dataclass
class ChronologyEventNode:
    node_id: str
    scope: Scope
    actor: str
    action: str
    object: str
    topic_id: str | None
    phase_id: str | None
    timestamp: datetime | None
    source_span_id: str | None
    source_turn_id: str | None
    text: str
    language: str
    confidence: float
    explicit_order_marker: str | None
    created_at: datetime


@dataclass
class ChronologyEventEdge:
    edge_id: str
    from_node_id: str
    to_node_id: str
    edge_type: str
    evidence_type: str
    source_span_ids: list[str]
    confidence: float
    created_at: datetime
