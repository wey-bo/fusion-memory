# Temporal Relation Layer Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared temporal relation layer for current value, value history, temporal lookup, and event ordering while keeping existing production retrieval behavior unchanged.

**Architecture:** Introduce a deterministic `temporal_relations` module that emits structured relation records from query/text/value/date context. Existing modules consume the shared classifier only for shadow metadata, telemetry, and replay/audit output in this phase. No default ranking, filtering, graph selector, LLM extractor, or router behavior changes.

**Tech Stack:** Python 3.11+, `unittest`, existing `fusion_memory.retrieval.temporal_pack`, `value_history_pack`, `event_chronology_graph`, `pipeline`, `tools/beam_retrieval_replay.py`, and rule/lifecycle telemetry.

## Global Constraints

- Legacy event ordering remains the production default.
- Graph, dual, hybrid, and shared temporal relation outputs remain shadow/replay/telemetry-only in this phase.
- Do not delete or rewrite legacy `event_ordering_*` modules.
- Do not make LLM extractor/router part of realtime retrieval.
- Do not add project-specific or software-specific regex rescue branches.
- No raw user text may be stored in pipeline trace, rule-hit telemetry, replay artifacts, or rule audit.
- Every retrieval behavior change must be measurable with focused tests or replay artifacts.
- Shared temporal relation metadata must use structural fields only: relation type, normalized value/date when already present, role labels, confidence, source ids, and safe reason codes.

---

## File Structure

- Create: `fusion_memory/retrieval/temporal_relations.py`
  - Owns `TemporalRelation`, relation type constants, safe serialization, deterministic relation extraction, and summary helpers.
- Modify: `fusion_memory/retrieval/value_history_pack.py`
  - Adds relation shadow fields to value rows and summaries without changing row sort order.
- Modify: `fusion_memory/retrieval/temporal_pack.py`
  - Adds relation shadow fields to temporal mention/candidate tables without changing existing candidate ranking.
- Modify: `fusion_memory/retrieval/event_chronology_graph.py`
  - Uses the shared relation classifier for graph edge shadow reasons while preserving current edge construction output.
- Modify: `fusion_memory/retrieval/pipeline.py`
  - Adds optional temporal relation summary to `RetrievalPipelineRecord` as sanitized structural telemetry.
- Modify: `tools/beam_retrieval_replay.py`
  - Preserves sanitized temporal relation telemetry in replay output.
- Tests:
  - `tests/test_temporal_relations.py`
  - `tests/test_value_history_pack.py` or existing value-history tests in `tests/test_fusion_memory.py`
  - `tests/test_temporal_pack.py` or existing temporal tests in `tests/test_fusion_memory.py`
  - `tests/test_event_ordering_graph.py`
  - `tests/test_retrieval_pipeline.py`
  - `tests/test_beam_retrieval_replay.py`

---

### Task 1: Shared Temporal Relation Model And Classifier

**Files:**
- Create: `fusion_memory/retrieval/temporal_relations.py`
- Create: `tests/test_temporal_relations.py`

**Interfaces:**
- Produces: `TemporalRelation`
- Produces: `temporal_relations_for_text(text: str, *, query: str = "", value_text: str = "", value_type: str = "", normalized_date: str | None = None, source_span_id: str | None = None) -> list[TemporalRelation]`
- Produces: `temporal_relation_summary(relations: list[TemporalRelation]) -> dict[str, object]`
- Produces: `safe_temporal_relation_records(relations: list[TemporalRelation], *, limit: int = 12) -> list[dict[str, object]]`

- [ ] **Step 1: Write failing relation model tests**

Create `tests/test_temporal_relations.py`:

```python
from __future__ import annotations

import unittest

from fusion_memory.retrieval.temporal_relations import (
    safe_temporal_relation_records,
    temporal_relation_summary,
    temporal_relations_for_text,
)


class TemporalRelationTests(unittest.TestCase):
    def test_detects_current_value_supersession_without_raw_text(self) -> None:
        relations = temporal_relations_for_text(
            "I updated the budget from $20 to $35 today.",
            query="what is my current budget?",
            value_text="$35",
            value_type="money",
            source_span_id="span-1",
        )

        relation_types = {relation.relation_type for relation in relations}
        self.assertIn("changed_to", relation_types)
        self.assertIn("supersedes", relation_types)
        records = safe_temporal_relation_records(relations)
        self.assertTrue(records)
        for record in records:
            self.assertNotIn("text", record)
            self.assertNotIn("query", record)
            self.assertNotIn("context", record)

    def test_detects_deadline_and_decision_roles(self) -> None:
        relations = temporal_relations_for_text(
            "We decided on June 3, 2026 and the deployment deadline is July 1, 2026.",
            query="when was the decision and deadline?",
            normalized_date="2026-07-01",
            source_span_id="span-2",
        )

        self.assertIn("deadline", {relation.relation_type for relation in relations})
        self.assertIn("decision_at", {relation.relation_type for relation in relations})

    def test_summary_counts_relation_types_and_sources(self) -> None:
        relations = temporal_relations_for_text(
            "First I set the target to 20, then I changed it to 30.",
            query="what changed?",
            value_text="30",
            value_type="count",
            source_span_id="span-3",
        )

        summary = temporal_relation_summary(relations)

        self.assertGreaterEqual(summary["relation_count"], 1)
        self.assertIn("changed_to", summary["relation_types"])
        self.assertEqual(summary["source_span_count"], 1)
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_temporal_relations -v
```

Expected: FAIL because `fusion_memory.retrieval.temporal_relations` does not exist.

- [ ] **Step 3: Implement minimal relation model**

Create `fusion_memory/retrieval/temporal_relations.py`:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal


TemporalRelationType = Literal[
    "before",
    "after",
    "supersedes",
    "valid_from",
    "valid_to",
    "changed_from",
    "changed_to",
    "deadline",
    "decision_at",
    "observed_at",
]

_ALLOWED_REASON_CODES = {
    "explicit_order_marker",
    "update_marker",
    "previous_value_marker",
    "current_value_marker",
    "deadline_marker",
    "decision_marker",
    "date_observed",
    "range_endpoint",
}


@dataclass(frozen=True)
class TemporalRelation:
    relation_type: TemporalRelationType
    confidence: float
    reason_code: str
    source_span_id: str | None = None
    value_type: str | None = None
    value_hash: str | None = None
    normalized_date: str | None = None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "relation_type": self.relation_type,
            "confidence": round(float(self.confidence), 3),
            "reason_code": self.reason_code if self.reason_code in _ALLOWED_REASON_CODES else _hash_label(self.reason_code),
            **({"source_span_id": self.source_span_id} if self.source_span_id else {}),
            **({"value_type": self.value_type} if self.value_type else {}),
            **({"value_hash": self.value_hash} if self.value_hash else {}),
            **({"normalized_date": self.normalized_date} if self.normalized_date else {}),
        }


def temporal_relations_for_text(
    text: str,
    *,
    query: str = "",
    value_text: str = "",
    value_type: str = "",
    normalized_date: str | None = None,
    source_span_id: str | None = None,
) -> list[TemporalRelation]:
    lower = text.lower()
    relations: list[TemporalRelation] = []
    value_hash = _hash_label(value_text) if value_text else None
    if re.search(r"\b(?:first|before|previously|initially|originally)\b", lower):
        relations.append(_relation("before", 0.72, "explicit_order_marker", source_span_id, value_type, value_hash, normalized_date))
    if re.search(r"\b(?:then|after|later|next|finally|subsequently)\b", lower):
        relations.append(_relation("after", 0.72, "explicit_order_marker", source_span_id, value_type, value_hash, normalized_date))
    if re.search(r"\b(?:from|previously|before|baseline|old|originally|initially)\b", lower) and value_text:
        relations.append(_relation("changed_from", 0.70, "previous_value_marker", source_span_id, value_type, value_hash, normalized_date))
    if re.search(r"\b(?:updated|changed|revised|adjusted|moved|rescheduled|raised|reduced|increased|decreased|now|current|latest|to)\b", lower) and value_text:
        relations.append(_relation("changed_to", 0.78, "update_marker", source_span_id, value_type, value_hash, normalized_date))
        relations.append(_relation("supersedes", 0.68, "current_value_marker", source_span_id, value_type, value_hash, normalized_date))
    if re.search(r"\b(?:deadline|due|target date|by)\b", lower):
        relations.append(_relation("deadline", 0.82, "deadline_marker", source_span_id, value_type, value_hash, normalized_date))
    if re.search(r"\b(?:decided|decision|chose|picked|settled)\b", lower):
        relations.append(_relation("decision_at", 0.82, "decision_marker", source_span_id, value_type, value_hash, normalized_date))
    if normalized_date:
        relations.append(_relation("observed_at", 0.62, "date_observed", source_span_id, value_type, value_hash, normalized_date))
    return _dedupe_relations(relations)


def temporal_relation_summary(relations: list[TemporalRelation]) -> dict[str, object]:
    relation_types = sorted({relation.relation_type for relation in relations})
    source_span_ids = {relation.source_span_id for relation in relations if relation.source_span_id}
    return {
        "relation_count": len(relations),
        "relation_types": relation_types,
        "source_span_count": len(source_span_ids),
    }


def safe_temporal_relation_records(relations: list[TemporalRelation], *, limit: int = 12) -> list[dict[str, object]]:
    return [relation.to_safe_dict() for relation in relations[: max(0, limit)]]


def _relation(
    relation_type: TemporalRelationType,
    confidence: float,
    reason_code: str,
    source_span_id: str | None,
    value_type: str | None,
    value_hash: str | None,
    normalized_date: str | None,
) -> TemporalRelation:
    return TemporalRelation(
        relation_type=relation_type,
        confidence=confidence,
        reason_code=reason_code,
        source_span_id=source_span_id,
        value_type=value_type or None,
        value_hash=value_hash,
        normalized_date=normalized_date,
    )


def _dedupe_relations(relations: list[TemporalRelation]) -> list[TemporalRelation]:
    out: list[TemporalRelation] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for relation in relations:
        key = (relation.relation_type, relation.source_span_id, relation.value_hash, relation.normalized_date)
        if key in seen:
            continue
        seen.add(key)
        out.append(relation)
    return out


def _hash_label(value: str) -> str:
    return "sha1:" + hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
```

- [ ] **Step 4: Run green test**

Run:

```bash
python3 -m unittest tests.test_temporal_relations -v
python3 -m py_compile fusion_memory/retrieval/temporal_relations.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fusion_memory/retrieval/temporal_relations.py tests/test_temporal_relations.py
git commit -m "feat: add temporal relation classifier"
```

---

### Task 2: Value History And Temporal Lookup Shadow Relations

**Files:**
- Modify: `fusion_memory/retrieval/value_history_pack.py`
- Modify: `fusion_memory/retrieval/temporal_pack.py`
- Modify: `tests/test_fusion_memory.py` or create focused tests if local conventions already have pack-level tests.

**Interfaces:**
- Consumes: `temporal_relations_for_text`, `safe_temporal_relation_records`, `temporal_relation_summary`.
- Produces value row field: `temporal_relations: list[dict[str, object]]`
- Produces temporal candidate field: `temporal_relations: list[dict[str, object]]`
- Produces summary field: `temporal_relation_summary: dict[str, object]`

- [ ] **Step 1: Write failing value-history shadow test**

Add a focused test that builds value history rows with an old and updated value:

```python
def test_value_history_rows_include_safe_temporal_relations_without_affecting_sort(self) -> None:
    spans = [
        {"id": "old", "speaker": "user", "content": "Previously my snack budget was $20.", "timeline_index": 1, "recency_rank": 2},
        {"id": "new", "speaker": "user", "content": "I updated my snack budget to $35 now.", "timeline_index": 2, "recency_rank": 1},
    ]

    rows = build_value_history_table("what is my current snack budget?", spans, [])

    self.assertEqual(rows[0]["source_span_id"], "new")
    self.assertTrue(rows[0]["temporal_relations"])
    self.assertIn("changed_to", {item["relation_type"] for item in rows[0]["temporal_relations"]})
    self.assertNotIn("content", rows[0]["temporal_relations"][0])
