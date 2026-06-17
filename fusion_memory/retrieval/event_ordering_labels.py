from __future__ import annotations

import re

from fusion_memory.core.text import compact_summary

_EVENT_ORDERING_MILESTONE_LABELS = {
    "initial_project_setup": "initial project setup",
    "core_functionality": "core functionality and MVP scope",
    "transaction_crud_implementation": "transaction CRUD implementation",
    "transaction_error_handling": "transaction validation and error handling",
    "deployment_configuration": "deployment configuration",
    "integration_test_coverage": "integration test coverage",
    "deployment_and_test_improvements": "deployment and test improvements",
    "setup_debugging": "setup debugging",
    "security_auth": "security and authentication",
    "security_and_deployment": "security and deployment",
}

_EVENT_ORDERING_LIFECYCLE_MILESTONE_ORDER = [
    "initial_project_setup",
    "transaction_crud_implementation",
    "deployment_configuration",
    "integration_test_coverage",
    "deployment_and_test_improvements",
    "core_functionality",
    "transaction_error_handling",
    "setup_debugging",
    "security_auth",
    "security_and_deployment",
]

def _event_ordering_cluster_fallback_label(labels: list[str], snippets: list[str]) -> str:
    cleaned_labels = [
        _event_ordering_clean_label(label)
        for label in labels
        if label and not _event_ordering_low_information_text(label) and not _event_ordering_shell_like_label(label)
    ]
    cleaned_labels = [label for label in cleaned_labels if not _event_ordering_bad_extracted_label(label)]
    if cleaned_labels:
        return max(cleaned_labels, key=lambda value: (_event_ordering_specificity(value), -len(value)))
    snippet_text = " ".join(snippets)
    terms = [
        term
        for term in _event_ordering_terms_ordered(snippet_text)
        if term not in _EVENT_ORDERING_SEQUENCE_STOPWORDS
        and term not in _EVENT_ORDERING_TOPIC_WORDS
        and term not in {"current", "help", "trying"}
    ]
    return _title_from_terms(terms[:4]) if terms else ""

def _event_ordering_cluster_label(labels: list[str], snippets: list[str]) -> str:
    candidates = [
        _event_ordering_clean_label(label)
        for label in labels
        if label
        and not _event_ordering_shell_like_label(label)
        and not _event_ordering_low_information_text(label)
        and not _event_ordering_low_information_theme_label(label)
    ]
    if candidates:
        first_representative = _event_ordering_cluster_representative_label(labels, snippets)
        if first_representative:
            return first_representative
        if len(candidates) == 1:
            return candidates[0]
        short_candidates = [
            candidate
            for candidate in candidates
            if 2 <= len(_event_ordering_terms(candidate) - _EVENT_ORDERING_SEQUENCE_STOPWORDS) <= 6
            and not _event_ordering_bad_extracted_label(candidate)
        ]
        if short_candidates:
            first_clean = _event_ordering_clean_label(labels[0]) if labels else ""
            if first_clean and first_clean in short_candidates and not _event_ordering_bad_extracted_label(first_clean):
                return first_clean
            earliest = next(
                (
                    candidate
                    for candidate in short_candidates
                    if candidate == min(short_candidates, key=lambda value: (len(value.split()), len(value), value.lower()))
                ),
                "",
            )
            if earliest:
                return earliest
        merged = _merge_cluster_candidate_labels(short_candidates or candidates)
        if merged:
            return merged
        shared = _shared_event_ordering_terms(candidates)
        if len(shared) >= 2:
            return _title_from_terms(shared)
        best = min(candidates, key=lambda value: (len(value.split()), len(value)))
        if not _event_ordering_bad_extracted_label(best):
            return best
    snippet_text = " ".join(snippets)
    terms = [
        term
        for term in _event_ordering_terms_ordered(snippet_text)
        if term not in _EVENT_ORDERING_SEQUENCE_STOPWORDS
        and term not in _EVENT_ORDERING_TOPIC_WORDS
        and term not in {"current", "help", "trying"}
    ]
    if terms:
        title = _title_from_terms(terms[:4])
        trailing = _event_ordering_trailing_short_term(snippet_text)
        if trailing and trailing.lower() not in _event_ordering_terms(title):
            title = f"{title} {trailing}".strip()
        return title
    return ""

def _event_ordering_cluster_representative_label(labels: list[str], snippets: list[str]) -> str:
    if not labels and not snippets:
        return ""
    label = labels[0] if labels else ""
    text = snippets[0] if snippets else label
    representative = _event_ordering_sequence_label({"label": label, "text": text, "conversation_content": text})
    representative = _event_ordering_clean_label(representative)
    if (
        representative
        and not _event_ordering_shell_like_label(representative)
        and not _event_ordering_low_information_text(representative)
        and not _event_ordering_low_information_theme_label(representative)
        and len(_event_ordering_terms(representative) - _EVENT_ORDERING_SEQUENCE_STOPWORDS) >= 2
    ):
        return representative
    fallback = _event_ordering_action_phrase_label(text)
    if fallback:
        return fallback
    return ""

def _event_ordering_action_phrase_label(text: str) -> str:
    for pattern in [
        r"\b((?:remove|removing|consolidate|consolidating|fix|fixing|configure|configuring|deploy|deploying|test|testing|implement|implementing|optimi[sz]e|optimi[sz]ing|protect|protecting|handle|handling|track|tracking|set up|setting up|upgrade|upgrading|link|linking)\s+[^.;!?]{8,120})",
    ]:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        label = _event_ordering_clean_label(match.group(1))
        if (
            label
            and not _event_ordering_low_information_text(label)
            and len(_event_ordering_terms(label) - _EVENT_ORDERING_SEQUENCE_STOPWORDS) >= 2
        ):
            return label
    return ""

def _merge_cluster_candidate_labels(candidates: list[str]) -> str:
    if not candidates:
        return ""
    ranked: list[str] = []
    seen: set[str] = set()
    for candidate in sorted(
        candidates,
        key=lambda value: (
            _event_ordering_specificity(value),
            -len(value),
            0 if not _event_ordering_shell_like_label(value) else -1,
            value.lower(),
        ),
        reverse=True,
    ):
        cleaned = _short_event_ordering_theme(candidate)
        if not cleaned:
            continue
        key = _event_ordering_label_key(cleaned)
        if not key or key in seen or _event_ordering_label_overlaps_seen(key, seen):
            continue
        ranked.append(cleaned)
        seen.add(key)
        if len(ranked) >= 3:
            break
    if not ranked:
        return ""
    if len(ranked) == 1:
        return ranked[0]
    if len(ranked) == 2 and _event_ordering_specificity(ranked[0]) >= _event_ordering_specificity(ranked[1]) + 1.0:
        return ranked[0]
    return " / ".join(ranked)

def _short_event_ordering_theme(label: str) -> str:
    text = _event_ordering_clean_label(label)
    text = re.sub(r"\b(?:can|could)\s+you\b.*$", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"\b(?:by|using|considering)\b.{35,}$", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"\b(?:from|to)\s+v?\d+(?:\.\d+){1,3}\b.*$", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"\b(?:which|that|who|where)\s*$", "", text, flags=re.I).strip(" .,:;-")
    words = text.split()
    if len(words) > 10:
        text = " ".join(words[:10]).strip(" .,:;-")
        text = re.sub(r"\b(?:which|that|who|where|with|using|by|to|for|and)\s*$", "", text, flags=re.I).strip(" .,:;-")
    if _event_ordering_low_information_theme_label(text):
        return ""
    return text

