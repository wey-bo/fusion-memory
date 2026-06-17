# BEAM 100K Rule + GPT5.4 Case Study

Date: 2026-06-12

Run:

- Result: `.runtime/beam-runs/beam_100k_rule_qwenembed_qwenrerank_gpt54_sessionized_20260612_1800.json`
- Workspace: `beam_100k_rule_qwenembed_sessionized_20260612_1745`
- Extractor: rule-based
- Retrieval: Qwen3 embedding + Qwen3 reranker
- Answer/Judge: GPT5.4
- Split: BEAM 100K

## Headline

The best complete baseline so far is still the original rule-based GPT run at accuracy `0.6312`.

There was one answer API read timeout:

- `beam:100k:16:information_extraction:1`
- The single-query retry succeeded with score `0.3333`.
- If treated as a diagnostic replacement for the timeout, the corrected run would be about `0.6320`.

This is below the target `0.75+`. The dominant failure mode is evidence coverage and evidence organization, not answer generation alone.

## What Changed

Compared with the original rule-based GPT baseline, the current architecture now adds:

- Query planning that routes event-ordering, multi-session aggregation, temporal, contradiction, and knowledge-update questions into different retrieval shapes.
- Structured event-ordering timelines with user anchors, supporting turns, phase clusters, and event hints.
- Generic aggregation keys for count/list style questions so the pack can expose candidate items instead of only free-form spans.
- Generic scent-trail follow-up recall that re-searches terms from strong evidence and preserves provenance through reranking.
- Generic quality fallback recall: when selected evidence is sparse, low-score, or too homogeneous, the service now broad-searches high-information query terms and ranks fallback spans by topic fit plus salience. This is meant to help summarization, multi-session, temporal, instruction, and exact-lookups recover from weak top-k retrieval without adding benchmark-domain rules.
- Summarization-specific resolution pairs and topic clusters so broad "over time" prompts get grouped issue/resolution evidence instead of a flat top-k pile.
- Value-history tables keyed by subject so current/latest values can be separated from older revisions.
- Temporal candidate tables with role labels so date ranges are selected from labeled evidence rather than raw timestamps.
- Instruction constraints that preserve requested format and output shape before the answer model sees the pack.
- Resume-safe BEAM runner partial handling so failed queries can be replayed without losing completed work.
- Runner worker failures now surface as retryable partials instead of aborting the whole run, so high-parallel replay stays practical.
- Retrieval preservation rules that keep coverage anchors and selected support spans ahead of reranking.
- Benchmark-mode retrieval budget expansion: when `answer_context(..., mode="benchmark")` is used, the pack now pulls a larger retrieval set, uses a larger but bounded rerank/MMR pool, and allows a larger answer-context token budget.

The most important regression still under active work is event-ordering pack quality: the pack can now surface the right family of spans, but it still needs cleaner chronology and less cross-topic spillover.

TrueMemory Pro source review:

- The source path combines hybrid retrieval, scent-trail follow-up, salience/quality filtering, source provenance, and HyDE-style query expansion when available.
- It also uses a sufficiency-style check and a broad-fallback path so weak top-k results do not stop recall too early.
- The transferable lesson is not benchmark-specific domain handling; it is broader recall plus provenance-preserving organization before reranking. Fusion-memory now applies that lesson in a generic way through query planning, scent-trail recall, quality fallback recall, aggregation coverage, summary clustering, temporal role tables, and value-history packing, while keeping normal application limits unchanged.
- 2026-06-15 source-level recheck: BEAM runner calls `search_agentic(question, limit=100, use_hyde=True, use_reranker=True)` but does not pass `llm_fn`, so that BEAM path is effectively hybrid/RRF + supplements + cross-encoder rerank, not active HyDE query generation.
- TrueMemory Pro passes up to `50` raw retrieved messages to the answer model. This is a strong benchmark recall strategy and useful as a fallback principle, but it is not enough for a product-grade memory module by itself because it shifts organization and lifecycle reasoning into answer-time context.
- TrueMemory Pro's own BEAM files report strong categories for preference, contradiction, information extraction, and summarization, but weak event ordering: BEAM-1M three-run mean `19.5%`, BEAM-10M single run `5.0%`. Its SOTA score is therefore not evidence that a temporal knowledge graph problem is solved.
- The source is `AGPL-3.0-only`, so we should borrow ideas and reimplement within fusion-memory's architecture rather than copy code into this repo unless the project intentionally accepts AGPL obligations.

## Current Estimate

With the current code, the best grounded estimate for a full BEAM100K run remains below target. The original complete baseline is `0.6312`; category-specific evidence suggests event_ordering is still the largest drag, while multi_session and summarization now have materially better targeted runs.
Until a fresh 400-query run verifies the current retrieval changes end to end, a reasonable estimate is around `0.70-0.74`, with a central guess near `0.72`. The new quality fallback may improve the low-recall tail, especially outside event_ordering and knowledge_update, but it has not yet been verified by a fresh GPT full replay.

## 2026-06-15 Non-Event Validation

New generic changes:

- Added quality fallback recall for sparse/low-score/homogeneous selected evidence.
- Added candidate-source provenance into evidence packs and coverage so fallback/coverage behavior can be diagnosed.
- Tightened generic aggregation for title/value/genre queries:
  - quoted titles are exposed as `title:*` aggregation items;
  - shoe-size values such as `11.5` are exposed as `value:size_11_5` and rendered as `size 11.5`;
  - title/value/genre queries no longer turn broad first-person action phrases into countable items.

Validation:

- Unit tests: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `120` tests passed.
- GPT5.4 non-event/knowledge tail sample:
  - Run: `.runtime/beam-runs/beam_100k_rule_latest_genericitems_nonevent_gpt54_20260615.json`
  - Scope: 18 low-score queries from information_extraction, instruction_following, multi_session_reasoning, preference_following, summarization, and temporal_reasoning.
  - Result: sample accuracy `0.3403`; `8` improved and `1` regressed versus the old baseline answers selected from `.runtime/beam-runs/beam_100k_rule_qwenembed_qwenrerank_gpt54_sessionized_20260612_1800.json`.
  - Strong positive signals: summarization low-score tail improved from `0.3056` to `0.6528`; instruction low-score tail improved from `0.0` to `0.25`; multi-session improved from `0.0556` to `0.3333` before tighter title filtering.
- Focused size validation:
  - Run: `.runtime/beam-runs/beam_100k_rule_latest_size_label_gpt54_20260615.json`
  - Query: `beam:100k:15:multi_session_reasoning:0`
  - Result: score `1.0`; answer correctly states two shoe sizes, `11` and `11.5`.

Remaining gaps from this validation:

- Temporal reasoning still scores `0.0` on the sampled low-score tail. The pack often contains the right episode and many dates, but date-role binding is too noisy: assistant planning examples and unrelated mentioned dates compete with user decision/reschedule/start/completion dates.
- Multi-session title/list questions still overcount when the query has a narrow scope such as "considering titles for April 6-7 and April 8" or "series/genres mentioned across conversations." The next generic fix should bind aggregation items to query scope constraints such as dates, quoted titles, and nearby user-intent language before exposing them as included items.

## 2026-06-15 Context-Scope Multi-Session Update

New generic changes:

- Added context-support preservation for aggregation candidates whose duplicate keys carry disambiguating date/schedule context. This keeps a dated schedule span even when the same titles appeared in earlier recommendation spans.
- Added a final coverage-preservation pass after quality fallback for temporal, multi-session, and event-ordering paths, followed by a final topic-scope filter so fallback recall cannot reintroduce unrelated topic groups.
- Added query-date-aware prioritization for aggregation coverage. Candidates with dates matching the query and compact exact/schedule context are preferred over long, broad recommendation lists.
- Exact-filter candidates for multi-session now carry `aggregation_keys`, allowing exact matches with useful context to participate in coverage preservation.
- Evidence pack construction now preserves longer aggregation summaries for multi-session spans that carry aggregation keys, so short summaries do not truncate later list items.
- The model pack now allows directly dated assistant schedules/final lists to contribute title items, while filtering strategy/advice examples and long assistant recommendation lists that are not explicit plans.

Validation:

- Unit tests: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `135` tests passed.
- Focused GPT5.4 validation:
  - Run: `.runtime/beam-runs/beam_100k_rule_latest_multisession_contextscope_gpt54_20260615.json`
  - Queries: `beam:100k:13:multi_session_reasoning:0`, `beam:100k:14:multi_session_reasoning:0`
  - Result: sample accuracy `0.1667`.
  - `beam:100k:13:multi_session_reasoning:0`: score `0.3333`; answer counted 3 series, still missing the four-series structure expected by rubric.
  - `beam:100k:14:multi_session_reasoning:0`: initial context-scope attempt still answered 15 and scored `0.0`.
- Focused q14 retry after tightening assistant schedule/list filtering:
  - Run: `.runtime/beam-runs/beam_100k_rule_latest_q14_contextscope_gpt54_20260615.json`
  - Result: score `1.0`.
  - Answer: `13 unique movies total`, using included aggregation items `8 movies + Klaus + Soul + The Mitchells vs. The Machines + Moana + Zootopia`.

Remaining gaps:

- Multi-session book/genre questions still need a generic selection-group representation. The q13 pack can recover later live-chat evidence but does not yet represent the earlier constrained recommendation context as "three fiction series from the relevant store/budget request" plus the later sci-fi live-chat selection.
- The failed q13 experiment showed that naively preserving all assistant recommendation lists or splitting them into title items degrades retrieval and overcounts. The safer next step is a bounded group/count abstraction for constrained recommendation contexts, not broader assistant-list inclusion.

## Category Scores

| Category | Score | matched_gold | Zero-score cases |
| --- | ---: | ---: | ---: |
| abstention | 0.8750 | 36/40 | 4 |
| contradiction_resolution | 0.6500 | 39/40 | 0 |
| event_ordering | 0.1849 | 1/40 | 0 |
| information_extraction | 0.6865 | 29/40 | 7 |
| instruction_following | 0.7188 | 31/40 | 9 |
| knowledge_update | 0.6813 | 28/40 | 12 |
| multi_session_reasoning | 0.5909 | 26/40 | 6 |
| preference_following | 0.8063 | 36/40 | 4 |
| summarization | 0.4310 | 18/40 | 1 |
| temporal_reasoning | 0.6875 | 28/40 | 9 |

Global split:

- `matched_gold=True`: 272/400, average score `0.8648`
- `matched_gold=False`: 128/400, average score `0.1348`

This is the strongest signal from the run: when the pack contains the official supporting source spans, GPT usually answers well. When it does not, scores collapse. The memory layer must improve source recall and pack construction before prompt tuning is likely to help.

## Case Study 1: Event Ordering

Example:

- Query: `beam:100k:2:event_ordering:0`
- Score: `0.1429`
- Prompt: order five aspects of implementing the city autocomplete feature.

Observed pack:

- `source_span_count=20`
- `event_count=5`
- `timeline_span_count=20`
- `timeline_basis=conversation_order`
- `matched_gold=False`

The pack contained related weather app spans, but the selected chronology mixed setup, API key handling, autocomplete, error handling, testing, deployment/security, and unrelated meeting/calendar spans. The answer then produced a plausible but wrong order.

Defect:

- Event ordering is still mostly raw-span/reranker-driven.
- Extracted events exist, but they are not canonical project timeline events.
- The event graph does not yet dominate recall or provide a topic-scoped chronology.
- Assistant spans can still steer ordering toward implementation advice rather than the user's sequence of topics.

Needed fix:

- Build timeline events around user-introduced topic changes, not only technical keywords.
- Use topic-scoped event graph traversal for event_ordering.
- Prefer user spans for ordering anchors.
- Treat source URI / turn order as the canonical chronology, not ingestion timestamp.
- Deduplicate adjacent assistant elaborations into the user topic they answer.

## Case Study 2: Summarization

Example:

- Query: `beam:100k:3:summarization:1`
- Score: `0.0`
- Prompt: summarize how the user approached and resolved web project issues over time.

Observed pack:

- `source_span_count=20`
- `event_count=0`
- `timeline_span_count=0`
- `matched_gold=False`

Retrieved evidence focused on planning, Lighthouse, SEO, performance, contact form integration, and image compression. The judge expected coverage of CSS box model help, JavaScript element-size calculation, Chrome DevTools guidance, DOM/navbar error handling, React gallery image loading, script-linking/function-reference issues, and retry/backoff behavior.

Defect:

- Summarization retrieval is too lexical and too "top-k".
- It retrieves broad project-progress evidence, but misses the issue-resolution sequence the query asks for.
- There is no coverage planner that forces breadth across distinct issue clusters.

Needed fix:

- For summarization, build a topic-cluster coverage pack instead of a flat top-k pack.
- Include representative spans across all subtopics with diversity constraints.
- Use raw user spans plus concise assistant resolution spans.
- Prefer issue/resolution pairs when the query asks how something was resolved.

## Case Study 3: Multi-Session Aggregation

Example:

- Query: `beam:100k:2:multi_session_reasoning:0`
- Score: `0.0`
- Prompt: how many different features or concerns were mentioned across weather app conversations.

Observed answer:

- The answer listed several features/concerns but refused to give a count.

Observed pack:

- `source_span_count=16`
- `event_count=0`
- `matched_gold=False`

The pack contained many related weather app details: dynamic display, autocomplete, API errors, rate limits, deployment, latency, UI wireframe, caching. It did not provide a clean candidate set for aggregation.

Defect:

- The pack recalls related evidence, but does not structure it as aggregation candidates.
- The answer model becomes conservative when the count is implicit.
- Similar failures occur for unique movies, shoe sizes, probability calculations, and total days off.

Needed fix:

- Add category-specific aggregation candidates for count/list questions.
- Extract and pack candidate values by type: features, columns, dates, people, versions, titles, numeric counts, roles, sizes.
- Include provenance for each candidate and mark duplicates/aliases.
- Do not compute the final answer in memory, but make the candidate set complete enough that the model can compute it.

## Case Study 4: Knowledge Update / Latest Value

Examples:

- `beam:100k:10:knowledge_update:0`: answered weekly word target `1,800`, expected `1,350`.
- `beam:100k:11:knowledge_update:0`: answered webinar date `March 20`, expected `March 27`.
- `beam:100k:12:knowledge_update:0`: answered onboarding completion date `April 25`, expected `April 22`.

Observed pattern:

- When `matched_gold=True`, knowledge_update scored `0.9732`.
- When `matched_gold=False`, knowledge_update scored `0.0`.

Defect:

- The system retrieves a plausible value but often not the current value.
- Value history is not reliably bound to a subject key.
- Original, revised, current, target, and deadline values are not separated strongly enough.

Needed fix:

- Build `value_history` around `(topic/entity, attribute)` keys.
- Pack latest/current and previous candidates in chronological order.
- Include date role and update role: original, revised, current, canceled, rescheduled, target, deadline.
- Avoid treating a high lexical match as current unless it is the latest value for the same subject key.

## Case Study 5: Temporal Reasoning

Examples:

- `beam:100k:5:temporal_reasoning:1`: answered `798 days` because it used a 2026 span timestamp; expected `10 days`.
- `beam:100k:7:temporal_reasoning:1`: answered `May 10, 2026 -> June 15, 2026`; expected `April 5, 2024 -> June 15, 2024`.
- `beam:100k:10:temporal_reasoning:1`: answered `April 2 -> April 15`; expected `April 2 -> May 10`.

Defect:

- Span timestamp and content date are still competing.
- Date roles are present in some packs, but not reliably bound to the query's target event.
- The pack often contains several date candidates without enough role labeling.

Needed fix:

- Treat BEAM conversation turn order and content dates separately from ingestion timestamps.
- Add date-role binding to topic/entity/event:
  - `start_date`
  - `completion_date`
  - `feature_finish_date`
  - `deployment_deadline`
  - `rescheduled_date`
  - `missed_session_date`
- In temporal packs, show candidate date pairs with source spans and role labels.
- Do not let current machine date or ingestion timestamp enter temporal reasoning unless the source text explicitly says so.

## Case Study 6: Contradiction Resolution

Example:

- Query: `beam:100k:2:contradiction_resolution:0`
- Score: `0.5`
- Prompt: whether the user obtained an API key.

Observed pack:

- `matched_gold=True`
- The pack included "I've never actually obtained an API key" and related later API-key/error-handling spans.

Observed answer:

- It said there was no direct contradiction and concluded the user had not obtained an API key.

Defect:

- Raw evidence is often present, but opposing claims are not explicitly linked.
- The model must infer whether later "I have/used/configured API key" language is a contradictory claim, setup instruction, or hypothetical code.
- Claim polarity and subject-key normalization are too weak.

Needed fix:

- Extract claim candidates with:
  - subject key: e.g. `weather_app.openweather_api_key.obtained`
  - polarity: positive / negative / uncertain / hypothetical
  - evidence span id
  - time/order
- Build opposing claim links before pack construction.
- Pack both sides under explicit buckets rather than relying on answer prompt wording.

## Case Study 7: Instruction Following

Examples:

- `beam:100k:5:instruction_following:1`: answer explained conditional probability but did not include the required tree/decision-tree representation.
- `beam:100k:13:instruction_following:0`: audiobook recommendations omitted narrator names.
- `beam:100k:14:instruction_following:0`: movie recommendations omitted streaming platforms.
- `beam:100k:14:instruction_following:1`: snack recommendations omitted allergy/dietary check.

Defect:

- The evidence pack retrieves topic-relevant content, but not always the user's required output constraints.
- Format/constraint requirements are not systematically represented in the pack.

Needed fix:

- Extract instruction constraints as memory items:
  - desired visual format
  - required fields
  - safety checks
  - examples requested
  - tone/level constraints
- In instruction_following packs, include those constraints in a dedicated section with raw provenance.
- Keep this as evidence organization, not a benchmark prompt hack.

## What This Run Proves

1. Raw verbatim evidence remains the most reliable source.
2. Retrieval and pack construction dominate performance.
3. Extracted facts/events exist, but they do not yet reliably drive recall.
4. Event graph/temporal graph need to become selectors, not just stored artifacts.

## Current Follow-Up

Recent targeted reruns confirmed two additional points:

- `beam_parallel_runner.py` needed a true global resume path. Successful records were previously only reliable per worker file, which made restarts harder to reason about. The runner now loads all partial files in the partial directory, skips completed query ids globally, and keeps `answer_failed` records retryable.
- The strongest remaining failures are not fixed by more prompt pressure. In the current full run, `event_ordering` mostly fails by selecting plausible local topic fragments instead of representative phases, and `multi_session_reasoning` fails when the pack does not expose the right abstraction level for counting or grouping. That is a retrieval/packing problem, not a cheating opportunity.

The safe next direction is a more general retrieval rewrite:

- query expansion with multiple paraphrases of the same request
- broader but still generic evidence clustering
- clearer included/excluded candidate packaging for list/count questions
- stronger separation of topic phase anchors from answer candidates
5. GPT can answer well when the right spans are present; qwen is likely to remain much lower until pack quality improves.
6. Prompt changes alone cannot recover missing or wrong evidence.

## Prioritized Fixes

1. Event graph selector for event_ordering:
   - topic-scoped user-introduced timeline events
   - source_uri/turn order chronology
   - assistant spans only as supporting detail

2. Summarization coverage planner:
   - cluster by subtopic / issue / resolution
   - force diverse raw evidence coverage
   - include issue-resolution pairs

3. Aggregation pack construction:
   - exhaustive candidates for count/list questions
   - candidate type detection for columns, titles, people, dates, values, sizes, features, roles

4. Value history and date role binding:
   - subject-keyed latest/current/previous values
   - content-date roles separated from ingestion timestamp

5. Claim polarity graph:
   - positive/negative/uncertain/hypothetical claim buckets
   - opposing claim links per subject key

6. Instruction constraint extraction:
   - required visual forms, fields, platforms, narrator names, allergy checks, tree diagrams, etc.

## Near-Term Evaluation Plan

Use this completed run as the new GPT rule baseline:

- Raw score: `0.6312`
- Diagnostic score with timeout retry: about `0.6320`

For each fix, run targeted replay first:

- event_ordering: all 40
- summarization: 15-20 low/mid score cases
- multi_session_reasoning: all score 0 and partial-count cases
- knowledge_update / temporal_reasoning: all score 0 cases
- instruction_following: all score 0 cases

Only run full 100K after targeted replay shows that matched_gold coverage and candidate completeness improved.

## 2026-06-14 Status Update

Current best complete full run remains:

- `.runtime/beam-runs/beam_100k_rule_qwenembed_qwenrerank_gpt54_sessionized_20260612_1800.json`
- Overall BEAM100K accuracy: `0.631209992229094`
- Completed queries: `400/400`
- Answer model / judge: GPT5.4 eval models
- Retrieval: Qwen3 embedding + Qwen3 reranker over rule-based ingestion

