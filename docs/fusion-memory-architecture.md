# Fusion Memory Architecture

Updated: 2026-06-16

The long historical architecture note was archived at
`docs/archive/fusion-memory-architecture-20260616-long.md`. This file is the
active architecture reference.

## Objective

Fusion Memory is a product memory layer for agents, not a benchmark-only RAG
pipeline. The current target is BEAM 100K accuracy above `0.766` while keeping
the product architecture clean enough to extend.

## Layer Model

| Layer | Responsibility | Current State |
| --- | --- | --- |
| L0 evidence | Raw turns, documents, tool results, provenance | Implemented; must remain visible in packs. |
| L1 facts | Add-only durable facts linked to source spans | Implemented. |
| L2 events | Time, order, updates, contradictions | Implemented but event-ordering policy is still noisy. |
| L3 views/profiles | Current state and long-lived profile summaries | Implemented. |
| L4 retrieval | Multi-source candidate generation, quota, rerank | Functional; service layer still owns too much policy. |
| L5 typed packs | Timeline, value history, temporal, aggregation, conflict, summary, instruction | Being split into section modules; event ordering and aggregation now have retrieval-side modules. |
| L6 model view | Compact serialization for answer/judge models | Adapter is thin again; keep new section logic out of it. |

## Non-Negotiable Boundaries

- Raw evidence is the recovery floor. Typed extraction must not hide the source
  span that supports it.
- Facts/events/views are derived objects and must retain source-span links.
- `api/service.py` should orchestrate API calls and retrieval phases; retrieval
  source policy belongs in `fusion_memory/retrieval/`.
- `retrieval/evidence_pack.py` should coordinate typed sections; section logic
  belongs in section modules such as `value_history_pack.py` and
  `temporal_pack.py`.
- `eval/model_adapters.py` should serialize model-facing context and call
  models. It should not be the only place where product evidence is resolved.

## TrueMemory Pro Takeaways

Use the ideas, not the code:

- broad raw retrieval before reranking;
- hybrid sparse/vector recall with normalized merging;
- entity, temporal, and cluster supplements as recall aids;
- raw excerpts preserved into the answer context;
- reranker after wide recall.

AGPL code from TrueMemory is not copied into Fusion Memory.

## Current Refactor State

Completed:

- Evidence packs declare a typed pack contract through
  `retrieval/pack_contract.py`.
- Value-history current/latest resolution moved to
  `retrieval/value_history_pack.py`.
- Temporal date roles, temporal candidates, range pairs, and temporal summaries
  moved to `retrieval/temporal_pack.py`.
- Candidate-source list orchestration moved behind
  `retrieval/candidate_provider.py`.
- Aggregation, preference-constraint, financial, and LLM aggregation model-view
  helpers moved to `retrieval/aggregation_pack.py`.
- Event-ordering model-view sequence construction moved to
  `retrieval/event_ordering_pack.py`.
- Event milestone grouping and representative selection moved to
  `retrieval/event_graph_selection.py`.
- Aggregation key composition is shared through
  `retrieval/aggregation_keys.py`.

Still overgrown:

- `api/service.py`: category-level rescue and post-rerank preservation policy.
- `retrieval/structured_annotations.py`: event-ordering timeline selection and
  labeling under a generic name.
- `retrieval/event_ordering_pack.py`: large temporary home for event-ordering
  heuristics; it should become graph-first instead of growing more rules.
- `retrieval/aggregation_pack.py`: large but section-owned; needs a public
  builder API and contract tests.
- `storage/postgres_store.py` and `storage/sqlite_store.py`: large but mostly
  mechanical; avoid adding retrieval semantics there.

## Next Architecture Moves

1. Make event-ordering graph-first: persist/select topic-scoped aspect/event
   nodes and ordering/support edges, then use heuristics only as fallback.
2. Split reusable timeline graph selection out of `structured_annotations.py`
   and `event_ordering_pack.py`.
3. Continue moving service-level category rescue and post-rerank preservation
   into retrieval providers.
4. Only after these boundaries are stable, run full BEAM 100K and tune by
   category.

## Heuristic Policy

Heuristics are acceptable when they normalize model-view output, parse explicit
query constraints, or suppress low-value spans. They are not acceptable as the
only place where product semantics are recovered. Current event-ordering rules
are too large because they compensate for an incomplete event graph; the next
optimization should add graph structure rather than more adapter-style rules.

## Validation Policy

Use focused section tests first. Run full BEAM 100K only when:

- section modules compile and focused tests pass;
- no new typed evidence rule lives only in `model_adapters.py`;
- broad raw evidence remains present for fallback;
- no qid/domain/gold-answer shortcuts were introduced.