def _event_ordering_compact_aspect_label(label: str, context: str = "") -> str:
    text = _event_ordering_clean_label(label)
    text = re.split(r"\s*;\s*", text, maxsplit=1)[0].strip(" .,:;-")
    text = _event_ordering_strip_request_shell(text)
    context_topic = _event_ordering_context_topic_label(text, context)
    if context_topic and (
        _event_ordering_low_information_theme_label(text)
        or _event_ordering_shell_like_label(text)
        or _event_ordering_under_specified_topic_label(text)
        or (
            _event_ordering_terms(text) & {"error", "404", "not", "found"}
            and len(_event_ordering_terms(context_topic) - _event_ordering_terms(text)) >= 1
        )
    ):
        return _event_ordering_preserve_acronyms(context_topic)
    aspect_hint = _event_ordering_aspect_hint_label(text, context)
    if aspect_hint and (
        not text
        or len(text.split()) > 9
        or re.search(r"\b(?:i|we|my|our)\b", text, flags=re.I)
        or _event_ordering_shell_like_label(text)
        or _event_ordering_under_specified_topic_label(text)
    ):
        return _event_ordering_preserve_acronyms(aspect_hint)
    if _event_ordering_low_information_theme_label(text) and context:
        text = _event_ordering_strip_request_shell(_event_ordering_clean_label(context))
    text = _event_ordering_trim_action_tail(text)
    concern = _event_ordering_concern_label(text)
    if concern:
        return _event_ordering_preserve_acronyms(concern)
    text = _event_ordering_nominal_event_label(text, context=context)
    trailing = _event_ordering_trailing_short_term(label) or _event_ordering_trailing_short_term(context)
    text = _short_event_ordering_theme(text)
    if trailing and trailing.lower() not in _event_ordering_terms(text):
        text = f"{text} {trailing}".strip()
    if text and not _event_ordering_low_information_theme_label(text):
        return _event_ordering_preserve_acronyms(text)
    fallback = _short_event_ordering_theme(label)
    return _event_ordering_preserve_acronyms(fallback) if fallback else ""

def _event_ordering_context_topic_label(label: str, context: str) -> str:
    source = re.sub(r"\s+", " ", context or label).strip(" .,:;-")
    if not source:
        return ""
    patterns = [
        (
            r"\b(?:i|we)\s+started\s+using\s+(?:the\s+)?([^,.;!?]{3,80}?)\s+(?:app|tool|service|platform)?\b.{0,100}?\b(?:synced|connected|linked)\s+it\s+with\s+(?:my|our|the)?\s*([^,.;!?]{3,80})",
            "{0} {1} sync",
        ),
        (
            r"\b(?:i|we)(?:'m| am|'re| are)\s+(?:kinda\s+|sorta\s+|really\s+)?curious,?\s+how\s+can\s+(?:i|we)\s+highlight\s+([^,.;!?]{4,100}?)\s+in\s+(?:a|an|the|my|our)\s+([^,.;!?]{4,80})",
            "{1} {0} highlight",
        ),
        (
            r"\bfigure\s+out\s+how\s+to\s+craft\s+(?:a|an|the)?\s*standout\s+([^,.;!?]{4,100})",
            "{0} crafting",
        ),
        (
            r"\b(?:i|we)(?:'m| am|'re| are)\s+(?:kinda\s+|sorta\s+|really\s+)?worried\s+about\s+(?:my|our)\s+([^,.;!?]{3,80}).{0,120}?\bproblem\s+in\s+(?:the\s+)?([^,.;!?]{4,100})",
            "{0} {1} concern",
        ),
        (
            r"\bthinking\s+of\s+reaching\s+out\s+to\s+(?:my|our)?\s*(?:close\s+friend\s+)?([A-Z][A-Za-z' -]{2,50}).{0,140}?\bfor\s+(?:some\s+)?advice\s+on\s+([^,.;!?]{4,100})",
            "{0} {1} advice",
        ),
        (
            r"\bworried\s+about\s+(?:my|our)\s+([^,.;!?]{3,80}).{0,120}?\b(?:told|asked|reminded)\s+(?:me|us)\s+to\s+update\s+it\b",
            "{0} update",
        ),
        (
            r"\b(?:i|we)(?:'m| am|'re| are)\s+considering\s+([^,.;!?]{6,120})",
            "{0}",
        ),
        (
            r"\b(?:i|we)(?:'ve| have)?\s*(?:researched|found|learned)\s+that\s+([^,.;!?]{4,140}).{0,120}?\bimproves?\s+([^,.;!?]{4,80})",
            "{0} {1}",
        ),
        (
            r"\b(?:i|we)(?:'ve| have)\s+researched\s+that\s+([^,.;!?]{4,120}?)\s+(?:is|are|has|have)\s+([^,.;!?]{4,120}?)\b.{0,120}?\bunderstand\s+how\s+this\s+feature\s+affects\s+([^,.;!?]{4,100})",
            "{0} {2}",
        ),
        (
            r"\b(?:i|we)(?:'ve| have)?\s*(?:researched|found|learned)\s+that\s+([^,.;!?]{4,140}).{0,180}?\baffects\s+([^,.;!?]{4,100})",
            "{0} {1}",
        ),
        (
            r"\b(?:best\s+way\s+to\s+)?protect\s+(?:my|our|the|a|an)?\s*([^,.;!?]{4,100}?)\s+from\s+([^,.;!?]{3,80})",
            "{0} {1} protection",
        ),
        (
            r"\b(?:does|should)\s+(?:the\s+)?([^,.;!?]{4,100}?)\s+need\s+to\s+(?:be\s+)?reapplied\b",
            "{0} reapplication",
        ),
        (
            r"\btrouble\s+with\s+(?:a|an|the)?\s*\"?([^\",.;!?]{3,80}?)\"?\s+error\s+on\s+(?:my|our|the)?\s*([^,.;!?]{3,100})",
            "{1} {0} fix",
        ),
        (
            r"\b(?:getting|seeing)\s+(?:a|an|the)?\s*\"?([^\",.;!?]{3,80}?)\"?\s+error\b.{0,100}?\b(?:in|on|from)\s+(?:my|our|the)?\s*([^,.;!?]{3,100})",
            "{1} {0} fix",
        ),
        (
            r"\b(?:i|we)(?:'ve| have)\s+been\s+tracking\s+([^,.;!?]{4,80}).{0,120}?\bbecause\s+of\s+([^,.;!?]{4,80})",
            "{0} and {1}",
        ),
        (
            r"\b(?:i|we)(?:'m| am|'re| are)\s+(?:kinda\s+|sorta\s+|really\s+)?stressed\s+about\s+([^,.;!?]{6,120})",
            "{0} stress",
        ),
        (
            r"\b(?:i|we)(?:'ve| have)\s+been\s+using\s+([A-Za-z][A-Za-z0-9.+_-]{2,40})(?:\s+for\s+[^,.;!?]{3,100})?.{0,120}?\bfees?\s+(?:are|is|average|averaging)\s+([^,.;!?]{1,40})",
            "{0} fees",
        ),
        (
            r"\bprefer\s+using\s+([^,.;!?]{3,80})\s+for\s+([^,.;!?]{3,80})",
            "{0} {1}",
        ),
    ]
    for pattern, template in patterns:
        match = re.search(pattern, source, flags=re.I)
        if not match:
            continue
        parts = [_event_ordering_short_topic_phrase(part) for part in match.groups()]
        parts = [part for part in parts if part]
        if not parts:
            continue
        label_text = template.format(*parts).strip(" .,:;-")
        label_text = _event_ordering_nominal_event_label(label_text)
        label_text = _short_event_ordering_theme(label_text)
        if label_text and not _event_ordering_low_information_theme_label(label_text):
            return label_text
    return ""

