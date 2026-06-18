from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class TaxonomyEntry:
    label: str
    aliases: list[str]
    tags: list[str]
    language: str = "unknown"


@lru_cache(maxsize=1)
def load_default_taxonomy() -> list[TaxonomyEntry]:
    config_path = Path(__file__).resolve().parents[1] / "config" / "default_taxonomy.json"
    raw_entries = json.loads(config_path.read_text(encoding="utf-8"))
    return [
        TaxonomyEntry(
            label=str(entry["label"]),
            aliases=[str(alias) for alias in entry.get("aliases", [])],
            tags=[str(tag) for tag in entry.get("tags", [])],
            language=str(entry.get("language", "unknown")),
        )
        for entry in raw_entries
    ]


def taxonomy_alias_hits(text: str, entries: list[TaxonomyEntry] | None = None) -> set[str]:
    haystack = str(text or "")
    selected_entries = entries if entries is not None else load_default_taxonomy()
    hits: set[str] = set()
    for entry in selected_entries:
        for alias in _entry_terms(entry):
            if _alias_present(haystack, alias):
                hits.add(entry.label)
                break
    return hits


def taxonomy_entry_for_text(text: str, entries: list[TaxonomyEntry] | None = None) -> TaxonomyEntry | None:
    haystack = str(text or "")
    selected_entries = entries if entries is not None else load_default_taxonomy()
    matches: list[tuple[int, TaxonomyEntry]] = []
    for entry in selected_entries:
        longest_match = max((len(term) for term in _entry_terms(entry) if _alias_present(haystack, term)), default=0)
        if longest_match:
            matches.append((longest_match, entry))
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], item[1].label))
    return matches[0][1]


def _alias_present(text: str, alias: str) -> bool:
    normalized_alias = alias.strip()
    if not normalized_alias:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized_alias):
        return normalized_alias in text
    pattern = r"(?<!\w)" + re.escape(normalized_alias) + r"(?!\w)"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _entry_terms(entry: TaxonomyEntry) -> list[str]:
    return [entry.label, *entry.aliases]
