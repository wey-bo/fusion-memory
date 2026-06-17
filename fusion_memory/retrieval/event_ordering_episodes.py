from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.event_ordering_common import (
    _event_ordering_sequence_output_sort_key,
    _requested_event_ordering_count,
)
from fusion_memory.retrieval.event_ordering_labels import (
    _EVENT_ORDERING_SEQUENCE_STOPWORDS,
    _EVENT_ORDERING_TOPIC_WORDS,
    _event_ordering_aspect_hint_label,
    _event_ordering_assistant_plan_text,
    _event_ordering_bad_extracted_label,
    _event_ordering_compact_aspect_label,
    _event_ordering_label_key,
    _event_ordering_low_information_record,
    _event_ordering_low_information_text,
    _event_ordering_nominal_event_label,
    _event_ordering_preserve_acronyms,
    _event_ordering_sequence_label,
    _event_ordering_shell_like_label,
    _event_ordering_terms,
    _event_ordering_terms_ordered,
    _short_event_ordering_theme,
)
from fusion_memory.retrieval.event_ordering_records import (
    _dedupe_event_ordering_records,
    _event_ordering_anchor_terms,
    _event_ordering_component_scope_query,
    _event_ordering_episode_terms,
    _event_ordering_search_records,
    _event_ordering_sequence_quality,
)
from fusion_memory.retrieval.event_ordering_typed import (
    _event_ordering_non_event_or_negated_record,
    _event_ordering_record_matches_query_scope,
    _event_ordering_typed_scope_terms,
)