def _event_ordering_aspect_hint_label(label: str, context: str = "") -> str:
    source = re.sub(r"\s+", " ", " ".join([label or "", context or ""])).strip(" .,:;-")
    if not source:
        return ""
    lower = source.lower()

    patterns: list[tuple[str, str]] = [
        (
            r"\b(?:ai|algorithm|automation)\b.{0,120}\b(?:recogni[sz]e|detect|evaluate|assess)\b.{0,80}\bsoft skills?\b",
            "AI soft skills recognition",
        ),
        (
            r"\b(?:i|we)\s+(?:collaborated|worked)\s+with\s+[A-Z][A-Za-z']{2,}(?:\s+[A-Z][A-Za-z']{2,}){0,2}\s+on\s+(?:(?:a|an|the|my|our)\s+)?([^,.;!?]{4,90})",
            "{0} collaboration",
        ),
        (
            r"\b(?:i|we)\s+used\s+([A-Z][A-Za-z0-9.+#&-]{2,40})\s+to\s+([^,.;!?]{4,90})",
            "{0} {1}",
        ),
        (
            r"\b([A-Z][A-Za-z']{2,}(?:\s+[A-Z][A-Za-z']{2,}){0,2})(?:,\s*\d+)?\s+(?:suggested|recommended|advised|told|reminded)\s+(?:me|us)?\s*(?:to\s+)?([^,.;!?]{4,100})",
            "{0} advice: {1}",
        ),
        (
            r"\b(?:specific|which|what)\s+([^,.;!?]{4,80}?\btools?)\b.{0,100}\b(fairness|transparency|bias|explainability)\b",
            "{0} {1}",
        ),
        (
            r"\b(?:AI|algorithm|automation)\b.{0,120}\b(?:recogni[sz]e|detect|evaluate|assess)\s+([^,.;!?]{4,80}?\bsoft skills?)\b",
            "AI {0} recognition",
        ),
        (
            r"\b(?:pilot|trial)\s+program\b.{0,120}\b(?:resume\s+screening|screening|efficiency|diversity|candidate\s+pool|bias)\b",
            "pilot program and screening impact",
        ),
        (
            r"\bthinking\s+of\s+(?:making\s+some\s+)?(?:big\s+)?changes?,?\s+(?:like\s+)?((?:automating|using|adopting|introducing)\s+[^,.;!?]{4,90})",
            "{0}",
        ),
        (
            r"\b(?:automating|using|adopting|introducing)\s+([^,.;!?]{4,80}?\bhiring\s+process)\b",
            "{0}",
        ),
        (
            r"\bimpact\s+(?:my|our|the)\s+role\s+and\s+(?:the\s+)?(?:overall\s+)?([^,.;!?]{4,80})",
            "role and {0} impact",
        ),
        (
            r"\bonly\s+secured\s+(\d+)\s+([^,.;!?]{4,80}?\binterviews?)\b",
            "{1} result concern",
        ),
        (
            r"\bdeclin(?:ed|ing)\s+(?:a\s+)?\$?\d[\d,]*(?:\s+job\s+offer)?\b.{0,140}\btarget(?:ing)?\s+([^,.;!?]{4,80})",
            "offer decision and {0} target",
        ),
        (
            r"\b(?:portfolio|profile|resume|cv|linkedin)\b[^.;!?]{0,80}\b(?:redesign|update|revision|tailoring|adaptation|metrics?|views?)\b",
            "{match}",
        ),
        (
            r"\b(?:(?:kinda|sorta|really|very|pretty)\s+)?(?:worried|concerned|conflicted|nervous)\s+(?:(?:that|about)\s+)?([^.;!?]{6,120})",
            "{0} concern",
        ),
        (
            r"\b(?:vacation|getaway|dinner|celebration|anniversary|outing)\b[^.;!?]{0,120}",
            "{match}",
        ),
    ]
    for pattern, template in patterns:
        match = re.search(pattern, source, flags=re.I)
        if not match:
            continue
        if template == "{match}":
            phrase = match.group(0)
        else:
            values = [_event_ordering_hint_phrase(part) for part in match.groups()]
            phrase = template.format(*values)
        phrase = _event_ordering_hint_phrase(phrase)
        phrase = re.sub(r"\s+and\s*$", "", phrase, flags=re.I).strip(" .,:;-")
        if phrase and not _event_ordering_low_information_theme_label(phrase):
            return _short_event_ordering_theme(phrase) or phrase

    if re.search(r"\b(?:challenge|challenges|personal|work-related|burnout|workload|stress)\b", lower):
        challenge_match = re.search(
            r"\b(?:burnout|workload|stress|motivation|team dynamics|vacation|unplug|meetings?|asana reports?|progress review|celebration|anniversary dinner)[^.;!?]{0,80}",
            source,
            flags=re.I,
        )
        if challenge_match:
            phrase = _event_ordering_hint_phrase(challenge_match.group(0))
            return _short_event_ordering_theme(phrase) or phrase
    return ""

def _event_ordering_hint_phrase(text: object) -> str:
    phrase = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;-")
    phrase = _event_ordering_strip_request_shell(_event_ordering_clean_label(phrase))
    phrase = re.sub(r"\b(?:can you|could you|please|help me|help us)\b.*$", "", phrase, flags=re.I).strip(" .,:;-")
    phrase = re.split(r"\b(?:you know|but|though|although)\b", phrase, maxsplit=1, flags=re.I)[0].strip(" .,:;-")
    phrase = re.split(r",?\s+and\s+(?:i|we)\b", phrase, maxsplit=1, flags=re.I)[0].strip(" .,:;-")
    phrase = re.split(r",?\s+as\s+(?:a|an)\s+", phrase, maxsplit=1, flags=re.I)[0].strip(" .,:;-")
    phrase = re.sub(r"\b(?:might|may|could|would)\s+not\b.*$", "", phrase, flags=re.I).strip(" .,:;-")
    phrase = re.sub(r"\b(?:on|by|starting|since|between)\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b.*$", "", phrase, flags=re.I).strip(" .,:;-")
    phrase = re.sub(r"\b(?:on|by|starting|since|between)\s+\d{4}-\d{1,2}-\d{1,2}\b.*$", "", phrase, flags=re.I).strip(" .,:;-")
    phrase = re.sub(r"\b(?:in|at|for)\s+my\s*$", "", phrase, flags=re.I).strip(" .,:;-")
    words = phrase.split()
    if len(words) > 10:
        phrase = " ".join(words[:10]).strip(" .,:;-")
    return phrase

def _event_ordering_short_topic_phrase(text: str) -> str:
    phrase = re.sub(r"\s+", " ", text).strip(" .,:;-")
    phrase = re.sub(r"^(?:my|our|the|a|an|new)\s+", "", phrase, flags=re.I)
    phrase = re.split(
        r"\b(?:since|to avoid|to reduce|to make|to save|because|by|how can|can you|should i|or should|without|with my|which are|which is|kinda like|and\s+(?:i|we)(?:'m| am|'re| are)?)\b|[,.;!?]",
        phrase,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" .,:;-")
    phrase = re.sub(r"\s+(?:is|are|has|have)\s+[^,.;!?]{2,80}$", "", phrase, flags=re.I).strip(" .,:;-")
    phrase = re.sub(r"\b(?:monthly|weekly|daily)\s+transfer\s+to\s+savings\b.*$", "savings", phrase, flags=re.I).strip(" .,:;-")
    phrase = re.sub(r"\b(?:app|tool|service|platform)$", "", phrase, flags=re.I).strip(" .,:;-")
    return phrase[:80]

