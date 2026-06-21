from __future__ import annotations

import unittest

from fusion_memory.core.models import Candidate
from fusion_memory.retrieval.candidate_lifecycle import CandidateLifecycleRecorder


class CandidateLifecycleTests(unittest.TestCase):
    def test_record_sanitizes_candidate_without_raw_text(self) -> None:
        candidate = Candidate(
            id="cand-1",
            type="span",
            text="raw private preference text",
            source="l0_raw_hybrid",
            scores={"utility_score": 0.9},
            source_span_ids=["span-1"],
            metadata={"raw_text": "do not persist", "safe": "candidate_1"},
        )
        recorder = CandidateLifecycleRecorder()

        recorder.record(candidate, "recalled", "raw_provider")
        payload = recorder.to_trace()

        self.assertEqual(payload[0]["candidate_id"], "cand-1")
        self.assertEqual(payload[0]["candidate_type"], "span")
        self.assertEqual(payload[0]["candidate_source"], "l0_raw_hybrid")
        self.assertEqual(payload[0]["stage"], "recalled")
        self.assertEqual(payload[0]["reason_code"], "raw_provider")
        self.assertNotIn("raw private preference text", repr(payload))
        self.assertNotIn("do not persist", repr(payload))

    def test_summary_counts_stages_and_sources(self) -> None:
        recorder = CandidateLifecycleRecorder()
        first = Candidate("a", "span", "alpha secret", "l0_raw_hybrid", {"utility_score": 0.8}, ["s1"], {})
        second = Candidate("b", "fact", "beta secret", "l1_fact_hybrid", {"utility_score": 0.5}, ["s2"], {})

        recorder.record(first, "recalled", "raw_provider")
        recorder.record(first, "selected", "final_selection", contributed=True)
        recorder.record(second, "filtered", "topic_scope", contributed=False)

        summary = recorder.summary()

        self.assertEqual(summary["stage_counts"]["recalled"], 1)
        self.assertEqual(summary["stage_counts"]["selected"], 1)
        self.assertEqual(summary["stage_counts"]["filtered"], 1)
        self.assertEqual(summary["source_counts"]["l0_raw_hybrid"], 2)
        self.assertEqual(summary["source_counts"]["l1_fact_hybrid"], 1)
        self.assertEqual(summary["contributed_count"], 1)

    def test_trace_limit_keeps_terminal_records_visible(self) -> None:
        recorder = CandidateLifecycleRecorder()
        for index in range(12):
            candidate = Candidate(f"recall-{index}", "span", f"secret recall {index}", "l0_raw", {}, [f"s{index}"], {})
            recorder.record(candidate, "recalled", "candidate_provider")
        rescued = Candidate("rescued", "span", "rescued secret", "quality_fallback", {}, ["sr"], {})
        filtered = Candidate("filtered", "span", "filtered secret", "l0_raw", {}, ["sf"], {})
        selected = Candidate("selected", "span", "selected secret", "l0_raw", {}, ["ss"], {})
        packed = Candidate("packed", "span", "packed secret", "l0_raw", {}, ["sp"], {})

        recorder.record(rescued, "rescued", "quality_fallback")
        recorder.record(filtered, "filtered", "topic_scope_filter")
        recorder.record(selected, "selected", "final_selection")
        recorder.record(packed, "packed", "answer_pack")

        trace = recorder.to_trace(limit=5)
        stages = [entry["stage"] for entry in trace]

        self.assertEqual(len(trace), 5)
        self.assertIn("rescued", stages)
        self.assertIn("filtered", stages)
        self.assertIn("selected", stages)
        self.assertIn("packed", stages)
        self.assertNotIn("rescued secret", repr(trace))


if __name__ == "__main__":
    unittest.main()
