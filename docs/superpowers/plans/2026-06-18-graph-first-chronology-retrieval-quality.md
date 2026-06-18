# Graph-First Chronology Retrieval Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent write-time chronology graph, use it as the first event-ordering selector with legacy fallback/shadow evaluation, and add retrieval preservation telemetry for high-signal evidence.

**Architecture:** Add first-class graph models and storage repositories, then build graph nodes/edges during `MemoryService.add()`. Event-ordering queries read the persisted graph before legacy `event_ordering_*` paths. Shared candidate preservation metadata makes current-value, Chinese, multi-condition, and graph chronology evidence observable when later filters or pack compaction drop it.

**Tech Stack:** Python dataclasses, existing `MemoryService`, `SQLiteMemoryStore`, `PostgresMemoryStore`, deterministic rule extraction, `unittest`, existing BEAM replay tooling.

## Global Constraints

- Do not delete existing `event_ordering_*` modules until shadow replay proves graph parity or improvement.
- Do not add more project-specific or software-specific regex branches to rescue individual BEAM cases.
- Do not make real LLM extractor/router part of the synchronous write/read path.
- Graph write failure must not fail user memory writes.
- Query-time graph building remains only as a compatibility fallback during migration.
- Rules kept in code must be high-precision generic order/date/status/phase markers.
- Framework/tool/domain terms must migrate to taxonomy config, not private regex branches.
- Every implementation task must use TDD: write failing tests, run red, implement minimal code, run green.

---

## File Structure

Create:

- `fusion_memory/core/chronology.py`: dataclasses for `ChronologyTopic`, `ChronologyPhase`, `ChronologyEventNode`, `ChronologyEventEdge`, and query result records.
- `fusion_memory/retrieval/chronology_normalizer.py`: deterministic write-time normalizer that converts accepted events/spans into topics, phases, nodes, and edges.
- `fusion_memory/retrieval/chronology_selector.py`: graph-first event-ordering selector that reads stored chronology graph records.
- `fusion_memory/retrieval/preservation.py`: shared candidate preservation helpers and dropped-candidate telemetry.
- `fusion_memory/retrieval/taxonomy.py`: configurable taxonomy loading and alias matching for domain/topic labels.
- `tests/test_chronology_models_and_storage.py`: graph model and SQLite/Postgres repository tests.
- `tests/test_chronology_normalizer.py`: write-time normalization tests.
- `tests/test_chronology_selector.py`: persisted graph selector tests.
- `tests/test_retrieval_preservation.py`: high-signal evidence preservation tests.

Modify:

- `fusion_memory/core/models.py`: optional exports or shared literal types if needed.
- `fusion_memory/storage/sqlite_store.py`: create graph tables and CRUD methods.
- `fusion_memory/storage/postgres_store.py`: create graph table support in Postgres migration and facade CRUD methods.
- `fusion_memory/storage/migrations/postgres/001_init.sql` if this path exists during implementation; otherwise update the SQL string/path used by `PostgresMigrationRunner`.
- `fusion_memory/api/service.py`: integrate graph normalizer in add path and graph selector in event-ordering candidate path.
- `fusion_memory/retrieval/candidate_provider.py`: keep graph candidates before legacy event-ordering candidates and add shadow metadata.
- `fusion_memory/retrieval/evidence_pack.py`: expose dropped high-signal candidates and graph chronology metadata.
- `tools/beam_event_ordering_replay.py`: add persisted graph diagnostics and pass/fail gate mode.
- Existing tests that assert event-ordering graph behavior.

---

### Task 1: Chronology Models And Storage Repositories

**Files:**
- Create: `fusion_memory/core/chronology.py`
- Modify: `fusion_memory/storage/sqlite_store.py`
- Modify: `fusion_memory/storage/postgres_store.py`
- Test: `tests/test_chronology_models_and_storage.py`

**Interfaces:**
- Produces:
  - `ChronologyTopic(topic_id: str, scope: Scope, canonical_label: str, aliases: list[str], language: str, taxonomy_tags: list[str], source_span_ids: list[str], confidence: float, created_at: datetime)`
  - `ChronologyPhase(phase_id: str, topic_id: str, phase_type: str, order_hint: int | None, source_span_ids: list[str], confidence: float, created_at: datetime)`
  - `ChronologyEventNode(node_id: str, scope: Scope, actor: str, action: str, object: str, topic_id: str | None, phase_id: str | None, timestamp: datetime | None, source_span_id: str | None, source_turn_id: str | None, text: str, language: str, confidence: float, explicit_order_marker: str | None, created_at: datetime)`
  - `ChronologyEventEdge(edge_id: str, from_node_id: str, to_node_id: str, edge_type: str, evidence_type: str, source_span_ids: list[str], confidence: float, created_at: datetime)`
  - Store methods:
    - `upsert_chronology_topic(topic: ChronologyTopic) -> None`
    - `upsert_chronology_phase(phase: ChronologyPhase) -> None`
    - `upsert_chronology_event_node(node: ChronologyEventNode) -> None`
    - `insert_chronology_event_edge(edge: ChronologyEventEdge) -> bool`
    - `list_chronology_topics(scope: Scope, include_session: bool = False) -> list[ChronologyTopic]`
    - `list_chronology_phases(topic_ids: list[str]) -> list[ChronologyPhase]`
    - `list_chronology_event_nodes(scope: Scope, include_session: bool = False, topic_ids: list[str] | None = None) -> list[ChronologyEventNode]`
    - `list_chronology_event_edges(node_ids: list[str]) -> list[ChronologyEventEdge]`

- [ ] **Step 1: Write failing SQLite round-trip test**

Add this to `tests/test_chronology_models_and_storage.py`:

```python
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
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_chronology_models_and_storage -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fusion_memory.core.chronology'` or missing store methods.

- [ ] **Step 3: Add dataclasses**

Create `fusion_memory/core/chronology.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fusion_memory.core.models import Scope


@dataclass
class ChronologyTopic:
    topic_id: str
    scope: Scope
    canonical_label: str
    aliases: list[str]
    language: str
    taxonomy_tags: list[str]
    source_span_ids: list[str]
    confidence: float
    created_at: datetime


@dataclass
class ChronologyPhase:
    phase_id: str
    topic_id: str
    phase_type: str
    order_hint: int | None
    source_span_ids: list[str]
    confidence: float
    created_at: datetime


@dataclass
class ChronologyEventNode:
    node_id: str
    scope: Scope
    actor: str
    action: str
    object: str
    topic_id: str | None
    phase_id: str | None
    timestamp: datetime | None
    source_span_id: str | None
    source_turn_id: str | None
    text: str
    language: str
    confidence: float
    explicit_order_marker: str | None
    created_at: datetime


@dataclass
class ChronologyEventEdge:
    edge_id: str
    from_node_id: str
    to_node_id: str
    edge_type: str
    evidence_type: str
    source_span_ids: list[str]
    confidence: float
    created_at: datetime
```

- [ ] **Step 4: Add SQLite tables and CRUD**

Modify `fusion_memory/storage/sqlite_store.py`:

```python
from fusion_memory.core.chronology import (
    ChronologyEventEdge,
    ChronologyEventNode,
    ChronologyPhase,
    ChronologyTopic,
)
```

Add tables inside `migrate()` before the closing `"""`:

```sql
create table if not exists chronology_topics (
  topic_id text primary key,
  workspace_id text,
  user_id text,
  agent_id text,
  run_id text,
  session_id text,
  app_id text,
  canonical_label text not null,
  aliases text not null default '[]',
  language text not null default 'unknown',
  taxonomy_tags text not null default '[]',
  source_span_ids text not null default '[]',
  confidence real not null,
  created_at text not null
);
create index if not exists chronology_topics_scope_idx on chronology_topics(workspace_id, user_id, agent_id, run_id, session_id);

create table if not exists chronology_phases (
  phase_id text primary key,
  topic_id text not null,
  phase_type text not null,
  order_hint integer,
  source_span_ids text not null default '[]',
  confidence real not null,
  created_at text not null
);
create index if not exists chronology_phases_topic_idx on chronology_phases(topic_id, order_hint);

create table if not exists chronology_event_nodes (
  node_id text primary key,
  workspace_id text,
  user_id text,
  agent_id text,
  run_id text,
  session_id text,
  app_id text,
  actor text not null,
  action text not null,
  object text not null,
  topic_id text,
  phase_id text,
  timestamp text,
  source_span_id text,
  source_turn_id text,
  text text not null,
  language text not null default 'unknown',
  confidence real not null,
  explicit_order_marker text,
  created_at text not null
);
create index if not exists chronology_nodes_scope_idx on chronology_event_nodes(workspace_id, user_id, agent_id, run_id, session_id);
create index if not exists chronology_nodes_topic_idx on chronology_event_nodes(topic_id, timestamp);

create table if not exists chronology_event_edges (
  edge_id text primary key,
  from_node_id text not null,
  to_node_id text not null,
  edge_type text not null,
  evidence_type text not null,
  source_span_ids text not null default '[]',
  confidence real not null,
  created_at text not null
);
create unique index if not exists chronology_edges_unique_idx on chronology_event_edges(from_node_id, to_node_id, edge_type, evidence_type);
```

Add CRUD methods near existing event methods:

```python
def upsert_chronology_topic(self, topic: ChronologyTopic) -> None:
    values = {
        **self._scope_columns(topic.scope),
        "topic_id": topic.topic_id,
        "canonical_label": topic.canonical_label,
        "aliases": dumps(topic.aliases),
        "language": topic.language,
        "taxonomy_tags": dumps(topic.taxonomy_tags),
        "source_span_ids": dumps(topic.source_span_ids),
        "confidence": topic.confidence,
        "created_at": dt_to_str(topic.created_at),
    }
    columns = ", ".join(values.keys())
    placeholders = ", ".join(["?"] * len(values))
    updates = ", ".join(f"{key}=excluded.{key}" for key in values if key != "topic_id")
    self.conn.execute(
        f"insert into chronology_topics ({columns}) values ({placeholders}) "
        f"on conflict(topic_id) do update set {updates}",
        list(values.values()),
    )
    self.conn.commit()

def upsert_chronology_phase(self, phase: ChronologyPhase) -> None:
    values = {
        "phase_id": phase.phase_id,
        "topic_id": phase.topic_id,
        "phase_type": phase.phase_type,
        "order_hint": phase.order_hint,
        "source_span_ids": dumps(phase.source_span_ids),
        "confidence": phase.confidence,
        "created_at": dt_to_str(phase.created_at),
    }
    columns = ", ".join(values.keys())
    placeholders = ", ".join(["?"] * len(values))
    updates = ", ".join(f"{key}=excluded.{key}" for key in values if key != "phase_id")
    self.conn.execute(
        f"insert into chronology_phases ({columns}) values ({placeholders}) "
        f"on conflict(phase_id) do update set {updates}",
        list(values.values()),
    )
    self.conn.commit()

def upsert_chronology_event_node(self, node: ChronologyEventNode) -> None:
    values = {
        **self._scope_columns(node.scope),
        "node_id": node.node_id,
        "actor": node.actor,
        "action": node.action,
        "object": node.object,
        "topic_id": node.topic_id,
        "phase_id": node.phase_id,
        "timestamp": dt_to_str(node.timestamp),
        "source_span_id": node.source_span_id,
        "source_turn_id": node.source_turn_id,
        "text": node.text,
        "language": node.language,
        "confidence": node.confidence,
        "explicit_order_marker": node.explicit_order_marker,
        "created_at": dt_to_str(node.created_at),
    }
    columns = ", ".join(values.keys())
    placeholders = ", ".join(["?"] * len(values))
    updates = ", ".join(f"{key}=excluded.{key}" for key in values if key != "node_id")
    self.conn.execute(
        f"insert into chronology_event_nodes ({columns}) values ({placeholders}) "
        f"on conflict(node_id) do update set {updates}",
        list(values.values()),
    )
    self.conn.commit()

def insert_chronology_event_edge(self, edge: ChronologyEventEdge) -> bool:
    cur = self.conn.execute(
        """
        insert or ignore into chronology_event_edges
        (edge_id, from_node_id, to_node_id, edge_type, evidence_type, source_span_ids, confidence, created_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge.edge_id,
            edge.from_node_id,
            edge.to_node_id,
            edge.edge_type,
            edge.evidence_type,
            dumps(edge.source_span_ids),
            edge.confidence,
            dt_to_str(edge.created_at),
        ),
    )
    self.conn.commit()
    return cur.rowcount > 0
```

