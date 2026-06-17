from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.llm import LLMClient, sanitize_error_text

LLM_AGGREGATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["key", "label", "value", "included", "count_role", "memory_object_type", "source_span_id", "confidence"],
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": ["number", "string"]},
                    "included": {"type": "boolean"},
                    "count_role": {"type": "string"},
                    "memory_object_type": {"type": "string"},
                    "source_span_id": {"type": "string"},
                    "confidence": {"type": ["number", "string"]},
                    "reason": {"type": "string"},
                },
            },
        },
    },
    "required": ["items"],
}

LLM_AGGREGATION_PROMPT = """Analyze the query and evidence spans as a structured memory aggregation task.

Return JSON only. Extract countable or summable memory items that directly answer
the query. Do not answer the user. Do not use outside knowledge. Do not use any
benchmark rubric, hidden label, or gold answer. Every item must cite a source_span_id
from the provided evidence.

Use included=true only when the item should contribute to the query's count or
sum. Use included=false for tempting but excluded evidence, with a short reason.
Use count_role from: user_reported_count, additive_item, additive_value,
candidate_group_count, assistant_supported_count, excluded.
Use memory_object_type from: user_intent_item, user_reported_aggregate,
derived_aggregate_item, assistant_supported_item, excluded_candidate.
Prefer durable semantic keys such as feature:..., security_feature:..., role:...,
title:..., value:..., break:..., calculation:..., ways:..., application_type:...,
plan_system:..., or vendor_tool:....

Partial evidence is allowed, but low-confidence guesses must be excluded or omitted.
"""

def _llm_aggregation_items(
    client: LLMClient,
    query: str,
    source_spans: list[dict[str, Any]],
    rule_items: list[dict[str, Any]],
    *,
    min_confidence: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    valid_span_ids = {str(span.get("id")) for span in source_spans if span.get("id")}
    telemetry: dict[str, Any] = {
        "source": "llm_aggregation",
        "prompt_version": "llm-aggregation-v0",
        "fallback": True,
        "accepted_count": 0,
        "rejected_count": 0,
    }
    if not source_spans or not valid_span_ids:
        telemetry["reason"] = "no_source_spans"
        return [], telemetry
    try:
        response = client.structured(
            prompt=f"llm-aggregation-v0\n\n{LLM_AGGREGATION_PROMPT}",
            schema=LLM_AGGREGATION_SCHEMA,
            input={
                "query": query,
                "source_spans": source_spans[:32],
                "rule_candidates": rule_items[:24],
                "min_confidence": min_confidence,
            },
        )
    except Exception as exc:
        telemetry["reason"] = "llm_call_failed"
        telemetry["error"] = sanitize_error_text(str(exc), limit=200)
        return [], telemetry
    raw_items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(raw_items, list):
        telemetry["reason"] = "invalid_items_payload"
        return [], telemetry
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = _validated_llm_aggregation_item(raw, valid_span_ids, min_confidence=min_confidence)
        if item is None:
            telemetry["rejected_count"] += 1
            continue
        key = str(item["key"])
        if key in seen:
            telemetry["rejected_count"] += 1
            continue
        seen.add(key)
        items.append(item)
    if not items:
        telemetry["reason"] = "no_valid_items"
        return [], telemetry
    telemetry["fallback"] = False
    telemetry["accepted_count"] = len(items)
    telemetry["included_count"] = sum(1 for item in items if item.get("included"))
    return items, telemetry

def _validated_llm_aggregation_item(
    raw: object,
    valid_span_ids: set[str],
    *,
    min_confidence: float,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or "").strip()
    label = str(raw.get("label") or key).strip()
    source_span_id = str(raw.get("source_span_id") or "").strip()
    if not key or len(key) > 120 or not re.match(r"^[a-z][a-z0-9_:-]{1,119}$", key):
        return None
    if not label or len(label) > 160:
        return None
    if source_span_id not in valid_span_ids:
        return None
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        return None
    if confidence < min_confidence:
        return None
    try:
        value = int(float(raw.get("value", 1)))
    except (TypeError, ValueError):
        return None
    included = bool(raw.get("included"))
    count_role = str(raw.get("count_role") or "").strip()
    memory_object_type = str(raw.get("memory_object_type") or "").strip()
    if count_role not in {
        "user_reported_count",
        "additive_item",
        "additive_value",
        "candidate_group_count",
        "assistant_supported_count",
        "excluded",
    }:
        return None
    if memory_object_type not in {
        "user_intent_item",
        "user_reported_aggregate",
        "derived_aggregate_item",
        "assistant_supported_item",
        "excluded_candidate",
    }:
        return None
    item = {
        "key": key,
        "value": value,
        "included": included,
        "memory_object_type": memory_object_type,
        "count_role": count_role if included else "excluded",
        "source_span_id": source_span_id,
        "label": label,
        "confidence": confidence,
        "source": "llm_aggregation",
    }
    reason = str(raw.get("reason") or "").strip()
    if reason:
        item["reason"] = reason[:200]
    return item
