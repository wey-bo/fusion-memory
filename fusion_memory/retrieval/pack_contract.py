from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PACK_CONTRACT_VERSION = "typed-evidence-pack-v1"


@dataclass(frozen=True)
class PackSection:
    """Contract for a typed evidence section.

    The contract is intentionally small: it records where a section should be
    built and whether model adapters are allowed to derive it as a fallback.
    This keeps retrieval, packing, and benchmark prompt shaping from silently
    growing duplicate implementations.
    """

    name: str
    owner: str
    description: str
    adapter_may_derive: bool = False


PACK_SECTIONS: tuple[PackSection, ...] = (
    PackSection(
        name="raw_evidence",
        owner="retrieval",
        description="Chronological source spans with stable ids, speakers, timestamps, and provenance.",
    ),
    PackSection(
        name="timeline",
        owner="typed_pack",
        description="Topic-scoped conversation chronology for ordered-event questions.",
    ),
    PackSection(
        name="value_history",
        owner="typed_pack",
        description="Subject-bound historical/current values for latest-value and update questions.",
    ),
    PackSection(
        name="aggregation",
        owner="typed_pack",
        description="Countable or summable items with inclusion roles and source spans.",
    ),
    PackSection(
        name="temporal",
        owner="typed_pack",
        description="Date/time mentions, endpoint roles, and answer-candidate ranges.",
    ),
    PackSection(
        name="conflict",
        owner="typed_pack",
        description="Contradictory claim buckets and source-backed resolution candidates.",
    ),
    PackSection(
        name="summary",
        owner="typed_pack",
        description="Issue/resolution pairs and workstream clusters for broad summaries.",
    ),
    PackSection(
        name="instruction",
        owner="typed_pack",
        description="Answer-format, preference, and instruction constraints.",
    ),
    PackSection(
        name="model_view",
        owner="model_adapter",
        description="Compact serialization for a specific answer model or benchmark harness.",
        adapter_may_derive=True,
    ),
)

_SECTION_BY_NAME = {section.name: section for section in PACK_SECTIONS}


def pack_contract_metadata(*, active_sections: list[str] | None = None) -> dict[str, Any]:
    sections = [
        {
            "name": section.name,
            "owner": section.owner,
            "adapter_may_derive": section.adapter_may_derive,
        }
        for section in PACK_SECTIONS
    ]
    return {
        "version": PACK_CONTRACT_VERSION,
        "active_sections": list(dict.fromkeys(active_sections or [])),
        "sections": sections,
    }


def ensure_known_pack_sections(section_names: list[str]) -> None:
    unknown = [name for name in section_names if name not in _SECTION_BY_NAME]
    if unknown:
        raise ValueError(f"unknown evidence pack sections: {unknown}")


def active_pack_sections_for(query_type: str, coverage: dict[str, Any]) -> list[str]:
    sections = ["raw_evidence"]
    if query_type == "event_ordering":
        sections.append("timeline")
    if coverage.get("value_history") or query_type == "knowledge_update":
        sections.append("value_history")
    if coverage.get("temporal_candidates") or coverage.get("temporal_range_pairs"):
        sections.append("temporal")
    if coverage.get("resolution_pairs") or coverage.get("summary_clusters"):
        sections.append("summary")
    if coverage.get("instruction_constraints") or query_type == "instruction":
        sections.append("instruction")
    if coverage.get("aggregation_items") or query_type == "multi_session_reasoning":
        sections.append("aggregation")
    if query_type == "contradiction_resolution":
        sections.append("conflict")
    ensure_known_pack_sections(sections)
    return list(dict.fromkeys(sections))