Also add `list_chronology_topics`, `list_chronology_phases`, `list_chronology_event_nodes`, `list_chronology_event_edges` with row mappers. Use existing `loads`, `dt_from_str`, and `_scope_where` patterns. Ordering must be by timestamp/created_at for nodes and by confidence descending for topics when no explicit order exists.

- [ ] **Step 5: Add Postgres migration and facade methods**

Modify `fusion_memory/storage/postgres_store.py` so `POSTGRES_TABLES` includes:

```python
"chronology_topics",
"chronology_phases",
"chronology_event_nodes",
"chronology_event_edges",
```

Add equivalent SQL tables to the Postgres migration source. Use `jsonb` for list fields and `timestamptz` for date fields. Add facade methods on `PostgresMemoryStore` with the same signatures as SQLite. If existing Postgres repositories are too large, implement the methods on the facade using direct cursor SQL first; move to a repository class in a later refactor.

- [ ] **Step 6: Run green tests**

Run:

```bash
python3 -m unittest tests.test_chronology_models_and_storage -v
python3 -m unittest tests.test_postgres_verifier tests.test_postgres_memory_store_facade -v
python3 -m py_compile fusion_memory/core/chronology.py fusion_memory/storage/sqlite_store.py fusion_memory/storage/postgres_store.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add fusion_memory/core/chronology.py fusion_memory/storage/sqlite_store.py fusion_memory/storage/postgres_store.py tests/test_chronology_models_and_storage.py
git commit -m "feat: add persistent chronology graph storage"
```

---

### Task 2: Deterministic Write-Time Chronology Normalizer

**Files:**
- Create: `fusion_memory/retrieval/chronology_normalizer.py`
- Test: `tests/test_chronology_normalizer.py`

**Interfaces:**
- Consumes: dataclasses and store methods from Task 1.
- Produces:
  - `ChronologyWriteBatch(topics: list[ChronologyTopic], phases: list[ChronologyPhase], nodes: list[ChronologyEventNode], edges: list[ChronologyEventEdge], telemetry: dict[str, object])`
  - `build_chronology_write_batch(scope: Scope, spans: list[EvidenceSpan], events: list[MemoryEvent]) -> ChronologyWriteBatch`

- [ ] **Step 1: Write failing normalizer test**

Create `tests/test_chronology_normalizer.py`:

```python
from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta

from fusion_memory.core.models import EvidenceSpan, MemoryEvent, Scope
from fusion_memory.core.text import stable_hash
from fusion_memory.retrieval.chronology_normalizer import build_chronology_write_batch


class ChronologyNormalizerTests(unittest.TestCase):
    def test_build_chronology_batch_extracts_action_object_phase_topic_and_order_edges(self) -> None:
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        base = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        spans = [
            EvidenceSpan(
                span_id="s1",
                scope=scope,
                turn_id="t1",
                speaker="user",
                span_type="turn",
                content="I first set up the budget tracker schema.",
                content_hash=stable_hash("s1"),
                timestamp=base,
            ),
            EvidenceSpan(
                span_id="s2",
                scope=scope,
                turn_id="t2",
                speaker="user",
                span_type="turn",
                content="Then I implemented transaction CRUD validation.",
                content_hash=stable_hash("s2"),
                timestamp=base + timedelta(minutes=5),
            ),
        ]
        events = [
            MemoryEvent(
                event_id="e1",
                scope=scope,
                event_type="user_action",
                description=spans[0].content,
                participants=["user"],
                source_span_ids=["s1"],
                time_start=base,
                confidence=0.8,
            ),
            MemoryEvent(
                event_id="e2",
                scope=scope,
                event_type="user_action",
                description=spans[1].content,
                participants=["user"],
                source_span_ids=["s2"],
                time_start=base + timedelta(minutes=5),
                confidence=0.8,
            ),
        ]

        batch = build_chronology_write_batch(scope, spans, events)

        self.assertEqual(len(batch.nodes), 2)
        self.assertEqual(batch.nodes[0].actor, "user")
        self.assertEqual(batch.nodes[0].explicit_order_marker, "first")
        self.assertEqual(batch.nodes[1].explicit_order_marker, "then")
        self.assertTrue(any(topic.canonical_label == "budget tracker" for topic in batch.topics))
        self.assertTrue(any(phase.phase_type == "setup" for phase in batch.phases))
        self.assertTrue(any(edge.edge_type == "before" and edge.evidence_type == "explicit_marker" for edge in batch.edges))

    def test_chinese_order_markers_are_supported_without_llm(self) -> None:
        scope = Scope(workspace_id="w", user_id="u", agent_id="a", session_id="s")
        base = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        spans = [
            EvidenceSpan(
                span_id="cn1",
                scope=scope,
                turn_id="t1",
                speaker="user",
                span_type="turn",
                content="我先完成了记忆系统的初始化配置。",
                content_hash=stable_hash("cn1"),
                timestamp=base,
            ),
            EvidenceSpan(
                span_id="cn2",
                scope=scope,
                turn_id="t2",
                speaker="user",
                span_type="turn",
                content="然后我开始测试中文召回。",
                content_hash=stable_hash("cn2"),
                timestamp=base + timedelta(minutes=5),
            ),
        ]
        events = [
            MemoryEvent("e1", scope, "user_action", spans[0].content, ["user"], ["cn1"], time_start=base, confidence=0.8),
            MemoryEvent("e2", scope, "user_action", spans[1].content, ["user"], ["cn2"], time_start=base + timedelta(minutes=5), confidence=0.8),
        ]

        batch = build_chronology_write_batch(scope, spans, events)

        self.assertEqual([node.language for node in batch.nodes], ["zh", "zh"])
        self.assertEqual(batch.nodes[0].explicit_order_marker, "first")
        self.assertEqual(batch.nodes[1].explicit_order_marker, "then")
        self.assertTrue(batch.edges)
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_chronology_normalizer -v
```

