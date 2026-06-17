from __future__ import annotations

import re


GENERIC_QUERY_RE = re.compile(r"\b(?:how many|total|unique|count|number of|different|list)\b")

_COLLECTOR_WORDS = {
    "area",
    "areas",
    "aspect",
    "aspects",
    "concern",
    "concerns",
    "conversation",
    "conversations",
    "different",
    "feature",
    "features",
    "item",
    "items",
    "mention",
    "mentioned",
    "request",
    "requests",
    "session",
    "sessions",
    "thing",
    "things",
    "topic",
    "topics",
    "unique",
}

_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "another",
    "are",
    "because",
    "been",
    "being",
    "can",
    "could",
    "did",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "help",
    "how",
    "into",
    "like",
    "many",
    "more",
    "need",
    "needed",
    "new",
    "our",
    "over",
    "that",
    "the",
    "these",
    "this",
    "through",
    "throughout",
    "total",
    "using",
    "want",
    "wanted",
    "were",
    "what",
    "when",
    "weekly",
    "with",
    "would",
}


def is_generic_count_or_list_query(query_lower: str) -> bool:
    return bool(GENERIC_QUERY_RE.search(query_lower))


def generic_list_candidate_keys(query_lower: str, text: str) -> list[str]:
    if not is_generic_count_or_list_query(query_lower):
        return []
    lower = text.lower()
    keys: list[str] = []
    prefix = _generic_list_key_prefix(query_lower)
    specialized_prefix = prefix in {"title", "value", "genre"}
    for title in _quoted_title_candidates(lower):
        if _is_non_title_quote(title):
            continue
        keys.append(f"{prefix}:{_normalize_label_key(title)}")
    if re.search(r"\b(?:shoe\s+)?sizes?\b", query_lower):
        for match in re.finditer(r"\b(?:size|sizes?)\s*(\d+(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s*(?:shoe\s*)?size\b", lower):
            value = match.group(1) or match.group(2)
            if value:
                keys.append(f"value:size_{value.replace('.', '_')}")
    if re.search(r"\bgenres?\b", query_lower):
        for match in re.finditer(r"\b(?:genre|genres?)\s*(?:like|including|such as|:)?\s*([a-z][a-z /-]{2,80})", lower):
            label = _clean_generic_list_label(match.group(1))
            if label:
                keys.append(f"genre:{label}")
        for genre in _common_genre_candidates(lower):
            keys.append(f"genre:{genre}")
    if re.search(r"\bseries\b", query_lower):
        for match in re.finditer(r"\b(?:series|trilog(?:y|ies)|saga)\s+(?:called|named)\s+([a-z][a-z0-9 '\"-]{2,80})", lower):
            label = _clean_generic_list_label(match.group(1))
            if label:
                keys.append(f"title:{label}")
    if specialized_prefix:
        return list(dict.fromkeys(keys[:16]))
    for match in re.finditer(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+([^\n]{3,120})", lower):
        label = _clean_generic_list_label(match.group(1))
        if label:
            keys.append(f"{prefix}:{label}")
    for match in re.finditer(
        r"\b(?:selected|chose|decided|planned|finalized|mentioned|listed|included|added|tracked|submitted|ordered)\s+"
        r"(?:my\s+|our\s+|the\s+|a\s+|an\s+)?([^.;!?]{3,100})",
        lower,
    ):
        label = _clean_generic_list_label(match.group(1))
        if label:
            keys.append(f"{prefix}:{label}")
    return list(dict.fromkeys(keys[:16]))


def generic_aggregation_keys(query: str, text: str, *, speaker: str | None = None) -> list[str]:
    """Extract query-scoped, first-person aggregation keys from raw evidence.

    The keys are intentionally generic speech-act objects rather than domain
    labels. This keeps count/list aggregation grounded in what the user said
    they wanted, did, mentioned, selected, or planned.
    """

    if speaker and speaker not in {"user", "document"}:
        return []
    query_lower = query.lower()
    if not is_generic_count_or_list_query(query_lower):
        return []
    if re.search(r"\b(?:movies?|films?|titles?|books?|series|genres?|(?:shoe\s+)?sizes?)\b", query_lower):
        return []
    lower = text.lower()
    if not re.search(r"\b(?:i|my|we|our)\b", lower):
        return []
    if speaker == "assistant":
        return []
    if _looks_like_advice_or_template(lower):
        return []
    action_patterns = [
        r"\b(?:i['’]m|i am|we['’]re|we are)\s+(?:currently\s+)?(?:updating|trying to update|improving|trying to improve)\s+(?:my\s+|our\s+|the\s+)?(?:resume|portfolio)\s+to\s+(?:highlight|include|showcase|emphasize|feature)\s+(?:my\s+|our\s+)?([a-z0-9][a-z0-9 /_.'\"-]{3,120})",
        r"\b(?:i['’]m|i am|we['’]re|we are)\s+(?:currently\s+)?(?:updating|trying to update|improving|trying to improve|working on|focused on)\s+(?:my\s+|our\s+|the\s+)?([a-z0-9][a-z0-9 /_.'\"-]{3,120})",
        r"\b(?:i['’]m|i am|we['’]re|we are)\s+(?:kinda\s+|sorta\s+|really\s+)?(?:worried|concerned|thinking)\s+that\s+(?:my\s+|our\s+|the\s+)?([a-z0-9][a-z0-9 /_.'\"-]{3,120})",
        r"\bimprovement\s+in\s+(?:my\s+|our\s+|the\s+)?([a-z0-9][a-z0-9 /_.'\"-]{3,120})\s+after\s+updating\b",
        r"\b(?:i|we)\s+(?:also\s+|already\s+)?(?:focused on|focus on|worked on|work on|prioritized|prioritize|improved|improve|updated|update|implemented|implementing|added|add|built|created|tested|tried|used|completed|finished|finalized|selected|chose|decided on|mentioned|asked about)\s+(?:to\s+)?(?:my\s+|our\s+|the\s+|a\s+|an\s+)?([a-z0-9][a-z0-9 /_.'\"-]{3,120})",
        r"\b(?:i|we)\s+(?:also\s+)?(?:want|wanted|need|needed|plan|planned|intend|intended|am trying|was trying|trying)\s+(?:to\s+)?(?:handle\s+|use\s+|add\s+|build\s+|create\s+|track\s+|find\s+|compare\s+|support\s+|include\s+|make\s+|improve\s+|improving\s+)?(?:my\s+|our\s+|the\s+|a\s+|an\s+)?([a-z0-9][a-z0-9 /_.'\"-]{3,120})",
        r"\b(?:my|our)\s+([a-z0-9][a-z0-9 /_.'\"-]{3,80})\s+(?:includes?|included|has|had)\s+([a-z0-9][a-z0-9 /_.'\"-]{3,100})",
        r"\bfor\s+(?:my|our)\s+[a-z0-9][a-z0-9 /_.'\"-]{3,80},\s*(?:i\s+|we\s+)?(?:want\s+to\s+|need\s+to\s+|planned\s+to\s+|plan\s+to\s+)?([a-z0-9][a-z0-9 /_.'\"-]{3,120})",
    ]
    query_focus = _query_focus_tokens(query_lower)
    prefix = _generic_key_prefix(query_lower)
    keys: list[str] = []
    for pattern in action_patterns:
        for match in re.finditer(pattern, lower):
            parts = [part for part in match.groups() if part]
            phrase = " ".join(parts)
            key = _clean_generic_aggregation_key(phrase, query_focus=query_focus)
            if key:
                keys.append(f"{prefix}:{key}")
    return list(dict.fromkeys(keys[:10]))


def is_vendor_tool_aggregation_query(query_lower: str) -> bool:
    if not is_generic_count_or_list_query(query_lower):
        return False
    object_term = bool(
        re.search(r"\b(?:vendors?|tools?|platforms?|software|apps?|applications?|services?|systems?)\b", query_lower)
        or re.search(r"供应商|工具|平台|软件|应用|系统", query_lower)
    )
    action_term = bool(
        re.search(
            r"\b(?:mention(?:ed)?|using|used|use|customiz(?:e|ed|ing)|select(?:ed)?|adopt(?:ed|ing)?|"
            r"pilot(?:ed|ing)?|implement(?:ed|ing)?|integrat(?:e|ed|ing)|automati(?:on|ng)|currently)\b",
            query_lower,
        )
        or re.search(r"提到|使用|采用|定制|集成|平台|工具|供应商", query_lower)
    )
    planning_context = bool(re.search(r"\b(?:reminders?|calendars?|planners?|to-?dos?|task\s+managers?)\b", query_lower))
    return bool(object_term and action_term and not planning_context)


def vendor_tool_aggregation_keys(query_lower: str, text: str, *, speaker: str | None = None) -> list[str]:
    if speaker and speaker not in {"user", "document", "assistant"}:
        return []
    if not is_vendor_tool_aggregation_query(query_lower):
        return []
    keys: list[str] = []
    for sentence in _vendor_tool_candidate_sentences(query_lower, text, speaker=speaker):
        keys.extend(_vendor_tool_keys_from_sentence(sentence))
    return list(dict.fromkeys(keys[:12]))


def _vendor_tool_candidate_sentences(query_lower: str, text: str, *, speaker: str | None = None) -> list[str]:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
    if not sentences:
        sentences = [text.strip()]
    out: list[str] = []
    query_terms = _query_focus_tokens(query_lower)
    for sentence in sentences:
        lower = sentence.lower()
        if _vendor_tool_sentence_is_hypothetical(lower):
            continue
        has_tool_context = bool(
            re.search(r"\b(?:vendors?|tools?|platforms?|software|apps?|applications?|services?|systems?|algorithm)\b", lower)
            or re.search(r"\b(?:screening|hiring|automation|automate|candidate|recruiting|recruiter)\b", lower)
            or re.search(r"供应商|工具|平台|软件|应用|系统", lower)
        )
        has_action_context = bool(
            re.search(
                r"\b(?:use|using|used|customiz(?:e|ed|ing)|select(?:ed)?|adopt(?:ed|ing)?|pilot(?:ed|ing)?|"
                r"implement(?:ed|ing)?|integrat(?:e|ed|ing)|currently|already|seen|list(?:ed)?|mention(?:ed)?)\b",
                lower,
            )
        )
        if speaker in {"user", "document"}:
            ownership = bool(re.search(r"\b(?:i|my|we|our)\b", lower))
        elif speaker == "assistant":
            ownership = bool(re.search(r"\b(?:you|your|you've|you have|currently using|already seen)\b", lower))
        else:
            ownership = True
        text_terms = _query_focus_tokens(lower)
        topical = bool(not query_terms or (query_terms & text_terms) or re.search(r"\b(?:ai|hiring|automation|screening)\b", lower))
        if ownership and topical and (has_tool_context or has_action_context):
            out.append(sentence)
    return out


def _vendor_tool_sentence_is_hypothetical(lower_sentence: str) -> bool:
    return bool(
        re.search(r"\b(?:research|compare|evaluate|consider|look for|reputable|options?|examples?)\b.{0,80}\b(?:vendors?|tools?|platforms?|software|apps?)\b", lower_sentence)
        or re.search(r"\b(?:vendors?|tools?|platforms?|software|apps?)\b.{0,80}\b(?:for example|e\.g\.|etc\.)\b", lower_sentence)
        or re.search(r"\b(?:could|might|may)\s+(?:use|choose|select|adopt|include)\b", lower_sentence)
    )


def _vendor_tool_keys_from_sentence(sentence: str) -> list[str]:
    keys: list[str] = []
    patterns = [
        r"\bvendor\s+([A-Z][A-Za-z0-9+&.-]*(?:\s+[A-Z][A-Za-z0-9+&.-]*){0,3})\b",
        r"\b(?:tools?|vendors?|platforms?|software|apps?|applications?|services?|systems?)\s*(?:like|such as|including|include|:)\s*([^.;!?()\n]{2,140})",
        r"\b(?:using|used|customizing|customized|selecting|selected|adopting|adopted|piloting|piloted|implementing|implemented|integrating|integrated)\s+([A-Z][A-Za-z0-9+&.-]*(?:\s+[A-Z][A-Za-z0-9+&.-]*){0,3})\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, sentence):
            keys.extend(_vendor_tool_keys_from_candidate_text(match.group(1), sentence))
    return list(dict.fromkeys(keys))


def _vendor_tool_keys_from_candidate_text(value: str, context: str) -> list[str]:
    parts = re.split(r"\s*(?:,|/|\band\b|\bor\b|&)\s*", value)
    keys: list[str] = []
    for part in parts:
        label = _clean_vendor_tool_label(part)
        if not label:
            continue
        if _reject_vendor_tool_label(label, context):
            continue
        keys.append(f"vendor_tool:{_normalize_label_key(label)}")
    return keys


def _clean_vendor_tool_label(value: str) -> str:
    value = re.sub(r"->->.*$", "", value)
    value = re.sub(r"\([^)]{0,80}\)", " ", value)
    value = value.strip(" \t\r\n'\"`:-")
    value = re.split(r"\b(?:to|for|so|because|while|after|before|when|that|which|who|as)\b|[,.;!?]", value, maxsplit=1)[0]
    value = re.sub(r"^(?:the|a|an|our|my|your|their)\s+", "", value.strip(), flags=re.I)
    return value.strip(" \t\r\n'\"`:-")


def _reject_vendor_tool_label(label: str, context: str) -> bool:
    lower = label.lower().strip()
    if not lower or len(lower) < 3 or len(lower) > 70:
        return True
    if lower in _BAD_VENDOR_TOOL_LABELS:
        return True
    if re.fullmatch(r"(?:q[1-4]|hr|ai|api|gdpr|roi|mvp|sql|ui|ux)", lower):
        return True
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+&.-]*", label)
    if not tokens:
        return True
    if all(token.lower() in _BAD_VENDOR_TOOL_LABELS for token in tokens):
        return True
    if not any(token[:1].isupper() or re.search(r"[A-Z]", token[1:]) or token.isupper() for token in tokens):
        return True
    context_lower = context.lower()
    label_pattern = re.escape(label.lower()).replace(r"\ ", r"\s+")
    if re.search(rf"\b(?:meeting|discussion|call|talk|involving|promoting|including)\s+with\s+{label_pattern}\b", context_lower):
        return True
    return False


_BAD_VENDOR_TOOL_LABELS = {
    "ai",
    "ai tool",
    "ai tools",
    "algorithm",
    "automation",
    "hiring",
    "hiring process",
    "tool",
    "tools",
    "vendor",
    "vendors",
    "software",
    "platform",
    "platforms",
    "application",
    "applications",
    "service",
    "services",
    "system",
    "systems",
    "current",
    "manual",
    "screening",
    "recruiting",
    "candidate",
    "candidates",
    "staff",
    "team",
    "training",
    "data",
    "privacy",
    "bias",
    "fairness",
    "budget",
    "cost",
    "costs",
}


def combinatorics_aggregation_keys(text: str) -> list[str]:
    lower = text.lower()
    keys: list[str] = []
    if re.search(r"\b(?:arrang(?:e|ing)|permutations?|n!|\d+!)\b.{0,140}\b(?:balls?|objects?|items?)\b", lower) or re.search(
        r"\b(?:balls?|objects?|items?)\b.{0,140}\b(?:arrang(?:e|ing)|permutations?|n!|\d+!)\b",
        lower,
    ):
        keys.append("ways:arrange_objects")
    if re.search(r"\b(?:choos(?:e|ing)|combinations?|\d+\s*c\s*\d+|\d+c\d+)\b.{0,160}\bballs?\b", lower) or re.search(
        r"\bballs?\b.{0,160}\b(?:choos(?:e|ing)|combinations?|\d+\s*c\s*\d+|\d+c\d+)\b",
        lower,
    ):
        keys.append("ways:choose_balls")
    if re.search(r"\b(?:choos(?:e|ing)|draw(?:ing)?|combinations?|\d+\s*c\s*\d+|\d+c\d+)\b.{0,180}\b(?:cards?|deck)\b", lower) or re.search(
        r"\b(?:cards?|deck)\b.{0,180}\b(?:choos(?:e|ing)|draw(?:ing)?|combinations?|\d+\s*c\s*\d+|\d+c\d+)\b",
        lower,
    ):
        keys.append("ways:choose_cards")
    for match in re.finditer(r"\b(?:probability calculation|calculation|problem)\b.{0,120}\b(?:coin|coins|dice|die|card|cards|deck)\b", lower):
        keys.append("calculation:" + re.sub(r"\W+", "_", match.group(0))[:48].strip("_"))
    return list(dict.fromkeys(keys))


def stress_break_aggregation_keys(text: str) -> list[str]:
    """Extract durable break/rest objects for stress or burnout aggregation.

    These keys are retrieval hints only. The answer-pack layer still decides
    inclusion and value semantics, but retrieval needs stable keys so relevant
    break/rest spans survive coverage selection.
    """

    lower = text.lower()
    keys: list[str] = []
    if re.search(r"\b(?:1|one)\s*-?\s*hour\b", lower) and "break" in lower:
        if re.search(r"\b(?:stress|stressed|burnout|focus|yoga|meditation|mindfulness)\b", lower):
            keys.append("break:one_hour_stress_day")
        else:
            keys.append("excluded:generic_reset_break")
    if re.search(r"\b(?:two|2)\s+full\s+days?\s+off\b", lower):
        keys.append("break:full_days_off")
    if re.search(r"\b(?:2|two)\s*-?\s*hours?\s+break\b|\b(?:2|two)\s*-?\s*hour\s+break\b", lower):
        if re.search(r"\b(?:stress|stressed|burnout|focus|yoga|meditation|mindfulness)\b", lower):
            keys.append("break:two_hour_stress_break")
        else:
            keys.append("excluded:generic_reset_break")
    if re.search(r"\b(?:yoga|meditation|mindfulness|walk(?:ing)?|nap)\s+break\b", lower) and re.search(
        r"\b(?:stress|stressed|burnout|reset|rest|focus)\b",
        lower,
    ):
        keys.append("break:restorative_break")
    return list(dict.fromkeys(keys))


def is_combinatorics_aggregation_query(query_lower: str) -> bool:
    if not re.search(r"\b(?:how many|total|count|number|different|across)\b", query_lower):
        return False
    return bool(
        re.search(
            r"\b(?:ways?|arrang(?:e|ing|ements?)|choos(?:e|ing)|combinations?|permutations?|balls?|cards?|deck|dice|coins?|probability calculations?|calculations?)\b",
            query_lower,
        )
    )


def is_stress_break_aggregation_query(query_lower: str) -> bool:
    if not re.search(r"\b(?:how many|total|count|number|across)\b", query_lower):
        return False
    return bool(re.search(r"\b(?:days?|take off|took off|breaks?|stress|burnout|rest)\b", query_lower))


def aggregation_keys_for_query(query: str, text: str, *, speaker: str | None = None) -> list[str]:
    query_lower = query.lower()
    lower = text.lower()
    keys: list[str] = []
    if is_combinatorics_aggregation_query(query_lower):
        keys.extend(combinatorics_aggregation_keys(lower))
    if is_stress_break_aggregation_query(query_lower):
        keys.extend(stress_break_aggregation_keys(lower))
    if is_generic_count_or_list_query(query_lower):
        keys.extend(generic_list_candidate_keys(query_lower, lower))
        keys.extend(vendor_tool_aggregation_keys(query_lower, text, speaker=speaker))
    keys.extend(generic_aggregation_keys(query, lower, speaker=speaker))
    return list(dict.fromkeys(keys))


def _generic_key_prefix(query_lower: str) -> str:
    if re.search(r"\b(?:movies?|films?|titles?|books?|series)\b", query_lower):
        return "title"
    if re.search(r"\bgenres?\b", query_lower):
        return "genre"
    if re.search(r"\b(?:shoe\s+)?sizes?\b", query_lower):
        return "value"
    if re.search(r"\b(?:features?|concerns?|capabilities|requirements?)\b", query_lower):
        return "feature"
    if re.search(r"\b(?:areas?|aspects?|topics?)\b", query_lower):
        return "area"
    if re.search(r"\b(?:requests?|questions?)\b", query_lower):
        return "request"
    if re.search(r"\b(?:items?|things?)\b", query_lower):
        return "item"
    return "generic"


def _generic_list_key_prefix(query_lower: str) -> str:
    if re.search(r"\b(?:titles?|names?|series|movies?|films?|books?)\b", query_lower):
        return "title"
    if re.search(r"\bgenres?\b", query_lower):
        return "genre"
    if re.search(r"\b(?:sizes?|amounts?|values?|numbers?)\b", query_lower):
        return "value"
    if re.search(r"\b(?:assets?|items?|things?|objects?)\b", query_lower):
        return "item"
    if re.search(r"\b(?:features?|concerns?|capabilities|requirements?)\b", query_lower):
        return "feature"
    return "item"


def _quoted_title_candidates(text: str) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(r'"([^"\n]{2,80})"', text) if match.group(1).strip()]


