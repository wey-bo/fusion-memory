from __future__ import annotations

import re

from fusion_memory.core.models import QueryPlan
from fusion_memory.core.text import extract_entities


EVENT_ORDERING_PHRASES = (
    "order in which",
    "in order",
    "chronological",
    "chronology",
    "sequence",
    "timeline",
)
TEMPORAL_HINTS = (
    "when",
    "first",
    "before",
    "after",
    "previously",
    "yesterday",
    "today",
    "tomorrow",
    "last week",
    "this week",
    "next week",
    "last month",
    "this month",
    "next month",
    "deadline",
    "duration",
    "how many weeks",
    "how many days",
    "between",
    "weeks between",
    "days between",
    "this monday",
    "this tuesday",
    "this wednesday",
    "this thursday",
    "this friday",
    "this saturday",
    "this sunday",
    "next monday",
    "next tuesday",
    "next wednesday",
    "next thursday",
    "next friday",
    "next saturday",
    "next sunday",
    "什么时候",
    "之前",
    "之后",
    "先",
)
RETRIEVAL_STOPWORDS = {
    "a",
    "across",
    "all",
    "and",
    "answer",
    "aspect",
    "aspects",
    "brought",
    "can",
    "chats",
    "conversations",
    "could",
    "different",
    "during",
    "each",
    "five",
    "four",
    "how",
    "i",
    "in",
    "into",
    "items",
    "list",
    "me",
    "mention",
    "only",
    "order",
    "our",
    "please",
    "show",
    "tell",
    "the",
    "their",
    "them",
    "these",
    "things",
    "through",
    "throughout",
    "three",
    "up",
    "walk",
    "was",
    "we",
    "which",
    "you",
}


class QueryPlanner:
    def plan(self, query: str) -> QueryPlan:
        lower = query.lower()
        query_type = "factual_exact"
        needs_current = False
        must_include = ["raw_evidence"]
        if any(w in lower for w in ["current", "currently", "now", "现在", "当前", "以后按"]):
            if any(w in lower for w in ["prefer", "preference", "喜欢", "偏好", "用什么"]):
                query_type = "preference"
                must_include = ["current_view", "raw_evidence"]
            elif _is_current_value_query(lower):
                query_type = "knowledge_update"
                must_include = ["raw_evidence", "facts", "events", "current_view"]
            else:
                query_type = "instruction"
                must_include = ["current_view", "raw_evidence"]
            needs_current = True
        if _is_event_ordering_query(lower):
            query_type = "event_ordering"
            must_include = ["raw_evidence", "events"]
        elif any(w in lower for w in TEMPORAL_HINTS) and not _is_historical_yes_no_query(lower):
            query_type = "temporal_lookup"
            must_include = ["raw_evidence", "events"]
        if query_type == "factual_exact" and _is_multi_session_query(lower):
            query_type = "multi_session_reasoning"
            must_include = ["raw_evidence", "facts"]
        if _is_historical_yes_no_query(lower) or any(w in lower for w in ["contradict", "conflict", "changed", "switched", "矛盾", "冲突", "改"]):
            query_type = "contradiction_resolution"
            must_include = ["raw_evidence", "facts"]
        if query_type == "factual_exact" and _is_current_value_query(lower):
            query_type = "knowledge_update"
            must_include = ["raw_evidence", "facts", "events"]
        if any(w in lower for w in ["unknown", "not mentioned", "没有提到", "不知道", "cluster name"]):
            query_type = "abstention"
            must_include = ["raw_evidence"]
        if any(w in lower for w in ["summarize", "summary", "总结"]):
            query_type = "summarization"
            must_include = ["raw_evidence"]
        speaker_focus = "any"
        if query_type == "event_ordering" and any(phrase in lower for phrase in ["i brought up", "i mentioned", "i asked", "my "]):
            speaker_focus = "user"
        if any(w in lower for w in ["you suggested", "assistant", "你建议", "上次你"]):
            speaker_focus = "assistant"
            query_type = "assistant_reference"
        return QueryPlan(
            query=query,
            query_type=query_type,
            entities=extract_entities(query),
            time_constraints=_time_constraints(query),
            retrieval_hints=_retrieval_hints(query),
            speaker_focus=speaker_focus,
            needs_current_state=needs_current,
            needs_source_evidence=True,
            must_include_sources=must_include,
        )


def _is_event_ordering_query(lower: str) -> bool:
    if any(phrase in lower for phrase in EVENT_ORDERING_PHRASES):
        return True
    if any(marker in lower for marker in ["what order", "first came up", "brought up"]) and "conversation" in lower:
        return True
    if ("first" in lower and "then" in lower) or ("first" in lower and "next" in lower):
        return True
    return False


def _is_historical_yes_no_query(lower: str) -> bool:
    return bool(
        re.search(r"\bhave\s+i\s+(?:ever\s+)?(?:used|worked|read|listened|met|done|tried|mentioned)\b", lower)
        or re.search(r"\bdid\s+i\s+(?:ever\s+)?(?:use|work|read|listen|meet|do|try|mention)\b", lower)
    )


def _is_multi_session_query(lower: str) -> bool:
    return bool(
        re.search(r"\b(?:across|throughout|over time|in total|total after|how many different|between my .+ and my .+|considering .+ and .+)\b", lower)
        or (re.search(r"\bhow many\b", lower) and re.search(r"\b(?:requests?|conversations?|sessions?|features?|concerns?|columns?|cards?|sources?)\b", lower))
    )


def _is_current_value_query(lower: str) -> bool:
    return bool(
        re.search(r"\b(?:current|currently|latest|recent|recently|now|final|updated|reached|improved|reduced|deadline|average response time|how many commits|what is the average|what deadline|what version)\b", lower)
        or re.search(r"\bwhat\s+is\s+(?:the\s+)?(?:average|deadline|count|number|version|status|response time)\b", lower)
    )


def _retrieval_hints(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", query.lower())
    hints: list[str] = []
    for token in tokens:
        if len(token) < 3:
            continue
        if token in RETRIEVAL_STOPWORDS:
            continue
        hints.append(token)
    return list(dict.fromkeys(hints[:8]))


def _time_constraints(query: str) -> list[dict]:
    lower = query.lower()
    out: list[dict] = []
    for phrase in [
        "last week",
        "this week",
        "next week",
        "last month",
        "this month",
        "next month",
        "yesterday",
        "today",
        "tomorrow",
        "this monday",
        "this tuesday",
        "this wednesday",
        "this thursday",
        "this friday",
        "this saturday",
        "this sunday",
        "next monday",
        "next tuesday",
        "next wednesday",
        "next thursday",
        "next friday",
        "next saturday",
        "next sunday",
    ]:
        if phrase in lower:
            out.append({"type": "relative", "text": phrase})
    explicit = re.findall(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", query)
    out.extend({"type": "explicit", "text": item} for item in explicit)
    month_dates = re.findall(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d{2})?\b",
        query,
        flags=re.I,
    )
    out.extend({"type": "explicit", "text": item} for item in month_dates)
    return out
