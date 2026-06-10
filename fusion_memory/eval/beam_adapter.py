from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fusion_memory import MemoryService, Scope
from fusion_memory.eval.adapter import BenchmarkAdapter, EvalDocument, EvalQuery, EvalResult


BEAM_SPLITS = {"small", "dev", "100k", "500k", "1m", "10m"}
BEAM_SPLIT_DIRS = {
    "small": "100K",
    "dev": "100K",
    "100k": "100K",
    "500k": "500K",
    "1m": "1M",
    "10m": "10M",
}


class BeamAdapter(BenchmarkAdapter):
    """BEAM-oriented harness built on the generic local benchmark adapter.

    The loader accepts the same JSON/JSONL shapes as `BenchmarkAdapter`, but
    requires a BEAM split label so reports can be compared and replayed by split.
    Production answer/judge models can be injected through the base adapter
    constructor.
    """

    benchmark = "BEAM"

    def __init__(self, service: MemoryService, scope: Scope, split: str = "small", answer_model: Any | None = None, judge_model: Any | None = None) -> None:
        validate_beam_split(split)
        super().__init__(service, scope, answer_model=answer_model, judge_model=judge_model)
        self.split = split

    def ingest_dataset(self, dataset_path: str | Path, split: str | None = None) -> dict[str, Any]:
        effective_split = self._effective_split(split)
        official = _load_official_beam_dataset(dataset_path, effective_split)
        if official:
            documents, _ = official
            span_ids = self.ingest_documents(documents)
            report = {"documents": len(documents), "span_ids": span_ids, "span_count": len(span_ids), "split": effective_split}
        else:
            report = super().ingest_dataset(dataset_path, split=effective_split)
        return {"benchmark": self.benchmark, **report, "split": effective_split}

    def build_queries(self, dataset_path: str | Path, split: str | None = None) -> list[EvalQuery]:
        effective_split = self._effective_split(split)
        official = _load_official_beam_dataset(dataset_path, effective_split)
        if official:
            _, queries = official
            return queries
        return super().build_queries(dataset_path, split=effective_split)

    def run_queries(self, queries: list[EvalQuery], budget: dict[str, Any] | None = None) -> list[EvalResult]:
        effective_budget = {"mode": "benchmark", **(budget or {})}
        return super().run_queries(queries, budget=effective_budget)

    def report(self, results: list[EvalResult]) -> dict[str, Any]:
        base = super().report(results)
        return {
            "benchmark": self.benchmark,
            "split": self.split,
            **base,
            "query_type_mapping": _query_type_mapping(results),
            "evidence_pack_trace_coverage": _evidence_pack_trace_coverage(results),
            "answers": [_answer_record(result) for result in results],
        }

    def run_dataset(self, dataset_path: str | Path, *, split: str | None = None, ablate: bool = False) -> dict[str, Any]:
        effective_split = self._effective_split(split)
        ingest = self.ingest_dataset(dataset_path, split=effective_split)
        queries = self.build_queries(dataset_path, split=effective_split)
        results = self.run_queries(queries)
        output: dict[str, Any] = {"ingest": ingest, "report": self.report(results)}
        if ablate:
            output["ablation"] = {
                "retrieval_modes": self.run_ablation(queries),
                "components": self.run_component_ablation(queries),
            }
        return output

    def _effective_split(self, split: str | None) -> str:
        effective_split = split or self.split
        validate_beam_split(effective_split)
        self.split = effective_split
        return effective_split


def validate_beam_split(split: str) -> None:
    if split not in BEAM_SPLITS:
        raise ValueError(f"unsupported BEAM split {split!r}; expected one of {sorted(BEAM_SPLITS)}")


