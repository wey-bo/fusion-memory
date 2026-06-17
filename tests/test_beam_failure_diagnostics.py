from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.beam_failure_diagnostics import build_diagnostics


class BeamFailureDiagnosticsTests(unittest.TestCase):
    def test_event_ordering_and_multi_session_patterns_are_classified(self) -> None:
        data = {
            "report": {
                "answers": [
                    {
                        "query_id": "q1",
                        "category": "event_ordering",
                        "score": 0.0,
                        "answer_failed": False,
                        "judge_reason": "event_ordering_tau_norm=0.125",
                        "answer": "1. partner connection planning 2. workload and meeting reduction",
                        "query_text": "Can you list the order ... Mention ONLY and ONLY four items.",
                    },
                    {
                        "query_id": "q2",
                        "category": "multi_session_reasoning",
                        "score": 0.0,
                        "answer_failed": False,
                        "judge_reason": "0.0:The response does not state the required count.",
                        "answer": "I don't have enough evidence in the provided pack to answer.",
                        "query_text": "How many different features did I mention?",
                    },
                    {
                        "query_id": "q3",
                        "category": "multi_session_reasoning",
                        "score": 0.0,
                        "answer_failed": False,
                        "judge_reason": "rubric scoring failed after retries: timeout",
                        "answer": "",
                        "query_text": "How many scenes were filmed?",
                    },
                ]
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(data), encoding="utf-8")

            report = build_diagnostics(path, categories={"event_ordering", "multi_session_reasoning"}, limit=3)

        event = report["categories"]["event_ordering"]
        multi = report["categories"]["multi_session_reasoning"]
        self.assertEqual(event["failure_patterns"]["event_wrong_item_count"], 1)
        self.assertEqual(multi["failure_patterns"]["abstention_or_missing_evidence"], 1)
        self.assertEqual(multi["failure_patterns"]["judge_failed"], 1)


if __name__ == "__main__":
    unittest.main()
