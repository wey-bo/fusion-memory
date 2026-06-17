from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.event_ordering_common import (
    _event_ordering_record_sort_key,
    _requested_event_ordering_count,
    _event_ordering_sequence_output_sort_key,
)
from fusion_memory.retrieval.event_ordering_labels import (
    _EVENT_ORDERING_GENERIC_SCOPE_EQUIVALENTS,
    _EVENT_ORDERING_SCOPE_EQUIVALENTS,
    _EVENT_ORDERING_SEQUENCE_STOPWORDS,
    _EVENT_ORDERING_TOPIC_WORDS,
    _event_ordering_aspect_hint_label,
    _event_ordering_assistant_plan_text,
    _event_ordering_compact_aspect_label,
    _event_ordering_hint_phrase,
    _event_ordering_label_key,
    _event_ordering_label_overlaps_seen,
    _event_ordering_low_information_record,
    _event_ordering_low_information_theme_label,
    _event_ordering_nominal_event_label,
    _event_ordering_preserve_acronyms,
    _event_ordering_sequence_label,
    _event_ordering_shell_like_label,
    _event_ordering_terms,
    _short_event_ordering_theme,
)
from fusion_memory.retrieval.event_ordering_milestones import _event_ordering_project_timeline_query
from fusion_memory.retrieval.event_ordering_records import (
    _dedupe_event_ordering_records,
    _event_ordering_component_scope_query,
    _event_ordering_search_records,
)

def _event_ordering_typed_aspect_sequence_items(
    query: str,
    source_spans: list[dict[str, Any]],
    anchor_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requested = _requested_event_ordering_count(query)
    if requested is None or requested <= 0:
        return []
    if _event_ordering_component_scope_query(query) or _event_ordering_project_timeline_query(query):
        return []
    records = _event_ordering_search_records(source_spans, anchor_timeline)
    records = _dedupe_event_ordering_records(records)
    records.sort(key=_event_ordering_record_sort_key)
    scope_terms = _event_ordering_typed_scope_terms(query)
    candidates: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("speaker") or "").lower() not in {"user", "document"}:
            continue
        text = str(record.get("conversation_content") or record.get("text") or "")
        if (
            not text.strip()
            or _event_ordering_assistant_plan_text(text)
            or _event_ordering_low_information_record(record)
            or _event_ordering_non_event_or_negated_record(text)
        ):
            continue
        label, aspect_type = _event_ordering_typed_aspect_label(query, record)
        if not label:
            continue
        if not _event_ordering_record_matches_query_scope(query, record, label, scope_terms):
            continue
        score = _event_ordering_typed_aspect_score(query, record, label, aspect_type)
        if score < 0.46:
            continue
        candidates.append(
            {
                "record": record,
                "label": label,
                "aspect_type": aspect_type,
                "score": score,
                "sort_key": _event_ordering_sequence_output_sort_key(record),
            }
        )
    candidates = _dedupe_event_ordering_typed_aspects(candidates)
    if len(candidates) < requested:
        return []
    selected = _event_ordering_select_typed_aspects(candidates, requested)
    if len(selected) < requested:
        return []
    selected.sort(key=lambda item: item["sort_key"])
    items: list[dict[str, Any]] = []
    for item in selected[:requested]:
        record = item["record"]
        out: dict[str, Any] = {
            "sequence_index": len(items) + 1,
            "label": item["label"],
            "context": compact_summary(str(record.get("text") or ""), 260),
            "aspect_type": item["aspect_type"],
        }
        if record.get("source_span_id"):
            out["source_span_id"] = record["source_span_id"]
        if record.get("timeline_index") is not None:
            out["timeline_index"] = record["timeline_index"]
        items.append(out)
    return items if len(items) == requested else []

