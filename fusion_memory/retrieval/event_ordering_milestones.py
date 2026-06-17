from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.ingestion.extractors import extract_milestone_mentions
from fusion_memory.retrieval.event_ordering_common import _requested_event_ordering_count
from fusion_memory.retrieval.event_ordering_labels import (
    _EVENT_ORDERING_LIFECYCLE_MILESTONE_ORDER,
    _EVENT_ORDERING_MILESTONE_LABELS,
    _event_ordering_assistant_plan_text,
    _event_ordering_terms,
)

def _event_ordering_milestone_sequence_items(
    query: str,
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0:
        return []
    if not _event_ordering_project_timeline_query(query):
        return []
    candidates = _event_ordering_milestone_candidates(query, source_spans, anchor_timeline)
    if len(candidates) < requested:
        return []
    selected = _event_ordering_select_milestones(query, candidates, requested)
    if len(selected) < requested:
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        label = _EVENT_ORDERING_MILESTONE_LABELS.get(str(item.get("milestone_group") or ""), str(item.get("milestone_group") or "").replace("_", " "))
        record: dict[str, Any] = {
            "sequence_index": index,
            "label": label,
            "context": compact_summary(str(item.get("context") or ""), 260),
            "timeline_index": item.get("timeline_index"),
            "milestone_group": item.get("milestone_group"),
        }
        if item.get("source_span_id"):
            record["source_span_id"] = item["source_span_id"]
        out.append(record)
    return out

def _event_ordering_select_milestones(
    query: str,
    candidates: list[dict[str, Any]],
    requested: int,
) -> list[dict[str, Any]]:
    sorted_candidates = sorted(
        candidates,
        key=lambda item: (
            int(item.get("timeline_index") or item.get("history_index") or 10**9),
            str(item.get("source_span_id") or ""),
        ),
    )
    first_by_group: dict[str, dict[str, Any]] = {}
    for item in sorted_candidates:
        group = str(item.get("milestone_group") or "")
        if group and group not in first_by_group:
            first_by_group[group] = item

    selected: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    if _event_ordering_lifecycle_milestone_query(query):
        for group in _EVENT_ORDERING_LIFECYCLE_MILESTONE_ORDER:
            item = _event_ordering_first_diverse_milestone_candidate(
                sorted_candidates,
                group,
                selected,
            ) or first_by_group.get(group)
            if item is None:
                continue
            selected.append(item)
            seen_groups.add(group)
            if len(selected) >= requested:
                break

    for item in sorted_candidates:
        if len(selected) >= requested:
            break
        group = str(item.get("milestone_group") or "")
        if not group or group in seen_groups:
            continue
        selected.append(item)
        seen_groups.add(group)
    return _event_ordering_diversify_milestone_selection(selected, sorted_candidates, requested)

def _event_ordering_first_diverse_milestone_candidate(
    candidates: list[dict[str, Any]],
    group: str,
    selected: list[dict[str, Any]],
) -> dict[str, Any] | None:
    selected_keys = {_event_ordering_milestone_source_key(item) for item in selected}
    for item in candidates:
        if str(item.get("milestone_group") or "") != group:
            continue
        key = _event_ordering_milestone_source_key(item)
        if key and key not in selected_keys:
            return item
    return None

def _event_ordering_diversify_milestone_selection(
    selected: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    requested: int,
) -> list[dict[str, Any]]:
    if len(selected) < requested:
        return selected
    chosen = list(selected[:requested])
    chosen_ids = {id(item) for item in chosen}
    chosen_groups = {str(item.get("milestone_group") or "") for item in chosen if item.get("milestone_group")}

    def key_counts(items: list[dict[str, Any]]) -> dict[tuple[Any, ...], int]:
        counts: dict[tuple[Any, ...], int] = {}
        for item in items:
            key = _event_ordering_milestone_source_key(item)
            if key:
                counts[key] = counts.get(key, 0) + 1
        return counts

    counts = key_counts(chosen)
    for index, item in enumerate(list(chosen)):
        key = _event_ordering_milestone_source_key(item)
        if not key or counts.get(key, 0) <= 1:
            continue
        replacement = next(
            (
                candidate
                for candidate in candidates
                if id(candidate) not in chosen_ids
                and str(candidate.get("milestone_group") or "") not in chosen_groups
                and _event_ordering_milestone_source_key(candidate)
                and counts.get(_event_ordering_milestone_source_key(candidate), 0) == 0
            ),
            None,
        )
        if replacement is None:
            continue
        chosen[index] = replacement
        chosen_ids.add(id(replacement))
        chosen_groups.add(str(replacement.get("milestone_group") or ""))
        counts = key_counts(chosen)

    chosen.sort(
        key=lambda item: (
            int(item.get("timeline_index") or item.get("history_index") or 10**9),
            str(item.get("source_span_id") or ""),
            str(item.get("milestone_group") or ""),
        )
    )
    return chosen[:requested]

def _event_ordering_milestone_source_key(item: dict[str, Any]) -> tuple[Any, ...]:
    source_span_id = str(item.get("source_span_id") or "")
    if source_span_id:
        return ("span", source_span_id)
    timeline_index = item.get("timeline_index") or item.get("history_index")
    if timeline_index is not None:
        return ("timeline", int(timeline_index))
    return ()

def _event_ordering_lifecycle_milestone_query(query: str) -> bool:
    lower = query.lower()
    if re.search(r"\b(?:project|app|application|website|development|developing|implementation|deployment|deploy)\b", lower):
        return True
    return bool(re.search(r"\b(?:mvp|feature|testing|test coverage|sprint|code)\b", lower))

def _event_ordering_project_timeline_query(query: str) -> bool:
    lower = query.lower()
    return bool(
        re.search(
            r"\b(?:project|app|application|website|development|developing|deployment|deploy|implementation|"
            r"framework|feature|testing|test coverage|mvp|sprint|code)\b",
            lower,
        )
    )

def _event_ordering_milestone_candidates(
    query: str,
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    query_terms = _event_ordering_terms(query)

    def add_from_text(text: str, source: dict[str, Any]) -> None:
        speaker = str(source.get("speaker") or "").lower()
        if speaker and speaker not in {"user", "document"}:
            return
        if _event_ordering_assistant_plan_text(text):
            return
        mentions = extract_milestone_mentions(text)
        for group, snippet in mentions:
            if not _event_ordering_milestone_matches_query(group, snippet, query_terms):
                continue
            source_span_id = str(source.get("original_span_id") or source.get("id") or source.get("source_span_id") or "")
            key = (group, source_span_id or compact_summary(snippet, 80).lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "milestone_group": group,
                    "context": snippet,
                    "source_span_id": source_span_id or None,
                    "timeline_index": source.get("timeline_index"),
                    "history_index": source.get("history_index"),
                    "speaker": source.get("speaker"),
                }
            )

    for span in source_spans[:96]:
        add_from_text(str(span.get("conversation_content") or span.get("content") or ""), span)
    if len(rows) < 8:
        for anchor in anchor_timeline:
            add_from_text(str(anchor.get("conversation_content") or anchor.get("content") or anchor.get("label") or ""), anchor)
    return rows

def _event_ordering_milestone_matches_query(group: str, snippet: str, query_terms: set[str]) -> bool:
    if not query_terms:
        return True
    group_terms = set(group.split("_"))
    snippet_terms = _event_ordering_terms(snippet)
    project_terms = {
        "app",
        "application",
        "code",
        "deploy",
        "deployment",
        "develop",
        "development",
        "feature",
        "framework",
        "implementation",
        "mvp",
        "project",
        "testing",
        "website",
    }
    return bool((group_terms | snippet_terms | project_terms) & query_terms)
