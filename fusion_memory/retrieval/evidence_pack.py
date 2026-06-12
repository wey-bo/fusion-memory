from __future__ import annotations

import re
from datetime import datetime, timezone

from fusion_memory.core.config import DEFAULT_CONFIG, MemoryConfig
from fusion_memory.core.models import Candidate, EvidencePack, QueryPlan
from fusion_memory.core.text import compact_summary, tokenize
from fusion_memory.storage.sqlite_store import SQLiteMemoryStore


class EvidencePackBuilder:
    def __init__(self, store: SQLiteMemoryStore, config: MemoryConfig | None = None) -> None:
        self.store = store
        self.config = config or DEFAULT_CONFIG

    def build(
        self,
        query: str,
        plan: QueryPlan,
        candidates: list[Candidate],
        coverage: dict,
        trace: list[dict],
        token_budget: int | None = None,
    ) -> EvidencePack:
        token_budget = token_budget or self.config.answer_context_budget_tokens
        current_views: list[dict] = []
        profiles: list[dict] = []
        facts: list[dict] = []
        events: list[dict] = []
        spans: list[dict] = []
        conflicts: list[dict] = []
        seen_spans: set[str] = set()
        estimated_tokens = 0
        selected_scope = None
        for candidate in candidates:
            if candidate.type == "view":
                current_views.append({"id": candidate.id, "text": candidate.text, "source_span_ids": candidate.source_span_ids})
            elif candidate.type == "profile":
                profiles.append({"id": candidate.id, "text": candidate.text, "source_span_ids": candidate.source_span_ids})
            elif candidate.type == "fact":
                facts.append({"id": candidate.id, "text": candidate.text, "source_span_ids": candidate.source_span_ids})
            elif candidate.type == "event":
                events.append(
                    {
                        "id": candidate.id,
                        "description": candidate.text,
                        "text": candidate.text,
                        "event_type": candidate.metadata.get("event_type"),
                        "milestone_group": candidate.metadata.get("milestone_group"),
                        "time_start": candidate.metadata.get("time_start"),
                        "source_span_ids": candidate.source_span_ids,
                    }
                )
            for span_id in candidate.source_span_ids:
                if span_id in seen_spans:
                    continue
                span = self.store.get_span(span_id)
                if not span:
                    continue
                selected_scope = selected_scope or span.scope
                seen_spans.add(span_id)
                record, estimated_tokens = self._span_record(query, plan, span, estimated_tokens, token_budget)
                if record:
                    spans.append(record)
        if selected_scope:
            expanded, estimated_tokens = self._expand_category_spans(
                query,
                plan,
                selected_scope,
                spans,
                seen_spans,
                estimated_tokens,
                token_budget,
            )
            spans.extend(expanded)
        if plan.query_type == "contradiction_resolution":
            conflicts = _contradiction_claim_buckets(query, spans, facts)
        elif plan.query_type == "knowledge_update":
            conflicts = [
                {"fact_id": fact["id"], "source_span_ids": fact["source_span_ids"]}
                for fact in facts[:4]
            ]
        if plan.query_type == "event_ordering":
            events.sort(key=self._event_timeline_sort_key)
            for index, event in enumerate(events, start=1):
                event["timeline_index"] = index
            spans.sort(key=_timeline_sort_key)
            for index, span in enumerate(spans, start=1):
                span["timeline_index"] = index
        if plan.query_type in {"knowledge_update", "multi_session_reasoning"}:
            spans.sort(key=_timeline_sort_key)
            for index, span in enumerate(spans, start=1):
                span["history_index"] = index
            if plan.query_type == "knowledge_update":
                for recency_rank, span in enumerate(reversed(spans), start=1):
                    span["recency_rank"] = recency_rank
                    values = _value_mentions(span.get("content", ""))
                    if values:
                        span["value_mentions"] = values
                spans.sort(key=lambda span: int(span.get("recency_rank") or 10**9))
        if plan.query_type == "temporal_lookup":
            temporal_role_counts: dict[str, int] = {}
            for span in spans:
                mentions = _temporal_mentions(query, span.get("content", ""), span.get("timestamp"))
                if mentions:
                    span["temporal_mentions"] = mentions
                    span["temporal_roles"] = list(dict.fromkeys(mention["role"] for mention in mentions))
                    for mention in mentions:
                        role = str(mention["role"])
                        temporal_role_counts[role] = temporal_role_counts.get(role, 0) + 1
        answer_policy = "answer_with_evidence_or_abstain"
        if plan.query_type == "abstention" or coverage.get("coverage_insufficient"):
            answer_policy = "abstain_if_not_supported"
        coverage = {
            **coverage,
            "token_budget": token_budget,
            "estimated_source_tokens": estimated_tokens,
            "timeline_span_count": len(spans) if plan.query_type == "event_ordering" else 0,
        }
        if plan.query_type == "event_ordering":
            coverage["timeline_basis"] = "conversation_order"
        if plan.query_type == "temporal_lookup":
            coverage["temporal_role_counts"] = temporal_role_counts
            coverage["temporal_target_roles"] = _temporal_target_roles(query)
        format_requirements = _format_requirements(query)
        if format_requirements:
            coverage["format_requirements"] = format_requirements
        if plan.query_type == "contradiction_resolution" and conflicts:
            coverage["claim_polarity_counts"] = {
                "positive": len(conflicts[0].get("positive_source_span_ids", [])),
                "negative": len(conflicts[0].get("negative_source_span_ids", [])),
                "uncertain": len(conflicts[0].get("uncertain_source_span_ids", [])),
            }
        return EvidencePack(
            query=query,
            answer_policy=answer_policy,
            current_views=current_views,
            entity_profiles=profiles,
            facts=facts,
            events=events,
            source_spans=spans,
            conflicts=conflicts,
            coverage=coverage,
            debug_trace=trace,
        )

    def _span_record(
        self,
        query: str,
        plan: QueryPlan,
        span,
        estimated_tokens: int,
        token_budget: int,
    ) -> tuple[dict | None, int]:
        content_limit = self.config.evidence_span_summary_chars
        content = (
            _temporal_summary(query, span.content, max(content_limit, 1200))
            if plan.query_type == "temporal_lookup"
            else compact_summary(span.content, content_limit)
        )
        content_tokens = len(tokenize(content))
        if estimated_tokens + content_tokens > token_budget:
            remaining = max(0, token_budget - estimated_tokens)
            if remaining <= 0:
                return None, estimated_tokens
            words = content.split()
            content = " ".join(words[:remaining])
            content_tokens = len(tokenize(content))
        estimated_tokens += content_tokens
        return (
            {
                "id": span.span_id,
                "session_id": span.scope.session_id,
                "turn_id": span.turn_id,
                "source_uri": span.source_uri,
                "speaker": span.speaker,
                "timestamp": span.timestamp.isoformat(),
                "content": content,
                "topic_group": _span_group_key(span.source_uri, span.turn_id),
            },
            estimated_tokens,
        )

    def _expand_category_spans(
        self,
        query: str,
        plan: QueryPlan,
        scope,
        current_spans: list[dict],
        seen_spans: set[str],
        estimated_tokens: int,
        token_budget: int,
    ) -> tuple[list[dict], int]:
        mode = _pack_expansion_mode(query, plan.query_type)
        if not mode:
            return [], estimated_tokens
        groups = {str(span.get("topic_group") or "") for span in current_spans if span.get("topic_group")}
        if not groups:
            return [], estimated_tokens
        candidates = [
            span
            for span in self.store.list_spans(scope)
            if span.span_id not in seen_spans
            and span.span_type in {"turn", "tool_result", "document_chunk"}
            and _span_group_key(span.source_uri, span.turn_id) in groups
        ]
        if not candidates:
            return [], estimated_tokens
        scored: list[tuple[float, object]] = []
        target_roles = set(_temporal_target_roles(query)) if mode == "temporal" else set()
        for span in candidates:
            score = _topic_scope_score(query, span.content)
            exact = _exact_overlap(query, span.content)
            date_signal = _date_signal(span.content)
            role_signal = 0.0
            if target_roles:
                roles = _temporal_roles_in_text(query, span.content)
                if roles & target_roles:
                    role_signal = 0.55 + 0.15 * min(len(roles & target_roles), 2)
            speaker_signal = 0.12 if span.speaker == "user" else 0.04
            if mode == "summary":
                total = (0.58 * score) + (0.20 * exact) + speaker_signal
            elif mode == "temporal":
                total = (0.36 * score) + (0.22 * exact) + (0.30 * role_signal) + (0.12 * date_signal)
            elif mode == "ordering":
                total = (0.50 * score) + (0.18 * exact) + (0.20 if span.speaker == "user" else 0.04)
            else:
                total = (0.48 * score) + (0.30 * exact) + (0.12 * date_signal) + speaker_signal
            if total > 0.08:
                scored.append((total, span))
        if not scored:
            return [], estimated_tokens
        if mode in {"summary", "ordering"}:
            scored.sort(key=lambda item: (_timeline_sort_key(_span_sort_record(item[1])), -item[0]))
        else:
            scored.sort(key=lambda item: (item[0], _reverse_timeline_key(_span_sort_record(item[1]))), reverse=True)
        max_total = {
            "summary": 20,
            "temporal": 18,
            "ordering": 20,
            "broad": 16,
            "exact": 14,
        }.get(mode, 14)
        out: list[dict] = []
        for _score, span in scored:
            if len(current_spans) + len(out) >= max_total:
                break
            record, estimated_tokens = self._span_record(query, plan, span, estimated_tokens, token_budget)
            if not record:
                break
            record["category_expansion"] = mode
            out.append(record)
            seen_spans.add(span.span_id)
        return out, estimated_tokens

    def _event_timeline_sort_key(self, event: dict) -> tuple[int, tuple[tuple[int, int | str], ...], tuple[tuple[int, int | str], ...], str, str]:
        span_id = next(iter(event.get("source_span_ids") or []), None)
        span = self.store.get_span(span_id) if span_id else None
        if span:
            return (
                0,
                _natural_turn_key(span.source_uri),
                _natural_turn_key(span.turn_id),
                span.timestamp.isoformat(),
                str(event.get("id") or ""),
            )
        return (
            1,
            (),
            (),
            str(event.get("time_start") or ""),
            str(event.get("id") or ""),
        )