def _event_ordering_under_specified_topic_label(label: str) -> bool:
    lower = label.lower().strip(" .,:;-")
    if not lower:
        return True
    patterns = [
        r"understand how (?:this|that|it|this feature|that feature) (?:will )?affect",
        r"how can (?:i|we) highlight",
        r"figure out how to craft",
        r"worried about (?:my|our) .+ problem in",
        r"(?:i|we)(?:'m| am|'re| are)?\s*thinking of reaching out to .+ advice on",
        r"thinking of reaching out to .+ advice on",
        r"(?:i|we)(?:'m| am|'re| are)?\s*thinking of reaching out to",
        r"thinking of reaching out to",
        r"worried about (?:my|our) .+ update it",
        r"^(?:my|our) age\b",
        r"^at .+ for$",
        r"^at .+ advice on",
        r"^(?:my|our) portfolio\b.+told (?:me|us) to update",
        r"protect (?:my|our|the|a|an)? .+ from",
        r"(?:does|should) .+ need to(?: be reapplied)?",
        r"trouble with .+ error",
        r"(?:getting|seeing) .+ error",
        r"make sure (?:i|we)(?:'m| am|'re| are)? making the right decision",
        r"^highlight$",
        r"set it up",
        r"start the transfers",
        r"minimi[sz]e these fees",
        r"(?:i|we)(?:'ve| have)\s+been\s+tracking",
        r"expecting me to support",
        r"prefer using .+ for",
    ]
    return any(re.search(pattern, lower) for pattern in patterns)

def _event_ordering_strip_request_shell(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" .,:;-")
    patterns = [
        r"^(?:can|could|would)\s+you\s+(?:please\s+)?(?:help\s+me\s+)?",
        r"^(?:please\s+)?help\s+me\s+",
        r"^(?:achieve|learn|understand|explore|see)\s*:\s*(?:how\s+(?:i|we)\s+can\s+)?",
        r"^(?:an?\s+)?example\s+of\s+how\s+(?:i|we)\s+can\s+",
        r"^how\s+can\s+(?:i|we)\s+",
        r"^how\s+(?:i|we)\s+can\s+",
        r"^(?:i|we)(?:'m|'re)\s+(?:currently\s+)?(?:trying|working|looking|hoping|planning)\s+to\s+",
        r"^(?:i|we)\s+(?:am|are|'m|'re)\s+(?:currently\s+)?(?:trying|working|looking|hoping|planning)\s+to\s+",
        r"^(?:i|we)\s+(?:want|wanted|need|needed|plan|planned|decided|chose)\s+to\s+",
        r"^(?:i|we)\s+(?:started|finished|completed|implemented|configured|created|added|fixed|reviewed|expanded|updated)\s+",
        r"^(?:then|next|after(?:ward)?|later),?\s+(?:i|we)\s+",
        r"^(?:what(?:'s| is)\s+)?(?:a|the)?\s*(?:best|good|recommended)?\s*(?:way|approach|method)\s+(?:to|for)\s+",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            updated = re.sub(pattern, "", cleaned, count=1, flags=re.I).strip(" .,:;-")
            if updated != cleaned and updated:
                cleaned = updated
                changed = True
                break
    return cleaned

def _event_ordering_trim_action_tail(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" .,:;-")
    cleaned = re.split(
        r"\b(?:including|so that|because|while|without|instead of|rather than|to avoid|in order to)\b",
        cleaned,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" .,:;-")
    cleaned = re.sub(r"\b(?:can|could|would)\s+you\b.*$", "", cleaned, flags=re.I).strip(" .,:;-")
    if not re.match(r"^(?:decide|deciding|choose|choosing)\s+between\b", cleaned, flags=re.I):
        long_modifier = re.search(r"\b(?:with|using|by)\s+.{55,}$", cleaned, flags=re.I)
        if long_modifier:
            prefix = cleaned[: long_modifier.start()].strip(" .,:;-")
            prefix_terms = _event_ordering_terms(prefix) - _EVENT_ORDERING_SEQUENCE_STOPWORDS
            if len(prefix_terms) >= 3:
                cleaned = prefix
    return cleaned

def _event_ordering_nominal_event_label(text: str, *, context: str = "") -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" .,:;-")
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    decision = _event_ordering_decision_label(cleaned)
    if decision:
        return decision
    nominal_patterns = [
        (r"^(?:set(?:ting)? up|setup)\s+(.{4,180})$", "setup"),
        (r"^(?:init(?:ialize)?|create|created|start(?:ed)?)\s+(.{4,180})$", "initialization"),
        (r"^(?:automat(?:e|ing|ed))\s+(.{4,180})$", "automation"),
        (r"^(?:implement(?:ing)?|build(?:ing)?|add(?:ing|ed)?|develop(?:ing)?)\s+(.{4,180})$", "implementation"),
        (r"^(?:design(?:ing|ed)?|architect(?:ing|ed)?|model(?:ing|ed)?)\s+(.{4,180})$", "design"),
        (r"^(?:configur(?:e|ing|ed)|setup|set(?:ting)? up)\s+(.{4,180})$", "configuration"),
        (r"^(?:deploy(?:ing|ed)?|ship(?:ping|ped)?)\s+(.{4,180})$", "deployment"),
        (r"^(?:finali[sz](?:e|ing|ed)|complete|completing|completed)\s+(.{4,180})$", "finalization"),
        (r"^(?:test(?:ing|ed)?|validate|validating|expand(?:ing|ed)?(?:\s+test)?)\s+(.{4,180})$", "testing"),
        (r"^(?:clean(?:ing)? up|remove|removing|consolidate|consolidating)\s+(.{4,180})$", "cleanup"),
        (r"^(?:fix(?:ing|ed)?|repair(?:ing)?|resolve|resolving)\s+(.{4,180})$", "fix"),
        (r"^(?:handle|handling|support(?:ing)?|manage|managing)\s+(.{4,180})$", "handling"),
        (r"^(?:optimi[sz](?:e|ing|ed)|improve|improving|refine|refining)\s+(.{4,180})$", "improvement"),
        (r"^(?:review(?:ing|ed)?|audit(?:ing|ed)?|check(?:ing|ed)?)\s+(.{4,180})$", "review"),
    ]
    for pattern, noun in nominal_patterns:
        match = re.match(pattern, cleaned, flags=re.I)
        if not match:
            continue
        obj = _event_ordering_nominal_object(match.group(1))
        if len(_event_ordering_terms(obj)) >= 1:
            return f"{obj} {noun}"
    if context:
        context_label = _event_ordering_trim_action_tail(
            _event_ordering_strip_request_shell(_event_ordering_clean_label(context))
        )
        for pattern, noun in nominal_patterns:
            match = re.match(pattern, context_label, flags=re.I)
            if not match:
                continue
            obj = _event_ordering_nominal_object(match.group(1))
            if not obj:
                continue
            if _event_ordering_terms(obj) & _event_ordering_terms(cleaned):
                return f"{obj} {noun}"
    if re.search(r"\b(?:coverage|tests?|validation)\b", lowered) and not re.search(r"\b(?:implement|configure|deploy)\b", lowered):
        return cleaned
    return cleaned

