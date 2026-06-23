"""Targeted functional tests for the memory-system review fixes.

Each test maps to a specific bug fixed in the memory subsystem review:

* cleanup_expired data-loss guard (cited / important / permanent survive TTL)
* get_expired_memory_ids ↔ cleanup_expired consistency
* episode dedup (no duplicate accumulation on interrupted consolidation)
* query() ``updated_after`` time-window (powers a real "recent" search)
* _get_schema_version graceful handling of a corrupt/missing version row
* relational graph tenant isolation on read paths (privacy)
* hybrid retrieval score normalization (RRF vs cosine scale mixing)
* content-aware (subject, predicate) dedup sweep (no silent data loss)

Run:  pytest tests/test_memory_fixes.py -v
"""

from __future__ import annotations

import sqlite3
import types
from datetime import datetime, timedelta

import pytest

from openakita.memory.storage import MemoryStorage


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _mk_storage(tmp_path) -> MemoryStorage:
    # _register=False bypasses the process-level singleton so each test is isolated.
    return MemoryStorage(tmp_path / "mem.db", _register=False)


def _save_mem(st: MemoryStorage, mid: str, **over) -> None:
    base = {
        "id": mid,
        "content": over.pop("content", f"content-{mid}"),
        "type": "fact",
        "priority": over.pop("priority", "long_term"),
        "importance_score": over.pop("importance_score", 0.5),
        "access_count": over.pop("access_count", 0),
        "scope": "global",
        "scope_owner": "",
        "user_id": "default",
        "workspace_id": "default",
    }
    base.update(over)
    st.save_memory(base)


# --------------------------------------------------------------------------- #
# DATA-LOSS: cleanup_expired must protect cited / important / permanent rows
# --------------------------------------------------------------------------- #
def test_cleanup_expired_protects_valuable_memories(tmp_path):
    st = _mk_storage(tmp_path)
    past = (datetime.now() - timedelta(days=1)).isoformat()

    _save_mem(st, "plain", expires_at=past, access_count=0, importance_score=0.5)
    _save_mem(st, "cited", expires_at=past, access_count=5, importance_score=0.5)
    _save_mem(st, "important", expires_at=past, access_count=0, importance_score=0.9)
    _save_mem(st, "perm", expires_at=past, access_count=0, importance_score=0.5,
              priority="permanent")
    _save_mem(st, "fresh", expires_at=None)  # not expired at all

    # get_expired_memory_ids must list ONLY the row cleanup will actually delete
    expired = set(st.get_expired_memory_ids())
    assert expired == {"plain"}, f"expected only 'plain' deletable, got {expired}"

    deleted = st.cleanup_expired()
    assert deleted == 1

    assert st.get_memory("plain") is None
    for survivor in ("cited", "important", "perm", "fresh"):
        assert st.get_memory(survivor) is not None, f"{survivor} was wrongly deleted"
    st.close()


# --------------------------------------------------------------------------- #
# DATA-LOSS: interrupted consolidation must not accumulate duplicate episodes
# --------------------------------------------------------------------------- #
def test_episode_dedup_same_session_started_at(tmp_path):
    st = _mk_storage(tmp_path)
    started = "2026-06-23T10:00:00"
    st.save_episode({"id": "e1", "session_id": "s1", "started_at": started, "summary": "first"})
    # Re-run with a *fresh* id for the same turns (the interruption scenario):
    st.save_episode({"id": "e2", "session_id": "s1", "started_at": started, "summary": "second"})

    rows = [r["id"] for r in st.search_episodes(session_id="s1", limit=50)]
    assert rows == ["e1"], f"duplicate episodes accumulated: {rows}"
    ep = st.get_episode("e1")
    assert ep is not None and ep["summary"] == "second", "episode should be updated in place"
    st.close()


# --------------------------------------------------------------------------- #
# RETRIEVAL: _search_recent is now backed by a real time-window in query()
# --------------------------------------------------------------------------- #
def test_query_updated_after_time_window(tmp_path):
    st = _mk_storage(tmp_path)
    _save_mem(st, "old")
    _save_mem(st, "new")
    # Push "old" back in time (save_memory always stamps updated_at=now).
    old_ts = (datetime.now() - timedelta(days=30)).isoformat()
    st._conn.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (old_ts, "old"))
    st._conn.commit()

    since = (datetime.now() - timedelta(days=3)).isoformat()
    recent_ids = {r["id"] for r in st.query(updated_after=since)}
    assert recent_ids == {"new"}, f"time-window failed: {recent_ids}"
    # Without the window both are returned (regression guard).
    all_ids = {r["id"] for r in st.query()}
    assert {"old", "new"} <= all_ids
    st.close()