def _event_ordering_typed_scope_terms(query: str) -> set[str]:
    terms = _event_ordering_terms(query)
    terms -= _EVENT_ORDERING_TOPIC_WORDS
    terms -= _EVENT_ORDERING_SEQUENCE_STOPWORDS
    terms -= {
        "challenge",
        "challenges",
        "concern",
        "concerns",
        "related",
        "work-related",
        "different",
        "using",
        "used",
        "process",
        "processes",
        "improving",
        "improve",
        "chat",
        "chats",
        "conversation",
        "conversations",
        "you",
        "our",
        "personal",
        "work",
    }
    expanded = set(terms)
    for term in list(terms):
        expanded.update(_EVENT_ORDERING_SCOPE_EQUIVALENTS.get(term, set()))
        expanded.update(_EVENT_ORDERING_GENERIC_SCOPE_EQUIVALENTS.get(term, set()))
    return expanded

def _event_ordering_record_matches_query_scope(
    query: str,
    record: dict[str, Any],
    label: str,
    scope_terms: set[str],
) -> bool:
    if not scope_terms:
        return True
    text = " ".join(
        str(value or "")
        for value in [
            label,
            record.get("label"),
            record.get("timeline_label"),
            record.get("text"),
            record.get("conversation_content"),
        ]
    )
    text_terms = _event_ordering_terms(text)
    if text_terms & scope_terms:
        return True
    query_lower = query.lower()
    text_lower = text.lower()
    if _event_ordering_support_option_scope_match(query_lower, text_lower):
        return True
    if re.search(r"\b(?:personal|work-related|challenge|challenges)\b", query_lower):
        return bool(
            re.search(
                r"\b(?:burnout|stress|workload|motivation|team dynamics|meetings?|vacation|getaway|anniversary|celebration|partner|date nights?)\b",
                text_lower,
            )
        )
    if re.search(r"\b(?:resume|profile|portfolio|linkedin|cv|career)\b", query_lower):
        return bool(re.search(r"\b(?:resume|profile|portfolio|linkedin|cv|career|interview|job offer|salary|ATS|keyword)\b", text_lower, flags=re.I))
    if re.search(r"\b(?:hiring|screening|candidate|AI)\b", query):
        return bool(re.search(r"\b(?:hiring|screening|candidate|AI|vendor|tool|platform|algorithm|bias|fairness|transparency)\b", text, flags=re.I))
    return False

def _event_ordering_support_option_scope_match(query_lower: str, text_lower: str) -> bool:
    if not re.search(r"\b(?:strateg(?:y|ies)|support|options?|resources?|tools?|ways?|help)\b", query_lower):
        return False
    resource_signal = bool(
        re.search(
            r"\b(?:assistant|agency|mentor|coach|consultant|advisor|adviser|specialist|contractor|service|team|colleague|partner|tool|tools|board|boards|calendar|reminder|workflow|automation|software|app|platform|system|template|checklist)\b",
            text_lower,
        )
    )
    action_signal = bool(
        re.search(
            r"\b(?:hire[ds]?|hiring|brought on|bring(?:ing)? on|contract(?:ed)?|outsourc(?:e|ed|ing)|delegate[ds]?|delegating|delegation|advice|recommended|suggested|strategy|strategies|approach|plan|process|method|option|support)\b",
            text_lower,
        )
    )
    workload_signal = bool(re.search(r"\b(?:manage|managing|schedule|workload|task|tasks|deadline|productivity|time)\b", text_lower))
    return (resource_signal and action_signal) or (resource_signal and workload_signal) or (action_signal and workload_signal)

def _event_ordering_non_event_or_negated_record(text: str) -> bool:
    lower = text.lower()
    if re.search(r"\b(?:never|not yet|haven't|hasn't|hadn't|didn't|do not|don't)\s+(?:accepted|started|used|implemented|completed|created|added|configured|worked|met|attended)\b", lower):
        return True
    if re.search(r"\b(?:do i need to|should i|can i still|could i|would it make sense to)\b", lower) and not re.search(
        r"\b(?:ai|hiring|screening|resume|profile|portfolio|career|burnout|stress|vacation|anniversary|feature|framework|testing|deployment)\b",
        lower,
    ):
        return True
    return False

