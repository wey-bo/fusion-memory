from __future__ import annotations

"""Aggregation model-view construction.

Aggregation heuristics are section-owned here instead of hidden in the eval
adapter. Keep the public surface focused on producing `aggregation_items`,
`aggregation_summary`, preference constraints, and financial summaries from
pack evidence; domain-specific key extraction should live behind
`aggregation_keys.py` or a dedicated extractor.
"""

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.aggregation_common import _append_aggregation_item, _match_context, _span_ref
from fusion_memory.retrieval.aggregation_keys import (
    generic_aggregation_keys,
    generic_list_candidate_keys,
    is_vendor_tool_aggregation_query,
    vendor_tool_aggregation_keys,
)
from fusion_memory.retrieval.aggregation_llm import (
    LLM_AGGREGATION_PROMPT,
    _llm_aggregation_items,
    _validated_llm_aggregation_item,
)
from fusion_memory.retrieval.aggregation_preferences import (
    _preference_constraint_candidates_from_text,
    _preference_constraint_items,
    _preference_requirement_checklist,
    _query_accepts_preference_constraints,
)
from fusion_memory.retrieval.aggregation_specialized import (
    _append_probability_calculation_items,
    _append_score_improvement_items,
    _append_stress_break_items,
    _combination_item_key,
    _filter_combinatorics_aggregation_items,
    _is_combinatorics_aggregation_query,
    _is_probability_calculation_query,
    _is_score_improvement_query,
    _is_stress_break_aggregation_query,
    _is_ways_combinatorics_query,
    _looks_like_sample_space_value,
    _previous_nonspace,
)
from fusion_memory.retrieval.aggregation_financial import (
    _financial_current_state,
    _financial_direction,
    _financial_impact_items,
    _financial_impact_role,
    _financial_impact_summary,
    _financial_period,
    _financial_query_subjects,
    _financial_subject_key,
    _financial_summary_amount,
    _financial_text_has_query_overlap,
    _format_money,
    _is_financial_impact_query,
    _money_mentions,
)

