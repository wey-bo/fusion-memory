from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fusion_memory import MemoryService, Scope
from fusion_memory.api.service import _event_ordering_milestone_score
from fusion_memory.core.models import Candidate
from fusion_memory.ingestion.extractors import classify_milestone, classify_milestones, extract_milestone_mentions
from fusion_memory.retrieval.rrf import reciprocal_rank_fusion


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class FusionMemoryTests(unittest.TestCase):
    def test_scope_required_for_add(self) -> None:
        memory = MemoryService()
        with self.assertRaises(ValueError):
            memory.add("Remember that I prefer Qdrant.", Scope())

    def test_scope_isolation(self) -> None:
        memory = MemoryService()
        scope_a = Scope(workspace_id="w", user_id="u1", agent_id="a")
        scope_b = Scope(workspace_id="w", user_id="u2", agent_id="a")
        memory.add("I prefer Qdrant for Atlas retrieval.", scope_a, ts("2026-06-01T10:00:00+00:00"))
        result = memory.search("Qdrant Atlas", scope_b)
        self.assertEqual(result.candidates, [])

    def test_preference_update_writes_source_fact_relation_and_current_view(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        first = memory.add("For Atlas, I prefer Chroma because it is simple.", scope, ts("2026-06-01T10:00:00+00:00"))
        second = memory.add(
            "We switched Atlas to Qdrant. Remember that Qdrant is now preferred.",
            scope,
            ts("2026-06-08T10:00:00+00:00"),
        )

        self.assertTrue(first.accepted_fact_ids)
        self.assertTrue(second.accepted_fact_ids)
        relations = memory.store.list_fact_relations(relation_type="supersedes")
        self.assertTrue(relations)
        latest_fact = memory.store.get_fact(second.accepted_fact_ids[0])
        self.assertIsNotNone(latest_fact)
        self.assertTrue(latest_fact.source_span_ids)

        pack = memory.answer_context("What do I currently prefer for Atlas?", scope)
        self.assertTrue(pack.current_views)
        self.assertTrue("Qdrant" in str(pack.current_views) or "Qdrant" in str(pack.facts))
        self.assertTrue(pack.source_spans)

    def test_speaker_attribution_rejects_assistant_suggestion_as_user_preference(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        result = memory.add(
            [
                {"role": "assistant", "content": "You may want to use PostgreSQL."},
                {"role": "user", "content": "Good idea, but don't remember that as my preference yet."},
            ],
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )
        facts = memory.store.list_facts(scope)
        user_preference_facts = [fact for fact in facts if fact.category == "preference"]
        self.assertEqual(user_preference_facts, [])
        trace = memory.debug_trace(result.trace_id)
        self.assertIsNotNone(trace)
        self.assertTrue("explicit_negative_memory_instruction" in str(trace) or "speaker_attribution" in str(trace))

    def test_temporal_ordering_builds_events_and_raw_evidence(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("I tested BM25 yesterday.", scope, ts("2026-06-03T12:00:00+00:00"))
        memory.add("After the BM25 test, I added dense retrieval.", scope, ts("2026-06-05T12:00:00+00:00"))

        events = memory.store.list_events(scope)
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(any(event.time_start and event.time_start.date().isoformat() == "2026-06-02" for event in events))

        pack = memory.answer_context("Which happened before dense retrieval?", scope)
        self.assertGreaterEqual(pack.coverage["source_span_quota_required"], 4)
        self.assertTrue(pack.source_spans)
        self.assertTrue(any("BM25" in span["content"] for span in pack.source_spans))

    def test_event_ordering_pack_includes_timeline_indices_for_milestones(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("I brought up Core functionality for the budget tracker.", scope, ts("2026-06-01T10:00:00+00:00"))
        memory.add("I mentioned Transaction error handling for the budget tracker.", scope, ts("2026-06-02T10:00:00+00:00"))
        memory.add("I discussed Security and deployment for the budget tracker.", scope, ts("2026-06-03T10:00:00+00:00"))

        events = memory.store.list_events(scope)
        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(any(event.event_type == "milestone" for event in events))

        pack = memory.answer_context(
            "Can you list the order in which I brought up different aspects of developing my personal budget tracker throughout our conversations, in order?",
            scope,
            budget={"limit": 8},
        )

        self.assertEqual(pack.coverage["query_type"], "event_ordering")
        self.assertEqual(pack.coverage["timeline_span_count"], len(pack.source_spans))
        self.assertTrue(pack.source_spans)
        self.assertEqual([span["timeline_index"] for span in pack.source_spans], list(range(1, len(pack.source_spans) + 1)))
        timestamps = [span["timestamp"] for span in pack.source_spans]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_event_ordering_timeline_recall_boosts_user_milestones(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("Here is a general assistant recap about the budget tracker.", scope, ts("2026-06-01T09:00:00+00:00"))
        memory.add("I'm setting up the initial project schema and local server for the budget tracker.", scope, ts("2026-06-01T10:00:00+00:00"))
        memory.add("I implemented transaction CRUD response handling and validation errors.", scope, ts("2026-06-02T10:00:00+00:00"))
        memory.add("I configured Render deployment with Gunicorn workers and port 10000.", scope, ts("2026-06-03T10:00:00+00:00"))
        memory.add("I expanded integration tests for endpoint coverage.", scope, ts("2026-06-04T10:00:00+00:00"))
        memory.add("I reviewed security auth changes before deployment.", scope, ts("2026-06-05T10:00:00+00:00"))

        pack = memory.answer_context(
            "Can you walk me through the order in which I brought up different aspects of my app development and deployment across our conversations?",
            scope,
            budget={"limit": 8},
        )

        contents = " ".join(span["content"] for span in pack.source_spans).lower()
        self.assertIn("initial project schema", contents)
        self.assertIn("transaction crud", contents)
        self.assertIn("render deployment", contents)
        self.assertIn("integration tests", contents)
        self.assertIn("security auth", contents)
        self.assertTrue(any("event_ordering_timeline" in item["source"] for item in pack.debug_trace))

    def test_event_ordering_pack_preserves_event_timeline_graph_candidates(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("I'm trying to set up the database schema and local server for the initial project.", scope, ts("2026-06-01T10:00:00+00:00"))
        memory.add("I'm trying to implement transaction CRUD with response handling and validation errors.", scope, ts("2026-06-02T10:00:00+00:00"))
        memory.add("I'm configuring deployment on Render with Gunicorn workers and port 10000.", scope, ts("2026-06-03T10:00:00+00:00"))
        memory.add("I expanded integration tests for endpoint coverage and the test suite.", scope, ts("2026-06-04T10:00:00+00:00"))

        pack = memory.answer_context(
            "Can you walk me through the order in which I brought up different aspects of my app development and deployment across our conversations?",
            scope,
            budget={"limit": 8},
        )

        groups = [event.get("milestone_group") for event in pack.events]
        self.assertIn("initial_project_setup", groups)
        self.assertIn("transaction_crud_implementation", groups)
        self.assertIn("deployment_configuration", groups)
        self.assertIn("integration_test_coverage", groups)
        self.assertEqual([event["timeline_index"] for event in pack.events], list(range(1, len(pack.events) + 1)))
        self.assertTrue(any(item["type"] == "event" and "event_timeline_graph" in item["source"] for item in pack.debug_trace))

    def test_event_ordering_short_timeline_covers_broad_project_phases(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("I planned core functionality for login, transaction tracking, and analytics.", scope, ts("2026-06-01T10:00:00+00:00"))
        memory.add("I designed a narrow database schema detail for the initial project.", scope, ts("2026-06-01T11:00:00+00:00"))
        memory.add("I implemented transaction CRUD and response error handling.", scope, ts("2026-06-02T10:00:00+00:00"))
        memory.add("I wrote authentication tests for password hashing.", scope, ts("2026-06-03T10:00:00+00:00"))
        memory.add("I finalized deployment with Render, Gunicorn, and security hardening.", scope, ts("2026-06-04T10:00:00+00:00"))

        pack = memory.answer_context(
            "Can you list the order in which I brought up different aspects of developing my app, in order? Mention ONLY and ONLY three items.",
            scope,
            budget={"limit": 8},
        )

        groups = [event.get("milestone_group") for event in pack.events]
        self.assertIn("core_functionality", groups)
        self.assertTrue(any(group in groups for group in ["transaction_error_handling", "transaction_crud_implementation"]))
        self.assertTrue(any(group in groups for group in ["security_and_deployment", "deployment_configuration"]))
        self.assertLess(groups.index("core_functionality"), groups.index(next(group for group in groups if group.startswith("transaction"))))

    def test_exact_signal_preserved_through_rrf_merge(self) -> None:
        raw = Candidate(
            id="s1",
            type="span",
            text="Dashboard API response time is 250ms.",
            source="l0_raw_hybrid",
            scores={"bm25_score": 0.2, "score": 0.2},
            source_span_ids=["s1"],
            metadata={"speaker": "user"},
        )
        exact = Candidate(
            id="s1",
            type="span",
            text="Dashboard API response time is 250ms.",
            source="exact_filter",
            scores={"bm25_score": 1.0, "exact_signal": 1.0, "score": 1.0},
            source_span_ids=["s1"],
            metadata={"exact_signal": 1.0},
        )

        fused = reciprocal_rank_fusion([[raw], [exact]])

        self.assertEqual(len(fused), 1)
        self.assertIn("exact_filter", fused[0].source)
        self.assertEqual(fused[0].scores["exact_signal"], 1.0)
        self.assertEqual(fused[0].metadata["exact_signal"], 1.0)

    def test_current_value_exact_signal_surfaces_latest_numeric_evidence(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("The dashboard API response time was initially 800ms.", scope, ts("2026-06-01T10:00:00+00:00"))
        memory.add("The dashboard API response time was reduced to 300ms after query optimization.", scope, ts("2026-06-02T10:00:00+00:00"))
        memory.add("The dashboard API response time has recently improved to 250ms after caching tweaks.", scope, ts("2026-06-03T10:00:00+00:00"))
        memory.add("I changed the dashboard color palette and sidebar layout.", scope, ts("2026-06-04T10:00:00+00:00"))

        pack = memory.answer_context("What is the average response time of the dashboard API?", scope, budget={"limit": 4})

        contents = [span["content"] for span in pack.source_spans]
        self.assertTrue(any("250ms" in content for content in contents[:2]))
        self.assertTrue(any("800ms" in content for content in contents))

    def test_temporal_lookup_pack_labels_date_roles(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add(
            "Phase 3 transaction management features are completed by January 15, 2024.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )
        memory.add(
            "The final deployment deadline is March 15, 2024.",
            scope,
            ts("2026-06-02T10:00:00+00:00"),
        )

        pack = memory.answer_context(
            "How many weeks do I have between finishing the transaction management features and the final deployment deadline?",
            scope,
            budget={"limit": 4},
        )

        self.assertEqual(pack.coverage["query_type"], "temporal_lookup")
        mentions = [mention for span in pack.source_spans for mention in span.get("temporal_mentions", [])]
        roles = {mention["role"] for mention in mentions}
        self.assertIn("feature_finish_date", roles)
        self.assertIn("deployment_deadline", roles)
        self.assertIn("2024-01-15", {mention["normalized_date"] for mention in mentions})
        self.assertIn("2024-03-15", {mention["normalized_date"] for mention in mentions})
        self.assertIn("feature_finish_date", pack.coverage["temporal_target_roles"])
        self.assertIn("deployment_deadline", pack.coverage["temporal_target_roles"])
        self.assertNotIn("temporal_role_candidates", pack.coverage)

    def test_temporal_lookup_uses_topic_scope_before_date_roles(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add(
            "The screenplay launch preparation has a final deployment deadline of March 20, 2024.",
            scope,
            ts("2026-06-01T09:00:00+00:00"),
            {"source_uri": "beam:test:20:batch1:msg1"},
        )
        memory.add(
            "The transaction management features are completed by January 15, 2024.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
            {"source_uri": "beam:test:1:batch1:msg1"},
        )
        memory.add(
            "The final deployment deadline for the budget tracker is March 15, 2024.",
            scope,
            ts("2026-06-02T10:00:00+00:00"),
            {"source_uri": "beam:test:1:batch1:msg2"},
        )

        pack = memory.answer_context(
            "How many weeks do I have between finishing the transaction management features and the final deployment deadline?",
            scope,
            budget={"limit": 6},
        )

        content = " ".join(span["content"] for span in pack.source_spans)
        self.assertIn("transaction management features", content)
        self.assertIn("March 15, 2024", content)
        self.assertNotIn("screenplay launch preparation", content)

    def test_temporal_lookup_infers_year_for_month_day_ranges(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add(
            "Phase 3 runs Dec 16, 2023 - Jan 15, with transaction management features complete at the end.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )

        pack = memory.answer_context(
            "When did the transaction management features finish?",
            scope,
            budget={"limit": 4},
        )

        mentions = [mention for span in pack.source_spans for mention in span.get("temporal_mentions", [])]
        self.assertIn("2024-01-15", {mention["normalized_date"] for mention in mentions})
        self.assertNotIn("2026-01-15", {mention["normalized_date"] for mention in mentions})
        self.assertIn("feature_finish_date", {mention["role"] for mention in mentions})

    def test_temporal_lookup_summary_preserves_late_date_contexts(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add(
            "Project overview. " * 40
            + "Milestones: Nov 16 - Dec 15, 2023 for authentication. "
            + "Dec 16, 2023 - Jan 15, 2024: Develop transaction management features. "
            + "Feb 16 - Mar 15, 2024: Final adjustments, testing, and deployment.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )

        pack = memory.answer_context(
            "How many weeks do I have between finishing the transaction management features and the final deployment deadline?",
            scope,
            budget={"limit": 4},
        )

        content = " ".join(span["content"] for span in pack.source_spans)
        mentions = [mention for span in pack.source_spans for mention in span.get("temporal_mentions", [])]
        by_date = {mention["normalized_date"]: mention for mention in mentions}
        self.assertIn("Jan 15, 2024", content)
        self.assertEqual(by_date["2024-01-15"]["role"], "feature_finish_date")
        self.assertEqual(by_date["2024-03-15"]["role"], "deployment_deadline")

    def test_temporal_lookup_distinguishes_mvp_and_deployment_deadlines(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add(
            "The MVP scope deadline is April 15, 2024.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )
        memory.add(
            "The final deployment deadline is March 15, 2024.",
            scope,
            ts("2026-06-02T10:00:00+00:00"),
        )

        pack = memory.answer_context(
            "How much time is there until the final deployment deadline?",
            scope,
            budget={"limit": 4},
        )

        by_date = {
            mention["normalized_date"]: mention["role"]
            for span in pack.source_spans
            for mention in span.get("temporal_mentions", [])
        }
        self.assertEqual(by_date["2024-04-15"], "mvp_deadline")
        self.assertEqual(by_date["2024-03-15"], "deployment_deadline")

    def test_temporal_lookup_uses_specific_feature_anchors(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add(
            "Sprint 1 ends March 29, 2024. Sprint 2 targets analytics features by April 19, 2024.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )
        memory.add(
            "Dec 16, 2023 - Jan 15, 2024: Develop transaction management features.",
            scope,
            ts("2026-06-02T10:00:00+00:00"),
        )

        pack = memory.answer_context(
            "How many days were there between the end of my first sprint and the deadline for completing the analytics features in sprint 2?",
            scope,
            budget={"limit": 4},
        )

        self.assertIn("sprint_end_date", pack.coverage["temporal_target_roles"])
        mentions = [mention for span in pack.source_spans for mention in span.get("temporal_mentions", [])]
        by_date = {mention["normalized_date"]: mention["role"] for mention in mentions}
        self.assertEqual(by_date["2024-03-29"], "sprint_end_date")
        self.assertEqual(by_date["2024-04-19"], "feature_finish_date")
        self.assertNotEqual(by_date["2024-01-15"], "feature_finish_date")

    def test_event_ordering_topic_scope_prevents_generic_milestone_bleed(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add(
            "I'm implementing transaction CRUD response handling for my budget tracker.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
            {"source_uri": "beam:test:1:batch1:msg1"},
        )
        memory.add(
            "I'm configuring Render deployment with Gunicorn workers for the budget tracker.",
            scope,
            ts("2026-06-02T10:00:00+00:00"),
            {"source_uri": "beam:test:1:batch1:msg2"},
        )
        memory.add(
            "I started managing stress by setting no-work Sundays and reducing burnout.",
            scope,
            ts("2026-06-03T10:00:00+00:00"),
            {"source_uri": "beam:test:16:batch1:msg1"},
        )
        memory.add(
            "I handled financial concerns by tracking rent, groceries, and emergency savings.",
            scope,
            ts("2026-06-04T10:00:00+00:00"),
            {"source_uri": "beam:test:16:batch1:msg2"},
        )

        pack = memory.answer_context(
            "Can you walk me through the order in which I brought up different ways I’ve been managing stress and financial concerns throughout our chats, in order?",
            scope,
            budget={"limit": 8},
        )

        content = " ".join(span["content"] for span in pack.source_spans).lower()
        self.assertIn("managing stress", content)
        self.assertIn("financial concerns", content)
        self.assertNotIn("transaction crud", content)
        self.assertNotIn("gunicorn", content)

    def test_summarization_expands_same_topic_group_timeline(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add(
            "My portfolio website started with HTML5 sections for About, Skills, Projects, and Contact.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
            {"source_uri": "beam:test:3:batch1:msg1"},
        )
        memory.add(
            "I added Bootstrap 5.3.0 cards and modal popups to the portfolio website project gallery.",
            scope,
            ts("2026-06-02T10:00:00+00:00"),
            {"source_uri": "beam:test:3:batch1:msg2"},
        )
        memory.add(
            "I debugged CSS box model and layout issues with Chrome DevTools for the portfolio website.",
            scope,
            ts("2026-06-03T10:00:00+00:00"),
            {"source_uri": "beam:test:3:batch1:msg3"},
        )
        memory.add(
            "I updated my Behance digital portfolio for a salary negotiation.",
            scope,
            ts("2026-06-04T10:00:00+00:00"),
            {"source_uri": "beam:test:30:batch1:msg1"},
        )

        pack = memory.answer_context(
            "Can you give me a comprehensive summary of how my portfolio website project has developed, including the key features and challenges I've worked through so far?",
            scope,
            budget={"limit": 6},
        )

        content = " ".join(span["content"] for span in pack.source_spans)
        self.assertIn("HTML5 sections", content)
        self.assertIn("Bootstrap 5.3.0", content)
        self.assertIn("Chrome DevTools", content)
        self.assertNotIn("salary negotiation", content)

    def test_contradiction_pack_groups_surface_claim_polarity(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add("I have never used Bootstrap components in my portfolio website.", scope, ts("2026-06-01T10:00:00+00:00"), {"source_uri": "beam:test:3:batch1:msg1"})
        memory.add("I used Bootstrap cards and modal components in the portfolio website gallery.", scope, ts("2026-06-02T10:00:00+00:00"), {"source_uri": "beam:test:3:batch1:msg2"})

        pack = memory.answer_context("Have I used Bootstrap components in my project before?", scope, budget={"limit": 6})

        self.assertEqual(pack.coverage["query_type"], "contradiction_resolution")
        polarities = {span.get("claim_polarity") for span in pack.source_spans}
        self.assertIn("negative", polarities)
        self.assertIn("positive", polarities)
        self.assertTrue(pack.conflicts)
        self.assertGreaterEqual(pack.coverage["claim_polarity_counts"]["positive"], 1)
        self.assertGreaterEqual(pack.coverage["claim_polarity_counts"]["negative"], 1)
        self.assertTrue(any(str(item["source"]).startswith("contradiction_claim_") for item in pack.debug_trace))

    def test_knowledge_update_pack_marks_history_and_value_mentions(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add("The dashboard API response time was initially 800ms.", scope, ts("2026-06-01T10:00:00+00:00"), {"source_uri": "beam:test:1:batch1:msg1"})
        memory.add("The dashboard API response time improved to 300ms after optimization.", scope, ts("2026-06-02T10:00:00+00:00"), {"source_uri": "beam:test:1:batch1:msg2"})
        memory.add("The dashboard API response time is now 250ms after caching tweaks.", scope, ts("2026-06-03T10:00:00+00:00"), {"source_uri": "beam:test:1:batch1:msg3"})

        pack = memory.answer_context("What is the average response time of the dashboard API?", scope, budget={"limit": 6})

        self.assertEqual(pack.coverage["query_type"], "knowledge_update")
        self.assertEqual(sorted(span["history_index"] for span in pack.source_spans), list(range(1, len(pack.source_spans) + 1)))
        self.assertTrue(any(span.get("recency_rank") == 1 and "250ms" in span["content"] for span in pack.source_spans))
        self.assertTrue(any(span.get("value_mentions") for span in pack.source_spans))

    def test_multi_session_pack_marks_history_order_for_aggregation(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add("I want to add a category column to the transactions table.", scope, ts("2026-06-01T10:00:00+00:00"), {"source_uri": "beam:test:1:batch1:msg1"})
        memory.add("I also want to add a notes column to the transactions table.", scope, ts("2026-06-02T10:00:00+00:00"), {"source_uri": "beam:test:1:batch2:msg1"})

        pack = memory.answer_context("How many new columns did I want to add to the transactions table across my requests?", scope, budget={"limit": 6})

        self.assertEqual(pack.coverage["query_type"], "multi_session_reasoning")
        content = " ".join(span["content"] for span in pack.source_spans)
        self.assertIn("category column", content)
        self.assertIn("notes column", content)
        self.assertEqual([span["history_index"] for span in pack.source_spans], list(range(1, len(pack.source_spans) + 1)))

    def test_instruction_pack_exposes_format_requirements(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add("The project uses Flask==2.3.1 and Flask-Login==0.6.2.", scope, ts("2026-06-01T10:00:00+00:00"))

        pack = memory.answer_context("Which libraries are used in this project? Include version details.", scope, budget={"limit": 4})

        self.assertIn("include_exact_versions_if_supported", pack.coverage["format_requirements"])

    def test_rule_extractor_preserves_fact_polarity_values_and_event_topic_terms(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        result = memory.add(
            "Remember that I have never used Bootstrap components, but I tested the portfolio website on March 15, 2024 with Flask==2.3.1.",
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )

        facts = [memory.store.get_fact(fact_id) for fact_id in result.accepted_fact_ids]
        facts = [fact for fact in facts if fact]
        self.assertTrue(any(fact.polarity == "negative" for fact in facts))
        self.assertTrue(any(fact.metadata.get("value_mentions") for fact in facts))
        events = memory.store.list_events(scope)
        self.assertTrue(any("portfolio" in event.participants or "bootstrap" in event.participants for event in events))

    def test_event_ordering_milestone_score_prefers_specific_milestones(self) -> None:
        generic = _event_ordering_milestone_score("I decided on blueprints for auth, transactions, and analytics.")
        specific = _event_ordering_milestone_score("I implemented transaction CRUD response handling and validation errors.")

        self.assertGreater(specific, generic)
        self.assertGreater(_event_ordering_milestone_score("I configured Render deployment with Gunicorn workers and port 10000."), 0.0)
        self.assertGreater(_event_ordering_milestone_score("I expanded integration tests for endpoint coverage."), 0.0)
        self.assertEqual(_event_ordering_milestone_score("I decided deployment can wait until later."), 0.0)

    def test_event_ordering_milestone_classifier_avoids_generic_deployment_noise(self) -> None:
        self.assertIsNone(classify_milestone("I decided Chart.js and dashboard API dependencies can be reviewed before deployment."))
        self.assertNotIn(
            "deployment_configuration",
            classify_milestones("I'm getting TemplateNotFound when I call render_template in my Flask route."),
        )
        self.assertNotIn(
            "deployment_configuration",
            classify_milestones("I'm rendering Chart.js expense pie charts in the dashboard."),
        )
        self.assertEqual(
            classify_milestone("I configured Render deployment with Gunicorn workers and port 10000."),
            "deployment_configuration",
        )
        self.assertEqual(
            classify_milestone("I expanded the deployment test suite with additional security-related tests."),
            "deployment_and_test_improvements",
        )
        self.assertEqual(
            classify_milestone("I want to improve the transaction creation response and error handling."),
            "transaction_error_handling",
        )
        groups = classify_milestones(
            "I'm having issues with deployment on Render.com and Gunicorn port 10000. "
            "I've also completed integration tests covering auth, transaction CRUD, and analytics endpoints with a 95% pass rate."
        )
        self.assertIn("deployment_configuration", groups)
        self.assertIn("integration_test_coverage", groups)

        mentions = extract_milestone_mentions(
            "I'm trying to design a database schema for my personal budget tracker. "
            "The transactions table should include type, amount, date, category, and notes."
        )
        self.assertIn("initial_project_setup", [group for group, _ in mentions])
        self.assertNotIn("transaction_error_handling", [group for group, _ in mentions])

        split_mentions = extract_milestone_mentions(
            "I'm having issues with deployment on Render.com and Gunicorn port 10000. "
            "I've also completed integration tests covering auth, transaction CRUD, and analytics endpoints with a 95% pass rate."
        )
        split_groups = [group for group, _ in split_mentions]
        self.assertIn("deployment_configuration", split_groups)
        self.assertIn("integration_test_coverage", split_groups)
        self.assertNotIn(
            "transaction_crud_implementation",
            classify_milestones("I've completed integration tests covering user auth, transaction CRUD, and analytics endpoints."),
        )
        core_groups = classify_milestones(
            "Can you help me implement the core functionality of my budget tracker, including user authentication, expense tracking, and data visualization?"
        )
        self.assertIn("core_functionality", core_groups)
        self.assertNotIn("security_auth", core_groups)
        setup_groups = classify_milestones(
            "I'm trying to set up my Flask app, but I keep getting TemplateNotFound and OperationalError: no such table."
        )
        self.assertIn("setup_debugging", setup_groups)
        self.assertNotIn("transaction_error_handling", setup_groups)
        long_mentions = extract_milestone_mentions(
            "I'm trying to optimize the dashboard API response time after caching tweaks, "
            "and I want the code to stay compatible with my existing database schema, "
            "and later I need examples for writing unit tests and integration tests for the application with pytest."
        )
        integration_snippets = [snippet for group, snippet in long_mentions if group == "integration_test_coverage"]
        self.assertTrue(integration_snippets)
        self.assertTrue(all("integration tests" in snippet.lower() or "unit tests" in snippet.lower() for snippet in integration_snippets))

    def test_event_ordering_pack_prefers_milestone_events_when_available(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add("I'm trying to set up the database schema and local server for the app.", scope, ts("2026-06-01T10:00:00+00:00"))
        memory.add("I'm implementing transaction CRUD with response handling and validation errors.", scope, ts("2026-06-02T10:00:00+00:00"))
        memory.add("I'm configuring Render deployment with Gunicorn workers and port 10000.", scope, ts("2026-06-03T10:00:00+00:00"))
        memory.add("I'm expanding integration tests for endpoint coverage.", scope, ts("2026-06-04T10:00:00+00:00"))

        pack = memory.answer_context(
            "Can you walk me through the order in which I brought up different aspects of my app development and deployment across our conversations?",
            scope,
            budget={"limit": 10},
        )

        groups = [event.get("milestone_group") for event in pack.events]
        self.assertIn("initial_project_setup", groups)
        self.assertIn("transaction_crud_implementation", groups)
        self.assertIn("deployment_configuration", groups)
        self.assertIn("integration_test_coverage", groups)
        self.assertEqual([event["timeline_index"] for event in pack.events], list(range(1, len(pack.events) + 1)))

    def test_abstention_sets_policy_when_evidence_insufficient(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add("Remember that my database is PostgreSQL.", scope, ts("2026-06-01T10:00:00+00:00"))
        pack = memory.answer_context("What is my Kubernetes cluster name?", scope)
        self.assertEqual(pack.answer_policy, "abstain_if_not_supported")
        self.assertIs(pack.coverage["coverage_insufficient"], True)

    def test_entity_profile_requires_repeated_support(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add("Always give me concise technical answers.", scope, ts("2026-06-01T10:00:00+00:00"))
        self.assertEqual(memory.store.list_entity_profiles(scope), [])
        memory.add("Please keep responses concise but include implementation tradeoffs.", scope, ts("2026-06-02T10:00:00+00:00"))
        profiles = memory.store.list_entity_profiles(scope)
        self.assertTrue(profiles)
        self.assertTrue(profiles[0].source_span_ids)
        self.assertGreaterEqual(profiles[0].support_count, 2)

    def test_document_input_is_chunked_with_overlap(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        content = " ".join(f"Token{i}" for i in range(35))
        result = memory.add(
            {"role": "document", "content": content, "source_uri": "doc://atlas", "chunk_size_tokens": 10, "chunk_overlap_tokens": 2},
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )
        spans = [memory.store.get_span(span_id) for span_id in result.span_ids]
        chunks = [span for span in spans if span and span.span_type == "document_chunk"]
        self.assertGreaterEqual(len(chunks), 4)
        self.assertTrue(all(chunk.source_uri == "doc://atlas" for chunk in chunks))
        self.assertIn("Token8", chunks[0].content)
        self.assertIn("Token8", chunks[1].content)

    def test_session_window_is_written_but_not_extracted_as_fact(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        result = memory.add(
            [
                {"role": "user", "content": "I prefer Qdrant for Atlas."},
                {"role": "assistant", "content": "I will remember that."},
                {"role": "user", "content": "Always answer with concise tradeoffs."},
            ],
            scope,
            ts("2026-06-01T10:00:00+00:00"),
            {"min_window_spans": 3, "window_size": 3},
        )
        spans = [memory.store.get_span(span_id) for span_id in result.span_ids]
        windows = [span for span in spans if span and span.span_type == "window"]
        self.assertEqual(len(windows), 1)
        facts = memory.store.list_facts(scope)
        self.assertFalse(any(fact.metadata.get("candidate_local_id") and "assistant: I will remember" in fact.text for fact in facts))

    def test_session_summary_is_refreshable_idempotent_and_retrievable(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        memory.add(
            [
                {"role": "user", "content": "Atlas uses Qdrant for retrieval."},
                {"role": "assistant", "content": "I noted the Atlas backend."},
                {"role": "user", "content": "Reports use PostgreSQL."},
                {"role": "assistant", "content": "I will keep reports on PostgreSQL."},
                {"role": "user", "content": "Reranking should use a cross encoder."},
                {"role": "assistant", "content": "I will include reranking in the retrieval plan."},
            ],
            scope,
            ts("2026-06-01T10:00:00+00:00"),
            {"min_window_spans": 3, "window_size": 3},
        )
        fact_count = len(memory.store.list_facts(scope))

        summary = memory.refresh_session_summary(scope)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.span_type, "summary")
        self.assertIn("Atlas", summary.content)
        self.assertIn("Qdrant", summary.content)
        self.assertEqual(summary.metadata["source_span_count"], 6)
        self.assertEqual(len(memory.store.list_facts(scope)), fact_count)

        duplicate = memory.refresh_session_summary(scope)
        self.assertEqual(duplicate.span_id, summary.span_id)
        summaries = memory.get_session_summaries(scope)
        self.assertEqual([item.span_id for item in summaries], [summary.span_id])

        result = memory.search("Which retrieval backend did Atlas use?", scope, options={"enabled_sources": ["raw"], "limit": 6})
        self.assertTrue(any(candidate.id == summary.span_id for candidate in result.candidates))

    def test_session_summary_background_task_is_enqueued_processed_and_idempotent(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")

        memory.add(
            [
                {"role": "user", "content": "Atlas uses Qdrant for retrieval."},
                {"role": "assistant", "content": "I noted the Atlas backend."},
                {"role": "user", "content": "Reports use PostgreSQL."},
                {"role": "assistant", "content": "I will keep reports on PostgreSQL."},
                {"role": "user", "content": "Reranking should use a cross encoder."},
                {"role": "assistant", "content": "I will include reranking in the retrieval plan."},
            ],
            scope,
            ts("2026-06-01T10:00:00+00:00"),
            {"min_window_spans": 3, "window_size": 3},
        )

        pending = memory.list_background_tasks(scope, status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["task_type"], "refresh_session_summary")
        self.assertEqual(pending[0]["payload"]["source_span_count"], 6)
        self.assertEqual(pending[0]["attempts"], 0)

        processed = memory.process_background_tasks(scope, limit=5)
        self.assertEqual(processed["processed_count"], 1)
        self.assertEqual(processed["status_counts"], {"succeeded": 1})
        self.assertEqual(processed["tasks"][0]["attempts"], 1)

        summaries = memory.get_session_summaries(scope)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(processed["tasks"][0]["payload"]["result"]["summary_span_id"], summaries[0].span_id)

        duplicate_run = memory.process_background_tasks(scope, limit=5)
        self.assertEqual(duplicate_run["processed_count"], 0)
        self.assertEqual(len(memory.get_session_summaries(scope)), 1)

    def test_session_summary_background_task_is_not_enqueued_below_threshold(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")

        memory.add(
            [
                {"role": "user", "content": "Atlas uses Qdrant for retrieval."},
                {"role": "assistant", "content": "I noted the Atlas backend."},
            ],
            scope,
            ts("2026-06-01T10:00:00+00:00"),
        )

        self.assertEqual(memory.list_background_tasks(scope), [])

    def test_entities_are_persisted_and_used_as_retrieval_source(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a")
        memory.add("I prefer Qdrant for Atlas retrieval.", scope, ts("2026-06-01T10:00:00+00:00"))
        entities = memory.store.list_entities(scope)
        names = {entity.name for entity in entities}
        self.assertIn("Qdrant", names)
        self.assertIn("Atlas", names)
        result = memory.search("Atlas", scope)
        self.assertTrue(any("entity_registry" in candidate.source for candidate in result.candidates))


if __name__ == "__main__":
    unittest.main()
