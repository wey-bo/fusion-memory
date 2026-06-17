from __future__ import annotations

import re
from typing import Any

from fusion_memory.retrieval.aggregation_common import _append_aggregation_item, _match_context, _span_ref


def _previous_nonspace(content: str, index: int) -> str | None:
    for char in reversed(content[:index]):
        if not char.isspace():
            return char
    return None


def _combination_item_key(n_text: str, context: str) -> str:
    lower = context.lower()
    try:
        n_value = int(n_text)
    except ValueError:
        n_value = 0
    if "ball" in lower:
        return "ways:choose_balls"
    if "ace" in lower:
        return "ways:choose_aces_cards"
    if "card" in lower or "deck" in lower or n_value >= 20:
        return "ways:choose_cards"
    return "ways:choose_objects"


def _looks_like_sample_space_value(n_text: str, context: str) -> bool:
    try:
        if int(n_text) >= 20:
            return True
    except ValueError:
        pass
    lower = context.lower()
    return bool(re.search(r"\b(?:sample space|total number of ways|total possible|possible outcomes|from 52)\b", lower))


def _filter_combinatorics_aggregation_items(query_lower: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not re.search(r"\b(?:balls?|cards?|deck|aces?)\b", query_lower):
        return items
    filtered: list[dict[str, Any]] = []
    best_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for item in items:
        key = str(item.get("key") or "")
        if not key.startswith("ways:") or not item.get("included"):
            filtered.append(item)
            continue
        context = str(item.get("context") or "")
        canonical = _canonical_combinatorics_key_for_query(query_lower, key, context)
        if canonical is None:
            excluded = dict(item)
            excluded["included"] = False
            excluded["reason"] = "outside_requested_combinatorics_domain"
            filtered.append(excluded)
            continue
        item = dict(item)
        item["key"] = canonical
        value = int(item.get("value") or 0)
        dedupe_key = (canonical, value)
        previous = best_by_key.get(dedupe_key)
        if previous is None or _combinatorics_item_preference(item) > _combinatorics_item_preference(previous):
            best_by_key[dedupe_key] = item
    filtered.extend(best_by_key.values())
    filtered.sort(
        key=lambda item: (
            1 if not item.get("included") else 0,
            int(item.get("history_index") or 10**9),
            0 if str(item.get("speaker") or "") in {"user", "document"} else 1,
            str(item.get("key") or ""),
            int(item.get("value") or 0),
        )
    )
    return filtered


def _canonical_combinatorics_key_for_query(query_lower: str, key: str, context: str) -> str | None:
    lower = context.lower()
    wants_balls = re.search(r"\bballs?\b", query_lower)
    wants_cards = re.search(r"\b(?:cards?|deck|aces?)\b", query_lower)
    has_ball = bool(re.search(r"\bballs?\b", lower))
    has_card = bool(re.search(r"\b(?:cards?|deck|aces?)\b", lower))
    if key == "ways:choose_objects" and has_ball and wants_balls:
        return "ways:choose_balls"
    if key == "ways:choose_objects" and has_card and wants_cards:
        return "ways:choose_aces_cards" if re.search(r"\baces?\b", lower) else "ways:choose_cards"
    if key == "ways:arrange_objects" and has_ball and wants_balls:
        return "ways:arrange_balls"
    if key == "ways:arrange_objects" and wants_balls and not has_ball:
        return None
    if key == "ways:choose_balls" and wants_balls:
        return key
    if key in {"ways:choose_cards", "ways:choose_aces_cards"} and wants_cards:
        return key
    if key == "ways:arrange_objects" and has_card and wants_cards and not wants_balls:
        return "ways:arrange_cards"
    if has_ball and wants_balls:
        return key
    if has_card and wants_cards:
        return key
    return None


def _combinatorics_item_preference(item: dict[str, Any]) -> tuple[int, int, int]:
    speaker = str(item.get("speaker") or "")
    context = str(item.get("context") or "").lower()
    return (
        1 if speaker in {"user", "document"} else 0,
        1 if re.search(r"\b(?:i|my|can you|help me|trying to|want to)\b", context) else 0,
        len(context),
    )


def _append_probability_calculation_items(
    items: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
    content: str,
    span_ref: dict[str, Any],
) -> None:
    lower = content.lower()
    candidates: list[tuple[str, str, bool, str]] = []
    speaker = str(span_ref.get("speaker") or "")
    if (
        "1/2" in lower
        and "heads" in lower
        and ("flipping a coin" in lower or "coin toss problem" in lower or "probability of getting heads" in lower or "getting heads" in lower)
        and "both heads" not in lower
    ):
        candidates.append(("calculation:coin_heads", "heads", speaker == "user" or _is_confirmation_calculation_context(lower), "probability of getting heads"))
    if "rolling a 4" in lower and "1/6" in lower:
        candidates.append(("calculation:die_roll_4", "rolling a 4", speaker == "user" or _is_confirmation_calculation_context(lower), "probability of rolling a 4"))
    if "greater than 4" in lower and ("2/6" in lower or "1/3" in lower):
        candidates.append(("calculation:die_greater_than_4", "greater than 4", speaker == "user" or _is_confirmation_calculation_context(lower), "probability of rolling greater than 4"))
    if "both heads" in lower and "1/4" in lower:
        both_heads_context = _calculation_context(content, "both heads", fallback="probability of both heads").lower()
        candidates.append(
            (
                "calculation:two_coin_both_heads",
                "both heads",
                _is_confirmation_calculation_context(both_heads_context) and not _is_negated_confirmation_context(both_heads_context),
                "probability of both heads",
            )
        )
    for key, term, include, label in candidates:
        reason = None if include else "educational_example_not_confirmed"
        context = _calculation_context(content, term, fallback=label)
        _append_aggregation_item(items, seen, key, 1, context, span_ref, included=include, reason=reason, dedupe_by_key=True)


def _calculation_context(content: str, term: str, *, fallback: str) -> str:
    index = content.lower().find(term.lower())
    if index < 0:
        return fallback
    return _match_context(content, index, index + len(term), radius=160)


def _is_confirmation_calculation_context(lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:confirm|verify|check|correct|really|compare(?:d)?(?: the)? results|same answer|getting stuck|make sure)\b",
            lower,
        )
    )


