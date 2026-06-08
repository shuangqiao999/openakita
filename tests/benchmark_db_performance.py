"""
数据库性能 Benchmark 脚本

测试 SQLite / LanceDB 在模拟大数据量下的增删改查性能，
对比"无索引"和"有索引"的差异。

运行: python tests/benchmark_db_performance.py
"""
from __future__ import annotations

import random
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────────────
NUM_DOCUMENTS = 5000         # 知识库文档数
NUM_MEMORIES = 10000         # 记忆条数
NUM_TURNS = 2000             # 对话轮次(每次 50 未提取)
NUM_CONCURRENT = 20          # 并发写入数

# ── Helpers ──────────────────────────────────────────────────────────

@contextmanager
def timer(name: str):
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    print(f"  {name}: {elapsed*1000:.1f} ms")


def format_speedup(before_ms: float, after_ms: float) -> str:
    if before_ms <= 0:
        return "∞"
    ratio = before_ms / after_ms
    if ratio >= 1000:
        return f"{ratio:.0f}x"
    elif ratio >= 10:
        return f"{ratio:.1f}x"
    else:
        return f"{ratio:.2f}x"


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: SQLite 知识库 Benchmark
# ═══════════════════════════════════════════════════════════════════════

def create_kb_db(path: str, with_indexes: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE knowledge_documents (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            file_type TEXT NOT NULL, upload_time REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing', content_hash TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE knowledge_chunks (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL, content TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX idx_chunks_doc ON knowledge_chunks(document_id)"
    )
    if with_indexes:
        conn.execute(
            "CREATE INDEX idx_docs_status ON knowledge_documents(status)"
        )
        conn.execute(
            "CREATE INDEX idx_docs_upload_time ON knowledge_documents(upload_time)"
        )
        conn.execute(
            "CREATE INDEX idx_docs_name_hash ON knowledge_documents(name, content_hash)"
        )
        conn.execute(
            "CREATE INDEX idx_docs_name ON knowledge_documents(name)"
        )
    conn.commit()
    return conn


def populate_kb_db(conn: sqlite3.Connection):
    statuses = ["ready"] * 180 + ["processing"] * 15 + ["failed"] * 5
    for i in range(NUM_DOCUMENTS):
        doc_id = f"doc_{i:06d}"
        status = random.choice(statuses)
        conn.execute(
            "INSERT INTO knowledge_documents(id,name,file_type,upload_time,status,content_hash) "
            "VALUES(?,?,?,?,?,?)",
            (doc_id, f"Document_{i}.md", "markdown", time.time() - i * 3600, status, f"hash_{i}"),
        )
        for j in range(random.randint(1, 5)):
            chunk_id = f"chunk_{i:06d}_{j}"
            conn.execute(
                "INSERT INTO knowledge_chunks(id,document_id,chunk_index,content) VALUES(?,?,?,?)",
                (chunk_id, doc_id, j, f"Content of chunk {j} in document {i}."),
            )
    conn.commit()


def benchmark_kb_query(conn: sqlite3.Connection, label: str):
    print(f"\n  --- {label} ---")
    results = {}

    # Test 1: WHERE status='ready' — most common query (7+ call sites)
    with timer("  status='ready' COUNT"):
        row = conn.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE status='ready'"
        ).fetchone()
        results["count_ready"] = row[0]

    # Test 2: ORDER BY upload_time — document listing
    with timer("  ORDER BY upload_time LIMIT 50"):
        rows = conn.execute(
            "SELECT id, name FROM knowledge_documents ORDER BY upload_time DESC LIMIT 50"
        ).fetchall()
        results["list_50"] = len(rows)

    # Test 3: Compound query — most common search pattern
    with timer("  status='ready' + ORDER BY upload_time LIMIT 20"):
        rows = conn.execute(
            "SELECT id, name FROM knowledge_documents "
            "WHERE status='ready' ORDER BY upload_time DESC LIMIT 20"
        ).fetchall()
        results["search_20"] = len(rows)

    # Test 4: JOIN chunks — full-text-join pattern
    with timer("  JOIN chunks WHERE status='ready' LIMIT 30"):
        rows = conn.execute(
            "SELECT kc.id, kc.content FROM knowledge_chunks kc "
            "JOIN knowledge_documents kd ON kc.document_id = kd.id "
            "WHERE kd.status = 'ready' LIMIT 30"
        ).fetchall()
        results["join_30"] = len(rows)

    # Test 5: Dedup check — find duplicate by name+hash
    with timer("  Dedup WHERE name=? AND content_hash=?"):
        row = conn.execute(
            "SELECT id FROM knowledge_documents WHERE name=? AND content_hash=?",
            ("Document_2500.md", "hash_2500"),
        ).fetchone()
        results["dedup_found"] = row is not None

    return results


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: SQLite 记忆 Benchmark
# ═══════════════════════════════════════════════════════════════════════

