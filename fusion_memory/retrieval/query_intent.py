from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from fusion_memory.core.llm import LLMClient, sanitize_error_text
from fusion_memory.core.text import extract_entities
from fusion_memory.retrieval.aggregation_keys import is_vendor_tool_aggregation_query


AnswerShape = Literal[
    "short_answer",
    "yes_no",
    "ordered_list",
    "unordered_list",
    "count",
    "sum",
    "duration",
    "summary",
    "instruction",
]


@dataclass(frozen=True)
class TemporalIntent:
    requires_time: bool = False
    requires_order: bool = False
    requires_duration: bool = False
    order_direction: str = "unknown"
    endpoint_roles: list[str] = field(default_factory=list)
    time_expressions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AggregationIntent:
    operation: str = "none"
    distinct: bool = False
    target_terms: list[str] = field(default_factory=list)
    unit_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QueryIntent:
    schema_version: str
    language: str
    answer_shape: AnswerShape
    evidence_scope: str
    speaker_scope: str
    entities: list[str]
    target_terms: list[str]
    object_types: list[str]
    temporal: TemporalIntent
    aggregation: AggregationIntent
    needs_current_state: bool
    needs_conflict_check: bool
    confidence: float
    route_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ALLOWED_LANGUAGES = {"en", "zh", "mixed", "other"}
ALLOWED_EVIDENCE_SCOPES = {"local_or_best_match", "multi_session"}
ALLOWED_SPEAKER_SCOPES = {"any", "user", "assistant"}
ALLOWED_ORDER_DIRECTIONS = {"unknown", "ascending", "descending"}
ALLOWED_AGGREGATION_OPERATIONS = {"none", "count", "count_distinct", "sum"}
ALLOWED_ENDPOINT_ROLES = {"start", "end", "deadline", "current", "previous"}
ALLOWED_OBJECT_TYPES = {
    "role",
    "security_feature",
    "financial_impact",
    "application_type",
    "planning_system",
    "vendor_tool",
    "event_aspect",
    "title",
    "genre",
    "value",
}
ALLOWED_ANSWER_SHAPES = set(AnswerShape.__args__)  # type: ignore[attr-defined]


LLM_QUERY_INTENT_PROMPT = """You are a query-understanding layer for a long-term memory system.

Normalize the user query into the provided QueryIntent schema. Use only the
query text and the deterministic baseline intent. Do not answer the query and
do not infer facts from memory. Prefer conservative routing when uncertain.

Return JSON only. The output must be evidence-neutral: no benchmark IDs, no
gold answers, no rubric assumptions, and no domain-specific shortcuts.
"""


LLM_QUERY_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent"],
    "properties": {
        "intent": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "language",
                "answer_shape",
                "evidence_scope",
                "speaker_scope",
                "target_terms",
                "object_types",
                "temporal",
                "aggregation",
                "needs_current_state",
                "needs_conflict_check",
                "confidence",
                "route_reasons",
            ],
            "properties": {
                "language": {"type": "string"},
                "answer_shape": {"type": "string", "enum": sorted(ALLOWED_ANSWER_SHAPES)},
                "evidence_scope": {"type": "string", "enum": sorted(ALLOWED_EVIDENCE_SCOPES)},
                "speaker_scope": {"type": "string", "enum": sorted(ALLOWED_SPEAKER_SCOPES)},
                "target_terms": {"type": "array", "items": {"type": "string"}},
                "object_types": {"type": "array", "items": {"type": "string"}},
                "temporal": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "requires_time",
                        "requires_order",
                        "requires_duration",
                        "order_direction",
                        "endpoint_roles",
                        "time_expressions",
                    ],
                    "properties": {
                        "requires_time": {"type": "boolean"},
                        "requires_order": {"type": "boolean"},
                        "requires_duration": {"type": "boolean"},
                        "order_direction": {"type": "string", "enum": sorted(ALLOWED_ORDER_DIRECTIONS)},
                        "endpoint_roles": {"type": "array", "items": {"type": "string"}},
                        "time_expressions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "aggregation": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operation", "distinct", "target_terms", "unit_terms"],
                    "properties": {
                        "operation": {"type": "string", "enum": sorted(ALLOWED_AGGREGATION_OPERATIONS)},
                        "distinct": {"type": "boolean"},
                        "target_terms": {"type": "array", "items": {"type": "string"}},
                        "unit_terms": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "needs_current_state": {"type": "boolean"},
                "needs_conflict_check": {"type": "boolean"},
                "confidence": {"type": "number"},
                "route_reasons": {"type": "array", "items": {"type": "string"}},
            },
        }
    },
}


