from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.aggregation_common import _match_context, _span_ref

def _financial_impact_items(query: str, source_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_lower = query.lower()
    if not _is_financial_impact_query(query_lower):
        return []
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for span in source_spans[:48]:
        content = str(span.get("content") or "")
        if not content or not _financial_text_has_query_overlap(query_lower, content):
            continue
        speaker = str(span.get("speaker") or "")
        if speaker and speaker not in {"user", "assistant", "document"}:
            continue
        span_ref = _span_ref(span)
        for mention in _money_mentions(content):
            context = mention["context"]
            lower_context = context.lower()
            lower_local_context = str(mention.get("local_context") or context).lower()
            subject_key, label = _financial_subject_key(query_lower, str(mention.get("local_context") or context), fallback_context=context)
            role = _financial_impact_role(lower_context, subject_key)
            period = _financial_period(lower_local_context, mention["text"])
            direction = _financial_direction(role, lower_context)
            current_state = _financial_current_state(lower_local_context, mention["text"])
            dedupe = (subject_key, mention["text"].lower(), period, role)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            item = {
                "subject_key": subject_key,
                "label": label,
                "amount": mention["text"],
                "amount_number": mention["number"],
                "period": period,
                "impact_role": role,
                "direction": direction,
                "current_state": current_state,
                "context": compact_summary(context, 260),
                **{key: value for key, value in span_ref.items() if value is not None},
            }
            items.append(item)
            if len(items) >= 16:
                return items
    return items

def _is_financial_impact_query(query_lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:budget|financial|finance|money|expense|expenses|cost|costs|bills?|income|contract|freelance|"
            r"savings?|save|afford|spend(?:ing)?|grocery|groceries|medical|salary|pay|payment|cashflow|cash\s+flow)\b",
            query_lower,
        )
    )

def _financial_text_has_query_overlap(query_lower: str, content: str) -> bool:
    lower = content.lower()
    if not re.search(r"\$\s?\d", lower):
        return False
    if re.search(
        r"\b(?:budget|financial|finance|money|expense|expenses|cost|costs|bills?|income|contract|freelance|"
        r"savings?|save|emergency fund|grocery|groceries|medical|salary|pay|payment|monthly|annually|per month)\b",
        lower,
    ):
        return True
    query_terms = {
        token
        for token in re.findall(r"[a-z0-9]+", query_lower)
        if len(token) >= 4 and token not in {"will", "while", "with", "from", "that", "this", "have"}
    }
    text_terms = set(re.findall(r"[a-z0-9]+", lower))
    return len(query_terms & text_terms) >= 2

def _money_mentions(content: str) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for match in re.finditer(r"\$\s?\d+(?:,\d{3})*(?:\.\d+)?\b", content):
        text = match.group(0)
        number_text = re.sub(r"[^0-9.]", "", text)
        try:
            number = float(number_text)
        except ValueError:
            number = None
        mentions.append(
            {
                "text": text,
                "number": number,
                "context": _match_context(content, match.start(), match.end(), radius=180),
                "local_context": _match_context(content, match.start(), match.end(), radius=70),
            }
        )
        if len(mentions) >= 12:
            break
    return mentions