def _timeline_sort_key(span: dict) -> tuple[int, tuple[tuple[int, int | str], ...], tuple[tuple[int, int | str], ...], str, str]:
    return (
        0 if span.get("source_uri") or span.get("turn_id") else 1,
        _natural_turn_key(span.get("source_uri")),
        _natural_turn_key(span.get("turn_id")),
        str(span.get("timestamp") or ""),
        str(span.get("id") or ""),
    )


def _natural_turn_key(value: object) -> tuple[tuple[int, int | str], ...]:
    text = "" if value is None else str(value)
    parts: list[tuple[int, int | str]] = []
    for part in re.split(r"(\d+)", text):
        if not part:
            continue
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)


def _span_sort_record(span) -> dict:
    return {
        "source_uri": span.source_uri,
        "turn_id": span.turn_id,
        "timestamp": span.timestamp.isoformat(),
        "id": span.span_id,
    }


def _reverse_timeline_key(span: dict) -> tuple[int, ...]:
    encoded = "|".join(str(part) for part in _timeline_sort_key(span))
    return tuple(-ord(char) for char in encoded)


def _span_group_key(source_uri: object, turn_id: object) -> str:
    for value in (source_uri, turn_id):
        if not value:
            continue
        text = str(value)
        match = re.match(r"^(beam:[^:]+:\d+):", text)
        if match:
            return match.group(1)
        if "#" in text:
            return text.split("#", 1)[0]
    return ""