def _clean_generic_list_label(value: str) -> str:
    value = re.sub(r"->->.*$", "", value)
    value = re.sub(r"\([^)]{0,80}\)", " ", value)
    value = re.split(r"\b(?:because|while|after|before|but|so that|considering|compared to|instead of)\b|[,.;!?]", value, maxsplit=1)[0]
    terms = [
        term
        for term in re.findall(r"[a-z0-9]+", value.lower())
        if len(term) >= 2 and term not in _STOPWORDS
    ]
    return "_".join(terms[:8])


def _normalize_label_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized[:60] or "untitled"


def _is_non_title_quote(value: str) -> bool:
    lower = value.lower().strip()
    return bool(re.fullmatch(r"\d{4}", lower)) or lower in {"pg", "pg-13", "r", "netflix", "disney+", "libby", "audible"}


def _common_genre_candidates(text: str) -> list[str]:
    out: list[str] = []
    genre_phrases = [
        "historical fiction",
        "science fiction",
        "sci fi",
        "sci-fi",
        "space opera",
        "urban fantasy",
        "epic fantasy",
        "dark fantasy",
        "fantasy",
        "mystery",
        "romance",
        "thriller",
        "horror",
        "nonfiction",
        "memoir",
    ]
    for phrase in genre_phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            out.append(_normalize_label_key(phrase))
    return list(dict.fromkeys(out))