# --------------------------------------------------------------------------- #
# MIGRATION: _get_schema_version handles a corrupt/missing version row safely
# --------------------------------------------------------------------------- #
def test_schema_version_handles_corrupt_value(tmp_path):
    st = _mk_storage(tmp_path)
    assert st._get_schema_version() == 4  # freshly migrated

    st._conn.execute(
        "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('version', 'not-an-int')"
    )
    st._conn.commit()
    # Corrupt value must degrade to 0, not raise.
    assert st._get_schema_version() == 0

    st._conn.execute("DELETE FROM _schema_meta WHERE key = 'version'")
    st._conn.commit()
    assert st._get_schema_version() == 0  # no row → fresh
    st.close()


# --------------------------------------------------------------------------- #
# PRIVACY: relational graph reads must be isolated by tenant
# --------------------------------------------------------------------------- #
def test_relational_tenant_isolation():
    from openakita.memory.relational.store import RelationalMemoryStore
    from openakita.memory.relational.types import EntityRef, MemoryNode

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    rs = RelationalMemoryStore(conn)

    a = MemoryNode(content="alpha shared topic", user_id="alice",
                   entities=[EntityRef(name="topic")])
    b = MemoryNode(content="alpha shared topic", user_id="bob",
                   entities=[EntityRef(name="topic")])
    rs.save_node(a)
    rs.save_node(b)

    # LIKE search scoped to alice → only alice's node
    ids = {n.id for n in rs.search_like("alpha", user_id="alice")}
    assert ids == {a.id}, f"search_like leaked across tenants: {ids}"

    # entity search scoped to bob → only bob's node
    ids = {n.id for n in rs.search_by_entity("topic", user_id="bob")}
    assert ids == {b.id}, f"search_by_entity leaked across tenants: {ids}"

    # no owner → backward-compatible, returns both
    ids = {n.id for n in rs.search_like("alpha")}
    assert ids == {a.id, b.id}
    rs.close()
    conn.close()


# --------------------------------------------------------------------------- #
# RETRIEVAL: heterogeneous backend scores are normalized to a common [0,1]
# --------------------------------------------------------------------------- #
class _FakeBackend:
    """Minimal SearchBackend that returns tiny RRF-like scores (the bug case)."""

    backend_type = "lancedb"
    available = True
    episodes_available = False

    def __init__(self, results):
        self._results = results

    def count(self):
        return 10_000  # large → skip backfill in UnifiedStore.__init__

    def search(self, query, limit=10, filter_type=None, scope=None, scope_owner=None,
               user_id=None, workspace_id=None, hybrid=False):
        return list(self._results)

    # Unused in this test but part of the surface:
    def add(self, *a, **k):
        return True

    def delete(self, *a, **k):
        return True

    def close(self):
        pass


def test_hybrid_score_normalization(tmp_path):
    from openakita.memory.unified_store import UnifiedStore

    fake = _FakeBackend([("m1", 0.020), ("m2", 0.015), ("m3", 0.010)])
    store = UnifiedStore(tmp_path / "u.db", search_backend=fake)
    for mid in ("m1", "m2", "m3"):
        _save_mem(store.db, mid)

    scored = store.search_semantic_scored(
        "anything", scope="global", scope_owner="", user_id="default", workspace_id="default"
    )
    by_id = {m.id: s for m, s in scored}
    assert set(by_id) == {"m1", "m2", "m3"}
    # Min-max normalized: best → 1.0, worst → 0.0 (was ~0.02 / ~0.01 before the fix).
    assert by_id["m1"] == pytest.approx(1.0)
    assert by_id["m3"] == pytest.approx(0.0)
    assert by_id["m2"] == pytest.approx(0.5, abs=1e-6)
    store.close()


# --------------------------------------------------------------------------- #
# DATA-LOSS: (subject, predicate) sweep must be content-aware
# --------------------------------------------------------------------------- #
def test_subject_predicate_sweep_is_content_aware():
    from openakita.memory.lifecycle import LifecycleManager

    def _mem(mid, content):
        return types.SimpleNamespace(
            id=mid, subject="用户", predicate="偏好",
            content=content, importance_score=0.5, access_count=0,
        )

    class _Store:
        def __init__(self, mems):
            self._mems = list(mems)
            self.deleted = []

        def load_all_memories(self, **kw):
            return list(self._mems)

        def delete_semantic(self, mid):
            self.deleted.append(mid)
            self._mems = [m for m in self._mems if m.id != mid]
            return True

    # Two distinct preferences sharing the coarse (subject, predicate) key,
    # plus one exact duplicate of the first.
    store = _Store([
        _mem("p1", "喜欢深色主题"),
        _mem("p2", "喜欢中文回复"),
        _mem("dup", "喜欢深色主题"),
    ])
    stub = types.SimpleNamespace(store=store)
    result = LifecycleManager._cleanup_subject_predicate_duplicates(stub)

    # Only the exact-duplicate is removed; the two distinct prefs survive.
    assert result["deleted"] == 1
    assert store.deleted == ["dup"]
    survivors = {m.id for m in store._mems}
    assert survivors == {"p1", "p2"}, f"distinct preferences lost: {survivors}"