def _pack_expansion_mode(query: str, query_type: str) -> str | None:
    lower = query.lower()
    if query_type == "event_ordering":
        return "ordering"
    if query_type == "temporal_lookup":
        return "temporal"
    if query_type == "summarization":
        return "summary"
    if query_type in {"contradiction_resolution", "knowledge_update"}:
        return "broad"
    if query_type == "factual_exact" and re.search(r"\b(?:across|throughout|over time|different|total|how many|between)\b", lower):
        return "broad"
    if query_type == "factual_exact":
        return "exact"
    return None


def _contradiction_claim_buckets(query: str, spans: list[dict], facts: list[dict]) -> list[dict]:
    positive: list[str] = []
    negative: list[str] = []
    uncertain: list[str] = []
    for span in spans:
        polarity = _claim_polarity(query, span.get("content", ""))
        span_id = span.get("id")
        if not span_id:
            continue
        span["claim_polarity"] = polarity
        if polarity == "positive":
            positive.append(str(span_id))
        elif polarity == "negative":
            negative.append(str(span_id))
        else:
            uncertain.append(str(span_id))
    return [
        {
            "type": "claim_polarity_buckets",
            "positive_source_span_ids": positive[:8],
            "negative_source_span_ids": negative[:8],
            "uncertain_source_span_ids": uncertain[:8],
            "fact_source_span_ids": [span_id for fact in facts[:6] for span_id in fact.get("source_span_ids", [])],
            "note": "Buckets organize retrieved raw claims by surface polarity; they do not decide the answer.",
        }
    ]


