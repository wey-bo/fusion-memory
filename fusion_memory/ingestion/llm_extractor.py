from __future__ import annotations

from datetime import datetime
from typing import Any

from fusion_memory.core.llm import LLMClient
from fusion_memory.core.models import EvidenceSpan, ExtractedCandidate, MemoryFact, new_id


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "source_span_ids"],
                "properties": {
                    "local_id": {"type": "string"},
                    "text": {"type": "string"},
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object": {"type": "string"},
                    "category": {"type": "string"},
                    "confidence": {"type": ["number", "string"]},
                    "salience": {"type": ["number", "string"]},
                    "source_span_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["description", "source_span_ids"],
                "properties": {
                    "local_id": {"type": "string"},
                    "text": {"type": "string"},
                    "description": {"type": "string"},
                    "label": {"type": "string"},
                    "event_type": {"type": "string"},
                    "participants": {"type": "array", "items": {"type": "string"}},
                    "time_start": {"type": ["string", "null"]},
                    "time_end": {"type": ["string", "null"]},
                    "time_granularity": {"type": "string"},
                    "time_source": {"type": "string"},
                    "confidence": {"type": ["number", "string"]},
                    "source_span_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["relation_type"],
                "properties": {
                    "local_id": {"type": "string"},
                    "text": {"type": "string"},
                    "relation_type": {"type": "string"},
                    "from_local_id": {"type": "string"},
                    "to_fact_id": {"type": "string"},
                    "confidence": {"type": ["number", "string"]},
                    "source_span_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
    },
    "required": ["facts", "events", "relations"],
}

EXTRACTION_PROMPT = """Extract only durable memory candidates from the input spans.

Return a JSON object with arrays: facts, events, relations. Use an empty array
for any category with no durable candidates.
Each fact must be an object with text, subject, predicate, object, category,
confidence, salience, and source_span_ids. source_span_ids must only contain
span_id values present in the input. Do not return plain strings.
Use facts for stable preferences, instructions, profile details, project state,
or other information that should be remembered later. Leave arrays empty when
there is nothing durable to remember.

Use events for timestamped or orderable conversation milestones, project actions,
decisions, tests, plans, mentions, questions, and state changes that may be used
later for chronology or timeline questions. Prefer cross-domain event_type values
from this controlled set when applicable: user_introduced_aspect,
preference_change, plan_step, concern, decision, activity, constraint. Use
milestone only for explicit project milestone summaries. Event descriptions
should preserve specific labels, dates, numbers, versions, file names, columns,
and code-like tokens from the source span when present. For orderable events,
include a concise label and preserve the source span id.
"""


class StructuredLLMExtractor:
    """Adapter for production structured extraction.

    The LLM is expected to return a dict with optional `facts`, `events`, and
    `relations` arrays. This class validates source attribution and converts
    the payload into Fusion `ExtractedCandidate`s; EncodingGate still decides
    whether candidates may be promoted.
    """

    def __init__(
        self,
        client: LLMClient,
        prompt_version: str = "llm-extractor-v0",
        *,
        strict: bool = True,
        allow_legacy_strings: bool = False,
    ) -> None:
        self.client = client
        self.prompt_version = prompt_version
        self.strict = strict
        self.allow_legacy_strings = allow_legacy_strings
        self.last_telemetry: dict[str, Any] = {}

    def extract(self, spans: list[EvidenceSpan], existing_facts: list[MemoryFact], session_time: datetime) -> list[ExtractedCandidate]:
        self.last_telemetry = self._new_telemetry(len(spans))
        if not spans:
            return []
        try:
            response = self.client.structured(
                prompt=f"{self.prompt_version}\n\n{EXTRACTION_PROMPT}",
                schema=EXTRACTION_SCHEMA,
                input={
                    "session_time": session_time.isoformat(),
                    "spans": [
                        {
                            "span_id": span.span_id,
                            "speaker": span.speaker,
                            "span_type": span.span_type,
                            "timestamp": span.timestamp.isoformat(),
                            "content": span.content,
                        }
                        for span in spans
                    ],
                    "existing_facts": [
                        {
                            "fact_id": fact.fact_id,
                            "text": fact.text,
                            "category": fact.category,
                            "source_span_ids": fact.source_span_ids,
                        }
                        for fact in existing_facts[-50:]
                    ],
                },
            )
        except Exception as exc:
            self.last_telemetry.update(
                {
                    "llm_call_failed": True,
                    "fallback_used": True,
                    "fallback_reason": type(exc).__name__,
                }
            )
            fallback = _rule_fallback(spans, existing_facts, session_time)
            self.last_telemetry["fallback_candidate_count"] = len(fallback)
            return fallback
        if not isinstance(response, dict):
            self.last_telemetry.update(
                {
                    "invalid_response": True,
                    "fallback_used": True,
                    "fallback_reason": "invalid_response_type",
                }
            )
            fallback = _rule_fallback(spans, existing_facts, session_time)
            self.last_telemetry["fallback_candidate_count"] = len(fallback)
            return fallback
        valid_span_ids = {span.span_id for span in spans}
        out: list[ExtractedCandidate] = []
        for fact in self._array_items(response, "facts"):
            candidate = self._fact_candidate(fact, valid_span_ids)
            if candidate is None:
                self.last_telemetry["invalid_fact_count"] += 1
                continue
            self.last_telemetry["accepted_fact_count"] += 1
            out.append(candidate)
        for event in self._array_items(response, "events"):
            candidate = self._event_candidate(event, valid_span_ids)
            if candidate is None:
                self.last_telemetry["invalid_event_count"] += 1
                continue
            self.last_telemetry["accepted_event_count"] += 1
            out.append(candidate)
        for relation in self._array_items(response, "relations"):
            candidate = self._relation_candidate(relation, valid_span_ids)
            if candidate is None:
                self.last_telemetry["invalid_relation_count"] += 1
                continue
            self.last_telemetry["accepted_relation_count"] += 1
            out.append(candidate)
        if not any(candidate.candidate_type == "event" for candidate in out):
            rule_events = [
                candidate
                for candidate in _rule_fallback(spans, existing_facts, session_time)
                if candidate.candidate_type == "event"
            ]
            if rule_events:
                self.last_telemetry["rule_event_fallback_used"] = True
                self.last_telemetry["rule_event_fallback_count"] = len(rule_events)
                out.extend(rule_events)
        return out

    def _fact_candidate(self, fact: Any, valid_span_ids: set[str]) -> ExtractedCandidate | None:
        if isinstance(fact, str):
            if self.strict and not self.allow_legacy_strings:
                return None
            fact = {
                "text": fact,
                "category": "general_fact",
                "confidence": 0.75,
                "salience": 0.6,
                "source_span_ids": list(valid_span_ids) if len(valid_span_ids) == 1 else [],
            }
        elif not isinstance(fact, dict):
            return None
        if not _non_empty_string(fact.get("text")):
            return None
        source_span_ids = self._source_span_ids(fact, valid_span_ids)
        if self.strict and not source_span_ids:
            return None
        structured = {
            "subject": fact.get("subject", "user"),
            "predicate": fact.get("predicate", "said"),
            "object": fact.get("object", fact.get("text", "")),
            "category": fact.get("category", "general_fact"),
            "confidence": _float_field(fact.get("confidence"), 0.5),
            "salience": _float_field(fact.get("salience"), 0.5),
        }
        return ExtractedCandidate(
            local_id=fact.get("local_id") or new_id("cand"),
            candidate_type="fact",
            text=fact.get("text") or f"{structured['subject']} {structured['predicate']} {structured['object']}",
            structured=structured,
            confidence=structured["confidence"],
            source_span_ids=source_span_ids,
            extractor_name="structured_llm_extractor",
            prompt_version=self.prompt_version,
        )

    def _event_candidate(self, event: Any, valid_span_ids: set[str]) -> ExtractedCandidate | None:
        if not isinstance(event, dict):
            return None
        source_span_ids = self._source_span_ids(event, valid_span_ids)
        if self.strict and not source_span_ids:
            return None
        event_type = event.get("event_type") or event.get("facet") or "user_introduced_aspect"
        description = event.get("description", event.get("text", ""))
        if not _non_empty_string(description):
            return None
        label = event.get("label")
        if label and "Facet [" not in str(description):
            description = f"Facet [{event_type}]: {str(event_type).replace('_', ' ')}. Label: {label}. Evidence: {description}"
        structured = {
            "event_type": event_type,
            "participants": event.get("participants", []),
            "description": description,
            "time_start": event.get("time_start"),
            "time_end": event.get("time_end"),
            "time_granularity": event.get("time_granularity", "unknown"),
            "time_source": event.get("time_source", "unknown"),
            "confidence": _float_field(event.get("confidence"), 0.5),
        }
        return ExtractedCandidate(
            local_id=event.get("local_id") or new_id("cand"),
            candidate_type="event",
            text=event.get("text") or structured["description"],
            structured=structured,
            confidence=structured["confidence"],
            source_span_ids=source_span_ids,
            extractor_name="structured_llm_extractor",
            prompt_version=self.prompt_version,
        )

    def _relation_candidate(self, relation: Any, valid_span_ids: set[str]) -> ExtractedCandidate | None:
        if not isinstance(relation, dict):
            return None
        if not _non_empty_string(relation.get("relation_type")):
            return None
        source_span_ids = self._source_span_ids(relation, valid_span_ids)
        if self.strict and not source_span_ids:
            return None
        confidence = _float_field(relation.get("confidence"), 0.5)
        return ExtractedCandidate(
            local_id=relation.get("local_id") or new_id("cand"),
            candidate_type="relation",
            text=relation.get("text") or f"{relation.get('from_local_id')} {relation.get('relation_type')} {relation.get('to_fact_id')}",
            structured={
                "relation_type": relation.get("relation_type", "linked_to"),
                "from_local_id": relation.get("from_local_id"),
                "to_fact_id": relation.get("to_fact_id"),
                "confidence": confidence,
            },
            confidence=confidence,
            source_span_ids=source_span_ids,
            extractor_name="structured_llm_extractor",
            prompt_version=self.prompt_version,
        )

    def _source_span_ids(self, item: dict[str, Any], valid_span_ids: set[str]) -> list[str]:
        raw_ids = item.get("source_span_ids", [])
        if not isinstance(raw_ids, list):
            return []
        source_span_ids = [span_id for span_id in raw_ids if isinstance(span_id, str) and span_id in valid_span_ids]
        return list(dict.fromkeys(source_span_ids))

    def _array_items(self, response: dict[str, Any], key: str) -> list[Any]:
        raw = response.get(key, [])
        if raw is None:
            return []
        if not isinstance(raw, list):
            self.last_telemetry[f"invalid_{key}_shape"] = True
            return []
        return raw

    def _new_telemetry(self, span_count: int) -> dict[str, Any]:
        return {
            "extractor": "structured_llm_extractor",
            "prompt_version": self.prompt_version,
            "strict": self.strict,
            "allow_legacy_strings": self.allow_legacy_strings,
            "span_count": span_count,
            "llm_call_failed": False,
            "invalid_response": False,
            "fallback_used": False,
            "fallback_reason": None,
            "invalid_fact_count": 0,
            "invalid_event_count": 0,
            "invalid_relation_count": 0,
            "accepted_fact_count": 0,
            "accepted_event_count": 0,
            "accepted_relation_count": 0,
            "rule_event_fallback_used": False,
            "rule_event_fallback_count": 0,
        }


def _rule_fallback(spans: list[EvidenceSpan], existing_facts: list[MemoryFact], session_time: datetime) -> list[ExtractedCandidate]:
    from fusion_memory.ingestion.extractors import RuleBasedExtractor

    return RuleBasedExtractor().extract(spans, existing_facts, session_time)


def _float_field(value: Any, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"high", "strong"}:
            return 0.85
        if normalized in {"medium", "moderate"}:
            return 0.60
        if normalized in {"low", "weak"}:
            return 0.35
        try:
            return float(normalized)
        except ValueError:
            return default
    return default


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