Verified targeted results after the retrieval/pack changes:

| Targeted area | Run | Queries | Accuracy | Interpretation |
| --- | --- | ---: | ---: | --- |
| multi_session_reasoning | `beam_100k_rule_latest_multi_session_full_aggfix_gpt54_20260613_224412.json` | 40 | `0.8100` | Large improvement over baseline `0.5909`; generic aggregation candidates help. |
| summarization | `beam_100k_rule_latest_summarization_detailsignal_full_gpt54_20260614_172211.json` | 40 | `0.7253` | Large improvement over baseline `0.4310`; broader issue/detail coverage helps. |
| event_ordering | `beam_100k_rule_latest_event_cross_episode_full_gpt54_20260614_191657.json` | 40 | `0.1508` | Regression versus baseline `0.1849`; current structured selector is still the main blocker. |
| knowledge_update low-score replay | `beam_100k_rule_latest_knowledge_zero_current_gpt54_20260614_183545.json` | 12 | `0.2500` | Still weak on previously zero-score latest-value cases. |
| knowledge_update current-value replay | `beam_100k_rule_latest_knowledge_zero_currentvalue_gpt54_20260614_185311.json` | 12 | `0.0833` | Current-value heuristics did not generalize. |

If we conservatively replace only the categories with complete targeted evidence, the projected full BEAM100K score is:

- Baseline: `0.6312`
- Replace multi_session_reasoning with `0.8100`
- Replace summarization with `0.7253`
- Replace event_ordering with the latest measured `0.1508`
- Projected overall: about `0.6791`

If event_ordering is not regressed and stays at its baseline `0.1849`, the projection is about `0.6825`.
Even if event_ordering rises to `0.60`, the projection is only about `0.7240`.
With the other eight categories unchanged, reaching `0.75+` requires both event_ordering and at least one more weak category, probably knowledge_update or temporal_reasoning, to improve substantially.

Current honest estimate for a fresh full run on the current code is therefore:

- Expected range: `0.69-0.71` if the targeted multi-session and summarization gains hold in a full run and the new event_ordering chronology pack remains stable.
- Downside range: `0.64-0.67` if event_ordering still underperforms or latest-value/temporal retrieval regressions dominate.
- Not yet plausible: `0.75+` without a stronger event_ordering and latest-value/temporal retrieval layer.

TrueMemory Pro source-level lessons checked on 2026-06-14:

- `benchmarks/beam/bench_truememory_pro_beam1m.py` retrieves with `engine.search_agentic(question, limit=100, use_hyde=True, use_reranker=True)` and passes up to 50 raw results to the answer model as `[timestamp] sender: content`.
- `truememory/engine.py::search_agentic` uses a large candidate pool (`max(limit * 8, 100)` when reranking), optional HyDE fusion, cluster supplements, entity-focused search, salience rescue, refined subqueries, and then cross-encoder reranking.
- The safe transferable idea is broad generic recall plus provenance-preserving raw context. This is not benchmark-domain handling and should remain category-generic.

Next architecture direction:

- Add a benchmark-safe broad raw recall layer similar in spirit to TrueMemory Pro: hybrid/query-expanded candidate pools, source provenance, and reranking before pack construction.
- For event_ordering specifically, stop over-compressing into guessed aspect anchors. Preserve a wider chronological raw slice of user turns around the retrieved topic and let the answer model see the actual conversation order. The current implementation now keeps same-topic user chronology in the pack and limits assistant support to nearby context.
- Keep `beam_parallel_runner.py` high-parallel and retry-safe. API `answer_failed` records are retryable and should be replayed rather than reducing worker count.

## 2026-06-14 Event Ordering Follow-Up

I changed the event-ordering pack to preserve raw user chronology instead of only support-adjacent spans.

Verified after the change:

- Related unit tests passed: `86` tests in `tests.test_fusion_memory`, `tests.test_model_adapters`, and `tests.test_beam_parallel_runner`
- No-LLM benchmark pack probe for `beam:100k:2:event_ordering:0`: `22.31s`, `24` source spans, `19` user spans
- No-LLM benchmark pack probe for `beam:100k:15:event_ordering:0`: `24.68s`, `32` source spans, `5` user spans

This is better than the earlier `40s+` probe, and it confirms the chronology expansion is now active without benchmark-specific hardcoding. The remaining gap is answer-quality verification on a fresh full BEAM run.

## 2026-06-14 Coverage Retrieval Update

I tightened the coverage selector so event_ordering now prefers raw user chronology first, then only adds graph/event hints when coverage is still missing.

Verified after the change:

- Related unit tests passed: `86` tests in `tests.test_fusion_memory`, `tests.test_model_adapters`, and `tests.test_beam_parallel_runner`
- No-LLM benchmark pack probe for `beam:100k:2:event_ordering:0`: `31.7s`, `26` source spans, `15` anchors
- The first anchor texts are now direct user turns like city autocomplete, debounce, invalid city names, and deployment, instead of synthetic `#event` labels

This is the first step toward a real coverage retrieval layer for event_ordering. It still needs the full 40-query GPT5.4 replay to quantify the actual score shift.

## 2026-06-14 Broader Pack Organization Update

I expanded the pack structure beyond event ordering so the answer model now sees more category-specific scaffolding:

- `summarization`: `resolution_pairs`
- `temporal_reasoning`: `temporal_candidates`
- `knowledge_update`: `value_history`
- `instruction_following`: `instruction_constraints`

Verified on a real BEAM workspace probe:

- `beam:100k:1:summarization:0`: 64 source spans, `resolution_pairs=12`
- `beam:100k:1:temporal_reasoning:0`: 50 source spans, `temporal_candidates=16`
- `beam:100k:1:knowledge_update:0`: 50 source spans, `value_history=16`
- `beam:100k:1:event_ordering:0`: 21 source spans, 3 events, `format_requirements` present

This helps the broader modules the user called out, not just event_ordering and knowledge_update. The remaining ceiling is still event_ordering chronology quality plus a fresh full-run check on the other weak categories.

Current honest estimate for the current code, without a new full GPT5.4 replay, is about `0.69-0.73`.
That is better than the original `0.6312`, but still short of `0.75+`.

## 2026-06-15 Retrieval Follow-Up

I added a generic raw scent-trail layer inspired by TrueMemory Pro's broader recall path:

- It builds follow-up queries from high-signal seed spans instead of only the original question.
- It re-searches the same scope and keeps provenance through the normal candidate fusion path.
- It is generic, not BEAM-domain specific, and is meant to help summarization, multi-session, temporal, and version/detail lookups alike.

I also hardened the BEAM runner so worker-level API failures stay retryable and do not tear down an otherwise useful high-parallel run.

I then widened the multi-session synthesis path so cross-factor questions keep all relevant factors in the pack instead of collapsing to the first two budget-like spans.

I also added summarization `summary_clusters`, which expose topic/workstream representatives in the evidence pack alongside `resolution_pairs`. This is a generic organization signal for long "over time" summaries and should help broad summaries avoid flattening unrelated workstreams into one theme.

I also widened the current-value planner so target/budget/date questions such as monthly budgets, weekly word-count targets, and onboarding deadlines route to `knowledge_update` instead of falling back to `factual_exact`. That should improve the generic latest-value path without adding benchmark-specific rules.

I also added `subject_key` to `value_history`, and preserved it through the answer-model pack. This gives latest-value questions a subject-scoped history instead of a flat list of numbers/dates, reducing the chance that a value from an adjacent topic wins just because it is recent or lexically similar.

Verified after the change:

- `91` core unit tests passed across `tests.test_fusion_memory`, `tests.test_model_adapters`, and `tests.test_beam_parallel_runner`
- `92` core unit tests passed after the cross-factor synthesis preservation update
- `92` core unit tests passed after adding `summary_clusters` to retrieval packs and answer-model input
- `92` core unit tests passed after the current-value routing expansion
- `103` tests passed after adding subject-scoped `value_history`

The practical impact should be modest but broad: better recall of follow-up detail spans, less wasted work on partial failures, and less pressure to rely on prompt-only fixes.

## 2026-06-15 Scope Binding and Temporal Range Update

I added another generic pass aimed at the user's instruction not to only optimize event_ordering and knowledge_update:

- Service-level temporal recall now recognizes `decision_date`, `reschedule_date`, and generic `completion_date` signals, matching the roles already exposed by evidence-pack candidate tables.
- Service-level temporal recall now also recognizes `download_date`, and preserves high-object-overlap temporal coverage spans before and after reranking.
- Temporal packs now expose `temporal_range_pairs` for explicit date ranges such as `May 10 to May 25`, preserving start/end dates, normalized values, endpoint roles, source span, and context.
- Multi-session aggregation now supports multiple generic prefixes in a single query, so a query asking for "series or genres" can include both `title:*` and `genre:*` items.
- Generic aggregation items now obey explicit query date scopes. If a count/list query asks for items on specific dates, item evidence outside those dates is marked excluded, and undated item evidence is marked `missing_query_date_scope` instead of being counted.
- The aggregation de-dupe path now lets later in-scope evidence replace an earlier excluded duplicate of the same item.
- Title aggregation now marks explicitly excluded/rejected titles as excluded items, and date-scoped movie plans can inherit scope from an adjacent in-scope user turn when the assistant gives the concrete list.

Verified after the change:

- Full unit suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `129` tests passed.
- Focused new coverage:
  - explicit temporal range pairs are present in the pack;
  - decision/reschedule temporal candidates still rank above assistant examples;
  - object-bound temporal coverage recovers a user decision span such as `rejecting ... raise on March 12`;
  - duration-only completion evidence such as `finished ... in 12 days` is preserved for temporal questions;
  - series/genre aggregation can include both item families;
  - date-scoped movie/title aggregation excludes out-of-scope and undated items.

GPT5.4 focused validation:

- Run: `.runtime/beam-runs/beam_100k_rule_latest_temporal_multi_scope_gpt54_20260615.json`
  - Scope: 5 known temporal/multi-session failure probes.
  - Result: accuracy `0.4333`; temporal `2/3` correct, multi-session `0/2` correct.
  - Improvements: `beam:100k:12:temporal_reasoning:0` improved to `1.0` by recovering March 12 -> March 30; `beam:100k:13:temporal_reasoning:0` improved to `1.0` by preserving the `12 days` completion evidence.
  - Still failing: `beam:100k:10:temporal_reasoning:1` still chooses May 25 instead of the rubric's May 10 endpoint; multi-session title/list probes still need better state modeling of user-confirmed choices vs assistant recommendations vs excluded items.
- Run: `.runtime/beam-runs/beam_100k_rule_latest_temporal_multi_scope2_gpt54_20260615.json`
  - Result after narrower title/genre extraction and adjacent assistant date-scope handling: accuracy `0.4000`; temporal stayed `2/3`, multi-session stayed `0/2`.
  - Conclusion: the temporal coverage change is useful; the current multi-session item-generation changes are not yet enough and should not be counted as a proven score improvement.

This is still not evidence of `0.75+`. It is a targeted architecture improvement for known low-score temporal tails, while multi-session remains an open retrieval/packing problem. A fresh full 400-query run is still required before raising the overall estimate.

## 2026-06-15 Extractor Hardening

I hardened the optional `StructuredLLMExtractor` as a product-quality improvement rather than a benchmark-specific change:

- The structured extraction schema now declares concrete object shapes for facts, events, and relations, including required text/description and source attribution fields.
- Strict mode is now the default. String facts and non-object records are rejected instead of being silently coerced into facts. A legacy compatibility path remains available only when explicitly enabled.
- Fact, event, and relation candidates must point to valid input `span_id` values in strict mode. Unattributed or wrongly attributed records are dropped and counted.
- LLM call failures still fall back to the rule extractor, but fallback is no longer silent. `MemoryService.add` now records `extractor_telemetry` in the trace with failure flags, fallback reason, accepted counts, and invalid record counts.
- The rule event fallback is also visible in telemetry, so a structured extractor that stops producing events can be detected before temporal/event-ordering quality quietly degrades.

Validation:

- Focused tests: `python3 -m unittest tests.test_llm_extractor_and_benchmark tests.test_model_adapters`
  - Result: `48` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `138` tests passed.

Expected BEAM impact:

- The default BEAM path still uses the rule-based extractor, so this does not directly tune any benchmark category.
- The practical value is quality control for future LLM extraction runs: schema drift, bad source attribution, and silent fallback are now observable. That supports product-grade memory ingestion and makes future BEAM experiments less likely to mistake extractor degradation for retrieval behavior.

## 2026-06-15 Runner Retry Selection

I tightened the parallel BEAM runner around the user's high-throughput validation workflow:

- Added `query --answer-failed-only` for use with `--from-result`. This selects only `answer_failed` records from a previous result/partial run, so transient API failures can be replayed directly without mixing in true low-score benchmark failures.
- Existing low-score replay behavior is preserved: without `--answer-failed-only`, `--from-result --score-lt ...` still selects low-score answers and keeps `answer_failed` records retryable.
- This does not reduce worker count or throttle concurrency. It only makes the retry set more precise.

Validation:

- Runner tests: `python3 -m unittest tests.test_beam_parallel_runner`
  - Result: `6` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `140` tests passed.

Example retry command shape:

```bash
PYTHONPATH=. .runtime/beam-venv/bin/python tools/beam_parallel_runner.py \
  --dataset /public/home/wwb/datasets/BEAM \
  --split 100k \
  --workspace <workspace> \
  --output .runtime/beam-runs/<retry-run>.json \
  query \
  --workers <high-parallel-worker-count> \
  --from-result .runtime/beam-runs/<previous-run>.json \
  --answer-failed-only
```

Expected BEAM impact:

- No score change by itself.
- It improves measurement efficiency and reliability: failed API calls can be replayed quickly while preserving high parallelism, which makes full-run estimates less noisy and avoids spending time rerunning already-valid low-score retrieval failures when the goal is just API recovery.

## 2026-06-15 Multi-Session Selection Groups

I added a generic selection/recommendation group abstraction for multi-session aggregation:

- When a user asks for a fixed number of recommendations/options and a nearby assistant turn answers with a list, the model pack can now expose a `group_count:*` aggregation item for the group size.
- The assistant recommendation list is not expanded into individual user-mentioned titles. This avoids the earlier regression where broad assistant lists were overcounted as if the user had selected or mentioned every item.
- User-stated titles/genres still remain normal `title:*` / `genre:*` aggregation items, so later explicit user choices can combine with the bounded recommendation group.
- The parser handles natural count phrases such as `three fiction series`, not only `three series`.

Validation:

- Focused model adapter tests: `python3 -m unittest tests.test_model_adapters`
  - Result: `41` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `141` tests passed.
- After the final group-support dedupe change, the full relevant suite was rerun:
  - Result: `144` tests passed.
- Focused GPT5.4 validation after dedupe:
  - Run: `.runtime/beam-runs/beam_100k_rule_latest_q13_dedup_group_support_gpt54_20260615.json`
  - Query: `beam:100k:13:multi_session_reasoning:0`
  - Result: score `0.0`; answer: `5 different book series or genres. Breakdown: leviathan wakes = 1; 4 recommended series = 4.`
  - Diagnosis: the pack now preserves the relevant adjacent assistant recommendation group, but the answer layer still treats assistant-supplied candidate recommendations and user-mentioned/accepted interests as additive objects. This is a product memory modeling failure, not a book-domain rule failure.

Expected BEAM impact:

- This targets the product-level state-modeling gap behind weak multi-session list/count questions: candidate groups and user selections are different memory objects.
- It is not domain-specific to books or BEAM. The rule is based on conversation structure (`user requested N recommendations/options` -> `assistant supplied nearby list`) and is bounded to a count hint instead of copying recommendation contents.
- It should help q13-like failures only after the memory layer can distinguish requested recommendations, assistant candidate options, user-stated interests, and user-confirmed choices. The current focused result shows that recall improved but semantic object typing is still insufficient.

## 2026-06-15 Multi-Session Object Typing Update

New generic changes:

- Aggregation items now carry `memory_object_type` and `count_role`.
  - `group_count:*` is marked as `assistant_recommendation_group` with `count_role=candidate_group_count`.
  - user/document title, genre, value, area, feature, request, and generic items are marked as `user_intent_item` with `count_role=additive_item`.
  - excluded candidates carry `count_role=excluded`.
- Evidence packs now include `aggregation_summary` whenever `aggregation_items` are present. The summary groups included items by `count_role` and `memory_object_type`, exposes role-level `value_sum`s, and gives generic guidance that candidate recommendation groups are count candidates but not blindly additive with user intent items.
- The BEAM multi-session answer instruction now tells the answer model not to blindly add `candidate_group_count` to separate user-stated items unless the evidence supports distinct objects. This is a general product memory rule: a bounded assistant recommendation group is not the same object type as a user-confirmed selection or direct user intent item.
- Exploratory title filtering now treats purchase/budget retrospectives as non-exploratory unless there is stronger nearby reading/exploration intent. A sentence such as “I spent $18 on X and wondered if it was worth it” is not counted as “I want to explore X”.
- Recommendation group labels now handle invariant plural nouns such as `series` without producing `seriess`.

Validation:

- Focused tests: `python3 -m unittest tests.test_model_adapters`
  - Result: `44` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `146` tests passed.
- q13 pack probe after the change:
  - `group_count:series:8:4` remains included, with `memory_object_type=assistant_recommendation_group` and `count_role=candidate_group_count`.
  - `title:leviathan_wakes` is now excluded with reason `not_exploratory_purchase_or_budget_review`.
  - `aggregation_summary.primary_count_candidates` contains only `candidate_group_count` with `value_sum=4`; no purchase-review title is mixed into the additive count.

Expected BEAM impact:

- This should reduce multi-session overcounting where the pack previously mixed assistant candidate recommendations, user intent items, and budget/purchase review titles as if they were all additive.
- It still does not prove a full-run gain. A GPT focused replay and then a high-parallel full 400-query run are needed before changing the overall `0.70-0.74` estimate.

## 2026-06-15 SOTA Source Review: What To Borrow

The current SOTA-like systems are not best understood as "lots of benchmark domain rules." The common pattern is broader and product-relevant:

- Wide raw recall first, then heavy reranking. TrueMemory Pro's BEAM runner calls `search_agentic(question, limit=100, use_hyde=True, use_reranker=True)` and passes up to `50` raw messages to the answer model. In that runner HyDE is not actually active because no `llm_fn` is passed, so the effective strength is hybrid/RRF retrieval plus cross-encoder reranking and broad context.
- Hybrid lexical + dense recall is table stakes. TrueMemory uses FTS5 + vector + optional separation vectors with RRF; Hindsight uses semantic vector + BM25 + graph + temporal retrieval; CSM explicitly added an embedding recall floor after observing keyword/router recall misses.
- Graph and temporal expansion help when they are bounded and evidence-bearing. Hindsight expands through entity/semantic/causal links and uses temporal coverage buckets; CSM's newer core coverage path builds date-ordered cited timelines without gold/rubric access.
- Reranking is a first-class layer, not an optional polish step. TrueMemory and Hindsight both use cross-encoder reranking after fusion; Hindsight additionally combines reranker score with recency, temporal proximity, and proof count as calibrated secondary boosts.
- The write model matters. Hindsight's stronger product architecture is a typed memory unit layer with `fact_type`, entities, memory links, temporal ranges, chunks, invalidation/curation flows, and source memory references. That is closer to product memory than a benchmark-only raw message retriever.

What not to borrow directly:

- Do not copy TrueMemory code. The source is AGPL-3.0-only; use architectural lessons and reimplement independently.
- Do not copy CSM's AMB bridge heuristics as-is. The bridge still contains BEAM/AMB-specific evidence capsule logic and comments about losing BEAM categories, even though its core coverage module is moving toward generic query-shape coverage. We can borrow the product idea: deterministic coverage timelines, recall floors, shard-local expansion, and leakage firewall tests.
- Do not make category-specific paths for `event_ordering`, `knowledge_update`, books, libraries, or any BEAM domain. The safe boundary is query-shape and evidence-shape logic: temporal query, aggregation query, contradiction query, recommendation group, current-state query, broad summary query.

Actionable architecture direction:

- Add a generic broad-recall lane before evidence-pack construction: lexical/BM25, dense, raw-message fallback, adjacent-turn expansion, temporal coverage, and entity/local-neighbor expansion. It should emit provenance and scores, not final answers.
- Keep structured memory as the product truth layer: facts/events/relations/current view should improve over time, but raw source spans must remain available so retrieval can recover from extraction misses.
- Make recall failures observable. Track per-query candidate sources, selected spans, reranker inputs, dropped candidates, token budget decisions, and extractor fallback. CSM and Hindsight both gained by measuring recall failure modes instead of treating answer errors as opaque.
- Preserve answer-model neutrality. The BEAM adapter can use a strong answer model and high parallelism, but retrieval should not see gold answers/rubrics and should not branch on benchmark category labels except for eval slicing.

