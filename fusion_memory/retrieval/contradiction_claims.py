from __future__ import annotations

import re
from typing import Any


def conflict_claims_for_model(query: str, conflicts: list[dict[str, Any]], source_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build query-grounded contradiction claims for the model pack.

    This is a typed pack operator, not an answer template. It exposes the
    polarity, role, and evidence strength of candidate claims so answer
    synthesis can distinguish explicit user facts from plans, help requests,
    and assistant inferences.
    """

    if not conflicts:
        return []
    query_terms = _conflict_query_terms(query)
    spans_by_id: dict[str, dict[str, Any]] = {}
    for span in source_spans:
        for key in [span.get("id"), span.get("source_span_id"), *(span.get("source_span_ids") or [])]:
            if key:
                spans_by_id[str(key)] = span

    out: list[dict[str, Any]] = []
    for conflict in conflicts[:4]:
        item: dict[str, Any] = {
            "type": conflict.get("type"),
            "note": conflict.get("note"),
        }
        for polarity, field in [
            ("positive", "positive_source_span_ids"),
            ("negative", "negative_source_span_ids"),
            ("uncertain", "uncertain_source_span_ids"),
        ]:
            claims: list[dict[str, Any]] = []
            ranked_spans: list[tuple[float, str, dict[str, Any]]] = []
            for span_id in conflict.get(field, [])[:10]:
                span = spans_by_id.get(str(span_id))
                if not span:
                    continue
                ranked_spans.append((_conflict_claim_query_score(query_terms, span), str(span_id), span))
            ranked_spans.sort(key=lambda ranked: (-ranked[0], ranked[1]))
            for _score, span_id, span in ranked_spans[:4]:
                claim = _conflict_claim_record(query_terms, span_id, span, expected_polarity=polarity)
                if claim:
                    claims.append(claim)
            if claims:
                item[polarity] = claims

        for polarity, supplemental in _query_grounded_conflict_claims(query_terms, source_spans).items():
            existing = item.setdefault(polarity, [])
            existing_ids = {str(claim.get("source_span_id") or "") for claim in existing if isinstance(claim, dict)}
            for claim in supplemental:
                if str(claim.get("source_span_id") or "") in existing_ids:
                    continue
                existing.insert(0, claim)
                existing_ids.add(str(claim.get("source_span_id") or ""))

        for polarity in ["positive", "negative", "uncertain"]:
            claims = item.get(polarity)
            if isinstance(claims, list):
                claims.sort(key=_claim_sort_key, reverse=True)

        promoted_positive = _promoted_positive_uncertain_claim(item, query_terms)
        if promoted_positive:
            positives = item.setdefault("positive", [])
            if not any(claim.get("source_span_id") == promoted_positive.get("source_span_id") for claim in positives):
                positives.insert(0, promoted_positive)
                positives.sort(key=_claim_sort_key, reverse=True)

        resolution = _conflict_resolution_candidate(item)
        if resolution:
            item["resolution_candidate"] = resolution
        if any(item.get(key) for key in ["positive", "negative", "uncertain"]):
            out.append(item)
    return out


def _claim_sort_key(claim: dict[str, Any]) -> tuple[float, float, int, int]:
    return (
        _float_value(claim.get("evidence_weight")),
        _float_value(claim.get("grounding_score")),
        1 if claim.get("speaker") == "user" else 0,
        -int(claim.get("timeline_index") or 10**9),
    )


def _promoted_positive_uncertain_claim(item: dict[str, Any], query_terms: set[str]) -> dict[str, Any] | None:
    uncertain_claims = item.get("uncertain") if isinstance(item.get("uncertain"), list) else []
    if not uncertain_claims:
        return None
    ranked: list[tuple[float, dict[str, Any]]] = []
    for claim in uncertain_claims:
        text = str(claim.get("claim") or "")
        role = str(claim.get("claim_role") or "")
        if role not in {"completed_current_explicit", "past_experience_explicit", "current_state_explicit"}:
            continue
        overlap = len(query_terms & _model_view_terms(text))
        if overlap < 2:
            continue
        ranked.append((_float_value(claim.get("evidence_weight")) + 0.1 * overlap, claim))
    if not ranked:
        return None
    ranked.sort(key=lambda ranked_item: (-ranked_item[0], str(ranked_item[1].get("source_span_id") or "")))
    promoted = dict(ranked[0][1])
    promoted["claim_polarity"] = "positive"
    return promoted


def _conflict_query_terms(query: str) -> set[str]:
    generic = {
        "about",
        "before",
        "ever",
        "have",
        "that",
        "this",
        "usually",
        "with",
        "worked",
        "project",
        "projects",
        "handled",
    }
    return {term for term in _model_view_terms(query) if term not in generic}


def _conflict_claim_record(
    query_terms: set[str],
    span_id: str,
    span: dict[str, Any],
    *,
    expected_polarity: str,
) -> dict[str, Any] | None:
    text = str(span.get("content") or "")
    if not text.strip():
        return None
    claim_text = _query_grounded_claim_text(query_terms, text)
    polarity = _claim_polarity(claim_text)
    if expected_polarity in {"positive", "negative"} and polarity not in {expected_polarity, "unknown"}:
        return None
    score = _conflict_claim_query_score(query_terms, span, claim_text=claim_text)
    if polarity == expected_polarity:
        score += 0.18
    if _claim_is_user_statement(claim_text):
        score += 0.12
    role = _claim_role(claim_text, speaker=str(span.get("speaker") or ""), polarity=polarity)
    weight = _claim_evidence_weight(role, polarity, score)
    return {
        "source_span_id": span_id,
        "speaker": span.get("speaker"),
        "timeline_index": span.get("timeline_index") or span.get("history_index"),
        "claim": claim_text,
        "claim_polarity": polarity,
        "claim_role": role,
        "grounding_score": round(score, 3),
        "evidence_weight": round(weight, 3),
    }


def _query_grounded_conflict_claims(query_terms: set[str], source_spans: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {"positive": [], "negative": []}
    for span in source_spans[:64]:
        if str(span.get("speaker") or "") != "user":
            continue
        text = str(span.get("content") or "")
        if not text.strip():
            continue
        claim_text = _query_grounded_claim_text(query_terms, text)
        polarity = _claim_polarity(claim_text)
        if polarity not in {"positive", "negative"}:
            continue
        overlap = len(query_terms & _model_view_terms(claim_text))
        if query_terms and overlap < max(1, min(2, len(query_terms))):
            continue
        record = _conflict_claim_record(
            query_terms,
            str(span.get("id") or span.get("source_span_id") or ""),
            span,
            expected_polarity=polarity,
        )
        if not record:
            continue
        record["grounding_score"] = round(_float_value(record.get("grounding_score")) + 0.22 + 0.08 * overlap, 3)
        record["evidence_weight"] = round(_claim_evidence_weight(str(record.get("claim_role") or ""), polarity, _float_value(record["grounding_score"])), 3)
        buckets[polarity].append(record)
    for polarity, claims in buckets.items():
        claims.sort(key=_claim_sort_key, reverse=True)
        buckets[polarity] = claims[:3]
    return buckets


def _claim_polarity(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(?:never|haven['’]?t|have not|had not|did not|didn['’]?t|no longer|not yet|without ever)\b", lower):
        return "negative"
    if re.search(
        r"\b(?:i['’]?ve|i have|i had|i got|i started|i completed|i attended|i invited|i declined|i registered|"
        r"i submitted|i used|i integrated|i worked|i collaborated|i enrolled|i read|i made|i finalized|"
        r"i created|i built|i implemented|i handled|i wrote|i met|i received|i managed|i obtained|i fixed|"
        r"i learned|i['’]?m feeling|i am feeling|i feel|i['’]?m usually|i am usually|i['’]?m currently|i am currently)\b",
        lower,
    ):
        return "positive"
    if re.search(r"\b(?:passed|success rate|test runs|already|current(?:ly)?|scheduled|planned|recommended)\b", lower):
        return "positive"
    return "unknown"


def _claim_role(text: str, *, speaker: str, polarity: str) -> str:
    lower = text.lower()
    if speaker not in {"user", "document"}:
        return "assistant_inference"
    if polarity == "negative" and re.search(r"\b(?:never|haven['’]?t|have not|had not|did not|didn['’]?t|not yet|without ever)\b", lower):
        return "direct_negative"
    if re.search(
        r"\b(?:i['’]?ve|i have|i had)\s+(?:completed|attended|used|integrated|worked|collaborated|enrolled|"
        r"read|made|finalized|created|built|implemented|handled|written|wrote|met|received|managed|obtained|"
        r"fixed|tested|submitted|registered|joined|started|learned)\b",
        lower,
    ):
        return "completed_current_explicit"
    if re.search(r"\bi\s+(?:completed|attended|used|integrated|worked|collaborated|enrolled|read|met|obtained|fixed|tested|learned)\b", lower):
        return "past_experience_explicit"
    if re.search(r"\b(?:i['’]?ve|i have)\s+been\s+(?:using|working|tracking|reading|listening|testing|attending|meeting|drafting|managing|collaborating)\b", lower):
        return "current_state_explicit"
    if re.search(r"\b(?:passed with|success rate|test runs|already\s+(?:completed|used|implemented|tested|obtained|met|attended))\b", lower):
        return "completed_current_explicit"
    if re.search(r"\b(?:friend|colleague|mentor|manager|teacher|coach)\b.{0,80}\brecommended\b|\brecommended\b.{0,80}\b(?:workshop|course|service|tool|event)\b", lower):
        return "related_opportunity"
    if re.search(r"\b(?:scheduled|planning|planned|trying to|hoping to|want to|would like to|considering|thinking of|getting ready|preparing to)\b", lower):
        return "planned_or_intended"
    if re.search(r"\b(?:can you|could you|help me|not sure|wondering how|what should|should i|how can i)\b", lower):
        return "help_request"
    if re.search(r"\b(?:i['’]?m using|i am using|i['’]?m currently|i am currently|i['’]?ve got|i have a|my)\b", lower):
        return "current_state_explicit"
    return "user_statement"


def _claim_evidence_weight(role: str, polarity: str, score: float) -> float:
    role_weights = {
        "completed_current_explicit": 1.0,
        "past_experience_explicit": 0.95,
        "current_state_explicit": 0.82,
        "direct_negative": 1.0,
        "related_opportunity": 0.42,
        "user_statement": 0.35,
        "planned_or_intended": 0.24,
        "help_request": 0.18,
        "assistant_inference": 0.08,
    }
    base = role_weights.get(role, 0.25)
    if polarity == "unknown":
        base *= 0.75
    return max(0.0, base + min(0.25, max(0.0, score) * 0.08))


def _query_grounded_claim_text(query_terms: set[str], text: str) -> str:
    text = _strip_dialogue_marker(text)
    fragment = _best_claim_fragment(query_terms, text)
    if not fragment:
        fragment = text
    fragment = _strip_dialogue_marker(fragment)
    fragment = _trim_question_tail(fragment)
    fragment = re.sub(r"\s+", " ", fragment).strip()
    if len(fragment) > 260:
        fragment = fragment[:257].rstrip() + "..."
    return fragment


def _best_claim_fragment(query_terms: set[str], text: str) -> str:
    fragments = _claim_fragments(text)
    if not fragments:
        return text.strip()
    scored: list[tuple[float, int, str]] = []
    for index, fragment in enumerate(fragments[:18]):
        lower = fragment.lower()
        terms = _model_view_terms(fragment)
        overlap = len(query_terms & terms)
        score = 0.22 * overlap
        if _claim_is_user_statement(fragment):
            score += 0.12
        if re.search(r"\b(?:never|haven['’]?t|have not|i['’]?ve|i have|i had|i met|i got|i completed|i used|i tested|i obtained|i attended)\b", lower):
            score += 0.22
        if re.search(r"\b(?:trying to|can you|help me|not sure|wondering|should i)\b", lower):
            score -= 0.06
        if re.search(r"\b(?:but|however|though|although|while|whereas)\b", lower):
            score -= 0.35
        if re.search(r"\b(?:api|version|v\d|date|deadline|\d{4}|\d+%|\$\d|[A-Z][A-Za-z]+)\b", fragment):
            score += 0.06
        scored.append((score, -index, fragment))
    scored.sort(reverse=True)
    return scored[0][2]


def _claim_fragments(text: str) -> list[str]:
    text = _strip_dialogue_marker(text)
    sentence_parts = [
        part.strip(" -")
        for part in re.split(r"(?<=[.!?])\s+|\n+|(?=\s*(?:[-*]|\d+[.)])\s+)", text)
        if part.strip(" -")
    ]
    if not sentence_parts:
        sentence_parts = [text.strip()]
    fragments: list[str] = []
    for sentence in sentence_parts[:8]:
        fragments.append(sentence)
        clauses = re.split(r"\s+(?:but|however|though|although|while|whereas)\s+|,\s+(?:but|however|though|although|while|whereas)\s+", sentence, flags=re.I)
        if len(clauses) > 1:
            fragments.extend(part.strip(" ,;:-") for part in clauses if part.strip(" ,;:-"))
    seen: set[str] = set()
    out: list[str] = []
    for fragment in fragments:
        key = re.sub(r"\W+", " ", fragment.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(fragment)
    return out


def _conflict_claim_query_score(query_terms: set[str], span: dict[str, Any], *, claim_text: str | None = None) -> float:
    text = claim_text if claim_text is not None else str(span.get("content") or "")
    lower = text.lower()
    terms = _model_view_terms(text)
    overlap = len(query_terms & terms)
    score = 0.08 * overlap
    if str(span.get("speaker") or "") == "user":
        score += 0.20
    if re.search(r"\b(?:i['’]?ve|i have|i had|i never|i attended|i invited|i declined|i registered|i scheduled|i started|i met|i obtained)\b", lower):
        score += 0.18
    if re.search(r"\b(?:can you|here are|steps|tips|recommend|strategy|strategies|should|could)\b", lower):
        score -= 0.12
    source = str(span.get("candidate_source") or "")
    if "exact_filter" in source:
        score += 0.08
    if "broad_raw_recall" in source and overlap < 2:
        score -= 0.10
    return score


def _conflict_resolution_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    positive_claims = item.get("positive") if isinstance(item.get("positive"), list) else []
    negative_claims = item.get("negative") if isinstance(item.get("negative"), list) else []
    uncertain_claims = item.get("uncertain") if isinstance(item.get("uncertain"), list) else []
    if not positive_claims or not negative_claims:
        return None

    positive_decisive = [
        claim
        for claim in positive_claims
        if str(claim.get("claim_role") or "") in {"completed_current_explicit", "past_experience_explicit", "current_state_explicit"}
        and str(claim.get("speaker") or "") == "user"
    ]
    negative_decisive = [
        claim
        for claim in negative_claims
        if str(claim.get("claim_role") or "") == "direct_negative" and str(claim.get("speaker") or "") == "user"
    ]
    for claim in uncertain_claims:
        if (
            str(claim.get("claim_role") or "") in {"completed_current_explicit", "past_experience_explicit", "current_state_explicit"}
            and str(claim.get("speaker") or "") == "user"
        ):
            positive_decisive.append(claim)

    if not positive_decisive:
        return None
    pos_weight = sum(_float_value(claim.get("evidence_weight")) for claim in positive_decisive)
    neg_weight = sum(_float_value(claim.get("evidence_weight")) for claim in negative_decisive)
    if pos_weight < max(1.0, neg_weight + 0.55):
        return None
    return {
        "resolved_answer": "yes",
        "confidence": 0.62 if pos_weight < neg_weight + 1.0 else 0.68,
        "basis": (
            "Explicit user completed/current evidence outweighs the direct negative claim; the answer should still "
            "mention the contradiction and should not treat plans, help requests, or assistant advice as completed facts."
        ),
        "support_counts": {"positive_or_current": len(positive_decisive), "negative": len(negative_decisive)},
        "support_weights": {"positive_or_current": round(pos_weight, 3), "negative": round(neg_weight, 3)},
    }


def _claim_is_user_statement(text: str) -> bool:
    return bool(re.search(r"\b(?:i|i['’]?ve|i have|i had|my|we|we['’]?ve|we have|our)\b", text, flags=re.I))


def _trim_question_tail(sentence: str) -> str:
    parts = re.split(
        r"\s*,?\s+(?:can you|could you|what are|what should|how can|do you think|would this|should i|should we)\b",
        sentence,
        maxsplit=1,
        flags=re.I,
    )
    trimmed = parts[0].strip(" ,;:-")
    return trimmed or sentence.strip()


def _strip_dialogue_marker(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:user|assistant)\s*:\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*->->\s*\d+,\d+\s*", " ", text).strip()
    return text


def _model_view_terms(text: str) -> set[str]:
    stop = {
        "about",
        "after",
        "again",
        "around",
        "before",
        "between",
        "could",
        "from",
        "give",
        "have",
        "help",
        "into",
        "like",
        "over",
        "should",
        "that",
        "their",
        "there",
        "these",
        "this",
        "through",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
        "your",
    }
    return {token for token in re.findall(r"[a-z0-9][a-z0-9'-]{2,}", text.lower()) if token not in stop}


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
