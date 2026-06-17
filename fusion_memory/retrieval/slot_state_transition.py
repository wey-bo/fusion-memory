from __future__ import annotations

"""Query-slot state transition candidates for update-style value questions."""

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.value_history_pack import (
    value_history_context_mismatch_rank,
    value_history_target_type_priority,
    value_history_topic_mismatch_rank,
    value_history_unit_mismatch_rank,
    value_summary_terms,
    value_topic_terms,
)


def value_state_summary(query: str, rows: list[dict[str, Any]], *, limit: int = 8) -> dict[str, Any]:
    if not rows:
        return {}
    candidates: list[dict[str, Any]] = []
    for row in rows:
        candidate = _state_candidate(query, row)
        if candidate:
            candidates.append(candidate)
    if not candidates:
        return {}
    candidates.extend(_derived_duration_candidates(query, candidates))
    _annotate_same_slot_replacements(query, candidates)
    candidates.sort(key=lambda item: item["state_score"], reverse=True)
    grouped = _dedupe_state_candidates(candidates, limit=limit)
    if not grouped:
        return {}
    resolved = _resolved_state_candidates(query, grouped)
    output = {
        "operator": "slot_state_transition",
        "state_candidates": grouped,
        "target_value_types": value_history_target_type_priority(query),
        "candidate_only": not bool(resolved),
        "guidance": (
            "These candidates group value mentions by query slot and state-transition evidence. "
            "Use resolved_value/resolved_values only when present; otherwise treat state_candidates as diagnostics and "
            "verify against value_history/raw evidence before selecting a current value."
        ),
    }
    if resolved:
        output["preferred_state"] = resolved[0]
        output["resolved_values"] = [candidate.get("value") for candidate in resolved if candidate.get("value")]
        if len(resolved) == 1:
            output["resolved_value"] = resolved[0].get("value")
            label = _resolved_value_label(resolved[0])
            if label != str(output["resolved_value"] or ""):
                output["resolved_label"] = label
        else:
            output["resolved_value"] = "; ".join(str(candidate.get("value") or "") for candidate in resolved)
            labels = [_resolved_value_label(candidate) for candidate in resolved if candidate.get("value")]
            if labels and labels != output["resolved_values"]:
                output["resolved_label"] = "; ".join(labels)
    return output


def _resolved_value_label(candidate: dict[str, Any]) -> str:
    value = str(candidate.get("value") or "").strip()
    if not value:
        return value
    qualifiers = candidate.get("qualifiers") if isinstance(candidate.get("qualifiers"), list) else []
    for qualifier in qualifiers:
        if not isinstance(qualifier, dict):
            continue
        qtype = str(qualifier.get("type") or "")
        qvalue = str(qualifier.get("value") or "").strip()
        if qtype in {"deadline", "target_date"} and qvalue and qvalue.lower() not in value.lower():
            return f"{value} by {qvalue}"
    return value


def _state_candidate(query: str, row: dict[str, Any]) -> dict[str, Any] | None:
    value = str(row.get("value") or "").strip()
    value_type = str(row.get("value_type") or "").strip()
    if not value or not value_type:
        return None
    context = str(row.get("context") or "")
    score, reasons = _state_score(query, row, value=value, value_type=value_type, context=context)
    if score < -4.0:
        return None
    return {
        "value": value,
        "value_type": value_type,
        "state_score": round(score, 3),
        "state_reasons": reasons[:10],
        "state_role": _state_role(row, context),
        "source_span_id": row.get("source_span_id"),
        "speaker": row.get("speaker"),
        "history_index": row.get("history_index"),
        "recency_rank": row.get("recency_rank"),
        "subject_key": row.get("subject_key"),
        "query_overlap": row.get("query_overlap"),
        "slot_overlap": row.get("slot_overlap"),
        "value_role": row.get("value_role"),
        "current": bool(row.get("current")),
        "update_marker_strength": row.get("update_marker_strength"),
        "slot_terms": sorted(_candidate_slot_terms(query, row, context)),
        "query_slot_terms": sorted(_query_slot_terms(query)),
        "capitalized_query_terms": sorted(_capitalized_query_terms(query)),
        "transition_relation": _transition_relation(context),
        "effective_date_key": _effective_date_key(context, value),
        "qualifiers": _state_value_qualifiers(context, value),
        "context": compact_summary(context, 220),
    }


def _state_score(query: str, row: dict[str, Any], *, value: str, value_type: str, context: str) -> tuple[float, list[str]]:
    lower_query = query.lower()
    lower_context = context.lower()
    role = str(row.get("value_role") or "")
    score = 0.0
    reasons: list[str] = []

    target_types = value_history_target_type_priority(query)
    if target_types:
        if value_type == target_types[0]:
            score += 5.0
            reasons.append("target_type")
        elif value_type in target_types:
            score += 2.2
            reasons.append("secondary_type")
        else:
            score -= 6.0
            reasons.append("wrong_type")

    unit_rank = value_history_unit_mismatch_rank(query, value, value_type)
    if unit_rank:
        score -= 4.0 * unit_rank
        reasons.append("unit_mismatch")
    else:
        score += 0.7
        reasons.append("unit_match")

    query_overlap = int(row.get("query_overlap") or 0)
    slot_overlap = int(row.get("slot_overlap") or 0)
    span_overlap = int(row.get("span_query_overlap") or 0)
    score += min(2.8, 0.45 * query_overlap)
    score += min(3.0, 0.55 * slot_overlap)
    score += min(0.8, 0.12 * span_overlap)
    if query_overlap:
        reasons.append("query_overlap")
    if slot_overlap:
        reasons.append("slot_overlap")

    context_rank = value_history_context_mismatch_rank(query, context, value_type, value=value)
    topic_rank = value_history_topic_mismatch_rank(query, context, value_type)
    if context_rank:
        score -= 2.0 * context_rank
        reasons.append("context_mismatch")
    if topic_rank:
        score -= 1.5 * topic_rank
        reasons.append("topic_mismatch")
    slot_rank = _value_slot_mismatch_rank(query, context, value_type, value)
    if slot_rank:
        score -= 3.0 * slot_rank
        reasons.append("value_slot_mismatch")
    scope_match = _compound_scope_anchor_match(query, context)
    if scope_match:
        score += min(2.2, 1.1 * scope_match)
        reasons.append("compound_scope_match")

    transition_strength = _transition_strength(lower_context, value)
    if _transition_relation(context):
        transition_strength = max(transition_strength, 3.0)
    if transition_strength:
        score += transition_strength
        reasons.append("state_transition")
        if transition_strength >= 1.8:
            reasons.append("strong_transition")
    marker = float(row.get("update_marker_strength") or 0.0)
    if marker:
        score += max(-1.0, min(1.8, marker))
        reasons.append("update_marker")

    if _context_marks_previous_value(lower_context, value):
        score -= 3.0
        reasons.append("context_previous_value")

    asks_target = _query_accepts_target_state(lower_query)
    if role == "previous":
        score -= 3.0
        reasons.append("previous_role")
    elif role == "example":
        score -= 4.0
        reasons.append("example_role")
    elif role == "target":
        score += 0.9 if asks_target or transition_strength else -0.5
        reasons.append("target_role")
    elif role == "current":
        score += 1.1
        reasons.append("current_role")
    elif role == "mentioned":
        score += 0.2

    if row.get("current"):
        score += 0.4
        reasons.append("current_flag")

    action_overlap = len(_state_action_terms(query) & value_summary_terms(context))
    if action_overlap:
        score += min(1.4, 0.55 * action_overlap)
        reasons.append("action_overlap")
    elif query_overlap < 2 and slot_overlap < 2:
        score -= 0.8
        reasons.append("low_slot_support")

    speaker = str(row.get("speaker") or "").lower()
    if speaker in {"user", "document", "fact"}:
        score += 1.1
        reasons.append("primary_source")
    elif speaker in {"assistant", "agent"}:
        score -= 0.15
        if _assistant_restates_user_state(lower_context):
            score += 0.45
            reasons.append("assistant_state_recap")

    if _context_is_hypothetical(lower_context):
        score -= 2.2
        reasons.append("hypothetical")

    history_index = int(row.get("history_index") or 0)
    if history_index:
        score += min(0.9, history_index / 90.0)
        reasons.append("timeline")

    if _query_requests_latest(lower_query):
        recency_rank = int(row.get("recency_rank") or 10**9)
        if recency_rank < 10**9:
            score += max(0.0, 0.7 - min(recency_rank, 60) * 0.012)
            reasons.append("recency")

    return score, reasons