Expected: FAIL because `chronology_normalizer` does not exist.

- [ ] **Step 3: Implement normalizer**

Create `fusion_memory/retrieval/chronology_normalizer.py` with:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fusion_memory.core.chronology import ChronologyEventEdge, ChronologyEventNode, ChronologyPhase, ChronologyTopic
from fusion_memory.core.models import EvidenceSpan, MemoryEvent, Scope
from fusion_memory.core.text import compact_summary, stable_hash, tokenize


@dataclass
class ChronologyWriteBatch:
    topics: list[ChronologyTopic]
    phases: list[ChronologyPhase]
    nodes: list[ChronologyEventNode]
    edges: list[ChronologyEventEdge]
    telemetry: dict[str, object]
```

Implement `build_chronology_write_batch(scope, spans, events)`:

- Map events to their first source span.
- Infer language: `zh` when text contains CJK, otherwise `en`.
- Infer explicit order marker with generic English and Chinese markers:
  - `first`: `first`, `initially`, `start`, `started`, `先`, `首先`, `一开始`
  - `then`: `then`, `next`, `later`, `after that`, `然后`, `接着`, `随后`
  - `finally`: `finally`, `最后`
  - `before`: `before`, `之前`
  - `after`: `after`, `之后`
- Infer phase:
  - `setup`: setup/set up/initialize/初始化/schema/配置
  - `implementation`: implement/build/add/实现/开发/完成
  - `debug`: debug/fix/error/修复/报错
  - `validation`: test/verify/coverage/测试/验证
  - `release`: deploy/release/上线/部署
  - default `unknown`
- Infer topic conservatively from repeated non-stopword tokens or a compact noun phrase. For the first pass, use `"budget tracker"` when both words appear, `"memory system"` for memory/记忆系统, otherwise the first 2-4 meaningful tokens.
- Build deterministic IDs with `stable_hash`.
- Create `before` edges for adjacent same-topic nodes when either adjacent node has an explicit order marker or both have timestamps and phase/order evidence.

Do not add framework-specific rule branches outside topic extraction examples required by the tests.

- [ ] **Step 4: Run green tests**

Run:

```bash
python3 -m unittest tests.test_chronology_normalizer -v
python3 -m py_compile fusion_memory/retrieval/chronology_normalizer.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fusion_memory/retrieval/chronology_normalizer.py tests/test_chronology_normalizer.py
git commit -m "feat: normalize write-time chronology graph"
```

---

### Task 3: Integrate Chronology Graph Writes Into MemoryService

**Files:**
- Modify: `fusion_memory/api/service.py`
- Test: `tests/test_chronology_normalizer.py`
- Test: `tests/test_fusion_memory.py`

**Interfaces:**
- Consumes: `build_chronology_write_batch(scope, spans, events) -> ChronologyWriteBatch`.
- Produces:
  - `MemoryService.add()` writes graph records after accepted events.
  - Add result trace includes `chronology_graph` counts and non-fatal error metadata.

- [ ] **Step 1: Write failing service integration test**

Append to `tests/test_chronology_normalizer.py`:

```python
from fusion_memory import MemoryService


class ChronologyServiceIntegrationTests(unittest.TestCase):
    def test_add_writes_chronology_graph_without_changing_add_contract(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="graph-write", user_id="u", agent_id="a", session_id="s")
        timestamp = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        result = memory.add(
            "I first set up the graph schema. Then I implemented the selector.",
            scope,
            timestamp,
            {"source_uri": "test:graph-write"},
        )

        self.assertTrue(result.span_ids)
        nodes = memory.store.list_chronology_event_nodes(scope, include_session=True)
        self.assertGreaterEqual(len(nodes), 1)
        self.assertTrue(any(node.source_span_id in result.span_ids for node in nodes))
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_chronology_normalizer.ChronologyServiceIntegrationTests -v
```

Expected: FAIL because `MemoryService.add()` does not call the graph normalizer.

- [ ] **Step 3: Integrate write path**

Modify `fusion_memory/api/service.py`:

```python
from fusion_memory.retrieval.chronology_normalizer import build_chronology_write_batch
```

After accepted events are inserted and before trace finalization, add a private method:

```python
def _write_chronology_graph(self, scope: Scope, spans: list[EvidenceSpan], accepted_event_ids: list[str]) -> dict[str, Any]:
    if not spans:
        return {"enabled": True, "node_count": 0, "edge_count": 0, "topic_count": 0, "phase_count": 0}
    events = [event for event in self.store.list_events(scope, include_session=True) if event.event_id in set(accepted_event_ids)]
    try:
        batch = build_chronology_write_batch(scope, spans, events)
        for topic in batch.topics:
            self.store.upsert_chronology_topic(topic)
        for phase in batch.phases:
            self.store.upsert_chronology_phase(phase)
        for node in batch.nodes:
            self.store.upsert_chronology_event_node(node)
        inserted_edges = 0
        for edge in batch.edges:
            inserted_edges += int(self.store.insert_chronology_event_edge(edge))
        return {
            "enabled": True,
            "topic_count": len(batch.topics),
            "phase_count": len(batch.phases),
            "node_count": len(batch.nodes),
            "edge_count": inserted_edges,
            "telemetry": batch.telemetry,
        }
    except Exception as exc:
        return {"enabled": True, "error": exc.__class__.__name__, "node_count": 0, "edge_count": 0, "topic_count": 0, "phase_count": 0}