def analyze_query_intent(query: str) -> QueryIntent:
    """Return a typed, deterministic query intent.

    This is intentionally model-free. It gives retrieval a stable contract and
    gives future LLM refinement a strict output shape to fill only when needed.
    """

    lower = query.lower()
    language = _query_language(query)
    route_reasons: list[str] = []
    answer_shape: AnswerShape = "short_answer"

    if re.search(r"\b(?:summari[sz]e|summary|recap|overview)\b", lower) or "总结" in lower:
        answer_shape = "summary"
        route_reasons.append("summary_request")
    elif (
        re.search(r"\b(?:what order|order in which|list the order|in order|chronolog|sequence|timeline|first came up)\b", lower)
        or (not _hypothetical_or_procedural_sequence(lower) and "first" in lower and ("then" in lower or "next" in lower))
        or re.search(r"按顺序|顺序|时间线|先后|依次|先.*再", lower)
    ):
        answer_shape = "ordered_list"
        route_reasons.append("ordered_output")
    elif re.search(r"\b(?:how long|duration|how many days|how many weeks|days between|weeks between)\b", lower) or re.search(
        r"多久|多少天|多少周|几天|几周|间隔",
        lower,
    ):
        answer_shape = "duration"
        route_reasons.append("duration_request")
    elif re.search(r"\b(?:how many|count|number of|total number|different|unique)\b", lower) or re.search(r"多少|几个|几种|一共|总共|不同", lower):
        answer_shape = "count"
        route_reasons.append("count_request")
    elif re.search(r"\b(?:total|sum|combined|altogether)\b", lower) or re.search(r"合计|总和|加起来", lower):
        answer_shape = "sum"
        route_reasons.append("sum_request")
    elif re.search(r"\b(?:did i|have i|was there|is there|do i)\b", lower) or re.search(r"我有没有|是否|是不是|有没有", lower):
        answer_shape = "yes_no"
        route_reasons.append("yes_no_request")
    elif re.search(r"\b(?:write|draft|format|code|implement|create|generate)\b", lower) or re.search(r"写|生成|实现|创建|草拟|格式", lower):
        answer_shape = "instruction"
        route_reasons.append("instruction_request")
    elif re.search(r"\b(?:list|which|what were|what are)\b", lower) or re.search(r"列出|哪些|分别是什么|是什么", lower):
        answer_shape = "unordered_list"
        route_reasons.append("list_request")

    temporal = _temporal_intent(lower)
    if temporal.requires_time:
        route_reasons.append("temporal_terms")

    aggregation = _aggregation_intent(lower)
    if aggregation.operation != "none":
        route_reasons.append(f"aggregation:{aggregation.operation}")
    object_types = _object_types(lower)
    if object_types:
        route_reasons.extend(f"object:{value}" for value in object_types[:4])

    evidence_scope = "multi_session" if _multi_session_scope(lower) else "local_or_best_match"
    if evidence_scope == "multi_session":
        route_reasons.append("multi_session_scope")

    speaker_scope = _speaker_scope(lower)
    if speaker_scope != "any":
        route_reasons.append(f"speaker:{speaker_scope}")

    needs_current_state = bool(re.search(r"\b(?:current|currently|now|latest|recent|recently|updated)\b", lower) or re.search(r"现在|当前|最新", lower))
    if needs_current_state:
        route_reasons.append("current_state")

    needs_conflict_check = bool(
        re.search(r"\b(?:contradict|conflict|changed|switched|updated|overrode|instead)\b", lower)
        or re.search(r"矛盾|冲突|改|更新", lower)
    )
    if needs_conflict_check:
        route_reasons.append("conflict_or_update")

    confidence = _intent_confidence(route_reasons, lower)
    return QueryIntent(
        schema_version="query-intent-v1",
        language=language,
        answer_shape=answer_shape,
        evidence_scope=evidence_scope,
        speaker_scope=speaker_scope,
        entities=extract_entities(query),
        target_terms=_target_terms(lower),
        object_types=object_types,
        temporal=temporal,
        aggregation=aggregation,
        needs_current_state=needs_current_state,
        needs_conflict_check=needs_conflict_check,
        confidence=confidence,
        route_reasons=list(dict.fromkeys(route_reasons)),
    )


