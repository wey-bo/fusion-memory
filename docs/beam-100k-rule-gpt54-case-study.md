# BEAM 100K Current Case Study

Updated: 2026-06-16

This file is the current BEAM 100K decision record. The full historical log was
moved to `docs/archive/beam-100k-rule-gpt54-case-study-20260616-full-log.md`.

## Current Result

Latest complete run:

- File: `.runtime/beam-runs/current_validation_20260616/full_after_stability_fixes_gpt54.json`
- Workspace: `beam_100k_rule_qwenembed_sessionized_20260612_1745`
- Answer/Judge model: GPT-5.4 compatible endpoint
- Total: `400`
- Accuracy: `0.6504458089516453`
- Answer match rate: `0.73`
- Retryable failures: `0`

Category scores:

| Category | Accuracy |
| --- | ---: |
| abstention | 0.875 |
| contradiction_resolution | 0.6125 |
| event_ordering | 0.1598 |
| information_extraction | 0.7771 |
| instruction_following | 0.6688 |
| knowledge_update | 0.4750 |
| multi_session_reasoning | 0.7178 |
| preference_following | 0.8688 |
| summarization | 0.6685 |
| temporal_reasoning | 0.6813 |

Target remains `>0.766`. The present system is not close enough; event ordering
and knowledge update are the dominant blockers.

## Main Diagnosis

The bottleneck is evidence organization, not answer-model capability alone.

- `event_ordering` often retrieves the right conversation family, but selected
  anchors are noisy, over-broad, or compressed into the wrong sequence.
- `knowledge_update` often retrieves relevant spans, but current/previous/goal
  values are inferred from regexes instead of a durable value-history model.
- Multi-session aggregation, summarization, and temporal reasoning improved
  with typed pack sections, but their logic is now spread across service,
  pack builder, and model adapter code.
- The current adapter is acting as a second pack builder and sometimes as a
  third retrieval system. That makes targeted fixes hard to reason about and
  raises regression risk.

## TrueMemory Pro Lessons

Reference source: `.runtime/references/TrueMemory`.

The BEAM runner stores raw messages, calls:

```python
engine.search_agentic(question, limit=100, use_hyde=True, use_reranker=True)
```

and passes the top `50` raw results to the answer model. In the observed BEAM
path no `llm_fn` is passed, so HyDE/refined-query generation is effectively not
active. The useful ideas to reimplement, without copying AGPL code:

- broad raw retrieval floor before narrow typed extraction;
- hybrid FTS/vector retrieval with RRF;
- normalized scores before merging supplementary sources;
- entity, temporal, and cluster supplements as recall helpers;
- cross-encoder reranking over a broad pool;
- preserve raw evidence rather than over-compressing too early.

Fusion Memory should keep its typed memory ambition, but typed sections must sit
on top of a reliable raw-evidence floor. TrueMemory's main lesson is retrieval
breadth and raw context preservation, not benchmark-specific heuristics.

## Architecture Direction

Use the typed evidence contract in `fusion_memory/retrieval/pack_contract.py`.
Every new BEAM or product optimization should fit one of these sections:

- `raw_evidence`
- `timeline`
- `value_history`
- `aggregation`
- `temporal`
- `conflict`
- `summary`
- `instruction`
- `model_view`

The ownership boundary is:

- `service.py`: orchestration and candidate-source fan-out only.
- retrieval candidate providers: source-specific recall and provenance.
- typed pack builders: section-specific tables and chronology/value models.
- `model_adapters.py`: compact serialization and prompt instructions only.

Do not add new query-category logic directly to `model_adapters.py` unless it is
pure presentation or fallback compaction.

## Next Work

1. Split timeline, value-history, aggregation, temporal, and summary pack logic
   out of `evidence_pack.py` into section modules.
2. Move event-ordering sequence construction out of `model_adapters.py` into a
   `timeline` pack builder.
3. Move value-current resolution out of `model_adapters.py` into a
   `value_history` pack builder with explicit roles:
   `current`, `previous`, `goal`, `deadline`, `rescheduled`, `achieved`.
4. Keep TrueMemory-style broad raw recall as a floor for all high-risk query
   types before typed reranking.
5. Only after those boundaries are stable, resume targeted BEAM optimization.