def _load_official_beam_dataset(dataset_path: str | Path, split: str) -> tuple[list[EvalDocument], list[EvalQuery]] | None:
    root = Path(dataset_path)
    chats_root = root / "chats"
    if not chats_root.is_dir():
        return None
    split_dir = chats_root / BEAM_SPLIT_DIRS[split]
    if not split_dir.is_dir():
        return None
    chat_dirs = _chat_dirs(split_dir)
    if split == "small":
        chat_dirs = chat_dirs[:1]
    elif split == "dev":
        chat_dirs = chat_dirs[:3]
    documents: list[EvalDocument] = []
    queries: list[EvalQuery] = []
    for chat_dir in chat_dirs:
        documents.extend(_load_chat_documents(chat_dir, split))
        queries.extend(_load_chat_queries(chat_dir, split))
    return documents, queries


def _chat_dirs(split_dir: Path) -> list[Path]:
    return sorted(
        [path for path in split_dir.iterdir() if path.is_dir() and path.name.isdigit()],
        key=lambda path: int(path.name),
    )


def _load_chat_documents(chat_dir: Path, split: str) -> list[EvalDocument]:
    chat_path = chat_dir / "chat.json"
    if not chat_path.exists():
        return []
    data = json.loads(chat_path.read_text(encoding="utf-8"))
    documents: list[EvalDocument] = []
    for batch_index, batch in enumerate(data if isinstance(data, list) else []):
        batch_number = batch.get("batch_number", batch_index + 1) if isinstance(batch, dict) else batch_index + 1
        turns = batch.get("turns", []) if isinstance(batch, dict) else []
        for turn_index, turn in enumerate(turns):
            messages = turn if isinstance(turn, list) else [turn]
            for message_index, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                content = str(message.get("content") or "").strip()
                if not content:
                    continue
                message_id = message.get("id", f"{batch_number}-{turn_index}-{message_index}")
                documents.append(
                    EvalDocument(
                        id=f"beam:{split}:{chat_dir.name}:batch{batch_number}:msg{message_id}",
                        content=content,
                        timestamp=_parse_beam_time(message.get("time_anchor")),
                        speaker=str(message.get("role") or "document"),
                    )
                )
    return documents


def _load_chat_queries(chat_dir: Path, split: str) -> list[EvalQuery]:
    questions_path = chat_dir / "probing_questions" / "probing_questions.json"
    if not questions_path.exists():
        return []
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    queries: list[EvalQuery] = []
    if not isinstance(data, dict):
        return queries
    for category, items in data.items():
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            gold = item.get("gold_answers") or item.get("answers") or item.get("answer") or item.get("ideal_answer") or item.get("ideal_response") or []
            if isinstance(gold, str):
                gold_answers = [gold]
            elif isinstance(gold, list):
                gold_answers = [str(answer) for answer in gold if str(answer).strip()]
            else:
                gold_answers = [str(gold)]
            queries.append(
                EvalQuery(
                    id=f"beam:{split}:{chat_dir.name}:{category}:{index}",
                    query=question,
                    gold_answers=gold_answers,
                    category=str(category),
                )
            )
    return queries


def _parse_beam_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    for fmt in ("%B-%d-%Y", "%b-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _query_type_mapping(results: list[EvalResult]) -> dict[str, Any]:
    by_category: dict[str, dict[str, Any]] = {}
    for result in results:
        category = result.category or "uncategorized"
        entry = by_category.setdefault(category, {"query_type": result.query_type, "count": 0, "query_ids": []})
        entry["count"] += 1
        entry["query_ids"].append(result.query_id)
    return by_category


def _evidence_pack_trace_coverage(results: list[EvalResult]) -> float:
    if not results:
        return 0.0
    traced = sum(1 for result in results if result.evidence_pack and "source_span_ids" in result.evidence_pack)
    return traced / len(results)


def _answer_record(result: EvalResult) -> dict[str, Any]:
    return {
        "query_id": result.query_id,
        "query_text": result.query_text,
        "category": result.category,
        "query_type": result.query_type,
        "answer": result.answer,
        "answer_policy": result.answer_policy,
        "matched_gold": result.matched_gold,
        "evidence_matched_gold": result.evidence_matched_gold,
        "retrieved_source_span_ids": result.retrieved_source_span_ids,
        "evidence_pack": result.evidence_pack,
        "tokens_query": result.tokens_query,
        "retrieval_latency_ms": result.retrieval_latency_ms,
    }
