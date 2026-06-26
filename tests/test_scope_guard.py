from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fusion_memory import MemoryService, Scope


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class ScopeGuardTests(unittest.TestCase):
    def test_read_requires_business_scope(self) -> None:
        memory = MemoryService()
        with self.assertRaises(ValueError):
            memory.search("anything", Scope())
        with self.assertRaises(ValueError):
            memory.answer_context("anything", Scope())
        with self.assertRaises(ValueError):
            memory.history(Scope())

    def test_search_defaults_to_current_session_plus_long_term_recall(self) -> None:
        memory = MemoryService()
        scope_s1 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s1")
        scope_s2 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s2")
        memory.add("I prefer Qdrant for Atlas retrieval.", scope_s1, ts("2026-06-01T10:00:00+00:00"))
        memory.add("I prefer Qdrant for Atlas retrieval in this session.", scope_s2, ts("2026-06-02T10:00:00+00:00"))

        result = memory.search("Qdrant Atlas", scope_s2)
        self.assertTrue(any(candidate.metadata.get("session_id") == "s2" for candidate in result.candidates))
        self.assertTrue(any("this session" not in candidate.text and "Qdrant" in candidate.text for candidate in result.candidates))
        self.assertIn("this session", result.candidates[0].text)
        trace = memory.debug_trace(result.trace_id)
        self.assertTrue(trace["allow_cross_session"])
        self.assertFalse(trace["include_session"])
        self.assertEqual(trace["session_filter_mode"], "current_session_plus_long_term")

        current_only = memory.search("Qdrant Atlas", scope_s2, options={"allow_cross_session": False})
        self.assertFalse(any("this session" not in candidate.text and "Qdrant" in candidate.text for candidate in current_only.candidates))
        current_only_trace = memory.debug_trace(current_only.trace_id)
        self.assertFalse(current_only_trace["allow_cross_session"])
        self.assertTrue(current_only_trace["include_session"])
        self.assertEqual(current_only_trace["session_filter_mode"], "current_session_only")

    def test_history_and_timeline_default_to_session_isolation(self) -> None:
        memory = MemoryService()
        scope_s1 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s1")
        scope_s2 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s2")
        memory.add("I tested BM25 yesterday.", scope_s1, ts("2026-06-03T12:00:00+00:00"))
        memory.add("I added dense retrieval today.", scope_s2, ts("2026-06-05T12:00:00+00:00"))

        isolated_history = memory.history(scope_s2)
        self.assertFalse(any("BM25" in event["description"] for event in isolated_history["events"]))

        cross_history = memory.history(scope_s2, allow_cross_session=True)
        self.assertTrue(any("BM25" in event["description"] for event in cross_history["events"]))

        isolated_timeline = memory.timeline(None, scope_s2)
        self.assertFalse(any("BM25" in event.description for event in isolated_timeline))

        cross_timeline = memory.timeline(None, scope_s2, allow_cross_session=True)
        self.assertTrue(any("BM25" in event.description for event in cross_timeline))

    def test_object_id_reads_are_scope_and_session_aware_when_scope_is_provided(self) -> None:
        memory = MemoryService()
        scope_s1 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s1")
        scope_s2 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s2")
        other_user = Scope(workspace_id="w", user_id="other", agent_id="a", session_id="s1")
        result = memory.add("I prefer Qdrant for Atlas retrieval.", scope_s1, ts("2026-06-01T10:00:00+00:00"))

        span_id = result.span_ids[0]
        fact_id = result.accepted_fact_ids[0]
        trace_id = result.trace_id

        self.assertIsNotNone(memory.get(span_id, "span"))
        self.assertIsNotNone(memory.get(span_id, "span", scope_s1))
        self.assertIsNone(memory.get(span_id, "span", scope_s2))
        self.assertIsNotNone(memory.get(span_id, "span", scope_s2, allow_cross_session=True))
        self.assertIsNone(memory.get(span_id, "span", other_user, allow_cross_session=True))

        self.assertIsNotNone(memory.get(fact_id, "fact", scope_s1))
        self.assertIsNone(memory.get(fact_id, "fact", scope_s2))
        self.assertIsNotNone(memory.get(fact_id, "fact", scope_s2, allow_cross_session=True))
        self.assertIsNone(memory.get(fact_id, "fact", other_user, allow_cross_session=True))

        self.assertIsNotNone(memory.debug_trace(trace_id, scope_s1))
        self.assertIsNone(memory.debug_trace(trace_id, scope_s2))
        self.assertIsNotNone(memory.debug_trace(trace_id, scope_s2, allow_cross_session=True))
        self.assertIsNone(memory.debug_trace(trace_id, other_user, allow_cross_session=True))

    def test_event_get_and_compare_are_scope_and_session_aware_when_scope_is_provided(self) -> None:
        memory = MemoryService()
        scope_s1 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s1")
        scope_s2 = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s2")
        other_user = Scope(workspace_id="w", user_id="other", agent_id="a", session_id="s1")
        memory.add("I tested BM25 yesterday.", scope_s1, ts("2026-06-03T12:00:00+00:00"))
        memory.add("I added dense retrieval today.", scope_s1, ts("2026-06-05T12:00:00+00:00"))
        events = memory.timeline(None, scope_s1)
        self.assertGreaterEqual(len(events), 2)
        left, right = events[0], events[1]

        self.assertIsNotNone(memory.get(left.event_id, "event", scope_s1))
        self.assertIsNone(memory.get(left.event_id, "event", scope_s2))
        self.assertIsNotNone(memory.get(left.event_id, "event", scope_s2, allow_cross_session=True))

        visible = memory.compare_events(left.event_id, right.event_id, scope_s1)
        self.assertEqual(visible["relation"], "before")

        isolated = memory.compare_events(left.event_id, right.event_id, scope_s2)
        self.assertEqual(isolated["relation"], "unknown")
        self.assertEqual(isolated["basis"], "missing_event")

        cross_session = memory.compare_events(left.event_id, right.event_id, scope_s2, allow_cross_session=True)
        self.assertEqual(cross_session["relation"], "before")

        other = memory.compare_events(left.event_id, right.event_id, other_user, allow_cross_session=True)
        self.assertEqual(other["relation"], "unknown")
        self.assertEqual(other["basis"], "missing_event")


if __name__ == "__main__":
    unittest.main()
