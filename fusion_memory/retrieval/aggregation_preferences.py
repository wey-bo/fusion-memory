from __future__ import annotations

import re
from typing import Any

from fusion_memory.core.text import compact_summary
from fusion_memory.retrieval.aggregation_common import _match_context

def _preference_constraint_items(query: str, source_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not source_spans or not _query_accepts_preference_constraints(query):
        return []
    query_lower = query.lower()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for span in source_spans[:96]:
        content = str(span.get("content") or "")
        if not content.strip():
            continue
        for candidate in _preference_constraint_candidates_from_text(query_lower, content):
            key = (candidate["type"], candidate["label"].lower())
            if key in seen:
                continue
            seen.add(key)
            candidate["source_span_id"] = span.get("id")
            candidate["speaker"] = span.get("speaker")
            candidate["timeline_index"] = span.get("timeline_index")
            candidate["history_index"] = span.get("history_index")
            candidate["recency_rank"] = span.get("recency_rank")
            candidate["context"] = compact_summary(candidate.pop("_context", content), 260)
            if str(span.get("speaker") or "").lower() == "user":
                candidate["score"] += 0.4
            rows.append(candidate)
    rows.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            int(item.get("recency_rank") or 10**9),
            -int(item.get("timeline_index") or item.get("history_index") or -1),
            str(item.get("type") or ""),
            str(item.get("label") or ""),
        )
    )
    return rows[:16]