```

Call it from `add()` using the same accepted spans list currently stored in `extraction_spans` or inserted source spans.

Add graph write counts to trace metadata. Do not change `AddResult`.

- [ ] **Step 4: Run green tests**

Run:

```bash
python3 -m unittest tests.test_chronology_normalizer tests.test_fusion_memory.FusionMemoryTests.test_add_and_search_roundtrip -v
python3 -m py_compile fusion_memory/api/service.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add fusion_memory/api/service.py tests/test_chronology_normalizer.py
git commit -m "feat: write chronology graph during add"
```

---

### Task 4: Persisted Graph-First Event-Ordering Selector

**Files:**
- Create: `fusion_memory/retrieval/chronology_selector.py`
- Modify: `fusion_memory/api/service.py`
- Modify: `fusion_memory/retrieval/candidate_provider.py`
- Test: `tests/test_chronology_selector.py`

**Interfaces:**
- Consumes: store chronology list methods.
- Produces:
  - `select_persisted_graph_event_ordering_candidates(query: str, scope: Scope, store: Any, limit: int, include_session: bool = False) -> tuple[list[Candidate], dict[str, object]]`
  - Candidate source `event_ordering_persisted_graph`
  - Candidate metadata: `graph_node_id`, `graph_topic_id`, `graph_phase_id`, `timeline_index`, `must_preserve_reason=["graph_chronology_anchor"]`

- [ ] **Step 1: Write failing selector test**

Create `tests/test_chronology_selector.py`:

```python
from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta

from fusion_memory import MemoryService
from fusion_memory.core.models import Scope
from fusion_memory.retrieval.chronology_selector import select_persisted_graph_event_ordering_candidates


class ChronologySelectorTests(unittest.TestCase):
    def test_persisted_graph_selector_returns_topic_scoped_ordered_candidates(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="graph-select", user_id="u", agent_id="a", session_id="s")
        base = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        memory.add("I first set up the budget tracker schema.", scope, base, {"source_uri": "m1"})
        memory.add("Then I implemented transaction CRUD validation.", scope, base + timedelta(minutes=5), {"source_uri": "m2"})
        memory.add("Unrelated: I changed my lunch plan.", scope, base + timedelta(minutes=10), {"source_uri": "m3"})

        candidates, telemetry = select_persisted_graph_event_ordering_candidates(
            "List the budget tracker work in order.",
            scope,
            memory.store,
            limit=5,
            include_session=True,
        )

        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0].source, "event_ordering_persisted_graph")
        self.assertIn("schema", candidates[0].text.lower())
        self.assertIn("crud", candidates[1].text.lower())
        self.assertTrue(all("lunch" not in candidate.text.lower() for candidate in candidates))
        self.assertEqual(telemetry["selected_driver"], "persisted_graph")
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_chronology_selector -v
```

Expected: FAIL because `chronology_selector` does not exist.

- [ ] **Step 3: Implement selector**

Create `fusion_memory/retrieval/chronology_selector.py`:

```python
from __future__ import annotations

from typing import Any

from fusion_memory.core.models import Candidate, Scope
from fusion_memory.core.text import keyword_score


def select_persisted_graph_event_ordering_candidates(
    query: str,
    scope: Scope,
    store: Any,
    limit: int,
    *,
    include_session: bool = False,
) -> tuple[list[Candidate], dict[str, object]]:
    topics = store.list_chronology_topics(scope, include_session=include_session)
    scored_topics = [
        (keyword_score(query, topic.canonical_label + " " + " ".join(topic.aliases)), topic)
        for topic in topics
    ]
    scored_topics = [(score, topic) for score, topic in scored_topics if score > 0]
    scored_topics.sort(key=lambda item: (-item[0], item[1].canonical_label))
    topic_ids = [topic.topic_id for _score, topic in scored_topics[:3]]
    if not topic_ids:
        return [], {"selected_driver": "none", "fallback_reason": "no_topic"}
    phases = {phase.phase_id: phase for phase in store.list_chronology_phases(topic_ids)}
    nodes = store.list_chronology_event_nodes(scope, include_session=include_session, topic_ids=topic_ids)
    if not nodes:
        return [], {"selected_driver": "none", "fallback_reason": "no_nodes", "topic_ids": topic_ids}
    node_ids = [node.node_id for node in nodes]
    edge_count_by_node: dict[str, int] = {node_id: 0 for node_id in node_ids}
    for edge in store.list_chronology_event_edges(node_ids):
        edge_count_by_node[edge.from_node_id] = edge_count_by_node.get(edge.from_node_id, 0) + 1
        edge_count_by_node[edge.to_node_id] = edge_count_by_node.get(edge.to_node_id, 0) + 1
    nodes.sort(key=lambda node: (node.timestamp is None, node.timestamp.isoformat() if node.timestamp else "", _phase_order(phases.get(node.phase_id)), node.node_id))
    candidates: list[Candidate] = []
    for index, node in enumerate(nodes, start=1):
        score = 0.55 + keyword_score(query, f"{node.text} {node.action} {node.object}") + min(0.2, edge_count_by_node.get(node.node_id, 0) * 0.05)
        candidates.append(
            Candidate(
                id=node.node_id,
                type="event",
                text=_candidate_text(node),
                source="event_ordering_persisted_graph",
                scores={"score": score, "graph_proximity": min(1.0, 0.5 + edge_count_by_node.get(node.node_id, 0) * 0.1), "temporal_fit": 0.95 if node.timestamp else 0.55},
                source_span_ids=[node.source_span_id] if node.source_span_id else [],
                metadata={
                    "graph_node_id": node.node_id,
                    "graph_topic_id": node.topic_id,
                    "graph_phase_id": node.phase_id,
                    "timeline_index": index,
                    "must_preserve_reason": ["graph_chronology_anchor"],
                    "evidence_role": "answer",
                },
            )
        )
    return candidates[:limit], {"selected_driver": "persisted_graph", "topic_ids": topic_ids, "node_count": len(nodes), "candidate_count": min(len(candidates), limit)}
