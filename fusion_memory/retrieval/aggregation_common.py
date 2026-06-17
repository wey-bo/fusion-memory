from __future__ import annotations

from typing import Any

from fusion_memory.core.text import compact_summary


def _match_context(content: str, start: int, end: int, *, radius: int = 120) -> str:
    return content[max(0, start - radius) : min(len(content), end + radius)].strip()


def _span_ref(span: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_span_id": span.get("id"),
        "turn_id": span.get("turn_id"),
        "speaker": span.get("speaker"),
        "history_index": span.get("history_index"),
    }


def _append_aggregation_item(
    items: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
    key: str,
    value: int,
    context: str,
    span_ref: dict[str, Any],
    *,
    included: bool,
    reason: str | None = None,
    dedupe_by_key: bool = False,
    label: str | None = None,
) -> None:
    dedupe = (key, "" if dedupe_by_key else str(span_ref.get("turn_id") or span_ref.get("source_span_id") or ""), value)
    key_dupe = next((index for index, item in enumerate(items) if item.get("key") == key and item.get("value") == value and not item.get("included") and included), None)
    if key_dupe is not None:
        items.pop(key_dupe)
        seen.difference_update({entry for entry in seen if entry[0] == key and entry[2] == value})
    elif dedupe in seen:
        return
    seen.add(dedupe)
    item = {
        "key": key,
        "value": value,
        "included": included,
        "memory_object_type": _aggregation_item_object_type(key, span_ref, included=included),
        "count_role": _aggregation_item_count_role(key, span_ref, included=included),
        "context": compact_summary(context, 260),
        **{key: value for key, value in span_ref.items() if value is not None},
    }
    if label:
        item["label"] = label
    if reason:
        item["reason"] = reason
    items.append(item)


def _aggregation_item_object_type(key: str, span_ref: dict[str, Any], *, included: bool) -> str:
    speaker = str(span_ref.get("speaker") or "")
    if key.startswith("group_count:"):
        return "assistant_recommendation_group"
    if key.startswith("count_hint:"):
        return "user_reported_aggregate" if speaker in {"user", "document"} else "assistant_supported_aggregate"
    if key.startswith("excluded:") or not included:
        return "excluded_candidate"
    if key.startswith(("title:", "genre:", "value:", "column:", "area:", "feature:", "security_feature:", "role:", "request:", "application_type:", "plan_system:", "asset:", "vendor_tool:", "item:", "generic:")):
        return "user_intent_item" if speaker in {"user", "document"} else "assistant_supported_item"
    return "derived_aggregate_item"


def _aggregation_item_count_role(key: str, span_ref: dict[str, Any], *, included: bool) -> str:
    speaker = str(span_ref.get("speaker") or "")
    if not included or key.startswith("excluded:"):
        return "excluded"
    if key.startswith("group_count:"):
        return "candidate_group_count"
    if key.startswith("count_hint:"):
        return "user_reported_count" if speaker in {"user", "document"} else "assistant_supported_count"
    if key.startswith(("title:", "genre:", "value:", "column:", "area:", "feature:", "security_feature:", "role:", "request:", "application_type:", "plan_system:", "asset:", "vendor_tool:", "item:", "generic:")):
        return "additive_item"
    return "additive_value"