def should_refine_query_intent(query: str, deterministic: QueryIntent) -> bool:
    """Return whether a model pass is worth spending on query normalization."""

    lower = query.lower()
    if deterministic.language != "en":
        return True
    if deterministic.confidence < 0.72:
        return True
    if deterministic.answer_shape in {"ordered_list", "count", "sum", "duration"} and deterministic.confidence < 0.85:
        return True
    if len(query) > 160 and re.search(r"\b(?:and|or|while|given|considering|between)\b", lower):
        return True
    return False


def refine_query_intent_with_llm(
    client: LLMClient,
    query: str,
    deterministic: QueryIntent,
    *,
    min_confidence: float = 0.70,
) -> tuple[QueryIntent, dict[str, Any]]:
    """Use a strict LLM schema to refine QueryIntent, with deterministic fallback.

    The LLM receives only the user query and deterministic baseline intent. The
    validator rejects low-confidence or out-of-contract outputs instead of
    letting a model silently take over routing.
    """

    telemetry: dict[str, Any] = {
        "source": "llm_query_intent",
        "prompt_version": "query-intent-refiner-v0",
        "fallback": True,
        "accepted": False,
        "deterministic_confidence": deterministic.confidence,
    }
    try:
        response = client.structured(
            prompt=f"query-intent-refiner-v0\n\n{LLM_QUERY_INTENT_PROMPT}",
            schema=LLM_QUERY_INTENT_SCHEMA,
            input={
                "query": query,
                "deterministic_intent": deterministic.to_dict(),
                "min_confidence": min_confidence,
            },
        )
    except Exception as exc:
        telemetry["reason"] = "llm_call_failed"
        telemetry["error"] = sanitize_error_text(str(exc), limit=200)
        return deterministic, telemetry
    refined = _validated_llm_query_intent(response, deterministic, min_confidence=min_confidence)
    if refined is None:
        telemetry["reason"] = "invalid_or_low_confidence_output"
        return deterministic, telemetry
    telemetry["fallback"] = False
    telemetry["accepted"] = True
    telemetry["confidence"] = refined.confidence
    return refined, telemetry