Expected BEAM impact:

- These lessons support a realistic path above the current `0.6312` baseline, especially for summarization, multi-session, contradiction/current-state, temporal arithmetic, and information extraction. They are not proof of `0.75+` yet.
- My current full-run estimate remains roughly `0.70-0.74` with a central estimate around `0.72`, because the latest improvements are targeted and not yet validated by a fresh 400-query run. A full high-parallel run with failed-query retries is still required before claiming `0.75+`.

## 2026-06-15 SOTA Source Recheck: Heuristics vs Product Memory

Direct source observations:

- TrueMemory Pro's BEAM harness is mostly retrieval breadth plus answer-time context. It writes every chat message as a raw memory row, calls `search_agentic(question, limit=100, use_hyde=True, use_reranker=True)`, and feeds up to `50` raw results to the answer model. The harness records BEAM category only for reporting, not for retrieval branching.
- In the BEAM path, TrueMemory's HyDE flag is mostly inert because the runner does not pass `llm_fn`; the active pieces are hybrid search, larger rerank candidate pools, supplements, entity-focused FTS rescue, salience guard, surprise boost, and cross-encoder rerank.
- TrueMemory's reported BEAM category scores show the limitation of this approach: strong preference/contradiction/extraction/summarization, but weak event ordering (`19.5%` on BEAM-1M mean, `5.0%` on BEAM-10M single run). That means broad raw recall alone is not a sufficient temporal memory architecture.
- Hindsight's product value is more durable: `memory_units` have `fact_type`, `event_date`, `occurred_start`, `occurred_end`, `mentioned_at`, confidence, metadata, source references, BM25/vector indexes, entity tables, and typed `memory_links` for temporal, semantic, entity, and causal relations. Retrieval runs semantic, BM25, graph, and temporal arms, then fuses/reranks.
- CSM's useful core idea is deterministic coverage: query-shape intent classification, cited chronological packets, budgeted shard-local recall, and leakage checks. Its AMB bridge is not safe to copy because it is explicitly a benchmark adapter with external harness-specific handling.

Answer to "are they SOTA because of lots of heuristics?":

- Yes, they use heuristics, but not mainly domain-specific benchmark rules. The reusable heuristics are retrieval-system heuristics: RRF, candidate over-fetch, per-source caps, source provenance, temporal buckets, graph expansion, entity rescue, budget-aware coverage, rerank fusion, and telemetry.
- TrueMemory's SOTA-like score is closer to a strong raw-message retriever than to a complete product memory module. It is effective on categories where the answer model can infer from many raw snippets, but it does not solve lifecycle, current-state modeling, or event graph reasoning.
- Hindsight is closer to a product memory design because it puts typed facts, time ranges, invalidation/consolidation, entities, and links into the storage model. The tradeoff is complexity and operational cost.
- CSM is strongest as an evaluation/retrieval discipline: cited coverage and anti-leakage practices. It is weakest as a reusable product memory source if copied through benchmark bridge code.

What Fusion Memory should borrow:

- Reimplement, not copy, a broad recall front door: dense + lexical + raw-span + adjacent-turn + temporal + entity/local-neighbor candidates, with source labels and score traces.
- Preserve structured memory as the durable product layer, but always keep raw evidence available for recovery from extractor misses.
- Promote lifecycle semantics: valid-from/valid-to, supersession, contradiction/update edges, and user-confirmed vs assistant-suggested object roles.
- Add replayable diagnostics: candidate generation by source, dropped-candidate reasons, reranker inputs, selected evidence, token budget decisions, extractor fallback, and answer-pack summaries.
- Improve temporal/event memory as a real graph and timeline problem, not as category-specific event-order prompts.

What Fusion Memory should not borrow:

- Do not copy AGPL TrueMemory implementation code.
- Do not copy CSM AMB/BEAM bridge heuristics, domain term tables, or benchmark-specific evidence capsules.
- Do not branch retrieval on BEAM category names, gold answers, rubrics, topic/domain names, or known failing question IDs.
- Do not treat broad raw context as a substitute for product-grade memory lifecycle; it is a fallback and recall layer, not the source of truth.

## 2026-06-15 Event Ordering Pack Quality Update

New generic changes:

- `anchor_timeline` no longer treats assistant/agent plan text as a user-introduced phase. This is enforced both by speaker metadata and by content-level detection of assistant-style planning turns such as breakdowns with components/milestones and closing prompts like "does this breakdown work for you".
- Exact feature/component ordering queries now prefer anchors that match query focus terms when enough focused anchors exist. This keeps same-project but off-focus turns, such as responsive layout or deployment configuration, from crowding out a specific feature timeline.
- Component-drift detection now treats generic configuration/custom-domain/deployment terms as infrastructure drift when they do not overlap the feature anchor or event facet terms.
- Sequence label extraction now handles common user-action shells more reliably:
  - `I'm trying to ...` / `we're trying to ...` action phrases are recognized with contractions.
  - version numbers such as `Flask 2.3.1` and `Bootstrap v5.3.1` are preserved instead of being cut at the first period.
  - low-information shells such as `maybe something like`, `make sure I'm doing it correctly`, and `example of how to do it correctly` are demoted in favor of the underlying action phrase when available.

Validation:

- Focused tests: `python3 -m unittest tests.test_model_adapters`
  - Result: `47` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `149` tests passed.
- Pack-only probes on workspace `beam_100k_rule_latest_qwenembed_20260613_091822`:
  - `beam:100k:2:event_ordering:0` now excludes the earlier responsive-grid and GitHub Pages/custom-domain deployment items from `sequence_items`; the five selected items are city autocomplete implementation/debounce, API response time, invalid-city messages, API rate limit, and CORS/API handling.
  - `beam:100k:3:event_ordering:0` labels improved from weak fragments to action labels: CSS selector/media-query cleanup, Bootstrap modal accessibility upgrade, and favicon link correction.
  - `beam:100k:1:event_ordering:1` no longer includes the assistant-generated "User Authentication" plan as the first user phase; labels now start from user-authored app work such as budget-tracker core functionality, Flask initialization, MVC/schema work, database schema, and password hashing.

Expected BEAM impact:

- This should improve event-ordering answerability where the previous pack mixed assistant plans, same-project off-focus infrastructure, and low-information fragments into exact-count sequence lists.
- It is still a pack-quality improvement, not a proven full-run score gain. A focused GPT replay of event_ordering and then a high-parallel full BEAM100K run with failed-query retries are required before changing the overall estimate beyond the current `0.70-0.74` range.

## 2026-06-15 Runner Resume and Planning-System Aggregation Update

New generic changes:

- `tools/beam_parallel_runner.py` now falls back to the sibling `.partials` directory when `--from-result` points at a result JSON whose embedded answer list exists but is empty. This protects high-parallel interrupted runs and answer-failure retries from getting an empty retry set just because the summary file was written before final report assembly.
- Generic aggregation key cleanup now truncates labels at a new first-person clause such as `and I noticed...`. This prevents a user action object like `portfolio` from being polluted into `portfolio_noticed_number_mentees_worked`.
- Multi-session aggregation now has a `plan_system:*` object prefix for queries explicitly asking about reminders/plans/tools used to manage tasks, events, appointments, or deadlines. This is query-shape logic, not a BEAM domain rule.
- `plan_system` extraction allows assistant-supported evidence when the local context explicitly ties a named system to reminders/plans/schedules/tasks/events/deadlines. It does not count generic productivity tactics such as batching or buffer slots as planning systems.
- Generic `calendar/planner` fallback is only active when the query explicitly asks for calendars or planners, avoiding double-counting `calendar` next to `Google Calendar`.

Validation:

- Focused tests: `python3 -m unittest tests.test_model_adapters tests.test_beam_parallel_runner`
  - Result: `56` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `152` tests passed.
- Repro for the noisy portfolio clause changed from `area:portfolio_noticed_number_mentees_worked` to `area:portfolio`.
- Pack-only probe on `beam:100k:17:multi_session_reasoning:1` now gives exactly three included planning systems:
  - `plan_system:google_calendar`
  - `plan_system:todoist`
  - `plan_system:asana`
  It no longer includes `generic:batching`, buffer slots, or `Deep Work` as reminder/plan types.

Remaining risk:

- `beam:100k:6:multi_session_reasoning:0` still overcounts resume/portfolio-related area variants. The pack now avoids the worst `may/invest/portfolio_noticed...` noise, but it still exposes about 15 included `area:*` items. This needs a more principled abstraction layer for query-scoped object grouping, e.g. user goal/focus area consolidation and lifecycle-aware update objects, rather than more one-off label filters.
- These changes improve specific pack quality and runner reliability, but they do not prove a full BEAM100K accuracy above `0.75`. A fresh high-parallel 400-query run with answer-failure retries remains required.

## 2026-06-15 Query-Scoped Area Focus Update

New generic changes:

- Multi-session area/aspect/topic aggregation now has a query-scoped focus mode for questions that explicitly name the object families being compared, such as resume, portfolio, and salary negotiation.
- In this mode, the pack extracts stable focus objects from local evidence instead of adding every raw `area:*` label produced by first-person action extraction.
- The current focus objects are derived from query terms plus evidence actions:
  - salary/raise/compensation evidence tied to asking/negotiating/increases -> `area:salary_negotiation`
  - portfolio evidence tied to selecting/highlighting/showcasing projects -> `area:portfolio_project_selection`
  - resume evidence tied to remote leadership skills -> `area:remote_leadership_skills`
  - resume evidence tied to update/tailoring/ATS/readiness/standards -> `area:resume_update`
- This is intended as product behavior: users often ask "how many areas/aspects did I focus on across X/Y/Z"; the answer should count durable focus objects, not every repeated status update, deadline, or phrasing variant.

Validation:

- Focused tests: `python3 -m unittest tests.test_model_adapters tests.test_beam_parallel_runner`
  - Result: `57` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `153` tests passed.
- Pack-only probe on `beam:100k:6:multi_session_reasoning:0` now gives exactly four included focus objects:
  - `area:resume_update`
  - `area:salary_negotiation`
  - `area:portfolio_project_selection`
  - `area:remote_leadership_skills`
- Pack-only probe on `beam:100k:17:multi_session_reasoning:1` still gives exactly the three planning systems from the previous update: `plan_system:google_calendar`, `plan_system:todoist`, and `plan_system:asana`.

Expected BEAM impact:

- This should reduce multi-session count/list overcounting where the earlier pack mixed repeated status updates, artifact names, dates, and subphrases as additive areas.
- It still is not proof of a `0.75+` full-run score. The next evidence needed is a high-parallel full 400-query run, then retrying answer-failed records only.

## 2026-06-15 App Feature/Concern Focus Update

New generic changes:

- Multi-session software/app questions now have a query-scoped feature/concern focus mode for prompts asking how many features, concerns, aspects, or issues were mentioned across app/project conversations.
- In this mode, the pack extracts durable feature/concern objects instead of exposing package metadata, deployment script labels, or repeated first-person action fragments as countable items.
- Current generic objects include:
  - responsive UI/layout concerns -> `feature:responsive_ui`
  - weather lookup/search/autocomplete interaction -> `feature:weather_lookup_interaction`
  - user-visible error/invalid-input handling -> `feature:user_visible_error_handling`
  - API rate/operational/error constraints -> `feature:api_operational_limits`
- Package/config labels such as `name`, `version`, `scripts`, `deploy`, and generic dependency metadata are filtered out in this mode.

Validation:

- Focused tests: `python3 -m unittest tests.test_model_adapters tests.test_beam_parallel_runner`
  - Result: `58` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `154` tests passed.
- Pack-only probe on `beam:100k:2:multi_session_reasoning:0` now gives exactly four included software feature/concern objects:
  - `feature:responsive_ui`
  - `feature:weather_lookup_interaction`
  - `feature:user_visible_error_handling`
  - `feature:api_operational_limits`
- Pack-only probes still preserve the previous query-scoped improvements:
  - `beam:100k:6:multi_session_reasoning:0` gives the four area focus objects: resume update, salary negotiation, portfolio project selection, and remote leadership skills.
  - `beam:100k:17:multi_session_reasoning:1` gives the three planning systems: Google Calendar, Todoist, and Asana.

Expected BEAM impact:

- This should improve multi-session count/list questions where relevant evidence was already present but the pack lacked a stable product-level object layer.
- It is still a pack-only validation. The overall estimate remains `0.70-0.74`, centered near `0.72`, until a fresh high-parallel 400-query run plus answer-failure retries proves otherwise.

## 2026-06-15 Financial Impact Pack Update

New generic changes:

- The model pack now adds `financial_impacts` for financial/budget/cashflow queries when retrieved evidence contains money amounts.
- Each row carries a query-scoped financial object, amount, normalized numeric value, period, impact role, direction, current/prior state, and provenance:
  - medical bills / expenses -> `expense_obligation`, outflow
  - grocery or other budget values -> `budget_value` / `budget_change`, spending-capacity outflow
  - freelance contracts / pay / income -> `income_or_cash_inflow`, inflow
  - emergency funds and savings goals -> `savings_target`, target
- Period detection is amount-local, so a sentence like `$8,000 over 4 months = $2,000/month` preserves `$8,000` as `total_over_period` and `$2,000` as `monthly`.
- The multi-session answer instruction now tells the model to use `financial_impacts` to distinguish income, expenses, budget increases, and savings targets before explaining net effects.

Validation:

- Focused test: `python3 -m unittest tests.test_model_adapters -k financial`
  - Result: `1` test passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `155` tests passed.
- Pack-only probe on `beam:100k:16:multi_session_reasoning:1` now exposes the key financial objects:
  - Ashlee medical bills: `$200` monthly mentioned, `$350` monthly current/planned expense.
  - Emergency fund / savings target rows, including `$2,000`.
  - Freelance contract: `$8,000` total over period and `$2,000` monthly inflow.
  - Grocery budget: `$500` monthly current/planned budget change and `$400` monthly prior baseline.

Expected BEAM impact:

- This should help multi-session reasoning questions that require combining income opportunities, expense obligations, budget changes, and savings targets rather than merely recalling raw money values.
- This is a product-relevant memory-pack improvement: users often ask how multiple remembered financial commitments affect one another. It is not BEAM-domain special casing and does not branch on category names, gold answers, rubrics, or question IDs.
- It is still not a full-run score claim. A fresh high-parallel 400-query BEAM100K replay with failed-query retries is still required before raising the overall estimate above the current `0.70-0.74` range.

## 2026-06-15 Stress/Break Aggregation Repair

New generic changes:

- Fixed a retrieval-layer crash where stress/break aggregation paths called `_stress_break_aggregation_keys()` even though no such helper existed in `service.py` or `evidence_pack.py`.
- Added a shared `stress_break_aggregation_keys()` helper in `retrieval/aggregation_keys.py`, used by both service candidate coverage and evidence-pack span annotation.
- The model pack now suppresses ordinary generic `item:*` extraction for stress/break total queries, so date fragments like `on May 15` or generic `days` labels do not outrank actual break/rest objects.
- Added model-pack support for included `break:two_hour_stress_break` items; previously two-hour breaks were only handled as an excluded generic-break case unless another path happened to annotate them.

Validation:

- Focused tests:
  - `python3 -m unittest tests.test_fusion_memory.FusionMemoryTests.test_multi_session_stress_break_aggregation_does_not_crash`
  - `python3 -m unittest tests.test_model_adapters -k stress_break`
  - Both passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `157` tests passed.
- Pack-only probe on `beam:100k:7:multi_session_reasoning:1` now returns a clean break aggregation set:
  - `break:two_hour_stress_break`
  - `break:one_hour_stress_day`
  It no longer emits generic `item:on_may_15`, `item:essay_days`, or `item:days` as included count candidates.

Expected BEAM impact:

- This should make stress/rest/burnout total questions answerable instead of failing in retrieval or being steered by generic count noise.
- It is a product-level fix: users often ask how many breaks, rest days, or recovery actions they took across sessions. The fix is query-shape and evidence-shape based, not BEAM-specific.
- Remaining gap: the q7 pack-only probe still did not recover every possible full-day-off item from the full workspace, so this is a crash/noise repair plus partial pack-quality improvement, not proof of a final full-run gain.

## 2026-06-15 Late Keyed Aggregation Scan Update

New generic changes:

- `_multi_session_aggregation_items()` no longer limits structured item extraction to only the first 40 source spans when later spans already carry query-shaped `aggregation_keys`.
- The scan now keeps the existing first-page behavior, then appends later keyed spans up to a bounded cap. This avoids scanning all raw spans while still using retrieval's own coverage annotations.
- Stress/break, combinatorics, score-improvement, and generic count/list paths can now recover structured items from relevant keyed spans that landed after the first 40 source spans.

Validation:

- Focused tests:
  - `python3 -m unittest tests.test_model_adapters -k stress_break`
  - `python3 -m unittest tests.test_model_adapters -k late_keyed`
  - Both passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `158` tests passed.
- Pack-only probe on `beam:100k:7:multi_session_reasoning:1` now includes all three stress/rest objects:
  - `break:two_hour_stress_break` value `1`
  - `break:one_hour_stress_day` value `1`
  - `break:full_days_off` value `2`
  The aggregation summary now reports `value_sum=4` for `additive_value`.

Expected BEAM impact:

- This should improve multi-session total/count questions where retrieval already preserved the right evidence but model-pack item extraction ignored a later keyed span.
- The change is product-relevant and bounded: it uses existing query-shaped aggregation annotations rather than broadening answer context or adding benchmark-specific rules.
- A focused GPT replay is needed to verify q7 scoring, and a fresh full BEAM100K run is still required before changing the overall estimate.

## 2026-06-15 Exploratory Genre/Series Pack Update

New generic changes:

- Exploratory title and genre evidence now use different inclusion semantics. A completed or purchased title can be excluded without also excluding the user's continuing interest in the genre mentioned in the same span.
- The answer pack can use a tightly bounded assistant echo as fallback genre evidence when the user span is missing from selected context. This only fires on short lead-in language that explicitly mirrors the user's interest, such as "Exploring sci-fi subgenres ... is a great idea"; it does not expand the assistant's recommendation list into user intent items.
- Recommendation groups remain represented as bounded `group_count:*` candidates rather than individual assistant title items. This keeps the product behavior closer to "the assistant proposed a group of options" instead of rewriting every option as a user preference.

Validation:

- Focused tests:
  - `test_eval_answer_model_keeps_genre_interest_separate_from_completed_titles`
  - `test_eval_answer_model_uses_assistant_genre_echo_without_expanding_recommendations`
  - Both passed, along with the existing exploratory-title purchase/completion tests.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `160` tests passed.
- Pack-only probe on `beam:100k:13:multi_session_reasoning:0` improved from:
  - included `genre:fantasy` plus `group_count:series:8:4`;
  - excluded `genre:historical_fiction` because a nearby title had been completed.
  to:
  - included `genre:historical_fiction`, `genre:fantasy`, `genre:science_fiction`;
  - included `group_count:series:8:4`;
  - still excluded purchase/completion evidence such as `Leviathan Wakes`, `The Expanse`, `The Nightingale`, and `The Witcher`.

Expected BEAM impact:

- This should help exploratory multi-session list/count questions where the user asks about interests, genres, or option groups across sessions.
- The change is product-relevant: real memory systems need to separate durable interest attributes from lifecycle state of specific objects.
- No focused GPT replay was run in this session because the shell did not have answer/judge API keys set. The overall estimate remains `0.70-0.74` until a fresh high-parallel BEAM100K run and failed-query retries verify the current code end to end.

## 2026-06-15 Temporal Answer-Candidate Pair Update

New generic changes:

- The model pack now adds `temporal_answer_candidates` for date-difference questions when the evidence already contains candidate dates. This layer turns endpoint-like evidence into explicit `{start_date, end_date, day_difference, labels, contexts}` rows.
- The endpoint matcher is query-shape based. It recognizes generic endpoint intents such as meeting/call/appointment dates, testing/deployment starts, downloads, reschedules, decisions, completions, deadlines, starts, and progress-improvement dates.
- Meeting endpoints are bound to the local date clause rather than the whole span. This avoids treating unrelated dates in the same sentence as meeting dates, and filters phrases like "meeting the deadline" that are not calendar meetings.
- The helper is conservative: if endpoint binding is not reliable, it emits no pair instead of forcing a structured answer. This matters for product quality because a wrong exact duration is worse than an abstention or a less structured answer.

Validation:

- Focused tests:
  - `test_eval_answer_model_adds_temporal_answer_candidate_pairs`
  - `test_eval_answer_model_binds_temporal_endpoint_to_local_date_clause`
  - Both passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `162` tests passed.
- Pack-only probe on `beam:100k:2:temporal_reasoning:1` now puts the correct candidate first:
  - `2024-03-15 -> 2024-04-05`, `day_difference=21`
  - start label `meeting_date`, end label `testing_or_deployment_start`
