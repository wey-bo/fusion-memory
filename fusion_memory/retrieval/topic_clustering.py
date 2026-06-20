from __future__ import annotations

from dataclasses import dataclass

from fusion_memory.core.text import tokenize
from fusion_memory.retrieval.taxonomy import taxonomy_entry_for_text


STOPWORDS = {
    "a",
    "after",
    "an",
    "and",
    "first",
    "i",
    "implemented",
    "next",
    "later",
    "set",
    "the",
    "then",
    "up",
}


@dataclass(frozen=True)
class TopicClusterDecision:
    label: str
    confidence: float
    reasons: tuple[str, ...]
    aliases: tuple[str, ...] = ()


def cluster_topic_label(
    text: str,
    *,
    session_hint: str | None = None,
    previous_label: str | None = None,
) -> TopicClusterDecision:
    entry = taxonomy_entry_for_text(text)
    if entry is not None:
        return TopicClusterDecision(
            label=entry.label,
            confidence=0.90 if len(entry.label.split()) >= 2 else 0.78,
            reasons=("taxonomy",),
            aliases=tuple(entry.aliases),
        )

    tokens = _meaningful_tokens(text)
    token_set = set(tokens)
    hint_tokens = {token for token in tokenize(session_hint or "") if len(token) > 2}
    previous_tokens = {token for token in tokenize(previous_label or "") if len(token) > 2}
    if session_hint and (token_set & hint_tokens or token_set & previous_tokens or _is_continuation(text)):
        return TopicClusterDecision(
            label=session_hint,
            confidence=0.74,
            reasons=("session_hint",),
            aliases=(previous_label,) if previous_label and previous_label != session_hint else (),
        )
    if previous_label and _is_continuation(text):
        return TopicClusterDecision(label=previous_label, confidence=0.62, reasons=("previous_topic",))
    label = " ".join(tokens[:4]) or "unknown"
    return TopicClusterDecision(label=label, confidence=0.45, reasons=("lexical_fallback",))


def cluster_topic_telemetry(decisions: list[TopicClusterDecision]) -> dict[str, object]:
    return {
        "decision_count": len(decisions),
        "merged_by_session_hint": sum(1 for decision in decisions if "session_hint" in decision.reasons),
        "taxonomy_count": sum(1 for decision in decisions if "taxonomy" in decision.reasons),
        "fallback_count": sum(1 for decision in decisions if "lexical_fallback" in decision.reasons),
        "labels": sorted({decision.label for decision in decisions}),
    }


def _is_continuation(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("then", "next", "later", "after that", "随后", "然后", "接着"))


def _meaningful_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for token in tokenize(text):
        if token in STOPWORDS or len(token) <= 1:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens
