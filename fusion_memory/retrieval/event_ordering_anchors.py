from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.event_ordering_common import (
    _event_ordering_sequence_output_sort_key,
    _requested_event_ordering_count,
)
from fusion_memory.retrieval.event_ordering_labels import (
    _EVENT_ORDERING_CODE_WORDS,
    _EVENT_ORDERING_FACET_WORDS,
    _EVENT_ORDERING_GENERIC_EPISODE_WORDS,
    _EVENT_ORDERING_INFRA_WORDS,
    _EVENT_ORDERING_METHOD_WORDS,
    _EVENT_ORDERING_SEQUENCE_STOPWORDS,
    _EVENT_ORDERING_TOPIC_WORDS,
    _event_ordering_aspect_hint_label,
    _event_ordering_assistant_plan_text,
    _event_ordering_bad_extracted_label,
    _event_ordering_clean_label,
    _event_ordering_compact_aspect_label,
    _event_ordering_fragment_like_label,
    _event_ordering_label_key,
    _event_ordering_label_overlaps_seen,
    _event_ordering_low_information_text,
    _event_ordering_low_information_theme_label,
    _event_ordering_preserve_acronyms,
    _event_ordering_sequence_label,
    _event_ordering_shell_like_label,
    _event_ordering_terms,
    _event_ordering_terms_ordered,
    _event_ordering_under_specified_topic_label,
)
from fusion_memory.retrieval.event_ordering_milestones import _event_ordering_project_timeline_query
from fusion_memory.retrieval.event_ordering_records import (
    _event_ordering_anchor_terms,
    _event_ordering_focus_terms,
    _event_ordering_representatives,
    _event_ordering_search_records,
)
from fusion_memory.retrieval.event_ordering_typed import (
    _event_ordering_non_event_or_negated_record,
    _event_ordering_record_matches_query_scope,
    _event_ordering_typed_scope_terms,
)

def _event_ordering_query_scoped_phase_sequence_items(query: str, anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0 or not anchors:
        return []
    if _event_ordering_project_timeline_query(query):
        return []
    records: list[dict[str, Any]] = []
    scope_terms = _event_ordering_typed_scope_terms(query)
    focus_terms = _event_ordering_focus_terms(query, _event_ordering_search_records([], anchors))
    query_terms = _event_ordering_terms(query) - _EVENT_ORDERING_SEQUENCE_STOPWORDS - _EVENT_ORDERING_TOPIC_WORDS
    for anchor in anchors:
        if str(anchor.get("speaker") or "").lower() in {"assistant", "agent"}:
            continue
        context_text = str(anchor.get("conversation_content") or anchor.get("content") or "")
        if not context_text.strip() or _event_ordering_assistant_plan_text(context_text) or _event_ordering_non_event_or_negated_record(context_text):
            continue
        label = _event_ordering_label_for_anchor_phase(anchor)
        if not label:
            continue
        record = {
            "label": anchor.get("label"),
            "timeline_label": anchor.get("label"),
            "text": " ".join(str(value or "") for value in [anchor.get("label"), context_text]),
            "conversation_content": context_text,
        }
        if scope_terms and not _event_ordering_record_matches_query_scope(query, record, label, scope_terms):
            continue
        score = _event_ordering_phase_candidate_score(query, label, context_text, query_terms, focus_terms, scope_terms)
        if score < 0.12:
            continue
        records.append(
            {
                "anchor": anchor,
                "label": label,
                "score": score,
                "sort_key": _event_ordering_sequence_output_sort_key(
                    {
                        "timeline_index": anchor.get("timeline_index"),
                        "source_uri": anchor.get("source_uri"),
                        "turn_id": anchor.get("turn_id"),
                    }
                ),
            }
        )
    records = _dedupe_event_ordering_phase_candidates(records)
    if not records:
        return []
    selected = _event_ordering_select_phase_candidates(records, requested)
    if not selected:
        return []
    selected.sort(key=lambda item: item["sort_key"])
    items: list[dict[str, Any]] = []
    for item in selected[:requested]:
        anchor = item["anchor"]
        context_text = str(anchor.get("conversation_content") or anchor.get("content") or "")
        out: dict[str, Any] = {
            "sequence_index": len(items) + 1,
            "label": item["label"],
            "context": compact_summary(context_text, 260),
            "timeline_index": anchor.get("timeline_index"),
            "selector": "query_scoped_phase",
        }
        source_span_ids = [str(span_id) for span_id in anchor.get("source_span_ids") or [] if span_id]
        if source_span_ids:
            out["source_span_ids"] = source_span_ids[:4]
        items.append(out)
    return items

def _event_ordering_label_for_anchor_phase(anchor: dict[str, Any]) -> str:
    context_text = str(anchor.get("conversation_content") or anchor.get("content") or "")
    label = _event_ordering_sequence_label(
        {
            "label": anchor.get("label"),
            "text": " ".join(str(value or "") for value in [anchor.get("label"), anchor.get("content"), context_text]),
            "conversation_content": context_text,
        }
    )
    label = _event_ordering_compact_aspect_label(_event_ordering_clean_label(label), context_text)
    hint_label = _event_ordering_aspect_hint_label(label, context_text)
    if hint_label and (
        len(label.split()) > 7
        or re.search(r"\b(?:i|we|my|our)\b", label, flags=re.I)
        or _event_ordering_under_specified_topic_label(label)
    ):
        label = _event_ordering_preserve_acronyms(hint_label)
    if not label or _event_ordering_low_information_text(label) or _event_ordering_shell_like_label(label):
        return ""
    if _event_ordering_bad_extracted_label(label) and not hint_label:
        return ""
    return label

def _event_ordering_phase_candidate_score(
    query: str,
    label: str,
    context: str,
    query_terms: set[str],
    focus_terms: set[str],
    scope_terms: set[str],
) -> float:
    label_terms = _event_ordering_terms(label)
    context_terms = _event_ordering_terms(context)
    all_terms = label_terms | context_terms
    score = 0.30
    score += min(0.26, 0.05 * len(all_terms & query_terms))
    score += min(0.24, 0.05 * len(all_terms & focus_terms))
    score += min(0.20, 0.05 * len(all_terms & scope_terms))
    if re.search(r"\b(?:started|used|implemented|configured|created|added|fixed|reviewed|worked|focused|decided|chose|collaborated|suggested|recommended|increased|reduced|improved|planned|scheduled)\b", context, flags=re.I):
        score += 0.16
    if _event_ordering_fragment_like_label(label) or _event_ordering_low_information_theme_label(label):
        score -= 0.25
    if len(label.split()) > 10:
        score -= 0.08
    if scope_terms and not (all_terms & scope_terms):
        score -= 0.30
    return score

def _dedupe_event_ordering_phase_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=lambda value: (value["sort_key"], -float(value["score"]))):
        key = _event_ordering_label_key(str(item["label"]))
        if not key or key in seen or _event_ordering_label_overlaps_seen(key, seen):
            continue
        out.append(item)
        seen.add(key)
    return out

def _event_ordering_select_phase_candidates(candidates: list[dict[str, Any]], requested: int) -> list[dict[str, Any]]:
    if len(candidates) <= requested:
        return candidates
    ordered = sorted(candidates, key=lambda value: value["sort_key"])
    selected: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    for bucket in range(requested):
        start = round(bucket * len(ordered) / requested)
        end = round((bucket + 1) * len(ordered) / requested)
        if end <= start:
            end = min(len(ordered), start + 1)
        window = [item for item in ordered[start:end] if id(item) not in used_ids]
        if not window:
            continue
        choice = max(window, key=lambda item: (float(item["score"]), -len(str(item["label"]).split())))
        selected.append(choice)
        used_ids.add(id(choice))
    if len(selected) < requested:
        for item in sorted(ordered, key=lambda value: float(value["score"]), reverse=True):
            if id(item) in used_ids:
                continue
            selected.append(item)
            used_ids.add(id(item))
            if len(selected) >= requested:
                break
    return selected[:requested]