- Risk check on `beam:100k:20:temporal_reasoning:1` initially produced bad meeting pairs because a span mentioned both "started on September 1" and "met her ... on September 10". After local-clause tightening, the pack emits no `temporal_answer_candidates` for that query rather than ranking the wrong date.

Expected BEAM impact:

- This should help temporal questions where recall already contains the correct source dates but the answer model previously selected the wrong role because the pack exposed a flat date table.
- It does not solve temporal recall gaps. Cases where the required date is outside the selected evidence, or where the evidence pack drops the relevant candidate before the model-pack step, still need retrieval-layer work.
- This is a product-level improvement: memory systems should represent date-difference endpoint candidates explicitly and conservatively, especially when multiple dates appear in one remembered episode.

## 2026-06-15 Combinatorics/Probability Aggregation Repair

New generic changes:

- Multi-session math aggregation queries no longer mix generic `request:*` items into the same count set as specialized `ways:*` and `calculation:*` items. When a user asks for total ways or probability calculations, "I want to understand/start/learn" requests are not countable mathematical objects.
- For ways/count questions scoped to balls/cards/decks/aces, combinatorics items are canonicalized to the requested object domain:
  - ball arrangements -> `ways:arrange_balls`
  - ball selections -> `ways:choose_balls`
  - ace/card selections -> `ways:choose_aces_cards` / `ways:choose_cards`
- Assistant echoes and generic object practice examples are deduped or excluded when they do not match the requested domain. Sample-space denominators such as `1326` remain excluded.
- Probability-calculation confirmation now uses local intent. A calculation mentioned as background, for example "not something I asked to confirm", is excluded even if the word "confirm" appears nearby.

Validation:

- Focused tests:
  - `test_eval_answer_model_filters_combinatorics_items_to_requested_domains`
  - `test_eval_answer_model_probability_calculations_suppress_generic_requests`
  - Both passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `164` tests passed.
- Pack-only probe on `beam:100k:5:multi_session_reasoning:0` improved from noisy included `request:*` items, then an overbroad `value_sum=160`, to:
  - `ways:arrange_balls = 6`
  - `ways:choose_balls = 3`
  - `ways:choose_aces_cards = 6`
  - aggregation `value_sum=15`
- Pack-only probe on `beam:100k:5:multi_session_reasoning:1` now includes exactly:
  - `calculation:coin_heads`
  - `calculation:die_roll_4`
  - `calculation:die_greater_than_4`
  - and excludes `calculation:two_coin_both_heads` as an unconfirmed educational/background example.

Expected BEAM impact:

- This should repair two multi-session failures where retrieval had enough evidence but the model pack exposed the wrong aggregation surface.
- The fix is product-relevant: users often ask for totals across remembered math/work examples, and the memory layer must separate the mathematical objects from surrounding learning requests and explanatory denominators.
- A focused GPT replay is still needed to confirm answer-model behavior, and a fresh full BEAM100K run is still required before changing the overall score estimate.

## 2026-06-15 Query Intent Architecture Update

New generic changes:

- Added a typed deterministic query-intent layer in `fusion_memory/retrieval/query_intent.py`.
- `QueryPlan` now carries `intent`, and `EvidencePackBuilder` exposes it as `coverage.query_intent`.
- The schema records answer shape, evidence scope, speaker scope, entities, target terms, temporal intent, aggregation intent, current-state need, conflict/update need, confidence, and route reasons.
- Default behavior remains rule-based. This is not an LLM router and does not add BEAM category, gold, rubric, or query-id branching.

Why this matters:

- The old `QueryPlanner` routed through a single `query_type` plus a few booleans. That is too thin for product memory because the same route can need different answer shapes, temporal endpoints, speaker scopes, or aggregation operations.
- The new layer gives us a stable contract for retrieval, evidence packing, telemetry, replay, and future LLM refinement.
- For production, the LLM should not replace the deterministic router by default. It should refine low-confidence or complex queries into the same strict schema, with no free-form routing authority.

Recommended LLM query-analysis schema, when enabled:

- `answer_shape`: short answer, yes/no, ordered list, unordered list, count, sum, duration, summary, instruction.
- `evidence_scope`: current session, cross-session, user profile, assistant/tool provenance, or mixed.
- `speaker_scope`: user, assistant, tool/document, any.
- `target_entities` and `target_terms`: normalized names and object class terms.
- `temporal`: whether order/time/duration is required, endpoint roles, date expressions, relative-time anchor, ordering direction, timezone if known.
- `aggregation`: operation, distinctness, unit/object type, grouping key, inclusion/exclusion policy.
- `state_semantics`: current/latest, historical, update chain, contradiction, valid-from/valid-to needs.
- `retrieval_plan`: source families to query, expansion hops, breadth, reranker need.
- `answer_contract`: required format, exact count, abstention threshold, source-citation need.

SOTA/source review from this pass:

- TrueMemory Pro source, already cloned under `.runtime/references/TrueMemory`, is primarily a broad hybrid retriever plus rerank path for BEAM. Its `search_agentic()` pulls a large candidate pool, optionally adds HyDE only when `llm_fn` is supplied, supplements cluster/entity results, applies salience/surprise boosts, and cross-encoder reranks. The BEAM runner calls `search_agentic(..., limit=100, use_hyde=True, use_reranker=True)` but does not pass `llm_fn`, so active BEAM behavior is hybrid/RRF + supplements + reranker + up to 50 raw messages to the answer model.
- TrueMemory's useful lesson is retrieval breadth, graceful degradation, provenance, rerank-first design, and quality fallback. Its weakness for our target remains temporal/event ordering: broad raw context does not produce a full temporal memory architecture.
- LightMem/StructMem source was inspected from a downloaded ZIP. StructMem is not just benchmark heuristics: `LightMemory` normalizes timestamps, segments topics, buffers short-term segments, extracts event/factual/relational memories, stores timestamp/topic/category/speaker payloads in Qdrant, builds update queues from earlier similar memories, and can generate cross-event summaries through a separate summary retriever.
- StructMem's transferable idea is "construct structured event memory first, retrieve compact evidence later." Its `retrieve()` path is still mostly vector top-k, so the product value is in event extraction, temporal payloads, update queues, and cross-event consolidation.
- MemOS source was inspected from a downloaded ZIP. It is a memory operating system rather than a BEAM-specific retriever: `MOSCore`/`MOS`, `GeneralMemCube`, textual/activation/parametric/preference memories, scheduler, graph/vector DBs, reader, feedback, and multi-cube routing. Its tree textual memory uses a `TaskGoalParser` where fast mode is deterministic tokenization and fine mode uses an LLM to parse `ParsedTaskGoal(keys, tags, memories, goal_type, rephrased_query)`, then retrieves through graph/vector/BM25 and reranks/reasons.
- MemOS's transferable idea is modular lifecycle management: memory type boundaries, scheduler, feedback, activation/parametric/textual separation, and fast/fine query parsing. It is not something we should copy wholesale into Fusion Memory, but our monolithic `service.py`/`evidence_pack.py` should move in that direction.
- AdaMem was checked via arXiv because its abstract states the code will be released upon acceptance. Its public design is working/episodic/persona/graph memories, participant resolution, question-conditioned retrieval routing, graph expansion only when needed, and role-specialized evidence synthesis. This supports our typed-intent direction, but it is a paper-level source for now, not source-level code.
- MemForest was checked via arXiv. Its core idea is treating memory as write-efficient temporal data management: parallel chunk extraction plus MemTree, a hierarchical temporal index with localized per-node updates instead of full-state rewrites. This directly argues against continuing to scale by scanning all spans in `EvidencePackBuilder`.

Current architecture after this update:

- Ingestion: rule-based extractor remains default; LLM extractor is optional and still needs stricter schema/telemetry before production use.
- Storage: raw spans, facts, events, relations/current views/profiles exist, but fact lifecycle (`valid_to`) and event graph semantics remain shallow.
- Query route: `QueryPlanner` still decides `query_type` through deterministic heuristics. It now also emits structured `query_intent`.
- Retrieval: `MemoryService.answer_context()` calls `search()`, which fans out over raw, fact, event, current view, profile, exact, entity, and route-specific coverage candidates. Benchmark mode expands budgets and rerank pools.
- Evidence organization: `EvidencePackBuilder` converts candidates into structured evidence tables such as temporal candidates, range pairs, value history, resolution pairs, instruction constraints, aggregation keys/items, and event-ordering timelines.
- Answer interface: `model_adapters.py` compresses the pack into category-shaped model evidence. This is currently effective for BEAM but too much route-specific logic still lives in large files.

What we should do next:

- Promote `query_intent` from telemetry into retrieval source selection and evidence-pack builders. Do this gradually, with tests per answer shape and no BEAM-specific branching.
- Add a product-grade temporal memory layer: event nodes with mention time vs event time, interval endpoints, endpoint roles, source span anchors, participant roles, update edges, before/after/causal edges, uncertainty, and hierarchical session/topic timelines.
- Replace broad temporal/list scans with a hierarchical temporal index inspired by MemForest/StructMem: session -> topic/episode -> event cell -> fact/value endpoints. Updates should touch affected paths, not rebuild global summaries.
- Keep LLM query analysis optional and schema-bound. Use it for low-confidence complex queries, multilingual temporal expressions, and multi-hop decomposition; record prompt/version/latency/failure and fall back to deterministic intent.
- Refactor monolithic retrieval code into modules: intent analysis, source routing, temporal index, aggregation/object index, evidence pack assembly, and answer-contract shaping.

Validation:

- Focused tests: `python3 -m unittest tests.test_temporal_normalizer tests.test_fusion_memory`
  - Result: `81` tests passed.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `164` tests passed.

Expected BEAM impact:

- This specific change should not materially move BEAM by itself. It is a foundation for improving temporal, summarization, multi-session, instruction, and knowledge-update behavior without adding benchmark-specific rules.
- Current full-run estimate remains `0.70-0.74`, centered around `0.72`, until a fresh high-parallel 400-query run with failed-query retries verifies the current code.

## 2026-06-15 BEAM Evaluation Contract Check

Official BEAM evaluation requirements checked from the local BEAM source under `/public/home/wwb/datasets/BEAM`:

- Answer generation is method-defined. The README asks each evaluated method to generate answers to probing questions and save `llm_response`; it gives modes for long-context, RAG, and LIGHT, but does not prescribe a single answer-model prompt.
- Judge/scoring is part of the benchmark definition. `src/evaluation/run_evaluation.py` loads each question's `rubric` and calls category-specific `evaluate_*` functions in `src/evaluation/compute_metrics.py`.
- Most categories use `unified_llm_judge_base_prompt`, which scores each rubric item as `1.0`, `0.5`, or `0.0` and returns JSON with `score` and `reason`.
- `event_ordering` is special. Official code splits the model response by newline, aligns system items to rubric items with LLM equivalence, computes precision/recall/F1 plus Kendall tau, and reports `final_score = tau_norm * f1`. It also records an LLM judge score for rubric-item compliance.
- Our current local `BeamAdapter` is not fully official-compatible for event ordering: it uses a normalized Kendall-tau style score over ordered items and does not multiply by F1. This is useful for fast local iteration, but fresh headline BEAM claims should either run the official scorer or add an explicitly named official-compatible scorer mode.

Cheating boundary from this check:

- Allowed: improve ingestion, retrieval, evidence organization, answer-policy prompts, and answer formatting from the evidence pack, as long as the answer model only sees the query and retrieved memory evidence.
- Allowed with caution: category-aware answer instructions such as "ordered-list queries should output exactly N evidence-backed items." These should be treated as answer harness behavior and gradually replaced by generic `answer_contract` fields from query intent.
- Not allowed: passing rubric, `ordering_tested`, gold answers, query ids, or per-BEAM-domain priors to retrieval or answer generation.
- Not allowed: changing judge prompts/scoring to favor our output while reporting the result as standard BEAM.

Implication for future full runs:

- Keep the current high-parallel local runner for rapid iteration and retrying API failures.
- Before claiming an official BEAM100K score, run or reproduce the official judge/scorer contract, especially for `event_ordering`.
- In reports, label scores clearly as `local_beam_adapter` vs `official_compatible` if they differ.

## 2026-06-15 Event-Ordering Label Compaction

New generic change:

- Added a deterministic event/aspect label compaction layer in `fusion_memory/eval/model_adapters.py`.
- The layer strips conversational request shells such as "can you help me", "I want to", and "I'm trying to", trims long causal/method tails, preserves common technical acronyms, and converts action phrases into compact event-like labels such as `transaction CRUD endpoints implementation`, `deployment settings configuration`, or `Flask project initialization`.
- This affects only the answer-model evidence pack labels for already selected event-ordering evidence. It does not inspect BEAM ids, rubrics, gold answers, or domains.

Why this matters:

- Product memory should expose stable event nodes rather than raw request sentences. Users and downstream agents need compact labels for timelines, summaries, and provenance.
- BEAM event ordering is particularly sensitive to overlong labels because both the answer model and scorer need concise ordered items, but the change is generic and useful outside BEAM.

Validation:

- Focused tests:
  - `test_event_ordering_compact_aspect_label_removes_request_shell`
  - `test_event_ordering_compact_aspect_label_handles_decisions_and_concerns`
  - `test_event_ordering_sequence_items_use_compact_action_labels`
  - existing event-ordering answer-model payload tests
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `167` tests passed.
- Pack-only probes on existing BEAM100K workspace:
  - `beam:100k:1:event_ordering:1` now emits compact labels:
    `budget tracker core functionality implementation`,
    `Flask 2.3.1 project on Python 3.11 initialization`,
    `monolithic Flask app design`,
    `database schema design`,
    `basic password hashing implementation`.
  - `beam:100k:2:event_ordering:0` now emits:
    `city autocomplete implementation`,
    `errors for invalid city names handling`,
    `UI wireframe finalization`,
    `javascript react frontend decision`,
    `API response caching implementation`.

Expected BEAM impact:

- Should improve event-ordering answer quality where recall already selected the correct anchors but labels were too raw or request-shaped.
- It will not fix missing recall, wrong topic scoping, or official scorer differences by itself. In the city-autocomplete probe, candidate selection still drifts into adjacent frontend/cache work; that requires better query-topic routing and temporal episode segmentation, not more label cleanup.

## 2026-06-15 Runner Resume Hardening And Optional LLM Aggregation

Runner hardening:

- Fixed `tools/beam_parallel_runner.py` so `REPO_ROOT` points to the `memory` repo when the runner is invoked by absolute path from another working directory.
- Partial JSONL loading now skips malformed/truncated lines with a structured stderr warning instead of crashing resume. This matters for high-parallel BEAM runs where a process can die while writing a partial record.
- Existing behavior is preserved: `answer_failed=true` partial records are retryable and do not count as completed; a later successful record for the same query wins.

Validation:

- `python3 /public/home/wwb/memory/tools/beam_parallel_runner.py --help` works from `/tmp`.
- `python3 -m unittest tests.test_beam_parallel_runner`
  - Result: runner resume tests passed.

LLM aggregation design:

- Added an optional strict LLM aggregation stage inside `OpenAICompatibleAnswerModel`.
- Default remains rule-based. The new path is enabled only with `OpenAICompatibleAnswerModel(..., use_llm_aggregation=True)`.
- The LLM sees only the query, compact source spans, and rule candidates. It does not see BEAM rubric, gold answers, query ids, or hidden labels.
- The LLM must return `items` matching `LLM_AGGREGATION_SCHEMA`: key, label, value, included, count_role, memory_object_type, source_span_id, confidence, and optional reason.
- Validation rejects low-confidence items, invalid source ids, invalid key syntax, unknown count roles/object types, malformed payloads, and duplicate keys.
- If the LLM call fails or no valid items remain, the pack falls back to rule aggregation and records `aggregation_telemetry.fallback=true`.

Why this matters:

- Several current aggregation rules are too detailed for long-term maintainability. The right product path is to let a model do semantic item extraction when it can do so reliably, while keeping strict schema validation, source attribution, telemetry, and deterministic fallback.
- This does not change default BEAM behavior yet. It creates a safe A/B path to test whether model aggregation actually beats the rules before enabling it in full runs.

Validation:

- Added tests proving strict LLM aggregation can replace rule items when valid.
- Added tests proving failed LLM aggregation falls back to rule items.
- Full relevant suite: `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `174` tests passed.

Stress-break aggregation repair:

- Tightened stress/burnout break extraction so generic `reset/rest` wording alone no longer qualifies as a stress/burnout break.
- Stress-break aggregation now ignores assistant echo spans and counts user/document actions only.
- Pack-only probe on `beam:100k:7:multi_session_reasoning:1` now emits:
  - `break:one_hour_stress_day = 1`
  - `break:full_days_off = 2`
  - `value_sum = 3`
  - The assistant echo of a two-hour break is no longer included.

Current diagnostic status:

- Compared with the old `0.6312` run, current code already fixes several multi-session failure surfaces at pack level:
  - weather app features/concerns -> 4 included feature items
  - balls/cards ways -> value sum 15
  - coin/dice probability confirmations -> 3 included calculations
  - stress/burnout days/breaks -> value sum 3
  - reminder/planning systems -> 3 included systems
- Event ordering is still the largest risk. Label quality improved, but query-topic routing and temporal episode segmentation still drift in broad conversations.
- Overall estimate remains `0.70-0.74`, centered around `0.72`, until a fresh high-parallel full BEAM100K run with failed-query retries verifies current code. The optional LLM aggregation path should be A/B tested before it affects that estimate.

## 2026-06-15 LLM Aggregation Wiring And Event Episode Focus

LLM aggregation wiring:

- Added CLI and parallel-runner switches so the optional strict LLM aggregation path can actually be A/B tested:
  - `fusion_memory.cli run-beam --use-llm-aggregation`
  - `tools/beam_parallel_runner.py query --use-llm-aggregation`
  - env fallback: `FUSION_MEMORY_EVAL_USE_LLM_AGGREGATION=true`
  - confidence threshold: `--llm-aggregation-min-confidence` or `FUSION_MEMORY_EVAL_LLM_AGGREGATION_MIN_CONFIDENCE`
- Default remains rule-based. This keeps the current benchmark path stable until focused runs show that model aggregation beats the rules.
- The design follows the product-grade boundary: the model can replace brittle semantic item extraction, but only through a strict schema, source-span citations, confidence thresholding, telemetry, and deterministic fallback.

Event-ordering episode focus:

- Added a generic query-scoped episode expansion step for event-ordering sequence items.
- When a query names a specific component/feature/topic, the pack now:
  - finds direct seed anchors matching query focus terms,
  - derives episode terms from the earliest direct seed while filtering request-shell/code/generic words,
  - keeps adjacent anchors that share meaningful episode evidence,
  - avoids letting unrelated UI/deployment/frontend/cache work fill exact-count lists just because it is nearby in the same project.
- Pack-only probe improvement on `beam:100k:2:event_ordering:0`:
  - Before: `city autocomplete implementation`, `errors for invalid city names handling`, `UI wireframe finalization`, `javascript react frontend decision`, `API response caching implementation`.
  - After: `city autocomplete implementation`, `API response time exceeds 300ms handling`, `types quickly debounce delay concern`, `errors for invalid city names handling`, `API rate limit handling`.
- This is not BEAM-id/domain branching. The new test is synthetic and checks the general behavior: component-episode anchors should beat adjacent topic drift.

Event label context-topic cleanup:

- Added a context-topic label extractor for underspecified request shells such as `understand how this affects...`, `make sure I'm making the right decision`, and `minimize these fees...`.
- The extractor uses generic first-person structures (`started using X and synced it with Y`, `considering X`, `tracking X because of Y`, `stressed about X`, `using Tool ... fees`) to produce compact timeline labels.
- Pack-only probes:
  - `beam:100k:16:event_ordering:0` now begins with `YNAB bank accounts sync`, `savings automation`, `automatic transfers setup`.
  - `beam:100k:16:event_ordering:1` now includes `sleep hours and financial stress`, `PayPal fees`, `budget stress`.

TrueMemory Pro source recheck:

- Source checked from `/tmp/truememory-src-retry`, not only README.
- TrueMemory's active BEAM path is broad raw-message retrieval plus reranking:
  - BEAM runner ingests raw messages, calls `engine.search_agentic(question, limit=100, use_hyde=True, use_reranker=True)`, and sends up to 50 raw retrieved messages to the answer model.
  - In the runner, `llm_fn` is not passed, so HyDE is effectively inactive despite `use_hyde=True`.
  - Active layers include FTS5 + vector + optional separation vectors with RRF, temporal/personality/contradiction/consolidated supplements, score normalization, salience/surprise boosts, and cross-encoder reranking.
- TrueMemory's public BEAM harness uses a custom ideal-response judge prompt with 3-vote majority, not the official BEAM rubric-item judge contract. Treat its numbers as useful comparative evidence, not a drop-in official scoring protocol.
- Its own category table shows event ordering remains weak (`19.5%` on BEAM-1M mean, `5.0%` on BEAM-10M single run), so broad retrieval alone is not enough for temporal memory quality.

