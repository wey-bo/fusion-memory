from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tempfile
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fusion_memory import MemoryService, Scope
from fusion_memory.core.config import DEFAULT_EMBEDDING_DIMENSION, DEFAULT_EMBEDDING_MODEL, DEFAULT_RERANKER_MODEL
from fusion_memory.core.embedding import DeterministicEmbedder, HTTPEmbeddingClient, Qwen3EmbeddingClient
from fusion_memory.core.llm import OpenAICompatibleLLMClient
from fusion_memory.core.runtime_config import memory_service_from_env
from fusion_memory.core.models import Candidate, EvidencePack
from fusion_memory.eval.adapter import BenchmarkAdapter, EvalDocument, EvalQuery
from fusion_memory.eval.model_adapters import OpenAICompatibleAnswerModel, OpenAICompatibleJudgeModel
from fusion_memory.ingestion.llm_extractor import StructuredLLMExtractor
from fusion_memory.retrieval.reranker import HTTPReranker, Qwen3Reranker, rerank_candidates


class ModelAdapterTests(unittest.TestCase):
    def test_rerank_candidates_normalizes_large_raw_scores(self) -> None:
        class FixedScaleReranker:
            def score(self, query: str, docs: list[str]) -> list[float]:
                return [-12.0, -3.0, 9.0]

        candidates = [
            Candidate(id="a", type="span", text="a", source="test", scores={"utility_score": 0.60}, source_span_ids=["a"]),
            Candidate(id="b", type="span", text="b", source="test", scores={"utility_score": 0.62}, source_span_ids=["b"]),
            Candidate(id="c", type="span", text="c", source="test", scores={"utility_score": 0.58}, source_span_ids=["c"]),
        ]

        reranked = rerank_candidates("query", candidates, FixedScaleReranker())

        normalized = [candidate.scores["rerank_score_normalized"] for candidate in reranked]
        self.assertTrue(all(0.0 <= value <= 1.0 for value in normalized))
        self.assertGreaterEqual(reranked[0].scores["utility_score"], reranked[-1].scores["utility_score"])

    def test_rubric_score_retries_with_longer_timeout(self) -> None:
        class FlakyJudgeClient:
            def __init__(self) -> None:
                self.timeout_seconds = 15.0
                self.calls: list[float] = []

            def structured(self, prompt, schema, input):
                self.calls.append(self.timeout_seconds)
                if len(self.calls) < 3:
                    raise ValueError("LLM endpoint did not return a structured JSON object")
                return {"score": 1.0, "reason": "retry recovered"}

        judge = OpenAICompatibleJudgeModel(FlakyJudgeClient())
        score, reason = judge.rubric_score("question", "answer", "rubric item")

        self.assertEqual(score, 1.0)
        self.assertEqual(reason, "retry recovered")
        self.assertEqual(judge.client.calls, [15.0, 180.0, 300.0])

    def test_openai_compatible_llm_client_feeds_structured_extractor(self) -> None:
        with FakeModelServer() as server:
            client = OpenAICompatibleLLMClient(server.url("/llm"), model="test-llm")
            extractor = StructuredLLMExtractor(client)
            memory = MemoryService(extractor=extractor)
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            result = memory.add("Please remember reports should use PostgreSQL.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))

            self.assertTrue(result.accepted_fact_ids)
            trace = memory.debug_trace(result.trace_id)
            self.assertIn("structured_llm_extractor", str(trace))
            self.assertTrue(any(call["component"] == "extractor_client" for call in trace["model_calls"]))
            llm_call = next(call for call in trace["model_calls"] if call["component"] == "extractor_client")
            self.assertEqual(llm_call["model"], "test-llm")
            self.assertEqual(llm_call["prompt_version"], "llm-extractor-v0")
            self.assertEqual(llm_call["usage"]["total_tokens"], 42)
            self.assertEqual(server.requests[-1]["path"], "/llm")
            self.assertEqual(server.requests[-1]["json"]["model"], "test-llm")
            self.assertTrue(client.calls)
            self.assertIn("latency_ms", client.calls[0])

    def test_openai_compatible_llm_client_retries_http_429(self) -> None:
        with FakeModelServer() as server:
            client = OpenAICompatibleLLMClient(
                server.url("/rate-limit-once"),
                model="test-llm",
                retry_attempts=2,
                retry_backoff_seconds=0.0,
                min_interval_seconds=0.0,
            )

            response = client.structured(
                prompt="answer",
                schema={"type": "object"},
                input={"evidence_pack": {"source_spans": [{"content": "Atlas uses Qdrant."}]}},
            )

            self.assertEqual(response["answer"], "Qdrant")
            self.assertEqual([request["path"] for request in server.requests].count("/rate-limit-once"), 2)
            self.assertEqual(client.calls[0]["attempts"], 2)

    def test_http_embedding_client_can_back_store_embeddings(self) -> None:
        with FakeModelServer() as server:
            embedder = HTTPEmbeddingClient(server.url("/embed"), model="test-embed")
            memory = MemoryService(embedder=embedder)
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            memory.add("I prefer Qdrant for Atlas retrieval.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))
            result = memory.search("Atlas Qdrant", scope)

            self.assertTrue(result.candidates)
            trace = memory.debug_trace(result.trace_id)
            self.assertTrue(any(call["component"] == "embedder" and call["model"] == "test-embed" for call in trace["model_calls"]))
            audit = memory.audit_events(scope, event_type="memory.search")
            self.assertGreater(audit[0]["payload"]["model_calls"]["count"], 0)
            self.assertTrue(embedder.calls)
            self.assertTrue(any(request["path"] == "/embed" for request in server.requests))

    def test_http_reranker_is_used_in_balanced_mode(self) -> None:
        with FakeModelServer() as server:
            reranker = HTTPReranker(server.url("/rerank"), model="test-rerank")
            memory = MemoryService(reranker=reranker)
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            memory.add("I tested BM25 yesterday.", scope, datetime(2026, 6, 3, tzinfo=timezone.utc))
            memory.add("I added dense retrieval today.", scope, datetime(2026, 6, 5, tzinfo=timezone.utc))
            result = memory.search("dense retrieval", scope, options={"mode": "balanced"})

            trace = memory.debug_trace(result.trace_id)
            self.assertEqual(trace["rerank"]["model_version"], "http-reranker:test-rerank")
            self.assertTrue(any(call["component"] == "reranker" and call["model"] == "test-rerank" for call in trace["model_calls"]))
            self.assertTrue(reranker.calls)
            self.assertTrue(any(request["path"] == "/rerank" for request in server.requests))

    def test_runtime_config_wires_http_model_adapters_from_env(self) -> None:
        with FakeModelServer() as server:
            env = {
                "FUSION_MEMORY_EMBEDDING_PROVIDER": "http",
                "FUSION_MEMORY_EMBEDDING_ENDPOINT": server.url("/embed"),
                "FUSION_MEMORY_EMBEDDING_MODEL": "env-embed",
                "FUSION_MEMORY_RERANKER_PROVIDER": "http",
                "FUSION_MEMORY_RERANKER_ENDPOINT": server.url("/rerank"),
                "FUSION_MEMORY_RERANKER_MODEL": "env-rerank",
                "FUSION_MEMORY_EXTRACTOR_ENDPOINT": server.url("/llm"),
                "FUSION_MEMORY_EXTRACTOR_MODEL": "env-extractor",
            }
            with patch.dict(os.environ, env, clear=True):
                memory = memory_service_from_env(":memory:")
                scope = Scope(workspace_id="w", user_id="u", agent_id="a")
                memory.add("Please remember reports should use PostgreSQL.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))
                result = memory.search("PostgreSQL reports", scope, options={"mode": "balanced"})
                memory.close()

            self.assertTrue(result.candidates)
            paths = [request["path"] for request in server.requests]
            self.assertIn("/llm", paths)
            self.assertIn("/embed", paths)
            self.assertIn("/rerank", paths)
            self.assertTrue(any(request["json"].get("model") == "env-embed" for request in server.requests))
            self.assertTrue(any(request["json"].get("model") == "env-rerank" for request in server.requests))
            self.assertTrue(any(request["json"].get("model") == "env-extractor" for request in server.requests))

    def test_runtime_config_accepts_extractor_base_url(self) -> None:
        with FakeModelServer() as server:
            env = {
                "FUSION_MEMORY_EXTRACTOR_BASE_URL": server.url(""),
                "FUSION_MEMORY_EXTRACTOR_MODEL": "env-extractor",
            }
            with patch.dict(os.environ, env, clear=True):
                memory = memory_service_from_env(":memory:")
                scope = Scope(workspace_id="w", user_id="u", agent_id="a")
                memory.add("Please remember reports should use PostgreSQL.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc))
                memory.close()

            self.assertTrue(any(request["path"] == "/chat/completions" for request in server.requests))

    def test_qwen_defaults_are_configured_without_required_runtime_dependency(self) -> None:
        self.assertEqual(len(DeterministicEmbedder().embed_text("Atlas")), DEFAULT_EMBEDDING_DIMENSION)
        self.assertEqual(DEFAULT_EMBEDDING_MODEL, "Qwen/Qwen3-Embedding-0.6B")
        self.assertEqual(DEFAULT_RERANKER_MODEL, "Qwen/Qwen3-Reranker-0.6B")
        real_import = __import__

        def block_sentence_transformers(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=block_sentence_transformers):
            with self.assertRaisesRegex(RuntimeError, "Qwen3EmbeddingClient requires optional ML dependencies"):
                Qwen3EmbeddingClient()
            with self.assertRaisesRegex(RuntimeError, "Qwen3Reranker requires optional ML dependencies"):
                Qwen3Reranker()

    def test_eval_answer_and_judge_models_are_pluggable(self) -> None:
        with FakeModelServer() as server:
            answer_client = OpenAICompatibleLLMClient(server.url("/answer"), model="answer-model")
            judge_client = OpenAICompatibleLLMClient(server.url("/judge"), model="judge-model")
            service = MemoryService()
            scope = Scope(workspace_id="w", user_id="u", agent_id="a")
            adapter = BenchmarkAdapter(
                service,
                scope,
                answer_model=OpenAICompatibleAnswerModel(answer_client),
                judge_model=OpenAICompatibleJudgeModel(judge_client),
            )
            adapter.ingest_documents(
                [EvalDocument(id="doc1", content="Atlas retrieval uses Qdrant.", timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc))]
            )

            results = adapter.run_queries([EvalQuery(id="q1", query="What does Atlas retrieval use?", gold_answers=["Qdrant"])])
            report = adapter.report(results)

            self.assertEqual(results[0].answer, "Qdrant")
            self.assertTrue(results[0].matched_gold)
            self.assertEqual(results[0].llm_calls, 2)
            self.assertIn("answer-model", results[0].answer_model)
            self.assertIn("judge-model", results[0].judge_model)
            self.assertEqual(report["llm_calls_query"], 2.0)
            self.assertTrue(any(request["path"] == "/answer" for request in server.requests))
            self.assertTrue(any(request["path"] == "/judge" for request in server.requests))

    def test_eval_answer_model_adds_beam_category_instructions(self) -> None:
        pack = EvidencePack(
            query="Could you show me how to implement a login feature?",
            answer_policy="answer_with_evidence_or_abstain",
            current_views=[],
            entity_profiles=[],
            facts=[],
            events=[],
            source_spans=[{"id": "s1", "content": "Always format code snippets with syntax highlighting."}],
            conflicts=[],
            coverage={"query_type": "instruction"},
            debug_trace=[],
        )
        with FakeModelServer() as server:
            client = OpenAICompatibleLLMClient(server.url("/answer"), model="answer-model")
            answer = OpenAICompatibleAnswerModel(client).answer_with_context(
                pack.query,
                pack,
                benchmark="BEAM",
                category="instruction_following",
                metadata={"rubric": ["code blocks with syntax highlighting"]},
            )

        self.assertEqual(answer, "Qdrant")
        request_input = _decode_model_payload(server.requests[-1]["json"])["input"]
        self.assertNotIn("benchmark", request_input)
        self.assertNotIn("category", request_input)
        self.assertNotIn("rubric", request_input)
        self.assertIn("fenced code blocks", request_input["instruction"])

    def test_eval_answer_model_surfaces_client_failure(self) -> None:
        class FailingClient:
            version = "failing"

            def structured(self, prompt, schema, input):
                raise RuntimeError("LLM endpoint returned HTTP 429: rate limited")

        pack = EvidencePack(
            query="Question",
            answer_policy="answer_with_evidence_or_abstain",
            current_views=[],
            entity_profiles=[],
            facts=[],
            events=[],
            source_spans=[{"id": "s1", "content": "Evidence."}],
            conflicts=[],
            coverage={},
            debug_trace=[],
        )

        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            OpenAICompatibleAnswerModel(FailingClient()).answer_with_context("Question", pack)

    def test_eval_answer_model_does_not_send_beam_gold_metadata(self) -> None:
        pack = EvidencePack(
            query="What happened first?",
            answer_policy="answer_with_evidence_or_abstain",
            current_views=[],
            entity_profiles=[],
            facts=[],
            events=[
                {
                    "id": "e1",
                    "timeline_index": 1,
                    "time_start": "2026-06-01T10:00:00+00:00",
                    "description": "Core functionality was planned.",
                    "event_type": "milestone",
                    "milestone_group": "core_functionality",
                    "source_span_ids": ["s1"],
                }
            ],
            source_spans=[
                {
                    "id": "s1",
                    "timeline_index": 1,
                    "timestamp": "2026-06-01T10:00:00+00:00",
                    "content": "The team planned core functionality before deployment.",
                }
            ],
            conflicts=[],
            coverage={"query_type": "event_ordering"},
            debug_trace=[],
        )
        gold_metadata = {
            "rubric": ["LLM response should contain: Core functionality"],
            "ideal_response": "Core functionality came first.",
            "source_chat_ids": ["chat-1"],
            "ordering_tested": ["1st: Core functionality", "2nd: Security and deployment"],
            "conversation_references": ["hidden-source"],
        }
        with FakeModelServer() as server:
            client = OpenAICompatibleLLMClient(server.url("/answer"), model="answer-model")
            OpenAICompatibleAnswerModel(client).answer_with_context(
                pack.query,
                pack,
                benchmark="BEAM",
                category="event_ordering",
                metadata=gold_metadata,
            )

        request_input = _decode_model_payload(server.requests[-1]["json"])["input"]
        serialized_input = json.dumps(request_input)
        self.assertIn("timeline_index", request_input["instruction"])
        self.assertIn("conversation chronology", request_input["instruction"])
        self.assertIn("timeline", request_input["evidence_pack"])
        self.assertNotIn("source_spans", request_input["evidence_pack"])
        self.assertNotIn("events", request_input["evidence_pack"])
        timeline = request_input["evidence_pack"]["timeline"]
        self.assertEqual(timeline[0]["timeline_index"], 1)
        self.assertEqual(timeline[0]["kind"], "event")
        self.assertEqual(timeline[0]["milestone_group"], "core_functionality")
        self.assertEqual(timeline[0]["source_span_ids"], ["s1"])
        self.assertNotIn("time_start", serialized_input)
        self.assertNotIn("timestamp", serialized_input)
        for key in gold_metadata:
            self.assertNotIn(key, request_input)
            self.assertNotIn(key, serialized_input)

    def test_eval_answer_model_sends_temporal_role_annotations(self) -> None:
        pack = EvidencePack(
            query="How many weeks are between completion and deployment?",
            answer_policy="answer_with_evidence_or_abstain",
            current_views=[],
            entity_profiles=[],
            facts=[],
            events=[],
            source_spans=[
                {
                    "id": "s1",
                    "content": "Features complete on January 15, 2024.",
                    "temporal_mentions": [
                        {
                            "text": "January 15, 2024",
                            "normalized_date": "2024-01-15",
                            "role": "feature_finish_date",
                            "role_confidence": 0.88,
                            "context": "Features complete on January 15, 2024.",
                        }
                    ],
                    "temporal_roles": ["feature_finish_date"],
                }
            ],
            conflicts=[],
            coverage={"query_type": "temporal_lookup"},
            debug_trace=[],
        )
        with FakeModelServer() as server:
            client = OpenAICompatibleLLMClient(server.url("/answer"), model="answer-model")
            OpenAICompatibleAnswerModel(client).answer_with_context(
                pack.query,
                pack,
                benchmark="BEAM",
                category="temporal_reasoning",
            )

        request_input = _decode_model_payload(server.requests[-1]["json"])["input"]
        span = request_input["evidence_pack"]["source_spans"][0]
        self.assertEqual(span["temporal_roles"], ["feature_finish_date"])
        self.assertEqual(span["temporal_mentions"][0]["normalized_date"], "2024-01-15")

    def test_eval_answer_model_adds_strict_abstention_and_contradiction_instructions(self) -> None:
        pack = EvidencePack(
            query="Question",
            answer_policy="answer_with_evidence_or_abstain",
            current_views=[],
            entity_profiles=[],
            facts=[],
            events=[],
            source_spans=[{"id": "s1", "content": "Evidence."}],
            conflicts=[],
            coverage={},
            debug_trace=[],
        )
        with FakeModelServer() as server:
            client = OpenAICompatibleLLMClient(server.url("/answer"), model="answer-model")
            model = OpenAICompatibleAnswerModel(client)
            model.answer_with_context("Missing?", pack, benchmark="BEAM", category="abstention")
            model.answer_with_context("Contradiction?", pack, benchmark="BEAM", category="contradiction_resolution")

        abstention_input = _decode_model_payload(server.requests[-2]["json"])["input"]
        contradiction_input = _decode_model_payload(server.requests[-1]["json"])["input"]
        self.assertIn("does not contain that information", abstention_input["instruction"])
        self.assertIn("contradictory claims", contradiction_input["instruction"])

    def test_cli_benchmark_accepts_eval_model_endpoints(self) -> None:
        with FakeModelServer() as server, tempfile.TemporaryDirectory() as tmp:
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
                    "--answer-endpoint",
                    server.url("/answer"),
                    "--answer-model",
                    "answer-model",
                    "--judge-endpoint",
                    server.url("/judge"),
                    "--judge-model",
                    "judge-model",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
                text=True,
                capture_output=True,
            )
            data = json.loads(proc.stdout)
            self.assertGreaterEqual(data["report"]["accuracy"], 1.0)
            self.assertEqual(data["report"]["llm_calls_query"], 2.0)
            self.assertIn("answer-model", data["report"]["answer_model"])
            self.assertIn("judge-model", data["report"]["judge_model"])

    def test_cli_benchmark_accepts_eval_model_env(self) -> None:
        with FakeModelServer() as server, tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dataset = _write_official_beam_fixture(tmp_path)
            db = tmp_path / "fm.sqlite3"
            env = {
                **{key: value for key, value in os.environ.items() if not key.startswith("FUSION_MEMORY_")},
                "FUSION_MEMORY_EVAL_BASE_URL": server.url(""),
                "FUSION_MEMORY_EVAL_ANSWER_MODEL": "env-answer-model",
                "FUSION_MEMORY_EVAL_JUDGE_MODEL": "env-judge-model",
            }
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
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )
            data = json.loads(proc.stdout)
            self.assertGreaterEqual(data["report"]["accuracy"], 1.0)
            self.assertEqual(data["report"]["llm_calls_query"], 2.0)
            self.assertIn("env-answer-model", data["report"]["answer_model"])
            self.assertIn("env-judge-model", data["report"]["judge_model"])
            self.assertTrue(all(request["path"] == "/chat/completions" for request in server.requests if "json" in request))


class FakeModelServer:
    def __enter__(self) -> "FakeModelServer":
        self.requests: list[dict[str, Any]] = []
        requests_ref = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler_self) -> None:
                length = int(handler_self.headers.get("Content-Length", "0"))
                payload = json.loads(handler_self.rfile.read(length).decode("utf-8"))
                requests_ref.append({"path": handler_self.path, "json": payload})
                if handler_self.path == "/rate-limit-once" and sum(1 for request in requests_ref if request["path"] == "/rate-limit-once") == 1:
                    body = json.dumps({"error": "rate limited"}).encode("utf-8")
                    handler_self.send_response(429)
                    handler_self.send_header("Content-Type", "application/json")
                    handler_self.send_header("Retry-After", "0")
                    handler_self.send_header("Content-Length", str(len(body)))
                    handler_self.end_headers()
                    handler_self.wfile.write(body)
                    return
                if handler_self.path in {"/llm", "/chat/completions", "/rate-limit-once"}:
                    data = _decode_model_payload(payload)
                    request_input = data["input"]
                    if "evidence_pack" in request_input:
                        response = {
                            "choices": [{"message": {"content": json.dumps({"answer": "Qdrant"})}}],
                            "usage": {"total_tokens": 7},
                        }
                    elif "rubric_item" in request_input:
                        rubric_item = str(request_input["rubric_item"]).lower()
                        answer = str(request_input.get("candidate_answer", "")).lower()
                        score = 1.0 if "qdrant" in rubric_item and "qdrant" in answer else 0.0
                        response = {
                            "choices": [{"message": {"content": json.dumps({"score": score, "reason": "fixture rubric score"})}}],
                            "usage": {"total_tokens": 5},
                        }
                    elif "candidate_answer" in request_input:
                        answer = request_input["candidate_answer"]
                        response = {
                            "choices": [{"message": {"content": json.dumps({"matched": "qdrant" in answer.lower()})}}],
                            "usage": {"total_tokens": 5},
                        }
                    else:
                        source_id = request_input["spans"][0]["span_id"]
                        response = {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            {
                                                "facts": [
                                                    {
                                                        "text": "User prefers PostgreSQL for reports.",
                                                        "subject": "user",
                                                        "predicate": "prefers",
                                                        "object": "PostgreSQL for reports",
                                                        "category": "preference",
                                                        "confidence": 0.91,
                                                        "salience": 0.84,
                                                        "source_span_ids": [source_id],
                                                    }
                                                ]
                                            }
                                        )
                                    }
                                }
                            ],
                            "usage": {"total_tokens": 42},
                        }
                elif handler_self.path == "/embed":
                    texts = payload["input"]
                    response = {"embeddings": [_embedding(text) for text in texts], "usage": {"total_tokens": len(texts)}}
                elif handler_self.path == "/rerank":
                    docs = payload["documents"]
                    response = {"scores": [float(index + 1) for index, _ in enumerate(docs)]}
                elif handler_self.path == "/answer":
                    response = {
                        "choices": [{"message": {"content": json.dumps({"answer": "Qdrant"})}}],
                        "usage": {"total_tokens": 7},
                    }
                elif handler_self.path == "/judge":
                    data = _decode_model_payload(payload)
                    request_input = data["input"]
                    if "rubric_item" in request_input:
                        rubric_item = str(request_input["rubric_item"]).lower()
                        answer = str(request_input.get("candidate_answer", "")).lower()
                        score = 1.0 if "qdrant" in rubric_item and "qdrant" in answer else 0.0
                        response = {
                            "choices": [{"message": {"content": json.dumps({"score": score, "reason": "fixture rubric score"})}}],
                            "usage": {"total_tokens": 5},
                        }
                    else:
                        answer = request_input["candidate_answer"]
                        response = {
                            "choices": [{"message": {"content": json.dumps({"matched": "qdrant" in answer.lower()})}}],
                            "usage": {"total_tokens": 5},
                        }
                else:
                    response = {"error": "unknown path"}
                body = json.dumps(response).encode("utf-8")
                handler_self.send_response(200)
                handler_self.send_header("Content-Type", "application/json")
                handler_self.send_header("Content-Length", str(len(body)))
                handler_self.end_headers()
                handler_self.wfile.write(body)

            def log_message(self, format: str, *args) -> None:
                return None

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def url(self, path: str) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}{path}"


def _embedding(text: str) -> list[float]:
    qdrant = 1.0 if "qdrant" in text.lower() else 0.0
    atlas = 1.0 if "atlas" in text.lower() else 0.0
    length = min(1.0, len(text.split()) / 20)
    return [qdrant, atlas, length]


def _decode_model_payload(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload["messages"][-1]["content"]
    if isinstance(content, str) and "\n" in content:
        content = content.split("\n", 1)[1]
    return json.loads(content)


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
                                "content": "Atlas retrieval uses Qdrant.",
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
                        "question": "What does Atlas retrieval use?",
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
