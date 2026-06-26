from __future__ import annotations

import unittest

from datetime import datetime, timezone

from fusion_memory import MemoryService, Scope
from fusion_memory.core.models import Candidate, EvidenceSpan, QueryPlan, Scope
from fusion_memory.retrieval.evidence_pack import EvidencePackBuilder
from fusion_memory.retrieval.pipeline import RecallResult
from fusion_memory.retrieval.preservation import (
    annotate_runtime_preservation_candidates,
    mark_must_preserve,
    preserve_required_candidates,
)
from fusion_memory.retrieval.raw_evidence_quota import QuotaResult


class _StoreStub:
    def __init__(self, spans: dict[str, EvidenceSpan]) -> None:
        self._spans = spans

    def get_span(self, span_id: str, scope: Scope | None = None, *, include_session: bool = False) -> EvidenceSpan | None:
        return self._spans.get(span_id)

    def list_spans(self, scope: Scope, *, include_session: bool = False) -> list[EvidenceSpan]:
        return list(self._spans.values())


class RetrievalPreservationTests(unittest.TestCase):
    def test_runtime_annotation_marks_high_signal_sources_without_mutating_inputs(self) -> None:
        graph = Candidate(
            "graph",
            "event",
            "setup schema",
            "event_ordering_persisted_graph",
            {"score": 0.9},
            ["s1"],
            {"existing": "kept"},
        )
        current = Candidate(
            "current",
            "view",
            "Current city is Berlin.",
            "l3_current_view",
            {"score": 0.8},
            ["s2"],
            {},
        )
        ordinary = Candidate("other", "fact", "Old city was Paris.", "l1_fact_hybrid", {"score": 1.0}, ["s3"], {})

        annotated = annotate_runtime_preservation_candidates([graph, current, ordinary])

        self.assertEqual(graph.metadata, {"existing": "kept"})
        self.assertEqual(current.metadata, {})
        self.assertIsNot(annotated[0], graph)
        self.assertIsNot(annotated[1], current)
        self.assertIs(annotated[2], ordinary)
        self.assertEqual(annotated[0].metadata["must_preserve_reason"], ["graph_chronology_anchor"])
        self.assertEqual(annotated[1].metadata["must_preserve_reason"], ["current_value"])

    def test_preserve_required_candidates_adds_missing_high_signal_candidate_and_reports_drops(self) -> None:
        required = mark_must_preserve(
            Candidate("current", "view", "Current city is Berlin.", "l3_current_view", {"score": 0.9}, ["s1"], {}),
            "current_value",
        )
        selected = [Candidate("old", "fact", "Old city was Paris.", "l1_fact_hybrid", {"score": 1.0}, ["s2"], {})]

        preserved, dropped = preserve_required_candidates([required, *selected], selected, limit=2)

        self.assertEqual([candidate.id for candidate in preserved], ["old", "current"])
        self.assertEqual(dropped, [])

    def test_preserve_required_candidates_reports_when_budget_forces_drop(self) -> None:
        required = mark_must_preserve(
            Candidate("graph", "event", "setup schema", "event_ordering_persisted_graph", {"score": 0.9}, ["s1"], {}),
            "graph_chronology_anchor",
        )
        selected = [Candidate("top", "span", "top ranked", "l0_raw_hybrid", {"score": 1.0}, ["s2"], {})]

        preserved, dropped = preserve_required_candidates([required, *selected], selected, limit=1)

        self.assertEqual([candidate.id for candidate in preserved], ["top"])
        self.assertEqual(dropped[0]["candidate_id"], "graph")
        self.assertEqual(dropped[0]["reason"], "budget_limit")
        self.assertEqual(dropped[0]["occupying_candidate_ids"], ["top"])
        self.assertEqual(dropped[0]["occupying_candidate_sources"], ["l0_raw_hybrid"])
        self.assertEqual(dropped[0]["replaced_by"], ["top"])

    def test_evidence_pack_preserves_dropped_high_signal_telemetry(self) -> None:
        scope = Scope(workspace_id="ws")
        span = EvidenceSpan(
            span_id="s2",
            scope=scope,
            turn_id="turn-1",
            speaker="user",
            span_type="turn",
            content="Top ranked supporting evidence.",
            content_hash="hash-s2",
            timestamp=datetime(2026, 6, 18, tzinfo=timezone.utc),
        )
        store = _StoreStub({"s2": span})
        builder = EvidencePackBuilder(store)
        plan = QueryPlan(query="what changed", query_type="fact_lookup", entities=[], time_constraints=[])
        selected = [Candidate("top", "span", "top ranked", "l0_raw_hybrid", {"score": 1.0}, ["s2"], {})]
        dropped = [
            {
                "candidate_id": "graph",
                "reason": "budget_limit",
                "must_preserve_reasons": ["graph_chronology_anchor"],
                "evidence_role": "answer",
                "source": "event_ordering_persisted_graph",
            }
        ]

        pack = builder.build(
            query="what changed",
            plan=plan,
            candidates=selected,
            coverage={"dropped_high_signal_candidates": dropped},
            trace=[],
        )

        self.assertEqual(pack.coverage["dropped_high_signal_candidates"], dropped)