def _claim_polarity(query: str, content: str) -> str:
    lower = content.lower()
    query_tokens = _topic_scope_tokens(query)
    text_tokens = _topic_scope_tokens(content)
    if query_tokens and len(query_tokens & text_tokens) == 0:
        return "uncertain"
    negative_patterns = [
        r"\bnever\b",
        r"\bnot\s+(?:yet\s+)?(?:used|integrated|worked|read|listened|met|downloaded|placed|attended|completed|tested|made|drafted|managed)\b",
        r"\bhaven['’]?t\b",
        r"\bhave\s+not\b",
        r"\bno\s+experience\b",
        r"\bwithout\s+(?:using|having|integrating|testing)\b",
    ]
    positive_patterns = [
        r"\b(?:used|integrated|worked|read|listened|met|downloaded|placed|attended|completed|tested|made|drafted|managed)\b",
        r"\bstarted\s+(?:using|listening|reading|working|testing)\b",
        r"\bhas\s+been\s+(?:used|integrated|tested|completed)\b",
        r"\balready\s+(?:used|integrated|tested|completed|started|drafted)\b",
    ]
    neg = any(re.search(pattern, lower) for pattern in negative_patterns)
    pos = any(re.search(pattern, lower) for pattern in positive_patterns)
    if neg:
        return "negative"
    if pos:
        return "positive"
    return "uncertain"


def _value_mentions(content: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    patterns = [
        ("date", r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b"),
        (
            "date",
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d{2})?\b",
        ),
        ("duration", r"\b\d+(?:\.\d+)?\s*(?:days?|weeks?|months?|hours?|minutes?)\b"),
        ("latency", r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?)\b"),
        ("version", r"\bv?\d+\.\d+(?:\.\d+)?\b"),
        ("count", r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:commits?|cards?|columns?|features?|concerns?|percent|%)\b"),
    ]
    for kind, pattern in patterns:
        for match in re.finditer(pattern, content, flags=re.I):
            out.append({"type": kind, "text": match.group(0), "context": compact_summary(_mention_context(content, match.start(), match.end()), 180)})
            if len(out) >= 12:
                return out
    return out


def _format_requirements(query: str) -> list[str]:
    lower = query.lower()
    requirements: list[str] = []
    if "only and only" in lower or re.search(r"\bmention only\b", lower):
        requirements.append("exact_item_count_or_only_constraint")
    if re.search(r"\b(?:code|function|snippet|program)\b", lower):
        requirements.append("code_or_snippet_expected")
    if "```" in query or "fenced" in lower:
        requirements.append("fenced_code_block")
    if re.search(r"\b(?:tree drawing|diagram|table|bullet|list)\b", lower):
        requirements.append("specific_visual_or_list_format")
    if re.search(r"\b(?:version|libraries|dependencies)\b", lower):
        requirements.append("include_exact_versions_if_supported")
    return requirements


TOPIC_SCOPE_STOPWORDS = {
    "about",
    "across",
    "after",
    "also",
    "and",
    "answer",
    "aspect",
    "aspects",
    "before",
    "been",
    "between",
    "brought",
    "can",
    "challenge",
    "challenges",
    "chat",
    "chats",
    "comprehensive",
    "conversation",
    "conversations",
    "deadline",
    "deadlines",
    "developed",
    "development",
    "different",
    "does",
    "during",
    "each",
    "for",
    "feature",
    "features",
    "final",
    "finish",
    "finished",
    "finishing",
    "from",
    "give",
    "have",
    "have",
    "help",
    "how",
    "include",
    "including",
    "into",
    "item",
    "items",
    "key",
    "list",
    "many",
    "management",
    "mention",
    "mentioned",
    "need",
    "only",
    "order",
    "our",
    "over",
    "project",
    "projects",
    "request",
    "requests",
    "should",
    "so",
    "summary",
    "target",
    "targets",
    "the",
    "through",
    "throughout",
    "used",
    "using",
    "walk",
    "want",
    "wanted",
    "way",
    "ways",
    "week",
    "weeks",
    "what",
    "which",
    "with",
    "work",
    "worked",
    "you",
}

TOPIC_SCOPE_EQUIVALENTS = {
    "auth": {"auth", "authentication", "login", "logout", "session"},
    "authentication": {"auth", "authentication", "login", "logout", "session"},
    "columns": {"column", "columns", "field", "fields"},
    "deadline": {"deadline", "deadlines", "due", "target"},
    "deployment": {"deployment", "deploy", "deployed", "launch", "production", "render", "gunicorn"},
    "features": {"feature", "features", "module", "modules", "functionality"},
    "financial": {"financial", "finance", "budget", "money", "cost", "costs", "income", "expense", "expenses"},
    "finish": {"finish", "finished", "complete", "completed", "completion", "end", "ended"},
    "latency": {"latency", "response", "time", "ms", "milliseconds"},
    "profession": {"profession", "job", "career", "role", "work"},
    "sprint": {"sprint", "sprints", "phase", "milestone"},
    "stress": {"stress", "stressed", "burnout", "overwhelmed", "workload"},
    "transaction": {"transaction", "transactions", "crud", "income", "expense", "expenses"},
}


def _topic_scope_score(query: str, text: str) -> float:
    query_tokens = _topic_scope_tokens(query)
    if not query_tokens:
        return 0.0
    text_tokens = _topic_scope_tokens(text)
    if not text_tokens:
        return 0.0
    direct = len(query_tokens & text_tokens) / max(1, len(query_tokens))
    expanded = len(_expand_topic_tokens(query_tokens) & _expand_topic_tokens(text_tokens)) / max(1, len(_expand_topic_tokens(query_tokens)))
    return min(1.0, (0.70 * direct) + (0.30 * expanded))


def _topic_scope_tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]*|\d+(?:\.\d+)?", text.lower())
    tokens: set[str] = set()
    for token in raw:
        token = token.strip("_+-")
        if len(token) < 3 or token in TOPIC_SCOPE_STOPWORDS:
            continue
        tokens.add(token)
        if token.endswith("s") and len(token) > 4:
            tokens.add(token[:-1])
        if token.endswith("ing") and len(token) > 6:
            tokens.add(token[:-3])
        if token.endswith("ed") and len(token) > 5:
            tokens.add(token[:-2])
    return tokens


