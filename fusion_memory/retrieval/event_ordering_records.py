from __future__ import annotations

import re
from typing import Any

from fusion_memory.retrieval.event_ordering_common import _event_ordering_record_sort_key
from fusion_memory.retrieval.event_ordering_labels import (
    _EVENT_ORDERING_CODE_WORDS,
    _EVENT_ORDERING_DESIGN_DRIFT_WORDS,
    _EVENT_ORDERING_FACET_WORDS,
    _EVENT_ORDERING_IMPLEMENTATION_SIGNAL_WORDS,
    _EVENT_ORDERING_INFRA_WORDS,
    _EVENT_ORDERING_METHOD_WORDS,
    _EVENT_ORDERING_TOPIC_WORDS,
    _event_ordering_label_key,
    _event_ordering_label_overlaps_seen,
    _event_ordering_low_information_record,
    _event_ordering_phase_key,
    _event_ordering_plain_support_text,
    _event_ordering_sequence_label,
    _event_ordering_standing_preference_record,
    _event_ordering_terms,
    _event_ordering_terms_ordered,
)

def _event_ordering_search_records(
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in anchor_timeline:
        text = " ".join(str(value or "") for value in [item.get("label"), item.get("content"), item.get("conversation_content")])
        records.append(
            {
                "text": text,
                "label": item.get("label"),
                "timeline_label": item.get("label"),
                "source_span_id": next((str(span_id) for span_id in item.get("source_span_ids", []) if span_id), None),
                "timeline_index": item.get("timeline_index"),
                "source_uri": item.get("source_uri"),
                "turn_id": item.get("turn_id"),
                "speaker": item.get("speaker"),
                "candidate_source": item.get("candidate_source"),
                "selector": "event_ordering_coverage",
                "timeline_role": "user_aspect_anchor",
                "conversation_content": item.get("conversation_content"),
                "aspect_key": item.get("aspect_key"),
                "coverage_terms": item.get("coverage_terms"),
            }
        )
    for span in source_spans:
        text = " ".join(str(value or "") for value in [span.get("timeline_label"), span.get("content"), span.get("conversation_content")])
        records.append(
            {
                "text": text,
                "label": span.get("timeline_label"),
                "timeline_label": span.get("timeline_label"),
                "source_span_id": str(span.get("original_span_id") or span.get("id") or "") or None,
                "timeline_index": span.get("timeline_index"),
                "source_uri": span.get("source_uri"),
                "turn_id": span.get("turn_id"),
                "speaker": span.get("speaker"),
                "candidate_source": span.get("candidate_source"),
                "selector": span.get("selector"),
                "timeline_role": span.get("timeline_role"),
                "conversation_content": span.get("conversation_content"),
                "aspect_key": span.get("aspect_key"),
                "coverage_terms": span.get("coverage_terms"),
                "broad_raw_recall": span.get("broad_raw_recall"),
                "recall_query": span.get("recall_query"),
            }
        )
    return records

def _dedupe_event_ordering_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_span_keys: set[str] = set()
    seen_labels: set[str] = set()
    for record in records:
        text = str(record.get("text") or "")
        if not text.strip():
            continue
        label_key = _event_ordering_label_key(_event_ordering_sequence_label(record))
        span_id = str(record.get("source_span_id") or "")
        span_key = span_id
        if record.get("selector") == "event_ordering_coverage" and record.get("timeline_role") == "user_aspect_anchor":
            aspect_key = str(record.get("aspect_key") or label_key or "")
            span_key = f"{span_id}:{aspect_key}" if span_id else aspect_key
        if span_key and span_key in seen_span_keys:
            continue
        if label_key and label_key in seen_labels:
            continue
        if label_key and _event_ordering_label_overlaps_seen(label_key, seen_labels):
            continue
        out.append(record)
        if span_key:
            seen_span_keys.add(span_key)
        if label_key:
            seen_labels.add(label_key)
    return out

def _attach_following_event_ordering_support(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        item = dict(record)
        speaker = str(item.get("speaker") or "").lower()
        if speaker and speaker not in {"user", "document"}:
            enriched.append(item)
            continue
        support = _nearest_following_event_ordering_support(records, index)
        if support:
            item["support_text"] = support
        enriched.append(item)
    return enriched

def _nearest_following_event_ordering_support(records: list[dict[str, Any]], index: int) -> str:
    current = records[index]
    current_position = _event_ordering_record_sort_key(current)
    for candidate in records[index + 1 : min(len(records), index + 4)]:
        speaker = str(candidate.get("speaker") or "").lower()
        if speaker in {"user", "document"}:
            break
        if _event_ordering_record_sort_key(candidate) < current_position:
            continue
        text = str(candidate.get("text") or "")
        if len(text.strip()) >= 40:
            return text
    return ""

def _event_ordering_representatives(query: str, records: list[dict[str, Any]], requested: int) -> list[dict[str, Any]]:
    focus_terms = _event_ordering_focus_terms(query, records)
    anchor_terms = _event_ordering_anchor_terms(query)
    if focus_terms:
        label_focused = [
            record
            for record in records
            if _event_ordering_terms(_event_ordering_sequence_label(record)) & focus_terms
        ]
        if anchor_terms and focus_terms <= anchor_terms and len(label_focused) < max(2, min(requested, len(records))):
            focused = []
        else:
            focused = label_focused if len(label_focused) >= max(2, min(requested, len(records))) else [
                record for record in records if _event_ordering_terms(str(record.get("text") or "")) & focus_terms
            ]
        if len(focused) >= max(2, min(requested, len(records))):
            records = focused
    if len(records) <= requested:
        return records
    query_terms = _event_ordering_terms(query)
    episode_terms = _event_ordering_episode_terms(records, anchor_terms)
    component_scope = _event_ordering_component_scope_query(query)
    scored = [
        (
            _event_ordering_sequence_quality(
                record,
                query_terms,
                anchor_terms=anchor_terms,
                episode_terms=episode_terms,
                component_scope=component_scope,
            ),
            index,
            record,
        )
        for index, record in enumerate(records)
    ]
    if component_scope:
        return _event_ordering_top_representatives(scored, requested, component_scope=component_scope, anchor_terms=anchor_terms)
    selected: list[tuple[int, dict[str, Any]]] = []
    used_phases: set[str] = set()
    total = len(records)

    for cluster_index in range(requested):
        start = round(cluster_index * total / requested)
        end = round((cluster_index + 1) * total / requested)
        if end <= start:
            end = min(total, start + 1)
        window = scored[start:end] or scored[start : start + 1]
        choice = _best_event_ordering_window_choice(window, used_phases)
        selected.append((choice[1], choice[2]))
        phase = _event_ordering_phase_key(choice[2])
        if phase:
            used_phases.add(phase)

    selected.sort(key=lambda item: item[0])
    out = [record for _index, record in selected]
    if len(out) < requested:
        selected_ids = {id(record) for record in out}
        for _score, index, record in sorted(scored, key=lambda item: (item[0], -item[1]), reverse=True):
            if id(record) in selected_ids:
                continue
            out.append(record)
            selected_ids.add(id(record))
            if len(out) >= requested:
                break
        out.sort(key=lambda record: records.index(record))
    return out[:requested]

def _event_ordering_top_representatives(
    scored: list[tuple[float, int, dict[str, Any]]],
    requested: int,
    *,
    component_scope: bool = False,
    anchor_terms: set[str] | None = None,
) -> list[dict[str, Any]]:
    selected: list[tuple[float, int, dict[str, Any]]] = []
    used_label_keys: set[str] = set()
    deferred: list[tuple[float, int, dict[str, Any]]] = []
    ranked = sorted(scored, key=lambda item: (item[0], -item[1]), reverse=True)
    for score, index, record in ranked:
        if component_scope and (
            _event_ordering_component_drift(record, anchor_terms or set())
            or _event_ordering_component_design_drift(record, anchor_terms or set())
        ):
            deferred.append((score, index, record))
            continue
        label_key = _event_ordering_label_key(_event_ordering_sequence_label(record))
        if label_key and label_key in used_label_keys:
            continue
        selected.append((score, index, record))
        if label_key:
            used_label_keys.add(label_key)
        if len(selected) >= requested:
            break
    if len(selected) < requested:
        for score, index, record in deferred:
            label_key = _event_ordering_label_key(_event_ordering_sequence_label(record))
            if label_key and label_key in used_label_keys:
                continue
            selected.append((score, index, record))
            if label_key:
                used_label_keys.add(label_key)
            if len(selected) >= requested:
                break
    selected.sort(key=lambda item: item[1])
    return [record for _score, _index, record in selected]

def _event_ordering_component_drift(record: dict[str, Any], anchor_terms: set[str]) -> bool:
    label_terms = _event_ordering_terms(_event_ordering_sequence_label(record))
    if label_terms & anchor_terms or label_terms & _EVENT_ORDERING_FACET_WORDS:
        return False
    return bool(label_terms & (_EVENT_ORDERING_INFRA_WORDS | _EVENT_ORDERING_METHOD_WORDS))

def _event_ordering_component_design_drift(record: dict[str, Any], anchor_terms: set[str]) -> bool:
    label_terms = _event_ordering_terms(_event_ordering_sequence_label(record))
    text_terms = _event_ordering_terms(str(record.get("text") or ""))
    terms = label_terms | text_terms
    if label_terms & anchor_terms:
        return False
    strong_implementation_terms = _EVENT_ORDERING_IMPLEMENTATION_SIGNAL_WORDS - {"error", "errors", "response", "responses"}
    if label_terms & strong_implementation_terms:
        return False
    return bool(terms & _EVENT_ORDERING_DESIGN_DRIFT_WORDS)

def _best_event_ordering_window_choice(
    window: list[tuple[float, int, dict[str, Any]]],
    used_phases: set[str],
) -> tuple[float, int, dict[str, Any]]:
    def key(item: tuple[float, int, dict[str, Any]]) -> tuple[float, float, int]:
        score, index, record = item
        phase = _event_ordering_phase_key(record)
        diversity = 0.20 if phase and phase not in used_phases else 0.0
        return (score + diversity, score, -index)

    best = max(window, key=key)
    best_score = key(best)[0]
    early_good = [
        item
        for item in window
        if key(item)[0] >= best_score - 0.12
    ]
    if early_good:
        return min(early_good, key=lambda item: item[1])
    return best

def _event_ordering_focus_terms(query: str, records: list[dict[str, Any]]) -> set[str]:
    query_terms = _event_ordering_terms(query) - _EVENT_ORDERING_TOPIC_WORDS
    if not query_terms or not records:
        return set()
    anchor_terms = _event_ordering_anchor_terms(query)
    document_frequency: dict[str, int] = {}
    for record in records:
        for term in _event_ordering_terms(str(record.get("text") or "")):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    high_frequency = {
        term
        for term, count in document_frequency.items()
        if count / max(1, len(records)) >= 0.35 and term not in _EVENT_ORDERING_FACET_WORDS and term not in anchor_terms
    }
    focus = query_terms - high_frequency
    return focus if len(focus) >= 2 else set()

def _event_ordering_sequence_quality(
    record: dict[str, Any],
    query_terms: set[str],
    *,
    anchor_terms: set[str] | None = None,
    episode_terms: set[str] | None = None,
    component_scope: bool = False,
) -> float:
    text = str(record.get("text") or "")
    lower = text.lower()
    if _event_ordering_low_information_record(record):
        return -1.0
    terms = _event_ordering_terms(text)
    label_terms = _event_ordering_terms(_event_ordering_sequence_label(record))
    anchor_terms = set(anchor_terms or set())
    episode_terms = set(episode_terms or set())
    score = 0.20 + min(0.40, 0.08 * len(query_terms & terms))
    if anchor_terms:
        direct_anchor_match = bool((label_terms | terms) & anchor_terms)
        if label_terms & anchor_terms:
            score += 0.30
        elif terms & anchor_terms:
            score += 0.12
        elif not label_terms & _EVENT_ORDERING_FACET_WORDS:
            score -= 0.22
        if not (label_terms & anchor_terms) and label_terms & _EVENT_ORDERING_INFRA_WORDS:
            score -= 0.55 if component_scope else 0.25
            if component_scope and not label_terms & _EVENT_ORDERING_FACET_WORDS and len(label_terms) <= 4:
                score = min(score, 0.35)
        if not (label_terms & anchor_terms) and label_terms & _EVENT_ORDERING_METHOD_WORDS:
            score -= 0.40 if component_scope else 0.18
        if component_scope:
            implementation_overlap = (label_terms | terms) & _EVENT_ORDERING_IMPLEMENTATION_SIGNAL_WORDS
            if implementation_overlap:
                score += min(0.42, 0.14 * len(implementation_overlap))
            if not direct_anchor_match and (label_terms | terms) & _EVENT_ORDERING_DESIGN_DRIFT_WORDS:
                score -= 0.85
    if episode_terms:
        episode_overlap = (label_terms | terms) & episode_terms
        if episode_overlap:
            score += min(0.24, 0.06 * len(episode_overlap))
        elif anchor_terms:
            score -= 0.16
    if record.get("timeline_index") is not None:
        score += 0.10
    if re.search(r"\b(?:i|we)\s+(?:am|was|were|have|had|need|needed|want|wanted|started|finished|completed|implemented|configured|created|added|fixed|reviewed|worked|tried|decided|chose|asked|mentioned|focused)\b", lower):
        score += 0.25
    if re.search(r"\b(?:error|issue|problem|blocked|trouble|concern|question|compare|decision|change|update|result|progress|next)\b", lower):
        score += 0.12
    if re.search(r"\b(?:schedule|timeline|deadline|time anchor)\b", lower) and not re.search(
        r"\b(?:started|finished|completed|implemented|configured|created|added|fixed|reviewed|worked|changed|updated)\b",
        lower,
    ):
        score -= 0.20
    if _event_ordering_standing_preference_record(record):
        score -= 0.22
    if len(text) > 700:
        score -= 0.05
    return score

def _event_ordering_anchor_terms(query: str) -> set[str]:
    lower = query.lower()
    anchors: list[str] = []
    patterns = [
        r"\b(?:aspects of|features of|concerns about)\s+(?:implementing|developing|building|creating|setting up|working on|handling)?\s*(?:my|the|this|that)?\s*([a-z0-9][a-z0-9 +#./-]{4,80}?)(?:\s+throughout|\s+across|\s+in order|\?|$)",
        r"\b(?:my|the|this|that)\s+([a-z0-9][a-z0-9 +#./-]*(?:feature|app|application|website|tracker|dashboard|project|system|tool|api|code))\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lower):
            anchors.append(match.group(1))
    if not anchors:
        return set()
    terms = _event_ordering_terms(anchors[0]) - _EVENT_ORDERING_TOPIC_WORDS
    return terms if len(terms) >= 1 else set()

def _event_ordering_component_scope_query(query: str) -> bool:
    return bool(re.search(r"\b(?:feature|function|component|module|endpoint|handler|code)\b", query, flags=re.I))

def _event_ordering_episode_terms(records: list[dict[str, Any]], anchor_terms: set[str]) -> set[str]:
    if not records:
        return set()
    seed = next(
        (
            record
            for record in records
            if anchor_terms and (_event_ordering_terms(str(record.get("text") or "")) | _event_ordering_terms(_event_ordering_sequence_label(record))) & anchor_terms
        ),
        records[0],
    )
    text = " ".join(
        [
            str(seed.get("label") or ""),
            str(seed.get("text") or ""),
            _event_ordering_plain_support_text(str(seed.get("support_text") or "")),
        ]
    )
    terms = [
        term
        for term in _event_ordering_terms_ordered(text)
        if term not in _EVENT_ORDERING_TOPIC_WORDS and term not in _EVENT_ORDERING_CODE_WORDS
    ]
    return set(terms[:18])
