from __future__ import annotations

from typing import Any

from fusion_memory.retrieval.rule_registry import RuleDefinition


def build_rule_audit(
    rule_definitions: list[RuleDefinition],
    hits: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_rule: dict[str, list[dict[str, object]]] = {}
    for hit in hits:
        by_rule.setdefault(str(hit.get("rule_id")), []).append(hit)

    rows: list[dict[str, Any]] = []
    for rule in sorted(rule_definitions, key=lambda item: item.rule_id):
        rule_hits = by_rule.get(rule.rule_id, [])
        contribution_count = sum(
            1
            for hit in rule_hits
            if hit.get("contributed") is True or hit.get("impact") == "selected"
        )
        negative_impact_count = sum(
            1 for hit in rule_hits if hit.get("impact") in {"filtered", "dropped", "misranked"}
        )
        rows.append(
            {
                "rule_id": rule.rule_id,
                "ability": rule.ability,
                "category": rule.category,
                "module": rule.module,
                "hit_count": len(rule_hits),
                "contribution_count": contribution_count,
                "negative_impact_count": negative_impact_count,
                "candidate_for_deletion": len(rule_hits) == 0,
            }
        )
    return rows