def _expand_topic_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in list(tokens):
        equivalents = TOPIC_SCOPE_EQUIVALENTS.get(token)
        if equivalents:
            expanded.update(equivalents)
    return expanded


def _exact_overlap(query: str, text: str) -> float:
    query_tokens = _topic_scope_tokens(query)
    if not query_tokens:
        return 0.0
    text_tokens = _topic_scope_tokens(text)
    return len(query_tokens & text_tokens) / max(1, len(query_tokens))


def _date_signal(text: str) -> float:
    lower = text.lower()
    if re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", lower):
        return 1.0
    if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d{2})?\b", lower):
        return 0.9
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:days?|weeks?|months?)\b", lower):
        return 0.55
    return 0.0


def _temporal_roles_in_text(query: str, text: str) -> set[str]:
    lower = text.lower()
    roles: set[str] = set()
    if ("deployment" in lower or "deploy" in lower or "launch" in lower or "production" in lower) and (
        "deadline" in lower or "by " in lower or "target" in lower or _date_signal(lower)
    ):
        roles.add("deployment_deadline")
    if (
        re.search(r"\bfinish|finished|complete|completed|completion|end|ended\b", lower)
        and ("feature" in lower or "features" in lower or len(_topic_scope_tokens(query) & _topic_scope_tokens(text)) >= 2)
    ):
        roles.add("feature_finish_date")
    if "sprint" in lower and re.search(r"\bend|ends|ended|first\b", lower):
        roles.add("sprint_end_date")
    if re.search(r"\bstart|starts|started|begin|begins\b", lower):
        roles.add("start_date")
    return roles


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
TEMPORAL_DATE_RE = re.compile(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b")
TEMPORAL_MONTH_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d{2})?\b",
    re.I,
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")
EXPLICIT_MONTH_DAY_YEAR_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))\b",
    re.I,
)
TEMPORAL_STOPWORDS = {
    "about",
    "after",
    "before",
    "between",
    "date",
    "dates",
    "deadline",
    "deadlines",
    "did",
    "feature",
    "features",
    "final",
    "complete",
    "completed",
    "completing",
    "completion",
    "finish",
    "finishing",
    "first",
    "have",
    "how",
    "many",
    "need",
    "second",
    "sprint",
    "sprints",
    "time",
    "weeks",
    "what",
    "when",
    "which",
}
DEPLOYMENT_TERMS = {
    "deploy",
    "deployed",
    "deploying",
    "deployment",
    "launch",
    "launched",
    "release",
    "released",
    "ship",
    "shipping",
    "production",
    "rollout",
}
FINAL_TERMS = {"final", "production", "launch", "release", "ship", "deployment"}
MVP_TERMS = {"mvp", "prototype", "scope", "minimum viable"}
DEADLINE_TERMS = {"deadline", "deadlines", "due", "target date", "by "}
COMPLETION_TERMS = {"finish", "finishing", "finished", "complete", "completed", "completing", "completion", "done", "end", "ends", "ending"}
START_TERMS = {"start", "starts", "started", "starting", "begin", "begins", "began", "from"}
FEATURE_TERMS = {
    "feature",
    "features",
    "implementation",
    "phase",
    "milestone",
    "work",
    "module",
    "component",
    "transaction",
    "management",
}