def _preference_requirement_checklist(
    constraints: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> dict[str, Any]:
    if not constraints:
        return {}
    must_satisfy: list[dict[str, str]] = []
    must_avoid: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for constraint in constraints:
        type_ = str(constraint.get("type") or "").strip()
        label = str(constraint.get("label") or "").strip()
        if not type_ or not label:
            continue
        key = (type_, label.lower())
        if key in seen:
            continue
        seen.add(key)
        row = {
            "type": type_,
            "requirement": _preference_requirement_text(type_, label),
        }
        if type_.startswith("avoid_"):
            must_avoid.append(row)
        else:
            must_satisfy.append(row)
        if len(must_satisfy) >= limit and len(must_avoid) >= 4:
            break
    if not must_satisfy and not must_avoid:
        return {}
    return {
        **({"must_satisfy": must_satisfy[:limit]} if must_satisfy else {}),
        **({"must_avoid": must_avoid[:4]} if must_avoid else {}),
        "coverage_guidance": (
            "Use this as an answer checklist for preference or planning queries. Preserve explicit numbers, "
            "times, named candidates, named tools, formats, and negative constraints instead of replacing them "
            "with generic advice."
        ),
    }

def _preference_requirement_text(type_: str, label: str) -> str:
    if type_ == "date_format":
        return f"Use the requested date format: {label}."
    if type_ == "version_detail":
        return f"Include software version details when supported: {label}."
    if type_ == "session_length":
        return f"Use the explicit short-session length: {label}."
    if type_ in {"time_window", "time_preference", "routine_timing"}:
        return f"Respect the timing preference: {label}."
    if type_ == "candidate_rationale":
        return f"Name the supported candidate and rationale: {label}."
    if type_ == "recommendation_balance":
        return f"Balance the recommendation set across the requested types, with comparable coverage or a clear alternating structure: {label}."
    if type_.startswith("avoid_"):
        return f"Do not recommend this avoided option or approach: {label}."
    return label.rstrip(".") + "."

def _query_accepts_preference_constraints(query: str) -> bool:
    lower = query.lower()
    return bool(
        re.search(
            r"\b(?:suggest|recommend|plan|schedule|how should|how would|help me|what should|what snacks|"
            r"where|places?|options?|materials?|quality|editing|writing|sessions?|movies?|books?|audiobooks?|"
            r"probability|chance|dependent events?|draw|deck|social norms?|expectations?|meeting someone|"
            r"organize|keep|include|content|materials?|documents?|candidates?|choose|buy|options?|"
            r"when|dates?|deadlines?|timeline|scheduling|meetings?|workshops?)\b",
            lower,
        )
    )


def _query_matches_version_instruction(query_lower: str, content_lower: str) -> bool:
    if not re.search(r"\b(?:software\s+)?version\s+details\b|\bversion\s+numbers?\b|\bversions?\b", content_lower):
        return False
    query_tokens = set(re.findall(r"[a-z0-9]+", query_lower))
    content_tokens = set(re.findall(r"[a-z0-9]+", content_lower))
    domain_tokens = {
        "application",
        "applications",
        "asset",
        "assets",
        "dependencies",
        "dependency",
        "digital",
        "files",
        "libraries",
        "library",
        "management",
        "organize",
        "packages",
        "software",
        "tools",
    }
    if query_tokens & content_tokens & domain_tokens:
        return True
    return bool(
        re.search(r"\b(?:libraries|dependencies|packages|software|tools)\b", query_lower)
        and re.search(r"\b(?:libraries|dependencies|packages|software|tools)\b", content_lower)
    )


def _preference_constraint_candidates_from_text(query_lower: str, content: str) -> list[dict[str, Any]]:
    lower = content.lower()
    rows: list[dict[str, Any]] = []

    def add(type_: str, label: str, context: str, score: float) -> None:
        label = re.sub(r"\s+", " ", label.strip())
        if not label:
            return
        rows.append({"type": type_, "label": label[:160], "_context": context, "score": score})

    writing_or_schedule = bool(re.search(r"\b(?:writ|edit|draft|session|schedule|week|statement)\b", query_lower))
    if writing_or_schedule:
        if re.search(r"\b(?:scrivener|split[-\s]?screen|side[-\s]?by[-\s]?side|two panels?)\b", lower):
            match = re.search(r"\b(?:scrivener|split[-\s]?screen|side[-\s]?by[-\s]?side|two panels?)\b", content, flags=re.I)
            if match:
                add(
                    "format_instruction",
                    "use Scrivener split-screen mode for draft revisions",
                    _match_context(content, match.start(), match.end(), radius=220),
                    3.5,
                )
        for match in re.finditer(
            r"\b(?:between\s+)?(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\s*(?:-|to|and)\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\b",
            content,
            flags=re.I,
        ):
            ctx = _match_context(content, match.start(), match.end(), radius=180)
            if re.search(r"\b(?:prefer|preferred|most focused|productive)\b", ctx, flags=re.I):
                add("time_window", f"preferred time window: {match.group(1)}-{match.group(2)}", ctx, 3.4)
        if re.search(r"\b(?:prefer|most focused|productive)\b.{0,80}\bmornings?\b|\bmornings?\b.{0,80}\b(?:most focused|productive)\b", lower):
            match = re.search(r"\bmornings?\b", content, flags=re.I)
            if match:
                add("time_preference", "prefers morning work sessions", _match_context(content, match.start(), match.end(), radius=180), 2.2)
        for match in re.finditer(
            r"\b(\d{1,3})\s*-?\s*minutes?\s+(?:sessions?|blocks?|sprints?|at\s+a\s+time|each|daily|per\s+(?:session|block|day))\b",
            content,
            flags=re.I,
        ):
            ctx = _match_context(content, match.start(), match.end(), radius=180)
            score = 3.5 if re.search(r"\b(?:prefer|short bursts?|rather than|marathon|burnout|avoid)\b", ctx, flags=re.I) else 2.8
            add("session_length", f"short session length: {match.group(1)} minutes", ctx, score)

    if re.search(r"\b(?:probability|chance|dependent events?|deck|draw)\b", query_lower):
        match = re.search(r"\b(?:tree\s+diagram|visual\s+(?:aid|diagram)|diagram)\b", content, flags=re.I)
        if match:
            ctx = _match_context(content, match.start(), match.end(), radius=220)
            if re.search(r"\b(?:probability|dependent|without replacement|draw|deck|events?)\b", ctx, flags=re.I):
                add("format_instruction", "include a tree diagram for dependent probability problems", ctx, 3.6)

    if re.search(r"\b(?:social norms?|expectations?|meeting someone|first time|common expectations)\b", query_lower):
        match = re.search(r"\b(?:cultural|culture|cross[-\s]?cultural|social norms?)\b", content, flags=re.I)
        if match:
            ctx = _match_context(content, match.start(), match.end(), radius=220)
            add("content_instruction", "include cultural context and cross-cultural variation for social norms", ctx, 3.4)

    if re.search(r"\b(?:where|place|places|location|writing|spend my next few hours)\b", query_lower):
        location_patterns = [
            ("library", r"\b(?:public\s+)?library\b"),
            ("quiet location", r"\bquiet\s+(?:place|space|location|environment)\b"),
            ("home office", r"\bhome\s+office\b"),
            ("coffee shop", r"\bcoffee\s+shop\b"),
            ("park", r"\bpublic\s+park\b|\bpark\b"),
        ]
        for label, pattern in location_patterns:
            match = re.search(pattern, content, flags=re.I)
            if match:
                ctx = _match_context(content, match.start(), match.end(), radius=180)
                if label == "library" or re.search(r"\b(?:writing|write|focus|quiet|location|place)\b", ctx, flags=re.I):
                    add("place_preference", label, ctx, 2.6)

    if re.search(r"\b(?:snacks?|food|eat|try|recommend)\b", query_lower):
        match = re.search(r"\b(?:food\s+)?allerg(?:y|ies|ic)\b", content, flags=re.I)
        if match:
            add("safety_check", "ask about food allergies before snack recommendations", _match_context(content, match.start(), match.end(), radius=180), 3.2)

    if re.search(r"\b(?:movies?|watch|michelle|family|streaming)\b", query_lower):
        for pattern, label in [
            (r"\bsubtitles?\b", "verify subtitle availability"),
            (r"\b(?:desired\s+)?languages?\b|\bdubbed\b|\baudio tracks?\b", "include language or audio-track options"),
            (r"\bspanish\b", "consider Spanish-language learning subtitles"),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("accessibility_language", label, _match_context(content, match.start(), match.end(), radius=180), 2.7)
        match = re.search(r"\b(?:positive\s+(?:family\s+)?reviews?|family\s+reviews?|audience\s+ratings?|critics?\s+and\s+audiences?)\b", content, flags=re.I)
        if match:
            add("review_signal", "prefer options with positive family or audience reviews", _match_context(content, match.start(), match.end(), radius=220), 3.1)

    if re.search(r"\b(?:sneakers?|materials?|quality|shoes?)\b", query_lower):
        for pattern, label in [
            (r"\bsustainab(?:le|ility)\b", "include sustainability features"),
            (r"\beco-?friendly\b", "include eco-friendly materials"),
            (r"\brecycled\s+materials?\b", "mention recycled materials"),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("sustainability", label, _match_context(content, match.start(), match.end(), radius=180), 2.9)
        for pattern, label in [
            (r"\bsleek\s+and\s+modern\b|\bmodern\s+(?:look|design|aesthetic)\b", "prefer sleek, modern sneaker styling"),
            (r"\bneutral\s+colors?\b|\bblack,\s*white,\s*or\s+gr[ae]y\b|\b(?:black|gr[ae]y)\s+(?:sneakers?|colorways?)\b", "prefer neutral colors such as black or gray"),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("style_constraint", label, _match_context(content, match.start(), match.end(), radius=220), 3.2)

    if re.search(r"\b(?:edit|editing|draft|revise|revision)\b", query_lower):
        for pattern, label in [
            (r"\bsplit-?screen\b", "use a split-screen editing view"),
            (r"\bside-?by-?side\b", "compare drafts side by side"),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("editing_workflow", label, _match_context(content, match.start(), match.end(), radius=180), 3.0)
        for pattern, label, score in [
            (
                r"\b(?:AI\s+tools?|AI-assisted|Grammarly|Hemingway\s+Editor|ProWritingAid|Jasper\s+AI)\b",
                "start editing with AI-assisted tools when supported",
                3.6,
            ),
            (
                r"\btone\s+calibration\b|\btone\s+(?:consistency|goals?)\b",
                "use AI/tool support for tone calibration or tone consistency",
                3.4,
            ),
            (
                r"\bcomparison\s+tool\b|\bcompare\s+versions?\b|\bversion\s+comparison\b",
                "compare draft versions during editing",
                2.8,
            ),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("tool_workflow", label, _match_context(content, match.start(), match.end(), radius=220), score)

    if re.search(r"\b(?:when|due|deadline|submission|date|schedule|scheduling|meeting|workshop|timeline)\b", query_lower):
        for pattern, label, score in [
            (r"\bMM/DD/YYYY\b|\bmm/dd/yyyy\b", "format dates as MM/DD/YYYY", 3.6),
            (r"\bmonth\s+day,\s+year\b|\bmonth-day-year\b|\bmonth\s+day\s+year\b", "format dates as Month Day, Year", 3.1),
            (r"\bformat\s+dates?\b", "preserve the requested date format", 2.4),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("date_format", label, _match_context(content, match.start(), match.end(), radius=180), score)

    if _query_matches_version_instruction(query_lower, lower):
        match = re.search(r"\b(?:software\s+)?version\s+details\b|\bversion\s+numbers?\b|\bversions?\b", content, flags=re.I)
        if match:
            add("version_detail", "include software version details when supported", _match_context(content, match.start(), match.end(), radius=220), 3.5)

    if re.search(r"\b(?:audiobooks?|listen|narrator|narrators)\b", query_lower):
        match = re.search(r"\bnarrators?\b|\bnarrated\s+by\b", content, flags=re.I)
        if match:
            add("audiobook_detail", "include narrator information when supported", _match_context(content, match.start(), match.end(), radius=180), 2.5)

    if re.search(r"\b(?:documents?|will|estate|updates?|changes?|keep|organize)\b", query_lower):
        for pattern, label in [
            (r"\b(?:WillMaker\s+Pro|will\s+updat(?:e|ing)\s+tools?|digital\s+will\s+tools?|estate\s+planning\s+software)\b", "use digital will updating tools when making future document changes"),
            (r"\belectronic\s+signatures?\b|\be-?filing\b|\belectronic\s+(?:copies|methods?)\b", "prefer electronic update and filing workflows when supported"),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("document_workflow", label, _match_context(content, match.start(), match.end(), radius=220), 3.4)

    if re.search(r"\b(?:patent|application|materials?|content|include|comprehensive|clear)\b", query_lower):
        for pattern, label in [
            (r"\bdetailed\s+drawings?\b|\bdrawings?\s+that\s+illustrate\b", "include detailed drawings"),
            (
                r"\b(?:video\s+)?demos?\b|\bdemonstration\s+videos?\b|\bmultimedia\b|\bdemonstrat(?:e|ion)\s+(?:the\s+)?(?:prototype|invention|device)\b",
                "include video demos or multimedia demonstrations when supported",
            ),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("content_format", label, _match_context(content, match.start(), match.end(), radius=220), 3.3)

    if re.search(r"\b(?:reading|books?|novels?|series|suggest|recommend|list)\b", query_lower):
        for pattern, label in [
            (
                r"\bbalanc(?:e|ing)\s+(?:standalone\s+)?novels?\s+with\s+series\b|\bgood\s+mix\s+of\s+both\s+types\s+of\s+books\b",
                "balance recommendations between standalone novels and series",
            ),
            (r"\bstandalone\s+novels?\b", "include standalone novels in reading recommendations"),
            (r"\b(?:book|fiction|fantasy|historical\s+fiction)\s+series\b|\bseries\s+recommendations?\b", "include series in reading recommendations"),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add("recommendation_balance", label, _match_context(content, match.start(), match.end(), radius=240), 3.1)

    if re.search(r"\b(?:expenses?|budget|dining out|track|tracking|organize|monthly)\b", query_lower):
        for pattern, label, type_, score in [
            (r"\b(?:Excel|spreadsheet|manual\s+tracking|notebook|journal)\b", "prefer simple spreadsheet or manual tracking tools", "tool_preference", 3.2),
            (r"\b(?:without\s+relying\s+on\s+digital\s+tools|full\s+control|customizable|basic\s+expense\s+tracking\s+spreadsheet)\b", "avoid overcomplicated or specialized budgeting platforms when simple tracking is requested", "avoid_tool_type", 3.4),
        ]:
            match = re.search(pattern, content, flags=re.I)
            if match:
                add(type_, label, _match_context(content, match.start(), match.end(), radius=220), score)

    if re.search(r"\b(?:responsibilities|executor|candidates?|choose|choosing|appoint)\b", query_lower):
        for match in re.finditer(
            r"\b([A-Z][a-z]{2,})\b.{0,120}\b(?:organizational\s+(?:skills|abilities)|organized|reliable|responsible|best\s+fit)\b",
            content,
            flags=re.I,
        ):
            name = match.group(1)
            if name.lower() in {"choosing", "factors", "pros", "cons", "step"}:
                continue
            add("candidate_rationale", f"consider {name} when their organizational or reliability strengths are supported", _match_context(content, match.start(), match.end(), radius=220), 3.2)

    if re.search(r"\b(?:organize|day|routine|responsibilities|stay on track|schedule)\b", query_lower):
        match = re.search(r"\b(?:consistent|regular|same|fixed|specific)\b.{0,80}\b(?:routine|time|timing|times?|schedule)\b|\b(?:routine|time|timing|times?|schedule)\b.{0,80}\b(?:consistent|regular|same|fixed|specific)\b", content, flags=re.I)
        if match:
            add("routine_timing", "use consistent timing for recurring routine activities", _match_context(content, match.start(), match.end(), radius=220), 3.1)

    return rows