def _validated_llm_query_intent(
    response: dict[str, Any],
    deterministic: QueryIntent,
    *,
    min_confidence: float,
) -> QueryIntent | None:
    if not isinstance(response, dict):
        return None
    raw = response.get("intent")
    if not isinstance(raw, dict):
        return None
    language = _enum_value(raw.get("language"), ALLOWED_LANGUAGES, aliases={"chinese": "zh", "english": "en"})
    answer_shape = _enum_value(
        raw.get("answer_shape"),
        ALLOWED_ANSWER_SHAPES,
        aliases={"list": "unordered_list", "bulleted_list": "unordered_list", "chronology": "ordered_list", "timeline": "ordered_list"},
    )
    evidence_scope = _enum_value(
        raw.get("evidence_scope"),
        ALLOWED_EVIDENCE_SCOPES,
        aliases={
            "all_relevant": "multi_session",
            "global": "multi_session",
            "cross_session": "multi_session",
            "all_sessions": "multi_session",
            "all_memory": "multi_session",
            "current_session": "local_or_best_match",
            "local": "local_or_best_match",
        },
    )
    speaker_scope = _enum_value(raw.get("speaker_scope"), ALLOWED_SPEAKER_SCOPES, aliases={"the_user": "user", "model": "assistant"})
    confidence = _bounded_float(raw.get("confidence"))
    temporal_raw = raw.get("temporal")
    aggregation_raw = raw.get("aggregation")
    if (
        language is None
        or answer_shape is None
        or evidence_scope is None
        or speaker_scope is None
        or confidence is None
        or confidence < min_confidence
        or not isinstance(temporal_raw, dict)
        or not isinstance(aggregation_raw, dict)
    ):
        return None

    order_direction = _enum_value(temporal_raw.get("order_direction"), ALLOWED_ORDER_DIRECTIONS, aliases={"none": "unknown", "na": "unknown"})
    operation = _enum_value(
        aggregation_raw.get("operation"),
        ALLOWED_AGGREGATION_OPERATIONS,
        aliases={"list": "count_distinct", "enumerate": "count_distinct", "count_unique": "count_distinct", "distinct_count": "count_distinct"},
    )
    if order_direction is None or operation is None:
        return None

    target_terms = _string_list(raw.get("target_terms"), max_items=16)
    object_types = _allowed_string_list(raw.get("object_types"), ALLOWED_OBJECT_TYPES, max_items=8)
    route_reasons = _string_list(raw.get("route_reasons"), max_items=16)
    endpoint_roles = _allowed_string_list(temporal_raw.get("endpoint_roles"), ALLOWED_ENDPOINT_ROLES, max_items=6)
    time_expressions = _string_list(temporal_raw.get("time_expressions"), max_items=8)
    aggregation_target_terms = _string_list(aggregation_raw.get("target_terms"), max_items=8)
    unit_terms = _string_list(aggregation_raw.get("unit_terms"), max_items=5)

    if not isinstance(temporal_raw.get("requires_time"), bool):
        return None
    if not isinstance(temporal_raw.get("requires_order"), bool):
        return None
    if not isinstance(temporal_raw.get("requires_duration"), bool):
        return None
    if not isinstance(aggregation_raw.get("distinct"), bool):
        return None
    if not isinstance(raw.get("needs_current_state"), bool):
        return None
    if not isinstance(raw.get("needs_conflict_check"), bool):
        return None

    merged_reasons = list(dict.fromkeys([*deterministic.route_reasons, *route_reasons, "llm_refined"]))
    return QueryIntent(
        schema_version="query-intent-v1",
        language=language,
        answer_shape=answer_shape,  # type: ignore[arg-type]
        evidence_scope=evidence_scope,
        speaker_scope=speaker_scope,
        entities=deterministic.entities,
        target_terms=target_terms or deterministic.target_terms,
        object_types=object_types,
        temporal=TemporalIntent(
            requires_time=temporal_raw["requires_time"],
            requires_order=temporal_raw["requires_order"],
            requires_duration=temporal_raw["requires_duration"],
            order_direction=order_direction,
            endpoint_roles=endpoint_roles,
            time_expressions=time_expressions,
        ),
        aggregation=AggregationIntent(
            operation=operation,
            distinct=aggregation_raw["distinct"],
            target_terms=aggregation_target_terms or target_terms[:8],
            unit_terms=unit_terms,
        ),
        needs_current_state=raw["needs_current_state"],
        needs_conflict_check=raw["needs_conflict_check"],
        confidence=round(confidence, 2),
        route_reasons=merged_reasons,
    )


