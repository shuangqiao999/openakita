"""
LanceDB backfill/index functional test — standalone

Uses local LMStudio embedding model:
  - Model: text-embedding-embeddinggemma-300m-qat
  - API:   http://127.0.0.1:1234/v1
  - Dims:  768
"""
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import sys
from pathlib import Path

import requests

LMSTUDIO_BASE = "http://127.0.0.1:1234/v1"
EMBED_MODEL = "text-embedding-embeddinggemma-300m-qat"
EMBED_DIM = 768


def check_lmstudio():
    try:
        r = requests.get(f"{LMSTUDIO_BASE}/models", timeout=5)
        if r.ok:
            return EMBED_MODEL in [m["id"] for m in r.json().get("data", [])]
    except Exception:
        pass
    return False


def setup_embedding_config():
    from openakita.config import settings
    settings.embedding_model = "api"
    settings.embedding_provider = "openai"
    settings.embedding_api_provider = "openai"
    settings.embedding_api_key = "lm-studio-not-needed"
    settings.embedding_api_base = LMSTUDIO_BASE
    settings.embedding_model_name = EMBED_MODEL
    settings.embedding_api_model = EMBED_MODEL
    settings.embedding_device = "cpu"


def init_sqlite_schema(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT OR REPLACE INTO _schema_meta VALUES ('version', '4');
        INSERT OR REPLACE INTO _schema_meta VALUES ('applied_migrations', 'v3,v4,v4.1');

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL, type TEXT DEFAULT 'fact',
            priority TEXT DEFAULT 'long_term', source TEXT DEFAULT '',
            importance_score REAL DEFAULT 0.5, access_count INTEGER DEFAULT 0,
            tags TEXT DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            expires_at TEXT, metadata TEXT DEFAULT '{}', subject TEXT DEFAULT '',
            predicate TEXT DEFAULT '', confidence REAL DEFAULT 0.5, decay_rate REAL DEFAULT 0.1,
            last_accessed_at TEXT, superseded_by TEXT, source_episode_id TEXT,
            scope TEXT DEFAULT 'global', scope_owner TEXT DEFAULT '',
            agent_id TEXT DEFAULT '', user_id TEXT DEFAULT 'default',
            workspace_id TEXT DEFAULT 'default'
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, summary TEXT NOT NULL, goal TEXT DEFAULT '',
            outcome TEXT DEFAULT 'completed', started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
            action_nodes TEXT DEFAULT '[]', entities TEXT DEFAULT '[]',
            tools_used TEXT DEFAULT '[]', linked_memory_ids TEXT DEFAULT '[]',
            tags TEXT DEFAULT '[]', importance_score REAL DEFAULT 0.5,
            access_count INTEGER DEFAULT 0, source TEXT DEFAULT 'session_end'
        );
    """)
    conn.commit()
    conn.close()


def insert_sqlite_memories(db_path: Path, count: int, prefix: str = "sqlite"):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    now = "2026-06-24T10:00:00"
    for i in range(count):
        conn.execute(
            "INSERT OR IGNORE INTO memories (id, content, type, priority, created_at, updated_at, "
            "scope, scope_owner, user_id, workspace_id) VALUES (?, ?, 'fact', 'long_term', ?, ?, "
            "'global', '', 'default', 'default')",
            (f"{prefix}-{i:04d}", f"记忆内容 {prefix}-{i} " * 5, now, now),
        )
    conn.commit()
    conn.close()


def insert_sqlite_episodes(db_path: Path, count: int):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    now = "2026-06-24T10:00:00"
    for i in range(count):
        conn.execute(
            "INSERT OR IGNORE INTO episodes (id, session_id, summary, goal, outcome, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, 'completed', ?, ?)",
            (f"ep-{i:04d}", f"sess-{i:02d}", f"对话摘要 {i}: 测试记忆系统向量化回填", f"目标{i}", now, now),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    print("=== LanceDB Backfill & Index Functional Test (LMStudio) ===\n")

    if not check_lmstudio():
        print(f"[SKIP] LMStudio not available. Load '{EMBED_MODEL}' in LMStudio.")
        sys.exit(0)

    print(f"[OK] LMStudio found: {EMBED_MODEL} ({EMBED_DIM}d)\n")
    setup_embedding_config()

    tmp = Path(tempfile.mkdtemp(prefix="test_lancedb_"))
    stats = [0, 0]

    def chk(name, condition, detail=""):
        if condition:
            stats[0] += 1
            print(f"  [PASS] {name} {detail}")
        else:
            stats[1] += 1
            print(f"  [FAIL] {name} {detail}")

    try:
        db_path = tmp / "openakita.db"
        lancedb_dir = tmp / "lancedb"
        lancedb_dir.mkdir(parents=True, exist_ok=True)
        init_sqlite_schema(db_path)

        from openakita.memory.lancedb_backend import LanceDBBackend

        backend = LanceDBBackend(
            persist_dir=str(lancedb_dir),
            embedding_dim=EMBED_DIM,
        )

        # ── 1. Warmup ──
        print("1. Warmup & table creation...")
        ok = backend.warmup()
        chk("warmup", ok)
        chk("table", backend._table is not None)

        # ── 2. Single add + search ──
        print("2. Single add + search...")
        backend.add("test-1", "OpenAkita是一个多智能体AI助手平台", {"type": "fact", "scope": "global"})
        results = backend.search("多智能体AI助手", limit=3)
        chk("search finds added", any(r[0] == "test-1" for r in results))

        # ── 3. Batch add → flush counter (P3) ──
        print("3. Batch add 120 items (P3: batch-flush per 50)...")
        backend._flush_counter = 0
        items = [{"id": f"b-{i:04d}", "content": f"批量测试{i} " * 5, "metadata": {"type": "fact"}} for i in range(120)]
        n = backend.batch_add(items)
        chk("batch_add count", n == 120, f"got {n}")
        # batch_add calls _flush_table() once → counter += 1
        chk("flush counter >=1", backend._flush_counter >= 1, f"got {backend._flush_counter}")

        # ── 4. Index auto-creation ──
        print("4. Index auto-creation (>256 rows for PQ)...")
        backend._creating_index = False
        items2 = [{"id": f"idx-{i:04d}", "content": f"索引测试{i}" * 5, "metadata": {"type": "fact"}} for i in range(150)]
        backend.batch_add(items2)
        for _ in range(30):
            if backend._index_created:
                break
            time.sleep(1)
        chk("index auto-created", backend._index_created)
        if backend._index_created:
            indices = list(backend._table.list_indices())
            chk("indices exist", len(indices) > 0, f"count={len(indices)}")

        # ── 5. SQLite→LanceDB backfill (P0) ──
        print("5. SQLite→LanceDB backfill (P0: incremental gap-fill)...")
        insert_sqlite_memories(db_path, 60, "be")

        from openakita.memory.unified_store import UnifiedStore
        tag_dir = tmp / "data"
        tag_dir.mkdir(parents=True, exist_ok=True)
        tag_file = tag_dir / ".semantic_backfill_tag"

        store = UnifiedStore(db_path=db_path, search_backend=backend)
        store._needs_full_backfill = True
        store._backfill_started = False
        store._tag_file_to_write = tag_file
        store._backfill_worker()
        count = backend.count()
        chk("backfill count >=60", count >= 60, f"got {count}")
        chk("tag file written", tag_file.exists())

        # ── 6. Index re-tuning (P1) ──
        print("6. Index re-tuning (P1)...")
        backend._creating_index = False
        backend._index_created = True
        t = threading.Thread(target=backend._create_index_sync, daemon=True)
        t.start()
        t.join(timeout=60)
        chk("index after re-tune", backend._index_created)

        # ── 7. Episodes backfill (P3b) ──
        print("7. Episodes backfill (P3b: tag after success)...")
        insert_sqlite_episodes(db_path, 15)

        ep_tag = tag_dir / ".episodes_backfill_tag"
        store._backfill_episodes_worker(tag_file=ep_tag)
        ep_count = backend.episodes_count()
        chk("episodes count >=15", ep_count >= 15, f"got {ep_count}")
        chk("episodes tag exists", ep_tag.exists())

        # ── 8. Health breaker ──
        print("8. Health breaker...")
        breaker = backend._breakers["search"]
        chk("breaker healthy", breaker.is_healthy())

        # ── 9. Close compact ──
        print("9. Close + compact...")
        backend.close()
        chk("table cleared", backend._table is None)
        chk("backend disabled", not backend._enabled)

        # ── Summary ──
        total = stats[0] + stats[1]
        print(f"\n{'='*50}")
        print(f"  Results: {stats[0]}/{total} passed, {stats[1]} failed")
        print(f"{'='*50}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    sys.exit(0 if stats[1] == 0 else 1)
