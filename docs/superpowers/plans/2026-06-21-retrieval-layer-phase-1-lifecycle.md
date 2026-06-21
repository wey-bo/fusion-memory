# Retrieval Layer Phase 1 Candidate Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sanitized candidate lifecycle telemetry to the existing retrieval path so replay can diagnose whether evidence was recalled, filtered, rescued, selected, or packed.

**Architecture:** This phase wraps the current retrieval path with structural lifecycle recording and does not change retrieval behavior. `MemoryService.search()` records recalled/scored/filtered/rescued/selected stages, `answer_context()` records packed counts, and replay serializes lifecycle summaries. Later phases will use this data to extract provider registries and clean rules.

**Tech Stack:** Python 3.11+/3.12, `unittest`, existing `MemoryService`, existing `Candidate`, existing replay tools, existing pipeline trace and rule telemetry.

## Global Constraints

- Legacy event ordering remains the production default.
- Graph, dual, and hybrid paths remain shadow/replay/flag-only until replay proves parity.
- Do not delete legacy event ordering code in this refactor.
- LLM extractor and LLM router remain out of the realtime main retrieval path.
- No raw user text may be stored in pipeline trace, rule-hit telemetry, replay artifacts, or rule audit.
- Every retrieval behavior change must be measurable with replay artifacts.
- Product-facing errors remain beginner-safe and must not expose tracebacks.
- Existing OpenClaw/Hermes integration remains external; do not modify host source trees.

---

## File Structure

- Create: `fusion_memory/retrieval/candidate_lifecycle.py`
  - Owns `CandidateLifecycleRecord`, `CandidateLifecycleRecorder`, sanitization, and summary generation.
- Modify: `fusion_memory/api/service.py`
  - Records lifecycle stages without changing candidate ordering or selection.
  - Adds `coverage["candidate_lifecycle"]`.
  - Updates `answer_context()` packed count after `EvidencePackBuilder.build()`.
- Modify: `tools/beam_retrieval_replay.py`
  - Serializes sanitized lifecycle summaries in replay records.
- Create: `tests/test_candidate_lifecycle.py`
  - Unit tests for lifecycle sanitization and summary.
- Modify: `tests/test_retrieval_pipeline.py`
  - Service-level lifecycle coverage tests.
- Modify: `tests/test_beam_retrieval_replay.py`
  - Replay serialization tests for lifecycle summaries.

---

### Task 1: Candidate Lifecycle Data Model

**Files:**
- Create: `fusion_memory/retrieval/candidate_lifecycle.py`
- Create: `tests/test_candidate_lifecycle.py`

**Interfaces:**
- Produces: `CandidateLifecycleRecord`
- Produces: `CandidateLifecycleRecorder`
- Produces: `CandidateLifecycleRecorder.record(candidate: Candidate, stage: str, reason_code: str, contributed: bool | None = None, scores: dict[str, float] | None = None) -> None`
- Produces: `CandidateLifecycleRecorder.summary() -> dict[str, object]`
- Produces: `CandidateLifecycleRecorder.to_trace(limit: int = 200) -> list[dict[str, object]]`

- [ ] **Step 1: Write failing unit tests**

Create `tests/test_candidate_lifecycle.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_candidate_lifecycle -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fusion_memory.retrieval.candidate_lifecycle'`.

- [ ] **Step 3: Implement lifecycle model**

