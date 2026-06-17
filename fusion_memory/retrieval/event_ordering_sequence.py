from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.event_ordering_common import (
    _event_ordering_natural_key,
    _event_ordering_record_sort_key,
    _requested_event_ordering_count,
    _event_ordering_sequence_output_sort_key,
    _safe_int,
)
from fusion_memory.retrieval.event_ordering_labels import (
    _EVENT_ORDERING_SEQUENCE_STOPWORDS,
    _EVENT_ORDERING_TOPIC_WORDS,
    _event_ordering_assistant_plan_text,
    _event_ordering_aspect_hint_label,
    _event_ordering_bad_extracted_label,
    _event_ordering_cluster_fallback_label,
    _event_ordering_cluster_label,
    _event_ordering_compact_aspect_label,
    _event_ordering_label_key,
    _event_ordering_label_overlaps_seen,
    _event_ordering_low_information_record,
    _event_ordering_low_information_text,
    _event_ordering_nominal_event_label,
    _event_ordering_preference_sequence_query,
    _event_ordering_preserve_acronyms,
    _event_ordering_sequence_label,
    _event_ordering_shell_like_label,
    _event_ordering_standing_preference_record,
    _event_ordering_terms,
    _event_ordering_terms_ordered,
)
from fusion_memory.retrieval.event_ordering_milestones import (
    _event_ordering_diversify_milestone_selection,
    _event_ordering_first_diverse_milestone_candidate,
    _event_ordering_lifecycle_milestone_query,
    _event_ordering_milestone_candidates,
    _event_ordering_milestone_matches_query,
    _event_ordering_milestone_sequence_items,
    _event_ordering_milestone_source_key,
    _event_ordering_project_timeline_query,
    _event_ordering_select_milestones,
)
from fusion_memory.retrieval.event_ordering_typed import (
    _dedupe_event_ordering_typed_aspects,
    _event_ordering_finalize_typed_label,
    _event_ordering_non_event_or_negated_record,
    _event_ordering_normalize_typed_aspect_label,
    _event_ordering_record_matches_query_scope,
    _event_ordering_select_typed_aspects,
    _event_ordering_typed_aspect_label,
    _event_ordering_typed_aspect_score,
    _event_ordering_typed_aspect_sequence_items,
    _event_ordering_typed_scope_terms,
)
from fusion_memory.retrieval.event_ordering_anchors import (
    _dedupe_event_ordering_phase_candidates,
    _event_ordering_anchor_candidate_record,
    _event_ordering_anchor_matches_episode,
    _event_ordering_anchor_matches_focus,
    _event_ordering_anchor_sequence_items,
    _event_ordering_episode_focused_anchors,
    _event_ordering_episode_seed_terms,
    _event_ordering_label_for_anchor_phase,
    _event_ordering_phase_candidate_score,
    _event_ordering_query_scoped_phase_sequence_items,
    _event_ordering_select_phase_candidates,
)
from fusion_memory.retrieval.event_ordering_records import (
    _attach_following_event_ordering_support,
    _best_event_ordering_window_choice,
    _dedupe_event_ordering_records,
    _event_ordering_anchor_terms,
    _event_ordering_component_design_drift,
    _event_ordering_component_drift,
    _event_ordering_component_scope_query,
    _event_ordering_episode_terms,
    _event_ordering_focus_terms,
    _event_ordering_representatives,
    _event_ordering_search_records,
    _event_ordering_sequence_quality,
    _event_ordering_top_representatives,
    _nearest_following_event_ordering_support,
)

