from __future__ import annotations

import unittest
from dataclasses import dataclass

from fusion_memory.core.models import Candidate, QueryPlan, Scope
from fusion_memory.retrieval.providers.base import RecallContext
from fusion_memory.retrieval.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class DummyProvider:
    provider_id: str
    source_family: str
    output_source: str
    supported_query_types: frozenset[str] | None = None
    production_default: bool = True
    shadow_only: bool = False
    graph_related: bool = False

    @property
    def output_sources(self) -> frozenset[str]:
        return frozenset({self.output_source})

    @property
    def replay_categories(self) -> frozenset[str]:
        return frozenset()

    def recall(self, context: RecallContext) -> list[Candidate]:
        return [
            Candidate(
                id=self.provider_id,
                type="span",
                text=f"candidate text for {self.provider_id}",
                source=self.output_source,
                scores={"score": 1.0},
                source_span_ids=[self.provider_id],
                metadata={},
            )
        ]


class RecallProviderRegistryTests(unittest.TestCase):
    def _context(
        self,
        *,
        query_type: str = "fact_lookup",
        enabled_sources: set[str] | None = None,
    ) -> RecallContext:
        return RecallContext(
            service=object(),
            query="raw private query should not be serialized",
            scope=Scope(workspace_id="w", user_id="u", agent_id="a"),
            plan=QueryPlan(query="q", query_type=query_type, entities=[], time_constraints=[]),
            per_source_limit=5,
            enabled_sources=enabled_sources,
            include_session=False,
            event_milestone_group=lambda event: None,
            prior_candidates=[],
        )

    def test_registry_filters_by_source_family_and_query_type_in_order(self) -> None:
        raw = DummyProvider("raw_span", "raw", "l0_raw_hybrid")
        facts = DummyProvider("facts", "facts", "l1_fact_hybrid")
        temporal = DummyProvider("temporal", "raw", "temporal_coverage", frozenset({"temporal_lookup"}))
        registry = ProviderRegistry([raw, facts, temporal])

        providers = registry.enabled_providers(self._context(enabled_sources={"raw"}))

        self.assertEqual([provider.provider_id for provider in providers], ["raw_span"])

    def test_registry_recall_preserves_provider_order_and_prior_candidates(self) -> None:
        first = DummyProvider("first", "raw", "l0_raw_hybrid")
        second = DummyProvider("second", "raw", "raw_scent_trail")
        registry = ProviderRegistry([first, second])
        context = self._context()

        lists = registry.recall(context)

        self.assertEqual([[candidate.id for candidate in items] for items in lists], [["first"], ["second"]])
        self.assertEqual([candidate.id for candidate in context.prior_candidates], ["first", "second"])

    def test_registry_summary_is_structural_without_query_text(self) -> None:
        registry = ProviderRegistry([DummyProvider("raw_span", "raw", "l0_raw_hybrid")])
        context = self._context()
        registry.recall(context)

        summary = registry.summary(context)

        self.assertEqual(summary[0]["provider_id"], "raw_span")
        self.assertEqual(summary[0]["source_family"], "raw")
        self.assertEqual(summary[0]["output_count"], 1)
        self.assertNotIn("raw private query", repr(summary))


if __name__ == "__main__":
    unittest.main()