class RetrievalRegressionFixtureTests(unittest.TestCase):
    def test_chinese_exact_phrase_survives_search(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="zh-recall", user_id="u", agent_id="a", session_id="s")
        memory.add(
            "请记住：我的默认数据库是 PostgreSQL，嵌入模型是 qwen0.6B。",
            scope,
            datetime(2026, 6, 18, tzinfo=timezone.utc),
            {"source_uri": "zh1"},
        )

        result = memory.search("我的默认数据库是什么？", scope, {"mode": "fast", "limit": 5})

        self.assertTrue(any("PostgreSQL" in candidate.text for candidate in result.candidates))

    def test_current_value_preserves_latest_view_over_stale_history(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="current-value", user_id="u", agent_id="a", session_id="s")
        memory.add("My preferred database is SQLite.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc), {"source_uri": "old"})
        memory.add(
            "Update: my preferred database is PostgreSQL now.",
            scope,
            datetime(2026, 6, 2, tzinfo=timezone.utc),
            {"source_uri": "new"},
        )

        pack = memory.answer_context("What is my current preferred database?", scope, budget={"mode": "benchmark"})

        joined = " ".join(span.get("content", "") for span in pack.source_spans)
        self.assertIn("PostgreSQL", joined)
        self.assertNotIn("SQLite", joined[:200])

    def test_multi_condition_recall_preserves_distributed_evidence(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="multi-condition", user_id="u", agent_id="a", session_id="s")
        ts = datetime(2026, 6, 18, tzinfo=timezone.utc)
        memory.add("For the OpenClaw adapter, install must be one command.", scope, ts, {"source_uri": "m1"})
        memory.add("For the same adapter, errors must be beginner friendly.", scope, ts, {"source_uri": "m2"})

        result = memory.search(
            "What OpenClaw adapter requirements mention install and beginner friendly errors?",
            scope,
            {"mode": "fast", "limit": 5},
        )

        text = " ".join(candidate.text for candidate in result.candidates)
        self.assertIn("one command", text)
        self.assertIn("beginner friendly", text)

    def test_event_ordering_restore_reports_structured_reason_codes(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="preserve-order", user_id="u", agent_id="a", session_id="s")
        selected = Candidate(
            "selected",
            "span",
            "Selected itinerary step.",
            "event_ordering_timeline",
            {"utility_score": 1.0, "semantic_score": 0.9},
            ["selected"],
            {},
        )
        required = Candidate(
            "graph",
            "span",
            "Graph chronology anchor.",
            "event_ordering_persisted_graph",
            {"utility_score": 0.1, "semantic_score": 0.1},
            ["graph"],
            {},
        )

        memory._recall_candidates = lambda context: RecallResult(
            candidate_lists=[[selected, required]],
            recalled_candidates=[selected, required],
        )
        memory.quota.enforce = lambda plan, search_scope, candidates, include_session=False: QuotaResult(
            candidates=list(candidates),
            selected_span_ids=["selected"],
            required=1,
            coverage_insufficient=False,
            backfilled=0,
        )
        memory._select_event_ordering_candidates = lambda query, plan, candidates, selected_candidates, limit: [
            candidate for candidate in selected_candidates if candidate.id == "selected"
        ]
        memory.store.insert_utility_example = lambda example: None

        result = memory.search(
            "List the itinerary changes in chronological order.",
            scope,
            {"mode": "fast", "limit": 2, "query_type_hint": "event_ordering"},
        )

        restored = result.coverage["event_ordering_selection"]["preservation_restored"]
        self.assertTrue(restored)
        self.assertEqual(restored[0]["candidate_id"], "graph")
        self.assertEqual(restored[0]["reason"], "graph_chronology_anchor")
        self.assertEqual(restored[0]["must_preserve_reasons"], ["graph_chronology_anchor"])
        self.assertEqual(restored[0]["source"], "event_ordering_persisted_graph")


if __name__ == "__main__":
    unittest.main()
