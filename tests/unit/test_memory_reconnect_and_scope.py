"""
功能测试: 验证记忆系统修复

覆盖:
1. _ensure_conn() 自动重连机制
2. _reconnect() 正确关闭损坏连接后重建
3. legacy_quarantine 在 scope=None 时被排除
4. scope=None 跨 scope 查询功能
5. builder.py scope=None 能检索到 scope="user" 的记忆
"""

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from openakita.memory.storage import MemoryStorage, get_shared_storage


@pytest.fixture
def storage(tmp_path):
    db_path = tmp_path / "test.db"
    s = MemoryStorage(db_path)
    yield s
    s.close()


@pytest.fixture
def populated_storage(storage):
    now = datetime.now().isoformat()
    memories = [
        {
            "id": "mem-user-1",
            "content": "user scoped memory",
            "type": "FACT",
            "scope": "user",
            "scope_owner": "",
            "user_id": "desktop_user",
            "workspace_id": "default",
            "importance_score": 0.8,
            "created_at": now,
        },
        {
            "id": "mem-system-1",
            "content": "system scoped memory",
            "type": "FACT",
            "scope": "system",
            "scope_owner": "",
            "user_id": "system",
            "workspace_id": "default",
            "importance_score": 0.7,
            "created_at": now,
        },
        {
            "id": "mem-session-1",
            "content": "session scoped memory",
            "type": "experience",
            "scope": "session",
            "scope_owner": "session-abc",
            "user_id": "desktop_user",
            "workspace_id": "default",
            "importance_score": 0.9,
            "created_at": now,
        },
        {
            "id": "mem-quarantine-1",
            "content": "quarantined legacy memory",
            "type": "FACT",
            "scope": "legacy_quarantine",
            "scope_owner": "",
            "user_id": "legacy",
            "workspace_id": "default",
            "importance_score": 0.5,
            "created_at": now,
        },
        {
            "id": "mem-rule-1",
            "content": "always use type hints",
            "type": "rule",
            "scope": "user",
            "scope_owner": "",
            "user_id": "desktop_user",
            "workspace_id": "default",
            "importance_score": 0.9,
            "created_at": now,
        },
    ]
    for m in memories:
        storage.save_memory(m)
    return storage