def _temporal_mentions(query: str, content: str, span_timestamp: object = None) -> list[dict[str, object]]:
    if not content:
        return []
    query_lower = query.lower()
    default_year = _default_year(span_timestamp)
    mentions: list[dict[str, object]] = []
    for match in list(TEMPORAL_DATE_RE.finditer(content)) + list(TEMPORAL_MONTH_RE.finditer(content)):
        text = match.group(0)
        context = _mention_context(content, match.start(), match.end())
        role_context = _role_context(content, match.start(), match.end())
        endpoint = _range_endpoint(content, match.start(), match.end())
        role, confidence = _temporal_role(query_lower, role_context.lower(), endpoint)
        normalized_date = _normalize_date_text(text, _infer_year_for_match(content, match.start(), match.end(), default_year))
        mention: dict[str, object] = {
            "text": text,
            "normalized_date": normalized_date,
            "role": role,
            "role_confidence": confidence,
            "context": compact_summary(context, 220),
        }
        if endpoint:
            mention["range_endpoint"] = endpoint
        mentions.append(
            mention
        )
    return mentions


def _default_year(span_timestamp: object) -> int | None:
    if isinstance(span_timestamp, str):
        try:
            parsed = datetime.fromisoformat(span_timestamp)
            return parsed.year
        except ValueError:
            return None
    if hasattr(span_timestamp, "year"):
        return int(getattr(span_timestamp, "year"))
    return None


def _mention_context(content: str, start: int, end: int, *, radius: int = 140) -> str:
    left = max(0, start - radius)
    right = min(len(content), end + radius)
    return content[left:right].strip()


def _role_context(content: str, start: int, end: int) -> str:
    sentence = _sentence_window(content, start, end)
    text = str(sentence["text"]).strip()
    if len(list(TEMPORAL_DATE_RE.finditer(text))) + len(list(TEMPORAL_MONTH_RE.finditer(text))) > 1:
        return _mention_context(content, start, end, radius=60)
    if len(text) <= 260:
        return text
    return _mention_context(content, start, end, radius=90)


def _temporal_role(query_lower: str, context_lower: str, range_endpoint: str | None = None) -> tuple[str, float]:
    explicit_deadline = _has_any(context_lower, {"deadline", "deadlines", "due", "target date"})
    deadline = explicit_deadline or (
        _has_any(context_lower, DEPLOYMENT_TERMS) and _has_any(context_lower, {"by ", "before", "no later"})
    )
    completion = _has_any(context_lower, COMPLETION_TERMS)
    feature = _has_any(context_lower, FEATURE_TERMS)
    query_deployment_target = _has_any(query_lower, DEPLOYMENT_TERMS | FINAL_TERMS)
    context_deployment_target = _has_any(context_lower, DEPLOYMENT_TERMS)
    context_final_target = _has_any(context_lower, FINAL_TERMS)
    context_mvp_target = _has_any(context_lower, MVP_TERMS)
    query_feature_target = _has_any(query_lower, FEATURE_TERMS)
    query_overlap = _query_context_overlap(query_lower, context_lower)

    if range_endpoint == "range_end" and context_deployment_target and query_deployment_target:
        return "deployment_deadline", 0.82
    if range_endpoint == "range_end" and feature and (query_overlap or not query_feature_target):
        return "feature_finish_date", 0.91 if query_overlap else 0.76
    if range_endpoint == "range_start" and "between" in query_lower:
        return "start_date", 0.70
    if (feature or query_overlap) and _has_any(context_lower, {"target", "targets", "targeting", "due"}) and _has_any(context_lower, {"by "}) and query_overlap:
        return "feature_finish_date", 0.88
    if _has_any(context_lower, {"sprint"}) and deadline:
        return "sprint_deadline", 0.84
    if _has_any(context_lower, {"sprint"}) and _has_any(context_lower, {"end", "ends", "ending"}):
        return "sprint_end_date", 0.82
    if completion and feature and (query_overlap or not query_feature_target):
        return "feature_finish_date", 0.90 if query_overlap else 0.74
    if completion and feature:
        return "phase_end_date", 0.72
    if completion:
        return "completion_date", 0.78
    if deadline and context_mvp_target and not (context_deployment_target and context_final_target):
        return "mvp_deadline", 0.88
    if deadline and context_deployment_target and (context_final_target or query_deployment_target):
        return "deployment_deadline", 0.94
    if deadline and query_deployment_target and not context_deployment_target:
        return "deployment_deadline", 0.76
    if deadline:
        return "deadline_date", 0.86
    if "between" in query_lower and any(term in context_lower for term in ["from", "start", "begin", "begins", "starting"]):
        return "start_date", 0.70
    return "mentioned_date", 0.50