def _event_ordering_anchor_sequence_items(query: str, anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0 or len(anchors) < requested:
        return []
    anchors = [
        anchor
        for anchor in anchors
        if str(anchor.get("speaker") or "").lower() not in {"assistant", "agent"}
        and not _event_ordering_assistant_plan_text(
            str(anchor.get("conversation_content") or anchor.get("content") or "")
        )
    ]
    if len(anchors) < requested:
        return []
    focus_terms = _event_ordering_focus_terms(query, _event_ordering_search_records([], anchors))
    anchor_terms = _event_ordering_anchor_terms(query)
    scope_terms = focus_terms | anchor_terms
    if scope_terms:
        focused_anchors = [
            anchor
            for anchor in anchors
            if _event_ordering_anchor_matches_focus(anchor, scope_terms)
        ]
        episode_anchors = _event_ordering_episode_focused_anchors(
            anchors,
            focused_anchors,
            scope_terms,
            requested,
        )
        if len(episode_anchors) >= requested:
            anchors = episode_anchors
        elif len(focused_anchors) >= requested:
            anchors = focused_anchors
    anchor_records = [_event_ordering_anchor_candidate_record(anchor) for anchor in anchors]
    if len(anchor_records) > requested:
        anchor_records = _event_ordering_representatives(query, anchor_records, requested)
    anchor_records.sort(key=_event_ordering_sequence_output_sort_key)
    anchors = [record["_anchor"] for record in anchor_records if isinstance(record.get("_anchor"), dict)]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    typed_scope_terms = _event_ordering_typed_scope_terms(query)
    for anchor in anchors:
        context_text = str(anchor.get("conversation_content") or anchor.get("content") or "")
        label = _event_ordering_sequence_label(
            {
                "label": anchor.get("label"),
                "text": " ".join(
                    str(value or "")
                    for value in [
                        anchor.get("label"),
                        anchor.get("content"),
                        anchor.get("conversation_content"),
                    ]
                ),
                "conversation_content": context_text,
            }
        )
        label = _event_ordering_compact_aspect_label(
            _event_ordering_clean_label(label),
            context_text,
        )
        hint_label = _event_ordering_aspect_hint_label(label, context_text)
        if hint_label and (
            len(label.split()) > 7
            or re.search(r"\b(?:i|we|my|our)\b", label, flags=re.I)
            or _event_ordering_under_specified_topic_label(label)
        ):
            label = _event_ordering_preserve_acronyms(hint_label)
        if typed_scope_terms and not _event_ordering_record_matches_query_scope(
            query,
            {
                "label": anchor.get("label"),
                "timeline_label": anchor.get("label"),
                "text": " ".join(str(value or "") for value in [anchor.get("label"), context_text]),
                "conversation_content": context_text,
            },
            label,
            typed_scope_terms,
        ):
            continue
        if not label or _event_ordering_low_information_text(label) or _event_ordering_shell_like_label(label):
            continue
        key = _event_ordering_label_key(label)
        if key in seen or _event_ordering_label_overlaps_seen(key, seen):
            continue
        item: dict[str, Any] = {
            "sequence_index": len(items) + 1,
            "label": label,
            "context": compact_summary(str(anchor.get("conversation_content") or anchor.get("content") or ""), 260),
            "timeline_index": anchor.get("timeline_index"),
        }
        source_span_ids = [str(span_id) for span_id in anchor.get("source_span_ids") or [] if span_id]
        if source_span_ids:
            item["source_span_ids"] = source_span_ids[:4]
        items.append(item)
        seen.add(key)
        if len(items) >= requested:
            break
    return items if len(items) == requested else []

def _event_ordering_anchor_candidate_record(anchor: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        str(value or "")
        for value in [
            anchor.get("label"),
            anchor.get("content"),
            anchor.get("conversation_content"),
        ]
    )
    return {
        "_anchor": anchor,
        "label": anchor.get("label"),
        "text": text,
        "conversation_content": anchor.get("conversation_content") or anchor.get("content"),
        "timeline_index": anchor.get("timeline_index"),
        "turn_id": anchor.get("turn_id"),
        "source_uri": anchor.get("source_uri"),
        "source_span_ids": anchor.get("source_span_ids") or [],
        "selector": anchor.get("selector"),
        "timeline_role": anchor.get("timeline_role"),
    }

def _event_ordering_anchor_matches_focus(anchor: dict[str, Any], scope_terms: set[str]) -> bool:
    if not scope_terms:
        return True
    text = " ".join(
        str(value or "")
        for value in [
            anchor.get("label"),
            anchor.get("content"),
            anchor.get("conversation_content"),
        ]
    )
    label_terms = _event_ordering_terms(str(anchor.get("label") or ""))
    all_terms = _event_ordering_terms(text)
    if label_terms & scope_terms:
        return True
    if all_terms & scope_terms:
        return True
    coverage_terms = {
        str(term).lower()
        for term in (anchor.get("coverage_terms") or [])
        if str(term or "").strip()
    }
    return bool(coverage_terms & scope_terms)

def _event_ordering_episode_focused_anchors(
    anchors: list[dict[str, Any]],
    focused_anchors: list[dict[str, Any]],
    scope_terms: set[str],
    requested: int,
) -> list[dict[str, Any]]:
    if not anchors or not focused_anchors or len(anchors) <= requested:
        return anchors if len(anchors) >= requested else []
    seed_terms = _event_ordering_episode_seed_terms(focused_anchors, scope_terms)
    if not seed_terms:
        return focused_anchors
    out: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    started = False
    misses_after_seed = 0
    for anchor in anchors:
        direct = _event_ordering_anchor_matches_focus(anchor, scope_terms)
        episode = _event_ordering_anchor_matches_episode(anchor, seed_terms)
        if direct or episode:
            out.append(anchor)
            seen_ids.add(id(anchor))
            started = True
            misses_after_seed = 0
            continue
        if started:
            misses_after_seed += 1
            if misses_after_seed >= 2 and len(out) >= requested:
                break
    if len(out) < requested:
        for anchor in focused_anchors:
            if id(anchor) in seen_ids:
                continue
            out.append(anchor)
            seen_ids.add(id(anchor))
            if len(out) >= requested:
                break
    return out if len(out) >= requested else focused_anchors

def _event_ordering_episode_seed_terms(
    focused_anchors: list[dict[str, Any]],
    scope_terms: set[str],
) -> set[str]:
    terms: list[str] = []
    for anchor in focused_anchors[:1]:
        text = " ".join(
            str(value or "")
            for value in [
                anchor.get("label"),
                anchor.get("content"),
                anchor.get("conversation_content"),
            ]
        )
        for term in _event_ordering_terms_ordered(text):
            if term in _EVENT_ORDERING_TOPIC_WORDS or term in _EVENT_ORDERING_SEQUENCE_STOPWORDS:
                continue
            if term in _EVENT_ORDERING_GENERIC_EPISODE_WORDS:
                continue
            if term in _EVENT_ORDERING_CODE_WORDS or term in _EVENT_ORDERING_INFRA_WORDS or term in _EVENT_ORDERING_METHOD_WORDS:
                continue
            terms.append(term)
    seed_terms = set(terms[:18])
    return seed_terms | scope_terms

def _event_ordering_anchor_matches_episode(anchor: dict[str, Any], episode_terms: set[str]) -> bool:
    if not episode_terms:
        return False
    text = " ".join(
        str(value or "")
        for value in [
            anchor.get("label"),
            anchor.get("content"),
            anchor.get("conversation_content"),
        ]
    )
    label_terms = _event_ordering_terms(str(anchor.get("label") or ""))
    all_terms = _event_ordering_terms(text)
    overlap = (label_terms | all_terms) & episode_terms
    if len(overlap) >= 2:
        return True
    if overlap and ((label_terms | all_terms) & _EVENT_ORDERING_FACET_WORDS):
        return True
    return False