def _event_ordering_nominal_object(text: str) -> str:
    obj = text.strip(" .,:;-")
    obj = re.sub(r"^(?:the|a|an|my|our|this|that)\s+", "", obj, flags=re.I).strip(" .,:;-")
    core_match = re.match(r"^core functionality of (?:my|our|the|this|that)\s+(.{4,80})$", obj, flags=re.I)
    if core_match:
        obj = f"{core_match.group(1).strip(' .,:;-')} core functionality"
    obj = re.sub(r"^cases?\s+where\s+(?:the\s+)?", "", obj, flags=re.I).strip(" .,:;-")
    owner_tail = re.search(r"\b(?:for|in|on)\s+(?:my|our|the|this|that)\s+.{4,}$", obj, flags=re.I)
    if owner_tail and len(_event_ordering_terms(obj[: owner_tail.start()])) >= 2:
        obj = obj[: owner_tail.start()].strip(" .,:;-")
    trimmed_obj = re.sub(r"\b(?:for|in|on)\s+(?:my|our|the|this|that)\s+.{30,}$", "", obj, flags=re.I).strip(" .,:;-")
    obj = trimmed_obj or obj
    return obj

def _event_ordering_decision_label(text: str) -> str:
    match = re.match(r"^(?:decide|deciding|choose|choosing)\s+between\s+(.{4,160})$", text, flags=re.I)
    if not match:
        return ""
    body = re.split(r"\s*,?\s+\bbut\b|\s*,\s+and\b", match.group(1), maxsplit=1, flags=re.I)[0].strip(" .,:;-")
    topic = ""
    topic_match = re.search(r"\bfor\s+(?:my|our|the|this|that)?\s*([^.;!?]{4,80})$", body, flags=re.I)
    if topic_match:
        topic = topic_match.group(1).strip(" .,:;-")
        topic_terms = _event_ordering_terms(topic)
        if topic_terms and topic_terms <= {"frontend", "front", "end", "backend", "back", "approach", "option", "method", "strategy"}:
            topic = ""
    if not topic:
        terms = [
            term
            for term in _event_ordering_terms_ordered(body)
            if term not in _EVENT_ORDERING_SEQUENCE_STOPWORDS and term not in {"using", "pure", "simple"}
        ]
        topic = " ".join(terms[:3])
    return f"{topic} decision".strip() if topic else ""

def _event_ordering_concern_label(text: str) -> str:
    match = re.match(r"^(?:hmm,?\s*)?what\s+if\s+(.{8,160})$", text, flags=re.I)
    if not match:
        return ""
    body = match.group(1).strip(" .,:;-")
    terms = [
        term
        for term in _event_ordering_terms_ordered(body)
        if term not in _EVENT_ORDERING_SEQUENCE_STOPWORDS and term not in {"user", "users", "keeps", "keep"}
    ]
    deduped: list[str] = []
    for term in terms:
        if term.endswith("s") and term[:-1] in deduped:
            continue
        if f"{term}s" in deduped:
            continue
        deduped.append(term)
    terms = deduped
    if not terms:
        return ""
    return f"{' '.join(terms[:4])} concern"

def _event_ordering_preserve_acronyms(text: str) -> str:
    replacements = {
        "api": "API",
        "css": "CSS",
        "crud": "CRUD",
        "html": "HTML",
        "http": "HTTP",
        "json": "JSON",
        "ssl": "SSL",
        "ui": "UI",
        "ux": "UX",
    }
    words = []
    for word in text.split():
        key = word.strip(".,:;()[]").lower()
        if key in replacements:
            words.append(re.sub(re.escape(key), replacements[key], word, flags=re.I))
        else:
            words.append(word)
    return " ".join(words)

def _event_ordering_low_information_theme_label(label: str) -> bool:
    lower = label.lower().strip(" .,:;-")
    if not lower:
        return True
    if _event_ordering_low_information_text(lower):
        return True
    if re.fullmatch(r"(?:sounds good|thanks(?: again)?|thank you|got it|okay|ok|sure)(?:[, ].*)?", lower):
        return True
    if re.fullmatch(r"(?:(?:yeah|yes|ok(?:ay)?|sure|right),?\s+)?(?:i'll|i will|i would|i probably|i think i'll|probably|it makes sense|makes sense).{0,80}", lower):
        return True
    return False

def _event_ordering_specificity(label: str) -> float:
    terms = _event_ordering_terms(label) - _EVENT_ORDERING_SEQUENCE_STOPWORDS - _EVENT_ORDERING_TOPIC_WORDS
    score = float(len(terms))
    if terms & {"error", "testing", "deployment", "configuration", "accessibility", "security", "review", "autocomplete", "api", "promise", "savings", "budget"}:
        score += 1.5
    if len(terms) <= 1:
        score -= 3.0
    if re.fullmatch(r"(?:site|using|weather|project|code|app|help|thanks)(?:\\s+\\w+)?", label.strip(), flags=re.I):
        score -= 4.0
    return score

def _shared_event_ordering_terms(labels: list[str]) -> list[str]:
    term_lists = [
        [
            term
            for term in _event_ordering_terms_ordered(label)
            if term not in _EVENT_ORDERING_SEQUENCE_STOPWORDS and term not in _EVENT_ORDERING_TOPIC_WORDS
        ]
        for label in labels
    ]
    term_lists = [terms for terms in term_lists if terms]
    if not term_lists:
        return []
    counts: dict[str, int] = {}
    first_pos: dict[str, int] = {}
    for terms in term_lists:
        for pos, term in enumerate(dict.fromkeys(terms)):
            counts[term] = counts.get(term, 0) + 1
            first_pos.setdefault(term, pos)
    threshold = 2 if len(term_lists) >= 2 else 1
    shared = [term for term, count in counts.items() if count >= threshold]
    if not shared:
        return []
    shared.sort(key=lambda term: (-counts[term], first_pos.get(term, 999), term))
    return shared[:4]

def _title_from_terms(terms: list[str]) -> str:
    words = [term.replace("_", " ") for term in terms if term]
    text = " ".join(words).strip()
    if not text:
        return ""
    return text.title()

def _event_ordering_label_overlaps_seen(label_key: str, seen_labels: set[str]) -> bool:
    terms = set(label_key.split("-"))
    if not terms:
        return False
    for seen in seen_labels:
        seen_terms = set(seen.split("-"))
        if not seen_terms:
            continue
        if len(terms & seen_terms) / max(1, min(len(terms), len(seen_terms))) >= 0.75:
            return True
    return False

def _event_ordering_plain_support_text(text: str) -> str:
    text = re.split(r"```|###|\b<!doctype\b|<html", text, maxsplit=1, flags=re.I)[0]
    return compact_summary(text, 500)