```

Import `build_value_history_table` from `fusion_memory.retrieval.value_history_pack`.

- [ ] **Step 2: Write failing temporal lookup shadow test**

Add a focused test for `temporal_mentions()` and `temporal_candidate_table()`:

```python
def test_temporal_candidate_table_includes_safe_relation_summary(self) -> None:
    mention_rows = [
        {
            "id": "span-date",
            "speaker": "user",
            "timeline_index": 1,
            "temporal_mentions": temporal_mentions(
                "when is the deployment deadline?",
                "The deployment deadline is July 1, 2026.",
            ),
        }
    ]

    candidates = temporal_candidate_table("when is the deployment deadline?", mention_rows)

    self.assertTrue(candidates)
    self.assertTrue(candidates[0]["temporal_relations"])
    self.assertIn("deadline", {item["relation_type"] for item in candidates[0]["temporal_relations"]})
```

- [ ] **Step 3: Run red tests**

Run the specific tests added in Step 1 and Step 2. Expected: FAIL because `temporal_relations` fields are missing.

- [ ] **Step 4: Add value-history shadow fields**

In `build_value_history_table()`, after `value_role` and `marker_strength` are known, compute:

```python
relations = safe_temporal_relation_records(
    temporal_relations_for_text(
        context or content,
        query=query,
        value_text=value_text,
        value_type=value_type,
        source_span_id=str(span.get("id") or "") or None,
    )
)
```

Add `"temporal_relations": relations` to each row. Do the same for fact-derived rows using fact text/source span id. Do not change `value_history_sort_key()`.

In `value_history_summary()`, aggregate relations from selected rows:

```python
"temporal_relation_summary": temporal_relation_summary_from_safe_records(...)
```

If Task 1 lacks a helper for safe-record summaries, add `temporal_relation_summary_from_safe_records(records: list[dict[str, object]]) -> dict[str, object]` in `temporal_relations.py` with counts only.

- [ ] **Step 5: Add temporal lookup shadow fields**

In `temporal_mentions()`, add safe relation records to each mention:

```python
"temporal_relations": safe_temporal_relation_records(
    temporal_relations_for_text(
        role_text,
        query=query,
        normalized_date=normalized_date,
    )
)
```

In `temporal_candidate_table()`, copy `mention["temporal_relations"]` into candidate rows. Do not change the candidate sort key.

- [ ] **Step 6: Run green tests**

Run:

```bash
python3 -m unittest tests.test_temporal_relations tests.test_fusion_memory -v
python3 -m py_compile fusion_memory/retrieval/temporal_relations.py fusion_memory/retrieval/value_history_pack.py fusion_memory/retrieval/temporal_pack.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add fusion_memory/retrieval/temporal_relations.py fusion_memory/retrieval/value_history_pack.py fusion_memory/retrieval/temporal_pack.py tests
git commit -m "feat: add temporal relation shadow metadata"
```

---

### Task 3: Event Graph And Pipeline Temporal Relation Telemetry

**Files:**
- Modify: `fusion_memory/retrieval/event_chronology_graph.py`
- Modify: `fusion_memory/retrieval/pipeline.py`
- Modify: `fusion_memory/api/service.py` or `fusion_memory/api/service_helpers.py` only if needed to pass relation summaries into pipeline records.
- Modify: `tests/test_event_ordering_graph.py`
- Modify: `tests/test_retrieval_pipeline.py`

**Interfaces:**
- Consumes: safe temporal relation records.
- Produces graph candidate metadata field: `temporal_relations`.
- Produces graph candidate metadata field: `temporal_relation_summary`.
- Produces pipeline layer field: `TemporalRelations`.

- [ ] **Step 1: Write failing graph metadata test**

Add to `tests/test_event_ordering_graph.py`:

```python
def test_graph_candidates_include_temporal_relation_shadow_metadata(self) -> None:
    # Reuse the existing test fixture style in this file to build two ordered events.
    candidates = select_graph_first_event_ordering_candidates(query, spans, events, limit=4)

    self.assertTrue(candidates)
    relation_candidates = [candidate for candidate in candidates if candidate.metadata.get("temporal_relations")]
    self.assertTrue(relation_candidates)
    self.assertIn("temporal_relation_summary", relation_candidates[0].metadata)
    self.assertNotIn("text", relation_candidates[0].metadata["temporal_relations"][0])