class TestEnsureConnAutoReconnect:
    def test_reconnects_when_conn_is_none(self, storage):
        storage.save_memory(
            {"id": "test-1", "content": "hello", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        storage._conn.close()
        storage._conn = None
        storage._closed_intentionally = False

        results = storage.load_all()
        assert len(results) == 1
        assert results[0]["content"] == "hello"

    def test_reconnects_when_conn_is_stale(self, storage):
        storage.save_memory(
            {"id": "test-2", "content": "world", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        storage._conn.close()
        storage._last_probe_ts = 0

        results = storage.load_all()
        assert len(results) == 1
        assert results[0]["content"] == "world"

    def test_no_reconnect_when_closed_intentionally(self, storage):
        storage.save_memory(
            {"id": "test-3", "content": "data", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        storage.close()

        results = storage.load_all()
        assert results == []

    def test_probe_interval_skips_redundant_checks(self, storage):
        storage.save_memory(
            {"id": "test-4", "content": "fast", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        storage._last_probe_ts = time.monotonic()

        assert storage._ensure_conn() is True
        results = storage.load_all()
        assert len(results) == 1


class TestReconnectClosesBrokenConnection:
    def test_reconnect_replaces_broken_conn(self, storage):
        storage.save_memory(
            {"id": "rc-1", "content": "reconnect test", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        old_conn = storage._conn
        old_conn.close()
        storage._last_probe_ts = time.monotonic() + 999

        result = storage._reconnect()
        assert result is True
        assert storage._conn is not old_conn
        assert storage._conn is not None

        results = storage.load_all()
        assert len(results) == 1

    def test_retry_in_load_all_reconnects(self, storage):
        storage.save_memory(
            {"id": "rc-2", "content": "retry data", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        storage._conn.close()
        storage._last_probe_ts = time.monotonic() + 999

        results = storage.load_all()
        assert len(results) == 1
        assert results[0]["content"] == "retry data"

    def test_retry_in_query_paged_reconnects(self, storage):
        storage.save_memory(
            {"id": "rc-3", "content": "paged data", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        storage._conn.close()
        storage._last_probe_ts = time.monotonic() + 999

        results, total = storage.query_paged()
        assert total == 1
        assert results[0]["content"] == "paged data"


class TestLegacyQuarantineExclusion:
    def test_load_all_excludes_quarantine_when_scope_none(self, populated_storage):
        results = populated_storage.load_all(scope=None)
        ids = {r["id"] for r in results}
        assert "mem-quarantine-1" not in ids
        assert "mem-user-1" in ids
        assert "mem-system-1" in ids
        assert "mem-session-1" in ids

    def test_load_all_includes_quarantine_when_scope_explicit(self, populated_storage):
        results = populated_storage.load_all(scope="legacy_quarantine")
        ids = {r["id"] for r in results}
        assert "mem-quarantine-1" in ids

    def test_query_paged_excludes_quarantine_when_scope_none(self, populated_storage):
        results, total = populated_storage.query_paged(scope=None)
        ids = {r["id"] for r in results}
        assert "mem-quarantine-1" not in ids
        assert total == 4

    def test_query_excludes_quarantine_when_scope_none(self, populated_storage):
        results = populated_storage.query(scope=None)
        ids = {r["id"] for r in results}
        assert "mem-quarantine-1" not in ids
        assert len(results) == 4

    def test_search_fts_excludes_quarantine_when_scope_none(self, populated_storage):
        results = populated_storage.search_fts("memory", scope=None)
        ids = {r["id"] for r in results}
        assert "mem-quarantine-1" not in ids


class TestScopeNoneUnfilteredQueries:
    def test_load_all_returns_all_scopes(self, populated_storage):
        results = populated_storage.load_all(scope=None)
        scopes = {r.get("scope") for r in results}
        assert "user" in scopes
        assert "system" in scopes
        assert "session" in scopes
        assert "legacy_quarantine" not in scopes

    def test_query_paged_no_user_filter(self, populated_storage):
        results, total = populated_storage.query_paged(scope=None, user_id=None, workspace_id=None)
        assert total == 4
        user_ids = {r.get("user_id") for r in results}
        assert "desktop_user" in user_ids
        assert "system" in user_ids

    def test_query_returns_rules_with_scope_none(self, populated_storage):
        results = populated_storage.query(memory_type="rule", scope=None)
        assert len(results) == 1
        assert results[0]["content"] == "always use type hints"

    def test_query_returns_experience_with_scope_none(self, populated_storage):
        results = populated_storage.query(memory_type="experience", scope=None)
        assert len(results) == 1
        assert results[0]["content"] == "session scoped memory"


class TestThreadSafety:
    def test_concurrent_ensure_conn_no_leak(self, tmp_path):
        db_path = tmp_path / "concurrent.db"
        storage = MemoryStorage(db_path)
        storage.save_memory(
            {"id": "t-1", "content": "thread test", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        storage._conn.close()
        storage._conn = None
        storage._closed_intentionally = False

        results = []
        errors = []

        def worker():
            try:
                r = storage.load_all()
                results.append(len(r))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors: {errors}"
        assert all(r == 1 for r in results), f"Results: {results}"
        storage.close()


class TestGetSharedStorageReconnect:
    def test_shared_storage_returns_new_after_close(self, tmp_path):
        db_path = tmp_path / "shared.db"
        s1 = get_shared_storage(db_path)
        s1.save_memory(
            {"id": "s-1", "content": "shared", "type": "FACT", "created_at": datetime.now().isoformat()}
        )
        s1.close()

        s2 = get_shared_storage(db_path)
        assert s2 is not s1
        results = s2.load_all()
        assert len(results) == 1
        s2.close()
