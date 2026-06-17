from __future__ import annotations

"""Recall compact value rows from update-like statements in the full query scope."""

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.value_history_pack import (
    dedupe_value_mentions,
    query_targeted_value_mentions,
    value_history_context_mismatch_rank,
    value_history_subject_key,
    value_history_target_type_priority,
    value_history_topic_mismatch_rank,
    value_history_unit_mismatch_rank,
    value_history_value_role,
    value_mentions,
    value_role_is_current,
    value_summary_terms,
    value_topic_terms,
    value_update_marker_strength,
)


def build_slot_update_recall_rows(query: str, scope_spans: list[Any], *, limit: int = 24) -> list[dict[str, Any]]:
    """Return high-signal value rows from update/current-state statements.

    This operator scans the already selected BEAM scope, but only emits compact
    typed value rows. It is intentionally separate from raw source-span
    expansion so the answer prompt does not grow with every matching turn.
    """

    target_types = set(value_history_target_type_priority(query))
    if not target_types:
        return []
    query_lower = query.lower()
    ordered = [
        span
        for span in scope_spans
        if str(getattr(span, "span_type", "") or "turn") in {"turn", "tool_result", "document_chunk"}
        and str(getattr(span, "speaker", "") or "").lower() in {"user", "assistant", "document", "fact"}
        and str(getattr(span, "content", "") or "").strip()
    ]
    if not ordered:
        return []
    rows: list[dict[str, Any]] = []
    total = len(ordered)
    for history_index, span in enumerate(ordered, start=1):
        content = str(getattr(span, "content", "") or "")
        values = dedupe_value_mentions(query_targeted_value_mentions(query, content) + value_mentions(content))
        if not values:
            continue
        speaker = str(getattr(span, "speaker", "") or "").lower()
        for value in values:
            value_text = str(value.get("text") or "").strip()
            value_type = str(value.get("type") or "").strip()
            if not value_text or not value_type:
                continue
            context = str(value.get("context") or content)
            score, reasons = _slot_update_recall_score(
                query,
                query_lower,
                content,
                context,
                speaker=speaker,
                value=value_text,
                value_type=value_type,
                target_types=target_types,
            )
            if score < 6.0:
                continue
            role = value_history_value_role(query, context, value_text, value_type)
            marker = value_update_marker_strength(query_lower, context.lower(), value_text)
            rows.append(
                {
                    "source_span_id": getattr(span, "span_id", None),
                    "speaker": speaker,
                    "timestamp": getattr(getattr(span, "timestamp", None), "isoformat", lambda: None)(),
                    "turn_id": getattr(span, "turn_id", None),
                    "source_uri": getattr(span, "source_uri", None),
                    "history_index": history_index,
                    "recency_rank": total - history_index + 1,
                    "value_type": value_type,
                    "value": value_text,
                    "context": compact_summary(context, 260),
                    "subject_key": value_history_subject_key(query, context),
                    "current": value_role_is_current(role)
                    or (role == "target" and _query_asks_target_or_plan(query_lower))
                    or (_update_relation_score(context, value_text) >= 1.4 and role not in {"previous", "example"}),
                    "query_overlap": len(value_summary_terms(query) & value_summary_terms(context)),
                    "span_query_overlap": len(value_summary_terms(query) & value_summary_terms(content)),
                    "slot_overlap": len(value_topic_terms(query) & value_topic_terms(context)),
                    "value_role": role,
                    "update_marker_strength": max(marker, min(2.4, _update_relation_score(context, value_text))),
                    "candidate_source": "slot_update_recall",
                    "recall_score": round(score, 3),
                    "recall_reasons": reasons[:10],
                }
            )
    rows.sort(
        key=lambda item: (
            -float(item.get("recall_score") or 0.0),
            int(item.get("recency_rank") or 10**9),
            -int(item.get("history_index") or -1),
            str(item.get("value_type") or ""),
            str(item.get("value") or ""),
        )
    )
    return _dedupe_rows(rows, limit=limit)