def _event_ordering_sequence_label(record: dict[str, Any]) -> str:
    timeline_label = str(record.get("timeline_label") or "").strip()
    if (
        timeline_label
        and not _event_ordering_low_information_text(timeline_label)
        and not _event_ordering_fragment_like_label(timeline_label)
        and not _event_ordering_shell_like_label(timeline_label)
        and not _event_ordering_bad_extracted_label(timeline_label)
    ):
        label = _event_ordering_clean_label(timeline_label)
        support_detail = _event_ordering_support_detail(record, label)
        return f"{label}; {support_detail}" if support_detail else label
    existing_label = timeline_label or str(record.get("label") or "").strip()
    conversation_content = str(record.get("conversation_content") or "").strip()
    raw_text = str(record.get("text") or "").strip()
    label_is_shell = bool(existing_label) and (
        _event_ordering_fragment_like_label(existing_label)
        or _event_ordering_shell_like_label(existing_label)
    )
    if (
        existing_label
        and len(existing_label) <= 70
        and not _event_ordering_low_information_text(existing_label)
        and not re.search(r"\b(?:i|we|can you|could you|please|help me)\b", existing_label, flags=re.I)
        and not _event_ordering_fragment_like_label(existing_label)
        and not _event_ordering_shell_like_label(existing_label)
        and not _event_ordering_bad_extracted_label(existing_label)
    ):
        label = _event_ordering_clean_label(existing_label)
        support_detail = _event_ordering_support_detail(record, label)
        return f"{label}; {support_detail}" if support_detail else label
    text = conversation_content or raw_text or existing_label
    if label_is_shell and raw_text:
        text = raw_text
    if existing_label and conversation_content and existing_label not in conversation_content and not label_is_shell:
        text = f"{existing_label}. {conversation_content}"
    elif existing_label and not conversation_content and record.get("text") and not label_is_shell:
        text = f"{existing_label}. {record.get('text')}"
    elif label_is_shell and raw_text:
        prefix = re.escape(existing_label)
        text = re.sub(rf"^\s*{prefix}\s*[:\-]\s*", "", text, count=1, flags=re.I)
        if text == raw_text and existing_label and raw_text.lower().startswith(existing_label.lower()):
            text = re.sub(rf"^\s*{prefix}\s+", "", text, count=1, flags=re.I)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" -;")
    evidence_match = re.search(r"\bEvidence:\s+(.{8,})$", text, flags=re.I)
    if evidence_match and re.match(r"^(?:milestone|event)\s*\[[^\]]+\]", text, flags=re.I):
        text = evidence_match.group(1).strip()
    text = _event_ordering_drop_low_information_lead(text)
    if not text:
        return "Conversation phase"

    explicit = re.match(r"^([^:]{4,90}):\s+.{8,}$", text)
    if explicit:
        explicit_label = explicit.group(1)
        if not (
            _event_ordering_fragment_like_label(explicit_label)
            or _event_ordering_shell_like_label(explicit_label)
        ):
            return _event_ordering_clean_label(explicit_label)
        text = text[explicit.end(1) + 1 :].strip()

    for pattern in [
        r"\b((?:correctly\s+)?(?:link|linking)\s+to\s+(?:the\s+)?[^.;!?]{4,100})",
        r"\b(?:identify|find|show)\s+(?:areas?\s+where\s+)?(?:i|we)\s+can\s+([^.;!?]{8,140})",
        r"\b(?:is there|there'?s|there is)\s+(?:a\s+)?(?:way|approach|method|strategy)\s+to\s+([^.;!?]{8,140})",
        r"\b(?:by|through)\s+([^.;!?]{8,140}?\b(?:strategy|configuration|integration|upgrade|refactor|optimization|testing|deployment|validation|review|fix|caching|monitoring|tracking|handling)\b[^.;!?]{0,80})",
        r"\b(?:i['’]m|i am|we['’]re|we are)\s+(?:currently\s+)?(?:started|finished|completed|implemented|configured|created|added|fixed|reviewed|working on|worked on|focused on|finalizing|planning to|decided|chose|asked about|mentioned|needed|wanted|tried to|trying to)\s+([^;!?]{8,140})",
        r"\b(?:can you|could you|please)\s+help\s+me\s+((?:implement|build|create|set up|setup|configure|fix|add|optimi[sz]e|understand|review|complete|refine)\s+[^;!?]{8,140})",
        r"\b(?:can you|could you|please)\s+help\s+me\s+(?:implement|build|create|set up|setup|configure|fix|add|optimi[sz]e|understand|review|complete|refine)\s+([^.;!?]{8,140})",
        r"\b(?:help me|i need help(?: with)?)\s+(?:implement(?:ing)?|build(?:ing)?|creat(?:e|ing)|set(?:ting)? up|configur(?:e|ing)|fix(?:ing)?|add(?:ing)?|optimi[sz](?:e|ing)|understand(?:ing)?|review(?:ing)?|complete|refine)\s+([^.;!?]{8,140})",
        r"\b(?:can you|could you|please|would you)\s+(?:recommend|suggest|explain|tell me about|walk me through|help me with|help me plan|help me decide|help me prepare|give me|show me)\s+([^.;!?]{8,140})",
        r"\bi\s+was\s+wondering\s+if\s+you\s+could\s+(?:recommend|suggest|explain|tell me about|help me with|help me plan|help me decide|help me prepare|give me|show me)\s+([^.;!?]{8,140})",
        r"\bwhat(?:'s| is)\s+(?:a|the)?\s*(?:best|good|better|effective|recommended)?\s*(?:way|approach|option|alternative|method)\s+(?:to|for)\s+([^.;!?]{8,140})",
        r"\bi['’]m\s+(?:kinda|sorta|really|just|also|still|pretty|very|a bit|so)?\s*(?:worried|nervous|excited|curious|stressed|concerned|unsure|thinking|wondering)\s+(?:about|that|whether|if)?\s+([^.;!?]{8,140})",
        r"\bi['’]ve\s+been\s+thinking\s+(?:a lot\s+)?(?:about|through)?\s+([^.;!?]{8,140})",
        r"\bi['’]ll\s+([^.;!?]{8,140})",
        r"\b(?:i|we)\s+(?:am|are|was|were|'m|'re)?\s*(?:kinda|sorta|really|just|also|still|pretty|very|a bit|so)?\s*(?:worried|nervous|excited|curious|stressed|concerned|unsure|thinking|wondering)\s+(?:about|that|whether|if)?\s+([^.;!?]{8,140})",
        r"\b(?:i|we)\s+(?:have|had|'ve|'d)?\s*been\s+thinking\s+(?:a lot\s+)?(?:about|through)?\s+([^.;!?]{8,140})",
        r"\b(?:i|we)\s+(?:will|'ll|plan to|planned to|planning to|want to|wanted to|decided to|going to|need to|needed to)\s+([^.;!?]{8,140})",
        r"\b(?:i|we)\s+(?:met|read|listened to|watched|joined|started|shared|discussed|asked about|talked about)\s+([^.;!?]{8,140})",
        r"\b(?:i|we)\s+(?:am |are |was |were |'m |'re )?(?:currently\s+)?(?:started|finished|completed|implemented|configured|created|added|fixed|reviewed|working on|worked on|focused on|finalizing|planning to|decided|chose|asked about|mentioned|needed|wanted|tried to|trying to)\s+([^.;!?]{8,140})",
        r"\b(?:what about|how about)\s+([^.;!?]{8,140})",
        r"\bmake\s+sure\s+(?:i(?:'m| am)|we(?:'re| are))\s+doing\s+it\s+correctly\s+to\s+avoid\s+([^.;!?]{8,140})",
    ]:
        match = re.search(pattern, text, flags=re.I)
        if match:
            label = _event_ordering_clean_label(match.group(1))
            if _event_ordering_bad_extracted_label(label):
                continue
            support_detail = _event_ordering_support_detail(record, label)
            return f"{label}; {support_detail}" if support_detail else label

    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    label = _event_ordering_clean_label(sentence[:140])
    support_detail = _event_ordering_support_detail(record, label)
    return f"{label}; {support_detail}" if support_detail else label