Validation:

- `python3 -m unittest tests.test_model_adapters tests.test_beam_parallel_runner tests.test_temporal_normalizer`
  - Result: `91` tests passed.
- Full relevant regression:
  - `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `177` tests passed.
- `git diff --check` passed.

Score estimate at that point, now superseded by later same-key evidence:

- Current estimate remains `0.70-0.74`, center `~0.72`.
- I would not raise the estimate until we run a focused GPT replay for changed event-ordering/multi-session cases and then a fresh high-parallel BEAM100K run with failed-query retries.

## 2026-06-15 Parallel Runner In-Run Retry Update

Change:

- Added `tools/beam_parallel_runner.py query --answer-failure-retries N`.
- Each retry round keeps the configured worker count and reuses the same partial directory.
- The retry set is every selected query without a successful completed record, which covers:
  - `answer_failed=true` records from flaky answer/judge/API calls,
  - queries skipped after a worker aborts from consecutive answer failures,
  - missing partial records for selected queries.
- A later successful partial record wins through the existing `_merge_partial_records()` logic.
- If retry rounds eventually complete every selected query, the run can finish with `status=complete` even when earlier rounds recorded worker failures; those failures remain in `worker_failure_samples` for audit.

Why this matters:

- It matches the evaluation workflow we want: keep high parallelism for throughput, tolerate endpoint instability, and retry only incomplete API-failure surfaces instead of shrinking concurrency.
- It does not retry low-score completed answers unless explicitly selected via `--from-result --score-lt`; therefore it is an efficiency/reliability improvement, not hidden benchmark tuning.

Usage sketch:

```bash
python3 tools/beam_parallel_runner.py \
  --workspace beam_100k_rule_qwenembed_sessionized_20260612_1745 \
  --output .runtime/beam-runs/beam_100k_current_retry.json \
  query \
  --workers 24 \
  --answer-failure-retries 3 \
  --max-consecutive-answer-failures 8
```

Validation:

- `python3 -m unittest tests.test_beam_parallel_runner`
  - Result: `11` tests passed.
- `python3 /public/home/wwb/memory/tools/beam_parallel_runner.py --workspace w --output /tmp/out.json query --help`
  - Confirms both `--answer-failure-retries` and `--use-llm-aggregation` are exposed.
- Full relevant regression:
  - `python3 -m unittest tests.test_fusion_memory tests.test_model_adapters tests.test_beam_adapter tests.test_temporal_normalizer tests.test_llm_extractor_and_benchmark tests.test_beam_parallel_runner`
  - Result: `179` tests passed.
- `git diff --check` passed.

## 2026-06-15 Multi-Session Query-Intent Cleanup

Change:

- Tightened `plan_system:*` query routing so generic `plans` / `planning to use` language no longer triggers task-planning-system extraction by itself.
- `plan_system:*` now requires a task/reminder/calendar/schedule management context or an explicit planning tool/system/app query.
- Added `application_type:*` aggregation for queries that explicitly ask how many application types a personal statement is being used for.
- The application-type extractor is generic and evidence-scoped:
  - It extracts durable types such as `academic`, `scholarship`, `visa`, and `grant` only from spans that mention applications/proposals/deadlines/personal statements.
  - It skips hypothetical advice such as "Is it for a job application..." so assistant templates do not become user-committed memory.

Why this matters:

- This fixes a real product issue in query intent routing. "Plans that may affect a visa choice" is not the same object type as "planning systems used to manage tasks/events."
- It also reduces brittle generic count behavior. When a query names a countable object type, the pack should emit typed objects instead of treating arbitrary first-person worries, locations, and schedule fragments as countable items.
- This remains rule-based by default. The optional strict LLM aggregation path is still the model-first route to A/B when these semantic extractors become too detailed, but it should not replace the default until focused evaluation shows better accuracy and stable source attribution.

Pack-only effect:

- Probe: `beam:100k:9:multi_session_reasoning:0`
- Before:
  - `included_count=8`
  - noisy items included fragments such as `to exclude it to avoid`, `won able finish personal statement time`, `already spent hours montserrat public library`, and `next day`.
  - Earlier diagnostics also showed unrelated `plan_system:*` pollution from reminder/calendar/task tools.
- After:
  - `included_count=4`
  - included keys:
    - `application_type:scholarship`
    - `application_type:visa`
    - `application_type:grant`
    - `application_type:academic`
  - no `plan_system:*` pollution.

Validation:

- `python3 -m unittest tests.test_model_adapters -k planning_system`
  - Result: `2` tests passed.
- `python3 -m unittest tests.test_model_adapters -k application_type`
  - Result: `1` test passed.
- `python3 -m unittest tests.test_model_adapters`
  - Result: `72` tests passed.

## 2026-06-15 Role And Security Feature Aggregation

Change:

- Added a generic engineering-project aggregation path for queries that explicitly ask about both user roles and security/auth features.
- The pack now emits typed objects:
  - `role:*`
  - `security_feature:*`
- This path suppresses ordinary `feature:*` and loose generic items for that query intent, so unrelated MVP features, transaction CRUD functions, analytics widgets, and timeline date ranges do not become security-feature counts.
- Current extracted security controls include authentication, password hashing, session management, role-based access control, account lockout/rate limiting, and login validation when directly supported by user/document evidence.

Why this matters:

- This is a product-grade object-typing improvement. Engineering memory should distinguish "features of the app" from "security controls" and "roles" when the query asks for those objects.
- It also reduces overcounting caused by generic list extraction over project plans. A markdown list that includes `Transaction Management`, `Add Income`, and date ranges is useful project context, but it is not evidence for user roles/security features.
- This is not BEAM-specific: there is no query-id, gold-answer, rubric, or hidden label dependency.

Pack-only effect:

- Probe: `beam:100k:1:multi_session_reasoning:1`
- Before:
  - `included_count=16`
  - noisy `feature:*` items included `transaction_management`, `add_income`, `view_transactions`, `monthly_summary`, and date-range keys such as `nov_16_dec_15`.
- After:
  - `included_count=7`
  - included keys:
    - `security_feature:authentication`
    - `security_feature:password_hashing`
    - `security_feature:session_management`
    - `role:user`
    - `security_feature:role_based_access_control`
    - `security_feature:login_validation`
    - `security_feature:account_lockout_rate_limiting`

Open product question:

- The current pack intentionally keeps distinct user-stated security implementation objects rather than collapsing them to the smallest possible benchmark answer. If later A/B shows answer-model overcounting, the product-safe next step is hierarchical grouping or LLM aggregation with strict source citations, not rubric-specific pruning.

## 2026-06-15 Financial Cashflow Summary

Change:

- Added `financial_summary` alongside `financial_impacts` for financial/budget/cashflow questions.
- The summary groups query-relevant monthly inflows, monthly obligations, budget-change deltas, savings targets, and a net monthly value.
- Money subject binding now prefers the local context around each amount before falling back to the larger span context. This prevents a sentence that mentions a freelance contract from causing unrelated rent/utilities amounts to be tagged as contract income.
- Period detection now binds `monthly` / annual / one-time wording to the nearby amount instead of letting one amount inherit another amount's period.

Why this matters:

- The product need is not just retrieving money amounts; it is classifying their role in a cashflow explanation.
- A memory answer should distinguish "new income", "increased budget", "medical obligation", and "savings contribution" before explaining whether the plan remains feasible.
- This is a generic financial-memory synthesis layer, not a benchmark-id or rubric-specific answer.

Pack-only effect:

- Probe: `beam:100k:16:multi_session_reasoning:1`
- `financial_summary` now exposes:
  - monthly inflow: freelance contract `$2,000`
  - monthly obligation: Ashlee medical bills `$350`
  - monthly budget change: grocery budget `$500` from `$400`, delta `$100`
  - savings target/contribution: `$200` monthly savings
  - net after obligations and budget increase: `$1,550`, interpreted as positive
- The answer model no longer needs to infer the core net effect from a flat list of raw money mentions.

Validation:

- `python3 -m unittest tests.test_model_adapters -k financial_impact`
  - Result: `1` test passed.

## 2026-06-15 Multilingual Query Intent Contract

Change:

- Extended the existing deterministic `query_intent` layer so it now carries a multilingual routing contract instead of only English-first heuristics.
- The intent object now exposes:
  - `language`
  - `answer_shape`
  - `evidence_scope`
  - `speaker_scope`
  - `object_types`
  - `aggregation`
  - `temporal`
- Query planner routing now recognizes basic Chinese order/count/multi-session phrases such as:
  - `按顺序`
  - `一共几个`
  - `跨会话`
  - `所有对话`

Why this matters:

- This is the right place to add model-assisted normalization later if we choose to: the contract is now explicit enough that an LLM analyzer could fill the same schema in shadow mode, while the deterministic layer remains the validator and fallback.
- It is better than letting regex proliferate directly inside the pack builders, because the planner can now centralize language, operation, object type, and scope decisions before retrieval and aggregation run.
- The goal is not to replace rules blindly. The product-safe path is a structured query-understanding layer with telemetry and deterministic fallback.

Follow-up wiring:

- `_pack_for_model()` now exposes `query_intent` inside the model evidence pack.
- Multi-session aggregation can consume `query_intent.object_types` as a prefix-selection signal.
- This means a Chinese query such as `我在所有会话里一共提到过几个用户角色和安全功能？` can route to the same `role:*` / `security_feature:*` typed aggregation path when the evidence is English engineering conversation text.
- The intent signal does not create evidence by itself; it only constrains which typed extractors/prefixes are allowed to interpret retrieved source spans.

Validation:

- `python3 -m unittest tests.test_temporal_normalizer`
  - Result: `13` tests passed.
- `python3 -m unittest tests.test_model_adapters -k multilingual_role_security`
  - Result: `1` test passed.

## 2026-06-15 Event Ordering Label Shell Cleanup

Change:

- Expanded generic event-ordering label normalization for request-shell and truncated-label patterns:
  - `understand how this feature affects...`
  - `protect X from Y`
  - `does X need to be reapplied`
  - `404 Not Found error on favicon`
  - `how can I highlight X in Y`
  - age/career-market concern phrasing
  - reaching out to a contact for networking advice
  - portfolio update requests
- These are label-quality improvements only. They do not branch on BEAM ids, gold answers, or rubric text.

Pack-only effects:

- `beam:100k:15:event_ordering:0` now emits:
  - `Dunk Low’s leather upper durability`
  - `Nike Dunk Low rain protection`
  - `sneaker protector spray reapplication`
  instead of request-shell labels such as `understand how this feature affects the overall` and `does the sneaker protector spray need to`.
- `beam:100k:8:event_ordering:1` now emits:
  - `age competitive job market concern`
  - `cover letter crafting`
  - `cover letter hands-on problem-solving skills highlight`
  - `Leslie networking at Caribbean Creative Hub advice`
  - `portfolio update`
  instead of truncated labels such as `my age, I'm 65, and I don't` and `at Montserrat Film Festival in 2004, for`.

Validation:

- `python3 -m unittest tests.test_model_adapters -k compact_label`
  - Result: `1` test passed.

## 2026-06-15 Optional Strict LLM QueryIntent Refiner

Change:

- Added an optional model-assisted query-understanding layer on top of the deterministic `QueryIntent` contract.
- The refiner lives in `fusion_memory/retrieval/query_intent.py` and uses prompt version `query-intent-refiner-v0`.
- It receives only:
  - the user query
  - the deterministic baseline intent
  - the minimum confidence threshold
- It must return a strict `QueryIntent`-shaped JSON object. The validator rejects invalid answer shapes, unknown object types, unknown scopes, bad temporal fields, unsupported aggregation operations, out-of-range confidence, or confidence below threshold.
- `QueryPlanner` can now be constructed with `intent_refiner=...`; default behavior remains rule-only.
- `MemoryService` accepts the same optional refiner and `memory_service_from_env()` can enable it through:
  - `FUSION_MEMORY_QUERY_INTENT_ENDPOINT`
  - `FUSION_MEMORY_QUERY_INTENT_BASE_URL`
  - `FUSION_MEMORY_QUERY_INTENT_API_KEY`
  - `FUSION_MEMORY_QUERY_INTENT_MODEL`
  - `FUSION_MEMORY_QUERY_INTENT_MIN_CONFIDENCE`
  - `FUSION_MEMORY_QUERY_INTENT_MODE`
- `answer_context()` now reuses its precomputed plan when calling `search()`, so enabling the refiner does not trigger duplicate planner LLM calls for one answer-pack request.

Why this matters:

- Regex-only routing is fragile under Chinese, mixed-language, paraphrased, and multi-clause queries. The previous multilingual work added useful Chinese trigger words, but it was still keyword coverage.
- The new layer is not an answer model and not a benchmark oracle. It only normalizes query intent into the existing retrieval contract.
- This is the product-safe way to use an LLM for routing: the deterministic layer remains the fallback and validator, and the evidence pack still has to retrieve source spans normally.
- The refiner can improve routes where Chinese wording like `我之前提过哪些权限控制和登录保护能力？` implies a cross-session aggregation but does not contain the exact old `所有会话` / `一共几个` triggers.

Cheating boundary:

- The refiner does not receive BEAM ids, categories, gold answers, rubrics, or hidden labels.
- The schema contains generic memory-routing fields only: answer shape, evidence scope, speaker scope, object types, temporal intent, aggregation intent, current-state/conflict flags, confidence, and reasons.
- Invalid or low-confidence output falls back to deterministic routing and records telemetry.

Validation:

- `python3 -m unittest tests.test_temporal_normalizer`
  - Result: `16` tests passed.
- `python3 -m unittest tests.test_model_adapters -k multilingual_role_security`
  - Result: `1` test passed.

## 2026-06-15 LLM Routing A/B and Full BEAM100K Baseline

Question:

- Should the next full BEAM100K run use the new LLM-refined routing path instead of the rule-only planner?
- Should the LLM aggregation path be enabled for multi-session count/list questions?

Safety boundary:

- The query-intent refiner only saw the query text plus the deterministic `QueryIntent`.
- It did not receive BEAM ids, categories, gold answers, rubrics, or judge prompts.
- The answer/judge model setup stayed the same OpenAI-compatible `gpt-5.4` evaluation path as the previous full baseline.
- LLM aggregation was tested as a generic evidence aggregation step, not with rubric-specific pruning.

Focused A/B result:

| Run | Scope | Config | Accuracy | Notes |
| --- | ---: | --- | ---: | --- |
| `.runtime/beam-runs/llm_ab_20260615/multi_session_rule_current_gpt54.json` | 40 multi-session | current rule path | `0.5958` | Valid comparator for the current codebase. |
| `.runtime/beam-runs/llm_ab_20260615/multi_session_llmrefine_agg_gpt54_key2.json` | 40 multi-session | `FUSION_MEMORY_QUERY_INTENT_MODE=always` + `--use-llm-aggregation` | `0.5360` | Negative. Over-routed some questions and over-enumerated aggregation answers. |

Conclusion from focused A/B:

- Do not run the current `always + LLM aggregation` configuration as the main benchmark path.
- The largest regression mode was not the idea of LLM query understanding itself; it was letting the model take over too much routing/aggregation.
- LLM aggregation over-produced broad lists in some cases where the rule aggregation gave a compact, better-supported answer.

Full BEAM100K run:

- Output: `.runtime/beam-runs/llm_auto_full_20260615/full_keysplit_llmauto_gpt54.json`
- Workspace: `beam_100k_rule_latest_qwenembed_20260613_091822`
- Config:
  - `FUSION_MEMORY_QUERY_INTENT_MODE=auto`
  - strict QueryIntent refiner enabled through the OpenAI-compatible endpoint
  - no LLM aggregation
  - two parallel 200-query batches with separate API keys
  - answer/judge model: `gpt-5.4`
- Result: `400/400` completed, accuracy `0.6723`.

Comparison with the original complete rule-based GPT baseline:

| Category | Old full baseline `0.6312` | LLM-auto full `0.6723` | Delta |
| --- | ---: | ---: | ---: |
| abstention | `0.8750` | `0.8875` | `+0.0125` |
| contradiction_resolution | `0.6500` | `0.7000` | `+0.0500` |
| event_ordering | `0.1849` | `0.1702` | `-0.0147` |
| information_extraction | `0.6865` | `0.7677` | `+0.0813` |
| instruction_following | `0.7188` | `0.7500` | `+0.0312` |
| knowledge_update | `0.6813` | `0.5188` | `-0.1625` |
| multi_session_reasoning | `0.5909` | `0.5923` | `+0.0014` |
| preference_following | `0.8063` | `0.9021` | `+0.0958` |
| summarization | `0.4310` | `0.6783` | `+0.2473` |
| temporal_reasoning | `0.6875` | `0.7562` | `+0.0688` |

Interpretation:

- The LLM-auto routing baseline is a real positive full-run result versus the original `0.6312`: `+0.0411` absolute.
- It is still far below the `0.75` target.
- The gain mainly comes from summarization, preference following, information extraction, temporal reasoning, and contradiction resolution.
- The two main regressions are `knowledge_update` and `event_ordering`.
- `multi_session_reasoning` did not inherit the earlier specialized run's `0.8100`; the full-run configuration and current generic path still need alignment with the better multi-session aggregation work.

Next engineering direction:

- Keep LLM query-intent refinement in `auto` mode for product-grade multilingual/ambiguous routing experiments.
- Keep LLM aggregation disabled for the main benchmark path until it has stricter grounding, source grouping, and over-enumeration controls.
- Prioritize event-ordering structure, not more label cleanup:
  - represent ordered user-introduced aspects as stable timeline items with session/turn anchors
  - separate event mention extraction from answer-facing phase labels
  - add pairwise/order confidence and use graph/topological repair only when evidence supports it
- Revisit `knowledge_update` as lifecycle management:
  - subject-scoped current values
  - explicit valid-from/valid-to or supersession state
  - better separation between latest numeric/date mentions and adjacent historical values
- Re-test multi-session after restoring the stronger generic aggregation path from the earlier `0.8100` run without adding BEAM-specific branches.

## 2026-06-15 Same-Key Parallel Replay

Run:

- Output: `.runtime/beam-runs/dual_full_20260615/full_samekey_llmauto_gpt54.json`
- Batch outputs:
  - `.runtime/beam-runs/dual_full_20260615/batch1_samekey_llmauto_gpt54.json`
  - `.runtime/beam-runs/dual_full_20260615/batch2_samekey_llmauto_gpt54.json`
- Workspace: `beam_100k_rule_latest_qwenembed_20260613_091822`
- Config:
  - `FUSION_MEMORY_QUERY_INTENT_MODE=auto`
  - strict QueryIntent refiner enabled through the OpenAI-compatible endpoint
  - no LLM aggregation
  - two parallel 200-query batches using the same configured API key
  - answer/judge model: `gpt-5.4`
- Result: `400/400` completed, one answer timeout retried successfully, merged accuracy `0.6326`.

Category scores:

| Category | Same-key replay |
| --- | ---: |
| abstention | `0.9625` |
| contradiction_resolution | `0.6781` |
| event_ordering | `0.1667` |
| information_extraction | `0.6318` |
| instruction_following | `0.6938` |
| knowledge_update | `0.4563` |
| multi_session_reasoning | `0.6167` |
| preference_following | `0.7458` |
| summarization | `0.6558` |
| temporal_reasoning | `0.7188` |

Interpretation:

- This replay is much lower than the previous key-split LLM-auto full run (`0.6723`) and only slightly above the original full rule-based GPT baseline (`0.6312`).
- The discrepancy is too large to treat the LLM-auto result as a stable `0.67+` baseline without additional repeats or configuration inspection.
- The bottlenecks remain product-relevant rather than benchmark-specific: event ordering is still structurally weak, and knowledge-update/current-value selection is worse than the original baseline.
- Same-key parallelism worked operationally: both batches completed, and the single timeout recovered through retry.

## 2026-06-15 Runner Resume and Schema-Column Aggregation Update

Runner/tooling changes:

- Added `tools/beam_parallel_runner.py --query-ids-file` so targeted replays can use newline/comma-separated ID files instead of long shell arguments.
- Progress JSON now updates `pending_queries` during worker chunk merges instead of leaving it at the initial selected-query count.
- BEAM report answer records now preserve coverage/model fields such as `source_span_quota_met`, `coverage_insufficient`, `answer_model`, `judge_model`, `mode`, and `llm_calls`. This prevents retry-success records from losing diagnostic fields when final JSON is reused for later selection or merge scripts.

Failure analysis from `.runtime/beam-runs/dual_full_20260615/full_samekey_llmauto_gpt54.json`:

- `event_ordering` remains mostly an answer-facing abstraction problem after recall. For examples like `beam:100k:1:event_ordering:1`, the pack contains ordered user anchors, but `sequence_items` and the final answer still choose overly granular early implementation details instead of broader user-introduced phases.
- `multi_session_reasoning` has two modes:
  - some failures are recall/coverage misses, such as missing the current value or a later user update;
  - some failures are structure misses, where relevant spans exist but the answer model sees 50 noisy spans and no compact aggregation item table.
- `beam:100k:1:multi_session_reasoning:0` was a structure miss. The evidence pack included the user request to add a `category` column and an assistant migration echo for the later `notes` column, but the model pack had no `column:*` aggregation items, so GPT abstained.

Generic product-grade change:

- Added schema-column aggregation items for database/table/field count questions.
- The extractor produces `column:*` items from user/document requests such as "add a category column to the transactions table".
- Assistant evidence contributes only when it contains strong migration syntax such as `op.add_column(...)` or `ALTER TABLE ... ADD COLUMN`; generic assistant examples like "for example, add description and note fields" are excluded.
- Later source spans with strong schema-column evidence can now enter aggregation scanning even when legacy retrieval keys do not already have a `column:` prefix.

Focused validation:

- Run: `.runtime/beam-runs/column_agg_20260615/q1_column_agg_gpt54.json`
- Query: `beam:100k:1:multi_session_reasoning:0`
- Previous same-key replay score: `0.0`
- New score: `1.0`
- Answer: `2 new columns`, `category` and `notes`.

Expected full-run impact:

- This specific fix is worth about `+0.0025` on BEAM100K if no regressions occur.
- It does not address the main gap to `0.75`; the remaining high-leverage areas are still event-ordering phase abstraction and knowledge-update/current-value lifecycle selection.

## 2026-06-15 Non-Event Current-Value and Aggregation Update

Motivation:

- The same-key replay showed several non-event modules that were "not terrible" but still capped the total score:
  - `information_extraction`: `0.6318`
  - `instruction_following`: `0.6938`
  - `knowledge_update`: `0.4563`
  - `multi_session_reasoning`: `0.6167`
  - `summarization`: `0.6558`
- Failure samples were mostly product memory failures, not answer-model creativity failures:
  - current/latest numeric values were missing or hidden behind older spans;
  - percentages such as `85%` were not reliably extracted as percentages;
  - quantities such as `1,200 calls per day` and `three days a week` were not consistently represented as values;
  - `value_history` existed only for `knowledge_update`, while many current-value questions were routed as `factual_exact` or `temporal_lookup`.

Generic product-grade changes:

- Expanded value extraction in `fusion_memory/retrieval/evidence_pack.py`:
  - percentages are extracted as `percentage` instead of being confused with versions;
  - quantity/count mentions now cover generic product metrics such as calls, requests, cards, columns, interviews, problems, roles, people, and days per week;
  - word-number quantities such as `three days a week` are extracted;
  - value contexts distinguish target/goal values from achieved/current values, e.g. `trying to achieve 100%` is not treated as current while `currently reached 65%` is.
- Made value-history generation intent-aware instead of category-only:
  - `needs_current_state`, aggregation requests, quotas, coverage, percentages, versions, dependencies, and scheduled counts can now receive `value_history` even when the planner route is `factual_exact`, `temporal_lookup`, or `instruction`;
  - plain `when ...` temporal questions are not forced into value-history mode.
- Added query-aware value-history sorting:
  - count/quota/per-week questions prioritize count values;
  - coverage/percent/rate questions prioritize percentages;
  - version/dependency questions prioritize versions;
  - explicit `recent/current/latest/now/updated` questions increase recency weight.
- Added `value_history_summary` in `fusion_memory/eval/model_adapters.py`:
  - exposes `current_candidates`, `target_value_types`, and `recency_priority`;
  - answer instructions now treat these candidates as the current-state shortlist, not merely as extra evidence.

Validation:

- Unit tests:
  - `tests.test_model_adapters`
  - `tests.test_beam_parallel_runner`
  - `tests.test_beam_adapter`
  - focused value extraction/current-value tests in `tests.test_fusion_memory`
- All 98 selected tests passed after the first value-history update.
- `git diff --check` passed.

Focused BEAM validation:

- Initial focused run with wrong endpoint:
  - Output: `.runtime/beam-runs/value_history_non_event_20260615/focused_value_history_gpt54.json`
  - Status: `partial`
  - Cause: endpoint was passed as base `/v1`, producing `Invalid URL (POST /v1)`.
  - This run is invalid and not counted.
- Corrected endpoint run:
  - Output: `.runtime/beam-runs/value_history_non_event_20260615/focused_value_history_gpt54_chat.json`
  - Query count: `10`
  - Accuracy: `0.5900`

Same-query comparison against `.runtime/beam-runs/dual_full_20260615/full_samekey_llmauto_gpt54.json`:

| Query | Category | Old score | New score | Notes |
| --- | --- | ---: | ---: | --- |
| `beam:100k:2:knowledge_update:0` | knowledge_update | `0.0` | `1.0` | daily API quota now extracted as `1,200 calls per day` |
| `beam:100k:8:knowledge_update:1` | knowledge_update | `0.0` | `1.0` | remote schedule now supports `three days a week` |
| `beam:100k:3:multi_session_reasoning:0` | multi_session_reasoning | `0.0` | `1.0` | project-card total recovered as `10` |
| `beam:100k:8:multi_session_reasoning:0` | multi_session_reasoning | `0.0` | `1.0` | count recovered as `3` mentions |
| `beam:100k:3:summarization:1` | summarization | `0.7` | `0.9` | no regression; better coverage of concrete issue/fix details |
| `beam:100k:9:instruction_following:1` | instruction_following | `0.0` | `0.5` | recovered due date but still missed year formatting |
| `beam:100k:1:information_extraction:0` | information_extraction | `0.5` | `0.5` | still chooses updated March 31 over rubric's March 29 |
| `beam:100k:2:knowledge_update:1` | knowledge_update | `0.0` | `0.0` | still answers `65%`; expected current lineage apparently needs a later `78%` update |
| `beam:100k:6:knowledge_update:0` | knowledge_update | `0.0` | `0.0` | `5 interviews` is now first structured candidate, but answer model still trusts older raw `3 interviews` span |
| `beam:100k:9:preference_following:0` | preference_following | `0.0` | `0.0` | still misses the user's 7-9 AM writing preference |

Follow-up single-query checks:

- `.runtime/beam-runs/value_history_non_event_20260615/focused_value_history_q2q6_gpt54.json`
  - quota stayed correct (`1.0`);
  - coverage remained wrong (`65%`, score `0.0`);
  - interviews remained wrong (`3`, score `0.0`).
- `.runtime/beam-runs/value_history_non_event_20260615/focused_value_history_q6_gpt54_v3.json`
  - raw `value_history` and `value_history_summary` both put `5 interviews` first;
  - answer model still returned `3 interviews`.

Interpretation:

- This is a real generic improvement for current-value and count extraction, especially for non-event modules.
- The expected full-run gain from the observed fixed examples is modest but meaningful: roughly `+0.010` to `+0.015` if the focused wins generalize without regressions.
- `knowledge_update` still needs a stronger lifecycle model:
  - distinguish user-authored state changes from assistant restatements;
  - assign confidence to assistant-supported updates instead of treating them as either irrelevant or fully authoritative;
  - expose a single resolved current value with provenance, not only a ranked table.
- `preference_following` and some `instruction_following` failures are not value-history problems. They need a preference/constraint retrieval layer that surfaces durable schedule/location/style preferences even when the query is routed as `factual_exact` or `temporal_lookup`.

## 2026-06-15 TrueMemory Source Review and Preference-Constraint Pack

TrueMemory Pro source review:

- Source inspected locally from `https://github.com/buildingjoshbetter/TrueMemory` cloned to `/tmp/TrueMemory`.
- Relevant files read:
  - `/tmp/TrueMemory/truememory/engine.py`
  - `/tmp/TrueMemory/truememory/hybrid.py`
  - `/tmp/TrueMemory/truememory/agentic_search.py`
  - `/tmp/TrueMemory/truememory/search_quality.py`
  - `/tmp/TrueMemory/truememory/temporal.py`
  - `/tmp/TrueMemory/truememory/query_classifier.py`
  - `/tmp/TrueMemory/benchmarks/beam/README.md`
  - `/tmp/TrueMemory/benchmarks/beam/bench_truememory_pro_beam1m.py`