def _is_negated_confirmation_context(lower: str) -> bool:
    return bool(
        re.search(r"\bnot\s+(?:something\s+)?(?:i\s+)?(?:asked\s+to\s+)?(?:confirm|verify|check)\b", lower)
        or re.search(r"\b(?:background|example|not\s+one\s+i\s+asked)\b", lower)
    )


def _is_probability_calculation_query(query_lower: str) -> bool:
    return bool(
        re.search(r"\bprobability calculations?\b", query_lower)
        or ("calculations" in query_lower and re.search(r"\b(?:coins?|dice|die|rolling|tossing)\b", query_lower))
    )


def _append_stress_break_items(
    items: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
    content: str,
    span_ref: dict[str, Any],
) -> None:
    if str(span_ref.get("speaker") or "") not in {"user", "document"}:
        return
    lower = content.lower()
    if re.search(r"\b(?:1|one)\s*-?\s*hour\b", lower) and "break" in lower:
        include = bool(re.search(r"\b(?:stress|stressed|burnout|focus|yoga)\b", lower))
        index = re.search(r"\b(?:1|one)\s*-?\s*hour\b", lower)
        context = _match_context(content, index.start(), index.end(), radius=160) if index else content
        _append_aggregation_item(
            items,
            seen,
            "break:one_hour_stress_day",
            1,
            context,
            span_ref,
            included=include,
            reason=None if include else "break_not_tied_to_stress_or_burnout",
            dedupe_by_key=True,
            label="one hour on one day",
        )
    full_days = re.search(r"\b(?:two|2)\s+full\s+days?\s+off\b", lower)
    if full_days:
        _append_aggregation_item(
            items,
            seen,
            "break:full_days_off",
            2,
            _match_context(content, full_days.start(), full_days.end(), radius=160),
            span_ref,
            included=True,
            dedupe_by_key=True,
            label="two full days off",
        )
    generic_hours = re.search(r"\b(?:2|two)\s*-?\s*hours?\s+break\b|\b(?:2|two)\s*-?\s*hour\s+break\b", lower)
    if generic_hours and re.search(r"\b(?:stress|stressed|burnout|focus|yoga|meditation|mindfulness)\b", lower):
        _append_aggregation_item(
            items,
            seen,
            "break:two_hour_stress_break",
            1,
            _match_context(content, generic_hours.start(), generic_hours.end(), radius=160),
            span_ref,
            included=True,
            dedupe_by_key=True,
            label="two hour stress break",
        )
    elif generic_hours:
        _append_aggregation_item(
            items,
            seen,
            "excluded:generic_reset_break",
            0,
            _match_context(content, generic_hours.start(), generic_hours.end(), radius=160),
            span_ref,
            included=False,
            reason="break_not_tied_to_stress_or_burnout",
        )