```

Use existing helper objects in the file rather than inventing new model construction if helpers exist.

- [ ] **Step 2: Write failing pipeline telemetry test**

Add to `tests/test_retrieval_pipeline.py`:

```python
def test_build_pipeline_record_can_include_temporal_relation_summary(self) -> None:
    record = build_pipeline_record(
        "temporal_lookup",
        "default",
        language="en",
        intent="temporal_lookup",
        features=["temporal_terms"],
        recalled=[],
        selected=[],
        dropped_count=0,
        source_span_count=0,
        coverage_insufficient=False,
        temporal_relation_summary={"relation_count": 2, "relation_types": ["deadline"], "source_span_count": 1},
    )

    data = record.to_dict()

    self.assertEqual(data["pipeline_layers"]["TemporalRelations"]["relation_count"], 2)
```

- [ ] **Step 3: Run red tests**

Run:

```bash
python3 -m unittest tests.test_event_ordering_graph tests.test_retrieval_pipeline -v
```

Expected: FAIL because graph/pipeline relation metadata is missing.

- [ ] **Step 4: Add graph relation metadata**

In `_graph_candidates()`, compute safe relation records from `node.label` or event description:

```python
relations = safe_temporal_relation_records(
    temporal_relations_for_text(
        node.label,
        query=query,
        source_span_id=node.source_span_id,
    )
)
```

Add these only to candidate metadata. Do not change graph score, candidate ordering, fallback behavior, or source text.

- [ ] **Step 5: Add optional pipeline summary field**

Extend `RetrievalPipelineRecord` and `build_pipeline_record()` with:

```python
temporal_relation_summary: dict[str, object] | None = None
```

Add a `TemporalRelations` layer only when the summary is present. Preserve existing `to_dict()` keys for compatibility.

- [ ] **Step 6: Run green tests**

Run:

```bash
python3 -m unittest tests.test_event_ordering_graph tests.test_retrieval_pipeline tests.test_temporal_relations -v
python3 -m py_compile fusion_memory/retrieval/event_chronology_graph.py fusion_memory/retrieval/pipeline.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add fusion_memory/retrieval/event_chronology_graph.py fusion_memory/retrieval/pipeline.py tests/test_event_ordering_graph.py tests/test_retrieval_pipeline.py
git commit -m "feat: expose temporal relation telemetry"
```

---

### Task 4: Replay Preservation And Verification Gate

**Files:**
- Modify: `tools/beam_retrieval_replay.py`
- Modify: `tests/test_beam_retrieval_replay.py`
- Modify: `docs/superpowers/plans/2026-06-21-temporal-relation-layer-phase-4.md` only if implementation discovered a plan correction.

**Interfaces:**
- Consumes relation metadata from pipeline traces, candidate lifecycle, and answer context coverage.
- Produces replay output field: `temporal_relation_summary`.
- Produces replay record field: sanitized `temporal_relations` when present.

- [ ] **Step 1: Write failing replay sanitization test**

Add to `tests/test_beam_retrieval_replay.py` a unit test around the existing replay sanitization helper:

```python
def test_run_replay_preserves_safe_temporal_relation_telemetry(self) -> None:
    # Follow existing fake service/replay test style in this file.
    # The fake answer context should include coverage/pipeline data with:
    # {"temporal_relation_summary": {"relation_count": 1, "relation_types": ["deadline"], "source_span_count": 1}}
    # and candidate metadata with:
    # {"temporal_relations": [{"relation_type": "deadline", "confidence": 0.82, "reason_code": "deadline_marker"}]}
    # Assert replay JSON preserves those fields but does not preserve raw text/query/context.
