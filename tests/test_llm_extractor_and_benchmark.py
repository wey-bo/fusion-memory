from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fusion_memory import MemoryService, Scope
from fusion_memory.eval.adapter import BenchmarkAdapter, load_dataset
from fusion_memory.ingestion.llm_extractor import StructuredLLMExtractor


class LLMExtractorAndBenchmarkTests(unittest.TestCase):
    def test_structured_llm_extractor_can_be_injected(self) -> None:
        class EchoSourceClient:
            def __init__(self) -> None:
                self.calls = []

            def structured(self, prompt, schema, input):
                self.calls.append({"prompt": prompt, "schema": schema, "input": input})
                span_id = input["spans"][0]["span_id"]
                return {
                    "facts": [
                        {
                            "local_id": "f0",
                            "text": "User prefers PostgreSQL for reports.",
                            "subject": "user",
                            "predicate": "prefers",
                            "object": "PostgreSQL for reports",
                            "category": "preference",
                            "confidence": 0.91,
                            "salience": 0.84,
                            "source_span_ids": [span_id],
                        }
                    ]
                }

        client = EchoSourceClient()
        extractor = StructuredLLMExtractor(client)
        memory = MemoryService(extractor=extractor)
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        result = memory.add("Please remember reports should use PostgreSQL.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertTrue(result.accepted_fact_ids)
        trace = memory.debug_trace(result.trace_id)
        self.assertIn("structured_llm_extractor", str(trace))
        self.assertTrue(client.calls)

    def test_structured_llm_extractor_rejects_string_fact_by_default(self) -> None:
        class StringFactClient:
            def structured(self, prompt, schema, input):
                return {"facts": ["User prefers Qdrant for Atlas retrieval."]}

        extractor = StructuredLLMExtractor(StringFactClient())
        memory = MemoryService(extractor=extractor)
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        result = memory.add("Please remember I prefer Qdrant for Atlas retrieval.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))

        self.assertFalse(result.accepted_fact_ids)
        trace = memory.debug_trace(result.trace_id)
        telemetry = next(step for step in trace["steps"] if step["step"] == "extractor_telemetry")
        self.assertEqual(telemetry["invalid_fact_count"], 1)
        self.assertFalse(telemetry["fallback_used"])

    def test_structured_llm_extractor_legacy_mode_accepts_string_fact_for_single_span(self) -> None:
        class StringFactClient:
            def structured(self, prompt, schema, input):
                return {"facts": ["User prefers Qdrant for Atlas retrieval."]}

        extractor = StructuredLLMExtractor(StringFactClient(), strict=False, allow_legacy_strings=True)
        memory = MemoryService(extractor=extractor)
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        result = memory.add("Please remember I prefer Qdrant for Atlas retrieval.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))

        self.assertTrue(result.accepted_fact_ids)
        fact = memory.get(result.accepted_fact_ids[0], "fact")
        self.assertEqual(fact.source_span_ids, result.span_ids)
        trace = memory.debug_trace(result.trace_id)
        telemetry = next(step for step in trace["steps"] if step["step"] == "extractor_telemetry")
        self.assertFalse(telemetry["strict"])
        self.assertTrue(telemetry["allow_legacy_strings"])

    def test_structured_llm_extractor_drops_unattributed_fact_with_telemetry(self) -> None:
        class UnattributedClient:
            def structured(self, prompt, schema, input):
                return {
                    "facts": [
                        {
                            "text": "User prefers Qdrant for Atlas retrieval.",
                            "subject": "user",
                            "predicate": "prefers",
                            "object": "Qdrant",
                            "category": "preference",
                            "confidence": 0.9,
                            "source_span_ids": ["not-a-real-span"],
                        }
                    ],
                    "events": [],
                    "relations": [],
                }

        extractor = StructuredLLMExtractor(UnattributedClient())
        memory = MemoryService(extractor=extractor)
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        result = memory.add("Please remember I prefer Qdrant for Atlas retrieval.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))

        self.assertFalse(result.accepted_fact_ids)
        trace = memory.debug_trace(result.trace_id)
        telemetry = next(step for step in trace["steps"] if step["step"] == "extractor_telemetry")
        self.assertEqual(telemetry["invalid_fact_count"], 1)
        self.assertEqual(telemetry["accepted_fact_count"], 0)

    def test_structured_llm_extractor_records_rule_fallback_telemetry_on_failure(self) -> None:
        class FailingClient:
            def structured(self, prompt, schema, input):
                raise RuntimeError("extractor unavailable")

        extractor = StructuredLLMExtractor(FailingClient())
        memory = MemoryService(extractor=extractor)
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        result = memory.add("Please remember I prefer Qdrant for Atlas retrieval.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))

        self.assertTrue(result.accepted_fact_ids)
        trace = memory.debug_trace(result.trace_id)
        telemetry = next(step for step in trace["steps"] if step["step"] == "extractor_telemetry")
        self.assertTrue(telemetry["llm_call_failed"])
        self.assertTrue(telemetry["fallback_used"])
        self.assertEqual(telemetry["fallback_reason"], "RuntimeError")
        self.assertGreater(telemetry["fallback_candidate_count"], 0)

    def test_structured_llm_extractor_uses_rule_event_fallback(self) -> None:
        class FactOnlyClient:
            def structured(self, prompt, schema, input):
                span_id = input["spans"][0]["span_id"]
                return {
                    "facts": [
                        {
                            "text": "User planned Core functionality.",
                            "subject": "user",
                            "predicate": "planned",
                            "object": "Core functionality",
                            "category": "project_state",
                            "confidence": 0.91,
                            "salience": 0.84,
                            "source_span_ids": [span_id],
                        }
                    ],
                    "events": [],
                }

        extractor = StructuredLLMExtractor(FactOnlyClient())
        memory = MemoryService(extractor=extractor)
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        result = memory.add("I planned Core functionality for the tracker.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))

        self.assertTrue(result.accepted_event_ids)
        event = memory.store.list_events(scope)[0]
        self.assertIn(event.event_type, {"plan_step", "milestone"})
        self.assertIn("Core functionality", event.description)

    def test_dataset_loader_and_benchmark_report_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dataset.json"
            path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "id": "doc1",
                                "content": "User said Atlas now uses Qdrant.",
                                "timestamp": "2026-06-01T00:00:00+00:00",
                                "speaker": "user",
                            }
                        ],
                        "queries": [
                            {
                                "id": "q1",
                                "query": "What does Atlas use?",
                                "gold_answers": ["Qdrant"],
                                "category": "factual_exact",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            docs, queries = load_dataset(path)
            self.assertEqual(len(docs), 1)
            self.assertEqual(len(queries), 1)
            memory = MemoryService()
            adapter = BenchmarkAdapter(memory, Scope(workspace_id="w", user_id="u", agent_id="a"))
            ingest = adapter.ingest_dataset(path)
            results = adapter.run_queries(adapter.build_queries(path))
            report = adapter.report(results)
            self.assertEqual(ingest["documents"], 1)
            self.assertEqual(report["retrieval_match_rate"], 1.0)
            self.assertIn("factual_exact", report["by_category"])

    def test_cli_run_benchmark_smoke(self) -> None:
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
            self.assertIn("accuracy", data["report"])
            self.assertEqual(data["report"]["split"], "small")


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
                                "content": "User prefers Qdrant for Atlas.",
                            }
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
                        "question": "What does user prefer for Atlas?",
                        "answer": "Qdrant",
                        "rubric": ["LLM response should contain: Qdrant"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return base


if __name__ == "__main__":
    unittest.main()