def _event_ordering_typed_aspect_label(query: str, record: dict[str, Any]) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(record.get("conversation_content") or record.get("text") or "")).strip()
    if not text:
        return "", ""
    patterns: list[tuple[str, str, str]] = [
        (
            r"\b(?:i|we)\s+started\s+using\s+([A-Z][A-Za-z0-9.+#&-]{2,40})\s+to\s+([^.;!?]{5,120})",
            "{0} {1}",
            "tool_usage",
        ),
        (
            r"\b(?:i|we)\s+used\s+([A-Z][A-Za-z0-9.+#&-]{2,40})\s+to\s+([^.;!?]{5,120})",
            "{0} {1}",
            "tool_usage",
        ),
        (
            r"\b(?:my|our)?\s*(?:partner|friend|mentor|colleague|manager|coach|advisor)\s+([A-Z][A-Za-z']{2,}(?:\s+[A-Z][A-Za-z']{2,}){0,2})\s+(?:suggested|recommended|advised|told|reminded)\s+(?:me|us)?\s*(?:to\s+)?([^.;!?]{5,130})",
            "{0} {1}",
            "person_advice",
        ),
        (
            r"\b(?:i|we)\s+(?:hired|contracted|brought on|outsourced to)\s+([^.;!?]{5,120}?\b(?:assistant|agency|consultant|advisor|adviser|coach|contractor|service|team|specialist)\b[^.;!?]{0,100})",
            "{0}",
            "support_resource",
        ),
        (
            r"\b(?:asking|ask|bringing|bring)\s+([^.;!?]{5,120}?\b(?:assistant|agency|consultant|advisor|adviser|coach|contractor|service|team|specialist)\b[^.;!?]{0,100})\s+to\s+([^.;!?]{5,100})",
            "{0} to {1}",
            "support_resource",
        ),
        (
            r"\b([A-Z][A-Za-z']{2,}(?:\s+[A-Z][A-Za-z']{2,}){0,2})(?:,\s*\d+)?\s+(?:suggested|recommended|advised|told|reminded)\s+(?:me|us)?\s*(?:to\s+)?([^.;!?]{5,130})",
            "{0} {1}",
            "person_advice",
        ),
        (
            r"\b(?:partner|friend|mentor|colleague|manager|coach|advisor)\b.{0,100}\b(?:suggested|recommended|advised|told|reminded)\s+(?:me|us)?\s*(?:to\s+)?([^.;!?]{5,130})",
            "{0}",
            "person_advice",
        ),
        (
            r"\b(?:collaborated|worked)\s+with\s+([A-Z][A-Za-z']{2,}(?:\s+[A-Z][A-Za-z']{2,}){0,2})\s+on\s+(?:(?:a|an|the|my|our)\s+)?([^.;!?]{5,120})",
            "{1} collaboration",
            "collaboration",
        ),
        (
            r"\b(?:improved|increased|reduced|raised|lowered)\s+([^.;!?]{4,100}?)\s+by\s+(\d+(?:\.\d+)?%?)",
            "{0} {1} result",
            "metric_result",
        ),
        (
            r"\b(?:\$[\d,]+|\d+(?:\.\d+)?%)\b.{0,80}\b(?:raise|salary|offer|compensation|bonus|budget|cost)\b|(?:raise|salary|offer|compensation|bonus|budget|cost)\b.{0,80}\b(?:\$[\d,]+|\d+(?:\.\d+)?%)\b",
            "compensation and budget update",
            "metric_result",
        ),
        (
            r"\b(?:job descriptions?|keywords?|keyword match|ATS|applicant tracking system|parser)\b[^.;!?]{0,140}",
            "{match}",
            "matching_or_screening",
        ),
        (
            r"\b(?:international|European|UK|global|regional)\b[^.;!?]{0,120}\b(?:resume|CV|profile|market|application)\b[^.;!?]{0,80}",
            "{match}",
            "localization",
        ),
        (
            r"\b(?:soft skills?|bias|fairness|transparency|human oversight|psychometric|audit|candidate pool|diversity|screening)\b[^.;!?]{0,140}",
            "{match}",
            "fairness_or_screening",
        ),
        (
            r"\b(?:pilot|trial)\s+program\b[^.;!?]{0,140}",
            "{match}",
            "pilot_or_trial",
        ),
        (
            r"\b(?:burnout|stress|stressed|workload|motivation|team dynamics|meeting strategies?|date nights?|vacation|getaway|anniversary dinner|surprise celebration|returning the favor)\b[^.;!?]{0,140}",
            "{match}",
            "personal_or_work_challenge",
        ),
        (
            r"\b(?:(?:kinda|sorta|really|very|pretty)\s+)?(?:worried|concerned|conflicted|nervous)\s+(?:(?:that|about)\s+)?([^.;!?]{6,140})",
            "{0} concern",
            "concern",
        ),
        (
            r"\b(?:decided|chose|declined|accepted|started|finished|completed|updated|revised|tailored|adapted)\s+([^.;!?]{6,140})",
            "{0}",
            "decision_or_update",
        ),
    ]
    for pattern, template, aspect_type in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        if template == "{match}":
            phrase = match.group(0)
        else:
            values = [_event_ordering_hint_phrase(value) for value in match.groups()]
            phrase = template.format(*values)
        label = _event_ordering_finalize_typed_label(phrase)
        normalization_seed = phrase if aspect_type == "person_advice" else (label or phrase)
        label = (
            _event_ordering_normalize_typed_aspect_label(normalization_seed, query_lower=query.lower(), context=text)
            or label
        )
        if label:
            return label, aspect_type
    fallback = _event_ordering_aspect_hint_label(str(record.get("label") or ""), text)
    if fallback:
        label = _event_ordering_finalize_typed_label(fallback)
        label = _event_ordering_normalize_typed_aspect_label(label, query_lower=query.lower(), context=text) or label
        return label, "aspect_hint"
    compact = _event_ordering_compact_aspect_label(_event_ordering_sequence_label(record), text)
    if compact:
        label = _event_ordering_finalize_typed_label(compact)
        label = _event_ordering_normalize_typed_aspect_label(label, query_lower=query.lower(), context=text) or label
        return label, "compact_label"
    return "", ""