```

Implement helper `_phase_order(phase)` and `_candidate_text(node)` in the same module. `_candidate_text` should prefer `"{phase_type}: {action} {object}"` when action/object are useful, otherwise `node.text`.

- [ ] **Step 4: Wire service graph selector**

Modify `MemoryService._event_ordering_graph_selector_candidates()`:

1. Call `select_persisted_graph_event_ordering_candidates()`.
2. If candidates are returned, use them.
3. If no candidates, fall back to existing query-time `select_graph_first_event_ordering_candidates`.
4. Store telemetry in candidate metadata or coverage via `_event_ordering_shadow_coverage`.

- [ ] **Step 5: Run green tests**

Run:

```bash
python3 -m unittest tests.test_chronology_selector tests.test_event_ordering_graph -v
python3 -m py_compile fusion_memory/retrieval/chronology_selector.py fusion_memory/api/service.py fusion_memory/retrieval/candidate_provider.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add fusion_memory/retrieval/chronology_selector.py fusion_memory/api/service.py fusion_memory/retrieval/candidate_provider.py tests/test_chronology_selector.py
git commit -m "feat: select event ordering from persisted chronology graph"
```

---

### Task 5: Shadow Replay Gate Diagnostics

**Files:**
- Modify: `tools/beam_event_ordering_replay.py`
- Test: `tests/test_beam_event_ordering_replay.py`

**Interfaces:**
- Consumes: persisted graph selector candidate source `event_ordering_persisted_graph`.
- Produces:
  - CLI option `--gate`
  - Summary fields `graph_vs_legacy_passed`, `gate_failures`, `path_wins`
  - Per-query fields `topic_drift_count`, `duplicate_label_count`, `graph_empty`

- [ ] **Step 1: Write failing gate test**

Append to `tests/test_beam_event_ordering_replay.py`:

```python
from tools.beam_event_ordering_replay import evaluate_gate


class BeamEventOrderingGateTests(unittest.TestCase):
    def test_evaluate_gate_requires_graph_to_match_legacy_f1_and_tau(self) -> None:
        summary = {
            "graph": {"f1": 0.10, "kendall_tau_norm": 0.20, "empty_rate": 0.0},
            "legacy": {"f1": 0.20, "kendall_tau_norm": 0.25, "empty_rate": 0.0},
            "hybrid": {"f1": 0.18, "kendall_tau_norm": 0.24, "empty_rate": 0.0},
        }

        gate = evaluate_gate(summary)

        self.assertFalse(gate["passed"])
        self.assertIn("graph_f1_below_legacy", gate["failures"])
        self.assertIn("graph_tau_below_legacy", gate["failures"])
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_beam_event_ordering_replay -v
```

Expected: FAIL because `evaluate_gate` is missing.

- [ ] **Step 3: Implement gate function**

Modify `tools/beam_event_ordering_replay.py`:

```python
def evaluate_gate(summary: dict[str, dict[str, float]]) -> dict[str, object]:
    failures: list[str] = []
    graph = summary.get("graph", {})
    legacy = summary.get("legacy", {})
    hybrid = summary.get("hybrid", {})
    if float(graph.get("f1", 0.0)) < float(legacy.get("f1", 0.0)):
        failures.append("graph_f1_below_legacy")
    if float(graph.get("kendall_tau_norm", 0.0)) < float(legacy.get("kendall_tau_norm", 0.0)):
        failures.append("graph_tau_below_legacy")
    if float(hybrid.get("f1", 0.0)) < float(legacy.get("f1", 0.0)):
        failures.append("hybrid_f1_below_legacy")
    if float(graph.get("empty_rate", 1.0)) > float(legacy.get("empty_rate", 0.0)):
        failures.append("graph_empty_rate_above_legacy")
    return {"passed": not failures, "failures": failures}
```

Add parser flag:

```python
parser.add_argument("--gate", action="store_true")
```

Add `report["gate"] = evaluate_gate(report["summary"])` when `args.gate` is true. If gate fails, keep exit code 0 for reporting mode; later CI can enforce failure after thresholds stabilize.

- [ ] **Step 4: Add path win counts**

In `_aggregate(records)`, add:

```python
"path_wins": _path_wins(records)
```

Implement `_path_wins(records)` for `f1` and `kendall_tau_norm`.

- [ ] **Step 5: Run green tests and a small replay**

Run:

```bash
python3 -m unittest tests.test_beam_event_ordering_replay -v
python3 tools/beam_event_ordering_replay.py --workspace beam_100k_rule_qwenembed_sessionized_20260612_1745 --split 100k --dataset /public/home/wwb/datasets/BEAM --db postgresql://fusion:fusion@127.0.0.1:55433/fusion_memory --max-queries 3 --gate --output .runtime/beam-runs/event_ordering_gate_smoke.json
```

Expected: tests PASS. Replay command writes JSON with a `gate` object. If local Postgres is unavailable, run only the unit test and document the replay skip.

- [ ] **Step 6: Commit**

```bash
git add tools/beam_event_ordering_replay.py tests/test_beam_event_ordering_replay.py
git commit -m "test: add event ordering graph legacy gate"
```

---

### Task 6: Retrieval Preservation Contract

**Files:**
- Create: `fusion_memory/retrieval/preservation.py`
- Modify: `fusion_memory/api/service.py`
- Modify: `fusion_memory/retrieval/evidence_pack.py`
- Test: `tests/test_retrieval_preservation.py`

**Interfaces:**
- Produces:
  - `mark_must_preserve(candidate: Candidate, reason: str, evidence_role: str = "answer") -> Candidate`
  - `must_preserve_reasons(candidate: Candidate) -> list[str]`
  - `preserve_required_candidates(candidates: list[Candidate], selected: list[Candidate], limit: int) -> tuple[list[Candidate], list[dict[str, object]]]`
  - Coverage field `dropped_high_signal_candidates`

- [ ] **Step 1: Write failing preservation test**

Create `tests/test_retrieval_preservation.py`:

```python
from __future__ import annotations

import unittest

from fusion_memory.core.models import Candidate
from fusion_memory.retrieval.preservation import mark_must_preserve, preserve_required_candidates