def _slot_update_recall_score(
    query: str,
    query_lower: str,
    content: str,
    context: str,
    *,
    speaker: str,
    value: str,
    value_type: str,
    target_types: set[str],
) -> tuple[float, list[str]]:
    context_lower = context.lower()
    content_lower = content.lower()
    score = 0.0
    reasons: list[str] = []

    if value_type in target_types:
        score += 3.4
        reasons.append("target_type")
    elif _secondary_value_type_allowed(query_lower, value_type):
        score += 0.8
        reasons.append("secondary_type")
    else:
        score -= 4.5
        reasons.append("wrong_type")

    unit_rank = value_history_unit_mismatch_rank(query, value, value_type)
    if unit_rank:
        score -= 3.0 * unit_rank
        reasons.append("unit_mismatch")
    else:
        score += 0.7
        reasons.append("unit_match")

    query_terms = value_summary_terms(query)
    context_terms = value_summary_terms(context)
    content_terms = value_summary_terms(content)
    topic_terms = value_topic_terms(query)
    context_topic_terms = value_topic_terms(context)
    query_overlap = len(query_terms & context_terms)
    span_overlap = len(query_terms & content_terms)
    slot_overlap = len(topic_terms & context_topic_terms)
    score += min(3.0, 0.55 * query_overlap)
    score += min(1.2, 0.12 * span_overlap)
    score += min(3.0, 0.75 * slot_overlap)
    if query_overlap:
        reasons.append("query_overlap")
    if slot_overlap:
        reasons.append("slot_overlap")

    if query_overlap < 2 and slot_overlap < 2 and not _query_name_overlap(query, context):
        score -= 2.5
        reasons.append("weak_slot_overlap")

    context_rank = value_history_context_mismatch_rank(query, context, value_type, value=value)
    topic_rank = value_history_topic_mismatch_rank(query, context, value_type)
    if context_rank:
        score -= 1.6 * context_rank
        reasons.append("context_mismatch")
    if topic_rank:
        score -= 1.4 * topic_rank
        reasons.append("topic_mismatch")

    relation = _update_relation_score(context, value)
    marker = value_update_marker_strength(query_lower, context_lower, value)
    if relation:
        score += relation
        reasons.append("update_relation")
    if marker:
        score += max(-0.8, min(1.6, marker))
        reasons.append("update_marker")
    if _label_bound_current_value(context, value):
        score += 1.8
        reasons.append("label_bound")

    role = value_history_value_role(query, context, value, value_type)
    if role == "previous":
        score -= 3.0
        reasons.append("previous_role")
    elif role == "example":
        score -= 3.5
        reasons.append("example_role")
    elif role == "target":
        score += 1.2 if _query_asks_target_or_plan(query_lower) else 0.2
        reasons.append("target_role")
    elif role == "current":
        score += 1.0
        reasons.append("current_role")

    if speaker in {"user", "document", "fact"}:
        score += 1.1
        reasons.append("primary_source")
    elif speaker == "assistant":
        if _assistant_state_recap(content_lower):
            score += 0.9
            reasons.append("assistant_state_recap")
        else:
            score -= 0.3
            reasons.append("assistant_source")

    if _hypothetical_context(context_lower):
        score -= 2.0
        reasons.append("hypothetical")

    if not relation and marker <= 0 and role not in {"current", "target"} and not _label_bound_current_value(context, value):
        score -= 2.2
        reasons.append("not_update_like")

    return score, reasons


def _update_relation_score(context: str, value: str) -> float:
    lower = context.lower()
    escaped = re.escape(value.lower().strip())
    window = lower
    if escaped:
        match = re.search(escaped, lower)
        if match:
            window = lower[max(0, match.start() - 140) : match.end() + 140]
    score = 0.0
    strong_patterns = [
        r"\b(?:updated|adjusted|revised|changed|corrected|rescheduled|moved|shifted)\b[^.?!]{0,120}\b(?:to|for|at|on|with|reflect)\b",
        r"\b(?:extended|extension)\b[^.?!]{0,120}\b(?:to|until|through|for|by)\b",
        r"\b(?:increased|raised|boosted|expanded|grew|grown|reduced|decreased|lowered)\b[^.?!]{0,120}\b(?:to|from|by)\b",
        r"\b(?:now|currently|latest|new)\b[^.?!]{0,120}\b(?:deadline|target|goal|budget|schedule|date|time|count|quota|amount)\b",
        r"\b(?:ordered|secured|completed|reached|achieved|confirmed|scheduled)\b[^.?!]{0,120}",
        r"\b(?:free|available)\b[^.?!]{0,80}\b(?:at|on|for)\b",
    ]
    medium_patterns = [
        r"\b(?:current|updated|revised|new)\s+(?:deadline|target|goal|budget|schedule|plan|allocation|amount)\b",
        r"\b(?:aiming|goal|target|deadline|due|scheduled|set|allocated|budgeted)\b[^.?!]{0,120}",
        r"\b(?:proceed with|go with|stick with)\b[^.?!]{0,80}",
    ]
    if any(re.search(pattern, window) for pattern in strong_patterns):
        score = max(score, 2.6)
    if any(re.search(pattern, window) for pattern in medium_patterns):
        score = max(score, 1.2)
    return score


def _secondary_value_type_allowed(query_lower: str, value_type: str) -> bool:
    if value_type == "date" and re.search(r"\b(?:how many days?|duration|scheduled)\b", query_lower):
        return True
    return False


def _query_asks_target_or_plan(query_lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:aim(?:ing)?|goal|target|deadline|due|plan|planned|scheduled|should|budget|allocated|ordered|secured|completed)\b",
            query_lower,
        )
    )


def _label_bound_current_value(context: str, value: str) -> bool:
    escaped = re.escape(value.lower().strip())
    if not escaped:
        return False
    lower = context.lower()
    return bool(
        re.search(
            rf"\b(?:current|updated|revised|new|final|latest)\s+(?:deadline|target|goal|budget|schedule|date|time|amount|count|quota)\s*[:=-]\s*[^.?!]{{0,80}}{escaped}",
            lower,
        )
        or re.search(
            rf"\b(?:deadline|target|goal|budget|schedule|date|time|amount|count|quota)\s*[:=-]\s*[^.?!]{{0,80}}{escaped}",
            lower,
        )
    )


def _assistant_state_recap(lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:you have|you've|your current|you currently|you already|you set|you allocated|you increased|"
            r"you adjusted|you updated|you reached|you achieved|you ordered|you secured|given that you|based on your|"
            r"with an? (?:increased|updated|revised|new)|let's proceed with)\b",
            lower,
        )
    )


def _hypothetical_context(lower: str) -> bool:
    return bool(
        re.search(r"\b(?:for example|example|sample|hypothetical|placeholder|assuming|would|could|might|if you)\b", lower)
        and not re.search(r"\b(?:you have|you've|you already|actual|current|updated|confirmed|scheduled)\b", lower)
    )


def _query_name_overlap(query: str, context: str) -> bool:
    names = {
        match.group(0).lower()
        for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2}\b", query)
        if match.group(0) not in {"What", "When", "How", "Which", "Where"}
    }
    if not names:
        return False
    lower = context.lower()
    return any(name in lower for name in names)


def _dedupe_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("source_span_id") or ""),
            str(row.get("value_type") or ""),
            str(row.get("value") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out