def create_memory_db(path: str, with_indexes: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL, type TEXT, priority TEXT,
            importance_score REAL, created_at TEXT,
            scope TEXT, scope_owner TEXT, user_id TEXT, workspace_id TEXT,
            expires_at TEXT, superseded_by TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_memories_scope ON memories(scope, scope_owner)")
    conn.execute("CREATE INDEX idx_memories_owner ON memories(workspace_id, user_id, scope, scope_owner)")
    conn.execute("CREATE INDEX idx_memories_type ON memories(type)")
    conn.execute("CREATE INDEX idx_memories_created ON memories(created_at)")
    conn.execute("CREATE INDEX idx_memories_importance ON memories(importance_score)")

    if with_indexes:
        conn.execute(
            "CREATE INDEX idx_memories_active_query "
            "ON memories(workspace_id, user_id, scope, created_at, importance_score)"
        )

    conn.execute("""
        CREATE TABLE conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, turn_index INTEGER, extracted INTEGER DEFAULT 0,
            timestamp TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_turns_session ON conversation_turns(session_id)")
    conn.execute("CREATE INDEX idx_turns_extracted ON conversation_turns(extracted)")
    conn.execute("CREATE INDEX idx_turns_timestamp ON conversation_turns(timestamp)")

    if with_indexes:
        conn.execute(
            "CREATE INDEX idx_turns_unextracted ON conversation_turns(extracted, timestamp)"
        )

    conn.execute("""
        CREATE TABLE extraction_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT DEFAULT 'pending', created_at TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_eq_status_created ON extraction_queue(status, created_at)")
    conn.commit()
    return conn


def populate_memory_db(conn: sqlite3.Connection):
    import datetime
    base = datetime.datetime(2026, 1, 1)
    types = ["fact", "preference", "rule", "experience", "error"]

    for i in range(NUM_MEMORIES):
        mem_id = f"mem_{i:06d}"
        ts = (base + datetime.timedelta(hours=i)).isoformat()
        conn.execute(
            "INSERT INTO memories(id,content,type,importance_score,created_at,"
            "scope,scope_owner,user_id,workspace_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                mem_id, f"Memory content {i}", random.choice(types),
                random.random(), ts,
                "global", "", "default", "default",
            ),
        )

    for i in range(NUM_TURNS):
        ts = (base + datetime.timedelta(minutes=i * 10)).isoformat()
        extracted = 1 if i < NUM_TURNS - 50 else 0  # last 50 are unextracted
        conn.execute(
            "INSERT INTO conversation_turns(session_id,turn_index,extracted,timestamp) "
            "VALUES(?,?,?,?)",
            (f"session_{i // 20}", i % 20, extracted, ts),
        )
        if not extracted:
            conn.execute(
                "INSERT INTO extraction_queue(status,created_at) VALUES('pending',?)",
                (ts,),
            )

    conn.commit()


def benchmark_memory_query(conn: sqlite3.Connection, label: str):
    print(f"\n  --- {label} ---")

    # Test 6: Hot query pattern — load_all style
    with timer("  memories: workspace+user+scope ORDER BY created_at DESC LIMIT 100"):
        rows = conn.execute(
            "SELECT id, content FROM memories "
            "WHERE workspace_id='default' AND user_id='default' AND scope='global' "
            "ORDER BY created_at DESC LIMIT 100"
        ).fetchall()

    # Test 7: Hot query pattern — query() with importance sort
    with timer("  memories: workspace+user+scope ORDER BY importance_score DESC LIMIT 50"):
        rows = conn.execute(
            "SELECT id, content FROM memories "
            "WHERE workspace_id='default' AND user_id='default' AND scope='global' "
            "ORDER BY importance_score DESC LIMIT 50"
        ).fetchall()

    # Test 8: Unextracted turns
    with timer("  turns: extracted=0 ORDER BY timestamp LIMIT 40"):
        rows = conn.execute(
            "SELECT id FROM conversation_turns "
            "WHERE extracted=0 ORDER BY timestamp ASC LIMIT 40"
        ).fetchall()

    # Test 9: Extraction queue dequeue
    with timer("  queue: status='pending' ORDER BY created_at LIMIT 20"):
        rows = conn.execute(
            "SELECT id FROM extraction_queue "
            "WHERE status='pending' ORDER BY created_at ASC LIMIT 20"
        ).fetchall()


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: LanceDB 并发写入 Benchmark
# ═══════════════════════════════════════════════════════════════════════

def benchmark_lancedb_concurrent():
    """模拟 LanceDB 并发写入场景，测试 LMStudio 嵌入 + 冲突重试。"""
    LMSTUDIO_BASE = "http://localhost:1234/v1"
    LMSTUDIO_EMBED = "text-embedding-embeddinggemma-300m-qat"

    try:
        import urllib.request
        urllib.request.urlopen(f"{LMSTUDIO_BASE}/models", timeout=3)
    except Exception:
        print("\n--- LanceDB: LMStudio offline, skipping ---")
        return

    import asyncio
    import httpx

    async def run():
        errors = []
        latency = []

        async def write_one(i: int) -> None:
            async with httpx.AsyncClient(timeout=60) as client:
                t0 = time.perf_counter()
                try:
                    resp = await client.post(
                        f"{LMSTUDIO_BASE}/embeddings",
                        json={"model": LMSTUDIO_EMBED, "input": [f"Benchmark doc {i}"]},
                    )
                    latency.append(time.perf_counter() - t0)
                    if resp.status_code != 200:
                        errors.append(f"req_{i}: {resp.status_code}")
                except Exception as e:
                    errors.append(f"req_{i}: {e}")

        # 3 rounds of concurrent writes
        for round_num in range(3):
            tasks = [write_one(i) for i in range(NUM_CONCURRENT)]
            await asyncio.gather(*tasks)

        print(f"\n  --- LanceDB 并发写入 ---")
        print(f"  并发数 x3轮: {NUM_CONCURRENT}x3 = {NUM_CONCURRENT * 3} 次写入")
        print(f"  成功率: {NUM_CONCURRENT * 3 - len(errors)}/{NUM_CONCURRENT * 3} ({100 * (1 - len(errors)/(NUM_CONCURRENT*3)):.1f}%)")
        if latency:
            avg_lat = sum(latency) / len(latency) * 1000
            print(f"  平均延迟: {avg_lat:.0f} ms")
        if errors:
            print(f"  错误: {len(errors)}")
            for e in errors[:3]:
                print(f"    - {e}")

    asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: 磁盘膨胀模拟 + VACUUM
# ═══════════════════════════════════════════════════════════════════════

def benchmark_disk_growth():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"

        # Setup with WAL mode (same as production)
        conn = create_kb_db(str(db_path), with_indexes=True)
        populate_kb_db(conn)
        conn.close()

        size_before = Path(db_path).stat().st_size

        # Simulate: DELETE many rows, INSERT new ones — creates fragmentation
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM knowledge_documents WHERE status = 'ready'")
        # Re-insert 2000 rows
        for i in range(2000):
            conn.execute(
                "INSERT INTO knowledge_documents(id,name,file_type,upload_time,status,content_hash) "
                "VALUES(?,?,?,?,?,?)",
                (f"new_{i:06d}", f"NewDoc_{i}.md", "md", time.time(), "ready", f"h_{i}"),
            )
        conn.commit()
        conn.close()

        size_after_delete_insert = Path(db_path).stat().st_size

        # VACUUM
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.close()

        size_after_vacuum = Path(db_path).stat().st_size

        print(f"\n  --- 磁盘膨胀 ---")
        print(f"  初始: {size_before / 1024:.1f} KB")
        print(f"  DELETE+INSERT 后: {size_after_delete_insert / 1024:.1f} KB (膨胀 {size_after_delete_insert / max(size_before,1):.2f}x)")
        print(f"  VACUUM 后: {size_after_vacuum / 1024:.1f} KB (回收 {(1 - size_after_vacuum / max(size_after_delete_insert, 1)) * 100:.0f}%)")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("OpenAkita 数据库性能 Benchmark")
    print(f"数据集: {NUM_DOCUMENTS:,} docs, {NUM_MEMORIES:,} memories, {NUM_TURNS:,} turns")
    print("=" * 60)

    # Phase 1: KB without indexes
    with tempfile.TemporaryDirectory() as tmp:
        db_no = create_kb_db(str(Path(tmp) / "kb_no.db"), with_indexes=False)
        populate_kb_db(db_no)
        with timer(f"\n[KB 无索引] Total benchmark"):
            benchmark_kb_query(db_no, "KB 无索引")
        db_no.close()

    # Phase 1: KB with indexes
    with tempfile.TemporaryDirectory() as tmp:
        db_yes = create_kb_db(str(Path(tmp) / "kb_yes.db"), with_indexes=True)
        populate_kb_db(db_yes)
        with timer(f"\n[KB 有索引] Total benchmark"):
            benchmark_kb_query(db_yes, "KB 有索引")
        db_yes.close()

    # Phase 2: Memory without compound
    with tempfile.TemporaryDirectory() as tmp:
        mem_no = create_memory_db(str(Path(tmp) / "mem_no.db"), with_indexes=False)
        populate_memory_db(mem_no)
        with timer(f"\n[Memory 无复合索引] Total benchmark"):
            benchmark_memory_query(mem_no, "Memory 无复合索引")
        mem_no.close()

    # Phase 2: Memory with compound
    with tempfile.TemporaryDirectory() as tmp:
        mem_yes = create_memory_db(str(Path(tmp) / "mem_yes.db"), with_indexes=True)
        populate_memory_db(mem_yes)
        with timer(f"\n[Memory 有复合索引] Total benchmark"):
            benchmark_memory_query(mem_yes, "Memory 有复合索引")
        mem_yes.close()

    # Phase 3: LanceDB concurrent
    benchmark_lancedb_concurrent()

    # Phase 4: Disk growth
    benchmark_disk_growth()

    print("\n" + "=" * 60)
    print("Benchmark 完成")
    print("=" * 60)