def _enum_value(value: Any, allowed: set[str], *, aliases: dict[str, str] | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if aliases and normalized in aliases:
        normalized = aliases[normalized]
    return normalized if normalized in allowed else None


def _bounded_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0.0 or parsed > 1.0:
        return None
    return parsed


def _string_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned or len(cleaned) > 80:
            continue
        out.append(cleaned)
    return list(dict.fromkeys(out))[:max_items]


def _allowed_string_list(value: Any, allowed: set[str], *, max_items: int) -> list[str]:
    return [item for item in _string_list(value, max_items=max_items) if item in allowed]


def _temporal_intent(lower: str) -> TemporalIntent:
    time_expressions = _time_expressions(lower)
    requires_duration = bool(
        re.search(r"\b(?:how long|duration|how many days|how many weeks|days between|weeks between|between)\b", lower)
        or re.search(r"多久|多少天|多少周|几天|几周|间隔", lower)
    )
    procedural_sequence = _hypothetical_or_procedural_sequence(lower)
    social_first_time = _non_temporal_first_time_phrase(lower)
    requires_order = bool(
        (
            not procedural_sequence
            and not social_first_time
            and re.search(r"\b(?:before|after|first|then|next|chronolog|sequence|timeline|what order|order in which|list the order|in order)\b", lower)
        )
        or re.search(r"之前|之后|先|顺序|按顺序|时间线|先后|依次", lower)
    )
    order_direction = "ascending" if requires_order else "unknown"
    if re.search(r"\b(?:latest|most recent|last|newest)\b", lower) or re.search(r"最新|最后", lower):
        order_direction = "descending"
    endpoint_roles: list[str] = []
    if requires_duration:
        endpoint_roles.extend(["start", "end"])
    if re.search(r"\b(?:deadline|due date|target date)\b", lower):
        endpoint_roles.append("deadline")
    if re.search(r"\b(?:start|started|begin|began)\b", lower):
        endpoint_roles.append("start")
    if re.search(r"\b(?:end|ended|finish|finished|complete|completed)\b", lower):
        endpoint_roles.append("end")
    return TemporalIntent(
        requires_time=bool(time_expressions or requires_order or requires_duration),
        requires_order=requires_order,
        requires_duration=requires_duration,
        order_direction=order_direction,
        endpoint_roles=list(dict.fromkeys(endpoint_roles)),
        time_expressions=time_expressions,
    )


def _aggregation_intent(lower: str) -> AggregationIntent:
    operation = "none"
    is_duration_query = bool(
        re.search(r"\b(?:how many days|how many weeks|days between|weeks between|duration|how long)\b", lower)
        or re.search(r"多久|多少天|多少周|几天|几周|间隔", lower)
    )
    if is_duration_query:
        operation = "none"
    elif re.search(r"\b(?:how many|count|number of|different|unique)\b", lower) or re.search(r"多少|几个|几种|一共|总共|不同", lower):
        operation = "count_distinct" if re.search(r"\b(?:different|unique|distinct)\b", lower) or re.search(r"不同|几种", lower) else "count"
    if not is_duration_query and (re.search(r"\b(?:total|sum|combined|altogether)\b", lower) or re.search(r"合计|总和|加起来", lower)):
        operation = "sum"
    target_terms = _target_terms(lower)
    unit_terms = [term for term in target_terms if term.endswith("s") or term in {"days", "weeks", "hours", "dollars", "movies", "books", "requests"}]
    return AggregationIntent(
        operation=operation,
        distinct=operation == "count_distinct" or bool(re.search(r"\b(?:different|unique|distinct)\b", lower)),
        target_terms=target_terms[:8],
        unit_terms=unit_terms[:5],
    )


def _speaker_scope(lower: str) -> str:
    if re.search(r"\b(?:i|me|my|mine|user)\b", lower) or re.search(r"我|我的|用户", lower):
        if re.search(r"\b(?:you suggested|assistant suggested|you said|your recommendation)\b", lower) or re.search(r"你建议|助手", lower):
            return "assistant"
        return "user"
    if re.search(r"\b(?:you suggested|assistant|you said|your recommendation)\b", lower) or re.search(r"你建议|助手", lower):
        return "assistant"
    return "any"


def _multi_session_scope(lower: str) -> bool:
    return bool(
        re.search(r"\b(?:across|throughout|over time|in total|total after|between my .+ and my .+|considering .+ and .+|sessions?|conversations?|chats?)\b", lower)
        or re.search(r"跨.*(?:会话|对话)|所有(?:会话|对话)|多次(?:会话|对话)|整个(?:会话|对话)|全部(?:会话|对话)", lower)
    )


def _query_language(query: str) -> str:
    has_zh = bool(re.search(r"[\u4e00-\u9fff]", query))
    has_latin = bool(re.search(r"[A-Za-z]", query))
    if has_zh and has_latin:
        return "mixed"
    if has_zh:
        return "zh"
    return "en"


def _hypothetical_or_procedural_sequence(lower: str) -> bool:
    return bool(
        re.search(r"\bif\b.{0,160}\bthen\b", lower)
        or (
            re.search(r"\b(?:chance|probability|calculate|figure out|how do i|how should i)\b", lower)
            and re.search(r"\b(?:first|then|next|after)\b", lower)
        )
    )


def _non_temporal_first_time_phrase(lower: str) -> bool:
    return bool(
        re.search(r"\b(?:meet|meeting|met)\s+(?:someone|people|a person|somebody)\s+for\s+the\s+first\s+time\b", lower)
        or re.search(r"\bfor\s+the\s+first\s+time\s+(?:meeting|meet|met)\b", lower)
    )


def _object_types(lower: str) -> list[str]:
    patterns: list[tuple[str, str]] = [
        ("role", r"\broles?\b|用户角色|角色"),
        ("security_feature", r"\b(?:security|auth(?:entication|orization)?|login|password|access control|rbac|permissions?)\b|安全功能|认证|鉴权|授权|登录|密码|访问控制|权限"),
        ("financial_impact", r"\b(?:budget|financial|expense|expenses|income|contract|freelance|savings?|grocery|medical|bills?)\b|预算|财务|费用|收入|合同|自由职业|储蓄|存款|医疗账单|账单"),
        ("application_type", r"\bapplication\s+types?\b|申请类型|申请种类"),
        ("planning_system", r"\b(?:reminders?|planners?|calendars?|schedules?|task\s+(?:tools?|systems?|apps?))\b|提醒|日历|日程|任务管理|计划工具"),
        ("vendor_tool", r"\b(?:vendors?|tools?|platforms?|software|apps?|applications?|services?|systems?)\b|供应商|工具|平台|软件|应用|系统"),
        ("event_aspect", r"\b(?:aspects?|topics?|features?|concerns?)\b|方面|主题|功能|关注点|问题"),
        ("title", r"\b(?:movies?|films?|books?|series|titles?)\b|电影|影片|书|书籍|系列|标题"),
        ("genre", r"\bgenres?\b|题材|类型|流派"),
        ("value", r"\b(?:sizes?|amounts?|values?|numbers?)\b|数值|金额|尺寸|尺码|数量"),
    ]
    out: list[str] = []
    for name, pattern in patterns:
        if re.search(pattern, lower):
            out.append(name)
    if is_vendor_tool_aggregation_query(lower) and "vendor_tool" not in out:
        out.append("vendor_tool")
    return out[:8]


def _time_expressions(lower: str) -> list[str]:
    phrases = [
        "yesterday",
        "today",
        "tomorrow",
        "last week",
        "this week",
        "next week",
        "last month",
        "this month",
        "next month",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "deadline",
        "due date",
    ]
    found = [phrase for phrase in phrases if phrase in lower]
    found.extend(match.group(0) for match in re.finditer(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?\b", lower))
    found.extend(match.group(0) for match in re.finditer(r"\b\d{4}-\d{1,2}-\d{1,2}\b", lower))
    return list(dict.fromkeys(found))


def _target_terms(lower: str) -> list[str]:
    stop = {
        "about",
        "across",
        "after",
        "and",
        "answer",
        "before",
        "between",
        "brought",
        "can",
        "considering",
        "could",
        "count",
        "current",
        "different",
        "during",
        "first",
        "from",
        "have",
        "how",
        "list",
        "many",
        "number",
        "only",
        "order",
        "please",
        "show",
        "that",
        "the",
        "their",
        "there",
        "these",
        "through",
        "throughout",
        "total",
        "what",
        "when",
        "which",
        "with",
        "would",
        "you",
    }
    terms = []
    for token in re.findall(r"[a-z0-9_\-\u4e00-\u9fff]+", lower):
        if len(token) < 3 or token in stop:
            continue
        terms.append(token)
    return list(dict.fromkeys(terms[:16]))


def _intent_confidence(route_reasons: list[str], lower: str) -> float:
    score = 0.45
    if route_reasons:
        score += min(0.35, len(route_reasons) * 0.07)
    if re.search(r"\b(?:and|or|while|considering|given)\b", lower) and len(lower) > 120:
        score -= 0.10
    if "?" in lower:
        score += 0.05
    return max(0.20, min(0.90, round(score, 2)))