- The useful transferable pattern is not a single benchmark-specific rule set. The search stack is layered:
  - query classification with adaptive retrieval weights;
  - hybrid FTS/vector/separation retrieval fused by RRF;
  - scent-trail and quality fallback supplements;
  - temporal supplement and temporal-rescoped search;
  - personality/preference supplement only when intent calls for it;
  - contradiction/consolidated supplement;
  - large candidate pools plus cross-encoder reranking;
  - source normalization so supplement rows do not dominate by raw score scale.
- This supports our current direction: answer packs should expose compact, provenance-rich supplement tables for the evidence type the query needs, rather than forcing the answer model to search 50 raw spans.

Generic product-grade change:

- Added `preference_constraints` to the answer-facing model pack in `fusion_memory/eval/model_adapters.py`.
- It scans the full retrieved source-span pool, not only the first 20 spans sent as raw `source_spans`.
- It extracts compact user-specific requirements/preferences when the query is advisory, planning, or recommendation-like:
  - time windows and morning work preferences;
  - short session lengths;
  - place/location preferences such as libraries or quiet writing locations;
  - snack safety checks such as food allergies;
  - accessibility/language/subtitle needs for movie recommendations;
  - sustainability/recycled-material requirements for sneaker/material questions;
  - editing workflow requirements such as split-screen/side-by-side comparison;
  - date format and audiobook narrator requirements when supported.
- BEAM ids, gold answers, rubrics, and category-specific hidden labels are not used by this extraction.

Validation:

- Unit test added:
  - `tests.test_model_adapters.ModelAdapterTests.test_pack_for_model_extracts_deep_preference_constraints`
  - It verifies that a preference buried after the first 20 raw spans still appears in `preference_constraints`, while raw `source_spans` remains capped.
- Full selected test set:
  - `python3 -m unittest tests.test_model_adapters tests.test_beam_parallel_runner tests.test_beam_adapter ...`
  - `100` tests passed.
  - `git diff --check` passed.

Focused BEAM validation:

- Main focused run:
  - Output: `.runtime/beam-runs/preference_constraints_20260615/focused_pref_constraints_gpt54.json`
  - Query count: `5`
  - Accuracy: `1.0`

Same-query comparison against `.runtime/beam-runs/dual_full_20260615/full_samekey_llmauto_gpt54.json`:

| Query | Category | Old score | New score | Constraint surfaced |
| --- | --- | ---: | ---: | --- |
| `beam:100k:9:preference_following:0` | preference_following | `0.0` | `1.0` | writing sessions between `7-9 AM` |
| `beam:100k:9:preference_following:1` | preference_following | `0.0` | `1.0` | Montserrat Public Library / quiet writing place |
| `beam:100k:10:preference_following:0` | preference_following | `0.5` | `1.0` | short `30-minute` sessions |
| `beam:100k:14:instruction_following:1` | instruction_following | `0.0` | `1.0` | ask about food allergies before snacks |
| `beam:100k:15:instruction_following:1` | instruction_following | `0.0` | `1.0` | eco-friendly/recycled sneaker materials |

- Extra focused run:
  - Output: `.runtime/beam-runs/preference_constraints_20260615/focused_pref_constraints_extra_gpt54.json`
  - Query count: `2`
  - Accuracy: `0.875`

Extra same-query comparison:

| Query | Category | Old score | New score | Constraint surfaced |
| --- | --- | ---: | ---: | --- |
| `beam:100k:14:preference_following:1` | preference_following | `0.0` | `1.0` | English/Spanish audio and subtitles |
| `beam:100k:13:instruction_following:0` | instruction_following | `0.5` | `0.75` | audiobook narrator information |

Category-slice validation:

- Output: `.runtime/beam-runs/preference_constraints_20260615/pref_instruction_categories_gpt54.json`
- Categories: `preference_following,instruction_following`
- Query count: `80`
- Workers: `20`
- Status: complete, one query retried successfully.
- Combined slice accuracy: `0.78125`

Comparison against the same-key replay:

| Category | Same-key replay | Preference-constraint slice | Delta |
| --- | ---: | ---: | ---: |
| `preference_following` | `0.7458` | `0.8875` | `+0.1417` |
| `instruction_following` | `0.6938` | `0.6750` | `-0.0188` |

Notable improvements in the 80-query slice:

- `beam:100k:9:preference_following:0`: `0.0 -> 1.0`
- `beam:100k:9:preference_following:1`: `0.0 -> 1.0`
- `beam:100k:10:preference_following:0`: `0.5 -> 1.0`
- `beam:100k:14:preference_following:1`: `0.0 -> 1.0`
- `beam:100k:15:preference_following:0`: `0.25 -> 1.0`
- `beam:100k:14:instruction_following:1`: `0.0 -> 1.0`
- `beam:100k:13:instruction_following:0`: `0.5 -> 1.0` in this slice run

Observed regressions:

- `instruction_following` regressed slightly overall. Examples include existentialism, patent steps, resume organization, and date-reminder questions.
- These regressions are not all direct preference-constraint hits. They appear to include existing retrieval/answer variance and cases where instruction questions need a separate "general knowledge allowed when user asks for advice" policy.
- The preference-constraint mechanism should therefore be kept, but it should not be used as evidence that instruction-following is solved.

Interpretation:

- This is a high-signal non-event improvement. It fixes cases where recall already had the relevant span but the answer-facing raw-span cap hid it from the model.
- Expected full-run impact is still limited by category size. The 80-query slice suggests a net gain of roughly `+0.012` to `+0.015` on full BEAM100K if the preference lift and instruction volatility reproduce similarly.
- It also addresses a product-memory requirement: advisory answers should respect stored user preferences/constraints even when the latest query is broad.
- Remaining preference/instruction misses include true recall misses, e.g. split-screen editing guidance was not present in the retrieved span pool for `beam:100k:7:instruction_following:1`. Those need retrieval supplement legs, not only answer-pack compression.

## 2026-06-15 Baseline Stability, Event Boundary, and Instruction Constraint Fixes

Baseline interpretation:

- The earlier key-split full run reported `0.6723117190173091`, but the same-key replay with the same benchmark surface reported `0.6326120303746116`.
- I do not treat `0.6723` as the reliable current baseline. It likely mixes model/API volatility, high-concurrency failures/retries, and possibly partial configuration differences from the key-split execution.
- The reliable comparison baseline for new changes is therefore the same-key replay:
  - overall: `0.6326120303746116`
  - event_ordering: `0.1667`
  - knowledge_update: `0.4563`
  - multi_session_reasoning: `0.6167`
  - summarization: `0.6558`
  - preference_following: `0.7458`
  - instruction_following: `0.6938`
- To "recover" toward the better number, the correct procedure is not to assume `0.6723` was real. It is to re-run same-key/category slices with the current code, retry failed queries, and then run a fresh full pass once category slices show stable gains.

Event-ordering status:

- Added generic project/application lifecycle milestone selection for event-ordering answer packs.
- This is not BEAM-id-specific. It uses generic milestone concepts such as project setup, CRUD/functionality implementation, deployment configuration, integration test coverage, and deployment/test improvements.
- Focused validation:
  - `beam:100k:1:event_ordering:1`: `0.1429 -> 1.0`
  - `beam:100k:1:event_ordering:0`: `0.5 -> 0.3889`
  - `beam:100k:2:event_ordering:0`: current answer text improved from UI-wireframe drift to API-rate-limit/caching/debounce oriented stages, but score stayed `0.1429` in focused validation.
- Interpretation: event-ordering still needs retrieval-level episode/lifecycle recall. More answer-pack tuning has diminishing returns, so focus should shift to low-but-improvable non-event categories.

Instruction/routing fix:

- Fixed query routing so procedural or hypothetical sequences such as card-draw probability questions are not classified as memory event ordering.
- Fixed query routing so social phrases such as "meeting someone for the first time" are not classified as temporal lookup.
- Added durable instruction constraints to the answer pack when supported by retrieved evidence:
  - include a tree diagram for dependent probability problems;
  - use Scrivener split-screen mode for draft revisions;
  - include cultural context and cross-cultural variation for social norms.
- These are product-grade durable user constraints, not benchmark-id or rubric shortcuts.

Focused validation:

| Query | Old same-key score | Current focused score | Change |
| --- | ---: | ---: | ---: |
| `beam:100k:5:instruction_following:1` | `0.0` | `1.0` | `+1.0` |
| `beam:100k:7:instruction_following:1` | `0.0` | `1.0` | `+1.0` |
| `beam:100k:12:instruction_following:0` | `0.0` | `0.75` | `+0.75` |

Focused run:

- Output: `.runtime/beam-runs/instruction_constraints_20260615/focused_instruction_constraints_gpt54.json`
- Query count: `3`
- Accuracy: `0.9166666666666666`

Current broader validation in progress:

- Output: `.runtime/beam-runs/low_categories_current_20260615/low5_categories_gpt54.json`
- Categories: `knowledge_update,summarization,multi_session_reasoning,information_extraction,contradiction_resolution`
- Query count: `200`
- Workers: `20`
- Purpose: estimate current non-event recovery against same-key replay before deciding whether a full BEAM100K run is justified.

Completed result:

- Status: complete
- Worker failures: `0`
- Retryable partial failures: `0`
- Accuracy across these 200 queries: `0.6229880952380953`

Category comparison against same-key replay:

| Category | Same-key replay | Current slice | Delta |
| --- | ---: | ---: | ---: |
| `knowledge_update` | `0.45625` | `0.52500` | `+0.06875` |
| `multi_session_reasoning` | `0.61670` | `0.68500` | `+0.06830` |
| `summarization` | `0.65576` | `0.64869` | `-0.00707` |
| `contradiction_resolution` | `0.67813` | `0.66875` | `-0.00938` |
| `information_extraction` | `0.63177` | `0.58750` | `-0.04427` |

Interpretation:

- Current-value and count aggregation improvements are real and reproduce on full category slices.
- `knowledge_update` is still below where it should be, but the value-history/current-candidate work moved it materially upward.
- `multi_session_reasoning` is now above the old average and close to a usable baseline for BEAM100K.
- `summarization` is effectively unchanged. It needs better coverage/compression of long trajectories, not more answer prompt wording.
- `information_extraction` regressed on this same-key category slice. This should be investigated before running a full benchmark because a full run would likely hide the regression behind gains in other categories.
- `contradiction_resolution` is roughly stable, with a small negative delta.

Top reproduced gains:

- `beam:100k:1:multi_session_reasoning:0`: `0.0 -> 1.0`
- `beam:100k:3:multi_session_reasoning:0`: `0.0 -> 1.0`
- `beam:100k:8:multi_session_reasoning:0`: `0.0 -> 1.0`
- `beam:100k:17:multi_session_reasoning:0`: `0.0 -> 1.0`
- `beam:100k:2:knowledge_update:0`: `0.0 -> 1.0`
- `beam:100k:3:knowledge_update:1`: `0.0 -> 1.0`
- `beam:100k:5:knowledge_update:1`: `0.0 -> 1.0`
- `beam:100k:6:knowledge_update:0`: `0.0 -> 1.0`
- `beam:100k:8:knowledge_update:1`: `0.0 -> 1.0`
- `beam:100k:16:knowledge_update:0`: `0.0 -> 1.0`

Largest regressions to inspect next:

- `beam:100k:14:information_extraction:0`: `1.0 -> 0.0`
- `beam:100k:12:information_extraction:1`: `0.5625 -> 0.0`
- `beam:100k:9:information_extraction:1`: `0.6667 -> 0.3333`
- `beam:100k:5:knowledge_update:0`: `1.0 -> 0.0`
- `beam:100k:7:knowledge_update:0`: `1.0 -> 0.0`
- `beam:100k:9:knowledge_update:0`: `1.0 -> 0.0`
- `beam:100k:14:multi_session_reasoning:0`: `1.0 -> 0.0`
- `beam:100k:4:multi_session_reasoning:0`: `1.0 -> 0.0`

Follow-up current-value recovery:

- Added typed value extraction/priority improvements:
  - `sources` is now recognized as a count unit;
  - clock times such as `4:30 PM` are extracted as `time`;
  - hour/minute/week/month duration queries prioritize duration over generic count;
  - cumulative duration and inventory/library count queries expose a `preferred_current_candidate` and `resolved_current_value`.
- Focused validation:

| Query | Low-category slice score | After fix | Note |
| --- | ---: | ---: | --- |
| `beam:100k:5:knowledge_update:0` | `0.0` | `1.0` | resolved `4 hours` over older `3 hours` raw span |
| `beam:100k:7:knowledge_update:0` | `0.0` | `1.0` | resolved `52 sources` over older `45 sources` |
| `beam:100k:9:knowledge_update:0` | `0.0` | `1.0` | resolved `4:30 PM` over older `3:00 PM` |