```

Use the concrete helper fixtures already present in the test file; do not add a broad integration dependency.

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_beam_retrieval_replay -v
```

Expected: FAIL because replay sanitization drops temporal relation telemetry.

- [ ] **Step 3: Preserve sanitized relation telemetry**

In `tools/beam_retrieval_replay.py`, extend existing safe-copy allowlists to include:

- `temporal_relation_summary`
- `temporal_relations`

For each relation record, preserve only:

- `relation_type`
- `confidence`
- `reason_code`
- `source_span_id`
- `value_type`
- `value_hash`
- `normalized_date`

Drop `text`, `query`, `context`, `prompt`, `content`, and unknown string fields unless they are hashed by the existing sanitizer.

- [ ] **Step 4: Run green test**

Run:

```bash
python3 -m unittest tests.test_beam_retrieval_replay tests.test_temporal_relations -v
python3 -m py_compile tools/beam_retrieval_replay.py
```

Expected: PASS.

- [ ] **Step 5: Run focused Phase 4 gate**

Run:

```bash
python3 -m unittest \
  tests.test_temporal_relations \
  tests.test_retrieval_pipeline \
  tests.test_beam_retrieval_replay \
  tests.test_event_ordering_graph \
  tests.test_fusion_memory.FusionMemoryTests.test_current_value_query_prioritizes_latest_correction_over_historical_value \
  tests.test_fusion_memory.FusionMemoryTests.test_event_ordering_default_search_does_not_select_graph_candidates \
  tests.test_fusion_memory.FusionMemoryTests.test_temporal_lookup_labels_decision_and_reschedule_dates \
  -v
```

Expected: PASS.

- [ ] **Step 6: Run broad regression gate**

Run:

```bash
python3 -m unittest \
  tests.test_runtime_config \
  tests.test_fusion_memory \
  tests.test_retrieval_pipeline \
  tests.test_retrieval_trace \
  tests.test_beam_event_ordering_replay \
  tests.test_beam_retrieval_replay \
  tests.test_rule_registry \
  tests.test_rule_audit \
  tests.test_config_and_reporting \
  tests.test_authorizer \
  tests.test_product_cli \
  tests.test_agent_installer \
  tests.test_agent_runtime_smoke \
  tests.test_event_ordering_graph \
  tests.test_chronology_selector \
  -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tools/beam_retrieval_replay.py tests/test_beam_retrieval_replay.py docs/superpowers/plans/2026-06-21-temporal-relation-layer-phase-4.md
git commit -m "feat: preserve temporal relation replay telemetry"
```

---

## Final Review And Merge Gate

- [ ] Generate final review package from the branch base to HEAD.
- [ ] Dispatch final whole-branch reviewer.
- [ ] Fix Critical/Important findings, then rerun focused and broad gates.
- [ ] Merge back to local `main` only after final review passes.
- [ ] Rerun focused and broad gates on merged `main`.
- [ ] Remove `.worktrees/temporal-relation-phase-4` and delete the local feature branch.

## Phase 4 Success Criteria

- Shared temporal relation records exist and serialize without raw text.
- Value history, temporal lookup, event graph, pipeline trace, and replay can carry relation telemetry.
- Existing current-value, temporal lookup, and event-ordering default behavior does not change.
- Legacy event ordering remains default.
- Replay artifacts preserve relation telemetry safely enough to compare later phases.
