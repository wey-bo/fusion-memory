# Retrieval Layer Refactor Design

## Goal

Optimize Fusion Memory retrieval without destabilizing current product behavior. The first phase adds lifecycle telemetry and replay visibility so later refactors can be evidence-driven. Later phases progressively extract provider orchestration, clean rules, and unify temporal reasoning.

## Current State

The retrieval layer already has useful typed operators for current value, multi-condition recall, Chinese recall, temporal lookup, aggregation, contradiction handling, and event ordering. It also has `pipeline_trace`, rule telemetry, rule audit, BEAM replay tools, and graph/dual/hybrid shadow evaluation.

The main weakness is that the actual execution path is still concentrated in `MemoryService.search()` and a large `candidate_provider.py` branch structure. Candidate recall, filtering, rescue, selection, and packing are not represented as a consistent candidate lifecycle. This makes it difficult to explain cases where correct evidence was recalled but dropped later.

## Non-Negotiable Constraints

- Legacy event ordering remains the production default.
- Graph, dual, and hybrid paths remain shadow/replay/flag-only until replay proves parity.
- Do not delete legacy event ordering code in this refactor.
- LLM extractor and LLM router remain out of the realtime main retrieval path.
- No raw user text may be stored in pipeline trace, rule-hit telemetry, replay artifacts, or rule audit.
- Every retrieval behavior change must be measurable with replay artifacts.
- Product-facing errors remain beginner-safe and must not expose tracebacks.
- Existing OpenClaw/Hermes integration remains external; do not modify host source trees.

## Phase 1: Candidate Lifecycle And Traceability

Phase 1 adds a safe lifecycle model around the existing retrieval path. It does not change selected candidates, default graph behavior, or ranking semantics.

New lifecycle stages:

- `recalled`: candidate returned by a provider or rescue path.
- `scored`: candidate received lexical/utility/rerank scores.
- `filtered`: candidate was removed by topic scope, stale-current-value, event-ordering scope, quota, or quality filters.
- `rescued`: candidate was reintroduced by preservation, broad raw recall, scent trail, temporal coverage, aggregation coverage, event-ordering coverage, or quality fallback.
- `selected`: candidate is returned by `search()`.
- `packed`: candidate contributes source spans, facts, events, current views, profiles, or conflicts to `EvidencePack`.

Each lifecycle event stores only structural fields:

- `candidate_id`
- `candidate_type`
- `candidate_source`
- `stage`
- `reason_code`
- `contributed`
- `source_span_ids`
- numeric scores and counts

It must not store candidate text, query text, source span content, prompt text, or raw metadata.

Expected Phase 1 output:

- A `CandidateLifecycleRecord` data model.
- A `CandidateLifecycleRecorder` used inside `MemoryService.search()` and `answer_context()`.
- `coverage["candidate_lifecycle"]` summary counts.
- Sanitized lifecycle entries in debug trace or `pipeline_trace`.
- Replay output that can answer: was the correct evidence recalled, filtered, rescued, selected, or packed?

## Phase 2: Recall Provider Registry

Phase 2 extracts provider orchestration from `candidate_provider.py` into a registry. This changes organization, not retrieval behavior.

Provider declarations:

- provider id
- supported query types
- source family
- production default boolean
- shadow-only boolean
- graph-related boolean
- output source names
- replay category coverage

Initial providers:

- `RawSpanProvider`
- `TopicScopedRawProvider`
- `BroadRawProvider`
- `ScentTrailProvider`
- `FactProvider`
- `EventProvider`
- `CurrentViewProvider`
- `EntityProfileProvider`
- `TemporalCoverageProvider`
- `AggregationCoverageProvider`
- `ContradictionClaimProvider`
- `EventOrderingCoverageProvider`
- `EventOrderingEpisodeProvider`
- `EventOrderingTimelineProvider`
- `GraphShadowProvider`

Production default must continue excluding graph candidates for event ordering. Graph providers can run only for explicit shadow/replay paths.

## Phase 3: Rule Registry Enforcement And First-Pass Cleanup

Phase 3 uses lifecycle/replay evidence to clean rules conservatively.

Rule audit criteria:

- hit count
- contribution count
- negative impact count
- duplicate-of relationship
- ability category
- final answer contribution
- filter/drop contribution

First-pass deletion is allowed only for:

- zero-hit rules across replay inputs
- zero-contribution rules with no protected role
- exact duplicate rules with an equivalent retained rule

Protected rules:

- legacy event ordering fallback rules
- high-precision current-value stale-history rules
- high-precision explicit temporal marker rules
- safety/error guidance rules

Domain label regex should not be deleted immediately. It should move into configurable taxonomy first.

## Phase 4: Temporal Relation Layer

Phase 4 introduces a shared temporal relation layer used by current value, value history, temporal lookup, and event ordering.

Relation types:

- `before`
- `after`
- `supersedes`
- `valid_from`
- `valid_to`
- `changed_from`
- `changed_to`
- `deadline`
- `decision_at`
- `observed_at`

This layer should replace scattered temporal reasoning where possible, but only after replay shows parity. Evidence pack rendering remains separate from temporal reasoning.

## Phase 5: Graph As Structure And Ordering Layer

Phase 5 strengthens event graph and chronology graph as ordering and explanation layers.

Graph responsibilities:

- topic clustering and merge
- episode/phase grouping
- event order explanation
- graph-order + legacy-recall dual ranking
- shadow parity evaluation

Graph non-responsibilities:

- it does not become the default recall backbone
- it does not replace raw evidence
- it does not bypass legacy fallback

Graph promotion gate:

- dual/hybrid must match or exceed legacy on event ordering replay.
- current value, multi-condition, and Chinese recall replay must not regress.
- empty rate must not increase.
- source span coverage must not decrease.

## Phase 6: Real Retrieval Pipeline Execution

Phase 6 turns the current trace-oriented pipeline into a real execution layer.

Target API:

- `QueryUnderstandingEngine.run(query, scope, options) -> QueryUnderstandingResult`
- `RecallOrchestrator.run(context) -> RecallResult`
- `CandidateFusionEngine.run(context, recall_result) -> FusionResult`
- `EvidencePackAssembler.run(context, fusion_result) -> EvidencePack`
- `RetrievalTraceRecorder.flush() -> dict`

`MemoryService.search()` should become a thin orchestrator. It should not own provider-specific business logic.

## Testing Strategy

Every phase must use TDD and replay gates.

Required focused tests:

- lifecycle records contain no raw query or candidate text
- default event ordering excludes graph candidates
- dual shadow does not replace selected candidates
- current value latest state wins over historical conflict
- Chinese recall survives topic/filter stages
- multi-condition evidence is not dropped by fusion
- `answer_context()` updates packed lifecycle counts
- replay artifacts contain sanitized lifecycle and pipeline trace

Required replay gates:

- event ordering four-path replay
- current value replay
- multi-condition replay
- Chinese recall replay
- rule audit over merged replay artifacts

## Success Criteria

Phase 1 succeeds when a failed retrieval can be diagnosed as one of:

- never recalled
- recalled but filtered
- recalled and rescued
- selected but not packed
- packed but insufficient source coverage

Later phases succeed only if replay and focused tests show no regression against the current `main@278c367` baseline.