Create `fusion_memory/retrieval/candidate_lifecycle.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fusion_memory.core.models import Candidate


_ALLOWED_STAGES = {"recalled", "scored", "filtered", "rescued", "selected", "packed"}


@dataclass(frozen=True)
class CandidateLifecycleRecord:
    candidate_id: str
    candidate_type: str
    candidate_source: str
    stage: str
    reason_code: str
    source_span_ids: tuple[str, ...] = ()
    scores: dict[str, float] = field(default_factory=dict)
    contributed: bool | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "candidate_id": self.candidate_id,
            "candidate_type": self.candidate_type,
            "candidate_source": self.candidate_source,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "source_span_ids": list(self.source_span_ids),
        }
        if self.scores:
            out["scores"] = dict(self.scores)
        if self.contributed is not None:
            out["contributed"] = self.contributed
        return out


class CandidateLifecycleRecorder:
    def __init__(self) -> None:
        self._records: list[CandidateLifecycleRecord] = []

    def record(
        self,
        candidate: Candidate,
        stage: str,
        reason_code: str,
        contributed: bool | None = None,
        scores: dict[str, float] | None = None,
    ) -> None:
        if stage not in _ALLOWED_STAGES:
            raise ValueError(f"unsupported lifecycle stage: {stage}")
        numeric_scores = {
            str(key): float(value)
            for key, value in (scores or candidate.scores or {}).items()
            if isinstance(value, (int, float, bool))
        }
        self._records.append(
            CandidateLifecycleRecord(
                candidate_id=str(candidate.id),
                candidate_type=str(candidate.type),
                candidate_source=str(candidate.source),
                stage=stage,
                reason_code=str(reason_code),
                source_span_ids=tuple(str(span_id) for span_id in candidate.source_span_ids if span_id),
                scores=numeric_scores,
                contributed=contributed,
            )
        )

    def extend(self, candidates: list[Candidate], stage: str, reason_code: str) -> None:
        for candidate in candidates:
            self.record(candidate, stage, reason_code)

    def to_trace(self, limit: int = 200) -> list[dict[str, object]]:
        return [record.to_dict() for record in self._records[: max(0, int(limit))]]

    def summary(self) -> dict[str, object]:
        stage_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        contributed_count = 0
        for record in self._records:
            stage_counts[record.stage] = stage_counts.get(record.stage, 0) + 1
            source_counts[record.candidate_source] = source_counts.get(record.candidate_source, 0) + 1
            reason_counts[record.reason_code] = reason_counts.get(record.reason_code, 0) + 1
            if record.contributed:
                contributed_count += 1
        return {
            "record_count": len(self._records),
            "stage_counts": stage_counts,
            "source_counts": source_counts,
            "reason_counts": reason_counts,
            "contributed_count": contributed_count,
        }
```

- [ ] **Step 4: Run green test**

Run:

```bash
python3 -m unittest tests.test_candidate_lifecycle -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fusion_memory/retrieval/candidate_lifecycle.py tests/test_candidate_lifecycle.py
git commit -m "feat: add candidate lifecycle recorder"
```

---

### Task 2: Search Lifecycle Coverage

**Files:**
- Modify: `fusion_memory/api/service.py`
- Modify: `tests/test_retrieval_pipeline.py`

**Interfaces:**
- Consumes: `CandidateLifecycleRecorder`
- Produces: `SearchResult.coverage["candidate_lifecycle"]`
- Produces: stored trace key `candidate_lifecycle_trace`

- [ ] **Step 1: Write failing service test**

Append to `tests/test_retrieval_pipeline.py`:

```python
    def test_search_coverage_includes_candidate_lifecycle_without_raw_text(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="ws-lifecycle", user_id="u", agent_id="a")
        raw_memory_text = "Remember private token violet-river-42 for lifecycle tracing."
        raw_query_text = "Which private token did I ask you to remember for lifecycle tracing?"
        try:
            memory.add({"role": "user", "content": raw_memory_text}, scope)
            result = memory.search(raw_query_text, scope)
            trace = memory.debug_trace(result.trace_id, scope)
        finally:
            memory.close()

        lifecycle = result.coverage["candidate_lifecycle"]
        self.assertGreater(lifecycle["stage_counts"].get("recalled", 0), 0)
        self.assertGreater(lifecycle["stage_counts"].get("selected", 0), 0)
        self.assertIn("candidate_lifecycle_trace", trace)
        self.assertNotIn(raw_memory_text, repr(lifecycle))
        self.assertNotIn(raw_query_text, repr(lifecycle))
        self.assertNotIn(raw_memory_text, repr(trace["candidate_lifecycle_trace"]))
        self.assertNotIn(raw_query_text, repr(trace["candidate_lifecycle_trace"]))
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_retrieval_pipeline.RetrievalPipelineTests.test_search_coverage_includes_candidate_lifecycle_without_raw_text -v
```

Expected: FAIL with missing `candidate_lifecycle`.

- [ ] **Step 3: Record recalled, scored, selected, and dropped high-signal candidates**

In `fusion_memory/api/service.py`:

Add import:

```python
from fusion_memory.retrieval.candidate_lifecycle import CandidateLifecycleRecorder
```

Inside `_search_with_rule_hits()`:

```python
lifecycle = CandidateLifecycleRecorder()
```

After `candidate_lists = ...`:

```python
for items in candidate_lists:
    lifecycle.extend(items, "recalled", "candidate_provider")
```

After `scored_again` is built:

```python
lifecycle.extend(scored_again, "scored", "utility_scoring")
```

After final `selected` and `dropped_high_signal` are known:

```python
for candidate in selected:
    lifecycle.record(candidate, "selected", "final_selection", contributed=True)
for dropped in dropped_high_signal:
    candidate = dropped.get("candidate") if isinstance(dropped, dict) else None
    if isinstance(candidate, Candidate):
        lifecycle.record(candidate, "filtered", str(dropped.get("reason") or "high_signal_drop"), contributed=False)
```

If `dropped_high_signal` does not contain Candidate objects, record only selected/recalled/scored in this phase. Do not add raw dropped text to lifecycle.

Before constructing `trace`:

```python
coverage["candidate_lifecycle"] = lifecycle.summary()
lifecycle_trace = lifecycle.to_trace()
```

Inside stored trace:

```python
"candidate_lifecycle_trace": lifecycle_trace,
```

- [ ] **Step 4: Run green test**

Run:

```bash
python3 -m unittest tests.test_retrieval_pipeline -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fusion_memory/api/service.py tests/test_retrieval_pipeline.py
git commit -m "feat: record search candidate lifecycle"
```

---

### Task 3: Packed Lifecycle Counts In Answer Context

**Files:**
- Modify: `fusion_memory/api/service.py`
- Modify: `tests/test_retrieval_pipeline.py`

**Interfaces:**
- Consumes: `pack.coverage["candidate_lifecycle"]`
- Produces: `candidate_lifecycle.stage_counts["packed"]`
- Produces: `candidate_lifecycle.packed_source_span_count`

- [ ] **Step 1: Write failing answer-context test**

Append to `tests/test_retrieval_pipeline.py`:

```python
    def test_answer_context_lifecycle_reports_packed_source_spans(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="ws-lifecycle-pack", user_id="u", agent_id="a")
        try:
            memory.add({"role": "user", "content": "Remember private token silver-forest-77 for packed lifecycle."}, scope)
            pack = memory.answer_context("Which private token did I ask you to remember for packed lifecycle?", scope)
        finally:
            memory.close()

        lifecycle = pack.coverage["candidate_lifecycle"]
        self.assertEqual(lifecycle["stage_counts"].get("packed", 0), len(pack.source_spans))
        self.assertEqual(lifecycle["packed_source_span_count"], len(pack.source_spans))
        self.assertNotIn("silver-forest-77", repr(lifecycle))
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_retrieval_pipeline.RetrievalPipelineTests.test_answer_context_lifecycle_reports_packed_source_spans -v
```

Expected: FAIL because `packed` counts are missing.

- [ ] **Step 3: Update packed lifecycle summary**

In `MemoryService._answer_context_with_rule_hits()`, after `pack = self.pack_builder.build(...)` and after `pipeline_trace` update:

```python
lifecycle = dict(pack.coverage.get("candidate_lifecycle") or {})
stage_counts = dict(lifecycle.get("stage_counts") or {})
stage_counts["packed"] = len(pack.source_spans)
lifecycle["stage_counts"] = stage_counts
lifecycle["packed_source_span_count"] = len(pack.source_spans)
pack.coverage["candidate_lifecycle"] = lifecycle
```

Do not record source span content.

- [ ] **Step 4: Run green tests**

Run:

```bash
python3 -m unittest tests.test_retrieval_pipeline -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fusion_memory/api/service.py tests/test_retrieval_pipeline.py
git commit -m "feat: report packed lifecycle counts"
```

---

### Task 4: Replay Lifecycle Serialization

**Files:**
- Modify: `tools/beam_retrieval_replay.py`
- Modify: `tests/test_beam_retrieval_replay.py`

**Interfaces:**
- Consumes: `pack.coverage["candidate_lifecycle"]`
- Produces: `records[].candidate_lifecycle`