def _query_focus_tokens(query_lower: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", query_lower)
        if len(token) >= 3 and token not in _STOPWORDS and token not in _COLLECTOR_WORDS
    }


def _clean_generic_aggregation_key(value: str, *, query_focus: set[str]) -> str:
    value = re.sub(r"->->.*$", "", value)
    value = re.sub(r"\([^)]{0,80}\)", " ", value)
    value = value.strip(" '\"`:-")
    value = re.split(
        r"\b(?:across|throughout|because|while|after|before|and then|and\s+(?:i|we|my|our)|but|so that|so i|so we|considering|compared to|instead of)\b|[,.;!?]",
        value,
        maxsplit=1,
    )[0]
    value = re.sub(r"\b(?:for|to support|using|with)\b.{0,80}$", "", value).strip()
    value = re.sub(r"^(?:my|our|the|a|an|some|new)\s+", "", value)
    value = re.sub(r"^(?:on|at|by)\s+", "", value)
    terms = [
        term
        for term in re.findall(r"[a-z0-9]+", value)
        if len(term) >= 3 and term not in _STOPWORDS
    ]
    terms = _trim_leading_action_terms(terms)
    if not terms:
        return ""
    if len(terms) == 1 and terms[0] in _BAD_GENERIC_SINGLETONS:
        return ""
    if query_focus and not (set(terms) & query_focus) and len(terms) > 8:
        terms = terms[:8]
    return "_".join(terms[:7])


def _looks_like_advice_or_template(lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:here are some steps|here are some tips|here's how|here is how|for example|you can|you could|consider|suggest|recommend|focus on|make sure|ensure|prioritize|keep an eye on|to help you|to stay on track|here are some points)\b",
            lower,
        )
    )


def _trim_leading_action_terms(terms: list[str]) -> list[str]:
    while terms and terms[0] in {
        "adapting",
        "handle",
        "include",
        "make",
        "improve",
        "improving",
        "support",
        "track",
        "understand",
    }:
        terms = terms[1:]
    return terms


_BAD_GENERIC_SINGLETONS = {
    "invest",
    "know",
    "may",
    "january",
    "february",
    "march",
    "april",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