class RetrievalPreservationTests(unittest.TestCase):
    def test_preserve_required_candidates_adds_missing_high_signal_candidate_and_reports_drops(self) -> None:
        required = mark_must_preserve(
            Candidate("current", "view", "Current city is Berlin.", "l3_current_view", {"score": 0.9}, ["s1"], {}),
            "current_value",
        )
        selected = [Candidate("old", "fact", "Old city was Paris.", "l1_fact_hybrid", {"score": 1.0}, ["s2"], {})]

        preserved, dropped = preserve_required_candidates([required, *selected], selected, limit=2)

        self.assertEqual([candidate.id for candidate in preserved], ["old", "current"])
        self.assertEqual(dropped, [])

    def test_preserve_required_candidates_reports_when_budget_forces_drop(self) -> None:
        required = mark_must_preserve(
            Candidate("graph", "event", "setup schema", "event_ordering_persisted_graph", {"score": 0.9}, ["s1"], {}),
            "graph_chronology_anchor",
        )
        selected = [Candidate("top", "span", "top ranked", "l0_raw_hybrid", {"score": 1.0}, ["s2"], {})]

        preserved, dropped = preserve_required_candidates([required, *selected], selected, limit=1)

        self.assertEqual([candidate.id for candidate in preserved], ["top"])
        self.assertEqual(dropped[0]["candidate_id"], "graph")
        self.assertEqual(dropped[0]["reason"], "budget_limit")
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_retrieval_preservation -v
```

Expected: FAIL because `preservation.py` does not exist.

- [ ] **Step 3: Implement preservation helpers**

Create `fusion_memory/retrieval/preservation.py` with functions listed in Interfaces. Preserve metadata by copying `Candidate` into a new `Candidate` object; do not mutate input candidates in place.

- [ ] **Step 4: Integrate after final filters**

In `MemoryService.search()`, after final topic/current-value filters and before writing utility examples, call:

```python
selected, dropped_high_signal = preserve_required_candidates(scored_again, selected, limit)
```

Add `dropped_high_signal_candidates` to `coverage`.

In `EvidencePackBuilder.build()`, copy `coverage["dropped_high_signal_candidates"]` into pack coverage; no new public schema object is needed.

- [ ] **Step 5: Run green tests**

Run:

```bash
python3 -m unittest tests.test_retrieval_preservation tests.test_fusion_memory -v
python3 -m py_compile fusion_memory/retrieval/preservation.py fusion_memory/api/service.py fusion_memory/retrieval/evidence_pack.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add fusion_memory/retrieval/preservation.py fusion_memory/api/service.py fusion_memory/retrieval/evidence_pack.py tests/test_retrieval_preservation.py
git commit -m "feat: preserve high signal retrieval candidates"
```

---

### Task 7: Chinese, Current-Value, And Multi-Condition Regression Fixtures

**Files:**
- Modify: `tests/test_retrieval_preservation.py`
- Modify: `fusion_memory/core/text.py`
- Modify: `fusion_memory/api/service_helpers.py`
- Modify: `fusion_memory/api/service.py`

**Interfaces:**
- Consumes: preservation contract from Task 6.
- Produces:
  - Chinese exact phrase candidates marked with `language_exact_match`.
  - Current-value candidates marked with `current_value`.
  - Multi-condition candidates marked with `matched_conditions`.

- [ ] **Step 1: Add failing Chinese recall preservation test**

Append:

```python
from datetime import datetime, timezone
from fusion_memory import MemoryService, Scope


class RetrievalRegressionFixtureTests(unittest.TestCase):
    def test_chinese_exact_phrase_survives_search(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="zh-recall", user_id="u", agent_id="a", session_id="s")
        memory.add("请记住：我的默认数据库是 PostgreSQL，嵌入模型是 qwen0.6B。", scope, datetime(2026, 6, 18, tzinfo=timezone.utc), {"source_uri": "zh1"})

        result = memory.search("我的默认数据库是什么？", scope, {"mode": "fast", "limit": 5})

        self.assertTrue(any("PostgreSQL" in candidate.text for candidate in result.candidates))
```

- [ ] **Step 2: Add failing current-value stale filtering test**

Append:

```python
    def test_current_value_preserves_latest_view_over_stale_history(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="current-value", user_id="u", agent_id="a", session_id="s")
        memory.add("My preferred database is SQLite.", scope, datetime(2026, 6, 1, tzinfo=timezone.utc), {"source_uri": "old"})
        memory.add("Update: my preferred database is PostgreSQL now.", scope, datetime(2026, 6, 2, tzinfo=timezone.utc), {"source_uri": "new"})

        pack = memory.answer_context("What is my current preferred database?", scope, budget={"mode": "benchmark"})

        joined = " ".join(span.get("content", "") for span in pack.source_spans)
        self.assertIn("PostgreSQL", joined)
        self.assertNotIn("SQLite", joined[:200])
```

- [ ] **Step 3: Add failing multi-condition distributed evidence test**

Append:

```python
    def test_multi_condition_recall_preserves_distributed_evidence(self) -> None:
        memory = MemoryService()
        scope = Scope(workspace_id="multi-condition", user_id="u", agent_id="a", session_id="s")
        ts = datetime(2026, 6, 18, tzinfo=timezone.utc)
        memory.add("For the OpenClaw adapter, install must be one command.", scope, ts, {"source_uri": "m1"})
        memory.add("For the same adapter, errors must be beginner friendly.", scope, ts, {"source_uri": "m2"})

        result = memory.search("What OpenClaw adapter requirements mention install and beginner friendly errors?", scope, {"mode": "fast", "limit": 5})

        text = " ".join(candidate.text for candidate in result.candidates)
        self.assertIn("one command", text)
        self.assertIn("beginner friendly", text)