def event_ordering_referenceable_episodes(
    query: str,
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return ordered, source-grounded episode candidates for event-ordering.

    This is intentionally a pack-level candidate pool, not a final answer
    template. It preserves more source tokens than `sequence_items` so the
    answer model can emit labels that remain alignable to reference episodes.
    """

    requested = _requested_event_ordering_count(query) or 0
    records = _event_ordering_referenceable_records(query, source_spans, anchor_timeline)
    if not records:
        return []
    query_terms = _event_ordering_terms(query)
    anchor_terms = _event_ordering_anchor_terms(query)
    episode_terms = _event_ordering_episode_terms(records, anchor_terms)
    scope_terms = _event_ordering_typed_scope_terms(query)
    component_scope = _event_ordering_component_scope_query(query)
    query_facets = _event_ordering_referenceable_query_facets(query)
    episodes: list[dict[str, Any]] = []
    for record in records:
        label = _event_ordering_referenceable_label(record)
        if not label:
            continue
        if scope_terms and not _event_ordering_record_matches_query_scope(query, record, label, scope_terms):
            continue
        base_quality = _event_ordering_sequence_quality(
            record,
            query_terms,
            anchor_terms=anchor_terms,
            episode_terms=episode_terms,
            component_scope=component_scope,
        )
        text = str(record.get("conversation_content") or record.get("text") or "")
        facet_hits = sorted(_event_ordering_terms(f"{label} {text}") & query_facets)
        score = base_quality + min(0.30, 0.06 * len(facet_hits))
        score += _referenceable_detail_score(text)
        if record.get("broad_raw_recall") or "broad_raw_recall" in str(record.get("candidate_source") or ""):
            score -= 0.08
        if score < 0.20:
            continue
        episode: dict[str, Any] = {
            "episode_index": len(episodes) + 1,
            "label": label,
            "source_snippet": compact_summary(text, 420),
            "timeline_index": record.get("timeline_index"),
            "chronology_key": _serializable_chronology_key(record),
            "quality": round(float(score), 4),
            "_sort_key": _event_ordering_sequence_output_sort_key(record),
        }
        if facet_hits:
            episode["facet_hits"] = facet_hits[:10]
        for key in (
            "source_span_id",
            "source_uri",
            "turn_id",
            "speaker",
            "candidate_source",
            "selector",
            "timeline_role",
            "broad_raw_recall",
        ):
            if record.get(key) is not None:
                episode[key] = record[key]
        episodes.append(episode)
    episodes = _dedupe_referenceable_episodes(episodes)
    episodes.sort(key=lambda item: item.get("_sort_key", ()))
    for index, episode in enumerate(episodes, start=1):
        episode["episode_index"] = index
        episode.pop("_sort_key", None)
    limit = max(12, min(36, (requested or 6) * 4))
    return episodes[:limit]


def _event_ordering_referenceable_records(
    query: str,
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records = _event_ordering_search_records(source_spans, anchor_timeline)
    records = _dedupe_event_ordering_records(records)
    out: list[dict[str, Any]] = []
    for record in records:
        speaker = str(record.get("speaker") or "").lower()
        if speaker not in {"user", "document"}:
            continue
        text = str(record.get("conversation_content") or record.get("text") or "")
        if (
            not text.strip()
            or _event_ordering_assistant_plan_text(text)
            or _event_ordering_low_information_record(record)
            or _event_ordering_non_event_or_negated_record(text)
        ):
            continue
        out.append(record)
    out.sort(key=_event_ordering_sequence_output_sort_key)
    return out


def _event_ordering_referenceable_label(record: dict[str, Any]) -> str:
    text = str(record.get("conversation_content") or record.get("text") or "")
    label = _event_ordering_compact_aspect_label(_event_ordering_sequence_label(record), text)
    if not label:
        label = _event_ordering_aspect_hint_label(str(record.get("label") or record.get("timeline_label") or ""), text)
    if not label:
        label = _event_ordering_nominal_event_label(
            str(record.get("label") or record.get("timeline_label") or text),
            context=text,
        )
    if not label:
        label = _short_event_ordering_theme(str(record.get("timeline_label") or record.get("label") or text))
    label = _event_ordering_preserve_acronyms(label.strip())
    if not label or _event_ordering_low_information_text(label) or _event_ordering_shell_like_label(label):
        return ""
    if _event_ordering_bad_extracted_label(label):
        return ""
    detail = _referenceable_detail_phrase(text, label)
    if detail and detail.lower() not in label.lower():
        label = f"{label}; {detail}"
    return compact_summary(label, 240)


def _referenceable_detail_phrase(text: str, label: str) -> str:
    cleaned = re.sub(r"```.*?```", " ", text, flags=re.S)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    label_terms = _event_ordering_terms(label)
    candidates: list[str] = []
    for pattern in [
        r"\b(?:using|with|via|through|by upgrading from|upgrading from)\s+([^.;!?]{4,140})",
        r"\b(?:advice|suggestion|recommended|suggested|told|shared|feedback)\s+(?:from|by)\s+([A-Z][A-Za-z' -]{2,40}[^.;!?]{0,100})",
        r"\b(?:from|with|by)\s+([A-Z][A-Za-z' -]{2,40})\s+(?:at|on|about|for)\s+([^.;!?]{4,80})",
        r"\b(\$[\d,]+(?:\.\d+)?(?:/[a-z]+)?|\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:[a-z][a-z-]{2,20})?)\b[^.;!?]{0,90}",
        r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?[^.;!?]{0,70})",
        r"([`'\"][^`'\"]{3,80}[`'\"])",
        r"\b([a-z][a-z0-9]+(?:[-_][a-z0-9]+)+[^.;!?]{0,80})",
    ]:
        for match in re.finditer(pattern, cleaned, flags=re.I):
            groups = [group.strip(" .,:;-") for group in match.groups() if group and group.strip(" .,:;-")]
            phrase = " ".join(groups) if groups else match.group(0).strip(" .,:;-")
            phrase = _trim_referenceable_phrase(phrase)
            if not phrase or not _referenceable_phrase_has_detail_signal(phrase):
                continue
            terms = _event_ordering_terms(phrase)
            if terms and len(terms - label_terms) >= 1:
                candidates.append(phrase)
    if not candidates:
        return ""
    candidates.sort(key=lambda value: (_referenceable_detail_score(value), len(_event_ordering_terms(value))), reverse=True)
    return _event_ordering_preserve_acronyms(candidates[0])


def _trim_referenceable_phrase(phrase: str) -> str:
    phrase = re.sub(r"\s+", " ", phrase).strip(" .,:;-")
    phrase = re.sub(r"^(?:and|but|so|because|since|that|which)\s+", "", phrase, flags=re.I)
    words = phrase.split()
    if len(words) > 16:
        phrase = " ".join(words[:16]).strip(" .,:;-")
    return phrase


def _referenceable_detail_score(text: str) -> float:
    score = 0.0
    if re.search(r"\b[A-Z][A-Za-z0-9.+#&-]{2,}\b", text):
        score += 0.08
    if re.search(r"\$[\d,]+|v?\d+(?:\.\d+){1,3}|\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:[a-z][a-z-]{2,20})?", text, flags=re.I):
        score += 0.08
    if re.search(r"[`'\"][^`'\"]{3,80}[`'\"]|[a-z][a-z0-9]+(?:[-_][a-z0-9]+)+", text):
        score += 0.10
    if re.search(r"\b(?:implemented|configured|fixed|debugged|planned|decided|chose|selected|compared|reviewed|updated|improved|tracking|met|shared|recommended|suggested|hired|started|completed)\b", text, flags=re.I):
        score += 0.06
    return min(0.30, score)


def _referenceable_phrase_has_detail_signal(phrase: str) -> bool:
    if re.search(r"\b[A-Z][A-Za-z0-9.+#&-]{2,}\b", phrase):
        return True
    if re.search(r"\$[\d,]+|v?\d+(?:\.\d+){1,3}|\d+(?:\.\d+)?%", phrase, flags=re.I):
        return True
    if re.search(r"[`'\"][^`'\"]{3,80}[`'\"]|[a-z][a-z0-9]+(?:[-_][a-z0-9]+)+", phrase):
        return True
    if len(_event_ordering_terms(phrase) - _EVENT_ORDERING_SEQUENCE_STOPWORDS - _EVENT_ORDERING_TOPIC_WORDS) >= 4:
        return True
    return False


def _event_ordering_referenceable_query_facets(query: str) -> set[str]:
    terms = _event_ordering_terms(query)
    terms -= _EVENT_ORDERING_SEQUENCE_STOPWORDS
    terms -= _EVENT_ORDERING_TOPIC_WORDS
    terms -= {
        "different",
        "related",
        "details",
        "ways",
        "concepts",
        "plans",
        "process",
        "progress",
        "personal",
        "professional",
        "work",
    }
    return {term for term in terms if len(term) >= 3}


def _serializable_chronology_key(record: dict[str, Any]) -> list[str]:
    key = _event_ordering_sequence_output_sort_key(record)
    return [repr(part) for part in key[:3]]


def _dedupe_referenceable_episodes(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_spans: set[str] = set()
    seen_labels: set[str] = set()
    for episode in episodes:
        label = str(episode.get("label") or "")
        label_key = _event_ordering_label_key(label)
        span_id = str(episode.get("source_span_id") or "")
        if span_id and span_id in seen_spans:
            continue
        if label_key and _referenceable_label_seen(label_key, seen_labels):
            continue
        out.append(episode)
        if span_id:
            seen_spans.add(span_id)
        if label_key:
            seen_labels.add(label_key)
    return out


def _referenceable_label_seen(label_key: str, seen_labels: set[str]) -> bool:
    terms = set(label_key.split("-"))
    if not terms:
        return False
    for seen in seen_labels:
        seen_terms = set(seen.split("-"))
        if not seen_terms:
            continue
        if len(terms & seen_terms) / max(1, min(len(terms), len(seen_terms))) >= 0.82:
            return True
    return False
