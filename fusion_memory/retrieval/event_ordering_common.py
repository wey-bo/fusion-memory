from __future__ import annotations

import re
from typing import Any


def _event_ordering_record_sort_key(record: dict[str, Any]) -> tuple[int, tuple[tuple[int, int | str], ...], tuple[tuple[int, int | str], ...], str, int]:
    source_uri = record.get("source_uri")
    turn_id = record.get("turn_id")
    if source_uri or turn_id:
        return (
            0,
            _event_ordering_natural_key(source_uri),
            _event_ordering_natural_key(turn_id),
            str(record.get("timestamp") or ""),
            int(record.get("timeline_index") or record.get("_sort_index") or 0),
        )
    return (
        1,
        (),
        (),
        str(record.get("timestamp") or ""),
        int(record.get("timeline_index") or record.get("_sort_index") or 0),
    )


def _event_ordering_sequence_output_sort_key(record: dict[str, Any]) -> tuple[int, tuple[tuple[int, int | str], ...], tuple[tuple[int, int | str], ...], str]:
    source_uri = record.get("source_uri")
    turn_id = record.get("turn_id")
    timeline_index = _safe_int(record.get("timeline_index") or record.get("history_index") or record.get("_sort_index"))
    if source_uri or turn_id:
        return (
            0,
            _event_ordering_natural_key(source_uri),
            _event_ordering_natural_key(turn_id),
            str(record.get("source_span_id") or record.get("timestamp") or ""),
        )
    return (
        timeline_index if timeline_index > 0 else 10**9,
        (),
        (),
        str(record.get("source_span_id") or record.get("timestamp") or ""),
    )


def _event_ordering_natural_key(value: object) -> tuple[tuple[int, int | str], ...]:
    text = "" if value is None else str(value)
    parts: list[tuple[int, int | str]] = []
    for part in re.split(r"(\d+)", text):
        if not part:
            continue
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _requested_event_ordering_count(query: str) -> int | None:
    lower = query.lower()
    word_numbers = {
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
    match = re.search(
        r"\b(?:only|exactly|mention|list|name)\s+(?:and\s+)?(?:only\s+)?"
        r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        lower,
    )
    if not match:
        match = re.search(
            r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
            r"(?:items?|aspects?|steps?|phases?)\b",
            lower,
        )
    if not match:
        return None
    raw = match.group(1)
    try:
        value = int(raw)
    except ValueError:
        value = word_numbers.get(raw)
    if value is None:
        return None
    return max(1, min(value, 12))
