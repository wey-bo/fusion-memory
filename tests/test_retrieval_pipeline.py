from __future__ import annotations

import unittest

from fusion_memory.api.service import MemoryService
from fusion_memory.core.models import Candidate
from fusion_memory.core.models import Scope
from fusion_memory.retrieval.pipeline import build_pipeline_record


class RetrievalPipelineTests(unittest.TestCase):
    def test_build_pipeline_record_counts_sources_without_raw_text(self) -> None:
        recalled = [
            Candidate("c1", "span", "raw secret text", "l0_raw", {"utility_score": 0.8}, ["s1"], {}),
            Candidate("c2", "fact", "another secret", "l3_current_view", {"utility_score": 0.7}, ["s2"], {}),
        ]
        selected = [recalled[1]]

        record = build_pipeline_record(
            "current_value",
            "benchmark",
            language="en",
            intent="current_value",
            features=["current_value"],
            recalled=recalled,
            selected=selected,
            dropped_count=1,
            source_span_count=1,
            coverage_insufficient=False,
        )
        payload = record.to_dict()

        self.assertEqual(payload["pipeline_layers"]["CandidateRecall"]["source_counts"]["l0_raw"], 1)
        self.assertEqual(payload["pipeline_layers"]["CandidateFusion"]["selected_sources"], ["l3_current_view"])
        self.assertNotIn("raw secret text", repr(payload))

    def test_search_attaches_pipeline_trace_without_raw_text(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="ws-pipeline", user_id="u", agent_id="a")
        raw_memory_text = "Remember private token zinc-sparrow-17 for the pipeline trace test."
        raw_query_text = "Which private token did I ask you to remember for pipeline trace?"
        try:
            memory.add({"role": "user", "content": raw_memory_text}, scope)

            result = memory.search(raw_query_text, scope)
            trace = memory.debug_trace(result.trace_id, scope)

            self.assertIn("pipeline_trace", result.coverage)
            self.assertEqual(trace["pipeline_trace"], result.coverage["pipeline_trace"])
            self.assertIn("retrieval_trace", trace)
            self.assertNotIn(raw_query_text, repr(result.coverage["pipeline_trace"]))
            self.assertNotIn(raw_memory_text, repr(result.coverage["pipeline_trace"]))
        finally:
            memory.close()

    def test_answer_context_exposes_pipeline_trace_in_coverage(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="ws-pipeline-pack", user_id="u", agent_id="a")
        raw_memory_text = "Remember private token cobalt-delta-29 for the answer pack trace test."
        raw_query_text = "Which private token did I ask you to remember for answer pack trace?"
        try:
            memory.add({"role": "user", "content": raw_memory_text}, scope)

            pack = memory.answer_context(raw_query_text, scope)

            self.assertIn("pipeline_trace", pack.coverage)
            self.assertIsInstance(pack.debug_trace, list)
            self.assertNotIn(raw_query_text, repr(pack.coverage["pipeline_trace"]))
            self.assertNotIn(raw_memory_text, repr(pack.coverage["pipeline_trace"]))
        finally:
            memory.close()