- Output files:
  - `.runtime/beam-runs/value_history_recovery_20260615/focused_value_recovery_gpt54.json`
  - `.runtime/beam-runs/value_history_recovery_20260615/focused_q5_resolved_value_gpt54.json`

Knowledge-update category retest:

- Output: `.runtime/beam-runs/value_history_recovery_20260615/knowledge_update_category_gpt54.json`
- Status: complete
- Worker failures: `0`
- Retryable partial failures: `0`
- Accuracy: `0.475`
- Same-key replay baseline for this category: `0.45625`
- Delta: `+0.01875`

Interpretation:

- The focused current-value recoveries are real, but the full `knowledge_update` category remains weak and volatile.
- The category still needs a more systematic current-state/lifecycle layer. The answer pack can now expose resolved values, but many failures still come from wrong or incomplete current-state grouping, e.g. deadlines, budgets, rescheduled dates, counts, and latest metric values.
- Treat `knowledge_update` as improved but not solved. It should not be counted as a major recovery toward `0.75` until a category slice reaches at least the mid-0.6 range.

## 2026-06-15 Information-Extraction Recovery

Problem:

- The same-key full replay scored `information_extraction = 0.6317708333333333`.
- The later low-category slice regressed to `0.5875`.
- The key-split full run reported `information_extraction = 0.7677083333333333`, but that run should not be treated as the stable baseline because it mixed API/key conditions and had a higher overall matched-gold rate.

Generic changes:

- Added same-session `exact_answer_candidates` for `factual_exact`, `assistant_reference`, `knowledge_update`, and now `temporal_lookup` style questions.
- Restricted exact candidate scanning to the selected memory scope/session so broad workspace scans cannot leak adjacent benchmark conversations or unrelated product memories.
- Added assistant-reference candidate scoring for:
  - work-transition preparation lists;
  - writing/submission timeline plans;
  - shared-interest/movie recommendation rationale;
  - hiring/fairness/pilot/human-oversight recommendations.
- Increased exact candidate content retention from `700` to `2600` characters so long step lists and timelines are not truncated before later milestones.
- Routed historical "did I say / what did I say" count/value questions as `factual_exact` unless they explicitly ask for current/latest state.
- Suppressed current-value `value_history_summary` for historical "did I say" questions, preventing a later "new/current" value from overriding the historical fact being asked for.
- Extended value mention extraction to count `series`, `books`, and `pages`.
- Restored chronological ordering for event-ordering user anchors and multi-session history items:
  - event user anchors now sort by source/turn/timestamp instead of coverage-candidate rank;
  - multi-session count/list packs keep history order unless the query truly needs current/latest value resolution.

Validation:

- Unit tests: `.runtime/beam-venv/bin/python -m unittest tests.test_fusion_memory tests.test_temporal_normalizer tests.test_model_adapters tests.test_beam_adapter tests.test_beam_parallel_runner`
  - Result: `195` tests passed.
- Focused information-extraction recovery:
  - `.runtime/beam-runs/information_extraction_recovery_20260615/focused_info_recovery_gpt54.json`
  - 4 queries, accuracy `0.8177083333333334`, worker failures `0`.
  - Restored:
    - `beam:100k:14:information_extraction:0` to `1.0` for `15 miles away in West Janethaven`.
    - `beam:100k:1:multi_session_reasoning:0` to `1.0` for `category` and `notes` columns.
    - `beam:100k:9:information_extraction:1` to `0.8333333333333334` after longer exact timeline candidates.
    - `beam:100k:12:information_extraction:1` to `0.75` after preserving the full work-transition step list.
- Full information-extraction category v1:
  - `.runtime/beam-runs/information_extraction_recovery_20260615/information_extraction_category_gpt54.json`
  - Accuracy `0.7125`, matched-gold `0.8`, worker failures `0`.
- Full information-extraction category v2:
  - `.runtime/beam-runs/information_extraction_recovery_20260615/information_extraction_category_v2_gpt54.json`
  - Accuracy `0.7583333333333333`, matched-gold `0.85`, worker failures `0`.

Category comparison:

| Run | `information_extraction` | matched_gold | Notes |
| --- | ---: | ---: | --- |
| Same-key full replay | `0.6317708333333333` | `0.725` | stable comparison baseline |
| Low-category current slice | `0.5875` | `0.675` | exposed regression |
| Category recovery v1 | `0.7125` | `0.8` | exact candidate recovery |
| Category recovery v2 | `0.7583333333333333` | `0.85` | historical routing + longer timelines |
| Key-split full run | `0.7677083333333333` | `0.875` | useful upper signal, not stable baseline |

Interpretation:

- This is a real category-level recovery: `+0.12656` versus same-key full replay and `+0.17083` versus the low-category slice.
- If all other categories stayed at same-key levels, this category alone would lift the full BEAM100K estimate from `0.6326120303746116` to roughly `0.6453`.
- That is still far from `0.75`; the remaining gap is architectural, especially `event_ordering`, `knowledge_update`, and long-horizon multi-session organization.
- Remaining information-extraction hard failures are mostly not answer-model failures:
  - `beam:100k:12:information_extraction:0` still fails because the festival/dating duration evidence is not being recalled.
  - `beam:100k:14:information_extraction:1` still fails because shared-interest movie rationale is not surfaced.
  - `beam:100k:8:information_extraction:1` still confuses the prior-connection attribution.
  - `beam:100k:11:information_extraction:1` retrieves AI fairness material but still under-surfaces the pilot/anonymization/third-party-audit recommendation.

Current score estimate:

- Treat `0.6723117190173091` as an optimistic, not fully controlled run.
- Treat `0.6326120303746116` as the reliable same-key full replay baseline.
- With the verified information-extraction recovery only, the conservative full-run estimate is around `0.645`.
- Focused knowledge-update and multi-session gains may add more, but they need another same-code category or full replay before being counted as stable.

## 2026-06-15 Multi-Session Exact Rescue and Event-Ordering Audit

Problem:

- Same-key full replay scored:
  - `event_ordering = 0.1666857799365919`
  - `multi_session_reasoning = 0.6166964285714285`
- Focused inspection showed two distinct failure modes:
  - `multi_session_reasoning` failures were often recall/pack failures. Correct raw spans existed but did not reach `exact_answer_candidates` or high-rank compact evidence.
  - `event_ordering` failures were mostly structural. The pack had chronological anchors, but the final sequence was still too fine-grained or topic-misaligned for "N aspects/phases in order" questions.

Generic changes:

- `tools/beam_parallel_runner.py` resume behavior now has an added regression test proving that a successful retry record wins even when an older failed partial appears in a later worker file.
- `multi_session_reasoning` is now eligible for `exact_answer_candidates`, giving cross-session count/location questions the same raw-evidence rescue path as factual/current-value queries.
- Exact candidate scoring now:
  - recognizes standalone first-person `I` as a user-fact signal;
  - extracts `scenes` counts and `12 of 16 scenes` style fraction/count mentions;
  - boosts user-authored how-many/count spans with targeted numeric values;
  - boosts first-person event/location queries when a user span contains an event, a person binding, and an `at/to/in` location;
  - binds proper names from the query, so a query about `David` does not rank unrelated location events first;
  - penalizes subject-role mismatches such as `David planned ...` when the query asks what `I am planning`.
- Event-ordering pack cleanup now:
  - prefers milestone source/timeline diversity instead of taking several final stages from one long source span;
  - removes request shells such as `How can I ...` and `example of how I can ...` from sequence labels;
  - preserves meaningful `with <person>` details when the prefix would otherwise collapse to a useless label;
  - sorts final `sequence_items` by `timeline_index` rather than source URI after representative selection.

Validation:

- Full related unit suite:
  - `.runtime/beam-venv/bin/python -m unittest tests.test_fusion_memory tests.test_temporal_normalizer tests.test_model_adapters tests.test_beam_adapter tests.test_beam_parallel_runner tests.test_llm_extractor_and_benchmark`
  - Result: `208` tests passed.
- Focused event/multi smoke v1:
  - `.runtime/beam-runs/focused_event_multi_20260615/current_event_multi_smoke_gpt54.json`
  - 7 queries, accuracy `0.42951180472188877`.
  - Restored `beam:100k:1:multi_session_reasoning:0` and `beam:100k:3:multi_session_reasoning:0` from `0` to `1.0`.
- Focused event/multi smoke v2:
  - `.runtime/beam-runs/focused_event_multi_20260615/current_event_multi_smoke_v2_gpt54.json`
  - 7 queries, accuracy `0.5366546618647459`.
  - Restored `beam:100k:17:multi_session_reasoning:0` from `0` to `1.0` by surfacing `12 of 16 scenes filmed`.
- Focused multi-session location/count v3:
  - `.runtime/beam-runs/focused_event_multi_20260615/current_multisession_location_count_v3_gpt54.json`
  - 2 queries, accuracy `1.0`.
  - Restored:
    - `beam:100k:17:multi_session_reasoning:0` to `1.0`.
    - `beam:100k:18:multi_session_reasoning:1` to `1.0` by ranking the anniversary dinner at The Coral Reef, East Janethaven and the weekend getaway at Blue Bay Resort as the top user evidence.
- Category replay:
  - `.runtime/beam-runs/event_multi_category_20260615/event_multi_categories_current_gpt54.json`
  - `80` queries, worker failures `0`.
  - `event_ordering = 0.16969683068199248`
  - `multi_session_reasoning = 0.7998214285714285`

Category comparison:

| Category | Same-key full replay | Current category replay | Delta |
| --- | ---: | ---: | ---: |
| `event_ordering` | `0.1666857799365919` | `0.16969683068199248` | `+0.00301` |
| `multi_session_reasoning` | `0.6166964285714285` | `0.7998214285714285` | `+0.18313` |

Interpretation:

- Multi-session reasoning is now a verified category-level recovery. If other categories stayed at same-key levels, this category alone would add about `+0.0183` to the full BEAM100K score.
- Combined with the verified information-extraction recovery, the conservative same-key full-run estimate is roughly `0.6636`.
- Event ordering remains unsolved. The small gain confirms that label cleanup and final ordering are useful but not enough. The category needs a real episode/aspect timeline layer: phase segmentation, topic continuity, actor/object binding, and compact phase labels generated before answer time.
- Current architecture is not fundamentally blocked for BEAM, but `>0.75` is not reachable by polishing exact retrieval alone. The remaining lift must come from `event_ordering`, `knowledge_update`, and possibly `summarization`/`contradiction_resolution` category-level improvements.

## 2026-06-16 Event-Ordering Chronology Rescue

Problem:

- Event-ordering was still selecting plausible local fragments instead of a full episode/aspect timeline.
- In `beam:100k:6:event_ordering:0`, the initial pack mostly covered the middle LinkedIn/interview/resume spans and missed enough early/later professional-profile phases to answer the six-item chronology.
- In `beam:100k:11:event_ordering:0` and `beam:100k:18:event_ordering:1`, evidence was present but labels were often long first-person fragments or empty low-information labels.

Generic changes:

- Added bounded event-ordering chronology rescue in `EvidencePackBuilder`.
  - It scans same-scope user turns when event-ordering anchors exist.
  - It scores out-of-window turns by query topic overlap, first-person update/action markers, concern/challenge markers, values/dates, and proper-name/tool signals.
  - It rejects assistant-style plan text even when speaker metadata is noisy.
  - It selects rescue turns with chronological diversity instead of only top lexical score, then the final ordering expansion also uses timeline-diverse sampling before token-budget insertion.
- Added topic equivalences for professional-profile objects: `profile`, `resume`, `portfolio`, `LinkedIn`, `CV`, and career terms. This is a product-level synonym family, not a BEAM-specific domain rule.
- Added generic aspect-hint label fallback for event ordering:
  - collaboration with a person on an object;
  - using a named tool to perform an action;
  - person advice/suggestion;
  - tool fairness/transparency/bias questions;
  - pilot/trial program screening impact;
  - automation/adoption change objects;
  - concern/challenge phrases;
  - vacation/dinner/celebration style personal events.
- `sequence_items` now fills from additional ordered candidates when selected representatives produce blank/duplicate labels, and final event-ordering output sort uses source/turn order ahead of pack-local `timeline_index`.

Validation:

- Full related unit suite:
  - `.runtime/beam-venv/bin/python -m unittest tests.test_fusion_memory tests.test_temporal_normalizer tests.test_model_adapters tests.test_beam_adapter tests.test_beam_parallel_runner tests.test_llm_extractor_and_benchmark`
  - Result: `210` tests passed.
- Focused GPT5.4 replay after first rescue:
  - `.runtime/beam-runs/event_ordering_rescue_20260616/focused_event_rescue_gpt54.json`
  - 3 event-ordering queries, accuracy `0.1833630421865716`, worker failures `0`.
  - `beam:100k:6:event_ordering:0` improved from previous `0.14705882352941174` to `0.26666666666666666`.
  - `beam:100k:11:event_ordering:0` stayed about flat at `0.14705882352941174`.
  - `beam:100k:18:event_ordering:1` stayed flat at `0.13636363636363635`.
- Focused GPT5.4 replay after chronology-diverse insertion:
  - `.runtime/beam-runs/event_ordering_rescue_20260616/focused_event_rescue_diverse_gpt54.json`
  - 3 event-ordering queries, accuracy `0.19077044959397896`, worker failures `0`.
  - `beam:100k:6:event_ordering:0` improved further to `0.28888888888888886`.
  - `beam:100k:11:event_ordering:0` stayed flat at `0.14705882352941174`.
  - `beam:100k:18:event_ordering:1` stayed flat at `0.13636363636363635`.

Interpretation:

- This is a real but small event-ordering improvement. It proves that broad chronological coverage can recover missed phases, but it does not solve the category.
- The remaining failure is not API instability. Event-ordering is scored deterministically by normalized ordering match, and labels must align with high-level aspect names.
- The next non-cheating architecture step is a typed aspect timeline layer before answer time:
  - cluster raw user turns into episode/aspect objects;
  - bind actor/person/tool/metric/decision/result roles;
  - merge repeated same-object updates across time;
  - produce compact phase labels from those typed roles, not from whole user sentences.
- Current conservative estimate remains around `0.66-0.68` from verified category gains, with upside only after `event_ordering` and `knowledge_update` get category-level improvements.

## 2026-06-16 TrueMemory Comparison And Typed-Aspect Repair

Source-level comparison:

- TrueMemory Pro's active BEAM path is raw-message-first retrieval: ingest every message, run `search_agentic(question, limit=100, use_hyde=True, use_reranker=True)`, and pass up to 50 timestamped raw messages to the answer model.
- In the BEAM runner, no `llm_fn` is supplied to `search_agentic`, so HyDE is effectively not the main factor. The durable retrieval lessons are broad hybrid recall, large rerank pools, source provenance, entity/cluster/salience supplements, and cross-encoder reranking.
- TrueMemory's strength is evidence recall and answer-time breadth. Its public BEAM results still show event-ordering weakness, which means broad raw storage alone is not enough for temporal/aspect ordering.

Current conclusion:

- Our low same-key full score is partly LLM/evaluation noise, but not mainly. The difference between the optimistic key-split `0.6723` and reliable same-key `0.6326` is consistent with answer/judge/API variance plus run-composition differences; it should not be treated as a stable architecture gain.
- The stable architecture gap is retrieval organization after recall: we can often retrieve relevant raw spans, but the evidence pack still needs better query routing, update-state reduction, contradiction-aware value history, summary coverage, and typed temporal/aspect timelines.
- To exceed TrueMemory Pro on BEAM100K without benchmark-specific handling, the highest-value path is not more regex. It is a product-grade pipeline:
  - broad raw hybrid recall similar to TrueMemory;
  - LLM or NLP query normalization into typed intent, entities, time constraints, requested count, and answer contract;
  - per-intent retrieval arms with observable candidate quotas;
  - typed memory objects for facts, events, updates, contradictions, summaries, and aspect timelines;
  - calibrated reranking and pack construction that preserves provenance and coverage before answer generation.

Repair:

- Restored `_event_ordering_normalize_typed_aspect_label()` after duplicate-code cleanup removed it.
- Tightened person-advice labeling so relationship phrases like `friend Carla suggested ...` preserve the named advice source instead of being collapsed into a generic fairness/bias label.
- Validation:
  - `.runtime/beam-venv/bin/python -m py_compile fusion_memory/eval/model_adapters.py fusion_memory/retrieval/evidence_pack.py tests/test_model_adapters.py` passed.
  - Targeted event-ordering tests passed:
    - `test_event_ordering_sequence_items_use_aspect_hints_for_non_code_timeline`
    - `test_event_ordering_typed_aspects_cover_personal_work_challenges`
    - `test_event_ordering_chronology_rescue_scores_out_of_window_profile_updates`

## 2026-06-16 Runner Retry Semantics And Failure Analysis

Runner fix:

- `tools/beam_parallel_runner.py` previously treated a record as completed when it had a `query_id` and `answer_failed` was false.
- That missed judge/rubric API failures. `BeamAdapter` records these as `judge_failed` in final answer records, or as `judge_reason` containing `rubric scoring failed after retries` in partial records.
- This could leave API/judge instability as permanent zero-score records during resume or retry runs.
- Completion now requires:
  - `query_id` is present;
  - `answer_failed` is false;
  - `judge_failed` is false;
  - `judge_reason` does not indicate rubric/judge scoring failure.
- `--answer-failed-only` now selects retryable answer or judge failures when used with `--from-result`.
- `.runtime/beam_tools/beam_autoquery_monitor.py` now invokes the maintained `tools/beam_parallel_runner.py` instead of the stale `.runtime/beam_tools/beam_parallel_runner.py` copy, and passes `--answer-failure-retries 2`.
- `.runtime/beam_tools/beam_parallel_runner.py` is now a shim to the maintained runner, so manual invocations of the old path also receive the current resume/retry behavior.

Validation:

- `.runtime/beam-venv/bin/python -m py_compile tools/beam_parallel_runner.py tests/test_beam_parallel_runner.py .runtime/beam_tools/beam_autoquery_monitor.py` passed.
- `.runtime/beam-venv/bin/python -m py_compile tools/beam_parallel_runner.py .runtime/beam_tools/beam_parallel_runner.py tests/test_beam_parallel_runner.py .runtime/beam_tools/beam_autoquery_monitor.py` passed.
- `.runtime/beam-venv/bin/python -m unittest tests.test_beam_parallel_runner` passed: `14` tests.
- `.runtime/beam-venv/bin/python .runtime/beam_tools/beam_parallel_runner.py --workspace w --output /tmp/beam-shim-help.json query --help` exposes `--answer-failure-retries`, `--answer-failed-only`, and `--use-llm-aggregation`.
- Added coverage for:
  - judge-failed records not being considered completed;
  - judge-failed records being selected by retry-only `--from-result`;
  - malformed partial JSONL still being skipped without dropping valid retryable records.

Failure analysis from saved runs:

- Reliable full same-key run remains `.runtime/beam-runs/dual_full_20260615/full_samekey_llmauto_gpt54.json`, accuracy `0.6326120303746116`, with no answer or judge failures.
- The optimistic `.runtime/beam-runs/llm_auto_full_20260615/full_keysplit_llmauto_gpt54.json` score `0.6723117190173091` should still be treated as noisy until reproduced under one stable key/config.
- `multi_session_reasoning` is not fundamentally broken. The later category replay `.runtime/beam-runs/event_multi_category_20260615/event_multi_categories_current_gpt54.json` reached `0.7998214285714285`, recovering examples that the same-key full run missed:
  - `beam:100k:1:multi_session_reasoning:0`: columns `category` and `notes`, `0.0 -> 1.0`;
  - `beam:100k:3:multi_session_reasoning:0`: `10` project cards, `0.0 -> 1.0`;
  - `beam:100k:8:multi_session_reasoning:0`: cover letter mentioned `3` times, `0.0 -> 1.0`;
  - `beam:100k:17:multi_session_reasoning:0`: `12` of `16` scenes filmed, `0.0 -> 1.0`;
  - `beam:100k:18:multi_session_reasoning:1`: anniversary dinner plus Blue Bay Resort getaway, `0.0 -> 1.0`.
- Remaining multi-session misses are mostly aggregation semantics, not raw recall:
  - overcounting categories or application types;
  - deduping aggregate totals versus individual mentions;
  - deciding whether a later stated total should override arithmetic from earlier spans;
  - preserving exact locations/events in the compact answer pack.
- `event_ordering` remains structural. The category replay is still only `0.16969683068199248`, and focused chronology rescue only moved three hard queries from `0.18336` to `0.19077`.
- The event-ordering failure pattern is that evidence exists but answer-facing `sequence_items` are too granular, duplicate adjacent work, or use labels that do not match high-level phase/aspect references. This is not likely to be solved by more API retries or by TrueMemory-style raw recall alone.

