from __future__ import annotations

import unittest

from fusion_memory.core.models import Candidate
from fusion_memory.retrieval.preservation import mark_must_preserve, preserve_required_candidates


class RetrievalPreservationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