def _event_ordering_phase_clusters(query: str, anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not anchors:
        return []
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0 or len(anchors) <= requested:
        requested = min(len(anchors), max(1, requested or len(anchors)))
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_terms: set[str] = set()
    for anchor in anchors:
        terms = _event_ordering_terms(str(anchor.get("label") or "")) | _event_ordering_terms(
            " ".join(str(value or "") for value in [anchor.get("label"), anchor.get("content"), anchor.get("conversation_content")])
        )
        if current and terms and current_terms and len(terms & current_terms) == 0 and len(clusters) + 1 < requested:
            clusters.append(current)
            current = []
            current_terms = set()
        current.append(anchor)
        current_terms.update(terms)
    if current:
        clusters.append(current)
    if len(clusters) > requested:
        merged: list[list[dict[str, Any]]] = []
        for cluster in clusters:
            if len(merged) < requested - 1:
                merged.append(cluster)
                continue
            if merged:
                merged[-1].extend(cluster)
            else:
                merged.append(cluster)
        clusters = merged
    packed_clusters = clusters[:requested]
    out: list[dict[str, Any]] = []
    for members in packed_clusters:
        labels = [str(item.get("label") or "").strip() for item in members if item.get("label")]
        evidence = [
            str(item.get("conversation_content") or item.get("content") or "").strip()
            for item in members
            if item.get("conversation_content") or item.get("content")
        ]
        cluster: dict[str, Any] = {
            "phase_index": len(out) + 1,
            "timeline_start": members[0].get("timeline_index"),
            "timeline_end": members[-1].get("timeline_index"),
            "candidate_labels": labels[:8],
            "evidence_snippets": [compact_summary(text, 900) for text in evidence[:4]],
            "source_span_ids": [
                span_id
                for item in members
                for span_id in (item.get("source_span_ids") or [])
                if span_id
            ][:8],
        }
        out.append(cluster)
    return out

def _event_ordering_cluster_sequence_items(query: str, clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0 or len(clusters) != requested:
        return []
    items: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        labels = [str(label or "").strip() for label in cluster.get("candidate_labels") or [] if str(label or "").strip()]
        snippets = [str(text or "").strip() for text in cluster.get("evidence_snippets") or [] if str(text or "").strip()]
        label = _event_ordering_cluster_label(labels, snippets)
        if not label:
            return []
        if _event_ordering_low_information_text(label) or _event_ordering_shell_like_label(label):
            fallback = _event_ordering_cluster_fallback_label(labels, snippets)
            if fallback:
                label = fallback
        if _event_ordering_low_information_text(label) or _event_ordering_shell_like_label(label):
            return []
        item: dict[str, Any] = {
            "sequence_index": index,
            "label": label,
            "context": compact_summary(" ".join(snippets or labels), 260),
            "timeline_index": cluster.get("timeline_start"),
            "timeline_start": cluster.get("timeline_start"),
            "timeline_end": cluster.get("timeline_end"),
        }
        source_span_ids = [str(span_id) for span_id in cluster.get("source_span_ids") or [] if span_id]
        if source_span_ids:
            item["source_span_ids"] = source_span_ids[:6]
        items.append(item)
    return items

def _event_ordering_structured_sequence_items(
    query: str,
    raw_source_spans: list[dict[str, Any]],
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
    phase_clusters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    builders = [
        ("milestone", lambda: _event_ordering_milestone_sequence_items(query, raw_source_spans, anchor_timeline)),
        ("typed_aspect", lambda: _event_ordering_typed_aspect_sequence_items(query, source_spans, anchor_timeline)),
        ("query_scoped_phase", lambda: _event_ordering_query_scoped_phase_sequence_items(query, anchor_timeline)),
        ("anchor_exact", lambda: _event_ordering_anchor_sequence_items(query, anchor_timeline)),
        ("cluster", lambda: _event_ordering_cluster_sequence_items(query, phase_clusters)),
        ("generic", lambda: _event_ordering_sequence_items(query, source_spans, anchor_timeline)),
    ]
    for source, builder in builders:
        items = builder()
        if items:
            return items, source
    return [], ""

def _event_ordering_choose_sequence_items(
    query: str,
    structured_items: list[dict[str, Any]],
    raw_items: list[dict[str, Any]],
    *,
    sequence_source: str = "",
    anchor_timeline: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0:
        return structured_items or raw_items
    if not _event_ordering_sequence_items_are_high_confidence(
        query,
        structured_items,
        raw_items,
        sequence_source=sequence_source,
        anchor_timeline=anchor_timeline or [],
    ):
        return []
    if not structured_items:
        return raw_items
    if not raw_items:
        return structured_items
    if len(structured_items) != requested and len(raw_items) == requested:
        return raw_items
    if len(structured_items) != requested:
        return structured_items
    if len(raw_items) != requested:
        return structured_items
    if _event_ordering_sequence_needs_raw_fallback(query, structured_items, raw_items):
        return raw_items
    return structured_items

def _event_ordering_sequence_items_are_high_confidence(
    query: str,
    structured_items: list[dict[str, Any]],
    raw_items: list[dict[str, Any]],
    *,
    sequence_source: str,
    anchor_timeline: list[dict[str, Any]],
) -> bool:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0:
        return bool(structured_items or raw_items)
    if sequence_source in {"milestone", "typed_aspect"} and len(structured_items) == requested:
        return True
    if len(anchor_timeline) == requested and len(structured_items) == requested:
        return True
    if _event_ordering_component_scope_query(query) and len(structured_items) == requested:
        return True
    if _event_ordering_component_scope_query(query) and len(raw_items) == requested:
        return True
    return False

def _event_ordering_sequence_needs_raw_fallback(
    query: str,
    structured_items: list[dict[str, Any]],
    raw_items: list[dict[str, Any]],
) -> bool:
    structured_indices = [_safe_int(item.get("timeline_index")) for item in structured_items if item.get("timeline_index") is not None]
    if structured_indices:
        if structured_indices != sorted(structured_indices):
            return True
        if len(set(structured_indices)) < len(structured_indices):
            return True
    raw_indices = [_safe_int(item.get("timeline_index")) for item in raw_items if item.get("timeline_index") is not None]
    if raw_indices and raw_indices != sorted(raw_indices):
        return False
    structured_bad = sum(_event_ordering_sequence_item_drift_score(query, item) for item in structured_items)
    raw_bad = sum(_event_ordering_sequence_item_drift_score(query, item) for item in raw_items)
    if structured_bad >= max(1, raw_bad + 1):
        return True
    structured_labels = [_event_ordering_label_key(str(item.get("label") or "")) for item in structured_items]
    if len([label for label in structured_labels if label]) != len(set(label for label in structured_labels if label)):
        return True
    raw_broad = sum(1 for item in raw_items if item.get("broad_raw_recall") or "broad_raw_recall" in str(item.get("candidate_source") or ""))
    structured_broad = sum(1 for item in structured_items if item.get("broad_raw_recall") or "broad_raw_recall" in str(item.get("candidate_source") or ""))
    if raw_broad and not structured_broad and raw_bad <= structured_bad:
        return True
    return False

def _event_ordering_sequence_item_drift_score(query: str, item: dict[str, Any]) -> int:
    label = str(item.get("label") or "")
    context = str(item.get("context") or item.get("content") or "")
    text = f"{label} {context}"
    drift = 0
    if not label or _event_ordering_low_information_text(label) or _event_ordering_shell_like_label(label):
        drift += 1
    if _event_ordering_bad_extracted_label(label):
        drift += 1
    if _event_ordering_label_drift_from_query(query, text):
        drift += 1
    return drift

def _event_ordering_label_drift_from_query(query: str, text: str) -> bool:
    query_lower = query.lower()
    text_lower = text.lower()
    scoped_patterns = [
        (
            r"\b(?:hiring|candidate|screening|recruitment)\b",
            r"\b(?:hiring|candidate|screening|recruitment|interview|vendor|tool|platform|algorithm|bias|fairness|transparency|resume|onboarding)\b",
        ),
        (
            r"\b(?:resume|profile|portfolio|linkedin|cv|professional profile)\b",
            r"\b(?:resume|profile|portfolio|linkedin|cv|professional|interview|job|ATS|certification|course|skills?|career)\b",
        ),
        (
            r"\b(?:framework|bootstrap|customiz|integrat)\b",
            r"\b(?:framework|bootstrap|customiz|integrat|css|javascript|html|deployment|test|seo|accessibility|bundle|component)\b",
        ),
    ]
    for query_pattern, allowed_pattern in scoped_patterns:
        if re.search(query_pattern, query_lower) and not re.search(allowed_pattern, text_lower, flags=re.I):
            return True
    return False

def _event_ordering_raw_chronology_sequence_items(
    query: str,
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0:
        return []
    records = _event_ordering_search_records(source_spans, anchor_timeline)
    records = [
        record
        for record in _dedupe_event_ordering_records(records)
        if str(record.get("speaker") or "").lower() in {"user", "document"}
        and not _event_ordering_assistant_plan_text(str(record.get("conversation_content") or record.get("text") or ""))
        and not _event_ordering_low_information_record(record)
        and not _event_ordering_non_event_or_negated_record(str(record.get("conversation_content") or record.get("text") or ""))
    ]
    if not records:
        return []
    records.sort(key=_event_ordering_raw_chronology_sort_key)
    scope_terms = _event_ordering_typed_scope_terms(query)
    if scope_terms:
        scoped = [
            record
            for record in records
            if _event_ordering_record_matches_query_scope(
                query,
                record,
                _event_ordering_sequence_label(record),
                scope_terms,
            )
        ]
        if len(scoped) >= max(2, min(requested, len(records))):
            records = scoped
    candidates: list[dict[str, Any]] = []
    for record in records:
        label = _event_ordering_compact_aspect_label(_event_ordering_sequence_label(record), str(record.get("conversation_content") or record.get("text") or ""))
        if not label:
            label = _event_ordering_aspect_hint_label(
                str(record.get("label") or record.get("timeline_label") or ""),
                str(record.get("conversation_content") or record.get("text") or ""),
            )
        if not label:
            label = _event_ordering_nominal_event_label(
                str(record.get("label") or record.get("timeline_label") or record.get("conversation_content") or record.get("text") or ""),
                context=str(record.get("conversation_content") or record.get("text") or ""),
            )
        if not label:
            label = _short_event_ordering_theme(
                str(record.get("timeline_label") or record.get("label") or record.get("conversation_content") or record.get("text") or "")
            )
        if not label:
            continue
        if _event_ordering_low_information_text(label) or _event_ordering_shell_like_label(label) or _event_ordering_bad_extracted_label(label):
            continue
        quality = _event_ordering_sequence_quality(
            record,
            _event_ordering_terms(query),
            anchor_terms=_event_ordering_anchor_terms(query),
            episode_terms=_event_ordering_episode_terms(records, _event_ordering_anchor_terms(query)),
            component_scope=_event_ordering_component_scope_query(query),
        )
        if record.get("broad_raw_recall") or "broad_raw_recall" in str(record.get("candidate_source") or ""):
            quality += 0.10
        if _event_ordering_label_drift_from_query(query, f"{label} {record.get('text') or ''}"):
            quality -= 0.45
        if quality < 0.18:
            continue
        candidate = {
            "record": record,
            "label": label,
            "quality": quality,
            "sort_key": _event_ordering_sequence_output_sort_key(record),
        }
        candidates.append(candidate)
    candidates = _event_ordering_dedupe_raw_chronology_candidates(candidates)
    if len(candidates) < requested:
        return []
    selected = _event_ordering_select_referenceable_chronology_candidates(query, candidates, requested)
    if len(selected) < requested:
        return []
    selected.sort(key=lambda item: item["sort_key"])
    items: list[dict[str, Any]] = []
    for item in selected[:requested]:
        record = item["record"]
        out: dict[str, Any] = {
            "sequence_index": len(items) + 1,
            "label": item["label"],
            "context": compact_summary(str(record.get("conversation_content") or record.get("text") or ""), 300),
            "timeline_index": record.get("timeline_index"),
            "selector": "raw_chronology",
        }
        for key in ("source_span_id", "candidate_source", "broad_raw_recall", "recall_query"):
            if record.get(key):
                out[key] = record[key]
        items.append(out)
    return items if len(items) == requested else []

def _event_ordering_select_referenceable_chronology_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    requested: int,
) -> list[dict[str, Any]]:
    if len(candidates) <= requested:
        return candidates
    query_facets = _event_ordering_query_facet_terms(query)
    if not query_facets:
        return _event_ordering_select_raw_chronology_candidates(candidates, requested)
    scored = [
        {
            **candidate,
            "_referenceable_score": _event_ordering_referenceable_candidate_score(query, candidate, query_facets),
            "_facet_hits": _event_ordering_candidate_facet_hits(candidate, query_facets),
        }
        for candidate in candidates
    ]
    selected: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    covered_facets: set[str] = set()
    ordered = sorted(scored, key=lambda item: item["sort_key"])
    total = len(ordered)

    for bucket in range(requested):
        if len(selected) >= requested:
            break
        start = round(bucket * total / requested)
        end = round((bucket + 1) * total / requested)
        if end <= start:
            end = min(total, start + 1)
        window = [item for item in ordered[start:end] if id(item) not in seen_ids]
        if not window:
            continue
        choice = max(
            window,
            key=lambda item: (
                float(item["_referenceable_score"]) + 0.08 * len(set(item["_facet_hits"]) - covered_facets),
                float(item.get("quality") or 0.0),
                -len(str(item.get("label") or "").split()),
            ),
        )
        selected.append(choice)
        seen_ids.add(id(choice))
        covered_facets.update(choice["_facet_hits"])

    if len(selected) < requested:
        for item in sorted(
            scored,
            key=lambda value: (
                len(set(value["_facet_hits"]) - covered_facets),
                float(value["_referenceable_score"]),
                float(value.get("quality") or 0.0),
            ),
            reverse=True,
        ):
            if id(item) in seen_ids:
                continue
            selected.append(item)
            seen_ids.add(id(item))
            covered_facets.update(item["_facet_hits"])
            if len(selected) >= requested:
                break

    selected = selected[:requested]
    selected.sort(key=lambda item: item["sort_key"])
    return selected

def _event_ordering_referenceable_candidate_score(
    query: str,
    candidate: dict[str, Any],
    query_facets: set[str],
) -> float:
    record = candidate["record"]
    label = _event_ordering_reference_preserving_label(str(candidate.get("label") or ""), record)
    if label:
        candidate["label"] = label
    text = " ".join(
        str(value or "")
        for value in [
            candidate.get("label"),
            record.get("label"),
            record.get("timeline_label"),
            record.get("conversation_content"),
            record.get("text"),
        ]
    )
    label_terms = _event_ordering_terms(str(candidate.get("label") or ""))
    text_terms = _event_ordering_terms(text)
    expanded_terms = _event_ordering_expand_facet_terms(label_terms | text_terms)
    facet_hits = expanded_terms & query_facets
    score = float(candidate.get("quality") or 0.0)
    score += min(0.36, 0.09 * len(facet_hits))
    score += min(0.20, 0.04 * len(label_terms - _EVENT_ORDERING_SEQUENCE_STOPWORDS - _EVENT_ORDERING_TOPIC_WORDS))
    if label_terms & query_facets:
        score += 0.12
    if re.search(
        r"\b(?:started|started using|using|used|implemented|configured|fixed|debugged|planned|planning|decided|chose|selected|compared|reviewed|updated|improved|tracking|practiced|met|shared|recommended|suggested)\b",
        text,
        flags=re.I,
    ):
        score += 0.12
    if re.search(r"\b[A-Z][A-Za-z0-9.+#&-]{2,}\b|\$[\d,]+|\d+(?:\.\d+)?%?\b", text):
        score += 0.08
    if record.get("broad_raw_recall") or "broad_raw_recall" in str(record.get("candidate_source") or ""):
        score -= 0.14
    if _event_ordering_low_information_text(str(candidate.get("label") or "")) or _event_ordering_shell_like_label(str(candidate.get("label") or "")):
        score -= 0.40
    if _event_ordering_bad_extracted_label(str(candidate.get("label") or "")):
        score -= 0.28
    return score

def _event_ordering_candidate_facet_hits(candidate: dict[str, Any], query_facets: set[str]) -> set[str]:
    record = candidate["record"]
    text = " ".join(
        str(value or "")
        for value in [
            candidate.get("label"),
            record.get("label"),
            record.get("timeline_label"),
            record.get("conversation_content"),
            record.get("text"),
        ]
    )
    return _event_ordering_expand_facet_terms(_event_ordering_terms(text)) & query_facets

def _event_ordering_query_facet_terms(query: str) -> set[str]:
    terms = _event_ordering_terms(query)
    terms -= _EVENT_ORDERING_SEQUENCE_STOPWORDS
    terms -= _EVENT_ORDERING_TOPIC_WORDS
    terms -= {
        "related",
        "concepts",
        "details",
        "plans",
        "ways",
        "using",
        "used",
        "handling",
        "across",
        "through",
        "progress",
        "professional",
    }
    return _event_ordering_expand_facet_terms({term for term in terms if len(term) >= 3})

def _event_ordering_expand_facet_terms(terms: set[str]) -> set[str]:
    expanded = set(terms)
    for term in list(terms):
        if len(term) > 5 and term.endswith("ing"):
            stem = term[:-3]
            expanded.add(stem)
            expanded.add(stem + "e")
        if len(term) > 4 and term.endswith("ed"):
            stem = term[:-2]
            expanded.add(stem)
            expanded.add(stem + "e")
        if len(term) > 4 and term.endswith("e"):
            expanded.add(term[:-1])
        if term.endswith("iz") or term.endswith("is"):
            expanded.add(term + "e")
        if term.endswith("izing"):
            expanded.add(term[:-5])
            expanded.add(term[:-3])
        if term.endswith("ization"):
            expanded.add(term[:-7])
            expanded.add(term[:-5])
    return {term for term in expanded if len(term) >= 3}

def _event_ordering_reference_preserving_label(label: str, record: dict[str, Any]) -> str:
    if label and not _event_ordering_low_information_text(label) and not _event_ordering_shell_like_label(label) and not _event_ordering_bad_extracted_label(label):
        if len(_event_ordering_terms(label) - _EVENT_ORDERING_SEQUENCE_STOPWORDS - _EVENT_ORDERING_TOPIC_WORDS) >= 3:
            return label
    text = str(record.get("conversation_content") or record.get("text") or "")
    terms = [
        term
        for term in _event_ordering_terms_ordered(text)
        if term not in _EVENT_ORDERING_SEQUENCE_STOPWORDS
        and term not in _EVENT_ORDERING_TOPIC_WORDS
        and term not in {"because", "since", "would", "really", "think", "help"}
    ]
    if not terms:
        return label
    chosen: list[str] = []
    for term in terms:
        if term.endswith("ing") or term in {"stress", "budget", "savings", "investment", "resume", "profile", "css", "api", "error", "testing", "planning", "feedback", "deadline", "meeting"}:
            chosen.append(term)
        elif len(chosen) < 2:
            chosen.append(term)
        if len(chosen) >= 5:
            break
    fallback = " ".join(chosen[:5]).strip()
    return _event_ordering_preserve_acronyms(fallback) if fallback else label

def _event_ordering_dedupe_raw_chronology_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    seen_spans: set[str] = set()
    for item in sorted(candidates, key=lambda value: (value["sort_key"], -float(value["quality"]))):
        record = item["record"]
        span_id = str(record.get("source_span_id") or "")
        label_key = _event_ordering_label_key(str(item.get("label") or ""))
        if span_id and span_id in seen_spans:
            continue
        if label_key and (label_key in seen_labels or _event_ordering_label_overlaps_seen(label_key, seen_labels)):
            continue
        out.append(item)
        if span_id:
            seen_spans.add(span_id)
        if label_key:
            seen_labels.add(label_key)
    return out

def _event_ordering_select_raw_chronology_candidates(candidates: list[dict[str, Any]], requested: int) -> list[dict[str, Any]]:
    if len(candidates) <= requested:
        return candidates
    ordered = sorted(candidates, key=lambda item: item["sort_key"])
    selected: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for bucket in range(requested):
        start = round(bucket * len(ordered) / requested)
        end = round((bucket + 1) * len(ordered) / requested)
        if end <= start:
            end = min(len(ordered), start + 1)
        window = [item for item in ordered[start:end] if id(item) not in seen_ids]
        if not window:
            continue
        choice = max(window, key=lambda item: (float(item["quality"]), -len(str(item["label"]).split())))
        selected.append(choice)
        seen_ids.add(id(choice))
    if len(selected) < requested:
        for item in sorted(ordered, key=lambda value: float(value["quality"]), reverse=True):
            if id(item) in seen_ids:
                continue
            selected.append(item)
            seen_ids.add(id(item))
            if len(selected) >= requested:
                break
    return selected[:requested]

def _event_ordering_raw_chronology_sort_key(record: dict[str, Any]) -> tuple[int, tuple[tuple[int, int | str], ...], tuple[tuple[int, int | str], ...], str]:
    timeline_index = _safe_int(record.get("timeline_index") or record.get("history_index"))
    return (
        timeline_index if timeline_index > 0 else 10**9,
        _event_ordering_natural_key(record.get("source_uri")),
        _event_ordering_natural_key(record.get("turn_id")),
        str(record.get("source_span_id") or ""),
    )

def _event_ordering_sequence_items(
    query: str,
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0:
        return []

    records = _event_ordering_search_records(source_spans, anchor_timeline)
    records = _dedupe_event_ordering_records(records)
    records.sort(key=_event_ordering_record_sort_key)
    records = _attach_following_event_ordering_support(records)
    records = [
        record
        for record in records
        if str(record.get("speaker") or "").lower() not in {"assistant", "agent"}
        and not _event_ordering_assistant_plan_text(str(record.get("text") or ""))
        and not _event_ordering_low_information_record(record)
    ]
    coverage_records = [
        record
        for record in records
        if record.get("selector") == "event_ordering_coverage"
        and record.get("timeline_role") in {"user_aspect_anchor", "user_introduced_topic"}
    ]
    if len(coverage_records) >= requested:
        expansion_records = [
            record
            for record in records
            if record not in coverage_records
            and str(record.get("speaker") or "").lower() in {"user", "document"}
            and _event_ordering_sequence_quality(
                record,
                _event_ordering_terms(query),
                anchor_terms=_event_ordering_anchor_terms(query),
                episode_terms=_event_ordering_episode_terms(records, _event_ordering_anchor_terms(query)),
                component_scope=_event_ordering_component_scope_query(query),
            )
            >= 0.42
        ]
        if expansion_records:
            records = sorted(coverage_records + expansion_records, key=_event_ordering_record_sort_key)
        else:
            records = coverage_records
    non_standing_records = [record for record in records if not _event_ordering_standing_preference_record(record)]
    if len(non_standing_records) >= requested and not _event_ordering_preference_sequence_query(query):
        records = non_standing_records
    if len(records) < max(2, min(requested, 3)):
        return []

    representatives = _event_ordering_representatives(query, records, requested)
    if len(representatives) < 2:
        return []
    if len(representatives) < requested:
        used_keys = {_event_ordering_label_key(_event_ordering_sequence_label(record)) for record in representatives}
        used_span_ids = {str(record.get("source_span_id") or "") for record in representatives}
        fallback = [
            record
            for record in records
            if str(record.get("source_span_id") or "") not in used_span_ids
            and _event_ordering_label_key(_event_ordering_sequence_label(record)) not in used_keys
        ]
        fallback.sort(
            key=lambda record: (
                _event_ordering_sequence_quality(
                    record,
                    _event_ordering_terms(query),
                    anchor_terms=_event_ordering_anchor_terms(query),
                    episode_terms=_event_ordering_episode_terms(records, _event_ordering_anchor_terms(query)),
                    component_scope=_event_ordering_component_scope_query(query),
                ),
                _event_ordering_record_sort_key(record),
            ),
            reverse=True,
        )
        for record in fallback:
            representatives.append(record)
            used_keys.add(_event_ordering_label_key(_event_ordering_sequence_label(record)))
            used_span_ids.add(str(record.get("source_span_id") or ""))
            if len(representatives) >= requested:
                break
        representatives.sort(key=_event_ordering_sequence_output_sort_key)

    representatives.sort(key=_event_ordering_sequence_output_sort_key)
    items: list[dict[str, Any]] = []
    used_span_ids: set[str] = set()
    seen_label_keys: set[str] = set()

    def add_sequence_item(match: dict[str, Any]) -> None:
        if len(items) >= requested:
            return
        label = _event_ordering_compact_aspect_label(
            _event_ordering_sequence_label(match),
            str(match.get("text") or ""),
        )
        if not label:
            label = _event_ordering_aspect_hint_label(
                str(match.get("label") or ""),
                str(match.get("conversation_content") or match.get("text") or ""),
            )
        if not label:
            return
        label_key = _event_ordering_label_key(label)
        span_id = str(match.get("source_span_id") or "")
        if span_id and span_id in used_span_ids:
            return
        if label_key and (label_key in seen_label_keys or _event_ordering_label_overlaps_seen(label_key, seen_label_keys)):
            return
        item = {
            "sequence_index": len(items) + 1,
            "label": label,
            "context": compact_summary(match["text"], 260),
        }
        if match.get("source_span_id"):
            item["source_span_id"] = match["source_span_id"]
        if match.get("timeline_index") is not None:
            item["timeline_index"] = match["timeline_index"]
        items.append(item)
        if span_id:
            used_span_ids.add(span_id)
        if label_key:
            seen_label_keys.add(label_key)

    for match in representatives:
        add_sequence_item(match)

    if len(items) < requested:
        representative_ids = {id(record) for record in representatives}
        fallback_records = [record for record in records if id(record) not in representative_ids]
        fallback_records.sort(
            key=lambda record: (
                _event_ordering_sequence_quality(
                    record,
                    _event_ordering_terms(query),
                    anchor_terms=_event_ordering_anchor_terms(query),
                    episode_terms=_event_ordering_episode_terms(records, _event_ordering_anchor_terms(query)),
                    component_scope=_event_ordering_component_scope_query(query),
                ),
                _event_ordering_record_sort_key(record),
            ),
            reverse=True,
        )
        for record in sorted(fallback_records[: max(requested * 3, 12)], key=_event_ordering_sequence_output_sort_key):
            add_sequence_item(record)
            if len(items) >= requested:
                break
    return items[:requested]
