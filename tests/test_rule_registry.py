from __future__ import annotations

import unittest

from fusion_memory.retrieval.rule_registry import (
    RuleDefinition,
    drain_rule_hits,
    record_rule_hit,
    register_rule,
    registered_rules,
)


class RuleRegistryTests(unittest.TestCase):
    def test_register_rule_and_record_hit_without_raw_text(self) -> None:
        drain_rule_hits()
        rule = register_rule(
            RuleDefinition(
                rule_id="current_value.stale_history_marker",
                module="fusion_memory.retrieval.evidence_pack",
                purpose="avoid stale current-value evidence",
                category="generic",
                pattern="initially|previously",
            )
        )

        hit = record_rule_hit(
            rule.rule_id,
            query="What is current?",
            text="I initially used SQLite.",
            stage="evidence_pack_filter",
            contributed_candidate_id="span_1",
        )

        self.assertIn(rule, registered_rules())
        self.assertEqual(hit.rule_id, rule.rule_id)
        self.assertNotIn("SQLite", hit.text_hash)
        self.assertEqual(drain_rule_hits()[0].contributed_candidate_id, "span_1")
