from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fusion_memory.core.chronology import (
    ChronologyEventEdge,
    ChronologyEventNode,
    ChronologyPhase,
    ChronologyTopic,
)
from fusion_memory.core.models import Scope
from fusion_memory.storage.sqlite_store import SQLiteMemoryStore


class ChronologyStorageTests(unittest.TestCase):
    def test_sqlite_chronology_graph_round_trips_topic_phase_node_and_edge(self) -> None:
        store = SQLiteMemoryStore()
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        topic = ChronologyTopic(
            topic_id="topic_budget",
            scope=scope,
            canonical_label="budget tracker",
            aliases=["budget app"],
            language="en",
            taxonomy_tags=["software"],
            source_span_ids=["s1"],
            confidence=0.9,
            created_at=now,
        )
        phase = ChronologyPhase(
            phase_id="phase_setup",
            topic_id=topic.topic_id,
            phase_type="setup",
            order_hint=1,
            source_span_ids=["s1"],
            confidence=0.8,
            created_at=now,
        )
        first = ChronologyEventNode(
            node_id="node_1",
            scope=scope,
            actor="user",
            action="set up",
            object="schema",
            topic_id=topic.topic_id,
            phase_id=phase.phase_id,
            timestamp=now,
            source_span_id="s1",
            source_turn_id="t1",
            text="I first set up the schema.",
            language="en",
            confidence=0.88,
            explicit_order_marker="first",
            created_at=now,
        )
        second = ChronologyEventNode(
            node_id="node_2",
            scope=scope,
            actor="user",
            action="implemented",
            object="transaction CRUD",
            topic_id=topic.topic_id,
            phase_id=phase.phase_id,
            timestamp=now,
            source_span_id="s2",
            source_turn_id="t2",
            text="Then I implemented transaction CRUD.",
            language="en",
            confidence=0.86,
            explicit_order_marker="then",
            created_at=now,
        )
        edge = ChronologyEventEdge(
            edge_id="edge_1",
            from_node_id=first.node_id,
            to_node_id=second.node_id,
            edge_type="before",
            evidence_type="explicit_marker",
            source_span_ids=["s1", "s2"],
            confidence=0.92,
            created_at=now,
        )

        store.upsert_chronology_topic(topic)
        store.upsert_chronology_phase(phase)
        store.upsert_chronology_event_node(first)
        store.upsert_chronology_event_node(second)
        inserted = store.insert_chronology_event_edge(edge)

        self.assertTrue(inserted)
        self.assertEqual(store.list_chronology_topics(scope, include_session=True)[0].canonical_label, "budget tracker")
        self.assertEqual(store.list_chronology_phases([topic.topic_id])[0].phase_type, "setup")
        nodes = store.list_chronology_event_nodes(scope, include_session=True, topic_ids=[topic.topic_id])
        self.assertEqual([node.node_id for node in nodes], ["node_1", "node_2"])
        edges = store.list_chronology_event_edges(["node_1", "node_2"])
        self.assertEqual(edges[0].edge_type, "before")
        self.assertEqual(edges[0].evidence_type, "explicit_marker")


if __name__ == "__main__":
    unittest.main()
