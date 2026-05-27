"""知识库管理器 — SQLite + LanceDB 双存储

独立于记忆系统，使用独立的 SQLite 数据库 (data/knowledge/knowledge.db)
和独立的 LanceDB 集合 (knowledge_base)。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa

from .chunker import TextChunker
from .extractor import extract_text

logger = logging.getLogger(__name__)

_KB_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
_KB_ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".md", ".txt", ".markdown",
    ".rst", ".org", ".tex", ".html", ".htm", ".csv", ".log",
    ".py", ".pyi", ".pyx", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".scala",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".sql", ".r",
    ".lua", ".dart", ".nim", ".zig", ".ex", ".exs",
    ".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".env", ".properties", ".editorconfig",
}

_KB_EMBED_BATCH_SIZE = 20  # 每批发送给嵌入模型的文本数（本地模型安全上限）
_KB_EMBED_CHUNK_TRUNCATE = 600  # 嵌入前单块最大字符数（适配 2048 token 本地模型）
_KB_EMBED_MAX_RETRIES = 3  # 单批最大重试次数
_KB_EMBED_BATCH_DELAY = 0.05  # 批次间隔秒（本地模型节流）
_KB_EMBED_MAX_CONCURRENT = 1  # 最大并发嵌入请求数（本地模型串行执行）
_KB_INDEX_MIN_ROWS = 1000     # 向量数超此阈值后自动创建索引
_KB_FTS_MIN_ROWS = 10         # 向量数超此阈值后自动创建 FTS 索引
_SEMANTIC_CACHE_MAX_SIZE = 200  # 语义边缓存最大条目数


def _segment_text(text: str) -> str:
    """jieba 中文分词，空格连接。用于 FTS5 索引和搜索。"""
    try:
        import logging as _logging

        import jieba
        jieba.setLogLevel(_logging.WARNING)
        return " ".join(jieba.cut_for_search(text))
    except Exception:
        return text


class EmbeddingFailedError(RuntimeError):
    """任一分块嵌入失败时抛出，整个文档处理终止，不产生部分数据。"""


def _parse_chunk_id(chunk_id: str) -> tuple[str | None, int | None]:
    """解析结构化 chunk_id，返回 (doc_id, chunk_index)。旧格式返回 (None, None)。"""
    if "_" in chunk_id:
        doc_id, idx_str = chunk_id.rsplit("_", 1)
        if idx_str.isdigit():
            return doc_id, int(idx_str)
    return None, None


class KnowledgeBaseManager:
    """知识库管理器。

    Usage:
        kb = KnowledgeBaseManager(workspace_root)
        doc_id = await kb.upload_document(file_path)
        results = await kb.search("查询内容", top_k=5)
    """

    @staticmethod
    def _safe_lance_id(doc_id: str) -> str:
        """转义单引号，防止 LanceDB filter 字符串注入。"""
        return doc_id.replace("'", "''")

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = Path(workspace_root)
        self._kb_dir = self._workspace_root / "data" / "knowledge"
        self._kb_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._kb_dir / "knowledge.db"
        self._tmp_dir = self._kb_dir / "tmp"
        self._tmp_dir.mkdir(exist_ok=True)

        self._write_lock = threading.RLock()

        self._lance_db = None
        self._lance_table = None
        self._embedding_dim: int | None = None
        self._index_lock = threading.Lock()
        self._index_creating = False
        self._fts_lock = threading.Lock()
        self._fts_index_creating = False
        self._fts_index_created = False
        self._semantic_cache: dict[str, list[dict]] = {}
        self._semantic_pending: dict[str, bool] = {}
        self._search_cache: dict[str, tuple[float, list[dict]]] = {}

        self._init_sqlite()
        self._init_lancedb()
        try:
            asyncio.create_task(self._scan_on_startup())
        except RuntimeError:
            logger.debug("[KB] No running event loop, skipping startup scan")

    def _init_sqlite(self) -> None:
        with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    upload_time REAL NOT NULL,
                    total_chunks INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'processing',
                    error_msg TEXT,
                    file_size INTEGER DEFAULT 0,
                    content_hash TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    FOREIGN KEY(document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON knowledge_chunks(document_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_doc_chunk ON knowledge_chunks(document_id, chunk_index)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT OR IGNORE INTO _meta (key, value) VALUES ('schema_version', '1')")
            try:
                conn.execute(
                    "ALTER TABLE knowledge_documents ADD COLUMN file_size INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "ALTER TABLE knowledge_documents ADD COLUMN content_hash TEXT DEFAULT ''"
                )
            except sqlite3.OperationalError:
                pass

            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
                    content,
                    prefix='2'
                )
            """)
            try:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks_fts"
                ).fetchone()[0]
                if existing == 0:
                    rows = conn.execute(
                        "SELECT rowid, content FROM knowledge_chunks"
                    ).fetchall()
                    for rowid, content in rows:
                        conn.execute(
                            "INSERT INTO knowledge_chunks_fts(rowid, content) VALUES(?, ?)",
                            (rowid, _segment_text(content)),
                        )
            except Exception:
                pass

            conn.commit()

    def _init_lancedb(self) -> None:
        import lancedb

        lance_path = str(self._workspace_root / "data" / "lancedb")
        self._lance_db = lancedb.connect(lance_path)
        self._ensure_lance_table()
        self._migrate_schema_if_needed()
        self._create_fts_index_if_needed()
        if self._lance_table is None and self.is_ready():
            try:
                self._proactive_task = asyncio.create_task(self._proactive_create_table())
            except RuntimeError:
                self._proactive_task = None
        logger.info("[KB] LanceDB initialized at %s, table=%s", lance_path, "knowledge_base")

    def _ensure_lance_table(self) -> None:
        if "knowledge_base" not in self._lance_db.table_names():
            self._lance_table = None
        else:
            self._lance_table = self._lance_db.open_table("knowledge_base")
            if self._lance_table.count_rows() > 0:
                logger.info("[KB] Opened existing knowledge_base table")

    def _migrate_schema_if_needed(self) -> None:
        if self._lance_table is None:
            return
        try:
            existing_cols = self._lance_table.schema.names
        except Exception:
            return
        if "content" in existing_cols:
            return
        logger.info("[KB] LanceDB schema migration triggered: adding content column")
        threading.Thread(target=self._migrate_with_content, daemon=True).start()

    def _migrate_with_content(self) -> None:
        try:
            sqlite_rows = []
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT kc.id, kc.content, kc.document_id, kd.status "
                    "FROM knowledge_chunks kc "
                    "JOIN knowledge_documents kd ON kc.document_id = kd.id"
                ).fetchall()
                sqlite_rows = [(r[0], r[1] or "", r[2], r[3]) for r in rows]

            if not sqlite_rows:
                return

            import lancedb
            import pyarrow as pa

            lance_path = str(self._workspace_root / "data" / "lancedb")
            tmp_db = lancedb.connect(lance_path)

            old_rows = []
            try:
                old_rows = self._lance_table.to_arrow(
                    columns=["id", "vector", "document_id"]
                ).to_pylist()
            except Exception:
                pass

            content_map = {r[0]: r[1] for r in sqlite_rows}

            dim = 0
            new_rows = []
            for row in old_rows:
                vid = row.get("id", "")
                vec = row.get("vector", [])
                did = row.get("document_id", "")
                if not vec:
                    continue
                if dim == 0:
                    dim = len(vec)
                new_rows.append({
                    "id": vid,
                    "vector": vec,
                    "document_id": did,
                    "content": content_map.get(vid, ""),
                })

            if not new_rows:
                return

            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
                pa.field("document_id", pa.string()),
                pa.field("content", pa.string()),
            ])
            try:
                tmp_db.drop_table("knowledge_base")
            except Exception:
                pass
            new_table = tmp_db.create_table("knowledge_base", schema=schema, mode="create")
            new_table.add(new_rows)
            self._lance_table = new_table
            if dim > 0:
                self._embedding_dim = dim
            logger.info("[KB] Schema migration complete: %d rows with content", len(new_rows))
        except Exception as e:
            logger.warning("[KB] Schema migration failed: %s", e)

    def _create_lance_table(self, dim: int) -> None:
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
                pa.field("document_id", pa.string()),
                pa.field("content", pa.string()),
            ]
        )
        try:
            self._lance_table = self._lance_db.create_table(
                "knowledge_base", schema=schema, mode="create"
            )
        except Exception:
            self._lance_table = self._lance_db.open_table("knowledge_base")
        self._embedding_dim = dim
        logger.info("[KB] knowledge_base table ready, dim=%d", dim)

    def _has_fts_index(self) -> bool:
        if self._lance_table is None:
            return False
        if self._fts_index_created:
            return True
        try:
            for idx in self._lance_table.list_indices():
                if "FTS" in str(idx):
                    self._fts_index_created = True
                    return True
        except Exception:
            pass
        return False

    def _create_fts_index_if_needed(self) -> None:
        if self._lance_table is None:
            return
        if not self._fts_lock.acquire(blocking=False):
            return
        try:
            if self._fts_index_creating or self._fts_index_created:
                return
            try:
                row_count = self._lance_table.count_rows()
                if row_count < _KB_FTS_MIN_ROWS:
                    return
            except Exception:
                return
            if self._has_fts_index():
                return
            self._fts_index_creating = True
        finally:
            self._fts_lock.release()

        def _build():
            try:
                self._lance_table.create_fts_index(
                    "content",
                    replace=True,
                    language="Chinese",
                    lower_case=True,
                    ascii_folding=True,
                    max_token_length=64,
                )
                self._fts_index_created = True
                logger.info("[KB] LanceDB FTS index created on content column")
            except Exception as e:
                logger.warning("[KB] LanceDB FTS index creation failed: %s", e)
            finally:
                self._fts_index_creating = False

        threading.Thread(target=_build, daemon=True).start()

    def _sync_fts5_for_document(self, doc_id: str) -> None:
        """同步文档分块到 FTS5 表（应用层 jieba 分词后写入）。"""
        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT rowid, content FROM knowledge_chunks "
                "WHERE document_id = ? AND rowid NOT IN "
                "(SELECT rowid FROM knowledge_chunks_fts)",
                (doc_id,),
            ).fetchall()
            for rowid, content in rows:
                conn.execute(
                    "INSERT INTO knowledge_chunks_fts(rowid, content) VALUES(?, ?)",
                    (rowid, _segment_text(content)),
                )
            if rows:
                conn.commit()

    async def _proactive_create_table(self) -> None:
        """启动时主动创建 LanceDB 表，避免死锁：没表→不能上传→不能建表。"""
        try:
            dim = await self._get_embedding_dim()
            await asyncio.to_thread(self._create_lance_table, dim)
        except Exception as e:
            logger.warning("[KB] Proactive table creation failed: %s", e)

    def _create_index_if_needed(self) -> None:
        """向量数超阈值时后台创建 IVF_PQ 索引（非阻塞）。"""
        if self._lance_table is None:
            return
        if not self._index_lock.acquire(blocking=False):
            return
        try:
            if self._index_creating:
                return
            try:
                row_count = self._lance_table.count_rows()
                if row_count < _KB_INDEX_MIN_ROWS:
                    return
                if list(self._lance_table.list_indices()):
                    return
            except Exception:
                return

            self._index_creating = True
        finally:
            self._index_lock.release()

        def _build():
            try:
                num_partitions = max(2, min(256, int(row_count ** 0.5)))
                self._lance_table.create_index(
                    metric="cosine",
                    index_type="IVF_PQ",
                    num_partitions=num_partitions,
                )
                logger.info(
                    "[KB] LanceDB index created: %d partitions for %d vectors",
                    num_partitions, row_count,
                )
            except Exception as e:
                logger.warning("[KB] LanceDB index creation failed: %s", e)
            finally:
                self._index_creating = False

        threading.Thread(target=_build, daemon=True).start()

    async def _get_embedding_dim(self) -> int:
        if self._embedding_dim is not None:
            return self._embedding_dim

        from openakita.llm.embeddings import get_embedding_model

        model = get_embedding_model()
        vec = await model.embed_query("dimension probe")
        self._embedding_dim = len(vec)
        return self._embedding_dim

    async def _get_embedder(self):
        from openakita.llm.embeddings import get_embedding_model

        return get_embedding_model()

    async def upload_document(
        self, file_path: str | Path, display_name: str | None = None
    ) -> dict[str, Any]:
        """上传并处理文档，返回 doc_id 或 duplicate 信息。

        Args:
            file_path: 临时文件路径
            display_name: 可选，显示用的原始文件名（用于去重和展示）

        Returns:
            {"doc_id": "...", "duplicate": false} 或 {"duplicate": true, ...}
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        doc_name = display_name or path.name
        suffix = "".join(Path(doc_name).suffixes).lower() or path.suffix.lower()
        if suffix not in _KB_ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的文件类型: {suffix}，支持: {sorted(_KB_ALLOWED_EXTENSIONS)}")

        if path.stat().st_size > _KB_MAX_FILE_SIZE:
            raise ValueError(f"文件超过大小限制 ({_KB_MAX_FILE_SIZE // 1024 // 1024} MB)")

        file_size = path.stat().st_size

        with open(path, "rb") as f:
            file_head = f.read(8192)
        content_hash = hashlib.sha256(file_head).hexdigest()[:16]

        existing = self._find_duplicate(doc_name, content_hash) or self._find_duplicate_by_hash(
            content_hash
        )
        if existing:
            return {
                "duplicate": True,
                "existing_doc_id": existing["id"],
                "existing_name": existing["name"],
                "existing_status": existing["status"],
            }

        doc_id = uuid.uuid4().hex

        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            conn.execute(
                "INSERT INTO knowledge_documents(id, name, file_type, upload_time, status, file_size, content_hash) "
                "VALUES(?, ?, ?, ?, 'processing', ?, ?)",
                (doc_id, doc_name, suffix.lstrip("."), time.time(), file_size, content_hash),
            )
            conn.commit()

        try:
            self._process_task = asyncio.create_task(self._process_document(doc_id, path))
        except RuntimeError:
            self._process_task = None
            logger.warning("[KB] No event loop, document %s stays in processing", doc_id)

        return {"doc_id": doc_id, "duplicate": False}

    async def _process_chunks(self, doc_id: str, text: str) -> int:
        """公共方法：分块 → 嵌入 → 原子写入 SQLite + LanceDB。

        任一步骤失败即终止，不产生部分数据。
        仅在所有向量完整性验证通过后才标记 ready。

        Returns:
            分块数量
        """
        chunker = TextChunker(strategy="paragraph", max_chunk_size=2000)
        chunks = chunker.chunk(text)

        if not chunks:
            raise RuntimeError("未能从文档中提取任何内容块")

        embedder = await self._get_embedder()
        truncate = _KB_EMBED_CHUNK_TRUNCATE
        chunk_texts = [c.content[:truncate].strip() for c in chunks]

        vectors, dim = await self._embed_in_batches(
            embedder,
            chunk_texts,
            doc_id,
        )

        if self._lance_table is None:
            if dim == 0:
                dim = await self._get_embedding_dim()
            await asyncio.to_thread(self._create_lance_table, dim)

        lance_rows: list[dict[str, Any]] = []
        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            conn.execute("BEGIN")
            try:
                for i, c in enumerate(chunks):
                    chunk_id = f"{doc_id}_{i:05d}"
                    conn.execute(
                        "INSERT INTO knowledge_chunks(id, document_id, chunk_index, content, token_count) "
                        "VALUES(?, ?, ?, ?, ?)",
                        (chunk_id, doc_id, i, c.content, c.token_estimate),
                    )
                    lance_rows.append({"id": chunk_id, "vector": vectors[i], "document_id": doc_id, "content": c.content[:600]})
                conn.execute(
                    "UPDATE knowledge_documents SET total_chunks=? WHERE id=?",
                    (len(chunks), doc_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        try:
            await asyncio.to_thread(self._lance_table.add, lance_rows)
        except Exception:
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "DELETE FROM knowledge_chunks_fts WHERE rowid IN "
                    "(SELECT rowid FROM knowledge_chunks WHERE document_id=?)",
                    (doc_id,),
                )
                conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (doc_id,))
                conn.execute("UPDATE knowledge_documents SET total_chunks=0 WHERE id=?", (doc_id,))
                conn.commit()
            raise

        self._sync_fts5_for_document(doc_id)

        self._create_index_if_needed()
        self._create_fts_index_if_needed()

        if not await self._verify_embeddings(doc_id):
            raise RuntimeError("向量完整性验证失败")

        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            conn.execute(
                "UPDATE knowledge_documents SET status='ready', error_msg=NULL WHERE id=?",
                (doc_id,),
            )
            conn.commit()

        logger.info(
            "[KB] Document %s processed: %d chunks verified OK",
            doc_id,
            len(chunks),
        )

        self._semantic_cache.clear()
        self._search_cache.clear()
        return len(chunks)

    async def _process_document(self, doc_id: str, file_path: Path) -> None:
        """后台处理文档：提取文本 → 分块 → 分批向量化 → 存储。"""
        try:
            text = await asyncio.to_thread(extract_text, file_path)
            if not text or not text.strip():
                raise RuntimeError("文档内容为空")

            await self._process_chunks(doc_id, text)

        except Exception as e:
            logger.error("[KB] Failed to process document %s: %s", doc_id, e)
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='failed', error_msg=? WHERE id=?",
                    (str(e)[:500], doc_id),
                )
                conn.commit()

    async def _embed_in_batches(
        self,
        embedder: Any,
        chunk_texts: list[str],
        doc_id: str,
        batch_size: int | None = None,
        batch_delay: float | None = None,
    ) -> tuple[list[list[float]], int]:
        """分批并发嵌入，返回 (vectors, dim)。

        任一批次失败即抛出 EmbeddingFailedError，绝不产生零向量占位。
        batch_size / batch_delay 为 None 时使用全局默认值。
        """
        bs = batch_size or _KB_EMBED_BATCH_SIZE
        delay = batch_delay if batch_delay is not None else _KB_EMBED_BATCH_DELAY
        total_batches = (len(chunk_texts) + bs - 1) // bs

        batches: list[tuple[int, list[str]]] = []
        for i in range(total_batches):
            start = i * bs
            batches.append((i, chunk_texts[start:start + bs]))

        sem = asyncio.Semaphore(_KB_EMBED_MAX_CONCURRENT)
        dim = 0
        dim_lock = asyncio.Lock()

        async def _embed_one(batch_idx: int, batch: list[str]) -> tuple[int, list[list[float]]]:
            last_error = None
            for attempt in range(_KB_EMBED_MAX_RETRIES):
                try:
                    async with sem:
                        batch_vecs = await embedder.embed(batch)
                    if not all(any(v != 0.0 for v in vec) for vec in batch_vecs):
                        raise EmbeddingFailedError(
                            f"批次 {batch_idx + 1}/{total_batches} 返回零向量"
                        )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    async with dim_lock:
                        nonlocal dim
                        if dim == 0 and batch_vecs:
                            dim = len(batch_vecs[0])
                    return batch_idx, batch_vecs
                except EmbeddingFailedError:
                    raise
                except Exception as e:
                    last_error = e
                    retry_delay = 2 ** attempt
                    if attempt < _KB_EMBED_MAX_RETRIES - 1:
                        logger.warning(
                            "[KB] Doc %s batch %d/%d attempt %d failed: %s, retrying in %ds",
                            doc_id,
                            batch_idx + 1,
                            total_batches,
                            attempt + 1,
                            e,
                            retry_delay,
                        )
                        await asyncio.sleep(retry_delay)
            logger.error(
                "[KB] Doc %s batch %d/%d failed after %d retries",
                doc_id,
                batch_idx + 1,
                total_batches,
                _KB_EMBED_MAX_RETRIES,
            )
            raise EmbeddingFailedError(
                f"批次 {batch_idx + 1}/{total_batches} 嵌入失败(重试{_KB_EMBED_MAX_RETRIES}次): {last_error}"
            )

        results = await asyncio.gather(*[
            _embed_one(idx, batch) for idx, batch in batches
        ])

        results.sort(key=lambda x: x[0])

        vectors: list[list[float]] = []
        for batch_idx, vecs in results:
            vectors.extend(vecs)
            logger.info("[KB] Doc %s: embedded %d/%d batches", doc_id, batch_idx + 1, total_batches)

        return vectors, dim

    async def list_documents(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """分页列出文档。"""
        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, name, file_type, upload_time, total_chunks, status, error_msg "
                    "FROM knowledge_documents ORDER BY upload_time DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0]
                return rows, total
        rows, total = await asyncio.to_thread(_query)
        return {"documents": [dict(r) for r in rows], "total": total}

    def get_doc_summary(self, max_docs: int = 30) -> str:
        """同步方法：返回知识库文档摘要列表（名称+类型+块数）。

        供 prompt builder 注入系统提示词，让 LLM 感知知识库中有哪些文档。
        """
        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT name, file_type, total_chunks FROM knowledge_documents "
                "WHERE status='ready' ORDER BY upload_time DESC LIMIT ?",
                (max_docs,),
            ).fetchall()
        if not rows:
            return ""
        lines: list[str] = [f"知识库中有 {len(rows)} 篇就绪文档："]
        for name, ftype, chunks in rows:
            lines.append(f"  - 《{name}》（{ftype}，{chunks}块）")
        return "\n".join(lines)

    async def delete_document(self, doc_id: str) -> bool:
        """删除文档及其所有分块（SQLite + LanceDB）。"""
        def _delete():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                cursor = conn.execute("SELECT id FROM knowledge_chunks WHERE document_id=?", (doc_id,))
                chunk_ids = [row[0] for row in cursor.fetchall()]
                conn.execute(
                    "DELETE FROM knowledge_chunks_fts WHERE rowid IN "
                    "(SELECT rowid FROM knowledge_chunks WHERE document_id=?)",
                    (doc_id,),
                )
                conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (doc_id,))
                conn.execute("DELETE FROM knowledge_documents WHERE id=?", (doc_id,))
                conn.commit()
                return chunk_ids
        chunk_ids = await asyncio.to_thread(_delete)
        if not chunk_ids and not self._document_exists(doc_id):
            return False
        if chunk_ids and self._lance_table is not None:
            try:
                await asyncio.to_thread(self._lance_table.delete, f"document_id = '{self._safe_lance_id(doc_id)}'")
            except Exception as e:
                logger.warning("[KB] Failed to delete LanceDB vectors for %s: %s", doc_id, e)
        self._semantic_cache.clear()
        self._search_cache.clear()
        return True

    def _document_exists(self, doc_id: str) -> bool:
        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            return conn.execute("SELECT 1 FROM knowledge_documents WHERE id=?", (doc_id,)).fetchone() is not None

    def _find_duplicate(self, name: str, content_hash: str) -> dict | None:
        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            row = conn.execute(
                "SELECT id, name, status FROM knowledge_documents WHERE name=? AND content_hash=?",
                (name, content_hash),
            ).fetchone()
            return {"id": row[0], "name": row[1], "status": row[2]} if row else None

    def _find_duplicate_by_hash(self, content_hash: str) -> dict | None:
        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            row = conn.execute(
                "SELECT id, name, status FROM knowledge_documents WHERE content_hash=? LIMIT 1",
                (content_hash,),
            ).fetchone()
            return {"id": row[0], "name": row[1], "status": row[2]} if row else None

    async def get_stats(self) -> dict[str, Any]:
        """返回知识库统计信息。"""

        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                total_docs = conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0]
                ready_docs = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_documents WHERE status='ready'"
                ).fetchone()[0]
                processing_docs = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_documents WHERE status='processing'"
                ).fetchone()[0]
                failed_docs = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_documents WHERE status='failed'"
                ).fetchone()[0]
                total_chunks = conn.execute(
                    "SELECT COALESCE(SUM(total_chunks), 0) FROM knowledge_documents WHERE status='ready'"
                ).fetchone()[0]
                recent = conn.execute(
                    "SELECT name, upload_time FROM knowledge_documents ORDER BY upload_time DESC LIMIT 3"
                ).fetchall()
                now = time.time()
                recent_docs = []
                for r in recent:
                    ago = int(now - r[1])
                    if ago < 60:
                        ago_str = f"{ago}秒前"
                    elif ago < 3600:
                        ago_str = f"{ago // 60}分钟前"
                    elif ago < 86400:
                        ago_str = f"{ago // 3600}小时前"
                    else:
                        ago_str = f"{ago // 86400}天前"
                    recent_docs.append({"name": r[0], "ago": ago_str})
                return (
                    total_docs,
                    ready_docs,
                    processing_docs,
                    failed_docs,
                    total_chunks,
                    recent_docs,
                )

        (
            total_docs,
            ready_docs,
            processing_docs,
            failed_docs,
            total_chunks,
            recent_docs,
        ) = await asyncio.to_thread(_query)
        return {
            "total_documents": total_docs,
            "ready_documents": ready_docs,
            "processing_documents": processing_docs,
            "failed_documents": failed_docs,
            "total_chunks": total_chunks,
            "recent_documents": recent_docs,
        }

    def find_document_by_name(self, name: str) -> list[dict[str, Any]]:
        """按名称搜索文档（支持模糊匹配）。"""
        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT id, name, file_type, total_chunks, status, upload_time "
                "FROM knowledge_documents WHERE name LIKE ? ORDER BY upload_time DESC LIMIT 5",
                (f"%{name}%",),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "file_type": r[2],
                    "total_chunks": r[3],
                    "status": r[4],
                    "upload_time": r[5],
                }
                for r in rows
            ]

    async def replace_document(
        self, existing_doc_id: str, file_path: str | Path, display_name: str | None = None
    ) -> dict[str, Any]:
        """覆盖已有文档：删除旧文档后重新上传。"""
        await self.delete_document(existing_doc_id)
        return await self.upload_document(file_path, display_name=display_name)

    async def ingest_text(self, title: str, content: str, file_type: str = "web") -> dict[str, Any]:
        """将纯文本内容作为虚拟文档保存到知识库。

        Args:
            title: 文档标题（将作为文件名）
            content: 文本内容
            file_type: 文档类型标签

        Returns:
            {"doc_id": "...", "duplicate": false}
        """
        safe_name = "".join(c for c in title if c.isalnum() or c in "._- ")[:100].strip()
        if not safe_name:
            safe_name = "untitled"
        ext = ".md" if file_type == "web" else ".txt"
        fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix=f"{safe_name}_", dir=str(self._tmp_dir))
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        result = await self.upload_document(tmp_path, display_name=f"{safe_name}{ext}")
        asyncio.create_task(self._delayed_unlink(tmp_path))
        return result

    async def _delayed_unlink(self, path: str) -> None:
        await asyncio.sleep(5)
        try:
            os.unlink(path)
        except OSError:
            pass

    async def search(
        self,
        query: str,
        top_k: int = 5,
        doc_filter: str | None = None,
        context_window: int = 0,
        rerank: bool = True,
    ) -> list[dict[str, Any]]:
        """在知识库中搜索相关内容（向量 + 关键词双路并行 + RRF 融合）。

        Args:
            query: 搜索查询
            top_k: 返回结果数
            doc_filter: 可选，按文档 ID 过滤
            context_window: 上下文窗口（±N邻块），0=不扩展
            rerank: 是否启用精排（默认 True）

        Returns:
            [{chunk_id, document_id, document_name, content, score}, ...]
        """
        if self._lance_table is None:
            return []

        cache_key = hashlib.sha256(f"{query}|{doc_filter}|{top_k}|{context_window}|{rerank}".encode()).hexdigest()
        cached = self._search_cache.get(cache_key)
        if cached and time.time() - cached[0] < 60:
            return cached[1]

        coarse_k = max(top_k * 6, 30)

        query_vec: list[float] | None = None
        try:
            embedder = await self._get_embedder()
            query_vec = await embedder.embed_query(query)
        except Exception as e:
            logger.warning("[KB] Embedding model unavailable, using keyword-only search: %s", e)

        use_hybrid = self._has_fts_index()

        async def _vector_search() -> list[dict[str, Any]]:
            if query_vec is None:
                return []
            def _do():
                if use_hybrid:
                    qb = (
                        self._lance_table.search(query_type="hybrid")
                        .text(query)
                        .vector(query_vec)
                        .metric("cosine")
                    )
                else:
                    qb = self._lance_table.search(query_vec).metric("cosine")
                if doc_filter:
                    qb = qb.where(f"document_id = '{self._safe_lance_id(doc_filter)}'")
                raw = qb.limit(coarse_k).to_list()
                results: list[dict[str, Any]] = []
                for r in raw:
                    cid = r.get("id", "")
                    if use_hybrid:
                        s = float(r.get("_score", 0))
                    else:
                        d = r.get("_distance", 1.0)
                        s = max(0.0, min(1.0, 1.0 - d / 2.0))
                    results.append({"chunk_id": cid, "score": s})
                return results
            return await asyncio.to_thread(_do)

        keyword_task = self._search_keyword(query, top_k=coarse_k, doc_filter=doc_filter)
        vector_task = _vector_search()

        kw_results, vec_results = await asyncio.gather(keyword_task, vector_task)

        chunk_scores: dict[str, float] = {}

        for rank, r in enumerate(kw_results):
            cid = r["chunk_id"]
            chunk_scores[cid] = chunk_scores.get(cid, 0.0) + 1.0 / (60.0 + rank)

        for rank, r in enumerate(vec_results):
            cid = r["chunk_id"]
            chunk_scores[cid] = chunk_scores.get(cid, 0.0) + 1.0 / (60.0 + rank)

        scored = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)
        fused: list[dict[str, Any]] = [
            {"chunk_id": cid, "score": s} for cid, s in scored[:coarse_k]
        ]

        if not fused:
            self._search_cache[cache_key] = (time.time(), [])
            return []

        if rerank and query_vec is not None and len(fused) > top_k:
            fused = await asyncio.to_thread(self._rerank_sync, query_vec, fused, top_k)

        fused = fused[:top_k]

        needed_ids: set[str] = set()
        matches: list[tuple[str, float, str | None, int | None]] = []
        for r in fused:
            cid = r["chunk_id"]
            s = r["score"]
            needed_ids.add(cid)
            parsed_doc_id, parsed_idx = _parse_chunk_id(cid)
            if parsed_doc_id is not None and context_window > 0:
                matches.append((cid, s, parsed_doc_id, parsed_idx))
                for offset in range(1, context_window + 1):
                    needed_ids.add(f"{parsed_doc_id}_{parsed_idx - offset:05d}")
                    needed_ids.add(f"{parsed_doc_id}_{parsed_idx + offset:05d}")
            else:
                matches.append((cid, s, None, None))

        def _fetch_content():
            content_map: dict[str, tuple[str, str, str]] = {}
            with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                placeholders = ",".join(["?"] * len(needed_ids))
                rows = conn.execute(
                    f"""SELECT kc.id, kc.content, kd.id as doc_id, kd.name as document_name
                        FROM knowledge_chunks kc
                        JOIN knowledge_documents kd ON kc.document_id = kd.id
                        WHERE kc.id IN ({placeholders})""",
                    list(needed_ids),
                ).fetchall()
                for row in rows:
                    content_map[row[0]] = (row[1], row[2], row[3])
            return content_map

        content_map = await asyncio.to_thread(_fetch_content)

        results_out: list[dict[str, Any]] = []
        for chunk_id, score, parsed_doc_id, parsed_idx in matches:
            row = content_map.get(chunk_id)
            if row is None:
                continue
            base_content, lancedb_doc_id, doc_name = row

            if parsed_doc_id is not None and context_window > 0:
                parts: list[str] = []
                for offset in range(-context_window, context_window + 1):
                    neighbor_id = f"{parsed_doc_id}_{parsed_idx + offset:05d}"
                    if neighbor_id == chunk_id:
                        parts.append(base_content)
                    elif neighbor_id in content_map:
                        parts.append(content_map[neighbor_id][0])
                expanded_content = "\n\n".join(parts)
            else:
                expanded_content = base_content

            results_out.append({
                "chunk_id": chunk_id,
                "document_id": lancedb_doc_id,
                "document_name": doc_name,
                "content": expanded_content,
                "score": score,
            })

        self._search_cache[cache_key] = (time.time(), results_out)
        if len(self._search_cache) > 200:
            self._search_cache.clear()
        return results_out

    def _rerank_sync(
        self, query_vec: list[float],
        candidates: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """精排：用同一 embedding 批量重算余弦相似度（同步，在搜索线程中执行）。"""
        import math

        chunk_ids = [c["chunk_id"] for c in candidates]
        try:
            dummy_vec = [0.0] * len(query_vec)
            raw_rows = self._lance_table.search(dummy_vec) \
                .where(f"id IN ({','.join(repr(cid) for cid in chunk_ids)})") \
                .limit(len(candidates)) \
                .to_list()
            vec_map: dict[str, list[float]] = {}
            for row in raw_rows:
                vid = row.get("id", "")
                vec = row.get("vector", [])
                if vid and vec:
                    vec_map[vid] = vec
        except Exception:
            return candidates

        def _cos_sim(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            if na == 0 or nb == 0:
                return 0.0
            return dot / (na * nb)

        scored: list[tuple[dict[str, Any], float]] = []
        for c in candidates:
            vec = vec_map.get(c["chunk_id"])
            if vec:
                sim = _cos_sim(query_vec, vec)
                scored.append((c, (sim + 1.0) / 2.0))
            else:
                scored.append((c, c.get("score", 0.0)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:top_k]]

    async def _search_keyword(
        self,
        query: str,
        top_k: int = 30,
        doc_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """关键词搜索：SQLite FTS5 BM25 + LIKE 回退。

        独立于向量搜索，确保精确术语匹配在任何情况下都能召回。
        """
        def _do():
            with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                results: list[dict[str, Any]] = []

                doc_where = ""
                doc_params: list[Any] = []
                if doc_filter:
                    doc_where = "AND kd.id = ?"
                    doc_params = [doc_filter]

                try:
                    segmented = _segment_text(query)
                    fts_rows = conn.execute(
                        f"""SELECT kc.id, kc.content, kd.id as doc_id, kd.name as doc_name,
                                   rank as bm25_rank
                            FROM knowledge_chunks_fts fts
                            JOIN knowledge_chunks kc ON kc.rowid = fts.rowid
                            JOIN knowledge_documents kd ON kc.document_id = kd.id
                            WHERE knowledge_chunks_fts MATCH ? {doc_where}
                              AND kd.status = 'ready'
                            ORDER BY rank
                            LIMIT ?""",
                        [segmented, *doc_params, top_k],
                    ).fetchall()
                    for row in fts_rows:
                        rank = float(row["bm25_rank"])
                        score = 1.0 / (1.0 + rank) if rank > 0 else 1.0
                        results.append({
                            "chunk_id": row["id"],
                            "document_id": row["doc_id"],
                            "document_name": row["doc_name"],
                            "content": row["content"],
                            "score": max(0.0, min(1.0, score)),
                        })
                    if results:
                        return results
                except Exception:
                    pass

                like_rows = conn.execute(
                    f"""SELECT kc.id, kc.content, kd.id as doc_id, kd.name as doc_name
                        FROM knowledge_chunks kc
                        JOIN knowledge_documents kd ON kc.document_id = kd.id
                        WHERE kc.content LIKE ? {doc_where}
                          AND kd.status = 'ready'
                        LIMIT ?""",
                    [f"%{query}%", *doc_params, top_k],
                ).fetchall()
                for row in like_rows:
                    results.append({
                        "chunk_id": row["id"],
                        "document_id": row["doc_id"],
                        "document_name": row["doc_name"],
                        "content": row["content"],
                        "score": 0.5,
                    })
                return results

        return await asyncio.to_thread(_do)

    async def get_document_status(self, doc_id: str) -> dict[str, Any] | None:
        """获取文档处理状态。"""

        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, name, file_type, upload_time, total_chunks, status, error_msg "
                    "FROM knowledge_documents WHERE id=?",
                    (doc_id,),
                ).fetchone()
                return dict(row) if row else None

        return await asyncio.to_thread(_query)

    async def get_chunk_text(self, chunk_id: str) -> str | None:
        """根据 chunk ID 获取原文内容。"""

        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                row = conn.execute(
                    "SELECT content FROM knowledge_chunks WHERE id=?",
                    (chunk_id,),
                ).fetchone()
                return row[0] if row else None

        return await asyncio.to_thread(_query)

    async def get_document_by_id(self, doc_id: str) -> dict[str, Any] | None:
        """获取单个文档的详细信息。"""

        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, name, file_type, upload_time, total_chunks, status, error_msg "
                    "FROM knowledge_documents WHERE id=?",
                    (doc_id,),
                ).fetchone()
                if row is None:
                    return None
                d = dict(row)
                chunks = conn.execute(
                    "SELECT id, chunk_index, content, token_count "
                    "FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index",
                    (doc_id,),
                ).fetchall()
                d["chunks"] = [dict(c) for c in chunks]
                return d

        return await asyncio.to_thread(_query)

    async def get_document_full_content(self, doc_id: str) -> dict[str, Any] | None:
        """获取文档完整内容（拼接所有分块）及元数据。"""

        def _query():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, name, file_type, upload_time, total_chunks, status, error_msg "
                    "FROM knowledge_documents WHERE id=?",
                    (doc_id,),
                ).fetchone()
                if row is None:
                    return None
                doc = dict(row)
                chunks = conn.execute(
                    "SELECT content FROM knowledge_chunks WHERE document_id=? "
                    "ORDER BY chunk_index",
                    (doc_id,),
                ).fetchall()
                doc["content"] = "\n\n".join(c[0] for c in chunks if c[0])
                return doc

        return await asyncio.to_thread(_query)

    async def update_document_content(
        self, doc_id: str, content: str, name: str | None = None
    ) -> dict[str, Any]:
        """编辑文档全文：删除旧分块和向量 → 重新分块 → 嵌入 → 入库（同步等待完成）。

        Args:
            doc_id: 文档 ID
            content: 编辑后的全文
            name: 可选的新文件名

        Returns:
            {"doc_id": "...", "status": "updated", "chunks": N}
        """
        if not self._document_exists(doc_id):
            raise ValueError(f"文档不存在: {doc_id}")

        def _delete_old():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "DELETE FROM knowledge_chunks_fts WHERE rowid IN "
                    "(SELECT rowid FROM knowledge_chunks WHERE document_id=?)",
                    (doc_id,),
                )
                conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (doc_id,))
                conn.commit()

        await asyncio.to_thread(_delete_old)

        if self._lance_table is not None:
            try:
                await asyncio.to_thread(
                    self._lance_table.delete, f"document_id = '{self._safe_lance_id(doc_id)}'"
                )
            except Exception as e:
                logger.warning("[KB] Failed to delete old vectors for %s: %s", doc_id, e)

        with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            if name:
                conn.execute(
                    "UPDATE knowledge_documents SET name=?, status='processing', error_msg=NULL WHERE id=?",
                    (name, doc_id),
                )
            else:
                conn.execute(
                    "UPDATE knowledge_documents SET status='processing', error_msg=NULL WHERE id=?",
                    (doc_id,),
                )
            conn.commit()

        try:
            n_chunks = await self._process_chunks(doc_id, content)
            return {"doc_id": doc_id, "status": "updated", "chunks": n_chunks}
        except Exception as e:
            logger.error("[KB] Failed to update document %s: %s", doc_id, e)
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='failed', error_msg=? WHERE id=?",
                    (str(e)[:500], doc_id),
                )
                conn.commit()
            raise

    def is_ready(self) -> bool:
        """检查知识库是否完全可用（嵌入模型已配置）。"""
        from openakita.llm.embeddings import get_embedding_model

        try:
            get_embedding_model()
            return True
        except Exception:
            return False

    async def _scan_on_startup(self) -> None:
        """启动时扫描：超时 processing 文档 → failed。"""
        try:
            await asyncio.sleep(2)
            await self._check_stuck_processing()
        except Exception as e:
            logger.warning("[KB] Startup scan failed: %s", e)

    async def _check_stuck_processing(self) -> int:
        """将超时的 processing 文档标记为 failed。"""

        def _do():
            cutoff = time.time() - 600
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                c = conn.execute(
                    "UPDATE knowledge_documents SET status='failed', error_msg=? "
                    "WHERE status='processing' AND upload_time < ?",
                    ("处理超时，可能因服务中断未完成", cutoff),
                )
                conn.commit()
                return c.rowcount

        count = await asyncio.to_thread(_do)
        if count:
            logger.warning("[KB] Marked %d timed-out processing documents as failed", count)
        return count

    async def repair_document(self, doc_id: str) -> dict[str, Any]:
        """修复文档：从 SQLite 分块重建 LanceDB 向量索引。

        零容忍：任一批次嵌入失败即标记 failed，不写部分向量。
        """

        def _read_chunks():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT id, content FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index",
                    (doc_id,),
                ).fetchall()
                return [(r[0], r[1]) for r in rows]

        chunks = await asyncio.to_thread(_read_chunks)
        if not chunks:
            return {"repaired": False, "reason": "文档无分块记录"}

        try:
            embedder = await self._get_embedder()
            chunk_texts = [c[1][:_KB_EMBED_CHUNK_TRUNCATE].strip() for c in chunks]
            vectors, dim = await self._embed_in_batches(embedder, chunk_texts, doc_id)
        except EmbeddingFailedError as e:
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='failed', error_msg=? WHERE id=?",
                    (str(e)[:500], doc_id),
                )
                conn.commit()
            return {"repaired": False, "reason": str(e)[:200]}

        if self._lance_table is None:
            if dim == 0:
                dim = await self._get_embedding_dim()
            await asyncio.to_thread(self._create_lance_table, dim)

        try:
            await asyncio.to_thread(self._lance_table.delete, f"document_id = '{self._safe_lance_id(doc_id)}'")
        except Exception:
            pass

        lance_rows = [
            {"id": chunks[i][0], "vector": vectors[i], "document_id": doc_id, "content": chunks[i][1][:600]}
            for i in range(len(chunks))
        ]
        try:
            await asyncio.to_thread(self._lance_table.add, lance_rows)
        except Exception as e:
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='failed', error_msg=? WHERE id=?",
                    (f"修复向量写入失败: {e!s:200}", doc_id),
                )
                conn.commit()
            return {"repaired": False, "reason": f"LanceDB写入失败: {e!s:200}"}

        self._create_index_if_needed()
        self._create_fts_index_if_needed()

        if await self._verify_embeddings(doc_id):
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='ready', error_msg=NULL WHERE id=?",
                    (doc_id,),
                )
                conn.commit()
            logger.info("[KB] Repaired document %s: %d chunks", doc_id, len(chunks))
            return {"repaired": True, "chunks": len(chunks)}
        else:
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "UPDATE knowledge_documents SET status='failed', error_msg=? WHERE id=?",
                    ("修复后向量完整性验证失败", doc_id),
                )
                conn.commit()
            return {"repaired": False, "reason": "修复后向量完整性验证失败"}

    async def repair_orphan_vectors(self) -> dict[str, Any]:
        """清理 LanceDB 中无对应 SQLite 文档的孤儿向量。"""
        if self._lance_table is None:
            return {"cleaned": 0}

        try:
            lance_ids = await asyncio.to_thread(
                lambda: {
                    r["document_id"]
                    for r in self._lance_table.to_arrow(columns=["document_id"]).to_pylist()
                }
            )
            logger.debug(
                "[KB] Loaded %d unique document_ids from LanceDB for orphan check", len(lance_ids)
            )
        except Exception:
            lance_ids = set()

        def _get_valid_ids():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                rows = conn.execute("SELECT id FROM knowledge_documents").fetchall()
                return {r[0] for r in rows}

        valid_ids = await asyncio.to_thread(_get_valid_ids)
        orphan_ids = lance_ids - valid_ids

        cleaned = 0
        for oid in orphan_ids:
            try:
                await asyncio.to_thread(self._lance_table.delete, f"document_id = '{oid}'")
                cleaned += 1
            except Exception:
                pass

        if cleaned:
            logger.info("[KB] Cleaned %d orphan vector groups", cleaned)

        stuck_count = await self._check_stuck_processing()
        return {"cleaned": cleaned, "stuck_processing_fixed": stuck_count}

    async def get_inconsistent_documents(self) -> list[dict[str, Any]]:
        """列出所有不一致的文档（SQLite chunk 数与 LanceDB 向量数不匹配）。"""
        if self._lance_table is None:
            return []

        def _get_ready_docs():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT id, name, total_chunks, status "
                    "FROM knowledge_documents WHERE status IN ('ready', 'failed')"
                ).fetchall()
                return [(r[0], r[1], r[2], r[3]) for r in rows]

        docs = await asyncio.to_thread(_get_ready_docs)
        inconsistent: list[dict[str, Any]] = []

        for doc_id, name, sqlite_count, status in docs:
            try:
                lance_count = await asyncio.to_thread(
                    lambda d=doc_id: self._lance_table.count_rows(f"document_id = '{d}'")
                )
            except Exception:
                lance_count = 0

            if lance_count != sqlite_count:
                inconsistent.append(
                    {
                        "doc_id": doc_id,
                        "name": name,
                        "status": status,
                        "sqlite_chunks": sqlite_count,
                        "lancedb_vectors": lance_count,
                    }
                )

        return inconsistent

    async def verify_document(self, doc_id: str) -> dict[str, Any] | None:
        """返回文档在 SQLite 和 LanceDB 中的记录数对比。"""

        def _get_sqlite():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                row = conn.execute(
                    "SELECT id, name, status, total_chunks FROM knowledge_documents WHERE id=?",
                    (doc_id,),
                ).fetchone()
                if row is None:
                    return None
                chunk_count = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?",
                    (doc_id,),
                ).fetchone()[0]
                return {
                    "id": row[0],
                    "name": row[1],
                    "status": row[2],
                    "total_chunks": row[3],
                    "actual_chunks": chunk_count,
                }

        sqlite_info = await asyncio.to_thread(_get_sqlite)
        if sqlite_info is None:
            return None

        lance_count = 0
        if self._lance_table is not None:
            try:
                lance_count = await asyncio.to_thread(
                    lambda: self._lance_table.count_rows(f"document_id = '{self._safe_lance_id(doc_id)}'")
                )
            except Exception:
                lance_count = 0

        return {
            **sqlite_info,
            "lancedb_vectors": lance_count,
            "consistent": lance_count == sqlite_info["actual_chunks"],
        }

    async def _verify_embeddings(self, doc_id: str) -> bool:
        """内部方法：验证文档向量完整性（数量一致 + 抽样非零）。

        与 verify_document() 不同，此方法还检查向量内容是否为零向量。
        """

        def _get_count():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (doc_id,)
                ).fetchone()[0]

        chunk_count = await asyncio.to_thread(_get_count)
        if self._lance_table is None:
            return False

        try:
            safe_id = self._safe_lance_id(doc_id)
            lance_count = await asyncio.to_thread(
                lambda: self._lance_table.count_rows(f"document_id = '{safe_id}'")
            )
        except Exception:
            return False

        if chunk_count != lance_count:
            logger.warning(
                "[KB] Doc %s: chunk count %d != lance vectors %d", doc_id, chunk_count, lance_count
            )
            return False

        if chunk_count == 0:
            return True

        try:
            dummy_vec = [0.0] * (self._embedding_dim or 1024)
            sample_rows = await asyncio.to_thread(
                lambda: self._lance_table.search(dummy_vec)
                .where(f"document_id = '{safe_id}'")
                .limit(min(5, chunk_count))
                .to_list()
            )
            for row in sample_rows:
                vec = row.get("vector", [])
                if vec and all(v == 0.0 for v in vec):
                    logger.warning("[KB] Doc %s chunk %s has zero vector", doc_id, row.get("id"))
                    return False
            return True
        except Exception as e:
            logger.warning("[KB] Zero-vector check failed for %s: %s", doc_id, e)
            return False

    def _semantic_cache_key(
        self, doc_id: str | None, threshold: float
    ) -> str:
        return f"sem_{doc_id or 'all'}_{threshold:.2f}"

    async def _compute_and_cache_semantic(
        self,
        key: str,
        embedder: Any,
        nodes: list[dict],
        nodes_by_id: dict[str, dict],
        seen_pairs: set[tuple[str, str]],
        threshold: float,
        doc_id: str | None,
    ) -> None:
        """后台计算语义边并写入缓存。"""
        import random as _random

        try:
            sample_size = max(30, min(500, len(nodes)))
            if doc_id:
                sample_nodes = nodes[: min(len(nodes), sample_size)]
            else:
                sample_indices = _random.sample(range(len(nodes)), min(sample_size, len(nodes)))
                sample_nodes = [nodes[i] for i in sorted(sample_indices)]

            texts = [n["content"][:_KB_EMBED_CHUNK_TRUNCATE].strip() for n in sample_nodes]
            try:
                vecs, _ = await self._embed_in_batches(
                    embedder, texts, (doc_id or "all") + "_sem",
                )
            except EmbeddingFailedError:
                self._semantic_cache[key] = []
                return

            links: list[dict] = []
            sem = asyncio.Semaphore(10)

            async def _process_one(n: dict, v: list[float]):
                try:
                    async with sem:
                        lance_results = await asyncio.to_thread(
                            lambda: (
                                self._lance_table.search(v)
                                .metric("cosine")
                                .limit(15)
                                .to_list()
                            ),
                        )
                        for r in lance_results:
                            sc = 1.0 - r.get("_distance", 1.0) / 2.0
                            if sc >= threshold:
                                tid = r.get("id", "")
                                if tid not in nodes_by_id or tid == n["id"]:
                                    continue
                                target = nodes_by_id.get(tid)
                                if target and target["group"] == n["group"]:
                                    if abs(target["chunk_index"] - n["chunk_index"]) <= 3:
                                        continue
                                pair_key = (n["id"], tid) if n["id"] < tid else (tid, n["id"])
                                if pair_key not in seen_pairs:
                                    seen_pairs.add(pair_key)
                                    links.append({
                                        "source": n["id"], "target": tid, "value": round(sc, 3),
                                    })
                except Exception:
                    pass

            await asyncio.gather(*[_process_one(n, v) for n, v in zip(sample_nodes, vecs, strict=True)])
            self._semantic_cache[key] = links
            logger.info("[KB] Semantic edges cached for %s: %d edges", key, len(links))
        except Exception as e:
            logger.warning("[KB] Semantic edge caching failed for %s: %s", key, e)
            self._semantic_cache[key] = []
        finally:
            self._semantic_pending.pop(key, None)
            if len(self._semantic_cache) > _SEMANTIC_CACHE_MAX_SIZE:
                self._semantic_cache.clear()

    async def get_graph_data(
        self,
        doc_id: str | None = None,
        include_semantic: bool = False,
        similarity_threshold: float = 0.75,
        max_nodes: int = 0,
    ) -> dict[str, Any]:
        """获取图谱数据：节点（chunk）+ 语义边（异步缓存）。max_nodes=0 表示不限制。"""

        threshold = max(0.5, min(0.95, similarity_threshold))
        limit = max_nodes if max_nodes > 0 else 9999999
        cache_key = self._semantic_cache_key(doc_id, threshold)

        cached_links = self._semantic_cache.get(cache_key)

        def _query_chunks():
            with self._write_lock, sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                if doc_id:
                    rows = conn.execute(
                        """SELECT kc.id, kc.content, kc.chunk_index, kc.document_id, kd.name as doc_name, kd.file_type
                           FROM knowledge_chunks kc
                           JOIN knowledge_documents kd ON kc.document_id = kd.id
                           WHERE kc.document_id = ? AND kd.status = 'ready'
                           ORDER BY kc.chunk_index
                            LIMIT ?""",
                        (doc_id, limit),
                    ).fetchall()
                    total = conn.execute(
                        "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?",
                        (doc_id,),
                    ).fetchone()[0]
                else:
                    total = conn.execute(
                        """SELECT COUNT(*) FROM knowledge_chunks kc
                           JOIN knowledge_documents kd ON kc.document_id = kd.id
                           WHERE kd.status = 'ready'"""
                    ).fetchone()[0]
                    rows = conn.execute(
                        """SELECT kc.id, kc.content, kc.chunk_index, kc.document_id, kd.name as doc_name, kd.file_type
                           FROM knowledge_chunks kc
                           JOIN knowledge_documents kd ON kc.document_id = kd.id
                           WHERE kd.status = 'ready'
                           ORDER BY kc.document_id, kc.chunk_index
                            LIMIT ?""",
                        (limit,),
                    ).fetchall()
                return [(r[0], r[1] or "", r[2], r[3], r[4], r[5] or "") for r in rows], total

        chunks, total_candidates = await asyncio.to_thread(_query_chunks)
        if not chunks:
            return {
                "nodes": [],
                "links": [],
                "meta": {
                    "total_nodes": 0,
                    "total_edges": 0,
                    "truncated": False,
                    "total_candidates": 0,
                },
            }

        nodes: list[dict] = []
        nodes_by_id: dict[str, dict] = {}

        for cid, content, idx, did, dname, file_type in chunks:
            name = content[:80].replace("\n", " ").strip()
            if len(content) > 80:
                name += "..."
            node = {
                "id": cid,
                "name": name,
                "doc_name": dname,
                "group": did,
                "type": file_type,
                "chunk_index": idx,
                "content": content[:200],
            }
            nodes.append(node)
            nodes_by_id[cid] = node

        links: list[dict] = []
        semantic_pending = False
        semantic_incomplete = False

        if cached_links is not None:
            links = cached_links
        elif include_semantic and self._lance_table is not None and cache_key not in self._semantic_pending:
            self._semantic_pending[cache_key] = True
            try:
                embedder = await self._get_embedder()
                asyncio.create_task(
                    self._compute_and_cache_semantic(
                        cache_key, embedder, nodes, nodes_by_id,
                        set(), threshold, doc_id,
                    )
                )
                semantic_pending = True
            except Exception as e:
                logger.warning("[KB] Failed to start semantic edge task: %s", e)
                self._semantic_pending.pop(cache_key, None)
        elif cache_key in self._semantic_pending:
            semantic_pending = True

        truncated = len(chunks) < total_candidates

        return {
            "nodes": nodes,
            "links": links,
            "meta": {
                "total_nodes": len(nodes),
                "total_edges": len(links),
                "truncated": truncated,
                "max_nodes": max_nodes,
                "total_candidates": total_candidates,
                "semantic_pending": semantic_pending,
                "semantic_incomplete": semantic_incomplete,
                "doc_groups": [
                    {"id": did, "name": dname}
                    for did, dname in sorted(
                        {(n["group"], n["doc_name"]) for n in nodes}
                    )
                ],
            },
        }