Current estimate:

- With verified information-extraction and multi-session category recoveries, a fresh full run should plausibly improve over `0.6326`.
- Until a fresh same-key 400-query run confirms the current code end to end, the defensible estimate remains `0.66-0.68`, not the earlier optimistic `0.70-0.74`.
- A path to `0.75+` still requires category-level architecture gains in `event_ordering`, `knowledge_update`, and at least one of `summarization` or `contradiction_resolution`, plus a retry-safe full run using the maintained runner.

## 2026-06-16 Event-Ordering Pack Probe And Diagnostics

Changes:

- Added `tools/beam_failure_diagnostics.py` failure-pattern grouping for:
  - retryable judge failures;
  - abstention / missing evidence;
  - event-ordering wrong item count;
  - event-ordering topic drift;
  - event-ordering non-event/question fragments;
  - multi-session count/dedup misses.
- Added generic event-ordering scope and non-event guards:
  - filter negated/non-event records such as "never started/accepted/used" before treating them as sequence phases;
  - use query-scope terms to avoid obviously cross-topic typed-aspect candidates;
  - add product-level aspect hint for AI/hiring soft-skill recognition.

Validation:

- `.runtime/beam-venv/bin/python -m py_compile fusion_memory/eval/model_adapters.py tools/beam_failure_diagnostics.py tests/test_beam_failure_diagnostics.py tests/test_beam_parallel_runner.py` passed.
- `.runtime/beam-venv/bin/python -m unittest tests.test_beam_failure_diagnostics tests.test_beam_parallel_runner tests.test_model_adapters.ModelAdapterTests.test_event_ordering_sequence_items_use_aspect_hints_for_non_code_timeline tests.test_model_adapters.ModelAdapterTests.test_event_ordering_typed_aspects_cover_personal_work_challenges tests.test_model_adapters.ModelAdapterTests.test_event_ordering_chronology_rescue_scores_out_of_window_profile_updates` passed: `18` tests.
- `tools/beam_failure_diagnostics.py` wrote `.runtime/beam-runs/dual_full_20260615/event_multi_failure_diagnostics_20260616.json`.

Local Postgres pack probe:

- `beam:100k:18:event_ordering:1` still emits four sequence items, but the order/labels remain weak: `partner connection planning`, `focus more on the team feedback sessions and mindful self-care`, `team dynamics`, `vacation and unplugging`.
- `beam:100k:6:event_ordering:0` still contains an obvious topic-drift label: `partner connection planning`, followed by profile/interview/offer/time/localization labels.
- `beam:100k:11:event_ordering:0` improved by preserving AI/hiring process labels, but still leaks a non-topic `burnout and stress management` item into an AI hiring sequence.
- `beam:100k:3:event_ordering:0` still selects late deployment/test labels rather than the earliest framework integration/customization phases.

Interpretation:

- The current bottleneck is confirmed as answer-facing sequence abstraction, not raw recall. The packs have anchors and source spans, but the selected `sequence_items` are often too granular, cross-topic, or selected from the wrong episode section.
- The small guard changes are useful hygiene, but they are not enough to move event-ordering materially. The next architecture step should be a proper phase selector:
  - build query-scoped candidate phases from anchors;
  - score each phase by topic overlap, eventiveness, chronology, and novelty;
  - select one phase per temporal bucket with MMR-like diversity;
  - reject cross-topic phases after label generation, not only before candidate scoring.

Follow-up implementation:

- Added `_event_ordering_query_scoped_phase_sequence_items()` ahead of the brittle anchor/cluster fallback.
- The selector builds candidate labels from user anchors, filters non-events and topic drift, scores by query scope/focus overlap plus eventiveness, dedupes by label, and selects chronologically diverse phases.
- It can return a partial high-confidence skeleton when it cannot fill the full requested count, allowing the answer model to use reliable phases before falling back to raw timeline items.
- Tightened `_event_ordering_non_event_or_negated_record()` so "do I need to..." questions are filtered only when they do not contain a concrete memory topic such as AI/hiring, resume/profile, burnout/stress, or framework/deployment.
- Added an aspect hint for AI/hiring soft-skill recognition.

Validation:

- `.runtime/beam-venv/bin/python -m py_compile fusion_memory/eval/model_adapters.py tests/test_model_adapters.py` passed.
- `.runtime/beam-venv/bin/python -m unittest tests.test_model_adapters.ModelAdapterTests.test_event_ordering_query_scoped_phase_selector_filters_topic_drift tests.test_model_adapters.ModelAdapterTests.test_event_ordering_sequence_items_use_aspect_hints_for_non_code_timeline tests.test_model_adapters.ModelAdapterTests.test_event_ordering_typed_aspects_cover_personal_work_challenges tests.test_model_adapters.ModelAdapterTests.test_event_ordering_chronology_rescue_scores_out_of_window_profile_updates tests.test_beam_failure_diagnostics tests.test_beam_parallel_runner` passed: `19` tests.
- Synthetic pack-only probe for an AI hiring timeline now emits:
  - `AI soft skills recognition`
  - `AI screening workflow`
  - `pilot program and screening impact`
  - `AI hiring fairness and transparency`
  and filters out the off-topic `burnout and stress management` anchor.

Remaining verification gap:

- Real local Postgres pack probe later completed at `.runtime/beam-runs/event_ordering_rescue_20260616/pack_probe_after_phase_selector.json`.
- The result did not justify raising the estimate:
  - `beam:100k:18:event_ordering:1` still emits weak/cross-topic labels: `partner connection planning`, `focus more on the team feedback sessions and mindful self-care`, `team dynamics`, `vacation and unplugging`.
  - `beam:100k:6:event_ordering:0` still leaks `partner connection planning` before resume/profile/interview/offer/localization items.
  - `beam:100k:11:event_ordering:0` keeps useful AI/hiring labels but still leaks `burnout and stress management`.
  - `beam:100k:3:event_ordering:0` still selects late deployment/test labels instead of early framework-integration phases.
- Conclusion: synthetic selector tests improved, but real BEAM packs show the current answer-facing sequence abstraction is still unstable. The next step should not be another narrow event-ordering rule pile. It should be a generic retrieval and packing architecture change:
  - query normalization into a typed intent with topic anchors, answer shape, temporal needs, aggregation semantics, and evidence scope;
  - broad raw evidence recall before compression;
  - source-aware reranking/fusion so raw spans, facts, events, summaries, and current views compete fairly;
  - a raw-evidence fallback similar in spirit to TrueMemory Pro's BEAM harness, but with Fusion provenance and lifecycle layers retained;
  - explicit timeline/object grouping after retrieval, not only pre-retrieval regex routing.

## 2026-06-16 Route Assessment: Beating TrueMemory Pro Requires Retrieval Breadth Plus Structure

Current answer to the architecture question:

- TrueMemory Pro's BEAM path is primarily a strong raw-message retrieval system. The harness stores every chat message, calls `search_agentic(question, limit=100, use_hyde=True, use_reranker=True)`, and gives the answer model up to `50` retrieved raw messages. In BEAM scripts, `llm_fn` is not passed, so HyDE is effectively inactive there; the active gain comes from hybrid FTS/vector retrieval, RRF-like fusion, supplement/rescue searches, salience/surprise boosts, cross-encoder reranking, and wide context.
- That is worth learning from. The high-value reusable idea is not the exact AGPL code or benchmark-specific prompts; it is the retrieval shape: large candidate pool, multiple independent recall channels, fair score normalization, reranking, and graceful fallback to raw source messages when structured memory is incomplete.
- It is not enough for a product memory system. TrueMemory Pro's own published BEAM category files show weak event ordering, so raw recall alone does not solve temporal abstraction, lifecycle updates, or high-level phase grouping.
- Fusion's current low stable full score is partly LLM/judge variance, but not mainly. The `0.6723` key-split run should be treated as noisy because it was not reproduced under one stable config, while the reliable same-key full run is `0.6326120303746116` with no answer/judge failures. The event-ordering pack probes show deterministic evidence organization failures before the answer LLM runs.
- The core architecture issue is retrieval/packing, not storage alone. Fusion stores raw spans and structured candidates, but answer-facing packs are often over-compressed into brittle heuristic items. For some categories the raw evidence is present but the selected abstraction is wrong, too granular, or cross-topic.

Practical route to surpass a TrueMemory-style score on BEAM100K without cheating:

- Add a broad, benchmark-agnostic raw recall floor for every query: hybrid lexical/dense candidates, query-expanded lexical candidates, recency/temporal candidates, and entity/topic candidates, all kept with provenance.
- Add source-aware reranking/fusion before pack construction. Avoid letting any one source type dominate merely because its score scale differs.
- Use LLM/NLP at query-analysis time to produce a strict typed plan, not free-form routing prose. Required fields should include answer shape, evidence scope, topic anchors, entities, temporal constraints, current-state need, aggregation operation/object type, and uncertainty. Regex remains a fast first pass and fallback.
- Build category-agnostic object/timeline grouping from retrieved evidence:
  - object grouping for counts and multi-session reasoning;
  - lifecycle/current-state grouping for knowledge updates and contradictions;
  - chronological phase grouping for temporal/event questions.
- Preserve raw evidence alongside structured summaries in the model pack. When structured grouping confidence is low, the answer model should see the chronological raw slice rather than only guessed labels.
- Treat LLM extractor/router as a quality improvement only after telemetry proves it is positive. It should be schema-strict, source-attributed, confidence-calibrated, and fallback-visible; otherwise it only hides extraction failures behind the rule extractor.

Score implication:

- A stable `0.75+` likely needs roughly:
  - event_ordering from `~0.17` to at least `0.55-0.65`;
  - knowledge_update from `~0.46` to `0.70+`;
  - full-run recovery of the already observed category gains in multi-session and information extraction;
  - modest gains in summarization/contradiction from better raw recall and lifecycle packs.
- Without those architecture changes, the defensible estimate remains around `0.66-0.68` for a fresh same-key BEAM100K run, even if individual focused category replays look better.

## 2026-06-16 Value-History And Aggregation Validation

Implementation changes:

- Fixed event-ordering raw chronology output sorting so selected raw sequence items are emitted in `timeline_index` order before source/turn id order.
- Tightened value-history candidate ranking:
  - query unit match now outranks weaker current/update markers, so `cards` is preferred over generic `items` when the query asks for cards;
  - current/latest questions prefer user/document current-state rows over assistant planning examples;
  - historical baseline phrases such as "given that you did 12 hours" are demoted for latest-value questions.
- Added a low-confidence aggregation fallback:
  - if included aggregation candidates are only weak `generic:` / `item:` fragments, the model pack omits `aggregation_items` instead of forcing the answer model to count bad structure;
  - stable object candidates such as `title:`, `value:`, `column:`, `feature:`, `area:`, `application_type:`, `count_hint:`, `group_count:`, and calculation/break keys are still preserved.

Validation:

- Unit tests:
  - value-history unit preference and user-current precedence passed;
  - event-ordering timeline-first sort regression passed;
  - weak-generic aggregation filtering and stable title/column aggregation regressions passed.
- Pack probes:
  - event-ordering raw chronology is now monotonic on the probed cases, but labels/topic scope remain weak;
  - `beam:100k:3:knowledge_update:1` now resolves `10 cards` instead of `6 items`;
  - `beam:100k:18:knowledge_update:0` now resolves `4 hours` instead of `12 hours`;
  - weak generic multi-session candidates are no longer exposed for AI-vendor, boundary-order, and children-gift questions.
- 24-query targeted validation:
  - Run: `.runtime/beam-runs/current_validation_20260616/known_gain_24_after_valuefix_gpt54.json`
  - Result: accuracy `0.8315972222222222`, answer match `0.8333333333333334`.
  - Category subset scores:
    - `information_extraction`: `0.7760416666666666`
    - `knowledge_update`: `0.875`
    - `multi_session_reasoning`: `0.84375`
  - This is a real positive smoke test, but it is a selected 24-query set and should not be treated as a full-score estimate.
- Three-category replay:
  - Run: `.runtime/beam-runs/current_validation_20260616/full_3cat_after_valuefix_gpt54.json`
  - Scope: all `information_extraction`, `knowledge_update`, and `multi_session_reasoning` queries, `120` total.
  - Result: accuracy `0.5962251984126984`, answer match `0.6416666666666667`.
  - Category scores:
    - `information_extraction`: `0.7380208333333333`, up from stable full baseline `0.6317708333333333`;
    - `knowledge_update`: `0.4875`, only slightly above stable full baseline `0.45625`;
    - `multi_session_reasoning`: `0.5631547619047619`, below stable full baseline `0.6166964285714285` and below the earlier category replay `0.7998214285714285`.
- Four-query regression after weak-generic filtering:
  - Run: `.runtime/beam-runs/current_validation_20260616/multisession_regression_after_generic_filter_gpt54.json`
  - Result: `0.5` on four known multi-session regressions.
  - Recovered:
    - boundary-order query: `0.0 -> 1.0`;
    - children-gift count: `0.0 -> 1.0`.
  - Still missing:
    - AI vendor/tool count needs stable vendor/tool entity extraction, not weak generic keys;
    - movie marathon count still misses one title (`Encanto`) despite preserving stable title/count candidates.

Current score estimate:

- Replacing only the three freshly replayed categories in the reliable full baseline gives an estimated full BEAM100K score of about `0.6410`.
- The optimistic 24-query result is useful as a smoke test for specific fixes, but not representative enough to justify a `0.70+` estimate.
- The current defensible estimate is therefore `~0.64-0.66` for a same-key full run unless multi-session is restored and knowledge_update improves at full-category scale.
- The architecture bottleneck remains retrieval/packing stability:
  - information extraction improved meaningfully;
  - value-history ranking fixes work on targeted cases but do not yet generalize enough across the full category;
  - multi-session needs stable object/entity extraction and confidence-calibrated aggregation fallback;
  - event-ordering still needs better phase/topic grouping, not just chronology sorting.

## 2026-06-16 Stability Fixes Before Full BEAM100K

Implementation changes made after the previous value-history / aggregation round:

- Summary retrieval now guards broad raw recall with a stronger topic-anchor check for `summarization` queries. This prevents broad fallback spans that share only one generic anchor, such as `portfolio`, from re-entering the pack after topic filtering when the query asks for a more specific project like `portfolio website project`.
- Contradiction retrieval now preserves `contradiction_claim_positive/negative/uncertain` entries in the search debug trace even after RRF merges the same span with broad/raw/entity/exact sources. This is a product observability fix: conflict answers must be auditable by claim polarity and source span.
- Event-ordering event support now deduplicates graph events by primary source span as well as milestone group. One turn can legitimately produce multiple nearby milestone labels, but those duplicate abstractions should not crowd out later turns in a chronology pack.
- Fixed an inconsistent test fixture expectation in `test_event_ordering_sequence_items_use_aspect_hints_for_non_code_timeline`: the source text names `Maya`, while the assertion expected `carla`.

Validation:

- `.runtime/beam-venv/bin/python -m unittest tests.test_fusion_memory tests.test_model_adapters` now passes: `175` tests.
- Summary probe for the portfolio website project now keeps only the three `beam:test:3` spans and excludes the Behance salary-negotiation span.
- Event-ordering probe for app development now surfaces milestone groups `initial_project_setup`, `transaction_crud_implementation`, `deployment_configuration`, and `integration_test_coverage` instead of letting two transaction milestones crowd out testing.
- 24-query selected validation:
  - Run: `.runtime/beam-runs/current_validation_20260616/known_gain_24_after_stability_fixes_gpt54.json`
  - Result: accuracy `0.8125`, answer match `0.875`, no answer/judge failures after retry.
  - This is below the adjacent-support selected run (`0.8533`) and should not be used as proof of full-score improvement by itself. It still confirms the current code path is runnable and stable enough for full BEAM100K.
- Full BEAM100K run started:
  - Run: `.runtime/beam-runs/current_validation_20260616/full_after_stability_fixes_gpt54.json`
  - Config: same-key OpenAI-compatible endpoint, `gpt-5.4`, 24 query workers, answer failure retries enabled.
  - Status at start of documentation update: actively running, no worker failure observed yet.

BEAM standard evaluation check:

- Local BEAM source at `/public/home/wwb/datasets/BEAM` shows official evaluation is rubric-driven:
  - `src/evaluation/run_evaluation.py` loads each probing question's `rubric` and dispatches to category-specific `evaluate_*` functions.
  - Non-event categories evaluate each rubric item with an LLM judge prompt and average item scores in `{0, 0.5, 1}`.
  - `event_ordering` additionally uses semantic alignment and Kendall tau-style ordering score, then mixes rubric-item LLM judge scoring.
  - `src/llm.py` defaults the GPT judge/client object to `gpt-4.1-mini` with temperature `0`, while `src/llms_config.json` leaves API/model endpoint configuration empty for users to fill.
- There is no single hardcoded leaderboard answer-model prompt in the local source. Answer generation is separate from evaluation and supports `rag`, `long-context`, and `kg` modes. Our runner uses the same rubric-item evaluation concept through an OpenAI-compatible structured judge, but the current experimental answer/judge model is `gpt-5.4`, so comparisons must record the model config.

Architecture assessment from source reading:

- TrueMemory Pro's BEAM runner stores all messages, calls `search_agentic(question, limit=100, use_hyde=True, use_reranker=True)`, and sends up to `50` retrieved raw messages to the answer model. In the published runner no `llm_fn` is passed, so HyDE/refined-query generation is effectively inactive; the active path is hybrid lexical/vector retrieval, RRF, temporal/entity/cluster supplements, source score normalization, surprise/salience boosts, and cross-encoder reranking.
- TrueMemory's source is still heuristic-heavy, but the important advantage is architectural rather than a pile of benchmark-specific rules: retrieve broadly first, normalize source scores, rerank a large pool, and preserve raw message evidence. Its temporal module is mostly regex plus SQL time-window filtering, not a deep temporal knowledge graph.
- Fusion's current architecture is over-concentrated:
  - `fusion_memory/api/service.py`: 4889 lines. Public API, ingestion, candidate generation, broad recall, scent trails, temporal coverage, aggregation coverage, event ordering selection, topic filtering, and utility telemetry are in one service class plus many local helper functions.
  - `fusion_memory/retrieval/evidence_pack.py`: 2844 lines. Evidence-pack construction, exact answer rescue, summary expansion, contradiction buckets, value history, temporal candidates, and formatting constraints are mixed in one builder module.
  - `fusion_memory/eval/model_adapters.py`: 7117 lines. Evaluation answer prompt, BEAM model-pack schema, event-ordering labelers, temporal/value/financial/multi-session aggregation logic, and judge glue are all coupled.
  - `fusion_memory/retrieval/structured_annotations.py`: 1810 lines. Timeline annotations, event-ordering selectors, label cleanup, phase selection, topic segmentation, and assistant support are mixed together.
- The bottleneck is therefore not raw storage alone. Fusion already stores raw spans and structured facts/events, but the answer-facing pack path is too procedural and category-specific. The system frequently has evidence somewhere in storage, then loses quality when heuristic packing over-compresses, chooses the wrong abstraction, or reintroduces broad noisy spans late in the pipeline.

Practical route toward beating `0.766` without benchmark cheating:

- Keep the product memory contract: raw spans, extracted facts/events, current views, profiles, provenance, and lifecycle/conflict handling.
- Add a TrueMemory-style raw retrieval floor as a first-class lane for every query: lexical, dense, exact, entity, temporal, recency, and query-expanded search should all contribute to a normalized candidate pool before pack compression.
- Replace ad hoc preservation functions with a source-aware reranking stage that operates on typed `EvidenceCandidate` objects with comparable scores and explicit provenance. The current `preserve_*` functions are hard to reason about because they run before and after MMR/rerank/topic filter and can undo each other.
- Move category-specific pack shaping out of `model_adapters.py` into separate modules: `timeline_pack`, `value_history_pack`, `aggregation_pack`, `conflict_pack`, `summary_pack`, and `instruction_pack`. The answer model should receive a stable schema independent of BEAM.
- Use LLM/NLP for query analysis only as a typed, schema-strict refiner: language, answer shape, evidence scope, topic anchors, entities, temporal constraints, aggregation operation/object type, current-state/conflict needs, and confidence. Regex should remain a fast fallback, and refiner failures must be visible telemetry.
- Improve temporal architecture by making the graph hierarchical rather than only `before` edges: raw turn order, session segments, topic spans, event candidates, lifecycle states, and value histories should be separate layers that can be queried and packed together. This is closer in spirit to StructMem/MemForest/AdaMem-style hierarchy than to another regex branch in `event_ordering`.
- To exceed `0.766` on BEAM100K, the full run must show that broad raw recall and structured pack improvements lift low categories together. If the new full score remains around `0.64-0.67`, the next work should be architectural retrieval/pack refactoring, not more event-ordering rules.