- [ ] **Step 1: Write failing replay test**

Append to `tests/test_beam_retrieval_replay.py`:

```python
    def test_run_replay_writes_sanitized_candidate_lifecycle(self) -> None:
        fake_query = SimpleNamespace(id="q1", query="What is my private token?", category="knowledge_update")
        fake_pack = SimpleNamespace(
            source_spans=[{"span_id": "s1"}],
            coverage={
                "coverage_insufficient": False,
                "candidate_lifecycle": {
                    "record_count": 2,
                    "stage_counts": {"recalled": 1, "selected": 1},
                    "source_counts": {"l0_raw_hybrid": 2},
                    "reason_counts": {"candidate_provider": 1, "final_selection": 1},
                    "raw_text": "do not persist",
                },
            },
            debug_trace=[],
        )
        service = MagicMock()
        service.answer_context.return_value = fake_pack

        with tempfile.TemporaryDirectory() as tmp, patch.object(replay, "_load_queries", return_value=[fake_query]):
            out = Path(tmp) / "replay.json"
            replay.run_replay(
                service,
                base_scope=replay.Scope(workspace_id="w", user_id="u", agent_id="a"),
                categories={"current_value"},
                output_path=out,
                query_limit=None,
            )
            payload = json.loads(out.read_text(encoding="utf-8"))

        lifecycle = payload["records"][0]["candidate_lifecycle"]
        self.assertEqual(lifecycle["stage_counts"]["recalled"], 1)
        self.assertEqual(lifecycle["source_counts"]["l0_raw_hybrid"], 2)
        self.assertNotIn("raw_text", lifecycle)
        self.assertNotIn("do not persist", json.dumps(payload, ensure_ascii=False))
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_beam_retrieval_replay.BeamRetrievalReplayTests.test_run_replay_writes_sanitized_candidate_lifecycle -v
```

Expected: FAIL because replay does not serialize lifecycle.

- [ ] **Step 3: Implement lifecycle sanitizer**

In `tools/beam_retrieval_replay.py`, add:

```python
def _sanitize_candidate_lifecycle(value: Any) -> dict[str, Any]:
    data = _object_dict(value)
    if not data:
        return {}
    out: dict[str, Any] = {}
    for key in ("record_count", "contributed_count", "packed_source_span_count"):
        count = _sanitize_count_value(data.get(key))
        if count is not None:
            out[key] = count
    for key in ("stage_counts", "source_counts", "reason_counts"):
        mapping = _sanitize_count_mapping(data.get(key))
        if mapping:
            out[key] = mapping
    return out
```

Inside record construction:

```python
lifecycle = _sanitize_candidate_lifecycle(coverage.get("candidate_lifecycle"))
if lifecycle:
    record["candidate_lifecycle"] = lifecycle
```

- [ ] **Step 4: Run green tests**

Run:

```bash
python3 -m unittest tests.test_beam_retrieval_replay -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/beam_retrieval_replay.py tests/test_beam_retrieval_replay.py
git commit -m "feat: include lifecycle in retrieval replay"
```

---

### Task 5: Phase 1 Verification Gate

**Files:**
- Modify only if tests expose issues.

**Interfaces:**
- Verifies Phase 1 does not alter retrieval behavior.

- [ ] **Step 1: Run focused retrieval/product tests**

Run:

```bash
python3 -m unittest \
  tests.test_candidate_lifecycle \
  tests.test_retrieval_pipeline \
  tests.test_beam_retrieval_replay \
  tests.test_runtime_config \
  tests.test_fusion_memory.FusionMemoryTests.test_event_ordering_default_search_does_not_select_graph_candidates \
  tests.test_fusion_memory.FusionMemoryTests.test_dual_shadow_does_not_replace_event_ordering_selected_candidates \
  tests.test_product_cli.ProductCliTests.test_upgrade_failure_json_is_beginner_safe_without_raw_subprocess_output \
  -v
```

Expected: PASS.

- [ ] **Step 2: Run broader productization suite**

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
  tests.test_product_cli \
  tests.test_agent_installer \
  tests.test_agent_runtime_smoke \
  tests.test_event_ordering_graph \
  tests.test_chronology_selector \
  -v