def _event_ordering_shell_like_label(label: str) -> bool:
    cleaned = re.sub(r"\s+", " ", label).strip()
    return bool(
        re.match(
            r"^(?:here'?s|here is|i(?:'m| am)\s+(?:trying|having|working|planning|looking|hoping)|i(?:'m| am| was| were)\s+(?:trying|having|working|planning|looking|hoping)|i want|i need|i was wondering|can you|could you|please|what about|how about)\b",
            cleaned,
            flags=re.I,
        )
    )

def _event_ordering_support_detail(record: dict[str, Any], label: str) -> str:
    support = str(record.get("support_text") or "")
    if not support:
        return ""
    label_terms = _event_ordering_terms(label)
    support_terms = _event_ordering_terms(support)
    if len(label_terms & support_terms) < 1:
        return ""
    for pattern in [
        r"\bincluding\s+(?:a\s+|an\s+|the\s+)?([^.;!?]{8,100})",
        r"\bwe(?:'ll| will)\s+also\s+(?:add|include|handle|ensure|create|implement)\s+([^.;!?]{8,100})",
        r"\b(?:add|include|handle|ensure|create|implement)\s+([^.;!?]{8,100})",
    ]:
        match = re.search(pattern, support, flags=re.I)
        if not match:
            continue
        detail = _event_ordering_clean_label(match.group(1))
        detail_terms = _event_ordering_terms(detail)
        if detail and len(detail_terms - label_terms) >= 1:
            return detail[:100]
    return ""

def _event_ordering_low_information_record(record: dict[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in [record.get("label"), record.get("text")])
    return _event_ordering_low_information_text(text)

def _event_ordering_low_information_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip(" .!?,-;:").lower()
    if not normalized:
        return True
    normalized = re.sub(r"^(?:ok(?:ay)?|cool|hmm|right|yeah|yes)[,!\s]+", "", normalized).strip()
    if len(normalized.split()) <= 3 and re.fullmatch(
        r"(?:ok(?:ay)?|cool|great|sure|thanks|thank you|sounds good|sounds great|good idea|nice)",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:ok(?:ay)?|cool|great|sure|thanks|thank you|thanks for (?:the )?[^.!?]{2,50})",
        normalized,
    ):
        return True
    if re.fullmatch(r"(?:that|this|it) sounds (?:good|great|fine|reasonable|solid|like (?:a )?(?:good|great|solid)? ?(?:plan|idea|breakdown|approach))", normalized):
        return True
    if re.fullmatch(r"(?:let'?s|lets) see how it goes", normalized):
        return True
    if re.fullmatch(r"(?:maybe\s+)?something like", normalized):
        return True
    if re.fullmatch(r"what do you think (?:about|of) that", normalized):
        return True
    return False

def _event_ordering_assistant_plan_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip()
    lower = normalized.lower()
    if not normalized:
        return False
    if lower.startswith(("sure, let's", "here's a breakdown", "here is a breakdown", "let's break it down")):
        return True
    if re.search(r"\bdoes this (?:breakdown|plan|timeline|approach) work for you\??\s*$", lower):
        return True
    if re.search(r"\b(?:components|milestones):\s*(?:\d+\.|[-*])", lower) and re.search(
        r"\b(?:registration|login|setup|implement|develop|deployment|testing)\b",
        lower,
    ):
        return True
    return False

