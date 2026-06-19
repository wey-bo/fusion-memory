from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha1
from typing import Iterator


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    module: str
    purpose: str
    category: str
    pattern: str | None = None
    owner: str = "retrieval"
    ability: str = "general"


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    query: str
    text_hash: str
    contributed_candidate_id: str | None
    stage: str
    contributed: bool | None = None
    impact: str = "observed"
    metadata: dict[str, object] = field(default_factory=dict)


_RULE_REGISTRY: dict[str, RuleDefinition] = {}
_ACTIVE_RULE_HITS: ContextVar[list[RuleHit] | None] = ContextVar("fusion_memory_rule_hits", default=None)
_SENSITIVE_METADATA_KEY_PARTS = (
    "raw_text",
    "text",
    "content",
    "span",
    "message",
    "query",
    "prompt",
)
_SENSITIVE_METADATA_KEYS = {
    "phrases",
    "conditions",
    "taxonomy_hits",
}


def register_rule(rule: RuleDefinition) -> RuleDefinition:
    _RULE_REGISTRY[rule.rule_id] = rule
    return rule


def record_rule_hit(
    rule_id: str,
    query: str,
    text: str,
    stage: str,
    contributed_candidate_id: str | None = None,
    contributed: bool | None = None,
    impact: str = "observed",
    metadata: dict[str, object] | None = None,
) -> RuleHit:
    hit = RuleHit(
        rule_id=rule_id,
        query=query,
        text_hash=sha1(text.encode("utf-8")).hexdigest()[:12],
        contributed_candidate_id=contributed_candidate_id,
        stage=stage,
        contributed=contributed,
        impact=impact,
        metadata=_sanitize_metadata(metadata),
    )
    _current_hits().append(hit)
    return hit


def drain_rule_hits() -> list[RuleHit]:
    current = _current_hits()
    hits = list(current)
    current.clear()
    return hits


def registered_rules() -> list[RuleDefinition]:
    return list(_RULE_REGISTRY.values())


@contextmanager
def collect_rule_hits() -> Iterator[RuleHitCollector]:
    hits: list[RuleHit] = []
    token = _ACTIVE_RULE_HITS.set(hits)
    collector = RuleHitCollector(hits)
    try:
        yield collector
    finally:
        hits.clear()
        _ACTIVE_RULE_HITS.reset(token)


class RuleHitCollector:
    def __init__(self, hits: list[RuleHit]) -> None:
        self._hits = hits

    def drain(self) -> list[RuleHit]:
        hits = list(self._hits)
        self._hits.clear()
        return hits


def _current_hits() -> list[RuleHit]:
    active = _ACTIVE_RULE_HITS.get()
    if active is None:
        active = []
        _ACTIVE_RULE_HITS.set(active)
    return active


def _sanitize_metadata(metadata: dict[str, object] | None) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key, value in (metadata or {}).items():
        if _metadata_key_contains_raw_text(key):
            sanitized[key] = _hash_metadata_value(value)
            continue
        sanitized[key] = value
    return sanitized


def _metadata_key_contains_raw_text(key: str) -> bool:
    normalized = key.lower()
    if normalized in _SENSITIVE_METADATA_KEYS:
        return True
    return any(part in normalized for part in _SENSITIVE_METADATA_KEY_PARTS)


def _hash_metadata_value(value: object) -> str:
    return sha1(repr(value).encode("utf-8")).hexdigest()[:12]