```

Expected: PASS.

- [ ] **Step 3: Commit verification notes if code changed**

If no code changed, do not create an empty commit. Record test output in the task report.

---

---

## Later Phase Roadmap

The following phases are planned work after Phase 1 lands and passes replay gates. Each phase must receive its own detailed `docs/superpowers/plans/YYYY-MM-DD-*.md` execution plan before code changes begin. The roadmap below defines scope, file ownership, gate criteria, and non-goals so the phases remain aligned.

### Phase 2: Recall Provider Registry

**Goal:** Move provider orchestration out of the large `candidate_provider.py` branch structure without changing selected evidence.

**Primary Files:**
- Create: `fusion_memory/retrieval/providers/base.py`
- Create: `fusion_memory/retrieval/providers/registry.py`
- Create: `fusion_memory/retrieval/providers/raw.py`
- Create: `fusion_memory/retrieval/providers/facts.py`
- Create: `fusion_memory/retrieval/providers/events.py`
- Create: `fusion_memory/retrieval/providers/current_views.py`
- Create: `fusion_memory/retrieval/providers/graph_shadow.py`
- Modify: `fusion_memory/retrieval/candidate_provider.py`
- Test: `tests/test_recall_provider_registry.py`
- Test: `tests/test_retrieval_pipeline.py`

**Execution Shape:**
- Introduce `RecallProvider` protocol with `provider_id`, `production_default`, `shadow_only`, `supported_query_types`, and `recall(context) -> list[Candidate]`.
- Register existing provider families one at a time behind parity tests.
- Keep `candidate_provider.py` as a compatibility facade until every existing path is represented in the registry.
- Record provider ids in candidate lifecycle as `reason_code` or `provider_id`.

**Gate:**
- Default event ordering still excludes graph candidates.
- `tests.test_fusion_memory` and `tests.test_retrieval_pipeline` pass with identical selected candidate ids for representative fixtures.
- Event ordering, current value, multi-condition, and Chinese replay show no selected-evidence regression versus Phase 1.

**Non-Goals:**
- Do not tune ranking.
- Do not delete legacy event ordering.
- Do not make graph providers production default.

### Phase 3: Rule Registry Enforcement And Cleanup

**Goal:** Make regex/rule contribution measurable globally, then remove only rules proven unused, duplicated, or harmful.

**Primary Files:**
- Modify: `fusion_memory/retrieval/rule_registry.py`
- Modify: `fusion_memory/retrieval/rule_audit.py`
- Modify: regex-heavy retrieval modules identified by audit output.
- Modify: `tools/beam_retrieval_replay.py`
- Create or modify: `tools/rule_audit_report.py`
- Test: `tests/test_rule_registry.py`
- Test: `tests/test_rule_audit.py`
- Test: `tests/test_beam_retrieval_replay.py`

**Execution Shape:**
- Require every audited rule to declare `rule_id`, `ability`, `protected`, and optional `duplicate_of`.
- Attach rule hits to candidate lifecycle where a rule recalls, filters, rescues, or selects evidence.
- Produce a sanitized rule audit table with hit count, contribution count, negative impact count, duplicate group, and protected status.
- First deletion batch removes only zero-hit, zero-contribution, unprotected, or exact duplicate rules.
- Domain label regex moves to configurable taxonomy before deletion.

**Gate:**
- Rule audit output contains no raw user text.
- First cleanup batch has before/after replay artifacts.
- No regression on current value, multi-condition, Chinese recall, and event ordering replay.

**Non-Goals:**
- Do not delete protected legacy event ordering fallback rules in this phase.
- Do not replace rules with LLM extractor/router in realtime retrieval.

### Phase 4: Shared Temporal Relation Layer

**Goal:** Replace scattered temporal/current-value reasoning with explicit relation objects, while keeping existing operators as fallback until parity is proven.

**Primary Files:**
- Create: `fusion_memory/retrieval/temporal_relations.py`
- Create: `fusion_memory/retrieval/temporal_relation_builder.py`
- Create: `fusion_memory/retrieval/temporal_relation_selector.py`
- Modify: `fusion_memory/retrieval/value_history_pack.py`
- Modify: `fusion_memory/retrieval/slot_state_transition.py`
- Modify: `fusion_memory/retrieval/temporal_pack.py`
- Modify: event ordering modules only behind shadow comparison.
- Test: `tests/test_temporal_relations.py`
- Test: `tests/test_value_history_pack.py`
- Test: `tests/test_beam_retrieval_replay.py`

**Execution Shape:**
- Define relation types: `before`, `after`, `supersedes`, `valid_from`, `valid_to`, `changed_from`, `changed_to`, `deadline`, `decision_at`, and `observed_at`.
- Build relations from existing events, current views, value history, and chronology graph metadata.
- Run relation selector in shadow mode beside current temporal/current-value logic.
- Add replay columns for relation count, relation source, latest-state decision, and stale-history filtering.

**Gate:**
- Current value replay keeps latest-value precision and does not revive stale conflicts.
- Temporal reasoning replay does not regress.
- Event ordering shadow artifacts explain where relation ordering agrees or disagrees with legacy.

**Non-Goals:**
- Do not remove current value or temporal fallback code until two consecutive replay runs show parity.

### Phase 5: Graph Ordering And Topic Clustering

**Goal:** Use graph as an ordering, clustering, and explanation layer over raw/legacy recall, not as a standalone recall backbone.

**Primary Files:**
- Modify: `fusion_memory/retrieval/event_graph_selection.py`
- Modify: chronology graph builder/backfill modules.
- Create or modify: `fusion_memory/retrieval/topic_clustering.py`
- Modify: `tools/beam_event_ordering_replay.py`
- Modify: graph-vs-legacy replay tooling.
- Test: `tests/test_event_ordering_graph.py`
- Test: `tests/test_chronology_selector.py`
- Test: `tests/test_beam_event_ordering_replay.py`

**Execution Shape:**
- Merge fine-grained span topics into episode/topic clusters within the same session.
- Keep dual path as `legacy/raw recall + graph ordering`.
- Compare four paths in replay: `legacy`, `graph`, `dual`, and `hybrid`.
- Add diagnostics for empty graph rate, too-few-nodes rate, source span coverage, Kendall tau, precision, recall, and F1.

**Gate:**
- Dual or hybrid must match or beat legacy on event ordering F1 and Kendall tau.
- Source span coverage must not decrease.
- Graph empty rate and too-few-nodes rate must stay below the threshold chosen in the phase plan.
- Default product path remains legacy unless gate passes and user explicitly approves promotion.

**Non-Goals:**
- Do not continue expanding event-ordering phrase lists.
- Do not delete legacy event ordering rules in this phase unless the phase explicitly reaches the promotion gate and a separate deletion plan is approved.

### Phase 6: Real Retrieval Pipeline Execution Layer

**Goal:** Turn `MemoryService.search()` into a thin orchestrator after telemetry, provider registry, and shadow graph paths are stable.

**Primary Files:**
- Create or modify: `fusion_memory/retrieval/query_understanding.py`
- Create or modify: `fusion_memory/retrieval/recall_orchestrator.py`
- Create or modify: `fusion_memory/retrieval/candidate_fusion.py`
- Create or modify: `fusion_memory/retrieval/evidence_pack_assembler.py`
- Modify: `fusion_memory/retrieval/pipeline.py`
- Modify: `fusion_memory/api/service.py`
- Test: `tests/test_retrieval_pipeline.py`
- Test: `tests/test_retrieval_trace.py`
- Test: `tests/test_fusion_memory.py`

**Execution Shape:**
- `QueryUnderstandingEngine.run(query, scope, options) -> QueryUnderstandingResult`
- `RecallOrchestrator.run(context) -> RecallResult`
- `CandidateFusionEngine.run(context, recall_result) -> FusionResult`
- `EvidencePackAssembler.run(context, fusion_result) -> EvidencePack`
- `RetrievalTraceRecorder.flush() -> dict`
- Preserve `MemoryService.search()` public API and trace ids.
- Move one responsibility at a time from service into the pipeline execution layer.

**Gate:**
- Service-level focused tests pass after every extracted component.
- Replay artifacts remain comparable before and after extraction.
- Product CLI, installer, runtime smoke, and beginner-safe error tests still pass.

**Non-Goals:**
- Do not change model defaults.
- Do not enable realtime LLM extractor/router.
- Do not modify real OpenClaw/Hermes source trees.