def _event_ordering_finalize_typed_label(text: str) -> str:
    label = _event_ordering_hint_phrase(text)
    label = re.sub(r"\s*->->.*$", "", label).strip(" .,:;-")
    label = re.sub(r"\b(?:what do you think|does that sound|how does that sound)\b.*$", "", label, flags=re.I).strip(" .,:;-")
    label = _event_ordering_nominal_event_label(label)
    label = _short_event_ordering_theme(label) or label
    label = _event_ordering_preserve_acronyms(label)
    if not label or _event_ordering_low_information_theme_label(label):
        return ""
    if len(_event_ordering_terms(label) - _EVENT_ORDERING_SEQUENCE_STOPWORDS) < 2:
        return ""
    return label

def _event_ordering_normalize_typed_aspect_label(label: str, *, query_lower: str, context: str) -> str:
    text = _event_ordering_hint_phrase(label)
    ctx = f"{text} {context}".lower()
    text = re.sub(
        r"^(?:i|we|my|our)\s+(?:am|are|was|were|have|had|started|decided|chose|updated|revised|tailored|adapted|used|using|to)\s+",
        "",
        text,
        flags=re.I,
    ).strip(" .,:;-")
    if re.match(r"^[A-Z][A-Za-z']{2,}(?:\s+[A-Z][A-Za-z']{2,}){0,2}\b", text) and re.search(
        r"\b(?:suggested|recommended|advised|told|reminded)\b", context, flags=re.I
    ):
        return _event_ordering_preserve_acronyms(text)
    rules: list[tuple[tuple[str, ...], str]] = [
        (("vacation", "unplug", "getaway"), "vacation and unplugging"),
        (("date night", "partner"), "partner connection planning"),
        (("anniversary dinner", "dinner reservation"), "anniversary dinner planning"),
        (("surprise celebration", "returning the favor", "celebration"), "surprise celebration planning"),
        (("profile view", "profile metrics", "portfolio metric", "linkedin metric"), "profile metrics"),
        (("keyword match", "job description", "applicant tracking", "ATS", "parser"), "job description keyword matching"),
        (("transferable skill",), "transferable skills"),
        (("remote team leadership", "remote leadership"), "remote leadership skills"),
        (("raise", "salary", "compensation", "negotiation"), "raise and salary negotiation"),
        (("international", "european", "global market", "regional market"), "international resume adaptation"),
        (("pilot program", "screening impact"), "pilot program and screening impact"),
        (("human oversight", "bias"), "bias and human oversight"),
        (("soft skill",), "AI soft skills recognition"),
        (("fairness", "transparency"), "AI hiring fairness and transparency"),
        (("psychometric",), "psychometric test integration"),
        (("vendor", "tool", "platform", "algorithm"), "tool/vendor selection and results"),
        (("model update", "model audit", "audit"), "AI model updates and audits"),
        (("burnout", "stress", "fatigue", "irritability", "sleep issue"), "burnout and stress management"),
        (("workload", "meeting reduction", "meeting strategy", "meeting strategies"), "workload and meeting reduction"),
    ]
    for needles, normalized in rules:
        if any(needle.lower() in ctx for needle in needles):
            return _event_ordering_preserve_acronyms(normalized)
    if (
        "hiring" in query_lower
        and "AI " not in text
        and any(term in ctx for term in ("hiring", "screening", "bias", "fairness", "transparency", "pilot"))
    ):
        text = f"AI {text}"
    return _event_ordering_preserve_acronyms(text)

