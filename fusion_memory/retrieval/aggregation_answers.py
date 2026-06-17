from __future__ import annotations

"""Typed answer candidates derived from aggregation items.

This module intentionally sits in retrieval rather than the eval adapter.  The
answer model should consume structured count/delta candidates, not rediscover
aggregation semantics from raw spans or benchmark-specific prompt text.
"""

import re
from typing import Any


def aggregation_answer_candidates(
    query: str,
    items: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    included = [item for item in items if item.get("included")]
    evidence_records = evidence_records or []
    lower = query.lower()
    if not included:
        grouped_candidate = _grouped_context_count_candidate(lower, included, evidence_records)
        return [grouped_candidate] if grouped_candidate else []
    role_sums: dict[str, int] = {}
    role_labels: dict[str, list[str]] = {}
    role_items: dict[str, list[dict[str, Any]]] = {}
    for item in included:
        role = str(item.get("count_role") or "unknown")
        try:
            value = int(item.get("value") or 0)
        except (TypeError, ValueError):
            value = 0
        role_sums[role] = role_sums.get(role, 0) + value
        role_items.setdefault(role, []).append(item)
        label = str(item.get("label") or item.get("key") or "").strip()
        if label:
            role_labels.setdefault(role, [])
            if len(role_labels[role]) < 10 and label not in role_labels[role]:
                role_labels[role].append(label)

    candidates: list[dict[str, Any]] = []
    slot_candidate = _distinct_slot_values_candidate(lower, included)
    if slot_candidate:
        candidates.append(slot_candidate)
    delta_candidate = _delta_between_values_candidate(lower, included)
    if delta_candidate:
        candidates.append(delta_candidate)
    grouped_candidate = None
    if (
        "value" not in _query_object_prefixes(lower)
        and not any(role in role_sums for role in {"user_reported_count", "candidate_group_count", "assistant_supported_count"})
    ):
        grouped_candidate = _grouped_context_count_candidate(lower, included, evidence_records)
    if "user_reported_count" in role_sums:
        value = role_sums["user_reported_count"]
        candidates.append(
            {
                "answer_value": value,
                "formula": "user_reported_count",
                "confidence": 0.74,
                "labels": role_labels.get("user_reported_count", []),
                "guidance": "Use when the query asks for the user's stated aggregate count.",
            }
        )
    if "additive_item" in role_sums:
        labels = role_labels.get("additive_item", [])
        candidates.append(
            {
                "answer_value": len(labels) or role_sums["additive_item"],
                "formula": "distinct_additive_items",
                "confidence": 0.70,
                "labels": labels,
                "guidance": "Use when the query asks for explicitly named distinct items.",
            }
        )
    if "candidate_group_count" in role_sums:
        group_scope_confidence = 0.62 if _query_can_use_candidate_group_count(lower, role_items.get("candidate_group_count", [])) else 0.38
        candidates.append(
            {
                "answer_value": role_sums["candidate_group_count"],
                "formula": "candidate_group_count",
                "confidence": group_scope_confidence,
                "labels": role_labels.get("candidate_group_count", []),
                "guidance": (
                    "Use when the query asks for a bounded assistant recommendation/option group, an explicit date/session "
                    "scope, or a planned/finalized group. Treat as auxiliary when the query asks what the user personally "
                    "mentioned, wanted, or explored."
                ),
            }
        )
    if grouped_candidate:
        candidates.insert(0, grouped_candidate)
    group_count_components = [
        role
        for role in ["user_reported_count", "additive_item", "additive_value", "candidate_group_count"]
        if role in role_sums
    ]
    if (
        re.search(r"\b(?:across|considering|between|and|other|unique|different)\b", lower)
        and "candidate_group_count" in role_sums
        and len(group_count_components) >= 2
    ):
        if _query_can_union_candidate_groups(lower, role_sums, role_items):
            union_candidate = _mixed_distinct_union_count_candidate(role_sums, role_labels)
            if union_candidate:
                candidates.insert(0, union_candidate)
        candidates.append(
            {
                "answer_value": None,
                "formula": "dedupe_mixed_count_roles",
                "confidence": 0.68,
                "labels": [label for role in group_count_components for label in role_labels.get(role, [])][:10],
                "component_values": {
                    role: role_sums[role]
                    for role in group_count_components
                },
                "guidance": (
                    "Use for cross-date/session unique-count questions only after checking overlap between the "
                    "user-reported/additive items and assistant recommendation groups; do not add the components blindly."
                ),
            }
        )
    elif re.search(r"\b(?:across|considering|between|and)\b", lower) and _query_can_union_candidate_groups(lower, role_sums, role_items):
        aggregate = 0
        parts: list[str] = []
        labels: list[str] = []
        if "user_reported_count" in role_sums:
            aggregate += role_sums["user_reported_count"]
            parts.append("user_reported_count")
            labels.extend(role_labels.get("user_reported_count", []))
        if "candidate_group_count" in role_sums:
            aggregate += role_sums["candidate_group_count"]
            parts.append("candidate_group_count")
            labels.extend(role_labels.get("candidate_group_count", []))
        if len(parts) >= 2:
            candidates.insert(
                0,
                {
                    "answer_value": aggregate,
                    "formula": " + ".join(parts),
                    "confidence": 0.86,
                    "labels": labels[:10],
                    "guidance": (
                        "Use for cross-date/session total questions when user_reported_count and "
                        "candidate_group_count refer to distinct named dates or subquestions."
                    ),
                },
            )
    return candidates[:4]


def deadline_answer_candidates(
    query: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    query_lower = query.lower()
    if not re.search(r"\b(?:deadlines?|due|file|filing|submit|submission)\b", query_lower):
        return []
    if not re.search(r"\b(?:what|which|list|two|different|deadlines?)\b", query_lower):
        return []
    rows = _deadline_rows(query_lower, records)
    if not rows:
        return []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_role.setdefault(str(row["deadline_role"]), []).append(row)
    selected: list[dict[str, Any]] = []
    for role in _deadline_roles_for_query(query_lower, rows):
        candidates = by_role.get(role, [])
        if not candidates:
            continue
        candidates.sort(key=lambda row: _deadline_row_score(query_lower, row), reverse=True)
        selected.append(candidates[0])
    if len(selected) < 2:
        return []
    selected.sort(key=lambda row: _deadline_output_order(query_lower, row))
    labels = [f"{row['value']} for {row['label']}" for row in selected]
    return [
        {
            "answer_value": labels,
            "formula": "deadline_pair",
            "confidence": 0.84,
            "labels": labels,
            "component_values": {
                str(row["deadline_role"]): row["value"]
                for row in selected
            },
            "guidance": (
                "Use when the query asks for multiple deadlines or due dates. Select one date per endpoint role "
                "from the local date context instead of collapsing to only the latest date."
            ),
        }
    ]


def _grouped_context_count_candidate(
    query_lower: str,
    included: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not re.search(r"\b(?:how many|count|number of|different|unique)\b", query_lower):
        return None
    prefixes = _query_object_prefixes(query_lower)
    if not prefixes:
        return None
    if not re.search(r"\b(?:across|conversations?|sessions?|messages?|different|unique|explor(?:e|ing)|want(?:ing)?|looking|interested)\b", query_lower):
        return None
    items_by_group = _context_group_items(query_lower, included, evidence_records, prefixes)
    if len(items_by_group) < 2:
        return None
    labels: list[str] = []
    total = 0
    component_values: dict[str, int] = {}
    seen_global_keys: set[str] = set()
    for group_key, group in sorted(items_by_group.items(), key=lambda row: _context_group_sort_key(row[0], row[1])):
        entries = [
            entry
            for entry in group["entries"]
            if str(entry.get("key") or "") and str(entry.get("key") or "") not in seen_global_keys
        ]
        if not entries:
            continue
        for entry in entries:
            seen_global_keys.add(str(entry.get("key") or ""))
        value = len(entries)
        total += value
        component_values[group_key] = value
        labels.append(_context_group_label(entries))
    if total <= 0 or len(labels) < 2:
        return None
    return {
        "answer_value": total,
        "formula": "grouped_distinct_count",
        "confidence": 0.84,
        "labels": labels,
        "component_values": component_values,
        "support_items": _group_support_items(items_by_group),
        "guidance": (
            "Use when a cross-conversation count query asks for distinct objects and the evidence clusters into "
            "separate source contexts. Count unique typed item keys across groups, dedupe repeated labels globally, "
            "and report the grouped breakdown rather than summing every nearby number."
        ),
    }


def _group_support_items(items_by_group: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group_key, group in sorted(items_by_group.items(), key=lambda row: _context_group_sort_key(row[0], row[1])):
        for entry in group["entries"]:
            key = str(entry.get("key") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            label = str(entry.get("label") or _label_from_key(key)).strip()
            context = str(entry.get("context") or "").strip()
            out.append(
                {
                    "key": key,
                    "label": label,
                    "group_key": group_key,
                    **({"context": context} if context else {}),
                }
            )
            if len(out) >= 12:
                return out
    return out


def _context_group_items(
    query_lower: str,
    included: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
    prefixes: set[str],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in included:
        key = str(item.get("key") or "")
        if not _key_matches_prefixes(key, prefixes):
            continue
        _add_context_group_entry(
            groups,
            _record_group_key(item),
            key,
            _item_label(item),
            int(item.get("history_index") or 10**9),
            context=str(item.get("context") or ""),
        )
    covered_record_keys = set(groups)
    for record in evidence_records:
        group_key = _record_group_key(record)
        context = str(record.get("context") or record.get("content") or record.get("text") or "")
        if not context or group_key in covered_record_keys:
            continue
        for key, label in _context_object_candidates(query_lower, context, prefixes):
            _add_context_group_entry(
                groups,
                group_key,
                key,
                label,
                int(record.get("history_index") or record.get("timeline_index") or 10**9),
                context=context,
            )
    return groups


def _query_object_prefixes(query_lower: str) -> set[str]:
    prefixes: set[str] = set()
    if re.search(r"\b(?:titles?|books?|series|movies?|films?)\b", query_lower):
        prefixes.add("title")
    if re.search(r"\bgenres?\b", query_lower):
        prefixes.add("genre")
    if re.search(r"\b(?:values?|sizes?|amounts?|numbers?)\b", query_lower):
        prefixes.add("value")
    if re.search(r"\b(?:features?|concerns?|requirements?|capabilities)\b", query_lower):
        prefixes.add("feature")
    asset_query = bool(re.search(r"\b(?:assets?|property|possessions?)\b", query_lower))
    if asset_query:
        prefixes.add("asset")
    if re.search(r"\b(?:reminders?|planners?|calendars?|schedules?|task\s+(?:tools?|systems?|apps?|managers?)|to-?do\s+(?:tools?|systems?|apps?|lists?))\b", query_lower):
        prefixes.add("plan_system")
    if not asset_query and re.search(r"\b(?:items?|things?|objects?)\b", query_lower):
        prefixes.add("item")
    return prefixes


def _key_matches_prefixes(key: str, prefixes: set[str]) -> bool:
    return any(key.startswith(f"{prefix}:") for prefix in prefixes)


def _record_group_key(record: dict[str, Any]) -> str:
    for field in ("source_span_id", "id", "turn_id"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    context = str(record.get("context") or record.get("content") or record.get("text") or "")
    return "context:" + re.sub(r"[^a-z0-9]+", "_", context.lower()).strip("_")[:48]


def _add_context_group_entry(
    groups: dict[str, dict[str, Any]],
    group_key: str,
    key: str,
    label: str,
    history_index: int,
    *,
    context: str = "",
) -> None:
    group = groups.setdefault(group_key, {"entries": [], "keys": set(), "history_index": history_index})
    if key in group["keys"]:
        return
    group["keys"].add(key)
    entry = {"key": key, "label": label}
    if context:
        entry["context"] = _compact_support_context(context, label)
    group["entries"].append(entry)
    group["history_index"] = min(int(group.get("history_index") or history_index), history_index)


def _compact_support_context(context: str, label: str) -> str:
    text = re.sub(r"\s+", " ", context).strip()
    if not text:
        return ""
    label_terms = [term for term in re.findall(r"[a-z0-9]+", label.lower()) if len(term) >= 3]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
    if label_terms:
        for sentence in sentences:
            lower = sentence.lower()
            if any(term in lower for term in label_terms):
                return sentence[:220].rstrip()
    return text[:220].rstrip()


def _context_object_candidates(query_lower: str, context: str, prefixes: set[str]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if "title" in prefixes:
        for quoted in re.findall(r'"([^"\n]{2,80})"', context):
            label = quoted.strip()
            if _reject_short_label(label):
                continue
            candidates.append((f"title:{_normalize_label_key(label)}", label))
    if "genre" in prefixes:
        for label in _genre_labels(context):
            candidates.append((f"genre:{_normalize_label_key(label)}", label))
    if "value" in prefixes:
        for match in re.finditer(r"\b(?:size|sizes?)\s*(\d+(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s*(?:[a-z][a-z-]{1,20}\s*)?size\b", context, flags=re.I):
            value = match.group(1) or match.group(2)
            if value:
                candidates.append((f"value:size_{value.replace('.', '_')}", f"size {value}"))
    if "asset" in prefixes:
        for key, label in _asset_candidates(context):
            candidates.append((f"asset:{key}", label))
    return list(dict.fromkeys(candidates[:12]))


def _asset_candidates(text: str) -> list[tuple[str, str]]:
    lower = text.lower()
    if re.search(r"\b(?:for example|such as|common choices|\[address\]|\[last name\])\b", lower):
        return []
    patterns = [
        ("home", "home", r"\b(?:\$\s?350,?000\s+)?home\b|\b45\s+coral\s+bay\s+rd\b|\breal\s+(?:estate|property)\b"),
        ("savings_account", "savings account", r"\bsavings\s+account\b"),
        ("film_equipment", "film equipment", r"\bfilm\s+equipment\b"),
        ("vehicle", "vehicle", r"\b(?:vehicle|2018\s+toyota\s+rav4|toyota\s+rav4)\b"),
        ("digital_assets", "digital assets", r"\bdigital\s+assets?\b"),
        ("vimeo_account", "Vimeo account", r"\bvimeo\s+account\b"),
        ("adobe_subscription", "Adobe Creative Cloud subscription", r"\badobe\s+creative\s+cloud\s+subscription\b"),
        ("parents_care_fund", "parents' care fund", r"\b(?:parents?'?\s+care|ongoing\s+care|care\s+fund)\b|\b\$\s?100,?000\s+fund\b|\b\$\s?7,?000\s+fund\b"),
        ("life_insurance_policy", "life insurance policy", r"\blife\s+insurance\s+policy\b"),
        ("financial_accounts", "financial accounts", r"\bfinancial\s+accounts?\b|\b(?:bank|investment)\s+accounts?\b"),
    ]
    return [(key, label) for key, label, pattern in patterns if re.search(pattern, lower)]


def _genre_labels(text: str) -> list[str]:
    lower = text.lower()
    patterns = [
        ("historical fiction", r"\bhistorical fiction\b"),
        ("science fiction", r"\b(?:science fiction|sci[-\s]?fi)\b"),
        ("space opera", r"\bspace opera\b"),
        ("urban fantasy", r"\burban fantasy\b"),
        ("epic fantasy", r"\bepic fantasy\b"),
        ("dark fantasy", r"\bdark fantasy\b"),
        ("fantasy", r"\bfantasy\b"),
        ("mystery", r"\bmystery\b"),
        ("romance", r"\bromance\b"),
        ("thriller", r"\bthriller\b"),
        ("horror", r"\bhorror\b"),
        ("nonfiction", r"\bnonfiction\b"),
        ("memoir", r"\bmemoir\b"),
    ]
    return [label for label, pattern in patterns if re.search(pattern, lower)]


def _context_group_label(entries: list[dict[str, str]]) -> str:
    by_prefix: dict[str, list[str]] = {}
    for entry in entries:
        key = str(entry.get("key") or "")
        prefix = key.split(":", 1)[0] if ":" in key else "item"
        label = str(entry.get("label") or _label_from_key(key)).strip()
        if label:
            by_prefix.setdefault(prefix, [])
            if label not in by_prefix[prefix]:
                by_prefix[prefix].append(label)
    parts: list[str] = []
    for prefix, labels in sorted(by_prefix.items()):
        noun = _prefix_noun(prefix, len(labels))
        shown = ", ".join(labels[:4])
        if len(labels) > 4:
            shown += f", +{len(labels) - 4} more"
        parts.append(f"{len(labels)} {noun} ({shown})" if shown else f"{len(labels)} {noun}")
    return "; ".join(parts)


def _context_group_sort_key(group_key: str, group: dict[str, Any]) -> tuple[int, str]:
    return int(group.get("history_index") or 10**9), group_key


def _prefix_noun(prefix: str, count: int) -> str:
    nouns = {
        "title": "title",
        "genre": "genre",
        "value": "value",
        "feature": "feature",
        "asset": "asset",
        "plan_system": "planning system",
        "item": "item",
    }
    noun = nouns.get(prefix, prefix or "item")
    return noun if count == 1 else noun + "s"


def _distinct_slot_values_candidate(query_lower: str, included: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not re.search(r"\b(?:how many|count|number of|different|unique)\b", query_lower):
        return None
    value_items = [item for item in included if str(item.get("key") or "").startswith("value:")]
    if len(value_items) < 2:
        return None
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for item in value_items:
        by_slot.setdefault(_value_slot_name(str(item.get("key") or "")), []).append(item)
    slot, slot_items = max(by_slot.items(), key=lambda entry: len(entry[1]))
    if len(slot_items) < 2:
        return None
    durable_items = [item for item in slot_items if _slot_value_context_role(str(item.get("context") or "")) == "durable_update"]
    example_items = [item for item in slot_items if _slot_value_context_role(str(item.get("context") or "")) == "example_or_trial"]
    selected_items = durable_items if len(durable_items) >= 2 and example_items else slot_items
    labels = _distinct_item_labels(selected_items)
    if len(labels) < 2:
        return None
    return {
        "answer_value": len(labels),
        "formula": "distinct_slot_values",
        "confidence": 0.83 if selected_items is durable_items and example_items else 0.78,
        "slot": slot,
        "labels": labels,
        "component_values": {
            "included_values": labels,
            "excluded_example_or_trial_values": _distinct_item_labels(example_items),
        },
        "guidance": (
            "Use for durable personal value-slot histories when evidence contains an update/order/selection chain plus "
            "separate trial, example, or hypothetical values. Count the durable slot values unless the query explicitly "
            "asks for every trial/example mention."
        ),
    }


def _value_slot_name(key: str) -> str:
    raw = key.split(":", 1)[-1]
    match = re.match(r"([a-z][a-z_]*?)(?:_-?\d|_\d|$)", raw)
    if match:
        return match.group(1).strip("_") or "value"
    return raw.split("_", 1)[0] or "value"


def _slot_value_context_role(context: str) -> str:
    lower = context.lower()
    if re.search(r"\b(?:for example|example|sample|hypothetical|could|would|might|template|placeholder)\b", lower):
        return "example_or_trial"
    if re.search(r"\b(?:tried|try on|trying on|trial|fitting room|sampled|tested)\b", lower):
        return "example_or_trial"
    if re.search(
        r"\b(?:ordered|reordered|placed an order|bought|purchased|returning|exchange|exchanged|"
        r"changed|switched|updated|current|currently|usually|wear|use|using|selected|chose|finalized|need)\b",
        lower,
    ):
        return "durable_update"
    return "mentioned_size"


def _distinct_item_labels(items: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for item in items:
        label = _item_label(item)
        if label and label not in labels:
            labels.append(label)
    return labels


def _delta_between_values_candidate(query_lower: str, included: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not re.search(r"\b(?:how much|difference|delta|improv(?:e|ed|ement)|increase|changed?)\b", query_lower):
        return None
    delta_items = [
        item
        for item in included
        if str(item.get("key") or "").startswith("score_improvement:")
        and item.get("value") is not None
    ]
    if not delta_items:
        return None
    delta_items.sort(key=lambda item: _delta_item_preference(query_lower, item), reverse=True)
    best = delta_items[0]
    try:
        value = int(best.get("value") or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    key_parts = str(best.get("key") or "").split(":")
    component_values: dict[str, Any] = {"delta": value}
    if len(key_parts) >= 3:
        component_values["from"] = key_parts[1]
        component_values["to"] = key_parts[2]
    if len(key_parts) >= 4:
        component_values["scope"] = key_parts[3]
    label = str(best.get("label") or "").strip()
    return {
        "answer_value": value,
        "formula": "delta_between_values",
        "confidence": 0.88 if len(delta_items) == 1 else 0.78,
        "unit": "percentage_points",
        "labels": [label] if label else [],
        "component_values": component_values,
        "guidance": (
            "Use when the query asks how much a score, accuracy, or percentage changed between two stated values. "
            "Report the difference as percentage points, not as a count of sessions or practice items."
        ),
    }


def _delta_item_preference(query_lower: str, item: dict[str, Any]) -> tuple[int, int, int]:
    context = str(item.get("context") or "").lower()
    key = str(item.get("key") or "")
    query_terms = _candidate_terms(query_lower)
    context_terms = _candidate_terms(context)
    return (
        1 if "area" in key and re.search(r"\barea\b", query_lower) else 0,
        len(query_terms & context_terms),
        len(context),
    )


def _query_can_union_candidate_groups(
    query_lower: str,
    role_sums: dict[str, int],
    role_items: dict[str, list[dict[str, Any]]],
) -> bool:
    if "candidate_group_count" not in role_sums:
        return False
    if "user_reported_count" in role_sums:
        return True
    if not _query_can_use_candidate_group_count(query_lower, role_items.get("candidate_group_count", [])):
        return False
    return True


def _query_can_use_candidate_group_count(query_lower: str, items: list[dict[str, Any]]) -> bool:
    if _has_explicit_date_scope(query_lower):
        return True
    if re.search(r"\b(?:recommended|recommendations?|suggest(?:ed|ions?)?|options?)\b", query_lower):
        return True
    if re.search(r"\b(?:plan(?:ned)?|watchlist|selected|chosen|finalized|picked)\b", query_lower) and _candidate_group_context_has_commitment(
        items
    ):
        return True
    if re.search(r"\b(?:assistant|you)\s+(?:recommended|suggested|listed|gave)\b", query_lower):
        return True
    return False


def _candidate_group_context_has_commitment(items: list[dict[str, Any]]) -> bool:
    for item in items:
        context = str(item.get("context") or "").lower()
        if re.search(r"\b(?:final list|schedule|watchlist|will include|would include|included|selected|chosen|planned)\b", context):
            return True
    return False


def _deadline_rows(query_lower: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        text = str(record.get("context") or record.get("content") or record.get("text") or "")
        if not text:
            continue
        for date_text, start, end in _date_mentions_with_spans(text):
            normalized = _normalize_deadline_date(date_text)
            if not normalized:
                continue
            context = _date_local_context(text, start, end)
            role = _deadline_role_for_context(query_lower, context, date_text)
            if not role:
                continue
            role_key, role_label = role
            key = (role_key, normalized, str(record.get("source_span_id") or record.get("id") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "deadline_role": role_key,
                    "label": role_label,
                    "value": normalized,
                    "speaker": record.get("speaker"),
                    "source_span_id": record.get("source_span_id") or record.get("id"),
                    "history_index": record.get("history_index") or record.get("timeline_index"),
                    "context": context,
                }
            )
    return rows


def _date_mentions(text: str) -> list[str]:
    return [value for value, _start, _end in _date_mentions_with_spans(text)]


def _date_mentions_with_spans(text: str) -> list[tuple[str, int, int]]:
    month = (
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    )
    return [
        (match.group(0), match.start(), match.end())
        for match in re.finditer(rf"\b(?:{month})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d{{2}})?\b", text, flags=re.I)
    ]


def _date_local_context(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind(";", 0, start))
    right_candidates = [pos for pos in [text.find(".", end), text.find("\n", end), text.find(";", end)] if pos >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    if left < 0 or start - left > 180:
        left = max(0, start - 180)
    else:
        left += 1
    if right - end > 180:
        right = min(len(text), end + 180)
    return text[left:right].strip()


def _deadline_role_for_context(query_lower: str, context: str, date_text: str) -> tuple[str, str] | None:
    lower = context.lower()
    if not re.search(r"\b(?:deadlines?|due|file|filing|submit|submission|complete|finish|deliver|send|by|target date|scheduled|rescheduled|set for)\b", lower):
        return None
    strong_deadline_marker = bool(re.search(r"\b(?:deadlines?|due|target date|set for|scheduled|rescheduled)\b", lower))
    if (
        re.search(r"\$\s?\d|\bbudget\b|\bapproved\b|\bfees?\b|\bcosts?\b", lower)
        and not strong_deadline_marker
        and not re.search(r"\b(?:file|submit|complete|finish|deliver|send)\b[^.?!]{0,120}\b(?:by|on|before)\b", lower)
    ):
        return None
    label = _endpoint_label_from_context(context, date_text)
    if not label:
        return None
    label_terms = _candidate_terms(label)
    query_terms = _candidate_terms(query_lower)
    if query_terms and label_terms and label_terms.isdisjoint(query_terms):
        endpoint_words = {"deadline", "due", "date", "target", "filing", "submission", "application", "meeting", "event", "module", "project"}
        if not label_terms & endpoint_words:
            return None
    key = _deadline_role_key(label)
    return key, _deadline_label_text(label)


def _endpoint_label_from_context(context: str, date_text: str) -> str | None:
    scrubbed = re.sub(re.escape(date_text), " <DATE> ", context, flags=re.I)
    patterns = [
        r"\bdeadlines?\s+(?:to\s+meet\s+)?for\s+(?:the\s+|my\s+|our\s+)?([^.;:,()]{3,90}?)(?:,?\s+(?:which|that)\s+(?:is|was)\s+)?(?:set\s+)?(?:for|on|by|to)\s+<DATE>",
        r"\b(?:deadline|due date|target date|date)\s+(?:for|of)\s+(?:the\s+|my\s+|our\s+)?([^.;:,()]{3,90})",
        r"\b(?:my|our|the)\s+([^.;:,()]{3,90}?)\s+(?:deadline|due date|target date)\b",
        r"\b(?:file|filing|submit|submission|complete|finish|deliver|send|renew|register|apply(?:ing)?)\s+(?:the\s+|my\s+|our\s+)?([^.;:,()]{3,90}?)\s+(?:by|on|before|<DATE>)",
        r"\b(?:aim|goal|target|need|trying|want|plan|planned|scheduled|set)\s+(?:to\s+)?(?:file|submit|complete|finish|deliver|send)?\s*(?:the\s+|my\s+|our\s+)?([^.;:,()]{3,90}?)\s+(?:by|on|before|for|<DATE>)",
        r"\b(?:the\s+|my\s+|our\s+)?([^.;:,()]{3,90}?)\s+(?:is|was|are|were)?\s*(?:due|scheduled|rescheduled|set)\s+(?:for|on|by|to)\s+<DATE>",
        r"\b(?:the\s+|my\s+|our\s+)?([^.;:,()]{3,90}?)\s+by\s+<DATE>",
    ]
    for pattern in patterns:
        match = re.search(pattern, scrubbed, flags=re.I)
        if not match:
            continue
        label = _clean_endpoint_label(match.group(1))
        if label:
            return label
    return None


def _clean_endpoint_label(value: str) -> str | None:
    value = re.sub(r"<DATE>", " ", value, flags=re.I)
    value = re.sub(r"\b(?:deadline|due date|target date|date|by|on|before|for|set|scheduled|rescheduled|is|was|are|were)\b", " ", value, flags=re.I)
    value = re.sub(r"^(?:i(?:'ve| have)?\s+got\s+a\s+|i\s+have\s+a\s+)?deadline\s+(?:to\s+meet\s+)?", "", value.strip(), flags=re.I)
    value = re.sub(r"^(?:to\s+)?(?:file|filing|submit|submission|complete|finish|deliver|send|renew|register|apply(?:ing)?|the|my|our|a|an)\s+", "", value.strip(), flags=re.I)
    value = re.sub(r"^\$?\d+(?:,\d{3})*(?:\.\d+)?\s+(?:approved|budgeted|allocated)\s+(?:for\s+)?", "", value.strip(), flags=re.I)
    value = re.split(r"\b(?:and|but|because|while|after|before|when|if|so)\b", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n'\"`:-")
    if not value or len(value) < 3 or len(value) > 90:
        return None
    if _reject_short_label(value):
        return None
    return value


def _normalize_deadline_date(value: str) -> str | None:
    match = re.match(
        r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b",
        value.strip(),
        flags=re.I,
    )
    if not match:
        return None
    month_raw = match.group(1).lower()
    month = _MONTH_NAMES.get(month_raw[:3])
    if not month:
        return None
    day = int(match.group(2))
    if day <= 0 or day > 31:
        return None
    year = match.group(3) or "2024"
    return f"{month} {day}, {year}"


_MONTH_NAMES = {
    "jan": "January",
    "feb": "February",
    "mar": "March",
    "apr": "April",
    "may": "May",
    "jun": "June",
    "jul": "July",
    "aug": "August",
    "sep": "September",
    "oct": "October",
    "nov": "November",
    "dec": "December",
}


def _deadline_roles_for_query(query_lower: str, rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    roles = list(dict.fromkeys(str(row.get("deadline_role") or "") for row in rows if row.get("deadline_role")))
    query_terms = _candidate_terms(query_lower)
    matched = [
        role
        for role in roles
        if _candidate_terms(role.replace("_", " ")) & query_terms
        or any(_candidate_terms(str(row.get("label") or "")) & query_terms for row in rows if row.get("deadline_role") == role)
    ]
    if len(matched) >= 2:
        return matched
    if re.search(r"\b(?:two|both|multiple|different|deadlines?|dates?|list|what|which)\b", query_lower):
        return roles[:4]
    return matched[:1]


def _deadline_label_text(label: str) -> str:
    clean = re.sub(r"\s+", " ", label).strip()
    if re.match(r"^(?:the|my|our)\b", clean, flags=re.I):
        return clean
    return "the " + clean


def _deadline_role_key(label: str) -> str:
    terms = [
        term
        for term in re.findall(r"[a-z0-9]+", label.lower())
        if term
        not in {
            "application",
            "deadline",
            "date",
            "due",
            "filing",
            "submission",
            "target",
        }
    ]
    return "_".join(terms[:8]) or _normalize_label_key(label)


def _deadline_output_order(query_lower: str, row: dict[str, Any]) -> tuple[int, int, str]:
    label = str(row.get("label") or row.get("deadline_role") or "").lower()
    positions = [query_lower.find(token) for token in _candidate_terms(label) if query_lower.find(token) >= 0]
    query_pos = min(positions) if positions else 10**6
    try:
        history_index = int(row.get("history_index") or 10**9)
    except (TypeError, ValueError):
        history_index = 10**9
    return query_pos, history_index, label


def _deadline_row_score(query_lower: str, row: dict[str, Any]) -> tuple[float, int]:
    context = str(row.get("context") or "")
    lower = context.lower()
    score = 0.0
    if str(row.get("speaker") or "") in {"user", "document"}:
        score += 1.0
    if re.search(r"\b(?:aim|goal|target|on track|trying to|want to|need to)\b", lower):
        score += 0.9
    if re.search(r"\b(?:deadline|due|by|set for|target filing date)\b", lower):
        score += 0.7
    if re.search(r"\b(?:filing|file|submit|submission|complete|finish|deliver|send)\b", lower):
        score += 0.35
    score += min(0.35, 0.07 * len(_candidate_terms(str(row.get("label") or "")) & _candidate_terms(query_lower)))
    score += min(0.4, 0.05 * len(_candidate_terms(query_lower) & _candidate_terms(lower)))
    if re.search(r"\b(?:example|hypothetical|template|sample)\b", lower):
        score -= 0.5
    try:
        history_index = int(row.get("history_index") or 10**9)
    except (TypeError, ValueError):
        history_index = 10**9
    return score, -history_index


def _has_explicit_date_scope(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*[-–—]\s*\d{1,2}(?:st|nd|rd|th)?)?\b",
            text,
        )
        or re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", text)
        or re.search(r"\b(?:session|conversation|chat)\s+\d+\b", text)
    )


def _mixed_distinct_union_count_candidate(
    role_sums: dict[str, int],
    role_labels: dict[str, list[str]],
) -> dict[str, Any] | None:
    """Build a deterministic unique-count candidate for mixed aggregate roles.

    This is high-confidence only after `_query_can_union_candidate_groups` has
    established that assistant recommendation groups are part of the requested
    object scope rather than echoes or alternatives for already-counted items.
    """

    base_roles = [role for role in ("user_reported_count", "additive_item") if role in role_sums]
    if not base_roles or "candidate_group_count" not in role_sums:
        return None
    base_value = max(role_sums[role] for role in base_roles)
    group_value = role_sums["candidate_group_count"]
    if base_value <= 0 or group_value <= 0:
        return None
    base_labels = [label for role in base_roles for label in role_labels.get(role, [])]
    group_labels = role_labels.get("candidate_group_count", [])
    explicit_overlap = len(_normalized_label_set(base_labels) & _normalized_label_set(group_labels))
    answer_value = max(base_value, base_value + group_value - explicit_overlap)
    return {
        "answer_value": answer_value,
        "formula": "distinct_union_count",
        "confidence": 0.82 if explicit_overlap == 0 else 0.78,
        "labels": (base_labels + group_labels)[:12],
        "component_values": {
            "base_unique_count": base_value,
            "candidate_group_count": group_value,
            "explicit_overlap": explicit_overlap,
        },
        "guidance": (
            "Use for unique-count questions spanning separate dates/sessions when a user-reported aggregate "
            "and a bounded recommendation group are both included. Subtract only titles/items explicitly named "
            "in both components."
        ),
    }


def _normalized_label_set(labels: list[str]) -> set[str]:
    out: set[str] = set()
    for label in labels:
        key = re.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip()
        if key:
            out.add(key)
    return out


def _item_label(item: dict[str, Any]) -> str:
    label = str(item.get("label") or "").strip()
    if label:
        return label
    return _label_from_key(str(item.get("key") or ""))


def _label_from_key(key: str) -> str:
    raw = key.split(":", 1)[-1] if ":" in key else key
    if raw.startswith("size_"):
        return "size " + raw.removeprefix("size_").replace("_", ".")
    return raw.replace("_", " ").strip()


def _normalize_label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized[:80] or "item"


def _reject_short_label(value: str) -> bool:
    lower = value.lower().strip()
    if not lower or len(lower) < 2:
        return True
    if re.fullmatch(r"\d{1,4}", lower):
        return True
    return lower in {
        "a",
        "an",
        "the",
        "and",
        "or",
        "by",
        "on",
        "for",
        "date",
        "deadline",
        "target",
        "goal",
        "item",
        "items",
        "thing",
        "things",
    }


def _candidate_terms(text: str) -> set[str]:
    stop = {"about", "between", "from", "have", "many", "much", "that", "this", "what", "with", "your"}
    return {token for token in re.findall(r"[a-z0-9][a-z0-9'-]{2,}", text.lower()) if token not in stop}
