from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fusion_memory import MemoryService, Scope
from fusion_memory.core.llm import StaticLLMClient
from fusion_memory.core.text import extract_entities
from fusion_memory.ingestion.temporal_normalizer import TemporalNormalizer
from fusion_memory.retrieval.query_planner import QueryPlanner


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class TemporalNormalizerTests(unittest.TestCase):
    def test_relative_days_weeks_months_and_weekdays(self) -> None:
        normalizer = TemporalNormalizer()
        session_time = ts("2026-06-09T15:30:00+00:00")

        tomorrow = normalizer.normalize("deploy tomorrow", session_time)
        self.assertEqual(tomorrow.time_start.date().isoformat(), "2026-06-10")
        self.assertEqual(tomorrow.source, "relative_resolved")

        next_month = normalizer.normalize("start rollout next month", session_time)
        self.assertEqual(next_month.time_start.date().isoformat(), "2026-07-01")
        self.assertEqual(next_month.time_end.date().isoformat(), "2026-08-01")
        self.assertEqual(next_month.granularity, "month")

        this_friday = normalizer.normalize("fixed reports this Friday", session_time)
        self.assertEqual(this_friday.time_start.date().isoformat(), "2026-06-12")

    def test_explicit_dates_and_unknown_fallback(self) -> None:
        normalizer = TemporalNormalizer()
        session_time = ts("2026-06-09T15:30:00+00:00")

        iso_date = normalizer.normalize("deployed on 2026-06-15", session_time)
        self.assertEqual(iso_date.time_start.date().isoformat(), "2026-06-15")
        self.assertEqual(iso_date.source, "explicit")

        month_name = normalizer.normalize("deployed on June 16, 2026", session_time)
        self.assertEqual(month_name.time_start.date().isoformat(), "2026-06-16")

        invalid = normalizer.normalize("deployed on June 31, 2026", session_time)
        self.assertIsNone(invalid.time_start)
        self.assertEqual(invalid.source, "unknown")

        unknown = normalizer.normalize("deployed Atlas", session_time)
        self.assertIsNone(unknown.time_start)
        self.assertEqual(unknown.granularity, "unknown")
        self.assertEqual(unknown.source, "unknown")

    def test_query_planner_extracts_new_time_constraints(self) -> None:
        plan = QueryPlanner().plan("What did we deploy next month and this Friday?")
        self.assertEqual(plan.query_type, "temporal_lookup")
        self.assertEqual(plan.intent["answer_shape"], "short_answer")
        self.assertTrue(plan.intent["temporal"]["requires_time"])
        constraints = {item["text"] for item in plan.time_constraints}
        self.assertIn("next month", constraints)
        self.assertIn("this friday", constraints)

        duration_plan = QueryPlanner().plan(
            "How many weeks do I have between finishing the feature work and the final deployment deadline?"
        )
        self.assertEqual(duration_plan.query_type, "temporal_lookup")
        self.assertEqual(duration_plan.intent["answer_shape"], "duration")
        self.assertTrue(duration_plan.intent["temporal"]["requires_duration"])
        self.assertEqual(duration_plan.intent["temporal"]["endpoint_roles"], ["start", "end", "deadline"])
        self.assertEqual(duration_plan.intent["temporal"]["order_direction"], "unknown")
        self.assertEqual(duration_plan.intent["aggregation"]["operation"], "none")
        self.assertFalse(duration_plan.intent["needs_current_state"])

    def test_query_planner_recognizes_order_in_which_queries(self) -> None:
        plan = QueryPlanner().plan(
            "Can you list the order in which I brought up different aspects of developing my personal budget tracker throughout our conversations, in order?"
        )
        self.assertEqual(plan.query_type, "event_ordering")
        self.assertEqual(plan.speaker_focus, "user")
        self.assertEqual(plan.intent["answer_shape"], "ordered_list")
        self.assertEqual(plan.intent["speaker_scope"], "user")
        self.assertTrue(plan.intent["temporal"]["requires_order"])
        self.assertIn("budget", plan.retrieval_hints)
        self.assertIn("tracker", plan.retrieval_hints)

    def test_query_planner_does_not_treat_procedural_first_then_as_memory_ordering(self) -> None:
        plan = QueryPlanner().plan(
            "If I draw a card from a deck and then draw another without putting the first back, how do I figure out the chance of both events happening?"
        )

        self.assertNotEqual(plan.query_type, "event_ordering")
        self.assertNotEqual(plan.intent["answer_shape"], "ordered_list")
        self.assertFalse(plan.intent["temporal"]["requires_order"])

    def test_query_planner_does_not_treat_social_first_time_as_temporal_lookup(self) -> None:
        plan = QueryPlanner().plan("What are some common expectations people have when meeting someone for the first time?")

        self.assertEqual(plan.query_type, "factual_exact")
        self.assertFalse(plan.intent["temporal"]["requires_time"])

    def test_query_planner_keeps_count_mentions_as_multi_session(self) -> None:
        plan = QueryPlanner().plan(
            "How many different features or concerns did I mention wanting to handle across my weather app conversations?"
        )
        self.assertEqual(plan.query_type, "multi_session_reasoning")

    def test_query_planner_routes_question_and_message_aggregations_to_multi_session(self) -> None:
        planner = QueryPlanner()
        probability = planner.plan(
            "In my questions about tossing coins and rolling dice, how many different probability calculations did I try to confirm?"
        )
        shoes = planner.plan("How many different shoe sizes have I mentioned across my messages?")
        reminders = planner.plan(
            "How many different types of reminders or plans have I mentioned using to manage my tasks and family events?"
        )

        self.assertEqual(probability.query_type, "multi_session_reasoning")
        self.assertEqual(shoes.query_type, "multi_session_reasoning")
        self.assertEqual(reminders.query_type, "multi_session_reasoning")
        self.assertEqual(probability.intent["aggregation"]["operation"], "count_distinct")
        self.assertEqual(reminders.intent["aggregation"]["operation"], "count_distinct")

    def test_query_planner_exposes_multilingual_query_intent_contract(self) -> None:
        planner = QueryPlanner()
        ordered = planner.plan("请按顺序列出我在所有对话中提到的不同安全功能，只列三个。")
        counted = planner.plan("我在所有会话里一共提到过几个用户角色和安全功能？")

        self.assertEqual(ordered.query_type, "event_ordering")
        self.assertEqual(ordered.speaker_focus, "user")
        self.assertEqual(ordered.intent["language"], "zh")
        self.assertEqual(ordered.intent["answer_shape"], "ordered_list")
        self.assertTrue(ordered.intent["temporal"]["requires_order"])
        self.assertIn("security_feature", ordered.intent["object_types"])
        self.assertEqual(ordered.intent["evidence_scope"], "multi_session")

        self.assertEqual(counted.query_type, "multi_session_reasoning")
        self.assertEqual(counted.intent["language"], "zh")
        self.assertEqual(counted.intent["answer_shape"], "count")
        self.assertEqual(counted.intent["aggregation"]["operation"], "count")
        self.assertIn("role", counted.intent["object_types"])
        self.assertIn("security_feature", counted.intent["object_types"])

    def test_query_planner_can_refine_multilingual_intent_with_strict_llm_output(self) -> None:
        client = StaticLLMClient(
            {
                "intent": {
                    "language": "zh",
                    "answer_shape": "unordered_list",
                    "evidence_scope": "multi_session",
                    "speaker_scope": "user",
                    "target_terms": ["权限控制", "登录保护"],
                    "object_types": ["security_feature"],
                    "temporal": {
                        "requires_time": False,
                        "requires_order": False,
                        "requires_duration": False,
                        "order_direction": "unknown",
                        "endpoint_roles": [],
                        "time_expressions": [],
                    },
                    "aggregation": {
                        "operation": "count_distinct",
                        "distinct": True,
                        "target_terms": ["security_feature"],
                        "unit_terms": [],
                    },
                    "needs_current_state": False,
                    "needs_conflict_check": False,
                    "confidence": 0.88,
                    "route_reasons": ["llm_multilingual_normalization", "multi_session_scope"],
                }
            }
        )
        planner = QueryPlanner(intent_refiner=client)

        plan = planner.plan("我之前提过哪些权限控制和登录保护能力？")

        self.assertEqual(plan.query_type, "multi_session_reasoning")
        self.assertEqual(plan.intent["evidence_scope"], "multi_session")
        self.assertEqual(plan.intent["answer_shape"], "unordered_list")
        self.assertIn("security_feature", plan.intent["object_types"])
        self.assertIn("llm_refined", plan.intent["route_reasons"])
        self.assertIsNotNone(planner.last_intent_telemetry)
        self.assertFalse(planner.last_intent_telemetry["fallback"])
        self.assertEqual(client.calls[0]["prompt"].splitlines()[0], "query-intent-refiner-v0")

    def test_query_planner_normalizes_common_llm_intent_aliases(self) -> None:
        client = StaticLLMClient(
            {
                "intent": {
                    "language": "zh",
                    "answer_shape": "list",
                    "evidence_scope": "all_relevant",
                    "speaker_scope": "user",
                    "target_terms": ["权限控制", "登录保护"],
                    "object_types": ["security_feature"],
                    "temporal": {
                        "requires_time": True,
                        "requires_order": False,
                        "requires_duration": False,
                        "order_direction": "none",
                        "endpoint_roles": [],
                        "time_expressions": ["之前"],
                    },
                    "aggregation": {
                        "operation": "list",
                        "distinct": True,
                        "target_terms": ["security_feature"],
                        "unit_terms": ["能力"],
                    },
                    "needs_current_state": False,
                    "needs_conflict_check": False,
                    "confidence": 0.86,
                    "route_reasons": ["list_request", "historical_reference"],
                }
            }
        )
        planner = QueryPlanner(intent_refiner=client)

        plan = planner.plan("我之前提过哪些权限控制和登录保护能力？")

        self.assertEqual(plan.query_type, "multi_session_reasoning")
        self.assertEqual(plan.intent["answer_shape"], "unordered_list")
        self.assertEqual(plan.intent["evidence_scope"], "multi_session")
        self.assertEqual(plan.intent["aggregation"]["operation"], "count_distinct")
        self.assertEqual(plan.intent["temporal"]["order_direction"], "unknown")
        self.assertFalse(planner.last_intent_telemetry["fallback"])

    def test_query_planner_falls_back_when_llm_intent_is_invalid(self) -> None:
        client = StaticLLMClient(
            {
                "intent": {
                    "language": "zh",
                    "answer_shape": "freeform",
                    "evidence_scope": "multi_session",
                    "speaker_scope": "user",
                    "target_terms": ["角色"],
                    "object_types": ["security_feature"],
                    "temporal": {
                        "requires_time": False,
                        "requires_order": False,
                        "requires_duration": False,
                        "order_direction": "unknown",
                        "endpoint_roles": [],
                        "time_expressions": [],
                    },
                    "aggregation": {
                        "operation": "count",
                        "distinct": False,
                        "target_terms": ["role"],
                        "unit_terms": [],
                    },
                    "needs_current_state": False,
                    "needs_conflict_check": False,
                    "confidence": 0.95,
                    "route_reasons": ["invalid_shape"],
                }
            }
        )
        planner = QueryPlanner(intent_refiner=client)

        plan = planner.plan("我在所有会话里一共提到过几个用户角色和安全功能？")

        self.assertEqual(plan.query_type, "multi_session_reasoning")
        self.assertEqual(plan.intent["answer_shape"], "count")
        self.assertNotIn("llm_refined", plan.intent["route_reasons"])
        self.assertIsNotNone(planner.last_intent_telemetry)
        self.assertTrue(planner.last_intent_telemetry["fallback"])
        self.assertEqual(planner.last_intent_telemetry["reason"], "invalid_or_low_confidence_output")

    def test_answer_context_reuses_refined_plan_and_exposes_intent_telemetry(self) -> None:
        client = StaticLLMClient(
            {
                "intent": {
                    "language": "zh",
                    "answer_shape": "unordered_list",
                    "evidence_scope": "multi_session",
                    "speaker_scope": "user",
                    "target_terms": ["权限控制", "登录保护"],
                    "object_types": ["security_feature"],
                    "temporal": {
                        "requires_time": False,
                        "requires_order": False,
                        "requires_duration": False,
                        "order_direction": "unknown",
                        "endpoint_roles": [],
                        "time_expressions": [],
                    },
                    "aggregation": {
                        "operation": "count_distinct",
                        "distinct": True,
                        "target_terms": ["security_feature"],
                        "unit_terms": [],
                    },
                    "needs_current_state": False,
                    "needs_conflict_check": False,
                    "confidence": 0.88,
                    "route_reasons": ["llm_multilingual_normalization"],
                }
            }
        )
        memory = MemoryService(query_intent_refiner=client)
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")

        pack = memory.answer_context("我之前提过哪些权限控制和登录保护能力？", scope)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(pack.coverage["query_type"], "multi_session_reasoning")
        self.assertFalse(pack.coverage["query_intent_telemetry"]["fallback"])

    def test_query_planner_routes_budget_and_target_value_questions_as_current_value(self) -> None:
        planner = QueryPlanner()
        budget = planner.plan("What is my monthly budget for books and subscriptions?")
        word_count = planner.plan("What is my weekly word count target for my writing goals?")
        deadline = planner.plan("By what date am I aiming to complete all my onboarding modules?")
        snack = planner.plan("What is my snack budget for ordering themed treats for the movie marathon?")

        self.assertEqual(budget.query_type, "knowledge_update")
        self.assertEqual(word_count.query_type, "knowledge_update")
        self.assertEqual(deadline.query_type, "knowledge_update")
        self.assertEqual(snack.query_type, "knowledge_update")

    def test_query_planner_keeps_did_i_say_counts_as_historical_exact(self) -> None:
        planner = QueryPlanner()
        historical = planner.plan("How many series did I say were on my reading list, and what was the total page count?")
        current = planner.plan("How many series are currently on my reading list?")

        self.assertEqual(historical.query_type, "factual_exact")
        self.assertFalse(historical.needs_current_state)
        self.assertEqual(current.query_type, "knowledge_update")
        self.assertTrue(current.needs_current_state)

    def test_query_planner_routes_evolved_and_considering_prompts_to_multi_session(self) -> None:
        planner = QueryPlanner()
        evolved = planner.plan(
            "How have my essay performance goals and feedback evolved from my initial grade concerns to aiming for publication, and what key improvements must I prioritize to meet both my grading and publication targets?"
        )
        considering = planner.plan(
            "Considering my current streaming subscriptions, snack budget for a family movie weekend, and past rental savings, how can I optimize my total monthly entertainment spending while maximizing simultaneous streaming and exclusive content access?"
        )
        timeline = planner.plan(
            "Given my timeline and actions from starting the prior art search to filing the provisional patent, how well did I align my search thoroughness, patent features, and budget to maximize my chances for a successful non-provisional filing?"
        )

        self.assertEqual(evolved.query_type, "multi_session_reasoning")
        self.assertEqual(considering.query_type, "multi_session_reasoning")
        self.assertEqual(timeline.query_type, "multi_session_reasoning")

    def test_query_planner_treats_cross_factor_effect_questions_as_multi_session(self) -> None:
        plan = QueryPlanner().plan(
            "How will increasing our grocery budget while taking on the freelance contract affect my ability to support Ashlee's medical bills and still meet my savings goals?"
        )
        self.assertEqual(plan.query_type, "multi_session_reasoning")

    def test_extract_entities_filters_question_boilerplate(self) -> None:
        entities = extract_entities(
            "Can you list the order in which I brought up Flask, SQLite, and Bootstrap? Mention ONLY three items."
        )
        self.assertIn("Flask", entities)
        self.assertIn("SQLite", entities)
        self.assertIn("Bootstrap", entities)
        self.assertNotIn("Can", entities)
        self.assertNotIn("Mention", entities)
        self.assertNotIn("ONLY", entities)

    def test_service_events_use_extended_temporal_rules(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("I fixed reports this Friday.", scope, ts("2026-06-09T15:30:00+00:00"))
        memory.add("I deployed Atlas on June 16, 2026.", scope, ts("2026-06-09T15:30:00+00:00"))
        memory.add("I started rollout next month.", scope, ts("2026-06-09T15:30:00+00:00"))
        memory.add("I deployed Atlas.", scope, ts("2026-06-09T15:30:00+00:00"))

        events = memory.store.list_events(scope)
        friday = next(event for event in events if "I fixed reports this Friday." in event.description)
        june_16 = next(event for event in events if "I deployed Atlas on June 16, 2026." in event.description)
        rollout = next(event for event in events if "I started rollout next month." in event.description)
        atlas = next(event for event in events if event.description.endswith("I deployed Atlas."))
        self.assertEqual(friday.time_start.date().isoformat(), "2026-06-12")
        self.assertEqual(june_16.time_start.date().isoformat(), "2026-06-16")
        self.assertEqual(rollout.time_start.date().isoformat(), "2026-07-01")
        self.assertIsNone(atlas.time_start)
        self.assertEqual(atlas.time_source, "unknown")

    def test_explicit_after_statement_writes_event_edge(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("I tested BM25 yesterday.", scope, ts("2026-06-03T12:00:00+00:00"))
        memory.add("After the BM25 test, I added dense retrieval.", scope, ts("2026-06-05T12:00:00+00:00"))

        events = memory.store.list_events(scope)
        bm25 = next(event for event in events if "BM25" in event.description and "After" not in event.description)
        dense = next(event for event in events if "dense retrieval" in event.description)
        comparison = memory.compare_events(bm25.event_id, dense.event_id)
        self.assertEqual(comparison["relation"], "before")
        self.assertEqual(comparison["basis"], "event_edge")
        self.assertGreaterEqual(comparison["confidence"], 0.82)


if __name__ == "__main__":
    unittest.main()
