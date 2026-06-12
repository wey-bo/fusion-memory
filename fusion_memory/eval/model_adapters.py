from __future__ import annotations

from typing import Any

from fusion_memory.core.llm import LLMClient
from fusion_memory.core.models import EvidencePack
from fusion_memory.core.text import compact_summary


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
    },
    "required": ["answer"],
}


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "matched": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["matched"],
}

RUBRIC_SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
}


class OpenAICompatibleAnswerModel:
    """Benchmark answer model backed by any structured OpenAI-compatible client."""

    def __init__(self, client: LLMClient, prompt_version: str = "eval-answer-v0") -> None:
        self.client = client
        self.prompt_version = prompt_version
        self.version = f"llm_answer:{_client_version(client)}:{prompt_version}"

    def answer(self, query: str, pack: EvidencePack) -> str:
        return self.answer_with_context(query, pack)

    def answer_with_context(
        self,
        query: str,
        pack: EvidencePack,
        *,
        benchmark: str | None = None,
        category: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        response = self.client.structured(
            prompt=self.prompt_version,
            schema=ANSWER_SCHEMA,
            input={
                "instruction": _answer_instruction(benchmark=benchmark, category=category),
                "query": query,
                "answer_policy": pack.answer_policy,
                "coverage": pack.coverage,
                "evidence_pack": _pack_for_model(pack),
            },
        )
        answer = response.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
        return "Not enough supported memory to answer."


class OpenAICompatibleJudgeModel:
    """Semantic answer judge backed by any structured OpenAI-compatible client."""

    def __init__(self, client: LLMClient, prompt_version: str = "eval-judge-v0") -> None:
        self.client = client
        self.prompt_version = prompt_version
        self.version = f"llm_judge:{_client_version(client)}:{prompt_version}"

    def score(self, answer: str, gold_answers: list[str]) -> bool:
        if not gold_answers:
            return False
        try:
            response = self.client.structured(
                prompt=self.prompt_version,
                schema=JUDGE_SCHEMA,
                input={
                    "instruction": (
                        "Return matched=true when the candidate answer is semantically equivalent "
                        "to at least one gold answer. Be strict about unsupported extra claims."
                    ),
                    "candidate_answer": answer,
                    "gold_answers": gold_answers,
                },
            )
        except Exception:
            return False
        return bool(response.get("matched", False))

    def rubric_score(self, query: str, answer: str, rubric_item: str) -> tuple[float, str]:
        errors: list[str] = []
        timeouts = _rubric_retry_timeouts(self.client)
        for attempt, timeout_seconds in enumerate(timeouts, start=1):
            try:
                response = _structured_with_timeout(
                    self.client,
                    prompt=f"{self.prompt_version}:rubric-score",
                    schema=RUBRIC_SCORE_SCHEMA,
                    input={
                        "instruction": (
                            "Evaluate the response against only this BEAM rubric criterion. "
                            "Return score 1.0 when fully satisfied, 0.5 when partially satisfied, "
                            "and 0.0 when not satisfied."
                        ),
                        "question": query,
                        "candidate_answer": answer,
                        "rubric_item": rubric_item,
                    },
                    timeout_seconds=timeout_seconds,
                )
                raw_score = response.get("score", 0.0)
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    score = 0.0
                if score >= 0.75:
                    score = 1.0
                elif score >= 0.25:
                    score = 0.5
                else:
                    score = 0.0
                reason = response.get("reason")
                return score, str(reason or "")
            except Exception as exc:
                errors.append(f"attempt {attempt} @ {timeout_seconds:.0f}s: {exc}")
        return 0.0, "rubric scoring failed after retries: " + " | ".join(errors[:3])


def _pack_for_model(pack: EvidencePack) -> dict[str, Any]:
    if pack.coverage.get("query_type") == "event_ordering":
        source_spans = _compact_event_ordering_records(pack.source_spans, preferred_text_key="content")
        events = _compact_event_ordering_records(pack.events, preferred_text_key="description")
        return {
            "timeline": _event_ordering_timeline(events, source_spans),
            "conflicts": pack.conflicts[:10],
        }
    return {
        "current_views": _compact_records(pack.current_views, preferred_text_key="text"),
        "entity_profiles": _compact_records(pack.entity_profiles, preferred_text_key="text"),
        "facts": _compact_records(pack.facts, preferred_text_key="text"),
        "events": _compact_records(pack.events, preferred_text_key="description"),
        "source_spans": _compact_records(pack.source_spans, preferred_text_key="content"),
        "conflicts": pack.conflicts[:10],
    }


def _answer_instruction(*, benchmark: str | None, category: str | None) -> str:
    base = (
        "Answer the query using only the provided Fusion Memory evidence pack. "
        "Do not use outside knowledge. Do not infer unsupported background, history, "
        "projects, dates, counts, versions, or implementation details. "
        "If the evidence pack does not directly support the answer, return a concise abstention."
    )
    if benchmark != "BEAM":
        return base
    if category == "abstention":
        return (
            base
            + " This is a BEAM abstention query: be especially strict. If the requested detail is not explicitly present, "
            "say that the provided chat/evidence does not contain that information. Do not fill in likely user background "
            "or previous projects from adjacent evidence."
        )
    if category == "contradiction_resolution":
        return (
            base
            + " This is a BEAM contradiction-resolution query. Explicitly state when the evidence contains contradictory "
            "claims, then name both sides of the contradiction with their supporting evidence. Do not collapse the answer "
            "to a simple yes/no unless the contradiction status is also stated."
        )
    if category == "instruction_following":
        return (
            "Answer the query using the provided Fusion Memory evidence pack for user-specific facts, preferences, "
            "versions, and constraints. Do not invent unsupported user history, dates, counts, or prior work. "
            "For implementation requests, you may synthesize ordinary example code that satisfies the supported "
            "stack and user instructions; do not abstain merely because the exact final code is not already present. "
            "If the evidence does not support the user-specific constraints, return a concise abstention."
            + " This is a BEAM instruction-following query. Follow every formatting constraint in the question and evidence. "
            "If implementation code is requested, include fenced code blocks with a language tag such as ```python. "
            "Respect ONLY/exact-count constraints and avoid extra prose that violates the requested format."
        )
    if category == "event_ordering":
        return (
            base
            + " This is a BEAM event-ordering query. Use only evidence_pack.timeline and timeline_index as the "
            "conversation chronology. Ignore calendar dates mentioned inside content when deciding order. Return "
            "an ordered list of the requested items only, using labels or concise descriptions supported by the "
            "evidence. Do not use hidden benchmark labels or guess items that are not present in the evidence."
        )
    if category == "knowledge_update":
        return (
            base
            + " This is a BEAM knowledge-update query. When the evidence gives multiple historical values for the "
            "same attribute, answer with the latest or current value supported by the evidence and mention older "
            "values only if they clarify the update. Do not abstain merely because older conflicting values are present."
        )
    if category == "temporal_reasoning":
        return (
            base
            + " This is a BEAM temporal-reasoning query. If the evidence provides the relevant start and end dates "
            "or deadlines, compute the requested duration from those dates using ordinary calendar arithmetic. "
            "State the date range used when it is supported by the evidence."
        )
    return base


def _compact_event_ordering_records(records: list[dict[str, Any]], *, preferred_text_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records[:20]:
        compacted: dict[str, Any] = {}
        for key in ["id", "timeline_index", "source_span_ids", "event_type", "milestone_group", "speaker"]:
            if key in record:
                compacted[key] = record[key]
        text = str(record.get(preferred_text_key) or record.get("text") or record.get("content") or "")
        if text:
            compacted[preferred_text_key] = compact_summary(text, 1200)
        out.append(compacted)
    return out


def _event_ordering_timeline(events: list[dict[str, Any]], source_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    span_content_by_id = {
        str(span.get("id")): span.get("content")
        for span in source_spans
        if span.get("id") and span.get("content")
    }
    if events:
        timeline: list[dict[str, Any]] = []
        ordered_events = [event for event in events if event.get("timeline_index") is not None]
        ordered_events.sort(key=lambda event: int(event.get("timeline_index") or 0))
        for event in ordered_events:
            source_span_ids = [str(span_id) for span_id in event.get("source_span_ids", []) if span_id]
            item: dict[str, Any] = {
                "timeline_index": len(timeline) + 1,
                "kind": "event",
                "description": event.get("description") or event.get("text"),
                "event_type": event.get("event_type"),
                "milestone_group": event.get("milestone_group"),
                "source_span_ids": source_span_ids,
            }
            supporting_content = next((span_content_by_id[span_id] for span_id in source_span_ids if span_id in span_content_by_id), None)
            if supporting_content:
                item["supporting_content"] = supporting_content
            timeline.append(item)
            if len(timeline) >= 30:
                break
        return timeline

    events_by_span: dict[str, list[dict[str, Any]]] = {}
    unmatched_events: list[dict[str, Any]] = []
    for event in events:
        event_record = {
            "description": event.get("description") or event.get("text"),
            "event_type": event.get("event_type"),
            "milestone_group": event.get("milestone_group"),
            "source_span_ids": event.get("source_span_ids", []),
        }
        span_ids = [str(span_id) for span_id in event.get("source_span_ids", []) if span_id]
        if not span_ids:
            unmatched_events.append({**event_record, "_sort_index": event.get("timeline_index")})
            continue
        for span_id in span_ids:
            events_by_span.setdefault(span_id, []).append(event_record)

    timeline: list[dict[str, Any]] = []
    spans = [span for span in source_spans if span.get("timeline_index") is not None]
    spans.sort(key=lambda span: int(span.get("timeline_index") or 0))
    for span in spans:
        span_id = span.get("id")
        source_span_ids = [str(span_id)] if span_id else []
        turn_events = events_by_span.get(str(span_id), []) if span_id else []
        item: dict[str, Any] = {
            "timeline_index": len(timeline) + 1,
            "kind": "conversation_turn",
            "speaker": span.get("speaker"),
            "content": span.get("content"),
            "source_span_ids": source_span_ids,
        }
        if turn_events:
            item["events"] = turn_events[:5]
        timeline.append(item)

    matched_span_ids = {str(span.get("id")) for span in spans if span.get("id")}
    for event in events:
        span_ids = [str(span_id) for span_id in event.get("source_span_ids", []) if span_id]
        if span_ids and any(span_id in matched_span_ids for span_id in span_ids):
            continue
        unmatched_events.append(
            {
                "description": event.get("description") or event.get("text"),
                "event_type": event.get("event_type"),
                "milestone_group": event.get("milestone_group"),
                "source_span_ids": event.get("source_span_ids", []),
                "_sort_index": event.get("timeline_index"),
            }
        )
    unmatched_events.sort(key=lambda event: int(event.get("_sort_index") or 0))
    for event in unmatched_events:
        event.pop("_sort_index", None)
        event["timeline_index"] = len(timeline) + 1
        event["kind"] = "event"
        timeline.append(event)
        if len(timeline) >= 30:
            break
    return timeline[:30]


def _compact_records(records: list[dict[str, Any]], *, preferred_text_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records[:20]:
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
            "source_span_ids",
        ]:
            if key in record:
                compacted[key] = record[key]
        text = str(record.get(preferred_text_key) or record.get("text") or record.get("content") or "")
        if text:
            compacted[preferred_text_key] = compact_summary(text, 1200)
        out.append(compacted)
    return out


def _client_version(client: LLMClient) -> str:
    return str(getattr(client, "version", client.__class__.__name__))


def _rubric_retry_timeouts(client: LLMClient) -> list[float]:
    base_timeout = float(getattr(client, "timeout_seconds", 30.0) or 30.0)
    return [
        base_timeout,
        max(base_timeout, 180.0),
        max(base_timeout, 300.0),
    ]


def _structured_with_timeout(
    client: LLMClient,
    *,
    prompt: str,
    schema: dict[str, Any],
    input: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    previous_timeout = getattr(client, "timeout_seconds", None)
    if previous_timeout is None:
        return client.structured(prompt=prompt, schema=schema, input=input)
    setattr(client, "timeout_seconds", timeout_seconds)
    try:
        return client.structured(prompt=prompt, schema=schema, input=input)
    finally:
        setattr(client, "timeout_seconds", previous_timeout)
