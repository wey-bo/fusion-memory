# Fusion Memory Execution Plan

Updated: 2026-06-16

This is the active engineering plan. The long original plan was moved to
`docs/archive/fusion-memory-execution-plan-20260616-long.md`.

## Goal

Build Fusion Memory as a product-quality memory module and raise BEAM 100K
above `0.766` without qid, domain, or gold-answer shortcuts.

## Current Priority

Before further benchmark tuning, reduce architecture drift:

- stop adding retrieval/pack rules directly to `model_adapters.py`;
- split typed evidence construction into section-owned modules;
- preserve broad raw evidence as a first-class floor;
- keep benchmark adapters thin and product-compatible.

## Layer Plan

| Layer | Status | Next Action |
| --- | --- | --- |
| L0 raw evidence | implemented | Keep raw-span provenance and broad recall floor. |
| L1 facts | implemented | Avoid relying on facts without source spans. |
| L2 events | implemented but noisy | Split timeline pack from event extraction. |
| L3 views/profiles | implemented | Keep current-state views out of raw chronology. |
| L4 retrieval orchestration | partially split | Continue moving rescue/preservation policy out of `service.py`. |
| L5 typed packs | partially split | Make event-ordering graph-first; split conflict, summary, instruction modules. |
| L6 model adapters | thin again | Keep adapters limited to prompt/model I/O and compact serialization. |
| L7 eval tooling | usable | Keep probes focused and reproducible. |

## Typed Evidence Contract

The authoritative section list is in
`fusion_memory/retrieval/pack_contract.py`.

Section ownership:

- `raw_evidence`: retrieval floor and provenance.
- `timeline`: topic-scoped chronological user/assistant turns.
- `value_history`: subject-bound current/previous/goal values.
- `aggregation`: count/sum/list items with inclusion roles.
- `temporal`: normalized dates and endpoint roles.
- `conflict`: contradictory claims and resolution context.
- `summary`: issue/resolution pairs and workstream clusters.
- `instruction`: output constraints and user preferences.
- `model_view`: compact serialization for an answer model.

## Refactor Sequence

1. Add contract metadata to every evidence pack. Done.
2. Split `timeline_pack.py` from `structured_annotations.py`,
   `evidence_pack.py`, and `model_adapters.py`.
3. Split `value_history_pack.py` from `evidence_pack.py` and
   `model_adapters.py`. Done.
4. Split `temporal_pack.py` from `evidence_pack.py` and temporal answer
   candidates from `model_adapters.py`. Done.
5. Split candidate source generation from `service.py` into a retrieval
   provider entry point. Done for the list-building entry point; category
   rescue and preservation still need migration.
6. Split aggregation helpers from `model_adapters.py` into retrieval-side pack
   builders. Done.
7. Add contract tests that fail when a typed section is built only in the model
   adapter.
8. Build an event-ordering graph contract: episode/aspect nodes, source-span
   chronology, and ordering/support/refinement edges. Use the current
   `event_ordering_pack.py` heuristics as fallback while graph coverage is
   incomplete.
9. Continue thinning `service.py` by moving post-rerank preservation and
   category rescue into retrieval providers.

## Validation Policy

Use three validation rings:

- Unit tests for each section builder.
- Probe tests for representative BEAM failures, without qid-specific logic.
- Full BEAM 100K only after section-level probes are stable.

Event-ordering validation should separately report graph coverage and heuristic
fallback rate. A score improvement that comes only from more text rules is not
stable enough for the product architecture.

Known sandbox caveat: tests using local `HTTPServer(("127.0.0.1", 0), ...)`
may fail with `PermissionError` in restricted environments. Treat that as a
sandbox limitation unless logic tests fail independently.

## Non-Negotiables

- No qid/domain/gold-answer hardcoding.
- No AGPL code copied from TrueMemory unless licensing is intentionally changed.
- Raw evidence must remain available even when typed extraction is wrong.
- Benchmark improvements must preserve product memory semantics.