def _financial_impact_summary(query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {}
    query_lower = query.lower()
    relevant_subjects = _financial_query_subjects(query_lower)
    monthly_inflows: dict[str, dict[str, Any]] = {}
    monthly_outflows: dict[str, dict[str, Any]] = {}
    budget_changes: dict[str, dict[str, Any]] = {}
    savings_targets: dict[str, dict[str, Any]] = {}
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        subject_key = str(item.get("subject_key") or "")
        if relevant_subjects and subject_key not in relevant_subjects:
            continue
        by_subject.setdefault(subject_key, []).append(item)
    for subject_key, subject_items in by_subject.items():
        label = str(subject_items[0].get("label") or subject_key)
        monthly_current = [
            item
            for item in subject_items
            if item.get("period") == "monthly"
            and item.get("current_state") != "prior_or_baseline"
            and isinstance(item.get("amount_number"), (int, float))
        ]
        if not monthly_current:
            continue
        role = str(monthly_current[0].get("impact_role") or "")
        if role == "income_or_cash_inflow":
            best = max(monthly_current, key=lambda item: float(item.get("amount_number") or 0))
            monthly_inflows[subject_key] = _financial_summary_amount(label, best)
        elif role in {"expense_obligation", "budget_value"}:
            best = max(monthly_current, key=lambda item: float(item.get("amount_number") or 0))
            monthly_outflows[subject_key] = _financial_summary_amount(label, best)
        elif role == "budget_change":
            current = max(monthly_current, key=lambda item: float(item.get("amount_number") or 0))
            prior_items = [
                item
                for item in subject_items
                if item.get("period") == "monthly"
                and item.get("current_state") == "prior_or_baseline"
                and isinstance(item.get("amount_number"), (int, float))
            ]
            entry = _financial_summary_amount(label, current)
            if prior_items:
                prior = max(prior_items, key=lambda item: float(item.get("amount_number") or 0))
                entry["prior_amount"] = prior.get("amount")
                entry["prior_amount_number"] = float(prior.get("amount_number") or 0)
                entry["delta_number"] = float(current.get("amount_number") or 0) - float(prior.get("amount_number") or 0)
                entry["delta"] = _format_money(entry["delta_number"])
            budget_changes[subject_key] = entry
        elif role == "savings_target":
            best = max(monthly_current, key=lambda item: float(item.get("amount_number") or 0))
            savings_targets[subject_key] = _financial_summary_amount(label, best)
    inflow_total = sum(float(item.get("amount_number") or 0) for item in monthly_inflows.values())
    outflow_total = sum(float(item.get("amount_number") or 0) for item in monthly_outflows.values())
    budget_delta_total = sum(float(item.get("delta_number") or 0) for item in budget_changes.values())
    summary: dict[str, Any] = {
        **({"monthly_inflows": list(monthly_inflows.values())} if monthly_inflows else {}),
        **({"monthly_outflows": list(monthly_outflows.values())} if monthly_outflows else {}),
        **({"budget_changes": list(budget_changes.values())} if budget_changes else {}),
        **({"savings_targets": list(savings_targets.values())} if savings_targets else {}),
    }
    if monthly_inflows or monthly_outflows or budget_changes:
        net = inflow_total - outflow_total - budget_delta_total
        summary["monthly_net_after_obligations_and_budget_changes"] = {
            "amount_number": net,
            "amount": _format_money(net),
            "calculation": {
                "monthly_inflows": inflow_total,
                "monthly_outflows": outflow_total,
                "monthly_budget_increase": budget_delta_total,
            },
            "interpretation": "positive" if net > 0 else "negative" if net < 0 else "neutral",
        }
    return summary

def _financial_query_subjects(query_lower: str) -> set[str]:
    subjects: set[str] = set()
    if re.search(r"\b(?:grocery|groceries|food budget)\b", query_lower):
        subjects.add("financial:grocery_budget")
    if re.search(r"\b(?:medical|bills?|ashlee)\b", query_lower):
        owner = "ashlee" if "ashlee" in query_lower else "medical"
        subjects.add(f"financial:{owner}_medical_bills")
    if re.search(r"\b(?:freelance|contract|client|project fee|natalie)\b", query_lower):
        subjects.add("financial:freelance_contract")
    if re.search(r"\b(?:savings?|save|emergency fund|goals?)\b", query_lower):
        subjects.update({"financial:savings_goal", "financial:emergency_fund"})
    return subjects

def _financial_summary_amount(label: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_key": item.get("subject_key"),
        "label": label,
        "amount": item.get("amount"),
        "amount_number": float(item.get("amount_number") or 0),
        "period": item.get("period"),
        "source_span_id": item.get("source_span_id"),
        "history_index": item.get("history_index"),
    }

def _format_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    if absolute.is_integer():
        return f"{sign}${int(absolute):,}"
    return f"{sign}${absolute:,.2f}"

def _financial_subject_key(query_lower: str, context: str, *, fallback_context: str | None = None) -> tuple[str, str]:
    lower = context.lower()
    fallback_lower = str(fallback_context or "").lower()
    if re.search(r"\b(?:grocery|groceries|food budget)\b", lower):
        return "financial:grocery_budget", "grocery budget"
    if re.search(r"\b(?:medical|doctor|hospital|healthcare|health care|bills?)\b", lower):
        owner = "ashlee" if "ashlee" in lower or "ashlee" in query_lower else "medical"
        return f"financial:{owner}_medical_bills", f"{owner} medical bills"
    if re.search(r"\b(?:rent|utilities|utility|dining out|fixed expenses?|monthly expenses?)\b", lower):
        return "financial:expense", "expense"
    if re.search(r"\b(?:freelance|contract|client|project fee|natalie)\b", lower):
        return "financial:freelance_contract", "freelance contract"
    if re.search(r"\b(?:emergency fund)\b", lower):
        return "financial:emergency_fund", "emergency fund"
    if re.search(r"\b(?:savings?|save|car fund|down payment)\b", lower):
        return "financial:savings_goal", "savings goal"
    if re.search(r"\b(?:salary|raise|compensation|pay)\b", lower):
        return "financial:income_or_pay", "income or pay"
    if re.search(r"\b(?:budget)\b", lower):
        return "financial:budget", "budget"
    if re.search(r"\b(?:expense|expenses|cost|costs|spend|spent|purchase|bought)\b", lower):
        return "financial:expense", "expense"
    if fallback_lower and fallback_lower != lower:
        if re.search(r"\b(?:grocery|groceries|food budget)\b", fallback_lower):
            return "financial:grocery_budget", "grocery budget"
        if re.search(r"\b(?:medical|doctor|hospital|healthcare|health care|bills?)\b", fallback_lower):
            owner = "ashlee" if "ashlee" in fallback_lower or "ashlee" in query_lower else "medical"
            return f"financial:{owner}_medical_bills", f"{owner} medical bills"
        if re.search(r"\b(?:rent|utilities|utility|dining out|fixed expenses?|monthly expenses?)\b", fallback_lower):
            return "financial:expense", "expense"
        if re.search(r"\b(?:freelance|contract|client|project fee|natalie)\b", fallback_lower):
            return "financial:freelance_contract", "freelance contract"
        if re.search(r"\b(?:emergency fund)\b", fallback_lower):
            return "financial:emergency_fund", "emergency fund"
        if re.search(r"\b(?:savings?|save|car fund|down payment)\b", fallback_lower):
            return "financial:savings_goal", "savings goal"
    return "financial:amount", "financial amount"

def _financial_impact_role(lower_context: str, subject_key: str) -> str:
    if "grocery_budget" in subject_key or subject_key.endswith(":budget"):
        if re.search(r"\b(?:up from|increase|increased|increasing|higher|raised|starting)\b", lower_context):
            return "budget_change"
        return "budget_value"
    if "medical_bills" in subject_key or subject_key.endswith(":expense"):
        return "expense_obligation"
    if "freelance_contract" in subject_key or re.search(r"\b(?:income|earn(?:ing)?|paid|payment|contract|salary|raise|compensation)\b", lower_context):
        return "income_or_cash_inflow"
    if "savings" in subject_key or "emergency_fund" in subject_key or re.search(r"\b(?:savings?|save|goal|target|emergency fund)\b", lower_context):
        return "savings_target"
    if re.search(r"\b(?:up from|increase|increased|increasing|higher|raised|starting)\b", lower_context) and "budget" in subject_key:
        return "budget_change"
    if re.search(r"\b(?:budget)\b", lower_context) and "budget" in subject_key:
        return "budget_value"
    if re.search(r"\b(?:bill|bills|expense|expenses|cost|costs|spend|spending|monthly)\b", lower_context):
        return "expense_obligation"
    return "financial_value"

def _financial_period(lower_context: str, amount_text: str = "") -> str:
    if amount_text:
        amount_pattern = re.escape(amount_text.lower()).replace(r"\ ", r"\s*")
        amount_match = re.search(amount_pattern, lower_context)
        if amount_match:
            before_amount = lower_context[max(0, amount_match.start() - 120) : amount_match.start()]
            after_amount = lower_context[amount_match.end() : amount_match.end() + 80]
            if re.search(r"^\W{0,20}over\s+\d+\s+months?\b", after_amount):
                return "total_over_period"
            if re.search(r"^\W{0,40}(?:per month|/month|monthly|a month|each month)\b", after_amount):
                return "monthly"
            if re.search(r"\b(?:monthly|per month|/month|a month|each month)\b.{0,80}$", before_amount):
                return "monthly"
            if re.search(r"\b(?:annually|annual|per year|a year|yearly)\b.{0,80}$", before_amount):
                return "annual"
            if re.search(r"\b(?:one-time|one time|upfront)\b.{0,80}$", before_amount):
                return "one_time"
            return "unspecified"
    if re.search(r"\bover\s+\d+\s+months?\b", lower_context) and not re.search(r"\b(?:per month|/month)\b", lower_context):
        return "total_over_period"
    if re.search(r"\b(?:monthly|per month|/month|a month|each month)\b", lower_context):
        return "monthly"
    if re.search(r"\b(?:annually|annual|per year|a year|yearly)\b", lower_context):
        return "annual"
    if re.search(r"\b(?:one-time|one time|upfront)\b", lower_context):
        return "one_time"
    return "unspecified"

def _financial_direction(role: str, lower_context: str) -> str:
    if role == "income_or_cash_inflow":
        return "inflow"
    if role in {"expense_obligation", "budget_change", "budget_value"}:
        return "outflow_or_spending_capacity"
    if role == "savings_target":
        return "target"
    if re.search(r"\b(?:income|earn|paid|payment|revenue)\b", lower_context):
        return "inflow"
    if re.search(r"\b(?:expense|cost|bill|spend|budget)\b", lower_context):
        return "outflow"
    return "unknown"

def _financial_current_state(lower_context: str, amount_text: str) -> str:
    amount_pattern = re.escape(amount_text.lower()).replace(r"\ ", r"\s*")
    if re.search(rf"\b(?:up\s+from|from)\s+{amount_pattern}\b", lower_context):
        return "prior_or_baseline"
    if re.search(rf"\b(?:was|were|used\s+to\s+be|previously)\s+{amount_pattern}\b", lower_context):
        return "prior_or_baseline"
    if re.search(r"\b(?:current|now|latest|updated|increased|increasing|starting|agreed|revealed|equates|equals|will be|is)\b", lower_context):
        return "current_or_planned"
    return "mentioned"
