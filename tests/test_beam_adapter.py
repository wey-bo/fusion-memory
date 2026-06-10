from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fusion_memory import MemoryService, Scope
from fusion_memory.eval.beam_adapter import BeamAdapter


class BeamAdapterTests(unittest.TestCase):
    def test_beam_adapter_loads_official_chat_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset = _write_official_beam_fixture(Path(tmp))
            adapter = BeamAdapter(MemoryService(), Scope(workspace_id="w", user_id="u", agent_id="a"), split="small")

            ingest = adapter.ingest_dataset(dataset, split="small")
            queries = adapter.build_queries(dataset, split="small")

            self.assertEqual(ingest["documents"], 2)
            self.assertEqual(len(queries), 2)
            self.assertEqual(queries[0].category, "information_extraction")
            self.assertIn("Qdrant", queries[0].gold_answers[0])

    def test_beam_adapter_runs_split_and_records_answer_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset = _write_beam_fixture(Path(tmp), split="small")
            service = MemoryService()
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            adapter = BeamAdapter(service, scope, split="small")

            output = adapter.run_dataset(dataset, split="small", ablate=True)
            report = output["report"]

            self.assertEqual(output["ingest"]["benchmark"], "BEAM")
            self.assertEqual(report["benchmark"], "BEAM")
            self.assertEqual(report["split"], "small")
            self.assertEqual(report["answer_match_rate"], 1.0)
            self.assertIn("factual_exact", report["query_type_mapping"])
            self.assertEqual(report["evidence_pack_trace_coverage"], 1.0)
            self.assertTrue(report["answers"][0]["evidence_pack"]["source_span_ids"])
            self.assertEqual(set(output["ablation"]), {"retrieval_modes", "components"})

    def test_cli_run_beam_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dataset = _write_beam_fixture(tmp_path, split="dev")
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
                    "dev",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                text=True,
                capture_output=True,
            )
            data = json.loads(proc.stdout)
            self.assertEqual(data["report"]["benchmark"], "BEAM")
            self.assertEqual(data["report"]["split"], "dev")
            self.assertEqual(data["report"]["retrieval_match_rate"], 1.0)


def _write_beam_fixture(base: Path, split: str) -> Path:
    split_dir = base / split
    split_dir.mkdir(parents=True)
    (split_dir / "documents.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"id": "doc1", "content": "User said Atlas retrieval now uses Qdrant.", "speaker": "user"}),
                json.dumps({"id": "doc2", "content": "Atlas retrieval backend is Qdrant in the BEAM fixture.", "speaker": "user"}),
            ]
        ),
        encoding="utf-8",
    )
    (split_dir / "queries.jsonl").write_text(
        json.dumps(
            {
                "id": "q1",
                "query": "What does Atlas retrieval use?",
                "gold_answers": ["Qdrant"],
                "category": "factual_exact",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return base


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
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return base


if __name__ == "__main__":
    unittest.main()