def _normalize_date_text(text: str, default_year: int | None) -> str | None:
    iso = re.fullmatch(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text.strip())
    if iso:
        year, month, day = map(int, iso.groups())
        return _safe_iso_date(year, month, day)
    match = re.fullmatch(
        r"(?i)(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?",
        text.strip(),
    )
    if not match:
        return None
    month_text, day_text, year_text = match.group(1), match.group(2), match.group(3)
    year = int(year_text) if year_text else default_year
    if not year:
        return None
    return _safe_iso_date(year, MONTHS[month_text.lower()], int(day_text))


def _infer_year_for_match(content: str, start: int, end: int, default_year: int | None) -> int | None:
    text = content[start:end]
    if re.search(r"\b20\d{2}\b", text):
        return default_year
    month_day = re.fullmatch(
        r"(?i)(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?",
        text.strip().rstrip(","),
    )
    if not month_day:
        return default_year
    current_month = MONTHS[month_day.group(1).lower()]
    current_day = int(month_day.group(2))
    previous_date = _nearest_explicit_month_date(content, start, direction=-1)
    next_date = _nearest_explicit_month_date(content, end, direction=1)
    if previous_date and _looks_like_date_range(content[previous_date["end"] : start]):
        previous_month = int(previous_date["month"])
        previous_day = int(previous_date["day"])
        previous_year = int(previous_date["year"])
        if (current_month, current_day) < (previous_month, previous_day):
            return previous_year + 1
        return previous_year
    if next_date and _looks_like_date_range(content[end : next_date["start"]]):
        next_month = int(next_date["month"])
        next_day = int(next_date["day"])
        next_year = int(next_date["year"])
        if (current_month, current_day) > (next_month, next_day):
            return next_year - 1
        return next_year
    sentence = _sentence_window(content, start, end)
    years = [(abs((start + end) // 2 - (sentence["offset"] + match.start())), int(match.group(1))) for match in YEAR_RE.finditer(sentence["text"])]
    if years:
        years.sort(key=lambda item: item[0])
        return years[0][1]
    wider = _nearby_year(content, start, end)
    if wider is not None:
        return wider
    return default_year


def _nearest_explicit_month_date(content: str, index: int, *, direction: int) -> dict[str, int] | None:
    window_size = 100
    if direction < 0:
        left = max(0, index - window_size)
        matches = list(EXPLICIT_MONTH_DAY_YEAR_RE.finditer(content[left:index]))
        if not matches:
            return None
        match = matches[-1]
        return {
            "start": left + match.start(),
            "end": left + match.end(),
            "month": MONTHS[match.group(1).lower()],
            "day": int(match.group(2)),
            "year": int(match.group(3)),
        }
    right = min(len(content), index + window_size)
    match = next(EXPLICIT_MONTH_DAY_YEAR_RE.finditer(content[index:right]), None)
    if not match:
        return None
    return {
        "start": index + match.start(),
        "end": index + match.end(),
        "month": MONTHS[match.group(1).lower()],
        "day": int(match.group(2)),
        "year": int(match.group(3)),
    }


def _nearest_month_date(content: str, index: int, *, direction: int) -> dict[str, int] | None:
    window_size = 100
    if direction < 0:
        left = max(0, index - window_size)
        matches = list(TEMPORAL_MONTH_RE.finditer(content[left:index]))
        if not matches:
            return None
        match = matches[-1]
        return {"start": left + match.start(), "end": left + match.end()}
    right = min(len(content), index + window_size)
    match = next(TEMPORAL_MONTH_RE.finditer(content[index:right]), None)
    if not match:
        return None
    return {"start": index + match.start(), "end": index + match.end()}


def _looks_like_date_range(text: str) -> bool:
    return bool(re.fullmatch(r"[\s,;:()]*[-–—]|[\s,;:()]*\b(?:to|through|until|and)\b[\s,;:()]*", text.strip(), re.I))


def _range_endpoint(content: str, start: int, end: int) -> str | None:
    previous_date = _nearest_month_date(content, start, direction=-1)
    if previous_date and _looks_like_date_range(content[previous_date["end"] : start]):
        return "range_end"
    next_date = _nearest_month_date(content, end, direction=1)
    if next_date and _looks_like_date_range(content[end : next_date["start"]]):
        return "range_start"
    return None


def _sentence_window(content: str, start: int, end: int) -> dict[str, object]:
    left_candidates = [content.rfind(mark, 0, start) for mark in [".", "\n", "?", "!"]]
    left = max(left_candidates)
    left = 0 if left < 0 else left + 1
    right_candidates = [pos for pos in [content.find(mark, end) for mark in [".", "\n", "?", "!"]] if pos >= 0]
    right = min(right_candidates) if right_candidates else len(content)
    return {"text": content[left:right], "offset": left}


def _has_any(text: str, terms: set[str]) -> bool:
    for term in terms:
        if not term:
            continue
        if not term.replace("_", "").isalnum():
            if term in text:
                return True
            continue
        if re.search(rf"\b{re.escape(term)}\b", text):
            return True
    return False


def _query_context_overlap(query_lower: str, context_lower: str) -> bool:
    return _query_context_overlap_score(query_lower, context_lower) > 0


def _query_context_overlap_score(query_lower: str, context_lower: str) -> int:
    query_tokens = {_normalize_token(token) for token in tokenize(query_lower)}
    context_tokens = {_normalize_token(token) for token in tokenize(context_lower)}
    query_tokens = {
        token
        for token in query_tokens
        if (len(token) > 3 or token.isdigit()) and token not in TEMPORAL_STOPWORDS
    }
    context_tokens = {
        token
        for token in context_tokens
        if len(token) > 3 or token.isdigit()
    }
    return len(query_tokens & context_tokens)


def _normalize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _temporal_target_roles(query: str) -> list[str]:
    query_lower = query.lower()
    roles: list[str] = []
    if _has_any(query_lower, {"sprint"}) and _has_any(query_lower, {"end", "ends", "ending"}):
        roles.append("sprint_end_date")
    if _has_any(query_lower, FEATURE_TERMS) and _has_any(query_lower, COMPLETION_TERMS | {"finish", "finishing"}):
        roles.append("feature_finish_date")
    if _has_any(query_lower, DEPLOYMENT_TERMS | FINAL_TERMS) and _has_any(query_lower, DEADLINE_TERMS):
        roles.append("deployment_deadline")
    if _has_any(query_lower, START_TERMS):
        roles.append("start_date")
    if not roles and _has_any(query_lower, DEADLINE_TERMS):
        roles.append("deadline_date")
    return roles


def _mention_target_roles(query_lower: str, context_lower: str, role: str) -> list[str]:
    target_roles = _temporal_target_roles(query_lower)
    if role in target_roles:
        return [role]
    if "deployment_deadline" in target_roles and role == "deadline_date" and not _has_any(context_lower, MVP_TERMS):
        return ["deployment_deadline"]
    if (
        "feature_finish_date" in target_roles
        and role in {"completion_date", "phase_end_date"}
        and _has_any(context_lower, FEATURE_TERMS)
        and _query_context_overlap(query_lower, context_lower)
    ):
        return ["feature_finish_date"]
    return []


def _nearby_year(content: str, start: int, end: int, *, radius: int = 600) -> int | None:
    left = max(0, start - radius)
    right = min(len(content), end + radius)
    center = start - left
    years = [(abs(center - match.start()), int(match.group(1))) for match in YEAR_RE.finditer(content[left:right])]
    if not years:
        return None
    years.sort(key=lambda item: item[0])
    return years[0][1]


def _temporal_summary(query: str, content: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", content).strip()
    if len(normalized) <= limit:
        return normalized
    matches = list(TEMPORAL_DATE_RE.finditer(content)) + list(TEMPORAL_MONTH_RE.finditer(content))
    if not matches:
        return compact_summary(content, limit)
    query_lower = query.lower()
    windows: list[tuple[float, int, int]] = []
    for match in matches:
        context = _role_context(content, match.start(), match.end())
        role, _ = _temporal_role(query_lower, context.lower(), _range_endpoint(content, match.start(), match.end()))
        score = 1.0
        if role != "mentioned_date":
            score += 2.0
        if _mention_target_roles(query_lower, context.lower(), role):
            score += 3.0
        if _query_context_overlap(query_lower, context.lower()):
            score += 1.0
        left = max(0, match.start() - 170)
        right = min(len(content), match.end() + 170)
        windows.append((score, left, right))
    selected: list[tuple[int, int]] = []
    used = 0
    for _, left, right in sorted(windows, key=lambda item: (-item[0], item[1])):
        if any(not (right < old_left or left > old_right) for old_left, old_right in selected):
            continue
        snippet_len = right - left
        if selected and used + snippet_len + 5 > limit:
            continue
        selected.append((left, right))
        used += snippet_len + (5 if len(selected) > 1 else 0)
        if used >= limit:
            break
    if not selected:
        return compact_summary(content, limit)
    snippets = [re.sub(r"\s+", " ", content[left:right]).strip() for left, right in sorted(selected)]
    return compact_summary(" ... ".join(snippet for snippet in snippets if snippet), limit)


def _safe_iso_date(year: int, month: int, day: int) -> str | None:
    try:
        value = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
    return value.date().isoformat()