def _event_ordering_typed_aspect_score(query: str, record: dict[str, Any], label: str, aspect_type: str) -> float:
    text = str(record.get("conversation_content") or record.get("text") or "")
    query_terms = _event_ordering_terms(query)
    label_terms = _event_ordering_terms(label)
    text_terms = _event_ordering_terms(text)
    score = 0.30 + min(0.28, 0.05 * len((label_terms | text_terms) & query_terms))
    score += min(0.24, 0.04 * len(label_terms - _EVENT_ORDERING_SEQUENCE_STOPWORDS - _EVENT_ORDERING_TOPIC_WORDS))
    if aspect_type in {
        "tool_usage",
        "person_advice",
        "collaboration",
        "metric_result",
        "matching_or_screening",
        "localization",
        "fairness_or_screening",
        "pilot_or_trial",
        "personal_or_work_challenge",
    }:
        score += 0.18
    if record.get("selector") == "event_ordering_coverage":
        score += 0.04
    if record.get("source_uri") or record.get("turn_id"):
        score += 0.04
    if _event_ordering_low_information_theme_label(label) or _event_ordering_shell_like_label(label):
        score -= 0.35
    if len(label.split()) > 11:
        score -= 0.06
    return score

def _dedupe_event_ordering_typed_aspects(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: (item["sort_key"], -float(item["score"])))
    out: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    seen_spans: set[str] = set()
    for item in ordered:
        record = item["record"]
        span_id = str(record.get("source_span_id") or "")
        key = _event_ordering_label_key(str(item["label"]))
        if span_id and span_id in seen_spans:
            continue
        if key and (key in seen_labels or _event_ordering_label_overlaps_seen(key, seen_labels)):
            continue
        out.append(item)
        if span_id:
            seen_spans.add(span_id)
        if key:
            seen_labels.add(key)
    return out

def _event_ordering_select_typed_aspects(candidates: list[dict[str, Any]], requested: int) -> list[dict[str, Any]]:
    if len(candidates) <= requested:
        return candidates
    ordered = sorted(candidates, key=lambda item: item["sort_key"])
    selected: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    used_types: set[str] = set()
    for bucket in range(requested):
        start = round(bucket * len(ordered) / requested)
        end = round((bucket + 1) * len(ordered) / requested)
        if end <= start:
            end = min(len(ordered), start + 1)
        window = [item for item in ordered[start:end] if id(item) not in seen_ids]
        if not window:
            continue
        choice = max(
            window,
            key=lambda item: (
                float(item["score"]) + (0.08 if item["aspect_type"] not in used_types else 0.0),
                -len(str(item["label"]).split()),
            ),
        )
        selected.append(choice)
        seen_ids.add(id(choice))
        used_types.add(str(choice["aspect_type"]))
    if len(selected) < requested:
        for item in sorted(ordered, key=lambda value: float(value["score"]), reverse=True):
            if id(item) in seen_ids:
                continue
            selected.append(item)
            seen_ids.add(id(item))
            if len(selected) >= requested:
                break
    return selected[:requested]