def _dedupe_state_candidates(candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_values: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (str(candidate.get("value_type") or ""), str(candidate.get("value") or "").lower())
        if key in seen_values:
            continue
        seen_values.add(key)
        out.append(candidate)
        if len(out) >= limit:
            break
    return out


def _derived_duration_candidates(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not re.search(r"\bhow\s+many\s+days?\b", query.lower()):
        return []
    date_candidates = [candidate for candidate in candidates if str(candidate.get("value_type") or "") == "date"]
    out: list[dict[str, Any]] = []
    for end in date_candidates:
        if str(end.get("transition_relation") or "") != "extended_to":
            continue
        end_date = _parse_month_day_value(str(end.get("value") or ""))
        if not end_date:
            continue
        end_slots = set(end.get("slot_terms") or [])
        prior = [
            candidate
            for candidate in date_candidates
            if candidate is not end
            and _candidate_is_older(candidate, end)
            and _candidate_slots_compatible(end, candidate)
            and _range_start_date(candidate)
        ]
        if not prior:
            continue
        start_holder = max(prior, key=lambda item: _candidate_order_index(item) or -1)
        start_date = _range_start_date(start_holder)
        if not start_date:
            continue
        start_month, start_day = start_date
        end_month, end_day = end_date
        if start_month != end_month or end_day < start_day:
            continue
        days = end_day - start_day + 1
        if days <= 0 or days > 45:
            continue
        value = f"{days} days"
        derived = dict(end)
        derived.update(
            {
                "value": value,
                "value_type": "duration",
                "state_score": round(float(end.get("state_score") or 0.0) + 2.2, 3),
                "state_role": "updated",
                "value_role": "current",
                "current": True,
                "transition_relation": "derived_extended_duration",
                "source_span_id": end.get("source_span_id"),
                "slot_terms": sorted(end_slots | set(start_holder.get("slot_terms") or [])),
                "context": (
                    f"Derived inclusive duration from {start_holder.get('value')} through {end.get('value')} "
                    f"for the same updated schedule. Source: {end.get('context')}"
                ),
                "derived_from_values": [start_holder.get("value"), end.get("value")],
                "qualifiers": [{"type": "derived_range", "value": f"{start_holder.get('value')} to {end.get('value')}"}],
            }
        )
        reasons = [reason for reason in list(end.get("state_reasons") or []) if reason not in {"secondary_type", "wrong_type"}]
        if "target_type" not in reasons:
            reasons.insert(0, "target_type")
        for reason in ["derived_duration", "state_transition", "strong_transition"]:
            if reason not in reasons:
                reasons.append(reason)
        derived["state_reasons"] = reasons[:12]
        out.append(derived)
    return out[:4]


def _range_start_date(candidate: dict[str, Any]) -> tuple[int, int] | None:
    context = str(candidate.get("context") or "")
    value = str(candidate.get("value") or "")
    parsed_value = _parse_month_day_value(value)
    if not parsed_value:
        return None
    month, day = parsed_value
    month_names = (
        "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        "aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    )
    match = re.search(rf"\b({month_names})\s+(\d{{1,2}})\s*(?:-|–|to)\s*(\d{{1,2}})\b", context, flags=re.I)
    if not match:
        return None
    start_month = _month_number(match.group(1))
    if start_month != month:
        return None
    start_day = int(match.group(2))
    end_day = int(match.group(3))
    if day not in {start_day, end_day} and not (start_day <= day <= end_day):
        return None
    return (start_month, start_day)


def _parse_month_day_value(value: str) -> tuple[int, int] | None:
    match = re.search(
        r"\b("
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
        r")\s+(\d{1,2})",
        value,
        flags=re.I,
    )
    if not match:
        return None
    month = _month_number(match.group(1))
    if not month:
        return None
    return (month, int(match.group(2)))


def _annotate_same_slot_replacements(query: str, candidates: list[dict[str, Any]]) -> None:
    query_slots = _query_slot_terms(query)
    if not query_slots:
        return
    for candidate in candidates:
        if str(candidate.get("state_role") or "") == "previous":
            continue
        if str(candidate.get("value_type") or "") not in set(value_history_target_type_priority(query)):
            continue
        if "value_slot_mismatch" in set(candidate.get("state_reasons") or []):
            continue
        if not _candidate_has_replacement_relation(candidate):
            continue
        previous = [
            other
            for other in candidates
            if other is not candidate
            and str(other.get("value_type") or "") == str(candidate.get("value_type") or "")
            and str(other.get("value") or "").lower() != str(candidate.get("value") or "").lower()
            and _candidate_matches_query_slot(query_slots, other, relaxed=True)
            and _candidate_is_older(other, candidate)
            and _candidate_slots_compatible(candidate, other)
        ]
        newer_conflicts = [
            other
            for other in candidates
            if other is not candidate
            and str(other.get("value_type") or "") == str(candidate.get("value_type") or "")
            and str(other.get("value") or "").lower() != str(candidate.get("value") or "").lower()
            and _candidate_matches_query_slot(query_slots, other, relaxed=True)
            and _candidate_is_older(candidate, other)
            and _candidate_slots_compatible(candidate, other)
        ]
        if newer_conflicts:
            continue
        same_slot_others = [
            other
            for other in candidates
            if other is not candidate
            and str(other.get("value_type") or "") == str(candidate.get("value_type") or "")
            and _candidate_matches_query_slot(query_slots, other, relaxed=True)
            and _candidate_slots_compatible(candidate, other)
        ]
        if _candidate_has_newer_effective_conflict(candidate, same_slot_others):
            continue
        if not previous:
            continue
        if not _candidate_has_specific_slot_support(query_slots, candidate):
            continue
        if not _candidate_strongly_supersedes_previous(query, candidate, previous):
            continue
        candidate["state_score"] = round(float(candidate.get("state_score") or 0.0) + 4.0, 3)
        candidate["state_role"] = "updated"
        reasons = list(candidate.get("state_reasons") or [])
        for reason in ["same_slot_replacement", "strong_transition"]:
            if reason not in reasons:
                reasons.append(reason)
        candidate["state_reasons"] = reasons[:12]
        candidate["replaces_values"] = [
            {
                "value": other.get("value"),
                "source_span_id": other.get("source_span_id"),
                "history_index": other.get("history_index"),
                "subject_key": other.get("subject_key"),
            }
            for other in sorted(previous, key=lambda item: float(item.get("state_score") or 0.0), reverse=True)[:3]
        ]


def _candidate_has_replacement_relation(candidate: dict[str, Any]) -> bool:
    relation = str(candidate.get("transition_relation") or "")
    if relation in {
        "rescheduled_to",
        "moved_to",
        "extended_to",
        "increased_to",
        "raised_to",
        "shortened_to",
        "adjusted_to",
        "updated_to",
        "revised_to",
        "changed_to",
        "now_contains",
        "added",
    }:
        return True
    reasons = set(candidate.get("state_reasons") or [])
    context = str(candidate.get("context") or "").lower()
    if {"state_transition", "update_marker"} & reasons and re.search(
        r"\b(?:added|now|new|updated|revised|adjusted|changed|extended|rescheduled|moved|increased|raised)\b",
        context,
    ):
        return True
    return False


def _candidate_matches_query_slot(query_slots: set[str], candidate: dict[str, Any], *, relaxed: bool = False) -> bool:
    slot_terms = set(candidate.get("slot_terms") or [])
    if not slot_terms:
        return False
    if _slot_discriminators_conflict(query_slots, slot_terms):
        return False
    overlap = query_slots & slot_terms
    substantive_overlap = overlap - set(candidate.get("capitalized_query_terms") or [])
    required = 1 if relaxed or len(query_slots) <= 2 else min(2, len(query_slots))
    if overlap and not substantive_overlap and len(overlap) == 1:
        return False
    if not relaxed and _candidate_has_replacement_relation(candidate) and overlap:
        if substantive_overlap and int(candidate.get("query_overlap") or 0) >= 3 and int(candidate.get("slot_overlap") or 0) >= 3:
            return True
    return len(overlap) >= required


def _candidate_slots_compatible(candidate: dict[str, Any], other: dict[str, Any]) -> bool:
    ignored = set(candidate.get("capitalized_query_terms") or []) | _weak_replacement_slot_terms()
    left = set(candidate.get("slot_terms") or []) - ignored
    right = set(other.get("slot_terms") or []) - ignored
    if not left or not right:
        return False
    if _slot_discriminators_conflict(left, right):
        return False
    overlap = left & right
    if len(overlap) >= 2:
        return True
    query_terms = set(candidate.get("query_slot_terms") or []) | set(other.get("query_slot_terms") or [])
    if len((left & query_terms) & (right & query_terms)) >= 1 and len((left | right) & query_terms) >= 2:
        return True
    return False


def _candidate_is_older(other: dict[str, Any], candidate: dict[str, Any]) -> bool:
    other_effective = other.get("effective_date_key")
    candidate_effective = candidate.get("effective_date_key")
    if isinstance(other_effective, str) and isinstance(candidate_effective, str):
        return candidate_effective > other_effective
    other_index = _candidate_order_index(other)
    candidate_index = _candidate_order_index(candidate)
    if other_index is not None and candidate_index is not None and candidate_index > other_index:
        return True
    if other_index is not None or candidate_index is not None:
        return False
    other_recency = int(other.get("recency_rank") or 10**9)
    candidate_recency = int(candidate.get("recency_rank") or 10**9)
    return candidate_recency < other_recency


def _candidate_has_newer_effective_conflict(candidate: dict[str, Any], others: list[dict[str, Any]]) -> bool:
    candidate_effective = candidate.get("effective_date_key")
    if not isinstance(candidate_effective, str):
        return False
    for other in others:
        other_effective = other.get("effective_date_key")
        if isinstance(other_effective, str) and other_effective > candidate_effective:
            return True
    return False


def _candidate_order_index(candidate: dict[str, Any]) -> int | None:
    for key in ("history_index", "timeline_index"):
        raw = candidate.get(key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value:
            return value
    return None


def _resolved_state_candidates(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    if _query_requires_multi_facet_resolution(query):
        return _resolved_multi_value_group(query, candidates)
    derived = _resolved_derived_duration_candidate(query, candidates)
    if derived:
        return [derived]
    multi_group = _resolved_multi_value_group(query, candidates)
    if multi_group:
        return multi_group
    latest_same_slot = _resolved_latest_same_slot_candidate(query, candidates)
    if latest_same_slot:
        return [latest_same_slot]
    latest_count = _resolved_latest_count_update_candidate(query, candidates)
    if latest_count:
        return [latest_count]
    single_current = _resolved_single_current_slot_candidate(query, candidates)
    if single_current:
        return [single_current]
    top = candidates[0]
    top_replacement = _resolved_top_replacement_candidate(query, candidates)
    if top_replacement:
        return [top_replacement]
    if not _candidate_is_resolvable(query, top):
        return []
    strong_close = [
        candidate
        for candidate in candidates[1:5]
        if _candidate_is_resolvable(query, candidate)
        and str(candidate.get("value_type") or "") == str(top.get("value_type") or "")
        and float(top.get("state_score") or 0.0) - float(candidate.get("state_score") or 0.0) < 1.0
    ]
    if strong_close:
        pair = [top, *strong_close]
        if _query_accepts_multi_value_resolution(query, pair):
            return _same_context_value_group(pair)
        return []
    second = candidates[1] if len(candidates) > 1 else None
    if second and _candidate_is_resolvable(query, second):
        margin = float(top.get("state_score") or 0.0) - float(second.get("state_score") or 0.0)
        if margin < 2.0 and not _query_accepts_multi_value_resolution(query, [top, second]):
            return []
    return [top]


def _resolved_top_replacement_candidate(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    top = candidates[0]
    reasons = set(top.get("state_reasons") or [])
    if "target_type" not in reasons or "strong_transition" not in reasons:
        return None
    if {"wrong_type", "unit_mismatch", "value_slot_mismatch", "hypothetical"} & reasons:
        return None
    if not _candidate_has_replacement_relation(top):
        return None
    if not _candidate_is_resolvable(query, top):
        return None
    second = candidates[1] if len(candidates) > 1 else None
    if not second:
        return top
    if str(second.get("value_type") or "") != str(top.get("value_type") or ""):
        return top
    margin = float(top.get("state_score") or 0.0) - float(second.get("state_score") or 0.0)
    if margin >= 1.0:
        return top
    if margin >= 0.6 and "compound_scope_match" in reasons:
        return top
    return None


def _resolved_derived_duration_candidate(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    target_types = value_history_target_type_priority(query)
    if not target_types or target_types[0] != "duration":
        return None
    for candidate in candidates[:5]:
        if str(candidate.get("value_type") or "") != "duration":
            continue
        if "derived_duration" not in set(candidate.get("state_reasons") or []):
            continue
        if not _derived_duration_is_resolvable(candidate):
            continue
        return candidate
    return None


def _derived_duration_is_resolvable(candidate: dict[str, Any]) -> bool:
    reasons = set(candidate.get("state_reasons") or [])
    if "target_type" not in reasons or "derived_duration" not in reasons:
        return False
    if "unit_mismatch" in reasons or "wrong_type" in reasons or "value_slot_mismatch" in reasons or "hypothetical" in reasons:
        return False
    if int(candidate.get("query_overlap") or 0) < 3 and int(candidate.get("slot_overlap") or 0) < 3:
        return False
    return float(candidate.get("state_score") or 0.0) >= 10.0


def _resolved_latest_same_slot_candidate(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    query_slots = _query_slot_terms(query)
    if not query_slots:
        return None
    target_types = set(value_history_target_type_priority(query))
    if not target_types:
        return None
    eligible: list[dict[str, Any]] = []
    for candidate in candidates[:8]:
        if str(candidate.get("value_type") or "") not in target_types:
            continue
        if _candidate_has_explicit_current_slot(candidate) is False and str(candidate.get("state_role") or "") not in {"updated", "target"}:
            continue
        if not _candidate_matches_query_slot(query_slots, candidate, relaxed=True):
            continue
        if {"wrong_type", "unit_mismatch", "value_slot_mismatch"} & set(candidate.get("state_reasons") or []):
            continue
        if str(candidate.get("speaker") or "").lower() not in {"user", "document", "fact", "assistant", "agent"}:
            continue
        order_index = _candidate_order_index(candidate)
        if order_index is None:
            continue
        if float(candidate.get("state_score") or 0.0) < 11.0:
            continue
        eligible.append(candidate)
    if len(eligible) < 2:
        return None
    compatible_groups: list[list[dict[str, Any]]] = []
    for candidate in eligible:
        placed = False
        for group in compatible_groups:
            if any(_candidate_slots_compatible(candidate, other) for other in group):
                group.append(candidate)
                placed = True
                break
        if not placed:
            compatible_groups.append([candidate])
    best: dict[str, Any] | None = None
    best_order = -1
    for group in compatible_groups:
        if len(group) < 2:
            continue
        newest = max(group, key=lambda item: _candidate_order_index(item) or -1)
        newest_order = _candidate_order_index(newest) or -1
        older = [item for item in group if item is not newest and (_candidate_order_index(item) or -1) < newest_order]
        if not older:
            continue
        if _candidate_has_newer_effective_conflict(newest, older):
            continue
        if not _latest_candidate_has_update_semantics(newest):
            continue
        if not _latest_candidate_can_supersede_slot(query, newest):
            continue
        if not _candidate_has_specific_slot_support(query_slots, newest):
            continue
        slot_support = _latest_candidate_slot_support(newest, query_slots)
        reasons = set(newest.get("state_reasons") or [])
        if slot_support < 2 and not ("same_slot_replacement" in reasons and slot_support >= 1):
            continue
        if not _candidate_strongly_supersedes_previous(query, newest, older):
            continue
        if newest_order > best_order:
            best = dict(newest)
            best_order = newest_order
    if not best:
        return None
    reasons = list(best.get("state_reasons") or [])
    for reason in ["latest_same_slot", "same_slot_replacement"]:
        if reason not in reasons:
            reasons.append(reason)
    best["state_reasons"] = reasons[:12]
    best["state_role"] = "updated" if str(best.get("state_role") or "") != "target" else best.get("state_role")
    best["state_score"] = round(float(best.get("state_score") or 0.0) + 1.5, 3)
    return best


def _resolved_single_current_slot_candidate(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    query_slots = _query_slot_terms(query)
    target_types = set(value_history_target_type_priority(query))
    if not query_slots or not target_types:
        return None
    eligible: list[dict[str, Any]] = []
    blocked_values: set[str] = set()
    for candidate in candidates[:8]:
        value_type = str(candidate.get("value_type") or "")
        if value_type not in target_types:
            continue
        if value_type != "percentage":
            continue
        reasons = set(candidate.get("state_reasons") or [])
        value_key = str(candidate.get("value") or "").lower()
        if {"wrong_type", "unit_mismatch", "value_slot_mismatch", "hypothetical"} & reasons:
            blocked_values.add(value_key)
            continue
        if not _candidate_matches_query_slot(query_slots, candidate, relaxed=True):
            continue
        if not _candidate_has_explicit_current_slot(candidate):
            continue
        if _latest_candidate_slot_support(candidate, query_slots) < 2:
            continue
        if not _candidate_has_specific_slot_support(query_slots, candidate):
            continue
        if float(candidate.get("state_score") or 0.0) < 11.5:
            continue
        eligible.append(candidate)
    if len(eligible) != 1:
        return None
    top = dict(eligible[0])
    if str(top.get("value") or "").lower() in blocked_values:
        return None
    reasons = list(top.get("state_reasons") or [])
    if "single_current_slot" not in reasons:
        reasons.append("single_current_slot")
    top["state_reasons"] = reasons[:12]
    top["state_score"] = round(float(top.get("state_score") or 0.0) + 0.5, 3)
    return top


def _resolved_latest_count_update_candidate(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if "count" not in set(value_history_target_type_priority(query)):
        return None
    query_slots = _query_slot_terms(query)
    eligible: list[dict[str, Any]] = []
    for candidate in candidates[:8]:
        if str(candidate.get("value_type") or "") != "count":
            continue
        reasons = set(candidate.get("state_reasons") or [])
        if {"wrong_type", "unit_mismatch", "value_slot_mismatch", "hypothetical"} & reasons:
            continue
        if not _candidate_matches_query_slot(query_slots, candidate, relaxed=True):
            continue
        if _latest_candidate_slot_support(candidate, query_slots) < 2:
            continue
        context = str(candidate.get("context") or "").lower()
        if _query_asks_goal_count(query.lower()) and _candidate_is_progress_count(context, str(candidate.get("value") or "")):
            continue
        if not _latest_candidate_has_update_semantics(candidate):
            continue
        if float(candidate.get("state_score") or 0.0) < 12.0:
            continue
        if _candidate_order_index(candidate) is None:
            continue
        eligible.append(candidate)
    if len(eligible) < 2:
        return None
    groups: list[list[dict[str, Any]]] = []
    for candidate in eligible:
        for group in groups:
            if any(_candidate_count_slots_compatible(candidate, other) for other in group):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    best: dict[str, Any] | None = None
    best_order = -1
    for group in groups:
        if len(group) < 2:
            continue
        newest = max(group, key=lambda item: _candidate_order_index(item) or -1)
        order = _candidate_order_index(newest) or -1
        top_score = max(float(item.get("state_score") or 0.0) for item in group)
        if order <= best_order or float(newest.get("state_score") or 0.0) + 4.5 < top_score:
            continue
        best = dict(newest)
        best_order = order
    if not best:
        return None
    reasons = list(best.get("state_reasons") or [])
    for reason in ["latest_count_update", "same_slot_count_update"]:
        if reason not in reasons:
            reasons.append(reason)
    best["state_reasons"] = reasons[:12]
    best["state_role"] = "updated"
    best["state_score"] = round(float(best.get("state_score") or 0.0) + 1.0, 3)
    return best


def _candidate_count_slots_compatible(candidate: dict[str, Any], other: dict[str, Any]) -> bool:
    if not _candidate_slots_compatible(candidate, other):
        return False
    left_value = str(candidate.get("value") or "").lower()
    right_value = str(other.get("value") or "").lower()
    for unit in {"interviews", "books", "cupcakes", "sources", "calls", "requests", "items", "pages", "scenes"}:
        singular = unit.rstrip("s")
        if re.search(rf"\b{singular}s?\b", left_value) and re.search(rf"\b{singular}s?\b", right_value):
            return True
    return False


def _latest_candidate_has_update_semantics(candidate: dict[str, Any]) -> bool:
    role = str(candidate.get("state_role") or "")
    value_role = str(candidate.get("value_role") or "")
    reasons = set(candidate.get("state_reasons") or [])
    context = str(candidate.get("context") or "").lower()
    if role in {"updated", "current", "target"} or value_role in {"current", "target"}:
        return True
    if {"state_transition", "update_marker", "current_flag", "current_role", "target_role"} & reasons:
        return True
    return bool(
        re.search(
            r"\b(?:secured|ordered|adjusted|increased|raised|extended|rescheduled|updated|current|now|latest|aiming|budget|scheduled|free|available)\b",
            context,
        )
    )


def _latest_candidate_can_supersede_slot(query: str, candidate: dict[str, Any]) -> bool:
    relation = str(candidate.get("transition_relation") or "")
    if relation in {
        "rescheduled_to",
        "moved_to",
        "extended_to",
        "increased_to",
        "raised_to",
        "shortened_to",
        "adjusted_to",
        "updated_to",
        "revised_to",
        "changed_to",
        "now_contains",
        "added",
    }:
        return True
    value_type = str(candidate.get("value_type") or "")
    context = str(candidate.get("context") or "").lower()
    lower_query = query.lower()
    if value_type == "money":
        if re.search(r"\b(?:adjusted|increased|raised|updated|revised|changed|allocated|budgeted|set|proceed with)\b", context):
            return True
        if re.search(r"\b(?:capped|cap|limit|limited|approved|spent|expense|cost)\b", context):
            return False
    if value_type == "time" and re.search(r"\b(?:plan|planned|should|visit|appointment|scheduled|time)\b", lower_query):
        return bool(re.search(r"\b(?:free|available|rescheduled|scheduled|moved|shifted|plan)\b", context))
    if value_type == "count":
        if _query_asks_goal_count(lower_query) and _candidate_is_progress_count(context, str(candidate.get("value") or "")):
            return False
        return bool(re.search(r"\b(?:secured|ordered|completed|reached|achieved|added|increased|expanded|extended|updated|adjusted)\b", context))
    if value_type == "date":
        return bool(re.search(r"\b(?:deadline|due|scheduled|rescheduled|moved|extended|updated|revised|changed|aiming|target)\b", context))
    return bool(re.search(r"\b(?:adjusted|updated|revised|changed|increased|extended|rescheduled|moved|confirmed|scheduled)\b", context))


def _latest_candidate_slot_support(candidate: dict[str, Any], query_slots: set[str]) -> int:
    slot_terms = set(candidate.get("slot_terms") or [])
    if _slot_discriminators_conflict(query_slots, slot_terms):
        return 0
    return len((query_slots & slot_terms) - set(candidate.get("capitalized_query_terms") or []))


def _candidate_has_specific_slot_support(query_slots: set[str], candidate: dict[str, Any]) -> bool:
    value_type = str(candidate.get("value_type") or "")
    if value_type not in {"date", "time", "percentage"}:
        return True
    specific = _specific_slot_terms(query_slots) - _weak_event_slot_terms()
    if not specific:
        return True
    slot_terms = set(candidate.get("slot_terms") or [])
    return bool(specific & slot_terms)


def _weak_event_slot_terms() -> set[str]:
    return {
        "appointment",
        "call",
        "calls",
        "date",
        "deadline",
        "event",
        "meet",
        "meeting",
        "place",
        "schedule",
        "scheduled",
        "session",
        "sessions",
        "time",
    }


def _candidate_strongly_supersedes_previous(
    query: str,
    candidate: dict[str, Any],
    previous: list[dict[str, Any]],
) -> bool:
    if not previous:
        return False
    if "same_slot_replacement" in set(candidate.get("state_reasons") or []):
        return True
    value_type = str(candidate.get("value_type") or "")
    if not _candidate_has_specific_slot_support(_query_slot_terms(query), candidate):
        return False
    relation = str(candidate.get("transition_relation") or "")
    if relation in {"rescheduled_to", "moved_to", "adjusted_to", "updated_to", "revised_to", "changed_to", "increased_to", "raised_to"}:
        return True
    if _candidate_has_replacement_relation(candidate):
        return True
    if value_type not in {"money", "date", "time", "count", "duration", "percentage"}:
        return False
    if not _latest_candidate_has_update_semantics(candidate):
        return False
    if not _latest_candidate_can_supersede_slot(query, candidate):
        return False
    candidate_score = float(candidate.get("state_score") or 0.0)
    best_previous = max(float(item.get("state_score") or 0.0) for item in previous)
    if candidate_score + 0.35 >= best_previous:
        return True
    candidate_order = _candidate_order_index(candidate) or -1
    previous_order = max((_candidate_order_index(item) or -1) for item in previous)
    return candidate_order > previous_order and candidate_score + 1.25 >= best_previous


def _query_asks_goal_count(lower_query: str) -> bool:
    return bool(re.search(r"\b(?:aim(?:ing)?|goal|target|challenge|read|reading)\b", lower_query)) and bool(
        re.search(r"\b(?:how many|number of|count)\b", lower_query)
    )


def _candidate_is_progress_count(context: str, value: str) -> bool:
    lower = context.lower()
    escaped = re.escape(value.lower().strip())
    if not escaped:
        return False
    match = re.search(escaped, lower)
    window = lower[max(0, match.start() - 120) : match.end() + 120] if match else lower
    if not re.search(r"\b(?:finish(?:ed|ing)?|completed?|surpassed|read|after finishing|first)\b", window):
        return False
    return not bool(
        re.search(
            r"\b(?:goal|target|aim(?:ing)?|challenge|extended|increased|updated|revised)\b[^.?!]{0,100}"
            + escaped
            + r"|"
            + escaped
            + r"[^.?!]{0,100}\b(?:goal|target|aim|challenge)\b",
            window,
        )
    )


def _resolved_multi_value_group(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _query_accepts_multi_value_resolution(query, candidates):
        return []
    medium = [
        candidate
        for candidate in candidates[:6]
        if _candidate_is_resolvable(query, candidate, allow_medium_transition=True)
        and str(candidate.get("value_type") or "") == "money"
    ]
    if len(medium) < 2:
        return []
    grouped = _best_same_context_value_group(query, medium)
    return grouped if len(grouped) >= 2 else []


def _best_same_context_value_group(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (str(candidate.get("source_span_id") or ""), str(candidate.get("context") or ""))
        groups.setdefault(key, []).append(candidate)
    best: list[dict[str, Any]] = []
    best_score = float("-inf")
    lower_query = query.lower()
    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: float(item.get("state_score") or 0.0), reverse=True)
        source_bonus = 0.45 * sum(1 for item in ordered[:2] if str(item.get("speaker") or "").lower() in {"user", "document", "fact"})
        recap_penalty = 0.35 * sum(1 for item in ordered[:2] if "assistant_state_recap" in set(item.get("state_reasons") or []))
        score = sum(float(item.get("state_score") or 0.0) for item in ordered[:2]) + source_bonus - recap_penalty
        context = " ".join(str(item.get("context") or "") for item in ordered[:2]).lower()
        if re.search(r"\b(?:initial|original|first)\b", lower_query) and re.search(r"\b(?:initial|original|first)\b", context):
            score += 3.0
        if re.search(
            r"\b(?:through|until|by)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
            lower_query,
        ) and re.search(
            r"\b(?:through|until|by)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
            context,
        ):
            score += 1.0
        if score > best_score:
            best = ordered
            best_score = score
    return best or _same_context_value_group(candidates)


def _candidate_is_resolvable(
    query: str,
    candidate: dict[str, Any],
    *,
    allow_medium_transition: bool = False,
) -> bool:
    reasons = set(candidate.get("state_reasons") or [])
    score = float(candidate.get("state_score") or 0.0)
    query_overlap = int(candidate.get("query_overlap") or 0)
    slot_overlap = int(candidate.get("slot_overlap") or 0)
    if candidate.get("state_role") == "previous" or "context_previous_value" in reasons:
        return False
    has_target_type = "target_type" in reasons
    if not has_target_type and not _candidate_has_explicit_current_slot(candidate):
        return False
    if "unit_mismatch" in reasons or "wrong_type" in reasons or "hypothetical" in reasons:
        return False
    strong_transition = "strong_transition" in reasons
    medium_transition = allow_medium_transition and _candidate_has_transition(candidate)
    same_slot_replacement = "same_slot_replacement" in reasons and candidate.get("replaces_values")
    explicit_latest = _query_requests_latest(query.lower()) and _candidate_has_explicit_current_slot(candidate)
    if not same_slot_replacement and not strong_transition and not medium_transition and not explicit_latest:
        return False
    min_overlap = 2 if _candidate_has_explicit_current_slot(candidate) else 3
    if query_overlap < min_overlap and slot_overlap < min_overlap:
        return False
    if not same_slot_replacement and not _candidate_has_slot_anchor(query, candidate):
        return False
    threshold = 9.5 if same_slot_replacement and has_target_type else (10.5 if has_target_type else 6.0)
    return score >= threshold


def _candidate_has_explicit_current_slot(candidate: dict[str, Any]) -> bool:
    reasons = set(candidate.get("state_reasons") or [])
    role = str(candidate.get("state_role") or "")
    value_role = str(candidate.get("value_role") or "")
    if role not in {"current", "target", "updated"} and value_role not in {"current", "target"}:
        return False
    if not candidate.get("current") and value_role != "target":
        return False
    return bool(
        {"state_transition", "strong_transition", "update_marker", "current_role", "target_role", "current_flag"} & reasons
    )


def _candidate_has_slot_anchor(query: str, candidate: dict[str, Any]) -> bool:
    context = str(candidate.get("context") or "")
    clause_terms = value_summary_terms(_value_clause(context, str(candidate.get("value") or "")))
    context_terms = value_summary_terms(context)
    query_terms = value_topic_terms(query)
    anchors = _slot_anchor_terms(query_terms)
    if anchors and len(anchors & (clause_terms | context_terms)) >= 2:
        return True
    specific = _specific_slot_terms(query_terms)
    if specific:
        required = min(2, len(specific))
        if len(specific & clause_terms) < required and len(specific & context_terms) < max(2, required + 1):
            return False
    if not anchors:
        return True
    return bool(anchors & clause_terms) or bool(anchors & context_terms)


def _specific_slot_terms(query_terms: set[str]) -> set[str]:
    generic = {
        "accuracy",
        "aim",
        "aiming",
        "amount",
        "application",
        "budget",
        "call",
        "calls",
        "complete",
        "completed",
        "coverage",
        "current",
        "date",
        "deadline",
        "draft",
        "fee",
        "fees",
        "goal",
        "hours",
        "latest",
        "maintained",
        "module",
        "modules",
        "monthly",
        "percentage",
        "problem",
        "problems",
        "quota",
        "rate",
        "recently",
        "response",
        "scheduled",
        "secured",
        "submit",
        "target",
        "time",
        "weekly",
        "words",
    }
    return {term for term in query_terms if term not in generic and not term.isdigit()}


def _slot_anchor_terms(query_terms: set[str]) -> set[str]:
    anchor_vocab = {
        "accuracy",
        "allocation",
        "budget",
        "call",
        "calls",
        "count",
        "coverage",
        "deadline",
        "fee",
        "fees",
        "quota",
        "rate",
        "response",
        "target",
        "time",
        "weekly",
        "word",
        "words",
    }
    return query_terms & anchor_vocab


def _candidate_has_transition(candidate: dict[str, Any]) -> bool:
    reasons = set(candidate.get("state_reasons") or [])
    return "state_transition" in reasons or "update_marker" in reasons


def _query_accepts_multi_value_resolution(query: str, candidates: list[dict[str, Any]]) -> bool:
    lower = query.lower()
    if re.search(r"\b(?:both|total|including)\b", lower):
        connector = True
    elif re.search(r"\band\b", lower) and re.search(r"\bfees?\b[^?]{0,80}\band\b[^?]{0,80}\bfees?\b", lower):
        connector = True
    else:
        connector = False
    if not connector:
        return False
    if not all(str(candidate.get("value_type") or "") == "money" for candidate in candidates[:2]):
        return False
    return bool(re.search(r"\b(?:fees?|costs?|budget|allocated|allocation|amounts?)\b", lower))


def _query_requires_multi_facet_resolution(query: str) -> bool:
    target_types = value_history_target_type_priority(query)
    if len(target_types) < 2:
        return False
    lower = query.lower()
    if not re.search(r"\band\b", lower):
        return False
    if not re.search(
        r"\b(?:how\s+many|how\s+much|what|which|when)\b[^?]{0,120}\band\b[^?]{0,120}\b(?:how\s+many|how\s+much|what|which|when)\b",
        lower,
    ):
        return False
    return not re.search(
        r"\b(?:budget|fee|fees|cost|costs|amount|amounts|allocation|allocated)\b",
        lower,
    )


def _same_context_value_group(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []
    top = candidates[0]
    top_source = str(top.get("source_span_id") or "")
    top_context = str(top.get("context") or "")
    out = [
        candidate
        for candidate in candidates
        if str(candidate.get("source_span_id") or "") == top_source
        and str(candidate.get("context") or "") == top_context
    ]
    return out or [top]


def _value_clause(context: str, value: str, *, radius: int = 90) -> str:
    if not context or not value:
        return context
    match = re.search(re.escape(value), context, flags=re.I)
    if not match:
        return compact_summary(context, radius * 2)
    start = max(0, match.start() - radius)
    end = min(len(context), match.end() + radius)
    left_boundaries = [
        context.rfind(separator, 0, match.start())
        for separator in [".", ";", "\n", " - ", " but ", " and "]
    ]
    left = max([boundary for boundary in left_boundaries if boundary >= start], default=start)
    if left != start:
        left += 1
    right_candidates = [
        pos
        for separator in [".", ";", "\n", " - ", " but ", " and "]
        if (pos := context.find(separator, match.end())) != -1 and pos <= end
    ]
    right = min(right_candidates) if right_candidates else end
    return context[left:right].strip()


def _state_role(row: dict[str, Any], context: str) -> str:
    role = str(row.get("value_role") or "")
    lower = context.lower()
    if role == "previous" or _context_marks_previous_value(lower, str(row.get("value") or "")):
        return "previous"
    if _transition_relation(context):
        return "updated"
    if _transition_strength(lower, str(row.get("value") or "")) >= 1.5:
        return "updated"
    if role == "target":
        return "target"
    if role == "current" or row.get("current"):
        return "current"
    return "mentioned"


def _transition_strength(lower_context: str, value: str) -> float:
    escaped = re.escape(value.lower().strip())
    value_window = lower_context
    if escaped:
        match = re.search(escaped, lower_context)
        if match:
            value_window = lower_context[max(0, match.start() - 140) : match.end() + 140]
    score = 0.0
    strong_patterns = [
        r"\b(?:adjusted|updated|revised|rescheduled|moved|extended|increased|raised|reduced|decreased|shortened|changed)\b[^.?!]{0,100}\b(?:to|at|by|for)\b",
        r"\b(?:improved|reached|achieved|completed|secured)\b[^.?!]{0,100}\b(?:to|at|by|for)\b",
        r"\b(?:free|available)\b[^.?!]{0,80}\b(?:at|on|for)\b",
        r"\b(?:new|newly|latest|current)\b[^.?!]{0,80}\b(?:quota|budget|target|deadline|schedule|date|amount|count|rate|value)\b",
        r"\b(?:got|was|were|has been|have been)\s+(?:extended|rescheduled|moved|increased|adjusted|updated|raised|shortened|changed)\b",
    ]
    medium_patterns = [
        r"\b(?:allocated|budgeted|agreed|confirmed|set|capped|ordered|secured|completed|reached|achieved)\b[^.?!]{0,100}",
        r"\b(?:now|currently|recently|already|just)\b[^.?!]{0,80}\b(?:at|is|are|has|have|reached|completed|secured|ordered|scheduled)\b",
        r"\b(?:up from|from)\b[^.?!]{0,80}\b(?:to|now)\b",
        r"\b(?:current|updated|revised)\s+(?:plan|budget|target|deadline|schedule|allocation)\b",
    ]
    if any(re.search(pattern, value_window) for pattern in strong_patterns):
        score = max(score, 3.0)
    if any(re.search(pattern, value_window) for pattern in medium_patterns):
        score = max(score, 0.8)
    if re.search(r"\b(?:for example|example|assuming|would|could|should|might|if you)\b", value_window):
        score -= 0.8
    return max(0.0, score)


def _transition_relation(context: str) -> str | None:
    lower = context.lower()
    patterns = [
        ("rescheduled_to", r"\b(?:rescheduled|re-scheduled)\b[^.?!]{0,120}\b(?:to|for|at|on)\b"),
        ("moved_to", r"\b(?:moved|shifted)\b[^.?!]{0,120}\b(?:to|for|at|on)\b"),
        ("extended_to", r"\b(?:extended|extension)\b[^.?!]{0,120}\b(?:to|until|through|for|by)\b"),
        ("increased_to", r"\b(?:increased|increase|grew|grown)\b[^.?!]{0,120}\b(?:to|from|by)\b"),
        ("raised_to", r"\b(?:raised|boosted)\b[^.?!]{0,120}\b(?:to|from|by)\b"),
        ("shortened_to", r"\b(?:shortened|streamlined|reduced)\b[^.?!]{0,120}\b(?:to|from|by)\b"),
        ("adjusted_to", r"\b(?:adjusted|adjust)\b[^.?!]{0,120}\b(?:to|for)\b"),
        ("updated_to", r"\b(?:updated|update)\b[^.?!]{0,120}\b(?:to|reflect|with)\b"),
        ("revised_to", r"\b(?:revised|revision)\b[^.?!]{0,120}\b(?:to|for|with)\b"),
        ("changed_to", r"\b(?:changed|change)\b[^.?!]{0,120}\b(?:to|from)\b"),
        ("now_contains", r"\b(?:now|currently)\b[^.?!]{0,120}\b(?:contains?|includes?|has|have)\b"),
        ("added", r"\b(?:added|add(?:ing)?)\b[^.?!]{0,120}"),
    ]
    for relation, pattern in patterns:
        if re.search(pattern, lower):
            return relation
    return None


def _effective_date_key(context: str, value: str) -> str | None:
    clause = _value_clause(context, value, radius=140)
    lower = clause.lower()
    month = (
        "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        "aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    )
    match = re.search(rf"\b({month})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?", value, flags=re.I)
    if match:
        month_key = _month_number(match.group(1))
        if month_key:
            day = int(match.group(2))
            year = int(match.group(3) or 0)
            return f"{year:04d}-{month_key:02d}-{day:02d}"
    match = re.search(
        rf"\b(?:starting|effective|from|beginning|on|by|until|through)\s+({month})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?",
        lower,
    )
    if not match:
        match = re.search(rf"\b({month})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?", lower)
    if not match:
        return None
    month_key = _month_number(match.group(1))
    if not month_key:
        return None
    day = int(match.group(2))
    year = int(match.group(3) or 0)
    return f"{year:04d}-{month_key:02d}-{day:02d}"


def _state_value_qualifiers(context: str, value: str) -> list[dict[str, str]]:
    clause = _value_clause(context, value, radius=140)
    lower = clause.lower()
    out: list[dict[str, str]] = []
    month = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    for match in re.finditer(rf"\b(?:by|before|until|through|on)\s+({month}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d{{2}})?)", clause, flags=re.I):
        out.append({"type": "deadline", "value": match.group(1)})
    if re.search(r"\b(?:goal|target|aim(?:ing)?|challenge)\b", lower):
        out.append({"type": "state", "value": "target_or_goal"})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in out:
        key = (item["type"], item["value"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 4:
            break
    return deduped


def _month_number(raw: str) -> int | None:
    prefix = raw[:3].lower()
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    return months.get(prefix)


def _candidate_slot_terms(query: str, row: dict[str, Any], context: str) -> set[str]:
    clause = _value_clause(context, str(row.get("value") or ""), radius=120)
    clause_terms = value_topic_terms(clause)
    terms = set(clause_terms) | _slot_discriminator_terms(clause)
    query_terms = _query_slot_terms(query)
    if len(_filter_slot_terms(_normalize_slot_terms(terms)) & query_terms) == 0:
        terms |= _subject_key_terms(row) & value_topic_terms(context)
    return _filter_slot_terms(_normalize_slot_terms(terms))


def _value_slot_mismatch_rank(query: str, context: str, value_type: str, value: str) -> int:
    if value_type != "percentage":
        return 0
    lower_query = query.lower()
    if not re.search(r"\b(?:accuracy|rate|evaluation|evaluations|coverage)\b", lower_query):
        return 0
    clause = _value_clause(context, value, radius=90).lower()
    if re.search(r"\b(?:accuracy|accurate|evaluation|evaluations|matching|match\s+rate|success\s+rate|coverage)\b", clause):
        return 0
    if re.search(r"\b(?:screening\s+time|hiring\s+time|time\s+reduction|reduced\s+screening|faster)\b", clause):
        return 2
    return 1


def _compound_scope_anchor_match(query: str, context: str) -> int:
    lower_context = context.lower()
    count = 0
    for phrase in _compound_scope_anchors(query):
        if re.search(rf"\b{re.escape(phrase)}\b", lower_context):
            count += 1
    return count


def _compound_scope_anchors(query: str) -> list[str]:
    raw_tokens = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z'-]*", query)]
    generic = {
        "am",
        "are",
        "budget",
        "current",
        "date",
        "deadline",
        "did",
        "does",
        "for",
        "have",
        "how",
        "is",
        "many",
        "much",
        "my",
        "of",
        "on",
        "ordered",
        "scheduled",
        "should",
        "the",
        "this",
        "to",
        "total",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
    anchors: list[str] = []
    for i in range(len(raw_tokens) - 1):
        pair = raw_tokens[i : i + 2]
        if any(token in generic or len(token) < 3 for token in pair):
            continue
        anchors.append(" ".join(pair))
    for i in range(len(raw_tokens) - 2):
        triple = raw_tokens[i : i + 3]
        if any(token in generic or len(token) < 3 for token in triple):
            continue
        anchors.append(" ".join(triple))
    return list(dict.fromkeys(anchors))[:8]


def _query_slot_terms(query: str) -> set[str]:
    terms = value_topic_terms(query)
    generic = {
        "aim",
        "aiming",
        "agreed",
        "allocated",
        "amount",
        "budget",
        "complete",
        "completed",
        "current",
        "date",
        "deadline",
        "event",
        "many",
        "monthly",
        "number",
        "ordered",
        "plan",
        "planned",
        "scheduled",
        "secured",
        "should",
        "submit",
        "take",
        "time",
        "total",
        "usually",
        "what",
        "when",
        "year",
    }
    return _filter_slot_terms(_normalize_slot_terms(term for term in terms if term not in generic) | _slot_discriminator_terms(query))


def _slot_discriminator_terms(text: str) -> set[str]:
    lower = text.lower()
    labels = r"sprints?|phases?|stages?|rounds?|steps?|parts?|modules?|sessions?|weeks?|days?"
    label_aliases = {
        "sprints": "sprint",
        "sprint": "sprint",
        "phases": "phase",
        "phase": "phase",
        "stages": "stage",
        "stage": "stage",
        "rounds": "round",
        "round": "round",
        "steps": "step",
        "step": "step",
        "parts": "part",
        "part": "part",
        "modules": "module",
        "module": "module",
        "sessions": "session",
        "session": "session",
        "weeks": "week",
        "week": "week",
        "days": "day",
        "day": "day",
    }
    ordinal_words = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
    out: set[str] = set()
    for match in re.finditer(rf"\b({'|'.join(ordinal_words)})\s+({labels})\b", lower):
        label = label_aliases.get(match.group(2), match.group(2).rstrip("s"))
        out.add(f"{label}_{ordinal_words[match.group(1)]}")
    for match in re.finditer(rf"\b({labels})\s*(?:#|no\.?\s*)?(\d{{1,2}})(?:st|nd|rd|th)?\b", lower):
        label = label_aliases.get(match.group(1), match.group(1).rstrip("s"))
        out.add(f"{label}_{int(match.group(2))}")
    return out


def _slot_discriminators(terms: set[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for term in terms:
        match = re.match(r"^([a-z][a-z0-9]*)_(\d{1,2})$", str(term))
        if not match:
            continue
        out.setdefault(match.group(1), set()).add(match.group(2))
    return out


def _slot_discriminators_conflict(left_terms: set[str], right_terms: set[str]) -> bool:
    left = _slot_discriminators(left_terms)
    right = _slot_discriminators(right_terms)
    for label in set(left) & set(right):
        if left[label].isdisjoint(right[label]):
            return True
    return False


def _subject_key_terms(row: dict[str, Any]) -> set[str]:
    raw = str(row.get("subject_key") or "")
    if raw.startswith("subject:"):
        raw = raw[len("subject:") :]
    elif raw.startswith("exact:"):
        return set()
    return {part for part in re.split(r"[^a-zA-Z0-9]+", raw.lower()) if len(part) >= 3}


def _normalize_slot_terms(terms: Any) -> set[str]:
    normalized: set[str] = set()
    aliases = {
        "schedul": "schedule",
        "scheduled": "schedule",
        "scheduling": "schedule",
        "rescheduled": "schedule",
        "module": "modules",
        "source": "sources",
        "book": "books",
        "cupcake": "cupcakes",
        "interview": "interviews",
        "session": "sessions",
        "fee": "fees",
        "gift": "gifts",
        "deadline": "deadline",
        "layout": "layout",
        "navigation": "navigation",
        "sprint": "sprint",
    }
    for raw in terms:
        term = str(raw or "").lower().strip("_-")
        if len(term) < 3:
            continue
        if term.endswith("ing") and len(term) > 6:
            term = term[:-3]
        elif term.endswith("ed") and len(term) > 5:
            term = term[:-2]
        elif term.endswith("s") and len(term) > 4 and term not in {"fees"}:
            term = term[:-1]
        normalized.add(aliases.get(term, term))
    return normalized


def _filter_slot_terms(terms: set[str]) -> set[str]:
    temporal_noise = {
        "april",
        "august",
        "december",
        "february",
        "january",
        "july",
        "june",
        "march",
        "may",
        "november",
        "october",
        "september",
        "2023",
        "2024",
        "2025",
    }
    weak = {
        "about",
        "already",
        "also",
        "because",
        "between",
        "can",
        "consider",
        "considering",
        "need",
        "new",
        "only",
        "per",
        "start",
        "starting",
        "through",
        "with",
        "without",
        "reflect",
        "update",
        "updated",
        "current",
        "now",
        "recently",
        "trying",
        "help",
    }
    return {
        term
        for term in terms
        if term not in temporal_noise and term not in weak and not term.isdigit()
    }


def _weak_replacement_slot_terms() -> set[str]:
    return {
        "back",
        "get",
        "giv",
        "start",
        "starting",
        "month",
        "monthly",
        "management",
        "per",
        "reschedul",
        "schedule",
        "skill",
        "week",
        "year",
        "time",
        "track",
        "good",
        "great",
        "help",
        "make",
        "plan",
        "project",
        "trying",
        "worry",
        "worri",
        "kinda",
        "feel",
        "question",
    }


def _capitalized_query_terms(query: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", query):
        out |= _normalize_slot_terms([token])
    return _filter_slot_terms(out)


def _context_marks_previous_value(lower_context: str, value: str) -> bool:
    escaped = re.escape(value.lower().strip())
    if not escaped:
        return bool(re.search(r"\b(?:previously|originally|initially|old|older|before)\b", lower_context))
    match = re.search(escaped, lower_context)
    if not match:
        return False
    window = lower_context[max(0, match.start() - 100) : match.end() + 100]
    return bool(
        re.search(rf"\b(?:previously|originally|initially|old|older|before|baseline|manual|up\s+from|from)\b[^.?!]{{0,80}}{escaped}", window)
        or re.search(rf"{escaped}[^.?!]{{0,80}}\b(?:previously|originally|initially|old|older|before|baseline|last\s+year)\b", window)
        or re.search(rf"{escaped}[^.?!]{{0,80}}\bin\s+(?:20\d{{2}}|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)", window)
    )


def _query_accepts_target_state(lower_query: str) -> bool:
    return bool(
        re.search(
            r"\b(?:aim(?:ing)?|goal|target|deadline|due|scheduled|plan(?:ned)?|should\s+i\s+plan|"
            r"submit|budget|allocated|agreed|ordered|secured|completed|practicing)\b",
            lower_query,
        )
    )


def _query_requests_latest(lower_query: str) -> bool:
    return bool(re.search(r"\b(?:current|currently|latest|recent|recently|now|updated|new)\b", lower_query))


def _state_action_terms(query: str) -> set[str]:
    action_like = {
        "achieve",
        "achieved",
        "agreed",
        "aim",
        "aiming",
        "allocated",
        "budget",
        "completed",
        "coverage",
        "deadline",
        "ordered",
        "practicing",
        "quota",
        "rate",
        "scheduled",
        "secured",
        "spent",
        "submit",
        "target",
    }
    return (value_summary_terms(query) | value_topic_terms(query)) & action_like


def _assistant_restates_user_state(lower_context: str) -> bool:
    return bool(
        re.search(
            r"\b(?:you have|you've|your current|you currently|you already|you set|you allocated|"
            r"you agreed|you increased|you reached|you achieved|you ordered|you secured|given that you|based on your)\b",
            lower_context,
        )
    )


def _context_is_hypothetical(lower_context: str) -> bool:
    return bool(
        re.search(r"\b(?:for example|example|hypothetical|placeholder|assuming|would|could|might|if you)\b", lower_context)
        and not re.search(
            r"\b(?:you have|you've|you already|actual|current|updated|confirmed|adjusted|increased|allocated|budgeted|scheduled|rescheduled|extended)\b",
            lower_context,
        )
    )
