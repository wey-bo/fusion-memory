from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize BEAM answer failures from an existing result JSON.")
    parser.add_argument("result", help="BEAM result JSON with report.answers")
    parser.add_argument("--categories", default="event_ordering,multi_session_reasoning")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    categories = {part.strip() for part in args.categories.split(",") if part.strip()}
    report = build_diagnostics(Path(args.result), categories=categories, limit=args.limit)
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))


def build_diagnostics(path: Path, *, categories: set[str], limit: int) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    answers = _answer_records(data)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for answer in answers:
        category = str(answer.get("category") or "")
        if category in categories:
            grouped[category].append(answer)

    out: dict[str, Any] = {
        "result": str(path),
        "categories": {},
    }
    for category, items in sorted(grouped.items()):
        scored = sorted(items, key=lambda item: _score(item.get("score")))
        scores = [_score(item.get("score")) for item in items]
        out["categories"][category] = {
            "total": len(items),
            "accuracy": sum(scores) / max(1, len(scores)),
            "matched": sum(1 for item in items if item.get("matched_gold")),
            "answer_failed": sum(1 for item in items if item.get("answer_failed")),
            "judge_failed": sum(1 for item in items if _judge_failed(item)),
            "failure_patterns": _failure_patterns(items),
            "low_samples": [_sample(item) for item in scored[:limit]],
        }
    return out


def _answer_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        data.get("report", {}).get("answers") if isinstance(data.get("report"), dict) else None,
        data.get("answers"),
        data.get("results"),
        data.get("records"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _sample(answer: dict[str, Any]) -> dict[str, Any]:
    pack = answer.get("evidence_pack") if isinstance(answer.get("evidence_pack"), dict) else {}
    coverage = pack.get("coverage") if isinstance(pack.get("coverage"), dict) else {}
    return {
        "query_id": answer.get("query_id"),
        "score": answer.get("score"),
        "matched_gold": answer.get("matched_gold"),
        "answer_failed": answer.get("answer_failed"),
        "judge_failed": _judge_failed(answer),
        "failure_pattern": _failure_pattern(answer),
        "query": answer.get("query_text"),
        "answer": answer.get("answer"),
        "judge_reason": answer.get("judge_reason"),
        "pack": {
            "query_type": coverage.get("query_type"),
            "source_span_count": pack.get("source_span_count"),
            "event_count": pack.get("event_count"),
            "fact_count": pack.get("fact_count"),
            "timeline_basis": coverage.get("timeline_basis"),
            "timeline_span_count": coverage.get("timeline_span_count"),
            "coverage_insufficient": coverage.get("coverage_insufficient"),
            "source_span_ids": (pack.get("source_span_ids") or [])[:12],
        },
    }


def _failure_patterns(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        if _score(item.get("score")) >= 0.5:
            continue
        counts[_failure_pattern(item)] += 1
    return dict(sorted(counts.items()))


def _failure_pattern(answer: dict[str, Any]) -> str:
    if answer.get("answer_failed"):
        return "answer_failed"
    if _judge_failed(answer):
        return "judge_failed"
    category = str(answer.get("category") or "")
    answer_text = str(answer.get("answer") or "")
    lower_answer = answer_text.lower()
    if "not enough" in lower_answer or "don't have" in lower_answer or "do not have" in lower_answer or "cannot support" in lower_answer:
        return "abstention_or_missing_evidence"
    if category == "event_ordering":
        ordered_count = len(_ordered_answer_items(answer_text))
        requested = _requested_count(str(answer.get("query_text") or ""))
        if requested and ordered_count and ordered_count != requested:
            return "event_wrong_item_count"
        if re_search(r"\b(?:never|not yet|do i need|should i|can i still|what are some|leaving work behind)\b", lower_answer):
            return "event_non_event_or_question_fragment"
        if re_search(r"\b(?:concern|worried|stress|burnout|workload|vacation|partner|anniversary)\b", lower_answer) and re_search(
            r"\b(?:resume|profile|portfolio|hiring|framework|api|budget|financial)\b",
            str(answer.get("query_text") or "").lower(),
        ):
            return "event_topic_drift"
        return "event_order_or_label_mismatch"
    if category == "multi_session_reasoning":
        if re_search(r"\b(?:total|different|count|how many|number)\b", str(answer.get("query_text") or "").lower()):
            return "multi_session_count_or_dedup"
        return "multi_session_synthesis"
    return "low_score"


def _judge_failed(answer: dict[str, Any]) -> bool:
    if answer.get("judge_failed"):
        return True
    reason = str(answer.get("judge_reason") or "").lower()
    return "rubric scoring failed" in reason or "judge scoring failed" in reason


def _ordered_answer_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*(?:\d+[.)]\s*|[-*]\s*)", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


def _requested_count(query: str) -> int | None:
    lower = query.lower()
    words = {
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
    for word, value in words.items():
        if re_search(rf"\b{word}\b", lower):
            return value
    match = re.search(r"\b(\d{1,2})\s+items?\b", lower)
    return int(match.group(1)) if match else None


def re_search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.I) is not None


def _score(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