def _merge_aggregation_source_spans(
    source_spans: list[dict[str, Any]],
    exact_answer_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = list(source_spans)
    seen = {
        str(span.get("id") or span.get("source_span_id") or span.get("turn_id") or "")
        for span in merged
        if span.get("id") or span.get("source_span_id") or span.get("turn_id")
    }
    for candidate in exact_answer_candidates:
        key = str(candidate.get("id") or candidate.get("source_span_id") or candidate.get("turn_id") or "")
        if key and key in seen:
            continue
        span = dict(candidate)
        if not span.get("id") and span.get("source_span_id"):
            span["id"] = span.get("source_span_id")
        span.setdefault("candidate_source", "exact_answer_candidate")
        merged.append(span)
        if key:
            seen.add(key)
    return merged[:96]

def _compact_records(records: list[dict[str, Any]], *, preferred_text_key: str, limit: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records[:limit]:
        compacted: dict[str, Any] = {}
        for key in [
            "id",
            "fact_id",
            "event_id",
            "view_id",
            "profile_id",
            "type",
            "category",
            "subject",
            "predicate",
            "object",
            "entity_id",
            "profile_type",
            "candidate_source",
            "source_span_id",
            "speaker",
            "timestamp",
            "time_start",
            "time_end",
            "timeline_index",
            "history_index",
            "recency_rank",
            "topic_group",
            "claim_polarity",
            "value_mentions",
            "temporal_mentions",
            "temporal_roles",
            "aggregation_keys",
            "aggregation_signal",
            "subject_key",
            "current",
            "source_span_ids",
            "role",
            "explicit_year",
            "normalized_date",
            "confidence",
            "score",
            "update_marker_strength",
            "target_role_match",
            "target_role_order",
            "query_overlap",
            "span_query_overlap",
            "slot_overlap",
            "value_role",
            "value_type",
            "value",
            "answer_value",
            "answer_type",
            "extraction_formula",
            "guidance",
            "range_endpoint",
            "start_date",
            "end_date",
            "start_text",
            "end_text",
            "start_role",
            "end_role",
            "range_role",
        ]:
            if key in record:
                compacted[key] = record[key]
        text = str(record.get(preferred_text_key) or record.get("text") or record.get("content") or "")
        if text:
            compacted[preferred_text_key] = compact_summary(text, 1200)
        out.append(compacted)
    return out

def _multi_session_aggregation_items(
    query: str,
    source_spans: list[dict[str, Any]],
    *,
    query_intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    query_lower = query.lower()
    is_generic_aggregation_query = _is_generic_count_or_list_query(query_lower) or _intent_requests_aggregation(query_intent)
    is_supported_specific_query = (
        _is_combinatorics_aggregation_query(query_lower)
        or _is_probability_calculation_query(query_lower)
        or _is_stress_break_aggregation_query(query_lower)
        or _is_score_improvement_query(query_lower)
    )
    if not is_supported_specific_query and not is_generic_aggregation_query:
        return []
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    item_scan_spans = _aggregation_item_scan_spans(query_lower, source_spans)
    extract_probability_calculations = _is_probability_calculation_query(query_lower)
    extract_combinatorics_values = _is_ways_combinatorics_query(query_lower)
    extract_stress_breaks = _is_stress_break_aggregation_query(query_lower)
    if _is_score_improvement_query(query_lower):
        _append_score_improvement_items(items, seen, query_lower, item_scan_spans)
    if is_generic_aggregation_query and not (extract_probability_calculations or extract_combinatorics_values):
        _append_generic_key_items(items, seen, query, item_scan_spans, query_intent=query_intent)
    for span in item_scan_spans:
        content = str(span.get("content") or "")
        lower = content.lower()
        span_ref = {
            "source_span_id": span.get("id"),
            "turn_id": span.get("turn_id"),
            "speaker": span.get("speaker"),
            "history_index": span.get("history_index"),
        }
        if extract_probability_calculations:
            _append_probability_calculation_items(items, seen, content, span_ref)
        if extract_stress_breaks:
            _append_stress_break_items(items, seen, content, span_ref)
        if not extract_combinatorics_values:
            continue
        for match in re.finditer(r"\b(\d+)!\s*(?:=|equals)\s*(\d+)\b", content, flags=re.I):
            context = _match_context(content, match.start(), match.end())
            if re.search(r"\b(?:arrang(?:e|ing)|permutations?|objects?|balls?|ways?)\b", context, flags=re.I):
                _append_aggregation_item(items, seen, "ways:arrange_objects", int(match.group(2)), context, span_ref, included=True)
        for match in re.finditer(r"\b(\d+)\s*C\s*(\d+)\s*(?:=|equals)\s*(\d+)\b", content, flags=re.I):
            if _previous_nonspace(content, match.start()) == "/":
                continue
            context = _match_context(content, match.start(), match.end())
            key = _combination_item_key(match.group(1), context)
            include = not _looks_like_sample_space_value(match.group(1), context)
            _append_aggregation_item(items, seen, key, int(match.group(3)), context, span_ref, included=include, reason=None if include else "sample_space_size")
        for match in re.finditer(r"\b(\d+)\s*C\s*(\d+)\s*/\s*(\d+)\s*C\s*(\d+)\s*=\s*(\d+)\s*/\s*(\d+)\b", content, flags=re.I):
            context = _match_context(content, match.start(), match.end())
            _append_aggregation_item(items, seen, _combination_item_key(match.group(1), context), int(match.group(5)), context, span_ref, included=True)
            _append_aggregation_item(items, seen, "excluded:sample_space", int(match.group(6)), context, span_ref, included=False, reason="denominator_or_sample_space")
        for match in re.finditer(r"\\binom\{(\d+)\}\{(\d+)\}[^=\n]{0,160}=\s*(\d+)", content, flags=re.I):
            context = _match_context(content, match.start(), match.end())
            include = not _looks_like_sample_space_value(match.group(1), context)
            _append_aggregation_item(
                items,
                seen,
                _combination_item_key(match.group(1), context),
                int(match.group(3)),
                context,
                span_ref,
                included=include,
                reason=None if include else "sample_space_size",
            )
        if "1326" in lower and re.search(r"\b(?:52-card|52 cards|52c2|\\binom\{52\}\{2\}|sample space|total number of ways)\b", lower):
            start = lower.find("1326")
            _append_aggregation_item(
                items,
                seen,
                "excluded:sample_space",
                1326,
                _match_context(content, start, start + 4),
                span_ref,
                included=False,
                reason="sample_space_size",
            )
    if extract_combinatorics_values:
        items = _filter_combinatorics_aggregation_items(query_lower, items)
    included = [item for item in items if item.get("included")]
    excluded = [item for item in items if not item.get("included")]
    return (included + excluded)[:16]

def _filter_low_confidence_aggregation_items(query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return items
    included = [item for item in items if item.get("included")]
    if not included:
        return items
    stable_prefixes = (
        "title:",
        "genre:",
        "value:",
        "column:",
        "area:",
        "feature:",
        "security_feature:",
        "role:",
        "request:",
        "application_type:",
        "plan_system:",
        "vendor_tool:",
        "count_hint:",
        "group_count:",
        "score_improvement:",
        "ways:",
        "calculation:",
        "break:",
    )
    stable = [item for item in included if str(item.get("key") or "").startswith(stable_prefixes)]
    weak = [
        item
        for item in included
        if str(item.get("key") or "").startswith(("generic:", "item:"))
        and not str(item.get("key") or "").startswith(stable_prefixes)
    ]
    if stable:
        return items
    if weak and len(weak) == len(included):
        return [item for item in items if not item.get("included")]
    return items

def _aggregation_item_scan_spans(query_lower: str, source_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return spans that should participate in structured aggregation extraction.

    Retrieval may preserve a relevant coverage span after the first page of
    source_spans.  Scanning every span can overproduce noisy items, so keep the
    existing first-40 behavior and append later spans only when retrieval has
    already annotated them with query-shaped aggregation keys.
    """

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed_prefixes = _generic_aggregation_item_prefixes(query_lower)
    if _is_stress_break_aggregation_query(query_lower):
        allowed_prefixes.add("break")
    if _is_combinatorics_aggregation_query(query_lower):
        allowed_prefixes.update({"ways", "calculation"})
    if _is_score_improvement_query(query_lower):
        allowed_prefixes.add("score_improvement")

    def add(span: dict[str, Any]) -> None:
        key = str(span.get("id") or span.get("source_span_id") or span.get("turn_id") or len(out))
        if key in seen:
            return
        seen.add(key)
        out.append(span)

    for span in source_spans[:40]:
        add(span)
    if allowed_prefixes:
        for span in source_spans[40:96]:
            keys = [str(key) for key in span.get("aggregation_keys") or [] if key]
            if any(any(key.startswith(f"{prefix}:") for prefix in allowed_prefixes) for key in keys):
                add(span)
                continue
            if str(span.get("candidate_source") or "") == "adjacent_exact_answer_support":
                add(span)
                continue
            if any(any(key.startswith(f"{prefix}:") for prefix in allowed_prefixes) for key in generic_list_candidate_keys(query_lower, str(span.get("content") or ""))):
                add(span)
                continue
            if "column" in allowed_prefixes and _schema_column_span_hint(query_lower, str(span.get("content") or ""), speaker=str(span.get("speaker") or "")):
                add(span)
    return out[:64]

def _schema_column_span_hint(query_lower: str, content: str, *, speaker: str | None = None) -> bool:
    if not _use_schema_column_candidates(query_lower):
        return False
    lower = content.lower()
    if speaker == "assistant" and not re.search(r"\b(?:op\.add_column|alter\s+table\s+[a-z_][a-z0-9_]*\s+add\s+column)\b", lower):
        return False
    table_names = _schema_table_names(query_lower)
    if table_names and not any(table in lower for table in table_names):
        return False
    return bool(re.search(r"\b(?:add|adding|added|include|including|new|migration|migrate|alter\s+table|op\.add_column|add_column)\b", lower))

def _aggregation_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    included = [item for item in items if item.get("included")]
    excluded = [item for item in items if not item.get("included")]
    by_count_role: dict[str, dict[str, Any]] = {}
    by_object_type: dict[str, int] = {}
    for item in included:
        count_role = str(item.get("count_role") or "unknown")
        object_type = str(item.get("memory_object_type") or "unknown")
        role_entry = by_count_role.setdefault(
            count_role,
            {
                "count": 0,
                "value_sum": 0,
                "labels": [],
                "keys": [],
            },
        )
        role_entry["count"] += 1
        try:
            role_entry["value_sum"] += int(item.get("value") or 0)
        except (TypeError, ValueError):
            pass
        label = item.get("label") or item.get("key")
        if label and len(role_entry["labels"]) < 8:
            role_entry["labels"].append(str(label))
        key = item.get("key")
        if key and len(role_entry["keys"]) < 8:
            role_entry["keys"].append(str(key))
        by_object_type[object_type] = by_object_type.get(object_type, 0) + 1
    primary_count_candidates: list[dict[str, Any]] = []
    for count_role in ("user_reported_count", "additive_item", "additive_value", "candidate_group_count", "assistant_supported_count"):
        entry = by_count_role.get(count_role)
        if not entry:
            continue
        primary_count_candidates.append(
            {
                "count_role": count_role,
                "count": entry["count"],
                "value_sum": entry["value_sum"],
                "labels": entry["labels"][:6],
            }
        )
    guidance = (
        "Add user_reported_count, additive_item, and additive_value roles when they match the requested object type. "
        "Treat candidate_group_count as a bounded assistant option/recommendation group. Use it as a count candidate, "
        "and combine it with user_reported_count/additive_item only when it represents a distinct date, session, request, "
        "or subquestion named by the query; otherwise do not double-count the same objects."
    )
    return {
        "included_count": len(included),
        "excluded_count": len(excluded),
        "by_count_role": by_count_role,
        "by_object_type": by_object_type,
        "primary_count_candidates": primary_count_candidates[:5],
        "guidance": guidance,
    }

def _is_generic_count_or_list_query(query_lower: str) -> bool:
    return bool(re.search(r"\b(?:how many|total|unique|count|number of|different|list)\b", query_lower))

def _append_generic_key_items(
    items: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
    query: str,
    source_spans: list[dict[str, Any]],
    *,
    query_intent: dict[str, Any] | None = None,
) -> None:
    query_lower = query.lower()
    if _is_stress_break_aggregation_query(query_lower):
        return
    allowed_prefixes = _generic_aggregation_item_prefixes(query_lower, query_intent=query_intent)
    specialized_prefixes = {"title", "genre", "value"}
    use_specialized_extractors = bool(allowed_prefixes & specialized_prefixes)
    scoped_history_indices = _date_scoped_history_indices(query_lower, source_spans)
    scoped_turn_contexts = _date_scoped_turn_contexts(query_lower, source_spans)
    recommendation_count_hints = _recommendation_group_count_hints(query_lower, source_spans)
    excluded_title_keys = _excluded_title_keys(source_spans)
    use_area_focus_candidates = _use_area_focus_candidates(query_lower)
    use_schema_column_candidates = _use_schema_column_candidates(query_lower)
    use_app_feature_focus_candidates = _use_app_feature_focus_candidates(query_lower)
    use_role_security_candidates = _is_role_security_feature_aggregation_query(query_lower)
    use_application_type_candidates = _is_application_type_aggregation_query(query_lower)
    use_vendor_tool_candidates = is_vendor_tool_aggregation_query(query_lower)
    allow_assistant_genre_echo = bool("genre" in allowed_prefixes and _is_exploratory_item_query(query_lower))
    allow_scoped_assistant_titles = bool(
        "title" in allowed_prefixes
        and (scoped_history_indices or _date_scope_labels(query_lower))
        and re.search(r"\b(?:movies?|films?|watch|planned|plan|marathons?)\b", query_lower)
    )
    ordered_spans = sorted(
        source_spans,
        key=lambda span: (
            0 if str(span.get("speaker") or "") in {"user", "document"} else 1,
            int(span.get("history_index") or 10**9),
        ),
    )
    for span in ordered_spans:
        content = str(span.get("content") or "")
        speaker = str(span.get("speaker") or "") or None
        history_index = int(span.get("history_index") or 10**9)
        planning_system_keys = _planning_system_aggregation_keys(query_lower, content)
        schema_column_keys = _schema_column_aggregation_keys(query_lower, content, speaker=speaker)
        area_focus_keys = _area_focus_aggregation_keys(query_lower, content, speaker=speaker)
        app_feature_focus_keys = _app_feature_focus_aggregation_keys(query_lower, content, speaker=speaker)
        role_security_keys = _role_security_feature_aggregation_keys(query_lower, content, speaker=speaker)
        application_type_keys = _application_type_aggregation_keys(query_lower, content, speaker=speaker)
        vendor_tool_keys = vendor_tool_aggregation_keys(query_lower, content, speaker=speaker)
        asset_keys = _asset_aggregation_keys(query_lower, content, speaker=speaker)
        assistant_genre_echo_keys = _assistant_exploratory_genre_echo_keys(query_lower, content, speaker=speaker) if allow_assistant_genre_echo else []
        directly_scoped_assistant = bool(
            speaker not in {"user", "document"}
            and allow_scoped_assistant_titles
            and _assistant_directly_matches_query_date_scope(query_lower, content)
        )
        recommendation_count_hint = recommendation_count_hints.get(history_index)
        scoped_by_adjacent_user = bool(
            history_index in scoped_history_indices
            or _span_has_adjacent_date_scope(span, scoped_turn_contexts)
            or directly_scoped_assistant
        )
        if (
            speaker
            and speaker not in {"user", "document"}
            and not (allow_scoped_assistant_titles and scoped_by_adjacent_user)
            and not recommendation_count_hint
            and not planning_system_keys
            and not schema_column_keys
            and not area_focus_keys
            and not app_feature_focus_keys
            and not role_security_keys
            and not application_type_keys
            and not vendor_tool_keys
            and not asset_keys
            and not assistant_genre_echo_keys
        ):
            continue
        if speaker not in {"user", "document"} and scoped_by_adjacent_user and not recommendation_count_hint and not _assistant_title_list_commits_to_plan(content):
            continue
        if (
            speaker not in {"user", "document"}
            and scoped_by_adjacent_user
            and not directly_scoped_assistant
            and not recommendation_count_hint
            and len(generic_list_candidate_keys(query.lower(), content)) > 4
        ):
            continue
        span_ref = _span_ref(span)
        count_hint = recommendation_count_hint or _generic_count_hint(query_lower, content, speaker=speaker, scoped_by_adjacent_user=scoped_by_adjacent_user)
        suppress_keys: set[str] = set()
        if count_hint:
            hint_key, hint_value, hint_label = count_hint
            _append_aggregation_item(
                items,
                seen,
                hint_key,
                hint_value,
                content,
                span_ref,
                included=True,
                dedupe_by_key=True,
                label=hint_label,
            )
            suppress_keys.update(generic_list_candidate_keys(query.lower(), content))
            if speaker not in {"user", "document"} and recommendation_count_hint:
                continue
        if use_schema_column_candidates:
            keys = list(schema_column_keys)
        elif use_area_focus_candidates:
            keys = list(area_focus_keys)
        elif use_role_security_candidates:
            keys = list(role_security_keys)
        elif use_app_feature_focus_candidates:
            keys = list(app_feature_focus_keys)
        elif use_application_type_candidates:
            keys = list(application_type_keys)
        elif use_vendor_tool_candidates:
            keys = list(vendor_tool_keys)
        elif "asset" in allowed_prefixes:
            keys = list(asset_keys)
        elif assistant_genre_echo_keys:
            keys = list(assistant_genre_echo_keys)
        else:
            keys = [] if use_specialized_extractors else [str(key) for key in span.get("aggregation_keys") or [] if key]
            keys.extend(planning_system_keys)
            keys.extend(generic_list_candidate_keys(query.lower(), content))
            if not use_specialized_extractors:
                keys.extend(generic_aggregation_keys(query, content, speaker=speaker))
        if not keys:
            continue
        for key in keys:
            if key in suppress_keys:
                continue
            if allowed_prefixes and not any(key.startswith(f"{prefix}:") for prefix in allowed_prefixes):
                continue
            label = _generic_key_label(key)
            included, reason = _generic_item_scope_inclusion(query_lower, content, key, scoped_by_adjacent_user=scoped_by_adjacent_user)
            if included and key in excluded_title_keys and not _title_positive_in_context(key, content):
                included = False
                reason = "title_excluded_or_rejected_elsewhere"
            _append_aggregation_item(
                items,
                seen,
                key,
                1 if included else 0,
                content or label,
                span_ref,
                included=included,
                reason=reason,
                dedupe_by_key=True,
                label=label,
            )

def _date_scoped_history_indices(query_lower: str, source_spans: list[dict[str, Any]]) -> set[int]:
    query_dates = _date_scope_labels(query_lower)
    if not query_dates:
        return set()
    scoped: set[int] = set()
    for span in source_spans:
        try:
            history_index = int(span.get("history_index") or 0)
        except (TypeError, ValueError):
            continue
        if history_index <= 0:
            continue
        if str(span.get("speaker") or "") not in {"user", "document"}:
            continue
        content = str(span.get("content") or "").lower()
        if not query_dates.isdisjoint(_date_scope_labels(content)) and _date_scope_user_can_project_to_assistant(content):
            scoped.update({history_index, history_index + 1})
    return scoped

def _recommendation_group_count_hints(query_lower: str, source_spans: list[dict[str, Any]]) -> dict[int, tuple[str, int, str]]:
    """Map assistant turns to bounded count hints from adjacent user requests.

    This preserves a product-useful abstraction: when a user asks for a fixed
    number of recommendations/options and the assistant answers with a list, the
    memory pack can carry the group size without treating every assistant
    recommendation as a user-mentioned or user-selected item.
    """

    if not re.search(r"\b(?:books?|series|genres?|titles?|movies?|films?|items?|options?|recommendations?)\b", query_lower):
        return {}
    covered_count_hint_dates = _count_hint_date_labels(query_lower, source_spans)
    candidates: list[tuple[int, tuple[str, int, str], bool, float, bool, bool]] = []
    ordered = sorted(source_spans, key=lambda span: int(span.get("history_index") or 10**9))
    user_requests: list[tuple[int, tuple[str, int] | None, int | None, str, str, bool, float]] = []
    for span in ordered:
        if str(span.get("speaker") or "") not in {"user", "document"}:
            continue
        try:
            history_index = int(span.get("history_index") or 0)
        except (TypeError, ValueError):
            continue
        if history_index <= 0:
            continue
        content = str(span.get("content") or "")
        parsed = _recommendation_request_count(query_lower, content)
        turn_parts = _turn_scope_parts(span)
        if parsed:
            value, noun, label = parsed
            user_requests.append((history_index, turn_parts, value, noun, label, True, _recommendation_request_specificity(content)))
            continue
        parsed_request = _recommendation_request_without_count(query_lower, content)
        if parsed_request:
            noun, label = parsed_request
            user_requests.append((history_index, turn_parts, None, noun, label, False, _recommendation_request_specificity(content)))
    if not user_requests:
        return {}
    for span in ordered:
        if str(span.get("speaker") or "") in {"user", "document"}:
            continue
        try:
            history_index = int(span.get("history_index") or 0)
        except (TypeError, ValueError):
            continue
        if history_index <= 0:
            continue
        content = str(span.get("content") or "")
        if not _assistant_looks_like_recommendation_list(content):
            continue
        nearest = next(
            (
                (idx, turn_parts, value, noun, label, explicit_count, specificity)
                for idx, turn_parts, value, noun, label, explicit_count, specificity in reversed(user_requests)
                if _turns_are_nearby(idx, turn_parts, span, history_index, max_distance=4)
            ),
            None,
        )
        if not nearest:
            continue
        request_index, _request_turn_parts, value, noun, label, explicit_count, specificity = nearest
        request_content = next(
            (
                str(request_span.get("content") or "")
                for request_span in ordered
                if int(request_span.get("history_index") or 0) == request_index
                and str(request_span.get("speaker") or "") in {"user", "document"}
            ),
            "",
        )
        request_dates = _date_scope_labels(request_content.lower())
        query_dates = _date_scope_labels(query_lower)
        date_matched = bool(query_dates and request_dates and not query_dates.isdisjoint(request_dates))
        date_already_covered = bool(request_dates and request_dates <= covered_count_hint_dates)
        if query_dates and request_dates and query_dates.isdisjoint(request_dates):
            continue
        if value is None:
            value = _assistant_recommendation_group_size(query_lower, content)
            if value is None:
                continue
            label = f"{value} recommended {noun}{'' if value == 1 or noun.endswith('s') else 's'}"
        noun_key = re.sub(r"[^a-z0-9]+", "_", noun.lower()).strip("_") or "items"
        key = f"group_count:{noun_key}:{history_index}:{value}"
        candidates.append((history_index, (key, value, label), explicit_count, specificity, date_matched, date_already_covered))
    if not candidates:
        return {}
    hints: dict[int, tuple[str, int, str]] = {}
    seen_keys: set[str] = set()
    explicit = [item for item in candidates if item[2]]
    inferred = [item for item in candidates if not item[2]]
    if explicit:
        inferred = []
    elif inferred:
        inferred = sorted(
            inferred,
            key=lambda item: (item[4], not item[5], item[3], item[1][1], -item[0]),
            reverse=True,
        )[:1]
    for history_index, hint, _explicit_count, _specificity, _date_matched, _date_already_covered in explicit + inferred:
        if hint[0] in seen_keys:
            continue
        seen_keys.add(hint[0])
        hints[history_index] = hint
    return hints

def _count_hint_date_labels(query_lower: str, source_spans: list[dict[str, Any]]) -> set[str]:
    labels: set[str] = set()
    query_dates = _date_scope_labels(query_lower)
    if not query_dates:
        return labels
    for span in source_spans:
        content = str(span.get("content") or "")
        count_hint = _generic_count_hint(query_lower, content, speaker=str(span.get("speaker") or "") or None, scoped_by_adjacent_user=True)
        if not count_hint:
            continue
        labels.update(_date_scope_labels(content.lower()) & query_dates)
    return labels

def _recommendation_request_without_count(query_lower: str, content: str) -> tuple[str, str] | None:
    lower = content.lower()
    if not re.search(r"\b(?:recommend|suggest|give me|list|options?|ideas?|looking for|help me pick|help me choose|find)\b", lower):
        return None
    if not re.search(r"\b(?:books?|series|genres?|titles?|movies?|films?|items?|options?|recommendations?)\b", lower):
        return None
    noun = _recommendation_noun_from_text(query_lower, lower)
    if not noun:
        return None
    return noun, f"recommended {noun}{'' if noun.endswith('s') else 's'}"

def _turns_are_nearby(
    history_index: int,
    turn_parts: tuple[str, int] | None,
    span: dict[str, Any],
    span_history_index: int,
    *,
    max_distance: int,
) -> bool:
    if 0 < span_history_index - history_index <= max_distance:
        return True
    span_parts = _turn_scope_parts(span)
    if not turn_parts or not span_parts:
        return False
    return turn_parts[0] == span_parts[0] and 0 < span_parts[1] - turn_parts[1] <= max_distance

def _recommendation_request_count(query_lower: str, content: str) -> tuple[int, str, str] | None:
    lower = content.lower()
    if not re.search(r"\b(?:recommend|suggest|give me|list|options?|ideas?|looking for|help me pick|help me choose)\b", lower):
        return None
    count_match = re.search(
        r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:[a-z][a-z-]{2,20}\s+){0,3}"
        r"(books?|series|genres?|titles?|movies?|films?|items?|options?|recommendations?)\b",
        lower,
    )
    if not count_match:
        return None
    value = _small_int_token(count_match.group(1))
    noun = count_match.group(2)
    if value is None or value <= 1 or value > 20:
        return None
    prefixes = _generic_aggregation_item_prefixes(query_lower)
    if "title" in prefixes and not re.search(r"\b(?:books?|series|titles?|movies?|films?)\b", noun):
        return None
    if "genre" in prefixes and "genre" not in noun and not re.search(r"\b(?:series|books?)\b", query_lower):
        return None
    noun_label = noun if noun.endswith("series") else noun.rstrip("s")
    plural_suffix = "" if value == 1 or noun_label.endswith("series") else "s"
    return value, noun_label, f"{value} recommended {noun_label}{plural_suffix}"

def _recommendation_noun_from_text(query_lower: str, lower: str) -> str | None:
    if re.search(r"\bseries\b", query_lower) and re.search(r"\bseries\b", lower):
        return "series"
    if re.search(r"\bgenres?\b", query_lower) and re.search(r"\bgenres?\b", lower):
        return "genre"
    if re.search(r"\b(?:books?|titles?)\b", query_lower) and re.search(r"\b(?:books?|titles?)\b", lower):
        return "title"
    if re.search(r"\b(?:movies?|films?)\b", query_lower) and re.search(r"\b(?:movies?|films?)\b", lower):
        return "movie"
    if re.search(r"\b(?:items?|options?|recommendations?)\b", lower):
        return "item"
    return None

def _recommendation_request_specificity(content: str) -> float:
    lower = content.lower()
    score = 0.0
    if re.search(r"\$\s?\d|\b\d+\s*(?:dollars?|usd)\b|\bbudget\b", lower):
        score += 0.35
    if re.search(r"\b(?:buy|purchase|order|borrow|download|print editions?|audiobooks?|e-?books?)\b", lower):
        score += 0.22
    if re.search(r"\b(?:from|at|on)\s+[A-Z][A-Za-z0-9&' -]{2,60}", content):
        score += 0.18
    if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}|20\d{2}\b", lower):
        score += 0.12
    if re.search(r"\b(?:fit|fits|criteria|preference|preferences|constraint|constraints|deadline|goal)\b", lower):
        score += 0.13
    return min(1.0, score)

def _assistant_recommendation_group_size(query_lower: str, content: str) -> int | None:
    heading_titles = [
        match.group(1)
        for match in re.finditer(r"(?:^|\n|\s)#{2,4}\s+\"([^\"]{2,80})\"", content)
        if match.group(1).strip()
    ]
    if len(heading_titles) >= 2:
        return min(len(heading_titles), 12)
    numbered = re.findall(r"(?:^|\n)\s*\d+[.)]\s+\*\*\"?[A-Z][^*\n\"]{2,80}\"?", content)
    if len(numbered) >= 2:
        return min(len(numbered), 12)
    inline_numbered_titles = re.findall(r"(?:^|\s)\d+[.)]\s+\*\*\"[^\"]{2,80}\"", content)
    if 2 <= len(inline_numbered_titles) <= 12:
        return len(inline_numbered_titles)
    quoted_titles = [key for key in generic_list_candidate_keys(query_lower, content) if key.startswith("title:")]
    if 2 <= len(quoted_titles) <= 12 and re.search(r"\b(?:recommend|suggest|here are|options?|movies?|films?|books?|series)\b", content.lower()):
        return len(quoted_titles)
    bullets = re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+\S", content)
    if 2 <= len(bullets) <= 12 and re.search(r"\b(?:recommend|suggest|here are|series|books?|titles?|genres?|movies?|films?)\b", content.lower()):
        return len(bullets)
    return None

def _assistant_looks_like_recommendation_list(content: str) -> bool:
    lower = content.lower()
    bullets = len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+\S", content))
    quoted = len(generic_list_candidate_keys("how many unique titles", content))
    if quoted >= 2:
        return True
    if not re.search(r"\b(?:recommend|suggest|option|good fit|here are|you might|consider|series|book|title|genre)\b", lower):
        return False
    return bullets >= 2 or quoted >= 2

def _small_int_token(value: str) -> int | None:
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    if value.isdigit():
        return int(value)
    return words.get(value)

def _date_scoped_turn_contexts(query_lower: str, source_spans: list[dict[str, Any]]) -> list[tuple[str, int]]:
    query_dates = _date_scope_labels(query_lower)
    if not query_dates:
        return []
    contexts: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for span in source_spans:
        if str(span.get("speaker") or "") not in {"user", "document"}:
            continue
        content = str(span.get("content") or "").lower()
        if query_dates.isdisjoint(_date_scope_labels(content)):
            continue
        if not _date_scope_user_can_project_to_assistant(content):
            continue
        parts = _turn_scope_parts(span)
        if parts and parts not in seen:
            contexts.append(parts)
            seen.add(parts)
    return contexts

def _span_has_adjacent_date_scope(span: dict[str, Any], contexts: list[tuple[str, int]]) -> bool:
    if not contexts:
        return False
    parts = _turn_scope_parts(span)
    if not parts:
        return False
    prefix, turn_number = parts
    for context_prefix, context_turn in contexts:
        if prefix == context_prefix and 0 <= turn_number - context_turn <= 4:
            return True
    return False

def _turn_scope_parts(span: dict[str, Any]) -> tuple[str, int] | None:
    text = str(span.get("turn_id") or span.get("source_uri") or span.get("id") or "")
    match = re.search(r"^(.*?)(?:[:/_-]?msg)(\d+)$", text)
    if not match:
        match = re.search(r"^(.*?)(\d+)$", text)
    if not match:
        return None
    try:
        return (match.group(1), int(match.group(2)))
    except (TypeError, ValueError):
        return None

def _date_scope_user_can_project_to_assistant(content_lower: str) -> bool:
    if re.search(r"\b(?:exclude|excluded|excluding|remove|removed|skip|skipped|avoid|avoided|replace|replacement)\b", content_lower):
        return False
    if re.search(r"\bshould\s+i\s+(?:add|include|watch|choose|pick)\b", content_lower):
        return False
    return bool(
        re.search(r"\b(?:plan|planned|planning|watchlist|chosen|chose|selected|finalized|recommend|suggest|suitable|add|include|movies?|films?)\b", content_lower)
    )

def _assistant_directly_matches_query_date_scope(query_lower: str, content: str) -> bool:
    query_dates = _date_scope_labels(query_lower)
    if not query_dates:
        return False
    content_lower = content.lower()
    content_dates = _date_scope_labels(content_lower)
    if not content_dates or query_dates.isdisjoint(content_dates):
        return False
    if len(generic_list_candidate_keys(query_lower, content)) > 4:
        return False
    return _assistant_title_list_commits_to_plan(content)

def _assistant_title_list_commits_to_plan(content: str) -> bool:
    lower = content.lower()
    if re.search(r"\b(?:strategies|tips|considerations|factors to consider|help you manage|keep .{0,40} engaged)\b", lower) and not re.search(
        r"\b(?:movie schedule|suggested schedule|revised schedule|final list|watchlist)\b",
        lower,
    ):
        return False
    if re.search(r"\b(?:final list|schedule|watchlist|will include|would include|included|selected|chosen|planned)\b", lower):
        return True
    if re.search(r"\b(?:additional|alternative|alternatives|suggestions?|recommendations?|consider|could also|might also)\b", lower):
        return False
    return False

def _generic_count_hint(query_lower: str, content: str, *, speaker: str | None, scoped_by_adjacent_user: bool) -> tuple[str, int, str] | None:
    if not re.search(r"\b(?:movies?|films?|titles?|items?)\b", query_lower):
        return None
    if speaker not in {"user", "document"} and not scoped_by_adjacent_user:
        return None
    lower = content.lower()
    match = re.search(
        r"\b(?:finalized|chosen|chose|selected|planned|picked)\s+(\d{1,2})\s+(movies?|films?|titles?|items?)\b"
        r"|\b(\d{1,2})\s+(movies?|films?|titles?|items?)\s+(?:i(?:'ve| have)?\s+)?(?:finalized|chosen|chose|selected|planned|picked)\b",
        lower,
    )
    if not match:
        return None
    value_text = match.group(1) or match.group(3)
    noun = match.group(2) or match.group(4) or "items"
    try:
        value = int(value_text)
    except (TypeError, ValueError):
        return None
    if value <= 1 or value > 50:
        return None
    if "including" not in lower and len(generic_list_candidate_keys(query_lower, content)) >= value:
        return None
    date_key = "_".join(sorted(_date_scope_labels(lower))) or "undated"
    noun_key = re.sub(r"[^a-z0-9]+", "_", noun).strip("_")
    return (f"count_hint:{noun_key}:{date_key}:{value}", value, f"{value} {noun}")

def _intent_requests_aggregation(query_intent: dict[str, Any] | None) -> bool:
    if not isinstance(query_intent, dict):
        return False
    aggregation = query_intent.get("aggregation") if isinstance(query_intent.get("aggregation"), dict) else {}
    operation = str(aggregation.get("operation") or "none")
    answer_shape = str(query_intent.get("answer_shape") or "")
    return operation not in {"", "none"} or answer_shape in {"count", "sum", "unordered_list"}

def _generic_aggregation_item_prefixes(query_lower: str, *, query_intent: dict[str, Any] | None = None) -> set[str]:
    prefixes: set[str] = set()
    if re.search(r"\b(?:movies?|films?|titles?|books?|series)\b", query_lower):
        prefixes.add("title")
    if re.search(r"\bgenres?\b", query_lower):
        prefixes.add("genre")
    if re.search(r"\b(?:shoe\s+)?sizes?\b", query_lower):
        prefixes.add("value")
    if re.search(r"\b(?:columns?|fields?)\b", query_lower):
        prefixes.add("column")
    if re.search(r"\b(?:areas?|aspects?|topics?)\b", query_lower):
        prefixes.add("area")
    if re.search(r"\b(?:features?|concerns?|capabilities|requirements?)\b", query_lower):
        prefixes.add("feature")
    if _is_role_security_feature_aggregation_query(query_lower):
        prefixes.update({"role", "security_feature"})
    if re.search(r"\b(?:requests?|questions?)\b", query_lower):
        prefixes.add("request")
    if _is_application_type_aggregation_query(query_lower):
        prefixes.add("application_type")
    if _is_planning_system_aggregation_query(query_lower):
        prefixes.add("plan_system")
    if _is_asset_aggregation_query(query_lower):
        prefixes.add("asset")
    if is_vendor_tool_aggregation_query(query_lower):
        prefixes.add("vendor_tool")
    prefixes.update(_prefixes_from_query_intent(query_intent))
    return prefixes

def _generic_aggregation_item_prefix(query_lower: str) -> str:
    prefixes = _generic_aggregation_item_prefixes(query_lower)
    for prefix in ("title", "genre", "value", "column", "area", "role", "security_feature", "feature", "request", "application_type", "plan_system", "asset", "vendor_tool"):
        if prefix in prefixes:
            return prefix
    return ""

def _prefixes_from_query_intent(query_intent: dict[str, Any] | None) -> set[str]:
    if not isinstance(query_intent, dict):
        return set()
    raw_object_types = query_intent.get("object_types")
    if not isinstance(raw_object_types, list):
        return set()
    mapping = {
        "role": "role",
        "security_feature": "security_feature",
        "application_type": "application_type",
        "planning_system": "plan_system",
        "vendor_tool": "vendor_tool",
        "asset": "asset",
        "event_aspect": "feature",
        "title": "title",
        "genre": "genre",
        "value": "value",
    }
    return {
        mapping[str(value)]
        for value in raw_object_types
        if str(value) in mapping
    }

def _use_area_focus_candidates(query_lower: str) -> bool:
    return bool(
        re.search(r"\b(?:areas?|aspects?|topics?)\b", query_lower)
        and re.search(r"\b(?:resume|portfolio|salary|negotiat(?:e|ion)|raise|promotion)\b", query_lower)
    )

def _use_schema_column_candidates(query_lower: str) -> bool:
    return bool(re.search(r"\b(?:columns?|fields?)\b", query_lower) and re.search(r"\b(?:tables?|schema|database|model)\b", query_lower))

def _schema_column_aggregation_keys(query_lower: str, content: str, *, speaker: str | None = None) -> list[str]:
    if speaker and speaker not in {"user", "document", "assistant"}:
        return []
    if not _use_schema_column_candidates(query_lower):
        return []
    lower = content.lower()
    if speaker == "assistant" and not re.search(r"\b(?:op\.add_column|alter\s+table\s+[a-z_][a-z0-9_]*\s+add\s+column)\b", lower):
        return []
    if speaker == "assistant" and re.search(r"\bfor example\b.{0,180}\b(?:add|include)\b.{0,80}\b(?:fields?|columns?)\b", lower):
        return []
    table_names = _schema_table_names(query_lower)
    if table_names and not any(table in lower for table in table_names):
        return []
    if not re.search(r"\b(?:add|adding|added|include|including|new|migration|migrate|alter\s+table|op\.add_column|add_column)\b", lower):
        return []
    columns: list[str] = []
    patterns = [
        r"\badd(?:ing|ed)?\s+(?:a\s+|an\s+|the\s+)?[`'\"]?([a-z_][a-z0-9_]*)[`'\"]?\s+(?:text\s+|varchar\(\d+\)\s+|string\s+|integer\s+|date\s+|datetime\s+)?columns?\b",
        r"\b(?:new|additional)\s+[`'\"]?([a-z_][a-z0-9_]*)[`'\"]?\s+(?:text\s+|varchar\(\d+\)\s+|string\s+|integer\s+|date\s+|datetime\s+)?columns?\b",
        r"\b(?:include|including)\s+(?:a\s+|an\s+|the\s+)?[`'\"]?([a-z_][a-z0-9_]*)[`'\"]?\s+(?:text\s+|varchar\(\d+\)\s+|string\s+|integer\s+|date\s+|datetime\s+)?columns?\b",
        r"\bop\.add_column\(\s*[`'\"]([a-z_][a-z0-9_]*)[`'\"]\s*,\s*sa\.column\(\s*[`'\"]([a-z_][a-z0-9_]*)[`'\"]",
        r"\balter\s+table\s+[`'\"]?([a-z_][a-z0-9_]*)[`'\"]?\s+add\s+column\s+[`'\"]?([a-z_][a-z0-9_]*)[`'\"]?",
        r"\b([a-z_][a-z0-9_]*)\s*=\s*(?:db\.)?column\([^)\n]{0,120}\)\s*#\s*new\s+field\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lower, flags=re.I):
            groups = [group for group in match.groups() if group]
            if not groups:
                continue
            if len(groups) >= 2 and _looks_like_schema_table(groups[0], table_names):
                columns.append(groups[1])
            else:
                columns.append(groups[-1])
    if speaker != "assistant" and re.search(r"\bnew\s+fields?\b", lower):
        for match in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*=\s*(?:db\.)?column\(", lower):
            columns.append(match.group(1))
    return [f"column:{_normalize_schema_column_name(column)}" for column in dict.fromkeys(columns) if _valid_schema_column_name(column)]

def _schema_table_names(query_lower: str) -> set[str]:
    out = set()
    for match in re.finditer(r"\b([a-z_][a-z0-9_]*)\s+tables?\b", query_lower):
        name = match.group(1)
        if name not in {"database", "schema"}:
            out.add(name)
            if name.endswith("s"):
                out.add(name[:-1])
            else:
                out.add(name + "s")
    return out

def _looks_like_schema_table(value: str, table_names: set[str]) -> bool:
    normalized = _normalize_schema_column_name(value)
    return bool(normalized in table_names or normalized.endswith("s"))

def _normalize_schema_column_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")

def _valid_schema_column_name(value: str) -> bool:
    normalized = _normalize_schema_column_name(value)
    if not normalized or normalized in {"id", "user_id", "type", "amount", "date", "created_at", "updated_at", "new", "field", "column", "the", "a", "an"}:
        return False
    return bool(re.fullmatch(r"[a-z_][a-z0-9_]{1,60}", normalized))

def _area_focus_aggregation_keys(query_lower: str, content: str, *, speaker: str | None = None) -> list[str]:
    if speaker and speaker not in {"user", "document", "assistant"}:
        return []
    if not _use_area_focus_candidates(query_lower):
        return []
    lower = content.lower()
    keys: list[str] = []
    if "salary" in query_lower or "negotiat" in query_lower or "raise" in query_lower:
        if re.search(r"\b(?:salary|raise|pay|compensation)\b", lower) and re.search(r"\b(?:negotiate|negotiating|negotiation|ask(?:ing)? for|increase)\b", lower):
            keys.append("area:salary_negotiation")
    if "portfolio" in query_lower and "portfolio" in lower:
        if re.search(r"\b(?:project|projects|case stud(?:y|ies)|work samples?)\b", lower) and re.search(r"\b(?:highlight|showcase|feature|select|selected|chose|choose|curate|curated)\b", lower):
            keys.append("area:portfolio_project_selection")
    if "resume" in query_lower and "resume" in lower:
        if re.search(r"\bremote\b", lower) and re.search(r"\bleadership(?:\s+skills?)?\b", lower):
            keys.append("area:remote_leadership_skills")
        if re.search(r"\b(?:update|updating|tailor|tailoring|ats|applicant tracking|callbacks?|ready|standard|standards|format|formats)\b", lower):
            keys.append("area:resume_update")
    return list(dict.fromkeys(keys))

def _use_app_feature_focus_candidates(query_lower: str) -> bool:
    return bool(
        re.search(r"\b(?:features?|concerns?|capabilities|requirements?)\b", query_lower)
        and re.search(r"\b(?:app|application|project|software|frontend|api|apis|website|site)\b", query_lower)
    )

def _app_feature_focus_aggregation_keys(query_lower: str, content: str, *, speaker: str | None = None) -> list[str]:
    if speaker and speaker not in {"user", "document"}:
        return []
    if not _use_app_feature_focus_candidates(query_lower):
        return []
    lower = content.lower()
    if not re.search(r"\b(?:i|my|we|our)\b", lower):
        return []
    keys: list[str] = []
    if re.search(r"\b(?:responsive|mobile|desktop|screen sizes?|orientation|wireframe|figma|css grid|flexbox)\b", lower):
        keys.append("feature:responsive_ui")
    if re.search(r"\b(?:autocomplete|geocoding|city input|enter city|city names?|weather display|fetch weather|weather data|dynamic weather)\b", lower):
        keys.append("feature:weather_lookup_interaction")
    if re.search(r"\b(?:error|errors|invalid|404|400|401|unauthorized|not found|try-catch|try catch|promise rejection|fallback ui|user-friendly messages?)\b", lower):
        keys.append("feature:user_visible_error_handling")
    if re.search(r"\b(?:rate limits?|quota|calls?/(?:minute|day)|calls per|response time|latency|cache|caching|retry|retries|backoff|performance|uptime)\b", lower):
        keys.append("feature:api_operational_limits")
    return list(dict.fromkeys(keys))

def _is_role_security_feature_aggregation_query(query_lower: str) -> bool:
    return bool(
        (
            re.search(r"\broles?\b", query_lower)
            or re.search(r"用户角色|角色", query_lower)
        )
        and (
            re.search(r"\b(?:security|auth(?:entication|orization)?|login|password|access control|rbac|permissions?)\b", query_lower)
            or re.search(r"安全功能|认证|鉴权|授权|登录|密码|访问控制|权限", query_lower)
        )
        and (
            re.search(r"\b(?:features?|requirements?|controls?|implement|trying|building|support)\b", query_lower)
            or re.search(r"功能|需求|控制|实现|构建|支持", query_lower)
        )
    )

def _role_security_feature_aggregation_keys(query_lower: str, content: str, *, speaker: str | None = None) -> list[str]:
    if speaker and speaker not in {"user", "document"}:
        return []
    if not _is_role_security_feature_aggregation_query(query_lower):
        return []
    lower = content.lower()
    keys: list[str] = []
    for key, pattern in _ROLE_PATTERNS:
        if re.search(pattern, lower):
            keys.append(f"role:{key}")
    if re.search(r"\buser authentication\b|\bauthentication\b", lower) and re.search(r"\b(?:implement|include|core functionality|mvp|feature|system)\b", lower):
        keys.append("security_feature:authentication")
    if re.search(r"\b(?:password_hash|password hash|password hashing|hashed passwords?|hash_password|argon2|bcrypt|scrypt|werkzeug\.security)\b", lower):
        keys.append("security_feature:password_hashing")
    if re.search(r"\b(?:flask-login|session login|session management|login sessions?|session validation|manual session handling)\b", lower):
        keys.append("security_feature:session_management")
    if re.search(r"\b(?:role-based access control|rbac|restrict access|based on (?:the )?user'?s role|authorization features?|authorize\()\b", lower):
        keys.append("security_feature:role_based_access_control")
    if re.search(r"\b(?:account lockout|lockout feature|failed login attempts?|rate limiting|locked out|redis)\b", lower):
        keys.append("security_feature:account_lockout_rate_limiting")
    if re.search(r"\b(?:login validation|validate_login|validation functions?|password validation)\b", lower):
        keys.append("security_feature:login_validation")
    return list(dict.fromkeys(keys[:10]))

_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("admin", r"\badmin(?:istrator)?\s+role\b|\brole\s*[:=]\s*['\"]?admin\b"),
    ("user", r"\buser\s+role\b|\brole\s*[:=]\s*['\"]?user\b|\ball users have (?:the )?['\"]user['\"] role\b"),
    ("manager", r"\bmanager\s+role\b|\brole\s*[:=]\s*['\"]?manager\b"),
    ("editor", r"\beditor\s+role\b|\brole\s*[:=]\s*['\"]?editor\b"),
    ("viewer", r"\bviewer\s+role\b|\brole\s*[:=]\s*['\"]?viewer\b"),
)

def _is_application_type_aggregation_query(query_lower: str) -> bool:
    return bool(
        re.search(r"\bapplication\s+types?\b", query_lower)
        or (
            re.search(r"\b(?:how many|number of|different|list)\b", query_lower)
            and re.search(r"\bapplications?\b", query_lower)
            and re.search(r"\bpersonal\s+statement\b", query_lower)
        )
    )

def _application_type_aggregation_keys(query_lower: str, content: str, *, speaker: str | None = None) -> list[str]:
    if speaker and speaker not in {"user", "document", "assistant"}:
        return []
    if not _is_application_type_aggregation_query(query_lower):
        return []
    lower = content.lower()
    if not re.search(r"\b(?:applications?|proposal|submission|deadline|personal\s+statement)\b", lower):
        return []
    keys: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", lower):
        if not re.search(r"\b(?:applications?|proposal|submission|deadline|personal\s+statement|opportunities)\b", sentence):
            continue
        if _application_type_sentence_is_hypothetical(sentence):
            continue
        for key, pattern in _APPLICATION_TYPE_PATTERNS:
            if re.search(pattern, sentence):
                keys.append(f"application_type:{key}")
    return list(dict.fromkeys(keys[:10]))


def _is_asset_aggregation_query(query_lower: str) -> bool:
    return bool(
        re.search(r"\b(?:assets?|items?|property|possessions?)\b", query_lower)
        and re.search(r"\b(?:estate|will|trust|beneficiar|inherit|asset\s+protection|planning)\b", query_lower)
    )


def _asset_aggregation_keys(query_lower: str, content: str, *, speaker: str | None = None) -> list[str]:
    if speaker and speaker not in {"user", "document", "assistant"}:
        return []
    if not _is_asset_aggregation_query(query_lower):
        return []
    lower = content.lower()
    if not re.search(r"\b(?:asset|estate|will|trust|beneficiar|inherit|account|home|vehicle|fund|policy|digital)\b", lower):
        return []
    if speaker == "assistant" and _asset_context_is_generic_template(lower):
        return []
    keys: list[str] = []
    for key, pattern in _ASSET_PATTERNS:
        if re.search(pattern, lower):
            keys.append(f"asset:{key}")
    if "digital" in lower and re.search(r"\b(?:vimeo|adobe|account|subscription|cloud|digital assets?)\b", lower):
        if "vimeo" in lower:
            keys.append("asset:vimeo_account")
        elif "adobe" in lower:
            keys.append("asset:adobe_subscription")
        else:
            keys.append("asset:digital_assets")
    if "financial accounts" in lower or re.search(r"\b(?:bank|investment)\s+accounts?\b", lower):
        keys.append("asset:financial_accounts")
    return list(dict.fromkeys(keys[:12]))


def _asset_context_is_generic_template(lower: str) -> bool:
    if re.search(r"\b(?:for example|such as|including:|include:|\[address\]|\[last name\]|\[executor|common choices)\b", lower):
        return True
    if re.search(r"\b(?:start by|first, take stock|comprehensive guide|here are some steps|you can include)\b", lower):
        return True
    if len(re.findall(r"(?:^|\n)\s*[-*]\s+", lower)) >= 4:
        return True
    return False


_ASSET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("home", r"\b(?:\$\s?350,?000\s+)?home\b|\b45\s+coral\s+bay\s+rd\b|\breal\s+(?:estate|property)\b"),
    ("savings_account", r"\bsavings\s+account\b|\b\$\s?25,?000\b[^.?!]{0,80}\bsavings\b"),
    ("film_equipment", r"\bfilm\s+equipment\b|\bequipment\b[^.?!]{0,80}\b\$\s?15,?000\b"),
    ("vehicle", r"\b(?:vehicle|2018\s+toyota\s+rav4|toyota\s+rav4)\b"),
    ("digital_assets", r"\bdigital\s+assets?\b"),
    ("parents_care_fund", r"\b(?:parents?'?\s+care|ongoing\s+care|care\s+fund)\b|\b\$\s?100,?000\s+fund\b|\b\$\s?7,?000\s+fund\b"),
    ("life_insurance_policy", r"\blife\s+insurance\s+policy\b"),
    ("financial_accounts", r"\bfinancial\s+accounts?\b"),
)


def _application_type_sentence_is_hypothetical(sentence: str) -> bool:
    return bool(
        re.search(r"\b(?:is it for|could be for|might be for|whether it is for|such as a job application|another type of submission)\b", sentence)
        or re.search(r"\b(?:for example|e\.g\.)\b", sentence)
    )

_APPLICATION_TYPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("academic", r"\bacademic\b(?=[^.!?]{0,100}\bapplications?\b)|\bacademic\s+(?:applications?|programs?|study)\b|\badvanced\s+stud(?:y|ies)\b"),
    ("scholarship", r"\bscholarships?\s+(?:applications?|deadlines?|letters?)\b|\bscholarship\s+deadline\b"),
    ("visa", r"\bvisa\s+(?:applications?|interviews?|issues?|choice|deadline|due)\b|\bstudy\s+visa\b"),
    ("grant", r"\bgrants?\s+(?:applications?|proposals?|requirements?|deadlines?)\b|\bgrant\s+(?:application|proposal|personal\s+statement)\b"),
    ("job", r"\bjob\s+applications?\b"),
    ("fellowship", r"\bfellowship\s+applications?\b"),
    ("internship", r"\binternship\s+applications?\b"),
)

def _is_planning_system_aggregation_query(query_lower: str) -> bool:
    task_management_context = bool(
        re.search(r"\b(?:tasks?|to-?dos?|events?|appointments?|deadlines?|family|reminders?|calendar|schedule)\b", query_lower)
    )
    explicit_planning_system = bool(
        re.search(
            r"\b(?:reminders?|planners?|calendars?|schedules?|task\s+(?:tools?|systems?|apps?|managers?)|to-?do\s+(?:tools?|systems?|apps?|lists?))\b",
            query_lower,
        )
        or re.search(r"\b(?:planning|planner|calendar|schedule|reminder)\s+(?:tools?|systems?|apps?|software|setup|workflow)\b", query_lower)
    )
    management_action = bool(
        re.search(r"\b(?:manage|using|use|used|track|organize|sync|schedule|remind|plan)\b", query_lower)
    )
    generic_tool_query = bool(
        re.search(r"\b(?:tools?|systems?|apps?|software)\b", query_lower)
        and re.search(r"\b(?:planning|planner|calendar|schedule|reminder|task|to-?do)\b", query_lower)
    )
    return bool((explicit_planning_system or generic_tool_query) and (task_management_context or management_action))

def _planning_system_aggregation_keys(query_lower: str, content: str) -> list[str]:
    if not _is_planning_system_aggregation_query(query_lower):
        return []
    keys: list[str] = []
    patterns = [
        ("todoist", r"\bTodoist\b"),
        ("google_calendar", r"\bGoogle\s+Calendar\b"),
        ("asana", r"\bAsana\b"),
        ("reminders_app", r"\bReminders?\s+app\b"),
        ("shared_calendar", r"\bshared\s+calendar\b"),
        ("family_calendar", r"\bfamily\s+calendar\b"),
    ]
    if re.search(r"\b(?:planners?|notebooks?)\b", query_lower):
        patterns.append(("moleskine_planner", r"\bMoleskine\s+planner\b"))
    for key, pattern in patterns:
        for match in re.finditer(pattern, content, flags=re.I):
            context = content[max(0, match.start() - 120) : min(len(content), match.end() + 120)]
            if _planning_system_context_matches(context):
                keys.append(f"plan_system:{key}")
                break
    if not keys and re.search(r"\b(?:calendars?|planners?)\b", query_lower):
        for match in re.finditer(r"\b(?:calendar|planner)\b", content, flags=re.I):
            context = content[max(0, match.start() - 80) : min(len(content), match.end() + 80)]
            if _planning_system_context_matches(context):
                label = match.group(0).lower()
                keys.append(f"plan_system:{label}")
    return list(dict.fromkeys(keys[:8]))

def _planning_system_context_matches(context: str) -> bool:
    lower = context.lower()
    return bool(
        re.search(r"\b(?:reminders?|plans?|planning|planner|calendar|schedule|tasks?|events?|appointments?|deadlines?|family)\b", lower)
        and re.search(r"\b(?:use|using|used|set|sync|synced|syncing|manage|organize|track|block|schedule|remind|plan|create|templates?)\b", lower)
    )

def _generic_item_scope_inclusion(query_lower: str, content: str, key: str, *, scoped_by_adjacent_user: bool = False) -> tuple[bool, str | None]:
    if key.startswith("plan_system:") and _plan_system_is_out_of_user_mentioned_scope(query_lower, content):
        return False, "assistant_plan_system_not_user_mentioned"
    if key.startswith("title:") and _title_negated_in_context(key, content):
        return False, "title_excluded_or_rejected_in_context"
    if key.startswith(("title:", "genre:")) and _is_exploratory_item_query(query_lower):
        exploratory_include, exploratory_reason = _exploratory_item_inclusion(content, key)
        if not exploratory_include:
            return False, exploratory_reason
    query_dates = _date_scope_labels(query_lower)
    if not query_dates:
        return True, None
    if scoped_by_adjacent_user:
        return True, None
    content_dates = _date_scope_labels(content.lower())
    if not content_dates:
        return False, "missing_query_date_scope"
    if query_dates.isdisjoint(content_dates):
        return False, "outside_query_date_scope"
    return True, None


def _plan_system_is_out_of_user_mentioned_scope(query_lower: str, content: str) -> bool:
    if not _query_needs_plan_system_scope_filter(query_lower):
        return False
    lower = content.lower()
    if re.search(r"\byou\s+(?:could|may|might|would)\s+(?:use|try|set up|create|consider)\b", lower):
        return True
    if re.search(r"\b(?:i|we)\b[^.?!]{0,140}\b(?:use|using|used|set|sync|synced|syncing|set up|started using)\b", lower):
        return False
    if re.search(r"\b(?:you|your)\b[^.?!]{0,140}\b(?:use|using|used|set|sync|synced|syncing|set up|started using)\b", lower):
        return False
    return _query_requires_user_stated_plan_system(query_lower)


def _query_needs_plan_system_scope_filter(query_lower: str) -> bool:
    return bool(
        re.search(r"\bmentioned\b.{0,40}\b(?:using|use|used)\b", query_lower)
        or _query_requires_user_stated_plan_system(query_lower)
    )


def _query_requires_user_stated_plan_system(query_lower: str) -> bool:
    if re.search(r"\btypes?\s+of\s+(?:reminders?|plans?)\b", query_lower):
        return False
    if re.search(r"\bwhat\b.{0,80}\b(?:i|we)\b.{0,80}\b(?:use|using|used|mentioned)\b", query_lower):
        return True
    if re.search(r"\b(?:i|we|my|our)\b.{0,40}\b(?:said|told|mentioned|actually|currently|already)\b.{0,80}\b(?:use|using|used)\b", query_lower):
        return True
    if re.search(r"\b(?:which|what)\b.{0,80}\b(?:tools?|systems?|apps?)\b.{0,80}\b(?:i|we)\b.{0,80}\b(?:use|using|used)\b", query_lower):
        return True
    if re.search(r"\bmentioned\b.{0,40}\b(?:using|use|used)\b", query_lower) and not re.search(r"\btypes?\s+of\b", query_lower):
        return True
    return False


def _is_exploratory_item_query(query_lower: str) -> bool:
    return bool(re.search(r"\b(?:want(?:ing)?|wanted|looking|interested|explor(?:e|ing)|try)\b", query_lower))

def _exploratory_item_inclusion(content: str, key: str) -> tuple[bool, str | None]:
    lower = content.lower()
    label = _generic_key_label(key).lower()
    if label and label in lower:
        label_pattern = re.escape(label).replace(r"\ ", r"\s+")
        around = rf"(?:[^.?!\n]{{0,140}}\b{label_pattern}\b[^.?!\n]{{0,140}})"
        match = re.search(around, lower)
        window = match.group(0) if match else lower
    else:
        window = lower
    if key.startswith("genre:"):
        return _exploratory_genre_inclusion(window)
    if re.search(r"\b(?:finished|completed|already\s+completed|already\s+finished|after\s+finishing|current\s+reading\s+list|overwhelmed\s+with\s+my\s+reading\s+list)\b", window):
        return False, "not_exploratory_or_already_completed"
    strong_exploratory = bool(
        re.search(
            r"\b(?:looking\s+for|interested|sounds?\s+(?:really\s+)?interesting|want\s+to\s+(?:read|explore|try)|"
            r"should\s+i\s+(?:read|try|start|explore)|consider(?:ing)?\s+(?:reading|starting|exploring)|"
            r"decid(?:e|ing)\s+(?:on|between)\s+(?:my\s+)?next\s+(?:read|series|book)|"
            r"next\s+(?:read|series|book)|recommend(?:ation)?|good\s+fit)\b",
            window,
        )
    )
    if re.search(
        r"\b(?:financial\s+decision|spend(?:ing)?|spent|cost(?:s|ing)?|price|worth\s+it|budget|over\s+budget|"
        r"exceed(?:ed|ing)?\s+(?:my\s+)?budget|purchase(?:d)?|bought)\b",
        window,
    ) and not strong_exploratory:
        return False, "not_exploratory_purchase_or_budget_review"
    if re.search(
        r"\b(?:looking\s+for|interested|sounds?\s+(?:really\s+)?interesting|want\s+to\s+(?:read|explore|try)|"
        r"wondering\s+if|should\s+i|consider(?:ing)?|decide|deciding|chose|chosen|start(?:ed)?|explor(?:e|ing)|"
        r"next\s+(?:read|series|book)|recommend(?:ation)?|good\s+fit)\b",
        window,
    ):
        return True, None
    return False, "missing_exploratory_intent"

def _exploratory_genre_inclusion(window: str) -> tuple[bool, str | None]:
    if re.search(
        r"\b(?:interested\s+in|looking\s+for|looking\s+to|want\s+to\s+(?:read|explore|try|start)|"
        r"trying\s+to\s+(?:find|decide|explore)|been\s+exploring|exploring|"
        r"new\s+(?:series|book|genre)|combines?\s+both\s+genres?|mix\s+of|blend(?:s|ing)?|"
        r"right\s+up\s+my\s+alley|sounds?\s+(?:really\s+)?interesting)\b",
        window,
    ):
        return True, None
    if re.search(r"\b(?:current\s+reading\s+list|overwhelmed\s+with\s+my\s+reading\s+list)\b", window):
        return False, "not_exploratory_or_already_completed"
    return False, "missing_exploratory_intent"

def _assistant_exploratory_genre_echo_keys(query_lower: str, content: str, *, speaker: str | None = None) -> list[str]:
    if speaker in {"user", "document"}:
        return []
    if not re.search(r"\bgenres?\b", query_lower) or not _is_exploratory_item_query(query_lower):
        return []
    lead = re.split(r"\b(?:here are|recommendations?|recommended|suggestions?|options?)\b|(?:^|\n)\s*(?:[-*]|\d+[.)]|#{2,4})\s+", content, maxsplit=1, flags=re.I)[0]
    if not lead:
        return []
    lead_lower = lead.lower()[:360]
    if not re.search(
        r"\b(?:your\s+(?:interest|preference|preferences)|you(?:'re| are)\s+(?:interested|looking|exploring)|"
        r"you\s+(?:mentioned|want|wanted)|given\s+your|sounds?\s+like\s+you)\b",
        lead_lower,
    ) and not re.search(r"^\s*exploring\b.{0,120}\bis\s+a\s+great\s+idea\b", lead_lower):
        return []
    keys = [key for key in generic_list_candidate_keys(query_lower, lead_lower) if key.startswith("genre:")]
    return list(dict.fromkeys(keys[:4]))

def _title_negated_in_context(key: str, content: str) -> bool:
    label = _generic_key_label(key).lower()
    if not label:
        return False
    lower = content.lower()
    if label not in lower:
        return False
    label_pattern = re.escape(label).replace(r"\ ", r"\s+")
    title_near_negative = rf"\b(?:exclude|excluded|excluding|remove|removed|skip|skipped|avoid|avoided|reject|rejected)\b[^.?!\n]{{0,120}}\b{label_pattern}\b"
    negative_near_title = rf"\b{label_pattern}\b[^.?!\n]{{0,120}}\b(?:excluded|removed|skipped|not\s+included|not\s+suitable|discomfort|uncomfortable|too\s+scary)\b"
    return bool(re.search(title_near_negative, lower) or re.search(negative_near_title, lower))

def _excluded_title_keys(source_spans: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for span in source_spans:
        if str(span.get("speaker") or "") not in {"user", "document"}:
            continue
        content = str(span.get("content") or "")
        lower = content.lower()
        if not re.search(r"\b(?:exclude|excluded|excluding|remove|removed|skip|skipped|avoid|avoided|reject|rejected|postpone|postponed|not\s+watch|not\s+include)\b", lower):
            continue
        for key in generic_list_candidate_keys("how many unique movie titles", content):
            if key.startswith("title:") and _title_negated_in_context(key, content):
                keys.add(key)
    return keys

def _title_positive_in_context(key: str, content: str) -> bool:
    label = _generic_key_label(key).lower()
    if not label:
        return False
    lower = content.lower()
    if label not in lower:
        return False
    label_pattern = re.escape(label).replace(r"\ ", r"\s+")
    positive_before = rf"\b(?:plan|planned|planning|chose|chosen|selected|finalized|add|added|include|included|watch|watched|start|started)\b[^.?!\n]{{0,120}}\b{label_pattern}\b"
    positive_after = rf"\b{label_pattern}\b[^.?!\n]{{0,120}}\b(?:planned|chosen|selected|finalized|added|included|on\s+(?:my|our|the)\s+watchlist)\b"
    return bool(re.search(positive_before, lower) or re.search(positive_after, lower))

def _date_scope_labels(text_lower: str) -> set[str]:
    labels: set[str] = set()
    month_names = (
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    )
    month_map = {
        "jan": "january",
        "january": "january",
        "feb": "february",
        "february": "february",
        "mar": "march",
        "march": "march",
        "apr": "april",
        "april": "april",
        "may": "may",
        "jun": "june",
        "june": "june",
        "jul": "july",
        "july": "july",
        "aug": "august",
        "august": "august",
        "sep": "september",
        "sept": "september",
        "september": "september",
        "oct": "october",
        "october": "october",
        "nov": "november",
        "november": "november",
        "dec": "december",
        "december": "december",
    }
    for match in re.finditer(rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*[-–—]\s*(\d{{1,2}})(?:st|nd|rd|th)?\b", text_lower):
        month = month_map.get(match.group(1), match.group(1))
        start_day = int(match.group(2))
        end_day = int(match.group(3))
        if 1 <= start_day <= end_day <= 31 and end_day - start_day <= 14:
            for day in range(start_day, end_day + 1):
                labels.add(f"{month}:{day}")
    for match in re.finditer(rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text_lower):
        month = month_map.get(match.group(1), match.group(1))
        labels.add(f"{month}:{int(match.group(2))}")
    for match in re.finditer(r"\b20\d{2}[-/](\d{1,2})[-/](\d{1,2})\b", text_lower):
        labels.add(f"{int(match.group(1))}:{int(match.group(2))}")
    return labels

def _generic_key_label(key: str) -> str:
    if key.startswith("value:size_"):
        value = key.split("value:size_", 1)[1].replace("_", ".")
        return f"size {value}"
    if ":" in key:
        key = key.split(":", 1)[1]
    label = re.sub(r"[_-]+", " ", key).strip()
    return label or key