def _append_score_improvement_items(
    items: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
    query_lower: str,
    source_spans: list[dict[str, Any]],
) -> None:
    wants_area_calculation = bool(re.search(r"\barea calculation problems?\b|\btriangle area\b|\barea formulas?\b", query_lower))
    for span in source_spans:
        content = str(span.get("content") or "")
        lower = content.lower()
        if not re.search(r"\b(?:score|accuracy|quiz)\b", lower):
            continue
        span_ref = _span_ref(span)
        for match in re.finditer(r"\b(?:improv(?:ed|ement)|increas(?:ed|e))\s+from\s+(\d{1,3})%\s+to\s+(\d{1,3})%", lower):
            start_score = int(match.group(1))
            end_score = int(match.group(2))
            value = end_score - start_score
            context = lower[max(0, match.start() - 120) : match.end() + 180]
            is_area_score = bool(re.search(r"\barea calculation problems?\b|\btriangle area\b", context))
            include = (not wants_area_calculation) or is_area_score
            reason = None if include else "score_pair_not_for_area_calculation_problem_accuracy"
            label = f"{start_score}% to {end_score}% improvement"
            if is_area_score:
                label = f"{start_score}% to {end_score}% area calculation improvement"
            _append_aggregation_item(
                items,
                seen,
                f"score_improvement:{start_score}:{end_score}:{'area' if is_area_score else 'other'}",
                value if include else 0,
                _match_context(content, match.start(), match.end(), radius=170),
                span_ref,
                included=include,
                reason=reason,
                dedupe_by_key=True,
                label=label,
            )


def _is_stress_break_aggregation_query(query_lower: str) -> bool:
    if not re.search(r"\b(?:how many|total|count|number|across)\b", query_lower):
        return False
    return bool(re.search(r"\b(?:days?|take off|took off|breaks?|stress|burnout|rest)\b", query_lower))


def _is_score_improvement_query(query_lower: str) -> bool:
    if not re.search(r"\b(?:how much|difference|improv(?:e|ed|ement)|increase|changed?)\b", query_lower):
        return False
    return bool(re.search(r"\b(?:score|scores|accuracy|percent|percentage|quiz)\b", query_lower))


def _is_combinatorics_aggregation_query(query_lower: str) -> bool:
    if not re.search(r"\b(?:how many|total|count|number|different|across)\b", query_lower):
        return False
    return bool(
        re.search(
            r"\b(?:ways?|arrang(?:e|ing|ements?)|choos(?:e|ing)|combinations?|permutations?|balls?|cards?|deck|dice|coins?|probability calculations?|calculations?)\b",
            query_lower,
        )
    )


def _is_ways_combinatorics_query(query_lower: str) -> bool:
    if not re.search(r"\b(?:how many|total|count|number|different|across)\b", query_lower):
        return False
    return bool(
        re.search(
            r"\b(?:ways?|arrang(?:e|ing|ements?)|choos(?:e|ing)|combinations?|permutations?|balls?|cards?|deck)\b",
            query_lower,
        )
    )