def _event_ordering_standing_preference_record(record: dict[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in [record.get("label"), record.get("text")]).strip()
    return bool(
        re.match(
            r"^(?:always|remember to|please always|make sure to always)\b.{0,140}\bwhen\s+(?:i|we)\s+(?:ask|talk|discuss|request)\b",
            text,
            flags=re.I,
        )
    )

def _event_ordering_fragment_like_label(label: str) -> bool:
    cleaned = re.sub(r"\s+", " ", label).strip(" .,:;-")
    if not cleaned:
        return True
    if re.search(r"\b(?:you could|could you|can you|i was wondering|i'm kinda|i'm sorta|i'm thinking|what do you think|maybe we can|let's)\b", cleaned, flags=re.I):
        return True
    if cleaned[-1] not in ".!?" and re.search(r"\b(?:but|so|and|because|while|since)\b", cleaned, flags=re.I):
        return True
    return False

def _event_ordering_bad_extracted_label(label: str) -> bool:
    terms = _event_ordering_terms(label)
    if len(label.strip()) < 12 or len(terms) < 2:
        return True
    if re.fullmatch(r"(?:achieve|learn|understand|explore|see|example|examples?|approach|method|way|option)s?", label.strip(), flags=re.I):
        return True
    if re.match(r"^(?:this\s+by\s+providing|make\s+sure\s+(?:i|we)(?:'m| am|'re| are)?\s+doing\s+it\s+correctly)\b", label.strip(), flags=re.I):
        return True
    if re.fullmatch(r"(?:he|she|they|it|we|i|you|something|someone|anything|everything)(?:\s+\w+){0,8}", label.strip(), flags=re.I):
        return True
    if re.match(r"^(?:he|she|they|it)\s+(?:might|may|could|would|should|can|will)\b", label.strip(), flags=re.I):
        return True
    if label.strip().lower().endswith((" migh", " coul", " shoul", " woul")):
        return True
    return False

def _event_ordering_preference_sequence_query(query: str) -> bool:
    return bool(re.search(r"\b(?:preferences?|instructions?|rules?|guidelines?|format(?:ting)?|always|remember)\b", query, flags=re.I))

def _event_ordering_drop_low_information_lead(text: str) -> str:
    current = text.strip()
    for _ in range(3):
        parts = re.split(r"(?<=[.!?])\s+", current, maxsplit=1)
        if len(parts) < 2 or not _event_ordering_low_information_text(parts[0]):
            break
        current = parts[1].strip()
    current = re.sub(r"^(?:ok(?:ay)?|cool|hmm|right|yeah|yes)[,!\s]+", "", current, flags=re.I).strip()
    return current

def _event_ordering_clean_label(label: str) -> str:
    label = re.sub(r"\s+", " ", label).strip(" .,:;-")
    label = _event_ordering_strip_context_shell(label)
    label = re.sub(r"^(?:the|a|an)\s+", "", label, flags=re.I)
    label = _event_ordering_keep_trailing_short_terms(label)
    return label[:120] or "Conversation phase"

def _event_ordering_strip_context_shell(label: str) -> str:
    text = re.sub(r"\s*->->\s*[\d,]+\s*$", "", label).strip(" .,:;-")
    text = re.sub(r"^(?:yeah|yes|ok(?:ay)?|sure|right|hmm)[,!\s]+", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"^sounds\s+good[,!\s]+", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"\b(?:you know|honestly|basically)\b,?\s*", "", text, flags=re.I)
    text = re.sub(r"\b(?:so\s+)?(?:can|could)\s+you\b.*$", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"\bconsidering\b.*$", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"\b(?:but|and)\s+i(?:'m| am)?\s+not\s+sure\b.*$", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"^something,\s*", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"^something\s+(?:that|for|about)\s+", "", text, flags=re.I).strip(" .,:;-")
    text = re.sub(r"^(?:i|we)\s+think\s+(?:i|we)(?:'ll| will)\s+(?:go ahead and\s+)?", "", text, flags=re.I)
    text = re.sub(r"^(?:i|we)(?:'ll| will)\s+(?:probably\s+)?(?:go ahead and\s+)?", "", text, flags=re.I)
    text = re.sub(r"^go\s+ahead\s+and\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:it|that|this)\s+makes\s+sense\s+to\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:i|we)\s+(?:want|wanted|need|needed|trying|tried)\s+to\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:i|we)\s+(?:was|were|am|are|'m|'re)\s+(?:kinda|sorta|really|pretty|very|a bit|so)?\s*", "", text, flags=re.I)
    text = re.sub(r"^(?:kinda|sorta|really|pretty|very|a bit|so)\s+", "", text, flags=re.I)
    return text or label

def _event_ordering_keep_trailing_short_terms(label: str) -> str:
    trailing = _event_ordering_trailing_short_term(label)
    if trailing:
        match = re.search(rf"\b{re.escape(trailing)}\s*$", label)
        if match:
            prefix = label[: match.start()].rstrip(" ,:-")
            return f"{prefix} {trailing}".strip()
    return label

def _event_ordering_trailing_short_term(text: str) -> str:
    match = re.search(r"\b([A-Za-z]{1,4}|[A-Z]{2,5}|\d+[A-Z]?)\s*$", text.strip(" .,:;-"))
    if not match:
        return ""
    trailing = match.group(1)
    if trailing.lower() in {"ux", "ui", "api", "ssl", "css", "json"}:
        return trailing
    return ""

def _event_ordering_label_key(label: str) -> str:
    terms = sorted(_event_ordering_terms(label) - _EVENT_ORDERING_SEQUENCE_STOPWORDS)
    return "-".join(terms[:8])

def _event_ordering_phase_key(record: dict[str, Any]) -> str:
    text = str(record.get("text") or "")
    terms = sorted(_event_ordering_terms(text) - _EVENT_ORDERING_SEQUENCE_STOPWORDS)
    return "-".join(terms[:3])

def _event_ordering_terms(text: str) -> set[str]:
    return set(_event_ordering_terms_ordered(text))

def _event_ordering_terms_ordered(text: str) -> list[str]:
    terms: set[str] = set()
    ordered: list[str] = []
    for token in re.findall(r"[a-z][a-z0-9_+-]{2,}", text.lower()):
        if token in _EVENT_ORDERING_SEQUENCE_STOPWORDS:
            continue
        if token not in terms:
            terms.add(token)
            ordered.append(token)
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            singular = token[:-1]
            if singular not in _EVENT_ORDERING_SEQUENCE_STOPWORDS and singular not in terms:
                terms.add(singular)
                ordered.append(singular)
    return ordered

_EVENT_ORDERING_SEQUENCE_STOPWORDS = {
    "about",
    "across",
    "after",
    "again",
    "also",
    "and",
    "another",
    "aspect",
    "aspects",
    "before",
    "between",
    "brought",
    "but",
    "can",
    "conversation",
    "conversations",
    "could",
    "did",
    "does",
    "doing",
    "during",
    "each",
    "eight",
    "five",
    "first",
    "four",
    "for",
    "from",
    "good",
    "had",
    "has",
    "have",
    "help",
    "into",
    "just",
    "last",
    "later",
    "like",
    "list",
    "make",
    "mention",
    "mentioned",
    "need",
    "needed",
    "only",
    "one",
    "order",
    "ordered",
    "our",
    "over",
    "phase",
    "phases",
    "please",
    "project",
    "question",
    "should",
    "seven",
    "six",
    "some",
    "step",
    "steps",
    "than",
    "ten",
    "that",
    "the",
    "then",
    "these",
    "think",
    "thing",
    "things",
    "this",
    "through",
    "throughout",
    "time",
    "timeline",
    "three",
    "two",
    "user",
    "walk",
    "was",
    "were",
    "what",
    "when",
    "which",
    "with",
    "work",
    "worked",
    "would",
    "yeah",
}

_EVENT_ORDERING_TOPIC_WORDS = {
    "app",
    "application",
    "code",
    "conversation",
    "conversations",
    "develop",
    "developing",
    "development",
    "different",
    "feature",
    "features",
    "implement",
    "implementing",
    "item",
    "items",
    "order",
    "personal",
    "project",
    "projects",
    "session",
    "sessions",
    "throughout",
    "tool",
    "tracker",
    "website",
}

_EVENT_ORDERING_SCOPE_EQUIVALENTS = {
    "ai": {"hiring", "screening", "candidate", "algorithm", "bias", "fairness", "transparency", "automation"},
    "hiring": {"ai", "screening", "candidate", "recruiting", "vendor", "tool", "bias", "fairness", "transparency"},
    "screening": {"hiring", "candidate", "resume", "bias", "fairness", "transparency"},
    "resume": {"profile", "portfolio", "linkedin", "cv", "career", "interview", "salary", "ATS", "keyword"},
    "profile": {"resume", "portfolio", "linkedin", "cv", "career", "interview"},
    "portfolio": {"resume", "profile", "linkedin", "cv", "career"},
    "linkedin": {"profile", "resume", "portfolio", "career"},
    "framework": {"bootstrap", "css", "cdn", "component", "custom", "class", "classes", "integration"},
    "customizing": {"custom", "css", "class", "classes", "integration", "configuration"},
    "integrating": {"integration", "configure", "configuration", "setup", "cdn"},
}

_EVENT_ORDERING_GENERIC_SCOPE_EQUIVALENTS = {
    "financial": {"finance", "finances", "money", "saving", "savings", "budget", "budgeting", "investment", "investing", "workshop", "literacy", "gift"},
    "planning": {"plan", "plans", "goal", "goals", "budget", "budgeting", "saving", "savings"},
    "topics": {"topic", "concern", "issue", "decision"},
    "experiences": {"experience", "shopping", "purchase", "return", "collection"},
    "challenges": {"challenge", "concern", "stress", "workload", "burnout", "conflict"},
    "ideas": {"idea", "suggestion", "plan", "contribution"},
}

_EVENT_ORDERING_FACET_WORDS = {
    "concern",
    "decision",
    "error",
    "failure",
    "fix",
    "handling",
    "issue",
    "problem",
    "promise",
    "rejection",
    "result",
    "update",
}

_EVENT_ORDERING_CODE_WORDS = {
    "basic",
    "code",
    "component",
    "current",
    "data",
    "display",
    "element",
    "example",
    "function",
    "html",
    "implementation",
    "javascript",
    "method",
    "proper",
    "structure",
    "using",
}

_EVENT_ORDERING_INFRA_WORDS = {
    "auth",
    "authentication",
    "authorization",
    "configuration",
    "coverage",
    "css",
    "custom",
    "deploy",
    "deployment",
    "domain",
    "flexbox",
    "github",
    "grid",
    "https",
    "oauth",
    "responsive",
    "security",
    "style",
    "testing",
}

_EVENT_ORDERING_METHOD_WORDS = {
    "approach",
    "chose",
    "choice",
    "decided",
    "faster",
    "library",
    "method",
    "simplicity",
    "stack",
    "vanilla",
}

_EVENT_ORDERING_IMPLEMENTATION_SIGNAL_WORDS = {
    "accuracy",
    "cache",
    "caching",
    "cost",
    "debounce",
    "debounced",
    "error",
    "errors",
    "handling",
    "latency",
    "limit",
    "limits",
    "performance",
    "rate",
    "response",
    "responses",
    "retry",
    "validation",
}

_EVENT_ORDERING_DESIGN_DRIFT_WORDS = {
    "figma",
    "layout",
    "responsive",
    "wireframe",
}

_EVENT_ORDERING_GENERIC_EPISODE_WORDS = {
    "add",
    "calls",
    "const",
    "help",
    "here",
    "trying",
    "want",
    "wanted",
}
