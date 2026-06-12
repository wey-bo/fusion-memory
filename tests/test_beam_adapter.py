from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fusion_memory import MemoryService, Scope
from fusion_memory.eval.beam_adapter import BeamAdapter, _event_ordering_score


class BeamAdapterTests(unittest.TestCase):
    def test_beam_adapter_loads_official_chat_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset = _write_official_beam_fixture(Path(tmp))
            adapter = BeamAdapter(MemoryService(), Scope(workspace_id="w", user_id="u", agent_id="a"), split="small")

            ingest = adapter.ingest_dataset(dataset, split="small")
            queries = adapter.build_queries(dataset, split="small")

            self.assertEqual(ingest["documents"], 2)
            self.assertEqual(len(queries), 3)
            self.assertEqual(queries[0].category, "information_extraction")
            self.assertIn("Qdrant", queries[0].gold_answers[0])
            instruction_query = next(query for query in queries if query.category == "instruction_following")
            self.assertTrue(instruction_query.gold_answers)
            self.assertIn("syntax highlighting", instruction_query.gold_answers[0])

    def test_beam_adapter_runs_split_and_records_answer_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset = _write_official_beam_fixture(Path(tmp))
            service = MemoryService()
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            adapter = BeamAdapter(service, scope, split="small")

            output = adapter.run_dataset(dataset, split="small", ablate=True)
            report = output["report"]

            self.assertEqual(output["ingest"]["benchmark"], "BEAM")
            self.assertEqual(report["benchmark"], "BEAM")
            self.assertEqual(report["split"], "small")
            self.assertIn("scoring", report)
            self.assertIn("judge_failures", report)
            self.assertIn("information_extraction", report["query_type_mapping"])
            self.assertEqual(report["evidence_pack_trace_coverage"], 1.0)
            self.assertTrue(report["answers"][0]["evidence_pack"]["source_span_ids"])
            self.assertEqual(set(output["ablation"]), {"retrieval_modes"})

    def test_beam_adapter_passes_category_context_to_answer_model(self) -> None:
        class ContextAnswer:
            version = "context-answer"

            def __init__(self) -> None:
                self.calls = []

            def answer_with_context(self, query, pack, *, benchmark=None, category=None, metadata=None):
                self.calls.append({"benchmark": benchmark, "category": category, "metadata": metadata})
                return "Qdrant"

        class AlwaysMatchJudge:
            version = "always-match"

            def score(self, answer, gold_answers):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            dataset = _write_official_beam_fixture(Path(tmp))
            answer_model = ContextAnswer()
            service = MemoryService()
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            adapter = BeamAdapter(service, scope, split="small", answer_model=answer_model, judge_model=AlwaysMatchJudge())
            adapter.ingest_dataset(dataset, split="small")
            query = next(item for item in adapter.build_queries(dataset, split="small") if item.category == "instruction_following")

            adapter.answer_query(query)

        self.assertEqual(answer_model.calls[0]["benchmark"], "BEAM")
        self.assertEqual(answer_model.calls[0]["category"], "instruction_following")
        self.assertEqual(answer_model.calls[0]["metadata"], {})

    def test_beam_adapter_reports_answer_model_failures(self) -> None:
        class FailingAnswer:
            version = "failing-answer"

            def answer_with_context(self, query, pack, *, benchmark=None, category=None, metadata=None):
                raise RuntimeError("LLM endpoint returned HTTP 429: rate limited")

        class NeverCalledJudge:
            version = "never-called"

            def rubric_score(self, query, answer, rubric_item):
                raise AssertionError("judge should not be called when answer generation fails")

        with tempfile.TemporaryDirectory() as tmp:
            dataset = _write_official_beam_fixture(Path(tmp))
            service = MemoryService()
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            adapter = BeamAdapter(service, scope, split="small", answer_model=FailingAnswer(), judge_model=NeverCalledJudge())
            adapter.ingest_dataset(dataset, split="small")
            query = adapter.build_queries(dataset, split="small")[0]

            result = adapter.answer_query(query)
            report = adapter.report([result])

        self.assertTrue(result.answer_failed)
        self.assertEqual(result.score, 0.0)
        self.assertFalse(result.matched_gold)
        self.assertIn("answer generation failed", result.judge_reason)
        self.assertEqual(report["answer_failures"]["count"], 1)
        self.assertEqual(report["judge_failures"]["count"], 0)

    def test_event_ordering_score_aligns_ordinals_and_descriptive_items(self) -> None:
        reference = [
            "1st: Core functionality",
            "2nd: Transaction error handling",
            "3rd: Security and deployment",
        ]
        system = [
            "Core functionality: planning the Flask app and SQLite schema.",
            "Transaction error handling: implementing validation and error handling.",
            "Security and deployment: adding password hashing before deployment.",
        ]

        self.assertEqual(_event_ordering_score(reference, system), 1.0)

    def test_event_ordering_score_does_not_overmatch_short_labels(self) -> None:
        reference = [
            "1st: Core functionality",
            "2nd: Transaction error handling",
            "3rd: Security and deployment",
        ]
        system = [
            "Core functionality",
            "Transaction error handling",
            "Security",
        ]

        self.assertLess(_event_ordering_score(reference, system), 1.0)

    def test_cli_run_beam_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dataset = _write_official_beam_fixture(tmp_path)
            db = tmp_path / "fm.sqlite3"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fusion_memory.cli",
                    "--db",
                    str(db),
                    "--workspace-id",
                    "w",
                    "--user-id",
                    "u",
                    "--agent-id",
                    "a",
                    "run-beam",
                    str(dataset),
                    "--split",
                    "small",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                text=True,
                capture_output=True,
            )
            data = json.loads(proc.stdout)
            self.assertEqual(data["report"]["benchmark"], "BEAM")
            self.assertEqual(data["report"]["split"], "small")
            self.assertIn("accuracy", data["report"])
            self.assertNotIn("retrieval_match_rate", data["report"])


def _write_official_beam_fixture(base: Path) -> Path:
    chat_dir = base / "chats" / "100K" / "1"
    questions_dir = chat_dir / "probing_questions"
    questions_dir.mkdir(parents=True)
    (chat_dir / "chat.json").write_text(
        json.dumps(
            [
                {
                    "batch_number": 1,
                    "turns": [
                        [
                            {
                                "role": "user",
                                "id": 1,
                                "time_anchor": "March-15-2024",
                                "content": "I prefer Qdrant for Atlas retrieval.",
                            },
                            {
                                "role": "assistant",
                                "id": 2,
                                "content": "Noted that Atlas retrieval should use Qdrant.",
                            },
                        ]
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    (questions_dir / "probing_questions.json").write_text(
        json.dumps(
            {
                "information_extraction": [
                    {
                        "question": "What does Atlas retrieval use?",
                        "answer": "Qdrant",
                    }
                ],
                "abstention": [
                    {
                        "question": "What database was never mentioned?",
                        "ideal_response": "The chat does not mention that database.",
                        "rubric": [
                            "LLM response should contain: The chat does not mention that database."
                        ],
                    }
                ],
                "instruction_following": [
                    {
                        "question": "Could you show me how to implement a login feature?",
                        "instruction_being_tested": "Always format all code snippets with syntax highlighting when I ask about implementation details.",
                        "rubric": [
                            "LLM response should contain: code blocks with syntax highlighting"
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return base


if __name__ == "__main__":
    unittest.main()