```

- [ ] **Step 4: Run red tests**

Run:

```bash
python3 -m unittest tests.test_retrieval_preservation.RetrievalRegressionFixtureTests -v
```

Expected: At least one test fails on current behavior.

- [ ] **Step 5: Implement minimal improvements**

Implement only what is needed for these fixtures:

- In `fusion_memory/core/text.py`, ensure Chinese tokenization emits character bigrams and meaningful mixed alphanumeric tokens.
- In raw/exact candidate creation, when query and candidate share an exact CJK substring of length >= 2, add metadata:

```python
"must_preserve_reason": ["language_exact_match"],
"language_match": "exact"
```

- In current-view candidate creation, mark:

```python
"must_preserve_reason": ["current_value"],
"evidence_role": "answer"
```

- In broad raw or topic scoped recall, add simple condition matching for query tokens `install`, `beginner`, `error`, `OpenClaw` and store `matched_conditions`.

Do not add domain-specific scoring constants beyond the fixture terms; if terms are domain labels, keep them as test data and use generic token matching logic.

- [ ] **Step 6: Run green tests**

Run:

```bash
python3 -m unittest tests.test_retrieval_preservation tests.test_fusion_memory tests.test_temporal_normalizer -v
python3 -m py_compile fusion_memory/core/text.py fusion_memory/api/service_helpers.py fusion_memory/api/service.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add fusion_memory/core/text.py fusion_memory/api/service_helpers.py fusion_memory/api/service.py tests/test_retrieval_preservation.py
git commit -m "fix: preserve chinese current and multi condition evidence"
```

---

### Task 8: Taxonomy Configuration And Rule Quarantine

**Files:**
- Create: `fusion_memory/retrieval/taxonomy.py`
- Create: `fusion_memory/config/default_taxonomy.json`
- Modify: `fusion_memory/retrieval/event_graph_selection.py`
- Modify: `fusion_memory/retrieval/event_ordering_pack.py`
- Test: `tests/test_chronology_selector.py`

**Interfaces:**
- Produces:
  - `TaxonomyEntry(label: str, aliases: list[str], tags: list[str], language: str = "unknown")`
  - `load_default_taxonomy() -> list[TaxonomyEntry]`
  - `taxonomy_alias_hits(text: str, entries: list[TaxonomyEntry] | None = None) -> set[str]`

- [ ] **Step 1: Write failing taxonomy test**

Append to `tests/test_chronology_selector.py`:

```python
from fusion_memory.retrieval.taxonomy import load_default_taxonomy, taxonomy_alias_hits


class TaxonomyTests(unittest.TestCase):
    def test_default_taxonomy_matches_aliases_without_private_regex_branches(self) -> None:
        entries = load_default_taxonomy()
        hits = taxonomy_alias_hits("I deployed the Flask app on Render with CRUD endpoints.", entries)

        self.assertIn("flask", hits)
        self.assertIn("render", hits)
        self.assertIn("crud", hits)
```

- [ ] **Step 2: Run red test**

Run:

```bash
python3 -m unittest tests.test_chronology_selector.TaxonomyTests -v
```

Expected: FAIL because taxonomy module does not exist.

- [ ] **Step 3: Add taxonomy config and loader**

Create `fusion_memory/config/default_taxonomy.json`:

```json
[
  {"label": "flask", "aliases": ["Flask", "Flask app"], "tags": ["framework"], "language": "en"},
  {"label": "render", "aliases": ["Render", "Render.com"], "tags": ["deployment"], "language": "en"},
  {"label": "crud", "aliases": ["CRUD", "create read update delete"], "tags": ["software"], "language": "en"},
  {"label": "postgresql", "aliases": ["PostgreSQL", "Postgres"], "tags": ["database"], "language": "en"},
  {"label": "qwen", "aliases": ["qwen", "qwen0.6B", "Qwen3"], "tags": ["model"], "language": "mixed"},
  {"label": "memory system", "aliases": ["记忆系统", "memory system"], "tags": ["memory"], "language": "mixed"}
]
```

Create `fusion_memory/retrieval/taxonomy.py` to load this JSON using `Path(__file__).resolve().parents[1] / "config" / "default_taxonomy.json"`.

- [ ] **Step 4: Quarantine direct domain regex usage**

In event-ordering graph/pack modules, replace new or obvious local domain-label matching with calls to `taxonomy_alias_hits`. Do not delete old legacy rules in this task. Add comments marking remaining project-specific helpers as legacy fallback until replay gate passes:

```python
# Legacy fallback: domain-specific event ordering rescue. Do not extend; migrate to taxonomy after graph parity.
```

- [ ] **Step 5: Run green tests**

Run:

```bash
python3 -m unittest tests.test_chronology_selector tests.test_model_adapters -v
python3 -m py_compile fusion_memory/retrieval/taxonomy.py fusion_memory/retrieval/event_graph_selection.py fusion_memory/retrieval/event_ordering_pack.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add fusion_memory/retrieval/taxonomy.py fusion_memory/config/default_taxonomy.json fusion_memory/retrieval/event_graph_selection.py fusion_memory/retrieval/event_ordering_pack.py tests/test_chronology_selector.py
git commit -m "feat: add taxonomy config for event ordering labels"
```

---

## Final Verification

After all tasks:

- [ ] Run focused graph/retrieval suite:

```bash
python3 -m unittest \
  tests.test_chronology_models_and_storage \
  tests.test_chronology_normalizer \
  tests.test_chronology_selector \
  tests.test_retrieval_preservation \
  tests.test_beam_event_ordering_replay \
  tests.test_event_ordering_graph \
  tests.test_fusion_memory \
  tests.test_model_adapters \
  tests.test_temporal_normalizer \
  -v
```

- [ ] Run compile check:

```bash
python3 -m py_compile \
  fusion_memory/core/chronology.py \
  fusion_memory/retrieval/chronology_normalizer.py \
  fusion_memory/retrieval/chronology_selector.py \
  fusion_memory/retrieval/preservation.py \
  fusion_memory/retrieval/taxonomy.py \
  fusion_memory/storage/sqlite_store.py \
  fusion_memory/storage/postgres_store.py \
  fusion_memory/api/service.py \
  tools/beam_event_ordering_replay.py
```

- [ ] Run diff check:

```bash
git diff --check
```

- [ ] Run BEAM event-ordering replay when local Postgres workspace is available:

```bash
.runtime/beam-venv/bin/python tools/beam_event_ordering_replay.py \
  --workspace beam_100k_rule_qwenembed_sessionized_20260612_1745 \
  --split 100k \
  --dataset /public/home/wwb/datasets/BEAM \
  --db postgresql://fusion:fusion@127.0.0.1:55433/fusion_memory \
  --gate \
  --output .runtime/beam-runs/event_ordering_graph_vs_legacy_after_persisted_graph.json
```

Expected at this stage: the gate may still fail until graph quality improves, but output must include graph/legacy/hybrid metrics and explicit gate failures. Do not prune legacy rules until the gate passes.
